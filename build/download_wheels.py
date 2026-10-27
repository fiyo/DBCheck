#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck
"""Cross-platform wheel downloader for offline CI builds.

Why
---
GitHub-hosted Windows/macOS runners' shared egress IPs are rate-limited/blocked
by PyPI (and by the CN mirrors we fall back to), so ``pip install`` on those
runners fails *deterministically* (fast connection-refused, not a slow retry).
The Docker build job, however, runs on ``ubuntu-latest`` and CAN reach PyPI.

This script is designed to run on that ubuntu runner: it downloads pre-built
wheels for each target platform into ``<out_dir>``. The Windows/macOS build jobs
then install them offline via ``pip install --no-index --find-links <dir>``.

Usage
-----
    python build/download_wheels.py <target> <out_dir>

    target:  win | mac-x86_64 | mac-arm64
    out_dir: directory to drop the wheels into (created if missing)
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REQUIREMENTS = os.path.join(ROOT, "deploy", "requirements.txt")

# (platform tag, python version, implementation, abi)
TARGETS = {
    "win":        ("win_amd64", "311", "cp", "cp311"),
    "mac-x86_64": ("macosx_11_0_x86_64", "311", "cp", "cp311"),
    "mac-arm64":  ("macosx_11_0_arm64", "311", "cp", "cp311"),
}

# Packages with no usable wheel for some/all target platforms. They are dropped
# from the download set; the build's lenient installer will then skip them
# (they are optional vendor drivers guarded by try/except at runtime).
#   gbase8sdb : sdist only, needs GBase SDK to build -> exclude everywhere.
#   pytest    : dev-only, not needed at runtime -> exclude everywhere.
#   dmpython  : has win + manylinux wheels, but NO macOS wheel -> exclude on macOS.
EXCLUDE = {
    "all": {"gbase8sdb", "pytest"},
    "mac": {"dmpython"},
}

MIRROR_FALLBACKS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
]


def _iter_requirement_lines(path: str):
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            for i, ch in enumerate(line):
                if ch == "#" and (i == 0 or line[i - 1].isspace()):
                    line = line[:i].strip()
                    break
            if line:
                yield line


def _bare_name(line: str) -> str:
    """Extract the bare distribution name from a (possibly pinned) requirement."""
    name = line.split("==")[0].split(">=")[0].split("<=")[0]
    name = name.split(">")[0].split("<")[0].split("~")[0]
    name = name.split(";")[0].split("[")[0].strip().lower()
    return name


def filtered_requirements(target: str) -> list[str]:
    mac = target.startswith("mac")
    excluded = EXCLUDE["all"] | (EXCLUDE["mac"] if mac else set())
    kept: list[str] = []
    for line in _iter_requirement_lines(REQUIREMENTS):
        if _bare_name(line) in excluded:
            print(f"[dl] EXCLUDE {line}  (no wheel for {target})", flush=True)
            continue
        kept.append(line)
    # Build tooling not declared in requirements.txt:
    kept.append("pyinstaller")
    return kept


def _pip_download(reqfile: str, out_dir: str, target: str, index_url: str | None) -> int:
    plat, pyver, impl, abi = TARGETS[target]
    cmd = [sys.executable, "-m", "pip", "download",
           "--only-binary=:all:",
           "--platform", plat,
           "--python-version", pyver,
           "--implementation", impl,
           "--abi", abi,
           "--timeout", "180", "--retries", "8",
           "-r", reqfile, "-d", out_dir]
    if index_url:
        cmd += ["--index-url", index_url]
    print(f"[dl] $ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        tail = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-25:]
        for t in tail:
            print(f"[dl]     | {t}", flush=True)
    return proc.returncode


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: download_wheels.py <win|mac-x86_64|mac-arm64> <out_dir>")
        return 2
    target, out_dir = sys.argv[1], sys.argv[2]
    if target not in TARGETS:
        print(f"[dl] unknown target {target}", flush=True)
        return 2

    os.makedirs(out_dir, exist_ok=True)
    reqs = filtered_requirements(target)
    reqfile = os.path.join(out_dir, "requirements.filtered.txt")
    with open(reqfile, "w", encoding="utf-8") as f:
        f.write("\n".join(reqs) + "\n")

    print(f"[dl] target={target} -> {out_dir}", flush=True)
    print(f"[dl] {len(reqs)} requirement lines (incl. pyinstaller)", flush=True)

    rc = _pip_download(reqfile, out_dir, target, None)
    if rc != 0:
        for mirror in MIRROR_FALLBACKS:
            print(f"[dl] RETRY with mirror {mirror}", flush=True)
            if _pip_download(reqfile, out_dir, target, mirror) == 0:
                rc = 0
                break
    if rc != 0:
        print(f"[dl] FAILED to download wheels for {target}", flush=True)
        return 1

    n = len([p for p in os.listdir(out_dir) if p.endswith((".whl", ".tar.gz"))])
    print(f"[dl] OK: {n} archives downloaded into {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
