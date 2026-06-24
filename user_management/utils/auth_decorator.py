# -*- coding: utf-8 -*-
"""
权限校验装饰器 - JWT 认证 + RBAC 权限校验
"""

from functools import wraps
from flask import request, g, jsonify
from user_management.utils.jwt_util import decode_token
from user_management.services.perm_service import PermService


def login_required(f):
    """验证用户是否登录（JWT Token）"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get(
            'Authorization', ''
        ).replace('Bearer ', '')
        if not token:
            return jsonify({
                'code': 401,
                'msg': '未登录，请先登录'
            }), 401
        try:
            payload = decode_token(token)
            g.current_user = payload
        except ValueError as e:
            return jsonify({
                'code': 401,
                'msg': str(e)
            }), 401
        return f(*args, **kwargs)
    return decorated


def require_permission(menu_code: str, min_level: int = 1):
    """
    验证当前用户对指定菜单的权限级别
    min_level: 1=只读, 2=读写, 3=修改, 4=管理
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            user_id = g.current_user['user_id']
            perm_service = PermService()
            actual_level = perm_service.get_user_menu_perm_level(
                user_id, menu_code
            )
            if actual_level < min_level:
                return jsonify({
                    'code': 403,
                    'msg': f'权限不足: 需要 {min_level} 级权限，当前 {actual_level} 级'
                }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_admin(f):
    """需要管理员权限（perm_level >= 4 on system_manage）"""
    @wraps(f)
    @require_permission('system_manage', min_level=4)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


def asset_filter(f):
    """注入数据权限过滤：g.allowed_asset_ids"""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        user_id = g.current_user['user_id']
        perm_service = PermService()
        # admin 角色不过滤
        if 'admin' in g.current_user.get('roles', []):
            g.allowed_asset_ids = None  # None 表示不限制
        else:
            g.allowed_asset_ids = perm_service.get_allowed_asset_ids(user_id)
        return f(*args, **kwargs)
    return decorated
