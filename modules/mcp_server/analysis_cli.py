# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""MCP 分析工具隔离子进程（与 intel_inspection_cli 同构）。

被 MCP 工具（slow_queries / index_health / baseline_check）在独立子进程中调用，
避免在主进程内直接跑驱动/分析器（JDBC/插件类型可能拉起 JVM 钉死 hub）。

用法:
    python analysis_cli.py <analysis_type> <instance_id> [db_type]

    analysis_type: slow_query | index_health | baseline
    instance_id  : 数据源 ID
    db_type      : 可选，覆盖 instance 的 db_type（adapter 透传场景）

输出契约:  stdout 仅打印**一行** JSON（成功或失败统一结构），其余日志走 stderr。
            {"ok": True,  "analysis_type": "...", "result": {...}}
            {"ok": False, "error": "..."}
"""

import json
import os
import sys

# 防递归标记（语义占位，当前无子进程再派生子进程的链路）
os.environ.setdefault("DBCheck_MCP_ANALYSIS_SUBPROCESS", "1")


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _run_analysis(analysis_type: str, db_type: str, conn, lang: str = "zh",
                  days_threshold: int = 90) -> dict:
    if analysis_type == "slow_query":
        from modules.inspection.slow_query import get_slow_query_analyzer
        analyzer = get_slow_query_analyzer(db_type)
        if analyzer is None:
            return {"ok": False, "error": f"慢查询分析暂不支持该数据库类型: {db_type}"}
        result = analyzer.analyze(conn, ai_advisor=None, lang=lang)
        return {"ok": True, "result": result.to_dict()}

    if analysis_type == "index_health":
        from modules.inspection.index_health import (
            get_index_health, format_index_health_report,
        )
        report = get_index_health(db_type, conn, days_threshold=days_threshold)
        if report is None:
            return {"ok": False, "error": f"索引健康分析暂不支持该数据库类型: {db_type}"}
        text = format_index_health_report(report, db_type)
        return {"ok": True, "result": {"report": report, "text": text}}

    if analysis_type == "baseline":
        from modules.inspection.config_baseline import (
            get_config_baseline, format_config_baseline_report,
        )
        report = get_config_baseline(db_type, conn)
        if report is None:
            return {"ok": False, "error": f"配置基线检查暂不支持该数据库类型: {db_type}"}
        text = format_config_baseline_report(report, db_type)
        return {"ok": True, "result": {"report": report, "text": text}}

    if analysis_type == "lock":
        from modules.inspection.lock_health import get_lock_tree
        result = get_lock_tree(db_type, inst)
        return {"ok": result.get("ok", False),
                **({"result": result} if result.get("ok") else {"error": result.get("error")})}

    return {"ok": False, "error": f"未知分析类型: {analysis_type}"}


def main() -> int:
    if len(sys.argv) < 3:
        _emit({"ok": False, "error": "usage: analysis_cli.py <type> <instance_id> [db_type]"})
        return 2
    analysis_type = sys.argv[1]
    instance_id = sys.argv[2]
    db_type_override = sys.argv[3] if len(sys.argv) > 3 else None

    from modules.mcp_server.bootstrap import bootstrap
    root = bootstrap()
    sys.path.insert(0, root)

    try:
        from modules.pro import get_instance_manager
        from modules.intelligence.db_executor import connect_instance, close_instance

        im = get_instance_manager()
        inst = im.get_instance_decrypted(instance_id)
        if not inst:
            _emit({"ok": False, "error": f"instance not found: {instance_id}"})
            return 0

        db_type = db_type_override or inst.get("db_type")
        conn = connect_instance(inst)
        try:
            out = _run_analysis(analysis_type, db_type, conn)
        finally:
            close_instance(conn)
        _emit({"ok": out.get("ok", False), "analysis_type": analysis_type,
               **({"result": out["result"]} if "result" in out else {}),
               **({"error": out["error"]} if "error" in out else {})})
        return 0
    except Exception as e:  # 任何异常都转为结构化失败，绝不污染 stdout 协议流
        _emit({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 0


if __name__ == "__main__":
    main()
