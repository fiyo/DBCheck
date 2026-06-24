# -*- coding: utf-8 -*-
"""
菜单模型 - 菜单/模块管理
"""

from user_management.models.db_manager import DBManager


class MenuModel:
    """菜单数据访问层"""

    def __init__(self):
        self.db = DBManager()

    def list_all(self) -> list:
        """获取所有菜单"""
        return self.db.query_all(
            "SELECT * FROM um_menu WHERE status=1 ORDER BY sort_order, id"
        )

    def get_by_code(self, menu_code: str) -> dict:
        return self.db.query_one(
            "SELECT * FROM um_menu WHERE menu_code=?", (menu_code,)
        )

    def get_by_id(self, menu_id: int) -> dict:
        return self.db.query_one(
            "SELECT * FROM um_menu WHERE id=?", (menu_id,)
        )

    def create(self, menu_code: str, menu_name: str,
               parent_id: int = 0, sort_order: int = 0,
               menu_type: int = 1) -> int:
        self.db.execute(
            """INSERT INTO um_menu(menu_code, menu_name, parent_id, sort_order, menu_type)
               VALUES(?,?,?,?,?)""",
            (menu_code, menu_name, parent_id, sort_order, menu_type)
        )
        row = self.db.query_one("SELECT last_insert_rowid() as id")
        return row['id']

    def update(self, menu_id: int, **kwargs) -> bool:
        allowed = ['menu_name', 'parent_id', 'sort_order', 'status']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ', '.join([f"{k}=?" for k in updates])
        self.db.execute(
            f"UPDATE um_menu SET {set_clause} WHERE id=?",
            list(updates.values()) + [menu_id]
        )
        return True

    def get_all_permissions(self) -> list:
        """获取所有权限级别定义"""
        return self.db.query_all(
            "SELECT * FROM um_permission ORDER BY perm_level"
        )
