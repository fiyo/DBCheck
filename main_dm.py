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
达梦 DM8 数据库自动化健康巡检工具 {VER}
支持 DM8 及以上版本
依赖: dmpython (pip install dmpython), python-docx, docxtpl, openpyxl, psutil, paramiko>=2.8,<2.10
注意: dmpython 需要达梦数据库自带的 dpi 动态库支持
"""

import warnings
warnings.filterwarnings("ignore")
import sys
import datetime
import argparse
import getpass

# 兼容函数 — 供 web_ui.py 旧代码调用
# (DmInspector 在下方定义，兼容函数仅在调用时才引用，不会提前报错)

def getData(ip, port, user, password, ssh_info=None, db_name=None, template_id=None):
    """
    原有 API - 创建 DmInspector 实例

    注意：这个函数在重构过程中保留，用于兼容 web_ui.py 中的旧代码。
    新代码应该直接使用 DmInspector 类。
    """
    inspector = DmInspector(ip, port, user, password, db_name, ssh_info, template_id)
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
    """原有 API - 创建 Word 模板（DM8 由 saveDoc 内部通过 inspector.generate_report 生成）"""
    return '_dm_inspector_generated_'


def saveDoc(context, ofile, ifile, inspector_name, **kwargs):
    """原有 API - 保存 Word 报告（委托给 DmInspector.generate_report）"""
    inspector = kwargs.get('H')  # web_ui.py 传 H=data.H，data 是 CompatWrapper
    class CompatWrapper:
        def __init__(self, context, ofile, inspector, inspector_name):
            self.context = context
            self.ofile = ofile
            self._inspector = inspector
            self._inspector_name = inspector_name

        def contextsave(self):
            if self._inspector and hasattr(self._inspector, 'generate_report'):
                return self._inspector.generate_report(self.ofile, self._inspector_name) is not None
            # 兜底：保存空文档
            from docx import Document
            doc = Document()
            doc.save(self.ofile)
            return True
    return CompatWrapper(context, ofile, inspector, inspector_name)


# 达梦 DM8 驱动
try:
    import dmPython as dm_driver
    DM_DRIVER = 'dmPython'
except ImportError:
    print(_t("dm8_driver_missing"))
    print("  pip install dmpython")
    print("  " + _t("dm8_driver_path_note"))
    sys.exit(1)


class DmInspector(BaseInspectionEngine):
    """
    达梦 DM8 巡检引擎
    继承 BaseInspectionEngine，只需实现 connect() 和 get_template_id()
    """
    
    def __init__(self, host, port, user, password, database=None, ssh_info=None, template_id=None):
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'dm8'  # 设置数据库类型
        self._lang = get_lang()
        
    def connect(self):
        """
        连接达梦 DM8 数据库
        使用 dmPython 驱动
        注意：dmPython.connect() 不支持单独的 port 参数，
             必须将 host:port 合并到 server 参数中
        """
        try:
            # 正确写法：server='host:port'（参考 instance_manager.py 第 448-449 行）
            dsn = '%s:%d' % (self.host, int(self.port))
            self.conn = dm_driver.connect(
                user=self.user,
                password=self.password,
                server=dsn  # 正确：'host:port' 格式
            )
            self.cursor = self.conn.cursor()
            
            # 获取达梦版本号
            try:
                self.cursor.execute("SELECT VERSION$ FROM V$VERSION")
                version = self.cursor.fetchone()[0]
            except Exception:
                try:
                    self.cursor.execute("SELECT BANNER FROM V$VERSION WHERE ROWNUM=1")
                    version = self.cursor.fetchone()[0]
                except Exception:
                    version = 'Unknown'
            self.context['version'] = [{'VERSION': version}]
            
            print(_t("dm8_connect_success").format(host=self.host, port=self.port))
            return True, version
            
        except Exception as e:
            err_msg = str(e)
            print(_t("dm8_connect_fail").format(error=err_msg))
            import traceback
            traceback.print_exc()
            return False, err_msg
    
# ============================================================
# CLI 入口
# ============================================================
def main_cli():
    """独立运行时的 argparse 入口"""
    parser = argparse.ArgumentParser(description=_t("dm8_cli_desc"))
    parser.add_argument('-H', '--host', required=True, help=_t("cli_host"))
    parser.add_argument('-P', '--port', type=int, default=5236, help=_t("dm8_cli_port"))
    parser.add_argument('-u', '--user', required=True, help=_t("cli_user"))
    parser.add_argument('-p', '--password', help=_t("cli_password"))
    parser.add_argument('-d', '--database', help=_t("cli_database"))
    parser.add_argument('-o', '--output', help=_t("cli_output"))
    parser.add_argument('--ssh-host', help=_t("cli_ssh_host"))
    parser.add_argument('--ssh-port', type=int, default=22, help=_t("cli_ssh_port"))
    parser.add_argument('--ssh-user', default='root', help=_t("cli_ssh_user"))
    parser.add_argument('--ssh-password', help=_t("cli_ssh_password"))
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass(_t("cli_pwd_prompt"))

    ssh_info = None
    if args.ssh_host:
        ssh_info = {
            'ssh_host': args.ssh_host,
            'ssh_port': args.ssh_port,
            'ssh_user': args.ssh_user,
            'ssh_password': args.ssh_password
        }

    inspector = DmInspector(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        database=args.database,
        ssh_info=ssh_info
    )

    ok, version = inspector.connect()
    if not ok:
        print(_t("dm8_conn_fail_exit"))
        sys.exit(1)

    inspector.collect_data()
    output_file = args.output or "DM8_Inspection_Report_{}.docx".format(datetime.now().strftime('%Y%m%d_%H%M%S'))
    inspector.generate_report(output_file)
    print(_t("dm8_report_generated").format(file=output_file))


def main(host=None, port=None, user=None, password=None, database=None, output=None, ssh_info=None):
    """DM8 巡检 CLI 入口 - 支持交互模式和参数模式"""
    if host is None:
        # 交互模式（从 main.py 调用时）
        print(u"DM8 达梦数据库巡检")
        print(u"=" * 50)
        host = input(u"主机地址 [localhost]: ") or "localhost"
        port = int(input(u"端口 [5236]: ") or 5236)
        user = input(u"用户名: ")
        if not user:
            print(u"用户名不能为空")
            return
        if password is None:
            password = getpass.getpass(u"密码: ")
        database = input(u"数据库名: ") or ""
        output = input(u"输出文件 [DM8_Inspection_Report.docx]: ") or "DM8_Inspection_Report.docx"

    inspector = DmInspector(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        ssh_info=ssh_info
    )

    ok, version = inspector.connect()
    if not ok:
        print(u"连接失败: {}".format(version))
        return

    inspector.collect_data()
    output_file = output or "DM8_Inspection_Report_{}.docx".format(datetime.now().strftime('%Y%m%d_%H%M%S'))
    inspector.generate_report(output_file)
    print(u"报告已生成: {}".format(output_file))


if __name__ == '__main__':
    main_cli()
