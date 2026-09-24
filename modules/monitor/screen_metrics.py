# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
监控大屏指标适配层 — DBA 视角六大域 → 统一 MetricKey

设计（docs/design/dashboard-redesign.md）：
- 指标域：可用性 / 连接会话 / 吞吐(QPS·TPS) / 容量空间 / 日志错误 / 复制 HA
- 各库 SQL 按 family 分发（mysql-family / pg-family / oracle / sqlserver / dm 达梦）；
  pg-family 覆盖 kingbase/hgdb/uxdb/vastbase/ivorysql，tidb/oceanbase 走 mysql-family
  （主从查询在 TiDB/OB 上失败自动降级 None）；mongodb/redis/clickhouse 等非 SQL 库
  只出连接数/慢查询（复用 MonitorEngine），无增量指标
- 连接数/慢查询复用 MonitorEngine 已有采集（不重复连接）；增量指标（QPS/TPS/缓存命中）
  由 ScreenCollector 周期采样差值计算
- 单条 SQL 失败只降级该指标（None），不影响其它指标与实例状态判定；
  仅"探针连不上"（engine 侧 error）才判 down

输出统一模型：
    {iid: {'name','db_type','group','host','port','label','status','ts','err',
           'conn':{'total','max_conn','usage_pct','active','idle','blocked'},
           'slowq': int, 'qps': float, 'tps': float, 'cache_hit_pct': float,
           'lock_waits': int, 'repl_lag_s': int,
           'tbs': [{'name','total_mb','free_mb','free_pct'}],
           'spark': [qps...]}   # 最近 N 个采样点的 QPS（迷你趋势）
    }
"""

import time
import json
import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from modules.monitor.queries import normalize_db_type
from modules.monitor import history_store  # 历史回放存储层（P2-4，仅非敏感白名单字段落库）
from modules.monitor.native_collect import NATIVE_DB_TYPES, collect_native

# ═══════════════════════════════════════════════════════════
#  db_type → family 归并
# ═══════════════════════════════════════════════════════════

_DB_FAMILY = {
    'mysql': 'mysql', 'mariadb': 'mysql', 'oceanbase': 'mysql', 'tidb': 'mysql',
    'percona': 'mysql',
    'postgresql': 'pg', 'pg': 'pg', 'ivorysql': 'pg',
    'oracle': 'oracle',
    'sqlserver': 'sqlserver', 'mssql': 'sqlserver',
    'dm': 'dm', 'kingbase': 'pg', 'hgdb': 'pg', 'uxdb': 'pg', 'vastbase': 'pg',
    'gbase': 'gbase', 'db2': 'db2', 'clickhouse': 'clickhouse',
}


def db_family(db_type):
    # 先过 queries.normalize_db_type（oracle_jdbc→oracle、hgdb→pg 等），
    # 保证插件命名（_jdbc 后缀）与国产库标识都能命中 family。
    return _DB_FAMILY.get(normalize_db_type(db_type))


# ═══════════════════════════════════════════════════════════
#  SQL 模板（按 family）
# ═══════════════════════════════════════════════════════════

MYSQL_STAT_SQL = (
    "SHOW GLOBAL STATUS WHERE Variable_name IN "
    "('Questions','Com_commit','Com_rollback','Uptime',"
    "'Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads','Aborted_connects')"
)

# 库/Schema 容量 Top（information_schema 全库聚合，权限要求低）
MYSQL_TBS_SQL = (
    "SELECT table_schema AS name, "
    "ROUND(SUM(data_length + index_length) / 1048576, 1) AS total_mb "
    "FROM information_schema.tables GROUP BY table_schema "
    "ORDER BY total_mb DESC LIMIT 10"
)

# 复制延迟（主从/级联；列名随版本可能是 Seconds_Behind_Master）
MYSQL_REPL_SQL = "SHOW SLAVE STATUS"
MYSQL_REPL_SQL8 = "SHOW REPLICA STATUS"

PG_STAT_SQL = (
    "SELECT "
    "(SELECT COALESCE(SUM(xact_commit), 0) FROM pg_stat_database) AS xact_commit, "
    "(SELECT COALESCE(SUM(xact_rollback), 0) FROM pg_stat_database) AS xact_rollback, "
    "(SELECT COALESCE(SUM(blks_hit), 0) FROM pg_stat_database) AS blks_hit, "
    "(SELECT COALESCE(SUM(blks_read), 0) FROM pg_stat_database) AS blks_read"
)

PG_TBS_SQL = (
    "SELECT datname AS name, ROUND(pg_database_size(datname) / 1048576.0, 1) AS total_mb "
    "FROM pg_database WHERE datallowconn = true "
    "ORDER BY total_mb DESC LIMIT 10"
)

PG_REPL_SQL = (
    "SELECT application_name, state, "
    "COALESCE(ROUND(EXTRACT(EPOCH FROM replay_lag))::int, 0) AS lag_s "
    "FROM pg_stat_replication"
)

PG_LOCKS_SQL = "SELECT count(*) AS n FROM pg_locks WHERE NOT granted"

ORACLE_STAT_SQL = (
    "SELECT name, value FROM v$sysstat WHERE name IN "
    "('execute count','user commits','user rollbacks',"
    "'consistent gets','db block gets','physical reads')"
)

ORACLE_TBS_SQL = (
    "SELECT df.tablespace_name AS name, "
    "ROUND(SUM(df.bytes) / 1048576, 1) AS total_mb, "
    "ROUND(NVL(SUM(fr.free_bytes), 0) / 1048576, 1) AS free_mb "
    "FROM dba_data_files df "
    "LEFT JOIN (SELECT tablespace_name, SUM(bytes) AS free_bytes "
    "           FROM dba_free_space GROUP BY tablespace_name) fr "
    "  ON fr.tablespace_name = df.tablespace_name "
    "GROUP BY df.tablespace_name ORDER BY free_mb ASC"
)

ORACLE_REPL_SQL = (
    "SELECT name, value FROM v$dataguard_stats WHERE name IN ('apply lag','transport lag')"
)

ORACLE_LOCKS_SQL = "SELECT count(*) AS n FROM v$session WHERE blocking_session IS NOT NULL"

# ── RAC / ADG 身份与配对（监控用户权限要求不高于现有 v$sysstat / v$dataguard_stats）──
# v$database：角色（PRIMARY / PHYSICAL STANDBY…）+ 集群唯一名（RAC 各实例相同）+ open_mode
ORACLE_IDENTITY_SQL = (
    "SELECT database_role, db_unique_name, name, open_mode FROM v$database"
)
# v$instance：RAC 实例身份（单机也返回一行：instance_number=1）
ORACLE_INSTANCE_SQL = (
    "SELECT instance_name, instance_number, host_name FROM v$instance"
)
# 主库侧归档目的地：声明往哪些 db_unique_name 送 redo → 前端自动画主备配对线
ORACLE_DEST_SQL = (
    "SELECT db_unique_name, status FROM v$archive_dest "
    "WHERE status = 'VALID' AND destination IS NOT NULL"
)

SQLSERVER_STAT_SQL = (
    "SELECT counter_name, cntr_value FROM sys.dm_os_performance_counters "
    "WHERE counter_name IN ('Batch Requests/sec','Transactions/sec',"
    "'Buffer cache hit ratio','Buffer cache hit ratio base')"
)

SQLSERVER_LOCKS_SQL = (
    "SELECT count(*) AS n FROM sys.dm_exec_requests WHERE blocking_session_id <> 0"
)

SQLSERVER_TBS_SQL = (
    "SELECT TOP 10 name AS name, "
    "ROUND(SUM(CAST(size AS BIGINT) * 8 / 1024.0), 1) AS total_mb "
    "FROM sys.master_files GROUP BY name ORDER BY total_mb DESC"
)

# ── DM8 达梦：V$SYSSTAT 列为 NAME/STAT_VAL，指标名 DM 专属（实测于 DM8，非 Oracle 命名）──
DM_STAT_SQL = (
    "SELECT name, stat_val AS value FROM v$sysstat WHERE name IN "
    "('sql executed count','transaction commit count','transaction rollback count')"
)

DM_TBS_SQL = (
    "SELECT df.tablespace_name AS name, "
    "ROUND(SUM(df.bytes) / 1048576, 1) AS total_mb, "
    "ROUND(NVL(SUM(fr.free_bytes), 0) / 1048576, 1) AS free_mb "
    "FROM dba_data_files df "
    "LEFT JOIN (SELECT tablespace_name, SUM(bytes) AS free_bytes "
    "           FROM dba_free_space GROUP BY tablespace_name) fr "
    "  ON fr.tablespace_name = df.tablespace_name "
    "GROUP BY df.tablespace_name ORDER BY free_mb ASC"
)

# 数据守护备库重演延迟（V$RAPPLY_INFO；单机/视图缺失时失败 → 优雅降级 None）
DM_REPL_SQL = (
    "SELECT ROUND(DATEDIFF(SECOND, MAX(APPLY_TIME), SYSDATE)) AS lag_s "
    "FROM v$rapply_info"
)

# 锁等待：只数 V$TRXWAIT 行数（视图实测存在但列名不在目录视图，避免列依赖）
DM_LOCKS_SQL = "SELECT count(*) AS n FROM v$trxwait"

# ── GBase 8s（sysmaster 库；JDBC 子进程通道）──
# sysprofile 累计计数：isreads/iswrites 为行级 I/O 计数（QPS 近似），
# iscommits/isrollbacks 为事务提交/回滚（TPS 精确）。
GBASE_STAT_SQL = (
    "SELECT name, value FROM sysmaster:sysprofile "
    "WHERE name IN ('isreads','iswrites','iscommits','isrollbacks')"
)

# dbspaces 容量 Top：chk_size 单位为页（按 2KB 页估算，页大小非 2K 时有偏差）。
GBASE_TBS_SQL = (
    "SELECT TRIM(d.name) AS name, "
    "ROUND(SUM(c.chk_size) * 2 / 1024.0, 1) AS total_mb "
    "FROM sysmaster:sysdbspaces d JOIN sysmaster:syschunks c "
    "  ON c.dbsnum = d.dbsnum "
    "GROUP BY d.name ORDER BY total_mb DESC LIMIT 10"
)

# ── DB2（SYSIBMADM / MON_GET_DATABASE 表函数；监控账号需 SYSMON 权限）──
# 列名经真实 DB2 LUW 实测：QPS 计数 ACT_RQSTS_TOTAL（语句活动总数）；
# TPS = TOTAL_APP_COMMITS + TOTAL_APP_ROLLBACKS。
DB2_STAT_SQL = (
    "SELECT 'act_rqsts' AS name, ACT_RQSTS_TOTAL AS value "
    "  FROM TABLE(MON_GET_DATABASE(-1)) "
    "UNION ALL SELECT 'commits', TOTAL_APP_COMMITS FROM TABLE(MON_GET_DATABASE(-1)) "
    "UNION ALL SELECT 'rollbacks', TOTAL_APP_ROLLBACKS FROM TABLE(MON_GET_DATABASE(-1))"
)

DB2_TBS_SQL = (
    "SELECT TBSP_NAME AS name, "
    "ROUND(TBSP_TOTAL_SIZE_KB / 1024.0, 1) AS total_mb, "
    "ROUND(TBSP_FREE_SIZE_KB / 1024.0, 1) AS free_mb "
    "FROM SYSIBMADM.TBSP_UTILIZATION "
    "ORDER BY TBSP_FREE_SIZE_KB ASC FETCH FIRST 10 ROWS ONLY"
)

# ── ClickHouse（system 表；JDBC 子进程通道）──
# system.events 累计计数：Query 作 QPS；InsertQuery 作写入 TPS
# （ClickHouse 无事务概念，TPS 即插入批次速率）。
CLICKHOUSE_STAT_SQL = (
    "SELECT event AS name, value FROM system.events "
    "WHERE event IN ('Query','SelectQuery','InsertQuery')"
)

# 库容量 Top（active parts 实际占用）。
CLICKHOUSE_TBS_SQL = (
    "SELECT database AS name, ROUND(SUM(bytes) / 1048576, 1) AS total_mb "
    "FROM system.parts WHERE active "
    "GROUP BY database ORDER BY total_mb DESC LIMIT 10"
)

# ── GBase 8s 复制 / 锁 ──
# 复制延迟：sysmaster:sysdri 仅暴露 HDR 状态列（type/state/name 等），
# 无「秒级延迟」列（onstat -g hdr 的 log page distance 无法用 SQL 取秒数）。
# 故 DR 激活且已配对（name 非空）视为已同步 → repl_lag_s=0；未配置 HDR 置 None。
GBASE_REPL_SQL = "SELECT type, state, name FROM sysmaster:sysdri"

# 锁等待：syslocks.waiter 非空的行即阻塞等待（多源确认列名），count 即等待会话数。
GBASE_LOCKS_SQL = (
    "SELECT count(*) AS n FROM sysmaster:syslocks WHERE waiter IS NOT NULL"
)

# ── DB2 复制 / 锁 ──
# HADR 备库回放延迟（毫秒）；未配置 HADR 时 MON_GET_HADR 返回空集 → 保持 None。
DB2_REPL_SQL = (
    "SELECT MAX(STANDBY_REPLAY_DELAY) AS lag_ms FROM TABLE(MON_GET_HADR(-1))"
)

# 锁等待：SYSIBMADM.LOCKWAITS 每行一个等待者（需 SYSMON/MON 权限，缺失则降级 None）。
DB2_LOCKS_SQL = "SELECT count(*) AS n FROM SYSIBMADM.LOCKWAITS"

# ── ClickHouse 复制 / 锁 ──
# 复制延迟：ReplicatedMergeTree 的 absolute_delay 单位为秒。
CLICKHOUSE_REPL_SQL = (
    "SELECT max(absolute_delay) AS lag_s FROM system.replicas"
)

# ClickHouse 无行级锁；以「未完成 mutation（卡住/阻塞的 schema 变更）」数作代理指标。
CLICKHOUSE_LOCKS_SQL = (
    "SELECT count(*) AS n FROM system.mutations WHERE NOT is_done"
)

SCREEN_SQLS = {
    'mysql': {'stat': MYSQL_STAT_SQL, 'tbs': MYSQL_TBS_SQL,
              'repl': MYSQL_REPL_SQL, 'repl8': MYSQL_REPL_SQL8},
    'pg': {'stat': PG_STAT_SQL, 'tbs': PG_TBS_SQL, 'repl': PG_REPL_SQL,
           'locks': PG_LOCKS_SQL},
    'oracle': {'stat': ORACLE_STAT_SQL, 'tbs': ORACLE_TBS_SQL, 'repl': ORACLE_REPL_SQL,
               'locks': ORACLE_LOCKS_SQL, 'identity': ORACLE_IDENTITY_SQL,
               'inst': ORACLE_INSTANCE_SQL, 'dest': ORACLE_DEST_SQL},
    'sqlserver': {'stat': SQLSERVER_STAT_SQL, 'tbs': SQLSERVER_TBS_SQL,
                  'locks': SQLSERVER_LOCKS_SQL},
    'dm': {'stat': DM_STAT_SQL, 'tbs': DM_TBS_SQL, 'repl': DM_REPL_SQL,
           'locks': DM_LOCKS_SQL},
    'gbase': {'stat': GBASE_STAT_SQL, 'tbs': GBASE_TBS_SQL,
              'repl': GBASE_REPL_SQL, 'locks': GBASE_LOCKS_SQL},
    'db2': {'stat': DB2_STAT_SQL, 'tbs': DB2_TBS_SQL,
            'repl': DB2_REPL_SQL, 'locks': DB2_LOCKS_SQL},
    'clickhouse': {'stat': CLICKHOUSE_STAT_SQL, 'tbs': CLICKHOUSE_TBS_SQL,
                   'repl': CLICKHOUSE_REPL_SQL, 'locks': CLICKHOUSE_LOCKS_SQL},
}


# ═══════════════════════════════════════════════════════════
#  数值解析小工具
# ═══════════════════════════════════════════════════════════

def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _sum_or_none(*vals):
    """相加；任一项缺失（None）返回 None，保证差值计算不误报为 0。"""
    if any(v is None for v in vals):
        return None
    return sum(_num(v) for v in vals)


def _find_key(row, *keywords):
    """在 row dict 的键名里模糊匹配（列名大小写/前缀随驱动不同）。"""
    for k in row or {}:
        kl = k.lower()
        if all(w in kl for w in keywords):
            return row[k]
    return None


def _scalar(rows, col=None):
    """取首行某列（或唯一列）的数值。"""
    if not rows:
        return None
    r = rows[0]
    if col:
        for k, v in r.items():
            if k.lower() == col.lower():
                return v
        return None
    vals = list(r.values())
    return vals[0] if vals else None


def _parse_dg_lag(value):
    """解析 Oracle v$dataguard_stats 的 '+00 00:00:12' / '+000 00:00:00.000'。"""
    try:
        s = str(value).strip()
        if not s:
            return None
        neg = s.startswith('-')
        s = s.lstrip('+-')
        days = 0
        if ' ' in s:
            d_part, s = s.split(' ', 1)
            days = int(d_part)
        parts = s.split(':')
        secs = days * 86400 + int(parts[0]) * 3600 + int(parts[1]) * 60
        if len(parts) > 2:
            secs += int(float(parts[2]))
        return -secs if neg else secs
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
#  下钻明细脱敏投影（连接/会话、慢查询逐行）
# ═══════════════════════════════════════════════════════════
# 明细已在 MonitorEngine 采集结果中（conn_data/slow_data['data']），此处仅做
# 截顶 + 值截断 + 剔除疑似凭据列，不引入新 SQL、不触碰数据库。
_DRILL_MAX_ROWS = 50       # 单实例下钻明细最多透出 50 行
_DRILL_VAL_MAX = 240       # 单行单元格值最大长度（超出截断，避免超大 SQL 文本撑爆接口）
_DRILL_SKIP_KEYS = ('password', 'pwd', 'passwd', 'token', 'secret', 'credential')


def _clip_val(v):
    """非字符串值原样返回；字符串/复合值超长截断并追加省略号。"""
    if isinstance(v, str):
        return v[:_DRILL_VAL_MAX] + '…' if len(v) > _DRILL_VAL_MAX else v
    if isinstance(v, (dict, list, tuple)):
        try:
            s = json.dumps(v, ensure_ascii=False)
        except Exception:
            s = str(v)
        return s[:_DRILL_VAL_MAX] + '…' if len(s) > _DRILL_VAL_MAX else s
    return v


def _sanitize_rows(data, limit=_DRILL_MAX_ROWS):
    """连接/会话/慢查询逐行 → 截顶 + 值截断 + 跳过疑似凭据列。

    返回 list[dict]：键为原列名（已过滤凭据列），值为截断后的标量/字符串。
    行结构随 db_type 不同（列名各异），前端按列动态渲染。"""
    if not data:
        return []
    rows = []
    for r in data[:limit]:
        if not isinstance(r, dict):
            rows.append({'_value': _clip_val(r)})
            continue
        clean = {}
        for k, v in r.items():
            kl = (str(k) or '').lower()
            if any(w in kl for w in _DRILL_SKIP_KEYS):
                continue
            clean[str(k)] = _clip_val(v)
        rows.append(clean)
    return rows


# ═══════════════════════════════════════════════════════════
#  单实例采集：额外指标（QPS/TPS/缓存/容量/复制/锁）
# ═══════════════════════════════════════════════════════════

def collect_extra(engine, instance_id, db_type):
    """采集 engine 之外的增量/容量/复制指标，返回 (counters, extras)。

    counters: 累计量（供差值计算 QPS/TPS/命中率），结构随 family
    extras:   即时指标 {'tbs': [...], 'repl_lag_s': int|None, 'lock_waits': int|None}
    任一 SQL 失败对应项为 None，不影响其它项。
    """
    fam = db_family(db_type)
    sqls = SCREEN_SQLS.get(fam) if fam else None
    counters, extras = {}, {'tbs': None, 'repl_lag_s': None, 'lock_waits': None,
                            # RAC / ADG 身份与配对（仅 oracle family 填充，其余保持 None）
                            'role': None, 'db_unique_name': None, 'db_name': None,
                            'open_mode': None, 'instance_name': None,
                            'instance_number': None, 'dests': None,
                            'transport_lag_s': None, 'apply_lag_s': None}
    if not sqls:
        return counters, extras

    def q(key):
        try:
            return engine.run_query(instance_id, sqls[key])
        except Exception as e:
            # stat（性能计数）查询失败不再静默：分类为权限提示码，
            # 由大屏前端转成可读文案（SQL Server 缺 VIEW SERVER STATE 最常见）。
            if key == 'stat' and not extras.get('stat_hint'):
                low = str(e).lower()
                if 'view server state' in low or (fam == 'sqlserver' and (
                        'permission' in low or 'denied' in low or 'privilege' in low)):
                    extras['stat_hint'] = 'view_server_state'
                else:
                    extras['stat_hint'] = 'stat_failed'
            return None

    if fam == 'mysql':
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'variable_name')
            v = _find_key(r, 'value')
            if k is not None:
                st[str(k).lower()] = _num(v)
        counters = {
            'questions': st.get('questions'),
            'com_commit': st.get('com_commit'),
            'com_rollback': st.get('com_rollback'),
            'buf_reads': st.get('innodb_buffer_pool_read_requests'),
            'buf_disk': st.get('innodb_buffer_pool_reads'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': r.get('name'), 'total_mb': _num(r.get('total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        repl = q('repl8') or q('repl')
        if repl:
            lag = _find_key(repl[0], 'seconds_behind')
            extras['repl_lag_s'] = int(_num(lag, -1)) if lag is not None else None

    elif fam == 'pg':
        rows = q('stat')
        if rows:
            r = rows[0]
            counters = {
                'xact_commit': _num(_find_key(r, 'xact_commit')),
                'xact_rollback': _num(_find_key(r, 'xact_rollback')),
                'blks_hit': _num(_find_key(r, 'blks_hit')),
                'blks_read': _num(_find_key(r, 'blks_read')),
            }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': r.get('name'), 'total_mb': _num(r.get('total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        repl = q('repl')
        if repl:
            # 取最大延迟（多从库时取最差值）
            extras['repl_lag_s'] = max(int(_num(r.get('lag_s'))) for r in repl) if repl else None
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'oracle':
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': st.get('execute count'),
            'commits': st.get('user commits'),
            'rollbacks': st.get('user rollbacks'),
            'buf_reads': (st.get('consistent gets') or 0) + (st.get('db block gets') or 0),
            'buf_disk': st.get('physical reads'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': r.get('name'), 'total_mb': _num(r.get('total_mb')),
                 'free_mb': _num(r.get('free_mb')),
                 'free_pct': (round(_num(r.get('free_mb')) / _num(r.get('total_mb')) * 100, 1)
                              if _num(r.get('total_mb')) > 0 else None)}
                for r in tbs]
        repl = q('repl')
        if repl:
            # transport lag / apply lag 分开保留（ADG 传输线分开标注），repl_lag_s 取最差
            for r in repl:
                nm = str(_find_key(r, 'name') or '').lower()
                lag = _parse_dg_lag(_find_key(r, 'value'))
                if lag is None:
                    continue
                if 'transport' in nm:
                    extras['transport_lag_s'] = lag
                elif 'apply' in nm:
                    extras['apply_lag_s'] = lag
            lags = [l for l in (extras['transport_lag_s'], extras['apply_lag_s'])
                    if l is not None]
            if lags:
                extras['repl_lag_s'] = max(lags)
        # RAC / ADG 身份（低权限/单机/查询失败均优雅降级为 None，不影响其它指标）
        ident = q('identity')
        if ident:
            m = {str(k).lower(): v for k, v in (ident[0] or {}).items()}
            # 精确小写列名取值（_find_key 模糊匹配会被 db_unique_name 含 'name' 干扰）
            extras['role'] = (str(m.get('database_role') or '').strip() or None)
            extras['db_unique_name'] = (str(m.get('db_unique_name') or '').strip() or None)
            extras['db_name'] = (str(m.get('name') or '').strip() or None)
            extras['open_mode'] = (str(m.get('open_mode') or '').strip() or None)
        inst_rows = q('inst')
        if inst_rows:
            m2 = {str(k).lower(): v for k, v in (inst_rows[0] or {}).items()}
            inum = _num(m2.get('instance_number'), 0)
            extras['instance_name'] = (str(m2.get('instance_name') or '').strip() or None)
            extras['instance_number'] = int(inum) if inum else None
        dest_rows = q('dest')
        if dest_rows:
            dests = []
            for r in dest_rows:
                m3 = {str(k).lower(): v for k, v in (r or {}).items()}
                dn = str(m3.get('db_unique_name') or '').strip()
                if dn and dn not in dests:
                    dests.append(dn)
            if dests:
                extras['dests'] = dests
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'dm':
        # DM8 达梦：v$sysstat 专属指标名（sql executed count 等），计数键沿用
        # oracle 命名（_rate 组合直接复用）；缓存命中暂缺（无 hit_base 指标）。
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': st.get('sql executed count'),
            'commits': st.get('transaction commit count'),
            'rollbacks': st.get('transaction rollback count'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': r.get('name'), 'total_mb': _num(r.get('total_mb')),
                 'free_mb': _num(r.get('free_mb')),
                 'free_pct': (round(_num(r.get('free_mb')) / _num(r.get('total_mb')) * 100, 1)
                              if _num(r.get('total_mb')) > 0 else None)}
                for r in tbs]
        repl = q('repl')
        if repl:
            lag = _scalar(repl, 'lag_s')
            # 单机库 DATEDIFF 对 NULL → 空结果/空串，视为无复制（None）
            if lag is not None and str(lag).strip() != '':
                extras['repl_lag_s'] = int(_num(lag))
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'sqlserver':
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'counter_name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'cntr_value'))
        counters = {
            'batch_req': st.get('batch requests/sec'),
            'trans': st.get('transactions/sec'),
            'hit': st.get('buffer cache hit ratio'),
            'hit_base': st.get('buffer cache hit ratio base'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': r.get('name'), 'total_mb': _num(r.get('total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'gbase':
        # GBase 8s（sysmaster）：isreads/iswrites 相加作 QPS 近似，
        # iscommits/isrollbacks 相加作 TPS；容量按 dbspace 页数估算（2KB 页）。
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': _sum_or_none(st.get('isreads'), st.get('iswrites')),
            'commits': st.get('iscommits'),
            'rollbacks': st.get('isrollbacks'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': r.get('name'), 'total_mb': _num(r.get('total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        repl = q('repl')
        if repl:
            # HDR 激活且已配对（name 非空）视为同步 → 0 秒；其余（含未配置 HDR）留 None
            st = str(_find_key(repl[0], 'state') or '').strip().lower()
            paired = bool(str(_find_key(repl[0], 'name') or '').strip())
            if st == 'on' and paired:
                extras['repl_lag_s'] = 0
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'db2':
        # DB2（MON_GET_DATABASE 累计计数，列名经真实实例实测）：
        # ACT_RQSTS_TOTAL 作 QPS；TOTAL_APP_COMMITS/ROLLBACKS 作 TPS；
        # 表空间自由空间来自 TBSP_UTILIZATION。
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': st.get('act_rqsts'),
            'commits': st.get('commits'),
            'rollbacks': st.get('rollbacks'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': r.get('name'), 'total_mb': _num(r.get('total_mb')),
                 'free_mb': _num(r.get('free_mb')),
                 'free_pct': (round(_num(r.get('free_mb')) / _num(r.get('total_mb')) * 100, 1)
                              if _num(r.get('total_mb')) > 0 else None)}
                for r in tbs]
        repl = q('repl')
        if repl:
            # STANDBY_REPLAY_DELAY 为毫秒；未配置 HADR 返回空集/NULL → 保持 None
            lag_ms = _scalar(repl, 'lag_ms')
            if lag_ms is not None and str(lag_ms).strip() != '':
                extras['repl_lag_s'] = int(_num(lag_ms) / 1000)
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'clickhouse':
        # ClickHouse（system.events 累计计数）：Query 作 QPS，InsertQuery 作写入 TPS。
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': st.get('query'),
            'commits': st.get('insertquery'),
            'rollbacks': 0,
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': r.get('name'), 'total_mb': _num(r.get('total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        repl = q('repl')
        if repl:
            # absolute_delay 单位秒；无复制表（system.replicas 空集）→ None
            lag = _scalar(repl, 'lag_s')
            if lag is not None and str(lag).strip() != '':
                extras['repl_lag_s'] = int(_num(lag))
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    return counters, extras


# ═══════════════════════════════════════════════════════════
#  大屏采样器：后台线程 + 差值计算 + 内存快照
# ═══════════════════════════════════════════════════════════

class ScreenCollector:
    """大屏采集器：周期采样所有实例 → 内存快照 → /api/dashboard/overview 直接读。"""

    INTERVAL = 15          # 采样间隔（秒）
    HISTORY = 60           # 每实例保留采样点数（15s × 60 ≈ 15 分钟）
    TREND_POINTS = 60      # 全库 QPS 趋势点数
    MAX_WORKERS = 8        # 单轮并发采集线程数

    # 阈值（与设计文档一致）
    WARN_CONN_UTIL, CRIT_CONN_UTIL = 80.0, 95.0
    WARN_TBS_FREE, CRIT_TBS_FREE = 30.0, 15.0
    WARN_REPL_LAG, CRIT_REPL_LAG = 30, 300
    WARN_LOCKS = 10

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._snap = {}      # iid → 快照 dict
        self._prev = {}      # iid → {'ts': float, 'counters': {...}}
        self._spark = {}     # iid → deque(qps)
        self._trend = deque(maxlen=self.TREND_POINTS)  # {'ts','qps','tps'}
        self._load_thresholds()

    # ── 阈值配置（独立于 dbc_config.json；运行时可调，持久化到 monitor_thresholds.json）──
    # dir: up=越大越糟(warn<crit)  down=越小越糟(crit<warn)  lock=仅告警阈值
    THRESHOLD_MAP = [
        ('warn_conn_util', 'crit_conn_util', 'up', (0, 100)),
        ('warn_tbs_free', 'crit_tbs_free', 'down', (0, 100)),
        ('warn_repl_lag', 'crit_repl_lag', 'up', (0, 86400)),
        ('warn_locks', None, 'lock', (0, 100000)),
    ]

    @staticmethod
    def _threshold_path():
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'monitor_thresholds.json')

    @staticmethod
    def _num(v, default):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _current_threshold_dict(self):
        d = {}
        for wk, ck, _, _ in self.THRESHOLD_MAP:
            d[wk] = getattr(self, wk.upper())
            if ck is not None:
                d[ck] = getattr(self, ck.upper())
        return d

    def _load_thresholds(self):
        """读取 monitor_thresholds.json 覆盖默认阈值；文件缺失/损坏时回退类属性默认值。
        首次运行（文件不存在）写入默认阈值文件。"""
        p = self._threshold_path()
        try:
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    for wk, ck, _, _ in self.THRESHOLD_MAP:
                        if wk in d: setattr(self, wk.upper(), self._num(d.get(wk), getattr(self, wk.upper())))
                        if ck is not None and ck in d: setattr(self, ck.upper(), self._num(d.get(ck), getattr(self, ck.upper())))
        except Exception:
            pass  # 文件异常 → 回退类属性默认值
        if not os.path.exists(p):
            try:
                self._save_thresholds(self._current_threshold_dict())
            except Exception:
                pass

    def _save_thresholds(self, d):
        p = self._threshold_path()
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def get_thresholds(self):
        """返回当前生效阈值（供 API GET）。"""
        out = self._current_threshold_dict()
        out['warn_locks'] = int(out['warn_locks'])
        return out

    def set_thresholds(self, data):
        """校验并应用新阈值，持久化到 JSON；返回生效后的阈值 dict。
        校验失败抛出 ValueError（含中文说明）。"""
        cur = self._current_threshold_dict()
        new = {}
        for wk, ck, _, _ in self.THRESHOLD_MAP:
            new[wk] = self._num(data.get(wk, cur[wk]), cur[wk])
            if ck is not None:
                new[ck] = self._num(data.get(ck, cur[ck]), cur[ck])
        errs = []
        if not (0 < new['warn_conn_util'] < new['crit_conn_util'] <= 100):
            errs.append('连接利用率：须 0 < 告警阈值 < 严重阈值 ≤ 100')
        if not (0 <= new['crit_tbs_free'] < new['warn_tbs_free'] <= 100):
            errs.append('表空间剩余：须 0 ≤ 严重阈值 < 告警阈值 ≤ 100')
        if not (0 <= new['warn_repl_lag'] < new['crit_repl_lag']):
            errs.append('复制延迟：须 0 ≤ 告警阈值 < 严重阈值')
        if new['warn_locks'] < 0:
            errs.append('锁等待：告警阈值须 ≥ 0')
        if errs:
            raise ValueError('；'.join(errs))
        for wk, ck, _, _ in self.THRESHOLD_MAP:
            setattr(self, wk.upper(), new[wk])
            if ck is not None:
                setattr(self, ck.upper(), new[ck])
        self._save_thresholds(self._current_threshold_dict())
        return self.get_thresholds()

    # ── 启停 ──
    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name='screen-collector')
            self._thread.start()
            print('[screen] 大屏指标采样器已启动（间隔 %ds）' % self.INTERVAL, flush=True)

    def stop(self):
        with self._lock:
            self._running = False

    @property
    def running(self):
        return self._running

    def _loop(self):
        # 启动先采一轮（首采样只有 counters，无速率），再进入周期循环
        while True:
            t0 = time.time()
            try:
                self._round()
            except Exception as e:
                print('[screen] 采集轮失败: %s' % e, flush=True)
            dt = self.INTERVAL - (time.time() - t0)
            end = time.time() + max(dt, 1)
            while time.time() < end:
                if not self._running:
                    return
                time.sleep(0.5)

    # ── 单轮采集 ──
    def _round(self):
        from modules.pro.instance_manager import get_instance_manager
        from modules.monitor.engine import get_monitor_engine

        engine = get_monitor_engine()
        im = get_instance_manager()
        try:
            instances = [i for i in im.get_all_instances_decrypted()
                         if i.get('enabled', True) and i.get('host')]
        except Exception as e:
            print('[screen] 实例列表获取失败: %s' % e, flush=True)
            return

        conn_data = {}
        slow_data = {}
        try:
            conn_data = engine.get_connections()
        except Exception:
            pass
        try:
            slow_data = engine.get_slow_queries()
        except Exception:
            pass

        cur_ids = set(i['id'] for i in instances)

        # ── 预置 pending 占位 + 清理已删实例 ──
        # 让拓扑与 KPI 总数在首轮采集完成前就立即有结构可渲染（打开大屏秒出基础图表）；
        # 各实例采集完成即增量覆盖为真实快照，前端随轮询渐进填充。
        with self._lock:
            for stale_iid in [k for k in self._snap if k not in cur_ids]:
                self._snap.pop(stale_iid, None)
                self._prev.pop(stale_iid, None)
                self._spark.pop(stale_iid, None)
            ts0 = time.time()
            for inst in instances:
                iid = inst['id']
                if iid not in self._snap:
                    self._snap[iid] = self._pending_snap(inst, ts0)

        # ── 增量采集：每个实例一完成即写入快照（先快的后慢的，随轮询渐进显现）──
        def do_one(inst):
            return self._collect_one(engine, inst, conn_data, slow_data)

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = {pool.submit(do_one, inst): inst for inst in instances}
            for fut in as_completed(futures):
                inst = futures[fut]
                try:
                    snap = fut.result()
                except Exception as e:
                    snap = self._pending_snap(inst, time.time(), err='采集线程异常: %s' % e)
                self._commit_snap(snap)
                # 历史回放：白名单投影后异步落库（pending 占位无真实指标，跳过；不阻塞采集热路径）
                if snap.get('status') != 'pending':
                    try:
                        history_store.get_history_store().write_snap(snap)
                    except Exception as e:
                        print('[screen] history write failed: %s' % e, flush=True)

        # ── 本轮趋势点（汇总已提交快照）──
        ts = time.time()
        with self._lock:
            total_qps = sum((s.get('qps') or 0) for s in self._snap.values())
            total_tps = sum((s.get('tps') or 0) for s in self._snap.values())
            self._trend.append({'ts': ts, 'qps': round(total_qps, 1), 'tps': round(total_tps, 1)})

        # 告警状态机评估（锁外）：状态迁移时经通知配置发邮件/IM（异步发送）
        try:
            from modules.monitor.alert_notify import get_alert_tracker
            with self._lock:
                snaps = [dict(s) for s in self._snap.values()]
            get_alert_tracker().update(snaps)
        except Exception as e:
            print('[alert] 告警评估失败: %s' % e, flush=True)

    @staticmethod
    def _rate(prev_c, cur_c, dt, kind):
        """按 kind 计算每秒速率；任一计数缺失返回 None。

        组合之间是"备选"关系（不同库的计数名），组合内部是"相加"关系
        （如 MySQL TPS = Com_commit + Com_rollback）。
        pg 家族无语句级计数（除非装 pg_stat_statements），以事务率
        （xact_commit + xact_rollback）作为 QPS 近似与 TPS。
        """
        if kind == 'qps':
            combos = (('questions',), ('exec_count',), ('batch_req',),
                      ('xact_commit', 'xact_rollback'))
        else:
            combos = (('com_commit', 'com_rollback'), ('commits', 'rollbacks'),
                      ('trans',), ('xact_commit', 'xact_rollback'))
        for combo in combos:
            if all(prev_c.get(k) is not None for k in combo):
                dv = sum((cur_c.get(k) or 0) - (prev_c.get(k) or 0) for k in combo)
                return max(dv / dt, 0.0)
        return None

    def _collect_one(self, engine, inst, conn_data, slow_data):
        iid = inst['id']
        db_type = (inst.get('db_type') or '').lower()
        snap = {
            'id': iid,  # 告警状态机按 id 跟踪实例（update() 无 id 的快照会被跳过）
            'name': inst.get('name', iid),
            'db_type': db_type,
            'group': inst.get('group') or 'default',
            'host': inst.get('host', ''),
            'port': inst.get('port', ''),
            'label': "%s (%s:%s)" % (inst.get('name', iid), inst.get('host', '?'), inst.get('port', '?')),
            'ts': time.time(),
            'conn': None, 'slowq': 0, 'qps': None, 'tps': None,
            'cache_hit_pct': None, 'lock_waits': None, 'repl_lag_s': None,
            'role': None, 'db_unique_name': None, 'db_name': None, 'open_mode': None,
            'instance_name': None, 'instance_number': None, 'dests': None,
            'transport_lag_s': None, 'apply_lag_s': None,
            'tbs': None, 'err': None, 'status': 'ok', 'counters': {},
            'stat_hint': None,
            # 下钻明细：连接/会话 与 慢查询 逐行（截顶脱敏，详见 _sanitize_rows）
            'conn_rows': [], 'slow_rows': [],
            # SSH 跳板信息：用于大屏主机节点展示"经由此网关/跳板可达该实例"
            'ssh': {
                'enabled': bool(inst.get('ssh_enabled')),
                'host': inst.get('ssh_host') or '',
                'port': inst.get('ssh_port') or 22,
                'user': inst.get('ssh_user') or '',
            },
        }

        # 0) MongoDB / Redis 原生驱动通道：SQL 引擎（DBAPI/JDBC）不覆盖的
        #    NoSQL 类型在此直连采集，连通性与指标一并拿到后提前返回。
        if db_type in NATIVE_DB_TYPES:
            nd = collect_native(inst)
            if not nd.get('alive'):
                snap['err'] = nd.get('err')
                snap['status'] = 'down'
                return snap
            snap['conn'] = nd.get('conn')
            snap['counters'] = nd.get('counters') or {}
            snap['tbs'] = nd.get('tbs')
            snap['repl_lag_s'] = nd.get('repl_lag_s')
            snap['lock_waits'] = nd.get('lock_waits')
            snap['slowq'] = nd.get('slowq') or 0
            snap['status'] = self._derive_status(snap)
            return snap

        # 1) 连接与会话（复用 MonitorEngine 采集结果）
        cd = conn_data.get(iid)
        if cd and cd.get('error'):
            # 「不支持的类型」= 该库无连接数采集模板（db2/mongodb/redis 等），
            # 属能力缺失而非实例故障：不判 down、不作为 err 标红，
            # conn 留空（卡片连接显示 –，第 4 行回落显示 host:port）。
            if '不支持的类型' in str(cd['error']):
                snap['status'] = 'unsupported'
            else:
                snap['err'] = cd['error']
                snap['status'] = 'down'
        elif cd:
            snap['conn'] = {
                'total': cd.get('total', 0), 'max_conn': cd.get('max_conn', 0),
                'usage_pct': cd.get('usage_pct', 0),
                'active': (cd.get('connections') or {}).get('active', 0),
                'idle': (cd.get('connections') or {}).get('idle', 0),
                'blocked': (cd.get('connections') or {}).get('blocked', 0),
            }
        else:
            # 引擎本轮尚未采到该实例（启动竞态/新加实例/引擎未就绪）：
            # 不能凭空判 ok（假绿误导），也不能判 down（误触发告警），
            # 保持 pending（连接中），等下一轮真实采集结果再落定状态；
            # 不设 err，避免卡片右上角出现「有错误」角标。
            snap['status'] = 'pending'
        sd = slow_data.get(iid)
        snap['slowq'] = len(sd.get('data') or []) if sd and not sd.get('error') else 0
        # 下钻明细：连接/会话 与 慢查询 逐行（复用 engine 本轮采集结果，不引入新 SQL）
        snap['conn_rows'] = _sanitize_rows(cd.get('data') if (cd and not cd.get('error')) else None)
        snap['slow_rows'] = _sanitize_rows(sd.get('data') if (sd and not sd.get('error')) else None)
        if sd and sd.get('error') and not snap['err']:
            # 慢查询模板缺失同属能力缺失，不得作为 err 标红；
            # 但真实 SQL 失败仍要判 down（即便连接采集不支持、慢查失败是实打实的探活失败）。
            if '不支持的类型' not in str(sd['error']):
                snap['err'] = sd['error']
                if snap['status'] == 'unsupported':
                    snap['status'] = 'down'

        # 2) 增量与容量指标（best effort）
        try:
            counters, extras = collect_extra(engine, iid, db_type)
            snap['counters'] = counters
            snap['tbs'] = extras.get('tbs')
            snap['repl_lag_s'] = extras.get('repl_lag_s')
            snap['lock_waits'] = extras.get('lock_waits')
            # RAC / ADG 身份与配对（oracle 专属；None 不覆盖保持快照结构稳定）
            for k in ('role', 'db_unique_name', 'db_name', 'open_mode',
                      'instance_name', 'instance_number', 'dests',
                      'transport_lag_s', 'apply_lag_s'):
                if extras.get(k) is not None:
                    snap[k] = extras[k]
            # 性能计数一条都没拿到时，把失败原因（权限提示码）透传给大屏
            if extras.get('stat_hint') and not any(
                    v is not None for v in (counters or {}).values()):
                snap['stat_hint'] = extras['stat_hint']
        except Exception as e:
            print('[screen] extra 采集失败 %s: %s' % (snap['label'], e), flush=True)

        # 3) 状态判定（pending = 本轮未探活成功，不参与阈值派生，留给下一轮落定）
        if snap['status'] not in ('down', 'pending'):
            snap['status'] = self._derive_status(snap)
        return snap

    def _derive_status(self, s):
        conn_util = (s.get('conn') or {}).get('usage_pct') or 0
        worst_tbs = None
        for t in (s.get('tbs') or []):
            fp = t.get('free_pct')
            if fp is not None and (worst_tbs is None or fp < worst_tbs):
                worst_tbs = fp
        lag = s.get('repl_lag_s')
        locks = s.get('lock_waits') or 0
        if (conn_util >= self.CRIT_CONN_UTIL or
                (worst_tbs is not None and worst_tbs <= self.CRIT_TBS_FREE) or
                (lag is not None and lag >= self.CRIT_REPL_LAG)):
            return 'crit'
        if (conn_util >= self.WARN_CONN_UTIL or locks >= self.WARN_LOCKS or
                (worst_tbs is not None and worst_tbs <= self.WARN_TBS_FREE) or
                (lag is not None and lag >= self.WARN_REPL_LAG)):
            return 'warn'
        return 'ok'

    def _pending_snap(self, inst, ts, err=None):
        """预置占位快照：首轮采集完成前就给出实例结构与 pending 状态，
        让拓扑与 KPI 总数立即有内容可渲染（打开大屏秒出基础图表）。"""
        iid = inst['id']
        db_type = (inst.get('db_type') or '').lower()
        return {
            'id': iid,
            'name': inst.get('name', iid),
            'db_type': db_type,
            'group': inst.get('group') or 'default',
            'host': inst.get('host', ''),
            'port': inst.get('port', ''),
            'label': "%s (%s:%s)" % (inst.get('name', iid), inst.get('host', '?'), inst.get('port', '?')),
            'ts': ts,
            'conn': None, 'slowq': 0, 'qps': None, 'tps': None,
            'cache_hit_pct': None, 'lock_waits': None, 'repl_lag_s': None,
            'role': None, 'db_unique_name': None, 'db_name': None, 'open_mode': None,
            'instance_name': None, 'instance_number': None, 'dests': None,
            'transport_lag_s': None, 'apply_lag_s': None,
            'tbs': None, 'err': err, 'status': 'pending', 'counters': {},
            'stat_hint': None, 'pending': True,
            'conn_rows': [], 'slow_rows': [],
            'ssh': {
                'enabled': bool(inst.get('ssh_enabled')),
                'host': inst.get('ssh_host') or '',
                'port': inst.get('ssh_port') or 22,
                'user': inst.get('ssh_user') or '',
            },
        }

    def _commit_snap(self, snap):
        """单实例快照增量提交：计算速率（有 prev 时）+ 写入 _snap/_prev/_spark。
        由 _round 的 as_completed 循环逐条调用，做到「谁先采完谁先显示」。"""
        if not snap or 'id' not in snap:
            return
        iid = snap['id']
        ts = snap.get('ts') or time.time()
        with self._lock:
            prev = self._prev.get(iid)
            if prev and snap.get('counters'):
                dt = max(ts - prev['ts'], 0.001)
                qps = self._rate(prev['counters'], snap['counters'], dt, 'qps')
                tps = self._rate(prev['counters'], snap['counters'], dt, 'tps')
                snap['qps'] = round(qps, 1) if qps is not None else None
                snap['tps'] = round(tps, 1) if tps is not None else None
                if snap.get('counters', {}).get('hit_base'):
                    h = snap['counters'].get('hit') or 0
                    b = snap['counters']['hit_base']
                    snap['cache_hit_pct'] = round(h / b * 100, 1) if b else None
            self._prev[iid] = {'ts': ts, 'counters': dict(snap.get('counters') or {})}
            self._snap[iid] = {k: v for k, v in snap.items() if k != 'counters'}
            if snap.get('qps') is not None:
                sp = self._spark.setdefault(iid, deque(maxlen=self.HISTORY))
                sp.append(snap['qps'])

    # ── 数据读取 ──
    def get_snapshot(self):
        with self._lock:
            out = {}
            for iid, s in self._snap.items():
                d = dict(s)
                d['spark'] = list(self._spark.get(iid) or [])
                out[iid] = d
            return out

    def get_trend(self):
        with self._lock:
            return list(self._trend)


# ═══════════════════════════════════════════════════════════
#  聚合：/api/dashboard/overview 的数据装配
# ═══════════════════════════════════════════════════════════

def _topn(items, key, n=10):
    return sorted([i for i in items if i.get(key) is not None],
                  key=lambda x: x[key], reverse=True)[:n]


def _build_nodes(snap):
    """从快照装配拓扑节点（含 tbs 明细，供 TopN 表空间使用）。"""
    nodes = []
    for iid, s in snap.items():
        worst_tbs = None
        for t in (s.get('tbs') or []):
            fp = t.get('free_pct')
            if fp is not None and (worst_tbs is None or fp < worst_tbs):
                worst_tbs = fp
        nodes.append({
            'id': iid, 'name': s['name'], 'db_type': s['db_type'],
            'group': s['group'], 'host': s['host'], 'port': s['port'],
            'label': s['label'], 'status': s['status'], 'err': s.get('err'),
            'conn': s.get('conn'), 'slowq': s.get('slowq', 0),
            'qps': s.get('qps'), 'tps': s.get('tps'),
            'cache_hit_pct': s.get('cache_hit_pct'),
            'lock_waits': s.get('lock_waits'), 'repl_lag_s': s.get('repl_lag_s'),
            'role': s.get('role'), 'db_unique_name': s.get('db_unique_name'),
            'db_name': s.get('db_name'), 'open_mode': s.get('open_mode'),
            'instance_name': s.get('instance_name'), 'instance_number': s.get('instance_number'),
            'dests': s.get('dests'),
            'transport_lag_s': s.get('transport_lag_s'), 'apply_lag_s': s.get('apply_lag_s'),
            'tbs': s.get('tbs'), 'tbs_free_pct': worst_tbs,
            'spark': s.get('spark') or [],
            'stat_hint': s.get('stat_hint'),
            'ssh': s.get('ssh'),
        })
    return nodes


def assemble_overview(nodes, collector, trend=None):
    """把节点列表装配成大屏一次请求所需的全部数据（KPI/分组/TopN/告警/趋势）。

    实时（build_overview）与历史回放（build_history_overview）共用此函数，
    区别在于 nodes 的来源与 trend 的提供方式。"""
    ts = time.time()
    trend = trend if trend is not None else (collector.get_trend() if hasattr(collector, 'get_trend') else [])

    total = len(nodes)
    online = sum(1 for n in nodes if n['status'] in ('ok', 'warn', 'crit', 'unsupported'))
    pending = sum(1 for n in nodes if n['status'] == 'pending')
    warn = sum(1 for n in nodes if n['status'] == 'warn')
    crit = sum(1 for n in nodes if n['status'] == 'crit')
    down = sum(1 for n in nodes if n['status'] == 'down')
    kpis = {
        'total': total, 'online': online, 'pending': pending,
        'warn': warn, 'crit': crit, 'down': down,
        'total_conn': sum((n.get('conn') or {}).get('total') or 0 for n in nodes),
        'total_qps': round(sum(n['qps'] or 0 for n in nodes), 1),
        'total_tps': round(sum(n['tps'] or 0 for n in nodes), 1),
        'probe_running': collector.running,
    }

    groups = {}
    for n in nodes:
        g = groups.setdefault(n['group'], {'name': n['group'], 'total': 0,
                                           'ok': 0, 'warn': 0, 'crit': 0, 'down': 0})
        g['total'] += 1
        g[n['status']] = g.get(n['status'], 0) + 1

    conn_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                  'value': (n.get('conn') or {}).get('usage_pct') or 0,
                  'detail': '%s/%s' % ((n.get('conn') or {}).get('total', 0),
                                       (n.get('conn') or {}).get('max_conn', 0))}
                 for n in nodes if n.get('conn')]
    tbs_list = []
    tbs_used_list = []
    for n in nodes:
        for t in (n.get('tbs') or []):
            fp = t.get('free_pct')
            if fp is not None:
                tbs_list.append({'id': n['id'], 'name': '%s · %s' % (n['name'], t['name']),
                                 'db_type': n['db_type'], 'value': fp,
                                 'total_mb': t.get('total_mb'), 'unit': '%', 'kind': 'free'})
            elif t.get('total_mb') is not None:
                # MySQL/PG/SQLServer/GBase/ClickHouse 等只能采到「已用大小」，
                # 无 free 列 → free_pct 恒为 None；退而求其次按已用 MB 排名，
                # 让表空间图对这类库也有数据（标题与单位随 kind 自适应）。
                tbs_used_list.append({'id': n['id'], 'name': '%s · %s' % (n['name'], t['name']),
                                      'db_type': n['db_type'], 'value': t['total_mb'],
                                      'total_mb': t.get('total_mb'), 'unit': 'MB', 'kind': 'used'})
    tbs_worst = sorted(tbs_list, key=lambda x: x['value'])[:10]
    tbs_used = sorted(tbs_used_list, key=lambda x: x['value'], reverse=True)[:10]
    repl_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                  'value': n.get('repl_lag_s')} for n in nodes if n.get('repl_lag_s') is not None]
    lock_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                  'value': n.get('lock_waits')} for n in nodes if n.get('lock_waits') is not None]
    # 连接数 / 活跃会话 TopN（连接数为主排序，会话数按实例名对齐展示）
    conn_total_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                        'value': (n.get('conn') or {}).get('total') or 0}
                       for n in nodes if n.get('conn')]
    sess_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                  'value': (n.get('conn') or {}).get('active') or 0}
                 for n in nodes if n.get('conn')]

    topn = {
        'qps': _topn([{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                       'value': n['qps']} for n in nodes], 'value'),
        'conn_util': _topn(conn_list, 'value'),
        'conn_total': _topn(conn_total_list, 'value'),
        'sess_active': _topn(sess_list, 'value'),
        'slowq': _topn([{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                         'value': n['slowq']} for n in nodes], 'value'),
        'tbs_worst': tbs_worst,
        'tbs_used': tbs_used,
        'repl_lag': _topn(repl_list, 'value'),
        'lock_wait': _topn(lock_list, 'value'),
    }

    alerts = []
    for n in nodes:
        if n['status'] == 'down':
            alerts.append({'level': 'crit', 'id': n['id'], 'name': n['name'],
                           'msg': '实例探活失败' + (('：' + n['err']) if n.get('err') else ''),
                           'ts': n.get('ts') or ts})
            continue
        cu = (n.get('conn') or {}).get('usage_pct') or 0
        if n.get('tbs_free_pct') is not None and n['tbs_free_pct'] <= collector.CRIT_TBS_FREE:
            alerts.append({'level': 'crit', 'id': n['id'], 'name': n['name'],
                           'msg': '表空间剩余 %.1f%%' % n['tbs_free_pct'], 'ts': ts})
        elif n.get('tbs_free_pct') is not None and n['tbs_free_pct'] <= collector.WARN_TBS_FREE:
            alerts.append({'level': 'warn', 'id': n['id'], 'name': n['name'],
                           'msg': '表空间剩余 %.1f%%' % n['tbs_free_pct'], 'ts': ts})
        if cu >= collector.CRIT_CONN_UTIL:
            alerts.append({'level': 'crit', 'id': n['id'], 'name': n['name'],
                           'msg': '连接利用率 %.0f%%' % cu, 'ts': ts})
        elif cu >= collector.WARN_CONN_UTIL:
            alerts.append({'level': 'warn', 'id': n['id'], 'name': n['name'],
                           'msg': '连接利用率 %.0f%%' % cu, 'ts': ts})
        lag = n.get('repl_lag_s')
        if lag is not None and lag >= collector.WARN_REPL_LAG:
            alerts.append({'level': 'crit' if lag >= collector.CRIT_REPL_LAG else 'warn',
                           'id': n['id'], 'name': n['name'],
                           'msg': '复制延迟 %ds' % lag, 'ts': ts})
        if (n.get('lock_waits') or 0) >= collector.WARN_LOCKS:
            alerts.append({'level': 'warn', 'id': n['id'], 'name': n['name'],
                           'msg': '锁等待 %d' % n['lock_waits'], 'ts': ts})
    lvl_rank = {'crit': 0, 'warn': 1}
    alerts.sort(key=lambda a: (lvl_rank.get(a['level'], 9), -a['ts']))

    return {
        'ok': True, 'ts': ts,
        'kpis': kpis,
        'groups': list(groups.values()),
        'topn': topn,
        'trend': [{'ts': t['ts'], 'qps': t['qps']} for t in trend],
        'alerts': alerts[:50],
        'nodes': nodes,
    }


def build_overview(collector):
    """把采样器快照装配成大屏一次请求所需的全部数据（实时）。"""
    snap = collector.get_snapshot()
    nodes = _build_nodes(snap)
    return assemble_overview(nodes, collector)


def _node_from_history_sample(s):
    """把一条历史样本还原为与实时节点同构的 node dict。

    注意：历史库仅存不透明 iid，未持久化实例名/地址；name/label/host/port
    先回退为 iid/空，由 build_history_overview 从实时快照按 iid 关联补全。"""
    conn = {
        'total': s.get('conn_total'), 'max_conn': s.get('conn_max'),
        'usage_pct': s.get('conn_usage_pct'),
        'active': s.get('conn_active'), 'idle': s.get('conn_idle'),
        'blocked': s.get('conn_blocked'),
    }
    try:
        tbs = json.loads(s['tbs_json']) if s.get('tbs_json') else []
    except Exception:
        tbs = []
    try:
        spark = json.loads(s['spark_json']) if s.get('spark_json') else []
    except Exception:
        spark = []
    return {
        'id': s['iid'], 'name': s['iid'], 'db_type': s.get('db_type'),
        'group': s.get('grp'), 'host': '', 'port': '', 'label': s['iid'],
        'status': s.get('status'), 'err': None,
        'conn': conn, 'slowq': s.get('slowq', 0),
        'qps': s.get('qps'), 'tps': s.get('tps'),
        'cache_hit_pct': s.get('cache_hit_pct'),
        'lock_waits': s.get('lock_waits'), 'repl_lag_s': s.get('repl_lag_s'),
        'tbs': tbs, 'tbs_free_pct': s.get('tbs_free_pct'),
        'spark': spark, 'stat_hint': None,
        'ssh': {'enabled': False, 'host': '', 'port': 22, 'user': ''},
    }


def build_history_overview(from_ts, to_ts, at_ts):
    """历史回放：在 [from_ts, to_ts] 内，按 at_ts 时间点（取 ≤at 的最新样本，
    若无则取区间内最早样本）重建各实例状态，装配为与实时同构的 overview。"""
    hs = history_store.get_history_store()
    samples = hs.query(from_ts=from_ts, to_ts=to_ts, limit=20000)

    best = {}      # iid → ts ≤ at_ts 的最新样本
    earliest = {}  # iid → 区间内最早样本（兜底）
    for s in samples:
        iid = s['iid']
        if s['ts'] <= at_ts:
            if iid not in best or s['ts'] > best[iid]['ts']:
                best[iid] = s
        if iid not in earliest or s['ts'] < earliest[iid]['ts']:
            earliest[iid] = s
    chosen = []
    for iid in set(list(best.keys()) + list(earliest.keys())):
        chosen.append(best.get(iid) or earliest.get(iid))
    nodes = [_node_from_history_sample(c) for c in chosen if c]

    # 身份字段（name/label/host/port/group/ssh）属敏感白名单之外，不入历史库；
    # 回放时从实时快照按 iid 关联补全，保证拓扑分组/卡片命名与实时一致。
    collector = get_screen_collector()
    live = collector.get_snapshot() if collector else {}
    for n in nodes:
        s0 = live.get(n['id'])
        if not s0:
            continue   # 实例已删除：回退为 iid 展示
        for k in ('name', 'label', 'host', 'port', 'group', 'ssh'):
            v = s0.get(k)
            if v not in (None, ''):
                n[k] = v

    # 历史趋势：按 ts 桶聚合全实例 qps 之和
    agg = {}
    for s in samples:
        k = round(s['ts'])
        agg[k] = agg.get(k, 0) + (s.get('qps') or 0)
    trend = [{'ts': t, 'qps': round(agg[t], 1)} for t in sorted(agg)]

    return assemble_overview(nodes, collector, trend=trend)

# ═══════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════

_screen_collector = None
_screen_lock = threading.Lock()


def get_screen_collector():
    global _screen_collector
    if _screen_collector is None:
        with _screen_lock:
            if _screen_collector is None:
                _screen_collector = ScreenCollector()
    return _screen_collector
