# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck 用户认证模块

- 默认管理员: dbcheck / dbcheck
- 密码 SHA-256 + 盐值哈希
- Flask session 管理
- 预留多租户扩展（role 字段）
"""

import os
import hashlib
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from flask import session, request, jsonify, redirect

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pro_data')
DB_PATH = os.path.join(DB_DIR, 'users.db')


def _get_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT DEFAULT '',
        email TEXT DEFAULT '',
        role TEXT DEFAULT 'user',
        is_active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS login_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        ip TEXT,
        success INTEGER,
        created_at TEXT NOT NULL
    )''')
    conn.commit()
    return conn


def _salt_hash(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return f'{salt}${h}'


def _verify_password(stored, password):
    salt, h = stored.split('$', 1)
    return _salt_hash(password, salt) == stored


def init_default_user():
    """初始化默认管理员用户"""
    conn = _get_db()
    try:
        existing = conn.execute('SELECT id FROM users WHERE username=?', ('dbcheck',)).fetchone()
        if not existing:
            conn.execute(
                'INSERT INTO users (username, password_hash, display_name, role, created_at, updated_at) VALUES (?,?,?,?,?,?)',
                ('dbcheck', _salt_hash('dbcheck'), '管理员', 'admin',
                 datetime.now().isoformat(), datetime.now().isoformat())
            )
            conn.commit()
            print("  ✅ 默认管理员已创建: dbcheck / dbcheck")
    finally:
        conn.close()


# ── 认证装饰器 ───────────────────────────────────────────────

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


# ── API 接口 ─────────────────────────────────────────────────

def register_auth_routes(app):
    """在 Flask app 上注册认证路由"""

    @app.route('/api/auth/login', methods=['POST'])
    def auth_login():
        data = request.get_json() or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return jsonify({'ok': False, 'error': '请输入用户名和密码'}), 400

        conn = _get_db()
        try:
            user = conn.execute(
                'SELECT * FROM users WHERE username=? AND is_active=1', (username,)
            ).fetchone()

            conn.execute(
                'INSERT INTO login_log (username, ip, success, created_at) VALUES (?,?,?,?)',
                (username, request.remote_addr, 1 if user else 0, datetime.now().isoformat())
            )
            conn.commit()

            if not user or not _verify_password(user['password_hash'], password):
                return jsonify({'ok': False, 'error': '用户名或密码错误'}), 401

            session['user_id'] = user['id']
            session['username'] = user['username']
            session['display_name'] = user['display_name'] or user['username']
            session['role'] = user['role']
            session.permanent = True

            return jsonify({
                'ok': True,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'display_name': user['display_name'],
                    'role': user['role'],
                }
            })
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

        conn = _get_db()
        try:
            user = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
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

        conn = _get_db()
        try:
            user = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
            if not user or not _verify_password(user['password_hash'], old_pw):
                return jsonify({'ok': False, 'error': '旧密码错误'}), 403

            conn.execute(
                'UPDATE users SET password_hash=?, updated_at=? WHERE id=?',
                (_salt_hash(new_pw), datetime.now().isoformat(), session['user_id'])
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

        conn = _get_db()
        try:
            conn.execute(
                'UPDATE users SET display_name=?, email=?, updated_at=? WHERE id=?',
                (display_name, email, datetime.now().isoformat(), session['user_id'])
            )
            conn.commit()
            session['display_name'] = display_name or session['username']
            return jsonify({'ok': True, 'message': '更新成功'})
        finally:
            conn.close()
