# -*- coding: utf-8 -*-
"""
数据库巡检工具统一入口
===========================
作者: Jack Ge
版本: v2.3.1
功能: 提供 MySQL、PostgreSQL、Oracle 和达梦 DM8 数据库巡检的统一入口
"""

import sys
import os
import warnings

# 屏蔽打包后 jinja2/markupsafe 引发的 pkg_resources 废弃警告
warnings.filterwarnings("ignore", category=UserWarning, message="pkg_resources is deprecated")

# frozen 模式下 sys._MEIPASS 包含打包后的临时目录，
# 将其加入搜索路径以确保子模块能找到 version.py 等资源
if getattr(sys, 'frozen', False):
    sys.path.insert(0, sys._MEIPASS)

from version import __version__ as VER


def _enable_ansi():
    """Windows 旧终端开启 ANSI 颜色支持"""
    try:
        import ctypes
        if os.name == "nt":
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


_enable_ansi()
CYAN    = "\033[96m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
MAGENTA = "\033[95m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"
RED     = "\033[91m"


def print_banner():
    art = f"""
{CYAN}{BOLD}  ██████╗ ██████╗  ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗
  ██╔══██╗██╔══██╗██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝
  ██║  ██║██████╔╝██║     ███████║██║     ██║     █████╔╝
  ██║  ██║██╔══██╗██║     ██╔══██║██╔══╝  ██║     ██╔═██╗
  ██████╔╝██████╔╝╚██████╗██║  ██║███████╗╚██████╗██║  ██╗
  ╚═════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝{RESET}
{BOLD}          🗄️  数据库自动化巡检工具  {VER}  统一入口{RESET}
{DIM}  ──────────────────────────────────────────────────────────{RESET}
{GREEN}{BOLD}    🐬  1 │ MySQL      {RESET}{DIM}MySQL 命令行巡检{RESET}
{CYAN}{BOLD}    🐘  2 │ PostgreSQL {RESET}{DIM}PostgreSQL 命令行巡检{RESET}
{RED}{BOLD}    🔴  3 │ Oracle     {RESET}{DIM}Oracle (11g以上版本) 命令行巡检{RESET}
{GREEN}{BOLD}    🟡  4 │ DM8 达梦  {RESET}{DIM}达梦 DM8 数据库命令行巡检{RESET}
{YELLOW}    📋  5 │ 生成巡检模板    {RESET}{DIM}生成批量巡检 Excel 模板{RESET}
{MAGENTA}    🌐  6 │ 启动 Web UI     {RESET}{DIM}通过可视化界面巡检{RESET}
{DIM}        7 │ 退出{RESET}
{DIM}  ──────────────────────────────────────────────────────────{RESET}
"""
    print(art)


def _run_web_ui():
    """启动 Web UI"""
    import web_ui
    print("\n🌐 正在启动 Web UI，请在浏览器打开 http://localhost:5003")
    print("   按 Ctrl+C 停止服务\n")
    try:
        web_ui.socketio.run(web_ui.app, host='0.0.0.0', port=5003)
    except KeyboardInterrupt:
        print("\n⏹️  Web UI 已停止")


def _run_mysql():
    import main_mysql
    main_mysql.main()


def _run_pg():
    import main_pg
    main_pg.main()


def _run_oracle():
    import main_oracle
    main_oracle.main()


def _run_dm():
    import main_dm
    main_dm.main()


def _run_template_menu():
    while True:
        print(f"\n{BOLD}{'='*50}{RESET}")
        print(f"{YELLOW}{BOLD}   批量巡检模板生成{RESET}")
        print(f"{DIM}{'='*50}{RESET}")
        print(f"  {GREEN}1{RESET}. MySQL 批量巡检模板 (xlsx)")
        print(f"  {CYAN}2{RESET}. PostgreSQL 批量巡检模板 (xlsx)")
        print(f"  {RED}3{RESET}. Oracle 批量巡检模板 (xlsx)")
        print(f"  {GREEN}4{RESET}. DM8 达梦 批量巡检模板 (xlsx)")
        print(f"  {DIM}0. 返回主菜单{RESET}")
        print(f"{DIM}{'='*50}{RESET}")
        sub = input("请选择 [0-4]: ").strip()

        if sub == '1':
            import main_mysql
            if hasattr(main_mysql, 'create_excel_template'):
                main_mysql.create_excel_template()
            else:
                print("❌ 当前版本不支持 MySQL 批量模板")
        elif sub == '2':
            import main_pg
            if hasattr(main_pg, 'create_excel_template'):
                main_pg.create_excel_template()
            else:
                print("❌ 当前版本不支持 PostgreSQL 批量模板")
        elif sub == '3':
            import main_oracle
            if hasattr(main_oracle, 'create_excel_template'):
                main_oracle.create_excel_template()
            else:
                print("❌ 当前版本不支持 Oracle 批量模板")
        elif sub == '4':
            import main_dm
            if hasattr(main_dm, 'create_excel_template'):
                main_dm.create_excel_template()
            else:
                print("❌ 当前版本不支持 DM8 批量模板")
        elif sub in ('0', ''):
            break
        else:
            print("\n❌ 无效选择。")


def main():
    while True:
        print_banner()
        choice = input("请选择功能 (1-7): ").strip().lower()

        if choice == '1':
            print("\n正在启动 MySQL 数据库巡检工具...\n")
            _run_mysql()
        elif choice == '2':
            print("\n正在启动 PostgreSQL 数据库巡检工具...\n")
            _run_pg()
        elif choice == '3':
            print("\n正在启动 Oracle 数据库巡检工具...\n")
            _run_oracle()
        elif choice == '4':
            print("\n正在启动达梦 DM8 数据库巡检工具...\n")
            _run_dm()
        elif choice == '5':
            _run_template_menu()
        elif choice == '6':
            _run_web_ui()
        elif choice == '7':
            print("\n感谢使用 DBCheck 数据库巡检工具，再见！👋")
            break
        else:
            print("\n❌ 无效选择，请输入 1-7。")


if __name__ == '__main__':
    main()
