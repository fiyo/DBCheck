# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck Professional — 专有商业软件，保留一切权利（Proprietary Software, All Rights Reserved）.
# See LICENSE for full license text.
#

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

MYSQL_CONNECTION_SQL = """
SELECT
    p.user AS username,
    p.db AS database_name,
    p.command AS command,
    ROUND(p.time / 3600, 1) AS duration_h,
    p.state,
    p.info AS current_sql,
    COUNT(*) OVER (PARTITION BY p.user) AS user_conn_count,
    (SELECT COUNT(*) FROM information_schema.processlist) AS total_connections
FROM information_schema.processlist p
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
FETCH FIRST 30 ROWS ONLY
"""

ORACLE_CONNECTION_SQL = """
SELECT
    s.username AS username,
    s.schema_name AS database_name,
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
FETCH FIRST 50 ROWS ONLY
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
    DB_NAME(t.database_id) AS database_name,
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
    SUBSTR(sql_text, 1, 200) AS sql_text,
    ROUND(elapse_int / 1000000.0, 3) AS avg_time_s,
    ROUND(elapse_int / 1000000.0, 3) AS max_time_s,
    EXECUTE_COUNT AS exec_count,
    ROUND(elapse_int / 1000000.0, 3) AS total_time_s,
    SCHEMA_NAME AS schema_name,
    SQL_ID AS digest
FROM V$SQL_HISTORY
WHERE SCHEMA_NAME NOT IN ('SYS', 'SYSSSO', 'SYSCMON', 'SYSJOB', 'SYSAUTH', 'SYSDMOP')
ORDER BY elapse_int DESC
LIMIT 30
"""

DM_CONNECTION_SQL = """
SELECT
    S.USER_NAME AS username,
    S.SCHEMA_NAME AS database_name,
    S.PROGRAM_NAME AS command,
    ROUND(DATEDIFF(HOUR, S.LAST_RECV_TIME, CURDATE()) , 1) AS duration_h,
    CASE S.STATE
        WHEN 'W' THEN 'waiting'
        WHEN 'R' THEN 'running'
        ELSE S.STATE
    END AS state,
    SUBSTR(Q.SQL_TEXT, 1, 200) AS current_sql,
    (SELECT COUNT(*) FROM V$SESSIONS WHERE USER_NAME = S.USER_NAME) AS user_conn_count,
    (SELECT COUNT(*) FROM V$SESSIONS) AS total_connections
FROM V$SESSIONS S
LEFT JOIN V$SQLTEXT Q ON S.SQL_ID = Q.SQL_ID
WHERE S.SESSION_ID != SES_ID()
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
# SQL 模板映射
# ═══════════════════════════════════════════════════════════════

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
}

SLOW_QUERY_FALLBACK_TEMPLATES = {
    'mysql': MYSQL_SLOW_QUERY_FALLBACK_SQL,
    'mariadb': MYSQL_SLOW_QUERY_FALLBACK_SQL,  # 复用 MySQL fallback（processlist 替代 performance_schema）
    'postgresql': PG_SLOW_QUERY_FALLBACK_SQL,
    'pg': PG_SLOW_QUERY_FALLBACK_SQL,
    'ivorysql': PG_SLOW_QUERY_FALLBACK_SQL,
    'tidb': TIDB_SLOW_QUERY_FALLBACK_SQL,
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
}
