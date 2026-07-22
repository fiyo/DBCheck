#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# -*- mode: python; coding: utf-8 -*-
"""
MongoDB SQL 编辑器树形导航接口回归验证脚本（mock MongoClient，不连真实实例）
================================================================================

验证工程师在 web_ui.py 中新增的两处 `elif db_type == 'mongodb':` 分支：
  - api_ds_databases  (/api/pro/datasources/<ds_id>/databases)  列数据库
  - api_ds_objects    (/api/pro/datasources/<ds_id>/objects)     列 集合/视图

验证目标：点击 MongoDB 数据源节点 -> /databases 不再返回「暂不支持该数据库类型」，
返回数据库列表；展开某库 -> /objects 返回该库下集合（standard->tables, view->views）。

环境：py -3.12（D:/DBCheck）。web_ui.py 有 BOM，运行环境 utf-8。
pymongo 4.x 已安装，但本脚本全程用 FakeMongoClient 替换 pymongo.MongoClient，
绝不触碰真实 MongoDB（也无需真实实例）。

分层：
  第 1 层（逻辑层，必做，绕过路由/认证）：monkeypatch pymongo.MongoClient +
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

# 使用项目根目录，保证 web_ui / pro / plugins 等可被导入
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

print("=" * 72)
print("MongoDB 树形导航接口回归验证（mock MongoClient，不连真实实例）")
print("=" * 72)

# ─────────────────────────────────────────────────────────────────────────
# Fake MongoDB（替换 pymongo.MongoClient）+ Fake Instance Manager
# ─────────────────────────────────────────────────────────────────────────
SCENARIO = {}  # 每个用例前重设：控制 fake client/db 的行为与兜底

class FakeMongoDatabase:
    """模拟一个 MongoDB database，支持按 filter 区分 standard/view 集合。"""

    def __init__(self, name, collections_by_type, all_collections,
                 raise_on_filter=False, raise_collection=False):
        self._name = name
        self._collections_by_type = collections_by_type  # {'standard':[...], 'view':[...]}
        self._all = all_collections                      # 无 filter 的全量集合
        self._raise_on_filter = raise_on_filter          # 老驱动不认 filter 参数
        self._raise_collection = raise_collection        # 连集合都列不出来

    def list_collection_names(self, filter=None, **kwargs):
        if self._raise_collection:
            raise Exception("simulated list_collection_names failure")
        if filter is not None and self._raise_on_filter:
            raise Exception("old pymongo driver does not support 'filter' argument")
        if filter is not None:
            return list(self._collections_by_type.get(filter.get('type'), []))
        return list(self._all)


class FakeMongoClient:
    """替换 pymongo.MongoClient，记录构造参数（uri/kwargs）作为连接路径证据。"""

    CAPTURE = []  # 记录每次构造调用的 {'uri':..., 'kwargs':...}

    def __init__(self, uri, **kwargs):
        FakeMongoClient.CAPTURE.append({'uri': uri, 'kwargs': dict(kwargs)})
        self._uri = uri
        self._kwargs = dict(kwargs)
        self._databases = list(SCENARIO.get('databases', []))
        self._raise_database = SCENARIO.get('raise_database', False)
        self._db = FakeMongoDatabase(
            SCENARIO.get('db_name', 'test'),
            SCENARIO.get('collections_by_type', {}),
            SCENARIO.get('all_collections', []),
            raise_on_filter=SCENARIO.get('raise_filter', False),
            raise_collection=SCENARIO.get('raise_collection', False),
        )

    def list_database_names(self):
        if self._raise_database:
            raise Exception("simulated list_database_names failure")
        return list(self._databases)

    def __getitem__(self, name):
        # 任意库名都返回同一个 fake db（符合本验证场景：只看 test 库）
        return self._db

    def close(self):
        pass


class FakeInstanceManager:
    """替换 pro 的 get_instance_manager，返回固定的 fake mongodb inst 字典。"""

    def __init__(self, inst):
        self._inst = inst

    def get_instance_decrypted(self, ds_id):
        return self._inst


# 标准 fake mongodb 数据源配置（与任务一致）
INST = {
    'db_type': 'mongodb',
    'host': '127.0.0.1',
    'port': 27017,
    'user': 'root',
    'password': 'pw',
    'database': 'test',
    'connect_mode': 'standard',
    'auth_source': 'admin',
    'auth_mechanism': '',
    'replica_set': '',
    'tls': False,
}

DS_ID = 'qa_mongodb_verify_001'

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
    # api_ds_databases 不使用 request，但用 test_request_context 包裹更稳妥
    with web_ui.app.test_request_context('/'):
        return web_ui.api_ds_databases(ds_id)


def call_objects(ds_id, database):
    # api_ds_objects 读取 request.args['database']，必须提供请求上下文
    with web_ui.app.test_request_context('/?database=' + database):
        return web_ui.api_ds_objects(ds_id)


def no_unsupported(j):
    return '暂不支持该数据库类型' not in json.dumps(j, ensure_ascii=False)


# =====================================================================
# 准备：导入 web_ui / pymongo / pro（patch 在 with 块内生效）
# =====================================================================
import web_ui
import pymongo
from pro import get_instance_manager  # 仅用于确认可导入；实际由 patch 接管

FAKE_MGR = FakeInstanceManager(INST)

print("\n──────── 第 1 层（逻辑层，必做：monkeypatch + 直调函数）────────")
a_results = {}
with patch('pymongo.MongoClient', FakeMongoClient), \
     patch('pro.get_instance_manager', return_value=FAKE_MGR):

    # ---- A1: databases 正常路径 ----
    print("\n[A1] api_ds_databases — 正常返回数据库列表（mock list_database_names）")
    try:
        FakeMongoClient.CAPTURE.clear()
        set_scenario(
            databases=['admin', 'config', 'local', 'test'],
            db_name='test',
            collections_by_type={'standard': ['colA', 'colB'], 'view': ['viewX']},
            all_collections=['colA', 'colB', 'viewX'],
        )
        resp = call_databases(DS_ID)
        body = resp.get_data(as_text=True)
        j = json.loads(body)

        captured = FakeMongoClient.CAPTURE[-1] if FakeMongoClient.CAPTURE else {}
        uri = captured.get('uri', '')
        kwargs = captured.get('kwargs', {})
        evidence = "uri=%r kwargs=%r databases=%r" % (uri, kwargs, j.get('databases'))

        assert FakeMongoClient.CAPTURE, "FakeMongoClient 未被构造（mongodb 分支未被执行？）"
        assert 'error' not in j, "返回含 error: %r" % j
        assert no_unsupported(j), "返回「暂不支持该数据库类型」: %r" % j
        assert 'databases' in j, "JSON 缺 databases 字段: %r" % j
        assert j['databases'] == ['admin', 'config', 'local', 'test'], \
            "databases 不符预期: %r" % j.get('databases')
        assert j.get('db_type') == 'mongodb', "db_type 应为 mongodb: %r" % j.get('db_type')
        # 连接路径证据：uri 确实走了 mongodb://host:port/db?authSource=...
        assert 'mongodb://' in uri, "uri 非 mongodb 协议: %r" % uri
        assert '127.0.0.1:27017' in uri, "uri 未含目标 host:port: %r" % uri
        assert '/test' in uri, "uri 未含 database=test: %r" % uri
        record("L1", "A1", "databases 正常返回数据库列表", True,
               "无「暂不支持」，databases==['admin','config','local','test']，已走 MongoDB 连接路径",
               evidence)
        a_results['A1'] = (True, j, captured)
    except Exception as e:
        record("L1", "A1", "databases 正常返回数据库列表", False,
               "源码逻辑产出错误输出: %s" % e, traceback.format_exc().splitlines()[-3:])
        a_results['A1'] = (False, None, None)

    # ---- A2: objects 正常路径（standard->tables, view->views）----
    print("\n[A2] api_ds_objects — 正常返回集合/视图列表（filter 分流）")
    try:
        FakeMongoClient.CAPTURE.clear()
        set_scenario(
            databases=['admin', 'config', 'local', 'test'],
            db_name='test',
            collections_by_type={'standard': ['colA', 'colB'], 'view': ['viewX']},
            all_collections=['colA', 'colB', 'viewX'],
        )
        resp = call_objects(DS_ID, 'test')
        body = resp.get_data(as_text=True)
        j = json.loads(body)

        captured = FakeMongoClient.CAPTURE[-1] if FakeMongoClient.CAPTURE else {}
        uri = captured.get('uri', '')
        evidence = "uri=%r tables=%r views=%r" % (uri, j.get('tables'), j.get('views'))

        assert FakeMongoClient.CAPTURE, "FakeMongoClient 未被构造（mongodb 分支未被执行？）"
        assert 'error' not in j, "返回含 error: %r" % j
        assert no_unsupported(j), "返回「暂不支持该数据库类型」: %r" % j
        assert 'tables' in j and 'views' in j, "JSON 缺 tables/views 字段: %r" % j
        assert j['tables'] == ['colA', 'colB'], "tables 不符预期: %r" % j.get('tables')
        assert j['views'] == ['viewX'], "views 不符预期: %r" % j.get('views')
        record("L1", "A2", "objects 正常返回 集合/视图 列表", True,
               "无「暂不支持」，tables==['colA','colB']，views==['viewX']", evidence)
        a_results['A2'] = (True, j, captured)
    except Exception as e:
        record("L1", "A2", "objects 正常返回 集合/视图 列表", False,
               "源码逻辑产出错误输出: %s" % e, traceback.format_exc().splitlines()[-3:])
        a_results['A2'] = (False, None, None)

    # ---- A3: objects 回退路径（老驱动不认 filter 参数）----
    print("\n[A3] api_ds_objects — 回退路径（list_collection_names(filter) 抛异常）")
    try:
        FakeMongoClient.CAPTURE.clear()
        set_scenario(
            databases=['admin', 'config', 'local', 'test'],
            db_name='test',
            collections_by_type={'standard': ['colA', 'colB'], 'view': ['viewX']},
            all_collections=['colA', 'colB', 'viewX'],
            raise_filter=True,  # 模拟老版本 pymongo 不识别 filter 参数
        )
        resp = call_objects(DS_ID, 'test')
        body = resp.get_data(as_text=True)
        j = json.loads(body)
        evidence = "tables=%r views=%r" % (j.get('tables'), j.get('views'))

        assert 'error' not in j, "返回含 error: %r" % j
        assert no_unsupported(j), "回退路径仍「暂不支持」: %r" % j
        # 回退：tables = 全量集合，views = []
        assert j['tables'] == ['colA', 'colB', 'viewX'], \
            "回退 tables 应为全量集合: %r" % j.get('tables')
        assert j['views'] == [], "回退 views 应为空: %r" % j.get('views')
        record("L1", "A3", "objects 回退路径（老驱动无 filter）", True,
               "不崩、不报「暂不支持」，兜底 tables==全量, views==[]", evidence)
        a_results['A3'] = (True, j, None)
    except Exception as e:
        record("L1", "A3", "objects 回退路径（老驱动无 filter）", False,
               "回退路径源码逻辑产出错误输出: %s" % e,
               traceback.format_exc().splitlines()[-3:])
        a_results['A3'] = (False, None, None)

    # ---- A4: databases 兜底数据库名（list_database_names 抛异常）----
    print("\n[A4] api_ds_databases — 兜底数据库名（list_database_names 抛异常）")
    try:
        FakeMongoClient.CAPTURE.clear()
        set_scenario(
            databases=['admin', 'config', 'local', 'test'],
            db_name='test',
            collections_by_type={'standard': ['colA', 'colB'], 'view': ['viewX']},
            all_collections=['colA', 'colB', 'viewX'],
            raise_database=True,  # 模拟无法列出数据库
        )
        resp = call_databases(DS_ID)
        body = resp.get_data(as_text=True)
        j = json.loads(body)
        evidence = "databases=%r" % j.get('databases')

        assert 'error' not in j, "返回含 error: %r" % j
        assert no_unsupported(j), "兜底路径仍「暂不支持」: %r" % j
        # 兜底：databases = [inst.get('database') or 'admin'] = ['test']
        assert j['databases'] == ['test'], \
            "兜底 databases 应为 ['test']: %r" % j.get('databases')
        record("L1", "A4", "databases 兜底数据库名", True,
               "不崩、不报「暂不支持」，兜底 databases==['test']", evidence)
        a_results['A4'] = (True, j, None)
    except Exception as e:
        record("L1", "A4", "databases 兜底数据库名", False,
               "兜底路径源码逻辑产出错误输出: %s" % e,
               traceback.format_exc().splitlines()[-3:])
        a_results['A4'] = (False, None, None)

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
        set_scenario(
            databases=['admin', 'config', 'local', 'test'],
            db_name='test',
            collections_by_type={'standard': ['colA', 'colB'], 'view': ['viewX']},
            all_collections=['colA', 'colB', 'viewX'],
        )
        resp = client.get("/api/pro/datasources/%s/databases" % DS_ID)
        body = resp.get_data(as_text=True)
        try:
            j = resp.get_json(force=True) or {}
        except Exception:
            j = {}
        http_ok = resp.status_code != 400
        no_unsupported_b = '暂不支持该数据库类型' not in body
        has_field = 'databases' in j
        b1_evidence = "HTTP=%s json=%s" % (resp.status_code, j)
        if resp.status_code in (401, 403):
            layer2_status = "AUTH_GATE"
            record("L2", "B1", "databases 路由（端到端）", None,
                   "被认证门禁拦截(HTTP %d)，非本次 Bug，退回以第1层为准" % resp.status_code,
                   b1_evidence)
        else:
            b1_ok = bool(http_ok and no_unsupported_b and has_field)
            record("L2", "B1", "databases 路由（端到端，HTTP 不含'暂不支持'）", b1_ok,
                   "HTTP 正常且含 databases 字段（无'暂不支持'错误）"
                   if b1_ok else "HTTP=400 或含'暂不支持'错误", b1_evidence)

        # B2: /objects
        print("\n[B2] GET /api/pro/datasources/%s/objects?database=test" % DS_ID)
        set_scenario(
            databases=['admin', 'config', 'local', 'test'],
            db_name='test',
            collections_by_type={'standard': ['colA', 'colB'], 'view': ['viewX']},
            all_collections=['colA', 'colB', 'viewX'],
        )
        resp2 = client.get("/api/pro/datasources/%s/objects?database=test" % DS_ID)
        body2 = resp2.get_data(as_text=True)
        try:
            j2 = resp2.get_json(force=True) or {}
        except Exception:
            j2 = {}
        http_ok2 = resp2.status_code != 400
        no_unsupported2 = '暂不支持该数据库类型' not in body2
        has_field2 = ('tables' in j2) and ('views' in j2)
        b2_evidence = "HTTP=%s json=%s" % (resp2.status_code, j2)
        if resp2.status_code in (401, 403):
            layer2_status = "AUTH_GATE"
            record("L2", "B2", "objects 路由（端到端）", None,
                   "被认证门禁拦截(HTTP %d)，非本次 Bug，退回以第1层为准" % resp2.status_code,
                   b2_evidence)
        else:
            b2_ok = bool(http_ok2 and no_unsupported2 and has_field2)
            record("L2", "B2", "objects 路由（端到端，HTTP 不含'暂不支持'）", b2_ok,
                   "HTTP 正常且含 tables/views 字段（无'暂不支持'错误）"
                   if b2_ok else "HTTP=400 或含'暂不支持'错误", b2_evidence)

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
print("\n" + "=" * 72)
print("验证结果明细")
print("=" * 72)
for r in RESULTS:
    v = "PASS" if r["passed"] is True else ("FAIL" if r["passed"] is False else "N/A")
    print("  [%s] %-16s %s" % (v, r["layer"] + "/" + r["item"], r["name"]))

a1_ok = a_results.get('A1', (False,))[0]
a2_ok = a_results.get('A2', (False,))[0]
a3_ok = a_results.get('A3', (False,))[0]
a4_ok = a_results.get('A4', (False,))[0]

print("\n──────── 智能路由判定 ────────")
if not a1_ok:
    decision = "Engineer"
    reason = ("源码 Bug：api_ds_databases 的 mongodb 分支未生效/逻辑错误，"
              "返回「暂不支持该数据库类型」或非预期数据库列表（见 A1 失败用例）。")
elif not a2_ok:
    decision = "Engineer"
    reason = ("源码 Bug：api_ds_objects 的 mongodb 分支未生效/逻辑错误，"
              "返回「暂不支持该数据库类型」或集合/视图分流错误（见 A2 失败用例）。")
elif not a3_ok:
    decision = "Engineer"
    reason = "源码 Bug：api_ds_objects 回退路径（老驱动无 filter）未兜底，崩溃或报错（见 A3 失败用例）。"
elif not a4_ok:
    decision = "Engineer"
    reason = "源码 Bug：api_ds_databases 兜底数据库名未生效（见 A4 失败用例）。"
else:
    decision = "NoOne"
    reason = "第1层逻辑验证全部通过（A1~A4），MongoDB 两个树形导航接口在 mock 下已正确工作。"

print("  路由判定: %s" % decision)
print("  判定理由: %s" % reason)

print("\n──────── 关键证据 ────────")
a1_j = a_results.get('A1', (None, None, None))[2]
if a1_j:
    print("  A1 (databases 构造 URI 证据): %s" % a1_j)
a2_j = a_results.get('A2', (None, None, None))[2]
if a2_j:
    print("  A2 (objects 构造 URI 证据):   %s" % a2_j)
print("  L2 状态: %s" % layer2_status)
if b1_evidence:
    print("  B1 (databases 路由): %s" % b1_evidence)
if b2_evidence:
    print("  B2 (objects 路由):   %s" % b2_evidence)

print("\n完成。本脚本仅验证，未修改任何产品代码。决策: %s" % decision)
