# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""自然语言探查专员（NlQueryExpert）。

工作流（对应智能诊断中心「选数据源 + 输入诊断目标」场景）：

1. 读取用户诊断目标 goal 与目标数据源实例。
2. 让 AI 规划「该查哪些系统视图 / 数据字典才能回答这个问题」，产出若干候选
   SQL 方案（含兜底的基于关键词的规则方案，保证未配置 AI 时也能跑通常见库）。
3. 对每个候选视图，最多 5 次「执行 → 若报错则让 AI 修正 SQL」循环：
   - 拿到结果（即便为空行集也算成功）→ 进入第 4 步；
   - 5 次仍失败 → 跳过该视图，换下一个候选思路；
   - 所有候选都失败 → 跳过，给出「无法回答」结论。
4. 把「用户问题 + 成功的 SQL + 查询结果」交给 AI 做最终分析，产出诊断结论。
5. 全程把尝试过程写进 ctx.notes，前端可透明看到「试了什么、为什么换思路」。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from ..context import SharedContext, Finding
from ..specialist import Specialist
from ..db_executor import execute_instance_query, get_table_columns, validate_sql_with_explain
from ..ai_helper import build_advisor
from ..schema_knowledge import find_relevant_views, build_sql_from_fields, get_view_columns, get_view_info


# ── 无 AI 时的规则兜底方案：关键词 → 候选 SQL ──────────────────────────────
def _rule_plan(db_type: str, goal: str) -> List[Dict[str, str]]:
    t = (db_type or "").lower().replace("oracle_full", "oracle")
    g = (goal or "").lower()
    out: List[Dict[str, str]] = []

    if t in ("oracle", "oracle_jdbc"):
        if "bigfile" in g or "表空间" in g or "tablespace" in g:
            out.append({
                "view": "DBA_TABLESPACES",
                "sql": "SELECT TABLESPACE_NAME, BIGFILE, STATUS, CONTENTS "
                       "FROM DBA_TABLESPACES ORDER BY TABLESPACE_NAME",
                "rationale": "DBA_TABLESPACES.BIGFILE 标识是否为 BIGFILE 表空间",
            })
            out.append({
                "view": "DBA_TABLESPACES(计数)",
                "sql": "SELECT COUNT(*) AS BIGFILE_COUNT FROM DBA_TABLESPACES "
                       "WHERE BIGFILE = 'YES'",
                "rationale": "直接统计 BIGFILE='YES' 的表空间数量",
            })
        if "表" in g or "table" in g or "对象数" in g:
            out.append({
                "view": "DBA_TABLES",
                "sql": "SELECT COUNT(*) AS TABLE_COUNT FROM DBA_TABLES",
                "rationale": "统计当前用户可访问的表数量",
            })
        if "用户" in g or "user" in g:
            out.append({
                "view": "DBA_USERS",
                "sql": "SELECT USERNAME, ACCOUNT_STATUS, CREATED FROM DBA_USERS "
                       "ORDER BY CREATED DESC",
                "rationale": "列出数据库用户及状态",
            })

    elif t in ("mysql", "tidb", "mariadb", "oceanbase", "tdsqlc_mysql"):
        if "表" in g or "table" in g or "库" in g or "database" in g:
            out.append({
                "view": "INFORMATION_SCHEMA.TABLES",
                "sql": "SELECT TABLE_SCHEMA, COUNT(*) AS TABLE_COUNT "
                       "FROM INFORMATION_SCHEMA.TABLES GROUP BY TABLE_SCHEMA "
                       "ORDER BY TABLE_COUNT DESC",
                "rationale": "按库统计表数量",
            })
        if "引擎" in g or "engine" in g:
            out.append({
                "view": "INFORMATION_SCHEMA.TABLES",
                "sql": "SELECT ENGINE, COUNT(*) AS CNT FROM "
                       "INFORMATION_SCHEMA.TABLES GROUP BY ENGINE",
                "rationale": "统计各存储引擎的表数量",
            })

    elif t in ("postgresql", "pg", "ivorysql", "kingbase", "hgdb", "hgdb_jdbc",
               "uxdb", "uxdb_jdbc"):
        if "表空间" in g or "tablespace" in g:
            out.append({
                "view": "pg_tablespace",
                "sql": "SELECT spcname, pg_tablespace_location(oid) AS location "
                       "FROM pg_tablespace ORDER BY spcname",
                "rationale": "PostgreSQL 表空间目录",
            })
        if "表" in g or "table" in g:
            out.append({
                "view": "pg_tables",
                "sql": "SELECT schemaname, COUNT(*) AS TABLE_COUNT FROM "
                       "pg_tables GROUP BY schemaname ORDER BY TABLE_COUNT DESC",
                "rationale": "按 schema 统计表数量",
            })

    if not out:
        # 通用兜底：尝试常见系统视图计数
        out.append({
            "view": "(通用)表数量统计",
            "sql": "SELECT 'tables' AS obj, COUNT(*) AS cnt FROM "
                   "INFORMATION_SCHEMA.TABLES",
            "rationale": "通用兜底：尝试 INFORMATION_SCHEMA.TABLES",
        })
    return out


# ── 解析 AI 返回的 JSON 计划 ──────────────────────────────────────────────
def _parse_plan(text: str) -> List[Dict[str, str]]:
    # 去掉 ```json ... ``` 围栏
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    blob = m.group(1) if m else text
    # 截取首个 [ 到最后的 ]
    s = blob.find("[")
    e = blob.rfind("]")
    if s != -1 and e != -1 and e > s:
        blob = blob[s:e + 1]
    try:
        arr = json.loads(blob)
        if isinstance(arr, list):
            return [
                {
                    "view": str(x.get("view", "")),
                    "sql": str(x.get("sql", "")).strip(),
                    "rationale": str(x.get("rationale", "")),
                }
                for x in arr if isinstance(x, dict) and x.get("sql")
            ]
    except Exception:
        pass
    return []


def _clean_sql(text: str) -> str:
    """从 AI 的文本里抽出可执行的 SQL。"""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # 去行注释与多余空白
    text = re.sub(r"--[^\n]*", " ", text)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = text.strip().rstrip(";").strip()
    return text


def _fix_sql_with_columns(sql: str, actual_cols: List[str], db_type: str) -> str:
    """根据实际列名列表校验并尝试自动修复 SQL。

    - 检查 SQL 中引用的列名是否在 actual_cols 中；
    - 如果列名不存在，尝试在 actual_cols 中找到相似列名并替换；
    - 对于常见错误列名做智能映射（如 BLOCKS -> BIGFILE，因为用户常想知道是否有 BIGFILE 表空间）。
    - 去掉末尾的分号（Python cx_Oracle/oracledb 不需要分号）。
    返回修复后的 SQL。
    """
    if not sql:
        return sql

    # 去掉末尾的分号（Python 执行不需要）
    sql = sql.rstrip().rstrip(';').rstrip()

    if not actual_cols:
        return sql

    # 全部转大写方便比较
    actual_set = {c.upper() for c in actual_cols}

    # 常见错误列名智能映射（Oracle 表空间等）
    KNOWN_MISMAP = {
        "TABLESPACE_TYPE": "BIGFILE",
        "TABLESPACE_NAME": "TABLESPACE_NAME",
        "BLOCKS": "BIGFILE",  # BLOCKS 不存在，用 BIGFILE 代替
        "NUMBLOCKS": "BIGFILE",
        "NUM_FILES": "BIGFILE",
    }

    # 提取 SQL 中的列名（简化：找 SELECT 后的列和 WHERE 后的列）
    col_pattern = re.compile(r'\b([A-Z_][A-Z0-9_$]*)\b', re.IGNORECASE)

    def replace_col(col):
        col_upper = col.upper()
        # 如果列名已在 actual_cols 中，直接返回
        if col_upper in actual_set:
            return col
        # 检查是否是已知错误映射
        if col_upper in KNOWN_MISMAP:
            mapped = KNOWN_MISMAP[col_upper]
            if mapped and mapped in actual_set:
                return mapped
        # 尝试模糊匹配：找包含原列名子串的列
        col_lower = col.lower()
        for ac in actual_cols:
            if col_lower in ac.lower() or ac.lower() in col_lower:
                return ac
        # 无法匹配，返回原列名
        return col

    # 简单处理：找到 SELECT 和 WHERE 子句中的列名并替换
    fixed_sql = sql
    # 找到所有列名位置并替换
    matches = list(col_pattern.finditer(sql))
    # 从后往前替换，避免索引变化
    for m in reversed(matches):
        start, end = m.span()
        old_col = m.group(1)
        new_col = replace_col(old_col)
        if new_col != old_col:
            fixed_sql = fixed_sql[:start] + new_col + fixed_sql[end:]

    return fixed_sql


_ADHOC_HINTS = (
    "?", "？", "几个", "多少", "哪些", "是否", "怎么", "如何", "查一下", "查 ",
    "统计", "计数", "表空间", "视图", "用户数", "有几", "数量", "占比", "分布",
    "tablespace", "how many", "how much", "which", "count", "list", "show",
    "query", "top ", "最大的", "最小的", "是否存在",
)


def _looks_adhoc(goal: str) -> bool:
    """判断诊断目标是否像「自然语言问答 / 即席查询」，仅此类才激活探查。"""
    g = (goal or "").strip().lower()
    if not g:
        return False
    if g.endswith(("?", "？")):
        return True
    return any(h in g for h in _ADHOC_HINTS)


class NlQueryExpert(Specialist):
    id = "nl_query_expert"
    name = "自然语言探查专员"
    description = "针对用户的自然语言诊断目标，自主规划应查询的系统视图/数据字典，构造 SQL 执行并自动纠错（单视图最多 5 次），拿到结果后交由 AI 给出诊断结论。"
    tags = ["nlquery", "ad-hoc", "diagnosis"]
    domain = "nl"

    MAX_ATTEMPTS = 5

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        out: List[Finding] = []
        goal = (ctx.goal or "").strip()
        inst = ctx.inputs.get("target_instance")
        meta = ctx.inputs.get("target_meta") or {}
        db_type = (inst or {}).get("db_type") or meta.get("db_type") or ""

        if not goal or not inst:
            return out

        # 仅当目标看起来是自然语言问答/即席查询时才激活，避免污染常规诊断
        if not _looks_adhoc(goal):
            ctx.notes.append(
                "自然语言探查专员：当前目标非问答式探查，跳过（如需即席查询，"
                "可在目标中描述「查/统计/几个/表空间」等意图）。"
            )
            return out

        ctx.notes.append(
            f"自然语言探查专员：目标={goal!r}，数据源类型={db_type}"
        )

        # ── 1) 规划候选查询 ──
        # 使用知识库动态生成SQL（基于用户问题和可用字段）
        advisor = build_advisor()  # 提前准备，列预检需要
        plan: List[Dict[str, str]] = []

        # 从知识库找到相关视图
        relevant_views = find_relevant_views(db_type, goal)
        if relevant_views:
            for rv in relevant_views[:3]:  # 最多3个候选
                view_name = rv.get("view", "")
                fields = rv.get("fields", {})
                # 动态生成SQL
                sql, reason = build_sql_from_fields(db_type, view_name, fields, goal)
                plan.append({
                    "view": view_name,
                    "sql": sql,
                    "rationale": f"知识库动态生成：{reason}"
                })
            ctx.notes.append(
                f"自然语言探查专员：知识库找到 {len(relevant_views)} 个相关视图，动态生成 {len(plan)} 个候选SQL。"
            )

        # 如果知识库没有找到视图，尝试 AI 规划
        if not plan and advisor is not None:
            try:
                plan_prompt = (
                    "你是数据库诊断助手。用户用自然语言提出了一个诊断目标，"
                    "请规划为了回答这个问题需要查询哪些系统视图/数据字典，"
                    "并给出对应的 SQL。\n"
                    f"数据库类型：{db_type}\n用户目标：{goal}\n"
                    "要求：\n"
                    "1. 返回 JSON 数组，每个元素含 view(视图名)、sql(只读SELECT)、"
                    "rationale(为什么选它)；\n"
                    "2. 优先使用数据库自带的系统视图（如 Oracle 的 DBA_TABLESPACES、"
                    "MySQL 的 INFORMATION_SCHEMA、PostgreSQL 的 pg_catalog）；\n"
                    "3. 给出 2-3 个由易到难的候选方案；\n"
                    "4. 只输出 JSON，不要多余解释。"
                )
                raw = advisor._call_llm(plan_prompt, timeout=60)
                plan = _parse_plan(raw)
            except Exception as e:
                ctx.notes.append(f"自然语言探查专员：AI 规划失败（{e}），改用规则兜底。")

        # 最后兜底到规则方案
        if not plan:
            plan = _rule_plan(db_type, goal)
            ctx.notes.append(
                f"自然语言探查专员：使用规则兜底方案，共 {len(plan)} 个候选视图。"
            )
        else:
            ctx.notes.append(
                f"自然语言探查专员：规划出 {len(plan)} 个候选视图。"
            )

        # ── 2) 逐视图尝试执行，单视图最多 5 次纠错 ──
        success_sql: Optional[str] = None
        success_view: Optional[str] = None
        success_res: Optional[Dict[str, Any]] = None

        for ci, cand in enumerate(plan, 1):
            view = cand.get("view", f"候选{ci}")
            sql = cand.get("sql", "").strip()
            if not sql:
                continue

            # ── 2.5) 视图列信息预检：执行前先查系统视图获取实际列名 ──
            # 尝试从 SQL 中提取表/视图名（简化版：取 FROM 后第一个词）
            tbl_match = re.search(r"FROM\s+([A-Za-z0-9_$#]+)", sql, re.IGNORECASE)
            tbl_name = tbl_match.group(1).strip() if tbl_match else ""
            actual_cols: List[str] = []
            if tbl_name and inst:
                try:
                    col_result = get_table_columns(inst, tbl_name)
                    if col_result.get("ok"):
                        actual_cols = [c.upper() for c in col_result.get("columns", [])]
                        if actual_cols:
                            ctx.notes.append(
                                f"  ℹ 视图[{ci}] {view} 实际列：{', '.join(actual_cols[:10])}"
                                f"{'...' if len(actual_cols) > 10 else ''}"
                            )
                            # 用实际列名校验并尝试自动修正 SQL
                            sql = _fix_sql_with_columns(sql, actual_cols, db_type)
                except Exception as e:
                    ctx.notes.append(f"  ℹ 视图[{ci}] 列信息查询失败（{e}），跳过预检")

            ctx.notes.append(f"  ▶ 尝试视图[{ci}] {view}：{sql}")

            # ── 执行前先用 EXPLAIN 验证 SQL ──
            if inst:
                try:
                    validate_result = validate_sql_with_explain(inst, sql)
                    if not validate_result.get("ok"):
                        ctx.notes.append(f"  ⚠ SQL 验证失败：{validate_result.get('error')}，跳过执行")
                        continue  # 跳过这个视图，进入下一个候选
                    elif not validate_result.get("skipped"):
                        ctx.notes.append(f"  ℹ SQL 验证通过")
                except Exception as ve:
                    ctx.notes.append(f"  ⚠ SQL 验证异常（{ve}），继续执行")

            for attempt in range(1, self.MAX_ATTEMPTS + 1):
                res = execute_instance_query(inst, sql, limit=200)
                if res.get("ok"):
                    success_sql, success_view, success_res = sql, view, res
                    ctx.notes.append(
                        f"  ✓ 视图[{ci}] 执行成功（{len(res.get('rows', []))} 行，"
                        f"{res.get('elapsed_ms', 0)}ms），共尝试 {attempt} 次。"
                    )
                    break
                err = res.get("error", "未知错误")
                ctx.notes.append(f"  ✗ 第 {attempt} 次失败：{err}")
                if attempt >= self.MAX_ATTEMPTS:
                    break
                # 让 AI 基于报错修正 SQL（仅当 AI 可用）
                if advisor is not None:
                    try:
                        fix_prompt = (
                            "下面这条 SQL 在数据库中执行报错了，请修正后只输出"
                            "修正后的 SQL（不含解释、不含 markdown 代码围栏）：\n"
                            f"数据库类型：{db_type}\n原 SQL：{sql}\n报错：{err}\n"
                        )
                        fixed = _clean_sql(advisor._call_llm(fix_prompt, timeout=60))
                        if fixed and fixed != sql:
                            sql = fixed
                            continue
                    except Exception:
                        pass
                # 无 AI 或修正无果：本视图放弃，换下一个候选
                break
            if success_res is not None:
                break

        # ── 3) 结果分析 / 结论产出 ──
        if success_res is None:
            ctx.notes.append(
                "自然语言探查专员：所有候选视图均未能取得结果，跳过本次探查。"
            )
            out.append(Finding(
                source=self.id,
                category="diagnosis",
                severity="info",
                title=f"未能回答：{goal}",
                detail="针对该目标规划的系统视图查询均执行失败，"
                       "可能是当前账号无对应数据字典权限，或该库类型暂无内置探查方案。"
                       "建议：确认账号具备系统视图查询权限，或在诊断目标中补充更具体的表/视图名。",
                suggestion="检查数据库账号权限（如 Oracle 需 SELECT ANY DICTIONARY 或具体视图授权）；"
                           "或手动在 SQL 编辑器中执行验证。",
            ))
            return out

        # 截断结果以控制 token
        cols = success_res.get("columns", [])
        rows = success_res.get("rows", [])[:30]
        result_text = f"列：{cols}\n行（前 {len(rows)} 条）：{rows}"
        if success_res.get("truncated"):
            result_text += "\n（结果已截断，仅展示前 30 行）"

        analysis = ""
        if advisor is not None:
            try:
                ana_prompt = (
                    "你是数据库诊断专家。用户提出了一个诊断目标，我们通过查询取得了结果，"
                    "请据此给出诊断结论（用中文）。\n"
                    f"数据库类型：{db_type}\n用户目标：{goal}\n"
                    f"使用的查询（视图 {success_view}）：{success_sql}\n"
                    f"查询结果：\n{result_text}\n"
                    "请输出：\n1. 直接回答用户的问题；\n"
                    "2. 对结果的解读（如有异常/风险请指出）；\n"
                    "3. 可选的后续建议。"
                )
                analysis = advisor._call_llm(ana_prompt, timeout=120).strip()
            except Exception as e:
                analysis = f"（AI 分析调用失败：{e}）\n原始结果：{result_text}"

        if not analysis:
            analysis = (
                f"已通过视图 {success_view} 取得查询结果。\n"
                f"查询：{success_sql}\n结果：{result_text}"
            )

        # 严重程度：若结果明显为空/异常给 warning，否则 info
        sev = "info"
        flat = " ".join(str(r) for r in rows).lower()
        if any(k in flat for k in ("fail", "error", "invalid", "拒绝", "失败", "异常")):
            sev = "warning"

        out.append(Finding(
            source=self.id,
            category="diagnosis",
            severity=sev,
            title=f"探查结论：{goal}",
            detail=analysis,
            suggestion="如需更深入分析，可在 SQL 编辑器中基于上述查询进一步下钻。",
            tags=["nlquery", db_type],
        ))
        ctx.notes.append("自然语言探查专员：已产出探查结论。")
        return out
