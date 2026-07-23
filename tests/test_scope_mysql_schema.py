# -*- coding: utf-8 -*-
"""MySQL / MariaDB 单库巡检 SQL 过滤（scope_mysql_schema）测试。

覆盖三类注入形态（information_schema.TABLES / mysql.db / performance_schema
与 MariaDB 的 INDEX_STATISTICS），以及空库名不过滤、注入转义。
集成测试从 data/inspection.db 读取真实查询，缺 db 时自动 skip。
"""
import copy
import os
import py_compile

from inspection_engine import scope_mysql_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS = (
    "db_size",
    "table_size",
    "stale_tables",
    "table_fragmentation",
    "db_privileges",
    "index_stats",
    "unused_indexes",
)

# ── 代表性 SQL（不依赖 inspection.db，始终可跑）─────────────────────────
TABLES_NOTIN = (
    "SELECT table_schema, SUM(data_length) FROM information_schema.TABLES "
    "WHERE table_schema NOT IN ('mysql','sys','performance_schema','information_schema') "
    "GROUP BY table_schema"
)
TABLES_EQ = "SELECT * FROM information_schema.TABLES WHERE table_schema = 'mysql'"
PRIV = "SELECT * FROM mysql.db WHERE db NOT IN ('mysql','sys')"
IDX_MYSQL = (
    "SELECT * FROM performance_schema.table_io_waits_summary_by_table "
    "WHERE object_schema NOT IN ('mysql','sys','performance_schema','information_schema')"
)
IDX_MDB = "SELECT * FROM information_schema.INDEX_STATISTICS"


def test_py_compile_source():
    for f in ["inspection_engine.py", "main_mysql.py", "main_mariadb.py"]:
        py_compile.compile(os.path.join(REPO_ROOT, f), doraise=True)


def test_tables_notin_to_eq():
    out = scope_mysql_schema({"db_size": TABLES_NOTIN}, "mydb")["db_size"]
    assert "table_schema = 'mydb'" in out and "NOT IN" not in out


def test_tables_eq_replaced():
    out = scope_mysql_schema({"table_size": TABLES_EQ}, "mydb")["table_size"]
    assert "table_schema = 'mydb'" in out and "table_schema = 'mysql'" not in out


def test_db_privileges_injection():
    out = scope_mysql_schema({"db_privileges": PRIV}, "mydb")["db_privileges"]
    assert "db = 'mydb'" in out


def test_index_stats_mysql_object_schema():
    out = scope_mysql_schema({"index_stats": IDX_MYSQL}, "mydb")["index_stats"]
    assert "object_schema = 'mydb'" in out and "NOT IN" not in out


def test_index_stats_mariadb_index_statistics():
    out = scope_mysql_schema({"index_stats": IDX_MDB}, "mydb")["index_stats"]
    assert "table_schema = 'mydb'" in out


def test_empty_and_none_no_injection():
    base = {
        "db_size": TABLES_NOTIN,
        "db_privileges": PRIV,
        "index_stats": IDX_MYSQL,
        "user_privileges": "SELECT * FROM mysql.user",
    }
    c1 = scope_mysql_schema(copy.deepcopy(base), "")
    c2 = scope_mysql_schema(copy.deepcopy(base), None)
    assert all(c1[k] == base[k] for k in base)
    assert all(c2[k] == base[k] for k in base)


def test_quote_escaping():
    esc = scope_mysql_schema({"db_size": "SELECT 1 FROM information_schema.TABLES"}, "my'db")["db_size"]
    assert "my''db" in esc or "my\\'db" in esc


def test_mysql_real_queries_integration(mysql_sql_dict):
    out = scope_mysql_schema(copy.deepcopy(mysql_sql_dict), "mydb")
    present = [t for t in TARGETS if t in mysql_sql_dict]
    filtered = [t for t in present if "mydb" in out[t]]
    assert len(filtered) == len(present) and len(present) > 0
    others = [k for k in mysql_sql_dict if k not in TARGETS]
    assert all(out[k] == mysql_sql_dict[k] for k in others)
    assert sum(1 for k in mysql_sql_dict if out[k] != mysql_sql_dict[k]) == len(present)


def test_mariadb_real_queries_integration(mariadb_sql_dict):
    out = scope_mysql_schema(copy.deepcopy(mariadb_sql_dict), "mydb")
    present = [t for t in TARGETS if t in mariadb_sql_dict]
    filtered = [t for t in present if "mydb" in out[t]]
    assert len(filtered) == len(present) and len(present) > 0
    others = [k for k in mariadb_sql_dict if k not in TARGETS]
    assert all(out[k] == mariadb_sql_dict[k] for k in others)
    assert sum(1 for k in mariadb_sql_dict if out[k] != mariadb_sql_dict[k]) == len(present)
