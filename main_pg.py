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
PostgreSQL 数据库巡检模块 - 基于 BaseInspectionEngine 重构版本

使用方式：
    from main_pg import PostgreSQLInspector
    inspector = PostgreSQLInspector(host, port, user, password, database, ssh_info)
    ok, ver = inspector.connect()
    if ok:
        inspector.collect_data()
        inspector.generate_report(output_file, inspector_name)
"""

import os
from inspection_engine import BaseInspectionEngine


class PostgreSQLInspector(BaseInspectionEngine):
    """
    PostgreSQL 数据库巡检器 - 继承 BaseInspectionEngine
    
    只需实现 connect() 方法，其他逻辑全部在基类中！
    """
    
    def __init__(self, host, port, user, password, database=None, ssh_info=None, template_id=None):
        """
        初始化 PostgreSQL 巡检器

        :param host: PostgreSQL 服务器 IP 地址或主机名
        :param port: PostgreSQL 服务端口
        :param user: PostgreSQL 登录用户名
        :param password: PostgreSQL 登录密码
        :param database: 要连接的数据库名（可选，默认 'postgres'）
        :param ssh_info: SSH 连接信息字典（可选）
        :param template_id: 巡检模板 ID（可选，指定后使用对应模板的 SQL）
        """
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'postgresql'
    
    def connect(self):
        """
        连接 PostgreSQL 数据库
        
        返回:
            (ok, version) - ok 为 True 时 version 是版本号，否则是错误信息
        """
        import psycopg2
        try:
            self.conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                dbname=self.database or 'postgres',
                client_encoding='UTF8',
                connect_timeout=10
            )
            self.cursor = self.conn.cursor()
            self.cursor.execute("SELECT version()")
            ver = self.cursor.fetchone()[0]
            return True, ver
        except Exception as e:
            return False, str(e)


# ── 保留原有 API 兼容性（供 web_ui.py 旧代码调用）────────────────────
def getData(ip, port, user, password, database='postgres', ssh_info=None, label=None, template_id=None):
    """
    原有 API - 创建 PostgreSQLInspector 实例

    注意：这个函数在重构过程中保留，用于兼容 web_ui.py 中的旧代码。
    新代码应该直接使用 PostgreSQLInspector 类。
    """
    inspector = PostgreSQLInspector(ip, port, user, password, database, ssh_info, template_id)
    ok, ver = inspector.connect()
    if not ok:
        return None
    # 为了兼容旧代码，返回一个对象，其中包含 conn_db2 属性
    class CompatWrapper:
        def __init__(self, inspector):
            self.inspector = inspector
            self.conn_db2 = inspector.conn
        def checkdb(self, sqlfile=''):
            self.inspector.collect_data()
            return self.inspector.context
        def generate_report(self, output_file, inspector_name="Jack"):
            return self.inspector.generate_report(output_file, inspector_name)
    return CompatWrapper(inspector)
