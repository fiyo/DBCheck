# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

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
from modules.config.version import EDITION

from .context import Finding, SharedContext
from .planner import plan_sequence, replan
from .ai_helper import build_advisor
from .registry import registry
from .specialists import register_all
from .reviewer import Reviewer

from modules.core import paths

paths.ensure_migrated()

# 迭代重规划的最大轮次（规划文档 4.4 A：自适应编排闭环）。
# 每轮跑完 pending 专家后做一次重规划；无新增专项能力或达上限即收敛。
DEFAULT_MAX_ITER = 3

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
        from modules.pro.instance_manager import get_instance_manager

        im = get_instance_manager()
        db_file = getattr(im, "db_file", None)
        if db_file and os.path.exists(db_file):
            return db_file
    except Exception:
        pass
    # 防御性回退：若 Pro 模块导入失败，按默认路径查找
    try:
        fallback = str(paths.PRO_DATA_DIR / "pro_history.db")
        if os.path.exists(fallback):
            return fallback
    except Exception:
        pass
    return None


def _get_instance(instance_id: str) -> Optional[Dict[str, Any]]:
    """从 Pro 实例管理器读取单个数据源详情。"""
    try:
        from modules.pro.instance_manager import get_instance_manager

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
        # 方案 B：诊断分析（col3）与处置建议（fix）分离。
        # fix 优先取独立的处置建议字段，退化取 col3（诊断分析）；
        # detail 优先取 detail 字段，退化取 col3（诊断分析），确保
        # 诊断结果页与处置方案页展示的内容不再完全雷同。
        fix = t(item.get("fix") or item.get("col3", ""), default=item.get("col3", ""))
        detail = t(item.get("detail") or item.get("col3", ""), default=item.get("col3", ""))
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


def prepare_instance_inputs(instance_id: str) -> Dict[str, Any]:
    """构造实例输入：数据源连接信息 + 历史巡检报告。

    供诊断中枢 ``_prepare`` 与「工作流任务」运行器共用，确保无论走哪条执行路径，
    专家都能拿到 ``target_instance`` / ``target_meta`` / ``inspection_report`` 三样数据。
    单点失败（解密失败 / 无历史巡检）不影响整体，缺失即留空由专家自行降级。
    """
    inputs: Dict[str, Any] = {}
    try:
        inst = _get_instance(instance_id)
        if inst:
            inputs["target_instance"] = inst
            inputs["target_meta"] = {
                "instance_id": instance_id,
                "instance_name": inst.get("name", ""),
                "db_type": inst.get("db_type", ""),
            }
    except Exception:
        pass
    try:
        report = _build_inspection_report(instance_id)
        if report is not None:
            inputs["inspection_report"] = report
    except Exception:
        pass
    return inputs


class DiagnosticHub:
    def __init__(self) -> None:
        register_all()
        self.registry = registry

    def capabilities(self) -> list:
        caps = [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tags": s.tags,
                "domain": getattr(s, "domain", "general") or "general",
            }
            for s in self.registry.all()
        ]
        # 协调员固定排在最前，作为编排入口可见
        caps.sort(key=lambda c: (0 if c["id"] == "coordinator" else 1, c["id"]))
        return caps

    def _prepare(self, goal: str, instance_id: str, inputs: dict = None):
        """构造共享上下文并规划协同顺序（供 dispatch / dispatch_stream 复用）。"""
        ctx_inputs = dict(inputs or {})
        # 注入实例数据源连接信息 + 历史巡检报告（与「工作流任务」运行器共用同一入口）
        ctx_inputs.update(prepare_instance_inputs(instance_id))

        ctx = SharedContext(
            goal=goal or "对目标数据源做一次协同诊断",
            target=instance_id,
            inputs=ctx_inputs,
        )
        ctx.started_at = _now()
        advisor = build_advisor()
        plan = plan_sequence(ctx, self.registry, advisor)
        return ctx, plan

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

    def _finalize(self, ctx: SharedContext, plan) -> dict:
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
        spec_names = {c["id"]: c["name"] for c in self.capabilities()}
        return {
            "edition": EDITION,
            "goal": ctx.goal,
            "target": ctx.target,
            "target_meta": meta,
            "sequence": plan.sequence,
            "coordinator": {
                "reason": plan.reason,
                "ai_driven": plan.ai_driven,
                "sequence": plan.sequence,
                "order": plan.order,
            },
            "findings": [f.to_dict() for f in ctx.findings],
            "plan": ctx.plan,
            "plan_validation": plan_validation,
            "notes": self._str_notes(ctx),
            "specialists": spec_names,
            # 迭代重规划闭环产物（规划文档 4.3/4.4 A）
            "iteration": ctx.iteration,
            "revision_log": ctx.revision_log,
            # Reviewer 把关结论（规划文档 4.4 B/D）：诊断闭环末端写入 ctx.review，
            # 此处回传前端用于审计链可视化；缺失时取 None 不阻断诊断。
            "review": getattr(ctx, "review", None),
            "started_at": ctx.started_at,
            "finished_at": ctx.finished_at,
        }

    def _resolve_max_iter(self, ctx: SharedContext, max_iter: Optional[int]) -> int:
        if max_iter is not None and max_iter > 0:
            return max_iter
        try:
            v = int(ctx.inputs.get("max_iter", DEFAULT_MAX_ITER))
            return v if v > 0 else DEFAULT_MAX_ITER
        except (TypeError, ValueError):
            return DEFAULT_MAX_ITER

    def _run_iterative(self, ctx: SharedContext, plan, max_iter: int):
        """迭代重规划闭环（规划文档 4.4 A：自适应编排）。

        每轮：
          1. 执行当前序列中尚未运行过的专家（pending）；
          2. 依据这些专家真实产出的发现标签做重规划（replan）；
          3. 若追加了新专项能力则进入下一轮，否则收敛退出。
        已运行的专家不会被重复执行；序列单调递增，无抖动。
        """
        run_set: set = set()
        for it in range(1, max_iter + 1):
            ctx.iteration = it
            pending = [sid for sid in plan.sequence if sid not in run_set]
            if not pending:
                break
            for sid in pending:
                self._run_specialist(ctx, sid)
                run_set.add(sid)
            # 本轮结束后，根据真实发现重规划
            try:
                new_plan = replan(ctx, plan, self.registry)
            except Exception as e:  # 重规划异常不应阻断诊断
                ctx.notes.append(f"重规划异常（已忽略，保持当前序列）：{e}")
                new_plan = None
            if new_plan is None or new_plan.sequence == plan.sequence:
                break
            plan = new_plan
        return plan

    def dispatch(self, goal: str, instance_id: str, inputs: dict = None,
                 max_iter: int = None) -> dict:
        ctx, plan = self._prepare(goal, instance_id, inputs)
        max_iter = self._resolve_max_iter(ctx, max_iter)
        plan = self._run_iterative(ctx, plan, max_iter)
        self._review(ctx)
        return self._finalize(ctx, plan)

    def _review(self, ctx: SharedContext) -> None:
        """协同诊断闭环末端的把关：把 Reviewer 结论写入 ctx.review（规划文档 4.4 B）。"""
        try:
            ctx.review = Reviewer().review(ctx).to_dict()
        except Exception as e:  # 把关失败绝不该阻断诊断
            ctx.review = {
                "approved": False,
                "confidence": "low",
                "summary": f"把关失败（已忽略）: {e}",
                "issues": [str(e)],
                "risk_flags": [],
                "gate_decisions": [],
            }

    def dispatch_stream(self, goal: str, instance_id: str, inputs: dict = None,
                        max_iter: int = None):
        """生成器版本：先产出协调员决策，再逐个专员执行并推送进度，最后完整结果。

        每次 yield 一个事件 dict：
          {"type":"coordinator", ... "total":n, "iteration":1}
          {"type":"progress", "current":sid, "name":..., "index":i, "total":n, "phase":"start|done", "iteration":it}
          {"type":"replan", "iteration":it, "sequence":[...], "reason":...}   # 发生重规划时
          {"type":"result", "result":{...}}
        """
        ctx, plan = self._prepare(goal, instance_id, inputs)
        max_iter = self._resolve_max_iter(ctx, max_iter)
        spec_names = {c["id"]: c["name"] for c in self.capabilities()}
        yield {
            "type": "coordinator",
            "reason": plan.reason,
            "ai_driven": plan.ai_driven,
            "sequence": plan.sequence,
            "order": plan.order,
            "specialists": spec_names,
            "total": len(plan.sequence),
            "iteration": 1,
        }
        run_set: set = set()
        for it in range(1, max_iter + 1):
            ctx.iteration = it
            pending = [sid for sid in plan.sequence if sid not in run_set]
            if not pending:
                break
            for i, sid in enumerate(pending, 1):
                yield {
                    "type": "progress", "phase": "start",
                    "current": sid, "name": spec_names.get(sid, sid),
                    "index": i, "total": len(pending), "iteration": it,
                }
                self._run_specialist(ctx, sid)
                run_set.add(sid)
                yield {
                    "type": "progress", "phase": "done",
                    "current": sid, "name": spec_names.get(sid, sid),
                    "index": i, "total": len(pending), "iteration": it,
                }
            # 本轮结束后重规划
            try:
                new_plan = replan(ctx, plan, self.registry)
            except Exception:
                new_plan = None
            if new_plan is None or new_plan.sequence == plan.sequence:
                break
            plan = new_plan
            yield {
                "type": "replan",
                "iteration": it + 1,
                "sequence": plan.sequence,
                "reason": plan.reason,
            }
        # 闭环末端把关：产出 Reviewer 结论并推送（规划文档 4.4 B）
        self._review(ctx)
        yield {
            "type": "review",
            "iteration": it,
            "result": ctx.review,
        }
        yield {"type": "result", "result": self._finalize(ctx, plan)}


_hub = None


def get_hub() -> DiagnosticHub:
    global _hub
    if _hub is None:
        _hub = DiagnosticHub()
    return _hub
