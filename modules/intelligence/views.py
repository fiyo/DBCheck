# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""协同诊断中枢的 Web 接口。"""

from __future__ import annotations

import json

from flask import Blueprint, Response, jsonify, request, session, stream_with_context

from modules.config.version import EDITION
from .hub import get_hub

intelligence_bp = Blueprint("intelligence", __name__)


def _save_history(result: dict, instance_id: str, goal: str) -> dict:
    """把一次诊断结果落库到诊断历史，返回摘要（失败静默）。"""
    try:
        from .diagnosis_store import save_diagnosis

        meta = result.get("target_meta") or {}
        return save_diagnosis(
            instance_id=instance_id,
            instance_name=meta.get("instance_name") or "",
            goal=goal or result.get("goal") or "",
            result=result,
        )
    except Exception:
        return {}


@intelligence_bp.route("/api/intelligence/capabilities")
def capabilities():
    hub = get_hub()
    return jsonify({"edition": EDITION, "capabilities": hub.capabilities()})


@intelligence_bp.route("/api/intelligence/diagnose")
def diagnose():
    hub = get_hub()
    instance_id = request.args.get("instance_id", "").strip()
    goal = request.args.get("goal", "").strip()
    if not instance_id:
        return jsonify({"ok": False, "msg": "instance_id required"}), 400
    result = hub.dispatch(goal=goal, instance_id=instance_id)
    record = _save_history(result, instance_id, goal)
    if record:
        result["history_id"] = record.get("id")
        result["diag_no"] = record.get("diag_no")
    return jsonify({"ok": True, **result})


@intelligence_bp.route("/api/intelligence/diagnose/stream")
def diagnose_stream():
    """SSE 流式诊断：逐个专员执行并推送进度，最后推送完整结果。"""
    hub = get_hub()
    instance_id = request.args.get("instance_id", "").strip()
    goal = request.args.get("goal", "").strip()

    def _gen():
        if not instance_id:
            yield "data: " + json.dumps(
                {"type": "error", "msg": "instance_id required"},
                ensure_ascii=False) + "\n\n"
            return
        try:
            for evt in hub.dispatch_stream(goal=goal, instance_id=instance_id):
                if evt.get("type") == "result":
                    record = _save_history(evt["result"], instance_id, goal)
                    if record:
                        evt["result"]["history_id"] = record.get("id")
                        evt["result"]["diag_no"] = record.get("diag_no")
                yield "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"
        except Exception as e:
            yield "data: " + json.dumps(
                {"type": "error", "msg": str(e)}, ensure_ascii=False) + "\n\n"

    resp = Response(stream_with_context(_gen()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ── 诊断历史 ──────────────────────────────────────────
@intelligence_bp.route("/api/intelligence/history")
def list_history():
    instance_id = request.args.get("instance_id", "").strip()
    try:
        from .diagnosis_store import list_diagnoses

        rows = list_diagnoses(instance_id=instance_id or None)
        return jsonify({"ok": True, "history": rows})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/history/<int:diag_id>", methods=["GET"])
def get_history(diag_id: int):
    try:
        from .diagnosis_store import get_diagnosis

        rec = get_diagnosis(diag_id)
        if not rec:
            return jsonify({"ok": False, "msg": "diagnosis not found"}), 404
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/history/<int:diag_id>", methods=["DELETE"])
def delete_history(diag_id: int):
    try:
        from .diagnosis_store import delete_diagnosis

        ok = delete_diagnosis(diag_id)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── 工单执行闭环 ──────────────────────────────────────────
@intelligence_bp.route("/api/intelligence/ticket", methods=["POST"])
def create_ticket():
    data = request.get_json(silent=True) or {}
    instance_id = (data.get("instance_id") or "").strip()
    if not instance_id:
        return jsonify({"ok": False, "msg": "instance_id required"}), 400
    instance_name = data.get("instance_name") or ""
    goal = data.get("goal") or ""
    findings = data.get("findings") or []
    plan = data.get("plan") or []
    plan_validation = data.get("plan_validation") or {}
    # 取最高严重级别作为工单严重度
    sev_order = {"critical": 3, "warning": 2, "info": 1}
    severity = "info"
    for f in findings:
        s = f.get("severity") if isinstance(f, dict) else None
        if s in sev_order and sev_order[s] > sev_order.get(severity, 0):
            severity = s
    try:
        from .ticket_store import create_ticket as _create

        ticket = _create(
            instance_id=instance_id,
            instance_name=instance_name,
            goal=goal,
            findings=findings,
            plan=plan,
            plan_validation=plan_validation,
            severity=severity,
        )
        return jsonify({"ok": True, "ticket": ticket})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/tickets")
def list_tickets():
    instance_id = request.args.get("instance_id", "").strip()
    status = request.args.get("status", "").strip()
    try:
        from .ticket_store import list_tickets as _list

        tickets = _list(instance_id=instance_id or None, status=status or None)
        return jsonify({"ok": True, "tickets": tickets})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/ticket/<int:ticket_id>", methods=["GET"])
def get_ticket(ticket_id: int):
    try:
        from .ticket_store import get_ticket as _get

        ticket = _get(ticket_id)
        if not ticket:
            return jsonify({"ok": False, "msg": "ticket not found"}), 404
        return jsonify({"ok": True, "ticket": ticket})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/ticket/<int:ticket_id>/update", methods=["POST"])
def update_ticket(ticket_id: int):
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip() or None
    assignee = (data.get("assignee") or "").strip() or None
    note = data.get("note")
    if note is not None:
        note = str(note).strip()
    try:
        from .ticket_store import update_ticket as _update

        ticket = _update(ticket_id, status=status, assignee=assignee, note=note)
        if not ticket:
            return jsonify({"ok": False, "msg": "ticket not found"}), 404
        return jsonify({"ok": True, "ticket": ticket})
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── 系统字典表（schema_knowledge）维护 ───────────────────────────────────
def _schema_dict_writer_ok() -> bool:
    """仅 admin / operator 可写系统字典表。"""
    roles = session.get("user_roles", []) or []
    if session.get("is_admin", False):
        return True
    return any(r in ("admin", "operator") for r in roles)


def _require_writer():
    if not _schema_dict_writer_ok():
        return jsonify({"ok": False, "msg": "权限不足：仅 admin / operator 可维护系统字典表"}), 403
    return None


@intelligence_bp.route("/api/intelligence/schema-dict", methods=["GET"])
def schema_dict_get():
    """读取系统字典表。可选项：db_type / view 过滤。"""
    from .schema_knowledge import get_knowledge

    db_type = request.args.get("db_type", "").strip()
    view = request.args.get("view", "").strip()
    kb = get_knowledge()
    databases = kb.get("databases", {})

    if db_type:
        databases = {db_type: databases.get(db_type, {})}
    if view:
        filtered = {}
        for dt, dt_info in databases.items():
            views = dt_info.get("views", {})
            if view in views:
                filtered[dt] = {"description": dt_info.get("description", ""), "views": {view: views[view]}}
        databases = filtered

    return jsonify({"ok": True, "databases": databases, "version": kb.get("version", "")})


@intelligence_bp.route("/api/intelligence/schema-dict", methods=["POST"])
def schema_dict_post():
    """新增：库 / 视图 / 字段。body 至少含 db_type。"""
    deny = _require_writer()
    if deny:
        return deny
    data = request.get_json(silent=True) or {}
    db_type = (data.get("db_type") or "").strip()
    view = (data.get("view") or "").strip()
    field = (data.get("field") or "").strip()
    if not db_type:
        return jsonify({"ok": False, "msg": "db_type 必填"}), 400

    from .schema_knowledge import get_knowledge, save_knowledge

    kb = get_knowledge()
    databases = kb.setdefault("databases", {})
    dt_info = databases.setdefault(db_type, {"description": data.get("description", "") or db_type, "views": {}})
    views = dt_info.setdefault("views", {})

    if not view:
        # 仅新增库类型
        return jsonify({"ok": True, "msg": "已确保库类型存在", "databases": databases})

    view_info = views.setdefault(view, {
        "description": data.get("view_description", "") or view,
        "keywords": data.get("keywords", []) or [],
        "fields": {},
    })
    if not field:
        return jsonify({"ok": True, "msg": "已确保视图存在", "databases": databases})

    if field in view_info.get("fields", {}):
        return jsonify({"ok": False, "msg": f"字段 {field} 已存在"}), 409
    view_info.setdefault("fields", {})[field] = {
        "type": (data.get("field_type") or "VARCHAR").strip(),
        "desc": (data.get("field_desc") or "").strip(),
    }
    if not save_knowledge(kb):
        return jsonify({"ok": False, "msg": "保存失败"}), 500
    return jsonify({"ok": True, "msg": "已新增字段", "databases": databases})


@intelligence_bp.route("/api/intelligence/schema-dict", methods=["PUT"])
def schema_dict_put():
    """更新：视图描述/keywords，或字段 type/desc。"""
    deny = _require_writer()
    if deny:
        return deny
    data = request.get_json(silent=True) or {}
    db_type = (data.get("db_type") or "").strip()
    view = (data.get("view") or "").strip()
    field = (data.get("field") or "").strip()
    if not db_type or not view:
        return jsonify({"ok": False, "msg": "db_type 与 view 必填"}), 400

    from .schema_knowledge import get_knowledge, save_knowledge

    kb = get_knowledge()
    views = kb.get("databases", {}).get(db_type, {}).get("views", {})
    if view not in views:
        return jsonify({"ok": False, "msg": "视图不存在"}), 404
    view_info = views[view]

    if field:
        if field not in view_info.get("fields", {}):
            return jsonify({"ok": False, "msg": "字段不存在"}), 404
        if data.get("field_type") is not None:
            view_info["fields"][field]["type"] = str(data["field_type"]).strip()
        if data.get("field_desc") is not None:
            view_info["fields"][field]["desc"] = str(data["field_desc"]).strip()
    else:
        if data.get("view_description") is not None:
            view_info["description"] = str(data["view_description"]).strip()
        if data.get("keywords") is not None:
            view_info["keywords"] = data["keywords"]

    if not save_knowledge(kb):
        return jsonify({"ok": False, "msg": "保存失败"}), 500
    return jsonify({"ok": True, "msg": "已更新", "databases": kb.get("databases", {})})


@intelligence_bp.route("/api/intelligence/schema-dict", methods=["DELETE"])
def schema_dict_delete():
    """删除：按 field > view > db_type 优先级逐层删除。"""
    deny = _require_writer()
    if deny:
        return deny
    db_type = request.args.get("db_type", "").strip()
    view = request.args.get("view", "").strip()
    field = request.args.get("field", "").strip()
    if not db_type:
        return jsonify({"ok": False, "msg": "db_type 必填"}), 400

    from .schema_knowledge import get_knowledge, save_knowledge

    kb = get_knowledge()
    databases = kb.get("databases", {})
    if db_type not in databases:
        return jsonify({"ok": False, "msg": "库类型不存在"}), 404
    dt_info = databases[db_type]
    views = dt_info.get("views", {})

    if field and view:
        if view not in views or field not in views[view].get("fields", {}):
            return jsonify({"ok": False, "msg": "字段不存在"}), 404
        del views[view]["fields"][field]
        msg = f"已删除字段 {field}"
    elif view:
        if view not in views:
            return jsonify({"ok": False, "msg": "视图不存在"}), 404
        del views[view]
        msg = f"已删除视图 {view}"
    else:
        del databases[db_type]
        msg = f"已删除库类型 {db_type}"

    if not save_knowledge(kb):
        return jsonify({"ok": False, "msg": "保存失败"}), 500
    return jsonify({"ok": True, "msg": msg, "databases": kb.get("databases", {})})


# ── Workflow 编排（阶段 D：Workflow Builder UI 后端） ──────────────────────────
@intelligence_bp.route("/api/intelligence/workflows", methods=["GET"])
def list_workflows_ep():
    try:
        from .workflow_store import list_workflows as _list
        return jsonify({"ok": True, "workflows": _list()})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflows", methods=["POST"])
def save_workflow_ep():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    steps = data.get("steps") or []
    edges = data.get("edges") or []
    wf_id = data.get("id")
    if wf_id is not None:
        try:
            wf_id = int(wf_id)
        except (TypeError, ValueError):
            wf_id = None
    try:
        from .workflow_store import save_workflow as _save
        wf = _save(name=name, steps=steps, edges=edges, wf_id=wf_id)
        return jsonify({"ok": True, "workflow": wf})
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflows/<int:wf_id>", methods=["DELETE"])
def delete_workflow_ep(wf_id: int):
    try:
        from .workflow_store import delete_workflow as _del
        return jsonify({"ok": _del(wf_id)})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflows/<int:wf_id>/run", methods=["POST"])
def run_workflow_ep(wf_id: int):
    """执行已保存的工作流。复用阶段 C 的 Workflow 引擎（DAG + 条件分支）。

    注意：前端 Builder 不暴露 ``when`` 表达式（避免 eval 风险），运行期
    ``when`` 恒为 None，所有节点按拓扑序执行。
    """
    data = request.get_json(silent=True) or {}
    instance_id = (data.get("instance_id") or "").strip()
    goal = data.get("goal") or ""
    if not instance_id:
        return jsonify({"ok": False, "msg": "instance_id required"}), 400
    try:
        from .workflow_store import get_workflow
        from .workflow import Workflow, Step

        wf = get_workflow(wf_id)
        if not wf:
            return jsonify({"ok": False, "msg": "workflow not found"}), 404
        steps = [
            Step(
                id=str(s.get("id")),
                kind=s.get("kind", "specialist"),
                ref=s.get("ref", "") or "",
                args=s.get("args") or {},
                label=s.get("label") or str(s.get("id")),
            )
            for s in wf["steps"]
        ]
        edges = [
            (str(e[0]), str(e[1]))
            for e in wf["edges"]
            if isinstance(e, (list, tuple)) and len(e) == 2
        ]
        engine = Workflow(steps, edges)
        result = engine.run(goal=goal, instance_id=instance_id, inputs={})
        result.pop("ctx", None)  # ctx 含非序列化对象，仅回传编排产物
        return jsonify({"ok": True, **result})
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflow-nodes", methods=["GET"])
def workflow_nodes_ep():
    """返回 Builder 可用的节点 ref 清单：专家能力 + 写类技能。"""
    try:
        hub = get_hub()
        specialists = hub.capabilities()
        skills: List[Dict[str, Any]] = []
        try:
            from modules.mcp_server.registry import get_skill_specs

            for spec in get_skill_specs():
                risk = spec.get("risk") or {}
                skills.append({
                    "name": spec.get("name"),
                    "title": spec.get("title") or spec.get("name"),
                    "kind": "skill",
                    "ref": spec.get("handler_key") or spec.get("name"),
                    "description": spec.get("description", ""),
                    "risk_level": risk.get("risk_level"),
                    "requires_approval": risk.get("requires_approval"),
                })
        except Exception:
            skills = []
        return jsonify({"ok": True, "specialists": specialists, "skills": skills})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Workflow 任务管理（独立导航「工作流任务」） ──────────────────────────
def _denorm_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """给任务补充工作流名称等冗余字段，便于前端卡片直接渲染。"""
    out = dict(task)
    try:
        from .workflow_store import get_workflow

        wf = get_workflow(task["workflow_id"])
        out["workflow_name"] = wf["name"] if wf else ("#%s" % task["workflow_id"])
    except Exception:
        out["workflow_name"] = "#%s" % task["workflow_id"]
    return out


@intelligence_bp.route("/api/intelligence/workflow-tasks", methods=["GET"])
def list_workflow_tasks_ep():
    try:
        from .workflow_task_store import list_tasks as _list

        tasks = [_denorm_task(t) for t in _list()]
        return jsonify({"ok": True, "tasks": tasks})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflow-tasks", methods=["POST"])
def create_workflow_task_ep():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    workflow_id = data.get("workflow_id")
    instance_id = (data.get("instance_id") or "").strip()
    goal = data.get("goal") or ""
    cron = (data.get("cron") or "").strip()
    if workflow_id is not None:
        try:
            workflow_id = int(workflow_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "msg": "workflow_id 必须是数字"}), 400
    try:
        from .workflow_task_store import save_task as _save

        task = _save(name=name, workflow_id=workflow_id, instance_id=instance_id,
                     goal=goal, cron=cron)
        return jsonify({"ok": True, "task": _denorm_task(task)})
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflow-tasks/<int:task_id>", methods=["GET"])
def get_workflow_task_ep(task_id: int):
    try:
        from .workflow_task_store import get_task as _get

        task = _get(task_id)
        if not task:
            return jsonify({"ok": False, "msg": "task not found"}), 404
        return jsonify({"ok": True, "task": _denorm_task(task)})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflow-tasks/<int:task_id>", methods=["PUT"])
def update_workflow_task_ep(task_id: int):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    workflow_id = data.get("workflow_id")
    instance_id = (data.get("instance_id") or "").strip()
    goal = data.get("goal") or ""
    cron = (data.get("cron") or "").strip()
    if workflow_id is not None:
        try:
            workflow_id = int(workflow_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "msg": "workflow_id 必须是数字"}), 400
    try:
        from .workflow_task_store import save_task as _save

        task = _save(name=name, workflow_id=workflow_id, instance_id=instance_id,
                     goal=goal, cron=cron, task_id=task_id)
        return jsonify({"ok": True, "task": _denorm_task(task)})
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflow-tasks/<int:task_id>", methods=["DELETE"])
def delete_workflow_task_ep(task_id: int):
    try:
        from .task_runner import get_runner

        get_runner().stop(task_id)  # 运行中先尝试停止
    except Exception:
        pass
    try:
        from .workflow_task_store import delete_task as _del

        ok = _del(task_id)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflow-tasks/<int:task_id>/start", methods=["POST"])
def start_workflow_task_ep(task_id: int):
    try:
        from .task_runner import get_runner

        ok = get_runner().start(task_id)
        if not ok:
            return jsonify({"ok": False, "msg": "任务不存在或已在运行"}), 409
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflow-tasks/<int:task_id>/stop", methods=["POST"])
def stop_workflow_task_ep(task_id: int):
    try:
        from .task_runner import get_runner

        ok = get_runner().stop(task_id)
        if not ok:
            return jsonify({"ok": False, "msg": "任务未运行或已停止"}), 409
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@intelligence_bp.route("/api/intelligence/workflow-tasks/<int:task_id>/runs", methods=["GET"])
def workflow_task_runs_ep(task_id: int):
    try:
        from .workflow_run_store import list_runs as _list

        runs = _list(task_id)
        return jsonify({"ok": True, "runs": runs})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ═══ BIC-QA 知识问答（阶段 1：诊断中心面板后端）════════════════════════════
# 仅做「协议调用外部 SaaS + 配置隔离 + 优雅降级」，绝不内置 BIC-QA 代码/硬编码 Key。
# 配置存于 dbc_config.json 的 bicqa 字段（该文件已被 .gitignore 忽略，不进版本库）。
import os as _os

_PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
_DBCONFIG_PATH = _os.path.join(_PROJECT_ROOT, "dbc_config.json")

# API Key 占位符：前端回填时若仍是此值表示「不修改」；绝不回传明文
_API_KEY_SENTINEL = "***SET***"


def _bicqa_load_cfg() -> dict:
    if not _os.path.exists(_DBCONFIG_PATH):
        return {}
    try:
        with open(_DBCONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("bicqa", {}) or {}
    except Exception:
        return {}


def _bicqa_save_cfg(bicqa: dict) -> None:
    cfg = {}
    if _os.path.exists(_DBCONFIG_PATH):
        try:
            with open(_DBCONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg["bicqa"] = bicqa
    with open(_DBCONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


@intelligence_bp.route("/api/bicqa/config", methods=["GET"])
def bicqa_config_get():
    """返回 BIC-QA 配置；api_key 仅暴露是否已配置，绝不回传明文。"""
    b = _bicqa_load_cfg()
    api_key = (b.get("api_key") or "").strip()
    return jsonify({
        "enabled": bool(b.get("enabled", False)),
        "api_base": b.get("api_base") or "https://api.bic-qa.com",
        "default_dbtype": b.get("default_dbtype", "") or "",
        "enable_masking": b.get("enable_masking", True),
        "has_key": bool(api_key),
        "api_key_masked": _API_KEY_SENTINEL if api_key else "",
    })


@intelligence_bp.route("/api/bicqa/config", methods=["POST"])
def bicqa_config_post():
    data = request.json or {}
    b = _bicqa_load_cfg()
    if "enabled" in data:
        b["enabled"] = bool(data["enabled"])
    if "api_base" in data:
        b["api_base"] = (data["api_base"] or "https://api.bic-qa.com").strip()
    if "default_dbtype" in data:
        b["default_dbtype"] = (data.get("default_dbtype") or "").strip()
    if "enable_masking" in data:
        b["enable_masking"] = bool(data["enable_masking"])
    # api_key：仅当传入「新值」（非占位符、非空）时更新；占位符/空表示保持原值
    if "api_key" in data:
        k = (data.get("api_key") or "").strip()
        if k and k != _API_KEY_SENTINEL:
            b["api_key"] = k
    _bicqa_save_cfg(b)
    # 配置变更后清空桥接单例缓存，下次调用重新读配置
    try:
        from modules.mcp_server.bicqa_bridge import reset_bridge
        reset_bridge()
    except Exception:
        pass
    return jsonify({"ok": True, "has_key": bool(b.get("api_key"))})


@intelligence_bp.route("/api/bicqa/ask", methods=["POST"])
def bicqa_ask():
    """转发知识问答到 BIC-QA；未配置/不可达时优雅降级（ok:false + error_code）。"""
    data = request.json or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error_code": "BICQA_BAD_REQUEST", "error": "问题不能为空"}), 400
    dbtype = (data.get("dbtype") or "").strip()
    # 脱敏：未显式指定时回落到配置里的 enable_masking
    if "mask" in data:
        mask = bool(data["mask"])
    else:
        mask = bool(_bicqa_load_cfg().get("enable_masking", True))
    try:
        from modules.mcp_server.bicqa_bridge import get_bridge, BICQAUnavailable, BICQAError
        try:
            bridge = get_bridge()
        except BICQAUnavailable as e:
            return jsonify({"ok": False, "error_code": e.error_code, "error": str(e)})
        try:
            result = bridge.ask(question, dbtype=dbtype, mask=mask)
        except BICQAUnavailable as e:
            return jsonify({"ok": False, "error_code": e.error_code, "error": str(e)})
        except BICQAError as e:
            return jsonify({"ok": False, "error_code": e.error_code, "error": str(e)})
        return jsonify({"ok": True, "result": result, "dbtype": dbtype})
    except Exception as e:  # 兜底：任何意外都不击穿通道
        return jsonify({"ok": False, "error_code": "BICQA_ERROR", "error": str(e)})


# ── 阶段 3：AWR 报告脱敏直传 BIC-QA 分析 ──────────────────────────────────────
# 主链路走官方契约 /skills/qa：AWR HTML → 本地解析 → 指标摘要 → 脱敏 → 问答。
# 不使用 BIC-QA 抓包发现的 multipart 上传端点（非官方契约，稳定性无保障）。

def _bicqa_awr_uploads_dir():
    from modules.core.paths import AWR_UPLOADS_DIR
    return str(AWR_UPLOADS_DIR)


@intelligence_bp.route("/api/bicqa/awr", methods=["GET"])
def bicqa_awr_list():
    """列出可分析的 AWR 报告（awr_uploads 目录下的 .html/.htm）。"""
    try:
        import os
        d = _bicqa_awr_uploads_dir()
        items = []
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if not name.lower().endswith((".html", ".htm")):
                    continue
                p = os.path.join(d, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                # 文件名约定 awr_<task_id>.htm[l]
                task_id = name[4:].rsplit(".", 1)[0] if name.lower().startswith("awr_") else name.rsplit(".", 1)[0]
                items.append({
                    "task_id": task_id,
                    "filename": name,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })
        items.sort(key=lambda x: x["mtime"], reverse=True)
        return jsonify({"ok": True, "reports": items[:50]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@intelligence_bp.route("/api/bicqa/awr", methods=["POST"])
def bicqa_awr_analyze():
    """AWR 报告指标摘要脱敏后经 /skills/qa 直传 BIC-QA 四段式分析。

    未配置 Key / 用户关闭 / 不可达时优雅降级（ok:false + error_code）。
    """
    data = request.json or {}
    task_id = (data.get("task_id") or "").strip()
    if not task_id or any(c in task_id for c in "/\\."):
        return jsonify({"ok": False, "error_code": "BICQA_BAD_REQUEST", "error": "task_id 非法"}), 400

    # 用户显式关闭 → 拒绝外发
    if _bicqa_load_cfg().get("enabled") is False:
        return jsonify({"ok": False, "error_code": "BICQA_DISABLED",
                        "error": "BIC-QA 已在配置页关闭，不向外部发送任何数据"})

    import os
    path = None
    d = _bicqa_awr_uploads_dir()
    for ext in (".html", ".htm"):
        cand = os.path.join(d, f"awr_{task_id}{ext}")
        if os.path.exists(cand):
            path = cand
            break
    if not path:
        return jsonify({"ok": False, "error_code": "BICQA_BAD_REQUEST", "error": "AWR 报告不存在"}), 404

    try:
        from modules.web.awr_parser import parse_awr_report, build_awr_ai_summary
        awr_data = parse_awr_report(path)
        meta = awr_data.get("metadata", {}) or {}
        summary = build_awr_ai_summary(awr_data, meta)
        if not summary.strip():
            return jsonify({"ok": False, "error_code": "BICQA_BAD_REQUEST", "error": "AWR 解析结果为空"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error_code": "BICQA_BAD_REQUEST", "error": f"AWR 解析失败：{e}"})

    question = (
        "以下是 Oracle AWR 报告的指标摘要（已脱敏）。请作为 Oracle 性能专家，"
        "按 现象解读 / 根因排序 / 定位命令 / 处置建议 四段给出分析。\n\n" + summary
    )
    dbtype = (data.get("dbtype") or "oracle").strip() or "oracle"
    if "mask" in data:
        mask = bool(data["mask"])
    else:
        mask = bool(_bicqa_load_cfg().get("enable_masking", True))

    try:
        from modules.mcp_server.bicqa_bridge import get_bridge, BICQAUnavailable, BICQAError
        try:
            bridge = get_bridge()
        except BICQAUnavailable as e:
            return jsonify({"ok": False, "error_code": e.error_code, "error": str(e)})
        try:
            result = bridge.ask(question, dbtype=dbtype, mask=mask)
        except BICQAUnavailable as e:
            return jsonify({"ok": False, "error_code": e.error_code, "error": str(e)})
        except BICQAError as e:
            return jsonify({"ok": False, "error_code": e.error_code, "error": str(e)})
        return jsonify({"ok": True, "result": result, "task_id": task_id,
                        "meta": {k: meta.get(k, "") for k in ("db_name", "instance", "snap_range")}})
    except Exception as e:  # 兜底：任何意外都不击穿通道
        return jsonify({"ok": False, "error_code": "BICQA_ERROR", "error": str(e)})
