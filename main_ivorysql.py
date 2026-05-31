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
IvorySQL 数据库自动化健康巡检工具 {VER}
支持 IvorySQL v3.0 及以上版本 (基于 PostgreSQL)
依赖: psycopg2-binary, python-docx, docxtpl, openpyxl, psutil, paramiko>=2.8,<2.10
注意: IvorySQL 是 PostgreSQL 的兼容分支，使用 psycopg2 驱动
"""

import warnings
warnings.filterwarnings("ignore")

# IvorySQL 驱动 (使用 psycopg2，因为 IvorySQL 基于 PostgreSQL)
try:
    import psycopg2 as ivorysql_driver
    IVORYSQL_DRIVER = 'psycopg2'
except ImportError:
    print(_t("ivorysql_driver_missing"))
    print("  pip install psycopg2-binary")
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
import subprocess
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


class IvorySQLInspector(BaseInspectionEngine):
    """
    IvorySQL 巡检引擎
    继承 BaseInspectionEngine，只需实现 connect() 和 get_template_id()
    """
    
    def __init__(self, host, port, user, password, database='ivorysql', ssh_info=None, template_id=None):
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'ivorysql'  # 设置数据库类型
        self._lang = get_lang()
        
    def connect(self):
        """
        连接 IvorySQL 数据库
        使用 psycopg2 驱动（IvorySQL 基于 PostgreSQL）
        """
        try:
            self.conn = ivorysql_driver.connect(
                host=self.host,
                port=int(self.port),
                user=self.user,
                password=self.password,
                database=self.database or 'ivorysql',
                connect_timeout=10
            )
            self.cursor = self.conn.cursor()
            
            # 获取版本信息
            self.cursor.execute("SELECT version()")
            version = self.cursor.fetchone()[0]
            self.context['version'] = [{'version': version}]
            
            print(_t("ivorysql_connect_success").format(host=self.host, port=self.port))
            return True, version
            
        except Exception as e:
            err_msg = str(e)
            print(_t("ivorysql_connect_fail").format(error=err_msg))
            return False, err_msg

# ── 保留原有 API 兼容性（供 web_ui.py 旧代码调用）────────────────────
def getData(ip, port, user, password, database='ivorysql', ssh_info=None, label=None, template_id=None):
    inspector = IvorySQLInspector(ip, port, user, password, database, ssh_info, template_id)
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
def main():
    parser = argparse.ArgumentParser(description=_t("ivorysql_cli_desc"))
    parser.add_argument('-H', '--host', required=True, help=_t("cli_host"))
    parser.add_argument('-P', '--port', type=int, default=5333, help=_t("ivorysql_cli_port"))
    parser.add_argument('-u', '--user', required=True, help=_t("cli_user"))
    parser.add_argument('-p', '--password', help=_t("cli_password"))
    parser.add_argument('-d', '--database', default='ivorysql', help=_t("cli_database"))
    parser.add_argument('-o', '--output', help=_t("cli_output"))
    parser.add_argument('--ssh-host', help=_t("cli_ssh_host"))
    parser.add_argument('--ssh-port', type=int, default=22, help=_t("cli_ssh_port"))
    parser.add_argument('--ssh-user', default='root', help=_t("cli_ssh_user"))
    parser.add_argument('--ssh-password', help=_t("cli_ssh_password"))
    args = parser.parse_args()
    
    # 获取密码
    password = args.password
    if not password:
        password = getpass.getpass(_t("cli_pwd_prompt"))
    
    # SSH 信息
    ssh_info = None
    if args.ssh_host:
        ssh_info = {
            'ssh_host': args.ssh_host,
            'ssh_port': args.ssh_port,
            'ssh_user': args.ssh_user,
            'ssh_password': args.ssh_password
        }
    
    # 创建巡检器
    inspector = IvorySQLInspector(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        database=args.database,
        ssh_info=ssh_info
    )
    
    # 连接数据库
    ok, version = inspector.connect()
    if not ok:
        print(_t("ivorysql_conn_fail_exit"))
        sys.exit(1)
    
    # 采集数据
    inspector.collect_data()
    
    # 生成报告
    output_file = args.output or f"IvorySQL_Inspection_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    inspector.generate_report(output_file)
    
    print(_t("ivorysql_report_generated").format(file=output_file))


if __name__ == '__main__':
    main()
