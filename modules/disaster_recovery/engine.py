"""AutobackupEngine —— 封装 vendored autobackup，提供 DBCheck 容灾备份能力。

设计要点：
- autobackup 的 config.yaml + history.json 为运行时唯一真相源（落 data/autobackup/）。
- 数据库密码复用 DBCheck 的 _encrypt_pwd（.db_key Fernet + base64）加密存储，config.yaml 不落明文；与 DBCheck 数据源共用同一密钥体系（autobackup 侧 resolve_secret 已适配识别）。
- 健康度 = 0.4 * 新鲜度 + 0.6 * 成功率；恢复验证维度暂无（占位「未验证」）。
"""

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.disaster_recovery.vendor.autobackup import (
    load_config,
    save_config,
    get_task_by_name,
    list_backup_files,
    execute_task,
    run_tasks_now,
    build_task_info,
    BackupResult,
    HistoryStore,
    Notifier,
    setup_logging,
    format_size,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # D:/DBCheck
DATA_DIR = BASE_DIR / "data" / "autobackup"
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
CONFIG_PATH = DATA_DIR / "config.yaml"

_LOCK = threading.Lock()

DEFAULT_GLOBAL: Dict[str, Any] = {
    "backup_dir": str(BACKUP_DIR),
    "log_dir": str(LOG_DIR),
    "retention_days": 30,
    "notify_on_success": False,
    "web": {"host": "127.0.0.1", "port": 5005, "token": ""},
}

SUPPORTED_TYPES = ("mysql", "postgresql", "file")


# ---------------------------------------------------------------------------
# 底层辅助
# ---------------------------------------------------------------------------
def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _load_config() -> Dict[str, Any]:
    _ensure_dirs()
    if not CONFIG_PATH.exists():
        cfg = {"global": dict(DEFAULT_GLOBAL), "tasks": []}
        save_config(str(CONFIG_PATH), cfg)
        return cfg
    return load_config(str(CONFIG_PATH))


def _save_config(cfg: Dict[str, Any]) -> None:
    _ensure_dirs()
    save_config(str(CONFIG_PATH), cfg)


def _get_logger():
    return setup_logging(str(LOG_DIR))


def _get_notifier(cfg: Optional[Dict[str, Any]] = None):
    cfg = cfg or _load_config()
    return Notifier(cfg, _get_logger())


def _get_history() -> HistoryStore:
    return HistoryStore(str(DATA_DIR))


def _version() -> str:
    try:
        from modules.disaster_recovery.vendor.autobackup import __version__
        return __version__
    except Exception:
        return "1.1.0"


# ---------------------------------------------------------------------------
# 类型归一
# ---------------------------------------------------------------------------
def normalize_db_type(db_type: str) -> str:
    """DBCheck db_type -> autobackup type。mariadb 复用 mysql(mysqldump 兼容)。"""
    dt = (db_type or "").lower()
    if dt in ("mysql", "mariadb"):
        return "mysql"
    if dt in ("postgresql", "postgres", "pg"):
        return "postgresql"
    if dt in ("file", "files", "directory", "dir"):
        return "file"
    return dt


# ---------------------------------------------------------------------------
# 计划管理
# ---------------------------------------------------------------------------
def list_plans() -> List[Dict[str, Any]]:
    cfg = _load_config()
    return [build_task_info(t, cfg.get("global", {})) for t in cfg.get("tasks", [])]


def get_plan(name: str) -> Optional[Dict[str, Any]]:
    cfg = _load_config()
    t = get_task_by_name(cfg, name)
    return build_task_info(t, cfg.get("global", {})) if t else None


def create_plan(payload: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        cfg = _load_config()
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("计划名称不能为空")
        if get_task_by_name(cfg, name):
            raise ValueError(f"计划 {name} 已存在")
        db_type = normalize_db_type(payload.get("db_type", ""))
        if db_type not in SUPPORTED_TYPES:
            raise ValueError(f"不支持的备份类型: {db_type}（仅支持 mysql/postgresql/file）")

        task: Dict[str, Any] = {
            "name": name,
            "type": db_type,
            "enabled": bool(payload.get("enabled", True)),
        }
        schedule = (payload.get("schedule") or "").strip()
        if schedule:
            task["schedule"] = schedule
        if payload.get("retention_days"):
            task["retention_days"] = int(payload["retention_days"])

        if db_type == "file":
            task["source"] = payload.get("source", "")
            if payload.get("exclude"):
                task["exclude"] = payload["exclude"]
        else:
            password = payload.get("password", "")
            connection_id = (payload.get("connection_id") or "").strip()
            if not password and connection_id:
                # 未输入密码且选了数据源：自动从 DBCheck 数据源取密码
                from pro.instance_manager import InstanceManager
                inst = InstanceManager().get_instance_decrypted(connection_id)
                if not inst or not inst.get("password"):
                    raise ValueError("无法从数据源获取密码，请手动填写或检查数据源配置")
                password = inst.get("password")
            task["database"] = {
                "host": payload.get("host", "localhost"),
                "port": int(payload.get("port", 3306 if db_type == "mysql" else 5432)),
                "user": payload.get("user", ""),
                "database": payload.get("database", ""),
            }
            if password:
                # 复用 DBCheck 数据源密码加密（.db_key Fernet + base64），不再引入 AUTOBACKUP_KEY
                from pro.instance_manager import _encrypt_pwd
                task["database"]["password"] = _encrypt_pwd(password)

        cfg.setdefault("tasks", []).append(task)
        _save_config(cfg)
    reload_scheduler()
    return get_plan(name)


def toggle_plan(name: str, enabled: bool) -> Dict[str, Any]:
    with _LOCK:
        cfg = _load_config()
        t = get_task_by_name(cfg, name)
        if not t:
            raise ValueError(f"计划 {name} 不存在")
        t["enabled"] = bool(enabled)
        _save_config(cfg)
    reload_scheduler()
    return get_plan(name)


def delete_plan(name: str) -> None:
    with _LOCK:
        cfg = _load_config()
        cfg["tasks"] = [t for t in cfg.get("tasks", []) if t.get("name") != name]
        _save_config(cfg)
    reload_scheduler()


# ---------------------------------------------------------------------------
# 执行 / 列表 / 历史
# ---------------------------------------------------------------------------
def run_now(name: str) -> Dict[str, Any]:
    cfg = _load_config()
    t = get_task_by_name(cfg, name)
    if not t:
        raise ValueError(f"计划 {name} 不存在")
    result = execute_task(
        t, cfg.get("global", {}), _get_notifier(cfg), _get_logger(), _get_history()
    )
    return result.to_dict()


def list_backups(task_name: Optional[str] = None) -> List[Dict[str, Any]]:
    cfg = _load_config()
    return list_backup_files(cfg, task_name)


def delete_backup(filename: str) -> None:
    path = BACKUP_DIR / filename
    if path.exists():
        path.unlink()


def get_history(limit: int = 50, task_name: Optional[str] = None) -> List[Dict[str, Any]]:
    return _get_history().list(limit=limit, task_name=task_name)


def get_health_metrics() -> Dict[str, Any]:
    history = _get_history()
    stats = history.stats()
    last = stats.get("last_run")
    freshness = _compute_freshness([last] if last else [])
    success_rate = float(stats.get("success_rate", 0))
    score = round(0.4 * freshness + 0.6 * success_rate, 1)
    return {
        "score": score,
        "freshness": freshness,
        "success_rate": success_rate,
        "verified": False,
        "verified_label": "未验证",
        "stats": stats,
    }


def _compute_freshness(records: List[Dict[str, Any]]) -> float:
    if not records:
        return 0.0
    latest = max((r.get("start_time") for r in records if r.get("start_time")), default=None)
    if not latest:
        return 0.0
    try:
        lt = datetime.strptime(latest, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return 0.0
    age_hours = (datetime.now() - lt).total_seconds() / 3600.0
    if age_hours <= 24:
        return 100.0
    if age_hours >= 24 * 7:
        return 0.0
    return round(100.0 * (1 - (age_hours - 24) / (24 * 6)), 1)


def get_status() -> Dict[str, Any]:
    from modules.disaster_recovery.scheduler_hook import get_scheduler

    sched = get_scheduler()
    cfg = _load_config()
    return {
        "scheduler_running": bool(sched and sched.is_running),
        "plan_count": len(cfg.get("tasks", [])),
        "autobackup_version": _version(),
    }


# ---------------------------------------------------------------------------
# 数据源连接（best-effort，供前端下拉）
# ---------------------------------------------------------------------------
def list_connections() -> List[Dict[str, Any]]:
    try:
        from pro.instance_manager import InstanceManager

        im = InstanceManager()
        # 取原始密码字段（加密串），仅用于判断是否存在密码，再脱敏返回
        insts = im.get_all_instances(mask_password=False)
        allowed = {"mysql", "mariadb", "postgresql"}
        out = []
        for it in insts:
            dt = (it.get("db_type") or "").lower()
            if dt in allowed:
                out.append(
                    {
                        "id": it.get("id") or it.get("name"),
                        "name": it.get("name"),
                        "db_type": dt,
                        "host": it.get("host"),
                        "port": it.get("port"),
                        "user": it.get("user"),
                        "database": it.get("database"),
                        "password_set": bool(it.get("password")),
                    }
                )
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 调度重载（计划变更后调用）
# ---------------------------------------------------------------------------
def reload_scheduler() -> None:
    from modules.disaster_recovery.scheduler_hook import reload_scheduler as _reload

    try:
        _reload()
    except Exception as exc:  # pragma: no cover - 调度失败不应阻断接口
        print(f"  [WARN] 容灾备份调度器重载失败: {exc}")
