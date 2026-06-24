-- ============================================
-- DBCheck 用户管理模块 (RBAC) 数据库 Schema
-- 版本: 1.0.0
-- 数据库: SQLite (pro_data/um_rbac.db)
-- ============================================

-- ============================================
-- 1. 用户表
-- ============================================
CREATE TABLE IF NOT EXISTS um_user (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    VARCHAR(64)  NOT NULL UNIQUE,
    password    VARCHAR(256) NOT NULL,   -- bcrypt 加密存储
    nickname    VARCHAR(64)  DEFAULT '',
    email       VARCHAR(128) DEFAULT '',
    status      TINYINT      DEFAULT 1,  -- 1=启用 0=禁用
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 2. 角色表
-- ============================================
CREATE TABLE IF NOT EXISTS um_role (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    role_code   VARCHAR(32)  NOT NULL UNIQUE,  -- 如 admin / viewer / operator
    role_name   VARCHAR(64)  NOT NULL,         -- 如 管理员 / 只读用户 / 运维人员
    description VARCHAR(256) DEFAULT '',
    status      TINYINT      DEFAULT 1,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 3. 用户-角色关联表（多对多）
-- ============================================
CREATE TABLE IF NOT EXISTS um_user_role (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    UNIQUE(user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES um_user(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES um_role(id) ON DELETE CASCADE
);

-- ============================================
-- 4. 权限定义表（操作权限级别）
-- ============================================
CREATE TABLE IF NOT EXISTS um_permission (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    perm_code       VARCHAR(64)  NOT NULL UNIQUE,
    perm_name       VARCHAR(64)  NOT NULL,
    perm_level      TINYINT      NOT NULL,
        -- 1=read_only  2=read_write  3=modify  4=admin
    description     VARCHAR(256) DEFAULT ''
);

-- 初始权限种子数据
INSERT OR IGNORE INTO um_permission(perm_code, perm_name, perm_level) VALUES
    ('read_only',  '只读',   1),
    ('read_write', '读写',   2),
    ('modify',     '修改',   3),
    ('admin',      '管理',   4);

-- ============================================
-- 5. 菜单/模块表
-- ============================================
CREATE TABLE IF NOT EXISTS um_menu (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_code   VARCHAR(64)  NOT NULL UNIQUE,  -- 如 check / slow_query / ai_diagnosis
    menu_name   VARCHAR(64)  NOT NULL,         -- 如 数据库检查 / 慢查询分析 / AI诊断
    parent_id   INTEGER      DEFAULT 0,        -- 父菜单 ID，0=顶级
    sort_order  INTEGER      DEFAULT 0,
    menu_type   TINYINT      DEFAULT 1,        -- 1=菜单 2=按钮/操作
    status      TINYINT      DEFAULT 1
);

-- 初始菜单种子数据（根据 DBCheck 实际功能）
INSERT OR IGNORE INTO um_menu(menu_code, menu_name, parent_id, sort_order) VALUES
    ('dashboard',     '仪表盘',       0, 1),
    ('db_check',      '数据库检查',    0, 2),
    ('slow_query',    '慢查询分析',    0, 3),
    ('ai_diagnosis',  'AI 诊断',      0, 4),
    ('plugin_market', '插件市场',      0, 5),
    ('asset_manage',  '资产管理',      0, 6),
    ('system_manage', '系统管理',      0, 7);

-- ============================================
-- 6. 角色-菜单-权限关联表
-- ============================================
-- 每条记录表示：某个角色 对 某个菜单 拥有 某个级别 的权限
CREATE TABLE IF NOT EXISTS um_role_menu_perm (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id     INTEGER NOT NULL,
    menu_id     INTEGER NOT NULL,
    perm_id     INTEGER NOT NULL,   -- 引用 um_permission.id
    UNIQUE(role_id, menu_id),
    FOREIGN KEY (role_id) REFERENCES um_role(id)    ON DELETE CASCADE,
    FOREIGN KEY (menu_id) REFERENCES um_menu(id)    ON DELETE CASCADE,
    FOREIGN KEY (perm_id) REFERENCES um_permission(id) ON DELETE CASCADE
);

-- ============================================
-- 7. 用户-数据库资产绑定表（数据权限隔离）
-- ============================================
CREATE TABLE IF NOT EXISTS um_user_asset_bind (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    asset_id    INTEGER NOT NULL,  -- 引用 DBCheck 的数据库资产表 ID
    UNIQUE(user_id, asset_id),
    FOREIGN KEY (user_id) REFERENCES um_user(id) ON DELETE CASCADE
);

-- ============================================
-- 8. 用户-模块绑定表（覆盖角色默认配置）
-- ============================================
CREATE TABLE IF NOT EXISTS um_user_module_bind (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    menu_id     INTEGER NOT NULL,
    perm_id     INTEGER NOT NULL,
    UNIQUE(user_id, menu_id),
    FOREIGN KEY (user_id) REFERENCES um_user(id) ON DELETE CASCADE,
    FOREIGN KEY (menu_id) REFERENCES um_menu(id) ON DELETE CASCADE,
    FOREIGN KEY (perm_id) REFERENCES um_permission(id) ON DELETE CASCADE
);

-- ============================================
-- 9. 操作审计日志
-- ============================================
CREATE TABLE IF NOT EXISTS um_audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    username    VARCHAR(64),
    action      VARCHAR(128),   -- 如 login / create_role / bind_asset
    target      VARCHAR(128),   -- 操作对象
    detail      TEXT,
    ip_address  VARCHAR(64),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
