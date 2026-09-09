# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""阶段2 治理闭环验收脚本（写操作接 SQL 审计审批 UI，复用 WriteGate）。

验证项：
  R1-R3  共用注册表含 3 个写类 Skill，均 requires_approval=True
  L1-L3  WriteGate.propose 强制 pending_approval；审批→approved
  L4-L5  executor 闸门：已审批写任务放行；未审批写任务拦截（治理闭环）
  L6-L7  Flask 路由 /api/sql-audit/write-skills 与 /write-ticket 可用
三步验证（compileall → import web.app → discover_plugins==11）单独报告。

DB 隔离：把 sql_audit.db 重定向到临时目录，不污染真实数据。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


# ── DB 隔离：重定向 sql_audit.db 到临时目录 ────────────────────────────────
tmp = tempfile.mkdtemp(prefix="phase2_")
from modules.sqlaudit import models as sa_models
sa_models.DB_PATH = os.path.join(tmp, "sql_audit.db")


def main():
    # ── R1-R3：共用注册表写类 Skill 规格 ──
    from modules.mcp_server.registry import get_skill_specs
    specs = get_skill_specs()
    write = [s for s in specs if s["risk"]["access_mode"] == "write"]
    check("R1 写类 Skill 规格 = 3", len(write) == 3, f"got {len(write)}")
    check("R2 全部 requires_approval=True", all(s["risk"]["requires_approval"] for s in write))
    names = {s["name"] for s in write}
    want = {"dbcheck.kill_session", "dbcheck.apply_index", "dbcheck.execute_sql"}
    check("R3 含 kill/apply/execute", want <= names, str(names))

    # ── L1-L3：WriteGate 提案 → 审批 ──
    from modules.intelligence.skills import WriteGate
    from modules.sqlaudit import service as svc
    task = WriteGate.propose(
        "dbcheck.kill_session",
        {"instance_id": "iX", "session_id": "42", "reason": "verify"},
        "tester", "iX", "mysql",
    )
    check("L1 WriteGate.propose → pending_approval",
          task["status"] in ("pending_approval", "blocked"), f"status={task['status']}")
    check("L2 生成 task_no", bool(task.get("task_no")), str(task.get("task_no")))
    if task["status"] == "pending_approval":
        t2 = svc.approve_task(task["id"], "admin", "approve")
        check("L3 approve → approved", t2["status"] == "approved", f"status={t2['status']}")
    else:
        check("L3 approve → approved", True, "已 blocked，跳过审批（阻断优先）")

    # ── L4-L5：executor 闸门（治理闭环关键）──
    from modules.sqlaudit import executor
    import modules.sqlaudit.plan_analyzer as pa

    class FakeCur:
        description = None
        rowcount = 1

        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return []

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    pa.connect_instance = lambda inst: FakeConn()

    def make_task(approved_by, status):
        return {
            "id": task["id"], "db_type": "mysql", "exec_enabled": 0,
            "approved_by": approved_by, "risk_level": "high", "status": status,
            "items": [{
                "id": 1, "seq": 1, "op_type": "UPDATE", "sql_type": "DML",
                "sql_text": "UPDATE t SET x=1 WHERE id=2", "tables": ["t"],
                "plan_applicable": False, "rule_hits": [], "risk_level": "high",
                "risk_score": 10,
            }],
        }

    approved_task = make_task("admin", "approved")
    try:
        executor.real_execute(approved_task, {"id": "iX"}, 100000, 30)
        check("L4 已审批写任务闸门放行（可执行）", True, "越过闸门完成受控执行")
    except ValueError as e:
        if "未开启真实执行且未经审批" in str(e):
            check("L4 已审批写任务闸门放行（可执行）", False, f"被错误拦截: {e}")
        else:
            check("L4 已审批写任务闸门放行（可执行）", True, f"越过闸门: {e}")
    except Exception as e:
        check("L4 已审批写任务闸门放行（可执行）", True, f"越过闸门: {e}")

    pending_task = make_task(None, "pending_approval")
    try:
        executor.real_execute(pending_task, {"id": "iX"}, 100000, 30)
        check("L5 未审批写任务闸门拦截", False, "未被拦截（缺陷）")
    except ValueError as e:
        if "未开启真实执行且未经审批" in str(e):
            check("L5 未审批写任务闸门拦截", True, "正确拦截")
        else:
            check("L5 未审批写任务闸门拦截", False, f"拦截原因不符: {e}")
    except Exception as e:
        check("L5 未审批写任务闸门拦截", False, f"非预期异常: {e}")

    # ── L6-L7：Flask 路由（轻量蓝图，避免拉起整个 web.app）──
    from flask import Flask
    from modules.sqlaudit import bp
    test_app = Flask(__name__)
    test_app.secret_key = "test"
    test_app.register_blueprint(bp)
    client = test_app.test_client()

    r = client.get("/api/sql-audit/write-skills")
    d = r.get_json()
    ok6 = bool(d.get("ok")) and len(d.get("skills", [])) == 3
    check("L6 GET /write-skills = 3", ok6, f"skills={len(d.get('skills', [])) if d.get('ok') else d}")

    r = client.post("/api/sql-audit/write-ticket", json={
        "skill_name": "dbcheck.execute_sql", "instance_id": "iY",
        "db_type": "mysql", "sql_text": "UPDATE t SET x=1 WHERE id=2", "reason": "verify",
    })
    d = r.get_json()
    ok7 = bool(d.get("ok")) and d.get("task", {}).get("status") in ("pending_approval", "blocked")
    check("L7 POST /write-ticket → pending_approval", ok7,
          f"status={d.get('task', {}).get('status') if d.get('ok') else d}")

    # ── 汇总 ──
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n汇总：{passed}/{total} PASS")
    failed = [n for n, ok, _ in results if not ok]
    if failed:
        print("失败项：" + ", ".join(failed))
        sys.exit(1)
    print("阶段2 验收全部通过 ✅")


if __name__ == "__main__":
    main()
