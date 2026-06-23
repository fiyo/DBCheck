#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

from version import __version__ as VER
from i18n import get_lang, t as _t
from inspection_engine import BaseInspectionEngine

"""
KingbaseES 数据库自动化健康巡检工具 {VER}
支持 KingbaseES V009 及以上版本 (基于 PostgreSQL 兼容)
依赖: psycopg2-binary, python-docx, docxtpl, openpyxl, psutil, paramiko>=2.8,<2.10
注意: KingbaseES 是 PostgreSQL 兼容的国产数据库，使用 psycopg2 驱动
"""

import warnings
warnings.filterwarnings("ignore")

# KingbaseES 驱动 (使用 psycopg2，因为 KingbaseES 基于 PostgreSQL 兼容)
try:
    import psycopg2 as kingbase_driver
    KINGBASE_DRIVER = 'psycopg2'
except ImportError:
    print("KingbaseES 驱动缺失: 请安装 psycopg2-binary")
    print("  pip install psycopg2-binary")
    import sys
    sys.exit(1)

import sys
import datetime
import argparse
import subprocess
import logging
import logging.handlers
import socket
import re
import time
from pathlib import Path
import getpass
import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Cm
from docxtpl import DocxTemplate
import configparser
import importlib
import json
import hashlib
import base64
from datetime import datetime, timedelta
import platform
import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
import tempfile
import io
import psutil
import shutil
import paramiko


class KingbaseESInspector(BaseInspectionEngine):
    """
    KingbaseES 巡检引擎
    继承 BaseInspectionEngine，只需实现 connect() 和 get_template_id()
    """
    
    def __init__(self, host, port, user, password, database='kingbase', ssh_info=None, template_id=None):
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'kingbase'  # 设置数据库类型
        self._lang = get_lang()
        
    def connect(self):
        """
        连接 KingbaseES 数据库
        使用 psycopg2 驱动（KingbaseES 基于 PostgreSQL 兼容）
        """
        try:
            self.conn = kingbase_driver.connect(
                host=self.host,
                port=int(self.port),
                user=self.user,
                password=self.password,
                database=self.database or 'kingbase',
                connect_timeout=10
            )
            self.cursor = self.conn.cursor()
            
            # 获取版本信息
            self.cursor.execute("SELECT version()")
            version = self.cursor.fetchone()[0]
            self.context['version'] = [{'version': version}]
            
            print("KingbaseES 连接成功: {}:{}".format(self.host, self.port))
            return True, version
            
        except Exception as e:
            err_msg = str(e)
            print("KingbaseES 连接失败: {}".format(err_msg))
            return False, err_msg

# ── 保留原有 API 兼容性（供 web_ui.py 旧代码调用）────────────────────
def getData(ip, port, user, password, database='kingbase', ssh_info=None, label=None, template_id=None):
    inspector = KingbaseESInspector(ip, port, user, password, database, ssh_info, template_id)
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
    parser = argparse.ArgumentParser(description="KingbaseES Database Inspection Tool")
    parser.add_argument('-H', '--host', required=True, help='Host address')
    parser.add_argument('-P', '--port', type=int, default=54321, help='Port (default: 54321)')
    parser.add_argument('-u', '--user', required=True, help='Username')
    parser.add_argument('-p', '--password', help='Password')
    parser.add_argument('-d', '--database', default='kingbase', help='Database name (default: kingbase)')
    parser.add_argument('-o', '--output', help='Output file path')
    parser.add_argument('--ssh-host', help='SSH host for remote inspection')
    parser.add_argument('--ssh-port', type=int, default=22, help='SSH port (default: 22)')
    parser.add_argument('--ssh-user', default='root', help='SSH user (default: root)')
    parser.add_argument('--ssh-password', help='SSH password')
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass("Password: ")

    ssh_info = None
    if args.ssh_host:
        ssh_info = {
            'ssh_host': args.ssh_host,
            'ssh_port': args.ssh_port,
            'ssh_user': args.ssh_user,
            'ssh_password': args.ssh_password
        }

    inspector = KingbaseESInspector(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        database=args.database,
        ssh_info=ssh_info
    )

    ok, version = inspector.connect()
    if not ok:
        print("Connection failed: {}".format(version))
        sys.exit(1)

    inspector.collect_data()
    output_file = args.output or "KingbaseES_Inspection_Report_{}.docx".format(datetime.now().strftime('%Y%m%d_%H%M%S'))
    inspector.generate_report(output_file)
    print("Report generated: {}".format(output_file))


def main(host=None, port=None, user=None, password=None, database=None, output=None, ssh_info=None):
    """KingbaseES 巡检 CLI 入口 - 支持交互模式和参数模式"""
    if host is None:
        # 交互模式（从 main.py 调用时）
        print("KingbaseES 数据库巡检")
        print("=" * 50)
        host = input("主机地址 [localhost]: ") or "localhost"
        port = int(input("端口 [54321]: ") or 54321)
        user = input("用户名: ")
        if not user:
            print("用户名不能为空")
            return
        if password is None:
            password = getpass.getpass("密码: ")
        database = input("数据库名 [kingbase]: ") or "kingbase"
        output = input("输出文件 [KingbaseES_Inspection_Report.docx]: ") or "KingbaseES_Inspection_Report.docx"

    inspector = KingbaseESInspector(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        ssh_info=ssh_info
    )

    ok, version = inspector.connect()
    if not ok:
        print("连接失败: {}".format(version))
        return

    inspector.collect_data()
    output_file = output or "KingbaseES_Inspection_Report_{}.docx".format(datetime.now().strftime('%Y%m%d_%H%M%S'))
    inspector.generate_report(output_file)
    print("报告已生成: {}".format(output_file))


if __name__ == '__main__':
    main_cli()
