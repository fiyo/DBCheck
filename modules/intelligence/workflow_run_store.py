# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""Workflow 任务运行结果持久化（每次运行的产物落库，供卡片点开回看）。

与 ``workflow_task_store`` 配合：任务（task）记录「配置与状态」，运行（run）
记录「某一次执行的产物」。一次任务可被多次运行（手动或 cron 触发），每次产生一条 run。

存储路径一律以 ``modules.core.paths.DATA_DIR`` 为准。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from modules.core import paths

_DB_PATH = str(paths.DATA_DIR / "intelligence_workflow_runs.db")
_LOCK = threading.Lock()
_MIGRATED = False

RUN_RUNNING = "running"
RUN_DONE = "done"
RUN_ERROR = "error"
RUN_CANCELLED = "cancelled"


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
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id         INTEGER NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'running',
                    started_at      TEXT NOT NULL,
                    finished_at     TEXT,
                    duration_ms     INTEGER,
                    error           TEXT,
                    findings_count  INTEGER,
                    plan_count      INTEGER,
                    summary         TEXT,
                    report_path     TEXT,
                    outputs_json    TEXT,
                    trigger         TEXT NOT NULL DEFAULT 'manual'
                )
                """
            )
            conn.commit()
            conn.close()
        finally:
            _MIGRATED = True


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    outputs = row["outputs_json"]
    try:
        outputs = json.loads(outputs) if outputs else []
    except (ValueError, TypeError):
        outputs = []
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "status": row["status"] or RUN_RUNNING,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": row["duration_ms"],
        "error": row["error"],
        "findings_count": row["findings_count"],
        "plan_count": row["plan_count"],
        "summary": row["summary"] or "",
        "report_path": row["report_path"],
        "outputs": outputs,
        "trigger": row["trigger"] or "manual",
    }


def add_run(task_id: int, trigger: str = "manual") -> Dict[str, Any]:
    """登记一次运行开始，返回带 ``id`` 的记录（供后续 update_run 引用）。"""
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            cur = conn.execute(
                "INSERT INTO workflow_runs "
                "(task_id, status, started_at, trigger) VALUES (?,?,?,?)",
                (task_id, RUN_RUNNING, _now(), trigger),
            )
            conn.commit()
            rid = cur.lastrowid
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (rid,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()


def update_run(
    run_id: int,
    status: str,
    finished_at: Optional[str] = None,
    duration_ms: Optional[int] = None,
    error: Optional[str] = None,
    findings_count: Optional[int] = None,
    plan_count: Optional[int] = None,
    summary: Optional[str] = None,
    report_path: Optional[str] = None,
    outputs: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """回写一次运行的结果（供 task_runner 在后台线程结束时调用）。"""
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            sets = ["status=?"]
            params: List[Any] = [status]
            if finished_at is not None:
                sets.append("finished_at=?")
                params.append(finished_at)
            else:
                sets.append("finished_at=?")
                params.append(_now())
            if duration_ms is not None:
                sets.append("duration_ms=?")
                params.append(duration_ms)
            if error is not None:
                sets.append("error=?")
                params.append(error)
            if findings_count is not None:
                sets.append("findings_count=?")
                params.append(findings_count)
            if plan_count is not None:
                sets.append("plan_count=?")
                params.append(plan_count)
            if summary is not None:
                sets.append("summary=?")
                params.append(summary)
            if report_path is not None:
                sets.append("report_path=?")
                params.append(report_path)
            if outputs is not None:
                sets.append("outputs_json=?")
                params.append(json.dumps(outputs, ensure_ascii=False))
            params.append(run_id)
            conn.execute(
                "UPDATE workflow_runs SET %s WHERE id=?" % ", ".join(sets), params
            )
            conn.commit()
        finally:
            conn.close()


def list_runs(task_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM workflow_runs WHERE task_id=? "
                "ORDER BY id DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()


def get_last_run(task_id: int) -> Optional[Dict[str, Any]]:
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()


def get_run(run_id: int) -> Optional[Dict[str, Any]]:
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id=?", (run_id,)
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()
