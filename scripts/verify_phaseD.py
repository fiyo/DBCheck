# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck
"""阶段 D 验收：Workflow Builder UI + Reviewer 审计链前端（可视化）。

纯逻辑（主进程，隔离临时库）：
  R1 workflow_store 结构校验（空名 / 空 steps / 悬空边 均拒绝）
  R2 workflow_store CRUD（保存 / 读取 / 列表 / 删除）
  R3 Workflow 引擎执行（func 节点不连库，返回编排产物）
  R4 ReviewResult 结构完整（reviewer.py，前端审计链数据源）
  R5 写类技能规格（registry，Builder 节点清单来源）

live 子进程（import web.app + discover_plugins==11 + Flask 测试客户端）：
  L1 列表端点返回空
  L2 保存端点返回 id
  L3 列表含刚保存项
  L4 workflow-nodes 端点（specialists + skills，含写类技能）
  L5 删除端点
  L6 discover_plugins() == 11（三步验证之一）
  L7 hub._finalize 含 review 字段（Reviewer 结论进诊断结果）
"""
import os
import sys
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"  | {detail}" if detail else ""))


# ───────────────────────── 纯逻辑 ─────────────────────────
def run_logic():
    import modules.intelligence.workflow_store as wfs
    tmp = tempfile.mkdtemp(prefix="wf_verify_")
    wfs._DB_PATH = os.path.join(tmp, "workflows.db")

    # R1 结构校验
    for bad in [("", [{"id": "a"}], []),
                ("x", [], []),
                ("x", [{"id": "a"}], [["a", "b"]])]:
        try:
            wfs.save_workflow(*bad)
            check("R1 校验拒绝(" + repr(bad[0]) + ")", False)
        except ValueError:
            check("R1 校验拒绝(" + repr(bad[0]) + ")", True)

    # R2 CRUD
    wf = wfs.save_workflow("t1", [{"id": "a", "kind": "specialist", "ref": "monitor_sentinel"}], [])
    check("R2 保存返回 id", wf.get("id") is not None, str(wf.get("id")))
    got = wfs.get_workflow(wf["id"])
    check("R2 读取一致", bool(got) and got["name"] == "t1" and len(got["steps"]) == 1)
    check("R2 列表含 1", len(wfs.list_workflows()) == 1)
    check("R2 删除", wfs.delete_workflow(wf["id"]) and len(wfs.list_workflows()) == 0)

    # R3 Workflow 引擎（func 节点无 callable，不连库不报错）
    from modules.intelligence.workflow import Workflow, Step
    res = Workflow([Step(id="a", kind="func")], []).run("g", "i1", {})
    check("R3 引擎执行 func 节点", "a" in res["steps_executed"], str(res["steps_executed"]))
    check("R3 引擎编排产物完整",
          isinstance(res.get("order"), list) and isinstance(res.get("log"), list))

    # R4 ReviewResult 结构
    from modules.intelligence.reviewer import ReviewResult
    rv = ReviewResult(approved=True, confidence="high", summary="s",
                      issues=["i1"], risk_flags=["lock"],
                      gate_decisions=[{"skill": "dbcheck.kill_session", "status": "requires_approval"}])
    d = rv.to_dict()
    need = ("approved", "confidence", "summary", "issues", "risk_flags", "gate_decisions")
    check("R4 ReviewResult 结构完整", all(k in d for k in need), str(d))

    # R5 写类技能规格
    from modules.mcp_server.registry import get_skill_specs
    skills = [s for s in get_skill_specs()
              if (s.get("risk") or {}).get("requires_approval")]
    check("R5 写类技能规格 >= 3", len(skills) >= 3, str(len(skills)))


# ───────────────────────── live 子进程 ─────────────────────────
def run_live():
    import tempfile
    import modules.intelligence.workflow_store as wfs
    wfs._DB_PATH = os.path.join(tempfile.mkdtemp(prefix="wf_live_"), "workflows.db")
    print("[live] importing web.app ...", flush=True)
    import modules.web.app as appmod
    from modules.pluginkit.loader import discover_plugins
    print("[live] web.app imported", flush=True)
    app = appmod.app
    client = app.test_client()

    # L1 列表空
    print("[live] L1 GET workflows", flush=True)
    r = client.get("/api/intelligence/workflows")
    j = r.get_json()
    check("L1 列表端点返回空", j["ok"] and j["workflows"] == [])

    # L2 保存
    r = client.post("/api/intelligence/workflows", json={
        "name": "verify-wf",
        "steps": [{"id": "a", "kind": "specialist", "ref": "monitor_sentinel"},
                  {"id": "b", "kind": "hub"}],
        "edges": [["a", "b"]],
    })
    j = r.get_json()
    check("L2 保存端点返回 id", j["ok"] and j["workflow"].get("id"), str(j.get("workflow", {}).get("id")))
    wid = j["workflow"]["id"]

    # L3 列表含 1
    r = client.get("/api/intelligence/workflows")
    check("L3 列表含刚保存项", len(r.get_json()["workflows"]) == 1)

    # L4 workflow-nodes
    r = client.get("/api/intelligence/workflow-nodes")
    j = r.get_json()
    has_write = any(s.get("requires_approval") for s in j.get("skills", []))
    check("L4 nodes 端点 ok", j["ok"] and "specialists" in j and "skills" in j)
    check("L4 skills 含写类技能", has_write)

    # L5 删除
    r = client.delete("/api/intelligence/workflows/" + str(wid))
    check("L5 删除端点", r.get_json()["ok"])

    # L6 三步验证之一
    check("L6 discover_plugins()==11", len(discover_plugins()) == 11,
          str(len(discover_plugins())))

    # L7 hub._finalize 含 review 字段
    from types import SimpleNamespace
    from modules.intelligence.hub import get_hub
    ctx = SimpleNamespace(goal="g", target="i", inputs={"target_meta": {}},
                          plan=[], iteration=1, revision_log=[], findings=[],
                          notes=[], started_at="", finished_at="",
                          review={"approved": True, "confidence": "high",
                                  "summary": "s", "issues": [], "risk_flags": [],
                                  "gate_decisions": []})
    plan = SimpleNamespace(sequence=[], reason="r", ai_driven=False, order=[])
    res = get_hub()._finalize(ctx, plan)
    check("L7 hub._finalize 含 review", "review" in res and res["review"]["approved"] is True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "live":
        # 直接作为 live 子进程运行（由主进程 spawn，避免递归 spawn）
        run_live()
    else:
        print("=== 阶段 D 验收：纯逻辑 ===")
        run_logic()
        if os.environ.get("PHASED_SKIP_LIVE"):
            print("(已跳过 live 子进程：PHASED_SKIP_LIVE=1)")
            sys.exit(0)
        print("=== 阶段 D 验收：live 子进程（import web.app + 端点） ===")
        import subprocess
        env = dict(os.environ)
        code = subprocess.run([sys.executable, __file__, "live"],
                             cwd=ROOT, env=env,
                             capture_output=True, text=True)
        out = code.stdout
        # live 子进程内的 check 已打印；这里只把其输出透出并判定失败
        print(out)
        if code.returncode != 0:
            print("LIVE STDERR:\n" + code.stderr)
        live_fail = any(("❌" in ln) for ln in out.splitlines())
        fails = [r for r in RESULTS if not r[1]]
        print(f"\n汇总：主进程 {len(RESULTS)} 项，live 子进程见上；"
              f"失败合计 {len(fails) + (1 if live_fail else 0)} 项")
        sys.exit(1 if (fails or live_fail or code.returncode != 0) else 0)
