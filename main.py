# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck Professional — 专有商业软件，保留一切权利（Proprietary Software, All Rights Reserved）.
# See LICENSE for full license text.
#

"""
DBCheck 极简入口
=====================
推荐用户使用 Web UI（功能最全）。
CLI 巡检模式保留给高级用户。
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
  ██╔══██╗██╔══██╗██╔════╝██║  ██║██╔════╝██╔════╝██║  ██╔╝
  ██║  ██║██████╔╝██║     ███████║██║     ██║     █████╔╝
  ██║  ██║██╔══██╗██║     ██╔══██║██║     ██║     ██╔═██╗
  ██████╔╝██████╔╝╚██████╗██║  ██║███████╗╚██████╗██║  ██╗
  ╚═════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝{RESET}
{BOLD}          [DBCheck]  {title}  {VER}  {t("cli.main_menu_title")}{RESET}
{DIM}  ───────────────────────────────────────────────────────{RESET}
{CYAN}{BOLD}    {t("cli.main_menu_recommend")}{RESET}
{WHITE}{BOLD}    {t("cli.main_menu_line1")}{RESET}
{YELLOW}{BOLD}    {t("cli.main_menu_line2")}{RESET}
{DIM}  ───────────────────────────────────────────────────────{RESET}
"""
    try:
        print(art)
    except UnicodeEncodeError:
        import sys
        enc = sys.stdout.encoding or 'ascii'
        safe = art.encode(enc, errors='replace').decode(enc)
        print(safe)


# ── 数据库巡检函数（CLI 子菜单）────────────────────────────────────────────

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


def _run_ivorysql():
    import main_ivorysql
    main_ivorysql.main()


def _run_yashandb():
    import main_yashandb
    main_yashandb.main()


def _run_kingbase():
    import main_kingbase
    main_kingbase.main()


def _run_gbase():
    """启动 GBase 8s 数据库巡检"""
    import main_gbase
    main_gbase.main()


def _run_oracle_full():
    """Oracle 全面巡检（增强版，基于 OS 层 + 数据库层）"""
    import main_oracle_full
    import sys
    sys.argv = ['main_oracle_full']      # 重置，避免父进程 argv 干扰子模块的 argparse
    main_oracle_full.main()


# ── CLI 巡检子菜单 ──────────────────────────────────────────────────────────

def _run_inspect_menu():
    """CLI 巡检子菜单（高级用户）"""
    from i18n import t
    while True:
        print(f"\n{BOLD}{'='*50}{RESET}")
        print(f"{CYAN}{BOLD}   {t('cli.inspect_menu_title')}{RESET}")
        print(f"{DIM}{'='*50}{RESET}")
        print(f"  {GREEN}1{RESET}. {t('cli.inspect_menu_line1')}")
        print(f"  {CYAN}2{RESET}. {t('cli.inspect_menu_line2')}")
        print(f"  {RED}3{RESET}. {t('cli.inspect_menu_line3')}")
        print(f"  {ORANGE}4{RESET}. {t('cli.inspect_menu_line4')}")
        print(f"  {YELLOW}5{RESET}. {t('cli.inspect_menu_line5')}")
        print(f"  {GREEN}6{RESET}. {t('cli.inspect_menu_line6')}")
        print(f"  {CYAN}7{RESET}. {t('cli.inspect_menu_line7')}")
        print(f"  {MAGENTA}8{RESET}. {t('cli.inspect_menu_line8')}")
        print(f"  {YELLOW}9{RESET}. {t('cli.inspect_menu_line9')}")
        print(f"  {GREEN}10{RESET}. {t('cli.inspect_menu_line10')}")
        print(f"  {DIM}11{RESET}. {t('cli.inspect_menu_line11')}")
        print(f"  {DIM}0{RESET}. {t('cli.inspect_menu_line0')}")
        print(f"{DIM}{'='*50}{RESET}")
        choice = input(t("cli.inspect_menu_prompt")).strip()

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
            print(f"\n{t('cli.main_menu_ivorysql_starting')}\n")
            _run_ivorysql()
        elif choice == '8':
            print(f"\n{t('cli.main_menu_yashandb_starting')}\n")
            _run_yashandb()
        elif choice == '9':
            print(f"\n{t('cli.main_menu_kingbase_starting')}\n")
            _run_kingbase()
        elif choice == '10':
            print(f"\n{t('cli.main_menu_gbase_starting')}\n")
            _run_gbase()
        elif choice == '11':
            _run_template_menu()
        elif choice in ('0', ''):
            break
        else:
            print(f"\n{t('cli.inspect_menu_invalid')}")


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
        sub = input(t("cli.batch_menu_prompt")).strip()

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


# ── Web UI ───────────────────────────────────────────────────────

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
            _run_web_ui()
        elif choice == '2':
            _run_inspect_menu()
        elif choice == '0':
            print(f"\n{t('cli.main_menu_exiting')}")
            break
        else:
            print(f"\n{t('cli.main_menu_invalid')}")


if __name__ == '__main__':
    args = _parse_args()
    _init_i18n(args.lang)
    main()
