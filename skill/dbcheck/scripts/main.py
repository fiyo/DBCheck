# -*- coding: utf-8 -*-
"""
数据库巡检工具统一入口
===========================
作者: 内部开发团队
版本: v2.0
功能: 提供 MySQL 和 PostgreSQL 数据库巡检的统一入口
"""

import subprocess
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def print_banner():
    """打印统一入口横幅"""
    print("=" * 60)
    print("            数据库巡检工具 v2.0 统一入口")
    print("=" * 60)
    print()
    print("  支持数据库类型:")
    print("    1) MySQL      - MySQL 数据库健康检查与报告生成")
    print("    2) PostgreSQL - PostgreSQL 数据库健康检查与报告生成")
    print()
    print("  其他功能:")
    print("    3) 生成 Excel 批量巡检模板 (MySQL)")
    print("    4) 生成 Excel 批量巡检模板 (PostgreSQL)")
    print("    5) 退出")
    print()
    print("=" * 60)


def run_mysql_inspector():
    """启动 MySQL 巡检工具"""
    script = os.path.join(SCRIPT_DIR, "main_mysql.py")
    try:
        subprocess.run([sys.executable, script], check=False)
    except Exception as e:
        print(f"\n❌ 启动 MySQL 巡检工具失败: {e}")
        input("\n按回车键返回...")


def run_pg_inspector():
    """启动 PostgreSQL 巡检工具"""
    script = os.path.join(SCRIPT_DIR, "main_pg.py")
    try:
        subprocess.run([sys.executable, script], check=False)
    except Exception as e:
        print(f"\n❌ 启动 PostgreSQL 巡检工具失败: {e}")
        input("\n按回车键返回...")


def main():
    """统一入口主函数"""
    while True:
        print_banner()
        choice = input("请选择数据库类型 (1-5): ").strip()

        if choice == '1':
            print("\n正在启动 MySQL 数据库巡检工具...")
            print("-" * 40)
            run_mysql_inspector()
        elif choice == '2':
            print("\n正在启动 PostgreSQL 数据库巡检工具...")
            print("-" * 40)
            run_pg_inspector()
        elif choice == '3':
            # 直接调用 main_mysql.py 的模板生成功能
            # 通过 -G 参数触发（如果有的话），否则还是启动完整程序让用户在菜单里选
            # 注: 如果 main_mysql.py 没有独立的命令行参数支持模板生成，就启动完整程序
            print("\n⚠️  请选择选项 1 进入 MySQL 巡检菜单，选择 3 生成模板。")
            input("\n按回车键返回...")
        elif choice == '4':
            print("\n⚠️  请选择选项 2 进入 PostgreSQL 巡检菜单，选择 3 生成模板。")
            input("\n按回车键返回...")
        elif choice == '5':
            print("\n感谢使用数据库巡检工具，再见！")
            break
        else:
            print("\n❌ 无效选择，请输入 1-5 之间的数字。")
            input("\n按回车键继续...")


if __name__ == '__main__':
    main()
