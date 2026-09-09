# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""多 Agent Skills 调度 + WriteGate（阶段 B：把关与工具）。

Skills 与 MCP 工具共用 ``modules.mcp_server.registry`` 这一唯一注册表
（规划文档 4.3「Skills ≡ P0 MCP 工具」）。本模块是 Skills 在「多 Agent 侧」的执行入口：

* 读类 Skill（access_mode=read，如 slow_queries / lock_tree）→ 直接复用
  ``modules.mcp_server.tools.HANDLERS`` 中的同一实现（一次定义、MCP/多 Agent 两方复用）；
* 写类 / 破坏性 Skill（access_mode=write，如 kill_session / apply_index /
  execute_sql）→ 一律走 WriteGate：生成 SQL 语句并提交 SQL 审计，进入
  ``pending_approval``（命中阻断规则则 ``blocked``），审批通过后才受控执行。

WriteGate 直接复用 SQL 审计状态机（submit_audit → approve_task → execute_task），
不另造轮子，也与阶段 0 的 PDP（modules.access）解耦——它只负责「写动作落地前的审批闸门」。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from modules.mcp_server.registry import get_spec


# ── 写类 Skill → SQL 文本 生成（供 WriteGate 提交 SQL 审计） ────────────────────
def _sql_for_skill(skill_name: str, args: Dict[str, Any]) -> str:
    """把写类 Skill 的参数转成一条（或一组）SQL 文本，用于 SQL 审计。"""
    if skill_name == "dbcheck.kill_session":
        sid = (args.get("session_id") or "?").strip()
        return f"KILL {sid};"
    if skill_name == "dbcheck.apply_index":
        table = (args.get("table") or "unknown_table").strip()
        cols = (args.get("columns") or "col").strip()
        safe_cols = cols.replace(" ", "")
        iname = (args.get("index_name") or "").strip() or \
            f"ix_{table.replace('.', '_')}_{safe_cols.replace(',', '_')}"
        uniq = "UNIQUE " if args.get("unique") else ""
        return f"CREATE {uniq}INDEX {iname} ON {table} ({cols});"
    if skill_name == "dbcheck.execute_sql":
        return (args.get("sql_text") or "").strip()
    # 兜底：若未来新增写类 Skill 但此处未实现，退回原始 sql_text（不静默丢失）
    return (args.get("sql_text") or "").strip()


def _resolve_db_type(instance_id: str) -> Optional[str]:
    """从实例管理器取库型；测试可 monkeypatch 本函数以避开真实库。

    返回 None 表示实例不存在或无权访问（调用方据此拒绝，避免绕过隔离）。
    """
    try:
        from modules.pro.instance_manager import get_instance_manager
        inst = get_instance_manager().get_instance_decrypted(instance_id)
        if inst:
            return inst.get("db_type")
    except Exception:
        return None
    return None


def _submitter_label(principal) -> str:
    return principal.label() if hasattr(principal, "label") else "agent"


class WriteGate:
    """写类 / 破坏性动作闸门：复用 SQL 审计状态机。

    提案（propose）→ 待审批（pending_approval）→ 审批（approve/reject）
    → 执行（execute）。任何写类 Skill 被调用时都必须先过本闸门；本类不持有任何
    业务状态，所有留痕落在 modules.sqlaudit 的状态机里。
    """

    @staticmethod
    def propose(
        skill_name: str,
        args: Dict[str, Any],
        submitter: str,
        instance_id: str,
        db_type: str,
        remark: str = "",
    ) -> Dict[str, Any]:
        """把写类动作作为一条 SQL 审核任务提交，强制进入 pending_approval。"""
        from modules.sqlaudit import service as svc
        from modules.sqlaudit import models as sqlaudit_models

        spec = get_spec(skill_name)
        sql_text = _sql_for_skill(skill_name, args)
        env = (args.get("env") or "prod")
        task = svc.submit_audit(
            submitter=submitter,
            instance_id=instance_id,
            db_type=db_type,
            env=env,
            sql_text=sql_text,
            plan_enabled=False,
            exec_enabled=False,
            remark=remark or f"WriteGate@{skill_name}",
        )
        # 写类 Skill 即便 SQL 审计规则未判 high，也强制待审批（闸门语义优先于规则评分）
        if spec and spec["risk"]["requires_approval"] and task.get("status") not in ("blocked",):
            sqlaudit_models.update_task_status(task["id"], "pending_approval")
            task = svc.get_task(task["id"])
        return task

    @staticmethod
    def resolve(task_id: int, approver: str, action: str, comment: str = "") -> Dict[str, Any]:
        """审批一条待审批任务（approve / reject）。"""
        from modules.sqlaudit import service as svc
        return svc.approve_task(task_id, approver, action, comment)

    @staticmethod
    def execute(task_id: int, approver: str, mode: str = "real") -> Dict[str, Any]:
        """受控执行一条已审批任务。"""
        from modules.sqlaudit import service as svc
        return svc.execute_task(task_id, mode=mode, operator=approver)


def dispatch_skill(
    name: str,
    args: Dict[str, Any],
    principal=None,
    approver: str = None,
) -> Dict[str, Any]:
    """Skills 中央调度（多 Agent 侧的唯一入口）。

    返回 dict：
    * 未知 Skill          -> {ok:False, error_code:"UNKNOWN_SKILL"}
    * 读类 Skill          -> 复用 tools.HANDLERS 直接执行（一次定义两方复用）
    * 写类 Skill（无审批人）-> {ok:False, error_code:"APPROVAL_REQUIRED",
                                task_id, task_no, sql_text, risk_level, status}
    * 写类 Skill（有审批人）-> {ok:True, executed:True, task_id, result}
    """
    args = args or {}
    spec = get_spec(name)
    if spec is None:
        return {"ok": False, "error_code": "UNKNOWN_SKILL", "error": f"unknown skill: {name}"}
    risk = spec["risk"]

    # ── 读类 Skill：复用 MCP 工具同一实现 ──
    if risk["access_mode"] == "read":
        from modules.mcp_server.tools import HANDLERS
        handler = HANDLERS.get(spec["handler_key"])
        if handler is None:
            return {"ok": False, "error": f"handler not wired: {spec['handler_key']}"}
        try:
            return handler(principal=principal, **args)
        except TypeError as e:
            return {"ok": False, "error": f"参数错误: {e}"}

    # ── 写类 / 破坏性 Skill：统一走 WriteGate ──
    instance_id = args.get("instance_id")
    if not instance_id:
        return {"ok": False, "error_code": "BAD_REQUEST",
                "error": "写类 Skill 必须指定 instance_id"}
    db_type = args.get("db_type") or _resolve_db_type(instance_id)
    if not db_type:
        return {"ok": False, "error_code": "BAD_REQUEST",
                "error": "无法解析目标数据源 db_type（实例不存在或无权访问）"}
    submitter = _submitter_label(principal)
    try:
        task = WriteGate.propose(
            name, args, submitter, instance_id, db_type,
            remark=args.get("reason", ""),
        )
    except Exception as e:
        return {"ok": False, "error_code": "PROPOSE_FAILED", "error": f"WriteGate 提案失败: {e}"}

    # 无审批人：返回待审批提案（不执行）——这是默认的安全行为
    if not approver:
        return {
            "ok": False,
            "error_code": "APPROVAL_REQUIRED",
            "error": "写类操作需经 SQL 审计审批（pending_approval）后方可执行",
            "task_id": task["id"],
            "task_no": task.get("task_no"),
            "sql_text": _sql_for_skill(name, args),
            "risk_level": risk["risk_level"],
            "status": task.get("status"),
        }

    # 带审批人：自动审批 + 受控执行（受控闭环的「提案—确认—执行—观察」末段）
    WriteGate.resolve(task["id"], approver, "approve",
                      comment=f"auto-approved by {approver} via Skill")
    try:
        res = WriteGate.execute(task["id"], approver, mode="real")
    except Exception as e:
        return {"ok": False, "error_code": "EXECUTE_FAILED",
                "error": f"受控执行失败: {e}", "task_id": task["id"]}
    return {"ok": True, "executed": True, "task_id": task["id"],
            "task_no": task.get("task_no"), "result": res}
