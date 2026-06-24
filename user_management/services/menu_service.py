# -*- coding: utf-8 -*-
"""
菜单服务 - 菜单管理
"""

from user_management.models.menu import MenuModel


class MenuService:
    """菜单管理服务"""

    def __init__(self):
        self.menu_model = MenuModel()

    def list_menus(self) -> list:
        return self.menu_model.list_all()

    def get_menu(self, menu_code: str) -> dict:
        return self.menu_model.get_by_code(menu_code)

    def create_menu(self, menu_code: str, menu_name: str,
                    parent_id: int = 0, sort_order: int = 0,
                    menu_type: int = 1) -> int:
        existing = self.menu_model.get_by_code(menu_code)
        if existing:
            raise ValueError(f"菜单代码 '{menu_code}' 已存在")
        return self.menu_model.create(
            menu_code, menu_name, parent_id, sort_order, menu_type
        )

    def update_menu(self, menu_id: int, **kwargs) -> bool:
        return self.menu_model.update(menu_id, **kwargs)

    def get_all_permissions(self) -> list:
        return self.menu_model.get_all_permissions()
