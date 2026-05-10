# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck 索引健康分析模块
=======================
提供三个核心能力：
1. 缺失索引识别 —— 查询频繁但无索引的表
2. 冗余/重复索引识别 —— 同一列被多个索引覆盖
3. 长期未使用索引识别 —— 长时间未被使用的索引

支持: MySQL / PostgreSQL
"""

import os
import sys
import importlib
import time

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
#  1. MySQL 索引健康分析
# ═══════════════════════════════════════════════════════

def analyze_mysql_indexes(conn, days_threshold=90):
    """
    分析 MySQL 索引健康状况。
    
    参数:
        conn: pymysql 数据库连接
        days_threshold: 未使用索引的判定天数阈值（默认 90 天）
    
    返回格式:
    {
        'missing_indexes': [
            {
                'table_schema': str,
                'table_name': str,
                'column_name': str,
                'select_count': int,       # SELECT 次数
                'rows_examined_avg': int,  # 平均扫描行数
                'recommendation': str,      # 建议
            },
            ...
        ],
        'redundant_indexes': [
            {
                'table_schema': str,
                'table_name': str,
                'index1': str,
                'index2': str,
                'reason': str,
                'recommendation': str,
            },
            ...
        ],
        'unused_indexes': [
            {
                'table_schema': str,
                'table_name': str,
                'index_name': str,
                'last_used': str,          # 最后使用时间
                'days_unused': int,        # 未使用天数
                'index_size_mb': float,     # 索引大小 MB
                'recommendation': str,
            },
            ...
        ],
        'summary': {
            'missing_count': int,
            'redundant_count': int,
            'unused_count': int,
            'total_indexes': int,
            'db_size_gb': float,
        }
    }
    """
    result = {
        'missing_indexes': [],
        'redundant_indexes': [],
        'unused_indexes': [],
        'summary': {
            'missing_count': 0,
            'redundant_count': 0,
            'unused_count': 0,
            'total_indexes': 0,
            'db_size_gb': 0.0,
        }
    }
    
    cursor = conn.cursor()
    
    # ── 1.1 获取数据库总大小 ─────────────────────────────────
    try:
        cursor.execute("""
            SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024 / 1024, 2) AS db_size_gb
            FROM information_schema.tables
            WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
        """)
        row = cursor.fetchone()
        result['summary']['db_size_gb'] = float(row[0]) if row and row[0] else 0.0
    except Exception:
        pass
    
    # ── 1.2 缺失索引分析 ────────────────────────────────────
    # 通过分析慢查询和全表扫描来识别缺失索引
    try:
        # 获取最近的全表扫描查询（来自 performance_schema）
        cursor.execute("""
            SELECT 
                DIGEST_TEXT AS query,
                COUNT_STAR AS exec_count,
                SUM_ROWS_EXAMINED AS rows_examined,
                DIGEST AS digest
            FROM performance_schema.events_statements_summary_by_digest
            WHERE DIGEST_TEXT LIKE '%%SELECT%%'
              AND DIGEST_TEXT NOT LIKE '%%information_schema%%'
              AND DIGEST_TEXT NOT LIKE '%%performance_schema%%'
            ORDER BY SUM_ROWS_EXAMINED DESC
            LIMIT 50
        """)
        
        high_scan_queries = []
        for row in cursor.fetchall():
            query_text = row[0] or ''
            # 排除系统库查询
            if any(x in query_text.lower() for x in ['information_schema', 'performance_schema', 'mysql.']):
                continue
            # 估算是否可能缺少索引（查询包含 WHERE/ORDER BY/LIMIT 且扫描行数大）
            if ('where' in query_text.lower() or 'order by' in query_text.lower()) and row[2] and int(row[2]) > 10000:
                high_scan_queries.append({
                    'query': query_text,
                    'exec_count': int(row[1]),
                    'rows_examined': int(row[2]),
                    'digest': row[3]
                })
        
        # 尝试从 table_statistics 或 user_stats 获取高访问量表
        try:
            cursor.execute("""
                SELECT 
                    OBJECT_SCHEMA AS table_schema,
                    OBJECT_NAME AS table_name,
                    COUNT_READ AS read_count,
                    COUNT_FETCH AS fetch_count
                FROM performance_schema.table_statistics
                WHERE OBJECT_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
                ORDER BY COUNT_READ DESC
                LIMIT 20
            """)
            
            high_access_tables = []
            for row in cursor.fetchall():
                if row[2] and int(row[2]) > 10000:  # 高读取量表
                    high_access_tables.append({
                        'table_schema': row[0],
                        'table_name': row[1],
                        'read_count': int(row[2])
                    })
        except Exception:
            high_access_tables = []
        
        # 对高访问量表分析可能缺失的索引
        for table in high_access_tables[:10]:
            try:
                # 获取表的列信息
                cursor.execute("""
                    SELECT COLUMN_NAME, COLUMN_KEY, DATA_TYPE
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                    ORDER BY ORDINAL_POSITION
                """, (table['table_schema'], table['table_name']))
                
                columns = cursor.fetchall()
                key_columns = [c[0] for c in columns if c[1] in ('PRI', 'UNI', 'MUL')]
                
                if not key_columns:
                    # 可能缺少主键索引
                    result['missing_indexes'].append({
                        'table_schema': table['table_schema'],
                        'table_name': table['table_name'],
                        'column_name': '（建议添加主键）',
                        'select_count': table['read_count'],
                        'rows_examined_avg': 0,
                        'recommendation': f"表 {table['table_schema']}.{table['table_name']} 缺少主键或索引，建议根据查询条件添加合适索引"
                    })
                    
            except Exception:
                pass
                
    except Exception:
        # performance_schema 可能未开启，忽略
        pass
    
    # ── 1.3 冗余/重复索引分析 ───────────────────────────────
    try:
        cursor.execute("""
            SELECT 
                a.TABLE_SCHEMA AS table_schema,
                a.TABLE_NAME AS table_name,
                a.INDEX_NAME AS index_name,
                a.COLUMN_NAME AS column_name,
                a.SEQ_IN_INDEX AS seq_in_index,
                b.INDEX_NAME AS idx2,
                b.COLUMN_NAME AS col2,
                b.SEQ_IN_INDEX AS seq2
            FROM information_schema.STATISTICS a
            JOIN information_schema.STATISTICS b
              ON a.TABLE_SCHEMA = b.TABLE_SCHEMA
             AND a.TABLE_NAME = b.TABLE_NAME
             AND a.INDEX_NAME < b.INDEX_NAME
             AND a.COLUMN_NAME = b.COLUMN_NAME
            WHERE a.TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
              AND a.NON_UNIQUE = 1
              AND b.NON_UNIQUE = 1
            ORDER BY a.TABLE_SCHEMA, a.TABLE_NAME, a.INDEX_NAME
        """)
        
        seen = set()
        for row in cursor.fetchall():
            key = (row[0], row[1], row[2], row[5])
            if key in seen:
                continue
            seen.add(key)
            
            # 检查是否冗余（同列、同顺序的索引，或左前缀匹配）
            result['redundant_indexes'].append({
                'table_schema': row[0],
                'table_name': row[1],
                'index1': row[2],
                'index2': row[5],
                'reason': f"列 {row[3]} 被索引 {row[2]} 和 {row[5]} 同时覆盖",
                'recommendation': f"考虑删除冗余索引 {row[2]} 或 {row[5]}（保留更短或更有选择性的）"
            })
        
        # 检查主键和唯一索引的冗余
        cursor.execute("""
            SELECT 
                TABLE_SCHEMA AS table_schema,
                TABLE_NAME AS table_name,
                INDEX_NAME AS index_name,
                COLUMN_NAME AS column_name,
                NON_UNIQUE AS non_unique
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
              AND INDEX_NAME IN ('PRIMARY', 'uniq')
            ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
        """)
        
        # 查找相同前缀的冗余索引
        prev_key = None
        prev_row = None
        for row in cursor.fetchall():
            key = (row[0], row[1])
            if prev_key == key:
                # 同表有两个唯一索引
                result['redundant_indexes'].append({
                    'table_schema': row[0],
                    'table_name': row[1],
                    'index1': prev_row[2],
                    'index2': row[2],
                    'reason': f"表存在多个唯一索引",
                    'recommendation': f"检查是否需要同时保留 {prev_row[2]} 和 {row[2]}"
                })
            prev_key = key
            prev_row = row
                
    except Exception:
        pass
    
    # ── 1.4 未使用索引分析 ──────────────────────────────────
    try:
        # 尝试从 table_statistics 获取未使用的索引
        cursor.execute("""
            SELECT 
                OBJECT_SCHEMA AS table_schema,
                OBJECT_NAME AS table_name,
                INDEX_NAME AS index_name,
                COUNT_FETCH AS fetch_count,
                COUNT_INSERT AS insert_count,
                COUNT_UPDATE AS update_count,
                COUNT_DELETE AS delete_count
            FROM performance_schema.table_statistics
            WHERE OBJECT_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
              AND INDEX_NAME IS NOT NULL
              AND INDEX_NAME != 'PRIMARY'
            HAVING COUNT_FETCH = 0 AND COUNT_INSERT = 0 AND COUNT_UPDATE = 0 AND COUNT_DELETE = 0
            LIMIT 50
        """)
        
        for row in cursor.fetchall():
            # 获取索引大小
            index_size_mb = 0.0
            try:
                cursor.execute("""
                    SELECT ROUND(SUM(INDEX_LENGTH) / 1024 / 1024, 2)
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                """, (row[0], row[1]))
                size_row = cursor.fetchone()
                index_size_mb = float(size_row[0]) if size_row and size_row[0] else 0.0
            except Exception:
                pass
            
            result['unused_indexes'].append({
                'table_schema': row[0],
                'table_name': row[1],
                'index_name': row[2],
                'last_used': '未知（performance_schema 记录）',
                'days_unused': days_threshold + 1,
                'index_size_mb': index_size_mb,
                'recommendation': f"索引 {row[2]} 长时间未被使用（查询/插入/更新/删除均为 0），建议评估是否可删除以减少维护开销"
            })
    except Exception:
        pass
    
    # 如果 performance_schema.table_statistics 不可用，使用替代方案
    if not result['unused_indexes']:
        try:
            # 从 information_schema 获取所有索引
            cursor.execute("""
                SELECT 
                    TABLE_SCHEMA AS table_schema,
                    TABLE_NAME AS table_name,
                    INDEX_NAME AS index_name,
                    TABLE_ROWS AS estimated_rows
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
                  AND TABLE_ROWS > 10000
                  AND INDEX_NAME IS NOT NULL
                ORDER BY TABLE_ROWS DESC
                LIMIT 100
            """)
            
            for row in cursor.fetchall():
                if row[3] and int(row[3]) > 100000:  # 大表上的索引更值得关注
                    result['unused_indexes'].append({
                        'table_schema': row[0],
                        'table_name': row[1],
                        'index_name': row[2],
                        'last_used': '未知（无法获取）',
                        'days_unused': -1,  # 未知
                        'index_size_mb': 0.0,
                        'recommendation': f"建议开启 performance_schema.table_statistics 以精确追踪索引使用情况"
                    })
        except Exception:
            pass
    
    # ── 1.5 获取总索引数 ────────────────────────────────────
    try:
        cursor.execute("""
            SELECT COUNT(*) AS total_indexes
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
              AND INDEX_NAME != 'PRIMARY'
        """)
        row = cursor.fetchone()
        result['summary']['total_indexes'] = int(row[0]) if row else 0
    except Exception:
        pass
    
    cursor.close()
    
    # 更新汇总
    result['summary']['missing_count'] = len(result['missing_indexes'])
    result['summary']['redundant_count'] = len(result['redundant_indexes'])
    result['summary']['unused_count'] = len(result['unused_indexes'])
    
    return result


# ═══════════════════════════════════════════════════════
#  2. PostgreSQL 索引健康分析
# ═══════════════════════════════════════════════════════

def analyze_pg_indexes(conn, days_threshold=90):
    """
    分析 PostgreSQL 索引健康状况。
    
    参数:
        conn: psycopg2 数据库连接
        days_threshold: 未使用索引的判定天数阈值
    
    返回格式同 MySQL。
    """
    result = {
        'missing_indexes': [],
        'redundant_indexes': [],
        'unused_indexes': [],
        'summary': {
            'missing_count': 0,
            'redundant_count': 0,
            'unused_count': 0,
            'total_indexes': 0,
            'db_size_gb': 0.0,
        }
    }
    
    cursor = conn.cursor()
    
    # ── 2.1 获取数据库总大小 ───────────────────────────────
    try:
        cursor.execute("""
            SELECT ROUND(SUM(pg_database_size(datname)) / 1024 / 1024 / 1024, 2) AS db_size_gb
            FROM pg_database
            WHERE datname NOT IN ('postgres', 'template0', 'template1')
        """)
        row = cursor.fetchone()
        result['summary']['db_size_gb'] = float(row[0]) if row and row[0] else 0.0
    except Exception:
        pass
    
    # ── 2.2 缺失索引分析 ────────────────────────────────────
    # 分析 pg_stat_statements 中高消耗查询
    try:
        cursor.execute("""
            SELECT 
                LEFT(query, 200) AS query_preview,
                calls,
                total_exec_time / calls AS avg_time_ms,
                rows / calls AS avg_rows
            FROM pg_stat_statements
            WHERE query LIKE '%%SELECT%%'
              AND calls > 100
            ORDER BY total_exec_time / calls DESC
            LIMIT 30
        """)
        
        for row in cursor.fetchall():
            query_text = row[0] or ''
            avg_rows = int(row[3]) if row[3] else 0
            
            # 高扫描行数可能是缺少索引
            if avg_rows > 10000:
                result['missing_indexes'].append({
                    'table_schema': 'public',
                    'table_name': '（从查询分析）',
                    'column_name': '（建议分析慢查询）',
                    'select_count': int(row[1]),
                    'rows_examined_avg': avg_rows,
                    'recommendation': f"查询平均扫描 {avg_rows} 行，建议分析具体表结构并添加合适索引：{query_text[:100]}..."
                })
    except Exception:
        # pg_stat_statements 可能未开启
        pass
    
    # ── 2.3 冗余/重复索引分析 ───────────────────────────────
    try:
        cursor.execute("""
            SELECT 
                schemaname AS table_schema,
                tablename AS table_name,
                indexname AS index_name,
                indexdef AS index_def
            FROM pg_indexes
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY schemaname, tablename
        """)
        
        indexes = cursor.fetchall()
        index_map = {}  # (schema, table) -> list of (index_name, columns)
        
        for idx in indexes:
            key = (idx[0], idx[1])
            if key not in index_map:
                index_map[key] = []
            
            # 提取索引列（简化解析）
            index_def = idx[3] or ''
            cols_start = index_def.find('(')
            cols_end = index_def.find(')')
            if cols_start > 0 and cols_end > cols_start:
                cols = index_def[cols_start+1:cols_end].replace('"', '').lower()
                index_map[key].append((idx[2], cols))
        
        # 查找冗余索引
        for (schema, table), indexes_list in index_map.items():
            for i in range(len(indexes_list)):
                for j in range(i+1, len(indexes_list)):
                    idx1_name, idx1_cols = indexes_list[i]
                    idx2_name, idx2_cols = indexes_list[j]
                    
                    # 检查是否有包含关系
                    if idx1_cols == idx2_cols:
                        result['redundant_indexes'].append({
                            'table_schema': schema,
                            'table_name': table,
                            'index1': idx1_name,
                            'index2': idx2_name,
                            'reason': f"索引列完全相同：({idx1_cols})",
                            'recommendation': f"考虑删除冗余索引 {idx1_name} 或 {idx2_name}"
                        })
                    elif idx1_cols.startswith(idx2_cols) or idx2_cols.startswith(idx1_cols):
                        result['redundant_indexes'].append({
                            'table_schema': schema,
                            'table_name': table,
                            'index1': idx1_name,
                            'index2': idx2_name,
                            'reason': f"索引列存在包含关系：({idx1_cols}) vs ({idx2_cols})",
                            'recommendation': f"考虑删除被包含的冗余索引"
                        })
    except Exception:
        pass
    
    # ── 2.4 未使用索引分析 ──────────────────────────────────
    try:
        cursor.execute("""
            SELECT 
                schemaname AS table_schema,
                relname AS table_name,
                indexrelname AS index_name,
                idx_scan AS scan_count,
                pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
            FROM pg_stat_user_indexes
            WHERE idx_scan = 0
              AND schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY pg_relation_size(indexrelid) DESC
            LIMIT 50
        """)
        
        for row in cursor.fetchall():
            result['unused_indexes'].append({
                'table_schema': row[0],
                'table_name': row[1],
                'index_name': row[2],
                'last_used': '从未使用（idx_scan=0）',
                'days_unused': days_threshold + 1,
                'index_size_mb': 0.0,  # 需要额外查询
                'recommendation': f"索引 {row[2]} 从未被使用，建议评估是否可删除"
            })
    except Exception:
        pass
    
    # ── 2.5 获取总索引数 ────────────────────────────────────
    try:
        cursor.execute("""
            SELECT COUNT(*) AS total_indexes
            FROM pg_indexes
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
              AND indexname NOT LIKE '%_pkey'
        """)
        row = cursor.fetchone()
        result['summary']['total_indexes'] = int(row[0]) if row else 0
    except Exception:
        pass
    
    cursor.close()
    
    # 更新汇总
    result['summary']['missing_count'] = len(result['missing_indexes'])
    result['summary']['redundant_count'] = len(result['redundant_indexes'])
    result['summary']['unused_count'] = len(result['unused_indexes'])
    
    return result


# ═══════════════════════════════════════════════════════
#  3. Oracle 索引健康分析
# ═══════════════════════════════════════════════════════

def analyze_oracle_indexes(conn, days_threshold=90):
    """
    分析 Oracle 索引健康状况。
    """
    result = {
        'missing_indexes': [],
        'redundant_indexes': [],
        'unused_indexes': [],
        'summary': {
            'missing_count': 0,
            'redundant_count': 0,
            'unused_count': 0,
            'total_indexes': 0,
            'db_size_gb': 0.0,
        }
    }
    cursor = conn.cursor()

    # ── 3.1 获取数据库总大小 ───────────────────────────────
    try:
        cursor.execute("""
            SELECT ROUND(SUM(bytes) / 1024 / 1024 / 1024, 2)
            FROM dba_data_files
        """)
        row = cursor.fetchone()
        result['summary']['db_size_gb'] = float(row[0]) if row and row[0] else 0.0
    except Exception:
        pass

    # ── 3.2 未使用索引分析 ──────────────────────────────────
    try:
        # Oracle 11g+: v$object_usage tracks index usage
        cursor.execute("""
            SELECT o.owner, o.table_name, o.index_name, o.used,
                   i.index_type, i.uniqueness
            FROM dba_indexes i
            LEFT JOIN dba_object_usage o ON i.owner = o.owner AND i.index_name = o.index_name
            WHERE i.owner NOT IN ('SYS', 'SYSTEM', 'WMSYS', 'EXFSYS', 'DBSNMP')
              AND i.index_type NOT IN ('LOB', 'IOT_TOP')
            ORDER BY i.owner, i.table_name
        """)

        for row in cursor.fetchall():
            owner, table_name, index_name, used, index_type, uniqueness = row
            is_used = used == 'YES' if used else None

            if is_used is False:
                result['unused_indexes'].append({
                    'table_schema': owner,
                    'table_name': table_name,
                    'index_name': index_name,
                    'last_used': '从未使用',
                    'days_unused': days_threshold + 1,
                    'index_size_mb': 0.0,
                    'recommendation': f"索引 {index_name} 从未被使用，建议评估是否可删除"
                })
            elif is_used is None:
                # monitoring not enabled
                pass
    except Exception:
        pass

    # ── 3.3 冗余索引分析 ────────────────────────────────────
    try:
        cursor.execute("""
            SELECT a.owner, a.table_name, a.index_name, a.column_name,
                   b.index_name AS idx2, b.column_name AS col2
            FROM dba_ind_columns a
            JOIN dba_ind_columns b ON a.index_owner = b.index_owner
                AND a.table_name = b.table_name
                AND a.index_name < b.index_name
                AND a.column_position = b.column_position
                AND a.column_name = b.column_name
            WHERE a.index_owner NOT IN ('SYS', 'SYSTEM')
            ORDER BY a.owner, a.table_name
        """)

        seen = set()
        for row in cursor.fetchall():
            key = (row[0], row[1], row[2], row[4])
            if key in seen:
                continue
            seen.add(key)

            result['redundant_indexes'].append({
                'table_schema': row[0],
                'table_name': row[1],
                'index1': row[2],
                'index2': row[4],
                'reason': f"索引列相同: {row[3]}",
                'recommendation': f"索引 {row[2]} 和 {row[4]} 有相同首列，建议删除冗余"
            })
    except Exception:
        pass

    # ── 3.4 总索引数 ────────────────────────────────────────
    try:
        cursor.execute("""
            SELECT COUNT(*) FROM dba_indexes
            WHERE owner NOT IN ('SYS', 'SYSTEM')
              AND index_type NOT IN ('LOB')
        """)
        row = cursor.fetchone()
        result['summary']['total_indexes'] = int(row[0]) if row else 0
    except Exception:
        pass

    cursor.close()
    result['summary']['missing_count'] = len(result['missing_indexes'])
    result['summary']['redundant_count'] = len(result['redundant_indexes'])
    result['summary']['unused_count'] = len(result['unused_indexes'])
    return result


# ═══════════════════════════════════════════════════════
#  4. DM8 索引健康分析
# ═══════════════════════════════════════════════════════

def analyze_dm_indexes(conn, days_threshold=90):
    """
    分析 DM8 达梦数据库索引健康状况。
    """
    result = {
        'missing_indexes': [],
        'redundant_indexes': [],
        'unused_indexes': [],
        'summary': {
            'missing_count': 0,
            'redundant_count': 0,
            'unused_count': 0,
            'total_indexes': 0,
            'db_size_gb': 0.0,
        }
    }
    cursor = conn.cursor()

    # ── 4.1 获取数据库总大小 ───────────────────────────────
    try:
        cursor.execute("""
            SELECT ROUND(SUM(PAGES)*8192 / 1024 / 1024 / 1024, 2)
            FROM V$DATAFILE
        """)
        row = cursor.fetchone()
        result['summary']['db_size_gb'] = float(row[0]) if row and row[0] else 0.0
    except Exception:
        pass

    # ── 4.2 冗余索引分析 ────────────────────────────────────
    try:
        cursor.execute("""
            SELECT USER_NAME, TABLE_NAME, INDEX_NAME, COLUMN_NAME, INDEX_POSITION
            FROM USER_IND_COLUMNS
            ORDER BY USER_NAME, TABLE_NAME, INDEX_NAME, INDEX_POSITION
        """)

        indexes = cursor.fetchall()
        index_map = {}  # (user, table) -> list of (idx_name, cols_str)

        for row in indexes:
            user, table, idx_name, col_name, pos = row
            key = (user, table)
            if key not in index_map:
                index_map[key] = []
            if pos == 1:
                index_map[key].append([idx_name, col_name])
            elif pos > 1 and index_map[key]:
                index_map[key][-1][1] += ',' + col_name

        for (user, table), idx_list in index_map.items():
            for i in range(len(idx_list)):
                for j in range(i+1, len(idx_list)):
                    cols1 = idx_list[i][1]
                    cols2 = idx_list[j][1]
                    if cols1 == cols2 or cols1.startswith(cols2) or cols2.startswith(cols1):
                        result['redundant_indexes'].append({
                            'table_schema': user,
                            'table_name': table,
                            'index1': idx_list[i][0],
                            'index2': idx_list[j][0],
                            'reason': f"索引列相同或包含: ({cols1}) vs ({cols2})",
                            'recommendation': f"建议删除冗余索引"
                        })
    except Exception:
        pass

    # ── 4.3 总索引数 ────────────────────────────────────────
    try:
        cursor.execute("SELECT COUNT(*) FROM USER_INDEXES")
        row = cursor.fetchone()
        result['summary']['total_indexes'] = int(row[0]) if row else 0
    except Exception:
        pass

    cursor.close()
    result['summary']['missing_count'] = len(result['missing_indexes'])
    result['summary']['redundant_count'] = len(result['redundant_indexes'])
    result['summary']['unused_count'] = len(result['unused_indexes'])
    return result


# ═══════════════════════════════════════════════════════
#  5. SQL Server 索引健康分析
# ═══════════════════════════════════════════════════════

def analyze_sqlserver_indexes(conn, days_threshold=90):
    """
    分析 SQL Server 索引健康状况。
    """
    result = {
        'missing_indexes': [],
        'redundant_indexes': [],
        'unused_indexes': [],
        'summary': {
            'missing_count': 0,
            'redundant_count': 0,
            'unused_count': 0,
            'total_indexes': 0,
            'db_size_gb': 0.0,
        }
    }
    cursor = conn.cursor()

    # ── 5.1 获取数据库总大小 ───────────────────────────────
    try:
        cursor.execute("""
            SELECT ISNULL(SUM(size) * 8192.0 / 1024 / 1024 / 1024, 0)
            FROM sys.master_files
            WHERE database_id > 4
        """)
        row = cursor.fetchone()
        result['summary']['db_size_gb'] = float(row[0]) if row and row[0] else 0.0
    except Exception:
        pass

    # ── 5.2 未使用索引分析 ──────────────────────────────────
    try:
        cursor.execute("""
            SELECT OBJECT_NAME(s.object_id) AS table_name,
                   i.name AS index_name,
                   i.type_desc,
                   ISNULL(s.user_seeks, 0) + ISNULL(s.user_scans, 0) AS total_reads
            FROM sys.indexes i
            LEFT JOIN sys.dm_db_index_usage_stats s
                ON i.object_id = s.object_id AND i.index_id = s.index_id
            WHERE i.object_id > 1000
              AND i.type > 0
              AND i.is_primary_key = 0
              AND i.is_unique = 0
              AND (ISNULL(s.user_seeks, 0) + ISNULL(s.user_scans, 0)) = 0
            ORDER BY total_reads
        """)

        for row in cursor.fetchall():
            table_name, index_name, type_desc, total_reads = row
            result['unused_indexes'].append({
                'table_schema': 'dbo',
                'table_name': table_name,
                'index_name': index_name,
                'last_used': '从未使用',
                'days_unused': days_threshold + 1,
                'index_size_mb': 0.0,
                'recommendation': f"索引 {index_name} ({type_desc}) 从未被使用，建议评估删除"
            })
    except Exception:
        pass

    # ── 5.3 冗余索引分析 ────────────────────────────────────
    try:
        cursor.execute("""
            SELECT a.name AS table_name,
                   i1.name AS index1, i1.type_desc AS type1,
                   i2.name AS index2, i2.type_desc AS type2,
                   c1.name AS col1, c2.name AS col2
            FROM sys.indexes i1
            JOIN sys.indexes i2 ON i1.object_id = i2.object_id AND i1.index_id < i2.index_id
            JOIN sys.index_columns ic1 ON i1.object_id = ic1.object_id AND i1.index_id = ic1.index_id
            JOIN sys.index_columns ic2 ON i2.object_id = ic2.object_id AND i2.index_id = ic2.index_id
            JOIN sys.columns c1 ON ic1.object_id = c1.object_id AND ic1.column_id = c1.column_id
            JOIN sys.columns c2 ON ic2.object_id = c2.object_id AND ic2.column_id = c2.column_id
            WHERE i1.object_id > 1000 AND i1.type > 0 AND i2.type > 0
              AND ic1.key_ordinal = 1 AND ic2.key_ordinal = 1
              AND c1.name = c2.name
        """)

        seen = set()
        for row in cursor.fetchall():
            table, idx1, type1, idx2, type2, col1, col2 = row
            key = (table, idx1, idx2)
            if key in seen:
                continue
            seen.add(key)

            result['redundant_indexes'].append({
                'table_schema': 'dbo',
                'table_name': table,
                'index1': idx1,
                'index2': idx2,
                'reason': f"首列相同: {col1}",
                'recommendation': f"索引 {idx1} 和 {idx2} 首列相同，建议删除冗余"
            })
    except Exception:
        pass

    # ── 5.4 总索引数 ────────────────────────────────────────
    try:
        cursor.execute("""
            SELECT COUNT(*) FROM sys.indexes
            WHERE object_id > 1000 AND type > 0
        """)
        row = cursor.fetchone()
        result['summary']['total_indexes'] = int(row[0]) if row else 0
    except Exception:
        pass

    cursor.close()
    result['summary']['missing_count'] = len(result['missing_indexes'])
    result['summary']['redundant_count'] = len(result['redundant_indexes'])
    result['summary']['unused_count'] = len(result['unused_indexes'])
    return result


# ═══════════════════════════════════════════════════════
#  6. TiDB 索引健康分析
# ═══════════════════════════════════════════════════════

def analyze_tidb_indexes(conn, days_threshold=90):
    """
    分析 TiDB 索引健康状况（兼容 MySQL 8.0）。
    """
    result = {
        'missing_indexes': [],
        'redundant_indexes': [],
        'unused_indexes': [],
        'summary': {
            'missing_count': 0,
            'redundant_count': 0,
            'unused_count': 0,
            'total_indexes': 0,
            'db_size_gb': 0.0,
        }
    }
    cursor = conn.cursor()

    # ── 6.1 获取数据库总大小 ───────────────────────────────
    try:
        cursor.execute("""
            SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024 / 1024, 2)
            FROM information_schema.tables
        """)
        row = cursor.fetchone()
        result['summary']['db_size_gb'] = float(row[0]) if row and row[0] else 0.0
    except Exception:
        pass

    # ── 6.2 冗余索引分析 ────────────────────────────────────
    try:
        cursor.execute("""
            SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'PERFORMANCE_SCHEMA', 'sys')
              AND NON_UNIQUE = 1
            ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
        """)

        index_map = {}
        for row in cursor.fetchall():
            schema, table, idx_name, col_name, seq = row
            key = (schema, table)
            if key not in index_map:
                index_map[key] = []
            if seq == 1:
                index_map[key].append([idx_name, col_name])
            elif index_map[key]:
                index_map[key][-1][1] += ',' + col_name

        for (schema, table), idx_list in index_map.items():
            for i in range(len(idx_list)):
                for j in range(i+1, len(idx_list)):
                    cols1 = idx_list[i][1]
                    cols2 = idx_list[j][1]
                    if cols1 == cols2 or cols1.startswith(cols2) or cols2.startswith(cols1):
                        result['redundant_indexes'].append({
                            'table_schema': schema,
                            'table_name': table,
                            'index1': idx_list[i][0],
                            'index2': idx_list[j][0],
                            'reason': f"索引列相同或包含: ({cols1}) vs ({cols2})",
                            'recommendation': f"建议删除冗余索引"
                        })
    except Exception:
        pass

    # ── 6.3 总索引数 ────────────────────────────────────────
    try:
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'PERFORMANCE_SCHEMA', 'sys')
        """)
        row = cursor.fetchone()
        result['summary']['total_indexes'] = int(row[0]) if row else 0
    except Exception:
        pass

    cursor.close()
    result['summary']['missing_count'] = len(result['missing_indexes'])
    result['summary']['redundant_count'] = len(result['redundant_indexes'])
    result['summary']['unused_count'] = len(result['unused_indexes'])
    return result


# ═══════════════════════════════════════════════════════
#  7. 统一入口函数
# ═══════════════════════════════════════════════════════

def get_index_health(db_type, conn, days_threshold=90):
    """
    统一索引健康分析入口。

    参数:
        db_type: 数据库类型 ('mysql', 'pg', 'oracle', 'dm', 'sqlserver', 'tidb')
        conn: 数据库连接对象
        days_threshold: 未使用索引判定天数

    返回:
        索引健康分析报告字典
    """
    if db_type == 'mysql':
        return analyze_mysql_indexes(conn, days_threshold)
    elif db_type == 'pg':
        return analyze_pg_indexes(conn, days_threshold)
    elif db_type == 'oracle':
        return analyze_oracle_indexes(conn, days_threshold)
    elif db_type == 'dm':
        return analyze_dm_indexes(conn, days_threshold)
    elif db_type == 'sqlserver':
        return analyze_sqlserver_indexes(conn, days_threshold)
    elif db_type == 'tidb':
        return analyze_tidb_indexes(conn, days_threshold)
    else:
        return None


def format_index_health_report(report, db_type='mysql'):
    """
    格式化索引健康分析报告为可读文本。
    """
    if not report:
        return "不支持的数据库类型"
    
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"  {db_type.upper()} 索引健康分析报告")
    lines.append(f"{'='*60}")
    lines.append(f"\n索引统计:")
    lines.append(f"  总索引数:     {report['summary']['total_indexes']}")
    lines.append(f"  缺失索引:     {report['summary']['missing_count']}")
    lines.append(f"  冗余索引:     {report['summary']['redundant_count']}")
    lines.append(f"  未使用索引:   {report['summary']['unused_count']}")
    lines.append(f"  数据库大小:   {report['summary']['db_size_gb']:.2f} GB")
    
    # 缺失索引
    if report['missing_indexes']:
        lines.append(f"\n{'─'*60}")
        lines.append("【缺失索引】")
        for idx in report['missing_indexes'][:10]:
            lines.append(f"\n  表: {idx['table_schema']}.{idx['table_name']}")
            lines.append(f"  列: {idx['column_name']}")
            lines.append(f"  SELECT 次数: {idx.get('select_count', 'N/A')}")
            lines.append(f"  建议: {idx['recommendation']}")
    
    # 冗余索引
    if report['redundant_indexes']:
        lines.append(f"\n{'─'*60}")
        lines.append("【冗余索引】")
        for idx in report['redundant_indexes'][:10]:
            lines.append(f"\n  表: {idx['table_schema']}.{idx['table_name']}")
            lines.append(f"  索引: {idx['index1']} <-> {idx['index2']}")
            lines.append(f"  原因: {idx['reason']}")
            lines.append(f"  建议: {idx['recommendation']}")
    
    # 未使用索引
    if report['unused_indexes']:
        lines.append(f"\n{'─'*60}")
        lines.append("【未使用索引】")
        for idx in report['unused_indexes'][:10]:
            lines.append(f"\n  表: {idx['table_schema']}.{idx['table_name']}")
            lines.append(f"  索引: {idx['index_name']}")
            lines.append(f"  最后使用: {idx['last_used']}")
            lines.append(f"  建议: {idx['recommendation']}")
    
    lines.append(f"\n{'='*60}")
    lines.append("说明:")
    lines.append("  缺失索引: 查询频繁但缺少索引的列，可能导致全表扫描")
    lines.append("  冗余索引: 同一列被多个索引覆盖，增加写入开销")
    lines.append("  未使用索引: 长时间未被使用，但仍消耗存储和维护开销")
    lines.append("="*60)
    
    return '\n'.join(lines)
