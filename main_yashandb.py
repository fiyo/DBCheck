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
崖山 YashanDB 数据库自动化健康巡检工具 {VER}
支持 YashanDB 23+ 及以上版本
依赖: yasdb (pip install yasdb), python-docx, docxtpl, openpyxl, psutil, paramiko>=2.8,<2.10
"""

import warnings
warnings.filterwarnings("ignore")
import sys
import datetime
import argparse
import getpass

# 兼容函数 — 供 web_ui.py 旧代码调用

def getData(ip, port, user, password, ssh_info=None, db_name=None, template_id=None):
    """
    原有 API - 创建 YashanDbInspector 实例
    """
    inspector = YashanDbInspector(ip, port, user, password, db_name, ssh_info, template_id)
    ok, ver = inspector.connect()
    if not ok:
        return None

    class CompatWrapper:
        def __init__(self, inspector):
            self.inspector = inspector
            self.conn_db = inspector.conn

        def checkdb(self, sqlfile=''):
            self.inspector.collect_data()
            return self.inspector.context

        def generate_report(self, output_file, inspector_name="Jack"):
            return self.inspector.generate_report(output_file, inspector_name)

    return CompatWrapper(inspector)


def create_word_template(inspector_name):
    """原有 API - 创建 Word 模板"""
    return '_yashandb_inspector_generated_'


def saveDoc(context, ofile, ifile, inspector_name, **kwargs):
    """原有 API - 保存 Word 报告"""
    inspector = kwargs.get('H')
    class CompatWrapper:
        def __init__(self, context, ofile, inspector, inspector_name):
            self.context = context
            self.ofile = ofile
            self._inspector = inspector
            self._inspector_name = inspector_name

        def contextsave(self):
            if self._inspector and hasattr(self._inspector, 'generate_report'):
                return self._inspector.generate_report(self.ofile, self._inspector_name) is not None
            from docx import Document
            doc = Document()
            doc.save(self.ofile)
            return True
    return CompatWrapper(context, ofile, inspector, inspector_name)


# 崖山 YashanDB 驱动
try:
    import yasdb as yashandb_driver
    YASHANDB_DRIVER = 'yasdb'
except ImportError:
    print(_t("yashandb_driver_missing"))
    print("  pip install yasdb")
    sys.exit(1)


class YashanDbInspector(BaseInspectionEngine):
    """
    崖山 YashanDB 巡检引擎
    继承 BaseInspectionEngine，只需实现 connect()
    """

    def __init__(self, host, port, user, password, database=None, ssh_info=None, template_id=None):
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'yashandb'
        self._lang = get_lang()

    def connect(self):
        """
        连接崖山 YashanDB 数据库
        使用 yasdb 驱动
        yasdb.connect(host='...', port=1688, user='sys', password='...')
        注意：yasdb.connect() 不需要 database 参数
        """
        try:
            self.conn = yashandb_driver.connect(
                host=self.host,
                port=int(self.port),
                user=self.user,
                password=self.password
            )
            self.cursor = self.conn.cursor()

            # 获取 YashanDB 版本号
            try:
                self.cursor.execute("SELECT BANNER FROM V$VERSION WHERE ROWNUM=1")
                version = self.cursor.fetchone()[0]
            except Exception:
                try:
                    self.cursor.execute("SELECT VERSION FROM V$INSTANCE")
                    version = self.cursor.fetchone()[0]
                except Exception:
                    version = 'Unknown'
            self.context['version'] = [{'VERSION': version}]

            print(_t("yashandb_connect_success").format(host=self.host, port=self.port))
            return True, version

        except Exception as e:
            err_msg = str(e)
            print(_t("yashandb_connect_fail").format(error=err_msg))
            import traceback
            traceback.print_exc()
            return False, err_msg

# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description=_t("yashandb_cli_desc"))
    parser.add_argument('-H', '--host', required=True, help=_t("cli_host"))
    parser.add_argument('-P', '--port', type=int, default=1688, help=_t("yashandb_cli_port"))
    parser.add_argument('-u', '--user', default='sys', help=_t("cli_user"))
    parser.add_argument('-p', '--password', help=_t("cli_password"))
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
    inspector = YashanDbInspector(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        ssh_info=ssh_info
    )

    # 连接数据库
    ok, version = inspector.connect()
    if not ok:
        print(_t("yashandb_conn_fail_exit"))
        sys.exit(1)

    # 采集数据
    inspector.collect_data()

    # 生成报告
    output_file = args.output or f"YashanDB_Inspection_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    inspector.generate_report(output_file)

    print(_t("yashandb_report_generated").format(file=output_file))


if __name__ == '__main__':
    main()
