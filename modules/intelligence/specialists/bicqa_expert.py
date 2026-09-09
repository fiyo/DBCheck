# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""BIC-QA 知识检索专家（阶段 2：诊断闭环的知识补充步）。

定位：本地专家（监控/巡检/根因/SQL 治理等）产出真实发现后，若命中本能力
triggers 标签，由迭代重规划自动追加为「知识检索步」——把诊断目标与关键发现
汇总为问题，检索 BIC-QA 云端知识库，补充四段式（现象解读/根因排序/定位命令/
处置建议）知识建议。

红线：
- 零 BIC-QA 代码嵌入，仅经 mcp_server.bicqa_bridge 以 HTTP 协议调用外部服务；
- 未配置 API Key / 用户关闭 / 外部不可达时优雅降级（info 发现），绝不阻断诊断；
- 外发前按配置做敏感信息脱敏（默认开启）。
"""

from __future__ import annotations

import json
import os
from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist

# 触发标签：覆盖本地专家可能产出的主要风险类别（rootcause 桶 + 专项标签 +
# 国产库标签）。命中任一即由 replan 在该轮之后追加本能力。
_TRIGGERS = [
    "sql", "lock", "io", "connection", "compute",
    "index", "capacity", "storage", "baseline", "config",
    "native_db", "slow_query",
    "dm8", "hgdb", "kingbase", "oceanbase", "tidb", "yashandb", "uxdb", "gbase",
]

# 参与问题构建的发现严重级别（info 噪音不外发）
_Q_SEVERITIES = {"warning", "critical", "error"}
# 单次外发问题中引用的发现上限
_MAX_FINDINGS = 6
# 单条发现文本截断长度（控制外发体积）
_SNIPPET = 160


def _user_config() -> dict:
    """读取 dbc_config.json 的 bicqa 字段（与 views.py / bicqa_bridge 同一存储）。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        # specialists -> intelligence -> modules -> DBCheck 根目录
        root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        cfg_path = os.path.join(root, "dbc_config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f).get("bicqa", {}) or {}
    except Exception:
        pass
    return {}


class BicqaKnowledgeExpert(Specialist):
    id = "bicqa_expert"
    name = "BIC-QA 知识检索"
    description = ("本地专家产出发现后检索 BIC-QA 云端知识库，补充四段式"
                   "（现象/根因/定位/处置）知识建议；未配置 API Key 时自动跳过。")
    tags = ["bicqa", "knowledge"]
    domain = "knowledge"
    deps = ["rootcause_expert"]
    triggers = list(_TRIGGERS)

    @staticmethod
    def _db_type_of(ctx: SharedContext) -> str:
        meta = ctx.inputs.get("target_meta") or {}
        inst = ctx.inputs.get("target_instance") or {}
        return (meta.get("db_type") or inst.get("db_type") or "").lower()

    def _build_question(self, ctx: SharedContext) -> str:
        parts = [f"诊断目标：{ctx.goal}"]
        rel = [f for f in ctx.findings
               if f.source != self.id and (f.severity or "").lower() in _Q_SEVERITIES]
        rel.sort(key=lambda f: 0 if (f.severity or "").lower() == "critical" else 1)
        if rel:
            parts.append("本地专家主要发现：")
            for f in rel[:_MAX_FINDINGS]:
                parts.append(f"- [{(f.severity or '').lower()}] {f.title}："
                             f"{(f.detail or '')[:_SNIPPET]}")
        parts.append("请结合数据库运维知识库，按 现象解读 / 根因排序 / 定位命令 / 处置建议 四段给出分析。")
        return "\n".join(parts)

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        ucfg = _user_config()
        # 用户显式关闭 → 完全离线，不外发任何数据
        if ucfg.get("enabled") is False:
            return [self._skip_finding("BIC-QA 能力已在「BIC-QA 知识库」配置页关闭，"
                                       "本轮跳过知识检索（不向外部发送任何数据）。")]

        # 优雅降级：未配置 Key 时不发起任何外部调用
        try:
            from ...mcp_server.bicqa_bridge import BICQAUnavailable, get_bridge
        except Exception as e:  # 桥接模块缺失等极端情况
            return [self._skip_finding(f"BIC-QA 桥接不可用（{e}），本轮跳过知识检索。")]

        try:
            bridge = get_bridge()
        except BICQAUnavailable as e:
            return [self._skip_finding(f"BIC-QA 未启用或未配置 API Key（{e}），"
                                       "本轮跳过知识检索；可在「BIC-QA 知识库」页配置。")]
        except Exception as e:
            return [self._skip_finding(f"BIC-QA 配置读取异常（{e}），本轮跳过知识检索。")]

        # 脱敏开关：inputs 显式指定 > 配置页开关（默认开）
        mask = ctx.inputs.get("bicqa_mask")
        if mask is None:
            mask = bool(ucfg.get("enable_masking", True))

        question = self._build_question(ctx)
        try:
            answer = bridge.ask(question, dbtype=self._db_type_of(ctx), mask=bool(mask))
        except BICQAUnavailable as e:
            return [self._skip_finding(f"BIC-QA 服务不可达（{e}），本轮跳过知识检索。")]
        except Exception as e:
            raise RuntimeError(f"BIC-QA 知识检索失败：{e}")  # 交由 hub 记入 notes

        if not answer:
            return [self._skip_finding("BIC-QA 返回为空，本轮无知识补充。")]

        return [Finding(
            source=self.id,
            category="knowledge",
            severity="info",
            title="BIC-QA 知识库补充建议（四段式）",
            detail=str(answer),
            suggestion="结合本地专家结论交叉验证后执行；涉及变更操作请走 SQL 审核 / 工单审批。",
            tags=["bicqa", "knowledge"],
        )]

    @staticmethod
    def _skip_finding(reason: str) -> Finding:
        return Finding(
            source="bicqa_expert",
            category="knowledge",
            severity="info",
            title="BIC-QA 知识检索跳过",
            detail=reason,
            suggestion="配置 API Key 并启用后，本能力将在发现命中时自动介入。",
            tags=["bicqa"],
        )
