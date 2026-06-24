# -*- coding: utf-8 -*-
"""
JWT 工具 - Token 生成和验证
"""

import jwt
import time
import os

# 配置
SECRET_KEY = os.environ.get(
    'JWT_SECRET',
    'dbcheck-rbac-dev-secret-change-in-production'
)
TOKEN_EXPIRE = 86400  # 24 小时


def generate_token(user_id: int, username: str,
                   roles: list = None) -> str:
    """生成 JWT Token"""
    payload = {
        'user_id': user_id,
        'username': username,
        'roles': roles or [],
        'exp': int(time.time()) + TOKEN_EXPIRE,
        'iat': int(time.time())
    }
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')


def decode_token(token: str) -> dict:
    """解码并验证 JWT Token"""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        raise ValueError("Token 已过期")
    except jwt.InvalidTokenError:
        raise ValueError("无效 Token")


def refresh_token(token: str) -> str:
    """刷新 Token（如果未过期）"""
    payload = decode_token(token)
    return generate_token(
        payload['user_id'],
        payload['username'],
        payload.get('roles', [])
    )
