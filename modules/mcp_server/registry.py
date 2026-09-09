# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""MCP / Skills 共用工具注册表（阶段 1：带风险元数据的工具箱）。

设计意图（见规划文档 3.2 / 4.3「Skills ≡ P0 MCP 工具」）：本注册表是
**唯一事实来源**——WebUI、多 Agent、外部 MCP Client 三方从同一处取规格，
一次定义、三方复用。本文件只存「元数据」，不含可执行逻辑（handler 在
``tools.py`` 实现，由 ``server.py`` 按 ``handler_key`` 接线），以保证注册表
可被 Skills/多 Agent 复用而无需 import 任何 Flask / 网络依赖。

风险元数据（risk）字段
-----------------------
* ``risk_level``     : low | medium | high —— 对生产库的潜在影响等级
* ``access_mode``    : read | write —— 只读分析 vs 变更
* ``requires_approval`` : bool —— 是否必须经审批（写操作在阶段 2 治理闭环接入）
* ``destructive``    : bool —— 是否会破坏/删除数据
* ``reversible``     : bool —— 动作是否可回滚
* ``runs_on``        : db | llm | history —— 主要在哪侧执行
* ``side_effects``   : str —— 副作用说明（写入审计、产生 token 等）
* ``tags``           : list —— 领域标签，供 Skills/多 Agent 按域检索

阶段 1 仅暴露**只读分析类**工具（access_mode=read、requires_approval=False）。
写操作（kill session / apply index / 执行 SQL）按规划文档延后到阶段 2，届时
只需把对应工具的 ``access_mode`` 置 write 并 ``requires_approval=True``，
``server.py`` 的中央门控会自动要求审批，无需改动工具实现。
"""

from __future__ import annotations

from typing import Any, Dict, List


# ── 风险档位说明（写入工具描述，供 MCP Client 直接可读） ──────────────────────
_RISK_FOOTER = (
    "【风险元数据】等级={risk_level} 模式={access_mode} "
    "需审批={requires_approval} 破坏性={destructive} 可逆={reversible} "
    "执行侧={runs_on}；副作用：{side_effects}"
)


def _foot(risk: Dict[str, Any]) -> str:
    return _RISK_FOOTER.format(
        risk_level=risk["risk_level"],
        access_mode=risk["access_mode"],
        requires_approval=risk["requires_approval"],
        destructive=risk["destructive"],
        reversible=risk["reversible"],
        runs_on=risk["runs_on"],
        side_effects=risk["side_effects"],
    )


# MCP ToolAnnotations（2024-11-05 起支持的提示字段；老客户端忽略）
def _annotations(risk: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "readOnlyHint": risk["access_mode"] == "read",
        "destructiveHint": risk["destructive"],
        "idempotentHint": risk["reversible"],
        "openWorldHint": risk["runs_on"] == "llm",
    }


# ── 工具规格表 ────────────────────────────────────────────────────────────────
_TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "dbcheck.list_instances",
        "title": "列出数据源",
        "domain": "inventory",
        "handler_key": "list_instances",
        "description": (
            "列出当前用户可见的全部数据源（数据库实例）。返回 id / name / "
            "db_type / host / port 等元信息，密码已被剔除。受多租户可见性约束，"
            "仅返回调用者有权看到的数据源。"
        ),
        "tags": ["inventory", "read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "mask_password": {
                    "type": "boolean",
                    "description": "是否脱敏密码（默认 true；无论真假本工具都剔除密码键）",
                    "default": True,
                }
            },
            "required": [],
        },
        "risk": {
            "risk_level": "low",
            "access_mode": "read",
            "requires_approval": False,
            "destructive": False,
            "reversible": True,
            "runs_on": "history",
            "side_effects": "写入 um_audit_log 访问留痕",
            "tags": ["inventory"],
        },
    },
    {
        "name": "dbcheck.run_inspection",
        "title": "执行健康检查巡检",
        "domain": "inspection",
        "handler_key": "run_inspection",
        "description": (
            "对指定数据源执行一次全量健康检查巡检，返回健康分、风险项与 AI 修复"
            "建议。同步阻塞（视库规模可能耗时数分钟）。结果回写本机巡检历史。"
        ),
        "tags": ["inspection", "health", "read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "数据源 ID（来自 dbcheck.list_instances 的 id 字段）",
                },
                "template_id": {"type": "string", "description": "可选巡检模板 ID"},
                "inspector_name": {
                    "type": "string",
                    "description": "巡检人署名",
                    "default": "Jack",
                },
            },
            "required": ["instance_id"],
        },
        "risk": {
            "risk_level": "low",
            "access_mode": "read",
            "requires_approval": False,
            "destructive": False,
            "reversible": True,
            "runs_on": "db",
            "side_effects": "写入本机 inspection_history（分析产物，跟随数据源归属）",
            "tags": ["inspection"],
        },
    },
    {
        "name": "dbcheck.slow_queries",
        "title": "慢 SQL 分析",
        "domain": "slow_sql",
        "handler_key": "slow_queries",
        "description": (
            "对指定数据源分析慢查询：按延迟/IO/锁等待排序的 Top SQL、全表扫描"
            "SQL、当前正在执行的慢查询，并附 AI 根因与处置建议。只读查询性能"
            "视图，不改数据。"
        ),
        "tags": ["slow_sql", "read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "数据源 ID（来自 dbcheck.list_instances 的 id 字段）",
                },
                "top_n": {
                    "type": "integer",
                    "description": "每类 Top SQL 返回条数",
                    "default": 10,
                },
                "lang": {
                    "type": "string",
                    "description": "AI 建议语言 zh/en",
                    "default": "zh",
                },
            },
            "required": ["instance_id"],
        },
        "risk": {
            "risk_level": "low",
            "access_mode": "read",
            "requires_approval": False,
            "destructive": False,
            "reversible": True,
            "runs_on": "db",
            "side_effects": "只读查询慢日志/性能视图；写入 um_audit_log 访问留痕",
            "tags": ["slow_sql"],
        },
    },
    {
        "name": "dbcheck.lock_tree",
        "title": "锁等待链分析",
        "domain": "lock",
        "handler_key": "lock_tree",
        "description": (
            "对指定数据源分析当前锁等待与阻塞链，定位持锁源头（blocking root）"
            "与受影响等待者，返回等待树与阻塞会话明细。只读查询系统锁视图。"
        ),
        "tags": ["lock", "read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "数据源 ID（来自 dbcheck.list_instances 的 id 字段）",
                }
            },
            "required": ["instance_id"],
        },
        "risk": {
            "risk_level": "low",
            "access_mode": "read",
            "requires_approval": False,
            "destructive": False,
            "reversible": True,
            "runs_on": "db",
            "side_effects": "只读查询锁视图；写入 um_audit_log 访问留痕",
            "tags": ["lock"],
        },
    },
    {
        "name": "dbcheck.index_health",
        "title": "索引健康分析",
        "domain": "index",
        "handler_key": "index_health",
        "description": (
            "对指定数据源分析索引健康度：冗余/重复索引、从未使用索引、选择性差"
            "的索引等，返回健康报告与可读文本。只读查询索引统计。"
        ),
        "tags": ["index", "read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "数据源 ID（来自 dbcheck.list_instances 的 id 字段）",
                },
                "days_threshold": {
                    "type": "integer",
                    "description": "未使用索引判定天数阈值",
                    "default": 90,
                },
            },
            "required": ["instance_id"],
        },
        "risk": {
            "risk_level": "low",
            "access_mode": "read",
            "requires_approval": False,
            "destructive": False,
            "reversible": True,
            "runs_on": "db",
            "side_effects": "只读查询索引统计；写入 um_audit_log 访问留痕",
            "tags": ["index"],
        },
    },
    {
        "name": "dbcheck.baseline_check",
        "title": "配置基线检查",
        "domain": "baseline",
        "handler_key": "baseline_check",
        "description": (
            "对指定数据源做配置基线与合规检查，对比当前参数与推荐值，输出严重/"
            "警告/正常项及差距百分比。只读查询配置参数。"
        ),
        "tags": ["baseline", "read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "数据源 ID（来自 dbcheck.list_instances 的 id 字段）",
                }
            },
            "required": ["instance_id"],
        },
        "risk": {
            "risk_level": "low",
            "access_mode": "read",
            "requires_approval": False,
            "destructive": False,
            "reversible": True,
            "runs_on": "db",
            "side_effects": "只读查询配置参数；写入 um_audit_log 访问留痕",
            "tags": ["baseline"],
        },
    },
    {
        "name": "dbcheck.ai_diagnose",
        "title": "AI 协同诊断",
        "domain": "ai",
        "handler_key": "ai_diagnose",
        "description": (
            "对指定数据源启动 AI 协同诊断中心（多专员迭代编排 + 协调员调度），"
            "返回发现项、处置方案与执行笔记。会调用本地 LLM（可能产生 token "
            "耗时），只读连接数据库，不改数据。"
        ),
        "tags": ["ai", "diagnose", "read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "数据源 ID（来自 dbcheck.list_instances 的 id 字段）",
                },
                "goal": {
                    "type": "string",
                    "description": "诊断目标（默认：综合诊断）",
                    "default": "对目标数据源做一次综合诊断",
                },
            },
            "required": ["instance_id"],
        },
        "risk": {
            "risk_level": "medium",
            "access_mode": "read",
            "requires_approval": False,
            "destructive": False,
            "reversible": True,
            "runs_on": "llm",
            "side_effects": "调用 LLM（耗时/可能消耗 token）；只读连接数据库；写入 um_audit_log",
            "tags": ["ai"],
        },
    },
    {
        "name": "dbcheck.nl2sql",
        "title": "自然语言转 SQL（Chat2DB）",
        "domain": "nl2sql",
        "handler_key": "nl2sql",
        "description": (
            "调用 Chat2DB（作为上游 MCP Server）的 text2sql 能力，把自然语言问题"
            "转换成可在目标数据源执行的 SQL。连接管理、表结构上下文由 Chat2DB 负责；"
            "DBCheck 仅做协议桥接与可见性/审计留痕，不嵌入 Chat2DB 任何代码（遵守 "
            "Apache-2.0 许可边界）。生成结果中的 SQL 若需执行，请走本通道既有写类 "
            "Skill dbcheck.execute_sql（经 WriteGate 审批）。Chat2DB 未配置/不可用时"
            "本工具返回清晰错误而非击穿通道。"
        ),
        "tags": ["nl2sql", "ai", "read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "自然语言问题，如「查过去 7 天未支付订单」",
                },
                "datasource_id": {
                    "type": "string",
                    "description": "Chat2DB 侧数据源 ID（来自 Chat2DB 已配置数据源）；"
                                  "若省略则按 instance_id 经 CHAT2DB_DATASOURCE_MAP 映射",
                },
                "instance_id": {
                    "type": "string",
                    "description": "可选 DBCheck 实例 ID，用于可见性校验与映射到 Chat2DB 数据源",
                },
            },
            "required": ["question"],
        },
        "risk": {
            "risk_level": "medium",
            "access_mode": "read",
            "requires_approval": False,
            "destructive": False,
            "reversible": True,
            "runs_on": "llm",
            "side_effects": "调用 Chat2DB 上游 MCP text2sql（经其配置的模型，可能产生 token）；"
                         "写入 um_audit_log 访问留痕",
            "tags": ["nl2sql"],
        },
    },
    {
        "name": "dbcheck.bicqa_ask",
        "title": "BIC-QA 知识问答",
        "domain": "knowledge",
        "handler_key": "bicqa_ask",
        "description": (
            "调用外部 BIC-QA 知识库（用户自带 API Key，注册 https://www.bic-qa.com 获取）"
            "回答数据库领域知识问题：Oracle AWR 怎么读、MySQL 慢查询怎么定位、金仓 work_mem "
            "怎么调、各类报错根因等。DBCheck 仅做协议桥接与脱敏/审计留痕，不嵌入 BIC-QA 任何"
            "代码（遵守 Apache-2.0 许可边界）。默认对问题文本做敏感信息脱敏后再外发（IP/邮箱/"
            "连接串/口令打码）。BIC-QA 未配置/不可用时本工具返回清晰错误而非击穿通道，与本地 11 "
            "专家互补（本地做现场数据分析，BIC-QA 提供知识库支撑）。"
        ),
        "tags": ["knowledge", "ai", "read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "数据库领域知识问题，如「Oracle AWR 报告里 DB Time 很高怎么分析」",
                },
                "dbtype": {
                    "type": "string",
                    "description": "数据库/主题类型（mysql|oracle|postgresql|kingbase|...），用于知识库定向检索",
                    "default": "",
                },
                "mask": {
                    "type": "boolean",
                    "description": "外发前是否对问题文本脱敏（默认 true；强烈建议保持开启）",
                    "default": True,
                },
            },
            "required": ["question"],
        },
        "risk": {
            "risk_level": "medium",
            "access_mode": "read",
            "requires_approval": False,
            "destructive": False,
            "reversible": True,
            "runs_on": "llm",
            "side_effects": "调用外部 BIC-QA API（用户自带 Key，不外发敏感信息）；写入 um_audit_log + 外部调用留痕",
            "tags": ["knowledge"],
        },
    },
]


# ── 衍生：带风险脚注与注释的工具定义（供 server.build_tools 使用） ──────────────
def _enrich(spec: Dict[str, Any]) -> Dict[str, Any]:
    """生成供 tools/list 使用的完整工具对象（描述含风险脚注 + annotations + x-risk）。"""
    risk = spec["risk"]
    return {
        "name": spec["name"],
        "title": spec["title"],
        "domain": spec["domain"],
        "handler_key": spec["handler_key"],
        "description": spec["description"] + "\n\n" + _foot(risk),
        "tags": spec.get("tags", []),
        "inputSchema": spec["inputSchema"],
        "risk": risk,
        "annotations": _annotations(risk),
        # 非标准扩展字段，未知客户端忽略
        "x-risk": risk,
    }


def get_tool_specs() -> List[Dict[str, Any]]:
    """返回全部工具规格（已 enrich）。"""
    return [_enrich(s) for s in _TOOL_SPECS]


def get_tool_spec(name: str) -> Dict[str, Any] | None:
    """按工具名取单条 enrich 后的规格；不存在返回 None。"""
    for s in _TOOL_SPECS:
        if s["name"] == name:
            return _enrich(s)
    return None


def list_tools_by_domain(domain: str) -> List[Dict[str, Any]]:
    """按领域取工具名列表（供 Skills/多 Agent 检索）。"""
    return [s["name"] for s in _TOOL_SPECS if s.get("domain") == domain]


def all_handler_keys() -> List[str]:
    """返回全部 handler_key（供 server 接线校验）。"""
    return [s["handler_key"] for s in _TOOL_SPECS]


# ─────────────────────────────────────────────────────────────────────────────
# Skills 规格（阶段 B：Skills 与 MCP 共用注册表）
# ─────────────────────────────────────────────────────────────────────────────
# 写类 / 破坏性能力：专家（多 Agent）或外部 MCP Client 均可调用。一次定义、两方复用。
# 这些 Skill 的 access_mode=write 且 requires_approval=True，调用时一律走 WriteGate
# （modules.intelligence.skills），生成 SQL 并提交 SQL 审计 pending_approval，审批后
# 受控执行。读类能力复用 _TOOL_SPECS，无需在此重复定义。
_SKILL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "dbcheck.kill_session",
        "title": "终止阻塞会话",
        "domain": "remediation",
        "handler_key": "kill_session",
        "description": (
            "终止指定阻塞/空闲会话（高危写操作）。生成 KILL 语句并提交 SQL 审计，"
            "需审批后执行，不影响其它会话。"
        ),
        "tags": ["remediation", "write", "session"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "目标数据源 ID（来自 dbcheck.list_instances 的 id 字段）",
                },
                "session_id": {
                    "type": "string",
                    "description": "待终止的会话 id（如 MySQL 的 Id / Oracle 的 SID,SERIAL#）",
                },
                "reason": {
                    "type": "string",
                    "description": "终止原因（进审计留痕）",
                    "default": "",
                },
            },
            "required": ["instance_id", "session_id"],
        },
        "risk": {
            "risk_level": "high",
            "access_mode": "write",
            "requires_approval": True,
            "destructive": False,
            "reversible": False,
            "runs_on": "db",
            "side_effects": "生成终止会话语句并提交 SQL 审计 pending_approval；审批后执行 KILL",
            "tags": ["remediation", "session"],
        },
    },
    {
        "name": "dbcheck.apply_index",
        "title": "创建/重建索引",
        "domain": "remediation",
        "handler_key": "apply_index",
        "description": (
            "在表上创建或重建索引（写操作，结构变更）。生成 CREATE INDEX 并提交 SQL 审计，"
            "需审批后执行；可回滚为 DROP INDEX。"
        ),
        "tags": ["remediation", "write", "index"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "目标数据源 ID（来自 dbcheck.list_instances 的 id 字段）",
                },
                "table": {
                    "type": "string",
                    "description": "目标表名（可带 schema，如 scott.emp）",
                },
                "columns": {
                    "type": "string",
                    "description": "逗号分隔的索引列，如 col1,col2",
                },
                "index_name": {
                    "type": "string",
                    "description": "索引名（可选，缺省按表名+列名自动生成）",
                    "default": "",
                },
                "unique": {
                    "type": "boolean",
                    "description": "是否唯一索引",
                    "default": False,
                },
            },
            "required": ["instance_id", "table", "columns"],
        },
        "risk": {
            "risk_level": "high",
            "access_mode": "write",
            "requires_approval": True,
            "destructive": False,
            "reversible": True,
            "runs_on": "db",
            "side_effects": "生成 CREATE INDEX 并提交 SQL 审计 pending_approval；审批后执行（可回滚）",
            "tags": ["remediation", "index"],
        },
    },
    {
        "name": "dbcheck.execute_sql",
        "title": "执行 SQL（受控）",
        "domain": "remediation",
        "handler_key": "execute_sql",
        "description": (
            "在目标数据源执行任意 SQL（写操作）。语句直接提交 SQL 审计：命中阻断规则则 "
            "blocked，否则进入 pending_approval，审批后受控执行（含回滚备援）。"
        ),
        "tags": ["remediation", "write", "sql"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "目标数据源 ID（来自 dbcheck.list_instances 的 id 字段）",
                },
                "sql_text": {
                    "type": "string",
                    "description": "待执行的 SQL（可含多条，分号分隔）",
                },
                "env": {
                    "type": "string",
                    "description": "环境标识 prod / test",
                    "default": "prod",
                },
            },
            "required": ["instance_id", "sql_text"],
        },
        "risk": {
            "risk_level": "high",
            "access_mode": "write",
            "requires_approval": True,
            "destructive": True,
            "reversible": False,
            "runs_on": "db",
            "side_effects": "语句提交 SQL 审计 pending_approval；审批后受控执行（含回滚备援）",
            "tags": ["remediation", "sql"],
        },
    },
]


def get_skill_specs() -> List[Dict[str, Any]]:
    """返回全部 Skill 规格（已 enrich，含风险元数据 + annotations + x-risk）。"""
    return [_enrich(s) for s in _SKILL_SPECS]


def list_skills() -> List[str]:
    """返回全部 Skill 名（供多 Agent 检索）。"""
    return [s["name"] for s in _SKILL_SPECS]


def get_spec(name: str) -> Dict[str, Any] | None:
    """按名取工具或 Skill 的 enrich 规格（Skills 与 MCP 共用注册表的统一查询入口）。

    先查 MCP 工具，再查 Skills；返回 None 表示未知。
    """
    for s in _TOOL_SPECS:
        if s["name"] == name:
            return _enrich(s)
    for s in _SKILL_SPECS:
        if s["name"] == name:
            return _enrich(s)
    return None


def all_spec_names() -> List[str]:
    """返回全部工具名 + Skill 名（供调试 / 文档生成）。"""
    return [s["name"] for s in _TOOL_SPECS] + [s["name"] for s in _SKILL_SPECS]
