# -*- coding: utf-8 -*-
"""协同诊断共享上下文（黑板）。

一次协同诊断的所有中间结论、专家发现与最终处置方案都写入此处，
各专家能力共享同一上下文，避免信息在层层传递中失真或压缩。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


@dataclass
class Finding:
    """一条专家发现。"""

    source: str                 # 产出该发现的专家能力 id
    category: str               # anomaly / risk / rootcause / plan
    severity: str               # info / warning / critical
    title: str
    detail: str = ""
    suggestion: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SharedContext:
    """一次协同诊断的共享工作区。"""

    goal: str
    target: str                               # 目标数据源 id
    inputs: Dict[str, Any] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def by_category(self, category: str) -> List[Finding]:
        return [f for f in self.findings if f.category == category]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "target": self.target,
            "inputs": self.inputs,
            "findings": [f.to_dict() for f in self.findings],
            "plan": self.plan,
            "notes": self.notes,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
