# -*- coding: utf-8 -*-
"""
DBCheck Pro Module
专业版核心模块
"""

# License removed - all Pro features are now free
def is_pro():
    """Always return True - Pro features are now free in community edition"""
    return True

def get_edition():
    """Return edition name"""
    return 'community+'

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
from .backup import (
    BackupManager,
    BackupResult,
    get_backup_manager,
)

__all__ = [
    # Pro status (no license required)
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
    # Backup
    "BackupManager",
    "BackupResult",
    "get_backup_manager",
]
