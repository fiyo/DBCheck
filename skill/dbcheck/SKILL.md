---
name: dbcheck
description: 执行 MySQL、PostgreSQL、Oracle、SQL Server、DM8 数据库健康巡检，内置 100+ 条增强风险分析规则 + 本地 Ollama AI 大模型诊断建议，一键生成专业 Word 巡检报告。适用于 DBA 和运维人员快速掌握数据库运行状况、排查风险。项目地址：https://github.com/fiyo/DBCheck.git
license: MIT
metadata:
  {
    "openclaw":
      {
        "emoji": "🔍",
      },
  }
---

# DBCheck — 数据库自动化巡检工具

> **安全声明（必读）**
>
> **本 Skill 的数据流向完全可控，如下所示：**
> ```
> 用户凭据 → [本地 Python 脚本] → 数据库服务器 → 巡检结果 → [本地 Word 报告]
>                                           ↘
>                                            → [本地 Ollama] → AI 建议（可选）
> ```
>
> - ✅ **数据库凭据**：仅用于建立连接，**不会写入磁盘持久文件**，**不会发送到任何第三方**
> - ✅ **AI 诊断**：**仅支持本地部署的 Ollama**（地址必须为 localhost / 127.0.0.1），代码层面强制校验，**不支持 OpenAI / DeepSeek 等任何远程 API**
> - ✅ **SSH 连接**：使用 `AutoAddPolicy` 自动接受主机密钥（适用于内网可信环境），连接仅用于采集系统资源指标
> - ✅ **本地文件写入**：巡检结果以 Word 报告形式保存在本地 `reports/` 目录；`history.json` 存储历史趋势数据（纯数值指标）；`autoDoc.log` 为运行日志。**所有文件均在本地，不含敏感凭据**
> - ⚠️ **限制**：本 Skill 仅用于合法授权的数据库巡检，请勿用于未授权访问

## 核心能力

| 能力 | 说明 |
|------|------|
| 📊 100+ 条增强风险规则 | 覆盖 MySQL 18+ / PostgreSQL 16+ / Oracle 20+ / SQL Server 15+ / DM8 16+ 全维度，每条附修复 SQL |
| 🤖 AI 智能诊断（仅本地 Ollama） | 调用本地部署的大模型（需安装 Ollama）生成个性化优化建议，**API 地址强制校验为 localhost，数据绝不外传** |
| 📈 历史趋势分析 | 多次巡检数据聚合，生成指标趋势折线图（存储在本地 history.json） |
| 🌐 Web UI 可视化 | 浏览器完成全部操作，含趋势图和 AI 配置页面 |

## 安全架构详解

### 数据不外传的保障机制

1. **代码层硬限制**：`AIAdvisor` 类在初始化时检查 backend 值，`openai`/`deepseek`/`custom` 等非 ollama 值会被强制降级为 `disabled`
2. **URL 地址校验**：Ollama 的 API URL 必须是 `localhost` 或 `127.0.0.1`，非本地地址会导致 AI 诊断自动禁用
3. **Web API 校验**：`/api/ai_config` 接口保存配置时同时校验 backend 和 URL，双重保险
4. **无远程 API 代码**：已移除 `_call_openai()` 等所有远程调用方法，仅保留 `_call_ollama()`

### 本地文件说明

| 文件/目录 | 用途 | 是否含敏感信息 |
|-----------|------|----------------|
| `reports/*.docx` | 巡检 Word 报告 | 含数据库结构和配置（不含密码） |
| `history.json` | 历史趋势数据 | 仅含数值指标（CPU/内存/连接数等） |
| `autoDoc.log` | 运行日志 | 含执行过程信息（不含密码） |

## 支持的数据库

| 数据库 | 驱动 | 默认端口 | SSH 系统采集 | 特有巡检项 |
|--------|------|---------|-------------|-----------|
| 🐬 MySQL | pymysql | 3306 | ✅ 支持 | 主从复制、binlog、查询缓存 |
| 🐘 PostgreSQL | psycopg2 | 5432 | ✅ 支持 | 归档模式、缓存命中率、dead tuples |
| 🔴 Oracle | oracledb / cx_Oracle | 1521 | ✅ 支持 | 表空间、SGA/PGA、RAC、ASM、Data Guard、Redo、备份 |
| 🟠 SQL Server | pyodbc (ODBC Driver 17) | 1433 | ❌ 不支持 | 等待统计、锁与阻塞、备份检查 |
| 🟡 DM8 | dmpython | 5236 | ✅ 支持 | 表空间、SGA/PGA、DM8 缓冲池、备份、DM8 特有视图 |

> **DM8 SSH 说明**：通过 SSH 采集达梦服务器的主机级系统信息（CPU/内存/磁盘）。连接失败时自动降级为本地采集器。

## 触发条件

当用户请求以下任意一项时，加载本 Skill 并执行：

- 对数据库做健康检查 / 健康巡检 / 体检
- 检查 MySQL / PostgreSQL / Oracle / SQL Server / DM8（达梦）的运行状态、连接数、缓存命中率等
- 生成数据库巡检报告 / 健康报告
- 数据库风险排查 / 巡检
- "帮我巡检一下 XX 数据库"
- "生成一份 MySQL/PostgreSQL/Oracle/SQL Server/DM8 巡检报告"

## 前置准备

### 必需信息

开始巡检前，**必须向用户收集以下信息**（缺少任何一项都要询问，不要自行猜测）：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `db_type` | 数据库类型 | 需用户确认：`mysql` / `pg` / `oracle` / `sqlserver` / `dm` |
| `host` | 数据库主机 IP 或域名 | 需用户确认 |
| `port` | 数据库端口 | MySQL 默认 3306，PG 默认 5432，Oracle 默认 1521，DM8 默认 5236 |
| `user` | 数据库用户名 | 需用户确认 |
| `password` | 数据库密码 | 需用户确认 |
| `label` | 数据库标签（用于报告命名） | 需用户确认，如 "生产库-MySQL" |
| `inspector` | 巡检人员姓名 | 需用户确认 |

### Oracle 专用参数

| 参数 | 说明 |
|------|------|
| `service_name` 或 `sid` | Oracle 服务名或 SID（二选一，必填） |
| 特权连接 | 用户名输入 `sys as sysdba` 可建立 SYSDBA 特权连接 |

### DM8 注意事项

| 参数 | 说明 |
|------|------|
| `database` | **无需填写**，DM8 连接用户即 Schema，无需指定 database 参数 |
| 驱动安装 | 需 `pip install dmpython` |
| 连接特性 | dmPython 为惰性连接，connect() 成功不代表真正连通，需通过游标执行探测 SQL 才能确认 |

### 可选信息

| 参数 | 说明 |
|------|------|
| `ssh_host` | SSH 主机 IP（采集系统资源时需要，DM8 除外） |
| `ssh_port` | SSH 端口，默认 22 |
| `ssh_user` | SSH 用户名 |
| `ssh_password` | SSH 密码 |
| `ssh_key` | SSH 私钥文件路径（与密码二选一） |

> **安全提醒**：
> - 数据库/SSH 凭据**仅用于建立连接**，不写入持久文件，不发送到任何第三方
> - AI 诊断（如启用）**仅使用本地 Ollama**（localhost），API 地址在代码和 API 层面均有校验
> - 巡检结果（Word 报告）保存在本地 `reports/` 目录，历史数据存储在本地 `history.json`
> - SSH 连接使用 `AutoAddPolicy`（适合内网可信环境）

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
pip install pymysql psycopg2-binary paramiko openpyxl docxtpl python-docx pandas psutil flask oracledb dmpython pyodbc flask_socketio
```

### 执行巡检

#### MySQL 巡检

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

#### PostgreSQL 巡检

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

#### Oracle 巡检

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type oracle \
    --host <数据库IP> \
    --port 1521 \
    --user <用户名> \
    --password <密码> \
    --service_name <服务名> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

> **Oracle 特权连接**：用户名输入 `sys as sysdba`，工具自动识别并使用 SYSDBA 模式连接。

#### SQL Server 巡检

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type sqlserver \
    --host <数据库IP> \
    --port 1433 \
    --user <用户名> \
    --password <密码> \
    --database <数据库名，默认master> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

> **SQL Server 注意**：需要安装 ODBC Driver 17。无需填写 `--service_name` 参数。

#### DM8（达梦）巡检

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type dm \
    --host <数据库IP> \
    --port 5236 \
    --user <用户名> \
    --password <密码> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

> **DM8 注意**：无需填写 `--database` 参数，连接用户即 Schema。SSH 采集连接失败时自动降级为本地采集器。

#### 带 SSH 系统资源采集（MySQL / PostgreSQL / Oracle）

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
--type          数据库类型: mysql / pg / oracle / sqlserver / dm（必需）
--host          数据库主机 IP 或域名（必需）
--port          数据库端口（默认 MySQL 3306，PG 5432，Oracle 1521，SQL Server 1433，DM8 5236）
--user          数据库用户名（必需）
--password      数据库密码（必需）
--service_name  Oracle 服务名（Oracle 专用）
--sid           Oracle SID（Oracle 专用，与 service_name 二选一）
--database      数据库名（PG/SQL Server 专用，默认 postgres/master）
--label         数据库标签，用于报告命名（必需）
--inspector     巡检人员姓名（必需）
--ssh-host      SSH 主机 IP（可选，SQL Server/DM8 暂不支持）
--ssh-port      SSH 端口（默认 22）
--ssh-user      SSH 用户名（可选）
--ssh-password  SSH 密码（可选）
--ssh-key       SSH 私钥文件路径（可选，与密码二选一）
```

### 报告输出

- 报告自动保存在 `<scripts_dir>/reports/` 目录下
- 文件名格式：
  - MySQL：`MySQL巡检报告_<标签>_<时间戳>.docx`
  - PostgreSQL：`PostgreSQL巡检报告_<标签>_<时间戳>.docx`
  - Oracle：`Oracle巡检报告_<标签>_<时间戳>.docx`
  - SQL Server：`SQLServer_<标签>_<时间戳>.docx`
  - DM8：`DM8巡检报告_<标签>_<时间戳>.docx`
- 报告可用 Microsoft Word 或 WPS 打开

### 报告结构（各数据库通用）

生成的 Word 报告包含以下章节（各数据库结构略有差异）：

- **封面**：数据库基本信息（名称/版本/实例/主机名/启动时间/巡检人员/平台/报告时间）
- **第1章**：数据库基本信息（版本/实例名/服务器版本等）
- **第2章**：巡检执行摘要（执行时间、耗时、异常项统计）
- **第3章**：表空间使用情况（各数据库文件使用率）
- **第4章**：会话与事务（活动会话列表 + 阻塞事务）
- **第5章**：内存分析（SGA/PGA + 各缓冲池详情，DM8 含 DM8 特有缓冲池）
- **第6章**：重做日志与归档
- **第7章**：系统资源监控（CPU/内存/磁盘，SSH 采集或本地采集）
- **第8章**：对象与用户安全
- **第9章**：备份与归档（Oracle/DM8 含备份集检查）
- **第10章**：风险与建议（80+ 条增强规则，每条附修复 SQL）
- **第11章**：报告说明

> Oracle 报告额外包含：RAC 集群信息、ASM 磁盘组、Data Guard 状态、Undo 表空间、Profile 密码策略等。  
> DM8 报告额外包含：DM8 缓冲池详情、DM8 特有视图等。

### 常见错误处理

| 错误信息 | 原因 | 解决方案 |
|---------|------|--------|
| `pymysql: Access denied` | 用户名或密码错误 | 核对数据库账户信息 |
| `Can't connect to MySQL server` | 防火墙阻止或端口不对 | 确认端口、防火墙、安全组规则 |
| `psycopg2: connection refused` | PG 端口不对或未监听该地址 | 检查 postgresql.conf 的 listen_addresses |
| `ORA-01017` (Oracle) | 用户名/口令无效 | 确认密码（注意大小写）；如用 SYSDBA 请输入完整格式 `sys as sysdba` |
| `ORA-00904` (Oracle) | 无效列名/标识符 | 部分高级视图在低版本 Oracle 中不存在，标记⚠跳过不影响整体巡检 |
| Oracle 连接需要 Instant Client？ | 使用 oracledb 驱动则不需要 | 推荐使用 oracledb（纯 Python，无需 Instant Client）|
| `dmPython returned a result with an exception set` (DM8) | dmPython 为惰性连接，探测失败 | 检查端口（默认 5236）、用户名、密码、服务器访问权限 |
| `SSH 采集失败` | SSH 认证失败或目标机器无相关命令 | 检查用户名密码或私钥路径；确认目标机器有 `top/free/df/lscpu` 命令 |
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
- SSH 采集依赖目标机器的 `top`、`free`、`df`、`lscpu` 命令（使用 `AutoAddPolicy` 接受主机密钥）
- 报告生成依赖 `python-docx` 和 `docxtpl` 库，务必确保已安装
- Oracle 支持 11g R2 / 12c / 19c / 21c 及以上版本，部分高级视图在不同版本间有差异，工具已做兼容处理
- DM8 巡检依赖 `dmpython` 驱动（`pip install dmpython`）；V$ 视图列名与 Oracle 有较大差异，工具已针对 DM8 实测列名做过适配
- SQL Server 巡检依赖 `pyodbc`（`pip install pyodbc`）和 **ODBC Driver 17**（需单独安装）
- **本地文件写入**：巡检会在 `reports/` 生成 Word 报告、在当前目录写入 `history.json`（纯数值指标）、`autoDoc.log`（运行日志），均在本地机器上
- **AI 诊断限制**：仅支持本地 Ollama，API 地址必须是 localhost/127.0.0.1；不支持任何远程 AI 服务
