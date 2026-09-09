# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""基线比对专员：识别配置/性能相对基线的漂移。

阶段 C 专家域扩展（规划文档 4.4 C）。triggers 命中后被迭代重规划自动追加。
"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist


class BaselineCompare(Specialist):
    id = "baseline_compare"
    name = "基线比对专员"
    description = "对比当前实例配置与性能相对部门基线的漂移，发现异常偏离。"
    tags = ["baseline", "config", "drift"]
    domain = "inspection"
    deps = ["inspection_expert"]
    triggers = ["baseline", "config", "drift", "param"]

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        rel = [f for f in ctx.findings
               if set(f.tags or []) & {"baseline", "config", "drift", "param"}]
        if not rel:
            return [
                Finding(
                    source=self.id,
                    category="risk",
                    severity="info",
                    title="基线比对待命中",
                    detail="当前无配置/性能基线漂移信号。",
                    suggestion="当巡检发现参数偏离基线或性能退化时，本能力将自动比对部门基线。",
                    tags=["baseline"],
                )
            ]
        out: List[Finding] = []
        for f in rel:
            out.append(
                Finding(
                    source=self.id,
                    category="plan",
                    severity=f.severity,
                    title=f"基线比对：{f.title}",
                    detail=f.detail,
                    suggestion="与本部门/历史基线对照，确认偏离是否业务预期；"
                              "非预期偏离需回滚参数或扩容。",
                    tags=["baseline", "config"],
                )
            )
        return out
