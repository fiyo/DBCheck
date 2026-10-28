# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""DBCheck 中央路径配置与迁移引导模块。

集中管理项目内所有关键路径，供各模块统一引用，避免散落的硬编码相对路径。
阶段3 的数据路径迁移会在此处暴露常量；本模块同时提供幂等的 ``ensure_migrated()``
入口，供 ``data_manager.py`` / ``web_ui.py`` 在导入时触发一次性旧路径迁移。
"""

import sys
import shutil
from pathlib import Path


def _project_root() -> Path:
    """返回项目根目录。

    - 打包（frozen）环境：PyInstaller 6.x 的 one-folder 构建会把随包资源
      （web_templates / db / i18n / assets / modules/config 等）统一收集到
      ``<exe 同级>/_internal/`` 目录下；one-file 模式则解压到 ``sys._MEIPASS``。
      以 web_templates / db 作为标记目录定位真实根，避免模板与建表脚本找不到。
    - 普通 Python 运行：使用本文件的上三级目录（core/ 的父目录）。
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates = [exe_dir, exe_dir / "_internal"]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass))
        for c in candidates:
            if (c / "web_templates").is_dir() or (c / "db").is_dir():
                return c
        # 兜底：保留原行为（exe 同级），避免启动崩溃，并打印告警便于排查
        import sys as _sys
        print(
            f"[paths][WARN] 未在 {exe_dir} 或其 _internal 下找到 "
            f"web_templates/db，回退到 exe 同级；_MEIPASS={meipass}",
            file=_sys.stderr,
        )
        return exe_dir
    # core 现已迁入 modules/（modules/core/paths.py），
    # 需上溯三级才能到达项目根目录 D:/DBCheck
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _project_root()
ASSETS_DIR = PROJECT_ROOT / "assets"

# ── 数据根与资源根分离（#66）────────────────────────────────
# 背景（打包版踩坑）：PyInstaller one-folder 会把 web_templates / db / modules
# 收进 <exe>/_internal/，而 _project_root() 在 frozen 下选中的正是 _internal，
# 于是 DATA_DIR = <exe>/_internal/data —— **用户数据落在分发包内部**。
# 用户每次下载新版本 zip 解压到同一目录，_internal 被整体覆盖 →
# um_rbac.db（用户与密码）、instances.db（数据源）、.db_key（加密密钥）、
# inspection.db（巡检历史）、scheduler_jobs.json（定时任务）、reports/ 全部归零，
# 启动时 init_default_user 重建 admin → 表现为「重启后密码被重置为初始密码」。
#
# 现在明确区分：
#   PROJECT_ROOT —— 只读资源根（模板/建表脚本/前端资源），frozen 下 = _internal；
#   DATA_ROOT    —— 可写数据根，frozen 下 = **exe 同级**（与分发包分离，升级不覆盖）。
# 旧数据在首次启动时由 ensure_data_dir_migrated() 一次性迁到新位置（幂等）。
if getattr(sys, "frozen", False):
    DATA_ROOT = Path(sys.executable).resolve().parent
else:
    DATA_ROOT = PROJECT_ROOT

DATA_DIR = DATA_ROOT / "data"
PRO_DATA_DIR = DATA_DIR / "pro_data"
REPORTS_DIR = DATA_DIR / "reports"
BACKUPS_DIR = DATA_DIR / "backups"
USER_DB_DIR = DATA_DIR / "user_db"
LOG_DIR = DATA_DIR / "logs"
SCHEDULER_LOG = LOG_DIR / "scheduler.log"
RESTART_LOG = LOG_DIR / "web_service_restart.log"
PYTEST_LOG = LOG_DIR / "pytest_p0.log"
SCHEDULER_JOBS = DATA_DIR / "scheduler_jobs.json"
# 密码加密密钥：锚定 data/ 运行时持久目录（与 instances.db / inspection.db 同域）。
# 历史上曾放在项目根（frozen 下即 <exe>/_internal/.db_key），重装或重新打包会
# 覆盖 _internal，导致密钥丢失、历史加密的数据源密码全部无法解密。
# 旧位置见 DB_KEY_PATH_LEGACY，首次启动由 instance_manager._get_fernet() 自动迁移。
DB_KEY_PATH = DATA_DIR / ".db_key"
DB_KEY_PATH_LEGACY = PROJECT_ROOT / ".db_key"
INSPECTION_DB = DATA_DIR / "inspection.db"
# 官方数据库文档知识库（DocKB）结构化事实库，存 data/ 不进 git
DOC_KB_DB = DATA_DIR / "doc_kb.db"
LOGO_PATH = ASSETS_DIR / "brand" / "dbcheck_logo.png"
AWR_UPLOADS_DIR = DATA_DIR / "awr_uploads"
# ── 主配置文件 dbc_config.json（#67：移入持久化数据目录）──────────────────
# 背景：dbc_config.json 存放通知邮件/SMTP、AI、BIC-QA、插件市场等用户配置与
# 加密密钥，历史上位于 PROJECT_ROOT（程序根/镜像根），在以下场景被重置：
#   - Docker：PROJECT_ROOT=/app 不在挂载卷 /app/data 内，容器重建即丢
#     （用户反馈「重启后通知邮件没了」）；
#   - 打包版（与 #66 同类）：frozen 下 PROJECT_ROOT=_internal，升级被覆盖；
#   - 开发版：散落在仓库根，易随 git 操作/清理丢失。
# 现统一锚定到 DATA_DIR（与 instances.db / um_rbac.db 同域，Docker 下即卷内）；
# 旧位置见 DBC_CONFIG_PATH_LEGACY，首次启动由 ensure_migrated() 自动迁移。
DBC_CONFIG_PATH = DATA_DIR / "dbc_config.json"
DBC_CONFIG_PATH_LEGACY = PROJECT_ROOT / "dbc_config.json"

# 旧路径（兼容解析用，供阶段3 迁移与兼容）
PRO_DATA_DIR_LEGACY = PROJECT_ROOT / "pro_data"
REPORTS_DIR_LEGACY = PROJECT_ROOT / "reports"
BACKUPS_DIR_LEGACY = PROJECT_ROOT / "data_backups"
USER_DB_DIR_LEGACY = PROJECT_ROOT / "user_management" / "db"


def resolve(new: Path, old: Path) -> Path:
    """优先返回新路径；若新路径不存在则回退到旧路径；都不存在时返回新路径。"""
    return new if new.exists() else (old if old.exists() else new)


_MANIFEST = DATA_DIR / ".migration_manifest.json"
_BACKUP_ROOT = DATA_DIR / ".migration_backup"


# ── 配置 / 数据资产目录（根目录文件梳理迁移，T1 新增）──────────────────
CONFIG_DIR = PROJECT_ROOT / "modules" / "config"
BUILTIN_REGISTRY_JSON = CONFIG_DIR / "builtin_registry.json"
BUILTIN_TYPES_JSON = CONFIG_DIR / "builtin_types.json"
QUOTES_JSON = CONFIG_DIR / "dbcheck-quotes.json"
VERSION_JSON = CONFIG_DIR / "version.json"
# 顶部公告条配置：内容源在官网（https://dbcheck.top/announcement.json），
# 后台线程定时拉取，成功才写入本地缓存 ANNOUNCEMENT_CACHE_JSON（data/，不进 git）；
# 断网/无缓存时公告条隐藏。ANNOUNCEMENT_JSON（data/，不进 git）为用户手动覆盖，
# 优先级高于缓存，enabled:false 可永久关闭公告条。
ANNOUNCEMENT_JSON = DATA_DIR / "announcement.json"
ANNOUNCEMENT_CACHE_JSON = DATA_DIR / "announcement_cache.json"
BATCH_TEMPLATE_DIR = CONFIG_DIR / "batch_templates"


def ensure_data_dir_migrated():
    """frozen 环境：把运行时数据从分发包内部迁到 exe 同级（#66）。

    旧位置：``<exe>/_internal/data``（随 zip 分发包被覆盖，升级即丢）
    新位置：``<exe>/data``（与分发包分离，升级保留）

    幂等：目标已存在且非空时不做任何事（不覆盖用户新数据）。
    仅打包版生效；开发版数据本来就在项目根，无需迁移。
    """
    if not getattr(sys, "frozen", False):
        return False
    try:
        old_dir = (PROJECT_ROOT / "data").resolve()
        new_dir = DATA_DIR.resolve()
        if old_dir == new_dir:
            return False
        # 目标已存在且有内容 → 视为已迁移/用户已有数据，不动它
        try:
            if new_dir.exists() and any(new_dir.iterdir()):
                return False
        except OSError:
            pass
        if not old_dir.exists() or not any(old_dir.iterdir()):
            return False
        import shutil
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_dir), str(new_dir))
        print("[paths] 运行时数据已从 %s 迁移到 %s（升级不再丢失用户数据）"
              % (old_dir, new_dir))
        return True
    except Exception as e:
        print(f"[paths][WARN] 运行时数据目录迁移失败（不阻断启动）: {e}")
        return False


def _migrate_dbc_config():
    """把旧位置（程序根）的 dbc_config.json 迁到 DATA_DIR（#67）。

    仅当旧文件存在、新位置不存在时执行；幂等。目的：
    - Docker 下 PROJECT_ROOT=/app 不在挂载卷内，重建即丢通知/AI 等配置；
    - 开发版避免散落在仓库根；打包版（#66 同因）避免升级被覆盖。
    """
    legacy = DBC_CONFIG_PATH_LEGACY
    new = DBC_CONFIG_PATH
    if not legacy.exists() or new.exists():
        return False
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(new))
        print("[paths] dbc_config.json 已从 %s 迁移到 %s（配置不再随容器/升级丢失）"
              % (legacy, new))
        return True
    except Exception as e:
        print(f"[paths][WARN] dbc_config.json 迁移失败（不阻断启动）: {e}")
        return False


def ensure_migrated():
    """幂等地执行一次旧路径迁移（若检测到遗留旧路径）。

    降级策略：迁移过程出现异常时仅打印告警并继续执行，不阻断应用启动。
    """
    # 打包版：先把运行时数据从分发包内部挪到 exe 同级（#66），再走旧路径迁移
    ensure_data_dir_migrated()
    # dbc_config.json：从程序根迁移到持久化数据目录（#67）
    _migrate_dbc_config()
    try:
        legacy_exists = any(
            p.exists()
            for p in (
                PRO_DATA_DIR_LEGACY,
                REPORTS_DIR_LEGACY,
                BACKUPS_DIR_LEGACY,
                USER_DB_DIR_LEGACY,
                PROJECT_ROOT / "scheduler.log",
                PROJECT_ROOT / "scheduler_jobs.json",
                PROJECT_ROOT / "awr_uploads",
            )
        )
        if legacy_exists:
            from scripts.migrate_legacy_paths import run

            run()
    except Exception as e:  # 降级：迁移失败不阻断启动
        print(f"[migration] 自动迁移跳过（降级旧路径）: {e}")
