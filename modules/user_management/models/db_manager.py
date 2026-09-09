# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
DBCheck 用户管理模块 - 数据库管理器
提供统一的 SQLite 数据库访问接口，专用于 RBAC 相关表
"""

import os
import sys
import sqlite3
from threading import Lock

from modules.core import paths


class DBManager:
    """RBAC 数据库管理器（单例模式）"""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # DB 统一收口到 data/user_db（与 core.paths.USER_DB_DIR 对齐）
        self._db_dir = str(paths.USER_DB_DIR)
        os.makedirs(self._db_dir, exist_ok=True)
        self._db_path = os.path.join(self._db_dir, 'um_rbac.db')
        self._init_schema()

    def _init_schema(self):
        """初始化数据库表结构"""
        schema_path = str(paths.PROJECT_ROOT / 'db' / 'user_management_schema.sql')
        if os.path.exists(schema_path):
            self.execute_sql_file(schema_path)
        # 老库升级：schema.sql 用的是 CREATE TABLE IF NOT EXISTS，已存在的表
        # 不会被补列。多租户（阶段 0）的租户/部门/归属表与 um_user 归属列
        # 必须由幂等迁移补齐，否则升级后 access 层会因缺列直接失效。
        try:
            from modules.access_schema import ensure_schema
            ensure_schema(self._db_path)
        except Exception as e:  # 迁移失败不阻断启动，但必须显式告警
            print(f"[DBManager][WARN] 多租户 schema 迁移失败: {e}",
                  file=sys.stderr)

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def execute_sql_file(self, filepath: str):
        """执行 SQL 文件"""
        with open(filepath, 'r', encoding='utf-8') as f:
            sql = f.read()
        conn = self._get_conn()
        try:
            conn.executescript(sql)
            conn.commit()
        finally:
            conn.close()

    def execute(self, sql: str, params=None):
        """执行写操作（INSERT/UPDATE/DELETE）"""
        conn = self._get_conn()
        try:
            if params:
                conn.execute(sql, params)
            else:
                conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def query_one(self, sql: str, params=None) -> dict:
        """查询单行，返回 dict 或 None"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(sql, params) if params else conn.execute(sql)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def query_all(self, sql: str, params=None) -> list:
        """查询多行，返回 list[dict]"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(sql, params) if params else conn.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def execute_many(self, sql: str, params_list: list):
        """批量执行写操作"""
        conn = self._get_conn()
        try:
            conn.executemany(sql, params_list)
            conn.commit()
        finally:
            conn.close()

    def get_db_path(self) -> str:
        return self._db_path
