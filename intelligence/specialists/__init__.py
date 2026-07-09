# -*- coding: utf-8 -*-
"""专家能力清单：注册所有内置专家能力。"""

from __future__ import annotations

from ..registry import registry
from .monitor_sentinel import MonitorSentinel
from .inspection_expert import InspectionExpert
from .rootcause_expert import RootCauseExpert
from .sql_governance import SqlGovernance
from .lock_analyst import LockAnalyst

_registered = False


def register_all() -> None:
    global _registered
    if _registered:
        return
    for s in (
        MonitorSentinel(),
        InspectionExpert(),
        RootCauseExpert(),
        SqlGovernance(),
        LockAnalyst(),
    ):
        registry.register(s)
    _registered = True
