# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

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

    def list_by_domain(self, domain: str) -> List["Specialist"]:
        """返回指定专业域的全部已注册能力。"""
        return [s for s in self._by_id.values() if getattr(s, "domain", "") == domain]

    def triggered_by(self, tags: set) -> List["Specialist"]:
        """返回 triggers 与给定标签集合有交集的能力（用于重规划动态追加）。"""
        out: List["Specialist"] = []
        for s in self._by_id.values():
            triggers = getattr(s, "triggers", None) or []
            if triggers and (set(triggers) & tags):
                out.append(s)
        return out


registry = SpecialistRegistry()
