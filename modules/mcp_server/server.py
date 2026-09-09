# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""DBCheck MCP Server —— 协议合规的最小 stdio 实现（阶段 1：工具箱化）。

协议：JSON-RPC 2.0，行分隔（每行一条 JSON），stdin 收请求、stdout 发响应。
支持的 method：
- initialize            -> 返回 protocolVersion / capabilities / serverInfo
- notifications/initialized (通知，不回)
- ping                 -> {}
- tools/list           -> 7 个工具（来自 registry，含风险元数据 + annotations）
- tools/call           -> 按 handler_key 中央门控派发

关键约束：stdout 只能放 JSON-RPC 响应，任何 DBCheck 模块的散落 print
（如迁移日志、巡检进度）必须重定向到 stderr，否则会污染 Claude Desktop 的
协议流。做法：进入 main 后把 sys.stdout 指向 sys.stderr，协议回复写
sys.__stdout__（原始 stdout fd）。
"""

import json
import os
import sys

from modules.mcp_server.registry import get_tool_specs, get_spec, get_skill_specs

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "dbcheck-mcp"
SERVER_VERSION = "0.2.0-phase1"


def _log(msg: str) -> None:
    sys.stderr.write(f"[mcp-server] {msg}\n")
    sys.stderr.flush()


def build_tools() -> list:
    """由共用注册表生成工具清单（MCP 工具 + Skills，含风险元数据 + MCP annotations）。

    Skills 与 MCP 工具共用同一注册表（规划文档 4.3「Skills ≡ P0 MCP 工具」）：
    读类工具与 Skills 一并暴露，写类 Skill 以 annotations.destructiveHint=true 提示
    客户端，实际调用由 dispatch_tool 统一路由到 WriteGate。
    """
    tools = []
    for spec in get_tool_specs() + get_skill_specs():
        tools.append({
            "name": spec["name"],
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
            "annotations": spec.get("annotations", {}),
            # 非标准扩展字段，支持风险元数据的 Client 可读取；未知客户端忽略
            "x-risk": spec.get("risk", {}),
            "x-domain": spec.get("domain"),
            "x-tags": spec.get("tags", []),
            # tool=只读分析类（MCP 暴露）；skill=写类/破坏性（走 WriteGate）
            "x-kind": "skill" if spec["risk"]["access_mode"] == "write" else "tool",
        })
    return tools


def _resolve_principal():
    """从环境变量里的 API Key 解析调用者身份。

    返回 ``(principal, enforced)``：
    - ``DBCHECK_MCP_REQUIRE_AUTH != 1`` → ``(None, False)``，保持向后兼容的
      全量行为（本地 stdio 受信模式）；
    - 开启鉴权后 Key 必须有效**且已绑定用户**，否则直接抛 ``PermissionError``，
      由 ``handle()`` 转成 JSON-RPC 错误，避免"有 Key 但无身份"绕过隔离。
    """
    if os.environ.get("DBCHECK_MCP_REQUIRE_AUTH") != "1":
        return None, False
    from modules.mcp_server.auth import resolve_principal
    ok, principal, reason = resolve_principal(os.environ.get("DBCHECK_MCP_API_KEY", ""))
    if not ok:
        raise PermissionError(reason or "INVALID_API_KEY")
    return principal, True


def dispatch_tool(name: str, args: dict, principal=None) -> dict:
    """中央工具派发 + 风险门控（MCP 工具与 Skills 共用入口）。

    * 未知 -> unknown tool
    * 写类 / 破坏性 Skill（access_mode=write）→ 路由到 Skills 调度器的 WriteGate：
      生成 SQL 并提交 SQL 审计，返回 APPROVAL_REQUIRED + 任务号（pending_approval）；
      带审批人时自动审批并受控执行。
    * 读类工具 / Skill → 按 handler_key 调对应实现，传入 principal（驱动可见性/审计）。
    """
    spec = get_spec(name)
    if spec is None:
        return {"ok": False, "error": f"unknown tool: {name}"}
    risk = spec["risk"]
    if risk["access_mode"] == "write":
        # 写类动作一律走 WriteGate（复用 SQL 审计状态机，不在此另造审批逻辑）
        from modules.intelligence.skills import dispatch_skill
        return dispatch_skill(name, args, principal=principal)
    from modules.mcp_server.tools import HANDLERS
    handler = HANDLERS.get(spec["handler_key"])
    if handler is None:
        return {"ok": False, "error": f"handler not wired: {spec['handler_key']}"}
    try:
        return handler(principal=principal, **args)
    except TypeError as e:
        return {"ok": False, "error": f"参数错误: {e}"}


def handle(msg: dict):
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params", {}) or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "notifications/initialized":
        return None  # 通知，无需回应

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": build_tools()}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        principal, _enforced = _resolve_principal()
        try:
            res = dispatch_tool(name, args, principal)
        except PermissionError as e:
            # 鉴权失败：显式返回错误，不静默降级为"全量放行"
            _log(f"tools/call {name} denied: {e}")
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(
                        {"ok": False, "error": str(e),
                         "error_code": "MCP_AUTH_REQUIRED"},
                        ensure_ascii=True)}],
                    "isError": True,
                },
            }
        except Exception as e:
            _log(f"tools/call {name} crashed: {e}")
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(
                        {"ok": False, "error": f"tool crashed: {e}"},
                        ensure_ascii=True, default=str)}],
                    "isError": True,
                },
            }
        text = json.dumps(res, ensure_ascii=True, indent=2, default=str)
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": not res.get("ok", True),
            },
        }

    if mid is not None:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def main() -> None:
    # 把所有散落 stdout 输出（迁移日志、巡检进度等）重定向到 stderr，
    # 保护 JSON-RPC 协议流。协议回复写原始 stdout fd。
    sys.stdout = sys.stderr

    from modules.mcp_server.bootstrap import bootstrap  # noqa: E402
    bootstrap()

    # 可选鉴权闸门（stdio 本地受信场景可关闭；HTTP transport 阶段再强制）
    # 开启后要求 Key 有效**且已绑定用户**：没有身份就等于无法判定数据可见性，
    # 放行会直接击穿多租户隔离，因此启动即拒绝。
    if os.environ.get("DBCHECK_MCP_REQUIRE_AUTH") == "1":
        from modules.mcp_server.auth import resolve_principal  # noqa: E402
        ok, principal, reason = resolve_principal(
            os.environ.get("DBCHECK_MCP_API_KEY", "")
        )
        if not ok:
            _log(f"FATAL: DBCHECK_MCP_REQUIRE_AUTH=1 但身份解析失败: {reason}")
            sys.exit(2)
        _log(f"auth ok, user={principal.username} "
             f"(tenant={principal.tenant_id}, dept={principal.department_id})")

    # 关键修复：直接写 UTF-8 字节到原始 stdout 的二进制缓冲，绕过 TextIOWrapper
    # 编码。子进程在非控制台(管道)环境下 sys.__stdout__ 默认编码可能是 cp936/mbcs，
    # 写原始中文字符会被替换成 '?' 导致客户端乱码（如 ����）。
    out = sys.__stdout__.buffer
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _log(f"{SERVER_NAME} {SERVER_VERSION} stdio server started")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        try:
            resp = handle(msg)
        except Exception as e:
            _log(f"handle error: {e}")
            resp = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": str(e)},
            }
        if resp is not None:
            out.write(
                (json.dumps(resp, ensure_ascii=True, default=str) + "\n").encode("utf-8")
            )
            out.flush()
