#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
GBase 8s 数据库巡检模块

连接方式：JDBC + jaydebeapi（无需 GBase SDK，需 JDK + JDBC 驱动 jar）
JDBC 驱动默认路径：DBCheck 安装目录/drivers/gbase/jdbc-3.5.1.jar
可通过环境变量 GBase_JDBC_DRIVER 指定自定义路径。
"""

import os
import sys
import warnings
from pathlib import Path
warnings.filterwarnings("ignore")

from inspection_engine import BaseInspectionEngine

# ── JDBC 驱动路径 ────────────────────────────────────────────────────────
DEFAULT_JDBC_DRIVER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'drivers', 'gbase', 'jdbc-3.5.1.jar'
)
JDBC_DRIVER_PATH = os.environ.get('GBase_JDBC_DRIVER', DEFAULT_JDBC_DRIVER)

# ── 自动探测 JAVA_HOME ───────────────────────────────────────────────────
def _detect_java_home():
    """自动探测 JAVA_HOME（按常见安装路径）"""
    candidates = [
        os.environ.get('JAVA_HOME', ''),
        'C:\\Program Files\\Java\\jdk-11',
        'C:\\Program Files\\Java\\jdk-17',
        'C:\\Program Files\\Java\\jdk-1.8',
        'C:\\Program Files\\Eclipse Adoptium\\jdk-11',
        'C:\\Program Files\\Eclipse Adoptium\\jdk-17',
    ]
    for path in candidates:
        if path and os.path.isdir(path):
            return path
    return None

_detected_java_home = _detect_java_home()
if _detected_java_home:
    os.environ['JAVA_HOME'] = _detected_java_home
    # 把 jvm.dll 所在目录加入 PATH（Windows 需要）
    jvm_dir = os.path.join(_detected_java_home, 'bin', 'server')
    if os.path.isdir(jvm_dir):
        os.environ['PATH'] = jvm_dir + os.pathsep + os.environ.get('PATH', '')


class GBaseInspector(BaseInspectionEngine):
    """
    GBase 8s 数据库巡检器 - 继承 BaseInspectionEngine

    连接模式：JDBC（jaydebeapi），无需 GBase SDK
    """

    def __init__(self, host, port, user, password, database=None, ssh_info=None, template_id=None, gbase_server_name=None):
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'gbase'
        self.jdbc_driver_path = JDBC_DRIVER_PATH
        self.gbase_server_name = gbase_server_name or 'gbase01'

    def connect(self):
        """
        连接 GBase 8s 数据库（JDBC 模式）

        返回:
            (ok, version) - ok 为 True 时 version 是版本号，否则是错误信息
        """
        return self._connect_jdbc()

    def _connect_jdbc(self):
        try:
            import jaydebeapi, jpype, os

            # 确保 JAVA_HOME 已设置
            _java_home = _detect_java_home()
            if _java_home:
                os.environ['JAVA_HOME'] = _java_home
                jvm_dir = os.path.join(_java_home, 'bin', 'server')
                if os.path.isdir(jvm_dir):
                    os.environ['PATH'] = jvm_dir + os.pathsep + os.environ.get('PATH', '')

            jdbc_driver_path = self.jdbc_driver_path

            # 显式启动 JVM，把 GBase JDBC 驱动 JAR 加入 classpath
            if not jpype.isJVMStarted():
                try:
                    jpype.startJVM(classpath=[jdbc_driver_path])
                except Exception:
                    pass

            # 构建 JDBC URL（官方格式，末尾加分号）
            jdbc_url = (
                f"jdbc:gbasedbt-sqli://{self.host}:{int(self.port)}/"
                f"{self.database}:GBASEDBTSERVER={self.gbase_server_name};"
            )

            # GBase 8s 驱动类名（官方）
            driver_class = 'com.gbasedbt.jdbc.Driver'
            try:
                conn = jaydebeapi.connect(
                    driver_class,
                    jdbc_url,
                    [self.user, self.password],
                    [jdbc_driver_path],
                )
            except Exception as e:
                return False, f"GBase JDBC 连接失败: {e}\nJDBC URL: {jdbc_url}"

            self.conn = conn
            self.cursor = self.conn.cursor()

            # GBase 8s 基于 Informix，用 DBINFO 获取版本（参数用双引号）
            self.cursor.execute('SELECT DBINFO("version", "full") FROM systables WHERE tabid = 1')
            row = self.cursor.fetchone()
            ver = row[0] if row else 'unknown'
            return True, ver

        except Exception as e:
            import traceback
            err_msg = str(e)
            tb = traceback.format_exc()
            # 把 JDBC URL 和完整堆栈也带出来方便调试
            return False, f"GBase JDBC 连接失败: {err_msg}\nJDBC URL: {jdbc_url}\n堆栈:\n{tb}"

    def disconnect(self):
        """断开数据库连接"""
        try:
            if self.cursor:
                self.cursor.close()
        except Exception:
            pass
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass


# ── 供 web_ui.py 调用的连接测试函数 ────────────────────────────────────
def test_gbase_jdbc_connection(host, port, user, password, database='gbase01', gbase_server_name='gbase01'):
    """
    测试 GBase 8s JDBC 连接（供 web_ui.py 调用）
    
    :param host: 服务器 IP
    :param port: 端口（原生协议默认 9088，MySQL 协议 5258）
    :param user: 用户名（如 gbasedbt）
    :param password: 密码
    :param database: 数据库名
    :param gbase_server_name: GBase 服务器实例名（INFORMIXSERVER 参数）
    :return: (ok: bool, msg: str)
    """
    # 在 try 外面先构建 jdbc_url，确保 except 里也能引用
    db = database if database else 'testdb'
    jdbc_url = (
        f"jdbc:gbasedbt-sqli://{host}:{int(port)}/"
        f"{db}:GBASEDBTSERVER={gbase_server_name};"
    )

    try:
        import jaydebeapi, jpype, os

        # 确保 JAVA_HOME 已设置
        _java_home = _detect_java_home()
        if _java_home:
            os.environ['JAVA_HOME'] = _java_home

        # JAR 路径
        _jdbc_driver_path = JDBC_DRIVER_PATH

        # 显式启动 JVM，把 GBase JDBC 驱动 JAR 加入 classpath
        if not jpype.isJVMStarted():
            try:
                jpype.startJVM(classpath=[_jdbc_driver_path])
            except Exception:
                pass

        # GBase 8s JDBC URL 官方格式：
        # jdbc:gbasedbt-sqli://host:port/database:GBASEDBTSERVER=server;

        # GBase 8s 驱动类名（官方）
        driver_class = 'com.gbasedbt.jdbc.Driver'
        try:
            conn = jaydebeapi.connect(
                driver_class,
                jdbc_url,
                [user, password],
                [_jdbc_driver_path],
            )
        except Exception as e:
            return False, f"GBase JDBC 连接失败: {e}\nJDBC URL: {jdbc_url}"

        cur = conn.cursor()
        # GBase 8s 基于 Informix，用 DBINFO 获取版本
        cur.execute('SELECT DBINFO("version", "full") FROM systables WHERE tabid = 1')
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, f"GBase 8s {ver}"
    except Exception as e:
        import traceback
        err = str(e)
        tb = traceback.format_exc()
        if 'Class not found' in err or 'No suitable driver' in err:
            return False, f"JDBC 驱动加载失败：请确认 {JDBC_DRIVER_PATH} 存在且版本正确"
        if 'Connection refused' in err or 'timed out' in err:
            return False, f"连接被拒绝：请确认 GBase 服务已启动且端口 {port} 可访问"
        # 把 JDBC URL 和完整堆栈也带出来方便调试
        return False, f"GBase 连接失败: {err}\nJDBC URL: {jdbc_url}\n堆栈:\n{tb}"


def getData(ip, port, user, password, database='testdb', ssh_info=None, label=None, template_id=None, gbase_server_name='gbase01'):
    """
    原有 API - 创建 GBaseInspector 实例

    注意：这个函数在重构过程中保留，用于兼容 web_ui.py 中的旧代码。
    新代码应该直接使用 GBaseInspector 类。
    """
    inspector = GBaseInspector(ip, port, user, password, database, ssh_info, template_id, gbase_server_name)
    ok, ver = inspector.connect()
    if not ok:
        return None
    # 为了兼容旧代码，返回一个对象，其中包含 conn_db2 属性
    class CompatWrapper:
        def __init__(self, inspector):
            self.inspector = inspector
            self.conn_db2 = inspector.conn
        def checkdb(self, sqlfile=''):
            self.inspector.collect_data()
            return self.inspector.context
        def generate_report(self, output_file, inspector_name="Jack"):
            return self.inspector.generate_report(output_file, inspector_name)
    return CompatWrapper(inspector)


if __name__ == '__main__':
    # 简单测试
    import argparse
    parser = argparse.ArgumentParser(description='GBase 8s 巡检测试（JDBC 模式）')
    parser.add_argument('--host', default='localhost', help='主机地址')
    parser.add_argument('--port', type=int, default=9088, help='端口（原生协议默认 9088）')
    parser.add_argument('--user', default='gbasedbt', help='用户名')
    parser.add_argument('--password', default='', help='密码')
    parser.add_argument('--database', default='', help='数据库名')
    args = parser.parse_args()

    ok, msg = test_gbase_jdbc_connection(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database
    )
    if ok:
        print(f"✅ 连接成功，版本: {msg}")
        sys.exit(0)
    else:
        print(f"❌ 连接失败: {msg}")
        sys.exit(1)
