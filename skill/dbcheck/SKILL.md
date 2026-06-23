---
name: dbcheck
description: 执行 MySQL、PostgreSQL、Oracle、SQL Server、DM8、TiDB、IvorySQL、GBase 8s、YashanDB、KingbaseES 数据库健康巡检，内置 130+ 条增强风险分析规则，一键生成专业 Word 巡检报告。适用于 DBA 和运维人员快速掌握数据库运行状况、排查风险。项目地址：https://github.com/fiyo/DBCheck
license: MIT
metadata:
  {
    "openclaw":
      {
        "emoji": "🔍",
      },
  }
---

# DBCheck — 数据库自动化巡检工具（OpenClaw Skill）

> **安全声明（必读）**
>
> **本 Skill 的数据流向完全可控，如下所示：**
> ```
> 用户凭据 → [本地 Python 脚本] → 数据库服务器 → 巡检结果 → [本地 Word 报告]
> ```
>
> - ✅ **数据库凭据**：仅用于建立连接，**不会写入磁盘持久文件**，**不会发送到任何第三方**
> - ✅ **本地文件写入**：巡检结果以 Word 报告形式保存在本地 `reports/` 目录，所有文件均在本地，不含敏感凭据
> - ⚠️ **限制**：本 Skill 仅用于合法授权的数据库巡检，请勿用于未授权访问

## 核心能力

| 能力 | 说明 |
|------|------|
| 📊 130+ 条增强风险规则 | 覆盖 MySQL / PostgreSQL / Oracle / SQL Server / DM8 / TiDB / IvorySQL / GBase 8s / YashanDB / KingbaseES |
| 📈 历史趋势分析 | 多次巡检数据聚合，生成指标趋势折线图（存储在本地 SQLite / history.json） |
| 🔒 脱敏报告导出 | 导出 Word 时自动掩码 IP、端口、用户名、服务名，防止信息泄露 |

## 支持的数据库

| 数据库 | 驱动 | 默认端口 | 说明 |
|--------|------|---------|------|
| 🐬 MySQL | pymysql | 3306 | 主从复制、binlog、查询缓存 |
| 🐘 PostgreSQL | psycopg2 | 5432 | 归档模式、缓存命中率、dead tuples |
| 🔴 Oracle | oracledb / cx_Oracle | 1521 | 表空间、SGA/PGA、RAC、ASM、Data Guard |
| 🟠 SQL Server | pyodbc (ODBC Driver 17/18) | 1433 | 等待统计、锁与阻塞、备份检查 |
| 🟡 DM8 | dmpython | 5236 | 表空间、缓冲池、备份 |
| 🐬 TiDB | pymysql | 4000 | Placement Rules、TiCDC/PD 心跳 |
| 🐘 IvorySQL | psycopg2 | 5432 | PG 兼容巡检项、复制状态 |
| 🟢 GBase 8s | jaydebeapi (JDBC) | 5258 | 需要 GBase 8s JDBC 驱动 jar（放在 `drivers/gbase/` 目录） |
| 🟣 YashanDB | ? | 5432 | 崖山数据库巡检 |
| 🔵 KingbaseES | psycopg2 | 54321 | 金仓数据库巡检 |

> **GBase 8s 说明**：需要手动下载 GBase 8s JDBC 驱动 jar 文件，放到 `drivers/gbase/` 目录下。

## 触发条件

当用户请求以下任意一项时，加载本 Skill 并执行：

- 对数据库做健康检查 / 健康巡检 / 体检
- 检查 MySQL / PostgreSQL / Oracle / SQL Server / DM8 / TiDB / IvorySQL / GBase 8s / YashanDB / KingbaseES 的运行状态
- 生成数据库巡检报告 / 健康报告
- "帮我巡检一下 XX 数据库"
- "生成一份 XX 数据库巡检报告"

## 前置准备

### 必需信息

开始巡检前，**必须向用户收集以下信息**（缺少任何一项都要询问，不要自行猜测）：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `db_type` | 数据库类型 | `mysql` / `pg` / `oracle` / `sqlserver` / `dm` / `tidb` / `ivorysql` / `gbase` / `yashandb` / `kingbase` |
| `host` | 数据库主机 IP 或域名 | 需用户确认 |
| `port` | 数据库端口 | 见上表 |
| `user` | 数据库用户名 | 需用户确认 |
| `password` | 数据库密码 | 需用户确认 |
| `label` | 数据库标签（用于报告命名） | 需用户确认，如 "生产库-MySQL" |
| `inspector` | 巡检人员姓名 | 需用户确认 |

### Oracle 专用参数

| 参数 | 说明 |
|------|------|
| `service_name` 或 `sid` | Oracle 服务名或 SID（二选一，必填） |
| 特权连接 | 用户名输入 `sys as sysdba` 可建立 SYSDBA 特权连接 |

### 注意事项

- **DM8**：无需填写 `database` 参数，连接用户即 Schema
- **GBase 8s**：需要 `drivers/gbase/*.jar` 驱动文件，工具会自动查找
- **KingbaseES**：默认端口 54321（不是 PostgreSQL 的 5432）

## 执行巡检

使用 `execute_command` 工具执行 Python 脚本。

### 依赖检查

先检查依赖是否完整：

```bash
python -c "import pymysql, psycopg2, docxtpl, paramiko, psutil, openpyxl, docx" 2>&1
```

如有缺失，提示用户安装：

```bash
pip install pymysql psycopg2-binary paramiko openpyxl docxtpl python-docx pandas psutil flask oracledb dmpython pyodbc flask-socketio jaydebeapi
```

### 完整巡检示例

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

#### TiDB 巡检

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type tidb \
    --host <数据库IP> \
    --port 4000 \
    --user <用户名> \
    --password <密码> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

#### IvorySQL 巡检

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type ivorysql \
    --host <数据库IP> \
    --port 5432 \
    --user <用户名> \
    --password <密码> \
    --database <数据库名，默认postgres> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

#### GBase 8s 巡检

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type gbase \
    --host <数据库IP> \
    --port 5258 \
    --user <用户名> \
    --password <密码> \
    --database <数据库名，默认gbase01> \
    --gbase-server-name <GBase 服务名，默认gbase01> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

> **GBase 8s 注意**：需要 `drivers/gbase/*.jar` 驱动文件。工具会自动查找该目录下的 jar 文件。

#### YashanDB 巡检

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type yashandb \
    --host <数据库IP> \
    --port 5432 \
    --user <用户名> \
    --password <密码> \
    --database <数据库名> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

#### KingbaseES 巡检

```bash
cd <skill_scripts_dir>
python run_inspection.py \
    --type kingbase \
    --host <数据库IP> \
    --port 54321 \
    --user <用户名> \
    --password <密码> \
    --database <数据库名，默认kingbase> \
    --label "<数据库标签>" \
    --inspector "<巡检人员姓名>"
```

### 完整参数参考

```
--type           数据库类型: mysql / pg / oracle / sqlserver / dm / tidb / ivorysql / gbase / yashandb / kingbase（完整巡检必需）
--host           数据库主机 IP 或域名
--port           数据库端口（默认: MySQL 3306, TiDB 4000, PG 5432, Oracle 1521, SQL Server 1433, DM8 5236, GBase 5258, KingbaseES 54321）
--user           数据库用户名
--password       数据库密码
--service_name   Oracle 服务名（Oracle 专用）
--sid            Oracle SID（Oracle 专用，与 service_name 二选一）
--database       数据库名（PG/SQL Server/GBase/KingbaseES 专用，默认 postgres/master/gbase01/kingbase）
--gbase-server-name  GBase 8s 服务名（GBase 专用，默认 gbase01）
--label          数据库标签，用于报告命名
--inspector      巡检人员姓名
--ssh-host       SSH 主机 IP（可选，采集系统资源）
--ssh-port       SSH 端口（默认 22）
--ssh-user       SSH 用户名（可选）
--ssh-password   SSH 密码（可选）
--ssh-key        SSH 私钥文件路径（可选，与密码二选一）
```

### 报告输出

- 报告自动保存在 `<scripts_dir>/reports/` 目录下
- 文件名格式：
  - MySQL：`MySQL巡检报告_<标签>_<时间戳>.docx`
  - PostgreSQL：`PostgreSQL巡检报告_<标签>_<时间戳>.docx`
  - Oracle：`Oracle巡检报告_<标签>_<时间戳>.docx`
  - SQL Server：`SQLServer_<标签>_<时间戳>.docx`
  - DM8：`DM8巡检报告_<标签>_<时间戳>.docx`
  - TiDB：`TiDB巡检报告_<标签>_<时间戳>.docx`
  - GBase 8s：`GBase巡检报告_<标签>_<时间戳>.docx`
  - YashanDB：`YashanDB巡检报告_<标签>_<时间戳>.docx`
  - KingbaseES：`KingbaseES巡检报告_<标签>_<时间戳>.docx`
- 报告可用 Microsoft Word / WPS 打开（DOCX）

### 结果展示

巡检完成后：

1. 告知用户报告文件完整路径
2. 使用 `open_result_view` 工具打开报告文件供用户查看
3. 简要汇报关键发现（如发现高风险项，单独列出）
4. 提示用户报告中风险建议仅供参考，需结合实际业务评估

## 常见错误处理

| 错误信息 | 原因 | 解决方案 |
|---------|------|--------|
| `pymysql: Access denied` | 用户名或密码错误 | 核对数据库账户信息 |
| `Can't connect to MySQL server` | 防火墙阻止或端口不对 | 确认端口、防火墙、安全组规则 |
| `psycopg2: connection refused` | PG 端口不对或未监听该地址 | 检查 postgresql.conf 的 listen_addresses |
| `ORA-01017` (Oracle) | 用户名/口令无效 | 确认密码（注意大小写）；如用 SYSDBA 请输入完整格式 `sys as sysdba` |
| `ORA-00904` (Oracle) | 无效列名/标识符 | 部分高级视图在低版本 Oracle 中不存在，标记⚠跳过不影响整体巡检 |
| `dmPython returned a result with an exception set` (DM8) | dmPython 为惰性连接，探测失败 | 检查端口（默认 5236）、用户名、密码、服务器访问权限 |
| `Class com.gbasedbt.jdbc.Driver is not found` (GBase) | JDBC 驱动 jar 未找到 | 将 GBase 8s JDBC 驱动 jar 文件放到 `drivers/gbase/` 目录下 |
| `SSH 采集失败` | SSH 认证失败或目标机器无相关命令 | 检查用户名密码或私钥路径；确认目标机器有 `top/free/df/lscpu` 命令 |
| `Permission denied`（SSH） | SSH 认证失败 | 检查用户名密码或私钥路径 |

## 限制与注意事项

- 本 Skill 仅用于**合法授权的数据库巡检**，请勿用于未授权访问
- SSH 采集依赖目标机器的 `top`、`free`、`df`、`lscpu` 命令（使用 `AutoAddPolicy` 接受主机密钥）
- 报告生成依赖 `python-docx` 和 `docxtpl` 库，务必确保已安装
- Oracle 支持 11g R2 / 12c / 19c / 21c 及以上版本，部分高级视图在不同版本间有差异，工具已做兼容处理
- DM8 巡检依赖 `dmpython` 驱动（`pip install dmpython`）
- SQL Server 巡检依赖 `pyodbc`（`pip install pyodbc`）和 **ODBC Driver 17 或 18**（需单独安装）
- GBase 8s 巡检依赖 `jaydebeapi`（`pip install jaydebeapi`）和 GBase 8s JDBC 驱动 jar 文件
- KingbaseES 使用 PostgreSQL 协议（psycopg2），默认端口 54321
- **本地文件写入**：巡检会在 `reports/` 生成 Word 报告、在当前目录写入 `history.json`（纯数值指标）、`autoDoc.log`（运行日志），均在本地机器上
