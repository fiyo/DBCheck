#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DBCheck 非交互式巡检脚本
=======================
通过命令行参数直接执行巡检，无需交互输入。
用法:
  python run_inspection.py --type mysql --host 127.0.0.1 --port 3306 \
      --user root --password secret --label "生产库" --inspector "张三"
  python run_inspection.py --type pg --host 127.0.0.1 --port 5432 \
      --user postgres --password secret --database mydb --label "PG主库" --inspector "李四"
"""

import argparse
import os
import sys
import traceback
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run_mysql(db_info, inspector_name, ssh_info=None):
    """执行 MySQL 巡检"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("main_mysql", os.path.join(SCRIPT_DIR, "main_mysql.py"))
    mod = importlib.util.module_from_spec(spec)

    class _FakeInfos:
        label = db_info.get('label', 'DBCheck')
        sqltemplates = 'builtin'
        batch = False
    mod.infos = _FakeInfos()
    spec.loader.exec_module(mod)
    mod.infos = _FakeInfos()

    ifile = mod.create_word_template(inspector_name)
    if not ifile:
        raise RuntimeError("Word 模板创建失败")

    reports_dir = os.path.join(SCRIPT_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    file_name = f"MySQL巡检报告_{db_info['label']}_{timestamp}.docx"
    ofile = os.path.join(reports_dir, file_name)

    data = mod.getData(
        db_info['host'], db_info['port'],
        db_info['user'], db_info['password'],
        ssh_info or {}
    )
    if data is None or data.conn_db2 is None:
        raise RuntimeError("无法建立数据库连接，请检查连接参数")

    ret = data.checkdb('builtin')
    if not ret:
        raise RuntimeError("巡检执行失败（checkdb 返回空）")

    ret.update({"co_name": [{'CO_NAME': db_info['label']}]})
    ret.update({"port": [{'PORT': db_info['port']}]})
    ret.update({"ip": [{'IP': db_info['host']}]})

    savedoc = mod.saveDoc(context=ret, ofile=ofile, ifile=ifile, inspector_name=inspector_name)
    success = savedoc.contextsave()

    try:
        if os.path.exists(ifile):
            os.remove(ifile)
    except Exception:
        pass

    if not success:
        raise RuntimeError("Word 报告渲染失败")

    return ofile, file_name


def run_pg(db_info, inspector_name, ssh_info=None):
    """执行 PostgreSQL 巡检"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("main_pg", os.path.join(SCRIPT_DIR, "main_pg.py"))
    mod = importlib.util.module_from_spec(spec)

    class _FakeInfos:
        label = db_info.get('label', 'DBCheck')
        sqltemplates = 'builtin'
        batch = False
    mod.infos = _FakeInfos()
    spec.loader.exec_module(mod)
    mod.infos = _FakeInfos()

    ifile = mod.create_word_template(inspector_name)
    if not ifile:
        raise RuntimeError("Word 模板创建失败")

    reports_dir = os.path.join(SCRIPT_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    file_name = f"PostgreSQL巡检报告_{db_info['label']}_{timestamp}.docx"
    ofile = os.path.join(reports_dir, file_name)

    data = mod.getData(
        db_info['host'], db_info['port'],
        db_info['user'], db_info['password'],
        database=db_info.get('database', 'postgres'),
        ssh_info=ssh_info or {}
    )
    if data is None or data.conn_db2 is None:
        raise RuntimeError("无法建立数据库连接，请检查连接参数")

    ret = data.checkdb('builtin')
    if not ret:
        raise RuntimeError("巡检执行失败（checkdb 返回空）")

    ret.update({"co_name": [{'CO_NAME': db_info['label']}]})
    ret.update({"port": [{'PORT': db_info['port']}]})
    ret.update({"ip": [{'IP': db_info['host']}]})

    savedoc = mod.saveDoc(context=ret, ofile=ofile, ifile=ifile, inspector_name=inspector_name)
    success = savedoc.contextsave()

    try:
        if os.path.exists(ifile):
            os.remove(ifile)
    except Exception:
        pass

    if not success:
        raise RuntimeError("Word 报告渲染失败")

    return ofile, file_name


def main():
    parser = argparse.ArgumentParser(
        description="DBCheck 数据库巡检工具（无交互版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_inspection.py --type mysql --host 192.168.1.10 --port 3306 \\
      --user root --password mypass --label "生产MySQL" --inspector "张三"

  python run_inspection.py --type pg --host 192.168.1.20 --port 5432 \\
      --user postgres --password mypass --database mydb \\
      --label "PG从库" --inspector "李四" \\
      --ssh-host 192.168.1.20 --ssh-user root --ssh-password mypass
"""
    )
    parser.add_argument('--type', required=True, choices=['mysql', 'pg'],
                        help='数据库类型: mysql 或 pg')
    parser.add_argument('--host', required=True, help='数据库主机 IP 或域名')
    parser.add_argument('--port', type=int, default=None,
                        help='数据库端口（默认: MySQL 3306, PG 5432）')
    parser.add_argument('--user', required=True, help='数据库用户名')
    parser.add_argument('--password', required=True, help='数据库密码')
    parser.add_argument('--database', default=None,
                        help='数据库名（PG 专有，默认 postgres）')
    parser.add_argument('--label', required=True,
                        help='数据库标签（用于报告命名，如"生产库-MySQL"）')
    parser.add_argument('--inspector', required=True,
                        help='巡检人员姓名')
    parser.add_argument('--ssh-host', default=None, help='SSH 主机 IP（可选）')
    parser.add_argument('--ssh-port', type=int, default=22, help='SSH 端口（默认 22）')
    parser.add_argument('--ssh-user', default=None, help='SSH 用户名（可选）')
    parser.add_argument('--ssh-password', default=None, help='SSH 密码（可选）')
    parser.add_argument('--ssh-key', default=None,
                        help='SSH 私钥文件路径（可选，与密码二选一）')

    args = parser.parse_args()

    if args.port is None:
        args.port = 3306 if args.type == 'mysql' else 5432

    db_info = {
        'label':    args.label,
        'host':     args.host,
        'port':     args.port,
        'user':     args.user,
        'password': args.password,
    }
    if args.database:
        db_info['database'] = args.database

    ssh_info = None
    if args.ssh_host:
        ssh_info = {
            'ssh_host':     args.ssh_host,
            'ssh_port':     args.ssh_port,
            'ssh_user':     args.ssh_user,
            'ssh_password': args.ssh_password or '',
            'ssh_key_file': args.ssh_key or '',
        }

    print(f"\n[{'MySQL' if args.type=='mysql' else 'PostgreSQL'}] 开始巡检: {args.label} ({args.host}:{args.port})")
    print("-" * 50)

    try:
        if args.type == 'mysql':
            ofile, fname = run_mysql(db_info, args.inspector, ssh_info)
        else:
            ofile, fname = run_pg(db_info, args.inspector, ssh_info)

        print("-" * 50)
        print(f"✅ 巡检完成！")
        print(f"📄 报告路径: {ofile}")
    except Exception as e:
        print("-" * 50)
        print(f"❌ 巡检失败: {e}")
        print(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()
