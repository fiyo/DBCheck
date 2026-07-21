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
  - 连接信息取自 instance_manager 的 get_instance_decrypted()，复用其连接参数与加密逻辑。
  - 每个指标的采集都包在 try/except 中，单点失败不影响整体循环。
"""

import os
import re
import json
import time
import socket
import sqlite3
import threading
from datetime import datetime

# ── 配置 ───────────────────────────────────────────────
DEFAULT_INTERVAL = 30          # 采集间隔（秒）
DEFAULT_MAX_POINTS = 2000      # 每实例保留的快照数（环形裁剪）
CIRCUIT_FAIL_THRESHOLD = 5     # 连续失败多少次进入退避
CIRCUIT_COOLDOWN = 60          # 退避时长（秒）
CONNECT_TIMEOUT = 3            # 连接超时（秒）

# ── SSH 并发护栏（全局共享，防止把目标机 sshd 打满）─────────────
# 同一主机同时仅允许 1 个 SSH 连接（即使被注册成多个实例，也不会在一轮 tick 内
# 并发去连它）；进程内 SSH 连接总数设上限（含实时监控 / 巡检 / 手动测试共享），
# 避免叠加时瞬时压垮 sshd 的 MaxStartups，表现为 Connection closed / banner EOFError。
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

def _is_self_host(host: str) -> bool:
    """判断 ssh_host 是否指向中心机自身（允许本地实例误配 SSH 时回退本机采集）。"""
    h = (host or '').strip().lower()
    if h in ('localhost', '127.0.0.1', '::1', '0.0.0.0', '::'):
        return True
    try:
        local = socket.gethostname().lower()
        if h == local:
            return True
        addrs = set()
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addrs.add(info[4][0])
        if h in addrs:
            return True
    except Exception:
        pass
    return False

def _host_unavailable(reason: str) -> dict:
    """构造“宿主资源不可用”的结果 dict，携带原因，便于前端直接展示。"""
    return {'host_collector_source': 'unavailable', 'host_error': str(reason or '未知原因')}

# 计数器型指标（用于差分算速率）——按 db_type 分派
COUNTER_KEYS = {
    'mysql': {'queries', 'bytes_received', 'bytes_sent', 'connections',
              'slow_queries', 'aborted_connects', 'threads_created'},
    'tidb': {'queries', 'bytes_received', 'bytes_sent', 'connections',
             'slow_queries', 'aborted_connects', 'threads_created'},
    'oceanbase': {'queries', 'bytes_received', 'bytes_sent', 'connections',
                  'slow_queries', 'aborted_connects', 'threads_created'},
    'postgresql': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit',
                   'deadlocks', 'conflicts', 'tup_returned', 'tup_fetched',
                   'tup_inserted', 'tup_updated', 'tup_deleted'},
    'pg': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit',
           'deadlocks', 'conflicts'},
    'ivorysql': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit', 'deadlocks'},
    'kingbase': {'xact_commit', 'xact_rollback', 'blks_read', 'blks_hit', 'deadlocks'},
    'oracle': {'ora_user_commits', 'ora_physical_reads', 'ora_redo_size',
               'ora_user_calls', 'ora_session_logical_reads',
               'ora_db_block_gets', 'ora_consistent_gets'},
    'oracle_jdbc': {'ora_user_commits', 'ora_physical_reads', 'ora_redo_size',
                    'ora_user_calls', 'ora_session_logical_reads',
                    'ora_db_block_gets', 'ora_consistent_gets'},
    'sqlserver': {'io_read_count', 'io_write_count', 'io_read_bytes', 'io_write_bytes'},
    'mssql':   {'io_read_count', 'io_write_count', 'io_read_bytes', 'io_write_bytes'},
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
class MetricsCollector:
    def __init__(self, socketio=None, db_path: str = None, interval: int = DEFAULT_INTERVAL):
        if db_path is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, 'data')
            db_path = os.path.join(data_dir, 'pro_metrics.db')
        self.store = MetricsStore(db_path)
        self.socketio = socketio
        self.interval = interval
        self.running = False
        self._last = {}        # instance_id -> (prev_counter_metrics, prev_ts)
        self._last_host = {}   # instance_id -> (prev_host_metrics, prev_ts)
        self._fail = {}        # instance_id -> 连续失败计数
        self._cooldown = {}    # instance_id -> 退避截止时间戳
        self._lock = threading.Lock()

    # ── 连接 ──
    def _connect(self, inst: dict):
        """返回底层 DBAPI 连接；不支持深采的类型返回 None。"""
        db_type = str(inst.get('db_type') or '').lower()
        host = inst.get('host')
        port = int(inst.get('port') or 0)
        user = inst.get('user')
        password = inst.get('password')
        if db_type in ('mysql', 'tidb', 'oceanbase'):
            import pymysql
            db_name = inst.get('database')
            if db_type == 'oceanbase':
                # OceanBase MySQL 租户：database 即租户名（默认 sys）。
                db_name = db_name or 'sys'
            return pymysql.connect(host=host, port=port, user=user,
                                   password=password, database=db_name,
                                   connect_timeout=CONNECT_TIMEOUT)
        if db_type in ('postgresql', 'pg', 'ivorysql', 'kingbase'):
            import psycopg2
            dbname = inst.get('database') or ('kingbase' if db_type == 'kingbase' else 'postgres')
            return psycopg2.connect(host=host, port=port, user=user,
                                    password=password, dbname=dbname,
                                    connect_timeout=CONNECT_TIMEOUT)
        if db_type == 'oracle_jdbc':
            # oracle_jdbc 数据源一律走插件 JDBC 连接，绝不走 oracledb，
            # 以避免 Oracle 11g 在无 Oracle 客户端环境下连接失败。
            return self._connect_oracle_jdbc(inst)
        if db_type == 'oracle':
            import oracledb
            # 原生 oracle 用 oracledb 直连；端口/协议探测提前暴露典型误配
            ohost, oport, oservice, ois_sid, oproto = self._parse_oracle_target(inst)
            if ohost and oport:
                self._oracle_protocol_probe(ohost, oport, oproto)
            jdbc_url = (inst.get('jdbc_url') or '').strip()
            # 纯 TNS 描述符 (DESCRIPTION=...) oracledb 可直接作为 dsn 使用，无需解析后重拼
            if jdbc_url and jdbc_url.lstrip().upper().startswith('(DESCRIPTION'):
                dsn = jdbc_url
            elif oservice:
                # 服务名用 host:port/service，SID 用 host:port:sid
                dsn = ('%s:%d/%s' % (ohost, oport, oservice)) if not ois_sid \
                      else ('%s:%d:%s' % (ohost, oport, oservice))
            else:
                dsn = '%s:%d/orcl' % (ohost or host, oport or port)
            mode = oracledb.SYSDBA if inst.get('sysdba') else oracledb.DEFAULT_MODE
            kw = dict(user=user, password=password, dsn=dsn, mode=mode)
            # TCPS / SSL：从 jdbc_url 解析到 tcps 时尝试 SSL 连接
            if oproto == 'tcps':
                try:
                    import ssl
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    kw['ssl_context'] = ctx
                except Exception:
                    pass
            return oracledb.connect(**kw)
        if db_type == 'dm':
            import dmPython
            return dmPython.connect(user=user, password=password, server='%s:%d' % (host, port))
        if db_type in ('sqlserver', 'mssql'):
            import pyodbc
            # 支持 DSN 或 host/port 直连
            conn_str = inst.get('connection_string') or ''
            if not conn_str:
                database = inst.get('database') or 'master'
                driver = '{ODBC Driver 17 for SQL Server}'
                try:
                    import pyodbc as _pyodbc
                    drivers = [d for d in _pyodbc.drivers() if 'sql server' in d.lower()]
                    if drivers:
                        driver = '{%s}' % drivers[0]
                except Exception:
                    pass
                conn_str = f'DRIVER={driver};SERVER={host},{port};DATABASE={database};UID={user};PWD={password}'
            return pyodbc.connect(conn_str, timeout=CONNECT_TIMEOUT)
        if db_type == 'mongodb':
            # MongoDB 返回 MongoClient（非 DBAPI 连接）。
            # 为避免 pro/ 与 plugins/available/mongodb/ 的跨目录硬依赖，
            # 此处内联一个轻量 URI 构建（字段向后兼容 ssh_info）。
            from pymongo import MongoClient
            from urllib.parse import quote_plus
            info = dict(inst)
            info.update(inst.get('ssh_info') or {})
            host = info.get('host') or '127.0.0.1'
            port = int(info.get('port') or 27017)
            user = info.get('user') or ''
            password = info.get('password') or ''
            database = info.get('database') or 'admin'
            connect_mode = (info.get('connect_mode') or 'standard').lower()
            auth_source = info.get('auth_source') or 'admin'
            auth_mechanism = info.get('auth_mechanism') or ''
            replica_set = info.get('replica_set') or ''
            tls = bool(info.get('tls', False))
            tls_ca_file = info.get('tls_ca_file') or ''
            tls_cert_key_file = info.get('tls_cert_key_file') or ''
            tls_allow_invalid_certs = bool(info.get('tls_allow_invalid_certs', False))

            scheme = 'mongodb+srv://' if connect_mode == 'srv' else 'mongodb://'
            auth_part = ''
            if user:
                eu = quote_plus(user)
                if password:
                    auth_part = '%s:%s@' % (eu, quote_plus(password))
                else:
                    auth_part = '%s@' % eu
            host_part = host if connect_mode == 'srv' else '%s:%d' % (host, port)
            db_part = '/%s' % database if database else '/'

            params = []
            if auth_source and auth_source != 'admin':
                params.append('authSource=%s' % quote_plus(auth_source))
            elif user and auth_source:
                params.append('authSource=%s' % quote_plus(auth_source))
            if auth_mechanism:
                params.append('authMechanism=%s' % quote_plus(auth_mechanism))
            if replica_set:
                params.append('replicaSet=%s' % quote_plus(replica_set))
            query = '?' + '&'.join(params) if params else ''
            uri = '%s%s%s%s%s' % (scheme, auth_part, host_part, db_part, query)

            kwargs = {
                'serverSelectionTimeoutMS': 5000,
                'connectTimeoutMS': 5000,
                'socketTimeoutMS': 10000,
            }
            if tls:
                kwargs['tls'] = True
                if tls_ca_file:
                    kwargs['tlsCAFile'] = tls_ca_file
                if tls_cert_key_file:
                    kwargs['tlsCertificateKeyFile'] = tls_cert_key_file
                if tls_allow_invalid_certs:
                    kwargs['tlsAllowInvalidCertificates'] = True
            return MongoClient(uri, **kwargs)
        return None

    def _connect_oracle_jdbc(self, inst: dict):
        """oracle_jdbc 类型：一律通过插件走 JDBC 连接（JdbcConnectionWrapper，DB-API 兼容），
        绝不走 oracledb，以避免 Oracle 11g 在无 Oracle 客户端环境下连接失败。
        插件在内部用 JPype 启动 JVM + ojdbc8.jar 建立连接，深采逻辑复用 _collect_oracle()。
        """
        try:
            from plugin_loader import get_plugin_module
        except Exception as e:
            raise RuntimeError('加载插件系统失败: %s' % e)
        mod = get_plugin_module('oracle_jdbc')
        if mod is None or not hasattr(mod, 'get_connection'):
            raise RuntimeError(
                'oracle_jdbc 插件未启用或缺少 get_connection 入口，'
                '请先在插件管理中启用 Oracle (JDBC) 插件'
            )
        return mod.get_connection(
            host=inst.get('host'),
            port=int(inst.get('port') or 1521),
            user=inst.get('user'),
            password=inst.get('password'),
            service_name=inst.get('service_name') or 'ORCL',
            sysdba=bool(inst.get('sysdba')),
            jdbc_url=inst.get('jdbc_url') or None,
        )

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

    # ── Oracle 端口/协议探测 ──
    def _parse_oracle_target(self, inst: dict):
        """从实例字段或 jdbc_url 统一解析出 (host, port, service, is_sid, protocol)。

        探测与正式建连共用同一套解析，避免「探测对、连接错」。支持的 JDBC URL 写法：
          - jdbc:oracle:thin:@host:port:SID             (老式 SID，dsn 用 host:port:sid)
          - jdbc:oracle:thin:@//host:port/SERVICE_NAME  (EZConnect 服务名，dsn 用 host:port/service)
          - jdbc:oracle:thin:@(DESCRIPTION=(HOST=..)(PORT=..)(CONNECT_DATA=(SERVICE_NAME=..|SID=..)))  (TNS 描述符)
          - PROTOCOL=tcps                               (SSL 监听，常用 2484 端口)
        """
        host = inst.get('host')
        port = int(inst.get('port') or 0)
        service = inst.get('service_name') or inst.get('dsn')
        is_sid = False
        protocol = 'tcp'
        jdbc_url = (inst.get('jdbc_url') or '').strip()

        if jdbc_url:
            m = re.search(r'PROTOCOL\s*=\s*(tcps|tcp)', jdbc_url, re.I)
            if m:
                protocol = m.group(1).lower()
            after = jdbc_url.split('@')[-1]
            # TNS 描述符优先用 HOST=/PORT=/SERVICE_NAME=/SID=
            hm = re.search(r'HOST\s*=\s*([^)\s]+)', jdbc_url, re.I)
            if hm:
                host = hm.group(1)
            pm = re.search(r'PORT\s*=\s*(\d+)', jdbc_url, re.I)
            if pm:
                port = int(pm.group(1))
            sm = re.search(r'SERVICE_NAME\s*=\s*([^)\s]+)', jdbc_url, re.I)
            sidm = re.search(r'\(SID\s*=\s*([^)\s]+)\)', jdbc_url, re.I)
            if sm:
                service = sm.group(1)
                is_sid = False
            elif sidm:
                service = sidm.group(1)
                is_sid = True
            if not service:
                # 形如 //host:port/service 或 host:port:sid
                if '//' in after:
                    seg = after.split('//')[-1]
                    p0 = seg.split(':')
                    if len(p0) >= 2:
                        host = p0[0]
                        port = int(p0[1].split('/')[0])
                        if '/' in p0[1]:
                            service = p0[1].split('/', 1)[1]
                else:
                    p0 = after.split(':')
                    if len(p0) == 3:
                        host, port, service = p0[0], int(p0[1]), p0[2]
                        is_sid = True
        return host, port, service, is_sid, protocol

    def _tns_connect_probe(self) -> bytes:
        """构造一个最小 TNS Connect 包，用于触发 Oracle 监听器回包以便嗅探。"""
        connect_data = b'(CONNECT_DATA=(SID=ORCL))'
        body = b''.join([
            (312).to_bytes(2, 'big'),     # version
            (312).to_bytes(2, 'big'),     # version(compat)
            (0).to_bytes(2, 'big'),       # service options
            (2048).to_bytes(2, 'big'),    # SDU
            (2048).to_bytes(2, 'big'),    # TDU
            (0x07ff).to_bytes(2, 'big'),  # characteristics
            (0).to_bytes(2, 'big'),       # turnaround
            (1).to_bytes(2, 'big'),       # 1 in hw
            len(connect_data).to_bytes(2, 'big'),  # connect data length
            (58).to_bytes(2, 'big'),      # offset to connect data (0x3a)
            (2048).to_bytes(2, 'big'),    # max receive
            (0).to_bytes(4, 'big'),       # connect flags
            connect_data,
        ])
        length = 8 + len(body)
        header = length.to_bytes(2, 'big') + bytes([0x01, 0x00]) + (0).to_bytes(4, 'big')
        return header + body

    def _oracle_protocol_probe(self, host, port, protocol):
        """连接端口并嗅探首字节，提前发现「连错端口 / SSL 不匹配」等典型误配，
        给出清晰中文提示，避免笼统的握手失败。探测用独立 socket，失败不阻断正式连接。"""
        s = None
        try:
            s = socket.create_connection((host, int(port)), timeout=CONNECT_TIMEOUT)
            try:
                s.sendall(self._tns_connect_probe())
            except Exception:
                pass
            s.settimeout(2.0)
            data = b''
            try:
                while len(data) < 16:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            except Exception:
                pass
        except socket.timeout:
            return
        except Exception:
            return
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        if not data:
            return
        if data[:4] == b'HTTP':
            raise ValueError(
                'Oracle 连接失败：该端口返回了 HTTP 响应，疑似连到了 Web/HTTP 服务'
                '（如 8080 / 443 控制台端口），并非 Oracle 监听器端口。'
                '请核对 jdbc_url 中 @ 后的 host:port 是否为真实 listener 端口（通常 1521）。'
            )
        if data[0] in (0x15, 0x16):
            raise ValueError(
                'Oracle 连接失败：该端口返回了 TLS/SSL 握手，疑似需要 TCPS(SSL) 连接'
                '（常用 2484 端口）。请在 jdbc_url 中使用 PROTOCOL=tcps 或改用 SSL 连接。'
            )
        # TNS 包：第 3 字节为包类型（1..12）
        if len(data) >= 3 and 1 <= data[2] <= 12:
            return
        raise ValueError(
            'Oracle 连接失败：该端口返回了非 TNS 的协议数据，可能不是 Oracle 监听器端口。'
            '请核对 jdbc_url 中的 host:port。'
        )

    # ── 深采分派 ──
    def _collect_deep(self, db_type: str, conn) -> dict:
        try:
            if db_type in ('mysql', 'tidb'):
                return self._collect_mysql(conn)
            if db_type == 'oceanbase':
                return self._collect_oceanbase(conn)
            if db_type in ('postgresql', 'pg', 'ivorysql', 'kingbase'):
                return self._collect_postgres(conn)
            if db_type in ('oracle', 'oracle_jdbc'):
                return self._collect_oracle(conn)
            if db_type == 'dm':
                return self._collect_dm(conn)
            if db_type in ('sqlserver', 'mssql'):
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
        # 批处理请求/秒 等计数器（sys.dm_os_performance_counters）
        try:
            cur.execute("""
                SELECT object_name, counter_name, cntr_value
                FROM sys.dm_os_performance_counters
                WHERE object_name LIKE '%SQL Statistics%'
                  AND counter_name IN ('SQL Compilations/sec', 'SQL Re-Compilations/sec',
                                       'Batch Requests/sec')
            """)
            for row in cur.fetchall():
                cname = str(row[1]).lower().replace('/sec','').replace(' ','_')
                key = 'mssql_' + cname
                m[key] = _to_num(row[2])
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

    # ── 宿主机资源（eBPF 级真实资源）──
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
        except Exception:
            pass

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

    def _collect_host(self, inst: dict) -> dict:
        """采集目标机的宿主资源。

        - 配置了 SSH → 走远端采集；若远端失败/无数据，返回 {}（该目标显示
          “宿主资源不可用”），**绝不回退到中心机本机数据**（否则会把中心机指标
          误标成目标机的，造成“错机器”误导）。
        - 未配置 SSH（监控中心机自身）→ 本机 psutil 采集。

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
        return self._collect_host_local()

    def _resolve_ssh(self, inst: dict):
        """解析实例的 SSH 配置；未启用或缺失主机则返回 None。"""
        if not inst.get('ssh_enabled'):
            return None
        host = (inst.get('ssh_host') or '').strip()
        if not host:
            return None
        return {
            'host': host,
            'port': int(inst.get('ssh_port') or 22),
            'user': inst.get('ssh_user') or '',
            'password': inst.get('ssh_password') or '',
            'key_file': inst.get('ssh_key_file') or '',
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

    def _collect_host_via_ssh(self, ssh: dict, inst: dict = None, max_retries: int = 2) -> dict:
        """SSH 到目标机，内联执行远端采集脚本，解析 JSON 返回。

        目标机默认零依赖（纯 Shell 读 /proc，无需 Python/psutil）；仅 eBPF 内核级
        路径才需要 python3 + bcc。脚本整份经 stdin 喂给远端解释器，避免目标机落地文件。

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
                pkey = paramiko.RSAKey.from_private_key_file(ssh['key_file'])
                kwargs['pkey'] = pkey
            except Exception:
                try:
                    pkey = paramiko.Ed25519Key.from_private_key_file(ssh['key_file'])
                    kwargs['pkey'] = pkey
                except Exception:
                    pass
        if 'pkey' not in kwargs and ssh['password']:
            kwargs['password'] = ssh['password']
        # 默认走纯 Shell(/proc) 采集：目标机零依赖（无需 Python/psutil）。
        # 仅当实例显式 host_ebpf=True 且目标机装有 Python 时，才走 Python+eBPF 路径。
        enable_ebpf = bool(inst.get('host_ebpf')) if inst else False
        py_src = self._remote_script_src()
        sh_src = self._remote_shell_src()
        ebpf_flag = '1' if enable_ebpf else '0'

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
                # bootstrap（纯 POSIX、无 heredoc）：只做解释器探测与转发。
                # 原实现把整份远端脚本作为「内嵌 heredoc」塞进一条长 cmd，在不同登录
                # shell（bash/dash/tcsh）下行为脆弱、脚本常整段不执行 → 无 stdout；
                # 且从不读 stderr，远端真实报错完全不可见。现改为「bootstrap 探解释器 +
                # 经 stdin 喂脚本」（这正是 "$SHBIN" -s / python - 已期望的：从 stdin 读脚本），
                # 并同时读取 stderr 以拿到真实诊断信息。脚本正文自带 %（如 shell 的 printf
                # '%s'），仍用字符串相加而非 % 拼接，避免误当格式化符。
                bootstrap = (
                    "SHBIN=$(command -v bash 2>/dev/null || command -v sh 2>/dev/null || true); "
                    "PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true); "
                    "if [ -n \"$PYBIN\" ] && [ \"" + ebpf_flag + "\" = \"1\" ]; then "
                    "  \"$PYBIN\" - --window 0.5 --max-time 8; "
                    "elif [ -n \"$SHBIN\" ]; then "
                    "  \"$SHBIN\" -s; "
                    "else "
                    "  echo 'DBCheck: no bash/sh/python interpreter found on target'; exit 3; "
                    "fi"
                )
                # 按 ebpf_flag 选择要经 stdin 喂给远端的脚本（bootstrap 已按同标志选解释器）。
                script_to_run = py_src if (ebpf_flag == '1') else sh_src
                stdin, stdout, stderr = client.exec_command(bootstrap, timeout=20)
                try:
                    stdin.write(script_to_run)
                except Exception:
                    pass
                try:
                    # 关键：关闭写端，远端脚本才会读到 EOF 并结束。
                    # 不要用 stdin.close()——部分 paramiko 版本 close() 不会 shutdown_write，
                    # 导致远端 sh -s 永远读不到 EOF 而挂起 / 无输出（本 bug 的真实诱因之一）。
                    stdin.channel.shutdown_write()
                except Exception:
                    pass
                raw = b''
                err = b''
                ch = stdout.channel
                try:
                    ch.settimeout(12)
                except Exception:
                    pass
                # 先收 stdout（直到 EOF）
                try:
                    while True:
                        chunk = ch.recv(4096)
                        if not chunk:
                            break
                        raw += chunk
                except Exception:
                    pass
                # 再收 stderr（独立通道，带超时），拿到远端真实报错而非瞎猜
                try:
                    stderr.channel.settimeout(5)
                    while True:
                        e = stderr.channel.recv(4096)
                        if not e:
                            break
                        err += e
                except Exception:
                    pass
                raw = raw.decode('utf-8', errors='ignore').strip()
                err_txt = err.decode('utf-8', errors='ignore').strip()
                if not raw:
                    print(f"[metrics] SSH 远端 {ssh['host']} 无任何 stdout 输出（远端脚本未产出 JSON）", flush=True)
                    if err_txt:
                        return _host_unavailable('远端脚本执行失败: ' + err_txt[:200])
                    return _host_unavailable('远端脚本未产出任何 JSON（bash/sh 缺失或脚本未执行）')
                try:
                    data = json.loads(raw)
                except Exception:
                    detail = raw[:80]
                    if err_txt:
                        detail += ' | stderr: ' + err_txt[:80]
                    return _host_unavailable('远端输出非合法 JSON: %s' % detail)
                return data if isinstance(data, dict) else _host_unavailable('远端输出非 JSON 对象')
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
        """中心机本机采集（同机场景或无 SSH 时降级）。默认仅 psutil，不自动加载 eBPF。"""
        try:
            from pro import remote_host_collector as rh
            return rh.collect_host(use_ebpf=False, window=0.5)
        except Exception:
            # 极端：连 remote_host_collector 都 import 失败，用内置 psutil 兜底
            return self._collect_host_psutil_fallback()

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
    def _in_cooldown(self, inst_id: str) -> bool:
        until = self._cooldown.get(inst_id)
        return bool(until and time.time() < until)

    def _on_fail(self, inst_id: str):
        self._fail[inst_id] = self._fail.get(inst_id, 0) + 1
        if self._fail[inst_id] >= CIRCUIT_FAIL_THRESHOLD:
            self._cooldown[inst_id] = time.time() + CIRCUIT_COOLDOWN

    def _on_success(self, inst_id: str):
        self._fail[inst_id] = 0

    # ── 单实例采集 ──
    def collect_one(self, inst: dict) -> dict:
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

        if self._in_cooldown(inst_id):
            snapshot['available'] = False
            snapshot['status'] = 'cooldown'
            return snapshot

        up, latency = self._tcp_probe(inst.get('host'), inst.get('port'))
        snapshot['available'] = bool(up)
        snapshot['latency_ms'] = latency
        if not up:
            self._on_fail(inst_id)
            # 数据库不可达仍采集宿主机资源，保证监控哨兵有真实数据底座
            self._attach_host(snapshot, inst_id, inst)
            return snapshot

        conn = None
        try:
            conn = self._connect(inst)
            if conn is not None:
                metrics = self._collect_deep(db_type, conn)
                snapshot.update(metrics)
                self._compute_rates(inst_id, db_type, snapshot, metrics)
        except Exception as e:
            snapshot['error'] = str(e)[:200]
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        self._on_success(inst_id)
        self._attach_host(snapshot, inst_id, inst)
        return snapshot

    # ── 一轮采集（被调度器调用）──
    def tick(self) -> list:
        try:
            from pro import get_instance_manager
            im = get_instance_manager()
            # 采集器在服务端运行，需要明文密码才能建立数据库连接与 SSH 跳板。
            # get_all_instances() 返回的是加密存储值，必须逐实例经 get_instance_decrypted()
            # 解密后才能用于连接——否则 DB/SSH 认证会用密文而 Authentication failed。
            # （参照 monitor_engine._connect_and_query 的既有正确做法。）
            raw_instances = []
            for meta in im.get_all_instances(mask_password=False):
                iid = meta.get('id')
                if not iid:
                    continue
                dec = im.get_instance_decrypted(iid)
                if dec:
                    raw_instances.append(dec)
        except Exception as e:
            return [{'error': 'collector tick failed: %s' % str(e)[:200]}]

        batch = []
        for inst in raw_instances:
            try:
                snap = self.collect_one(inst)
            except Exception as e:
                snap = {'instance_id': inst.get('id'), 'error': str(e)[:200]}
            if snap.get('instance_id'):
                try:
                    self.store.save_snapshot(snap['instance_id'], snap['ts'], snap)
                except Exception:
                    pass
                batch.append(snap)

        if self.socketio and batch:
            try:
                self.socketio.emit(
                    'metrics',
                    {'snapshots': batch, 'ts': datetime.now().isoformat()},
                    room='monitor',
                )
            except Exception:
                pass
        return batch


# ── 进程内单例 ──────────────────────────────────────────
_collector = None


def get_collector() -> MetricsCollector:
    return _collector


def set_collector(c: MetricsCollector):
    global _collector
    _collector = c
