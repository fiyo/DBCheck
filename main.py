# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
数据库巡检工具统一入口
===========================
作者: Jack Ge
功能: 提供 MySQL、PostgreSQL、Oracle 和达梦 DM8 数据库巡检的统一入口
"""

import sys
import os
import warnings
import argparse

# 屏蔽打包后 jinja2/markupsafe 引发的 pkg_resources 废弃警告
warnings.filterwarnings("ignore", category=UserWarning, message="pkg_resources is deprecated")

# ── i18n 初始化（必须在其他模块导入之前）──────────────────────────
_i18n_loaded = False


def _init_i18n(lang_override=None):
    """初始化 i18n，设置全局语言偏好"""
    global _i18n_loaded
    if _i18n_loaded:
        return
    # lang_override: 来自 --lang 参数，CLI 临时覆盖，不写配置文件
    if lang_override:
        from i18n import set_lang
        set_lang(lang_override, persist=False)
    _i18n_loaded = True

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
WHITE   = "\033[97m"
ORANGE  = "\033[38;2;255;140;0m"  # 真橙色 RGB


def _parse_args():
    parser = argparse.ArgumentParser(description='DBCheck - Database Inspection Tool')
    parser.add_argument('--lang', dest='lang', default=None,
                        help='Language: zh (Chinese, default) or en (English)')
    return parser.parse_args()


def print_banner():
    from i18n import t
    title = t("cli.main_banner_title")
    art = f"""
{CYAN}{BOLD}  ██████╗ ██████╗  ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗
  ██╔══██╗██╔══██╗██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝
  ██║  ██║██████╔╝██║     ███████║██║     ██║     █████╔╝
  ██║  ██║██╔══██╗██║     ██╔══██║██╔══╝  ██║     ██╔═██╗
  ██████╔╝██████╔╝╚██████╗██║  ██║███████╗╚██████╗██║  ██╗
  ╚═════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝{RESET}
{BOLD}          🗄️  {title}  {VER}  {t("cli.main_menu_title")}{RESET}
{DIM}  ──────────────────────────────────────────────────────────{RESET}
{CYAN}{BOLD}    {t("cli.main_menu_line1")}{RESET}
{WHITE}{BOLD}    {t("cli.main_menu_line2")}{RESET}
{RED}{BOLD}    {t("cli.main_menu_line3")}{RESET}
{ORANGE}{BOLD}    {t("cli.main_menu_line4")}{RESET}
{YELLOW}{BOLD}    {t("cli.main_menu_line5")}{RESET}
{CYAN}{BOLD}    {t("cli.main_menu_line6")}{RESET}
{DIM}  ──────────────────────────────────────────────────────────{RESET}
{GREEN}{BOLD}    {t("cli.main_menu_line7")}{RESET}
{CYAN}{BOLD}    {t("cli.main_menu_line8")}{RESET}
{MAGENTA}{BOLD}    {t("cli.main_menu_line9")}{RESET}
{DIM}{BOLD}    {t("cli.main_menu_line10")}{RESET}
{DIM}  ──────────────────────────────────────────────────────────{RESET}
"""
    print(art)


# ── 数据库巡检函数 ────────────────────────────────────────────────

def _run_mysql():
    import main_mysql
    main_mysql.main()


def _run_pg():
    import main_pg
    main_pg.main()


def _run_dm():
    import main_dm
    main_dm.main()


def _run_sqlserver():
    import main_sqlserver
    main_sqlserver.main()

def _run_tidb():
    import main_tidb
    main_tidb.main()


def _run_oracle_full():
    """Oracle 全面巡检（增强版，基于 OS 层 + 数据库层）"""
    import main_oracle_full
    import sys
    sys.argv = ['main_oracle_full']      # 重置，避免父进程 argv 干扰子模块的 argparse
    main_oracle_full.main()


# ── 配置基线检查 ─────────────────────────────────────────────────

def _run_config_baseline():
    """启动配置基线检查向导"""
    from i18n import t
    import subprocess
    import sys

    print(f"\n{BOLD}{'='*50}{RESET}")
    print(f"{GREEN}{BOLD}   {t('cli.config_menu_title')}{RESET}")
    print(f"{DIM}{'='*50}{RESET}")

    # 选择数据库类型
    print(f"\n  {GREEN}1{RESET}. MySQL")
    print(f"  {CYAN}2{RESET}. PostgreSQL")
    print(f"  {DIM}0{RESET}. {t('cli.batch_menu_opt0')}")
    sub = input(f"\n{t('cli.batch_menu_prompt')}").strip()

    if sub == '1':
        db_type = 'mysql'
    elif sub == '2':
        db_type = 'pg'
    elif sub in ('0', ''):
        return
    else:
        print(f"\n{t('cli.batch_menu_invalid')}")
        return

    # 收集连接信息
    print(f"\n{t('cli_db_info_title')}")
    host = input(f"{t('cli_db_host').format(default='localhost')}").strip() or 'localhost'
    port = input(f"{t('cli_db_port').format(default='3306' if db_type == 'mysql' else '5432')}").strip()
    user = input(f"{t('cli_db_user').format(default='root' if db_type == 'mysql' else 'postgres')}").strip()
    password = input(f"{t('cli_db_password')}").strip()
    label = input(f"{t('cli_db_name').format(default='config_baseline')}").strip() or 'config_baseline'

    if not host or not user or not password:
        print(f"\n{t('cli.input_required_missing')}")
        return

    # 调用巡检脚本，带 --check-config 参数
    try:
        script_map = {'mysql': 'main_mysql.py', 'pg': 'main_pg.py'}
        script = script_map.get(db_type)
        if not script:
            return

        cmd = [sys.executable, script, '--check-config',
               '--host', host, '--port', port or ('3306' if db_type == 'mysql' else '5432'),
               '--user', user, '--password', password, '--label', label]
        subprocess.run(cmd)
    except Exception as e:
        print(f"\n{t('cli.config_baseline_error')}: {e}")


# ── 索引健康分析 ─────────────────────────────────────────────────

def _run_index_health():
    """启动索引健康分析向导"""
    from i18n import t
    import subprocess
    import sys

    print(f"\n{BOLD}{'='*50}{RESET}")
    print(f"{YELLOW}{BOLD}   {t('cli.index_menu_title')}{RESET}")
    print(f"{DIM}{'='*50}{RESET}")

    # 选择数据库类型
    print(f"\n  {GREEN}1{RESET}. MySQL")
    print(f"  {CYAN}2{RESET}. PostgreSQL")
    print(f"  {DIM}0{RESET}. {t('cli.batch_menu_opt0')}")
    sub = input(f"\n{t('cli.batch_menu_prompt')}").strip()

    if sub == '1':
        db_type = 'mysql'
    elif sub == '2':
        db_type = 'pg'
    elif sub in ('0', ''):
        return
    else:
        print(f"\n{t('cli.batch_menu_invalid')}")
        return

    # 收集连接信息
    print(f"\n{t('cli_db_info_title')}")
    host = input(f"{t('cli_db_host').format(default='localhost')}").strip() or 'localhost'
    port = input(f"{t('cli_db_port').format(default='3306' if db_type == 'mysql' else '5432')}").strip()
    user = input(f"{t('cli_db_user').format(default='root' if db_type == 'mysql' else 'postgres')}").strip()
    password = input(f"{t('cli_db_password')}").strip()
    label = input(f"{t('cli_db_name').format(default='index_health')}").strip() or 'index_health'

    if not host or not user or not password:
        print(f"\n{t('cli.input_required_missing')}")
        return

    # 调用巡检脚本，带 --check-indexes 参数
    try:
        script_map = {'mysql': 'main_mysql.py', 'pg': 'main_pg.py'}
        script = script_map.get(db_type)
        if not script:
            return

        cmd = [sys.executable, script, '--check-indexes',
               '--host', host, '--port', port or ('3306' if db_type == 'mysql' else '5432'),
               '--user', user, '--password', password, '--label', label]
        subprocess.run(cmd)
    except Exception as e:
        print(f"\n{t('cli.index_health_error')}: {e}")


# ── 批量模板生成 ─────────────────────────────────────────────────

def _run_template_menu():
    from i18n import t
    while True:
        print(f"\n{BOLD}{'='*50}{RESET}")
        print(f"{CYAN}{BOLD}   {t('cli.batch_menu_title')}{RESET}")
        print(f"{DIM}{'='*50}{RESET}")
        print(f"  {GREEN}1{RESET}. {t('cli.template_mysql')}")
        print(f"  {CYAN}2{RESET}. {t('cli.template_pg')}")
        print(f"  {RED}3{RESET}. {t('cli.template_dm')}")
        print(f"  {DIM}{t('cli.batch_menu_opt0')}{RESET}")
        print(f"{DIM}{'='*50}{RESET}")
        sub = input(f"{t('cli.batch_menu_prompt')}").strip()

        if sub == '1':
            import main_mysql
            if hasattr(main_mysql, 'create_excel_template'):
                main_mysql.create_excel_template()
            else:
                print(t("cli.batch_menu_not_support").format("MySQL"))
        elif sub == '2':
            import main_pg
            if hasattr(main_pg, 'create_excel_template'):
                main_pg.create_excel_template()
            else:
                print(t("cli.batch_menu_not_support").format("PostgreSQL"))
        elif sub == '3':
            import main_dm
            if hasattr(main_dm, 'create_excel_template'):
                main_dm.create_excel_template()
            else:
                print(t("cli.batch_menu_not_support").format("DM8"))
        elif sub in ('0', ''):
            break
        else:
            print(f"\n{t('cli.batch_menu_invalid')}")


# ── Web UI ────────────────────────────────────────────────────────

def _run_web_ui():
    """启动 Web UI"""
    from i18n import t
    import web_ui
    print(f"\n{t('cli.webui_starting')}")
    print(f"   {t('cli.webui_stop_hint')}\n")
    try:
        web_ui.socketio.run(web_ui.app, host='0.0.0.0', port=5003)
    except KeyboardInterrupt:
        print(f"\n{t('cli.webui_stopped')}")


# ── 主循环 ───────────────────────────────────────────────────────

def main():
    from i18n import t
    while True:
        print_banner()
        choice = input(t("cli.main_menu_prompt")).strip()

        if choice == '1':
            print(f"\n{t('cli.main_menu_mysql_starting')}\n")
            _run_mysql()
        elif choice == '2':
            print(f"\n{t('cli.main_menu_pg_starting')}\n")
            _run_pg()
        elif choice == '3':
            print(f"\n{t('cli.main_menu_oracle_starting')}\n")
            _run_oracle_full()
        elif choice == '4':
            print(f"\n{t('cli.main_menu_sqlserver_starting')}\n")
            _run_sqlserver()
        elif choice == '5':
            print(f"\n{t('cli.main_menu_dm_starting')}\n")
            _run_dm()
        elif choice == '6':
            print(f"\n{t('cli.main_menu_tidb_starting')}\n")
            _run_tidb()
        elif choice == '7':
            _run_template_menu()
        elif choice == '8':
            _run_web_ui()
        elif choice == '0':
            print(f"\n{t('cli.main_menu_exiting')}")
            break
        else:
            print(f"\n{t('cli.main_menu_invalid')}")


if __name__ == '__main__':
    args = _parse_args()
    _init_i18n(args.lang)
    main()
