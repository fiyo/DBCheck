# -*- coding: utf-8 -*-
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
                'col1': '连接数使用率', 'col2': '高风险',
                'col3': f'连接使用率 {usage_pct:.1f}%（{used}/{max_conn}），接近上限将拒绝新连接',
                'col4': '高', 'col5': 'DBA',
                'fix_sql': f"-- 修改 postgresql.conf：\n-- max_connections = {min(max_conn * 2, 1000)}\n-- 建议同时使用 PgBouncer 连接池"
            })
        elif usage_pct > 80:
            issues.append({
                'col1': '连接数使用率', 'col2': '中风险',
                'col3': f'连接使用率 {usage_pct:.1f}%（{used}/{max_conn}），建议关注',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': "SELECT pid, usename, application_name, state, query_start, query FROM pg_stat_activity WHERE state != 'idle' ORDER BY query_start;"
            })

    # ── 2. 缓存命中率 ────────────────────────────────────
    cache_hit = context.get('pg_cache_hit', [])
    for row in cache_hit:
        hit_rate = _float(row.get('cache_hit_ratio', 100))
        if hit_rate < 95:
            issues.append({
                'col1': '缓冲区缓存命中率低', 'col2': '高风险',
                'col3': f'缓存命中率仅 {hit_rate:.1f}%（建议 > 99%），大量数据从磁盘读取',
                'col4': '高', 'col5': 'DBA',
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
                'col1': 'shared_buffers 偏小', 'col2': '中风险',
                'col3': f'shared_buffers = {sb} pages（约 {sb_gb:.2f} GB），建议设为物理内存的 25%',
                'col4': '中', 'col5': 'DBA',
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
            'col1': '长时间运行的查询', 'col2': '高风险',
            'col3': f'发现 {len(long_queries)} 个执行超过 60 秒的查询，可能持有锁',
            'col4': '高', 'col5': 'DBA',
            'fix_sql': '\n'.join([f"SELECT pg_terminate_backend({p.get('pid', '')});  -- {str(p.get('query',''))[:60]}" for p in long_queries[:5]])
        })

    # ── 5. 锁等待 ─────────────────────────────────────────
    for p in pg_proc:
        if str(p.get('wait_event_type', '')) == 'Lock':
            issues.append({
                'col1': '存在锁等待', 'col2': '中风险',
                'col3': '当前有进程在等待锁释放，可能影响业务响应速度',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': "SELECT blocked_locks.pid AS blocked_pid, blocking_locks.pid AS blocking_pid,\n  blocked_activity.query AS blocked_query, blocking_activity.query AS blocking_query\nFROM pg_catalog.pg_locks blocked_locks\nJOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid\nJOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype\n  AND blocking_locks.granted\nJOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid\nWHERE NOT blocked_locks.granted;"
            })
            break

    # ── 6. 用户安全（超级用户过多） ──────────────────────
    pg_users = context.get('pg_users', [])
    superusers = [u for u in pg_users if str(u.get('superuser', '')).upper() in ('T', 'TRUE', 'YES', '1')]
    if len(superusers) > 2:
        issues.append({
            'col1': '超级用户数量过多', 'col2': '中风险',
            'col3': f'发现 {len(superusers)} 个超级用户，建议最小化权限，超级用户仅用于管理',
            'col4': '中', 'col5': 'DBA',
            'fix_sql': "-- 查看超级用户：\nSELECT usename, usesuper FROM pg_user WHERE usesuper;\n-- 撤销多余超级权限：\nALTER USER username NOSUPERUSER;"
        })

    # ── 7. 归档日志 ───────────────────────────────────────
    archive = _setting('archive_mode')
    if archive and str(archive).lower() == 'off':
        issues.append({
            'col1': '归档模式未开启', 'col2': '建议',
            'col3': 'archive_mode=off，无法实现 PITR（时间点恢复），生产环境建议开启',
            'col4': '低', 'col5': 'DBA',
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
                'col1': f'磁盘空间紧张 ({mp})', 'col2': '高风险',
                'col3': f'磁盘 {mp} 使用率 {usage:.1f}%，可能导致数据库停止写入',
                'col4': '高', 'col5': '系统管理员',
                'fix_sql': "-- 查找大表：\nSELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size\nFROM pg_tables ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC LIMIT 10;"
            })
        elif usage > 80:
            issues.append({
                'col1': f'磁盘空间预警 ({mp})', 'col2': '中风险',
                'col3': f'磁盘 {mp} 使用率 {usage:.1f}%',
                'col4': '中', 'col5': '系统管理员',
                'fix_sql': ''
            })

    # ── 9. 内存使用率 ────────────────────────────────────
    mem_usage = _float(context.get('system_info', {}).get('memory', {}).get('usage_percent', 0))
    if mem_usage > 90:
        issues.append({
            'col1': '系统内存使用率', 'col2': '高风险',
            'col3': f'系统内存使用率 {mem_usage:.1f}%，可能触发 OOM Killer 杀掉 PG 进程',
            'col4': '高', 'col5': '系统管理员',
            'fix_sql': ''
        })

    # ── 10. 大量 dead tuples（需要 vacuum） ─────────────
    for db in context.get('pg_db_size', []):
        dead = _int(db.get('n_dead_tup', 0))
        live = _int(db.get('n_live_tup', 1))
        if live > 0 and dead / live > 0.2 and dead > 10000:
            dbname = db.get('datname', '?')
            issues.append({
                'col1': f'{dbname} 存在大量 dead tuples', 'col2': '中风险',
                'col3': f'数据库 {dbname} dead tuples 占比 {dead/(live+dead)*100:.1f}%，建议执行 VACUUM',
                'col4': '中', 'col5': 'DBA',
                'fix_sql': f"VACUUM ANALYZE {dbname};\n-- 或全库：\nVACUUM VERBOSE ANALYZE;"
            })

    return issues


# ═══════════════════════════════════════════════════════
#  3. 历史记录管理器
# ═══════════════════════════════════════════════════════

class HistoryManager:
    """
    将每次巡检的关键指标持久化到 history.json，
    支持同一数据库实例的历史对比和趋势数据生成。

    文件位于：<SCRIPT_DIR>/history.json
    """

    def __init__(self, base_dir: str):
        self.path = os.path.join(base_dir, 'history.json')
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  历史记录保存失败: {e}")

    @staticmethod
    def _db_key(db_type: str, host: str, port) -> str:
        """生成数据库实例唯一键"""
        raw = f"{db_type}:{host}:{port}"
        return hashlib.md5(raw.encode()).hexdigest()[:12] + f"_{host}_{port}"

    def save_snapshot(self, db_type: str, host: str, port, label: str, context: dict):
        """
        从 context 提取关键指标并存入历史记录。

        :param db_type: 'mysql' 或 'pg'
        :param host: 数据库 IP
        :param port: 数据库端口
        :param label: 数据库标签名
        :param context: getData.checkdb() 返回的 context 字典
        """
        key = self._db_key(db_type, host, port)
        if key not in self._data:
            self._data[key] = {
                'db_type': db_type, 'host': host, 'port': str(port),
                'label': label, 'snapshots': []
            }

        snap = self._extract_metrics(db_type, context)
        snap['ts'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        snap['report_time'] = snap['ts']
        snap['risk_count'] = len(context.get('auto_analyze', []))
        snap['health_status'] = context.get('health_status', '未知')

        self._data[key]['snapshots'].append(snap)
        # 只保留最近 30 条
        self._data[key]['snapshots'] = self._data[key]['snapshots'][-30:]
        self._save()
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
        m['cpu_usage'] = _safe_float([sys_info.get('cpu', {})], 'usage_percent') if isinstance(sys_info.get('cpu'), dict) else sys_info.get('cpu', {}).get('usage_percent', 0)
        m['mem_usage'] = sys_info.get('memory', {}).get('usage_percent', 0)
        disks = sys_info.get('disk_list', [])
        m['disk_usage_max'] = max((d.get('usage_percent', 0) for d in disks
                                   if d.get('mountpoint', '/') not in IGNORE_MOUNTS), default=0)

        if db_type == 'mysql':
            m['connections'] = _safe_int(context.get('threads_connected', []))
            m['max_connections'] = _safe_int(context.get('max_connections', []))
            m['max_used_connections'] = _safe_int(context.get('max_used_connections', []))
            queries_data = context.get('queries', [])
            m['queries_total'] = _safe_int(queries_data)
            m['version'] = context.get('myversion', [{}])[0].get('version', '') if context.get('myversion') else ''
        else:
            pg_conn = context.get('pg_connections', [])
            m['connections'] = _safe_int(pg_conn, 'used_connections') if pg_conn else 0
            m['max_connections'] = _safe_int(pg_conn, 'max_connections') if pg_conn else 0
            cache_hits = context.get('pg_cache_hit', [])
            m['cache_hit_ratio'] = _safe_float(cache_hits, 'cache_hit_ratio') if cache_hits else 0.0
            m['version'] = context.get('pg_version', [{}])[0].get('version', '') if context.get('pg_version') else ''

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
        key = self._db_key(db_type, host, port)
        record = self._data.get(key)
        if not record or not record.get('snapshots'):
            return {}

        snaps = record['snapshots']
        labels = [s['ts'] for s in snaps]
        metric_keys = ['mem_usage', 'cpu_usage', 'disk_usage_max', 'connections',
                       'cache_hit_ratio', 'queries_total', 'max_used_connections']
        metrics = {}
        for mk in metric_keys:
            vals = [s.get(mk, None) for s in snaps]
            if any(v is not None and v != 0 for v in vals):
                metrics[mk] = [v if v is not None else 0 for v in vals]

        return {
            'labels': labels,
            'metrics': metrics,
            'risk_counts': [s.get('risk_count', 0) for s in snaps],
            'health_statuses': [s.get('health_status', '未知') for s in snaps],
            'label': record.get('label', ''),
            'snapshots_count': len(snaps)
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
        key = self._db_key(db_type, host, port)
        record = self._data.get(key)
        if not record or len(record.get('snapshots', [])) < 2:
            return {}

        snaps = record['snapshots']
        prev, curr = snaps[-2], snaps[-1]
        diff = {}
        for k in curr:
            if k in prev and isinstance(curr[k], (int, float)) and isinstance(prev[k], (int, float)):
                diff[k] = round(curr[k] - prev[k], 2)

        return {'prev': prev, 'curr': curr, 'diff': diff,
                'prev_ts': prev['ts'], 'curr_ts': curr['ts']}

    def list_instances(self) -> list:
        """列出所有已记录的数据库实例"""
        result = []
        for key, rec in self._data.items():
            result.append({
                'key': key,
                'db_type': rec.get('db_type', ''),
                'host': rec.get('host', ''),
                'port': rec.get('port', ''),
                'label': rec.get('label', ''),
                'snapshots_count': len(rec.get('snapshots', []))
            })
        return result


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
    }

    def __init__(self, backend: str = None, api_key: str = None,
                 api_url: str = None, model: str = None):
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

    @property
    def enabled(self) -> bool:
        return self.backend != 'disabled'

    def _build_prompt(self, db_type: str, label: str, metrics: dict, issues: list) -> str:
        """构建发给 LLM 的诊断 Prompt"""
        metric_lines = []
        for k, v in metrics.items():
            zh = self.METRIC_LABELS_ZH.get(k, k)
            if v is not None:
                metric_lines.append(f"  - {zh}: {v}")

        issue_lines = []
        for i, iss in enumerate(issues[:8], 1):
            issue_lines.append(f"  {i}. [{iss.get('col2','')}] {iss.get('col1','')}: {iss.get('col3','')}")

        prompt = f"""你是一位经验丰富的数据库运维专家（DBA）。
以下是对 {db_type.upper()} 数据库「{label}」的巡检数据，请给出专业的优化建议。

【关键指标】
{chr(10).join(metric_lines) or '  (无)'}

【发现的风险项】
{chr(10).join(issue_lines) or '  未发现风险项，运行状态良好'}

请基于以上数据，给出 3~5 条最重要的优化建议。要求：
1. 每条建议简洁明了，直接说明"做什么"和"为什么"
2. 如果有明确的参数调整建议，给出具体数值参考
3. 按优先级从高到低排列
4. 最后给出一句话整体评价

格式如下（直接输出，不要加额外标题）：
1. [建议内容]
2. [建议内容]
...
整体评价：[一句话评价]"""
        return prompt

    def diagnose(self, db_type: str, label: str, context: dict, issues: list,
                 timeout: int = 30) -> str:
        """
        调用 AI 后端进行诊断分析。

        :param db_type: 'mysql' 或 'pg'
        :param label: 数据库标签名
        :param context: getData.checkdb() 返回的 context
        :param issues: smart_analyze_* 返回的风险列表
        :param timeout: 请求超时秒数
        :return: AI 生成的建议文本，失败时返回空字符串
        """
        if not self.enabled:
            return ''

        # 提取关键指标（轻量版）
        sys_info = context.get('system_info', {})
        metrics = {
            'mem_usage': sys_info.get('memory', {}).get('usage_percent', 0),
            'cpu_usage': sys_info.get('cpu', {}).get('usage_percent', 0) if isinstance(sys_info.get('cpu'), dict) else 0,
            'disk_usage_max': max((d.get('usage_percent', 0) for d in sys_info.get('disk_list', [])
                                   if d.get('mountpoint', '/') not in IGNORE_MOUNTS), default=0),
            'risk_count': len(issues),
            'health_status': context.get('health_status', '未知'),
        }
        if db_type == 'mysql':
            metrics['connections'] = context.get('threads_connected', [{}])[0].get('Value', 0) if context.get('threads_connected') else 0
            metrics['max_connections'] = context.get('max_connections', [{}])[0].get('Value', 0) if context.get('max_connections') else 0
        else:
            pg_conn = context.get('pg_connections', [{}])
            if pg_conn and pg_conn[0]:
                metrics['connections'] = pg_conn[0].get('used_connections', 0)
                metrics['max_connections'] = pg_conn[0].get('max_connections', 0)
                metrics['cache_hit_ratio'] = context.get('pg_cache_hit', [{}])[0].get('cache_hit_ratio', 0) if context.get('pg_cache_hit') else 0

        prompt = self._build_prompt(db_type, label, metrics, issues)

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
        # 使用较长超时（120s），避免首次加载模型时冷启动超时
        with urllib.request.urlopen(req, timeout=max(timeout, 120)) as resp:
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

    :param db_type: 'mysql' 或 'pg'
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
    else:
        issues = smart_analyze_pg(context)

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
