# -*- coding: utf-8 -*-
"""
用户模型 - 用户 CRUD 操作
"""

from user_management.models.db_manager import DBManager


class UserModel:
    """用户数据访问层"""

    def __init__(self):
        self.db = DBManager()

    def create(self, username: str, password_hash: str,
               nickname: str = '', email: str = '') -> int:
        """创建用户，返回 user_id"""
        sql = """INSERT INTO um_user(username, password, nickname, email)
                 VALUES(?, ?, ?, ?)"""
        self.db.execute(sql, (username, password_hash, nickname, email))
        row = self.db.query_one(
            "SELECT id FROM um_user WHERE username=?", (username,)
        )
        return row['id'] if row else 0

    def get_by_id(self, user_id: int) -> dict:
        """根据 ID 获取用户"""
        return self.db.query_one(
            "SELECT * FROM um_user WHERE id=?", (user_id,)
        )

    def get_by_username(self, username: str) -> dict:
        """根据用户名获取用户"""
        return self.db.query_one(
            "SELECT * FROM um_user WHERE username=?", (username,)
        )

    def list_users(self, page: int = 1, size: int = 20,
                   status: int = None) -> dict:
        """分页获取用户列表"""
        where = ""
        params = []
        if status is not None:
            where = "WHERE status = ?"
            params.append(status)

        count_sql = f"SELECT COUNT(*) as total FROM um_user {where}"
        total = self.db.query_one(count_sql, params)['total']

        offset = (page - 1) * size
        data_sql = f"""SELECT id, username, nickname, email, status,
                              created_at, updated_at
                       FROM um_user {where}
                       ORDER BY id DESC LIMIT ? OFFSET ?"""
        rows = self.db.query_all(data_sql, params + [size, offset])

        return {
            'total': total,
            'page': page,
            'size': size,
            'items': rows
        }

    def update(self, user_id: int, **kwargs) -> bool:
        """更新用户信息"""
        allowed = ['nickname', 'email', 'status', 'password']
        updates = {}
        for k in allowed:
            if k in kwargs:
                updates[k] = kwargs[k]

        if not updates:
            return False

        set_clause = ', '.join(
            [f"{k}=?" for k in updates.keys()]
        )
        set_clause += ", updated_at=CURRENT_TIMESTAMP"
        values = list(updates.values()) + [user_id]

        self.db.execute(
            f"UPDATE um_user SET {set_clause} WHERE id=?",
            values
        )
        return True

    def delete(self, user_id: int) -> bool:
        """删除用户"""
        self.db.execute("DELETE FROM um_user WHERE id=?", (user_id,))
        return True

    def get_roles(self, user_id: int) -> list:
        """获取用户的角色列表"""
        return self.db.query_all("""
            SELECT r.id, r.role_code, r.role_name, r.description
            FROM um_user_role ur
            JOIN um_role r ON ur.role_id = r.id
            WHERE ur.user_id = ? AND r.status = 1
        """, (user_id,))

    def assign_roles(self, user_id: int, role_ids: list):
        """为用户分配角色（先删后增）"""
        self.db.execute(
            "DELETE FROM um_user_role WHERE user_id=?", (user_id,)
        )
        if role_ids:
            self.db.execute_many(
                "INSERT OR IGNORE INTO um_user_role(user_id, role_id) VALUES(?,?)",
                [(user_id, rid) for rid in role_ids]
            )

    def bind_assets(self, user_id: int, asset_ids: list):
        """为用户绑定数据库资产（先删后增）"""
        self.db.execute(
            "DELETE FROM um_user_asset_bind WHERE user_id=?", (user_id,)
        )
        if asset_ids:
            self.db.execute_many(
                "INSERT OR IGNORE INTO um_user_asset_bind(user_id, asset_id) VALUES(?,?)",
                [(user_id, aid) for aid in asset_ids]
            )

    def bind_modules(self, user_id: int, modules: list):
        """为用户绑定模块权限（先删后增）
        modules: [{"menu_id": 1, "perm_id": 2}, ...]
        """
        self.db.execute(
            "DELETE FROM um_user_module_bind WHERE user_id=?", (user_id,)
        )
        if modules:
            self.db.execute_many(
                "INSERT OR IGNORE INTO um_user_module_bind(user_id, menu_id, perm_id) VALUES(?,?,?)",
                [(user_id, m['menu_id'], m['perm_id']) for m in modules]
            )
