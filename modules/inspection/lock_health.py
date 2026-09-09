# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""锁等待 / 阻塞链分析引擎（lock_tree）。

通过只读系统视图抓取当前持锁/等待会话，构建「等待—阻塞」树，定位
持锁源头（blocking root）与受影响的等待者。供 MCP 工具 ``dbcheck.lock_tree``
与多 Agent 的锁分析专员共用（与 ``slow_query.py`` / ``index_health.py`` 同构）。

所有查询强制只读（走 ``db_executor.execute_instance_query``，已自带只读校验），
不持有锁、不改数据。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


# ── 各库「当前锁会话 + 阻塞关系」查询 ─────────────────────────────────────────
# 返回统一列：pid / user / host / db / state / sql / blocking_pid / wait_age
_SQL = {
    # MySQL / MariaDB：innodb_trx 提供事务，sys.innodb_lock_waits 提供等待边
    "mysql": [
        ("sessions",
         "SELECT trx_mysql_thread_id AS pid, trx_state AS state, "
         "trx_query AS sql, trx_started AS started "
         "FROM information_schema.innodb_trx"),
        ("waits",
         "SELECT waiting_pid, blocking_pid, wait_age "
         "FROM sys.innodb_lock_waits"),
    ],
    "mariadb": [
        ("sessions",
         "SELECT trx_mysql_thread_id AS pid, trx_state AS state, "
         "trx_query AS sql, trx_started AS started "
         "FROM information_schema.innodb_trx"),
        ("waits",
         "SELECT waiting_trx_id AS waiting_pid, blocking_trx_id AS blocking_pid, "
         "'unknown' AS wait_age FROM information_schema.innodb_lock_waits"),
    ],
    # PostgreSQL：pg_stat_activity + pg_blocking_pids 直接给出阻塞者数组
    "pg": [
        ("sessions",
         "SELECT pid, usename AS user, client_addr AS host, datname AS db, "
         "state, query AS sql, "
         "COALESCE(array_to_string(pg_blocking_pids(pid), ','), '') AS blocking_pid, "
         "EXTRACT(EPOCH FROM (now() - xact_start))::int AS wait_age "
         "FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
         "AND (state IS NOT NULL OR wait_event_type IS NOT NULL)"),
    ],
    # Oracle / DM（v$session 兼容）
    "oracle": [
        ("sessions",
         "SELECT s.sid || ',' || s.serial# AS pid, s.username AS user, "
         "s.machine AS host, s.status AS state, s.event, "
         "s.sql_id, s.seconds_in_wait AS wait_age, "
         "NVL(TO_CHAR(s.blocking_session), '0') AS blocking_pid "
         "FROM v$session s WHERE s.username IS NOT NULL"),
    ],
    # SQL Server：dm_exec_requests 的 blocking_session_id
    "sqlserver": [
        ("sessions",
         "SELECT CAST(r.session_id AS VARCHAR) AS pid, s.login_name AS user, "
         "s.host_name AS host, DB_NAME(r.database_id) AS db, r.status AS state, "
         "r.wait_type, t.text AS sql, "
         "CAST(r.blocking_session_id AS VARCHAR) AS blocking_pid, "
         "r.wait_time / 1000 AS wait_age "
         "FROM sys.dm_exec_requests r "
         "JOIN sys.dm_exec_sessions s ON r.session_id = s.session_id "
         "CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t "
         "WHERE r.session_id <> @@SPID"),
    ],
}


def _run(db_info: Dict[str, Any], sql: str, limit: int = 500) -> Dict[str, Any]:
    """包一层 db_executor 只读执行。失败返回 ok=False 不抛。"""
    try:
        from modules.intelligence.db_executor import execute_instance_query
        return execute_instance_query(db_info, sql, limit=limit)
    except Exception as e:  # pragma: no cover - 仅在模块缺失时
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _normalize_blocking_pid(raw: Any) -> Optional[int]:
    """把 blocking_pid 规范成 int（0 / '' / None 视为无阻塞）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "0", "None", "NULL"):
        return None
    # PostgreSQL 返回逗号分隔数组
    s = s.split(",")[0].strip()
    try:
        v = int(s)
        return None if v == 0 else v
    except Exception:
        return None


def _build_tree(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """由 (pid, blocking_pid) 列表构建等待树：每个节点列出其等待者。"""
    by_pid: Dict[int, Dict[str, Any]] = {}
    children: Dict[int, List[int]] = {}
    for s in sessions:
        try:
            pid = int(s.get("pid"))
        except Exception:
            continue
        s2 = dict(s)
        s2["pid"] = pid
        by_pid[pid] = s2
        bp = _normalize_blocking_pid(s.get("blocking_pid"))
        s2["blocking_pid"] = bp
        if bp:
            children.setdefault(bp, []).append(pid)

    def make(node_pid: int, depth: int = 0) -> Dict[str, Any]:
        node = dict(by_pid.get(node_pid, {"pid": node_pid}))
        node["depth"] = depth
        kids = children.get(node_pid, [])
        node["waits_for_this"] = [
            make(k, depth + 1) for k in kids if k in by_pid
        ]
        return node

    # 根 = 阻塞他人但自身不被阻塞的会话
    roots = [make(pid) for pid in by_pid
             if (by_pid[pid].get("blocking_pid") in (None,)) and pid in children]
    return roots


def get_lock_tree(db_type: str, db_info: Dict[str, Any]) -> Dict[str, Any]:
    """对目标数据源执行锁等待分析，返回结构化等待树。

    参数:
        db_type: 数据库类型（mysql / mariadb / pg / oracle / dm / sqlserver ...）
        db_info: 解密后的实例连接信息（host/port/user/password/db_type ...）

    返回:
        {"ok": True, "db_type": ..., "lock_count": int, "blocking_roots": [...],
         "sessions": [...], "edges": [...], "summary": str}
        {"ok": False, "error": "..."}  （不支持类型或查询失败）
    """
    t = (db_type or "").lower().replace("oracle_full", "oracle")
    # 类型归一：hgdb/kingbase/uxdb/ivorysql 内核是 PG
    if t in ("hgdb", "hgdb_jdbc", "kingbase", "uxdb", "uxdb_jdbc", "ivorysql", "postgresql"):
        t = "pg"
    if t == "mariadb":
        t = "mariadb"
    if t not in _SQL:
        return {"ok": False, "error": f"锁分析暂不支持该数据库类型: {db_type}"}

    queries = _SQL[t]
    sessions: List[Dict[str, Any]] = []
    extra_edges: List[Dict[str, Any]] = []

    for label, sql in queries:
        res = _run(db_info, sql)
        if not res.get("ok"):
            if label == "sessions":
                return {"ok": False, "error": f"锁会话查询失败: {res.get('error')}"}
            # waits 查询失败（如 sys 视图缺失）不致命，继续只用 sessions
            continue
        cols = res.get("columns", [])
        rows = res.get("rows", [])
        idx = {c: i for i, c in enumerate(cols)}
        for row in rows:
            rec = {c: (row[idx[c]] if idx.get(c, -1) >= 0 else None) for c in cols}
            if label == "sessions":
                sessions.append(rec)
            else:  # waits：waiting_pid -> blocking_pid 边
                wp = _normalize_blocking_pid(rec.get("waiting_pid"))
                bp = _normalize_blocking_pid(rec.get("blocking_pid"))
                if wp and bp:
                    extra_edges.append({"waiting_pid": wp, "blocking_pid": bp})

    # 把 waits 边回填进 sessions（MySQL/MariaDB 路径）
    pid_set = set()
    for s in sessions:
        try:
            pid_set.add(int(s.get("pid")))
        except Exception:
            pass
    for e in extra_edges:
        wp, bp = e["waiting_pid"], e["blocking_pid"]
        for s in sessions:
            if int(s.get("pid", -1)) == wp:
                s["blocking_pid"] = bp
                break

    roots = _build_tree(sessions)
    locking = [s for s in sessions if _normalize_blocking_pid(s.get("blocking_pid"))]
    summary = (
        f"检测到 {len(sessions)} 个活跃会话，其中 {len(locking)} 个处于等待状态，"
        f"发现 {len(roots)} 个阻塞源头。"
    )
    return {
        "ok": True,
        "db_type": db_type,
        "lock_count": len(sessions),
        "blocking_roots": roots,
        "blocking_count": len(roots),
        "waiting_count": len(locking),
        "sessions": sessions,
        "summary": summary,
    }
