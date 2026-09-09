# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""容量分析专员：评估存储增长趋势与容量风险。

阶段 C 专家域扩展（规划文档 4.4 C）。triggers 命中后被迭代重规划自动追加。
"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist


class CapacityAnalyst(Specialist):
    id = "capacity_analyst"
    name = "容量分析专员"
    description = "评估存储使用率与增长趋势，预测容量风险并给出扩容建议。"
    tags = ["capacity", "storage", "growth"]
    domain = "monitor"
    deps = ["monitor_sentinel"]
    triggers = ["capacity", "storage", "growth", "disk"]

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        rel = [f for f in ctx.findings
               if set(f.tags or []) & {"capacity", "storage", "growth", "disk"}]
        if not rel:
            return [
                Finding(
                    source=self.id,
                    category="risk",
                    severity="info",
                    title="容量分析待命中",
                    detail="当前无容量/增长类风险信号。",
                    suggestion="当监控发现磁盘使用率偏高或增长过快时，本能力将自动介入评估。",
                    tags=["capacity"],
                )
            ]
        out: List[Finding] = []
        for f in rel:
            out.append(
                Finding(
                    source=self.id,
                    category="plan",
                    severity=f.severity,
                    title=f"容量建议：{f.title}",
                    detail=f.detail,
                    suggestion="估算增长率与剩余可承载周期；提前规划扩容或清理归档，"
                              "避免写满导致实例不可用。",
                    tags=["capacity", "storage"],
                )
            )
        return out
