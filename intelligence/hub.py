# -*- coding: utf-8 -*-
"""协同诊断中枢。

接收一句目标与一个数据源，组织相关专家能力共享同一上下文，
串行协同产出发现与处置方案。能力之间互不隶属，结论沉淀在共享上下文。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from i18n import t

from .context import Finding, SharedContext
from .planner import plan_sequence
from .registry import registry
from .specialists import register_all

# 旧版巡检记录生成 instance_id 时使用的前缀（兼容历史数据）
_LEGACY_PREFIX = {
    "mysql": "mysql",
    "pg": "pg",
    "postgresql": "pg",
    "oracle": "oracle",
    "oracle_jdbc": "oracle",
    "dm": "dm",
    "sqlserver": "sqlserver",
    "tidb": "tidb",
    "ivorysql": "ivorysql",
    "kingbase": "kingbase",
    "yashandb": "yashandb",
    "gbase": "gbase",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _legacy_instance_id(db_type: str, host: str, port: int) -> str:
    """计算旧版 hash 形式的 instance_id，用于兼容早期巡检记录。"""
    prefix = _LEGACY_PREFIX.get(db_type, db_type)
    raw = f"{prefix}-{host}-{port}".encode()
    return hashlib.md5(raw).hexdigest()[:12]


def _get_instance_manager_db() -> Optional[str]:
    """获取 Pro 巡检历史库的绝对路径。"""
    try:
        from pro.instance_manager import get_instance_manager

        im = get_instance_manager()
        db_file = getattr(im, "db_file", None)
        if db_file and os.path.exists(db_file):
            return db_file
    except Exception:
        pass
    # 防御性回退：若 Pro 模块导入失败，按默认路径查找
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fallback = os.path.join(base, "pro_data", "pro_history.db")
        if os.path.exists(fallback):
            return fallback
    except Exception:
        pass
    return None


def _get_instance(instance_id: str) -> Optional[Dict[str, Any]]:
    """从 Pro 实例管理器读取单个数据源详情。"""
    try:
        from pro.instance_manager import get_instance_manager

        im = get_instance_manager()
        return im.get_instance_decrypted(instance_id)
    except Exception:
        return None


def _fetch_latest_inspection(instance_id: str) -> Optional[Dict[str, Any]]:
    """
    为目标数据源拉取最近一次巡检记录。

    优先匹配 datasource_id；未命中时尝试旧版 hash(instance_prefix-host-port)，
    保证新记录与早期记录都能被协同诊断中枢消费。
    """
    inst = _get_instance(instance_id)
    if inst is None:
        return None

    candidates = [instance_id]
    host = inst.get("host", "")
    port = int(inst.get("port", 0) or 0)
    db_type = inst.get("db_type", "")
    if host and port and db_type:
        candidates.append(_legacy_instance_id(db_type, host, port))

    db_path = _get_instance_manager_db()
    if not db_path or not sqlite3:
        return None

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(candidates))
        cursor.execute(
            f"""
            SELECT auto_analyze, health_score, risk_count, risk_level,
                   inspect_time, instance_name, db_type
            FROM inspection_history
            WHERE instance_id IN ({placeholders})
            ORDER BY inspect_time DESC
            LIMIT 1
            """,
            tuple(candidates),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return dict(row)
    except Exception:
        return None


def _auto_analyze_to_risks(auto_analyze: Any) -> List[Dict[str, Any]]:
    """把巡检自动分析的 col1/col2/col3 结构转成 diagnosis 需要的 risks。"""
    risks: List[Dict[str, Any]] = []
    if not isinstance(auto_analyze, list):
        try:
            auto_analyze = json.loads(auto_analyze) if isinstance(auto_analyze, str) else []
        except Exception:
            auto_analyze = []
    if not isinstance(auto_analyze, list):
        return risks
    for item in auto_analyze:
        if not isinstance(item, dict):
            continue
        title = t(item.get("col1", ""), default=item.get("col1", ""))
        level = t(item.get("col4", "") or item.get("col2", ""), default=item.get("col2", ""))
        fix = t(item.get("col3", ""), default=item.get("col3", ""))
        detail = item.get("detail") or ""
        if title or detail or fix:
            risks.append(
                {
                    "title": title,
                    "detail": detail,
                    "level": level,
                    "fix": fix,
                }
            )
    return risks


def _build_inspection_report(instance_id: str) -> Optional[Dict[str, Any]]:
    """为 intelligence 构造 inspection_report 输入。"""
    row = _fetch_latest_inspection(instance_id)
    if not row:
        return None
    auto_analyze = row.get("auto_analyze")
    risks = _auto_analyze_to_risks(auto_analyze)
    return {
        "risks": risks,
        "health_score": row.get("health_score"),
        "risk_count": row.get("risk_count"),
        "risk_level": row.get("risk_level"),
        "inspect_time": row.get("inspect_time"),
        "instance_name": row.get("instance_name"),
        "db_type": row.get("db_type"),
    }


class DiagnosticHub:
    def __init__(self) -> None:
        register_all()
        self.registry = registry

    def capabilities(self) -> list:
        return [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tags": s.tags,
            }
            for s in self.registry.all()
        ]

    def _prepare(self, goal: str, instance_id: str, inputs: dict = None):
        """构造共享上下文并规划协同顺序（供 dispatch / dispatch_stream 复用）。"""
        ctx_inputs = dict(inputs or {})

        # 取目标数据源的解密连接信息，供巡检专员实时调用引擎使用
        target_instance = None
        try:
            target_instance = _get_instance(instance_id)
        except Exception:
            target_instance = None
        if target_instance:
            ctx_inputs["target_instance"] = target_instance
            ctx_inputs["target_meta"] = {
                "instance_id": instance_id,
                "instance_name": target_instance.get("name", ""),
                "db_type": target_instance.get("db_type", ""),
            }

        # 同时预取最近一次历史巡检报告，作为实时引擎失败时的回退
        try:
            report = _build_inspection_report(instance_id)
            if report is not None:
                ctx_inputs.setdefault("inspection_report", report)
        except Exception:
            pass

        ctx = SharedContext(
            goal=goal or "对目标数据源做一次协同诊断",
            target=instance_id,
            inputs=ctx_inputs,
        )
        ctx.started_at = _now()
        seq = plan_sequence(ctx, self.registry)
        return ctx, seq

    def _run_specialist(self, ctx: SharedContext, sid: str) -> None:
        spec = self.registry.get(sid)
        if spec is None:
            return
        try:
            for f in spec.analyze(ctx):
                ctx.add(f)
        except Exception as e:  # 单个能力异常不影响整体协同
            ctx.notes.append(f"{spec.name} 执行异常：{e}")

    def _str_notes(self, ctx: SharedContext) -> List[str]:
        """确保 notes 里都是字符串，防止非字符串对象混入前端显示 [object Object]。"""
        out: List[str] = []
        for n in ctx.notes:
            if isinstance(n, str):
                out.append(n)
            elif isinstance(n, bytes):
                out.append(n.decode("utf-8", "ignore"))
            else:
                try:
                    out.append(str(n))
                except Exception:
                    out.append(repr(n))
        return out

    def _finalize(self, ctx: SharedContext, seq: list) -> dict:
        ctx.finished_at = _now()

        # 方案验证（Cost Optimizer 思路）：对处置方案做代价/收益/可行性评估
        plan_validation = {}
        try:
            from .cost_optimizer import validate_plan

            plan_validation = validate_plan(
                ctx.plan, [f.to_dict() for f in ctx.findings]
            )
        except Exception:
            plan_validation = {}

        meta = ctx.inputs.get("target_meta") or {}
        return {
            "edition": "professional",
            "goal": ctx.goal,
            "target": ctx.target,
            "target_meta": meta,
            "sequence": seq,
            "findings": [f.to_dict() for f in ctx.findings],
            "plan": ctx.plan,
            "plan_validation": plan_validation,
            "notes": self._str_notes(ctx),
            "specialists": {c["id"]: c["name"] for c in self.capabilities()},
            "started_at": ctx.started_at,
            "finished_at": ctx.finished_at,
        }

    def dispatch(self, goal: str, instance_id: str, inputs: dict = None) -> dict:
        ctx, seq = self._prepare(goal, instance_id, inputs)
        for sid in seq:
            self._run_specialist(ctx, sid)
        return self._finalize(ctx, seq)

    def dispatch_stream(self, goal: str, instance_id: str, inputs: dict = None):
        """生成器版本：逐个专员执行，产出进度事件，最后产出完整结果。

        每次 yield 一个事件 dict：
          {"type":"sequence", "sequence":[...], "specialists":{id:name}, "total":n}
          {"type":"progress", "current":sid, "name":..., "index":i, "total":n, "phase":"start|done"}
          {"type":"result", "result":{...}}
        """
        ctx, seq = self._prepare(goal, instance_id, inputs)
        spec_names = {c["id"]: c["name"] for c in self.capabilities()}
        total = len(seq)
        yield {
            "type": "sequence",
            "sequence": seq,
            "specialists": spec_names,
            "total": total,
        }
        for i, sid in enumerate(seq, 1):
            yield {
                "type": "progress", "phase": "start",
                "current": sid, "name": spec_names.get(sid, sid),
                "index": i, "total": total,
            }
            self._run_specialist(ctx, sid)
            yield {
                "type": "progress", "phase": "done",
                "current": sid, "name": spec_names.get(sid, sid),
                "index": i, "total": total,
            }
        yield {"type": "result", "result": self._finalize(ctx, seq)}


_hub = None


def get_hub() -> DiagnosticHub:
    global _hub
    if _hub is None:
        _hub = DiagnosticHub()
    return _hub
