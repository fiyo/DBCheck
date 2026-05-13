# -*- coding: utf-8 -*-
"""
DBCheck SQLite 历史记录管理器
将每次巡检的关键指标持久化到 SQLite 数据库，
支持同一数据库实例的历史对比和趋势数据生成。

表结构：
  history_instances   - 数据库实例注册表
  snapshots   - 每次巡检的快照（含提取的数值指标 + 完整 context JSON）
"""

import os
import json
import sqlite3
import hashlib
from datetime import datetime
from contextlib import contextmanager

# 忽略的挂载点（外接 ISO/Media 光盘等分区，不应计入磁盘使用率）
IGNORE_MOUNTS = {'/mnt/iso', '/media', '/run/media', '/iso', '/cdrom'}


def _db_key(db_type: str, host: str, port) -> str:
    """生成数据库实例唯一键"""
    raw = f"{db_type}:{host}:{port}"
    return hashlib.md5(raw.encode()).hexdigest()[:12] + f"_{host}_{port}"


@contextmanager
def _get_conn(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class SQLiteHistoryManager:
    """
    将每次巡检的关键指标持久化到 SQLite 数据库，
    支持同一数据库实例的历史对比和趋势数据生成。

    文件位于：<base_dir>/history.db
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS history_instances (
        key         TEXT PRIMARY KEY,   -- _db_key() 生成
        db_type     TEXT NOT NULL,
        host        TEXT NOT NULL,
        port        TEXT NOT NULL,
        label       TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS snapshots (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_key TEXT NOT NULL,
        ts          TEXT NOT NULL,          -- 'YYYY-MM-DD HH:MM:SS'
        report_time TEXT NOT NULL,
        risk_count  INTEGER NOT NULL DEFAULT 0,
        health_status TEXT NOT NULL DEFAULT '未知',
        -- 提取的数值指标（供趋势图直接使用）
        cpu_usage       REAL,
        mem_usage       REAL,
        disk_usage_max  REAL,
        connections      INTEGER,
        max_connections INTEGER,
        max_used_connections INTEGER,
        queries_total   INTEGER,
        cache_hit_ratio REAL,
        sga_total_mb    REAL,
        max_tablespace_pct REAL,
        connection_usage_pct REAL,
        db_version      TEXT,
        -- 完整 context JSON（供详细对比）
        context_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (instance_key) REFERENCES history_instances(key) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_snapshots_instance_ts
        ON snapshots(instance_key, ts);
    """

    def __init__(self, base_dir: str):
        self.db_path = os.path.join(base_dir, 'history.db')
        self._ensure_db()

    def _ensure_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with _get_conn(self.db_path) as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()

    def _now_str(self) -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _ensure_instance(self, conn, instance_key: str, db_type: str, host: str, port, label: str):
        row = conn.execute(
            "SELECT key FROM history_instances WHERE key=?", (instance_key,)
        ).fetchone()
        if not row:
            now = self._now_str()
            conn.execute(
                "INSERT INTO history_instances (key,db_type,host,port,label,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (instance_key, db_type, host, str(port), label, now, now)
            )
        else:
            conn.execute(
                "UPDATE history_instances SET label=?,updated_at=? WHERE key=?",
                (label, self._now_str(), instance_key)
            )

    def save_snapshot(self, db_type: str, host: str, port, label: str, context: dict):
        """
        从 context 提取关键指标，并存入 SQLite。

        :param db_type: 'mysql'、'pg'、'oracle'、'dm'、'sqlserver'、'tidb'
        :param host: 数据库 IP
        :param port: 数据库端口
        :param label: 数据库标签名
        :param context: getData.checkdb() 返回的 context 字典
        """
        key = _db_key(db_type, host, port)
        snap = self._extract_metrics(db_type, context)
        snap['ts'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        snap['report_time'] = snap['ts']
        snap['risk_count'] = len(context.get('auto_analyze', []))
        snap['health_status'] = context.get('health_status', '未知')

        # 保留完整 context 供后续详细分析（序列化时去掉不可 JSON 序列化的部分）
        try:
            context_json = json.dumps(context, ensure_ascii=False, default=str)
        except Exception:
            context_json = '{}'

        with _get_conn(self.db_path) as conn:
            self._ensure_instance(conn, key, db_type, host, port, label)
            conn.execute("""
                INSERT INTO snapshots (
                    instance_key, ts, report_time, risk_count, health_status,
                    cpu_usage, mem_usage, disk_usage_max,
                    connections, max_connections, max_used_connections,
                    queries_total, cache_hit_ratio, sga_total_mb,
                    max_tablespace_pct, connection_usage_pct, db_version,
                    context_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                key, snap['ts'], snap['report_time'], snap['risk_count'], snap['health_status'],
                snap.get('cpu_usage'), snap.get('mem_usage'), snap.get('disk_usage_max'),
                snap.get('connections'), snap.get('max_connections'), snap.get('max_used_connections'),
                snap.get('queries_total'), snap.get('cache_hit_ratio'), snap.get('sga_total_mb'),
                snap.get('max_tablespace_pct'), snap.get('connection_usage_pct'), snap.get('version'),
                context_json
            ))
            # 保留最近 30 条
            conn.execute("""
                DELETE FROM snapshots WHERE instance_key=? AND id NOT IN (
                    SELECT id FROM snapshots WHERE instance_key=? ORDER BY ts DESC LIMIT 30
                )
            """, (key, key))
            conn.commit()
        return key

    def _extract_metrics(self, db_type: str, context: dict) -> dict:
        """从 context 提取可量化的核心指标"""
        def _safe_int(lst, field='Value'):
            try:
                return int(str(lst[0].get(field, 0)).replace(',', ''))
            except Exception:
                return 0

        def _safe_float(lst, field='Value'):
            try:
                return float(str(lst[0].get(field, 0)).replace(',', ''))
            except Exception:
                return 0.0

        m = {}
        sys_info = context.get('system_info', {})
        cpu_val = sys_info.get('cpu', {})
        if isinstance(cpu_val, dict):
            m['cpu_usage'] = cpu_val.get('usage_percent', 0) or 0.0
        else:
            m['cpu_usage'] = float(cpu_val) if cpu_val else 0.0
        m['mem_usage'] = sys_info.get('memory', {}).get('usage_percent', 0) or 0.0

        disks = sys_info.get('disk_list', [])
        m['disk_usage_max'] = 0.0
        for d in disks:
            mp = d.get('mountpoint', '/')
            if mp in IGNORE_MOUNTS:
                continue
            pct = d.get('usage_percent', 0) or 0.0
            if isinstance(pct, str):
                pct = float(pct.rstrip('%'))
            m['disk_usage_max'] = max(m['disk_usage_max'], pct)

        # disk_usage fallback（SSH 采集器格式）
        if m['disk_usage_max'] == 0:
            raw = sys_info.get('disk_usage', '')
            if raw:
                for line in raw.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        try:
                            pct = int(parts[4].rstrip('%'))
                            mp = parts[-1]
                            if mp not in IGNORE_MOUNTS:
                                m['disk_usage_max'] = max(m['disk_usage_max'], pct)
                        except (ValueError, IndexError):
                            continue

        if db_type == 'mysql':
            m['connections'] = _safe_int(context.get('threads_connected', []))
            m['max_connections'] = _safe_int(context.get('max_connections', []))
            m['max_used_connections'] = _safe_int(context.get('max_used_connections', []))
            queries_data = context.get('queries', [])
            m['queries_total'] = _safe_int(queries_data)
            m['version'] = (context.get('myversion', [{}]) or [{}])[0].get('version', '') if context.get('myversion') else ''
        elif db_type in ('postgres', 'postgresql'):
            pg_conn = context.get('pg_connections', [])
            m['connections'] = _safe_int(pg_conn, 'used_connections') if pg_conn else 0
            m['max_connections'] = _safe_int(pg_conn, 'max_connections') if pg_conn else 0
            cache_hits = context.get('pg_cache_hit', [])
            m['cache_hit_ratio'] = _safe_float(cache_hits, 'cache_hit_ratio') if cache_hits else 0.0
            m['version'] = (context.get('pg_version', [{}]) or [{}])[0].get('version', '') if context.get('pg_version') else ''
        elif db_type in ('oracle', 'oracle_full'):
            ora_sess = context.get('ora_sessions', [])
            m['connections'] = _safe_int(ora_sess, 'TOTAL_SESSIONS') if ora_sess else 0
            ora_limit = context.get('ora_session_limit', [])
            if ora_limit:
                m['max_connections'] = _safe_int(ora_limit, 'SESSIONS_LIMIT')
            else:
                m['max_connections'] = m['connections'] + 100
            sga_total = context.get('ora_sga_total', [])
            m['sga_total_mb'] = _safe_float(sga_total, 'SGA_TOTAL_MB') if sga_total else 0.0
            ts_list = context.get('ora_tablespace', [])
            if ts_list and isinstance(ts_list, list):
                m['max_tablespace_pct'] = max((_safe_float(ts.get('USED_PCT_WITH_MAXEXT', ts.get('USED_PCT', 0))) for ts in ts_list), default=0.0)
            m['version'] = (context.get('ora_version', [{}]) or [{}])[0].get('BANNER', '') if context.get('ora_version') else ''
        elif db_type == 'dm':
            dm_sess = context.get('dm_sessions', [])
            m['connections'] = _safe_int(dm_sess, 'TOTAL_SESSIONS') if dm_sess and isinstance(dm_sess, list) else 0
            dm_limit = context.get('dm_session_limit', [])
            m['max_connections'] = _safe_int(dm_limit, 'SESSIONS_LIMIT') if dm_limit and isinstance(dm_limit, list) else m['connections'] + 100
            dm_sga = context.get('dm_sga_total', [])
            m['sga_total_mb'] = _safe_float(dm_sga, 'SGA_TOTAL_MB') if dm_sga and isinstance(dm_sga, list) else 0.0
            dm_ts = context.get('dm_tablespace', [])
            if dm_ts and isinstance(dm_ts, list):
                m['max_tablespace_pct'] = max((_safe_float(ts.get('USED_PCT', 0)) for ts in dm_ts), default=0.0)
            dm_ver = context.get('dm_version')
            m['version'] = (dm_ver or [{}])[0].get('BANNER', '') if dm_ver and isinstance(dm_ver, list) else ''
        elif db_type == 'sqlserver':
            ss_conn = context.get('connections', [])
            m['connections'] = ss_conn[0].get('connection_count', 0) if ss_conn and isinstance(ss_conn[0], dict) else 0
            m['max_connections'] = ss_conn[0].get('max_connections', 0) if ss_conn and isinstance(ss_conn[0], dict) else 0
            m['connection_usage_pct'] = ss_conn[0].get('connection_usage_pct', 0) if ss_conn and isinstance(ss_conn[0], dict) else 0.0
            host_info = context.get('host', {})
            m['mem_usage'] = host_info.get('memory_percent', 0) or 0.0 if host_info else 0.0
            m['version'] = ''
            ver_rows = context.get('version', [])
            if ver_rows and isinstance(ver_rows, list) and len(ver_rows) > 0:
                for row in ver_rows:
                    if isinstance(row, (list, tuple)) and len(row) >= 2:
                        m['version'] = str(row[1])
                        break
        elif db_type == 'tidb':
            m['connections'] = _safe_int(context.get('threads_connected', []))
            m['max_connections'] = _safe_int(context.get('max_connections', []))
            m['max_used_connections'] = _safe_int(context.get('max_used_connections', []))
            queries_data = context.get('queries', [])
            m['queries_total'] = _safe_int(queries_data)
            m['version'] = (context.get('myversion', [{}]) or [{}])[0].get('version', '') if context.get('myversion') else ''

        return m

    def get_trend(self, db_type: str, host: str, port) -> dict:
        """
        获取指定实例的历史趋势数据，供前端图表使用。

        :return: {
            'labels': ['2026-04-10 08:00', ...],
            'metrics': {
                'mem_usage': [65.2, 70.1, ...],
                'connections': [20, 35, ...],
                ...
            },
            'risk_counts': [1, 2, ...],
            'health_statuses': ['良好', ...],
            'label': '数据库标签名',
            'snapshots_count': 10
        }
        """
        key = _db_key(db_type, host, port)
        with _get_conn(self.db_path) as conn:
            # 获取实例信息
            inst_row = conn.execute(
                "SELECT label FROM history_instances WHERE key=?", (key,)
            ).fetchone()
            if not inst_row:
                return {}

            label = inst_row[0] or ''

            # 获取所有快照（按时间升序）
            rows = conn.execute("""
                SELECT ts, cpu_usage, mem_usage, disk_usage_max,
                       connections, max_connections, max_used_connections,
                       queries_total, cache_hit_ratio, sga_total_mb,
                       max_tablespace_pct, connection_usage_pct,
                       risk_count, health_status
                FROM snapshots
                WHERE instance_key=?
                ORDER BY ts ASC
            """, (key,)).fetchall()

            if not rows:
                return {}

            labels = [r[0] for r in rows]
            metric_keys = [
                'cpu_usage', 'mem_usage', 'disk_usage_max',
                'connections', 'max_connections', 'max_used_connections',
                'queries_total', 'cache_hit_ratio', 'sga_total_mb',
                'max_tablespace_pct', 'connection_usage_pct'
            ]
            metrics = {mk: [] for mk in metric_keys}
            risk_counts = []
            health_statuses = []

            for r in rows:
                risk_counts.append(r[12] or 0)
                health_statuses.append(r[13] or '未知')
                metrics['cpu_usage'].append(r[1] or 0.0)
                metrics['mem_usage'].append(r[2] or 0.0)
                metrics['disk_usage_max'].append(r[3] or 0.0)
                metrics['connections'].append(r[4] or 0)
                metrics['max_connections'].append(r[5] or 0)
                metrics['max_used_connections'].append(r[6] or 0)
                metrics['queries_total'].append(r[7] or 0)
                metrics['cache_hit_ratio'].append(r[8] or 0.0)
                metrics['sga_total_mb'].append(r[9] or 0.0)
                metrics['max_tablespace_pct'].append(r[10] or 0.0)
                metrics['connection_usage_pct'].append(r[11] or 0.0)

            # 只返回有数据的指标
            final_metrics = {}
            for mk, vals in metrics.items():
                if any(v != 0 and v is not None for v in vals):
                    final_metrics[mk] = vals

            return {
                'labels': labels,
                'metrics': final_metrics,
                'risk_counts': risk_counts,
                'health_statuses': health_statuses,
                'label': label,
                'snapshots_count': len(rows)
            }

    def get_comparison(self, db_type: str, host: str, port) -> dict:
        """
        获取最近两次巡检的对比数据。

        :return: {
            'prev': {...metrics...},
            'curr': {...metrics...},
            'diff': {'mem_usage': +5.2, ...}
        }
        """
        key = _db_key(db_type, host, port)
        with _get_conn(self.db_path) as conn:
            rows = conn.execute("""
                SELECT ts, cpu_usage, mem_usage, disk_usage_max,
                       connections, max_connections, max_used_connections,
                       queries_total, cache_hit_ratio, sga_total_mb,
                       max_tablespace_pct, connection_usage_pct,
                       risk_count, health_status
                FROM snapshots
                WHERE instance_key=?
                ORDER BY ts DESC
                LIMIT 2
            """, (key,)).fetchall()

            if not rows or len(rows) < 2:
                return {}

            curr, prev = rows[0], rows[1]  # curr 是最新的

            fields = ['ts', 'cpu_usage', 'mem_usage', 'disk_usage_max',
                      'connections', 'max_connections', 'max_used_connections',
                      'queries_total', 'cache_hit_ratio', 'sga_total_mb',
                      'max_tablespace_pct', 'connection_usage_pct',
                      'risk_count', 'health_status']

            curr_dict = {f: curr[i] for i, f in enumerate(fields)}
            prev_dict = {f: prev[i] for i, f in enumerate(fields)}

            diff = {}
            for k in fields:
                if k in ('ts', 'health_status'):
                    continue
                a, b = curr_dict.get(k), prev_dict.get(k)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    diff[k] = round(a - b, 2)

            return {
                'prev': prev_dict,
                'curr': curr_dict,
                'diff': diff,
                'prev_ts': prev_dict['ts'],
                'curr_ts': curr_dict['ts']
            }

    def list_instances(self) -> list:
        """列出所有已记录的数据库实例"""
        with _get_conn(self.db_path) as conn:
            rows = conn.execute("""
                SELECT i.key, i.db_type, i.host, i.port, i.label,
                       i.updated_at,
                       (SELECT COUNT(*) FROM snapshots s WHERE s.instance_key=i.key) AS snapshot_count,
                       (SELECT s.health_status FROM snapshots s WHERE s.instance_key=i.key
                        ORDER BY s.ts DESC LIMIT 1) AS last_health,
                       (SELECT s.risk_count FROM snapshots s WHERE s.instance_key=i.key
                        ORDER BY s.ts DESC LIMIT 1) AS last_risk,
                       (SELECT s.ts FROM snapshots s WHERE s.instance_key=i.key
                        ORDER BY s.ts DESC LIMIT 1) AS last_time
                FROM history_instances i
                ORDER BY i.updated_at DESC
            """).fetchall()

        result = []
        for r in rows:
            result.append({
                'key': r[0],
                'db_type': r[1],
                'host': r[2],
                'port': r[3],
                'label': r[4] or r[0],
                'updated_at': r[5] or '',
                'snapshots_count': r[6] or 0,
                'last_health': r[7] or '未知',
                'last_risk': r[8] or 0,
                'last_time': r[9] or '',
            })
        return result

    def delete_instance(self, key: str):
        """删除指定实例及其所有快照"""
        with _get_conn(self.db_path) as conn:
            conn.execute("DELETE FROM snapshots WHERE instance_key=?", (key,))
            conn.execute("DELETE FROM history_instances WHERE key=?", (key,))
            conn.commit()
