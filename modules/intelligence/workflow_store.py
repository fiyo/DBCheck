# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""Workflow 编排持久化（规划文档 4.4 D：Workflow Builder UI 后端）。

把用户在 Workflow Builder 中可视化编排的 DAG（节点 steps + 依赖边 edges）落库到
SQLite，支持列表 / 保存（按 id 幂等 upsert）/ 删除。执行时由 ``workflow.py`` 引擎
消费，本模块只负责存储，不解释编排语义。

存储路径一律以 ``modules.core.paths.DATA_DIR`` 为准，与诊断历史库同目录。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from modules.core import paths

_DB_PATH = str(paths.DATA_DIR / "intelligence_workflows.db")
_LOCK = threading.Lock()
_MIGRATED = False


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _init_db() -> None:
    global _MIGRATED
    if _MIGRATED:
        return
    with _LOCK:
        if _MIGRATED:
            return
        try:
            conn = sqlite3.connect(_DB_PATH)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    name      TEXT NOT NULL,
                    steps     TEXT NOT NULL DEFAULT '[]',
                    edges     TEXT NOT NULL DEFAULT '[]',
                    description TEXT NOT NULL DEFAULT '',
                    author    TEXT NOT NULL DEFAULT '',
                    version   TEXT NOT NULL DEFAULT '1.0.0',
                    category  TEXT NOT NULL DEFAULT '',
                    tags      TEXT NOT NULL DEFAULT '[]',
                    source    TEXT NOT NULL DEFAULT 'user',
                    visibility TEXT NOT NULL DEFAULT 'local',
                    installed_version TEXT NOT NULL DEFAULT '',
                    parent_id INTEGER,
                    rating_avg REAL NOT NULL DEFAULT 0,
                    rating_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # 迁移：旧库只有 6 列，补加市场/UGC 扩列（PRAGMA 探测，缺才 ALTER）
            _wf_cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(workflows)").fetchall()}
            _wf_new_cols = (
                ("description", "TEXT", "''"),
                ("author", "TEXT", "''"),
                ("version", "TEXT", "'1.0.0'"),
                ("category", "TEXT", "''"),
                ("tags", "TEXT", "'[]'"),
                ("source", "TEXT", "'user'"),
                ("visibility", "TEXT", "'local'"),
                ("installed_version", "TEXT", "''"),
                ("parent_id", "INTEGER", "NULL"),
                ("rating_avg", "REAL", "0"),
                ("rating_count", "INTEGER", "0"),
            )
            for col, ctype, dflt in _wf_new_cols:
                if col not in _wf_cols:
                    conn.execute(
                        f"ALTER TABLE workflows ADD COLUMN {col} {ctype} DEFAULT {dflt}")
            conn.commit()
            conn.close()
        finally:
            _MIGRATED = True


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        tags = json.loads(row["tags"] or "[]")
    except Exception:
        tags = []
    return {
        "id": row["id"],
        "name": row["name"],
        "steps": json.loads(row["steps"] or "[]"),
        "edges": json.loads(row["edges"] or "[]"),
        "description": row["description"] or "",
        "author": row["author"] or "",
        "version": row["version"] or "1.0.0",
        "category": row["category"] or "",
        "tags": tags if isinstance(tags, list) else [],
        "source": row["source"] or "user",
        "visibility": row["visibility"] or "local",
        "installed_version": row["installed_version"] or "",
        "parent_id": row["parent_id"],
        "rating_avg": row["rating_avg"] or 0,
        "rating_count": row["rating_count"] or 0,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_workflows() -> List[Dict[str, Any]]:
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM workflows ORDER BY updated_at DESC, id DESC"
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()


def get_workflow(wf_id: int) -> Optional[Dict[str, Any]]:
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM workflows WHERE id=?", (wf_id,)
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()


def save_workflow(name: str, steps: List[Dict[str, Any]], edges: List[Any],
                  wf_id: Optional[int] = None, *,
                  description: Optional[str] = None,
                  author: Optional[str] = None,
                  version: Optional[str] = None,
                  category: Optional[str] = None,
                  tags: Optional[List[str]] = None,
                  source: Optional[str] = None,
                  visibility: Optional[str] = None,
                  parent_id: Optional[int] = None,
                  installed_version: Optional[str] = None) -> Dict[str, Any]:
    """保存工作流。``wf_id`` 给定则幂等更新，否则新增。返回完整记录。

    ``description/author/version/category/tags/source/visibility/parent_id/
    installed_version`` 为市场/UGC 扩列，仅在显式提供（非 None）时写入，
    未提供则保留既存值，避免覆盖。
    """
    if not name or not name.strip():
        raise ValueError("工作流名称不能为空")
    steps = steps or []
    edges = edges or []
    # 结构校验：steps 必须含 id；edges 为 [from,to] 列表，两端均存在于 steps
    ids = {str(s.get("id")) for s in steps}
    if not ids:
        raise ValueError("工作流至少需要一个节点")
    for e in edges:
        if not (isinstance(e, (list, tuple)) and len(e) == 2):
            raise ValueError("edges 必须是 [from, to] 二元组列表")
        if str(e[0]) not in ids or str(e[1]) not in ids:
            raise ValueError(f"边 {list(e)} 引用的节点不存在")
    now = _now()
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            opt = {
                "description": description,
                "author": author,
                "version": version,
                "category": category,
                "tags": (json.dumps(tags, ensure_ascii=False)
                         if isinstance(tags, list) else tags),
                "source": source,
                "visibility": visibility,
                "parent_id": parent_id,
                "installed_version": installed_version,
            }
            if wf_id:
                cur = conn.execute(
                    "SELECT * FROM workflows WHERE id=?", (wf_id,))
                row = cur.fetchone()
                exist = dict(zip([c[0] for c in cur.description], row)) if row else {}
                setters = ["name=?", "steps=?", "edges=?", "updated_at=?"]
                params = [name.strip(), json.dumps(steps, ensure_ascii=False),
                          json.dumps(edges, ensure_ascii=False), now]
                for k, v in opt.items():
                    if v is not None:
                        setters.append(f"{k}=?")
                        params.append(v)
                params.append(wf_id)
                conn.execute(
                    "UPDATE workflows SET " + ",".join(setters) + " WHERE id=?",
                    params)
                if cur.rowcount == 0:
                    wf_id = None  # 不存在则退化为新增
            if not wf_id:
                cols = ["name", "steps", "edges", "created_at", "updated_at"]
                vals = [name.strip(), json.dumps(steps, ensure_ascii=False),
                        json.dumps(edges, ensure_ascii=False), now, now]
                for k, v in opt.items():
                    if v is not None:
                        cols.append(k)
                        vals.append(v)
                ph = ",".join("?" * len(cols))
                cur = conn.execute(
                    "INSERT INTO workflows (" + ",".join(cols) + ") "
                    "VALUES (" + ph + ")", vals)
                wf_id = cur.lastrowid
            conn.commit()
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM workflows WHERE id=?", (wf_id,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()


def delete_workflow(wf_id: int) -> bool:
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            cur = conn.execute("DELETE FROM workflows WHERE id=?", (wf_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ── P1：工作流市场（导出 / 导入 / 内置模板） ─────────────────────────────

MARKET_SCHEMA_VERSION = 1


def export_workflow(wf_id: int) -> Optional[Dict[str, Any]]:
    """导出工作流为可分享的 JSON 载荷（市场资产格式）。

    载荷不含实例敏感信息（只有编排结构），可安全外发。
    """
    wf = get_workflow(wf_id)
    if not wf:
        return None
    return {
        "schema": "dbcheck.workflow",
        "schema_version": MARKET_SCHEMA_VERSION,
        "name": wf["name"],
        "description": wf.get("description") or "",
        "steps": wf["steps"],
        "edges": wf["edges"],
        "exported_at": _now(),
    }


def import_workflow(payload: Dict[str, Any]) -> Dict[str, Any]:
    """导入一个市场载荷。同名自动加「（导入）」后缀去重。

    结构校验复用 ``save_workflow``；返回 ``{"ok", "workflow"/"error"}``。
    """
    if not isinstance(payload, dict) or payload.get("schema") != "dbcheck.workflow":
        return {"ok": False, "error": "不是有效的 DBCheck 工作流文件（schema 不符）"}
    steps = payload.get("steps") or []
    edges = payload.get("edges") or []
    if not steps:
        return {"ok": False, "error": "载荷中没有节点"}
    name = (payload.get("name") or "").strip() or "导入的工作流"
    existing = {w["name"] for w in list_workflows()}
    final = name
    n = 0
    while final in existing:
        n += 1
        final = "%s（导入%d）" % (name, n)
    try:
        wf = save_workflow(name=final, steps=steps, edges=edges)
        return {"ok": True, "workflow": wf}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


# ── P1：工作流市场 UGC（publish / list / install / update / rate / remote） ──

def publish_workflow(wf_id: int, *, author: str, version: str = "1.0.0",
                     description: str = "", category: str = "",
                     tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """将本地工作流发布到市场（标记 visibility=published 并写作者/版本元数据）。"""
    wf = get_workflow(wf_id)
    if not wf:
        return {"ok": False, "error": "工作流不存在"}
    save_workflow(
        name=wf["name"], steps=wf["steps"], edges=wf["edges"], wf_id=wf_id,
        description=description, author=author or "", version=version or "1.0.0",
        category=category or "", tags=tags or [], source="user",
        visibility="published")
    return {"ok": True, "workflow": get_workflow(wf_id)}


def _ver_tuple(v: str):
    """把 'a.b.c' 版本号解析为可比元组；解析失败回退 (0,0,0)。"""
    try:
        return tuple(int(x) for x in str(v or "0").split(".")[:3])
    except Exception:
        return (0, 0, 0)


def _listing_from_wf(wf: Dict[str, Any], *, installed: bool = False,
                     has_update: bool = False) -> Dict[str, Any]:
    return {
        "id": wf["id"],
        "name": wf["name"],
        "description": wf["description"],
        "author": wf["author"],
        "version": wf["version"],
        "category": wf["category"],
        "tags": wf["tags"],
        "source": wf["source"],
        "visibility": wf["visibility"],
        "steps": wf["steps"],
        "edges": wf["edges"],
        "installed": installed,
        "installed_version": wf["installed_version"],
        "has_update": has_update,
        "rating_avg": wf["rating_avg"],
        "rating_count": wf["rating_count"],
    }


def market_list(remote_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """聚合市场可安装资产：内置模板 + 已发布用户模板 + 可选远程源。"""
    out: List[Dict[str, Any]] = []
    # 1) 内置模板（代码内置，不落库）
    try:
        from .market_templates import builtin_templates
        for i, t in enumerate(builtin_templates()):
            out.append({
                "id": "builtin::%s" % t.get("name", i),
                "name": t.get("name", "内置模板"),
                "description": t.get("description", ""),
                "author": "DBCheck 官方",
                "version": "1.0.0",
                "category": "官方内置",
                "tags": [],
                "source": "builtin",
                "visibility": "published",
                "steps": t.get("steps", []),
                "edges": t.get("edges", []),
                "installed": False,
                "installed_version": "",
                "has_update": False,
                "rating_avg": 0,
                "rating_count": 0,
            })
    except Exception:
        pass
    # 2) 已发布用户模板
    pub = [w for w in list_workflows() if w.get("visibility") == "published"]
    installed_ids = {w.get("parent_id") for w in list_workflows()
                     if isinstance(w.get("parent_id"), int)}
    for w in pub:
        installed = w["id"] in installed_ids
        inst_ver = ""
        inst_id = None
        for ins in list_workflows():
            if ins.get("parent_id") == w["id"]:
                inst_ver = ins.get("installed_version", "")
                inst_id = ins.get("id")
        has_update = installed and _ver_tuple(inst_ver) < _ver_tuple(w["version"])
        item = _listing_from_wf(w, installed=installed, has_update=has_update)
        item["installed_id"] = inst_id
        out.append(item)
    # 3) 可选远程源
    if remote_url:
        out.extend(fetch_remote_market(remote_url))
    return out


def fetch_remote_market(url: str, timeout: int = 10) -> List[Dict[str, Any]]:
    """拉取远程市场索引（JSON 数组）。每条需含 steps/edges；失败返回 []。"""
    import ssl
    import urllib.request
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "DBCheck/Market"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data if isinstance(data, list) else (data.get("templates") or [])
        out = []
        for it in items:
            if not isinstance(it, dict) or not it.get("steps"):
                continue
            out.append({
                "id": "remote::" + str(it.get("name", "")),
                "name": it.get("name", "远程模板"),
                "description": it.get("description", ""),
                "author": it.get("author", "社区"),
                "version": str(it.get("version", "1.0.0")),
                "category": it.get("category", "社区"),
                "tags": it.get("tags", []) if isinstance(it.get("tags"), list) else [],
                "source": "remote",
                "visibility": "published",
                "steps": it.get("steps", []),
                "edges": it.get("edges", []),
                "installed": False,
                "installed_version": "",
                "has_update": False,
                "rating_avg": 0,
                "rating_count": 0,
                "remote_url": url,
            })
        return out
    except Exception:
        return []


def install_listing(item: Dict[str, Any]) -> Dict[str, Any]:
    """从市场 listing 安装到本地（import 语义 + 记 installed_version/source）。"""
    payload = {
        "schema": "dbcheck.workflow",
        "schema_version": 1,
        "name": item.get("name", "市场模板"),
        "description": item.get("description", ""),
        "steps": item.get("steps", []),
        "edges": item.get("edges", []),
    }
    r = import_workflow(payload)
    if not r.get("ok"):
        return r
    wf = r["workflow"]
    parent_id = item["id"] if isinstance(item.get("id"), int) else None
    save_workflow(
        name=wf["name"], steps=wf["steps"], edges=wf["edges"], wf_id=wf["id"],
        description=item.get("description", ""), author=item.get("author", ""),
        version=item.get("version", "1.0.0"), category=item.get("category", ""),
        tags=item.get("tags", []), source="market",
        installed_version=item.get("version", "1.0.0"), parent_id=parent_id)
    return {"ok": True, "workflow": get_workflow(wf["id"])}


def update_installed(wf_id: int, item: Dict[str, Any]) -> Dict[str, Any]:
    """升级已安装的市场模板：用 listing 最新 steps/edges 覆盖，刷新版本。"""
    wf = get_workflow(wf_id)
    if not wf:
        return {"ok": False, "error": "本地工作流不存在"}
    save_workflow(
        name=wf["name"], steps=item.get("steps", wf["steps"]),
        edges=item.get("edges", wf["edges"]), wf_id=wf_id,
        installed_version=item.get("version", wf.get("installed_version", "")))
    return {"ok": True, "workflow": get_workflow(wf_id)}


def rate_workflow(wf_id: int, score: float) -> Dict[str, Any]:
    """对模板评分（1–5），增量更新 rating_avg/rating_count。"""
    wf = get_workflow(wf_id)
    if not wf:
        return {"ok": False, "error": "工作流不存在"}
    try:
        s = max(1.0, min(5.0, float(score)))
    except Exception:
        return {"ok": False, "error": "评分需为 1–5 的数字"}
    cnt = wf["rating_count"] + 1
    avg = (wf["rating_avg"] * wf["rating_count"] + s) / cnt
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            conn.execute(
                "UPDATE workflows SET rating_avg=?, rating_count=? WHERE id=?",
                (round(avg, 2), cnt, wf_id))
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "workflow": get_workflow(wf_id)}


# ─────────────────────────────────────────────────────────────────────────────
# P0-b：安全自治 DBA 闭环状态表（autonomy_runs）
# 与 workflows 同库、独立表，不污染 workflow schema（设计文档 4：闭环状态机）。
# 状态流转对齐既有 SQL 审计：detected→diagnosing→proposing→pending_approval
#   →approved→executing→verifying→resolved/closed；rejected/blocked/failed 分支。
# ─────────────────────────────────────────────────────────────────────────────

def _init_autonomy() -> None:
    """确保 autonomy_runs 表存在（同时复用 workflows 的 _init_db 完成迁移门控）。"""
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomy_runs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id     TEXT NOT NULL,
                    finding_ref     TEXT,
                    state           TEXT NOT NULL DEFAULT 'detected',
                    proposals_json  TEXT,
                    audit_task_ids  TEXT,
                    diagnosis_json  TEXT,
                    executed_json   TEXT,
                    verify_report_json TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    closed_at       TEXT
                )
                """
            )
            # 迁移：驳回留痕列（P0-c 补丁：驳回意见需可回看）。
            # SQLite 不支持 ADD COLUMN IF NOT EXISTS，用 PRAGMA 探测列是否存在。
            _aut_cols = {r[1] for r in conn.execute("PRAGMA table_info(autonomy_runs)").fetchall()}
            for col, ctype in (
                ("reject_comment", "TEXT"),
                ("rejected_by", "TEXT"),
                ("rejected_at", "TEXT"),
            ):
                if col not in _aut_cols:
                    conn.execute(f"ALTER TABLE autonomy_runs ADD COLUMN {col} {ctype}")
            conn.commit()
        finally:
            conn.close()


def _json_loads(v):
    if v is None or v == "":
        return None
    try:
        return json.loads(v)
    except Exception:
        return None


def _autonomy_row(row: sqlite3.Row) -> Dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    return {
        "id": d["id"],
        "instance_id": d["instance_id"],
        "finding_ref": _json_loads(d["finding_ref"]),
        "state": d["state"],
        "proposals": _json_loads(d["proposals_json"]) or [],
        "audit_task_ids": _json_loads(d["audit_task_ids"]) or [],
        "diagnosis": _json_loads(d["diagnosis_json"]),
        "executed": _json_loads(d["executed_json"]),
        "verify_report": _json_loads(d["verify_report_json"]),
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
        "closed_at": d.get("closed_at"),
        "reject_comment": d.get("reject_comment"),
        "rejected_by": d.get("rejected_by"),
        "rejected_at": d.get("rejected_at"),
    }


def create_autonomy_run(instance_id: str, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """新建一条自治闭环 run（初始状态 detected），写入触发它的种子发现。"""
    now = _now()
    _init_autonomy()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            cur = conn.execute(
                "INSERT INTO autonomy_runs "
                "(instance_id, finding_ref, state, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                (instance_id, json.dumps(findings, ensure_ascii=False),
                 "detected", now, now),
            )
            rid = cur.lastrowid
            conn.commit()
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM autonomy_runs WHERE id=?", (rid,)).fetchone()
            return _autonomy_row(row)
        finally:
            conn.close()


def get_autonomy_run(run_id: int) -> Optional[Dict[str, Any]]:
    _init_autonomy()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM autonomy_runs WHERE id=?", (run_id,)).fetchone()
            return _autonomy_row(row) if row else None
        finally:
            conn.close()


def update_autonomy_run(
    run_id: int,
    *,
    state: Optional[str] = None,
    proposals: Optional[List[Any]] = None,
    audit_task_ids: Optional[List[Any]] = None,
    diagnosis: Optional[Any] = None,
    executed: Optional[Any] = None,
    verify_report: Optional[Any] = None,
    finding_ref: Optional[Any] = None,
    closed_at: Optional[str] = None,
    reject_comment: Optional[str] = None,
    rejected_by: Optional[str] = None,
    rejected_at: Optional[str] = None,
    clear_reject: bool = False,
) -> Optional[Dict[str, Any]]:
    """按字段增量更新一条 run；自动刷新 updated_at。

    ``clear_reject=True`` 时显式将驳回留痕三列置 NULL（用于重新打开闭环）。
    """
    setters, params = [], []
    if state is not None:
        setters.append("state=?")
        params.append(state)
    if proposals is not None:
        setters.append("proposals_json=?")
        params.append(json.dumps(proposals, ensure_ascii=False))
    if audit_task_ids is not None:
        setters.append("audit_task_ids=?")
        params.append(json.dumps(audit_task_ids, ensure_ascii=False))
    if diagnosis is not None:
        setters.append("diagnosis_json=?")
        params.append(json.dumps(diagnosis, ensure_ascii=False))
    if executed is not None:
        setters.append("executed_json=?")
        params.append(json.dumps(executed, ensure_ascii=False))
    if verify_report is not None:
        setters.append("verify_report_json=?")
        params.append(json.dumps(verify_report, ensure_ascii=False))
    if finding_ref is not None:
        setters.append("finding_ref=?")
        params.append(json.dumps(finding_ref, ensure_ascii=False))
    if closed_at is not None:
        setters.append("closed_at=?")
        params.append(closed_at)
    if reject_comment is not None:
        setters.append("reject_comment=?")
        params.append(reject_comment)
    if rejected_by is not None:
        setters.append("rejected_by=?")
        params.append(rejected_by)
    if rejected_at is not None:
        setters.append("rejected_at=?")
        params.append(rejected_at)
    if clear_reject:
        setters.append("reject_comment=NULL")
        setters.append("rejected_by=NULL")
        setters.append("rejected_at=NULL")
    if not setters:
        return get_autonomy_run(run_id)
    setters.append("updated_at=?")
    params.append(_now())
    params.append(run_id)
    _init_autonomy()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            conn.execute(
                "UPDATE autonomy_runs SET " + ",".join(setters) + " WHERE id=?",
                params,
            )
            conn.commit()
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM autonomy_runs WHERE id=?", (run_id,)).fetchone()
            return _autonomy_row(row) if row else None
        finally:
            conn.close()


def list_autonomy_runs(status: Optional[str] = None, instance_id: Optional[str] = None,
                      limit: int = 100) -> List[Dict[str, Any]]:
    _init_autonomy()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            sql = "SELECT * FROM autonomy_runs"
            where, params = [], []
            if status:
                where.append("state=?")
                params.append(status)
            if instance_id:
                where.append("instance_id=?")
                params.append(instance_id)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [_autonomy_row(r) for r in rows]
        finally:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# P0-c：安全自治闭环全局熔断开关（kill switch）
# 与 autonomy_runs 同库、独立 kv 表。关闭后 autonomy_run_ep 拒绝新建闭环。
# ─────────────────────────────────────────────────────────────────────────────

def _init_autonomy_config() -> None:
    """确保 autonomy_config kv 表存在。"""
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomy_config (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def get_autonomy_config() -> Dict[str, Any]:
    """返回自治闭环全局配置（当前仅 kill_switch）。"""
    _init_autonomy_config()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            row = conn.execute(
                "SELECT value FROM autonomy_config WHERE key='kill_switch'"
            ).fetchone()
            enabled = (row[0] if row else "0") != "1"
            return {"ok": True, "kill_switch": enabled}  # kill_switch=true 表示允许自治
        finally:
            conn.close()


def set_autonomy_config(kill_switch: bool) -> Dict[str, Any]:
    """设置自治闭环开关：kill_switch=true 允许自治，false 熔断（拒绝新建）。"""
    _init_autonomy_config()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            conn.execute(
                "INSERT INTO autonomy_config(key, value) VALUES('kill_switch', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 ("0" if kill_switch else "1",),
            )
            conn.commit()
            return {"ok": True, "kill_switch": kill_switch}
        finally:
            conn.close()
