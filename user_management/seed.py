# -*- coding: utf-8 -*-
"""
种子数据初始化脚本（幂等：重复运行结果一致）

运行方式:
  python -m user_management.seed
"""

import sys
import os

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from user_management.models.db_manager import DBManager
from user_management.models.menu import MenuModel
from user_management.utils.password import hash_password


def init_seed_data():
    """初始化种子数据（幂等：先清空再插入）"""
    db = DBManager()
    print("开始初始化 RBAC 种子数据...")

    # 0. 清空旧数据，确保幂等
    print("  [0] 清空旧数据...")
    for table in [
        'um_role_menu_perm', 'um_user_role', 'um_user_asset',
        'um_user_module', 'um_menu', 'um_role',
        'um_permission', 'um_user',
    ]:
        try:
            db.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    print("  [OK] 旧数据已清空")

    # 1. 初始化权限定义（只有一种：有权限）
    db.execute(
        "INSERT INTO um_permission(perm_code, perm_name, perm_level) "
        "VALUES('access', '有权限', 1)"
    )
    access_perm = db.query_one("SELECT id FROM um_permission WHERE perm_level=1")
    print("  ✅ 权限定义已初始化: 有权限(1)")

    # 2. 初始化菜单数据
    # menu_code 必须与前端 index.html 中 nav-item 的 id 对应（去掉 "nav-" 前缀）
    menus_data = [
        ('home',             '首页',            0, 10),
        ('wizard',           '数据库巡检',       0, 21),
        ('server-inspect',   '服务器巡检',       0, 22),
        ('scheduler',        '任务调度',         0, 23),
        ('awr',              'AWR报告',         0, 24),
        ('reports',          '巡检报告',         0, 25),
        ('server-history',   '历史记录',         0, 26),
        ('trend',            '趋势分析',         0, 27),
        ('datasources',     '数据源管理',       0, 31),
        ('inspection-config', '巡检配置',         0, 32),
        ('baseline-config',  '基线配置',         0, 33),
        ('server-thresholds', '阈值设置',         0, 34),
        ('rules',            '规则管理',         0, 35),
        ('rag',              '知识库',          0, 36),
        ('plugin-market',    '插件市场',         0, 41),
        ('sql-editor',       'SQL编辑器',       0, 42),
        ('remote-shell',     '远程终端',         0, 43),
        ('monitor-slow',     '慢查询监控',       0, 51),
        ('monitor-conn',     '连接池监控',       0, 52),
        ('ai',               'AI助手',          0, 53),
        ('intelligence',     '智能诊断中心',     0, 53),
        ('diag-history',     '诊断历史',         0, 53),
        ('oracle-client',    'Oracle客户端',     0, 54),
        ('notifier',         '通知管理',         0, 55),
        ('apikey',           'API密钥',         0, 56),
        ('shares',           '共享管理',         0, 57),
        ('data-management',  '数据管理',         0, 66),
        ('about',            '关于DBCheck',      0, 67),
    ]
    menu_model = MenuModel()
    for code, name, pid, order in menus_data:
        menu_model.create(code, name, parent_id=pid, sort_order=order)
    print(f"  ✅ 菜单数据已初始化: {len(menus_data)} 个菜单")

    # 3. 创建默认角色
    roles_data = [
        ('admin',    '系统管理员', '拥有所有权限'),
        ('viewer',   '只读用户',   '只能查看，不可修改'),
        ('operator', '运维人员',   '可读写大部分功能'),
    ]
    for code, name, desc in roles_data:
        db.execute(
            "INSERT OR IGNORE INTO um_role(role_code, role_name, description) VALUES(?,?,?)",
            (code, name, desc)
        )
    print("  ✅ 默认角色已创建: admin, viewer, operator")

    # 4. 给 admin 角色分配所有菜单权限
    menus = db.query_all("SELECT id, menu_code FROM um_menu")
    admin_role = db.query_one("SELECT id FROM um_role WHERE role_code='admin'")
    if admin_role and access_perm:
        for menu in menus:
            db.execute(
                "INSERT INTO um_role_menu_perm(role_id, menu_id, perm_id) VALUES(?,?,?)",
                (admin_role['id'], menu['id'], access_perm['id'])
            )
    print(f"  ✅ admin 角色已分配 {len(menus)} 个菜单权限")

    # 5. 给 viewer 角色分配只读菜单（首页、报告、慢查询监控、AI助手）
    viewer_role = db.query_one("SELECT id FROM um_role WHERE role_code='viewer'")
    if viewer_role and access_perm:
        viewer_menus = ['home', 'reports', 'monitor-slow', 'ai']
        cnt = 0
        for menu in menus:
            if menu['menu_code'] in viewer_menus:
                db.execute(
                    "INSERT INTO um_role_menu_perm(role_id, menu_id, perm_id) VALUES(?,?,?)",
                    (viewer_role['id'], menu['id'], access_perm['id'])
                )
                cnt += 1
        print(f"  ✅ viewer 角色已分配 {cnt} 个菜单权限: {viewer_menus}")

    # 6. 给 operator 角色分配运维菜单
    operator_role = db.query_one("SELECT id FROM um_role WHERE role_code='operator'")
    if operator_role and access_perm:
        operator_menus = ['home', 'wizard', 'monitor-slow', 'awr', 'reports', 'sql-editor', 'datasources']
        cnt = 0
        for menu in menus:
            if menu['menu_code'] in operator_menus:
                db.execute(
                    "INSERT INTO um_role_menu_perm(role_id, menu_id, perm_id) VALUES(?,?,?)",
                    (operator_role['id'], menu['id'], access_perm['id'])
                )
                cnt += 1
        print(f"  ✅ operator 角色已分配 {cnt} 个菜单权限: {operator_menus}")

    # 7. 创建默认管理员账户 admin / admin123
    pw_hash = hash_password('admin123')
    db.execute(
        "INSERT INTO um_user(username, password, nickname) VALUES('admin', ?, '系统管理员')",
        (pw_hash,)
    )
    print("  ✅ 默认管理员账户: admin / admin123")

    # 8. 绑定 admin 用户 → admin 角色
    admin_user = db.query_one("SELECT id FROM um_user WHERE username='admin'")
    if admin_user and admin_role:
        db.execute(
            "INSERT INTO um_user_role(user_id, role_id) VALUES(?,?)",
            (admin_user['id'], admin_role['id'])
        )
    print("  ✅ admin 用户已绑定 admin 角色")

    # 9. 创建演示用户
    demo_users = [
        ('viewer',   'viewer123',   '只读用户', 'viewer'),
        ('operator', 'operator123', '运维人员', 'operator'),
    ]
    for uname, upass, nick, role_code in demo_users:
        pw_hash = hash_password(upass)
        db.execute(
            "INSERT INTO um_user(username, password, nickname) VALUES(?,?,?)",
            (uname, pw_hash, nick)
        )
        user = db.query_one(f"SELECT id FROM um_user WHERE username='{uname}'")
        role = db.query_one(f"SELECT id FROM um_role WHERE role_code='{role_code}'")
        if user and role:
            db.execute(
                "INSERT INTO um_user_role(user_id, role_id) VALUES(?,?)",
                (user['id'], role['id'])
            )
    print("  ✅ 演示用户已创建:")
    print("     - viewer / viewer123 (只读用户)")
    print("     - operator / operator123 (运维人员)")

    print("\n🎉 RBAC 种子数据初始化完成!")


if __name__ == '__main__':
    init_seed_data()
