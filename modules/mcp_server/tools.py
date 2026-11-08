# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""MCP 工具实现（阶段 1 只读工具箱 + 阶段 2 写类闸门）。

每个 tool 只是现有 service 层的一层薄封装：
- list_instances   -> InstanceManager.get_all_instances(mask_password=True)
- run_inspection   -> run_target_inspection(db_type, instance, ...)
- slow_queries     -> subprocess(analysis_cli.py slow_query)
- lock_tree        -> subprocess(analysis_cli.py lock)
- index_health     -> subprocess(analysis_cli.py index_health)
- baseline_check   -> subprocess(analysis_cli.py baseline)
- ai_diagnose      -> DiagnosticHub.dispatch(goal, instance_id)
- nl2sql / bicqa_ask -> 外部上游 MCP / 知识库协议桥接
- kill_session / apply_index / execute_sql
                   -> ``_write_gate``：转 SQL 提交 SQL 审计状态机（WriteGate），
                      **从不直接连库执行**；无审批人只返回 pending_approval 提案。

隔离契约（沿用阶段 0）
----------------------
* ``principal`` 为 None 表示"无身份"（本地 stdio 且未开鉴权），保持向后兼容的
  全量行为；一旦解析出身份就必须过滤，绝不返回越权数据。
* 所有实例级工具（**含写类**）先过 ``assert_visible``，越权返回
  ``RESOURCE_NOT_VISIBLE``（不泄露"资源是否存在"）。
* 每次调用都写 ``um_audit_log``（client=mcp），形成可追溯时间线。
* 写类的审批人只能由进程内受信调用方经形参传入；工具入参里的 approver/principal
  一律丢弃（``_WRITE_TRUSTED_KWARGS``），杜绝外部客户端自审批。
* DB 连接类分析统一走 ``analysis_cli.py`` 子进程隔离（避免在主进程内直接拉起
  驱动/JVM），子进程单行 JSON 回传，其余日志走 stderr。
"""

import json
import os
import subprocess
import sys

_STATE: dict = {}


def _pool_enabled() -> bool:
    """连接池是否启用：环境变量最高优先级，否则读集中配置。"""
    if os.environ.get("DBCHECK_MCP_SUBPROC_POOL") == "1":
        return True
    try:
        from modules.mcp_server.config import MCP_CONFIG
        return bool(MCP_CONFIG.get("subproc_pool_enabled"))
    except Exception:  # noqa: BLE001
        return False


def _log(msg: str) -> None:
    sys.stderr.write(f"[mcp-tools] {msg}\n")
    sys.stderr.flush()


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
def _run_analysis_subprocess(analysis_type: str, instance_id, timeout: int = 600,
                             lang: str = "zh", days_threshold: int = 90) -> dict:
    """在干净子进程中跑 analysis_cli.py，返回其单行 JSON 结果。

    连接池（opt-in）：配置开启（``mcp_server_config.json`` / ``dbc_config.json`` 的
    ``mcp_server.subproc_pool_enabled`` / 环境变量 ``DBCHECK_MCP_SUBPROC_POOL=1``）
    时优先走 ``subproc_pool`` 复用常驻 worker（省去冷启动）；池不可用/失败时自动
    回退到原有的一次性子进程，行为与历史完全一致（零回归）。``lang`` /
    ``days_threshold`` 仅池路径使用，一次性路径忽略（保持历史契约）。
    """
    if _pool_enabled():
        try:
            from modules.mcp_server.subproc_pool import get_pool
            pool = get_pool()
            if pool is not None:
                return pool.run({
                    "analysis_type": analysis_type,
                    "instance_id": str(instance_id),
                    "db_type": "",
                    "lang": lang,
                    "days_threshold": days_threshold,
                }, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            _log(f"连接池不可用，回退一次性子进程: {e}")
    # 回退：一次性子进程（保持原有行为）
    py = sys.executable
    cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis_cli.py")
    env = dict(os.environ)
    env["DBCheck_MCP_ANALYSIS_SUBPROCESS"] = "1"
    try:
        proc = subprocess.run(
            [py, cli, analysis_type, str(instance_id), ""],
            capture_output=True, encoding="utf-8", timeout=timeout, env=env,
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
    res = _run_analysis_subprocess("slow_query", instance_id, lang=lang)
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
    res = _run_analysis_subprocess("index_health", instance_id,
                                   days_threshold=days_threshold)
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


# ── 写类工具（access_mode=write）：统一走 WriteGate ───────────────────────────
# 契约：写类工具**不直接连库执行**，一律把动作转成 SQL 提交 SQL 审计状态机
# （submit_audit → pending_approval → approve → execute），与 Web 侧「写操作工单」
# 和多 Agent 侧 Skills 共用同一个闸门，不另造审批逻辑。
_WRITE_TRUSTED_KWARGS = ("approver", "principal")


def _resolve_instance_db_type(instance_id: str) -> str | None:
    """从实例管理器取库型；None 表示实例不存在或无权访问。"""
    try:
        from modules.pro import get_instance_manager  # noqa: E402
        inst = get_instance_manager().get_instance_decrypted(instance_id)
        if inst:
            return (inst.get("db_type") or "").strip() or None
    except Exception:  # noqa: BLE001
        return None
    return None


def _submitter_label(principal) -> str:
    return principal.label() if hasattr(principal, "label") else "agent"


def _write_gate(op_key: str, args: dict, principal, approver) -> dict:
    """写类工具的公共实现：可见性 → 库型 → 提案 →（可选）审批执行 → 审计。

    ``approver`` **只能**由进程内受信调用方（dispatch_skill / 多 Agent）通过形参传入；
    MCP 客户端在 arguments 里塞的 approver / principal 一律丢弃（``_WRITE_TRUSTED_KWARGS``），
    否则任何客户端都能自审批，闸门形同虚设。
    """
    args = {k: v for k, v in (args or {}).items() if k not in _WRITE_TRUSTED_KWARGS}
    instance_id = str(args.get("instance_id") or "").strip()
    if not instance_id:
        return {"ok": False, "error_code": "BAD_REQUEST",
                "error": "写类工具必须指定 instance_id"}
    deny = _assert_or_deny(principal, instance_id)
    if deny is not None:
        return deny
    db_type = str(args.get("db_type") or "").strip() or _resolve_instance_db_type(instance_id)
    if not db_type:
        _audit(principal, f"mcp.{op_key}", instance_id, "deny",
               detail="db_type unresolved")
        return {"ok": False, "error_code": "BAD_REQUEST",
                "error": "无法解析目标数据源 db_type（实例不存在或无权访问）"}

    from modules.intelligence.skills import WriteGate, sql_for_skill  # noqa: E402
    skill_name = f"dbcheck.{op_key}"
    try:
        task = WriteGate.propose(
            skill_name, args, _submitter_label(principal), instance_id, db_type,
            remark=str(args.get("reason") or ""),
        )
    except Exception as e:  # noqa: BLE001
        _audit(principal, f"mcp.{op_key}", instance_id, "deny", detail=str(e)[:200])
        return {"ok": False, "error_code": "PROPOSE_FAILED",
                "error": f"WriteGate 提案失败: {e}"}

    sql_text = sql_for_skill(skill_name, args)
    status = task.get("status")
    base = {
        "task_id": task["id"],
        "task_no": task.get("task_no"),
        "sql_text": sql_text,
        "status": status,
        "risk_level": task.get("risk_level"),
        "instance_id": instance_id,
        "db_type": db_type,
    }
    # 命中阻断规则：直接拦截，不进审批流
    if status == "blocked":
        _audit(principal, f"mcp.{op_key}", instance_id, "deny",
               detail=f"blocked task={base['task_no']}")
        return {"ok": False, "error_code": "BLOCKED_BY_RULE",
                "error": "语句命中 SQL 审计阻断规则，已拦截（未进入审批）", **base}
    # 无审批人：只返回待审批提案，绝不执行 —— 默认的安全行为
    if not approver:
        _audit(principal, f"mcp.{op_key}", instance_id, "allow",
               detail=f"propose task={base['task_no']} status={status}")
        return {"ok": False, "error_code": "APPROVAL_REQUIRED",
                "error": "写类操作需经 SQL 审计审批（pending_approval）后方可执行", **base}
    # 带审批人：审批 + 受控执行（受信调用方专用，MCP 客户端拿不到这条路径）
    try:
        WriteGate.resolve(task["id"], approver, "approve",
                          comment=f"auto-approved by {approver} via WriteGate")
        res = WriteGate.execute(task["id"], approver, mode="real")
    except Exception as e:  # noqa: BLE001
        _audit(principal, f"mcp.{op_key}", instance_id, "deny",
               detail=f"execute failed: {str(e)[:180]}")
        return {"ok": False, "error_code": "EXECUTE_FAILED",
                "error": f"受控执行失败: {e}", **base}
    _audit(principal, f"mcp.{op_key}", instance_id, "allow",
           detail=f"execute task={base['task_no']} by {approver}")
    return {"ok": True, "executed": True, "approved_by": approver, **base, "result": res}


def kill_session_tool(principal=None, approver=None, **kwargs) -> dict:
    """终止会话（高危写）：生成 KILL 并提交 SQL 审计，审批后执行。"""
    if not str(kwargs.get("session_id") or "").strip():
        return {"ok": False, "error_code": "BAD_REQUEST", "error": "session_id 不能为空"}
    return _write_gate("kill_session", kwargs, principal, approver)


def apply_index_tool(principal=None, approver=None, **kwargs) -> dict:
    """创建/重建索引（写）：生成 CREATE INDEX 并提交 SQL 审计，审批后执行。"""
    if not str(kwargs.get("table") or "").strip() or not str(kwargs.get("columns") or "").strip():
        return {"ok": False, "error_code": "BAD_REQUEST", "error": "table / columns 不能为空"}
    return _write_gate("apply_index", kwargs, principal, approver)


def execute_sql_tool(principal=None, approver=None, **kwargs) -> dict:
    """执行 SQL（受控写）：语句直接提交 SQL 审计，阻断则拦截，否则待审批。"""
    if not str(kwargs.get("sql_text") or "").strip():
        return {"ok": False, "error_code": "BAD_REQUEST", "error": "sql_text 不能为空"}
    return _write_gate("execute_sql", kwargs, principal, approver)


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
    # 写类（access_mode=write）：实现落在 WriteGate，签名统一带 approver
    "kill_session": kill_session_tool,
    "apply_index": apply_index_tool,
    "execute_sql": execute_sql_tool,
}
