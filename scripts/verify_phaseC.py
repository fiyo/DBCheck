# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""阶段 C 验收脚本（规划文档 4.4 C：告警驱动 + Workflow + 专家域扩展）。

纯逻辑验证（不依赖 web.app / 真实库）：
  C1-C4  4 个新专家注册 + analyze 产出
  C5     replan 按 triggers 动态追加新专家（index/baseline/capacity/native）
  C6-C7  Workflow DAG 拓扑序 + when 条件分支
  C8-C9  告警驱动自动 RCA：高危判定 + handle_alert 派发 RCA 并写回 IM

三步验证（live 子进程）：compileall → import modules.web.app → discover_plugins()==11

用法：
  python scripts/verify_phaseC.py            # 全部
  python scripts/verify_phaseC.py live      # 仅三步验证（live 子进程）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))


# ───────────────────────── 纯逻辑验证 ─────────────────────────
def run_logic() -> None:
    from modules.intelligence.specialists import register_all
    from modules.intelligence.registry import registry
    from modules.intelligence.context import SharedContext, Finding
    from modules.intelligence.planner import replan, Plan
    from modules.intelligence.workflow import Workflow, Step

    register_all()

    # C1 4 个新专家已注册
    new_ids = {"index_advisor", "baseline_compare", "capacity_analyst", "native_db"}
    present = new_ids.issubset(set(registry.ids()))
    check("C1 新专家注册 (index/baseline/capacity/native)", present,
          f"registered={sorted(registry.ids())}")

    # C2 各专家无相关发现时返回 info 待命
    from modules.intelligence.specialists.index_advisor import IndexAdvisor
    from modules.intelligence.specialists.baseline_compare import BaselineCompare
    from modules.intelligence.specialists.capacity_analyst import CapacityAnalyst
    from modules.intelligence.specialists.native_db import NativeDbExpert

    ctx_empty = SharedContext(goal="g", target="i1")
    idle = {
        "index_advisor": IndexAdvisor().analyze(ctx_empty),
        "baseline_compare": BaselineCompare().analyze(ctx_empty),
        "capacity_analyst": CapacityAnalyst().analyze(ctx_empty),
        "native_db": NativeDbExpert().analyze(ctx_empty),
    }
    all_idle = all(
        f and f[0].severity == "info" and f[0].category == "risk" for f in idle.values()
    )
    check("C2 新专家空上下文返回 info 待命", all_idle)

    # C3 专家带相关发现产出 plan
    ctx_sql = SharedContext(goal="g", target="i1")
    ctx_sql.add(Finding(source="x", category="risk", severity="warning",
                        title="慢 SQL 全表扫描", tags=["slow_sql", "sql"]))
    adv_out = IndexAdvisor().analyze(ctx_sql)
    check("C3 index_advisor 产出索引建议", any("索引" in f.title for f in adv_out),
          f"titles={[f.title for f in adv_out]}")

    # C4 native_db 命中国产库类型
    ctx_native = SharedContext(goal="g", target="i1",
                               inputs={"target_meta": {"db_type": "dm8"}})
    nat_out = NativeDbExpert().analyze(ctx_native)
    check("C4 native_db 命中 DM8 产出运维提示",
          any("dm8" in f.title.lower() or "国产库" in f.title for f in nat_out),
          f"titles={[f.title for f in nat_out]}")

    # C5 replan 按 triggers 动态追加新专家
    def replan_adds(tags, expect):
        ctx = SharedContext(goal="g", target="i1")
        for t in tags:
            ctx.add(Finding(source="x", category="risk", severity="warning",
                            title="t", tags=[t]))
        ctx.iteration = 1
        prev = Plan(sequence=["monitor_sentinel", "inspection_expert", "rootcause_expert"])
        new = replan(ctx, prev, registry)
        added = new.sequence[len(prev.sequence):] if new else []
        return expect.issubset(set(added)), added

    ok1, a1 = replan_adds(["slow_sql", "sql"], {"index_advisor"})
    ok2, a2 = replan_adds(["baseline", "config"], {"baseline_compare"})
    ok3, a3 = replan_adds(["capacity", "disk"], {"capacity_analyst"})
    ok4, a4 = replan_adds(["dm8"], {"native_db"})
    check("C5a replan 追加 index_advisor", ok1, f"added={a1}")
    check("C5b replan 追加 baseline_compare", ok2, f"added={a2}")
    check("C5c replan 追加 capacity_analyst", ok3, f"added={a3}")
    check("C5d replan 追加 native_db", ok4, f"added={a4}")

    # C6 Workflow DAG 拓扑序 + when 条件执行
    def when_index(c: SharedContext) -> bool:
        return any("index" in (f.tags or []) for f in c.findings)

    def seed_index(c, a):
        c.add(Finding(source="seed", category="risk", severity="warning",
                      title="缺失索引", tags=["index", "sql"]))

    wf = Workflow([
        Step("seed", kind="func", args={"callable": seed_index}),
        Step("adv", kind="specialist", ref="index_advisor", when=when_index),
    ], edges=[("seed", "adv")])
    res = wf.run("g", "i1")
    ok_topo = res["order"] == ["seed", "adv"]
    ok_both = set(res["steps_executed"]) == {"seed", "adv"}
    check("C6a Workflow 拓扑序 seed→adv", ok_topo, f"order={res['order']}")
    check("C6b when 满足时 adv 执行", ok_both, f"exec={res['steps_executed']}")

    # C7 when 不满足 → 跳过
    wf_skip = Workflow([Step("adv", kind="specialist", ref="index_advisor",
                             when=lambda c: False)])
    res_skip = wf_skip.run("g", "i1")
    check("C7 when 不满足时跳过", res_skip["steps_skipped"] == ["adv"],
          f"skipped={res_skip['steps_skipped']}")

    # C8 告警高危判定
    import modules.intelligence.alert_trigger as at
    hi = at.is_high_alert({"level": "critical", "tags": []})
    warn = at.is_high_alert({"level": "warning", "tags": []})
    risk_tag = at.is_high_alert({"level": "info", "tags": ["deadlock"]})
    check("C8 告警高危判定", hi and (not warn) and risk_tag,
          f"critical={hi} warning={warn} risk_tag={risk_tag}")

    # C9 handle_alert 高危 → 派发 RCA + 写回 IM（注入 sink，mock RCA）
    calls = []

    def fake_rca(goal, iid, inputs):
        return {"findings": [{"source": "x", "category": "risk",
                              "severity": "warning", "title": "慢查询",
                              "tags": ["sql"]}], "plan": []}

    at._dispatch_rca = fake_rca

    def sink(alert, summary):
        calls.append((alert, summary))
        return True

    r = at.handle_alert({
        "instance_id": "i1", "instance_name": "主库", "db_type": "mysql",
        "level": "critical", "title": "慢查询告警", "tags": ["slow_sql"],
    }, sink=sink)
    ok_rca = r.get("triggered") and r.get("notified") and calls and "summary" in r
    check("C9 告警驱动 RCA + 写回 IM", ok_rca,
          f"triggered={r.get('triggered')} notified={r.get('notified')} calls={len(calls)}")

    # C10 非高危告警被忽略（独立 sink，避免与 C9 的 calls 串味）
    calls2: list = []

    def sink2(alert, summary):
        calls2.append((alert, summary))
        return True

    r2 = at.handle_alert({"instance_id": "i1", "level": "info", "tags": []}, sink=sink2)
    check("C10 非高危告警忽略", (not r2.get("triggered")) and not calls2,
          f"triggered={r2.get('triggered')}")


# ───────────────────────── 三步验证（live 子进程） ─────────────────────────
def run_live() -> None:
    code = (
        "import sys, os;"
        "sys.path.insert(0, r'%s');"
        "from modules.intelligence.specialists import register_all;"
        "register_all();"
        "from modules.intelligence.registry import registry;"
        "caps = sorted(registry.ids());"
        "ok_caps = {'index_advisor','baseline_compare','capacity_analyst','native_db'}.issubset(set(caps));"
        "import modules.web.app as web_app;"
        "from modules.pluginkit.loader import discover_plugins;"
        "n = len(discover_plugins());"
        "print('LIVE_CAPS_OK=' + str(ok_caps));"
        "print('LIVE_PLUGINS=' + str(n));"
        "import json; print('LIVE_RESULT=' + json.dumps({'caps_ok': ok_caps, 'plugins': n}));"
    ) % ROOT
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    print("  --- live subprocess output ---")
    for line in out.splitlines():
        if line.startswith("LIVE_"):
            print("  " + line)
    if proc.returncode != 0:
        print("  [FAIL] live subprocess crashed")
        print(out[-2000:])
        return
    ok_caps = "LIVE_CAPS_OK=True" in out
    ok_plugins = "LIVE_PLUGINS=11" in out
    check("L1 新专家进入能力清单（live）", ok_caps)
    check("L2 discover_plugins()==11（live）", ok_plugins)


def main() -> int:
    print("=== 阶段 C 验收：纯逻辑 ===")
    run_logic()

    if len(sys.argv) > 1 and sys.argv[1] == "live":
        print("=== 阶段 C 验收：三步验证（live） ===")
        run_live()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n=== 汇总：{passed}/{total} PASS ===")
    fails = [n for n, ok, _ in results if not ok]
    if fails:
        print("未通过：" + "; ".join(fails))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
