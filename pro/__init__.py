# -*- coding: utf-8 -*-
"""
DBCheck Pro Module
专业版核心模块
"""

from .license_manager import (
    LicenseManager,
    get_license_manager,
    is_pro,
    get_edition,
)
from .instance_manager import (
    InstanceManager,
    DatabaseInstance,
    InstanceGroup,
    get_instance_manager,
)
from .report_score import (
    ReportScorer,
    ScoreReport,
    ScoreItem,
    InspectionDataScorer,
    format_score_report,
)
from .rule_engine import (
    RuleEngine,
    get_rule_engine,
)

__all__ = [
    # License
    "LicenseManager",
    "get_license_manager",
    "is_pro",
    "get_edition",
    # Instance
    "InstanceManager",
    "DatabaseInstance",
    "InstanceGroup",
    "get_instance_manager",
    # Report Score
    "ReportScorer",
    "ScoreReport",
    "ScoreItem",
    "InspectionDataScorer",
    "format_score_report",
    # Rule Engine
    "RuleEngine",
    "get_rule_engine",
]
