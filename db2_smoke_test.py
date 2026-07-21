#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Db2 连接基座骨架冒烟测试（开发用，非插件组成部分）。

验证三件事：
  1. import jdbc_jvm; jdbc_jvm.ensure_jvm() 不报错（JVM 启动 + 驱动 jar 上 classpath）
  2. Db2ConnectionConfig.build_jdbc_url() 构造出 jdbc:db2://localhost:50000/testdb
  3. 用 ensure_jvm 后的 JVM + com.ibm.db2.jcc.DB2Driver + DriverManager.getConnection
     连上 live DB2（localhost:50000/testdb）取到 CURRENT TIMESTAMP 与版本号

运行：在 D:/wt-pro-i18n 下 `python db2_smoke_test.py`
"""

import os
import sys

# 将 db2_jdbc 插件目录加入导入路径（自包含，不依赖框架其余部分）
_PLUGIN_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "plugins", "available", "db2_jdbc"
)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import jpype
from jpype import JClass

import jdbc_jvm
from connection_config import Db2ConnectionConfig


def main() -> int:
    print("== 1. ensure_jvm() ==")
    jars = jdbc_jvm.ensure_jvm()
    print(f"  classpath jar 数量: {len(jars)}")
    assert any("db2jcc4" in j for j in jars), "db2jcc4.jar 未进入 classpath"
    print("  OK: JVM 已启动且 db2jcc4.jar 在 classpath 中")

    print("\n== 2. build_jdbc_url() ==")
    cfg = Db2ConnectionConfig(
        host="localhost", port=50000, database="testdb",
        user="db2inst1", password="password",
    )
    url = cfg.build_jdbc_url()
    print(f"  url = {url}")
    assert url == "jdbc:db2://localhost:50000/testdb", f"URL 不符预期: {url}"
    print("  OK: 构造出 jdbc:db2://localhost:50000/testdb")

    print("\n== 3. 连接 live DB2 并取 CURRENT TIMESTAMP ==")
    jdbc_jvm.register_db2_driver()
    DriverManager = JClass("java.sql.DriverManager")
    conn = DriverManager.getConnection(url, "db2inst1", "password")
    try:
        stmt = conn.createStatement()

        rs = stmt.executeQuery("SELECT CURRENT TIMESTAMP FROM sysibm.sysdummy1")
        while rs.next():
            ts = rs.getObject(1)
            print(f"  CURRENT TIMESTAMP = {ts}")
        rs.close()

        rs2 = stmt.executeQuery("SELECT versionnumber FROM sysibm.sysversions")
        while rs2.next():
            print(f"  versionnumber = {rs2.getObject(1)}")
        rs2.close()

        stmt.close()
    finally:
        conn.close()

    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"\nSMOKE TEST FAILED: {exc}")
        sys.exit(1)
