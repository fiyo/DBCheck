# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""智能诊断中心共享的 AI 配置加载与 JSON 解析辅助。

供协调员（planner）与自然语言探查专员（nl_query_expert）复用，
避免各专家重复实现同一套 AIAdvisor 构造逻辑。
"""

from __future__ import annotations

import json as _json
import os
import re
from typing import Any, Optional

from .context import SharedContext, Finding


def build_advisor():
    """构造 AIAdvisor，配置解析与 run_ai_diagnosis 完全一致。

    在线开启（online_enabled=true）→ 使用 online_backend / online_api_url / online_model；
    否则回落到本地 backend（ollama）/ api_url / model。
    未配置或不可用返回 None。
    """
    try:
        from modules.inspection.analyzer import AIAdvisor

        # ai_helper.py -> modules/intelligence -> modules -> DBCheck
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cfg_path = os.path.join(root, "dbc_config.json")
        backend = os.environ.get("DBCHECK_AI_BACKEND", "")
        api_url = os.environ.get("DBCHECK_AI_URL", "")
        model = os.environ.get("DBCHECK_AI_MODEL", "")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = _json.load(f).get("ai", {})
                online_on = bool(cfg.get("online_enabled", False))
                backend = backend or (
                    (cfg.get("online_backend", "openai") or "openai")
                    if online_on
                    else (cfg.get("backend", "ollama") or "ollama")
                )
                api_url = api_url or (
                    cfg.get("online_api_url") if online_on else cfg.get("api_url", "")
                ) or cfg.get("api_url", "")
                model = model or (
                    cfg.get("online_model") if online_on else cfg.get("model", "")
                ) or cfg.get("model", "")
            except Exception:
                pass
        backend = backend or "ollama"
        adv = AIAdvisor(backend=backend, api_url=api_url, model=model)
        return adv if adv.enabled else None
    except Exception:
        return None


def safe_json(text: str, default: Any = None) -> Any:
    """从 LLM 返回文本中稳健地提取 JSON（对象或数组）。

    依次尝试：整体解析 → 剥离 ```json 围栏 → 截取首个 {…} / […] 子串解析。
    均失败返回 default。
    """
    if not text:
        return default
    try:
        return _json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    blob = m.group(1) if m else text
    for a, b in (("{", "}"), ("[", "]")):
        s = blob.find(a)
        e = blob.rfind(b)
        if s != -1 and e != -1 and e > s:
            try:
                return _json.loads(blob[s:e + 1])
            except Exception:
                pass
    return default
