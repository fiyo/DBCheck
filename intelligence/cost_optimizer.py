# -*- coding: utf-8 -*-
"""协同诊断 · 方案验证（Cost Optimizer 思路）。

对根因定位专员产出的处置方案逐条做「代价 / 收益 / 可行性」评估，
帮助用户在落地前判断哪些动作性价比最高、哪些需要维护窗口。
纯启发式（基于方案文本关键词），不依赖外部系统。
"""

from __future__ import annotations

from typing import Any, Dict, List

# 成本档位：实施代价（人力 + 风险 + 停机可能）
_COST_RULES = [
    # (关键词, 成本, 收益描述, 风险, 可行性)
    (("扩容", "迁移", "升级", "增加内存", "增加节点", "分库"), "high",
     "提升资源/容量上限，从根源缓解瓶颈", "high", "window"),
    (("参数", "配置", "缓冲池", "连接池", "max_connections", "内核", "内核参数"), "medium",
     "以较低改动提升系统吞吐与稳定性", "medium", "manual"),
    (("锁", "事务", "阻塞", "死锁", "加锁"), "medium",
     "消除阻塞与等待链，恢复并发吞吐", "medium", "manual"),
    (("索引", "改写", "sql", "执行计划", "全表扫描", "统计信息", "analyze"), "low",
     "降低单条 SQL 响应时间与资源消耗", "low", "auto"),
]

_LEVEL_WEIGHT = {"low": 1, "medium": 2, "high": 3}


def _match(text: str):
    t = (text or "").lower()
    for kws, cost, benefit, risk, feas in _COST_RULES:
        if any(k.lower() in t for k in kws):
            return cost, benefit, risk, feas
    return "medium", "改善对应维度的健康度", "medium", "manual"


def validate_plan(plan: List[Dict[str, Any]], findings: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对处置方案逐条评估，返回 {validations: [...], summary: {...}}。

    validations 与 plan 下标对齐，每条含：
        cost        实施成本 low/medium/high
        benefit     预期收益（中文短句）
        risk        回滚风险 low/medium/high
        feasibility 落地方式 auto(可自动)/manual(需人工)/window(需维护窗口)
        validated   是否具备可执行动作
        priority    建议优先级 1(最高)~3
    """
    plan = plan or []
    validations: List[Dict[str, Any]] = []
    cost_scores = []
    for idx, step in enumerate(plan):
        focus = step.get("focus", "") or ""
        actions = step.get("actions") or []
        action_text = " ".join(actions)
        cost, benefit, risk, feas = _match(focus + " " + action_text)
        has_action = bool(actions)
        validations.append({
            "step": step.get("step", idx + 1),
            "cost": cost,
            "benefit": benefit,
            "risk": risk,
            "feasibility": feas,
            "validated": has_action,
            "priority": _LEVEL_WEIGHT.get(cost, 2),
        })
        cost_scores.append(_LEVEL_WEIGHT.get(cost, 2))

    # 汇总
    total = len(validations)
    auto_count = sum(1 for v in validations if v["feasibility"] == "auto")
    window_count = sum(1 for v in validations if v["feasibility"] == "window")
    high_risk = sum(1 for v in validations if v["risk"] == "high")
    avg_cost = round(sum(cost_scores) / total, 2) if total else 0
    # 推荐顺序：成本升序（先易后难）
    recommended_order = sorted(
        range(total), key=lambda i: (validations[i]["priority"], 0 if validations[i]["validated"] else 1)
    )
    summary = {
        "total": total,
        "auto_executable": auto_count,
        "needs_window": window_count,
        "high_risk": high_risk,
        "avg_cost_score": avg_cost,
        "recommended_first": [validations[i]["step"] for i in recommended_order[:3]],
    }
    return {"validations": validations, "summary": summary}
