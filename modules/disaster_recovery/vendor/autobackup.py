#!/usr/bin/env python3
"""
autobackup - 定时自动备份工具
支持 MySQL、PostgreSQL 数据库及文件目录备份。

This file is a **modified** copy of autobackup (https://github.com/MMCISAGOODMAN/autobackup),
version 1.1.0, licensed under the MIT License.
Copyright (c) 2026 MMCISAGOODMAN

DBCheck modifications (see NOTICE for the full MIT license text):
  - Password encryption/decryption reuse DBCheck's own crypto
    (pro.instance_manager._encrypt_pwd / _decrypt_pwd, key from the .db_key Fernet);
    AUTOBACKUP_KEY is only a fallback for standalone CLI / legacy data.
  - An empty MySQL database name falls back to `mysqldump --all-databases`
    (back up the whole instance) instead of being passed as a positional arg.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import gzip
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml
from croniter import croniter

GRACE_SECONDS = 120  # 调度器启动后，允许在计划时间后 2 分钟内补跑

__version__ = "1.1.0"

ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")
ENC_PREFIX = "enc:"


# ---------------------------------------------------------------------------
# Password encryption (optional, key from AUTOBACKUP_KEY env)
# ---------------------------------------------------------------------------

def _derive_fernet_key(key_material: str) -> bytes:
    digest = hashlib.sha256(key_material.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_password(plain: str, key_material: Optional[str] = None) -> str:
    """加密数据库密码。

    优先复用 DBCheck 数据源密码加密（pro.instance_manager._encrypt_pwd，
    基于根目录 .db_key 的 Fernet + base64 包装），不再依赖 AUTOBACKUP_KEY；
    仅在无法导入 DBCheck 时回退到 AUTOBACKUP_KEY 逻辑（兼容独立 CLI / 旧数据）。
    """
    try:
        from pro.instance_manager import _encrypt_pwd
        return _encrypt_pwd(plain)
    except Exception:
        pass
    # 回退：保留原 AUTOBACKUP_KEY 行为
    key_material = key_material or os.environ.get("AUTOBACKUP_KEY")
    if not key_material:
        raise ValueError("AUTOBACKUP_KEY 环境变量未设置，无法加密密码")
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise ImportError("加密功能需要安装 cryptography: pip install cryptography") from exc
    f = Fernet(_derive_fernet_key(key_material))
    token = f.encrypt(plain.encode()).decode()
    return f"{ENC_PREFIX}{token}"


def decrypt_password(cipher: str, key_material: Optional[str] = None) -> str:
    if not cipher:
        return cipher
    # enc: 前缀：旧 AUTOBACKUP_KEY 加密数据，保留兼容
    if cipher.startswith(ENC_PREFIX):
        key_material = key_material or os.environ.get("AUTOBACKUP_KEY")
        if not key_material:
            raise ValueError("加密密码需要设置 AUTOBACKUP_KEY 环境变量")
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise ImportError("解密功能需要安装 cryptography: pip install cryptography") from exc
        f = Fernet(_derive_fernet_key(key_material))
        token = cipher[len(ENC_PREFIX):]
        return f.decrypt(token.encode()).decode()
    # 非 enc: 前缀：尝试 DBCheck 加密格式（base64(Fernet)，无前缀）
    try:
        from pro.instance_manager import _decrypt_pwd
        return _decrypt_pwd(cipher)
    except Exception:
        return cipher


def _try_dbcheck_decrypt(text: str) -> Optional[str]:
    """尝试用 DBCheck 的 .db_key(Fernet) 解密 base64 密文；失败返回 None。

    用于识别 DBCheck 数据源密码加密格式（base64(Fernet)，无 enc: 前缀），
    使备份任务执行时能与 DBCheck 共用同一套密钥体系。
    """
    try:
        from pro.instance_manager import _get_fernet
        import base64
        f = _get_fernet()
        if f is None:
            return None
        return f.decrypt(base64.b64decode(text.encode())).decode()
    except Exception:
        return None


def resolve_secret(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    match = ENV_PATTERN.fullmatch(text.strip())
    if match:
        env_val = os.environ.get(match.group(1))
        if env_val is None:
            raise ValueError(f"环境变量 {match.group(1)} 未设置")
        return env_val
    if text.startswith(ENC_PREFIX):
        return decrypt_password(text)
    # 尝试 DBCheck 加密格式（base64(Fernet)，无前缀）；非密文则原样返回
    dec = _try_dbcheck_decrypt(text)
    if dec is not None and dec != text:
        return dec
    return text


def resolve_value(value: Any) -> Any:
    if isinstance(value, str):
        def replacer(m: re.Match) -> str:
            env_val = os.environ.get(m.group(1))
            if env_val is None:
                raise ValueError(f"环境变量 {m.group(1)} 未设置")
            return env_val
        if ENV_PATTERN.search(value):
            return ENV_PATTERN.sub(replacer, value)
        if value.startswith(ENC_PREFIX):
            return decrypt_password(value)
        # 尝试 DBCheck 加密格式；非密文则原样返回
        dec = _try_dbcheck_decrypt(value)
        if dec is not None and dec != value:
            return dec
        return value
    if isinstance(value, dict):
        return {k: resolve_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str) -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with config_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_config(path: str, config: Dict[str, Any]) -> None:
    config_path = Path(path)
    with config_path.open("w", encoding="utf-8") as fh:
        yaml.dump(config, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)


def get_task_by_name(config: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for task in config.get("tasks", []):
        if task.get("name") == name:
            return task
    return None


def task_type_label(task_type: str) -> str:
    mapping = {
        "mysql": "MySQL",
        "postgresql": "PostgreSQL",
        "postgres": "PostgreSQL",
        "pg": "PostgreSQL",
        "file": "文件目录",
        "files": "文件目录",
        "directory": "文件目录",
        "dir": "文件目录",
    }
    return mapping.get(task_type.lower(), task_type)


def get_task_backup_dir(task: Dict[str, Any], global_cfg: Dict[str, Any]) -> Path:
    return Path(task.get("backup_dir") or global_cfg.get("backup_dir", "./backups"))


def list_backup_files(config: Dict[str, Any], task_name: Optional[str] = None) -> List[Dict[str, Any]]:
    global_cfg = config.get("global", {})
    default_dir = Path(global_cfg.get("backup_dir", "./backups"))
    tasks = config.get("tasks", [])
    if task_name:
        tasks = [t for t in tasks if t.get("name") == task_name]

    seen: set[Path] = set()
    files: List[Dict[str, Any]] = []
    search_dirs = {get_task_backup_dir(t, global_cfg) for t in tasks} if tasks else {default_dir}

    for backup_dir in search_dirs:
        if not backup_dir.exists():
            continue
        for path in backup_dir.iterdir():
            if not path.is_file() or path in seen:
                continue
            if not (path.name.endswith(".sql.gz") or path.name.endswith(".tar.gz")):
                continue
            seen.add(path)
            stat = path.stat()
            matched_task = task_name
            if not matched_task:
                for task in config.get("tasks", []):
                    if path.name.startswith(f"{task['name']}_"):
                        matched_task = task["name"]
                        break
            files.append({
                "filename": path.name,
                "path": str(path),
                "task": matched_task or "unknown",
                "size_bytes": stat.st_size,
                "size_human": format_size(stat.st_size),
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "type": "sql" if path.name.endswith(".sql.gz") else "tar",
            })

    files.sort(key=lambda x: x["created_at"], reverse=True)
    return files


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_dir: str, verbose: bool = False) -> logging.Logger:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("autobackup")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_path / "autobackup.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(console)
    return logger


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class Notifier:
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.config = config.get("notification", {})
        self.logger = logger

    def notify_failure(self, subject: str, message: str) -> None:
        self.logger.error("备份失败通知: %s - %s", subject, message)
        self._send_all(subject, message, force=True)

    def notify_success(self, subject: str, message: str, enabled: bool) -> None:
        if not enabled:
            return
        self.logger.info("备份成功通知: %s", subject)
        self._send_all(subject, message, force=False)

    def _send_all(self, subject: str, message: str, force: bool) -> None:
        errors = []
        enabled_channels = [
            name for name in ("email", "dingtalk", "wecom", "feishu")
            if self.config.get(name, {}).get("enabled")
        ]
        if force and not enabled_channels:
            self.logger.error("备份失败但未启用任何通知渠道，请在配置中启用至少一种")
        for name, sender in (
            ("email", self._send_email),
            ("dingtalk", self._send_dingtalk),
            ("wecom", self._send_wecom),
            ("feishu", self._send_feishu),
        ):
            channel = self.config.get(name, {})
            if not channel.get("enabled"):
                continue
            try:
                sender(subject, message, resolve_value(channel))
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                self.logger.exception("通知发送失败 (%s)", name)
        if errors and force:
            self.logger.error("部分通知发送失败: %s", "; ".join(errors))

    def _send_email(self, subject: str, message: str, cfg: Dict[str, Any]) -> None:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = cfg["from_addr"]
        msg["To"] = ", ".join(cfg["to_addrs"])

        password = resolve_secret(cfg.get("password", ""))
        with smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port", 587))) as server:
            if cfg.get("use_tls", True):
                server.starttls()
            if cfg.get("username"):
                server.login(cfg["username"], password)
            server.sendmail(cfg["from_addr"], cfg["to_addrs"], msg.as_string())

    def _send_dingtalk(self, subject: str, message: str, cfg: Dict[str, Any]) -> None:
        payload = {
            "msgtype": "text",
            "text": {"content": f"{subject}\n{message}"},
        }
        resp = requests.post(cfg["webhook"], json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(data.get("errmsg", "钉钉通知失败"))

    def _send_wecom(self, subject: str, message: str, cfg: Dict[str, Any]) -> None:
        payload = {
            "msgtype": "text",
            "text": {"content": f"{subject}\n{message}"},
        }
        resp = requests.post(cfg["webhook"], json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(data.get("errmsg", "企业微信通知失败"))

    def _send_feishu(self, subject: str, message: str, cfg: Dict[str, Any]) -> None:
        payload = {
            "msg_type": "text",
            "content": {"text": f"{subject}\n{message}"},
        }
        resp = requests.post(cfg["webhook"], json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(data.get("msg", "飞书通知失败"))


# ---------------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------------

def timestamp_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def set_file_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def format_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def cleanup_old_backups(
    backup_dir: Path,
    name: str,
    retention_days: int,
    extensions: Tuple[str, ...],
    logger: logging.Logger,
) -> None:
    if retention_days <= 0:
        return
    cutoff = datetime.now() - timedelta(days=retention_days)
    prefix = f"{name}_"
    removed = 0
    for path in backup_dir.iterdir():
        if not path.is_file():
            continue
        if not path.name.startswith(prefix):
            continue
        if not any(path.name.endswith(ext) for ext in extensions):
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if mtime < cutoff:
            path.unlink()
            removed += 1
            logger.info("已清理过期备份: %s", path.name)
    if removed:
        logger.info("任务 %s 共清理 %d 个过期备份", name, removed)


def run_command(cmd: List[str], env: Optional[Dict[str, str]] = None) -> None:
    result = subprocess.run(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"命令执行失败 ({result.returncode}): {' '.join(cmd)}\n{stderr}")


# ---------------------------------------------------------------------------
# Backup executors
# ---------------------------------------------------------------------------

class BackupResult:
    def __init__(
        self,
        task_name: str,
        success: bool,
        start_time: datetime,
        end_time: datetime,
        backup_file: Optional[Path] = None,
        size_bytes: int = 0,
        error: Optional[str] = None,
        task_type: str = "",
    ):
        self.task_name = task_name
        self.success = success
        self.start_time = start_time
        self.end_time = end_time
        self.backup_file = backup_file
        self.size_bytes = size_bytes
        self.error = error
        self.task_type = task_type

    @property
    def duration(self) -> timedelta:
        return self.end_time - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_name": self.task_name,
            "task_type": self.task_type,
            "success": self.success,
            "start_time": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": self.end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": round(self.duration.total_seconds(), 1),
            "backup_file": str(self.backup_file) if self.backup_file else None,
            "filename": self.backup_file.name if self.backup_file else None,
            "size_bytes": self.size_bytes,
            "size_human": format_size(self.size_bytes),
            "error": self.error,
        }

    def summary(self) -> str:
        status = "成功" if self.success else "失败"
        lines = [
            f"任务: {self.task_name}",
            f"状态: {status}",
            f"开始: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"结束: {self.end_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"耗时: {self.duration.total_seconds():.1f}s",
        ]
        if self.backup_file:
            lines.append(f"文件: {self.backup_file}")
            lines.append(f"大小: {format_size(self.size_bytes)}")
        if self.error:
            lines.append(f"错误: {self.error}")
        return "\n".join(lines)


class HistoryStore:
    def __init__(self, log_dir: str, max_records: int = 500):
        self.path = Path(log_dir) / "history.json"
        self.max_records = max_records
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def _read(self) -> List[Dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return []

    def _write(self, records: List[Dict[str, Any]]) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(records[-self.max_records:], fh, ensure_ascii=False, indent=2)

    def add(self, result: BackupResult) -> None:
        record = result.to_dict()
        record["id"] = f"{result.task_name}_{int(result.start_time.timestamp())}"
        with self._lock:
            records = self._read()
            records.append(record)
            self._write(records)

    def list(self, limit: int = 50, task_name: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            records = self._read()
        if task_name:
            records = [r for r in records if r.get("task_name") == task_name]
        return list(reversed(records[-limit:]))

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            records = self._read()
        if not records:
            return {
                "total_runs": 0,
                "success_count": 0,
                "failure_count": 0,
                "success_rate": 0,
                "total_size_bytes": 0,
                "last_run": None,
            }
        success = [r for r in records if r.get("success")]
        last = records[-1]
        return {
            "total_runs": len(records),
            "success_count": len(success),
            "failure_count": len(records) - len(success),
            "success_rate": round(len(success) / len(records) * 100, 1),
            "total_size_bytes": sum(r.get("size_bytes", 0) for r in success),
            "last_run": last,
        }


def backup_mysql(task: Dict[str, Any], backup_dir: Path, logger: logging.Logger) -> Path:
    db = task["database"]
    host = db.get("host", "localhost")
    port = int(db.get("port", 3306))
    user = db["user"]
    password = resolve_secret(db.get("password", ""))
    database = db.get("database", "")

    outfile = backup_dir / f"{task['name']}_{timestamp_str()}.sql.gz"
    mysqldump = shutil.which("mysqldump")
    if not mysqldump:
        raise RuntimeError("未找到 mysqldump 命令，请安装 MySQL 客户端")

    cmd = [
        mysqldump,
        f"--host={host}",
        f"--port={port}",
        f"--user={user}",
        "--single-transaction",
        "--routines",
        "--triggers",
        "--set-gtid-purged=OFF",
    ]
    if database:
        cmd.append(database)
    else:
        # 库名为空 → 备份整个实例（MySQL 实例级，无默认库）
        cmd.append("--all-databases")
    env = os.environ.copy()
    env["MYSQL_PWD"] = password

    logger.info("执行 MySQL 备份: %s@%s:%s/%s", user, host, port, database or "ALL")
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    with gzip.open(outfile, "wb") as gz:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            gz.write(chunk)
    stderr = proc.stderr.read().decode() if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        if outfile.exists():
            outfile.unlink()
        raise RuntimeError(f"mysqldump 失败 ({rc}): {stderr.strip()}")

    set_file_permissions(outfile)
    return outfile


def backup_postgresql(task: Dict[str, Any], backup_dir: Path, logger: logging.Logger) -> Path:
    db = task["database"]
    host = db.get("host", "localhost")
    port = int(db.get("port", 5432))
    user = db["user"]
    password = resolve_secret(db.get("password", ""))
    database = db["database"]

    outfile = backup_dir / f"{task['name']}_{timestamp_str()}.sql.gz"
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError("未找到 pg_dump 命令，请安装 PostgreSQL 客户端")

    cmd = [
        pg_dump,
        f"--host={host}",
        f"--port={port}",
        f"--username={user}",
        "--format=plain",
        "--no-owner",
        "--no-privileges",
        database,
    ]
    env = os.environ.copy()
    env["PGPASSWORD"] = password

    logger.info("执行 PostgreSQL 备份: %s@%s:%s/%s", user, host, port, database)
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    with gzip.open(outfile, "wb") as gz:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            gz.write(chunk)
    stderr = proc.stderr.read().decode() if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        if outfile.exists():
            outfile.unlink()
        raise RuntimeError(f"pg_dump 失败 ({rc}): {stderr.strip()}")

    set_file_permissions(outfile)
    return outfile


def _should_exclude(name: str, exclude_patterns: List[str]) -> bool:
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def backup_files(task: Dict[str, Any], backup_dir: Path, logger: logging.Logger) -> Path:
    source = Path(task["source"]).resolve()
    if not source.exists():
        raise FileNotFoundError(f"备份源目录不存在: {source}")

    exclude_patterns = task.get("exclude", [])
    outfile = backup_dir / f"{task['name']}_{timestamp_str()}.tar.gz"

    logger.info("执行文件备份: %s -> %s", source, outfile.name)

    def tar_filter(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        basename = os.path.basename(tarinfo.name)
        if _should_exclude(basename, exclude_patterns):
            return None
        return tarinfo

    with tarfile.open(outfile, "w:gz") as tar:
        tar.add(source, arcname=source.name, filter=tar_filter)

    set_file_permissions(outfile)
    return outfile


def execute_task(
    task: Dict[str, Any],
    global_cfg: Dict[str, Any],
    notifier: Notifier,
    logger: logging.Logger,
    history: Optional[HistoryStore] = None,
) -> BackupResult:
    name = task["name"]
    task_type = task["type"].lower()
    start = datetime.now()
    backup_dir = get_task_backup_dir(task, global_cfg)
    backup_dir.mkdir(parents=True, exist_ok=True)

    notify_success = task.get(
        "notify_on_success",
        global_cfg.get("notify_on_success", False),
    )
    retention_days = int(task.get("retention_days", global_cfg.get("retention_days", 30)))

    logger.info("======== 开始备份任务: %s ========", name)
    backup_file: Optional[Path] = None
    error_msg: Optional[str] = None

    try:
        if task_type == "mysql":
            backup_file = backup_mysql(task, backup_dir, logger)
            extensions = (".sql.gz",)
        elif task_type in ("postgresql", "postgres", "pg"):
            backup_file = backup_postgresql(task, backup_dir, logger)
            extensions = (".sql.gz",)
        elif task_type in ("file", "files", "directory", "dir"):
            backup_file = backup_files(task, backup_dir, logger)
            extensions = (".tar.gz",)
        else:
            raise ValueError(f"不支持的备份类型: {task_type}")

        size_bytes = backup_file.stat().st_size if backup_file else 0
        cleanup_old_backups(backup_dir, name, retention_days, extensions, logger)

        end = datetime.now()
        result = BackupResult(name, True, start, end, backup_file, size_bytes, task_type=task_type)
        logger.info(
            "任务 %s 备份成功 | 文件: %s | 大小: %s | 耗时: %.1fs",
            name,
            backup_file,
            format_size(size_bytes),
            result.duration.total_seconds(),
        )
        notifier.notify_success(
            f"[autobackup] 备份成功: {name}",
            result.summary(),
            notify_success,
        )
        if history:
            history.add(result)
        return result

    except Exception as exc:
        end = datetime.now()
        error_msg = str(exc)
        result = BackupResult(name, False, start, end, backup_file, error=error_msg, task_type=task_type)
        logger.error("任务 %s 备份失败: %s", name, error_msg)
        notifier.notify_failure(
            f"[autobackup] 备份失败: {name}",
            result.summary(),
        )
        if history:
            history.add(result)
        return result


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def get_next_run(cron_expr: str, base: Optional[datetime] = None) -> datetime:
    base = base or datetime.now()
    return croniter(cron_expr, base).get_next(datetime)


def show_next_runs(tasks: List[Dict[str, Any]], logger: logging.Logger) -> None:
    now = datetime.now()
    logger.info("当前时间: %s", now.strftime("%Y-%m-%d %H:%M:%S"))
    for task in tasks:
        if not task.get("enabled", True):
            logger.info("  [%s] 已禁用", task["name"])
            continue
        schedule = task.get("schedule")
        if not schedule:
            logger.info("  [%s] 未配置 schedule", task["name"])
            continue
        nxt = get_next_run(schedule, now)
        delta = nxt - now
        logger.info(
            "  [%s] cron=%s | 下次执行: %s (%.0f 秒后)",
            task["name"],
            schedule,
            nxt.strftime("%Y-%m-%d %H:%M:%S"),
            delta.total_seconds(),
        )


def run_tasks_now(
    config: Dict[str, Any],
    notifier: Notifier,
    logger: logging.Logger,
    task_filter: Optional[str] = None,
    history: Optional[HistoryStore] = None,
) -> List[BackupResult]:
    global_cfg = config.get("global", {})
    tasks = config.get("tasks", [])
    results = []
    for task in tasks:
        if not task.get("enabled", True):
            continue
        if task_filter and task["name"] != task_filter:
            continue
        results.append(execute_task(task, global_cfg, notifier, logger, history))
    return results


def build_task_info(task: Dict[str, Any], global_cfg: Dict[str, Any]) -> Dict[str, Any]:
    schedule = task.get("schedule")
    now = datetime.now()
    info: Dict[str, Any] = {
        "name": task["name"],
        "type": task["type"].lower(),
        "type_label": task_type_label(task["type"]),
        "enabled": task.get("enabled", True),
        "schedule": schedule,
        "retention_days": task.get("retention_days", global_cfg.get("retention_days", 30)),
    }
    if schedule and task.get("enabled", True):
        nxt = get_next_run(schedule, now)
        info["next_run"] = nxt.strftime("%Y-%m-%d %H:%M:%S")
        info["next_run_in_seconds"] = int((nxt - now).total_seconds())
    else:
        info["next_run"] = None
        info["next_run_in_seconds"] = None

    task_type = task["type"].lower()
    if task_type == "mysql":
        db = task.get("database", {})
        info["target"] = f"{db.get('host', 'localhost')}:{db.get('port', 3306)}/{db.get('database', '')}"
    elif task_type in ("postgresql", "postgres", "pg"):
        db = task.get("database", {})
        info["target"] = f"{db.get('host', 'localhost')}:{db.get('port', 5432)}/{db.get('database', '')}"
    elif task_type in ("file", "files", "directory", "dir"):
        info["target"] = task.get("source", "")

    backup_dir = get_task_backup_dir(task, global_cfg)
    task_files = [f for f in list_backup_files({"global": global_cfg, "tasks": [task]}) if f["task"] == task["name"]]
    info["backup_count"] = len(task_files)
    info["latest_backup"] = task_files[0] if task_files else None
    info["backup_dir"] = str(backup_dir)
    return info


class Scheduler:
    def __init__(
        self,
        config: Dict[str, Any],
        notifier: Notifier,
        logger: logging.Logger,
        history: Optional[HistoryStore] = None,
    ):
        self.config = config
        self.notifier = notifier
        self.logger = logger
        self.history = history
        self.global_cfg = config.get("global", {})
        self.tasks = [t for t in config.get("tasks", []) if t.get("enabled", True)]
        self._running = True
        self._last_run: Dict[str, datetime] = {}
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def reload_config(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.global_cfg = config.get("global", {})
        self.tasks = [t for t in config.get("tasks", []) if t.get("enabled", True)]

    def start_background(self) -> None:
        if self.is_running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self.run,
            kwargs={"register_signals": False},
            daemon=True,
            name="autobackup-scheduler",
        )
        self._thread.start()

    def stop(self, *_args: Any) -> None:
        self.logger.info("收到停止信号，调度器退出...")
        self._running = False

    def _due_tasks(self, now: datetime) -> List[Dict[str, Any]]:
        due = []
        for task in self.tasks:
            schedule = task.get("schedule")
            if not schedule:
                continue
            name = task["name"]
            last = self._last_run.get(name)
            cron = croniter(schedule, now)
            prev_run = cron.get_prev(datetime)
            if last is None:
                if (now - prev_run).total_seconds() <= GRACE_SECONDS:
                    due.append(task)
            elif prev_run > last:
                due.append(task)
        return due

    def run(self, register_signals: bool = True) -> None:
        if register_signals:
            try:
                signal.signal(signal.SIGINT, self.stop)
                signal.signal(signal.SIGTERM, self.stop)
            except ValueError:
                pass

        self.logger.info("autobackup 调度器已启动，共 %d 个任务", len(self.tasks))
        for task in self.tasks:
            if task.get("schedule"):
                nxt = get_next_run(task["schedule"])
                self.logger.info("  [%s] 下次执行: %s", task["name"], nxt.strftime("%Y-%m-%d %H:%M:%S"))

        while self._running:
            now = datetime.now()
            due = self._due_tasks(now)
            for task in due:
                if not self._running:
                    break
                execute_task(task, self.global_cfg, self.notifier, self.logger, self.history)
                self._last_run[task["name"]] = datetime.now()

            if not self._running:
                break

            next_times = []
            for task in self.tasks:
                schedule = task.get("schedule")
                if not schedule:
                    continue
                base = self._last_run.get(task["name"], now)
                nxt = get_next_run(schedule, base)
                next_times.append(nxt)

            if not next_times:
                self.logger.warning("没有可调度任务，60 秒后重试")
                time.sleep(60)
                continue

            sleep_until = min(next_times)
            sleep_secs = max(1, (sleep_until - datetime.now()).total_seconds())
            self.logger.debug("下次检查: %s (%.0fs 后)", sleep_until.strftime("%H:%M:%S"), sleep_secs)
            slept = 0.0
            while slept < sleep_secs and self._running:
                chunk = min(1.0, sleep_secs - slept)
                time.sleep(chunk)
                slept += chunk


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="autobackup - 定时自动备份 MySQL / PostgreSQL / 文件目录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s -c config.yaml              启动调度器
  %(prog)s -c config.yaml --now        立即执行所有任务
  %(prog)s -c config.yaml --now -t mysql_prod  立即执行指定任务
  %(prog)s -c config.yaml --next       查看下次执行时间
  %(prog)s -c config.yaml --web        启动 Web 管理界面
  %(prog)s --encrypt-password 'secret' 加密密码（需 AUTOBACKUP_KEY）
        """,
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径 (默认: config.yaml)")
    parser.add_argument("--now", action="store_true", help="立即执行备份任务")
    parser.add_argument("--next", action="store_true", help="查看下次执行时间")
    parser.add_argument("-t", "--task", help="指定任务名称（配合 --now 使用）")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志输出")
    parser.add_argument("--encrypt-password", metavar="PASSWORD", help="加密密码并输出 enc: 字符串")
    parser.add_argument("--web", action="store_true", help="启动 Web 管理界面")
    parser.add_argument("--host", default=None, help="Web 监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Web 监听端口 (默认 8080)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.encrypt_password:
        try:
            encrypted = encrypt_password(args.encrypt_password)
            print(encrypted)
            return 0
        except Exception as exc:
            print(f"加密失败: {exc}", file=sys.stderr)
            return 1

    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"加载配置失败: {exc}", file=sys.stderr)
        return 1

    global_cfg = config.get("global", {})
    log_dir = global_cfg.get("log_dir", "./logs")
    logger = setup_logging(log_dir, verbose=args.verbose)
    notifier = Notifier(config, logger)
    history = HistoryStore(log_dir)

    if args.web:
        from web import run_web_server
        web_cfg = global_cfg.get("web", {})
        host = args.host or web_cfg.get("host", "0.0.0.0")
        port = args.port or int(web_cfg.get("port", 8080))
        run_web_server(args.config, config, logger, notifier, history, host, port)
        return 0

    if args.next:
        tasks = config.get("tasks", [])
        show_next_runs(tasks, logger)
        return 0

    if args.now:
        results = run_tasks_now(config, notifier, logger, task_filter=args.task, history=history)
        if args.task and not results:
            logger.error("未找到任务: %s", args.task)
            return 1
        failed = sum(1 for r in results if not r.success)
        return 1 if failed else 0

    scheduler = Scheduler(config, notifier, logger, history)
    scheduler.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
