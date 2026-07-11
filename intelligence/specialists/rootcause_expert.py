# -*- coding: utf-8 -*-
"""根因分析专员：综合监控异常与巡检风险，关联推断根因。"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist

_LABELS = {
    "compute": "计算资源（CPU）瓶颈",
    "io": "IO / 存储瓶颈",
    "connection": "连接 / 会话压力",
    "lock": "锁等待 / 阻塞",
    "sql": "SQL 性能问题",
    "other": "综合异常",
}


def _bucket(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("cpu", "处理器", "计算")):
        return "compute"
    if any(k in t for k in ("io", "磁盘", "存储")):
        return "io"
    if any(k in t for k in ("连接", "connection", "会话", "session")):
        return "connection"
    if any(k in t for k in ("锁", "lock", "阻塞", "block")):
        return "lock"
    # SQL 性能：避免把 "SQLServer"、"MySQL" 等产品名误判为 SQL 问题
    sql_perf_kw = ("慢", "slow", "执行计划", "全表扫描", "索引缺失", "index", "cost", "高代价")
    if any(k in t for k in sql_perf_kw):
        return "sql"
    return "other"


class RootCauseExpert(Specialist):
    id = "rootcause_expert"
    name = "根因定位分析专员"
    description = "汇总监控异常与巡检风险，关联聚类推断根因，并给出处置主线。"
    tags = ["rootcause"]

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        raw = ctx.by_category("anomaly") + ctx.by_category("risk")
        out: List[Finding] = []
        if not raw:
            out.append(
                Finding(
                    source=self.id,
                    category="rootcause",
                    severity="info",
                    title="暂无可关联的原始现象",
                    detail="监控与巡检两侧都未提供现象数据，无法推断根因。",
                    suggestion="先由运行监控专员或巡检分析专员产出现象，再回到本能力做关联。",
                )
            )
            return out

        clusters: dict = {}
        for f in raw:
            b = _bucket(f.title + " " + f.detail)
            clusters.setdefault(b, []).append(f)

        step = 1
        for b, items in clusters.items():
            label = _LABELS.get(b, "综合异常")
            evidence = "; ".join(i.title for i in items)
            out.append(
                Finding(
                    source=self.id,
                    category="rootcause",
                    severity="warning",
                    title=f"疑似根因：{label}",
                    detail=f"关联现象：{evidence}",
                    suggestion="定位到主因后，由 SQL 治理 / 锁分析等能力给出具体处置动作。",
                    tags=[b] if b != "other" else [],
                )
            )
            ctx.plan.append(
                {
                    "step": step,
                    "focus": label,
                    "evidence": evidence,
                    "actions": [i.suggestion for i in items if i.suggestion][:6],
                }
            )
            step += 1
        return out
