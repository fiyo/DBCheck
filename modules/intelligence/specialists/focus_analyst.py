# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""定向分析专员（FocusAnalyst）：只回答用户所问的单一主题问题。

场景：用户在诊断目标里只问一个具体问题（如「分析一下数据库连接数」），
协调员按 定向链路（monitor_sentinel → inspection_expert → focus_analyst）
先采集监控与巡检数据，本能力随后：

1. 从监控快照与本轮发现中只摘取与该主题相关的数据（连接/锁/慢查询/容量…）；
2. BIC-QA 已配置 → 把「问题 + 主题相关数据」交由 BIC-QA 知识库分析；
3. 未配置 → 用已配置的 AI 模型从巡检/监控结果中分析；
4. 两者都不可用 → 退回原始相关数据，绝不编造。
5. 只产出一条结论发现；与主题无关的发现由中枢折叠进 side_findings，
   前端默认收起，不再把一堆无关结果甩给用户。

红线：BIC-QA 零代码嵌入，仅经 mcp_server.bicqa_bridge HTTP 协议调用；
未配置 / 用户关闭 / 不可达时优雅降级，绝不阻断诊断。
"""

from __future__ import annotations

import os
import re
import json
from typing import Any, Dict, List, Optional

from ..context import SharedContext, Finding
from ..specialist import Specialist
from ..ai_helper import build_advisor
from ..planner import detect_focus_topic


def _read_latest_snapshot(instance_id: str) -> Dict[str, Any]:
    """安全读取目标数据源最近一次监控快照（与 monitor_sentinel 同源）。"""
    try:
        from .monitor_sentinel import _read_latest

        return _read_latest(instance_id) or {}
    except Exception:
        return {}


def _bicqa_user_config() -> dict:
    """读取 dbc_config.json 的 bicqa 字段（与 bicqa_expert 同一存储）。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        cfg_path = os.path.join(root, "dbc_config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f).get("bicqa", {}) or {}
    except Exception:
        pass
    return {}


def _fmt_metric(key: str, val: Any, ts: str) -> str:
    """把一个监控指标格式化为一行数据依据。"""
    line = f"- 监控指标 {key} = {val}"
    if ts:
        line += f"（采样时间 {ts}）"
    return line


# ── 主题 → 巡检结果 context 键名正则 ─────────────────────────────────────────
# checkdb 返回的 context 形如 {键: [行dict,...]}。各库型键名前缀不同
# （内置引擎 sessions/session_limit/long_sql；HGDB/UXDB 模板 hgdb_conn_summary；
#   Redis redis_clients；MongoDB mongodb_connections；MSSQL mssql_sessions；
#   ClickHouse clickhouse_query_log_stats…），模式按公共词根覆盖全部库型。
# 按主题只抽取对应数据段，避免把"全库巡检"的所有章节一股脑喂给分析模型。
FOCUS_CONTEXT_PATTERNS = {
    # redis_clients（Redis 连接客户端） / mssql_sessions / hgdb_conn_summary / sessions
    "connection": re.compile(r"(session|conn|connect|thread|abort|client)"),
    # 含 wait：oracle top_waits/wait_class、mssql_wait_stats 等等待事件归入锁/等待主题
    "lock":       re.compile(r"(lock|block|trx|transaction|deadlock|wait)"),
    "slow_query": re.compile(r"(slow|long_sql|top_sql|sql|query_log|pkg_cache|mongodb_profile)"),
    "compute":    re.compile(r"(cpu|load|process)"),
    "io":         re.compile(r"(disk|io|await|latency)"),
    # dbmcfg：DB2 实例配置（内存参数所在章节）
    "memory":     re.compile(r"(memory|mem|sga|pga|swap|buffer|dbmcfg)"),
    # size：hgdb/uxdb_database_size、*_tables_size；db_stats：mongodb_db_stats；
    # temp_ts：临时表空间
    "capacity":   re.compile(r"(tablespace|datafile|space|storage|disk|size|db_stats|temp_ts)"),
    # replic：replication/replicas/replicate 全覆盖；archiv/wal：PG 系归档与 WAL；
    # always_on：MSSQL AG
    "replication":re.compile(r"(replic|rss|slave|standby|lag|sync|archiv|wal|always_on)"),
    # bgwriter：PG 系后台写进程（缓冲区活动）
    "cache":      re.compile(r"(buffer|cache|hit|bgwriter)"),
}

# 主题 → 额外匹配词（含英文参数 / 指标名，弥补中文关键词匹配不到
# MAX_CONNECTIONS / Threads_connected / Aborted_connects 等英文项的问题）。
FOCUS_MATCH_TERMS = {
    "connection": ["max_connections", "threads_connected", "threads_running",
                   "aborted", "connection", "max_used_connections",
                   "current connections", "连接数", "最大连接", "活跃连接", "失败连接"],
    "lock":       ["lock", "阻塞", "死锁", "deadlock", "blocking", "等待"],
    "slow_query": ["slow", "慢查询", "慢 sql", "慢sql"],
    "compute":    ["cpu", "负载", "处理器"],
    "io":         ["io", "磁盘", "吞吐", "await"],
    "memory":     ["memory", "内存", "swap", "sga", "pga"],
    "capacity":   ["容量", "表空间", "空间", "tablespace", "storage"],
    "replication":["复制", "主从", "replication", "延迟", "回放"],
    "cache":      ["命中率", "缓存", "cache", "buffer"],
}

# 单条数据依据截断长度（context 行可能很长）
_ROW_SNIPPET = 160


def _extract_context_section(context: Dict[str, Any], pattern, limit: int = 14) -> List[str]:
    """从巡检 checkdb 结果里只抽取与主题对应的数据段（按键名正则过滤）。"""
    lines: List[str] = []
    if not isinstance(context, dict):
        return lines
    for key, val in context.items():
        if not pattern.search(str(key).lower()):
            continue
        if not isinstance(val, list):
            # 非列表值（如 version 字符串）也一并呈现
            lines.append(f"- [{key}] {str(val)[:_ROW_SNIPPET]}")
            continue
        for row in val[:limit]:
            if isinstance(row, dict):
                s = "; ".join(f"{k}={v}" for k, v in row.items())
            else:
                s = str(row)
            lines.append(f"- [{key}] {s[:_ROW_SNIPPET]}")
    return lines


class FocusAnalyst(Specialist):
    id = "focus_analyst"
    name = "定向分析专员"
    description = ("针对单一主题问题（如「分析一下数据库连接数」）只答所问：摘取巡检与"
                   "监控中与该主题相关的数据，BIC-QA 已配置时交由其分析，未配置时用 AI "
                   "模型从巡检结果分析，不输出与问题无关的结论。")
    tags = ["focus", "diagnosis"]
    domain = "focus"

    # 主题相关发现条数上限（控制交给分析模型的体积）
    MAX_RELATED = 10
    # 单条发现文本截断长度
    SNIPPET = 200

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        out: List[Finding] = []
        goal = (ctx.goal or "").strip()
        topic = detect_focus_topic(goal) or {
            "id": "general", "label": "定向", "keywords": [], "metrics": [],
        }
        meta = ctx.inputs.get("target_meta") or {}
        inst = ctx.inputs.get("target_instance") or {}
        db_type = (inst.get("db_type") or meta.get("db_type") or "").lower()
        inst_name = meta.get("instance_name", "")

        # ── 1) 采集主题相关数据 ─────────────────────────────────
        data_lines: List[str] = []

        # 匹配词：主题关键词 + 主题专属英文术语（覆盖 MAX_CONNECTIONS 等参数名）
        kws = [k.lower() for k in topic.get("keywords", [])]
        kws += [t.lower() for t in FOCUS_MATCH_TERMS.get(topic["id"], [])]

        # 1a. 监控快照中与主题相关的实时指标
        snap = _read_latest_snapshot(ctx.target)
        ts = snap.get("ts", "") if isinstance(snap, dict) else ""
        for key in topic.get("metrics", []):
            v = snap.get(key) if isinstance(snap, dict) else None
            if isinstance(v, (int, float)):
                data_lines.append(_fmt_metric(key, v, ts))

        # 1b. 本轮发现（监控异常 / 巡检风险）中与主题相关的条目
        related: List[Finding] = []
        for f in ctx.findings:
            if f.source == self.id:
                continue
            text = (f.title or "") + " " + (f.detail or "")
            low = text.lower()
            if any(k in low for k in kws):
                related.append(f)
        for f in related[: self.MAX_RELATED]:
            data_lines.append(
                f"- [{(f.severity or '').lower()}] {f.title}：{(f.detail or '')[: self.SNIPPET]}"
            )

        # 1c. 从巡检 checkdb 结果里只抽取与主题对应的数据段（按章节键名过滤）。
        #     这是"精准"的关键：不再把全库巡检所有章节数据都喂给分析模型，
        #     而是按问题主题定位到对应数据段（如连接 → sessions/session_limit）。
        ctxdata = ctx.inputs.get("inspection_context") or {}
        has_inspection_ctx = isinstance(ctxdata, dict) and len(ctxdata) > 0
        pattern = FOCUS_CONTEXT_PATTERNS.get(topic["id"])
        if pattern and isinstance(ctxdata, dict):
            sec_lines = _extract_context_section(ctxdata, pattern)
            for ln in sec_lines:
                if ln not in data_lines:
                    data_lines.append(ln)

        ctx.notes.append(
            f"定向分析专员：主题={topic['label']}，采集到 {len(data_lines)} 条相关数据"
            f"（实时指标来自{'快照' if data_lines and not related else '快照/巡检'}，"
            f"相关发现 {len(related)} 条）。"
        )

        if not data_lines:
            if has_inspection_ctx:
                # 巡检已跑、上下文有数据，但该库型模板没有对应主题章节
                detail = (f"{inst_name + ' ' if inst_name else ''}已完成实时巡检"
                          f"（采集到 {len(ctxdata)} 个数据段），但其巡检模板中没有"
                          f"与「{topic['label']}」对应的章节，无法抽取该主题数据。")
                suggestion = ("该库型巡检模板暂未覆盖此主题章节；"
                              "可改用广谱巡检（去掉主题限定词）获取整体结论，"
                              "或为该库型模板补充对应章节。")
            else:
                detail = (f"{inst_name + ' ' if inst_name else ''}当前监控快照与巡检结果中"
                          f"没有与「{goal}」相关的数据，无法给出定向分析结论。")
                suggestion = "确认实时监控采集已开启，并先对该数据源跑一次深度巡检，再重新发起定向分析。"
            out.append(Finding(
                source=self.id,
                category="diagnosis",
                severity="info",
                title=f"暂无与「{topic['label']}」主题相关的数据",
                detail=detail,
                suggestion=suggestion,
                tags=["focus", topic["id"]],
            ))
            return out

        data_text = "\n".join(data_lines)

        # ── 2) BIC-QA 优先 ─────────────────────────────────────
        answer = ""
        via = ""
        skip_reasons: List[str] = []
        bicqa_answer = self._ask_bicqa(ctx, goal, db_type, data_text)
        if bicqa_answer:
            answer, via = bicqa_answer, "bicqa"
        else:
            skip_reasons.append("BIC-QA 未启用/不可达")

        # ── 3) AI 模型兜底 ─────────────────────────────────────
        if not answer:
            advisor = build_advisor()
            if advisor is not None:
                try:
                    prompt = (
                        "你是数据库诊断专家。用户只问了一个具体问题，请只针对该问题作答：\n"
                        f"数据库类型：{db_type}\n用户问题：{goal}\n\n"
                        "与该问题相关的巡检/监控数据：\n" + data_text + "\n\n"
                        "要求：\n"
                        "1. 直接回答问题本身（含关键数值解读）；\n"
                        "2. 如有异常给出根因与处置建议；无异常则明确说明当前状态正常；\n"
                        "3. 不要展开与问题无关的内容，不要输出全面体检式结论。"
                    )
                    answer = advisor._call_llm(prompt, timeout=120).strip()
                    via = "ai"
                except Exception as e:
                    skip_reasons.append(f"AI 模型调用失败（{e}）")
            else:
                skip_reasons.append("AI 模型未配置")

        # ── 4) 原始数据兜底（绝不编造）─────────────────────────
        if not answer:
            answer = ("BIC-QA 与 AI 模型均不可用（" + "；".join(skip_reasons) +
                      "），以下为与该问题相关的原始数据：\n" + data_text)
            via = "raw"

        via_label = {"bicqa": "BIC-QA 知识库", "ai": "AI 模型", "raw": "原始数据"}.get(via, via)
        ctx.notes.append(f"定向分析专员：分析完成（分析来源：{via_label}）。")

        # 严重程度：主题相关发现里有 critical 则 warning 提示，否则 info
        sev = "info"
        if any((f.severity or "").lower() == "critical" for f in related):
            sev = "warning"

        out.append(Finding(
            source=self.id,
            category="diagnosis",
            severity=sev,
            title=f"定向分析结论：{goal}",
            detail=f"（分析来源：{via_label}）\n\n{answer}",
            suggestion="如需全面体检，可去掉主题限定词（如「全面巡检」）后重新发起协同诊断。",
            tags=["focus", topic["id"]],
        ))
        return out

    @staticmethod
    def _ask_bicqa(ctx: SharedContext, goal: str, db_type: str,
                   data_text: str) -> Optional[str]:
        """尝试 BIC-QA 知识库分析；未启用/不可达返回 None（优雅降级）。"""
        ucfg = _bicqa_user_config()
        if ucfg.get("enabled") is False:
            return None
        try:
            from ...mcp_server.bicqa_bridge import BICQAUnavailable, get_bridge
        except Exception:
            return None
        try:
            bridge = get_bridge()
        except BICQAUnavailable:
            return None
        except Exception:
            return None

        mask = ctx.inputs.get("bicqa_mask")
        if mask is None:
            mask = bool(ucfg.get("enable_masking", True))

        question = (
            f"用户问题：{goal}\n"
            f"数据库类型：{db_type}\n\n"
            "与该问题相关的巡检/监控数据：\n" + data_text + "\n\n"
            "请只针对用户的问题给出分析结论：直接回答问题本身（含关键数值解读），"
            "如有异常按 现象解读 / 根因排序 / 定位命令 / 处置建议 给出；"
            "不要展开与问题无关的内容。"
        )
        try:
            return (bridge.ask(question, dbtype=db_type, mask=bool(mask)) or "").strip() or None
        except Exception:
            return None
