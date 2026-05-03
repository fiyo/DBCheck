# DBCheck v2.4.0 发版说明

## 版本概要

**版本号：** v2.4.0
**发版日期：** 2026-05-03
**核心新增：** 数据源管理中心、规则引擎、RAG 知识库、定时巡检调度

---

## 新增功能

### 1. 数据源管理模块（Pro）

统一管理多数据库实例，支持分组管理、连接测试、CSV 导入导出。

| 功能 | 说明 |
|------|------|
| 多数据库支持 | MySQL / PostgreSQL / Oracle / SQL Server / DM8 / TiDB |
| 实例信息 | 自定义标签、分组、端口、用户名 |
| Oracle 专属 | 服务名/SID 配置、SYSDBA 特权连接 |
| 连接测试 | 一键测试数据库连接，实时返回结果 |
| 分组管理 | 按业务/环境分组，支持自定义颜色标签 |
| CSV 批量导入导出 | 批量导入/导出数据源配置 |

**核心文件：**
- `pro/instance_manager.py` - 数据源实例管理器

**Web UI 入口：** 左侧导航「🗄️ 数据源管理」

**新增 API 接口：**

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/pro/datasources` | GET/POST | 列出/添加数据源 |
| `/api/pro/datasources/<id>` | GET/PUT/DELETE | 获取/更新/删除数据源 |
| `/api/pro/datasources/<id>/test` | POST | 测试数据源连接 |
| `/api/pro/datasources/import` | POST | CSV 批量导入 |
| `/api/pro/datasources/export` | GET | CSV 导出 |
| `/api/pro/groups` | GET/POST | 列出/创建分组 |
| `/api/pro/groups/<name>` | PUT/DELETE | 更新/删除分组 |

---

### 2. 规则引擎（Pro）

支持 YAML 规则描述文件，用户无需修改 Python 代码即可添加、禁用检查规则。

| 特性 | 说明 |
|------|------|
| YAML 规则配置 | 将检查规则定义为 YAML 文件，热加载生效 |
| 安全沙箱执行 | 使用 ast 预检查 + eval 白名单，禁止危险操作 |
| 内置 + 自定义规则 | 内置规则目录 + 自定义规则目录，分离管理 |
| 规则启用/禁用 | 通过 overrides.yaml 控制规则开关 |
| 多数据库支持 | 规则可指定适用的数据库类型 |
| 优先级排序 | 按 high/medium/low/info 优先级输出结果 |

**核心文件：**
- `pro/rule_engine.py` - 规则引擎核心
- `pro/rules/builtin/` - 内置规则目录
- `pro/rules/custom/` - 自定义规则目录
- `pro/rules/overrides.yaml` - 规则覆盖配置

**规则 YAML 示例：**

```yaml
rules:
  - id: mysql_conn_overuse
    name: 连接数超限告警
    name_en: Connection Overuse Alert
    db_types: [mysql]
    severity: high
    priority: high
    params:
      threshold: 80
      used: ${context.max_used_connections[0].Value}
      max: ${context.max_connections[0].Value}
    condition: (used / max * 100) > threshold
    message_zh: "连接数使用率超过 {threshold}%，当前 {used}/{max}"
    message_en: "Connection usage exceeds {threshold}%, current {used}/{max}"
    fix_sql: "SET GLOBAL max_connections = {max_conn};"
```

**Web UI 入口：** 规则管理页面（可查看/启用/禁用规则）

---

### 3. RAG 知识库

基于本地 Ollama 的 RAG（检索增强生成）知识库，支持文档向量化检索。

| 功能 | 说明 |
|------|------|
| 文档上传 | 支持 PDF、TXT、MD、DOCX 格式 |
| 智能分块 | 自动将文档切分为合适大小的块 |
| 向量化存储 | 使用 Ollama Embedding 模型生成向量 |
| 语义检索 | 支持相似度检索，返回相关文档块 |
| 数据库分类 | 按 MySQL/PostgreSQL/Oracle/DM/SQL Server/TiDB 分类管理 |

**核心文件：**
- `rag/manager.py` - RAG 文档管理器
- `rag/document_processor.py` - 文档处理（加载、分块）
- `rag/embeddings.py` - Ollama 向量化接口
- `rag/vector_store.py` - 向量存储

**工作流程：**
```
上传文档 → 文档加载 → 智能分块 → Ollama 向量化 → 向量存储
                                    ↓
                            相似度检索 → 返回相关块
```

**使用前提：** 本地 Ollama 服务运行中（localhost:11434），并安装 embedding 模型：
```bash
ollama pull nomic-embed-text
```

**API 接口：**

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/rag/stats` | GET | 获取知识库统计信息 |
| `/api/rag/documents` | GET | 列出所有文档 |
| `/api/rag/documents` | POST | 上传新文档 |
| `/api/rag/documents/<id>` | DELETE | 删除文档 |
| `/api/rag/search` | POST | 语义检索 |

---

### 4. 定时巡检调度

支持 Cron 表达式配置，自动定时执行数据库巡检。

| 功能 | 说明 |
|------|------|
| Cron 表达式 | 支持秒/分/时/日/月/周完整配置 |
| 预设快捷选项 | 每天凌晨、工作日每天、自定义周期 |
| 持久化存储 | 任务配置写入 scheduler_jobs.json，重启自动恢复 |
| 立即执行 | 支持手动触发立即执行 |
| 启用/禁用 | 可随时暂停和恢复定时任务 |

**核心文件：**
- `scheduler.py` - 定时调度核心模块
- `notifier.py` - 邮件 + Webhook 通知模块

**Web UI 入口：** 左侧导航「⏰ 定时巡检」

**API 接口：**

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/scheduler/jobs` | GET/POST | 列出/创建定时任务 |
| `/api/scheduler/jobs/<id>` | DELETE | 删除定时任务 |
| `/api/scheduler/jobs/<id>/toggle` | POST | 启用/禁用任务 |
| `/api/scheduler/jobs/<id>/run` | POST | 立即执行任务 |
| `/api/notifier/config` | GET/POST | 获取/更新通知配置 |
| `/api/notifier/test-email` | POST | 发送测试邮件 |
| `/api/notifier/test-webhook` | POST | 测试 Webhook |

---

### 5. 首页布局优化

全新设计的首页布局，更加现代化和清晰。

**新增区域：**
- **核心功能区：** 2 列卡片网格展示核心功能，突出"快速巡检"入口
- **数据库支持展示：** 6 列卡片网格展示所有支持的数据库类型

**响应式设计：**
| 屏幕尺寸 | 数据库展示 | 功能卡片 |
|---------|-----------|---------|
| 大屏幕 | 6 列 | 2 列 |
| 中等屏幕 | 3 列 | 2 列 |
| 小屏幕 | 2 列 | 单列 |

---

### 6. Oracle 连接优化

**Oracle 默认用户名改为 system：**
- 新建数据源选择 Oracle 时，用户名输入框自动填入 `system`
- 新建巡检向导同样适用

**服务名标签优化：**
- 标签从"服务名 (Oracle)"改为"服务名/SID (Service Name)"
- 添加 Oracle 小标签，与新建巡检向导风格一致

**SYSDBA 复选框：**
- 新增"以 SYSDBA 身份连接"复选框
- 与服务名输入框之间增加适当间距

---

## Bug 修复

| 模块 | 修复内容 |
|------|---------|
| 数据源管理 | Oracle 数据源保存时未正确处理服务名和 SYSDBA 标识 |
| 数据源管理 | 编辑数据源时密码未正确回显 |
| 数据源管理 | CSV 导入时字段映射错误 |
| Web UI | Oracle 数据源默认用户名未更新的问题（改为 system） |
| Web UI | SYSDBA 复选框与服务名输入框间距过小的问题 |

---

## 升级指南

### 已有用户升级步骤

```bash
# 1. 拉取代码
git pull origin main

# 2. 安装新依赖（定时巡检需要）
pip install apscheduler>=3.10.0

# 3. 重启 Web UI
python web_ui.py
```

### 新功能验证

1. **数据源管理**
   - 访问 Web UI → 点击左侧导航「🗄️ 数据源管理」
   - 添加一个数据源进行测试

2. **定时巡检**
   - 点击左侧导航「⏰ 定时巡检」
   - 创建第一个定时任务

3. **规则引擎**
   - 检查 `pro/rules/` 目录下的规则文件

4. **RAG 知识库**
   - 确保 Ollama 服务运行中
   - 上传一份数据库文档进行测试

---

## 已知问题

- [ ] 数据源管理暂无批量巡检入口（计划下版本添加）
- [ ] 数据源管理暂无权限控制
- [ ] CSV 导入时暂不支持覆盖已存在的数据源

---

## 贡献者

- sdfiyon@gmail.com

---

*DBCheck — 开源数据库健康检查工具*
