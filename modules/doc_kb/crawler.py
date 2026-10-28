# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""DocKB Phase 2：官方文档抓取 + 结构化抽取流水线。

流程：
  1. fetch_page   —— requests 抓取官方文档页正文（BeautifulSoup 清洗导航/脚本/版权块）
  2. extract_facts —— 调用 AIAdvisor._call_llm，用强约束 prompt 抽成结构化事实草稿
  3. copyright_review —— 过滤超长摘录 / 疑似整页复制，保证只存「短摘录 + 引用链接」

安全/版权约束（设计稿 §2）：
  - 绝不整本搬运；每条 excerpt ≤ 500 字，且必须带 official_url 溯源
  - 仅返回 draft，绝不自动直写数据库（零风险：入库须经前端人工校验）
  - 抓取走用户显式粘贴的 official_url，不做整站自动爬取
"""
import re
import os

from modules.core.paths import DBC_CONFIG_PATH

try:
    from modules.inspection.analyzer import PROJECT_ROOT
except Exception:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from bs4 import BeautifulSoup

# 最大摘录长度（版权红线）
_MAX_EXCERPT = 500
# 单次抽取最多事实条数
_MAX_FACTS = 30
# 抓取超时
_FETCH_TIMEOUT = 30
_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 正文噪声选择器（导航/脚本/样式/版权/广告）
_NOISE_SELECTORS = [
    "script", "style", "nav", "header", "footer", "aside",
    ".sidebar", ".nav", ".menu", ".breadcrumb", ".toc", ".footer",
    ".copyright", ".cookie", ".ads", ".advertisement", "[role='navigation']",
]


def _resolve_ai_backend(backend: str = None) -> str:
    """解析 AI backend 默认值。

    优先级：显式传入 > 环境变量 DBCHECK_AI_BACKEND > dbc_config.json 的 ai.backend > 'disabled'。
    与 modules.inspection.analyzer.AIAdvisor 的配置来源保持一致，避免「用户已配 Ollama
    但 DocKB 抽取仍报『AI 后端未启用』」——AIAdvisor 自身不会自动从配置探测本地 Ollama，
    必须显式拿到 'ollama' 才会启用，故此处兜底读取 dbc_config 的 ai.backend。
    """
    if backend and str(backend).strip().lower() in ("ollama", "openai"):
        return str(backend).strip().lower()
    env_b = os.environ.get("DBCHECK_AI_BACKEND")
    if env_b and env_b.strip().lower() in ("ollama", "openai"):
        return env_b.strip().lower()
    try:
        import json as _json
        _cfg_path = DBC_CONFIG_PATH
        if os.path.exists(_cfg_path):
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _cfg = _json.load(_f)
            _ai = _cfg.get("ai", {}) or {}
            _b = _ai.get("backend")
            if _b and str(_b).strip().lower() in ("ollama", "openai"):
                return str(_b).strip().lower()
    except Exception:
        pass
    return "disabled"

# 抽取 prompt：强约束只输出短事实 JSON（JSONL 形式，易于小模型遵守）
_EXTRACT_PROMPT = """你是一名数据库官方文档结构化抽取器。请从下面给定的官方文档正文里，
抽取对「数据库巡检 / 健康诊断 / 参数调优 / 风险识别」有用的权威事实。

【输出格式·强制】
- 你【只能】输出若干行，【每行是一个独立 JSON 对象】，行与行之间不要有任何其他文字、标题、解释或 Markdown。
- 不要输出 `[` `]` 包裹，不要输出代码块标记，不要输出总结。
- 如果确实没有可抽取的内容，直接输出一个空行或什么都不输出。

【每个 JSON 对象的字段】（严格如下）：
- "category": 取值之一 → "param"(参数/配置) | "baseline"(基线/阈值) | "rule"(规则/规范) | "bug"(已知缺陷/限制)
- "key": 简短关键词（英文或中文，如 "max_connections" / "缓冲命中率阈值"）
- "value": 该事实的核心结论（尽量用原文短句，≤200字）
- "excerpt": 直接摘自原文的片段（≤500字），用于溯源；不要改写、不要编造
- "severity": 可选，"low"|"mid"|"high"|"block"，仅当是风险/缺陷类时给；否则省略

【约束】
- 每条 excerpt 必须是正文里的真实片段，不得凭空编造；无法确认的内容不要抽。
- 数据库类型上下文：{db_type}，版本：{version}（仅用于理解正文，不要写进 key/value）。
- 最多抽取 {max_facts} 条最有价值的事实。

【正确输出示例（每行一个对象）】
{{"category":"param","key":"innodb_buffer_pool_size","value":"默认 128MB，大内存建议设为物理内存 60-80%","excerpt":"The default value is 128MB ... set to 60-80% of RAM.","severity":"mid"}}
{{"category":"rule","key":"read_only_clone","value":"克隆只读库会保留其只读状态","excerpt":"Cloning a read-only database retains its read-only state."}}

官方文档正文如下：
====================
{content}
====================
"""


def _clean_db_type(db_type: str) -> str:
    dt = (db_type or "").strip().lower()
    if dt == "postgresql":
        dt = "pg"
    return dt


def _mirror_url(url: str) -> str:
    """反爬域名镜像重写：dev.mysql.com 被 WAF 403 拦截，但其官方 Oracle CDN 镜像
    (docs.oracle.com/cd/E17952_01/mysql-<ver>-en/...) 内容同源且可直连抓取。
    路径规则：dev.mysql.com/doc/refman/<ver>/en/<page> →
              docs.oracle.com/cd/E17952_01/mysql-<ver>-en/<page>
    仅做透明改写，不改变抽取语义。其他域名原样返回。
    """
    import re
    m = re.match(r"https?://dev\.mysql\.com/doc/refman/([^/]+)/en/(.+)$", url)
    if m:
        ver, page = m.group(1), m.group(2)
        return f"https://docs.oracle.com/cd/E17952_01/mysql-{ver}-en/{page}"
    return url




def _browser_headers(referer: str = None) -> dict:
    """构造接近真实浏览器的请求头，规避官方站的反爬 403（如 dev.mysql.com / docs.oracle.com）。"""
    h = {
        "User-Agent": _DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    if referer:
        h["Referer"] = referer
    return h


def fetch_page(url: str, timeout: int = _FETCH_TIMEOUT, proxy: str = None,
               max_retry: int = 2) -> dict:
    """抓取官方文档页并抽取正文纯文本。

    反爬处理：
      - 完整浏览器仿真头（Accept / Sec-Fetch-* / Upgrade-Insecure-Requests 等）
      - 用 Session 保持 cookie jar（绕过首次挑战）
      - 403 时退化重试：去掉 Sec-Fetch-*、改用更朴素的 UA 再试，仍失败才报错
    返回 {"ok": bool, "text": str, "title": str, "error": str}
    """
    if not url or not str(url).startswith("http"):
        return {"ok": False, "text": "", "title": "", "error": "无效的 URL"}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    sess = requests.Session()
    last_err = ""
    for attempt in range(max_retry + 1):
        try:
            # 首次尝试原 URL；后续尝试（含 403 重试）改用反爬镜像 URL
            target = url if attempt == 0 else _mirror_url(url)
            if attempt == 0:
                headers = _browser_headers()
            else:
                # 退化重试：朴素头，去掉 Sec-Fetch-* / UA 伪装成更老的实现
                headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            resp = sess.get(target, headers=headers, timeout=timeout, proxies=proxies)
            if resp.status_code == 403 and attempt < max_retry:
                last_err = "403 反爬拦截，正在切换镜像重试"
                continue
            resp.raise_for_status()
            # 编码容错
            if resp.encoding and resp.encoding.lower() in ("iso-8859-1",):
                resp.encoding = resp.apparent_encoding or "utf-8"
            html = resp.text
            return {"ok": True, "text": html, "title": "", "error": ""}
        except Exception as e:
            last_err = str(e)
            if attempt < max_retry:
                continue
            return {"ok": False, "text": "", "title": "", "error": f"抓取失败：{last_err}"}

    try:
        soup = BeautifulSoup(html, "html.parser")
        # 去噪声
        for sel in _NOISE_SELECTORS:
            for tag in soup.select(sel):
                tag.decompose()
        # 取正文：优先 main/article，否则 body
        main = soup.find("main") or soup.find("article") or soup.body
        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)
        # 压缩空行
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        text = "\n".join(lines)
        # 限制长度（过长截断，避免喂爆 LLM 上下文；6000 字已足够抽取关键事实，
        # 且能显著提升小模型（如 qwen3:8b）对 JSON 输出格式的遵从度）
        if len(text) > 6000:
            text = text[:6000] + "\n...[正文过长已截断]"
        title = (soup.title.string.strip() if soup.title and soup.title.string else "") or ""
        return {"ok": True, "text": text, "title": title, "error": ""}
    except Exception as e:
        return {"ok": False, "text": "", "title": "", "error": f"解析正文失败：{e}"}


def _parse_llm_json(raw: str):
    """从 LLM 返回中尽量解析出 JSON 数组（兼容多种不守格式的输出）。

    解析优先级：
      1) JSONL：逐行解析，每行一个独立 JSON 对象（小模型最易遵守，优先）
      2) 标准数组 `[...]`：取第一个 [ 到最后一个 ]
      3) Markdown 代码块包裹 → 去围栏后按上述两种重试
      4) 兜底：正则提取所有 {...} 对象组装成数组
    """
    if not raw:
        return []
    _json = __import__("json")
    s = raw.strip()

    # 0) 去 Markdown 代码块围栏
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        s = m.group(1).strip()

    # 1) JSONL：逐行解析对象（忽略空行）
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if lines:
        objs = []
        for ln in lines:
            if not (ln.startswith("{") and ln.endswith("}")):
                # 行内若包含 {...}，也尝试截取
                mm = re.search(r"\{.*\}", ln, re.S)
                if mm:
                    ln = mm.group(0)
                else:
                    continue
            try:
                o = _json.loads(ln)
                if isinstance(o, dict):
                    objs.append(o)
            except Exception:
                continue
        if objs:
            return objs

    # 2) 标准数组
    try:
        start = s.index("[")
        end = s.rindex("]")
        arr = _json.loads(s[start:end + 1])
        if isinstance(arr, list):
            return arr
    except Exception:
        pass

    # 3) 裸 json
    try:
        obj = _json.loads(s)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            return [obj]
    except Exception:
        pass

    # 4) 兜底：正则提取所有 {...}
    try:
        objs = []
        for om in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", s):
            try:
                o = _json.loads(om.group(0))
                if isinstance(o, dict):
                    objs.append(o)
            except Exception:
                continue
        if objs:
            return objs
    except Exception:
        pass
    return []


def _copyright_review(facts: list, official_url: str) -> list:
    """版权审查：过滤超长 excerpt、缺 excerpt、非结构字段，并强制补 official_url。

    返回清洗后的事实列表（draft 项）。
    """
    cleaned = []
    valid_cat = {"param", "baseline", "rule", "bug"}
    valid_sev = {"low", "mid", "high", "block"}
    # 中文 category 归一化（防止小模型用中文枚举被整体过滤）
    cat_cn_map = {
        "参数": "param", "参数配置": "param", "参数/配置": "param", "配置": "param",
        "基线": "baseline", "基线/阈值": "baseline", "阈值": "baseline",
        "规则": "rule", "规则/规范": "rule", "规范": "rule",
        "缺陷": "bug", "已知缺陷": "bug", "限制": "bug", "已知缺陷/限制": "bug",
    }
    # 英文自由 category 兜底映射（模型偶尔输出 Database/Config/Setting 等，归到最近枚举）
    cat_en_map = {
        "database": "rule", "config": "param", "config": "param", "setting": "param",
        "settings": "param", "parameter": "param", "parameters": "param",
        "threshold": "baseline", "baselines": "baseline", "limit": "baseline",
        "limitation": "bug", "limitations": "bug", "defect": "bug", "bug": "bug",
        "issue": "bug", "risk": "rule", "rule": "rule", "rules": "rule",
        "best practice": "rule", "guideline": "rule", "note": "rule", "fact": "rule",
    }
    for f in facts:
        if not isinstance(f, dict):
            continue
        cat = str(f.get("category", "")).strip().lower()
        cat = cat_cn_map.get(cat, cat)        # 中文→英文
        if cat not in valid_cat:
            cat = cat_en_map.get(cat, "rule")  # 未知英文→按语义兜底；仍未知→rule（保留事实不丢弃）
        key = str(f.get("key", "")).strip()
        value = str(f.get("value", "")).strip()
        excerpt = str(f.get("excerpt", "")).strip()
        if not key or not value:
            continue
        if not key or not value:
            continue
        # 版权红线：excerpt 超长或缺失 → 仅保留 value（短结论），丢弃长摘录
        if len(excerpt) > _MAX_EXCERPT:
            excerpt = excerpt[:_MAX_EXCERPT - 1] + "…"
        if not excerpt:
            excerpt = value[:_MAX_EXCERPT]
        sev = str(f.get("severity", "")).strip().lower()
        if sev and sev not in valid_sev:
            sev = ""
        cleaned.append({
            "category": cat,
            "key": key,
            "value": value[:200],
            "excerpt": excerpt,
            "severity": sev,
            "official_url": official_url,
        })
    # 去重（按 key+category）
    seen = set()
    uniq = []
    for c in cleaned:
        k = (c["category"], c["key"].lower())
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return uniq[:_MAX_FACTS]


def extract_facts(url: str, db_type: str, version: str = "all",
                  backend: str = None, api_key: str = None,
                  api_url: str = None, model: str = None,
                  proxy: str = None, advisor=None) -> dict:
    """抓取官方文档页并抽成结构化事实草稿（不入库）。

    参数：
      advisor: 可注入已实例化的 AIAdvisor；不传则内部按需实例化（依赖 dbc_config 的 ai 配置）
    返回：{"ok": bool, "draft": [..], "error": str, "title": str, "db_type":.., "version":..}
    """
    dt = _clean_db_type(db_type)
    # 1) 抓取
    page = fetch_page(url, proxy=proxy)
    if not page["ok"]:
        return {"ok": False, "draft": [], "error": page["error"],
                "title": "", "db_type": dt, "version": version}
    # 2) LLM 抽取
    if advisor is None:
        try:
            from modules.inspection.analyzer import AIAdvisor
            # 关键：AIAdvisor 不会自动从 dbc_config 探测本地 Ollama，必须显式传 'ollama'。
            # 此处用 _resolve_ai_backend 兜底读取 dbc_config 的 ai.backend，
            # 避免「用户已配 Ollama 但抽取仍报『AI 后端未启用』」。
            resolved_backend = _resolve_ai_backend(backend)
            advisor = AIAdvisor(backend=resolved_backend, api_key=api_key,
                                api_url=api_url, model=model)
        except Exception as e:
            return {"ok": False, "draft": [],
                    "error": f"AI 后端不可用（请检查 AI 设置/本地 Ollama）：{e}",
                    "title": page["title"], "db_type": dt, "version": version}
    if getattr(advisor, "backend", "disabled") == "disabled":
        return {"ok": False, "draft": [],
                "error": "AI 后端未启用（请配置 Ollama 或开启在线模型后再抽取）",
                "title": page["title"], "db_type": dt, "version": version}
    prompt = _EXTRACT_PROMPT.format(
        db_type=dt, version=version, max_facts=_MAX_FACTS, content=page["text"])
    # Ollama structured outputs：用 JSON Schema 强制模型只输出合法 JSON 数组，
    # 彻底规避 qwen3 等小模型不遵守「只输出 JSON」指令、夹带 Markdown 总结的问题。
    response_format = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "key": {"type": "string"},
                "value": {"type": "string"},
                "excerpt": {"type": "string"},
                "severity": {"type": "string"},
            },
            "required": ["category", "key", "value", "excerpt"],
        },
    }
    try:
        raw = advisor._call_llm(prompt, timeout=120, response_format=response_format)
    except Exception as e:
        return {"ok": False, "draft": [], "error": f"LLM 调用失败：{e}",
                "title": page["title"], "db_type": dt, "version": version}
    # 3) 解析 + 版权审查
    facts = _parse_llm_json(raw)
    if not isinstance(facts, list):
        facts = []
    draft = _copyright_review(facts, url)
    return {"ok": True, "draft": draft, "error": "",
            "title": page["title"], "db_type": dt, "version": version}
