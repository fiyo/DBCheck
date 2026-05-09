# -*- coding: utf-8 -*-
#
# Copyright (c) 2024 DBCheck Contributors
# sdfiyon@gmail.com
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License.
# See LICENSE or visit https://opensource.org/licenses/MIT for full license text.
#
"""
DBCheck Pro Backup Module
数据库备份模块
"""

from .base import BaseBackupEngine, BackupResult
from .mysql_backup import MySQLBackupEngine
from .pg_backup import PGBackupEngine
from .oracle_backup import OracleBackupEngine
from .sqlserver_backup import SQLServerBackupEngine
from .manager import BackupManager, get_backup_manager

__all__ = [
    "BaseBackupEngine",
    "BackupResult",
    "MySQLBackupEngine",
    "PGBackupEngine",
    "OracleBackupEngine",
    "SQLServerBackupEngine",
    "BackupManager",
    "get_backup_manager",
]
