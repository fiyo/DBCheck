# -*- coding: utf-8 -*-
"""
数据库巡检工具统一入口
===========================
作者: Jack Ge
版本: v2.1
功能: 提供 MySQL、PostgreSQL 和 Oracle 数据库巡检的统一入口
"""

import subprocess
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _enable_ansi():
    """Windows 旧终端开启 ANSI 颜色支持"""
    try:
        import ctypes
        if os.name == "nt":
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

# 模块级 ANSI 颜色常量（供 print_banner 和子菜单共用）
_enable_ansi()
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
MAGENTA= "\033[95m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"
RED    = "\033[91m"


def print_banner():
    """打印统一入口横幅（彩色 ASCII Art）"""

    art = f"""
{CYAN}{BOLD}  ██████╗ ██████╗  ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗
  ██╔══██╗██╔══██╗██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝
  ██║  ██║██████╔╝██║     ███████║█████╗  ██║     █████╔╝
  ██║  ██║██╔══██╗██║     ██╔══██║██╔══╝  ██║     ██╔═██╗
  ██████╔╝██████╔╝╚██████╗██║  ██║███████╗╚██████╗██║  ██╗
  ╚═════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝{RESET}
{BOLD}          🗄️  数据库自动化巡检工具  v2.2  统一入口{RESET}
{DIM}  ──────────────────────────────────────────────────────────{RESET}
{GREEN}{BOLD}    🐬  1 │ MySQL      {RESET}{DIM}MySQL 数据库健康巡检与报告生成{RESET}
{CYAN}{BOLD}    🐘  2 │ PostgreSQL {RESET}{DIM}PostgreSQL 数据库健康巡检与报告生成{RESET}
{RED}{BOLD}    🔴  3 │ Oracle     {RESET}{DIM}Oracle (11g/12c/19c/21c) 数据库健康巡检与报告生成{RESET}
{YELLOW}    📋  4 │ 生成批量巡检模板{RESET}{DIM}生成 MySQL / PostgreSQL / Oracle 批量巡检 Excel 模板{RESET}
{MAGENTA}    🌐  5 │ 启动 Web UI     {RESET}{DIM}浏览器可视化操作界面{RESET}
{DIM}        6 │ 退出{RESET}
{DIM}  ──────────────────────────────────────────────────────────{RESET}
"""
    print(art)


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


def run_oracle_inspector():
    """启动 Oracle 巡检工具"""
    script = os.path.join(SCRIPT_DIR, "main_oracle.py")
    try:
        subprocess.run([sys.executable, script], check=False)
    except Exception as e:
        print(f"\n❌ 启动 Oracle 巡检工具失败: {e}")
        input("\n按回车键返回...")


def run_web_ui():
    """启动 Web UI"""
    script = os.path.join(SCRIPT_DIR, "web_ui.py")
    try:
        print("\n🌐 正在启动 Web UI，请在浏览器打开 http://localhost:5000")
        print("   按 Ctrl+C 停止服务\n")
        subprocess.run([sys.executable, script], check=False)
    except KeyboardInterrupt:
        print("\n⏹️  Web UI 已停止")
    except Exception as e:
        print(f"\n❌ 启动 Web UI 失败: {e}")
        input("\n按回车键返回...")


def _run_batch_template_menu():
    """统一批量巡检模板生成菜单（MySQL / PostgreSQL / Oracle）"""
    while True:
        print(f"\n{BOLD}{'='*50}{RESET}")
        print(f"{YELLOW}{BOLD}   批量巡检模板生成{RESET}")
        print(f"{DIM}{'='*50}{RESET}")
        print(f"  {GREEN}1{RESET}. MySQL 批量巡检模板 (xlsx)")
        print(f"  {CYAN}2{RESET}. PostgreSQL 批量巡检模板 (xlsx)")
        print(f"  {RED}3{RESET}. Oracle 批量巡检模板 (xlsx)")
        print(f"  {DIM} 0. 返回主菜单{RESET}")
        print(f"{DIM}{'='*50}{RESET}")
        sub = input("请选择 [0-3]: ").strip()

        if sub == '1':
            script = os.path.join(SCRIPT_DIR, "main_mysql.py")
            subprocess.run([sys.executable, script, "--template"], check=False)
        elif sub == '2':
            script = os.path.join(SCRIPT_DIR, "main_pg.py")
            subprocess.run([sys.executable, script, "--template"], check=False)
        elif sub == '3':
            script = os.path.join(SCRIPT_DIR, "main_oracle.py")
            subprocess.run([sys.executable, script, "--template"], check=False)
        elif sub == '0' or sub == '':
            break
        else:
            print("\n❌ 无效选择。")
            input("\n按回车键继续...")


def main():
    """统一入口主函数"""
    while True:
        print_banner()
        choice = input("请选择功能 (1-6): ").strip().lower()

        if choice == '1':
            print("\n正在启动 MySQL 数据库巡检工具...")
            run_mysql_inspector()
        elif choice == '2':
            print("\n正在启动 PostgreSQL 数据库巡检工具...")
            run_pg_inspector()
        elif choice == '3':
            print("\n正在启动 Oracle 数据库巡检工具...")
            run_oracle_inspector()
        elif choice == '4':
            _run_batch_template_menu()
        elif choice == '5':
            run_web_ui()
        elif choice == '6':
            print("\n感谢使用 DBCheck 数据库巡检工具，再见！👋")
            break
        else:
            print("\n❌ 无效选择，请输入 1-6。")
            input("\n按回车键继续...")


if __name__ == '__main__':
    main()

