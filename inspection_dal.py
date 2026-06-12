#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
数据库巡检配置的数据访问层（DAL）。

这个模块封装了所有与巡检配置相关的数据库操作，包括：
- 巡检模板的 CRUD 操作
- 章节的 CRUD 操作
- SQL 查询的 CRUD 操作
- 导入/导出功能
- 历史记录功能
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Any
from config_baseline import _get_default_baselines


# 数据库文件路径
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'inspection.db')


def get_db_connection(db_path: str = None) -> sqlite3.Connection:
    """
    获取数据库连接。
    
    :param db_path: 数据库文件路径，如果为 None，则使用默认路径
    :return: sqlite3.Connection 对象
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 使结果可以通过列名访问
    conn.execute("PRAGMA foreign_keys = ON")  # 启用外键约束
    return conn


def init_database(db_path: str = None):
    """
    初始化数据库，创建所有必要的表。
    
    :param db_path: 数据库文件路径，如果为 None，则使用默认路径
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        # 创建巡检模板表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inspection_template (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                db_type VARCHAR(50) NOT NULL,
                template_name_zh VARCHAR(200) NOT NULL,
                template_name_en VARCHAR(200),
                version VARCHAR(50) DEFAULT 'v1',
                description TEXT,
                is_default INTEGER DEFAULT 0,
                is_preset INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(db_type, template_name_zh)
            )
        """)
        
        # 创建章节表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inspection_chapter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                chapter_number INTEGER NOT NULL,
                chapter_title_zh VARCHAR(200) NOT NULL,
                chapter_title_en VARCHAR(200),
                description TEXT,
                enabled INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (template_id) REFERENCES inspection_template(id) ON DELETE CASCADE,
                UNIQUE(template_id, chapter_number)
            )
        """)
        
        # 创建 SQL 查询表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inspection_query (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL,
                query_key VARCHAR(100) NOT NULL,
                query_sql TEXT NOT NULL,
                query_description_zh TEXT,
                query_description_en TEXT,
                enabled INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chapter_id) REFERENCES inspection_chapter(id) ON DELETE CASCADE,
                UNIQUE(chapter_id, query_key)
            )
        """)
        
        # 创建修改历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inspection_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name VARCHAR(50) NOT NULL,
                record_id INTEGER NOT NULL,
                action VARCHAR(20) NOT NULL,
                old_value TEXT,
                new_value TEXT,
                modified_by VARCHAR(100),
                modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建基线配置表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inspection_baseline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                db_type VARCHAR(50) NOT NULL,
                param_name VARCHAR(100) NOT NULL,
                query_sql TEXT NOT NULL,
                operator VARCHAR(10) NOT NULL DEFAULT '=',
                expected_value VARCHAR(500),
                expected_value_min VARCHAR(500),
                expected_value_max VARCHAR(500),
                risk_level VARCHAR(20) DEFAULT 'MEDIUM',
                description_zh TEXT,
                description_en TEXT,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建索引以提高查询性能
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_template_db_type ON inspection_template(db_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chapter_template_id ON inspection_chapter(template_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_query_chapter_id ON inspection_query(chapter_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_table_record ON inspection_history(table_name, record_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_baseline_db_type ON inspection_baseline(db_type)")

        # 迁移：为已有数据库添加 version 字段（如果不存在）
        cursor.execute("PRAGMA table_info(inspection_template)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'version' not in columns:
            cursor.execute("ALTER TABLE inspection_template ADD COLUMN version VARCHAR(50) DEFAULT 'v1'")
            print("🔄 已为 inspection_template 表添加 version 字段")
        if 'is_preset' not in columns:
            cursor.execute("ALTER TABLE inspection_template ADD COLUMN is_preset INTEGER DEFAULT 0")
            print("🔄 已为 inspection_template 表添加 is_preset 字段")
        # is_preset 由 create_template 时显式指定，不应根据 is_default 自动设置
        # is_default（巡检默认选中）和 is_preset（系统预置不可删除）是两个独立概念
        # Oracle 11g 模板也是预置模板（is_default=0 但同样不可删除）
        cursor.execute("UPDATE inspection_template SET is_preset = 1 WHERE db_type = 'oracle' AND version = '11g'")
        conn.commit()

        # 迁移：为 inspection_query 表补充缺失的列（旧版数据库可能没有）
        cursor.execute("PRAGMA table_info(inspection_query)")
        query_columns = [row[1] for row in cursor.fetchall()]
        if query_columns:
            if 'query_sql' not in query_columns:
                cursor.execute("ALTER TABLE inspection_query ADD COLUMN query_sql TEXT NOT NULL DEFAULT ''")
                print("🔄 已为 inspection_query 表添加 query_sql 字段")
            if 'query_description_zh' not in query_columns:
                cursor.execute("ALTER TABLE inspection_query ADD COLUMN query_description_zh TEXT")
                print("🔄 已为 inspection_query 表添加 query_description_zh 字段")
            if 'query_description_en' not in query_columns:
                cursor.execute("ALTER TABLE inspection_query ADD COLUMN query_description_en TEXT")
                print("🔄 已为 inspection_query 表添加 query_description_en 字段")
            if 'enabled' not in query_columns:
                cursor.execute("ALTER TABLE inspection_query ADD COLUMN enabled INTEGER DEFAULT 1")
                print("🔄 已为 inspection_query 表添加 enabled 字段")
            if 'sort_order' not in query_columns:
                cursor.execute("ALTER TABLE inspection_query ADD COLUMN sort_order INTEGER DEFAULT 0")
                print("🔄 已为 inspection_query 表添加 sort_order 字段")
            if 'created_at' not in query_columns:
                cursor.execute("ALTER TABLE inspection_query ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                print("🔄 已为 inspection_query 表添加 created_at 字段")
            if 'updated_at' not in query_columns:
                cursor.execute("ALTER TABLE inspection_query ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                print("🔄 已为 inspection_query 表添加 updated_at 字段")
            conn.commit()

        conn.commit()
        print("[OK] 数据库初始化成功")
        
    except Exception as e:
        conn.rollback()
        print(f"[FAIL] 数据库初始化失败: {e}")
        raise
    finally:
        conn.close()


# ==================== 巡检模板操作 ====================

def create_template(db_type: str, template_name: str, description: str = None,
                   template_name_en: str = None, version: str = 'v1', is_default: int = 0, is_preset: int = 0, db_path: str = None) -> int:
    """
    创建新的巡检模板。

    :param db_type: 数据库类型（'mysql', 'postgresql', etc.）
    :param template_name: 模板名称
    :param description: 模板描述
    :param template_name_en: 模板英文名称
    :param version: 模板版本号（默认 'v1'）
    :param is_default: 是否默认模板（0=否，1=是）
    :param is_preset: 是否预置模板（0=否，1=是）
    :param db_path: 数据库文件路径
    :return: 新创建的模板 ID
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    try:
        if template_name_en:
            cursor.execute("""
                INSERT INTO inspection_template (db_type, template_name_zh, template_name_en, version, description, is_default, is_preset)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (db_type, template_name, template_name_en, version, description, is_default, is_preset))
        else:
            cursor.execute("""
                INSERT INTO inspection_template (db_type, template_name_zh, version, description, is_default, is_preset)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (db_type, template_name, version, description, is_default, is_preset))
        
        template_id = cursor.lastrowid
        conn.commit()
        
        # 记录历史
        _record_history(cursor, 'inspection_template', template_id, 'INSERT', 
                       None, {'db_type': db_type, 'template_name': template_name})
        conn.commit()
        
        return template_id
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_template(template_id: int, db_path: str = None) -> Optional[Dict]:
    """
    获取指定 ID 的巡检模板。
    
    :param template_id: 模板 ID
    :param db_path: 数据库文件路径
    :return: 模板信息字典，如果不存在则返回 None
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT id,
                   template_name_zh as template_name,
                   template_name_en,
                   db_type,
                   version,
                   description,
                   is_default,
                   is_preset,
                   created_at,
                   updated_at
            FROM inspection_template
            WHERE id = ?
        """, (template_id,))

        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    finally:
        conn.close()


def get_all_templates(db_path: str = None) -> List[Dict]:
    """
    获取所有巡检模板。
    
    :param db_path: 数据库文件路径
    :return: 模板信息字典列表
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT t.id,
                   t.template_name_zh as template_name,
                   t.template_name_en,
                   t.db_type,
                   t.version,
                   t.is_default,
                   t.is_preset,
                   t.created_at,
                   COUNT(DISTINCT c.id) as chapter_count,
                   COUNT(DISTINCT q.id) as query_count
            FROM inspection_template t
            LEFT JOIN inspection_chapter c ON t.id = c.template_id AND c.enabled = 1
            LEFT JOIN inspection_query q ON c.id = q.chapter_id AND q.enabled = 1
            GROUP BY t.id
            ORDER BY t.db_type, t.is_default DESC, t.template_name_zh
        """)
        
        return [dict(row) for row in cursor.fetchall()]
        
    finally:
        conn.close()


def get_templates_by_db_type(db_type: str, db_path: str = None) -> List[Dict]:
    """
    获取指定数据库类型的所有巡检模板。
    
    :param db_type: 数据库类型
    :param db_path: 数据库文件路径
    :return: 模板信息字典列表
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT id,
                   template_name_zh as template_name,
                   template_name_en,
                   db_type,
                   version,
                   description,
                   is_default,
                   is_preset,
                   created_at,
                   updated_at
            FROM inspection_template
            WHERE db_type = ?
            ORDER BY is_default DESC, version DESC, template_name_zh
        """, (db_type,))
        
        return [dict(row) for row in cursor.fetchall()]
        
    finally:
        conn.close()


def get_default_template(db_type: str, db_path: str = None,
                         db_version_major: int = None) -> Optional[Dict]:
    """
    获取指定数据库类型的默认模板。
    Oracle 支持按版本区分：db_version_major<=11 时匹配 version='11g' 的默认模板，
    其他版本匹配 version!='11g' 的默认模板。

    :param db_type: 数据库类型
    :param db_path: 数据库文件路径
    :param db_version_major: 数据库主版本号（仅 Oracle 使用，如 11、12、19）
    :return: 默认模板信息字典，如果不存在则返回 None
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    try:
        sql = """
            SELECT id,
                   template_name_zh as template_name,
                   template_name_en,
                   db_type,
                   version,
                   description,
                   is_default,
                   is_preset,
                   created_at,
                   updated_at
            FROM inspection_template
            WHERE db_type = ? AND is_default = 1
        """
        params = [db_type]

        # Oracle 按版本分组匹配默认模板
        if db_type == 'oracle' and db_version_major is not None:
            if db_version_major <= 11:
                sql += " AND version = '11g'"
            else:
                sql += " AND (version IS NULL OR version != '11g')"

        sql += " LIMIT 1"
        cursor.execute(sql, params)
        
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
        
    finally:
        conn.close()


def update_template(template_id: int, template_name: str = None,
                   version: str = None, description: str = None, is_default: int = None,
                   db_path: str = None) -> bool:
    """
    更新巡检模板。
    预置模板（is_preset=1）不能修改模板名称和版本号。
    设为默认时（is_default=1），自动取消同数据库类型其他模板的默认。
    Oracle 特殊处理：11g 模板和非 11g 模板各有一个独立的默认值。

    :param template_id: 模板 ID
    :param template_name: 新的模板名称（如果为 None，则不更新）
    :param version: 新的版本号（如果为 None，则不更新）
    :param description: 新的模板描述（如果为 None，则不更新）
    :param is_default: 是否默认模板（如果为 None，则不更新）
    :param db_path: 数据库文件路径
    :return: 是否更新成功；预置模板改标题/版本时返回 False
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    try:
        # 获取旧值
        cursor.execute("""
            SELECT id,
                   template_name_zh as template_name,
                   template_name_en,
                   db_type,
                   version,
                   description,
                   is_default,
                   is_preset,
                   created_at,
                   updated_at
            FROM inspection_template
            WHERE id = ?
        """, (template_id,))
        old_row = cursor.fetchone()
        if not old_row:
            return False

        old_value = dict(old_row)
        is_preset = old_value.get('is_preset', 0)
        tmpl_db_type = old_value.get('db_type')
        tmpl_version = old_value.get('version')

        # 预置模板不能修改名称和版本（仅在实际变更时拒绝）
        if is_preset == 1:
            if (template_name is not None and template_name != old_value.get('template_name')) or \
               (version is not None and version != old_value.get('version')):
                return False

        # 默认模板互斥：设为默认时，取消同类型其他模板的默认
        # Oracle：11g 和非 11g 各一个默认；其他数据库：只有一个默认
        if is_default == 1:
            if tmpl_db_type == 'oracle':
                # Oracle 按 version 分组互斥
                if tmpl_version == '11g':
                    cursor.execute("""
                        UPDATE inspection_template SET is_default = 0
                        WHERE db_type = ? AND version = '11g' AND id != ? AND is_default = 1
                    """, (tmpl_db_type, template_id))
                else:
                    cursor.execute("""
                        UPDATE inspection_template SET is_default = 0
                        WHERE db_type = ? AND (version IS NULL OR version != '11g')
                        AND id != ? AND is_default = 1
                    """, (tmpl_db_type, template_id))
            else:
                # 非 Oracle：同类型全清
                cursor.execute("""
                    UPDATE inspection_template SET is_default = 0
                    WHERE db_type = ? AND id != ? AND is_default = 1
                """, (tmpl_db_type, template_id))

        # 构建更新语句
        updates = []
        params = []

        if template_name is not None:
            updates.append("template_name_zh = ?")
            params.append(template_name)

        if version is not None:
            updates.append("version = ?")
            params.append(version)

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if is_default is not None:
            updates.append("is_default = ?")
            params.append(is_default)
        
        if not updates:
            return True  # 没有需要更新的字段
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(template_id)
        
        cursor.execute(f"""
            UPDATE inspection_template 
            SET {', '.join(updates)}
            WHERE id = ?
        """, params)
        
        # 获取新值
        cursor.execute("""
            SELECT id,
                   template_name_zh as template_name,
                   template_name_en,
                   db_type,
                   version,
                   description,
                   is_default,
                   created_at,
                   updated_at
            FROM inspection_template
            WHERE id = ?
        """, (template_id,))
        new_row = cursor.fetchone()
        new_value = dict(new_row)

        # 记录历史
        _record_history(cursor, 'inspection_template', template_id, 'UPDATE',
                       old_value, new_value)

        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def delete_template(template_id: int, db_path: str = None, force: bool = False) -> bool:
    """
    删除巡检模板（由于外键约束，相关的章节和查询也会被删除）。
    预置模板（is_preset=1）不能删除，除非 force=True。

    :param template_id: 模板 ID
    :param db_path: 数据库文件路径
    :param force: 强制删除（用于 --force 重新初始化）
    :return: 是否删除成功，预置模板且非 force 时返回 False
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    try:
        # 获取旧值
        cursor.execute("""
            SELECT id,
                   template_name_zh as template_name,
                   template_name_en,
                   db_type,
                   version,
                   description,
                   is_default,
                   is_preset,
                   created_at,
                   updated_at
            FROM inspection_template
            WHERE id = ?
        """, (template_id,))
        old_row = cursor.fetchone()
        if not old_row:
            return False

        old_value = dict(old_row)

        # 预置模板不能删除（force 模式除外，用于 --force 重新初始化）
        if old_value.get('is_preset', 0) == 1 and not force:
            return False

        # 记录历史（先记录，再删除）
        _record_history(cursor, 'inspection_template', template_id, 'DELETE',
                       old_value, None)

        # 删除模板（由于外键约束，相关的章节和查询也会被删除）
        cursor.execute("DELETE FROM inspection_template WHERE id = ?", (template_id,))

        conn.commit()
        return True

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# ==================== 章节操作 ====================

def create_chapter(template_id: int, chapter_number: int, 
                  chapter_title_zh: str, chapter_title_en: str = None,
                  description: str = None, enabled: int = 1, 
                  sort_order: int = 0, db_path: str = None) -> int:
    """
    创建新的章节。
    
    :param template_id: 所属模板 ID
    :param chapter_number: 章节序号
    :param chapter_title_zh: 章节标题（中文）
    :param chapter_title_en: 章节标题（英文）
    :param description: 章节描述
    :param enabled: 是否启用（0=禁用，1=启用）
    :param sort_order: 排序字段
    :param db_path: 数据库文件路径
    :return: 新创建的章节 ID
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO inspection_chapter 
            (template_id, chapter_number, chapter_title_zh, chapter_title_en, 
             description, enabled, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (template_id, chapter_number, chapter_title_zh, chapter_title_en, 
              description, enabled, sort_order))
        
        chapter_id = cursor.lastrowid
        conn.commit()
        
        # 记录历史
        cursor.execute("SELECT * FROM inspection_chapter WHERE id = ?", (chapter_id,))
        new_row = cursor.fetchone()
        _record_history(cursor, 'inspection_chapter', chapter_id, 'INSERT', 
                       None, dict(new_row))
        conn.commit()
        
        return chapter_id
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_chapter(chapter_id: int, db_path: str = None) -> Optional[Dict]:
    """
    获取指定 ID 的章节。
    
    :param chapter_id: 章节 ID
    :param db_path: 数据库文件路径
    :return: 章节信息字典，如果不存在则返回 None
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT * FROM inspection_chapter WHERE id = ?
        """, (chapter_id,))
        
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
        
    finally:
        conn.close()


def get_chapters_by_template(template_id: int, db_path: str = None) -> List[Dict]:
    """
    获取指定模板的所有章节（按 sort_order 排序）。
    
    :param template_id: 模板 ID
    :param db_path: 数据库文件路径
    :return: 章节信息字典列表
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT c.*, 
                   COUNT(q.id) as query_count
            FROM inspection_chapter c
            LEFT JOIN inspection_query q ON c.id = q.chapter_id AND q.enabled = 1
            WHERE c.template_id = ?
            GROUP BY c.id
            ORDER BY c.sort_order, c.chapter_number
        """, (template_id,))
        
        return [dict(row) for row in cursor.fetchall()]
        
    finally:
        conn.close()


def update_chapter(chapter_id: int, chapter_number: int = None, 
                   chapter_title_zh: str = None, chapter_title_en: str = None,
                   description: str = None, enabled: int = None, 
                   sort_order: int = None, db_path: str = None) -> bool:
    """
    更新章节。
    
    :param chapter_id: 章节 ID
    :param chapter_number: 新的章节序号（如果为 None，则不更新）
    :param chapter_title_zh: 新的章节标题（中文）（如果为 None，则不更新）
    :param chapter_title_en: 新的章节标题（英文）（如果为 None，则不更新）
    :param description: 新的章节描述（如果为 None，则不更新）
    :param enabled: 是否启用（如果为 None，则不更新）
    :param sort_order: 新的排序字段（如果为 None，则不更新）
    :param db_path: 数据库文件路径
    :return: 是否更新成功
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        # 获取旧值
        cursor.execute("SELECT * FROM inspection_chapter WHERE id = ?", (chapter_id,))
        old_row = cursor.fetchone()
        if not old_row:
            return False
        
        old_value = dict(old_row)
        
        # 构建更新语句
        updates = []
        params = []
        
        if chapter_number is not None:
            updates.append("chapter_number = ?")
            params.append(chapter_number)
        
        if chapter_title_zh is not None:
            updates.append("chapter_title_zh = ?")
            params.append(chapter_title_zh)
        
        if chapter_title_en is not None:
            updates.append("chapter_title_en = ?")
            params.append(chapter_title_en)
        
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(enabled)
        
        if sort_order is not None:
            updates.append("sort_order = ?")
            params.append(sort_order)
        
        if not updates:
            return True  # 没有需要更新的字段
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(chapter_id)
        
        cursor.execute(f"""
            UPDATE inspection_chapter 
            SET {', '.join(updates)}
            WHERE id = ?
        """, params)
        
        # 获取新值
        cursor.execute("SELECT * FROM inspection_chapter WHERE id = ?", (chapter_id,))
        new_row = cursor.fetchone()
        new_value = dict(new_row)
        
        # 记录历史
        _record_history(cursor, 'inspection_chapter', chapter_id, 'UPDATE', 
                       old_value, new_value)
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def delete_chapter(chapter_id: int, db_path: str = None) -> bool:
    """
    删除章节（由于外键约束，相关的查询也会被删除）。
    
    :param chapter_id: 章节 ID
    :param db_path: 数据库文件路径
    :return: 是否删除成功
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        # 获取旧值
        cursor.execute("SELECT * FROM inspection_chapter WHERE id = ?", (chapter_id,))
        old_row = cursor.fetchone()
        if not old_row:
            return False
        
        old_value = dict(old_row)
        
        # 记录历史（先记录，再删除）
        _record_history(cursor, 'inspection_chapter', chapter_id, 'DELETE', 
                       old_value, None)
        
        # 删除章节（由于外键约束，相关的查询也会被删除）
        cursor.execute("DELETE FROM inspection_chapter WHERE id = ?", (chapter_id,))
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def reorder_chapters(template_id: int, chapter_ids: List[int], db_path: str = None) -> bool:
    """
    重新排序章节。
    
    :param template_id: 模板 ID
    :param chapter_ids: 按新的顺序排序的章节 ID 列表
    :param db_path: 数据库文件路径
    :return: 是否重新排序成功
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        for sort_order, chapter_id in enumerate(chapter_ids):
            cursor.execute("""
                UPDATE inspection_chapter 
                SET sort_order = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND template_id = ?
            """, (sort_order, chapter_id, template_id))
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# ==================== SQL 查询操作 ====================

def create_query(chapter_id: int, query_key: str, query_sql: str, 
                 query_description_zh: str = None, query_description_en: str = None,
                 enabled: int = 1, sort_order: int = 0, db_path: str = None) -> int:
    """
    创建新的 SQL 查询。
    
    :param chapter_id: 所属章节 ID
    :param query_key: 查询键名（如 'datadir', 'myversion'）
    :param query_sql: SQL 语句
    :param query_description_zh: 查询描述（中文）
    :param query_description_en: 查询描述（英文）
    :param enabled: 是否启用（0=禁用，1=启用）
    :param sort_order: 排序字段
    :param db_path: 数据库文件路径
    :return: 新创建的查询 ID
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO inspection_query 
            (chapter_id, query_key, query_sql, query_description_zh, 
             query_description_en, enabled, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (chapter_id, query_key, query_sql, query_description_zh, 
              query_description_en, enabled, sort_order))
        
        query_id = cursor.lastrowid
        conn.commit()
        
        # 记录历史
        cursor.execute("SELECT * FROM inspection_query WHERE id = ?", (query_id,))
        new_row = cursor.fetchone()
        _record_history(cursor, 'inspection_query', query_id, 'INSERT', 
                       None, dict(new_row))
        conn.commit()
        
        return query_id
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_query(query_id: int, db_path: str = None) -> Optional[Dict]:
    """
    获取指定 ID 的 SQL 查询。
    
    :param query_id: 查询 ID
    :param db_path: 数据库文件路径
    :return: 查询信息字典，如果不存在则返回 None
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT * FROM inspection_query WHERE id = ?
        """, (query_id,))
        
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
        
    finally:
        conn.close()


def get_queries_by_chapter(chapter_id: int, db_path: str = None) -> List[Dict]:
    """
    获取指定章节的所有 SQL 查询（按 sort_order 排序）。
    
    :param chapter_id: 章节 ID
    :param db_path: 数据库文件路径
    :return: 查询信息字典列表
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT * FROM inspection_query 
            WHERE chapter_id = ?
            ORDER BY sort_order, query_key
        """, (chapter_id,))
        
        return [dict(row) for row in cursor.fetchall()]
        
    finally:
        conn.close()


def update_query(query_id: int, query_key: str = None, query_sql: str = None, 
                 query_description_zh: str = None, query_description_en: str = None,
                 enabled: int = None, sort_order: int = None, db_path: str = None) -> bool:
    """
    更新 SQL 查询。
    
    :param query_id: 查询 ID
    :param query_key: 新的查询键名（如果为 None，则不更新）
    :param query_sql: 新的 SQL 语句（如果为 None，则不更新）
    :param query_description_zh: 新的查询描述（中文）（如果为 None，则不更新）
    :param query_description_en: 新的查询描述（英文）（如果为 None，则不更新）
    :param enabled: 是否启用（如果为 None，则不更新）
    :param sort_order: 新的排序字段（如果为 None，则不更新）
    :param db_path: 数据库文件路径
    :return: 是否更新成功
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        # 获取旧值
        cursor.execute("SELECT * FROM inspection_query WHERE id = ?", (query_id,))
        old_row = cursor.fetchone()
        if not old_row:
            return False
        
        old_value = dict(old_row)
        
        # 构建更新语句
        updates = []
        params = []
        
        if query_key is not None:
            updates.append("query_key = ?")
            params.append(query_key)
        
        if query_sql is not None:
            updates.append("query_sql = ?")
            params.append(query_sql)
        
        if query_description_zh is not None:
            updates.append("query_description_zh = ?")
            params.append(query_description_zh)
        
        if query_description_en is not None:
            updates.append("query_description_en = ?")
            params.append(query_description_en)
        
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(enabled)
        
        if sort_order is not None:
            updates.append("sort_order = ?")
            params.append(sort_order)
        
        if not updates:
            return True  # 没有需要更新的字段
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(query_id)
        
        cursor.execute(f"""
            UPDATE inspection_query 
            SET {', '.join(updates)}
            WHERE id = ?
        """, params)
        
        # 获取新值
        cursor.execute("SELECT * FROM inspection_query WHERE id = ?", (query_id,))
        new_row = cursor.fetchone()
        new_value = dict(new_row)
        
        # 记录历史
        _record_history(cursor, 'inspection_query', query_id, 'UPDATE', 
                       old_value, new_value)
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def delete_query(query_id: int, db_path: str = None) -> bool:
    """
    删除 SQL 查询。
    
    :param query_id: 查询 ID
    :param db_path: 数据库文件路径
    :return: 是否删除成功
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        # 获取旧值
        cursor.execute("SELECT * FROM inspection_query WHERE id = ?", (query_id,))
        old_row = cursor.fetchone()
        if not old_row:
            return False
        
        old_value = dict(old_row)
        
        # 记录历史（先记录，再删除）
        _record_history(cursor, 'inspection_query', query_id, 'DELETE', 
                       old_value, None)
        
        # 删除查询
        cursor.execute("DELETE FROM inspection_query WHERE id = ?", (query_id,))
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def reorder_queries(chapter_id: int, query_ids: List[int], db_path: str = None) -> bool:
    """
    重新排序 SQL 查询。
    
    :param chapter_id: 章节 ID
    :param query_ids: 按新的顺序排序的查询 ID 列表
    :param db_path: 数据库文件路径
    :return: 是否重新排序成功
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        for sort_order, query_id in enumerate(query_ids):
            cursor.execute("""
                UPDATE inspection_query 
                SET sort_order = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND chapter_id = ?
            """, (sort_order, query_id, chapter_id))
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# ==================== 导入/导出功能 ====================

def export_template(template_id: int, db_path: str = None) -> Dict:
    """
    导出巡检模板为 JSON 格式。
    
    :param template_id: 模板 ID
    :param db_path: 数据库文件路径
    :return: 包含模板配置（包含章节和查询）的字典
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        # 获取模板信息
        cursor.execute("""
            SELECT id,
                   template_name_zh as template_name,
                   template_name_en,
                   db_type,
                   version,
                   description,
                   is_default,
                   created_at,
                   updated_at
            FROM inspection_template
            WHERE id = ?
        """, (template_id,))
        template_row = cursor.fetchone()
        if not template_row:
            raise ValueError(f"模板不存在: {template_id}")
        
        template = dict(template_row)
        
        # 获取章节信息
        cursor.execute("""
            SELECT * FROM inspection_chapter 
            WHERE template_id = ? AND enabled = 1
            ORDER BY sort_order, chapter_number
        """, (template_id,))
        chapters = []
        for chapter_row in cursor.fetchall():
            chapter = dict(chapter_row)
            
            # 获取查询信息
            cursor.execute("""
                SELECT * FROM inspection_query 
                WHERE chapter_id = ? AND enabled = 1
                ORDER BY sort_order, query_key
            """, (chapter['id'],))
            queries = [dict(query_row) for query_row in cursor.fetchall()]
            
            chapter['queries'] = queries
            chapters.append(chapter)
        
        template['chapters'] = chapters
        
        return template
        
    finally:
        conn.close()


def import_template(template_config: Dict, db_path: str = None, 
                    overwrite: bool = False) -> int:
    """
    从 JSON 格式导入巡检模板。
    
    :param template_config: 模板配置字典（包含章节和查询）
    :param db_path: 数据库文件路径
    :param overwrite: 如果模板已存在，是否覆盖（根据 db_type 和 template_name 判断）
    :return: 导入的模板 ID
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        db_type = template_config.get('db_type')
        template_name = template_config.get('template_name')
        version = template_config.get('version', 'v1')

        # 检查模板是否已存在
        cursor.execute("""
            SELECT id FROM inspection_template
            WHERE db_type = ? AND template_name_zh = ? AND version = ?
        """, (db_type, template_name, version))
        existing_row = cursor.fetchone()

        if existing_row and overwrite:
            # 删除现有模板（由于外键约束，相关的章节和查询也会被删除）
            cursor.execute("DELETE FROM inspection_template WHERE id = ?", (existing_row['id'],))
        elif existing_row and not overwrite:
            raise ValueError(f"模板已存在: {db_type}/{template_name}")
        
        is_default_val = template_config.get('is_default', 0)

        # 创建新模板
        cursor.execute("""
            INSERT INTO inspection_template (db_type, template_name_zh, version, description, is_default, is_preset)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (db_type, template_name, version,
              template_config.get('description'),
              is_default_val,
              0))  # is_preset 强制为 0，导入的模板一律非预置，确保可删除

        template_id = cursor.lastrowid

        # 默认模板互斥：如果导入模板设为默认，取消同组其它默认
        if is_default_val == 1:
            if db_type == 'oracle':
                # Oracle 按 version 分组互斥：11g 一组，非 11g 一组
                if version == '11g':
                    cursor.execute("""
                        UPDATE inspection_template SET is_default = 0
                        WHERE db_type = ? AND version = '11g' AND id != ? AND is_default = 1
                    """, (db_type, template_id))
                else:
                    cursor.execute("""
                        UPDATE inspection_template SET is_default = 0
                        WHERE db_type = ? AND (version IS NULL OR version != '11g')
                        AND id != ? AND is_default = 1
                    """, (db_type, template_id))
            else:
                # 非 Oracle：同数据库类型只能有一个默认
                cursor.execute("""
                    UPDATE inspection_template SET is_default = 0
                    WHERE db_type = ? AND id != ? AND is_default = 1
                """, (db_type, template_id))
        
        # 创建章节和查询
        for chapter_config in template_config.get('chapters', []):
            cursor.execute("""
                INSERT INTO inspection_chapter 
                (template_id, chapter_number, chapter_title_zh, chapter_title_en, 
                 description, enabled, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (template_id, chapter_config.get('chapter_number'), 
                  chapter_config.get('chapter_title_zh'), 
                  chapter_config.get('chapter_title_en'), 
                  chapter_config.get('description'), 
                  chapter_config.get('enabled', 1), 
                  chapter_config.get('sort_order', 0)))
            
            chapter_id = cursor.lastrowid
            
            # 创建查询
            for query_config in chapter_config.get('queries', []):
                cursor.execute("""
                    INSERT INTO inspection_query 
                    (chapter_id, query_key, query_sql, query_description_zh, 
                     query_description_en, enabled, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (chapter_id, query_config.get('query_key'), 
                      query_config.get('query_sql'), 
                      query_config.get('query_description_zh'), 
                      query_config.get('query_description_en'), 
                      query_config.get('enabled', 1), 
                      query_config.get('sort_order', 0)))
        
        conn.commit()
        return template_id
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# ==================== 辅助函数 ====================

def _record_history(cursor: sqlite3.Cursor, table_name: str, record_id: int, 
                    action: str, old_value: Any, new_value: Any):
    """
    记录修改历史。
    
    :param cursor: 数据库游标
    :param table_name: 表名
    :param record_id: 记录 ID
    :param action: 操作类型（'INSERT', 'UPDATE', 'DELETE'）
    :param old_value: 修改前的值（字典或 None）
    :param new_value: 修改后的值（字典或 None）
    """
    cursor.execute("""
        INSERT INTO inspection_history 
        (table_name, record_id, action, old_value, new_value)
        VALUES (?, ?, ?, ?, ?)
    """, (table_name, record_id, action, 
          json.dumps(old_value, ensure_ascii=False) if old_value else None, 
          json.dumps(new_value, ensure_ascii=False) if new_value else None))


def get_history(table_name: str = None, record_id: int = None, 
                db_path: str = None, limit: int = 100) -> List[Dict]:
    """
    获取修改历史。
    
    :param table_name: 表名（如果为 None，则查询所有表）
    :param record_id: 记录 ID（如果为 None，则查询所有记录）
    :param db_path: 数据库文件路径
    :param limit: 返回记录数量限制
    :return: 修改历史记录列表
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        query = "SELECT * FROM inspection_history WHERE 1=1"
        params = []
        
        if table_name:
            query += " AND table_name = ?"
            params.append(table_name)
        
        if record_id:
            query += " AND record_id = ?"
            params.append(record_id)
        
        query += " ORDER BY modified_at DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        
        return [dict(row) for row in cursor.fetchall()]
        
    finally:
        conn.close()


# ==================== 测试代码 ====================

if __name__ == '__main__':
    # 初始化数据库
    init_database()
    
    # 创建测试模板
    template_id = create_template(
        db_type='mysql',
        template_name='测试模板',
        description='这是一个测试模板'
    )
    print(f"创建模板成功，ID: {template_id}")
    
    # 创建测试章节
    chapter_id = create_chapter(
        template_id=template_id,
        chapter_number=1,
        chapter_title_zh='系统信息',
        chapter_title_en='System Information',
        description='采集系统CPU、内存、磁盘信息'
    )
    print(f"创建章节成功，ID: {chapter_id}")
    
    # 创建测试查询
    query_id = create_query(
        chapter_id=chapter_id,
        query_key='cpu_info',
        query_sql='SHOW GLOBAL STATUS LIKE "Threads_connected"',
        query_description_zh='获取CPU信息',
        query_description_en='Get CPU information'
    )
    print(f"创建查询成功，ID: {query_id}")
    
    # 导出模板
    template_config = export_template(template_id)
    print(f"导出模板成功: {json.dumps(template_config, ensure_ascii=False, indent=2)}")
    

# ==================== 基线配置操作 ====================

def create_baseline(db_type: str, param_name: str, query_sql: str,
                     operator: str = '=', expected_value: str = None,
                     expected_value_min: str = None, expected_value_max: str = None,
                     risk_level: str = 'MEDIUM', description_zh: str = None,
                     description_en: str = None, db_path: str = None) -> int:
    """
    创建新的基线配置。
    
    :param db_type: 数据库类型
    :param param_name: 参数名称
    :param query_sql: 查询 SQL
    :param operator: 运算符（'=', '>', '<', '>=', '<=', '!=', 'BETWEEN', 'LIKE'）
    :param expected_value: 期望值（单个值）
    :param expected_value_min: 期望值最小值（用于 BETWEEN）
    :param expected_value_max: 期望值最大值（用于 BETWEEN）
    :param risk_level: 风险等级（'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'）
    :param description_zh: 描述（中文）
    :param description_en: 描述（英文）
    :param db_path: 数据库文件路径
    :return: 新创建的基线配置 ID
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO inspection_baseline (
                db_type, param_name, query_sql, operator,
                expected_value, expected_value_min, expected_value_max,
                risk_level, description_zh, description_en
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (db_type, param_name, query_sql, operator,
               expected_value, expected_value_min, expected_value_max,
               risk_level, description_zh, description_en))
        
        baseline_id = cursor.lastrowid
        conn.commit()
        
        # 记录历史
        _record_history(cursor, 'inspection_baseline', baseline_id, 'INSERT', 
                       None, {'db_type': db_type, 'param_name': param_name})
        conn.commit()
        
        return baseline_id
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_baseline(baseline_id: int, db_path: str = None) -> Optional[Dict]:
    """
    获取指定 ID 的基线配置。
    
    :param baseline_id: 基线配置 ID
    :param db_path: 数据库文件路径
    :return: 基线配置信息字典，如果不存在则返回 None
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT * FROM inspection_baseline WHERE id = ?
        """, (baseline_id,))
        
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
        
    finally:
        conn.close()


def get_baselines_by_db_type(db_type: str, enabled_only: bool = True, db_path: str = None) -> List[Dict]:
    """
    获取指定数据库类型的所有基线配置。
    
    :param db_type: 数据库类型
    :param enabled_only: 是否只返回启用的配置
    :param db_path: 数据库文件路径
    :return: 基线配置信息字典列表
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        if enabled_only:
            cursor.execute("""
                SELECT * FROM inspection_baseline 
                WHERE db_type = ? AND enabled = 1
                ORDER BY param_name
            """, (db_type,))
        else:
            cursor.execute("""
                SELECT * FROM inspection_baseline 
                WHERE db_type = ?
                ORDER BY param_name
            """, (db_type,))
        
        return [dict(row) for row in cursor.fetchall()]
        
    finally:
        conn.close()


def update_baseline(baseline_id: int, param_name: str = None, query_sql: str = None,
                     operator: str = None, expected_value: str = None,
                     expected_value_min: str = None, expected_value_max: str = None,
                     risk_level: str = None, description_zh: str = None,
                     description_en: str = None, enabled: int = None,
                     db_path: str = None) -> bool:
    """
    更新基线配置。
    
    :param baseline_id: 基线配置 ID
    :param param_name: 新的参数名称（如果为 None，则不更新）
    :param query_sql: 新的查询 SQL（如果为 None，则不更新）
    :param operator: 新的运算符（如果为 None，则不更新）
    :param expected_value: 新的期望值（如果为 None，则不更新）
    :param expected_value_min: 新的期望值最小值（如果为 None，则不更新）
    :param expected_value_max: 新的期望值最大值（如果为 None，则不更新）
    :param risk_level: 新的风险等级（如果为 None，则不更新）
    :param description_zh: 新的描述（中文）（如果为 None，则不更新）
    :param description_en: 新的描述（英文）（如果为 None，则不更新）
    :param enabled: 是否启用（如果为 None，则不更新）
    :param db_path: 数据库文件路径
    :return: 是否更新成功
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        # 获取旧值
        cursor.execute("SELECT * FROM inspection_baseline WHERE id = ?", (baseline_id,))
        old_row = cursor.fetchone()
        if not old_row:
            return False
        
        old_value = dict(old_row)
        
        # 构建更新语句
        updates = []
        params = []
        
        if param_name is not None:
            updates.append("param_name = ?")
            params.append(param_name)
        
        if query_sql is not None:
            updates.append("query_sql = ?")
            params.append(query_sql)
        
        if operator is not None:
            updates.append("operator = ?")
            params.append(operator)
        
        if expected_value is not None:
            updates.append("expected_value = ?")
            params.append(expected_value)
        
        if expected_value_min is not None:
            updates.append("expected_value_min = ?")
            params.append(expected_value_min)
        
        if expected_value_max is not None:
            updates.append("expected_value_max = ?")
            params.append(expected_value_max)
        
        if risk_level is not None:
            updates.append("risk_level = ?")
            params.append(risk_level)
        
        if description_zh is not None:
            updates.append("description_zh = ?")
            params.append(description_zh)
        
        if description_en is not None:
            updates.append("description_en = ?")
            params.append(description_en)
        
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(enabled)
        
        if not updates:
            return True  # 没有需要更新的字段
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(baseline_id)
        
        cursor.execute(f"""
            UPDATE inspection_baseline 
            SET {', '.join(updates)}
            WHERE id = ?
        """, params)
        
        # 获取新值
        cursor.execute("SELECT * FROM inspection_baseline WHERE id = ?", (baseline_id,))
        new_row = cursor.fetchone()
        new_value = dict(new_row)
        
        # 记录历史
        _record_history(cursor, 'inspection_baseline', baseline_id, 'UPDATE', 
                       old_value, new_value)
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def delete_baseline(baseline_id: int, db_path: str = None) -> bool:
    """
    删除基线配置。
    
    :param baseline_id: 基线配置 ID
    :param db_path: 数据库文件路径
    :return: 是否删除成功
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        # 获取旧值
        cursor.execute("SELECT * FROM inspection_baseline WHERE id = ?", (baseline_id,))
        old_row = cursor.fetchone()
        if not old_row:
            return False
        
        old_value = dict(old_row)
        
        # 记录历史（先记录，再删除）
        _record_history(cursor, 'inspection_baseline', baseline_id, 'DELETE', 
                       old_value, None)
        
        # 删除基线配置
        cursor.execute("DELETE FROM inspection_baseline WHERE id = ?", (baseline_id,))
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def init_default_baselines(db_path: str = None):
    """
    初始化默认基线配置（为所有支持的数据库类型）。
    覆盖安全、性能、高可用、运维四个维度。
    :param db_path: 数据库文件路径
    """
    default_baselines = {
        # ═══════════════════════════════════════════
        # Oracle
        # ═══════════════════════════════════════════
        'oracle': [
            # ── 安全 ──
            {'param_name': 'FAILED_LOGIN_ATTEMPTS', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='failed_login_attempts'", 'operator': '<=', 'expected_value': '5', 'risk_level': 'HIGH', 'description_zh': '登录失败尝试次数应 <= 5，防止暴力破解', 'description_en': 'Failed login attempts should be <= 5 to prevent brute force'},
            {'param_name': 'PASSWORD_LIFE_TIME', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='password_life_time'", 'operator': '<=', 'expected_value': '180', 'risk_level': 'MEDIUM', 'description_zh': '密码有效期应 <= 180 天', 'description_en': 'Password lifetime should be <= 180 days'},
            {'param_name': 'AUDIT_TRAIL', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='audit_trail'", 'operator': '=', 'expected_value': 'DB', 'risk_level': 'HIGH', 'description_zh': '审计应开启（AUDIT_TRAIL=DB）', 'description_en': 'Audit trail should be enabled (AUDIT_TRAIL=DB)'},
            {'param_name': 'REMOTE_LOGIN_PASSWORDFILE', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='remote_login_passwordfile'", 'operator': '=', 'expected_value': 'EXCLUSIVE', 'risk_level': 'MEDIUM', 'description_zh': '远程登录密码文件应为 EXCLUSIVE', 'description_en': 'Remote login password file should be EXCLUSIVE'},
            # ── 性能 ──
            {'param_name': 'PROCESSES', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='processes'", 'operator': '>=', 'expected_value': '500', 'risk_level': 'MEDIUM', 'description_zh': '进程数应 >= 500', 'description_en': 'Processes should be >= 500'},
            {'param_name': 'SESSIONS', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='sessions'", 'operator': '>=', 'expected_value': '555', 'risk_level': 'MEDIUM', 'description_zh': '会话数应 >= 555', 'description_en': 'Sessions should be >= 555'},
            {'param_name': 'SGA_TARGET', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='sga_target'", 'operator': '>=', 'expected_value': '1073741824', 'risk_level': 'MEDIUM', 'description_zh': 'SGA 目标大小应 >= 1GB', 'description_en': 'SGA target should be >= 1GB'},
            {'param_name': 'PGA_AGGREGATE_TARGET', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='pga_aggregate_target'", 'operator': '>=', 'expected_value': '536870912', 'risk_level': 'LOW', 'description_zh': 'PGA 聚合目标应 >= 512MB', 'description_en': 'PGA aggregate target should be >= 512MB'},
            # ── 高可用 ──
            {'param_name': 'ARCHIVELOG_MODE', 'query_sql': "SELECT LOG_MODE FROM V$DATABASE", 'operator': '=', 'expected_value': 'ARCHIVELOG', 'risk_level': 'HIGH', 'description_zh': '数据库应运行在归档模式', 'description_en': 'Database should be in ARCHIVELOG mode'},
            {'param_name': 'FORCE_LOGGING', 'query_sql': "SELECT FORCE_LOGGING FROM V$DATABASE", 'operator': '=', 'expected_value': 'YES', 'risk_level': 'MEDIUM', 'description_zh': '应启用强制日志（FORCE_LOGGING=YES）', 'description_en': 'Force logging should be enabled (FORCE_LOGGING=YES)'},
            # ── 运维 ──
            {'param_name': 'OPEN_CURSORS_PER_SESSION', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='open_cursors_per_session'", 'operator': '>=', 'expected_value': '300', 'risk_level': 'LOW', 'description_zh': '每会话打开游标数应 >= 300', 'description_en': 'Open cursors per session should be >= 300'},
            {'param_name': 'UNDO_RETENTION', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='undo_retention'", 'operator': '>=', 'expected_value': '900', 'risk_level': 'LOW', 'description_zh': 'UNDO 保留时间应 >= 900 秒', 'description_en': 'UNDO retention should be >= 900 seconds'},
        ],
        # ═══════════════════════════════════════════
        # MySQL
        # ═══════════════════════════════════════════
        'mysql': [
            # ── 安全 ──
            {'param_name': 'validate_password.policy', 'query_sql': "SHOW VARIABLES LIKE 'validate_password.policy'", 'operator': '>=', 'expected_value': 'MEDIUM', 'risk_level': 'HIGH', 'description_zh': '密码策略应 >= MEDIUM', 'description_en': 'Password policy should be >= MEDIUM'},
            {'param_name': 'max_connect_errors', 'query_sql': "SHOW VARIABLES LIKE 'max_connect_errors'", 'operator': '>=', 'expected_value': '100', 'risk_level': 'MEDIUM', 'description_zh': '最大连接错误数应 >= 100，防止暴力破解', 'description_en': 'Max connect errors should be >= 100'},
            {'param_name': 'local_infile', 'query_sql': "SHOW VARIABLES LIKE 'local_infile'", 'operator': '=', 'expected_value': 'OFF', 'risk_level': 'HIGH', 'description_zh': 'local_infile 应关闭（防止 SQL 注入加载本地文件）', 'description_en': 'local_infile should be OFF to prevent local file injection'},
            {'param_name': 'sql_mode', 'query_sql': "SHOW VARIABLES LIKE 'sql_mode'", 'operator': 'LIKE', 'expected_value': 'STRICT_TRANS_TABLES', 'risk_level': 'MEDIUM', 'description_zh': 'sql_mode 应包含 STRICT_TRANS_TABLES', 'description_en': 'sql_mode should contain STRICT_TRANS_TABLES'},
            {'param_name': 'log_error', 'query_sql': "SHOW VARIABLES LIKE 'log_error'", 'operator': '!=', 'expected_value': '', 'risk_level': 'LOW', 'description_zh': '应配置错误日志路径', 'description_en': 'Error log path should be configured'},
            # ── 性能 ──
            {'param_name': 'max_connections', 'query_sql': "SHOW VARIABLES LIKE 'max_connections'", 'operator': '>=', 'expected_value': '500', 'risk_level': 'MEDIUM', 'description_zh': '最大连接数应 >= 500', 'description_en': 'Max connections should be >= 500'},
            {'param_name': 'innodb_buffer_pool_size', 'query_sql': "SHOW VARIABLES LIKE 'innodb_buffer_pool_size'", 'operator': '>=', 'expected_value': '1073741824', 'risk_level': 'MEDIUM', 'description_zh': 'InnoDB 缓冲池大小应 >= 1GB', 'description_en': 'InnoDB buffer pool size should be >= 1GB'},
            {'param_name': 'innodb_log_file_size', 'query_sql': "SHOW VARIABLES LIKE 'innodb_log_file_size'", 'operator': '>=', 'expected_value': '268435456', 'risk_level': 'LOW', 'description_zh': 'InnoDB 日志文件大小应 >= 256MB', 'description_en': 'InnoDB log file size should be >= 256MB'},
            {'param_name': 'query_cache_size', 'query_sql': "SHOW VARIABLES LIKE 'query_cache_size'", 'operator': '<=', 'expected_value': '0', 'risk_level': 'LOW', 'description_zh': 'MySQL 8.0+ 查询缓存应关闭（0）', 'description_en': 'Query cache should be disabled (0) for MySQL 8.0+'},
            # ── 高可用 ──
            {'param_name': 'sync_binlog', 'query_sql': "SHOW VARIABLES LIKE 'sync_binlog'", 'operator': '>=', 'expected_value': '1', 'risk_level': 'HIGH', 'description_zh': 'sync_binlog 应 >= 1（保证 binlog 落盘）', 'description_en': 'sync_binlog should be >= 1 for data safety'},
            {'param_name': 'innodb_flush_log_at_trx_commit', 'query_sql': "SHOW VARIABLES LIKE 'innodb_flush_log_at_trx_commit'", 'operator': '>=', 'expected_value': '1', 'risk_level': 'HIGH', 'description_zh': '事务提交时刷盘策略应 >= 1', 'description_en': 'InnoDB flush log at trx commit should be >= 1'},
            # ── 运维 ──
            {'param_name': 'expire_logs_days', 'query_sql': "SHOW VARIABLES LIKE 'expire_logs_days'", 'operator': '>=', 'expected_value': '7', 'risk_level': 'LOW', 'description_zh': 'binlog 保留天数应 >= 7 天', 'description_en': 'Binlog expiration should be >= 7 days'},
            {'param_name': 'max_allowed_packet', 'query_sql': "SHOW VARIABLES LIKE 'max_allowed_packet'", 'operator': '>=', 'expected_value': '67108864', 'risk_level': 'LOW', 'description_zh': '最大包大小应 >= 64MB', 'description_en': 'Max allowed packet should be >= 64MB'},
        ],
        # ═══════════════════════════════════════════
        # PostgreSQL
        # ═══════════════════════════════════════════
        'postgresql': [
            # ── 安全 ──
            {'param_name': 'password_encryption', 'query_sql': "SHOW password_encryption", 'operator': '=', 'expected_value': 'scram-sha-256', 'risk_level': 'HIGH', 'description_zh': '密码加密应使用 scram-sha-256', 'description_en': 'Password encryption should use scram-sha-256'},
            {'param_name': 'ssl', 'query_sql': "SHOW ssl", 'operator': '=', 'expected_value': 'on', 'risk_level': 'HIGH', 'description_zh': 'SSL 连接应开启', 'description_en': 'SSL should be enabled'},
            {'param_name': 'log_connections', 'query_sql': "SHOW log_connections", 'operator': '=', 'expected_value': 'on', 'risk_level': 'MEDIUM', 'description_zh': '应记录连接日志', 'description_en': 'Connection logging should be enabled'},
            {'param_name': 'log_disconnections', 'query_sql': "SHOW log_disconnections", 'operator': '=', 'expected_value': 'on', 'risk_level': 'MEDIUM', 'description_zh': '应记录断开连接日志', 'description_en': 'Disconnection logging should be enabled'},
            # ── 性能 ──
            {'param_name': 'max_connections', 'query_sql': "SHOW max_connections", 'operator': '>=', 'expected_value': '200', 'risk_level': 'MEDIUM', 'description_zh': '最大连接数应 >= 200', 'description_en': 'Max connections should be >= 200'},
            {'param_name': 'shared_buffers', 'query_sql': "SHOW shared_buffers", 'operator': '>=', 'expected_value': '128MB', 'risk_level': 'MEDIUM', 'description_zh': '共享缓冲区应 >= 128MB（建议 25% 内存）', 'description_en': 'Shared buffers should be >= 128MB (recommend 25% of RAM)'},
            {'param_name': 'work_mem', 'query_sql': "SHOW work_mem", 'operator': '>=', 'expected_value': '4MB', 'risk_level': 'LOW', 'description_zh': '工作内存应 >= 4MB', 'description_en': 'Work memory should be >= 4MB'},
            {'param_name': 'maintenance_work_mem', 'query_sql': "SHOW maintenance_work_mem", 'operator': '>=', 'expected_value': '64MB', 'risk_level': 'LOW', 'description_zh': '维护工作内存应 >= 64MB', 'description_en': 'Maintenance work memory should be >= 64MB'},
            {'param_name': 'effective_cache_size', 'query_sql': "SHOW effective_cache_size", 'operator': '>=', 'expected_value': '4096MB', 'risk_level': 'LOW', 'description_zh': '有效缓存大小应 >= 4GB', 'description_en': 'Effective cache size should be >= 4GB'},
            # ── 高可用 ──
            {'param_name': 'wal_level', 'query_sql': "SHOW wal_level", 'operator': '>=', 'expected_value': 'replica', 'risk_level': 'HIGH', 'description_zh': 'WAL 级别应 >= replica（用于复制/归档）', 'description_en': 'WAL level should be >= replica for replication/archiving'},
            {'param_name': 'archive_mode', 'query_sql': "SHOW archive_mode", 'operator': '=', 'expected_value': 'on', 'risk_level': 'MEDIUM', 'description_zh': '归档模式应开启（用于 PITR）', 'description_en': 'Archive mode should be on for PITR'},
            # ── 运维 ──
            {'param_name': 'autovacuum', 'query_sql': "SHOW autovacuum", 'operator': '=', 'expected_value': 'on', 'risk_level': 'MEDIUM', 'description_zh': '自动清理（autovacuum）应开启', 'description_en': 'Autovacuum should be enabled'},
            {'param_name': 'vacuum_cost_limit', 'query_sql': "SHOW vacuum_cost_limit", 'operator': '>=', 'expected_value': '200', 'risk_level': 'LOW', 'description_zh': 'VACUUM 成本限制应 >= 200', 'description_en': 'VACUUM cost limit should be >= 200'},
        ],
        # ═══════════════════════════════════════════
        # DM8 达梦
        # ═══════════════════════════════════════════
        'dm8': [
            # ── 安全 ──
            {'param_name': 'MAX_LOGIN_FAILURE', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='MAX_LOGIN_FAILURE'", 'operator': '<=', 'expected_value': '5', 'risk_level': 'HIGH', 'description_zh': '最大登录失败次数应 <= 5', 'description_en': 'Max login failure should be <= 5'},
            {'param_name': 'PASSWORD_LIFE_DAYS', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='PASSWORD_LIFE_DAYS'", 'operator': '<=', 'expected_value': '180', 'risk_level': 'MEDIUM', 'description_zh': '密码有效期应 <= 180 天', 'description_en': 'Password life days should be <= 180'},
            {'param_name': 'ENABLE_AUDIT', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='ENABLE_AUDIT'", 'operator': '>=', 'expected_value': '1', 'risk_level': 'HIGH', 'description_zh': '审计功能应开启（ENABLE_AUDIT >= 1）', 'description_en': 'Audit should be enabled (ENABLE_AUDIT >= 1)'},
            # ── 性能 ──
            {'param_name': 'MAX_SESSIONS', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='MAX_SESSIONS'", 'operator': '>=', 'expected_value': '500', 'risk_level': 'MEDIUM', 'description_zh': '最大会话数应 >= 500', 'description_en': 'Max sessions should be >= 500'},
            {'param_name': 'BUFFER_SIZE', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='BUFFER_SIZE'", 'operator': '>=', 'expected_value': '1024', 'risk_level': 'MEDIUM', 'description_zh': '缓冲池大小应 >= 1024 (MB)', 'description_en': 'Buffer size should be >= 1024 (MB)'},
            {'param_name': 'BUFFER_POOLS', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='BUFFER_POOLS'", 'operator': '>=', 'expected_value': '3', 'risk_level': 'LOW', 'description_zh': '缓冲池数量应 >= 3', 'description_en': 'Buffer pools should be >= 3'},
            {'param_name': 'MAX_BUFFER_SIZE', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='MAX_BUFFER_SIZE'", 'operator': '>=', 'expected_value': '2048', 'risk_level': 'LOW', 'description_zh': '最大缓冲池大小应 >= 2048 (MB)', 'description_en': 'Max buffer size should be >= 2048 (MB)'},
            # ── 高可用 ──
            {'param_name': 'ARCH_INI', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='ARCH_INI'", 'operator': '=', 'expected_value': '1', 'risk_level': 'HIGH', 'description_zh': '归档配置应开启（ARCH_INI=1）', 'description_en': 'Archive should be enabled (ARCH_INI=1)'},
            # ── 运维 ──
            {'param_name': 'UNDO_RETENTION', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='UNDO_RETENTION'", 'operator': '>=', 'expected_value': '1800', 'risk_level': 'LOW', 'description_zh': 'UNDO 保留时间应 >= 1800 秒', 'description_en': 'UNDO retention should be >= 1800 seconds'},
            {'param_name': 'TEMP_SIZE', 'query_sql': "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='TEMP_SIZE'", 'operator': '>=', 'expected_value': '1024', 'risk_level': 'LOW', 'description_zh': '临时表空间大小应 >= 1024 (MB)', 'description_en': 'Temp tablespace size should be >= 1024 (MB)'},
        ],
        # ═══════════════════════════════════════════
        # SQL Server
        # ═══════════════════════════════════════════
        'sqlserver': [
            # ── 安全 ──
            {'param_name': 'is_auto_close', 'query_sql': "SELECT name, is_auto_close_on FROM sys.databases WHERE name = DB_NAME()", 'operator': '=', 'expected_value': '0', 'risk_level': 'HIGH', 'description_zh': '数据库 AUTO_CLOSE 应关闭（0）', 'description_en': 'AUTO_CLOSE should be OFF (0)'},
            {'param_name': 'is_trustworthy_on', 'query_sql': "SELECT name, is_trustworthy_on FROM sys.databases WHERE name = DB_NAME()", 'operator': '=', 'expected_value': '0', 'risk_level': 'HIGH', 'description_zh': 'TRUSTWORTHY 应关闭（除非必要）', 'description_en': 'TRUSTWORTHY should be OFF unless explicitly needed'},
            {'param_name': 'is_broker_enabled', 'query_sql': "SELECT name, is_broker_enabled FROM sys.databases WHERE name = DB_NAME()", 'operator': '=', 'expected_value': '0', 'risk_level': 'MEDIUM', 'description_zh': 'Service Broker 应关闭（除非使用）', 'description_en': 'Service Broker should be OFF unless used'},
            {'param_name': 'HAS_DBACCESS', 'query_sql': "SELECT IS_SRVROLEMEMBER('sysadmin') AS is_sa", 'operator': '=', 'expected_value': '0', 'risk_level': 'CRITICAL', 'description_zh': '当前登录不应是 sysadmin（最小权限原则）', 'description_en': 'Current login should not be sysadmin (principle of least privilege)'},
            # ── 性能 ──
            {'param_name': 'max server memory', 'query_sql': "EXEC sp_configure 'max server memory'", 'operator': '>=', 'expected_value': '4096', 'risk_level': 'MEDIUM', 'description_zh': '最大服务器内存应 >= 4096 (MB)', 'description_en': 'Max server memory should be >= 4096 (MB)'},
            {'param_name': 'min server memory', 'query_sql': "EXEC sp_configure 'min server memory'", 'operator': '>=', 'expected_value': '1024', 'risk_level': 'LOW', 'description_zh': '最小服务器内存应 >= 1024 (MB)', 'description_en': 'Min server memory should be >= 1024 (MB)'},
            {'param_name': 'max degree of parallelism', 'query_sql': "EXEC sp_configure 'max degree of parallelism'", 'operator': '>=', 'expected_value': '1', 'risk_level': 'MEDIUM', 'description_zh': '最大并行度应 >= 1（建议 = CPU 核心数）', 'description_en': 'Max degree of parallelism should be configured'},
            {'param_name': 'cost threshold for parallelism', 'query_sql': "EXEC sp_configure 'cost threshold for parallelism'", 'operator': '>=', 'expected_value': '50', 'risk_level': 'LOW', 'description_zh': '并行成本阈值应 >= 50', 'description_en': 'Cost threshold for parallelism should be >= 50'},
            # ── 高可用 ──
            {'param_name': 'recovery model', 'query_sql': "SELECT name, recovery_model_desc FROM sys.databases WHERE name = DB_NAME()", 'operator': '=', 'expected_value': 'FULL', 'risk_level': 'HIGH', 'description_zh': '恢复模式应为 FULL（用于完整备份/恢复）', 'description_en': 'Recovery model should be FULL for full backup/recovery'},
            {'param_name': 'is_fulltext_enabled', 'query_sql': "SELECT name, is_fulltext_enabled FROM sys.databases WHERE name = DB_NAME()", 'operator': '=', 'expected_value': '0', 'risk_level': 'LOW', 'description_zh': '全文索引应关闭（除非使用）', 'description_en': 'Fulltext should be OFF unless used'},
            # ── 运维 ──
            {'param_name': 'automatic database backup', 'query_sql': "SELECT COUNT(*) FROM msdb.dbo.backupset WHERE database_name = DB_NAME() AND backup_finish_date > DATEADD(day, -1, GETDATE())", 'operator': '>=', 'expected_value': '1', 'risk_level': 'HIGH', 'description_zh': '数据库应有最近 24 小时内的备份', 'description_en': 'Database should have a backup within the last 24 hours'},
            {'param_name': 'page_verify_option', 'query_sql': "SELECT name, page_verify_option_desc FROM sys.databases WHERE name = DB_NAME()", 'operator': '=', 'expected_value': 'CHECKSUM', 'risk_level': 'MEDIUM', 'description_zh': '页验证选项应为 CHECKSUM', 'description_en': 'Page verify option should be CHECKSUM'},
        ],
        # ═══════════════════════════════════════════
        # TiDB
        # ═══════════════════════════════════════════
        'tidb': [
            # ── 安全 ──
            {'param_name': 'tidb_general_log', 'query_sql': "SHOW VARIABLES LIKE 'tidb_general_log'", 'operator': '=', 'expected_value': 'ON', 'risk_level': 'MEDIUM', 'description_zh': 'TiDB 通用日志应开启（审计）', 'description_en': 'TiDB general log should be ON for auditing'},
            {'param_name': 'validate_password.policy', 'query_sql': "SHOW VARIABLES LIKE 'validate_password.policy'", 'operator': '>=', 'expected_value': 'MEDIUM', 'risk_level': 'HIGH', 'description_zh': '密码策略应 >= MEDIUM', 'description_en': 'Password policy should be >= MEDIUM'},
            # ── 性能 ──
            {'param_name': 'tidb_mem_quota_query', 'query_sql': "SHOW VARIABLES LIKE 'tidb_mem_quota_query'", 'operator': '>=', 'expected_value': '1073741824', 'risk_level': 'MEDIUM', 'description_zh': '单查询内存配额应 >= 1GB', 'description_en': 'Per-query memory quota should be >= 1GB'},
            {'param_name': 'tidb_distsql_scan_concurrency', 'query_sql': "SHOW VARIABLES LIKE 'tidb_distsql_scan_concurrency'", 'operator': '>=', 'expected_value': '15', 'risk_level': 'LOW', 'description_zh': '分布式 SQL 扫描并发度应 >= 15', 'description_en': 'DistSQL scan concurrency should be >= 15'},
            # ── 高可用 ──
            {'param_name': 'tidb_txn_mode', 'query_sql': "SHOW VARIABLES LIKE 'tidb_txn_mode'", 'operator': '=', 'expected_value': 'optimistic', 'risk_level': 'LOW', 'description_zh': '事务模式应为 optimistic（或 pessimistic）', 'description_en': 'Transaction mode should be optimistic or pessimistic'},
            {'param_name': 'tidb_gc_life_time', 'query_sql': "SHOW VARIABLES LIKE 'tidb_gc_life_time'", 'operator': '>=', 'expected_value': '10m', 'risk_level': 'MEDIUM', 'description_zh': 'GC 生命周期应 >= 10 分钟', 'description_en': 'GC life time should be >= 10 minutes'},
            # ── 运维 ──
            {'param_name': 'tidb_slow_log_threshold', 'query_sql': "SHOW VARIABLES LIKE 'tidb_slow_log_threshold'", 'operator': '>=', 'expected_value': '300', 'risk_level': 'LOW', 'description_zh': '慢查询阈值应 >= 300ms', 'description_en': 'Slow log threshold should be >= 300ms'},
            {'param_name': 'tidb_enable_stmt_summary', 'query_sql': "SHOW VARIABLES LIKE 'tidb_enable_stmt_summary'", 'operator': '=', 'expected_value': 'ON', 'risk_level': 'LOW', 'description_zh': '语句摘要应开启（性能分析）', 'description_en': 'Statement summary should be enabled for performance analysis'},
        ],
        # ═══════════════════════════════════════════
        # IvorySQL（基于 PostgreSQL）
        # ═══════════════════════════════════════════
        'ivorysql': [
            # ── 安全 ──
            {'param_name': 'password_encryption', 'query_sql': "SHOW password_encryption", 'operator': '=', 'expected_value': 'scram-sha-256', 'risk_level': 'HIGH', 'description_zh': '密码加密应使用 scram-sha-256', 'description_en': 'Password encryption should use scram-sha-256'},
            {'param_name': 'ssl', 'query_sql': "SHOW ssl", 'operator': '=', 'expected_value': 'on', 'risk_level': 'HIGH', 'description_zh': 'SSL 连接应开启', 'description_en': 'SSL should be enabled'},
            {'param_name': 'log_connections', 'query_sql': "SHOW log_connections", 'operator': '=', 'expected_value': 'on', 'risk_level': 'MEDIUM', 'description_zh': '应记录连接日志', 'description_en': 'Connection logging should be enabled'},
            # ── 性能 ──
            {'param_name': 'max_connections', 'query_sql': "SHOW max_connections", 'operator': '>=', 'expected_value': '200', 'risk_level': 'MEDIUM', 'description_zh': '最大连接数应 >= 200', 'description_en': 'Max connections should be >= 200'},
            {'param_name': 'shared_buffers', 'query_sql': "SHOW shared_buffers", 'operator': '>=', 'expected_value': '128MB', 'risk_level': 'MEDIUM', 'description_zh': '共享缓冲区应 >= 128MB', 'description_en': 'Shared buffers should be >= 128MB'},
            {'param_name': 'work_mem', 'query_sql': "SHOW work_mem", 'operator': '>=', 'expected_value': '4MB', 'risk_level': 'LOW', 'description_zh': '工作内存应 >= 4MB', 'description_en': 'Work memory should be >= 4MB'},
            {'param_name': 'effective_cache_size', 'query_sql': "SHOW effective_cache_size", 'operator': '>=', 'expected_value': '4096MB', 'risk_level': 'LOW', 'description_zh': '有效缓存大小应 >= 4GB', 'description_en': 'Effective cache size should be >= 4GB'},
            # ── 高可用 ──
            {'param_name': 'wal_level', 'query_sql': "SHOW wal_level", 'operator': '>=', 'expected_value': 'replica', 'risk_level': 'HIGH', 'description_zh': 'WAL 级别应 >= replica', 'description_en': 'WAL level should be >= replica'},
            {'param_name': 'archive_mode', 'query_sql': "SHOW archive_mode", 'operator': '=', 'expected_value': 'on', 'risk_level': 'MEDIUM', 'description_zh': '归档模式应开启', 'description_en': 'Archive mode should be on'},
            # ── 运维 ──
            {'param_name': 'autovacuum', 'query_sql': "SHOW autovacuum", 'operator': '=', 'expected_value': 'on', 'risk_level': 'MEDIUM', 'description_zh': '自动清理（autovacuum）应开启', 'description_en': 'Autovacuum should be enabled'},
            {'param_name': 'oracle_compatibility', 'query_sql': "SELECT setting FROM pg_settings WHERE name = 'ivorysql.oracle_compatibility'", 'operator': '=', 'expected_value': 'on', 'risk_level': 'LOW', 'description_zh': 'Oracle 兼容模式应开启（仅 ORAMODE）', 'description_en': 'Oracle compatibility mode should be on (ORAMODE only)'},
        ],
        # ═══════════════════════════════════════════
        # YashanDB
        # ═══════════════════════════════════════════
        'yashandb': [
            # ── 安全 ──
            {'param_name': 'FAILED_LOGIN_ATTEMPTS', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='failed_login_attempts'", 'operator': '<=', 'expected_value': '5', 'risk_level': 'HIGH', 'description_zh': '登录失败尝试次数应 <= 5，防止暴力破解', 'description_en': 'Failed login attempts should be <= 5 to prevent brute force'},
            {'param_name': 'PASSWORD_LIFE_TIME', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='password_life_time'", 'operator': '<=', 'expected_value': '180', 'risk_level': 'MEDIUM', 'description_zh': '密码有效期应 <= 180 天', 'description_en': 'Password lifetime should be <= 180 days'},
            # ── 性能 ──
            {'param_name': 'PROCESSES', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='processes'", 'operator': '>=', 'expected_value': '150', 'risk_level': 'MEDIUM', 'description_zh': '最大进程数应 >= 150', 'description_en': 'Processes should be >= 150'},
            {'param_name': 'SGA_TARGET', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='sga_target'", 'operator': '>=', 'expected_value': '1073741824', 'risk_level': 'MEDIUM', 'description_zh': 'SGA 目标大小应 >= 1GB', 'description_en': 'SGA target should be >= 1GB'},
            {'param_name': 'PGA_AGGREGATE_TARGET', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='pga_aggregate_target'", 'operator': '>=', 'expected_value': '536870912', 'risk_level': 'MEDIUM', 'description_zh': 'PGA 聚合目标应 >= 512MB', 'description_en': 'PGA aggregate target should be >= 512MB'},
            {'param_name': 'OPEN_CURSORS', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='open_cursors'", 'operator': '>=', 'expected_value': '300', 'risk_level': 'MEDIUM', 'description_zh': '单会话最大打开游标数应 >= 300', 'description_en': 'Open cursors should be >= 300'},
            # ── 高可用 ──
            {'param_name': 'ARCHIVELOG_MODE', 'query_sql': "SELECT LOG_MODE FROM V$DATABASE", 'operator': '=', 'expected_value': 'ARCHIVELOG', 'risk_level': 'HIGH', 'description_zh': '数据库应运行在归档模式', 'description_en': 'Database should be in ARCHIVELOG mode'},
            # ── 运维 ──
            {'param_name': 'UNDO_RETENTION', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='undo_retention'", 'operator': '>=', 'expected_value': '900', 'risk_level': 'LOW', 'description_zh': 'UNDO 保留时间应 >= 900 秒', 'description_en': 'UNDO retention should be >= 900 seconds'},
            {'param_name': 'DB_FILE_MULTIBLOCK_READ_COUNT', 'query_sql': "SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME='db_file_multiblock_read_count'", 'operator': '>=', 'expected_value': '64', 'risk_level': 'LOW', 'description_zh': '多块读计数应 >= 64', 'description_en': 'Multiblock read count should be >= 64'},
        ],
    }
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    try:
        for db_type, baselines in default_baselines.items():
            # 仅在该 db_type 没有任何基线时（首次使用）才插入
            cursor.execute("SELECT COUNT(*) FROM inspection_baseline WHERE db_type = ?", (db_type,))
            if cursor.fetchone()[0] > 0:
                continue
            for bl in baselines:
                cursor.execute("""
                    INSERT INTO inspection_baseline (
                        db_type, param_name, query_sql, operator,
                        expected_value, risk_level, description_zh, description_en
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (db_type, bl['param_name'], bl['query_sql'], bl['operator'],
                       bl['expected_value'], bl['risk_level'],
                       bl['description_zh'], bl['description_en']))

        conn.commit()
        print("[OK] 默认基线配置初始化成功")

    except Exception as e:
        conn.rollback()
        print(f"[FAIL] 默认基线配置初始化失败: {e}")
        raise
    finally:
        conn.close()


def force_reset_baselines(db_type: str = None, db_path: str = None):
    """手动重置基线配置。删除指定（或全部）db_type 的基线后重新插入默认值。
    注意：此操作不可逆，用户自定义的基线将被清除。"""
    default_baselines = _get_default_baselines()
    # _get_default_baselines() 返回扁平列表 [{db_type, param_name, ...}, ...]

    valid_types = {bl['db_type'] for bl in default_baselines}
    if db_type and db_type not in valid_types:
        raise ValueError(f"不支持的数据库类型: {db_type}")

    filtered = [bl for bl in default_baselines if db_type is None or bl['db_type'] == db_type]

    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    try:
        if db_type:
            cursor.execute("DELETE FROM inspection_baseline WHERE db_type = ?", (db_type,))
        else:
            cursor.execute("DELETE FROM inspection_baseline")

        for bl in filtered:
            cursor.execute("""
                INSERT INTO inspection_baseline (
                    db_type, param_name, query_sql, operator,
                    expected_value, risk_level, description_zh, description_en
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (bl['db_type'], bl['param_name'], bl['query_sql'], bl['operator'],
                   bl['expected_value'], bl['risk_level'],
                   bl['description_zh'], bl['description_en']))

        conn.commit()
        if db_type:
            print(f"[OK] {db_type} 基线配置已重置")
        else:
            print("[OK] 所有基线配置已重置")

    except Exception as e:
        conn.rollback()
        print(f"[FAIL] 基线配置重置失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    # 初始化数据库
    init_database()
    
    # 初始化默认基线配置
    init_default_baselines()
    
    # 创建测试模板
    template_id = create_template(
        db_type='mysql',
        template_name='测试模板',
        description='这是一个测试模板'
    )
    print(f"创建模板成功，ID: {template_id}")
    
    # 创建测试章节
    chapter_id = create_chapter(
        template_id=template_id,
        chapter_number=1,
        chapter_title_zh='系统信息',
        chapter_title_en='System Information',
        description='采集系统CPU、内存、磁盘信息'
    )
    print(f"创建章节成功，ID: {chapter_id}")
    
    # 创建测试查询
    query_id = create_query(
        chapter_id=chapter_id,
        query_key='cpu_info',
        query_sql='SHOW GLOBAL STATUS LIKE "Threads_connected"',
        query_description_zh='获取CPU信息',
        query_description_en='Get CPU information'
    )
    print(f"创建查询成功，ID: {query_id}")
    
    # 导出模板
    template_config = export_template(template_id)
    print(f"导出模板成功: {json.dumps(template_config, ensure_ascii=False, indent=2)}")
    
    print("[OK] 测试完成")
