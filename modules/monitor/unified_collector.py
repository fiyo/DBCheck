# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
统一采集器 —— 全系统唯一一条常驻监控采集循环。

改造前（v26.11.17.0）进程启动时会拉起三条互不相干的循环：
  - MetricsCollector.tick        APScheduler  30s → 首页实时监控
  - MonitorEngine._background_loop          10s → 实时慢查询 / 活跃连接
  - ScreenCollector._loop                   15s → 监控大屏
且 MonitorEngine 的 ``_connect_and_query`` **每条 SQL 都新建一条连接**，
单实例 30s 内可达 10~20 次握手；三套存储（SQLite / 两个内存缓冲）里的
数据也互相对不上时间。

本模块把它们合并为：

  一条 15s 循环 → 每实例一次建连（或一次 JDBC 批量子进程）
                → 在同一连接上依次完成四组采集
                   ① 首页深采（即时指标 + 速率差分）
                   ② 连接数 / 会话明细
                   ③ 慢查询
                   ④ 大屏增量与容量指标（SCREEN_SQLS）+ 状态判定
                → 一次写帧到 UnifiedStore
                → 一次 socketio 推流 / 告警评估 / 历史落库

所有旧类（MonitorEngine / ScreenCollector / MetricsCollector）降级为
UnifiedStore 的只读投影适配器，其 public 方法与返回结构保持不变，
因此现有 API、前端页面、socketio 事件、告警与历史回放全部零改动。

宿主资源采集与数据库探测彻底解耦：即使建连失败 / 退避 / 探测不通，
也照常落一份宿主快照；宿主采集按 30s 节流，避免 15s 轮把 SSH 采集翻倍。
"""

import time
import threading
import concurrent.futures as cf
from datetime import datetime

from modules.monitor import connection_scope
from modules.monitor.connection_factory import open_db_connection, uses_db_tunnel
from modules.monitor.unified_store import UnifiedStore

DEFAULT_INTERVAL = 15          # 大屏速率口径（原始 counters 做差值）
MIN_INTERVAL = 5
MAX_INTERVAL = 60
MAX_WORKERS = 8                # 单轮并发实例数
HOST_INTERVAL = 30             # 宿主资源采集节流（SSH 开销大）
QUERY_TIMEOUT = 15

# 大屏宿主面板需要的字段（与 screen_metrics._HOST_KEYS 同口径）
_HOST_KEYS = ('host_cpu', 'host_cpu_iowait', 'host_mem', 'host_swap_pct',
              'host_load1', 'host_load5', 'host_load15',
              'host_disk_read_mb_s', 'host_disk_write_mb_s', 'host_disk_await_ms',
              'host_os', 'host_services', 'host_services_count',
              'host_services_running', 'host_services_abnormal',
              'host_services_cpu_pct', 'host_services_mem_mb',
              'host_service_alerts', 'host_service_alert_count',
              'host_collector_source', 'host_error',
              # 数据新鲜度：真实采样时刻 / 最后成功时刻 / 陈旧秒数 / 是否历史数据
              'host_sample_ts', 'host_last_ok_ts', 'host_age_s', 'host_stale')


class BatchResultConnection:
    """把 JDBC 批量子进程的「SQL → 行」结果伪装成 DBAPI 连接。

    用途：``oracle_jdbc`` / ``uxdb_jdbc`` 的首页深采原本要单独 spawn 一个
    ``jdbc_metrics_cli`` 子进程，与大屏的 ``jdbc_collect_cli`` 批量子进程
    重复。现在把首页深采 SQL 一并放进批量集合，用本代理从同一次结果里
    投影，首页深采函数（``_collect_oracle`` / ``_collect_uxdb`` 等）无需
    任何改动即可复用 ``conn.cursor()`` 接口。
    """

    class _Cursor:
        def __init__(self, res):
            self.res = res
            self.description = None
            self._rows = []

        def execute(self, sql):
            entry = self.res.get(sql)
            if entry is None:
                raise RuntimeError('批量采集未覆盖该 SQL: %s' % str(sql)[:60])
            if isinstance(entry, tuple):
                raise RuntimeError(str(entry[1]) if len(entry) > 1 else '采集失败')
            rows = entry or []
            if rows and isinstance(rows[0], dict):
                self.description = [(k,) for k in rows[0].keys()]
                self._rows = [tuple(r.values()) for r in rows]
            else:
                self.description = None
                self._rows = []
            return self

        def fetchall(self):
            return list(self._rows)

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def close(self):
            self._rows = []

    def __init__(self, res):
        self.res = res or {}

    def cursor(self):
        return self._Cursor(self.res)

    def close(self):
        return None


class UnifiedCollector:
    """唯一采集循环 + 唯一写入口。"""

    def __init__(self, socketio=None, store=None, interval=DEFAULT_INTERVAL):
        self.store = store or UnifiedStore()
        self.socketio = socketio
        self._interval = max(MIN_INTERVAL, min(int(interval), MAX_INTERVAL))
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._host_cache = {}       # iid -> (ts_epoch, host_payload)
        self._host_lock = threading.Lock()
        self.running = False

    # ── 启停（供旧类 /app.py 的 /api/monitor/config 委托）──
    @property
    def is_running(self):
        return self._running

    @property
    def interval(self):
        return self._interval

    def start(self, interval=None):
        if interval:
            self.set_interval(interval)
        with self._lock:
            if self._running:
                return
            self._running = True
            self.running = True
            self.store.running = True
            self.store.interval = self._interval
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name='unified-collector')
            self._thread.start()
        print('[unified] 统一监控采集器已启动（间隔 %ds，单一循环）' % self._interval,
              flush=True)

    def stop(self):
        with self._lock:
            self._running = False
        self.running = False
        self.store.running = False

    def set_interval(self, interval):
        try:
            v = int(interval)
        except (TypeError, ValueError):
            return
        self._interval = max(MIN_INTERVAL, min(v, MAX_INTERVAL))
        self.store.interval = self._interval

    def trigger_collect(self):
        """手动触发一轮（不阻塞调用方）。"""
        t = threading.Thread(target=self._round, daemon=True,
                             name='unified-collector-manual')
        t.start()

    def _loop(self):
        while True:
            if not self._running:
                return
            t0 = time.time()
            try:
                self._round()
            except Exception as e:
                print('[unified] 采集轮失败: %s' % e, flush=True)
            dt = self._interval - (time.time() - t0)
            end = time.time() + max(dt, 1)
            while time.time() < end:
                if not self._running:
                    return
                time.sleep(0.5)

    # ── 单轮 ──────────────────────────────────────────────
    def _round(self):
        from modules.pro.instance_manager import get_instance_manager
        from modules.monitor.engine import (
            get_monitor_engine, JDBC_ONLY_TYPES, JDBC_BATCH_DB_TYPES)
        from modules.monitor.screen_metrics import get_screen_collector
        from modules.pro.metrics_collector import get_collector
        import modules.monitor.queries as mq

        try:
            instances = [i for i in get_instance_manager().get_all_instances_decrypted()
                         if i.get('enabled', True) and i.get('host')]
        except Exception as e:
            print('[unified] 实例列表获取失败: %s' % e, flush=True)
            return
        if not instances:
            return

        cur_ids = [str(i.get('id')) for i in instances]
        self.store.drop_instances(cur_ids)

        # 首轮 / 新增实例：先放 pending 占位帧，大屏秒出结构（只写内存）
        screen = get_screen_collector()
        for inst in instances:
            iid = str(inst.get('id'))
            if not self.store.get_frame(iid):
                self.store.set_pending(iid, screen._pending_snap(inst, time.time()))

        results = []
        with cf.ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(instances))) as pool:
            futures = {pool.submit(self._collect_instance, inst): inst
                       for inst in instances}
            for fut in cf.as_completed(futures):
                inst = futures[fut]
                try:
                    frame = fut.result()
                except Exception as e:
                    print('[unified] 实例采集异常 %s: %s'
                          % (inst.get('name'), e), flush=True)
                    frame = self._error_frame(inst, str(e))
                if frame:
                    results.append(frame)

        ts = time.time()
        snap = self.store.get_snapshot()
        total_qps = sum((s.get('qps') or 0) for s in snap.values())
        total_tps = sum((s.get('tps') or 0) for s in snap.values())
        self.store.append_trend({'ts': ts, 'qps': round(total_qps, 1),
                                 'tps': round(total_tps, 1)})
        self.store.append_conn_history({
            'ts': ts,
            'instances': [{
                'id': f['iid'],
                'label': (f.get('screen') or {}).get('label') or f['iid'],
                'total': ((f.get('screen') or {}).get('conn') or {}).get('total', 0),
                'max_conn': ((f.get('screen') or {}).get('conn') or {}).get('max_conn', 0),
                'db_type': (f.get('screen') or {}).get('db_type', ''),
            } for f in results if not f.get('pending')],
        })
        self.store.note_round(ts)

        # socketio 推流：一次一轮，首页实时图表按原事件契约收流
        if self.socketio and results:
            try:
                self.socketio.emit(
                    'metrics',
                    {'snapshots': [f.get('home') or {} for f in results
                                   if f.get('home')],
                     'ts': datetime.now().isoformat()},
                    room='monitor',
                )
            except Exception:
                pass

        # 历史回放落库（白名单投影，异步写入由 history_store 自己保证）
        try:
            from modules.monitor.history_store import get_history_store
            hs = get_history_store()
            for f in results:
                s = f.get('screen') or {}
                if s.get('status') != 'pending' and not f.get('pending'):
                    try:
                        hs.write_snap(s)
                    except Exception:
                        pass
        except Exception:
            pass

        # 告警状态机（读统一快照，与原 ScreenCollector._round 同口径）
        try:
            from modules.monitor.alert_notify import get_alert_tracker
            get_alert_tracker().update([dict(s) for s in snap.values()])
        except Exception as e:
            print('[unified][alert] 告警评估失败: %s' % e, flush=True)
        return results

    def _error_frame(self, inst, err):
        """采集线程整体异常时仍落一帧，避免该实例在大屏永久 pending。"""
        from modules.monitor.screen_metrics import get_screen_collector
        iid = str(inst.get('id') or '')
        if not iid:
            return None
        snap = get_screen_collector()._pending_snap(inst, time.time(), err=err)
        snap['status'] = 'down'
        return {
            'iid': iid, 'ts': datetime.now().isoformat(), 'ts_epoch': time.time(),
            'home': {'instance_id': iid, 'name': inst.get('name'),
                     'db_type': inst.get('db_type'), 'available': False,
                     'error': err[:200], 'ts': datetime.now().isoformat()},
            'screen': snap, 'conn': {}, 'slow': {}, 'host_res': None,
        }

    # ── 单实例：唯一连接 / 唯一批量子进程 ─────────────────────
    def _collect_instance(self, inst):
        from modules.monitor.engine import (
            get_monitor_engine, JDBC_ONLY_TYPES, JDBC_BATCH_DB_TYPES)
        from modules.monitor.screen_metrics import get_screen_collector
        from modules.pro.metrics_collector import get_collector
        import modules.monitor.queries as mq

        iid_raw = inst.get('id')
        iid = str(iid_raw or '')
        if not iid:
            return None
        raw = (inst.get('db_type') or '').strip().lower()
        norm = mq.normalize_db_type(raw)
        label = '%s (%s:%s)' % (inst.get('name', iid),
                                inst.get('host', '?'), inst.get('port', '?'))
        engine = get_monitor_engine()
        screen = get_screen_collector()
        metrics = get_collector()

        ts_epoch = time.time()
        ts_iso = datetime.now().isoformat()

        home = {'instance_id': iid, 'name': inst.get('name'),
                'db_type': raw, 'ts': ts_iso}
        conn_d, slow_d = {}, {}
        screen_snap = None
        managed = None
        native_data = None
        batch_res = None
        # 隧道实例：直连原始 host:port 的 TCP 探针没有意义，改记本轮
        # 经隧道实际建连耗时。计时点必须在真正建连处（collect_one 命中的
        # connection_scope 是借用视图，在那里计时会接近 0）。
        tunnel_t0 = time.perf_counter() if uses_db_tunnel(inst) else None
        tunnel_connect_ms = None

        def _mark_tunnel_connect():
            if tunnel_t0 is not None:
                return round((time.perf_counter() - tunnel_t0) * 1000, 2)
            return None

        try:
            # ── 1) 准备本轮唯一的连接通道 ──────────────────────
            if raw in JDBC_ONLY_TYPES:
                # 一次 JDBC 批量子进程：连接/慢查/大屏 SQL + 首页深采 SQL
                conn_type, tmpl_key = JDBC_ONLY_TYPES[raw]
                sqls = engine._jdbc_batch_sqls(tmpl_key)
                if sqls:
                    engine._jdbc_query(inst, conn_type, sqls[0],
                                       template_key=tmpl_key)
                    tunnel_connect_ms = _mark_tunnel_connect()
                cache = (engine._jdbc_cache.get(iid_raw)
                         or engine._jdbc_cache.get(iid) or {})
                batch_res = cache.get('res') or {}
            elif raw == 'mongodb':
                # MongoClient 一条连接同时供首页深采与大屏原生通道使用；
                # 显式开启 ssh_tunnel 时由 open_mongo_connection 把端点改写为
                # 本地转发端口，隧道与连接同生命周期（与巡检/测试连接同口径）。
                from modules.monitor.native_collect import open_mongo_connection
                managed, cfg = open_mongo_connection(inst)
                connection_scope.bind(iid, managed)
                native_data = ('mongo', managed.conn, cfg)
                tunnel_connect_ms = _mark_tunnel_connect()
            elif raw in JDBC_BATCH_DB_TYPES or norm in ('gbase', 'db2', 'clickhouse'):
                # 无 python 驱动：engine 的 _jdbc_query 缓存保证一次子进程
                pass
            elif raw in ('redis', 'redis-cluster'):
                # 与 mongodb 同口径：本轮唯一一条 Redis 连接（含 SSH 隧道；
                # cluster 由 address_remap 为每个成员懒建隧道），首页与大屏
                # 原生通道共用；统一采集器负责关闭连接与全部隧道。
                from modules.monitor.native_collect import open_redis_connection
                managed, cfg = open_redis_connection(inst)
                connection_scope.bind(iid, managed)
                native_data = ('redis', managed.conn, cfg)
                tunnel_connect_ms = _mark_tunnel_connect()
            else:
                managed = open_db_connection(
                    inst, connect_timeout=QUERY_TIMEOUT,
                    query_timeout=QUERY_TIMEOUT,
                    probe_oracle_protocol=True)
                if managed is not None:
                    connection_scope.bind(iid, managed)
                    tunnel_connect_ms = _mark_tunnel_connect()

            # ── 2) 首页深采 ──────────────────────────────────
            if batch_res is not None:
                proxy = BatchResultConnection(batch_res)
                deep = metrics._collect_deep(raw, proxy)
                home.update(deep)
                metrics._compute_rates(iid, raw, home, deep)
                # 批量结果里：tuple 表示该 SQL 失败，None 表示未覆盖，
                # 空列表是「成功但无行」也算可达，不能误判成宕机。
                ok = any(not isinstance(v, tuple) and v is not None
                         for v in batch_res.values())
                home['available'] = bool(ok)
                home['latency_ms'] = None
            elif metrics is not None:
                # collect_host=False：宿主由统一采集器按 30s 节流集中采集
                home = metrics.collect_one(inst, collect_host=False)

            # collect_one() 独立调用时仍写 None（兼容脚本/测试）；只有统一
            # 采集器知道真实建连耗时，在这里覆盖并标注口径。
            if tunnel_connect_ms is not None and home.get('available') is not False:
                home['latency_ms'] = tunnel_connect_ms
                home['latency_source'] = 'tunnel_connect'
                home['probe_skipped'] = 'ssh_tunnel'

            # ── 3) 连接数 / 会话、慢查询（命中 scope 或 JDBC 缓存）──
            try:
                conn_d = engine._collect_conn(iid, norm, label)
            except Exception as e:
                conn_d = {'data': [], 'error': str(e), 'total': 0,
                          'max_conn': 0, 'connections': {}, 'usage_pct': 0,
                          'ts': time.time(), 'db_type': norm, 'label': label}
            try:
                slow_d = engine._collect_slow(iid, norm, label)
            except Exception as e:
                slow_d = {'data': [], 'error': str(e), 'ts': time.time(),
                          'db_type': norm, 'label': label}

            # ── 4) 大屏快照（含 SCREEN_SQLS 增量与容量）─────────
            if native_data is not None:
                from modules.monitor.native_collect import collect_native
                nd = collect_native(inst, shared=native_data)
            else:
                nd = None
            screen_snap = screen._collect_one(
                engine, inst, {iid: conn_d}, {iid: slow_d}, native_data=nd)
        except Exception as e:
            home.setdefault('available', False)
            home.setdefault('error', str(e)[:200])
            if screen_snap is None:
                screen_snap = screen._pending_snap(inst, ts_epoch, err=str(e)[:200])
                screen_snap['status'] = 'down'
        finally:
            connection_scope.unbind(iid)
            if managed is not None:
                managed.close()

        # ── 5) 大屏速率差值（状态统一存 UnifiedStore）──────────
        try:
            prev = self.store.get_rate_prev(iid)
            screen_snap, new_prev = screen._apply_rates(
                screen_snap, prev, self._interval)
            self.store.set_rate_prev(iid, new_prev)
        except Exception as e:
            print('[unified] 速率计算失败 %s: %s' % (label, e), flush=True)

        # ── 6) 宿主资源：与 DB 可达性解耦 + 30s 节流 ───────────
        host_payload = self._host_payload(iid, inst, metrics) or {}
        if host_payload:
            home.update(host_payload)
        host_res = {k: host_payload[k] for k in _HOST_KEYS
                    if host_payload.get(k) is not None} or None
        # ⚠️ 绝不把本轮 ts_epoch 刷成 host_sample_ts：_host_payload 给的是
        # 宿主**真实**采集时刻（失败时保留最后一次成功时刻）。旧实现每轮
        # 覆盖，导致 30s 节流复用的旧值被伪装成新采集，前端按该 ts 去重
        # 后每 15s 追加一个「值不变」的伪新点，折线看起来一直在实时更新。
        screen_snap['host_res'] = host_res
        screen_snap.setdefault('ts', ts_epoch)

        frame = {
            'iid': iid, 'ts': ts_iso, 'ts_epoch': ts_epoch,
            'home': home, 'screen': screen_snap,
            'conn': conn_d, 'slow': slow_d, 'host_res': host_res,
        }
        self.store.write_frame(frame)
        return frame

    def _host_payload(self, iid, inst, metrics):
        """返回本轮宿主指标，含**真实采样时刻**与数据新鲜度标记。

        缓存条目 ``(attempt_ts, last_ok_ts, last_ok_payload, last_error)``：
          - 30s 节流：窗口内不重复发起 SSH 采集，但仍按当前时间重算
            ``host_age_s``，让「历史数据 · N 分钟前」持续更新；
          - 本轮采集成功 → 刷新 last_ok_*，``host_stale=False``；
          - 本轮采集失败（主机宕机 / SSH 不通 / 未配 SSH）→ 保留最后一次
            成功的数值供用户参考，但打上 ``host_stale=True``、失败原因与
            **真实**采样时刻，绝不把旧值伪装成新数据；从未成功过则只有
            unavailable，前端按原有「宿主资源不可用」展示。
        """
        now = time.time()
        with self._host_lock:
            cached = self._host_cache.get(iid)
            if cached and now - cached[0] < HOST_INTERVAL:
                return self._host_view(cached, now)

        tmp = {}
        err = None
        try:
            if metrics is not None:
                metrics._attach_host(tmp, iid, inst)
        except Exception as e:
            err = str(e)[:200]
            tmp = {}
        payload = {k: v for k, v in tmp.items() if k.startswith('host_')}
        has_values = any(payload.get(k) is not None for k in
                         ('host_cpu', 'host_mem', 'host_swap_pct', 'host_load1'))
        ok = has_values and payload.get('host_collector_source') != 'unavailable'

        with self._host_lock:
            prev = self._host_cache.get(iid)
            if ok:
                entry = (now, now, dict(payload), None)
            else:
                reason = (payload.get('host_error') or err
                          or '宿主采集失败（无数据）')
                entry = (now, prev[1] if prev else None,
                         prev[2] if prev else None, reason)
            self._host_cache[iid] = entry
            return self._host_view(entry, now)

    @staticmethod
    def _host_view(entry, now):
        """把缓存条目投影成本帧 payload（不改缓存内容）。"""
        attempt_ts, ok_ts, ok_payload, err = entry
        if ok_payload:
            out = dict(ok_payload)
            ts = ok_ts or attempt_ts
            out['host_sample_ts'] = ts
            out['host_last_ok_ts'] = ts
            out['host_age_s'] = round(max(0.0, now - ts), 1)
            out['host_stale'] = bool(err)
            if err:
                # 来源保留最后一次真实来源（ebpf/psutil/local…），错误单独带出；
                # 前端据此显示「宿主采集中断：<原因>」而非笼统的不可用。
                out['host_error'] = err
            return out
        # 从未成功采集过：没有数值可复用，保持原有「宿主资源不可用」语义
        return {
            'host_collector_source': 'unavailable',
            'host_error': err or '宿主资源不可用',
            'host_sample_ts': attempt_ts,
            'host_stale': False,
            'host_age_s': 0.0,
        }

    # ── 供旧 ScreenCollector._commit_snap 兼容委托 ────────────
    def commit_snap(self, snap):
        """兼容入口：旧代码若直接调 _commit_snap，仍走统一存储。"""
        from modules.monitor.screen_metrics import get_screen_collector
        screen = get_screen_collector()
        iid = str(snap.get('id') or '')
        if not iid:
            return snap
        prev = self.store.get_rate_prev(iid)
        snap, new_prev = screen._apply_rates(snap, prev, self._interval)
        self.store.set_rate_prev(iid, new_prev)
        frame = self.store.get_frame(iid) or {
            'iid': iid, 'ts': datetime.now().isoformat(),
            'ts_epoch': time.time(), 'home': {}, 'conn': {}, 'slow': {},
        }
        frame['screen'] = snap
        frame['host_res'] = snap.get('host_res')
        self.store.write_frame(frame)
        return snap


# ── 进程内单例 ──────────────────────────────────────────────
_unified_collector = None
_unified_lock = threading.Lock()


def get_unified_collector():
    global _unified_collector
    if _unified_collector is None:
        with _unified_lock:
            if _unified_collector is None:
                _unified_collector = UnifiedCollector()
    return _unified_collector


def set_unified_collector(c):
    global _unified_collector
    _unified_collector = c
    return c
