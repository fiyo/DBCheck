#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck 依赖检查脚本
运行此脚本检查所有依赖是否正确安装
"""
import sys
import os

def check_module(module_name, import_name=None, required=True):
    """检查模块是否可用"""
    if import_name is None:
        import_name = module_name

    try:
        __import__(import_name)
        print(f"  [OK] {module_name}")
        return True
    except ImportError as e:
        status = "[FAIL]" if required else "[WARN]"
        print(f"  {status} {module_name} - {e}")
        return False

def main():
    print("=" * 60)
    print("DBCheck 依赖检查")
    print("=" * 60)

    # 核心依赖
    print("\n[核心依赖]")
    results = []
    results.append(check_module("python-docx", "docx"))
    results.append(check_module("docxtpl"))
    results.append(check_module("psutil"))
    results.append(check_module("PyYAML", "yaml"))
    results.append(check_module("cryptography"))

    # Web UI
    print("\n[Web UI]")
    results.append(check_module("Flask"))
    results.append(check_module("Flask-SocketIO", "flask_socketio"))

    # 数据库驱动
    print("\n[数据库驱动]")
    results.append(check_module("pymysql", "pymysql"))
    results.append(check_module("psycopg2"))
    results.append(check_module("oracledb", "oracledb"))
    results.append(check_module("dmpython", "dmpython"))
    results.append(check_module("pyodbc", "pyodbc"))

    # 其他
    print("\n[其他]")
    results.append(check_module("paramiko"))
    results.append(check_module("openpyxl"))
    results.append(check_module("pandas"))
    results.append(check_module("reportlab"))
    results.append(check_module("APScheduler", "apscheduler"))

    # Pro 模块检查
    print("\n[Pro 模块]")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from pro import get_instance_manager, InstanceGroup
        print("  [OK] pro 模块加载成功")
        results.append(True)

        # 测试分组功能
        try:
            im = get_instance_manager()
            print(f"  [OK] 分组功能正常 (现有分组: {list(im._groups.keys())})")
        except Exception as e:
            print(f"  [WARN] 分组功能测试失败: {e}")

    except ImportError as e:
        print(f"  [FAIL] pro 模块加载失败: {e}")
        results.append(False)
    except Exception as e:
        print(f"  [FAIL] pro 模块异常: {e}")
        results.append(False)

    # 总结
    print("\n" + "=" * 60)
    if all(results):
        print("所有依赖检查通过！")
        print("=" * 60)
        return 0
    else:
        print("部分依赖缺失，请运行以下命令安装：")
        print("  pip install -r requirements.txt")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
