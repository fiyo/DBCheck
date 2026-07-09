# -*- coding: utf-8 -*-
"""协同诊断中枢 · 实时巡检引擎调度。

让「深度巡检分析专员」直接调用 DBCheck 巡检引擎，为目标数据源
实时产出一份巡检报告（getData → checkdb → 智能分析），再交给专员解析，
从而保证诊断结论与所选数据源的真实状态严格相关。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

# 确保项目根目录在 sys.path（与 web_ui 运行环境一致）
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _db_default(db_type: str) -> str:
    return {
        "mysql": "mysql",
        "pg": "postgres",
        "oracle": "orcl",
        "oracle_jdbc": "orcl",
        "dm": "DAMENG",
        "sqlserver": "master",
        "tidb": "mysql",
        "ivorysql": "ivorysql",
        "kingbase": "kingbase",
        "yashandb": "YASHANDB",
        "gbase": "testdb",
    }.get(db_type, "")


def _build_db_info(db_type: str, instance: Dict[str, Any]) -> Dict[str, Any]:
    """把实例管理器的解密信息映射成巡检引擎需要的 db_info。"""
    label = instance.get("name") or instance.get("host") or "unknown"
    return {
        "host": instance.get("host", ""),
        "port": int(instance.get("port", 0) or 0),
        "user": instance.get("user", ""),
        "password": instance.get("password", ""),
        "database": instance.get("database") or _db_default(db_type),
        "service_name": instance.get("service_name"),
        "sid": instance.get("sid"),
        "sysdba": bool(instance.get("sysdba", False)),
        "name": instance.get("name", ""),
        "label": label,
    }


def _score(context: Dict[str, Any]):
    """依据巡检上下文计算健康评分 / 风险等级（与 web_ui 流程一致）。"""
    risk_count = context.get("risk_count", 0)
    if not risk_count:
        issues = context.get("issues", [])
        risk_count = len(issues) if isinstance(issues, list) else 0
    health_status = context.get("health_status", "")
    if "优秀" in health_status or "Excellent" in health_status:
        health_score = 100
    elif "良好" in health_status or "Good" in health_status:
        health_score = 80
    elif "一般" in health_status or "Fair" in health_status:
        health_score = 60
    elif "需关注" in health_status or "Attention" in health_status:
        health_score = 40
    else:
        health_score = 100 - min(risk_count * 5, 50)
    if health_score >= 85:
        risk_level = "healthy"
    elif health_score >= 70:
        risk_level = "low"
    elif health_score >= 50:
        risk_level = "medium"
    elif health_score >= 30:
        risk_level = "high"
    else:
        risk_level = "critical"
    return health_score, risk_count, risk_level, health_status


def run_target_inspection(
    db_type: str,
    instance: Dict[str, Any],
    inspector_name: str = "Jack",
    template_id=None,
) -> Dict[str, Any]:
    """为目标数据源实时运行巡检引擎，返回结构化结果。

    返回字典包含:
        ok            是否成功
        auto_analyze  智能分析发现列表（每项含 col1/col2/col3 结构）
        report_file / report_name  生成的报告路径
        health_score / risk_count / risk_level / health_status  健康评估
        ai_advice     AI 诊断建议
        error         失败时的错误信息
    """
    if _SCRIPT_DIR not in sys.path:
        sys.path.insert(0, _SCRIPT_DIR)

    import run_inspection as ri

    _RUNNER_MAP = {
        "mysql": ri.run_mysql,
        "pg": ri.run_pg,
        "oracle": ri.run_oracle_full,
        "oracle_jdbc": ri.run_oracle_full,
        "dm": ri.run_dm,
        "sqlserver": ri.run_sqlserver,
        "tidb": ri.run_tidb,
        "ivorysql": ri.run_ivorysql,
        "kingbase": ri.run_kingbase,
        "yashandb": ri.run_yashandb,
        "gbase": ri.run_gbase,
    }
    _ENGINE_DB_TYPE = {
        "oracle_jdbc": "oracle",
    }
    _ANALYZER_MAP = {
        "mysql": "smart_analyze_mysql",
        "pg": "smart_analyze_pg",
        "oracle": "smart_analyze_oracle",
        "dm": "smart_analyze_dm",
        "sqlserver": "smart_analyze_sqlserver",
        "tidb": "smart_analyze_tidb",
        "ivorysql": "smart_analyze_ivorysql",
        "kingbase": "smart_analyze_kingbase",
        "yashandb": "smart_analyze_yashandb",
        "gbase": "smart_analyze_gbase",
    }

    runner = _RUNNER_MAP.get(db_type)
    if runner is None:
        return {"ok": False, "error": f"暂不支持的数据库类型：{db_type}", "auto_analyze": []}

    db_info = _build_db_info(db_type, instance)
    engine_db_type = _ENGINE_DB_TYPE.get(db_type, db_type)

    try:
        result = runner(db_info, inspector_name, None)
        # result 形如 (report_file, report_name, context)
        if not isinstance(result, tuple) or len(result) < 3:
            return {"ok": False, "error": "巡检引擎未返回上下文", "auto_analyze": []}
        ofile, fname, context = result[0], result[1], result[2]
        if not context:
            return {"ok": False, "error": "巡检引擎返回空上下文", "auto_analyze": []}

        # 智能分析（与 web_ui 巡检流程一致）
        auto_analyze: List[Dict[str, Any]] = []
        analyzer_name = _ANALYZER_MAP.get(engine_db_type)
        try:
            import analyzer

            fn = getattr(analyzer, analyzer_name, None)
            if fn:
                auto_analyze = list(fn(context) or [])
        except Exception:
            auto_analyze = []

        # 插件附加风险（尽力而为，失败不影响主流程）
        try:
            from plugin_core import run_plugin_inspections_for_db

            plugin_issues = run_plugin_inspections_for_db(engine_db_type, context)
            if plugin_issues:
                auto_analyze = auto_analyze + list(plugin_issues)
        except Exception:
            pass

        health_score, risk_count, risk_level, health_status = _score(context)
        return {
            "ok": True,
            "db_type": db_type,
            "instance_name": db_info.get("label", ""),
            "auto_analyze": auto_analyze,
            "report_file": ofile,
            "report_name": fname,
            "health_score": health_score,
            "risk_count": risk_count,
            "risk_level": risk_level,
            "health_status": health_status,
            "ai_advice": context.get("ai_advice", ""),
            "error": None,
        }
    except Exception as e:  # 实时巡检失败，交由上层回退历史报告
        return {
            "ok": False,
            "error": str(e),
            "auto_analyze": [],
            "db_type": db_type,
            "instance_name": db_info.get("label", ""),
        }
