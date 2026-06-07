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
import threading
import json
from collections import deque
from pro.instance_manager import get_instance_manager
import monitor_queries as mq


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
            self._thread = threading.Thread(target=self._background_loop, daemon=True)
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
            all_instances = im.get_all_instances(mask_password=False)
            instances = [i for i in all_instances if i.get('enabled', True)]
            if not instances:
                return

            # 采集慢查询
            new_slow = {}
            new_conn = {}
            conn_history_items = []

            for inst in instances:
                iid = inst['id']
                if not inst.get('host'):
                    print(f"[Monitor] 跳过空 host 实例: {iid}", flush=True)
                    continue
                db_type = inst.get('db_type', '').lower()
                label = f"{inst.get('name', iid)} ({inst.get('host', '?')}:{inst.get('port', '?')})"

                # 慢查询
                try:
                    sq = self._collect_slow(iid, db_type, label)
                    new_slow[iid] = sq
                except Exception as e:
                    print(f"[Monitor] 慢查询采集失败 {label}: {e}", flush=True)
                    new_slow[iid] = {
                        'data': [], 'error': str(e),
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
                    print(f"[Monitor] 连接采集失败 {label}: {e}", flush=True)
                    new_conn[iid] = {
                        'data': [], 'error': str(e),
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
            print(f"[Monitor] 采集失败: {e}", flush=True)

    # ═══════════════════════════════════════════════════════════
    #  采集实现
    # ═══════════════════════════════════════════════════════════

    def _collect_slow(self, instance_id, db_type, label):
        sql = mq.SLOW_QUERY_TEMPLATES.get(db_type)
        if not sql:
            return {'data': [], 'error': f'不支持的类型: {db_type}',
                    'ts': time.time(), 'db_type': db_type, 'label': label}

        try:
            rows = self._connect_and_query(instance_id, sql)
        except Exception as e:
            # 尝试 fallback SQL
            fallback_sql = mq.SLOW_QUERY_FALLBACK_TEMPLATES.get(db_type)
            if fallback_sql:
                try:
                    rows = self._connect_and_query(instance_id, fallback_sql)
                    print(f"[Monitor] {label} 使用 fallback 慢查询 SQL", flush=True)
                except Exception as fb:
                    return {'data': [], 'error': f'SQL失败: {fb}',
                            'ts': time.time(), 'db_type': db_type, 'label': label}
            else:
                return {'data': [], 'error': f'SQL失败: {e}',
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
            print(f"[Monitor] 连接 SQL 失败 {label}: {e}", flush=True)
            return {'data': [], 'error': f'连接SQL失败: {e}',
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
    #  通用连接+查询
    # ═══════════════════════════════════════════════════════════

    def _connect_and_query(self, instance_id, sql, timeout=None):
        """连接到指定数据源并执行 SQL，返回 list of dicts"""
        if timeout is None:
            timeout = self.QUERY_TIMEOUT

        im = get_instance_manager()
        inst = im.get_instance_decrypted(instance_id)
        if not inst:
            raise ValueError(f"实例 {instance_id} 不存在")

        db_type = inst['db_type'].lower()
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
                    row_dict[col] = val
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

        if db_type == 'mysql':
            import pymysql
            return pymysql.connect(
                host=host, port=port, user=user, password=password,
                database=inst.get('database') or 'mysql',
                charset='utf8mb4', connect_timeout=timeout, read_timeout=timeout,
            )

        elif db_type in ('postgresql', 'pg'):
            import psycopg2
            return psycopg2.connect(
                host=host, port=port, user=user, password=password,
                dbname=inst.get('database') or 'postgres',
                client_encoding='UTF8', connect_timeout=timeout,
            )

        elif db_type == 'oracle':
            import oracledb
            dsn = inst.get('service_name') or f"{host}:{port}/orcl"
            mode = oracledb.SYSDBA if inst.get('sysdba') else oracledb.DEFAULT_MODE
            return oracledb.connect(user=user, password=password, dsn=dsn, mode=mode)

        elif db_type == 'sqlserver':
            import pyodbc
            conn_str = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={host},{port};"
                f"UID={user};PWD={password};"
                f"TrustServerCertificate=yes;Encrypt=yes;"
                f"Connect Timeout={timeout};"
            )
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
            import yasdb
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
