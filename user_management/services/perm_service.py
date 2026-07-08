# -*- coding: utf-8 -*-
"""
权限校验服务 - 核心权限判断逻辑
"""

from user_management.models.db_manager import DBManager


class PermService:
    """权限校验服务"""

    def __init__(self):
        self.db = DBManager()

    def _is_admin(self, user_id: int) -> bool:
        """判断用户是否拥有 admin 角色（超级管理员）。
        admin 角色所有功能菜单全部开放，不依赖角色菜单权限分配。"""
        sql = """
            SELECT 1 FROM um_user_role ur
            JOIN um_role r ON ur.role_id = r.id
            WHERE ur.user_id = ? AND r.role_code = 'admin' AND r.status = 1
            LIMIT 1
        """
        return self.db.query_one(sql, (user_id,)) is not None

    def get_user_menu_perm_level(self, user_id: int,
                                  menu_code: str) -> int:
        """
        获取用户对某菜单的最高权限级别
        优先级：用户级覆盖 > 角色级配置
        返回 0 表示无权限
        admin 角色直接返回系统最高权限级别，跳过权限分配判断
        """
        # 超级管理员：所有菜单拥有最高权限，无需判断是否分配了权限
        if self._is_admin(user_id):
            max_level = self.db.query_one(
                "SELECT MAX(perm_level) as ml FROM um_permission"
            )
            return (max_level['ml'] if max_level and max_level['ml'] else 1)

        # 1. 先查用户级覆盖配置
        sql_user = """
            SELECT p.perm_level
            FROM um_user_module_bind umb
            JOIN um_menu m ON umb.menu_id = m.id
            JOIN um_permission p ON umb.perm_id = p.id
            WHERE umb.user_id = ? AND m.menu_code = ?
            LIMIT 1
        """
        row = self.db.query_one(sql_user, (user_id, menu_code))
        if row:
            return row['perm_level']

        # 2. 查角色级配置（取最高权限）
        sql_role = """
            SELECT MAX(p.perm_level) as max_level
            FROM um_user_role ur
            JOIN um_role_menu_perm rmp ON ur.role_id = rmp.role_id
            JOIN um_menu m ON rmp.menu_id = m.id
            JOIN um_permission p ON rmp.perm_id = p.id
            WHERE ur.user_id = ? AND m.menu_code = ?
        """
        row = self.db.query_one(sql_role, (user_id, menu_code))
        if row and row['max_level']:
            return row['max_level']

        return 0

    def get_user_visible_menus(self, user_id: int) -> list:
        """获取用户可见的所有菜单及权限级别

        admin 角色：直接返回全部菜单（status=1），无需判断是否分配了权限。
        普通用户：按角色菜单权限分配（um_role_menu_perm）过滤。
        """
        # 超级管理员：所有功能菜单全部开放，不依赖角色菜单权限分配
        if self._is_admin(user_id):
            sql = """
                SELECT m.id, m.menu_code, m.menu_name,
                       m.parent_id, m.sort_order, m.menu_type,
                       1 as perm_level
                FROM um_menu m
                WHERE m.status = 1
                ORDER BY m.sort_order
            """
            return self.db.query_all(sql)

        sql = """
            SELECT DISTINCT m.id, m.menu_code, m.menu_name,
                   m.parent_id, m.sort_order, m.menu_type,
                   COALESCE(umb_perm.perm_level, rmp_perm.perm_level, 0) as perm_level
            FROM um_user_role ur
            JOIN um_role_menu_perm rmp ON ur.role_id = rmp.role_id
            JOIN um_menu m ON rmp.menu_id = m.id
            JOIN um_permission rmp_perm ON rmp.perm_id = rmp_perm.id
            LEFT JOIN um_user_module_bind umb
                   ON umb.user_id = ur.user_id AND umb.menu_id = m.id
            LEFT JOIN um_permission umb_perm ON umb.perm_id = umb_perm.id
            WHERE ur.user_id = ? AND m.status = 1
            ORDER BY m.sort_order
        """
        return self.db.query_all(sql, (user_id,))

    def get_allowed_asset_ids(self, user_id: int) -> list:
        """获取用户可见的数据库资产 ID 列表"""
        sql = """
            SELECT asset_id FROM um_user_asset_bind WHERE user_id = ?
        """
        rows = self.db.query_all(sql, (user_id,))
        return [r['asset_id'] for r in rows]

    def check_permission(self, user_id: int, menu_code: str,
                         min_level: int = 1) -> bool:
        """检查用户是否有指定权限"""
        return self.get_user_menu_perm_level(user_id, menu_code) >= min_level
