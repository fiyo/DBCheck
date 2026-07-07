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
    # DM8 计数器名不固定，速率在深采中按 dm_ 前缀动态处理
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
        if db_type == 'oracle':
            import oracledb
            dsn = inst.get('service_name') or '%s:%d/orcl' % (host, port)
            mode = oracledb.SYSDBA if inst.get('sysdba') else oracledb.DEFAULT_MODE
            return oracledb.connect(user=user, password=password, dsn=dsn, mode=mode)
        if db_type == 'dm':
            import dmPython
            return dmPython.connect(user=user, password=password, server='%s:%d' % (host, port))
        return None

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

    # ── 深采分派 ──
    def _collect_deep(self, db_type: str, conn) -> dict:
        try:
            if db_type in ('mysql', 'tidb'):
                return self._collect_mysql(conn)
            if db_type in ('postgresql', 'pg', 'ivorysql', 'kingbase'):
                return self._collect_postgres(conn)
            if db_type == 'oracle':
                return self._collect_oracle(conn)
            if db_type == 'dm':
                return self._collect_dm(conn)
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

    def _collect_oracle(self, conn) -> dict:
        m = {}
        cur = conn.cursor()
        names = ('user commits', 'physical reads', 'redo size', 'user calls',
                 'session logical reads', 'db block gets', 'consistent gets')
        placeholders = ','.join(':%d' % (i + 1) for i in range(len(names)))
        try:
            cur.execute(
                "SELECT name, value FROM v$sysstat WHERE name IN (%s)" % placeholders,
                names,
            )
            for name, value in cur.fetchall():
                m['ora_' + str(name).replace(' ', '_')] = _to_num(value)
        except Exception:
            pass
        # 用户会话数
        try:
            cur.execute("SELECT count(*) FROM v$session WHERE type = 'USER'")
            m['user_sessions'] = _to_num(cur.fetchone()[0])
        except Exception:
            pass
        return m

    def _collect_dm(self, conn) -> dict:
        m = {}
        cur = conn.cursor()
        # 达梦 v$sysstat 列名在不同版本有差异，优先尝试 stat_val，失败回退 value
        try:
            cur.execute("SELECT name, stat_val FROM v$sysstat")
            rows = cur.fetchall()
        except Exception:
            rows = []
            try:
                cur.execute("SELECT name, value FROM v$sysstat")
                rows = cur.fetchall()
            except Exception:
                rows = []
        for name, value in rows:
            m['dm_' + str(name).replace(' ', '_')] = _to_num(value)
        # 活跃会话
        try:
            cur.execute("SELECT count(*) FROM v$session WHERE state = 'ACTIVE'")
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
        return snapshot

    # ── 一轮采集（被调度器调用）──
    def tick(self) -> list:
        try:
            from pro import get_instance_manager
            im = get_instance_manager()
            instances = im.get_all_instances(mask_password=True)
        except Exception as e:
            return [{'error': 'collector tick failed: %s' % str(e)[:200]}]

        batch = []
        for inst in instances:
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
