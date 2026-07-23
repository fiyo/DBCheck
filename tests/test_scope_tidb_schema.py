# -*- coding: utf-8 -*-
"""TiDB 单库巡检过滤测试（与 MySQL/MariaDB 行为一致）。

TiDB 复用 MySQL 协议，但其 index_stats / unused_indexes 使用
information_schema.tidb_indexes（按 TABLE_SCHEMA 过滤），因此 scope_mysql_schema
对其增加了第三重回退。本文件验证：7 个目标键过滤、仅目标键变动、空库名不过滤、
注入转义，以及 TiDBInspector._customize_queries 实际调用了单库过滤（链路闭环）。
"""
import copy

from inspection_engine import scope_mysql_schema
from main_tidb import TiDBInspector

TARGETS = (
    "db_size",
    "table_size",
    "stale_tables",
    "table_fragmentation",
    "db_privileges",
    "index_stats",
    "unused_indexes",
)


def test_tidb_target_keys_filtered(tidb_sql_dict):
    out = scope_mysql_schema(copy.deepcopy(tidb_sql_dict), "mydb")
    for k in TARGETS:
        assert k in out, "缺失目标键 %s" % k
        sql = out[k].upper()
        assert "TABLE_SCHEMA = 'MYDB'" in sql or "DB = 'MYDB'" in sql, \
            "%s 未注入单库过滤: %s" % (k, out[k])


def test_tidb_only_targets_changed(tidb_sql_dict):
    out = scope_mysql_schema(copy.deepcopy(tidb_sql_dict), "mydb")
    changed = {k for k in tidb_sql_dict if tidb_sql_dict[k] != out.get(k)}
    assert changed == (set(TARGETS) & set(tidb_sql_dict))


def test_tidb_empty_no_injection():
    base = {
        "db_size": "SELECT table_schema FROM information_schema.TABLES WHERE table_schema NOT IN ('mysql')",
        "db_privileges": "SELECT * FROM mysql.db",
        "index_stats": "SELECT * FROM information_schema.tidb_indexes",
    }
    d = copy.deepcopy(base)
    scope_mysql_schema(d, "")
    scope_mysql_schema(d, None)
    assert d == base


def test_tidb_quote_escaping():
    d = {"db_size": "SELECT 1 FROM information_schema.TABLES"}
    scope_mysql_schema(d, "my'db")
    assert "TABLE_SCHEMA = 'MY''DB'" in d["db_size"].upper()


def test_tidb_inspector_wires_scope():
    """TiDBInspector 实例化后，_customize_queries 必须真正注入单库过滤。"""
    insp = TiDBInspector("h", 4000, "u", "p", "mydb")
    sample = {
        "db_size": "SELECT table_schema FROM information_schema.TABLES WHERE table_schema NOT IN ('mysql')",
        "db_privileges": "SELECT * FROM mysql.db",
        "index_stats": "SELECT * FROM information_schema.tidb_indexes",
    }
    insp._customize_queries(sample)
    assert "TABLE_SCHEMA = 'MYDB'" in sample["db_size"].upper()
    assert "TABLE_SCHEMA = 'MYDB'" in sample["index_stats"].upper()
