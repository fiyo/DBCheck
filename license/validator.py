# -*- coding: utf-8 -*-
"""
DBCheck License Validator
许可证验证模块（供运行时使用）
"""

import hashlib
import json
import os
import socket
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

# License types
LICENSE_TYPES = {
    "trial": {"max_instances": 3, "days": 14, "features": ["basic"]},
    "per_instance": {"max_instances": 1, "days": 365, "features": ["basic", "security", "compliance"]},
    "per_team": {"max_instances": 50, "days": 365, "features": ["basic", "security", "compliance", "advanced"]},
    "enterprise": {"max_instances": -1, "days": 365, "features": ["all"]},
}

# 私钥（正式发布前替换为销售系统生成的私钥，务必保密）
DEFAULT_SECRET_KEY = "DBCheck-Pro-SecretKey-2025"


class LicenseValidator:
    """许可证验证器"""

    def __init__(self, license_file: str = "license.key",
                 secret_key: str = DEFAULT_SECRET_KEY):
        self.license_file = license_file
        self.secret_key = secret_key
        self.license_data: Optional[Dict[str, Any]] = None
        self._load_license()

    def _load_license(self) -> bool:
        """加载已激活的许可证文件"""
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
        mac = uuid.getnode()
        hostname = socket.gethostname()
        try:
            hostname = socket.getfqdn()
        except Exception:
            pass
        raw = "%s-%s" % (mac, hostname)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _verify_signature(self, signature: str, payload_json: str) -> bool:
        """验证 HMAC-SHA256 签名"""
        if not signature or len(signature) < 16:
            return False
        expected = hashlib.sha256(
            (payload_json + self.secret_key).encode()
        ).hexdigest()[:16]
        return signature == expected

    def activate(self, license_key: str) -> Dict[str, Any]:
        """激活许可证"""
        validation = self._validate_key(license_key)
        if not validation["valid"]:
            return validation

        activation = {
            "license_key": license_key,
            "machine_id": self._generate_machine_id(),
            "activated_at": datetime.now().isoformat(),
            "expires": validation["expires"],
            "type": validation["type"],
            "max_instances": validation["max_instances"],
            "features": validation["features"],
        }

        try:
            with open(self.license_file, "w", encoding="utf-8") as f:
                json.dump(activation, f, indent=2, ensure_ascii=False)
            self.license_data = activation
            return {"success": True, "message": "许可证激活成功", "data": activation}
        except Exception as e:
            return {"success": False, "message": "激活失败: %s" % str(e)}

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
            expires = datetime.fromisoformat(self.license_data["expires"])
            if expires < datetime.now():
                return {
                    "valid": False,
                    "edition": "pro",
                    "message": "许可证已过期",
                    "features": self.license_data.get("features", []),
                }
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
                "message": "验证失败: %s" % str(e),
                "features": ["basic"],
            }

    def is_pro(self) -> bool:
        """检查是否为专业版"""
        return self.verify().get("valid", False)

    def get_edition(self) -> str:
        """获取当前版本"""
        return self.verify().get("edition", "community")

    def get_features(self) -> list:
        """获取可用功能列表"""
        return self.verify().get("features", ["basic"])

    def has_feature(self, feature: str) -> bool:
        """检查是否拥有某功能"""
        features = self.get_features()
        return "all" in features or feature in features

    def deactivate(self) -> Dict[str, Any]:
        """注销许可证"""
        if os.path.exists(self.license_file):
            try:
                os.remove(self.license_file)
                self.license_data = None
                return {"success": True, "message": "许可证已注销"}
            except Exception as e:
                return {"success": False, "message": "注销失败: %s" % str(e)}
        return {"success": False, "message": "没有找到许可证文件"}

    # ── 内部验证逻辑 ────────────────────────────────────────

    def _validate_key(self, license_key: str) -> Dict[str, Any]:
        """验证许可证密钥有效性"""
        result = {
            "valid": False, "type": None, "expires": None,
            "max_instances": 0, "features": [], "message": "",
        }

        try:
            # 格式: BASE64(payload).HMAC16
            if "." not in license_key:
                result["message"] = "许可证格式无效"
                return result

            token, sig = license_key.rsplit(".", 1)
            if len(sig) != 16:
                result["message"] = "许可证签名无效"
                return result

            # 1. 先验证签名（对原始 token 签名，不需要解码）
            if not self._verify_signature(sig, token):
                result["message"] = "许可证签名验证失败"
                return result

            # 2. 解码并解析 payload
            try:
                import base64 as _b64
                payload_json = _b64.b64decode(token.encode()).decode()
                payload = json.loads(payload_json)
            except Exception:
                result["message"] = "许可证数据损坏"
                return result

            license_type = payload.get("type", "")
            if license_type not in LICENSE_TYPES:
                result["message"] = "未知的许可证类型"
                return result

            expires_str = payload.get("expiry", "")
            if expires_str:
                expires_date = datetime.fromisoformat(expires_str)
                if expires_date < datetime.now():
                    result["message"] = "许可证已过期"
                    return result
                result["expires"] = expires_str
            else:
                result["expires"] = None

            type_info = LICENSE_TYPES[license_type]
            result["valid"] = True
            result["type"] = license_type
            result["max_instances"] = type_info["max_instances"]
            result["features"] = type_info["features"]
            result["message"] = "许可证有效"

        except Exception as e:
            result["message"] = "验证失败: %s" % str(e)

        return result


# 全局单例
_validator: Optional[LicenseValidator] = None


def get_validator(license_file: str = "license.key",
                   secret_key: str = DEFAULT_SECRET_KEY) -> LicenseValidator:
    """获取验证器单例"""
    global _validator
    if _validator is None:
        _validator = LicenseValidator(license_file, secret_key)
    return _validator


def is_pro() -> bool:
    """快捷函数：检查是否为专业版"""
    return get_validator().is_pro()


def get_edition() -> str:
    """快捷函数：获取当前版本"""
    return get_validator().get_edition()
