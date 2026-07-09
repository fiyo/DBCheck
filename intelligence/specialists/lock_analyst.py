# -*- coding: utf-8 -*-
"""锁分析专员：针对锁等待与阻塞溯源等待链。"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist


class LockAnalyst(Specialist):
    id = "lock_analyst"
    name = "锁等待分析专员"
    description = "针对锁等待与阻塞，溯源持锁会话与等待链并给出拆解建议。"
    tags = ["lock"]

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        lock_findings = [f for f in ctx.findings if "lock" in f.tags or "block" in f.tags]
        if not lock_findings:
            return [
                Finding(
                    source=self.id,
                    category="risk",
                    severity="info",
                    title="锁分析待命中",
                    detail="当前现象未涉及锁等待。",
                    suggestion="当出现锁阻塞时，本能力将自动溯源等待链。",
                    tags=["lock"],
                )
            ]
        out: List[Finding] = []
        for f in lock_findings:
            out.append(
                Finding(
                    source=self.id,
                    category="plan",
                    severity=f.severity,
                    title=f"锁分析：{f.title}",
                    detail=f.detail,
                    suggestion="定位持锁会话与等待链，优先提交或回滚源头事务以解开阻塞。",
                    tags=["lock"],
                )
            )
        return out
