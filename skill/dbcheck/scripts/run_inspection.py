#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck 非交互式巡检脚本
=======================
通过命令行参数直接执行巡检，无需交互输入。

支持三种模式：
1. 完整巡检（默认）：生成完整 Word 巡检报告
2. 配置基线检查（--check-config）：检查配置参数与推荐值的差距
3. 索引健康分析（--check-indexes）：分析缺失/冗余/未使用索引
4. PDF 导出（--to-pdf）：将已生成的 DOCX 报告转换为 PDF

用法（完整巡检）:
  python run_inspection.py --type mysql --host 127.0.0.1 --port 3306 \
      --user root --password secret --label "生产库" --inspector "张三"

用法（配置基线检查）:
  python run_inspection.py --type mysql --host 127.0.0.1 --port 3306 \
      --user root --password secret --label "生产库" --check-config

用法（索引健康分析）:
  python run_inspection.py --type mysql --host 127.0.0.1 --port 3306 \
      --user root --password secret --label "生产库" --check-indexes

用法（PDF 导出）:
  python run_inspection.py --to-pdf /path/to/report.docx
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


def run_oracle_full(db_info, inspector_name, ssh_info=None):
    """执行 Oracle 全面巡检"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("main_oracle_full", os.path.join(SCRIPT_DIR, "main_oracle_full.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    reports_dir = os.path.join(SCRIPT_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    file_name = f"Oracle全面巡检报告_{db_info['label']}_{timestamp}.docx"
    ofile = os.path.join(reports_dir, file_name)

    data = mod.getData(
        db_info['host'], db_info['port'],
        db_info['user'], db_info['password'],
        db_info.get('service_name', ''),
        db_info.get('sid', ''),
        ssh_info or {}
    )
    if data is None:
        raise RuntimeError("无法建立数据库连接，请检查连接参数")

    ret = data.run_full_inspection(inspector_name)

    savedoc = mod.saveDoc(
        context=ret,
        ofile=ofile,
        inspector_name=inspector_name,
        label=db_info['label']
    )
    success = savedoc.contextsave()

    if not success:
        raise RuntimeError("Word 报告渲染失败")

    return ofile, file_name


def run_dm(db_info, inspector_name, ssh_info=None):
    """执行 DM8 达梦巡检"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("main_dm", os.path.join(SCRIPT_DIR, "main_dm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    reports_dir = os.path.join(SCRIPT_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    file_name = f"DM8巡检报告_{db_info['label']}_{timestamp}.docx"
    ofile = os.path.join(reports_dir, file_name)

    data = mod.getData(
        db_info['host'], db_info['port'],
        db_info['user'], db_info['password'],
        ssh_info or {}
    )
    if data is None:
        raise RuntimeError("无法建立数据库连接，请检查连接参数")

    ret = data.checkdb('builtin')
    if not ret:
        raise RuntimeError("巡检执行失败（checkdb 返回空）")

    ret.update({"co_name": [{'CO_NAME': db_info['label']}]})
    ret.update({"port": [{'PORT': db_info['port']}]})
    ret.update({"ip": [{'IP': db_info['host']}]})

    # DM8 的 saveDoc 调用方式（与 MySQL/PG 不同）
    savedoc = mod.saveDoc(
        context=ret,
        ofile=ofile,
        inspector_name=inspector_name,
        label=db_info['label']
    )
    success = savedoc.contextsave() if hasattr(savedoc, 'contextsave') else savedoc.save()

    if not success:
        raise RuntimeError("Word 报告渲染失败")

    return ofile, file_name


def run_sqlserver(db_info, inspector_name, ssh_info=None):
    """执行 SQL Server 巡检"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("main_sqlserver", os.path.join(SCRIPT_DIR, "main_sqlserver.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    data = mod.DBCheckSQLServer(
        host=db_info['host'],
        port=db_info['port'],
        user=db_info['user'],
        password=db_info['password'],
        database=db_info.get('database'),
        label=db_info['label'],
        inspector=inspector_name,
        ssh_host=ssh_info.get('ssh_host') if ssh_info else None,
        ssh_user=ssh_info.get('ssh_user') if ssh_info else None,
        ssh_password=ssh_info.get('ssh_password') if ssh_info else None,
        ssh_key_file=ssh_info.get('ssh_key_file') if ssh_info else None
    )

    if not data.checkdb():
        raise RuntimeError("巡检执行失败")

    # SQL Server 的 _save_report 会自动生成报告
    if not data.report_path or not os.path.exists(data.report_path):
        raise RuntimeError("报告生成失败")

    return data.report_path, os.path.basename(data.report_path)


def run_tidb(db_info, inspector_name, ssh_info=None):
    """执行 TiDB 巡检"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("main_tidb", os.path.join(SCRIPT_DIR, "main_tidb.py"))
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
    file_name = f"TiDB巡检报告_{db_info['label']}_{timestamp}.docx"
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


def run_config_baseline(db_info, db_type, output_format='txt'):
    """
    执行配置基线检查。
    
    参数:
        db_info: 数据库连接信息字典
        db_type: 数据库类型 ('mysql', 'pg')
        output_format: 输出格式 ('txt', 'pdf')
    
    返回:
        (文件路径, 文件名)
    """
    import importlib.util
    
    reports_dir = os.path.join(SCRIPT_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    if db_type == 'mysql':
        import pymysql
        conn = pymysql.connect(
            host=db_info['host'],
            port=db_info['port'],
            user=db_info['user'],
            password=db_info['password'],
            charset='utf8mb4'
        )
        db_label = 'MySQL'
    elif db_type == 'pg':
        import psycopg2
        conn = psycopg2.connect(
            host=db_info['host'],
            port=db_info['port'],
            user=db_info['user'],
            password=db_info['password'],
            database=db_info.get('database', 'postgres')
        )
        db_label = 'PostgreSQL'
    else:
        raise ValueError(f"不支持的数据库类型: {db_type}")
    
    # 导入配置基线模块
    sys.path.insert(0, SCRIPT_DIR)
    from config_baseline import get_config_baseline, format_config_baseline_report, generate_config_baseline_pdf_report
    
    # 执行配置基线检查
    report = get_config_baseline(db_type, conn)
    conn.close()
    
    if output_format == 'pdf':
        file_name = f"{db_label}配置基线报告_{db_info['label']}_{timestamp}.pdf"
        ofile = os.path.join(reports_dir, file_name)
        success, result = generate_config_baseline_pdf_report(report, ofile, db_type)
        if not success:
            raise RuntimeError(result)
    else:
        # 文本格式输出到控制台并保存
        report_text = format_config_baseline_report(report, db_type)
        file_name = f"{db_label}配置基线报告_{db_info['label']}_{timestamp}.txt"
        ofile = os.path.join(reports_dir, file_name)
        with open(ofile, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(report_text)
    
    return ofile, file_name


def run_index_health(db_info, db_type, output_format='txt'):
    """
    执行索引健康分析。
    
    参数:
        db_info: 数据库连接信息字典
        db_type: 数据库类型 ('mysql', 'pg')
        output_format: 输出格式 ('txt', 'pdf')
    
    返回:
        (文件路径, 文件名)
    """
    import importlib.util
    
    reports_dir = os.path.join(SCRIPT_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    if db_type == 'mysql':
        import pymysql
        conn = pymysql.connect(
            host=db_info['host'],
            port=db_info['port'],
            user=db_info['user'],
            password=db_info['password'],
            charset='utf8mb4'
        )
        db_label = 'MySQL'
    elif db_type == 'pg':
        import psycopg2
        conn = psycopg2.connect(
            host=db_info['host'],
            port=db_info['port'],
            user=db_info['user'],
            password=db_info['password'],
            database=db_info.get('database', 'postgres')
        )
        db_label = 'PostgreSQL'
    else:
        raise ValueError(f"不支持的数据库类型: {db_type}")
    
    # 导入索引健康分析模块
    sys.path.insert(0, SCRIPT_DIR)
    from index_health import get_index_health, format_index_health_report, generate_index_health_pdf_report
    
    # 执行索引健康分析
    report = get_index_health(db_type, conn)
    conn.close()
    
    if output_format == 'pdf':
        file_name = f"{db_label}索引健康分析_{db_info['label']}_{timestamp}.pdf"
        ofile = os.path.join(reports_dir, file_name)
        success, result = generate_index_health_pdf_report(report, ofile, db_type)
        if not success:
            raise RuntimeError(result)
    else:
        # 文本格式输出到控制台并保存
        report_text = format_index_health_report(report, db_type)
        file_name = f"{db_label}索引健康分析_{db_info['label']}_{timestamp}.txt"
        ofile = os.path.join(reports_dir, file_name)
        with open(ofile, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(report_text)
    
    return ofile, file_name


def convert_to_pdf(docx_path):
    """
    将 DOCX 文件转换为 PDF。
    
    参数:
        docx_path: DOCX 文件路径
    
    返回:
        (成功标志, PDF文件路径或错误信息)
    """
    sys.path.insert(0, SCRIPT_DIR)
    from pdf_export import convert_docx_to_pdf
    
    success, result = convert_docx_to_pdf(docx_path)
    if success:
        return result
    else:
        raise RuntimeError(result)


def main():
    parser = argparse.ArgumentParser(
        description="DBCheck 数据库巡检工具（无交互版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
支持三种模式：
1. 完整巡检（默认）：生成完整 Word 巡检报告
2. 配置基线检查（--check-config）：检查配置参数与推荐值的差距
3. 索引健康分析（--check-indexes）：分析缺失/冗余/未使用索引

示例（完整巡检）:
  python run_inspection.py --type mysql --host localhost --port 3306 \\
      --user root --password mypass --label "生产MySQL" --inspector "张三"

示例（配置基线检查）:
  python run_inspection.py --type mysql --host localhost --port 3306 \\
      --user root --password mypass --label "生产MySQL" --check-config

示例（索引健康分析）:
  python run_inspection.py --type mysql --host localhost --port 3306 \\
      --user root --password mypass --label "生产MySQL" --check-indexes

示例（PDF 导出）:
  python run_inspection.py --to-pdf /path/to/report.docx
"""
    )
    
    # 模式选择
    parser.add_argument('--to-pdf', metavar='DOCX_PATH',
                        help='将 DOCX 报告转换为 PDF')
    parser.add_argument('--check-config', action='store_true',
                        help='执行配置基线与合规检查（支持 MySQL/PostgreSQL）')
    parser.add_argument('--check-indexes', action='store_true',
                        help='执行索引健康分析（支持 MySQL/PostgreSQL）')
    parser.add_argument('--output-format', default='txt', choices=['txt', 'pdf'],
                        help='配置基线/索引分析输出格式（默认 txt）')
    
    # 数据库连接参数（完整巡检模式需要）
    parser.add_argument('--type', required=False, choices=['mysql', 'pg', 'oracle', 'sqlserver', 'dm', 'tidb'],
                        help='数据库类型: mysql / pg / oracle / sqlserver / dm / tidb（完整巡检必需）')
    parser.add_argument('--host', help='数据库主机 IP 或域名')
    parser.add_argument('--port', type=int, default=None,
                        help='数据库端口（默认: MySQL/TiDB 3306/4000, PG 5432, Oracle 1521, SQL Server 1433, DM8 5236）')
    parser.add_argument('--user', help='数据库用户名')
    parser.add_argument('--password', help='数据库密码')
    parser.add_argument('--database', default=None,
                        help='数据库名（PG/SQL Server 专有，默认 postgres/master）')
    parser.add_argument('--label', help='数据库标签（用于报告命名，如"生产库-MySQL"）')
    parser.add_argument('--inspector', help='巡检人员姓名')
    parser.add_argument('--ssh-host', default=None, help='SSH 主机 IP（可选）')
    parser.add_argument('--ssh-port', type=int, default=22, help='SSH 端口（默认 22）')
    parser.add_argument('--ssh-user', default=None, help='SSH 用户名（可选）')
    parser.add_argument('--ssh-password', default=None, help='SSH 密码（可选）')
    parser.add_argument('--ssh-key', default=None,
                        help='SSH 私钥文件路径（可选，与密码二选一）')

    args = parser.parse_args()
    
    # ── PDF 转换模式 ────────────────────────────────────────
    if args.to_pdf:
        print(f"\n[PDF 转换] 源文件: {args.to_pdf}")
        print("-" * 50)
        try:
            pdf_path = convert_to_pdf(args.to_pdf)
            print("-" * 50)
            print(f"✅ PDF 转换成功！")
            print(f"📄 PDF 路径: {pdf_path}")
        except Exception as e:
            print("-" * 50)
            print(f"❌ PDF 转换失败: {e}")
            print(traceback.format_exc())
            sys.exit(1)
        return
    
    # ── 配置基线 / 索引分析模式 ────────────────────────────
    if args.check_config or args.check_indexes:
        # 验证必需参数
        if not args.type or not args.host or not args.user or not args.password:
            print("错误: 配置基线/索引分析模式需要 --type, --host, --user, --password 参数")
            sys.exit(1)
        
        if args.type not in ('mysql', 'pg'):
            print(f"错误: {args.type} 暂不支持配置基线/索引分析（仅支持 MySQL/PostgreSQL）")
            sys.exit(1)
        
        if args.port is None:
            defaults = {'mysql': 3306, 'pg': 5432, 'tidb': 4000}
            args.port = defaults.get(args.type, 3306)
        
        db_info = {
            'label':    args.label or args.host,
            'host':     args.host,
            'port':     args.port,
            'user':     args.user,
            'password': args.password,
        }
        if args.database:
            db_info['database'] = args.database
        
        type_labels = {'mysql': 'MySQL', 'pg': 'PostgreSQL'}
        mode_labels = {'check_config': '配置基线检查', 'check_indexes': '索引健康分析'}
        
        if args.check_config:
            mode = '配置基线检查'
        else:
            mode = '索引健康分析'
        
        print(f"\n[{type_labels.get(args.type)}] {mode}: {db_info['label']} ({args.host}:{args.port})")
        print("-" * 50)
        
        try:
            if args.check_config:
                ofile, fname = run_config_baseline(db_info, args.type, args.output_format)
            else:
                ofile, fname = run_index_health(db_info, args.type, args.output_format)
            
            print("-" * 50)
            print(f"✅ {mode}完成！")
            print(f"📄 报告路径: {ofile}")
        except Exception as e:
            print("-" * 50)
            print(f"❌ {mode}失败: {e}")
            print(traceback.format_exc())
            sys.exit(1)
        return
    
    # ── 完整巡检模式 ────────────────────────────────────────
    # 验证必需参数
    required_missing = []
    for param in ['type', 'host', 'user', 'password', 'label', 'inspector']:
        if not getattr(args, param):
            required_missing.append(f'--{param}')
    
    if required_missing:
        print(f"错误: 完整巡检模式缺少必需参数: {', '.join(required_missing)}")
        print("或使用 --check-config / --check-indexes 进入单项检查模式")
        sys.exit(1)
    
    if args.port is None:
        defaults = {'mysql': 3306, 'pg': 5432, 'oracle': 1521, 'sqlserver': 1433, 'dm': 5236, 'tidb': 4000}
        args.port = defaults.get(args.type, 3306)

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

    type_labels = {'mysql': 'MySQL', 'pg': 'PostgreSQL', 'oracle': 'Oracle', 'sqlserver': 'SQL Server', 'dm': 'DM8', 'tidb': 'TiDB'}
    print(f"\n[{type_labels.get(args.type, args.type)}] 开始巡检: {args.label} ({args.host}:{args.port})")
    print("-" * 50)

    try:
        if args.type == 'mysql':
            ofile, fname = run_mysql(db_info, args.inspector, ssh_info)
        elif args.type == 'pg':
            ofile, fname = run_pg(db_info, args.inspector, ssh_info)
        elif args.type == 'oracle':
            ofile, fname = run_oracle_full(db_info, args.inspector, ssh_info)
        elif args.type == 'sqlserver':
            ofile, fname = run_sqlserver(db_info, args.inspector, ssh_info)
        elif args.type == 'dm':
            ofile, fname = run_dm(db_info, args.inspector, ssh_info)
        elif args.type == 'tidb':
            ofile, fname = run_tidb(db_info, args.inspector, ssh_info)

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
