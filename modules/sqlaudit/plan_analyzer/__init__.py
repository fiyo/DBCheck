# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""SQL 审核执行计划适配器包（MVP2）。

提供：
- normalize_db_type(db_type): 库型别名归并（pg→postgresql、kingbase/ivorysql/hgdb→postgresql、tidb→mysql、sqlserver_jdbc→sqlserver、dameng→dm ...）
- get_analyzer(db_type): 按库型返回对应的 BasePlanAnalyzer 实例；不支持的库型返回 None
- connect_instance(instance): 依据实例信息惰性 import 驱动并建立 DB-API 连接

设计：各库 EXPLAIN 调用统一封装为可插拔适配器，呼应品牌 X = eXtensible。
EXPLAIN 为只读操作，不会执行原 SQL，符合「默认只读 / 不执行」安全原则。
"""
import os
import platform

from .base import BasePlanAnalyzer
from .mysql import MySQLPlanAnalyzer
from .postgres import PostgresPlanAnalyzer
from .oracle import OraclePlanAnalyzer
from .sqlserver import SqlServerPlanAnalyzer
from .dm import DamengPlanAnalyzer


# 当前已支持执行计划分析的规范族（服务层不支持提示文案复用）
SUPPORTED_ENGINES = ("mysql", "postgresql", "oracle", "sqlserver", "dm")


def normalize_db_type(db_type):
    """将库型别名归并到规范族（mysql / postgresql / oracle / sqlserver / dm）。"""
    db_type = (db_type or "mysql").lower()
    alias = {
        "pg": "postgresql", "postgres": "postgresql",
        "ivorysql": "postgresql", "kingbase": "postgresql", "hgdb": "postgresql",
        "mariadb": "mysql", "tidb": "mysql", "oceanbase": "mysql", "gbase": "mysql",
        "sqlserver_jdbc": "sqlserver", "sqlserver": "sqlserver", "mssql": "sqlserver",
        "dameng": "dm", "dm8": "dm", "dm": "dm",
    }
    return alias.get(db_type, db_type)


def get_analyzer(db_type):
    """按库型返回执行计划适配器；不支持返回 None（优雅降级）。"""
    dt = normalize_db_type(db_type)
    if dt in ("mysql", "mariadb", "tidb", "oceanbase", "gbase"):
        return MySQLPlanAnalyzer()
    if dt in ("postgresql", "ivorysql", "kingbase"):
        return PostgresPlanAnalyzer()
    if dt == "oracle":
        return OraclePlanAnalyzer()
    if dt == "sqlserver":
        return SqlServerPlanAnalyzer()
    if dt == "dm":
        return DamengPlanAnalyzer()
    return None


def connect_instance(instance: dict):
    """按实例信息建立 DB-API 连接（惰性 import 驱动）。不支持的库型抛 ValueError。

    issue #64：若实例显式开启 ``ssh_tunnel`` 且配置了 ``ssh_host``，先在本地开
    ``127.0.0.1:local_port`` 经 SSH 跳板转发到真实数据库，并把 host/port 改写为本地
    端点（对所有驱动透明）。隧道对象挂到返回的 conn 上（``conn._ssh_tunnel``），
    由调用方在关闭 conn 时一并关闭。
    """
    dt = normalize_db_type(instance.get("db_type"))
    password = instance.get("password") or ""
    host = instance.get("host")
    port = instance.get("port")
    user = instance.get("user")

    # issue #64：数据库经 SSH 隧道连接（与巡检基类同口径：显式开启 + 配 ssh_host）
    from modules.ssh.tunnel_helper import should_use_db_tunnel, build_db_ssh_tunnel
    _ssh_info = {k: instance.get(k) for k in ('ssh_host', 'ssh_port', 'ssh_user',
                                              'ssh_password', 'ssh_key_file',
                                              'ssh_key_password', 'ssh_ebpf', 'ssh_tunnel')}
    _tunnel = None
    if should_use_db_tunnel(_ssh_info):
        _tunnel, host, port = build_db_ssh_tunnel(_ssh_info, host, port)

    def _mk(conn):
        if _tunnel is not None:
            try:
                conn._ssh_tunnel = _tunnel
            except Exception:
                pass
        return conn

    if dt in ("mysql", "mariadb", "tidb", "oceanbase", "gbase"):
        import pymysql
        import pymysql.cursors
        return _mk(pymysql.connect(
            host=host, port=port, user=user, password=password,
            database=instance.get("database") or None,
            connect_timeout=10,
            cursorclass=pymysql.cursors.DictCursor,
        ))
    if dt in ("postgresql", "ivorysql", "kingbase", "hgdb"):
        import psycopg2
        import psycopg2.extras
        # 与 modules/web/app.py 保持一致：PG 系各分支默认库名不同
        raw_db = (instance.get("db_type") or "").lower()
        _pg_default_db = {
            "kingbase": "kingbase",
            "hgdb": "highgo",
            "ivorysql": "ivorysql",
        }.get(raw_db, "postgres")
        return _mk(psycopg2.connect(
            host=host, port=port, user=user, password=password,
            dbname=instance.get("database") or _pg_default_db,
            connect_timeout=10,
            cursor_factory=psycopg2.extras.RealDictCursor,
        ))
    if dt == "oracle":
        import oracledb
        dsn = instance.get("service_name") or f"{host}:{port}/orcl"
        mode = oracledb.SYSDBA if instance.get("sysdba") else oracledb.AUTH_MODE_DEFAULT
        try:
            return _mk(oracledb.connect(user=user, password=password, dsn=dsn, mode=mode))
        except Exception as e:  # noqa: BLE001
            err_str = str(e)
            # 老版本 Oracle（11g 等）thin 模式不支持老密码验证器，需切换厚模式 + Instant Client
            if "DPY-3010" in err_str or "DPY-3015" in err_str:
                _ok = False
                try:
                    oracledb.init_oracle_client()  # 自动探测系统已安装的 Instant Client
                    _ok = True
                except Exception:
                    _sys = platform.system().lower()
                    _sub = {"windows": "windows_x64", "linux": "linux_x64",
                            "darwin": "darwin_x64"}.get(_sys)
                    if _sub:
                        from modules.core import paths
                        _bd = str(paths.PROJECT_ROOT / "drivers" / "oracle_client" / _sub)
                        if os.path.isdir(_bd):
                            try:
                                oracledb.init_oracle_client(lib_dir=_bd)
                                _ok = True
                            except Exception:
                                pass
                if _ok:
                    return _mk(oracledb.connect(user=user, password=password, dsn=dsn, mode=mode))
            raise
    if dt == "sqlserver":
        import pyodbc
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={host},{port};"
            f"UID={user};PWD={password};"
            f"TrustServerCertificate=yes;Encrypt=yes;"
            f"Connect Timeout=10;"
        )
        if instance.get("database"):
            conn_str += f"Database={instance['database']};"
        return _mk(pyodbc.connect(conn_str))
    if dt == "dm":
        import dmPython
        dsn = f"{host}:{port}"
        return _mk(dmPython.connect(user=user, password=password, server=dsn))
    raise ValueError(f"不支持的执行计划分析数据库类型: {dt}")
