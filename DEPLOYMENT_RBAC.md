# DBCheck RBAC 用户管理模块 - 部署说明

## 版本信息

- 基于 DBCheck (fiyo/DBCheck) 二次开发
- 新增模块: RBAC 用户管理系统
- 开发分支: `feature/user-management`
- 仓库地址: https://github.com/acdante-zhang/DBCheck

---

## 一、功能概述

在 DBCheck 原有数据库巡检功能基础上，新增了完整的 **RBAC（基于角色的访问控制）用户管理模块**，支持：

| 功能维度 | 说明 |
|----------|------|
| **用户认证** | JWT Token 登录/登出，bcrypt 密码加密 |
| **角色管理** | 预置 admin/viewer/operator 三种角色，支持自定义 |
| **菜单权限** | 7 个核心菜单的细粒度权限控制（只读/读写/修改/管理） |
| **资产隔离** | 用户只能看到绑定的数据库资产 |
| **审计日志** | 记录所有登录和操作行为 |
| **Web 管理后台** | 完整的用户管理、角色管理、权限配置界面 |

---

## 二、快速部署

### 2.1 环境要求

- Python 3.9+
- Git
- pip

### 2.2 一键启动

**Linux/Mac:**
```bash
git clone https://github.com/acdante-zhang/DBCheck.git
cd DBCheck
git checkout feature/user-management
chmod +x start.sh
./start.sh
```

**Windows:**
```cmd
git clone https://github.com/acdante-zhang/DBCheck.git
cd DBCheck
git checkout feature/user-management
start.bat
```

### 2.3 手动部署

```bash
# 1. 克隆项目
git clone https://github.com/acdante-zhang/DBCheck.git
cd DBCheck
git checkout feature/user-management

# 2. 安装依赖
pip install -r requirements.txt

# 3. 初始化 RBAC 数据
python -m user_management.seed

# 4. 启动服务
python web_ui.py
```

### 2.4 Docker 部署

```bash
docker build -t dbcheck-rbac .
docker run -p 5003:5003 dbcheck-rbac
```

---

## 三、访问地址

| 地址 | 说明 |
|------|------|
| http://localhost:5003 | DBCheck 主页面 |
| http://localhost:5003/um/login | RBAC 登录页面 |
| http://localhost:5003/um/admin | 系统管理后台 |

---

## 四、默认账户

| 用户名 | 密码 | 角色 | 说明 |
|--------|------|------|------|
| `admin` | `admin123` | 系统管理员 | 拥有所有权限 |
| `viewer` | `viewer123` | 只读用户 | 只能查看，不可修改 |
| `operator` | `operator123` | 运维人员 | 可读写大部分功能 |
| `dbcheck` | `dbcheck` | 管理员 | 原有认证系统保留 |

---

## 五、API 接口

### 5.1 认证接口

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/api/um/auth/login` | 用户登录 | 否 |
| POST | `/api/um/auth/logout` | 用户登出 | JWT |
| GET | `/api/um/auth/status` | 获取当前用户状态 | JWT |
| POST | `/api/um/auth/change-password` | 修改密码 | JWT |

### 5.2 用户管理

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/um/user/list` | 用户列表 | system_manage:1 |
| POST | `/api/um/user` | 创建用户 | system_manage:3 |
| GET | `/api/um/user/<id>` | 用户详情 | system_manage:1 |
| PUT | `/api/um/user/<id>` | 更新用户 | system_manage:3 |
| DELETE | `/api/um/user/<id>` | 删除用户 | system_manage:4 |
| GET | `/api/um/user/menus` | 我的可见菜单 | JWT |

### 5.3 角色管理

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/um/role/list` | 角色列表 | system_manage:1 |
| POST | `/api/um/role` | 创建角色 | system_manage:3 |
| GET | `/api/um/role/<id>` | 角色详情 | system_manage:1 |
| PUT | `/api/um/role/<id>` | 更新角色 | system_manage:3 |
| DELETE | `/api/um/role/<id>` | 删除角色 | system_manage:4 |
| PUT | `/api/um/role/<id>/menu-perm` | 配置菜单权限 | system_manage:4 |

---

## 六、目录结构

```
DBCheck/
├── user_management/           # RBAC 用户管理模块
│   ├── models/                # 数据模型层
│   │   ├── db_manager.py      # SQLite 数据库管理器
│   │   ├── user.py            # 用户模型
│   │   ├── role.py            # 角色模型
│   │   ├── menu.py            # 菜单模型
│   │   └── permission.py      # 权限模型
│   ├── services/              # 业务逻辑层
│   │   ├── auth_service.py    # 认证服务
│   │   ├── user_service.py    # 用户服务
│   │   ├── role_service.py    # 角色服务
│   │   ├── menu_service.py    # 菜单服务
│   │   └── perm_service.py    # 权限校验服务
│   ├── routes/                # API 路由层
│   │   ├── auth_routes.py     # 认证 API
│   │   ├── user_routes.py     # 用户管理 API
│   │   ├── role_routes.py     # 角色管理 API
│   │   └── menu_routes.py     # 菜单管理 API
│   ├── utils/                 # 工具层
│   │   ├── password.py        # bcrypt 密码工具
│   │   ├── jwt_util.py        # JWT Token 工具
│   │   └── auth_decorator.py  # 权限装饰器
│   ├── tests/                 # 测试套件
│   │   └── test_all.py        # 39 个完整测试用例
│   └── seed.py                # 种子数据初始化
├── db/
│   └── user_management_schema.sql  # 数据库建表脚本
├── web_templates/
│   ├── user_management/
│   │   ├── login.html         # 登录页面
│   │   └── admin.html         # 系统管理后台
│   └── static/
│       └── auth-guard.js      # 前端权限守卫
├── web_ui.py                  # 主应用（已集成 RBAC）
├── start.sh                   # Linux 启动脚本
├── start.bat                  # Windows 启动脚本
└── requirements.txt           # 依赖（已添加 bcrypt, PyJWT）
```

---

## 七、权限模型

### 权限级别

| 级别 | 代码 | 说明 |
|------|------|------|
| 1 | read_only | 只读 |
| 2 | read_write | 读写 |
| 3 | modify | 修改（含创建/编辑/删除） |
| 4 | admin | 管理（含角色分配/权限配置） |

### 权限判断优先级

1. **用户级覆盖** (`um_user_module_bind`) > 角色级配置 (`um_role_menu_perm`)
2. 同一用户多角色时，取**最高权限**
3. admin 角色的资产隔离被**跳过**（可看所有资产）

---

## 八、测试

运行测试套件：
```bash
pytest user_management/tests/ -v
```

测试覆盖：认证(10)、用户CRUD(9)、角色权限(6)、菜单(6)、用户绑定(3)、资产隔离(3)、页面(2) = **39 个用例**

---

## 九、安全特性

- 密码使用 **bcrypt** 加密存储
- API 使用 **JWT Bearer Token** 认证
- SQL 注入防护（参数化查询）
- 操作审计日志完整记录
- 权限校验使用装饰器模式，代码侵入性低
