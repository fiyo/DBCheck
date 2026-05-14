# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#


"""
DBCheck 慢查询深度分析模块
===========================
提供各数据库慢查询的深度采集与 AI 智能诊断。

支持数据库：
  - MySQL 5.7+   （performance_schema + sys 视图）
  - PostgreSQL 10+（pg_stat_statements）
  - Oracle 11g+   （AWR / v$sql / DBA_HIST_SQLTEXT）
  - SQL Server 2012+（sys.dm_exec_query_stats）
  - DM8           （v$sql / v$sql_plan）

核心类：
  SlowQueryAnalyzer    — 工厂类，根据 db_type 返回对应分析器
  BaseSlowQueryAnalyzer — 各数据库抽象基类
  MySQLSlowQueryAnalyzer
  PGSlowQueryAnalyzer
  OracleSlowQueryAnalyzer
  SQLServerSlowQueryAnalyzer
  DMSlowQueryAnalyzer

AI 诊断：
  利用现有 AIAdvisor（analyzer.py），自动注入慢查询指标，
  生成"根因分析 → 优化建议"格式的诊断报告。
"""

import time
import re


# ═══════════════════════════════════════════════════════════
#  1. 各数据库慢查询 SQL 模板
# ═══════════════════════════════════════════════════════════

MYSQL_SLOW_QUERIES = {
    # Top SQL by total latency (MySQL 5.7+)
    "mysql_top_by_latency": """
        SELECT
            DIGEST_TEXT AS query_sample_text,
            COUNT_STAR AS exec_count,
            SUM_TIMER_WAIT / 1000000000000 AS total_latency_sec,
            AVG_TIMER_WAIT / 1000000000000 AS avg_latency_sec,
            SUM_ROWS_EXAMINED AS rows_scanned,
            SUM_ROWS_SENT AS rows_sent,
            SUM_CREATED_TMP_DISK_TABLES AS tmp_disk_tables,
            SUM_SORT_MERGE_PASSES AS sort_merge_passes,
            SUM_SORT_ROWS AS rows_sorted,
            FIRST_SEEN AS first_seen,
            LAST_SEEN AS last_seen
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST_TEXT IS NOT NULL
          AND DIGEST_TEXT NOT LIKE 'EXPLAIN %'
        ORDER BY total_latency_sec DESC
        LIMIT 20
    """,

    # Top SQL by full table scans (MySQL 5.7+)
    "mysql_top_full_table_scan": """
        SELECT
            DIGEST_TEXT AS query_sample_text,
            COUNT_STAR AS exec_count,
            SUM_ROWS_EXAMINED AS rows_scanned,
            SUM_ROWS_SENT AS rows_sent,
            (SUM_ROWS_EXAMINED - SUM_ROWS_SENT) AS rows_filtered,
            ROUND((SUM_ROWS_EXAMINED - SUM_ROWS_SENT) * 100.0 / NULLIF(SUM_ROWS_EXAMINED, 0), 2) AS filter_ratio_pct
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST_TEXT IS NOT NULL
          AND SUM_ROWS_EXAMINED > 0
          AND (SUM_ROWS_EXAMINED - SUM_ROWS_SENT) > SUM_ROWS_SENT * 5
        ORDER BY rows_scanned DESC
        LIMIT 10
    """,

    # Top SQL by lock wait time (MySQL 5.7+)
    # 注意：AVG_LOCK_TIME 列在 MySQL 8.0.21+ 才存在，早期版本会报错
    # 为兼容性，仅使用 SUM_LOCK_TIME / COUNT_STAR 替代
    "mysql_top_by_lock": """
        SELECT
            DIGEST_TEXT AS query_sample_text,
            COUNT_STAR AS exec_count,
            SUM_LOCK_TIME / 1000000000000 AS total_lock_sec,
            SUM_LOCK_TIME / GREATEST(COUNT_STAR, 1) / 1000000000000 AS avg_lock_sec,
            SUM_ROWS_AFFECTED AS rows_affected
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST_TEXT IS NOT NULL
          AND SUM_LOCK_TIME > 0
        ORDER BY total_lock_sec DESC
        LIMIT 10
    """,

    # Recent slow query log entries (requires slow_query_log=ON)
    "mysql_slow_log_recent": """
        SELECT
            start_time,
            user_host,
            query_time,
            lock_time,
            rows_sent,
            rows_examined,
            db,
            LEFT(sql_text, 200) AS sql_text
        FROM mysql.slow_log
        ORDER BY start_time DESC
        LIMIT 10
    """,

    # Top statements by tmp table usage
    "mysql_top_tmp_tables": """
        SELECT
            DIGEST_TEXT AS query_sample_text,
            COUNT_STAR AS exec_count,
            SUM_CREATED_TMP_DISK_TABLES AS disk_tmp_tables,
            SUM_CREATED_TMP_TABLES AS tmp_tables_created,
            AVG_TIMER_WAIT / 1000000000000 AS avg_latency_sec
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST_TEXT IS NOT NULL
          AND (SUM_CREATED_TMP_DISK_TABLES > 0 OR SUM_CREATED_TMP_TABLES > 0)
        ORDER BY disk_tmp_tables DESC, tmp_tables_created DESC
        LIMIT 10
    """,

    # Top statements by sort operations
    "mysql_top_sorting": """
        SELECT
            DIGEST_TEXT AS query_sample_text,
            COUNT_STAR AS exec_count,
            SUM_SORT_ROWS AS rows_sorted,
            SUM_SORT_MERGE_PASSES AS merge_passes,
            SUM_SORT_RANGE AS range_sorts,
            AVG_TIMER_WAIT / 1000000000000 AS avg_latency_sec
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST_TEXT IS NOT NULL
          AND SUM_SORT_ROWS > 0
        ORDER BY rows_sorted DESC
        LIMIT 10
    """,
}

# PostgreSQL 慢查询 SQL
PG_SLOW_QUERIES = {
    # Top SQL by total time (requires pg_stat_statements)
    "pg_top_by_total_time": """
        SELECT
            LEFT(query, 200) AS query_text,
            calls,
            total_exec_time / 1000 AS total_time_sec,
            min_exec_time / 1000 AS min_time_sec,
            max_exec_time / 1000 AS max_time_sec,
            mean_exec_time / 1000 AS mean_time_sec,
            stddev_exec_time / 1000 AS stddev_sec,
            rows,
            shared_blks_hit,
            shared_blks_read,
            shared_blks_dirtied,
            shared_blks_written,
            local_blks_hit,
            local_blks_read,
            local_blks_dirtied,
            local_blks_written,
            temp_blks_read,
            temp_blks_written,
            blk_read_time / 1000 AS blk_read_ms,
            blk_write_time / 1000 AS blk_write_ms
        FROM pg_stat_statements
        ORDER BY total_exec_time DESC
        LIMIT 20
    """,

    # Top SQL by avg time (calls > 10 to filter out noise)
    "pg_top_by_avg_time": """
        SELECT
            LEFT(query, 200) AS query_text,
            calls,
            total_exec_time / 1000 AS total_time_sec,
            mean_exec_time / 1000 AS mean_time_sec,
            max_exec_time / 1000 AS max_time_sec,
            rows,
            shared_blks_hit,
            shared_blks_read
        FROM pg_stat_statements
        WHERE calls > 10
        ORDER BY mean_exec_time DESC
        LIMIT 15
    """,

    # Top SQL by IO time
    "pg_top_by_io": """
        SELECT
            LEFT(query, 200) AS query_text,
            calls,
            total_exec_time / 1000 AS total_time_sec,
            blk_read_time / 1000 AS read_time_sec,
            blk_write_time / 1000 AS write_time_sec,
            (blk_read_time + blk_write_time) / 1000 AS total_io_sec,
            shared_blks_read,
            shared_blks_written,
            rows
        FROM pg_stat_statements
        WHERE (blk_read_time + blk_write_time) > 0
        ORDER BY total_io_sec DESC
        LIMIT 10
    """,

    # Top SQL by temp block usage (disk spill)
    "pg_top_by_temp": """
        SELECT
            LEFT(query, 200) AS query_text,
            calls,
            temp_blks_read,
            temp_blks_written,
            (temp_blks_read + temp_blks_written) AS total_temp_blocks,
            total_exec_time / 1000 AS total_time_sec
        FROM pg_stat_statements
        WHERE (temp_blks_read + temp_blks_written) > 0
        ORDER BY total_temp_blocks DESC
        LIMIT 10
    """,

    # Long running queries snapshot (current)
    "pg_long_running": """
        SELECT
            pid,
            now() - query_start AS duration,
            state,
            left(query, 200) AS query_text,
            usename,
            datname,
            client_addr,
            wait_event_type,
            wait_event
        FROM pg_stat_activity
        WHERE state != 'idle'
          AND query_start IS NOT NULL
          AND (now() - query_start) > interval '5 seconds'
        ORDER BY duration DESC
        LIMIT 15
    """,
}

# Oracle 慢查询 SQL（利用 v$sql / DBA_HIST_SQLTEXT）
ORACLE_SLOW_QUERIES = {
    "ora_top_sql_by_buffer_gets": """
        SELECT
            sql_id,
            SUBSTR(sql_text, 1, 200) AS sql_text,
            parse_calls,
            executions,
            ROUND(elapsed_time / 1000000, 2) AS elapsed_sec,
            ROUND(elapsed_time / 1000000 / NULLIF(executions, 0), 3) AS avg_elapsed_sec,
            buffer_gets,
            disk_reads,
            rows_processed,
            module,
            first_load_time,
            last_active_time
        FROM v$sql
        WHERE executions > 0
          AND sql_text NOT LIKE '/*+%'
          AND sql_text NOT LIKE 'EXPLAIN%'
        ORDER BY buffer_gets DESC
        FETCH FIRST 20 ROWS ONLY
    """,

    "ora_top_sql_by_disk_reads": """
        SELECT
            sql_id,
            SUBSTR(sql_text, 1, 200) AS sql_text,
            executions,
            ROUND(elapsed_time / 1000000, 2) AS elapsed_sec,
            buffer_gets,
            disk_reads,
            rows_processed,
            fetches,
            loads,
            Invalidations,
            sharable_mem / 1024 AS sharable_mem_kb
        FROM v$sql
        WHERE executions > 0
          AND disk_reads > 0
        ORDER BY disk_reads DESC
        FETCH FIRST 20 ROWS ONLY
    """,

    "ora_top_sql_by_elapsed": """
        SELECT
            sql_id,
            SUBSTR(sql_text, 1, 200) AS sql_text,
            executions,
            ROUND(elapsed_time / 1000000, 2) AS elapsed_sec,
            ROUND(elapsed_time / 1000000 / NULLIF(executions, 0), 3) AS avg_elapsed_sec,
            buffer_gets,
            disk_reads,
            rows_processed,
            cpu_time / 1000000 AS cpu_sec,
            application_wait_time / 1000000 AS app_wait_sec,
            cluster_wait_time / 1000000 AS cluster_wait_sec,
            user_io_wait_time / 1000000 AS user_io_sec
        FROM v$sql
        WHERE executions > 0
        ORDER BY elapsed_time DESC
        FETCH FIRST 20 ROWS ONLY
    """,

    "ora_sql_with_full_table_scan": """
        SELECT
            sql_id,
            SUBSTR(sql_text, 1, 200) AS sql_text,
            executions,
            ROUND(elapsed_time / 1000000, 2) AS elapsed_sec,
            buffer_gets,
            disk_reads,
            rows_processed
        FROM v$sql
        WHERE executions > 0
          AND (buffer_gets > rows_processed * 100 OR disk_reads > rows_processed * 10)
          AND sql_text NOT LIKE 'EXPLAIN%'
        ORDER BY elapsed_time DESC
        FETCH FIRST 15 ROWS ONLY
    """,
}

# SQL Server 慢查询 SQL
SQLSERVER_SLOW_QUERIES = {
    "mssql_top_by_cpu": """
        SELECT TOP 20
            qs.execution_count,
            SUBSTRING(qt.text, qs.statement_start_offset/2 + 1,
                (CASE WHEN qs.statement_end_offset = -1
                    THEN DATALENGTH(qt.text)
                    ELSE qs.statement_end_offset END - qs.statement_start_offset)/2 + 1) AS query_text,
            qs.total_worker_time / 1000 AS total_cpu_ms,
            qs.last_worker_time / 1000 AS last_cpu_ms,
            qs.total_elapsed_time / 1000 AS total_elapsed_ms,
            qs.last_elapsed_time / 1000 AS last_elapsed_ms,
            qs.total_logical_reads,
            qs.last_logical_reads,
            qs.total_physical_reads,
            qs.last_physical_reads,
            qs.creation_time,
            qs.last_execution_time,
            DB_NAME(qt.dbid) AS db_name,
            OBJECT_NAME(qt.objectid, qt.dbid) AS object_name
        FROM sys.dm_exec_query_stats qs
        CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) qt
        WHERE qs.execution_count > 0
        ORDER BY qs.total_worker_time DESC
    """,

    "mssql_top_by_logical_reads": """
        SELECT TOP 20
            qs.execution_count,
            SUBSTRING(qt.text, qs.statement_start_offset/2 + 1,
                (CASE WHEN qs.statement_end_offset = -1
                    THEN DATALENGTH(qt.text)
                    ELSE qs.statement_end_offset END - qs.statement_start_offset)/2 + 1) AS query_text,
            qs.total_logical_reads,
            qs.last_logical_reads,
            qs.total_physical_reads,
            qs.total_worker_time / 1000 AS total_cpu_ms,
            qs.total_elapsed_time / 1000 AS total_elapsed_ms,
            qs.creation_time,
            qs.last_execution_time,
            DB_NAME(qt.dbid) AS db_name
        FROM sys.dm_exec_query_stats qs
        CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) qt
        WHERE qs.execution_count > 0
        ORDER BY qs.total_logical_reads DESC
    """,

    "mssql_top_by_elapsed": """
        SELECT TOP 20
            qs.execution_count,
            SUBSTRING(qt.text, qs.statement_start_offset/2 + 1,
                (CASE WHEN qs.statement_end_offset = -1
                    THEN DATALENGTH(qt.text)
                    ELSE qs.statement_end_offset END - qs.statement_start_offset)/2 + 1) AS query_text,
            qs.total_elapsed_time / 1000 AS total_elapsed_ms,
            qs.last_elapsed_time / 1000 AS last_elapsed_ms,
            qs.total_worker_time / 1000 AS total_cpu_ms,
            qs.total_logical_reads,
            qs.total_physical_reads,
            qs.creation_time,
            qs.last_execution_time,
            DB_NAME(qt.dbid) AS db_name
        FROM sys.dm_exec_query_stats qs
        CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) qt
        WHERE qs.execution_count > 0
        ORDER BY qs.total_elapsed_time DESC
    """,

    "mssql_top_by_physical_reads": """
        SELECT TOP 15
            qs.execution_count,
            SUBSTRING(qt.text, qs.statement_start_offset/2 + 1,
                (CASE WHEN qs.statement_end_offset = -1
                    THEN DATALENGTH(qt.text)
                    ELSE qs.statement_end_offset END - qs.statement_start_offset)/2 + 1) AS query_text,
            qs.total_physical_reads,
            qs.last_physical_reads,
            qs.total_logical_reads,
            qs.total_worker_time / 1000 AS total_cpu_ms,
            qs.total_elapsed_time / 1000 AS total_elapsed_ms,
            qs.last_execution_time,
            DB_NAME(qt.dbid) AS db_name
        FROM sys.dm_exec_query_stats qs
        CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) qt
        WHERE qs.execution_count > 0
        ORDER BY qs.total_physical_reads DESC
    """,
}

# DM8 慢查询 SQL
# 说明：
#   - 慢 SQL 视图：V$LONG_EXEC_SQLS（记录执行时间超过阈值的历史SQL）
#   - 会话视图：V$SESSIONS（复数，非 V$SESSION）
#   - SQL 文本：V$SESSIONS 中需用 SF_GET_SESSION_SQL(SESS_ID) 获取
#   - 注意：V$SESSIONS 无 STATUS 列，用 SQL_ID IS NOT NULL 过滤有活动的会话
DM_SLOW_QUERIES = {
    "dm_top_sql_by_time": """
        SELECT
            SQL_TEXT,
            N_RUNS      AS exec_count,
            EXEC_TIME   AS exec_time_ms,
            FINISH_TIME
        FROM V$LONG_EXEC_SQLS
        ORDER BY EXEC_TIME DESC
        LIMIT 20
    """,

    "dm_top_sql_by_disk_read": """
        SELECT
            SQL_TEXT,
            N_RUNS      AS exec_count,
            EXEC_TIME   AS exec_time_ms
        FROM V$LONG_EXEC_SQLS
        WHERE EXEC_TIME > 0
        ORDER BY EXEC_TIME DESC
        LIMIT 15
    """,

    "dm_long_sql": """
        SELECT
            SESS_ID     AS sess_id,
            SF_GET_SESSION_SQL(SESS_ID) AS SQL_TEXT,
            SQL_ID,
            CREATE_TIME
        FROM V$SESSIONS
        WHERE SQL_ID IS NOT NULL
        ORDER BY CREATE_TIME DESC
        LIMIT 15
    """,
}


# ═══════════════════════════════════════════════════════════
#  2. 慢查询分析结果标准化结构
# ═══════════════════════════════════════════════════════════

class SlowQueryResult:
    """
    标准化慢查询分析结果容器。
    所有数据库的分析器返回统一的字段格式，报告层无需感知数据库差异。
    """
    def __init__(self, db_type: str):
        self.db_type = db_type
        self.top_sql_by_latency = []     # 按延迟排序的 Top SQL
        self.top_sql_by_io = []           # 按 IO 排序的 Top SQL
        self.top_sql_by_lock = []         # 按锁等待排序的 Top SQL（MySQL/Oracle）
        self.full_table_scan_sql = []     # 全表扫描 SQL
        self.slow_queries_current = []    # 当前正在执行的慢查询
        self.ai_diagnosis = ''            # AI 诊断建议（由 AIAdvisor 生成）
        self.extension_available = {}      # 各扩展是否可用（如 pg_stat_statements）
        self.summary = {}                  # 汇总统计

    def to_dict(self) -> dict:
        return {
            'db_type': self.db_type,
            'top_sql_by_latency': self.top_sql_by_latency,
            'top_sql_by_io': self.top_sql_by_io,
            'top_sql_by_lock': self.top_sql_by_lock,
            'full_table_scan_sql': self.full_table_scan_sql,
            'slow_queries_current': self.slow_queries_current,
            'ai_diagnosis': self.ai_diagnosis,
            'extension_available': self.extension_available,
            'summary': self.summary,
        }

    def is_empty(self) -> bool:
        """判断是否没有任何慢查询数据（可能是扩展未开启）"""
        return (
            not self.top_sql_by_latency
            and not self.top_sql_by_io
            and not self.top_sql_by_lock
            and not self.full_table_scan_sql
            and not self.slow_queries_current
        )


# ═══════════════════════════════════════════════════════════
#  3. 慢查询 AI 诊断 Prompt 构造器
# ═══════════════════════════════════════════════════════════

def build_slow_query_ai_prompt(db_type: str, result: SlowQueryResult,
                                lang: str = 'zh') -> str:
    """
    为慢查询深度分析构造 AI 诊断 Prompt。
    自动注入 Top SQL、全表扫描、当前长查询等数据，
    要求 AI 输出根因分析和优化建议。
    """
    sep = '=' * 56

    def _fmt_row(r: dict, cols: list) -> str:
        """将字典 r 按 cols 过滤，格式化为文本行"""
        parts = []
        for c in cols:
            v = r.get(c, 'N/A')
            if v is None:
                v = 'N/A'
            parts.append(f"{c}: {v}")
        return '  ' + ', '.join(parts)

    def _render_list(items: list, cols: list, max_rows: int = 10) -> str:
        if not items:
            return '  (无数据)'
        header = '  ' + ' | '.join(cols)
        lines = [header, '  ' + '-' * len(header)]
        for item in items[:max_rows]:
            vals = [str(item.get(c, 'N/A') or 'N/A')[:40] for c in cols]
            lines.append('  ' + ' | '.join(vals))
        return '\n'.join(lines)

    if lang == 'zh':
        db_name = {
            'mysql': 'MySQL', 'pg': 'PostgreSQL',
            'oracle': 'Oracle', 'sqlserver': 'SQL Server', 'dm': '达梦 DM8'
        }.get(db_type, db_type.upper())

        lines = [
            f"你是 {db_name} 数据库慢查询诊断专家。",
            f"{sep}",
            "【一、按延迟排序的 Top SQL】",
            _render_list(result.top_sql_by_latency,
                ['query_text', 'exec_count', 'total_time_sec', 'avg_time_sec', 'rows_scanned'], 10),
            f"{sep}",
            "【二、全表扫描 SQL（高风险）】",
            _render_list(result.full_table_scan_sql,
                ['query_text', 'exec_count', 'rows_scanned', 'filter_ratio_pct'], 10),
            f"{sep}",
            "【三、按 IO 排序的 Top SQL】",
            _render_list(result.top_sql_by_io,
                ['query_text', 'exec_count', 'total_time_sec', 'blk_read_sec', 'rows_read'], 10),
        ]

        if result.top_sql_by_lock:
            lines += [
                f"{sep}",
                "【四、锁等待严重的 SQL】",
                _render_list(result.top_sql_by_lock,
                    ['query_text', 'exec_count', 'total_lock_sec', 'avg_lock_sec'], 10),
            ]

        if result.slow_queries_current:
            lines += [
                f"{sep}",
                "【五、当前正在执行的慢查询】",
                _render_list(result.slow_queries_current,
                    ['pid', 'duration', 'state', 'query_text'], 10),
            ]

        lines += [
            f"{sep}",
            "【六、汇总统计】",
            _render_list([result.summary], list(result.summary.keys())) if result.summary else '  (无)',
            f"{sep}",
            "【七、诊断要求】",
            "请对以上慢查询数据进行深度分析，要求：",
            "1. 识别最严重的 3~5 条慢查询，分析其根因（索引缺失？统计信息过时？参数配置不当？）",
            "2. 全表扫描 queries 分析：评估影响范围，给出添加索引或改写 SQL 的具体建议",
            "3. IO 密集型 queries：分析是否可以通过优化减少磁盘读写",
            "4. 当前长查询：判断是否需要紧急 Kill，并分析阻塞原因",
            "5. 每条 query 给出：根因定位 → 影响评估 → 具体修复方案（含参考 SQL）",
            "6. 最后给出该数据库慢查询整体评价及短期/中期优化路线图",
            "",
            "格式要求（直接输出 Markdown，不要任何前缀）：",
            "## 重点慢查询（Top 5）",
            "### 1. [Query 摘要]",
            "**根因**: ...",
            "**影响**: ...",
            "**优化建议**: ...",
            "",
            "## 全表扫描分析",
            "## IO 优化建议",
            "## 紧急处理（如有长查询）",
            "## 整体评价与优化路线图",
        ]
    else:
        # English
        db_name = {
            'mysql': 'MySQL', 'pg': 'PostgreSQL',
            'oracle': 'Oracle', 'sqlserver': 'SQL Server', 'dm': 'DM8'
        }.get(db_type, db_type.upper())

        lines = [
            f"You are a {db_name} slow query diagnosis expert.",
            f"{sep}",
            "[I. Top SQL by Latency]",
            _render_list(result.top_sql_by_latency,
                ['query_text', 'exec_count', 'total_time_sec', 'avg_time_sec', 'rows_scanned'], 10),
            f"{sep}",
            "[II. Full Table Scan SQL (High Risk)]",
            _render_list(result.full_table_scan_sql,
                ['query_text', 'exec_count', 'rows_scanned', 'filter_ratio_pct'], 10),
            f"{sep}",
            "[III. Top SQL by IO]",
            _render_list(result.top_sql_by_io,
                ['query_text', 'exec_count', 'total_time_sec', 'blk_read_sec', 'rows_read'], 10),
        ]

        if result.top_sql_by_lock:
            lines += [
                f"{sep}",
                "[IV. SQL with High Lock Wait]",
                _render_list(result.top_sql_by_lock,
                    ['query_text', 'exec_count', 'total_lock_sec', 'avg_lock_sec'], 10),
            ]

        if result.slow_queries_current:
            lines += [
                f"{sep}",
                "[V. Currently Running Slow Queries]",
                _render_list(result.slow_queries_current,
                    ['pid', 'duration', 'state', 'query_text'], 10),
            ]

        lines += [
            f"{sep}",
            "[VI. Summary Statistics]",
            _render_list([result.summary], list(result.summary.keys())) if result.summary else '  (none)',
            f"{sep}",
            "[VII. Diagnosis Requirements]",
            "Provide in-depth analysis of the slow query data above:",
            "1. Identify the 3~5 most severe slow queries and analyze root causes",
            "2. Full table scan queries: assess impact and provide specific recommendations",
            "3. IO-intensive queries: suggest ways to reduce disk reads",
            "4. Currently running long queries: determine if emergency Kill is needed",
            "5. For each query: Root Cause → Impact → Fix (with reference SQL)",
            "6. Overall slow query health rating and short/medium-term optimization roadmap",
            "",
            "Format (output Markdown directly, no prefixes):",
            "## Top Slow Queries (Top 5)",
            "### 1. [Query Summary]",
            "**Root Cause**: ...",
            "**Impact**: ...",
            "**Recommendation**: ...",
            "",
            "## Full Table Scan Analysis",
            "## IO Optimization Recommendations",
            "## Emergency Actions (if any)",
            "## Overall Assessment & Optimization Roadmap",
        ]

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════
#  4. 各数据库慢查询分析器
# ═══════════════════════════════════════════════════════════

class BaseSlowQueryAnalyzer:
    """
    各数据库慢查询分析器抽象基类。
    子类需实现：
      - collect(conn)       : 采集慢查询原始数据
      - normalize(raw)      : 标准化为 SlowQueryResult
    """
    DB_TYPE = 'base'

    def __init__(self):
        self.db_type = self.DB_TYPE
        self._result = SlowQueryResult(self.DB_TYPE)

    def analyze(self, conn, ai_advisor=None, lang='zh') -> SlowQueryResult:
        """
        执行完整慢查询分析流程：
        1. 采集原始数据
        2. 标准化
        3. AI 诊断（如启用）
        """
        raw = self.collect(conn)
        self._result = self.normalize(raw)
        if ai_advisor and ai_advisor.enabled:
            prompt = build_slow_query_ai_prompt(self.DB_TYPE, self._result, lang)
            try:
                self._result.ai_diagnosis = ai_advisor._call_llm(prompt, timeout=60)
            except Exception as e:
                self._result.ai_diagnosis = ''
                print("⚠️ 慢查询 AI 诊断失败 [%s]: %s" % (self.DB_TYPE, e))
        return self._result

    def collect(self, conn):
        """子类实现：采集慢查询原始数据"""
        raise NotImplementedError

    def normalize(self, raw: dict) -> SlowQueryResult:
        """子类实现：将原始数据标准化"""
        raise NotImplementedError

    def _exec_sql(self, conn, sql: str, fetch=True) -> list:
        """执行 SQL 并返回结果字典列表"""
        try:
            cursor = conn.cursor()
            cursor.execute(sql)
            if fetch:
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
            return []
        except Exception as e:
            # 权限不足或扩展未开启时打印具体错误再降级
            import logging
            logging.getLogger('slow_query').warning(
                'SQL exec failed (db=%s): %s', self.db_type, str(e))
            # psycopg2 在查询失败后事务进入 aborted 状态，必须回滚
            # 否则后续所有 SQL 都会报 "current transaction is aborted"
            try:
                conn.rollback()
            except Exception:
                pass
            return []


class MySQLSlowQueryAnalyzer(BaseSlowQueryAnalyzer):
    DB_TYPE = 'mysql'

    def collect(self, conn) -> dict:
        result = {}

        # 1. Top SQL by latency（需要 performance_schema.events_statements_summary_by_digest）
        try:
            result['top_by_latency'] = self._exec_sql(conn,
                MYSQL_SLOW_QUERIES['mysql_top_by_latency'])
        except Exception:
            result['top_by_latency'] = []

        # 2. 全表扫描 SQL
        try:
            result['full_table_scan'] = self._exec_sql(conn,
                MYSQL_SLOW_QUERIES['mysql_top_full_table_scan'])
        except Exception:
            result['full_table_scan'] = []

        # 3. 按锁等待排序（AVG_LOCK_TIME 可能在早期 MySQL 中不存在）
        try:
            result['top_by_lock'] = self._exec_sql(conn,
                MYSQL_SLOW_QUERIES['mysql_top_by_lock'])
        except Exception:
            result['top_by_lock'] = []

        # 4. 按临时表使用量（可能溢出到磁盘）
        try:
            result['top_tmp_tables'] = self._exec_sql(conn,
                MYSQL_SLOW_QUERIES['mysql_top_tmp_tables'])
        except Exception:
            result['top_tmp_tables'] = []

        # 5. 按排序操作量
        try:
            result['top_sorting'] = self._exec_sql(conn,
                MYSQL_SLOW_QUERIES['mysql_top_sorting'])
        except Exception:
            result['top_sorting'] = []

        # 6. 当前 slow_log（如开启）
        try:
            result['slow_log'] = self._exec_sql(conn,
                MYSQL_SLOW_QUERIES['mysql_slow_log_recent'])
        except Exception:
            result['slow_log'] = []

        # 检查 performance_schema 是否启用
        result['ps_enabled'] = self._check_ps_enabled(conn)

        return result

    def _check_ps_enabled(self, conn) -> bool:
        """检查 performance_schema 是否开启"""
        try:
            rows = self._exec_sql(conn,
                "SELECT COUNT(*) AS cnt FROM performance_schema.events_statements_summary_by_digest LIMIT 1")
            return True
        except Exception:
            return False

    def normalize(self, raw: dict) -> SlowQueryResult:
        r = SlowQueryResult('mysql')
        r.extension_available['performance_schema'] = raw.get('ps_enabled', False)

        # 标准化列名（兼容不同数据库字段名）
        for row in raw.get('top_by_latency', []):
            r.top_sql_by_latency.append({
                'query_text': self._digest_text(row.get('query_sample_text', '')),
                'exec_count': row.get('exec_count', 0),
                'total_time_sec': round(float(row.get('total_latency_sec') or 0), 3),
                'avg_time_sec': round(float(row.get('avg_latency_sec') or 0), 3),
                'rows_scanned': row.get('rows_scanned', 0),
                'rows_sent': row.get('rows_sent', 0),
                'tmp_disk_tables': row.get('tmp_disk_tables', 0),
                'sort_merge_passes': row.get('sort_merge_passes', 0),
            })

        for row in raw.get('full_table_scan', []):
            r.full_table_scan_sql.append({
                'query_text': self._digest_text(row.get('query_sample_text', '')),
                'exec_count': row.get('exec_count', 0),
                'rows_scanned': row.get('rows_scanned', 0),
                'rows_sent': row.get('rows_sent', 0),
                'filter_ratio_pct': round(float(row.get('filter_ratio_pct') or 0), 2),
            })

        for row in raw.get('top_by_lock', []):
            r.top_sql_by_lock.append({
                'query_text': self._digest_text(row.get('query_sample_text', '')),
                'exec_count': row.get('exec_count', 0),
                'total_lock_sec': round(float(row.get('total_lock_sec') or 0), 3),
                'avg_lock_sec': round(float(row.get('avg_lock_sec') or 0), 3),
            })

        # slow_log 当前查询（从 slow_log）
        for row in raw.get('slow_log', []):
            r.slow_queries_current.append({
                'start_time': str(row.get('start_time', '')),
                'query_time': str(row.get('query_time', '')),
                'lock_time': str(row.get('lock_time', '')),
                'rows_sent': row.get('rows_sent', 0),
                'rows_examined': row.get('rows_examined', 0),
                'db': row.get('db', ''),
                'sql_text': str(row.get('sql_text', '')),
            })

        # 汇总
        total = len(r.top_sql_by_latency)
        if total > 0:
            r.summary = {
                'total_sampled_queries': total,
                'has_full_table_scan': len(r.full_table_scan_sql) > 0,
                'has_tmp_disk_spill': any(x.get('tmp_disk_tables', 0) > 0 for x in r.top_sql_by_latency),
                'has_lock_contention': len(r.top_sql_by_lock) > 0,
            }

        return r

    def _digest_text(self, text: str) -> str:
        """将 DIGEST_TEXT 标准化：去除多余空白，便于阅读"""
        if not text:
            return ''
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:300]  # 截断超长 query


class PGSlowQueryAnalyzer(BaseSlowQueryAnalyzer):
    DB_TYPE = 'pg'

    def collect(self, conn) -> dict:
        result = {}

        # 1. Top SQL by total time（需要 pg_stat_statements）
        result['top_by_total_time'] = self._exec_sql(conn,
            PG_SLOW_QUERIES['pg_top_by_total_time'])

        # 2. Top SQL by avg time
        result['top_by_avg_time'] = self._exec_sql(conn,
            PG_SLOW_QUERIES['pg_top_by_avg_time'])

        # 3. Top SQL by IO
        result['top_by_io'] = self._exec_sql(conn,
            PG_SLOW_QUERIES['pg_top_by_io'])

        # 4. Top SQL by temp blocks
        result['top_by_temp'] = self._exec_sql(conn,
            PG_SLOW_QUERIES['pg_top_by_temp'])

        # 5. 当前长查询快照
        result['long_running'] = self._exec_sql(conn,
            PG_SLOW_QUERIES['pg_long_running'])

        # 检查 pg_stat_statements 是否启用
        result['pg_statements_enabled'] = self._check_extension(conn)

        return result

    def _check_extension(self, conn) -> bool:
        try:
            rows = self._exec_sql(conn,
                "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements' LIMIT 1")
            return len(rows) > 0
        except Exception:
            return False

    def normalize(self, raw: dict) -> SlowQueryResult:
        r = SlowQueryResult('pg')
        r.extension_available['pg_stat_statements'] = raw.get('pg_statements_enabled', False)

        # Top by total time
        for row in raw.get('top_by_total_time', []):
            r.top_sql_by_latency.append({
                'query_text': str(row.get('query_text', ''))[:300],
                'exec_count': row.get('calls', 0),
                'total_time_sec': round(float(row.get('total_time_sec') or 0), 3),
                'avg_time_sec': round(float(row.get('mean_time_sec') or 0), 3),
                'max_time_sec': round(float(row.get('max_time_sec') or 0), 3),
                'rows': row.get('rows', 0),
                'blk_read': row.get('shared_blks_read', 0),
                'blk_hit': row.get('shared_blks_hit', 0),
            })

        # Top by IO
        for row in raw.get('top_by_io', []):
            r.top_sql_by_io.append({
                'query_text': str(row.get('query_text', ''))[:300],
                'exec_count': row.get('calls', 0),
                'total_time_sec': round(float(row.get('total_time_sec') or 0), 3),
                'blk_read_sec': round(float(row.get('blk_read_sec') or 0), 3),
                'blk_write_sec': round(float(row.get('blk_write_sec') or 0), 3),
                'rows_read': row.get('shared_blks_read', 0),
            })

        # 当前长查询
        for row in raw.get('long_running', []):
            dur = str(row.get('duration', '0'))
            r.slow_queries_current.append({
                'pid': row.get('pid', ''),
                'duration': dur,
                'state': row.get('state', ''),
                'query_text': str(row.get('query_text', ''))[:200],
                'usename': row.get('usename', ''),
                'wait_event_type': row.get('wait_event_type', ''),
                'wait_event': row.get('wait_event', ''),
            })

        # 汇总
        if r.top_sql_by_latency:
            r.summary = {
                'total_sampled_queries': len(r.top_sql_by_latency),
                'has_high_io': len(r.top_sql_by_io) > 0,
                'has_temp_spill': len(raw.get('top_by_temp', [])) > 0,
                'current_long_running': len(r.slow_queries_current),
            }

        return r


class OracleSlowQueryAnalyzer(BaseSlowQueryAnalyzer):
    DB_TYPE = 'oracle'

    def collect(self, conn) -> dict:
        result = {}

        result['top_by_buffer_gets'] = self._exec_sql(conn,
            ORACLE_SLOW_QUERIES['ora_top_sql_by_buffer_gets'])

        result['top_by_disk_reads'] = self._exec_sql(conn,
            ORACLE_SLOW_QUERIES['ora_top_sql_by_disk_reads'])

        result['top_by_elapsed'] = self._exec_sql(conn,
            ORACLE_SLOW_QUERIES['ora_top_sql_by_elapsed'])

        result['full_table_scan'] = self._exec_sql(conn,
            ORACLE_SLOW_QUERIES['ora_sql_with_full_table_scan'])

        return result

    def normalize(self, raw: dict) -> SlowQueryResult:
        r = SlowQueryResult('oracle')

        for row in raw.get('top_by_buffer_gets', []):
            r.top_sql_by_latency.append({
                'sql_id': row.get('sql_id', ''),
                'query_text': str(row.get('sql_text', ''))[:300],
                'exec_count': row.get('executions', 0),
                'total_time_sec': round(float(row.get('elapsed_sec') or 0), 3),
                'avg_time_sec': round(float(row.get('avg_elapsed_sec') or 0), 3),
                'buffer_gets': row.get('buffer_gets', 0),
                'disk_reads': row.get('disk_reads', 0),
                'rows_processed': row.get('rows_processed', 0),
            })

        for row in raw.get('top_by_disk_reads', []):
            r.top_sql_by_io.append({
                'sql_id': row.get('sql_id', ''),
                'query_text': str(row.get('sql_text', ''))[:300],
                'exec_count': row.get('executions', 0),
                'total_time_sec': round(float(row.get('elapsed_sec') or 0), 3),
                'disk_reads': row.get('disk_reads', 0),
                'buffer_gets': row.get('buffer_gets', 0),
                'rows_processed': row.get('rows_processed', 0),
            })

        for row in raw.get('full_table_scan', []):
            r.full_table_scan_sql.append({
                'sql_id': row.get('sql_id', ''),
                'query_text': str(row.get('sql_text', ''))[:300],
                'exec_count': row.get('executions', 0),
                'buffer_gets': row.get('buffer_gets', 0),
                'disk_reads': row.get('disk_reads', 0),
                'rows_processed': row.get('rows_processed', 0),
            })

        if r.top_sql_by_latency:
            r.summary = {
                'total_sampled_queries': len(r.top_sql_by_latency),
                'has_full_table_scan': len(r.full_table_scan_sql) > 0,
            }

        return r


class SQLServerSlowQueryAnalyzer(BaseSlowQueryAnalyzer):
    DB_TYPE = 'sqlserver'

    def collect(self, conn) -> dict:
        result = {}

        result['top_by_cpu'] = self._exec_sql(conn,
            SQLSERVER_SLOW_QUERIES['mssql_top_by_cpu'])

        result['top_by_logical_reads'] = self._exec_sql(conn,
            SQLSERVER_SLOW_QUERIES['mssql_top_by_logical_reads'])

        result['top_by_elapsed'] = self._exec_sql(conn,
            SQLSERVER_SLOW_QUERIES['mssql_top_by_elapsed'])

        result['top_by_physical_reads'] = self._exec_sql(conn,
            SQLSERVER_SLOW_QUERIES['mssql_top_by_physical_reads'])

        return result

    def normalize(self, raw: dict) -> SlowQueryResult:
        r = SlowQueryResult('sqlserver')

        for row in raw.get('top_by_cpu', []):
            r.top_sql_by_latency.append({
                'query_text': str(row.get('query_text', ''))[:300],
                'exec_count': row.get('execution_count', 0),
                'total_cpu_ms': row.get('total_cpu_ms', 0),
                'total_elapsed_ms': row.get('total_elapsed_ms', 0),
                'total_logical_reads': row.get('total_logical_reads', 0),
                'total_physical_reads': row.get('total_physical_reads', 0),
                'db_name': row.get('db_name', ''),
                'last_execution_time': str(row.get('last_execution_time', '')),
            })

        for row in raw.get('top_by_logical_reads', []):
            r.top_sql_by_io.append({
                'query_text': str(row.get('query_text', ''))[:300],
                'exec_count': row.get('execution_count', 0),
                'total_logical_reads': row.get('total_logical_reads', 0),
                'total_physical_reads': row.get('total_physical_reads', 0),
                'total_elapsed_ms': row.get('total_elapsed_ms', 0),
                'db_name': row.get('db_name', ''),
            })

        for row in raw.get('top_by_physical_reads', []):
            r.full_table_scan_sql.append({
                'query_text': str(row.get('query_text', ''))[:300],
                'exec_count': row.get('execution_count', 0),
                'total_physical_reads': row.get('total_physical_reads', 0),
                'total_logical_reads': row.get('total_logical_reads', 0),
                'db_name': row.get('db_name', ''),
            })

        if r.top_sql_by_latency:
            r.summary = {
                'total_sampled_queries': len(r.top_sql_by_latency),
                'has_high_physical_reads': len(r.full_table_scan_sql) > 0,
            }

        return r


class DMSlowQueryAnalyzer(BaseSlowQueryAnalyzer):
    DB_TYPE = 'dm'

    def collect(self, conn) -> dict:
        result = {}

        result['top_by_time'] = self._exec_sql(conn,
            DM_SLOW_QUERIES['dm_top_sql_by_time'])

        result['top_by_disk_read'] = self._exec_sql(conn,
            DM_SLOW_QUERIES['dm_top_sql_by_disk_read'])

        result['long_sql'] = self._exec_sql(conn,
            DM_SLOW_QUERIES['dm_long_sql'])

        return result

    def normalize(self, raw: dict) -> SlowQueryResult:
        r = SlowQueryResult('dm')

        for row in raw.get('top_by_time', []):
            r.top_sql_by_latency.append({
                'query_text': str(row.get('SQL_TEXT', ''))[:300],
                'exec_count': row.get('exec_count', 0),
                'exec_time_ms': row.get('exec_time_ms', 0),
            })

        for row in raw.get('top_by_disk_read', []):
            r.top_sql_by_io.append({
                'query_text': str(row.get('SQL_TEXT', ''))[:300],
                'exec_count': row.get('exec_count', 0),
                'exec_time_ms': row.get('exec_time_ms', 0),
            })

        for row in raw.get('long_sql', []):
            r.slow_queries_current.append({
                'sess_id': row.get('sess_id', ''),
                'exec_time_ms': 0,
                'state': row.get('state', ''),
                'query_text': str(row.get('SQL_TEXT', ''))[:200],
            })

        if r.top_sql_by_latency:
            r.summary = {
                'total_sampled_queries': len(r.top_sql_by_latency),
                'current_long_sql': len(r.slow_queries_current),
            }

        return r


# ═══════════════════════════════════════════════════════════
#  5. 工厂函数
# ═══════════════════════════════════════════════════════════

def get_slow_query_analyzer(db_type: str) -> BaseSlowQueryAnalyzer:
    """
    根据数据库类型返回对应的慢查询分析器。

    :param db_type: 'mysql' | 'pg' | 'oracle' | 'sqlserver' | 'dm'
    :return: 对应数据库的 BaseSlowQueryAnalyzer 子类实例
    """
    TABLE = {
        'mysql':     MySQLSlowQueryAnalyzer,
        'pg':        PGSlowQueryAnalyzer,
        'oracle':    OracleSlowQueryAnalyzer,
        'sqlserver': SQLServerSlowQueryAnalyzer,
        'dm':        DMSlowQueryAnalyzer,
    }
    cls = TABLE.get(db_type.lower())
    if cls is None:
        raise ValueError(f"Unsupported db_type for slow query analysis: {db_type}")
    return cls()
