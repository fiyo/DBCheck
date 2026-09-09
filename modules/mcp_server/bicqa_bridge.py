# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""BIC-QA 桥接客户端（阶段 0：知识库外部大脑协议接入）。

设计边界（严守许可红线 + 数据外发合规）
-----------------------------------------
BIC-QA 是第三方 SaaS 知识问答服务（注册 https://www.bic-qa.com 得 API Key，
新户赠 1 亿 Token）。DBCheck 社区版 Apache-2.0，**绝不嵌入 BIC-QA 任何代码、
绝不硬编码 Key**；本模块仅作为「协议层 HTTP 客户端」，按官方 Open API
（文档 https://api.bic-qa.com/bic-qa-html/apikey-guide.zhcn.html）调用其知识
问答接口，把返回的知识结论回灌给本地诊断流程。

「复用 MCP 通道」含义
---------------------
把 BIC-QA 注册进 DBCheck 既有 ``registry.py``（单一事实来源），使其成为 MCP
工具箱一员，自动流过 build_tools / dispatch_tool 中央门控 / WebUI / 多 Agent。
连接管理、知识库维护、模型调用全归 BIC-QA 负责，DBCheck 只做协议调用 +
可见性/审计留痕。本工具只读（不触达生产库），与本地 11 专家互补：本地专家
做现场数据分析，BIC-QA 提供知识库支撑。

官方 Open API 契约（v1.1，2026-08-25）
-------------------------------------
* 基础地址：``https://api.bic-qa.com``
* 接口前缀：``/open-api/v1``（注意：不是 ``/skills/qa``）
* 认证：``Authorization: Bearer sk-xxxxxxxx``（Key 仅存 gitignored
  ``dbc_config.json`` 的 ``bicqa.api_key``，绝不进版本库）
* 调用顺序：``POST /open-api/v1/session/create`` 取得 ``conversationId``
  → ``POST /open-api/v1/chat``（SSE 流式）按 ``delta.content`` 累积回答
* 流式响应为标准 SSE：``meta`` / ``delta`` / ``file`` / ``error`` / ``done``
  五类事件，回答文本在 ``delta.delta.content`` 中增量下发。

依赖：纯标准库（urllib + json），不引入任何第三方 HTTP 包，避免污染运行环境。
"""

from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_API_BASE = "https://api.bic-qa.com"
SESSION_CREATE_PATH = "/open-api/v1/session/create"
CHAT_PATH = "/open-api/v1/chat"

# 超时（秒）：外部 SaaS 不可达时快速失败，不阻塞主流程
DEFAULT_TIMEOUT = 60
# 单次对话最大等待（秒）：SSE 流式可能较慢，给足余量
STREAM_TIMEOUT = 120


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
    """BIC-QA 能力桥：把外部知识问答包装为 DBCheck 友好的 Python 方法。

    真实调用顺序（官方 Open API v1）：
      1. POST /open-api/v1/session/create  -> data.conversationId
      2. POST /open-api/v1/chat (SSE)      -> 累积 delta.delta.content
    """

    def __init__(self, api_base: str, api_key: str):
        self._api_base = api_base
        self._api_key = api_key

    # ── 底层 HTTP ──────────────────────────────────────────────────────────
    def _auth_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    def _post_json(self, path: str, payload: Dict[str, Any],
                   timeout: int = DEFAULT_TIMEOUT) -> Tuple[int, bytes]:
        """普通 JSON POST，返回 (status, body_bytes)。"""
        url = self._api_base + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers=self._auth_headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.getcode(), resp.read()
        except urllib.error.HTTPError as e:  # 4xx/5xx：先读错误体再抛出
            body = b""
            try:
                body = e.read() or b""
            except Exception:
                pass
            raise BICQAError(
                f"BIC-QA HTTP {e.code}: {body.decode('utf-8', 'replace')[:300]}"
            ) from e
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            raise BICQAUnavailable(f"BIC-QA 不可达（{url}）：{e}") from e

    # ── 步骤 1：创建会话 ────────────────────────────────────────────────────
    def _create_session(self, title: str = "DBCheck 知识检索",
                        timeout: int = DEFAULT_TIMEOUT) -> str:
        status, body = self._post_json(
            SESSION_CREATE_PATH, {"title": title}, timeout=timeout
        )
        try:
            obj = json.loads(body.decode("utf-8", "replace"))
        except Exception as e:
            raise BICQAError(f"BIC-QA 创建会话返回非 JSON：{body.decode('utf-8','replace')[:200]}") from e
        if obj.get("status") != "success" or not isinstance(obj.get("data"), dict):
            msg = obj.get("message") or obj.get("code") or "未知错误"
            raise BICQAError(f"BIC-QA 创建会话失败：{msg}")
        cid = obj["data"].get("conversationId")
        if not cid:
            raise BICQAError("BIC-QA 创建会话未返回 conversationId")
        return cid

    # ── 步骤 2：流式聊天（SSE） ─────────────────────────────────────────────
    def _chat_stream(self, question: str, conversation_id: str,
                     timeout: int = STREAM_TIMEOUT) -> str:
        url = self._api_base + CHAT_PATH
        payload = {"question": question, "conversationId": conversation_id}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers=self._auth_headers({
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
            }),
            method="POST",
        )
        parts: List[str] = []  # 累积的回答文本
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if "text/event-stream" not in ctype:
                    # 非 SSE：可能是建流前的 JSON 错误信封
                    err_body = resp.read().decode("utf-8", "replace")[:300]
                    raise BICQAError(f"BIC-QA 聊天未返回 SSE 流：{err_body}")
                self._consume_sse(resp, parts)
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read() or b""
            except Exception:
                pass
            raise BICQAError(
                f"BIC-QA HTTP {e.code}: {body.decode('utf-8', 'replace')[:300]}"
            ) from e
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            raise BICQAUnavailable(f"BIC-QA 不可达（{url}）：{e}") from e
        return "".join(parts).strip()

    @staticmethod
    def _consume_sse(resp, parts: List[str]) -> None:
        """逐行解析 SSE；把 delta.delta.content 累加到 parts。

        事件以空行分隔；每个事件可能含多行 ``data:``，拼接后整体解析为一个 JSON。
        容忍 delta.thinking 与未知字段；遇到 type=error 直接抛出。
        """
        data_lines: List[str] = []
        for raw in resp:
            line = raw.decode("utf-8", "replace")
            if line in ("\n", "\r\n", ""):
                # 事件结束：处理已收集的 data 行
                if data_lines:
                    BICQABridge._dispatch_event(data_lines, parts)
                    data_lines = []
                continue
            if line.startswith("data:"):
                payload = line[5:]
                if payload.startswith(" "):
                    payload = payload[1:]
                data_lines.append(payload)
            # 其它字段（event:/id:/retry:）忽略
        # 连接关闭时 flush 残余
        if data_lines:
            BICQABridge._dispatch_event(data_lines, parts)

    @staticmethod
    def _dispatch_event(data_lines: List[str], parts: List[str]) -> None:
        blob = "\n".join(data_lines).strip()
        if not blob:
            return
        try:
            evt = json.loads(blob)
        except Exception:
            return  # 非 JSON 片段，忽略
        if not isinstance(evt, dict):
            return
        etype = evt.get("type")
        if etype == "error":
            content = evt.get("content") or evt.get("message") or "未知错误"
            code = evt.get("errorCode") or ""
            raise BICQAError(f"BIC-QA 流式错误（{code}）：{content}")
        if etype == "delta":
            delta = evt.get("delta") or {}
            if isinstance(delta, dict):
                # 回答文本
                if isinstance(delta.get("content"), str):
                    parts.append(delta["content"])
                # 思考过程（不计入最终回答，但可作为调试；此处不附加）
        # meta / file / done 等事件忽略

    # ── 对外主方法 ─────────────────────────────────────────────────────────
    def ask(self, question: str, dbtype: str = "", mask: bool = True,
            timeout: int = STREAM_TIMEOUT) -> str:
        """向 BIC-QA 提问，返回知识结论文本。

        流程：创建会话 -> 流式聊天 -> 累积 delta.content。
        ``dbtype`` 官方 API 无原生参数，这里作为上下文前缀拼进问题，
        让模型按数据库类型给出更贴切的知识；``mask`` 控制外发前脱敏。
        """
        q = str(question).strip()
        if not q:
            raise BICQAError("question 不能为空")
        if mask:
            q = mask_sensitive(q)
        if dbtype:
            q = f"[数据库类型: {str(dbtype).strip()}]\n{q}"
        cid = self._create_session()
        return self._chat_stream(q, cid, timeout=timeout)


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
