# -*- coding: utf-8 -*-
"""
DBCheck RBAC 模块 - 完整集成测试套件
运行: pytest user_management/tests/ -v
"""

import sys
import os
import json
import pytest

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from web_ui import app


@pytest.fixture
def client():
    """创建测试客户端"""
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def admin_token(client):
    """获取管理员 Token"""
    res = client.post('/api/um/auth/login',
                      json={'username': 'admin', 'password': 'admin123'})
    data = json.loads(res.data)
    return data['data']['token']


@pytest.fixture
def viewer_token(client):
    """获取只读用户 Token"""
    res = client.post('/api/um/auth/login',
                      json={'username': 'viewer', 'password': 'viewer123'})
    data = json.loads(res.data)
    return data['data']['token']


@pytest.fixture
def operator_token(client):
    """获取运维人员 Token"""
    res = client.post('/api/um/auth/login',
                      json={'username': 'operator', 'password': 'operator123'})
    data = json.loads(res.data)
    return data['data']['token']


# ==================== 认证测试 ====================

class TestAuth:
    """认证功能测试"""

    def test_admin_login_success(self, client):
        """管理员登录成功，返回 Token"""
        res = client.post('/api/um/auth/login',
                          json={'username': 'admin', 'password': 'admin123'})
        data = json.loads(res.data)
        assert res.status_code == 200
        assert data['code'] == 0
        assert 'token' in data['data']
        assert data['data']['user']['roles'] == ['admin']

    def test_login_wrong_password(self, client):
        """错误密码返回 401"""
        res = client.post('/api/um/auth/login',
                          json={'username': 'admin', 'password': 'wrong'})
        assert res.status_code == 401
        data = json.loads(res.data)
        assert data['code'] == 401

    def test_login_empty_fields(self, client):
        """空字段返回 400"""
        res = client.post('/api/um/auth/login',
                          json={'username': '', 'password': ''})
        assert res.status_code == 400

    def test_login_nonexistent_user(self, client):
        """不存在用户返回 401"""
        res = client.post('/api/um/auth/login',
                          json={'username': 'nobody', 'password': 'x'})
        assert res.status_code == 401

    def test_status_with_token(self, client, admin_token):
        """Token 有效时获取状态"""
        res = client.get('/api/um/auth/status',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert res.status_code == 200
        assert data['code'] == 0
        assert data['data']['user']['username'] == 'admin'

    def test_status_without_token(self, client):
        """无 Token 返回 401"""
        res = client.get('/api/um/auth/status')
        assert res.status_code == 401

    def test_status_invalid_token(self, client):
        """无效 Token 返回 401"""
        res = client.get('/api/um/auth/status',
                         headers={'Authorization': 'Bearer invalid_token'})
        assert res.status_code == 401

    def test_change_password(self, client, admin_token):
        """修改密码成功"""
        res = client.post('/api/um/auth/change-password',
                          headers={'Authorization': f'Bearer {admin_token}'},
                          json={'old_password': 'admin123',
                                'new_password': 'admin1234'})
        data = json.loads(res.data)
        assert data['code'] == 0

        # 改回原密码
        client.post('/api/um/auth/change-password',
                    headers={'Authorization': f'Bearer {admin_token}'},
                    json={'old_password': 'admin1234',
                          'new_password': 'admin123'})

    def test_viewer_login(self, client):
        """只读用户登录"""
        res = client.post('/api/um/auth/login',
                          json={'username': 'viewer', 'password': 'viewer123'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert data['data']['user']['roles'] == ['viewer']

    def test_operator_login(self, client):
        """运维用户登录"""
        res = client.post('/api/um/auth/login',
                          json={'username': 'operator', 'password': 'operator123'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert data['data']['user']['roles'] == ['operator']


# ==================== 用户 CRUD 测试 ====================

class TestUserCRUD:
    """用户管理 CRUD 测试"""

    def test_list_users_admin(self, client, admin_token):
        """管理员可以查看用户列表"""
        res = client.get('/api/um/user/list',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert data['data']['total'] >= 3
        assert len(data['data']['items']) > 0

    def test_list_users_viewer(self, client, viewer_token):
        """只读用户也可以查看用户列表（system_manage 只读权限）"""
        res = client.get('/api/um/user/list',
                         headers={'Authorization': f'Bearer {viewer_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0

    def test_create_user_admin(self, client, admin_token):
        """管理员创建用户"""
        import random
        uname = f'test_user_{random.randint(1000, 9999)}'
        res = client.post('/api/um/user',
                          headers={'Authorization': f'Bearer {admin_token}'},
                          json={'username': uname, 'password': 'test123',
                                'nickname': '测试用户', 'email': 'test@test.com'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert data['data']['id'] > 0

    def test_create_duplicate_user(self, client, admin_token):
        """创建重复用户失败"""
        res = client.post('/api/um/user',
                          headers={'Authorization': f'Bearer {admin_token}'},
                          json={'username': 'admin', 'password': 'test123'})
        data = json.loads(res.data)
        assert data['code'] == 400

    def test_create_user_missing_fields(self, client, admin_token):
        """缺少必填字段"""
        res = client.post('/api/um/user',
                          headers={'Authorization': f'Bearer {admin_token}'},
                          json={'username': 'test'})
        assert res.status_code == 400

    def test_viewer_cannot_create_user(self, client, viewer_token):
        """只读用户不能创建用户（需要 level 3）"""
        res = client.post('/api/um/user',
                          headers={'Authorization': f'Bearer {viewer_token}'},
                          json={'username': 'hacker', 'password': 'hack'})
        assert res.status_code == 403

    def test_get_user_detail(self, client, admin_token):
        """获取用户详情"""
        res = client.get('/api/um/user/1',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert 'password' not in data['data']

    def test_update_user(self, client, admin_token):
        """更新用户信息"""
        res = client.put('/api/um/user/2',
                         headers={'Authorization': f'Bearer {admin_token}'},
                         json={'nickname': '更新昵称'})
        data = json.loads(res.data)
        assert data['code'] == 0

    def test_delete_user_requires_admin(self, client, operator_token):
        """运维不能删除用户（需要 level 4）"""
        res = client.delete('/api/um/user/2',
                            headers={'Authorization': f'Bearer {operator_token}'})
        assert res.status_code == 403


# ==================== 角色权限测试 ====================

class TestRolePermission:
    """角色与权限测试"""

    def test_list_roles_admin(self, client, admin_token):
        """管理员查看角色列表"""
        res = client.get('/api/um/role/list',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert len(data['data']) >= 3

    def test_create_role_admin(self, client, admin_token):
        """管理员创建角色"""
        import random
        code = f'test_role_{random.randint(1000, 9999)}'
        res = client.post('/api/um/role',
                          headers={'Authorization': f'Bearer {admin_token}'},
                          json={'role_code': code,
                                'role_name': '测试角色',
                                'description': '测试用'})
        data = json.loads(res.data)
        assert data['code'] == 0

    def test_create_role_viewer_denied(self, client, viewer_token):
        """只读用户不能创建角色"""
        res = client.post('/api/um/role',
                          headers={'Authorization': f'Bearer {viewer_token}'},
                          json={'role_code': 'bad', 'role_name': 'Bad'})
        assert res.status_code == 403

    def test_set_role_menu_perm(self, client, admin_token):
        """设置角色菜单权限 - 使用 viewer 角色测试避免破坏 admin"""
        # 使用 viewer 角色 (id=2) 做测试，不破坏 admin
        res = client.put('/api/um/role/2/menu-perm',
                         headers={'Authorization': f'Bearer {admin_token}'},
                         json={'menu_perms': [
                             {'menu_id': 1, 'perm_id': 1},
                             {'menu_id': 2, 'perm_id': 1},
                         ]})
        data = json.loads(res.data)
        assert data['code'] == 0

        # 恢复 viewer 角色的完整只读权限
        menus_res = client.get('/api/um/menu/list',
                               headers={'Authorization': f'Bearer {admin_token}'})
        all_menus = json.loads(menus_res.data)['data']
        client.put('/api/um/role/2/menu-perm',
                   headers={'Authorization': f'Bearer {admin_token}'},
                   json={'menu_perms': [
                       {'menu_id': m['id'], 'perm_id': 1}
                       for m in all_menus
                   ]})

    def test_get_role_detail(self, client, admin_token):
        """获取角色详情含权限"""
        res = client.get('/api/um/role/1',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert 'menu_permissions' in data['data']

    def test_role_menu_perm_list(self, client, admin_token):
        """获取角色菜单权限列表"""
        res = client.get('/api/um/role/1/menu-perm',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0


# ==================== 菜单权限测试 ====================

class TestMenuPermission:
    """菜单与权限定义测试"""

    def test_list_menus(self, client, admin_token):
        """获取菜单列表"""
        res = client.get('/api/um/menu/list',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert len(data['data']) >= 7

    def test_list_permissions(self, client, admin_token):
        """获取权限级别定义"""
        res = client.get('/api/um/menu/permissions',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert len(data['data']) == 4

    def test_get_user_menus_admin(self, client, admin_token):
        """管理员获取可见菜单"""
        res = client.get('/api/um/user/menus',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert len(data['data']) == 7

    def test_get_user_menus_viewer(self, client, viewer_token):
        """只读用户获取可见菜单"""
        res = client.get('/api/um/user/menus',
                         headers={'Authorization': f'Bearer {viewer_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0
        assert len(data['data']) == 7  # viewer 也能看到所有菜单

    def test_viewer_perm_level_is_1(self, client, viewer_token):
        """只读用户权限级别为 1"""
        res = client.get('/api/um/user/menus',
                         headers={'Authorization': f'Bearer {viewer_token}'})
        data = json.loads(res.data)
        for m in data['data']:
            assert m['perm_level'] == 1


# ==================== 用户角色绑定测试 ====================

class TestUserRoleBinding:
    """用户-角色绑定测试"""

    def test_get_user_roles(self, client, admin_token):
        """获取用户角色"""
        res = client.get('/api/um/user/1/roles',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0

    def test_assign_roles(self, client, admin_token):
        """为用户分配角色 - 使用 operator 用户 (id=3) 避免影响 viewer"""
        # 获取 operator 原有角色
        res_before = client.get('/api/um/user/3/roles',
                                headers={'Authorization': f'Bearer {admin_token}'})
        original_ids = []
        if res_before.status_code == 200:
            data_before = json.loads(res_before.data)
            if data_before['code'] == 0:
                original_ids = [r['id'] for r in data_before['data']]

        # 给 operator 分配 viewer 角色
        res = client.put('/api/um/user/3/roles',
                         headers={'Authorization': f'Bearer {admin_token}'},
                         json={'role_ids': [2]})
        data = json.loads(res.data)
        assert data['code'] == 0

        # 恢复 operator 原有角色
        if original_ids:
            client.put('/api/um/user/3/roles',
                       headers={'Authorization': f'Bearer {admin_token}'},
                       json={'role_ids': original_ids})

    def test_assign_roles_viewer_denied(self, client, viewer_token):
        """只读用户不能分配角色"""
        res = client.put('/api/um/user/2/roles',
                         headers={'Authorization': f'Bearer {viewer_token}'},
                         json={'role_ids': [1]})
        assert res.status_code == 403


# ==================== 资产隔离测试 ====================

class TestAssetIsolation:
    """数据权限隔离测试"""

    def test_bind_assets_admin(self, client, admin_token):
        """管理员绑定资产"""
        res = client.put('/api/um/user/2/assets',
                         headers={'Authorization': f'Bearer {admin_token}'},
                         json={'asset_ids': [1, 2, 3]})
        data = json.loads(res.data)
        assert data['code'] == 0

    def test_get_user_assets(self, client, admin_token):
        """获取用户绑定的资产"""
        res = client.get('/api/um/user/2/assets',
                         headers={'Authorization': f'Bearer {admin_token}'})
        data = json.loads(res.data)
        assert data['code'] == 0

    def test_bind_assets_viewer_denied(self, client, viewer_token):
        """只读用户不能绑定资产"""
        res = client.put('/api/um/user/2/assets',
                         headers={'Authorization': f'Bearer {viewer_token}'},
                         json={'asset_ids': [1]})
        assert res.status_code == 403

    def test_bind_modules_admin(self, client, admin_token):
        """管理员绑定模块权限"""
        res = client.put('/api/um/user/2/modules',
                         headers={'Authorization': f'Bearer {admin_token}'},
                         json={'modules': [
                             {'menu_id': 1, 'perm_id': 4}
                         ]})
        data = json.loads(res.data)
        assert data['code'] == 0


# ==================== 登录页面测试 ====================

class TestLoginPage:
    """前端页面测试"""

    def test_login_page(self, client):
        """登录页面可访问"""
        res = client.get('/um/login')
        assert res.status_code == 200
        assert b'DBCheck' in res.data

    def test_admin_page_accessible(self, client):
        """管理页面可访问（由前端控制权限）"""
        res = client.get('/um/admin')
        assert res.status_code == 200
        assert b'DBCheck' in res.data
