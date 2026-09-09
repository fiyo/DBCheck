# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""协调员（Coordinator）。

界面上作为一个可见的「角色」展示，负责理解用户的诊断目标，
决定该由哪些协同能力角色（专员）参与、以及它们的执行顺序。

实际调度逻辑在 ``modules/intelligence.planner`` 中完成（AI 驱动或规则兜底），
本类 ``analyze`` 不产出发现，只作为占位角色让前端渲染状态与顺序。
"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist


class Coordinator(Specialist):
    id = "coordinator"
    name = "协调员"
    description = (
        "理解诊断目标，调用大模型判断该由哪些协同能力角色参与、以什么顺序处理，"
        "并把调度决策下达给各角色执行。"
    )
    domain = "orchestration"
    tags = ["coordinator", "orchestration"]

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        # 协调逻辑在 planner 中完成，这里不产出发现。
        return []
