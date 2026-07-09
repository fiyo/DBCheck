# -*- coding: utf-8 -*-
"""协同诊断 · 工单执行闭环。

轻量工单模型（本仓库内 SQLite，不依赖外部系统）：
  诊断结果可一键生成工单；工单状态可跟踪（open/in_progress/done/closed/cancelled）；
  执行反馈（状态变更 + 备注）可回写，形成「诊断 → 派单 → 处置 → 反馈」闭环。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_BASE, "data", "intelligence_tickets.db")

STATUS_OPEN = "open"
STATUS_IN_PROGRESS = "in_progress"
STATUS_RESOLVED = "resolved"
STATUS_CLOSED = "closed"
STATUS_CANCELLED = "cancelled"
# 兼容旧值 done（历史工单/旧前端）
STATUS_DONE = "done"
VALID_STATUS = (
    STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_RESOLVED,
    STATUS_CLOSED, STATUS_CANCELLED, STATUS_DONE,
)

_STATUS_LABEL = {
    STATUS_OPEN: "待处理",
    STATUS_IN_PROGRESS: "处理中",
    STATUS_RESOLVED: "已解决",
    STATUS_CLOSED: "已关闭",
    STATUS_CANCELLED: "已取消",
    STATUS_DONE: "已处置",
}

_LOCK = threading.Lock()


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
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_no TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                instance_id TEXT,
                instance_name TEXT,
                goal TEXT,
                severity TEXT,
                findings TEXT,
                plan TEXT,
                plan_validation TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                assignee TEXT,
                note TEXT,
                feedback TEXT
            )
        """)
        c.commit()


def _next_no() -> str:
    d = datetime.now().strftime("%Y%m%d")
    with _LOCK, _conn() as c:
        cur = c.execute(
            "SELECT COUNT(*) AS n FROM tickets WHERE ticket_no LIKE ?",
            (f"DC-T-{d}-%",),
        )
        n = (cur.fetchone() or {}).get("n", 0) if False else (cur.fetchone()[0] if cur else 0)
    return f"DC-T-{d}-{n + 1:03d}"


def create_ticket(instance_id: str, instance_name: str, goal: str,
                  findings: List[Dict[str, Any]], plan: List[Dict[str, Any]],
                  plan_validation: Dict[str, Any] = None,
                  severity: str = "info") -> Dict[str, Any]:
    """从一次诊断结果创建工单。"""
    _init_db()
    ticket_no = _next_no()
    now = _now()
    with _LOCK, _conn() as c:
        cur = c.execute(
            """INSERT INTO tickets
               (ticket_no, created_at, updated_at, instance_id, instance_name, goal,
                severity, findings, plan, plan_validation, status, note, feedback)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticket_no, now, now, instance_id, instance_name, goal, severity,
                _json(findings), _json(plan), _json(plan_validation),
                STATUS_OPEN, "", _json([]),
            ),
        )
        tid = cur.lastrowid
    return get_ticket(tid)


def get_ticket(tid: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    return _row_to_dict(row) if row else None


def get_ticket_by_no(ticket_no: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM tickets WHERE ticket_no=?", (ticket_no,)).fetchone()
    return _row_to_dict(row) if row else None


def list_tickets(instance_id: str = None, status: str = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM tickets WHERE 1=1"
    args: List[Any] = []
    if instance_id:
        sql += " AND instance_id=?"
        args.append(instance_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY id DESC"
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_ticket(tid: int, status: str = None, assignee: str = None,
                  note: str = None) -> Optional[Dict[str, Any]]:
    """更新工单状态 / 负责人 / 备注，并把本次变更写入 feedback（闭环回写）。"""
    if status is not None and status not in VALID_STATUS:
        raise ValueError(f"非法状态：{status}")
    _init_db()
    now = _now()
    with _LOCK, _conn() as c:
        row = c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
        if not row:
            return None
        cur_status = status or row["status"]
        cur_assignee = assignee if assignee is not None else (row["assignee"] or "")
        cur_note = note if note is not None else (row["note"] or "")
        feedback = _parse(row["feedback"]) or []
        if status is not None or note:
            feedback.append({
                "ts": now,
                "time": now,
                "status": cur_status,
                "status_label": _STATUS_LABEL.get(cur_status, cur_status),
                "note": note or "",
            })
        c.execute(
            """UPDATE tickets SET status=?, assignee=?, note=?, feedback=?, updated_at=?
               WHERE id=?""",
            (cur_status, cur_assignee, cur_note, _json(feedback), now, tid),
        )
    return get_ticket(tid)


def _json(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False)


def _parse(s: str):
    import json
    try:
        return json.loads(s) if s else None
    except (ValueError, TypeError):
        return None


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["findings"] = _parse(d.get("findings") or "") or []
    d["plan"] = _parse(d.get("plan") or "") or []
    d["plan_validation"] = _parse(d.get("plan_validation") or "") or {}
    d["feedback"] = _parse(d.get("feedback") or "") or []
    d["status_label"] = _STATUS_LABEL.get(d.get("status"), d.get("status"))
    return d
