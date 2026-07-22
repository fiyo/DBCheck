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

# 菜单清单（单一数据源）：menu_code 必须与前端 index.html 中 nav-item 的 id 对应（去掉 "nav-" 前缀）
# init_seed_data() 与 sync_menus() 共用；新增菜单只需在此追加一行。
menus_data = [
    ('home',             'menu.home',            0, 10),
    ('wizard',           'menu.wizard',          0, 21),
    ('server-inspect',   'menu.server-inspect',  0, 22),
    ('scheduler',        'menu.scheduler',       0, 23),
    ('awr',              'menu.awr',             0, 24),
    ('reports',          'menu.reports',         0, 25),
    ('server-history',   'menu.server-history',  0, 26),
    ('trend',            'menu.trend',           0, 27),
    ('dm8-offline',      'menu.dm8-offline',     0, 28),
    ('datasources',     'menu.datasources',      0, 31),
    ('inspection-config','menu.inspection-config',0, 32),
    ('baseline-config',  'menu.baseline-config', 0, 33),
    ('server-thresholds','menu.server-thresholds',0, 34),
    ('rules',            'menu.rules',           0, 35),
    ('rag',              'menu.rag',             0, 36),
    ('plugin-market',    'menu.plugin-market',   0, 41),
    ('sql-editor',       'menu.sql-editor',      0, 42),
    ('remote-shell',     'menu.remote-shell',    0, 43),
    ('monitor-slow',     'menu.monitor-slow',    0, 51),
    ('monitor-conn',     'menu.monitor-conn',    0, 52),
    ('ai',               'menu.ai',              0, 53),
    ('oracle-client',    'menu.oracle-client',   0, 54),
    ('notifier',         'menu.notifier',        0, 55),
    ('apikey',           'menu.apikey',          0, 56),
    ('shares',           'menu.shares',          0, 57),
    ('data-management',  'menu.data-management', 0, 66),
    ('about',            'menu.about',           0, 67),
    ('disaster-recovery','menu.disaster-recovery',0, 65),
    ('diag-history',     'menu.diag-history',    0, 53),
]

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

    # 2. 初始化菜单数据（menus_data 为模块级单一数据源，定义见文件顶部）
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
        operator_menus = ['home', 'wizard', 'monitor-slow', 'awr', 'reports', 'sql-editor', 'datasources', 'disaster-recovery']
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


def sync_menus():
    """每次启动幂等同步菜单到 um_menu（upsert），并对菜单按 ROLE_MENU_MAP 补齐角色授权。

    对新增菜单 INSERT；对已在 um_menu 的现有行也 UPDATE menu_name 为 i18n key
    （按 menu_code 匹配），使旧的中文 menu_name 在每次启动时被幂等覆盖成 key。
    绝不删除任何既有授权；数据库不可达时仅告警不阻断启动。
    """
    try:
        db = DBManager()
        menu_model = MenuModel()
        existing = {m['menu_code']: m for m in db.query_all("SELECT id, menu_code, menu_name FROM um_menu")}
        for code, name, pid, order in menus_data:
            if code not in existing:
                menu_model.create(code, name, parent_id=pid, sort_order=order)
                print(f"  [sync] 新增菜单: {code}")
            else:
                # 幂等覆盖：旧的中文 menu_name 在每次启动时被覆盖成 i18n key
                if existing[code]['menu_name'] != name:
                    menu_model.update(
                        existing[code]['id'],
                        menu_name=name,
                        parent_id=pid,
                        sort_order=order,
                    )
                    print(f"  [sync] 更新菜单 menu_name: {code} -> {name}")

        role_menu_map = {
            'admin': 'ALL',
            'operator': ['home', 'wizard', 'monitor-slow', 'awr', 'reports', 'sql-editor', 'datasources', 'disaster-recovery'],
            'viewer': ['home', 'reports', 'monitor-slow', 'ai'],
        }
        access_perm = db.query_one("SELECT id FROM um_permission WHERE perm_level=1")
        if not access_perm:
            return
        for role_code, menus in role_menu_map.items():
            role = db.query_one("SELECT id FROM um_role WHERE role_code=?", (role_code,))
            if not role:
                continue
            if menus == 'ALL':
                menus = [m['menu_code'] for m in db.query_all("SELECT menu_code FROM um_menu")]
            for code in menus:
                menu = db.query_one("SELECT id FROM um_menu WHERE menu_code=?", (code,))
                if not menu:
                    continue
                cnt = db.query_one(
                    "SELECT COUNT(*) c FROM um_role_menu_perm WHERE role_id=? AND menu_id=? AND perm_id=?",
                    (role['id'], menu['id'], access_perm['id']),
                )
                if cnt['c'] == 0:
                    db.execute(
                        "INSERT INTO um_role_menu_perm(role_id, menu_id, perm_id) VALUES(?,?,?)",
                        (role['id'], menu['id'], access_perm['id']),
                    )
        print("  [OK] 菜单同步完成（含容灾备份 disaster-recovery）")
    except Exception as e:
        print(f"  [WARN] 菜单同步失败: {e}")


if __name__ == '__main__':
    init_seed_data()
