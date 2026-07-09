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

# 计数器型指标（用于差分算速率）——按 db_type 分派
COUNTER_KEYS = {
    'mysql': {'queries', 'bytes_received', 'bytes_sent', 'connections',
              'slow_queries', 'aborted_connects', 'threads_created'},
    'tidb': {'queries', 'bytes_received', 'bytes_sent', 'connections',
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
        if db_type in ('mysql', 'tidb'):
            import pymysql
            return pymysql.connect(host=host, port=port, user=user,
                                   password=password, connect_timeout=CONNECT_TIMEOUT)
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
            if db_type in ('postgresql', 'pg', 'ivorysql', 'kingbase'):
                return self._collect_postgres(conn)
            if db_type in ('oracle', 'oracle_jdbc'):
                return self._collect_oracle(conn)
            if db_type == 'dm':
                return self._collect_dm(conn)
            if db_type in ('sqlserver', 'mssql'):
                return self._collect_sqlserver(conn)
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

    # ── 宿主机资源（eBPF 级真实资源，对标 DBdoctor「数据底座」）──
    def _collect_host(self) -> dict:
        """采集宿主机真实资源：CPU 指令/IO 等待拆解、内存、交换、磁盘 IO。

        仅依赖 psutil（已在 requirements.txt 声明）。采集失败时返回空字典，
        不影响数据库层指标。生产环境（Linux）可拿到与 eBPF 同级的细粒度资源视图。
        """
        try:
            import psutil
        except Exception:
            return {}
        m: dict = {}
        # CPU 时间拆解（user / system / iowait / idle）
        try:
            ct = psutil.cpu_times_percent(interval=None)
            user = float(getattr(ct, 'user', 0) or 0)
            system = float(getattr(ct, 'system', 0) or 0)
            iowait = float(getattr(ct, 'iowait', 0) or 0)
            idle = float(getattr(ct, 'idle', 0) or 0)
            m['host_cpu_user'] = round(user, 1)
            m['host_cpu_system'] = round(system, 1)
            m['host_cpu_iowait'] = round(iowait, 1)
            m['host_cpu_idle'] = round(idle, 1)
            m['host_cpu'] = round(100.0 - idle, 1)
        except Exception:
            pass
        # 内存 / 交换
        try:
            vm = psutil.virtual_memory()
            m['host_mem'] = round(float(vm.percent), 1)
            m['host_mem_used_gb'] = round(vm.used / (1024 ** 3), 1)
            m['host_mem_total_gb'] = round(vm.total / (1024 ** 3), 1)
            sm = psutil.swap_memory()
            m['host_swap_pct'] = round(float(sm.percent), 1)
        except Exception:
            pass
        # 系统负载（Unix 可用）
        try:
            la = psutil.getloadavg()
            m['host_load1'] = round(float(la[0]), 2)
            m['host_load5'] = round(float(la[1]), 2)
            m['host_load15'] = round(float(la[2]), 2)
        except Exception:
            pass
        # 磁盘 IO 计数器（累计值，速率在 _compute_host_rates 差分）
        try:
            d = psutil.disk_io_counters()
            if d:
                m['host_disk_read_bytes'] = int(getattr(d, 'read_bytes', 0) or 0)
                m['host_disk_write_bytes'] = int(getattr(d, 'write_bytes', 0) or 0)
                m['host_disk_read_count'] = int(getattr(d, 'read_count', 0) or 0)
                m['host_disk_write_count'] = int(getattr(d, 'write_count', 0) or 0)
                m['host_disk_read_ms'] = int(getattr(d, 'read_time', 0) or 0)
                m['host_disk_write_ms'] = int(getattr(d, 'write_time', 0) or 0)
        except Exception:
            pass
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

    def _attach_host(self, snapshot: dict, inst_id: str):
        """挂载宿主机资源指标（与数据库可用性无关，连接失败时也采集）。"""
        try:
            host = self._collect_host()
            if host:
                snapshot.update(host)
                self._compute_host_rates(inst_id, snapshot, host)
        except Exception:
            pass

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
            self._attach_host(snapshot, inst_id)
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
        self._attach_host(snapshot, inst_id)
        return snapshot

    # ── 一轮采集（被调度器调用）──
    def tick(self) -> list:
        try:
            from pro import get_instance_manager
            im = get_instance_manager()
            # 注意：采集器在服务端运行，需要真实密码才能建立数据库连接，
            # 因此不能用 mask_password=True（会把密码替换为 ****）。
            # 参考 scheduler.py / monitor_engine.py 的做法：使用 False 或 get_instance_decrypted()。
            raw_instances = im.get_all_instances(mask_password=False)
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
