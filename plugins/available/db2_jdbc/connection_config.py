#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Db2 连接配置数据类 + JDBC URL 构建器。

字段对齐 web_ui / inspection_dal 透传给插件的实例字典
（database / jdbc_url / ssl 等可选）。
SSL 参数不拼入 JDBC URL，而是通过 build_properties() 返回的字典传递，
由 main_plugin 转换为 java.util.Properties 后交给 DriverManager，
与 MongoDB 插件的 TLS 设计保持一致（更安全、更灵活）。
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class Db2ConnectionConfig:
    """Db2 JDBC 连接配置。

    Attributes:
        host: Db2 服务器主机名或 IP
        port: Db2 实例端口（默认 50000）
        user: 登录用户名（默认 db2inst1）
        password: 登录密码
        database: 目标数据库名（注意：Db2 对数据库名大小写敏感，现场验证小写可用）
        jdbc_url: 完整 JDBC URL（可选；以 jdbc:db2 开头则直接透传，不做拼接）
        ssl: 是否启用 SSL/TLS 连接
        ssl_truststore: 信任库（TrustStore）文件路径
        ssl_truststore_password: 信任库密码
        ssl_keystore: 密钥库（KeyStore）文件路径
        ssl_keystore_password: 密钥库密码
        ssl_cipher_suites: 启用的加密套件（逗号分隔，可选）
        connect_timeout_ms: 连接超时（毫秒，可选）
    """

    host: str = "127.0.0.1"
    port: int = 50000
    user: str = "db2inst1"
    password: str = ""
    database: str = "testdb"
    jdbc_url: str = ""
    ssl: bool = False
    ssl_truststore: str = ""
    ssl_truststore_password: str = ""
    ssl_keystore: str = ""
    ssl_keystore_password: str = ""
    ssl_cipher_suites: str = ""
    connect_timeout_ms: int = 10000

    def build_jdbc_url(self) -> str:
        """构建 JDBC URL。

        - 若 jdbc_url 以 'jdbc:db2' 开头：原样透传（支持自定义属性 / z/OS 等）
        - 否则按标准格式拼接： jdbc:db2://{host}:{port}/{database}

        Returns:
            可用的 JDBC 连接 URL 字符串
        """
        if self.jdbc_url and str(self.jdbc_url).strip().lower().startswith("jdbc:db2"):
            return self.jdbc_url.strip()
        return f"jdbc:db2://{self.host}:{self.port}/{self.database}"

    def build_properties(self) -> Dict[str, str]:
        """构建 JDBC 连接属性字典。

        返回 Python dict；main_plugin 负责转换为 java.util.Properties 并
        与 user/password 一并交给 DriverManager.getConnection(url, props)。

        SSL 相关属性（sslConnection / sslTrustStoreLocation 等）仅在
        ssl=True 时加入，与 MongoDB 插件 TLS 设计保持一致。

        Returns:
            连接属性字典（含 user / password 及可选 SSL 属性）
        """
        props: Dict[str, str] = {
            "user": self.user,
            "password": self.password,
        }
        if self.connect_timeout_ms and self.connect_timeout_ms > 0:
            # Db2 JDBC 客户端登录超时（秒）
            props["loginTimeout"] = str(max(1, self.connect_timeout_ms // 1000))

        if self.ssl:
            props["sslConnection"] = "true"
            if self.ssl_truststore:
                props["sslTrustStoreLocation"] = self.ssl_truststore
            if self.ssl_truststore_password:
                props["sslTrustStorePassword"] = self.ssl_truststore_password
            if self.ssl_keystore:
                props["sslKeyStoreLocation"] = self.ssl_keystore
            if self.ssl_keystore_password:
                props["sslKeyStorePassword"] = self.ssl_keystore_password
            if self.ssl_cipher_suites:
                props["sslCipherSuites"] = self.ssl_cipher_suites

        return props

    @classmethod
    def from_instance(cls, inst: dict) -> "Db2ConnectionConfig":
        """从 web_ui / inspection_dal 透传的实例字典构建配置。

        Args:
            inst: 含 host/port/user/password/database/jdbc_url/ssl 等键的字典
                  （缺失键使用默认值，向后兼容）

        Returns:
            Db2ConnectionConfig 实例
        """
        if not inst:
            inst = {}

        def _get_str(key: str, default: str = "") -> str:
            val = inst.get(key, default)
            return str(val) if val is not None else default

        def _get_bool(key: str, default: bool = False) -> bool:
            val = inst.get(key, default)
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.strip().lower() in ("true", "1", "yes", "on")
            return bool(val)

        def _get_int(key: str, default: int) -> int:
            try:
                return int(inst.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            host=_get_str("host", "127.0.0.1") or "127.0.0.1",
            port=_get_int("port", 50000) or 50000,
            user=_get_str("user", "db2inst1") or "db2inst1",
            password=_get_str("password", ""),
            database=_get_str("database", "testdb") or "testdb",
            jdbc_url=_get_str("jdbc_url", ""),
            ssl=_get_bool("ssl", False),
            ssl_truststore=_get_str("ssl_truststore", ""),
            ssl_truststore_password=_get_str("ssl_truststore_password", ""),
            ssl_keystore=_get_str("ssl_keystore", ""),
            ssl_keystore_password=_get_str("ssl_keystore_password", ""),
            ssl_cipher_suites=_get_str("ssl_cipher_suites", ""),
            connect_timeout_ms=_get_int("connect_timeout_ms", 10000),
        )

    def __repr__(self) -> str:
        """安全的字符串表示（隐藏密码）。"""
        safe_pass = "***" if self.password else ""
        return (
            f"Db2ConnectionConfig(host={self.host!r}, port={self.port}, "
            f"database={self.database!r}, user={self.user!r}, "
            f"jdbc_url={self.jdbc_url!r}, ssl={self.ssl}, "
            f"password={safe_pass!r})"
        )
