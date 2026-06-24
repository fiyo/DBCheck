# -*- coding: utf-8 -*-
"""
用户管理路由 - 用户 CRUD API
"""

from flask import Blueprint, request, jsonify, g
from user_management.utils.auth_decorator import (
    login_required, require_permission, require_admin
)
from user_management.services.user_service import UserService
from user_management.services.perm_service import PermService

user_bp = Blueprint('um_user', __name__, url_prefix='/api/um/user')
user_service = UserService()
perm_service = PermService()


@user_bp.route('/list', methods=['GET'])
@require_permission('system_manage', min_level=1)
def list_users():
    """获取用户列表"""
    page = request.args.get('page', 1, type=int)
    size = request.args.get('size', 20, type=int)
    status = request.args.get('status', type=int)
    result = user_service.list_users(page, size, status)
    return jsonify({'code': 0, 'data': result})


@user_bp.route('', methods=['POST'])
@require_permission('system_manage', min_level=3)
def create_user():
    """新建用户"""
    data = request.get_json(silent=True) or {}
    required = ['username', 'password']
    for field in required:
        if field not in data:
            return jsonify({
                'code': 400,
                'msg': f'缺少必填字段: {field}'
            }), 400

    try:
        user_id = user_service.create_user(
            username=data['username'],
            password=data['password'],
            nickname=data.get('nickname', ''),
            email=data.get('email', '')
        )
        return jsonify({
            'code': 0,
            'data': {'id': user_id},
            'msg': '用户创建成功'
        })
    except ValueError as e:
        return jsonify({'code': 400, 'msg': str(e)}), 400


@user_bp.route('/<int:uid>', methods=['GET'])
@require_permission('system_manage', min_level=1)
def get_user(uid):
    """获取用户详情"""
    user = user_service.get_user(uid)
    if not user:
        return jsonify({'code': 404, 'msg': '用户不存在'}), 404

    # 不返回密码
    user.pop('password', None)
    return jsonify({'code': 0, 'data': user})


@user_bp.route('/<int:uid>', methods=['PUT'])
@require_permission('system_manage', min_level=3)
def update_user(uid):
    """修改用户信息"""
    data = request.get_json(silent=True) or {}
    allowed = ['nickname', 'email', 'status', 'password']
    updates = {k: v for k, v in data.items()
               if k in allowed and v is not None}
    if not updates:
        return jsonify({
            'code': 400,
            'msg': '没有可更新的字段'
        }), 400

    user_service.update_user(uid, **updates)
    return jsonify({'code': 0, 'msg': '用户更新成功'})


@user_bp.route('/<int:uid>', methods=['DELETE'])
@require_admin
def delete_user(uid):
    """删除用户"""
    user_service.delete_user(uid)
    return jsonify({'code': 0, 'msg': '用户已删除'})


@user_bp.route('/<int:uid>/roles', methods=['GET'])
@require_permission('system_manage', min_level=1)
def get_user_roles(uid):
    """获取用户角色"""
    roles = user_service.get_user_roles(uid)
    return jsonify({'code': 0, 'data': roles})


@user_bp.route('/<int:uid>/roles', methods=['PUT'])
@require_permission('system_manage', min_level=4)
def assign_roles(uid):
    """为用户分配角色"""
    data = request.get_json(silent=True) or {}
    role_ids = data.get('role_ids', [])
    user_service.assign_roles(uid, role_ids)
    return jsonify({'code': 0, 'msg': '角色分配成功'})


@user_bp.route('/<int:uid>/assets', methods=['GET'])
@require_permission('system_manage', min_level=1)
def get_user_assets(uid):
    """获取用户绑定的资产"""
    allowed_ids = perm_service.get_allowed_asset_ids(uid)
    return jsonify({'code': 0, 'data': allowed_ids})


@user_bp.route('/<int:uid>/assets', methods=['PUT'])
@require_permission('system_manage', min_level=4)
def bind_assets(uid):
    """为用户绑定可见数据库资产"""
    data = request.get_json(silent=True) or {}
    asset_ids = data.get('asset_ids', [])
    user_service.bind_assets(uid, asset_ids)
    return jsonify({'code': 0, 'msg': '资产绑定成功'})


@user_bp.route('/<int:uid>/modules', methods=['PUT'])
@require_permission('system_manage', min_level=4)
def bind_modules(uid):
    """为用户配置模块可见性和权限级别"""
    data = request.get_json(silent=True) or {}
    modules = data.get('modules', [])
    # modules 格式: [{"menu_id": 1, "perm_id": 2}, ...]
    user_service.bind_modules(uid, modules)
    return jsonify({'code': 0, 'msg': '模块配置成功'})


@user_bp.route('/menus', methods=['GET'])
@login_required
def get_my_menus():
    """获取当前用户可见菜单及权限"""
    user_id = g.current_user['user_id']
    menus = perm_service.get_user_visible_menus(user_id)
    return jsonify({'code': 0, 'data': menus})
