# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck Pro Backup - Base Engine
备份引擎抽象基类，定义统一接口
"""

import os
import json
import gzip
import hashlib
import subprocess
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class BackupResult:
    """备份结果"""

    def __init__(self, success: bool, message: str = "",
                 file_path: str = "", file_size: int = 0,
                 duration: float = 0, backup_type: str = "full",
                 checksum: str = ""):
        self.success = success
        self.message = message
        self.file_path = file_path
        self.file_size = file_size
        self.duration = duration
        self.backup_type = backup_type
        self.checksum = checksum
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "duration": self.duration,
            "backup_type": self.backup_type,
            "checksum": self.checksum,
            "created_at": self.created_at,
        }

    def __repr__(self):
        status = "OK" if self.success else "FAIL"
        return f"<BackupResult {status}: {self.file_path} ({self.file_size} bytes, {self.duration:.1f}s)>"


class BaseBackupEngine(ABC):
    """备份引擎抽象基类"""

    # 子类需设置
    DB_TYPE: str = "unknown"

    def __init__(self, backup_dir: str = "./backups", config: Dict = None):
        self.backup_dir = backup_dir
        self.config = config or {}
        os.makedirs(backup_dir, exist_ok=True)

        # 通用配置
        self.compression = self.config.get("compression", True)
        self.verify_after = self.config.get("verify_after_backup", True)
        self.retention_days = self.config.get("retention_days", 30)

    # ── 子类必须实现 ──────────────────────────────────────

    @abstractmethod
    def backup(self, instance_id: str, conn_info: Dict[str, Any],
               backup_type: str = "full", **kwargs) -> BackupResult:
        """执行备份，返回 BackupResult"""
        pass

    @abstractmethod
    def restore(self, backup_file: str, conn_info: Dict[str, Any],
                **kwargs) -> BackupResult:
        """从备份文件恢复"""
        pass

    @abstractmethod
    def list_backups(self, instance_id: str) -> List[Dict[str, Any]]:
        """列出指定实例的所有备份"""
        pass

    # ── 公共方法 ──────────────────────────────────────────

    def verify(self, backup_file: str) -> bool:
        """校验备份文件完整性（默认用 SHA256）"""
        if not os.path.exists(backup_file):
            logger.warning(f"备份文件不存在: {backup_file}")
            return False
        try:
            sha = hashlib.sha256()
            with open(backup_file, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    sha.update(chunk)
            logger.info(f"校验通过: {backup_file} SHA256={sha.hexdigest()[:16]}...")
            return True
        except Exception as e:
            logger.error(f"校验失败: {backup_file} - {e}")
            return False

    def cleanup(self, instance_id: str, days: int = None) -> int:
        """清理过期备份，返回删除数量"""
        if days is None:
            days = self.retention_days
        cutoff = datetime.now().timestamp() - days * 86400
        deleted = 0

        instance_dir = self._get_instance_dir(instance_id)
        if not os.path.exists(instance_dir):
            return 0

        for entry in os.scandir(instance_dir):
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                self._rmtree(entry.path)
                deleted += 1
                logger.info(f"清理过期备份: {entry.path}")

        return deleted

    def compress_file(self, src: str, dst: str = None) -> str:
        """gzip 压缩文件"""
        if dst is None:
            dst = src + ".gz"
        with open(src, 'rb') as f_in:
            with gzip.open(dst, 'wb', compresslevel=6) as f_out:
                while True:
                    chunk = f_in.read(65536)
                    if not chunk:
                        break
                    f_out.write(chunk)
        os.remove(src)
        return dst

    def checksum(self, file_path: str) -> str:
        """计算文件 SHA256"""
        sha = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha.update(chunk)
        return sha.hexdigest()

    # ── 工具方法 ──────────────────────────────────────────

    def _get_instance_dir(self, instance_id: str) -> str:
        """获取实例备份目录"""
        path = os.path.join(self.backup_dir, instance_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _get_backup_path(self, instance_id: str, timestamp: str = None) -> str:
        """生成备份文件路径"""
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(self._get_instance_dir(instance_id), timestamp)
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"{self.DB_TYPE}_backup_{timestamp}")

    def _run_cmd(self, cmd: List[str], timeout: int = 3600,
                 env: Dict = None) -> subprocess.CompletedProcess:
        """安全执行命令"""
        logger.debug(f"执行命令: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, env=env or os.environ.copy(),
                encoding="utf-8", errors="replace"
            )
            return result
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"命令执行超时 ({timeout}s): {' '.join(cmd)}")
        except FileNotFoundError:
            raise RuntimeError(f"命令未找到: {cmd[0]}，请确认已安装")

    # ── 多场景执行支持 ────────────────────────────────────

    def _get_exec_env(self, conn_info: Dict[str, Any]) -> Dict[str, Any]:
        """解析执行环境配置，返回 {'mode','docker','ssh'} """
        return {
            "mode": conn_info.get("exec_mode", "local"),  # local | docker | ssh
            "docker": conn_info.get("docker", {}),
            "ssh": conn_info.get("ssh", {}),
        }

    def _build_cmd(self, base_cmd: List[str],
                   exec_env: Dict[str, Any],
                   env_vars: Dict[str, str] = None) -> List[str]:
        """根据执行模式包装命令"""
        mode = exec_env.get("mode", "local")

        if mode == "docker":
            container = exec_env.get("docker", {}).get("container", "")
            if not container:
                raise RuntimeError("Docker 模式下必须指定 container 名称")
            # 检查容器是否在运行
            check = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace"
            )
            if check.returncode != 0:
                raise RuntimeError(
                    f"Docker 容器不存在: {container}\n"
                    f"💡 用 `docker ps` 查看运行中的容器"
                )
            if (check.stdout or "").strip() != "true":
                # 容器存在但未运行，尝试用 ID 的前12位查找
                short_id = container[:12] if len(container) > 12 else container
                raise RuntimeError(
                    f"Docker 容器未运行: {container}\n"
                    f"💡 用 `docker start {short_id}` 启动容器后重试"
                )
            # 注入环境变量（docker exec 需要 -e 显式传递）
            docker_cmd = ["docker", "exec"]
            if env_vars:
                for k, v in env_vars.items():
                    docker_cmd.extend(["-e", f"{k}={v}"])
            docker_cmd.append(container)
            return docker_cmd + base_cmd

        return base_cmd  # local mode

    def _run_backup_cmd(self, cmd: List[str], conn_info: Dict[str, Any],
                        timeout: int = 3600, env_vars: Dict[str, str] = None
                        ) -> subprocess.CompletedProcess:
        """统一备份命令执行入口，自动处理 Docker/SSH"""
        exec_env = self._get_exec_env(conn_info)
        mode = exec_env.get("mode", "local")

        if mode == "ssh":
            return self._exec_ssh(cmd, exec_env, timeout, env_vars)

        full_cmd = self._build_cmd(cmd, exec_env, env_vars)
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
        return self._run_cmd(full_cmd, timeout=timeout, env=env)

    def _exec_ssh(self, cmd: List[str], exec_env: Dict,
                  timeout: int = 3600,
                  env_vars: Dict[str, str] = None
                  ) -> subprocess.CompletedProcess:
        """通过 paramiko SSH 执行远程命令"""
        import paramiko

        ssh_cfg = exec_env.get("ssh", {})
        host = ssh_cfg.get("host", "")
        port = int(ssh_cfg.get("port", 22))
        user = ssh_cfg.get("user", "root")
        password = ssh_cfg.get("password", "")
        key_file = ssh_cfg.get("key_file", "")

        if not host:
            raise RuntimeError("SSH 模式需要配置 SSH 主机地址")

        cmd_str = " ".join(cmd)
        if env_vars:
            exports = "; ".join(f"export {k}={v}" for k, v in env_vars.items())
            cmd_str = f"{exports}; {cmd_str}"

        logger.info(f"SSH {user}@{host}:{port} -> {cmd_str[:120]}")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self._ssh_connect(client, host, port, user, password, key_file)
            stdin, stdout, stderr = client.exec_command(cmd_str, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()

            return subprocess.CompletedProcess(
                args=cmd, returncode=exit_code, stdout=out, stderr=err
            )
        finally:
            client.close()

    def _ssh_connect(self, client, host, port, user, password, key_file):
        """建立 SSH 连接"""
        if key_file and os.path.exists(key_file):
            client.connect(host, port=port, username=user, key_filename=key_file,
                          timeout=30, look_for_keys=False, allow_agent=False)
        elif password:
            client.connect(host, port=port, username=user, password=password,
                          timeout=30, look_for_keys=False, allow_agent=False)
        else:
            raise RuntimeError("SSH 需要配置密码或私钥文件")

    def _sftp_download_dir(self, exec_env: Dict, remote_dir: str, local_dir: str):
        """通过 SFTP 下载远程目录到本地"""
        import paramiko

        ssh_cfg = exec_env.get("ssh", {})
        host = ssh_cfg.get("host", "")
        port = int(ssh_cfg.get("port", 22))
        user = ssh_cfg.get("user", "root")
        password = ssh_cfg.get("password", "")
        key_file = ssh_cfg.get("key_file", "")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self._ssh_connect(client, host, port, user, password, key_file)
            sftp = client.open_sftp()
            os.makedirs(local_dir, exist_ok=True)
            files = sftp.listdir(remote_dir)
            for f in files:
                remote_path = f"{remote_dir}/{f}"
                local_path = os.path.join(local_dir, f)
                sftp.get(remote_path, local_path)
                logger.info(f"  SFTP 下载: {f} -> {local_path}")
            sftp.close()
            return files
        finally:
            client.close()

    @staticmethod
    def _rmtree(path: str):
        """递归删除目录"""
        import shutil
        shutil.rmtree(path, ignore_errors=True)
