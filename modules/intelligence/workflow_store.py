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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
        "steps": json.loads(row["steps"] or "[]"),
        "edges": json.loads(row["edges"] or "[]"),
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
                  wf_id: Optional[int] = None) -> Dict[str, Any]:
    """保存工作流。``wf_id`` 给定则幂等更新，否则新增。返回完整记录。"""
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
            if wf_id:
                cur = conn.execute(
                    "UPDATE workflows SET name=?, steps=?, edges=?, updated_at=? WHERE id=?",
                    (name.strip(), json.dumps(steps, ensure_ascii=False),
                     json.dumps(edges, ensure_ascii=False), now, wf_id),
                )
                if cur.rowcount == 0:
                    wf_id = None  # 不存在则退化为新增
            if not wf_id:
                cur = conn.execute(
                    "INSERT INTO workflows (name, steps, edges, created_at, updated_at) "
                    "VALUES (?,?,?,?,?)",
                    (name.strip(), json.dumps(steps, ensure_ascii=False),
                     json.dumps(edges, ensure_ascii=False), now, now),
                )
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
