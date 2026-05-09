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
DBCheck Pro Backup - Oracle Engine
基于 expdp (Data Pump) 的 Oracle 备份引擎
"""

import os
import time
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from .base import BaseBackupEngine, BackupResult

logger = logging.getLogger(__name__)


class OracleBackupEngine(BaseBackupEngine):
    """Oracle 备份引擎（expdp Data Pump）"""

    DB_TYPE = "oracle"

    def backup(self, instance_id: str, conn_info: Dict[str, Any],
               backup_type: str = "full", schemas: List[str] = None,
               tables: List[str] = None, **kwargs) -> BackupResult:
        """
        执行 Oracle 备份
        参数:
          instance_id: 实例标识
          conn_info: {'host','port','user','password','service_name','sysdba'}
          backup_type: 'full' | 'schema'
          schemas: 指定 schema 列表，不传则备份所有用户 schema
        """
        start = time.time()
        host = conn_info.get("host", "localhost")
        port = conn_info.get("port", 1521)
        user = conn_info.get("user", "system")
        password = conn_info.get("password", "")
        service_name = conn_info.get("service_name", "orcl")
        sysdba = conn_info.get("sysdba", False)
        exec_mode = conn_info.get("exec_mode", "local")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # SSH 模式下 expdp 先写到远程临时目录，再 SFTP 下载
        remote_tmp = None
        if exec_mode == "ssh":
            remote_tmp = f"/tmp/dbcheck_backup_{timestamp}"
            dump_dir_path = os.path.join(self._get_instance_dir(instance_id), timestamp)
            os.makedirs(dump_dir_path, exist_ok=True)
        else:
            dump_dir_path = os.path.join(self._get_instance_dir(instance_id), timestamp)
            os.makedirs(dump_dir_path, exist_ok=True)

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # 构建连接串
            if sysdba:
                connect_str = f'"{user}/{password}@//{host}:{port}/{service_name} AS SYSDBA"'
            else:
                connect_str = f'"{user}/{password}@//{host}:{port}/{service_name}"'

            dp_name = f"DBCheck_{instance_id}_{timestamp}"

            # SSH 模式：创建远程临时目录 + Oracle DIRECTORY + source 环境
            oracle_prefix = ""
            if exec_mode == "ssh":
                oracle_prefix = (
                    f"mkdir -p {remote_tmp} 2>/dev/null; "
                    f"for f in ~/.bash_profile ~/.bashrc /etc/profile ~/.profile; do test -f $f && . $f 2>/dev/null; done; "
                    f"echo \"CREATE OR REPLACE DIRECTORY DBCheck_DUMP AS '{remote_tmp}';\" "
                    f"| sqlplus -s {user}/{password}@//{host}:{port}/{service_name} >/dev/null 2>&1; "
                )

            # 构建 expdp 命令
            cmd = [
                oracle_prefix + "expdp" if oracle_prefix else "expdp",
                f"{user}/{password}@//{host}:{port}/{service_name}",
                f"DIRECTORY=DBCheck_DUMP",
                f"DUMPFILE={dp_name}_%U.dmp",
                f"LOGFILE={dp_name}.log",
                f"JOB_NAME=DBCheck_{instance_id}",
                "-c",  # compression=all (11g+)
            ]

            # 备份范围
            if backup_type == "schema":
                if schemas:
                    cmd.append(f"SCHEMAS={','.join(schemas)}")
                else:
                    # 获取所有非系统 schema
                    schemas = self._get_user_schemas(host, port, user, password,
                                                      service_name, sysdba)
                    if schemas:
                        cmd.append(f"SCHEMAS={','.join(schemas)}")
            else:
                cmd.append("FULL=Y")

            logger.info(f"Oracle 备份开始: {dp_name}")
            result = self._run_backup_cmd(cmd, conn_info, timeout=7200)

            if result.returncode != 0:
                # 如果 directory 不存在，尝试创建
                if "ORA-39002" in result.stderr or "directory" in result.stderr.lower():
                    logger.info("Directory 不存在，尝试创建...")
                    self._create_dump_dir(host, port, user, password,
                                          service_name, dump_dir_name,
                                          dump_dir_path, sysdba)
                    # 重试
                    result = self._run_backup_cmd(cmd, conn_info, timeout=7200)

            if result.returncode != 0:
                return BackupResult(False,
                                    f"expdp 失败: {result.stderr}",
                                    file_path=dump_dir_path)

            # SSH 模式：SFTP 下载远程文件
            if exec_mode == "ssh":
                logger.info(f"SFTP 下载远程文件: {remote_tmp} -> {dump_dir_path}")
                self._sftp_download_dir(self._get_exec_env(conn_info), remote_tmp, dump_dir_path)
                # 清理远程临时目录
                cleanup_cmd = [f"rm -rf {remote_tmp}"]
                self._run_backup_cmd(cleanup_cmd, conn_info, timeout=30)

            # 收集导出文件
            dmp_files = []
            log_file = os.path.join(dump_dir_path, f"{dp_name}.log")
            for f in os.listdir(dump_dir_path):
                full = os.path.join(dump_dir_path, f)
                if f.endswith(".dmp") or f.endswith(".log"):
                    dmp_files.append(f)
                    if f.endswith(".log"):
                        log_file = full

            total_size = sum(os.path.getsize(os.path.join(dump_dir_path, f))
                             for f in dmp_files)

            # manifest
            manifest = {
                "instance_id": instance_id, "db_type": "oracle",
                "backup_type": backup_type, "schemas": schemas,
                "files": dmp_files, "total_size": total_size,
                "timestamp": timestamp, "host": host, "port": port,
                "service_name": service_name,
            }
            with open(os.path.join(dump_dir_path, "manifest.json"),
                      "w", encoding="utf-8") as f:
                import json
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            duration = time.time() - start
            logger.info(f"Oracle 备份完成: {instance_id}, "
                        f"{total_size:,} bytes, {duration:.1f}s")

            return BackupResult(True, f"成功导出 {len(dmp_files)} 个文件",
                                file_path=dump_dir_path, file_size=total_size,
                                duration=duration, backup_type=backup_type)

        except Exception as e:
            duration = time.time() - start
            err_msg = str(e)
            hint = ""
            if "命令未找到" in err_msg:
                hint = ("\n💡 Oracle Data Pump (expdp) 必须在数据库服务器上运行！\n"
                        "   → Docker: 选择执行模式=Docker, 填写 Oracle 容器名\n"
                        "   → 远程: 选择执行模式=SSH")
            logger.error(f"Oracle 备份异常: {e}{hint}")
            return BackupResult(False, err_msg + hint, duration=duration)

    def restore(self, backup_file: str, conn_info: Dict[str, Any],
                target_schema: str = None, **kwargs) -> BackupResult:
        """从 dmp 文件恢复"""
        # Oracle 恢复通常需要 impdp，这里提供入口，实际恢复建议在 DBA 指导下进行
        return BackupResult(False,
                            "Oracle 恢复操作风险较高，请使用 impdp 手动执行")

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
                        "schemas": m.get("schemas", []),
                        "backup_type": m.get("backup_type", "full"),
                        "files": m.get("files", []),
                    })
                except Exception:
                    pass
        return backups

    # ── 辅助方法 ──────────────────────────────────────────

    def _get_user_schemas(self, host, port, user, password,
                          service_name, sysdba) -> List[str]:
        """获取非系统用户 schema 列表"""
        # 这一步需要 SQL*Plus 或 Python oracledb
        # 这里用 Python 方式（如果 oracledb 可用）
        try:
            import oracledb
            params = {
                "host": host, "port": port, "user": user,
                "password": password, "service_name": service_name,
            }
            conn = oracledb.connect(**params)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT username FROM dba_users
                WHERE account_status='OPEN'
                  AND username NOT IN (
                    'SYS','SYSTEM','DBSNMP','XDB','OUTLN','APPQOSSYS',
                    'ORACLE_OCM','DIP','ANONYMOUS','XS$NULL'
                  )
                  AND username NOT LIKE 'APEX%'
                  AND username NOT LIKE 'FLOWS%'
                ORDER BY username
            """)
            schemas = [row[0] for row in cursor.fetchall()]
            conn.close()
            return schemas
        except Exception as e:
            logger.warning(f"获取 schema 列表失败: {e}")
            return []

    def _create_dump_dir(self, host, port, user, password,
                         service_name, dir_name, dir_path, sysdba):
        """创建 Oracle directory 对象"""
        try:
            import oracledb
            params = {
                "host": host, "port": port, "user": user,
                "password": password, "service_name": service_name,
            }
            conn = oracledb.connect(**params)
            cursor = conn.cursor()
            cursor.execute(f"""
                CREATE OR REPLACE DIRECTORY {dir_name} AS '{dir_path}'
            """)
            cursor.execute(f"GRANT READ,WRITE ON DIRECTORY {dir_name} TO PUBLIC")
            conn.commit()
            conn.close()
            logger.info(f"创建 Oracle directory: {dir_name} -> {dir_path}")
        except Exception as e:
            logger.warning(f"创建 directory 失败: {e}")
