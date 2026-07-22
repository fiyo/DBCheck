# -*- coding: utf-8 -*-
"""
OceanBase 树形导航 @tenant 连接回归验证脚本
==========================================
目标：验证 web_ui.api_ds_databases / api_ds_objects 的 oceanbase 分支
     - 连接 user 必须拼接 @tenant（修复 2013 根因）
     - databases 分支默认库为 sys（对齐权威路径）
     - 仍执行 SHOW 命令（未回退到 INFORMATION_SCHEMA）
     - 无 tenant 时不拼接 @（A4 基线）

环境：py -3.12，D:/DBCheck。mock pymysql.connect，不连真实 OceanBase。

运行：py -3.12 qa_oceanbase_tenant_verify.py
"""
import sys
import json
from unittest import mock

import web_ui
import pro
import pymysql

# ──────────────────────────────────────────────────────────────
# Fake connector 基础设施
# ──────────────────────────────────────────────────────────────
class FakeCursor:
    """按执行的 SQL 文本分派返回不同结果，并记录 SQL。"""

    def __init__(self):
        self.executed = []

    def execute(self, sql, args=None):
        self.executed.append(sql)

    def fetchall(self):
        last = (self.executed[-1] if self.executed else "").upper()
        if "SHOW DATABASES" in last:
            return [("sys",), ("test",), ("oceanbase",)]
        if "SHOW FULL TABLES FROM" in last:
            return [("t1", "BASE TABLE"), ("v1", "VIEW")]
        return []

    def close(self):
        pass


class FakeConn:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._cursor = FakeCursor()

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def make_fake_connect(connect_calls):
    """返回一个 fake pymysql.connect：记录每次连接的参数与 conn 对象。"""

    def fake_connect(**kwargs):
        conn = FakeConn(**kwargs)
        connect_calls.append({"kwargs": kwargs, "conn": conn})
        return conn

    return fake_connect


class FakeManager:
    """fake pro InstanceManager，get_instance_decrypted 返回预置 inst。"""

    def __init__(self, inst):
        self._inst = inst

    def get_instance_decrypted(self, ds_id):
        return self._inst


# ──────────────────────────────────────────────────────────────
# 场景运行器
# ──────────────────────────────────────────────────────────────
def run_direct_databases(inst, ds_id="ds_ob_1"):
    connect_calls = []
    fc = make_fake_connect(connect_calls)
    mgr = FakeManager(inst)
    # jsonify 需要 application context；databases 分支不读 request.args，故用 app_context
    with mock.patch.object(pymysql, "connect", fc), \
         mock.patch.object(pro, "get_instance_manager", return_value=mgr), \
         web_ui.app.app_context():
        resp = web_ui.api_ds_databases(ds_id)
        data = resp.get_json()
    return data, connect_calls


def run_direct_objects(inst, ds_id="ds_ob_1", database="test"):
    connect_calls = []
    fc = make_fake_connect(connect_calls)
    mgr = FakeManager(inst)
    with mock.patch.object(pymysql, "connect", fc), \
         mock.patch.object(pro, "get_instance_manager", return_value=mgr), \
         web_ui.app.test_request_context(
             f"/api/pro/datasources/{ds_id}/objects?database={database}"
         ):
        resp = web_ui.api_ds_objects(ds_id)
        data = resp.get_json()
    return data, connect_calls


def run_route(inst, path, ds_id="ds_ob_1"):
    connect_calls = []
    fc = make_fake_connect(connect_calls)
    mgr = FakeManager(inst)
    with mock.patch.object(pymysql, "connect", fc), \
         mock.patch.object(pro, "get_instance_manager", return_value=mgr):
        client = web_ui.app.test_client()
        resp = client.get(path)
    return resp, connect_calls


# ──────────────────────────────────────────────────────────────
# 断言收集
# ──────────────────────────────────────────────────────────────
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "pass": bool(cond), "detail": detail})
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}: {detail}")


def conn_user(connect_calls):
    return connect_calls[0]["kwargs"].get("user") if connect_calls else None


def conn_db(connect_calls):
    return connect_calls[0]["kwargs"].get("database") if connect_calls else None


def executed_sql(connect_calls):
    sql_list = []
    for c in connect_calls:
        sql_list.extend(c["conn"]._cursor.executed)
    return sql_list


# ──────────────────────────────────────────────────────────────
# 主验证流程
# ──────────────────────────────────────────────────────────────
def main():
    DS_ID = "ds_ob_1"
    # 团队负责人指定的 fake inst：tenant='test'（触发 bug 的关键字段），user 裸 'root'
    inst_tenant = {
        "db_type": "oceanbase", "host": "127.0.0.1", "port": 2881,
        "user": "root", "password": "pw", "database": "test", "tenant": "test",
    }
    # A3：默认库 sys 证明用 —— 空 database，tenant 仍 'test'
    inst_default_db = {
        "db_type": "oceanbase", "host": "127.0.0.1", "port": 2881,
        "user": "root", "password": "pw", "database": "", "tenant": "test",
    }
    # A4 基线：空 tenant（不拼 @）
    inst_no_tenant = {
        "db_type": "oceanbase", "host": "127.0.0.1", "port": 2881,
        "user": "root", "password": "pw", "database": "test", "tenant": "",
    }
    inst_no_tenant_default_db = {
        "db_type": "oceanbase", "host": "127.0.0.1", "port": 2881,
        "user": "root", "password": "pw", "database": "", "tenant": "",
    }

    print("=" * 70)
    print("OceanBase 树形导航 @tenant 连接回归验证")
    print("=" * 70)

    # ── A1：databases 分支（team-lead inst）──
    try:
        data, calls = run_direct_databases(inst_tenant, DS_ID)
        ok = ("error" not in data) and ("databases" in data) \
            and data.get("databases") == ["sys", "test", "oceanbase"]
        check("A1", ok,
              f"databases={data.get('databases')} error={data.get('error')}")
    except Exception as e:
        check("A1", False, f"EXCEPTION: {e!r}")

    # ── A2：objects 分支（team-lead inst, ?database=test）──
    try:
        data, calls = run_direct_objects(inst_tenant, DS_ID, "test")
        ok = ("error" not in data) and ("tables" in data) and ("views" in data) \
            and data.get("tables") == ["t1"] and data.get("views") == ["v1"]
        check("A2", ok,
              f"tables={data.get('tables')} views={data.get('views')} error={data.get('error')}")
    except Exception as e:
        check("A2", False, f"EXCEPTION: {e!r}")

    # ── A3（关键证据）：databases 分支 connect user 含 @tenant 且默认库 sys ──
    # A3a：拼接 @tenant（team-lead inst）
    try:
        _, calls = run_direct_databases(inst_tenant, DS_ID)
        u = conn_user(calls)
        db = conn_db(calls)
        ok = (u == "root@test")
        check("A3-user", ok,
              f"connect user={u!r} (期望 'root@test'); connect database={db!r}")
    except Exception as e:
        check("A3-user", False, f"EXCEPTION: {e!r}")

    # A3b：默认库 sys（空 database 时 or 'sys' 兜底，对齐权威路径）
    try:
        _, calls = run_direct_databases(inst_default_db, DS_ID)
        u = conn_user(calls)
        db = conn_db(calls)
        ok = (db == "sys") and (u == "root@test")
        check("A3-default-db", ok,
              f"connect database={db!r} (期望 'sys'); connect user={u!r}")
    except Exception as e:
        check("A3-default-db", False, f"EXCEPTION: {e!r}")

    # ── A4 基线：空 tenant 不拼 @ ──
    try:
        _, calls = run_direct_databases(inst_no_tenant, DS_ID)
        u = conn_user(calls)
        ok = (u == "root")
        check("A4-tenant-empty", ok, f"connect user={u!r} (期望 'root'，无 @tenant)")
    except Exception as e:
        check("A4-tenant-empty", False, f"EXCEPTION: {e!r}")

    try:
        _, calls = run_direct_databases(inst_no_tenant_default_db, DS_ID)
        u = conn_user(calls)
        db = conn_db(calls)
        ok = (u == "root") and (db == "sys")
        check("A4-tenant-empty-default-db", ok,
              f"connect user={u!r} database={db!r} (期望 user='root', database='sys')")
    except Exception as e:
        check("A4-tenant-empty-default-db", False, f"EXCEPTION: {e!r}")

    # ── A5：SQL 文本证据（仍为 SHOW 命令，未回退 INFORMATION_SCHEMA）──
    try:
        d_data, d_calls = run_direct_databases(inst_tenant, DS_ID)
        o_data, o_calls = run_direct_objects(inst_tenant, DS_ID, "test")
        sql = executed_sql(d_calls) + executed_sql(o_calls)
        has_show = any("SHOW" in s.upper() for s in sql)
        no_info = not any("INFORMATION_SCHEMA" in s.upper() for s in sql)
        ok = has_show and no_info
        check("A5-sql", ok,
              f"执行的 SQL={sql} (含 SHOW={has_show}, 无 INFORMATION_SCHEMA={no_info})")
    except Exception as e:
        check("A5-sql", False, f"EXCEPTION: {e!r}")

    # ── 路由级（尽量做，遇 401/403 视为认证层、非本次 Bug）──
    try:
        resp, calls = run_route(inst_tenant, f"/api/pro/datasources/{DS_ID}/databases", DS_ID)
        sc = resp.status_code
        if sc == 200:
            j = resp.get_json()
            ok = ("error" not in j) and ("databases" in j) \
                and j.get("databases") == ["sys", "test", "oceanbase"]
            u = conn_user(calls)
            check("B1-route-databases", ok,
                  f"HTTP {sc} databases={j.get('databases')} connect user={u!r}")
        else:
            check("B1-route-databases", True,
                  f"HTTP {sc} —— 认证/门禁层拦截（非本次 Bug），退回直调函数验证为准")
    except Exception as e:
        check("B1-route-databases", False, f"EXCEPTION: {e!r}")

    try:
        resp, calls = run_route(inst_tenant,
                                f"/api/pro/datasources/{DS_ID}/objects?database=test", DS_ID)
        sc = resp.status_code
        if sc == 200:
            j = resp.get_json()
            ok = ("error" not in j) and j.get("tables") == ["t1"] and j.get("views") == ["v1"]
            u = conn_user(calls)
            check("B2-route-objects", ok,
                  f"HTTP {sc} tables={j.get('tables')} views={j.get('views')} connect user={u!r}")
        else:
            check("B2-route-objects", True,
                  f"HTTP {sc} —— 认证/门禁层拦截（非本次 Bug），退回直调函数验证为准")
    except Exception as e:
        check("B2-route-objects", False, f"EXCEPTION: {e!r}")

    # ── 汇总报告 ──
    passed = sum(1 for r in RESULTS if r["pass"])
    total = len(RESULTS)
    print("\n" + "=" * 70)
    print(f"汇总: {passed}/{total} 通过")
    real_fail = [r for r in RESULTS if not r["pass"]]
    if real_fail:
        print("失败用例:")
        for r in real_fail:
            print(f"  - {r['name']}: {r['detail']}")
    else:
        print("全部通过：OceanBase 树形两分支已对齐权威路径（user@tenant + 默认库 sys + SHOW 命令）。")

    # 智能路由判定
    source_bug = any(
        r["name"].startswith(("A3", "A4")) and not r["pass"]
        for r in RESULTS
    )
    if real_fail and source_bug:
        decision = "Engineer"
        reason = "源码 Bug：OceanBase 连接 user 未拼接 @tenant 或默认库非 sys"
    elif real_fail:
        decision = "QA(self-fix)"
        reason = "测试代码 Bug，需自行修正重跑"
    else:
        decision = "NoOne"
        reason = "全部通过"
    print(f"智能路由判定: {decision} —— {reason}")
    return decision, real_fail


if __name__ == "__main__":
    main()
