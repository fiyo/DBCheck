# -*- coding: utf-8 -*-

#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck Professional — 专有商业软件，保留一切权利（Proprietary Software, All Rights Reserved）.
# See LICENSE for full license text.
#

"""
DBCheck 用户认证模块 (v2.6.3)

- 验证 RBAC 用户 (rbac.db / um_user 表)
- 密码使用 bcrypt 验证
- Flask session 管理 (保持原逻辑)
- 预留多租户扩展（role 字段）
"""

import os
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from flask import session, request, jsonify, redirect

# RBAC 数据库路径（项目目录，跟着项目走）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UM_RBAC_DB_PATH = os.path.join(_BASE_DIR, 'user_management', 'db', 'um_rbac.db')
USERS_DB_PATH   = os.path.join(_BASE_DIR, 'user_management', 'db', 'users.db')
os.makedirs(os.path.dirname(UM_RBAC_DB_PATH), exist_ok=True)


def _get_um_rbac_db():
    """连接 RBAC 数据库 (um_rbac.db，与 DBManager 一致)"""
    conn = sqlite3.connect(UM_RBAC_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_users_db():
    """连接原用户数据库 (users.db) — 仅用于迁移检查"""
    conn = sqlite3.connect(USERS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _verify_password(stored_hash, plain_password):
    """验证密码 (bcrypt)"""
    try:
        import bcrypt
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            stored_hash.encode('utf-8')
        )
    except Exception:
        return False


def _verify_password_old(stored_hash, plain_password):
    """验证原系统密码 (SHA-256 + salt)"""
    try:
        salt, h = stored_hash.split('$', 1)
        import hashlib
        check = hashlib.sha256((salt + plain_password).encode('utf-8')).hexdigest()
        return check == h
    except Exception:
        return False


def _generate_token(user_id, username, roles=None):
    """生成 JWT Token (兼容 RBAC 管理页)"""
    try:
        import jwt
        import time
        SECRET_KEY = os.environ.get(
            'JWT_SECRET',
            'dbcheck-rbac-dev-secret-change-in-production'
        )
        payload = {
            'user_id': user_id,
            'username': username,
            'roles': roles or [],
            'exp': int(time.time()) + 86400,  # 24小时
            'iat': int(time.time())
        }
        return jwt.encode(payload, SECRET_KEY, algorithm='HS256')
    except Exception:
        return ''
def init_default_user():
    """初始化默认管理员用户 (RBAC 兼容)
    - 如果 um_user 表不存在，先执行 schema.sql 初始化
    - 如果已有用户，跳过
    - 创建默认管理员 admin/admin123
    """
    # 确保数据库和表存在
    schema_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'db', 'user_management_schema.sql'
    )
    conn = _get_um_rbac_db()
    try:
        # 检查 um_user 表是否存在
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='um_user'"
        ).fetchone()
        if not table_exists and os.path.exists(schema_path):
            with open(schema_path, 'r', encoding='utf-8') as f:
                conn.executescript(f.read())
            conn.commit()
            print("  [OK] RBAC 数据表已初始化")

        # 检查是否已有用户
        existing = conn.execute('SELECT id FROM um_user WHERE username=?', ('admin',)).fetchone()
        if not existing:
            # 创建默认管理员
            import bcrypt
            password_hash = bcrypt.hashpw(
                'admin123'.encode('utf-8'),
                bcrypt.gensalt()
            ).decode('utf-8')
            now = datetime.now().isoformat()
            conn.execute(
                """INSERT INTO um_user(username, password, nickname, email, status, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                ('admin', password_hash, '管理员', '', 1, now, now)
            )
            admin_user_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            print("  [OK] RBAC 默认管理员已创建: admin / admin123")
        else:
            admin_user_id = existing['id']
            print("  [OK] RBAC 用户已存在，检查角色分配...")

        # 确保 admin 角色存在并分配给 admin 用户
        conn.execute(
            "INSERT OR IGNORE INTO um_role(role_name, role_code, description) VALUES('管理员', 'admin', '系统管理员，拥有所有权限')"
        )
        admin_role = conn.execute("SELECT id FROM um_role WHERE role_code='admin'").fetchone()
        if admin_role:
            existing_role = conn.execute(
                'SELECT 1 FROM um_user_role WHERE user_id=? AND role_id=?',
                (admin_user_id, admin_role['id'])
            ).fetchone()
            if not existing_role:
                conn.execute(
                    "INSERT INTO um_user_role(user_id, role_id) VALUES(?,?)",
                    (admin_user_id, admin_role['id'])
                )
                print("  [OK] 已为 admin 用户分配 admin 角色")
        # 菜单数据统一由 user_management/seed.py 的 sync_menus() 管理，此处不再插入；此处仅提交用户/角色变更
        conn.commit()
    except Exception as e:
        print(f"  [WARN] RBAC 用户初始化失败: {e}")
    finally:
        conn.close()


# ── 认证装饰器 ───────────────────────────────────────────

def login_required(f):
    """强制登录"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'ok': False, 'error': '未登录', 'error_code': 'NOT_LOGGED_IN'}), 401
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """需要管理员权限"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'ok': False, 'error': '未登录', 'error_code': 'NOT_LOGGED_IN'}), 401
        if session.get('role') != 'admin':
            return jsonify({'ok': False, 'error': '需要管理员权限', 'error_code': 'FORBIDDEN'}), 403
        return f(*args, **kwargs)
    return wrapper


# ── API 接口 ───────────────────────────────────────────────

def register_auth_routes(app):
    """在 Flask app 上注册认证路由"""

    @app.route('/api/auth/login', methods=['POST'])
    def auth_login():
        data = request.get_json() or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return jsonify({'ok': False, 'error': '请输入用户名和密码'}), 400

        # 优先验证 RBAC 用户 (rbac.db)
        conn = _get_um_rbac_db()
        try:
            user = conn.execute(
                'SELECT * FROM um_user WHERE username=? AND status=1',
                (username,)
            ).fetchone()

            if user and _verify_password(user['password'], password):
                # 获取用户角色
                roles = []
                try:
                    role_rows = conn.execute(
                        """SELECT r.role_code FROM um_user_role ur
                           JOIN um_role r ON ur.role_id = r.id
                           WHERE ur.user_id=? AND r.status=1""",
                        (user['id'],)
                    ).fetchall()
                    roles = [r['role_code'] for r in role_rows]
                except Exception:
                    pass

                # 设置 Flask session (保持原逻辑)
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['display_name'] = user['nickname'] or user['username']
                session['role'] = roles[0] if roles else 'user'
                session.permanent = True

                # 生成 JWT token (供 RBAC 管理页使用)
                token = _generate_token(user['id'], user['username'], roles)

                # 记录审计日志
                try:
                    conn.execute(
                        """INSERT INTO um_audit_log(user_id, username, action, target, detail)
                           VALUES(?,?,?,?,?)""",
                        (user['id'], username, 'login_success', 'login', '登录成功')
                    )
                    conn.commit()
                except Exception:
                    pass

                return jsonify({
                    'ok': True,
                    'token': token,  # JWT token，前端可存到 localStorage
                    'user': {
                        'id': user['id'],
                        'username': user['username'],
                        'display_name': user['nickname'] or user['username'],
                        'email': user['email'] or '',
                        'role': roles[0] if roles else 'user',
                        'roles': roles,
                    }
                })

            # RBAC 验证失败，尝试原用户系统 (users.db) — 兼容旧数据
            if not user:
                conn_old = _get_users_db()
                try:
                    user_old = conn_old.execute(
                        'SELECT * FROM users WHERE username=? AND is_active=1',
                        (username,)
                    ).fetchone()
                    if user_old and _verify_password_old(user_old['password_hash'], password):
                        # 原系统用户，设置 session
                        session['user_id'] = f"old_{user_old['id']}"
                        session['username'] = user_old['username']
                        session['display_name'] = user_old['display_name'] or user_old['username']
                        session['role'] = user_old['role']
                        session.permanent = True
                        return jsonify({
                            'ok': True,
                            'user': {
                                'id': user_old['id'],
                                'username': user_old['username'],
                                'display_name': user_old['display_name'] or user_old['username'],
                                'role': user_old['role'],
                            }
                        })
                finally:
                    conn_old.close()

            # 验证失败
            # 记录失败日志
            try:
                conn.execute(
                    """INSERT INTO um_audit_log(user_id, username, action, target, detail)
                       VALUES(?,?,?,?,?)""",
                    (None, username, 'login_fail', 'wrong_password', '密码错误')
                )
                conn.commit()
            except Exception:
                pass

            return jsonify({'ok': False, 'error': '用户名或密码错误'}), 401

        finally:
            conn.close()


    @app.route('/api/auth/logout', methods=['POST'])
    def auth_logout():
        session.clear()
        return jsonify({'ok': True})


    @app.route('/api/auth/status', methods=['GET'])
    def auth_status():
        """检查登录状态（永远返回200，不触发401）"""
        if not session.get('user_id'):
            return jsonify({'ok': True, 'logged_in': False})
        return jsonify({
            'ok': True, 'logged_in': True,
            'user': {
                'id': session['user_id'],
                'username': session.get('username', ''),
                'display_name': session.get('display_name', ''),
                'role': session.get('role', ''),
            }
        })


    @app.route('/api/auth/me', methods=['GET'])
    def auth_me():
        if not session.get('user_id'):
            return jsonify({'ok': False, 'error': '未登录'}), 401

        # 如果是原系统用户 (id 带 old_ 前缀)
        uid = session['user_id']
        if isinstance(uid, str) and uid.startswith('old_'):
            conn = _get_users_db()
            try:
                user = conn.execute('SELECT * FROM users WHERE id=?', (int(uid[4:]),)).fetchone()
                if not user:
                    session.clear()
                    return jsonify({'ok': False, 'error': '用户不存在'}), 401
                return jsonify({
                    'ok': True,
                    'user': {
                        'id': user['id'],
                        'username': user['username'],
                        'display_name': user['display_name'],
                        'email': user['email'] or '',
                        'role': user['role'],
                        'created_at': user['created_at'],
                    }
                })
            finally:
                conn.close()

        # RBAC 用户
        conn = _get_um_rbac_db()
        try:
            user = conn.execute('SELECT * FROM um_user WHERE id=?', (uid,)).fetchone()
            if not user:
                session.clear()
                return jsonify({'ok': False, 'error': '用户不存在'}), 401

            # 获取角色
            roles = []
            try:
                role_rows = conn.execute(
                    """SELECT r.role_code FROM um_user_role ur
                       JOIN um_role r ON ur.role_id = r.id
                       WHERE ur.user_id=? AND r.status=1""",
                    (uid,)
                ).fetchall()
                roles = [r['role_code'] for r in role_rows]
            except Exception:
                pass

            return jsonify({
                'ok': True,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'display_name': user['nickname'] or user['username'],
                    'email': user['email'] or '',
                    'role': roles[0] if roles else 'user',
                    'roles': roles,
                    'created_at': user['created_at'],
                }
            })
        finally:
            conn.close()


    @app.route('/api/auth/change-password', methods=['POST'])
    def auth_change_password():
        if not session.get('user_id'):
            return jsonify({'ok': False, 'error': '未登录'}), 401

        data = request.get_json() or {}
        old_pw = data.get('old_password', '')
        new_pw = data.get('new_password', '')

        if not old_pw or not new_pw:
            return jsonify({'ok': False, 'error': '请输入旧密码和新密码'}), 400
        if len(new_pw) < 6:
            return jsonify({'ok': False, 'error': '新密码至少6位'}), 400
        if old_pw == new_pw:
            return jsonify({'ok': False, 'error': '新密码不能与旧密码相同'}), 400

        uid = session['user_id']
        if isinstance(uid, str) and uid.startswith('old_'):
            return jsonify({'ok': False, 'error': '原系统用户请通过原方式修改密码'}), 400

        conn = _get_um_rbac_db()
        try:
            user = conn.execute('SELECT * FROM um_user WHERE id=?', (uid,)).fetchone()
            if not user or not _verify_password(user['password'], old_pw):
                return jsonify({'ok': False, 'error': '旧密码错误'}), 403

            # 更新密码
            import bcrypt
            new_hash = bcrypt.hashpw(
                new_pw.encode('utf-8'),
                bcrypt.gensalt()
            ).decode('utf-8')
            conn.execute(
                'UPDATE um_user SET password=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                (new_hash, uid)
            )
            conn.commit()
            return jsonify({'ok': True, 'message': '密码修改成功'})
        finally:
            conn.close()


    @app.route('/api/auth/update-profile', methods=['POST'])
    def auth_update_profile():
        if not session.get('user_id'):
            return jsonify({'ok': False, 'error': '未登录'}), 401

        data = request.get_json() or {}
        display_name = data.get('display_name', '').strip()
        email = data.get('email', '').strip()

        uid = session['user_id']
        if isinstance(uid, str) and uid.startswith('old_'):
            return jsonify({'ok': False, 'error': '原系统用户请通过原方式更新资料'}), 400

        conn = _get_um_rbac_db()
        try:
            conn.execute(
                'UPDATE um_user SET nickname=?, email=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                (display_name, email, uid)
            )
            conn.commit()
            session['display_name'] = display_name or session['username']
            return jsonify({'ok': True, 'message': '更新成功'})
        finally:
            conn.close()
