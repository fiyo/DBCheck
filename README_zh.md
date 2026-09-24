# 🦝 RaccoonX

![RaccoonX Logo](snapshot/dbcheck_logo_info.png)

### 开源智能数据库巡检与运维平台

> **RaccoonX** 是一个开源、跨平台的数据库巡检与运维平台，面向 DBA、数据库工程师、DevOps 团队与基础设施团队。
>
> 项目**原名 DBCheck**。

[![Version](https://img.shields.io/badge/Version-v26.9.17.0-blue.svg)]()
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![AI](https://img.shields.io/badge/AI-Ollama%20%7C%20OpenAI-orange.svg)]()
[![RAG](https://img.shields.io/badge/RAG-知识库-red.svg)]()
[![WebUI](https://img.shields.io/badge/WebUI-Flask-success.svg)]()
[![Docker Pulls](https://img.shields.io/docker/pulls/jackge12345/dbcheck?style=flat-square\&label=Docker%20Pulls\&cacheSeconds=300)](https://hub.docker.com/r/jackge12345/dbcheck)
[![GitHub Stars](https://img.shields.io/github/stars/fiyo/DBCheck?style=flat-square\&label=Stars)](https://github.com/fiyo/DBCheck/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/fiyo/DBCheck?style=flat-square\&label=Forks)](https://github.com/fiyo/DBCheck/network/members)

> 🐳 **25,000+ Docker 镜像拉取**
> 🗄️ **21+ 数据库类型**
> 🔍 **330+ 巡检规则**
> 🤖 **AI 辅助诊断**
> 🔌 **可扩展插件架构**
> 📜 **Apache License 2.0**

如果 RaccoonX 对你有帮助，欢迎给仓库点一个 ⭐。

一个 Star 能让更多数据库工程师发现这个项目，也是支持独立开源项目最简单的方式。

---

## 🌐 项目链接

* **官网：** https://raccoonx.cn/
* **GitHub：** https://github.com/fiyo/DBCheck
* **Docker Hub：** https://hub.docker.com/r/jackge12345/dbcheck
* **English：** [README.md](./README.md)
* **中文：** [README_zh.md](./README_zh.md)
* **Issues：** https://github.com/fiyo/DBCheck/issues
* **Discussions：** https://github.com/fiyo/DBCheck/discussions

---

# 🦝 为什么选择 RaccoonX？

数据库环境正变得越来越复杂。

一套典型的生产环境可能同时包含：

* MySQL
* PostgreSQL
* Oracle
* SQL Server
* 达梦 DM8
* TiDB
* OceanBase
* Redis
* MongoDB
* ClickHouse
* 以及更多其它数据库系统。

传统的数据库巡检往往依赖手工执行 SQL 脚本、检查操作系统资源、截图留存、分析慢 SQL，再人工撰写巡检报告。

RaccoonX 希望把这些工作整合到一个开源平台里。

### RaccoonX 帮助你：

```text
连接
   ↓
巡检
   ↓
采集
   ↓
分析
   ↓
识别风险
   ↓
生成报告
   ↓
跟踪变化
   ↓
AI 辅助诊断
```

它既可以作为独立的数据库巡检工具使用，也可以融入更完整的数据库运维工作流。

---

# 📊 项目里程碑

RaccoonX 前身是 **DBCheck**，在持续开发与社区反馈中不断演进。

| 里程碑             |         现状 |
| ------------------ | -------------: |
| Docker 镜像拉取       |    **25,000+** |
| GitHub Stars       |       **166+** |
| GitHub Forks       |        **57+** |
| 数据库类型             |        **21+** |
| 巡检规则               |       **330+** |
| 界面语言               |          **9** |
| 开源协议               | **Apache 2.0** |

Docker 拉取次数代表镜像拉取量，不应解读为独立用户数或安装数。

### ⭐ 帮我们到达下一个里程碑

如果你用过 RaccoonX、测试过它、从它学到过东西，或者单纯觉得这个项目有意思：

**给它一个 Star。**

```text
25,000+ Docker Pulls
        ↓
     持续构建
        ↓
   ⭐ Star RaccoonX
```

---

# ✨ 功能一览

| 功能                         | 说明                                                                 |
| ---------------------------- | -------------------------------------------------------------------- |
| 🗄️ 数据源管理                 | 统一管理数据库实例，支持分组、批量巡检、CSV 导入导出                     |
| 📋 数据库巡检                 | 21+ 数据库类型、330+ 巡检规则，自动生成 Word 报告                     |
| 🔌 插件系统                   | 独立的插件生命周期、插件数据、模板、基线与规则                        |
| 🔍 慢查询分析                 | 执行计划、I/O 模式、锁等待与 AI 辅助分析                              |
| 🔒 锁诊断                     | 阻塞链、死锁、长事务与处置建议                                        |
| 📊 索引健康                   | 缺失、冗余与长期未使用索引分析                                        |
| ⚙️ 配置基线                   | 将数据库参数与可配置的推荐值对比                                      |
| 📈 历史趋势                   | 多轮巡检历史、趋势与前后对比                                          |
| 🤖 AI 诊断                    | 本地 Ollama 或兼容云端 API 的 AI 辅助分析                             |
| 💬 AI 对话巡检                | 以自然语言与巡检系统交互                                              |
| 📡 实时监控                   | 连接、延迟、吞吐与可用性监控                                          |
| 🖥️ 服务器巡检                | CPU、内存、磁盘、网络、服务与进程                                     |
| 🔗 分享报告                   | 生成免登录即可查看的报告链接                                          |
| ⏰ 定时任务                   | 基于 Cron 的巡检调度，支持邮件/Webhook 通知                           |
| 📚 RAG 知识库                 | 上传运维文档，用于 AI 辅助诊断                                        |
| 📊 Oracle AWR 分析            | 解析 Oracle AWR HTML 报告并生成结构化报告                             |
| 💿 DM8 离线存储检查           | 无需启动数据库即可分析 DM8 数据文件                                   |
| 📝 SQL 编辑器                 | 交互式 SQL 编辑器，语法高亮与执行历史                                 |
| 🖥️ 远程终端                  | 基于 SSH 的终端，多标签页与全屏模式                                   |
| 💾 容灾备份                   | 数据库/文件的定时备份，含保留清理与健康跟踪                           |
| 🌍 多语言界面                 | 简体中文、English、繁體中文、日本語、한국어、Español、Français、Deutsch、Русский |

---

# 🗄️ 支持的数据库

RaccoonX 目前支持 **21+ 数据库与数据系统**。

| 数据库        | 驱动                    | 默认端口 | 说明                       |
| ------------- | ----------------------- | -----------: | -------------------------- |
| MySQL         | pymysql                 |         3306 | 5.6 / 5.7 / 8.0+           |
| MariaDB       | pymysql                 |         3306 | 10.3+                      |
| PostgreSQL    | psycopg2                |         5432 | 10+                        |
| Oracle        | oracledb                |         1521 | 11g R2 / 12c / 19c / 21c+  |
| Oracle (JDBC) | JPype1 + ojdbc          |         1521 | Oracle JDBC 连接           |
| SQL Server    | pyodbc + ODBC Driver 17 |         1433 | 2012+                      |
| 达梦 DM8      | dmpython                |         5236 | 国产数据库                 |
| TiDB          | pymysql                 |         4000 | MySQL 协议                 |
| IvorySQL      | psycopg2                |         5333 | PostgreSQL 兼容            |
| 崖山 YashanDB | yashandb                |         1688 | Oracle 兼容                |
| 人大金仓 KingbaseES | psycopg2          |        54321 | PostgreSQL 兼容            |
| 南大通用 GBase 8s | JDBC                |         9088 | JDK + JDBC                 |
| 优炫 UXDB     | JDBC                    |        33060 | PostgreSQL 兼容            |
| 瀚高 HGDB     | JDBC                    |         5866 | PostgreSQL 兼容            |
| MongoDB       | pymongo                 |        27017 | 4.0+                       |
| DB2 LUW       | JDBC                    |        50000 | 11.5+ / 12.x               |
| OceanBase     | pymysql                 |         2881 | MySQL 租户                 |
| TDSQL-C MySQL | pymysql                 |         3306 | MySQL 兼容                 |
| Redis         | redis-py                |         6379 | 3.0+                       |
| Redis 集群    | redis-py                |         6379 | 集群拓扑与槽位             |
| ClickHouse    | JDBC                    |         8123 | 21.8+                      |

> **Oracle JDBC**
>
> Oracle JDBC 是基于 JPype 与 Oracle JDBC 驱动的独立插件，适合无法安装 Oracle Instant Client 的环境。

---

# 🚀 快速开始

## 🐳 Docker — 推荐

Docker 是启动 RaccoonX 最简单的方式。

### Docker Hub

```bash
docker pull jackge12345/dbcheck:latest

docker run -d \
  -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_reports:/app/reports \
  --name dbcheck \
  jackge12345/dbcheck:latest
```

### GitHub Container Registry

```bash
docker pull ghcr.io/fiyo/dbcheck:latest

docker run -d \
  -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_reports:/app/reports \
  --name dbcheck \
  ghcr.io/fiyo/dbcheck:latest
```

打开：

```text
http://localhost:5003
```

默认账号为 `admin`，密码为 `admin123`（首次登录后请及时修改密码）。

### docker-compose

```bash
curl -o deploy/docker-compose.yml \
  https://raw.githubusercontent.com/fiyo/DBCheck/main/deploy/docker-compose.yml

docker compose -f deploy/docker-compose.yml up -d
```

> **GBase 8s**
>
> Docker 镜像已内置所需的 JDK 与 JDBC 驱动，通过镜像运行 GBase 8s 无需额外安装驱动。

---

# 💻 源码安装

## 环境要求

* Python 3.10+
* Git
* 各数据库对应的 Python 驱动
* 按需安装额外的 JDBC / ODBC 依赖

克隆仓库：

```bash
git clone https://github.com/fiyo/DBCheck.git
cd DBCheck
```

安装依赖：

```bash
pip install -r deploy/requirements.txt
```

启动 Web UI：

```bash
python web_ui.py
```

打开：

```text
http://localhost:5003
```

---

## CLI 模式

中文界面：

```bash
python -m entrypoints.cli
```

英文界面：

```bash
python -m entrypoints.cli --lang en
```

Web 界面：

```bash
python web_ui.py
```

---

# 🔍 数据库巡检

RaccoonX 提供配置驱动的数据库巡检。

巡检引擎采集数据库元数据、配置、性能信息、资源使用与安全相关信息，并依据巡检规则进行评估。

典型流程：

```text
数据库
   │
   ├── 连接
   │
   ├── 元数据
   │
   ├── 配置
   │
   ├── 性能
   │
   ├── 会话
   │
   ├── 锁
   │
   ├── 存储
   │
   ├── 安全
   │
   └── SQL
          ↓
     巡检引擎
          ↓
     风险分析
          ↓
      优化建议
          ↓
     Word 报告
```

---

# 📋 巡检覆盖

RaccoonX 内置各数据库专属的巡检模板。

典型巡检维度包括：

| 维度         | 覆盖内容                                        |
| ----------------- | ----------------------------------------------- |
| 基本信息     | 版本、实例、数据库信息                          |
| 会话         | 活跃会话与连接使用情况                          |
| 内存         | 内存配置与利用率                                |
| 存储         | 表空间、文件与容量                              |
| 配置         | 重要数据库参数                                  |
| 安全         | 用户、权限与安全配置                            |
| SQL          | Top SQL 与慢查询                                |
| 锁           | 阻塞会话与锁等待                                |
| 复制         | 支持场景下的复制 / Data Guard 状态              |
| 备份         | 备份就绪度与相关配置                            |
| 统计信息     | 对象与优化器统计                                |
| 性能         | 数据库性能指标                                  |
| 可用性       | 数据库与服务状态                                |

具体覆盖范围因数据库类型而异。

---

# 📊 Word 巡检报告

RaccoonX 可以自动生成结构化的 Word 巡检报告。

典型的 Oracle 报告包含：

| 章节         | 内容                                                                     |
| ------------ | ------------------------------------------------------------------------ |
| 封面         | 数据库名、版本、主机、巡检人与时间戳                                     |
| 第 1 章      | OS 主机信息                                                              |
| 第 2 章      | 数据库基本信息                                                           |
| 第 3 章      | 表空间                                                                   |
| 第 4 章      | SGA / PGA                                                                |
| 第 5 章      | 关键参数                                                                 |
| 第 6–19 章   | Undo、Redo、归档、DG、RAC、ASM、会话、性能、安全等                       |
| 第 20 章     | 风险与建议                                                               |
| 第 21 章     | AI 诊断建议                                                              |
| 第 22 章     | 报告说明                                                                 |

报告章节可通过 Web UI 自由配置。

---

# ⚠️ 智能风险分析

RaccoonX 依据可配置的巡检规则评估采集到的信息。

一条风险项可以包含：

* 风险描述
* 当前值
* 推荐值
* 风险等级
* 相关 SQL
* 建议操作
* 可执行的修复 SQL（如适用）

示例流程：

```text
发现风险
     ↓
理解风险
     ↓
查看建议
     ↓
审查 SQL
     ↓
确认
     ↓
执行修复
```

以下危险操作：

```text
DELETE
DROP
TRUNCATE
```

需要二次确认。

所有执行操作均有日志记录。

> 风险建议仅供参考。执行前请结合实际生产架构、负载与业务需求自行评估变更。

---

# 🐢 慢查询深度分析

RaccoonX 可以从多个维度分析慢 SQL 或高代价 SQL。

分析内容可包括：

* SQL 文本
* 执行计划
* 执行时长
* CPU 占用
* I/O 行为
* 锁等待
* 索引使用
* 执行频率
* 资源消耗
* 历史信息
* AI 辅助诊断

目标是把 SQL 症状与数据库及系统层面的证据关联起来，而不是只盯着 SQL 文本。

---

# 🔒 锁与阻塞诊断

锁分析提供如下信息：

* 阻塞会话
* 被阻塞会话
* 阻塞链
* 锁等待
* 长事务
* 死锁统计
* 会话关系
* 建议处置动作

对于支持的数据库，可以直接从风险分析界面生成处置 SQL。

---

# 📊 索引健康分析

RaccoonX 分析索引相关状况，包括：

* 可能缺失的索引
* 冗余索引
* 长期未使用的索引
* 索引统计信息
* 索引相关的 SQL 性能问题

应用任何变更前，请结合实际负载特征审阅建议。

---

# ⚙️ 配置基线

RaccoonX 提供可配置的基线管理。

通过 Web UI 可以定义：

* 推荐值
* 阈值
* 合规规则
* 数据库专属配置
* 巡检规则

示例覆盖：

```text
MySQL
PostgreSQL
Oracle
SQL Server
DM8
TiDB
YashanDB
KingbaseES
GBase 8s
MongoDB
ClickHouse
```

基线引擎将数据库实际参数与配置的推荐值进行对比。

---

# 📈 历史趋势分析

巡检结果可以在本地留存，并进行多轮对比分析。

历史分析可以呈现：

* 资源趋势
* 配置变化
* 风险变化
* 性能变化
* 前后对比
* 风险随时间的演化

从而实现从：

```text
一次性巡检
```

到：

```text
数据库健康持续跟踪
```

的转变。

---

# 📡 实时监控

Web UI 为支持的数据库类型提供实时监控。

典型指标包括：

* 响应延迟
* QPS / TPS
* 活跃连接数
* 总连接数
* 运行中会话数
* 可用性
* 慢查询
* 活跃连接热力图

监控大屏会根据各数据库类型的能力自动适配。

对于不支持深度指标采集的数据库类型，RaccoonX 可以提供 TCP 层连通性信息，包括：

* 可达 / 不可达时间线
* 可用率
* 认证失败
* 端口不可达
* 断路器状态
* 不支持深采状态

---

# 🔥 慢查询与连接热力图

监控界面提供热力图可视化：

* 慢查询
* 活跃连接
* 时间分布
* 连接活动

自动刷新间隔可在以下范围配置：

```text
5s – 60s
```

同时支持 CSV 导出。

---

# 🤖 AI 智能诊断

RaccoonX 支持 AI 辅助数据库诊断。

AI 层可以分析巡检结果并给出：

* 风险解释
* 可能的根因
* 优化建议
* SQL 分析
* 配置建议
* 运维建议

## 基于 Ollama 的本地 AI

对于要求数据库信息不出内网的环境：

```bash
ollama pull qwen3:30b
ollama pull nomic-embed-text
```

然后启动 RaccoonX：

```bash
python web_ui.py
```

在「AI 设置」页面配置 AI 后端。

### 支持的 AI 后端

| 后端       | 说明                         |
| ---------- | ---------------------------- |
| `ollama`   | 本地 AI 部署                 |
| `openai`   | OpenAI 兼容云端 API          |
| `disabled` | 关闭 AI                      |

使用 Ollama 时，巡检数据可以完全保留在本地环境。

> AI 生成的建议在生产环境应用前，应由合格的数据库工程师审阅。

---

# 💬 AI 对话巡检

RaccoonX 在 Web UI 中内置了 AI 交互面板。

无需逐个手动翻阅巡检选项，用户可以用自然语言与巡检流程交互。

例如：

```text
看看连接使用率最高的数据库有哪些。

分析一下这个实例的慢 SQL。

风险最高的配置项有哪些？

解释一下这个数据库为什么 I/O 这么高。

对比本次巡检与上次巡检的差异。
```

可用能力取决于所配置的数据库与 AI 后端。

---

# 📚 RAG 知识库

RaccoonX 内置本地知识库能力。

支持的文档类型包括：

* PDF
* Word
* Markdown
* TXT

文档可被向量化，并在 AI 诊断时检索引用。

典型流程：

```text
上传文档
        ↓
向量化
        ↓
知识检索
        ↓
数据库巡检
        ↓
AI 诊断
        ↓
结合上下文的建议
```

企业可以把数据库巡检数据与自己的运维文档、规范结合起来。

---

# 🔌 插件架构

RaccoonX 采用可扩展的插件架构。

插件可以独立管理自己的：

* 生命周期
* 元数据
* 巡检模板
* 基线
* 规则
* 插件数据

典型插件结构：

```text
plugins/available/your_plugin/
├── plugin.json
├── main_plugin.py
├── template_data.json
├── baseline_data.json
└── rules/
```

插件可以通过 Web UI 安装、启用、禁用与卸载。

---

## 内置插件

| 插件          | 数据库                        | 说明                                                 |
| ------------- | ----------------------------- | ---------------------------------------------------- |
| MongoDB       | MongoDB 4.0+                  | 连接状态、数据库统计、慢查询                         |
| Oracle JDBC   | Oracle 11g / 12c / 19c / 21c+ | 基于 JDBC 的 Oracle 巡检                             |
| DB2 JDBC      | DB2 LUW 11.5+ / 12.x          | JDBC 巡检与系统目录分析                              |
| Redis         | Redis 3.0+                    | 内存、客户端、持久化、复制、安全                     |
| Redis 集群    | Redis Cluster                 | 拓扑、槽位、节点与故障转移                           |
| UXDB JDBC     | UXDB 2.x                      | PostgreSQL 兼容巡检                                  |
| HGDB JDBC     | HGDB V9                       | PostgreSQL 兼容巡检                                  |
| TDSQL-C MySQL | TDSQL-C                       | MySQL 兼容巡检                                       |

插件开发文档：

```text
docs/plugin/
```

---

# 🖥️ 服务器巡检

服务器巡检独立于数据库巡检。

可采集：

* CPU
* 内存
* 磁盘
* 网络
* 进程
* 服务
* 系统资源

可以单独生成服务器巡检报告。

这样在排障时可以将数据库与操作系统信息放在一起综合考量。

---

# 🖥️ 远程终端

RaccoonX 内置基于 SSH 的远程终端。

支持的功能包括：

* 密码认证
* SSH 密钥认证
* 多终端标签页
* 全屏模式

当数据库诊断需要检查底层主机时非常实用。

---

# 🔗 分享报告

巡检报告可以通过生成的链接分享。

支持的能力包括：

* 一键分享
* 免登录查看
* 权限隔离
* 访问计数
* 随时删除

示例：

```text
/share/<share_id>
```

---

# ⏰ 定时巡检

RaccoonX 支持基于 Cron 表达式的定时巡检任务。

常见调度包括：

```text
每天
工作日
每周
每月
自定义 Cron
```

巡检完成后，可通过以下方式发送通知：

* 邮件
* Webhook
* 企业微信
* 钉钉
* 自定义 JSON Webhook

Word 巡检报告可作为邮件附件发送。

---

# 💾 容灾备份

RaccoonX 内置容灾备份模块。

支持的备份对象包括：

* MySQL
* MariaDB
* PostgreSQL
* 文件

功能包括：

* 定时备份
* Cron 调度
* 保留期清理
* 备份历史
* 备份健康评分
* 一键恢复点
* Webhook 通知
* 邮件通知

数据库密码使用 Fernet 加密存储，并在 API 响应中脱敏。

---

# 📊 Oracle AWR 分析

Oracle AWR HTML 报告可以上传到 RaccoonX。

系统可以解析关键性能信息并生成结构化的 Word 分析报告。

也可以开启 AI 辅助分析。

典型流程：

```text
Oracle AWR HTML
       ↓
上传
       ↓
解析
       ↓
分析
       ↓
生成报告
       ↓
AI 辅助诊断
```

---

# 💿 DM8 离线存储检查

RaccoonX 支持在数据库实例未启动的情况下进行 DM8 存储巡检。

可直接检查：

```text
.DBF
dm.ctl
```

支持的模式：

* 本地目录
* 远程 SSH 服务器

存储扫描器可以基于二进制特征识别可疑数据块。

例如：

```text
ZERO_PAGE（全零页）
CONSTANT_FILL（单一字节填充）
TRUNCATED（文件截断）
```

检出的数据块按以下维度报告：

* 物理页号
* 文件偏移
* 表空间

也可以生成结构化的 Word 报告。

---

# 📝 SQL 编辑器

RaccoonX 在 Web UI 中内置了交互式 SQL 编辑器。

功能包括：

* SQL 语法高亮
* 数据库对象浏览
* 结果表格
* 执行历史
* 友好的错误提示

编辑器支持已配置驱动与插件所覆盖的数据库类型。

---

# 🌍 多语言支持

RaccoonX 目前支持：

1. 中文
2. English
3. 繁體中文
4. 日本語
5. 한국어
6. Español
7. Français
8. Deutsch
9. Русский

切换语言的方式：

* Web UI 语言选择器
* CLI 参数

示例：

```bash
python -m entrypoints.cli --lang en
```

本地化覆盖范围：

* 界面文字
* 菜单
* 报告
* AI 诊断标签

RaccoonX 同时支持：

* 深色主题
* 浅色主题

---

# 🔌 REST API

RaccoonX 提供用于自动化与集成的 REST API。

通过 API Key 认证，可以将 RaccoonX 集成到：

* CI/CD
* 监控平台
* 自动化系统
* 内部运维平台

## 健康检查

```bash
curl http://localhost:5003/api/v1/health
```

## 触发巡检

```bash
curl -X POST http://localhost:5003/api/v1/inspect \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "db_type": "mysql",
    "host": "192.168.1.100",
    "port": 3306,
    "user": "root",
    "password": "****"
  }'
```

## 异步巡检

```bash
curl -X POST http://localhost:5003/api/v1/inspect \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "db_type": "oracle",
    "host": "192.168.1.200",
    "service_name": "ORCL",
    "user": "system",
    "password": "****",
    "mode": "async"
  }'
```

## API 端点

| 端点                        | 方法 | 说明       |
| --------------------------- | ---- | ---------- |
| `/api/v1/health`            | GET  | 健康检查   |
| `/api/v1/inspect`           | POST | 触发巡检   |
| `/api/v1/inspect/{task_id}` | GET  | 查询任务结果 |
| `/api/v1/inspects`          | GET  | 近期任务列表 |
| `/share/<share_id>`         | GET  | 查看分享报告 |

生产环境建议使用 nginx 等反向代理，并定期轮换 API Key。

---

# 📦 打包分发

RaccoonX 可以使用 PyInstaller 打包为独立可执行文件。

## Windows

```bash
rd /s /q build dist __pycache__
pyinstaller dbcheck.spec
cd dist
dbcheck.exe
```

## Linux

```bash
pyinstaller build/dbcheck_linux.spec
cd dist
./dbcheck
```

---

# 🧩 环境要求速查

| 数据库                             | Python 驱动         | 额外依赖                   |
| ---------------------------------- | ------------------- | -------------------------- |
| MySQL / TiDB                       | pymysql             | —                          |
| PostgreSQL / IvorySQL / KingbaseES | psycopg2-binary     | —                          |
| Oracle                             | oracledb            | 无需 Instant Client        |
| SQL Server                         | pyodbc              | ODBC Driver 17             |
| DM8                                | dmpython            | DM8 客户端库               |
| YashanDB                           | yashandb            | —                          |
| GBase 8s                           | jaydebeapi + JPype1 | JDK + JDBC 驱动            |
| Oracle JDBC                        | JPype1 + ojdbc      | JDK + ojdbc                |
| MongoDB                            | pymongo             | —                          |
| DB2 LUW                            | JPype1 + db2jcc4    | JDK + db2jcc4.jar          |
| OceanBase                          | pymysql             | —                          |
| Redis / Redis 集群                 | redis-py            | —                          |
| ClickHouse                         | JDBC                | JDK + JDBC 驱动            |

---

# ❓ FAQ

### 部分巡检章节为空？

各数据库类型的巡检覆盖范围不同。

RaccoonX 使用数据库专属模板，并在部分指标不可用时通过优雅降级机制处理。

---

### 连接失败，应该检查什么？

检查：

1. 数据库网络可达性
2. 防火墙规则
3. 数据库监听 / 服务状态
4. 用户名与密码
5. 用户权限
6. 数据库专属连接参数

---

### GBase 8s 报 "Driver not found"。

使用 Docker 镜像时，所需的 JDK 与 JDBC 驱动已内置。

源码安装时，请检查 JDBC 驱动与 JDK 配置。

---

### AI 诊断不工作。

检查：

```bash
ollama serve
```

并确认所选模型已下载：

```bash
ollama pull qwen3:30b
```

然后在 Web UI 中核对 AI 设置。

---

### Oracle 报 ORA-01017。

核对：

* 用户名
* 密码
* 服务名
* 认证模式

SYSDBA 用户请在 Web UI 中勾选 SYSDBA 选项，或使用：

```text
sys as sysdba
```

---

### 推荐的修复可以自动执行吗？

任何建议都不应盲目应用到生产环境。

RaccoonX 提供巡检结果与建议处置动作。数据库工程师应在执行前结合实际环境审阅变更方案。

---

# 🔄 从 DBCheck 到 RaccoonX

RaccoonX 前身是 **DBCheck**。

项目从一款数据库巡检工具起步，逐步扩展为更完整的数据库运维平台。

**RaccoonX** 这个名字代表项目的下一个阶段。

```text
DBCheck
   │
   ├── 数据库巡检
   ├── 风险分析
   ├── 性能分析
   ├── AI 诊断
   ├── 监控
   ├── 插件
   └── 自动化
          ↓
      RaccoonX
```

原 DBCheck 名称仍可能出现在：

* 仓库地址
* Docker 镜像名
* 既有部署配置
* 历史文档
* 既有脚本

随着 RaccoonX 品牌演进，这些将逐步统一。

> **RaccoonX — 原名 DBCheck。**

---

# 🤝 参与贡献

RaccoonX 是开源项目，欢迎各种形式的贡献。

你可以通过以下方式参与：

* ⭐ 给仓库点 Star
* 🐛 报告 Bug
* 💡 提交功能建议
* 🗄️ 完善数据库巡检规则
* 🔌 开发插件
* 📝 完善文档
* 🌍 完善翻译
* 🔧 提交 Pull Request
* 📢 把 RaccoonX 分享给其他数据库工程师

提交 Pull Request 前，请先阅读：

```text
CONTRIBUTING.md
```

---

# 🐛 问题与功能建议

请通过 GitHub Issues 提交：

* Bug 报告
* 功能建议
* 数据库兼容性问题
* 安装问题
* 性能问题
* 文档问题

报告数据库专属问题时，请提供：

```text
数据库类型
数据库版本
RaccoonX 版本
操作系统
安装方式
相关报错信息
复现步骤
```

发帖前请先移除密码、凭据、IP 地址等敏感信息。

---

# 💬 社区

欢迎围绕以下话题交流：

* 数据库运维
* DBA 自动化
* 数据库巡检
* 数据库性能
* AI 辅助数据库运维
* 插件开发
* 开源开发
* 数据库兼容性

不属于 Bug 报告的疑问与想法，请使用 GitHub Discussions。

---

# 🙏 致谢

RaccoonX 参考并借鉴了数据库社区的项目、工具与思路。

特别感谢以下项目：

* [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK)
* [Zhh9126/SQL-SERVER-CHECK](https://github.com/Zhh9126/SQL-SERVER-CHECK)

感谢每一位为数据库社区与开源社区贡献力量的人。

---

# ❤️ 支持项目

RaccoonX 基于 **Apache License 2.0** 开源。

如果 RaccoonX 对你有帮助，有很多方式可以支持这个项目。

### ⭐ 点个 Star

一个 Star 能让更多数据库工程师发现 RaccoonX。

```text
使用它
  ↓
喜欢它
  ↓
⭐ Star 它
  ↓
分享它
  ↓
更多人发现它
  ↓
更多反馈
  ↓
更好的 RaccoonX
```

### 🐛 报告 Bug

一份好的 Bug 报告比一个 Star 更有价值。

### 💡 提出功能建议

你的一线需求决定着接下来做什么。

### 🔧 贡献代码

欢迎提交 Pull Request。

### 📢 分享项目

如果你认识可能用得上 RaccoonX 的 DBA 或数据库工程师，把项目分享出去就是最有价值的支持之一。

---

## ☕ 赞助

一些用户通过捐赠支持了这个项目。

金额从来不是最重要的。

对一个独立开源项目来说，一笔小小的捐赠意味着：

> 有人真正用过它。
> 有人注意到了它。
> 有人相信它值得继续做下去。

感谢每一位通过捐赠、Star、Issue、Pull Request、建议、文章、测试，或只是向另一位工程师提起 RaccoonX 来支持项目的人。

<img src="snapshot/pay.png" alt="RaccoonX 赞助二维码" width="800" />

<img src="snapshot/dbcheck-badge-800w.png" alt="RaccoonX 支持者徽章" width="800" />

> 赞助时请备注姓名或昵称 ❤️

### 社区支持者

| 日期       | 昵称          | 编号      |
| ---------- | ------------- | --------- |
| 2026-04-28 | 自由的风      | No.000001 |
| 2026-04-29 | 黄嵘          | No.000002 |
| 2026-05-04 | 张佰政        | No.000003 |
| 2026-06-02 | 残酷月光      | No.000004 |
| 2026-06-03 | 大树          | No.000005 |
| 2026-06-07 | 岳彩波（Adil0518） | No.000006 |
| 2026-06-17 | 轩            | No.000007 |
| 2026-06-18 | 卿云          | No.000008 |
| 2026-06-18 | yuanlnet      | No.000009 |
| 2026-06-18 | 赵法威        | No.000010 |
| 2026-06-19 | 类延良        | No.000011 |
| 2026-06-19 | 渺渺兮予怀    | No.000012 |
| 2026-09-06 | leon          | No.000013 |
| 2026-09-24 | James.Yao          | No.000014 |

---

# 📜 开源协议

RaccoonX 是基于以下协议的开源软件：

**Apache License 2.0**

完整协议文本见：

```text
LICENSE
```

依据 Apache License 2.0 的条款，你可以自由地使用、修改、分发本项目并基于其构建。

---

# ⚠️ 第三方商标声明

本项目中提及的名称、徽标、商标与数据库技术归其各自所有者所有。

它们在 RaccoonX 中的出现仅表示与对应技术的兼容或集成，不构成任何背书、从属或合作关系。

---

# 🦝 持续构建

RaccoonX 不是由一个大团队打造的。

它靠代码、反馈、Issue、想法、测试、文档、贡献，以及每一位选择使用它的人成长。

```text
25,000+ Docker Pulls

一次拉取一小步，
我们持续构建。

🦝 RaccoonX
```

**感谢你成为 RaccoonX 社区的一员。**

---

**作者：** [Jack Ge](https://github.com/fiyo)
**项目：** RaccoonX — 原名 DBCheck
**官网：** https://raccoonx.cn/
**邮箱：** [sdfiyon@gmail.com](mailto:sdfiyon@gmail.com)
