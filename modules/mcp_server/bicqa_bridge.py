# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""BIC-QA 桥接客户端（阶段 0：知识库外部大脑协议接入）。

设计边界（严守许可红线 + 数据外发合规）
-----------------------------------------
BIC-QA 是第三方 SaaS 知识问答服务（注册 https://www.bic-qa.com 得 API Key，
新户赠 1 亿 Token）。DBCheck 社区版 Apache-2.0，**绝不嵌入 BIC-QA 任何代码、
绝不硬编码 Key**；本模块仅作为「协议层 HTTP 客户端」，按官方契约
``POST https://api.bic-qa.com/skills/qa`` 调用其知识问答接口，把返回的知识
结论回灌给本地诊断流程。

「复用 MCP 通道」含义
---------------------
把 BIC-QA 注册进 DBCheck 既有 ``registry.py``（单一事实来源），使其成为 MCP
工具箱一员，自动流过 build_tools / dispatch_tool 中央门控 / WebUI / 多 Agent。
连接管理、知识库维护、模型调用全归 BIC-QA 负责，DBCheck 只做协议调用 +
可见性/审计留痕。本工具只读（不触达生产库），与本地 11 专家互补：本地专家
做现场数据分析，BIC-QA 提供知识库支撑。

依赖：纯标准库（urllib + json），不引入任何第三方 HTTP 包，避免污染运行环境。

配置（隔离存储，绝不提交）
-------------------------
* ``BICQA_API_KEY``  : BIC-QA API Key（优先读环境变量；或落地 dbc_config.json 的
                       ``bicqa.api_key`` 字段，该文件已被 .gitignore 忽略，不进版本库）
* ``BICQA_API_BASE`` : 接口地址（可选，默认 https://api.bic-qa.com）
未配置 API Key → get_bridge() 抛 BICQAUnavailable（通道优雅降级，不击穿 MCP 协议流）。
"""

from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


DEFAULT_API_BASE = "https://api.bic-qa.com"
QA_PATH = "/skills/qa"

# 超时（秒）：外部 SaaS 不可达时快速失败，不阻塞主流程
DEFAULT_TIMEOUT = 20


class BICQAError(Exception):
    """桥接层通用错误；携带 error_code 供上层映射。"""

    error_code = "BICQA_ERROR"


class BICQAUnavailable(BICQAError):
    """BIC-QA 未配置或不可达：通道应优雅降级，绝不击穿 MCP 协议流。"""

    error_code = "BICQA_UNAVAILABLE"


# ── 配置加载（隔离存储，优先 env，回落 dbc_config.json） ─────────────────────────
def _load_config() -> Dict[str, str]:
    """返回 {api_base, api_key}；api_key 缺失则抛出 BICQAUnavailable。"""
    api_base = os.environ.get("BICQA_API_BASE") or DEFAULT_API_BASE
    api_key = os.environ.get("BICQA_API_KEY") or ""
    if not api_key:
        # 回落 dbc_config.json 的 bicqa 字段（该文件已被 .gitignore 忽略，绝不进版本库）
        try:
            # bicqa_bridge.py -> modules/mcp_server -> modules -> DBCheck
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            cfg_path = os.path.join(root, "dbc_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f).get("bicqa", {}) or {}
                api_base = cfg.get("api_base") or api_base
                api_key = cfg.get("api_key") or ""
        except Exception:
            pass
    if not api_key:
        raise BICQAUnavailable(
            "未配置 BIC-QA API Key（环境变量 BICQA_API_KEY 或 dbc_config.json 的 "
            "bicqa.api_key）；BIC-QA 知识问答不可用，通道降级（不击穿）"
        )
    return {"api_base": api_base.rstrip("/"), "api_key": api_key}


# ── 敏感信息脱敏（数据外发合规） ─────────────────────────────────────────────────
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CONN_RE = re.compile(r"(jdbc:[a-z0-9:]+://[^\s'\"]+)", re.I)
_PWD_RE = re.compile(r"(?i)(password|pwd|passwd)\s*[=:]\s*['\"]?[^\s'\"]+")


def mask_sensitive(text: str) -> str:
    """尽力脱敏后再外发：IP、邮箱、JDBC 连接串、显式口令。

    这是尽力而为的最佳实践脱敏，无法覆盖所有形态；用户仍应避免在问题文本中
    粘贴真实口令/Token。脱敏默认开启，仅当确有需要时由调用方显式关闭。
    """
    if not text:
        return text
    t = _PWD_RE.sub(r"\1=<REDACTED>", text)
    t = _CONN_RE.sub("<JDBC_URL>", t)
    t = _EMAIL_RE.sub("<EMAIL>", t)
    t = _IP_RE.sub("<IP>", t)
    return t


# ── 业务桥接 ───────────────────────────────────────────────────────────────────
class BICQABridge:
    """BIC-QA 能力桥：把外部知识问答包装为 DBCheck 友好的 Python 方法。"""

    def __init__(self, api_base: str, api_key: str):
        self._api_base = api_base
        self._api_key = api_key

    def ask(self, question: str, dbtype: str = "", mask: bool = True,
            timeout: int = DEFAULT_TIMEOUT) -> str:
        """调用 /skills/qa 返回知识结论文本（result 字段）。"""
        q = str(question).strip()
        if not q:
            raise BICQAError("question 不能为空")
        if mask:
            q = mask_sensitive(q)
        payload = {"question": q}
        if dbtype:
            payload["dbtype"] = str(dbtype).strip()
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = self._api_base + QA_PATH
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise BICQAError(f"BIC-QA HTTP {e.code}: {body}") from e
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            raise BICQAUnavailable(f"BIC-QA 不可达（{url}）：{e}") from e
        try:
            obj = json.loads(raw)
        except Exception:
            # 非 JSON 也尽量回传原文，避免信息丢失
            return raw
        # 优先取 result；兼容 {data:{result}} 等形态
        if isinstance(obj, dict):
            if "result" in obj:
                return obj["result"]
            if isinstance(obj.get("data"), dict) and "result" in obj["data"]:
                return obj["data"]["result"]
        return raw


# ── 单例工厂（读配置，未配置即不可用） ─────────────────────────────────────────
_BRIDGE: Optional[BICQABridge] = None


def get_bridge() -> BICQABridge:
    """返回进程内复用的桥接单例；BIC-QA 未配置/不可用时抛 BICQAUnavailable。"""
    global _BRIDGE
    if _BRIDGE is not None:
        return _BRIDGE
    cfg = _load_config()
    _BRIDGE = BICQABridge(cfg["api_base"], cfg["api_key"])
    return _BRIDGE


def reset_bridge() -> None:
    """测试/重载时清空单例缓存。"""
    global _BRIDGE
    _BRIDGE = None
