# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
监控大屏指标适配层 — DBA 视角六大域 → 统一 MetricKey

设计（docs/design/dashboard-redesign.md）：
- 指标域：可用性 / 连接会话 / 吞吐(QPS·TPS) / 容量空间 / 日志错误 / 复制 HA
- 各库 SQL 按 family 分发（mysql-family / pg-family / oracle / sqlserver / dm 达梦）；
  pg-family 覆盖 kingbase/hgdb/uxdb/vastbase/ivorysql，tidb/oceanbase 走 mysql-family
  （主从查询在 TiDB/OB 上失败自动降级 None）；mongodb/redis/clickhouse 等非 SQL 库
  只出连接数/慢查询（复用 MonitorEngine），无增量指标
- 连接数/慢查询复用 MonitorEngine 已有采集（不重复连接）；增量指标（QPS/TPS/缓存命中）
  由 ScreenCollector 周期采样差值计算
- 单条 SQL 失败只降级该指标（None），不影响其它指标与实例状态判定；
  仅"探针连不上"（engine 侧 error）才判 down

输出统一模型：
    {iid: {'name','db_type','group','host','port','label','status','ts','err',
           'conn':{'total','max_conn','usage_pct','active','idle','blocked'},
           'slowq': int, 'qps': float, 'tps': float, 'cache_hit_pct': float,
           'lock_waits': int, 'repl_lag_s': int,
           'tbs': [{'name','total_mb','free_mb','free_pct'}],
           'spark': [qps...]}   # 最近 N 个采样点的 QPS（迷你趋势）
    }
"""

import time
import json
import os
import threading
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from modules.monitor.queries import normalize_db_type
from modules.monitor import history_store  # 历史回放存储层（P2-4，仅非敏感白名单字段落库）
from modules.monitor.native_collect import NATIVE_DB_TYPES, collect_native

# ═══════════════════════════════════════════════════════════
#  db_type → family 归并
# ═══════════════════════════════════════════════════════════

_DB_FAMILY = {
    'mysql': 'mysql', 'mariadb': 'mysql', 'oceanbase': 'mysql', 'tidb': 'mysql',
    'percona': 'mysql',
    'postgresql': 'pg', 'pg': 'pg', 'ivorysql': 'pg',
    'oracle': 'oracle',
    'sqlserver': 'sqlserver', 'mssql': 'sqlserver',
    'dm': 'dm', 'kingbase': 'pg', 'hgdb': 'pg', 'uxdb': 'pg', 'vastbase': 'pg',
    'halodb': 'pg',
    'gbase': 'gbase', 'db2': 'db2', 'clickhouse': 'clickhouse',
}


def db_family(db_type):
    # 先过 queries.normalize_db_type（oracle_jdbc→oracle、hgdb→pg 等），
    # 保证插件命名（_jdbc 后缀）与国产库标识都能命中 family。
    return _DB_FAMILY.get(normalize_db_type(db_type))


# ═══════════════════════════════════════════════════════════
#  SQL 模板（按 family）
# ═══════════════════════════════════════════════════════════

MYSQL_STAT_SQL = (
    "SHOW GLOBAL STATUS WHERE Variable_name IN "
    "('Questions','Com_commit','Com_rollback','Uptime',"
    "'Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads','Aborted_connects')"
)

# 库/Schema 容量 Top（information_schema 全库聚合，权限要求低）
MYSQL_TBS_SQL = (
    "SELECT table_schema AS name, "
    "ROUND(SUM(data_length + index_length) / 1048576, 1) AS total_mb "
    "FROM information_schema.tables GROUP BY table_schema "
    "ORDER BY total_mb DESC LIMIT 10"
)

# 复制延迟（主从/级联；列名随版本可能是 Seconds_Behind_Master）
MYSQL_REPL_SQL = "SHOW SLAVE STATUS"
MYSQL_REPL_SQL8 = "SHOW REPLICA STATUS"

# ── OceanBase（MySQL 租户）专属 ──
# OB 的 SHOW GLOBAL STATUS 不提供 Queries / Com_commit / Com_rollback / Innodb_*
# 计数器，QPS/TPS/缓存命中必须走 OB 原生 v$sysstat。stat_id 跨版本稳定，
# 口径与 OCP 官方监控指标一致：
#   QPS = 40000 select + 40002 insert + 40004 replace + 40006 update + 40008 delete
#   TPS = 30005 transaction_count
#   缓存命中 = 50008 block cache hit / 50009 block cache miss
OB_QPS_STAT_IDS = (40000, 40002, 40004, 40006, 40008)
OB_TPS_STAT_ID = 30005
OB_CACHE_HIT_STAT_ID = 50008
OB_CACHE_MISS_STAT_ID = 50009
OB_STAT_SQL = (
    "SELECT stat_id, SUM(value) AS value FROM gv$sysstat "
    "WHERE stat_id IN (40000,40002,40004,40006,40008,30005,50008,50009) "
    "AND class < 1000 GROUP BY stat_id"
)
# gv$ 不可达（低权限/老版本）时退回本机 v$：仅统计当前 observer，口径略窄但可用
OB_STAT_SQL_LOCAL = (
    "SELECT stat_id, SUM(value) AS value FROM v$sysstat "
    "WHERE stat_id IN (40000,40002,40004,40006,40008,30005,50008,50009) "
    "AND class < 1000 GROUP BY stat_id"
)
# OB 租户 information_schema.tables 的 data_length/index_length 在未 ANALYZE 前
# 常为 NULL，SUM 全 NULL 会把 total_mb 变 None（前端显示 0），COALESCE 加固
OB_TBS_SQL = (
    "SELECT table_schema AS name, "
    "ROUND(SUM(COALESCE(data_length, 0) + COALESCE(index_length, 0)) / 1048576, 1) AS total_mb "
    "FROM information_schema.tables GROUP BY table_schema "
    "ORDER BY total_mb DESC LIMIT 10"
)

PG_STAT_SQL = (
    "SELECT "
    "(SELECT COALESCE(SUM(xact_commit), 0) FROM pg_stat_database) AS xact_commit, "
    "(SELECT COALESCE(SUM(xact_rollback), 0) FROM pg_stat_database) AS xact_rollback, "
    "(SELECT COALESCE(SUM(blks_hit), 0) FROM pg_stat_database) AS blks_hit, "
    "(SELECT COALESCE(SUM(blks_read), 0) FROM pg_stat_database) AS blks_read"
)

PG_TBS_SQL = (
    "SELECT datname AS name, ROUND(pg_database_size(datname) / 1048576.0, 1) AS total_mb "
    "FROM pg_database WHERE datallowconn = true "
    "ORDER BY total_mb DESC LIMIT 10"
)

PG_REPL_SQL = (
    "SELECT application_name, state, "
    "COALESCE(ROUND(EXTRACT(EPOCH FROM replay_lag))::int, 0) AS lag_s "
    "FROM pg_stat_replication"
)

PG_LOCKS_SQL = "SELECT count(*) AS n FROM pg_locks WHERE NOT granted"

ORACLE_STAT_SQL = (
    "SELECT name, value FROM v$sysstat WHERE name IN "
    "('execute count','user commits','user rollbacks',"
    "'consistent gets','db block gets','physical reads')"
)

ORACLE_TBS_SQL = (
    "SELECT df.tablespace_name AS name, "
    "ROUND(SUM(df.bytes) / 1048576, 1) AS total_mb, "
    # 最大可用容量：AUTOEXTEND 数据文件取 MAXBYTES 上限（无上限/未开启取当前 bytes），
    # 剩余率按「最大可用」口径计算，避免可扩展表空间误告警（#61）
    "ROUND(SUM(CASE WHEN df.autoextensible = 'YES' "
    "            THEN GREATEST(df.maxbytes, df.bytes) ELSE df.bytes END) / 1048576, 1) AS max_mb, "
    "ROUND(NVL(SUM(fr.free_bytes), 0) / 1048576, 1) AS free_mb "
    "FROM dba_data_files df "
    "LEFT JOIN (SELECT tablespace_name, SUM(bytes) AS free_bytes "
    "           FROM dba_free_space GROUP BY tablespace_name) fr "
    "  ON fr.tablespace_name = df.tablespace_name "
    "GROUP BY df.tablespace_name ORDER BY free_mb ASC"
)

ORACLE_REPL_SQL = (
    "SELECT name, value FROM v$dataguard_stats WHERE name IN ('apply lag','transport lag')"
)

ORACLE_LOCKS_SQL = "SELECT count(*) AS n FROM v$session WHERE blocking_session IS NOT NULL"

# ── RAC / ADG 身份与配对（监控用户权限要求不高于现有 v$sysstat / v$dataguard_stats）──
# v$database：角色（PRIMARY / PHYSICAL STANDBY…）+ 集群唯一名（RAC 各实例相同）+ open_mode
ORACLE_IDENTITY_SQL = (
    "SELECT database_role, db_unique_name, name, open_mode FROM v$database"
)
# v$instance：RAC 实例身份（单机也返回一行：instance_number=1）
ORACLE_INSTANCE_SQL = (
    "SELECT instance_name, instance_number, host_name FROM v$instance"
)
# 主库侧归档目的地：声明往哪些 db_unique_name 送 redo → 前端自动画主备配对线
ORACLE_DEST_SQL = (
    "SELECT db_unique_name, status FROM v$archive_dest "
    "WHERE status = 'VALID' AND destination IS NOT NULL"
)

# SQL Server 性能计数器名随服务器语言本地化：中文实例的 sys.dm_os_performance_counters
# 返回「批请求/秒」「事务数/秒」等而非英文名，只匹配英文名在中文实例上查 0 行
# → 大屏 QPS/TPS 恒空（连接数正常、sa 权限正常却无吞吐的典型症状）。
# 匹配表收录英/中命名 → 统一指标键；SQLSERVER_STAT_SQL 的 IN 清单由表键派生，保证两边同步。
SQLSERVER_COUNTER_MAP = {
    'batch requests/sec': 'batch_req', '批请求/秒': 'batch_req',
    'transactions/sec': 'trans', '事务数/秒': 'trans',
    'buffer cache hit ratio': 'hit', '缓冲区高速缓存命中率': 'hit',
    'buffer cache hit ratio base': 'hit_base', '缓冲区高速缓存命中率基': 'hit_base',
}

SQLSERVER_STAT_SQL = (
    "SELECT counter_name, cntr_value FROM sys.dm_os_performance_counters "
    "WHERE counter_name IN (%s)" % ",".join(
        ("N'%s'" if any(ord(c) > 127 for c in k) else "'%s'") % k.replace("'", "''")
        for k in SQLSERVER_COUNTER_MAP)
)

SQLSERVER_LOCKS_SQL = (
    "SELECT count(*) AS n FROM sys.dm_exec_requests WHERE blocking_session_id <> 0"
)

SQLSERVER_TBS_SQL = (
    "SELECT TOP 10 name AS name, "
    "ROUND(SUM(CAST(size AS BIGINT) * 8 / 1024.0), 1) AS total_mb "
    "FROM sys.master_files GROUP BY name ORDER BY total_mb DESC"
)

# ── DM8 达梦：V$SYSSTAT 列为 NAME/STAT_VAL，指标名 DM 专属（实测于 DM8，非 Oracle 命名）──
DM_STAT_SQL = (
    "SELECT name, stat_val AS value FROM v$sysstat WHERE name IN "
    "('sql executed count','transaction commit count','transaction rollback count')"
)

DM_TBS_SQL = (
    "SELECT df.tablespace_name AS name, "
    "ROUND(SUM(df.bytes) / 1048576, 1) AS total_mb, "
    # 同 ORACLE_TBS_SQL：按 AUTOEXTEND 上限计算最大可用容量，剩余率防误告警（#61）
    "ROUND(SUM(CASE WHEN df.autoextensible = 'YES' "
    "            THEN GREATEST(df.maxbytes, df.bytes) ELSE df.bytes END) / 1048576, 1) AS max_mb, "
    "ROUND(NVL(SUM(fr.free_bytes), 0) / 1048576, 1) AS free_mb "
    "FROM dba_data_files df "
    "LEFT JOIN (SELECT tablespace_name, SUM(bytes) AS free_bytes "
    "           FROM dba_free_space GROUP BY tablespace_name) fr "
    "  ON fr.tablespace_name = df.tablespace_name "
    "GROUP BY df.tablespace_name ORDER BY free_mb ASC"
)

# 数据守护备库重演延迟（V$RAPPLY_INFO；单机/视图缺失时失败 → 优雅降级 None）
DM_REPL_SQL = (
    "SELECT ROUND(DATEDIFF(SECOND, MAX(APPLY_TIME), SYSDATE)) AS lag_s "
    "FROM v$rapply_info"
)

# 锁等待：只数 V$TRXWAIT 行数（视图实测存在但列名不在目录视图，避免列依赖）
DM_LOCKS_SQL = "SELECT count(*) AS n FROM v$trxwait"

# ── GBase 8s（sysmaster 库；JDBC 子进程通道）──
# sysprofile 累计计数：isreads/iswrites 为行级 I/O 计数（QPS 近似），
# iscommits/isrollbacks 为事务提交/回滚（TPS 精确）。
GBASE_STAT_SQL = (
    "SELECT name, value FROM sysmaster:sysprofile "
    "WHERE name IN ('isreads','iswrites','iscommits','isrollbacks')"
)

# dbspaces 容量 Top：chk_size 单位为页（按 2KB 页估算，页大小非 2K 时有偏差）。
GBASE_TBS_SQL = (
    "SELECT TRIM(d.name) AS name, "
    "ROUND(SUM(c.chk_size) * 2 / 1024.0, 1) AS total_mb "
    "FROM sysmaster:sysdbspaces d JOIN sysmaster:syschunks c "
    "  ON c.dbsnum = d.dbsnum "
    "GROUP BY d.name ORDER BY total_mb DESC LIMIT 10"
)

# ── DB2（SYSIBMADM / MON_GET_DATABASE 表函数；监控账号需 SYSMON 权限）──
# 列名经真实 DB2 LUW 实测：QPS 计数 ACT_RQSTS_TOTAL（语句活动总数）；
# TPS = TOTAL_APP_COMMITS + TOTAL_APP_ROLLBACKS。
DB2_STAT_SQL = (
    "SELECT 'act_rqsts' AS name, ACT_RQSTS_TOTAL AS value "
    "  FROM TABLE(MON_GET_DATABASE(-1)) "
    "UNION ALL SELECT 'commits', TOTAL_APP_COMMITS FROM TABLE(MON_GET_DATABASE(-1)) "
    "UNION ALL SELECT 'rollbacks', TOTAL_APP_ROLLBACKS FROM TABLE(MON_GET_DATABASE(-1))"
)

DB2_TBS_SQL = (
    "SELECT TBSP_NAME AS name, "
    "ROUND(TBSP_TOTAL_SIZE_KB / 1024.0, 1) AS total_mb, "
    "ROUND(TBSP_FREE_SIZE_KB / 1024.0, 1) AS free_mb "
    "FROM SYSIBMADM.TBSP_UTILIZATION "
    "ORDER BY TBSP_FREE_SIZE_KB ASC FETCH FIRST 10 ROWS ONLY"
)

# ── ClickHouse（system 表；JDBC 子进程通道）──
# system.events 累计计数：Query 作 QPS；InsertQuery 作写入 TPS
# （ClickHouse 无事务概念，TPS 即插入批次速率）。
CLICKHOUSE_STAT_SQL = (
    "SELECT event AS name, value FROM system.events "
    "WHERE event IN ('Query','SelectQuery','InsertQuery')"
)

# 库容量 Top（active parts 实际占用）。
CLICKHOUSE_TBS_SQL = (
    "SELECT database AS name, ROUND(SUM(bytes) / 1048576, 1) AS total_mb "
    "FROM system.parts WHERE active "
    "GROUP BY database ORDER BY total_mb DESC LIMIT 10"
)

# ── GBase 8s 复制 / 锁 ──
# 复制延迟：sysmaster:sysdri 仅暴露 HDR 状态列（type/state/name 等），
# 无「秒级延迟」列（onstat -g hdr 的 log page distance 无法用 SQL 取秒数）。
# 故 DR 激活且已配对（name 非空）视为已同步 → repl_lag_s=0；未配置 HDR 置 None。
GBASE_REPL_SQL = "SELECT type, state, name FROM sysmaster:sysdri"

# 锁等待：syslocks.waiter 非空的行即阻塞等待（多源确认列名），count 即等待会话数。
GBASE_LOCKS_SQL = (
    "SELECT count(*) AS n FROM sysmaster:syslocks WHERE waiter IS NOT NULL"
)

# ── DB2 复制 / 锁 ──
# HADR 备库回放延迟（毫秒）；未配置 HADR 时 MON_GET_HADR 返回空集 → 保持 None。
DB2_REPL_SQL = (
    "SELECT MAX(STANDBY_REPLAY_DELAY) AS lag_ms FROM TABLE(MON_GET_HADR(-1))"
)

# 锁等待：SYSIBMADM.LOCKWAITS 每行一个等待者（需 SYSMON/MON 权限，缺失则降级 None）。
DB2_LOCKS_SQL = "SELECT count(*) AS n FROM SYSIBMADM.LOCKWAITS"

# ── ClickHouse 复制 / 锁 ──
# 复制延迟：ReplicatedMergeTree 的 absolute_delay 单位为秒。
CLICKHOUSE_REPL_SQL = (
    "SELECT max(absolute_delay) AS lag_s FROM system.replicas"
)

# ClickHouse 无行级锁；以「未完成 mutation（卡住/阻塞的 schema 变更）」数作代理指标。
CLICKHOUSE_LOCKS_SQL = (
    "SELECT count(*) AS n FROM system.mutations WHERE NOT is_done"
)

SCREEN_SQLS = {
    'mysql': {'stat': MYSQL_STAT_SQL, 'tbs': MYSQL_TBS_SQL,
              'repl': MYSQL_REPL_SQL, 'repl8': MYSQL_REPL_SQL8},
    'pg': {'stat': PG_STAT_SQL, 'tbs': PG_TBS_SQL, 'repl': PG_REPL_SQL,
           'locks': PG_LOCKS_SQL},
    'oracle': {'stat': ORACLE_STAT_SQL, 'tbs': ORACLE_TBS_SQL, 'repl': ORACLE_REPL_SQL,
               'locks': ORACLE_LOCKS_SQL, 'identity': ORACLE_IDENTITY_SQL,
               'inst': ORACLE_INSTANCE_SQL, 'dest': ORACLE_DEST_SQL},
    'sqlserver': {'stat': SQLSERVER_STAT_SQL, 'tbs': SQLSERVER_TBS_SQL,
                  'locks': SQLSERVER_LOCKS_SQL},
    'dm': {'stat': DM_STAT_SQL, 'tbs': DM_TBS_SQL, 'repl': DM_REPL_SQL,
           'locks': DM_LOCKS_SQL},
    'gbase': {'stat': GBASE_STAT_SQL, 'tbs': GBASE_TBS_SQL,
              'repl': GBASE_REPL_SQL, 'locks': GBASE_LOCKS_SQL},
    'db2': {'stat': DB2_STAT_SQL, 'tbs': DB2_TBS_SQL,
            'repl': DB2_REPL_SQL, 'locks': DB2_LOCKS_SQL},
    'clickhouse': {'stat': CLICKHOUSE_STAT_SQL, 'tbs': CLICKHOUSE_TBS_SQL,
                   'repl': CLICKHOUSE_REPL_SQL, 'locks': CLICKHOUSE_LOCKS_SQL},
}


# ═══════════════════════════════════════════════════════════
#  数值解析小工具
# ═══════════════════════════════════════════════════════════

def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _sum_or_none(*vals):
    """相加；任一项缺失（None）返回 None，保证差值计算不误报为 0。"""
    if any(v is None for v in vals):
        return None
    return sum(_num(v) for v in vals)


def _tbs_free_pct(total_mb, max_mb, free_mb):
    """表空间剩余率：优先按「最大可用容量」(含 datafile AUTOEXTEND 上限 MAXBYTES) 计算，
    避免 autoextend 表空间因未计入可扩展空间而误告警（#61）；
    无 autoextend / 取不到上限时退化为「当前分配容量」口径，行为不变。"""
    total_mb = _num(total_mb)
    max_mb = _num(max_mb)
    free_mb = _num(free_mb)
    if max_mb and max_mb > 0:
        used = (total_mb or 0) - free_mb
        free_max = max_mb - used
        return round(free_max / max_mb * 100, 1)
    if total_mb and total_mb > 0:
        return round(free_mb / total_mb * 100, 1)
    return None


def _find_key(row, *keywords):
    """在 row dict 的键名里模糊匹配（列名大小写/前缀随驱动不同）。"""
    for k in row or {}:
        kl = k.lower()
        if all(w in kl for w in keywords):
            return row[k]
    return None


def _scalar(rows, col=None):
    """取首行某列（或唯一列）的数值。"""
    if not rows:
        return None
    r = rows[0]
    if col:
        for k, v in r.items():
            if k.lower() == col.lower():
                return v
        return None
    vals = list(r.values())
    return vals[0] if vals else None


def _parse_dg_lag(value):
    """解析 Oracle v$dataguard_stats 的 '+00 00:00:12' / '+000 00:00:00.000'。"""
    try:
        s = str(value).strip()
        if not s:
            return None
        neg = s.startswith('-')
        s = s.lstrip('+-')
        days = 0
        if ' ' in s:
            d_part, s = s.split(' ', 1)
            days = int(d_part)
        parts = s.split(':')
        secs = days * 86400 + int(parts[0]) * 3600 + int(parts[1]) * 60
        if len(parts) > 2:
            secs += int(float(parts[2]))
        return -secs if neg else secs
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
#  下钻明细脱敏投影（连接/会话、慢查询逐行）
# ═══════════════════════════════════════════════════════════
# 明细已在 MonitorEngine 采集结果中（conn_data/slow_data['data']），此处仅做
# 截顶 + 值截断 + 剔除疑似凭据列，不引入新 SQL、不触碰数据库。
_DRILL_MAX_ROWS = 50       # 单实例下钻明细最多透出 50 行
_DRILL_VAL_MAX = 240       # 单行单元格值最大长度（超出截断，避免超大 SQL 文本撑爆接口）
_DRILL_SKIP_KEYS = ('password', 'pwd', 'passwd', 'token', 'secret', 'credential')


def _clip_val(v):
    """非字符串值原样返回；字符串/复合值超长截断并追加省略号。"""
    if isinstance(v, str):
        return v[:_DRILL_VAL_MAX] + '…' if len(v) > _DRILL_VAL_MAX else v
    if isinstance(v, (dict, list, tuple)):
        try:
            s = json.dumps(v, ensure_ascii=False)
        except Exception:
            s = str(v)
        return s[:_DRILL_VAL_MAX] + '…' if len(s) > _DRILL_VAL_MAX else s
    return v


def _sanitize_rows(data, limit=_DRILL_MAX_ROWS):
    """连接/会话/慢查询逐行 → 截顶 + 值截断 + 跳过疑似凭据列。

    返回 list[dict]：键为原列名（已过滤凭据列），值为截断后的标量/字符串。
    行结构随 db_type 不同（列名各异），前端按列动态渲染。"""
    if not data:
        return []
    rows = []
    for r in data[:limit]:
        if not isinstance(r, dict):
            rows.append({'_value': _clip_val(r)})
            continue
        clean = {}
        for k, v in r.items():
            kl = (str(k) or '').lower()
            if any(w in kl for w in _DRILL_SKIP_KEYS):
                continue
            clean[str(k)] = _clip_val(v)
        rows.append(clean)
    return rows


# ═══════════════════════════════════════════════════════════
#  单实例采集：额外指标（QPS/TPS/缓存/容量/复制/锁）
# ═══════════════════════════════════════════════════════════

def collect_extra(engine, instance_id, db_type):
    """采集 engine 之外的增量/容量/复制指标，返回 (counters, extras)。

    counters: 累计量（供差值计算 QPS/TPS/命中率），结构随 family
    extras:   即时指标 {'tbs': [...], 'repl_lag_s': int|None, 'lock_waits': int|None}
    任一 SQL 失败对应项为 None，不影响其它项。
    """
    fam = db_family(db_type)
    sqls = SCREEN_SQLS.get(fam) if fam else None
    if sqls and normalize_db_type(db_type) == 'oceanbase':
        # OceanBase（MySQL 租户）：计数器换 OB 原生 v$sysstat，容量查询 COALESCE 加固
        sqls = dict(sqls, ob_stat=OB_STAT_SQL, ob_stat_local=OB_STAT_SQL_LOCAL,
                    tbs=OB_TBS_SQL)
    counters, extras = {}, {'tbs': None, 'repl_lag_s': None, 'lock_waits': None,
                            # RAC / ADG 身份与配对（仅 oracle family 填充，其余保持 None）
                            'role': None, 'db_unique_name': None, 'db_name': None,
                            'open_mode': None, 'instance_name': None,
                            'instance_number': None, 'dests': None,
                            'transport_lag_s': None, 'apply_lag_s': None}
    if not sqls:
        return counters, extras

    def q(key):
        try:
            return engine.run_query(instance_id, sqls[key])
        except Exception as e:
            # stat（性能计数）查询失败不再静默：分类为权限提示码，
            # 由大屏前端转成可读文案（SQL Server 缺 VIEW SERVER STATE 最常见）。
            if key == 'stat' and not extras.get('stat_hint'):
                low = str(e).lower()
                if 'view server state' in low or (fam == 'sqlserver' and (
                        'permission' in low or 'denied' in low or 'privilege' in low)):
                    extras['stat_hint'] = 'view_server_state'
                else:
                    extras['stat_hint'] = 'stat_failed'
            return None

    if fam == 'mysql':
        if normalize_db_type(db_type) == 'oceanbase':
            # ── OceanBase：SHOW GLOBAL STATUS 在 OB 上无 Queries/Com_* 计数器，
            # QPS/TPS/缓存命中走 OB 原生 v$sysstat（stat_id 口径同 OCP 官方）。
            # counters 键名复用 _rate 的既有组合：
            #   QPS ← 'questions'；TPS ← 'com_commit'+'com_rollback'；
            #   缓存命中 ← 'hit'/'hit_base'（_commit_snap 直接算百分比）
            rows = q('ob_stat') or q('ob_stat_local')
            st = {}
            for r in rows or []:
                sid = int(_num(_find_key(r, 'stat_id'), -1))
                if sid >= 0:
                    st[sid] = _num(_find_key(r, 'value'))
            ob_qps = sum((st.get(i) or 0) for i in OB_QPS_STAT_IDS) if st else None
            ob_tps = st.get(OB_TPS_STAT_ID)
            c_hit = st.get(OB_CACHE_HIT_STAT_ID)
            c_miss = st.get(OB_CACHE_MISS_STAT_ID)
            counters = {}
            if ob_qps:
                counters['questions'] = ob_qps
            if ob_tps is not None:
                counters['com_commit'] = ob_tps
                counters['com_rollback'] = 0
            if c_hit is not None and c_miss is not None and (c_hit + c_miss) > 0:
                counters['hit'] = c_hit
                counters['hit_base'] = c_hit + c_miss
        else:
            rows = q('stat')
            st = {}
            for r in rows or []:
                k = _find_key(r, 'variable_name')
                v = _find_key(r, 'value')
                if k is not None:
                    st[str(k).lower()] = _num(v)
            counters = {
                'questions': st.get('questions'),
                'com_commit': st.get('com_commit'),
                'com_rollback': st.get('com_rollback'),
                'buf_reads': st.get('innodb_buffer_pool_read_requests'),
                'buf_disk': st.get('innodb_buffer_pool_reads'),
            }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': _find_key(r, 'name'), 'total_mb': _num(_find_key(r, 'total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        repl = q('repl8') or q('repl')
        if repl:
            lag = _find_key(repl[0], 'seconds_behind')
            extras['repl_lag_s'] = int(_num(lag, -1)) if lag is not None else None

    elif fam == 'pg':
        rows = q('stat')
        if rows:
            r = rows[0]
            counters = {
                'xact_commit': _num(_find_key(r, 'xact_commit')),
                'xact_rollback': _num(_find_key(r, 'xact_rollback')),
                'blks_hit': _num(_find_key(r, 'blks_hit')),
                'blks_read': _num(_find_key(r, 'blks_read')),
            }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': _find_key(r, 'name'), 'total_mb': _num(_find_key(r, 'total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        repl = q('repl')
        if repl:
            # 取最大延迟（多从库时取最差值）
            extras['repl_lag_s'] = max(int(_num(r.get('lag_s'))) for r in repl) if repl else None
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'oracle':
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': st.get('execute count'),
            'commits': st.get('user commits'),
            'rollbacks': st.get('user rollbacks'),
            'buf_reads': (st.get('consistent gets') or 0) + (st.get('db block gets') or 0),
            'buf_disk': st.get('physical reads'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': _find_key(r, 'name'), 'total_mb': _num(_find_key(r, 'total_mb')),
                 'max_mb': _num(_find_key(r, 'max_mb')),
                 'free_mb': _num(_find_key(r, 'free_mb')),
                 'free_pct': _tbs_free_pct(_find_key(r, 'total_mb'), _find_key(r, 'max_mb'), _find_key(r, 'free_mb'))}
                for r in tbs]
        repl = q('repl')
        if repl:
            # transport lag / apply lag 分开保留（ADG 传输线分开标注），repl_lag_s 取最差
            for r in repl:
                nm = str(_find_key(r, 'name') or '').lower()
                lag = _parse_dg_lag(_find_key(r, 'value'))
                if lag is None:
                    continue
                if 'transport' in nm:
                    extras['transport_lag_s'] = lag
                elif 'apply' in nm:
                    extras['apply_lag_s'] = lag
            lags = [l for l in (extras['transport_lag_s'], extras['apply_lag_s'])
                    if l is not None]
            if lags:
                extras['repl_lag_s'] = max(lags)
        # RAC / ADG 身份（低权限/单机/查询失败均优雅降级为 None，不影响其它指标）
        ident = q('identity')
        if ident:
            m = {str(k).lower(): v for k, v in (ident[0] or {}).items()}
            # 精确小写列名取值（_find_key 模糊匹配会被 db_unique_name 含 'name' 干扰）
            extras['role'] = (str(m.get('database_role') or '').strip() or None)
            extras['db_unique_name'] = (str(m.get('db_unique_name') or '').strip() or None)
            extras['db_name'] = (str(m.get('name') or '').strip() or None)
            extras['open_mode'] = (str(m.get('open_mode') or '').strip() or None)
        inst_rows = q('inst')
        if inst_rows:
            m2 = {str(k).lower(): v for k, v in (inst_rows[0] or {}).items()}
            inum = _num(m2.get('instance_number'), 0)
            extras['instance_name'] = (str(m2.get('instance_name') or '').strip() or None)
            extras['instance_number'] = int(inum) if inum else None
        dest_rows = q('dest')
        if dest_rows:
            dests = []
            for r in dest_rows:
                m3 = {str(k).lower(): v for k, v in (r or {}).items()}
                dn = str(m3.get('db_unique_name') or '').strip()
                if dn and dn not in dests:
                    dests.append(dn)
            if dests:
                extras['dests'] = dests
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'dm':
        # DM8 达梦：v$sysstat 专属指标名（sql executed count 等），计数键沿用
        # oracle 命名（_rate 组合直接复用）；缓存命中暂缺（无 hit_base 指标）。
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': st.get('sql executed count'),
            'commits': st.get('transaction commit count'),
            'rollbacks': st.get('transaction rollback count'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': _find_key(r, 'name'), 'total_mb': _num(_find_key(r, 'total_mb')),
                 'max_mb': _num(_find_key(r, 'max_mb')),
                 'free_mb': _num(_find_key(r, 'free_mb')),
                 'free_pct': _tbs_free_pct(_find_key(r, 'total_mb'), _find_key(r, 'max_mb'), _find_key(r, 'free_mb'))}
                for r in tbs]
        repl = q('repl')
        if repl:
            lag = _scalar(repl, 'lag_s')
            # 单机库 DATEDIFF 对 NULL → 空结果/空串，视为无复制（None）
            if lag is not None and str(lag).strip() != '':
                extras['repl_lag_s'] = int(_num(lag))
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'sqlserver':
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'counter_name')
            if k is None:
                continue
            ck = SQLSERVER_COUNTER_MAP.get(str(k).strip().lower())
            if ck:
                # 同计数器多行（如 Transactions/sec 按库一行）累加，得实例级总值
                st[ck] = st.get(ck, 0) + _num(_find_key(r, 'cntr_value'))
        counters = {
            'batch_req': st.get('batch_req'),
            'trans': st.get('trans'),
            'hit': st.get('hit'),
            'hit_base': st.get('hit_base'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': _find_key(r, 'name'), 'total_mb': _num(_find_key(r, 'total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'gbase':
        # GBase 8s（sysmaster）：isreads/iswrites 相加作 QPS 近似，
        # iscommits/isrollbacks 相加作 TPS；容量按 dbspace 页数估算（2KB 页）。
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': _sum_or_none(st.get('isreads'), st.get('iswrites')),
            'commits': st.get('iscommits'),
            'rollbacks': st.get('isrollbacks'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': _find_key(r, 'name'), 'total_mb': _num(_find_key(r, 'total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        repl = q('repl')
        if repl:
            # HDR 激活且已配对（name 非空）视为同步 → 0 秒；其余（含未配置 HDR）留 None
            st = str(_find_key(repl[0], 'state') or '').strip().lower()
            paired = bool(str(_find_key(repl[0], 'name') or '').strip())
            if st == 'on' and paired:
                extras['repl_lag_s'] = 0
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'db2':
        # DB2（MON_GET_DATABASE 累计计数，列名经真实实例实测）：
        # ACT_RQSTS_TOTAL 作 QPS；TOTAL_APP_COMMITS/ROLLBACKS 作 TPS；
        # 表空间自由空间来自 TBSP_UTILIZATION。
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': st.get('act_rqsts'),
            'commits': st.get('commits'),
            'rollbacks': st.get('rollbacks'),
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': _find_key(r, 'name'), 'total_mb': _num(_find_key(r, 'total_mb')),
                 'free_mb': _num(_find_key(r, 'free_mb')),
                 'free_pct': (round(_num(_find_key(r, 'free_mb')) / _num(_find_key(r, 'total_mb')) * 100, 1)
                              if _num(_find_key(r, 'total_mb')) > 0 else None)}
                for r in tbs]
        repl = q('repl')
        if repl:
            # STANDBY_REPLAY_DELAY 为毫秒；未配置 HADR 返回空集/NULL → 保持 None
            lag_ms = _scalar(repl, 'lag_ms')
            if lag_ms is not None and str(lag_ms).strip() != '':
                extras['repl_lag_s'] = int(_num(lag_ms) / 1000)
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    elif fam == 'clickhouse':
        # ClickHouse（system.events 累计计数）：Query 作 QPS，InsertQuery 作写入 TPS。
        rows = q('stat')
        st = {}
        for r in rows or []:
            k = _find_key(r, 'name')
            if k is not None:
                st[str(k).lower()] = _num(_find_key(r, 'value'))
        counters = {
            'exec_count': st.get('query'),
            'commits': st.get('insertquery'),
            'rollbacks': 0,
        }
        tbs = q('tbs')
        if tbs:
            extras['tbs'] = [
                {'name': _find_key(r, 'name'), 'total_mb': _num(_find_key(r, 'total_mb')),
                 'free_mb': None, 'free_pct': None} for r in tbs]
        repl = q('repl')
        if repl:
            # absolute_delay 单位秒；无复制表（system.replicas 空集）→ None
            lag = _scalar(repl, 'lag_s')
            if lag is not None and str(lag).strip() != '':
                extras['repl_lag_s'] = int(_num(lag))
        locks = q('locks')
        if locks:
            extras['lock_waits'] = int(_num(_scalar(locks)))

    return counters, extras


# ═══════════════════════════════════════════════════════════
#  大屏采样器：后台线程 + 差值计算 + 内存快照
# ═══════════════════════════════════════════════════════════

class ScreenCollector:
    """监控大屏 — 统一采集器的只读投影适配器 + 阈值配置持有者。

    历史版本本类自带 15s 后台循环与一整套内存快照（``_snap`` / ``_prev``
    / ``_spark`` / ``_trend``），与首页 30s、MonitorEngine 10s 三路重复建连。
    现在快照、速率状态、趋势全部收敛到 UnifiedStore；本类只保留
    阈值配置、速率算法与单实例快照组装能力，供统一采集器调用。
    ``get_snapshot`` / ``get_trend`` 的返回结构与旧版完全一致。
    """

    INTERVAL = 15          # 采样间隔（秒，与 UnifiedCollector 一致）
    HISTORY = 60           # 每实例保留采样点数（15s × 60 ≈ 15 分钟）
    TREND_POINTS = 60      # 全库 QPS 趋势点数
    MAX_WORKERS = 8        # 单轮并发采集线程数
    # 速率防噪（#63）：计数器类指标做差求速率时，异常样本不得污染大屏
    DT_MIN_RATIO = 0.3     # 实际时间差低于「采样间隔 × 此系数」视为抖动样本，丢弃
    QPS_SPIKE_MAX = 100000 # 单实例 QPS 物理不可能阈值，超出记为采样异常、不进 KPI

    # 阈值（与设计文档一致）
    WARN_CONN_UTIL, CRIT_CONN_UTIL = 80.0, 95.0
    WARN_TBS_FREE, CRIT_TBS_FREE = 30.0, 15.0
    WARN_REPL_LAG, CRIT_REPL_LAG = 30, 300
    WARN_LOCKS = 10

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._load_thresholds()

    @staticmethod
    def _store():
        from modules.monitor.unified_collector import get_unified_collector
        return get_unified_collector().store

    @staticmethod
    def _collector():
        from modules.monitor.unified_collector import get_unified_collector
        return get_unified_collector()

    @property
    def running(self):
        """assemble_overview 读取的 probe_running 开关，取自唯一采集器。"""
        try:
            return bool(self._collector().is_running)
        except Exception:
            return False

    # ── 阈值配置（独立于 dbc_config.json；运行时可调，持久化到 monitor_thresholds.json）──
    # dir: up=越大越糟(warn<crit)  down=越小越糟(crit<warn)  lock=仅告警阈值
    THRESHOLD_MAP = [
        ('warn_conn_util', 'crit_conn_util', 'up', (0, 100)),
        ('warn_tbs_free', 'crit_tbs_free', 'down', (0, 100)),
        ('warn_repl_lag', 'crit_repl_lag', 'up', (0, 86400)),
        ('warn_locks', None, 'lock', (0, 100000)),
    ]

    @staticmethod
    def _threshold_path():
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'monitor_thresholds.json')

    @staticmethod
    def _num(v, default):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _current_threshold_dict(self):
        d = {}
        for wk, ck, _, _ in self.THRESHOLD_MAP:
            d[wk] = getattr(self, wk.upper())
            if ck is not None:
                d[ck] = getattr(self, ck.upper())
        # 同一份 JSON 还承载「Windows 服务告警」等扩展阈值（不属于大屏
        # THRESHOLD_MAP）。保存时原样带回，避免用户在设置页改一次大屏阈值
        # 就把扩展配置整份覆盖丢失。
        try:
            with open(self._threshold_path(), 'r', encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k not in d:
                        d[k] = v
        except Exception:
            pass
        return d

    def _load_thresholds(self):
        """读取 monitor_thresholds.json 覆盖默认阈值；文件缺失/损坏时回退类属性默认值。
        首次运行（文件不存在）写入默认阈值文件。"""
        p = self._threshold_path()
        try:
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    for wk, ck, _, _ in self.THRESHOLD_MAP:
                        if wk in d: setattr(self, wk.upper(), self._num(d.get(wk), getattr(self, wk.upper())))
                        if ck is not None and ck in d: setattr(self, ck.upper(), self._num(d.get(ck), getattr(self, ck.upper())))
        except Exception:
            pass  # 文件异常 → 回退类属性默认值
        if not os.path.exists(p):
            try:
                self._save_thresholds(self._current_threshold_dict())
            except Exception:
                pass

    def _save_thresholds(self, d):
        p = self._threshold_path()
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def get_thresholds(self):
        """返回当前生效阈值（供 API GET）。"""
        out = self._current_threshold_dict()
        out['warn_locks'] = int(out['warn_locks'])
        return out

    def set_thresholds(self, data):
        """校验并应用新阈值，持久化到 JSON；返回生效后的阈值 dict。
        校验失败抛出 ValueError（含中文说明）。"""
        cur = self._current_threshold_dict()
        new = {}
        for wk, ck, _, _ in self.THRESHOLD_MAP:
            new[wk] = self._num(data.get(wk, cur[wk]), cur[wk])
            if ck is not None:
                new[ck] = self._num(data.get(ck, cur[ck]), cur[ck])
        errs = []
        if not (0 < new['warn_conn_util'] < new['crit_conn_util'] <= 100):
            errs.append('连接利用率：须 0 < 告警阈值 < 严重阈值 ≤ 100')
        if not (0 <= new['crit_tbs_free'] < new['warn_tbs_free'] <= 100):
            errs.append('表空间剩余：须 0 ≤ 严重阈值 < 告警阈值 ≤ 100')
        if not (0 <= new['warn_repl_lag'] < new['crit_repl_lag']):
            errs.append('复制延迟：须 0 ≤ 告警阈值 < 严重阈值')
        if new['warn_locks'] < 0:
            errs.append('锁等待：告警阈值须 ≥ 0')
        if errs:
            raise ValueError('；'.join(errs))
        for wk, ck, _, _ in self.THRESHOLD_MAP:
            setattr(self, wk.upper(), new[wk])
            if ck is not None:
                setattr(self, ck.upper(), new[ck])
        self._save_thresholds(self._current_threshold_dict())
        return self.get_thresholds()

    # ── 启停：委托唯一采集器（绝不再启动第二条循环）──
    def start(self):
        self._collector().start()

    def stop(self):
        self._collector().stop()

    # 后台循环 _loop / 单轮 _round 已移除：全系统只有 UnifiedCollector._loop
    # 一条采集线程。本类保留的 _collect_one（单实例快照组装）与 _apply_rates
    # （速率差值）由统一采集器在同一条借用连接上调用，结果写入 UnifiedStore；
    # 趋势、历史回放、告警评估也在统一采集器的一轮里各执行一次。

    @staticmethod
    def _rate(prev_c, cur_c, dt, kind):
        """按 kind 计算每秒速率；任一计数缺失返回 None。

        组合之间是"备选"关系（不同库的计数名），组合内部是"相加"关系
        （如 MySQL TPS = Com_commit + Com_rollback）。
        pg 家族无语句级计数（除非装 pg_stat_statements），以事务率
        （xact_commit + xact_rollback）作为 QPS 近似与 TPS。

        增量为负说明计数器被重置（实例重启 / PMU 归零 / 跨实例切换），
        此时速率无意义，返回 None 由调用方标记 reset——不再用 0 兜底
        造成"突然掉零"的假谷（#63）。
        """
        if kind == 'qps':
            combos = (('questions',), ('exec_count',), ('batch_req',),
                      ('xact_commit', 'xact_rollback'))
        else:
            combos = (('com_commit', 'com_rollback'), ('commits', 'rollbacks'),
                      ('trans',), ('xact_commit', 'xact_rollback'))
        for combo in combos:
            if all(prev_c.get(k) is not None for k in combo):
                dv = sum((cur_c.get(k) or 0) - (prev_c.get(k) or 0) for k in combo)
                if dv < 0:
                    return None
                return dv / dt
        return None

    def _collect_one(self, engine, inst, conn_data, slow_data, native_data=None):
        """组装单实例大屏快照。

        ``native_data``：NoSQL（mongodb/redis）原生通道结果。统一采集器
        已在一轮里建好唯一连接并完成采集，把它传进来复用，避免本方法
        再调一次 ``collect_native(inst)`` 造成第二条连接。独立调用时
        传 None，行为与旧版一致。
        """
        # 统一用字符串 id：UnifiedStore 的帧键、URL 下钻参数、告警状态机
        # 全部按 str 归一，避免 int/str 混用导致 conn_data 查不到而大屏空连接。
        iid = str(inst['id'])
        db_type = (inst.get('db_type') or '').lower()
        snap = {
            'id': iid,  # 告警状态机按 id 跟踪实例（update() 无 id 的快照会被跳过）
            'name': inst.get('name', iid),
            'db_type': db_type,
            'group': inst.get('group') or 'default',
            'host': inst.get('host', ''),
            'port': inst.get('port', ''),
            'label': "%s (%s:%s)" % (inst.get('name', iid), inst.get('host', '?'), inst.get('port', '?')),
            'ts': time.time(),
            'conn': None, 'slowq': 0, 'qps': None, 'tps': None,
            'cache_hit_pct': None, 'lock_waits': None, 'repl_lag_s': None,
            'role': None, 'db_unique_name': None, 'db_name': None, 'open_mode': None,
            'instance_name': None, 'instance_number': None, 'dests': None,
            'transport_lag_s': None, 'apply_lag_s': None,
            'tbs': None, 'err': None, 'status': 'ok', 'counters': {},
            'stat_hint': None, 'degraded': False,
            # 下钻明细：连接/会话 与 慢查询 逐行（截顶脱敏，详见 _sanitize_rows）
            'conn_rows': [], 'slow_rows': [],
            # SSH 跳板信息：用于大屏主机节点展示"经由此网关/跳板可达该实例"
            'ssh': {
                'enabled': bool(inst.get('ssh_enabled')),
                'host': inst.get('ssh_host') or '',
                'port': inst.get('ssh_port') or 22,
                'user': inst.get('ssh_user') or '',
            },
        }

        # 0) MongoDB / Redis 原生驱动通道：SQL 引擎（DBAPI/JDBC）不覆盖的
        #    NoSQL 类型在此直连采集，连通性与指标一并拿到后提前返回。
        if db_type in NATIVE_DB_TYPES:
            nd = native_data if native_data is not None else collect_native(inst)
            if not nd.get('alive'):
                snap['err'] = nd.get('err')
                snap['status'] = 'down'
                return snap
            snap['conn'] = nd.get('conn')
            snap['counters'] = nd.get('counters') or {}
            snap['tbs'] = nd.get('tbs')
            snap['repl_lag_s'] = nd.get('repl_lag_s')
            snap['lock_waits'] = nd.get('lock_waits')
            snap['slowq'] = nd.get('slowq') or 0
            # NoSQL 原生通道的逐行明细（Redis CLIENT LIST / SLOWLOG 等）：
            # 经脱敏投影（剔除凭据列、截顶、值截断）后填入下钻 tab，与 SQL 通道同口径。
            snap['conn_rows'] = _sanitize_rows(nd.get('conn_rows'))
            snap['slow_rows'] = _sanitize_rows(nd.get('slow_rows'))
            snap['conn_err'] = None
            snap['status'] = self._derive_status(snap)
            return snap

        # 1) 连接与会话（复用 MonitorEngine 采集结果）
        cd = conn_data.get(iid)
        if cd and cd.get('error'):
            # 「不支持的类型」= 该库无连接数采集模板（db2/mongodb/redis 等），
            # 属能力缺失而非实例故障：不判 down、不作为 err 标红，
            # conn 留空（卡片连接显示 –，第 4 行回落显示 host:port）。
            if '不支持的类型' in str(cd['error']):
                snap['status'] = 'unsupported'
            elif cd.get('err_kind') == 'query':
                # 连接已建立、仅 SQL 失败（ORA-00942 视图权限不足、
                # ORA-00904 11gR1 无该列等）：实例真实可达，绝不能判 down
                # 触发宕机告警。标 warn（采集不完整）+ 保留错误供下钻定位；
                # degraded 阻止末尾 _derive_status 用空指标把它派生成 ok 假绿。
                snap['err'] = cd['error']
                snap['status'] = 'warn'
                snap['degraded'] = True
            else:
                snap['err'] = cd['error']
                snap['status'] = 'down'
        elif cd:
            snap['conn'] = {
                'total': cd.get('total', 0), 'max_conn': cd.get('max_conn', 0),
                'usage_pct': cd.get('usage_pct', 0),
                'active': (cd.get('connections') or {}).get('active', 0),
                'idle': (cd.get('connections') or {}).get('idle', 0),
                'blocked': (cd.get('connections') or {}).get('blocked', 0),
            }
        else:
            # 引擎本轮尚未采到该实例（启动竞态/新加实例/引擎未就绪）：
            # 不能凭空判 ok（假绿误导），也不能判 down（误触发告警），
            # 保持 pending（连接中），等下一轮真实采集结果再落定状态；
            # 不设 err，避免卡片右上角出现「有错误」角标。
            snap['status'] = 'pending'
        sd = slow_data.get(iid)
        snap['slowq'] = len(sd.get('data') or []) if sd and not sd.get('error') else 0
        # 下钻明细：连接/会话 与 慢查询 逐行（复用 engine 本轮采集结果，不引入新 SQL）
        snap['conn_rows'] = _sanitize_rows(cd.get('data') if (cd and not cd.get('error')) else None)
        snap['slow_rows'] = _sanitize_rows(sd.get('data') if (sd and not sd.get('error')) else None)
        # 连接采集真实失败（排除「不支持的类型」这种能力缺失）→ 透传前端，
        # 避免连接会话 tab 一律显示误导性的「可能不支持连接采集」，让用户看到
        # 真实 ORA 错误（如 ORA-00942 权限、JDBC 连接失败、service_name 落空等）。
        if cd and cd.get('error') and '不支持的类型' not in str(cd['error']):
            snap['conn_err'] = str(cd['error'])
        else:
            snap['conn_err'] = None
        # 「已连接但无其它活跃会话」：连接/SQL 均成功，仅因实例上当前只有监控
        # 自身一个连接（连接 SQL 排除自身后 0 行）。属正常空状态，绝不能回落为
        # 「不支持连接采集」，否则会误导用户以为该类型不支持。前端据此显示中性文案。
        if cd and not cd.get('error') and not cd.get('data'):
            snap['conn_no_sessions'] = True
        if sd and sd.get('error') and not snap['err']:
            # 慢查询模板缺失同属能力缺失，不得作为 err 标红。
            # 按阶段区分：query = 已连上但 SQL 失败 → warn（采集不完整）；
            # connect = 真连不上 → 仅把 unsupported 升级为 down，避免把
            # 「该类型无连接模板」误判成实例故障。
            if '不支持的类型' not in str(sd['error']):
                snap['err'] = sd['error']
                if sd.get('err_kind') == 'query':
                    if snap['status'] == 'ok':
                        snap['status'] = 'warn'
                        snap['degraded'] = True
                elif snap['status'] == 'unsupported':
                    snap['status'] = 'down'

        # 2) 增量与容量指标（best effort）
        try:
            counters, extras = collect_extra(engine, iid, db_type)
            snap['counters'] = counters
            snap['tbs'] = extras.get('tbs')
            snap['repl_lag_s'] = extras.get('repl_lag_s')
            snap['lock_waits'] = extras.get('lock_waits')
            # RAC / ADG 身份与配对（oracle 专属；None 不覆盖保持快照结构稳定）
            for k in ('role', 'db_unique_name', 'db_name', 'open_mode',
                      'instance_name', 'instance_number', 'dests',
                      'transport_lag_s', 'apply_lag_s'):
                if extras.get(k) is not None:
                    snap[k] = extras[k]
            # 性能计数一条都没拿到时，把失败原因（权限提示码）透传给大屏
            if extras.get('stat_hint') and not any(
                    v is not None for v in (counters or {}).values()):
                snap['stat_hint'] = extras['stat_hint']
        except Exception as e:
            print('[screen] extra 采集失败 %s: %s' % (snap['label'], e), flush=True)

        # 3) 状态判定（pending = 本轮未探活成功，不参与阈值派生，留给下一轮落定）
        # unsupported（无采集模板的能力缺失）同样不得进入阈值派生——
        # conn/tbs 全空会被 _derive_status 派生成 ok 假绿（OB 漏注册模板期间实测）。
        # degraded（已连上但 SQL/权限失败）同理：已标 warn，不得被空指标改回 ok。
        if (snap['status'] not in ('down', 'pending', 'unsupported')
                and not snap.get('degraded')):
            snap['status'] = self._derive_status(snap)
        return snap

    def _derive_status(self, s):
        conn_util = (s.get('conn') or {}).get('usage_pct') or 0
        worst_tbs = None
        for t in (s.get('tbs') or []):
            fp = t.get('free_pct')
            if fp is not None and (worst_tbs is None or fp < worst_tbs):
                worst_tbs = fp
        lag = s.get('repl_lag_s')
        locks = s.get('lock_waits') or 0
        if (conn_util >= self.CRIT_CONN_UTIL or
                (worst_tbs is not None and worst_tbs <= self.CRIT_TBS_FREE) or
                (lag is not None and lag >= self.CRIT_REPL_LAG)):
            return 'crit'
        if (conn_util >= self.WARN_CONN_UTIL or locks >= self.WARN_LOCKS or
                (worst_tbs is not None and worst_tbs <= self.WARN_TBS_FREE) or
                (lag is not None and lag >= self.WARN_REPL_LAG)):
            return 'warn'
        return 'ok'

    def _pending_snap(self, inst, ts, err=None):
        """预置占位快照：首轮采集完成前就给出实例结构与 pending 状态，
        让拓扑与 KPI 总数立即有内容可渲染（打开大屏秒出基础图表）。"""
        iid = str(inst['id'])
        db_type = (inst.get('db_type') or '').lower()
        return {
            'id': iid,
            'name': inst.get('name', iid),
            'db_type': db_type,
            'group': inst.get('group') or 'default',
            'host': inst.get('host', ''),
            'port': inst.get('port', ''),
            'label': "%s (%s:%s)" % (inst.get('name', iid), inst.get('host', '?'), inst.get('port', '?')),
            'ts': ts,
            'conn': None, 'slowq': 0, 'qps': None, 'tps': None,
            'cache_hit_pct': None, 'lock_waits': None, 'repl_lag_s': None,
            'role': None, 'db_unique_name': None, 'db_name': None, 'open_mode': None,
            'instance_name': None, 'instance_number': None, 'dests': None,
            'transport_lag_s': None, 'apply_lag_s': None,
            'tbs': None, 'err': err, 'status': 'pending', 'counters': {},
            'stat_hint': None, 'pending': True,
            'conn_rows': [], 'slow_rows': [],
            'ssh': {
                'enabled': bool(inst.get('ssh_enabled')),
                'host': inst.get('ssh_host') or '',
                'port': inst.get('ssh_port') or 22,
                'user': inst.get('ssh_user') or '',
            },
        }

    def _apply_rates(self, snap, prev, interval):
        """纯速率计算：返回 ``(snap_without_counters, new_prev)``。

        由统一采集器调用（prev 状态存 UnifiedStore），不再写任何本地缓冲。
        速率防噪三层（#63 Oracle QPS 偶发冲高）保持原逻辑：
        ① RAC / 负载均衡下采样落到不同实例 → 本轮作废；
        ② 实际间隔远小于采样周期 → 抖动样本丢弃；
        ③ 超出物理阈值 → 记为采样异常，不进 KPI 与迷你趋势。
        """
        if not snap or 'id' not in snap:
            return snap, prev
        out = dict(snap)
        ts = out.get('ts') or time.time()
        counters = out.get('counters') or {}
        if prev and counters:
            cur_id = (out.get('instance_number'), out.get('db_unique_name'))
            prev_id = (prev.get('inum'), prev.get('dbname'))
            id_mismatch = (cur_id[0] is not None and prev_id[0] is not None
                           and cur_id[0] != prev_id[0])
            dt = ts - prev['ts']
            dt_valid = dt >= interval * self.DT_MIN_RATIO
            if id_mismatch:
                out['rate_note'] = 'instance_changed'
            elif not dt_valid:
                out['rate_note'] = 'dt_too_small'
            else:
                dt = max(dt, 0.001)
                qps = self._rate(prev['counters'], counters, dt, 'qps')
                tps = self._rate(prev['counters'], counters, dt, 'tps')
                if qps is not None and qps > self.QPS_SPIKE_MAX:
                    out['qps_raw'] = round(qps, 1)
                    out['rate_note'] = 'spike'
                    qps = None
                out['qps'] = round(qps, 1) if qps is not None else None
                out['tps'] = round(tps, 1) if tps is not None else None
                if qps is None:
                    out['rate_note'] = out.get('rate_note') or 'counter_reset'
                if counters.get('hit_base'):
                    h = counters.get('hit') or 0
                    b = counters['hit_base']
                    out['cache_hit_pct'] = round(h / b * 100, 1) if b else None
        new_prev = {'ts': ts, 'counters': dict(counters),
                    'inum': out.get('instance_number'),
                    'dbname': out.get('db_unique_name')}
        # 与旧 _snap 口径一致：counters 是中间量，不进对外快照
        out.pop('counters', None)
        return out, new_prev

    def _commit_snap(self, snap):
        """兼容入口：旧代码若直接调用，仍经统一采集器落到 UnifiedStore。"""
        if not snap or 'id' not in snap:
            return snap
        if snap.get('host_res') is None:
            try:
                snap['host_res'] = _read_host_metrics(snap['id'])
            except Exception:
                snap['host_res'] = None
        from modules.monitor.unified_collector import get_unified_collector
        return get_unified_collector().commit_snap(snap)

    # ── 数据读取：投影统一存储 ──
    def get_snapshot(self):
        return self._store().get_snapshot()

    def get_trend(self):
        return self._store().get_trend()


# ═══════════════════════════════════════════════════════════
#  聚合：/api/dashboard/overview 的数据装配
# ═══════════════════════════════════════════════════════════

def _topn(items, key, n=10):
    return sorted([i for i in items if i.get(key) is not None],
                  key=lambda x: x[key], reverse=True)[:n]


# ── 宿主资源：复用常驻 MetricsCollector 的采集结果 ───────────────
# 大屏不自行发起 SSH 采集。MetricsCollector 在 web 启动时即常驻运行
# （APScheduler 每 30s tick，见 modules/web/app.py），其宿主采集已包含
# 「实例是否与 DBCheck 同机」的判定（未配 SSH 且异地 -> unavailable，不再拿
# 中心机指标冒充）与来源标注（ebpf / psutil / shell / local / unavailable）。
# 大屏只读它的存储结果：既零额外 SSH 开销（不叠加连接压垮 sshd、不拖慢采样轮），
# 也保证与首页监控卡片、AI 巡检的宿主口径完全一致。
_HOST_KEYS = ('host_cpu', 'host_cpu_iowait', 'host_mem', 'host_swap_pct',
              'host_load1', 'host_load5', 'host_load15',
              'host_disk_read_mb_s', 'host_disk_write_mb_s', 'host_disk_await_ms',
              # Windows PowerShell 通道：服务级 CPU / 内存与服务告警
              'host_os', 'host_services', 'host_services_count',
              'host_services_running', 'host_services_abnormal',
              'host_services_cpu_pct', 'host_services_mem_mb',
              'host_service_alerts', 'host_service_alert_count',
              'host_collector_source', 'host_error',
              # 数据新鲜度（统一采集器写入）：真实采样时刻 / 陈旧秒数 / 历史标记
              'host_sample_ts', 'host_last_ok_ts', 'host_age_s', 'host_stale')


def _ts_to_epoch(raw):
    """把采集快照的 ts 统一成 epoch 秒（float），解析不了返回 None。

    MetricsStore 的 ts 列是 TEXT，写入端用 datetime.now().isoformat()；早期数据
    也可能是 epoch 数字/数字字符串。大屏折线与前端缓冲只认 epoch 秒，若把 ISO
    字符串原样透出，前端 new Date(ts * 1000) 会得到 Invalid Date，时间标签被
    格式化成 'aN:aN'。naive datetime 按本地时区解释，与写入端 datetime.now()
    一致；带时区的 ISO 串由 fromisoformat 自行处理。
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw or '').strip()
    if not s:
        return None
    try:
        return float(s)                       # 兼容早期 epoch 字符串
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _read_host_metrics(iid):
    """读取指定实例最近一次宿主资源快照。

    返回 None 的情形（前端据此显示"暂无宿主数据"）：
      - 采集器未运行（get_collector() 为 None，如 web 启动时初始化失败）；
      - 该实例尚无快照；
      - 快照中不含任何宿主指标（如该实例采集失败）。
    """
    try:
        from modules.pro.metrics_collector import get_collector
        c = get_collector()
        if c is None or not hasattr(c, 'store'):
            return None
        latest = c.store.get_latest(iid)
        if not isinstance(latest, dict) or not latest:
            return None
        out = {}
        for k in _HOST_KEYS:
            v = latest.get(k)
            if v is not None:
                out[k] = v
        if not out:                       # 快照里没有任何宿主字段，仍按「无数据」处理
            return None
        # 采样时刻（不是 overview 的请求时刻）：前端据此判断两帧是否同一次采集，
        # 否则 2s/15s 轮询会把每个重复帧都当成新采样点或永远卡在末点。
        sample_ts = _ts_to_epoch(latest.get('ts'))
        # 统一采集器已在 payload 里写入**真实宿主采样时刻**（采集中断时是
        # 最后一次成功时刻）；它比帧 ts 更准确，绝不能被帧时间覆盖，否则
        # 历史数据又会被伪装成本轮新采集。仅旧数据无该字段时才回退帧 ts。
        if sample_ts and out.get('host_sample_ts') is None:
            out['host_sample_ts'] = sample_ts
        return out
    except Exception:
        return None


def _read_host_series(iid, limit=60):
    """读取指定实例最近 N 个宿主资源采样点（供大屏「小倍数」折线图使用）。

    复用常驻 MetricsCollector 的快照存储（MetricsStore.get_recent），逐点提取
    host_cpu / host_mem / host_swap_pct / host_load1。采集器每 30s 落一个点，
    默认 60 点 ≈ 30 分钟窗口（与全库 QPS 趋势 TREND_POINTS=60 同口径）。

    返回按时间升序的 [{'ts','cpu','mem','swap','load1'}]（ts 为 epoch 秒），
    或 None（采集器未运行 / 该实例无宿主快照 / 所有点宿主采集均失败）。

    注意：本函数只读历史、不触发任何采集，因此不产生额外 SSH 开销。
    """
    try:
        from modules.pro.metrics_collector import get_collector
        c = get_collector()
        if c is None or not hasattr(c, 'store'):
            return None
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 60
        if n <= 0:
            n = 60
        # 多取一倍再过滤：宿主采集失败的点（四项皆 None）在 SQL 层看不出区别，
        # 若只取 n 行再过滤，遇到近期采集失败时有效点会不足 n，30 分钟窗口会
        # 悄然缩水（例如 SSH 断开 20 分钟 → 窗口只剩 10 分钟，易被误读）。
        # 因此先多取、过滤、再截取最近 n 个有效点；上限 480 行控制解析开销。
        rows = c.store.get_recent(iid, min(n * 2, 480))
        if not rows:
            return None
        pts = []
        for r in rows:
            m = r.get('metrics')
            if not isinstance(m, dict):
                continue
            # 宿主采集中断期间统一采集器会保留最后一次成功数值并标 stale；
            # 这些帧不是新采样点，绝不能进历史序列——否则折线会把同一组旧值
            # 沿时间轴平铺成一条「看似一直在实时更新」的直线。
            if m.get('host_stale'):
                continue
            pt = {'cpu': m.get('host_cpu'), 'mem': m.get('host_mem'),
                  'swap': m.get('host_swap_pct'), 'load1': m.get('host_load1')}
            # 该点宿主采集整体失败（四项皆空）时跳过——否则会在折线上留下
            # 一串取值为 null 的断点，看上去像"指标掉零"。
            if all(pt[k] is None for k in ('cpu', 'mem', 'swap', 'load1')):
                continue
            ts = _ts_to_epoch(r.get('ts'))
            if ts is None:
                # 时间戳解析不了的点不进序列：前端会把它算成 Invalid Date，
                # 折线的 tooltip 标题会变成 'aN:aN'（('0'+NaN).slice(-2)）。
                continue
            pts.append({'ts': ts, 'cpu': pt['cpu'], 'mem': pt['mem'],
                        'swap': pt['swap'], 'load1': pt['load1']})
        # get_recent 返回升序，截取最近 n 个有效点
        return pts[-n:] if pts else None
    except Exception:
        return None


def _build_nodes(snap):
    """从快照装配拓扑节点（含 tbs 明细，供 TopN 表空间使用）。"""
    nodes = []
    for iid, s in snap.items():
        worst_tbs = None
        for t in (s.get('tbs') or []):
            fp = t.get('free_pct')
            if fp is not None and (worst_tbs is None or fp < worst_tbs):
                worst_tbs = fp
        nodes.append({
            'id': iid, 'name': s['name'], 'db_type': s['db_type'],
            'group': s['group'], 'host': s['host'], 'port': s['port'],
            'label': s['label'], 'status': s['status'], 'err': s.get('err'),
            'conn': s.get('conn'), 'slowq': s.get('slowq', 0),
            'qps': s.get('qps'), 'tps': s.get('tps'),
            'cache_hit_pct': s.get('cache_hit_pct'),
            'lock_waits': s.get('lock_waits'), 'repl_lag_s': s.get('repl_lag_s'),
            'role': s.get('role'), 'db_unique_name': s.get('db_unique_name'),
            'db_name': s.get('db_name'), 'open_mode': s.get('open_mode'),
            'instance_name': s.get('instance_name'), 'instance_number': s.get('instance_number'),
            'dests': s.get('dests'),
            'transport_lag_s': s.get('transport_lag_s'), 'apply_lag_s': s.get('apply_lag_s'),
            'tbs': s.get('tbs'), 'tbs_free_pct': worst_tbs,
            'spark': s.get('spark') or [],
            'stat_hint': s.get('stat_hint'),
            'ssh': s.get('ssh'),
            'host_res': s.get('host_res'),
        })
    return nodes


def _filter_nodes_by_group(nodes, group):
    """按数据源分组筛选大屏节点；group 为空表示全部环境。"""
    if not group:
        return list(nodes)
    return [n for n in nodes if (n.get('group') or 'default') == group]


def _all_group_names(nodes):
    """节点中出现过的全部分组名，稳定排序后供大屏下拉选项使用。"""
    return sorted(set((n.get('group') or 'default') for n in nodes))


def assemble_overview(nodes, collector, trend=None):
    """把节点列表装配成大屏一次请求所需的全部数据（KPI/分组/TopN/告警/趋势）。

    实时（build_overview）与历史回放（build_history_overview）共用此函数，
    区别在于 nodes 的来源与 trend 的提供方式。"""
    ts = time.time()
    trend = trend if trend is not None else (collector.get_trend() if hasattr(collector, 'get_trend') else [])

    total = len(nodes)
    online = sum(1 for n in nodes if n['status'] in ('ok', 'warn', 'crit', 'unsupported'))
    pending = sum(1 for n in nodes if n['status'] == 'pending')
    warn = sum(1 for n in nodes if n['status'] == 'warn')
    crit = sum(1 for n in nodes if n['status'] == 'crit')
    down = sum(1 for n in nodes if n['status'] == 'down')
    kpis = {
        'total': total, 'online': online, 'pending': pending,
        'warn': warn, 'crit': crit, 'down': down,
        'total_conn': sum((n.get('conn') or {}).get('total') or 0 for n in nodes),
        'total_qps': round(sum(n['qps'] or 0 for n in nodes), 1),
        'total_tps': round(sum(n['tps'] or 0 for n in nodes), 1),
        'probe_running': collector.running,
    }

    groups = {}
    for n in nodes:
        g = groups.setdefault(n['group'], {'name': n['group'], 'total': 0,
                                           'ok': 0, 'warn': 0, 'crit': 0, 'down': 0})
        g['total'] += 1
        g[n['status']] = g.get(n['status'], 0) + 1

    conn_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                  'value': (n.get('conn') or {}).get('usage_pct') or 0,
                  'detail': '%s/%s' % ((n.get('conn') or {}).get('total', 0),
                                       (n.get('conn') or {}).get('max_conn', 0))}
                 for n in nodes if n.get('conn')]
    tbs_list = []
    tbs_used_list = []
    for n in nodes:
        for t in (n.get('tbs') or []):
            fp = t.get('free_pct')
            if fp is not None:
                tbs_list.append({'id': n['id'], 'name': '%s · %s' % (n['name'], t['name']),
                                 'db_type': n['db_type'], 'value': fp,
                                 'total_mb': t.get('total_mb'), 'unit': '%', 'kind': 'free'})
            elif t.get('total_mb') is not None:
                # MySQL/PG/SQLServer/GBase/ClickHouse 等只能采到「已用大小」，
                # 无 free 列 → free_pct 恒为 None；退而求其次按已用 MB 排名，
                # 让表空间图对这类库也有数据（标题与单位随 kind 自适应）。
                tbs_used_list.append({'id': n['id'], 'name': '%s · %s' % (n['name'], t['name']),
                                      'db_type': n['db_type'], 'value': t['total_mb'],
                                      'total_mb': t.get('total_mb'), 'unit': 'MB', 'kind': 'used'})
    tbs_worst = sorted(tbs_list, key=lambda x: x['value'])[:10]
    tbs_used = sorted(tbs_used_list, key=lambda x: x['value'], reverse=True)[:10]
    repl_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                  'value': n.get('repl_lag_s')} for n in nodes if n.get('repl_lag_s') is not None]
    lock_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                  'value': n.get('lock_waits')} for n in nodes if n.get('lock_waits') is not None]
    # 连接数 / 活跃会话 TopN（连接数为主排序，会话数按实例名对齐展示）
    conn_total_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                        'value': (n.get('conn') or {}).get('total') or 0}
                       for n in nodes if n.get('conn')]
    sess_list = [{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                  'value': (n.get('conn') or {}).get('active') or 0}
                 for n in nodes if n.get('conn')]

    topn = {
        'qps': _topn([{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                       'value': n['qps']} for n in nodes], 'value'),
        'conn_util': _topn(conn_list, 'value'),
        'conn_total': _topn(conn_total_list, 'value'),
        'sess_active': _topn(sess_list, 'value'),
        'slowq': _topn([{'id': n['id'], 'name': n['name'], 'db_type': n['db_type'],
                         'value': n['slowq']} for n in nodes], 'value'),
        'tbs_worst': tbs_worst,
        'tbs_used': tbs_used,
        'repl_lag': _topn(repl_list, 'value'),
        'lock_wait': _topn(lock_list, 'value'),
    }

    alerts = []
    for n in nodes:
        if n['status'] == 'down':
            alerts.append({'level': 'crit', 'id': n['id'], 'name': n['name'],
                           'msg': '实例探活失败' + (('：' + n['err']) if n.get('err') else ''),
                           'ts': n.get('ts') or ts})
            continue
        cu = (n.get('conn') or {}).get('usage_pct') or 0
        if n.get('tbs_free_pct') is not None and n['tbs_free_pct'] <= collector.CRIT_TBS_FREE:
            alerts.append({'level': 'crit', 'id': n['id'], 'name': n['name'],
                           'msg': '表空间剩余 %.1f%%' % n['tbs_free_pct'], 'ts': ts})
        elif n.get('tbs_free_pct') is not None and n['tbs_free_pct'] <= collector.WARN_TBS_FREE:
            alerts.append({'level': 'warn', 'id': n['id'], 'name': n['name'],
                           'msg': '表空间剩余 %.1f%%' % n['tbs_free_pct'], 'ts': ts})
        if cu >= collector.CRIT_CONN_UTIL:
            alerts.append({'level': 'crit', 'id': n['id'], 'name': n['name'],
                           'msg': '连接利用率 %.0f%%' % cu, 'ts': ts})
        elif cu >= collector.WARN_CONN_UTIL:
            alerts.append({'level': 'warn', 'id': n['id'], 'name': n['name'],
                           'msg': '连接利用率 %.0f%%' % cu, 'ts': ts})
        lag = n.get('repl_lag_s')
        if lag is not None and lag >= collector.WARN_REPL_LAG:
            alerts.append({'level': 'crit' if lag >= collector.CRIT_REPL_LAG else 'warn',
                           'id': n['id'], 'name': n['name'],
                           'msg': '复制延迟 %ds' % lag, 'ts': ts})
        if (n.get('lock_waits') or 0) >= collector.WARN_LOCKS:
            alerts.append({'level': 'warn', 'id': n['id'], 'name': n['name'],
                           'msg': '锁等待 %d' % n['lock_waits'], 'ts': ts})
    lvl_rank = {'crit': 0, 'warn': 1}
    alerts.sort(key=lambda a: (lvl_rank.get(a['level'], 9), -a['ts']))

    return {
        'ok': True, 'ts': ts,
        'kpis': kpis,
        'groups': list(groups.values()),
        'topn': topn,
        'trend': [{'ts': t['ts'], 'qps': t['qps']} for t in trend],
        'alerts': alerts[:50],
        'nodes': nodes,
        # OceanBase 表空间统计取自 information_schema，需 ANALYZE TABLE 后才准确；
        # 拓扑中存在 OceanBase 实例时，前端在表空间面板常驻一条弱提示。
        'tbs_analyze_hint': any(normalize_db_type(n.get('db_type')) == 'oceanbase' for n in nodes),
    }


def build_overview(collector, group=None):
    """把采样器快照装配成大屏一次请求所需的全部数据（实时）。

    group 非空时只保留该数据源分组的实例：KPI / TopN / 告警 / 拓扑 /
    健康矩阵 / 表空间 / 宿主资源均由 assemble_overview 从过滤后的 nodes
    重新派生，采集与告警规则本身不受影响。
    all_groups 始终来自全量节点，保证筛选后下拉仍可切回其它分组。
    """
    all_nodes = _build_nodes(collector.get_snapshot())
    ov = assemble_overview(_filter_nodes_by_group(all_nodes, group), collector)
    ov['all_groups'] = _all_group_names(all_nodes)
    ov['group'] = group or ''
    return ov


def _node_from_history_sample(s):
    """把一条历史样本还原为与实时节点同构的 node dict。

    注意：历史库仅存不透明 iid，未持久化实例名/地址；name/label/host/port
    先回退为 iid/空，由 build_history_overview 从实时快照按 iid 关联补全。"""
    conn = {
        'total': s.get('conn_total'), 'max_conn': s.get('conn_max'),
        'usage_pct': s.get('conn_usage_pct'),
        'active': s.get('conn_active'), 'idle': s.get('conn_idle'),
        'blocked': s.get('conn_blocked'),
    }
    try:
        tbs = json.loads(s['tbs_json']) if s.get('tbs_json') else []
    except Exception:
        tbs = []
    try:
        spark = json.loads(s['spark_json']) if s.get('spark_json') else []
    except Exception:
        spark = []
    return {
        'id': s['iid'], 'name': s['iid'], 'db_type': s.get('db_type'),
        'group': s.get('grp'), 'host': '', 'port': '', 'label': s['iid'],
        'status': s.get('status'), 'err': None,
        'conn': conn, 'slowq': s.get('slowq', 0),
        'qps': s.get('qps'), 'tps': s.get('tps'),
        'cache_hit_pct': s.get('cache_hit_pct'),
        'lock_waits': s.get('lock_waits'), 'repl_lag_s': s.get('repl_lag_s'),
        'tbs': tbs, 'tbs_free_pct': s.get('tbs_free_pct'),
        'spark': spark, 'stat_hint': None,
        'ssh': {'enabled': False, 'host': '', 'port': 22, 'user': ''},
    }


def build_history_overview(from_ts, to_ts, at_ts, group=None):
    """历史回放：在 [from_ts, to_ts] 内，按 at_ts 时间点（取 ≤at 的最新样本，
    若无则取区间内最早样本）重建各实例状态，装配为与实时同构的 overview。

    group 非空时同步过滤回放节点和历史 QPS 趋势（按该组实例 iid 聚合）。"""
    hs = history_store.get_history_store()
    samples = hs.query(from_ts=from_ts, to_ts=to_ts, limit=20000)

    best = {}      # iid → ts ≤ at_ts 的最新样本
    earliest = {}  # iid → 区间内最早样本（兜底）
    for s in samples:
        iid = s['iid']
        if s['ts'] <= at_ts:
            if iid not in best or s['ts'] > best[iid]['ts']:
                best[iid] = s
        if iid not in earliest or s['ts'] < earliest[iid]['ts']:
            earliest[iid] = s
    chosen = []
    for iid in set(list(best.keys()) + list(earliest.keys())):
        chosen.append(best.get(iid) or earliest.get(iid))
    nodes = [_node_from_history_sample(c) for c in chosen if c]

    # 身份字段（name/label/host/port/group/ssh）属敏感白名单之外，不入历史库；
    # 回放时从实时快照按 iid 关联补全，保证拓扑分组/卡片命名与实时一致。
    collector = get_screen_collector()
    live = collector.get_snapshot() if collector else {}
    for n in nodes:
        s0 = live.get(n['id'])
        if not s0:
            continue   # 实例已删除：回退为 iid 展示
        for k in ('name', 'label', 'host', 'port', 'group', 'ssh'):
            v = s0.get(k)
            if v not in (None, ''):
                n[k] = v

    # 分组来自实时快照（历史库未持久化身份字段），先用它确定本组实例集合
    group_ids = None
    if group:
        group_ids = set(iid for iid, s0 in live.items()
                        if (s0.get('group') or 'default') == group)
        nodes = [n for n in nodes if (n.get('group') or 'default') == group]

    # 历史趋势：按 ts 桶聚合 qps 之和；分组模式下只累计该组实例
    agg = {}
    for s in samples:
        if group_ids is not None and s['iid'] not in group_ids:
            continue
        k = round(s['ts'])
        agg[k] = agg.get(k, 0) + (s.get('qps') or 0)
    trend = [{'ts': t, 'qps': round(agg[t], 1)} for t in sorted(agg)]

    ov = assemble_overview(nodes, collector, trend=trend)
    ov['all_groups'] = sorted(set((s0.get('group') or 'default')
                                   for s0 in live.values()))
    ov['group'] = group or ''
    return ov

# ═══════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════

_screen_collector = None
_screen_lock = threading.Lock()


def get_screen_collector():
    global _screen_collector
    if _screen_collector is None:
        with _screen_lock:
            if _screen_collector is None:
                _screen_collector = ScreenCollector()
    return _screen_collector
