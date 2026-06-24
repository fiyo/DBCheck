# -*- coding: utf-8 -*-
"""
认证路由 - 登录/登出/状态
"""

from flask import Blueprint, request, jsonify, g
from user_management.services.auth_service import AuthService
from user_management.utils.auth_decorator import login_required
from user_management.services.user_service import UserService
from user_management.services.perm_service import PermService

auth_bp = Blueprint('um_auth', __name__, url_prefix='/api/um/auth')
auth_service = AuthService()
user_service = UserService()
perm_service = PermService()


@auth_bp.route('/login', methods=['POST'])
def login():
    """用户登录"""
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({
            'code': 400,
            'msg': '请输入用户名和密码'
        }), 400

    result = auth_service.login(username, password)
    if result:
        return jsonify({'code': 0, 'data': result, 'msg': '登录成功'})
    return jsonify({
        'code': 401,
        'msg': '用户名或密码错误'
    }), 401


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    """用户登出"""
    return jsonify({'code': 0, 'msg': '已登出'})


@auth_bp.route('/status', methods=['GET'])
@login_required
def status():
    """获取当前用户状态"""
    user_id = g.current_user['user_id']
    user = user_service.get_user(user_id)
    if not user:
        return jsonify({'code': 401, 'msg': '用户不存在'}), 401

    menus = perm_service.get_user_visible_menus(user_id)

    return jsonify({
        'code': 0,
        'data': {
            'user': {
                'id': user['id'],
                'username': user['username'],
                'nickname': user['nickname'],
                'email': user['email'],
                'roles': [r['role_code'] for r in user.get('roles', [])]
            },
            'menus': menus
        }
    })


@auth_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """修改密码"""
    data = request.get_json(silent=True) or {}
    old_pw = data.get('old_password', '')
    new_pw = data.get('new_password', '')

    if not old_pw or not new_pw:
        return jsonify({
            'code': 400,
            'msg': '请输入旧密码和新密码'
        }), 400

    if len(new_pw) < 6:
        return jsonify({
            'code': 400,
            'msg': '新密码至少6位'
        }), 400

    from user_management.utils.password import verify_password, hash_password

    user = user_service.get_user(g.current_user['user_id'])
    if not user or not verify_password(old_pw, user['password']):
        return jsonify({
            'code': 403,
            'msg': '旧密码错误'
        }), 403

    user_service.update_user(g.current_user['user_id'], password=new_pw)
    return jsonify({'code': 0, 'msg': '密码修改成功'})
