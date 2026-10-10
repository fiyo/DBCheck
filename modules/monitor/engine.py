# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
实时监控引擎 — 多数据源连接、查询执行、后台定时采集、内存缓冲

架构：
- MonitorEngine 单例：管理所有数据源的监控采集
- _connect_and_query(): 通用连接+查询方法，支持7种数据库类型
- _collect_slow_queries() / _collect_connections(): 采集单个数据源
- _background_loop(): 后台定时采集线程
- 环形缓冲区存储历史连接数数据（最近 72 条，每条记录所有数据源）
"""

import time
import os
import sys
import threading
import json
import logging
from collections import deque
from modules.pro.instance_manager import get_instance_manager
from modules.core.paths import PROJECT_ROOT
import modules.monitor.queries as mq
from modules.monitor.screen_metrics import SCREEN_SQLS

# 监控专属 logger：经 loghub 汇聚到「运行日志」页（category=monitor），且默认不写
# 共享控制台（由 MonitorConsoleFilter 过滤），从根上避免污染巡检 / 控制台输出。
_log = logging.getLogger('dbcheck.monitor')


# ── JDBC 批量采集通道 ─────────────────────────────────────────
# gbase / db2 / clickhouse 无 python 原生驱动（或驱动不可用），统一走
# 「独立非 gevent 子进程 + JDBC」通道：主进程绝不 startJVM（否则 JPype/JVM
# 与 gevent hub 死锁，整个 Web UI 冻结）。每轮每实例只 spawn 一次子进程，
# 一次跑完该实例全部采集 SQL（连接/慢查/性能计数/容量），结果缓存供本轮复用。
JDBC_BATCH_DB_TYPES = frozenset(('gbase', 'db2', 'clickhouse'))
JDBC_BATCH_TTL = 12.0        # 批量结果缓存时长（s），略短于采样间隔 15s
JDBC_BATCH_TIMEOUT = 90      # 子进程硬超时（含 JVM 冷启动 4~10s）

# ── JDBC 专属数据源（*_jdbc）─────────────────────────────────
# 这些类型被用户配置成 *_jdbc 的原因就是「python 原生驱动连不上」（旧版
# Oracle / TCPS / EZConnect / TNS 别名等），且 jdbc_url 优先于 host/port
# （见 instance_manager 字段注释）；metrics_collector 侧早有约定
# 「oracle_jdbc 一律走插件 JDBC，绝不走 oracledb」。监控引擎此前把它们
# 归一成 oracle/pg 走 oracledb thin / psycopg2 直连 → 实时监控（pro collector，
# 走 JDBC 插件）有数据、监控大屏（MonitorEngine）判宕机。
# 修复：路由进 JDBC 批量子进程通道（主进程绝不起 JVM）；连接器用原始插件
# id（oracle_jdbc/uxdb），采集 SQL 模板用归一键（oracle/pg）。
JDBC_ONLY_TYPES = {
    # raw db_type   → (JDBC 连接器 db_type, monitor 模板键)
    'oracle_jdbc': ('oracle_jdbc', 'oracle'),
    'uxdb_jdbc':   ('uxdb',        'pg'),
}


# ── 驱动异常 → 友好提示 ──────────────────────────────────────
# 驱动原始异常（如 "<class 'dmPython.Connection'> returned a result with
# an exception set"）对用户无意义，按常见根因归类成可操作的提示；
# 完整异常始终由调用方写入服务端日志，此处只负责面向用户的文案。
_ERROR_PATTERNS = [
    (('returned a result with an exception set',),
     '连接失败，请检查地址、端口、用户名密码及登录配置'),
    (('connection refused', '拒绝连接', '10061', 'cannot connect'),
     '无法连接数据库主机（数据库未启动或端口不通）'),
    (('timed out', 'timeout', '超时'),
     '连接超时（网络不通或防火墙拦截）'),
    (('getaddrinfo failed', 'name or service not known', 'unknown host',
      'nodename nor servname'),
     '主机地址无法解析，请检查地址配置'),
    (('access denied', 'authentication', 'login failed', 'ora-01017',
      'invalid username', 'password authentication', '密码'),
     '用户名或密码错误'),
    (('unknown database', 'database does not exist', 'database "',
      'specified database'),  # psycopg2: database "xxx" does not exist（库名夹在引号内）
     '数据库/服务名不存在，请检查库名配置'),
    (('ssl', 'certificate'),
     'SSL/证书校验失败'),
]


def _friendly_db_error(e):
    """把驱动异常翻译成用户可读的提示；未命中已知模式时给通用提示。"""
    low = str(e).lower()
    for keys, msg in _ERROR_PATTERNS:
        if any(k in low for k in keys):
            return msg
    return '数据库连接或查询异常，请检查实例配置或查看服务端日志'


class MonitorEngine:
    """实时监控引擎 — 全局单例"""

    # ── 默认配置 ──
    DEFAULT_INTERVAL = 10  # 采集间隔（秒）
    MAX_HISTORY = 72       # 历史记录条数（10s 一条 ≈ 2小时）
    QUERY_TIMEOUT = 15     # 单次查询超时（秒）

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._interval = self.DEFAULT_INTERVAL
        # 当前慢查询数据: {instance_id: {'data': [...], 'error': str, 'ts': float, 'db_type': str, 'label': str}}
        self._slow_queries = {}
        # 当前连接数据: {instance_id: {'data': [...], 'error': str, 'ts': float, 'total': int, 'max_conn': int, 'db_type': str, 'label': str}}
        self._connections = {}
        # 连接数历史: deque of {ts, instances: [{id, label, total, max_conn, db_type}]}
        self._conn_history = deque(maxlen=self.MAX_HISTORY)
        # 最后一次采集时间
        self._last_collect_ts = 0
        # JDBC 批量采集缓存: {iid: {'ts': float, 'res': {sql: rows|('__err__',msg)}, 'lock': Lock}}
        self._jdbc_cache = {}
        # 慢查询主 SQL 依赖缺失记忆（如 pg_stat_statements 扩展未安装）:
        # 这些实例每轮直接走 fallback，不再重复刷错误日志
        self._slow_prim_skip = set()

    # ═══════════════════════════════════════════════════════════
    #  启停控制
    # ═══════════════════════════════════════════════════════════

    def start(self, interval=None):
        """启动后台采集线程"""
        if interval:
            self._interval = max(5, min(interval, 60))
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._background_loop, daemon=True, name='MonitorCollector')
            self._thread.start()

    def stop(self):
        """停止后台采集"""
        with self._lock:
            self._running = False
            self._thread = None

    def set_interval(self, interval):
        """修改采集间隔（秒）"""
        self._interval = max(5, min(interval, 60))

    @property
    def is_running(self):
        return self._running

    @property
    def interval(self):
        return self._interval

    # ═══════════════════════════════════════════════════════════
    #  数据读取（线程安全）
    # ═══════════════════════════════════════════════════════════

    def get_slow_queries(self):
        """获取当前慢查询数据"""
        with self._lock:
            return dict(self._slow_queries)

    def get_connections(self):
        """获取当前连接数据"""
        with self._lock:
            return dict(self._connections)

    def get_conn_history(self):
        """获取连接数历史"""
        with self._lock:
            return list(self._conn_history)

    def get_status(self):
        """获取监控状态"""
        with self._lock:
            return {
                'running': self._running,
                'interval': self._interval,
                'last_collect_ts': self._last_collect_ts,
                'data_source_count': len(self._slow_queries),
            }

    # ═══════════════════════════════════════════════════════════
    #  手动触发采集
    # ═══════════════════════════════════════════════════════════

    def trigger_collect(self):
        """手动触发一次采集（不阻塞）"""
        t = threading.Thread(target=self._do_collect, daemon=True)
        t.start()

    # ═══════════════════════════════════════════════════════════
    #  后台循环
    # ═══════════════════════════════════════════════════════════

    def _background_loop(self):
        while self._running:
            try:
                self._do_collect()
            except Exception:
                pass
            # 按 interval 分段 sleep，便于响应 stop
            for _ in range(self._interval * 2):
                if not self._running:
                    break
                time.sleep(0.5)

    def _do_collect(self):
        """执行一轮采集"""
        try:
            im = get_instance_manager()
            # 使用解密后的实例：本轮采集会据此建立真实数据库连接。
            # 虽然 _collect_slow / _collect_conn 内部还会各自调用 get_instance_decrypted()，
            # 但此处统一取明文，避免后续维护者误以为 mask_password=False 就是明文密码。
            all_instances = im.get_all_instances_decrypted()
            instances = [i for i in all_instances if i.get('enabled', True)]
            # 明确标识：后台监控采集是独立后台任务，遍历全部已保存实例，
            # 与用户手动发起的巡检无关，避免日志混流被误判为「巡检串库」。
            _log.info("===== 后台监控采集任务（独立后台线程，遍历 %d 个实例，与手动巡检无关）=====", len(instances))
            if not instances:
                return

            # 采集慢查询
            new_slow = {}
            new_conn = {}
            conn_history_items = []

            for inst in instances:
                iid = inst['id']
                if not inst.get('host'):
                    _log.info("跳过空 host 实例: %s", iid)
                    continue
                db_type = mq.normalize_db_type(inst.get('db_type', ''))
                label = f"{inst.get('name', iid)} ({inst.get('host', '?')}:{inst.get('port', '?')})"

                # 慢查询
                try:
                    sq = self._collect_slow(iid, db_type, label)
                    new_slow[iid] = sq
                except Exception as e:
                    _log.warning("慢查询采集失败 %s: %s", label, e)
                    new_slow[iid] = {
                        'data': [], 'error': _friendly_db_error(e),
                        'ts': time.time(), 'db_type': db_type, 'label': label,
                    }

                # 连接
                try:
                    cn = self._collect_conn(iid, db_type, label)
                    new_conn[iid] = cn
                    conn_history_items.append({
                        'id': iid,
                        'label': label,
                        'total': cn.get('total', 0),
                        'max_conn': cn.get('max_conn', 0),
                        'db_type': db_type,
                    })
                except Exception as e:
                    _log.warning("连接采集失败 %s: %s", label, e)
                    new_conn[iid] = {
                        'data': [], 'error': _friendly_db_error(e),
                        'ts': time.time(), 'total': 0, 'max_conn': 0,
                        'db_type': db_type, 'label': label,
                    }

            ts = time.time()
            with self._lock:
                self._slow_queries = new_slow
                self._connections = new_conn
                self._last_collect_ts = ts
                self._conn_history.append({
                    'ts': ts,
                    'instances': conn_history_items,
                })
        except Exception as e:
            _log.error("采集失败: %s", e)

    # ═══════════════════════════════════════════════════════════
    #  采集实现
    # ═══════════════════════════════════════════════════════════

    def _collect_slow(self, instance_id, db_type, label):
        sql = mq.SLOW_QUERY_TEMPLATES.get(db_type)
        if not sql:
            return {'data': [], 'error': f'不支持的类型: {db_type}',
                    'ts': time.time(), 'db_type': db_type, 'label': label}

        fallback_sql = mq.SLOW_QUERY_FALLBACK_TEMPLATES.get(db_type)
        rows = None
        if instance_id not in self._slow_prim_skip:
            try:
                rows = self._connect_and_query(instance_id, sql)
            except Exception as e:
                low = str(e).lower()
                # 依赖对象缺失（如 pg_stat_statements / performance_schema 未安装）→
                # 记忆化：首行错误摘要打印一次，后续轮直接 fallback，不再重复刷日志
                if fallback_sql and ('does not exist' in low or "doesn't exist" in low):
                    self._slow_prim_skip.add(instance_id)
                    _log.info("%s 慢查询主 SQL 依赖缺失（%s），后续轮次直接使用 fallback",
                              label, str(e).splitlines()[0][:100])
                else:
                    _log.warning("慢查询 SQL 失败 %s: %s", label, e)
                if not fallback_sql:
                    return {'data': [], 'error': _friendly_db_error(e),
                            'ts': time.time(), 'db_type': db_type, 'label': label}
        if rows is None:
            # fallback：主 SQL 失败降级，或已记忆依赖缺失直接走此路（静默）
            try:
                rows = self._connect_and_query(instance_id, fallback_sql)
                if instance_id not in self._slow_prim_skip:
                    _log.info("%s 使用 fallback 慢查询 SQL", label)
            except Exception as fb:
                _log.warning("慢查询 fallback 亦失败 %s: %s", label, fb)
                return {'data': [], 'error': _friendly_db_error(fb),
                        'ts': time.time(), 'db_type': db_type, 'label': label}

        result = {
            'data': rows,
            'error': None,
            'ts': time.time(),
            'db_type': db_type,
            'label': label,
        }
        return result

    def _collect_conn(self, instance_id, db_type, label):
        conn_sql = mq.CONNECTION_TEMPLATES.get(db_type)
        if not conn_sql:
            return {'data': [], 'error': f'不支持的类型: {db_type}',
                    'ts': time.time(), 'total': 0, 'max_conn': 0,
                    'connections': {}, 'usage_pct': 0,
                    'db_type': db_type, 'label': label}

        try:
            rows = self._connect_and_query(instance_id, conn_sql)
        except Exception as e:
            _log.warning("连接 SQL 失败 %s: %s", label, e)
            return {'data': [], 'error': _friendly_db_error(e),
                    'ts': time.time(), 'total': 0, 'max_conn': mq.MAX_CONNECTION_DEFAULTS.get(db_type, 100),
                    'connections': {'active': 0, 'idle': 0, 'blocked': 0}, 'usage_pct': 0,
                    'db_type': db_type, 'label': label}

        # 获取最大连接数
        max_sql = mq.MAX_CONN_QUERY_SQL.get(db_type)
        max_conn = mq.MAX_CONNECTION_DEFAULTS.get(db_type, 100)
        if max_sql:
            try:
                max_rows = self._connect_and_query(instance_id, max_sql)
                if max_rows and max_rows[0]:
                    val = list(max_rows[0].values())[0]
                    try:
                        max_conn = int(val)
                    except (ValueError, TypeError):
                        pass
            except Exception:
                pass

        total = len(rows)
        # 统计连接状态分布
        active = 0
        idle = 0
        blocked = 0
        for r in rows:
            state_val = None
            for k, v in r.items():
                if 'state' in k.lower():
                    state_val = str(v).lower() if v else ''
                    break
            if state_val in ('sleeping', 'idle', 'inactive'):
                idle += 1
            elif state_val in ('waiting', 'wait', 'blocked', 'locked'):
                blocked += 1
            else:
                active += 1

        result = {
            'data': rows,
            'error': None,
            'ts': time.time(),
            'total': total,
            'max_conn': max_conn,
            'connections': {'active': active, 'idle': idle, 'blocked': blocked},
            'usage_pct': round(total / max_conn * 100, 1) if max_conn > 0 else 0,
            'db_type': db_type,
            'label': label,
        }
        return result

    # ═══════════════════════════════════════════════════════════
    #  JDBC 批量子进程采集（gbase / db2 / clickhouse）
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _jdbc_batch_sqls(db_type):
        """该类型一轮采集所需的全部 SQL（模板去重，主/备选全带上）。"""
        sqls, seen = [], set()

        def _add(s):
            if s and s not in seen:
                seen.add(s)
                sqls.append(s)

        for src in (mq.CONNECTION_TEMPLATES, mq.MAX_CONN_QUERY_SQL,
                    mq.SLOW_QUERY_TEMPLATES, mq.SLOW_QUERY_FALLBACK_TEMPLATES):
            _add(src.get(db_type))
        # 大屏指标 SQL：family 条目为 {key: sql}，逐条带上（repl/repl8 同理）
        fam_sqls = SCREEN_SQLS.get(db_type) or {}
        for s in fam_sqls.values():
            _add(s)
        return sqls

    def _jdbc_query(self, inst, db_type, sql, timeout=None, template_key=None):
        """JDBC 批量通道查询单条 SQL：结果来自每轮一次的子进程批量采集缓存。

        首个调用方（引擎采集线程或大屏采样线程）触发一次子进程批量执行，
        同轮内其余查询（含另一侧线程的 run_query）直接读缓存，避免每条 SQL
        都冷启动一次 JVM（4~10s）。inst 为已解密实例 dict。
        ``template_key``：采集 SQL 模板键（oracle/pg 等），缺省同 db_type；
        *_jdbc 专属类型的连接器 id 与模板键不同（oracle_jdbc vs oracle）。
        """
        instance_id = inst.get('id')
        cache = self._jdbc_cache.get(instance_id)
        if cache and time.time() - cache['ts'] <= JDBC_BATCH_TTL:
            return self._jdbc_pick(cache['res'], sql)

        lock = cache['lock'] if cache else threading.Lock()
        with lock:
            cache = self._jdbc_cache.get(instance_id)
            if cache and time.time() - cache['ts'] <= JDBC_BATCH_TTL:
                return self._jdbc_pick(cache['res'], sql)
            res = self._jdbc_run_batch(inst, db_type, timeout,
                                       template_key=template_key)
            self._jdbc_cache[instance_id] = {'ts': time.time(), 'res': res,
                                             'lock': lock}
            return self._jdbc_pick(res, sql)

    @staticmethod
    def _jdbc_pick(res, sql):
        """从批量结果中取单条 SQL 的行；错误标记转成异常（沿用友好文案链路）。"""
        entry = res.get(sql)
        if entry is None:
            raise RuntimeError('该类型批量采集未覆盖此 SQL: %s' % sql[:60])
        if isinstance(entry, tuple):
            raise RuntimeError(entry[1])
        return entry

    def _jdbc_run_batch(self, inst, db_type, timeout=None, template_key=None):
        """spawn 隔离子进程，一次跑完该实例本轮全部采集 SQL。

        参数经 stdin 临时文件传入（密码不进进程列表），结果取 stdout 中
        最后一个以 { 开头的行（与 jdbc_test_cli / jdbc_metrics_cli 同源约定）。
        ``db_type`` 传给 JDBC 连接器（原始插件 id，如 oracle_jdbc/uxdb）；
        ``template_key`` 决定采集 SQL 模板集合（oracle/pg 等），缺省同 db_type。
        """
        import subprocess as _sp
        import tempfile as _tf

        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, '--jdbc-collect-cli']
        else:
            cmd = [sys.executable, os.path.join(
                str(PROJECT_ROOT), 'modules', 'monitor', 'jdbc_collect_cli.py')]

        sqls = self._jdbc_batch_sqls(template_key or db_type)
        payload = {
            'db_type': db_type,
            'host': inst.get('host'),
            'port': inst.get('port'),
            'user': inst.get('user') or '',
            'password': inst.get('password') or '',
            'database': inst.get('database') or inst.get('service_name') or '',
            # Oracle 专属字段必须透传：监控子进程此前漏传 service_name/sid/
            # use_sid/sysdba，导致 build_jdbc_url 对 oracle_jdbc 落到硬编码默认
            # 'ORCLCDB'（或旧 SID 格式默认 'ORCL'），连错服务名 → ORA-12514 →
            # 大屏误判宕机；而测试连接（插件 test_connection）正确传 service_name
            # → 测试过、监控挂。sys 用户必须以 SYSDBA 登录，漏传则同样连不上。
            'service_name': inst.get('service_name') or '',
            'sid': inst.get('sid') or '',
            'use_sid': bool(inst.get('use_sid', False)),
            'sysdba': bool(inst.get('sysdba', False)),
            'jdbc_url': inst.get('jdbc_url') or '',
            'ssl': bool(inst.get('ssl', False)),
            'driver_version': inst.get('driver_version') or '',
            'gbase_server_name': inst.get('gbase_server_name') or '',
            'queries': sqls,
        }

        _in_fd, _in_path = _tf.mkstemp(prefix='dbc_mjdbc_in_', suffix='.json')
        _out_fd, _out_path = _tf.mkstemp(prefix='dbc_mjdbc_out_', suffix='.log')
        os.close(_out_fd)
        try:
            with os.fdopen(_in_fd, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=True)

            env = os.environ.copy()
            env['DBCheck_NO_GEVENT_PATCH'] = '1'   # 子进程绝不能被 monkey-patch
            env['PYTHONIOENCODING'] = 'utf-8'

            kw = {}
            if os.name == 'nt':
                kw['creationflags'] = (getattr(_sp, 'CREATE_NO_WINDOW', 0x08000000)
                                       | getattr(_sp, 'CREATE_NEW_PROCESS_GROUP', 0x00000200))
            else:
                kw['start_new_session'] = True

            with open(_in_path, 'r', encoding='utf-8') as fin, \
                    open(_out_path, 'w', encoding='utf-8', errors='replace') as fout:
                proc = _sp.Popen(cmd, stdin=fin, stdout=fout, stderr=_sp.STDOUT,
                                 env=env, cwd=str(PROJECT_ROOT), **kw)
                try:
                    rc = proc.wait(timeout=timeout or JDBC_BATCH_TIMEOUT)
                except _sp.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    raise RuntimeError('JDBC 采集子进程超时（%ds）'
                                       % (timeout or JDBC_BATCH_TIMEOUT))

            # 解析输出：最后一个以 { 开头的行是结构化结果
            result = None
            with open(_out_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('{'):
                        result = line
            if result is None:
                # 附带子进程输出尾部，便于定位 spawn/导入失败的真实原因
                with open(_out_path, 'r', encoding='utf-8', errors='replace') as f:
                    _tail = f.read()[-400:]
                raise RuntimeError('JDBC 采集子进程无结果输出（exit=%s）: %s'
                                   % (rc, _tail.replace('\n', ' | ')))
            try:
                data = json.loads(result)
            except Exception as e:
                raise RuntimeError('JDBC 采集子进程输出解析失败: %s' % e)

            fatal = (data or {}).get('fatal')
            res = {}
            for i, s in enumerate(sqls):
                entry = (data or {}).get('q%d' % i)
                if fatal:
                    res[s] = ('__err__', fatal)
                elif entry is None or entry.get('error'):
                    res[s] = ('__err__', (entry or {}).get('error') or '采集失败')
                else:
                    res[s] = entry.get('rows') or []
            return res
        finally:
            for p in (_in_path, _out_path):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # ═══════════════════════════════════════════════════════════
    #  通用连接+查询
    # ═══════════════════════════════════════════════════════════

    def run_query(self, instance_id, sql, timeout=None):
        """公共查询入口：供大屏指标适配层（screen_metrics）等外部复用连接逻辑。"""
        return self._connect_and_query(instance_id, sql, timeout=timeout)

    def _connect_and_query(self, instance_id, sql, timeout=None):
        """连接到指定数据源并执行 SQL，返回 list of dicts"""
        if timeout is None:
            timeout = self.QUERY_TIMEOUT

        im = get_instance_manager()
        inst = im.get_instance_decrypted(instance_id)
        if not inst:
            raise ValueError(f"实例 {instance_id} 不存在")

        # 归一到模板键（oracle_jdbc→oracle、tdsqlc_mysql→mysql 等）再分派：
        # _create_connection 的 python 驱动分支按协议族分派（TDSQL-C 兼容 MySQL
        # 协议、oracle_jdbc 实例字段与 oracle 相同），与 jdbc 插件 id 无关。
        raw_type = (inst.get('db_type') or '').strip().lower()
        db_type = mq.normalize_db_type(raw_type)

        # *_jdbc 专属数据源：必须走 JDBC 子进程通道，绝不能被归一成
        # oracle/pg 后用 python 原生驱动直连（否则实时监控有数据、大屏判宕机）
        if raw_type in JDBC_ONLY_TYPES:
            conn_type, tmpl_key = JDBC_ONLY_TYPES[raw_type]
            return self._jdbc_query(inst, conn_type, sql, timeout=timeout,
                                    template_key=tmpl_key)

        # gbase/db2/clickhouse：JDBC 批量子进程通道（主进程绝不起 JVM）
        if db_type in JDBC_BATCH_DB_TYPES:
            return self._jdbc_query(inst, db_type, sql, timeout=timeout)

        password = inst['password']
        conn = None
        cursor = None

        try:
            conn = self._create_connection(inst, password, db_type, timeout)
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description]
            rows = []
            for row in cursor.fetchall():
                row_dict = {}
                for i, col in enumerate(columns):
                    val = row[i]
                    # 序列化非标准类型
                    if val is not None and not isinstance(val, (int, float, str, bool)):
                        try:
                            val = str(val)
                        except Exception:
                            val = None
                    # 列名统一小写：dmPython/oracledb 等驱动返回大写标识符，
                    # 下游消费者（screen_metrics/前端模板）一律按小写键取值
                    row_dict[col.lower() if isinstance(col, str) else col] = val
                rows.append(row_dict)
            return rows
        finally:
            try:
                if cursor:
                    cursor.close()
            except Exception:
                pass
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    def _create_connection(self, inst, password, db_type, timeout):
        """根据 db_type 创建数据库连接"""
        host = inst.get('host', '')
        if not host:
            raise ValueError('主机地址不能为空')
        port = int(inst['port'])
        user = inst['user']

        if db_type in ('mysql', 'mariadb', 'oceanbase', 'starrocks', 'doris'):
            # MariaDB / OceanBase MySQL 租户与 MySQL 协议/参数高度兼容，复用同款 pymysql 连接逻辑。
            # OceanBase MySQL 租户的 database 即租户名，默认 sys。
            import pymysql
            db_name = inst.get('database')
            if db_type == 'oceanbase':
                db_name = db_name or 'sys'
            elif db_name is None:
                db_name = 'mysql'
            return pymysql.connect(
                host=host, port=port, user=user, password=password,
                database=db_name,
                charset='utf8mb4', connect_timeout=timeout, read_timeout=timeout,
            )

        elif db_type in ('postgresql', 'pg', 'hgdb', 'kingbase', 'uxdb', 'vastbase',
                         'highgo', 'halodb', 'opengauss', 'greenplum'):
            # 国产 PG 线协议系：瀚高/人大金仓/优炫/羲和直连 PG；Vastbase（海量 G100）
            # 基于 openGauss 内核（openGauss 源自 PG 9.2 分支），仍保留 PG 兼容层，
            # 故同一 psycopg2 + pg 系统视图路径即可覆盖，无需分叉。
            # ⚠️ 本分支收到的是归一后 db_type（hgdb/kingbase/vastbase 等已归一为 pg），
            # default_db 映射必须用实例的原始 db_type 查，否则键全部落空、
            # 库名空缺时一律回落 postgres（瀚高无 postgres 库 → FATAL 报错）。
            # 兜底回落「用户名同名库」（PG 惯例：默认库与用户同名，与 JDBC
            # 数据源测试不指定库名时同口径）。
            raw_type = (inst.get('db_type') or '').lower()
            default_db = {'hgdb': 'highgo', 'kingbase': 'kingbase',
                          'uxdb': 'uxdb',
                          # Vastbase 基于 openGauss 内核，openGauss/Vastbase 均保留 PG
                          # 兼容层且默认库为 postgres，故与 opengauss/greenplum 同取 postgres。
                          'vastbase': 'postgres', 'highgo': 'highgo',
                          'halodb': 'halo',
                          'opengauss': 'postgres', 'greenplum': 'postgres'}.get(raw_type)
            import psycopg2
            return psycopg2.connect(
                host=host, port=port, user=user, password=password,
                dbname=inst.get('database') or default_db or user,
                client_encoding='UTF8', connect_timeout=timeout,
            )

        elif db_type == 'oracle':
            import oracledb
            # 必须用 makedsn 构造完整 easy-connect 描述符（DESCRIPTION=...），
            # 不能直接把 service_name 当 dsn 传入：裸服务名会被 oracledb 当作 TNS
            # net service name 去解析 tnsnames.ora，而无 config_dir 时会抛
            # DPY-4027: no configuration directory specified。
            svc = inst.get('service_name', '') or ''
            sid = inst.get('sid', '') or ''
            if svc:
                dsn = oracledb.makedsn(host=host, port=port, service_name=svc)
            elif sid:
                dsn = oracledb.makedsn(host=host, port=port, sid=sid)
            else:
                dsn = f"{host}:{port}/orcl"
            mode = oracledb.SYSDBA if inst.get('sysdba') else oracledb.AUTH_MODE_DEFAULT
            return oracledb.connect(user=user, password=password, dsn=dsn, mode=mode)

        elif db_type in ('sqlserver', 'sqlserver_jdbc', 'mssql'):
            # sqlserver_jdbc（插件标识）与 sqlserver 同为 SQL Server 线协议，
            # pyodbc/ODBC 直连（绝不走 JDBC 插件——JPype 进程内启动会与 gevent 死锁）。
            # 驱动分级探测：优先新版 ODBC Driver 17/18（支持 Encrypt），
            # 其次 Native Client；仅当只剩旧版 DBNETLIB 驱动时去掉加密参数
            #（它不认识 TrustServerCertificate/Encrypt，会报"无效的连接字符串属性"+SSL 错误）。
            import pyodbc
            drivers = []
            try:
                drivers = [d for d in pyodbc.drivers() if 'sql server' in d.lower()]
            except Exception:
                pass
            modern = [d for d in drivers if 'odbc driver' in d.lower()]
            native = [d for d in drivers if 'native client' in d.lower()]
            if modern:
                driver, legacy = '{%s}' % modern[-1], False
            elif native:
                driver, legacy = '{%s}' % native[0], False
            elif drivers:
                driver, legacy = '{%s}' % drivers[-1], True
            else:
                driver, legacy = '{ODBC Driver 17 for SQL Server}', False
            conn_str = (
                f"DRIVER={driver};"
                f"SERVER={host},{port};"
                f"UID={user};PWD={password};"
                f"Connect Timeout={timeout};"
            )
            if not legacy:
                conn_str += "TrustServerCertificate=yes;Encrypt=yes;"
            if inst.get('database'):
                conn_str += f"Database={inst['database']};"
            return pyodbc.connect(conn_str)

        elif db_type == 'dm':
            import dmPython
            dsn = f"{host}:{port}"
            return dmPython.connect(user=user, password=password, server=dsn)

        elif db_type == 'tidb':
            import pymysql
            return pymysql.connect(
                host=host, port=port, user=user, password=password,
                database=inst.get('database') or '',
                charset='utf8mb4', connect_timeout=timeout, read_timeout=timeout,
                autocommit=True,
            )

        elif db_type == 'ivorysql':
            import psycopg2
            return psycopg2.connect(
                host=host, port=port, user=user, password=password,
                dbname=inst.get('database') or 'ivorysql',
                client_encoding='UTF8', connect_timeout=timeout,
            )

        elif db_type == 'yashandb':
            try:
                import yasdb
            except ImportError:
                raise RuntimeError("YashanDB 驱动未安装，请先安装 yasdb 驱动后再使用崖山数据库监控。")
            return yasdb.connect(host=host, port=port, user=user, password=password)

        else:
            raise ValueError(f"不支持的数据库类型: {db_type}")


# ═══════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════

_monitor_engine = None
_monitor_lock = threading.Lock()


def get_monitor_engine():
    """获取 MonitorEngine 全局单例"""
    global _monitor_engine
    if _monitor_engine is None:
        with _monitor_lock:
            if _monitor_engine is None:
                _monitor_engine = MonitorEngine()
    return _monitor_engine
