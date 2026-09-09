# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""智能诊断中心 · 通用实例 SQL 执行器。

为「自然语言探查专员」提供对任意数据源执行只读 SQL 的统一入口。
逻辑复用 Web 端 ``/api/execute_sql`` 各 db_type 的连接分支，并把
``*_jdbc`` 插件类型映射到其底层原生驱动（oracle_jdbc→oracledb、
sqlserver_jdbc→pyodbc、hgdb_jdbc/uxdb_jdbc→psycopg2、db2/clickhouse_jdbc→插件
get_connection），从而覆盖社区版常见的所有数据源。

所有执行强制**只读**：拒绝 DELETE/UPDATE/INSERT/DROP/ALTER/TRUNCATE/CREATE/
MERGE/GRANT/REVOKE 等写与 DDL，避免 AI 生成的 SQL 误伤生产库。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Tuple


# ── 只读 SQL 校验 ───────────────────────────────────────────────────────────
_DML_DDL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|MERGE|"
    r"GRANT|REVOKE|COMMENT\s+ON|RENAME|SET\s+SESSION|SET\s+GLOBAL|"
    r"EXEC|EXECUTE\s+IMMEDIATE|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|"
    r"CALL|LOCK\s+TABLE|UNLOCK\s+TABLES|FLUSH|PURGE|ANALYZE|VACUUM)\b",
    re.IGNORECASE,
)


def assert_readonly_sql(sql: str) -> None:
    """校验 SQL 是否为只读查询，非只读则抛 ValueError。

    允许 SELECT / WITH(CTE) / SHOW / DESC[RIBE] / EXPLAIN(部分) 等读操作。
    """
    s = (sql or "").strip()
    if not s:
        raise ValueError("SQL 不能为空")
    # 去掉行内注释 /* */ 与 -- 到行尾，避免注释里藏写语句绕过
    stripped = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    stripped = re.sub(r"--[^\n]*", " ", stripped)
    stripped = re.sub(r"#[^\n]*", " ", stripped)  # mysql 行注释
    upper = stripped.upper()
    # 允许的首指令白名单（前缀匹配）
    first = upper.lstrip()
    if first.startswith(("SELECT", "WITH", "SHOW", "DESC", "DESCRIBE",
                         "EXPLAIN", "VALUES", "PRAGMA", "USE ")):
        # EXPLAIN ANALYZE 会真正执行，禁止
        if first.startswith("EXPLAIN") and "ANALYZE" in upper:
            raise ValueError("禁止 EXPLAIN ANALYZE（会真实执行写）")
        if _DML_DDL_RE.search(stripped):
            raise ValueError("仅允许只读查询，SQL 中包含写/DDL 关键字")
        return
    raise ValueError("仅允许 SELECT / WITH / SHOW / DESC / EXPLAIN 等只读语句")


# ── SQL 执行计划验证 ─────────────────────────────────────────────────────────
def validate_sql_with_explain(db_info: Dict[str, Any], sql: str) -> Dict[str, Any]:
    """用 EXPLAIN 验证 SQL 是否可执行。

    返回: {"ok": True, "plan": "执行计划摘要"} 或 {"ok": False, "error": "..."}
    """
    db_type = (db_info.get("db_type", "") or "").replace("oracle_full", "oracle")
    t = db_type.lower()

    # 构造 EXPLAIN 语句
    explain_sql = None
    if t in ("oracle", "oracle_jdbc"):
        # Oracle: 使用 EXPLAIN PLAN FOR
        explain_sql = f"EXPLAIN PLAN FOR {sql}"
    elif t in ("mysql", "tidb", "mariadb", "oceanbase", "tdsqlc_mysql"):
        # MySQL: 使用 EXPLAIN
        explain_sql = f"EXPLAIN {sql}"
    elif t in ("postgresql", "pg", "ivorysql", "kingbase", "hgdb", "hgdb_jdbc",
               "uxdb", "uxdb_jdbc"):
        # PostgreSQL: 使用 EXPLAIN
        explain_sql = f"EXPLAIN {sql}"
    elif t in ("sqlserver", "sqlserver_jdbc"):
        # SQL Server: 使用 SET SHOWPLAN_ALL ON + 执行计划
        explain_sql = None  # SQL Server 特殊处理
    elif t == "dm":
        # 达梦: 使用 EXPLAIN
        explain_sql = f"EXPLAIN {sql}"

    if not explain_sql:
        # 不支持的数据库类型，跳过验证
        return {"ok": True, "skipped": True, "reason": f"不支持 EXPLAIN 的数据库类型: {db_type}"}

    # 执行 EXPLAIN（捕获异常）
    try:
        # 调用已有的执行器运行 EXPLAIN
        result = execute_instance_query(db_info, explain_sql, limit=1)
        if not result.get("ok"):
            return {"ok": False, "error": f"EXPLAIN 失败: {result.get('error', '未知错误')}"}
        # EXPLAIN 成功，SQL 可以执行
        return {"ok": True, "plan": "EXPLAIN 验证通过"}
    except Exception as e:
        return {"ok": False, "error": f"SQL 验证失败: {e}"}


# ── 各类驱动连接 + 执行 ─────────────────────────────────────────────────────
def _fetch(cur, limit: int) -> Tuple[List[str], List[List[Any]]]:
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchmany(limit) if limit and limit > 0 else cur.fetchall()
    out: List[List[Any]] = []
    for r in rows:
        out.append(list(r) if not isinstance(r, dict) else [r.get(c) for c in cols])
    return cols, out


def _exec_oracledb(db_info: Dict[str, Any], sql: str, limit: int):
    import oracledb
    host = db_info.get("host", "")
    port = int(db_info.get("port", 1521))
    svc = db_info.get("service_name", "") or ""
    sid = db_info.get("sid", "") or ""
    if svc:
        dsn = oracledb.makedsn(host=host, port=port, service_name=svc)
    elif sid:
        dsn = oracledb.makedsn(host=host, port=port, sid=sid)
    else:
        dsn = f"{host}:{port}/orcl"
    sysdba = bool(db_info.get("sysdba", False))
    extra = {"mode": oracledb.SYSDBA} if sysdba else {}
    try:
        conn = oracledb.connect(
            user=db_info.get("user", ""),
            password=db_info.get("password") or "",
            dsn=dsn, **extra,
        )
    except Exception:
        # 部分环境需要 thick 客户端，尝试初始化后重试
        try:
            oracledb.init_client()
            conn = oracledb.connect(
                user=db_info.get("user", ""),
                password=db_info.get("password") or "",
                dsn=dsn, **extra,
            )
        except Exception as e:
            raise
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return _fetch(cur, limit)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _exec_pymysql(db_info, sql, limit, db_default="INFORMATION_SCHEMA"):
    import pymysql
    db_name = db_info.get("database") or db_default
    conn = pymysql.connect(
        host=db_info.get("host", ""), port=int(db_info.get("port", 3306)),
        user=db_info.get("user", ""), password=db_info.get("password", ""),
        database=db_name, charset="utf8mb4", connect_timeout=10,
    )
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(sql)
        cols, rows = _fetch(cur, limit)
        # DictCursor 返回 dict，转成按列顺序的 list
        rows = [[r.get(c) for c in cols] for r in rows]
        return cols, rows
    finally:
        conn.close()


def _exec_psycopg(db_info, sql, limit, db_default="postgres"):
    import psycopg2
    db_type = (db_info.get("db_type", "") or "").lower()
    db_name = db_info.get("database") or (
        "highgo" if "hgdb" in db_type else (
            "ivorysql" if db_type == "ivorysql" else "postgres"))
    conn = psycopg2.connect(
        host=db_info.get("host", ""), port=int(db_info.get("port", 5432)),
        user=db_info.get("user", ""), password=db_info.get("password", ""),
        dbname=db_name, connect_timeout=10,
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return _fetch(cur, limit)
    finally:
        conn.close()


def _exec_pyodbc(db_info, sql, limit, database=""):
    conn_str = (
        "DRIVER={{ODBC Driver 17 for SQL Server}};"
        "SERVER={host},{port};UID={user};PWD={pwd};"
    ).format(
        host=db_info.get("host", ""), port=int(db_info.get("port", 1433)),
        user=db_info.get("user", ""), pwd=db_info.get("password", ""),
    )
    if database:
        conn_str += f"DATABASE={database};"
    import pyodbc
    conn = pyodbc.connect(conn_str, timeout=10)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [col[0] for col in cur.description] if cur.description else []
        rows = cur.fetchmany(limit) if limit and limit > 0 else cur.fetchall()
        return cols, [list(r) for r in rows]
    finally:
        conn.close()


def _exec_dm(db_info, sql, limit):
    import dmPython
    conn = dmPython.connect(
        user=db_info.get("user", ""), password=db_info.get("password", ""),
        server=db_info.get("host", ""), port=int(db_info.get("port", 5236)),
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return _fetch(cur, limit)
    finally:
        conn.close()


def _exec_yasdb(db_info, sql, limit):
    import yasdb
    conn = yasdb.connect(
        host=db_info.get("host", ""), port=int(db_info.get("port", 1688)),
        user=db_info.get("user", ""), password=db_info.get("password", ""),
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return _fetch(cur, limit)
    finally:
        conn.close()


def _exec_plugin(db_info, sql, limit, plugin_pkg: str, db_default="testdb"):
    """db2 / clickhouse 等走插件自带 get_connection（JDBC）。"""
    from importlib import import_module
    mod = import_module(f"plugins.available.{plugin_pkg}.main_plugin")
    get_connection = getattr(mod, "get_connection")
    db_name = db_info.get("database") or db_default
    conn = get_connection(
        db_info.get("host", ""), int(db_info.get("port", 50000)),
        db_info.get("user", ""), db_info.get("password", ""), database=db_name,
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return _fetch(cur, limit)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# db_type（含 *_jdbc 变体）→ 执行器
def _resolve(db_type: str):
    t = (db_type or "").lower().replace("oracle_full", "oracle")
    if t in ("oracle", "oracle_jdbc"):
        return ("oracledb", None)
    if t in ("mysql", "tidb", "mariadb", "oceanbase", "tdsqlc_mysql"):
        return ("pymysql", None)
    if t in ("postgresql", "pg", "ivorysql", "kingbase", "hgdb", "hgdb_jdbc",
             "uxdb", "uxdb_jdbc"):
        return ("psycopg", None)
    if t in ("sqlserver", "sqlserver_jdbc"):
        return ("pyodbc", None)
    if t == "dm":
        return ("dm", None)
    if t == "yashandb":
        return ("yasdb", None)
    if t in ("db2", "db2_jdbc"):
        return ("plugin", "db2_jdbc")
    if t in ("clickhouse", "clickhouse_jdbc"):
        return ("plugin", "clickhouse_jdbc")
    return (None, None)


def execute_instance_query(
    db_info: Dict[str, Any], sql: str, limit: int = 200
) -> Dict[str, Any]:
    """对目标数据源执行一条只读 SQL。

    返回:
        {"ok": True, "columns": [...], "rows": [[...]], "truncated": bool}
        {"ok": False, "error": "<可读错误信息>"}
    """
    # 去掉末尾的分号（Python 执行不需要）
    sql = sql.strip().rstrip(';').strip()
    if not sql:
        return {"ok": False, "error": "SQL 不能为空"}

    try:
        assert_readonly_sql(sql)
    except ValueError as e:
        return {"ok": False, "error": f"只读校验失败：{e}"}

    db_type = (db_info.get("db_type", "") or "").replace("oracle_full", "oracle")
    kind, plugin = _resolve(db_type)
    if kind is None:
        return {"ok": False, "error": f"不支持的数据库类型: {db_type}"}

    t0 = time.time()
    try:
        if kind == "oracledb":
            cols, rows = _exec_oracledb(db_info, sql, limit)
        elif kind == "pymysql":
            cols, rows = _exec_pymysql(db_info, sql, limit)
        elif kind == "psycopg":
            cols, rows = _exec_psycopg(db_info, sql, limit)
        elif kind == "pyodbc":
            cols, rows = _exec_pyodbc(db_info, sql, limit,
                                      database=db_info.get("database", ""))
        elif kind == "dm":
            cols, rows = _exec_dm(db_info, sql, limit)
        elif kind == "yasdb":
            cols, rows = _exec_yasdb(db_info, sql, limit)
        elif kind == "plugin":
            cols, rows = _exec_plugin(db_info, sql, limit, plugin)
        else:
            return {"ok": False, "error": f"未实现的执行器: {kind}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    truncated = len(rows) >= (limit or 0)
    return {
        "ok": True,
        "columns": cols,
        "rows": rows,
        "truncated": truncated,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }


# ── 原始连接获取（供巡检/分析器复用，调用方负责关闭） ────────────────────────
def _connect_oracledb(db_info):
    import oracledb
    host = db_info.get("host", "")
    port = int(db_info.get("port", 1521))
    svc = db_info.get("service_name", "") or ""
    sid = db_info.get("sid", "") or ""
    if svc:
        dsn = oracledb.makedsn(host=host, port=port, service_name=svc)
    elif sid:
        dsn = oracledb.makedsn(host=host, port=port, sid=sid)
    else:
        dsn = f"{host}:{port}/orcl"
    sysdba = bool(db_info.get("sysdba", False))
    extra = {"mode": oracledb.SYSDBA} if sysdba else {}
    try:
        return oracledb.connect(
            user=db_info.get("user", ""),
            password=db_info.get("password") or "",
            dsn=dsn, **extra,
        )
    except Exception:
        try:
            oracledb.init_client()
            return oracledb.connect(
                user=db_info.get("user", ""),
                password=db_info.get("password") or "",
                dsn=dsn, **extra,
            )
        except Exception as e:
            raise


def _connect_pymysql(db_info, db_default="INFORMATION_SCHEMA"):
    import pymysql
    db_name = db_info.get("database") or db_default
    return pymysql.connect(
        host=db_info.get("host", ""), port=int(db_info.get("port", 3306)),
        user=db_info.get("user", ""), password=db_info.get("password", ""),
        database=db_name, charset="utf8mb4", connect_timeout=10,
    )


def _connect_psycopg(db_info, db_default="postgres"):
    import psycopg2
    db_type = (db_info.get("db_type", "") or "").lower()
    db_name = db_info.get("database") or (
        "highgo" if "hgdb" in db_type else (
            "ivorysql" if db_type == "ivorysql" else "postgres"))
    return psycopg2.connect(
        host=db_info.get("host", ""), port=int(db_info.get("port", 5432)),
        user=db_info.get("user", ""), password=db_info.get("password", ""),
        dbname=db_name, connect_timeout=10,
    )


def _connect_pyodbc(db_info, database=""):
    conn_str = (
        "DRIVER={{ODBC Driver 17 for SQL Server}};"
        "SERVER={host},{port};UID={user};PWD={pwd};"
    ).format(
        host=db_info.get("host", ""), port=int(db_info.get("port", 1433)),
        user=db_info.get("user", ""), pwd=db_info.get("password", ""),
    )
    if database:
        conn_str += f"DATABASE={database};"
    import pyodbc
    return pyodbc.connect(conn_str, timeout=10)


def _connect_dm(db_info):
    import dmPython
    return dmPython.connect(
        user=db_info.get("user", ""), password=db_info.get("password", ""),
        server=db_info.get("host", ""), port=int(db_info.get("port", 5236)),
    )


def _connect_yasdb(db_info):
    import yasdb
    return yasdb.connect(
        host=db_info.get("host", ""), port=int(db_info.get("port", 1688)),
        user=db_info.get("user", ""), password=db_info.get("password", ""),
    )


def _connect_plugin(db_info, plugin_pkg, db_default="testdb"):
    from importlib import import_module
    mod = import_module(f"plugins.available.{plugin_pkg}.main_plugin")
    get_connection = getattr(mod, "get_connection")
    db_name = db_info.get("database") or db_default
    return get_connection(
        db_info.get("host", ""), int(db_info.get("port", 50000)),
        db_info.get("user", ""), db_info.get("password", ""), database=db_name,
    )


def connect_instance(db_info: Dict[str, Any]):
    """返回目标数据源的原始 DB-API 连接（只读取向）。

    与 :func:`execute_instance_query` 共用同一套按 db_type 的连接分支，
    但本函数返回**未关闭**的连接，供巡检/分析器（slow_query / index_health /
    config_baseline / lock_tree 等）复用。调用方必须调用
    :func:`close_instance` 关闭。
    """
    db_type = (db_info.get("db_type", "") or "").lower().replace("oracle_full", "oracle")
    kind, plugin = _resolve(db_type)
    if kind is None:
        raise ValueError(f"不支持的数据库类型: {db_type}")
    if kind == "oracledb":
        return _connect_oracledb(db_info)
    if kind == "pymysql":
        return _connect_pymysql(db_info)
    if kind == "psycopg":
        return _connect_psycopg(db_info)
    if kind == "pyodbc":
        return _connect_pyodbc(db_info, database=db_info.get("database", ""))
    if kind == "dm":
        return _connect_dm(db_info)
    if kind == "yasdb":
        return _connect_yasdb(db_info)
    if kind == "plugin":
        return _connect_plugin(db_info, plugin)
    raise ValueError(f"未实现的连接类型: {kind}")


def close_instance(conn) -> None:
    """安全关闭 :func:`connect_instance` 返回的连接。"""
    try:
        conn.close()
    except Exception:
        pass


# ── 获取视图/表的列信息（供自然语言探查专员预检） ────────────────────────────
def get_table_columns(db_info: Dict[str, Any], table_name: str) -> Dict[str, Any]:
    """查询视图/表的列信息，用于在生成 SQL 前验证列名是否正确。

    对于常见数据库类型，查询系统视图获取目标表的列名列表。
    返回: {"ok": True, "columns": ["COL1", "COL2", ...]} 或 {"ok": False, "error": "..."}
    """
    db_type = (db_info.get("db_type", "") or "").replace("oracle_full", "oracle")
    t = db_type.lower()

    # 标准化表名（大写是 Oracle 惯例）
    tbl = (table_name or "").strip().upper()

    # 根据数据库类型构造查询列信息的 SQL
    col_sql = None
    if t in ("oracle", "oracle_jdbc"):
        # Oracle: 优先查 USER_TABLESPACES（当前用户可访问的），若无则查 ALL_TABLESPACES
        col_sql = f"""SELECT COLUMN_NAME FROM USER_TAB_COLUMNS
WHERE TABLE_NAME = '{tbl}' ORDER BY COLUMN_ID"""
        # 如果是表空间相关视图，尝试 ALL_TABLESPACES
        if "TABLESPACE" in tbl:
            col_sql = f"""SELECT COLUMN_NAME FROM USER_TAB_COLUMNS
WHERE TABLE_NAME = '{tbl}' UNION ALL
SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS
WHERE OWNER = (SELECT DEFAULT_TABLESPACE FROM USER_USERS)
AND TABLE_NAME = '{tbl}' ORDER BY COLUMN_NAME"""
    elif t in ("mysql", "tidb", "mariadb", "oceanbase", "tdsqlc_mysql"):
        db_name = db_info.get("database") or "information_schema"
        col_sql = f"""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = '{db_name}' AND TABLE_NAME = '{table_name}'
ORDER BY ORDINAL_POSITION"""
    elif t in ("postgresql", "pg", "ivorysql", "kingbase", "hgdb", "hgdb_jdbc",
               "uxdb", "uxdb_jdbc"):
        # PostgreSQL: 先尝试当前 schema 的 columns
        col_sql = f"""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = '{table_name}' AND TABLE_SCHEMA = current_schema()
ORDER BY ORDINAL_POSITION"""
    elif t in ("sqlserver", "sqlserver_jdbc"):
        db_name = db_info.get("database") or "db_name()"
        col_sql = f"""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = '{table_name}' AND TABLE_SCHEMA = SCHEMA_NAME()
ORDER BY ORDINAL_POSITION"""
    elif t == "dm":
        col_sql = f"""SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS
WHERE OWNER = USER AND TABLE_NAME = '{tbl}' ORDER BY COLUMN_ID"""
    elif t == "yashandb":
        col_sql = f"""SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS
WHERE OWNER = USER AND TABLE_NAME = '{tbl}' ORDER BY COLUMN_ID"""

    if not col_sql:
        return {"ok": False, "error": f"不支持查询列信息的数据库类型: {db_type}"}

    # 执行列信息查询（也走只读校验）
    try:
        assert_readonly_sql(col_sql)
    except ValueError as e:
        return {"ok": False, "error": f"列信息查询只读校验失败：{e}"}

    # 复用已有执行器获取列信息
    result = execute_instance_query(db_info, col_sql, limit=500)
    if not result.get("ok"):
        # 列信息查询失败不阻断主流程，返回空列即可
        return {"ok": True, "columns": [], "error": result.get("error", "")}

    cols = result.get("columns", [])
    rows = result.get("rows", [])
    # 把行数据转成列名列表
    col_names = []
    col_idx = None
    # 找 COLUMN_NAME 列的位置
    for i, c in enumerate(cols):
        if c.upper() == "COLUMN_NAME":
            col_idx = i
            break
    if col_idx is not None:
        for row in rows:
            if len(row) > col_idx:
                col_names.append(str(row[col_idx]).strip().upper())
    else:
        # 如果没有找到 COLUMN_NAME 列，直接取第一列
        for row in rows:
            if row:
                col_names.append(str(row[0]).strip().upper())

    return {"ok": True, "columns": col_names}
