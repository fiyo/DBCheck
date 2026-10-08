#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""JDBC 连接测试隔离子进程入口。

背景（为什么必须隔离到子进程）
--------------------------------
Web 端（冻结 exe）通过 ``build/runtime-hook-gevent.py`` 在进程启动阶段执行
``gevent.monkey.patch_all()``。而 HGDB / DB2 / SQL Server(JDBC) 三类数据源
都依赖 JPype 在**当前进程内**启动 JVM（``jpype.startJVM``）并调用
``DriverManager.getConnection``。JVM 使用的是真实 OS 原生线程，且会接管
信号与部分运行时状态，与 gevent 的绿色线程 hub 相互踩踏：一旦在 patch 过的
进程里发起 JDBC 连接，hub 可能被原生线程钉死，HTTP 响应再也写不回去，
表现为「点测试连接后整个界面卡死」。

``app._conn_test_with_timeout`` 里的 ``threading.Thread + Event.wait`` 只能
兜住「Python 层慢」，兜不住「进程级 JVM 死锁」——线程超时返回后，被钉死的
hub 依旧无法把响应写出去。因此唯一可靠的解法是：**把 JVM 挪到另一个没有被
monkey-patch 的干净进程里**，主进程只做 ``subprocess`` 调用 + 超时杀进程。

协议
----
- 子进程由 ``<python|dbcheck.exe> --jdbc-test-cli`` 启动；
- 连接参数以**单个 JSON 对象**经 stdin 传入（不走命令行参数，避免密码出现在
  进程列表 / 任务管理器里）；
- 结果以**唯一一行** ``__DBCHECK_JDBC_TEST_RESULT__{json}`` 写入 stdout。
  插件自身可能打印大量日志，故主进程按该前缀行提取结果，其余输出忽略。

入参 JSON 结构::

    {
      "db_type": "hgdb" | "db2" | "sqlserver_jdbc" | ...,
      "host": "127.0.0.1",
      "port": 5866,
      "user": "highgo",
      "password": "***",
      "kwargs": {"database": "highgo", "jdbc_url": "", ...}
    }

出参 JSON 结构::

    {"ok": true, "msg": "HighGo Database V6.0"}
    {"ok": false, "msg": "连接被拒绝: ..."}
"""

import json
import os
import sys


# 项目根目录：本 CLI 独立运行时也需定位 drivers/ 目录（fallback_dirs）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_project_root_on_path():
    """确保项目根目录在 sys.path 上（开发态 `python modules/jdbc_test_cli.py`）。

    冻结态由 PyInstaller 处理导入，无需干预；开发态直接执行本文件时
    ``sys.path[0]`` 是 ``modules/``，需要把上级目录补进去才能 import modules.*。
    注意：本函数必须在下方任何 ``from modules.* import`` 之前调用——解释器引导
    阶段的 sys.path 自举，无法（也不应）依赖尚未导入的 modules.core.paths。
    """
    if getattr(sys, 'frozen', False):
        return
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)


# 立即执行：模块级 import modules.* 必须在本调用之后（开发态直接执行本文件时
# sys.path[0] 是脚本所在目录 modules/，不含项目根，晚调用会 ModuleNotFoundError）。
_ensure_project_root_on_path()

from modules.driver_registry import resolve_jdbc_driver_jars

# 结果行前缀：主进程按此前缀从 stdout 中提取唯一结果行
RESULT_PREFIX = "__DBCHECK_JDBC_TEST_RESULT__"

# 本 CLI 支持隔离执行的数据库类型（均为进程内 JVM/JPype 实现，或纯 Java JDBC）。
# 与巡检子进程 JVM_INSPECTION_DB_TYPES / driver_registry.JDBC_PLUGIN_TO_CATALOG
# 对齐为 8 类型（6 个 JDBC 插件 + 核心内置 dm/gbase）：
#   hgdb / db2 / sqlserver_jdbc / oracle_jdbc / clickhouse / uxdb / dm / gbase
SUPPORTED_DB_TYPES = ('hgdb', 'db2', 'sqlserver_jdbc', 'oracle_jdbc', 'clickhouse', 'uxdb', 'dm', 'gbase', 'ivorysql', 'halodb', 'pg', 'kingbase', 'yashandb', 'mysql', 'mariadb', 'tidb', 'oceanbase')


def _test_gbase_jdbc(payload):
    """GBase 8s JDBC 连接测试——驱动 jar 优先 driver_registry.resolve_jdbc_driver_jars，
    否则回退 drivers/gbase/ 自动发现。走共享入口 main_gbase.connect_gbase_jdbc，
    避免主进程启动 JVM 与 gevent 冲突。
    """
    _kw = payload.get('kwargs') or {}
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 9088)
    except (TypeError, ValueError):
        _port = 9088
    # TCP 预检：避免启 JVM 才报主机不可达
    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err
    try:
        from modules.entrypoints.main_gbase import connect_gbase_jdbc
    except Exception as e:  # noqa: BLE001
        return False, f'导入 main_gbase 失败：{e}'
    try:
        conn, basename = connect_gbase_jdbc(
            _host, _port, payload.get('user') or '', payload.get('password') or '',
            database=_kw.get('database') or '',
            gbase_server_name=_kw.get('gbase_server_name') or 'gbase01',
            driver_version=_kw.get('driver_version') or '',
        )
    except Exception as e:  # noqa: BLE001
        return False, f'GBase 8s JDBC 连接失败：{e}'
    try:
        cur = conn.cursor()
        cur.execute('SELECT 1 FROM systables WHERE tabid = 1')
        cur.fetchall()
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
    return True, f'GBase 8s 连接成功（驱动：{basename}）'


def _find_plugin_dir(db_type):
    """按 db_type 查找**已启用**插件目录。

    Args:
        db_type: 插件声明的数据库类型标识

    Returns:
        (plugin_dir, err)：成功时 err 为 None；失败时 plugin_dir 为 None
    """
    try:
        from modules.pluginkit.loader import discover_plugins
    except Exception as e:  # noqa: BLE001 - 冻结环境缺模块时给出可读原因
        return None, f'插件加载器不可用: {e}'

    try:
        plugins = discover_plugins()
    except Exception as e:  # noqa: BLE001
        return None, f'插件发现失败: {e}'

    for p in plugins:
        if not p.get('enabled'):
            continue
        # 兼容单类型（db_type）与多类型（db_types）两种声明形式
        if p.get('db_type') == db_type or db_type in (p.get('db_types') or []):
            path = p.get('path')
            if path and os.path.isdir(path):
                return path, None
            return None, f'插件目录不存在: {path}'
    # 二次归一：前端常发带 _jdbc 后缀的 db_type（clickhouse_jdbc/uxdb_jdbc），
    # 而插件 plugin.json 声明为 clickhouse/uxdb。去掉后缀再匹配一次。
    norm = db_type[:-5] if isinstance(db_type, str) and db_type.endswith('_jdbc') else None
    if norm:
        for p in plugins:
            if not p.get('enabled'):
                continue
            if p.get('db_type') == norm or norm in (p.get('db_types') or []):
                path = p.get('path')
                if path and os.path.isdir(path):
                    return path, None
                return None, f'插件目录不存在: {path}'
    return None, f'插件 {db_type} 未启用或不存在'


def _load_plugin_module(plugin_dir):
    """从插件目录动态加载 ``main_plugin.py``。

    Args:
        plugin_dir: 插件根目录

    Returns:
        (module, err)
    """
    import importlib.util

    plugin_path = os.path.join(plugin_dir, 'main_plugin.py')
    if not os.path.exists(plugin_path):
        return None, f'插件主文件不存在: {plugin_path}'

    # 插件内部普遍使用顶层导入（同目录同级模块），需要把插件目录加入 sys.path
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    try:
        spec = importlib.util.spec_from_file_location('dbcheck_jdbc_test_plugin', plugin_path)
        if spec is None or spec.loader is None:
            return None, f'无法加载插件模块: {plugin_path}'
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, None
    except Exception as e:  # noqa: BLE001
        return None, f'插件模块导入失败: {e}'


def _tcp_preflight(host, port, timeout=8):
    """JDBC 之前先做一次纯 Python 的 TCP 连通性探测。

    JVM 冷启动要 4~10 秒，而各家 JDBC 驱动的 loginTimeout 语义并不统一
    （Db2 JCC 的 loginTimeout 在部分版本里管不住 TCP connect，会一路挂到
    操作系统级超时，Windows 上约 21 秒）。主机不可达 / 端口未开 / 防火墙丢包
    是最常见的失败场景，用 socket 先探一下即可在几秒内给出准确原因，
    既不用启 JVM，也不会撞上外层硬超时被强杀（那样只能返回笼统的"超时"）。

    Args:
        host / port: 目标地址
        timeout: 探测超时（秒）

    Returns:
        err: None 表示可达（或参数不全无法探测，放行）；否则为可读错误串
    """
    import socket

    if not host:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None

    try:
        sock = socket.create_connection((str(host), port), timeout=timeout)
        sock.close()
        return None
    except socket.timeout:
        return (f'无法连接 {host}:{port}（TCP 握手超时 {timeout} 秒）。'
                f'请检查主机地址、端口是否正确，以及防火墙/安全组是否放行。')
    except OSError as e:
        return (f'无法连接 {host}:{port}（{e.strerror or e}）。'
                f'请确认数据库服务已启动并在该端口监听。')


def _find_dm_jdbc_jar():
    """定位 <项目根>/drivers/dm8/ 下的达梦 JDBC 驱动 jar。

    选择优先级：DmJdbcDriver18.jar（达梦官方推荐 DM8 驱动）>
    其余按版本号降序取最高版本（如 DmJdbcDriver11 > 8 > 7 > 6）。

    Returns:
        jar 绝对路径；未找到返回 None。
    """
    import glob
    import re
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _dm_dir = os.path.join(_root, 'drivers', 'dm8')
    if not os.path.isdir(_dm_dir):
        return None
    _jars = glob.glob(os.path.join(_dm_dir, 'DmJdbcDriver*.jar'))
    if not _jars:
        return None

    def _ver(p):
        m = re.search(r'DmJdbcDriver(\d+)', os.path.basename(p))
        return int(m.group(1)) if m else 0

    _sorted = sorted(_jars, key=_ver, reverse=True)
    for _j in _sorted:
        if os.path.basename(_j) == 'DmJdbcDriver18.jar':
            return _j
    return _sorted[0]


def _test_dm_jdbc(payload):
    """DM8（达梦）JDBC 连接测试——纯 Java 驱动，无需达梦原生客户端。

    驱动 jar 优先取「数据库驱动管理」中登记的指定版本（payload.driver_version），
    否则回退 <项目根>/drivers/dm8/ 自动发现。不依赖 dmPython /
    达梦客户端原生库（libdmcrypt.so），从根本上规避
    ``-70089 Encryption module failed to load``。不另做插件，直接复用本隔离
    子进程（JVM 在 monkey-patch 之外执行，避免钉死 gevent hub）。
    """
    # 统一连接层（modules/jdbc_connector）：驱动管理优先（driver_version），
    # 回退 <项目根>/drivers/dm8/ 自动发现；连接测试与巡检引擎同一套连接方法。
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 5236)
    except (TypeError, ValueError):
        _port = 5236
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _dv = (payload.get('kwargs') or {}).get('driver_version') or ''

    # TCP 预检：主机不可达时几秒内给出准确原因，不必启 JVM
    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'dm', _host, _port, _user, _pw,
            driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'dm8'),
                           os.path.join(str(PROJECT_ROOT), 'drivers', 'dm')],
            recursive=True,
        )
        if _conn is None:
            _jdbc_err = (_meta or {}).get('error') or ''
            # -70089：旧版驱动加密模块缺失（dmpython 同样需要达梦客户端 libdmcrypt，
            # 回退无意义）→ 直接透出带修复指引的错误；其它失败才回退 dmPython。
            if '-70089' in _jdbc_err or 'Encryption module' in _jdbc_err:
                return False, _jdbc_err
            # 驱动缺失 → 回退 dmPython（需本机达梦客户端原生库）。
            # 本子进程未被 gevent monkey-patch，dmPython 在此执行安全。
            return _test_dm_dmpython(payload, jdbc_reason=_jdbc_err)
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'DM8 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        return False, f'DM8 JDBC 连接失败：{e}'


def _test_ivorysql_jdbc(payload):
    """IvorySQL JDBC 连接测试——兼容 PostgreSQL 协议，复用 PG JDBC 驱动。

    驱动 jar 优先「数据库驱动管理」登记的指定版本（kwargs.driver_version），
    否则回退 drivers/postgresql/ 自动发现。与巡检引擎同一套统一连接层
    （modules.jdbc_connector.open_jdbc_connection），JVM 在本隔离子进程
    执行（未被 gevent monkey-patch），避免钉死主进程 hub。
    """
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 5432)
    except (TypeError, ValueError):
        _port = 5432
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _kw = payload.get('kwargs') or {}
    _dv = _kw.get('driver_version') or ''
    _db = _kw.get('database') or 'ivorysql'

    # TCP 预检：主机不可达时几秒内给出准确原因，不必启 JVM
    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'ivorysql', _host, _port, _user, _pw,
            database=_db,
            driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'postgresql')],
        )
        if _conn is None:
            return False, (_meta or {}).get('error') or 'IvorySQL JDBC 连接失败'
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'IvorySQL 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        return False, f'IvorySQL JDBC 连接失败：{e}'


def _test_halodb_jdbc(payload):
    """HaloDB（羲和）JDBC 连接测试——兼容 PostgreSQL 协议，复用 PG JDBC 驱动。

    与 _test_ivorysql_jdbc 同一套路：统一连接层（open_jdbc_connection）+
    drivers/postgresql/ 回退；JDBC 不可用时回退 psycopg2。
    """
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 5432)
    except (TypeError, ValueError):
        _port = 5432
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _kw = payload.get('kwargs') or {}
    _dv = _kw.get('driver_version') or ''
    _db = _kw.get('database') or 'halo'

    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'halodb', _host, _port, _user, _pw,
            database=_db,
            driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'postgresql')],
        )
        if _conn is None:
            return False, (_meta or {}).get('error') or 'HaloDB JDBC 连接失败'
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'HaloDB 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        # JDBC 不可用/失败 → psycopg2 原生回退（PG 线协议直连）
        return _pg_fallback('HaloDB', _host, _port, _user, _pw, _db, str(e))


def _pg_fallback(kind, host, port, user, password, database, reason):
    """PG 系原生回退（psycopg2）：JDBC 不可用时的兜底路径。

    运行在本隔离子进程（未 gevent monkey-patch），psycopg2 原生调用安全。
    返回 (ok, msg)，msg 合并 JDBC 失败原因便于排查。
    """
    try:
        import psycopg2
        _c = psycopg2.connect(host=host, port=int(port), user=user, password=password,
                              dbname=database, connect_timeout=10)
        _cur = _c.cursor()
        try:
            _cur.execute('SELECT version()')
            _ver = _cur.fetchone()[0]
            return True, f'{kind} 连接成功（psycopg2 回退）：{_ver}'
        finally:
            _cur.close()
            _c.close()
    except Exception as e2:  # noqa: BLE001
        return False, f'{kind} JDBC 失败: {reason}；psycopg2 回退也失败: {e2}'


def _test_pg_jdbc(payload):
    """PostgreSQL JDBC 连接测试——统一连接层优先，回退 psycopg2。

    驱动 jar 优先「数据库驱动管理」（catalog postgresql），否则回退
    drivers/postgresql/ 自动发现。与巡检引擎同一套统一连接层。
    """
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 5432)
    except (TypeError, ValueError):
        _port = 5432
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _kw = payload.get('kwargs') or {}
    _dv = _kw.get('driver_version') or ''
    _db = _kw.get('database') or 'postgres'

    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'pg', _host, _port, _user, _pw,
            database=_db, driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'postgresql')],
        )
        if _conn is None:
            return _pg_fallback('PostgreSQL', _host, _port, _user, _pw, _db,
                                (_meta or {}).get('error') or 'JDBC 连接失败')
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'PostgreSQL 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        return _pg_fallback('PostgreSQL', _host, _port, _user, _pw, _db, str(e))


def _test_kingbase_jdbc(payload):
    """KingbaseES JDBC 连接测试——统一连接层优先，回退 psycopg2。

    KingbaseES V8 兼容 PG 协议；官方驱动 com.kingbase8.jdbc.Driver，
    URL jdbc:kingbase8://（catalog key：kingbase）。
    """
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 54321)
    except (TypeError, ValueError):
        _port = 54321
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _kw = payload.get('kwargs') or {}
    _dv = _kw.get('driver_version') or ''
    _db = _kw.get('database') or 'kingbase'

    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'kingbase', _host, _port, _user, _pw,
            database=_db, driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'kingbase')],
        )
        if _conn is None:
            return _pg_fallback('KingbaseES', _host, _port, _user, _pw, _db,
                                (_meta or {}).get('error') or 'JDBC 连接失败')
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'KingbaseES 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        return _pg_fallback('KingbaseES', _host, _port, _user, _pw, _db, str(e))


def _test_yashandb_jdbc(payload):
    """YashanDB JDBC 连接测试——统一连接层优先，回退 yasdb 原生驱动。

    YashanDB 官方驱动 com.yashandb.jdbc.Driver，URL jdbc:yashandb://
    （catalog key：yashandb）。原生回退依赖 pip 包 yasdb。
    """
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 1688)
    except (TypeError, ValueError):
        _port = 1688
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _kw = payload.get('kwargs') or {}
    _dv = _kw.get('driver_version') or ''
    _db = _kw.get('database') or 'yashandb'

    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'yashandb', _host, _port, _user, _pw,
            database=_db, driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'yashandb')],
        )
        if _conn is None:
            return _yashandb_fallback(_host, _port, _user, _pw, _db,
                                      (_meta or {}).get('error') or 'JDBC 连接失败')
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'YashanDB 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        return _yashandb_fallback(_host, _port, _user, _pw, _db, str(e))


def _yashandb_fallback(host, port, user, password, database, reason):
    """YashanDB 原生回退（yasdb pip 包）。"""
    try:
        import yasdb
        _c = yasdb.connect(user=user, password=password, host=host,
                           port=int(port))
        _cur = _c.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
            return True, 'YashanDB 连接成功（yasdb 原生回退）'
        finally:
            _cur.close()
            _c.close()
    except ImportError:
        return False, (f'{reason}；且未安装 yasdb（pip install yasdb），'
                       f'无法原生回退。请到「数据库驱动管理」上传 YashanDB JDBC 驱动 jar。')
    except Exception as e2:  # noqa: BLE001
        return False, f'YashanDB JDBC 失败: {reason}；yasdb 回退也失败: {e2}'


def _mysql_fallback(kind, host, port, user, password, database, reason):
    """MySQL 系原生回退（pymysql）：JDBC 不可用时的兜底路径。

    运行在本隔离子进程（未 gevent monkey-patch），pymysql 原生调用安全。
    返回 (ok, msg)，msg 合并 JDBC 失败原因便于排查。
    """
    try:
        import pymysql
        _kw = dict(host=host, port=int(port), user=user, password=password,
                   connect_timeout=10, charset='utf8mb4')
        if database:
            _kw['database'] = database
        _c = pymysql.connect(**_kw)
        _cur = _c.cursor()
        try:
            _cur.execute('SELECT VERSION()')
            _ver = _cur.fetchone()[0]
            return True, f'{kind} 连接成功（pymysql 回退）：{_ver}'
        finally:
            _cur.close()
            _c.close()
    except ImportError:
        return False, (f'{reason}；且未安装 pymysql（pip install pymysql），'
                       f'无法原生回退。请到「数据库驱动管理」上传 {kind} JDBC 驱动 jar。')
    except Exception as e2:  # noqa: BLE001
        return False, f'{kind} JDBC 失败: {reason}；pymysql 回退也失败: {e2}'


def _oceanbase_native_fallback(host, port, user, password, database, reason):
    """OceanBase 原生回退（pymysql）：JDBC 不可用时的兜底，并针对 OB 给出明确诊断。

    OceanBase MySQL 租户的连接语义与 MySQL 不同：握手是 lazy 的，连接阶段不校验
    租户/host 白名单，直到执行首个查询（如 SELECT VERSION()）时服务器才校验，
    不通过即断开 → 表现为 pymysql 的 (2013, 'Lost connection to MySQL server
    during query')。pymysql 回退无法像官方 JDBC 驱动那样完整解析集群/租户，
    故在 OB 上稳定性弱于 JDBC；此处把这类错误翻译成可操作的诊断，而不是丢一个
    裸的 (2013, ...) 让用户无从下手。
    """
    try:
        import pymysql
    except ImportError:
        return False, (f'{reason}；且未安装 pymysql（pip install pymysql）。\n'
                       f'建议到「数据库驱动管理」上传 OceanBase 官方 JDBC 驱动 jar'
                       f'（com.oceanbase.jdbc.Driver），官方驱动对 OB 租户/集群解析最完整。')
    try:
        _kw = dict(host=host, port=int(port), user=user, password=password,
                   connect_timeout=10, charset='utf8mb4', read_timeout=30)
        if database:
            _kw['database'] = database
        _c = pymysql.connect(**_kw)
        _cur = _c.cursor()
        try:
            _cur.execute('SELECT VERSION()')
            _ver = _cur.fetchone()[0]
            return True, f'OceanBase 连接成功（pymysql 回退）：{_ver}'
        finally:
            _cur.close()
            _c.close()
    except pymysql.err.OperationalError as e:
        _code = e.args[0] if e.args else None
        if _code in (2013, 2006):
            # 区分「用户名未带租户」与「已带租户但仍被断」两种情形，避免误判：
            # 当前 user 已含 @ 说明租户格式正确，应把排查重心移到远程授权 / JDBC 驱动。
            _has_tenant = '@' in user
            if _has_tenant:
                _uname, _tenant = user.split('@', 1)[0], user.split('@', 1)[1]
                _hint = (
                    f'{reason}\n'
                    f'【OceanBase 专属诊断】连接已建立，但执行首个查询时被 OceanBase 服务器断开'
                    f'（错误 {_code}）。\n'
                    f'当前 user={user!r} 已携带租户（租户名={_tenant!r}），租户格式正确，'
                    f'可排除「用户名未带租户」这一项。\n'
                    f'最可能为以下原因之一：\n'
                    f'1) OB 用户未授权远程登录（最常见）：请在 OB 用 sys 租户执行\n'
                    f"   CREATE USER '{_uname}'@'%' IDENTIFIED BY '密码';\n"
                    f"   GRANT ALL PRIVILEGES ON *.* TO '{_uname}'@'%' WITH GRANT OPTION;\n"
                    f"   （默认 '{_uname}'@'localhost' 仅允许本机登录，远程连接会在首个查询被断，"
                    f"表现为 2013）。\n"
                    f'2) 强烈建议到「数据库驱动管理」上传 OceanBase 官方 JDBC 驱动 jar'
                    f'（com.oceanbase.jdbc.Driver）：官方驱动对 OB 租户/集群解析最完整，'
                    f'pymysql 回退在部分 OB 版本上不稳定。\n'
                    f'3) 若租户不在默认集群，用户名需写成 "user@tenant#cluster"。'
                )
            else:
                _hint = (
                    f'{reason}\n'
                    f'【OceanBase 专属诊断】连接已建立，但执行首个查询时被 OceanBase 服务器断开'
                    f'（错误 {_code}）。常见原因与对策：\n'
                    f'1) 用户名必须携带租户：当前 user={user!r}，应为 "用户名@租户名"'
                    f'（如 root@sys / root@test），否则 OB 无法定位租户。\n'
                    f'2) OB 用户未授权远程登录：请在 OB 用 sys 租户执行 '
                    f"GRANT ALL PRIVILEGES ON *.* TO '用户名'@'%' IDENTIFIED BY '密码'; "
                    f"（默认 '用户名'@'localhost' 仅允许本机登录，远程连接会在首个查询被断）。\n"
                    f'3) 强烈建议到「数据库驱动管理」上传 OceanBase 官方 JDBC 驱动 jar'
                    f'（com.oceanbase.jdbc.Driver）：官方驱动对 OB 租户/集群解析最完整，'
                    f'pymysql 回退在部分 OB 版本上不稳定。'
                )
            return False, _hint
        return False, f'{reason}；pymysql 回退也失败: {e}'
    except Exception as e2:  # noqa: BLE001
        return False, f'{reason}；pymysql 回退也失败: {e2}'


def _test_mysql_jdbc(payload):
    """MySQL JDBC 连接测试——统一连接层优先，回退 pymysql。"""
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 3306)
    except (TypeError, ValueError):
        _port = 3306
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _kw = payload.get('kwargs') or {}
    _dv = _kw.get('driver_version') or ''
    _db = _kw.get('database') or 'mysql'

    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'mysql', _host, _port, _user, _pw,
            database=_db, driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'mysql')],
        )
        if _conn is None:
            return _mysql_fallback('MySQL', _host, _port, _user, _pw, _db,
                                   (_meta or {}).get('error') or 'JDBC 连接失败')
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'MySQL 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        return _mysql_fallback('MySQL', _host, _port, _user, _pw, _db, str(e))


def _test_mariadb_jdbc(payload):
    """MariaDB JDBC 连接测试——统一连接层优先，回退 pymysql。"""
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 3306)
    except (TypeError, ValueError):
        _port = 3306
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _kw = payload.get('kwargs') or {}
    _dv = _kw.get('driver_version') or ''
    _db = _kw.get('database') or 'mysql'

    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'mariadb', _host, _port, _user, _pw,
            database=_db, driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'mariadb')],
        )
        if _conn is None:
            return _mysql_fallback('MariaDB', _host, _port, _user, _pw, _db,
                                   (_meta or {}).get('error') or 'JDBC 连接失败')
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'MariaDB 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        return _mysql_fallback('MariaDB', _host, _port, _user, _pw, _db, str(e))


def _test_tidb_jdbc(payload):
    """TiDB JDBC 连接测试——兼容 MySQL 协议，统一连接层优先，回退 pymysql。"""
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 4000)
    except (TypeError, ValueError):
        _port = 4000
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _kw = payload.get('kwargs') or {}
    _dv = _kw.get('driver_version') or ''
    _db = _kw.get('database') or 'mysql'

    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'tidb', _host, _port, _user, _pw,
            database=_db, driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'mysql')],
        )
        if _conn is None:
            return _mysql_fallback('TiDB', _host, _port, _user, _pw, _db,
                                   (_meta or {}).get('error') or 'JDBC 连接失败')
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'TiDB 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        return _mysql_fallback('TiDB', _host, _port, _user, _pw, _db, str(e))


def _test_oceanbase_jdbc(payload):
    """OceanBase JDBC 连接测试——统一连接层优先，回退 pymysql。"""
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 2881)
    except (TypeError, ValueError):
        _port = 2881
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''
    _kw = payload.get('kwargs') or {}
    _dv = _kw.get('driver_version') or ''
    _db = _kw.get('database') or ''

    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        from modules.jdbc_connector import open_jdbc_connection
        _conn, _meta = open_jdbc_connection(
            'oceanbase', _host, _port, _user, _pw,
            database=_db or None, driver_version=_dv,
            fallback_dirs=[os.path.join(str(PROJECT_ROOT), 'drivers', 'oceanbase')],
        )
        if _conn is None:
            return _oceanbase_native_fallback(_host, _port, _user, _pw, _db,
                                             (_meta or {}).get('error') or 'JDBC 连接失败')
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, f'OceanBase 连接成功（JDBC 驱动：{(_meta or {}).get("driver")}）'
    except Exception as e:  # noqa: BLE001
        return _oceanbase_native_fallback(_host, _port, _user, _pw, _db, str(e))


def _test_dm_dmpython(payload, jdbc_reason=None):
    """DM8 回退方案：dmPython（需本机安装达梦客户端原生库 libdmcrypt.so）。

    仅在 jaydebeapi / JPype1 / JDK 缺失时使用。若达梦客户端也未安装，
    给出明确的双路径修复指引，而不是笼统报一个 import 错。本函数运行在
    未被 gevent monkey-patch 的隔离子进程内，dmPython 原生调用安全。
    """
    _host = payload.get('host')
    try:
        _port = int(payload.get('port') or 5236)
    except (TypeError, ValueError):
        _port = 5236
    _user = payload.get('user') or ''
    _pw = payload.get('password') or ''

    try:
        import dmPython  # 无达梦客户端时 import 即失败
    except Exception as e:  # noqa: BLE001
        _hint = (f'JDBC 后端不可用（{jdbc_reason}）；dmPython 亦不可用（{e}）。\n'
                 f'请二选一：① 安装 JDBC 依赖：pip install jaydebeapi JPype1，'
                 f'并确保运行环境有 JDK/JRE；② 安装达梦客户端后 pip install dmPython。')
        return False, _hint

    # TCP 预检：主机不可达时几秒内给出准确原因
    _err = _tcp_preflight(_host, _port)
    if _err:
        return False, _err

    try:
        _dsn = '%s:%d' % (_host, _port)
        _conn = dmPython.connect(user=_user, password=_pw, server=_dsn)
        _cur = _conn.cursor()
        try:
            _cur.execute('SELECT 1')
            _cur.fetchall()
        finally:
            _cur.close()
            _conn.close()
        return True, 'DM8 连接成功（dmPython 驱动，依赖本机达梦客户端原生库）'
    except Exception as e:  # noqa: BLE001
        return False, f'DM8（dmPython）连接失败：{e}'


def run_test(payload):
    """执行一次 JDBC 连接测试。

    Args:
        payload: 入参 dict（见模块 docstring）

    Returns:
        (ok: bool, msg: str)
    """
    db_type = (payload.get('db_type') or '').strip()
    if not db_type:
        return False, '缺少 db_type'

    # Oracle（原生 oracledb）连接测试：
    # oracledb thin 模式与 gevent monkey-patch 不兼容（连接会挂死），且启用 Instant
    # Client 时的 thick/OCI 原生库同样会在被 patch 的主进程里钉死 hub。因此必须像
    # JVM 类 JDBC 数据源一样，在未被 patch 的干净子进程内执行。逻辑直接复用
    # modules.oracle_conn_test._ct_oracle_pro（含 SSH 隧道 + thick 回退），避免重复实现。
    if db_type == 'oracle':
        try:
            # 从独立模块导入，避免子进程 import 整个 Flask 应用（连带 Redis /
            # 调度器 / 控制台保活线程等副作用）。逻辑与 app._ct_oracle_pro 一致。
            from modules.oracle_conn_test import _ct_oracle_pro
        except Exception as e:  # noqa: BLE001 - 子进程内导入失败要转成可读错误
            return False, f'Oracle 测试器不可用: {e}'
        _data = {
            'host': payload.get('host'),
            'port': payload.get('port'),
            'user': payload.get('user'),
            'password': payload.get('password'),
        }
        _data.update(payload.get('kwargs') or {})
        try:
            r = _ct_oracle_pro(_data)
        except Exception as e:  # noqa: BLE001
            return False, f'Oracle 连接测试异常: {e}'
        return bool(r.get('ok')), str(r.get('message') or r.get('error') or '')

    # DM8（达梦）JDBC 连接测试：纯 Java 驱动，无需达梦原生客户端（libdmcrypt.so）。
    # 驱动 jar 置于 <项目根>/drivers/dm8/DmJdbcDriver*.jar；不依赖 dmPython，
    # 从根上规避 -70089 Encryption module failed to load。
    if db_type == 'dm':
        return _test_dm_jdbc(payload)

    # GBase 8s 连接测试：走 modules.entrypoints.main_gbase 的共享连接函数，
    # 使「测试连接」也在未 monkey-patch 的干净子进程内跑（避免 jaydebeapi 启动
    # JVM 钉死 gevent hub）。驱动 jar 优先 driver_registry.resolve_jdbc_driver_jars，
    # 否则回退 drivers/gbase/ 自动发现。
    if db_type == 'gbase':
        return _test_gbase_jdbc(payload)

    # IvorySQL（兼容 PG 协议）：统一 JDBC 连接层，复用 PostgreSQL 驱动
    if db_type == 'ivorysql':
        return _test_ivorysql_jdbc(payload)

    # HaloDB（羲和，兼容 PG 协议）：统一 JDBC 连接层，复用 PostgreSQL 驱动
    if db_type == 'halodb':
        return _test_halodb_jdbc(payload)

    # PostgreSQL / KingbaseES / YashanDB（PG 系）：统一 JDBC 连接层，
    # JDBC 不可用时回退原生驱动（psycopg2 / yasdb）
    if db_type == 'pg':
        return _test_pg_jdbc(payload)
    if db_type == 'kingbase':
        return _test_kingbase_jdbc(payload)
    if db_type == 'yashandb':
        return _test_yashandb_jdbc(payload)

    # MySQL 系（mysql/mariadb/tidb/oceanbase）：统一 JDBC 连接层，
    # JDBC 不可用时回退 pymysql
    if db_type == 'mysql':
        return _test_mysql_jdbc(payload)
    if db_type == 'mariadb':
        return _test_mariadb_jdbc(payload)
    if db_type == 'tidb':
        return _test_tidb_jdbc(payload)
    if db_type == 'oceanbase':
        return _test_oceanbase_jdbc(payload)

    # 自定义 jdbc_url 可能指向别的地址（含多主机/故障转移），此时不做预检
    _kw = payload.get('kwargs') or {}
    if not (isinstance(_kw, dict) and _kw.get('jdbc_url')):
        _err = _tcp_preflight(payload.get('host'), payload.get('port'))
        if _err:
            return False, _err

    plugin_dir, err = _find_plugin_dir(db_type)
    if err:
        return False, err

    module, err = _load_plugin_module(plugin_dir)
    if err:
        return False, err

    if not hasattr(module, 'test_connection'):
        return False, f'插件 {db_type} 未提供 test_connection 函数'

    kwargs = payload.get('kwargs') or {}
    if not isinstance(kwargs, dict):
        kwargs = {}

    try:
        result = module.test_connection(
            payload.get('host'),
            payload.get('port'),
            payload.get('user'),
            payload.get('password'),
            **kwargs,
        )
    except TypeError as e:
        # 插件签名不接受某些 kwargs 时降级为最小参数集重试，避免直接失败
        try:
            result = module.test_connection(
                payload.get('host'), payload.get('port'),
                payload.get('user'), payload.get('password'),
            )
        except Exception as e2:  # noqa: BLE001
            return False, f'调用插件 test_connection 失败: {e} / {e2}'
    except Exception as e:  # noqa: BLE001
        return False, str(e)

    # 插件约定返回 (ok, msg)；兼容仅返回 bool 的旧实现
    if isinstance(result, tuple) and len(result) >= 2:
        return bool(result[0]), str(result[1] or '')
    return bool(result), ''


def main(argv=None):
    """子进程入口：stdin 读 JSON，stdout 写单行结果。

    Returns:
        进程退出码：0 表示流程正常（连接失败也算正常流程，结果在 JSON 里），
        2 表示入参不可解析。
    """
    _ensure_project_root_on_path()

    raw = ''
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        raw = ''

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as e:  # noqa: BLE001
        _emit({'ok': False, 'msg': f'入参 JSON 解析失败: {e}'})
        return 2

    try:
        ok, msg = run_test(payload)
    except BaseException as e:  # noqa: BLE001 - 子进程内任何异常都要转成结果行
        ok, msg = False, f'连接测试异常: {e}'

    _emit({'ok': bool(ok), 'msg': msg})
    return 0


def _emit(obj):
    """输出结果行并立即冲刷（子进程可能被强杀，必须即时落盘到管道）。

    编码安全：ensure_ascii=True 保证输出纯 ASCII（中文变 \\uXXXX，读侧
    json.loads 自动还原），并用 buffer 直写绕开 TextIOWrapper——
    PyInstaller 冻结版子进程不尊重 PYTHONIOENCODING，文本写会按
    Windows ANSI(GBK) 落盘，主进程按 UTF-8 读即成乱码。
    """
    try:
        line = RESULT_PREFIX + json.dumps(obj, ensure_ascii=True)
    except Exception:  # noqa: BLE001
        line = RESULT_PREFIX + '{"ok": false, "msg": "结果序列化失败"}'
    text = '\n' + line + '\n'
    try:
        sys.stdout.buffer.write(text.encode('utf-8'))
        sys.stdout.buffer.flush()
    except Exception:  # noqa: BLE001
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
