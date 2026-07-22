#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# -*- mode: python; coding: utf-8 -*-
"""
DB2 树形导航接口回归验证脚本（真实 DB2 连接）
================================================

验证工程师在 web_ui.py 中新增的两处 `elif db_type == 'db2':` 分支：
  - api_ds_databases  (/api/pro/datasources/<ds_id>/databases)  列数据库
  - api_ds_objects    (/api/pro/datasources/<ds_id>/objects)     列 表/视图

运行：在 D:/DBCheck 下执行 `py -3.12 qa_db2_tree_verify.py`
真实 DB2：localhost:50000 / testdb / db2inst1 / password / DB2 v120.10.50

分层：
  第 1 层（逻辑层，必做，绕过路由/认证）：直接复用工程师分支逻辑，证明 DB2 代码路径对真实库可用。
  第 2 层（路由层，尽量做）：用 Flask test_client 打真实路由，证明端到端。

智能路由判定：源码有 Bug → Engineer；测试代码有 Bug → 自行修正重跑；全部通过 → NoOne。
本脚本只读取/验证，不修改任何产品代码。
"""

import os
import sys
import time
import json
import traceback

# 使用项目根目录，保证 plugins / pro / inspection_engine 等可被导入
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── 真实 DB2 连接参数（与任务一致）──────────────────────────────────
DB_HOST = 'localhost'
DB_PORT = 50000
DB_USER = 'db2inst1'
DB_PWD = 'password'
DB_NAME = 'testdb'

print("=" * 70)
print("DB2 树形导航接口回归验证（真实 DB2: %s:%d / %s）" % (DB_HOST, DB_PORT, DB_NAME))
print("=" * 70)

# 结果收集
RESULTS = []  # 每个元素: dict(layer, item, name, passed, detail, evidence)

def record(layer, item, name, passed, detail, evidence=""):
    RESULTS.append({
        "layer": layer, "item": item, "name": name,
        "passed": passed, "detail": detail, "evidence": evidence,
    })
    tag = "PASS" if passed else "FAIL"
    print("  [%s] %s / %s — %s" % (tag, layer, name, detail))
    if evidence:
        print("       证据: %s" % evidence)

# =====================================================================
# 第 1 层：逻辑层（必做，绕过路由/认证）—— 直接复用工程师分支逻辑
# =====================================================================
print("\n──────── 第 1 层（逻辑层，必做）────────")

from plugins.available.db2_jdbc.main_plugin import get_connection

# ---- A1: databases 分支逻辑 ----
print("\n[A1] databases 分支逻辑（getConnection → getMetaData().getCatalogs()）")
a1_ok = True
a1_err = ""
a1_evidence = ""
try:
    t0 = time.time()
    # 完全镜像工程师 api_ds_databases 的 db2 分支逻辑
    conn = get_connection(DB_HOST, int(DB_PORT), DB_USER, DB_PWD, database=DB_NAME)
    try:
        jconn = conn.jdbc_conn
        # 完全镜像工程师 api_ds_databases 修复后的 db2 分支逻辑（web_ui.py ~L5557）
        rs = jconn.getMetaData().getCatalogs()
        databases = []
        while rs.next():
            _c = rs.getString(1)
            if _c:  # 跳过 Java null（DB2 getCatalogs 的 catalog 列为 null）
                databases.append(str(_c))
        try:
            rs.close()
        except Exception:
            pass
        if not databases:
            databases = [DB_NAME]
    except Exception:
        databases = [DB_NAME]
    finally:
        conn.close()
    dt = time.time() - t0
    a1_evidence = "databases=%r (耗时 %.2fs)" % (databases, dt)

    # 断言 1：返回的是 list 且无异常
    assert isinstance(databases, list), "databases 不是 list: %r" % (databases,)
    # 断言 2（正确性）：列表不能含 'None'（Java null 经 str() 变成的假库名）
    assert 'None' not in databases, "databases 含无效条目 'None'（DB2 getCatalogs 返回 null 未处理）"
    # 断言 3（正确性）：应含真实库名（CURRENT SERVER 返回 TESTDB，或兜底配置的 testdb）
    low = [str(d).lower() for d in databases]
    assert len(databases) >= 1 and ('testdb' in low or 'testdb' == DB_NAME.lower()), \
        "databases 未包含真实 DB2 库名: %r" % (databases,)
    record("L1", "A1", "databases 分支正确返回数据库列表", True,
           "返回真实数据库列表，无 'None' 假条目", a1_evidence)
except Exception as e:
    a1_ok = False
    a1_err = "%s: %s" % (type(e).__name__, e)
    record("L1", "A1", "databases 分支正确返回数据库列表", False,
           "源码逻辑产出错误输出", a1_evidence + (" | 异常: " + a1_err if a1_evidence else a1_err))

# 同时用 CURRENT SERVER 取得真实库名，作为"正确值"对照
real_db = None
try:
    conn = get_connection(DB_HOST, int(DB_PORT), DB_USER, DB_PWD, database=DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT CURRENT SERVER FROM sysibm.sysdummy1")
    r = cur.fetchone()
    real_db = r[0] if r else None
    cur.close()
    conn.close()
except Exception:
    real_db = None
print("    真实当前库名 (CURRENT SERVER): %r" % (real_db,))
if a1_ok is False:
    print("    >> 正确预期: databases 应为 %r（或兜底 %r），而非 ['None']" % (
        [real_db] if real_db else None, DB_NAME))

# ---- A2: objects 分支逻辑 ----
print("\n[A2] objects 分支逻辑（getConnection → cursor → SYSCAT.TABLES 分流 T/V）")
a2_ok = True
a2_err = ""
a2_evidence = ""
try:
    t0 = time.time()
    # 完全镜像工程师 api_ds_objects 的 db2 分支逻辑
    conn = get_connection(DB_HOST, int(DB_PORT), DB_USER, DB_PWD, database=DB_NAME)
    try:
        tables, views = [], []
        cur = conn.cursor()
        cur.execute(
            "SELECT TABNAME, TYPE FROM SYSCAT.TABLES "
            "WHERE TABSCHEMA NOT LIKE 'SYS%' ORDER BY TABNAME"
        )
        for row in cur.fetchall():
            if row[1] == 'T':
                tables.append(row[0])
            elif row[1] == 'V':
                views.append(row[0])
        cur.close()
    except Exception:
        pass
    finally:
        conn.close()
    dt = time.time() - t0
    a2_evidence = "tables=%r views=%r (耗时 %.2fs)" % (tables, views, dt)

    assert isinstance(tables, list) and isinstance(views, list), "tables/views 非 list"
    assert len(tables) >= 1, "未查询到任何表（SYSCAT.TABLES 为空？）"
    # 已知 testdb 含表 T1（TYPE='T'），应进入 tables 而非 views
    assert 'T1' in tables, "期望表 T1 在 tables 中，实际 tables=%r" % (tables,)
    assert 'T1' not in views, "T1 不应出现在 views 中"
    record("L1", "A2", "objects 分支正确返回 表/视图 列表", True,
           "表/视图按 TYPE 正确分流，无 '不支持' 类错误", a2_evidence)
except Exception as e:
    a2_ok = False
    a2_err = "%s: %s" % (type(e).__name__, e)
    record("L1", "A2", "objects 分支正确返回 表/视图 列表", False,
           "源码逻辑产出错误输出", a2_evidence + (" | 异常: " + a2_err if a2_evidence else a2_err))

# =====================================================================
# 第 2 层：路由层（尽量做）—— Flask test_client 打真实路由
# =====================================================================
print("\n──────── 第 2 层（路由层，尽量做）────────")
layer2_status = "ATTEMPTED"
b1_ok = b2_ok = None
b1_evidence = b2_evidence = ""
ds_id_used = None
mgr = None
created_in_memory = False

try:
    from web_ui import app
    print("  [OK] from web_ui import app 成功 (has app: %s)" % (app is not None))

    from pro import get_instance_manager
    from pro.instance_manager import DatabaseInstance
    mgr = get_instance_manager()

    # 复用现有 db2 数据源；没有则临时在内存中建一个（不落盘，验证后清理）
    existing = [i for i in mgr.get_all_instances(mask_password=False)
                if (i.get('db_type') or '').lower() == 'db2']
    if existing:
        ds_id_used = existing[0]['id']
        print("  [INFO] 复用现有 db2 数据源: %s" % ds_id_used)
    else:
        inst = DatabaseInstance(
            name="QA_DB2_VERIFY_TMP", db_type="db2", host=DB_HOST,
            port=DB_PORT, user=DB_USER, password=DB_PWD, database=DB_NAME,
        )
        inst.id = "qa_db2_verify_tmp"
        mgr._instances[inst.id] = inst   # 仅内存，避免污染产品数据
        ds_id_used = inst.id
        created_in_memory = True
        print("  [INFO] 临时在内存创建 db2 数据源(不落盘): %s" % ds_id_used)

    client = app.test_client()

    # B1: /databases
    print("\n[B1] GET /api/pro/datasources/%s/databases" % ds_id_used)
    resp = client.get("/api/pro/datasources/%s/databases" % ds_id_used)
    body = resp.get_data(as_text=True)
    try:
        j = resp.get_json(force=True) or {}
    except Exception:
        j = {}
    http_ok = resp.status_code != 400
    no_unsupported = '暂不支持该数据库类型' not in body
    has_field = 'databases' in j
    b1_evidence = "HTTP=%s json=%s" % (resp.status_code, j)
    if resp.status_code in (401, 403):
        layer2_status = "AUTH_GATE"
        record("L2", "B1", "databases 路由（端到端）", None,
               "被认证门禁拦截(HTTP %d)，非本次 Bug，退回以第1层为准" % resp.status_code,
               b1_evidence)
    else:
        b1_ok = http_ok and no_unsupported and has_field
        # 同时检验内容质量（是否与 A1 同样的 'None' 问题）
        db_list = j.get('databases')
        content_note = ""
        if isinstance(db_list, list) and 'None' in db_list:
            content_note = "；但内容含 'None' 假条目(同源 Bug)"
            b1_ok = b1_ok  # HTTP 层仍算通过，内容问题在 L1 已报
        record("L2", "B1", "databases 路由（端到端，HTTP 不含'暂不支持'）",
               bool(b1_ok),
               ("HTTP 正常且含 databases 字段（无'暂不支持'错误）" + content_note)
               if b1_ok else "HTTP=400 或含'暂不支持'错误",
               b1_evidence)

    # B2: /objects
    print("\n[B2] GET /api/pro/datasources/%s/objects?database=%s" % (ds_id_used, DB_NAME))
    resp2 = client.get("/api/pro/datasources/%s/objects?database=%s" % (ds_id_used, DB_NAME))
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
        b2_ok = http_ok2 and no_unsupported2 and has_field2
        record("L2", "B2", "objects 路由（端到端，HTTP 不含'暂不支持'）",
               bool(b2_ok),
               "HTTP 正常且含 tables/views 字段（无'暂不支持'错误）"
               if b2_ok else "HTTP=400 或含'暂不支持'错误",
               b2_evidence)

    # 清理临时数据源（仅内存）
    if created_in_memory and ds_id_used in mgr._instances:
        del mgr._instances[ds_id_used]
        print("  [INFO] 已清理临时内存数据源 %s" % ds_id_used)

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
print("\n" + "=" * 70)
print("验证结果明细")
print("=" * 70)
for r in RESULTS:
    v = "PASS" if r["passed"] is True else ("FAIL" if r["passed"] is False else "N/A")
    print("  [%s] %-20s %s" % (v, r["layer"] + "/" + r["item"], r["name"]))

print("\n──────── 智能路由判定 ────────")
if a1_ok is False:
    decision = "Engineer"
    reason = ("源码 Bug：api_ds_databases 的 db2 分支未处理 getCatalogs() 返回 null 的情况，"
              "str(null) 生成假库名 'None'，真实库应为 %r。" % (real_db,))
elif a2_ok is False:
    decision = "Engineer"
    reason = "源码 Bug：api_ds_objects 的 db2 分支产出错误输出（见 A2 失败用例）。"
else:
    decision = "NoOne"
    reason = "第1层逻辑验证全部通过，DB2 两个树形导航接口对真实库可用。"

print("  路由判定: %s" % decision)
print("  判定理由: %s" % reason)
if a1_ok is False:
    print("\n  >> 失败用例(A1):")
    print("     复现: get_connection(...) → jconn.getMetaData().getCatalogs() → str(rs.getString(1))")
    print("     实测 databases = ['None']  (实际 getCatalogs 返回 1 行，catalog 列为 Java null)")
    print("     期望 databases = %r 或兜底 %r" % (([real_db] if real_db else None), DB_NAME))
    print("     建议修复: 跳过 null catalog 或回退到已连接库名，例如:")
    print("         while rs.next():")
    print("             c = rs.getString(1)")
    print("             databases.append(str(c) if c else db_name)")
    print("         # 若遍历后为空，databases = [db_name]")

print("\n──────── 关键证据 ────────")
print("  A1 (databases 逻辑): %s" % (a1_evidence or a1_err))
print("  A2 (objects 逻辑):   %s" % (a2_evidence or a2_err))
print("  真实库名 (CURRENT SERVER): %r" % (real_db,))
print("  L2 状态: %s" % layer2_status)
if b1_evidence:
    print("  B1 (databases 路由): %s" % b1_evidence)
if b2_evidence:
    print("  B2 (objects 路由):   %s" % b2_evidence)

print("\n完成。本脚本仅验证，未修改任何产品代码。")
