# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""任务分解：把一句目标拆成专家能力的协同顺序。

协调员（Coordinator）角色：

- 当 AI 可用时，由大模型读取诊断目标与可选协同能力清单，输出
  「应参与的专员 + 执行顺序」的 JSON 决策（AI 驱动）；
- AI 不可用时回落到确定性规则（基于查询意图关键词与已发现的风险标签）。

各能力直接读写共享上下文，结论完整、不被压缩；顺序依据目标动态确定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .context import SharedContext
from .registry import SpecialistRegistry


@dataclass
class Plan:
    """一次协调决策的结果。"""

    sequence: List[str]
    reason: str = ""
    ai_driven: bool = False

    @property
    def order(self) -> Dict[str, int]:
        """id -> 执行顺序（1-based）。"""
        return {sid: i + 1 for i, sid in enumerate(self.sequence)}


# 自然语言查询意图关键词 / 标点
_NL_QUERY_MARKERS = (
    "?", "？", "几个", "多少", "是否", "有没有", "哪些是", "哪些表", "哪些用户",
    "什么", "查询", "列出", "统计", "查看", "告诉我", "检查一下",
    "数据库里有", "库里", "库中", "这个库",
)

# 国产库类型标识（与 specialists/native_db.py 保持一致）
_NATIVE_TYPES = {
    "dm", "dm8", "hgdb", "kingbase", "kingbasees",
    "oceanbase", "tidb", "yashandb", "uxdb", "gbase",
}


def _looks_nl_query(goal: str) -> bool:
    """判断诊断目标是否为自然语言查询（需要自然语言探查专员回答）。"""
    g = (goal or "").lower()
    return any(m in g for m in _NL_QUERY_MARKERS)


# ── 规则兜底 ───────────────────────────────────────────────────────────────
def _plan_rule_based(ctx: SharedContext, reg: SpecialistRegistry) -> Plan:
    goal = ctx.goal or ""
    # 明确的自然语言查询目标：只调度自然语言探查专员，避免其他专家
    # 基于历史巡检报告给出与问题无关的噪音结论。
    if reg.get("nl_query_expert") is not None and _looks_nl_query(goal):
        return Plan(
            sequence=["nl_query_expert"],
            reason="目标是自然语言提问，直接交由自然语言探查专员查询并作答。",
            ai_driven=False,
        )

    # 基础链路：监控 → 巡检 → 根因。
    # 注意：规划发生在任何专家执行之前，ctx.findings 此时为空，因此专项能力
    # （sql_governance / lock_analyst）不再在此无脑追加，而是由迭代重规划
    # replan() 依据专家运行后真实产出的发现标签动态追加（见规划文档 4.1/4.4 A）。
    seq = [s for s in ("monitor_sentinel", "inspection_expert", "rootcause_expert")
           if reg.get(s) is not None]

    # 目标为国产库时，初始调度就引入国产库专家（避免基础链路未产出 hgdb/dm8 等
    # 标签导致重规划无法触发 native_db）。
    meta = ctx.inputs.get("target_meta") or {}
    inst = ctx.inputs.get("target_instance") or {}
    db_type = (meta.get("db_type") or inst.get("db_type") or "").lower()
    if db_type in _NATIVE_TYPES and reg.get("native_db") is not None:
        seq.append("native_db")

    reason_parts = ["基础链路：监控 → 巡检 → 根因；专项能力（SQL 治理 / 锁分析）将依据实际发现由重规划动态追加。"]
    if db_type in _NATIVE_TYPES:
        reason_parts.append(f"目标库型 {db_type} 为国产库，初始调度追加国产库专家。")
    return Plan(
        sequence=seq,
        reason=" ".join(reason_parts),
        ai_driven=False,
    )


# ── 迭代重规划 ─────────────────────────────────────────────────────────────
def replan(ctx: SharedContext, prev_plan: Plan, reg: SpecialistRegistry) -> Optional[Plan]:
    """基于已运行专家的真实发现，动态追加需要参与的专项能力。

    这是「一次性串行」升级为「迭代闭环」的核心（规划文档 4.3/4.4 A）：
    每轮跑完 pending 专家后调用本函数，扫描 ctx.findings 的标签，把 triggers
    命中的能力追加到执行序列；无新增则返回 None，由调用方判定收敛。

    返回的新 Plan 仅 *追加* 能力（序列单调递增），不会重排已执行项，避免抖动。
    """
    tags: set = set()
    for f in ctx.findings:
        tags.update(f.tags or [])

    if not tags:
        return None

    seq = list(prev_plan.sequence)
    added: List[str] = []
    for s in reg.all():
        if s.id in seq or s.id == "coordinator":
            continue
        triggers = getattr(s, "triggers", None) or []
        if triggers and (set(triggers) & tags):
            seq.append(s.id)
            added.append(s.id)

    if not added:
        return None

    ctx.revision_log.append({
        "iteration": ctx.iteration,
        "triggered_by_tags": sorted(tags),
        "added": added,
    })
    return Plan(
        sequence=seq,
        reason=f"第 {ctx.iteration} 轮重规划：依据发现标签 {sorted(tags)} 追加专项能力 {added}。",
        ai_driven=False,
    )


# ── AI 驱动协调 ─────────────────────────────────────────────────────────────
def _build_coordinator_prompt(goal: str, db_type: str, specs: List[Dict[str, Any]]) -> str:
    spec_lines = "\n".join(
        f"- id={s['id']} | 名称={s['name']} | 能力={s['description']}"
        for s in specs
    )
    return (
        "你是一个数据库「协同诊断中心」的协调员。请根据用户的诊断目标，"
        "从下面的协同能力中选出需要参与的，并按执行先后顺序排列。\n\n"
        f"目标数据源类型：{db_type or '未知'}\n"
        f"用户诊断目标：{goal}\n\n"
        "可选协同能力：\n" + spec_lines + "\n\n"
    "编排规则：\n"
    "1. 若目标是自然语言提问（含「几个 / 多少 / 是否 / 哪些 / 统计 / 查询 / 这个库」等词或问号），"
    "优先只选 nl_query_expert 直接作答；除非目标同时包含性能 / 健康类诉求。\n"
    "2. 性能 / 连接 / 资源类问题：monitor_sentinel → inspection_expert → rootcause_expert。\n"
    "3. 若目标数据源类型是国产库（DM8 / HGDB / Kingbase / OceanBase / TiDB / YashanDB 等），"
    "必须追加 native_db 专家。\n"
    "4. 若出现 SQL 慢 / 锁等待现象，追加 sql_governance、lock_analyst。\n"
    "5. coordinator 自身不计入执行序列。\n\n"
        "只输出一个 JSON 对象，格式严格如下（不要输出任何解释性文字或代码块围栏）：\n"
        '{"reason": "一句话说明为什么这样调度", "sequence": ["id1", "id2", ...]}'
    )


def _plan_with_ai(ctx: SharedContext, reg: SpecialistRegistry, advisor) -> Optional[Plan]:
    specs = []
    for s in reg.all():
        if s.id == "coordinator":
            continue
        specs.append({
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "tags": list(s.tags),
        })
    if not specs:
        return None

    from .ai_helper import safe_json

    db_type = ""
    meta = ctx.inputs.get("target_meta") or {}
    if meta:
        db_type = meta.get("db_type", "")
    if not db_type:
        inst = ctx.inputs.get("target_instance") or {}
        db_type = inst.get("db_type", "")

    prompt = _build_coordinator_prompt(ctx.goal or "", db_type, specs)
    raw = advisor._call_llm(prompt, timeout=60)
    data = safe_json(raw)
    if isinstance(data, dict):
        seq_raw = data.get("sequence") or data.get("order") or []
        reason = data.get("reason") or ""
    elif isinstance(data, list):
        seq_raw = data
        reason = ""
    else:
        return None

    seq: List[str] = []
    for sid in seq_raw:
        sid = str(sid).strip()
        if reg.get(sid) is not None and sid != "coordinator":
            seq.append(sid)
    # 去重保序
    seen = set()
    seq = [x for x in seq if not (x in seen or seen.add(x))]
    if not seq:
        return None
    return Plan(sequence=seq, reason=reason, ai_driven=True)


def plan_sequence(
    ctx: SharedContext,
    reg: SpecialistRegistry,
    advisor=None,
) -> Plan:
    """规划专员执行顺序。

    AI 可用时优先使用 AI 驱动编排；失败时自动回落规则兜底，
    保证即便 AI 未配置也能正常工作。
    """
    if advisor is not None:
        try:
            p = _plan_with_ai(ctx, reg, advisor)
            if p is not None:
                return p
        except Exception as e:  # 协调失败不应阻断诊断
            ctx.notes.append(f"协调员：AI 编排失败（{e}），改用规则编排。")
    return _plan_rule_based(ctx, reg)
