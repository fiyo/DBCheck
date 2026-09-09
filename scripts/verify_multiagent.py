# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
"""阶段 A（多 Agent 迭代重规划）验收脚本。

验证范围（对标规划文档 4.1/4.3/4.4 A）：
  - 纯逻辑：replan 按真实 findings 标签追加专项能力；无命中返回 None；
            不会重复追加已在序列中的能力；清理后的规则规划不再无脑追加。
  - 仿真闭环：用假专家注入到 DiagnosticHub，跑真实的 _run_iterative + replan
             + _run_specialist，验证「监控发现锁 → 重规划追加 lock_analyst →
             收敛」的多轮闭环，以及 max_iter 封顶。
  - 三步验证：compileall → import modules.web.app → discover_plugins()==11

运行：
  python scripts/verify_multiagent.py            # 全部
  python scripts/verify_multiagent.py live       # 仅仿真闭环（子进程）
  python scripts/verify_multiagent.py steps      # 仅三步验证（子进程）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# 纯逻辑矩阵
# ─────────────────────────────────────────────────────────────────────────────
def pure_logic():
    import sys as _sys
    _sys.path.insert(0, ROOT)
    from modules.intelligence.planner import replan, Plan
    from modules.intelligence.registry import registry, SpecialistRegistry
    from modules.intelligence.specialist import Specialist
    from modules.intelligence.context import SharedContext, Finding
    from modules.intelligence.specialists import register_all

    register_all()
    res = []

    # 1) 无 findings 标签 → replan 返回 None（不应重规划）
    ctx = SharedContext(goal="g", target="t")
    p0 = Plan(sequence=["monitor_sentinel", "inspection_expert", "rootcause_expert"])
    r = replan(ctx, p0, registry)
    res.append(("L1 空 findings → replan 返回 None", r is None, str(r)))

    # 2) 出现 lock 标签 → 追加 lock_analyst（不追加 sql_governance）
    ctx.findings = [Finding(source="x", category="risk", severity="warning",
                            title="锁等待", tags=["lock"])]
    r = replan(ctx, p0, registry)
    ok = (r is not None and "lock_analyst" in r.sequence
          and "sql_governance" not in r.sequence)
    res.append(("L2 lock 标签 → 仅追加 lock_analyst", ok, str(r.sequence) if r else None))

    # 3) 出现 sql / slow_sql 标签 → 追加 sql_governance
    ctx.findings = [Finding(source="x", category="risk", severity="warning",
                            title="慢 SQL", tags=["sql", "slow_sql"])]
    r = replan(ctx, p0, registry)
    ok = (r is not None and "sql_governance" in r.sequence
          and "lock_analyst" not in r.sequence)
    res.append(("L3 sql 标签 → 仅追加 sql_governance", ok, str(r.sequence) if r else None))

    # 4) 同时命中 → 两者都追加
    ctx.findings = [Finding(source="x", category="risk", severity="warning",
                            title="", tags=["lock", "sql"])]
    r = replan(ctx, p0, registry)
    ok = (r is not None and "lock_analyst" in r.sequence
          and "sql_governance" in r.sequence)
    res.append(("L4 同时命中 → 两者都追加", ok, str(r.sequence) if r else None))

    # 5) 已追加的能力不重复追加（单调性）
    ctx.findings = [Finding(source="x", category="risk", severity="warning",
                            title="", tags=["lock"])]
    p1 = Plan(sequence=["monitor_sentinel", "inspection_expert", "rootcause_expert", "lock_analyst"])
    r = replan(ctx, p1, registry)
    ok = (r is None) or ("lock_analyst" not in r.sequence[3:])
    res.append(("L5 已包含 lock_analyst → 不再重复追加", ok, str(r.sequence) if r else "None(converged)"))

    # 6) revision_log 记录重规划决策
    ctx.findings = [Finding(source="x", category="risk", severity="warning",
                            title="锁等待", tags=["lock"])]
    ctx.revision_log = []
    ctx.iteration = 1
    r = replan(ctx, p0, registry)
    ok = (r is not None and len(ctx.revision_log) == 1
          and ctx.revision_log[0]["added"] == ["lock_analyst"])
    res.append(("L6 revision_log 记录追加决策", ok, str(ctx.revision_log)))

    # 7) 规则规划不再无脑追加专项能力（清理死代码后，基础链路只有三件套）
    from modules.intelligence.planner import _plan_rule_based
    ctx2 = SharedContext(goal="做一次性能诊断", target="t")
    rp = _plan_rule_based(ctx2, registry)
    ok = rp.sequence == ["monitor_sentinel", "inspection_expert", "rootcause_expert"]
    res.append(("L7 规则规划=基础三件套（不再无脑追加）", ok, str(rp.sequence)))

    # 8) registry 元数据助手可用
    ok = (len(registry.list_by_domain("lock")) == 1
          and registry.list_by_domain("lock")[0].id == "lock_analyst")
    res.append(("L8 registry.list_by_domain 可用", ok,
                str([s.id for s in registry.list_by_domain("lock")])))

    return res


# ─────────────────────────────────────────────────────────────────────────────
# 仿真闭环：注入假专家，跑真实 _run_iterative / replan / _run_specialist
# ─────────────────────────────────────────────────────────────────────────────
def run_live():
    sys.path.insert(0, ROOT)
    from modules.intelligence.hub import DiagnosticHub
    from modules.intelligence.registry import SpecialistRegistry
    from modules.intelligence.specialist import Specialist
    from modules.intelligence.context import SharedContext, Finding
    from modules.intelligence.planner import Plan

    # 假专家：确定性产出，不触碰任何数据库。
    # 注意：不替换 hub._run_specialist（避免猴子补丁副作用），而是直接检查
    # ctx.findings 中是否出现对应专家的 source 来判断「是否实际运行」。
    class FakeMonitor(Specialist):
        id = "monitor_sentinel"; name = "M"; description = "fake"; tags = ["monitor"]
        domain = "monitor"
        def analyze(self, ctx):
            return [Finding(source=self.id, category="anomaly", severity="warning",
                            title="检测到锁等待", tags=["lock"])]

    class FakeRoot(Specialist):
        id = "rootcause_expert"; name = "R"; description = "fake"; tags = ["rootcause"]
        domain = "rootcause"
        def analyze(self, ctx):
            return [Finding(source=self.id, category="rootcause", severity="warning",
                            title="疑似根因：锁等待", tags=["lock"])]

    class FakeQuietMonitor(Specialist):
        id = "monitor_sentinel"; name = "M"; description = "fake"; tags = ["monitor"]
        domain = "monitor"
        def analyze(self, ctx):
            return [Finding(source=self.id, category="anomaly", severity="info",
                            title="一切正常", tags=["healthy"])]

    class FakeQuietRoot(Specialist):
        id = "rootcause_expert"; name = "R"; description = "fake"; tags = ["rootcause"]
        domain = "rootcause"
        def analyze(self, ctx):
            return [Finding(source=self.id, category="rootcause", severity="info",
                            title="无明显根因", tags=["healthy"])]

    class FakeSqlMonitor(Specialist):
        id = "monitor_sentinel"; name = "M"; description = "fake"; tags = ["monitor"]
        domain = "monitor"
        def analyze(self, ctx):
            return [Finding(source=self.id, category="anomaly", severity="warning",
                            title="慢 SQL", tags=["sql", "slow_sql"])]

    class FakeLock(Specialist):
        id = "lock_analyst"; name = "L"; description = "fake"; tags = ["lock"]
        domain = "lock"; triggers = ["lock", "block"]
        def analyze(self, ctx):
            return [Finding(source=self.id, category="plan", severity="info",
                            title="锁分析完成", tags=["lock"])]

    class FakeSql(Specialist):
        id = "sql_governance"; name = "S"; description = "fake"; tags = ["sql"]
        domain = "sql"; triggers = ["sql", "slow_sql"]
        def analyze(self, ctx):
            return [Finding(source=self.id, category="plan", severity="info",
                            title="SQL 治理完成", tags=["sql"])]

    def build_hub(specs):
        hub = DiagnosticHub()
        reg = SpecialistRegistry()
        for s in specs:
            reg.register(s)
        hub.registry = reg
        return hub

    def ran_sources(ctx):
        return {f.source for f in ctx.findings}

    results = []

    # 场景 A：monitor+root 产出 lock → 应追加并运行 lock_analyst（2 轮收敛）
    hub = build_hub([FakeMonitor(), FakeRoot(), FakeLock(), FakeSql()])
    ctx = SharedContext(goal="诊断锁问题", target="t")
    plan = Plan(sequence=["monitor_sentinel", "rootcause_expert"])
    final = hub._run_iterative(ctx, plan, max_iter=3)
    ok = (ctx.iteration == 2 and "lock_analyst" in final.sequence
          and "lock_analyst" in ran_sources(ctx) and len(ctx.revision_log) == 1)
    results.append(("A 锁链路自适应：追加并运行 lock_analyst（迭代2收敛）", ok,
                    f"iter={ctx.iteration} seq={final.sequence} ran={sorted(ran_sources(ctx))} log={ctx.revision_log}"))

    # 场景 B：无触发标签 → 首轮即收敛（迭代1，不追加）
    hub = build_hub([FakeQuietMonitor(), FakeQuietRoot(), FakeLock(), FakeSql()])
    ctx = SharedContext(goal="g", target="t")
    plan = Plan(sequence=["monitor_sentinel", "rootcause_expert"])
    final = hub._run_iterative(ctx, plan, max_iter=3)
    ok = (ctx.iteration == 1 and "lock_analyst" not in final.sequence
          and "sql_governance" not in final.sequence)
    results.append(("B 无命中 → 首轮收敛（不追加）", ok,
                    f"iter={ctx.iteration} seq={final.sequence}"))

    # 场景 C：max_iter=1 封顶 → lock_analyst 被追加但未运行
    hub = build_hub([FakeMonitor(), FakeRoot(), FakeLock(), FakeSql()])
    ctx = SharedContext(goal="诊断锁问题", target="t")
    plan = Plan(sequence=["monitor_sentinel", "rootcause_expert"])
    final = hub._run_iterative(ctx, plan, max_iter=1)
    ok = (ctx.iteration == 1 and "lock_analyst" in final.sequence
          and "lock_analyst" not in ran_sources(ctx))
    results.append(("C max_iter=1 封顶：追加但未运行 lock_analyst", ok,
                    f"iter={ctx.iteration} seq={final.sequence} ran={sorted(ran_sources(ctx))}"))

    # 场景 D：sql 标签 + lock 标签 → 追加并运行 sql_governance 与 lock_analyst
    hub = build_hub([FakeSqlMonitor(), FakeRoot(), FakeLock(), FakeSql()])
    ctx = SharedContext(goal="g", target="t")
    plan = Plan(sequence=["monitor_sentinel", "rootcause_expert"])
    final = hub._run_iterative(ctx, plan, max_iter=3)
    ok = (ctx.iteration == 2 and "sql_governance" in final.sequence
          and "sql_governance" in ran_sources(ctx)
          and "lock_analyst" in final.sequence and "lock_analyst" in ran_sources(ctx))
    results.append(("D SQL+锁标签 → 追加并运行 sql_governance 与 lock_analyst", ok,
                    f"iter={ctx.iteration} seq={final.sequence} ran={sorted(ran_sources(ctx))}"))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# 三步验证
# ─────────────────────────────────────────────────────────────────────────────
def run_steps():
    import sys as _sys
    _sys.path.insert(0, ROOT)
    out = []
    # 1) compileall
    import compileall
    ok = compileall.compile_dir(os.path.join(ROOT, "modules"), quiet=1)
    out.append(("compileall modules/", bool(ok), "see stderr for errors" if not ok else "ok"))
    # 2) import web.app
    try:
        import modules.web.app  # noqa: F401
        out.append(("import modules.web.app", True, "ok"))
    except Exception as e:
        out.append(("import modules.web.app", False, repr(e)))
    # 3) discover_plugins() == 11
    try:
        from modules.pluginkit.loader import discover_plugins
        n = len(discover_plugins())
        out.append(("discover_plugins()==11", n == 11, f"plugins={n}"))
    except Exception as e:
        out.append(("discover_plugins()==11", False, repr(e)))
    return out


def _last_json(text: str):
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("["):
            try:
                return json.loads(line)
            except Exception:
                pass
    return None


def _report(rows):
    pass_count = sum(1 for _, ok, _ in rows if ok is True)
    fail_count = sum(1 for _, ok, _ in rows if ok is False)
    none_count = sum(1 for _, ok, _ in rows if ok is None)
    print("=" * 72)
    for name, ok, detail in rows:
        mark = "PASS" if ok is True else ("FAIL" if ok is False else "N/A ")
        print(f"[{mark}] {name}")
        if detail not in (None, "", True):
            print(f"        -> {detail}")
    print("-" * 72)
    print(f"汇总：{pass_count} 通过 / {fail_count} 失败 / {none_count} 不适用")
    print("=" * 72)
    return 1 if fail_count else 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "live":
        print(json.dumps(run_live(), ensure_ascii=False))
    elif mode == "steps":
        print(json.dumps(run_steps(), ensure_ascii=False))
    else:
        res = pure_logic()
        try:
            out = subprocess.check_output([sys.executable, __file__, "live"],
                                          cwd=ROOT, text=True)
            res += _last_json(out) or []
        except Exception as e:
            res.append(("LIVE-SUBPROCESS", False, str(e)))
        try:
            out2 = subprocess.check_output([sys.executable, __file__, "steps"],
                                           cwd=ROOT, text=True)
            res += _last_json(out2) or []
        except Exception as e:
            res.append(("STEPS-SUBPROCESS", False, str(e)))
        sys.exit(_report(res))
