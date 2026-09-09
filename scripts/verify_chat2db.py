# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck
"""阶段 3（编排与生态）：Chat2DB 并入复用 MCP 通道 —— 验收脚本。

纯逻辑（主进程，不 import web.app）：
  R1 registry 含 dbcheck.nl2sql（单一事实来源，自动流经 MCP 通道）
  R2 nl2sql_tool 在 Chat2DB 未配置时优雅降级（error_code=CHAT2DB_UNAVAILABLE）
  R3 桥接 JSON-RPC 客户端对内存 transport 握手 + tools/call 解析 + SQL 抽取
  R4 server.dispatch_tool 把读类工具路由到 handler（中央门控不改一行即生效）

live 子进程（import web.app + 三步验证）：
  L1 import web.app 无错
  L2 get_tool_specs / build_tools 含 dbcheck.nl2sql（MCP 工具箱可见）
  L3 discover_plugins() == 11（三步验证之一）
  L4 dispatch_tool 对未配置 Chat2DB 返回优雅降级（通道不击穿）
"""
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"  | {detail}" if detail else ""))


class _FakeTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self.written = []

    def write(self, obj):
        self.written.append(obj)

    def read(self):
        if not self._responses:
            raise RuntimeError("no more frames")
        return self._responses.pop(0)

    def close(self):
        pass


# ───────────────────────── 纯逻辑 ─────────────────────────
def run_logic():
    from modules.mcp_server.registry import get_tool_specs
    from modules.mcp_server.tools import HANDLERS, nl2sql_tool
    from modules.mcp_server.chat2db_bridge import (
        Chat2DBBridge, Chat2DBUnavailable, McpClient, reset_bridge,
    )

    # R1 注册表含 nl2sql
    specs = get_tool_specs()
    names = [s["name"] for s in specs]
    check("R1 registry 含 dbcheck.nl2sql", "dbcheck.nl2sql" in names)
    spec = next(s for s in specs if s["name"] == "dbcheck.nl2sql")
    check("R1 nl2sql 为读类工具", spec["risk"]["access_mode"] == "read")
    check("R1 handler 已接线", HANDLERS.get("nl2sql") is nl2sql_tool)

    # R2 未配置 Chat2DB → 优雅降级
    os.environ.pop("CHAT2DB_MCP_CMD", None)
    os.environ.pop("CHAT2DB_DATASOURCE_MAP", None)
    reset_bridge()
    res = nl2sql_tool(question="查未支付订单", datasource_id="ds_demo")
    check("R2 未配置时降级 CHAT2DB_UNAVAILABLE",
          (not res["ok"]) and res.get("error_code") == "CHAT2DB_UNAVAILABLE",
          res.get("error_code", ""))

    # R3 桥接 JSON-RPC 客户端（内存 transport）
    init = {"jsonrpc": "2.0", "id": 1, "result": {
        "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
        "serverInfo": {"name": "chat2db-mcp", "version": "5.3.0"}}}
    call = {"jsonrpc": "2.0", "id": 2, "result": {
        "content": [{"type": "text",
                     "text": "生成 SQL：\n```sql\nSELECT * FROM orders WHERE paid=0;\n```"}]}}
    t = _FakeTransport([init, call])
    c = McpClient(t)
    methods = [f.get("method") for f in t.written]
    check("R3 握手发 initialize + initialized",
          "initialize" in methods and "notifications/initialized" in methods)
    sql = Chat2DBBridge(c).text2sql("查未支付订单", "ds_demo")
    check("R3 text2sql 抽取 SQL", "SELECT * FROM orders" in sql, sql)
    # 异常帧不静默：工具名未知由上游报错，这里仅验证客户端能抛 Chat2DBError
    bad = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "boom"}}
    t2 = _FakeTransport([init, bad])
    c2 = McpClient(t2)
    try:
        c2.call_tool("text2sql", {"question": "x", "dataSourceId": "d"})
        check("R3 上游错误上抛 Chat2DBError", False)
    except Chat2DBUnavailable:
        check("R3 上游错误上抛 Chat2DBError", False)  # 非 unavailable
    except Exception as e:
        check("R3 上游错误上抛 Chat2DBError", "Chat2DBError" in type(e).__name__,
              type(e).__name__)

    # R4 dispatch_tool 路由读类工具到 handler（中央门控零改动生效）
    from modules.mcp_server import server
    r4 = server.dispatch_tool("dbcheck.nl2sql",
                              {"question": "q", "datasource_id": "ds_demo"})
    check("R4 dispatch_tool 路由到 handler 并优雅降级",
          isinstance(r4, dict) and r4.get("error_code") == "CHAT2DB_UNAVAILABLE")


# ───────────────────────── live 子进程 ─────────────────────────
def run_live():
    os.environ.pop("CHAT2DB_MCP_CMD", None)
    os.environ.pop("CHAT2DB_DATASOURCE_MAP", None)
    print("[live] importing web.app ...", flush=True)
    import modules.web.app as appmod
    from modules.pluginkit.loader import discover_plugins
    from modules.mcp_server.registry import get_tool_specs
    from modules.mcp_server import server
    print("[live] web.app imported", flush=True)

    # L2 工具箱可见
    names = [s["name"] for s in get_tool_specs()]
    tool_names = [t["name"] for t in server.build_tools()]
    check("L2 get_tool_specs 含 nl2sql", "dbcheck.nl2sql" in names)
    check("L2 build_tools 含 nl2sql", "dbcheck.nl2sql" in tool_names)

    # L3 三步验证之一
    check("L3 discover_plugins()==11", len(discover_plugins()) == 11,
          str(len(discover_plugins())))

    # L4 中央门控对未配置 Chat2DB 优雅降级
    r = server.dispatch_tool("dbcheck.nl2sql",
                             {"question": "q", "datasource_id": "ds_demo"})
    check("L4 dispatch_tool 优雅降级（不击穿）",
          isinstance(r, dict) and r.get("error_code") == "CHAT2DB_UNAVAILABLE")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "live":
        run_live()
    else:
        print("=== 阶段3 Chat2DB 验收：纯逻辑 ===")
        run_logic()
        if os.environ.get("PHASED_SKIP_LIVE"):
            print("(已跳过 live 子进程：PHASED_SKIP_LIVE=1)")
            sys.exit(0)
        print("=== 阶段3 Chat2DB 验收：live 子进程（import web.app + 三步验证） ===")
        env = dict(os.environ)
        env.pop("CHAT2DB_MCP_CMD", None)
        env.pop("CHAT2DB_DATASOURCE_MAP", None)
        code = subprocess.run([sys.executable, __file__, "live"],
                              cwd=ROOT, env=env, capture_output=True, text=True)
        out = code.stdout
        print(out)
        if code.returncode != 0:
            print("LIVE STDERR:\n" + code.stderr)
        live_fail = any(("❌" in ln) for ln in out.splitlines())
        fails = [r for r in RESULTS if not r[1]]
        print(f"\n汇总：主进程 {len(RESULTS)} 项，live 子进程见上；"
              f"失败合计 {len(fails) + (1 if live_fail else 0)} 项")
        sys.exit(1 if (fails or live_fail or code.returncode != 0) else 0)
