# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""MCP 工具实现（阶段 1：6+ 带风险元数据的工具箱）。

每个 tool 只是现有 service 层的一层薄封装：
- list_instances   -> InstanceManager.get_all_instances(mask_password=True)
- run_inspection   -> run_target_inspection(db_type, instance, ...)
- slow_queries     -> subprocess(analysis_cli.py slow_query)
- lock_tree        -> subprocess(analysis_cli.py lock)
- index_health     -> subprocess(analysis_cli.py index_health)
- baseline_check   -> subprocess(analysis_cli.py baseline)
- ai_diagnose      -> DiagnosticHub.dispatch(goal, instance_id)

隔离契约（沿用阶段 0）
----------------------
* ``principal`` 为 None 表示"无身份"（本地 stdio 且未开鉴权），保持向后兼容的
  全量行为；一旦解析出身份就必须过滤，绝不返回越权数据。
* 所有实例级工具先过 ``assert_visible``，越权返回 ``RESOURCE_NOT_VISIBLE``
  （不泄露"资源是否存在"）。
* 每次调用都写 ``um_audit_log``（client=mcp），形成可追溯时间线。
* DB 连接类分析统一走 ``analysis_cli.py`` 子进程隔离（避免在主进程内直接拉起
  驱动/JVM），子进程单行 JSON 回传，其余日志走 stderr。
"""

import json
import os
import subprocess
import sys

_STATE: dict = {}


def _ensure() -> dict:
    """懒加载并缓存底层 service 单例（首次调用时引导 sys.path + 迁移）。"""
    if "IM" in _STATE:
        return _STATE
    from modules.mcp_server.bootstrap import bootstrap  # noqa: E402
    bootstrap()
    from modules.pro import get_instance_manager  # noqa: E402
    from modules.intelligence.inspection_runner import run_target_inspection  # noqa: E402
    _STATE["IM"] = get_instance_manager()
    _STATE["run"] = run_target_inspection
    return _STATE


def _mask(row: dict) -> dict:
    """剔除密码键：即便上游已脱敏也绝不外传。"""
    d = dict(row)
    d.pop("password", None)
    d.pop("ssh_password", None)
    return d


# ── 子进程调度（DB 连接类分析共用） ────────────────────────────────────────────
def _run_analysis_subprocess(analysis_type: str, instance_id, timeout: int = 600) -> dict:
    """在干净子进程中跑 analysis_cli.py，返回其单行 JSON 结果。"""
    py = sys.executable
    cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis_cli.py")
    env = dict(os.environ)
    env["DBCheck_MCP_ANALYSIS_SUBPROCESS"] = "1"
    try:
        proc = subprocess.run(
            [py, cli, analysis_type, str(instance_id), ""],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"分析超时（>{timeout}s）"}
    out_lines = (proc.stdout or "").strip().splitlines()
    for line in reversed(out_lines):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {"ok": False, "error": f"子进程无 JSON 输出；stderr={(proc.stderr or '')[-800:]}"}


def _assert_or_deny(principal, instance_id) -> dict | None:
    """实例级可见性断言；通过返回 None，越权返回统一的 NOT_VISIBLE 响应。"""
    if principal is None:
        return None
    from modules.access import assert_visible, AUDIT_CLIENT_MCP
    ok, err = assert_visible(principal, "instance", instance_id, client=AUDIT_CLIENT_MCP)
    if not ok:
        return {
            "ok": False,
            "error": err.get("error", "resource not visible"),
            "error_code": err.get("error_code", "RESOURCE_NOT_VISIBLE"),
        }
    return None


def _audit(principal, action: str, instance_id, result: str, detail: str = "") -> None:
    if principal is None:
        return
    from modules.access import audit, AUDIT_CLIENT_MCP
    audit(principal, action, "instance", resource_id=str(instance_id),
          detail=detail, result=result, client=AUDIT_CLIENT_MCP)


# ── 工具实现 ───────────────────────────────────────────────────────────────────
def list_instances_tool(mask_password: bool = True, principal=None) -> dict:
    st = _ensure()
    rows = st["IM"].get_all_instances(mask_password=mask_password)
    if principal is not None:
        from modules.access import filter_visible
        rows = filter_visible(principal, rows, "instance")
    clean = [_mask(r) for r in rows]
    if principal is not None:
        from modules.access import audit, AUDIT_CLIENT_MCP
        audit(principal, "mcp.list_instances", "instance",
              detail=f"count={len(clean)}", result="allow",
              resource_type="instance", client=AUDIT_CLIENT_MCP)
    return {"ok": True, "count": len(clean), "instances": clean}


def run_inspection_tool(
    instance_id: str,
    template_id=None,
    inspector_name: str = "Jack",
    principal=None,
) -> dict:
    # 越权检查前置：未授权直接拒绝，避免为无效请求触发重型引导/连接。
    deny = _assert_or_deny(principal, instance_id)
    if deny is not None:
        return deny
    st = _ensure()
    try:
        inst = st["IM"].get_instance_decrypted(instance_id)
    except Exception:
        inst = None
    if not inst:
        return {"ok": False, "error": f"instance not found: {instance_id}"}
    db_type = inst.get("db_type")
    if not db_type:
        return {"ok": False, "error": "instance missing db_type"}
    try:
        result = st["run"](db_type, inst, inspector_name, template_id)
    except Exception as e:  # 软降级，绝不抛给 MCP 协议层
        return {"ok": False, "error": f"inspection failed: {e}"}
    _audit(principal, "mcp.run_inspection", instance_id, "allow")
    return {"ok": True, "result": result}


def slow_queries_tool(instance_id: str, top_n: int = 10, lang: str = "zh",
                      principal=None) -> dict:
    deny = _assert_or_deny(principal, instance_id)
    if deny is not None:
        return deny
    res = _run_analysis_subprocess("slow_query", instance_id)
    _audit(principal, "mcp.slow_queries", instance_id,
           "allow" if res.get("ok") else "deny", detail=f"ok={res.get('ok')}")
    return res


def lock_tree_tool(instance_id: str, principal=None) -> dict:
    deny = _assert_or_deny(principal, instance_id)
    if deny is not None:
        return deny
    res = _run_analysis_subprocess("lock", instance_id)
    _audit(principal, "mcp.lock_tree", instance_id,
           "allow" if res.get("ok") else "deny", detail=f"ok={res.get('ok')}")
    return res


def index_health_tool(instance_id: str, days_threshold: int = 90,
                      principal=None) -> dict:
    deny = _assert_or_deny(principal, instance_id)
    if deny is not None:
        return deny
    res = _run_analysis_subprocess("index_health", instance_id)
    _audit(principal, "mcp.index_health", instance_id,
           "allow" if res.get("ok") else "deny", detail=f"ok={res.get('ok')}")
    return res


def baseline_check_tool(instance_id: str, principal=None) -> dict:
    deny = _assert_or_deny(principal, instance_id)
    if deny is not None:
        return deny
    res = _run_analysis_subprocess("baseline", instance_id)
    _audit(principal, "mcp.baseline_check", instance_id,
           "allow" if res.get("ok") else "deny", detail=f"ok={res.get('ok')}")
    return res


def ai_diagnose_tool(instance_id: str, goal: str = "对目标数据源做一次综合诊断",
                    principal=None) -> dict:
    deny = _assert_or_deny(principal, instance_id)
    if deny is not None:
        return deny
    try:
        from modules.intelligence.hub import get_hub
        hub = get_hub()
        result = hub.dispatch(goal, instance_id, inputs=None)
        _audit(principal, "mcp.ai_diagnose", instance_id, "allow")
        return {"ok": True, "result": result}
    except Exception as e:
        _audit(principal, "mcp.ai_diagnose", instance_id, "deny", detail=str(e)[:200])
        return {"ok": False, "error": f"ai_diagnose failed: {e}"}


def _chat2db_datasource_for(instance_id: str) -> str | None:
    """经 CHAT2DB_DATASOURCE_MAP（JSON：DBCheck instance_id -> Chat2DB datasource_id）映射。

    未配置或解析失败返回 None（调用方据此要求显式 datasource_id）。
    """
    raw = os.environ.get("CHAT2DB_DATASOURCE_MAP")
    if not raw:
        return None
    try:
        mp = json.loads(raw)
        return mp.get(instance_id)
    except Exception:
        return None


def nl2sql_tool(question: str, datasource_id: str = None,
                instance_id: str = None, principal=None) -> dict:
    """自然语言转 SQL：桥接 Chat2DB 上游 MCP text2sql（协议层，零代码嵌入）。

    可见性：若给了 instance_id 且已鉴权，先校验，避免越权触发上游生成。
    Chat2DB 未配置/不可用 → 清晰错误（error_code=CHAT2DB_UNAVAILABLE），不击穿通道。
    生成的 SQL 若需执行，交由 DBCheck 既有 dbcheck.execute_sql 写类 Skill（WriteGate）。
    """
    if not question or not str(question).strip():
        return {"ok": False, "error_code": "BAD_REQUEST", "error": "question 不能为空"}
    # 可见性前置（已鉴权才校验；未鉴权保持本地 stdio 全量兼容）
    if instance_id and principal is not None:
        deny = _assert_or_deny(principal, instance_id)
        if deny is not None:
            return deny
    ds = datasource_id or (instance_id and _chat2db_datasource_for(instance_id))
    if not ds:
        return {
            "ok": False,
            "error_code": "CHAT2DB_NO_DATASOURCE",
            "error": "未提供 datasource_id 且无法从 instance_id 映射 Chat2DB 数据源"
                     "（请配置 CHAT2DB_DATASOURCE_MAP 或显式传 datasource_id）",
        }
    try:
        from modules.mcp_server.chat2db_bridge import get_bridge
        sql = get_bridge().text2sql(str(question).strip(), ds)
    except Exception as e:
        code = getattr(e, "error_code", None) or "CHAT2DB_UNAVAILABLE"
        return {"ok": False, "error_code": code, "error": f"Chat2DB 调用失败: {e}"}
    _audit(principal, "mcp.nl2sql", instance_id or ds,
           "allow" if sql else "deny", detail=f"q={str(question)[:60]}")
    return {"ok": True, "datasource_id": ds, "sql": sql}


def bicqa_ask_tool(question: str, dbtype: str = "", mask: bool = True,
                   principal=None) -> dict:
    """BIC-QA 知识问答：桥接外部知识库（协议层，零代码嵌入）。

    不绑定具体实例（纯知识检索），无需可见性校验；但任何外部 API 调用都写审计。
    BIC-QA 未配置/不可用 → 清晰错误（error_code=BICQA_UNAVAILABLE），不击穿通道。
    """
    if not question or not str(question).strip():
        return {"ok": False, "error_code": "BAD_REQUEST", "error": "question 不能为空"}
    try:
        from modules.mcp_server.bicqa_bridge import get_bridge
        result = get_bridge().ask(
            str(question).strip(), dbtype=str(dbtype or ""), mask=bool(mask)
        )
    except Exception as e:
        code = getattr(e, "error_code", None) or "BICQA_UNAVAILABLE"
        return {"ok": False, "error_code": code, "error": f"BIC-QA 调用失败: {e}"}
    _audit(principal, "mcp.bicqa_ask", "bicqa",
           "allow" if result else "deny", detail=f"q={str(question)[:60]}")
    return {"ok": True, "answer": result}


# handler_key -> 实现函数（供 server.dispatch_tool 接线；与 registry 共用注册表）
HANDLERS = {
    "list_instances": list_instances_tool,
    "run_inspection": run_inspection_tool,
    "slow_queries": slow_queries_tool,
    "lock_tree": lock_tree_tool,
    "index_health": index_health_tool,
    "baseline_check": baseline_check_tool,
    "ai_diagnose": ai_diagnose_tool,
    "nl2sql": nl2sql_tool,
    "bicqa_ask": bicqa_ask_tool,
}
