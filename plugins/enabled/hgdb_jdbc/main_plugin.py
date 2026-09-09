#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
HGDB JDBC 巡检插件 —— 通过 JPype + postgresql.jar + 共享 jdbc_jvm 连接瀚高数据库 HGDB V9。

设计依据：HGDB V9 为 PostgreSQL 14 内核（完全兼容标准 PG 系统视图），仅有 JDBC 驱动，
驱动类 org.postgresql.Driver。巡检 SQL 大量复用 PostgreSQL 系统目录
（pg_catalog.* / pg_stat_* / pg_settings 等），HGDB 兼容 PG 故可直接使用。

- 连接复用 plugins/available/hgdb_jdbc/jdbc_jvm.py（JVM 单例 + classpath 合并）
- 连接配置复用 connection_config.HgdbConnectionConfig（jdbc_url 透传）
- 数据采集直接跑 PG 兼容系统目录，结果以 hgdb_* list[dict] 存入 context，
  供报告章节（inspection.db 模板）使用。
- 智能分析接入：get_task_config() 返回 'smart_analyze': 'smart_analyze_pg'
  （HGDB 兼容 PG，优先复用 PG 兼容分析器，与 ivorysql/kingbase 同策略）。
"""

import os
import sys
import json
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 插件自身目录注入 sys.path（loader 动态加载时不会自动加），以便裸导入同级模块
_PLUGIN_DIR = str(Path(__file__).parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import importlib.util


def _load_own_connection_config():
    """按文件绝对路径 + 唯一模块名加载本插件自有的 connection_config 模块。

    每个插件的 connection_config.py 文件同名，若用裸 import 会被缓存进全局
    sys.modules['connection_config']，导致相互污染（db2_jdbc 已踩坑）。
    改用 importlib 按绝对路径 + 唯一模块名加载，各插件各取所需，与导入顺序无关。
    """
    spec = importlib.util.spec_from_file_location(
        "hgdb_jdbc_connection_config",
        os.path.join(_PLUGIN_DIR, "connection_config.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 模块级绑定：connect() 内不再裸导入，避免被其它插件的同名模块污染。
_DbCC = _load_own_connection_config()
HgdbConnectionConfig = _DbCC.HgdbConnectionConfig


def _normalize_hgdb_database(database):
    """HGDB 默认库名为 highgo。

    兼容 web_ui 对未登记 db_type 误用 'postgres' 兜底的情况：
    空值或 'postgres' 一律回退到 'highgo'。
    """
    if not database or database == 'postgres':
        return 'highgo'
    return database


def _load_own_jdbc_jvm():
    """按文件绝对路径 + 唯一模块名加载本插件自有的 jdbc_jvm 模块。

    各插件的 jdbc_jvm.py 同名，裸 import 会被 sys.path 顺序影响（先加载的插件
    目录抢注 jdbc_jvm），导致 hgdb 误导入 db2 的 jdbc_jvm（缺 register_hgdb_driver）。
    改用 importlib 按绝对路径 + 唯一模块名加载，与导入顺序无关。
    """
    spec = importlib.util.spec_from_file_location(
        "hgdb_jdbc_jdbc_jvm",
        os.path.join(_PLUGIN_DIR, "jdbc_jvm.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hgdb_jdbc_jdbc_jvm"] = mod
    spec.loader.exec_module(mod)
    return mod


# 项目根目录：main_plugin.py -> hgdb_jdbc -> available -> plugins -> root
_PROJECT_ROOT = os.path.abspath(os.path.join(_PLUGIN_DIR, "..", "..", ".."))

# BaseInspectionEngine 必须在模块级导入（类继承需要）
from modules.inspection.engine import (
    BaseInspectionEngine,
    LocalSystemInfoCollector,
    RemoteSystemInfoCollector,
    get_host_disk_usage,
)
from modules.inspection.chapter_filter import build_chapter_filter_sql  # 章节可选：共享过滤 helper

# jpype.imports 必须在模块顶层导入（注册 java 钩子），否则 from java.sql import DriverManager
# / from java.util import Properties 等语句无法解析。不会启动 JVM，仅注册 import 钩子。
import jpype.imports  # noqa: E402


# ── JDBC 连接包装器（兼容 Python DB-API 2.0）────────────────────────
class JdbcCursorWrapper:
    """包装 JDBC Statement/ResultSet，提供类似 Python DB-API 的 cursor 接口"""

    def __init__(self, connection):
        self.conn = connection
        self.stmt = connection.createStatement()
        self.rs = None
        self.description = None
        self._rowcount = -1

    def execute(self, sql):
        """执行 SQL（自动判断查询/更新）。

        HGDB 通过 SHOW 获取运行时参数（pg_settings 未以同名视图暴露），
        故此处把 SHOW 与 SELECT/WITH/VALUES 一并按查询（executeQuery）处理。
        """
        sql_upper = sql.strip().upper()
        if (sql_upper.startswith('SELECT') or sql_upper.startswith('WITH')
                or sql_upper.startswith('VALUES') or sql_upper.startswith('SHOW')
                or 'FROM ' in sql_upper):
            self.rs = self.stmt.executeQuery(sql)
            meta = self.rs.getMetaData()
            col_count = meta.getColumnCount()
            self.description = tuple(
                (meta.getColumnName(i + 1), meta.getColumnTypeName(i + 1), None, None, None, None, None)
                for i in range(col_count)
            )
        else:
            self._rowcount = self.stmt.executeUpdate(sql)

    def fetchall(self):
        """获取所有行"""
        if not self.rs:
            return []
        rows = []
        meta = self.rs.getMetaData()
        col_count = meta.getColumnCount()
        while self.rs.next():
            rows.append(tuple(self._convert_java_obj(self.rs.getObject(i + 1)) for i in range(col_count)))
        return rows

    def fetchone(self):
        """获取一行"""
        if not self.rs:
            return None
        if self.rs.next():
            meta = self.rs.getMetaData()
            col_count = meta.getColumnCount()
            return tuple(self._convert_java_obj(self.rs.getObject(i + 1)) for i in range(col_count))
        return None

    def fetchmany(self, n):
        """分页获取：首次 execute 时把结果全集缓存到 self._rows 并按游标切片。"""
        if not hasattr(self, '_rows') or self._rows is None:
            self._rows = self.fetchall() if self.rs else []
            self._idx = 0
        if self._idx >= len(self._rows):
            return []
        chunk = self._rows[self._idx:self._idx + n]
        self._idx += len(chunk)
        return chunk

    @property
    def rowcount(self):
        return getattr(self, '_rowcount', -1)

    def _convert_java_obj(self, obj):
        """将 Java 对象转换为 Python 对象"""
        if obj is None:
            return None
        try:
            if hasattr(obj, 'intValue'):
                return obj.intValue()
            if hasattr(obj, 'longValue'):
                return obj.longValue()
            if hasattr(obj, 'doubleValue'):
                return obj.doubleValue()
            if hasattr(obj, 'booleanValue'):
                return bool(obj.booleanValue())
            return str(obj)
        except Exception:
            return str(obj)

    def close(self):
        """关闭游标"""
        if self.rs:
            self.rs.close()
        if self.stmt:
            self.stmt.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class JdbcConnectionWrapper:
    """包装 JDBC Connection，提供类似 Python DB-API 的 connection 接口"""

    def __init__(self, jdbc_conn):
        self.jdbc_conn = jdbc_conn

    def cursor(self):
        """返回包装后的 cursor 对象"""
        return JdbcCursorWrapper(self.jdbc_conn)

    def close(self):
        """关闭连接"""
        self.jdbc_conn.close()

    def commit(self):
        """提交事务"""
        self.jdbc_conn.commit()

    def rollback(self):
        """回滚事务"""
        self.jdbc_conn.rollback()


# ── HGDB JDBC 巡检器 ───────────────────────────────────────────────────────
class HgdbJdbcInspector(BaseInspectionEngine):
    """HGDB JDBC 巡检器。

    继承 BaseInspectionEngine，覆盖 connect() / collect_data()，直接跑 PG 兼容
    系统目录填充 hgdb_* context。
    """

    # 8 章巡检查询（key 与 template_data.json 的 query_key 一一对应，
    # 保证 _load_chapters_from_db 命中已采集键后跳过重复执行）。
    # HGDB V9 基于 PostgreSQL 14 内核，完全兼容标准 PG 系统视图
    # （pg_stat_activity / pg_settings / pg_database / pg_roles / pg_locks 等），
    # 运行时参数通过 SHOW 获取。以下 SQL 直接使用标准 PG 视图。
    QUERIES: List[Tuple[str, str]] = [
        # 1. 实例与版本
        ('hgdb_version', 'SELECT version()'),
        ('hgdb_uptime', "SELECT pg_postmaster_start_time() AS start_time, now()-pg_postmaster_start_time() AS uptime"),
        # 2. 连接与会话
        ('hgdb_conn_summary', 'SELECT state, count(*) AS conn_count FROM pg_stat_activity GROUP BY state ORDER BY conn_count DESC'),
        ('hgdb_connections', 'SELECT pid, usename, application_name, client_addr, state, query FROM pg_stat_activity'),
        # 3. 配置参数
        ('hgdb_settings', 'SELECT name, setting, unit FROM pg_settings ORDER BY name'),
        ('hgdb_shared_buffers', 'SHOW shared_buffers'),
        # 4. 资源与性能
        ('hgdb_database_size', 'SELECT datname, pg_size_pretty(pg_database_size(datname)) AS size_pretty, pg_database_size(datname) AS size_bytes FROM pg_database ORDER BY size_bytes DESC'),
        ('hgdb_bgwriter', 'SELECT * FROM pg_stat_bgwriter'),
        ('hgdb_stat_database', 'SELECT datname, numbackends, xact_commit, xact_rollback, blks_read, blks_hit, tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted, conflicts, temp_files, temp_bytes, deadlocks FROM pg_stat_database'),
        # 5. 空间与对象
        ('hgdb_tables_size', "SELECT schemaname, relname, pg_total_relation_size(relid) AS total_bytes, n_live_tup, n_dead_tup FROM pg_stat_user_tables ORDER BY total_bytes DESC LIMIT 50"),
        ('hgdb_tables', 'SELECT schemaname, relname, n_live_tup, n_dead_tup, last_vacuum, last_autovacuum, last_analyze, last_autoanalyze FROM pg_stat_user_tables'),
        ('hgdb_indexes', 'SELECT schemaname, relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch FROM pg_stat_user_indexes'),
        # 6. 日志与锁
        ('hgdb_locks', 'SELECT locktype, database::regclass AS db, relation::regclass AS rel, pid, mode, granted FROM pg_locks'),
        ('hgdb_lock_waits', "SELECT a.pid, a.usename, a.query, l.locktype, l.mode FROM pg_locks l JOIN pg_stat_activity a ON l.pid = a.pid WHERE NOT l.granted"),
        # 7. 安全与权限
        ('hgdb_roles', 'SELECT rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles ORDER BY rolname'),
        ('hgdb_auth_settings', 'SHOW password_encryption'),
        ('hgdb_hba', 'SELECT type, database, user_name, address, auth_method FROM pg_hba_file_rules'),
        # 8. 备份恢复
        ('hgdb_wal_level', 'SHOW wal_level'),
        ('hgdb_archive_mode', 'SHOW archive_mode'),
        ('hgdb_archiver', 'SELECT archived_count, failed_count, last_archived_wal, last_archived_time, stats_reset FROM pg_stat_archiver'),
        ('hgdb_replication', 'SELECT pid, usename, application_name, client_addr, state, sync_state, sent_lsn, write_lsn, flush_lsn, replay_lsn FROM pg_stat_replication'),
    ]

    def __init__(self, host, port, user, password, database=None,
                 ssh_info=None, template_id=None, jdbc_url=None,
                 driver_version=''):
        super().__init__(host, int(port), user, password, database=database,
                         ssh_info=ssh_info, template_id=template_id)
        self.db_type = 'hgdb'
        self.jdbc_url = jdbc_url
        self.conn = None
        self.cursor = None
        self.raw_jdbc_conn = None
        self.conn_cfg = None
        self._hgdb_version_str = 'unknown'
        self.driver_version = driver_version or ''
        self.jdbc_driver_path = None
        try:
            from modules import driver_registry as _dr
            _jars = _dr.resolve_jdbc_driver_jars('hgdb', self.driver_version)
            if _jars:
                self.jdbc_driver_path = _jars
        except Exception:
            self.jdbc_driver_path = None

    # ════════════════════════════════════════════════
    # 连接层
    # ════════════════════════════════════════════════
    def connect(self) -> Tuple[bool, str]:
        """连接 HGDB 数据库（统一连接层 JPype 模式）。

        Returns:
            (ok, msg)：ok 为 True 时 msg 是版本可读串；
                          ok 为 False 时 msg 是错误信息。
        """
        try:
            # 1. 构建连接配置（HgdbConnectionConfig 已在模块级按路径绑定；
            #    build_jdbc_url 委托统一层，含 PG 系超时参数段）
            cfg = HgdbConnectionConfig(
                host=self.host,
                port=int(self.port),
                user=self.user,
                password=self.password,
                database=self.database or 'highgo',
                jdbc_url=self.jdbc_url or '',
            )
            self.conn_cfg = cfg

            # 2. 统一连接层：驱动解析 / JVM 启动 / URL 构造 / props 装配 / 建连。
            #    HGDB 与 PostgreSQL 线协议兼容，统一走标准 PostgreSQL 驱动
            #    （org.postgresql.Driver + jdbc:postgresql://）；项目未捆绑
            #    HighGo 专属 HgdbJdbc jar，避免缺 jar 报 ClassNotFoundException。
            from modules.jdbc_connector import open_jdbc_connection
            conn, meta = open_jdbc_connection(
                'hgdb', self.host, self.port,
                user=self.user, password=self.password,
                driver_version=self.driver_version,
                database=self.database or 'highgo',
                jdbc_url=self.jdbc_url or '',
                mode='jpype',
                connect_timeout_s=cfg.connect_timeout_s,
                socket_timeout_s=cfg.socket_timeout_s,
            )
            if conn is None:
                return False, meta['error']

            self.raw_jdbc_conn = conn
            self.conn = JdbcConnectionWrapper(conn)
            self.cursor = self.conn.cursor()

            # 3. 读取版本
            self.cursor.execute("SELECT version()")
            row = self.cursor.fetchone()
            version = str(row[0]) if row else 'unknown'
            self._hgdb_version_str = version
            self.context['hgdb_version'] = [{'VERSION': version}]
            self.context['version'] = [{'VERSION': version}]

            print(f"[HGDB] 连接成功，版本: {version}")
            return True, version
        except Exception as e:
            print(f"[HGDB] 连接失败: {e}")
            traceback.print_exc()
            return False, str(e)

    def disconnect(self):
        """关闭数据库连接"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
        except Exception as e:
            print(f"[HGDB] 关闭连接失败: {e}")

    def get_template_id(self):
        """返回 inspection_template 表的 template_id。"""
        try:
            from modules.inspection.dal import get_templates_by_db_type
            templates = get_templates_by_db_type("hgdb")
            return templates[0]['id'] if templates else None
        except Exception as e:
            print(f"[HGDB] 获取模板 ID 失败: {e}")
            return None

    # ════════════════════════════════════════════════
    # 采集辅助
    # ════════════════════════════════════════════════
    def _exec_to_dicts(self, sql: str) -> List[Dict[str, Any]]:
        """执行 SQL 并返回 list[dict]（列名取自 cursor.description）。"""
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            return [dict(zip(cols, row)) for row in rows]
        finally:
            cur.close()

    def _collect_query(self, key: str, sql: str) -> None:
        """执行一条查询并写入 context[key]；失败写 context[key]=[{'ERROR':...}]。

        保证「巡检无错」：任何单条查询异常都被吞掉，不影响整体采集。
        """
        try:
            self.context[key] = self._exec_to_dicts(sql)
        except Exception as e:
            self.context[key] = [{'ERROR': str(e)[:200]}]

    # ════════════════════════════════════════════════
    # 报告章节（从 inspection.db 加载并执行模板 query）
    # ════════════════════════════════════════════════
    def _load_chapters_from_db(self):
        """从 inspection.db 加载本插件模板章节，并把每个 query_sql 执行结果
        存入 context[query_key]（与已采集键同键时跳过，避免重复执行）。
        """
        db_path = os.path.join(_PROJECT_ROOT, 'data', 'inspection.db')
        if not os.path.exists(db_path):
            self.context['_chapters'] = []
            return
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        _chap_frag, _chap_params = build_chapter_filter_sql(self.chapter_ids, alias='ch')
        cur.execute(
            "SELECT ch.id, ch.chapter_number, ch.chapter_title_zh, ch.chapter_title_en, ch.description "
            "FROM inspection_chapter ch JOIN inspection_template t ON ch.template_id=t.id "
            "WHERE t.db_type=?" + _chap_frag + " ORDER BY ch.chapter_number",
            (self.db_type,) + tuple(_chap_params))
        chapters = []
        for tid, num, zh, en, desc in cur.fetchall():
            cur2 = conn.cursor()
            cur2.execute(
                "SELECT query_key, query_sql, query_description_zh, query_description_en "
                "FROM inspection_query WHERE chapter_id=? ORDER BY sort_order, id",
                (tid,))
            queries = [{
                'query_key': r[0],
                'query_sql': r[1],
                'query_description_zh': r[2] or '',
                'query_description_en': r[3] or '',
            } for r in cur2.fetchall()]
            cur2.close()
            chapters.append({
                'chapter_number': num,
                'chapter_title_zh': zh,
                'chapter_title_en': en,
                'description': desc or '',
                'queries': queries,
            })
        cur.close()
        conn.close()
        self.context['_chapters'] = chapters

        # 执行每个模板 query（已由 QUERIES 填充的键跳过）
        for ch in chapters:
            for q in ch['queries']:
                key = q['query_key']
                if key in self.context and self.context.get(key):
                    continue
                try:
                    self.context[key] = self._exec_to_dicts(q['query_sql'])
                except Exception as e:
                    self.context[key] = [{'ERROR': str(e)[:200]}]

    # ════════════════════════════════════════════════
    # 主采集入口
    # ════════════════════════════════════════════════
    def collect_data(self, sql_templates: str = ''):
        """采集 HGDB 数据（覆盖父类）。

        流程：connect → 逐条执行 PG 兼容查询填充 hgdb_* context（8 章）→
        加载章节并执行模板 query（报告源）→ 基线检查 → 智能分析（PG 规则）。
        任何子步骤异常均被吞掉，整体保证「巡检无错」。

        Returns:
            成功返回 self.context(dict)，失败返回 (False, error_msg)。
        """
        print("\n[HGDB] 开始采集数据...")
        ok, version = self.connect()
        if not ok:
            return False, version

        self.context['version'] = [{'VERSION': version}]
        self.context['db_type'] = 'hgdb'

        # 8 章 PG 兼容查询（每条 try/except 包裹）
        total = len(self.QUERIES)
        for i, (key, sql) in enumerate(self.QUERIES, start=1):
            try:
                self.print_progress_bar(i, total, prefix='[HGDB]', suffix=f'{key} ({i}/{total})')
                self._collect_query(key, sql)
            except Exception as e:
                self.context[key] = [{'ERROR': str(e)[:200]}]

        # 系统资源采集（统一补充 system_info，供报告"系统资源"章节使用）
        try:
            if getattr(self, 'ssh_info', None) and self.ssh_info.get('ssh_host'):
                collector = RemoteSystemInfoCollector(
                    host=self.ssh_info['ssh_host'], port=self.ssh_info.get('ssh_port', 22),
                    username=self.ssh_info.get('ssh_user', 'root'),
                    password=self.ssh_info.get('ssh_password'), key_file=self.ssh_info.get('ssh_key_file')
                )
                if not collector.connect():
                    collector = LocalSystemInfoCollector()
            else:
                collector = LocalSystemInfoCollector()
            system_info = collector.get_system_info()
            disk_list = system_info.get('disk_list') or system_info.get('disk') or get_host_disk_usage()
            if isinstance(disk_list, dict):
                disk_list = list(disk_list.values())
            system_info['disk_list'] = disk_list
            self.context.update({"system_info": system_info})
        except Exception as e:
            print(f"[HGDB] 系统信息采集失败: {e}")
            self.context.update({"system_info": {
                'platform': '未知', 'boot_time': '未知',
                'cpu': {}, 'memory': {},
                'disk_list': [{'device': 'C:', 'mountpoint': 'C:\\', 'fstype': 'NTFS',
                               'total_gb': 0, 'used_gb': 0, 'free_gb': 0, 'usage_percent': 0}]
            }})

        # 报告章节（从 inspection.db 加载并执行模板 query）
        try:
            self._load_chapters_from_db()
        except Exception as e:
            print(f"[HGDB] 加载章节失败: {e}")
            self.context['_chapters'] = []

        # 慢查询深度分析（hgdb 无独立分析器时降级为 None）
        try:
            from modules.inspection.slow_query import get_slow_query_analyzer
            self.context['slow_query_result'] = get_slow_query_analyzer('hgdb').analyze(self.conn).to_dict()
        except Exception as e:
            print(f"[HGDB] 慢查询分析跳过: {e}")
            self.context['slow_query_result'] = None

        # 索引健康分析（hgdb 无独立分析器时降级为 None）
        try:
            from modules.inspection.index_health import get_index_health
            self.context['index_health_result'] = get_index_health('hgdb', self.conn)
        except Exception as e:
            print(f"[HGDB] 索引健康分析跳过: {e}")
            self.context['index_health_result'] = None

        # 基线检查（HGDB 基线已注册到 inspection.db）
        try:
            self._check_baselines()
        except Exception as e:
            print(f"[HGDB] 基线检查失败: {e}")
            self.context['baseline_results'] = []

        # 智能分析（复用 PG 兼容分析器，异常降级空列表）
        try:
            from modules.inspection.analyzer import smart_analyze_pg
            self.context['auto_analyze'] = smart_analyze_pg(self.context) or []
        except Exception as e:
            print(f"[HGDB] 智能分析失败: {e}")
            self.context['auto_analyze'] = []
        # 规则引擎（YAML 规则，pro/rules/builtin/hgdb.yaml）—— HGDB 专属检查
        try:
            from modules.pro.rule_engine import analyze_with_plugins
            rule_issues = analyze_with_plugins('hgdb', self.context)
            if rule_issues:
                if not isinstance(self.context.get('auto_analyze'), list):
                    self.context['auto_analyze'] = []
                self.context['auto_analyze'].extend(rule_issues)
        except Exception as e:
            print(f"[HGDB] 规则引擎检查跳过: {e}")

        # AI 诊断（统一接口）—— 此前缺失导致第 7 章恒为空
        from modules.inspection.analyzer import run_ai_diagnosis
        self.context['ai_advice'] = run_ai_diagnosis(
            'hgdb',
            self.context.get('co_name', [{}])[0].get('DB_NAME') or getattr(self, 'host', '') or self.db_type,
            self.context,
            lang=getattr(self, '_lang', 'zh'),
            timeout=600,
        )

        print(f"[HGDB] 数据采集完成，context keys: {list(self.context.keys())}")
        return self.context


# ── 测试连接函数（供 web_ui / 自测调用）────────────────────────────
def test_connection(host, port, user, password, database='', jdbc_url=None,
                    driver_version='', **kwargs):
    """测试 HGDB JDBC 连接。

    Args:
        host: HGDB 服务器地址
        port: 端口
        user: 用户名
        password: 密码
        database: 目标数据库名
        jdbc_url: 完整 JDBC URL（可选，以 jdbc:postgresql 开头则透传）
    Returns:
        (ok, msg)
    """
    database = _normalize_hgdb_database(database)
    try:
        inspector = HgdbJdbcInspector(
            host, int(port), user, password,
            database=database, jdbc_url=jdbc_url,
            driver_version=driver_version)
        ok, msg = inspector.connect()
        inspector.disconnect()
        return ok, msg
    except Exception as e:
        return False, str(e)


# ── 实时监控连接工厂（供 pro/metrics_collector.py 使用）─────────────
def get_connection(host, port, user, password, database='', jdbc_url=None,
                   driver_version=''):
    """返回 DB-API 2.0 兼容的 JDBC 连接包装（JdbcConnectionWrapper）。

    Raises:
        RuntimeError: 连接失败时抛出。
    """
    database = _normalize_hgdb_database(database)
    inspector = HgdbJdbcInspector(
        host, int(port), user, password,
        database=database, jdbc_url=jdbc_url,
        driver_version=driver_version)
    ok, msg = inspector.connect()
    if not ok:
        raise RuntimeError('HGDB JDBC 连接失败: %s' % msg)
    return inspector.conn


# ── 数据源获取函数（供 web_ui.py 使用）─────────────────────────────
def getData(ip, port, user, password, ssh_info=None, template_id=None,
            driver_version=''):
    """获取 HGDB 数据源。

    返回 CompatWrapper 对象，web_ui 通过 wrapper.checkdb('builtin')
    触发采集并获取 context。

    Returns:
        CompatWrapper 对象；失败返回 None。
    """
    ssh_info = ssh_info or {}
    database = _normalize_hgdb_database(ssh_info.get('database', ''))
    jdbc_url = ssh_info.get('jdbc_url')

    inspector = HgdbJdbcInspector(
        ip, int(port), user, password,
        database=database, jdbc_url=jdbc_url,
        driver_version=driver_version,
        ssh_info=ssh_info, template_id=template_id)
    ok, msg = inspector.connect()
    if not ok:
        print(f"[HGDB] 连接失败: {msg}")
        return None

    class CompatWrapper:
        """兼容 web_ui.py 调用约定的包装对象。"""

        def __init__(self, inspector):
            self.inspector = inspector
            self.conn = inspector.conn

        def checkdb(self, sqlfile=''):
            result = self.inspector.collect_data()
            if isinstance(result, dict):
                return result
            return None

        def generate_report(self, output_file, inspector_name="Jack"):
            return self.inspector.generate_report(output_file, inspector_name)

    return CompatWrapper(inspector)


# ── 任务配置函数（供 plugin_loader / web_ui 调用）────────────────────
def _plugin_test_connection(info: dict):
    """插件连接测试入口（供 web_ui 经 get_task_config 调用）。

    Args:
        info: 包含 ip/host/port/user/password/database/jdbc_url 的字典
    Returns:
        (ok, msg)
    """
    info = info or {}
    return test_connection(
        info.get('ip', info.get('host', '')),
        int(info.get('port', 5866) or 5866),
        info.get('user', ''),
        info.get('password', ''),
        database=info.get('database', ''),
        jdbc_url=info.get('jdbc_url'),
    )


def parse_connection_result(ok: bool, msg: Any) -> Dict[str, Any]:
    """解析 HGDB JDBC 连接测试结果（供 web_ui.py 动态调用）。

    目前仅返回空字典；如需后续提取版本号，可在此解析 msg。
    """
    return {}


def get_task_config():
    """返回插件任务配置（供 plugin_loader.get_plugin_task_config 调用）。"""
    return {
        'module_name': 'main_plugin',
        'plugin_path': str(Path(__file__).parent),
        'main_file': 'main_plugin.py',
        'connect_test': _plugin_test_connection,
        'connect_test_args': lambda info: [info],
        'getdata_args': lambda info: (
            [info.get('ip', ''), int(info.get('port', 5866) or 5866),
             info.get('user', ''), info.get('password', '')],
            {'ssh_info': {
                 'database': info.get('database', ''),
                 'jdbc_url': info.get('jdbc_url', ''),
                 'ssl': bool(info.get('ssl', False)),
             }, 'template_id': info.get('template_id'),
             'driver_version': info.get('driver_version', '')}
        ),
        'conn_attr': '',  # getData 返回 CompatWrapper，跳过 conn_attr 检查
        'filename_key': 'webui.hgdb_report_filename',
        'history_db_type': 'hgdb',
        'instance_prefix': 'hgdb',
        'error_task_name': 'HGDB',
        'log_start_key': 'webui.log_hgdb_start',
        'err_module_key': 'webui.err_hgdb_module',
        'label_default': 'HGDB',
        'db_name_default': 'highgo',
        'smart_analyze': 'smart_analyze_pg',  # ← 智能分析接入铁律（HGDB 兼容 PG）
    }


# ── 注册插件（无侵入式架构）──────────────────────────────────────────
try:
    from modules.pluginkit.core import InspectionPlugin, register

    class HgdbJdbcPluginAdapter(InspectionPlugin):
        """HGDB JDBC 插件适配器（实现标准接口）。"""

        def __init__(self, parse_func=None):
            self.id = 'hgdb_jdbc'  # 与目录名 hgdb_jdbc / plugin.json 逻辑 id 对齐，避免已安装列表重复与卸载失效
            self.name = 'HGDB (瀚高/JDBC)'  # 与 plugin.json 的 name 字段保持一致，避免注册名与清单不一致导致的幽灵记录
            self.version = '1.0.0'
            self.db_types = ['hgdb']
            self.author = 'DBCheck Team'
            self.description = '瀚高数据库 HGDB V9（PostgreSQL 兼容系）JDBC 巡检插件'
            self._parse_func = parse_func
            super().__init__()

        def parse_connection_result(self, ok: bool, msg: Any) -> Dict[str, Any]:
            if self._parse_func:
                return self._parse_func(ok, msg)
            return {}

        def get_queries(self) -> List[Any]:
            return []

        def analyze(self, context: Dict[str, Any]) -> List[Any]:
            return []

        def on_install(self, db_path: str = None):
            """插件安装：幂等注册模板/章节/查询/基线到 inspection.db。"""
            print("[HGDB] 开始初始化数据（模板 + 基线）...")
            try:
                import sqlite3  # noqa: F401
                from modules.inspection.dal import (
                    get_templates_by_db_type,
                    create_template,
                    create_chapter,
                    create_query,
                    create_baseline,
                    get_db_connection,
                    get_baselines_by_db_type,
                    delete_baseline,
                )

                template_path = os.path.join(os.path.dirname(__file__), 'template_data.json')
                if not os.path.isfile(template_path):
                    print("[HGDB] 错误：未找到 template_data.json")
                    return

                with open(template_path, 'r', encoding='utf-8') as f:
                    template_data = json.load(f)

                # 1. 创建模板（幂等）
                existing_templates = get_templates_by_db_type('hgdb', db_path=db_path)
                if existing_templates:
                    template_id = existing_templates[0]['id']
                    print(f"[HGDB] 模板已存在，使用现有模板: {template_id}")
                else:
                    template_info = template_data['template']
                    template_id = create_template(
                        db_type=template_info['db_type'],
                        template_name=template_info.get('template_name_zh', ''),
                        template_name_en=template_info.get('template_name_en', ''),
                        description=template_info.get('description', ''),
                        is_default=template_info.get('is_default', 1),
                        is_preset=template_info.get('is_preset', 1),
                        db_path=db_path,
                    )
                    print(f"[HGDB] 创建模板: {template_id}")

                # 2. 创建章节和查询（幂等）
                chapters_data = template_data.get('chapters', [])
                print(f"[HGDB] 共有 {len(chapters_data)} 个章节")
                conn = get_db_connection(db_path) if db_path else get_db_connection()
                for chapter_data in chapters_data:
                    chapter_number = chapter_data['chapter_number']
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id FROM inspection_chapter WHERE template_id = ? AND chapter_number = ?",
                        (template_id, chapter_number))
                    existing_chapter = cur.fetchone()
                    if existing_chapter:
                        chapter_id = existing_chapter[0]
                    else:
                        chapter_id = create_chapter(
                            template_id=template_id,
                            chapter_number=chapter_number,
                            chapter_title_zh=chapter_data.get('chapter_title_zh', ''),
                            chapter_title_en=chapter_data.get('chapter_title_en', ''),
                            description=chapter_data.get('description', ''),
                            db_path=db_path,
                        )
                    cur.close()
                    for query_data in chapter_data.get('queries', []):
                        try:
                            create_query(
                                chapter_id=chapter_id,
                                query_key=query_data['query_key'],
                                query_sql=query_data['query_sql'],
                                query_description_zh=query_data.get('query_description_zh', ''),
                                query_description_en=query_data.get('query_description_en', ''),
                                db_path=db_path,
                            )
                        except Exception as e:
                            if 'UNIQUE constraint' in str(e):
                                pass
                            else:
                                print(f"[HGDB]   创建查询失败: {query_data['query_key']} - {e}")
                conn.close()

                # 3. 创建基线（从 baseline_data.json，幂等）
                baseline_path = os.path.join(os.path.dirname(__file__), 'baseline_data.json')
                existing_bl = get_baselines_by_db_type('hgdb', db_path=db_path)
                if not existing_bl and os.path.isfile(baseline_path):
                    with open(baseline_path, 'r', encoding='utf-8') as f:
                        baseline_data = json.load(f)
                    print(f"[HGDB] 共有 {len(baseline_data)} 条基线")
                    for bl in baseline_data:
                        try:
                            create_baseline(
                                db_type=bl.get('db_type', 'hgdb'),
                                param_name=bl['param_name'],
                                query_sql=bl.get('query_sql'),
                                operator=bl.get('operator', '='),
                                expected_value=bl.get('expected_value'),
                                expected_value_min=bl.get('expected_value_min'),
                                expected_value_max=bl.get('expected_value_max'),
                                risk_level=bl.get('risk_level', 'LOW'),
                                description_zh=bl.get('description_zh'),
                                description_en=bl.get('description_en'),
                                db_path=db_path,
                            )
                        except Exception as e:
                            if 'UNIQUE constraint' in str(e):
                                pass
                            else:
                                print(f"[HGDB]   创建基线失败: {bl['param_name']} - {e}")
                print("[HGDB] 数据初始化完成")
            except Exception as e:
                print(f"[HGDB] 数据初始化失败: {e}")
                traceback.print_exc()

        def on_uninstall(self, db_path: str = None):
            """插件卸载：清理 hgdb 的模板与基线数据。"""
            print("[HGDB] 开始清理数据...")
            try:
                from modules.inspection.dal import (
                    get_templates_by_db_type,
                    get_baselines_by_db_type,
                    delete_template,
                    delete_baseline,
                )
                templates = get_templates_by_db_type('hgdb')
                for t in templates:
                    try:
                        delete_template(t['id'], db_path=db_path)
                        print(f"[HGDB] 删除模板: {t.get('template_name_zh', t['id'])} (ID: {t['id']})")
                    except Exception as e:
                        print(f"[HGDB] 删除模板 {t['id']} 失败: {e}")

                baselines = get_baselines_by_db_type('hgdb')
                for b in baselines:
                    try:
                        delete_baseline(b['id'], db_path=db_path)
                    except Exception as e:
                        print(f"[HGDB] 删除基线 {b['id']} 失败: {e}")
                print("[HGDB] 数据清理完成")
            except Exception as e:
                print(f"[HGDB] 数据清理失败: {e}")

    adapter = HgdbJdbcPluginAdapter(parse_func=parse_connection_result)
    register(adapter)
    print("[HGDB] 插件注册成功")
except Exception as e:
    print(f"[HGDB] 插件注册失败: {e}")


if __name__ == '__main__':
    if len(sys.argv) > 2:
        ip = sys.argv[1]
        port = int(sys.argv[2])
        user = sys.argv[3] if len(sys.argv) > 3 else 'highgo'
        password = sys.argv[4] if len(sys.argv) > 4 else 'password'
        database = sys.argv[5] if len(sys.argv) > 5 else 'highgo'
        ok, ver = test_connection(ip, port, user, password, database)
        print(("连接成功: %s" % ver) if ok else ("连接失败: %s" % ver))
