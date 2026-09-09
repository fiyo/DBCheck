# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""多租户隔离（阶段 0）的 DDL 与幂等迁移。

本模块**刻意零业务依赖**（只用 sqlite3 + core.paths），可被
``modules.user_management.models.db_manager`` 与 ``modules.access`` 同时导入而不会
产生循环引用。

设计要点
--------
1. 归属信息集中放在 RBAC 库（``um_rbac.db``）的 ``um_resource_owner`` 表，
   而不是给 6 张异构实体表各自 ALTER 5 个列。原因：
   - 这些实体分散在 5 个不同的 SQLite 文件（instances.db / history.db /
     inspection.db / doc_kb.db / pro_history.db），逐个改表成本高、风险大；
   - ``InstanceManager`` 每次保存都 ``DELETE FROM instances`` 全量重写，
     且 ``DatabaseInstance.from_dict(**row)`` 对未知列直接 TypeError，
     一旦表结构与 dataclass 不同步，会把**全部数据源**静默加载失败。
   集中注册表规避了这条致命路径，语义（租户硬隔离 + scope 软隔离）完全等价。
2. 所有 DDL 幂等：CREATE TABLE IF NOT EXISTS + ALTER TABLE 包 try/except，
   重复执行安全，老库升级不会丢数据。
3. 建表同时播种默认租户/部门，并回填存量用户的归属，保证老库升级后
   "所有现存用户都在默认租户/部门内"，不会出现孤儿用户。
"""

import os
import sqlite3

from modules.core import paths

RBAC_DB_PATH = str(paths.USER_DB_DIR / 'um_rbac.db')

# 默认租户 / 部门（单团队部署的兜底归属）
DEFAULT_TENANT_CODE = 'default'
DEFAULT_TENANT_NAME = '默认企业'
DEFAULT_DEPARTMENT_CODE = 'default'
DEFAULT_DEPARTMENT_NAME = '默认部门'


def _db_path(custom: str = None) -> str:
    return custom or RBAC_DB_PATH


def _connect(db_path: str):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _add_column(conn, table: str, column: str, decl: str) -> bool:
    """幂等地为表增加一列。已存在返回 False（不报错）。"""
    try:
        cols = {r['name'] for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return False
    if column in cols:
        return False
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True
    except Exception:
        return False


DDL = """
CREATE TABLE IF NOT EXISTS um_tenant (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        VARCHAR(64)  NOT NULL UNIQUE,
    name        VARCHAR(128) NOT NULL,
    status      TINYINT      DEFAULT 1,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS um_department (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER      NOT NULL DEFAULT 1,
    code        VARCHAR(64)  NOT NULL,
    name        VARCHAR(128) NOT NULL,
    status      TINYINT      DEFAULT 1,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, code)
);

-- 资源归属登记表：集中保存每类实体的拥有者与可见范围。
-- entity_type 取值见 modules.access.ENTITY_TYPES
CREATE TABLE IF NOT EXISTS um_resource_owner (
    entity_type         VARCHAR(64)  NOT NULL,
    entity_id           VARCHAR(128) NOT NULL,
    owner_user_id       INTEGER,
    owner_tenant_id     INTEGER      NOT NULL DEFAULT 1,
    owner_department_id INTEGER,
    scope               VARCHAR(32)  NOT NULL DEFAULT 'private',
    shared_with         TEXT         DEFAULT '[]',
    created_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_type, entity_id)
);
"""

INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_ro_owner ON um_resource_owner(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_ro_dept  ON um_resource_owner(owner_department_id);
CREATE INDEX IF NOT EXISTS idx_ro_scope ON um_resource_owner(entity_type, scope);
"""


def ensure_schema(db_path: str = None) -> dict:
    """执行幂等 DDL + 默认数据播种。返回本次实际发生的变更摘要。

    返回::
        {'created_tables': [...], 'added_columns': [...], 'seeded': bool}
    """
    path = _db_path(db_path)
    conn = _connect(path)
    added_columns = []
    try:
        before = {
            r['name']
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        conn.executescript(DDL)
        conn.executescript(INDEX_DDL)
        created_tables = sorted(
            {'um_tenant', 'um_department', 'um_resource_owner'} - before
        )

        # um_user 归属三列
        for col, decl in (
            ('tenant_id', 'INTEGER NOT NULL DEFAULT 1'),
            ('department_id', 'INTEGER NOT NULL DEFAULT 1'),
            ('is_tenant_admin', 'INTEGER NOT NULL DEFAULT 0'),
        ):
            if _add_column(conn, 'um_user', col, decl):
                added_columns.append(f'um_user.{col}')

        # um_audit_log 扩展：记录被访问的资源与判定结果（阶段 0 的可追溯时间线）
        for col, decl in (
            ('resource_type', 'VARCHAR(64)'),
            ('resource_id', 'VARCHAR(128)'),
            ('result', 'VARCHAR(32)'),
            ('client', 'VARCHAR(64)'),
        ):
            if _add_column(conn, 'um_audit_log', col, decl):
                added_columns.append(f'um_audit_log.{col}')

        # 扩展列上的索引（与 user_management_schema.sql 解耦，列补完后再建，
        # 避免旧库升级时 CREATE INDEX 引用尚未存在的列而失败）。
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_resource "
            "ON um_audit_log(resource_type, resource_id)"
        )

        seeded = _seed_defaults(conn)
        conn.commit()
        return {
            'created_tables': created_tables,
            'added_columns': added_columns,
            'seeded': seeded,
        }
    finally:
        conn.close()


def _seed_defaults(conn) -> bool:
    """播种默认租户/部门，并把存量用户挂到默认归属。"""
    changed = False
    row = conn.execute(
        "SELECT id FROM um_tenant WHERE code=?", (DEFAULT_TENANT_CODE,)
    ).fetchone()
    if row:
        tenant_id = row['id']
    else:
        conn.execute(
            "INSERT INTO um_tenant(code, name) VALUES(?,?)",
            (DEFAULT_TENANT_CODE, DEFAULT_TENANT_NAME),
        )
        tenant_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        changed = True

    row = conn.execute(
        "SELECT id FROM um_department WHERE tenant_id=? AND code=?",
        (tenant_id, DEFAULT_DEPARTMENT_CODE),
    ).fetchone()
    if row:
        dept_id = row['id']
    else:
        conn.execute(
            "INSERT INTO um_department(tenant_id, code, name) VALUES(?,?,?)",
            (tenant_id, DEFAULT_DEPARTMENT_CODE, DEFAULT_DEPARTMENT_NAME),
        )
        dept_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        changed = True

    # 存量用户回填补齐归属（原本为 NULL 的补齐为默认租户/部门）
    try:
        cur = conn.execute(
            "UPDATE um_user SET tenant_id=? WHERE tenant_id IS NULL",
            (tenant_id,),
        )
        if cur.rowcount:
            changed = True
        cur = conn.execute(
            "UPDATE um_user SET department_id=? WHERE department_id IS NULL",
            (dept_id,),
        )
        if cur.rowcount:
            changed = True
    except Exception:
        pass

    # 第一个用户（最小 id）默认成为租户管理员，避免升级后无人可管
    try:
        first = conn.execute(
            "SELECT id FROM um_user ORDER BY id LIMIT 1"
        ).fetchone()
        if first:
            exists = conn.execute(
                "SELECT 1 FROM um_user WHERE is_tenant_admin=1 LIMIT 1"
            ).fetchone()
            if not exists:
                conn.execute(
                    "UPDATE um_user SET is_tenant_admin=1 WHERE id=?",
                    (first['id'],),
                )
                changed = True
    except Exception:
        pass

    return changed
