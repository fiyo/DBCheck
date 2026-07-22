#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# -*- mode: python; coding: utf-8 -*-
"""
OceanBase SQL 编辑器树形导航接口回归验证脚本（mock pymysql，不连真实实例）
================================================================================

验证工程师在 web_ui.py 中新增的两处 `if db_type == 'oceanbase':` 独立分支：
  - api_ds_databases  (/api/pro/datasources/<ds_id>/databases)  列数据库
  - api_ds_objects    (/api/pro/datasources/<ds_id>/objects)     列 表/视图

BUG 背景：点击 OceanBase 数据源节点报 (2013, 'Lost connection to MySQL server
during query')。根因是 OceanBase MySQL 租户在部分版本/代理下执行
INFORMATION_SCHEMA 查询（SCHEMATA / TABLES）会触发 2013。修复本质是把 OceanBase
从 mysql/mariadb/tidb 共享分支摘出，改用原生 SHOW 命令。

验证目标（本沙箱无真实 OceanBase 实例，全程 mock）：
  点击 OceanBase 数据源节点 -> /databases 不再报 2013、返回数据库列表
    （来自 `SHOW DATABASES`）；
  展开某库 -> /objects 返回表/视图（来自 `SHOW FULL TABLES FROM db`）。
  **关键证据**：fake cursor 记录的 SQL 文本必须含 `SHOW` 且不含 `INFORMATION_SCHEMA`
    —— 这才是证明修复生效的核心断言。若仍出现 INFORMATION_SCHEMA 字样即判源码 Bug。

环境：py -3.12（D:/DBCheck）。web_ui.py 有既有 BOM，import 时由 importlib 自动处理。
pymysql 已安装，但本脚本全程用 FakeConnector 替换 pymysql.connect，绝不触碰真实 OceanBase。

分层：
  第 1 层（逻辑层，必做，绕过路由/认证）：monkeypatch pymysql.connect +
  pro.get_instance_manager，直调 api_ds_databases / api_ds_objects 模块级函数。
  第 2 层（路由层，尽量做）：同样 mock 下用 Flask test_client 打真实路由。
  若遇 401/403 认证门禁：明确标注是认证层、非本次 Bug，退回以第 1 层为准。

智能路由判定：源码有 Bug -> Engineer；测试代码有 Bug -> 自行修正重跑；全部通过 -> NoOne。
本脚本只读取/验证，不修改任何产品代码。
"""

import os
import sys
import json
import traceback
from unittest.mock import patch

# 使用项目根目录，保证 web_ui / pro 等可被导入
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

print("=" * 78)
print("OceanBase 树形导航接口回归验证（mock pymysql，不连真实实例）")
print("=" * 78)

# ─────────────────────────────────────────────────────────────────────────
# Fake pymysql（替换 pymysql.connect）+ Fake cursor（记录并执行 SQL 文本）
# ─────────────────────────────────────────────────────────────────────────

# 各用例执行前由 set_scenario 配置：
#   databases_result: SHOW DATABASES -> fetchall() 行列表
#   tables_result:    SHOW FULL TABLES -> fetchall() 行列表
SCENARIO = {
    "databases_result": [('sys',), ('oceanbase',), ('test',), ('mysql',)],
    "tables_result":    [('t1', 'BASE TABLE'), ('v1', 'VIEW')],
}

CONNECT_LOG = []   # 记录每次 pymysql.connect 的 kwargs（连接路径证据）
SQL_LOG = []       # 记录 fake cursor 每次 execute 的 SQL 文本（修复本质证据）


class FakeCursor:
    """模拟 pymysql.cursors.Cursor，按 SQL 文本分派返回结果，并记录 SQL。"""

    def __init__(self):
        self._rows = []

    def execute(self, sql, args=None):
        # 记录实际执行的 SQL 文本（第一参数），这是本次验证的核心证据
        SQL_LOG.append(sql)
        s = sql.upper()
        if 'SHOW DATABASES' in s:
            self._rows = list(SCENARIO.get("databases_result", []))
        elif 'SHOW FULL TABLES' in s:
            self._rows = list(SCENARIO.get("tables_result", []))
        else:
            # 任何非预期 SQL（如 INFORMATION_SCHEMA 查询）一律返回空，避免误判通过
            self._rows = []
        return None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeConnector:
    """替换 pymysql.connect，返回持有 FakeCursor 的 fake conn；记录连接参数。"""

    def __init__(self, **kwargs):
        CONNECT_LOG.append(dict(kwargs))
        self._kwargs = dict(kwargs)
        self._closed = False

    def cursor(self):
        return FakeCursor()

    def close(self):
        self._closed = True


class FakeInstanceManager:
    """替换 pro 的 get_instance_manager，返回固定的 fake oceanbase inst 字典。"""

    def __init__(self, inst):
        self._inst = inst

    def get_instance_decrypted(self, ds_id):
        return self._inst


# 标准 fake oceanbase 数据源配置（含 tenant：root@test，真实 OceanBase 场景）
INST = {
    'db_type': 'oceanbase',
    'host': '127.0.0.1',
    'port': 2881,
    'user': 'root@test',
    'password': 'pw',
    'database': 'test',
}

DS_ID = 'qa_oceanbase_verify_001'

# 结果收集
RESULTS = []  # dict(layer, item, name, passed, detail, evidence)


def record(layer, item, name, passed, detail, evidence=""):
    RESULTS.append({
        "layer": layer, "item": item, "name": name,
        "passed": passed, "detail": detail, "evidence": evidence,
    })
    tag = "PASS" if passed is True else ("FAIL" if passed is False else "N/A")
    print("  [%s] %s / %s — %s" % (tag, layer, name, detail))
    if evidence:
        print("       证据: %s" % evidence)


def set_scenario(**kw):
    SCENARIO.clear()
    SCENARIO.update(kw)


def call_databases(ds_id):
    with web_ui.app.test_request_context('/'):
        return web_ui.api_ds_databases(ds_id)


def call_objects(ds_id, database):
    with web_ui.app.test_request_context('/?database=' + database):
        return web_ui.api_ds_objects(ds_id)


def sql_has_show(sqls):
    return any('SHOW' in (s or '').upper() for s in sqls)


def sql_has_information_schema(sqls):
    return any('INFORMATION_SCHEMA' in (s or '').upper() for s in sqls)


# =====================================================================
# 准备：导入 web_ui / pymysql / pro（patch 在 with 块内生效）
# =====================================================================
import web_ui
import pymysql
from pro import get_instance_manager  # 仅用于确认可导入；实际由 patch 接管

FAKE_MGR = FakeInstanceManager(INST)

print("\n──────── 第 1 层（逻辑层，必做：monkeypatch + 直调函数）────────")
a_results = {}

with patch('pymysql.connect', FakeConnector), \
     patch('pro.get_instance_manager', return_value=FAKE_MGR):

    # ---- A1: databases 正常路径（SHOW DATABASES）----
    print("\n[A1] api_ds_databases — 正常返回数据库列表（mock SHOW DATABASES）")
    try:
        CONNECT_LOG.clear()
        SQL_LOG.clear()
        set_scenario(
            databases_result=[('sys',), ('oceanbase',), ('test',), ('mysql',)],
        )
        resp = call_databases(DS_ID)
        j = resp.get_json(force=True) or {}

        connect_kwargs = CONNECT_LOG[-1] if CONNECT_LOG else {}
        sqls = list(SQL_LOG)
        evidence = ("connect_kwargs=%r | sql=%r | json=%r"
                    % (connect_kwargs, sqls, j))

        assert CONNECT_LOG, "FakeConnector 未被构造（oceanbase 分支未被执行？）"
        assert 'error' not in j, "返回含 error: %r" % j
        assert 'databases' in j, "JSON 缺 databases 字段: %r" % j
        assert j['databases'] == ['sys', 'oceanbase', 'test', 'mysql'], \
            "databases 不符预期: %r" % j.get('databases')
        assert j.get('db_type') == 'oceanbase', "db_type 应为 oceanbase: %r" % j.get('db_type')
        # 连接路径证据：确实走了 pymysql 连接（host/port/charset）
        assert connect_kwargs.get('host') == '127.0.0.1', "connect host 不符: %r" % connect_kwargs
        assert connect_kwargs.get('port') == 2881, "connect port 不符: %r" % connect_kwargs
        assert connect_kwargs.get('charset') == 'utf8mb4', "connect charset 不符: %r" % connect_kwargs
        record("L1", "A1", "databases 正常返回数据库列表", True,
               "无 error，databases==['sys','oceanbase','test','mysql']，已走 pymysql 连接路径",
               evidence)
        a_results['A1'] = (True, j, sqls, connect_kwargs)
    except Exception as e:
        record("L1", "A1", "databases 正常返回数据库列表", False,
               "源码逻辑产出错误输出: %s" % e, traceback.format_exc().splitlines()[-3:])
        a_results['A1'] = (False, None, list(SQL_LOG), None)

    # ---- A2: objects 正常路径（SHOW FULL TABLES FROM `db`）----
    print("\n[A2] api_ds_objects — 正常返回表/视图列表（mock SHOW FULL TABLES FROM）")
    try:
        CONNECT_LOG.clear()
        SQL_LOG.clear()
        set_scenario(
            tables_result=[('t1', 'BASE TABLE'), ('v1', 'VIEW')],
        )
        resp = call_objects(DS_ID, 'test')
        j = resp.get_json(force=True) or {}

        connect_kwargs = CONNECT_LOG[-1] if CONNECT_LOG else {}
        sqls = list(SQL_LOG)
        evidence = ("connect_kwargs=%r | sql=%r | json=%r"
                    % (connect_kwargs, sqls, j))

        assert CONNECT_LOG, "FakeConnector 未被构造（oceanbase 分支未被执行？）"
        assert 'error' not in j, "返回含 error: %r" % j
        assert 'tables' in j and 'views' in j, "JSON 缺 tables/views 字段: %r" % j
        assert j['tables'] == ['t1'], "tables 不符预期: %r" % j.get('tables')
        assert j['views'] == ['v1'], "views 不符预期: %r" % j.get('views')
        # 连接路径证据：objects 连接应带 database=test
        assert connect_kwargs.get('database') == 'test', "connect database 不符: %r" % connect_kwargs
        record("L1", "A2", "objects 正常返回 表/视图 列表", True,
               "无 error，tables==['t1']，views==['v1']，连接带 database=test",
               evidence)
        a_results['A2'] = (True, j, sqls, connect_kwargs)
    except Exception as e:
        record("L1", "A2", "objects 正常返回 表/视图 列表", False,
               "源码逻辑产出错误输出: %s" % e, traceback.format_exc().splitlines()[-3:])
        a_results['A2'] = (False, None, list(SQL_LOG), None)

    # ---- A3（关键证据）：SQL 必须含 SHOW 且不含 INFORMATION_SCHEMA ----
    print("\n[A3] 关键证据 — oceanbase 分支执行的 SQL 为 SHOW 命令（非 INFORMATION_SCHEMA）")
    try:
        # 合并 A1/A2 记录的 SQL；若任一未记录则补跑一次
        sqls = []
        if a_results.get('A1', (False,))[2]:
            sqls += a_results['A1'][2]
        if a_results.get('A2', (False,))[2]:
            sqls += a_results['A2'][2]
        if not sqls:
            CONNECT_LOG.clear()
            SQL_LOG.clear()
            set_scenario(databases_result=[('sys',), ('oceanbase',), ('test',), ('mysql',)],
                         tables_result=[('t1', 'BASE TABLE'), ('v1', 'VIEW')])
            call_databases(DS_ID)
            call_objects(DS_ID, 'test')
            sqls = list(SQL_LOG)

        evidence = "recorded_sql=%r" % sqls
        assert sqls, "未记录到任何执行的 SQL（oceanbase 分支可能未执行）"
        assert sql_has_show(sqls), "执行的 SQL 未包含 SHOW：%r" % sqls
        assert not sql_has_information_schema(sqls), \
            "执行的 SQL 仍含 INFORMATION_SCHEMA（修复未生效，判源码 Bug）：%r" % sqls
        # 进一步断言具体语句形态
        assert any('SHOW DATABASES' in s.upper() for s in sqls), \
            "未发现 SHOW DATABASES：%r" % sqls
        assert any('SHOW FULL TABLES FROM' in s.upper() for s in sqls), \
            "未发现 SHOW FULL TABLES FROM：%r" % sqls
        record("L1", "A3", "关键证据：SQL 为 SHOW 非 INFORMATION_SCHEMA", True,
               "oceanbase 分支执行的是 SHOW DATABASES / SHOW FULL TABLES FROM，无 INFORMATION_SCHEMA 查询",
               evidence)
        a_results['A3'] = True
    except Exception as e:
        record("L1", "A3", "关键证据：SQL 为 SHOW 非 INFORMATION_SCHEMA", False,
               "核心证据断言失败: %s" % e, traceback.format_exc().splitlines()[-3:])
        a_results['A3'] = False

    # ---- A4: 防注入路径（database 含反引号）----
    print("\n[A4] api_ds_objects — 防注入路径（database 含反引号 `）")
    try:
        CONNECT_LOG.clear()
        SQL_LOG.clear()
        set_scenario(tables_result=[('t', 'BASE TABLE')])
        weird_db = "weird`name"
        resp = call_objects(DS_ID, weird_db)
        j = resp.get_json(force=True) or {}

        sqls = list(SQL_LOG)
        evidence = "sql=%r | json=%r" % (sqls, j)

        assert 'error' not in j, "防注入路径返回 error: %r" % j
        assert 'tables' in j and 'views' in j, "JSON 缺 tables/views 字段: %r" % j
        # fake cursor 对含 SHOW FULL TABLES 的 SQL 一律返回 [('t','BASE TABLE')]
        assert j['tables'] == ['t'], "防注入路径 tables 不符预期: %r" % j.get('tables')
        assert j['views'] == [], "防注入路径 views 应空: %r" % j.get('views')
        # 证明转义生效：SQL 中数据库名应为 weird``name（反引号被转义）
        assert any('weird``name' in s for s in sqls), \
            "未检测到反引号转义（期望 weird``name）：%r" % sqls
        assert not any('weird`name`' in s for s in sqls), \
            "检测到未转义的注入型 SQL（危险）：%r" % sqls
        record("L1", "A4", "objects 防注入路径（database 含反引号）", True,
               "不崩、不报 SQL 语法错，反引号已转义为 weird``name，tables==['t']",
               evidence)
        a_results['A4'] = True
    except Exception as e:
        record("L1", "A4", "objects 防注入路径（database 含反引号）", False,
               "防注入路径源码逻辑产出错误输出: %s" % e,
               traceback.format_exc().splitlines()[-3:])
        a_results['A4'] = False

    # =====================================================================
    # 第 2 层：路由层（尽量做）—— Flask test_client 打真实路由
    # =====================================================================
    print("\n──────── 第 2 层（路由层，尽量做：Flask test_client）────────")
    layer2_status = "ATTEMPTED"
    b1_ok = b2_ok = None
    b1_evidence = b2_evidence = ""

    try:
        client = web_ui.app.test_client()

        # B1: /databases
        print("\n[B1] GET /api/pro/datasources/%s/databases" % DS_ID)
        CONNECT_LOG.clear()
        SQL_LOG.clear()
        set_scenario(
            databases_result=[('sys',), ('oceanbase',), ('test',), ('mysql',)],
        )
        resp = client.get("/api/pro/datasources/%s/databases" % DS_ID)
        try:
            j = resp.get_json(force=True) or {}
        except Exception:
            j = {}
        body = resp.get_data(as_text=True)
        http_ok = resp.status_code not in (400, 500)
        no_err = 'error' not in j
        has_field = 'databases' in j
        sql_l1 = list(SQL_LOG)
        b1_evidence = "HTTP=%s json=%r sql=%r" % (resp.status_code, j, sql_l1)
        if resp.status_code in (401, 403):
            layer2_status = "AUTH_GATE"
            record("L2", "B1", "databases 路由（端到端）", None,
                   "被认证门禁拦截(HTTP %d)，非本次 Bug，退回以第1层为准" % resp.status_code,
                   b1_evidence)
        else:
            ok = bool(http_ok and no_err and has_field
                      and sql_has_show(sql_l1) and not sql_has_information_schema(sql_l1))
            b1_ok = ok
            record("L2", "B1", "databases 路由（端到端，SQL 为 SHOW）", ok,
                   "HTTP 非 4xx/5xx、含 databases 字段、fake cursor SQL 为 SHOW 且非 INFORMATION_SCHEMA"
                   if ok else "路由层断言未通过（详见证据）", b1_evidence)

        # B2: /objects
        print("\n[B2] GET /api/pro/datasources/%s/objects?database=test" % DS_ID)
        CONNECT_LOG.clear()
        SQL_LOG.clear()
        set_scenario(tables_result=[('t1', 'BASE TABLE'), ('v1', 'VIEW')])
        resp2 = client.get("/api/pro/datasources/%s/objects?database=test" % DS_ID)
        try:
            j2 = resp2.get_json(force=True) or {}
        except Exception:
            j2 = {}
        body2 = resp2.get_data(as_text=True)
        http_ok2 = resp2.status_code not in (400, 500)
        no_err2 = 'error' not in j2
        has_field2 = ('tables' in j2) and ('views' in j2)
        sql_l2 = list(SQL_LOG)
        b2_evidence = "HTTP=%s json=%r sql=%r" % (resp2.status_code, j2, sql_l2)
        if resp2.status_code in (401, 403):
            layer2_status = "AUTH_GATE"
            record("L2", "B2", "objects 路由（端到端）", None,
                   "被认证门禁拦截(HTTP %d)，非本次 Bug，退回以第1层为准" % resp2.status_code,
                   b2_evidence)
        else:
            ok = bool(http_ok2 and no_err2 and has_field2
                      and sql_has_show(sql_l2) and not sql_has_information_schema(sql_l2))
            b2_ok = ok
            record("L2", "B2", "objects 路由（端到端，SQL 为 SHOW）", ok,
                   "HTTP 非 4xx/5xx、含 tables/views 字段、fake cursor SQL 为 SHOW 且非 INFORMATION_SCHEMA"
                   if ok else "路由层断言未通过（详见证据）", b2_evidence)

    except Exception as e:
        layer2_status = "SKIPPED"
        print("  [WARN] 路由层执行异常，退回以第1层为准: %s" % e)
        traceback.print_exc()
        record("L2", "B1", "databases 路由（端到端）", None,
               "路由层不可用(导入/环境异常)，以第1层逻辑验证为准: %s" % e, "")
        record("L2", "B2", "objects 路由（端到端）", None,
               "路由层不可用(导入/环境异常)，以第1层逻辑验证为准: %s" % e, "")

# =====================================================================
# 汇总 + 智能路由判定
# =====================================================================
print("\n" + "=" * 78)
print("验证结果明细")
print("=" * 78)
for r in RESULTS:
    v = "PASS" if r["passed"] is True else ("FAIL" if r["passed"] is False else "N/A")
    print("  [%s] %-16s %s" % (v, r["layer"] + "/" + r["item"], r["name"]))

a1_ok = a_results.get('A1', (False,))[0]
a2_ok = a_results.get('A2', (False,))[0]
a3_ok = a_results.get('A3', False)
a4_ok = a_results.get('A4', False)

print("\n──────── 智能路由判定 ────────")
if not a1_ok:
    decision = "Engineer"
    reason = ("源码 Bug：api_ds_databases 的 oceanbase 分支未生效/逻辑错误，"
              "未返回预期数据库列表或仍走 INFORMATION_SCHEMA（见 A1 失败用例）。")
elif not a2_ok:
    decision = "Engineer"
    reason = ("源码 Bug：api_ds_objects 的 oceanbase 分支未生效/逻辑错误，"
              "表/视图分流错误或未返回预期结果（见 A2 失败用例）。")
elif not a3_ok:
    decision = "Engineer"
    reason = ("源码 Bug：关键证据断言失败 —— oceanbase 分支执行的 SQL 仍含 INFORMATION_SCHEMA "
              "或不含 SHOW，修复未生效（见 A3 失败用例）。")
elif not a4_ok:
    decision = "Engineer"
    reason = "源码 Bug：api_ds_objects 防注入路径（database 含反引号）未转义或崩溃（见 A4 失败用例）。"
else:
    decision = "NoOne"
    reason = ("第1层逻辑验证全部通过（A1~A4）。OceanBase 两个树形导航接口在 mock 下已正确走原生 "
              "SHOW 命令（SHOW DATABASES / SHOW FULL TABLES FROM），不再触发 INFORMATION_SCHEMA 查询，"
              "因此不会再有 (2013, 'Lost connection') 风险。路由层 B1/B2 见上方状态。")

print("  路由判定: %s" % decision)
print("  判定理由: %s" % reason)

print("\n──────── 关键证据（fake cursor 实际执行的 SQL 文本）────────")
a1_sql = a_results.get('A1', (None, None, []))[2]
a2_sql = a_results.get('A2', (None, None, []))[2]
if a1_sql:
    print("  A1 (databases SQL): %s" % a1_sql)
if a2_sql:
    print("  A2 (objects  SQL):  %s" % a2_sql)
print("  L2 状态: %s" % layer2_status)
if b1_evidence:
    print("  B1 (databases 路由): %s" % b1_evidence)
if b2_evidence:
    print("  B2 (objects 路由):   %s" % b2_evidence)

print("\n完成。本脚本仅验证，未修改任何产品代码。决策: %s" % decision)
