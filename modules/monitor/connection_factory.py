# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
数据库连接统一工厂 — 首页实时监控 / 监控大屏共用同一套连接构造口径。

背景：
  首页实时监控（modules/pro/metrics_collector.py）与监控大屏
  （modules/monitor/engine.py）此前各自拼 DSN / ODBC 串 / 默认库名：
  SQL Server 驱动选择与加密参数、PG 库名回落、Oracle 服务名 / SID / thick
  回退、SSH 隧道开关都不一致。表现是"数据源测试连接正常，大屏却判宕机"。

边界：
  - 只统一**连接构造**（地址、库名、驱动、超时、SYSDBA、SSL、隧道），
    不共享连接对象：采集线程各自建连、各自关闭，DBAPI 连接绝不跨线程复用。
  - JVM 类型（oracle_jdbc / uxdb_jdbc）仍由调用方走隔离子进程，本工厂不
    在主进程 startJVM；不支持的类型返回 None，由调用方按原语义处理。
  - SSH 隧道与"仅用 SSH 采集宿主资源"（ssh_enabled）是两个独立开关：
    只有显式 ssh_tunnel + ssh_host 才把数据库流量改到 127.0.0.1 本地端点。
"""

import re
import socket
import ssl
import time

from modules.monitor.queries import normalize_db_type

DEFAULT_CONNECT_TIMEOUT = 15
DEFAULT_QUERY_TIMEOUT = 15

_SSH_FIELDS = ('ssh_host', 'ssh_port', 'ssh_user', 'ssh_password',
               'ssh_key_file', 'ssh_key_password', 'ssh_tunnel')

MYSQL_FAMILY = frozenset((
    'mysql', 'mariadb', 'oceanbase', 'starrocks', 'doris', 'tidb',
    'percona', 'tdsqlc_mysql',
))
PG_FAMILY = frozenset((
    'postgresql', 'pg', 'ivorysql', 'kingbase', 'uxdb', 'vastbase',
    'highgo', 'hgdb', 'halodb', 'opengauss', 'greenplum',
))
SQLSERVER_FAMILY = frozenset(('sqlserver', 'sqlserver_jdbc', 'mssql'))
JDBC_ONLY_TYPES = frozenset(('oracle_jdbc', 'uxdb_jdbc'))

# 国产 PG 系库名缺省值：必须用**原始** db_type 查映射（归一后都是 pg，键会全落空）。
# 与首页原口径、数据源测试口径保持一致：都没填时回落用户名同名库（PG 惯例）。
_PG_DEFAULT_DB = {
    'kingbase': 'kingbase', 'uxdb': 'uxdb', 'highgo': 'highgo',
    'hgdb': 'highgo', 'halodb': 'halo',
    'vastbase': 'postgres', 'opengauss': 'postgres',
    'greenplum': 'postgres',
}


class ManagedConnection:
    """DBAPI 连接 + 可选 SSH 隧道的生命周期容器。

    调用方使用 ``.conn`` 执行查询，结束时必须调用 ``close()``（或用 with），
    否则隧道转发线程会泄漏。
    """

    def __init__(self, conn, tunnel=None):
        self.conn = conn
        self.tunnel = tunnel

    def close(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:
            pass
        try:
            if self.tunnel is not None:
                self.tunnel.close()
        except Exception:
            pass
        self.conn = None
        self.tunnel = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def is_supported(db_type):
    """该类型是否走本工厂建连。

    ``*_jdbc`` 类型也纳入工厂（插件 get_connection），但**只允许在隔离子进程
    调用**：主进程的调用方（MonitorEngine / MetricsCollector.collect_one）会
    先路由到 JDBC 批量 / JVM metrics 子进程，绝不会在主进程 startJVM。
    """
    raw = (db_type or '').strip().lower()
    norm = normalize_db_type(raw)
    return bool(raw in MYSQL_FAMILY or raw in PG_FAMILY or raw in SQLSERVER_FAMILY
                or raw in JDBC_ONLY_TYPES
                or norm in ('mysql', 'pg', 'sqlserver', 'oracle')
                or raw in ('dm', 'yashandb', 'mongodb'))


def _ssh_info(inst):
    return {k: inst[k] for k in _SSH_FIELDS if inst.get(k) is not None}


def uses_db_tunnel(inst):
    """该实例是否显式开启「数据库流量经 SSH 隧道」（与 ssh_enabled 无关）。"""
    info = _ssh_info(inst)
    return bool(info.get('ssh_tunnel') and info.get('ssh_host'))


def validate_mongo_tunnel_mode(inst):
    """MongoDB 连接模式与 SSH 隧道的兼容性校验（**建隧道前**调用）。

    - ``mongodb+srv://`` 由 DNS SRV 记录解析真实地址，本地端口转发无法生效；
    - ``replicaSet`` 模式下 pymongo 会按拓扑发现结果直连各成员真实地址，
      绕过本地转发端点。

    两者都直接抛清晰中文错误，不静默回退直连、也不悄悄改用户配置；
    提前校验还能避免"隧道白建一次再报错"。
    """
    info = dict(inst)
    info.update(inst.get('ssh_info') or {})
    mode = (info.get('connect_mode') or 'standard').lower()
    if mode == 'srv':
        raise RuntimeError(
            'MongoDB SRV 连接不支持 SSH 隧道：mongodb+srv:// 由 DNS SRV 记录'
            '解析真实地址，无法经本地端口转发。请改用 standard 模式并填写'
            '跳板机视角可达的 host/port。')
    if str(info.get('replica_set') or '').strip():
        raise RuntimeError(
            'MongoDB 副本集(replicaSet)连接不支持 SSH 隧道：pymongo 会按拓扑'
            '发现结果直连各成员真实地址、绕过本地转发端点。请将该数据源改为'
            '单节点 standard 模式后再启用隧道。')


def resolve_endpoint(inst, host=None, port=None):
    """统一 SSH 隧道判定，返回 ``(tunnel, connect_host, connect_port)``。

    显式开启 ssh_tunnel 且配了 ssh_host 才转发数据库流量；否则原样直连。
    隧道建立失败直接抛错，绝不静默回退直连（用户显式开启即期望走隧道）。
    """
    remote_host = host if host is not None else inst.get('host')
    remote_port = int(port if port is not None else (inst.get('port') or 0))
    info = _ssh_info(inst)
    if not (info.get('ssh_tunnel') and info.get('ssh_host')):
        return None, remote_host, remote_port

    from modules.ssh.tunnel_helper import build_db_ssh_tunnel, should_use_db_tunnel
    if not should_use_db_tunnel(info):
        return None, remote_host, remote_port
    try:
        return build_db_ssh_tunnel(info, remote_host, remote_port)
    except Exception as e:
        raise RuntimeError(
            'SSH 隧道建立失败（数据库 %s:%s 经 %s）：%s'
            % (remote_host, remote_port, info.get('ssh_host'), e)) from e


# ── Oracle 目标解析（首页与大屏共用，避免"探测对、连接错"）────────────

def parse_oracle_target(inst):
    """从实例字段或 jdbc_url 解析 (host, port, service, is_sid, protocol)。"""
    host = inst.get('host')
    port = int(inst.get('port') or 0)
    service = inst.get('service_name') or inst.get('dsn')
    is_sid = False
    protocol = 'tcp'
    jdbc_url = (inst.get('jdbc_url') or '').strip()

    if jdbc_url:
        m = re.search(r'PROTOCOL\s*=\s*(tcps|tcp)', jdbc_url, re.I)
        if m:
            protocol = m.group(1).lower()
        after = jdbc_url.split('@')[-1]
        hm = re.search(r'HOST\s*=\s*([^)\s]+)', jdbc_url, re.I)
        if hm:
            host = hm.group(1)
        pm = re.search(r'PORT\s*=\s*(\d+)', jdbc_url, re.I)
        if pm:
            port = int(pm.group(1))
        sm = re.search(r'SERVICE_NAME\s*=\s*([^)\s]+)', jdbc_url, re.I)
        sidm = re.search(r'\(SID\s*=\s*([^)\s]+)\)', jdbc_url, re.I)
        if sm:
            service = sm.group(1)
            is_sid = False
        elif sidm:
            service = sidm.group(1)
            is_sid = True
        if not service:
            if '//' in after:
                seg = after.split('//')[-1]
                p0 = seg.split(':')
                if len(p0) >= 2:
                    host = p0[0]
                    port = int(p0[1].split('/')[0])
                    if '/' in p0[1]:
                        service = p0[1].split('/', 1)[1]
            else:
                p0 = after.split(':')
                if len(p0) == 3:
                    host, port, service = p0[0], int(p0[1]), p0[2]
                    is_sid = True
    return host, port, service, is_sid, protocol


def _tns_connect_probe_bytes():
    connect_data = b'(CONNECT_DATA=(SID=ORCL))'
    body = b''.join([
        (312).to_bytes(2, 'big'), (312).to_bytes(2, 'big'),
        (0).to_bytes(2, 'big'), (2048).to_bytes(2, 'big'),
        (2048).to_bytes(2, 'big'), (0x07ff).to_bytes(2, 'big'),
        (0).to_bytes(2, 'big'), (1).to_bytes(2, 'big'),
        len(connect_data).to_bytes(2, 'big'), (58).to_bytes(2, 'big'),
        (2048).to_bytes(4, 'big'), (0).to_bytes(4, 'big'), connect_data,
    ])
    length = 8 + len(body)
    return length.to_bytes(2, 'big') + bytes([0x01, 0x00]) + (0).to_bytes(4, 'big') + body


def oracle_protocol_probe(host, port, protocol, timeout=3):
    """TNS 首包嗅探：提前暴露连到 HTTP / TCPS 端口等典型误配。

    失败只抛清晰中文提示（stage=connect），网络不可达时静默跳过，由正式
    建连报错。仅首页实时采集默认开启；大屏沿用原有行为（不额外探测）。
    """
    s = None
    data = b''
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        try:
            s.sendall(_tns_connect_probe_bytes())
        except Exception:
            pass
        s.settimeout(2.0)
        while len(data) < 16:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    except Exception:
        return
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass
    if not data:
        return
    if data[:4] == b'HTTP':
        raise ValueError(
            'Oracle 连接失败：该端口返回了 HTTP 响应，疑似连到了 Web/HTTP 服务'
            '（如 8080 / 443 控制台端口），并非 Oracle 监听器端口。'
            '请核对 jdbc_url 中 @ 后的 host:port 是否为真实 listener 端口（通常 1521）。')
    if data[0] in (0x15, 0x16):
        raise ValueError(
            'Oracle 连接失败：该端口返回了 TLS/SSL 握手，疑似需要 TCPS(SSL) 连接'
            '（常用 2484 端口）。请在 jdbc_url 中使用 PROTOCOL=tcps 或改用 SSL 连接。')
    if len(data) >= 3 and 1 <= data[2] <= 12:
        return
    raise ValueError(
        'Oracle 连接失败：该端口返回了非 TNS 的协议数据，可能不是 Oracle 监听器端口。'
        '请核对 jdbc_url 中的 host:port。')


def _oracle_dsn(inst, host, port, service, is_sid):
    """统一 DSN 构造：TNS 描述符保留原文（隧道场景替换 HOST/PORT），
    其余统一用 makedsn，避免裸服务名被当 TNS 别名解析（DPY-4027）。"""
    import oracledb
    jdbc_url = (inst.get('jdbc_url') or '').strip()
    if jdbc_url.lstrip().upper().startswith('(DESCRIPTION'):
        if (host, port) != (inst.get('host'), int(inst.get('port') or 0)):
            dsn = re.sub(r'\(HOST\s*=\s*[^)]+\)', '(HOST=%s)' % host,
                         jdbc_url, flags=re.I)
            dsn = re.sub(r'\(PORT\s*=\s*\d+\)', '(PORT=%d)' % int(port),
                         dsn, flags=re.I)
            return dsn
        return jdbc_url
    if service:
        if is_sid:
            return oracledb.makedsn(host=host, port=port, sid=service)
        return oracledb.makedsn(host=host, port=port, service_name=service)
    return '%s:%s/orcl' % (host, port)


def _create_dbapi_connection(inst, host, port, raw, norm,
                             connect_timeout, query_timeout,
                             probe_oracle_protocol=False, tunneled=False):
    user = inst.get('user')
    password = inst.get('password')

    # JVM 插件型：仅在隔离子进程调用（见 is_supported 文档），主进程绝不到此。
    # 隧道已由本工厂预先建好，host/port 是 127.0.0.1 本地端点；插件按该端点
    # 建 JDBC 连接即可透明穿透。
    if raw == 'oracle_jdbc':
        from modules.pluginkit.loader import get_plugin_module
        mod = get_plugin_module('oracle_jdbc')
        if mod is None or not hasattr(mod, 'get_connection'):
            raise RuntimeError('oracle_jdbc 插件未启用或缺少 get_connection 入口')
        return mod.get_connection(
            host=host, port=int(port or 1521), user=user, password=password,
            service_name=inst.get('service_name') or 'ORCL',
            sysdba=bool(inst.get('sysdba')),
            jdbc_url=inst.get('jdbc_url') or None,
        )

    if raw == 'uxdb_jdbc':
        from modules.pluginkit.loader import get_plugin_module
        mod = get_plugin_module('uxdb_jdbc')
        if mod is None or not hasattr(mod, 'get_connection'):
            raise RuntimeError('uxdb_jdbc 插件未启用或缺少 get_connection 入口')
        return mod.get_connection(
            host=host, port=int(port or 33060), user=user, password=password,
            database=inst.get('database') or 'uxdb',
            jdbc_url=inst.get('jdbc_url') or None,
        )

    if raw in MYSQL_FAMILY or norm == 'mysql':
        import pymysql
        db_name = inst.get('database')
        if raw == 'oceanbase':
            db_name = db_name or 'sys'
        elif not db_name:
            db_name = 'mysql'
        return pymysql.connect(
            host=host, port=int(port), user=user, password=password,
            database=db_name, charset='utf8mb4',
            connect_timeout=connect_timeout,
            read_timeout=query_timeout, write_timeout=query_timeout,
            autocommit=True,
        )

    if raw in PG_FAMILY or norm == 'pg':
        import psycopg2
        db_name = (inst.get('database') or _PG_DEFAULT_DB.get(raw) or user
                   or 'postgres')
        return psycopg2.connect(
            host=host, port=int(port), user=user, password=password,
            dbname=db_name, client_encoding='UTF8',
            connect_timeout=connect_timeout,
            options='-c statement_timeout=%d' % int(query_timeout * 1000),
        )

    if raw == 'oracle' or norm == 'oracle':
        import oracledb
        ohost, oport, service, is_sid, protocol = parse_oracle_target(inst)
        if probe_oracle_protocol and ohost and oport:
            oracle_protocol_probe(host, port, protocol, timeout=connect_timeout)
        dsn = _oracle_dsn(inst, host, port, service, is_sid)
        mode = oracledb.SYSDBA if inst.get('sysdba') else oracledb.AUTH_MODE_DEFAULT
        params = dict(user=user, password=password, dsn=dsn, mode=mode,
                      tcp_connect_timeout=connect_timeout)
        if protocol == 'tcps':
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            params['ssl_context'] = ctx
        try:
            conn = oracledb.connect(**params)
        except TypeError as te:
            if 'tcp_connect_timeout' not in str(te):
                raise
            params.pop('tcp_connect_timeout', None)
            conn = oracledb.connect(**params)
        except Exception as e:
            # 薄模式不支持 11g / 老口令验证器 → 与数据源测试同口径切厚模式。
            from modules.oracle_conn_test import (
                ensure_oracle_thick, thin_error_needs_thick)
            if not thin_error_needs_thick(e):
                raise
            ok, hint = ensure_oracle_thick()
            if not ok:
                raise RuntimeError(hint) from e
            conn = oracledb.connect(**params)
        try:
            conn.call_timeout = int(query_timeout * 1000)
        except Exception:
            pass
        return conn

    if raw in SQLSERVER_FAMILY or norm in ('sqlserver', 'mssql'):
        import pyodbc
        conn_str = inst.get('connection_string') or ''
        if not conn_str:
            drivers = []
            try:
                drivers = [d for d in pyodbc.drivers() if 'sql server' in d.lower()]
            except Exception:
                pass
            modern = [d for d in drivers if 'odbc driver' in d.lower()]
            native = [d for d in drivers if 'native client' in d.lower()]
            if modern:
                driver, legacy = '{%s}' % modern[-1], False
            elif native:
                driver, legacy = '{%s}' % native[0], False
            elif drivers:
                driver, legacy = '{%s}' % drivers[-1], True
            else:
                driver, legacy = '{ODBC Driver 17 for SQL Server}', False
            safe_pwd = str(password or '').replace('}', '}}')
            conn_str = (
                'DRIVER=%s;SERVER=%s,%s;UID=%s;PWD=%s;'
                'DATABASE=%s;Connect Timeout=%d;'
                % (driver, host, port, user, safe_pwd,
                   inst.get('database') or 'master', connect_timeout)
            )
            # 新版驱动必须同时带 TrustServerCertificate，否则自签证书场景
            # 即使测试连接能过，大屏仍报 SSL Provider 错误被误判 down。
            if not legacy:
                conn_str += 'TrustServerCertificate=yes;Encrypt=yes;'
        return pyodbc.connect(conn_str, timeout=connect_timeout)

    if raw == 'dm':
        import dmPython
        return dmPython.connect(user=user, password=password,
                                server='%s:%s' % (host, port))

    if raw == 'yashandb':
        try:
            import yasdb
        except ImportError:
            raise RuntimeError('YashanDB 驱动未安装，请先安装 yasdb 驱动。')
        return yasdb.connect(host=host, port=int(port), user=user, password=password)

    if raw == 'mongodb':
        # MongoDB 返回 MongoClient（非 DBAPI，但同样有 close()）。
        # ⚠️ 必须用 open_db_connection 传入的 host/port：开启 SSH 隧道时它们
        # 已是 127.0.0.1:local_port。早期版本这里误用 inst 的原地址，导致
        # 隧道建了却没被使用（"测试连接正常、首页与大屏却连不上"）。
        from urllib.parse import quote_plus
        from pymongo import MongoClient
        info = dict(inst)
        info.update(inst.get('ssh_info') or {})
        mhost = host if host is not None else (info.get('host') or '127.0.0.1')
        mport = int(port if port is not None else (info.get('port') or 27017))
        muser = info.get('user') or ''
        mpwd = info.get('password') or ''
        database = info.get('database') or 'admin'
        connect_mode = (info.get('connect_mode') or 'standard').lower()
        auth_source = info.get('auth_source') or 'admin'
        auth_mechanism = info.get('auth_mechanism') or ''
        replica_set = info.get('replica_set') or ''
        if tunneled:
            # 隧道端点只代表单个节点：srv / replicaSet 会让 pymongo 连到
            # 隧道外的真实地址，且与 directConnection 互斥。
            connect_mode = 'standard'
            replica_set = ''

        scheme = 'mongodb+srv://' if connect_mode == 'srv' else 'mongodb://'
        auth_part = ''
        if muser:
            eu = quote_plus(muser)
            auth_part = ('%s:%s@' % (eu, quote_plus(mpwd))) if mpwd else ('%s@' % eu)
        host_part = mhost if connect_mode == 'srv' else '%s:%d' % (mhost, mport)
        params = []
        if auth_source and (auth_source != 'admin' or muser):
            params.append('authSource=%s' % quote_plus(auth_source))
        if auth_mechanism:
            params.append('authMechanism=%s' % quote_plus(auth_mechanism))
        if replica_set:
            params.append('replicaSet=%s' % quote_plus(replica_set))
        query = '?' + '&'.join(params) if params else ''
        uri = '%s%s%s/%s%s' % (scheme, auth_part, host_part, database, query)
        kwargs = {'serverSelectionTimeoutMS': 5000,
                  'connectTimeoutMS': 5000, 'socketTimeoutMS': 10000}
        if tunneled:
            # 固定在本地转发端点，禁止拓扑发现把流量引到隧道外
            kwargs['directConnection'] = True
        if info.get('tls'):
            kwargs['tls'] = True
            if info.get('tls_ca_file'):
                kwargs['tlsCAFile'] = info['tls_ca_file']
            if info.get('tls_cert_key_file'):
                kwargs['tlsCertificateKeyFile'] = info['tls_cert_key_file']
            if info.get('tls_allow_invalid_certs'):
                kwargs['tlsAllowInvalidCertificates'] = True
        return MongoClient(uri, **kwargs)

    return None


def open_db_connection(inst, connect_timeout=DEFAULT_CONNECT_TIMEOUT,
                       query_timeout=DEFAULT_QUERY_TIMEOUT,
                       probe_oracle_protocol=False):
    """按统一口径建连，返回 ``ManagedConnection``；不支持的类型返回 None。

    连接失败时自动关闭已建立的隧道，避免 paramiko 转发线程泄漏。
    调用方负责按阶段打标：本函数抛出的异常均属 **connect** 阶段。
    """
    raw = (inst.get('db_type') or '').strip().lower()
    if not is_supported(raw):
        return None
    norm = normalize_db_type(raw)

    # 隧道建立前先校验 MongoDB 连接模式，避免白建一次 SSH 连接
    if raw == 'mongodb' and uses_db_tunnel(inst):
        validate_mongo_tunnel_mode(inst)

    ohost = oport = None
    if raw == 'oracle' or norm == 'oracle':
        ohost, oport, _svc, _is_sid, _proto = parse_oracle_target(inst)

    tunnel, host, port = resolve_endpoint(inst, ohost, oport)
    try:
        conn = _create_dbapi_connection(
            inst, host, port, raw, norm,
            connect_timeout=connect_timeout, query_timeout=query_timeout,
            probe_oracle_protocol=probe_oracle_protocol,
            tunneled=tunnel is not None)
    except Exception:
        try:
            if tunnel is not None:
                tunnel.close()
        except Exception:
            pass
        raise
    if conn is None:
        try:
            if tunnel is not None:
                tunnel.close()
        except Exception:
            pass
        return None
    return ManagedConnection(conn, tunnel)
