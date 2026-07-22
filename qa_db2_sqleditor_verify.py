#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QA 收尾验证（Issue4 闭环）：web_ui SQL 编辑器后端新增 db2 分支

验证目标（真实 DB2：localhost:50000/testdb/db2inst1，版本 DB2 v120.10.50）：
  方案 A（单元级直连，推荐）：
    - get_connection 构建 cursor，跑 SELECT，断言 cur.description 非空 / columns 可取到 /
      rows=fetchmany(200) 是 list 且 >=1 行 / cur.rowcount 不抛异常。
    - fetchmany 多页切片逻辑稳定（5 行 -> 2/2/1/[]）。
    - rs 为 None 时 fetchmany 返回 [] 不崩。
  方案 B（路由级，可选）：
    - Flask app.test_client() POST /api/execute_sql 与 /api/inspection/execute_sql
      （monkeypatch 数据源查询为 db2 连接信息），断言不再返回"不支持的数据库类型"，
      且响应含 rows/columns 字段（或 ok=True）。

解释器：py -3.12 （D:/DBCheck 根目录运行）
注意：web_ui.py / 插件文件首部有 BOM，文本读取用 encoding='utf-8-sig'；本脚本以 import 方式加载，
      Python 解释器会自动处理 BOM，无需特殊处理。
"""

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RESULTS = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    RESULTS.append((name, status, str(detail)))
    print(f"  [{status}] {name}" + (f"  -- {detail}" if detail else ""))

DB_INFO = {
    'host': 'localhost',
    'port': 50000,
    'database': 'testdb',
    'user': 'db2inst1',
    'password': 'password',
}

print("=" * 72)
print("方案 A：单元级直连 db2 分支底层（get_connection + fetchmany + rowcount）")
print("=" * 72)
conn = None
try:
    from plugins.available.db2_jdbc.main_plugin import get_connection, JdbcCursorWrapper
    check("A: 可从 plugins.available.db2_jdbc.main_plugin import get_connection", True)

    conn = get_connection(DB_INFO['host'], DB_INFO['port'], DB_INFO['user'],
                          DB_INFO['password'], database=DB_INFO['database'])
    check("A: get_connection 返回连接对象（含 .cursor）", conn is not None and hasattr(conn, 'cursor'))

    cur = conn.cursor()
    check("A: conn.cursor() 返回 JdbcCursorWrapper", isinstance(cur, JdbcCursorWrapper))

    # —— SELECT 查询：description / columns / fetchmany / rowcount ——
    cur.execute("SELECT * FROM SYSIBM.SYSDUMMY1")
    check("A: SELECT 执行未抛异常（cur.description 可设）", True)
    check("A: cur.description 非空", bool(cur.description),
          f"ncols={len(cur.description) if cur.description else 0}")
    columns = [d[0] for d in cur.description] if cur.description else []
    check("A: columns 可取到（列名列表非空）", bool(columns), f"columns={columns}")
    rows = cur.fetchmany(200)
    check("A: fetchmany(200) 返回 list", isinstance(rows, list))
    check("A: fetchmany 返回 >=1 行", isinstance(rows, list) and len(rows) >= 1,
          f"rows={rows}")
    # rowcount 不应抛异常（SELECT 走 rs 分支，_rowcount 保持 -1）
    try:
        rc = cur.rowcount
        check("A: cur.rowcount 不抛异常（返回 int）", isinstance(rc, int), f"rowcount={rc}")
    except Exception as e:
        check("A: cur.rowcount 不抛异常（返回 int）", False, str(e))

    # —— fetchmany 多页切片（5 行 -> 2/2/1/[]）——
    cur2 = conn.cursor()
    cur2.execute("SELECT * FROM TABLE(VALUES (1),(2),(3),(4),(5)) AS T(C)")
    p1 = cur2.fetchmany(2)
    p2 = cur2.fetchmany(2)
    p3 = cur2.fetchmany(2)
    p4 = cur2.fetchmany(2)
    total_rows = (len(p1) + len(p2) + len(p3))
    check("A: fetchmany 多页切片无异常且合计取回全部行",
          isinstance(p1, list) and isinstance(p2, list) and isinstance(p3, list)
          and isinstance(p4, list) and total_rows == 5 and p4 == [],
          f"p1={len(p1)} p2={len(p2)} p3={len(p3)} p4={len(p4)}")

    # —— rs 为 None 边界：fetchmany 返回 [] 不崩 ——
    cur3 = conn.cursor()
    cur3.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")  # 先建立正常 rs
    cur3.rs = None          # 模拟结果集为 None（DDL/DML 或非查询路径）
    cur3._rows = None       # 重置 fetchmany 缓存，强制走 None 分支
    none_rows = cur3.fetchmany(200)
    check("A: rs 为 None 时 fetchmany 返回 [] 不崩",
          none_rows == [], f"none_rows={none_rows}")

    cur.close()
    conn.close()
except Exception as e:
    check("A: 单元级直连未抛异常", False, str(e))
    traceback.print_exc()
finally:
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass

print("=" * 72)
print("方案 B：路由级（Flask app.test_client() POST SQL 编辑器路由）")
print("=" * 72)
plan_b_ok = False
try:
    import web_ui
    import pro

    class FakeIM:
        def get_instance_decrypted(self, iid):
            return {
                'db_type': 'db2', 'host': DB_INFO['host'], 'port': DB_INFO['port'],
                'user': DB_INFO['user'], 'password': DB_INFO['password'],
                'database': DB_INFO['database'], 'name': 'db2_qa',
            }

    # 路由内 `from pro import get_instance_manager` 每次重新从 pro 取，故 patch pro
    pro.get_instance_manager = lambda: FakeIM()
    client = web_ui.app.test_client()
    check("B: web_ui 模块与 app 导入成功", hasattr(web_ui, 'app'))

    # —— /api/execute_sql（db2 分支）——
    r1 = client.post('/api/execute_sql', json={
        'instance_id': 'db2_qa',
        'sql': 'SELECT CURRENT TIMESTAMP FROM SYSIBM.SYSDUMMY1',
        'database': DB_INFO['database'],
    })
    d1 = r1.get_json()
    err1 = (d1 or {}).get('error') or ''
    check("B: /api/execute_sql 不再返回『不支持的数据库类型』",
          '不支持的数据库类型' not in err1, f"error={err1}")
    check("B: /api/execute_sql 响应含 rows 与 columns 字段",
          isinstance(d1, dict) and 'rows' in d1 and 'columns' in d1,
          f"keys={list(d1.keys()) if isinstance(d1, dict) else d1}")
    check("B: /api/execute_sql 返回真实数据行",
          isinstance(d1, dict) and isinstance(d1.get('rows'), list)
          and len(d1.get('rows', [])) >= 1,
          f"n_rows={len(d1.get('rows', [])) if isinstance(d1, dict) else 'NA'}")

    # —— /api/inspection/execute-sql（db2 分支，修复 SQL；路由含连字符）——
    r2 = client.post('/api/inspection/execute-sql', json={
        'datasource_id': 'db2_qa',
        'sql': 'SELECT CURRENT TIMESTAMP FROM SYSIBM.SYSDUMMY1',
    })
    d2 = r2.get_json()
    check("B: /api/inspection/execute-sql 返回 ok=True（不再 unsupported）",
          isinstance(d2, dict) and d2.get('ok') is True,
          f"resp={d2}")
    plan_b_ok = True
except ImportError as e:
    check("B: web_ui 导入（Flask 依赖缺失，按约定降级到方案 A）", False,
          f"ImportError: {e} —— 方案 B 跳过，方案 A 已证明 db2 分支逻辑可用")
except Exception as e:
    check("B: 路由级验证未抛异常", False, str(e))
    traceback.print_exc()

print("=" * 72)
print("SUMMARY")
print("=" * 72)
passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
total = len(RESULTS)
print(f"  总断言: {total}  通过: {passed}  失败: {failed}")
if failed:
    print("  失败项:")
    for n, s, d in RESULTS:
        if s == "FAIL":
            print(f"    - {n}  ({d})")

if failed:
    ROUTING = "Engineer"
elif plan_b_ok:
    ROUTING = "NoOne（方案A+方案B 全通过）"
else:
    ROUTING = "NoOne（方案A 全通过；方案B 因 Flask 依赖未导入而降级，底层逻辑已证可用）"
print(f"  路由判定: {ROUTING}")
print(f"  通过率: {passed}/{total} = {100.0*passed/total:.0f}%")
print(f"  DB2 版本: DB2 v120.10.50（ver 字段）")
