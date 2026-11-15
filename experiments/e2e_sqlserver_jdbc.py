#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
MSSQL-JDBC 端到端 demo 脚本 —— 模拟 web_ui.py → MssqlJdbcInspector → docx 报告整链路。

设计依据：deliverables/software-company/dbcheck-mssql-jdbc-architecture-2026-08-05.md §7 T-016

调用链（与 web_ui.py 完全一致）：
  1. main_sqlserver_dual.getData(host, port, user, password, ssh_info, ...)
       → SQLServerDualInspector(connection_mode='auto')
       → MssqlJdbcInspector.connect()
  2. inspector.collect_data()
       → 15 个 _collect_*  →  _build_rule_scalars  →  章节加载  →  慢查询  →  智能分析
  3. inspector.generate_report(output_file, inspector_name)

支持两种运行模式：
  A. 真实模式（需真实 SQL Server）：命令行传 host/port/user/pwd
       python experiments/e2e_sqlserver_jdbc.py 127.0.0.1 1433 sa P@ssw0rd
  B. Mock 模式（无 SQL Server，用 mock 数据演示至少 1 个章节报告生成）：
       python experiments/e2e_sqlserver_jdbc.py
       python experiments/e2e_sqlserver_jdbc.py --mock

返回码：
  0 PASS（真实跑通或 mock 报告生成成功）
  1 参数错误
  2 真实连接失败
  3 报告生成失败
"""

import argparse
import os
import sys
import time
import traceback
from typing import Any, Dict

# 确保项目根在 sys.path（独立运行也能 import）
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _print_banner(title: str, width: int = 70):
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def _print_step(step_num: int, label: str, ok: bool = True, detail: str = ""):
    status = "✓ OK " if ok else "✗ FAIL"
    print(f"\n[{step_num}/5] {status}  {label}")
    if detail:
        for line in detail.splitlines():
            print(f"        {line}")


def _build_mock_context(db_type: str = "sqlserver_jdbc") -> Dict[str, Any]:
    """构造一个最小的 mock context（用于无 SQL Server 时的报告生成演示）。

    至少包含 1 章节的 mssql_* 字段 + _chapters 列表 + system_info，使 generate_report
    能渲染 1 章节的报告。
    """
    print("\n[Mock] 构造 mock context（演示用，无真实 SQL Server）")
    return {
        "db_type": db_type,
        "version": [{"VERSION": "Microsoft SQL Server 2022 (RTM) - 16.0.1000.6 (X64)"}],
        "mssql_version": [
            {"VERSION": "Microsoft SQL Server 2022 (RTM) - 16.0.1000.6 (X64)",
             "VERSION_STR": "Microsoft SQL Server 2022 (RTM)"},
        ],
        "mssql_instance": [
            {
                "server_name": "MOCK-MSSQL",
                "service_name": "MSSQLSERVER",
                "is_clustered": 0,
                "machine_name": "mock-host",
                "edition": "Enterprise Edition",
                "product_version": "16.0.1000.6",
                "product_level": "RTM",
            }
        ],
        "mssql_databases": [
            {"database_id": 1, "name": "master", "state_desc": "ONLINE", "recovery_model_desc": "SIMPLE"},
            {"database_id": 2, "name": "tempdb", "state_desc": "ONLINE", "recovery_model_desc": "SIMPLE"},
            {"database_id": 3, "name": "msdb", "state_desc": "ONLINE", "recovery_model_desc": "SIMPLE"},
            {"database_id": 4, "name": "AppDB", "state_desc": "ONLINE", "recovery_model_desc": "FULL"},
            {"database_id": 5, "name": "DemoDB", "state_desc": "ONLINE", "recovery_model_desc": "FULL"},
        ],
        "mssql_db_files": [
            {"DATABASE_ID": 1, "SIZE_KB": 10240, "TYPE_DESC": "ROWS", "LOGICAL_NAME": "master"},
            {"DATABASE_ID": 2, "SIZE_KB": 20480, "TYPE_DESC": "ROWS", "LOGICAL_NAME": "tempdev"},
            {"DATABASE_ID": 2, "SIZE_KB": 5120,  "TYPE_DESC": "LOG",  "LOGICAL_NAME": "templog"},
            {"DATABASE_ID": 4, "SIZE_KB": 1048576, "TYPE_DESC": "ROWS", "LOGICAL_NAME": "AppDB"},
            {"DATABASE_ID": 4, "SIZE_KB": 524288,  "TYPE_DESC": "LOG",  "LOGICAL_NAME": "AppDB_log"},
        ],
        "mssql_configurations": [
            {"NAME": "max server memory (MB)", "VALUE_IN_USE": "8192"},
            {"NAME": "max worker threads", "VALUE_IN_USE": "512"},
            {"NAME": "max degree of parallelism", "VALUE_IN_USE": "4"},
        ],
        "mssql_locks": [
            {"REQUEST_STATUS": "GRANT", "request_session_id": 51},
        ],
        "mssql_sessions": [
            {"SESSION_ID": 51, "LOGIN_NAME": "sa", "HOST_NAME": "mock-host", "STATUS": "sleeping", "CPU_TIME": 123},
        ],
        "mssql_index_usage": [
            {"TABLE_NAME": "Orders", "INDEX_NAME": "PK_Orders", "USER_SEEKS": 100, "USER_SCANS": 0,
             "USER_LOOKUPS": 50, "USER_UPDATES": 200},
            {"TABLE_NAME": "Orders", "INDEX_NAME": "IX_OrderDate", "USER_SEEKS": 0, "USER_SCANS": 0,
             "USER_LOOKUPS": 0, "USER_UPDATES": 30},
        ],
        "mssql_index_physical": [
            {"TABLE_NAME": "Orders", "INDEX_NAME": "PK_Orders", "AVG_FRAGMENTATION_IN_PERCENT": 12.5},
            {"TABLE_NAME": "Orders", "INDEX_NAME": "IX_OrderDate", "AVG_FRAGMENTATION_IN_PERCENT": 45.0},
        ],
        "mssql_top_sql": [
            {"execution_count": 100, "total_worker_time": 1234567, "total_elapsed_time": 2345678,
             "query_text": "SELECT * FROM Orders WHERE OrderDate > '2025-01-01'"},
        ],
        "mssql_wait_stats": [
            {"WAIT_TYPE": "PAGEIOLATCH_SH", "wait_time_ms": 123456, "wait_tasks_count": 5000},
            {"WAIT_TYPE": "LCK_M_S", "wait_time_ms": 1234, "wait_tasks_count": 10},
        ],
        "mssql_backup_history": [
            {"DATABASE_NAME": "AppDB", "BACKUP_START_DATE": "2026-08-04 22:00:00",
             "BACKUP_SIZE_MB": 512.0, "RECOVERY_MODEL": "FULL"},
        ],
        "mssql_always_on": [],  # mock 无 AG
        "mssql_dbmemory": [
            {"TYPE": "MEMORYCLERK_SQLBUFFERPOOL", "NAME": "Buffer Pool", "PAGES_KB": 524288},
        ],
        # 规则标量（_build_rule_scalars 派生结果）
        "mssql_version_str": "Microsoft SQL Server 2022 (RTM)",
        "mssql_db_count": 5,
        "mssql_max_conn_pct": 0.2,
        "max_worker_count": 512,
        "active_connections": 1,
        "mssql_longest_running_query_ms": 123,
        "mssql_top_wait_type": "PAGEIOLATCH_SH",
        "mssql_db_total_gb": 1.62,
        "mssql_tempdb_used_gb": 0.02,
        "mssql_backup_24h": 1,
        "mssql_stale_stats_count": 1,
        "mssql_missing_index_count": 1,
        "mssql_blocked_session_count": 0,
        "mssql_ag_health": "NOT_CONFIGURED",
        # 系统信息（generate_report 需要）
        "system_info": {
            "platform": "Windows (Mock)",
            "boot_time": "2026-07-01 08:00:00",
            "cpu": {"brand": "Mock CPU", "cores": 8, "usage_percent": 25.0},
            "memory": {"total_gb": 32.0, "used_gb": 16.0, "usage_percent": 50.0},
            "disk_list": [
                {"device": "C:", "mountpoint": "C:\\", "fstype": "NTFS",
                 "total_gb": 500.0, "used_gb": 250.0, "free_gb": 250.0, "usage_percent": 50.0},
            ],
        },
        # 章节（从 inspection.db 加载的报告源）
        "_chapters": [
            {
                "chapter_number": 1,
                "chapter_title_zh": "实例概况（Mock）",
                "chapter_title_en": "Instance Overview (Mock)",
                "description": "SQL Server 版本 / 实例 / 物理机 / 集群状态（mock 数据）",
                "queries": [
                    {
                        "query_key": "mssql_version",
                        "query_sql": "SELECT @@VERSION",
                        "query_description_zh": "SQL Server 版本信息（mock）",
                        "query_description_en": "SQL Server version (mock)",
                    },
                ],
            },
        ],
        # 智能分析（mock 1 条规则）
        "auto_analyze": [
            {
                "level": "MEDIUM",
                "title": "索引碎片率偏高（mock）",
                "description": "IX_OrderDate 索引碎片率 45.0%，建议 REBUILD。",
                "suggestion": "ALTER INDEX IX_OrderDate ON dbo.Orders REBUILD;",
                "fix_sql": "ALTER INDEX IX_OrderDate ON dbo.Orders REBUILD;",
                "category": "index_health",
            }
        ],
        "ai_advice": "（mock）建议定期维护索引 + 启用备份压缩。",
        "baseline_results": [],
        "slow_query_result": None,
        "index_health_result": None,
        "ip": [{"IP": "127.0.0.1"}],
        "port": [{"PORT": 1433}],
        "co_name": [{"CO_NAME": "master"}],
    }


def _run_mock_mode(report_path: str) -> int:
    """Mock 模式：构造 mock context，生成报告（演示 1 章节）。"""
    _print_banner("MSSQL-JDBC E2E DEMO  ── Mock 模式")
    print(f"模式: MOCK（无真实 SQL Server，演示 1 章节报告生成）")
    print(f"报告输出: {report_path}")

    # 1. 构造 mock context
    mock_ctx = _build_mock_context()

    # 2. 模拟 web_ui 调用链：main_sqlserver_dual.getData 返回的 CompatWrapper.checkdb
    #    → collect_data → 拿到 context；这里直接用 mock context
    _print_step(1, "模拟 web_ui 调用链 (mock context)", ok=True,
                detail="getData → MssqlJdbcInspector → CompatWrapper.checkdb")

    # 3. 验证 context 至少包含 1 章节
    chapters = mock_ctx.get("_chapters", [])
    if not chapters:
        print("\n[FAIL] mock context 必须包含至少 1 章节")
        return 1
    _print_step(2, "章节加载（mock 1 章节）", ok=True,
                detail=f"chapters={len(chapters)}, chapter_1='{chapters[0]['chapter_title_zh']}'")

    # 4. 调用 generate_report 生成 docx
    _print_step(3, "生成 docx 报告（generate_report）")
    try:
        from modules.inspection.engine import BaseInspectionEngine

        # 构造一个最小可用的 inspector 实例（只调用父类方法，绕过 JDBC）
        insp = BaseInspectionEngine.__new__(BaseInspectionEngine)
        insp.host = "127.0.0.1"
        insp.port = 1433
        insp.user = "sa"
        insp.password = ""
        insp.db_type = "sqlserver_jdbc"
        # 国际化 / 报告渲染所需属性
        insp._lang = "zh"
        insp._t = lambda x, **kw: x  # 键名直返
        insp.output_file = None
        insp.context = mock_ctx

        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        t0 = time.time()
        result_path = insp.generate_report(report_path, "MockInspector")
        elapsed = time.time() - t0

        if not os.path.isfile(result_path):
            print(f"        FAIL: 报告文件未生成: {result_path}")
            return 3
        size_kb = os.path.getsize(result_path) / 1024
        print(f"        OK   report={result_path}")
        print(f"        size = {size_kb:.1f} KB   elapsed = {elapsed:.2f}s")
    except Exception as e:
        print(f"        FAIL: {e}")
        traceback.print_exc()
        return 3

    # 5. 校验 context 关键字段
    _print_step(4, "校验 mock context 关键字段", ok=True,
                detail=f"mssql_version_str={mock_ctx.get('mssql_version_str')}, "
                       f"mssql_db_count={mock_ctx.get('mssql_db_count')}, "
                       f"mssql_top_wait_type={mock_ctx.get('mssql_top_wait_type')}")
    _print_step(5, "E2E DEMO PASS（mock 模式）", ok=True,
                detail=f"报告路径: {result_path}\n"
                       f"包含 1 章节（实例概况）\n"
                       f"包含 1 条 mock 智能分析（索引碎片）")

    print("\n" + "=" * 70)
    print("MSSQL-JDBC E2E DEMO PASS（mock 模式）")
    print("=" * 70)
    return 0


def _run_real_mode(host: str, port: int, user: str, password: str,
                   database: str, report_path: str) -> int:
    """真实模式：调用 main_sqlserver_dual.getData → collect_data → generate_report。"""
    _print_banner("MSSQL-JDBC E2E DEMO  ── 真实模式")
    print(f"host={host}  port={port}  user={user}  database={database}")
    print(f"connection_mode=auto  report={report_path}")

    # 1. 调用 web_ui 调用链：main_sqlserver_dual.getData
    _print_step(1, "web_ui 调用链 → main_sqlserver_dual.getData")
    try:
        from modules.entrypoints import main_sqlserver_dual
        wrapper = main_sqlserver_dual.getData(
            ip=host, port=port, user=user, password=password,
            ssh_info={"database": database, "connection_mode": "auto"},
            connection_mode="auto",
        )
        if wrapper is None:
            print("        FAIL: getData 返回 None（连接失败）")
            return 2
        print(f"        OK   resolved_mode={getattr(wrapper.inspector, '_resolved_mode', 'unknown')}, "
              f"db_type={getattr(wrapper.inspector, 'db_type', 'unknown')}")
    except Exception as e:
        print(f"        FAIL: {e}")
        traceback.print_exc()
        return 2

    # 2. collect_data
    _print_step(2, "MssqlJdbcInspector.collect_data()")
    try:
        t0 = time.time()
        context = wrapper.checkdb("builtin")
        elapsed = time.time() - t0
        if not context:
            print("        FAIL: collect_data 返回空")
            return 2
        keys = [k for k in context.keys() if k.startswith("mssql_")]
        print(f"        OK   elapsed={elapsed:.2f}s, mssql_* keys={len(keys)}")
        print(f"        keys: {', '.join(keys[:8])}{' ...' if len(keys) > 8 else ''}")
    except Exception as e:
        print(f"        FAIL: {e}")
        traceback.print_exc()
        return 2

    # 3. 章节加载
    _print_step(3, "章节加载（_chapters）", ok=True,
                detail=f"chapters={len(context.get('_chapters', []))}")

    # 4. generate_report
    _print_step(4, "生成 docx 报告（generate_report）")
    try:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        t0 = time.time()
        result_path = wrapper.generate_report(report_path, "E2EDemo")
        elapsed = time.time() - t0
        if not os.path.isfile(result_path):
            print(f"        FAIL: 报告文件未生成: {result_path}")
            return 3
        size_kb = os.path.getsize(result_path) / 1024
        print(f"        OK   report={result_path}")
        print(f"        size = {size_kb:.1f} KB   elapsed = {elapsed:.2f}s")
    except Exception as e:
        print(f"        FAIL: {e}")
        traceback.print_exc()
        return 3
    finally:
        try:
            wrapper.inspector.disconnect()
        except Exception:
            pass

    # 5. PASS
    _print_step(5, "E2E DEMO PASS（真实模式）", ok=True,
                detail=f"报告路径: {result_path}")
    print("\n" + "=" * 70)
    print("MSSQL-JDBC E2E DEMO PASS（真实模式）")
    print("=" * 70)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="MSSQL-JDBC 端到端 demo 脚本（支持真实模式 + Mock 模式）"
    )
    parser.add_argument("host", nargs="?", default="", help="SQL Server 地址（真实模式）")
    parser.add_argument("port", nargs="?", type=int, default=1433, help="端口（默认 1433）")
    parser.add_argument("user", nargs="?", default="sa", help="用户名（默认 sa）")
    parser.add_argument("password", nargs="?", default="", help="密码（真实模式必填）")
    parser.add_argument("database", nargs="?", default="master", help="数据库（默认 master）")
    parser.add_argument("--mock", action="store_true", help="强制 mock 模式")
    parser.add_argument(
        "--report",
        default=os.path.join(_PROJECT_ROOT, "reports", "SQLServer_JDBC_E2E_Report.docx"),
        help="报告输出路径（默认 reports/SQLServer_JDBC_E2E_Report.docx）",
    )
    args = parser.parse_args()

    # 判断运行模式
    if args.mock or not (args.host and args.password):
        # Mock 模式
        return _run_mock_mode(args.report)

    # 真实模式
    return _run_real_mode(
        host=args.host, port=args.port, user=args.user, password=args.password,
        database=args.database, report_path=args.report,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]")
        sys.exit(130)
