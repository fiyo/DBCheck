# -*- coding: utf-8 -*-
"""
DBCheck Pro Backup - MySQL Engine
基于 mysqldump 的 MySQL 备份引擎
"""

import os
import time
import glob
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from .base import BaseBackupEngine, BackupResult

logger = logging.getLogger(__name__)


class MySQLBackupEngine(BaseBackupEngine):
    """MySQL 备份引擎（mysqldump）"""

    DB_TYPE = "mysql"

    # ── 备份 ──────────────────────────────────────────────

    def backup(self, instance_id: str, conn_info: Dict[str, Any],
               backup_type: str = "full", databases: List[str] = None,
               **kwargs) -> BackupResult:
        """
        执行 MySQL 备份
        参数:
          instance_id: 实例标识
          conn_info: {
            'host','port','user','password',
            'exec_mode': 'local'(默认)|'docker'|'ssh',
            'docker': {'container': 'mysql-container'},
            'ssh': {'host','port','user','key_file'}
          }
          backup_type: 'full' | 'schema'
          databases: 指定数据库列表，不传则备份所有
        """
        start = time.time()

        host = conn_info.get("host", "localhost")
        port = conn_info.get("port", 3306)
        user = conn_info.get("user", "root")
        password = conn_info.get("password", "")
        exec_mode = conn_info.get("exec_mode", "local")

        # 检测密码是否可能未正确解密
        if password and len(password) > 50 and password.count('=') > 2:
            return BackupResult(False,
                "密码解密失败，可能是 .db_key 密钥不匹配。\n"
                "💡 请在「数据源管理」中重新输入密码后保存")

        # Docker/SSH 模式下数据库在本容器/本机，强制连 localhost + 默认端口
        if exec_mode == "docker":
            host = "localhost"
            port = 3306
        elif exec_mode == "ssh":
            host = "localhost"

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_dir = os.path.join(self._get_instance_dir(instance_id), timestamp)
            os.makedirs(base_dir, exist_ok=True)

            # Docker/SSH 模式用 -p 参数直传密码（通用性最好）
            auth_args = []
            if exec_mode in ("docker", "ssh"):
                auth_args = [f"-p{password}"] if password else []
            else:
                auth_args = []

            # 获取数据库列表
            list_cmd = (
                ["mysql", f"-h{host}", f"-P{port}", f"-u{user}"] +
                auth_args +
                ["-N", "-e", "SHOW DATABASES"]
            )
            list_result = self._run_backup_cmd(
                list_cmd, conn_info, timeout=120
            )

            if not databases:
                out = list_result.stdout or ""
                databases = [
                    db.strip() for db in out.strip().split("\n")
                    if db.strip() and db.strip() not in (
                        "information_schema", "performance_schema",
                        "mysql", "sys", "Database")
                ]
                if not databases:
                    if list_result.returncode != 0:
                        return BackupResult(False,
                            f"无法获取数据库列表: {list_result.stderr or '无输出'}\n"
                            f"💡 可能是密码错误，请在「数据源管理」中重新输入密码保存后重试\n"
                            f"💡 或检查 MySQL 是否允许 root@localhost 登录")
                    return BackupResult(False, "数据库列表为空（无用户数据库），无需备份")

            # 逐库备份
            total_size = 0
            backup_files = []
            # 表级备份：解析 db.table 格式
            table_backup = backup_type == "tables" and tables
            if table_backup:
                # 按数据库分组
                db_tables = {}
                for t in (tables or []):
                    parts = t.split(".")
                    if len(parts) == 2:
                        db_tables.setdefault(parts[0], []).append(parts[1])
                databases = list(db_tables.keys())
            for db in databases:
                dump_file = os.path.join(base_dir, f"{db}.sql")
                dump_cmd = (
                    ["mysqldump", f"-h{host}", f"-P{port}", f"-u{user}"] +
                    auth_args +
                    ["--single-transaction", "--routines", "--triggers",
                     "--events", "--hex-blob",
                     "--default-character-set=utf8mb4"]
                )
                if backup_type == "schema":
                    dump_cmd.append("--no-data")
                if table_backup and db in db_tables:
                    dump_cmd.append("--tables")
                    dump_cmd.extend(db_tables[db])
                dump_cmd.append(db)

                result = self._run_backup_cmd(
                    dump_cmd, conn_info, timeout=3600
                )

                if result.returncode != 0:
                    return BackupResult(
                        False, f"备份 {db} 失败: {result.stderr}")

                # 将 stdout 写入文件
                out = result.stdout or ""
                with open(dump_file, "w", encoding="utf-8") as f:
                    f.write(out)

                if self.compression:
                    self.compress_file(dump_file)
                    dump_file += ".gz"

                size = os.path.getsize(dump_file)
                total_size += size
                backup_files.append(dump_file)
                logger.info(f"  {db}: {size:,} bytes -> {os.path.basename(dump_file)}")

            # 生成备份清单
            manifest = {
                "instance_id": instance_id,
                "db_type": "mysql",
                "backup_type": backup_type,
                "databases": databases,
                "files": [os.path.basename(f) for f in backup_files],
                "total_size": total_size,
                "timestamp": timestamp,
                "host": host,
                "port": port,
            }
            manifest_file = os.path.join(base_dir, "manifest.json")
            with open(manifest_file, "w", encoding="utf-8") as f:
                import json
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            duration = time.time() - start
            logger.info(f"MySQL 备份完成: {instance_id}, {len(databases)} 个库, "
                        f"{total_size:,} bytes, {duration:.1f}s")

            return BackupResult(
                True,
                f"成功备份 {len(databases)} 个数据库",
                file_path=base_dir,
                file_size=total_size,
                duration=duration,
                backup_type=backup_type
            )

        except Exception as e:
            duration = time.time() - start
            err_msg = str(e)
            hint = ""
            if "命令未找到" in err_msg:
                hint = ("\n💡 提示: MySQL 在 Docker 中?\n"
                        "   → 在数据源管理中设置 exec_mode=docker, docker.container=容器名\n"
                        "   → 或在宿主机安装: apt install mysql-client / brew install mysql-client\n"
                        "💡 远程主机?\n"
                        "   → 设置 exec_mode=ssh 并提供 SSH 配置")
            logger.error(f"MySQL 备份异常: {e}{hint}")
            return BackupResult(False, err_msg + hint, duration=duration)

    # ── 恢复 ──────────────────────────────────────────────

    def restore(self, backup_file: str, conn_info: Dict[str, Any],
                target_db: str = None, **kwargs) -> BackupResult:
        """
        从 SQL 备份文件恢复
        参数: backup_file: .sql/.sql.gz 路径, conn_info, target_db
        """
        start = time.time()
        host = conn_info.get("host", "localhost")
        port = conn_info.get("port", 3306)
        user = conn_info.get("user", "root")
        password = conn_info.get("password", "")
        exec_mode = conn_info.get("exec_mode", "local")

        if exec_mode == "docker":
            host = "localhost"; port = 3306
        elif exec_mode == "ssh":
            host = "localhost"

        if not os.path.exists(backup_file):
            return BackupResult(False, f"备份文件不存在: {backup_file}")

        try:
            if target_db is None:
                fname = os.path.basename(backup_file).replace(".gz", "").replace(".sql", "")
                target_db = fname

            # 解压
            sql_file = backup_file
            is_gz = backup_file.endswith(".gz")
            if is_gz:
                import gzip
                sql_file = backup_file[:-3]
                if not os.path.exists(sql_file):
                    with gzip.open(backup_file, 'rb') as f_in:
                        with open(sql_file, 'wb') as f_out:
                            f_out.write(f_in.read())

            auth_args = [f"-p{password}"] if password and exec_mode in ("docker", "ssh") else []
            env_vars = {}
            if password and exec_mode == "local":
                env_vars["MYSQL_PWD"] = password

            # 建库
            create_cmd = (
                ["mysql", f"-h{host}", f"-P{port}", f"-u{user}"] + auth_args +
                ["-e", f"CREATE DATABASE IF NOT EXISTS `{target_db}` "
                       f"DEFAULT CHARACTER SET utf8mb4"]
            )
            self._run_backup_cmd(create_cmd, conn_info, timeout=30, env_vars=env_vars)

            # 恢复：stdin 导入 SQL
            import subprocess
            if exec_mode == "docker":
                container = conn_info.get("docker", {}).get("container", "")
                docker_cmd = ["docker", "exec", "-i"]
                if password:
                    docker_cmd.extend(["-e", f"MYSQL_PWD={password}"])
                docker_cmd += [container, "mysql", f"-h{host}", f"-P{port}", f"-u{user}", target_db]
                with open(sql_file, 'rb') as f:
                    proc_result = subprocess.run(
                        docker_cmd, stdin=f, capture_output=True,
                        timeout=3600, encoding="utf-8", errors="replace")
            else:
                cmd = ["mysql", f"-h{host}", f"-P{port}", f"-u{user}", target_db]
                env = os.environ.copy()
                if env_vars: env.update(env_vars)
                with open(sql_file, 'r', encoding='utf-8') as f:
                    proc_result = subprocess.run(
                        cmd, stdin=f, capture_output=True, text=True,
                        timeout=3600, env=env)

            if is_gz and os.path.exists(sql_file) and sql_file != backup_file:
                os.remove(sql_file)

            duration = time.time() - start
            if proc_result.returncode != 0:
                return BackupResult(False, f"恢复失败: {proc_result.stderr}", duration=duration)

            logger.info(f"MySQL 恢复完成: {target_db}, {duration:.1f}s")
            return BackupResult(True, f"成功恢复到 {target_db}", duration=duration)

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
