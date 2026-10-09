# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
实时监控 SQL 模板 — 各数据库类型的慢查询和活跃连接查询

每个数据库类型定义两组 SQL：
- slow_query_sql: 获取 Top 慢查询
- connection_sql: 获取当前活跃连接信息
"""

# ═══════════════════════════════════════════════════════════════
# MySQL
# ═══════════════════════════════════════════════════════════════
MYSQL_SLOW_QUERY_SQL = """
SELECT
    SUBSTRING(d.digest_text, 1, 200) AS sql_text,
    ROUND(d.avg_timer_wait / 1000000000, 3) AS avg_time_s,
    ROUND(d.max_timer_wait / 1000000000, 3) AS max_time_s,
    d.count_star AS exec_count,
    ROUND(d.sum_timer_wait / 1000000000, 3) AS total_time_s,
    d.schema_name,
    d.digest
FROM performance_schema.events_statements_summary_by_digest d
WHERE d.schema_name NOT IN ('mysql', 'sys', 'information_schema', 'performance_schema')
  AND d.digest_text NOT LIKE 'COMMIT%'
  AND d.digest_text NOT LIKE 'ROLLBACK%'
ORDER BY d.avg_timer_wait DESC
LIMIT 30
"""

# ⚠️ 兼容性：窗口函数需 MySQL 8.0+（5.7 报 1064 → 连接采集无 fallback → 大屏误判宕机）。
# 每用户连接数改用派生表 JOIN（information_schema 聚合），5.7 / 8.0 通用。
MYSQL_CONNECTION_SQL = """
SELECT
    p.user AS username,
    p.db AS database_name,
    p.command AS command,
    ROUND(p.time / 3600, 1) AS duration_h,
    p.state,
    p.info AS current_sql,
    COALESCE(u.cnt, 0) AS user_conn_count,
    (SELECT COUNT(*) FROM information_schema.processlist) AS total_connections
FROM information_schema.processlist p
LEFT JOIN (SELECT user, COUNT(*) AS cnt
           FROM information_schema.processlist GROUP BY user) u
       ON u.user = p.user
WHERE p.id != CONNECTION_ID()
ORDER BY p.time DESC
LIMIT 50
"""

# MySQL fallback: 使用 processlist 替代 performance_schema（无需 SUPER 权限）
MYSQL_SLOW_QUERY_FALLBACK_SQL = """
SELECT
    SUBSTRING(info, 1, 200) AS sql_text,
    ROUND(time, 3) AS avg_time_s,
    ROUND(time, 3) AS max_time_s,
    1 AS exec_count,
    ROUND(time, 3) AS total_time_s,
    db AS schema_name,
    CONCAT(id, '_', time) AS digest
FROM information_schema.processlist
WHERE info IS NOT NULL
  AND info NOT LIKE 'COMMIT%'
  AND info NOT LIKE 'ROLLBACK%'
  AND `command` != 'Sleep'
  AND id != CONNECTION_ID()
ORDER BY time DESC
LIMIT 30
"""

# ═══════════════════════════════════════════════════════════════
# PostgreSQL / IvorySQL
# ═══════════════════════════════════════════════════════════════
PG_SLOW_QUERY_SQL = """
SELECT
    SUBSTRING(q.query, 1, 200) AS sql_text,
    ROUND(q.mean_exec_time / 1000, 3) AS avg_time_s,
    ROUND(q.max_exec_time / 1000, 3) AS max_time_s,
    q.calls AS exec_count,
    ROUND(q.total_exec_time / 1000, 3) AS total_time_s,
    q.dbid,
    q.queryid::text AS digest
FROM pg_stat_statements q
WHERE q.dbid != 0
ORDER BY q.mean_exec_time DESC
LIMIT 30
"""

# 如果 pg_stat_statements 未安装，使用 pg_stat_activity 作为 fallback
PG_SLOW_QUERY_FALLBACK_SQL = """
SELECT
    SUBSTRING(COALESCE(query, ''), 1, 200) AS sql_text,
    COALESCE(ROUND(EXTRACT(EPOCH FROM (NOW() - xact_start))::numeric, 3), 0) AS avg_time_s,
    COALESCE(ROUND(EXTRACT(EPOCH FROM (NOW() - xact_start))::numeric, 3), 0) AS max_time_s,
    1 AS exec_count,
    COALESCE(ROUND(EXTRACT(EPOCH FROM (NOW() - xact_start))::numeric, 3), 0) AS total_time_s,
    datname AS schema_name,
    pid::text AS digest
FROM pg_stat_activity
WHERE state != 'idle'
  AND state IS NOT NULL
  AND query NOT LIKE 'autovacuum%'
  AND pid != pg_backend_pid()
ORDER BY (NOW() - xact_start) DESC NULLS LAST
LIMIT 30
"""

PG_CONNECTION_SQL = """
SELECT
    a.usename AS username,
    a.datname AS database_name,
    a.state AS command,
    COALESCE(ROUND(EXTRACT(EPOCH FROM (NOW() - a.xact_start))::numeric / 3600, 1), 0) AS duration_h,
    COALESCE(a.state, 'unknown') AS state,
    SUBSTRING(COALESCE(a.query, ''), 1, 200) AS current_sql,
    (SELECT COUNT(*) FROM pg_stat_activity a2 WHERE a2.usename = a.usename) AS user_conn_count,
    (SELECT COUNT(*) FROM pg_stat_activity) AS total_connections
FROM pg_stat_activity a
WHERE a.pid != pg_backend_pid()
ORDER BY (NOW() - a.xact_start) DESC NULLS LAST
LIMIT 50
"""

# ═══════════════════════════════════════════════════════════════
# Oracle
# ═══════════════════════════════════════════════════════════════
ORACLE_SLOW_QUERY_SQL = """
SELECT * FROM (
  SELECT
    SUBSTR(s.sql_text, 1, 200) AS sql_text,
    ROUND(s.elapsed_time / 1000000, 3) AS avg_time_s,
    ROUND(s.elapsed_time / 1000000, 3) AS max_time_s,
    s.executions AS exec_count,
    ROUND(s.elapsed_time / 1000000, 3) AS total_time_s,
    s.parsing_schema_name AS schema_name,
    s.sql_id AS digest
  FROM v$sql s
  WHERE s.parsing_schema_name NOT IN ('SYS', 'SYSTEM', 'OUTLN')
    AND s.module != 'DBMS_SCHEDULER'
  ORDER BY s.elapsed_time DESC
) WHERE ROWNUM <= 30
"""

ORACLE_CONNECTION_SQL = """
SELECT * FROM (
  SELECT
    s.username AS username,
    s.schemaname AS database_name,
    s.program AS command,
    ROUND((SYSDATE - s.logon_time) * 24, 1) AS duration_h,
    s.status AS state,
    SUBSTR(q.sql_text, 1, 200) AS current_sql,
    (SELECT COUNT(*) FROM v$session WHERE username = s.username) AS user_conn_count,
    (SELECT COUNT(*) FROM v$session) AS total_connections
  FROM v$session s
  LEFT JOIN v$sql q ON s.sql_id = q.sql_id
  WHERE s.type != 'BACKGROUND'
  ORDER BY (SYSDATE - s.logon_time) DESC
) WHERE ROWNUM <= 50
"""

# ═══════════════════════════════════════════════════════════════
# SQL Server
# ═══════════════════════════════════════════════════════════════
SQLSERVER_SLOW_QUERY_SQL = """
SELECT TOP 30
    SUBSTRING(st.text, 1, 200) AS sql_text,
    ROUND(qs.total_elapsed_time * 1.0 / qs.execution_count / 1000000, 3) AS avg_time_s,
    ROUND(qs.max_elapsed_time * 1.0 / 1000000, 3) AS max_time_s,
    qs.execution_count AS exec_count,
    ROUND(qs.total_elapsed_time * 1.0 / 1000000, 3) AS total_time_s,
    DB_NAME(st.dbid) AS schema_name,
    CONVERT(VARCHAR(32), qs.plan_handle, 1) AS digest
FROM sys.dm_exec_query_stats qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
WHERE st.text NOT LIKE 'CREATE STATISTICS%'
  AND st.text NOT LIKE 'DBCC%'
ORDER BY qs.total_elapsed_time DESC
"""

SQLSERVER_CONNECTION_SQL = """
SELECT TOP 50
    s.login_name AS username,
    DB_NAME(s.database_id) AS database_name,
    COALESCE(r.command, 'idle') AS command,
    ROUND(DATEDIFF(SECOND, ISNULL(r.start_time, s.last_request_start_time), GETDATE()) / 3600.0, 1) AS duration_h,
    COALESCE(r.status, 'sleeping') AS state,
    SUBSTRING(st.text, 1, 200) AS current_sql,
    (SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE login_name = s.login_name) AS user_conn_count,
    (SELECT COUNT(*) FROM sys.dm_exec_sessions) AS total_connections
FROM sys.dm_exec_sessions s
LEFT JOIN sys.dm_exec_requests r ON s.session_id = r.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) st
WHERE s.is_user_process = 1
ORDER BY ISNULL(r.start_time, s.last_request_start_time) ASC
"""

# ═══════════════════════════════════════════════════════════════
# DM8 达梦
# ═══════════════════════════════════════════════════════════════
DM_SLOW_QUERY_SQL = """
SELECT
    SUBSTR(MAX(TOP_SQL_TEXT), 1, 200) AS sql_text,
    ROUND(MAX(TIME_USED) / 1000000.0, 3) AS avg_time_s,
    ROUND(MAX(TIME_USED) / 1000000.0, 3) AS max_time_s,
    COUNT(*) AS exec_count,
    ROUND(SUM(TIME_USED) / 1000000.0, 3) AS total_time_s,
    NULL AS schema_name,
    SQL_ID AS digest
FROM V$SQL_HISTORY
WHERE TOP_SQL_TEXT IS NOT NULL
GROUP BY SQL_ID
ORDER BY SUM(TIME_USED) DESC
LIMIT 30
"""

# DM8 V$SESSIONS 实测列名（SESS_ID/CURR_SCH/APPNAME/SQL_TEXT，无 SCHEMA_NAME/PROGRAM_NAME）；
# STATE 单字符（W=等待 R=运行 I=空闲），time 单位微秒。
# 注意：不按 SES_ID() 过滤自连接——该内建在部分账号/环境下不可解析，且自连接本身
# 也是一条真实连接，纳入计数无碍。
DM_CONNECTION_SQL = """
SELECT
    S.USER_NAME AS username,
    S.CURR_SCH AS database_name,
    S.APPNAME AS command,
    ROUND(DATEDIFF(HOUR, S.LAST_RECV_TIME, CURDATE()), 1) AS duration_h,
    CASE S.STATE
        WHEN 'W' THEN 'waiting'
        WHEN 'R' THEN 'running'
        WHEN 'I' THEN 'idle'
        ELSE S.STATE
    END AS state,
    SUBSTR(S.SQL_TEXT, 1, 200) AS current_sql,
    (SELECT COUNT(*) FROM V$SESSIONS WHERE USER_NAME = S.USER_NAME) AS user_conn_count,
    (SELECT COUNT(*) FROM V$SESSIONS) AS total_connections
FROM V$SESSIONS S
ORDER BY S.LAST_RECV_TIME ASC
LIMIT 50
"""

# ═══════════════════════════════════════════════════════════════
# TiDB
# ═══════════════════════════════════════════════════════════════
TIDB_SLOW_QUERY_SQL = """
SELECT
    SUBSTRING(query, 1, 200) AS sql_text,
    ROUND(avg_latency, 3) AS avg_time_s,
    ROUND(max_latency, 3) AS max_time_s,
    SUM_COUNT AS exec_count,
    ROUND(total_latency, 3) AS total_time_s,
    SCHEMA_NAME AS schema_name,
    DIGEST AS digest
FROM information_schema.CLUSTER_STATEMENTS_SUMMARY
WHERE SCHEMA_NAME NOT IN ('mysql', 'sys', 'information_schema', 'performance_schema', 'METRICS_SCHEMA')
ORDER BY avg_latency DESC
LIMIT 30
"""

# TiDB fallback: 使用 PROCESSLIST（老版本可能没有 CLUSTER_STATEMENTS_SUMMARY）
TIDB_SLOW_QUERY_FALLBACK_SQL = """
SELECT
    SUBSTRING(info, 1, 200) AS sql_text,
    ROUND(time, 3) AS avg_time_s,
    ROUND(time, 3) AS max_time_s,
    1 AS exec_count,
    ROUND(time, 3) AS total_time_s,
    db AS schema_name,
    id::text AS digest
FROM information_schema.processlist
WHERE info IS NOT NULL
  AND info NOT LIKE 'COMMIT%'
  AND info NOT LIKE 'ROLLBACK%'
  AND `command` != 'Sleep'
ORDER BY time DESC
LIMIT 30
"""

TIDB_CONNECTION_SQL = """
SELECT
    user AS username,
    db AS database_name,
    `command` AS command,
    ROUND(time / 3600, 1) AS duration_h,
    info AS state,
    SUBSTRING(info, 1, 200) AS current_sql,
    COUNT(*) OVER (PARTITION BY user) AS user_conn_count,
    (SELECT COUNT(*) FROM information_schema.processlist) AS total_connections
FROM information_schema.processlist
WHERE id != CONNECTION_ID()
ORDER BY time DESC
LIMIT 50
"""

# ═══════════════════════════════════════════════════════════════
# GBase 8s（南大通用，Informix 血统；sysmaster 库系统表，经 JDBC 子进程通道）
# ═══════════════════════════════════════════════════════════════

# 慢查询：GBase 8s 无语句级统计视图，用「当前等待中会话」近似（等待即慢）。
GBASE_SLOW_QUERY_SQL = """
SELECT
    s.sid AS digest,
    TRIM(s.username) AS username,
    COALESCE(w.wait_time, 0) AS avg_time_s,
    COALESCE(w.wait_time, 0) AS max_time_s,
    1 AS exec_count,
    COALESCE(w.wait_time, 0) AS total_time_s,
    COALESCE(TRIM(d.odb_dbname), '-') AS schema_name,
    '等待中的会话（wait_time 秒）' AS sql_text
FROM sysmaster:sysseswait w
  LEFT JOIN sysmaster:syssessions s ON s.sid = w.sid
  LEFT JOIN sysmaster:sysopendb d ON d.odb_sessionid = w.sid AND d.is_current = 'Y'
LIMIT 30
"""

GBASE_CONNECTION_SQL = """
SELECT
    TRIM(s.username) AS username,
    COALESCE(TRIM(d.odb_dbname), '-') AS database_name,
    CASE WHEN w.sid IS NOT NULL THEN 'waiting'
         WHEN s.sid = dbinfo('sessionid') THEN 'active'
         ELSE 'idle' END AS state,
    0 AS duration_h,
    '' AS current_sql,
    (SELECT COUNT(*) FROM sysmaster:syssessions s2 WHERE s2.username = s.username)
        AS user_conn_count,
    (SELECT COUNT(*) FROM sysmaster:syssessions) AS total_connections
FROM sysmaster:syssessions s
  LEFT JOIN sysmaster:sysopendb d ON d.odb_sessionid = s.sid AND d.is_current = 'Y'
  LEFT JOIN sysmaster:sysseswait w ON w.sid = s.sid
ORDER BY s.sid
LIMIT 50
"""

# ═══════════════════════════════════════════════════════════════
# DB2（IBM Db2，SYSIBMADM 管理视图，监控账号需 SYSMON 权限；JDBC 子进程通道）
# ═══════════════════════════════════════════════════════════════

# 慢查询：MON_CURRENT_SQL 为内存实时视图（当前执行 >= 1s 的语句）。
# 列名经真实 DB2 LUW 实测：作者列是 SESSION_AUTH_ID（无 AUTHID 列，-206）。
DB2_SLOW_QUERY_SQL = """
SELECT
    SUBSTR(STMT_TEXT, 1, 200) AS sql_text,
    ELAPSED_TIME_SEC AS avg_time_s,
    ELAPSED_TIME_SEC AS max_time_s,
    1 AS exec_count,
    ELAPSED_TIME_SEC AS total_time_s,
    SESSION_AUTH_ID AS schema_name,
    VARCHAR(APPLICATION_HANDLE) AS digest
FROM SYSIBMADM.MON_CURRENT_SQL
WHERE ELAPSED_TIME_SEC >= 1
ORDER BY ELAPSED_TIME_SEC DESC
FETCH FIRST 30 ROWS ONLY
"""

DB2_CONNECTION_SQL = """
SELECT
    AUTHID AS username,
    COALESCE(DB_NAME, '-') AS database_name,
    APPL_STATUS AS state,
    0 AS duration_h,
    APPL_NAME AS current_sql,
    COUNT(*) OVER (PARTITION BY AUTHID) AS user_conn_count,
    (SELECT COUNT(*) FROM SYSIBMADM.APPLICATIONS) AS total_connections
FROM SYSIBMADM.APPLICATIONS
WHERE AUTHID IS NOT NULL
FETCH FIRST 50 ROWS ONLY
"""

# ═══════════════════════════════════════════════════════════════
# ClickHouse（system 表；JDBC 子进程通道）
# ═══════════════════════════════════════════════════════════════

# 慢查询：主路径 system.query_log（需日志开启），回退 system.processes（实时）。
CLICKHOUSE_SLOW_QUERY_SQL = """
SELECT
    SUBSTRING(query, 1, 200) AS sql_text,
    ROUND(query_duration_ms / 1000, 3) AS avg_time_s,
    ROUND(query_duration_ms / 1000, 3) AS max_time_s,
    1 AS exec_count,
    ROUND(query_duration_ms / 1000, 3) AS total_time_s,
    user AS schema_name,
    toString(event_time) AS digest
FROM system.query_log
WHERE type = 'QueryFinish' AND query_duration_ms >= 1000
  AND event_date >= today() - 1
ORDER BY query_duration_ms DESC
LIMIT 30
"""

CLICKHOUSE_SLOW_QUERY_FALLBACK_SQL = """
SELECT
    SUBSTRING(query, 1, 200) AS sql_text,
    ROUND(elapsed, 3) AS avg_time_s,
    ROUND(elapsed, 3) AS max_time_s,
    1 AS exec_count,
    ROUND(elapsed, 3) AS total_time_s,
    user AS schema_name,
    toString(query_id) AS digest
FROM system.processes
WHERE elapsed >= 1
ORDER BY elapsed DESC
LIMIT 30
"""

CLICKHOUSE_CONNECTION_SQL = """
SELECT
    user AS username,
    currentDatabase() AS database_name,
    'active' AS state,
    ROUND(elapsed / 3600, 1) AS duration_h,
    SUBSTRING(query, 1, 200) AS current_sql,
    COUNT(*) OVER () AS user_conn_count,
    (SELECT value FROM system.metrics WHERE metric = 'TCPConnection')
        AS total_connections
FROM system.processes
LIMIT 50
"""

# ═══════════════════════════════════════════════════════════════
# SQL 模板映射
# ═══════════════════════════════════════════════════════════════

# 数据源/插件层的 db_type 标识 → monitor 查询模板键。
# 插件 id 带 _jdbc 后缀（oracle_jdbc/sqlserver_jdbc），TDSQL-C 用专有名；
# 国产 PG 系（hgdb/kingbase/uxdb/vastbase）连接走 PG 线协议，模板复用 pg。
# 映射口径与 driver_registry.py 的 catalog 归并保持一致。
_DB_TYPE_ALIASES = {
    'oracle_jdbc': 'oracle',
    'sqlserver_jdbc': 'sqlserver',
    'tdsqlc_mysql': 'mysql',
    'hgdb': 'pg', 'kingbase': 'pg', 'uxdb': 'pg', 'vastbase': 'pg',
    'halodb': 'pg',   # HaloDB（羲和）PG14 内核，连接与模板复用 pg
    'gbase8s': 'gbase',   # driver_registry catalog 名 → monitor 模板键
}


def normalize_db_type(db_type):
    """归一 db_type 为 monitor 模板键（oracle_jdbc→oracle 等）。

    模板/指标 family 查找与监控引擎的连接分派（_create_connection 的
    python 驱动分支）均使用归一后的键。
    """
    dt = (db_type or '').strip().lower()
    if dt in _DB_TYPE_ALIASES:
        return _DB_TYPE_ALIASES[dt]
    if dt.endswith('_jdbc') and dt[:-5] in SLOW_QUERY_TEMPLATES:
        return dt[:-5]
    return dt


SLOW_QUERY_TEMPLATES = {
    'mysql': MYSQL_SLOW_QUERY_SQL,
    'mariadb': MYSQL_SLOW_QUERY_SQL,  # MariaDB 与 MySQL 协议兼容，复用 MySQL 慢查询 SQL
    'postgresql': PG_SLOW_QUERY_SQL,
    'pg': PG_SLOW_QUERY_SQL,
    'ivorysql': PG_SLOW_QUERY_SQL,
    'oracle': ORACLE_SLOW_QUERY_SQL,
    'sqlserver': SQLSERVER_SLOW_QUERY_SQL,
    'dm': DM_SLOW_QUERY_SQL,
    'tidb': TIDB_SLOW_QUERY_SQL,
    'gbase': GBASE_SLOW_QUERY_SQL,
    'db2': DB2_SLOW_QUERY_SQL,
    'clickhouse': CLICKHOUSE_SLOW_QUERY_SQL,
    # OceanBase MySQL 租户兼容 performance_schema.events_statements_summary_by_digest（4.x），
    # 复用 MySQL 慢查询 SQL；老版本 OB 无该表时走 fallback（processlist 版）。
    'oceanbase': MYSQL_SLOW_QUERY_SQL,
}

SLOW_QUERY_FALLBACK_TEMPLATES = {
    'mysql': MYSQL_SLOW_QUERY_FALLBACK_SQL,
    'mariadb': MYSQL_SLOW_QUERY_FALLBACK_SQL,  # 复用 MySQL fallback（processlist 替代 performance_schema）
    'postgresql': PG_SLOW_QUERY_FALLBACK_SQL,
    'pg': PG_SLOW_QUERY_FALLBACK_SQL,
    'ivorysql': PG_SLOW_QUERY_FALLBACK_SQL,
    'tidb': TIDB_SLOW_QUERY_FALLBACK_SQL,
    'clickhouse': CLICKHOUSE_SLOW_QUERY_FALLBACK_SQL,
    # OB processlist 版 fallback：performance_schema 不可用/老版本 OB 的兜底（100% 兼容）
    'oceanbase': MYSQL_SLOW_QUERY_FALLBACK_SQL,
}

CONNECTION_TEMPLATES = {
    'mysql': MYSQL_CONNECTION_SQL,
    'mariadb': MYSQL_CONNECTION_SQL,  # 复用 MySQL 活跃连接 SQL（information_schema.processlist）
    'postgresql': PG_CONNECTION_SQL,
    'pg': PG_CONNECTION_SQL,
    'ivorysql': PG_CONNECTION_SQL,
    'oracle': ORACLE_CONNECTION_SQL,
    'sqlserver': SQLSERVER_CONNECTION_SQL,
    'dm': DM_CONNECTION_SQL,
    'tidb': TIDB_CONNECTION_SQL,
    'gbase': GBASE_CONNECTION_SQL,
    'db2': DB2_CONNECTION_SQL,
    'clickhouse': CLICKHOUSE_CONNECTION_SQL,
    # OceanBase MySQL 租户兼容 information_schema.processlist（含派生表 JOIN），
    # 直接复用 MySQL 连接 SQL——此前漏注册导致 engine 恒返「不支持的类型」，
    # 大屏对 OB 从不探活、状态被 _derive_status 覆盖成假绿 ok（2026-10-09 实测）。
    'oceanbase': MYSQL_CONNECTION_SQL,
}

# 各数据库最大连接数默认值（用于计算使用率）
MAX_CONNECTION_DEFAULTS = {
    'mysql': 151,
    'mariadb': 151,  # 与 MySQL 一致的默认上限
    'postgresql': 100,
    'pg': 100,
    'ivorysql': 100,
    'oracle': 1500,
    'sqlserver': 32767,
    'dm': 1000,
    'tidb': 16384,
    'gbase': 100,       # Informix 血统无统一 max sessions 参数，用保守默认
    'db2': 500,         # MAX_CONNECTIONS 因版本而异，取中位默认（查询失败时兜底）
    'clickhouse': 4096, # 官方默认 max_connections
    'oceanbase': 1000,  # OB 租户常见默认量级（实际以 @@global.max_connections 查询为准）
}

# 获取最大连接数的 SQL
MAX_CONN_QUERY_SQL = {
    'mysql': "SELECT @@global.max_connections AS max_conn",
    'mariadb': "SELECT @@global.max_connections AS max_conn",  # 复用 MySQL 查询
    'postgresql': "SELECT setting::int AS max_conn FROM pg_settings WHERE name = 'max_connections'",
    'pg': "SELECT setting::int AS max_conn FROM pg_settings WHERE name = 'max_connections'",
    'ivorysql': "SELECT setting::int AS max_conn FROM pg_settings WHERE name = 'max_connections'",
    'oracle': "SELECT TO_NUMBER(VALUE) AS max_conn FROM v$parameter WHERE NAME = 'processes'",
    'sqlserver': "SELECT 32767 AS max_conn",
    'dm': "SELECT VALUE AS max_conn FROM V$DM_INI WHERE PARA_NAME = 'MAX_SESSIONS'",
    'tidb': "SELECT @@global.max_connections AS max_conn",
    'oceanbase': "SELECT @@global.max_connections AS max_conn",  # MySQL 租户兼容
    # db2：DBCFG 对监控账号通常不可见（实测空行集），直接用 MAX_CONNECTION_DEFAULTS 兜底
    'clickhouse': ("SELECT toUInt32(value) AS max_conn FROM system.server_settings "
                   "WHERE name = 'max_connections'"),
}
