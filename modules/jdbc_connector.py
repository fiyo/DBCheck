# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck
"""
统一 JDBC 连接层（jdbc_connector）

目标：所有走 JDBC 的数据库类型（8 类：6 个 JDBC 插件 + 核心内置 dm/gbase）
共用同一套连接方法 —— 每种库只差「连接串模板 + 驱动类」，其余全部统一：

  - 驱动解析：driver_registry.resolve_jdbc_driver_jars（驱动管理优先，目录兜底）
  - JVM 启动：addClassPath 必须在 startJVM 之前；JVM 已启动则补 classpath
  - 建连：jaydebeapi.connect(driver_class, jdbc_url, [user, password], jars)
  - 报错规范化：统一返回 (conn, meta) / (None, err)

注册表 JDBC_PROFILES 的 key 是「巡检/数据源分派令牌」（与前端下拉、plugin.json
db_type 一致）：oracle_jdbc / sqlserver_jdbc / db2 / hgdb / clickhouse / uxdb /
dm / gbase。新增 JDBC 类型只需在 driver_registry 登记驱动 + 本表加一行模板。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from modules.driver_registry import JDBC_PLUGIN_TO_CATALOG, resolve_jdbc_driver_jars

# ═══════════════════════════════════════════════════════════════════════════
# 注册表：分派令牌 → 连接串模板
# 模板占位符：{host} {port} {db} {service} {server}；None/空 的段自动回退默认
# ═══════════════════════════════════════════════════════════════════════════
JDBC_PROFILES: Dict[str, Dict[str, Any]] = {
    'oracle_jdbc': {
        'driver_class': 'oracle.jdbc.driver.OracleDriver',
        # use_sid=True 时走 @host:port:SID 旧格式（见 build_jdbc_url 特判）
        'url': 'jdbc:oracle:thin:@//{host}:{port}/{service}',
        'port': 1521,
        'service_default': 'ORCLCDB',
    },
    'sqlserver_jdbc': {
        'driver_class': 'com.microsoft.sqlserver.jdbc.SQLServerDriver',
        # encrypt/trust/instance_name 逻辑在 build_jdbc_url 特判（SQL Server 细节多）
        'url': 'jdbc:sqlserver://{host}:{port};databaseName={db}',
        'port': 1433,
        'db_default': 'master',
    },
    'db2': {
        'driver_class': 'com.ibm.db2.jcc.DB2Driver',
        'url': 'jdbc:db2://{host}:{port}/{db}',
        'port': 50000,
    },
    'hgdb': {
        # HGDB 与 PostgreSQL 线协议兼容。项目未捆绑 HighGo 专属驱动
        # （com.highgo.jdbc.Driver，仅在 HgdbJdbc-*.jar 内），故统一走标准
        # PostgreSQL JDBC 驱动（org.postgresql.Driver + jdbc:postgresql://），
        # 对 HGDB 实例可直接连通（与 ivorysql 处理方式一致）。
        'driver_class': 'org.postgresql.Driver',
        'url': 'jdbc:postgresql://{host}:{port}/{db}',
        'port': 5866,
        'db_default': 'highgo',
    },
    'clickhouse': {
        'driver_class': 'com.clickhouse.jdbc.ClickHouseDriver',
        'url': 'jdbc:clickhouse://{host}:{port}/{db}',
        'port': 8123,
        'db_default': 'default',
    },
    'uxdb': {
        'driver_class': 'com.uxsino.uxdb.Driver',
        'url': 'jdbc:uxdb://{host}:{port}/{db}',
        'port': 33060,
    },
    'ivorysql': {
        # IvorySQL 兼容 PostgreSQL 协议：用标准 PG 驱动 + jdbc:postgresql://
        'driver_class': 'org.postgresql.Driver',
        'url': 'jdbc:postgresql://{host}:{port}/{db}',
        'port': 5432,
        'db_default': 'ivorysql',
    },
    'pg': {
        'driver_class': 'org.postgresql.Driver',
        'url': 'jdbc:postgresql://{host}:{port}/{db}',
        'port': 5432,
        'db_default': 'postgres',
    },
    'kingbase': {
        # KingbaseES V8 官方驱动/URL（kingbase8 协议）。
        # 注意：jar 内真实驱动类为 com.kingbase8.Driver（非 com.kingbase8.jdbc.Driver，
        # 后者是文档常见笔误，实测不在 jar 中，2026-08-19 修正）。
        'driver_class': 'com.kingbase8.Driver',
        'url': 'jdbc:kingbase8://{host}:{port}/{db}',
        'port': 54321,
        'db_default': 'kingbase',
    },
    'yashandb': {
        'driver_class': 'com.yashandb.jdbc.Driver',
        'url': 'jdbc:yashandb://{host}:{port}/{db}',
        'port': 1688,
        'db_default': 'yashandb',
    },
    'mysql': {
        'driver_class': 'com.mysql.cj.jdbc.Driver',
        'url': 'jdbc:mysql://{host}:{port}/{db}',
        'port': 3306,
        'db_default': 'mysql',
    },
    'mariadb': {
        'driver_class': 'org.mariadb.jdbc.Driver',
        'url': 'jdbc:mariadb://{host}:{port}/{db}',
        'port': 3306,
        'db_default': 'mysql',
    },
    'tidb': {
        # TiDB 兼容 MySQL 协议：用 MySQL Connector/J + jdbc:mysql://
        'driver_class': 'com.mysql.cj.jdbc.Driver',
        'url': 'jdbc:mysql://{host}:{port}/{db}',
        'port': 4000,
        'db_default': 'mysql',
    },
    'oceanbase': {
        # OceanBase MySQL 租户：database 指向租户名；不指定时连到租户（无 db 段）
        'driver_class': 'com.oceanbase.jdbc.Driver',
        'url': 'jdbc:oceanbase://{host}:{port}/{db}',
        'port': 2881,
        'db_default': '',
    },
    'dm': {
        'driver_class': 'dm.jdbc.driver.DmDriver',
        'url': 'jdbc:dm://{host}:{port}',
        'port': 5236,
        'db_default': 'DAMENG',
    },
    'gbase': {
        'driver_class': 'com.gbasedbt.jdbc.Driver',
        'url': 'jdbc:gbasedbt-sqli://{host}:{port}/{db}:GBASEDBTSERVER={server};',
        'port': 9088,
        'db_default': 'gbase01',
        'server_default': 'gbase01',
    },
}

# 需要隔离到 JVM 子进程的 JDBC 类型全集（与 jdbc_inspection_cli.JVM_INSPECTION_DB_TYPES 对齐，
# 供调用方快速判定；单一事实来源在 driver_registry.JDBC_PLUGIN_TO_CATALOG）。
JDBC_DB_TYPES: Tuple[str, ...] = tuple(JDBC_PROFILES.keys())


# ═══════════════════════════════════════════════════════════════════════════
# JAVA_HOME 探测（与 main_gbase / 各插件的探测逻辑合并后的统一版本）
# ═══════════════════════════════════════════════════════════════════════════
def detect_java_home() -> Optional[str]:
    """探测 JAVA_HOME：优先环境变量，其次常见安装路径。返回 None 表示未找到。"""
    _env = os.environ.get('JAVA_HOME') or os.environ.get('JRE_HOME')
    if _env and os.path.isdir(_env):
        return _env
    _candidates = []
    if sys.platform == 'win32':
        _candidates = [
            r'C:\Program Files\Java\jdk-17',
            r'C:\Program Files\Java\jdk-11',
            r'C:\Program Files\Java\jdk-1.8',
            r'C:\Program Files\Java\jre-1.8',
            r'C:\Program Files\Microsoft\jdk-17.0.12.7-hotspot',
            r'C:\Program Files\Eclipse Adoptium',
            r'C:\Program Files\Zulu',
        ]
        for _base in _candidates:
            if os.path.isdir(_base):
                # 返回含 bin/server 的 JDK 根；Adoptium/Zulu 下有多版本子目录，取第一个
                if os.path.isdir(os.path.join(_base, 'bin', 'server')):
                    return _base
                try:
                    for _sub in sorted(os.listdir(_base)):
                        _p = os.path.join(_base, _sub)
                        if os.path.isdir(os.path.join(_p, 'bin', 'server')):
                            return _p
                except OSError:
                    pass
    else:
        for _cand in ('/usr/lib/jvm/java-17-openjdk', '/usr/lib/jvm/java-11-openjdk',
                      '/usr/lib/jvm/java-8-openjdk', '/usr/lib/jvm/default-java'):
            if os.path.isdir(_cand):
                return _cand
    return None


def setup_jvm_env() -> None:
    """设置 JAVA_HOME / PATH，供 startJVM 使用（幂等）。"""
    _java_home = detect_java_home()
    if _java_home:
        os.environ['JAVA_HOME'] = _java_home
        _jvm_dir = os.path.join(_java_home, 'bin', 'server')
        if os.path.isdir(_jvm_dir):
            os.environ['PATH'] = _jvm_dir + os.pathsep + os.environ.get('PATH', '')


# ═══════════════════════════════════════════════════════════════════════════
# 连接串构造：每种库只差这里
# ═══════════════════════════════════════════════════════════════════════════
def build_jdbc_url(
    db_type: str,
    host: str,
    port: Optional[int] = None,
    database: Optional[str] = None,
    service_name: Optional[str] = None,
    use_sid: bool = False,
    gbase_server_name: Optional[str] = None,
    encrypt: Optional[bool] = None,
    trust_server_certificate: Optional[bool] = None,
    jdbc_url: Optional[str] = None,
    **extra: Any,
) -> str:
    """按分派令牌构造 JDBC URL。

    - jdbc_url 非空 → 原样透传（支持用户自定义/多主机/故障转移，调用方自行保证格式）
    - 否则按 JDBC_PROFILES[db_type].url 模板填充；未知类型回退
      ``jdbc:<db_type>://host:port`` 最简格式（仍可连，报错信息友好）。
    """
    if jdbc_url and str(jdbc_url).strip():
        _u = str(jdbc_url).strip()
        # HGDB 现统一走标准 PostgreSQL 驱动（org.postgresql.Driver），其
        # acceptsURL 只认 jdbc:postgresql: 前缀；用户若填 jdbc:highgo: 则改写，
        # 避免 PG 驱动报 No suitable driver。
        if db_type == 'hgdb' and _u.lower().startswith('jdbc:highgo:'):
            _u = 'jdbc:postgresql:' + _u[len('jdbc:highgo:'):]
        return _u

    _prof = JDBC_PROFILES.get(db_type) or {}
    _tpl = _prof.get('url') or f'jdbc:{db_type}://{{host}}:{{port}}'
    _port = int(port or _prof.get('port') or 0)
    _db = database or _prof.get('db_default') or ''
    # 空库名：去掉 /{db} 段（clickhouse/db2/hgdb 统一行为：不产生尾部斜杠）
    if not _db:
        _tpl = _tpl.replace('/{db}', '')

    # Oracle：use_sid 走 @host:port:SID 旧格式（service_name 字段承载 SID 值）
    if db_type == 'oracle_jdbc' and use_sid:
        _sid = service_name or _prof.get('service_default') or 'ORCL'
        return f'jdbc:oracle:thin:@{host}:{_port}:{_sid}'

    # SQL Server：encrypt=false 时省略 trustServerCertificate（部分驱动/服务器在
    # 强制加密场景下带 false 会走异常 SSL 分支报 unexpected_message）；命名实例
    # 不带端口，由 SQL Browser 解析（instance_name 经 extra 传入）。
    if db_type == 'sqlserver_jdbc':
        _instance = extra.get('instance_name') or ''
        _host_part = f'{host};instanceName={_instance}' if _instance else f'{host}:{_port}'
        _url = f'jdbc:sqlserver://{_host_part};databaseName={_db or "master"}'
        if encrypt:
            _trust = 'true' if trust_server_certificate else 'false'
            _url += f';encrypt=true;trustServerCertificate={_trust};sslProtocol=TLSv1.2'
        else:
            _url += ';encrypt=false'
        # 专属扩展段（loginTimeout/applicationName/认证方式）由统一层拼接，
        # 与插件 MssqlJdbcConnectionConfig 原行为逐字节一致。
        _lt = int(extra.get('login_timeout_s') or 0)
        _lt = _lt if _lt > 0 else 10
        _url += (
            f';loginTimeout={_lt}'
            f";applicationName={extra.get('application_name') or 'DBCheck'}"
            ';authentication=NotSpecified'
        )
        return _url

    _svc = service_name or _prof.get('service_default') or _db
    _server = gbase_server_name or _prof.get('server_default') or 'gbase01'

    # HGDB：追加 PG 系超时参数段（connectTimeout/loginTimeout/socketTimeout），
    # 与插件 HgdbConnectionConfig 原行为逐字节一致（extra 透传超时秒数）。
    if db_type == 'hgdb':
        _ct = max(1, int(extra.get('connect_timeout_s') or 15))
        _st = max(1, int(extra.get('socket_timeout_s') or 30))
        _base = _tpl.format(
            host=host, port=_port, db=_db, service=_svc, server=_server,
            encrypt='true' if encrypt else 'false',
            trust='true' if trust_server_certificate else 'false',
        )
        return _base + f'?connectTimeout={_ct}&loginTimeout={_ct}&socketTimeout={_st}'

    _url = _tpl.format(
        host=host,
        port=_port,
        db=_db,
        service=_svc,
        server=_server,
        encrypt='true' if encrypt else 'false',
        trust='true' if trust_server_certificate else 'false',
    )
    return _url


# ═══════════════════════════════════════════════════════════════════════════
# 驱动解析：驱动管理优先，目录自动发现兜底
# ═══════════════════════════════════════════════════════════════════════════
def _sort_jars_by_version(jars: List[str]) -> List[str]:
    """按文件名中的数字版本降序排序（DmJdbcDriver18 > 11 > 8 > 7 > 6），
    避免字符串序把 DmJdbcDriver6 排到 18 前面。"""
    import re

    def _ver(p: str):
        _m = re.search(r'(\d+)', os.path.basename(p))
        return int(_m.group(1)) if _m else 0
    return sorted(jars, key=_ver, reverse=True)


def _append_driver_deps(db_type: str, jars: List[str]) -> List[str]:
    """附加驱动运行时依赖 jar（驱动 jar 未内置但 classpath 必需的依赖）。

    现状：ClickHouse JDBC 驱动（clickhouse-jdbc all 版）不打包 slf4j-api，
    驱动类加载时引用 org/slf4j/LoggerFactory → NoClassDefFoundError。
    将 drivers/clickhouse/slf4j-api-*.jar 附加进 classpath 解决；其它类型
    无附加依赖时原样返回。
    """
    if db_type == 'clickhouse':
        import glob as _glob
        try:
            from modules.core.paths import PROJECT_ROOT
            _dir = os.path.join(str(PROJECT_ROOT), 'drivers', 'clickhouse')
            _deps = sorted(_glob.glob(os.path.join(_dir, 'slf4j-api-*.jar')))
            for _dep in _deps:
                if _dep not in jars:
                    jars = list(jars) + [_dep]
        except Exception:  # noqa: BLE001 - 依赖附加失败不影响主驱动
            pass
    return jars


def resolve_driver_jars(db_type: str, driver_version: str = '', *,
                        fallback_dirs: Optional[List[str]] = None,
                        recursive: bool = False) -> Optional[List[str]]:
    """解析驱动 jar 列表。

    1) 驱动管理（登记/激活/指定版本）：resolve_jdbc_driver_jars
    2) fallback_dirs 提供的目录 glob（如 drivers/dm8/、drivers/gbase/）；
       recursive=True 时含子目录（drivers/dm/ 下按版本分子目录），
       结果按文件名数字版本降序（高版本优先）。
    返回值统一经过 _append_driver_deps 附加运行时依赖 jar。
    """
    try:
        _resolved = resolve_jdbc_driver_jars(db_type, driver_version or None)
        if _resolved:
            return _append_driver_deps(db_type, _resolved)
    except Exception:  # noqa: BLE001
        pass
    for _d in (fallback_dirs or []):
        if not _d or not os.path.isdir(_d):
            continue
        import glob
        _pat = os.path.join(_d, '**', '*.jar') if recursive else os.path.join(_d, '*.jar')
        _jars = _sort_jars_by_version(glob.glob(_pat, recursive=recursive))
        if _jars:
            return _append_driver_deps(db_type, _jars)
    return None


# 最近一次 JVM 启动失败原因（_start_jvm 记录，供建连层透传给用户；
# JPype 默认的 "Attempt to create Java package java without jvm" 会完全
# 掩盖根因——如找不到 libjvm / JDK 版本不兼容 / 内存不足等）。
_JVM_LAST_ERROR: Optional[str] = None


def _is_jvm_started() -> bool:
    """当前进程 JVM 是否已启动（幂等、无副作用）。"""
    try:
        import jpype
        return bool(jpype.isJVMStarted())
    except Exception:
        return False


def _start_jvm(jars: List[str]) -> None:
    """启动 JVM：classpath 直接作为 startJVM 参数（标准做法）；已启动则补 classpath。

    JVM 启动失败**不再静默吞掉**：真实原因记录到 _JVM_LAST_ERROR，
    由 _open_jpype_connection / jaydebeapi 分支在 JVM 未就绪时透传给用户，
    便于定位（如 JAVA_HOME 缺失、libjvm 找不到、JDK 与驱动版本不兼容）。
    """
    global _JVM_LAST_ERROR
    import jpype
    setup_jvm_env()
    if not jpype.isJVMStarted():
        try:
            jpype.startJVM(classpath=list(jars))
            _JVM_LAST_ERROR = None
        except Exception as e:  # noqa: BLE001 - 记录真实原因，不吞
            _JVM_LAST_ERROR = f'{type(e).__name__}: {e}'
    else:
        try:
            for _jar in jars:
                jpype.addClassPath(_jar)
        except Exception as e:  # noqa: BLE001
            _JVM_LAST_ERROR = f'{type(e).__name__}: {e}'


# ═══════════════════════════════════════════════════════════════════════════
# 统一建连入口
# ═══════════════════════════════════════════════════════════════════════════
def open_jdbc_connection(
    db_type: str,
    host: str,
    port: Optional[int] = None,
    user: str = '',
    password: str = '',
    driver_version: str = '',
    jdbc_url: Optional[str] = None,
    database: Optional[str] = None,
    service_name: Optional[str] = None,
    use_sid: bool = False,
    gbase_server_name: Optional[str] = None,
    encrypt: Optional[bool] = None,
    trust_server_certificate: Optional[bool] = None,
    fallback_dirs: Optional[List[str]] = None,
    mode: str = 'jaydebeapi',
    driver_class: Optional[str] = None,
    properties: Optional[Dict[str, str]] = None,
    on_error: Optional[Callable[[Exception, Dict[str, Any]],
                                Optional[Tuple[str, Dict[str, str]]]]] = None,
    **extra: Any,
) -> Tuple[Any, Dict[str, Any]]:
    """统一 JDBC 建连（双模式）。

    mode='jaydebeapi'（默认）：经 jaydebeapi.connect 建连（内置 dm/gbase 等）。
    mode='jpype'：JPype 直连（DriverManager.getConnection），6 个 JDBC 插件
    （oracle_jdbc/sqlserver_jdbc/db2/hgdb/clickhouse/uxdb）的 connect() 均走
    此分支；专属容错通过 properties / on_error / **extra 注入，不在统一层特判
    具体数据库。

    Args:
        mode: 'jaydebeapi' | 'jpype'。
        driver_class: 覆盖注册表默认驱动类（一般不需要，注册表已对齐 jar 内真实类）。
        properties: JPype 模式附加 JDBC 属性 dict（user/password 自动注入，显式键覆盖）。
        on_error: JPype 模式建连失败回调，签名 on_error(e, ctx) -> (new_url,
            new_props) | None；返回非 None 则用新参数重试一次（如 SQL Server
            SSL 回退）。
        **extra: 透传给 build_jdbc_url（如 sqlserver 的 instance_name /
            login_timeout_s / application_name、hgdb 的 connect_timeout_s /
            socket_timeout_s）。

    Returns:
        (conn, meta) 或 (None, {'error': ...})。
        meta 含 driver / url / driver_class / mode，供日志与错误提示。
    """
    _prof = JDBC_PROFILES.get(db_type) or {}
    _url = build_jdbc_url(
        db_type, host, port,
        database=database, service_name=service_name, use_sid=use_sid,
        gbase_server_name=gbase_server_name, encrypt=encrypt,
        trust_server_certificate=trust_server_certificate, jdbc_url=jdbc_url,
        **extra,
    )

    _jars = resolve_driver_jars(db_type, driver_version, fallback_dirs=fallback_dirs)
    # HGDB 兜底：HighGo 专属驱动 jar（com.highgo.jdbc.Driver）未捆绑时，
    # 回退到标准 PostgreSQL 驱动（org.postgresql.Driver + jdbc:postgresql://），
    # HGDB 线协议兼容 PostgreSQL，可直接连通，避免缺 jar 报 Class not found。
    if not _jars and db_type == 'hgdb':
        _prof = JDBC_PROFILES['pg']
        _url = build_jdbc_url(
            'pg', host, port,
            database=database, service_name=service_name, use_sid=use_sid,
            gbase_server_name=gbase_server_name, encrypt=encrypt,
            trust_server_certificate=trust_server_certificate, jdbc_url=jdbc_url,
            **extra,
        )
        _jars = resolve_driver_jars('pg', driver_version, fallback_dirs=fallback_dirs)
    if not _jars:
        _catalog = JDBC_PLUGIN_TO_CATALOG.get(db_type, db_type)
        return None, {
            'error': f'{db_type} JDBC 驱动未找到：请到「数据库驱动管理」上传 {_catalog} 驱动，'
                     f'或放入 drivers/{_catalog}/ 目录',
            'driver': None, 'url': _url, 'driver_class': _prof.get('driver_class'),
            'mode': mode,
        }

    _start_jvm(_jars)

    _driver_class = driver_class or _prof.get('driver_class')
    _basename = os.path.basename(_jars[0]) if _jars else ''

    if mode == 'jpype':
        return _open_jpype_connection(
            db_type, _url, user, password, _jars,
            driver_class=_driver_class, properties=properties, on_error=on_error,
        )

    try:
        import jaydebeapi
    except Exception as e:  # noqa: BLE001
        return None, {'error': f'未安装 jaydebeapi：{e}', 'driver': None,
                      'url': _url, 'driver_class': _driver_class, 'mode': mode}

    # JVM 未就绪 → 直接返回真实原因（jaydebeapi 底层同样依赖 jpype）
    if not _is_jvm_started():
        _reason = _JVM_LAST_ERROR or '未知原因（JVM 未启动）'
        return None, {
            'error': f'JVM 启动失败：{_reason}。请检查运行环境 Java/JDK 安装（JAVA_HOME）'
                     f'与 jpype/JDK 版本兼容性',
            'driver': _basename, 'url': _url, 'driver_class': _driver_class, 'mode': mode,
        }

    try:
        conn = jaydebeapi.connect(
            _driver_class, _url, [user, password], _jars,
        )
        return conn, {'driver': _basename, 'url': _url,
                      'driver_class': _driver_class, 'mode': mode}
    except Exception as e:  # noqa: BLE001
        return _jdbc_error_result(db_type, e, _url, _basename, _driver_class, mode)


def _jdbc_error_result(
    db_type: str,
    e: Exception,
    url: str,
    basename: str,
    driver_class: Optional[str],
    mode: str = 'jaydebeapi',
) -> Tuple[None, Dict[str, Any]]:
    """错误归一化：统一错误文案 + 达梦 -70089 修复指引（两模式共用）。"""
    _err = f'{db_type} JDBC 连接失败: {e}\nJDBC URL: {url}\n驱动: {basename}'
    # 达梦 -70089：服务端开启通信加密（COMM_ENCRYPT）时，驱动
    # （DmCipherEncryptDLL.loadLibrary('zbCrypto')）需 JNI 加载本机达梦
    # 客户端原生加密库；未安装客户端即报 -70089。与驱动版本新旧无关。
    if db_type == 'dm' and ('-70089' in str(e) or 'Encryption module' in str(e)):
        _err += (
            '\n\n[达梦 -70089 修复指引] 当前达梦服务端开启了通信加密（COMM_ENCRYPT），'
            'JDBC 驱动的加密模块（zbCrypto）依赖本机达梦客户端原生库。请任选其一：\n'
            '  ① 服务端 dm.ini 将 COMM_ENCRYPT 设为 0（不加密）后重启实例；\n'
            '  ② 本机安装达梦数据库客户端（含加密库，安装后其 bin 目录自动生效）；\n'
            '  ③ 若服务端是 DM7/DM6，请改用对应版本的 JDBC 驱动。'
        )
    return None, {'error': _err, 'driver': basename, 'url': url,
                  'driver_class': driver_class, 'mode': mode}


def _open_jpype_connection(
    db_type: str,
    url: str,
    user: str,
    password: str,
    jars: List[str],
    *,
    driver_class: Optional[str] = None,
    properties: Optional[Dict[str, str]] = None,
    on_error: Optional[Callable[[Exception, Dict[str, Any]],
                                Optional[Tuple[str, Dict[str, str]]]]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """JPype 直连建连：统一 JVM + 驱动注册 + Properties 装配 + DriverManager。

    6 个 JDBC 插件的 connect() 均走此分支；专属容错（oracle sysdba props /
    sqlserver SSL 回退 / db2 -4461 规避 / clickhouse HTTP 头）通过
    properties / on_error 注入，不在此处特判具体数据库。
    """
    import jpype
    import jpype.imports  # noqa: F401 - 注册 java 包导入钩子（幂等）

    _driver_class = driver_class or JDBC_PROFILES.get(db_type, {}).get('driver_class')
    _basename = os.path.basename(jars[0]) if jars else ''

    # JVM 未就绪 → 直接返回真实原因（否则 JPype 只报误导性的
    # "Attempt to create Java package java without jvm"）。
    if not jpype.isJVMStarted():
        _reason = _JVM_LAST_ERROR or '未知原因（JVM 未启动）'
        return None, {
            'error': f'JVM 启动失败：{_reason}。'
                     f'请检查运行环境 Java/JDK 安装（JAVA_HOME）、jpype 与 JDK 版本兼容性；'
                     f'Oracle 驱动 ojdbc6.jar 仅支持 JDK 6-8，JDK 9+ 请改用 ojdbc8/19c 驱动',
            'driver': _basename, 'url': url, 'driver_class': _driver_class, 'mode': 'jpype',
        }

    try:
        # 1. 驱动类显式加载并注册到 DriverManager。
        #    不能只靠 JClass 实例化触发静态块：国产驱动（实测 HighGo
        #    com.highgo.jdbc.Driver）不带静态自注册，JDBC 4 的
        #    META-INF/services 自动发现又只在 DriverManager 首次初始化时
        #    扫描 classpath，晚加载的 jar 不会被发现 → No suitable driver。
        #    registerDriver 显式注册（DriverManager 内部 addIfAbsent 幂等），
        #    对自带静态注册的官方驱动（Oracle/SQLServer/Db2/CH）无副作用。
        from java.sql import DriverManager
        _driver = jpype.JClass(_driver_class)()
        DriverManager.registerDriver(_driver)
        from java.util import Properties as _JProps

        # 2. Properties 装配：user/password 自动注入，显式键覆盖
        _props = _JProps()
        _props.setProperty('user', str(user))
        _props.setProperty('password', str(password))
        for _k, _v in (properties or {}).items():
            _props.setProperty(str(_k), str(_v))

        # 3. 建连；失败时 on_error 回调返回 (new_url, new_props) 则重试一次
        try:
            conn = DriverManager.getConnection(url, _props)
        except Exception as first_e:  # noqa: BLE001
            if on_error is not None:
                _retry = on_error(first_e, {
                    'db_type': db_type, 'url': url,
                    'properties': dict(properties or {}), 'user': user,
                })
                if _retry:
                    _new_url, _new_props = _retry
                    _p2 = _JProps()
                    for _k, _v in (_new_props or {}).items():
                        _p2.setProperty(str(_k), str(_v))
                    try:
                        conn = DriverManager.getConnection(_new_url, _p2)
                    except Exception as second_e:  # noqa: BLE001
                        raise RuntimeError(
                            f'{first_e}（已尝试回退重试，但仍失败: {second_e}）'
                        ) from second_e
                else:
                    raise
            else:
                raise
        return conn, {'driver': _basename, 'url': url,
                      'driver_class': _driver_class, 'mode': 'jpype'}
    except Exception as e:  # noqa: BLE001
        return _jdbc_error_result(db_type, e, url, _basename, _driver_class, 'jpype')
