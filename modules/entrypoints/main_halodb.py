#!/usr/bin/env python3
# -*- coding:utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
HaloDB（羲和）数据库自动化健康巡检工具
HaloDB 兼容 PostgreSQL 协议（PG14 内核血统），使用 PostgreSQL JDBC 驱动
（org.postgresql.Driver / jdbc:postgresql://），psycopg2 作为直连回退。
"""

from modules.inspection.engine import BaseInspectionEngine

import warnings
warnings.filterwarnings("ignore")

import sys
import datetime
import getpass
import argparse

try:
    from i18n import get_lang, t as _t
except ImportError:  # 允许独立运行时降级
    def _t(key, **kwargs):
        return key

    def get_lang():
        return 'zh'


class HaloDBInspector(BaseInspectionEngine):
    """
    HaloDB 巡检引擎
    继承 BaseInspectionEngine，只需实现 connect() 和 get_template_id()
    """

    def __init__(self, host, port, user, password, database='halo', ssh_info=None, template_id=None, driver_version=''):
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'halodb'  # 设置数据库类型
        try:
            self._lang = get_lang()
        except Exception:
            pass
        self.driver_version = driver_version or ''

    def connect(self):
        """
        连接 HaloDB 数据库（统一 JDBC 连接层）。

        HaloDB 兼容 PostgreSQL 协议，复用 PostgreSQL JDBC 驱动。
        驱动 jar 优先「数据库驱动管理」登记版本（self.driver_version），
        否则回退 drivers/postgresql/ 自动发现。
        与其它 JDBC 类型同一套 modules.jdbc_connector.open_jdbc_connection。
        """
        try:
            import os as _os
            from modules.jdbc_connector import open_jdbc_connection
            from modules.core.paths import PROJECT_ROOT
            _conn, _meta = open_jdbc_connection(
                'halodb', self.host, int(self.port),
                user=self.user, password=self.password,
                database=self.database or 'halo',
                driver_version=self.driver_version,
                fallback_dirs=[_os.path.join(str(PROJECT_ROOT), 'drivers', 'postgresql')],
            )
            if _conn is None:
                return False, (_meta or {}).get('error') or 'HaloDB JDBC 连接失败'
            self.conn = _conn
            self.cursor = self.conn.cursor()

            # 获取版本信息
            self.cursor.execute("SELECT version()")
            version = self.cursor.fetchone()[0]
            self.context['version'] = [{'version': version}]

            print(f"[HaloDB] 已连接 {self.host}:{self.port}")
            return True, version

        except Exception as e:
            err_msg = str(e)
            print(f"[HaloDB] 连接失败: {err_msg}")
            return False, err_msg


# ── 保留原有 API 兼容性（供 web 层旧代码调用）────────────────────
def getData(ip, port, user, password, database='halo', ssh_info=None, label=None, template_id=None, driver_version=''):
    inspector = HaloDBInspector(ip, port, user, password, database, ssh_info, template_id, driver_version)
    ok, ver = inspector.connect()
    if not ok:
        return None

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


# ============================================================
# CLI 入口
# ============================================================
def main_cli():
    """独立运行时的 argparse 入口"""
    parser = argparse.ArgumentParser(description="HaloDB 数据库巡检工具")
    parser.add_argument('-H', '--host', required=True, help="主机地址")
    parser.add_argument('-P', '--port', type=int, default=5432, help="端口（默认 5432）")
    parser.add_argument('-u', '--user', required=True, help="用户名")
    parser.add_argument('-p', '--password', help="密码")
    parser.add_argument('-d', '--database', default='halo', help="数据库名（默认 halo0root 管理库下的业务库）")
    parser.add_argument('-o', '--output', help="输出文件")
    parser.add_argument('--ssh-host', help="SSH 主机")
    parser.add_argument('--ssh-port', type=int, default=22, help="SSH 端口")
    parser.add_argument('--ssh-user', default='root', help="SSH 用户")
    parser.add_argument('--ssh-password', help="SSH 密码")
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass("密码: ")

    ssh_info = None
    if args.ssh_host:
        ssh_info = {
            'ssh_host': args.ssh_host,
            'ssh_port': args.ssh_port,
            'ssh_user': args.ssh_user,
            'ssh_password': args.ssh_password
        }

    inspector = HaloDBInspector(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        database=args.database,
        ssh_info=ssh_info
    )

    ok, version = inspector.connect()
    if not ok:
        print(f"连接失败: {version}")
        sys.exit(1)

    inspector.collect_data()
    output_file = args.output or "HaloDB_Inspection_Report_{}.docx".format(datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    inspector.generate_report(output_file)
    print(f"报告已生成: {output_file}")


if __name__ == '__main__':
    main_cli()
