# -*- coding: utf-8 -*-
"""巡检分析专员：实时调用巡检引擎产出报告，再提炼风险项。"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist


class InspectionExpert(Specialist):
    id = "inspection_expert"
    name = "深度巡检分析专员"
    description = "实时调用巡检引擎对目标数据源产出报告，提炼配置、容量、性能等维度的风险项并标注等级。"
    tags = ["inspection", "risk"]

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        out: List[Finding] = []

        # ── 优先：直接调用巡检引擎，针对所选数据源产出实时报告 ──
        inst = ctx.inputs.get("target_instance")
        if inst:
            try:
                from ..inspection_runner import run_target_inspection

                res = run_target_inspection(inst.get("db_type"), inst)
            except Exception as e:
                res = {"ok": False, "error": str(e), "auto_analyze": []}

            if res.get("ok"):
                auto_analyze = res.get("auto_analyze") or []
                # 复用中枢的风险结构转换（延迟导入避免循环依赖）
                from ..hub import _auto_analyze_to_risks

                risks = _auto_analyze_to_risks(auto_analyze)
                name = res.get("instance_name") or inst.get("name") or ""
                ctx.notes.append(
                    "深度巡检分析专员：已对 {} 实时运行巡检引擎，识别 {} 项风险".format(
                        name, len(risks)
                    )
                    + (f"，报告：{res.get('report_name')}" if res.get("report_name") else "")
                )
                if risks:
                    for r in risks:
                        out.extend(self._risk_to_findings(r))
                    return out
                # 有巡检但无风险：给出健康结论
                detail = f"{name} 实时巡检未识别到高风险项。"
                if res.get("health_status"):
                    detail += f"（健康评估：{res.get('health_status')}）"
                out.append(
                    Finding(
                        source=self.id,
                        category="risk",
                        severity="info",
                        title="实时深度巡检未见明显风险",
                        detail=detail,
                        suggestion="保持健康观测；如需刷新最新风险，可重新运行协同诊断。",
                    )
                )
                return out
            else:
                ctx.notes.append(
                    "深度巡检分析专员：实时巡检引擎调用失败（{}），回退历史报告。".format(
                        res.get("error")
                    )
                )

        # ── 回退：使用最近一次历史巡检报告 ──
        report = ctx.inputs.get("inspection_report")
        if report:
            meta = ctx.inputs.get("target_meta", {})
            instance_name = meta.get("instance_name", "")
            inspect_time = report.get("inspect_time", "")
            risks = report.get("risks") if isinstance(report, dict) else report

            if isinstance(risks, list) and risks:
                for r in risks:
                    out.extend(self._risk_to_findings(r))
                return out

            detail = "最近一次巡检未识别到高风险项。"
            if instance_name:
                detail = f"{instance_name} 最近一次巡检未识别到高风险项。"
            if inspect_time:
                detail += f"（巡检时间：{inspect_time}）"
            out.append(
                Finding(
                    source=self.id,
                    category="risk",
                    severity="info",
                    title="深度巡检未见明显风险",
                    detail=detail,
                    suggestion="保持健康观测，可在数据源页面重新执行巡检以获取最新风险。",
                )
            )
            return out

        # ── 没有任何数据 ──
        out.append(
            Finding(
                source=self.id,
                category="risk",
                severity="info",
                title="建议执行一次深度巡检",
                detail="当前数据源尚未产生巡检数据，无法提炼风险项。",
                suggestion="在数据源页面运行一次深度巡检后重新诊断，即可自动汇总风险。",
            )
        )
        return out

    @staticmethod
    def _risk_to_findings(r: dict) -> List[Finding]:
        lvl = str(r.get("level", "warning")).lower()
        if lvl in ("high", "critical", "严重"):
            sev = "critical"
        elif lvl in ("mid", "medium", "中"):
            sev = "warning"
        else:
            sev = "info"
        text = (r.get("title") or "") + " " + (r.get("detail") or "")
        low = text.lower()
        tags: List[str] = []
        if "sql" in low or "慢" in text:
            tags.append("sql")
        if "锁" in text or "lock" in low:
            tags.append("lock")
        return [
            Finding(
                source="inspection_expert",
                category="risk",
                severity=sev,
                title=r.get("title", "巡检风险项"),
                detail=r.get("detail", ""),
                suggestion=r.get("fix", ""),
                tags=tags,
            )
        ]
