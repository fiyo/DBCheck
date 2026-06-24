# -*- coding: utf-8 -*-
"""
用户服务 - 用户管理业务逻辑
"""

from user_management.models.user import UserModel
from user_management.utils.password import hash_password


class UserService:
    """用户管理服务"""

    def __init__(self):
        self.user_model = UserModel()

    def list_users(self, page: int = 1, size: int = 20,
                   status: int = None) -> dict:
        return self.user_model.list_users(page, size, status)

    def create_user(self, username: str, password: str,
                    nickname: str = '', email: str = '') -> int:
        """创建新用户"""
        # 检查用户名是否已存在
        existing = self.user_model.get_by_username(username)
        if existing:
            raise ValueError(f"用户名 '{username}' 已存在")

        password_hash = hash_password(password)
        return self.user_model.create(
            username, password_hash, nickname, email
        )

    def get_user(self, user_id: int) -> dict:
        user = self.user_model.get_by_id(user_id)
        if user:
            user['roles'] = self.user_model.get_roles(user_id)
        return user

    def update_user(self, user_id: int, **kwargs) -> bool:
        if 'password' in kwargs:
            kwargs['password'] = hash_password(kwargs['password'])
        return self.user_model.update(user_id, **kwargs)

    def delete_user(self, user_id: int) -> bool:
        return self.user_model.delete(user_id)

    def assign_roles(self, user_id: int, role_ids: list):
        self.user_model.assign_roles(user_id, role_ids)

    def bind_assets(self, user_id: int, asset_ids: list):
        self.user_model.bind_assets(user_id, asset_ids)

    def bind_modules(self, user_id: int, modules: list):
        self.user_model.bind_modules(user_id, modules)

    def get_user_roles(self, user_id: int) -> list:
        return self.user_model.get_roles(user_id)

    def get_user_by_username(self, username: str) -> dict:
        return self.user_model.get_by_username(username)
