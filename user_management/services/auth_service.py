# -*- coding: utf-8 -*-
"""
认证服务 - 登录/登出/Token 管理
"""

from user_management.models.user import UserModel
from user_management.models.db_manager import DBManager
from user_management.utils.password import verify_password
from user_management.utils.jwt_util import generate_token


class AuthService:
    """认证服务"""

    def __init__(self):
        self.user_model = UserModel()
        self.db = DBManager()

    def login(self, username: str, password: str) -> dict:
        """用户登录，成功返回 token 和用户信息"""
        user = self.user_model.get_by_username(username)
        if not user:
            self._audit_log(None, username, 'login_fail',
                            'user_not_found', '用户不存在')
            return None

        if user['status'] != 1:
            self._audit_log(user['id'], username, 'login_fail',
                            'user_disabled', '用户已禁用')
            return None

        if not verify_password(password, user['password']):
            self._audit_log(user['id'], username, 'login_fail',
                            'wrong_password', '密码错误')
            return None

        # 获取用户角色
        roles = self.user_model.get_roles(user['id'])
        role_codes = [r['role_code'] for r in roles]

        # 生成 token
        token = generate_token(user['id'], user['username'], role_codes)

        self._audit_log(user['id'], username, 'login_success',
                        'login', '登录成功')

        return {
            'token': token,
            'user': {
                'id': user['id'],
                'username': user['username'],
                'nickname': user['nickname'],
                'email': user['email'],
                'roles': role_codes
            }
        }

    def _audit_log(self, user_id: int, username: str,
                   action: str, target: str, detail: str):
        """记录审计日志"""
        try:
            self.db.execute(
                """INSERT INTO um_audit_log(user_id, username, action, target, detail)
                   VALUES(?,?,?,?,?)""",
                (user_id, username, action, target, detail)
            )
        except Exception:
            pass  # 审计日志不应阻塞主流程
