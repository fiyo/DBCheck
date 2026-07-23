#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck Professional — 专有商业软件，保留一切权利（Proprietary Software, All Rights Reserved）.
# See LICENSE for full license text.
#

"""
TiDB 数据库巡检模块 - 基于 BaseInspectionEngine 重构版本

TiDB 使用 MySQL 协议，可以用 pymysql 连接。
巡检模板：1-21 章完全参照 MySQL + 22-29 章 TiDB 特性（集群拓扑、TiKV、PD、GC、热点、DDL、SQL 执行统计、TiDB 专用变量）

使用方式：
    from main_tidb import TiDBInspector
    inspector = TiDBInspector(host, port, user, password, database, ssh_info)
    ok, ver = inspector.connect()
    if ok:
        inspector.collect_data()
        inspector.generate_report(output_file, inspector_name)
"""

import os
from inspection_engine import BaseInspectionEngine


class TiDBInspector(BaseInspectionEngine):
    """
    TiDB 数据库巡检器 - 继承 BaseInspectionEngine

    只需实现 connect() 方法，其他逻辑全部在基类中！
    """

    def __init__(self, host, port, user, password, database=None, ssh_info=None, template_id=None):
        """
        初始化 TiDB 巡检器

        :param host: TiDB 服务器 IP 地址或主机名
        :param port: TiDB 服务端口（默认 4000）
        :param user: TiDB 登录用户名
        :param password: TiDB 登录密码
        :param database: 要连接的数据库名（可选）
        :param ssh_info: SSH 连接信息字典（可选）
        :param template_id: 巡检模板 ID（可选，指定后使用对应模板的 SQL）
        """
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'tidb'

    def connect(self):
        """
        连接 TiDB 数据库
        使用 pymysql 驱动（TiDB 兼容 MySQL 协议）

        返回:
            (ok, version) - ok 为 True 时 version 是版本号，否则是错误信息
        """
        import pymysql
        try:
            self.conn = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database or '',
                charset='utf8mb4',
                connect_timeout=10,
                read_timeout=60,
                autocommit=True
            )
            self.cursor = self.conn.cursor()
            self.cursor.execute("SELECT VERSION()")
            ver = self.cursor.fetchone()[0]
            return True, ver
        except Exception as e:
            return False, str(e)

    def _customize_queries(self, sql_dict):
        """覆盖 MySQL 不兼容的 SQL 查询（TiDB 专用）"""
        # TiDB 没有 performance_schema.global_variables，改用 SHOW GLOBAL VARIABLES WHERE
        if 'key_vars' in sql_dict:
            sql_dict['key_vars'] = "SHOW GLOBAL VARIABLES WHERE variable_name IN ('innodb_buffer_pool_size','innodb_log_file_size','max_connections','tmp_table_size','max_heap_table_size','thread_cache_size','table_open_cache','open_files_limit','innodb_flush_log_at_trx_commit','sync_binlog','log_bin','slow_query_log','long_query_time')"

        # STATEMENTS_SUMMARY 列名在不同 TiDB 版本差异很大，运行时动态探测
        self._fix_stmt_summary(sql_dict)

        # 单库巡检过滤（与 MySQL/MariaDB 一致）：指定库名时只巡检该库
        from inspection_engine import scope_mysql_schema
        scope_mysql_schema(sql_dict, self.database)

    def _fix_stmt_summary(self, sql_dict):
        """动态探测 STATEMENTS_SUMMARY 列名并生成兼容 SQL"""
        try:
            # 获取实际列名
            self.cursor.execute("""
                SELECT COLUMN_NAME FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = 'information_schema'
                  AND TABLE_NAME = 'STATEMENTS_SUMMARY'
                ORDER BY ORDINAL_POSITION
            """)
            columns = [r[0] for r in self.cursor.fetchall()]
            if not columns:
                # 表不存在，跳过
                return

            col_lower = {c.lower(): c for c in columns}

            # 映射我们需要的字段 -> 实际列名
            def find_col(*candidates):
                for c in candidates:
                    if c.lower() in col_lower:
                        return col_lower[c.lower()]
                return None

            digest_col = find_col('digest_text')
            count_col = find_col('exec_count', 'EXEC_COUNT', 'SUM_EXEC_COUNT', 'COUNT_READ', 'COUNT')
            latency_col = find_col('total_latency', 'SUM_LATENCY')
            avg_col = find_col('avg_latency', 'AVG_LATENCY')
            rows_col = find_col('rows_examined', 'SUM_SCAN_ROWS')

            if not digest_col:
                return

            # stmt_summary - 按执行次数排序
            select_cols = [f"{digest_col} AS digest_text"]
            if count_col:
                select_cols.append(f"{count_col} AS exec_count")
            if latency_col:
                select_cols.append(f"{latency_col} AS total_latency")
            if avg_col:
                select_cols.append(f"{avg_col} AS avg_latency")
            if rows_col:
                select_cols.append(f"{rows_col} AS rows_examined")

            order_by = f"ORDER BY {count_col} DESC" if count_col else f"ORDER BY {latency_col} DESC" if latency_col else ""
            limit = "LIMIT 20"

            where_clause = "WHERE schema_name NOT IN ('mysql','information_schema','performance_schema','sys')"
            # 有些版本没有 schema_name
            if 'schema_name' not in col_lower:
                where_clause = ""

            sql = f"SELECT {', '.join(select_cols)} FROM information_schema.STATEMENTS_SUMMARY {where_clause} {order_by} {limit}".strip()
            if 'stmt_summary' in sql_dict:
                sql_dict['stmt_summary'] = sql

            # stmt_summary_top_latency - 按总耗时排序
            if latency_col:
                sql2 = f"SELECT {', '.join(select_cols[:4])} FROM information_schema.STATEMENTS_SUMMARY {where_clause} ORDER BY {latency_col} DESC {limit}".strip()
                if 'stmt_summary_top_latency' in sql_dict:
                    sql_dict['stmt_summary_top_latency'] = sql2

        except Exception:
            pass
        else:
            print(f"[INFO] TiDB STATEMENTS_SUMMARY 列名探测成功，已生成兼容 SQL")


# ── 保留原有 API 兼容性（供 web_ui.py 旧代码调用）────────────────────
def getData(ip, port, user, password, ssh_info=None, template_id=None, database=None):
    """
    原有 API - 创建 TiDBInspector 实例

    注意：这个函数在重构过程中保留，用于兼容 web_ui.py 中的旧代码。
    新代码应该直接使用 TiDBInspector 类。
    """
    inspector = TiDBInspector(ip, port, user, password, database, ssh_info, template_id)
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
            """委托给 inspector.generate_report()"""
            return self.inspector.generate_report(output_file, inspector_name)
    return CompatWrapper(inspector)

def main():
    """TiDB 巡检 CLI 入口"""
    import getpass

    print(u"TiDB 数据库巡检")
    print(u"=" * 50)

    host = input(u"主机地址 [localhost]: ") or "localhost"
    port = int(input(u"端口 [4000]: ") or 4000)
    user = input(u"用户名: ")
    if not user:
        print(u"用户名不能为空"); return
    password = getpass.getpass(u"密码: ")
    database = input(u"数据库名 [mysql]: ") or "mysql"

    inspector = TiDBInspector(host, port, user, password, database)
    ok, ver = inspector.connect()
    if not ok:
        print(u"连接失败: {}".format(ver)); return
    print(u"连接成功: {}".format(ver))

    inspector.collect_data()
    name = "{}_{}".format(host, port)
    output = "TiDB_Inspection_Report_{}.docx".format(name)
    inspector.generate_report(output, name)
    print(u"报告已生成: {}".format(output))


if __name__ == '__main__':
    main()
