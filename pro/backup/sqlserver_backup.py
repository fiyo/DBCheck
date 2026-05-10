# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck Pro Backup - SQL Server Engine
基于 T-SQL BACKUP DATABASE 的 SQL Server 备份引擎
"""

import os
import time
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from .base import BaseBackupEngine, BackupResult

logger = logging.getLogger(__name__)


class SQLServerBackupEngine(BaseBackupEngine):
    """SQL Server 备份引擎（T-SQL BACKUP DATABASE）"""

    DB_TYPE = "sqlserver"

    def backup(self, instance_id: str, conn_info: Dict[str, Any],
               backup_type: str = "full", databases: List[str] = None,
               tables: List[str] = None, **kwargs) -> BackupResult:
        """
        执行 SQL Server 备份
        参数:
          instance_id: 实例标识
          conn_info: {'host','port','user','password'} 或 ODBC 连接串
          backup_type: 'full' | 'diff' | 'log'
          databases: 指定数据库，不传则备份所有非系统库
        """
        start = time.time()
        host = conn_info.get("host", "localhost")
        port = conn_info.get("port", 1433)
        user = conn_info.get("user", "sa")
        password = conn_info.get("password", "")
        odbc_conn = conn_info.get("odbc_conn")  # 可选：直接传 ODBC 连接串

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_dir = os.path.join(self._get_instance_dir(instance_id), timestamp)
            os.makedirs(base_dir, exist_ok=True)

            conn = self._connect(host, port, user, password, odbc_conn)
            cursor = conn.cursor()

            # 获取数据库列表
            if not databases:
                cursor.execute("""
                    SELECT name FROM sys.databases
                    WHERE name NOT IN ('master','tempdb','model','msdb')
                      AND state_desc = 'ONLINE'
                    ORDER BY name
                """)
                databases = [row[0] for row in cursor.fetchall()]
                logger.info(f"自动获取数据库列表: {databases}")

            total_size = 0
            backup_files = []

            for db in databases:
                if backup_type == "full":
                    ext = "bak"
                elif backup_type == "diff":
                    ext = "dif"
                elif backup_type == "log":
                    ext = "trn"
                else:
                    ext = "bak"

                bak_file = os.path.join(base_dir, f"{db}_{backup_type}.{ext}")
                bak_file = bak_file.replace("\\", "\\\\")

                sql = self._build_backup_sql(db, bak_file, backup_type)
                logger.info(f"执行 SQL: {sql[:120]}...")
                cursor.execute(sql)

                # 等待完成
                while cursor.nextset():
                    pass

                conn.commit()

                size = os.path.getsize(bak_file) if os.path.exists(bak_file) else 0
                total_size += size
                backup_files.append(os.path.basename(bak_file))
                logger.info(f"  {db}: {size:,} bytes")

            conn.close()

            # manifest
            manifest = {
                "instance_id": instance_id, "db_type": "sqlserver",
                "backup_type": backup_type, "databases": databases,
                "files": backup_files, "total_size": total_size,
                "timestamp": timestamp, "host": host, "port": port,
            }
            with open(os.path.join(base_dir, "manifest.json"),
                      "w", encoding="utf-8") as f:
                import json
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            duration = time.time() - start
            logger.info(f"SQL Server 备份完成: {instance_id}, "
                        f"{len(databases)} 个库, {total_size:,} bytes, {duration:.1f}s")

            return BackupResult(True, f"成功备份 {len(databases)} 个数据库",
                                file_path=base_dir, file_size=total_size,
                                duration=duration, backup_type=backup_type)

        except Exception as e:
            duration = time.time() - start
            logger.error(f"SQL Server 备份异常: {e}")
            return BackupResult(False, str(e), duration=duration)

    def restore(self, backup_file: str, conn_info: Dict[str, Any],
                target_db: str = None, **kwargs) -> BackupResult:
        """从 bak 文件恢复"""
        start = time.time()
        host = conn_info.get("host", "localhost")
        port = conn_info.get("port", 1433)
        user = conn_info.get("user", "sa")
        password = conn_info.get("password", "")
        odbc_conn = conn_info.get("odbc_conn")

        if not os.path.exists(backup_file):
            return BackupResult(False, f"备份文件不存在: {backup_file}")

        # 推断目标数据库
        if target_db is None:
            fname = os.path.basename(backup_file)
            target_db = fname.split("_")[0]

        try:
            conn = self._connect(host, port, user, password, odbc_conn)
            cursor = conn.cursor()

            bak_file_escaped = backup_file.replace("\\", "\\\\")

            # 先获取备份文件的逻辑文件名
            cursor.execute(f"""
                RESTORE FILELISTONLY FROM DISK = '{bak_file_escaped}'
            """)
            filelist = cursor.fetchall()
            while cursor.nextset():
                pass

            # 构建 MOVE 子句，还原到默认数据目录
            cursor.execute("SELECT SERVERPROPERTY('InstanceDefaultDataPath')")
            data_path = cursor.fetchone()[0] or ""
            cursor.execute("SELECT SERVERPROPERTY('InstanceDefaultLogPath')")
            log_path = cursor.fetchone()[0] or ""

            move_clauses = []
            for row in filelist:
                logical_name = row[0]
                file_type = row[2]  # D=Data, L=Log
                if file_type == "L":
                    dest = os.path.join(log_path, f"{target_db}_log.ldf")
                else:
                    dest = os.path.join(data_path, f"{target_db}.mdf")
                move_clauses.append(
                    f"MOVE '{logical_name}' TO '{dest.replace(chr(92), '/')}'"
                )

            move_sql = ",\n".join(move_clauses)
            restore_sql = f"""
                RESTORE DATABASE [{target_db}]
                FROM DISK = '{bak_file_escaped}'
                WITH REPLACE,
                {move_sql},
                STATS = 10
            """
            logger.info(f"恢复: {restore_sql[:120]}...")
            cursor.execute(restore_sql)
            while cursor.nextset():
                pass
            conn.commit()
            conn.close()

            duration = time.time() - start
            return BackupResult(True, f"成功恢复到 {target_db}",
                                duration=duration)

        except Exception as e:
            duration = time.time() - start
            return BackupResult(False, str(e), duration=duration)

    def list_backups(self, instance_id: str) -> List[Dict[str, Any]]:
        backups = []
        instance_dir = self._get_instance_dir(instance_id)
        for entry in sorted(os.scandir(instance_dir),
                            key=lambda e: e.name, reverse=True):
            if not entry.is_dir():
                continue
            mf = os.path.join(entry.path, "manifest.json")
            if os.path.exists(mf):
                try:
                    import json
                    with open(mf, "r", encoding="utf-8") as f:
                        m = json.load(f)
                    total = sum(
                        os.path.getsize(os.path.join(entry.path, x))
                        for x in m.get("files", [])
                        if os.path.exists(os.path.join(entry.path, x))
                    )
                    backups.append({
                        "timestamp": m.get("timestamp", entry.name),
                        "path": entry.path, "size": total,
                        "databases": m.get("databases", []),
                        "backup_type": m.get("backup_type", "full"),
                        "files": m.get("files", []),
                    })
                except Exception:
                    pass
        return backups

    # ── 辅助 ──────────────────────────────────────────────

    def _connect(self, host, port, user, password, odbc_conn=None):
        import pyodbc
        if odbc_conn:
            conn_str = odbc_conn
        else:
            conn_str = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={host},{port};"
                f"UID={user};PWD={password};"
                f"TrustServerCertificate=yes;"
                f"Connection Timeout=30;"
            )
        conn = pyodbc.connect(conn_str, autocommit=True)
        return conn

    def _build_backup_sql(self, db: str, filepath: str, backup_type: str) -> str:
        type_map = {"full": "DATABASE", "diff": "DATABASE", "log": "LOG"}
        diff_clause = ", DIFFERENTIAL" if backup_type == "diff" else ""
        return f"""
            BACKUP {type_map[backup_type]} [{db}]
            TO DISK = '{filepath}'
            WITH COMPRESSION, CHECKSUM, INIT,
                 STATS = 10{diff_clause}
        """
