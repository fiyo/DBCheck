# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""生成官方工作流市场远程索引 ``market/index.json``。

把内置 runbook 模板 + 社区贡献模板（``market/community/*.json``）聚合成一份
自包含的 JSON 索引，托管到 ``https://raccoonx.cn/market/index.json`` 后，用户
即可在「工作流市场」弹窗点「🌐 从 URL 安装」一键拉取全部可安装模板。

索引格式对齐 ``modules.intelligence.workflow_store.fetch_remote_market`` 的解析：
``{"schema": "dbcheck.market.index", "templates": [ {name,description,author,
version,category,tags,steps,edges}, ... ]}``。

用法::

    python scripts/gen_market_index.py
    python scripts/gen_market_index.py --out modules/intelligence/market/index.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from modules.intelligence.market_templates import builtin_templates  # noqa: E402

MARKET_DIR = os.path.join(REPO_ROOT, "modules", "intelligence", "market")
COMMUNITY_DIR = os.path.join(MARKET_DIR, "community")


def collect_builtin() -> list:
    out = []
    for t in builtin_templates():
        out.append({
            "name": t.get("name"),
            "description": t.get("description", ""),
            "author": "DBCheck 官方",
            "version": "1.0.0",
            "category": "官方内置",
            "tags": [],
            "source": "official",
            "steps": t.get("steps", []),
            "edges": t.get("edges", []),
        })
    return out


def collect_community() -> list:
    out = []
    if not os.path.isdir(COMMUNITY_DIR):
        return out
    for fn in sorted(os.listdir(COMMUNITY_DIR)):
        if not fn.endswith(".json") or fn == "index.json":
            continue
        path = os.path.join(COMMUNITY_DIR, fn)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            print("跳过无法解析的社区模板 %s: %s" % (fn, e))
            continue
        if d.get("schema") != "dbcheck.workflow" or not d.get("steps"):
            print("跳过非工作流文件 %s" % fn)
            continue
        out.append({
            "name": d.get("name", fn),
            "description": d.get("description", ""),
            "author": d.get("author", "社区贡献者"),
            "version": str(d.get("version", "1.0.0")),
            "category": d.get("category", "社区"),
            "tags": d.get("tags", []) if isinstance(d.get("tags"), list) else [],
            "source": "community",
            "homepage": d.get("homepage", ""),
            "steps": d.get("steps", []),
            "edges": d.get("edges", []),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="生成官方工作流市场远程索引")
    ap.add_argument("--out", default=os.path.join(MARKET_DIR, "index.json"),
                    help="输出索引路径")
    args = ap.parse_args()

    templates = collect_builtin() + collect_community()
    index = {
        "schema": "dbcheck.market.index",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(templates),
        "templates": templates,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    n_off = len([t for t in templates if t["source"] == "official"])
    n_com = len([t for t in templates if t["source"] == "community"])
    print("已生成 %s（%d 项：%d 内置 + %d 社区）" % (args.out, len(templates), n_off, n_com))


if __name__ == "__main__":
    main()
