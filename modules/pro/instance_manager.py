# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
DBCheck Pro Instance Manager
专业版多实例管理模块
支持实例分组、标签管理、批量巡检、汇总报告
"""

import json
import os
import platform
import shutil
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import hashlib
import base64

from modules.core import paths

paths.ensure_migrated()

# Fernet 密码加密
try:
    from cryptography.fernet import Fernet
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False
    Fernet = None

def _get_fernet():
    if not _FERNET_AVAILABLE:
        return None
    import sys
    # .db_key 一律以 modules.core.paths.DB_KEY_PATH 为准，禁止用 __file__ 上溯推算：
    # pro/ 迁入 modules/pro/ 后，__file__ 上溯两级会错指 D:/DBCheck/modules，
    # 导致读取到错误位置的密钥、历史加密密码全部解密失败。
    # 该常量现锚定 data/ 运行时持久目录（源码态 <项目根>/data/.db_key，
    # 打包态 <exe>/_internal/data/.db_key），与 instances.db 同目录，
    # 重装 / 重新打包覆盖 _internal 时不会连带丢失密钥。
    # 旧版本把密钥放在项目根，因此首次启动需从旧位置自动迁移（见下）。
    key_file = str(paths.DB_KEY_PATH)

    # ── 旧位置迁移（幂等）────────────────────────────────────────
    # 仅当新位置缺失、旧位置存在时才迁移；采用 copy2 保留旧文件（保守策略），
    # 避免与仍读取旧路径的历史组件/用户备份脚本冲突。密钥内容不变，
    # 因此 instances.db 中已加密的密码迁移后仍可正常解密。
    legacy_key_file = str(paths.DB_KEY_PATH_LEGACY)
    if not os.path.exists(key_file) and os.path.exists(legacy_key_file):
        try:
            os.makedirs(os.path.dirname(key_file), exist_ok=True)
            shutil.copy2(legacy_key_file, key_file)
            print(
                f"[InstanceManager] 已将密码加密密钥迁移到持久目录: "
                f"{legacy_key_file} → {key_file}（内容不变，旧文件保留）"
            )
        except Exception as e:  # 降级：迁移失败则回退到"生成新密钥"分支前先告警
            print(
                f"[InstanceManager][WARN] 密钥迁移失败（{legacy_key_file} → "
                f"{key_file}）: {e}",
                file=sys.stderr,
            )

    if not os.path.exists(key_file):
        # 密钥缺失时自动生成。若此时已存在实例库，说明密钥与数据"走散"了
        # （典型场景：打包产物携带了 data/pro_data/instances.db 却没有携带
        # .db_key，首次启动便生成全新密钥），历史密码将全部无法解密。
        # 这种情况必须显式告警，否则会静默丢失所有已保存的数据源密码。
        try:
            _store = paths.PRO_DATA_DIR / 'instances.db'
            if _store.exists() and _store.stat().st_size > 0:
                print(
                    f"[InstanceManager][WARN] 未找到密钥文件 {key_file}，"
                    f"但检测到已存在实例库 {_store}；将生成新密钥，"
                    f"历史数据源密码将无法解密，需要重新录入。",
                    file=sys.stderr,
                )
        except Exception:
            pass
        key = Fernet.generate_key()
        # data/ 为运行时目录，打包首启时可能尚未创建，写入前必须先建目录，
        # 否则 open() 会抛 FileNotFoundError 导致整个 Pro 模块不可用。
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, 'wb') as f:
            f.write(key)
    else:
        with open(key_file, 'rb') as f:
            key = f.read()
    return Fernet(key)

def _encrypt_pwd(password: str) -> str:
    if not password:
        return password
    f = _get_fernet()
    if f is None:
        return password
    return base64.b64encode(f.encrypt(password.encode())).decode()


# ── 密文形态识别 ────────────────────────────────────────────────
# Fernet token 是 urlsafe-base64 文本，二进制布局为
#   version(1B, 固定 0x80) + timestamp(8B) + iv(16B) + ciphertext + hmac(32B)
# 因此 token 文本恒以 'gAAAA' 开头。
# DBCheck 的存储格式是 base64(Fernet token)（见 _encrypt_pwd），
# 外层再 base64 一次后恒以 'Z0FBQUF' 开头。
#
# 注意：不能用 "长度 > 50 且含 '=' 且含 '/'" 这类启发式判断——
# base64 结果是否含 '/' 完全取决于密文字节，含 '=' 也取决于长度是否对齐，
# 实测 IvorySQL 数据源密文（长度 136，含 '='、不含 '/'）会被误判为明文。
_FERNET_TOKEN_PREFIX = 'gAAAA'
_FERNET_B64_PREFIX = 'Z0FBQUF'
# Fernet token 最小原始长度：1 + 8 + 16 + 32 = 57 字节
_FERNET_MIN_RAW_LEN = 57


def _is_fernet_token(token: str) -> bool:
    """判断字符串是否为结构合法的 Fernet token（urlsafe base64 文本）。"""
    if not token or not token.startswith(_FERNET_TOKEN_PREFIX):
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode())
    except Exception:
        return False
    return len(raw) >= _FERNET_MIN_RAW_LEN and raw[0] == 0x80


def _looks_like_encrypted_pwd(value: str) -> bool:
    """判断字符串是否为 DBCheck 加密密文。

    识别两种形态：
      1. DBCheck 标准存储格式 ``base64(Fernet token)``（``_encrypt_pwd`` 产出）；
      2. 裸 Fernet token（兼容历史 / 外部写入的数据）。

    Args:
        value: 待判断的字符串（可为 None）。

    Returns:
        bool: True 表示该值是密文（尚未解密），False 表示是明文或非密文。
    """
    if not value or not isinstance(value, str):
        return False
    if len(value) < 40:
        return False
    # 形态 1：base64(Fernet token)
    if value.startswith(_FERNET_B64_PREFIX):
        try:
            inner = base64.b64decode(value.encode(), validate=True).decode('ascii')
        except Exception:
            return False
        return _is_fernet_token(inner)
    # 形态 2：裸 Fernet token
    return _is_fernet_token(value)


def _decrypt_pwd(encrypted: str) -> str:
    """解密数据源密码。

    语义约定（重要）：
      - 空值：原样返回；
      - cryptography 不可用：原样返回（无法解密，维持旧行为）；
      - 输入不是密文形态（例如历史明文密码）：原样返回；
      - 输入是密文但解密失败（``.db_key`` 变更 / 损坏）：返回 ``''``。

    最后一条是本函数与旧实现的关键差异。旧实现在解密失败时返回原密文，
    调用方无法区分"明文"与"密文"，导致密文被回填到表单并直接用于数据库
    认证（表现为 password authentication failed）。返回空字符串可以让上层
    明确感知"没有拿到明文"。

    Args:
        encrypted: 数据库中存储的密码字段值。

    Returns:
        str: 解密后的明文；解密失败时为 ``''``。
    """
    if not encrypted:
        return encrypted
    f = _get_fernet()
    if f is None:
        return encrypted
    if not _looks_like_encrypted_pwd(encrypted):
        # 明文（或非 DBCheck 密文格式）直接返回，兼容历史明文数据
        return encrypted
    try:
        if encrypted.startswith(_FERNET_B64_PREFIX):
            token = base64.b64decode(encrypted.encode())
        else:
            token = encrypted.encode()
        return f.decrypt(token).decode()
    except Exception:
        # 密钥不匹配 / 密文损坏：明确返回空串，绝不回吐密文
        return ''


@dataclass
class DatabaseInstance:
    """数据库实例"""
    id: str
    name: str
    db_type: str  # mysql, postgresql, oracle, sqlserver, dm, tidb
    host: str
    port: int
    user: str
    password: str = ""  # 加密存储
    database: str = ""  # PG/IvorySQL 数据库名
    service_name: str = ""  # Oracle 专用
    gbase_server_name: str = ""  # GBase 8s 服务器实例名
    tenant: str = ""  # OceanBase 租户（连接用户名构造为 user@tenant）
    sysdba: bool = False  # Oracle SYSDBA 连接
    jdbc_url: str = ""  # Oracle JDBC 专用连接串（EZConnect / TNS / TCPS），优先于 host/port/service_name
    ssh_host: str = ""     # SSH 跳板主机
    ssh_port: int = 22     # SSH 端口
    ssh_user: str = ""     # SSH 用户
    ssh_password: str = "" # SSH 密码（加密存储）
    ssh_key_file: str = "" # SSH 私钥路径
    ssh_key_password: str = "" # SSH 私钥密码
    ssh_enabled: bool = False  # 是否启用 SSH
    tags: List[str] = None  # 标签列表
    group: str = "default"  # 分组
    enabled: bool = True
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    # MongoDB 专用连接配置
    connect_mode: str = ""  # standard / srv
    auth_source: str = ""  # 认证源（默认 admin）
    auth_mechanism: str = ""  # 认证机制：'' / SCRAM-SHA-256 / SCRAM-SHA-1
    replica_set: str = ""  # 副本集名称
    tls: int = 0  # 是否启用 TLS（0/1）
    tls_ca_file: str = ""  # TLS CA 证书路径
    tls_cert_key_file: str = ""  # TLS 客户端证书路径
    tls_allow_invalid_certs: int = 0  # 是否允许无效证书（0/1）
    # Redis / Redis Cluster 专用
    seed_nodes: str = ""    # 集群种子节点（逗号分隔 host:port，或 JSON 数组）
    # SQL Server (JDBC) 专用
    connection_mode: str = "odbc"  # 'odbc' / 'jdbc' / 'auto'，控制 SQL Server JDBC 双轨路由，默认 odbc 向后兼容
    encrypt: bool = False  # JDBC 是否启用 TLS 加密（mssql-jdbc 13.x 默认 true，DBCheck 默认 false 兼容内网）
    trust_server_certificate: bool = True  # 是否信任服务器证书
    driver_version: str = ""  # JDBC 驱动版本（驱动管理登记；空=用激活驱动）
    use_sid: bool = False  # Oracle 连接串使用 SID 格式（jdbc:oracle:thin:@host:port:SID）而非服务名格式

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()
        # 归一化 db_type 为小写，避免 IvorySQL/PG 等大小写不匹配
        if self.db_type:
            self.db_type = self.db_type.lower().replace('oracle_full', 'oracle')

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DatabaseInstance":
        """从字典创建"""
        return cls(**data)


class InstanceGroup:
    """实例分组"""

    def __init__(self, name: str, description: str = "", color: str = "#378ADD"):
        self.name = name
        self.description = description
        self.color = color
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "color": self.color,
            "created_at": self.created_at
        }


class InstanceManager:
    """实例管理器"""

    def __init__(self, data_dir: str = str(paths.PRO_DATA_DIR)):
        self.data_dir = data_dir
        self.instances_db = os.path.join(data_dir, "instances.db")
        self.groups_db = os.path.join(data_dir, "groups.db")
        # 兼容旧版 JSON 路径（迁移时回退用）
        self.instances_file = os.path.join(data_dir, "instances.json")
        self.groups_file = os.path.join(data_dir, "groups.json")
        self.db_file = os.path.join(data_dir, "pro_history.db")

        # 确保数据目录存在
        os.makedirs(data_dir, exist_ok=True)

        # 初始化存储
        self._instances: Dict[str, DatabaseInstance] = {}
        self._groups: Dict[str, InstanceGroup] = {}
        # 跨进程缓存失效依据：revision 戳文件（instances.db.rev / groups.db.rev）
        # 每次本进程或通过本类写库都会使 rev +1，读取时比对即可感知外部进程（Web 端）
        # 的数据源改动。相比 mtime / 内容哈希 / SQLite data_version，rev 戳不受同秒写入、
        # 文件大小不变、SQLite 页复用、跨连接版本同步等边界影响，最可靠。
        self._instances_rev = 0
        self._groups_rev = 0
        self._load_data()

        # 初始化数据库
        self._init_database()

    def _load_data(self):
        """加载数据（优先从 SQLite，回退到 JSON 兼容旧数据）"""
        # ── 加载实例 ──
        if os.path.exists(self.instances_db):
            try:
                conn = sqlite3.connect(self.instances_db)
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute("SELECT * FROM instances ORDER BY created_at")
                loaded = {}
                for row in c.fetchall():
                    d = dict(row)
                    # 兼容旧数据：oracle_full → oracle
                    if d.get('db_type') == 'oracle_full':
                        d['db_type'] = 'oracle'
                    # tags 从 JSON 字符串还原为列表
                    if isinstance(d.get('tags'), str):
                        try:
                            d['tags'] = json.loads(d['tags'])
                        except Exception:
                            d['tags'] = []
                    # sysdba / ssh_enabled / enabled / encrypt / trust_server_certificate / use_sid 从 INTEGER 还原为 bool
                    for bool_field in ('sysdba', 'ssh_enabled', 'enabled', 'encrypt', 'trust_server_certificate', 'use_sid'):
                        d[bool_field] = bool(d.get(bool_field, False))
                    # 存量行 connection_mode 可能为 NULL/''，按类型归一化：
                    # sqlserver_jdbc 语义即 JDBC，缺省必须归 'jdbc'，否则会被固化成
                    # 'odbc'，测试连接时错误走到 pyodbc 报 "ODBC Driver 17" 错误；
                    # 其余类型保持 'odbc'（向后兼容）。须与保存路径(下方 save)一致。
                    if not d.get('connection_mode'):
                        d['connection_mode'] = 'jdbc' if d.get('db_type') == 'sqlserver_jdbc' else 'odbc'
                    inst = DatabaseInstance.from_dict(d)
                    loaded[inst.id] = inst
                conn.close()
                # 整体替换（而非增量 merge）：反映 DB 中的「删除」——增量写法会让已删除
                # 实例永久残留在内存，导致跨进程缓存失效后仍返回旧快照。
                self._instances = loaded
            except Exception:
                pass

        # JSON 回退（兼容旧数据）
        # 注意：一旦从 instances.db 成功加载，立即把 instances.json 重命名为 .bak，
        # 防止 DB 损坏时旧数据回流，导致已删除实例"复活"。
        if not self._instances and os.path.exists(self.instances_file):
            try:
                with open(self.instances_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for inst_data in data.get("instances", []):
                        if inst_data.get('db_type') == 'oracle_full':
                            inst_data['db_type'] = 'oracle'
                        inst = DatabaseInstance.from_dict(inst_data)
                        self._instances[inst.id] = inst
            except Exception:
                pass
        elif self._instances and os.path.exists(self.instances_file):
            # 已从 instances.db 成功加载，把旧 JSON 文件重命名为 .bak，避免回流
            try:
                bak_file = self.instances_file + '.bak'
                if os.path.exists(bak_file):
                    os.remove(bak_file)
                os.rename(self.instances_file, bak_file)
            except Exception:
                pass

        # ── 加载分组 ──
        if os.path.exists(self.groups_db):
            try:
                conn = sqlite3.connect(self.groups_db)
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute("SELECT * FROM groups ORDER BY created_at")
                loaded_groups = {}
                for row in c.fetchall():
                    d = dict(row)
                    grp = InstanceGroup(**d)
                    loaded_groups[grp.name] = grp
                conn.close()
                # 整体替换，确保被删除的分组不残留
                self._groups = loaded_groups
            except Exception:
                pass

        # JSON 回退（兼容旧数据）
        if not self._groups and os.path.exists(self.groups_file):
            try:
                with open(self.groups_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for grp_data in data.get("groups", []):
                        grp = InstanceGroup(**grp_data)
                        self._groups[grp.name] = grp
            except Exception:
                pass

        # 默认分组
        if not self._groups:
            self._groups["default"] = InstanceGroup("default", "默认分组", "#888888")
            self._groups["production"] = InstanceGroup("production", "生产环境", "#E24B4A")
            self._groups["test"] = InstanceGroup("test", "测试环境", "#639922")

        # ── 跨进程缓存失效依据：读取 rev 戳 ──
        self._instances_rev = self._read_rev(self.instances_db)
        self._groups_rev = self._read_rev(self.groups_db)

    # ── 跨进程缓存失效机制 ──────────────────────────────────
    @staticmethod
    def _rev_path(db_path):
        return db_path + ".rev"

    @staticmethod
    def _read_rev(db_path):
        """读取 revision 戳文件（instances.db.rev / groups.db.rev）。

        每次本类写库都会使该值 +1，用于跨进程（Web / MCP 独立进程）感知数据源变更。
        文件不存在或损坏返回 0。
        """
        try:
            p = InstanceManager._rev_path(db_path)
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    return int((f.read() or "0").strip() or 0)
        except Exception:
            pass
        return 0

    def _bump_rev(self, db_path):
        """原子地使 revision 戳 +1（写临时文件后 os.replace）。"""
        try:
            p = self._rev_path(db_path)
            v = self._read_rev(db_path) + 1
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(str(v))
            os.replace(tmp, p)
        except Exception as e:
            print(f"[InstanceManager] 更新 revision 戳失败: {e}")

    def _ensure_instances_fresh(self):
        """惰性失效：若 instances.db 的 rev 戳被外部进程（Web 端改名 / 改类型 / 增删）
        推高，重新加载内存缓存。解决 Web 与 MCP 作为独立进程各自持有旧快照的问题。"""
        cur = self._read_rev(self.instances_db)
        if cur != self._instances_rev:
            self._load_data()

    def _ensure_groups_fresh(self):
        """同 _ensure_instances_fresh，针对 groups.db。"""
        cur = self._read_rev(self.groups_db)
        if cur != self._groups_rev:
            self._load_data()

    def _save_data(self):
        """保存数据到 SQLite（失败直接抛异常，不静默吞掉）"""
        # ── 保存实例到 instances.db ──
        conn = None
        try:
            conn = sqlite3.connect(self.instances_db)
            c = conn.cursor()
            # 迁移：为旧表添加 database 列
            try:
                c.execute('ALTER TABLE instances ADD COLUMN "database" TEXT DEFAULT \'\'')
            except Exception:
                pass
            # 迁移：为旧表添加 gbase_server_name 列
            try:
                c.execute('ALTER TABLE instances ADD COLUMN "gbase_server_name" TEXT DEFAULT \'\'')
            except Exception:
                pass
            # 迁移：为旧表添加 tenant 列（OceanBase 租户）
            try:
                c.execute('ALTER TABLE instances ADD COLUMN "tenant" TEXT DEFAULT \'\'')
            except Exception:
                pass
            # 迁移：为旧表添加 MongoDB 专用连接配置列
            for col, coltype in (
                ('connect_mode', 'TEXT'),
                ('auth_source', 'TEXT'),
                ('auth_mechanism', 'TEXT'),
                ('replica_set', 'TEXT'),
                ('tls', 'INTEGER'),
                ('tls_ca_file', 'TEXT'),
                ('tls_cert_key_file', 'TEXT'),
                ('tls_allow_invalid_certs', 'INTEGER'),
            ):
                try:
                    c.execute(f'ALTER TABLE instances ADD COLUMN "{col}" {coltype} DEFAULT \'\'')
                except Exception:
                    pass
            # 迁移：为旧表添加 connection_mode 列（SQL Server 双轨路由）
            # 默认 'odbc'，保证存量实例迁移后行为完全不变（向后兼容铁律）
            try:
                c.execute('ALTER TABLE instances ADD COLUMN "connection_mode" TEXT DEFAULT \'odbc\'')
            except Exception:
                pass
            # 迁移：为旧表添加 SQL Server JDBC TLS 参数列
            try:
                c.execute('ALTER TABLE instances ADD COLUMN "encrypt" INTEGER DEFAULT 0')
            except Exception:
                pass
            try:
                c.execute('ALTER TABLE instances ADD COLUMN "trust_server_certificate" INTEGER DEFAULT 1')
            except Exception:
                pass
            # 迁移：为旧表添加 driver_version 列（JDBC 驱动版本选择）
            try:
                c.execute('ALTER TABLE instances ADD COLUMN "driver_version" TEXT DEFAULT \'\'')
            except Exception:
                pass
            # 迁移：为旧表添加 use_sid 列（Oracle SID 格式连接开关）
            try:
                c.execute('ALTER TABLE instances ADD COLUMN "use_sid" INTEGER DEFAULT 0')
            except Exception:
                pass
            # 迁移：为旧表添加 ssh_key_password 列（SSH 私钥文件密码）
            try:
                c.execute('ALTER TABLE instances ADD COLUMN "ssh_key_password" TEXT DEFAULT \'\'')
            except Exception:
                pass
            # 确保表存在
            c.execute("""
                CREATE TABLE IF NOT EXISTS instances (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL, db_type TEXT NOT NULL, host TEXT NOT NULL,
                    port INTEGER NOT NULL, "user" TEXT NOT NULL,                 password TEXT DEFAULT '',
                    "database" TEXT DEFAULT '',
                    service_name TEXT DEFAULT '', gbase_server_name TEXT DEFAULT '', tenant TEXT DEFAULT '', sysdba INTEGER DEFAULT 0,
                    connect_mode TEXT DEFAULT '', auth_source TEXT DEFAULT '', auth_mechanism TEXT DEFAULT '', replica_set TEXT DEFAULT '',
                    tls INTEGER DEFAULT 0, tls_ca_file TEXT DEFAULT '', tls_cert_key_file TEXT DEFAULT '', tls_allow_invalid_certs INTEGER DEFAULT 0,
                    ssh_host TEXT DEFAULT '', ssh_port INTEGER DEFAULT 22,
                    ssh_user TEXT DEFAULT '', ssh_password TEXT DEFAULT '',
                    ssh_key_file TEXT DEFAULT '', ssh_key_password TEXT DEFAULT '', ssh_enabled INTEGER DEFAULT 0,
                    tags TEXT DEFAULT '[]', "group" TEXT DEFAULT 'default',
                    enabled INTEGER DEFAULT 1, description TEXT DEFAULT '',
                    created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '',
                    connection_mode TEXT DEFAULT 'odbc',
                    encrypt INTEGER DEFAULT 0,
                    trust_server_certificate INTEGER DEFAULT 1,
                    driver_version TEXT DEFAULT '', use_sid INTEGER DEFAULT 0
                )
            """)
            c.execute("DELETE FROM instances")
            for inst in self._instances.values():
                d = inst.to_dict() if not isinstance(inst, dict) else inst
                c.execute("""
                    INSERT OR REPLACE INTO instances
                    (id, name, db_type, host, port, "user", password, "database", service_name, gbase_server_name, tenant, sysdba,
                     connect_mode, auth_source, auth_mechanism, replica_set, tls, tls_ca_file, tls_cert_key_file, tls_allow_invalid_certs,
                     ssh_host, ssh_port, ssh_user, ssh_password, ssh_key_file, ssh_key_password, ssh_enabled,
                     tags, "group", enabled, description, created_at, updated_at, connection_mode,
                     encrypt, trust_server_certificate, use_sid, driver_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    d.get("id", ""), d.get("name", ""), d.get("db_type", ""),
                    d.get("host", ""), d.get("port", 0), d.get("user", ""),
                    d.get("password", ""), d.get("database", ""), d.get("service_name", ""),
                    d.get("gbase_server_name", ""), d.get("tenant", ""),
                    1 if d.get("sysdba") else 0,
                    d.get("connect_mode", ""),
                    d.get("auth_source", ""),
                    d.get("auth_mechanism", ""),
                    d.get("replica_set", ""),
                    1 if d.get("tls") else 0,
                    d.get("tls_ca_file", ""),
                    d.get("tls_cert_key_file", ""),
                    1 if d.get("tls_allow_invalid_certs") else 0,
                    d.get("ssh_host", ""), d.get("ssh_port", 22),
                    d.get("ssh_user", ""), d.get("ssh_password", ""),
                    d.get("ssh_key_file", ""), d.get("ssh_key_password", ""), 1 if d.get("ssh_enabled") else 0,
                    json.dumps(d.get("tags", []), ensure_ascii=False),
                    d.get("group", "default"), 1 if d.get("enabled", True) else 0,
                    d.get("description", ""), d.get("created_at", ""), d.get("updated_at", ""),
                    # 空值归一化，避免存量空字符串导致路由拿到无效模式。
                    # sqlserver_jdbc 类型语义即 JDBC，缺省必须归一为 'jdbc'，
                    # 否则会被固化成 'odbc'，测试连接时错误走到 pyodbc 报错。
                    (d.get("connection_mode")
                     or ("jdbc" if d.get("db_type") == "sqlserver_jdbc" else "odbc")),
                    1 if d.get("encrypt") else 0,
                    1 if d.get("trust_server_certificate") else 0,
                    1 if d.get("use_sid") else 0,
                    d.get("driver_version", "")
                ))
            conn.commit()
        except Exception as e:
            print(f"[InstanceManager] 保存 instances.db 失败: {e}")
            raise
        finally:
            if conn:
                conn.close()

        # ── 保存分组到 groups.db ──
        conn = None
        try:
            conn = sqlite3.connect(self.groups_db)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    name TEXT PRIMARY KEY,
                    description TEXT DEFAULT '',
                    color TEXT DEFAULT '#378ADD',
                    created_at TEXT DEFAULT ''
                )
            """)
            c.execute("DELETE FROM groups")
            for grp in self._groups.values():
                d = grp.to_dict() if not isinstance(grp, dict) else grp
                c.execute("""
                    INSERT OR REPLACE INTO groups (name, description, color, created_at)
                    VALUES (?, ?, ?, ?)
                """, (d.get("name", ""), d.get("description", ""),
                      d.get("color", "#378ADD"), d.get("created_at", "")))
            conn.commit()
        except Exception as e:
            print(f"[InstanceManager] 保存 groups.db 失败: {e}")
            raise
        finally:
            if conn:
                conn.close()

        # 本进程刚写库，原子推高 rev 戳并同步本进程记录，避免下次读取误判为外部变更而重复 reload
        self._bump_rev(self.instances_db)
        self._bump_rev(self.groups_db)
        self._instances_rev = self._read_rev(self.instances_db)
        self._groups_rev = self._read_rev(self.groups_db)

    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        # 巡检历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inspection_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                instance_name TEXT,
                db_type TEXT,
                inspect_time TEXT,
                health_score INTEGER,
                risk_count INTEGER,
                risk_level TEXT,
                report_path TEXT,
                duration REAL,
                auto_analyze TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 实例健康趋势表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS instance_trend (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                date TEXT NOT NULL,
                health_score INTEGER,
                risk_count INTEGER,
                connection_time REAL,
                query_count INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(instance_id, date)
            )
        """)

        conn.commit()
        conn.close()

    def _generate_id(self, name: str, db_type: str) -> str:
        """生成唯一ID"""
        raw = f"{name}-{db_type}-{datetime.now().isoformat()}".encode()
        return hashlib.md5(raw).hexdigest()[:12]

    def export_csv(self) -> str:
        """导出所有实例为 CSV 格式（密码为空）"""
        import csv
        import io
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'name', 'db_type', 'host', 'port', 'user', 'password',
            'service_name', 'sysdba', 'group', 'tags', 'description'
        ])
        writer.writeheader()
        for inst in self._instances.values():
            row = {
                'name': inst.name,
                'db_type': inst.db_type,
                'host': inst.host,
                'port': inst.port,
                'user': inst.user,
                'password': '',  # 不导出明文密码
                'service_name': inst.service_name,
                'gbase_server_name': inst.gbase_server_name,
                'sysdba': inst.sysdba,
                'group': inst.group,
                'tags': ','.join(inst.tags or []),
                'description': inst.description,
            }
            writer.writerow(row)
        return output.getvalue()

    def test_connection(self, instance_id: str) -> dict:
        """测试实例连接，返回 {'ok': bool, 'message': str}"""
        inst = self._instances.get(instance_id)
        if not inst:
            return {'ok': False, 'message': '实例不存在'}

        password = _decrypt_pwd(inst.password)
        db_type = inst.db_type.lower()

        try:
            if db_type == 'mysql':
                import pymysql
                conn = pymysql.connect(
                    host=inst.host, port=inst.port,
                    user=inst.user, password=password,
                    connect_timeout=10,
                )
                conn.close()
                return {'ok': True, 'message': '连接成功 (MySQL %s:%d)' % (inst.host, inst.port)}

            elif db_type == 'oceanbase':
                import pymysql
                # OceanBase MySQL 租户：默认端口 2881，database 即租户名（默认 sys）。
                conn = pymysql.connect(
                    host=inst.host, port=inst.port or 2881,
                    user=inst.user, password=password,
                    database=inst.database or 'sys',
                    connect_timeout=10,
                )
                conn.close()
                return {'ok': True, 'message': '连接成功 (OceanBase %s:%d)' % (inst.host, inst.port)}

            if db_type in ('postgresql', 'pg'):
                import psycopg2
                conn = psycopg2.connect(
                    host=inst.host, port=inst.port,
                    user=inst.user, password=password,
                    connect_timeout=10,
                )
                conn.close()
                return {'ok': True, 'message': '连接成功 (PostgreSQL %s:%d)' % (inst.host, inst.port)}

            elif db_type == 'ivorysql':
                import psycopg2
                conn = psycopg2.connect(
                    host=inst.host, port=inst.port,
                    user=inst.user, password=password,
                    dbname='postgres', connect_timeout=10,
                )
                conn.close()
                return {'ok': True, 'message': '连接成功 (IvorySQL %s:%d)' % (inst.host, inst.port)}

            elif db_type == 'oracle':
                import oracledb
                dsn = inst.service_name or '%s:%d/orcl' % (inst.host, inst.port)
                mode = oracledb.SYSDBA if inst.sysdba else oracledb.AUTH_MODE_DEFAULT
                try:
                    conn = oracledb.connect(user=inst.user, password=password, dsn=dsn, mode=mode)
                except Exception as e:
                    err_str = str(e)
                    if 'DPY-3010' in err_str or 'DPY-3015' in err_str:
                        print('[Oracle Thick Mode] DPY-3010/3015 detected, attempting thick mode fallback...', flush=True)
                        # thin mode 不支持 11g / 老密码验证器(0x939)，尝试 thick mode
                        _ok = False
                        try:
                            oracledb.init_oracle_client()
                            _ok = True
                            print('[Oracle Thick Mode] Auto-detect OK', flush=True)
                        except Exception as ae:
                            print(f'[Oracle Thick Mode] Auto-detect failed: {ae}', flush=True)
                        if not _ok:
                            _sys = platform.system().lower()
                            if _sys == 'windows':
                                _sub, _mk = 'windows_x64', 'oci.dll'
                            elif _sys == 'linux':
                                _sub, _mk = 'linux_x64', 'libclntsh.so'
                            elif _sys == 'darwin':
                                _sub, _mk = 'darwin_x64', 'libclntsh.dylib'
                            else:
                                _sub, _mk = None, None
                            if _sub:
                                # 随包 Instant Client 目录一律以 paths.PROJECT_ROOT 为准，
                                # 禁止用 __file__ 上溯推算：pro/ 迁入 modules/pro/ 后，
                                # 上溯两级只到 D:/DBCheck/modules，会去找不存在的
                                # modules/drivers/oracle_client/<平台>，导致厚模式回退失败。
                                _bd = str(paths.PROJECT_ROOT / 'drivers' / 'oracle_client' / _sub)
                                _dir_exists = os.path.isdir(_bd)
                                _marker_exists = os.path.isfile(os.path.join(_bd, _mk)) if _dir_exists else False
                                print(f'[Oracle Thick Mode] Bundled dir={_bd}, dir_exists={_dir_exists}, marker_exists={_marker_exists}', flush=True)
                                if _dir_exists and _marker_exists:
                                    try:
                                        oracledb.init_oracle_client(lib_dir=_bd)
                                        _ok = True
                                        print(f'[Oracle Thick Mode] Bundled init OK: {_bd}', flush=True)
                                    except Exception as be:
                                        print(f'[Oracle Thick Mode] Bundled init failed: {be}', flush=True)
                        if not _ok:
                            return {'ok': False, 'message': 'Oracle 11g 需要 Instant Client，请将包解压到 drivers/oracle_client/windows_x64 目录'}
                        print('[Oracle Thick Mode] Reconnecting with thick mode...', flush=True)
                        conn = oracledb.connect(user=inst.user, password=password, dsn=dsn, mode=mode)
                    else:
                        raise
                conn.close()
                sysdba_msg = " (SYSDBA)" if inst.sysdba else ""
                return {'ok': True, 'message': '连接成功 (Oracle%s %s)' % (sysdba_msg, dsn)}

            elif db_type == 'sqlserver':
                import pyodbc
                driver = '{ODBC Driver 17 for SQL Server}'
                dsn = 'DRIVER=%s;SERVER=%s,%d;DATABASE=master;UID=%s;PWD=%s' % (
                    driver, inst.host, inst.port, inst.user, password)
                conn = pyodbc.connect(dsn, timeout=10)
                conn.close()
                return {'ok': True, 'message': '连接成功 (SQL Server %s:%d)' % (inst.host, inst.port)}

            elif db_type == 'dm':
                try:
                    import dmPython
                    dsn = '%s:%d' % (inst.host, inst.port)
                    conn = dmPython.connect(user=inst.user, password=password, server=dsn)
                    conn.close()
                    return {'ok': True, 'message': '连接成功 (DM %s:%d)' % (inst.host, inst.port)}
                except ImportError:
                    return {'ok': False, 'message': 'dmPython 驱动未安装'}

            elif db_type == 'tidb':
                import pymysql
                conn = pymysql.connect(
                    host=inst.host, port=inst.port,
                    user=inst.user, password=password,
                    connect_timeout=10,
                )
                conn.close()
                return {'ok': True, 'message': '连接成功 (TiDB %s:%d)' % (inst.host, inst.port)}

            elif db_type == 'yashandb':
                try:
                    import yasdb
                    conn = yasdb.connect(host=inst.host, port=inst.port, user=inst.user, password=password)
                    conn.close()
                    return {'ok': True, 'message': '连接成功 (YashanDB %s:%d)' % (inst.host, inst.port)}
                except ImportError as e:
                    return {'ok': False, 'message': f'yasdb 驱动未安装: {str(e)}'}

            elif db_type == 'kingbase':
                import psycopg2
                conn = psycopg2.connect(
                    host=inst.host, port=inst.port,
                    user=inst.user, password=password,
                    dbname=inst.database or 'kingbase', connect_timeout=10,
                )
                conn.close()
                return {'ok': True, 'message': '连接成功 (KingbaseES %s:%d)' % (inst.host, inst.port)}

            elif db_type == 'gbase':
                try:
                    import pymysql
                    conn = pymysql.connect(
                        host=inst.host, port=inst.port,
                        user=inst.user, password=password,
                        database=inst.database or 'gbase01',
                        connect_timeout=10, charset='utf8mb4'
                    )
                    conn.close()
                    return {'ok': True, 'message': '连接成功 (GBase 8s %s:%d)' % (inst.host, inst.port)}
                except ImportError as e:
                    return {'ok': False, 'message': 'pymysql 驱动未安装: %s' % str(e)}
                except Exception as e:
                    err = str(e)
                    if 'Lost connection' in err or 'Can not get' in err:
                        return {'ok': False, 'message': '连接失败: %s\n提示：GBase 8s 需要开启 MySQL 协议支持' % err}
                    return {'ok': False, 'message': '连接失败: %s' % err}
            
            # 插件数据库类型：尝试委托给插件
            try:
                from modules.pluginkit.loader import discover_plugins
                plugins = discover_plugins()
                plugin_meta = None
                for p in plugins:
                    if p.get('enabled') and p.get('db_type') == db_type:
                        plugin_meta = p
                        break
                
                if plugin_meta:
                    # 加载插件并尝试测试连接
                    plugin_path = plugin_meta.get('path')
                    main_file = plugin_meta.get('main_file', 'main_plugin.py')
                    
                    import importlib.util
                    spec = importlib.util.spec_from_file_location(
                        f"plugin_{db_type}",
                        f"{plugin_path}/{main_file}"
                    )
                    if spec:
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        
                        # 查找引擎类（名称包含 Engine 的类）
                        engine_class = None
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if isinstance(attr, type) and 'Engine' in attr_name:
                                engine_class = attr
                                break
                        
                        if engine_class:
                            engine = engine_class()
                            # 尝试调用连接测试方法
                            if hasattr(engine, 'test_connection'):
                                return engine.test_connection(inst)
                            elif hasattr(engine, 'connect'):
                                # 尝试连接，成功则关闭并返回成功
                                try:
                                    conn = engine.connect(inst)
                                    if conn:
                                        if hasattr(conn, 'close'):
                                            conn.close()
                                        return {'ok': True, 'message': '连接成功 (插件 %s)' % db_type}
                                    else:
                                        return {'ok': False, 'message': '连接失败: 插件返回空连接'}
                                except Exception as conn_e:
                                    return {'ok': False, 'message': '连接失败: %s' % str(conn_e)}
                            else:
                                return {'ok': False, 'message': '插件 %s 不支持连接测试' % db_type}
            except Exception as plugin_e:
                return {'ok': False, 'message': '插件连接测试失败: %s' % str(plugin_e)}
            
            else:
                return {'ok': False, 'message': '不支持的数据库类型: %s' % db_type}

        except ImportError as e:
            return {'ok': False, 'message': '驱动未安装: %s' % str(e)}
        except Exception as e:
            return {'ok': False, 'message': '连接失败: %s' % str(e)}

    def add_instance(self, instance: DatabaseInstance) -> Dict[str, Any]:
        """添加实例（密码自动加密）"""
        if not instance.id:
            instance.id = self._generate_id(instance.name, instance.db_type)
        if instance.id in self._instances:
            return {"ok": False, "message": "实例ID已存在"}
        # 加密密码
        instance.password = _encrypt_pwd(instance.password)
        if instance.ssh_password:
            instance.ssh_password = _encrypt_pwd(instance.ssh_password)
        if instance.ssh_key_password:
            instance.ssh_key_password = _encrypt_pwd(instance.ssh_key_password)
        self._instances[instance.id] = instance
        self._save_data()
        return {"ok": True, "message": "实例添加成功", "instance_id": instance.id}

    def update_instance(self, instance_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """更新实例（密码变更时自动加密）"""
        if instance_id not in self._instances:
            return {"ok": False, "message": "实例不存在"}
        instance = self._instances[instance_id]
        for key, value in updates.items():
            if hasattr(instance, key):
                # 空值跳过，保留原值（密码字段除外：空密码不保存）
                if value is None:
                    continue
                if isinstance(value, str) and value == '' and key not in ('ssh_host', 'ssh_user', 'ssh_key_file'):
                    continue
                # 密码字段自动加密
                if key in ('password', 'ssh_password', 'ssh_key_password') and value:
                    value = _encrypt_pwd(value)
                setattr(instance, key, value)
        instance.updated_at = datetime.now().isoformat()
        self._save_data()
        return {"ok": True, "message": "实例更新成功"}

    def delete_instance(self, instance_id: str) -> Dict[str, Any]:
        """删除实例，同时清理巡检历史和趋势数据；失败回滚，不静默部分成功"""
        if instance_id not in self._instances:
            return {"ok": False, "message": "实例不存在"}

        # 先备份，便于回滚
        deleted_inst = self._instances[instance_id]

        # 删除关联的巡检历史和趋势数据
        hist_conn = None
        try:
            hist_conn = sqlite3.connect(self.db_file)
            cursor = hist_conn.cursor()
            cursor.execute("DELETE FROM inspection_history WHERE instance_id = ?", (instance_id,))
            cursor.execute("DELETE FROM instance_trend WHERE instance_id = ?", (instance_id,))
            hist_conn.commit()
        except Exception as e:
            print(f"[InstanceManager] 删除历史数据失败: {e}")
            if hist_conn:
                try: hist_conn.close()
                except Exception: pass
            raise RuntimeError(f"删除历史数据失败: {e}")
        finally:
            if hist_conn:
                try: hist_conn.close()
                except Exception: pass

        # 从内存删除，并持久化到 DB
        del self._instances[instance_id]
        try:
            self._save_data()
        except Exception as e:
            # 回滚：把实例加回内存（DB 写入失败，内存必须与 DB 一致）
            self._instances[instance_id] = deleted_inst
            print(f"[InstanceManager] 删除实例持久化失败，已回滚: {e}")
            raise RuntimeError(f"删除实例持久化失败: {e}")

        return {"ok": True, "message": "实例及历史数据删除成功"}

    def get_all_instances(self, mask_password: bool = True) -> List[Dict]:
        """获取所有实例，密码脱敏"""
        self._ensure_instances_fresh()
        result = []
        for inst in self._instances.values():
            # 统一转为字典（兼容对象和字典两种存储格式）
            if isinstance(inst, dict):
                d = inst.copy()
            else:
                d = inst.to_dict()
            if mask_password and d.get('password'):
                d['password'] = '********'
            result.append(d)
        return result

    def get_instance(self, instance_id: str, mask_password: bool = True) -> Optional[Dict]:
        """获取单个实例，密码脱敏"""
        self._ensure_instances_fresh()
        inst = self._instances.get(instance_id)
        if not inst:
            return None
        if isinstance(inst, dict):
            d = inst.copy()
        else:
            d = inst.to_dict()
        if mask_password and d.get('password'):
            d['password'] = '********'
        return d

    def get_instance_decrypted(self, instance_id: str) -> Optional[Dict]:
        """获取单个实例，密码解密（供巡检使用）"""
        self._ensure_instances_fresh()
        inst = self._instances.get(instance_id)
        if not inst:
            return None
        if isinstance(inst, dict):
            d = inst.copy()
        else:
            d = inst.to_dict()
        if d.get('password'):
            d['password'] = _decrypt_pwd(d['password'])
        if d.get('ssh_password'):
            d['ssh_password'] = _decrypt_pwd(d['ssh_password'])
        if d.get('ssh_key_password'):
            d['ssh_key_password'] = _decrypt_pwd(d['ssh_key_password'])
        return d

    def get_all_instances_decrypted(self) -> List[Dict]:
        """获取所有实例，``password`` / ``ssh_password`` 已解密。

        专供**服务端后台连接**场景使用（实时监控采集器、监控引擎、调度器等），
        这些场景需要真实明文密码才能建立数据库 / SSH 连接。

        与 ``get_all_instances(mask_password=False)`` 的区别：后者只是"不脱敏"，
        返回的仍是加密密文；若直接把它交给驱动去认证，会表现为
        ``ORA-01005: null password given`` / ``password authentication failed``
        等认证错误（密文被当成密码提交）。

        解密失败（``.db_key`` 变更 / 密文损坏）的实例，其密码字段为 ``''``
        （见 ``_decrypt_pwd`` 的语义约定），调用方可据此判断需要用户重新录入。

        Returns:
            List[Dict]: 实例字典列表，密码字段为明文。
        """
        self._ensure_instances_fresh()
        result = []
        for inst in self._instances.values():
            # 统一转为字典（兼容对象和字典两种存储格式）
            if isinstance(inst, dict):
                d = inst.copy()
            else:
                d = inst.to_dict()
            if d.get('password'):
                d['password'] = _decrypt_pwd(d['password'])
            if d.get('ssh_password'):
                d['ssh_password'] = _decrypt_pwd(d['ssh_password'])
            result.append(d)
        return result




    def get_instances_by_group(self, group: str) -> List[DatabaseInstance]:
        """按分组获取实例"""
        self._ensure_instances_fresh()
        return [inst for inst in self._instances.values() if inst.group == group]

    def get_instances_by_tag(self, tag: str) -> List[DatabaseInstance]:
        """按标签获取实例"""
        self._ensure_instances_fresh()
        return [inst for inst in self._instances.values() if tag in inst.tags]

    def get_instances_by_type(self, db_type: str) -> List[DatabaseInstance]:
        """按数据库类型获取实例"""
        self._ensure_instances_fresh()
        return [inst for inst in self._instances.values() if inst.db_type == db_type]

    def get_enabled_instances(self) -> List[DatabaseInstance]:
        """获取启用的实例"""
        self._ensure_instances_fresh()
        return [inst for inst in self._instances.values() if inst.enabled]

    # 分组管理
    def add_group(self, group: InstanceGroup) -> Dict[str, Any]:
        """添加分组"""
        if group.name in self._groups:
            return {"ok": False, "message": "分组已存在"}

        self._groups[group.name] = group
        self._save_data()
        return {"ok": True, "message": "分组添加成功"}

    def delete_group(self, group_name: str) -> Dict[str, Any]:
        """删除分组"""
        if group_name == "default":
            return {"ok": False, "message": "默认分组不能删除"}

        if group_name in self._groups:
            # 将该分组的实例移到默认分组
            for inst in self._instances.values():
                if inst.group == group_name:
                    inst.group = "default"

            del self._groups[group_name]
            self._save_data()
            return {"ok": True, "message": "分组删除成功"}

        return {"ok": False, "message": "分组不存在"}

    def get_all_groups(self) -> List[InstanceGroup]:
        """获取所有分组"""
        self._ensure_groups_fresh()
        return list(self._groups.values())

    # 统计信息
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        self._ensure_instances_fresh()
        total = len(self._instances)
        enabled = len([i for i in self._instances.values() if i.enabled])

        # 按类型统计
        by_type = {}
        for inst in self._instances.values():
            by_type[inst.db_type] = by_type.get(inst.db_type, 0) + 1

        # 按分组统计
        by_group = {}
        for inst in self._instances.values():
            by_group[inst.group] = by_group.get(inst.group, 0) + 1

        # 计算风险项总数
        total_risks = 0
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(risk_count) FROM inspection_history")
            result = cursor.fetchone()
            if result and result[0]:
                total_risks = result[0]
            conn.close()
        except Exception:
            pass

        return {
            "total_instances": total,
            "enabled_instances": enabled,
            "by_type": by_type,
            "by_group": by_group,
            "total_groups": len(self._groups),
            "total_risks": total_risks,
        }

    # 巡检历史记录
    def record_inspection(
        self,
        instance_id: str,
        instance_name: str,
        db_type: str,
        health_score: int,
        risk_count: int,
        risk_level: str,
        report_path: str,
        duration: float,
        host: str = '',
        auto_analyze: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """记录巡检历史"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        # 确保 auto_analyze 字段存在
        try:
            cursor.execute("ALTER TABLE inspection_history ADD COLUMN auto_analyze TEXT")
        except Exception:
            pass

        # 确保 host 字段存在
        try:
            cursor.execute("ALTER TABLE inspection_history ADD COLUMN host TEXT")
        except Exception:
            pass

        try:
            auto_analyze_json = json.dumps(auto_analyze, ensure_ascii=False) if auto_analyze else None

            # 插入历史记录
            cursor.execute("""
                INSERT INTO inspection_history
                (instance_id, instance_name, db_type, inspect_time, health_score,
                 risk_count, risk_level, report_path, duration, auto_analyze, host)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                instance_id, instance_name, db_type, datetime.now().isoformat(),
                health_score, risk_count, risk_level, report_path, duration,
                auto_analyze_json, host
            ))

            # 更新趋势数据
            today = datetime.now().strftime("%Y-%m-%d")
            cursor.execute("""
                INSERT OR REPLACE INTO instance_trend
                (instance_id, date, health_score, risk_count)
                VALUES (?, ?, ?, ?)
            """, (instance_id, today, health_score, risk_count))

            conn.commit()
            return {"ok": True, "message": "巡检记录已保存"}

        except Exception as e:
            conn.rollback()
            return {"ok": False, "message": f"记录失败: {str(e)}"}
        finally:
            conn.close()

    def get_inspection_history(
        self,
        instance_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """获取巡检历史"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if instance_id:
            cursor.execute("""
                SELECT * FROM inspection_history
                WHERE instance_id = ?
                ORDER BY inspect_time DESC
                LIMIT ?
            """, (instance_id, limit))
        else:
            cursor.execute("""
                SELECT * FROM inspection_history
                ORDER BY inspect_time DESC
                LIMIT ?
            """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_instance_trend(self, instance_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """获取实例健康趋势"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM instance_trend
            WHERE instance_id = ?
            ORDER BY date DESC
            LIMIT ?
        """, (instance_id, days))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_global_health_score(self) -> int:
        """计算全局健康评分"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        # 获取最近一次巡检的每个实例的健康分
        cursor.execute("""
            SELECT instance_id, MAX(inspect_time) as latest, health_score
            FROM inspection_history
            GROUP BY instance_id
        """)

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return 0

        total_score = sum(row[2] for row in rows if row[2] is not None)
        return int(total_score / len(rows))

    # 批量操作
    def batch_add_from_csv(self, csv_content: str) -> Dict[str, Any]:
        """从CSV批量导入实例"""
        import csv
        import io

        added = 0
        errors = []

        reader = csv.DictReader(io.StringIO(csv_content))
        for row in reader:
            try:
                instance = DatabaseInstance(
                    id=self._generate_id(row.get("name", ""), row.get("db_type", "mysql")),
                    name=row.get("name", ""),
                    db_type=row.get("db_type", "mysql"),
                    host=row.get("host", ""),
                    port=int(row.get("port", 3306)),
                    user=row.get("user", ""),
                    password=row.get("password", ""),
                    service_name=row.get("service_name", ""),
                    tags=row.get("tags", "").split(","),
                    group=row.get("group", "default"),
                    description=row.get("description", "")
                )
                result = self.add_instance(instance)
                if result["ok"]:
                    added += 1
                else:
                    errors.append(f"{row.get('name', 'unknown')}: {result['message']}")
            except Exception as e:
                errors.append(f"{row.get('name', 'unknown')}: {str(e)}")

        return {
            "ok": True,
            "added": added,
            "errors": errors,
            "message": f"成功导入 {added} 个实例"
        }


# 全局单例
_instance_manager: Optional[InstanceManager] = None


def get_instance_manager() -> InstanceManager:
    """获取实例管理器单例"""
    global _instance_manager
    if _instance_manager is None:
        _instance_manager = InstanceManager()
    return _instance_manager
