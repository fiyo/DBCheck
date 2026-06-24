# -*- coding: utf-8 -*-
"""
角色服务 - 角色 CRUD + 权限分配
"""

from user_management.models.role import RoleModel


class RoleService:
    """角色管理服务"""

    def __init__(self):
        self.role_model = RoleModel()

    def list_roles(self) -> list:
        return self.role_model.list_all()

    def create_role(self, role_code: str, role_name: str,
                    description: str = '') -> int:
        existing = self.role_model.get_by_code(role_code)
        if existing:
            raise ValueError(f"角色代码 '{role_code}' 已存在")
        return self.role_model.create(role_code, role_name, description)

    def get_role(self, role_id: int) -> dict:
        role = self.role_model.get_by_id(role_id)
        if role:
            role['menu_permissions'] = self.role_model.get_menu_permissions(
                role_id
            )
        return role

    def update_role(self, role_id: int, **kwargs) -> bool:
        return self.role_model.update(role_id, **kwargs)

    def delete_role(self, role_id: int) -> bool:
        return self.role_model.delete(role_id)

    def set_menu_permissions(self, role_id: int,
                              menu_perms: list):
        self.role_model.set_menu_permissions(role_id, menu_perms)

    def get_menu_permissions(self, role_id: int) -> list:
        return self.role_model.get_menu_permissions(role_id)
