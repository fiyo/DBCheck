#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
一键刷新 HGDB 插件巡检模板到运行库。

背景：
  pluginkit.loader._init_plugin_templates 在模板已存在时会直接跳过，
  因此修改 plugins/enabled/hgdb_jdbc/template_data.json 后，已导入实例的
  inspection_query 表不会自动更新。本脚本读取最新的 template_data.json，
  把 query_sql / 描述 同步到 data/inspection.db（覆盖已存在、补插新增）。

用法：
  python scripts/refresh_hgdb_plugin_template.py              # 刷新（实际写入）
  python scripts/refresh_hgdb_plugin_template.py --dry-run    # 只预览，不写入
  python scripts/refresh_hgdb_plugin_template.py --db-type hgdb
  python scripts/refresh_hgdb_plugin_template.py --db /path/inspection.db --plugin-dir /path/hgdb_jdbc
  python scripts/refresh_hgdb_plugin_template.py --prune      # 同时删除“库里有、json 里没有”的 query

说明：
  - 只刷新“默认模板”（db_type 匹配、is_default=1），不影响用户复制出的自定义模板。
  - 复用 dal.update_query / create_query，自动写入 inspection_history 变更记录。
  - Web 进程实时读库，无需重启即可生效（下次巡检即取新 SQL）。
"""
import argparse
import json
import sys
from pathlib import Path

# 脚本自身位于 <root>/scripts/，用 __file__ 定位自身父目录（开发工具定位自身，
# 非业务代码上溯取项目根），并通过 sys.path 暴露模块。
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _plugin_db_type(plugin_dir: Path) -> str:
    """与 loader._plugin_db_type 保持一致：优先 plugin.json 的 db_type，否则取目录名前缀。"""
    pj = plugin_dir / "plugin.json"
    if pj.exists():
        try:
            d = json.loads(pj.read_text(encoding="utf-8"))
            if d.get("db_type"):
                return d["db_type"]
        except Exception:
            pass
    name = plugin_dir.name
    return name.split("_", 1)[0] if "_" in name else name


def parse_plugin_template(plugin_dir: Path) -> dict:
    """读取并归一化 template_data.json。"""
    data_path = plugin_dir / "template_data.json"
    if not data_path.exists():
        raise SystemExit(f"[错误] 未找到模板文件: {data_path}")
    cfg = json.loads(data_path.read_text(encoding="utf-8"))
    tpl = cfg.get("template", {})
    db_type = tpl.get("db_type") or _plugin_db_type(plugin_dir)
    chapters = []
    for c in cfg.get("chapters", []):
        chapters.append({
            "number": c.get("chapter_number", 1),
            "title_zh": c.get("chapter_title_zh", "未命名章节"),
            "title_en": c.get("chapter_title_en", ""),
            "queries": [{
                "key": q.get("query_key", ""),
                "sql": q.get("query_sql", ""),
                "desc_zh": q.get("query_description_zh", ""),
                "desc_en": q.get("query_description_en", ""),
                "sort": q.get("sort_order", 1),
            } for q in c.get("queries", [])],
        })
    return {"db_type": db_type, "chapters": chapters}


def _db_chapter_map(dal, template_id: int, db_path: str) -> dict:
    """返回 {chapter_number: chapter_id}。"""
    conn = dal.get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, chapter_number FROM inspection_chapter WHERE template_id=?",
            (template_id,),
        )
        return {row["chapter_number"]: row["id"] for row in cur.fetchall()}
    finally:
        conn.close()


def _find_query(dal, db_path: str, chapter_id: int, query_key: str):
    conn = dal.get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, query_sql, query_description_zh, query_description_en "
            "FROM inspection_query WHERE chapter_id=? AND query_key=?",
            (chapter_id, query_key),
        )
        return cur.fetchone()
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="一键刷新 HGDB 插件巡检模板到运行库")
    ap.add_argument("--db-type", default="hgdb", help="数据库类型，默认 hgdb")
    ap.add_argument("--db", default=None, help="inspection.db 路径（默认用 paths.INSPECTION_DB）")
    ap.add_argument("--plugin-dir", default=None,
                    help="插件目录（默认 <root>/plugins/enabled/hgdb_jdbc）")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    ap.add_argument("--prune", action="store_true",
                    help="删除“运行库里有、但 template_data.json 里没有”的 query")
    args = ap.parse_args()

    from modules.core import paths
    from modules.inspection import dal

    db_path = args.db or str(paths.INSPECTION_DB)
    plugin_dir = Path(args.plugin_dir) if args.plugin_dir \
        else (paths.PROJECT_ROOT / "plugins" / "enabled" / "hgdb_jdbc")

    tpl = parse_plugin_template(plugin_dir)
    db_type = tpl["db_type"]
    print(f"[刷新] 插件目录 : {plugin_dir}")
    print(f"[刷新] 数据库   : {db_path}")
    print(f"[刷新] db_type  : {db_type}  dry-run={args.dry_run}  prune={args.prune}")

    # 1. 找默认模板
    templates = dal.get_templates_by_db_type(db_type, db_path=db_path)
    if not templates:
        raise SystemExit(
            f"[错误] 运行库中不存在 db_type={db_type} 的模板，请先启动 Web 完成插件初始化"
        )
    template = templates[0]
    template_id = template["id"]
    print(f"[刷新] 命中模板 : {template['template_name']} (id={template_id})")

    # 2. 运行库章节映射
    db_chapters = _db_chapter_map(dal, template_id, db_path)

    updated = inserted = skipped = pruned = 0

    # 3. 逐 chapter / query 同步
    for ch in tpl["chapters"]:
        ch_id = db_chapters.get(ch["number"])
        if ch_id is None:
            print(f"  [跳过] 章节 {ch['number']} {ch['title_zh']} 在运行库中不存在")
            continue
        for q in ch["queries"]:
            if not q["key"]:
                continue
            row = _find_query(dal, db_path, ch_id, q["key"])
            if row is None:
                if args.dry_run:
                    print(f"  [新增(预览)] {q['key']}: {q['sql'][:70]}")
                    inserted += 1
                    continue
                dal.create_query(ch_id, q["key"], q["sql"], q["desc_zh"],
                                q["desc_en"], enabled=1, sort_order=q["sort"],
                                db_path=db_path)
                print(f"  [新增] {q['key']}")
                inserted += 1
                continue
            if (row["query_sql"] or "") == (q["sql"] or ""):
                skipped += 1
                continue
            if args.dry_run:
                print(f"  [更新(预览)] {q['key']}")
                print(f"      - {(row['query_sql'] or '')[:90]}")
                print(f"      + {(q['sql'] or '')[:90]}")
                updated += 1
                continue
            dal.update_query(row["id"], query_sql=q["sql"],
                             query_description_zh=q["desc_zh"],
                             query_description_en=q["desc_en"], db_path=db_path)
            print(f"  [更新] {q['key']}")
            updated += 1

    # 4. 可选 prune：删除“库里有、json 里没有”的 query
    if args.prune:
        json_keys = {
            (ch["number"], q["key"])
            for ch in tpl["chapters"] for q in ch["queries"] if q["key"]
        }
        for ch_number, ch_id in db_chapters.items():
            conn = dal.get_db_connection(db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, query_key FROM inspection_query WHERE chapter_id=?",
                    (ch_id,),
                )
                for r in cur.fetchall():
                    if (ch_number, r["query_key"]) not in json_keys:
                        if args.dry_run:
                            print(f"  [删除(预览)] {r['query_key']}")
                        else:
                            cur.execute(
                                "DELETE FROM inspection_query WHERE id=?",
                                (r["id"],),
                            )
                            conn.commit()
                            print(f"  [删除] {r['query_key']}")
                        pruned += 1
            finally:
                conn.close()

    print(f"\n[完成] 更新 {updated} / 新增 {inserted} / 跳过(无变化) {skipped}"
          + (f" / 删除 {pruned}" if args.prune else ""))


if __name__ == "__main__":
    main()
