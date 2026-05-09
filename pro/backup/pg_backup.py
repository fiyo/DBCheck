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
DBCheck Pro Backup - PostgreSQL Engine
基于 pg_dump 的 PostgreSQL 备份引擎
"""

import os
import time
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from .base import BaseBackupEngine, BackupResult

logger = logging.getLogger(__name__)


class PGBackupEngine(BaseBackupEngine):
    """PostgreSQL 备份引擎（pg_dump）"""

    DB_TYPE = "postgresql"

    # ── 备份 ──────────────────────────────────────────────

    def backup(self, instance_id: str, conn_info: Dict[str, Any],
               backup_type: str = "full", databases: List[str] = None,
               tables: List[str] = None, **kwargs) -> BackupResult:
        """
        执行 PostgreSQL 备份
        参数:
          instance_id: 实例标识
          conn_info: {
            'host','port','user','password','database',
            'exec_mode': 'local'(默认)|'docker'|'ssh',
            'docker': {'container': 'pg-container'},
            'ssh': {'host','port','user','key_file'}
          }
          backup_type: 'full' | 'schema' | 'data'
        """
        start = time.time()

        host = conn_info.get("host", "localhost")
        port = conn_info.get("port", 5432)
        user = conn_info.get("user", "postgres")
        password = conn_info.get("password", "")
        exec_mode = conn_info.get("exec_mode", "local")

        # Docker/SSH 模式下数据库在本容器/本机，强制连 localhost + 默认端口
        if exec_mode == "docker":
            host = "localhost"
            port = 5432
        elif exec_mode == "ssh":
            host = "localhost"

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_dir = os.path.join(self._get_instance_dir(instance_id), timestamp)
            os.makedirs(base_dir, exist_ok=True)

            env_vars = {}
            if password:
                env_vars["PGPASSWORD"] = password

            # 获取数据库列表
            if not databases:
                default_db = conn_info.get("database", "postgres")
                list_cmd = [
                    "psql", "-h", host, "-p", str(port), "-U", user,
                    "-d", default_db, "-t", "-c",
                    "SELECT datname FROM pg_database "
                    "WHERE datistemplate = false AND datname != 'postgres'"
                ]
                list_result = self._run_backup_cmd(
                    list_cmd, conn_info, timeout=120, env_vars=env_vars
                )
                out = list_result.stdout or ""
                databases = [db.strip() for db in out.strip().split("\n") if db.strip()]
                if not databases:
                    databases = [default_db]
                else:
                    logger.info(f"自动获取数据库列表: {databases}")

            # 逐库备份
            total_size = 0
            backup_files = []
            for db in databases:
                dump_file = os.path.join(base_dir, f"{db}.sql")
                cmd = [
                    "pg_dump", "-h", host, "-p", str(port), "-U", user,
                    "-d", db, "--no-owner", "--no-acl", "--encoding=UTF8",
                ]
                if backup_type == "schema":
                    cmd.append("--schema-only")
                elif backup_type == "data":
                    cmd.append("--data-only")

                result = self._run_backup_cmd(
                    cmd, conn_info, timeout=3600, env_vars=env_vars
                )

                if result.returncode != 0:
                    return BackupResult(False,
                        f"备份 {db} 失败: {result.stderr}")

                with open(dump_file, "w", encoding="utf-8") as f:
                    f.write(result.stdout or "")

                if self.compression:
                    self.compress_file(dump_file)
                    dump_file += ".gz"

                size = os.path.getsize(dump_file)
                total_size += size
                backup_files.append(dump_file)
                logger.info(f"  {db}: {size:,} bytes -> {os.path.basename(dump_file)}")

            # 备份全局对象
            globals_file = os.path.join(base_dir, "globals.sql")
            globals_cmd = [
                "pg_dumpall", "-h", host, "-p", str(port), "-U", user,
                "--globals-only"
            ]
            g_result = self._run_backup_cmd(
                globals_cmd, conn_info, timeout=60, env_vars=env_vars
            )
            if g_result.returncode == 0:
                with open(globals_file, "w", encoding="utf-8") as f:
                    f.write(g_result.stdout or "")
                if self.compression:
                    self.compress_file(globals_file)
                    globals_file += ".gz"
                backup_files.append(globals_file)
                total_size += os.path.getsize(globals_file)
            else:
                logger.warning(f"备份全局对象失败: {g_result.stderr}")

            # manifest
            manifest = {
                "instance_id": instance_id,
                "db_type": "postgresql",
                "backup_type": backup_type,
                "databases": databases,
                "files": [os.path.basename(f) for f in backup_files],
                "total_size": total_size,
                "timestamp": timestamp,
                "host": host, "port": port,
            }
            with open(os.path.join(base_dir, "manifest.json"),
                      "w", encoding="utf-8") as f:
                import json
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            duration = time.time() - start
            logger.info(f"PG 备份完成: {instance_id}, {len(databases)} 个库, "
                        f"{total_size:,} bytes, {duration:.1f}s")

            return BackupResult(
                True, f"成功备份 {len(databases)} 个数据库 + 全局对象",
                file_path=base_dir, file_size=total_size,
                duration=duration, backup_type=backup_type
            )

        except Exception as e:
            duration = time.time() - start
            err_msg = str(e)
            hint = ""
            if "命令未找到" in err_msg:
                hint = ("\n💡 提示: PostgreSQL 在 Docker 中?\n"
                        "   → 在数据源管理中设置 exec_mode=docker, docker.container=容器名\n"
                        "   → 或在宿主机安装: apt install postgresql-client\n"
                        "💡 远程主机?\n"
                        "   → 设置 exec_mode=ssh 并提供 SSH 配置")
            logger.error(f"PG 备份异常: {e}{hint}")
            return BackupResult(False, err_msg + hint, duration=duration)

    # ── 恢复 ──────────────────────────────────────────────

    def restore(self, backup_file: str, conn_info: Dict[str, Any],
                target_db: str = None, **kwargs) -> BackupResult:
        """
        从 SQL 备份恢复
        参数:
          backup_file: .sql 或 .sql.gz 文件路径
          conn_info: 连接信息
          target_db: 目标数据库
        """
        start = time.time()
        host = conn_info.get("host", "localhost")
        port = conn_info.get("port", 5432)
        user = conn_info.get("user", "postgres")
        password = conn_info.get("password", "")

        if not os.path.exists(backup_file):
            return BackupResult(False, f"备份文件不存在: {backup_file}")

        try:
            env = os.environ.copy()
            if password:
                env["PGPASSWORD"] = password

            # 推断目标数据库
            fname = os.path.basename(backup_file).replace(".gz", "").replace(".sql", "")
            if target_db is None:
                target_db = fname

            # 跳过 globals 文件的库创建
            if fname == "globals":
                target_db = None

            # 解压
            sql_file = backup_file
            is_gz = backup_file.endswith(".gz")
            if is_gz:
                import gzip
                sql_file = backup_file[:-3]
                with gzip.open(backup_file, 'rb') as f_in:
                    with open(sql_file, 'wb') as f_out:
                        f_out.write(f_in.read())

            if target_db:
                # 先断开其他连接
                term_cmd = [
                    "psql", "-h", host, "-p", str(port), "-U", user,
                    "-d", "postgres", "-c",
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{target_db}' AND pid <> pg_backend_pid()"
                ]
                self._run_cmd(term_cmd, timeout=30, env=env)

                # 删除并重建数据库
                drop_cmd = [
                    "psql", "-h", host, "-p", str(port), "-U", user,
                    "-d", "postgres", "-c",
                    f"DROP DATABASE IF EXISTS \"{target_db}\""
                ]
                self._run_cmd(drop_cmd, timeout=30, env=env)

                create_cmd = [
                    "psql", "-h", host, "-p", str(port), "-U", user,
                    "-d", "postgres", "-c",
                    f"CREATE DATABASE \"{target_db}\" ENCODING 'UTF8'"
                ]
                self._run_cmd(create_cmd, timeout=30, env=env)

                restore_cmd = [
                    "psql", "-h", host, "-p", str(port), "-U", user,
                    "-d", target_db, "-f", sql_file
                ]
            else:
                # globals 恢复
                restore_cmd = [
                    "psql", "-h", host, "-p", str(port), "-U", user,
                    "-d", "postgres", "-f", sql_file
                ]

            import subprocess
            proc_result = subprocess.run(
                restore_cmd,
                capture_output=True, text=True,
                timeout=3600, env=env
            )

            if is_gz and os.path.exists(sql_file):
                os.remove(sql_file)

            duration = time.time() - start
            if proc_result.returncode != 0:
                return BackupResult(False, proc_result.stderr, duration=duration)

            logger.info(f"PG 恢复完成: {target_db or 'globals'}, {duration:.1f}s")
            return BackupResult(True, f"成功恢复 {target_db or '全局对象'}",
                                duration=duration)

        except Exception as e:
            duration = time.time() - start
            return BackupResult(False, str(e), duration=duration)

    # ── 列表 ──────────────────────────────────────────────

    def list_backups(self, instance_id: str) -> List[Dict[str, Any]]:
        """列出实例所有备份"""
        backups = []
        instance_dir = self._get_instance_dir(instance_id)

        for entry in sorted(os.scandir(instance_dir),
                            key=lambda e: e.name, reverse=True):
            if not entry.is_dir():
                continue
            manifest_file = os.path.join(entry.path, "manifest.json")
            if os.path.exists(manifest_file):
                try:
                    import json
                    with open(manifest_file, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    total = sum(
                        os.path.getsize(os.path.join(entry.path, f))
                        for f in manifest.get("files", [])
                        if os.path.exists(os.path.join(entry.path, f))
                    )
                    backups.append({
                        "timestamp": manifest.get("timestamp", entry.name),
                        "path": entry.path,
                        "size": total,
                        "databases": manifest.get("databases", []),
                        "backup_type": manifest.get("backup_type", "full"),
                        "files": manifest.get("files", []),
                    })
                except Exception:
                    pass

        return backups
