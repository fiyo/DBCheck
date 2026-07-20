#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MongoDB 版本适配层。

根据 MongoDB 版本号判断支持的参数和功能，用于：
  - 基线检查时选择正确的参数名（如 7.0+ 用 slowQuerySampler 替代 slowOpThresholdMs）
  - 采集时判断命令是否可用，不可用时降级处理
  - 4.x 标记为 unsupported，打印 [WARN] 但仍采集基础数据

版本约定：
  - 5.0+: supported=True
  - 4.x:  supported=False（仅采基础数据，不执行基线检查）
  - 3.x 及更早: supported=False
"""

import re
from typing import Tuple


class MongoVersionAdapter:
    """MongoDB 版本适配器。

    解析版本字符串并提供版本感知的功能判断。

    Attributes:
        major: 主版本号
        minor: 次版本号
        raw: 原始版本字符串
    """

    # 最低完全支持的版本（5.0+）
    MIN_SUPPORTED_MAJOR = 5

    def __init__(self, version_str: str):
        """初始化版本适配器。

        Args:
            version_str: MongoDB 版本字符串，如 "7.0.12"、"6.0.3"、"4.4.18"
        """
        self.raw = version_str or "0.0.0"
        self.major, self.minor = self._parse(self.raw)

    @staticmethod
    def _parse(version_str: str) -> Tuple[int, int]:
        """解析版本字符串为 (major, minor) 元组。

        支持格式："7.0.12"、"6.0"、"4.4.18-ent"、等。
        解析失败时返回 (0, 0)。

        Args:
            version_str: 版本字符串

        Returns:
            (major, minor) 元组
        """
        if not version_str:
            return (0, 0)
        # 提取数字部分：如 "7.0.12-ent" → [7, 0, 12]
        match = re.match(r"(\d+)(?:\.(\d+))?", str(version_str).strip())
        if not match:
            return (0, 0)
        major = int(match.group(1))
        minor = int(match.group(2)) if match.group(2) else 0
        return (major, minor)

    @property
    def supported(self) -> bool:
        """是否为完全支持的版本（5.0+）。

        4.x 及以下版本返回 False，调用方应打印 [WARN] 并跳过基线检查。
        """
        return self.major >= self.MIN_SUPPORTED_MAJOR

    @property
    def is_unsupported(self) -> bool:
        """是否为不支持的版本（4.x 及以下）。"""
        return not self.supported

    def get_slow_op_param(self) -> str:
        """获取慢操作阈值参数名。

        - 7.0+: 返回 "slowQuerySampler"（7.0 引入了新的采样器参数）
        - 5.0~6.x: 返回 "slowOpThresholdMs"
        - 4.x 及以下: 返回 "slowOpThresholdMs"（但仍可能不支持 getParameter）

        Returns:
            慢操作阈值参数名字符串
        """
        if self.major >= 7:
            return "slowQuerySampler"
        return "slowOpThresholdMs"

    def supports_param(self, param: str) -> bool:
        """判断当前版本是否支持指定的 getParameter 参数。

        Args:
            param: 参数名，如 "slowOpThresholdMs"、"slowQuerySampler"、
                   "writeConcernMajorityJournalDefault" 等

        Returns:
            True 如果版本支持该参数
        """
        # 7.0+ 新增参数
        if param == "slowQuerySampler":
            return self.major >= 7
        # 7.0+ 废弃参数（被 slowQuerySampler 替代）
        if param == "slowOpThresholdMs":
            return self.major < 7
        # 以下参数在 5.0+ 均支持
        if param in (
            "authenticationMechanisms",
            "enableLocalhostAuthBypass",
            "writeConcernMajorityJournalDefault",
            "wiredTigerCacheSizeGB",
            "javascriptEnabled",
            "clusterAuthMode",
            "logLevel",
            "tlsMode",
            "auditLogDestination",
            "maxConnections",
            "networkMessageCompressors",
            "diagnosticDataCollectionEnabled",
            "enableMajorityReadConcern",
            "traceExceptions",
            "ttlMonitorEnabled",
            "textSearchEnabled",
            "auditLogDestination",
        ):
            return self.supported
        # 默认：支持版本以上认为支持
        return self.supported

    def version_label(self) -> str:
        """返回可读的版本标签字符串。"""
        if self.is_unsupported:
            return f"{self.major}.{self.minor} (unsupported)"
        return f"{self.major}.{self.minor}"

    def __repr__(self) -> str:
        return (
            f"MongoVersionAdapter(major={self.major}, minor={self.minor}, "
            f"supported={self.supported}, raw={self.raw!r})"
        )
