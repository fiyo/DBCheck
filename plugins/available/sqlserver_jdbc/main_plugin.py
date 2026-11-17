#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
SQL Server JDBC 巡检插件 —— 通过 JPype + mssql-jdbc-13.4.0.jre11.jar + 共享
jdbc_jvm 连接 Microsoft SQL Server 2017/2019/2022/2025。

设计依据：deliverables/software-company/dbcheck-mssql-jdbc-architecture-2026-08-05.md
- 连接复用 plugins/available/sqlserver_jdbc/jdbc_jvm.py（JVM 单例 + classpath 合并）
- 连接配置复用 connection_config.MssqlJdbcConnectionConfig（jdbc_url 透传 +
  encrypt / trustServerCertificate / 命名实例）
- 数据采集直接跑 SQL Server DMV / sys.*（sys.databases / sys.master_files /
  sys.dm_exec_query_stats / sys.dm_exec_sessions 等），结果以 mssql_*
  list[dict] 存入 context，供 modules/pro/rules/builtin/sqlserver.yaml 规则
  与 inspection.db 模板章节使用（db_types 已追加 sqlserver_jdbc）。
- 智能分析接入铁律：get_task_config() 必须返回 'smart_analyze':
  'smart_analyze_sqlserver'（复用现有函数）。
"""

import os
import sys
import json
import traceback
import datetime
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
    sys.modules['connection_config']，导致相互污染（参见 db2_jdbc 同款修复）。
    """
    spec = importlib.util.spec_from_file_location(
        "sqlserver_jdbc_connection_config",
        os.path.join(_PLUGIN_DIR, "connection_config.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 模块级绑定：connect() 内不再裸导入，避免被其它插件的同名模块污染。
_MssqlCC = _load_own_connection_config()
MssqlJdbcConnectionConfig = _MssqlCC.MssqlJdbcConnectionConfig


def _load_own_jdbc_jvm():
    """按文件绝对路径 + 唯一模块名加载本插件自有的 jdbc_jvm 模块。

    与 db2_jdbc 同款防同名抢注设计。
    """
    spec = importlib.util.spec_from_file_location(
        "sqlserver_jdbc_jdbc_jvm",
        os.path.join(_PLUGIN_DIR, "jdbc_jvm.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sqlserver_jdbc_jdbc_jvm"] = mod
    spec.loader.exec_module(mod)
    return mod


# 项目根目录：main_plugin.py -> sqlserver_jdbc -> available -> plugins -> root
_PROJECT_ROOT = os.path.abspath(os.path.join(_PLUGIN_DIR, "..", "..", ".."))

# BaseInspectionEngine 必须在模块级导入（类继承需要）
from modules.inspection.engine import (
    BaseInspectionEngine,
    LocalSystemInfoCollector,
    RemoteSystemInfoCollector,
    get_host_disk_usage,
)


# ── JDBC 连接包装器（兼容 Python DB-API 2.0）────────────────────────
class JdbcCursorWrapper:
    """包装 JDBC Statement/ResultSet，提供类似 Python DB-API 的 cursor 接口。

    在 db2_jdbc 基础上增强 `_convert_java_obj`：
      - 新增 java.sql.Timestamp → Python datetime 转换
      - 新增 java.math.BigDecimal → Python float 转换
      - 新增 java.time.OffsetDateTime / LocalDateTime → Python datetime
      - 兼容 Boolean / Number / String 的原有转换
    """

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
                or 'FROM SYS' in sql_upper or 'FROM MSDB' in sql_upper \
                or 'FROM SYS.' in sql_upper:
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
        """将 Java 对象转换为 Python 对象。

        转换优先级：
          1. None → None
          2. java.sql.Timestamp / java.sql.Date / java.sql.Time → datetime
          3. java.time.OffsetDateTime / LocalDateTime / LocalDate / Instant → datetime
          4. java.math.BigDecimal / BigInteger → float / int
          5. java.lang.Boolean / Number → bool / int / float
          6. byte[] / bytes / bytearray（varbinary 等二进制列）→ '0x' + hex
          7. 其余 → str(obj)
        """
        if obj is None:
            return None
        # 数字 / 布尔（优先于 str/hasattr 判别，避免误转）
        try:
            if hasattr(obj, 'booleanValue') and callable(obj.booleanValue):
                return bool(obj.booleanValue())
        except Exception:
            pass
        try:
            if hasattr(obj, 'intValue') and callable(obj.intValue) and not hasattr(obj, 'getYear'):
                return obj.intValue()
        except Exception:
            pass
        try:
            if hasattr(obj, 'longValue') and callable(obj.longValue) and not hasattr(obj, 'getYear'):
                return obj.longValue()
        except Exception:
            pass
        try:
            if hasattr(obj, 'doubleValue') and callable(obj.doubleValue):
                return obj.doubleValue()
        except Exception:
            pass
        # java.sql.Timestamp / Date / Time（has getTime + getYear）
        try:
            if hasattr(obj, 'getTime') and hasattr(obj, 'getYear'):
                ts_ms = obj.getTime() / 1000.0
                return datetime.datetime.fromtimestamp(ts_ms)
        except Exception:
            pass
        # java.time.OffsetDateTime / LocalDateTime / LocalDate / Instant
        try:
            if hasattr(obj, 'toLocalDateTime') and callable(obj.toLocalDateTime):
                ldt = obj.toLocalDateTime()
                return datetime.datetime(
                    ldt.getYear(), ldt.getMonthValue(), ldt.getDayOfMonth(),
                    ldt.getHour(), ldt.getMinute(), ldt.getSecond(),
                    ldt.getNano() // 1000,
                )
        except Exception:
            pass
        try:
            if hasattr(obj, 'toEpochMilli') and callable(obj.toEpochMilli):
                ts_ms = obj.toEpochMilli() / 1000.0
                return datetime.datetime.fromtimestamp(ts_ms)
        except Exception:
            pass
        # 二进制（varbinary / byte[] 列，如 sql_handle / plan_handle）：JPype 的
        # Java byte[] 或 Python 原生 bytes / bytearray，统一转 0x 十六进制字符串
        # （SQL Server Management Studio 惯例）。mssql-jdbc 对 varbinary 列返回
        # byte[]，直接 str() 会按 JVM 字符集解码成乱码，因此必须在兜底前处理。
        try:
            if isinstance(obj, (bytes, bytearray)):
                return '0x' + bytes(obj).hex()
            # JPype JByteArray（类名 [B = 原始 byte 数组，hasattr getClass 判定）
            if hasattr(obj, 'getClass') and callable(obj.getClass) \
                    and obj.getClass().getName() == '[B':
                # bytes(obj) 走 buffer protocol（jpype 1.7.1 实测可用）
                try:
                    return '0x' + bytes(obj).hex()
                except Exception:
                    # 兼容无 buffer protocol 的旧版 jpype：逐字节取低 8 位
                    return '0x' + bytes(b & 0xFF for b in obj).hex()
        except Exception:
            pass
        return str(obj)

    def close(self):
        """关闭游标"""
        if self.rs:
            try:
                self.rs.close()
            except Exception:
                pass
        if self.stmt:
            try:
                self.stmt.close()
            except Exception:
                pass

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
        try:
            self.jdbc_conn.close()
        except Exception:
            pass

    def commit(self):
        """提交事务"""
        self.jdbc_conn.commit()

    def rollback(self):
        """回滚事务"""
        self.jdbc_conn.rollback()


# ── 版本号解析 ─────────────────────────────────────────────────────────
def _parse_mssql_version(raw: str) -> str:
    """将 SQL Server 完整版本串解析为可读版本（含版本年号）。

    例：
      "Microsoft SQL Server 2019 (RTM-CU22) - 15.0.4322.2 (X64) ..." → "Microsoft SQL Server 2019 (RTM-CU22)"
      "Microsoft SQL Server 2022 (RTM-GDR) (KB5021522) - 16.0.1050.5 ..." → "Microsoft SQL Server 2022 (RTM-GDR)"
    """
    s = str(raw).strip()
    # 提取 "Microsoft SQL Server YYYY (...)" 段
    import re
    m = re.search(r"(Microsoft SQL Server\s+\d{4}(?:\s*\([^\)]+\))?)", s)
    if m:
        return m.group(1).strip()
    # 兜底：返回原串前 80 字符
    return s[:80] if s else "unknown"


# ── SQL Server JDBC 巡检器 ─────────────────────────────────────────────
class MssqlJdbcInspector(BaseInspectionEngine):
    """SQL Server JDBC 巡检器。

    继承 BaseInspectionEngine，覆盖 connect() / collect_data()，直接跑
    SQL Server DMV/sys.* 填充 mssql_* context（对应 architecture §3）。
    """

    def __init__(self, host, port, user, password, database=None,
                 ssh_info=None, template_id=None, jdbc_url=None,
                 instance_name="", encrypt=False, trust_server_certificate=True,
                 chapter_ids=None, driver_version=''):
        super().__init__(host, int(port), user, password, database=database,
                         ssh_info=ssh_info, template_id=template_id,
                         chapter_ids=chapter_ids)
        self.db_type = 'sqlserver_jdbc'
        self.jdbc_url = jdbc_url or ''
        self.instance_name = instance_name or ''
        self.encrypt = bool(encrypt)
        self.trust_server_certificate = bool(trust_server_certificate)
        self.conn = None
        self.cursor = None
        self.raw_jdbc_conn = None
        self.conn_cfg = None
        self._mssql_version_str = 'unknown'
        self._used_encrypt_fallback = False
        self.driver_version = driver_version or ''
        self.jdbc_driver_path = None
        try:
            from modules import driver_registry as _dr
            _jars = _dr.resolve_jdbc_driver_jars('sqlserver_jdbc', self.driver_version)
            if _jars:
                self.jdbc_driver_path = _jars
        except Exception:
            self.jdbc_driver_path = None

    # ════════════════════════════════════════════════
    # 连接层
    # ════════════════════════════════════════════════
    def connect(self) -> Tuple[bool, str]:
        """连接 SQL Server 数据库（统一连接层 JPype 模式）。

        Returns:
            (ok, msg)：ok 为 True 时 msg 是版本可读串；
                          ok 为 False 时 msg 是错误信息。
        """
        try:
            # 1. 构建连接配置（MssqlJdbcConnectionConfig 已在模块级按路径绑定；
            #    build_jdbc_url/build_properties 均委托统一层）
            cfg = MssqlJdbcConnectionConfig(
                host=self.host,
                port=int(self.port),
                user=self.user,
                password=self.password,
                database=self.database or 'master',
                instance_name=self.instance_name or '',
                jdbc_url=self.jdbc_url or '',
                encrypt=self.encrypt,
                trust_server_certificate=self.trust_server_certificate,
            )
            self.conn_cfg = cfg

            # SSL 回退钩子：encrypt=false 时若错误与 TLS/encrypt 相关，自动
            # 启用 encrypt=true;trustServerCertificate=true 再试一次（兼容
            # 服务器强制加密场景）。经统一层 on_error 回调注入。
            def _ssl_retry(e, ctx):
                err = str(e).lower()
                _ssl_keywords = (
                    'encrypt', 'ssl', 'tls', 'secure',
                    'trustservercertificate', 'certificate', 'handshake',
                    'unexpected_message', 'did not return a response',
                )
                if (not cfg.encrypt) and any(k in err for k in _ssl_keywords):
                    cfg.encrypt = True
                    cfg.trust_server_certificate = True
                    self._used_encrypt_fallback = True
                    print(f"[MSSQL-JDBC] 首次连接因 TLS/encrypt 失败，尝试启用 TLS 回退")
                    return cfg.build_jdbc_url(), cfg.build_properties()
                return None

            # 2. 统一连接层：驱动解析 / JVM 启动 / URL 构造 / props 装配 / 建连
            from modules.jdbc_connector import open_jdbc_connection
            conn, meta = open_jdbc_connection(
                'sqlserver_jdbc', self.host, self.port,
                user=self.user, password=self.password,
                driver_version=self.driver_version,
                database=self.database or 'master',
                jdbc_url=self.jdbc_url or '',
                encrypt=self.encrypt,
                trust_server_certificate=self.trust_server_certificate,
                mode='jpype',
                properties=cfg.build_properties(),
                on_error=_ssl_retry,
                instance_name=self.instance_name or '',
                login_timeout_s=cfg.login_timeout_s,
                application_name=cfg.application_name,
            )
            if conn is None:
                return False, meta['error']

            self.raw_jdbc_conn = conn
            self.conn = JdbcConnectionWrapper(conn)
            self.cursor = self.conn.cursor()

            # 3. 读取版本
            self.cursor.execute("SELECT @@VERSION")
            row = self.cursor.fetchone()
            version_raw = str(row[0]) if row else 'unknown'
            self._mssql_version_str = _parse_mssql_version(version_raw)
            self.context['mssql_version'] = [{'VERSION': version_raw, 'VERSION_STR': self._mssql_version_str}]
            self.context['version'] = [{'VERSION': version_raw, 'VERSION_STR': self._mssql_version_str}]

            if self._used_encrypt_fallback:
                print(f"[MSSQL-JDBC] 连接成功（已自动启用 TLS 加密回退），版本: {self._mssql_version_str}")
                return True, f"{self._mssql_version_str}（已自动启用 TLS 加密回退）"
            print(f"[MSSQL-JDBC] 连接成功，版本: {self._mssql_version_str}")
            return True, self._mssql_version_str
        except Exception as e:
            err_msg = str(e)
            print(f"[MSSQL-JDBC] 连接失败: {err_msg}")
            traceback.print_exc()
            return False, err_msg

    def disconnect(self):
        """关闭数据库连接"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
        except Exception as e:
            print(f"[MSSQL-JDBC] 关闭连接失败: {e}")

    def get_template_id(self):
        """返回 inspection_template 表的 template_id。"""
        try:
            from modules.inspection.dal import get_templates_by_db_type
            templates = get_templates_by_db_type("sqlserver_jdbc")
            return templates[0]['id'] if templates else None
        except Exception as e:
            print(f"[MSSQL-JDBC] 获取模板 ID 失败: {e}")
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
    # 数据采集（15 个 _collect_* 方法）
    # ════════════════════════════════════════════════
    def _collect_version(self):
        try:
            self.cursor.execute("SELECT @@VERSION")
            row = self.cursor.fetchone()
            v = str(row[0]) if row else 'unknown'
            self.context['mssql_version'] = [{'VERSION': v, 'VERSION_STR': _parse_mssql_version(v)}]
            self.context['version'] = [{'VERSION': v, 'VERSION_STR': _parse_mssql_version(v)}]
        except Exception as e:
            self.context['mssql_version'] = [{'ERROR': str(e)[:200]}]

    def _collect_instance(self):
        sql = (
            "SELECT @@SERVERNAME AS server_name, "
            "@@SERVICENAME AS service_name, "
            "SERVERPROPERTY('IsClustered') AS is_clustered, "
            "SERVERPROPERTY('MachineName') AS machine_name, "
            "SERVERPROPERTY('Edition') AS edition, "
            "SERVERPROPERTY('ProductVersion') AS product_version, "
            "SERVERPROPERTY('ProductLevel') AS product_level"
        )
        self.context['mssql_instance'] = self._exec_to_dicts(sql)

    def _collect_configurations(self):
        sql = (
            "SELECT name, value, value_in_use, minimum, maximum, "
            "description, is_dynamic, is_advanced "
            "FROM sys.configurations WHERE is_advanced = 1 "
            "ORDER BY name"
        )
        self.context['mssql_configurations'] = self._exec_to_dicts(sql)

    def _collect_databases(self):
        sql = (
            "SELECT database_id, name, state_desc, recovery_model_desc, "
            "compatibility_level, is_read_only, is_auto_close_on, is_auto_shrink_on, "
            "is_auto_create_stats_on, is_auto_update_stats_on, "
            "is_in_standby, is_encrypted, "
            "create_date, collation_name "
            "FROM sys.databases ORDER BY database_id"
        )
        self.context['mssql_databases'] = self._exec_to_dicts(sql)

    def _collect_tablespaces(self):
        # sys.master_files 全字段；用量聚合由模板查询补足
        sql = (
            "SELECT database_id, file_id, type_desc, name AS logical_name, "
            "physical_name, size * 8 AS size_kb, "
            "size * 8 / 1024.0 AS size_mb, "
            "size * 8 / 1024.0 / 1024.0 AS size_gb "
            "FROM sys.master_files "
            "ORDER BY database_id, file_id"
        )
        self.context['mssql_tablespaces'] = self._exec_to_dicts(sql)

    def _collect_locks(self):
        sql = (
            "SELECT request_session_id, resource_type, resource_subtype, "
            "resource_database_id, resource_description, request_mode, "
            "request_status, request_owner_type "
            "FROM sys.dm_tran_locks "
            "WHERE request_status IN ('WAIT', 'CONVERT') "
            "ORDER BY request_session_id"
        )
        self.context['mssql_locks'] = self._exec_to_dicts(sql)

    def _collect_sessions(self):
        sql = (
            "SELECT session_id, login_name, host_name, program_name, "
            "status, login_time, last_request_start_time, last_request_end_time, "
            "cpu_time, memory_usage, reads, writes, logical_reads, "
            "transaction_isolation_level "
            "FROM sys.dm_exec_sessions "
            "WHERE is_user_process = 1 "
            "ORDER BY session_id"
        )
        self.context['mssql_sessions'] = self._exec_to_dicts(sql)

    def _collect_index_usage(self):
        sql = (
            "SELECT OBJECT_NAME(s.object_id) AS table_name, "
            "i.name AS index_name, s.index_id, s.user_seeks, s.user_scans, "
            "s.user_lookups, s.user_updates, "
            "s.last_user_seek, s.last_user_scan, s.last_user_lookup, s.last_user_update "
            "FROM sys.dm_db_index_usage_stats s "
            "INNER JOIN sys.indexes i ON s.object_id = i.object_id AND s.index_id = i.index_id "
            "WHERE OBJECTPROPERTY(s.object_id, 'IsUserTable') = 1 "
            "ORDER BY s.user_seeks + s.user_scans + s.user_lookups DESC"
        )
        self.context['mssql_index_usage'] = self._exec_to_dicts(sql)

    def _collect_index_physical(self):
        sql = (
            "SELECT OBJECT_NAME(s.object_id) AS table_name, "
            "i.name AS index_name, s.index_id, s.index_type_desc, "
            "s.avg_fragmentation_in_percent, s.avg_page_space_used_in_percent, "
            "s.page_count, s.record_count "
            "FROM sys.dm_db_index_physical_stats(DB_ID(), NULL, NULL, NULL, 'LIMITED') s "
            "INNER JOIN sys.indexes i ON s.object_id = i.object_id AND s.index_id = i.index_id "
            "WHERE s.page_count > 0 "
            "ORDER BY s.avg_fragmentation_in_percent DESC"
        )
        self.context['mssql_index_physical'] = self._exec_to_dicts(sql)

    def _collect_top_sql(self):
        sql = (
            "SELECT TOP 50 "
            "s.execution_count, "
            "s.total_worker_time, s.total_elapsed_time, s.total_logical_reads, "
            "s.total_logical_writes, s.total_physical_reads, "
            "s.creation_time, s.last_execution_time, "
            "SUBSTRING(t.text, (s.statement_start_offset/2)+1, "
            "  ((CASE s.statement_end_offset WHEN -1 THEN DATALENGTH(t.text) "
            "    ELSE s.statement_end_offset END - s.statement_start_offset)/2)+1) AS query_text "
            "FROM sys.dm_exec_query_stats s "
            "CROSS APPLY sys.dm_exec_sql_text(s.sql_handle) t "
            "ORDER BY s.total_elapsed_time DESC"
        )
        self.context['mssql_top_sql'] = self._exec_to_dicts(sql)

    def _collect_wait_stats(self):
        sql = (
            "SELECT TOP 30 wait_type, wait_time_ms, wait_tasks_count, "
            "max_wait_time_ms, signal_wait_time_ms "
            "FROM sys.dm_os_wait_stats "
            "WHERE wait_type NOT IN ("
            "'BROKER_EVENTHANDLER','BROKER_RECEIVE_WAITFOR','BROKER_TASK_STOP',"
            "'BROKER_TO_FLUSH','BROKER_TRANSMITTER','CHECKPOINT_QUEUE',"
            "'CHKPT','CLR_AUTO_EVENT','CLR_MANUAL_EVENT','CLR_SEMAPHORE',"
            "'CXCONSUMER','DBMIRROR_DBM_EVENT','DBMIRROR_EVENTS_QUEUE',"
            "'DBMIRROR_WORKER_QUEUE','DBMIRRORING_CMD','DIRTY_PAGE_POLL',"
            "'DISPATCHER_QUEUE_SEMAPHORE','EXECSYNC','FSAGENT',"
            "'FT_IFTS_SCHEDULER_IDLE_WAIT','FT_IFTSHC_MUTEX','HADR_CLUSAPI_CALL',"
            "'HADR_FILESTREAM_IOMGR_IOCOMPLETION','HADR_LOGCAPTURE_WAIT',"
            "'HADR_NOTIFICATION_DEQUEUE','HADR_TIMER_TASK','HADR_WORK_QUEUE',"
            "'KSOURCE_WAKEUP','LAZYWRITER_SLEEP','LOGMGR_QUEUE',"
            "'ONDEMAND_TASK_QUEUE','PARALLEL_REDO_WORKER_WAIT_WORK',"
            "'PREEMPTIVE_OS_FLUSHFILEBUFFERS','PREEMPTIVE_XE_GETTARGETSTATE',"
            "'PWAIT_ALL_COMPONENTS_INITIALIZED','PWAIT_DIRECTLOGCONSUMER_GETNEXT',"
            "'QDS_ASYNC_QUEUE','QDS_CLEANUP_STALE_QUERIES_TASK_MAIN_LOOP_SLEEP',"
            "'QDS_PERSIST_TASK_MAIN_LOOP_SLEEP','REDO_THREAD_PENDING_WORK',"
            "'REQUEST_FOR_DEADLOCK_SEARCH','RESOURCE_QUEUE','SERVER_IDLE_CHECK',"
            "'SLEEP_BPOOL_FLUSH','SLEEP_DBSTARTUP','SLEEP_DCOMSTARTUP',"
            "'SLEEP_MASTERDBREADY','SLEEP_MASTERMDREADY','SLEEP_MASTERUPGRADED',"
            "'SLEEP_MSDBSTARTUP','SLEEP_SYSTEMTASK','SLEEP_TASK',"
            "'SLEEP_TEMPDBSTARTUP','SNI_HTTP_ACCEPT','SP_SERVER_DIAGNOSTICS_SLEEP',"
            "'SQLTRACE_BUFFER_FLUSH','SQLTRACE_INCREMENTAL_FLUSH_SLEEP',"
            "'SQLTRACE_WAIT_ENTRIES','UCE_THREAD_REGROUP','WAIT_FOR_RESULTS',"
            "'WAIT_XTP_OFFLINE_CKPT_NEW_LOG','WAIT_XTP_RECOVERY',"
            "'WAIT_XTP_HOST_WAIT','WAIT_XTP_INTERNAL_TDES',"
            "'XE_DISPATCHER_JOIN','XE_DISPATCHER_WAIT','XE_TIMER_EVENT') "
            "ORDER BY wait_time_ms DESC"
        )
        self.context['mssql_wait_stats'] = self._exec_to_dicts(sql)

    def _collect_db_files(self):
        sql = (
            "SELECT database_id, file_id, type_desc, name AS logical_name, "
            "physical_name, size * 8 AS size_kb, "
            "CAST(size AS BIGINT) * 8 / 1024.0 AS size_mb, "
            "is_read_only, is_primary_file, is_log_file, growth, is_percent_growth "
            "FROM sys.master_files "
            "ORDER BY database_id, file_id"
        )
        self.context['mssql_db_files'] = self._exec_to_dicts(sql)

    def _collect_backup_history(self):
        sql = (
            "SELECT TOP 100 "
            "bs.database_name, bs.backup_start_date, bs.backup_finish_date, "
            "bs.type, bs.recovery_model, "
            "CAST(bs.backup_size AS BIGINT) / 1024.0 / 1024.0 AS backup_size_mb, "
            "bmf.physical_device_name, bs.server_name, bs.user_name "
            "FROM msdb.dbo.backupset bs "
            "LEFT JOIN msdb.dbo.backupmediafamily bmf "
            "  ON bs.media_set_id = bmf.media_set_id "
            "WHERE bs.backup_start_date >= DATEADD(DAY, -7, GETDATE()) "
            "ORDER BY bs.backup_start_date DESC"
        )
        self.context['mssql_backup_history'] = self._exec_to_dicts(sql)

    def _collect_always_on(self):
        # 捕获无 AG 的情况：直接判断 dm_hadr_availability_group_states 是否存在
        try:
            sql = (
                "SELECT ag.name AS availability_group_name, "
                "ag.primary_replica, ags.synchronization_health_desc, "
                "ags.primary_recovery_health_desc, ags.secondary_recovery_health_desc "
                "FROM sys.dm_hadr_availability_group_states ags "
                "JOIN sys.availability_groups ag ON ags.group_id = ag.group_id"
            )
            self.context['mssql_always_on'] = self._exec_to_dicts(sql)
        except Exception as e:
            err = str(e)
            # 297 / 9762：未启用 AG / AlwaysOn 功能关闭 → 写空列表而非错误
            if any(s in err for s in ('297', '9762', 'unavailable', 'does not exist', '42S02')):
                self.context['mssql_always_on'] = []
            else:
                self.context['mssql_always_on'] = [{'ERROR': err[:200]}]

    def _collect_dbmemory(self):
        sql = (
            "SELECT TOP 20 type, name, memory_node_id, "
            "pages_kb, pages_kb / 1024.0 AS pages_mb "
            "FROM sys.dm_os_memory_clerks "
            "ORDER BY pages_kb DESC"
        )
        self.context['mssql_dbmemory'] = self._exec_to_dicts(sql)

    # ════════════════════════════════════════════════
    # 规则标量派生（供 sqlserver.yaml 条件引用）
    # ════════════════════════════════════════════════
    def _build_rule_scalars(self):
        """把 15 个 mssql_* list[dict] 汇总成规则引擎可直接引用的标量 / 字典，
        供 modules/pro/rules/builtin/sqlserver.yaml 的 condition 使用。

        派生约 12 个标量：
          mssql_version_str / mssql_db_count / mssql_max_conn_pct /
          mssql_longest_running_query_ms / mssql_top_wait_type /
          mssql_db_total_gb / mssql_backup_24h / mssql_stale_stats_count /
          mssql_missing_index_count / mssql_blocked_session_count /
          mssql_tempdb_used_gb / mssql_ag_health

        所有派生值都做防御式处理：列表可能被 {ERROR:...} 占用、字段大小写
        不一致、JDBC 返回的 java.sql.Timestamp 等，均不抛异常。
        """
        # 1. 标量：版本
        for r in self.context.get('mssql_version') or []:
            if isinstance(r, dict):
                self.context['mssql_version_str'] = r.get('VERSION_STR') or r.get('version_str')

        # 2. 数据库计数
        dbs = self.context.get('mssql_databases') or []
        self.context['mssql_db_count'] = len([r for r in dbs if isinstance(r, dict)])

        # 3. 表空间总用量（GB）
        total_kb = 0
        for r in self.context.get('mssql_db_files') or []:
            if not isinstance(r, dict):
                continue
            try:
                total_kb += float(r.get('SIZE_KB', r.get('size_kb')) or 0)
            except (TypeError, ValueError):
                pass
        self.context['mssql_db_total_gb'] = round(total_kb / 1024.0 / 1024.0, 2)

        # 4. 长时间运行 SQL 最长耗时（ms）
        longest = 0
        max_conn = 0
        for r in self.context.get('mssql_sessions') or []:
            if not isinstance(r, dict):
                continue
            try:
                cpu = float(r.get('CPU_TIME', r.get('cpu_time')) or 0)
                if cpu > longest:
                    longest = cpu
            except (TypeError, ValueError):
                pass
        self.context['mssql_longest_running_query_ms'] = longest

        # 5. top wait type（按 wait_time_ms 降序的第一行 wait_type）
        waits = self.context.get('mssql_wait_stats') or []
        if waits and isinstance(waits[0], dict):
            self.context['mssql_top_wait_type'] = (
                waits[0].get('WAIT_TYPE') or waits[0].get('wait_type') or 'unknown'
            )
        else:
            self.context['mssql_top_wait_type'] = 'unknown'

        # 6. 24h 内是否有备份
        now = datetime.datetime.now()
        backup_24h = 0
        for r in self.context.get('mssql_backup_history') or []:
            if not isinstance(r, dict):
                continue
            ts = r.get('BACKUP_START_DATE', r.get('backup_start_date'))
            if not ts:
                continue
            try:
                if hasattr(ts, 'getTime'):
                    delta = now - datetime.datetime.fromtimestamp(ts.getTime() / 1000.0)
                elif isinstance(ts, datetime.datetime):
                    delta = now - ts
                else:
                    continue
                if delta.total_seconds() < 86400:
                    backup_24h += 1
            except Exception:
                pass
        self.context['mssql_backup_24h'] = backup_24h

        # 7. 索引统计过期计数（90 天未使用视为 stale；DMV 重启后归零）
        stale = 0
        for r in self.context.get('mssql_index_usage') or []:
            if not isinstance(r, dict):
                continue
            try:
                seeks = int(r.get('USER_SEEKS', r.get('user_seeks')) or 0)
                scans = int(r.get('USER_SCANS', r.get('user_scans')) or 0)
                lookups = int(r.get('USER_LOOKUPS', r.get('user_lookups')) or 0)
                if seeks + scans + lookups == 0:
                    stale += 1
            except (TypeError, ValueError):
                pass
        self.context['mssql_stale_stats_count'] = stale

        # 8. 缺失索引计数（高碎片率 > 30% 视为潜在问题索引）
        miss = 0
        for r in self.context.get('mssql_index_physical') or []:
            if not isinstance(r, dict):
                continue
            try:
                frag = float(r.get('AVG_FRAGMENTATION_IN_PERCENT', r.get('avg_fragmentation_in_percent')) or 0)
                if frag > 30:
                    miss += 1
            except (TypeError, ValueError):
                pass
        self.context['mssql_missing_index_count'] = miss

        # 9. 阻塞会话计数（锁处于 WAIT 状态）
        blocked = 0
        for r in self.context.get('mssql_locks') or []:
            if not isinstance(r, dict):
                continue
            status = r.get('REQUEST_STATUS', r.get('request_status'))
            if status and 'WAIT' in str(status).upper():
                blocked += 1
        self.context['mssql_blocked_session_count'] = blocked

        # 10. tempdb 用量（GB）
        tempdb_kb = 0
        for r in self.context.get('mssql_db_files') or []:
            if not isinstance(r, dict):
                continue
            db_id = r.get('DATABASE_ID', r.get('database_id'))
            name = str(r.get('LOGICAL_NAME', r.get('logical_name')) or '').lower()
            tdesc = str(r.get('TYPE_DESC', r.get('type_desc')) or '')
            # tempdb database_id=2；按 logical_name 含 'tempdev' / 'templog' 也可
            if (str(db_id) == '2' or 'temp' in name) and 'LOG' not in tdesc.upper():
                try:
                    tempdb_kb += float(r.get('SIZE_KB', r.get('size_kb')) or 0)
                except (TypeError, ValueError):
                    pass
        self.context['mssql_tempdb_used_gb'] = round(tempdb_kb / 1024.0 / 1024.0, 2)

        # 11. AG 健康
        ag_list = self.context.get('mssql_always_on') or []
        if isinstance(ag_list, list) and ag_list and isinstance(ag_list[0], dict):
            if 'ERROR' in ag_list[0]:
                self.context['mssql_ag_health'] = 'UNAVAILABLE'
            elif not ag_list:
                self.context['mssql_ag_health'] = 'NOT_CONFIGURED'
            else:
                worst = 'HEALTHY'
                for r in ag_list:
                    s = str(r.get('SYNCHRONIZATION_HEALTH_DESC', r.get('synchronization_health_desc')) or '').upper()
                    if 'NOT_HEALTHY' in s or 'CRITICAL' in s:
                        worst = 'CRITICAL'
                        break
                    if 'PARTIALLY' in s and worst == 'HEALTHY':
                        worst = 'PARTIALLY_HEALTHY'
                self.context['mssql_ag_health'] = worst
        else:
            self.context['mssql_ag_health'] = 'NOT_CONFIGURED'

        # 12. 连接使用率（dm_exec_sessions / max_worker_count）
        try:
            sessions = [r for r in (self.context.get('mssql_sessions') or []) if isinstance(r, dict)]
            max_conn = max(len(sessions), 1)
            # 从 sys.configurations 取 max worker threads（若可访问）
            max_worker = 0
            for r in self.context.get('mssql_configurations') or []:
                if not isinstance(r, dict):
                    continue
                if str(r.get('NAME', r.get('name')) or '').lower() == 'max worker threads':
                    try:
                        max_worker = int(r.get('VALUE_IN_USE', r.get('value_in_use')) or 0)
                    except (TypeError, ValueError):
                        max_worker = 0
                    break
            if max_worker > 0:
                self.context['mssql_max_conn_pct'] = round(len(sessions) * 100.0 / max_worker, 1)
                self.context['max_worker_count'] = max_worker
                self.context['active_connections'] = len(sessions)
            else:
                self.context['mssql_max_conn_pct'] = 0.0
        except Exception:
            self.context['mssql_max_conn_pct'] = 0.0

    # ════════════════════════════════════════════════
    # 报告章节（从 inspection.db 加载并执行模板 query）
    # ════════════════════════════════════════════════
    def _load_chapters_from_db(self):
        """从 inspection.db 加载本插件模板章节，并把每个 query_sql 执行结果
        存入 context[query_key]（与 _collect_* 同键时跳过，避免重复执行）。
        """
        # 统一路径：frozen 下 INSPECTION_DB 在包外 <exe>/data，PROJECT_ROOT 指向 _internal
        from modules.core import paths
        db_path = str(paths.resolve(paths.INSPECTION_DB, Path(_PROJECT_ROOT) / 'data' / 'inspection.db'))
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
        """采集 SQL Server 数据（覆盖父类）。

        流程：connect → 逐 _collect_* 填充 mssql_* context（规则源）→ 加载章节
        并执行模板 query（报告源）→ 慢查询 → 索引健康 → 基线检查 → 智能分析
        （复用 smart_analyze_sqlserver）。任何子步骤异常均被吞掉，整体保证
        「巡检无错」。

        Returns:
            成功返回 self.context(dict)，失败返回 (False, error_msg)。
        """
        print("\n[MSSQL-JDBC] 开始采集数据...")
        ok, version = self.connect()
        if not ok:
            return False, version

        self.context['version'] = [{'VERSION': version}]
        self.context['db_type'] = 'sqlserver_jdbc'

        # 15 个 _collect_* 直接采集（规则与基线数据源）
        methods = [
            '_collect_version', '_collect_instance', '_collect_configurations',
            '_collect_databases', '_collect_tablespaces', '_collect_locks',
            '_collect_sessions', '_collect_index_usage', '_collect_index_physical',
            '_collect_top_sql', '_collect_wait_stats', '_collect_db_files',
            '_collect_backup_history', '_collect_always_on', '_collect_dbmemory',
        ]
        for i, m in enumerate(methods):
            try:
                self.print_progress_bar(i + 1, len(methods), prefix='[MSSQL-JDBC]', suffix=f'{m} ({i+1}/{len(methods)})')
                getattr(self, m)()
            except Exception as e:
                key = '_' + m.split('_collect_')[-1]
                self.context[key] = [{'ERROR': str(e)[:200]}]

        # 汇总 15 个 mssql_* list[dict] 为规则引擎标量（供 sqlserver.yaml 条件引用）
        try:
            self._build_rule_scalars()
        except Exception as e:
            print(f"[MSSQL-JDBC] 构建规则标量失败: {e}")

        # 系统资源采集（与 db2 模式一致）
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
            print(f"[MSSQL-JDBC] 系统信息采集失败: {e}")
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
            print(f"[MSSQL-JDBC] 加载章节失败: {e}")
            self.context['_chapters'] = []

        # 慢查询深度分析（复用通用分析器，DB type 走 'sqlserver_jdbc'）
        try:
            from modules.inspection.slow_query import get_slow_query_analyzer
            self.context['slow_query_result'] = get_slow_query_analyzer('sqlserver_jdbc').analyze(self.conn).to_dict()
        except Exception as e:
            print(f"[MSSQL-JDBC] 慢查询分析失败: {e}")
            self.context['slow_query_result'] = None

        # 索引健康分析
        try:
            from modules.inspection.index_health import get_index_health
            self.context['index_health_result'] = get_index_health('sqlserver_jdbc', self.conn)
        except Exception as e:
            print(f"[MSSQL-JDBC] 索引健康分析失败: {e}")
            self.context['index_health_result'] = None

        # 基线检查
        try:
            self._check_baselines()
        except Exception as e:
            print(f"[MSSQL-JDBC] 基线检查失败: {e}")
            self.context['baseline_results'] = []

        # 智能分析（复用 sqlserver 智能分析函数；db_types 已含 sqlserver_jdbc）
        try:
            from modules.inspection.analyzer import smart_analyze_sqlserver
            self.context['auto_analyze'] = smart_analyze_sqlserver(self.context)
        except Exception as e:
            print(f"[MSSQL-JDBC] 智能分析失败: {e}")
            self.context['auto_analyze'] = []

        print(f"[MSSQL-JDBC] 数据采集完成，context keys: {list(self.context.keys())}")

        # AI 诊断（统一接口）
        try:
            from modules.inspection.analyzer import run_ai_diagnosis
            self.context['ai_advice'] = run_ai_diagnosis(
                self.db_type,
                getattr(self, 'host', '') or self.db_type,
                self.context,
                lang=getattr(self, '_lang', 'zh'),
                timeout=600,
            )
        except Exception as e:
            print(f"[MSSQL-JDBC] AI 诊断失败: {e}")
            self.context['ai_advice'] = ''

        return self.context


# ── 测试连接函数（供 web_ui / 自测调用）────────────────────────────
def test_connection(host, port, user, password, database='', jdbc_url=None,
                    instance_name=None, encrypt=False, trust_server_certificate=True,
                    driver_version='', **kwargs):
    """测试 SQL Server JDBC 连接。

    Args:
        host: SQL Server 地址
        port: 端口
        user: 用户名
        password: 密码
        database: 目标数据库（默认 master）
        jdbc_url: 完整 JDBC URL（可选，以 jdbc:sqlserver:// 开头则透传）
        instance_name: 命名实例名（可选；非空时 URL 用 instanceName 替代端口）
        encrypt: 启用 TLS 加密（默认 false；内网/旧版 SQL Server 建议关闭，有 CA 证书环境可显式设为 true）
        trust_server_certificate: 信任自签证书（默认 true）
    Returns:
        (ok, msg)
    """
    try:
        inspector = MssqlJdbcInspector(
            host, int(port), user, password,
            database=database or 'master',
            jdbc_url=jdbc_url,
            instance_name=instance_name or '',
            encrypt=encrypt,
            trust_server_certificate=trust_server_certificate,
            driver_version=driver_version,
        )
        ok, msg = inspector.connect()
        inspector.disconnect()
        return ok, msg
    except Exception as e:
        return False, str(e)


# ── 实时监控连接工厂（供 pro/metrics_collector.py 使用）─────────────
def get_connection(host, port, user, password, database='', jdbc_url=None,
                   instance_name=None, encrypt=False, trust_server_certificate=True,
                   driver_version=''):
    """返回 DB-API 2.0 兼容的 JDBC 连接包装（JdbcConnectionWrapper）。

    Raises:
        RuntimeError: 连接失败时抛出。
    """
    inspector = MssqlJdbcInspector(
        host, int(port), user, password,
        database=database or 'master',
        jdbc_url=jdbc_url,
        instance_name=instance_name or '',
        encrypt=encrypt,
        trust_server_certificate=trust_server_certificate,
        driver_version=driver_version,
    )
    ok, msg = inspector.connect()
    if not ok:
        raise RuntimeError('SQL Server JDBC 连接失败: %s' % msg)
    return inspector.conn


# ── 数据源获取函数（供 web_ui.py 使用）─────────────────────────────
def getData(ip, port, user, password, ssh_info=None, template_id=None, driver_version=''):
    """获取 SQL Server JDBC 数据源。

    返回 CompatWrapper 对象，web_ui 通过 wrapper.checkdb('builtin')
    触发采集并获取 context。

    Returns:
        CompatWrapper 对象；失败返回 None。
    """
    ssh_info = ssh_info or {}
    database = ssh_info.get('database', '')
    jdbc_url = ssh_info.get('jdbc_url')
    instance_name = ssh_info.get('instance_name', '')
    encrypt = bool(ssh_info.get('encrypt', False))
    trust_server_certificate = bool(ssh_info.get('trust_server_certificate', True))

    inspector = MssqlJdbcInspector(
        ip, int(port), user, password,
        database=database, jdbc_url=jdbc_url, instance_name=instance_name,
        encrypt=encrypt, trust_server_certificate=trust_server_certificate,
        driver_version=driver_version,
        ssh_info=ssh_info, template_id=template_id)
    ok, msg = inspector.connect()
    if not ok:
        print(f"[MSSQL-JDBC] 连接失败: {msg}")
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
        info: 包含 ip/host/port/user/password/database/jdbc_url/instance_name/
              encrypt/trust_server_certificate 的字典
    Returns:
        (ok, msg)
    """
    info = info or {}
    return test_connection(
        info.get('ip', info.get('host', '')),
        int(info.get('port', 1433) or 1433),
        info.get('user', ''),
        info.get('password', ''),
        database=info.get('database', ''),
        jdbc_url=info.get('jdbc_url'),
        instance_name=info.get('instance_name', ''),
        encrypt=bool(info.get('encrypt', False)),
        trust_server_certificate=bool(info.get('trust_server_certificate', True)),
    )


def parse_connection_result(ok: bool, msg: Any) -> Dict[str, Any]:
    """解析 SQL Server JDBC 连接测试结果（供 web_ui.py 动态调用）。

    从 msg 提取年份（2017/2019/2022/2025）→ mssql_major_version
    """
    if not ok:
        return {}
    import re
    m = re.search(r"SQL Server\s+(\d{4})", str(msg))
    if m:
        try:
            year = int(m.group(1))
            return {'mssql_major_version': year}
        except (TypeError, ValueError):
            pass
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
            [info.get('ip', ''), int(info.get('port', 1433) or 1433),
             info.get('user', ''), info.get('password', '')],
            {'ssh_info': {
                 'database': info.get('database', ''),
                 'jdbc_url': info.get('jdbc_url', ''),
                 'instance_name': info.get('instance_name', ''),
                 'encrypt': bool(info.get('encrypt', False)),
                 'trust_server_certificate': bool(info.get('trust_server_certificate', True)),
             }, 'template_id': info.get('template_id'),
             'driver_version': info.get('driver_version', '')}
        ),
        'conn_attr': '',  # getData 返回 CompatWrapper，跳过 conn_attr 检查
        'filename_key': 'webui.sqlserver_jdbc_report_filename',
        'history_db_type': 'sqlserver_jdbc',
        'instance_prefix': 'sqlserver_jdbc',
        'error_task_name': 'MSSQL-JDBC',
        'log_start_key': 'webui.log_sqlserver_jdbc_start',
        'err_module_key': 'webui.err_sqlserver_jdbc_module',
        'label_default': 'MSSQL-JDBC',
        'db_name_default': 'master',
        'smart_analyze': 'smart_analyze_sqlserver',  # ← 智能分析接入铁律（复用）
    }


# ── 注册插件（无侵入式架构）──────────────────────────────────────────
try:
    from modules.pluginkit.core import InspectionPlugin, register

    class MssqlJdbcPluginAdapter(InspectionPlugin):
        """SQL Server JDBC 插件适配器（实现标准接口）。"""

        def __init__(self, parse_func=None):
            self.id = 'sqlserver_jdbc'  # 与目录名 sqlserver_jdbc / plugin.json 逻辑 id 对齐
            self.name = 'SQL Server (JDBC)'  # 与 plugin.json 的 name 字段保持一致
            self.version = '1.0.0'
            self.db_types = ['sqlserver_jdbc', 'sqlserver']  # 同时支持两种 db_type
            self.author = 'DBCheck Team'
            self.description = 'Microsoft SQL Server 2017/2019/2022/2025 巡检插件（JDBC + JPype）'
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
            print("[MSSQL-JDBC] 开始初始化数据（模板 + 基线）...")
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
                )

                template_path = os.path.join(os.path.dirname(__file__), 'template_data.json')
                if not os.path.isfile(template_path):
                    print("[MSSQL-JDBC] 错误：未找到 template_data.json")
                    return

                with open(template_path, 'r', encoding='utf-8') as f:
                    template_data = json.load(f)

                # 1. 创建模板（幂等）
                existing_templates = get_templates_by_db_type('sqlserver_jdbc', db_path=db_path)
                if existing_templates:
                    template_id = existing_templates[0]['id']
                    print(f"[MSSQL-JDBC] 模板已存在，使用现有模板: {template_id}")
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
                    print(f"[MSSQL-JDBC] 创建模板: {template_id}")

                # 2. 创建章节和查询（幂等）
                chapters_data = template_data.get('chapters', [])
                print(f"[MSSQL-JDBC] 共有 {len(chapters_data)} 个章节")
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
                                print(f"[MSSQL-JDBC]   创建查询失败: {query_data['query_key']} - {e}")
                conn.close()

                # 3. 创建基线（从 baseline_data.json，幂等；P1 任务，本期无文件则跳过）
                baseline_path = os.path.join(os.path.dirname(__file__), 'baseline_data.json')
                existing_bl = get_baselines_by_db_type('sqlserver_jdbc', db_path=db_path)
                if not existing_bl and os.path.isfile(baseline_path):
                    with open(baseline_path, 'r', encoding='utf-8') as f:
                        baseline_data = json.load(f)
                    print(f"[MSSQL-JDBC] 共有 {len(baseline_data)} 条基线")
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
                                print(f"[MSSQL-JDBC]   创建基线失败: {bl['param_name']} - {e}")
                print("[MSSQL-JDBC] 数据初始化完成")
            except Exception as e:
                print(f"[MSSQL-JDBC] 数据初始化失败: {e}")
                traceback.print_exc()

        def on_uninstall(self, db_path: str = None):
            """插件卸载：清理 sqlserver_jdbc 的模板与基线数据。"""
            print("[MSSQL-JDBC] 开始清理数据...")
            try:
                from modules.inspection.dal import (
                    get_templates_by_db_type,
                    get_baselines_by_db_type,
                    delete_template,
                    delete_baseline,
                )
                templates = get_templates_by_db_type('sqlserver_jdbc')
                for t in templates:
                    try:
                        delete_template(t['id'], db_path=db_path)
                        print(f"[MSSQL-JDBC] 删除模板: {t.get('template_name_zh', t['id'])} (ID: {t['id']})")
                    except Exception as e:
                        print(f"[MSSQL-JDBC] 删除模板 {t['id']} 失败: {e}")

                baselines = get_baselines_by_db_type('sqlserver_jdbc')
                for b in baselines:
                    try:
                        delete_baseline(b['id'], db_path=db_path)
                    except Exception as e:
                        print(f"[MSSQL-JDBC] 删除基线 {b['id']} 失败: {e}")
                print("[MSSQL-JDBC] 数据清理完成")
            except Exception as e:
                print(f"[MSSQL-JDBC] 数据清理失败: {e}")

    adapter = MssqlJdbcPluginAdapter(parse_func=parse_connection_result)
    register(adapter)
    print("[MSSQL-JDBC] 插件注册成功")
except Exception as e:
    print(f"[MSSQL-JDBC] 插件注册失败: {e}")


if __name__ == '__main__':
    if len(sys.argv) > 2:
        ip = sys.argv[1]
        port = int(sys.argv[2])
        user = sys.argv[3] if len(sys.argv) > 3 else 'sa'
        password = sys.argv[4] if len(sys.argv) > 4 else 'password'
        database = sys.argv[5] if len(sys.argv) > 5 else 'master'
        ok, ver = test_connection(ip, port, user, password, database)
        print(("连接成功: %s" % ver) if ok else ("连接失败: %s" % ver))
