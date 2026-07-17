"""容灾备份 REST 接口（Blueprint: disaster_recovery, url_prefix=/api/dr）。

接口契约（供前端 index.html 调用）：
  GET  /api/dr/plans                  列表
  POST /api/dr/plans                  新建
  GET  /api/dr/plans/<name>           详情
  POST /api/dr/plans/<name>/run       立即备份
  POST /api/dr/plans/<name>/toggle    启用/停用
  DELETE /api/dr/plans/<name>         删除
  GET  /api/dr/backups                备份文件列表(?task=)
  GET  /api/dr/backups/<filename>/download  下载
  DELETE /api/dr/backups/<filename>   删除备份文件
  GET  /api/dr/history                执行历史(?limit=&task=)
  GET  /api/dr/health                 健康度评分
  GET  /api/dr/status                 调度器状态
  GET  /api/dr/connections            可选数据源下拉
"""

from flask import Blueprint, request, jsonify, send_file

from modules.disaster_recovery import engine

bp_dr = Blueprint("disaster_recovery", __name__, url_prefix="/api/dr")


@bp_dr.route("/plans", methods=["GET"])
def list_plans():
    return jsonify({"ok": True, "plans": engine.list_plans()})


@bp_dr.route("/plans", methods=["POST"])
def create_plan():
    try:
        data = request.get_json(force=True) or {}
        plan = engine.create_plan(data)
        return jsonify({"ok": True, "plan": plan})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp_dr.route("/plans/<name>", methods=["GET"])
def get_plan(name):
    plan = engine.get_plan(name)
    if not plan:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "plan": plan})


@bp_dr.route("/plans/<name>/run", methods=["POST"])
def run_plan(name):
    try:
        result = engine.run_now(name)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp_dr.route("/plans/<name>/toggle", methods=["POST"])
def toggle_plan(name):
    try:
        data = request.get_json(force=True) or {}
        enabled = bool(data.get("enabled", True))
        plan = engine.toggle_plan(name, enabled)
        return jsonify({"ok": True, "plan": plan})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp_dr.route("/plans/<name>", methods=["DELETE"])
def delete_plan(name):
    engine.delete_plan(name)
    return jsonify({"ok": True})


@bp_dr.route("/backups", methods=["GET"])
def list_backups():
    task = request.args.get("task")
    return jsonify({"ok": True, "backups": engine.list_backups(task)})


@bp_dr.route("/backups/<path:filename>/download", methods=["GET"])
def download_backup(filename):
    from modules.disaster_recovery import engine as _engine

    path = _engine.BACKUP_DIR / filename
    if not path.exists():
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_file(str(path), as_attachment=True)


@bp_dr.route("/backups/<path:filename>", methods=["DELETE"])
def delete_backup(filename):
    engine.delete_backup(filename)
    return jsonify({"ok": True})


@bp_dr.route("/history", methods=["GET"])
def history():
    limit = int(request.args.get("limit", 50))
    task = request.args.get("task")
    return jsonify({"ok": True, "history": engine.get_history(limit, task)})


@bp_dr.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "health": engine.get_health_metrics()})


@bp_dr.route("/status", methods=["GET"])
def status():
    return jsonify({"ok": True, "status": engine.get_status()})


@bp_dr.route("/connections", methods=["GET"])
def connections():
    return jsonify({"ok": True, "connections": engine.list_connections()})
