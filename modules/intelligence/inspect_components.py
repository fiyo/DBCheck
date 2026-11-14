# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""巡检组件层（P1 巡检组件化 · Level-2 组件模型）。

把「数据库巡检」的 **章节/检查组** 暴露为一等公民组件（不是 query 级，
避免选择过载；见 docs/design/fleet-intelligence.md 同期战略评估）：

* ``list_components()``   —— 枚举全部可用组件（db_type → template → chapter）
* ``resolve_component()`` —— 按标题关键词匹配组件（内置 runbook 用，避免硬编码 id）
* ``run_component()``     —— 只读执行组件并产出 Finding 契约

组件契约（Level-2）::

    run(ctx) -> List[Finding]

- 数据采集：章节内全部启用查询，经 SQL 审核同源的 ``plan_analyzer
  .connect_instance`` 只读执行（复用其库型归并与驱动适配，不新造通道）；
- 风险判定：``analyzer.collect_issues(db_type, context)`` 复用既有智能
  分析规则（auto_analyze / issues / smart_analyze 同一口径），只跑选中
  章节时规则引擎对缺失 key 自动跳过，天然支持章节裁剪；
- 兜底发现：每条查询无论是否有规则命中，都会产出一条 data 类 Finding
  （含行数/列名摘要），保证组件输出可观测、可驱动下游编排。

安全边界：只执行 SELECT/SHOW 等只读语句——章节查询来自巡检模板本身，
且执行前做写类语句拦截（与自治闭环 _WRITE_TOKENS 同源口径）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 组件 id 前缀（Workflow inspect 节点 ref 统一格式）
COMP_PREFIX = "insp"

# 标题关键词 → 类别标签（用于组件分组展示与 runbook 语义匹配）
_CATEGORY_KEYWORDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("slow",     ("慢查询", "慢 SQL", "slow_query", "慢sql")),
    ("lock",     ("锁", "阻塞", "lock", "等待事件")),
    ("capacity", ("表空间", "空间", "容量", "磁盘", "存储")),
    ("session",  ("会话", "连接", "session", "process")),
    ("config",   ("参数", "配置", "配置基线", "参数基线")),
    ("repl",     ("复制", "主备", "延迟", "replication", "adg", "dataguard")),
    ("backup",   ("备份", "归档", "backup")),
    ("security", ("权限", "账号", "安全", "审计", "密码")),
    ("index",    ("索引", "index")),
    ("object",   ("对象", "表结构", "大表", "碎片")),
    ("version",  ("版本", "补丁")),
]


def _category_from_title(title: str) -> List[str]:
    t = (title or "").lower()
    cats = []
    for cat, kws in _CATEGORY_KEYWORDS:
        for kw in kws:
            if kw.lower() in t:
                cats.append(cat)
                break
    return cats or ["general"]


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 组件枚举 ─────────────────────────────────────────────────────────────

def list_components(db_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """枚举巡检组件（章节级）。

    返回元素形如::

        {
            "id": "insp:<template_id>:<chapter_id>",
            "kind": "inspect",
            "title": "慢查询统计",
            "db_type": "mysql",
            "template_id": 1, "template_name": "MySQL 默认模板",
            "chapter_id": 3, "query_count": 4,
            "is_default": 1, "categories": ["slow"],
        }
    """
    from modules.inspection import dal

    templates = dal.get_all_templates()
    out: List[Dict[str, Any]] = []
    for t in templates:
        t_db = (t.get("db_type") or "").lower()
        if db_type and t_db != db_type.lower():
            continue
        try:
            chapters = dal.get_chapters_by_template(t["id"])
        except Exception:
            continue
        for ch in chapters:
            qn = int(ch.get("query_count") or 0)
            if qn <= 0:
                continue  # 空章节不作为组件暴露
            title = ch.get("chapter_title_zh") or ch.get("chapter_title_en") \
                or ("章节 #%s" % ch.get("chapter_number"))
            out.append({
                "id": "%s:%s:%s" % (COMP_PREFIX, t["id"], ch["id"]),
                "kind": "inspect",
                "title": title,
                "db_type": t_db,
                "template_id": t["id"],
                "template_name": t.get("template_name") or "",
                "chapter_id": ch["id"],
                "query_count": qn,
                "is_default": int(t.get("is_default") or 0),
                "categories": _category_from_title(title),
            })
    out.sort(key=lambda c: (c["db_type"], not c["is_default"], c["title"]))
    return out


def get_component(comp_id: str) -> Optional[Dict[str, Any]]:
    """按 id 精确取组件元数据。"""
    parts = (comp_id or "").split(":")
    if len(parts) != 3 or parts[0] != COMP_PREFIX:
        return None
    try:
        template_id, chapter_id = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    for c in list_components():
        if c["template_id"] == template_id and c["chapter_id"] == chapter_id:
            return c
    return None


def resolve_component(db_type: str, match_title: str) -> Optional[Dict[str, Any]]:
    """按库型 + 标题关键词模糊匹配第一个组件（runbook 声明式引用）。

    匹配规则：``match_title`` 为子串（大小写不敏感）匹配组件标题；
    优先命中 default 模板。找不到返回 None。
    """
    kw = (match_title or "").strip().lower()
    if not kw:
        return None
    comps = list_components(db_type=db_type)
    defaults = [c for c in comps if c["is_default"]]
    for c in defaults + [x for x in comps if x not in defaults]:
        if kw in (c["title"] or "").lower():
            return c
    return None


# ── 组件执行 ─────────────────────────────────────────────────────────────

# 写类语句拦截（组件只读；与自治闭环口径一致）
_WRITE_TOKENS = re.compile(
    r"^\s*(insert|update|delete|merge|replace|create|alter|drop|truncate|"
    r"grant|revoke|call|exec|execute|begin|declare|set\s+global|set\s+session|"
    r"shutdown|kill|reconfigure|sp_configure)\b",
    re.IGNORECASE,
)


def _safe_queries(queries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤出启用的只读查询。"""
    out = []
    for q in queries:
        if not int(q.get("enabled", 1) or 0):
            continue
        sql = (q.get("query_sql") or "").strip()
        if not sql:
            continue
        # 剥离注释后再判写类（-- 与 # 行注释、/* */ 块注释）
        cleaned = re.sub(r"--[^\n]*", "", sql)
        cleaned = re.sub(r"/\*[\s\S]*?\*/", "", cleaned)
        cleaned = re.sub(r"#[^\n]*", "", cleaned)
        if _WRITE_TOKENS.match(cleaned):
            continue
        out.append({"key": q.get("query_key") or ("q%d" % q.get("id", 0)),
                    "sql": sql})
    return out


def _resolve_instance(instance_id: str) -> Optional[Dict[str, Any]]:
    """解析实例连接信息（解密）。id 找不到时退化为按名称匹配。"""
    from modules.pro import get_instance_manager

    im = get_instance_manager()
    inst = im.get_instance_decrypted(instance_id)
    if inst:
        return inst
    for it in im.get_all_instances_decrypted():
        if (it.get("name") or "") == instance_id:
            return it
    return None


def run_component(comp_id: str = "", instance_id: str = "",
                  match_title: str = "", timeout_s: int = 15) -> Dict[str, Any]:
    """只读执行一个巡检组件。

    支持两种引用方式（工作流 inspect 节点二选一）：
    - ``comp_id``：精确 id（``insp:<template_id>:<chapter_id>``）
    - ``match_title``：按目标实例库型 + 标题关键词匹配（runbook 声明式）

    返回 ``{"ok", "component", "findings", "metrics", "error", "error_code"}``。
    """
    comp = get_component(comp_id) if comp_id else None
    if comp is None and match_title:
        inst0 = _resolve_instance(instance_id)
        db_t = (inst0 or {}).get("db_type") or ""
        comp = resolve_component(db_t, match_title)
    if comp is None:
        return {"ok": False, "error_code": "COMPONENT_NOT_FOUND",
                "error": "组件不存在: %s" % (comp_id or match_title),
                "findings": [], "metrics": {}}

    inst = _resolve_instance(instance_id)
    if not inst:
        return {"ok": False, "error_code": "INSTANCE_NOT_FOUND",
                "error": "实例不存在: %s" % instance_id,
                "findings": [], "metrics": {}}

    from modules.inspection import dal

    queries = _safe_queries(dal.get_queries_by_chapter(comp["chapter_id"]))
    if not queries:
        return {"ok": False, "error_code": "NO_QUERIES",
                "error": "组件内没有可执行的只读查询",
                "findings": [], "metrics": {}}

    from modules.sqlaudit.plan_analyzer import connect_instance

    try:
        conn = connect_instance(inst)
    except Exception as e:
        return {"ok": False, "error_code": "CONNECT_FAILED",
                "error": "连接失败: %s" % e, "findings": [], "metrics": {}}

    context: Dict[str, Any] = {}
    data_findings: List[Dict[str, Any]] = []
    metrics: Dict[str, Any] = {}
    try:
        cur = conn.cursor()
        for q in queries:
            try:
                cur.execute(q["sql"])
                cols = None
                if cur.description:
                    cols = [c[0] if isinstance(c, tuple) else c.name
                            for c in cur.description]
                rows = cur.fetchall()
                data = []
                for r in rows[:200]:
                    if hasattr(r, "keys"):
                        data.append({k: r[k] for k in r.keys()})
                    else:
                        data.append(dict(zip(cols or [], r)))
                context[q["key"]] = {"columns": cols or [], "data": data}
                metrics[q["key"]] = {"rows": len(rows),
                                     "columns": cols or []}
                data_findings.append({
                    "source": "inspect_component",
                    "category": "data",
                    "severity": "info",
                    "title": "[%s] %s：%d 行" % (comp["title"], q["key"], len(rows)),
                    "detail": "只读采集完成，列：%s" % ", ".join(cols or [])[:200],
                    "suggestion": "",
                    "tags": [comp["id"], "inspect"],
                })
            except Exception as e:  # 单条失败不阻断整章
                context[q["key"]] = {"columns": [], "data": [], "_error": str(e)}
                metrics[q["key"]] = {"rows": 0, "error": str(e)}
    finally:
        # issue #64：关闭工作流 inspect 步骤经 SSH 隧道建立的连接时一并关闭隧道
        try:
            _t = getattr(conn, '_ssh_tunnel', None)
            if _t is not None:
                _t.__exit__(None, None, None)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    # 复用既有智能分析规则（缺失 key 自动跳过，天然支持章节裁剪）
    from modules.inspection.analyzer import collect_issues

    rule_issues: List[Dict[str, Any]] = []
    try:
        rule_issues = collect_issues(inst.get("db_type"), context) or []
    except Exception:
        rule_issues = []

    severity_rank = {"info": 0, "warning": 1, "high": 2, "critical": 3}
    findings: List[Dict[str, Any]] = []
    for it in rule_issues:
        if not isinstance(it, dict):
            continue
        sev = str(it.get("severity") or it.get("risk") or "warning").lower()
        if sev not in severity_rank:
            sev = "warning"
        findings.append({
            "source": "inspect_component",
            "category": _category_from_title(comp["title"])[0],
            "severity": sev,
            "title": it.get("title") or it.get("description") or comp["title"],
            "detail": it.get("detail") or it.get("value") or "",
            "suggestion": it.get("suggestion") or "",
            "tags": [comp["id"], "inspect"],
        })
    findings.extend(data_findings)

    return {
        "ok": True,
        "component": comp,
        "instance": {"id": instance_id, "name": inst.get("name"),
                     "db_type": inst.get("db_type")},
        "metrics": metrics,
        "findings": findings,
        "run_at": _now(),
        "severity_max": max(
            [severity_rank.get(f["severity"], 0) for f in findings] or [0]),
    }
