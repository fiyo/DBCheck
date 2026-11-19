# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
pro/metrics_collector.py — 实时监控采集器（v2.10）

职责：
  1. 通用探针：TCP 连通探测 → 可用性 (up/down) + 响应延迟 (ms)，对所有 db_type 通用。
  2. 按 db_type 深采：MySQL/TiDB、PostgreSQL/Kingbase、Oracle、达梦 DM8。
     其余类型（SQL Server / YashanDB / GBase 等）降级为通用探针。
  3. 速率计算：对计数器型指标做快照差分 → QPS / TPS / 字节率。
  4. 断路器：实例连续失败达到阈值后退避一段时间，避免单个坏库拖死采集轮。
  5. 存储：SQLite 环形裁剪（每实例保留最近 N 个快照）。
  6. 推送：socketio.emit('metrics', {...}, room='monitor')，前端 socket.on('metrics') 收流。

设计要点：
  - 采集器运行在 web_ui 进程内（与 socketio 同进程），才能实时推流。
  - 连接信息取自 instance_manager 的 get_all_instances_decrypted()，密码已解密为明文；
    切勿改用 get_all_instances(mask_password=False)——那样拿到的是密文，会导致认证失败。
  - 每个指标的采集都包在 try/except 中，单点失败不影响整体循环。
"""

import os
import re
import json
import sys
import time
import socket
import sqlite3
import base64
import threading
import concurrent.futures as cf
from datetime import datetime

from modules.monitor.connection_factory import (
    open_db_connection, parse_oracle_target, oracle_protocol_probe,
    uses_db_tunnel)

# ── 配置 ───────────────────────────────────────────────
DEFAULT_INTERVAL = 30          # 采集间隔（秒）
DEFAULT_MAX_POINTS = 2000      # 每实例保留的快照数（环形裁剪）
CIRCUIT_FAIL_THRESHOLD = 5     # 连续失败多少次进入退避
CIRCUIT_COOLDOWN = 60          # 退避时长（秒）
CONNECT_TIMEOUT = 3            # 连接超时（秒）

# JVM 类型（oracle_jdbc / uxdb_jdbc）：依赖 JPype 在进程内启动 JVM。
# 这类连接**绝不能在 gevent monkey-patch 的主进程内执行**（会冻结 hub → 整个界面卡死），
# 必须隔离到干净子进程（modules/jdbc_metrics_cli.py）执行，与测试连接/巡检同源。
JVM_METRICS_DB_TYPES = ('oracle_jdbc', 'uxdb_jdbc')
# 子进程采集硬超时（秒）。Oracle thin 驱动连接不可达主机可能阻塞较久，超时就丢本轮。
JVM_METRICS_SUBPROC_TIMEOUT = 50

# 计数器型指标（用于差分算速率）——按 db_type 分派
COUNTER_KEYS = {
    'mysql': {'queries', 'bytes_received', 'bytes_sent', 'connections',
              'slow_queries', 'aborted_connects', 'threads_created'},
    'tidb': {'queries', 'bytes_received', 'bytes_sent', 'connections',
             'slow_queries', 'aborted_connects', 'threads_created'},
    'oceanbase': {'queries', 'ob_qps', 'ob_tps', 'bytes_received', 'bytes_sent',
                  'connections', 'slow_queries', 'aborted_connects', 'threads_created'},
    'postgresql': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit',
                   'deadlocks', 'conflicts', 'tup_returned', 'tup_fetched',
                   'tup_inserted', 'tup_updated', 'tup_deleted'},
    'pg': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit',
           'deadlocks', 'conflicts'},
    'ivorysql': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit', 'deadlocks'},
    'kingbase': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit', 'deadlocks'},
    # Vastbase G100（海量，openGauss/PG 内核）：pg_stat_database 系视图实测全通
    'vastbase': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit', 'deadlocks'},
    # HaloDB（羲和）为 PG14 内核血统，统计视图同 PG
    'halodb': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit', 'deadlocks'},
    'uxdb': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit',
             'deadlocks', 'conflicts', 'numbackends'},
    'uxdb_jdbc': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit',
                  'deadlocks', 'conflicts', 'numbackends'},
    'oracle': {'ora_user_commits', 'ora_physical_reads', 'ora_redo_size',
               'ora_user_calls', 'ora_session_logical_reads',
               'ora_db_block_gets', 'ora_consistent_gets'},
    'oracle_jdbc': {'ora_user_commits', 'ora_physical_reads', 'ora_redo_size',
                    'ora_user_calls', 'ora_session_logical_reads',
                    'ora_db_block_gets', 'ora_consistent_gets'},
    'sqlserver': {'io_read_count', 'io_write_count', 'io_read_bytes', 'io_write_bytes',
                  'mssql_batch_requests', 'mssql_transactions'},
    'mssql':   {'io_read_count', 'io_write_count', 'io_read_bytes', 'io_write_bytes',
                'mssql_batch_requests', 'mssql_transactions'},
    # sqlserver_jdbc 与 sqlserver 同协议同指标口径（pyodbc 直连，见 _connect）
    'sqlserver_jdbc': {'io_read_count', 'io_write_count', 'io_read_bytes', 'io_write_bytes',
                       'mssql_batch_requests', 'mssql_transactions'},
    # MongoDB 计数器（来自 serverStatus）：opcounters / 连接 / 网络 / 全局锁
    'mongodb': {
        'mongodb_opcounters_insert', 'mongodb_opcounters_query',
        'mongodb_opcounters_update', 'mongodb_opcounters_delete',
        'mongodb_opcounters_getmore', 'mongodb_opcounters_command',
        'mongodb_net_in', 'mongodb_net_out',
        'mongodb_network_requests', 'mongodb_global_lock_total',
    },
    # DM8 计数器名不固定，速率在深采中按 dm_ 前缀动态处理
}

# ── 首页深采 SQL（供 JDBC 批量子进程一次性带入）──────────────
# oracle_jdbc / uxdb_jdbc 的首页深采原本要单独 spawn 一个 jdbc_metrics_cli
# 子进程，与大屏的 jdbc_collect_cli 批量子进程重复。统一采集器现在把这些
# SQL 一并放进 _jdbc_batch_sqls，用 BatchResultConnection 从同一次批量结果
# 投影首页指标，每实例每轮只剩一次子进程。
# ⚠️ 字符串必须与 _collect_oracle / _collect_postgres / _collect_uxdb /
#    _collect_dm 里的字面量**逐字一致**（批量结果以 SQL 文本为键）。
DEEP_METRIC_SQLS = (
    "SELECT * FROM v$sysstat",
    "SELECT count(*) FROM v$session WHERE type = 'USER'",
    "SELECT count(*) FROM v$session WHERE username IS NOT NULL",
    "SELECT count(*) FROM v$session WHERE state = 'ACTIVE'",
    "SELECT count(*) FROM v$session WHERE state <> 'INACTIVE'",
    "SELECT count(*) FROM v$session",
    "SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, "
    "deadlocks, conflicts FROM pg_stat_database WHERE datname = current_database()",
    "SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, "
    "deadlocks, conflicts FROM ux_catalog.ux_stat_database",
    "SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))::float",
)

# ── 统计项名称 → 规范指标键 的跨版本映射 ──────────────────
# 用于 Oracle / 达梦 等「按名称取计数器」的场景。
# 设计原则：不写死任何单一版本——
#   1) 数值列（VALUE/STAT_VAL ...）在采集时动态探测，见 _fetch_name_value；
#   2) 统计项名称跨版本高度兼容，匹配策略为「精确名优先，子串兜底」，见 _map_stats。
_ORACLE_STAT_MAP = {
    'user_commits': ['user commits', 'user commit'],
    'physical_reads': ['physical reads', 'physical read'],
    'redo_size': ['redo size'],
    'user_calls': ['user calls', 'user call'],
    'session_logical_reads': ['session logical reads', 'session logical read'],
    'db_block_gets': ['db block gets', 'db block get'],
    'consistent_gets': ['consistent gets', 'consistent get'],
}
_DM_STAT_MAP = {
    'user_commits': ['user commit'],
    'physical_reads': ['physical read'],
    'redo_size': ['redo size'],
    'logical_reads': ['logical read', 'session logical read'],
    'db_block_gets': ['db block get'],
    'consistent_gets': ['consistent get'],
    'user_calls': ['user call'],
}


def _to_num(v):
    """把数据库返回的值尽量转成数字；失败返回 0。"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).strip().replace(',', '')
        if '.' in s:
            return float(s)
        return int(s)
    except (ValueError, TypeError):
        return 0


def _fetch_name_value(cur, sql, value_candidates=('STAT_VAL', 'STAT_VALUE', 'VALUE', 'VAL')):
    """执行 sql，按 NAME 列做键、动态选择数值列做值，返回 {name_lower: value}。

    解决不同版本「数值列名」不一致的问题（如达梦 stat_val / value、Oracle VALUE）。
    - 优先在 value_candidates 中匹配存在的列名；
    - 否则退避到「列名含 VAL 的非键列」；
    - 再否则退避到 NAME 之后的第一列。
    """
    cur.execute(sql)
    desc = cur.description
    if not desc:
        return {}
    cols = [str(c[0]).upper() for c in desc]
    key_idx = cols.index('NAME') if 'NAME' in cols else 0
    val_idx = None
    for cand in value_candidates:
        if cand in cols:
            val_idx = cols.index(cand)
            break
    if val_idx is None:
        for i, c in enumerate(cols):
            if i != key_idx and 'VAL' in c:
                val_idx = i
                break
    if val_idx is None:
        for i, c in enumerate(cols):
            if i != key_idx:
                val_idx = i
                break
    out = {}
    for row in cur.fetchall():
        try:
            key = str(row[key_idx]).strip().lower()
        except Exception:
            continue
        if not key:
            continue
        out[key] = _to_num(row[val_idx])
    return out


def _map_stats(stats, mapping):
    """stats: {name_lower: value}；mapping: {canonical: [候选名...]}。
    优先精确匹配，失败用子串兜底；返回 {canonical: value}。
    """
    out = {}
    for canonical, names in mapping.items():
        val = None
        for n in names:
            nl = n.lower()
            if nl in stats:
                val = stats[nl]
                break
        if val is None:
            # 子串兜底：收集所有含 needle 的键，取最长 needle 命中、同档取最短键，
            # 避免 'physical read total bytes' 这类汇总项被误当成基础统计。
            for needle in names:
                nl = needle.lower()
                hits = [k for k in stats if nl in k]
                if hits:
                    val = stats[min(hits, key=len)]
                    break
        if val is not None:
            out[canonical] = val
    return out


# ── 存储层 ──────────────────────────────────────────────
class MetricsStore:
    """SQLite 环形存储：每个快照存为一行 JSON，按实例裁剪到最近 N 个。"""

    def __init__(self, db_path: str, max_points: int = DEFAULT_MAX_POINTS):
        self.db_path = db_path
        self.max_points = max_points
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        with self.lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE(instance_id, ts)
                )
            """)
            conn.commit()

    def save_snapshot(self, instance_id: str, ts: str, snapshot: dict):
        with self.lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO metrics_snapshot(instance_id, ts, payload) VALUES(?,?,?)",
                (instance_id, ts, json.dumps(snapshot, ensure_ascii=False)),
            )
            # 环形裁剪：每实例只保留最近 max_points 个快照
            conn.execute("""
                DELETE FROM metrics_snapshot
                WHERE instance_id = ? AND id NOT IN (
                    SELECT id FROM metrics_snapshot
                    WHERE instance_id = ? ORDER BY ts DESC LIMIT ?
                )
            """, (instance_id, instance_id, self.max_points))
            conn.commit()

    def get_latest(self, instance_id: str) -> dict:
        with self.lock, sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT payload FROM metrics_snapshot WHERE instance_id = ? ORDER BY ts DESC LIMIT 1",
                (instance_id,),
            )
            r = cur.fetchone()
            if r:
                try:
                    return json.loads(r[0])
                except (ValueError, TypeError):
                    return {}
            return {}

    def get_recent(self, instance_id: str, limit: int = 120) -> list:
        with self.lock, sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT ts, payload FROM metrics_snapshot WHERE instance_id = ? ORDER BY ts DESC LIMIT ?",
                (instance_id, limit),
            )
            return [{'ts': r[0], 'metrics': json.loads(r[1])} for r in reversed(cur.fetchall())]


# ── 采集器 ──────────────────────────────────────────────
# ── 宿主采集（host/eBPF 子系统，自专业版合并；UXDB 分支保留不动）──
_SSH_GLOBAL_SEM = threading.Semaphore(4)
_ssh_host_locks = {}
_ssh_host_locks_guard = threading.Lock()

def _get_ssh_host_lock(host: str) -> threading.Lock:
    with _ssh_host_locks_guard:
        lk = _ssh_host_locks.get(host)
        if lk is None:
            lk = threading.Lock()
            _ssh_host_locks[host] = lk
        return lk

# 本机身份集合缓存（hostname / FQDN / 所有网卡 IP / 出口 IP），带 TTL。
# 网卡会因 DHCP、VPN、容器网络变动，故不做永久缓存，TTL 到期后重算。
_LOCAL_ID_CACHE = {'ts': 0.0, 'addrs': frozenset()}
_LOCAL_ID_TTL = 300.0


def _local_identities() -> frozenset:
    """返回「本机身份集合」（均为小写），用于判定某个地址是否指向中心机自身。

    覆盖：主机名、FQDN（及其首段）、hostname 解析出的所有 IP、所有网卡 IP、
    默认路由出口 IP。多网卡场景下，用户填写的往往是本机另一块网卡的地址，
    仅靠 hostname 解析会漏判，故必须枚举网卡。
    """
    import time as _t
    now = _t.time()
    cached_addrs = _LOCAL_ID_CACHE['addrs']
    if cached_addrs and (now - _LOCAL_ID_CACHE['ts']) < _LOCAL_ID_TTL:
        return cached_addrs

    ids = set()
    try:
        hn = (socket.gethostname() or '').lower()
        if hn:
            ids.add(hn)
        try:
            fqdn = (socket.getfqdn() or '').lower()
            if fqdn:
                ids.add(fqdn)
                ids.add(fqdn.split('.')[0])
        except Exception:
            pass
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ids.add(str(info[4][0]).lower())
    except Exception:
        pass

    # 所有网卡 IP（hostname 解析不全 / 多网卡 / 仅配 hosts 的场景靠这步兜住）
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                ids.add(str(a.address).lower())
    except Exception:
        pass

    # 默认路由出口 IP（UDP connect 不实际发包，仅用于选出出口网卡地址）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ids.add(str(s.getsockname()[0]).lower())
        finally:
            s.close()
    except Exception:
        pass

    result = frozenset(ids)
    _LOCAL_ID_CACHE['ts'] = now
    _LOCAL_ID_CACHE['addrs'] = result
    return result


def _is_self_host(host: str) -> bool:
    """判断给定主机地址是否指向中心机自身（DBCheck 所在机器）。

    用于两处：① SSH 远端采集失败时，仅当 ssh_host 指向本机才合法回退本机采集；
    ② 未配置 SSH 时，判定数据库实例是否真与 DBCheck 同机（决定能否采本机顶替）。

    地址缺失（本地文件库 / 未填主机）视为本机。
    """
    h = (host or '').strip().lower()
    # IPv6 字面量可能带方括号，如 [::1]
    if h.startswith('[') and h.endswith(']'):
        h = h[1:-1]
    if not h:
        return True
    if h in ('localhost', '127.0.0.1', '::1', '0.0.0.0', '::'):
        return True
    if h.startswith('127.'):          # 127.0.0.0/8 整段都是环回
        return True
    return h in _local_identities()

def _host_unavailable(reason: str) -> dict:
    """构造“宿主资源不可用”的结果 dict，携带原因，便于前端直接展示。"""
    return {'host_collector_source': 'unavailable', 'host_error': str(reason or '未知原因')}


# ── Windows 服务告警规则（实时监控快照口径）──────────────────────────
# 说明：规则引擎的 YAML 规则只对「巡检 context」求值（见 analyzer.py →
# rule_engine.analyze），而 Windows 服务指标来自实时监控快照，不进巡检 context。
# 若把服务规则写进 YAML，规则会被加载却永远拿不到参数，成为永不触发的死规则。
# 因此这两条规则落在采集器侧，对每轮 host_services 直接求值，阈值复用
# modules/monitor/monitor_thresholds.json（与大屏阈值同一份配置）。
_SERVICE_THRESHOLDS_DEFAULT = {
    'warn_service_mem_mb': 1024.0,
    'crit_service_mem_mb': 4096.0,
    'service_stop_level': 'crit',
}
_service_thresholds_cache = {'ts': 0.0, 'cfg': None}


def _service_thresholds() -> dict:
    """读取服务告警阈值（60s 缓存）。文件缺失/损坏时回退默认，绝不影响采集。"""
    now = time.time()
    cached = _service_thresholds_cache.get('cfg')
    if cached and now - _service_thresholds_cache['ts'] < 60:
        return cached
    cfg = dict(_SERVICE_THRESHOLDS_DEFAULT)
    try:
        p = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            '..', 'monitor', 'monitor_thresholds.json'))
        with open(p, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for key in cfg:
                if key not in raw:
                    continue
                if key == 'service_stop_level':
                    val = str(raw[key]).lower()
                    cfg[key] = val if val in ('warn', 'crit') else cfg[key]
                else:
                    try:
                        cfg[key] = max(0.0, float(raw[key]))
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass
    _service_thresholds_cache['cfg'] = cfg
    _service_thresholds_cache['ts'] = now
    return cfg


def evaluate_host_service_alerts(services, thresholds: dict = None) -> list:
    """两条服务规则求值，返回结构化告警列表（最多 50 条）。

    规则 1 service_stopped：自动启动（Auto）的服务未运行即告警；手动启动
      （Manual）的服务停止属正常，仅当用户显式点名该服务时才告警；
      Disabled 永不告警。
    规则 2 service_mem_high：工作集内存超过 warn/crit 阈值。共享宿主进程
      （svchost.exe 承载多服务）无法精确拆分，告警文案会明确标注该口径。

    注意：服务指标是「进程」口径而非 Windows 原生「服务」计数器——Windows
    没有按服务统计 CPU 的计数器，本实现取服务对应进程的两帧 CPU 时间差分。
    """
    th = thresholds or _service_thresholds()
    warn_mem = float(th.get('warn_service_mem_mb', 1024))
    crit_mem = float(th.get('crit_service_mem_mb', 4096))
    stop_level = str(th.get('service_stop_level', 'crit')).lower()
    if stop_level not in ('warn', 'crit'):
        stop_level = 'crit'
    alerts = []
    for svc in services or []:
        if not isinstance(svc, dict):
            continue
        name = str(svc.get('name') or svc.get('display_name') or '?')
        status = str(svc.get('status') or '')
        start_mode = str(svc.get('start_mode') or '')
        explicit = bool(svc.get('explicit'))
        if (status.lower() != 'running'
                and start_mode.lower() != 'disabled'
                and (start_mode.lower() == 'auto' or explicit)):
            alerts.append({
                'level': stop_level,
                'rule': 'service_stopped',
                'service': name,
                'message': '服务 %s 未运行（状态 %s，启动方式 %s）'
                           % (name, status or '未知', start_mode or '未知'),
            })
        mem = _to_num(svc.get('mem_mb'))
        if mem >= crit_mem:
            level, lim = 'crit', crit_mem
        elif mem >= warn_mem:
            level, lim = 'warn', warn_mem
        else:
            continue
        note = ''
        if svc.get('shared_process'):
            note = '（共享宿主进程，%s 个服务共用，不可精确拆分）' % (
                svc.get('shared_services') or '?')
        alerts.append({
            'level': level,
            'rule': 'service_mem_high',
            'service': name,
            'message': '服务 %s 内存 %.1f MB 超过 %.0f MB 阈值%s'
                       % (name, mem, lim, note),
        })
    return alerts[:50]


class MetricsCollector:
    """首页实时监控采集器 — 统一采集器的深采能力提供者 / 只读投影适配器。

    历史版本本类由 APScheduler 每 30s 独立 ``tick`` 一轮，自带连接与
    SQLite 存储；现在**不再调度、不再独立建连**（除非脱离统一采集器
    被单独调用），只保留按库型的深采函数、速率差分与宿主采集能力，
    统一由 UnifiedCollector 在同一条连接上调用，结果写 UnifiedStore。
    ``store`` 直接指向统一存储，``_read_host_metrics`` 等旧读取方零改动。
    """

    def __init__(self, socketio=None, db_path: str = None,
                 interval: int = DEFAULT_INTERVAL, store=None):
        if store is None:
            if db_path is None:
                from modules.core import paths
                db_path = os.path.join(str(paths.DATA_DIR), 'pro_metrics.db')
            store = MetricsStore(db_path)
        self.store = store
        self.socketio = socketio
        self.interval = interval
        self.running = False
        self._last = {}        # instance_id -> (prev_counter_metrics, prev_ts)
        self._last_host = {}   # instance_id -> (prev_host_metrics, prev_ts)；宿主磁盘速率差分
        self._fail = {}        # instance_id -> 连续失败计数
        self._cooldown = {}    # instance_id -> 退避截止时间戳
        self._lock = threading.Lock()

    @property
    def running(self):
        """运行状态取自唯一采集器（供首页 / API / 关停逻辑读取）。"""
        try:
            from modules.monitor.unified_collector import get_unified_collector
            return bool(get_unified_collector().is_running)
        except Exception:
            return self._running

    @running.setter
    def running(self, value):
        # app.py 关停段会写 ``_collector.running = False``；这里只记录标志，
        # 真正的循环开关在统一采集器上（由关停段显式调用 stop）。
        self._running = bool(value)

    # ── 连接 ──
    def _connect(self, inst: dict):
        """返回 ``ManagedConnection``（DBAPI 连接 + 可选 SSH 隧道）。

        统一采集器在一轮开始时已为当前实例建好唯一连接并绑定到
        ``connection_scope``；命中绑定时返回借用视图（``close()`` 为
        no-op），本轮所有采集通道共用这一条连接。未命中时按原逻辑
        由 connection_factory 自行建连（兼容独立调用 / 脚本 / 测试）。
        """
        from modules.monitor import connection_scope
        borrowed = connection_scope.borrowed(inst.get('id'))
        if borrowed is not None:
            return borrowed
        return open_db_connection(
            inst, connect_timeout=CONNECT_TIMEOUT,
            query_timeout=self._query_timeout(),
            probe_oracle_protocol=True)

    # ── 通用探针 ──
    def _tcp_probe(self, host, port) -> tuple:
        if not host or not port:
            return False, None
        t0 = time.time()
        s = None
        try:
            s = socket.create_connection((host, int(port)), timeout=CONNECT_TIMEOUT)
            latency = (time.time() - t0) * 1000
            return True, round(latency, 1)
        except Exception:
            return False, None
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass

    def _query_timeout(self) -> int:
        """单实例采集的硬上限（秒）。

        用于在连接层对「查询执行」施加超时，避免某个实例的慢查询/
        网络半通导致整个 tick 串行阻塞超过采集间隔。取间隔的 40%，
        下限 5s，确保即使极端情况下单实例也不会拖垮整轮。
        """
        return max(5, int(self.interval * 0.4))

    # ── Oracle 端口/协议探测（统一实现在 connection_factory，保留方法名兼容调用）──
    def _parse_oracle_target(self, inst: dict):
        return parse_oracle_target(inst)

    def _oracle_protocol_probe(self, host, port, protocol):
        return oracle_protocol_probe(host, port, protocol, timeout=CONNECT_TIMEOUT)

    # ── 深采分派 ──
    def _collect_deep(self, db_type: str, conn) -> dict:
        try:
            if db_type in ('mysql', 'tidb'):
                return self._collect_mysql(conn)
            if db_type == 'oceanbase':
                return self._collect_oceanbase(conn)
            if db_type in ('postgresql', 'pg', 'ivorysql', 'kingbase', 'halodb', 'vastbase'):
                return self._collect_postgres(conn)
            if db_type in ('uxdb', 'uxdb_jdbc'):
                return self._collect_uxdb(conn)
            if db_type in ('oracle', 'oracle_jdbc'):
                return self._collect_oracle(conn)
            if db_type == 'dm':
                return self._collect_dm(conn)
            if db_type in ('sqlserver', 'mssql', 'sqlserver_jdbc'):
                return self._collect_sqlserver(conn)
            if db_type == 'mongodb':
                return self._collect_mongodb(conn)
        except Exception as e:
            return {'deep_error': str(e)[:200]}
        return {}

    def _collect_mysql(self, conn) -> dict:
        m = {}
        cur = conn.cursor()
        cur.execute("SHOW GLOBAL STATUS")
        status = {str(r[0]).lower(): _to_num(r[1]) for r in cur.fetchall()}
        for k in ('queries', 'threads_connected', 'threads_running', 'slow_queries',
                  'aborted_connects', 'bytes_received', 'bytes_sent', 'connections',
                  'threads_created'):
            if k in status:
                m[k] = status[k]
        # 主从延迟
        try:
            cur.execute("SHOW SLAVE STATUS")
            rows = cur.fetchall()
            if rows and cur.description:
                cols = [str(c[0]).lower() for c in cur.description]
                sd = dict(zip(cols, rows[0]))
                sbm = sd.get('seconds_behind_master')
                if sbm is not None and str(sbm).upper() != 'NONE':
                    m['seconds_behind_master'] = _to_num(sbm)
        except Exception:
            pass
        return m

    def _collect_oceanbase(self, conn) -> dict:
        """OceanBase MySQL 租户深采：兼容 MySQL 计数器 + 补充 OB 原生 GV$OB_* 监控指标。

        设计文档 §3.2 / PRD OB-P0-07 要求返回基于 GV$OB_* 的监控指标，键至少包含：
            qps / tps / active_sessions / memstore_water_level /
            latency_ms / sysstat / processlist
        同时保留 SHOW GLOBAL STATUS 兼容逻辑（queries/threads_connected/...），
        供向后兼容。每条 GV$OB 查询独立 try/except，视图不存在/列缺失/权限不足
        均落入安全默认值（0 / 0.0 / {} / []），绝不让单条视图查询失败导致整个函数抛异常。
        """
        m = {}
        cur = conn.cursor()

        # ── 1) SHOW GLOBAL STATUS 兼容逻辑（向后兼容）──
        status = {}
        try:
            cur.execute("SHOW GLOBAL STATUS")
            status = {str(r[0]).lower(): _to_num(r[1]) for r in cur.fetchall()}
            for k in ('queries', 'threads_connected', 'threads_running', 'slow_queries',
                      'aborted_connects', 'bytes_received', 'bytes_sent', 'connections',
                      'threads_created'):
                if k in status:
                    m[k] = status[k]
        except Exception:
            pass

        # ── 2) active_sessions：GV$OB_PROCESSLIST COUNT(*)（映射为 active_sessions）──
        # 同时覆盖 threads_connected（更贴近 OB 真实会话模型，向后兼容）。
        try:
            cur.execute("SELECT COUNT(*) FROM GV$OB_PROCESSLIST")
            row = cur.fetchone()
            if row and row[0] is not None:
                cnt = int(_to_num(row[0]))
                m['active_sessions'] = cnt
                m['threads_connected'] = cnt
        except Exception:
            m.setdefault('active_sessions', 0)

        # ── 3) processlist：完整会话快照（行列表，tuple 直存，不依赖具体列名）──
        try:
            cur.execute("SELECT * FROM GV$OB_PROCESSLIST")
            m['processlist'] = list(cur.fetchall())
        except Exception:
            m['processlist'] = []

        # ── 4) sysstat：GV$SYSSTAT / GV$OB_SYSSTAT 按 CLASS 归并的 dict ──
        try:
            # 优先 GV$SYSSTAT，缺失再退 GV$OB_SYSSTAT；两视图列名跨版本略有差异，
            # 这里读 CLASS（分组键）+ 末列数值（VALUE），避免写死 VALUE 列名。
            try:
                cur.execute("SELECT CLASS, STAT_NAME, VALUE FROM GV$SYSSTAT")
            except Exception:
                cur.execute("SELECT CLASS, STAT_NAME, VALUE FROM GV$OB_SYSSTAT")
            sysstat = {}
            for row in cur.fetchall():
                if not row or len(row) < 2:
                    continue
                class_name = str(row[0])
                value = _to_num(row[-1])
                sysstat[class_name] = sysstat.get(class_name, 0) + value
            m['sysstat'] = sysstat
        except Exception:
            m['sysstat'] = {}

        # ── 4b) OB 原生 QPS/TPS/缓存命中计数器（stat_id 跨版本稳定，口径同 OCP）──
        # OB 的 SHOW GLOBAL STATUS 不提供 Queries/Com_commit/Com_rollback，
        # 速率图（rate_*）必须以 v$sysstat 计数器为源：
        #   ob_qps = 40000 select + 40002 insert + 40004 replace + 40006 update
        #            + 40008 delete；ob_tps = 30005 transaction_count；
        #   buffer_cache_hit_pct = 50008 hit / (50008 + 50009 miss)
        try:
            _OB_STAT_IDS = '40000,40002,40004,40006,40008,30005,50008,50009'
            try:
                cur.execute(
                    "SELECT stat_id, SUM(value) FROM gv$sysstat "
                    "WHERE stat_id IN (%s) AND class < 1000 GROUP BY stat_id" % _OB_STAT_IDS)
            except Exception:
                cur.execute(
                    "SELECT stat_id, SUM(value) FROM v$sysstat "
                    "WHERE stat_id IN (%s) AND class < 1000 GROUP BY stat_id" % _OB_STAT_IDS)
            st = {}
            for row in cur.fetchall():
                if row and len(row) >= 2:
                    try:
                        st[int(_to_num(row[0]))] = _to_num(row[1])
                    except (TypeError, ValueError):
                        continue
            if st:
                m['ob_qps'] = sum(st.get(i, 0) for i in (40000, 40002, 40004, 40006, 40008))
                m['ob_tps'] = st.get(30005, 0)
                c_hit, c_miss = st.get(50008), st.get(50009)
                if c_hit is not None and c_miss is not None and (c_hit + c_miss) > 0:
                    m['buffer_cache_hit_pct'] = round(c_hit / (c_hit + c_miss) * 100.0, 1)
                # SHOW GLOBAL STATUS 缺 queries 时用 OB 原生 SQL 计数兜底，
                # 保证 rate_queries（吞吐图主序列）在 OB 上可用
                if not m.get('queries'):
                    m['queries'] = m['ob_qps']
        except Exception:
            pass

        # ── 5) memstore_water_level：GV$OB_MEMSTORE 水位百分比 ──
        try:
            cur.execute(
                "SELECT MEMSTORE_USED_PERCENT, ACTIVE_MEMSTORE_USED, MEMSTORE_LIMIT "
                "FROM GV$OB_MEMSTORE"
            )
            rows = cur.fetchall()
            memstore = 0.0
            if rows and rows[0] is not None:
                first = rows[0]
                pct = _to_num(first[0]) if len(first) > 0 else 0
                if pct > 0:
                    memstore = float(pct)
                else:
                    active = _to_num(first[1]) if len(first) > 1 else 0
                    limit = _to_num(first[2]) if len(first) > 2 else 0
                    if limit > 0:
                        memstore = round(active / limit * 100.0, 2)
            m['memstore_water_level'] = memstore
        except Exception:
            m['memstore_water_level'] = 0.0

        # ── 6) latency_ms：GV$OB_SERVER_STAT 的 RPC/SQL 平均延迟（ms）──
        try:
            cur.execute(
                "SELECT SQL_RT, RPC_RT, AVG_RPC_TIME, SQL_PROCESS_RT "
                "FROM GV$OB_SERVER_STAT"
            )
            rows = cur.fetchall()
            latency = 0.0
            if rows and rows[0] is not None:
                first = rows[0]
                for idx in range(min(len(first), 4)):
                    v = _to_num(first[idx])
                    if v > 0:
                        latency = float(v)
                        break
            m['latency_ms'] = latency
        except Exception:
            m['latency_ms'] = 0.0

        # ── 7) qps / tps：单点快照无时间差，缺速率上下文默认 0.0；
        #        优先从 GV$SYSSTAT 查询/事务类累计计数器取值（生产环境真实速率
        #        由 MetricsCollector._compute_rates 基于这些计数器差分得到）。
        qps = 0.0
        tps = 0.0
        try:
            ss = m.get('sysstat') or {}
            q_terms = ('query', 'select', 'rpc total', 'sql select', 'execute', 'rpc')
            t_terms = ('commit', 'rollback', 'transaction', 'user commit')
            qps = sum(float(v) for k, v in ss.items()
                      if any(t in str(k).lower() for t in q_terms))
            tps = sum(float(v) for k, v in ss.items()
                      if any(t in str(k).lower() for t in t_terms))
        except Exception:
            pass
        # 真正的每秒速率由 _compute_rates 计算（基于 queries / Com_commit+Com_rollback
        # 计数器差分），此处单点快照无 Δt，故无速率上下文时保持 0.0。
        m['qps'] = float(qps)
        m['tps'] = float(tps)

        return m

    def _collect_postgres(self, conn) -> dict:
        m = {}
        cur = conn.cursor()
        cur.execute(
            "SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, "
            "deadlocks, conflicts FROM pg_stat_database WHERE datname = current_database()"
        )
        row = cur.fetchone()
        if row:
            keys = ('numbackends', 'xact_commit', 'xact_rollback', 'blks_read',
                    'blks_hit', 'deadlocks', 'conflicts')
            for k, v in zip(keys, row):
                m[k] = _to_num(v)
        # 复制延迟
        try:
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))::float"
            )
            r = cur.fetchone()
            if r and r[0] is not None:
                m['replay_lag_sec'] = float(r[0])
        except Exception:
            pass
        return m

    def _collect_uxdb(self, conn) -> dict:
        """UXDB 深采：UXDB 2.x 不暴露 PostgreSQL 系统视图，改用 ux_catalog.ux_stat_database
        （列名与 PostgreSQL 统计视图一致）。每条查询独立 try/except，
        单条失败不影响整体，尽力返回已采集的指标。
        """
        m = {}
        try:
            cur = conn.cursor()
            try:
                # UXDB 2.x 无 PostgreSQL 统计视图，使用 ux_catalog.ux_stat_database；
                # 不确定 current_database() 是否可用，故去掉 WHERE 过滤，直接 SELECT 全表取第一行。
                cur.execute(
                    "SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, "
                    "deadlocks, conflicts FROM ux_catalog.ux_stat_database"
                )
                row = cur.fetchone()
                if row:
                    keys = ('numbackends', 'xact_commit', 'xact_rollback', 'blks_read',
                            'blks_hit', 'deadlocks', 'conflicts')
                    for k, v in zip(keys, row):
                        m[k] = _to_num(v)
            except Exception:
                pass
            # 复制延迟（PG 兼容；UXDB 无备库时查询可能为空，忽略即可）
            try:
                cur.execute(
                    "SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))::float"
                )
                r = cur.fetchone()
                if r and r[0] is not None:
                    m['replay_lag_sec'] = float(r[0])
            except Exception:
                pass
        except Exception:
            pass
        return m

    def _collect_sqlserver(self, conn) -> dict:
        """SQL Server 深采：连接数、批处理数、锁等待等。"""
        m = {}
        cur = conn.cursor()
        # 连接数（sys.dm_exec_sessions 按状态统计）
        try:
            cur.execute("""
                SELECT status, COUNT(*) FROM sys.dm_exec_sessions
                WHERE session_id > 50 GROUP BY status
            """)
            for row in cur.fetchall():
                st = (str(row[0]) or '').lower()
                if 'running' in st:
                    m['sessions_running'] = int(row[1])
                elif 'sleeping' in st or 'background' in st:
                    m['sessions_idle'] = int(row[1])
                m['total_sessions'] = m.get('total_sessions', 0) + int(row[1])
        except Exception:
            pass
        # 吞吐计数器（sys.dm_os_performance_counters）
        #   Batch Requests/sec → mssql_batch_requests（QPS 近似，全局单值）
        #   Transactions/sec   → mssql_transactions（TPS 近似，按库多行，需求和为实例级）
        # 计数器名随服务器语言本地化（中文实例为「批请求/秒」「事务数/秒」），英中名都匹配；
        # Transactions/sec 属 'Transactions' 对象而非 'SQL Statistics'，故去掉
        # object_name 过滤、直接按 counter_name IN 取，否则 TPS 计数器会被漏采。
        try:
            cur.execute("""
                SELECT counter_name, cntr_value
                FROM sys.dm_os_performance_counters
                WHERE counter_name IN ('Batch Requests/sec', N'批请求/秒',
                                       'Transactions/sec', N'事务数/秒',
                                       'SQL Compilations/sec', 'SQL Re-Compilations/sec')
            """)
            for row in cur.fetchall():
                cname = (str(row[0]).strip().lower()
                         .replace('/sec', '').replace('/秒', '').replace(' ', '_'))
                val = _to_num(row[1]) or 0
                if cname in ('batch_requests', '批请求'):
                    m['mssql_batch_requests'] = val
                elif cname in ('transactions', '事务数'):
                    m['mssql_transactions'] = m.get('mssql_transactions', 0) + val
                else:
                    m['mssql_' + cname] = val
        except Exception:
            pass
        # 数据库级 I/O
        try:
            cur.execute("""
                SELECT SUM(num_of_reads), SUM(num_of_writes), SUM(num_of_bytes_read),
                       SUM(num_of_bytes_written)
                FROM sys.dm_io_virtual_file_stats(NULL, NULL)
            """)
            row = cur.fetchone()
            if row:
                m['io_read_count'] = _to_num(row[0]) or 0
                m['io_write_count'] = _to_num(row[1]) or 0
                m['io_read_bytes'] = _to_num(row[2]) or 0
                m['io_write_bytes'] = _to_num(row[3]) or 0
        except Exception:
            pass
        return m

    def _collect_mongodb(self, conn) -> dict:
        """MongoDB 深采：从 admin.command('serverStatus') 提取关键计数器。

        conn 为 pymongo.MongoClient（_connect 对 mongodb 返回 MongoClient）。
        所有指标键以 'mongodb_' 前缀统一命名，便于速率计算与前端展示。
        单点采集无时间差，原始计数器直接落盘，速率由 _compute_rates 差分。
        """
        m = {}
        try:
            status = conn.admin.command('serverStatus')
        except Exception:
            return m

        # 连接
        conns = status.get('connections', {}) or {}
        m['mongodb_connections_current'] = _to_num(conns.get('current', 0))
        m['mongodb_connections_available'] = _to_num(conns.get('available', 0))

        # 操作计数器（opcounters）
        ops = status.get('opcounters', {}) or {}
        m['mongodb_opcounters_insert'] = _to_num(ops.get('insert', 0))
        m['mongodb_opcounters_query'] = _to_num(ops.get('query', 0))
        m['mongodb_opcounters_update'] = _to_num(ops.get('update', 0))
        m['mongodb_opcounters_delete'] = _to_num(ops.get('delete', 0))
        m['mongodb_opcounters_getmore'] = _to_num(ops.get('getmore', 0))
        m['mongodb_opcounters_command'] = _to_num(ops.get('command', 0))
        m['mongodb_opcounters_total'] = (
            m['mongodb_opcounters_insert'] + m['mongodb_opcounters_query']
            + m['mongodb_opcounters_update'] + m['mongodb_opcounters_delete']
            + m['mongodb_opcounters_getmore'] + m['mongodb_opcounters_command'])

        # 内存
        mem = status.get('mem', {}) or {}
        m['mongodb_mem_resident'] = _to_num(mem.get('resident', 0))
        m['mongodb_mem_virtual'] = _to_num(mem.get('virtual', 0))

        # 网络
        net = status.get('network', {}) or {}
        m['mongodb_net_in'] = _to_num(net.get('bytesIn', 0))
        m['mongodb_net_out'] = _to_num(net.get('bytesOut', 0))
        m['mongodb_network_requests'] = _to_num(net.get('numRequests', 0))

        # 全局锁（totalTime 单调递增，可算速率）
        gl = status.get('globalLock', {}) or {}
        m['mongodb_global_lock_total'] = _to_num(gl.get('totalTime', 0))

        # WiredTiger 缓存（当前使用量，用于水位参考）
        wt_cache = (status.get('wiredTiger', {}) or {}).get('cache', {}) or {}
        m['mongodb_wt_cache_used'] = _to_num(
            wt_cache.get('bytes currently in the cache', 0))
        m['mongodb_wt_cache_dirty'] = _to_num(
            wt_cache.get('tracked dirty bytes in the cache', 0))

        # 辅助：uptime（非计数器，供监控面板展示）
        m['mongodb_uptime'] = round(_to_num(status.get('uptime', 0)), 1)
        return m

    def _collect_oracle(self, conn) -> dict:
        m = {}
        cur = conn.cursor()
        # v$sysstat 的「数值列」与「统计项名」跨版本高度兼容，但为稳妥仍：
        #  - 数值列动态探测（_fetch_name_value，不写死 VALUE）；
        #  - 统计项名按「精确名 → 子串」匹配（_map_stats），不写死某一版本。
        try:
            stats = _fetch_name_value(cur, "SELECT * FROM v$sysstat")
            mapped = _map_stats(stats, _ORACLE_STAT_MAP)
            for k, v in mapped.items():
                m['ora_' + k] = v
        except Exception:
            pass
        # 用户会话数：type='USER' 在多数版本通用；兜底用 username IS NOT NULL
        try:
            cur.execute("SELECT count(*) FROM v$session WHERE type = 'USER'")
            m['user_sessions'] = _to_num(cur.fetchone()[0])
        except Exception:
            try:
                cur.execute("SELECT count(*) FROM v$session WHERE username IS NOT NULL")
                m['user_sessions'] = _to_num(cur.fetchone()[0])
            except Exception:
                pass
        return m

    def _collect_dm(self, conn) -> dict:
        m = {}
        cur = conn.cursor()
        # 达梦 v$sysstat 的「数值列」在不同版本叫 stat_val/stat_value/value，
        # 动态探测，绝不写死；统计名按子串自适应。
        try:
            stats = _fetch_name_value(cur, "SELECT * FROM v$sysstat")
            mapped = _map_stats(stats, _DM_STAT_MAP)
            for k, v in mapped.items():
                m['dm_' + k] = v
        except Exception:
            pass
        # 活跃会话：STATE='ACTIVE' 通用；兜底为「非 INACTIVE」或全量
        try:
            cur.execute("SELECT count(*) FROM v$session WHERE state = 'ACTIVE'")
            m['active_sessions'] = _to_num(cur.fetchone()[0])
        except Exception:
            try:
                cur.execute("SELECT count(*) FROM v$session WHERE state <> 'INACTIVE'")
                m['active_sessions'] = _to_num(cur.fetchone()[0])
            except Exception:
                try:
                    cur.execute("SELECT count(*) FROM v$session")
                    m['active_sessions'] = _to_num(cur.fetchone()[0])
                except Exception:
                    pass
        return m

    def _attach_host(self, snapshot: dict, inst_id: str, inst: dict):
        """挂载宿主机资源指标（与数据库可用性无关，连接失败时也采集）。

        实例感知：若实例配置了 SSH，则 SSH 到目标数据库服务器采集其真实
        宿主资源（含可选 eBPF 内核级指标）；否则回退中心机本机 psutil。
        二者均由 pro.remote_host_collector 统一产出，host_collector_source
        标记本次实际数据源（ebpf / psutil / unavailable）。
        """
        try:
            host = self._collect_host(inst)
            if host:
                snapshot.update(host)
                self._compute_host_rates(inst_id, snapshot, host)
                # Windows 服务告警（服务停止 / 内存超阈值）。
                # PowerShell 对单元素数组可能序列化成对象，这里统一归一为 list。
                services = host.get('host_services')
                if isinstance(services, dict):
                    services = [services]
                    snapshot['host_services'] = services
                if isinstance(services, list):
                    alerts = evaluate_host_service_alerts(services)
                    snapshot['host_service_alerts'] = alerts
                    snapshot['host_service_alert_count'] = len(alerts)
        except Exception:
            pass

    def _collect_host(self, inst: dict) -> dict:
        """采集目标机的宿主资源。

        - 配置了 SSH → 走远端采集；若远端失败/无数据，返回 {}（该目标显示
          “宿主资源不可用”），**绝不回退到中心机本机数据**（否则会把中心机指标
          误标成目标机的，造成“错机器”误导）。
        - 未配置 SSH → **先判定数据库实例是否真与 DBCheck 同机**：
            同机 → 本机 psutil 采集（来源标注 'local'，口径可见）；
            非同机 → 返回“未配置 SSH，无法采集该实例宿主资源”，
            不再拿中心机本机数据顶替（异地部署时旧逻辑会静默“错机器”误导）。

        并发保护（修复“目标机 sshd 被监控的连接打满 / Connection closed /
        Error reading SSH protocol banner / EOFError”）：
          - 每目标主机一把锁：同一台机器同一时刻只会有 1 个 SSH 连接（哪怕被注册成
            多个实例，也不会在一轮 tick 内并发去连它）；
          - 全局 SSH 信号量：限制 DBCheck 进程内同时存在的 SSH 连接总数，避免监控 +
            手动测试 + 巡检任务叠加时瞬时压垮 sshd 的 MaxStartups。
        """
        ssh = self._resolve_ssh(inst)
        if ssh:
            host_lock = _get_ssh_host_lock(ssh['host'])
            if not _SSH_GLOBAL_SEM.acquire(timeout=5):
                # 全局 SSH 槽位紧张，本轮跳过，避免再给 sshd 加压（下轮重试）
                print(f"[metrics] SSH 采集跳过 {ssh['host']}: 全局 SSH 槽位紧张", flush=True)
                return _host_unavailable('全局 SSH 槽位紧张，本轮跳过（下轮重试）')
            try:
                with host_lock:
                    try:
                        remote = self._collect_host_via_ssh(ssh, inst)
                    except Exception as e:
                        remote = _host_unavailable('采集异常: %s' % e)
                        print(f"[metrics] SSH 采集异常 {ssh['host']}: {e}", flush=True)
                # remote 可能是：真实数据 / {'host_collector_source':'unavailable','host_error':...}
                if remote and remote.get('host_collector_source') != 'unavailable':
                    return remote
                # 远端失败/无数据：同机实例（ssh_host 指向中心机自身）误配 SSH 时，
                # 合法回退本机采集（本就同机，不会“错机器”）；其余情况把失败原因
                # 直接带进 snapshot，前端显示“宿主资源不可用：原因”，不再静默成
                # 无意义的“暂无宿主数据”。
                if _is_self_host(ssh['host']):
                    print(f"[metrics] SSH 远端无数据，{ssh['host']} 判定为本机 → 回退本机采集", flush=True)
                    return self._collect_host_local()
                reason = (remote or {}).get('host_error') or 'SSH 远端无数据（连接失败或目标无产出）'
                print(f"[metrics] {ssh['host']} 宿主采集不可用: {reason}", flush=True)
                return _host_unavailable(reason)
            finally:
                _SSH_GLOBAL_SEM.release()

        # ── 未配置 SSH：必须先判定实例地址是否就是中心机自身 ──
        # 旧逻辑无条件 self._collect_host_local()，异地部署时会把 DBCheck 所在机器的
        # CPU/内存/磁盘静默当成数据库服务器的指标展示（与“C 盘警告误取自本地主机”同类坑）。
        db_host = str(inst.get('host') or '').strip()
        if _is_self_host(db_host):
            return self._collect_host_local()
        reason = ('未配置 SSH：实例主机 %s 非本机，无法采集其宿主资源；'
                  '请在数据源配置该机的 SSH 后重试' % (db_host or '(地址缺失)'))
        print(f"[metrics] 实例 {inst.get('id') or inst.get('name') or ''} "
              f"宿主采集不可用: {reason}", flush=True)
        return _host_unavailable(reason)

    def _resolve_ssh(self, inst: dict):
        """解析实例的 SSH 配置；未启用或缺失主机则返回 None。"""
        if not inst.get('ssh_enabled'):
            return None
        # UI 上「SSH 主机地址」输入框只有占位文案「与数据库主机相同」，
        # 用户留空时保存的是空字符串；旧逻辑在此直接返回 None，导致明明
        # 打开了「启用 SSH 系统信息采集」却报「未配置 SSH」，首页实时监控
        # 与监控大屏的宿主资源同时失效。按 UI 明示语义回退到数据库主机，
        # 对已保存的存量数据立即生效，无需用户重新保存数据源。
        host = ((inst.get('ssh_host') or '').strip()
                or str(inst.get('host') or '').strip())
        if not host:
            return None
        return {
            'host': host,
            'port': int(inst.get('ssh_port') or 22),
            'user': inst.get('ssh_user') or '',
            'password': inst.get('ssh_password') or '',
            'key_file': inst.get('ssh_key_file') or '',
            'key_password': inst.get('ssh_key_password') or '',
            # 宿主采集 OS 分流（auto/linux/windows）与 Windows 服务名单
            'host_os': str(inst.get('host_os') or 'auto').strip().lower(),
            'monitor_services': str(inst.get('monitor_services') or ''),
        }

    def _remote_script_src(self) -> str:
        """读取远端 Python 采集脚本源码（缓存），用于 SSH 内联执行。"""
        src = getattr(self, '_rhs_src', None)
        if src is None:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'remote_host_collector.py')
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    src = f.read()
            except Exception:
                src = ''
            self._rhs_src = src
        return src

    def _remote_shell_src(self) -> str:
        """读取纯 Shell(/proc) 远端采集脚本源码（缓存），用于 SSH 内联执行（目标机零依赖）。"""
        src = getattr(self, '_rhs_sh_src', None)
        if src is None:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'remote_host_shell.sh')
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    src = f.read()
            except Exception:
                src = ''
            self._rhs_sh_src = src
        return src

    def _remote_windows_src(self) -> str:
        """读取 Windows PowerShell 采集脚本源码（缓存），经 SSH stdin 喂给 powershell.exe。"""
        src = getattr(self, '_rh_win_src', None)
        if src is None:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'remote_host_windows.ps1')
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    src = f.read()
            except Exception:
                src = ''
            self._rh_win_src = src
        return src

    @staticmethod
    def _windows_script_with_config(ps1: str, monitor_services: str) -> str:
        """把实例的服务名单注入 PowerShell 脚本首行。

        用 base64(UTF-8 JSON) 传参：服务名可能含引号、通配符、空格或中文，
        直接拼命令行会在 cmd.exe 与 PowerShell 两层解析里被转义破坏，且
        Windows PowerShell 5.1 从管道读脚本时按 ANSI 解码，非 ASCII 源码会乱码。
        base64 纯 ASCII，脚本内再自行解码，两种编码环境都安全。
        """
        raw = (monitor_services or '').strip()
        patterns = []
        if raw.startswith('['):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    patterns = [str(x).strip() for x in parsed if str(x).strip()]
            except (ValueError, TypeError):
                patterns = []
        if not patterns and raw:
            patterns = [p.strip() for p in re.split(r'[,;\n]+', raw) if p.strip()]
        cfg = {
            'service_patterns': patterns,
            'sample_ms': 900,
            'explicit': bool(patterns),   # 用户显式点名时，Manual 服务停止也告警
        }
        b64 = base64.b64encode(
            json.dumps(cfg, ensure_ascii=True).encode('utf-8')).decode('ascii')
        return "$RxConfigB64 = '%s'\n%s" % (b64, ps1)

    @staticmethod
    def _ssh_run_script(client, bootstrap: str, script_src: str,
                        read_timeout: int = 12):
        """经 stdin 把整份脚本喂给远端解释器，返回 (stdout, stderr) 文本。

        两处历史硬约束（勿改）：
          - 必须 shutdown_write()，远端解释器才会读到 EOF 并结束；
          - stdout / stderr 是独立通道，都要读，否则远端报错完全不可见。
        """
        stdin, stdout, stderr = client.exec_command(bootstrap, timeout=20)
        try:
            stdin.write(script_src)
        except Exception:
            pass
        try:
            stdin.channel.shutdown_write()
        except Exception:
            pass
        raw, err = b'', b''
        try:
            stdout.channel.settimeout(read_timeout)
        except Exception:
            pass
        try:
            while True:
                chunk = stdout.channel.recv(4096)
                if not chunk:
                    break
                raw += chunk
        except Exception:
            pass
        try:
            stderr.channel.settimeout(5)
            while True:
                e = stderr.channel.recv(4096)
                if not e:
                    break
                err += e
        except Exception:
            pass
        # Windows PowerShell 的 UTF8 输出可能带 BOM，去掉才能 json.loads
        out = raw.decode('utf-8', errors='ignore').strip().lstrip('\ufeff').strip()
        return out, err.decode('utf-8', errors='ignore').strip()

    def _collect_host_via_ssh(self, ssh: dict, inst: dict = None, max_retries: int = 2) -> dict:
        """SSH 到目标机，内联执行远端采集脚本，解析 JSON 返回。

        按实例的 host_os 分流采集通道：
          - linux/auto → 纯 Shell 读 /proc，目标机零依赖；仅 host_ebpf=True
            时才需要 python3 + bcc；
          - windows → powershell.exe 跑 CIM/Get-Process 脚本，额外产出
            host_services（服务级 CPU / 工作集内存）；
          - auto → Linux 通道无有效输出时，在同一 SSH 连接内回退 Windows。
        脚本整份经 stdin 喂给远端解释器，避免目标机落地文件。

        健壮性（修复「后端崩溃后目标机 sshd 被半开连接撑满 / Connection closed /
        Error reading SSH protocol banner / EOFError」）：
          - client.connect() 放进 try/finally，**任何握手失败都保证 client.close()**；
          - 开启 SSH/TCP keepalive，让目标 sshd 快速探活回收死连接；
          - 对 stdout 做有界读取（单次 recv 超时），远端脚本卡死也不永久阻塞；
          - 远端命令默认 --no-ebpf --max-time，绝不默认往目标内核注入 eBPF 程序；
          - 对「握手阶段被 sshd 丢弃」（banner EOF / EOFError / 超时）做有限退避重试，
            这是 sshd 的 MaxStartups 瞬时拥塞所致，退避后通常自愈，避免误判为「无数据」。
        """
        import paramiko
        kwargs = {
            'hostname': ssh['host'],
            'port': int(ssh['port']),
            'username': ssh['user'],
            'timeout': 15,
            'banner_timeout': 10,
            'auth_timeout': 15,
            'look_for_keys': False,
            'allow_agent': False,
        }
        if ssh['key_file'] and os.path.isfile(ssh['key_file']):
            try:
                if ssh['key_password']:
                    key_password = ssh['key_password']
                else:
                    key_password = None
                pkey = paramiko.RSAKey.from_private_key_file(ssh['key_file'], key_password)
                kwargs['pkey'] = pkey
            except Exception:
                try:
                    pkey = paramiko.Ed25519Key.from_private_key_file(ssh['key_file'])
                    kwargs['pkey'] = pkey
                except Exception:
                    pass
        if 'pkey' not in kwargs and ssh['password']:
            kwargs['password'] = ssh['password']
        # 按目标机 OS 选择远端采集通道：
        #   Linux/POSIX → 纯 Shell(/proc) 零依赖；仅 host_ebpf=True 时改走 Python+eBPF；
        #   Windows     → PowerShell(CIM/Get-Process)，含 Windows 服务级 CPU / 内存；
        #   auto        → Linux 优先，失败在同一 SSH 连接内回退 Windows（存量实例零改动）。
        enable_ebpf = bool(inst.get('host_ebpf')) if inst else False
        py_src = self._remote_script_src()
        sh_src = self._remote_shell_src()
        win_src = self._remote_windows_src()
        ebpf_flag = '1' if enable_ebpf else '0'
        os_kind = str(ssh.get('host_os') or 'auto').strip().lower()
        if os_kind not in ('auto', 'linux', 'unix', 'posix', 'windows', 'win'):
            os_kind = 'auto'

        # bootstrap（纯 POSIX、无 heredoc）：只做解释器探测与转发。
        # 整份脚本经 stdin 喂给 "$SHBIN" -s / python -，避免不同登录 shell 下
        # 内嵌 heredoc 行为脆弱；脚本正文自带 %，故用字符串相加而非 % 拼接。
        bootstrap_linux = (
            "SHBIN=$(command -v bash 2>/dev/null || command -v sh 2>/dev/null || true); "
            "PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true); "
            "if [ -n \"$PYBIN\" ] && [ \"" + ebpf_flag + "\" = \"1\" ]; then "
            "  \"$PYBIN\" - --window 0.5 --max-time 8; "
            "elif [ -n \"$SHBIN\" ]; then "
            "  \"$SHBIN\" -s; "
            "else "
            "  echo 'RaccoonX: no bash/sh/python interpreter found on target'; exit 3; "
            "fi"
        )
        linux_script = py_src if (ebpf_flag == '1') else sh_src
        # Windows OpenSSH 默认 shell 可能是 cmd.exe 也可能是 PowerShell；显式拉起
        # powershell.exe 子进程，两种默认 shell 下都成立。-Command - 表示从 stdin
        # 读取整份脚本，正好复用现有 SSH stdin 通道，目标机不落地任何文件。
        win_bootstrap = ('powershell.exe -NoProfile -NonInteractive '
                         '-ExecutionPolicy Bypass -Command -')
        win_script = self._windows_script_with_config(
            win_src, ssh.get('monitor_services', ''))
        if os_kind in ('windows', 'win'):
            plan = [('windows', win_bootstrap, win_script, 30)]
        elif os_kind in ('linux', 'unix', 'posix'):
            plan = [('linux', bootstrap_linux, linux_script, 12)]
        else:
            plan = [('linux', bootstrap_linux, linux_script, 12),
                    ('windows', win_bootstrap, win_script, 30)]
        if not win_src and any(p[0] == 'windows' for p in plan):
            print('[metrics] remote_host_windows.ps1 缺失，Windows 通道不可用', flush=True)
            plan = [p for p in plan if p[0] != 'windows']

        last_err = None
        for attempt in range(max_retries):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(**kwargs)   # 连接阶段也在 try 内，失败必关
                tp = client.get_transport()
                if tp:
                    try:
                        tp.set_keepalive(15)
                    except Exception:
                        pass
                # 按 plan 依次尝试采集通道。auto 模式下 Windows 目标机上 Linux
                # bootstrap 会无 stdout（cmd 不认识 command -v / sh -s），随即在同一
                # SSH 连接内回退 PowerShell，不会多建连接、也不会压垮 sshd。
                data = None
                last_detail = '远端脚本未产出任何 JSON'
                for channel, boot, script, rtimeout in plan:
                    raw, err_txt = self._ssh_run_script(client, boot, script, rtimeout)
                    if not raw:
                        last_detail = ('远端脚本执行失败: ' + err_txt[:200]) if err_txt \
                            else '远端脚本未产出任何 JSON（解释器缺失或脚本未执行）'
                        if len(plan) > 1:
                            print(f"[metrics] SSH {ssh['host']} {channel} 通道无输出，尝试下一条", flush=True)
                        continue
                    try:
                        cand = json.loads(raw)
                    except (ValueError, TypeError):
                        last_detail = '远端输出非合法 JSON: %s' % raw[:80]
                        if err_txt:
                            last_detail += ' | stderr: ' + err_txt[:80]
                        if len(plan) > 1:
                            continue
                        return _host_unavailable(last_detail)
                    if (isinstance(cand, dict)
                            and cand.get('host_collector_source') != 'unavailable'):
                        data = cand
                        break
                    reason = cand.get('host_error') if isinstance(cand, dict) else ''
                    last_detail = '远端采集器返回 unavailable: %s' % (reason or '无数据')
                    if len(plan) > 1:
                        continue
                if data is None:
                    print(f"[metrics] SSH 远端 {ssh['host']} 所有通道均无有效数据（{last_detail}）",
                          flush=True)
                    return _host_unavailable(last_detail)
                # PowerShell 对单元素数组可能序列化成对象，统一归一为 list，
                # 避免前端与告警评估拿到 dict 时遍历出错。
                svcs = data.get('host_services')
                if isinstance(svcs, dict):
                    data['host_services'] = [svcs]
                elif svcs is None:
                    data['host_services'] = []
                return data
            except Exception as e:
                last_err = e
                msg = str(e)
                # sshd MaxStartups 打满 / 瞬时拥塞：握手阶段即被丢（banner EOF），
                # 属瞬时错误，退避重试一次通常可自愈；否则本轮放弃，交由下次 tick 再试。
                transient = ('Error reading SSH protocol banner' in msg
                             or 'EOFError' in msg or 'EOF' in msg
                             or 'timed out' in msg.lower())
                if not transient or attempt >= max_retries - 1:
                    kind = '瞬时重试耗尽（sshd 瞬时拥塞）' if transient else '非瞬时错误'
                    print(f"[metrics] SSH 采集失败 {ssh['host']}: {msg}（{kind}）", flush=True)
                    return _host_unavailable('SSH 采集失败: %s（%s）' % (msg, kind))
                time.sleep(2)
                continue
            finally:
                try:
                    client.close()
                except Exception:
                    pass
        return _host_unavailable('SSH 采集重试后仍未成功')

    def _collect_host_local(self) -> dict:
        """中心机本机采集（同机场景或无 SSH 且实例同机时降级）。默认仅 psutil，不自动加载 eBPF。

        结果的 host_collector_source 统一标注为 'local'，与 SSH 远端采集
        （'ebpf'/'psutil'/'shell'）区分开——本机口径与数据库服务器口径本就不同，
        必须让使用者一眼看出这组指标来自 DBCheck 所在机器。
        采集失败（'unavailable'）不覆盖，以免掩盖真实错误原因。
        """
        try:
            from modules.pro import remote_host_collector as rh
            m = rh.collect_host(use_ebpf=False, window=0.5)
        except Exception:
            # 极端：连 remote_host_collector 都 import 失败，用内置 psutil 兜底
            m = self._collect_host_psutil_fallback()
        if isinstance(m, dict) and m.get('host_collector_source') != 'unavailable':
            m['host_collector_source'] = 'local'
        return m

    def _collect_host_psutil_fallback(self) -> dict:
        """remote_host_collector 不可用时的内置 psutil 兜底。"""
        try:
            import psutil
        except Exception:
            return {'host_collector_source': 'unavailable'}
        m: dict = {}
        try:
            ct = psutil.cpu_times_percent(interval=None)
            m['host_cpu'] = round(100.0 - float(getattr(ct, 'idle', 100) or 100), 1)
            m['host_cpu_iowait'] = round(float(getattr(ct, 'iowait', 0) or 0), 1)
        except Exception:
            pass
        try:
            vm = psutil.virtual_memory()
            m['host_mem'] = round(float(vm.percent), 1)
        except Exception:
            pass
        m['host_collector_source'] = 'psutil'
        return m

    def _compute_host_rates(self, inst_id: str, snapshot: dict, host: dict):
        """对宿主机磁盘 IO 计数器做差分，得到吞吐（MB/s）与 await（ms/op）。"""
        prev = self._last_host.get(inst_id)
        now = time.time()
        if prev:
            prev_metrics, prev_ts = prev
            dt = now - prev_ts
            if dt > 0:
                rb = (host.get('host_disk_read_bytes', 0)
                      - prev_metrics.get('host_disk_read_bytes', 0))
                wb = (host.get('host_disk_write_bytes', 0)
                      - prev_metrics.get('host_disk_write_bytes', 0))
                rms = (host.get('host_disk_read_ms', 0)
                       - prev_metrics.get('host_disk_read_ms', 0))
                wms = (host.get('host_disk_write_ms', 0)
                       - prev_metrics.get('host_disk_write_ms', 0))
                rc = (host.get('host_disk_read_count', 0)
                      - prev_metrics.get('host_disk_read_count', 0))
                wc = (host.get('host_disk_write_count', 0)
                      - prev_metrics.get('host_disk_write_count', 0))
                snapshot['host_disk_read_mb_s'] = round(
                    max(0.0, rb / dt) / (1024 ** 2), 3)
                snapshot['host_disk_write_mb_s'] = round(
                    max(0.0, wb / dt) / (1024 ** 2), 3)
                total_ms = max(0.0, rms + wms)
                total_ops = max(1, rc + wc)
                snapshot['host_disk_await_ms'] = round(total_ms / total_ops, 2)
        self._last_host[inst_id] = (dict(host), now)

    # ── 断路器 ──

    # ── 速率计算 ──
    def _compute_rates(self, inst_id: str, db_type: str, snapshot: dict, metrics: dict):
        counters = COUNTER_KEYS.get(db_type, set())
        # DM8：对任意 dm_ 前缀的计数器也尝试算速率
        if db_type == 'dm':
            counters = {k for k in metrics if k.startswith('dm_')}
        if not counters:
            return
        prev = self._last.get(inst_id)
        now = time.time()
        if prev:
            prev_metrics, prev_ts = prev
            dt = now - prev_ts
            if dt > 0:
                for k in counters:
                    if k in metrics and k in prev_metrics:
                        try:
                            dv = float(metrics[k]) - float(prev_metrics[k])
                            snapshot['rate_' + k] = round(max(0.0, dv / dt), 2)
                        except (TypeError, ValueError):
                            pass
        self._last[inst_id] = (dict(metrics), now)

    # ── 断路器 ──
    def _in_cooldown(self, inst_id: str) -> bool:
        until = self._cooldown.get(inst_id)
        return bool(until and time.time() < until)

    def _on_fail(self, inst_id: str):
        self._fail[inst_id] = self._fail.get(inst_id, 0) + 1
        if self._fail[inst_id] >= CIRCUIT_FAIL_THRESHOLD:
            self._cooldown[inst_id] = time.time() + CIRCUIT_COOLDOWN

    def _on_success(self, inst_id: str):
        self._fail[inst_id] = 0

    # ── JVM 类型：隔离到干净子进程采集（规避主进程 startJVM 冻结 hub）──
    def _collect_jvm_subprocess(self, inst: dict) -> dict:
        """对 oracle_jdbc / uxdb_jdbc 等 JVM 类型，在干净子进程里执行连接 + 深采。

        主进程只负责 spawn、读取 stdout 最后一个 JSON 行并取回指标字典；
        JPype/JVM 完全在子进程内，绝不污染 gevent hub。
        返回指标字典（含 available/error），失败返回 None。
        """
        import subprocess as _sp
        from modules.core import paths

        db_type = str(inst.get('db_type') or '').lower()
        payload = {
            'task_id': 'metrics-%s-%s' % (db_type, datetime.now().strftime('%Y%m%d%H%M%S')),
            'db_type': db_type,
            'db_info': {
                'id': inst.get('id') or inst.get('instance_id'),
                'instance_id': inst.get('id') or inst.get('instance_id'),
                'name': inst.get('name') or '',
                'host': inst.get('host'),
                'port': int(inst.get('port') or 0),
                'user': inst.get('user'),
                'password': inst.get('password'),
                'database': inst.get('database') or '',
                'service_name': inst.get('service_name') or inst.get('database') or '',
                'sysdba': bool(inst.get('sysdba', False)),
                'jdbc_url': inst.get('jdbc_url') or None,
                # SSH 隧道（数据库经隧道连接）：子进程内由统一连接工厂建隧道，
                # 与主进程采集/大屏 JDBC 通道同口径（仅显式 ssh_tunnel 生效）。
                'ssh_tunnel': bool(inst.get('ssh_tunnel', False)),
                'ssh_host': inst.get('ssh_host') or '',
                'ssh_port': int(inst.get('ssh_port', 22) or 22),
                'ssh_user': inst.get('ssh_user') or '',
                'ssh_password': inst.get('ssh_password') or '',
                'ssh_key_file': inst.get('ssh_key_file') or '',
                'ssh_key_password': inst.get('ssh_key_password') or '',
            },
        }
        raw = json.dumps(payload, ensure_ascii=True)

        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, '--jdbc-metrics-cli']
        else:
            cmd = [sys.executable,
                   os.path.join(str(paths.PROJECT_ROOT), 'modules', 'jdbc_metrics_cli.py')]

        env = os.environ.copy()
        env['DBCheck_NO_GEVENT_PATCH'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'

        try:
            proc = _sp.run(
                cmd, input=raw.encode('utf-8'), capture_output=True,
                timeout=JVM_METRICS_SUBPROC_TIMEOUT, env=env, cwd=str(paths.PROJECT_ROOT),
            )
        except _sp.TimeoutExpired:
            return {'available': False, 'error': 'jvm metrics subprocess timeout'}
        except Exception as e:  # noqa: BLE001
            return {'available': False, 'error': 'jvm metrics spawn failed: %s' % str(e)[:200]}

        out = (proc.stdout or b'').decode('utf-8', errors='replace').strip()
        # 插件会向 stdout 打印 [Oracle JDBC] 调试信息；结构化结果在最后一行（以 { 开头）。
        for ln in reversed(out.splitlines()):
            ln = ln.strip()
            if ln.startswith('{'):
                try:
                    return json.loads(ln)
                except Exception:
                    break
        return None

    # ── 单实例采集 ──
    def collect_one(self, inst: dict, collect_host: bool = True) -> dict:
        """深采单个实例。

        ``collect_host=False``：由统一采集器按 30s 节流集中采集宿主资源
        （15s 轮里每轮都做 SSH 采集会让开销翻倍）。独立调用时保持 True，
        即老语义「无论 DB 可达与否都落一份宿主快照」。
        """
        inst_id = inst.get('id') or inst.get('instance_id')
        name = inst.get('name')
        db_type = str(inst.get('db_type') or '').lower()
        ts = datetime.now().isoformat()
        snapshot = {
            'instance_id': inst_id, 'name': name,
            'db_type': db_type, 'ts': ts,
        }
        if not inst_id:
            snapshot['error'] = 'missing instance id'
            return snapshot

        try:
            if self._in_cooldown(inst_id):
                snapshot['available'] = False
                snapshot['status'] = 'cooldown'
                return snapshot

            # 数据库经 SSH 隧道时，直连原始 host:port 的 TCP 探测没有意义
            # （远端库往往只在跳板机视角可达）→ 跳过探针，由统一工厂建连结果
            # 决定可用性；未开隧道的实例保留原探针行为。
            if uses_db_tunnel(inst):
                up, latency = True, None
                snapshot['probe_skipped'] = 'ssh_tunnel'
            else:
                up, latency = self._tcp_probe(inst.get('host'), inst.get('port'))
            snapshot['available'] = bool(up)
            snapshot['latency_ms'] = latency
            if not up:
                self._on_fail(inst_id)
                return snapshot

            managed = None
            try:
                if db_type in JVM_METRICS_DB_TYPES:
                    # JVM 类型：连接 + 深采必须隔离到干净子进程（绝不在主进程
                    # startJVM），否则 gevent monkey-patch 下冻结整个 hub。
                    metrics = self._collect_jvm_subprocess(inst)
                    if metrics:
                        snapshot.update(metrics)
                        self._compute_rates(inst_id, db_type, snapshot, metrics)
                    else:
                        snapshot['error'] = 'jvm metrics subprocess returned nothing'
                        snapshot['available'] = False
                else:
                    managed = self._connect(inst)
                    if managed is not None:
                        metrics = self._collect_deep(db_type, managed.conn)
                        snapshot.update(metrics)
                        self._compute_rates(inst_id, db_type, snapshot, metrics)
            except Exception as e:
                snapshot['available'] = False
                snapshot['error'] = str(e)[:200]
            finally:
                if managed is not None:
                    managed.close()
            self._on_success(inst_id)
            return snapshot
        finally:
            # 宿主资源与数据库探测彻底解耦：退避(cooldown) / TCP 探测失败 /
            # 深采异常三条早退路径此前都在 _attach_host 之前 return，导致
            # get_latest(iid) 永远没有宿主字段 → 大屏「宿主资源」整片空白。
            # 现在无论数据库可达与否都落一份宿主快照（含 unavailable 原因）；
            # 统一采集器路径下改由它按 30s 节流集中采集并注入。
            if collect_host:
                self._attach_host(snapshot, inst_id, inst)

    # ── 一轮采集（兼容入口，委托唯一采集器）──
    def tick(self) -> list:
        """兼容入口：委托 UnifiedCollector 执行一轮，不另起调度循环。

        历史版本这里由 APScheduler 每 30s 调一次，是三条采集循环之一；
        现在只保留方法签名，供脚本 / 单元测试 / 手动调用沿用。
        """
        from modules.monitor.unified_collector import get_unified_collector
        frames = get_unified_collector()._round() or []
        return [f.get('home') or {} for f in frames]


# ── 进程内单例 ──────────────────────────────────────────
_collector = None


def get_collector() -> MetricsCollector:
    return _collector


def set_collector(c: MetricsCollector):
    global _collector
    _collector = c


# ── 回归冒烟测试：确保 _collect_uxdb 使用 UXDB 真实系统视图 ──
# UXDB 2.x 不暴露 pg_catalog/pg_stat_*，必须使用 ux_catalog.ux_stat_database；
# 若误用 pg_stat_database，深采会静默失败导致实时图表为空。
if __name__ == '__main__':
    import sys

    class _FakeCursor:
        """记录执行过的 SQL，并模拟 ux_stat_database 返回一行。"""
        def __init__(self):
            self.last_sql = None

        def execute(self, sql):
            self.last_sql = sql

        def fetchone(self):
            # 模拟 UXDB ux_stat_database 返回当前连接库所在行
            return (10, 1000, 5, 200, 8000, 0, 0)

        def fetchall(self):
            return []

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def close(self):
            pass

    mc = MetricsCollector()
    cur = _FakeConn().cursor()
    # 捕获 _collect_uxdb 内部执行的 SQL
    executed = {}

    class _SpyCursor(_FakeCursor):
        def execute(self, sql):
            executed.setdefault('sql', sql)
            super().execute(sql)

    class _SpyConn(_FakeConn):
        def cursor(self):
            return _SpyCursor()

    result = mc._collect_uxdb(_SpyConn())
    sql = executed.get('sql', '')
    ok = ('ux_catalog.ux_stat_database' in sql) and ('pg_stat_database' not in sql)
    if ok:
        print('SMOKE_OK: _collect_uxdb 命中 ux_catalog.ux_stat_database')
        print('  返回指标:', result)
        sys.exit(0)
    else:
        print('SMOKE_FAIL: _collect_uxdb 未正确使用 UXDB 系统视图, SQL=%s' % sql)
        sys.exit(1)
