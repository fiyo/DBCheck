# -*- coding: utf-8 -*-
"""任务分解：把一句目标拆成专家能力的协同顺序。

不做层级转发，各能力直接读写共享上下文，保持结论完整、
不被压缩。顺序依据已有现象动态追加相关专业能力。
"""

from __future__ import annotations

from typing import List

from .context import SharedContext
from .registry import SpecialistRegistry


def plan_sequence(ctx: SharedContext, reg: SpecialistRegistry) -> List[str]:
    """规划专员执行顺序。

    基础三大能力（监控、巡检、根因）始终参与；SQL 治理与锁等待分析
    默认也纳入协同。当上下文已出现 sql/lock 标签时，把对应能力提前
    到根因之后立即执行，实现「按现象动态追加」的效果。
    """
    seq = ["monitor_sentinel", "inspection_expert", "rootcause_expert"]
    tags: set = set()
    for f in ctx.findings:
        tags.update(f.tags)

    # 默认全员参与；若已发现 SQL/锁相关标签，则提前执行对应专员
    if tags & {"sql", "slow_sql"}:
        if reg.get("sql_governance") is not None and "sql_governance" not in seq:
            seq.insert(3, "sql_governance")
    if tags & {"lock", "block"}:
        if reg.get("lock_analyst") is not None and "lock_analyst" not in seq:
            seq.insert(3, "lock_analyst")

    # 兜底：只要已注册就加入序列
    for sid in ("sql_governance", "lock_analyst"):
        if reg.get(sid) is not None and sid not in seq:
            seq.append(sid)

    # 仅保留已注册的能力
    return [s for s in seq if reg.get(s) is not None]
