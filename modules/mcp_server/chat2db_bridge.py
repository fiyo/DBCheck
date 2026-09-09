# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""Chat2DB MCP 桥接客户端（阶段 3 编排与生态：Chat2DB 并入复用 MCP 通道）。

设计边界（严守许可红线）
--------------------------
Chat2DB 社区版 5.3.0+ 采用 **source-available 许可**（非 Apache-2.0）。DBCheck
社区版是 Apache 2.0，按项目红线「严格规避 GPL 及潜在侵权、零代码移植」，本模块
**绝不嵌入/移植 Chat2DB 任何源代码**。它只作为一个**协议层 MCP Client**，按标准
MCP（JSON-RPC 2.0 行协议）连接「用户自行部署的 Chat2DB MCP Server」，调用其
暴露的 ``text2sql`` / ``list_all_datasources`` 等工具。

「复用 MCP 通道」含义
---------------------
把 Chat2DB 的能力注册进 DBCheck 既有 ``registry.py``（单一事实来源），使其成为
DBCheck MCP 工具箱的一员，自动流过 ``build_tools`` / ``dispatch_tool`` 中央门控 /
WebUI / 多 Agent。连接管理、表结构上下文、模型调用全归 Chat2DB 负责，DBCheck 只做
协议桥接 + 可见性/审计留痕。**执行侧不重复**——桥接产出的 SQL 交由 DBCheck 既有
写类 Skill ``dbcheck.execute_sql``（WriteGate 审批）落地，治理不破。

依赖：纯标准库（subprocess + json），不引入任何第三方 MCP 包，避免污染运行环境。

配置（环境变量）
---------------
* ``CHAT2DB_MCP_CMD``   : Chat2DB MCP Server 的启动命令（空格分隔，将被 shlex 切分），
                          例如 ``chat2db-mcp`` 或 ``java -jar chat2db-mcp.jar``。
                          为空 → get_bridge() 抛 Chat2DBUnavailable（通道优雅降级）。
* ``CHAT2DB_MCP_URL``   : （预留）HTTP/SSE 传输端点；当前实现以 stdio 为主，留作扩展。

transport 可注入：单元测可用内存态 ``Transport`` 替换 StdioTransport，无需启动 Java。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from typing import Any, Dict, Optional


PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "dbcheck-bridge"
CLIENT_VERSION = "0.1.0"


class Chat2DBError(Exception):
    """桥接层通用错误；携带 error_code 供上层映射。"""

    error_code = "CHAT2DB_ERROR"


class Chat2DBUnavailable(Chat2DBError):
    """Chat2DB 未配置或不可达：通道应优雅降级，绝不击穿 MCP 协议流。"""

    error_code = "CHAT2DB_UNAVAILABLE"


# ── Transport 抽象（便于测试注入内存实现） ──────────────────────────────────────
class Transport:
    """MCP 传输层最小契约：write 一帧 JSON-RPC，read 一帧 JSON-RPC。"""

    def write(self, obj: Dict[str, Any]) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    def read(self) -> Dict[str, Any]:  # pragma: no cover - 接口
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - 接口
        raise NotImplementedError


class StdioTransport(Transport):
    """基于子进程的 stdio 传输：每行一条 JSON-RPC。"""

    def __init__(self, cmd: str):
        try:
            self._p = subprocess.Popen(
                shlex.split(cmd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                encoding="utf-8",
            )
        except Exception as e:  # 起不来即视为不可用
            raise Chat2DBUnavailable(f"无法启动 Chat2DB MCP 进程（命令={cmd!r}）：{e}")

    def write(self, obj: Dict[str, Any]) -> None:
        if self._p.stdin is None or self._p.poll() is not None:
            raise Chat2DBUnavailable("Chat2DB MCP 进程已退出")
        self._p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._p.stdin.flush()

    def read(self) -> Dict[str, Any]:
        if self._p.stdout is None:
            raise Chat2DBUnavailable("Chat2DB MCP 无 stdout")
        while True:
            line = self._p.stdout.readline()
            if not line:
                raise Chat2DBUnavailable("Chat2DB MCP 进程已退出（stdout 关闭）")
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except Exception:
                # 忽略非 JSON 行（如启动日志），继续读下一帧
                continue

    def close(self) -> None:
        try:
            if self._p.stdin is not None:
                self._p.stdin.close()
            if self._p.poll() is None:
                self._p.terminate()
        except Exception:
            pass


# ── MCP JSON-RPC 客户端（握手 + tools/call） ───────────────────────────────────
class McpClient:
    """最小 MCP 客户端：initialize 握手 + tools/call。"""

    def __init__(self, transport: Transport):
        self._t = transport
        self._id = 0
        self._handshake()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _handshake(self) -> None:
        mid = self._next_id()
        self._t.write({
            "jsonrpc": "2.0",
            "id": mid,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        })
        self._read_response(mid)  # 消费 initialize 响应
        # 发送 initialized 通知（无 id，服务端不回）
        self._t.write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read_response(self, mid: int) -> Dict[str, Any]:
        """读到 id==mid 的响应；跳过无 id 的通知帧。"""
        while True:
            msg = self._t.read()
            if msg.get("id") == mid:
                if "error" in msg:
                    err = msg["error"]
                    raise Chat2DBError(
                        f"MCP error {err.get('code')}: {err.get('message')}"
                    )
                return msg.get("result", {}) or {}
            # 无 id 视为通知，忽略继续读

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """调用一个 MCP 工具，返回其 content 拼接文本。"""
        mid = self._next_id()
        self._t.write({
            "jsonrpc": "2.0",
            "id": mid,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        res = self._read_response(mid)
        content = res.get("content") or []
        parts = [c.get("text", "") for c in content if isinstance(c, dict)
                 and c.get("type") == "text"]
        return "".join(parts)


# ── 业务桥接 ───────────────────────────────────────────────────────────────────
def _extract_sql(raw: str) -> str:
    """从 Chat2DB text2sql 回文里抽取 SQL：优先 ```sql 围栏，其次任意围栏，否则原文。"""
    if not raw:
        return ""
    m = re.search(r"```sql\s*(.*?)```", raw, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\s*(.*?)```", raw, re.S)
    if m:
        return m.group(1).strip()
    return raw.strip()


class Chat2DBBridge:
    """Chat2DB 能力桥：把上游 MCP 工具包装为 DBCheck 友好的 Python 方法。"""

    def __init__(self, client: McpClient):
        self._c = client

    def text2sql(self, question: str, datasource_id: str) -> str:
        """自然语言 → SQL。参数名兼容 Chat2DB text2sql（dataSourceId）。"""
        raw = self._c.call_tool(
            "text2sql",
            {"question": question, "dataSourceId": datasource_id},
        )
        return _extract_sql(raw)

    def list_datasources(self) -> str:
        return self._c.call_tool("list_all_datasources", {})

    def get_tables_schema(self, datasource_id: str, table: Optional[str] = None) -> str:
        args: Dict[str, Any] = {"dataSourceId": datasource_id}
        if table:
            args["table"] = table
        return self._c.call_tool("get_tables_schema", args)


# ── 单例工厂（读配置，未配置即不可用） ─────────────────────────────────────────
_BRIDGE: Optional[Chat2DBBridge] = None


def get_bridge() -> Chat2DBBridge:
    """返回进程内复用的桥接单例；Chat2DB 未配置/不可用时抛 Chat2DBUnavailable。"""
    global _BRIDGE
    if _BRIDGE is not None:
        return _BRIDGE
    cmd = os.environ.get("CHAT2DB_MCP_CMD")
    if not cmd:
        raise Chat2DBUnavailable(
            "未配置 CHAT2DB_MCP_CMD（Chat2DB MCP Server 启动命令）；"
            "桥接不可用，通道降级（不击穿）"
        )
    try:
        _BRIDGE = Chat2DBBridge(McpClient(StdioTransport(cmd)))
    except Chat2DBUnavailable:
        raise
    except Exception as e:
        raise Chat2DBUnavailable(f"Chat2DB MCP 连接失败: {e}")
    return _BRIDGE


def reset_bridge() -> None:
    """测试/重载时清空单例缓存。"""
    global _BRIDGE
    _BRIDGE = None
