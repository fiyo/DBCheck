# -*- coding: utf-8 -*-
"""
DBCheck Pro License Manager
专业版许可证管理模块
核心验证逻辑已迁移至 license/validator.py
"""

# 从独立的 license 模块导入，保持接口兼容
from license.validator import (
    LicenseValidator as _LicenseValidator,
    get_validator,
    is_pro,
    get_edition,
    LICENSE_TYPES,
)
from typing import Optional, Dict, Any

# 保持向后兼容的别名
LicenseManager = _LicenseValidator


def get_license_manager() -> _LicenseValidator:
    """获取许可证管理器单例（向后兼容）"""
    return get_validator()


__all__ = [
    "LicenseManager",
    "get_license_manager",
    "get_validator",
    "is_pro",
    "get_edition",
    "LICENSE_TYPES",
]
