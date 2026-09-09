# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""MCP Server 纯函数 API Key 校验 → 身份主体解析。

复用现有存储契约（data/pro_data/api_keys.db，sha256(key) 作 key_hash，
is_active=1 有效），但**不依赖 Flask**：现有 _verify_api_key() 失败分支调用
jsonify() 需要 app context，stdio 模式没有。

阶段 0 升级：Key 不再只认"有没有"，而要认"是谁"——
``verify_api_key_raw`` 返回 ``(ok, owner_user_id)``，``resolve_principal``
把它转成 :class:`modules.access.Principal`，供 tools 层做数据可见性过滤。
**未绑定用户的 Key 视为无身份**，MCP 侧一律拒绝（否则等于绕过隔离）。
"""

import os
import sqlite3
import hashlib

from modules.core import paths


def hash_key(key: str) -> str:
    """与 modules/web/api.py:_hash_key 完全一致。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _db_path() -> str:
    return str(paths.PRO_DATA_DIR / "api_keys.db")


def verify_api_key_raw(key: str):
    """纯函数校验 API Key。

    返回 ``(ok, owner_user_id | None)``。
    - key 为空或库不存在 → (False, None)
    - 命中有效记录 → (True, row['user_id'])（未绑定用户时为 None）
    - 其它 → (False, None)

    注：``owner`` 语义由"Key 名称"改为"归属用户 ID"。历史 Key 若未绑定用户，
    返回 ``(True, None)``，调用方应据此拒绝服务（见 ``resolve_principal``）。
    """
    if not key:
        return (False, None)
    db = _db_path()
    if not os.path.exists(db):
        return (False, None)
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT user_id FROM api_keys WHERE key_hash=? AND is_active=1",
            (hash_key(key),),
        ).fetchone()
        conn.close()
    except Exception:
        return (False, None)
    if row:
        return (True, row["user_id"])
    return (False, None)


def resolve_principal(key: str):
    """把 API Key 解析为身份主体。

    返回 ``(ok, principal, reason)``：
    - ok=False：Key 无效 / 用户不存在 / **Key 未绑定用户**，``reason`` 说明原因；
    - ok=True：``principal`` 为 :class:`modules.access.Principal`。
    """
    ok, owner_user_id = verify_api_key_raw(key)
    if not ok:
        return (False, None, "INVALID_API_KEY")
    if owner_user_id in (None, ""):
        # 未绑定的 Key 不具备身份，放行等于绕过多租户隔离
        return (False, None, "API_KEY_NOT_BOUND_TO_USER")
    try:
        from modules.access import get_principal
        principal = get_principal(owner_user_id)
    except Exception:
        principal = None
    if principal is None:
        return (False, None, "API_KEY_OWNER_NOT_FOUND")
    return (True, principal, "")
