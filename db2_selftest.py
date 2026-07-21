#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DB2 插件完整自测（开发用，非插件组成部分）。

验证「添加数据源 / 测试连接 / 巡检」全链路在真实 DB2（localhost:50000/testdb）上无错：
  STEP 1  插件模块导入 + JVM 启动
  STEP 2  test_connection()  -> (True, version)
  STEP 3  on_install()        -> 幂等注册模板 + 基线到 inspection.db
  STEP 4  inspection.db 校验  -> db2 模板 / 基线存在；init_default_baselines 不重复
  STEP 5  collect_data()      -> 填充 §5 db2_* 字段 + 规则标量
  STEP 6  规则引擎逐条评估     -> 每条 db2 规则不得抛异常；统计命中数
  STEP 7  smart_analyze_db2() -> 返回 list（异常降级 []）
  STEP 8  DB2SlowQueryAnalyzer + analyze_db2_indexes 直接调用

运行：在 D:/wt-pro-i18n 下 `python db2_selftest.py`
"""

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(ROOT, "plugins", "available", "db2_jdbc")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


print("=" * 64)
print("STEP 1: 导入插件模块 + 启动 JVM")
try:
    from jdbc_jvm import ensure_jvm
    ensure_jvm()
    import main_plugin as db2
    from plugin_core import PluginRegistry
    from inspection_dal import (
        get_templates_by_db_type,
        get_baselines_by_db_type,
        init_default_baselines,
    )
    from pro.rule_engine import RuleEngine
    from analyzer import smart_analyze_db2
    from slow_query_analyzer import get_slow_query_analyzer
    from index_health import get_index_health, analyze_db2_indexes
    check("模块导入 + JVM", True)
except Exception as e:
    print(f"  [FATAL] 导入/JVM 失败: {e}")
    traceback.print_exc()
    sys.exit(1)

HOST, PORT, USER, PW, DB = "localhost", 50000, "db2inst1", "password", "testdb"

print("=" * 64)
print("STEP 2: test_connection()")
try:
    ok, msg = db2.test_connection(HOST, PORT, USER, PW, DB)
    check("test_connection 返回 ok=True", ok, repr(msg))
    check("version 可读串非空", bool(msg))
except Exception as e:
    check("test_connection 未抛异常", False, str(e))
    traceback.print_exc()

print("=" * 64)
print("STEP 3: on_install() 注册模板 + 基线（幂等）")
try:
    adapter = PluginRegistry.get_plugin_instance("db2")
    check("插件已注册到 PluginRegistry", adapter is not None)
    adapter.on_install()
    check("on_install 执行无异常", True)
except Exception as e:
    check("on_install 无异常", False, str(e))
    traceback.print_exc()

print("=" * 64)
print("STEP 4: inspection.db 校验")
try:
    templates = get_templates_by_db_type("db2")
    check("db2 模板已注册", bool(templates), f"count={len(templates)}")
    baselines = get_baselines_by_db_type("db2")
    check("db2 基线已注册", bool(baselines), f"count={len(baselines)}")
    # 幂等：init_default_baselines 不应重复写入 db2 基线
    n_before = len(get_baselines_by_db_type("db2"))
    init_default_baselines()
    n_after = len(get_baselines_by_db_type("db2"))
    check("init_default_baselines 对 db2 幂等（不重复）",
          n_after == n_before, f"before={n_before} after={n_after}")
except Exception as e:
    check("inspection.db 校验", False, str(e))
    traceback.print_exc()

print("=" * 64)
print("STEP 5: collect_data() 采集 §5 字段 + 规则标量")
ctx = {}
insp = None
try:
    insp = db2.Db2JdbcInspector(HOST, PORT, USER, PW, database=DB)
    res = insp.collect_data()
    check("collect_data 返回 context（非 False）", res is not False)
    ctx = insp.context

    # 规则标量
    for k in ["db2_dbcfg_map", "db2_dbmcfg_map", "db2_tablespace_max_used",
              "db2_lockwait_count", "db2_applications_count",
              "db2_pkg_cache_max_time", "db2_pkg_cache_count",
              "db2_stale_stats_count", "db2_unused_index_count",
              "db2_version_number", "db2_instance_name", "db2_database_name"]:
        v = ctx.get(k)
        present = v is not None
        check(f"规则标量 {k} 存在", present, repr(v)[:60])
    # §5 list 字段
    for k in ["db2_version", "db2_instance", "db2_dbmcfg", "db2_dbcfg",
              "db2_tablespaces", "db2_lockwaits", "db2_applications",
              "db2_pkg_cache_stmt", "db2_indexes", "db2_index_runstats",
              "db2_dbmembers"]:
        v = ctx.get(k)
        ok_list = isinstance(v, list)
        check(f"§5 字段 {k} 为 list", ok_list, f"n={len(v) if ok_list else v}")
    # 报告章节 / 慢查询 / 索引健康 / 基线 / 智能分析
    check("报告章节 _chapters 已生成", isinstance(ctx.get("_chapters"), list),
          f"n={len(ctx.get('_chapters') or [])}")
    check("slow_query_result 非 None", ctx.get("slow_query_result") is not None)
    check("index_health_result 非 None", ctx.get("index_health_result") is not None)
    check("baseline_results 已计算", "baseline_results" in ctx,
          f"n={len(ctx.get('baseline_results') or [])}")
    check("auto_analyze（smart_analyze_db2）已执行", "auto_analyze" in ctx)
except Exception as e:
    check("collect_data 无异常", False, str(e))
    traceback.print_exc()

print("=" * 64)
print("STEP 6: 规则引擎逐条评估 db2.yaml（每条不得抛异常）")
try:
    engine = RuleEngine()
    rules = engine.get_enabled_rules("db2")
    check("db2 规则已加载", bool(rules), f"count={len(rules)}")
    triggered = 0
    err_rules = []
    for r in rules:
        rid = r.get("id", "?")
        try:
            issue = engine._run_rule(r, "db2", ctx)
            if issue:
                triggered += 1
        except Exception as ex:
            err_rules.append((rid, str(ex)))
    check("所有 db2 规则评估无异常", not err_rules,
          ("异常规则: " + ", ".join(f"{i}:{m}" for i, m in err_rules)) if err_rules else "")
    print(f"  >> 命中规则数: {triggered} / {len(rules)}")
    # analyze() 整体调用也不应抛（逐条已验证，这里做整体冒烟）
    try:
        issues_all = engine.analyze("db2", ctx)
        check("engine.analyze('db2') 整体无异常", True, f"issues={len(issues_all)}")
    except Exception as ex:
        check("engine.analyze('db2') 整体无异常", False, str(ex))
except Exception as e:
    check("规则引擎评估", False, str(e))
    traceback.print_exc()

print("=" * 64)
print("STEP 7: smart_analyze_db2() 降级安全")
try:
    sa = smart_analyze_db2(ctx)
    check("smart_analyze_db2 返回 list", isinstance(sa, list),
          f"len={len(sa)}")
except Exception as e:
    check("smart_analyze_db2 不抛异常", False, str(e))

print("=" * 64)
print("STEP 8: DB2SlowQueryAnalyzer / analyze_db2_indexes 直接调用")
try:
    if insp and insp.conn:
        sqa = get_slow_query_analyzer("db2")
        sqr = sqa.analyze(insp.conn)
        check("DB2SlowQueryAnalyzer.analyze 无异常", True,
              f"top_latency={len(getattr(sqr, 'top_sql_by_latency', []) or [])}")
        ih = analyze_db2_indexes(insp.conn, 90)
        check("analyze_db2_indexes 无异常", isinstance(ih, dict),
              f"keys={list(ih.keys()) if isinstance(ih, dict) else ih}")
    else:
        check("慢查询/索引健康直接调用", False, "inspector 连接不可用，跳过")
except Exception as e:
    check("慢查询/索引健康直接调用", False, str(e))
    traceback.print_exc()

print("=" * 64)
print("SUMMARY")
print(f"  PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
    print("  失败项:")
    for f in FAIL:
        print(f"    - {f}")
    print("\nSELFTEST FAILED")
    sys.exit(1)
else:
    print("\nALL GREEN — 添加数据源 / 测试连接 / 巡检 全链路通过")
    sys.exit(0)
