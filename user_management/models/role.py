# -*- coding: utf-8 -*-
"""
角色模型 - 角色 CRUD 与权限分配
"""

from user_management.models.db_manager import DBManager


class RoleModel:
    """角色数据访问层"""

    def __init__(self):
        self.db = DBManager()

    def create(self, role_code: str, role_name: str,
               description: str = '') -> int:
        """创建角色"""
        self.db.execute(
            """INSERT INTO um_role(role_code, role_name, description)
               VALUES(?, ?, ?)""",
            (role_code, role_name, description)
        )
        row = self.db.query_one(
            "SELECT id FROM um_role WHERE role_code=?", (role_code,)
        )
        return row['id'] if row else 0

    def get_by_id(self, role_id: int) -> dict:
        return self.db.query_one(
            "SELECT * FROM um_role WHERE id=?", (role_id,)
        )

    def get_by_code(self, role_code: str) -> dict:
        return self.db.query_one(
            "SELECT * FROM um_role WHERE role_code=?", (role_code,)
        )

    def list_all(self) -> list:
        """获取所有角色"""
        return self.db.query_all(
            "SELECT * FROM um_role WHERE status=1 ORDER BY id"
        )

    def update(self, role_id: int, **kwargs) -> bool:
        """更新角色信息"""
        allowed = ['role_name', 'description', 'status']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ', '.join([f"{k}=?" for k in updates])
        self.db.execute(
            f"UPDATE um_role SET {set_clause} WHERE id=?",
            list(updates.values()) + [role_id]
        )
        return True

    def delete(self, role_id: int) -> bool:
        self.db.execute("DELETE FROM um_role WHERE id=?", (role_id,))
        return True

    def set_menu_permissions(self, role_id: int,
                              menu_perms: list):
        """设置角色的菜单权限（先删后增）
        menu_perms: [{"menu_id": 1, "perm_id": 2}, ...]
        """
        self.db.execute(
            "DELETE FROM um_role_menu_perm WHERE role_id=?", (role_id,)
        )
        if menu_perms:
            self.db.execute_many(
                """INSERT OR IGNORE INTO um_role_menu_perm(role_id, menu_id, perm_id)
                   VALUES(?,?,?)""",
                [(role_id, mp['menu_id'], mp['perm_id']) for mp in menu_perms]
            )

    def get_menu_permissions(self, role_id: int) -> list:
        """获取角色的菜单权限"""
        return self.db.query_all("""
            SELECT rmp.id, m.id as menu_id, m.menu_code, m.menu_name,
                   p.id as perm_id, p.perm_code, p.perm_name, p.perm_level
            FROM um_role_menu_perm rmp
            JOIN um_menu m ON rmp.menu_id = m.id
            JOIN um_permission p ON rmp.perm_id = p.id
            WHERE rmp.role_id = ?
            ORDER BY m.sort_order
        """, (role_id,))
