# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""多 Agent Skills 调度 + WriteGate（阶段 B：把关与工具）。

Skills 与 MCP 工具共用 ``modules.mcp_server.registry`` 这一唯一注册表
（规划文档 4.3「Skills ≡ P0 MCP 工具」）。本模块是 Skills 在「多 Agent 侧」的执行入口：

* 读类 Skill（access_mode=read，如 slow_queries / lock_tree）→ 直接复用
  ``modules.mcp_server.tools.HANDLERS`` 中的同一实现（一次定义、MCP/多 Agent 两方复用）；
* 写类 / 破坏性 Skill（access_mode=write，如 kill_session / apply_index /
  execute_sql）→ 同样由 ``HANDLERS`` 中的写类 handler 承接，handler 内部过 WriteGate：
  生成 SQL 语句并提交 SQL 审计，进入 ``pending_approval``（命中阻断规则则 ``blocked``），
  审批通过后才受控执行。本模块的 ``WriteGate`` 是闸门本体，写类 handler 复用它。

WriteGate 直接复用 SQL 审计状态机（submit_audit → approve_task → execute_task），
不另造轮子，也与阶段 0 的 PDP（modules.access）解耦——它只负责「写动作落地前的审批闸门」。
可见性（多租户隔离）由写类 handler 统一在提案前做 ``assert_visible``。
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
    写类工具的库型解析已收敛到 ``modules.mcp_server.tools._resolve_instance_db_type``，
    此处仅作向后兼容的薄封装（同一实现，避免两处口径漂移）。
    """
    from modules.mcp_server.tools import _resolve_instance_db_type
    return _resolve_instance_db_type(instance_id)


def sql_for_skill(skill_name: str, args: Dict[str, Any]) -> str:
    """公开别名：按写类 Skill 参数生成 SQL 文本（供 MCP 侧回显 / 审计）。
    """
    return _sql_for_skill(skill_name, args)


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


def _legacy_write_gate(
    name: str,
    args: Dict[str, Any],
    principal=None,
    approver: str = None,
) -> Dict[str, Any]:
    """写类 handler 缺失时的兜底闸门（防御未来新增写类 Skill 忘配 handler）。

    宁可落到 WriteGate 提案（pending_approval）也不放行执行：闸门语义优先于「有没有实现」。
    """
    instance_id = args.get("instance_id")
    if not instance_id:
        return {"ok": False, "error_code": "BAD_REQUEST",
                "error": "写类 Skill 必须指定 instance_id"}
    db_type = args.get("db_type") or _resolve_db_type(instance_id)
    if not db_type:
        return {"ok": False, "error_code": "BAD_REQUEST",
                "error": "无法解析目标数据源 db_type（实例不存在或无权访问）"}
    spec = get_spec(name)
    try:
        task = WriteGate.propose(
            name, args, _submitter_label(principal), instance_id, db_type,
            remark=args.get("reason", "") or "",
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error_code": "PROPOSE_FAILED", "error": f"WriteGate 提案失败: {e}"}
    if not approver:
        return {
            "ok": False,
            "error_code": "APPROVAL_REQUIRED",
            "error": "写类操作需经 SQL 审计审批（pending_approval）后方可执行",
            "task_id": task["id"],
            "task_no": task.get("task_no"),
            "sql_text": _sql_for_skill(name, args),
            "risk_level": (spec or {}).get("risk", {}).get("risk_level"),
            "status": task.get("status"),
        }
    WriteGate.resolve(task["id"], approver, "approve",
                      comment=f"auto-approved by {approver} via Skill")
    try:
        res = WriteGate.execute(task["id"], approver, mode="real")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error_code": "EXECUTE_FAILED",
                "error": f"受控执行失败: {e}", "task_id": task["id"]}
    return {"ok": True, "executed": True, "task_id": task["id"],
            "task_no": task.get("task_no"), "result": res}


def dispatch_skill(
    name: str,
    args: Dict[str, Any],
    principal=None,
    approver: str = None,
) -> Dict[str, Any]:
    """Skills 中央调度（多 Agent 侧的唯一入口）。

    实现一律走 ``modules.mcp_server.tools.HANDLERS``（一次定义、MCP / 多 Agent 两方复用）：
    读类直接执行；写类由 handler 内部过 WriteGate（提案 → 审批 → 受控执行）。
    写类 handler 缺失时退回 ``_legacy_write_gate``（仍走审批，绝不放行执行）。

    返回 dict：
    * 未知 Skill          -> {ok:False, error_code:"UNKNOWN_SKILL"}
    * 读类 handler 未接线  -> {ok:False, error_code:"HANDLER_NOT_WIRED"}
    * 读类 Skill          -> handler 结果
    * 写类 Skill（无审批人）-> {ok:False, error_code:"APPROVAL_REQUIRED",
                                task_id, task_no, sql_text, risk_level, status}
    * 写类 Skill（有审批人）-> {ok:True, executed:True, task_id, result}

    ``approver`` 只接受**形参**传入（受信调用方如 hub / 工作流显式指定）；args 里携带的
    approver / principal 一律丢弃，避免外部输入自审批绕过闸门。
    """
    args = dict(args or {})
    spec = get_spec(name)
    if spec is None:
        return {"ok": False, "error_code": "UNKNOWN_SKILL", "error": f"unknown skill: {name}"}
    risk = spec["risk"]
    clean = {k: v for k, v in args.items() if k not in ("approver", "principal")}

    from modules.mcp_server.tools import HANDLERS
    handler = HANDLERS.get(spec["handler_key"])
    if handler is None:
        if risk["access_mode"] == "write":
            return _legacy_write_gate(name, clean, principal=principal, approver=approver)
        return {"ok": False, "error_code": "HANDLER_NOT_WIRED",
                "error": f"handler not wired: {spec['handler_key']}"}

    try:
        if risk["access_mode"] == "read":
            return handler(principal=principal, **clean)
        return handler(principal=principal, approver=approver, **clean)
    except TypeError as e:
        return {"ok": False, "error_code": "BAD_REQUEST", "error": f"参数错误: {e}"}
