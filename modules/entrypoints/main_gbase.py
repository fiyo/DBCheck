#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
GBase 8s 数据库巡检模块

连接方式：JDBC + jaydebeapi（无需 GBase SDK，需 JDK + JDBC 驱动 jar）
JDBC 驱动默认路径：DBCheck 安装目录/drivers/gbase/jdbc-3.5.1.jar
可通过环境变量 GBase_JDBC_DRIVER 指定自定义路径。
"""

from modules.core.paths import PROJECT_ROOT
from modules.driver_registry import resolve_jdbc_driver_jars
import os
import sys
import warnings
from pathlib import Path
warnings.filterwarnings("ignore")

from modules.inspection.engine import BaseInspectionEngine
from modules.core import entry

# ── JDBC 驱动路径 ────────────────────────────────────────────────────────
# ── JDBC 驱动路径 ──────────────────────────────────────────────
_script_dir = str(PROJECT_ROOT)
# main_gbase.py 可能在 scripts/ 子目录（skill 包），也可能在项目根目录
# 自动探测 drivers/gbase/ 的实际位置
_drivers_gbase_dir = os.path.join(_script_dir, "..", "drivers", "gbase")
if not os.path.isdir(_drivers_gbase_dir):
    _drivers_gbase_dir = os.path.join(_script_dir, "drivers", "gbase")
# 自动查找 jar 文件（不硬编码文件名）— 向后兼容；新代码请走 driver_registry
import glob as _glob
_jar_files = _glob.glob(os.path.join(_drivers_gbase_dir, "*.jar"))
DEFAULT_JDBC_DRIVER = _jar_files[0] if _jar_files else os.path.join(_drivers_gbase_dir, "jdbc-3.5.1.jar")
JDBC_DRIVER_PATH = os.environ.get("GBASE_JDBC_DRIVER", DEFAULT_JDBC_DRIVER)


def resolve_gbase_driver(driver_version=''):
    """GBase 8s JDBC 驱动解析：优先驱动管理（用户上传/激活的版本），
    否则回退 <项目根>/drivers/gbase/ 自动发现。返回 jar 绝对路径或 None。
    """
    try:
        _resolved = resolve_jdbc_driver_jars('gbase', driver_version)
        if _resolved:
            return _resolved[0]
    except Exception:
        pass
    if JDBC_DRIVER_PATH and os.path.isfile(JDBC_DRIVER_PATH):
        return JDBC_DRIVER_PATH
    return None
# ── 自动探测 JAVA_HOME ───────────────────────────────────────────────────
def _detect_java_home():
    """自动探测 JAVA_HOME（按常见安装路径）"""
    candidates = [
        os.environ.get('JAVA_HOME', ''),
        # Windows
        'C:\\Program Files\\Java\\jdk-11',
        'C:\\Program Files\\Java\\jdk-17',
        'C:\\Program Files\\Java\\jdk-1.8',
        'C:\\Program Files\\Eclipse Adoptium\\jdk-11',
        'C:\\Program Files\\Eclipse Adoptium\\jdk-17',
        # Linux (Debian/Ubuntu Docker 镜像)
        '/usr/lib/jvm/java-17-openjdk-amd64',
        '/usr/lib/jvm/java-11-openjdk-amd64',
        '/usr/lib/jvm/default-java',
        '/usr/lib/jvm/java-1.17.0-openjdk-amd64',
        '/usr/lib/jvm/java-1.11.0-openjdk-amd64',
    ]
    for path in candidates:
        if path and os.path.isdir(path):
            return path
    # Linux fallback: glob /usr/lib/jvm/* 找任意已安装的 JVM
    try:
        import glob
        jvm_dirs = sorted(glob.glob('/usr/lib/jvm/java-*'))
        for d in jvm_dirs:
            if os.path.isdir(d):
                return d
    except Exception:
        pass
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

    def __init__(self, host, port, user, password, database=None, ssh_info=None, template_id=None, gbase_server_name=None, driver_version=''):
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'gbase'
        self.gbase_server_name = gbase_server_name or 'gbase01'
        self.driver_version = driver_version   # 来自数据源/手动表单；空串=用激活驱动

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

            jdbc_driver_path = resolve_gbase_driver(self.driver_version)
            if not jdbc_driver_path or not os.path.isfile(jdbc_driver_path):
                return False, ('GBase 8s JDBC 驱动未找到。请到「数据库驱动管理」上传驱动；'
                               '或将 jar 放入 drivers/gbase/；或设置环境变量 GBase_JDBC_DRIVER。')

            # 显式启动 JVM，把 GBase JDBC 驱动 JAR 加入 classpath
            # ⚠️ addClassPath() 必须在 startJVM() 之前调用！
            if not jpype.isJVMStarted():
                try:
                    jpype.addClassPath(jdbc_driver_path)
                    jpype.startJVM()
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
                return False, f"GBase JDBC 连接失败: {e}\nJDBC URL: {jdbc_url}\n驱动: {jdbc_driver_path}"

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
            jdbc_url = (
                f"jdbc:gbasedbt-sqli://{self.host}:{int(self.port)}/"
                f"{self.database or 'testdb'}:GBASEDBTSERVER={self.gbase_server_name};"
            )
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
        self._close_ssh_tunnel()


# ── 供 web_ui.py 调用的连接测试函数 ────────────────────────────────────
def test_gbase_jdbc_connection(host, port, user, password, database='gbase01', gbase_server_name='gbase01', driver_version=''):
    """
    测试 GBase 8s JDBC 连接（供 web_ui.py 调用）

    :param host: 服务器 IP
    :param port: 端口（原生协议默认 9088，MySQL 协议 5258）
    :param user: 用户名（如 gbasedbt）
    :param password: 密码
    :param database: 数据库名
    :param gbase_server_name: GBase 服务器实例名（INFORMIXSERVER 参数）
    :param driver_version: 可选，驱动管理中的指定版本；空串=用激活驱动
    :return: (ok: bool, msg: str)
    """
    jdbc_url, _err = _gbase_jdbc_url(host, port, database, gbase_server_name)
    if not jdbc_url:
        return False, _err
    conn, _jar, _err_open = _open_gbase_jdbc(host, port, user, password, database, gbase_server_name, jdbc_url, driver_version)
    if _err_open:
        return False, _err_open
    try:
        cur = conn.cursor()
        cur.execute('SELECT DBINFO("version", "full") FROM systables WHERE tabid = 1')
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, f"GBase 8s {ver}（驱动：{os.path.basename(_jar)}）"
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        import traceback
        err = str(e)
        tb = traceback.format_exc()
        return False, f"GBase 连接失败: {err}\nJDBC URL: {jdbc_url}\n堆栈:\n{tb}"


# ── 共享辅助函数（供 web_ui 内联连接 / app.py 端点复用）──────────────
def _gbase_jdbc_url(host, port, database, gbase_server_name='gbase01'):
    """构造 GBase 8s 官方 JDBC URL。返回 (url, err)。"""
    db = database if database else 'testdb'
    try:
        url = (f"jdbc:gbasedbt-sqli://{host}:{int(port)}/"
               f"{db}:GBASEDBTSERVER={gbase_server_name};")
        return url, None
    except Exception as e:
        return None, f'GBase JDBC URL 构造失败: {e}'


def connect_gbase_jdbc(host, port, user, password, database='', gbase_server_name='gbase01', driver_version=''):
    """GBase 8s JDBC 连接统一入口（供 web_ui 的「列数据库」「列表/视图」端点复用）。

    内部走 modules.jdbc_connector 统一连接层（url 模板 + 驱动解析 + JVM + 建连），
    与 DM / 6 个 JDBC 插件共用同一套连接方法，GBase 只贡献自己的连接串模板。

    Returns:
        (conn, jar_basename_or_None)  连接成功时返回 (jaydebeapi.Connection, basename)；
        失败时抛 RuntimeError，包含可读原因。
    """
    from modules.jdbc_connector import open_jdbc_connection

    conn, meta = open_jdbc_connection(
        'gbase', host, port, user, password,
        driver_version=driver_version,
        database=database or None,
        gbase_server_name=gbase_server_name,
        fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'gbase')],
    )
    if conn is None:
        raise RuntimeError((meta or {}).get('error') or 'GBase 8s JDBC 连接失败')
    return conn, (meta or {}).get('driver')


def _open_gbase_jdbc(host, port, user, password, database, gbase_server_name, jdbc_url, driver_version=''):
    """打开 GBase 8s JDBC 连接。返回 (conn, jar_or_None, err_or_None)。"""
    import jaydebeapi, jpype

    _jar = resolve_gbase_driver(driver_version)
    if not _jar or not os.path.isfile(_jar):
        return None, None, ('GBase 8s JDBC 驱动未找到，请到「数据库驱动管理」上传驱动；'
                            '或将 jar 放入 drivers/gbase/；或设置环境变量 GBase_JDBC_DRIVER。')

    # 设置 JAVA_HOME（探测常见路径，与连接测试函数一致）
    _java_home = _detect_java_home()
    if _java_home:
        os.environ['JAVA_HOME'] = _java_home
        jvm_dir = os.path.join(_java_home, 'bin', 'server')
        if os.path.isdir(jvm_dir):
            os.environ['PATH'] = jvm_dir + os.pathsep + os.environ.get('PATH', '')

    # addClassPath 必须在 startJVM 之前；JVM 已启动时再补 classpath
    if not jpype.isJVMStarted():
        try:
            jpype.addClassPath(_jar)
            jpype.startJVM()
        except Exception:
            pass
    else:
        try:
            jpype.addClassPath(_jar)
        except Exception:
            pass

    try:
        conn = jaydebeapi.connect(
            'com.gbasedbt.jdbc.Driver', jdbc_url,
            [user, password], [_jar],
        )
        return conn, _jar, None
    except Exception as e:
        return None, _jar, f'GBase JDBC 连接失败: {e}\nJDBC URL: {jdbc_url}\n驱动: {_jar}'


def getData(ip, port, user, password, database='testdb', ssh_info=None, label=None, template_id=None, gbase_server_name='gbase01', driver_version=''):
    """
    原有 API - 创建 GBaseInspector 实例

    注意：这个函数在重构过程中保留，用于兼容 web_ui.py 中的旧代码。
    新代码应该直接使用 GBaseInspector 类。
    """
    inspector = GBaseInspector(ip, port, user, password, database, ssh_info, template_id,
                                gbase_server_name, driver_version=driver_version)
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


def main():
    entry.ensure_bootstrapped('gbase')
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


if __name__ == '__main__':
    main()
