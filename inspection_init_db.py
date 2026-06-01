#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
数据库巡检配置初始化脚本。

这个脚本用于初始化巡检配置数据库，包括：
1. 创建所有必要的表
2. 为每种数据库类型创建默认模板和章节
3. 为每种数据库类型预置常用巡检 SQL 查询

使用方法：
    python inspection_init_db.py [--db-path PATH] [--force]

参数：
    --db-path PATH: 数据库文件路径（默认：DBCheck 目录下的 inspection.db）
    --force: 强制重新初始化（会删除现有数据！）
"""

import os
import sys
import argparse
from inspection_dal import (
    init_database,
    create_template,
    create_chapter,
    create_query,
    get_default_template,
    get_template,
    get_all_templates,
    DEFAULT_DB_PATH,
)
# 临时文件：MySQL 21 章配置
# 由 gen_inspection_init_db.py 自动拼接，不要手动编辑

MYSQL_DEFAULT_CHAPTERS = [
    {
        'chapter_number': 1,
        'chapter_title_zh': '健康状态概览',
        'chapter_title_en': 'Health Overview',
        'description': '数据库整体健康状态概览',
        'queries': [
            {'key': 'my_version',   'sql': "SELECT VERSION() AS version;",
             'desc_zh': '获取 MySQL 版本',          'desc_en': 'Get MySQL version'},
            {'key': 'uptime',      'sql': "SHOW GLOBAL STATUS LIKE 'Uptime';",
             'desc_zh': '数据库运行时长(秒)', 'desc_en': 'Database uptime (seconds)'},
            {'key': 'datadir',     'sql': "SHOW VARIABLES LIKE 'datadir';",
             'desc_zh': '数据目录路径',            'desc_en': 'Data directory path'},
            {'key': 'server_uuid', 'sql': "SHOW VARIABLES LIKE 'server_uuid';",
             'desc_zh': '服务器 UUID',             'desc_en': 'Server UUID'},
        ]
    },
    {
        'chapter_number': 2,
        'chapter_title_zh': '连接状态检查',
        'chapter_title_en': 'Connection Status',
        'description': '数据库连接相关状态检查',
        'queries': [
            {'key': 'threads_connected',     'sql': "SHOW GLOBAL STATUS LIKE 'Threads_connected';",
             'desc_zh': '当前连接数',                'desc_en': 'Current connections'},
            {'key': 'max_used_connections', 'sql': "SHOW GLOBAL STATUS LIKE 'Max_used_connections';",
             'desc_zh': '历史最大连接数',           'desc_en': 'Max used connections'},
            {'key': 'max_connections',      'sql': "SHOW VARIABLES LIKE 'max_connections';",
             'desc_zh': '最大连接数配置',           'desc_en': 'Max connections config'},
            {'key': 'aborted_connects',    'sql': "SHOW GLOBAL STATUS LIKE 'Aborted_connects';",
             'desc_zh': '失败连接次数',              'desc_en': 'Aborted connection count'},
            {'key': 'connection_errors',    'sql': "SHOW GLOBAL STATUS LIKE 'Connection_errors%';",
             'desc_zh': '连接错误统计',              'desc_en': 'Connection error stats'},
            {'key': 'threads_running',      'sql': "SHOW GLOBAL STATUS LIKE 'Threads_running';",
             'desc_zh': '当前活跃连接数',           'desc_en': 'Running threads count'},
        ]
    },
    {
        'chapter_number': 3,
        'chapter_title_zh': '配置参数检查',
        'chapter_title_en': 'Configuration Check',
        'description': '关键配置参数检查',
        'queries': [
            {'key': 'innodb_buffer_pool_size',   'sql': "SHOW VARIABLES LIKE 'innodb_buffer_pool_size';",
             'desc_zh': 'InnoDB 缓冲池大小',     'desc_en': 'InnoDB buffer pool size'},
            {'key': 'innodb_log_file_size',      'sql': "SHOW VARIABLES LIKE 'innodb_log_file_size';",
             'desc_zh': 'Redo 日志文件大小',      'desc_en': 'Redo log file size'},
            {'key': 'innodb_flush_log_at_trx_commit', 'sql': "SHOW VARIABLES LIKE 'innodb_flush_log_at_trx_commit';",
             'desc_zh': '事务提交刷盘策略',         'desc_en': 'Transaction flush policy'},
            {'key': 'sync_binlog',        'sql': "SHOW VARIABLES LIKE 'sync_binlog';",
             'desc_zh': 'Binlog 刷盘策略',        'desc_en': 'Binlog sync policy'},
            {'key': 'log_bin',            'sql': "SHOW VARIABLES LIKE 'log_bin';",
             'desc_zh': 'Binlog 是否开启',        'desc_en': 'Binary logging enabled'},
            {'key': 'slow_query_log',    'sql': "SHOW VARIABLES LIKE 'slow_query_log';",
             'desc_zh': '慢查询日志是否开启',        'desc_en': 'Slow query log enabled'},
            {'key': 'long_query_time',    'sql': "SHOW VARIABLES LIKE 'long_query_time';",
             'desc_zh': '慢查询阈值（秒）',        'desc_en': 'Slow query threshold (s)'},
            {'key': 'table_open_cache',   'sql': "SHOW VARIABLES LIKE 'table_open_cache';",
             'desc_zh': '表缓存大小',               'desc_en': 'Table open cache size'},
            {'key': 'key_buffer_size',    'sql': "SHOW VARIABLES LIKE 'key_buffer_size';",
             'desc_zh': 'MyISAM 键缓存大小',     'desc_en': 'MyISAM key buffer size'},
        ]
    },
    {
        'chapter_number': 4,
        'chapter_title_zh': '性能分析',
        'chapter_title_en': 'Performance Analysis',
        'description': '数据库性能指标分析',
        'queries': [
            {'key': 'qps',           'sql': "SHOW GLOBAL STATUS LIKE 'Queries';",
             'desc_zh': 'QPS 累计查询数',        'desc_en': 'Cumulative queries'},
            {'key': 'com_commit',    'sql': "SHOW GLOBAL STATUS LIKE 'Com_commit';",
             'desc_zh': '事务提交次数',             'desc_en': 'Transaction commit count'},
            {'key': 'com_rollback',  'sql': "SHOW GLOBAL STATUS LIKE 'Com_rollback';",
             'desc_zh': '事务回滚次数',             'desc_en': 'Transaction rollback count'},
            {'key': 'innodb_row_ops', 'sql': "SHOW GLOBAL STATUS LIKE 'Innodb_rows_%';",
             'desc_zh': 'InnoDB 行操作统计',      'desc_en': 'InnoDB row operation stats'},
            {'key': 'innodb_data_ops','sql': "SHOW GLOBAL STATUS LIKE 'Innodb_data_%';",
             'desc_zh': 'InnoDB 数据读写统计',     'desc_en': 'InnoDB data R/W stats'},
            {'key': 'cache_hit_ratio', 'sql': "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads';",
             'desc_zh': '缓冲池物理读次数',       'desc_en': 'Buffer pool physical reads'},
            {'key': 'cache_hit_requests', 'sql': "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read_requests';",
             'desc_zh': '缓冲池读请求次数',       'desc_en': 'Buffer pool read requests'},
        ]
    },
    {
        'chapter_number': 5,
        'chapter_title_zh': '数据库空间使用',
        'chapter_title_en': 'Database Space Usage',
        'description': '数据库和表的空间使用情况',
        'queries': [
            {'key': 'db_size', 'sql': """
                SELECT table_schema AS database_name,
                       ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS total_mb,
                       ROUND(SUM(data_length) / 1024 / 1024, 2) AS data_mb,
                       ROUND(SUM(index_length) / 1024 / 1024, 2) AS index_mb,
                       COUNT(*) AS table_count
                FROM information_schema.TABLES
                WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                GROUP BY table_schema
                ORDER BY total_mb DESC;
            """,
             'desc_zh': '各数据库大小',           'desc_en': 'Database sizes'},
            {'key': 'table_size', 'sql': """
                SELECT table_schema AS database_name, table_name,
                       ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb,
                       table_rows
                FROM information_schema.TABLES
                WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                ORDER BY (data_length + index_length) DESC
                LIMIT 20;
            """,
             'desc_zh': '最大的 20 张表',      'desc_en': 'Top 20 largest tables'},
        ]
    },
    {
        'chapter_number': 6,
        'chapter_title_zh': '安全信息',
        'chapter_title_en': 'Security Information',
        'description': '数据库安全相关配置',
        'queries': [
            {'key': 'mysql_users', 'sql': """
                SELECT user AS col1, host AS col2, Grant_priv AS col3,
                       plugin AS col4, account_locked AS col5, password_expired AS col6
                FROM mysql.user
                WHERE user NOT IN ('mysql.infoschema','mysql.session','mysql.sys','root')
                ORDER BY user;
            """,
             'desc_zh': '非系统用户列表',           'desc_en': 'Non-system users'},
            {'key': 'password_expiry', 'sql': """
                SELECT user, host, password_expired, password_lifetime
                FROM mysql.user
                WHERE password_expired='Y' OR password_lifetime IS NOT NULL;
            """,
             'desc_zh': '密码过期用户',            'desc_en': 'Password expiry status'},
            {'key': 'user_privileges', 'sql': """
                SELECT grantee, privilege_type, is_grantable
                FROM information_schema.USER_PRIVILEGES
                WHERE grantee NOT LIKE '%root%' AND grantee NOT LIKE '%mysql%'
                ORDER BY grantee;
            """,
             'desc_zh': '用户权限一览',            'desc_en': 'User privileges overview'},
        ]
    },
    {
        'chapter_number': 7,
        'chapter_title_zh': '复制状态检查',
        'chapter_title_en': 'Replication Status',
        'description': '主从复制状态检查',
        'queries': [
            {'key': 'slave_status',    'sql': "SHOW SLAVE STATUS\\G",
             'desc_zh': '从库复制状态',           'desc_en': 'Slave replication status'},
            {'key': 'master_status',    'sql': "SHOW MASTER STATUS;",
             'desc_zh': '主库 Binlog 位置',      'desc_en': 'Master binlog position'},
            {'key': 'slave_io_running', 'sql': "SHOW SLAVE STATUS\\G",
             'desc_zh': '复制 IO 线程状态',       'desc_en': 'Replication IO thread status'},
            {'key': 'replication_lag',  'sql': "SHOW SLAVE STATUS\\G",
             'desc_zh': '复制延迟（Seconds_Behind_Master）', 'desc_en': 'Replication lag'},
        ]
    },
    {
        'chapter_number': 8,
        'chapter_title_zh': 'InnoDB 锁等待检查',
        'chapter_title_en': 'InnoDB Lock Analysis',
        'description': 'InnoDB 锁等待和长事务检查',
        'queries': [
            {'key': 'innodb_lock_chain', 'sql': """
                SELECT r.trx_id AS waiting_trx_id, r.trx_mysql_thread_id AS waiting_thread,
                       LEFT(COALESCE(r.trx_query, ''), 200) AS waiting_query,
                       r.trx_state AS waiting_state,
                       b.trx_id AS blocking_trx_id, b.trx_mysql_thread_id AS blocking_thread,
                       LEFT(COALESCE(b.trx_query, ''), 200) AS blocking_query
                FROM information_schema.INNODB_TRX r
                JOIN performance_schema.data_lock_waits w ON r.trx_id = w.REQUESTING_ENGINE_TRANSACTION_ID
                JOIN information_schema.INNODB_TRX b ON w.BLOCKING_ENGINE_TRANSACTION_ID = b.trx_id
                ORDER BY r.trx_started;
            """,
             'desc_zh': 'InnoDB 锁等待链',      'desc_en': 'InnoDB lock wait chain'},
            {'key': 'innodb_long_trx', 'sql': """
                SELECT trx_id, trx_mysql_thread_id, trx_state,
                       LEFT(COALESCE(trx_query, ''), 200) AS trx_query,
                       TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS trx_duration_sec
                FROM information_schema.INNODB_TRX
                WHERE TIMESTAMPDIFF(SECOND, trx_started, NOW()) > 60
                ORDER BY trx_started;
            """,
             'desc_zh': '运行超过 60 秒的长事务', 'desc_en': 'Long transactions > 60s'},
            {'key': 'innodb_deadlock', 'sql': "SHOW ENGINE INNODB STATUS\\G",
             'desc_zh': 'InnoDB 引擎状态（含死锁信息）', 'desc_en': 'InnoDB engine status'},
        ]
    },
    {
        'chapter_number': 9,
        'chapter_title_zh': '缓冲池状态',
        'chapter_title_en': 'Buffer Pool Status',
        'description': 'InnoDB 缓冲池使用状态',
        'queries': [
            {'key': 'buffer_pool_status', 'sql': "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool%';",
             'desc_zh': '缓冲池相关状态',           'desc_en': 'Buffer pool related status'},
            {'key': 'buffer_pool_size',    'sql': "SHOW VARIABLES LIKE 'innodb_buffer_pool_size';",
             'desc_zh': '缓冲池大小配置',           'desc_en': 'Buffer pool size config'},
            {'key': 'buffer_pool_instances', 'sql': "SHOW VARIABLES LIKE 'innodb_buffer_pool_instances';",
             'desc_zh': '缓冲池实例数',            'desc_en': 'Buffer pool instances'},
        ]
    },
    {
        'chapter_number': 10,
        'chapter_title_zh': '事务和锁分析',
        'chapter_title_en': 'Transaction & Lock Analysis',
        'description': '事务状态和锁等待分析',
        'queries': [
            {'key': 'trx_list',   'sql': "SELECT * FROM information_schema.INNODB_TRX ORDER BY trx_started;",
             'desc_zh': '当前 InnoDB 事务列表',   'desc_en': 'Current InnoDB transactions'},
            {'key': 'lock_waits', 'sql': "SELECT * FROM performance_schema.data_lock_waits LIMIT 20;",
             'desc_zh': '锁等待列表',              'desc_en': 'Lock wait list'},
            {'key': 'lock_summary', 'sql': "SELECT lock_mode, lock_type, COUNT(*) AS cnt FROM performance_schema.data_locks GROUP BY lock_mode, lock_type;",
             'desc_zh': '锁类型统计',               'desc_en': 'Lock type statistics'},
        ]
    },
    {
        'chapter_number': 11,
        'chapter_title_zh': '慢查询分析',
        'chapter_title_en': 'Slow Query Analysis',
        'description': '慢查询日志分析',
        'queries': [
            {'key': 'slow_query_status',  'sql': "SHOW VARIABLES LIKE 'slow_query%';",
             'desc_zh': '慢查询配置',              'desc_en': 'Slow query configuration'},
            {'key': 'slow_query_count',  'sql': "SHOW GLOBAL STATUS LIKE 'Slow_queries';",
             'desc_zh': '慢查询次数',              'desc_en': 'Slow query count'},
            {'key': 'long_query_time_cfg', 'sql': "SHOW VARIABLES LIKE 'long_query_time';",
             'desc_zh': '慢查询阈值配置',           'desc_en': 'Slow query threshold config'},
        ]
    },
    {
        'chapter_number': 12,
        'chapter_title_zh': '表碎片和统计信息',
        'chapter_title_en': 'Table Fragmentation & Statistics',
        'description': '表碎片率和统计信息新鲜度',
        'queries': [
            {'key': 'table_fragmentation', 'sql': """
                SELECT table_schema, table_name,
                       ROUND(data_length / 1024 / 1024, 2) AS data_mb,
                       ROUND(data_free / 1024 / 1024, 2) AS free_mb,
                       ROUND(data_free * 100.0 / NULLIF(data_length, 0), 2) AS frag_percent
                FROM information_schema.TABLES
                WHERE data_free > 0
                  AND table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                ORDER BY data_free DESC
                LIMIT 20;
            """,
             'desc_zh': '表碎片率 TOP 20',       'desc_en': 'Top 20 fragmented tables'},
            {'key': 'stale_tables', 'sql': """
                SELECT table_schema, table_name, update_time
                FROM information_schema.TABLES
                WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                  AND update_time < NOW() - INTERVAL 7 DAY
                ORDER BY update_time DESC
                LIMIT 20;
            """,
             'desc_zh': '超过7天未更新的表',     'desc_en': 'Tables not updated in 7 days'},
        ]
    },
    {
        'chapter_number': 13,
        'chapter_title_zh': '索引使用情况',
        'chapter_title_en': 'Index Usage Analysis',
        'description': '索引使用率和冗余索引分析',
        'queries': [
            {'key': 'unused_indexes', 'sql': """
                SELECT object_schema, object_name, index_name, count_star, sum_timer_wait
                FROM performance_schema.table_io_waits_summary_by_index_usage
                WHERE count_star = 0
                  AND index_name IS NOT NULL
                  AND object_schema NOT IN ('mysql','information_schema','performance_schema','sys')
                ORDER BY object_schema, object_name;
            """,
             'desc_zh': '从未使用的索引',           'desc_en': 'Unused indexes'},
            {'key': 'index_stats', 'sql': """
                SELECT object_schema, object_name, index_name, count_star AS rows_accessed
                FROM performance_schema.table_io_waits_summary_by_index_usage
                WHERE object_schema NOT IN ('mysql','information_schema','performance_schema','sys')
                ORDER BY count_star DESC
                LIMIT 20;
            """,
             'desc_zh': '索引访问次数 TOP 20',    'desc_en': 'Top 20 index access count'},
        ]
    },
    {
        'chapter_number': 14,
        'chapter_title_zh': '主从复制延迟',
        'chapter_title_en': 'Replication Lag',
        'description': '主从复制延迟详细分析',
        'queries': [
            {'key': 'repl_lag_detail', 'sql': "SHOW SLAVE STATUS\\G",
             'desc_zh': '复制延迟详细信息',            'desc_en': 'Replication lag details'},
            {'key': 'repl_channels',  'sql': "SHOW REPLICA STATUS\\G",
             'desc_zh': '复制通道状态（MySQL 8.0+）', 'desc_en': 'Replica status (MySQL 8.0+)'},
        ]
    },
    {
        'chapter_number': 15,
        'chapter_title_zh': 'Binlog 状态',
        'chapter_title_en': 'Binary Log Status',
        'description': 'Binary Log 状态和配置',
        'queries': [
            {'key': 'binlog_status', 'sql': "SHOW BINARY LOGS;",
             'desc_zh': 'Binlog 文件列表',       'desc_en': 'Binary log file list'},
            {'key': 'binlog_config', 'sql': "SHOW VARIABLES LIKE 'binlog%';",
             'desc_zh': 'Binlog 相关配置',       'desc_en': 'Binary log configuration'},
            {'key': 'binlog_cache',  'sql': "SHOW GLOBAL STATUS LIKE 'Binlog_cache%';",
             'desc_zh': 'Binlog 缓存统计',       'desc_en': 'Binary log cache stats'},
        ]
    },
    {
        'chapter_number': 16,
        'chapter_title_zh': '用户权限审计',
        'chapter_title_en': 'User Privilege Audit',
        'description': '用户权限和安全审计',
        'queries': [
            {'key': 'user_list', 'sql': """
                SELECT user, host, authentication_string IS NOT NULL AS has_password,
                       password_expired, password_lifetime, account_locked, plugin
                FROM mysql.user
                WHERE user != ''
                ORDER BY user, host;
            """,
             'desc_zh': '用户账号安全状态',          'desc_en': 'User account security status'},
            {'key': 'db_privileges', 'sql': "SELECT * FROM mysql.db ORDER BY user, db;",
             'desc_zh': '数据库级别权限',           'desc_en': 'Database-level privileges'},
            {'key': 'role_edges',    'sql': "SELECT * FROM mysql.role_edges ORDER BY from_user;",
             'desc_zh': '角色关系',                'desc_en': 'Role edges'},
        ]
    },
    {
        'chapter_number': 17,
        'chapter_title_zh': '存储引擎状态',
        'chapter_title_en': 'Storage Engine Status',
        'description': '存储引擎状态和统计',
        'queries': [
            {'key': 'engine_status', 'sql': "SHOW ENGINES;",
             'desc_zh': '支持的存储引擎',           'desc_en': 'Supported storage engines'},
            {'key': 'innodb_status', 'sql': "SHOW ENGINE INNODB STATUS\\G",
             'desc_zh': 'InnoDB 引擎详细状态',    'desc_en': 'InnoDB engine detailed status'},
        ]
    },
    {
        'chapter_number': 18,
        'chapter_title_zh': '系统变量检查',
        'chapter_title_en': 'System Variables Check',
        'description': '关键系统变量检查',
        'queries': [
            {'key': 'key_vars', 'sql': """
                SELECT variable_name, variable_value
                FROM performance_schema.global_variables
                WHERE variable_name IN (
                    'innodb_buffer_pool_size','innodb_log_file_size','max_connections',
                    'query_cache_size','tmp_table_size','max_heap_table_size',
                    'thread_cache_size','table_open_cache','open_files_limit',
                    'innodb_flush_log_at_trx_commit','sync_binlog','log_bin',
                    'slow_query_log','long_query_time'
                )
                ORDER BY variable_name;
            """,
             'desc_zh': '关键系统变量一览',           'desc_en': 'Key system variables overview'},
        ]
    },
    {
        'chapter_number': 19,
        'chapter_title_zh': '错误日志检查',
        'chapter_title_en': 'Error Log Check',
        'description': '错误日志和告警信息',
        'queries': [
            {'key': 'error_log_path', 'sql': "SHOW VARIABLES LIKE 'log_error';",
             'desc_zh': '错误日志路径',            'desc_en': 'Error log path'},
            {'key': 'log_warnings',   'sql': "SHOW VARIABLES LIKE 'log_warnings';",
             'desc_zh': '警告日志级别',            'desc_en': 'Warning log level'},
        ]
    },
    {
        'chapter_number': 20,
        'chapter_title_zh': '计划任务和事件',
        'chapter_title_en': 'Scheduled Events',
        'description': 'MySQL 事件调度器状态',
        'queries': [
            {'key': 'event_scheduler', 'sql': "SHOW VARIABLES LIKE 'event_scheduler';",
             'desc_zh': '事件调度器是否开启',        'desc_en': 'Event scheduler status'},
            {'key': 'events_list',    'sql': "SHOW EVENTS FROM information_schema;",
             'desc_zh': '事件列表',                'desc_en': 'Event list'},
        ]
    },
    {
        'chapter_number': 21,
        'chapter_title_zh': 'InnoDB 表空间状态',
        'chapter_title_en': 'InnoDB Tablespace Status',
        'description': 'InnoDB 表空间和文件状态',
        'queries': [
            {'key': 'innodb_tablespaces', 'sql': "SELECT * FROM information_schema.INNODB_TABLESPACES;",
             'desc_zh': 'InnoDB 表空间列表',      'desc_en': 'InnoDB tablespace list'},
            {'key': 'innodb_datafiles',   'sql': "SELECT * FROM information_schema.INNODB_DATAFILES;",
             'desc_zh': 'InnoDB 数据文件列表',     'desc_en': 'InnoDB datafile list'},
            {'key': 'file_per_table',     'sql': "SHOW VARIABLES LIKE 'innodb_file_per_table';",
             'desc_zh': '独立表空间是否开启',        'desc_en': 'File-per-table enabled'},
        ]
    },
]
# 临时文件：PostgreSQL 21 章配置

POSTGRESQL_DEFAULT_CHAPTERS = [
    {
        'chapter_number': 1,
        'chapter_title_zh': '健康状态概览',
        'chapter_title_en': 'Health Overview',
        'description': '数据库整体健康状态概览',
        'queries': [
            {'key': 'pg_version',    'sql': "SELECT version();",
             'desc_zh': '获取 PostgreSQL 版本',       'desc_en': 'Get PostgreSQL version'},
            {'key': 'pg_uptime',    'sql': "SELECT now() - pg_postmaster_start_time() AS uptime, pg_postmaster_start_time() AS start_time;",
             'desc_zh': '数据库运行时长',             'desc_en': 'Database uptime'},
            {'key': 'pg_connections','sql': """
                SELECT count(*) AS total_connections,
                       (SELECT setting FROM pg_settings WHERE name='max_connections')::int AS max_connections,
                       round(count(*) * 100.0 / (SELECT setting FROM pg_settings WHERE name='max_connections')::int, 2) AS usage_percent
                FROM pg_stat_activity;
            """,
             'desc_zh': '连接数使用率',              'desc_en': 'Connection usage percentage'},
        ]
    },
    {
        'chapter_number': 2,
        'chapter_title_zh': '连接状态详情',
        'chapter_title_en': 'Connection Details',
        'description': '连接状态详细分析',
        'queries': [
            {'key': 'pg_conn_detail',  'sql': "SELECT state, count(*) AS count FROM pg_stat_activity WHERE state IS NOT NULL GROUP BY state ORDER BY count DESC;",
             'desc_zh': '连接状态分布',                'desc_en': 'Connection state breakdown'},
            {'key': 'pg_wait_events', 'sql': "SELECT wait_event_type, wait_event, count(*) AS count FROM pg_stat_activity WHERE wait_event IS NOT NULL GROUP BY wait_event_type, wait_event ORDER BY count DESC LIMIT 10;",
             'desc_zh': '等待事件 TOP 10',           'desc_en': 'Top 10 wait events'},
            {'key': 'pg_long_queries', 'sql': "SELECT pid, now()-query_start AS duration, state, left(query,120) AS query FROM pg_stat_activity WHERE state NOT IN ('idle') AND query_start IS NOT NULL AND now()-query_start > interval '30 seconds' ORDER BY duration DESC LIMIT 10;",
             'desc_zh': '运行超过 30 秒的查询',    'desc_en': 'Queries running > 30s'},
        ]
    },
    {
        'chapter_number': 3,
        'chapter_title_zh': 'PostgreSQL 配置检查',
        'chapter_title_en': 'PostgreSQL Configuration',
        'description': '关键配置参数检查',
        'queries': [
            {'key': 'shared_buffers',          'sql': "SHOW shared_buffers;",
             'desc_zh': '共享缓冲区大小',          'desc_en': 'Shared buffers size'},
            {'key': 'work_mem',               'sql': "SHOW work_mem;",
             'desc_zh': '工作内存',                   'desc_en': 'Work memory'},
            {'key': 'maintenance_work_mem',   'sql': "SHOW maintenance_work_mem;",
             'desc_zh': '维护工作内存',            'desc_en': 'Maintenance work memory'},
            {'key': 'effective_cache_size',   'sql': "SHOW effective_cache_size;",
             'desc_zh': '优化器假设缓存大小',        'desc_en': 'Effective cache size'},
            {'key': 'wal_level',              'sql': "SHOW wal_level;",
             'desc_zh': 'WAL 级别',                'desc_en': 'WAL level'},
            {'key': 'pg_settings_key', 'sql': """
                SELECT name, setting, unit, short_desc
                FROM pg_settings
                WHERE name IN ('max_connections','shared_buffers','work_mem','maintenance_work_mem',
                               'effective_cache_size','wal_level','archive_mode','max_wal_size',
                               'checkpoint_completion_target','random_page_cost')
                ORDER BY name;
            """,
             'desc_zh': '关键参数一览',               'desc_en': 'Key parameters overview'},
        ]
    },
    {
        'chapter_number': 4,
        'chapter_title_zh': '性能分析',
        'chapter_title_en': 'Performance Analysis',
        'description': '数据库性能指标和锁分析',
        'queries': [
            {'key': 'pg_lock_info',      'sql': "SELECT count(*) AS total_locks, sum(CASE WHEN granted THEN 1 ELSE 0 END) AS granted_locks, sum(CASE WHEN NOT granted THEN 1 ELSE 0 END) AS waiting_locks FROM pg_locks;",
             'desc_zh': '锁数量统计',                  'desc_en': 'Lock count statistics'},
            {'key': 'pg_blocking_chain', 'sql': """
                SELECT blocked_locks.pid AS blocked_pid, blocked_activity.usename AS blocked_user,
                       left(blocked_activity.query, 200) AS blocked_query,
                       blocking_locks.pid AS blocking_pid, blocking_activity_block.usename AS blocking_user,
                       left(blocking_activity_block.query, 200) AS blocking_query
                FROM pg_catalog.pg_locks blocked_locks
                JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
                JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
                    AND blocking_locks.pid != blocked_locks.pid AND blocking_locks.granted
                JOIN pg_catalog.pg_stat_activity blocking_activity_block ON blocking_activity_block.pid = blocking_locks.pid
                WHERE NOT blocked_locks.granted
                ORDER BY blocked_activity.query_start;
            """,
             'desc_zh': '锁等待链',                     'desc_en': 'Lock wait chain'},
            {'key': 'pg_long_xact',   'sql': """
                SELECT pid, usename, datname, application_name, state, xact_start,
                       EXTRACT(EPOCH FROM (now() - xact_start)) AS xact_seconds,
                       left(query, 200) AS query
                FROM pg_stat_activity
                WHERE xact_start IS NOT NULL AND state != 'idle'
                ORDER BY xact_start;
            """,
             'desc_zh': '长事务列表',                    'desc_en': 'Long transactions'},
            {'key': 'pg_deadlock_count', 'sql': "SELECT datname, deadlocks FROM pg_stat_database WHERE deadlocks > 0 ORDER BY datid DESC;",
             'desc_zh': '死锁次数统计',                 'desc_en': 'Deadlock count by database'},
        ]
    },
    {
        'chapter_number': 5,
        'chapter_title_zh': '数据库空间使用',
        'chapter_title_en': 'Database Space Usage',
        'description': '数据库和表空间使用情况',
        'queries': [
            {'key': 'pg_db_size',    'sql': "SELECT datname AS database_name, pg_size_pretty(pg_database_size(datname)) AS size FROM pg_database WHERE datistemplate=false ORDER BY pg_database_size(datname) DESC;",
             'desc_zh': '各数据库大小',              'desc_en': 'Database sizes'},
            {'key': 'pg_tablespace', 'sql': "SELECT spcname AS tablespace_name, pg_size_pretty(pg_tablespace_size(oid)) AS size FROM pg_tablespace ORDER BY pg_tablespace_size(oid) DESC;",
             'desc_zh': '表空间大小',              'desc_en': 'Tablespace sizes'},
        ]
    },
    {
        'chapter_number': 6,
        'chapter_title_zh': '表与索引分析',
        'chapter_title_en': 'Table & Index Analysis',
        'description': '表膨胀、索引使用率分析',
        'queries': [
            {'key': 'pg_table_stats', 'sql': """
                SELECT schemaname, relname AS tablename,
                       n_live_tup AS live_rows, n_dead_tup AS dead_rows,
                       round(n_dead_tup * 100.0 / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_ratio,
                       last_vacuum, last_autovacuum, last_analyze, last_autoanalyze
                FROM pg_stat_user_tables
                ORDER BY n_dead_tup DESC LIMIT 15;
            """,
             'desc_zh': '表死行统计（需 vacuum）',  'desc_en': 'Table dead tuples (vacuum needed)'},
            {'key': 'pg_index_usage', 'sql': """
                SELECT schemaname, relname AS tablename, indexrelname AS indexname,
                       idx_scan, idx_tup_read, idx_tup_fetch
                FROM pg_stat_user_indexes
                ORDER BY idx_scan ASC LIMIT 15;
            """,
             'desc_zh': '索引扫描次数（低者可考虑删除）', 'desc_en': 'Index scan count (low = candidate for removal)'},
            {'key': 'pg_invalid_indexes', 'sql': """
                SELECT n.nspname AS schemaname, c.relname AS indexname, i.indrelid::regclass AS tablename
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indexrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE NOT i.indisvalid
                ORDER BY n.nspname, c.relname;
            """,
             'desc_zh': '无效索引列表',               'desc_en': 'Invalid indexes'},
        ]
    },
    {
        'chapter_number': 7,
        'chapter_title_zh': '复制状态检查',
        'chapter_title_en': 'Replication Status',
        'description': '流复制状态检查',
        'queries': [
            {'key': 'pg_replication',   'sql': "SELECT pid, usename, application_name, client_addr, state, sent_lsn, write_lsn, flush_lsn, replay_lsn, sync_state FROM pg_stat_replication;",
             'desc_zh': '流复制状态',                    'desc_en': 'Streaming replication status'},
            {'key': 'pg_wal_status',   'sql': "SELECT pg_is_in_recovery() AS is_in_recovery, pg_current_wal_lsn() AS current_wal_lsn;",
             'desc_zh': 'WAL 位置信息',              'desc_en': 'WAL position info'},
        ]
    },
    {
        'chapter_number': 8,
        'chapter_title_zh': 'WAL 与检查点',
        'chapter_title_en': 'WAL & Checkpoint Analysis',
        'description': 'WAL 生成量和检查点频率',
        'queries': [
            {'key': 'pg_wal_rate', 'sql': """
                SELECT pg_walfile_name(pg_current_wal_lsn()) AS current_wal_file,
                       pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0') AS wal_bytes_total;
            """,
             'desc_zh': '当前 WAL 位置',              'desc_en': 'Current WAL position'},
            {'key': 'pg_checkpoint', 'sql': "SELECT * FROM pg_stat_bgwriter;",
             'desc_zh': '检查点写入统计',               'desc_en': 'Checkpoint write stats'},
        ]
    },
    {
        'chapter_number': 9,
        'chapter_title_zh': '表空间与磁盘',
        'chapter_title_en': 'Tablespace & Disk',
        'description': '表空间使用和磁盘 I/O',
        'queries': [
            {'key': 'pg_tablespace_size', 'sql': "SELECT spcname, pg_size_pretty(pg_tablespace_size(oid)) AS size FROM pg_tablespace;",
             'desc_zh': '表空间大小',                 'desc_en': 'Tablespace sizes'},
            {'key': 'pg_io_stats', 'sql': "SELECT datname, blks_read, blks_hit, temp_files, temp_bytes FROM pg_stat_database WHERE datname NOT IN ('template0','template1') ORDER BY blks_read + blks_hit DESC LIMIT 20;",
             'desc_zh': '数据库 I/O 统计',         'desc_en': 'Database I/O stats'},
        ]
    },
    {
        'chapter_number': 10,
        'chapter_title_zh': '数据库年龄与事务ID',
        'chapter_title_en': 'Database Age & Transaction ID',
        'description': '防止事务 ID 回卷',
        'queries': [
            {'key': 'pg_database_age', 'sql': """
                SELECT datname, age(datfrozenxid) AS xid_age,
                       round(age(datfrozenxid) * 100.0 / 2147483647, 2) AS wraparound_risk
                FROM pg_database
                ORDER BY age(datfrozenxid) DESC;
            """,
             'desc_zh': '数据库年龄（xid 回卷风险）', 'desc_en': 'Database age (xid wraparound risk)'},
            {'key': 'pg_autovacuum_settings', 'sql': "SELECT name, setting FROM pg_settings WHERE name LIKE '%autovacuum%' OR name LIKE '%vacuum%' ORDER BY name;",
             'desc_zh': 'Autovacuum 相关参数',       'desc_en': 'Autovacuum related parameters'},
        ]
    },
    {
        'chapter_number': 11,
        'chapter_title_zh': '扩展与 pg_stat_statements',
        'chapter_title_en': 'Extensions & pg_stat_statements',
        'description': '是否启用 pg_stat_statements',
        'queries': [
            {'key': 'pg_extensions', 'sql': "SELECT * FROM pg_available_extensions WHERE installed_version IS NOT NULL ORDER BY name;",
             'desc_zh': '已安装扩展',                   'desc_en': 'Installed extensions'},
            {'key': 'pg_stat_statements_status', 'sql': "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements') AS is_installed, current_setting('shared_preload_libraries') AS preload_libs;",
             'desc_zh': 'pg_stat_statements 是否启用', 'desc_en': 'pg_stat_statements enabled'},
        ]
    },
    {
        'chapter_number': 12,
        'chapter_title_zh': 'TOP SQL 分析',
        'chapter_title_en': 'Top SQL Analysis',
        'description': '最耗时/最频繁的 SQL',
        'queries': [
            {'key': 'pg_top_elapsed', 'sql': """
                SELECT LEFT(query, 200) AS query, COUNT(*) AS calls,
                       MAX(EXTRACT(EPOCH FROM now() - xact_start))::numeric AS max_duration_sec
                FROM pg_stat_activity
                WHERE state != 'idle' AND query NOT LIKE 'SELECT query%' AND datname IS NOT NULL
                GROUP BY LEFT(query, 200)
                ORDER BY max_duration_sec DESC
                LIMIT 10;
            """,
             'desc_zh': 'TOP SQL（当前活跃，需 pg_stat_statements 获取历史）', 'desc_en': 'Top SQL by duration (active, full history needs pg_stat_statements)'},
            {'key': 'pg_top_calls', 'sql': """
                SELECT LEFT(query, 200) AS query, COUNT(*) AS active_count, state
                FROM pg_stat_activity
                WHERE state != 'idle' AND query NOT LIKE 'SELECT query%' AND datname IS NOT NULL
                GROUP BY LEFT(query, 200), state
                ORDER BY active_count DESC
                LIMIT 10;
            """,
             'desc_zh': 'TOP SQL（活跃并发，需 pg_stat_statements 获取历史）', 'desc_en': 'Top SQL by concurrency (full history needs pg_stat_statements)'},
        ]
    },
    {
        'chapter_number': 13,
        'chapter_title_zh': '用户与角色权限',
        'chapter_title_en': 'Users & Roles',
        'description': '数据库用户和角色权限',
        'queries': [
            {'key': 'pg_users', 'sql': "SELECT usename, usesuper, usecreatedb, userepl, usebypassrls, passwd IS NOT NULL AS has_password FROM pg_user ORDER BY usename;",
             'desc_zh': '数据库用户列表',                'desc_en': 'Database users list'},
            {'key': 'pg_roles', 'sql': "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication FROM pg_roles ORDER BY rolname;",
             'desc_zh': '角色列表',                      'desc_en': 'Roles list'},
        ]
    },
    {
        'chapter_number': 14,
        'chapter_title_zh': '数据库连接池状态',
        'chapter_title_en': 'Connection Pool Status',
        'description': '连接池相关信息',
        'queries': [
            {'key': 'pg_conn_by_db', 'sql': "SELECT datname, count(*) AS conn_count, count(*) FILTER (WHERE state='active') AS active_count FROM pg_stat_activity GROUP BY datname ORDER BY conn_count DESC;",
             'desc_zh': '按数据库统计连接数',           'desc_en': 'Connections by database'},
            {'key': 'pg_conn_by_user', 'sql': "SELECT usename, count(*) AS conn_count FROM pg_stat_activity GROUP BY usename ORDER BY conn_count DESC;",
             'desc_zh': '按用户统计连接数',           'desc_en': 'Connections by user'},
        ]
    },
    {
        'chapter_number': 15,
        'chapter_title_zh': '备份与恢复状态',
        'chapter_title_en': 'Backup & Recovery Status',
        'description': '备份工具和恢复相关信息',
        'queries': [
            {'key': 'pg_is_in_recovery', 'sql': "SELECT pg_is_in_recovery() AS is_standby, CASE WHEN pg_is_in_recovery() THEN pg_last_wal_receive_lsn() ELSE pg_current_wal_lsn() END AS last_lsn;",
             'desc_zh': '是否处于恢复模式',              'desc_en': 'Is in recovery mode'},
        ]
    },
    {
        'chapter_number': 16,
        'chapter_title_zh': '表统计信息新鲜度',
        'chapter_title_en': 'Table Statistics Freshness',
        'description': '统计信息最后收集时间',
        'queries': [
            {'key': 'pg_stat_last_analyze', 'sql': """
                SELECT schemaname, relname, last_analyze, last_autoanalyze, n_live_tup
                FROM pg_stat_user_tables
                WHERE last_analyze IS NULL OR last_analyze < now() - interval '7 days'
                ORDER BY last_analyze ASC NULLS FIRST
                LIMIT 20;
            """,
             'desc_zh': '统计信息过期的表（>7天）', 'desc_en': 'Tables with stale stats (>7 days)'},
        ]
    },
    {
        'chapter_number': 17,
        'chapter_title_zh': '索引大小与维护',
        'chapter_title_en': 'Index Size & Maintenance',
        'description': '索引大小和是否需要重建',
        'queries': [
            {'key': 'pg_index_size', 'sql': """
                SELECT schemaname, relname AS tablename, indexrelname AS indexname,
                       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
                FROM pg_stat_user_indexes
                ORDER BY pg_relation_size(indexrelid) DESC
                LIMIT 20;
            """,
             'desc_zh': '索引大小 TOP 20',            'desc_en': 'Top 20 largest indexes'},
            {'key': 'pg_index_bloat', 'sql': """
                SELECT schemaname, relname AS tablename, indexrelname AS indexname,
                       idx_scan, idx_tup_read, idx_tup_fetch
                FROM pg_stat_user_indexes
                WHERE idx_scan = 0
                ORDER BY pg_relation_size(indexrelid) DESC
                LIMIT 20;
            """,
             'desc_zh': '从未使用的索引（按大小排序）', 'desc_en': 'Unused indexes by size'},
        ]
    },
    {
        'chapter_number': 18,
        'chapter_title_zh': '数据库对象统计',
        'chapter_title_en': 'Database Object Statistics',
        'description': '表、索引、序列等对象数量',
        'queries': [
            {'key': 'pg_table_count', 'sql': "SELECT schemaname, count(*) AS table_count FROM pg_stat_user_tables GROUP BY schemaname;",
             'desc_zh': '各 schema 表数量',         'desc_en': 'Table count by schema'},
            {'key': 'pg_index_count', 'sql': "SELECT schemaname, count(*) AS index_count FROM pg_stat_user_indexes GROUP BY schemaname;",
             'desc_zh': '各 schema 索引数量',        'desc_en': 'Index count by schema'},
        ]
    },
    {
        'chapter_number': 19,
        'chapter_title_zh': '死锁与错误日志',
        'chapter_title_en': 'Deadlocks & Error Logs',
        'description': '死锁统计和日志配置',
        'queries': [
            {'key': 'pg_deadlocks', 'sql': "SELECT datname, deadlocks FROM pg_stat_database ORDER BY deadlocks DESC;",
             'desc_zh': '各库死锁次数',                'desc_en': 'Deadlocks by database'},
            {'key': 'pg_log_settings', 'sql': "SELECT name, setting, unit FROM pg_settings WHERE name LIKE '%log%' AND setting != 'off' ORDER BY name;",
             'desc_zh': '日志相关配置',                 'desc_en': 'Logging configuration'},
        ]
    },
    {
        'chapter_number': 20,
        'chapter_title_zh': '归档状态',
        'chapter_title_en': 'Archive Status',
        'description': 'WAL 归档状态检查',
        'queries': [
            {'key': 'pg_archive_status', 'sql': "SELECT * FROM pg_stat_archiver;",
             'desc_zh': '归档进程状态',                'desc_en': 'Archiver process status'},
            {'key': 'pg_archive_ready', 'sql': "SELECT count(*) AS ready_count FROM (SELECT * FROM pg_ls_dir('pg_wal/archive_status')) AS files WHERE pg_ls_dir LIKE '%.ready';",
             'desc_zh': '待归档 WAL 数量',           'desc_en': 'WAL files waiting for archive'},
        ]
    },
    {
        'chapter_number': 21,
        'chapter_title_zh': '插件与扩展状态',
        'chapter_title_en': 'Extensions & Plugins',
        'description': '已安装扩展和可用扩展',
        'queries': [
            {'key': 'pg_installed_ext', 'sql': "SELECT extname, extversion, extrelocatable FROM pg_extension ORDER BY extname;",
             'desc_zh': '已安装扩展详情',              'desc_en': 'Installed extensions detail'},
            {'key': 'pg_available_ext', 'sql': "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE installed_version IS NULL ORDER BY name LIMIT 20;",
             'desc_zh': '可安装扩展（前20）',        'desc_en': 'Available extensions (top 20)'},
        ]
    },
]
# 临时文件：Oracle 21 章配置

ORACLE_DEFAULT_CHAPTERS = [
    {
        'chapter_number': 1,
        'chapter_title_zh': '健康状态概览',
        'chapter_title_en': 'Health Overview',
        'description': '数据库整体健康状态概览',
        'queries': [
            {'key': 'oracle_version',   'sql': "SELECT * FROM v$version;",
             'desc_zh': '获取 Oracle 版本',         'desc_en': 'Get Oracle version'},
            {'key': 'instance_status', 'sql': "SELECT INSTANCE_NUMBER, INSTANCE_NAME, HOST_NAME, VERSION, STARTUP_TIME, STATUS, PARALLEL FROM v$instance;",
             'desc_zh': '实例状态',                 'desc_en': 'Instance status'},
            {'key': 'database_info',   'sql': "SELECT name, dbid, created, log_mode, open_mode, force_logging, flashback_on FROM v$database;",
             'desc_zh': '数据库概要信息',           'desc_en': 'Database summary info'},
        ]
    },
    {
        'chapter_number': 2,
        'chapter_title_zh': '数据库空间使用',
        'chapter_title_en': 'Tablespace Usage',
        'description': '表空间使用率检查',
        'queries': [
            {'key': 'tablespace_usage', 'sql': """
                SELECT a.tablespace_name,
                       ROUND(a.total_space_mb, 2) AS total_mb,
                       ROUND(NVL(b.used_space_mb, 0), 2) AS used_mb,
                       ROUND(a.total_space_mb - NVL(b.used_space_mb, 0), 2) AS free_mb,
                       ROUND(NVL(b.used_space_mb, 0) * 100.0 / a.total_space_mb, 2) AS used_percent
                FROM (SELECT tablespace_name, SUM(bytes) / 1024 / 1024 AS total_space_mb FROM dba_data_files GROUP BY tablespace_name) a
                LEFT JOIN (SELECT tablespace_name, SUM(bytes) / 1024 / 1024 AS used_space_mb FROM dba_segments GROUP BY tablespace_name) b
                ON a.tablespace_name = b.tablespace_name
                ORDER BY used_percent DESC;
            """,
             'desc_zh': '表空间使用率',      'desc_en': 'Tablespace usage'},
            {'key': 'datafile_status', 'sql': "SELECT file_name, tablespace_name, ROUND(bytes/1024/1024) AS size_mb, autoextensible, maxbytes/1024/1024 AS max_mb, status FROM dba_data_files ORDER BY tablespace_name;",
             'desc_zh': '数据文件状态',      'desc_en': 'Data file status'},
            {'key': 'temp_space',      'sql': """
                SELECT tablespace_name, SUM(bytes)/1024/1024 AS total_mb, SUM(NVL(blocks,0))*8192/1024/1024 AS used_mb
                FROM dba_temp_files
                GROUP BY tablespace_name;
            """,
             'desc_zh': '临时表空间使用',    'desc_en': 'Temp tablespace usage'},
        ]
    },
    {
        'chapter_number': 3,
        'chapter_title_zh': '会话与连接检查',
        'chapter_title_en': 'Session & Connection Check',
        'description': '会话和连接状态检查',
        'queries': [
            {'key': 'session_count', 'sql': "SELECT COUNT(*) AS total_sessions, SUM(CASE WHEN username IS NOT NULL THEN 1 ELSE 0 END) AS user_sessions, SUM(CASE WHEN username IS NULL THEN 1 ELSE 0 END) AS background_sessions FROM v$session;",
             'desc_zh': '会话数统计',         'desc_en': 'Session count statistics'},
            {'key': 'session_wait',  'sql': "SELECT event, COUNT(*) AS wait_count FROM v$session_wait WHERE wait_class != 'Idle' GROUP BY event ORDER BY wait_count DESC;",
             'desc_zh': '等待事件统计',         'desc_en': 'Wait event statistics'},
            {'key': 'long_sessions', 'sql': "SELECT sid, serial#, username, status, machine, program, logon_time FROM v$session WHERE type='USER' AND username IS NOT NULL ORDER BY logon_time DESC;",
             'desc_zh': '用户会话列表',         'desc_en': 'User session list'},
            {'key': 'process_count',  'sql': "SELECT COUNT(*) AS process_count FROM v$process;",
             'desc_zh': '进程数',               'desc_en': 'Process count'},
        ]
    },
    {
        'chapter_number': 4,
        'chapter_title_zh': '锁等待检查',
        'chapter_title_en': 'Lock & Wait Analysis',
        'description': '锁等待和长事务检查',
        'queries': [
            {'key': 'lock_wait', 'sql': """
                SELECT s.sid, s.serial#, s.username, s.event, l.type, l.lmode, l.request, s.seconds_in_wait
                FROM v$session s
                JOIN v$lock l ON s.sid = l.sid
                WHERE l.request > 0
                ORDER BY s.seconds_in_wait DESC;
            """,
             'desc_zh': '锁等待会话',           'desc_en': 'Lock waiting sessions'},
            {'key': 'long_trx', 'sql': """
                SELECT s.sid, s.serial#, s.username, s.machine, s.program,
                       t.START_DATE, ROUND((SYSDATE - t.START_DATE) * 86400) AS seconds,
                       t.USED_UBLK, t.USED_UREC
                FROM v$transaction t
                JOIN v$session s ON s.SADDR = t.SES_ADDR
                ORDER BY t.START_DATE;
            """,
             'desc_zh': '长事务列表',           'desc_en': 'Long transactions'},
            {'key': 'lock_blockers', 'sql': """
                SELECT blocking_session, sid, serial#, username, event, seconds_in_wait
                FROM v$session
                WHERE blocking_session IS NOT NULL
                ORDER BY blocking_session;
            """,
             'desc_zh': '锁阻塞源头',           'desc_en': 'Lock blocking sessions'},
        ]
    },
    {
        'chapter_number': 5,
        'chapter_title_zh': '参数与配置检查',
        'chapter_title_en': 'Parameter & Config Check',
        'description': '关键参数检查',
        'queries': [
            {'key': 'key_params', 'sql': """
                SELECT name, value, display_value, isdefault, ismodified
                FROM v$parameter
                WHERE name IN ('processes','sessions','sga_target','pga_aggregate_target',
                               'db_cache_size','shared_pool_size','log_buffer',
                               'open_cursors','cursor_sharing','optimizer_mode',
                               'undo_tablespace','undo_retention')
                ORDER BY name;
            """,
             'desc_zh': '关键参数一览',         'desc_en': 'Key parameters overview'},
            {'key': 'nondefault_params', 'sql': """
                SELECT name, value, isdefault
                FROM v$parameter
                WHERE isdefault = 'FALSE'
                ORDER BY name;
            """,
             'desc_zh': '非默认参数',           'desc_en': 'Non-default parameters'},
        ]
    },
    {
        'chapter_number': 6,
        'chapter_title_zh': 'RMAN 备份状态',
        'chapter_title_en': 'RMAN Backup Status',
        'description': 'RMAN 备份状态检查',
        'queries': [
            {'key': 'rman_status', 'sql': "SELECT * FROM v$rman_status;",
             'desc_zh': 'RMAN 运行状态',        'desc_en': 'RMAN running status'},
            {'key': 'backup_files', 'sql': """
                SELECT TO_CHAR(END_TIME, 'YYYY-MM-DD HH24:MI') AS backup_time, STATUS, OUTPUT_DEVICE_TYPE AS device,
                       ROUND(OUTPUT_BYTES/1024/1024/1024, 2) AS output_gb, ROUND(ELAPSED_SECONDS/60, 1) AS elapsed_min
                FROM v$rman_backup_job_details
                WHERE END_TIME IS NOT NULL ORDER BY END_TIME DESC;
            """,
             'desc_zh': '当前备份文件',         'desc_en': 'Current backup files'},
        ]
    },
    {
        'chapter_number': 7,
        'chapter_title_zh': '归档日志状态',
        'chapter_title_en': 'Archive Log Status',
        'description': '归档日志状态检查',
        'queries': [
            {'key': 'archive_dest', 'sql': """
                SELECT dest_id, dest_name, status, target, schedule, destination
                FROM v$archive_dest
                WHERE target != 'PRIMARY'
                ORDER BY dest_id;
            """,
             'desc_zh': '归档目标状态',         'desc_en': 'Archive destination status'},
            {'key': 'archive_log', 'sql': """
                SELECT sequence#, first_time, next_time, applied, deleted
                FROM v$archived_log
                ORDER BY sequence# DESC
                FETCH FIRST 20 ROWS ONLY;
            """,
             'desc_zh': '最近归档日志',         'desc_en': 'Recent archived logs'},
            {'key': 'archive_gap', 'sql': """
                SELECT thread#, low_sequence#, high_sequence#
                FROM v$archive_gap;
            """,
             'desc_zh': '归档日志缺口',         'desc_en': 'Archive log gap'},
        ]
    },
    {
        'chapter_number': 8,
        'chapter_title_zh': '数据库文件状态',
        'chapter_title_en': 'Datafile Status',
        'description': '数据文件和临时文件状态',
        'queries': [
            {'key': 'datafile_status', 'sql': """
                SELECT file#, name, status, enabled, bytes/1024/1024 AS size_mb
                FROM v$datafile
                ORDER BY file#;
            """,
             'desc_zh': '数据文件状态',         'desc_en': 'Data file status'},
            {'key': 'tempfile_status', 'sql': """
                SELECT file#, name, status, enabled, bytes/1024/1024 AS size_mb
                FROM v$tempfile
                ORDER BY file#;
            """,
             'desc_zh': '临时文件状态',         'desc_en': 'Temp file status'},
        ]
    },
    {
        'chapter_number': 9,
        'chapter_title_zh': '重做日志状态',
        'chapter_title_en': 'Redo Log Status',
        'description': '重做日志组和成员状态',
        'queries': [
            {'key': 'log_group', 'sql': """
                SELECT group#, thread#, sequence#, bytes/1024/1024 AS size_mb, members, archived, status
                FROM v$log
                ORDER BY group#;
            """,
             'desc_zh': '日志组状态',           'desc_en': 'Log group status'},
            {'key': 'log_file',  'sql': """
                SELECT group#, member, type, status
                FROM v$logfile
                ORDER BY group#, member;
            """,
             'desc_zh': '日志文件成员',         'desc_en': 'Log file members'},
            {'key': 'log_switch', 'sql': """
                SELECT TO_CHAR(first_time, 'YYYY-MM-DD HH24:MI') AS switch_time,
                       COUNT(*) AS switch_count
                FROM v$log_history
                WHERE first_time > SYSDATE - 1
                GROUP BY TO_CHAR(first_time, 'YYYY-MM-DD HH24:MI')
                ORDER BY switch_time;
            """,
             'desc_zh': '日志切换频率（24h）',  'desc_en': 'Log switch frequency (24h)'},
        ]
    },
    {
        'chapter_number': 10,
        'chapter_title_zh': 'UNDO 表空间',
        'chapter_title_en': 'UNDO Tablespace',
        'description': 'UNDO 表空间使用率和状态',
        'queries': [
            {'key': 'undo_status', 'sql': """
                SELECT tablespace_name, status, contents, logging
                FROM dba_tablespaces
                WHERE contents = 'UNDO'
                ORDER BY tablespace_name;
            """,
             'desc_zh': 'UNDO 表空间状态',    'desc_en': 'UNDO tablespace status'},
            {'key': 'undo_usage',  'sql': """
                SELECT seg.tablespace_name,
                       ROUND(SUM(seg.bytes)/1024/1024, 2) AS used_mb,
                       ROUND(SUM(NVL(u.bytes,0))) AS total_mb
                FROM dba_segments seg
                LEFT JOIN dba_data_files u ON seg.tablespace_name = u.tablespace_name
                WHERE seg.tablespace_name IN (SELECT tablespace_name FROM dba_tablespaces WHERE contents = 'UNDO')
                GROUP BY seg.tablespace_name;
            """,
             'desc_zh': 'UNDO 使用率',          'desc_en': 'UNDO usage'},
            {'key': 'undo_stats',   'sql': """
                SELECT TO_CHAR(begin_time, 'YYYY-MM-DD HH24:MI') AS begin_time,
                       TO_CHAR(end_time, 'YYYY-MM-DD HH24:MI') AS end_time,
                       undoblks, txncount, maxquerylen
                FROM v$undostat
                WHERE begin_time > SYSDATE - 1
                ORDER BY begin_time;
            """,
             'desc_zh': 'UNDO 统计（1h）',      'desc_en': 'UNDO statistics (1h)'},
        ]
    },
    {
        'chapter_number': 11,
        'chapter_title_zh': 'AWR 快照状态',
        'chapter_title_en': 'AWR Snapshot Status',
        'description': 'AWR 快照和基线状态',
        'queries': [
            {'key': 'awr_snap', 'sql': """
                SELECT snap_id, dbid, instance_number, begin_interval_time, end_interval_time,
                       snap_level, error_count
                FROM dba_hist_snapshot
                ORDER BY snap_id DESC
                FETCH FIRST 20 ROWS ONLY;
            """,
             'desc_zh': 'AWR 快照列表',       'desc_en': 'AWR snapshot list'},
            {'key': 'awr_baseline', 'sql': """
                SELECT baseline_id, baseline_name, start_snap_id, end_snap_id,
                       TO_CHAR(creation_time, 'YYYY-MM-DD HH24:MI') AS created
                FROM dba_hist_baseline
                ORDER BY baseline_id;
            """,
             'desc_zh': 'AWR 基线列表',       'desc_en': 'AWR baseline list'},
            {'key': 'awr_retention', 'sql': """
                SELECT retention, snap_interval, topnsql
                FROM dba_hist_wr_control;
            """,
             'desc_zh': 'AWR 保留策略',       'desc_en': 'AWR retention policy'},
        ]
    },
    {
        'chapter_number': 12,
        'chapter_title_zh': 'ADDM 发现',
        'chapter_title_en': 'ADDM Findings',
        'description': 'ADDM 自动发现的问题',
        'queries': [
            {'key': 'addm_findings', 'sql': """
                SELECT r.RANK, f.FINDING_ID, f.FINDING_NAME, f.IMPACT_TYPE,
                       f.IMPACT, r.BENEFIT_TYPE, r.BENEFIT, f.MESSAGE
                FROM dba_advisor_findings f
                INNER JOIN dba_advisor_recommendations r
                  ON f.TASK_ID = r.TASK_ID
                 AND f.EXECUTION_NAME = r.EXECUTION_NAME
                 AND f.FINDING_ID = r.FINDING_ID
                WHERE r.TASK_NAME = 'ADDM TASK'
                ORDER BY r.RANK, f.FINDING_ID;
            """,
             'desc_zh': '最新 ADDM 发现',      'desc_en': 'Latest ADDM findings'},
        ]
    },
    {
        'chapter_number': 13,
        'chapter_title_zh': 'TOP SQL 分析',
        'chapter_title_en': 'Top SQL Analysis',
        'description': '按资源消耗排序的 TOP SQL',
        'queries': [
            {'key': 'top_sql_elapsed', 'sql': """
                SELECT sql_id, executions, elapsed_time/1000000 AS elapsed_sec,
                       cpu_time/1000000 AS cpu_sec, disk_reads, buffer_gets,
                       SUBSTR(sql_text, 1, 100) AS sql_preview
                FROM v$sqlarea
                WHERE executions > 0
                ORDER BY elapsed_time DESC
                FETCH FIRST 10 ROWS ONLY;
            """,
             'desc_zh': 'TOP SQL（耗时）',       'desc_en': 'Top SQL by elapsed time'},
            {'key': 'top_sql_cpu', 'sql': """
                SELECT sql_id, executions, cpu_time/1000000 AS cpu_sec,
                       elapsed_time/1000000 AS elapsed_sec, disk_reads,
                       SUBSTR(sql_text, 1, 100) AS sql_preview
                FROM v$sqlarea
                WHERE executions > 0
                ORDER BY cpu_time DESC
                FETCH FIRST 10 ROWS ONLY;
            """,
             'desc_zh': 'TOP SQL（CPU）',        'desc_en': 'Top SQL by CPU time'},
            {'key': 'top_sql_reads', 'sql': """
                SELECT sql_id, executions, disk_reads,
                       elapsed_time/1000000 AS elapsed_sec,
                       SUBSTR(sql_text, 1, 100) AS sql_preview
                FROM v$sqlarea
                WHERE executions > 0
                ORDER BY disk_reads DESC
                FETCH FIRST 10 ROWS ONLY;
            """,
             'desc_zh': 'TOP SQL（物理读）',     'desc_en': 'Top SQL by disk reads'},
        ]
    },
    {
        'chapter_number': 14,
        'chapter_title_zh': '表统计信息状态',
        'chapter_title_en': 'Table Statistics Status',
        'description': '表统计信息新鲜度和质量',
        'queries': [
            {'key': 'stale_tables', 'sql': """
                SELECT owner, table_name, num_rows, last_analyzed,
                       stale_stats
                FROM dba_tab_statistics
                WHERE stale_stats = 'YES'
                ORDER BY owner, table_name;
            """,
             'desc_zh': '统计信息过期的表',    'desc_en': 'Tables with stale statistics'},
            {'key': 'no_stats_tables', 'sql': """
                SELECT owner, table_name, num_rows, last_analyzed
                FROM dba_tables
                WHERE last_analyzed IS NULL
                  AND owner NOT IN ('SYS','SYSTEM','OUTLN','DBSNMP')
                ORDER BY owner, table_name;
            """,
             'desc_zh': '从未收集统计的表',   'desc_en': 'Tables without statistics'},
        ]
    },
    {
        'chapter_number': 15,
        'chapter_title_zh': '索引统计信息状态',
        'chapter_title_en': 'Index Statistics Status',
        'description': '索引统计信息新鲜度和质量',
        'queries': [
            {'key': 'stale_indexes', 'sql': """
                SELECT owner, index_name, table_name, num_rows, last_analyzed,
                       stale_stats
                FROM dba_ind_statistics
                WHERE stale_stats = 'YES'
                ORDER BY owner, index_name;
            """,
             'desc_zh': '统计信息过期的索引',  'desc_en': 'Indexes with stale statistics'},
            {'key': 'unused_indexes', 'sql': """
                SELECT d.OWNER, d.OBJECT_NAME AS INDEX_NAME, d.OBJECT_TYPE, s.VALUE AS LOGICAL_READS
                FROM v$segment_statistics s
                JOIN dba_objects d ON s.OBJ# = d.OBJECT_ID
                WHERE s.STATISTIC_NAME = 'logical reads'
                  AND d.OBJECT_TYPE = 'INDEX'
                  AND d.OWNER NOT IN ('SYS', 'SYSTEM')
                  AND TO_NUMBER(s.VALUE) = 0
                ORDER BY d.OWNER, d.OBJECT_NAME;
            """,
             'desc_zh': '未使用的索引',        'desc_en': 'Unused indexes'},
        ]
    },
    {
        'chapter_number': 16,
        'chapter_title_zh': '无效对象',
        'chapter_title_en': 'Invalid Objects',
        'description': '无效或破损的数据库对象',
        'queries': [
            {'key': 'invalid_objects', 'sql': """
                SELECT owner, object_type, object_name, status, last_ddl_time
                FROM dba_objects
                WHERE status != 'VALID'
                ORDER BY owner, object_type, object_name;
            """,
             'desc_zh': '无效对象列表',         'desc_en': 'Invalid objects list'},
            {'key': 'broken_triggers', 'sql': """
                SELECT owner, trigger_name, table_name, status
                FROM dba_triggers
                WHERE status = 'DISABLED'
                ORDER BY owner, trigger_name;
            """,
             'desc_zh': '禁用/破损的触发器',   'desc_en': 'Disabled/broken triggers'},
        ]
    },
    {
        'chapter_number': 17,
        'chapter_title_zh': '数据库链接',
        'chapter_title_en': 'Database Links',
        'description': '数据库链接状态',
        'queries': [
            {'key': 'db_links', 'sql': """
                SELECT owner, db_link, username, host, created
                FROM dba_db_links
                ORDER BY owner, db_link;
            """,
             'desc_zh': '数据库链接列表',         'desc_en': 'Database links list'},
        ]
    },
    {
        'chapter_number': 18,
        'chapter_title_zh': '作业/调度器状态',
        'chapter_title_en': 'Jobs & Scheduler Status',
        'description': '数据库作业和调度器状态',
        'queries': [
            {'key': 'dba_jobs', 'sql': """
                SELECT job, log_user, priv_user, last_date, last_sec, next_date, next_sec,
                       broken, failures
                FROM dba_jobs
                ORDER BY job;
            """,
             'desc_zh': '传统作业状态',         'desc_en': 'Legacy jobs status'},
            {'key': 'scheduler_jobs', 'sql': """
                SELECT owner, job_name, enabled, state, last_start_date, next_run_date
                FROM dba_scheduler_jobs
                WHERE owner NOT IN ('SYS','SYSTEM')
                ORDER BY owner, job_name;
            """,
             'desc_zh': '调度器作业状态',        'desc_en': 'Scheduler jobs status'},
        ]
    },
    {
        'chapter_number': 19,
        'chapter_title_zh': '用户与安全审计',
        'chapter_title_en': 'User & Security Audit',
        'description': '用户、权限和安全审计',
        'queries': [
            {'key': 'dba_users', 'sql': """
                SELECT username, account_status, lock_date, expiry_date,
                       created, profile, authentication_type
                FROM dba_users
                WHERE username NOT IN ('SYS','SYSTEM','OUTLN','DBSNMP','APPQOSSYS','GSMADMIN_INTERNAL')
                ORDER BY username;
            """,
             'desc_zh': '用户账号状态',         'desc_en': 'User account status'},
            {'key': 'privileged_users', 'sql': """
                SELECT grantee, granted_role, admin_option
                FROM dba_role_privs
                WHERE granted_role = 'DBA'
                ORDER BY grantee;
            """,
             'desc_zh': 'DBA 权限用户',        'desc_en': 'DBA privileged users'},
            {'key': 'password_policy', 'sql': """
                SELECT profile, resource_name, resource_type, limit
                FROM dba_profiles
                WHERE resource_type = 'PASSWORD'
                ORDER BY profile, resource_name;
            """,
             'desc_zh': '密码策略配置',         'desc_en': 'Password policy config'},
        ]
    },
    {
        'chapter_number': 20,
        'chapter_title_zh': '补丁级别',
        'chapter_title_en': 'Patch Level',
        'description': '数据库补丁和 PSU 级别',
        'queries': [
            {'key': 'registry_sqlpatch', 'sql': """
                SELECT patch_id, patch_uid, action, action_time, description, status
                FROM dba_registry_sqlpatch
                ORDER BY patch_id DESC;
            """,
             'desc_zh': '已安装补丁列表',        'desc_en': 'Installed patches list'},
            {'key': 'registry', 'sql': """
                SELECT comp_id, comp_name, version, status, modified
                FROM dba_registry
                ORDER BY comp_id;
            """,
             'desc_zh': '数据库组件注册表',      'desc_en': 'Database component registry'},
        ]
    },
    {
        'chapter_number': 21,
        'chapter_title_zh': 'Data Guard 状态',
        'chapter_title_en': 'Data Guard Status',
        'description': 'Data Guard 主备同步状态',
        'queries': [
            {'key': 'dg_database', 'sql': """
                SELECT name, db_unique_name, database_role, protection_mode,
                       protection_level, switchover_status, dataguard_broker
                FROM v$database;
            """,
             'desc_zh': 'Data Guard 数据库角色', 'desc_en': 'Data Guard database role'},
            {'key': 'dg_archive_dest', 'sql': """
                SELECT DEST_ID, DEST_NAME, STATUS, TARGET, SCHEDULE, DESTINATION, ERROR
                FROM v$archive_dest
                WHERE DEST_ID < 3
                ORDER BY DEST_ID;
            """,
             'desc_zh': '归档目标同步状态',      'desc_en': 'Archive destination sync status'},
            {'key': 'dg_stats', 'sql': """
                SELECT name, value, unit, time_computed
                FROM v$dataguard_stats;
            """,
             'desc_zh': 'Data Guard 统计信息',  'desc_en': 'Data Guard statistics'},
        ]
    },
]
# Oracle 11g 专用巡检模板（21 章，兼容 Oracle 11gR2）
# 与默认模板差异：
#   - 所有章节 FETCH FIRST N ROWS ONLY → WHERE ROWNUM <= N
#   - 章节3 session_wait 改用 v$session_event（11g 无 wait_class 列）
#   - 章节20 registry_sqlpatch 改用 registry$history
ORACLE_11G_CHAPTERS = [
    {
        'chapter_number': 1,
        'chapter_title_zh': '健康状态概览',
        'chapter_title_en': 'Health Overview',
        'description': '数据库整体健康状态概览',
        'queries': [
            {'key': 'oracle_version',   'sql': "SELECT * FROM v$version;",
             'desc_zh': '获取 Oracle 版本',         'desc_en': 'Get Oracle version'},
            {'key': 'instance_status', 'sql': "SELECT INSTANCE_NUMBER, INSTANCE_NAME, HOST_NAME, VERSION, STARTUP_TIME, STATUS, PARALLEL FROM v$instance;",
             'desc_zh': '实例状态',                 'desc_en': 'Instance status'},
            {'key': 'database_info',   'sql': "SELECT name, dbid, created, log_mode, open_mode, force_logging, flashback_on FROM v$database;",
             'desc_zh': '数据库概要信息',           'desc_en': 'Database summary info'},
        ]
    },
    {
        'chapter_number': 2,
        'chapter_title_zh': '数据库空间使用',
        'chapter_title_en': 'Tablespace Usage',
        'description': '表空间使用率检查',
        'queries': [
            {'key': 'tablespace_usage', 'sql': """
                SELECT a.tablespace_name,
                       ROUND(a.total_space_mb, 2) AS total_mb,
                       ROUND(NVL(b.used_space_mb, 0), 2) AS used_mb,
                       ROUND(a.total_space_mb - NVL(b.used_space_mb, 0), 2) AS free_mb,
                       ROUND(NVL(b.used_space_mb, 0) * 100.0 / a.total_space_mb, 2) AS used_percent
                FROM (SELECT tablespace_name, SUM(bytes) / 1024 / 1024 AS total_space_mb FROM dba_data_files GROUP BY tablespace_name) a
                LEFT JOIN (SELECT tablespace_name, SUM(bytes) / 1024 / 1024 AS used_space_mb FROM dba_segments GROUP BY tablespace_name) b
                ON a.tablespace_name = b.tablespace_name
                ORDER BY used_percent DESC;
            """,
             'desc_zh': '表空间使用率',      'desc_en': 'Tablespace usage'},
            {'key': 'ts_datafile_status', 'sql': "SELECT file_name, tablespace_name, ROUND(bytes/1024/1024) AS size_mb, autoextensible, maxbytes/1024/1024 AS max_mb, status FROM dba_data_files ORDER BY tablespace_name;",
             'desc_zh': '数据文件状态',      'desc_en': 'Data file status'},
            {'key': 'temp_space',      'sql': """
                SELECT tablespace_name, SUM(bytes)/1024/1024 AS total_mb, SUM(NVL(blocks,0))*8192/1024/1024 AS used_mb
                FROM dba_temp_files
                GROUP BY tablespace_name;
            """,
             'desc_zh': '临时表空间使用',    'desc_en': 'Temp tablespace usage'},
        ]
    },
    {
        'chapter_number': 3,
        'chapter_title_zh': '会话与连接检查',
        'chapter_title_en': 'Session & Connection Check',
        'description': '会话和连接状态检查',
        'queries': [
            {'key': 'session_count', 'sql': "SELECT COUNT(*) AS total_sessions, SUM(CASE WHEN username IS NOT NULL THEN 1 ELSE 0 END) AS user_sessions, SUM(CASE WHEN username IS NULL THEN 1 ELSE 0 END) AS background_sessions FROM v$session;",
             'desc_zh': '会话数统计',         'desc_en': 'Session count statistics'},
            {'key': 'session_wait',  'sql': """
                SELECT event, SUM(total_waits) AS total_waits, SUM(time_waited) AS total_time
                FROM v$session_event
                WHERE event NOT IN (
                    'smon timer','pmon timer','rdbms ipc message','null event',
                    'SQL*Net message from client','SQL*Net message to client',
                    'wakeup time manager','pipe get','PX Idle Wait',
                    'i/o completion wait','gcs remote message','gcs for action',
                    'DIAG idle wait','ASM background timer','pfc timer')
                GROUP BY event ORDER BY total_waits DESC;
            """,
             'desc_zh': '等待事件统计',         'desc_en': 'Wait event statistics'},
            {'key': 'long_sessions', 'sql': "SELECT sid, serial#, username, status, machine, program, logon_time FROM v$session WHERE type='USER' AND username IS NOT NULL ORDER BY logon_time DESC;",
             'desc_zh': '用户会话列表',         'desc_en': 'User session list'},
            {'key': 'process_count',  'sql': "SELECT COUNT(*) AS process_count FROM v$process;",
             'desc_zh': '进程数',               'desc_en': 'Process count'},
        ]
    },
    {
        'chapter_number': 4,
        'chapter_title_zh': '锁等待检查',
        'chapter_title_en': 'Lock & Wait Analysis',
        'description': '锁等待和长事务检查',
        'queries': [
            {'key': 'lock_wait', 'sql': """
                SELECT s.sid, s.serial#, s.username, s.event, l.type, l.lmode, l.request, s.seconds_in_wait
                FROM v$session s
                JOIN v$lock l ON s.sid = l.sid
                WHERE l.request > 0
                ORDER BY s.seconds_in_wait DESC;
            """,
             'desc_zh': '锁等待会话',           'desc_en': 'Lock waiting sessions'},
            {'key': 'long_trx', 'sql': """
                SELECT s.sid, s.serial#, s.username, s.machine, s.program,
                       t.START_DATE, ROUND((SYSDATE - t.START_DATE) * 86400) AS seconds,
                       t.USED_UBLK, t.USED_UREC
                FROM v$transaction t
                JOIN v$session s ON s.SADDR = t.SES_ADDR
                ORDER BY t.START_DATE;
            """,
             'desc_zh': '长事务列表',           'desc_en': 'Long transactions'},
            {'key': 'lock_blockers', 'sql': """
                SELECT blocking_session, sid, serial#, username, event, seconds_in_wait
                FROM v$session
                WHERE blocking_session IS NOT NULL
                ORDER BY blocking_session;
            """,
             'desc_zh': '锁阻塞源头',           'desc_en': 'Lock blocking sessions'},
        ]
    },
    {
        'chapter_number': 5,
        'chapter_title_zh': '参数与配置检查',
        'chapter_title_en': 'Parameter & Config Check',
        'description': '关键参数检查',
        'queries': [
            {'key': 'key_params', 'sql': """
                SELECT name, value, display_value, isdefault, ismodified
                FROM v$parameter
                WHERE name IN ('processes','sessions','sga_target','pga_aggregate_target',
                               'db_cache_size','shared_pool_size','log_buffer',
                               'open_cursors','cursor_sharing','optimizer_mode',
                               'undo_tablespace','undo_retention')
                ORDER BY name;
            """,
             'desc_zh': '关键参数一览',         'desc_en': 'Key parameters overview'},
            {'key': 'nondefault_params', 'sql': """
                SELECT name, value, isdefault
                FROM v$parameter
                WHERE isdefault = 'FALSE'
                ORDER BY name;
            """,
             'desc_zh': '非默认参数',           'desc_en': 'Non-default parameters'},
        ]
    },
    {
        'chapter_number': 6,
        'chapter_title_zh': 'RMAN 备份状态',
        'chapter_title_en': 'RMAN Backup Status',
        'description': 'RMAN 备份状态检查',
        'queries': [
            {'key': 'rman_status', 'sql': "SELECT * FROM v$rman_status;",
             'desc_zh': 'RMAN 运行状态',        'desc_en': 'RMAN running status'},
            {'key': 'backup_files', 'sql': """
                SELECT TO_CHAR(END_TIME, 'YYYY-MM-DD HH24:MI') AS backup_time, STATUS, OUTPUT_DEVICE_TYPE AS device,
                       ROUND(OUTPUT_BYTES/1024/1024/1024, 2) AS output_gb, ROUND(ELAPSED_SECONDS/60, 1) AS elapsed_min
                FROM v$rman_backup_job_details
                WHERE END_TIME IS NOT NULL ORDER BY END_TIME DESC;
            """,
             'desc_zh': '当前备份文件',         'desc_en': 'Current backup files'},
        ]
    },
    {
        'chapter_number': 7,
        'chapter_title_zh': '归档日志状态',
        'chapter_title_en': 'Archive Log Status',
        'description': '归档日志状态检查',
        'queries': [
            {'key': 'archive_dest', 'sql': """
                SELECT dest_id, dest_name, status, target, schedule, destination
                FROM v$archive_dest
                WHERE target != 'PRIMARY'
                ORDER BY dest_id;
            """,
             'desc_zh': '归档目标状态',         'desc_en': 'Archive destination status'},
            {'key': 'archive_log', 'sql': """
                SELECT sequence#, first_time, next_time, applied, deleted
                FROM (SELECT sequence#, first_time, next_time, applied, deleted,
                              ROW_NUMBER() OVER (ORDER BY sequence# DESC) AS rn
                       FROM v$archived_log)
                WHERE rn <= 20
                ORDER BY sequence# DESC;
            """,
             'desc_zh': '最近归档日志',         'desc_en': 'Recent archived logs'},
            {'key': 'archive_gap', 'sql': """
                SELECT thread#, low_sequence#, high_sequence#
                FROM v$archive_gap;
            """,
             'desc_zh': '归档日志缺口',         'desc_en': 'Archive log gap'},
        ]
    },
    {
        'chapter_number': 8,
        'chapter_title_zh': '数据库文件状态',
        'chapter_title_en': 'Datafile Status',
        'description': '数据文件和临时文件状态',
        'queries': [
            {'key': 'df_status', 'sql': """
                SELECT file#, name, status, enabled, bytes/1024/1024 AS size_mb
                FROM v$datafile
                ORDER BY file#;
            """,
             'desc_zh': '数据文件状态',         'desc_en': 'Data file status'},
            {'key': 'tempfile_status', 'sql': """
                SELECT file#, name, status, enabled, bytes/1024/1024 AS size_mb
                FROM v$tempfile
                ORDER BY file#;
            """,
             'desc_zh': '临时文件状态',         'desc_en': 'Temp file status'},
        ]
    },
    {
        'chapter_number': 9,
        'chapter_title_zh': '重做日志状态',
        'chapter_title_en': 'Redo Log Status',
        'description': '重做日志组和成员状态',
        'queries': [
            {'key': 'log_group', 'sql': """
                SELECT group#, thread#, sequence#, bytes/1024/1024 AS size_mb, members, archived, status
                FROM v$log
                ORDER BY group#;
            """,
             'desc_zh': '日志组状态',           'desc_en': 'Log group status'},
            {'key': 'log_file',  'sql': """
                SELECT group#, member, type, status
                FROM v$logfile
                ORDER BY group#, member;
            """,
             'desc_zh': '日志文件成员',         'desc_en': 'Log file members'},
            {'key': 'log_switch', 'sql': """
                SELECT TO_CHAR(first_time, 'YYYY-MM-DD HH24:MI') AS switch_time,
                       COUNT(*) AS switch_count
                FROM v$log_history
                WHERE first_time > SYSDATE - 1
                GROUP BY TO_CHAR(first_time, 'YYYY-MM-DD HH24:MI')
                ORDER BY switch_time;
            """,
             'desc_zh': '日志切换频率（24h）',  'desc_en': 'Log switch frequency (24h)'},
        ]
    },
    {
        'chapter_number': 10,
        'chapter_title_zh': 'UNDO 表空间',
        'chapter_title_en': 'UNDO Tablespace',
        'description': 'UNDO 表空间使用率和状态',
        'queries': [
            {'key': 'undo_status', 'sql': """
                SELECT tablespace_name, status, contents, logging
                FROM dba_tablespaces
                WHERE contents = 'UNDO'
                ORDER BY tablespace_name;
            """,
             'desc_zh': 'UNDO 表空间状态',    'desc_en': 'UNDO tablespace status'},
            {'key': 'undo_usage',  'sql': """
                SELECT seg.tablespace_name,
                       ROUND(SUM(seg.bytes)/1024/1024, 2) AS used_mb,
                       ROUND(SUM(NVL(u.bytes,0))) AS total_mb
                FROM dba_segments seg
                LEFT JOIN dba_data_files u ON seg.tablespace_name = u.tablespace_name
                WHERE seg.tablespace_name IN (SELECT tablespace_name FROM dba_tablespaces WHERE contents = 'UNDO')
                GROUP BY seg.tablespace_name;
            """,
             'desc_zh': 'UNDO 使用率',          'desc_en': 'UNDO usage'},
            {'key': 'undo_stats',   'sql': """
                SELECT TO_CHAR(begin_time, 'YYYY-MM-DD HH24:MI') AS begin_time,
                       TO_CHAR(end_time, 'YYYY-MM-DD HH24:MI') AS end_time,
                       undoblks, txncount, maxquerylen
                FROM v$undostat
                WHERE begin_time > SYSDATE - 1
                ORDER BY begin_time;
            """,
             'desc_zh': 'UNDO 统计（1h）',      'desc_en': 'UNDO statistics (1h)'},
        ]
    },
    {
        'chapter_number': 11,
        'chapter_title_zh': 'AWR 快照状态',
        'chapter_title_en': 'AWR Snapshot Status',
        'description': 'AWR 快照和基线状态（需 Diagnostic+Tuning Pack 授权）',
        'queries': [
            {'key': 'awr_snap', 'sql': """
                SELECT snap_id, dbid, instance_number, begin_interval_time, end_interval_time,
                       snap_level, error_count
                FROM (SELECT snap_id, dbid, instance_number, begin_interval_time, end_interval_time,
                              snap_level, error_count,
                              ROW_NUMBER() OVER (ORDER BY snap_id DESC) AS rn
                       FROM dba_hist_snapshot)
                WHERE rn <= 20;
            """,
             'desc_zh': 'AWR 快照列表',       'desc_en': 'AWR snapshot list'},
            {'key': 'awr_baseline', 'sql': """
                SELECT baseline_id, baseline_name, start_snap_id, end_snap_id,
                       TO_CHAR(creation_time, 'YYYY-MM-DD HH24:MI') AS created
                FROM dba_hist_baseline
                ORDER BY baseline_id;
            """,
             'desc_zh': 'AWR 基线列表',       'desc_en': 'AWR baseline list'},
            {'key': 'awr_retention', 'sql': """
                SELECT retention, snap_interval, topnsql
                FROM dba_hist_wr_control;
            """,
             'desc_zh': 'AWR 保留策略',       'desc_en': 'AWR retention policy'},
        ]
    },
    {
        'chapter_number': 12,
        'chapter_title_zh': 'ADDM 发现',
        'chapter_title_en': 'ADDM Findings',
        'description': 'ADDM 自动发现的问题',
        'queries': [
            {'key': 'addm_findings', 'sql': """
                SELECT r.RANK, f.FINDING_ID, f.FINDING_NAME, f.IMPACT_TYPE,
                       f.IMPACT, r.BENEFIT_TYPE, r.BENEFIT, f.MESSAGE
                FROM dba_advisor_findings f
                INNER JOIN dba_advisor_recommendations r
                  ON f.TASK_ID = r.TASK_ID
                 AND f.EXECUTION_NAME = r.EXECUTION_NAME
                 AND f.FINDING_ID = r.FINDING_ID
                WHERE r.TASK_NAME = 'ADDM TASK'
                ORDER BY r.RANK, f.FINDING_ID;
            """,
             'desc_zh': '最新 ADDM 发现',      'desc_en': 'Latest ADDM findings'},
        ]
    },
    {
        'chapter_number': 13,
        'chapter_title_zh': 'TOP SQL 分析',
        'chapter_title_en': 'Top SQL Analysis',
        'description': '按资源消耗排序的 TOP SQL',
        'queries': [
            {'key': 'top_sql_elapsed', 'sql': """
                SELECT sql_id, executions, elapsed_time/1000000 AS elapsed_sec,
                       cpu_time/1000000 AS cpu_sec, disk_reads, buffer_gets,
                       SUBSTR(sql_text, 1, 100) AS sql_preview
                FROM (SELECT sql_id, executions, elapsed_time, cpu_time, disk_reads, buffer_gets, sql_text,
                              ROW_NUMBER() OVER (ORDER BY elapsed_time DESC) AS rn
                       FROM v$sqlarea WHERE executions > 0)
                WHERE rn <= 10;
            """,
             'desc_zh': 'TOP SQL（耗时）',       'desc_en': 'Top SQL by elapsed time'},
            {'key': 'top_sql_cpu', 'sql': """
                SELECT sql_id, executions, cpu_time/1000000 AS cpu_sec,
                       elapsed_time/1000000 AS elapsed_sec, disk_reads,
                       SUBSTR(sql_text, 1, 100) AS sql_preview
                FROM (SELECT sql_id, executions, cpu_time, elapsed_time, disk_reads, sql_text,
                              ROW_NUMBER() OVER (ORDER BY cpu_time DESC) AS rn
                       FROM v$sqlarea WHERE executions > 0)
                WHERE rn <= 10;
            """,
             'desc_zh': 'TOP SQL（CPU）',        'desc_en': 'Top SQL by CPU time'},
            {'key': 'top_sql_reads', 'sql': """
                SELECT sql_id, executions, disk_reads,
                       elapsed_time/1000000 AS elapsed_sec,
                       SUBSTR(sql_text, 1, 100) AS sql_preview
                FROM (SELECT sql_id, executions, disk_reads, elapsed_time, sql_text,
                              ROW_NUMBER() OVER (ORDER BY disk_reads DESC) AS rn
                       FROM v$sqlarea WHERE executions > 0)
                WHERE rn <= 10;
            """,
             'desc_zh': 'TOP SQL（物理读）',     'desc_en': 'Top SQL by disk reads'},
        ]
    },
    {
        'chapter_number': 14,
        'chapter_title_zh': '表统计信息状态',
        'chapter_title_en': 'Table Statistics Status',
        'description': '表统计信息新鲜度和质量',
        'queries': [
            {'key': 'stale_tables', 'sql': """
                SELECT owner, table_name, num_rows, last_analyzed,
                       stale_stats
                FROM dba_tab_statistics
                WHERE stale_stats = 'YES'
                ORDER BY owner, table_name;
            """,
             'desc_zh': '统计信息过期的表',    'desc_en': 'Tables with stale statistics'},
            {'key': 'no_stats_tables', 'sql': """
                SELECT owner, table_name, num_rows, last_analyzed
                FROM dba_tables
                WHERE last_analyzed IS NULL
                  AND owner NOT IN ('SYS','SYSTEM','OUTLN','DBSNMP')
                ORDER BY owner, table_name;
            """,
             'desc_zh': '从未收集统计的表',   'desc_en': 'Tables without statistics'},
        ]
    },
    {
        'chapter_number': 15,
        'chapter_title_zh': '索引统计信息状态',
        'chapter_title_en': 'Index Statistics Status',
        'description': '索引统计信息新鲜度和质量',
        'queries': [
            {'key': 'stale_indexes', 'sql': """
                SELECT owner, index_name, table_name, num_rows, last_analyzed,
                       stale_stats
                FROM dba_ind_statistics
                WHERE stale_stats = 'YES'
                ORDER BY owner, index_name;
            """,
             'desc_zh': '统计信息过期的索引',  'desc_en': 'Indexes with stale statistics'},
            {'key': 'unused_indexes', 'sql': """
                SELECT d.OWNER, d.OBJECT_NAME AS INDEX_NAME, d.OBJECT_TYPE, s.VALUE AS LOGICAL_READS
                FROM v$segment_statistics s
                JOIN dba_objects d ON s.OBJ# = d.OBJECT_ID
                WHERE s.STATISTIC_NAME = 'logical reads'
                  AND d.OBJECT_TYPE = 'INDEX'
                  AND d.OWNER NOT IN ('SYS', 'SYSTEM')
                  AND TO_NUMBER(s.VALUE) = 0
                ORDER BY d.OWNER, d.OBJECT_NAME;
            """,
             'desc_zh': '未使用的索引',        'desc_en': 'Unused indexes'},
        ]
    },
    {
        'chapter_number': 16,
        'chapter_title_zh': '无效对象',
        'chapter_title_en': 'Invalid Objects',
        'description': '无效或破损的数据库对象',
        'queries': [
            {'key': 'invalid_objects', 'sql': """
                SELECT owner, object_type, object_name, status, last_ddl_time
                FROM dba_objects
                WHERE status != 'VALID'
                ORDER BY owner, object_type, object_name;
            """,
             'desc_zh': '无效对象列表',         'desc_en': 'Invalid objects list'},
            {'key': 'broken_triggers', 'sql': """
                SELECT owner, trigger_name, table_name, status
                FROM dba_triggers
                WHERE status = 'DISABLED'
                ORDER BY owner, trigger_name;
            """,
             'desc_zh': '禁用/破损的触发器',   'desc_en': 'Disabled/broken triggers'},
        ]
    },
    {
        'chapter_number': 17,
        'chapter_title_zh': '数据库链接',
        'chapter_title_en': 'Database Links',
        'description': '数据库链接状态',
        'queries': [
            {'key': 'db_links', 'sql': """
                SELECT owner, db_link, username, host, created
                FROM dba_db_links
                ORDER BY owner, db_link;
            """,
             'desc_zh': '数据库链接列表',         'desc_en': 'Database links list'},
        ]
    },
    {
        'chapter_number': 18,
        'chapter_title_zh': '作业/调度器状态',
        'chapter_title_en': 'Jobs & Scheduler Status',
        'description': '数据库作业和调度器状态',
        'queries': [
            {'key': 'dba_jobs', 'sql': """
                SELECT job, log_user, priv_user, last_date, last_sec, next_date, next_sec,
                       broken, failures
                FROM dba_jobs
                ORDER BY job;
            """,
             'desc_zh': '传统作业状态',         'desc_en': 'Legacy jobs status'},
            {'key': 'scheduler_jobs', 'sql': """
                SELECT owner, job_name, enabled, state, last_start_date, next_run_date
                FROM dba_scheduler_jobs
                WHERE owner NOT IN ('SYS','SYSTEM')
                ORDER BY owner, job_name;
            """,
             'desc_zh': '调度器作业状态',        'desc_en': 'Scheduler jobs status'},
        ]
    },
    {
        'chapter_number': 19,
        'chapter_title_zh': '用户与安全审计',
        'chapter_title_en': 'User & Security Audit',
        'description': '用户、权限和安全审计',
        'queries': [
            {'key': 'dba_users', 'sql': """
                SELECT username, account_status, lock_date, expiry_date,
                       created, profile, authentication_type
                FROM dba_users
                WHERE username NOT IN ('SYS','SYSTEM','OUTLN','DBSNMP','APPQOSSYS','GSMADMIN_INTERNAL')
                ORDER BY username;
            """,
             'desc_zh': '用户账号状态',         'desc_en': 'User account status'},
            {'key': 'privileged_users', 'sql': """
                SELECT grantee, granted_role, admin_option
                FROM dba_role_privs
                WHERE granted_role = 'DBA'
                ORDER BY grantee;
            """,
             'desc_zh': 'DBA 权限用户',        'desc_en': 'DBA privileged users'},
            {'key': 'password_policy', 'sql': """
                SELECT profile, resource_name, resource_type, limit
                FROM dba_profiles
                WHERE resource_type = 'PASSWORD'
                ORDER BY profile, resource_name;
            """,
             'desc_zh': '密码策略配置',         'desc_en': 'Password policy config'},
        ]
    },
    {
        'chapter_number': 20,
        'chapter_title_zh': '补丁级别',
        'chapter_title_en': 'Patch Level',
        'description': '数据库补丁和 PSU 级别',
        'queries': [
            {'key': 'opatch_history', 'sql': """
                SELECT action_time, action, namespace, version, id, comments
                FROM registry$history
                ORDER BY action_time DESC;
            """,
             'desc_zh': '已安装补丁列表',        'desc_en': 'Installed patches list'},
            {'key': 'registry', 'sql': """
                SELECT comp_id, comp_name, version, status, modified
                FROM dba_registry
                ORDER BY comp_id;
            """,
             'desc_zh': '数据库组件注册表',      'desc_en': 'Database component registry'},
        ]
    },
    {
        'chapter_number': 21,
        'chapter_title_zh': 'Data Guard 状态',
        'chapter_title_en': 'Data Guard Status',
        'description': 'Data Guard 主备同步状态',
        'queries': [
            {'key': 'dg_database', 'sql': """
                SELECT name, db_unique_name, database_role, protection_mode,
                       protection_level, switchover_status, dataguard_broker
                FROM v$database;
            """,
             'desc_zh': 'Data Guard 数据库角色', 'desc_en': 'Data Guard database role'},
            {'key': 'dg_archive_dest', 'sql': """
                SELECT DEST_ID, DEST_NAME, STATUS, TARGET, SCHEDULE, DESTINATION, ERROR
                FROM v$archive_dest
                WHERE DEST_ID < 3
                ORDER BY DEST_ID;
            """,
             'desc_zh': '归档目标同步状态',      'desc_en': 'Archive destination sync status'},
            {'key': 'dg_stats', 'sql': """
                SELECT name, value, unit, time_computed
                FROM v$dataguard_stats;
            """,
             'desc_zh': 'Data Guard 统计信息',  'desc_en': 'Data Guard statistics'},
        ]
    },
]
# 临时文件：SQL Server 21 章配置

SQLSERVER_DEFAULT_CHAPTERS = [
    {
        'chapter_number': 1,
        'chapter_title_zh': '健康状态概览',
        'chapter_title_en': 'Health Overview',
        'description': '数据库整体健康状态概览',
        'queries': [
            {'key': 'sqlserver_version', 'sql': "SELECT @@VERSION AS version;",
             'desc_zh': '获取 SQL Server 版本', 'desc_en': 'Get SQL Server version'},
            {'key': 'server_uptime', 'sql': """
                SELECT sqlserver_start_time AS start_time,
                       DATEDIFF(SECOND, sqlserver_start_time, CURRENT_TIMESTAMP) AS uptime_sec
                FROM sys.dm_os_sys_info;
            """,
             'desc_zh': '数据库运行时长', 'desc_en': 'Database uptime'},
        ]
    },
    {
        'chapter_number': 2,
        'chapter_title_zh': '连接状态检查',
        'chapter_title_en': 'Connection Status',
        'description': '数据库连接和会话检查',
        'queries': [
            {'key': 'connection_count', 'sql': "SELECT COUNT(*) AS total_connections, SUM(CASE WHEN is_user_process = 1 THEN 1 ELSE 0 END) AS user_connections FROM sys.dm_exec_sessions;",
             'desc_zh': '连接数统计', 'desc_en': 'Connection count'},
            {'key': 'session_details', 'sql': "SELECT session_id, login_name, host_name, status, cpu_time, memory_usage, total_elapsed_time FROM sys.dm_exec_sessions WHERE is_user_process = 1 ORDER BY login_name;",
             'desc_zh': '用户会话详情', 'desc_en': 'User session details'},
        ]
    },
    {
        'chapter_number': 3,
        'chapter_title_zh': '数据库空间使用',
        'chapter_title_en': 'Database Space Usage',
        'description': '数据库文件空间使用',
        'queries': [
            {'key': 'db_size', 'sql': """
                SELECT database_id, name AS database_name,
                       ROUND(SUM(size) * 8.0 / 1024, 2) AS total_mb,
                       ROUND(SUM(CASE WHEN type = 0 THEN size * 8.0 / 1024 ELSE 0 END), 2) AS data_mb,
                       ROUND(SUM(CASE WHEN type = 1 THEN size * 8.0 / 1024 ELSE 0 END), 2) AS log_mb
                FROM sys.master_files
                GROUP BY database_id, name
                ORDER BY total_mb DESC;
            """,
             'desc_zh': '各数据库文件大小', 'desc_en': 'Database file sizes'},
        ]
    },
    {
        'chapter_number': 4,
        'chapter_title_zh': '性能分析',
        'chapter_title_en': 'Performance Analysis',
        'description': '等待统计和性能计数器',
        'queries': [
            {'key': 'wait_stats', 'sql': """
                SELECT wait_type, waiting_tasks_count, wait_time_ms, max_wait_time_ms
                FROM sys.dm_os_wait_stats
                WHERE wait_time_ms > 0
                ORDER BY wait_time_ms DESC;
            """,
             'desc_zh': '等待类型统计', 'desc_en': 'Wait type statistics'},
            {'key': 'perf_counters', 'sql': """
                SELECT object_name, counter_name, cntr_value, cntr_type
                FROM sys.dm_os_performance_counters
                WHERE counter_name IN ('Batch Requests/sec','SQL Compilations/sec','SQL Recompilations/sec',
                                       'Buffer cache hit ratio','Page life expectancy')
                ORDER BY object_name, counter_name;
            """,
             'desc_zh': '关键性能计数器', 'desc_en': 'Key performance counters'},
        ]
    },
    {
        'chapter_number': 5,
        'chapter_title_zh': '缓冲池状态',
        'chapter_title_en': 'Buffer Pool Status',
        'description': '缓冲池和内存使用',
        'queries': [
            {'key': 'buffer_pool_status', 'sql': """
                SELECT counter_name, cntr_value AS pages,
                       cntr_value * 8 / 1024 AS size_mb
                FROM sys.dm_os_performance_counters
                WHERE counter_name IN ('Total pages','Database pages','Free pages',
                                       'Stolen pages','Reserved pages')
                  AND object_name LIKE '%Buffer Manager%'
                ORDER BY counter_name;
            """,
             'desc_zh': '缓冲池页面统计', 'desc_en': 'Buffer pool page stats'},
            {'key': 'memory_grant', 'sql': """
                SELECT counter_name, cntr_value
                FROM sys.dm_os_performance_counters
                WHERE counter_name IN ('Memory Grants Pending','Memory Grants Outstanding')
                ORDER BY counter_name;
            """,
             'desc_zh': '内存授权状态', 'desc_en': 'Memory grant status'},
        ]
    },
    {
        'chapter_number': 6,
        'chapter_title_zh': '锁和阻塞分析',
        'chapter_title_en': 'Lock & Blocking Analysis',
        'description': '锁等待和阻塞会话',
        'queries': [
            {'key': 'lock_waits', 'sql': """
                SELECT request_session_id AS blocked_spid, blocking_session_id AS blocking_spid,
                       wait_type, wait_time, transaction_id
                FROM sys.dm_os_waiting_tasks
                WHERE blocking_session_id IS NOT NULL
                ORDER BY wait_time DESC;
            """,
             'desc_zh': '锁等待列表', 'desc_en': 'Lock wait list'},
            {'key': 'blocking_chain', 'sql': """
                SELECT session_id, blocking_session_id, wait_type, wait_time, last_wait_type
                FROM sys.dm_exec_sessions
                WHERE blocking_session_id IS NOT NULL
                ORDER BY blocking_session_id;
            """,
             'desc_zh': '阻塞链', 'desc_en': 'Blocking chain'},
        ]
    },
    {
        'chapter_number': 7,
        'chapter_title_zh': '索引使用率分析',
        'chapter_title_en': 'Index Usage Analysis',
        'description': '索引使用率和碎片率',
        'queries': [
            {'key': 'unused_indexes', 'sql': """
                SELECT OBJECT_NAME(i.object_id) AS table_name, i.name AS index_name,
                       ius.user_seeks, ius.user_scans, ius.user_lookups, ius.user_updates
                FROM sys.indexes i
                LEFT JOIN sys.dm_db_index_usage_stats ius ON i.object_id = ius.object_id AND i.index_id = ius.index_id
                WHERE i.type > 0
                  AND ius.user_seeks + ius.user_scans + ius.user_lookups = 0
                ORDER BY OBJECT_NAME(i.object_id);
            """,
             'desc_zh': '从未使用的索引', 'desc_en': 'Unused indexes'},
            {'key': 'index_fragmentation', 'sql': """
                SELECT OBJECT_NAME(ips.object_id) AS table_name, i.name AS index_name,
                       ips.avg_fragmentation_in_percent, ips.page_count
                FROM sys.dm_db_index_physical_stats(DB_ID(), NULL, NULL, NULL, 'LIMITED') ips
                JOIN sys.indexes i ON ips.object_id = i.object_id AND ips.index_id = i.index_id
                WHERE ips.avg_fragmentation_in_percent > 30
                ORDER BY ips.avg_fragmentation_in_percent DESC;
            """,
             'desc_zh': '索引碎片率 >30%', 'desc_en': 'Index fragmentation >30%'},
        ]
    },
    {
        'chapter_number': 8,
        'chapter_title_zh': '查询性能分析',
        'chapter_title_en': 'Query Performance Analysis',
        'description': '最耗资源的查询',
        'queries': [
            {'key': 'top_cpu_queries', 'sql': """
                SELECT TOP 10 qs.total_worker_time / 1000000.0 AS total_cpu_sec,
                       qs.total_elapsed_time / 1000000.0 AS total_duration_sec,
                       qs.execution_count, qs.total_logical_reads,
                       SUBSTRING(qt.text, (qs.statement_start_offset/2)+1,
                               ((CASE qs.statement_end_offset
                                 WHEN -1 THEN DATALENGTH(qt.text)
                                 ELSE qs.statement_end_offset END
                                 - qs.statement_start_offset)/2) + 1) AS query_text
                FROM sys.dm_exec_query_stats qs
                CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) AS qt
                ORDER BY qs.total_worker_time DESC;
            """,
             'desc_zh': 'TOP 查询（CPU）', 'desc_en': 'Top queries by CPU'},
            {'key': 'top_io_queries', 'sql': """
                SELECT TOP 10 qs.total_logical_reads AS logical_reads,
                       qs.total_physical_reads AS physical_reads,
                       qs.execution_count, qs.total_elapsed_time / 1000000.0 AS total_duration_sec,
                       SUBSTRING(qt.text, (qs.statement_start_offset/2)+1,
                               ((CASE qs.statement_end_offset
                                 WHEN -1 THEN DATALENGTH(qt.text)
                                 ELSE qs.statement_end_offset END
                                 - qs.statement_start_offset)/2) + 1) AS query_text
                FROM sys.dm_exec_query_stats qs
                CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) AS qt
                ORDER BY qs.total_logicial_reads DESC;
            """,
             'desc_zh': 'TOP 查询（逻辑读）', 'desc_en': 'Top queries by logical reads'},
        ]
    },
    {
        'chapter_number': 9,
        'chapter_title_zh': '数据库文件状态',
        'chapter_title_en': 'Database File Status',
        'description': '数据文件和日志文件状态',
        'queries': [
            {'key': 'datafile_status', 'sql': """
                SELECT database_id, file_id, type_desc, name AS file_name,
                       physical_name, size * 8 / 1024 AS size_mb,
                       max_size * 8 / 1024 AS max_size_mb, growth * 8 / 1024 AS growth_mb
                FROM sys.master_files
                ORDER BY database_id, file_id;
            """,
             'desc_zh': '数据库文件列表', 'desc_en': 'Database file list'},
            {'key': 'log_file_status', 'sql': """
                SELECT database_id, file_id, name AS file_name,
                       size * 8 / 1024 AS size_mb, max_size, growth
                FROM sys.master_files
                WHERE type = 1
                ORDER BY database_id;
            """,
             'desc_zh': '日志文件状态', 'desc_en': 'Log file status'},
        ]
    },
    {
        'chapter_number': 10,
        'chapter_title_zh': '事务日志分析',
        'chapter_title_en': 'Transaction Log Analysis',
        'description': '事务日志使用和 VLF 状态',
        'queries': [
            {'key': 'log_space_usage', 'sql': """
                SELECT name AS database_name, log_reuse_wait, log_reuse_wait_desc,
                       recovery_model_desc, log_size_mb, log_space_used_mb
                FROM sys.databases
                ORDER BY name;
            """,
             'desc_zh': '日志重用等待状态', 'desc_en': 'Log reuse wait status'},
        ]
    },
    {
        'chapter_number': 11,
        'chapter_title_zh': '错误日志检查',
        'chapter_title_en': 'Error Log Check',
        'description': 'SQL Server 错误日志',
        'queries': [
            {'key': 'error_log_config', 'sql': """
                SELECT server_name, is_enabled, is_logged
                FROM sys.dm_os_server_diagnostics_log_settings;
            """,
             'desc_zh': '错误日志配置', 'desc_en': 'Error log configuration'},
        ]
    },
    {
        'chapter_number': 12,
        'chapter_title_zh': '作业/代理状态',
        'chapter_title_en': 'Jobs & Agent Status',
        'description': 'SQL Agent 作业状态',
        'queries': [
            {'key': 'agent_jobs', 'sql': """
                SELECT job_id, name AS job_name, enabled, description
                FROM msdb.dbo.sysjobs
                ORDER BY name;
            """,
             'desc_zh': '作业列表', 'desc_en': 'Job list'},
            {'key': 'job_history', 'sql': """
                SELECT TOP 20 j.name AS job_name, h.step_name, h.run_date, h.run_time,
                       h.run_status, h.message
                FROM msdb.dbo.sysjobhistory h
                JOIN msdb.dbo.sysjobs j ON h.job_id = j.job_id
                ORDER BY h.run_date DESC, h.run_time DESC;
            """,
             'desc_zh': '作业执行历史（最近20）', 'desc_en': 'Recent job history (top 20)'},
        ]
    },
    {
        'chapter_number': 13,
        'chapter_title_zh': '复制状态检查',
        'chapter_title_en': 'Replication Status',
        'description': '事务复制/合并复制状态',
        'queries': [
            {'key': 'repl_pubulations', 'sql': """
                SELECT publisher_db, publication, publication_type, status
                FROM distribution.dbo.MSpublications;
            """,
             'desc_zh': '发布列表', 'desc_en': 'Publication list'},
        ]
    },
    {
        'chapter_number': 14,
        'chapter_title_zh': '用户权限审计',
        'chapter_title_en': 'User Privilege Audit',
        'description': '登录账号和权限审计',
        'queries': [
            {'key': 'sql_logins', 'sql': """
                SELECT name AS login_name, is_disabled, create_date, modify_date,
                       LOGINPROPERTY(name, 'PasswordLastSetTime') AS pwd_last_set
                FROM sys.server_principals
                WHERE type IN ('S', 'U', 'G')
                ORDER BY name;
            """,
             'desc_zh': '登录账号列表', 'desc_en': 'SQL login list'},
            {'key': 'server_role_members', 'sql': """
                SELECT sp.name AS login_name, rp.name AS role_name
                FROM sys.server_role_members rm
                JOIN sys.server_principals sp ON rm.member_principal_id = sp.principal_id
                JOIN sys.server_principals rp ON rm.role_principal_id = rp.principal_id
                ORDER BY rp.name, sp.name;
            """,
             'desc_zh': '服务器角色成员', 'desc_en': 'Server role members'},
        ]
    },
    {
        'chapter_number': 15,
        'chapter_title_zh': '备份状态检查',
        'chapter_title_en': 'Backup Status',
        'description': '数据库备份历史',
        'queries': [
            {'key': 'backup_history', 'sql': """
                SELECT TOP 20 database_name, backup_start_date, backup_finish_date,
                       type AS backup_type, physical_device_name
                FROM msdb.dbo.backupset bs
                JOIN msdb.dbo.backupmediafamily bf ON bs.media_set_id = bf.media_set_id
                ORDER BY backup_start_date DESC;
            """,
             'desc_zh': '备份历史（最近20）', 'desc_en': 'Recent backup history (top 20)'},
            {'key': 'db_recovery_model', 'sql': """
                SELECT name AS database_name, recovery_model_desc, log_reuse_wait_desc
                FROM sys.databases
                ORDER BY name;
            """,
             'desc_zh': '恢复模式和日志状态', 'desc_en': 'Recovery model and log status'},
        ]
    },
    {
        'chapter_number': 16,
        'chapter_title_zh': '系统配置检查',
        'chapter_title_en': 'System Configuration Check',
        'description': '关键系统配置参数',
        'queries': [
            {'key': 'server_config', 'sql': """
                SELECT name, value, value_in_use, description
                FROM sys.configurations
                WHERE name IN ('max server memory','min server memory',
                               'max degree of parallelism','cost threshold for parallelism',
                               'optimize for ad hoc workloads','backup compression default')
                ORDER BY name;
            """,
             'desc_zh': '关键服务器配置', 'desc_en': 'Key server configurations'},
        ]
    },
    {
        'chapter_number': 17,
        'chapter_title_zh': 'TempDB 状态',
        'chapter_title_en': 'TempDB Status',
        'description': 'TempDB 文件使用和争用',
        'queries': [
            {'key': 'tempdb_size', 'sql': """
                SELECT name AS file_name, physical_name,
                       size * 8 / 1024 AS size_mb, max_size
                FROM sys.master_files
                WHERE database_id = 2
                ORDER BY file_id;
            """,
             'desc_zh': 'TempDB 文件大小', 'desc_en': 'TempDB file sizes'},
            {'key': 'tempdb_contention', 'sql': """
                SELECT session_id, wait_type, wait_time, resource_description
                FROM sys.dm_os_waiting_tasks
                WHERE wait_type LIKE '%PAGE%' OR wait_type LIKE '%LATCH%'
                ORDER BY wait_time DESC;
            """,
             'desc_zh': 'TempDB 争用等待', 'desc_en': 'TempDB contention waits'},
        ]
    },
    {
        'chapter_number': 18,
        'chapter_title_zh': '数据库一致性检查',
        'chapter_title_en': 'Database Consistency Check',
        'description': 'DBCC 检查结果',
        'queries': [
            {'key': 'dbcc_history', 'sql': """
                SELECT TOP 10 database_name, check_date, error_number, error_message
                FROM msdb.dbo.suspect_pages
                ORDER BY check_date DESC;
            """,
             'desc_zh': '可疑页记录（最近10）', 'desc_en': 'Suspect page records (top 10)'},
        ]
    },
    {
        'chapter_number': 19,
        'chapter_title_zh': '可用性组状态',
        'chapter_title_en': 'Availability Group Status',
        'description': 'AlwaysOn 可用性组状态',
        'queries': [
            {'key': 'ag_status', 'sql': """
                SELECT ag.name AS ag_name, ar.replica_server_name, ar.availability_mode_desc,
                       ar.failover_mode_desc, ars.connected_state_desc, ars.operational_state_desc
                FROM sys.availability_groups ag
                JOIN sys.availability_replicas ar ON ag.group_id = ar.group_id
                JOIN sys.dm_hadr_availability_replica_states ars ON ar.replica_id = ars.replica_id
                ORDER BY ag.name, ar.replica_server_name;
            """,
             'desc_zh': '可用性组状态', 'desc_en': 'Availability group status'},
        ]
    },
    {
        'chapter_number': 20,
        'chapter_title_zh': '资源调控器状态',
        'chapter_title_en': 'Resource Governor Status',
        'description': '资源池和工作负荷组状态',
        'queries': [
            {'key': 'resource_pools', 'sql': """
                SELECT pool_id, name AS pool_name, min_cpu_percent, max_cpu_percent,
                       min_memory_percent, max_memory_percent
                FROM sys.resource_governor_resource_pools
                ORDER BY pool_id;
            """,
             'desc_zh': '资源池配置', 'desc_en': 'Resource pool configuration'},
        ]
    },
    {
        'chapter_number': 21,
        'chapter_title_zh': '扩展事件和跟踪',
        'chapter_title_en': 'Extended Events & Traces',
        'description': '扩展事件会话和 SQL Trace',
        'queries': [
            {'key': 'xe_sessions', 'sql': """
                SELECT name AS session_name, create_time, state, total_target_memory
                FROM sys.dm_xe_sessions
                ORDER BY name;
            """,
             'desc_zh': '扩展事件会话', 'desc_en': 'Extended events sessions'},
            {'key': 'trace_status', 'sql': """
                SELECT id AS trace_id, status, path, max_size, start_time, stop_time
                FROM sys.traces
                ORDER BY id;
            """,
             'desc_zh': 'SQL Trace 状态', 'desc_en': 'SQL Trace status'},
        ]
    },
]
# 临时文件：DM8 达梦 21 章配置

DM8_DEFAULT_CHAPTERS = [
    {
        'chapter_number': 1,
        'chapter_title_zh': '健康状态概览',
        'chapter_title_en': 'Health Overview',
        'description': '数据库整体健康状态概览',
        'queries': [
            {'key': 'dm_version', 'sql': "SELECT * FROM V$VERSION;",
             'desc_zh': '获取 DM8 版本', 'desc_en': 'Get DM8 version'},
            {'key': 'dm_status', 'sql': "SELECT * FROM V$INSTANCE;",
             'desc_zh': '实例状态', 'desc_en': 'Instance status'},
        ]
    },
    {
        'chapter_number': 2,
        'chapter_title_zh': '数据库空间使用',
        'chapter_title_en': 'Database Space Usage',
        'description': '表空间和数据文件使用情况',
        'queries': [
            {'key': 'tablespace_usage', 'sql': """
                SELECT T.TABLESPACE_NAME, T.STATUS AS TS_STATUS,
                       D.FILE_NAME, D.BYTES/1024/1024 AS FILE_SIZE_MB,
                       D.STATUS AS FILE_STATUS
                FROM DBA_TABLESPACES T
                LEFT JOIN DBA_DATA_FILES D ON T.TABLESPACE_NAME = D.TABLESPACE_NAME
                ORDER BY T.TABLESPACE_NAME;
            """,
             'desc_zh': '表空间使用率', 'desc_en': 'Tablespace usage rate'},
        ]
    },
    {
        'chapter_number': 3,
        'chapter_title_zh': '会话与连接检查',
        'chapter_title_en': 'Session & Connection Check',
        'description': '会话数和连接状态检查',
        'queries': [
            {'key': 'session_count', 'sql': "SELECT COUNT(*) AS session_count FROM V$SESSIONS;",
             'desc_zh': '用户会话数', 'desc_en': 'User session count'},
            {'key': 'session_list', 'sql': "SELECT SESS_ID, USER_NAME, CLNT_IP, STATE, CREATE_TIME FROM V$SESSIONS ORDER BY CREATE_TIME DESC LIMIT 50;",
             'desc_zh': '用户会话列表', 'desc_en': 'User session list'},
        ]
    },
    {
        'chapter_number': 4,
        'chapter_title_zh': '配置参数检查',
        'chapter_title_en': 'Configuration Check',
        'description': '非只读参数和关键配置检查',
        'queries': [
            {'key': 'dm_params', 'sql': """
                SELECT NAME, TYPE, VALUE, SYS_VALUE
                FROM V$PARAMETER
                WHERE TYPE != 'R'
                ORDER BY NAME;
            """,
             'desc_zh': '非只读参数一览', 'desc_en': 'Non-readonly parameters'},
        ]
    },
    {
        'chapter_number': 5,
        'chapter_title_zh': '归档日志状态',
        'chapter_title_en': 'Archive Log Status',
        'description': '归档日志开启状态和最近归档情况',
        'queries': [
            {'key': 'archive_status', 'sql': """
                SELECT NAME, VALUE
                FROM V$PARAMETER
                WHERE NAME IN ('ARCH_MODE', 'ARCH_FILE_SIZE', 'ARCH_SPACE_LIMIT')
            """,
             'desc_zh': '归档配置参数', 'desc_en': 'Archive configuration parameters'},
        ]
    },
    {
        'chapter_number': 6,
        'chapter_title_zh': '重做日志状态',
        'chapter_title_en': 'Redo Log Status',
        'description': '重做日志组和成员状态',
        'queries': [
            {'key': 'log_group', 'sql': "SELECT * FROM V$RLOG;",
             'desc_zh': '日志组状态', 'desc_en': 'Log group status'},
            {'key': 'log_file', 'sql': "SELECT * FROM V$RLOGFILE ORDER BY GROUP_ID;",
             'desc_zh': '日志文件成员', 'desc_en': 'Log file members'},
        ]
    },
    {
        'chapter_number': 7,
        'chapter_title_zh': '锁等待检查',
        'chapter_title_en': 'Lock & Wait Analysis',
        'description': '锁等待和长事务检查',
        'queries': [
            {'key': 'lock_wait', 'sql': "SELECT * FROM V$LOCK ORDER BY TABLE_ID;",
             'desc_zh': '锁等待会话', 'desc_en': 'Lock waiting sessions'},
            {'key': 'long_trx', 'sql': "SELECT trx.ID AS trx_id, trx.SESS_ID, trx.STATUS, sess.USER_NAME, sess.CLNT_IP, DATEDIFF(SS, sess.CREATE_TIME, SYSDATE) AS duration_sec FROM V$TRX trx JOIN V$SESSIONS sess ON trx.SESS_ID=sess.SESS_ID WHERE DATEDIFF(SS, sess.CREATE_TIME, SYSDATE) > 60 ORDER BY duration_sec DESC;",
             'desc_zh': '运行超过 60 秒的长事务', 'desc_en': 'Long transactions > 60s'},
        ]
    },
    {
        'chapter_number': 8,
        'chapter_title_zh': '缓冲池状态',
        'chapter_title_en': 'Buffer Pool Status',
        'description': '数据缓冲池使用状态',
        'queries': [
            {'key': 'buffer_pool', 'sql': "SELECT * FROM V$BUFFERPOOL;",
             'desc_zh': '缓冲池状态', 'desc_en': 'Buffer pool status'},
        ]
    },
    {
        'chapter_number': 9,
        'chapter_title_zh': '表与索引分析',
        'chapter_title_en': 'Table & Index Analysis',
        'description': '表大小和索引状态',
        'queries': [
            {'key': 'table_size', 'sql': """
                SELECT OWNER, TABLE_NAME, TABLESPACE_NAME,
                       NUM_ROWS, BLOCKS, LAST_ANALYZED, STATUS
                FROM DBA_TABLES
                WHERE OWNER NOT IN ('SYS','SYSTEM')
                ORDER BY NVL(NUM_ROWS, 0) DESC
                LIMIT 20;
            """,
             'desc_zh': '最大的 20 张表', 'desc_en': 'Top 20 largest tables'},
            {'key': 'index_status', 'sql': """
                SELECT OWNER, INDEX_NAME, INDEX_TYPE, TABLE_OWNER, TABLE_NAME,
                       UNIQUENESS, STATUS, NUM_ROWS, LAST_ANALYZED
                FROM DBA_INDEXES
                WHERE OWNER NOT IN ('SYS','SYSTEM')
                ORDER BY OWNER, TABLE_NAME;
            """,
             'desc_zh': '索引状态列表', 'desc_en': 'Index status list'},
        ]
    },
    {
        'chapter_number': 10,
        'chapter_title_zh': '用户与权限审计',
        'chapter_title_en': 'User & Privilege Audit',
        'description': '用户权限和安全审计',
        'queries': [
            {'key': 'dba_users', 'sql': """
                SELECT USERNAME, ACCOUNT_STATUS, LOCK_DATE, EXPIRY_DATE, CREATED, PROFILE
                FROM DBA_USERS
                WHERE USERNAME NOT IN ('SYS','SYSTEM')
                ORDER BY USERNAME;
            """,
             'desc_zh': '用户账号状态', 'desc_en': 'User account status'},
            {'key': 'dba_role_privs', 'sql': """
                SELECT * FROM DBA_ROLE_PRIVS
                WHERE GRANTED_ROLE = 'DBA'
                ORDER BY GRANTEE;
            """,
             'desc_zh': 'DBA 权限用户', 'desc_en': 'DBA privileged users'},
        ]
    },
    {
        'chapter_number': 11,
        'chapter_title_zh': '无效对象和依赖',
        'chapter_title_en': 'Invalid Objects & Dependencies',
        'description': '无效或破损的数据库对象',
        'queries': [
            {'key': 'invalid_objects', 'sql': """
                SELECT OWNER, OBJECT_NAME, OBJECT_TYPE, STATUS, LAST_DDL_TIME
                FROM DBA_OBJECTS
                WHERE STATUS != 'VALID'
                ORDER BY OWNER, OBJECT_TYPE, OBJECT_NAME;
            """,
             'desc_zh': '无效对象列表', 'desc_en': 'Invalid objects list'},
        ]
    },
    {
        'chapter_number': 12,
        'chapter_title_zh': '数据库链路状态',
        'chapter_title_en': 'Database Links',
        'description': '数据库链接状态',
        'queries': [
            {'key': 'db_links', 'sql': """
                SELECT OWNER, DB_LINK, USERNAME, HOST, CREATED
                FROM DBA_DB_LINKS
                ORDER BY OWNER, DB_LINK;
            """,
             'desc_zh': '数据库链接列表', 'desc_en': 'Database links list'},
        ]
    },
    {
        'chapter_number': 13,
        'chapter_title_zh': '作业状态检查',
        'chapter_title_en': 'Job Status',
        'description': '数据库作业状态',
        'queries': [
            {'key': 'dm_jobs', 'sql': "SELECT 'DM8 不支持 DBA_JOBS/DBA_SCHEDULER_JOBS 视图，请通过 DM 管理工具查看作业' AS message;",
             'desc_zh': '数据库作业状态列表', 'desc_en': 'Database job status list (not supported in DM8)'},
        ]
    },
    {
        'chapter_number': 14,
        'chapter_title_zh': 'SQL 执行统计',
        'chapter_title_en': 'SQL Execution Statistics',
        'description': 'SQL 语句执行统计',
        'queries': [
            {'key': 'top_sql_elapsed', 'sql': """
                SELECT * FROM V$SQL_HISTORY
                ORDER BY TIME_USED DESC
                LIMIT 10;
            """,
             'desc_zh': 'TOP SQL（耗时）', 'desc_en': 'Top SQL by elapsed time'},
            {'key': 'top_sql_cpu', 'sql': """
                SELECT * FROM V$SQL_HISTORY
                ORDER BY START_TIME DESC
                LIMIT 10;
            """,
             'desc_zh': 'TOP SQL（CPU）', 'desc_en': 'Top SQL by CPU time'},
        ]
    },
    {
        'chapter_number': 15,
        'chapter_title_zh': '表统计信息状态',
        'chapter_title_en': 'Table Statistics Status',
        'description': '表统计信息新鲜度',
        'queries': [
            {'key': 'stale_tables', 'sql': """
                SELECT OWNER AS TABLE_OWNER, TABLE_NAME, NUM_ROWS, LAST_ANALYZED
                FROM DBA_TABLES
                WHERE OWNER NOT IN ('SYS','SYSTEM')
                  AND (LAST_ANALYZED IS NULL OR LAST_ANALYZED < SYSDATE - 90)
                ORDER BY OWNER, TABLE_NAME
                LIMIT 30;
            """,
             'desc_zh': '超过90天未分析或从未分析的表', 'desc_en': 'Tables not analyzed in 90+ days'},
        ]
    },
    {
        'chapter_number': 16,
        'chapter_title_zh': '数据库文件状态',
        'chapter_title_en': 'Datafile Status',
        'description': '数据文件和临时文件状态',
        'queries': [
            {'key': 'datafile_status', 'sql': """
                SELECT FILE_ID, FILE_NAME, TABLESPACE_NAME,
                       ROUND(BYTES / 1024 / 1024, 2) AS size_mb,
                       STATUS
                FROM DBA_DATA_FILES
                ORDER BY TABLESPACE_NAME, FILE_ID;
            """,
             'desc_zh': '数据文件状态', 'desc_en': 'Data file status'},
        ]
    },
    {
        'chapter_number': 17,
        'chapter_title_zh': '错误日志检查',
        'chapter_title_en': 'Error Log Check',
        'description': '错误日志和告警信息',
        'queries': [
            {'key': 'alert_log', 'sql': """
                SELECT * FROM V$ERR_INFO
                LIMIT 20;
            """,
             'desc_zh': '告警日志最新 20 条', 'desc_en': 'Latest 20 alert log entries'},
        ]
    },
    {
        'chapter_number': 18,
        'chapter_title_zh': '数据库角色和权限',
        'chapter_title_en': 'Roles & Privileges',
        'description': '数据库角色和权限分配',
        'queries': [
            {'key': 'role_privs', 'sql': """
                SELECT GRANTEE, GRANTED_ROLE, ADMIN_OPTION, DEFAULT_ROLE
                FROM DBA_ROLE_PRIVS
                ORDER BY GRANTEE;
            """,
             'desc_zh': '角色权限分配', 'desc_en': 'Role privilege assignments'},
            {'key': 'sys_privs', 'sql': """
                SELECT GRANTEE, PRIVILEGE, ADMIN_OPTION
                FROM DBA_SYS_PRIVS
                WHERE GRANTEE NOT IN ('SYS','SYSTEM')
                ORDER BY GRANTEE, PRIVILEGE;
            """,
             'desc_zh': '系统权限分配', 'desc_en': 'System privilege assignments'},
        ]
    },
]

# 临时文件：TiDB 29 章配置（1-21 章完全参照 MySQL + 22-29 章 TiDB 特性）

TIDB_DEFAULT_CHAPTERS = [
    {
        'chapter_number': 1,
        'chapter_title_zh': '健康状态概览',
        'chapter_title_en': 'Health Overview',
        'description': '数据库整体健康状态概览',
        'queries': [
            {'key': 'my_version',   'sql': "SELECT VERSION() AS version;",
             'desc_zh': '获取 TiDB 版本',          'desc_en': 'Get TiDB version'},
            {'key': 'uptime',      'sql': "SHOW GLOBAL STATUS LIKE 'Uptime';",
             'desc_zh': '数据库运行时长(秒)', 'desc_en': 'Database uptime (seconds)'},
            {'key': 'datadir',     'sql': "SHOW VARIABLES LIKE 'datadir';",
             'desc_zh': '数据目录路径',            'desc_en': 'Data directory path'},
            {'key': 'server_uuid', 'sql': "SHOW VARIABLES LIKE 'server_uuid';",
             'desc_zh': '服务器 UUID',             'desc_en': 'Server UUID'},
        ]
    },
    {
        'chapter_number': 2,
        'chapter_title_zh': '连接状态检查',
        'chapter_title_en': 'Connection Status',
        'description': '数据库连接相关状态检查',
        'queries': [
            {'key': 'threads_connected',     'sql': "SHOW GLOBAL STATUS LIKE 'Threads_connected';",
             'desc_zh': '当前连接数',                'desc_en': 'Current connections'},
            {'key': 'max_used_connections', 'sql': "SHOW GLOBAL STATUS LIKE 'Max_used_connections';",
             'desc_zh': '历史最大连接数',           'desc_en': 'Max used connections'},
            {'key': 'max_connections',      'sql': "SHOW VARIABLES LIKE 'max_connections';",
             'desc_zh': '最大连接数配置',           'desc_en': 'Max connections config'},
            {'key': 'aborted_connects',    'sql': "SHOW GLOBAL STATUS LIKE 'Aborted_connects';",
             'desc_zh': '失败连接次数',              'desc_en': 'Aborted connection count'},
            {'key': 'connection_errors',    'sql': "SHOW GLOBAL STATUS LIKE 'Connection_errors%';",
             'desc_zh': '连接错误统计',              'desc_en': 'Connection error stats'},
            {'key': 'threads_running',      'sql': "SHOW GLOBAL STATUS LIKE 'Threads_running';",
             'desc_zh': '当前活跃连接数',           'desc_en': 'Running threads count'},
        ]
    },
    {
        'chapter_number': 3,
        'chapter_title_zh': '配置参数检查',
        'chapter_title_en': 'Configuration Check',
        'description': '关键配置参数检查',
        'queries': [
            {'key': 'innodb_buffer_pool_size',   'sql': "SHOW VARIABLES LIKE 'innodb_buffer_pool_size';",
             'desc_zh': 'InnoDB 缓冲池大小',     'desc_en': 'InnoDB buffer pool size'},
            {'key': 'innodb_log_file_size',      'sql': "SHOW VARIABLES LIKE 'innodb_log_file_size';",
             'desc_zh': 'Redo 日志文件大小',      'desc_en': 'Redo log file size'},
            {'key': 'innodb_flush_log_at_trx_commit', 'sql': "SHOW VARIABLES LIKE 'innodb_flush_log_at_trx_commit';",
             'desc_zh': '事务提交刷盘策略',         'desc_en': 'Transaction flush policy'},
            {'key': 'sync_binlog',        'sql': "SHOW VARIABLES LIKE 'sync_binlog';",
             'desc_zh': 'Binlog 刷盘策略',        'desc_en': 'Binlog sync policy'},
            {'key': 'log_bin',            'sql': "SHOW VARIABLES LIKE 'log_bin';",
             'desc_zh': 'Binlog 是否开启',        'desc_en': 'Binary logging enabled'},
            {'key': 'slow_query_log',    'sql': "SHOW VARIABLES LIKE 'slow_query_log';",
             'desc_zh': '慢查询日志是否开启',        'desc_en': 'Slow query log enabled'},
            {'key': 'long_query_time',    'sql': "SHOW VARIABLES LIKE 'long_query_time';",
             'desc_zh': '慢查询阈值（秒）',        'desc_en': 'Slow query threshold (s)'},
            {'key': 'table_open_cache',   'sql': "SHOW VARIABLES LIKE 'table_open_cache';",
             'desc_zh': '表缓存大小',               'desc_en': 'Table open cache size'},
            {'key': 'key_buffer_size',    'sql': "SHOW VARIABLES LIKE 'key_buffer_size';",
             'desc_zh': 'MyISAM 键缓存大小',     'desc_en': 'MyISAM key buffer size'},
        ]
    },
    {
        'chapter_number': 4,
        'chapter_title_zh': '性能分析',
        'chapter_title_en': 'Performance Analysis',
        'description': '数据库性能指标分析',
        'queries': [
            {'key': 'qps',           'sql': "SHOW GLOBAL STATUS LIKE 'Queries';",
             'desc_zh': 'QPS 累计查询数',        'desc_en': 'Cumulative queries'},
            {'key': 'com_commit',    'sql': "SHOW GLOBAL STATUS LIKE 'Com_commit';",
             'desc_zh': '事务提交次数',             'desc_en': 'Transaction commit count'},
            {'key': 'com_rollback',  'sql': "SHOW GLOBAL STATUS LIKE 'Com_rollback';",
             'desc_zh': '事务回滚次数',             'desc_en': 'Transaction rollback count'},
            {'key': 'innodb_row_ops', 'sql': "SHOW GLOBAL STATUS LIKE 'Innodb_rows_%';",
             'desc_zh': 'InnoDB 行操作统计',      'desc_en': 'InnoDB row operation stats'},
            {'key': 'innodb_data_ops','sql': "SHOW GLOBAL STATUS LIKE 'Innodb_data_%';",
             'desc_zh': 'InnoDB 数据读写统计',     'desc_en': 'InnoDB data R/W stats'},
            {'key': 'cache_hit_ratio', 'sql': "SELECT 'TiDB uses TiKV with different cache mechanism' AS info;",
             'desc_zh': '缓冲池命中率',            'desc_en': 'Buffer pool hit ratio'},
        ]
    },
    {
        'chapter_number': 5,
        'chapter_title_zh': '数据库空间使用',
        'chapter_title_en': 'Database Space Usage',
        'description': '数据库和表的空间使用情况',
        'queries': [
            {'key': 'db_size', 'sql': """
                SELECT table_schema AS database_name,
                       ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS total_mb,
                       ROUND(SUM(data_length) / 1024 / 1024, 2) AS data_mb,
                       ROUND(SUM(index_length) / 1024 / 1024, 2) AS index_mb,
                       COUNT(*) AS table_count
                FROM information_schema.TABLES
                WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                GROUP BY table_schema
                ORDER BY total_mb DESC;
            """,
             'desc_zh': '各数据库大小',           'desc_en': 'Database sizes'},
            {'key': 'table_size', 'sql': """
                SELECT table_schema AS database_name, table_name,
                       ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb,
                       table_rows
                FROM information_schema.TABLES
                WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                ORDER BY (data_length + index_length) DESC
                LIMIT 20;
            """,
             'desc_zh': '最大的 20 张表',      'desc_en': 'Top 20 largest tables'},
        ]
    },
    {
        'chapter_number': 6,
        'chapter_title_zh': '安全信息',
        'chapter_title_en': 'Security Information',
        'description': '数据库安全相关配置',
        'queries': [
            {'key': 'mysql_users', 'sql': """
                SELECT user AS col1, host AS col2, Grant_priv AS col3,
                       plugin AS col4, account_locked AS col5, password_expired AS col6
                FROM mysql.user
                WHERE user NOT IN ('mysql.infoschema','mysql.session','mysql.sys','root')
                ORDER BY user;
            """,
             'desc_zh': '非系统用户列表',           'desc_en': 'Non-system users'},
            {'key': 'password_expiry', 'sql': """
                SELECT user, host, password_expired, password_lifetime
                FROM mysql.user
                WHERE password_expired='Y' OR password_lifetime IS NOT NULL;
            """,
             'desc_zh': '密码过期用户',            'desc_en': 'Password expiry status'},
            {'key': 'user_privileges', 'sql': """
                SELECT grantee, privilege_type, is_grantable
                FROM information_schema.USER_PRIVILEGES
                WHERE grantee NOT LIKE '%root%' AND grantee NOT LIKE '%mysql%'
                ORDER BY grantee;
            """,
             'desc_zh': '用户权限一览',            'desc_en': 'User privileges overview'},
        ]
    },
    {
        'chapter_number': 7,
        'chapter_title_zh': '复制状态检查',
        'chapter_title_en': 'Replication Status',
        'description': '主从复制状态检查',
        'queries': [
            {'key': 'slave_status',    'sql': "SELECT 'TiDB has no master-slave replication' AS info;",
             'desc_zh': 'TiDB 无主从复制',           'desc_en': 'TiDB no replication'},
            {'key': 'master_status',    'sql': "SELECT 'TiDB has no master-slave replication' AS info;",
             'desc_en': 'TiDB no replication', 'desc_zh': 'TiDB 无主从复制'},
            {'key': 'slave_io_running', 'sql': "SELECT 'TiDB has no master-slave replication' AS info;",
             'desc_zh': 'TiDB 无主从复制',       'desc_en': 'TiDB no replication'},
            {'key': 'replication_lag',  'sql': "SELECT 'TiDB has no master-slave replication' AS info;",
             'desc_zh': 'TiDB 无主从复制延迟', 'desc_en': 'TiDB no replication lag'},
        ]
    },
    {
        'chapter_number': 8,
        'chapter_title_zh': 'InnoDB 锁等待检查',
        'chapter_title_en': 'InnoDB Lock Analysis',
        'description': 'InnoDB 锁等待和长事务检查',
        'queries': [
            {'key': 'innodb_lock_chain', 'sql': "SELECT 'Lock wait info available in tidb_trx and slow query log' AS info;",
             'desc_zh': 'TiDB 锁等待链',      'desc_en': 'TiDB lock wait chain'},
            {'key': 'innodb_long_trx', 'sql': "SELECT * FROM information_schema.tidb_trx LIMIT 50;",
             'desc_zh': '运行超过 60 秒的长事务', 'desc_en': 'Long transactions > 60s'},
            {'key': 'innodb_deadlock', 'sql': "SELECT 'Deadlock info available in slow query log and TiDB Dashboard' AS info;",
             'desc_zh': 'TiDB 锁等待详情', 'desc_en': 'TiDB lock wait details'},
        ]
    },
    {
        'chapter_number': 9,
        'chapter_title_zh': '缓冲池状态',
        'chapter_title_en': 'Buffer Pool Status',
        'description': 'InnoDB 缓冲池使用状态',
        'queries': [
            {'key': 'buffer_pool_status', 'sql': "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool%';",
             'desc_zh': '缓冲池相关状态',           'desc_en': 'Buffer pool related status'},
            {'key': 'buffer_pool_size',    'sql': "SHOW VARIABLES LIKE 'innodb_buffer_pool_size';",
             'desc_zh': '缓冲池大小配置',           'desc_en': 'Buffer pool size config'},
            {'key': 'buffer_pool_instances', 'sql': "SHOW VARIABLES LIKE 'innodb_buffer_pool_instances';",
             'desc_zh': '缓冲池实例数',            'desc_en': 'Buffer pool instances'},
        ]
    },
    {
        'chapter_number': 10,
        'chapter_title_zh': '事务和锁分析',
        'chapter_title_en': 'Transaction & Lock Analysis',
        'description': '事务状态和锁等待分析',
        'queries': [
            {'key': 'trx_list',   'sql': "SELECT * FROM information_schema.tidb_trx LIMIT 50;",
             'desc_zh': '当前 TiDB 事务列表',   'desc_en': 'Current TiDB transactions'},
            {'key': 'lock_waits', 'sql': "SELECT 'Lock wait info available in tidb_trx and slow query log' AS info;",
             'desc_zh': '锁等待列表',              'desc_en': 'Lock wait list'},
            {'key': 'lock_summary', 'sql': "SELECT 'Lock state stats available in slow query log' AS info;",
             'desc_zh': '锁状态统计',               'desc_en': 'Lock state statistics'},
        ]
    },
    {
        'chapter_number': 11,
        'chapter_title_zh': '慢查询分析',
        'chapter_title_en': 'Slow Query Analysis',
        'description': '慢查询日志分析',
        'queries': [
            {'key': 'slow_query_status',  'sql': "SHOW VARIABLES LIKE 'slow_query%';",
             'desc_zh': '慢查询配置',              'desc_en': 'Slow query configuration'},
            {'key': 'slow_query_count',  'sql': "SHOW GLOBAL STATUS LIKE 'Slow_queries';",
             'desc_zh': '慢查询次数',              'desc_en': 'Slow query count'},
            {'key': 'long_query_time_cfg', 'sql': "SHOW VARIABLES LIKE 'long_query_time';",
             'desc_zh': '慢查询阈值配置',           'desc_en': 'Slow query threshold config'},
        ]
    },
    {
        'chapter_number': 12,
        'chapter_title_zh': '表碎片和统计信息',
        'chapter_title_en': 'Table Fragmentation & Statistics',
        'description': '表碎片率和统计信息新鲜度',
        'queries': [
            {'key': 'table_fragmentation', 'sql': """
                SELECT table_schema, table_name,
                       ROUND(data_length / 1024 / 1024, 2) AS data_mb,
                       ROUND(data_free / 1024 / 1024, 2) AS free_mb,
                       ROUND(data_free * 100.0 / NULLIF(data_length, 0), 2) AS frag_percent
                FROM information_schema.TABLES
                WHERE data_free > 0
                  AND table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                ORDER BY data_free DESC
                LIMIT 20;
            """,
             'desc_zh': '表碎片率 TOP 20',       'desc_en': 'Top 20 fragmented tables'},
            {'key': 'stale_tables', 'sql': """
                SELECT table_schema, table_name, update_time
                FROM information_schema.TABLES
                WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                  AND update_time < NOW() - INTERVAL 7 DAY
                ORDER BY update_time DESC
                LIMIT 20;
            """,
             'desc_zh': '超过7天未更新的表',     'desc_en': 'Tables not updated in 7 days'},
        ]
    },
    {
        'chapter_number': 13,
        'chapter_title_zh': '索引使用情况',
        'chapter_title_en': 'Index Usage Analysis',
        'description': '索引使用率和冗余索引分析',
        'queries': [
            {'key': 'unused_indexes', 'sql': "SELECT * FROM information_schema.tidb_indexes",
             'desc_zh': '从未使用的索引',           'desc_en': 'Unused indexes'},
            {'key': 'index_stats', 'sql': "SELECT * FROM information_schema.tidb_indexes",
             'desc_zh': '索引使用情况 TOP 20',    'desc_en': 'Top 20 index usage'},
        ]
    },
    {
        'chapter_number': 14,
        'chapter_title_zh': '主从复制延迟',
        'chapter_title_en': 'Replication Lag',
        'description': '主从复制延迟详细分析',
        'queries': [
            {'key': 'repl_lag_detail', 'sql': "SELECT 'TiDB has no master-slave replication' AS info;",
             'desc_zh': 'TiDB 无主从复制',            'desc_en': 'TiDB no master-slave replication'},
            {'key': 'repl_channels',  'sql': "SELECT 'TiDB has no replication channels' AS info;",
             'desc_zh': 'TiDB 无复制通道', 'desc_en': 'TiDB no replication channels'},
        ]
    },
    {
        'chapter_number': 15,
        'chapter_title_zh': 'Binlog 状态',
        'chapter_title_en': 'Binary Log Status',
        'description': 'Binary Log 状态和配置',
        'queries': [
            {'key': 'binlog_status', 'sql': "SELECT 'TiDB uses distributed storage, no traditional binlog' AS note;",
             'desc_zh': 'Binlog 文件列表',       'desc_en': 'Binary log file list'},
            {'key': 'binlog_config', 'sql': "SHOW VARIABLES LIKE 'binlog%';",
             'desc_zh': 'Binlog 相关配置',       'desc_en': 'Binary log configuration'},
            {'key': 'binlog_cache',  'sql': "SHOW GLOBAL STATUS LIKE 'Binlog_cache%';",
             'desc_zh': 'Binlog 缓存统计',       'desc_en': 'Binary log cache stats'},
        ]
    },
    {
        'chapter_number': 16,
        'chapter_title_zh': '用户权限审计',
        'chapter_title_en': 'User Privilege Audit',
        'description': '用户权限和安全审计',
        'queries': [
            {'key': 'user_list', 'sql': """
                SELECT user, host, authentication_string IS NOT NULL AS has_password,
                       password_expired, password_lifetime, account_locked, plugin
                FROM mysql.user
                WHERE user != ''
                ORDER BY user, host;
            """,
             'desc_zh': '用户账号安全状态',          'desc_en': 'User account security status'},
            {'key': 'db_privileges', 'sql': "SELECT * FROM mysql.db ORDER BY user, db;",
             'desc_zh': '数据库级别权限',           'desc_en': 'Database-level privileges'},
            {'key': 'role_edges',    'sql': "SELECT * FROM mysql.role_edges ORDER BY from_user;",
             'desc_zh': '角色关系',                'desc_en': 'Role edges'},
        ]
    },
    {
        'chapter_number': 17,
        'chapter_title_zh': '存储引擎状态',
        'chapter_title_en': 'Storage Engine Status',
        'description': '存储引擎状态和统计',
        'queries': [
            {'key': 'engine_status', 'sql': "SHOW ENGINES;",
             'desc_zh': '支持的存储引擎',           'desc_en': 'Supported storage engines'},
            {'key': 'innodb_status', 'sql': "SELECT 'TiDB uses TiKV storage engine' AS engine_info;",
             'desc_zh': 'InnoDB 引擎详细状态',    'desc_en': 'InnoDB engine detailed status'},
        ]
    },
    {
        'chapter_number': 18,
        'chapter_title_zh': '系统变量检查',
        'chapter_title_en': 'System Variables Check',
        'description': '关键系统变量检查',
        'queries': [
            {'key': 'key_vars', 'sql': """
                SELECT variable_name, variable_value
                FROM information_schema.global_variables
                WHERE variable_name IN (
                    'tidb_mem_quota_query','tidb_distsql_scan_concurrency','tidb_txn_mode',
                    'tidb_enable_streaming','tidb_batch_insert','tidb_batch_delete',
                    'tidb_batch_commit','tidb_dml_batch_size','tidb_index_lookup_size',
                    'tidb_index_lookup_concurrency','tidb_mem_quota_index_lookup',
                    'tidb_gc_enable','tidb_gc_run_interval','tidb_gc_life_time'
                )
                ORDER BY variable_name;
            """,
             'desc_zh': '关键系统变量一览',           'desc_en': 'Key system variables overview'},
        ]
    },
    {
        'chapter_number': 19,
        'chapter_title_zh': '错误日志检查',
        'chapter_title_en': 'Error Log Check',
        'description': '错误日志和告警信息',
        'queries': [
            {'key': 'error_log_path', 'sql': "SHOW VARIABLES LIKE 'log_error';",
             'desc_zh': '错误日志路径',            'desc_en': 'Error log path'},
            {'key': 'log_warnings',   'sql': "SHOW VARIABLES LIKE 'log_warnings';",
             'desc_zh': '警告日志级别',            'desc_en': 'Warning log level'},
        ]
    },
    {
        'chapter_number': 20,
        'chapter_title_zh': '计划任务和事件',
        'chapter_title_en': 'Scheduled Events',
        'description': 'MySQL 事件调度器状态',
        'queries': [
            {'key': 'event_scheduler', 'sql': "SHOW VARIABLES LIKE 'event_scheduler';",
             'desc_zh': '事件调度器是否开启',        'desc_en': 'Event scheduler status'},
            {'key': 'events_list',    'sql': "SHOW EVENTS FROM information_schema;",
             'desc_zh': '事件列表',                'desc_en': 'Event list'},
        ]
    },
    {
        'chapter_number': 21,
        'chapter_title_zh': 'InnoDB 表空间状态',
        'chapter_title_en': 'InnoDB Tablespace Status',
        'description': 'InnoDB 表空间和文件状态',
        'queries': [
            {'key': 'innodb_tablespaces', 'sql': "SELECT table_schema, table_name, table_rows, data_length, index_length FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys') ORDER BY data_length + index_length DESC LIMIT 30;",
             'desc_zh': 'TiDB 表空间',            'desc_en': 'TiDB tablespaces'},
            {'key': 'innodb_datafiles',   'sql': "SELECT 'TiDB uses distributed TiKV storage, no local data files' AS info;",
             'desc_zh': 'TiDB 无本地数据文件',  'desc_en': 'TiDB no local datafiles'},
            {'key': 'file_per_table',     'sql': "SELECT 'TiDB uses TiKV storage engine' AS engine_info;",
             'desc_zh': '存储引擎信息',        'desc_en': 'Storage engine info'},
        ]
    },
    # ========== 以下为 TiDB 特性章节 ==========
    {
        'chapter_number': 22,
        'chapter_title_zh': '集群拓扑检查',
        'chapter_title_en': 'Cluster Topology',
        'description': 'TiDB 集群拓扑结构（TiDB 特性）',
        'queries': [
            {'key': 'cluster_info', 'sql': "SELECT * FROM information_schema.CLUSTER_INFO;",
             'desc_zh': '集群节点信息',            'desc_en': 'Cluster node info'},
            {'key': 'cluster_config', 'sql': "SELECT * FROM information_schema.CLUSTER_CONFIG LIMIT 50;",
             'desc_zh': '集群配置信息',            'desc_en': 'Cluster config info'},
        ]
    },
    {
        'chapter_number': 23,
        'chapter_title_zh': 'TiKV 状态检查',
        'chapter_title_en': 'TiKV Status',
        'description': 'TiKV 存储引擎状态（TiDB 特性）',
        'queries': [
            {'key': 'tikv_store', 'sql': "SELECT * FROM information_schema.tidb_servers_info;",
             'desc_zh': 'TiDB 集群节点',            'desc_en': 'TiDB cluster nodes'},
            {'key': 'tikv_config', 'sql': "SHOW CONFIG WHERE type='tikv';",
             'desc_zh': 'TiKV 配置参数',          'desc_en': 'TiKV config parameters'},
        ]
    },
    {
        'chapter_number': 24,
        'chapter_title_zh': 'PD 状态检查',
        'chapter_title_en': 'PD Status',
        'description': 'Placement Driver 状态检查（TiDB 特性）',
        'queries': [
            {'key': 'pd_config', 'sql': "SHOW CONFIG WHERE type='pd';",
             'desc_zh': 'PD 配置参数',            'desc_en': 'PD config parameters'},
            {'key': 'pd_regions_info', 'sql': "SELECT 'PD regions info available via TiDB Dashboard or PD API' AS info;",
             'desc_zh': 'PD Regions 信息',       'desc_en': 'PD Regions info'},
        ]
    },
    {
        'chapter_number': 25,
        'chapter_title_zh': 'GC 状态检查',
        'chapter_title_en': 'GC Status',
        'description': 'GC (Garbage Collection) 状态（TiDB 特性）',
        'queries': [
            {'key': 'gc_config', 'sql': "SHOW VARIABLES LIKE 'tidb_gc%';",
             'desc_zh': 'GC 相关配置',            'desc_en': 'GC related config'},
            {'key': 'gc_life_time', 'sql': "SHOW CONFIG WHERE type='tikv' AND name LIKE '%gc%';",
             'desc_zh': 'TiKV GC 配置',           'desc_en': 'TiKV GC config'},
        ]
    },
    {
        'chapter_number': 26,
        'chapter_title_zh': '热点检测',
        'chapter_title_en': 'Hotspot Detection',
        'description': 'Region 热点检测（TiDB 特性）',
        'queries': [
            {'key': 'hot_regions', 'sql': "SELECT 'Hot regions info available via TiDB Dashboard or PD API' AS info;",
             'desc_zh': '热 Region 信息',         'desc_en': 'Hot regions info'},
            {'key': 'hot_regions_by_table', 'sql': "SELECT 'Hot regions by table available via TiDB Dashboard or PD API' AS info;",
             'desc_zh': '按表统计热 Region',       'desc_en': 'Hot regions by table'},
        ]
    },
    {
        'chapter_number': 27,
        'chapter_title_zh': 'DDL 状态检查',
        'chapter_title_en': 'DDL Status',
        'description': 'DDL 操作状态检查（TiDB 特性）',
        'queries': [
            {'key': 'ddl_jobs', 'sql': "SELECT * FROM information_schema.DDL_JOBS LIMIT 20;",
             'desc_zh': 'DDL 任务列表',            'desc_en': 'DDL job list'},
        ]
    },
    {
        'chapter_number': 28,
        'chapter_title_zh': 'SQL 执行统计',
        'chapter_title_en': 'SQL Execution Statistics',
        'description': 'SQL 语句执行统计（TiDB 特性）',
        'queries': [
            {'key': 'stmt_summary', 'sql': """
                SELECT DIGEST_TEXT AS digest_text,
                       EXEC_COUNT AS exec_count,
                       SUM_LATENCY AS total_latency,
                       AVG_LATENCY AS avg_latency
                FROM information_schema.STATEMENTS_SUMMARY
                WHERE SCHEMA_NAME NOT IN ('mysql','information_schema','performance_schema','sys')
                ORDER BY EXEC_COUNT DESC
                LIMIT 20;
            """,
             'desc_zh': 'SQL 执行次数 TOP 20',    'desc_en': 'Top 20 SQL by exec count'},
            {'key': 'stmt_summary_top_latency', 'sql': """
                SELECT DIGEST_TEXT AS digest_text,
                       EXEC_COUNT AS exec_count,
                       SUM_LATENCY AS total_latency,
                       AVG_LATENCY AS avg_latency
                FROM information_schema.STATEMENTS_SUMMARY
                WHERE SCHEMA_NAME NOT IN ('mysql','information_schema','performance_schema','sys')
                ORDER BY SUM_LATENCY DESC
                LIMIT 20;
            """,
             'desc_zh': 'SQL 总耗时 TOP 20',     'desc_en': 'Top 20 SQL by total latency'},
        ]
    },
    {
        'chapter_number': 29,
        'chapter_title_zh': 'TiDB 专用变量',
        'chapter_title_en': 'TiDB Specific Variables',
        'description': 'TiDB 特有系统变量（TiDB 特性）',
        'queries': [
            {'key': 'tidb_vars', 'sql': "SHOW VARIABLES WHERE variable_name LIKE 'tidb%';",
             'desc_zh': 'TiDB 相关系统变量',       'desc_en': 'TiDB related system variables'},
            {'key': 'tidb_kv_request', 'sql': "SHOW GLOBAL STATUS LIKE 'tidb_kv_request%';",
             'desc_zh': 'KV 请求延迟统计',         'desc_en': 'KV request latency stats'},
        ]
    },
]
# 临时文件：IvorySQL 21 章配置（基于 PostgreSQL 协议，增加 Oracle 兼容视图）

IVORYSQL_DEFAULT_CHAPTERS = [
    {
        'chapter_number': 1,
        'chapter_title_zh': '健康状态概览',
        'chapter_title_en': 'Health Overview',
        'description': '数据库整体健康状态概览',
        'queries': [
            {'key': 'ivorysql_version', 'sql': "SELECT version();",
             'desc_zh': '获取 IvorySQL 版本',       'desc_en': 'Get IvorySQL version'},
            {'key': 'ivorysql_uptime', 'sql': "SELECT now() - pg_postmaster_start_time() AS uptime;",
             'desc_zh': '数据库运行时长',        'desc_en': 'Database uptime'},
            {'key': 'ivorysql_compat', 'sql': "SELECT name, setting FROM pg_settings WHERE name = 'ivorysql.compatible_db';",
             'desc_zh': 'Oracle 兼容模式（仅 ORAMODE）', 'desc_en': 'Oracle compatibility mode (ORAMODE only)'},
        ]
    },
    {
        'chapter_number': 2,
        'chapter_title_zh': '连接状态检查',
        'chapter_title_en': 'Connection Status',
        'description': '数据库连接相关状态检查',
        'queries': [
            {'key': 'pg_conn_detail', 'sql': "SELECT state, count(*) AS count FROM pg_stat_activity WHERE state IS NOT NULL GROUP BY state ORDER BY count DESC;",
             'desc_zh': '连接状态分布',              'desc_en': 'Connection state breakdown'},
            {'key': 'pg_wait_events', 'sql': "SELECT wait_event_type, wait_event, count(*) AS count FROM pg_stat_activity WHERE wait_event IS NOT NULL GROUP BY wait_event_type, wait_event ORDER BY count DESC LIMIT 10;",
             'desc_zh': '等待事件 TOP 10',          'desc_en': 'Top 10 wait events'},
        ]
    },
    {
        'chapter_number': 3,
        'chapter_title_zh': 'IvorySQL 配置检查',
        'chapter_title_en': 'IvorySQL Configuration',
        'description': 'IvorySQL 关键配置参数（含 Oracle 兼容）',
        'queries': [
            {'key': 'shared_buffers',         'sql': "SHOW shared_buffers;",
             'desc_zh': '共享缓冲区大小',         'desc_en': 'Shared buffers size'},
            {'key': 'ivorysql_compat_db', 'sql': "SELECT name, setting FROM pg_settings WHERE name = 'ivorysql.compatible_db';",
             'desc_zh': 'Oracle 兼容数据库名（仅 ORAMODE）', 'desc_en': 'Oracle compatible db name (ORAMODE only)'},
            {'key': 'pg_settings_key', 'sql': """
                SELECT name, setting, unit, short_desc
                FROM pg_settings
                WHERE name IN ('max_connections','shared_buffers','work_mem','maintenance_work_mem',
                               'effective_cache_size','wal_level','archive_mode','ivorysql.compatible_db')
                ORDER BY name;
            """,
             'desc_zh': '关键参数一览',               'desc_en': 'Key parameters overview'},
        ]
    },
    {
        'chapter_number': 4,
        'chapter_title_zh': 'Oracle 兼容对象检查',
        'chapter_title_en': 'Oracle Compatible Objects',
        'description': 'IvorySQL Oracle 兼容模式下的对象检查',
        'queries': [
            {'key': 'ora_syonyms', 'sql': """
                SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'pg_synonyms') AS pg_synonyms_exists;
            """,
             'desc_zh': 'pg_synonyms 表是否存在（仅 ORAMODE）', 'desc_en': 'Check pg_synonyms table exists (ORAMODE only)'},
            {'key': 'ora_sequences', 'sql': """
                SELECT * FROM pg_sequences LIMIT 20;
            """,
             'desc_zh': 'Oracle 序列列表（前20）',   'desc_en': 'Oracle sequences list (top 20)'},
        ]
    },
    {
        'chapter_number': 5,
        'chapter_title_zh': '性能分析',
        'chapter_title_en': 'Performance Analysis',
        'description': '数据库性能指标和锁分析',
        'queries': [
            {'key': 'pg_lock_info', 'sql': "SELECT count(*) AS total_locks, sum(CASE WHEN granted THEN 1 ELSE 0 END) AS granted_locks, sum(CASE WHEN NOT granted THEN 1 ELSE 0 END) AS waiting_locks FROM pg_locks;",
             'desc_zh': '锁数量统计',                'desc_en': 'Lock count statistics'},
            {'key': 'pg_long_xact', 'sql': """
                SELECT pid, usename, datname, application_name, state, xact_start,
                       EXTRACT(EPOCH FROM (now() - xact_start)) AS xact_seconds,
                       left(query, 200) AS query
                FROM pg_stat_activity
                WHERE xact_start IS NOT NULL AND state != 'idle'
                ORDER BY xact_start;
            """,
             'desc_zh': '长事务列表',                 'desc_en': 'Long transactions'},
        ]
    },
    {
        'chapter_number': 6,
        'chapter_title_zh': '数据库空间使用',
        'chapter_title_en': 'Database Space Usage',
        'description': '数据库和表空间使用情况',
        'queries': [
            {'key': 'pg_db_size',    'sql': "SELECT datname AS database_name, pg_size_pretty(pg_database_size(datname)) AS size FROM pg_database WHERE datistemplate=false ORDER BY pg_database_size(datname) DESC;",
             'desc_zh': '各数据库大小',            'desc_en': 'Database sizes'},
            {'key': 'pg_tablespace', 'sql': "SELECT spcname AS tablespace_name, pg_size_pretty(pg_tablespace_size(oid)) AS size FROM pg_tablespace ORDER BY pg_tablespace_size(oid) DESC;",
             'desc_zh': '表空间大小',            'desc_en': 'Tablespace sizes'},
        ]
    },
    {
        'chapter_number': 7,
        'chapter_title_zh': '表与索引分析',
        'chapter_title_en': 'Table & Index Analysis',
        'description': '表膨胀、索引使用率分析',
        'queries': [
            {'key': 'pg_table_stats', 'sql': """
                SELECT schemaname, relname AS tablename,
                       n_live_tup AS live_rows, n_dead_tup AS dead_rows,
                       round(n_dead_tup * 100.0 / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_ratio,
                       last_vacuum, last_autovacuum, last_analyze, last_autoanalyze
                FROM pg_stat_user_tables
                ORDER BY n_dead_tup DESC LIMIT 15;
            """,
             'desc_zh': '表死行统计（需 vacuum）', 'desc_en': 'Table dead tuples (vacuum needed)'},
            {'key': 'pg_index_usage', 'sql': """
                SELECT schemaname, relname AS tablename, indexrelname AS indexname,
                       idx_scan, idx_tup_read, idx_tup_fetch
                FROM pg_stat_user_indexes
                ORDER BY idx_scan ASC LIMIT 15;
            """,
             'desc_zh': '索引扫描次数（低者可考虑删除）', 'desc_en': 'Index scan count (low = candidate for removal)'},
        ]
    },
    {
        'chapter_number': 8,
        'chapter_title_zh': '复制状态检查',
        'chapter_title_en': 'Replication Status',
        'description': '流复制状态检查',
        'queries': [
            {'key': 'pg_replication', 'sql': "SELECT pid, usename, application_name, client_addr, state, sent_lsn, write_lsn, flush_lsn, replay_lsn, sync_state FROM pg_stat_replication;",
             'desc_zh': '流复制状态',               'desc_en': 'Streaming replication status'},
        ]
    },
    {
        'chapter_number': 9,
        'chapter_title_zh': 'WAL 与检查点',
        'chapter_title_en': 'WAL & Checkpoint Analysis',
        'description': 'WAL 生成量和检查点频率',
        'queries': [
            {'key': 'pg_wal_rate', 'sql': """
                SELECT pg_walfile_name(pg_current_wal_lsn()) AS current_wal_file,
                       pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0') AS wal_bytes_total;
            """,
             'desc_zh': '当前 WAL 位置',          'desc_en': 'Current WAL position'},
            {'key': 'pg_checkpoint', 'sql': "SELECT * FROM pg_stat_bgwriter;",
             'desc_zh': '检查点写入统计',            'desc_en': 'Checkpoint write stats'},
        ]
    },
    {
        'chapter_number': 10,
        'chapter_title_zh': '表空间与磁盘',
        'chapter_title_en': 'Tablespace & Disk',
        'description': '表空间使用和磁盘 I/O',
        'queries': [
            {'key': 'pg_tablespace_size', 'sql': "SELECT spcname, pg_size_pretty(pg_tablespace_size(oid)) AS size FROM pg_tablespace;",
             'desc_zh': '表空间大小',               'desc_en': 'Tablespace sizes'},
        ]
    },
    {
        'chapter_number': 11,
        'chapter_title_zh': '数据库年龄与事务ID',
        'chapter_title_en': 'Database Age & Transaction ID',
        'description': '防止事务 ID 回卷',
        'queries': [
            {'key': 'pg_database_age', 'sql': """
                SELECT datname, age(datfrozenxid) AS xid_age,
                       round(age(datfrozenxid) * 100.0 / 2147483647, 2) AS wraparound_risk
                FROM pg_database
                ORDER BY age(datfrozenxid) DESC;
            """,
             'desc_zh': '数据库年龄（xid 回卷风险）', 'desc_en': 'Database age (xid wraparound risk)'},
        ]
    },
    {
        'chapter_number': 12,
        'chapter_title_zh': '扩展与 pg_stat_statements',
        'chapter_title_en': 'Extensions & pg_stat_statements',
        'description': '是否启用 pg_stat_statements',
        'queries': [
            {'key': 'pg_extensions', 'sql': "SELECT * FROM pg_available_extensions WHERE installed_version IS NOT NULL ORDER BY name;",
             'desc_zh': '已安装扩展',               'desc_en': 'Installed extensions'},
        ]
    },
    {
        'chapter_number': 13,
        'chapter_title_zh': 'TOP SQL 分析',
        'chapter_title_en': 'Top SQL Analysis',
        'description': '最耗时/最频繁的 SQL',
        'queries': [
            {'key': 'pg_top_elapsed', 'sql': """
                SELECT queryid, query, calls, total_time, mean_time, rows
                FROM pg_stat_statements
                ORDER BY total_time DESC
                LIMIT 10;
            """,
             'desc_zh': 'TOP SQL（总耗时，需 pg_stat_statements）', 'desc_en': 'Top SQL by total time (requires pg_stat_statements)'},
            {'key': 'pg_top_calls', 'sql': """
                SELECT queryid, query, calls, total_time, mean_time, rows
                FROM pg_stat_statements
                ORDER BY calls DESC
                LIMIT 10;
            """,
             'desc_zh': 'TOP SQL（执行次数，需 pg_stat_statements）', 'desc_en': 'Top SQL by calls (requires pg_stat_statements)'},
        ]
    },
    {
        'chapter_number': 14,
        'chapter_title_zh': '用户与角色权限',
        'chapter_title_en': 'Users & Roles',
        'description': '数据库用户和角色权限',
        'queries': [
            {'key': 'pg_users', 'sql': "SELECT usename, usesuper, usecreatedb, userepl, usebypassrls, passwd IS NOT NULL AS has_password FROM pg_user ORDER BY usename;",
             'desc_zh': '数据库用户列表',              'desc_en': 'Database users list'},
            {'key': 'pg_roles', 'sql': "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication FROM pg_roles ORDER BY rolname;",
             'desc_zh': '角色列表',                   'desc_en': 'Roles list'},
        ]
    },
    {
        'chapter_number': 15,
        'chapter_title_zh': '数据库连接池状态',
        'chapter_title_en': 'Connection Pool Status',
        'description': '连接池相关信息',
        'queries': [
            {'key': 'pg_conn_by_db', 'sql': "SELECT datname, count(*) AS conn_count, count(*) FILTER (WHERE state='active') AS active_count FROM pg_stat_activity GROUP BY datname ORDER BY conn_count DESC;",
             'desc_zh': '按数据库统计连接数',        'desc_en': 'Connections by database'},
        ]
    },
    {
        'chapter_number': 16,
        'chapter_title_zh': '备份与恢复状态',
        'chapter_title_en': 'Backup & Recovery Status',
        'description': '备份工具和恢复相关信息',
        'queries': [
            {'key': 'pg_is_in_recovery', 'sql': "SELECT pg_is_in_recovery() AS is_standby, CASE WHEN pg_is_in_recovery() THEN pg_last_wal_receive_lsn() ELSE pg_current_wal_lsn() END AS last_lsn;",
             'desc_zh': '是否处于恢复模式',           'desc_en': 'Is in recovery mode'},
        ]
    },
    {
        'chapter_number': 17,
        'chapter_title_zh': '表统计信息新鲜度',
        'chapter_title_en': 'Table Statistics Freshness',
        'description': '统计信息最后收集时间',
        'queries': [
            {'key': 'pg_stat_last_analyze', 'sql': """
                SELECT schemaname, relname, last_analyze, last_autoanalyze, n_live_tup
                FROM pg_stat_user_tables
                WHERE last_analyze IS NULL OR last_analyze < now() - interval '7 days'
                ORDER BY last_analyze ASC NULLS FIRST
                LIMIT 20;
            """,
             'desc_zh': '统计信息过期的表（>7天）', 'desc_en': 'Tables with stale stats (>7 days)'},
        ]
    },
    {
        'chapter_number': 18,
        'chapter_title_zh': '索引大小与维护',
        'chapter_title_en': 'Index Size & Maintenance',
        'description': '索引大小和是否需要重建',
        'queries': [
            {'key': 'pg_index_size', 'sql': """
                SELECT schemaname, relname AS tablename, indexrelname AS indexname,
                       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
                FROM pg_stat_user_indexes
                ORDER BY pg_relation_size(indexrelid) DESC
                LIMIT 20;
            """,
             'desc_zh': '索引大小 TOP 20',        'desc_en': 'Top 20 largest indexes'},
        ]
    },
    {
        'chapter_number': 19,
        'chapter_title_zh': '数据库对象统计',
        'chapter_title_en': 'Database Object Statistics',
        'description': '表、索引、序列等对象数量',
        'queries': [
            {'key': 'pg_table_count', 'sql': "SELECT schemaname, count(*) AS table_count FROM pg_stat_user_tables GROUP BY schemaname;",
             'desc_zh': '各 schema 表数量',     'desc_en': 'Table count by schema'},
            {'key': 'pg_index_count', 'sql': "SELECT schemaname, count(*) AS index_count FROM pg_stat_user_indexes GROUP BY schemaname;",
             'desc_zh': '各 schema 索引数量',    'desc_en': 'Index count by schema'},
        ]
    },
    {
        'chapter_number': 20,
        'chapter_title_zh': '死锁与错误日志',
        'chapter_title_en': 'Deadlocks & Error Logs',
        'description': '死锁统计和日志配置',
        'queries': [
            {'key': 'pg_deadlocks', 'sql': "SELECT datname, deadlocks FROM pg_stat_database ORDER BY deadlocks DESC;",
             'desc_zh': '各库死锁次数',              'desc_en': 'Deadlocks by database'},
        ]
    },
    {
        'chapter_number': 21,
        'chapter_title_zh': '插件与扩展状态',
        'chapter_title_en': 'Extensions & Plugins',
        'description': '已安装扩展和可用扩展',
        'queries': [
            {'key': 'pg_installed_ext', 'sql': "SELECT extname, extversion, extrelocatable FROM pg_extension ORDER BY extname;",
             'desc_zh': '已安装扩展详情',             'desc_en': 'Installed extensions detail'},
            {'key': 'pg_available_ext', 'sql': "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE installed_version IS NULL ORDER BY name LIMIT 20;",
             'desc_zh': '可安装扩展（前20）',        'desc_en': 'Available extensions (top 20)'},
        ]
    },
]
# ==================== 初始化函数 ====================

def init_default_templates(db_path: str = None, force: bool = False):
    """
    为每种数据库类型创建默认模板和章节。

    :param db_path: 数据库文件路径
    :param force: 是否强制重新初始化（会删除现有默认模板！）
    """
    print("开始初始化默认模板...")

    # 定义所有数据库类型的默认配置
    db_types = [
        ('mysql',     'MySQL 默认巡检模板',        'MySQL Default Inspection Template',        MYSQL_DEFAULT_CHAPTERS,        'v1', 1),
        ('postgresql', 'PostgreSQL 默认巡检模板',   'PostgreSQL Default Inspection Template',   POSTGRESQL_DEFAULT_CHAPTERS,   'v1', 1),
        ('oracle',     'Oracle 默认巡检模板',        'Oracle Default Inspection Template',         ORACLE_DEFAULT_CHAPTERS,         'v1', 1),
        ('oracle',     'Oracle 11g 巡检模板',        'Oracle 11g Inspection Template',             ORACLE_11G_CHAPTERS,             '11g', 0),
        ('sqlserver',  'SQL Server 默认巡检模板',    'SQL Server Default Inspection Template',    SQLSERVER_DEFAULT_CHAPTERS,    'v1', 1),
        ('dm8',       'DM8 达梦默认巡检模板',      'DM8 Default Inspection Template',          DM8_DEFAULT_CHAPTERS,          'v1', 1),
        ('tidb',      'TiDB 默认巡检模板',          'TiDB Default Inspection Template',          TIDB_DEFAULT_CHAPTERS,          'v1', 1),
        ('ivorysql',  'IvorySQL 默认巡检模板',    'IvorySQL Default Inspection Template',    IVORYSQL_DEFAULT_CHAPTERS,    'v1', 1),
    ]

    for db_type, template_name, template_name_en, chapters, version, is_default in db_types:
        # 检查是否已存在
        from inspection_dal import get_all_templates as _get_all_templates
        existing = None
        if is_default:
            existing = get_default_template(db_type, db_path)
        else:
            # 非默认模板：按 db_type + 名称查找
            all_tmpls = _get_all_templates(db_path)
            for t in all_tmpls:
                if t.get('db_type') == db_type and t.get('template_name') == template_name:
                    existing = t
                    break

        if existing and not force:
            print(f"⚠️  {db_type} {template_name} 已存在（ID: {existing['id']}），跳过")
            continue

        if existing and force:
            print(f"🗑️  强制重新初始化，删除 {template_name}（ID: {existing['id']}）")
            from inspection_dal import delete_template
            delete_template(existing['id'], db_path)

        # 创建模板
        tmpl_label = f"{db_type} {template_name}"
        print(f"📝 创建 {tmpl_label}...")
        template_id = create_template(
            db_type=db_type,
            template_name=template_name,
            template_name_en=template_name_en,
            description=f'{db_type.upper()} 数据库巡检模板（预置常用巡检 SQL，共 {len(chapters)} 章）',
            version=version,
            is_default=is_default,
            db_path=db_path
        )
        print(f"   ✅ 模板创建成功（ID: {template_id}）")

        # 创建章节和查询
        for chapter_config in chapters:
            print(f"   📝 创建章节：{chapter_config['chapter_title_zh']}...")
            chapter_id = create_chapter(
                template_id=template_id,
                chapter_number=chapter_config['chapter_number'],
                chapter_title_zh=chapter_config['chapter_title_zh'],
                chapter_title_en=chapter_config.get('chapter_title_en'),
                description=chapter_config.get('description'),
                db_path=db_path
            )
            print(f"      ✅ 章节创建成功（ID: {chapter_id}）")

            # 创建查询
            for query_config in chapter_config.get('queries', []):
                print(f"      📝 创建查询：{query_config['key']}...")
                query_id = create_query(
                    chapter_id=chapter_id,
                    query_key=query_config['key'],
                    query_sql=query_config['sql'],
                    query_description_zh=query_config.get('desc_zh'),
                    query_description_en=query_config.get('desc_en'),
                    db_path=db_path
                )
                print(f"         ✅ 查询创建成功（ID: {query_id}）")

    print("\n✅ 默认模板初始化完成！")


def main():
    parser = argparse.ArgumentParser(description='初始化 DBCheck 巡检配置数据库')
    parser.add_argument('--db-path', type=str, default=None,
                        help='数据库文件路径（默认：DBCheck 目录下的 inspection.db）')
    parser.add_argument('--force', action='store_true',
                        help='强制重新初始化（会删除现有默认模板数据！）')

    args = parser.parse_args()

    db_path = args.db_path or DEFAULT_DB_PATH

    print(f"数据库路径：{db_path}")
    print(f"强制重新初始化：{args.force}")
    print("-" * 50)

    # 初始化数据库表
    print("1. 创建数据库表...")
    init_database(db_path)

    # 初始化默认模板
    print("\n2. 创建默认模板（含预置 SQL）...")
    init_default_templates(db_path, args.force)

    print("\n" + "=" * 50)
    print("✅ 初始化完成！")
    print(f"数据库文件：{db_path}")
    print("现在你可以使用 DBCheck Web UI 来管理巡检配置。")


if __name__ == '__main__':
    main()
