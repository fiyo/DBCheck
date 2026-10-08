# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""巡检发现出口（P0 安全自治闭环 · 感知层）。

把巡检历史（inspection_history.auto_analyze）映射为统一的「发现」事件流，供
智能诊断中心的 hub.run_autonomous 作为种子消费。

设计要点：
* 只读、零新采集——直接复用巡检已落库的 auto_analyze 数据（与
  modules.intelligence.hub 的 _auto_analyze_to_risks 同源）。
* 返回 Finding 形状的纯 dict（字段与 modules.intelligence.context.Finding 一致），
  不引入对 intelligence 模块的运行时依赖，保持巡检侧解耦；由 hub 侧负责转成
  真正的 Finding 对象注入共享上下文。
* severity_threshold 控制下钻粒度，过滤低噪发现，避免淹没自治决策。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from modules.core import paths

# 与 hub._LEGACY_PREFIX 保持一致：旧版巡检记录用 hash 形式 instance_id
_LEGACY_PREFIX = {
    "mysql": "mysql", "pg": "pg", "postgresql": "pg", "oracle": "oracle",
    "oracle_jdbc": "oracle", "dm": "dm", "sqlserver": "sqlserver",
    "tidb": "tidb", "ivorysql": "ivorysql", "kingbase": "kingbase",
    "halodb": "halodb", "yashandb": "yashandb", "gbase": "gbase",
}

# 巡检结论里的级别字段取值（col4=处理优先级 / col2=风险等级）
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def _legacy_instance_id(db_type: str, host: str, port: int) -> str:
    """计算旧版 hash 形式的 instance_id，用于兼容早期巡检记录。"""
    prefix = _LEGACY_PREFIX.get(db_type, db_type)
    raw = f"{prefix}-{host}-{port}".encode()
    return hashlib.md5(raw).hexdigest()[:12]


def _get_instance_manager_db() -> Optional[str]:
    """获取 Pro 巡检历史库的绝对路径（与 hub 实现保持一致）。"""
    try:
        from modules.pro.instance_manager import get_instance_manager

        im = get_instance_manager()
        db_file = getattr(im, "db_file", None)
        if db_file and os.path.exists(db_file):
            return db_file
    except Exception:
        pass
    try:
        fallback = str(paths.PRO_DATA_DIR / "pro_history.db")
        if os.path.exists(fallback):
            return fallback
    except Exception:
        pass
    return None


def _get_instance(instance_id: str) -> Optional[Dict[str, Any]]:
    """从 Pro 实例管理器读取单个数据源详情（兼容旧版 hash id 计算）。"""
    try:
        from modules.pro.instance_manager import get_instance_manager

        im = get_instance_manager()
        return im.get_instance_decrypted(instance_id)
    except Exception:
        return None


def _normalize_severity(level: str) -> str:
    """把巡检级别文本归一为 Finding.severity（info/warning/critical）。"""
    s = (level or "").lower()
    if any(k in s for k in ("高", "critical", "high", "error", "严重")):
        return "critical"
    if any(k in s for k in ("中", "warning", "warn", "medium")):
        return "warning"
    if any(k in s for k in ("低", "建议", "info", "low", "note", "提示")):
        return "info"
    return "warning"


def _fetch_auto_analyze(instance_id: str) -> Optional[List[Dict[str, Any]]]:
    """拉取目标数据源最近一次巡检的 auto_analyze 列表。"""
    inst = _get_instance(instance_id)
    candidates = [instance_id]
    if inst:
        host = inst.get("host", "")
        port = int(inst.get("port", 0) or 0)
        db_type = inst.get("db_type", "")
        if host and port and db_type:
            candidates.append(_legacy_instance_id(db_type, host, port))

    db_path = _get_instance_manager_db()
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        placeholders = ",".join("?" * len(candidates))
        cur.execute(
            f"SELECT auto_analyze FROM inspection_history "
            f"WHERE instance_id IN ({placeholders}) ORDER BY inspect_time DESC LIMIT 1",
            tuple(candidates),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        aa = row["auto_analyze"]
        if isinstance(aa, str):
            try:
                aa = json.loads(aa)
            except Exception:
                aa = []
        return aa if isinstance(aa, list) else []
    except Exception:
        return None


def emit_findings(instance_id: str, severity_threshold: str = "warning") -> List[Dict[str, Any]]:
    """把目标数据源最近一次巡检结果映射为「发现」事件流（Finding 形状 dict）。

    Args:
        instance_id: 数据源 id（兼容旧版 hash id）。
        severity_threshold: 下钻粒度，只返回不低于该级别的发现。
            默认 "warning"（即 warning + critical）；"info" 表示不过滤。

    Returns:
        Finding 形状的 dict 列表，字段与 context.Finding 对齐
        （source/category/severity/title/detail/suggestion/tags）。
        失败或无记录时返回 []，不影响上游编排。
    """
    threshold_rank = _SEVERITY_RANK.get(severity_threshold, 1)
    aa = _fetch_auto_analyze(instance_id)
    if not aa:
        return []

    out: List[Dict[str, Any]] = []
    for item in aa:
        if not isinstance(item, dict):
            continue
        # 优先处理优先级(col4)，退化风险等级(col2)
        level = (item.get("col4") or item.get("col2") or "")
        severity = _normalize_severity(level)
        if _SEVERITY_RANK.get(severity, 1) < threshold_rank:
            continue
        title = item.get("col1") or ""
        detail = item.get("detail") or item.get("col3") or ""
        suggestion = item.get("fix") or item.get("fix_sql") or ""
        if not (title or detail or suggestion):
            continue
        out.append({
            "source": "inspection",
            "category": "risk",
            "severity": severity,
            "title": title,
            "detail": detail,
            "suggestion": suggestion,
            "tags": ["inspection", (item.get("col5") or "DBA").lower()],
        })
    return out
