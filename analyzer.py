# -*- coding: utf-8 -*-
#
# Copyright (c) 2024 DBCheck Contributors
# sdfiyon@gmail.com
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License.
# See LICENSE or visit https://opensource.org/licenses/MIT for full license text.
#
"""
DBCheck 增强智能分析模块
========================
提供三个核心能力：
1. smart_analyze_mysql / smart_analyze_pg  —— 16+ 条风险规则 + 修复 SQL
2. HistoryManager   —— 历史指标存储与趋势数据生成
3. AIAdvisor        —— 本地 Ollama 诊断适配器（仅支持本地部署）

安全说明：
- AI 诊断功能仅支持本地部署的 Ollama，不支持任何远程 AI API
- 所有数据在本地处理，不会发送到第三方服务器
"""

import os
import json
import time
import hashlib
from datetime import datetime

# 忽略的挂载点（外接 ISO/Media 光盘等分区，不应计入磁盘使用率）
IGNORE_MOUNTS = {'/mnt/iso', '/media', '/run/media', '/iso', '/cdrom'}


# ═══════════════════════════════════════════════════════
#  1. 智能风险分析（MySQL）
# ═══════════════════════════════════════════════════════

def smart_analyze_mysql(context: dict) -> list:
    """
    对 MySQL 巡检结果执行 15+ 条增强风险规则分析。

    在原有 3 条规则基础上扩展到覆盖：连接、缓存、日志、锁、
    慢查询、用户安全、复制、磁盘、内存等关键维度。

    每条结果字典包含：
        col1  - 风险项名称
        col2  - 风险等级（高风险/中风险/低风险/建议）
        col3  - 详细描述
        col4  - 处理优先级（高/中/低）
        col5  - 负责人（DBA/系统管理员）
        fix_sql - 修复参考 SQL（可直接复制执行，可为空字符串）
    """
    issues = []

    def _val(key, sub='Value', default=None):
        """从 context 中安全取单值"""
        data = context.get(key, [])
        if data and isinstance(data, list) and data[0]:
            return data[0].get(sub, default)
        return default

    def _int(v, default=0):
        try:
            return int(str(v).replace(',', ''))
        except Exception:
            return default

    def _float(v, default=0.0):
        try:
            return float(str(v).replace(',', '').replace('%', ''))
        except Exception:
            return default

    # ── 1. 连接数使用率 ──────────────────────────────────
    max_used = _int(_val('max_used_connections'))
    max_conn = _int(_val('max_connections'), 151)
    if max_conn > 0:
        conn_pct = max_used / max_conn * 100
        if conn_pct > 90:
            issues.append({
                'col1': '连接数使用率', 'col2': '高风险',
                'col3': f'历史最大连接数使用率高达 {conn_pct:.1f}%（{max_used}/{max_conn}），极有可能出现拒绝连接',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': f'SET GLOBAL max_connections = {min(max_conn * 2, 2000)};'
            })
        elif conn_pct > 80:
            issues.append({
                'col1': '连接数使用率', 'col2': '中风险',
                'col3': f'连接数使用率达 {conn_pct:.1f}%（{max_used}/{max_conn}），建议提前关注',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': f'SET GLOBAL max_connections = {int(max_conn * 1.5)};'
            })

    # ── 2. 当前活跃连接异常进程 ─────────────────────────
    processlist = context.get('processlist', [])
    long_queries = [p for p in processlist if _int(p.get('Time', 0)) > 60 and p.get('Command', '') not in ('Sleep', 'Binlog Dump')]
    if long_queries:
        issues.append({
            'col1': '长时间运行的 SQL', 'col2': '高风险',
            'col3': f'发现 {len(long_queries)} 个执行超过 60 秒的 SQL，可能导致锁等待和性能下降',
            'col4': '高', 'col5': 'DBA',
            'fix_sql': '\n'.join([f"KILL {p.get('Id', '')}; -- {str(p.get('Info',''))[:60]}" for p in long_queries[:5]])
        })

    # ── 3. 慢查询日志未开启 ──────────────────────────────
    slow_log = _val('slow_query_log')
    if slow_log and str(slow_log).upper() in ('OFF', '0'):
        issues.append({
            'col1': '慢查询日志未开启', 'col2': '建议',
            'col3': '慢查询日志已关闭，无法追踪性能问题，建议开启',
            'col4': '低', 'col5': 'DBA',
            'fix_sql': "SET GLOBAL slow_query_log = 'ON';\nSET GLOBAL long_query_time = 1;"
        })

    # ── 4. binlog 未开启（生产环境风险） ────────────────
    log_bin = _val('log_bin')
    if log_bin and str(log_bin).upper() in ('OFF', '0'):
        issues.append({
            'col1': 'binlog 未开启', 'col2': '中风险',
            'col3': 'binlog 未开启，无法实现基于时间点的数据恢复，生产环境建议开启',
            'col4': '中', 'col5': 'DBA',
            'fix_sql': '-- 需在 my.cnf 中添加：\n-- log_bin = /var/log/mysql/mysql-bin.log\n-- server-id = 1\n-- 然后重启 MySQL'
        })

    # ── 5. binlog 过期时间 ───────────────────────────────
    expire_days = _int(_val('expire_logs_days'), -1)
    if expire_days == 0:
        issues.append({
            'col1': 'binlog 永不过期', 'col2': '中风险',
            'col3': 'expire_logs_days=0 表示 binlog 永不自动清理，可能导致磁盘耗尽',
            'col4': '中', 'col5': 'DBA',
            'fix_sql': "SET GLOBAL expire_logs_days = 7;"
        })

    # ── 6. InnoDB 缓冲池大小 ─────────────────────────────
    buf_val = _val('innodb_buffer_pool_size')
    if buf_val:
        buf_bytes = _int(buf_val)
        # 如果是带单位的字符串（如 '128M'），尝试解析
        if buf_bytes == 0 and isinstance(buf_val, str):
            s = buf_val.upper()
            if s.endswith('G'):
                buf_bytes = int(float(s[:-1]) * 1024**3)
            elif s.endswith('M'):
                buf_bytes = int(float(s[:-1]) * 1024**2)
        buf_gb = buf_bytes / 1024**3 if buf_bytes > 0 else 0
        if 0 < buf_gb < 1:
            issues.append({
                'col1': 'InnoDB 缓冲池偏小', 'col2': '中风险',
                'col3': f'innodb_buffer_pool_size 仅 {buf_val}，建议设置为物理内存的 50%~70%',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': '-- 建议修改 my.cnf：\n-- innodb_buffer_pool_size = 4G  # 根据实际内存调整\n-- 或在线调整（MySQL 5.7+）：\nSET GLOBAL innodb_buffer_pool_size = 4294967296;  -- 4G'
            })

    # ── 7. 查询缓存（MySQL 8.0 已废弃） ─────────────────
    query_cache = context.get('query_cache', [])
    for row in query_cache:
        if row.get('Variable_name') == 'query_cache_type' and str(row.get('Value', '')).upper() == 'ON':
            issues.append({
                'col1': '查询缓存已开启（不建议）', 'col2': '建议',
                'col3': 'query_cache 在高并发场景下会造成严重锁竞争，MySQL 8.0 已彻底移除，建议关闭',
                'col4': '低', 'col5': 'DBA',
                'fix_sql': "SET GLOBAL query_cache_type = 0;\nSET GLOBAL query_cache_size = 0;"
            })
            break

    # ── 8. 表锁等待比例 ──────────────────────────────────
    immediate = _int(_val('table_locks_immediate'))
    waited = _int(_val('table_locks_waited'))
    if immediate + waited > 0:
        lock_pct = waited / (immediate + waited) * 100
        if lock_pct > 5:
            issues.append({
                'col1': '表锁等待比例过高', 'col2': '高风险',
                'col3': f'表锁等待比例达 {lock_pct:.2f}%（等待次数 {waited}），存在大量锁竞争',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': '-- 排查锁等待来源：\nSHOW FULL PROCESSLIST;\nSHOW OPEN TABLES WHERE In_use > 0;\nSELECT * FROM information_schema.INNODB_LOCKS;'
            })

    # ── 9. 中止连接数 ────────────────────────────────────
    aborted = _int(_val('aborted_connections'))
    if aborted > 100:
        issues.append({
            'col1': '异常中止连接数较多', 'col2': '中风险',
            'col3': f'累计中止连接数达 {aborted}，可能存在连接池配置异常或网络问题',
            'col4': '中', 'col5': 'DBA',
            'fix_sql': '-- 查看详情：\nSHOW GLOBAL STATUS LIKE "Aborted%";\n-- 检查 interactive_timeout / wait_timeout 设置：\nSHOW VARIABLES LIKE "%timeout%";'
        })

    # ── 10. 数据库用户安全 ───────────────────────────────
    users = context.get('mysql_users', [])
    for u in users:
        host = str(u.get('Host', ''))
        plugin = str(u.get('plugin', ''))
        uname = str(u.get('User', ''))
        # 空密码检测（authentication_string 为空）
        auth = str(u.get('authentication_string', '') or '')
        if not auth and uname != 'mysql.sys':
            issues.append({
                'col1': f'用户 {uname}@{host} 空密码', 'col2': '高风险',
                'col3': f'数据库用户 {uname}@{host} 未设置密码，存在严重安全风险',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': f"ALTER USER '{uname}'@'{host}' IDENTIFIED BY '强密码请替换';"
            })
        # 允许所有主机连接
        if host == '%' and uname == 'root':
            issues.append({
                'col1': 'root 用户允许所有主机连接', 'col2': '高风险',
                'col3': "root@'%' 允许从任意主机登录，存在严重安全风险，建议限制为本地",
                'col4': '高', 'col5': 'DBA',
                'fix_sql': "-- 删除 root@% 并仅保留 root@localhost：\nDROP USER 'root'@'%';\nCREATE USER 'root'@'localhost' IDENTIFIED BY '强密码请替换';\nGRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost' WITH GRANT OPTION;"
            })

    # ── 11. 复制延迟 ─────────────────────────────────────
    slave_status = context.get('slave_status', [])
    if slave_status and slave_status[0]:
        lag = _int(slave_status[0].get('Seconds_Behind_Master', 0))
        sql_running = str(slave_status[0].get('Slave_SQL_Running', ''))
        io_running = str(slave_status[0].get('Slave_IO_Running', ''))
        if sql_running.upper() != 'YES' or io_running.upper() != 'YES':
            issues.append({
                'col1': '复制线程异常', 'col2': '高风险',
                'col3': f'复制状态异常：IO线程={io_running}，SQL线程={sql_running}',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': 'SHOW SLAVE STATUS\\G\n-- 如需重启复制：\nSTOP SLAVE; START SLAVE;'
            })
        elif lag > 60:
            issues.append({
                'col1': '主从复制延迟过高', 'col2': '中风险',
                'col3': f'从库延迟 {lag} 秒，数据同步滞后，读操作可能读到旧数据',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': 'SHOW SLAVE STATUS\\G\nSHOW PROCESSLIST;'
            })

    # ── 12. 打开文件数 ────────────────────────────────────
    open_files = _int(_val('open_files_limit'))
    opened_tables = _int(_val('opened_tables'))
    table_cache = _int(_val('table_open_cache'), 2000)
    if opened_tables > table_cache * 0.8:
        issues.append({
            'col1': '表缓存命中率低', 'col2': '中风险',
            'col3': f'已打开表数({opened_tables}) 接近 table_open_cache({table_cache})，可能频繁开关文件句柄',
            'col4': '中', 'col5': 'DBA',
            'fix_sql': f'SET GLOBAL table_open_cache = {min(table_cache * 2, 8192)};'
        })

    # ── 13. 内存使用率 ────────────────────────────────────
    mem_usage = _float(context.get('system_info', {}).get('memory', {}).get('usage_percent', 0))
    if mem_usage > 90:
        issues.append({
            'col1': '系统内存使用率', 'col2': '高风险',
            'col3': f'系统内存使用率 {mem_usage:.1f}%，超过 90% 可能触发 OOM Killer',
            'col4': '高', 'col5': '系统管理员',
            'fix_sql': ''
        })
    elif mem_usage > 80:
        issues.append({
            'col1': '系统内存使用率', 'col2': '中风险',
            'col3': f'系统内存使用率 {mem_usage:.1f}%，建议关注内存增长趋势',
            'col4': '中', 'col5': '系统管理员',
            'fix_sql': ''
        })

    # ── 14. 磁盘使用率 ────────────────────────────────────
    for disk in context.get('system_info', {}).get('disk_list', []):
        usage = _float(disk.get('usage_percent', 0))
        mp = disk.get('mountpoint', '/')
        if mp in IGNORE_MOUNTS:
            continue
        if usage > 90:
            issues.append({
                'col1': f'磁盘空间紧张 ({mp})', 'col2': '高风险',
                'col3': f'磁盘 {mp} 使用率 {usage:.1f}%，可能导致数据库写入失败',
                'col4': '高', 'col5': '系统管理员',
                'fix_sql': f'-- 清理旧 binlog：\nPURGE BINARY LOGS BEFORE DATE_SUB(NOW(), INTERVAL 3 DAY);\n-- 查看数据库占用：\nSELECT table_schema, ROUND(SUM(data_length+index_length)/1024/1024,2) AS mb FROM information_schema.tables GROUP BY 1 ORDER BY 2 DESC LIMIT 10;'
            })
        elif usage > 80:
            issues.append({
                'col1': f'磁盘空间预警 ({mp})', 'col2': '中风险',
                'col3': f'磁盘 {mp} 使用率 {usage:.1f}%，建议及时清理或扩容',
                'col4': '中', 'col5': '系统管理员',
                'fix_sql': ''
            })

    # ── 15. innodb_flush_log_at_trx_commit ───────────────
    flush_val = _val('innodb_flush_log_at_trx_commit')
    if flush_val and str(flush_val) == '0':
        issues.append({
            'col1': 'innodb_flush_log_at_trx_commit=0', 'col2': '高风险',
            'col3': '设置为 0 时 MySQL 崩溃可能丢失最多 1 秒的事务，生产环境建议设为 1',
            'col4': '高', 'col5': 'DBA',
            'fix_sql': "SET GLOBAL innodb_flush_log_at_trx_commit = 1;"
        })

    # ── 16. 字符集不一致 ──────────────────────────────────
    charset = _val('character_set_database')
    if charset and charset.lower() not in ('utf8mb4', 'utf8'):
        issues.append({
            'col1': '数据库字符集非 UTF8', 'col2': '建议',
            'col3': f'当前字符集为 {charset}，建议统一使用 utf8mb4 以支持 emoji 和多语言',
            'col4': '低', 'col5': 'DBA',
            'fix_sql': "-- 修改 my.cnf：\n-- character-set-server = utf8mb4\n-- collation-server = utf8mb4_unicode_ci"
        })

    # ── 17. 慢查询深度分析（P2）─────────────────────────────
    # 利用 performance_schema.events_statements_summary_by_digest 数据
    sq_result = context.get('slow_query_result', {})
    if sq_result:
        ext_available = sq_result.get('extension_available', {})
        # performance_schema 未开启时给出建议
        if not ext_available.get('performance_schema', False):
            issues.append({
                'col1': 'performance_schema 未开启', 'col2': '建议',
                'col3': 'performance_schema 未开启，无法进行慢查询深度分析。建议在 my.cnf 中添加 performance_schema=ON 并重启',
                'col4': '低', 'col5': 'DBA',
                'fix_sql': '-- 在 my.cnf 中添加后重启：\n-- performance_schema=ON\n-- 或在线启用（部分参数）：\n-- SET GLOBAL performance_schema_events_statements_history_size = 10000;'
            })

        top_latency = sq_result.get('top_sql_by_latency', [])
        full_scan = sq_result.get('full_table_scan_sql', [])
        top_lock = sq_result.get('top_sql_by_lock', [])

        # Top SQL 整体延迟偏高
        if top_latency:
            max_latency = max((float(x.get('total_time_sec') or 0) for x in top_latency), default=0)
            if max_latency > 300:  # > 5 分钟总延迟
                issues.append({
                    'col1': 'Top SQL 总体延迟偏高', 'col2': '高风险',
                    'col3': f'Top SQL 最高累计延迟 {max_latency:.1f} 秒，需重点关注最慢的查询',
                    'col4': '高', 'col5': 'DBA',
                    'fix_sql': '-- 查看最慢的 SQL：\n-- SELECT * FROM performance_schema.events_statements_summary_by_digest\n--   ORDER BY SUM_TIMER_WAIT DESC LIMIT 10;'
                })

        # 全表扫描 SQL
        if full_scan:
            scan_count = len(full_scan)
            worst = full_scan[0] if full_scan else {}
            issues.append({
                'col1': f'发现 {scan_count} 条全表扫描 SQL', 'col2': '高风险',
                'col3': f'最严重 SQL 扫描了 {worst.get("rows_scanned", 0)} 行但只返回 {worst.get("rows_sent", 0)} 行，过滤率 {(1 - float(worst.get("rows_sent", 1) / max(worst.get("rows_scanned", 1), 1)))*100:.1f}%，建议添加合适索引',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': '-- 查看全表扫描 SQL：\nSELECT DIGEST_TEXT, COUNT_STAR, SUM_ROWS_EXAMINED, SUM_ROWS_SENT\nFROM performance_schema.events_statements_summary_by_digest\nWHERE DIGEST_TEXT IS NOT NULL\nORDER BY SUM_ROWS_EXAMINED DESC LIMIT 10;\n-- 建议：分析 SQL 添加合适索引，或使用 STRAIGHT_JOIN / FORCE INDEX 强制使用索引'
            })

        # 锁等待严重
        if top_lock:
            lock_count = len(top_lock)
            worst_lock = top_lock[0] if top_lock else {}
            issues.append({
                'col1': f'发现 {lock_count} 条高锁等待 SQL', 'col2': '中风险',
                'col3': f'最严重 SQL 累计锁等待 {worst_lock.get("total_lock_sec", 0):.3f} 秒，建议检查锁竞争',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': '-- 查看锁等待：\nSELECT * FROM performance_schema.events_statements_summary_by_digest\n  WHERE SUM_LOCK_TIME > 0\n  ORDER BY SUM_LOCK_TIME DESC LIMIT 10;\n-- 结合 PROCESSLIST 确认阻塞源：\n-- SHOW FULL PROCESSLIST;'
            })

        # AI 诊断结果（如有）
        ai_diag = sq_result.get('ai_diagnosis', '')
        if ai_diag:
            # 将 AI 诊断结果注入到 issues 中（标记为 AI 生成）
            issues.append({
                'col1': 'AI 慢查询诊断', 'col2': 'AI 建议',
                'col3': ai_diag[:500],  # 限制长度避免过长
                'col4': '参考', 'col5': 'AI (Ollama)',
                'fix_sql': ''
            })

    return issues


# ═══════════════════════════════════════════════════════
#  2. 智能风险分析（PostgreSQL）
# ═══════════════════════════════════════════════════════

def smart_analyze_pg(context: dict) -> list:
    """
    对 PostgreSQL 巡检结果执行 15+ 条增强风险规则分析。
    """
    issues = []

    def _float(v, default=0.0):
        try:
            return float(str(v).replace(',', '').replace('%', ''))
        except Exception:
            return default

    def _int(v, default=0):
        try:
            return int(str(v).replace(',', ''))
        except Exception:
            return default

    def _setting(name):
        for item in context.get('pg_settings_key', []):
            if item.get('name') == name:
                return item.get('setting', None)
        return None

    # ── 1. 连接数使用率 ──────────────────────────────────
    pg_conn = context.get('pg_connections', [])
    if pg_conn and pg_conn[0]:
        usage_pct = _float(pg_conn[0].get('usage_percent', 0))
        used = _int(pg_conn[0].get('used_connections', 0))
        max_conn = _int(pg_conn[0].get('max_connections', 100))
        if usage_pct > 90:
            issues.append({
                'col1': 'report.pg_issue_conn_usage_high', 'col2': 'report.risk_high',
                'col3': f'连接使用率 {usage_pct:.1f}%（{used}/{max_conn}），接近上限将拒绝新连接',
                'col4': 'report.pg_fallback_priority_high', 'col5': 'report.pg_fallback_owner_dba',
                'fix_sql': f"-- 修改 postgresql.conf：\n-- max_connections = {min(max_conn * 2, 1000)}\n-- 建议同时使用 PgBouncer 连接池"
            })
        elif usage_pct > 80:
            issues.append({
                'col1': 'report.pg_issue_conn_usage_high', 'col2': 'report.risk_mid',
                'col3': f'连接使用率 {usage_pct:.1f}%（{used}/{max_conn}），建议关注',
                'col4': 'report.pg_fallback_priority_mid', 'col5': 'report.pg_fallback_owner_dba',
                'fix_sql': "SELECT pid, usename, application_name, state, query_start, query FROM pg_stat_activity WHERE state != 'idle' ORDER BY query_start;"
            })

    # ── 2. 缓存命中率 ────────────────────────────────────
    cache_hit = context.get('pg_cache_hit', [])
    for row in cache_hit:
        hit_rate = _float(row.get('cache_hit_ratio', 100))
        if hit_rate < 95:
            issues.append({
                'col1': 'report.pg_issue_cache_hit_low', 'col2': 'report.risk_high',
                'col3': f'缓存命中率仅 {hit_rate:.1f}%（建议 > 99%），大量数据从磁盘读取',
                'col4': 'report.pg_fallback_priority_high', 'col5': 'report.pg_fallback_owner_dba',
                'fix_sql': "-- 增大 shared_buffers（建议物理内存的 25%）：\n-- shared_buffers = 4GB  # 修改 postgresql.conf 后重启"
            })

    # ── 3. shared_buffers 偏小 ───────────────────────────
    sb = _setting('shared_buffers')
    if sb:
        # 单位：8KB pages
        sb_pages = _int(sb)
        sb_gb = sb_pages * 8 / 1024 / 1024
        if 0 < sb_gb < 1:
            issues.append({
                'col1': 'report.pg_issue_shared_buffers_small', 'col2': 'report.risk_mid',
                'col3': f'shared_buffers = {sb} pages（约 {sb_gb:.2f} GB），建议设为物理内存的 25%',
                'col4': 'report.pg_fallback_priority_mid', 'col5': 'report.pg_fallback_owner_dba',
                'fix_sql': "-- 修改 postgresql.conf：\n-- shared_buffers = 4GB\n-- 需要重启 PostgreSQL"
            })

    # ── 4. 长时间运行的查询 ───────────────────────────────
    pg_proc = context.get('pg_processlist', [])
    long_queries = []
    for p in pg_proc:
        state = str(p.get('state', ''))
        dur = str(p.get('duration', ''))
        if state == 'active' and dur:
            # duration 格式：'0:01:23.456' 或 '00:00:05'
            try:
                parts = dur.split(':')
                secs = int(parts[-1].split('.')[0]) + int(parts[-2]) * 60
                if len(parts) >= 3:
                    secs += int(parts[-3]) * 3600
                if secs > 60:
                    long_queries.append(p)
            except Exception:
                pass
    if long_queries:
        issues.append({
            'col1': 'report.pg_issue_long_query', 'col2': 'report.risk_high',
            'col3': f'发现 {len(long_queries)} 个执行超过 60 秒的查询，可能持有锁',
            'col4': 'report.pg_fallback_priority_high', 'col5': 'report.pg_fallback_owner_dba',
            'fix_sql': '\n'.join([f"SELECT pg_terminate_backend({p.get('pid', '')});  -- {str(p.get('query',''))[:60]}" for p in long_queries[:5]])
        })

    # ── 5. 锁等待 ─────────────────────────────────────────
    for p in pg_proc:
        if str(p.get('wait_event_type', '')) == 'Lock':
            issues.append({
                'col1': 'report.pg_issue_lock_wait', 'col2': 'report.risk_mid',
                'col3': '当前有进程在等待锁释放，可能影响业务响应速度',
                'col4': 'report.pg_fallback_priority_mid', 'col5': 'report.pg_fallback_owner_dba',
                'fix_sql': "SELECT blocked_locks.pid AS blocked_pid, blocking_locks.pid AS blocking_pid,\n  blocked_activity.query AS blocked_query, blocking_activity.query AS blocking_query\nFROM pg_catalog.pg_locks blocked_locks\nJOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid\nJOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype\n  AND blocking_locks.granted\nJOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid\nWHERE NOT blocked_locks.granted;"
            })
            break

    # ── 6. 用户安全（超级用户过多） ──────────────────────
    pg_users = context.get('pg_users', [])
    superusers = [u for u in pg_users if str(u.get('superuser', '')).upper() in ('T', 'TRUE', 'YES', '1')]
    if len(superusers) > 2:
        issues.append({
            'col1': 'report.pg_issue_superuser_many', 'col2': 'report.risk_mid',
            'col3': f'发现 {len(superusers)} 个超级用户，建议最小化权限，超级用户仅用于管理',
            'col4': 'report.pg_fallback_priority_mid', 'col5': 'report.pg_fallback_owner_dba',
            'fix_sql': "-- 查看超级用户：\nSELECT usename, usesuper FROM pg_user WHERE usesuper;\n-- 撤销多余超级权限：\nALTER USER username NOSUPERUSER;"
        })

    # ── 7. 归档日志 ───────────────────────────────────────
    archive = _setting('archive_mode')
    if archive and str(archive).lower() == 'off':
        issues.append({
            'col1': 'report.pg_issue_archive_mode_off', 'col2': 'report.risk_suggest',
            'col3': 'archive_mode=off，无法实现 PITR（时间点恢复），生产环境建议开启',
            'col4': 'report.pg_fallback_priority_low', 'col5': 'report.pg_fallback_owner_dba',
            'fix_sql': "-- 修改 postgresql.conf：\n-- archive_mode = on\n-- archive_command = 'cp %p /path/to/archive/%f'\n-- wal_level = replica\n-- 需要重启 PostgreSQL"
        })

    # ── 8. 磁盘使用率 ────────────────────────────────────
    for disk in context.get('system_info', {}).get('disk_list', []):
        usage = _float(disk.get('usage_percent', 0))
        mp = disk.get('mountpoint', '/')
        if mp in IGNORE_MOUNTS:
            continue
        if usage > 90:
            issues.append({
                'col1': 'report.pg_issue_disk_usage_high', 'col2': 'report.risk_high',
                'col3': f'磁盘 {mp} 使用率 {usage:.1f}%，可能导致数据库停止写入',
                'col4': 'report.pg_fallback_priority_high', 'col5': 'report.pg_fallback_owner_sysadmin',
                'fix_sql': "-- 查找大表：\nSELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size\nFROM pg_tables ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC LIMIT 10;"
            })
        elif usage > 80:
            issues.append({
                'col1': 'report.pg_issue_disk_warning', 'col2': 'report.risk_mid',
                'col3': f'磁盘 {mp} 使用率 {usage:.1f}%',
                'col4': 'report.pg_fallback_priority_mid', 'col5': 'report.pg_fallback_owner_sysadmin',
                'fix_sql': ''
            })

    # ── 9. 内存使用率 ────────────────────────────────────
    mem_usage = _float(context.get('system_info', {}).get('memory', {}).get('usage_percent', 0))
    if mem_usage > 90:
        issues.append({
            'col1': 'report.pg_issue_mem_usage_high', 'col2': 'report.risk_high',
            'col3': f'系统内存使用率 {mem_usage:.1f}%，可能触发 OOM Killer 杀掉 PG 进程',
            'col4': 'report.pg_fallback_priority_high', 'col5': 'report.pg_fallback_owner_sysadmin',
            'fix_sql': ''
        })

    # ── 10. 大量 dead tuples（需要 vacuum） ─────────────
    for db in context.get('pg_db_size', []):
        dead = _int(db.get('n_dead_tup', 0))
        live = _int(db.get('n_live_tup', 1))
        if live > 0 and dead / live > 0.2 and dead > 10000:
            dbname = db.get('datname', '?')
            issues.append({
                'col1': 'report.pg_issue_dead_tuples', 'col2': 'report.risk_mid',
                'col3': f'数据库 {dbname} dead tuples 占比 {dead/(live+dead)*100:.1f}%，建议执行 VACUUM',
                'col4': 'report.pg_fallback_priority_mid', 'col5': 'report.pg_fallback_owner_dba',
                'fix_sql': f"VACUUM ANALYZE {dbname};\n-- 或全库：\nVACUUM VERBOSE ANALYZE;"
            })

    # ── 11. 慢查询深度分析（P2）─────────────────────────────
    sq_result = context.get('slow_query_result', {})
    if sq_result:
        ext_available = sq_result.get('extension_available', {})
        if not ext_available.get('pg_stat_statements', False):
            issues.append({
                'col1': 'report.pg_issue_pg_stat_statements_off', 'col2': 'report.risk_suggest',
                'col3': 'pg_stat_statements 扩展未开启，无法进行慢查询深度分析',
                'col4': 'report.pg_fallback_priority_low', 'col5': 'report.pg_fallback_owner_dba',
                'fix_sql': "-- 在 postgresql.conf 中添加并重启：\n-- shared_preload_libraries = \'pg_stat_statements\'\n-- pg_stat_statements.track = all\n-- 然后执行：CREATE EXTENSION pg_stat_statements;"
            })

        top_latency = sq_result.get('top_sql_by_latency', [])
        top_io = sq_result.get('top_sql_by_io', [])
        long_running = sq_result.get('slow_queries_current', [])

        if top_latency:
            max_latency = max((float(x.get('total_time_sec') or 0) for x in top_latency), default=0)
            if max_latency > 300:
                issues.append({
                    'col1': 'report.pg_issue_slow_query_high_latency', 'col2': 'report.risk_high',
                    'col3': f'Top SQL 最高累计延迟 {max_latency:.1f} 秒，需重点关注',
                    'col4': 'report.pg_fallback_priority_high', 'col5': 'report.pg_fallback_owner_dba',
                    'fix_sql': "-- 查看最慢 SQL：\nSELECT query, calls, total_exec_time / 1000 AS total_sec,\n       mean_exec_time / 1000 AS mean_sec, rows\nFROM pg_stat_statements\nORDER BY total_exec_time DESC LIMIT 10;"
                })

        if top_io:
            high_io_count = len(top_io)
            worst_io = top_io[0] if top_io else {}
            issues.append({
                'col1': 'report.pg_issue_slow_query_high_io', 'col2': 'report.risk_high',
                'col3': f'发现 {high_io_count} 条高 IO SQL，最严重的读取了 {worst_io.get("rows_read", 0)} 个块',
                'col4': 'report.pg_fallback_priority_high', 'col5': 'report.pg_fallback_owner_dba',
                'fix_sql': "-- 查看高 IO SQL：\nSELECT query, calls, blk_read_time / 1000 AS read_ms,\n       blk_write_time / 1000 AS write_ms, shared_blks_read, shared_blks_written\nFROM pg_stat_statements\nWHERE (blk_read_time + blk_write_time) > 0\nORDER BY (blk_read_time + blk_write_time) DESC LIMIT 10;"
            })

        if long_running:
            issues.append({
                'col1': 'report.pg_issue_long_running_sql', 'col2': 'report.risk_high',
                'col3': f'当前有 {len(long_running)} 个长查询正在执行，最长等待锁或执行中',
                'col4': 'report.pg_fallback_priority_high', 'col5': 'report.pg_fallback_owner_dba',
                'fix_sql': "-- 查看长查询：\nSELECT pid, now()-query_start AS duration, state, left(query,100)\nFROM pg_stat_activity\nWHERE state != \'idle\' AND (now()-query_start) > interval \'5 seconds\'\nORDER BY duration DESC;\n-- 杀掉问题查询：\n-- SELECT pg_terminate_backend(pid);"
            })

        ai_diag = sq_result.get('ai_diagnosis', '')
        if ai_diag:
            issues.append({
                'col1': 'AI 慢查询诊断', 'col2': 'AI 建议',
                'col3': ai_diag[:500],
                'col4': '参考', 'col5': 'AI (Ollama)',
                'fix_sql': ''
            })

    return issues


# ═══════════════════════════════════════════════════════
#  2b. Oracle 增强智能分析（20+ 条规则）
# ═══════════════════════════════════════════════════════

def smart_analyze_oracle(context: dict) -> list:
    """
    对 Oracle 巡检结果执行 20+ 条增强风险规则分析。
    覆盖：表空间、会话、锁、内存、Redo、归档、用户安全、无效对象等
    """
    issues = []

    def _float(v, default=0.0):
        try: return float(str(v).replace(',', '').replace('%', ''))
        except Exception: return default

    def _int(v, default=0):
        try: return int(str(v).replace(',', ''))
        except Exception: return default

    # ── 1. 表空间使用率（含自动扩展）─────────────
    for ts in context.get('ora_tablespace', []):
        tsname = ts.get('TABLESPACE_NAME', '?')
        used_pct = _float(ts.get('USED_PCT_WITH_MAXEXT', ts.get('USED_PCT', 0)))
        total_mb = _float(ts.get('TOTAL_MB', 0))
        if used_pct > 95:
            issues.append({
                'col1': f'表空间 {tsname} 严重不足', 'col2': '高风险',
                'col3': f'表空间 {tsname} 使用率 {used_pct:.1f}%（含自动扩展），即将满',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': f"-- 检查大对象：\nSELECT segment_name, segment_type, owner, bytes/1024/1024 AS mb\nFROM dba_segments WHERE tablespace_name='{tsname}' ORDER BY bytes DESC;\n-- 清理方案：\n-- 1) 删除不需要的对象\n-- 2) TRUNCATE 大表\n-- 3) ALTER TABLESPACE {tsname} ADD DATAFILE SIZE 1G AUTOEXTEND ON;"
            })
        elif used_pct > 85:
            issues.append({
                'col1': f'表空间 {tsname} 偏高', 'col2': '中风险',
                'col3': f'表空间 {tsname} 使用率 {used_pct:.1f}%（总容量 {total_mb:.0f} MB）',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': ''
            })

    # ── 2. TEMP 表空间使用率 ─────────────────────
    for tmp in context.get('ora_temp_ts', []):
        tmpname = tmp.get('TABLESPACE_NAME', '?')
        tmp_used = _float(tmp.get('USED_PCT', 0))
        if tmp_used > 80:
            issues.append({
                'col1': f'TEMP 表空间 {tmpname} 使用率偏高', 'col2': '中风险',
                'col3': f'TEMP 表空间 {tmpname} 使用率 {tmp_used:.1f}%，可能有大量排序/临时操作',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': "-- 查看 TEMP 使用者：\nSELECT s.sid, s.serial#, s.username, u.tablespace, u.segtype,\n       ROUND(u.blocks * p.value / 1024 / 1024, 2) AS mb_used\nFROM v$tempseg_usage u JOIN v$session s ON u.session_addr = s.saddr\nJOIN v$parameter p ON p.name='db_block_size'\nORDER BY mb_used DESC;"
            })

    # ── 3. 会话数接近上限 ───────────────────────
    sess = context.get('ora_sessions', [])
    limit = context.get('ora_session_limit', [])
    plimit = context.get('ora_process_limit', [])
    if sess and limit:
        total = _int(sess[0].get('TOTAL_SESSIONS', 0))
        active = _int(sess[0].get('ACTIVE_SESSIONS', 0))
        max_sess = _int(limit[0].get('SESSIONS_LIMIT', 0))
        max_proc = _int(plimit[0].get('PROCESSES_LIMIT', 0)) if plimit else 0
        if max_sess > 0:
            usage = (total / max_sess) * 100
            if usage > 90:
                issues.append({
                    'col1': '会话数严重超标', 'col2': '高风险',
                    'col3': f'当前会话数 {total}/{max_sess}（{usage:.1f}%），活跃会话 {active}',
                    'col4': '高', 'col5': 'DBA',
                    'fix_sql': '-- 查看会话详情：\nSELECT sid, serial#, username, status, machine, program, sql_id, logon_time FROM v$session WHERE type=\'USER\' AND username IS NOT NULL ORDER BY logon_time;'
                })
            elif usage > 80:
                issues.append({
                    'col1': '会话数偏高', 'col2': '中风险',
                    'col3': f'当前会话数 {total}/{max_sess}（{usage:.1f}%）',
                    'col4': '中', 'col5': 'DBA',
                    'fix_sql': ''
                })
        if max_proc > 0 and total > max_proc * 0.85:
            issues.append({
                'col1': '进程数可能超限', 'col2': '高风险',
                'col3': f'sessions={total}, processes上限={max_proc}，sessions 应小于 processes*1.5+22',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': '-- 调整参数：\nALTER SYSTEM SET processes=<新值> SCOPE=SPFILE;\nALTER SYSTEM SET sessions=<新值> SCOPE=SPFILE;\n-- 重启后生效'
            })

    # ── 4. 锁等待 / 阻塞 ─────────────────────────
    blocked = context.get('ora_blocked', [])
    if blocked:
        b_count = len(blocked)
        max_wait = max((_float(b.get('SEC_IN_WAIT', 0)) for b in blocked), default=0)
        fix_lines = []
        for b in blocked[:5]:
            bsid = b.get("BLOCKED_SID", "")
            fix_lines.append(
                f"ALTER SYSTEM KILL SESSION '{bsid},{bsid}'; "
                "-- 杀掉被阻塞的会话"
            )
        issues.append({
            'col1': f'发现 {b_count} 组锁阻塞', 'col2': '高风险',
            'col3': f'{b_count} 个会话被锁阻塞，最长等待 {max_wait:.0f} 秒，可能影响业务响应速度',
            'col4': '高', 'col5': 'DBA',
            'fix_sql': '\n'.join(fix_lines)
        })

    # ── 5. SGA 总量检查 ───────────────────────────
    sga_total = context.get('ora_sga_total', [])
    if sga_total:
        sga_mb = _float(sga_total[0].get('SGA_TOTAL_MB', 0))
        mem_mb = _float(context.get('system_info', {}).get('memory', {}).get('total_mb', 0)) * 1000  # GB to MB approx
        if mem_mb > 100 and sga_mb > mem_mb * 0.8:
            issues.append({
                'col1': 'SGA 占用物理内存比例过高', 'col2': '中风险',
                'col3': f'SGA 总计 {sga_mb:.0f} MB，约占物理内存的 {(sga_mb/mem_mb)*100:.0f}%，可能导致操作系统换页',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': '-- 调整 SGA_TARGET（需重启）：\n-- ALTER SYSTEM SET sga_target = <较小值> SCOPE=SPFILE;\n-- 或启用 AMM: memory_target = 物理内存 * 70%'
            })

    # ── 6. Redo 日志组状态 ───────────────────────
    redo_logs = context.get('ora_redo_logs', [])
    inactive_count = sum(1 for r in redo_logs if str(r.get('STATUS','')).upper() == 'INACTIVE')
    current_logs = [r for r in redo_logs if str(r.get('STATUS','')).upper() == 'CURRENT']
    if redo_logs and inactive_count <= 1 and len(redo_logs) >= 3:
        issues.append({
            'col1': 'Redo 日志组可能过少或切换频繁', 'col2': '建议',
            'col3': f'Redo 共 {len(redo_logs)} 组，仅 {inactive_count} 组为 INACTIVE，可能需要增加日志组大小或数量',
            'col4': '低', 'col5': 'DBA',
            'fix_sql': '-- 查看日志切换频率：\nSELECT TO_CHAR(first_time,\'YYYY-MM-DD HH24\') AS hour, COUNT(*) AS switch_count\nFROM v$log_history GROUP BY TO_CHAR(first_time,\'YYYY-MM-DD HH24\') ORDER BY hour;\n-- 新增日志组：\n-- ALTER DATABASE ADD LOGFILE GROUP <N> SIZE 512M;\n'
        })
    for r in redo_logs:
        if str(r.get('STATUS','')).upper() not in ('CURRENT', 'ACTIVE', 'INACTIVE'):
            issues.append({
                'col1': f'Redo 日志组异常状态: {r.get("STATUS","")}', 'col2': '高风险',
                'col3': f'Group# {r.get("GROUP#")} 状态为 {r.get("STATUS","")}',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': ''
            })

    # ── 7. 归档模式与备份 ───────────────────────
    dbinfo = context.get('ora_database', [{}])
    if dbinfo and str(dbinfo[0].get('LOG_MODE','')).upper() == 'NOARCHIVELOG':
        issues.append({
            'col1': '数据库未开启归档模式', 'col2': '高风险',
            'col3': 'log_mode=NOARCHIVELOG，无法进行在线热备份和时间点恢复(PITR)',
            'col4': '高', 'col5': 'DBA',
            'fix_sql': '-- 开启归档模式（需要重启到 MOUNT 状态）：\n-- SHUTDOWN IMMEDIATE;\n-- STARTUP MOUNT;\n-- ALTER DATABASE ARCHIVELOG;\n-- ALTER DATABASE OPEN;\n-- 设置归档路径：\n-- ALTER SYSTEM SET log_archive_dest_1=\'LOCATION=/arch/orcl\' SCOPE=SPFILE;'
        })

    backup = context.get('ora_backup', [])
    if not backup:
        issues.append({
            'col1': '未找到最近的 RMAN 备份记录', 'col2': '高风险',
            'col3': 'v$rman_backup_job_details 中无备份记录，请确认备份策略是否正常运行',
            'col4': '高', 'col5': 'DBA',
            'fix_sql': ''
        })
    elif backup:
        last_bk = backup[0]
        bk_time = str(last_bk.get('START_TIME', ''))
        if bk_time and '1970' in bk_time or '0001' in bk_time:
            pass  # 无效时间
        else:
            issues.append({  # 仅记录最近备份信息作为参考
                'col1': 'RMAN 备份记录', 'col2': '信息',
                'col3': f"最近一次备份: {bk_time}, 类型={last_bk.get('INPUT_TYPE','?'),}, 状态={last_bk.get('STATUS','?')}",
                'col4': '低', 'col5': 'DBA', 'fix_sql': ''
            })

    # ── 8. Data Guard / ADG 同步延迟 ────────────
    dg = context.get('ora_dg_status', [{}])
    if dg and str(dg[0].get('DATABASE_ROLE','')).upper() != 'PRIMARY':
        role = dg[0].get('DATABASE_ROLE','')
        apply_info = context.get('ora_dg_apply', [])
        mrp_running = any(str(a.get('STATUS','')).upper() in ('APPLYING','MANAGED') for a in apply_info)
        if not mrp_running and apply_info:
            issues.append({
                'col1': f'Data Guard 备库 ({role}) MRP 未运行', 'col2': '高风险',
                'col3': f'MRP 进程状态非 APPLYING/MANAGED，备库可能未同步主库 Redo',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': '-- 在备库上启动实时应用：\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE DISCONNECT USING CURRENT SESSION;\n-- 或查看告警日志排查原因'
            })
        # 检查保护模式
        prot_mode = str(dg[0].get('PROTECTION_MODE',''))
        if prot_mode and 'MAXIMUM PERFORMANCE' in prot_mode.upper():
            mode_text = "MAXIMUM PERFORMANCE(异步)"
            issues.append({
                'col1': 'Data Guard 保护模式较低', 'col2': '中风险',
                'col3': f'保护模式为 {mode_text}，故障切换时可能丢失数据',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': '-- 如业务允许零丢失，升级保护模式：\n-- 主库: ALTER DATABASE SET STANDBY DATABASE TO MAXIMIZE AVAILABILITY;  \n-- 需要配置 standby_redo_log 和确认网络带宽'
            } if False else {
                'col1': 'Data Guard 保护模式较低', 'col2': '中风险',
                'col3': f'保护模式为 {prot_mode}，如业务要求零数据丢失请考虑升级',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': ''
            })

    # ── 9. ASM 磁盘组 ─────────────────────────────
    asm_list = context.get('ora_asm_diskgroup', [])
    for asm in asm_list:
        asmname = asm.get('NAME', '?')
        asm_used = _float(asm.get('USED_PCT', 0))
        offline = _int(asm.get('OFFLINE_DISKS', 0))
        if asm_used > 90:
            issues.append({
                'col1': f'ASM 磁盘组 {asmname} 空间紧张', 'col2': '高风险',
                'col3': f'ASM 磁盘组 {asmname} 使用率 {asm_used:.1f}%',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': f"-- 查看 ASM 磁盘组使用情况：\nSELECT name, type, total_mb, free_mb, required_mirror_free_mb AS rmf_mb,\n       usable_file_mb, offline_disks FROM v$asm_diskgroup WHERE name='{asmname}';"
            })
        if offline > 0:
            issues.append({
                'col1': f'ASM 磁盘组 {asmname} 有离线磁盘', 'col2': '高风险',
                'col3': f'{offline} 个磁盘处于 OFFLINE 状态，存在冗余降级风险',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': f"-- 检查磁盘组冗余和磁盘状态：\nSELECT group_number, disk_number, name, header_status, mode_status, state, failgroup\nFROM v$asm_disk WHERE group_number=(SELECT group_number FROM v$asm_diskgroup WHERE name='{asmname}') ORDER BY disk_number;"
            })

    # ── 10. 闪回恢复区使用率 ─────────────────────
    fb_area = context.get('ora_flashback_area', [])
    for fb in fb_area:
        fb_used = _float(fb.get('USED_PCT', 0))
        if fb_used > 85:
            issues.append({
                'col1': '闪回恢复区(FRA)使用率偏高', 'col2': '高风险',
                'col3': f'FRA 使用率 {fb_used:.1f}%，可能影响备份和归档操作',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': '-- 查看FRA占用：\nSELECT file_type, percent_space_used, space_used/1024/1024 MB_used, number_of_files\nFROM v$recovery_file_dest_usage ORDER BY percent_space_used DESC;\n-- 解决方案：\n-- 1) 扩大 DB_RECOVERY_FILE_DEST_SIZE\n-- 2) 删除过期备份: RMAN> delete obsolete;\n-- 3) 调整保留策略: CONFIGURE RETENTION POLICY TO RECOVERY WINDOW OF 7 DAYS;'
            })

    # ── 11. 无效对象 ─────────────────────────────
    inv_cnt = context.get('ora_invalid_cnt', [])
    for inv in inv_cnt:
        cnt = _int(inv.get('INVALID_COUNT', 0))
        owner = inv.get('OWNER', '?')
        if cnt > 10:
            issues.append({
                'col1': f'用户 {owner} 有 {cnt} 个无效对象', 'col2': '中风险',
                'col3': f'Schema {owner} 存在 {cnt} 个 INVALID 对象，可能导致功能异常',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': f"-- 编译无效对象（推荐以 SYS 执行）：\nEXEC DBMS_UTILITY.compile_schema(schema=>'{owner}', compile_all=>FALSE);\n-- 或逐个编译：\n-- @?/rdbms/admin/utlrp.sql"
            })

    # ── 12. 用户密码策略 ─────────────────────────
    profile_pwd = context.get('ora_profile_pwd', [])
    unlimited_pwd = [p for p in profile_pwd if str(p.get('LIMIT','')).upper() in ('UNLIMITED', '') and p.get('RESOURCE_NAME') not in ('FAILED_LOGIN_ATTEMPTS',)]
    if unlimited_pwd:
        names = ', '.join(p['RESOURCE_NAME'] for p in unlimited_pwd[:3])
        issues.append({
            'col1': 'DEFAULT Profile 密码策略宽松', 'col2': '建议',
            'col3': f'DEFAULT Profile 的 {names} 设为 UNLIMITED，生产环境建议设置合理限制',
            'col4': '低', 'col5': 'DBA',
            'fix_sql': '-- 示例：修改密码有效期为 180 天\n-- ALTER PROFILE DEFAULT LIMIT PASSWORD_LIFE_TIME 180;\n-- ALTER PROFILE DEFAULT LIMIT PASSWORD_REUSE_TIME 365;\n-- ALTER PROFILE DEFAULT LIMIT PASSWORD_LOCK_TIME 1/24;'
        })

    # ── 13. 锁定/过期用户 ─────────────────────────
    users = context.get('ora_users', [])
    locked_users = [u for u in users if 'LOCKED' in str(u.get('ACCOUNT_STATUS',''))]
    expired_users = [u for u in users if 'EXPIRED' in str(u.get('ACCOUNT_STATUS',''))]
    if locked_users and len(locked_users) > 10:
        names = ','.join([u['USERNAME'] for u in locked_users[:5]]) + '...'
        issues.append({
            'col1': f'{len(locked_users)} 个用户被锁定', 'col2': '信息',
            'col3': f'锁定用户: {names} 等',
            'col4': '低', 'col5': 'DBA', 'fix_sql': ''
        })

    # ── 14. 统计信息陈旧 ─────────────────────────
    stale = context.get('ora_stale_stats', [])
    if stale:
        owners = set(s.get('OWNER','?') for s in stale)
        issues.append({
            'col1': f'统计信息陈旧（{len(stale)}个对象）', 'col2': '中风险',
            'col3': f'涉及 Schema: {", ".join(list(owners)[:5])}，共 {len(stale)} 张表统计信息已过时，CBO 可能生成次优执行计划',
            'col4': '中', 'col5': 'DBA',
            'fix_sql': '-- 收集全库统计信息（低峰期执行）：\nEXEC DBMS_STATS.GATHER_DATABASE_STATS(options => \'GATHER AUTO\', estimate_percent => DBMS_STATS.AUTO_SAMPLE_SIZE);\n-- 或针对特定 Schema：\nEXEC DBMS_STATS.GATHER_SCHEMA_STATS(ownname=>\'<SCHEMA_NAME>\', cascade=>TRUE);'
        })

    # ── 15. 系统资源（CPU/内存/磁盘）──────────────
    sys_info = context.get('system_info', {})
    mem = sys_info.get('memory', {})
    mem_usage = _float(mem.get('usage_percent', 0))
    if mem_usage > 90:
        issues.append({'col1': '系统内存使用率过高', 'col2': '高风险',
                       'col3': f'内存使用率 {mem_usage:.1f}%，Oracle 可能被 OOM Kill',
                       'col4': '高', 'col5': '系统管理员', 'fix_sql': ''})
    cpu_val = sys_info.get('cpu', {})
    cpu_usage = _float(cpu_val.get('usage_percent', 0)) if isinstance(cpu_val, dict) else 0
    if cpu_usage > 95:
        issues.append({'col1': '系统 CPU 过载', 'col2': '高风险',
                       'col3': f'CPU 使用率 {cpu_usage:.1f}%',
                       'col4': '高', 'col5': '系统管理员', 'fix_sql': ''})
    for disk in sys_info.get('disk_list', []):
        dusage = _float(disk.get('usage_percent', 0))
        mp = disk.get('mountpoint', '/')
        if mp in IGNORE_MOUNTS: continue
        if dusage > 90:
            issues.append({'col1': f'磁盘空间不足 ({mp})', 'col2': '高风险',
                           'col3': f'磁盘 {mp} 使用率 {dusage:.1f}%',
                           'col4': '高', 'col5': '系统管理员', 'fix_sql': ''})

    # ── 16. Undo 段争用（如果有 undo 信息）────────
    # （undo SQL 较复杂，如果采集失败则跳过）
    undo = context.get('ora_undo_info', [])
    if undo and len(undo) > 0:
        urow = undo[0] if isinstance(undo, list) else undo
        active_blks = _int(urow.get('ACTIVE_BLKS', 0))
        exp_blks = _int(urow.get('EXP_UNDO_BLKS', 0))
        if active_blks > 10000:
            issues.append({
                'col1': 'Undo 段活跃事务过多', 'col2': '中风险',
                'col3': f'当前活跃 Undo 块数 {active_blks}，可能有大事务长时间运行',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': '-- 查看长事务：\nSELECT sid, serial#, status, username, to_char(start_time,\'YYYY-MM-DD HH24:MI:SS\') start_time, elapsed_seconds, sql_id\nFROM v$transaction t JOIN v$session s ON t.addr = taddr\nWHERE t.status=\'ACTIVE\' ORDER BY start_time;'
            })

    # ── 17. 回收站占用空间 ───────────────────────
    rb = context.get('ora_recyclebin', [])
    if rb:
        total_rb_mb = sum(_float(r.get('SIZE_MB', 0)) for r in rb)
        if total_rb_mb > 500:
            issues.append({
                'col1': '回收站占用空间过大', 'col2': '中风险',
                'col3': f'回收站中共有 {len(rb)} 个对象，约 {total_rb_mb:.0f} MB',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': '-- 清空回收站（谨慎操作！）：\nPURGE RECYCLEBIN;\n-- 或清空特定用户的回收站：\nPURGE TABLESPACE <ts_name> USER <username>;'
            })

    # ── 18. open_cursors 参数 ────────────────────
    params = context.get('ora_params', [])
    oc_param = next((p for p in params if p.get('NAME')=='OPEN_CURSORS'), None)
    if oc_param:
        oc_val = _int(oc_param.get('VALUE', 300))
        if oc_val < 300:
            issues.append({
                'col1': 'open_cursors 参数偏小', 'col2': '建议',
                'col3': f'open_cursors={oc_val}，复杂应用建议设为 500-2000 以避免 ORA-01000 错误',
                'col4': '低', 'col5': 'DBA',
                'fix_sql': f'ALTER SYSTEM SET open_cursors=1000 SCOPE=BOTH;  -- 根据实际需求调整'
            })

    # ── 19. audit_trail 审计 ─────────────────────
    aud = next((p for p in params if p.get('NAME')=='AUDIT_TRAIL'), None)
    if aud and str(aud.get('VALUE','')).upper() == 'NONE':
        issues.append({
            'col1': '审计功能未开启', 'col2': '建议',
            'col3': 'audit_trail=NONE，无法追踪敏感操作（登录/DDL/DML），合规性要求建议开启',
            'col4': '低', 'col5': 'DBA',
            'fix_sql': '-- 开启标准审计（无需重启）：\nALTER SYSTEM SET audit_trail=DB SCOPE=SPFILE;\n-- 或仅审计特定操作：\nAUDIT CREATE SESSION, ALTER USER, DROP ANY TABLE;'
        })

    # ── 20. 数据文件脱机 ─────────────────────────
    datafiles = context.get('ora_datafiles', [])
    off_df = [d for d in datafiles if str(d.get('STATUS','')).upper() == 'OFFLINE']
    if off_df:
        fix_lines = []
        for d in off_df[:3]:
            fname = d.get("FILE_NAME", "?")
            fix_lines.append(f"-- 尝试联机:\n-- ALTER DATABASE DATAFILE '{fname}' ONLINE;")
        issues.append({
            'col1': f'{len(off_df)} 个数据文件处于 OFFLINE', 'col2': '高风险',
            'col3': ', '.join(f"{d.get('FILE_NAME','?')} ({d.get('TABLESPACE_NAME','?')})" for d in off_df[:3]),
            'col4': '高', 'col5': 'DBA',
            'fix_sql': '\n'.join(fix_lines)
        })

    return issues


# ═══════════════════════════════════════════════════════
#  3. 历史记录管理器
# ═══════════════════════════════════════════════════════

class HistoryManager:
    """
    将每次巡检的关键指标持久化到 SQLite 数据库，
    支持同一数据库实例的历史对比和趋势数据生成。

    文件位于：<base_dir>/history.db
    """

    def __init__(self, base_dir: str):
        # 使用组合模式：内部持有 SQLiteHistoryManager 实例
        # 避免直接继承导致的 MRO 问题
        try:
            from db_history import SQLiteHistoryManager
            self._inner = SQLiteHistoryManager(base_dir)
        except Exception:
            self._inner = None

    def save_snapshot(self, db_type: str, host: str, port, label: str, context: dict):
        if self._inner is None:
            return None
        return self._inner.save_snapshot(db_type, host, port, label, context)

    def get_trend(self, db_type: str, host: str, port):
        if self._inner is None:
            return {}
        return self._inner.get_trend(db_type, host, port)

    def get_comparison(self, db_type: str, host: str, port):
        if self._inner is None:
            return {}
        return self._inner.get_comparison(db_type, host, port)

    def list_instances(self):
        if self._inner is None:
            return []
        return self._inner.list_instances()





# ═══════════════════════════════════════════════════════
#  4. AI 诊断适配器（仅支持本地 Ollama）
# ═══════════════════════════════════════════════════════
#
# 安全限制：
# 1. 仅支持本地 Ollama（backend='ollama'）或关闭（'disabled'）
# 2. 不支持 OpenAI、DeepSeek 等任何远程 AI API（会被强制降级为 disabled）
# 3. Ollama 的 API 地址必须为本地地址（localhost / 127.0.0.1），非本地地址将被拒绝
#
# 配置优先级：代码传参 > ai_config.json > 环境变量
# 所有诊断数据在本地处理，绝不外传。
#

def _is_localhost_url(url: str) -> bool:
    """校验 URL 是否为本地地址"""
    if not url:
        return True  # 空值走默认 localhost
    import re as _re
    parsed = _re.match(r'https?://([^:/]+)', url.strip())
    if not parsed:
        return False
    host = parsed.group(1).lower()
    return host in ('localhost', '127.0.0.1', '::1', '0.0.0.0') or host.startswith('127.')


class AIAdvisor:
    """
    AI 诊断适配器 —— **仅支持本地部署的 Ollama**。

    支持模式：
    - ollama   : 本地 Ollama（默认 http://localhost:11434，地址必须是本地）
    - disabled : 关闭 AI 诊断

    为安全起见：
    - 不支持任何远程 AI API（openai/deepseek/custom 等均被拒绝）
    - API 地址必须为 localhost/127.0.0.1，非本地地址将导致 AI 诊断禁用
    """

    METRIC_LABELS_ZH = {
        'mem_usage': '内存使用率',
        'cpu_usage': 'CPU 使用率',
        'disk_usage_max': '磁盘最大使用率',
        'connections': '当前连接数',
        'max_connections': '最大连接数配置',
        'max_used_connections': '历史最大连接数',
        'cache_hit_ratio': '缓冲区命中率',
        'queries_total': '累计查询次数',
        'risk_count': '风险项数量',
        'health_status': '健康状态',
        # ── Oracle 专用指标 ──────────────────────────────────────
        'db_version': '数据库版本',
        'hostname': '主机名',
        'uptime': '运行时长',
        'tablespace_count': '表空间数量',
        'wait_events_top5': '等待事件 Top5',
        'blocked_sessions': '阻塞会话',
        'top_sql_top5': 'Top SQL 前5',
        # ── SQL Server 专用指标 ──────────────────────────────────
        'connection_usage_pct': '连接使用率',
        'wait_type': '等待类型',
        'wait_time_ms': '等待时间(毫秒)',
        'waiting_tasks_count': '等待任务数',
        # ── 慢查询深度分析（P2）─────────────────────────────────
        'slow_query_top3': '慢查询 Top 3',
        'slow_query_count': '慢查询样本数',
    }

    METRIC_LABELS_EN = {
        'mem_usage': 'Memory Usage',
        'cpu_usage': 'CPU Usage',
        'disk_usage_max': 'Max Disk Usage',
        'connections': 'Current Connections',
        'max_connections': 'Max Connections Config',
        'max_used_connections': 'Peak Connections Used',
        'cache_hit_ratio': 'Buffer Cache Hit Ratio',
        'queries_total': 'Total Queries',
        'risk_count': 'Risk Count',
        'health_status': 'Health Status',
        # ── Oracle-specific metrics ────────────────────────────────
        'db_version': 'DB Version',
        'hostname': 'Hostname',
        'uptime': 'Uptime',
        'tablespace_count': 'Tablespace Count',
        'wait_events_top5': 'Top 5 Wait Events',
        'blocked_sessions': 'Blocked Sessions',
        'top_sql_top5': 'Top 5 SQL by Buffer Gets',
        # ── SQL Server-specific metrics ────────────────────────────
        'connection_usage_pct': 'Connection Usage %',
        'wait_type': 'Wait Type',
        'wait_time_ms': 'Wait Time (ms)',
        'waiting_tasks_count': 'Waiting Tasks Count',
        # ── Slow Query Deep Analysis (P2) ─────────────────────────
        'slow_query_top3': 'Slow Query Top 3',
        'slow_query_count': 'Slow Query Sample Count',
    }

    def __init__(self, backend: str = None, api_key: str = None,
                 api_url: str = None, model: str = None,
                 rag_enabled: bool = True):
        # ── 安全限制 1: 只允许 ollama 或 disabled ──
        raw_backend = (backend or os.environ.get('DBCHECK_AI_BACKEND', 'disabled')).lower()
        if raw_backend == 'openai':
            print("⚠️  安全限制：远程 AI API 已禁用，AI 诊断仅支持本地 Ollama")
            raw_backend = 'disabled'
        elif raw_backend not in ('ollama', 'disabled'):
            print(f"⚠️  安全限制：不支持的 backend '{raw_backend}'，已禁用 AI 诊断")
            raw_backend = 'disabled'
        self.backend = raw_backend

        # ── 安全限制 2: URL 必须是本地地址 ──
        resolved_url = api_url or os.environ.get('DBCHECK_AI_URL', 'http://localhost:11434')
        if self.backend == 'ollama' and not _is_localhost_url(resolved_url):
            print(f"⚠️  安全限制：API 地址 {resolved_url} 不是本地地址，AI 诊断已禁用")
            self.backend = 'disabled'

        self.api_key = ''  # 本地 Ollama 不需要 API Key
        self.api_url = resolved_url
        self.model   = model   or os.environ.get('DBCHECK_AI_MODEL', 'qwen3:8b')

        if self.backend == 'ollama' and not model and not os.environ.get('DBCHECK_AI_MODEL'):
            self.model = 'qwen3:8b'

        # ── RAG 知识库初始化（静默降级，失败不影响 AI 诊断主流程）─────────
        self.rag_enabled = False
        self.rag_retriever = None
        if rag_enabled and self.backend == 'ollama':
            self._init_rag()

    def _init_rag(self):
        """初始化 RAG 知识库（失败时静默降级）"""
        try:
            from rag import RAGRetriever, VectorStore, OllamaEmbedding
            vs = VectorStore()
            emb = OllamaEmbedding(api_url=self.api_url)
            self.rag_retriever = RAGRetriever(vs, emb)
            self.rag_enabled = True
            print("[AIAdvisor] RAG 知识库已启用")
        except Exception as e:
            self.rag_enabled = False
            self.rag_retriever = None
            # 仅 debug 级别输出，不影响用户体验
            import sys
            if '-v' in sys.argv or '--verbose' in sys.argv:
                print(f"[AIAdvisor] RAG 初始化跳过: {e}")

    @property
    def enabled(self) -> bool:
        return self.backend != 'disabled'

    def _build_prompt(self, db_type: str, label: str, metrics: dict, issues: list,
                      lang: str = 'zh', rag_context: str = '') -> str:
        """构建发给 LLM 的诊断 Prompt，支持中英文"""
        labels = self.METRIC_LABELS_ZH if lang == 'zh' else self.METRIC_LABELS_EN
        sep = '=' * 60

        metric_lines = []
        for k, v in metrics.items():
            lbl = labels.get(k, k)
            if v is not None:
                metric_lines.append(f"  - {lbl}: {v}")

        issue_lines = []
        for i, iss in enumerate(issues[:10], 1):
            issue_lines.append(f"  {i}. [{iss.get('col2','')}] {iss.get('col1','')}: {iss.get('col3','')}")

        # Oracle 专属详细信息节（由 main_oracle_full.py 预构建）
        oracle_extra = ""
        # 慢查询深度分析节（MySQL/PG P2）
        slow_query_extra = ""
        if 'slow_query_top3' in metrics and metrics['slow_query_top3']:
            sq_val = metrics['slow_query_top3']
            if lang == 'zh':
                slow_query_extra += f"\n【慢查询 Top 3】\n{sq_val}"
            else:
                slow_query_extra += f"\n[Slow Query Top 3]\n{sq_val}"
        if lang == 'zh':
            if 'wait_events_top5' in metrics and metrics['wait_events_top5'] not in ('N/A', None, ''):
                oracle_extra += f"\n【等待事件 Top5】\n{metrics['wait_events_top5']}"
            if 'blocked_sessions' in metrics and metrics['blocked_sessions'] not in ('N/A', None, ''):
                oracle_extra += f"\n【阻塞会话】\n  {metrics['blocked_sessions']}"
            if 'top_sql_top5' in metrics and metrics['top_sql_top5'] not in ('N/A', None, ''):
                oracle_extra += f"\n【Top SQL（Buffer Gets 前5）】\n{metrics['top_sql_top5']}"
        else:
            if 'wait_events_top5' in metrics and metrics['wait_events_top5'] not in ('N/A', None, ''):
                oracle_extra += f"\n[Top 5 Wait Events]\n{metrics['wait_events_top5']}"
            if 'blocked_sessions' in metrics and metrics['blocked_sessions'] not in ('N/A', None, ''):
                oracle_extra += f"\n[Blocked Sessions]\n  {metrics['blocked_sessions']}"
            if 'top_sql_top5' in metrics and metrics['top_sql_top5'] not in ('N/A', None, ''):
                oracle_extra += f"\n[Top SQL by Buffer Gets]\n{metrics['top_sql_top5']}"

        if lang == 'zh':
            db_type_name = {'mysql': 'MySQL', 'pg': 'PostgreSQL', 'oracle': 'Oracle', 'sqlserver': 'SQL Server'}.get(db_type, db_type.upper())
            prompt = f"""你是一位拥有20年经验的 {db_type_name} 数据库资深DBA，以下是对 {db_type_name} 数据库「{label}」的全面巡检结果，请进行深度诊断。

{sep}
【一、关键健康指标】
{sep}
{chr(10).join(metric_lines) or '  (无)'}

{sep}
【二、发现的风险项】
{sep}
{chr(10).join(issue_lines) or '  未发现明显风险项'}

{slow_query_extra if slow_query_extra else ''}
{oracle_extra if oracle_extra else ''}
{sep}
【三、参考文档（RAG 知识库）】
{rag_context if rag_context else '  （未加载任何参考文档）'}
{sep}
【四、诊断要求】
{sep}
请基于以上巡检数据，给出 4~6 条专业优化建议，要求：
1. 优先分析【等待事件 Top5】：识别主要等待类型（如 db file sequential read、log file sync、buffer busy waits 等），给出具体优化方向
2. 如存在【阻塞会话】：分析阻塞原因（锁竞争、热块更新等）并给出解决思路
3. 针对【Top SQL】：评估是否存在全表扫描、大量磁盘读等问题，给出优化建议
4. 结合【关键指标】和【风险项】综合判断，给出整体健康评价
5. 每条建议必须包含：问题定位 → 原因分析 → 具体修复方案（或参数调整参考值）
6. 最后给出该数据库的整体健康评价（优秀/良好/一般/危险）及主要关注点

格式要求（直接输出 Markdown，不要加"以下是"等前缀）：
## 重点关注

[Top SQL 和等待事件的深度分析]

## 优化建议

1. [建议1]
2. [建议2]
...

## 整体评价

[一句话整体评价]"""
        else:
            prompt = f"""You are a senior DBA with 20 years of experience. Below is the comprehensive inspection report for the {db_type.upper()} database "{label}". Please provide an in-depth diagnosis.

{sep}
[I. Key Health Metrics]
{sep}
{chr(10).join(metric_lines) or '  (none)'}

{sep}
[II. Detected Risk Items]
{sep}
{chr(10).join(issue_lines) or '  No significant risks found.'}

{slow_query_extra if slow_query_extra else ''}
{oracle_extra if oracle_extra else ''}
{sep}
[III. Reference Documents (RAG Knowledge Base)]
{rag_context if rag_context else '  (No reference documents loaded)'}
{sep}
[IV. Diagnosis Requirements]
{sep}
Based on the inspection data above, provide 4~6 professional optimization recommendations:
1. Prioritize analysis of [Top 5 Wait Events]: identify major wait types (e.g., db file sequential read, log file sync, buffer busy waits) and provide specific optimization directions.
2. If [Blocked Sessions] exist: analyze the cause (lock contention, hot block updates, etc.) and propose solutions.
3. For [Top SQL]: evaluate whether there are full table scans, high disk reads, etc., and provide optimization recommendations.
4. Combine [Key Metrics] and [Risk Items] for an overall health assessment.
5. Each recommendation must include: Problem Identification → Cause Analysis → Specific Fix (or parameter tuning reference).
6. Provide an overall health rating (Excellent/Good/Fair/Critical) and main concerns.

Format requirement (output Markdown directly, no prefixes like "Here are"):
## Key Concerns

[In-depth analysis of Top SQL and wait events]

## Optimization Recommendations

1. [Recommendation 1]
2. [Recommendation 2]
...

## Overall Assessment

[One-sentence overall assessment]"""
        return prompt

    def diagnose(self, db_type: str, label: str, context: dict, issues: list,
                 timeout: int = 30, lang: str = 'zh') -> str:
        """
        调用 AI 后端进行诊断分析。

        :param db_type: 'mysql'、'pg'、'oracle' 或 'sqlserver'
        :param label: 数据库标签名
        :param context: MySQL/PG/SQLServer: getData.checkdb() 返回的 context；
                        Oracle: 预构建的 metrics dict（含 wait_events_top5/top_sql_top5 等）
        :param issues: smart_analyze_* 返回的风险列表
        :param timeout: 请求超时秒数
        :param lang: 'zh' 或 'en'，决定 AI 提示词语言
        :return: AI 生成的建议文本，失败时返回空字符串
        """
        if not self.enabled:
            return ''

        # ── 判断传入的是预构建 metrics（Oracle）还是原始 context（MySQL/PG/SQLServer）──
        _is_oracle_metrics = 'wait_events_top5' in context or 'top_sql_top5' in context

        if _is_oracle_metrics:
            metrics = context
        else:
            sys_info = context.get('system_info', {})
            hs_default = 'Unknown' if lang == 'en' else '未知'
            metrics = {
                'mem_usage': sys_info.get('memory', {}).get('usage_percent', 0),
                'cpu_usage': sys_info.get('cpu', {}).get('usage_percent', 0) if isinstance(sys_info.get('cpu'), dict) else 0,
                'disk_usage_max': max((d.get('usage_percent', 0) for d in sys_info.get('disk_list', [])
                                       if d.get('mountpoint', '/') not in IGNORE_MOUNTS), default=0),
                'risk_count': len(issues),
                'health_status': context.get('health_status', hs_default),
            }
            if db_type == 'mysql':
                metrics['connections'] = context.get('threads_connected', [{}])[0].get('Value', 0) if context.get('threads_connected') else 0
                metrics['max_connections'] = context.get('max_connections', [{}])[0].get('Value', 0) if context.get('max_connections') else 0
                # MySQL 慢查询深度分析指标（P2）
                sq = context.get('slow_query_result', {})
                if sq and sq.get('top_sql_by_latency'):
                    top3 = sq['top_sql_by_latency'][:3]
                    metrics['slow_query_top3'] = '\n'.join([
                        f"  - latency={x.get('total_time_sec', 0):.3f}s, "
                        f"exec={x.get('exec_count', 0)}, "
                        f"scan={x.get('rows_scanned', 0)}, "
                        f"sql={x.get('query_text', '')[:100]}"
                        for x in top3
                    ])
                    metrics['slow_query_count'] = len(sq.get('top_sql_by_latency', []))
            elif db_type == 'sqlserver':
                # SQL Server 连接统计
                conn_data = context.get('connections', [])
                if conn_data and isinstance(conn_data, list) and len(conn_data) > 0:
                    first_conn = conn_data[0] if isinstance(conn_data[0], dict) else {}
                    metrics['connections'] = first_conn.get('total_connections', 0)
                    metrics['max_connections'] = first_conn.get('max_connections', 0)
                    metrics['connection_usage_pct'] = first_conn.get('connection_usage_pct', 0)
                # SQL Server 等待事件 Top5
                wait_stats = context.get('wait_stats', [])
                if wait_stats:
                    wait_top5 = []
                    for w in wait_stats[:5]:
                        if isinstance(w, dict):
                            wait_top5.append({
                                'wait_type': w.get('wait_type', ''),
                                'wait_time_ms': w.get('wait_time_ms', 0),
                                'waiting_tasks_count': w.get('waiting_tasks_count', 0)
                            })
                    if wait_top5:
                        metrics['wait_events_top5'] = wait_top5
            else:
                pg_conn = context.get('pg_connections', [{}])
                if pg_conn and pg_conn[0]:
                    metrics['connections'] = pg_conn[0].get('used_connections', 0)
                    metrics['max_connections'] = pg_conn[0].get('max_connections', 0)
                    metrics['cache_hit_ratio'] = context.get('pg_cache_hit', [{}])[0].get('cache_hit_ratio', 0) if context.get('pg_cache_hit') else 0
                # PostgreSQL 慢查询深度分析指标（P2）
                sq = context.get('slow_query_result', {})
                if sq and sq.get('top_sql_by_latency'):
                    top3 = sq['top_sql_by_latency'][:3]
                    metrics['slow_query_top3'] = '\n'.join([
                        f"  - time={x.get('total_time_sec', 0):.3f}s, "
                        f"calls={x.get('exec_count', 0)}, "
                        f"rows={x.get('rows', 0)}, "
                        f"sql={x.get('query_text', '')[:100]}"
                        for x in top3
                    ])
                    metrics['slow_query_count'] = len(sq.get('top_sql_by_latency', []))

        # ── RAG 知识库检索（在构建 Prompt 前执行）─────────────────────
        rag_context = ''
        if self.rag_enabled and self.rag_retriever:
            try:
                rag_results = self.rag_retriever.retrieve_for_diagnosis(
                    db_type, metrics, issues, top_k=3)
                rag_context = self.rag_retriever.format_rag_context(rag_results, lang)
            except Exception:
                pass  # RAG 失败不影响 AI 诊断主流程

        prompt = self._build_prompt(db_type, label, metrics, issues, lang, rag_context)

        try:
            if self.backend == 'ollama':
                return self._call_ollama(prompt, timeout)
            else:
                return ''
        except Exception as e:
            print(f"⚠️  AI 诊断调用失败 [{self.backend}]: {e}")
            import traceback; traceback.print_exc()
            return ''

    def _call_ollama(self, prompt: str, timeout: int) -> str:
        """调用本地 Ollama API"""
        import urllib.request
        import json as _json
        url = self.api_url.rstrip('/') + '/api/generate'
        payload = _json.dumps({
            'model': self.model,
            'prompt': prompt,
            'stream': False,
            'think': False,
            'options': {'temperature': 0.3}
        }).encode('utf-8')
        req = urllib.request.Request(url, data=payload, method='POST')
        req.add_header('Content-Type', 'application/json')
        # 使用较长超时（300s），避免首次加载模型时冷启动超时；qwen3:30b 等大模型加载时间可达数分钟
        with urllib.request.urlopen(req, timeout=max(timeout, 300)) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
            raw = data.get('response', '').strip()
            # 过滤 qwen3 的 thinking 残留（如果 think:false 未生效）
            import re
            raw = re.sub(r'<\|reserved_for_thinking\|>[\s\S]*?<\|end_of_thought\|>', '', raw)
            return raw


# ═══════════════════════════════════════════════════════
#  5. 综合分析入口（供 main_mysql.py / main_pg.py 调用）
# ═══════════════════════════════════════════════════════

def run_full_analysis(db_type: str, host: str, port, label: str,
                      context: dict, base_dir: str,
                      ai_backend: str = None, ai_key: str = None,
                      ai_url: str = None, ai_model: str = None) -> dict:
    """
    一键执行完整增强分析（智能规则 + 历史存储 + AI诊断）。

    :param db_type: 'mysql'、'pg' 或 'oracle'
    :param host/port/label: 数据库信息
    :param context: checkdb() 返回的 context
    :param base_dir: 项目根目录（用于存储 history.json）
    :param ai_*: AI 诊断配置（仅支持本地 Ollama，非本地地址将被拒绝）
    :return: {
        'issues': [...],       # 增强风险列表
        'ai_advice': str,      # AI 建议文本（未启用时为空字符串）
        'trend': {...},        # 历史趋势数据
        'comparison': {...},   # 与上次对比
    }

    安全说明：AI 诊断仅使用本地 Ollama，所有数据不外传。
    """
    # 1. 增强智能分析
    if db_type == 'mysql':
        issues = smart_analyze_mysql(context)
    elif db_type == 'pg':
        issues = smart_analyze_pg(context)
    else:
        issues = smart_analyze_oracle(context)

    # 2. 保存历史并获取趋势
    hm = HistoryManager(base_dir)
    hm.save_snapshot(db_type, host, port, label, context)
    trend = hm.get_trend(db_type, host, port)
    comparison = hm.get_comparison(db_type, host, port)

    # 3. AI 诊断（可选）
    advisor = AIAdvisor(backend=ai_backend, api_key=ai_key, api_url=ai_url, model=ai_model)
    ai_advice = ''
    if advisor.enabled:
        print(f"🤖 正在调用 AI 诊断（{advisor.backend} / {advisor.model}）...")
        ai_advice = advisor.diagnose(db_type, label, context, issues)

    return {
        'issues': issues,
        'ai_advice': ai_advice,
        'trend': trend,
        'comparison': comparison,
    }
