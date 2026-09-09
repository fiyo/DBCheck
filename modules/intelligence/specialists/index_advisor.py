# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""索引顾问：针对慢 SQL / 缺失索引现象，给出索引设计与代价评估建议。

阶段 C 专家域扩展（规划文档 4.4 C）。triggers 命中后被迭代重规划自动追加。
"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist


class IndexAdvisor(Specialist):
    id = "index_advisor"
    name = "索引顾问"
    description = "针对慢 SQL 与缺失索引现象，给出索引创建/重建建议与写入放大代价评估。"
    tags = ["index", "sql", "performance"]
    domain = "sql"
    deps = ["sql_governance"]
    triggers = ["index", "slow_sql", "sql", "missing_index"]

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        rel = [f for f in ctx.findings
               if set(f.tags or []) & {"index", "slow_sql", "sql", "missing_index"}]
        if not rel:
            return [
                Finding(
                    source=self.id,
                    category="risk",
                    severity="info",
                    title="索引顾问待命中",
                    detail="当前未发现索引或慢 SQL 相关问题。",
                    suggestion="当发现缺失索引/全表扫描时，本能力将自动介入评估索引方案。",
                    tags=["index"],
                )
            ]
        out: List[Finding] = []
        for f in rel:
            out.append(
                Finding(
                    source=self.id,
                    category="plan",
                    severity=f.severity,
                    title=f"索引建议：{f.title}",
                    detail=f.detail,
                    suggestion="评估选择性高的列建立复合索引；注意写入放大与索引维护成本，"
                              "先在测试环境验证执行计划变化后再上线。",
                    tags=["index", "sql"],
                )
            )
        return out
