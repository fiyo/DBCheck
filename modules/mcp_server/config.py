# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""MCP Server 生产参数集中配置（零依赖、可降级）。

设计目标：把原先散落在环境变量里的 ``DBCHECK_MCP_*`` 开关，集中到一个
**非敏感** 配置文件 ``mcp_server_config.json``（可入库，不含任何密钥），
同时保留环境变量作为最高优先级覆盖（兼容旧部署）。

优先级（高 → 低）：
    1. 环境变量 ``DBCHECK_MCP_*``（临时覆盖，无需改文件）
    2. 项目根 ``mcp_server_config.json``（推荐，可随仓库分发）
    3. ``dbc_config.json`` 的 ``mcp_server`` 节点（兜底，密钥同源文件）

PROJECT_ROOT 基于本模块文件位置推算，不依赖进程 CWD —— MCP Server 常作为
子进程由 Claude Desktop / Codex 拉起，CWD 未必是仓库根。
"""
import json
import os

# modules/mcp_server/config.py → 往上三级即仓库根（config.py → mcp_server → modules → 根）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEDICATED_CFG = os.path.join(PROJECT_ROOT, "mcp_server_config.json")
DBCONFIG_PATH = os.path.join(PROJECT_ROOT, "dbc_config.json")

# 默认值（pool 默认关闭，行为与历史完全一致；生产环境按需开启）
_DEFAULTS = {
    "subproc_pool_enabled": False,  # 分析子进程连接池（opt-in）
    "pool_min": 1,                  # 预热常驻 worker 数
    "pool_max": 4,                  # 最大 worker 数
    "tool_timeout": 600,            # 单次工具调用墙钟超时（秒）
    "otel_enabled": False,          # OpenTelemetry 指标导出
}


def _read_json(path):
    try:
        if not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def load_mcp_config():
    """合并三层来源，返回完整配置 dict（所有键均有默认值兜底）。"""
    cfg = dict(_DEFAULTS)

    # 3) dbc_config.json 的 mcp_server 节点（兜底）
    dc = _read_json(DBCONFIG_PATH).get("mcp_server") or {}
    if isinstance(dc, dict):
        for k in _DEFAULTS:
            if k in dc and dc[k] is not None:
                cfg[k] = dc[k]

    # 2) 专用 mcp_server_config.json（推荐，可入库）
    fcfg = _read_json(DEDICATED_CFG)
    if isinstance(fcfg, dict):
        for k in _DEFAULTS:
            if k in fcfg and fcfg[k] is not None:
                cfg[k] = fcfg[k]

    # 1) 环境变量（最高优先级，兼容旧部署）
    env_map = {
        "subproc_pool_enabled": "DBCHECK_MCP_SUBPROC_POOL",
        "pool_min": "DBCHECK_MCP_POOL_MIN",
        "pool_max": "DBCHECK_MCP_POOL_MAX",
        "tool_timeout": "DBCHECK_MCP_TOOL_TIMEOUT",
        "otel_enabled": "DBCHECK_MCP_OTEL",
    }
    for key, env in env_map.items():
        val = os.environ.get(env)
        if val is None:
            continue
        if isinstance(_DEFAULTS[key], bool):
            cfg[key] = val.strip() in ("1", "true", "True", "yes", "on")
        elif isinstance(_DEFAULTS[key], int):
            try:
                cfg[key] = int(val)
            except ValueError:
                pass  # 保持下层来源的值

    # 归一化边界
    cfg["pool_min"] = max(0, int(cfg["pool_min"]))
    cfg["pool_max"] = max(cfg["pool_min"], int(cfg["pool_max"]))
    cfg["tool_timeout"] = max(5, int(cfg["tool_timeout"]))
    return cfg


# 模块加载时即解析一次（避免每次 tools/call 都读盘）
MCP_CONFIG = load_mcp_config()
