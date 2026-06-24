# -*- coding: utf-8 -*-
"""
DBCheck 用户管理模块 - 数据库管理器
提供统一的 SQLite 数据库访问接口，专用于 RBAC 相关表
"""

import os
import sqlite3
from threading import Lock


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
        # __file__ = user_management/models/db_manager.py
        # dirname x3 = 项目根目录
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._db_dir = os.path.join(base_dir, 'pro_data')
        self._db_path = os.path.join(self._db_dir, 'um_rbac.db')
        os.makedirs(self._db_dir, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        """初始化数据库表结构"""
        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'db', 'user_management_schema.sql'
        )
        if os.path.exists(schema_path):
            self.execute_sql_file(schema_path)

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
