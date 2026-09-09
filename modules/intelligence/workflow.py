# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""轻量 DAG 条件编排（规划文档 4.4 C：Workflow Builder 引擎）。

一个 Workflow 由若干 Step 与有向边 edges 组成；引擎按拓扑序执行，每个 Step 可带
``when`` 条件（基于共享上下文）决定是否执行。Step 复用既有协同诊断能力：

* ``specialist`` —— 调 ``registry.get(ref).analyze(ctx)`` 追加发现（与重规划同一套能力）；
* ``hub``        —— 调 ``DiagnosticHub.dispatch`` 组合一次完整重诊断；
* ``skill``      —— 调 ``dispatch_skill`` 复用阶段 B 的 Skills/WriteGate；
* ``func``       —— 任意可调用 ``callable(ctx, args)``，做数据搬运/条件注入。

本文件仅实现编排引擎；可视化（Workflow Builder UI）属阶段 D。复用不重复造轮子：
专家能力、WriteGate、Reviewer 全部来自既有模块。
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable, Dict, List, Optional

from .context import SharedContext, Finding


class Step:
    """工作流的一个节点。"""

    def __init__(
        self,
        id: str,
        kind: str = "specialist",
        ref: str = "",
        args: Optional[Dict[str, Any]] = None,
        when: Optional[Callable[[SharedContext], bool]] = None,
        label: str = "",
    ) -> None:
        self.id = id
        self.kind = kind          # specialist | hub | skill | func
        self.ref = ref
        self.args = args or {}
        self.when = when          # Optional[Callable[[SharedContext], bool]] 条件分支
        self.label = label or id


class Workflow:
    """轻量 DAG 编排引擎（Kahn 拓扑序 + when 条件分支）。"""

    def __init__(self, steps: List[Step], edges: Optional[List[tuple]] = None) -> None:
        self.steps = {s.id: s for s in steps}
        self.order = [s.id for s in steps]
        self.edges = edges or []  # List[(from_id, to_id)]
        self.outputs: List[Dict[str, Any]] = []  # 输出节点执行产物（查看/报告/邮件）
        self._cancel_event: Optional[threading.Event] = None  # 由 task_runner 注入，用于停止

    def _topo_order(self) -> List[str]:
        indeg = {sid: 0 for sid in self.steps}
        adj: Dict[str, List[str]] = {sid: [] for sid in self.steps}
        for a, b in self.edges:
            if a in self.steps and b in self.steps:
                adj[a].append(b)
                indeg[b] += 1
        q = deque(sid for sid in self.steps if indeg[sid] == 0)
        out: List[str] = []
        seen = set()
        while q:
            n = q.popleft()
            if n not in seen:
                out.append(n)
                seen.add(n)
            for m in adj[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    q.append(m)
        # 无环且全部入队 → 拓扑序；否则退化为定义顺序（容错，不阻断）
        if len(out) == len(self.steps):
            return out
        return list(self.order)

    def run(
        self,
        goal: str,
        instance_id: str,
        inputs: Optional[Dict[str, Any]] = None,
        ctx: Optional[SharedContext] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        """执行 DAG。``cancel_event`` 若被置位，则完成当前节点后中止后续节点
        （不回滚已执行步骤），并在结果中标记 ``cancelled=True``。"""
        self._cancel_event = cancel_event
        ctx = ctx or SharedContext(goal=goal, target=instance_id, inputs=dict(inputs or {}))
        order = self._topo_order()
        log: List[Dict[str, Any]] = []
        executed: List[str] = []
        skipped: List[str] = []
        cancelled = False

        for idx, sid in enumerate(order):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                for pending in order[idx:]:
                    skipped.append(pending)
                    log.append({"step": pending, "status": "skipped", "reason": "任务已停止"})
                break
            step = self.steps[sid]
            if step.when is not None and not step.when(ctx):
                skipped.append(sid)
                log.append({"step": sid, "status": "skipped", "reason": "when 条件不满足"})
                continue
            try:
                self._exec_step(step, ctx, goal, instance_id)
                executed.append(sid)
                log.append({"step": sid, "status": "done"})
            except Exception as e:  # 单步失败不影响整体编排
                log.append({"step": sid, "status": "error", "error": str(e)})

        return {
            "ctx": ctx.to_dict(),
            "steps_executed": executed,
            "steps_skipped": skipped,
            "order": order,
            "log": log,
            "outputs": self.outputs,
            "cancelled": cancelled,
        }

    def _exec_step(self, step: Step, ctx: SharedContext, goal: str, instance_id: str) -> None:
        if step.kind == "specialist":
            from .registry import registry

            spec = registry.get(step.ref)
            if spec is None:
                raise ValueError(f"unknown specialist: {step.ref}")
            for f in spec.analyze(ctx):
                ctx.add(f)
        elif step.kind == "hub":
            from .hub import get_hub

            res = get_hub().dispatch(goal, instance_id, step.args or {})
            for f in res.get("findings", []):
                if isinstance(f, dict):
                    ctx.add(Finding(
                        source=f.get("source", "hub"),
                        category=f.get("category", "risk"),
                        severity=f.get("severity", "info"),
                        title=f.get("title", ""),
                        detail=f.get("detail", ""),
                        suggestion=f.get("suggestion", ""),
                        tags=f.get("tags", []),
                    ))
            if res.get("plan"):
                ctx.plan.extend(res["plan"])
        elif step.kind == "skill":
            from .skills import dispatch_skill

            dispatch_skill(step.ref, step.args or {}, principal=None)
        elif step.kind == "func":
            fn = step.args.get("callable")
            if callable(fn):
                fn(ctx, step.args)
            elif callable(step.ref):
                step.ref(ctx, step.args)
        elif step.kind == "output":
            entry = self._exec_output(step, ctx)
            self.outputs.append(entry)
        elif step.kind in ("start", "end"):
            # 起止节点为编排标记，无需执行
            pass
        else:
            raise ValueError(f"unknown step kind: {step.kind}")

    def _exec_output(self, step: Step, ctx: SharedContext) -> Dict[str, Any]:
        """输出节点：查看结果 / 生成报告 / 邮件通知。

        仅做编排产物落地，不阻塞整体流程；任何子操作失败都降级为记录状态而非抛异常。
        """
        args = step.args or {}
        action = str(args.get("action", "view")).lower()  # view | report | email
        to_addr = str(args.get("to", "") or "").strip()
        findings = ctx.findings or []
        plan = ctx.plan or []

        summary_lines = [
            "工作流输出：%s" % (step.label or step.id),
            "目标实例：%s" % ctx.target,
            "发现数：%d" % len(findings),
            "处置方案数：%d" % len(plan),
        ]
        report_path: Optional[str] = None
        email_status: Optional[str] = None

        if action in ("report", "email"):
            try:
                report_path = self._save_report(step, ctx, findings, plan)
            except Exception as e:  # 报告生成失败不阻断编排
                report_path = None
                email_status = "report_failed: %s" % e

        if action == "email":
            try:
                from modules.notify import EmailNotifier

                notifier = EmailNotifier()
                recips = [a.strip() for a in to_addr.split(",") if a.strip()] or (notifier.recipients or [])
                if not recips:
                    email_status = "no_recipients"
                else:
                    notifier.send_report(
                        label=ctx.target,
                        db_type="",
                        report_file=report_path or "",
                        recipients=recips,
                        custom_msg="<p>%s</p>" % "<br>".join(summary_lines),
                    )
                    email_status = "sent:%s" % ",".join(recips)
            except Exception as e:  # 邮件失败不阻断编排
                email_status = "email_failed: %s" % e

        summary = "；".join("[%s] %s" % (f.severity, f.title) for f in findings[:5]) or "无发现"
        return {
            "id": step.id,
            "label": step.label or step.id,
            "action": action,
            "to": to_addr,
            "findings_count": len(findings),
            "plan_count": len(plan),
            "summary": summary,
            "report_path": report_path,
            "email_status": email_status,
        }

    def _save_report(self, step: Step, ctx: SharedContext, findings, plan) -> str:
        """把上下文发现与处置方案导出为 Markdown 报告，落到 DATA_DIR/reports。

        报告风格对齐诊断中心：每条发现标注来源专家，并单列「AI 诊断叙述」
        （多专家协同过程中写入 ctx.notes 的结论），让工作流产物与协同诊断
        报告观感一致、可读可溯源。
        """
        import datetime
        import os

        from modules.core import paths

        # 来源专家 id → 中文名（解析失败退化为 id 本身）
        def _src_name(sid: str) -> str:
            try:
                from .registry import registry as _reg

                s = _reg.get(sid)
                return s.name if s else (sid or "未知专家")
            except Exception:
                return sid or "未知专家"

        notes = getattr(ctx, "notes", []) or []
        out_dir = paths.DATA_DIR / "reports"
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fpath = out_dir / ("workflow_%s_%s.md" % (step.id, ts))
        content = [
            "# 工作流输出报告：%s" % (step.label or step.id),
            "",
            "生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "目标实例：%s" % ctx.target,
            "",
            "## 发现（%d）" % len(findings),
        ]
        for f in findings:
            content.append("- **[%s/%s] %s**" % (f.severity, f.category, f.title))
            if getattr(f, "detail", ""):
                content.append("  - %s" % f.detail)
            if getattr(f, "suggestion", ""):
                content.append("  - 建议：%s" % f.suggestion)
            src = getattr(f, "source", "") or ""
            if src:
                content.append("  - 来源专家：%s" % _src_name(src))
        if notes:
            content.append("")
            content.append("## AI 诊断叙述")
            for n in notes:
                content.append("- %s" % n)
        content.append("")
        content.append("## 处置方案（%d）" % len(plan))
        for p in plan:
            if isinstance(p, dict):
                content.append("- %s" % (p.get("title") or p.get("content") or p))
            else:
                content.append("- %s" % p)
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write("\n".join(content))
        return str(fpath)
