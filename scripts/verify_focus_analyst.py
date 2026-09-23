# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""定向分析（focus_analyst）纯逻辑验证：主题识别 + 编排 + replan 抑制 + 过滤。"""
import sys

sys.path.insert(0, ".")

from modules.intelligence.planner import detect_focus_topic, plan_sequence, replan
from modules.intelligence.context import SharedContext, Finding

cases = [
    ("分析一下数据库连接数", "connection"),
    ("排查一下锁等待情况", "lock"),
    ("分析下慢查询为什么多", "slow_query"),
    ("帮我分析一下表空间容量", "capacity"),
    ("分析一下内存使用", "memory"),
    ("看一下主从复制延迟", "replication"),
    ("全面巡检 HGDB-测试", None),          # 广谱目标 → 不定向
    ("对 HGDB-测试 做一次深度诊断", None),   # 无主题词 → 不定向
    ("数据库里有多少张表", None),            # NL 查询 → 交给 nl_query_expert
    ("列一下所有数据源", None),
]
ok = True
for goal, expect in cases:
    got = detect_focus_topic(goal)
    tid = got["id"] if got else None
    mark = "OK  " if tid == expect else "FAIL"
    if tid != expect:
        ok = False
    print(f"{mark} {goal!r} -> {tid} (expect {expect})")

from modules.intelligence.hub import get_hub

hub = get_hub()
reg = hub.registry
caps = hub.capabilities()
print("CAPS", len(caps), "focus_analyst registered:", any(c["id"] == "focus_analyst" for c in caps))
assert any(c["id"] == "focus_analyst" for c in caps)

ctx = SharedContext(goal="分析一下数据库连接数", target="i1", inputs={})
plan = plan_sequence(ctx, reg, advisor=None)
print("FOCUS_PLAN", plan.sequence, "| focus_mode =", ctx.inputs.get("focus_mode"), "| topic =", ctx.inputs.get("focus_topic"))
assert "focus_analyst" in plan.sequence and ctx.inputs.get("focus_mode") is True
assert plan.sequence == [s for s in ("monitor_sentinel", "inspection_expert", "focus_analyst")]

ctx2 = SharedContext(goal="全面巡检这个库", target="i1", inputs={})
plan2 = plan_sequence(ctx2, reg, advisor=None)
print("BROAD_PLAN", plan2.sequence, "| focus_mode =", ctx2.inputs.get("focus_mode"))
assert ctx2.inputs.get("focus_mode") is None

# replan 在定向模式下必须抑制（即便出现 sql 等标签）
ctx.findings = [Finding(source="x", category="risk", severity="warning",
                        title="SQL 慢", detail="存在慢查询", tags=["sql"])]
p3 = replan(ctx, plan, reg)
print("REPLAN_IN_FOCUS ->", p3, "(expect None)")
assert p3 is None

# _finalize 焦点过滤
ctx.plan = []
ctx.review = None
res = hub._finalize(ctx, plan)
print("FINALIZE findings:", [f["source"] for f in res["findings"]],
      "| side:", len(res.get("side_findings") or []),
      "| focus:", bool(res.get("focus")))
assert res["focus"] is not None and res["focus"]["topic"] == "connection"
assert all("SQL 慢" not in (f["title"]) for f in res["findings"])  # 无关发现被折叠
assert len(res.get("side_findings") or []) == 1

print("ALL_LOGIC_PASS" if ok else "SOME_CASE_FAIL")

# ── 新增回归：意图词「巡检」必须命中，且精准抽取 context 数据段 ──
from modules.intelligence.specialists.focus_analyst import (
    _extract_context_section, FOCUS_CONTEXT_PATTERNS,
)

# 「巡检连接数」此前因意图词缺「巡检」而不触发定向分析 → 走全链路甩出无关结果
new_cases = [
    ("巡检连接数", "connection"),
    ("巡检一下连接数", "connection"),
    ("诊断内存使用情况", "memory"),
    ("排查锁等待", "lock"),
]
for goal, expect in new_cases:
    got = detect_focus_topic(goal)
    tid = got["id"] if got else None
    mark = "OK  " if tid == expect else "FAIL"
    if tid != expect:
        ok = False
    print(f"{mark} {goal!r} -> {tid} (expect {expect})")

# 「全面巡检」仍属广谱，不进入定向分析
broad = detect_focus_topic("全面巡检这个库")
assert broad is None, "全面巡检 不应触发定向分析"
print("OK  全面巡检这个库 -> None (expect None)")

# 精准抽取：连接主题只取 sessions/session_limit/aborted，排除 tablespace/sga
ctx_sample = {
    "sessions": [{"ID": 12, "USER": "app"}],
    "session_limit": [{"VARIABLE_NAME": "max_connections", "VALUE": "151"}],
    "aborted": [{"name": "Aborted_connects", "value": 3}],
    "tablespace": [{"TS": "SYSTEM", "USED_PCT": 70}],
    "sga": [{"POOL": "Buffer", "BYTES": 123456}],
}
conn_lines = _extract_context_section(ctx_sample, FOCUS_CONTEXT_PATTERNS["connection"])
assert any("sessions" in l for l in conn_lines)
assert any("session_limit" in l for l in conn_lines)
assert any("aborted" in l for l in conn_lines)
assert not any("tablespace" in l for l in conn_lines)
assert not any("sga" in l for l in conn_lines)
print(f"OK  context 段抽取（连接）命中 {len(conn_lines)} 行且不含容量/内存段")

# HGDB 插件路径回归：模板连接与会话章节数据存于 hgdb_conn_summary /
# hgdb_connections；此前插件路径 _run_plugin_inspection 返回 dict 不带
# context，定向分析专员拿不到任何巡检数据 →「暂无与主题相关的数据」。
hgdb_ctx = {
    "hgdb_version": [{"VERSION": "HighGo Database V9"}],
    "hgdb_uptime": [{"START_TIME": "2026-09-23", "UPTIME": "3 days"}],
    "hgdb_conn_summary": [
        {"STATE": "active", "CONN_COUNT": 5},
        {"STATE": "idle", "CONN_COUNT": 12},
    ],
    "hgdb_connections": [{"PID": 101, "USENAME": "highgo", "STATE": "active"}],
    "hgdb_settings": [{"NAME": "max_connections", "SETTING": "100"}],
    "_chapters": [{"chapter_title_zh": "连接与会话"}],
}
hgdb_lines = _extract_context_section(hgdb_ctx, FOCUS_CONTEXT_PATTERNS["connection"])
assert any("hgdb_conn_summary" in l for l in hgdb_lines), "HGDB 连接分布应命中"
assert any("hgdb_connections" in l for l in hgdb_lines), "HGDB 会话清单应命中"
assert not any("hgdb_version" in l for l in hgdb_lines), "版本段不应混入"
assert not any("_chapters" in l for l in hgdb_lines)
print(f"OK  HGDB 模板 context 连接段抽取命中 {len(hgdb_lines)} 行且不含版本/元数据段")

# ── 多库型键名覆盖矩阵：各库型代表键必须被对应主题模式命中/排除 ──
# 此前各插件库型（Redis/MongoDB/MSSQL/DB2/ClickHouse/HGDB/UXDB）键名前缀
# 各异，主题模式只按 MySQL 键名设计导致漏抽。逐一锁定。
DB_KEY_MATRIX = [
    # (库型, 主题, 必须命中的键, 必须排除的键)
    ("mysql",   "connection", ["sessions", "session_limit", "aborted"], ["tablespace"]),
    ("pg",      "lock",       ["lock_blocking", "blocked", "trx"], ["sessions"]),
    ("oracle",  "lock",       ["top_waits", "wait_class"], []),
    ("oracle",  "slow_query", ["long_sql", "top_sql_cpu"], ["profile_pwd"]),  # 密码策略不得误伤
    ("dm",      "capacity",   ["tablespace", "temp_ts", "datafiles"], ["sga"]),
    ("hgdb",    "connection", ["hgdb_conn_summary", "hgdb_connections"], ["hgdb_version"]),
    ("hgdb",    "capacity",   ["hgdb_database_size", "hgdb_tables_size"], ["hgdb_settings"]),
    ("hgdb",    "replication",["hgdb_replication", "hgdb_wal_level", "hgdb_archiver"], []),
    ("uxdb",    "connection", ["uxdb_conn_summary", "uxdb_connections"], []),
    ("uxdb",    "lock",       ["uxdb_locks", "uxdb_lock_waits"], []),
    ("uxdb",    "replication",["uxdb_replication", "uxdb_wal_level", "uxdb_archiver"], []),
    ("redis",   "connection", ["redis_clients", "redis_conn_pct"], ["redis_memory"]),
    ("redis",   "memory",     ["redis_memory"], []),
    ("redis",   "slow_query", ["redis_slowlog"], []),
    ("mongodb", "connection", ["mongodb_connections"], []),
    ("mongodb", "memory",     ["mongodb_memory"], []),
    ("mongodb", "capacity",   ["mongodb_db_stats"], []),
    ("mongodb", "slow_query", ["mongodb_profile"], []),
    ("sqlserver_jdbc", "connection",  ["mssql_sessions", "mssql_max_conn_pct", "active_connections"], []),
    ("sqlserver_jdbc", "lock",        ["mssql_locks", "mssql_wait_stats"], []),
    ("sqlserver_jdbc", "memory",      ["mssql_dbmemory"], []),
    ("sqlserver_jdbc", "capacity",    ["mssql_tablespaces"], []),
    ("sqlserver_jdbc", "replication", ["mssql_always_on"], []),
    ("sqlserver_jdbc", "slow_query",  ["mssql_top_sql"], []),
    ("db2",     "slow_query", ["db2_pkg_cache_stmt"], []),
    ("db2",     "capacity",   ["db2_tablespaces"], []),
    ("db2",     "lock",       ["db2_lockwaits", "db2_locks"], []),
    ("clickhouse", "slow_query",  ["clickhouse_slow_query_count", "clickhouse_query_log_stats"], []),
    ("clickhouse", "replication", ["clickhouse_replicas"], []),
    ("clickhouse", "capacity",    ["clickhouse_disks", "clickhouse_storage_policies"], []),
]
for db, topic_id, must, must_not in DB_KEY_MATRIX:
    pat = FOCUS_CONTEXT_PATTERNS[topic_id]
    for k in must:
        assert pat.search(k), f"{db}/{topic_id} 应命中键 {k}"
    for k in must_not:
        assert not pat.search(k), f"{db}/{topic_id} 不应命中键 {k}"
print(f"OK  多库型键名覆盖矩阵 {len(DB_KEY_MATRIX)} 组全过")

print("ALL_LOGIC_PASS" if ok else "SOME_CASE_FAIL")
sys.exit(0 if ok else 1)

