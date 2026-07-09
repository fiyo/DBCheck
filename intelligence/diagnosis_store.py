# -*- coding: utf-8 -*-
"""协同诊断 · 诊断历史存储。

每一次协同诊断的完整结果都会落库（本仓库内 SQLite），
支持按数据源筛选、列表浏览、查看完整结果，并可从历史记录一键回填工单。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_BASE, "data", "intelligence_diagnoses.db")

_LOCK = threading.Lock()

_SEV_ORDER = {"critical": 3, "warning": 2, "info": 1}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _init_db():
    with _LOCK, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS diagnoses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                diag_no TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                instance_id TEXT,
                instance_name TEXT,
                db_type TEXT,
                goal TEXT,
                severity TEXT,
                finding_count INTEGER DEFAULT 0,
                plan_count INTEGER DEFAULT 0,
                result_json TEXT
            )
        """)
        c.commit()


def _next_no() -> str:
    d = datetime.now().strftime("%Y%m%d")
    with _LOCK, _conn() as c:
        cur = c.execute(
            "SELECT COUNT(*) FROM diagnoses WHERE diag_no LIKE ?",
            (f"DC-D-{d}-%",),
        )
        n = cur.fetchone()[0]
    return f"DC-D-{d}-{n + 1:03d}"


def _top_severity(findings: List[Dict[str, Any]]) -> str:
    sev = "info"
    for f in findings or []:
        s = f.get("severity") if isinstance(f, dict) else None
        if s in _SEV_ORDER and _SEV_ORDER[s] > _SEV_ORDER.get(sev, 0):
            sev = s
    return sev


def save_diagnosis(instance_id: str, instance_name: str, goal: str,
                   result: Dict[str, Any]) -> Dict[str, Any]:
    """把一次协同诊断结果落库，返回带 id / diag_no 的摘要。"""
    _init_db()
    result = result or {}
    findings = result.get("findings") or []
    plan = result.get("plan") or []
    meta = result.get("target_meta") or {}
    db_type = meta.get("db_type") or result.get("db_type") or ""
    if not instance_name:
        instance_name = meta.get("instance_name") or ""
    severity = _top_severity(findings)
    diag_no = _next_no()
    now = _now()
    with _LOCK, _conn() as c:
        cur = c.execute(
            """INSERT INTO diagnoses
               (diag_no, created_at, instance_id, instance_name, db_type, goal,
                severity, finding_count, plan_count, result_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                diag_no, now, instance_id, instance_name, db_type,
                goal or result.get("goal") or "", severity,
                len(findings), len(plan), _json(result),
            ),
        )
        did = cur.lastrowid
    return get_diagnosis(did) or {}


def list_diagnoses(instance_id: str = None, limit: int = 200) -> List[Dict[str, Any]]:
    """列表（摘要，不含完整 result_json）。"""
    _init_db()
    sql = ("SELECT id, diag_no, created_at, instance_id, instance_name, db_type, "
           "goal, severity, finding_count, plan_count FROM diagnoses WHERE 1=1")
    args: List[Any] = []
    if instance_id:
        sql += " AND instance_id=?"
        args.append(instance_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit or 200))
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def get_diagnosis(did: int) -> Optional[Dict[str, Any]]:
    """单条完整记录（含解析后的 result）。"""
    _init_db()
    with _conn() as c:
        row = c.execute("SELECT * FROM diagnoses WHERE id=?", (did,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["result"] = _parse(d.pop("result_json", "") or "") or {}
    return d


def delete_diagnosis(did: int) -> bool:
    _init_db()
    with _LOCK, _conn() as c:
        cur = c.execute("DELETE FROM diagnoses WHERE id=?", (did,))
    return cur.rowcount > 0


def _json(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _parse(s: str):
    try:
        return json.loads(s) if s else None
    except (ValueError, TypeError):
        return None
