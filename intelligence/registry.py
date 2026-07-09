# -*- coding: utf-8 -*-
"""专家能力注册表。"""

from __future__ import annotations

from typing import Dict, List, Optional

from .context import Finding
from .specialist import Specialist


class SpecialistRegistry:
    def __init__(self) -> None:
        self._by_id: Dict[str, Specialist] = {}

    def register(self, spec: Specialist) -> None:
        self._by_id[spec.id] = spec

    def get(self, sid: str) -> Optional[Specialist]:
        return self._by_id.get(sid)

    def all(self) -> List[Specialist]:
        return list(self._by_id.values())

    def ids(self) -> List[str]:
        return list(self._by_id.keys())


registry = SpecialistRegistry()
