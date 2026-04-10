---
name: dbcheck
description: 执行 MySQL 或 PostgreSQL 数据库健康巡检，一键生成专业 Word 巡检报告。适用于 DBA 和运维人员快速掌握数据库运行状况、排查风险。项目地址：https://github.com/fiyo/DBCheck.git
license: MIT
---

# DBCheck — 数据库自动化巡检工具

> **安全说明**：本 Skill 需要调用本地 Python 脚本连接数据库并读取系统信息（CPU/内存/磁盘），这是数据库巡检的合法需求。脚本不会访问用户授权范围之外的任何资源，不会外传任何凭据或数据。

## 触发条件

当用户请求以下任意一项时，加载本 Skill 并执行：

- 对数据库做健康检查 / 健康巡检 / 体检
- 检查 MySQL / PostgreSQL 的运行状态、连接数、缓存命中率等
- 生成数据库巡检报告 / 健康报告
- 数据库风险排查 / 巡检
- "帮我巡检一下 XX 数据库"
- "生成一份 MySQL/PostgreSQL 巡检报告"

## 前置准备

### 必需信息

开始巡检前，**必须向用户收集以下信息**（缺少任何一项都要询问，不要自行猜测）：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `db_type` | 数据库类型 | 需用户确认：`mysql` 或 `pg` |
| `host` | 数据库主机 IP 或域名 | 需用户确认 |
| `port` | 数据库端口 | MySQL 默认 3306，PG 默认 5432 |
| `user` | 数据库用户名 | 需用户确认 |
| `password` | 数据库密码 | 需用户确认 |
| `label` | 数据库标签（用于报告命名） | 需用户确认，如 "生产库-MySQL" |
| `inspector` | 巡检人员姓名 | 需用户确认 |

### 可选信息

| 参数 | 说明 |
|------|------|
| `database` | 数据库名（PG 专用，默认 `postgres`） |
| `ssh_host` | SSH 主机 IP（采集系统资源时需要） |
| `ssh_port` | SSH 端口，默认 22 |
| `ssh_user` | SSH 用户名 |
| `ssh_password` | SSH 密码 |
| `ssh_key` | SSH 私钥文件路径（与密码二选一） |

> **安全提醒**：凭据仅用于建立连接，不会写入磁盘文件或发送到任何第三方。巡检结果（Word 报告）保存在本地 scripts 目录下。

## 工具选择

使用 `execute_command` 工具执行 Python 脚本。**不要**使用 `del /F` 或 `rm -rf` 等危险命令。

### 脚本路径

DBCheck 工具位于 Agent 的 **skills 目录**中，通过 `run_inspection.py` 非交互式入口执行。

### 依赖检查

先检查依赖是否完整：

```bash
python -c "import pymysql, psycopg2, docxtpl, paramiko, psutil, openpyxl, docx" 2>&1
```

如有缺失，提示用户安装：
```bash
pip install pymysql psycopg2-binary paramiko openpyxl docxtpl python-docx pandas psutil
```

### 执行巡检

#### 基本用法（MySQL）

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type mysql \
    --host <数据库IP> \
    --port 3306 \
    --user <用户名> \
    --password <密码> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

#### 基本用法（PostgreSQL）

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type pg \
    --host <数据库IP> \
    --port 5432 \
    --user <用户名> \
    --password <密码> \
    --database <数据库名，默认postgres> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

#### 带 SSH 系统资源采集

```bash
python run_inspection.py \
    --type mysql \
    --host <IP> \
    --user <用户名> \
    --password <密码> \
    --label "<标签>" \
    --inspector "<姓名>" \
    --ssh-host <SSH主机IP> \
    --ssh-user <SSH用户名> \
    --ssh-password <SSH密码>
```

#### 完整参数参考

```
--type          数据库类型: mysql 或 pg（必需）
--host          数据库主机 IP 或域名（必需）
--port          数据库端口（默认 MySQL 3306，PG 5432）
--user          数据库用户名（必需）
--password      数据库密码（必需）
--database      数据库名（PG 专用，默认 postgres）
--label         数据库标签，用于报告命名（必需）
--inspector     巡检人员姓名（必需）
--ssh-host      SSH 主机 IP（可选）
--ssh-port      SSH 端口（默认 22）
--ssh-user      SSH 用户名（可选）
--ssh-password  SSH 密码（可选）
--ssh-key       SSH 私钥文件路径（可选，与密码二选一）
```

### 报告输出

- 报告自动保存在 `<scripts_dir>/reports/` 目录下
- 文件名格式：`MySQL巡检报告_<标签>_<时间戳>.docx` 或 `PostgreSQL巡检报告_<标签>_<时间戳>.docx`
- 报告可用 Microsoft Word 或 WPS 打开

### 报告结构（用户可参考）

生成的 Word 报告包含以下章节：

- **封面**：数据库基本信息、巡检人员、报告时间
- **健康状态概览**：总体评级及发现问题数量
- **系统资源检查**：CPU、内存、磁盘详细指标（需要 SSH 或本地采集）
- **数据库配置检查**：连接、内存、日志相关关键参数
- **性能分析**：QPS、锁信息、异常连接、索引使用情况
- **数据库信息**：各库大小、当前活跃进程
- **安全信息**：数据库用户列表及权限概要
- **风险与建议**：自动分析出的潜在问题及优化方向

### 常见错误处理

| 错误信息 | 原因 | 解决方案 |
|---------|------|--------|
| `pymysql: Access denied` | 用户名或密码错误 | 核对数据库账户信息 |
| `Can't connect to MySQL server` | 防火墙阻止或端口不对 | 确认端口、防火墙、安全组规则 |
| `Permission denied`（SSH） | SSH 认证失败 | 检查用户名密码或私钥路径 |
| `command not found: lscpu` | 精简版 Linux 缺少命令 | 报告该部分显示"N/A"，不影响数据库数据 |

### 结果展示

巡检完成后：

1. 告知用户报告文件完整路径
2. 使用 `open_result_view` 工具打开报告文件供用户查看
3. 简要汇报关键发现（如发现高风险项，单独列出）
4. 提示用户报告中风险建议仅供参考，需结合实际业务评估

## 限制与注意事项

- 本 Skill 仅用于**合法授权的数据库巡检**，请勿用于未授权访问
- SSH 采集依赖目标机器的 `top`、`free`、`df`、`lscpu` 命令
- 报告生成依赖 `python-docx` 和 `docxtpl` 库，务必确保已安装
- 如果用户同时有 MySQL 和 PostgreSQL，可分别巡检后再汇总报告
