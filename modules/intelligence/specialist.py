# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""专家能力基类。

每个专家能力聚焦一个专业领域：读取共享上下文中的已有结论，
追加自己的发现或处置建议，写入同一上下文供其它能力复用。
各能力之间互不隶属，结论沉淀在共享上下文里。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from .context import SharedContext, Finding


class Specialist(ABC):
    id: str = ""
    name: str = ""
    description: str = ""
    tags: List[str] = []

    # ── 多 Agent 增强元数据（对标 Ongrid Skills/MCP 工具目录，规划文档 4.3）──
    # domain: 专业域（monitor/inspection/rootcause/sql/lock/nl），供 list_by_domain 检索
    domain: str = ""
    # deps: 应在其之后执行的专项能力（仅作为重规划追加时的排序提示）
    deps: List[str] = []
    # triggers: 当共享上下文中出现这些标签的发现时，由重规划动态追加该能力
    triggers: List[str] = []

    @abstractmethod
    def analyze(self, ctx: SharedContext) -> List[Finding]:
        """分析共享上下文，返回本次新增的发现。"""
        raise NotImplementedError
