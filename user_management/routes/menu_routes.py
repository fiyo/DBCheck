# -*- coding: utf-8 -*-
"""
菜单管理路由 - 菜单查询和管理 API
"""

from flask import Blueprint, request, jsonify
from user_management.utils.auth_decorator import (
    require_permission, login_required
)
from user_management.services.menu_service import MenuService

menu_bp = Blueprint('um_menu', __name__, url_prefix='/api/um/menu')
menu_service = MenuService()


@menu_bp.route('/list', methods=['GET'])
@login_required
def list_menus():
    """获取菜单列表（所有登录用户可见）"""
    menus = menu_service.list_menus()
    return jsonify({'code': 0, 'data': menus})


@menu_bp.route('/permissions', methods=['GET'])
@login_required
def list_permissions():
    """获取权限级别定义"""
    perms = menu_service.get_all_permissions()
    return jsonify({'code': 0, 'data': perms})


@menu_bp.route('', methods=['POST'])
@require_permission('system_manage', min_level=4)
def create_menu():
    """创建菜单（管理员）"""
    data = request.get_json(silent=True) or {}
    try:
        menu_id = menu_service.create_menu(
            menu_code=data['menu_code'],
            menu_name=data['menu_name'],
            parent_id=data.get('parent_id', 0),
            sort_order=data.get('sort_order', 0),
            menu_type=data.get('menu_type', 1)
        )
        return jsonify({
            'code': 0,
            'data': {'id': menu_id},
            'msg': '菜单创建成功'
        })
    except ValueError as e:
        return jsonify({'code': 400, 'msg': str(e)}), 400


@menu_bp.route('/<int:mid>', methods=['PUT'])
@require_permission('system_manage', min_level=4)
def update_menu(mid):
    """更新菜单"""
    data = request.get_json(silent=True) or {}
    menu_service.update_menu(
        mid,
        menu_name=data.get('menu_name'),
        parent_id=data.get('parent_id'),
        sort_order=data.get('sort_order'),
        status=data.get('status')
    )
    return jsonify({'code': 0, 'msg': '菜单更新成功'})
