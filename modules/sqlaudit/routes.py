# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""SQL 审核 API 路由（蓝图 sqlaudit_bp）。

路由前缀 /api/sql-audit：
  POST   /submit          提交审核（SQL + 实例 + 环境 + 库类型）
  GET    /tasks           任务列表（支持 submitter / status 过滤）
  GET    /tasks/<id>      任务详情（含 items / rule_hits）
  GET    /rules           规则列表
  GET    /instances       可选目标实例列表（取自 InstanceManager）
"""
from flask import request, jsonify, g, session

from . import bp
from . import service


# 可审批 / 可执行角色（与 RBAC 种子角色 admin/viewer/operator 对齐；viewer 只读不可审批/执行）
APPROVER_ROLES = {"admin", "operator"}


def _current_actor():
    """解析当前操作用户（与 user_management.auth_decorator 语义一致，惰性导入避免顶层依赖）。

    优先 JWT Bearer，其次 Flask session；返回 (username, roles)。未登录返回 (None, [])。
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token:
        try:
            from modules.user_management.utils.jwt_util import decode_token
            p = decode_token(token)
            return p.get("username") or p.get("user_id"), list(p.get("roles", []))
        except Exception:
            pass
    if session.get("user_id"):
        try:
            from modules.user_management.services.user_service import UserService
            u = UserService().get_user(session["user_id"])
            if u:
                return u.get("username"), [r["role_code"] for r in u.get("roles", [])]
        except Exception:
            pass
    # 未登录：回退到 X-User 头（免登录/单用户部署可用），无角色信息
    return request.headers.get("X-User") or "anonymous", []


@bp.route("/submit", methods=["POST"])
def submit():
    try:
        data = request.get_json(force=True, silent=True) or {}
        sql_text = (data.get("sql_text") or "").strip()
        if not sql_text:
            return jsonify({"ok": False, "error": "sql_text 不能为空"}), 400
        username, _roles = _current_actor()
        submitter = data.get("submitter") or username or request.headers.get("X-User") or "anonymous"
        instance_id = data.get("instance_id")
        db_type = (data.get("db_type") or "mysql").lower()
        env = data.get("env") or "prod"
        plan_enabled = bool(data.get("plan_enabled", False))
        exec_enabled = bool(data.get("exec_enabled", False))
        remark = data.get("remark", "")
        task = service.submit_audit(
            submitter=submitter, instance_id=instance_id, db_type=db_type,
            env=env, sql_text=sql_text, plan_enabled=plan_enabled,
            exec_enabled=exec_enabled, remark=remark,
        )
        return jsonify({"ok": True, "task": task})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/write-skills", methods=["GET"])
def write_skills():
    """返回写类 Skill 规格（来自共用注册表，写操作工单表单据此动态生成）。"""
    try:
        from modules.mcp_server.registry import get_skill_specs
        specs = get_skill_specs()
        out = [{
            "name": s["name"],
            "title": s["title"],
            "description": s["description"],
            "domain": s.get("domain"),
            "inputSchema": s.get("inputSchema"),
            "risk": s.get("risk"),
            "tags": s.get("tags", []),
        } for s in specs]
        return jsonify({"ok": True, "skills": out})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/write-ticket", methods=["POST"])
def write_ticket():
    """写操作工单：结构化写类 Skill 经 WriteGate 提交 SQL 审计，强制 pending_approval。

    复用阶段 B 的 WriteGate（modules.intelligence.skills），与多 Agent 侧同一闸门；
    审批人随后在 SQL 审核任务详情中审批/执行，构成治理闭环。
    """
    try:
        username, _roles = _current_actor()
        data = request.get_json(force=True, silent=True) or {}
        skill_name = (data.get("skill_name") or "").strip()
        if not skill_name:
            return jsonify({"ok": False, "error": "skill_name 不能为空"}), 400
        instance_id = (data.get("instance_id") or "").strip()
        if not instance_id:
            return jsonify({"ok": False, "error": "写操作必须指定目标实例"}), 400
        db_type = (data.get("db_type") or "").lower()
        if not db_type:
            # 兜底：从实例解析库型，避免前端漏传导致绕过隔离
            try:
                from modules.pro.instance_manager import get_instance_manager
                inst = get_instance_manager().get_instance_decrypted(instance_id)
                if inst:
                    db_type = (inst.get("db_type") or "").lower()
            except Exception:
                pass
        if not db_type:
            return jsonify({"ok": False, "error": "无法解析目标数据源 db_type（实例不存在或无权访问）"}), 400
        submitter = username or request.headers.get("X-User") or "anonymous"
        env = data.get("env") or "prod"
        reason = data.get("reason", "")
        args = {
            "instance_id": instance_id,
            "db_type": db_type,
            "env": env,
            "reason": reason,
            "session_id": (data.get("session_id") or "").strip(),
            "table": (data.get("table") or "").strip(),
            "columns": (data.get("columns") or "").strip(),
            "index_name": (data.get("index_name") or "").strip(),
            "unique": bool(data.get("unique", False)),
            "sql_text": (data.get("sql_text") or "").strip(),
        }
        from modules.intelligence.skills import WriteGate
        task = WriteGate.propose(skill_name, args, submitter, instance_id, db_type, remark=reason)
        return jsonify({"ok": True, "task": task})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/tasks", methods=["GET"])
def tasks():
    submitter = request.args.get("submitter")
    status = request.args.get("status")
    try:
        rows = service.list_tasks(submitter=submitter, status=status)
        return jsonify({"ok": True, "tasks": rows})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/tasks/<int:task_id>", methods=["GET"])
def task_detail(task_id):
    try:
        t = service.get_task(task_id)
        if not t:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        return jsonify({"ok": True, "task": t})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    try:
        n = service.delete_task(task_id)
        if n == 0:
            return jsonify({"ok": False, "error": "任务不存在或已删除"}), 404
        return jsonify({"ok": True, "deleted": n})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/tasks/<int:task_id>/execute", methods=["POST"])
def execute_task_route(task_id):
    """受控执行 SQL 审核任务（MVP3 执行器 + 回滚）。

    请求体: {mode: 'dry_run'|'real', max_affected_rows?, timeout?}
    dry_run 默认只读重跑执行计划；real 需任务已开启 exec_enabled、且高风险任务须先审批通过。
    需登录（RBAC），执行人取当前登录用户；免登录部署回退到 X-User 头身份。
    """
    try:
        username, _roles = _current_actor()
        data = request.get_json(force=True, silent=True) or {}
        mode = (data.get("mode") or "dry_run").lower()
        operator = username
        max_affected_rows = data.get("max_affected_rows")
        timeout = data.get("timeout")
        result = service.execute_task(
            task_id, mode=mode, operator=operator,
            max_affected_rows=max_affected_rows, timeout=timeout,
        )
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/tasks/<int:task_id>/bind-instance", methods=["POST"])
def bind_instance_route(task_id):
    """为已提交任务补充/更正目标实例。

    请求体: {instance_id: str}
    已执行的任务不可再绑定。
    """
    try:
        username, _roles = _current_actor()
        data = request.get_json(force=True, silent=True) or {}
        instance_id = (data.get("instance_id") or "").strip()
        task = service.bind_task_instance(task_id, instance_id)
        return jsonify({"ok": True, "task": task})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/tasks/<int:task_id>/approve", methods=["POST"])
def approve_task_route(task_id):
    """审批 SQL 审核任务（需 admin / operator 角色）。

    请求体: {action: 'approve'|'reject', comment?}
    """
    try:
        username, roles = _current_actor()
        # 仅当已登录且角色不足时才拦截；免登录部署（无角色）放行，审批人记为当前身份。
        # 核心安全闸（高风险真实执行须 approved_by）在 executor.real_execute 中独立 enforced。
        if roles and not (APPROVER_ROLES & set(roles)):
            return jsonify({"ok": False, "error": "权限不足：审批需 admin 或 operator 角色"}), 403
        data = request.get_json(force=True, silent=True) or {}
        action = data.get("action") or ""
        comment = data.get("comment", "")
        task = service.approve_task(task_id, username, action, comment)
        return jsonify({"ok": True, "task": task})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/tasks/<int:task_id>/rollback", methods=["POST"])
def rollback_task_route(task_id):
    """一键回滚（P1 ⑤）：执行任务已生成的自动回滚方案。

    高危险操作，需 admin / operator 角色。请求体: {confirm: true}（二次确认）。
    仅执行 auto_rollback=True 的回滚项。
    """
    try:
        username, roles = _current_actor()
        if roles and not (APPROVER_ROLES & set(roles)):
            return jsonify({"ok": False, "error": "权限不足：回滚需 admin 或 operator 角色"}), 403
        data = request.get_json(force=True, silent=True) or {}
        if not data.get("confirm"):
            return jsonify({"ok": False, "error": "需二次确认 confirm=true"}), 400
        result = service.execute_rollback(task_id, username)
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/rules", methods=["GET"])
def rules():
    try:
        return jsonify({"ok": True, "rules": service.list_rules()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


# 规则写操作（新增/编辑/删除/启停）仅 admin / operator 可操作，与审批/执行角色一致。
RULE_WRITER_ROLES = {"admin", "operator"}


def _require_rule_writer():
    """写操作角色闸：已登录且角色不足返回 403；免登录部署放行，由核心业务层兜底。"""
    _username, roles = _current_actor()
    if roles and not (RULE_WRITER_ROLES & set(roles)):
        return jsonify({"ok": False, "error": "权限不足：规则管理需 admin 或 operator 角色"}), 403
    return None


@bp.route("/rules", methods=["POST"])
def create_rule_route():
    """新增规则（MVP3.5，需 admin / operator）。"""
    deny = _require_rule_writer()
    if deny:
        return deny
    try:
        data = request.get_json(force=True, silent=True) or {}
        rule = service.create_rule(data)
        return jsonify({"ok": True, "rule": rule})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/rules/<string:rule_id>", methods=["PUT"])
def update_rule_route(rule_id):
    """编辑规则（MVP3.5，需 admin / operator）。"""
    deny = _require_rule_writer()
    if deny:
        return deny
    try:
        data = request.get_json(force=True, silent=True) or {}
        rule = service.update_rule(rule_id, data)
        return jsonify({"ok": True, "rule": rule})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/rules/<string:rule_id>", methods=["DELETE"])
def delete_rule_route(rule_id):
    """删除规则（MVP3.5，需 admin / operator）。"""
    deny = _require_rule_writer()
    if deny:
        return deny
    try:
        n = service.delete_rule(rule_id)
        if n == 0:
            return jsonify({"ok": False, "error": "规则不存在"}), 404
        return jsonify({"ok": True, "deleted": n})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/rules/<string:rule_id>/toggle", methods=["POST"])
def toggle_rule_route(rule_id):
    """启停规则（MVP3.5，需 admin / operator）。请求体 {enabled: bool}。"""
    deny = _require_rule_writer()
    if deny:
        return deny
    try:
        data = request.get_json(force=True, silent=True) or {}
        enabled = bool(data.get("enabled", True))
        rule = service.toggle_rule(rule_id, enabled)
        return jsonify({"ok": True, "rule": rule})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/instances", methods=["GET"])
def instances():
    try:
        from modules.pro.instance_manager import get_instance_manager
        insts = get_instance_manager().get_all_instances(mask_password=True)
        out = [{
            "id": i.get("id"),
            "name": i.get("name") or i.get("host"),
            "db_type": i.get("db_type"),
            "host": i.get("host"),
        } for i in insts]
        return jsonify({"ok": True, "instances": out})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e), "instances": []}), 500


def _list_rules():
    from . import models
    return models.list_rules()
