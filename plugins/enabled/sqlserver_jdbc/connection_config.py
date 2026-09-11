#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
SQL Server 连接配置数据类 + JDBC URL / Properties 构建器。

字段对齐 web_ui / inspection_dal 透传给插件的实例字典
（database / jdbc_url / instance_name / encrypt / trust_server_certificate 等可选）。

设计要点：
  - JDBC URL 通过 build_jdbc_url() 拼接（支持 jdbc_url 透传与字段拼接两种模式）
  - 加密/连接超时等参数走 build_properties() 返回的字典（main_plugin 转换为
    java.util.Properties 后交给 DriverManager.getConnection(url, props)）
  - 命名实例（host\\instance）通过 instance_name 字段表达，URL 中
    `instanceName=xxx` 替代 `port=1433`
  - 默认 encrypt=false + trustServerCertificate=true（兼容多数内网/旧版 SQL
    Server，避免无 CA 证书时握手失败；有合法 CA 环境可显式传入 encrypt=true）
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class MssqlJdbcConnectionConfig:
    """SQL Server JDBC 连接配置。

    Attributes:
        host: SQL Server 主机名或 IP
        port: 端口（默认实例 1433；命名实例可留空，URL 自动用 instanceName 替代）
        user: 登录用户名（默认 sa）
        password: 登录密码
        database: 目标数据库名（默认 master）
        instance_name: 命名实例名（默认实例留空；非空时 URL 用 instanceName=xxx 替代端口）
        jdbc_url: 完整 JDBC URL（可选；以 jdbc:sqlserver:// 开头则直接透传）
        encrypt: 启用 TLS 加密（默认 false，兼容多数内网/旧版 SQL Server；有 CA 证书环境可设为 true）
        trust_server_certificate: 信任自签证书（dev 友好，prod 应配置 CA）
        login_timeout_s: 登录超时（秒）
        application_name: 应用标识（DMV sys.dm_exec_sessions.program_name 可见）
    """

    host: str = "127.0.0.1"
    port: int = 1433
    user: str = "sa"
    password: str = ""
    database: str = "master"
    instance_name: str = ""
    jdbc_url: str = ""
    encrypt: bool = False
    trust_server_certificate: bool = True
    login_timeout_s: int = 10
    application_name: str = "DBCheck"

    def build_jdbc_url(self) -> str:
        """构建 JDBC URL。

        优先级：
          1. 若 jdbc_url 以 'jdbc:sqlserver://' 开头 → 原样透传
          2. 若 instance_name 非空 → jdbc:sqlserver://{host};instanceName={instance};...
          3. 否则 → jdbc:sqlserver://{host}:{port};databaseName={database};...

        安全/兼容性要点：
          - encrypt=false 时**不附加** trustServerCertificate，避免 mssql-jdbc
            在旧版/强制加密服务器上进入异常 SSL 握手分支而报 unexpected_message。
          - encrypt=true 时附加 trustServerCertificate + sslProtocol=TLS，兼容
            自签证书；TLS 版本交由 JVM 协商（见 modules/jdbc_connector._jvm_tls_args），
            以兼容仅支持 TLS 1.0 的老旧 SQL Server。
          - 固定 authentication=NotSpecified，显式使用 SQL 认证，避免驱动尝试
            Windows/Kerberos 集成认证回退。

        Returns:
            可用的 JDBC 连接 URL 字符串
        """
        # 统一连接层：基础连接串 + 专属扩展段（透传/实例名/encrypt/trust/
        # loginTimeout/applicationName/认证方式）均由 modules.jdbc_connector
        # build_jdbc_url 生成，本插件不再手拼（避免双重拼接）。
        from modules.jdbc_connector import build_jdbc_url as _build_jdbc_url
        return _build_jdbc_url(
            'sqlserver_jdbc', self.host, self.port,
            database=self.database,
            encrypt=bool(self.encrypt),
            trust_server_certificate=bool(self.trust_server_certificate),
            jdbc_url=self.jdbc_url,
            instance_name=self.instance_name,
            login_timeout_s=self.login_timeout_s,
            application_name=self.application_name,
        )

    def build_properties(self) -> Dict[str, str]:
        """构建 JDBC 连接属性字典。

        返回 Python dict；main_plugin 负责转换为 java.util.Properties 并
        与 user/password 一并交给 DriverManager.getConnection(url, props)。

        Returns:
            连接属性字典（含 user / password）
        """
        props: Dict[str, str] = {
            "user": self.user,
            "password": self.password,
        }
        return props

    @classmethod
    def from_instance(cls, inst: dict) -> "MssqlJdbcConnectionConfig":
        """从 web_ui / inspection_dal 透传的实例字典构建配置。

        Args:
            inst: 含 host/port/user/password/database/instance_name/jdbc_url/
                  encrypt/trust_server_certificate 等键的字典（缺失键使用默认值，
                  向后兼容）

        Returns:
            MssqlJdbcConnectionConfig 实例
        """
        if not inst:
            inst = {}

        def _get_str(key: str, default: str = "") -> str:
            val = inst.get(key, default)
            return str(val) if val is not None else default

        def _get_bool(key: str, default: bool) -> bool:
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
            port=_get_int("port", 1433) or 1433,
            user=_get_str("user", "sa") or "sa",
            password=_get_str("password", ""),
            database=_get_str("database", "master") or "master",
            instance_name=_get_str("instance_name", ""),
            jdbc_url=_get_str("jdbc_url", ""),
            encrypt=_get_bool("encrypt", False),
            trust_server_certificate=_get_bool("trust_server_certificate", True),
            login_timeout_s=_get_int("login_timeout_s", 10),
            application_name=_get_str("application_name", "DBCheck") or "DBCheck",
        )

    def __repr__(self) -> str:
        """安全的字符串表示（隐藏密码）。"""
        safe_pass = "***" if self.password else ""
        return (
            f"MssqlJdbcConnectionConfig(host={self.host!r}, port={self.port}, "
            f"database={self.database!r}, user={self.user!r}, "
            f"instance_name={self.instance_name!r}, "
            f"jdbc_url={self.jdbc_url!r}, encrypt={self.encrypt}, "
            f"trust_server_certificate={self.trust_server_certificate}, "
            f"login_timeout_s={self.login_timeout_s}, "
            f"application_name={self.application_name!r}, "
            f"password={safe_pass!r})"
        )
