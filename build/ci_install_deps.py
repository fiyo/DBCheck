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

# 离线安装模式：当 CI 把跨平台 wheel 预下载成 artifact 并传入此目录时，
# 改用 `pip install --no-index --find-links <DIR>` 离线装，彻底摆脱被封的
# Windows/macOS runner 出口。装不上的可选驱动（该平台无 wheel 的，如 macOS
# 上的 dmpython）按既有 lenient 逻辑跳过。未设置则走在线安装（本地开发用）。
WHEEL_DIR = os.environ.get("DBCHECK_WHEEL_DIR") or ""

# PyPI 备用镜像（仅在官方源失败时回退使用）：
# GitHub Actions runner 出口 IP 共享，PyPI 对这些 IP 有速率限制，表现为
# 403/429 或连接长时间无数据——同源重试无效，必须换源。
MIRROR_FALLBACKS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
]

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
    # 离线模式：所有依赖已预下载成 wheel（含完整依赖树），仅需从本地目录装。
    # --no-index 强制不再访问任何网络源，--find-links 指向预下载目录。
    if WHEEL_DIR:
        cmd = [sys.executable, "-m", "pip", "install", "--no-index",
               "--find-links", WHEEL_DIR, spec]
        return _run_pip(cmd, spec)

    # --prefer-binary: 优先用预编译 wheel，避免从 sdist 源码编译（Pillow 源码编译需
    #   jpeg 等系统库，且 CI 网络偶发抖动时大 wheel 易超时 fallback 到源码编译而失败）。
    # --timeout 120 --retries 10: CI 到 pypi 网络偶发超时（默认 15s 对 numpy/pandas/
    #   Pillow/JPype1 等大包不够），放宽超时与重试，避免非确定性构建失败。
    cmd = [sys.executable, "-m", "pip", "install", "--prefer-binary",
           "--timeout", "120", "--retries", "10", spec]
    ok = _run_pip(cmd, spec)
    if not ok:
        # 回退 1：禁用 pip 缓存。缓存里的半包/坏包是「重试 10 次仍失败」的常见原因
        # （runner 镜像自带缓存 + 上次中断留下的损坏 wheel），清缓存后往往一次就成。
        # 注意：选项必须放在包名之前，否则 pip 可能不识别。
        print(f"[ci_deps] RETRY with --no-cache-dir: {spec}", flush=True)
        ok = _run_pip([sys.executable, "-m", "pip", "install", "--prefer-binary",
                       "--timeout", "120", "--retries", "10", "--no-cache-dir", spec], spec)
    if not ok:
        # 回退 2：换镜像源。GitHub runner 出口 IP 共享，PyPI 对这些 IP 有速率限制，
        # 表现为 403/429 或长时间 0 字节——重试同源无效，必须换源。
        # 顺序：清华 → 阿里云（国内镜像，作为最后救命通道）。
        for mirror in MIRROR_FALLBACKS:
            print(f"[ci_deps] RETRY with mirror {mirror}: {spec}", flush=True)
            mcmd = [sys.executable, "-m", "pip", "install", "--prefer-binary",
                    "--no-cache-dir", "--index-url", mirror,
                    "--timeout", "180", "--retries", "5", spec]
            if _run_pip(mcmd, spec):
                return True
    return ok


def _run_pip(cmd, spec: str) -> bool:
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
        # 落一份清单文件并由 CI 收进 artifact / job summary：
        # 构建日志本身可能因 artifact 命名问题上传失败，缺哪个模块必须另有出口，
        # 否则线上排障只能靠猜。
        try:
            out = os.path.join(HERE, "core-missing.txt")
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("core modules missing:\n")
                for m in missing:
                    fh.write(f"- {m}\n")
                fh.write(f"\nskipped optional installs:\n")
                for spec in failed:
                    fh.write(f"- {spec}\n")
            print(f"[ci_deps] missing list written to {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"[ci_deps] failed to write missing list: {exc}")
        return 1

    # 成功时清理旧清单，避免上传到上一轮遗留的过期文件
    try:
        stale = os.path.join(HERE, "core-missing.txt")
        if os.path.exists(stale):
            os.remove(stale)
    except Exception:
        pass

    print(f"[ci_deps] core modules OK ({len(CORE_MODULES)} checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
