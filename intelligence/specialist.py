# -*- coding: utf-8 -*-
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

    @abstractmethod
    def analyze(self, ctx: SharedContext) -> List[Finding]:
        """分析共享上下文，返回本次新增的发现。"""
        raise NotImplementedError
