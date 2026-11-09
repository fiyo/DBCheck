# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""DBCheck 旧路径迁移脚本（幂等）。

将历史散落在仓库根/旧目录中的图片、脚本、数据目录统一迁移到新结构：

- 阶段1（23 项图片）：根目录图片 -> ``assets/db_logos`` 与 ``assets/brand``
- 阶段2（6 项脚本）：根/旧目录脚本 -> ``scripts``
- 阶段3（7 项目录/文件）：根/旧数据目录 -> ``data`` 下新结构

特性：
- 幂等：已迁移（manifest 中 status==done 且新位置存在）的项自动跳过。
- 安全：迁移前先备份到 ``<DATA_DIR>/.migration_backup/<时间戳>/<原相对路径>``。
- 可回滚：通过 ``scripts/rollback_paths.py`` 依据 manifest 逆序回滚。

用法：
    python scripts/migrate_legacy_paths.py            # 执行真实迁移
    python scripts/migrate_legacy_paths.py --dry-run  # 仅打印计划，不移动
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 保证从仓库根启动时能 import core / scripts
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from modules.core import paths  # noqa: E402


# 源相对路径 -> 目标相对路径（均以 PROJECT_ROOT 拼接）
OPERATIONS = [
    # ---------- 阶段1：图片 (23) ----------
    ("clickhouse.png", "assets/db_logos/clickhouse.png"),
    ("db2.png", "assets/db_logos/db2.png"),
    ("dm.png", "assets/db_logos/dm.png"),
    ("dm8.png", "assets/db_logos/dm8.png"),
    ("gbase.png", "assets/db_logos/gbase.png"),
    ("gbase.jpg", "assets/db_logos/gbase.jpg"),
    ("hgdb.png", "assets/db_logos/hgdb.png"),
    ("ivorysql.png", "assets/db_logos/ivorysql.png"),
    ("kingbase.png", "assets/db_logos/kingbase.png"),
    ("mongodb.png", "assets/db_logos/mongodb.png"),
    ("mysql.png", "assets/db_logos/mysql.png"),
    ("oceanbase.png", "assets/db_logos/oceanbase.png"),
    ("oracle.png", "assets/db_logos/oracle.png"),
    ("pg.png", "assets/db_logos/pg.png"),
    ("redis.png", "assets/db_logos/redis.png"),
    ("sqlserver.png", "assets/db_logos/sqlserver.png"),
    ("tidb.png", "assets/db_logos/tidb.png"),
    ("uxdb.png", "assets/db_logos/uxdb.png"),
    ("yashandb.png", "assets/db_logos/yashandb.png"),
    ("dbcheck_logo.png", "assets/brand/dbcheck_logo.png"),
    ("dbcheck_logo_banner.png", "assets/brand/dbcheck_logo_banner.png"),
    ("dbcheck_logo_icon.png", "assets/brand/dbcheck_logo_icon.png"),
    ("favicon.ico", "assets/brand/favicon.ico"),
    # ---------- 阶段2：脚本 (5) ----------
    ("init_mongodb_template.py", "scripts/init_mongodb_template.py"),
    ("diag_oceanbase.py", "scripts/diag_oceanbase.py"),
    ("mod_logger.py", "scripts/mod_logger.py"),
    ("pro_data/_scan_dbs.py", "scripts/pro_scan_dbs.py"),
    ("pro_data/_merge_dbs.py", "scripts/pro_merge_dbs.py"),
    # ---------- 阶段3：目录/文件级 (7) ----------
    ("pro_data", "data/pro_data"),
    ("reports", "data/reports"),
    ("data_backups", "data/backups"),
    ("user_management/db", "data/user_db"),
    ("scheduler.log", "data/logs/scheduler.log"),
    ("scheduler_jobs.json", "data/scheduler_jobs.json"),
    ("awr_uploads", "data/awr_uploads"),
]


def _read_manifest() -> dict:
    """读取迁移清单；不存在或解析失败返回空清单。"""
    if paths._MANIFEST.exists():
        try:
            with open(paths._MANIFEST, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"version": 1, "entries": []}


def _write_manifest(manifest: dict) -> None:
    """写回迁移清单（确保父目录存在）。"""
    paths._MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with open(paths._MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)


def _is_mount_point(path) -> bool:
    """判断 path 是否为挂载点（如 Docker volume 挂载的 /app/reports）。

    挂载点目录不能被 rmtree 删除，否则抛 [Errno 16] Device or resource busy。
    - 优先用 os.path.ismount（POSIX 下能正确识别 volume 挂载）。
    - 兜底：比较 path 与父目录的 st_dev，分属不同设备即视为挂载点。
    """
    path = Path(path)
    if not path.exists():
        return False
    try:
        if os.path.ismount(str(path)):
            return True
    except OSError:
        pass
    try:
        src_dev = path.stat().st_dev
        parent = path.parent
        if parent.exists():
            parent_dev = parent.stat().st_dev
            if src_dev != parent_dev:
                return True
    except OSError:
        pass
    return False


def _merge_into(src: Path, dst: Path):
    """把 src 的内容归并进 dst（覆盖同名），然后删除 src。

    用于「目标已存在」场景：将旧目录内容归并到已存在的新目录，避免 shutil.move
    把旧目录嵌套进新目录。src 为文件时直接 rename 到 dst；src 为目录时逐个子项
    归并，冲突时以 src 为准覆盖，最后删除 src 根目录。

    注意：若 src 本身是挂载点（Docker volume 挂载的 /app/reports 等），
    内容归并完毕**后不删除 src 根目录**（rmtree 挂载点会抛
    [Errno 16] Device or resource busy），仅保留挂载点；调用方仍会写 manifest，
    避免每次启动重试迁移而刷屏日志。
    """
    import shutil

    src, dst = Path(src), Path(dst)
    if not src.exists():
        return
    if src.is_file():
        shutil.move(str(src), str(dst))
        return
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir() and target.exists() and target.is_dir():
            _merge_into(child, target)
        else:
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(str(target))
                else:
                    target.unlink()
            shutil.move(str(child), str(target))
    # src 为挂载点时禁止 rmtree（避免 Device or resource busy）；内容已归并至 dst，
    # 保留挂载点即可，调用方仍会将此项写入 manifest 标记为已完成。
    if _is_mount_point(src):
        print(f"[migration] 源 {src} 为挂载点，已归并内容但保留挂载点（跳过删除）")
        return
    shutil.rmtree(str(src))


def run(dry_run: bool = False) -> None:
    """执行（或预览）旧路径迁移。

    :param dry_run: 为 True 时仅打印计划，不移动任何文件。
    """
    manifest = _read_manifest()
    entries = manifest.setdefault("entries", [])
    done_keys = {
        (e.get("old"), e.get("new"))
        for e in entries
        if e.get("status") == "done"
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    planned = 0
    moved = 0

    for old_rel, new_rel in OPERATIONS:
        old = paths.PROJECT_ROOT / old_rel
        new = paths.PROJECT_ROOT / new_rel

        # 幂等跳过：已迁移且新位置存在（或旧位置已不存在）
        if (old_rel, new_rel) in done_keys and (new.exists() or not old.exists()):
            continue
        # 源不存在则跳过该项
        if not old.exists():
            continue

        if dry_run:
            print(f"[dry-run] {old_rel} -> {new_rel}")
            planned += 1
            continue

        # 非 dry-run：先备份，再移动，并追加 manifest 条目
        backup = paths._BACKUP_ROOT / timestamp / old_rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        if old.is_file():
            shutil.copy2(old, backup)
        else:
            shutil.copytree(str(old), str(backup), dirs_exist_ok=True)

        if new.exists() and old.is_dir() and new.is_dir():
            # 目标已存在且同为目录：把旧目录内容归并进已存在的新目录，再删除旧目录
            _merge_into(old, new)
        elif new.exists() and new.is_file():
            # 目标为文件：先备份已存在的目标，再用 old 覆盖（本场景目标均为目录，仅兜底）
            new_backup = backup.parent / f"{new.name}__existing"
            shutil.copy2(str(new), str(new_backup))
            if new.is_dir():
                shutil.rmtree(str(new))
            else:
                new.unlink()
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
        else:
            # 目标不存在：常规备份 + 创建父目录 + 移动
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))

        entries.append(
            {
                "old": old_rel,
                "new": new_rel,
                "backup": str(backup),
                "status": "done",
                "ts": datetime.now().isoformat(),
            }
        )
        _write_manifest(manifest)
        moved += 1

    if dry_run:
        print(
            f"[dry-run] 预览完成：共 {planned} 项将迁移（未实际移动任何文件）"
        )
    else:
        print(f"[migration] 迁移完成：本次移动 {moved} 项")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="DBCheck 旧路径迁移脚本（幂等，支持回滚）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印迁移计划，不实际移动文件",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
