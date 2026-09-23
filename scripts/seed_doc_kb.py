# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""DocKB 官方文档知识库 - 种子示例数据。

策展内容统一存放在随包种子 ``modules/config/doc_kb_seed.json``（单一数据源，
应用启动时由 ``modules.doc_kb.models.seed_from_json()`` 幂等自动播种）。
本脚本仅用于本地**手动**重写 / 重置数据（Docker/exe 分发无需手动执行）：

    python scripts/seed_doc_kb.py            # 清空并重写策展种子
    python scripts/seed_doc_kb.py --keep     # 仅追加缺失项（幂等）

注意：
- data/doc_kb.db 不进 git，可随时删除重建，零风险。
- 版权边界：只存「短事实摘录 + official_url 引用」，绝不整本搬运官方文档。
"""
import argparse
import json
import os
import sys

# ── 路径收口：剥离可能遮蔽 D:\DBCheck 的会话目录命名空间包 ──
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # D:\DBCheck （脚本位于 scripts/ 下）
sys.path = [REPO] + [
    p for p in sys.path
    if p and os.path.abspath(p or ".") != REPO
    and "2026-06-19-10-06-09" not in p
]
os.chdir(REPO)

from modules.doc_kb import models  # noqa: E402

SEED_PATH = os.path.join(REPO, "modules", "config", "doc_kb_seed.json")


def _load_seed():
    """从随包策展种子 JSON 读取 sources / facts（单一数据源）。"""
    with open(SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    sources = seed.get("sources") or []
    facts = seed.get("facts") or []
    if not sources or not facts:
        raise SystemExit(f"[seed] 种子文件缺少数据: {SEED_PATH}")
    return sources, facts


def _existing_keys() -> set:
    keys = set()
    for f in models.get_facts():
        keys.add((f["db_type"], f.get("version") or "all", f["key"]))
    return keys


def seed(reset: bool) -> None:
    sources, facts = _load_seed()
    models.ensure_db()

    if reset:
        with models._connect() as conn:
            conn.execute("DELETE FROM doc_facts")
            conn.execute("DELETE FROM doc_sources")
            try:
                conn.execute("DELETE FROM sqlite_sequence "
                             "WHERE name IN ('doc_facts','doc_sources')")
            except Exception:
                pass
        print("[seed] 已清空 doc_facts / doc_sources，准备重写策展种子。")
    else:
        print("[seed] 保留既有数据，仅追加缺失项（幂等）。")

    have = _existing_keys() if not reset else set()
    src_ids = []
    for s in sources:
        sid = models.insert_source(
            db_type=s["db_type"], version=s["version"],
            official_url=s["official_url"], title=s.get("title"),
            license_note=s.get("license_note"),
            note="curated seed",
        )
        src_ids.append(sid)

    added = 0
    for f in facts:
        try:
            idx = int(f["source_idx"])
            src = sources[idx]
        except (KeyError, ValueError, IndexError):
            continue
        ver = src["version"]
        if (src["db_type"], ver, f["key"]) in have:
            continue
        models.insert_fact(
            source_id=src_ids[idx],
            db_type=src["db_type"], version=ver,
            category=f.get("category", "param"), key=f["key"],
            value=f.get("value"), excerpt=f.get("excerpt"),
            official_url=f.get("official_url"),
            severity=f.get("severity"), lang=f.get("lang", "both"),
            created_by="seed",
        )
        added += 1

    total = len(models.get_facts())
    print(f"[seed] 完成：新增 {added} 条事实，当前库共 {total} 条事实 / {len(src_ids)} 个来源。")


def main() -> None:
    ap = argparse.ArgumentParser(description="DocKB 策展种子数据写入")
    ap.add_argument("--keep", action="store_true",
                    help="不清空既有数据，仅在缺失时追加（幂等）")
    args = ap.parse_args()
    seed(reset=not args.keep)


if __name__ == "__main__":
    main()
