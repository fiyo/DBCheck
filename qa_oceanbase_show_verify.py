# -*- coding: utf-8 -*-
"""
OceanBase 树形导航 SHOW→INFORMATION_SCHEMA 修复验证脚本
========================================================
目标：验证 web_ui.api_ds_databases / api_ds_objects 的 oceanbase 分支修复
     - 主用 INFORMATION_SCHEMA（与 MySQL/TiDB 一致），SHOW 仅作兜底
     - 连接 user 必须拼接 @tenant（tenant='test' → 'root@test'）
     - databases 分支不指定 database（连 user@tenant）
     - objects 分支指定 database=请求库

模拟用户真实失败模式：当 cursor 执行 SHOW DATABASES / SHOW FULL TABLES 时抛
pymysql.err.OperationalError(2013, 'Lost connection...')，而执行 INFORMATION_SCHEMA
查询时返回正常数据。证明主路径真的走到 INFORMATION_SCHEMA 并拿到结果，且不会触发 2013。

环境：py -3.12，D:/DBCheck。mock pymysql.connect，不连真实 OceanBase。

运行：py -3.12 qa_oceanbase_show_verify.py
"""
import sys
from unittest import mock

import web_ui
import pro
import pymysql


# ──────────────────────────────────────────────────────────────
# Fake 基础设施
# ──────────────────────────────────────────────────────────────
class FakeCursor:
    """按 SQL 文本分派：INFORMATION_SCHEMA 返回数据；SHOW 可配置为抛 2013 或返回数据。"""

    def __init__(self, info_fails=False, show_fails=False):
        self.executed = []          # 记录本 cursor 执行过的 SQL
        self._info_fails = info_fails
        self._show_fails = show_fails
        self._result = []

    def execute(self, sql, args=None):
        self.executed.append(sql)
        up = sql.upper()
        if "INFORMATION_SCHEMA" in up:
            if self._info_fails:
                # 模拟 INFORMATION_SCHEMA 失败 → 触发 except 兜底分支
                raise pymysql.err.OperationalError(
                    2013, 'Lost connection to MySQL server during query'
                )
            if "SCHEMATA" in up:
                self._result = [("sys",), ("test",), ("oceanbase",)]
            elif "TABLES" in up:
                self._result = [("t1", "BASE TABLE"), ("v1", "VIEW")]
            else:
                self._result = []
        elif "SHOW DATABASES" in up:
            if self._show_fails:
                raise pymysql.err.OperationalError(
                    2013, 'Lost connection to MySQL server during query'
                )
            self._result = [("sys",), ("test",), ("oceanbase",)]
        elif "SHOW FULL TABLES" in up:
            if self._show_fails:
                raise pymysql.err.OperationalError(
                    2013, 'Lost connection to MySQL server during query'
                )
            self._result = [("t1", "BASE TABLE"), ("v1", "VIEW")]
        else:
            self._result = []

    def fetchall(self):
        return list(self._result)

    def close(self):
        pass


class FakeConn:
    def __init__(self, info_fails=False, show_fails=False, **kwargs):
        self.kwargs = kwargs
        self._cursor = FakeCursor(info_fails=info_fails, show_fails=show_fails)

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def make_fake_connect(connect_calls, info_fails=False, show_fails=False):
    def fake_connect(**kwargs):
        conn = FakeConn(info_fails=info_fails, show_fails=show_fails, **kwargs)
        connect_calls.append({"kwargs": kwargs, "conn": conn})
        return conn
    return fake_connect


class FakeManager:
    """fake pro.InstanceManager，get_instance_decrypted 返回预置 inst。"""

    def __init__(self, inst):
        self._inst = inst

    def get_instance_decrypted(self, ds_id):
        return self._inst


# ──────────────────────────────────────────────────────────────
# 调用运行器（路由优先，直调兜底）
# ──────────────────────────────────────────────────────────────
def invoke(method, inst, ds_id, path, info_fails=False, show_fails=False):
    """通过 app.test_client() 调路由（优先），若被门禁拦截(非200)则直调函数兜底。
    返回 (json_data, connect_calls, source) 。"""
    connect_calls = []
    fc = make_fake_connect(connect_calls, info_fails=info_fails, show_fails=show_fails)
    mgr = FakeManager(inst)
    with mock.patch.object(pymysql, "connect", fc), \
         mock.patch.object(pro, "get_instance_manager", return_value=mgr):
        client = web_ui.app.test_client()
        resp = client.get(path)
    if resp.status_code == 200:
        return resp.get_json(), connect_calls, "route"
    # 兜底：直调函数（jsonify 需 app/test_request context）
    connect_calls = []
    fc = make_fake_connect(connect_calls, info_fails=info_fails, show_fails=show_fails)
    mgr = FakeManager(inst)
    with mock.patch.object(pymysql, "connect", fc), \
         mock.patch.object(pro, "get_instance_manager", return_value=mgr), \
         web_ui.app.test_request_context(path):
        if method == "databases":
            r = web_ui.api_ds_databases(ds_id)
        else:
            r = web_ui.api_ds_objects(ds_id)
        data = r.get_json() if hasattr(r, "get_json") else r[0].get_json()
    return data, connect_calls, "direct"


# ──────────────────────────────────────────────────────────────
# 断言辅助
# ──────────────────────────────────────────────────────────────
RESULTS = []


def check(name, ok, detail, kind="semantic"):
    RESULTS.append({"name": name, "pass": bool(ok), "detail": detail, "kind": kind})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}\n        {detail}")


def all_sql(connect_calls):
    out = []
    for c in connect_calls:
        out.extend(c["conn"]._cursor.executed)
    return out


def has_info_schema(connect_calls):
    return any("INFORMATION_SCHEMA" in s.upper() for s in all_sql(connect_calls))


def has_show(connect_calls):
    return any(
        ("SHOW DATABASES" in s.upper()) or ("SHOW FULL TABLES" in s.upper())
        for s in all_sql(connect_calls)
    )


def conn_user(connect_calls):
    return connect_calls[0]["kwargs"].get("user") if connect_calls else None


def conn_db(connect_calls):
    return connect_calls[0]["kwargs"].get("database") if connect_calls else None


# ──────────────────────────────────────────────────────────────
# 主验证流程
# ──────────────────────────────────────────────────────────────
def main():
    DS_ID = "ds_ob_1"
    PATH_DB = f"/api/pro/datasources/{DS_ID}/databases"
    PATH_OBJ = f"/api/pro/datasources/{DS_ID}/objects?database=test"

    # team-lead 指定 fake inst：tenant='test'（触发 2013 根因的关键字段），user 裸 'root'
    inst = {
        "db_type": "oceanbase", "host": "127.0.0.1", "port": 2881,
        "user": "root", "password": "x", "tenant": "test", "database": "",
    }

    print("=" * 72)
    print("OceanBase 树形导航 SHOW→INFORMATION_SCHEMA 修复验证")
    print("=" * 72)

    # ── C1：databases 主路径生效（SHOW 抛 2013，INFORMATION_SCHEMA 应被优先使用）──
    try:
        data, calls, src = invoke("databases", inst, DS_ID, PATH_DB,
                                  info_fails=False, show_fails=True)
        ok_data = (data.get("databases") == ["sys", "test", "oceanbase"])\
            and (data.get("db_type") == "oceanbase") and ("error" not in data)
        ok_primary = has_info_schema(calls) and (not has_show(calls))  # 走到 INFORMATION_SCHEMA 且未碰 SHOW
        ok_no2013 = "Lost connection" not in str(data.get("error", ""))
        ok = ok_data and ok_primary and ok_no2013
        check("C1 databases-主路径(INFORMATION_SCHEMA优先, 不触发2013)",
              ok,
              f"source={src} databases={data.get('databases')} db_type={data.get('db_type')} "
              f"info_used={has_info_schema(calls)} show_executed={has_show(calls)} error={data.get('error')}")
    except Exception as e:
        check("C1 databases-主路径(INFORMATION_SCHEMA优先, 不触发2013)", False,
              f"EXCEPTION: {e!r}", kind="harness")

    # ── C2：databases @tenant 拼接正确（user='root@test'，且不指定 database）──
    try:
        data, calls, src = invoke("databases", inst, DS_ID, PATH_DB,
                                  info_fails=False, show_fails=False)
        ok = (conn_user(calls) == "root@test") and ("database" not in calls[0]["kwargs"])
        check("C2 databases-@tenant拼接(user='root@test', 不指定database)",
              ok,
              f"source={src} connect_user={conn_user(calls)!r} "
              f"database_kwarg={conn_db(calls)!r} (期望 'root@test', 无 database)")
    except Exception as e:
        check("C2 databases-@tenant拼接(user='root@test', 不指定database)", False,
              f"EXCEPTION: {e!r}", kind="harness")

    # ── C3：databases 正常路径（INFORMATION_SCHEMA 直接成功）──
    try:
        data, calls, src = invoke("databases", inst, DS_ID, PATH_DB,
                                  info_fails=False, show_fails=False)
        ok_data = (data.get("databases") == ["sys", "test", "oceanbase"])\
            and (data.get("db_type") == "oceanbase") and ("error" not in data)
        ok_primary = has_info_schema(calls) and (not has_show(calls))
        ok = ok_data and ok_primary
        check("C3 databases-正常路径(INFORMATION_SCHEMA直接成功)",
              ok,
              f"source={src} databases={data.get('databases')} "
              f"info_used={has_info_schema(calls)} show_executed={has_show(calls)}")
    except Exception as e:
        check("C3 databases-正常路径(INFORMATION_SCHEMA直接成功)", False,
              f"EXCEPTION: {e!r}", kind="harness")

    # ── C4：objects 主路径生效（SHOW FULL TABLES 抛 2013，INFORMATION_SCHEMA.TABLES 应被优先使用）──
    try:
        data, calls, src = invoke("objects", inst, DS_ID, PATH_OBJ,
                                  info_fails=False, show_fails=True)
        ok_data = (data.get("tables") == ["t1"]) and (data.get("views") == ["v1"])\
            and (data.get("db_type") == "oceanbase") and ("error" not in data)
        ok_primary = has_info_schema(calls) and (not has_show(calls))
        ok_no2013 = "Lost connection" not in str(data.get("error", ""))
        ok = ok_data and ok_primary and ok_no2013
        check("C4 objects-主路径(INFORMATION_SCHEMA.TABLES优先, 不触发2013)",
              ok,
              f"source={src} tables={data.get('tables')} views={data.get('views')} "
              f"info_used={has_info_schema(calls)} show_executed={has_show(calls)} error={data.get('error')}")
    except Exception as e:
        check("C4 objects-主路径(INFORMATION_SCHEMA.TABLES优先, 不触发2013)", False,
              f"EXCEPTION: {e!r}", kind="harness")

    # ── C5：objects @tenant 拼接正确（user='root@test'，且 database=test）──
    try:
        data, calls, src = invoke("objects", inst, DS_ID, PATH_OBJ,
                                  info_fails=False, show_fails=False)
        ok = (conn_user(calls) == "root@test") and (conn_db(calls) == "test")
        check("C5 objects-@tenant拼接(user='root@test', database='test')",
              ok,
              f"source={src} connect_user={conn_user(calls)!r} connect_database={conn_db(calls)!r} "
              f"(期望 user='root@test', database='test')")
    except Exception as e:
        check("C5 objects-@tenant拼接(user='root@test', database='test')", False,
              f"EXCEPTION: {e!r}", kind="harness")

    # ── C6：objects 正常路径（INFORMATION_SCHEMA.TABLES 直接成功）──
    try:
        data, calls, src = invoke("objects", inst, DS_ID, PATH_OBJ,
                                  info_fails=False, show_fails=False)
        ok_data = (data.get("tables") == ["t1"]) and (data.get("views") == ["v1"])\
            and ("error" not in data)
        ok_primary = has_info_schema(calls) and (not has_show(calls))
        ok = ok_data and ok_primary
        check("C6 objects-正常路径(INFORMATION_SCHEMA.TABLES直接成功)",
              ok,
              f"source={src} tables={data.get('tables')} views={data.get('views')} "
              f"info_used={has_info_schema(calls)} show_executed={has_show(calls)}")
    except Exception as e:
        check("C6 objects-正常路径(INFORMATION_SCHEMA.TABLES直接成功)", False,
              f"EXCEPTION: {e!r}", kind="harness")

    # ── C7（兜底正确性验证）：INFORMATION_SCHEMA 失败 → SHOW 兜底生效 ──
    try:
        data, calls, src = invoke("databases", inst, DS_ID, PATH_DB,
                                  info_fails=True, show_fails=False)
        ok = (data.get("databases") == ["sys", "test", "oceanbase"])\
            and ("error" not in data) and has_show(calls)
        check("C7 兜底验证(INFORMATION_SCHEMA失败→SHOW DATABASES兜底生效)",
              ok,
              f"source={src} databases={data.get('databases')} "
              f"info_used={has_info_schema(calls)} show_executed={has_show(calls)} error={data.get('error')}")
    except Exception as e:
        check("C7 兜底验证(INFORMATION_SCHEMA失败→SHOW DATABASES兜底生效)", False,
              f"EXCEPTION: {e!r}", kind="harness")

    # ── 汇总报告 ──
    passed = sum(1 for r in RESULTS if r["pass"])
    total = len(RESULTS)
    print("\n" + "=" * 72)
    print(f"汇总: {passed}/{total} 通过")
    real_fail = [r for r in RESULTS if not r["pass"]]
    if real_fail:
        print("失败用例:")
        for r in real_fail:
            print(f"  - [{r['kind']}] {r['name']}: {r['detail']}")

    # 智能路由判定
    semantic_fails = [r for r in real_fail if r["kind"] == "semantic"]
    harness_fails = [r for r in real_fail if r["kind"] == "harness"]
    if semantic_fails:
        decision = "Engineer"
        reason = "web_ui.py 源码 Bug（修复逻辑未生效：主路径未走 INFORMATION_SCHEMA / @tenant 丢失 / 解析错误 / 触发2013）"
    elif harness_fails:
        decision = "QA(self-fix)"
        reason = "测试脚本自身问题（mock/app context 等），需自修重跑"
    else:
        decision = "NoOne"
        reason = "全部通过：OceanBase 两分支已主用 INFORMATION_SCHEMA 且 @tenant 拼接正确"
    print(f"智能路由判定: {decision} —— {reason}")
    return decision, real_fail


if __name__ == "__main__":
    main()
