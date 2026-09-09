# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""CI 专用的「宽松」依赖安装器。

为什么需要它
------------
``deploy/requirements.txt`` 里有若干**平台相关的可选数据库驱动**，它们在部分
平台根本没有可用 wheel，只能从 sdist 编译，而编译往往因缺少厂商 SDK 而失败：

* ``dmpython``（达梦 DM8）：只有 manylinux / win_amd64 的 wheel，**没有任何 macOS wheel**；
* ``gbase8sdb``（GBase 8s）：**只有 sdist**，且需要 GBase SDK 才能编译。

在本地开发机上装不上时，人可以手工处理；但在 CI 里，一行装不上就会让
``pip install -r`` 整条命令失败、构建直接挂掉。

策略
----
**逐个依赖单独安装**：装不上的记录为 WARN 并继续，而不是中断整条链；
全部装完后，再对**构建期真正必需的模块**做 import 校验——核心模块缺一个
就判定失败退出，可选驱动缺了只警告（运行时是动态 import，且有 try/except 兜底，
对应数据库的**原生模式**不可用、JDBC/其它路径不受影响）。

这样既保证构建产物完整可用，又不会被某个厂商 SDK 卡死整条流水线。

用法：``python build/ci_install_deps.py``
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REQUIREMENTS = os.path.join(ROOT, "deploy", "requirements.txt")

# 构建期必需模块：import 名 ->  pip 包名（仅用于提示）
CORE_MODULES = [
    "flask",
    "flask_socketio",
    "gevent",
    "werkzeug",
    "docx",
    "docxtpl",
    "openpyxl",
    "yaml",
    "numpy",
    "pandas",
    "psutil",
    "cryptography",
    "bcrypt",
    "jwt",
    "paramiko",
    "requests",
    "croniter",
    "bs4",
    "reportlab",
    "PyPDF2",
    "apscheduler",
    "pymysql",
    "psycopg2",
    "oracledb",
    "pymongo",
    "redis",
    "jpype",          # JPype1：JDBC 插件的 JVM 后端
    "jaydebeapi",
]


def _iter_requirement_lines(path: str):
    """逐行读取 requirements，剥离注释与空行，保留 PEP 508 环境标记。"""
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # 剥离行内注释（注意 # 前需有空白，避免误伤 URL 中的 #）
            for i, ch in enumerate(line):
                if ch == "#" and (i == 0 or line[i - 1].isspace()):
                    line = line[:i].strip()
                    break
            if line:
                yield line


def _pip_install(spec: str) -> bool:
    # --prefer-binary: 优先用预编译 wheel，避免从 sdist 源码编译（Pillow 源码编译需
    #   jpeg 等系统库，且 CI 网络偶发抖动时大 wheel 易超时 fallback 到源码编译而失败）。
    # --timeout 120 --retries 10: CI 到 pypi 网络偶发超时（默认 15s 对 numpy/pandas/
    #   Pillow/JPype1 等大包不够），放宽超时与重试，避免非确定性构建失败。
    cmd = [sys.executable, "-m", "pip", "install", "--prefer-binary",
           "--timeout", "120", "--retries", "10", spec]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ok = proc.returncode == 0
    print(f"[ci_deps] {'OK  ' if ok else 'FAIL'} pip install {spec}", flush=True)
    if not ok:
        tail = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-12:]
        for t in tail:
            print(f"[ci_deps]     | {t}", flush=True)
    return ok


def main() -> int:
    if not os.path.exists(REQUIREMENTS):
        print(f"[ci_deps] requirements not found: {REQUIREMENTS}")
        return 1

    print(f"[ci_deps] python: {sys.executable} ({sys.version.split()[0]})")
    print(f"[ci_deps] requirements: {REQUIREMENTS}")

    failed: list[str] = []
    total = 0
    for spec in _iter_requirement_lines(REQUIREMENTS):
        total += 1
        if not _pip_install(spec):
            failed.append(spec)

    print(f"[ci_deps] ---- installed {total - len(failed)}/{total} ----")
    if failed:
        print("[ci_deps] skipped (optional on this platform):")
        for spec in failed:
            print(f"[ci_deps]   - {spec}")

    # 核心模块 import 校验
    missing: list[str] = []
    for mod in CORE_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001 - 只关心能否导入
            missing.append(f"{mod} ({type(exc).__name__})")

    if missing:
        print("[ci_deps] ERROR: core modules missing:")
        for m in missing:
            print(f"[ci_deps]   - {m}")
        return 1

    print(f"[ci_deps] core modules OK ({len(CORE_MODULES)} checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
