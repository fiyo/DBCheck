#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Db2 JDBC 巡检插件 —— 通过 JPype + db2jcc4.jar + 共享 jdbc_jvm 连接 Db2 LUW。

设计依据：docs/db2-inspection-design.md（SOP 收口版）。
- 连接复用 plugins/available/db2_jdbc/jdbc_jvm.py（JVM 单例 + classpath 合并）
- 连接配置复用 connection_config.Db2ConnectionConfig（jdbc_url 透传 + SSL）
- 数据采集直接跑 Db2 系统目录（SYSIBMADM.* / SYSCAT.* / MON_GET_* /
  sysibm.sys*），结果以 db2_* list[dict] 存入 context，供 db2.yaml 规则
  与 inspection.db 模板章节使用。
- 智能分析接入铁律：get_task_config() 必须返回 'smart_analyze': 'smart_analyze_db2'。
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

# 项目根目录：main_plugin.py -> db2_jdbc -> available -> plugins -> root
_PROJECT_ROOT = os.path.abspath(os.path.join(_PLUGIN_DIR, "..", "..", ".."))

# BaseInspectionEngine 必须在模块级导入（类继承需要）
from inspection_engine import BaseInspectionEngine


# ── JDBC 连接包装器（兼容 Python DB-API 2.0）────────────────────────
# 直接照搬 oracle_jdbc 实现的独立副本（避免跨插件导入路径问题）。
class JdbcCursorWrapper:
    """包装 JDBC Statement/ResultSet，提供类似 Python DB-API 的 cursor 接口"""

    def __init__(self, connection):
        self.conn = connection
        self.stmt = connection.createStatement()
        self.rs = None
        self.description = None
        self._rowcount = -1

    def execute(self, sql):
        """执行 SQL（自动判断查询/更新）"""
        sql_upper = sql.strip().upper()
        if sql_upper.startswith('SELECT') or sql_upper.startswith('WITH') \
                or sql_upper.startswith('VALUES') or 'FROM TABLE' in sql_upper \
                or 'FROM SYSIBMADM' in sql_upper or 'FROM SYSCAT' in sql_upper \
                or 'FROM SYSIBM' in sql_upper:
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


# ── 版本号解析 ─────────────────────────────────────────────────────────
def _parse_db2_version(raw: str) -> str:
    """将 Db2 版本号（如 '12010500'）解析为人类可读串（'DB2 v12.1.5'）。

    Db2 版本号格式为 MMMmmFFF：前 3 位主版本、中间 2 位次版本、末 2 位 fixpack。
    """
    s = str(raw).strip()
    digits = ''.join(ch for ch in s if ch.isdigit())
    if len(digits) >= 7:
        try:
            major = int(digits[0:3])
            minor = int(digits[3:5])
            fix = int(digits[5:7])
            return f"DB2 v{major}.{minor}.{fix}"
        except (ValueError, IndexError):
            pass
    return s or "unknown"


# ── Db2 JDBC 巡检器 ───────────────────────────────────────────────────────
class Db2JdbcInspector(BaseInspectionEngine):
    """Db2 JDBC 巡检器。

    继承 BaseInspectionEngine，覆盖 connect() / collect_data()，直接跑 Db2 系统目录
    填充 db2_* context（对应 design §5）。
    """

    def __init__(self, host, port, user, password, database=None,
                 ssh_info=None, template_id=None, jdbc_url=None, ssl=False):
        super().__init__(host, int(port), user, password, database=database,
                         ssh_info=ssh_info, template_id=template_id)
        self.db_type = 'db2'
        self.jdbc_url = jdbc_url
        self.ssl = bool(ssl)
        self.conn = None
        self.cursor = None
        self.raw_jdbc_conn = None
        self.conn_cfg = None
        self._db2_version_str = 'unknown'

    # ════════════════════════════════════════════════
    # 连接层
    # ════════════════════════════════════════════════
    def connect(self) -> Tuple[bool, str]:
        """连接 Db2 数据库（JPype + JDBC）。

        Returns:
            (ok, msg)：ok 为 True 时 msg 是版本可读串；
                          ok 为 False 时 msg 是错误信息。
        """
        try:
            import jpype
            import jpype.imports

            # 1. 确保 JVM 启动且驱动 jar 在 classpath（共享单例）
            from jdbc_jvm import ensure_jvm, register_db2_driver
            ensure_jvm()
            register_db2_driver()

            # 2. 构建连接配置
            from connection_config import Db2ConnectionConfig
            cfg = Db2ConnectionConfig(
                host=self.host,
                port=int(self.port),
                user=self.user,
                password=self.password,
                database=self.database or 'testdb',
                jdbc_url=self.jdbc_url or '',
                ssl=self.ssl,
            )
            self.conn_cfg = cfg

            from java.sql import DriverManager
            from java.util import Properties

            url = cfg.build_jdbc_url()
            if cfg.ssl:
                props = Properties()
                for k, v in cfg.build_properties().items():
                    props.setProperty(str(k), str(v))
                jdbc_conn = DriverManager.getConnection(url, props)
            else:
                jdbc_conn = DriverManager.getConnection(url, self.user, self.password)

            self.raw_jdbc_conn = jdbc_conn
            self.conn = JdbcConnectionWrapper(jdbc_conn)
            self.cursor = self.conn.cursor()

            # 3. 读取版本
            self.cursor.execute("SELECT versionnumber FROM sysibm.sysversions")
            row = self.cursor.fetchone()
            version = str(row[0]) if row else 'unknown'
            self._db2_version_str = _parse_db2_version(version)
            self.context['db2_version'] = [{'VERSIONNUMBER': version}]
            self.context['version'] = [{'VERSION': version, 'VERSION_STR': self._db2_version_str}]

            print(f"[DB2] 连接成功，版本: {self._db2_version_str}")
            return True, self._db2_version_str
        except Exception as e:
            print(f"[DB2] 连接失败: {e}")
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
            print(f"[DB2] 关闭连接失败: {e}")

    def get_template_id(self):
        """返回 inspection_template 表的 template_id。"""
        try:
            from inspection_dal import get_templates_by_db_type
            templates = get_templates_by_db_type("db2")
            return templates[0]['id'] if templates else None
        except Exception as e:
            print(f"[DB2] 获取模板 ID 失败: {e}")
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

    # ════════════════════════════════════════════════
    # 数据采集（design §5 的 db2_* 字段）
    # ════════════════════════════════════════════════
    def _collect_version(self):
        try:
            self.cursor.execute("SELECT versionnumber FROM sysibm.sysversions")
            row = self.cursor.fetchone()
            v = str(row[0]) if row else 'unknown'
            self.context['db2_version'] = [{'VERSIONNUMBER': v}]
            self.context['version'] = [{'VERSION': v, 'VERSION_STR': _parse_db2_version(v)}]
        except Exception as e:
            self.context['db2_version'] = [{'ERROR': str(e)[:200]}]

    def _collect_instance(self):
        # 实例名取自 SYSIBMADM.ENV_INST_INFO.inst_name（DBMCFG 无 instance_name
        # 参数，CURRENT INSTANCE 亦不可在 SELECT 列表直接取别名）；
        # 库名取 CURRENT SERVER（当前连接库）。
        sql = (
            "SELECT (SELECT inst_name FROM SYSIBMADM.ENV_INST_INFO "
            "FETCH FIRST 1 ROWS ONLY) AS instance_name, "
            "CURRENT SERVER AS db_name FROM sysibm.sysdummy1"
        )
        self.context['db2_instance'] = self._exec_to_dicts(sql)

    def _collect_dbmcfg(self):
        self.context['db2_dbmcfg'] = self._exec_to_dicts(
            "SELECT name, value FROM SYSIBMADM.DBMCFG")

    def _collect_dbcfg(self):
        self.context['db2_dbcfg'] = self._exec_to_dicts(
            "SELECT name, value FROM SYSIBMADM.DBCFG")

    def _collect_dbmembers(self):
        self.context['db2_dbmembers'] = self._exec_to_dicts(
            "SELECT member, dbpartitionnum, host, port FROM TABLE(SYSPROC.DB_MEMBERS())")

    def _collect_tablespaces(self):
        self.context['db2_tablespaces'] = self._exec_to_dicts(
            "SELECT tbsp_name, tbsp_type, tbsp_state, tbsp_total_size, tbsp_used_size, "
            "CASE WHEN tbsp_total_size>0 THEN tbsp_used_size*100/tbsp_total_size ELSE 0 END AS used_pct "
            "FROM SYSIBMADM.TBSP_UTILIZATION")

    def _collect_bufferpools(self):
        self.context['db2_bufferpools'] = self._exec_to_dicts(
            "SELECT * FROM SYSIBMADM.BUFFERPOOLS")

    def _collect_applications(self):
        self.context['db2_applications'] = self._exec_to_dicts(
            "SELECT * FROM SYSIBMADM.APPLICATIONS")

    def _collect_lockwaits(self):
        self.context['db2_lockwaits'] = self._exec_to_dicts(
            "SELECT * FROM SYSIBMADM.LOCKWAITS")

    def _collect_locks(self):
        self.context['db2_locks'] = self._exec_to_dicts(
            "SELECT * FROM SYSIBMADM.LOCKS")

    def _collect_tables(self):
        self.context['db2_tables'] = self._exec_to_dicts(
            "SELECT tabschema, tabname, card, npages, fpages, status "
            "FROM SYSCAT.TABLES WHERE tabschema NOT LIKE 'SYS%'")

    def _collect_indexes(self):
        self.context['db2_indexes'] = self._exec_to_dicts(
            "SELECT indschema, indname, tabname, indextype, lastused, colnames "
            "FROM SYSCAT.INDEXES WHERE indschema NOT LIKE 'SYS%'")

    def _collect_index_runstats(self):
        self.context['db2_index_runstats'] = self._exec_to_dicts(
            "SELECT indname, tabname, stats_time, nleaf, nused FROM SYSSTAT.INDEXES")

    def _collect_pkg_cache_stmt(self):
        self.context['db2_pkg_cache_stmt'] = self._exec_to_dicts(
            "SELECT STMT_TEXT, NUM_EXECUTIONS, TOTAL_EXEC_TIME, TOTAL_CPU_TIME, "
            "ROWS_READ, ROWS_RETURNED FROM TABLE(MON_GET_PKG_CACHE_STMT(NULL,'N',NULL,-2)) "
            "ORDER BY TOTAL_EXEC_TIME DESC FETCH FIRST 50 ROWS ONLY")

    def _collect_mon_activity(self):
        self.context['db2_mon_activity'] = self._exec_to_dicts(
            "SELECT * FROM TABLE(MON_GET_ACTIVITY(NULL,-2))")

    def _collect_dbm_memory(self):
        self.context['db2_dbm_memory'] = self._exec_to_dicts(
            "SELECT * FROM SYSIBMADM.SNAPDBM")

    # ═════════════════════════════════════
    # 规则标量派生（供 db2.yaml 条件引用）
    # ═════════════════════════════════════
    def _build_rule_scalars(self):
        """把 §5 的 db2_* list[dict] 汇总成规则引擎可直接引用的标量 / 字典，
        供 pro/rules/builtin/db2.yaml 的 condition 使用。

        所有派生值都做防御式处理：列表可能被 {ERROR:...} 占用、字段大小写
        不一致、JDBC 返回的 java.sql.Timestamp 等，均不抛异常。
        """
        def _to_map(rows):
            mp = {}
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                key = str(r.get('NAME') or r.get('name') or '').upper()
                val = r.get('VALUE', r.get('value'))
                if key:
                    mp[key] = val
            return mp

        self.context['db2_dbcfg_map'] = _to_map(self.context.get('db2_dbcfg'))
        self.context['db2_dbmcfg_map'] = _to_map(self.context.get('db2_dbmcfg'))

        # 表空间最大使用率
        used = []
        for r in self.context.get('db2_tablespaces') or []:
            if not isinstance(r, dict):
                continue
            v = r.get('USED_PCT', r.get('used_pct'))
            try:
                used.append(float(v))
            except (TypeError, ValueError):
                pass
        self.context['db2_tablespace_max_used'] = float(max(used)) if used else 0.0

        # 锁等待计数 / 应用连接数
        self.context['db2_lockwait_count'] = len(
            [r for r in self.context.get('db2_lockwaits') or [] if isinstance(r, dict)])
        self.context['db2_applications_count'] = len(
            [r for r in self.context.get('db2_applications') or [] if isinstance(r, dict)])

        # 慢 SQL 最大总执行时间（毫秒）
        max_t = 0
        for r in self.context.get('db2_pkg_cache_stmt') or []:
            if not isinstance(r, dict):
                continue
            try:
                t = float(r.get('TOTAL_EXEC_TIME', r.get('total_exec_time')) or 0)
                max_t = max(max_t, t)
            except (TypeError, ValueError):
                pass
        self.context['db2_pkg_cache_max_time'] = max_t
        self.context['db2_pkg_cache_count'] = len(
            [r for r in self.context.get('db2_pkg_cache_stmt') or [] if isinstance(r, dict)])

        # 过期统计（RUNSTATS）计数：STATS_TIME 缺失或早于 90 天
        import datetime
        now_ms = datetime.datetime.now().timestamp() * 1000.0
        stale = 0
        for r in self.context.get('db2_index_runstats') or []:
            if not isinstance(r, dict):
                continue
            st = r.get('STATS_TIME', r.get('stats_time'))
            if not st:
                stale += 1
                continue
            ts_ms = None
            try:
                ts_ms = st.getTime()  # java.sql.Timestamp（JDBC）
            except Exception:
                try:
                    ts_ms = float(st)
                except (TypeError, ValueError):
                    ts_ms = None
            if ts_ms is not None and (now_ms - ts_ms) > 90 * 86400 * 1000.0:
                stale += 1
        self.context['db2_stale_stats_count'] = stale

        # 未使用索引计数（LASTUSED 为空）
        unused = 0
        for r in self.context.get('db2_indexes') or []:
            if not isinstance(r, dict):
                continue
            lu = r.get('LASTUSED', r.get('lastused'))
            if not lu:
                unused += 1
        self.context['db2_unused_index_count'] = unused

        # 实例 / 库 / 版本标量
        for r in self.context.get('db2_instance') or []:
            if isinstance(r, dict):
                self.context['db2_instance_name'] = r.get('INSTANCE_NAME') or r.get('instance_name')
                self.context['db2_database_name'] = r.get('DB_NAME') or r.get('db_name')
        for r in self.context.get('db2_version') or []:
            if isinstance(r, dict):
                self.context['db2_version_number'] = r.get('VERSIONNUMBER') or r.get('versionnumber')

    # ════════════════════════════════════════════════
    # 报告章节（从 inspection.db 加载并执行模板 query）
    # ════════════════════════════════════════════════
    def _load_chapters_from_db(self):
        """从 inspection.db 加载本插件模板章节，并把每个 query_sql 执行结果
        存入 context[query_key]（与 _collect_* 同键时跳过，避免重复执行）。
        """
        db_path = os.path.join(_PROJECT_ROOT, 'data', 'inspection.db')
        if not os.path.exists(db_path):
            self.context['_chapters'] = []
            return
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT ch.id, ch.chapter_number, ch.chapter_title_zh, ch.chapter_title_en, ch.description "
            "FROM inspection_chapter ch JOIN inspection_template t ON ch.template_id=t.id "
            "WHERE t.db_type=? ORDER BY ch.chapter_number",
            (self.db_type,))
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

        # 执行每个模板 query（已由 _collect_* 填充的键跳过）
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
        """采集 Db2 数据（覆盖父类）。

        流程：connect → 逐 _collect_* 填充 db2_* context（规则源）→ 加载章节并执行
        模板 query（报告源）→ 慢查询 → 索引健康 → 基线检查 → 智能分析（DB2 规则）。
        任何子步骤异常均被吞掉，整体保证「巡检无错」。

        Returns:
            成功返回 self.context(dict)，失败返回 (False, error_msg)。
        """
        print("\n[DB2] 开始采集数据...")
        ok, version = self.connect()
        if not ok:
            return False, version

        self.context['version'] = [{'VERSION': version}]
        self.context['db_type'] = 'db2'

        # §5 直接采集（规则与基线数据源）
        methods = [
            '_collect_version', '_collect_instance', '_collect_dbmcfg', '_collect_dbcfg',
            '_collect_dbmembers', '_collect_tablespaces', '_collect_bufferpools',
            '_collect_applications', '_collect_lockwaits', '_collect_locks',
            '_collect_tables', '_collect_indexes', '_collect_index_runstats',
            '_collect_pkg_cache_stmt', '_collect_mon_activity', '_collect_dbm_memory',
        ]
        for m in methods:
            try:
                getattr(self, m)()
            except Exception as e:
                key = '_' + m.split('_collect_')[-1]
                self.context[key] = [{'ERROR': str(e)[:200]}]

        # 汇总 §5 的 db2_* list[dict] 为规则引擎标量（供 db2.yaml 条件引用）
        try:
            self._build_rule_scalars()
        except Exception as e:
            print(f"[DB2] 构建规则标量失败: {e}")

        # 报告章节（从 inspection.db 加载并执行模板 query）
        try:
            self._load_chapters_from_db()
        except Exception as e:
            print(f"[DB2] 加载章节失败: {e}")
            self.context['_chapters'] = []

        # 慢查询深度分析
        try:
            from slow_query_analyzer import get_slow_query_analyzer
            self.context['slow_query_result'] = get_slow_query_analyzer('db2').analyze(self.conn).to_dict()
        except Exception as e:
            print(f"[DB2] 慢查询分析失败: {e}")
            self.context['slow_query_result'] = None

        # 索引健康分析
        try:
            from index_health import get_index_health
            self.context['index_health_result'] = get_index_health('db2', self.conn)
        except Exception as e:
            print(f"[DB2] 索引健康分析失败: {e}")
            self.context['index_health_result'] = None

        # 基线检查（DB2 基线已注册到 inspection.db）
        try:
            self._check_baselines()
        except Exception as e:
            print(f"[DB2] 基线检查失败: {e}")
            self.context['baseline_results'] = []

        # 智能分析（DB2 规则，异常降级空列表）
        try:
            from analyzer import smart_analyze_db2
            self.context['auto_analyze'] = smart_analyze_db2(self.context)
        except Exception as e:
            print(f"[DB2] 智能分析失败: {e}")
            self.context['auto_analyze'] = []

        print(f"[DB2] 数据采集完成，context keys: {list(self.context.keys())}")
        return self.context


# ── 测试连接函数（供 web_ui / 自测调用）────────────────────────────
def test_connection(host, port, user, password, database='', jdbc_url=None, ssl=False):
    """测试 Db2 JDBC 连接。

    Args:
        host: Db2 服务器地址
        port: 端口
        user: 用户名
        password: 密码
        database: 目标数据库名
        jdbc_url: 完整 JDBC URL（可选，以 jdbc:db2 开头则透传）
        ssl: 是否启用 SSL
    Returns:
        (ok, msg)
    """
    try:
        inspector = Db2JdbcInspector(
            host, int(port), user, password,
            database=database, jdbc_url=jdbc_url, ssl=ssl)
        ok, msg = inspector.connect()
        inspector.disconnect()
        return ok, msg
    except Exception as e:
        return False, str(e)


# ── 实时监控连接工厂（供 pro/metrics_collector.py 使用）─────────────
def get_connection(host, port, user, password, database='', jdbc_url=None, ssl=False):
    """返回 DB-API 2.0 兼容的 JDBC 连接包装（JdbcConnectionWrapper）。

    Raises:
        RuntimeError: 连接失败时抛出。
    """
    inspector = Db2JdbcInspector(
        host, int(port), user, password,
        database=database, jdbc_url=jdbc_url, ssl=ssl)
    ok, msg = inspector.connect()
    if not ok:
        raise RuntimeError('DB2 JDBC 连接失败: %s' % msg)
    return inspector.conn


# ── 数据源获取函数（供 web_ui.py 使用）─────────────────────────────
def getData(ip, port, user, password, ssh_info=None, template_id=None):
    """获取 Db2 数据源。

    返回 CompatWrapper 对象，web_ui 通过 wrapper.checkdb('builtin')
    触发采集并获取 context。

    Returns:
        CompatWrapper 对象；失败返回 None。
    """
    ssh_info = ssh_info or {}
    database = ssh_info.get('database', '')
    jdbc_url = ssh_info.get('jdbc_url')
    ssl = bool(ssh_info.get('ssl', False))

    inspector = Db2JdbcInspector(
        ip, int(port), user, password,
        database=database, jdbc_url=jdbc_url, ssl=ssl,
        ssh_info=ssh_info, template_id=template_id)
    ok, msg = inspector.connect()
    if not ok:
        print(f"[DB2] 连接失败: {msg}")
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
        info: 包含 ip/host/port/user/password/database/jdbc_url/ssl 的字典
    Returns:
        (ok, msg)
    """
    info = info or {}
    return test_connection(
        info.get('ip', info.get('host', '')),
        int(info.get('port', 50000) or 50000),
        info.get('user', ''),
        info.get('password', ''),
        database=info.get('database', ''),
        jdbc_url=info.get('jdbc_url'),
        ssl=bool(info.get('ssl', False)),
    )


def get_task_config():
    """返回插件任务配置（供 plugin_loader.get_plugin_task_config 调用）。"""
    return {
        'module_name': 'main_plugin',
        'plugin_path': str(Path(__file__).parent),
        'main_file': 'main_plugin.py',
        'connect_test': _plugin_test_connection,
        'connect_test_args': lambda info: [info],
        'getdata_args': lambda info: (
            [info.get('ip', ''), int(info.get('port', 50000) or 50000),
             info.get('user', ''), info.get('password', '')],
            {'ssh_info': {
                 'database': info.get('database', ''),
                 'jdbc_url': info.get('jdbc_url', ''),
                 'ssl': bool(info.get('ssl', False)),
                 'ssl_truststore': info.get('ssl_truststore', ''),
                 'ssl_truststore_password': info.get('ssl_truststore_password', ''),
                 'ssl_keystore': info.get('ssl_keystore', ''),
                 'ssl_keystore_password': info.get('ssl_keystore_password', ''),
             }, 'template_id': info.get('template_id')}
        ),
        'conn_attr': '',  # getData 返回 CompatWrapper，跳过 conn_attr 检查
        'filename_key': 'webui.db2_report_filename',
        'history_db_type': 'db2',
        'instance_prefix': 'db2',
        'error_task_name': 'DB2',
        'log_start_key': 'webui.log_db2_start',
        'err_module_key': 'webui.err_db2_module',
        'label_default': 'DB2',
        'db_name_default': '',  # Db2 LUW 需显式 database
        'smart_analyze': 'smart_analyze_db2',  # ← 智能分析接入铁律
    }


# ── 注册插件（无侵入式架构）──────────────────────────────────────────
try:
    from plugin_core import InspectionPlugin, register

    class Db2JdbcPluginAdapter(InspectionPlugin):
        """Db2 JDBC 插件适配器（实现标准接口）。"""

        def __init__(self, parse_func=None):
            self.id = 'db2'
            self.name = 'Db2 JDBC'
            self.version = '1.0.0'
            self.db_types = ['db2']
            self.author = 'DBCheck Team'
            self.description = 'IBM Db2 LUW 11.x/12.x 巡检插件（JDBC + JPype）'
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
            print("[DB2] 开始初始化数据（模板 + 基线）...")
            try:
                import sqlite3  # noqa: F401
                from inspection_dal import (
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
                    print("[DB2] 错误：未找到 template_data.json")
                    return

                with open(template_path, 'r', encoding='utf-8') as f:
                    template_data = json.load(f)

                # 1. 创建模板（幂等）
                existing_templates = get_templates_by_db_type('db2', db_path=db_path)
                if existing_templates:
                    template_id = existing_templates[0]['id']
                    print(f"[DB2] 模板已存在，使用现有模板: {template_id}")
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
                    print(f"[DB2] 创建模板: {template_id}")

                # 2. 创建章节和查询（幂等）
                chapters_data = template_data.get('chapters', [])
                print(f"[DB2] 共有 {len(chapters_data)} 个章节")
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
                                print(f"[DB2]   创建查询失败: {query_data['query_key']} - {e}")
                conn.close()

                # 3. 创建基线（从 baseline_data.json，幂等）
                # 若 inspection.db 已通过 init_default_baselines 注册过 db2 基线则跳过，
                # 避免与 inspection_dal.init_default_baselines 的 'db2' 块重复写入。
                baseline_path = os.path.join(os.path.dirname(__file__), 'baseline_data.json')
                existing_bl = get_baselines_by_db_type('db2', db_path=db_path)
                if not existing_bl and os.path.isfile(baseline_path):
                    with open(baseline_path, 'r', encoding='utf-8') as f:
                        baseline_data = json.load(f)
                    print(f"[DB2] 共有 {len(baseline_data)} 条基线")
                    for bl in baseline_data:
                        try:
                            create_baseline(
                                db_type=bl['db_type'],
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
                                print(f"[DB2]   创建基线失败: {bl['param_name']} - {e}")
                print("[DB2] 数据初始化完成")
            except Exception as e:
                print(f"[DB2] 数据初始化失败: {e}")
                traceback.print_exc()

        def on_uninstall(self, db_path: str = None):
            """插件卸载：清理 db2 的模板与基线数据。"""
            print("[DB2] 开始清理数据...")
            try:
                from inspection_dal import (
                    get_templates_by_db_type,
                    get_baselines_by_db_type,
                    delete_template,
                    delete_baseline,
                )
                templates = get_templates_by_db_type('db2')
                for t in templates:
                    try:
                        delete_template(t['id'], db_path=db_path)
                        print(f"[DB2] 删除模板: {t.get('template_name_zh', t['id'])} (ID: {t['id']})")
                    except Exception as e:
                        print(f"[DB2] 删除模板 {t['id']} 失败: {e}")

                baselines = get_baselines_by_db_type('db2')
                for b in baselines:
                    try:
                        delete_baseline(b['id'], db_path=db_path)
                    except Exception as e:
                        print(f"[DB2] 删除基线 {b['id']} 失败: {e}")
                print("[DB2] 数据清理完成")
            except Exception as e:
                print(f"[DB2] 数据清理失败: {e}")

    adapter = Db2JdbcPluginAdapter(parse_func=_plugin_test_connection)
    register(adapter)
    print("[DB2] 插件注册成功")
except Exception as e:
    print(f"[DB2] 插件注册失败: {e}")


if __name__ == '__main__':
    if len(sys.argv) > 2:
        ip = sys.argv[1]
        port = int(sys.argv[2])
        user = sys.argv[3] if len(sys.argv) > 3 else 'db2inst1'
        password = sys.argv[4] if len(sys.argv) > 4 else 'password'
        database = sys.argv[5] if len(sys.argv) > 5 else 'testdb'
        ok, ver = test_connection(ip, port, user, password, database)
        print(("连接成功: %s" % ver) if ok else ("连接失败: %s" % ver))
