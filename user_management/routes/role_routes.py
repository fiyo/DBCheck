# -*- coding: utf-8 -*-
"""
角色管理路由 - 角色 CRUD + 权限配置 API
"""

from flask import Blueprint, request, jsonify
from user_management.utils.auth_decorator import (
    require_permission, require_admin
)
from user_management.services.role_service import RoleService

role_bp = Blueprint('um_role', __name__, url_prefix='/api/um/role')
role_service = RoleService()


@role_bp.route('/list', methods=['GET'])
@require_permission('system_manage', min_level=1)
def list_roles():
    """获取角色列表"""
    roles = role_service.list_roles()
    return jsonify({'code': 0, 'data': roles})


@role_bp.route('', methods=['POST'])
@require_permission('system_manage', min_level=3)
def create_role():
    """创建角色"""
    data = request.get_json(silent=True) or {}
    required = ['role_code', 'role_name']
    for field in required:
        if field not in data:
            return jsonify({
                'code': 400,
                'msg': f'缺少必填字段: {field}'
            }), 400

    try:
        role_id = role_service.create_role(
            role_code=data['role_code'],
            role_name=data['role_name'],
            description=data.get('description', '')
        )
        return jsonify({
            'code': 0,
            'data': {'id': role_id},
            'msg': '角色创建成功'
        })
    except ValueError as e:
        return jsonify({'code': 400, 'msg': str(e)}), 400


@role_bp.route('/<int:rid>', methods=['GET'])
@require_permission('system_manage', min_level=1)
def get_role(rid):
    """获取角色详情（含菜单权限）"""
    role = role_service.get_role(rid)
    if not role:
        return jsonify({'code': 404, 'msg': '角色不存在'}), 404
    return jsonify({'code': 0, 'data': role})


@role_bp.route('/<int:rid>', methods=['PUT'])
@require_permission('system_manage', min_level=3)
def update_role(rid):
    """更新角色"""
    data = request.get_json(silent=True) or {}
    role_service.update_role(
        rid,
        role_name=data.get('role_name'),
        description=data.get('description'),
        status=data.get('status')
    )
    return jsonify({'code': 0, 'msg': '角色更新成功'})


@role_bp.route('/<int:rid>', methods=['DELETE'])
@require_admin
def delete_role(rid):
    """删除角色"""
    role_service.delete_role(rid)
    return jsonify({'code': 0, 'msg': '角色已删除'})


@role_bp.route('/<int:rid>/menu-perm', methods=['GET'])
@require_permission('system_manage', min_level=1)
def get_role_menu_perm(rid):
    """获取角色的菜单权限"""
    perms = role_service.get_menu_permissions(rid)
    return jsonify({'code': 0, 'data': perms})


@role_bp.route('/<int:rid>/menu-perm', methods=['PUT'])
@require_permission('system_manage', min_level=4)
def set_role_menu_perm(rid):
    """设置角色对各菜单的权限级别"""
    data = request.get_json(silent=True) or {}
    menu_perms = data.get('menu_perms', [])
    # menu_perms 格式: [{"menu_id": 1, "perm_id": 2}, ...]
    role_service.set_menu_permissions(rid, menu_perms)
    return jsonify({'code': 0, 'msg': '权限配置成功'})
