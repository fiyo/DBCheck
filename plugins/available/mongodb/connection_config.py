#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MongoDB 连接配置数据类 + URI 构建器。

支持标准连接 (mongodb://) 和 SRV 连接 (mongodb+srv://)，
支持 TLS/SSL、副本集、认证源、认证机制等高级参数。

设计原则：
  - TLS 参数不拼入 URI，通过 MongoClient kwargs 传递（更安全、更灵活）
  - authSource / authMechanism / replicaSet 拼入 URI query string
  - SRV 模式下 port 被忽略（由 DNS SRV 记录决定）
"""

from dataclasses import dataclass, field
from urllib.parse import quote_plus
from typing import Dict


@dataclass
class MongoConnectionConfig:
    """MongoDB 连接配置数据类。

    Attributes:
        host: MongoDB 服务器主机名或 IP 地址
        port: MongoDB 服务端口（SRV 模式下被忽略）
        user: 认证用户名（空字符串表示无认证）
        password: 认证密码
        database: 连接的默认数据库
        connect_mode: 连接模式，"standard" 或 "srv"
        auth_source: 认证源数据库
        auth_mechanism: 认证机制，如 "SCRAM-SHA-256"、"SCRAM-SHA-1"
        replica_set: 副本集名称（可选）
        tls: 是否启用 TLS/SSL
        tls_ca_file: CA 证书文件路径
        tls_cert_key_file: 客户端证书+私钥文件路径
        tls_allow_invalid_certs: 是否允许无效证书（仅用于测试环境）
    """

    host: str = "127.0.0.1"
    port: int = 27017
    user: str = ""
    password: str = ""
    database: str = "admin"
    connect_mode: str = "standard"  # "standard" | "srv"
    auth_source: str = "admin"
    auth_mechanism: str = ""  # "SCRAM-SHA-256" | "SCRAM-SHA-1" | ""
    replica_set: str = ""
    tls: bool = False
    tls_ca_file: str = ""
    tls_cert_key_file: str = ""
    tls_allow_invalid_certs: bool = False

    @classmethod
    def from_ssh_info(cls, ssh_info: dict) -> "MongoConnectionConfig":
        """从 ssh_info 字典构建连接配置。

        ssh_info 由 web_ui.py 透传，包含 MongoDB 专用参数和（可选）SSH 隧道参数。
        向后兼容：缺失的字段使用默认值。

        Args:
            ssh_info: 包含连接参数的字典，可包含以下键：
                - database: 连接的默认数据库
                - connect_mode: "standard" 或 "srv"
                - auth_source: 认证源
                - auth_mechanism: 认证机制
                - replica_set: 副本集名称
                - tls: 是否启用 TLS
                - tls_ca_file: CA 证书路径
                - tls_cert_key_file: 客户端证书路径
                - tls_allow_invalid_certs: 是否允许无效证书

        Returns:
            MongoConnectionConfig 实例（host/port/user/password 由外部填充）
        """
        if not ssh_info:
            ssh_info = {}

        def _get_str(key: str, default: str = "") -> str:
            val = ssh_info.get(key, default)
            return str(val) if val is not None else default

        def _get_bool(key: str, default: bool = False) -> bool:
            val = ssh_info.get(key, default)
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes", "on")
            return bool(val)

        return cls(
            database=_get_str("database", "admin") or "admin",
            connect_mode=_get_str("connect_mode", "standard") or "standard",
            auth_source=_get_str("auth_source", "admin") or "admin",
            auth_mechanism=_get_str("auth_mechanism", ""),
            replica_set=_get_str("replica_set", ""),
            tls=_get_bool("tls", False),
            tls_ca_file=_get_str("tls_ca_file", ""),
            tls_cert_key_file=_get_str("tls_cert_key_file", ""),
            tls_allow_invalid_certs=_get_bool("tls_allow_invalid_certs", False),
        )

    def build_uri(self) -> str:
        """构建 MongoDB 连接 URI。

        - 标准模式: mongodb://[user:pass@]host:port/database?params
        - SRV 模式:  mongodb+srv://[user:pass@]host/database?params（port 被忽略）

        TLS 参数不拼入 URI，通过 build_client_kwargs() 返回。

        Returns:
            完整的 MongoDB 连接 URI 字符串
        """
        scheme = "mongodb+srv://" if self.connect_mode == "srv" else "mongodb://"

        # 认证部分
        auth_part = ""
        if self.user:
            encoded_user = quote_plus(self.user)
            if self.password:
                encoded_pass = quote_plus(self.password)
                auth_part = f"{encoded_user}:{encoded_pass}@"
            else:
                auth_part = f"{encoded_user}@"

        # 主机部分
        if self.connect_mode == "srv":
            # SRV 模式不包含端口
            host_part = self.host
        else:
            host_part = f"{self.host}:{self.port}"

        # 数据库部分
        db_part = f"/{self.database}" if self.database else "/"

        # Query 参数（authSource / authMechanism / replicaSet）
        params = []
        if self.auth_source and self.auth_source != "admin":
            params.append(f"authSource={quote_plus(self.auth_source)}")
        elif self.user and self.auth_source:
            # 有认证时总是带上 authSource
            params.append(f"authSource={quote_plus(self.auth_source)}")

        if self.auth_mechanism:
            params.append(f"authMechanism={quote_plus(self.auth_mechanism)}")

        if self.replica_set:
            params.append(f"replicaSet={quote_plus(self.replica_set)}")

        query_part = "?" + "&".join(params) if params else ""

        return f"{scheme}{auth_part}{host_part}{db_part}{query_part}"

    def build_client_kwargs(self) -> Dict:
        """构建 MongoClient 额外参数。

        TLS/SSL 参数通过 kwargs 传递，不拼入 URI。
        同时设置合理的超时参数。

        Returns:
            传递给 pymongo.MongoClient 的 kwargs 字典
        """
        kwargs: Dict = {
            "serverSelectionTimeoutMS": 5000,
            "connectTimeoutMS": 5000,
            "socketTimeoutMS": 10000,
        }

        if self.tls:
            kwargs["tls"] = True
            if self.tls_ca_file:
                kwargs["tlsCAFile"] = self.tls_ca_file
            if self.tls_cert_key_file:
                kwargs["tlsCertificateKeyFile"] = self.tls_cert_key_file
            if self.tls_allow_invalid_certs:
                kwargs["tlsAllowInvalidCertificates"] = True

        return kwargs

    def __repr__(self) -> str:
        """安全的字符串表示（隐藏密码）"""
        safe_pass = "***" if self.password else ""
        return (
            f"MongoConnectionConfig(host={self.host!r}, port={self.port}, "
            f"database={self.database!r}, connect_mode={self.connect_mode!r}, "
            f"auth_source={self.auth_source!r}, auth_mechanism={self.auth_mechanism!r}, "
            f"replica_set={self.replica_set!r}, tls={self.tls}, "
            f"user={self.user!r}, password={safe_pass!r})"
        )
