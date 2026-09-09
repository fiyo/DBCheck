# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""权限判定层（PDP）—— 多租户数据隔离（阶段 0）。

定位：**唯一的把关点**。Web 路由、MCP 工具、后续多 Agent 的 Skills 都必须
经过本模块才能拿到数据，禁止任何地方绕过它直接返回全量列表。

归属模型
--------
    Enterprise(tenant)  ── 最高隔离边界（跨租户硬不可见）
        └── Department  ── 主协作粒度
             └── User   ── 个体

每类实体在 ``um_resource_owner`` 登记 5 个属性：
``owner_user_id`` / ``owner_tenant_id`` / ``owner_department_id`` / ``scope`` /
``shared_with``（JSON 数组，元素形如 ``"7"`` 或 ``"dept:2"``）。

scope 语义
----------
============  ==========================================
private       仅拥有者本人可见（数据源默认，含密码/地址/SSH）
department    同部门可见（模板 / 基线默认）
enterprise    租户内可见（官方规则 / 官方知识库）
specific      仅 shared_with 白名单内可见
============  ==========================================

两条降级规则（明确写死，便于审计）
----------------------------------
1. **匿名（未登录 / 未绑定身份）**：默认保持旧行为（全量放行），
   以免打断现有单用户部署；置环境变量 ``DBCHECK_ACCESS_STRICT=1`` 后改为
   fail-closed（一律不可见）。MCP 侧只要解析出了身份就一定过滤，不受此影响。
2. **资源未登记归属**：默认降级为"租户内可见"（不跨租户的硬边界仍成立），
   同上，``DBCHECK_ACCESS_STRICT=1`` 时改为一律不可见。
   所有创建路径都会显式登记归属，正常情况不会走到这条分支。
"""

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from modules.core import paths

from modules.user_management.models.db_manager import DBManager

# ── 环境变量开关 ────────────────────────────────────────────────
# 置 1 表示 fail-closed：匿名身份 / 未登记归属的资源一律不可见。
STRICT = os.environ.get('DBCHECK_ACCESS_STRICT', '0') == '1'

# ── scope 常量 ─────────────────────────────────────────────────
SCOPE_PRIVATE = 'private'
SCOPE_DEPARTMENT = 'department'
SCOPE_ENTERPRISE = 'enterprise'
SCOPE_SPECIFIC = 'specific'
SCOPES = (SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_ENTERPRISE, SCOPE_SPECIFIC)

# ── 受管实体类型 ───────────────────────────────────────────────
ENTITY_INSTANCE = 'instance'              # 数据源（pro_data/instances.db）
ENTITY_SNAPSHOT = 'snapshot'              # 巡检快照（data/history.db）
ENTITY_HISTORY = 'history_instance'       # 历史趋势实例（data/history.db）
ENTITY_TEMPLATE = 'template'              # 巡检模板（data/inspection.db）
ENTITY_BASELINE = 'baseline'              # 配置基线（data/inspection.db）
ENTITY_RULE = 'rule'                      # SQL 审计规则（data/sql_audit.db）
ENTITY_DOC_FACT = 'doc_fact'              # 知识库事实（data/doc_kb.db）

ENTITY_TYPES = (
    ENTITY_INSTANCE, ENTITY_SNAPSHOT, ENTITY_HISTORY,
    ENTITY_TEMPLATE, ENTITY_BASELINE, ENTITY_RULE, ENTITY_DOC_FACT,
)

# 各类实体的默认可见范围（对应设计文档「数据开放范围建议」）
DEFAULT_SCOPE = {
    ENTITY_INSTANCE: SCOPE_PRIVATE,     # 含密码/地址/SSH，最高敏感
    ENTITY_SNAPSHOT: SCOPE_PRIVATE,     # 派生数据跟随数据源
    ENTITY_HISTORY: SCOPE_PRIVATE,
    ENTITY_DOC_FACT: SCOPE_PRIVATE,     # 自建知识库；官方知识库播种时显式写 enterprise
    ENTITY_TEMPLATE: SCOPE_DEPARTMENT,  # 团队最佳实践
    ENTITY_BASELINE: SCOPE_DEPARTMENT,  # 部门级性能基线
    ENTITY_RULE: SCOPE_ENTERPRISE,      # 平台级非敏感资产
}

# 各实体在各自库里的主键列名（filter_visible 用它取 id）
ENTITY_ID_KEY = {
    ENTITY_INSTANCE: 'id',
    ENTITY_SNAPSHOT: 'id',
    ENTITY_HISTORY: 'key',
    ENTITY_TEMPLATE: 'id',
    ENTITY_BASELINE: 'id',
    ENTITY_RULE: 'rule_id',
    ENTITY_DOC_FACT: 'id',
}

AUDIT_CLIENT_WEB = 'web'
AUDIT_CLIENT_MCP = 'mcp'
AUDIT_CLIENT_SCHEDULER = 'scheduler'
AUDIT_CLIENT_SYSTEM = 'system'


# ── 身份主体 ───────────────────────────────────────────────────

@dataclass
class Principal:
    """一次访问中的"谁"。Web 由 session 构造，MCP 由 API Key 构造。"""

    id: Optional[int] = None
    username: str = ''
    tenant_id: int = 1
    department_id: Optional[int] = 1
    is_tenant_admin: bool = False
    roles: List[str] = field(default_factory=list)
    display_name: str = ''

    @property
    def is_anonymous(self) -> bool:
        return self.id is None

    @property
    def is_admin(self) -> bool:
        """租户管理员：可见本租户内全部资源（含无主资源）。"""
        return bool(self.is_tenant_admin) or 'admin' in (self.roles or [])

    @classmethod
    def from_row(cls, row: Any) -> 'Principal':
        d = dict(row) if row is not None else {}
        return cls(
            id=d.get('id'),
            username=d.get('username', '') or '',
            tenant_id=d.get('tenant_id') or 1,
            department_id=d.get('department_id') or 1,
            is_tenant_admin=bool(d.get('is_tenant_admin')),
            roles=[],
            display_name=d.get('nickname') or d.get('username') or '',
        )

    def label(self) -> str:
        return self.username or str(self.id or 'anonymous')


ANONYMOUS = Principal()


def _db() -> DBManager:
    return DBManager()


def get_principal(user_id: Any) -> Optional[Principal]:
    """按 user_id 构造身份主体。

    - 兼容历史系统的 ``old_<id>`` 形态（返回 None，视为匿名）；
    - 用户不存在 / 已禁用 → None。
    """
    if user_id is None or user_id == '':
        return None
    if isinstance(user_id, str) and user_id.startswith('old_'):
        # 旧 users.db 用户：没有租户/部门归属，无法参与隔离判定
        return None
    try:
        row = _db().query_one(
            "SELECT id, username, nickname, status, tenant_id, department_id,"
            "       is_tenant_admin FROM um_user WHERE id=?", (user_id,)
        )
    except Exception:
        return None
    if not row or row.get('status') == 0:
        return None
    p = Principal.from_row(row)
    # 角色（admin 角色等价于租户管理员）
    try:
        roles = _db().query_all(
            "SELECT r.role_code FROM um_user_role ur"
            " JOIN um_role r ON ur.role_id=r.id"
            " WHERE ur.user_id=? AND r.status=1", (user_id,)
        )
        p.roles = [r['role_code'] for r in roles]
    except Exception:
        p.roles = []
    return p


def principal_from_session() -> Optional[Principal]:
    """从 Flask session 构造身份主体；无 request 上下文时返回 None。"""
    try:
        from flask import session  # 延迟导入：MCP/CLI 场景没有 Flask
        uid = session.get('user_id')
    except Exception:
        return None
    return get_principal(uid)


def principal_from_api_key(key: str) -> Optional[Principal]:
    """由 API Key 解析身份主体。

    Key 必须已绑定到某个用户（``api_keys.user_id``）；未绑定的 Key 返回 None，
    调用方按"未鉴权"处理（MCP 侧会拒绝，避免绕过隔离）。
    """
    if not key:
        return None
    db_path = str(paths.PRO_DATA_DIR / 'api_keys.db')
    if not os.path.exists(db_path):
        return None
    try:
        from modules.web.api import _hash_key  # 与 Web 侧一致的哈希算法
    except Exception:
        import hashlib
        _hash_key = lambda k: hashlib.sha256(k.encode('utf-8')).hexdigest()
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT user_id, is_active FROM api_keys WHERE key_hash=? AND is_active=1",
            (_hash_key(key),),
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    return get_principal(row['user_id'])


# ── 归属登记 ───────────────────────────────────────────────────

def normalize_entity_id(entity_id: Any) -> str:
    return '' if entity_id is None else str(entity_id)


def get_owner(entity_type: str, entity_id: Any) -> Optional[Dict]:
    eid = normalize_entity_id(entity_id)
    if not eid:
        return None
    try:
        return _db().query_one(
            "SELECT * FROM um_resource_owner WHERE entity_type=? AND entity_id=?",
            (entity_type, eid),
        )
    except Exception:
        return None


def set_owner(
    entity_type: str,
    entity_id: Any,
    user: Optional[Principal],
    scope: str = None,
    shared_with: Iterable[str] = None,
) -> Optional[Dict]:
    """登记资源归属（INSERT OR REPLACE）。user 为 None 时不登记。"""
    eid = normalize_entity_id(entity_id)
    if not eid or user is None or user.is_anonymous:
        return None
    if entity_type not in ENTITY_TYPES:
        entity_type = entity_type or 'unknown'
    scope = scope or DEFAULT_SCOPE.get(entity_type, SCOPE_PRIVATE)
    if scope not in SCOPES:
        scope = SCOPE_PRIVATE
    sw = list(shared_with or [])
    try:
        _db().execute(
            "INSERT OR REPLACE INTO um_resource_owner"
            " (entity_type, entity_id, owner_user_id, owner_tenant_id,"
            "  owner_department_id, scope, shared_with, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (
                entity_type, eid, user.id, user.tenant_id or 1,
                user.department_id, scope,
                json.dumps(sw, ensure_ascii=False),
            ),
        )
    except Exception as e:
        print(f"[access] 登记归属失败 {entity_type}/{eid}: {e}", file=__import__('sys').stderr)
        return None
    return get_owner(entity_type, eid)


def update_scope(
    entity_type: str,
    entity_id: Any,
    scope: str = None,
    shared_with: Iterable[str] = None,
) -> Optional[Dict]:
    """改可见范围 / 共享白名单（阶段 2 的「分享动作」先用它）。"""
    eid = normalize_entity_id(entity_id)
    rec = get_owner(entity_type, eid)
    if not rec:
        return None
    sets, params = [], []
    if scope and scope in SCOPES:
        sets.append("scope=?")
        params.append(scope)
    if shared_with is not None:
        sets.append("shared_with=?")
        params.append(json.dumps(list(shared_with), ensure_ascii=False))
    if not sets:
        return rec
    sets.append("updated_at=CURRENT_TIMESTAMP")
    params += [entity_type, eid]
    _db().execute(
        f"UPDATE um_resource_owner SET {', '.join(sets)}"
        " WHERE entity_type=? AND entity_id=?", params
    )
    return get_owner(entity_type, eid)


def remove_owner(entity_type: str, entity_id: Any) -> None:
    """资源被删除时同步清理归属记录，避免主键垃圾堆积。"""
    eid = normalize_entity_id(entity_id)
    if not eid:
        return
    try:
        _db().execute(
            "DELETE FROM um_resource_owner WHERE entity_type=? AND entity_id=?",
            (entity_type, eid),
        )
    except Exception:
        pass


def load_owners(entity_type: str) -> Dict[str, Dict]:
    """一次性取出某类实体的全部归属记录（避免逐行查库的 N+1）。"""
    try:
        rows = _db().query_all(
            "SELECT * FROM um_resource_owner WHERE entity_type=?", (entity_type,)
        )
    except Exception:
        return {}
    return {r['entity_id']: r for r in rows}


# ── 判定核心 ───────────────────────────────────────────────────

def visible_to(user: Optional[Principal], res: Optional[Dict]) -> bool:
    """资源对某身份是否可见。

    ``res`` 为含 owner_*/scope/shared_with 键的 dict；缺失键按"未登记"处理。
    """
    if user is None or user.is_anonymous:
        # 匿名：默认保持旧行为（放行），严格模式下 fail-closed
        return not STRICT

    r = dict(res or {})
    owner_tenant = r.get('owner_tenant_id')

    # 跨租户：硬不可见（除无归属记录时 owner_tenant 缺失，走下面的降级）
    if owner_tenant is not None and user.tenant_id is not None:
        try:
            if int(owner_tenant) != int(user.tenant_id):
                return False
        except (TypeError, ValueError):
            return False

    if user.is_admin:
        # 租户管理员可见本租户全部（含无主资源）
        return True

    # 未登记归属：降级为"租户内可见"；严格模式下不可见
    if owner_tenant is None:
        return not STRICT

    owner_user = r.get('owner_user_id')
    if owner_user is not None:
        try:
            if int(owner_user) == int(user.id):
                return True
        except (TypeError, ValueError):
            pass

    scope = r.get('scope') or SCOPE_PRIVATE
    if scope == SCOPE_ENTERPRISE:
        return True
    if scope == SCOPE_DEPARTMENT:
        od = r.get('owner_department_id')
        if od is None or user.department_id is None:
            return False
        try:
            return int(od) == int(user.department_id)
        except (TypeError, ValueError):
            return False
    if scope == SCOPE_SPECIFIC:
        sw = _parse_shared_with(r.get('shared_with'))
        if str(user.id) in sw:
            return True
        if user.department_id is not None and f"dept:{user.department_id}" in sw:
            return True
        return False
    # private：上面 owner 判定未命中即不可见
    return False


def _parse_shared_with(raw: Any) -> set:
    if not raw:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(x) for x in raw}
    try:
        data = json.loads(raw)
        return {str(x) for x in data} if isinstance(data, list) else set()
    except Exception:
        return set()


def can(user: Optional[Principal], action: str, res: Optional[Dict]) -> bool:
    """动作级判定：read 看可见性，写/删/分享要求拥有者或租户管理员。"""
    if user is None or user.is_anonymous:
        return not STRICT
    if user.is_admin:
        return True
    if action in ('read', 'view', 'list'):
        return visible_to(user, res)
    r = dict(res or {})
    owner_user = r.get('owner_user_id')
    if owner_user is None:
        # 未登记归属的写操作：严格模式拒绝，宽松模式放行（保持旧行为）
        return not STRICT
    try:
        return int(owner_user) == int(user.id)
    except (TypeError, ValueError):
        return False


def filter_visible(
    user: Optional[Principal],
    rows: Iterable[Any],
    entity_type: str,
    id_key: str = None,
) -> List[Dict]:
    """按身份过滤列表；返回 dict 列表（sqlite3.Row 会被转成 dict）。"""
    rows = list(rows or [])
    if user is None or user.is_anonymous:
        if STRICT:
            return []
        return [dict(r) if not isinstance(r, dict) else r for r in rows]

    ensure_backfill()
    key = id_key or ENTITY_ID_KEY.get(entity_type, 'id')
    owners = load_owners(entity_type)

    out = []
    for r in rows:
        d = dict(r) if not isinstance(r, dict) else r
        rid = normalize_entity_id(d.get(key))
        rec = owners.get(rid)
        merged = dict(d)
        if rec:
            merged.update(rec)
        if visible_to(user, merged):
            out.append(d)
    return out


def assert_visible(
    user: Optional[Principal],
    entity_type: str,
    entity_id: Any,
    client: str = AUDIT_CLIENT_WEB,
    rows: Iterable[Any] = None,
) -> Tuple[bool, Optional[Dict]]:
    """单个资源的可见性断言，失败时写审计并返回错误字典。

    返回 ``(True, None)`` 或 ``(False, {'error':..., 'error_code':...})``。
    """
    eid = normalize_entity_id(entity_id)
    rec = get_owner(entity_type, eid)
    ok = visible_to(user, rec)
    if not ok and rec is None and rows is not None:
        # 调用方传了原始行（如刚从业务库读出的实例），按行内字段再判一次
        for r in rows:
            d = dict(r) if not isinstance(r, dict) else r
            if normalize_entity_id(d.get(ENTITY_ID_KEY.get(entity_type, 'id'))) == eid:
                ok = visible_to(user, d)
                break
    if ok:
        audit(user, f'read_{entity_type}', eid, resource_type=entity_type,
              resource_id=eid, result='allow', client=client)
        return True, None
    audit(user, f'read_{entity_type}', eid, resource_type=entity_type,
          resource_id=eid, result='deny', client=client,
          detail='resource not visible')
    return False, {
        'error': '资源不存在或无权访问',
        'error_code': 'RESOURCE_NOT_VISIBLE',
    }


# ── 审计 ───────────────────────────────────────────────────────

def audit(
    user: Optional[Principal],
    action: str,
    target: str = '',
    detail: str = '',
    result: str = 'allow',
    resource_type: str = '',
    resource_id: str = '',
    client: str = AUDIT_CLIENT_WEB,
    ip: str = '',
) -> None:
    """写审计时间线。失败绝不抛异常（审计不能阻断业务）。"""
    try:
        _db().execute(
            "INSERT INTO um_audit_log"
            " (user_id, username, action, target, detail, ip_address,"
            "  resource_type, resource_id, result, client)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                user.id if user else None,
                user.label() if user else 'anonymous',
                action, target or '', detail or '', ip or '',
                resource_type or '', normalize_entity_id(resource_id), result, client,
            ),
        )
    except Exception:
        pass


def recent_audit(limit: int = 50, resource_type: str = None) -> List[Dict]:
    """读取最近审计记录（管理员视图 / 验收脚本用）。"""
    try:
        if resource_type:
            return _db().query_all(
                "SELECT * FROM um_audit_log WHERE resource_type=?"
                " ORDER BY id DESC LIMIT ?", (resource_type, limit)
            )
        return _db().query_all(
            "SELECT * FROM um_audit_log ORDER BY id DESC LIMIT ?", (limit,)
        )
    except Exception:
        return []


# ── 存量数据归属回填 ───────────────────────────────────────────

_BACKFILL_DONE = False


def ensure_backfill(force: bool = False) -> Dict[str, int]:
    """把升级前已存在的实体登记到默认租户管理员名下（幂等，进程内只跑一次）。

    不回填的后果是：老数据没有归属记录 → 走"未登记"降级分支 → 租户内所有人都
    可见，隔离形同虚设。因此这一步是阶段 0 能否真正生效的前提。
    """
    global _BACKFILL_DONE
    if _BACKFILL_DONE and not force:
        return {'skipped': 1}
    _BACKFILL_DONE = True

    owner = _default_owner()
    if owner is None:
        return {'error': 'no_owner'}

    stats: Dict[str, int] = {}
    for entity_type, ids in _collect_existing_ids().items():
        done = 0
        for eid in ids:
            if not eid:
                continue
            try:
                exists = _db().query_one(
                    "SELECT 1 AS x FROM um_resource_owner"
                    " WHERE entity_type=? AND entity_id=?", (entity_type, eid)
                )
                if exists:
                    continue
                set_owner(entity_type, eid, owner, DEFAULT_SCOPE.get(entity_type))
                done += 1
            except Exception:
                continue
        stats[entity_type] = done
    return stats


def _default_owner() -> Optional[Principal]:
    """默认归属人：租户管理员优先，其次 id 最小的用户。"""
    try:
        row = _db().query_one(
            "SELECT id, username, nickname, status, tenant_id, department_id,"
            "       is_tenant_admin FROM um_user"
            " WHERE is_tenant_admin=1 AND status=1 ORDER BY id LIMIT 1"
        )
        if not row:
            row = _db().query_one(
                "SELECT id, username, nickname, status, tenant_id, department_id,"
                "       is_tenant_admin FROM um_user WHERE status=1 ORDER BY id LIMIT 1"
            )
    except Exception:
        return None
    return Principal.from_row(row) if row else None


def _sqlite_ids(db_path: str, sql: str) -> List[str]:
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
        conn.close()
        return [str(r[0]) for r in rows if r[0] is not None]
    except Exception:
        return []


def _collect_existing_ids() -> Dict[str, List[str]]:
    """扫描各业务库，收集现有实体的主键（供回填使用）。"""
    data_dir = str(paths.DATA_DIR)
    history_db = os.path.join(data_dir, 'history.db')
    inspection_db = str(paths.INSPECTION_DB)
    doc_kb_db = str(paths.DOC_KB_DB)
    sql_audit_db = os.path.join(data_dir, 'sql_audit.db')

    ids: Dict[str, List[str]] = {
        ENTITY_SNAPSHOT: _sqlite_ids(history_db, "SELECT id FROM snapshots"),
        ENTITY_HISTORY: _sqlite_ids(history_db, "SELECT key FROM history_instances"),
        ENTITY_TEMPLATE: _sqlite_ids(inspection_db, "SELECT id FROM inspection_template"),
        ENTITY_BASELINE: _sqlite_ids(inspection_db, "SELECT id FROM inspection_baseline"),
        ENTITY_RULE: _sqlite_ids(sql_audit_db, "SELECT rule_id FROM sql_audit_rules"),
        ENTITY_DOC_FACT: _sqlite_ids(doc_kb_db, "SELECT id FROM doc_facts"),
    }

    # 数据源走 InstanceManager（instances.db），失败不影响其它实体
    try:
        from modules.pro import get_instance_manager
        ids[ENTITY_INSTANCE] = [
            str(i.get('id')) for i in get_instance_manager().get_all_instances()
            if i.get('id')
        ]
    except Exception:
        ids[ENTITY_INSTANCE] = _sqlite_ids(
            str(paths.PRO_DATA_DIR / 'instances.db'), "SELECT id FROM instances"
        )
    return ids
