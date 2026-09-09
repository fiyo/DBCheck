# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""Workflow 任务持久化（独立导航「工作流任务」）。

每个任务绑定一个已编排的工作流（``workflow_id``）与一个目标数据源（``instance_id``），
可配置 ``cron`` 周期运行。任务有独立生命周期：``stopped`` / ``running`` / ``error``。
执行产物（每次运行的发现 / 方案 / 输出）落库到 ``workflow_run_store``，本模块
只管任务元数据与状态机。

存储路径一律以 ``modules.core.paths.DATA_DIR`` 为准，与诊断历史库同目录。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from modules.core import paths

_DB_PATH = str(paths.DATA_DIR / "intelligence_workflow_tasks.db")
_LOCK = threading.Lock()
_MIGRATED = False

STATUS_STOPPED = "stopped"
STATUS_RUNNING = "running"
STATUS_ERROR = "error"


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
                CREATE TABLE IF NOT EXISTS workflow_tasks (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL,
                    workflow_id     INTEGER NOT NULL,
                    instance_id     TEXT NOT NULL,
                    goal            TEXT NOT NULL DEFAULT '',
                    cron            TEXT NOT NULL DEFAULT '',
                    status          TEXT NOT NULL DEFAULT 'stopped',
                    last_run_at     TEXT,
                    last_error      TEXT,
                    last_duration_ms INTEGER,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                )
                """
            )
            conn.commit()
            conn.close()
        finally:
            _MIGRATED = True


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "workflow_id": row["workflow_id"],
        "instance_id": row["instance_id"],
        "goal": row["goal"] or "",
        "cron": row["cron"] or "",
        "status": row["status"] or STATUS_STOPPED,
        "last_run_at": row["last_run_at"],
        "last_error": row["last_error"],
        "last_duration_ms": row["last_duration_ms"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_tasks() -> List[Dict[str, Any]]:
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM workflow_tasks ORDER BY updated_at DESC, id DESC"
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM workflow_tasks WHERE id=?", (task_id,)
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()


def save_task(
    name: str,
    workflow_id: int,
    instance_id: str,
    goal: str = "",
    cron: str = "",
    task_id: Optional[int] = None,
) -> Dict[str, Any]:
    """保存任务。``task_id`` 给定则幂等更新，否则新增。返回完整记录。"""
    if not name or not name.strip():
        raise ValueError("任务名称不能为空")
    if not workflow_id:
        raise ValueError("必须绑定一个工作流")
    if not instance_id:
        raise ValueError("必须选择一个数据源")
    name = name.strip()
    goal = goal or ""
    cron = cron or ""
    now = _now()
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            if task_id:
                cur = conn.execute(
                    "UPDATE workflow_tasks SET name=?, workflow_id=?, instance_id=?, "
                    "goal=?, cron=?, updated_at=? WHERE id=?",
                    (name, workflow_id, instance_id, goal, cron, now, task_id),
                )
                if cur.rowcount == 0:
                    task_id = None
            if not task_id:
                cur = conn.execute(
                    "INSERT INTO workflow_tasks "
                    "(name, workflow_id, instance_id, goal, cron, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (name, workflow_id, instance_id, goal, cron,
                     STATUS_STOPPED, now, now),
                )
                task_id = cur.lastrowid
            conn.commit()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM workflow_tasks WHERE id=?", (task_id,)
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()


def delete_task(task_id: int) -> bool:
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            cur = conn.execute("DELETE FROM workflow_tasks WHERE id=?", (task_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def update_status(
    task_id: int,
    status: str,
    last_error: Optional[str] = None,
    last_run_at: Optional[str] = None,
    last_duration_ms: Optional[int] = None,
) -> None:
    """更新任务运行状态（供 task_runner 在后台线程结束时回写）。"""
    _init_db()
    with _LOCK:
        conn = sqlite3.connect(_DB_PATH)
        try:
            sets = ["status=?", "updated_at=?"]
            params: List[Any] = [status, _now()]
            if last_error is not None:
                sets.append("last_error=?")
                params.append(last_error)
            if last_run_at is not None:
                sets.append("last_run_at=?")
                params.append(last_run_at)
            if last_duration_ms is not None:
                sets.append("last_duration_ms=?")
                params.append(last_duration_ms)
            params.append(task_id)
            conn.execute(
                "UPDATE workflow_tasks SET %s WHERE id=?" % ", ".join(sets), params
            )
            conn.commit()
        finally:
            conn.close()
