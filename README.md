# 数据库巡检工具 - DBCheck

支持对 **MySQL** 和 **PostgreSQL** 两种主流关系型数据库进行自动化健康巡检，生成格式规范的 Microsoft Word 报告，帮助 DBA 和运维人员快速掌握数据库运行状况、发现潜在风险。

> 本项目由 [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git) 改进而来，在原 MySQL 支持的基础上新增了 PostgreSQL 支持。

## 四大核心能力

| 能力 | 说明 |
|------|------|
| 📊 **历史趋势分析** | 同一数据库多次巡检数据自动汇聚，生成指标趋势折线图，与上次对比发现变化 |
| 🤖 **AI 智能诊断** | 基于巡检指标调用大模型（DeepSeek / OpenAI / Ollama），生成个性化优化建议 |
| 🔍 **16+ 条增强规则** | 覆盖连接、缓存、日志、锁、慢查询、安全、复制等全维度，每条附修复 SQL |
| 🦞 **OpenClaw Skill** | AI 助手一句话完成巡检，零操作生成报告 |

---

## 四种使用方式

| 方式 | 说明 |
|------|------|
| 🖥️ 命令行 | `python main.py`，终端交互，适合熟悉命令行的用户 |
| 🌐 Web UI | `python web_ui.py`，浏览器图形界面，支持趋势图和 AI 诊断配置 |
| 🤖 **OpenClaw Skill** | 告诉 AI 助手"帮我巡检 XX 库"，零操作自动完成 |
| 📦 打包部署 | PyInstaller 打包成分发版，给团队成员使用 |

---

## 功能特性

### 数据库巡检

| 维度 | MySQL | PostgreSQL |
|------|:-----:|:----------:|
| 连接与会话状态 | ✅ | ✅ |
| 内存与缓存配置 | ✅ | ✅ |
| 日志与存储 | ✅ | ✅ |
| 性能与锁 | ✅ | ✅ |
| 索引使用情况 | ✅ | ✅ |
| 数据库对象 | ✅ | ✅ |
| 安全与用户 | ✅ | ✅ |
| 实例信息 | ✅ | ✅ |
| 复制与主从状态 | ✅ | ✅ |
| 缓存命中率 | ✅ | ✅ |
| 后台写入器状态 | — | ✅ |
| 已安装扩展 | — | ✅ |
| 关键参数配置 | ✅ | ✅ |

### 系统资源监控

- **CPU**：使用率、核心数、频率
- **内存**：总量、使用量、可用量、使用率
- **磁盘**：各挂载点容量及使用率
- **采集方式**：本地直采或 SSH 远程采集（支持密码/密钥认证）

### 智能风险分析（16+ 条规则）

自动检测数据库潜在风险，**每条风险附可执行的修复 SQL**，可直接复制到数据库执行：

| 维度 | MySQL | PostgreSQL |
|------|:-----:|:----------:|
| 连接数使用率（90% 高危 / 80% 中危） | ✅ | ✅ |
| 内存使用率偏高 | ✅ | ✅ |
| 磁盘使用率（>85% 警告 / >95% 高危） | ✅ | ✅ |
| 长时间运行 SQL（>60s） | ✅ | ✅ |
| 慢查询日志未开启 | ✅ | — |
| 锁等待比例偏高 | ✅ | ✅ |
| 查询缓存残留配置 | ✅ | — |
| InnoDB 缓冲池偏小（<数据总量 60%） | ✅ | — |
| binlog 未开启 / 过期时间=0 | ✅ | — |
| 异常中止连接数过多 | ✅ | — |
| 用户空密码 / root@% 暴露 | ✅ | — |
| 主从复制延迟 > 30s | ✅ | — |
| 主从复制状态异常 | ✅ | — |
| 字符集非 UTF8 | ✅ | — |
| 缓存命中率偏低（<80%） | — | ✅ |
| shared_buffers 偏小（<8GB 且内存>16GB） | — | ✅ |
| 超级用户过多 | — | ✅ |
| 归档模式未开启 | — | ✅ |
| dead tuples 大量累积 | — | ✅ |

### 历史趋势分析 📊

> 多次巡检同一个数据库，自动汇聚指标数据，生成趋势图，发现悄然发生的变化。

- 每次巡检后，关键指标（内存使用率、连接数、QPS、CPU 等）自动写入本地 `history.json`
- 同一数据库（IP + 端口）多次巡检数据聚合保留，最多 30 条历史记录
- Web UI 提供**趋势分析页面**，绘制指标折线图，带警戒线标注
- 与上次巡检逐项对比：变化量带颜色箭头（↑ 变差 / ↓ 好转）

![趋势分析](snapshot/trend1.png)
*趋势分析页：内存使用率折线图，黄色警戒线标注 80% 阈值*

![指标对比](snapshot/trend2.png)
*与上次巡检对比：逐项指标变化量，高亮异常项*

### AI 智能诊断 🤖

> 基于巡检数据，调用大模型生成个性化优化建议，从"发现问题"升级到"解决问题"。

AI 诊断与智能分析的关系：

| | 智能分析 | AI 诊断 |
|---|---|---|
| 原理 | 固定规则，离线判断 | 大模型推理，个性化输出 |
| 速度 | 毫秒级 | 取决于模型响应时间 |
| 结果 | 确定性结论 + 修复 SQL | 自然语言优化建议 |
| 调用 | 每次巡检自动执行 | 按需调用（可关闭） |

**支持的 AI 后端（Web UI 可视化配置，无需改代码）：**

| 后端 | 说明 | 适用场景 |
|------|------|---------|
| `disabled` | 不调用 AI（默认） | 离线环境 |
| `openai` | OpenAI / DeepSeek / Azure / 任意兼容 API | 有 API Key 的用户 |
| `ollama` | 本地模型（完全离线） | 注重数据隐私、无外网的环境 |
| `custom` | 用户自定义 API URL | 企业内部模型接入 |

配置路径：Web UI → 侧边栏「AI 诊断设置」→ 填写对应参数 → 保存后自动生效。

---

## 环境要求

- **操作系统**：Linux / macOS / Windows
- **Python**：3.6 及以上
- **依赖**：pymysql、psycopg2-binary、python-docx、docxtpl、paramiko、psutil、openpyxl、pandas
- **MySQL 权限**：查询 information_schema、performance_schema、mysql 库的只读权限
- **PostgreSQL 权限**：查询 pg_stat_* 系列系统视图及 pg_roles 的只读权限
- **SSH（可选）**：用于远程采集系统资源

---

## 快速开始

### 安装依赖

```bash
pip3 install pyinstaller pymysql psycopg2-binary paramiko openpyxl docxtpl python-docx pandas psutil==5.9.0 flask
```

### 启动巡检

```bash
python3 main.py
```

数据库类型菜单提供五个选项：
| 选项 | 说明 |
|:---:|------|
| 1 | MySQL - MySQL 数据库健康检查与报告生成 |
| 2 | PostgreSQL - PostgreSQL 数据库健康检查与报告生成 |
| 3 | 生成 Excel 批量巡检模板（MySQL） |
| 4 | 生成 Excel 批量巡检模板（PostgreSQL） |
| W | 启动 Web UI     浏览器可视化操作界面
| 5 | 退出 |

1. 输入 **1** 或 **2**，进入 `MySQL` 或 `PostgreSQL` 巡检功能菜单
2. 输入 **3** 或 **4**，生成 `mysql_batch_template.xlsx` 或 `pg_batch_template.xlsx` 配置模板
3. 输入 **W** 或 **w**，启动 Web UI 服务
4. 输入 **5** 退出工具

> 💡 命令行启动时显示彩色 ASCII Art Banner，按 `W` 可直接启动 Web UI。

功能菜单提供四个选项：
| 选项 | 说明 |
|:---:|------|
| 1 | 单机巡检 |
| 2 | 批量巡检（从 Excel 导入） |
| 3 | 创建 Excel 配置模板 |
| 4 | 退出 |

#### 单机巡检
1. 选择 **1**  进入单机巡检
2. 根据提示填写数据库连接信息及 SSH 信息（可选）
3. 工具自动进行巡检并生成 Word 巡检报告

#### 批量巡检

1. 选择菜单 **2**，程序自动读取配置并依次巡检所有实例
2. 工具自动根据 Excel 模型中的内容批量巡检

> 注意：Excel 模板中请勿明文保存密码，填写完成后注意妥善保管配置文件。

### Web UI（可视化界面）

启动 Web 服务后，在浏览器访问 **http://localhost:5000** 即可通过图形界面完成所有巡检操作，无需记忆命令行参数。

```bash
python3 web_ui.py
```

**Web UI 完整功能：**

| 步骤 | 功能 |
|:---:|------|
| 1 | 选择数据库类型（MySQL / PostgreSQL） |
| 2 | 填写连接信息，支持在线测试连接 |
| 3 | 配置 SSH 采集系统资源（可选） |
| 4 | 填写巡检人员姓名 |
| 5 | 确认信息后一键执行，实时查看日志进度 |
| 6 | 巡检完成，在线预览智能分析 + AI 诊断结果 |
| 7 | 📊 历史趋势分析：查看同一数据库多次巡检的指标趋势 |
| 8 | 🤖 AI 诊断设置：配置 AI 后端参数 |
| 9 | 下载 Word 报告，随时查阅历史报告 |

**界面截图：**

![步骤一：选择数据库类型](snapshot/webui1.png)
*图 1：选择数据库类型（MySQL 🐬 / PostgreSQL 🐘）*

![步骤二：填写连接信息](snapshot/webui2.png)
*图 2：填写数据库连接信息*

![步骤三：在线连接测试数据库连接](snapshot/webui3.png)
*图 3：在线连接测试数据库连接*

![步骤四：SSH 配置](snapshot/webui4.png)
*图 4：SSH 连接配置（可选）*

![步骤五：测试 SSH 连接](snapshot/webui5.png)
*图 5：测试 SSH 连接*

![步骤六：巡检人员](snapshot/webui6.png)
*图 6：巡检人员配置*

![步骤七：确认巡检信息](snapshot/webui7.png)
*图 7：确认巡检信息*

![步骤八：执行巡检](snapshot/webui8.png)
*图 8：一键巡检，实时预览巡检进度*

![步骤九：报告下载](snapshot/webui9.png)
*图 9：巡检完成后直接下载 Word 报告*

![历史报告](snapshot/webui10.png)
*图 10：历史报告列表页，支持按名称、大小、时间浏览*

![AI 诊断建议](snapshot/webui11.png)
*图 11：诊断建议*

![历史趋势分析](snapshot/webui12.png)
*图 12：历史趋势分析*

### OpenClaw Skill（AI 助手直连）

本项目已发布为 [ClawHub](https://clawhub.ai/skills/dbcheck) 上的 OpenClaw Skill，接入 AI 助手后可通过自然语言直接触发巡检，无需手动操作命令行或 Web UI。

![Clawhub dbcheck skill](snapshot/skill0.png)
*dbcheck 已发布到 Clawhub*

#### 安装方式

在 OpenClaw 客户端执行：

```bash
clawhub install dbcheck
```

![QClaw](snapshot/skill1.png)
*也可以在 QClaw 等支持 OpenClaw Skills 的软件中使用 dbcheck。*

#### 使用方式

安装后，直接告诉 AI 助手你想做的事，例如：

> "帮我巡检一下生产 MySQL 库，IP 是 192.168.1.10，用户名 root，密码是 xxx"

AI 助手会自动加载 Skill，按步骤询问缺少的信息（端口、标签、巡检人员姓名等），然后调用巡检脚本生成 Word 报告，最后把报告打开给你。

#### 支持的指令

| 指令示例 | 说明 |
|---------|------|
| 帮我巡检一下 MySQL 库 | 单机 MySQL 巡检 |
| 帮我巡检一下 PostgreSQL 库 | 单机 PG 巡检 |
| 巡检 192.168.1.10 的 MySQL | 指定 IP 的快速巡检 |
| 生成一份数据库巡检报告 | 触发完整巡检流程 |

#### 完整命令参数

```bash
python run_inspection.py \
    --type mysql \          # 数据库类型: mysql 或 pg
    --host <IP> \           # 主机地址
    --port 3306 \           # 端口（MySQL 默认 3306，PG 默认 5432）
    --user <用户名> \        # 数据库用户名
    --password <密码> \     # 数据库密码
    --label "<标签>" \       # 报告命名用
    --inspector "<姓名>" \  # 巡检人员
    --ssh-host <SSH_IP> \   # 可选：SSH 主机
    --ssh-user <SSH用户> \  # 可选：SSH 用户名
    --ssh-password <密码>  # 可选：SSH 密码
```

> ⚠️ **安全提示**：Skill 凭据仅用于建立本地连接，不会写入磁盘或发送到任何第三方。ClawHub 安全扫描会将 base64 加密工具类误标记为"CryptoRequires wallet"，这是本地密码保存的正常用途，不是加密货币功能，请放心使用。

#### Skill 文件说明

```
~/.workbuddy/skills/dbcheck/
├── SKILL.md           # Skill 说明（供 AI 助手理解何时及如何调用）
├── security.md        # 安全说明（解释扫描器误判原因）
└── scripts/
    ├── run_inspection.py   # 非交互式入口
    ├── main_mysql.py       # MySQL 巡检逻辑
    ├── main_pg.py         # PostgreSQL 巡检逻辑
    └── main.py             # 菜单入口
```

> 本 Skill 源代码完全开源，可在 `~/.workbuddy/skills/dbcheck/scripts/` 目录下查看所有脚本，确认无恶意行为后再安装使用。

---

## 打包部署

```bash
pyinstaller --onefile --name dbcheck \
    --hidden-import pymysql \
    --hidden-import psycopg2 \
    --hidden-import docx \
    --hidden-import docxtpl \
    --hidden-import paramiko \
    --hidden-import psutil \
    --hidden-import openpyxl \
    --hidden-import pandas \
    main.py
```

打包后在 `dist` 目录下执行：

```bash
cd dist
./dbcheck
```

---

## 报告结构

生成的 Word 报告包含以下章节：

- **封面**：数据库基本信息、巡检人员、报告时间
- **健康状态概览**：总体评级及发现问题数量
- **系统资源检查**：CPU、内存、磁盘详细指标
- **数据库配置检查**：连接、内存、日志相关关键参数
- **性能分析**：QPS、锁信息、异常连接、索引使用情况
- **数据库信息**：各库大小、当前活跃进程、已安装扩展
- **安全信息**：数据库用户列表及权限概要
- **风险与建议**：智能分析出的潜在问题及修复 SQL（16+ 条）

---

## 常见问题

1. **部分内容为空或缺失**
   模板渲染出现兼容性问题时，程序会自动切换至备用渲染模式，仍可生成包含所有关键数据的完整报告，不影响使用。

2. **连接失败**
   检查数据库是否允许远程访问、用户权限是否充足、防火墙是否放行对应端口。

3. **SSH 采集失败**
   确认 SSH 服务正常运行、认证信息正确。部分精简版 Linux 可能缺少 `lscpu` 等命令，导致部分 CPU 信息显示为"未获取"，属正常现象。

4. **AI 诊断不生效**
   确认已在 Web UI「AI 诊断设置」中保存了有效的后端配置（API Key、URL、模型名称）。离线环境推荐使用 Ollama 后端。

5. **风险建议仅供参考**
   内置阈值基于通用最佳实践，实际场景中请结合业务负载综合评估。

---

## 鸣谢

感谢 [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git) 作者的贡献！

目前部分功能仍在持续完善中，欢迎共同参与功能开发以及反馈问题与建议。
