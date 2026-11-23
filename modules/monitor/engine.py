# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
实时监控引擎 — 统一采集器的 SQL 执行层与只读投影适配器。

架构（改造后，全系统只有一条采集循环）：
- UnifiedCollector._loop：唯一 15s 常驻循环，每实例只建一条连接
- connection_scope：把该连接绑定给本轮所有 SQL 通道复用
- MonitorEngine：``_collect_slow`` / ``_collect_conn`` / ``run_query``
  在同一条连接上执行；public 读接口从 UnifiedStore 投影
- UnifiedStore：唯一时序存储（内存最新帧 + SQLite），首页 / 大屏 /
  实时慢查询 / 热力图全部读同一份数据

本模块**不再启动后台线程**，也不再维护独立的内存缓冲。
"""

import time
import os
import sys
import threading
import json
import logging
from collections import deque
from modules.pro.instance_manager import get_instance_manager
from modules.core.paths import PROJECT_ROOT
import modules.monitor.queries as mq
from modules.monitor.screen_metrics import SCREEN_SQLS
from modules.monitor.connection_factory import open_db_connection

# 监控专属 logger：经 loghub 汇聚到「运行日志」页（category=monitor），且默认不写
# 共享控制台（由 MonitorConsoleFilter 过滤），从根上避免污染巡检 / 控制台输出。
_log = logging.getLogger('dbcheck.monitor')


# ── JDBC 批量采集通道 ─────────────────────────────────────────
# gbase / db2 / clickhouse 无 python 原生驱动（或驱动不可用），统一走
# 「独立非 gevent 子进程 + JDBC」通道：主进程绝不 startJVM（否则 JPype/JVM
# 与 gevent hub 死锁，整个 Web UI 冻结）。每轮每实例只 spawn 一次子进程，
# 一次跑完该实例全部采集 SQL（连接/慢查/性能计数/容量），结果缓存供本轮复用。
JDBC_BATCH_DB_TYPES = frozenset(('gbase', 'db2', 'clickhouse'))
JDBC_BATCH_TTL = 12.0        # 批量结果缓存时长（s），略短于采样间隔 15s
JDBC_BATCH_TIMEOUT = 90      # 子进程硬超时（含 JVM 冷启动 4~10s）

# ── 必须全程 JDBC 的数据源（*_jdbc + 标准 pg）───────────────
# 这些类型被用户配置成 *_jdbc 的原因就是「python 原生驱动连不上」（旧版
# Oracle / TCPS / EZConnect / TNS 别名等），且 jdbc_url 优先于 host/port
# （见 instance_manager 字段注释）；metrics_collector 侧早有约定
# 「oracle_jdbc 一律走插件 JDBC，绝不走 oracledb」。监控引擎此前把它们
# 归一成 oracle/pg 走 oracledb thin / psycopg2 直连 → 实时监控（pro collector，
# 走 JDBC 插件）有数据、监控大屏（MonitorEngine）判宕机。
# 修复：路由进 JDBC 批量子进程通道（主进程绝不起 JVM）；连接器用原始插件
# id（oracle_jdbc/uxdb），采集 SQL 模板用归一键（oracle/pg）。
# SQL Server 选了 *_jdbc 同样必须全程 JDBC：此前漏配该类型，首页深采与大屏
# 落到 connection_factory 的 SQLSERVER_FAMILY → pyodbc，中心机缺 ODBC Driver 时
# 抛 [01000] [unixODBC] Can't open，被 _error_stage 判 connect → 大屏误判 down
# （数据库实际存活、JDBC 测试连接也通过）。与 oracle_jdbc 同构：连接器用原始
# 插件 id，采集 SQL 模板用归一键 sqlserver。
JDBC_ONLY_TYPES = {
    # raw db_type   → (JDBC 连接器 db_type, monitor 模板键)
    'oracle_jdbc': ('oracle_jdbc', 'oracle'),
    'uxdb_jdbc':   ('uxdb',        'pg'),
    'sqlserver_jdbc': ('sqlserver_jdbc', 'sqlserver'),
    # 标准 PostgreSQL：数据源配置界面允许选择「JDBC 驱动版本（默认=激活版本）」，
    # 测试连接与巡检也都走 JDBC（test_pg_connection → run_jdbc_test_subprocess）。
    # 此前仅 *_jdbc 类型进本通道，标准 pg 被归一后落回 connection_factory 的
    # psycopg2 原生分支 → 数据源里配的 driver_version 对实时监控/大屏完全失效，
    # 且库名留空时会回落到用户名（FATAL: database "<user>" does not exist）。
    # 与 uxdb_jdbc 同构：连接器用原始类型 pg（JDBC_PROFILES['pg'] 默认库
    # postgres、驱动 org.postgresql.Driver），采集 SQL 模板复用 pg，通道已验证。
    'pg':          ('pg',          'pg'),
    # 存量实例可能直接存 'postgresql'（instance_manager 仍兼容该写法）：
    # 归一后同为 pg，连接器仍用 JDBC_PROFILES 的 pg 条目。
    'postgresql':  ('pg',          'pg'),
}


# ── 驱动异常 → 友好提示 ──────────────────────────────────────
# 驱动原始异常（如 "<class 'dmPython.Connection'> returned a result with
# an exception set"）对用户无意义，按常见根因归类成可操作的提示；
# 完整异常始终由调用方写入服务端日志，此处只负责面向用户的文案。
_ERROR_PATTERNS = [
    # Oracle：JDBC 子进程把原始 ORA 错误透传上来，按可操作根因归类，
    # 避免一律退化成「请查看服务端日志」让用户无法自助定位。
    (('ora-28009', 'ora-28007'),
     '以 SYS 登录必须勾选 SYSDBA（数据源高级配置）'),
    (('ora-12514', 'ora-12505', 'ora-12504', 'ora-12541', 'ora-12154'),
     '服务名/SID 或监听配置不正确，请检查服务名、SID 模式与端口'),
    (('ora-01031', 'insufficient privileges'),
     '数据库账号权限不足，请授予监控所需视图的查询权限'),
    (('ora-00942', 'ora-00904'),
     '对象不存在或无权限，请检查监控账号权限与数据库版本'),
    (('no suitable driver', 'classnotfoundexception', 'noclassdeffounderror'),
     'JDBC 驱动未正确加载，请到「数据库驱动管理」检查驱动版本'),
    (('returned a result with an exception set',),
     '连接失败，请检查地址、端口、用户名密码及登录配置'),
    (('connection refused', '拒绝连接', '10061', 'cannot connect'),
     '无法连接数据库主机（数据库未启动或端口不通）'),
    (('timed out', 'timeout', '超时'),
     '连接超时（网络不通或防火墙拦截）'),
    (('getaddrinfo failed', 'name or service not known', 'unknown host',
      'nodename nor servname'),
     '主机地址无法解析，请检查地址配置'),
    (('access denied', 'authentication', 'login failed', 'ora-01017',
      'invalid username', 'password authentication', '密码'),
     '用户名或密码错误'),
    (('unknown database', 'database does not exist', 'database "',
      'specified database'),  # psycopg2: database "xxx" does not exist（库名夹在引号内）
     '数据库/服务名不存在，请检查库名配置'),
    (('ssl', 'certificate'),
     'SSL/证书校验失败'),
    (('view server state',),
     'SQL Server 监控账号缺少 VIEW SERVER STATE 权限，请先授权'),
]


def _friendly_db_error(e):
    """把驱动异常翻译成用户可读的提示；未命中已知模式时给通用提示。

    未命中时附带原始异常首行摘要：大屏「连接/会话」下钻 tab 的 conn_err
    展示的就是本函数结果，若只给「请查看服务端日志」，用户无法自助定位
    具体 ORA / JDBC 错误码（服务端日志里才有）。摘要截断 160 字符，避免
    撑破卡片布局；完整异常仍由调用方写服务端日志。
    """
    low = str(e).lower()
    for keys, msg in _ERROR_PATTERNS:
        if any(k in low for k in keys):
            return msg
    raw = ' '.join(str(e).split())[:160]
    if raw:
        return '数据库连接或查询异常：%s' % raw
    return '数据库连接或查询异常，请检查实例配置或查看服务端日志'


class MonitorQueryError(RuntimeError):
    """带阶段标记的采集异常。

    stage='connect'：建连阶段失败，实例真不可达 → 大屏应判 down。
    stage='query'  ：连接已建立，SQL 执行阶段失败（视图权限不足、
                     对象/列不存在、版本差异等）→ 实例可达，不得判
                     down 触发误告警，只标「采集不完整」。
    """

    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def _error_stage(e, default='connect'):
    """推断采集失败阶段；无结构化标记时按已知 SQL 层错误兜底分类。

    用于 JDBC 子进程、SSH 隧道、外层兜底 catch 等无法直接区分阶段的路径。
    """
    stage = getattr(e, 'stage', None)
    if stage in ('connect', 'query'):
        return stage
    low = str(e).lower()
    # 建连已成功、仅 SQL/权限/对象/版本差异导致的失败：大屏只能标
    # "采集不完整"(warn)，不得判 down 触发误告警。白名单按数据库族分组，
    # 全量错误一律保留在服务端日志与下钻 conn_err 里供定位。
    _query_only = (
        # Oracle：视图/列不存在、权限不足、SQL 语法/标识符/类型不兼容
        'ora-00942', 'ora-00904', 'ora-01031', 'ora-00933',
        'ora-00911', 'ora-00907', 'ora-01756',
        'ora-00923', 'ora-00936', 'ora-00921', 'ora-00937',
        'ora-00979', 'ora-00972', 'ora-01403', 'ora-01422', 'ora-01722',
        # PostgreSQL 系：视图/函数/列/类型不存在、权限不足
        'relation "', 'column "', 'function "', 'operator "',
        'type "', 'schema "', 'extension "',
        'must be superuser', 'permission denied for',
        # SQL Server：系统视图权限、对象/列不存在（含中文实例文案）
        'view server state', 'the select permission was denied',
        'execute permission denied', 'invalid object name',
        '对象名', '列名', '权限不足',
        # 通用 SQL 层错误（JDBC 子进程未带 stage 时的兜底）
        'permission denied', 'must be owner of', 'unknown column',
        'syntax error', 'unrecognized configuration parameter',
        'missing from-clause entry',
    )
    if any(k in low for k in _query_only):
        return 'query'
    return default


class MonitorEngine:
    """实时监控引擎 — 统一采集器的只读投影适配器。

    历史版本本类自带 10s 后台采集循环，与首页 30s、大屏 15s 两条循环
    三路重复建连；且 ``_connect_and_query`` 每条 SQL 各建一条连接。
    现在**不再启动任何线程**，只保留两件事：
      1. SQL 采集能力（``_collect_slow`` / ``_collect_conn`` /
         ``run_query``），供 UnifiedCollector 在同一条连接上调用；
      2. public 读接口，统一从 UnifiedStore 投影，返回结构与旧版完全一致。
    """

    # ── 默认配置 ──
    DEFAULT_INTERVAL = 15  # 与统一采集器一致（大屏速率口径）
    MAX_HISTORY = 72
    QUERY_TIMEOUT = 15     # 单次查询超时（秒）

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        # JDBC 批量采集缓存: {iid: {'ts', 'res', 'lock'}}；统一采集器一轮
        # 只触发一次，后续各组 SQL 全部命中缓存，不重复 spawn 子进程。
        self._jdbc_cache = {}
        # 慢查询主 SQL 依赖缺失记忆（pg_stat_statements 未安装等）
        self._slow_prim_skip = set()

    @staticmethod
    def _store():
        from modules.monitor.unified_collector import get_unified_collector
        return get_unified_collector().store

    @staticmethod
    def _collector():
        from modules.monitor.unified_collector import get_unified_collector
        return get_unified_collector()

    # ═══════════════════════════════════════════════════════════
    #  启停控制：全部委托唯一采集器（保持 /api/monitor/config 契约）
    # ═══════════════════════════════════════════════════════════

    def start(self, interval=None):
        self._collector().start(interval=interval)

    def stop(self):
        self._collector().stop()

    def set_interval(self, interval):
        self._collector().set_interval(interval)

    @property
    def is_running(self):
        return self._collector().is_running

    @property
    def interval(self):
        return self._collector().interval

    # ═══════════════════════════════════════════════════════════
    #  数据读取（投影统一存储，返回结构与旧版一致）
    # ═══════════════════════════════════════════════════════════

    def get_slow_queries(self):
        return self._store().get_slow_queries()

    def get_connections(self):
        return self._store().get_connections()

    def get_conn_history(self):
        return self._store().get_conn_history()

    def get_status(self):
        return self._store().get_status()

    def trigger_collect(self):
        """手动触发统一采集器的一轮（不阻塞调用方）。"""
        self._collector().trigger_collect()

    # 后台循环已移除：全系统只有 UnifiedCollector._loop 一条采集线程。
    # 单实例的慢查询 / 连接数采集由统一采集器在同一条借用连接上调用
    # 下面的 _collect_slow / _collect_conn，采集结果写入 UnifiedStore。

    # ═══════════════════════════════════════════════════════════
    #  采集实现
    # ═══════════════════════════════════════════════════════════

    def _collect_slow(self, instance_id, db_type, label):
        sql = mq.SLOW_QUERY_TEMPLATES.get(db_type)
        if not sql:
            return {'data': [], 'error': f'不支持的类型: {db_type}',
                    'ts': time.time(), 'db_type': db_type, 'label': label}

        fallback_sql = mq.SLOW_QUERY_FALLBACK_TEMPLATES.get(db_type)
        rows = None
        if instance_id not in self._slow_prim_skip:
            try:
                rows = self._connect_and_query(instance_id, sql)
            except Exception as e:
                low = str(e).lower()
                # 依赖对象缺失（pg_stat_statements / performance_schema 未安装）
                # 或列不存在（Oracle 11gR1 无 max_elapsed_time → ORA-00904
                # invalid identifier，#63）→ 记忆化：首行错误摘要打印一次，
                # 后续轮直接 fallback，不再重复刷日志
                dep_missing = ('does not exist' in low or "doesn't exist" in low
                               or 'ora-00904' in low or 'invalid identifier' in low)
                if fallback_sql and dep_missing:
                    self._slow_prim_skip.add(instance_id)
                    _log.info("%s 慢查询主 SQL 依赖缺失（%s），后续轮次直接使用 fallback",
                              label, str(e).splitlines()[0][:100])
                else:
                    _log.warning("慢查询 SQL 失败 %s: %s", label, e)
                if not fallback_sql:
                    return {'data': [], 'error': _friendly_db_error(e),
                            'err_kind': _error_stage(e),
                            'ts': time.time(), 'db_type': db_type, 'label': label}
        if rows is None:
            # fallback：主 SQL 失败降级，或已记忆依赖缺失直接走此路（静默）
            try:
                rows = self._connect_and_query(instance_id, fallback_sql)
                if instance_id not in self._slow_prim_skip:
                    _log.info("%s 使用 fallback 慢查询 SQL", label)
            except Exception as fb:
                _log.warning("慢查询 fallback 亦失败 %s: %s", label, fb)
                return {'data': [], 'error': _friendly_db_error(fb),
                        'err_kind': _error_stage(fb),
                        'ts': time.time(), 'db_type': db_type, 'label': label}

        result = {
            'data': rows,
            'error': None,
            'ts': time.time(),
            'db_type': db_type,
            'label': label,
        }
        return result

    def _collect_conn(self, instance_id, db_type, label):
        conn_sql = mq.CONNECTION_TEMPLATES.get(db_type)
        if not conn_sql:
            return {'data': [], 'error': f'不支持的类型: {db_type}',
                    'ts': time.time(), 'total': 0, 'max_conn': 0,
                    'connections': {}, 'usage_pct': 0,
                    'db_type': db_type, 'label': label}

        try:
            rows = self._connect_and_query(instance_id, conn_sql)
        except Exception as e:
            _log.warning("连接 SQL 失败 %s: %s", label, e)
            return {'data': [], 'error': _friendly_db_error(e),
                    'err_kind': _error_stage(e),
                    'ts': time.time(), 'total': 0, 'max_conn': mq.MAX_CONNECTION_DEFAULTS.get(db_type, 100),
                    'connections': {'active': 0, 'idle': 0, 'blocked': 0}, 'usage_pct': 0,
                    'db_type': db_type, 'label': label}

        # 获取最大连接数
        max_sql = mq.MAX_CONN_QUERY_SQL.get(db_type)
        max_conn = mq.MAX_CONNECTION_DEFAULTS.get(db_type, 100)
        if max_sql:
            try:
                max_rows = self._connect_and_query(instance_id, max_sql)
                if max_rows and max_rows[0]:
                    val = list(max_rows[0].values())[0]
                    try:
                        max_conn = int(val)
                    except (ValueError, TypeError):
                        pass
            except Exception:
                pass

        total = len(rows)
        # 统计连接状态分布
        active = 0
        idle = 0
        blocked = 0
        for r in rows:
            state_val = None
            for k, v in r.items():
                if 'state' in k.lower():
                    state_val = str(v).lower() if v else ''
                    break
            if state_val in ('sleeping', 'idle', 'inactive'):
                idle += 1
            elif state_val in ('waiting', 'wait', 'blocked', 'locked'):
                blocked += 1
            else:
                active += 1

        result = {
            'data': rows,
            'error': None,
            'ts': time.time(),
            'total': total,
            'max_conn': max_conn,
            'connections': {'active': active, 'idle': idle, 'blocked': blocked},
            'usage_pct': round(total / max_conn * 100, 1) if max_conn > 0 else 0,
            'db_type': db_type,
            'label': label,
        }
        return result

    # ═══════════════════════════════════════════════════════════
    #  JDBC 批量子进程采集（gbase / db2 / clickhouse）
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _jdbc_batch_sqls(db_type):
        """该类型一轮采集所需的全部 SQL（模板去重，主/备选全带上）。"""
        sqls, seen = [], set()

        def _add(s):
            if s and s not in seen:
                seen.add(s)
                sqls.append(s)

        for src in (mq.CONNECTION_TEMPLATES, mq.MAX_CONN_QUERY_SQL,
                    mq.SLOW_QUERY_TEMPLATES, mq.SLOW_QUERY_FALLBACK_TEMPLATES):
            _add(src.get(db_type))
        # 大屏指标 SQL：family 条目为 {key: sql}，逐条带上（repl/repl8 同理）
        fam_sqls = SCREEN_SQLS.get(db_type) or {}
        for s in fam_sqls.values():
            _add(s)
        # 首页深采 SQL：让 oracle_jdbc / uxdb_jdbc 的一轮采集只用**一次**
        # 批量子进程，首页指标由统一采集器从批量结果投影，不再单独 spawn
        # jdbc_metrics_cli（详见 unified_collector.BatchResultConnection）。
        try:
            from modules.pro.metrics_collector import DEEP_METRIC_SQLS
            for s in DEEP_METRIC_SQLS:
                _add(s)
        except Exception:
            pass
        return sqls

    def _jdbc_query(self, inst, db_type, sql, timeout=None, template_key=None):
        """JDBC 批量通道查询单条 SQL：结果来自每轮一次的子进程批量采集缓存。

        首个调用方（引擎采集线程或大屏采样线程）触发一次子进程批量执行，
        同轮内其余查询（含另一侧线程的 run_query）直接读缓存，避免每条 SQL
        都冷启动一次 JVM（4~10s）。inst 为已解密实例 dict。
        ``template_key``：采集 SQL 模板键（oracle/pg 等），缺省同 db_type；
        *_jdbc 专属类型的连接器 id 与模板键不同（oracle_jdbc vs oracle）。
        """
        instance_id = inst.get('id')
        cache = self._jdbc_cache.get(instance_id)
        if cache and time.time() - cache['ts'] <= JDBC_BATCH_TTL:
            return self._jdbc_pick(cache['res'], sql)

        lock = cache['lock'] if cache else threading.Lock()
        with lock:
            cache = self._jdbc_cache.get(instance_id)
            if cache and time.time() - cache['ts'] <= JDBC_BATCH_TTL:
                return self._jdbc_pick(cache['res'], sql)
            res = self._jdbc_run_batch(inst, db_type, timeout,
                                       template_key=template_key)
            self._jdbc_cache[instance_id] = {'ts': time.time(), 'res': res,
                                             'lock': lock}
            return self._jdbc_pick(res, sql)

    @staticmethod
    def _jdbc_pick(res, sql):
        """从批量结果中取单条 SQL 的行；错误标记转成带阶段的异常。

        错误 tuple 形如 ('__err__', msg) 或 ('__err__', msg, stage)，
        stage 由 JDBC 采集子进程在结果里标注（建连失败 / 单条 SQL 失败）。
        """
        entry = res.get(sql)
        if entry is None:
            raise MonitorQueryError(
                'query', '该类型批量采集未覆盖此 SQL: %s' % sql[:60])
        if isinstance(entry, tuple):
            raise MonitorQueryError(
                entry[2] if len(entry) > 2 else 'connect', entry[1])
        return entry

    def _jdbc_run_batch(self, inst, db_type, timeout=None, template_key=None):
        """spawn 隔离子进程，一次跑完该实例本轮全部采集 SQL。

        参数经 stdin 临时文件传入（密码不进进程列表），结果取 stdout 中
        最后一个以 { 开头的行（与 jdbc_test_cli / jdbc_metrics_cli 同源约定）。
        ``db_type`` 传给 JDBC 连接器（原始插件 id，如 oracle_jdbc/uxdb）；
        ``template_key`` 决定采集 SQL 模板集合（oracle/pg 等），缺省同 db_type。
        """
        import subprocess as _sp
        import tempfile as _tf

        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, '--jdbc-collect-cli']
        else:
            cmd = [sys.executable, os.path.join(
                str(PROJECT_ROOT), 'modules', 'monitor', 'jdbc_collect_cli.py')]

        sqls = self._jdbc_batch_sqls(template_key or db_type)
        payload = {
            'db_type': db_type,
            'host': inst.get('host'),
            'port': inst.get('port'),
            'user': inst.get('user') or '',
            'password': inst.get('password') or '',
            'database': inst.get('database') or inst.get('service_name') or '',
            # Oracle 专属字段必须透传：监控子进程此前漏传 service_name/sid/
            # use_sid/sysdba，导致 build_jdbc_url 对 oracle_jdbc 落到硬编码默认
            # 'ORCLCDB'（或旧 SID 格式默认 'ORCL'），连错服务名 → ORA-12514 →
            # 大屏误判宕机；而测试连接（插件 test_connection）正确传 service_name
            # → 测试过、监控挂。sys 用户必须以 SYSDBA 登录，漏传则同样连不上。
            'service_name': inst.get('service_name') or '',
            'sid': inst.get('sid') or '',
            'use_sid': bool(inst.get('use_sid', False)),
            'sysdba': bool(inst.get('sysdba', False)),
            'jdbc_url': inst.get('jdbc_url') or '',
            'ssl': bool(inst.get('ssl', False)),
            'driver_version': inst.get('driver_version') or '',
            'gbase_server_name': inst.get('gbase_server_name') or '',
            # SSH 隧道（数据库经隧道连接）：与巡检/实时监控同口径，仅显式开启
            # ssh_tunnel 且配了 ssh_host 才在采集子进程内建隧道（子进程非 gevent
            # 环境，paramiko 安全）。jdbc_collect_cli 据此把 host/port 改写为
            # 隧道本地端点，JDBC 驱动直连本地端点透明穿透。
            'ssh_tunnel': bool(inst.get('ssh_tunnel', False)),
            'ssh_host':   inst.get('ssh_host') or '',
            'ssh_port':   int(inst.get('ssh_port', 22) or 22),
            'ssh_user':   inst.get('ssh_user') or '',
            'ssh_password': inst.get('ssh_password') or '',
            'ssh_key_file':  inst.get('ssh_key_file') or '',
            'ssh_key_password': inst.get('ssh_key_password') or '',
            'queries': sqls,
        }

        _in_fd, _in_path = _tf.mkstemp(prefix='dbc_mjdbc_in_', suffix='.json')
        _out_fd, _out_path = _tf.mkstemp(prefix='dbc_mjdbc_out_', suffix='.log')
        os.close(_out_fd)
        try:
            with os.fdopen(_in_fd, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=True)

            env = os.environ.copy()
            env['DBCheck_NO_GEVENT_PATCH'] = '1'   # 子进程绝不能被 monkey-patch
            env['PYTHONIOENCODING'] = 'utf-8'

            kw = {}
            if os.name == 'nt':
                kw['creationflags'] = (getattr(_sp, 'CREATE_NO_WINDOW', 0x08000000)
                                       | getattr(_sp, 'CREATE_NEW_PROCESS_GROUP', 0x00000200))
            else:
                kw['start_new_session'] = True

            with open(_in_path, 'r', encoding='utf-8') as fin, \
                    open(_out_path, 'w', encoding='utf-8', errors='replace') as fout:
                proc = _sp.Popen(cmd, stdin=fin, stdout=fout, stderr=_sp.STDOUT,
                                 env=env, cwd=str(PROJECT_ROOT), **kw)
                try:
                    rc = proc.wait(timeout=timeout or JDBC_BATCH_TIMEOUT)
                except _sp.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    raise RuntimeError('JDBC 采集子进程超时（%ds）'
                                       % (timeout or JDBC_BATCH_TIMEOUT))

            # 解析输出：最后一个以 { 开头的行是结构化结果
            result = None
            with open(_out_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('{'):
                        result = line
            if result is None:
                # 附带子进程输出尾部，便于定位 spawn/导入失败的真实原因
                with open(_out_path, 'r', encoding='utf-8', errors='replace') as f:
                    _tail = f.read()[-400:]
                raise RuntimeError('JDBC 采集子进程无结果输出（exit=%s）: %s'
                                   % (rc, _tail.replace('\n', ' | ')))
            try:
                data = json.loads(result)
            except Exception as e:
                raise RuntimeError('JDBC 采集子进程输出解析失败: %s' % e)

            fatal = (data or {}).get('fatal')
            res = {}
            for i, s in enumerate(sqls):
                entry = (data or {}).get('q%d' % i)
                if fatal:
                    res[s] = ('__err__', fatal, 'connect')
                elif entry is None or entry.get('error'):
                    res[s] = ('__err__',
                              (entry or {}).get('error') or '采集失败',
                              (entry or {}).get('stage') or 'connect')
                else:
                    res[s] = entry.get('rows') or []
            return res
        finally:
            for p in (_in_path, _out_path):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # ═══════════════════════════════════════════════════════════
    #  通用连接+查询
    # ═══════════════════════════════════════════════════════════

    def run_query(self, instance_id, sql, timeout=None):
        """公共查询入口：供大屏指标适配层（screen_metrics）等外部复用连接逻辑。"""
        return self._connect_and_query(instance_id, sql, timeout=timeout)

    @staticmethod
    def _execute_rows(conn, sql):
        """在已有 DBAPI 连接上执行一条查询，返回 list[dict]（列名统一小写）。

        抽取成独立方法，让「借用统一采集器连接」与「自行建连」两条路径
        共用完全相同的行序列化口径，避免下游消费者出现大小写差异。
        """
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            columns = [col[0] for col in (cursor.description or [])]
            rows = []
            for row in cursor.fetchall():
                row_dict = {}
                for i, col in enumerate(columns):
                    val = row[i]
                    # 序列化非标准类型
                    if val is not None and not isinstance(val, (int, float, str, bool)):
                        try:
                            val = str(val)
                        except Exception:
                            val = None
                    # 列名统一小写：dmPython/oracledb 等驱动返回大写标识符，
                    # 下游消费者（screen_metrics/前端模板）一律按小写键取值
                    row_dict[col.lower() if isinstance(col, str) else col] = val
                rows.append(row_dict)
            return rows
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def _connect_and_query(self, instance_id, sql, timeout=None):
        """执行一条 SQL，返回 list of dicts。

        统一采集器在一个实例的一轮采集里只建一条连接，并把它绑定到
        ``connection_scope``；本方法命中绑定时直接借用（不新建、不关闭），
        让慢查询 / 连接数 / 大屏增量各组 SQL 共享同一条连接。
        未命中时（独立调用、脚本、单元测试）按原逻辑自行建连关闭。
        """
        if timeout is None:
            timeout = self.QUERY_TIMEOUT

        from modules.monitor import connection_scope
        scoped = connection_scope.current(instance_id)
        if scoped is not None and getattr(scoped, 'conn', None) is not None:
            try:
                return self._execute_rows(scoped.conn, sql)
            except Exception as e:
                raise MonitorQueryError('query', str(e)) from e

        im = get_instance_manager()
        inst = im.get_instance_decrypted(instance_id)
        if not inst:
            raise ValueError(f"实例 {instance_id} 不存在")

        # 归一到模板键（oracle_jdbc→oracle、tdsqlc_mysql→mysql 等）再分派：
        # 原生驱动连接按协议族在 connection_factory 中构造（TDSQL-C 兼容 MySQL
        # 协议、oracle_jdbc 实例字段与 oracle 相同），与 jdbc 插件 id 无关。
        raw_type = (inst.get('db_type') or '').strip().lower()
        db_type = mq.normalize_db_type(raw_type)

        # *_jdbc 专属数据源：必须走 JDBC 子进程通道，绝不能被归一成
        # oracle/pg 后用 python 原生驱动直连（否则实时监控有数据、大屏判宕机）
        if raw_type in JDBC_ONLY_TYPES:
            conn_type, tmpl_key = JDBC_ONLY_TYPES[raw_type]
            return self._jdbc_query(inst, conn_type, sql, timeout=timeout,
                                    template_key=tmpl_key)

        # gbase/db2/clickhouse：JDBC 批量子进程通道（主进程绝不起 JVM）
        if db_type in JDBC_BATCH_DB_TYPES:
            return self._jdbc_query(inst, db_type, sql, timeout=timeout)

        # 独立调用路径（未经统一采集器）：自行建连、执行、关闭。
        # 建连与查询分阶段标记：权限不足 / 视图不存在（ORA-00942 等）
        # 是「已连上但 SQL 失败」，实例可达，大屏不能据此判宕机。
        # SSH 隧道、DSN/ODBC 串、库名回落、Oracle thick 回退统一走连接工厂。
        managed = None
        try:
            try:
                managed = open_db_connection(
                    inst, connect_timeout=timeout, query_timeout=timeout)
            except Exception as e:
                raise MonitorQueryError('connect', str(e)) from e
            if managed is None:
                raise MonitorQueryError(
                    'connect', f'不支持的数据库类型: {raw_type}')
            try:
                return self._execute_rows(managed.conn, sql)
            except Exception as e:
                raise MonitorQueryError('query', str(e)) from e
        finally:
            # 同时关闭 DBAPI 连接与 SSH 隧道（避免泄漏 paramiko 转发线程）
            if managed is not None:
                managed.close()

# ═══════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════

_monitor_engine = None
_monitor_lock = threading.Lock()


def get_monitor_engine():
    """获取 MonitorEngine 全局单例"""
    global _monitor_engine
    if _monitor_engine is None:
        with _monitor_lock:
            if _monitor_engine is None:
                _monitor_engine = MonitorEngine()
    return _monitor_engine
