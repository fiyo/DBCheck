# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""SQL 审核受控执行器（MVP3）。

设计原则（设计文档 §10 安全与合规）：
  - 默认只读 / dry-run；真实执行需任务 exec_enabled=1 且连接目标实例。
  - 真实执行：单任务内事务（DML），影响行数硬上限兜底，执行前对 UPDATE/DELETE
    做 SELECT 原行快照并落备份表（bak_sa_<task>_<item>_<ts>）以备回滚。
  - DDL 在多数引擎下隐式提交、不可回滚，仅生成建议级反向 DDL，不纳入事务。
  - 超时：PostgreSQL 用 SET LOCAL statement_timeout；MySQL 设 MAX_EXECUTION_TIME
    （仅作用于 SELECT/EXPLAIN，DML 无硬超时，依赖影响行数兜底）；其余引擎依赖连接超时。

所有执行/回滚动作均落库（sql_audit_executions / sql_audit_rollbacks），append-only 留痕。
"""
import re
import time
from datetime import datetime, timezone

from . import models
from . import plan_analyzer
from . import rollback as rollback_mod

MAX_AFFECTED_ROWS_DEFAULT = 100000
EXEC_TIMEOUT_DEFAULT = 30  # 秒


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_timeout(conn, db_type: str, timeout: int) -> None:
    """按库型设置单语句超时（best-effort，失败静默忽略）。"""
    dt = plan_analyzer.normalize_db_type(db_type)
    try:
        cur = conn.cursor()
        if dt == "postgresql":
            cur.execute(f"SET LOCAL statement_timeout = {int(timeout * 1000)}")
        elif dt == "mysql":
            # 仅对 SELECT/EXPLAIN 生效；DML 无硬超时，靠 max_affected_rows 兜底
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME={int(timeout * 1000)}")
        cur.close()
    except Exception:  # noqa: BLE001
        pass


def _extract_where(stmt: str):
    """提取语句中最后一个 WHERE 之后的条件子句（best-effort）。"""
    matches = list(re.finditer(r"\bWHERE\b", stmt, re.I))
    if not matches:
        return None
    return stmt[matches[-1].end():].strip().rstrip(";").strip()


def _take_snapshot(conn, task_id: int, db_type: str, it: dict):
    """对 UPDATE/DELETE 执行前 SELECT 原行快照并落备份表。返回 snapshot dict。"""
    from .parser import extract_tables
    tables = it.get("tables") or []
    if len(tables) != 1:
        return {"table": tables[0] if tables else None, "columns": [], "rows": [],
                "where": None, "backup_table": None,
                "note": "多表语句不支持自动快照，回滚将依赖手动备份"}
    table = tables[0]
    where = _extract_where(it["sql_text"])
    if not where:
        return {"table": table, "columns": [], "rows": [], "where": None, "backup_table": None,
                "note": "无 WHERE 条件，影响全表，未生成自动快照（回滚需依赖整表备份）"}
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT * FROM {table} WHERE {where}")
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        return {"table": table, "columns": [], "rows": [], "where": where, "backup_table": None,
                "note": f"快照失败: {e}"}
    # 落备份表（DDL，与主 DML 事务独立；即使主事务回滚，备份仍保留）
    backup_table = None
    try:
        backup_table = f"bak_sa_{task_id}_{it['id']}_{int(time.time())}"
        bcur = conn.cursor()
        bcur.execute(f"CREATE TABLE {backup_table} AS SELECT * FROM {table} WHERE {where}")
        bcur.close()
    except Exception:  # noqa: BLE001
        backup_table = None
    return {"table": table, "columns": cols, "rows": rows, "where": where,
            "backup_table": backup_table}


def dry_run(task: dict, instance: dict = None) -> dict:
    """dry-run：只读重跑执行计划（EXPLAIN），不修改任何数据。"""
    items = task.get("items") or []
    results = []
    conn = None
    if instance:
        try:
            conn = plan_analyzer.connect_instance(instance)
        except Exception as e:  # noqa: BLE001
            return {"mode": "dry_run", "connected": False, "error": str(e), "items": []}
    try:
        for it in items:
            started = _now_iso()
            plan = None
            note = ""
            if it.get("plan_applicable") and conn:
                analyzer = plan_analyzer.get_analyzer(task["db_type"])
                if analyzer:
                    try:
                        plan = analyzer.analyze(conn, it["sql_text"], it)
                    except Exception as e:  # noqa: BLE001
                        plan = {"engine": plan_analyzer.normalize_db_type(task["db_type"]),
                                "applicable": False, "error": f"执行计划预览失败: {e}"}
                else:
                    note = f"暂不支持 {task['db_type']} 的执行计划预览"
            finished = _now_iso()
            models.insert_execution(task["id"], it["id"], "dry_run", it["sql_text"],
                                    None, "success", started, finished, None)
            results.append({"seq": it["seq"], "op_type": it["op_type"],
                            "plan": plan, "note": note})
    finally:
        if conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return {"mode": "dry_run", "connected": bool(instance), "items": results}


def real_execute(task: dict, instance: dict, max_affected_rows: int, timeout: int) -> dict:
    """真实执行：受控事务 + 影响行数兜底 + 执行前快照 + 回滚方案生成。

    返回 {"mode","status","executions","rollbacks","started_at","finished_at"}。
    任何单条失败或超影响行数 → 回滚整个任务事务并抛出 RuntimeError。
    """
    if not instance:
        raise ValueError("真实执行必须指定目标实例")
    risk = (task.get("risk_level") or "low").lower()
    if risk == "block":
        raise ValueError("任务含阻断级风险（如 DROP TABLE / TRUNCATE），出于安全禁止执行")
    # 审批闸门：阻断级禁止；待审批 / 高风险任务须先审批通过（approved_by 非空）。
    # 已审批通过（approved_by 非空）即视为已授权真实执行——写操作工单（exec_enabled=0）
    # 经 WriteGate 提交、审批通过后同样可受控执行；未审批且未开启真实执行的任务禁止。
    if not task.get("approved_by") and not task.get("exec_enabled"):
        raise ValueError("该任务未开启真实执行且未经审批，出于安全拒绝执行")
    if risk == "high" and not task.get("approved_by"):
        raise ValueError("高风险任务须先经审批通过（approved_by 为空），请走审批流程后再执行")
    items = task.get("items") or []
    conn = plan_analyzer.connect_instance(instance)
    started_all = _now_iso()
    exec_results = []
    rollback_results = []
    try:
        _set_timeout(conn, task["db_type"], timeout)
        for it in items:
            op = it["op_type"]
            sql_type = it["sql_type"]
            # DQL/OTHER（SELECT/SHOW/EXPLAIN 等）不执行，只读已分析
            if sql_type in ("DQL", "OTHER"):
                continue
            started = _now_iso()
            snapshot = None
            backup_ref = None
            if op in ("UPDATE", "DELETE"):
                snapshot = _take_snapshot(conn, task["id"], task["db_type"], it)
                if snapshot and snapshot.get("backup_table"):
                    backup_ref = snapshot["backup_table"]
            try:
                cur = conn.cursor()
                cur.execute(it["sql_text"])
                affected = cur.rowcount
                finished = _now_iso()
                if affected is not None and affected > max_affected_rows:
                    conn.rollback()
                    models.insert_execution(
                        task["id"], it["id"], "real", it["sql_text"], affected, "failed",
                        started, finished,
                        f"影响行数 {affected} 超过上限 {max_affected_rows}，已回滚",
                    )
                    raise RuntimeError(
                        f"语句 #{it['seq']} 影响行数 {affected} 超过上限 "
                        f"{max_affected_rows}，已回滚整个任务事务"
                    )
                rb = rollback_mod.generate_rollback_plan(it["sql_text"], task["db_type"], snapshot)
                models.insert_execution(
                    task["id"], it["id"], "real", it["sql_text"], affected,
                    "success", started, finished, None,
                )
                if rb.get("rollback_sql") or rb.get("note"):
                    models.insert_rollback(
                        task["id"], it["id"], rb.get("rollback_sql"), backup_ref,
                        bool(rb.get("auto_rollback")), finished, rb.get("note"),
                    )
                exec_results.append({
                    "seq": it["seq"], "op_type": op, "affected_rows": affected,
                    "status": "success", "rollback_sql": rb.get("rollback_sql"),
                    "auto_rollback": bool(rb.get("auto_rollback")), "note": rb.get("note"),
                })
                rollback_results.append(rb)
            except Exception as e:  # noqa: BLE001
                finished = _now_iso()
                models.insert_execution(
                    task["id"], it["id"], "real", it["sql_text"], None, "failed",
                    started, finished, str(e),
                )
                conn.rollback()
                raise
        conn.commit()
        finished_all = _now_iso()
        models.update_task_status(task["id"], "executed", executed_at=finished_all)
        return {"mode": "real", "status": "success", "executions": exec_results,
                "rollbacks": rollback_results, "started_at": started_all,
                "finished_at": finished_all}
    except Exception as e:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        # 真实执行失败时状态保持 approved，允许重试；只更新 executed_at 作为失败时间戳
        # 便于审计定位，但不在 status 上标记 executed，避免任务被"锁死"
        try:
            models.update_task_status(task["id"], "approved", executed_at=_now_iso())
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def execute_rollback(task: dict, instance: dict, operator: str) -> dict:
    """执行已生成的自动回滚方案（MVP3.5 一键回滚，P1 ⑤）。

    仅执行 auto_rollback=True 且有 rollback_sql 的回滚项（即执行前已对 UPDATE/DELETE
    做快照落备份表 / 生成反向 DML 的项）；仅建议级（auto_rollback=False）不在此自动执行。
    整体置于单事务，任一条失败即整任务回滚并抛出 RuntimeError。
    所有动作 append-only 落库（sql_audit_executions, mode='rollback'）。
    """
    if not instance:
        raise ValueError("回滚执行必须指定目标实例")
    if task.get("status") != "executed":
        raise ValueError("仅已执行的任务可回滚")
    rollbacks = models.get_rollbacks(task["id"])
    targets = [r for r in rollbacks if r.get("auto_rollback") and r.get("rollback_sql")]
    if not targets:
        raise ValueError("该任务无可自动回滚的项（可能仅生成了建议级回滚）")
    conn = plan_analyzer.connect_instance(instance)
    started_all = _now_iso()
    results = []
    try:
        _set_timeout(conn, task["db_type"], EXEC_TIMEOUT_DEFAULT)
        for rb in targets:
            started = _now_iso()
            try:
                cur = conn.cursor()
                cur.execute(rb["rollback_sql"])
                affected = cur.rowcount
                finished = _now_iso()
                models.insert_execution(
                    task["id"], rb.get("item_id"), "rollback", rb["rollback_sql"],
                    affected, "success", started, finished, None,
                )
                results.append({"item_id": rb.get("item_id"), "status": "success",
                                "affected_rows": affected})
            except Exception as e:  # noqa: BLE001
                finished = _now_iso()
                models.insert_execution(
                    task["id"], rb.get("item_id"), "rollback", rb["rollback_sql"],
                    None, "failed", started, finished, str(e),
                )
                raise RuntimeError(f"回滚项 #{rb.get('item_id')} 执行失败: {e}") from e
        conn.commit()
        return {"ok": True, "mode": "rollback", "results": results,
                "started_at": started_all, "finished_at": _now_iso()}
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
