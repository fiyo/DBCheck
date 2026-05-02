# -*- coding: utf-8 -*-
"""
DBCheck License Module
许可证模块 — 包含验证器和命令行工具
"""

from .validator import (
    LicenseValidator,
    LicenseValidator as LicenseManager,
    get_validator,
    get_validator as get_license_manager,
    is_pro,
    get_edition,
    DEFAULT_SECRET_KEY,
)

__all__ = [
    "LicenseValidator",
    "LicenseManager",
    "get_validator",
    "get_license_manager",
    "is_pro",
    "get_edition",
    "DEFAULT_SECRET_KEY",
]
