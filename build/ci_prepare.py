# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""为「干净检出」准备打包所需的输入物（CI / 全新克隆场景）。

背景：PyInstaller 的 spec 会打包若干运行时目录与配置文件，但其中一部分
被 .gitignore 排除、不会进入版本库，在 CI 的全新 checkout 里根本不存在，
导致 PyInstaller 直接报
``ERROR: Unable to find '<dir>' when adding binary and data files``。

本脚本负责补齐这些「仓库里没有、但打包必须有」的输入物，且保证幂等：
已存在的文件一律不覆盖（本地开发机上你自己的 dbc_config.json 不会被冲掉）。

补齐内容：
1. 运行时目录（git 不跟踪空目录）：``data/pro_data`` 等；
2. ``dbc_config.json`` 默认配置 —— 该文件含凭据，**禁止入库**，
   故只能在构建现场生成一份不含任何密钥的最小可用默认配置。

用法：``python build/ci_prepare.py``（在仓库根目录执行，或任意目录均可）。
"""

from __future__ import annotations

import json
import os
import sys


def _project_root() -> str:
    # build/ci_prepare.py -> 上级即仓库根目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 运行时目录：git 不跟踪空目录，但 spec 的 data_dirs 需要其存在
# 注意：data/pro_data 必须是个「存在且非空的目录」，否则 PyInstaller 报错
# "Unable to find '<dir>' when adding binary and data files"
RUNTIME_DIRS = [
    os.path.join("data", "pro_data"),
    os.path.join("data", "reports"),
    os.path.join("data", "logs"),
    os.path.join("data", "backups"),
    os.path.join("data", "awr_uploads"),
]

# 配置文件：spec 的 data_files 需要这些文件存在于源码目录
# （builtin_registry.json / dbcheck-quotes.json 位于 modules/config/ 下，已随源码检出，
# 但 dbc_config.json 在根目录被 .gitignore 忽略，需现场生成）
CONFIG_FILES = [
    "dbc_config.json",
]

# dbc_config.json 默认配置（不含任何密钥；AI 默认指向本地 Ollama）
DEFAULT_CONFIG = {
    "language": "zh",
    "ai": {
        "backend": "ollama",
        "online_enabled": False,
        "api_key": "",
        "api_url": "http://localhost:11434",
        "model": "qwen3:8b",
        "timeout": 600,
        "rag": {
            "enabled": True,
            "embedding_model": "nomic-embed-text",
            "top_k": 3,
            "chunk_size": 1000,
            "chunk_overlap": 100,
        },
    },
    "notification": {
        "enabled": False,
        "email": {
            "host": "smtp.126.com",
            "port": 465,
            "user": "",
            "password": "",
            "use_tls": False,
            "recipients": [],
        },
        "notification_encryption_key": "",
    },
    "oracle_client_lib_dir": "",
    "show_ai_assistant": True,
}


def ensure_dirs(root: str) -> int:
    made = 0
    for rel in RUNTIME_DIRS:
        path = os.path.join(root, *rel.split("/"))
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            print(f"[ci_prepare] created dir: {rel}")
            made += 1
        # 确保 data/pro_data 非空（PyInstaller 要求非空目录才能被打包）
        if rel == "data/pro_data":
            keep = os.path.join(path, ".gitkeep")
            if not os.path.exists(keep):
                with open(keep, "w") as f:
                    f.write("# Keep this directory non-empty for PyInstaller\n")
                print(f"[ci_prepare] created .gitkeep in data/pro_data")
    return made


def ensure_config(root: str) -> bool:
    path = os.path.join(root, "dbc_config.json")
    if os.path.exists(path):
        print("[ci_prepare] dbc_config.json already exists, kept as-is")
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("[ci_prepare] generated default dbc_config.json (no secrets)")
    return True


def main() -> int:
    root = _project_root()
    print(f"[ci_prepare] project root: {root}")
    ensure_dirs(root)
    ensure_config(root)
    print("[ci_prepare] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
