# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""ClickHouse JDBC 巡检插件 —— 通过 JPype + clickhouse-jdbc.jar + 共享 jdbc_jvm 连接 ClickHouse。

设计依据：docs/design/clickhouse-integration.md（JDBC 插件修订版）。
- 连接复用 plugins/available/clickhouse_jdbc/jdbc_jvm.py（JVM 单例 + classpath 合并）
- 连接配置复用 connection_config.ClickHouseConnectionConfig（jdbc_url 透传 + SSL + 自定义 HTTP 头）
- 数据采集直接跑 ClickHouse 系统表（system.replicas / system.parts / system.mutations /
  system.merges / system.disks / system.settings / system.query_log / system.clusters /
  system.macros 等），结果以 clickhouse_* list[dict] 存入 context，供 clickhouse.yaml
  规则与 inspection.db 模板章节使用。
- 智能分析接入铁律：get_task_config() 必须返回 'smart_analyze': 'smart_analyze_clickhouse'，
  且 smart_analyze_clickhouse 必须定义在核心 analyzer.py（web_ui L953 经 cfg['smart_analyze'] 解析）。
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
    sys.modules['connection_config']，导致相互污染（db2 插件同款修复）。
    改用 importlib 按绝对路径 + 唯一模块名加载，各插件各取所需。
    """
    spec = importlib.util.spec_from_file_location(
        "clickhouse_jdbc_connection_config",
        os.path.join(_PLUGIN_DIR, "connection_config.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 模块级绑定：connect() 内不再裸导入，避免被其它插件的同名模块污染。
_ClickHouseCC = _load_own_connection_config()
ClickHouseConnectionConfig = _ClickHouseCC.ClickHouseConnectionConfig


def _load_own_jdbc_jvm():
    """按文件绝对路径 + 唯一模块名加载本插件自有的 jdbc_jvm 模块。

    各插件的 jdbc_jvm.py 同名，裸 import 会被 sys.path 顺序影响（先加载的插件
    目录抢注 jdbc_jvm）。改用 importlib 按绝对路径 + 唯一模块名加载，与导入
    顺序无关，避免本插件误导入 db2 等其他插件的 jdbc_jvm。
    """
    spec = importlib.util.spec_from_file_location(
        "clickhouse_jdbc_jdbc_jvm",
        os.path.join(_PLUGIN_DIR, "jdbc_jvm.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["clickhouse_jdbc_jdbc_jvm"] = mod
    spec.loader.exec_module(mod)
    return mod


# 项目根目录：main_plugin.py -> clickhouse_jdbc -> available -> plugins -> root
_PROJECT_ROOT = os.path.abspath(os.path.join(_PLUGIN_DIR, "..", "..", ".."))

# BaseInspectionEngine 必须在模块级导入（类继承需要）
from modules.inspection.engine import (
    BaseInspectionEngine,
    LocalSystemInfoCollector,
    RemoteSystemInfoCollector,
    get_host_disk_usage,
)
from modules.inspection.chapter_filter import build_chapter_filter_sql  # 章节可选：共享过滤 helper

# 注册 java 模块导入钩子，使 `from java.x import` 可用（兼容 JPype 全版本）。
# 仅注册钩子，不会启动 JVM；本插件须自包含，不依赖全局 import jpype.imports。
import jpype.imports  # noqa: E402  (必须晚于 inspection_engine 导入，注册 java 钩子)


# ── JDBC 连接包装器（兼容 Python DB-API 2.0）────────────────────────
# 直接照搬 oracle_jdbc / db2_jdbc 实现的独立副本（避免跨插件导入路径问题）。
class JdbcCursorWrapper:
    """包装 JDBC Statement/ResultSet，提供类似 Python DB-API 的 cursor 接口"""

    def __init__(self, connection):
        self.conn = connection
        self.stmt = connection.createStatement()
        self.rs = None
        self.description = None
        self._rowcount = -1

    @staticmethod
    def _is_query(sql: str) -> bool:
        """判断 SQL 是否为查询（返回结果集）。

        ClickHouse 中 SELECT / WITH / SHOW / DESCRIBE / EXPLAIN / OPTIMIZE
        均返回结果集；INSERT / CREATE / ALTER / DROP / SYSTEM 等为更新语句。
        """
        s = sql.strip().upper()
        for kw in ("SELECT", "WITH", "SHOW", "DESCRIBE", "EXPLAIN", "OPTIMIZE"):
            if s.startswith(kw):
                return True
        return False

    def execute(self, sql):
        """执行 SQL（自动判断查询/更新）"""
        if self._is_query(sql):
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


# ── ClickHouse JDBC 巡检器 ───────────────────────────────────────────
class ClickHouseJdbcInspector(BaseInspectionEngine):
    """ClickHouse JDBC 巡检器。

    继承 BaseInspectionEngine，覆盖 connect() / collect_data()，直接跑 ClickHouse
    系统表填充 clickhouse_* context（对应 design §3.2）。
    """

    def __init__(self, host, port=8123, user='default', password='', database='',
                 ssh_info=None, template_id=None, jdbc_url=None, ssl=False,
                 custom_http_headers=None, driver_version=''):
        super().__init__(host, int(port), user, password, database=database,
                         ssh_info=ssh_info, template_id=template_id)
        self.db_type = 'clickhouse'
        self.jdbc_url = jdbc_url
        self.ssl = bool(ssl)
        self.custom_http_headers = dict(custom_http_headers or {})
        self.conn = None
        self.cursor = None
        self.raw_jdbc_conn = None
        self.conn_cfg = None
        self._ssh_tunnel = None
        self._db_version_str = 'unknown'
        self.driver_version = driver_version or ''
        self.jdbc_driver_path = None
        try:
            from modules import driver_registry as _dr
            _jars = _dr.resolve_jdbc_driver_jars('clickhouse', self.driver_version)
            if _jars:
                self.jdbc_driver_path = _jars
        except Exception:
            self.jdbc_driver_path = None

    # ════════════════════════════════════════════════
    # 连接层
    # ════════════════════════════════════════════════
    def connect(self) -> Tuple[bool, str]:
        """连接 ClickHouse 数据库（统一连接层 JPype 模式，JDBC-over-HTTP）。

        Returns:
            (ok, msg)：ok 为 True 时 msg 是版本可读串；
                          ok 为 False 时 msg 是错误信息。
        """
        try:
            # 1. 构建连接配置（ClickHouseConnectionConfig 已在模块级按路径绑定；
            #    build_jdbc_url/build_properties 均委托统一层）
            cfg = ClickHouseConnectionConfig(
                host=self.host,
                port=int(self.port),
                user=self.user,
                password=self.password,
                database=self.database or '',
                jdbc_url=self.jdbc_url or '',
                ssl=self.ssl,
                custom_http_headers=self.custom_http_headers,
            )

            # 2. 可选 SSH 隧道：把连接目标改写为 127.0.0.1:<local_port>（决策 ③）
            tunnel_cfg = cfg
            if getattr(self, 'ssh_info', None) and self.ssh_info.get('ssh_host'):
                try:
                    from modules.ssh import SSHTunnel
                    tunnel = SSHTunnel(
                        ssh_host=self.ssh_info['ssh_host'],
                        ssh_port=int(self.ssh_info.get('ssh_port', 22)),
                        ssh_user=self.ssh_info.get('ssh_user', 'root'),
                        ssh_password=self.ssh_info.get('ssh_password', ''),
                        ssh_key=self.ssh_info.get('ssh_key_file', ''),
                        remote_host=self.host,
                        remote_port=int(self.port),
                    )
                    tunnel.__enter__()
                    self._ssh_tunnel = tunnel
                    local_port = tunnel.local_port
                    # 经隧道时重建配置：host=127.0.0.1，port=local_port，jdbc_url 清空以重建
                    tunnel_cfg = ClickHouseConnectionConfig(
                        host='127.0.0.1',
                        port=int(local_port),
                        user=cfg.user,
                        password=cfg.password,
                        database=cfg.database,
                        jdbc_url='',
                        ssl=cfg.ssl,
                        custom_http_headers=cfg.custom_http_headers,
                    )
                    print(f"[ClickHouse] 已建立 SSH 隧道：127.0.0.1:{local_port} -> "
                          f"{self.host}:{self.port}")
                except Exception as e:
                    print(f"[ClickHouse] SSH 隧道建立失败，回退直连：{e}")

            self.conn_cfg = tunnel_cfg

            # 3. 统一连接层：驱动解析 / JVM 启动 / URL 构造 / props 装配 / 建连。
            #    认证映射（user/password -> X-ClickHouse-User/X-ClickHouse-Key）、
            #    socket_timeout、ssl、自定义 HTTP 头均经 properties 透传。
            from modules.jdbc_connector import open_jdbc_connection
            conn, meta = open_jdbc_connection(
                'clickhouse', tunnel_cfg.host, tunnel_cfg.port,
                user=tunnel_cfg.user, password=tunnel_cfg.password,
                driver_version=self.driver_version,
                database=tunnel_cfg.database,
                jdbc_url=tunnel_cfg.jdbc_url or '',
                mode='jpype',
                properties=tunnel_cfg.build_properties(),
            )
            if conn is None:
                return False, meta['error']

            self.raw_jdbc_conn = conn
            self.conn = JdbcConnectionWrapper(conn)
            self.cursor = self.conn.cursor()

            # 4. 读取版本
            self.cursor.execute("SELECT version()")
            row = self.cursor.fetchone()
            version = str(row[0]) if row else 'unknown'
            self._db_version_str = version
            self.context['clickhouse_version'] = version
            self.context['version'] = [{'VERSION': version, 'VERSION_STR': version}]

            print(f"[ClickHouse] 连接成功，版本: {self._db_version_str}")
            return True, self._db_version_str
        except Exception as e:
            print(f"[ClickHouse] 连接失败: {e}")
            traceback.print_exc()
            return False, str(e)

    def disconnect(self):
        """关闭数据库连接（含 SSH 隧道）"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
        except Exception as e:
            print(f"[ClickHouse] 关闭连接失败: {e}")
        try:
            if self._ssh_tunnel is not None:
                self._ssh_tunnel.__exit__(None, None, None)
                self._ssh_tunnel = None
        except Exception:
            pass

    def get_template_id(self):
        """返回 inspection_template 表的 template_id。"""
        try:
            from modules.inspection.dal import get_templates_by_db_type
            templates = get_templates_by_db_type("clickhouse")
            return templates[0]['id'] if templates else None
        except Exception as e:
            print(f"[ClickHouse] 获取模板 ID 失败: {e}")
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
    # 数据采集（design §3.2 的 clickhouse_* 字段）
    # ════════════════════════════════════════════════
    def _collect_version(self):
        try:
            self.cursor.execute("SELECT version()")
            row = self.cursor.fetchone()
            v = str(row[0]) if row else 'unknown'
            self.context['clickhouse_version'] = v
            self.context['version'] = [{'VERSION': v, 'VERSION_STR': v}]
        except Exception as e:
            self.context['clickhouse_version'] = 'unknown'
            self.context['clickhouse_version_error'] = str(e)[:200]

    def _collect_replicas(self):
        # 复制健康：只读/延迟/未来 part/队列深度（决策 ④ 集群复制）
        self.context['clickhouse_replicas'] = self._exec_to_dicts(
            "SELECT database, table, is_readonly, absolute_delay, future_parts, "
            "queue_size, inserts_in_queue, merges_in_queue, log_max_index, "
            "log_pointer, total_replicas, active_replicas "
            "FROM system.replicas"
        )

    def _collect_parts(self):
        # parts 爆炸检测：按库/表聚合 part 数与字节（仅 active part）
        self.context['clickhouse_parts'] = self._exec_to_dicts(
            "SELECT database, table, count() AS part_count, "
            "sum(bytes) AS total_bytes, sum(rows) AS total_rows "
            "FROM system.parts WHERE active GROUP BY database, table "
            "ORDER BY part_count DESC LIMIT 500"
        )

    def _collect_merges(self):
        # 后台合并压力
        self.context['clickhouse_merges'] = self._exec_to_dicts(
            "SELECT database, table, elapsed, progress, num_parts, "
            "rows_read, rows_written, memory_usage "
            "FROM system.merges"
        )

    def _collect_mutations(self):
        # mutations 卡住检测：未完成（is_done=0）或最近失败
        self.context['clickhouse_mutations'] = self._exec_to_dicts(
            "SELECT database, table, mutation_id, command, create_time, "
            "parts_to_do, is_done, latest_failed_part, latest_fail_reason "
            "FROM system.mutations WHERE is_done = 0"
        )

    def _collect_disks(self):
        # 磁盘使用率
        self.context['clickhouse_disks'] = self._exec_to_dicts(
            "SELECT name, path, free_space, total_space, "
            "if(total_space > 0, (total_space - free_space) * 100.0 / total_space, 0) AS used_pct "
            "FROM system.disks"
        )

    def _collect_storage_policies(self):
        self.context['clickhouse_storage_policies'] = self._exec_to_dicts(
            "SELECT policy_name, volume_name, disks, max_data_part_size, "
            "move_factor FROM system.storage_policies"
        )

    def _collect_settings(self):
        # 关键参数（内存/并发/执行/合并相关），供基线/规则引用
        names = [
            "max_memory_usage", "max_server_memory_usage", "max_concurrent_queries",
            "background_pool_size", "background_merges_mutations_concurrency_ratio",
            "max_partitions_per_insert_block", "max_insert_block_size",
            "max_execution_time", "max_rows_to_read", "max_bytes_to_read",
            "max_memory_usage_for_user", "max_server_memory_usage_to_ram_ratio",
            "background_pool_size",
        ]
        placeholders = ",".join("'%s'" % n for n in names)
        rows = self._exec_to_dicts(
            "SELECT name, value, changed FROM system.settings "
            "WHERE name IN (%s)" % placeholders
        )
        settings_map = {}
        for r in rows:
            if isinstance(r, dict):
                settings_map[str(r.get('name', '')).upper()] = r.get('value')
        self.context['clickhouse_settings'] = settings_map

    def _collect_query_log_stats(self):
        # 异常/慢查询聚合（近 24 小时）
        try:
            exception_count = self._exec_to_dicts(
                "SELECT count() AS c FROM system.query_log "
                "WHERE event_time > now() - INTERVAL 24 HOUR AND type = 'Exception'"
            )
            slow_count = self._exec_to_dicts(
                "SELECT count() AS c FROM system.query_log "
                "WHERE event_time > now() - INTERVAL 24 HOUR "
                "AND type = 'QueryFinish' AND query_duration_ms > 5000"
            )
            top_exceptions = self._exec_to_dicts(
                "SELECT exception_code, substring(exception, 1, 200) AS msg, count() AS c "
                "FROM system.query_log "
                "WHERE event_time > now() - INTERVAL 24 HOUR AND type = 'Exception' "
                "GROUP BY exception_code, msg ORDER BY c DESC LIMIT 10"
            )
            ec = exception_count[0].get('c') if exception_count else 0
            sc = slow_count[0].get('c') if slow_count else 0
            self.context['clickhouse_query_log_stats'] = {
                'exception_count': _to_num(ec),
                'slow_query_count': _to_num(sc),
                'top_exceptions': top_exceptions or [],
            }
        except Exception as e:
            self.context['clickhouse_query_log_stats'] = {
                'exception_count': 0, 'slow_query_count': 0, 'top_exceptions': [],
                'error': str(e)[:200],
            }

    def _collect_cluster(self):
        # 集群拓扑（决策 ④）
        self.context['clickhouse_cluster'] = self._exec_to_dicts(
            "SELECT cluster, shard_num, replica_num, host_name, port, is_local "
            "FROM system.clusters ORDER BY cluster, shard_num, replica_num"
        )

    def _collect_macros(self):
        # 集群分片宏
        self.context['clickhouse_macros'] = self._exec_to_dicts(
            "SELECT macro, substitution FROM system.macros"
        )

    def _collect_metrics(self):
        # 运行时指标
        metrics = self._exec_to_dicts(
            "SELECT metric, value FROM system.metrics"
        )
        async_metrics = self._exec_to_dicts(
            "SELECT metric, value FROM system.asynchronous_metrics"
        )
        m_map = {}
        for r in (metrics or []) + (async_metrics or []):
            if isinstance(r, dict):
                m_map[str(r.get('metric', ''))] = r.get('value')
        self.context['clickhouse_metrics'] = m_map

    def _collect_tables(self):
        # 表规模（排除系统库）
        self.context['clickhouse_tables'] = self._exec_to_dicts(
            "SELECT database, name, engine, total_rows, total_bytes "
            "FROM system.tables WHERE database NOT IN ('system') "
            "ORDER BY total_bytes DESC NULLS LAST LIMIT 200"
        )

    def _collect_databases(self):
        try:
            self.cursor.execute("SHOW DATABASES")
            rows = self.cursor.fetchall()
            dbs = [str(r[0]) for r in rows if r]
            self.context['clickhouse_databases'] = dbs
        except Exception as e:
            self.context['clickhouse_databases'] = [{'ERROR': str(e)[:200]}]

    # ════════════════════════════════════════════════
    # 规则标量派生（供 clickhouse.yaml 条件引用）
    # ════════════════════════════════════════════════
    def _build_rule_scalars(self):
        """把 §3.2 的 clickhouse_* list[dict] 汇总成规则引擎可直接引用的标量 / 字典，
        供 pro/rules/builtin/clickhouse.yaml 的 condition 使用。

        所有派生值都做防御式处理：列表可能被 {ERROR:...} 占用、字段大小写
        不一致、JDBC 返回的 java 数值类型等，均不抛异常。
        """
        # 参数名大写 -> 值 映射
        settings = self.context.get('clickhouse_settings') or {}
        if isinstance(settings, dict):
            self.context['clickhouse_settings_map'] = {str(k).upper(): v for k, v in settings.items()}
        else:
            self.context['clickhouse_settings_map'] = {}

        # parts 最大 part 数
        parts_max = 0
        for r in self.context.get('clickhouse_parts') or []:
            if not isinstance(r, dict):
                continue
            try:
                parts_max = max(parts_max, int(r.get('part_count', 0) or 0))
            except (TypeError, ValueError):
                pass
        self.context['clickhouse_parts_max_count'] = parts_max

        # future parts 总数（复制积压）
        future_total = 0
        for r in self.context.get('clickhouse_replicas') or []:
            if not isinstance(r, dict):
                continue
            try:
                future_total += int(r.get('future_parts', 0) or 0)
            except (TypeError, ValueError):
                pass
        self.context['clickhouse_future_parts_total'] = future_total

        # 复制最大延迟（秒）
        max_delay = 0
        for r in self.context.get('clickhouse_replicas') or []:
            if not isinstance(r, dict):
                continue
            try:
                max_delay = max(max_delay, float(r.get('absolute_delay', 0) or 0))
            except (TypeError, ValueError):
                pass
        self.context['clickhouse_replicas_max_delay'] = max_delay

        # 只读副本数（复制异常）
        readonly_cnt = 0
        for r in self.context.get('clickhouse_replicas') or []:
            if not isinstance(r, dict):
                continue
            if str(r.get('is_readonly', 0)) in ('1', 'true', 'True', 'TRUE'):
                readonly_cnt += 1
        self.context['clickhouse_replicas_readonly_count'] = readonly_cnt

        # mutations 卡住数（未完成）
        stuck = 0
        for r in self.context.get('clickhouse_mutations') or []:
            if not isinstance(r, dict):
                continue
            stuck += 1
        self.context['clickhouse_mutations_stuck_count'] = stuck

        # 磁盘最大使用率(%)
        disk_max = 0.0
        for r in self.context.get('clickhouse_disks') or []:
            if not isinstance(r, dict):
                continue
            try:
                disk_max = max(disk_max, float(r.get('used_pct', 0) or 0))
            except (TypeError, ValueError):
                pass
        self.context['clickhouse_disks_max_used_pct'] = disk_max

        # 异常 / 慢查询计数
        qls = self.context.get('clickhouse_query_log_stats') or {}
        self.context['clickhouse_exception_count'] = _to_num(qls.get('exception_count', 0))
        self.context['clickhouse_slow_query_count'] = _to_num(qls.get('slow_query_count', 0))

        # 后台合并数（合并压力）
        self.context['clickhouse_merges_active_count'] = len(
            [r for r in self.context.get('clickhouse_merges') or [] if isinstance(r, dict)]
        )

        # 表数量
        self.context['clickhouse_tables_count'] = len(
            [r for r in self.context.get('clickhouse_tables') or [] if isinstance(r, dict)]
        )

        # 集群分片数（去重 cluster+shard）
        shards = set()
        for r in self.context.get('clickhouse_cluster') or []:
            if isinstance(r, dict):
                shards.add((r.get('cluster'), r.get('shard_num')))
        self.context['clickhouse_shard_count'] = len(shards)

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

        # 执行每个模板 query（已由 _collect_* 填充的键跳过）
        for ch in chapters:
            for q in ch['queries']:
                key = q['query_key']
                existing = self.context.get(key)
                if isinstance(existing, list) and existing and isinstance(existing[0], dict):
                    continue  # 已是可渲染的 list[dict]，跳过重复执行（避免字符串/字典值被误判为“数据缺失”）
                try:
                    self.context[key] = self._exec_to_dicts(q['query_sql'])
                except Exception as e:
                    self.context[key] = [{'ERROR': str(e)[:200]}]

    # ════════════════════════════════════════════════
    # 主采集入口
    # ════════════════════════════════════════════════
    def collect_data(self, sql_templates: str = ''):
        """采集 ClickHouse 数据（覆盖父类）。

        流程：connect → 逐 _collect_* 填充 clickhouse_* context（规则源）→ 加载章节并执行
        模板 query（报告源）→ 派生规则标量 → 系统资源 → 基线检查 → 智能分析。
        任何子步骤异常均被吞掉，整体保证「巡检无错」。

        Returns:
            成功返回 self.context(dict)，失败返回 (False, error_msg)。
        """
        print("\n[ClickHouse] 开始采集数据...")
        ok, version = self.connect()
        if not ok:
            return False, version

        self.context['version'] = [{'VERSION': version}]
        self.context['db_type'] = 'clickhouse'

        # §3.2 直接采集（规则与基线数据源）
        methods = [
            '_collect_version', '_collect_replicas', '_collect_parts', '_collect_merges',
            '_collect_mutations', '_collect_disks', '_collect_storage_policies',
            '_collect_settings', '_collect_query_log_stats', '_collect_cluster',
            '_collect_macros', '_collect_metrics', '_collect_tables', '_collect_databases',
        ]
        for i, m in enumerate(methods):
            try:
                self.print_progress_bar(i + 1, len(methods), prefix='[ClickHouse]',
                                        suffix=f'{m} ({i+1}/{len(methods)})')
                getattr(self, m)()
            except Exception as e:
                key = '_' + m.split('_collect_')[-1]
                self.context[key] = [{'ERROR': str(e)[:200]}]

        # 汇总 §3.2 的 clickhouse_* list[dict] 为规则引擎标量（供 clickhouse.yaml 条件引用）
        try:
            self._build_rule_scalars()
        except Exception as e:
            print(f"[ClickHouse] 构建规则标量失败: {e}")

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
            print(f"[ClickHouse] 系统信息采集失败: {e}")
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
            print(f"[ClickHouse] 加载章节失败: {e}")
            self.context['_chapters'] = []

        # 基线检查（ClickHouse 基线已注册到 inspection.db）
        try:
            self._check_baselines()
        except Exception as e:
            print(f"[ClickHouse] 基线检查失败: {e}")
            self.context['baseline_results'] = []

        # 智能分析（clickhouse 规则，异常降级空列表）
        try:
            from modules.inspection.analyzer import smart_analyze_clickhouse
            self.context['auto_analyze'] = smart_analyze_clickhouse(self.context)
        except Exception as e:
            print(f"[ClickHouse] 智能分析失败: {e}")
            self.context['auto_analyze'] = []

        print(f"[ClickHouse] 数据采集完成，context keys: {list(self.context.keys())}")

        # AI 诊断（统一接口）
        from modules.inspection.analyzer import run_ai_diagnosis
        self.context['ai_advice'] = run_ai_diagnosis(
            self.db_type,
            getattr(self, 'host', '') or self.db_type,
            self.context,
            lang=getattr(self, '_lang', 'zh'),
            timeout=600,
        )
        return self.context


# ── 工具函数 ────────────────────────────────────────────────────────
def _to_num(v: Any, default: float = 0) -> float:
    """安全转为数值（ClickHouse/JDBC 返回可能是 Decimal/int/str）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _java_properties(py_dict: Dict[str, str]):
    """将 Python dict 转为 java.util.Properties。"""
    from java.util import Properties
    props = Properties()
    for k, v in py_dict.items():
        props.setProperty(str(k), str(v))
    return props


# ── 测试连接函数（供 web_ui / 自测调用）────────────────────────────
def test_connection(host, port, user, password, database='', jdbc_url=None,
                    ssl=False, custom_http_headers=None,
                    driver_version='', **kwargs):
    """测试 ClickHouse JDBC 连接。

    Args:
        host: ClickHouse 服务器地址
        port: 端口（默认 8123）
        user: 用户名（默认 default）
        password: 密码
        database: 目标数据库名（可选）
        jdbc_url: 完整 JDBC URL（可选，以 jdbc:clickhouse 开头则透传）
        ssl: 是否启用 SSL/TLS
        custom_http_headers: 自定义 HTTP 头（决策 ⑨）
    Returns:
        (ok, msg)
    """
    try:
        inspector = ClickHouseJdbcInspector(
            host, int(port), user, password,
            database=database, jdbc_url=jdbc_url, ssl=ssl,
            custom_http_headers=custom_http_headers,
            driver_version=driver_version)
        ok, msg = inspector.connect()
        inspector.disconnect()
        return ok, msg
    except Exception as e:
        return False, str(e)


# ── 实时监控连接工厂（供 pro/metrics_collector.py 使用）─────────────
def get_connection(host, port, user, password, database='', jdbc_url=None,
                  ssl=False, custom_http_headers=None, driver_version=''):
    """返回 DB-API 2.0 兼容的 JDBC 连接包装（JdbcConnectionWrapper）。

    Raises:
        RuntimeError: 连接失败时抛出。
    """
    inspector = ClickHouseJdbcInspector(
        host, int(port), user, password,
        database=database, jdbc_url=jdbc_url, ssl=ssl,
        custom_http_headers=custom_http_headers,
        driver_version=driver_version)
    ok, msg = inspector.connect()
    if not ok:
        raise RuntimeError('ClickHouse JDBC 连接失败: %s' % msg)
    return inspector.conn


# ── 数据源获取函数（供 web_ui.py 使用）─────────────────────────────
def getData(ip, port, user, password, ssh_info=None, template_id=None,
            driver_version=''):
    """获取 ClickHouse 数据源。

    返回 CompatWrapper 对象，web_ui 通过 wrapper.checkdb('builtin')
    触发采集并获取 context。

    Returns:
        CompatWrapper 对象；失败返回 None。
    """
    ssh_info = ssh_info or {}
    database = ssh_info.get('database', '')
    jdbc_url = ssh_info.get('jdbc_url')
    ssl = bool(ssh_info.get('ssl', False))
    custom_http_headers = ssh_info.get('custom_http_headers') or {}

    inspector = ClickHouseJdbcInspector(
        ip, int(port), user, password,
        database=database, jdbc_url=jdbc_url, ssl=ssl,
        custom_http_headers=custom_http_headers,
        driver_version=driver_version,
        ssh_info=ssh_info, template_id=template_id)
    ok, msg = inspector.connect()
    if not ok:
        print(f"[ClickHouse] 连接失败: {msg}")
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
    """插件连接测试入口（供 web_ui 经 get_task_config 调用）。"""
    info = info or {}
    return test_connection(
        info.get('ip', info.get('host', '')),
        int(info.get('port', 8123) or 8123),
        info.get('user', 'default'),
        info.get('password', ''),
        database=info.get('database', ''),
        jdbc_url=info.get('jdbc_url'),
        ssl=bool(info.get('ssl', False)),
        custom_http_headers=info.get('custom_http_headers'),
    )


def parse_connection_result(ok: bool, msg: Any) -> Dict[str, Any]:
    """解析 ClickHouse JDBC 连接测试结果（供 web_ui.py 动态调用）。"""
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
            [info.get('ip', ''), int(info.get('port', 8123) or 8123),
             info.get('user', 'default'), info.get('password', '')],
            {'ssh_info': {
                 'ssh_host': info.get('ssh_host', ''),
                 'ssh_port': info.get('ssh_port', 22),
                 'ssh_user': info.get('ssh_user', ''),
                 'ssh_password': info.get('ssh_password', ''),
                 'ssh_key_file': info.get('ssh_key_file', ''),
                 'database': info.get('database', ''),
                 'jdbc_url': info.get('jdbc_url', ''),
                 'ssl': bool(info.get('ssl', False)),
                 'custom_http_headers': info.get('custom_http_headers', {}),
             }, 'template_id': info.get('template_id'),
             'driver_version': info.get('driver_version', '')}
        ),
        'conn_attr': '',  # getData 返回 CompatWrapper，跳过 conn_attr 检查
        'filename_key': 'webui.clickhouse_jdbc_report_filename',
        'history_db_type': 'clickhouse',
        'instance_prefix': 'clickhouse',
        'error_task_name': 'ClickHouse',
        'log_start_key': 'webui.log_clickhouse_jdbc_start',
        'err_module_key': 'webui.err_clickhouse_jdbc_module',
        'label_default': 'ClickHouse',
        'db_name_default': '',  # database 可选
        'smart_analyze': 'smart_analyze_clickhouse',  # ← 智能分析接入铁律
    }


# ── 注册插件（无侵入式架构）──────────────────────────────────────────
try:
    from modules.pluginkit.core import InspectionPlugin, register

    class ClickHouseJdbcPluginAdapter(InspectionPlugin):
        """ClickHouse JDBC 插件适配器（实现标准接口）。"""

        def __init__(self, parse_func=None):
            self.id = 'clickhouse_jdbc'  # = 目录名，避免幽灵记录（db2 教训）
            self.name = 'ClickHouse (JDBC)'  # 与 plugin.json 的 name 字段保持一致
            self.version = '1.0.0'
            self.db_types = ['clickhouse']
            self.author = 'DBCheck Team'
            self.description = 'ClickHouse 列式 OLAP 巡检插件（JDBC + JPype，JDBC-over-HTTP 8123）'
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
            print("[ClickHouse] 开始初始化数据（模板 + 基线）...")
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
                    print("[ClickHouse] 错误：未找到 template_data.json")
                    return

                with open(template_path, 'r', encoding='utf-8') as f:
                    template_data = json.load(f)

                # 1. 创建模板（幂等）
                existing_templates = get_templates_by_db_type('clickhouse', db_path=db_path)
                if existing_templates:
                    template_id = existing_templates[0]['id']
                    print(f"[ClickHouse] 模板已存在，使用现有模板: {template_id}")
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
                    print(f"[ClickHouse] 创建模板: {template_id}")

                # 2. 创建章节和查询（幂等）
                chapters_data = template_data.get('chapters', [])
                print(f"[ClickHouse] 共有 {len(chapters_data)} 个章节")
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
                                print(f"[ClickHouse]   创建查询失败: {query_data['query_key']} - {e}")
                conn.close()

                # 3. 创建基线（从 baseline_data.json，幂等）
                baseline_path = os.path.join(os.path.dirname(__file__), 'baseline_data.json')
                existing_bl = get_baselines_by_db_type('clickhouse', db_path=db_path)
                if not existing_bl and os.path.isfile(baseline_path):
                    with open(baseline_path, 'r', encoding='utf-8') as f:
                        baseline_data = json.load(f)
                    print(f"[ClickHouse] 共有 {len(baseline_data)} 条基线")
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
                                print(f"[ClickHouse]   创建基线失败: {bl['param_name']} - {e}")
                print("[ClickHouse] 数据初始化完成")
            except Exception as e:
                print(f"[ClickHouse] 数据初始化失败: {e}")
                traceback.print_exc()

        def on_uninstall(self, db_path: str = None):
            """插件卸载：清理 clickhouse 的模板与基线数据。"""
            print("[ClickHouse] 开始清理数据...")
            try:
                from modules.inspection.dal import (
                    get_templates_by_db_type,
                    get_baselines_by_db_type,
                    delete_template,
                    delete_baseline,
                )
                templates = get_templates_by_db_type('clickhouse')
                for t in templates:
                    try:
                        delete_template(t['id'], db_path=db_path)
                        print(f"[ClickHouse] 删除模板: {t.get('template_name_zh', t['id'])} (ID: {t['id']})")
                    except Exception as e:
                        print(f"[ClickHouse] 删除模板 {t['id']} 失败: {e}")

                baselines = get_baselines_by_db_type('clickhouse')
                for b in baselines:
                    try:
                        delete_baseline(b['id'], db_path=db_path)
                    except Exception as e:
                        print(f"[ClickHouse] 删除基线 {b['id']} 失败: {e}")
                print("[ClickHouse] 数据清理完成")
            except Exception as e:
                print(f"[ClickHouse] 数据清理失败: {e}")

    adapter = ClickHouseJdbcPluginAdapter(parse_func=parse_connection_result)
    register(adapter)
    print("[ClickHouse] 插件注册成功")
except Exception as e:
    print(f"[ClickHouse] 插件注册失败: {e}")


if __name__ == '__main__':
    if len(sys.argv) > 2:
        ip = sys.argv[1]
        port = int(sys.argv[2])
        user = sys.argv[3] if len(sys.argv) > 3 else 'default'
        password = sys.argv[4] if len(sys.argv) > 4 else 'password'
        database = sys.argv[5] if len(sys.argv) > 5 else ''
        ok, ver = test_connection(ip, port, user, password, database)
        print(("连接成功: %s" % ver) if ok else ("连接失败: %s" % ver))
