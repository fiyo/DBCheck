# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck Pro Backup - Manager
统一备份管理器
"""

import os
import json
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from .base import BaseBackupEngine, BackupResult
from .mysql_backup import MySQLBackupEngine
from .pg_backup import PGBackupEngine
from .oracle_backup import OracleBackupEngine
from .sqlserver_backup import SQLServerBackupEngine

logger = logging.getLogger(__name__)

# 引擎注册表
_ENGINE_REGISTRY = {
    "mysql": MySQLBackupEngine,
    "postgresql": PGBackupEngine,
    "pg": PGBackupEngine,
    "oracle": OracleBackupEngine,
    "sqlserver": SQLServerBackupEngine,
    # P2 阶段扩展:
    # "dm": DMBackupEngine,
    # "tidb": TiDBBackupEngine,
}


class BackupManager:
    """统一备份管理器（单例）"""

    def __init__(self, data_dir: str = "pro_data",
                 backup_dir: str = None,
                 config_path: str = None):
        self.data_dir = data_dir
        self.backup_dir = backup_dir or os.path.join(data_dir, "..", "backups")
        self.config_path = config_path or os.path.join(data_dir, "backup_config.json")
        self.db_file = os.path.join(data_dir, "backup_history.db")

        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

        self.config = self._load_config()
        self._init_database()

        # 引擎缓存
        self._engines: Dict[str, BaseBackupEngine] = {}

    # ── 配置管理 ──────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        """加载备份配置"""
        defaults = {
            "backup_dir": self.backup_dir,
            "retention_days": 30,
            "compression": True,
            "verify_after_backup": True,
            "slack_before_cleanup": 7,
            "per_db": {
                "mysql": {},
                "postgresql": {},
            },
        }
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                defaults.update(loaded)
            except Exception as e:
                logger.warning(f"加载备份配置失败: {e}")
        return defaults

    def save_config(self, config: Dict = None) -> None:
        """保存配置"""
        if config:
            self.config.update(config)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    # ── 数据库 ────────────────────────────────────────────

    def _init_database(self):
        """初始化备份历史数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backup_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                instance_name TEXT,
                db_type TEXT NOT NULL,
                backup_type TEXT DEFAULT 'full',
                file_path TEXT,
                file_size INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                message TEXT,
                checksum TEXT,
                databases TEXT,
                started_at TEXT,
                finished_at TEXT,
                duration REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backup_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                cron_expression TEXT NOT NULL,
                backup_type TEXT DEFAULT 'full',
                retention_days INTEGER DEFAULT 30,
                enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    # ── 引擎管理 ──────────────────────────────────────────

    def get_engine(self, db_type: str) -> Optional[BaseBackupEngine]:
        """获取或创建引擎实例"""
        db_type = db_type.lower()
        if db_type not in _ENGINE_REGISTRY:
            logger.warning(f"不支持的数据库类型: {db_type}")
            return None

        if db_type not in self._engines:
            per_db_config = self.config.get("per_db", {}).get(db_type, {})
            merged_config = {**self.config, **per_db_config}
            engine_cls = _ENGINE_REGISTRY[db_type]
            self._engines[db_type] = engine_cls(
                backup_dir=self.backup_dir,
                config=merged_config,
            )
        return self._engines[db_type]

    def get_supported_types(self) -> List[str]:
        """获取已支持的数据库类型"""
        return list(_ENGINE_REGISTRY.keys())

    # ── 备份操作 ──────────────────────────────────────────

    def backup(self, instance_id: str, db_type: str,
               conn_info: Dict[str, Any],
               backup_type: str = "full",
               databases: List[str] = None,
               tables: List[str] = None,
               instance_name: str = "") -> BackupResult:
        """执行备份并记录历史"""
        engine = self.get_engine(db_type)
        if engine is None:
            return BackupResult(False, f"不支持的数据库类型: {db_type}")

        # 记录开始
        record_id = self._insert_history(
            instance_id, instance_name, db_type, backup_type, status="running"
        )

        # 执行
        result = engine.backup(instance_id, conn_info, backup_type, databases, tables)

        # 更新记录
        self._update_history(record_id, result)

        return result

    def restore(self, backup_file: str, db_type: str,
                conn_info: Dict[str, Any],
                target_db: str = None) -> BackupResult:
        """恢复备份"""
        engine = self.get_engine(db_type)
        if engine is None:
            return BackupResult(False, f"不支持的数据库类型: {db_type}")
        return engine.restore(backup_file, conn_info, target_db)

    def list_backups(self, instance_id: str, db_type: str) -> List[Dict]:
        """列出实例备份"""
        engine = self.get_engine(db_type)
        if engine is None:
            return []
        return engine.list_backups(instance_id)

    def cleanup(self, instance_id: str, db_type: str = None,
                days: int = None) -> int:
        """清理过期备份"""
        if db_type:
            engine = self.get_engine(db_type)
            if engine:
                return engine.cleanup(instance_id, days)
            return 0

        total = 0
        for engine_type in _ENGINE_REGISTRY:
            engine = self.get_engine(engine_type)
            if engine:
                total += engine.cleanup(instance_id, days)
        return total

    # ── 备份历史 ──────────────────────────────────────────

    def _insert_history(self, instance_id: str, instance_name: str,
                        db_type: str, backup_type: str,
                        status: str = "running") -> int:
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO backup_history
            (instance_id, instance_name, db_type, backup_type, status, started_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (instance_id, instance_name, db_type, backup_type, status,
              datetime.now().isoformat()))
        conn.commit()
        rid = cursor.lastrowid
        conn.close()
        return rid

    def _update_history(self, record_id: int, result: BackupResult):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        status = "success" if result.success else "failed"
        cursor.execute("""
            UPDATE backup_history
            SET status=?, file_path=?, file_size=?,
                duration=?, checksum=?, message=?, finished_at=?
            WHERE id=?
        """, (
            status, result.file_path, result.file_size,
            result.duration, result.checksum, result.message,
            datetime.now().isoformat(), record_id
        ))
        conn.commit()
        conn.close()

    def get_history(self, instance_id: str = None,
                    limit: int = 50) -> List[Dict]:
        """获取备份历史"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if instance_id:
            cursor.execute("""
                SELECT * FROM backup_history
                WHERE instance_id = ?
                ORDER BY started_at DESC LIMIT ?
            """, (instance_id, limit))
        else:
            cursor.execute("""
                SELECT * FROM backup_history
                ORDER BY started_at DESC LIMIT ?
            """, (limit,))

        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def get_statistics(self) -> Dict[str, Any]:
        """获取备份统计"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM backup_history")
        total = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM backup_history WHERE status='success'")
        success = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM backup_history WHERE status='failed'")
        failed = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COALESCE(SUM(file_size), 0) FROM backup_history "
            "WHERE status='success'")
        total_size = cursor.fetchone()[0]

        # 最近24小时
        cursor.execute("""
            SELECT COUNT(*) FROM backup_history
            WHERE started_at >= datetime('now', '-1 day')
        """)
        last_24h = cursor.fetchone()[0]

        conn.close()

        return {
            "total_backups": total,
            "success": success,
            "failed": failed,
            "total_size_bytes": total_size,
            "last_24h": last_24h,
        }

    # ── 调度配置 ──────────────────────────────────────────

    def get_schedules(self) -> List[Dict]:
        """获取备份调度配置"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM backup_schedules ORDER BY id")
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def add_schedule(self, instance_id: str, cron_expression: str,
                     backup_type: str = "full",
                     retention_days: int = 30) -> int:
        """添加备份调度"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO backup_schedules
            (instance_id, cron_expression, backup_type, retention_days)
            VALUES (?, ?, ?, ?)
        """, (instance_id, cron_expression, backup_type, retention_days))
        conn.commit()
        rid = cursor.lastrowid
        conn.close()
        return rid

    def remove_schedule(self, schedule_id: int):
        """删除备份调度"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM backup_schedules WHERE id=?", (schedule_id,))
        conn.commit()
        conn.close()


# ── 全局单例 ──────────────────────────────────────────────

_backup_manager: Optional[BackupManager] = None


def get_backup_manager() -> BackupManager:
    """获取备份管理器单例"""
    global _backup_manager
    if _backup_manager is None:
        _backup_manager = BackupManager()
    return _backup_manager
