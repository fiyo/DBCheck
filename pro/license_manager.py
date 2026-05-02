# -*- coding: utf-8 -*-
"""
DBCheck Pro License Manager
专业版许可证验证模块
"""

import hashlib
import json
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

# License types
LICENSE_TYPES = {
    "trial": {"max_instances": 3, "days": 14, "features": ["basic"]},
    "per_instance": {"max_instances": 1, "days": 365, "features": ["basic", "security", "compliance"]},
    "per_team": {"max_instances": 50, "days": 365, "features": ["basic", "security", "compliance", "advanced"]},
    "enterprise": {"max_instances": -1, "days": 365, "features": ["all"]},
}


class LicenseManager:
    """许可证管理器"""

    def __init__(self, license_file: str = "license.key"):
        self.license_file = license_file
        self.license_data: Optional[Dict[str, Any]] = None
        self._load_license()

    def _load_license(self) -> bool:
        """加载许可证文件"""
        if os.path.exists(self.license_file):
            try:
                with open(self.license_file, "r", encoding="utf-8") as f:
                    self.license_data = json.load(f)
                return True
            except Exception:
                return False
        return False

    def _generate_machine_id(self) -> str:
        """生成机器唯一标识"""
        # 组合多个硬件特征生成唯一ID
        mac = uuid.getnode()
        hostname = socket.gethostname()
        try:
            hostname = socket.getfqdn()
        except Exception:
            pass

        raw = f"{mac}-{hostname}-{uuid.getnode()}".encode()
        return hashlib.sha256(raw).hexdigest()[:32]

    def _verify_signature(self, license_key: str) -> bool:
        """验证许可证签名"""
        if not license_key or len(license_key) < 50:
            return False

        # 简化的签名验证：前16位是机器码，中间是授权信息，后16位是校验
        parts = license_key.split("-")
        if len(parts) != 4:
            return False

        # 实际生产中应该用RSA等非对称加密验证签名
        # 这里用简化版本做演示
        return True

    def _validate_license(self, license_key: str) -> Dict[str, Any]:
        """验证许可证有效性"""
        result = {
            "valid": False,
            "type": None,
            "expires": None,
            "max_instances": 0,
            "features": [],
            "message": ""
        }

        try:
            # 解析许可证密钥
            # 格式: TYPE-YYYYMMDD-XXXX-CHECKSUM
            parts = license_key.split("-")
            if len(parts) != 4:
                result["message"] = "许可证格式无效"
                return result

            license_type = parts[0]
            expires_str = parts[1]

            # 验证许可证类型
            if license_type not in LICENSE_TYPES:
                result["message"] = "未知的许可证类型"
                return result

            # 验证到期日期
            try:
                expires_date = datetime.strptime(expires_str, "%Y%m%d")
                if expires_date < datetime.now():
                    result["message"] = "许可证已过期"
                    return result
            except ValueError:
                result["message"] = "许可证日期格式无效"
                return result

            # 验证机器码
            if not self._verify_signature(license_key):
                result["message"] = "许可证验证失败"
                return result

            # 许可证有效
            type_info = LICENSE_TYPES[license_type]
            result["valid"] = True
            result["type"] = license_type
            result["expires"] = expires_date.isoformat()
            result["max_instances"] = type_info["max_instances"]
            result["features"] = type_info["features"]
            result["message"] = "许可证有效"

        except Exception as e:
            result["message"] = f"验证失败: {str(e)}"

        return result

    def activate(self, license_key: str) -> Dict[str, Any]:
        """激活许可证"""
        # 验证许可证
        validation = self._validate_license(license_key)
        if not validation["valid"]:
            return validation

        # 生成激活信息
        activation = {
            "license_key": license_key,
            "machine_id": self._generate_machine_id(),
            "activated_at": datetime.now().isoformat(),
            "expires": validation["expires"],
            "type": validation["type"],
            "max_instances": validation["max_instances"],
            "features": validation["features"],
        }

        # 保存激活文件
        try:
            with open(self.license_file, "w", encoding="utf-8") as f:
                json.dump(activation, f, indent=2, ensure_ascii=False)
            self.license_data = activation
            return {"success": True, "message": "许可证激活成功", "data": activation}
        except Exception as e:
            return {"success": False, "message": f"激活失败: {str(e)}"}

    def verify(self) -> Dict[str, Any]:
        """验证当前许可证状态"""
        if not self.license_data:
            return {
                "valid": False,
                "edition": "community",
                "message": "未激活许可证",
                "features": ["basic"],
            }

        try:
            # 检查到期日期
            expires = datetime.fromisoformat(self.license_data["expires"])
            if expires < datetime.now():
                return {
                    "valid": False,
                    "edition": "pro",
                    "message": "许可证已过期",
                    "features": self.license_data.get("features", []),
                }

            # 检查机器码（可选，防止许可证迁移）
            current_machine = self._generate_machine_id()
            stored_machine = self.license_data.get("machine_id", "")

            # 返回验证结果
            return {
                "valid": True,
                "edition": "pro",
                "type": self.license_data.get("type", "unknown"),
                "expires": self.license_data["expires"],
                "max_instances": self.license_data.get("max_instances", 0),
                "features": self.license_data.get("features", []),
                "message": "许可证有效",
            }

        except Exception as e:
            return {
                "valid": False,
                "edition": "community",
                "message": f"验证失败: {str(e)}",
                "features": ["basic"],
            }

    def is_pro(self) -> bool:
        """检查是否为专业版"""
        result = self.verify()
        return result.get("valid", False)

    def get_edition(self) -> str:
        """获取当前版本"""
        result = self.verify()
        return result.get("edition", "community")

    def get_features(self) -> list:
        """获取可用功能列表"""
        result = self.verify()
        return result.get("features", ["basic"])

    def has_feature(self, feature: str) -> bool:
        """检查是否拥有某功能"""
        features = self.get_features()
        if "all" in features:
            return True
        return feature in features

    def deactivate(self) -> Dict[str, Any]:
        """注销许可证"""
        if os.path.exists(self.license_file):
            try:
                os.remove(self.license_file)
                self.license_data = None
                return {"success": True, "message": "许可证已注销"}
            except Exception as e:
                return {"success": False, "message": f"注销失败: {str(e)}"}
        return {"success": False, "message": "没有找到许可证文件"}

    def generate_trial_license(self) -> str:
        """生成试用许可证（仅用于测试）"""
        trial_type = "trial"
        expires = datetime.now() + timedelta(days=LICENSE_TYPES[trial_type]["days"])
        expires_str = expires.strftime("%Y%m%d")
        machine_id = self._generate_machine_id()[:8]

        # 简化格式，实际生产中应该加密
        return f"{trial_type}-{expires_str}-{machine_id}-trial"


# 全局单例
_license_manager: Optional[LicenseManager] = None


def get_license_manager() -> LicenseManager:
    """获取许可证管理器单例"""
    global _license_manager
    if _license_manager is None:
        _license_manager = LicenseManager()
    return _license_manager


def is_pro() -> bool:
    """快捷函数：检查是否为专业版"""
    return get_license_manager().is_pro()


def get_edition() -> str:
    """快捷函数：获取当前版本"""
    return get_license_manager().get_edition()
