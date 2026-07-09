# -*- coding: utf-8 -*-
"""SQL 治理专员：针对 SQL 相关风险给出改写与索引建议。"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist


class SqlGovernance(Specialist):
    id = "sql_governance"
    name = "SQL 治理专员"
    description = "针对慢 SQL 与高代价语句，给出改写、索引与变更审核建议。"
    tags = ["sql", "governance"]

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        sql_findings = [f for f in ctx.findings if "sql" in f.tags or "slow_sql" in f.tags]
        if not sql_findings:
            return [
                Finding(
                    source=self.id,
                    category="risk",
                    severity="info",
                    title="SQL 治理待命中",
                    detail="当前现象未涉及 SQL 性能问题。",
                    suggestion="当监控或巡检发现慢 SQL 时，本能力将自动介入做改写与索引建议。",
                    tags=["sql"],
                )
            ]
        out: List[Finding] = []
        for f in sql_findings:
            out.append(
                Finding(
                    source=self.id,
                    category="plan",
                    severity=f.severity,
                    title=f"SQL 治理：{f.title}",
                    detail=f.detail,
                    suggestion="评估执行计划，必要时重写 SQL 或补充索引；变更前做风险审核与回滚预案。",
                    tags=["sql"],
                )
            )
        return out
