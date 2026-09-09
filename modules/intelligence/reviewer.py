# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""诊断把关人 Reviewer（阶段 B：把关与工具）。

在协同诊断产出结论后做一次「质量 + 安全」把关：
* 复核发现的一致性、证据强度、置信度；
* 扫描处置方案（ctx.plan）中的写类 / 破坏性动作，标记为需经 WriteGate 审批；
* 产出结构化 ReviewResult 写入 ctx.review，供前端审计链（阶段 D）展示。

Reviewer 只做「把关与标注」，自身不执行任何写操作；写类动作的落地由
``skills.WriteGate``（SQL 审计 pending_approval）负责。本模块刻意不 import skills
（避免循环依赖），仅在 ``auto_propose=True`` 且检测到可执行 SQL 时才惰性导入 WriteGate。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .context import SharedContext


@dataclass
class ReviewResult:
    """一次把关的结论（可序列化进 ctx.review）。"""

    approved: bool                                  # 诊断结论本身是否可信（写动作另需审批）
    confidence: str                                 # high / medium / low
    summary: str
    issues: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    gate_decisions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "confidence": self.confidence,
            "summary": self.summary,
            "issues": self.issues,
            "risk_flags": self.risk_flags,
            "gate_decisions": self.gate_decisions,
        }


# 处置方案文本 → 写类 Skill 映射（保守关键词匹配，宁可漏标也不误标）。
_WRITE_INTENT = [
    ("dbcheck.kill_session", (
        "终止会话", "杀掉会话", "kill session", "kill", "断开连接",
        "断开会话", "killsession",
    )),
    ("dbcheck.apply_index", (
        "建索引", "创建索引", "create index", "加索引",
        "重建索引", "rebuild index", "rebuild",
    )),
    ("dbcheck.execute_sql", (
        "执行sql", "执行 sql", "execute", "运行sql", "drop ",
        "truncate", "alter table", "delete from", "update ",
        "grant", "revoke", "重建表",
    )),
]


def _classify_write_intent(text: str) -> Optional[str]:
    t = (text or "").lower()
    for skill, kws in _WRITE_INTENT:
        if any(k.lower() in t for k in kws):
            return skill
    return None


def _extract_fenced_sql(text: str) -> Optional[str]:
    """从文本中抽取 ```sql ... ``` 代码块；无则返回 None（避免把自然语言当 SQL 提交）。"""
    import re
    m = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        sql = m.group(1).strip()
        return sql or None
    return None


class Reviewer:
    """把关人：复核诊断发现与处置方案，标注需审批的写类动作。"""

    def review(
        self,
        ctx: SharedContext,
        principal=None,
        auto_propose: bool = False,
    ) -> ReviewResult:
        """把关一次协同诊断。

        ``auto_propose`` 为 True 时，若处置方案文本中能抽取出明确 SQL（execute_sql
        场景），会惰性调用 WriteGate 把该动作落地为 SQL 审计 pending_approval 任务；
        其余写类动作（kill/apply_index）因自由文本缺乏结构化参数，仅标注不提案。
        """
        findings = ctx.findings
        plan = ctx.plan or []

        # ── 置信度 ──
        sev = [f.severity for f in findings]
        has_rootcause = any(f.category == "rootcause" for f in findings)
        has_critical = any(s == "critical" for s in sev)
        has_warning = any(s == "warning" for s in sev)
        if has_rootcause and (has_critical or has_warning):
            confidence = "high"
        elif findings:
            confidence = "medium"
        else:
            confidence = "low"

        issues: List[str] = []
        risk_flags: List[str] = []
        gate_decisions: List[Dict[str, Any]] = []

        # ── 风险标记：高危/严重发现的标签 ──
        for f in findings:
            if f.severity in ("critical", "warning"):
                for tag in (f.tags or []):
                    if tag not in risk_flags:
                        risk_flags.append(tag)

        # ── 证据充分性 ──
        rootcauses = [f for f in findings if f.category == "rootcause"]
        for rc in rootcauses:
            if not rc.detail:
                issues.append(f"根因「{rc.title}」缺少关联证据，结论置信度受限")
        if not findings:
            issues.append("未发现任何现象/风险，诊断结论为空")

        # ── 处置方案把关：写类动作 → WriteGate 审批 ──
        meta = ctx.inputs.get("target_meta") or {}
        instance_id = meta.get("instance_id")
        db_type = meta.get("db_type")
        for step in plan:
            text = " ".join([
                str(step.get("focus", "") or ""),
                " ".join(step.get("actions") or []),
                str(step.get("evidence", "") or ""),
            ])
            skill = _classify_write_intent(text)
            if not skill:
                continue
            decision: Dict[str, Any] = {
                "skill": skill,
                "status": "requires_approval",
                "source_step": step.get("step"),
                "text": text[:200],
            }
            # 仅当能抽取出明确 SQL（execute_sql）且带实例信息时，才落地为 pending_approval 任务
            if auto_propose and skill == "dbcheck.execute_sql" and instance_id and db_type:
                sql = _extract_fenced_sql(text)
                if sql:
                    try:
                        from .skills import WriteGate
                        submitter = principal.label() if hasattr(principal, "label") else "reviewer"
                        task = WriteGate.propose(
                            skill,
                            {"sql_text": sql, "instance_id": instance_id, "env": "prod"},
                            submitter, instance_id, db_type,
                            remark=f"Reviewer@{skill}",
                        )
                        decision["task_id"] = task["id"]
                        decision["task_no"] = task.get("task_no")
                        decision["status"] = task.get("status")
                    except Exception as e:  # 提案失败不阻断把关
                        decision["note"] = f"自动提案失败（需人工提交 SQL 审计）: {e}"
            gate_decisions.append(decision)

        if gate_decisions:
            issues.append(
                f"检测到 {len(gate_decisions)} 项写类/破坏性处置建议，必须经 SQL 审计"
                f"审批（pending_approval）后方可执行"
            )

        # 写类动作存在 → 整体结论为「诊断可信，但执行需审批」；无发现则不可信
        approved = bool(findings) and not (confidence == "low" and not rootcauses)

        summary = (
            f"协同诊断把关完成：置信度 {confidence}，发现 {len(findings)} 条，"
            f"处置方案 {len(plan)} 步，其中 {len(gate_decisions)} 步需审批。"
        )

        return ReviewResult(
            approved=approved,
            confidence=confidence,
            summary=summary,
            issues=issues,
            risk_flags=risk_flags,
            gate_decisions=gate_decisions,
        )
