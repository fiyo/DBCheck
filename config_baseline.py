# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck 配置基线与合规检查模块
=============================
提供两个核心能力：
1. MySQL 配置基线检查 —— 常见配置项的推荐值 vs 当前值对比
2. PostgreSQL 配置基线检查 —— 常见配置项的推荐值 vs 当前值对比
3. 根据数据库规模（数据量、QPS）自动计算推荐配置
4. 生成配置差距报告（当前值 vs 推荐值 + 差距分析）

推荐值计算依据：
- MySQL: 基于 innodb_buffer_pool_size、max_connections、tmp_table_size 等核心参数
- PostgreSQL: 基于 shared_buffers、effective_cache_size、maintenance_work_mem 等核心参数
"""

import os
import sys
import importlib

# ── i18n setup ──────────────────────────────────────────────────────
try:
    from i18n import get_lang
    _LANG = get_lang()
except Exception:
    _LANG = 'zh'

def _t(key, lang=None):
    """翻译接口"""
    try:
        from i18n import t as _tt
        return _tt(key, lang or _LANG)
    except Exception:
        return key


# ═══════════════════════════════════════════════════════
#  1. MySQL 配置基线
# ═══════════════════════════════════════════════════════

# MySQL 配置项基线定义
# 格式: (变量名, 当前值获取SQL, 推荐值计算函数, 单位, 说明)
MYSQL_BASELINE_RULES = [
    # 核心内存参数
    ('innodb_buffer_pool_size', 
     "SHOW GLOBAL VARIABLES LIKE 'innodb_buffer_pool_size';",
     lambda conn, ctx: _calc_innodb_buffer_pool(conn),
     '字节',
     'InnoDB 缓冲池大小，应设置为总内存的 60-80%'),
    
    ('max_connections',
     "SHOW GLOBAL VARIABLES LIKE 'max_connections';",
     lambda conn, ctx: _calc_max_connections(conn, ctx),
     '个',
     '最大连接数，应根据 QPS 和平均连接时长计算'),
    
    ('tmp_table_size',
     "SHOW GLOBAL VARIABLES LIKE 'tmp_table_size';",
     lambda conn, ctx: _calc_tmp_table_size(conn),
     '字节',
     '临时表大小，应与 max_heap_table_size 保持一致'),
    
    ('max_heap_table_size',
     "SHOW GLOBAL VARIABLES LIKE 'max_heap_table_size';",
     lambda conn, ctx: _calc_max_heap_table_size(conn),
     '字节',
     '内存表大小，应与 tmp_table_size 保持一致'),
    
    # 日志参数
    ('innodb_log_file_size',
     "SHOW GLOBAL VARIABLES LIKE 'innodb_log_file_size';",
     lambda conn, ctx: _calc_innodb_log_file_size(conn, ctx),
     '字节',
     'Redo 日志文件大小，建议 256MB-1GB'),
    
    ('innodb_log_buffer_size',
     "SHOW GLOBAL VARIABLES LIKE 'innodb_log_buffer_size';",
     lambda conn, ctx: _calc_innodb_log_buffer_size(conn),
     '字节',
     '日志缓冲区大小，默认 16MB 足够'),
    
    ('sync_binlog',
     "SHOW GLOBAL VARIABLES LIKE 'sync_binlog';",
     lambda conn, ctx: 1,
     '次',
     'Binlog 同步频率，高并发写入建议设为 1'),
    
    ('innodb_flush_log_at_trx_commit',
     "SHOW GLOBAL VARIABLES LIKE 'innodb_flush_log_at_trx_commit';",
     lambda conn, ctx: 1,
     '次',
     '事务提交时日志刷盘策略，1=严格刷盘，安全性最高'),
    
    # 缓存参数
    ('query_cache_size',
     "SHOW GLOBAL VARIABLES LIKE 'query_cache_size';",
     lambda conn, ctx: 0,  # MySQL 8.0 已移除查询缓存
     '字节',
     'MySQL 8.0 已移除查询缓存，此项仅供参考'),
    
    ('table_open_cache',
     "SHOW GLOBAL VARIABLES LIKE 'table_open_cache';",
     lambda conn, ctx: _calc_table_open_cache(conn, ctx),
     '个',
     '表缓存大小，应设置为 max_connections * 表数量 / 线程数'),
    
    ('table_definition_cache',
     "SHOW GLOBAL VARIABLES LIKE 'table_definition_cache';",
     lambda conn, ctx: _calc_table_definition_cache(conn, ctx),
     '个',
     '表定义缓存，应设置为足够容纳所有表'),
    
    # 线程参数
    ('thread_cache_size',
     "SHOW GLOBAL VARIABLES LIKE 'thread_cache_size';",
     lambda conn, ctx: _calc_thread_cache_size(conn, ctx),
     '个',
     '线程缓存大小，建议设置为 50+ 或 CPU 核心数的 2-4 倍'),
    
    ('innodb_thread_concurrency',
     "SHOW GLOBAL VARIABLES LIKE 'innodb_thread_concurrency';",
     lambda conn, ctx: _calc_innodb_thread_concurrency(conn),
     '个',
     'InnoDB 线程并发数，建议设置为 CPU 核心数的 2 倍'),
    
    # I/O 参数
    ('innodb_io_capacity',
     "SHOW GLOBAL VARIABLES LIKE 'innodb_io_capacity';",
     lambda conn, ctx: _calc_innodb_io_capacity(conn),
     'IOPS',
     'InnoDB I/O 容量，应根据磁盘 IOPS 能力设置'),
    
    ('innodb_io_capacity_max',
     "SHOW GLOBAL VARIABLES LIKE 'innodb_io_capacity_max';",
     lambda conn, ctx: _calc_innodb_io_capacity_max(conn),
     'IOPS',
     'InnoDB 最大 I/O 容量，应设置为 io_capacity 的 2 倍'),
    
    # 网络参数
    ('max_allowed_packet',
     "SHOW GLOBAL VARIABLES LIKE 'max_allowed_packet';",
     lambda conn, ctx: _calc_max_allowed_packet(conn),
     '字节',
     '最大数据包大小，建议 16MB-64MB'),
    
    ('wait_timeout',
     "SHOW GLOBAL VARIABLES LIKE 'wait_timeout';",
     lambda conn, ctx: _calc_wait_timeout(conn),
     '秒',
     '空闲连接超时，建议 300-600 秒'),
    
    ('interactive_timeout',
     "SHOW GLOBAL VARIABLES LIKE 'interactive_timeout';",
     lambda conn, ctx: _calc_interactive_timeout(conn),
     '秒',
     '交互式连接超时，建议与 wait_timeout 保持一致'),
    
    # 排序参数
    ('sort_buffer_size',
     "SHOW GLOBAL VARIABLES LIKE 'sort_buffer_size';",
     lambda conn, ctx: _calc_sort_buffer_size(conn),
     '字节',
     '排序缓冲区大小，建议 2-4MB'),
    
    ('join_buffer_size',
     "SHOW GLOBAL VARIABLES LIKE 'join_buffer_size';",
     lambda conn, ctx: _calc_join_buffer_size(conn),
     '字节',
     'join 缓冲区大小，建议 1-2MB'),
    
    ('read_buffer_size',
     "SHOW GLOBAL VARIABLES LIKE 'read_buffer_size';",
     lambda conn, ctx: _calc_read_buffer_size(conn),
     '字节',
     '读缓冲区大小，建议 1-2MB'),
    
    ('read_rnd_buffer_size',
     "SHOW GLOBAL VARIABLES LIKE 'read_rnd_buffer_size';",
     lambda conn, ctx: _calc_read_rnd_buffer_size(conn),
     '字节',
     '随机读缓冲区大小，建议 1-4MB'),
    
    # Long Query Time
    ('long_query_time',
     "SHOW GLOBAL VARIABLES LIKE 'long_query_time';",
     lambda conn, ctx: _calc_long_query_time(conn),
     '秒',
     '慢查询阈值，建议 1-2 秒'),
]


def _get_db_size_gb(conn):
    """获取数据库总大小（GB）"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024 / 1024, 2) AS db_size_gb
            FROM information_schema.tables
            WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
        """)
        result = cursor.fetchone()
        cursor.close()
        return float(result[0]) if result and result[0] else 0.0
    except Exception:
        return 0.0


def _get_qps(conn):
    """获取数据库 QPS（每秒查询数）"""
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW GLOBAL STATUS LIKE 'Queries';")
        queries1 = int(cursor.fetchone()[1])
        import time
        time.sleep(1)
        cursor.execute("SHOW GLOBAL STATUS LIKE 'Queries';")
        queries2 = int(cursor.fetchone()[1])
        cursor.close()
        return queries2 - queries1
    except Exception:
        return 0


def _get_total_memory_gb():
    """获取主机总内存（GB）"""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024**3)
    except Exception:
        return 0.0


def _parse_bytes(value_str):
    """解析字节字符串为字节数"""
    if not value_str:
        return 0
    value_str = str(value_str).strip().upper()
    try:
        if value_str.endswith('G'):
            return int(float(value_str[:-1]) * 1024 * 1024 * 1024)
        elif value_str.endswith('M'):
            return int(float(value_str[:-1]) * 1024 * 1024)
        elif value_str.endswith('K'):
            return int(float(value_str[:-1]) * 1024)
        elif value_str.endswith('T'):
            return int(float(value_str[:-1]) * 1024 * 1024 * 1024 * 1024)
        else:
            return int(value_str)
    except Exception:
        return 0


def _format_bytes(bytes_val):
    """格式化字节数为人类可读格式"""
    if bytes_val >= 1024**4:
        return f"{bytes_val / (1024**4):.1f}T"
    elif bytes_val >= 1024**3:
        return f"{bytes_val / (1024**3):.1f}G"
    elif bytes_val >= 1024**2:
        return f"{bytes_val / (1024**2):.1f}M"
    elif bytes_val >= 1024:
        return f"{bytes_val / 1024:.1f}K"
    else:
        return f"{bytes_val}B"


# ── MySQL 推荐值计算函数 ──────────────────────────────────────────

def _calc_innodb_buffer_pool(conn):
    """计算 InnoDB 缓冲池推荐大小（总内存的 60-80%）"""
    total_mem = _get_total_memory_gb()
    if total_mem == 0:
        total_mem = 8  # 默认 8GB
    # 缓冲区设置为总内存的 70%
    target_gb = total_mem * 0.7
    # 向下取整到 1GB
    target_bytes = int(target_gb) * 1024 * 1024 * 1024
    return max(target_bytes, 1024**3)  # 最小 1GB


def _calc_max_connections(conn, ctx):
    """计算最大连接数推荐值"""
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW GLOBAL STATUS LIKE 'Max_used_connections';")
        result = cursor.fetchone()
        max_used = int(result[1]) if result else 0
        cursor.close()
        
        # 推荐值 = 历史最大使用 * 1.5，或基础值 2000
        if max_used > 0:
            return int(max_used * 1.5)
        return 2000
    except Exception:
        return 2000


def _calc_tmp_table_size(conn):
    """计算临时表大小推荐值"""
    return 64 * 1024 * 1024  # 64MB


def _calc_max_heap_table_size(conn):
    """计算内存表大小推荐值"""
    return 64 * 1024 * 1024  # 64MB


def _calc_innodb_log_file_size(conn, ctx):
    """计算 InnoDB 日志文件大小推荐值"""
    db_size = ctx.get('db_size_gb', 0)
    if db_size == 0:
        db_size = _get_db_size_gb(conn)
    
    # 日志大小根据数据量计算
    if db_size < 10:
        return 256 * 1024 * 1024  # 256MB
    elif db_size < 100:
        return 512 * 1024 * 1024  # 512MB
    else:
        return 1024 * 1024 * 1024  # 1GB


def _calc_innodb_log_buffer_size(conn):
    """计算 InnoDB 日志缓冲区推荐值"""
    return 16 * 1024 * 1024  # 16MB


def _calc_table_open_cache(conn, ctx):
    """计算表缓存大小推荐值"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys');
        """)
        result = cursor.fetchone()
        table_count = int(result[0]) if result else 100
        cursor.close()
        return max(table_count * 2, 4000)
    except Exception:
        return 4000


def _calc_table_definition_cache(conn, ctx):
    """计算表定义缓存推荐值"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys');
        """)
        result = cursor.fetchone()
        table_count = int(result[0]) if result else 100
        cursor.close()
        return max(table_count, 2000)
    except Exception:
        return 2000


def _calc_thread_cache_size(conn, ctx):
    """计算线程缓存大小推荐值"""
    try:
        import psutil
        cpu_count = psutil.cpu_count() or 4
        return cpu_count * 4
    except Exception:
        return 16


def _calc_innodb_thread_concurrency(conn):
    """计算 InnoDB 线程并发数推荐值"""
    try:
        import psutil
        cpu_count = psutil.cpu_count() or 4
        return cpu_count * 2
    except Exception:
        return 8


def _calc_innodb_io_capacity(conn):
    """计算 InnoDB I/O 容量推荐值"""
    try:
        import psutil
        # 估算：根据磁盘类型
        disk = psutil.disk_usage('/')
        # SSD 估算 20000 IOPS，HDD 估算 200 IOPS
        # 这里简化处理，默认 SSD
        return 20000
    except Exception:
        return 2000


def _calc_innodb_io_capacity_max(conn):
    """计算 InnoDB 最大 I/O 容量推荐值"""
    return 40000  # io_capacity 的 2 倍


def _calc_max_allowed_packet(conn):
    """计算最大数据包推荐值"""
    return 16 * 1024 * 1024  # 16MB


def _calc_wait_timeout(conn):
    """计算等待超时推荐值"""
    return 300  # 5 分钟


def _calc_interactive_timeout(conn):
    """计算交互超时推荐值"""
    return 300  # 5 分钟


def _calc_sort_buffer_size(conn):
    """计算排序缓冲区推荐值"""
    return 4 * 1024 * 1024  # 4MB


def _calc_join_buffer_size(conn):
    """计算 join 缓冲区推荐值"""
    return 2 * 1024 * 1024  # 2MB


def _calc_read_buffer_size(conn):
    """计算读缓冲区推荐值"""
    return 2 * 1024 * 1024  # 2MB


def _calc_read_rnd_buffer_size(conn):
    """计算随机读缓冲区推荐值"""
    return 4 * 1024 * 1024  # 4MB


def _calc_long_query_time(conn):
    """计算慢查询阈值推荐值"""
    return 2.0  # 2 秒


def check_mysql_config_baseline(conn):
    """
    检查 MySQL 配置基线，返回配置差距报告。
    
    返回格式:
    {
        'db_size_gb': float,  # 数据库总大小
        'qps': int,           # 每秒查询数
        'total_memory_gb': float,  # 主机总内存
        'items': [
            {
                'param': str,        # 参数名
                'current': str,      # 当前值（格式化）
                'recommended': str,  # 推荐值（格式化）
                'current_raw': int,  # 当前值（字节）
                'recommended_raw': int,  # 推荐值（字节）
                'gap': str,          # 差距（格式化）
                'gap_pct': float,    # 差距百分比
                'severity': str,     # 严重程度：critical/warning/info
                'description': str,  # 说明
            },
            ...
        ],
        'summary': {
            'critical_count': int,  # 严重问题数
            'warning_count': int,   # 警告问题数
            'info_count': int,     # 提示数
        }
    }
    """
    result = {
        'db_size_gb': _get_db_size_gb(conn),
        'qps': _get_qps(conn),
        'total_memory_gb': _get_total_memory_gb(),
        'items': [],
        'summary': {'critical_count': 0, 'warning_count': 0, 'info_count': 0}
    }
    
    cursor = conn.cursor()
    ctx = {'db_size_gb': result['db_size_gb'], 'qps': result['qps']}
    
    for rule in MYSQL_BASELINE_RULES:
        param_name = rule[0]
        query_sql = rule[1]
        calc_func = rule[2]
        unit = rule[3]
        description = rule[4]
        
        try:
            # 获取当前值
            cursor.execute(query_sql)
            row = cursor.fetchone()
            if row:
                current_raw = _parse_bytes(row[1])
            else:
                current_raw = 0
            
            # 计算推荐值
            recommended_raw = calc_func(conn, ctx)
            
            # 计算差距
            if recommended_raw > 0 and current_raw > 0:
                gap_pct = abs(current_raw - recommended_raw) / recommended_raw * 100
            elif recommended_raw == 0 and current_raw > 0:
                gap_pct = 100  # 已废弃参数但仍设置值
            else:
                gap_pct = 0
            
            # 判断严重程度
            if param_name == 'query_cache_size':
                # MySQL 8.0 已移除查询缓存，忽略
                severity = 'info'
            elif gap_pct == 0:
                severity = 'info'
            elif gap_pct > 50:
                severity = 'critical'
            elif gap_pct > 20:
                severity = 'warning'
            else:
                severity = 'info'
            
            # 更新统计
            if severity == 'critical':
                result['summary']['critical_count'] += 1
            elif severity == 'warning':
                result['summary']['warning_count'] += 1
            else:
                result['summary']['info_count'] += 1
            
            result['items'].append({
                'param': param_name,
                'current': _format_bytes(current_raw) if unit == '字节' else str(current_raw),
                'recommended': _format_bytes(recommended_raw) if unit == '字节' else str(recommended_raw),
                'current_raw': current_raw,
                'recommended_raw': recommended_raw,
                'gap': _format_bytes(abs(current_raw - recommended_raw)) if unit == '字节' else f"{abs(current_raw - recommended_raw)}",
                'gap_pct': round(gap_pct, 1),
                'severity': severity,
                'description': description,
                'unit': unit,
            })
            
        except Exception as e:
            # 忽略单条查询错误
            pass
    
    cursor.close()
    return result


# ═══════════════════════════════════════════════════════
#  2. PostgreSQL 配置基线
# ═══════════════════════════════════════════════════════

# PostgreSQL 配置项基线定义
PG_BASELINE_RULES = [
    ('shared_buffers',
     "SHOW shared_buffers;",
     lambda conn, ctx: _calc_pg_shared_buffers(conn, ctx),
     '字节',
     '共享缓冲区大小，建议设置为总内存的 25%'),
    
    ('effective_cache_size',
     "SHOW effective_cache_size;",
     lambda conn, ctx: _calc_pg_effective_cache_size(conn, ctx),
     '字节',
     '有效缓存大小，建议设置为总内存的 75%'),
    
    ('maintenance_work_mem',
     "SHOW maintenance_work_mem;",
     lambda conn, ctx: _calc_pg_maintenance_work_mem(conn),
     '字节',
     '维护操作内存，建议 256MB-1GB'),
    
    ('work_mem',
     "SHOW work_mem;",
     lambda conn, ctx: _calc_pg_work_mem(conn, ctx),
     '字节',
     '工作内存，根据并发和排序需求设置'),
    
    ('max_connections',
     "SHOW max_connections;",
     lambda conn, ctx: _calc_pg_max_connections(conn, ctx),
     '个',
     '最大连接数，建议 200-1000'),
    
    ('temp_buffers',
     "SHOW temp_buffers;",
     lambda conn, ctx: _calc_pg_temp_buffers(conn),
     '字节',
     '临时缓冲区大小'),
    
    ('max_prepared_transactions',
     "SHOW max_prepared_transactions;",
     lambda conn, ctx: _calc_pg_max_prepared_transactions(conn),
     '个',
     '预处理事务数'),
    
    ('wal_buffers',
     "SHOW wal_buffers;",
     lambda conn, ctx: _calc_pg_wal_buffers(conn),
     '字节',
     'WAL 缓冲区大小'),
    
    ('checkpoint_completion_target',
     "SHOW checkpoint_completion_target;",
     lambda conn, ctx: 0.9,
     '比例',
     '检查点完成目标，建议 0.9'),
    
    ('max_wal_size',
     "SHOW max_wal_size;",
     lambda conn, ctx: _calc_pg_max_wal_size(conn, ctx),
     '字节',
     '最大 WAL 大小'),
    
    ('min_wal_size',
     "SHOW min_wal_size;",
     lambda conn, ctx: _calc_pg_min_wal_size(conn),
     '字节',
     '最小 WAL 大小'),
    
    ('random_page_cost',
     "SHOW random_page_cost;",
     lambda conn, ctx: _calc_pg_random_page_cost(conn),
     '倍数',
     '随机页成本，SSD 建议 1.1，HDD 建议 4.0'),
    
    ('effective_io_concurrency',
     "SHOW effective_io_concurrency;",
     lambda conn, ctx: _calc_pg_effective_io_concurrency(conn),
     '个',
     '有效 I/O 并发数，SSD 建议 200'),
    
    ('shared_preload_libraries',
     "SHOW shared_preload_libraries;",
     lambda conn, ctx: _check_pg_preload_libraries(conn),
     '字符串',
     '预加载库，建议包含 pg_stat_statements'),
    
    ('track_activities',
     "SHOW track_activities;",
     lambda conn, ctx: 'on',
     '开关',
     '活动追踪，建议开启'),
    
    ('track_counts',
     "SHOW track_counts;",
     lambda conn, ctx: 'on',
     '开关',
     '统计追踪，建议开启'),
    
    ('track_io_timing',
     "SHOW track_io_timing;",
     lambda conn, ctx: _calc_pg_track_io_timing(conn),
     '开关',
     'I/O 计时追踪'),
    
    ('track_functions',
     "SHOW track_functions;",
     lambda conn, ctx: _calc_pg_track_functions(conn),
     '开关',
     '函数追踪'),
    
    ('autovacuum',
     "SHOW autovacuum;",
     lambda conn, ctx: 'on',
     '开关',
     '自动清理，建议开启'),
    
    ('log_destination',
     "SHOW log_destination;",
     lambda conn, ctx: 'csvlog',
     '字符串',
     '日志目标'),
    
    ('logging_collector',
     "SHOW logging_collector;",
     lambda conn, ctx: 'on',
     '开关',
     '日志收集器，建议开启'),
    
    ('log_min_duration_statement',
     "SHOW log_min_duration_statement;",
     lambda conn, ctx: _calc_pg_log_min_duration(conn),
     '毫秒',
     '慢查询日志阈值，建议 1000-3000'),
]


def _calc_pg_shared_buffers(conn, ctx):
    """计算 PostgreSQL 共享缓冲区推荐值（总内存的 25%）"""
    total_mem = _get_total_memory_gb()
    if total_mem == 0:
        total_mem = 8  # 默认 8GB
    target_gb = total_mem * 0.25
    # 向下取整到 128MB
    target_bytes = int(target_gb * 0.8) * 1024 * 1024 * 1024
    return max(target_bytes, 128 * 1024 * 1024)  # 最小 128MB


def _calc_pg_effective_cache_size(conn, ctx):
    """计算 PostgreSQL 有效缓存推荐值（总内存的 75%）"""
    total_mem = _get_total_memory_gb()
    if total_mem == 0:
        total_mem = 8
    target_gb = total_mem * 0.75
    target_bytes = int(target_gb) * 1024 * 1024 * 1024
    return max(target_bytes, 512 * 1024 * 1024)  # 最小 512MB


def _calc_pg_maintenance_work_mem(conn):
    """计算 PostgreSQL 维护内存推荐值"""
    return 512 * 1024 * 1024  # 512MB


def _calc_pg_work_mem(conn, ctx):
    """计算 PostgreSQL 工作内存推荐值"""
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW max_connections;")
        max_conn = int(cursor.fetchone()[0])
        cursor.close()
        
        total_mem = _get_total_memory_gb()
        if total_mem == 0:
            total_mem = 8
        
        # work_mem = (总内存 * 0.25) / max_connections
        target_bytes = (total_mem * 0.25 * 1024 * 1024 * 1024) / max_conn
        return max(int(target_bytes), 4 * 1024 * 1024)  # 最小 4MB
    except Exception:
        return 4 * 1024 * 1024


def _calc_pg_max_connections(conn, ctx):
    """计算 PostgreSQL 最大连接数推荐值"""
    return 200


def _calc_pg_temp_buffers(conn):
    """计算 PostgreSQL 临时缓冲区推荐值"""
    return 8 * 1024 * 1024  # 8MB


def _calc_pg_max_prepared_transactions(conn):
    """计算 PostgreSQL 最大预处理事务推荐值"""
    return 0  # 默认 0，如需两阶段提交则需设置


def _calc_pg_wal_buffers(conn):
    """计算 PostgreSQL WAL 缓冲区推荐值"""
    return 16 * 1024 * 1024  # 16MB


def _calc_pg_max_wal_size(conn, ctx):
    """计算 PostgreSQL 最大 WAL 大小推荐值"""
    return 2 * 1024 * 1024 * 1024  # 2GB


def _calc_pg_min_wal_size(conn):
    """计算 PostgreSQL 最小 WAL 大小推荐值"""
    return 256 * 1024 * 1024  # 256MB


def _calc_pg_random_page_cost(conn):
    """计算 PostgreSQL 随机页成本推荐值"""
    # 假设 SSD
    return 1.1


def _calc_pg_effective_io_concurrency(conn):
    """计算 PostgreSQL 有效 I/O 并发推荐值"""
    return 200


def _check_pg_preload_libraries(conn):
    """检查 PostgreSQL 预加载库"""
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW shared_preload_libraries;")
        result = cursor.fetchone()
        cursor.close()
        if result and 'pg_stat_statements' in str(result[0]):
            return 'pg_stat_statements'
        return ''  # 建议添加 pg_stat_statements
    except Exception:
        return ''


def _calc_pg_track_io_timing(conn):
    """计算 PostgreSQL I/O 计时追踪推荐值"""
    return 'on'


def _calc_pg_track_functions(conn):
    """计算 PostgreSQL 函数追踪推荐值"""
    return 'pl'  # 仅追踪过程语言函数


def _calc_pg_log_min_duration(conn):
    """计算 PostgreSQL 慢查询日志阈值推荐值"""
    return 3000  # 3 秒


def _parse_pg_value(value_str):
    """解析 PostgreSQL 配置值"""
    if not value_str:
        return 0
    value_str = str(value_str).strip()
    try:
        if value_str.endswith('MB'):
            return int(float(value_str[:-2]) * 1024 * 1024)
        elif value_str.endswith('GB'):
            return int(float(value_str[:-2]) * 1024 * 1024 * 1024)
        elif value_str.endswith('KB'):
            return int(float(value_str[:-2]) * 1024)
        elif value_str.endswith('kB'):
            return int(float(value_str[:-2]) * 1024)
        elif value_str.endswith('TB'):
            return int(float(value_str[:-2]) * 1024 * 1024 * 1024 * 1024)
        elif value_str.endswith('ms'):
            return int(value_str[:-2])
        elif value_str.endswith('s'):
            return int(value_str[:-1])
        else:
            # 可能是数字或 on/off
            if value_str.lower() in ('on', 'off'):
                return 1 if value_str.lower() == 'on' else 0
            try:
                return int(float(value_str))
            except Exception:
                return 0
    except Exception:
        return 0


def _format_pg_value(bytes_val, unit):
    """格式化 PostgreSQL 配置值"""
    if unit == '字节':
        return _format_bytes(bytes_val)
    elif unit == '开关':
        return '开启' if bytes_val == 1 else '关闭'
    else:
        return str(bytes_val)


def check_pg_config_baseline(conn):
    """
    检查 PostgreSQL 配置基线，返回配置差距报告。
    
    返回格式同 MySQL。
    """
    result = {
        'db_size_gb': _get_db_size_gb(conn),
        'qps': _get_qps(conn),
        'total_memory_gb': _get_total_memory_gb(),
        'items': [],
        'summary': {'critical_count': 0, 'warning_count': 0, 'info_count': 0}
    }
    
    cursor = conn.cursor()
    ctx = {'db_size_gb': result['db_size_gb'], 'qps': result['qps']}
    
    for rule in PG_BASELINE_RULES:
        param_name = rule[0]
        query_sql = rule[1]
        calc_func = rule[2]
        unit = rule[3]
        description = rule[4]
        
        try:
            cursor.execute(query_sql)
            row = cursor.fetchone()
            if row:
                current_raw = _parse_pg_value(row[0])
            else:
                current_raw = 0
            
            recommended_raw = calc_func(conn, ctx)
            
            # 计算差距
            if recommended_raw > 0 and current_raw > 0:
                gap_pct = abs(current_raw - recommended_raw) / recommended_raw * 100
            elif recommended_raw == 0 and current_raw > 0:
                gap_pct = 100
            else:
                gap_pct = 0
            
            # 判断严重程度
            if gap_pct == 0:
                severity = 'info'
            elif gap_pct > 50:
                severity = 'critical'
            elif gap_pct > 20:
                severity = 'warning'
            else:
                severity = 'info'
            
            if severity == 'critical':
                result['summary']['critical_count'] += 1
            elif severity == 'warning':
                result['summary']['warning_count'] += 1
            else:
                result['summary']['info_count'] += 1
            
            result['items'].append({
                'param': param_name,
                'current': _format_pg_value(current_raw, unit),
                'recommended': _format_pg_value(recommended_raw, unit),
                'current_raw': current_raw,
                'recommended_raw': recommended_raw,
                'gap': _format_bytes(abs(current_raw - recommended_raw)) if unit == '字节' else f"{abs(current_raw - recommended_raw)}",
                'gap_pct': round(gap_pct, 1),
                'severity': severity,
                'description': description,
                'unit': unit,
            })
            
        except Exception as e:
            pass
    
    cursor.close()
    return result


# ═══════════════════════════════════════════════════════
#  3. Oracle 配置基线
# ═══════════════════════════════════════════════════════

def _get_oracle_db_size_gb(conn):
    """获取 Oracle 数据库总大小（GB）"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT NVL(SUM(bytes) / 1024 / 1024 / 1024, 0)
            FROM dba_data_files
        """)
        result = cursor.fetchone()[0] or 0
        cursor.close()
        return float(result)
    except Exception:
        return 0.0

def _get_oracle_total_memory_gb():
    """获取主机总内存（GB）"""
    return _get_total_memory_gb()

def _get_oracle_cpu_cores():
    """获取 CPU 核心数"""
    try:
        import psutil
        return psutil.cpu_count() or 4
    except Exception:
        return 4

ORACLE_BASELINE_RULES = [
    ('memory_target',
     "SELECT value FROM v$parameter WHERE name = 'memory_target'",
     lambda conn, ctx: _get_oracle_total_memory_gb() * 0.85 * 1024 * 1024 * 1024,
     '字节',
     '内存目标大小（SGA+PGA），建议设为物理内存的 85%'),
    ('sga_target',
     "SELECT value FROM v$parameter WHERE name = 'sga_target'",
     lambda conn, ctx: _get_oracle_total_memory_gb() * 0.6 * 1024 * 1024 * 1024,
     '字节',
     'SGA 目标大小，建议设为总内存的 60%'),
    ('pga_aggregate_target',
     "SELECT value FROM v$parameter WHERE name = 'pga_aggregate_target'",
     lambda conn, ctx: _get_oracle_total_memory_gb() * 0.25 * 1024 * 1024 * 1024,
     '字节',
     'PGA 目标大小，建议设为总内存的 25%'),
    ('processes',
     "SELECT value FROM v$parameter WHERE name = 'processes'",
     lambda conn, ctx: max(150, _get_oracle_cpu_cores() * 50),
     '个',
     '最大进程数，建议根据 CPU 核心数和连接密度设置'),
    ('open_cursors',
     "SELECT value FROM v$parameter WHERE name = 'open_cursors'",
     lambda conn, ctx: 500,
     '个',
     '单会话最大打开游标数，建议 300-500'),
    ('session_cached_cursors',
     "SELECT value FROM v$parameter WHERE name = 'session_cached_cursors'",
     lambda conn, ctx: 50,
     '个',
     '会话缓存游标数，建议 50'),
    ('log_buffer',
     "SELECT value FROM v$parameter WHERE name = 'log_buffer'",
     lambda conn, ctx: max(int(8 * 1024 * 1024), min(int(64 * 1024 * 1024), int(_get_oracle_total_memory_gb() * 0.01 * 1024 * 1024))),
     '字节',
     '日志缓冲区大小，建议 8-64MB'),
    ('undo_retention',
     "SELECT value FROM v$parameter WHERE name = 'undo_retention'",
     lambda conn, ctx: 3600,
     '秒',
     'Undo 保留时间，建议 3600 秒（1小时）'),
    ('fast_start_mttr_target',
     "SELECT value FROM v$parameter WHERE name = 'fast_start_mttr_target'",
     lambda conn, ctx: 300,
     '秒',
     'MTTR 目标，建议 300 秒'),
    ('db_file_multiblock_read_count',
     "SELECT value FROM v$parameter WHERE name = 'db_file_multiblock_read_count'",
     lambda conn, ctx: 128,
     '块',
     '多块读计数，建议 128'),
    ('statistics_level',
     "SELECT value FROM v$parameter WHERE name = 'statistics_level'",
     lambda conn, ctx: 0,  # TYPICAL=typical
     '模式',
     '统计级别，建议 TYPICAL（值为 "TYPICAL"）'),
    ('control_file_record_keep_time',
     "SELECT value FROM v$parameter WHERE name = 'control_file_record_keep_time'",
     lambda conn, ctx: 7,
     '天',
     '控制文件记录保留天数，建议 7 天'),
]

def _parse_oracle_value(value_str, unit):
    """解析 Oracle 参数值"""
    if not value_str:
        return 0
    value_str = str(value_str).strip().upper()
    if unit == '模式':
        return 1 if value_str == 'TYPICAL' else (2 if value_str == 'ALL' else 0)
    try:
        if 'G' in value_str:
            return int(float(value_str.replace('G', '')) * 1024 * 1024 * 1024)
        elif 'M' in value_str:
            return int(float(value_str.replace('M', '')) * 1024 * 1024)
        elif 'K' in value_str:
            return int(float(value_str.replace('K', '')) * 1024)
        else:
            return int(value_str)
    except Exception:
        return 0

def _format_oracle_value(bytes_val, unit):
    """格式化 Oracle 配置值为可读字符串"""
    if unit == '模式':
        return 'TYPICAL' if bytes_val == 1 else ('ALL' if bytes_val == 2 else 'BASIC')
    if unit == '个' or unit == '块' or unit == '天' or unit == '秒':
        return str(int(bytes_val))
    return _format_bytes(int(bytes_val))

def check_oracle_config_baseline(conn):
    """
    检查 Oracle 配置基线，返回配置差距报告。
    """
    result = {
        'db_size_gb': _get_oracle_db_size_gb(conn),
        'qps': 0,
        'total_memory_gb': _get_oracle_total_memory_gb(),
        'items': [],
        'summary': {'critical_count': 0, 'warning_count': 0, 'info_count': 0}
    }
    cursor = conn.cursor()
    ctx = {'db_size_gb': result['db_size_gb'], 'qps': 0}

    for rule in ORACLE_BASELINE_RULES:
        param_name = rule[0]
        query_sql = rule[1]
        calc_func = rule[2]
        unit = rule[3]
        description = rule[4]

        try:
            cursor.execute(query_sql)
            row = cursor.fetchone()
            if row:
                current_raw = _parse_oracle_value(row[0], unit)
            else:
                current_raw = 0

            recommended_raw = calc_func(conn, ctx)

            if unit == '模式':
                # 特殊处理：比较字符串
                gap_pct = 0 if (current_raw == 1 and recommended_raw == 0) else (50 if current_raw != recommended_raw else 0)
                severity = 'info' if gap_pct == 0 else ('critical' if current_raw == 0 else 'warning')
            elif recommended_raw > 0 and current_raw > 0:
                gap_pct = abs(current_raw - recommended_raw) / recommended_raw * 100
            elif recommended_raw == 0 and current_raw > 0:
                gap_pct = 100
            else:
                gap_pct = 0

            if unit == '模式':
                severity = 'info' if gap_pct == 0 else ('critical' if current_raw == 0 else 'warning')
            elif gap_pct == 0:
                severity = 'info'
            elif gap_pct > 50:
                severity = 'critical'
            elif gap_pct > 20:
                severity = 'warning'
            else:
                severity = 'info'

            if severity == 'critical':
                result['summary']['critical_count'] += 1
            elif severity == 'warning':
                result['summary']['warning_count'] += 1
            else:
                result['summary']['info_count'] += 1

            result['items'].append({
                'param': param_name,
                'current': _format_oracle_value(current_raw, unit),
                'recommended': _format_oracle_value(recommended_raw, unit),
                'current_raw': current_raw,
                'recommended_raw': recommended_raw,
                'gap': _format_oracle_value(abs(current_raw - recommended_raw), unit),
                'gap_pct': round(gap_pct, 1),
                'severity': severity,
                'description': description,
                'unit': unit,
            })
        except Exception:
            pass

    cursor.close()
    return result


# ═══════════════════════════════════════════════════════
#  4. DM8 配置基线
# ═══════════════════════════════════════════════════════

def _get_dm_db_size_gb(conn):
    """获取 DM8 数据库总大小（GB）"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT NVL(SUM(PAGES)*8192 / 1024 / 1024 / 1024, 0)
            FROM V$DATAFILE
        """)
        result = cursor.fetchone()[0] or 0
        cursor.close()
        return float(result)
    except Exception:
        return 0.0

def _get_dm_total_memory_gb():
    """获取主机总内存（GB）"""
    return _get_total_memory_gb()

DM_BASELINE_RULES = [
    ('MEMORY_TARGET',
     "SELECT SF_GET_PARAMLINK_VALUE('MEMORY_TARGET');",
     lambda conn, ctx: _get_dm_total_memory_gb() * 1024 * 0.85,
     '字节',
     '内存目标大小，建议设为物理内存的 85%'),
    ('SGA_TARGET',
     "SELECT SF_GET_PARAMLINK_VALUE('SGA_TARGET');",
     lambda conn, ctx: _get_dm_total_memory_gb() * 1024 * 0.6,
     '字节',
     'SGA 目标大小，建议设为总内存的 60%'),
    ('PGA_TARGET',
     "SELECT SF_GET_PARAMLINK_VALUE('PGA_TARGET');",
     lambda conn, ctx: _get_dm_total_memory_gb() * 1024 * 0.25,
     '字节',
     'PGA 目标大小，建议设为总内存的 25%'),
    ('MAX_SESSIONS',
     "SELECT SF_GET_PARAMLINK_VALUE('MAX_SESSIONS');",
     lambda conn, ctx: 1000,
     '个',
     '最大会话数，建议 1000'),
    ('OPEN_CURSORS',
     "SELECT SF_GET_PARAMLINK_VALUE('OPEN_CURSORS');",
     lambda conn, ctx: 500,
     '个',
     '单会话最大打开游标数，建议 500'),
    ('UNDO_RETENTION',
     "SELECT SF_GET_PARAMLINK_VALUE('UNDO_RETENTION');",
     lambda conn, ctx: 3600,
     '秒',
     'Undo 保留时间，建议 3600 秒'),
    ('BUFFER',
     "SELECT SF_GET_PARAMLINK_VALUE('BUFFER');",
     lambda conn, ctx: max(128 * 1024 * 1024, _get_dm_db_size_gb(conn) * 1024 * 1024 * 0.3),
     '字节',
     '缓冲池大小，建议设为数据库大小的 30%'),
]

def _parse_dm_value(value_str, unit):
    """解析 DM8 参数值"""
    if not value_str:
        return 0
    value_str = str(value_str).strip().upper()
    try:
        if 'G' in value_str:
            return int(float(value_str.replace('G', '')) * 1024 * 1024 * 1024)
        elif 'M' in value_str:
            return int(float(value_str.replace('M', '')) * 1024 * 1024)
        elif 'K' in value_str:
            return int(float(value_str.replace('K', '')) * 1024)
        else:
            return int(value_str)
    except Exception:
        return 0

def check_dm_config_baseline(conn):
    """
    检查 DM8 配置基线，返回配置差距报告。
    """
    result = {
        'db_size_gb': _get_dm_db_size_gb(conn),
        'qps': 0,
        'total_memory_gb': _get_dm_total_memory_gb(),
        'items': [],
        'summary': {'critical_count': 0, 'warning_count': 0, 'info_count': 0}
    }
    cursor = conn.cursor()
    ctx = {'db_size_gb': result['db_size_gb'], 'qps': 0}

    for rule in DM_BASELINE_RULES:
        param_name = rule[0]
        query_sql = rule[1]
        calc_func = rule[2]
        unit = rule[3]
        description = rule[4]

        try:
            cursor.execute(query_sql)
            row = cursor.fetchone()
            if row:
                current_raw = _parse_dm_value(row[0], unit)
            else:
                current_raw = 0

            recommended_raw = calc_func(conn, ctx)

            if recommended_raw > 0 and current_raw > 0:
                gap_pct = abs(current_raw - recommended_raw) / recommended_raw * 100
            elif recommended_raw == 0 and current_raw > 0:
                gap_pct = 100
            else:
                gap_pct = 0

            if gap_pct == 0:
                severity = 'info'
            elif gap_pct > 50:
                severity = 'critical'
            elif gap_pct > 20:
                severity = 'warning'
            else:
                severity = 'info'

            if severity == 'critical':
                result['summary']['critical_count'] += 1
            elif severity == 'warning':
                result['summary']['warning_count'] += 1
            else:
                result['summary']['info_count'] += 1

            result['items'].append({
                'param': param_name,
                'current': _format_bytes(current_raw) if unit == '字节' else str(int(current_raw)),
                'recommended': _format_bytes(recommended_raw) if unit == '字节' else str(int(recommended_raw)),
                'current_raw': current_raw,
                'recommended_raw': recommended_raw,
                'gap': _format_bytes(abs(current_raw - recommended_raw)) if unit == '字节' else str(int(abs(current_raw - recommended_raw))),
                'gap_pct': round(gap_pct, 1),
                'severity': severity,
                'description': description,
                'unit': unit,
            })
        except Exception:
            pass

    cursor.close()
    return result


# ═══════════════════════════════════════════════════════
#  5. SQL Server 配置基线
# ═══════════════════════════════════════════════════════

def _get_sqlserver_db_size_gb(conn):
    """获取 SQL Server 数据库总大小（GB）"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ISNULL(SUM(size) * 8192.0 / 1024 / 1024 / 1024, 0)
            FROM sys.master_files
            WHERE database_id > 4
        """)
        result = cursor.fetchone()[0] or 0
        cursor.close()
        return float(result)
    except Exception:
        return 0.0

def _get_sqlserver_total_memory_gb():
    """获取主机总内存（GB）"""
    return _get_total_memory_gb()

SQLSERVER_BASELINE_RULES = [
    ('max server memory (MB)',
     "SELECT CAST(value AS INT) FROM sys.configurations WHERE name = 'max server memory (MB)';",
     lambda conn, ctx: int(_get_sqlserver_total_memory_gb() * 0.85 * 1024),
     'MB',
     '最大服务器内存，建议设为物理内存的 85%（保留 15% 给 OS）'),
    ('cost threshold for parallelism',
     "SELECT CAST(value AS INT) FROM sys.configurations WHERE name = 'cost threshold for parallelism';",
     lambda conn, ctx: 25,
     '阈值',
     '并行开销阈值，建议 25-50'),
    ('max degree of parallelism',
     "SELECT CAST(value AS INT) FROM sys.configurations WHERE name = 'max degree of parallelism';",
     lambda conn, ctx: max(4, psutil.cpu_count() // 2),
     '个',
     '最大并行度，建议 CPU 核心数的一半'),
    ('fill factor (%)',
     "SELECT CAST(value AS INT) FROM sys.configurations WHERE name = 'fill factor (%)';",
     lambda conn, ctx: 90,
     '百分比',
     '填充因子，建议 80-90%'),
    ('recovery interval (min)',
     "SELECT CAST(value AS INT) FROM sys.configurations WHERE name = 'recovery interval (min)';",
     lambda conn, ctx: 60,
     '分钟',
     '恢复间隔，建议 60 分钟'),
    ('backup compression default',
     "SELECT CAST(value AS INT) FROM sys.configurations WHERE name = 'backup compression default';",
     lambda conn, ctx: 1,
     '开关',
     '备份压缩默认，建议 1（开启）'),
]

def _parse_sqlserver_value(value_str, unit):
    """解析 SQL Server 配置值"""
    if not value_str:
        return 0
    try:
        val = int(float(value_str))
    except Exception:
        val = 0
    if unit == '开关':
        return val  # 0=off, 1=on
    return val

def _format_sqlserver_value(val, unit):
    """格式化 SQL Server 配置值"""
    if unit == '开关':
        return 'ON' if val == 1 else 'OFF'
    if unit == 'MB':
        return f"{val}MB"
    if unit == '百分比':
        return f"{val}%"
    return str(val)

def check_sqlserver_config_baseline(conn):
    """
    检查 SQL Server 配置基线，返回配置差距报告。
    """
    result = {
        'db_size_gb': _get_sqlserver_db_size_gb(conn),
        'qps': 0,
        'total_memory_gb': _get_sqlserver_total_memory_gb(),
        'items': [],
        'summary': {'critical_count': 0, 'warning_count': 0, 'info_count': 0}
    }
    cursor = conn.cursor()
    ctx = {'db_size_gb': result['db_size_gb']}

    for rule in SQLSERVER_BASELINE_RULES:
        param_name = rule[0]
        query_sql = rule[1]
        calc_func = rule[2]
        unit = rule[3]
        description = rule[4]

        try:
            cursor.execute(query_sql)
            row = cursor.fetchone()
            if row:
                current_raw = _parse_sqlserver_value(row[0], unit)
            else:
                current_raw = 0

            recommended_raw = calc_func(conn, ctx)

            if unit == '开关':
                gap_pct = 0 if current_raw == recommended_raw else 100
                severity = 'info' if gap_pct == 0 else 'critical'
            elif recommended_raw > 0 and current_raw > 0:
                gap_pct = abs(current_raw - recommended_raw) / recommended_raw * 100
            elif recommended_raw == 0 and current_raw > 0:
                gap_pct = 100
            else:
                gap_pct = 0

            if unit == '开关':
                severity = 'info' if current_raw == recommended_raw else 'critical'
            elif gap_pct == 0:
                severity = 'info'
            elif gap_pct > 50:
                severity = 'critical'
            elif gap_pct > 20:
                severity = 'warning'
            else:
                severity = 'info'

            if severity == 'critical':
                result['summary']['critical_count'] += 1
            elif severity == 'warning':
                result['summary']['warning_count'] += 1
            else:
                result['summary']['info_count'] += 1

            result['items'].append({
                'param': param_name,
                'current': _format_sqlserver_value(current_raw, unit),
                'recommended': _format_sqlserver_value(recommended_raw, unit),
                'current_raw': current_raw,
                'recommended_raw': recommended_raw,
                'gap': _format_sqlserver_value(abs(current_raw - recommended_raw), unit),
                'gap_pct': round(gap_pct, 1),
                'severity': severity,
                'description': description,
                'unit': unit,
            })
        except Exception:
            pass

    cursor.close()
    return result


# ═══════════════════════════════════════════════════════
#  6. TiDB 配置基线（兼容 MySQL 8.0）
# ═══════════════════════════════════════════════════════

def _get_tidb_db_size_gb(conn):
    """获取 TiDB 数据库总大小（GB）"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024 / 1024, 2)
            FROM information_schema.tables
        """)
        row = cursor.fetchone()
        cursor.close()
        return float(row[0] or 0) if row else 0.0
    except Exception:
        return 0.0

def _calc_tidb_innodb_buffer_pool(conn):
    total_mem = _get_total_memory_gb()
    return int(total_mem * 0.7 * 1024 * 1024 * 1024)

TIDB_BASELINE_RULES = [
    ('innodb_buffer_pool_size',
     "SHOW GLOBAL VARIABLES LIKE 'innodb_buffer_pool_size';",
     lambda conn, ctx: _calc_tidb_innodb_buffer_pool(conn),
     '字节',
     'InnoDB 缓冲池大小，建议设为总内存的 70%'),
    ('max_connections',
     "SHOW GLOBAL VARIABLES LIKE 'max_connections';",
     lambda conn, ctx: 3000,
     '个',
     '最大连接数，TiDB 建议 3000'),
    ('tmp_table_size',
     "SHOW GLOBAL VARIABLES LIKE 'tmp_table_size';",
     lambda conn, ctx: 256 * 1024 * 1024,
     '字节',
     '临时表大小，建议 256MB'),
    ('max_heap_table_size',
     "SHOW GLOBAL VARIABLES LIKE 'max_heap_table_size';",
     lambda conn, ctx: 256 * 1024 * 1024,
     '字节',
     '内存表大小，建议与 tmp_table_size 一致'),
    ('innodb_log_file_size',
     "SHOW GLOBAL VARIABLES LIKE 'innodb_log_file_size';",
     lambda conn, ctx: 256 * 1024 * 1024,
     '字节',
     'InnoDB 日志文件大小，建议 256MB-1GB'),
    ('innodb_log_buffer_size',
     "SHOW GLOBAL VARIABLES LIKE 'innodb_log_buffer_size';",
     lambda conn, ctx: 64 * 1024 * 1024,
     '字节',
     'InnoDB 日志缓冲区，建议 64MB'),
    ('max_allowed_packet',
     "SHOW GLOBAL VARIABLES LIKE 'max_allowed_packet';",
     lambda conn, ctx: 64 * 1024 * 1024,
     '字节',
     '最大包大小，建议 64MB'),
    ('tidb_hash_join_concurrency',
     "SHOW GLOBAL VARIABLES LIKE 'tidb_hash_join_concurrency';",
     lambda conn, ctx: 5,
     '个',
     'Hash Join 并发数，建议 5'),
    ('tidb_index_lookup_concurrency',
     "SHOW GLOBAL VARIABLES LIKE 'tidb_index_lookup_concurrency';",
     lambda conn, ctx: 5,
     '个',
     'Index Lookup 并发数，建议 5'),
]

def check_tidb_config_baseline(conn):
    """
    检查 TiDB 配置基线，返回配置差距报告。
    """
    result = {
        'db_size_gb': _get_tidb_db_size_gb(conn),
        'qps': 0,
        'total_memory_gb': _get_total_memory_gb(),
        'items': [],
        'summary': {'critical_count': 0, 'warning_count': 0, 'info_count': 0}
    }
    cursor = conn.cursor()
    ctx = {'db_size_gb': result['db_size_gb'], 'qps': 0}

    for rule in TIDB_BASELINE_RULES:
        param_name = rule[0]
        query_sql = rule[1]
        calc_func = rule[2]
        unit = rule[3]
        description = rule[4]

        try:
            cursor.execute(query_sql)
            row = cursor.fetchone()
            if row:
                current_raw = _parse_bytes(row[1])
            else:
                current_raw = 0

            recommended_raw = calc_func(conn, ctx)

            if recommended_raw > 0 and current_raw > 0:
                gap_pct = abs(current_raw - recommended_raw) / recommended_raw * 100
            elif recommended_raw == 0 and current_raw > 0:
                gap_pct = 100
            else:
                gap_pct = 0

            if gap_pct == 0:
                severity = 'info'
            elif gap_pct > 50:
                severity = 'critical'
            elif gap_pct > 20:
                severity = 'warning'
            else:
                severity = 'info'

            if severity == 'critical':
                result['summary']['critical_count'] += 1
            elif severity == 'warning':
                result['summary']['warning_count'] += 1
            else:
                result['summary']['info_count'] += 1

            result['items'].append({
                'param': param_name,
                'current': _format_bytes(current_raw) if unit == '字节' else str(current_raw),
                'recommended': _format_bytes(recommended_raw) if unit == '字节' else str(recommended_raw),
                'current_raw': current_raw,
                'recommended_raw': recommended_raw,
                'gap': _format_bytes(abs(current_raw - recommended_raw)) if unit == '字节' else str(abs(current_raw - recommended_raw)),
                'gap_pct': round(gap_pct, 1),
                'severity': severity,
                'description': description,
                'unit': unit,
            })
        except Exception:
            pass

    cursor.close()
    return result


# ═══════════════════════════════════════════════════════
#  7. 统一入口函数
# ═══════════════════════════════════════════════════════

def get_config_baseline(db_type, conn):
    """
    统一配置基线检查入口。

    参数:
        db_type: 数据库类型 ('mysql', 'pg', 'oracle', 'dm', 'sqlserver', 'tidb')
        conn: 数据库连接对象

    返回:
        配置基线报告字典
    """
    if db_type == 'mysql':
        return check_mysql_config_baseline(conn)
    elif db_type == 'pg':
        return check_pg_config_baseline(conn)
    elif db_type == 'oracle':
        return check_oracle_config_baseline(conn)
    elif db_type == 'dm':
        return check_dm_config_baseline(conn)
    elif db_type == 'sqlserver':
        return check_sqlserver_config_baseline(conn)
    elif db_type == 'tidb':
        return check_tidb_config_baseline(conn)
    else:
        return None


def format_config_baseline_report(report, db_type='mysql'):
    """
    格式化配置基线报告为可读文本。
    
    参数:
        report: 配置基线报告字典
        db_type: 数据库类型
    
    返回:
        格式化的报告文本
    """
    if not report:
        return "不支持的数据库类型"
    
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"  {db_type.upper()} 配置基线与合规检查报告")
    lines.append(f"{'='*60}")
    lines.append(f"\n数据库规模: {report.get('db_size_gb', 0):.2f} GB")
    lines.append(f"每秒查询数 (QPS): {report.get('qps', 0)}")
    lines.append(f"主机总内存: {report.get('total_memory_gb', 0):.2f} GB")
    lines.append(f"\n检查结果汇总:")
    lines.append(f"  严重问题 (Critical): {report['summary']['critical_count']}")
    lines.append(f"  警告问题 (Warning):  {report['summary']['warning_count']}")
    lines.append(f"  提示信息 (Info):     {report['summary']['info_count']}")
    
    if report['items']:
        lines.append(f"\n{'─'*60}")
        lines.append(f"{'配置项':<35} {'当前值':<12} {'推荐值':<12} {'差距':<10} {'状态'}")
        lines.append(f"{'─'*60}")
        
        for item in report['items']:
            severity_icon = {
                'critical': '🔴 严重',
                'warning': '🟡 警告',
                'info': '🟢 正常'
            }.get(item['severity'], '')
            
            lines.append(
                f"{item['param']:<35} "
                f"{item['current']:<12} "
                f"{item['recommended']:<12} "
                f"{item['gap_pct']:>6.1f}%  "
                f"{severity_icon}"
            )
    
    lines.append(f"\n{'='*60}")
    lines.append("说明:")
    lines.append("  🔴 严重: 配置差距 > 50%，建议立即调整")
    lines.append("  🟡 警告: 配置差距 > 20%，建议尽快调整")
    lines.append("  🟢 正常: 配置合理或差距在可接受范围内")
    lines.append("="*60)
    
    return '\n'.join(lines)
