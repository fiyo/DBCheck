#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck
#
# MySQL 巡检报告生成诊断脚本（绕过 Web 界面，直接验证 generate_report 链路）
import os
import sys

# 确保能 import 项目模块
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.core.paths import PROJECT_ROOT, REPORTS_DIR
from modules.entrypoints.main_mysql import MySQLInspector


def main():
    print("=" * 60)
    print("MySQL 巡检报告生成诊断")
    print("=" * 60)
    print(f"PROJECT_ROOT : {PROJECT_ROOT}")
    print(f"REPORTS_DIR  : {REPORTS_DIR}")

    tpl = os.path.join(str(PROJECT_ROOT), "templates", "mysql_wordtemplates_v1.0.docx")
    print(f"模板路径     : {tpl}")
    print(f"模板是否存在 : {os.path.exists(tpl)}")

    # 连接参数（按需修改此处或运行时输入）
    ip = input("MySQL IP [127.0.0.1]: ").strip() or "127.0.0.1"
    port = int(input("MySQL Port [3306]: ").strip() or "3306")
    user = input("MySQL User [root]: ").strip() or "root"
    password = input("MySQL Password: ").strip()
    database = input("MySQL Database (可选): ").strip() or None

    print("-" * 60)
    print("1) 建立连接 ...")
    inspector = MySQLInspector(ip, port, user, password, database, {}, None, "")
    ok, ver = inspector.connect()
    if not ok:
        print(f"   [FAIL] 连接失败: {ver}")
        return
    print(f"   [OK] 连接成功，版本: {ver}")

    print("2) 收集数据 (collect_data) ...")
    try:
        inspector.collect_data()
    except Exception as e:
        import traceback
        print(f"   [FAIL] collect_data 异常: {e}")
        traceback.print_exc()
        # collect_data 失败仍尝试生成报告（看下游是否崩）
    print(f"   [INFO] context 字段数: {len(inspector.context)}")

    print("3) 生成报告 (generate_report) ...")
    os.makedirs(str(REPORTS_DIR), exist_ok=True)
    ofile = os.path.join(str(REPORTS_DIR), "diagnose_mysql.docx")
    if os.path.exists(ofile):
        os.remove(ofile)
    try:
        result = inspector.generate_report(ofile, "Jack")
    except Exception as e:
        import traceback
        print(f"   [FAIL] generate_report 抛出异常: {e}")
        traceback.print_exc()
        return

    print(f"   [INFO] generate_report 返回: {result}")
    print(f"   [INFO] 文件是否存在: {os.path.exists(ofile)}")
    if result and os.path.exists(result):
        print(f"   [OK] 报告生成成功: {result} (大小 {os.path.getsize(result)} 字节)")
    else:
        print("   [FAIL] 报告未生成 —— 这就是 Web 报错的根因")
        print("   请检查上方是否有 [ERROR] 生成报告失败 / [ERROR] 渲染上下文失败 等日志")


if __name__ == "__main__":
    main()
