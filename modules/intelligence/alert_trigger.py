# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""告警驱动自动 RCA（规划文档 4.4 C：Alert-driven 自动调查）。

当监控/巡检产生高危告警时，自动 spawn 一次根因分析（RCA），并把诊断结论
写回 IM（企业微信/钉钉 Webhook）。这是 Ongrid「当告警发生时，调查也应该随之开始」
的工程落地，不复制任何 Ongrid 代码，完全自主实现。

设计要点：
* ``is_high_alert`` 判定高危（级别 critical/high 或含 risk/deadlock 类标签）；
* ``handle_alert`` 高危则自动派发 RCA，再经可注入 sink 写回 IM；
* RCA 派发抽成模块级 ``_dispatch_rca``，便于测试 monkeypatch，避免 import 期副作用；
* sink 默认走 ``modules.notify.WebhookNotifier``，失败仅告警不阻断。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("intelligence.alert_trigger")

# 判定为「高危」的级别集合（兼容中英文）
_HIGH_LEVELS = {"critical", "crit", "严重", "high", "高"}
_RISK_TAGS = {"risk", "critical", "block", "down", "deadlock", "hang", "failover"}


def is_high_alert(alert: Dict[str, Any]) -> bool:
    """判定一条告警是否达到「自动派发 RCA」的高危门槛。"""
    level = (alert.get("level") or "").lower()
    if level in _HIGH_LEVELS:
        return True
    tags = set(alert.get("tags") or [])
    if tags & _RISK_TAGS:
        return True
    return False


def _dispatch_rca(goal: str, instance_id: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """自动派发一次协同诊断（RCA）。抽成模块级函数便于测试 monkeypatch。

    注意：仅在此处懒导入 hub，避免模块 import 期触发 ``ensure_migrated`` 副作用。
    """
    from .hub import get_hub

    return get_hub().dispatch(goal, instance_id, inputs or {})


def _default_sink(alert: Dict[str, Any], summary: str) -> bool:
    """默认写回 IM：经 WebhookNotifier 推送（企业微信/钉钉/自定义）。"""
    try:
        from modules.notify import WebhookNotifier

        inst = alert.get("instance_name") or alert.get("instance_id") or "未知实例"
        db_type = alert.get("db_type") or ""
        status = "告警自动诊断完成"
        notifier = WebhookNotifier()
        return notifier.send_alert(inst, db_type, status, error=summary[:200])
    except Exception as e:  # 写回 IM 失败绝不该阻断自动诊断
        logger.warning("告警写回 IM 失败（已忽略）: %s", e)
        return False


def handle_alert(
    alert: Dict[str, Any],
    sink: Optional[Callable[[Dict[str, Any], str], bool]] = None,
) -> Dict[str, Any]:
    """处理一条告警。

    高危告警 → 自动派发 RCA → 把结论摘要写回 IM（sink 可注入）。

    alert 字段：``instance_id, instance_name, db_type, level, title, detail, tags``
    返回 dict：``{triggered, reason, rca?, notified?, summary?}``
    """
    if not is_high_alert(alert):
        return {
            "triggered": False,
            "reason": "非高危告警，已忽略（仅 critical/high 级别或 risk/deadlock 类标签触发自动 RCA）",
        }
    instance_id = alert.get("instance_id")
    if not instance_id:
        return {"triggered": False, "reason": "告警缺少 instance_id，无法定位目标数据源"}

    goal = alert.get("title") or "自动诊断告警"
    goal = f"【告警自动诊断】{goal}"
    inputs = {"alert": alert}
    try:
        rca = _dispatch_rca(goal, instance_id, inputs)
    except Exception as e:
        return {
            "triggered": True,
            "reason": f"RCA 派发失败: {e}",
            "rca": None,
            "notified": False,
        }

    findings = rca.get("findings", []) if isinstance(rca, dict) else []
    crit = [f for f in findings
            if isinstance(f, dict) and f.get("severity") in ("critical", "warning")]
    summary = (f"告警『{alert.get('title')}』自动诊断完成：共 {len(findings)} 条结论，"
               f"其中需关注 {len(crit)} 条。")

    sink_fn = sink or _default_sink
    try:
        notified = bool(sink_fn(alert, summary))
    except Exception as e:
        logger.warning("告警写回 IM 失败（已忽略）: %s", e)
        notified = False

    return {
        "triggered": True,
        "reason": "高危告警已自动派发 RCA 并写回 IM",
        "rca": rca,
        "notified": notified,
        "summary": summary,
    }
