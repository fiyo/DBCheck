# DBCheck 专业版 — 商业数据库智能巡检工具

![logo](snapshot/dbcheck_logo_info.png)

DBCheck 专业版是一款商业、跨平台的数据库自动化健康巡检工具，支持 **13 种主流关系型数据库**，通过执行预定义的巡检 SQL 并采集系统资源，自动生成标准化的 Word 巡检报告。同时提供 SQL 编辑器、远程终端、可配置巡检章节、配置基线管理、历史趋势分析、AI 智能诊断、索引健康分析、慢查询深度分析、服务器巡检、分享链接、数据脱敏导出等高级功能。

> **注意**：本文及 DBCheck 软件中包含第三方的软件名称、logo、商标、徽章等均为第三方公司或机构所有，本文以及 DBCheck 软件中展示仅表示本软件支持对接相应的数据库或平台，并不暗示与其有任何关联或合作。

> 官网：[https://dbcheck.top](https://dbcheck.top) &nbsp;|&nbsp; 邮箱：sdfiyon@gmail.com
> 
> Language: [English](./README.md) | 语言：[中文](./README_zh.md)

[![Version](https://img.shields.io/badge/版本-v26.7.21.1-blue.svg)]()
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)]()
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![AI](https://img.shields.io/badge/AI-Ollama+OpenAI-orange.svg)]()
[![RAG](https://img.shields.io/badge/知识库增强-RAG-red.svg)]()
[![WebUI](https://img.shields.io/badge/WebUI-Flask-success.svg)]()
[![WeChat](https://img.shields.io/badge/公众号-山东Oracle用户组-brightgreen?logo=WeChat)]()
[![WebSite](https://img.shields.io/badge/网址-www.dbcheck.top-green.svg)](https://dbcheck.top)
[![Docker Pulls](https://img.shields.io/docker/pulls/jackge12345/dbcheck?style=flat-square&label=Docker%20Pulls&cacheSeconds=300)](https://hub.docker.com/r/jackge12345/dbcheck)
[![GHCR Pulls](https://img.shields.io/badge/88-blue.svg?label=GHCR+Pulls)]()
![Downloads](https://img.shields.io/github/downloads/fiyo/DBCheck/total?style=flat-square&label=Source+Downloads)

---

## 支持的数据库

| 数据库 | 驱动方式 | 默认端口 | 说明 |
|--------|---------|:---:|------|
| MySQL | pymysql | 3306 | 5.6 / 5.7 / 8.0+ |
| MariaDB | pymysql（MySQL 协议） | 3306 | 10.3+ |
| PostgreSQL | psycopg2 | 5432 | 10+ |
| Oracle | oracledb（纯 Python，无需客户端） | 1521 | 11g R2 / 12c / 19c / 21c+ |
| Oracle (JDBC) | JDBC（JPype1 + ojdbc） | 1521 | 11g / 12c / 19c / 21c+，完整移植 Oracle 11g 巡检模板 |
| SQL Server | pyodbc + ODBC Driver 17 | 1433 | 2012+ |
| DM8（达梦） | dmpython | 5236 | 国产数据库 |
| TiDB | pymysql（MySQL 协议） | 4000 | 6.5+ |
| IvorySQL | psycopg2（PG 协议） | 5333 | PG + Oracle 双兼容 |
| YashanDB（崖山） | yashandb | 1688 | Oracle 兼容，国产数据库 |
| KingbaseES（人大金仓） | psycopg2（PG 协议） | 54321 | 国产数据库 |
| GBase 8s | JDBC（jaydebeapi + JDK） | 9088 | 国产数据库 |
| MongoDB | pymongo | 27017 | 4.0+ |
| DB2（LUW） | JDBC（JPype1 + db2jcc4） | 50000 | 11.5+ / 12.x（LUW） |

> **说明**：Oracle (JDBC) 是基于 JDBC (JPype) 连接的独立插件，提供与 Oracle 原生驱动相同的巡检能力，适合无法安装 Oracle 客户端的场景。

---

## 🐳 Docker 快速上手（推荐）

一条命令启动，无需安装任何依赖：

```bash
# Docker Hub
docker pull jackge12345/dbcheck:latest
docker run -d -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_reports:/app/reports \
  --name dbcheck \
  jackge12345/dbcheck:latest

# GitHub Container Registry（国内友好）
docker pull ghcr.io/fiyo/dbcheck:latest
docker run -d -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_reports:/app/reports \
  --name dbcheck \
  ghcr.io/fiyo/dbcheck:latest
```

访问 **http://localhost:5003**，默认账号为 `admin`，密码为 `admin123`（首次登录后请在账户中心修改密码）。

### docker-compose（推荐）

```bash
curl -o docker-compose.yml https://raw.githubusercontent.com/fiyo/DBCheck/main/docker-compose.yml
docker compose up -d
```

> **GBase 8s 特别说明**：Docker 镜像已预装 JDK + JDBC 驱动，添加 GBase 数据源后直接可用，无需额外配置。

---

## 源码安装快速上手

### 环境要求

- Python 3.10+
- 各数据库对应的 Python 驱动（见上表）

```bash
# 克隆项目
git clone https://github.com/fiyo/DBCheck.git
cd DBCheck

# 安装依赖
pip install -r requirements.txt

# 启动 Web UI
python web_ui.py
```

访问 **http://localhost:5003**。

### CLI 命令行模式

```bash
python main.py           # 中文界面
python main.py --lang en # 英文界面
python web_ui.py         # Web 界面
```

---

## 核心功能一览

| 功能 | 说明 |
|------|------|
| 🗄️ 数据源管理 | 统一管理所有数据库实例，支持分组、批量巡检、CSV 导入导出 |
| 📋 数据库巡检 | 覆盖 13 种数据库，160+ 条增强规则，自动生成 Word 报告 |
| 🔌 插件系统 | 可扩展插件架构，支持生命周期管理（安装/卸载）、插件数据独立、插件市场 |
| 🔍 慢查询深度分析 | 关联执行计划、I/O 模式、锁等待等维度，AI 辅助根因分析 |
| 🔒 锁诊断 | 阻塞链可视化、死锁统计、长事务检测，含可执行修复脚本 |
| 📊 索引健康分析 | 检测缺失索引、冗余索引、长期未使用索引 |
| ⚙️ 配置基线检查 | 各库关键参数当前值与推荐值对比分析 |
| 📈 历史趋势分析 | 多轮巡检数据聚合，生成趋势折线图，前后对比变化 |
| 🤖 AI 智能诊断 | 基于本地 Ollama，根据巡检指标自动生成优化建议 |
| 💬 AI 对话巡检 | Web UI 右下角 AI 面板，自然语言发起巡检 |
| 📡 实时监控 | 首页实时监控（吞吐/连接/延迟/可用性）+ 慢查询/活跃连接热力图 |
| 🖥️ 服务器巡检 | CPU/内存/磁盘/网络/进程全面检查 |
| 🔗 分享链接 | 一键生成在线分享链接，免登录查看报告 |
| ⏰ 定时任务 | Cron 表达式定期巡检，完成后自动邮件/Webhook 通知 |
| 📚 RAG 知识库 | 上传运维文档，AI 诊断时自动检索相关知识 |
| 📊 AWR 报告分析 | 上传 Oracle AWR HTML 报告，自动生成 Word 分析报告 |
| 💿 DM8 离线存储检查 | 离线检查 DM8 存储健康，扫描数据文件定位坏块（全零/异常填充/截断） |
| 📝 SQL 编辑器 | Web UI 内置，语法高亮，结果表格，执行历史 |
| 🖥️ 远程终端 | 基于 SSH，多标签页，全屏模式 |

---

## 专业版专属能力

> 专业版通过 `version.py` 的 `EDITION='professional'` 标识启用，诊断中心等专属入口仅在专业版渲染。在上方社区版通用能力之外，专业版额外提供：

### 协同诊断中枢（智能诊断中心）

把「一句目标 + 一个数据源」交给一组专精的**诊断专员**，在**共享上下文（黑板）**上协同推进，最终输出：异常发现、根因推断、可执行处置方案，以及方案代价评估与工单。

| 专员 | 职责 |
|------|------|
| 运行监控哨兵 | 紧盯宿主真实资源与数据库细粒度指标，第一时间发现 CPU、IO、内存、连接、锁、复制等异常波动并预警 |
| 深度巡检分析专员 | 实时调用巡检引擎对目标数据源产出报告，提炼配置 / 容量 / 性能等维度风险并标注等级 |
| 根因定位分析专员 | 汇总监控异常与巡检风险，关联聚类推断根因，并给出处置主线 |
| SQL 治理专员 | 针对慢 SQL 与高代价语句，给出改写、索引与变更审核建议 |
| 锁等待分析专员 | 针对锁等待与阻塞，溯源持锁会话与等待链并给出拆解建议 |

- **共享上下文（黑板）**：所有中间结论、发现与处置方案沉淀于同一空间，专员间直接读写，避免结论在层层传递中失真。
- **任务动态规划**：运行监控、深度巡检、根因定位常驻协同；SQL 治理与锁分析在发现相关现象时动态提前执行。
- **单点容错**：单个专员异常不影响整体协同，异常记入上下文，协同继续推进。
- **流式协同**：中枢逐个调度专员并产出进度事件，前端经 SSE 实时展示"现在谁在研判"。
- **方案代价验证（Cost Optimizer）**：对处置方案逐条评估代价 / 收益 / 可行性，推荐"先易后难"执行顺序，并标注是否需维护窗口或可由系统自动执行。
- **工单闭环**：诊断结果一键生成工单，跟踪 `待处理 / 处理中 / 已解决 / 已关闭 / 已取消`，回写执行反馈，形成"诊断 → 派单 → 处置 → 反馈"闭环。
- **诊断历史**：每次协同诊断完整落库（本地 SQLite），生成诊断编号（`diag_no`），支持按数据源筛选、查看完整结果、一键回填工单。

### eBPF 内核级宿主采集

当目标 Linux 主机具备 **Python3 + bcc + root** 时，可启用 eBPF，获取用户态工具难以企及的内核指标：
- **块设备服务时间百分位（p50 / p95 / p99，微秒级）**：内核 kprobe 测量每次块 IO 的 device service time，精度高于 psutil 聚合的 `await`，擅长暴露长尾抖动。
- **按进程的 IO 归因**：IO 起始记录 pid / 命令名，Completion 关联，输出 Top IO 进程。
- **按进程的 CPU 占用归因**：基于 `sched:sched_switch` 跟踪点计算 on-CPU 时间，输出 Top CPU 进程，区分"真忙"与"等 IO"。

克制与安全：默认关闭、仅 opt-in，绝不向生产默认注入 eBPF；每条指标带 `host_collector_source` 标记（`ebpf` / `psutil` / `unavailable`）；任一环节失败安全降级为 psutil；无 Python / psutil 时降级为纯 Shell（`/proc`）采集。

### SSH 安全宿主采集

面向"不想在被采集机装 agent"的场景：
- **无 agent、无 Python 也可采集**：纯 Shell（`/proc`）采集脚本经 SSH 注入远端执行；目标机有 Python3 + bcc 才进一步启用 eBPF。
- **安全护栏**：全局并发信号量（Semaphore = 4）限制 SSH 总数；每主机一把锁（同刻至多 1 条连接）；`set_keepalive(15)` 保活；通道 `settimeout(12)` 有界读取；硬超时看门狗（默认 8s，SIGALRM + 独立线程 `os._exit`）防止会话悬挂；瞬时错误退避重试（max_retries = 2），认证失败不重试。
- **凭据安全**：实例密码经 **Fernet 加密存储**，采集时解密使用，绝不把密文当明文传给远端或数据库。

### 统一可观测视图

专业版把**宿主资源（eBPF / psutil / SSH）+ 数据库细粒度指标 + 巡检风险**统一到同一分析平面。一次协同诊断里，既能看到"磁盘 p99 时延突增"，也能看到"对应时段的慢 SQL 与锁等待"，根因定位不再是孤立数字，而是可串联的证据链。

---

## 社区版 vs 专业版 · 核心能力对比

| 能力 | 社区版 | 专业版 |
|------|:-----:|:-----:|
| 多数据源巡检 | ✅ | ✅ |
| 实时监控 + 健康大屏 | ✅ | ✅ |
| AI 智能诊断 | ✅ | ✅ |
| 插件体系 | ✅ | ✅ |
| 企业级 RBAC | ✅ | ✅ |
| eBPF 内核级宿主采集 | — | ✅（opt-in） |
| SSH 安全宿主采集 | — | ✅ |
| 协同诊断中枢（5 专员 + 黑板） | — | ✅ |
| 方案代价验证 | — | ✅ |
| 工单闭环 | — | ✅ |
| 诊断历史 | — | ✅ |
| 统一可观测视图 | — | ✅ |

> 社区版聚焦"发现风险"，专业版进一步解释"为什么"与"先做什么"。

---

## 🔌 插件系统

DBCheck v2.8.0 引入了完全独立的插件架构。插件现在可以管理自己的生命周期和数据，实现真正的可扩展性。

### 核心特性

| 特性 | 说明 |
|------|------|
| 插件生命周期管理 | `on_install()` 和 `on_uninstall()` 方法，自动初始化和清理数据 |
| 插件数据独立 | 每个插件自带 `template_data.json`、`baseline_data.json` 和规则引擎文件 |
| 插件市场 | 通过 Web UI 浏览、安装、卸载、启用/禁用插件 |
| 干净卸载 | 卸载插件时自动清理模板、基线和规则 |
| 插件配置 | 每个插件有自己的 `plugin.json` 用于元数据和配置 |

### 插件开发

插件是独立的 Python 包，结构如下：

```
plugins/available/your_plugin/
├── plugin.json          # 插件元数据
├── main_plugin.py      # 插件类（继承 InspectionPlugin）
├── template_data.json  # 巡检模板（可选）
├── baseline_data.json  # 基线配置（可选）
└── rules/             # 规则引擎文件（可选）
```

详细插件开发指南，请参见[插件开发文档](docs/plugin/)。

### 内置插件（v2.8.0）

| 插件 | 数据库 | 说明 |
|------|--------|------|
| MongoDB | MongoDB 4.0+ | 基础巡检（连接状态、数据库统计、慢查询） |
| Oracle (JDBC) | Oracle 11g/12c/19c/21c+ | 完整移植 Oracle 11g 巡检模板（21 个章节、58 个查询、11 条基线） |
| DB2（JDBC） | DB2 LUW 11.5+ / 12.x | JDBC（JPype1 + db2jcc4）LUW 巡检插件，42 条规则，基于系统目录 SQL |

> **说明**：插件完全独立。安装插件时自动初始化数据；卸载插件时自动清理所有关联数据。

---

## 数据库巡检

### 各库巡检覆盖

| Category | MySQL | PG | Oracle | Oracle (JDBC) | SQL Server | DM8 | TiDB | IvorySQL | YashanDB | KingbaseES | GBase 8s | MongoDB |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Basic Info (version/instance/DB) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Sessions & Connections | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Memory & Cache | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| Tablespaces | — | — | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | — | ✅ | — |
| SGA / PGA Memory | — | — | ✅ | ✅ | — | ✅ | — | — | ✅ | — | — | — |
| Redo Logs | — | — | ✅ | ✅ | — | ✅ | — | ✅ | — | — | — | — |
| Archive & Backup | — | — | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | — | — | — |
| Key Parameter Config | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Invalid Objects | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| User Security Audit | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Top SQL / Slow Queries | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Replication / Data Guard | ✅ | ✅ | — | — | — | — | ✅ | ✅ | — | ✅ | — | ✅ |
| RAC Cluster | — | — | ✅ | ✅ | — | — | — | — | — | — | — | — |
| Lock & Blocking Detection | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| Object Statistics | — | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | — |
| Partitioned Tables | — | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | — | — |
| Chunks / Disk Storage | — | — | — | — | — | — | — | — | — | — | — | — |
| Logical Logs / Checkpoints | — | — | — | — | — | — | — | — | — | — | — | — |
| Database Status & Stats | — | — | — | — | — | — | — | — | — | — | — | ✅ |


### Word 报告结构（Oracle 示例）

| 章节 | 内容 |
|------|------|
| 封面 | 数据库名、版本、主机信息、巡检人、时间戳 |
| 第1章 | OS 主机信息（CPU / 内存 / 磁盘） |
| 第2章 | 数据库基本信息 |
| 第3章 | 表空间（含自动扩展） |
| 第4章 | SGA / PGA 内存分析 |
| 第5章 | 关键参数配置 |
| 第6~19章 | Undo / Redo / 归档 / DG / RAC / ASM / 会话 / 性能 / 安全等 |
| 第20章 | 风险与建议（含可执行修复 SQL） |
| 第21章 | AI 诊断建议（Markdown 自动渲染为 Word） |
| 第22章 | 报告说明 |

> 各数据库类型报告结构略有差异，均可通过 Web UI 自由配置巡检章节。

### DB2 LUW 巡检（JDBC）

IBM Db2 LUW（Linux/Unix/Windows）**11.5+ / 12.x** 通过 JDBC 插件（`db2_jdbc`）提供支持，使用 **JPype1 + IBM `db2jcc4.jar`** 连接（默认端口 **50000**）。采用数据驱动巡检，共 **6 个章节**、内置 **42 条规则**，全部基于 Db2 系统目录与监控视图编写（不含已废弃的 9.7 旧目录名）。

| 维度 | 覆盖内容 |
|------|----------|
| 版本与实例 | DB2 版本、实例配置（dbm cfg）、数据库配置（db cfg）、成员/分区拓扑 |
| 表空间与存储 | 表空间大小、使用率、自动扩展、容器状态 |
| 缓冲池 | 缓冲池定义、命中率、容量建议 |
| 会话与应用 | 活跃应用、Top 消耗者、连接饱和度 |
| 锁与阻塞 | 锁等待、持锁情况、阻塞链、长事务 |
| 表与索引 | 表/行统计、索引 RUNSTATS 新鲜度、未使用/冗余索引 |
| Top SQL | 来自包缓存的高代价 SQL |
| 活动监控 | Mon-get 活动指标、耗时热点 |
| 内存 | `MON_GET` 内存集、dbm 内存分布 |

生成的 Word 报告包含统一的**系统资源**章节（CPU / 内存 / 磁盘）、**风险与建议**章节（含一键修复 SQL）以及 **AI 诊断建议**章节。需要 `db2jcc4.jar` 驱动与 JDK 8/11/17——Docker 镜像已内置，Db2 数据源开箱即用。

---

## 智能风险分析

自动检测各类数据库潜在风险，**每条风险附带可执行修复 SQL，支持一键执行**。

### 风险规则统计

| 数据库 | 规则数 | 覆盖维度 |
|--------|:---:|------|
| MySQL | 35+ | 连接、内存、磁盘、慢查询、锁、安全、复制 |
| PostgreSQL | 27+ | 连接、缓存、性能、安全、归档、死元组 |
| Oracle | 20+ | 表空间、TEMP、会话、SGA、Redo、DG、ASM、安全 |
| Oracle (JDBC) | 20+ | 与 Oracle 相同（完整移植 Oracle 11g 模板） |
| SQL Server | 15+ | 连接、会话、等待、锁、死锁、备份、内存 |
| DM8 | 16+ | 表空间、内存池、会话、事务、备份、安全 |
| TiDB | 18+ | 连接、内存、磁盘、慢查询、锁、安全、Placement |
| IvorySQL | 27+ | 与 PostgreSQL 相同 |
| YashanDB | 15+ | 连接、内存、表空间、锁、备份、安全 |
| KingbaseES | 19+ | 连接、缓存、性能、安全、归档、统计信息 |
| GBase 8s | 6+ | 连接、dbspace、日志、内存、密码策略 |
| MongoDB | 10+ | 连接、内存、操作、复制、安全 |
| DB2（LUW） | 42 | 表空间、缓冲池、锁、内存、配置、Top SQL、安全 |

### 一键修复

每条风险卡片提供「执行修复」按钮，危险操作（DELETE/DROP/TRUNCATE）需二次确认，所有操作均有日志记录。

---

## AI 智能诊断

基于本地 **Ollama** 部署，巡检数据完全离线，无需联网。

| 后端 | 说明 | 适用场景 |
|------|------|---------|
| `ollama` | 纯本地，零成本，数据不出机器 | 内网环境、高安全要求 |
| `openai` | 云端 API（OpenAI / DeepSeek），需联网 | 允许云端 API 的环境 |
| `disabled` | 禁用 AI（默认） | 不需要 AI 功能 |

**快速开始：**

```bash
ollama pull qwen3:30b          # 拉取诊断模型（越大效果越好）
ollama pull nomic-embed-text    # 拉取 RAG 嵌入模型（知识库功能需要）
python web_ui.py                # 启动后在 AI 设置页面配置
```

---

## 其他功能

### SQL 编辑器

Web UI 内置交互式 SQL 编辑器，支持全部 13 种数据库，语法高亮、结果表格、错误友好提示。

### 首页实时监控

首页「📡 实时监控」面板按实例展示实时 ECharts 图表，通过 flask-socketio 每 30s 自动推送刷新（v2.10.0 新增）：

- **响应延迟 (ms)** —— TCP 往返时延，所有数据库类型均有。
- **吞吐（QPS / TPS）** —— 深采计数器（查询数、事务数、批处理请求、编译次数等）自动差分算速率。支持 MySQL/TiDB、PostgreSQL/PG/Kingbase、Oracle、达梦 DM8 与 SQL Server。
- **连接数** —— 活跃/总会话数与运行中的会话数。

**非深采实例的连通性画像**：暂不支持深采（或深采临时失败）的实例不再展示空白图表，而是显示「端口可用性」时间线（可达/不可达）与「连通性诊断」仪表盘（可用率 + 真实失败原因，如认证失败、断路器退避、端口不可达或"该类型暂不支持深采"），即使只有 TCP 层数据也能保持信息完整。

### 慢查询与连接热力图

慢查询 + 活跃连接实时监控，热力图可视化，自动刷新（5~60 秒可调），支持 CSV 导出。

### 远程终端

基于 SSH，支持密码/密钥认证，多标签页管理，全屏模式。

### 服务器巡检

独立于数据库巡检，覆盖 CPU / 内存 / 磁盘 / 网络 / 服务 / 进程，生成专业服务器巡检报告。

### 历史趋势分析

多轮巡检数据自动聚合，Web UI 趋势分析页面展示折线图 + 阈值线，前后对比变化用彩色箭头标注。

### 定时任务与通知

支持 Cron 表达式，快捷预设（每天/工作日/每周/每月），任务完成后自动邮件（附件 Word 报告）或 Webhook（企业微信/钉钉/自定义 JSON）通知。

### 分享链接

一键生成在线分享链接，免登录查看报告，权限隔离，自动记录访问次数，随时删除。

### 配置基线管理

Web UI 可视化编辑各库关键参数的推荐值、阈值和合规规则。当前支持：

- MySQL：22 项参数（buffer pool、连接数、binlog 等）
- PostgreSQL：21 项参数（shared_buffers、work_mem、WAL 等）
- Oracle：12 项参数（SGA/PGA、processes、undo 等）
- Oracle (JDBC)：12 项参数（与 Oracle 相同）
- SQL Server：6 项参数（内存、并行度、备份压缩等）
- DM8：7 项参数（内存目标、会话数、缓冲池等）
- TiDB：9 项参数（buffer pool、连接数、并发度等）
- YashanDB：8 项参数（缓冲池、连接、日志等）
- KingbaseES：7 项参数（连接、缓冲、vacuum 等）
- GBase 8s：9 项参数（MAXCONNECTIONS、SHMVIRTSIZE、BUFFERS、LOGSMAX 等）
- MongoDB：8 项参数（最大连接数、缓存大小、复制集等）

### 巡检章节管理

可配置驱动，每种数据库可独立添加/删除/排序/启停巡检章节，Word 报告动态生成。

### AWR 报告分析

上传 Oracle AWR HTML 报告，自动解析关键性能指标，生成结构化 Word 分析报告，支持 AI 辅助诊断。

### DM8 离线存储检查

**无需启动数据库实例**，直接扫描 DM8 数据文件目录（`.DBF` 文件 + `dm.ctl`）即可评估存储健康度。支持本地目录与 SSH 远程两种方式。

- **本地 / SSH 远程双模式** —— 既可指定本地目录，也可通过 SSH（密码 / 密钥）连接远程服务器扫描其数据文件。
- **数据块损坏分析** —— 基于通用二进制信号识别可疑坏块：
  - `ZERO_PAGE` —— 整页全为 `0x00`
  - `CONSTANT_FILL` —— 整页为单一字节（如全 `0xFF`）
  - `TRUNCATED` —— 文件末页字节数不足页大小（文件被截断）
  - 每个坏块都标注物理页号与文件偏移，并归属到对应表空间（由 `dm.ctl` 解析）。
- **Word 报告 + Web UI 查看** —— 自动生成结构化 Word 报告（含独立的「数据块损坏分析」章节），同时坏块清单可直接在 Web UI 中查看。报告统一保存至 `reports/` 目录。

### RAG 知识库

上传 PDF / Word / Markdown / TXT 文档，自动向量化，AI 诊断时自动检索相关知识，生成更精准的建议。

### 多语言与主题

- **支持 9 种语言**：简体中文（默认）、English、繁體中文、日本語、한국어、Español、Français、Deutsch、Русский
- 可随时通过 Web UI 语言选择器（右上角）或 CLI 参数（`python main.py --lang <语言码>`）切换
- 界面文字、菜单、报告模板、AI 诊断标签均已本地化
- 支持深色 / 浅色主题，偏好自动保存

---

## REST API

API Key 认证，支持 CI/CD 和监控系统集成。

```bash
# 健康检查
curl http://localhost:5003/api/v1/health

# 触发巡检（同步）
curl -X POST http://localhost:5003/api/v1/inspect \
  -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"db_type":"mysql","host":"192.168.1.100","port":3306,"user":"root","password":"****"}'

# 触发巡检（异步，返回 task_id）
curl -X POST http://localhost:5003/api/v1/inspect \
  -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"db_type":"oracle","host":"192.168.1.200","service_name":"ORCL","user":"system","password":"****","mode":"async"}'
```

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/health` | GET | 健康检查 |
| `/api/v1/inspect` | POST | 触发巡检 |
| `/api/v1/inspect/{task_id}` | GET | 查询任务结果 |
| `/api/v1/inspects` | GET | 近期任务列表 |
| `/share/<share_id>` | GET | 查看分享报告 |

> 生产环境建议搭配 nginx 反向代理，定期轮换 API Key。

---

## 打包分发

使用 PyInstaller 打包为单个可执行文件：

```bash
# Windows
rd /s /q build dist __pycache__
pyinstaller dbcheck.spec
cd dist
dbcheck.exe

# Linux
pyinstaller build/dbcheck_linux.spec
cd dist
./dbcheck
```

---

## 环境要求速查

| 数据库 | Python 驱动 | 额外依赖 |
|--------|-----------|---------|
| MySQL / TiDB | pymysql | — |
| PostgreSQL / IvorySQL / KingbaseES | psycopg2-binary | — |
| Oracle | oracledb（推荐） | 无需 Instant Client |
| SQL Server | pyodbc | ODBC Driver 17 |
| DM8 | dmpython | DM8 客户端库 |
| YashanDB | yashandb | — |
| **GBase 8s** | **jaydebeapi + JPype1** | **JDK 8/11/17 + JDBC 驱动 jar** |
| **Oracle (JDBC)** | **jpype1 + ojdbc** | **JDK 8/11/17 + ojdbc6.jar/ojdbc8.jar** |
| **MongoDB** | **pymongo** | **—** |
| **DB2（LUW）** | **jpype1 + db2jcc4** | **JDK 8/11/17 + db2jcc4.jar** |

---

## FAQ

**Q：部分内容为空或缺失？**
A：模板渲染兼容性问题时会自动降级渲染，关键数据不会丢失。

**Q：连接失败？**
A：检查数据库是否允许远程访问、用户权限、防火墙端口。

**Q：GBase 8s 连接报 "Driver not found"？**
A：确认 JDBC 驱动 jar 在 `drivers/gbase/jdbc-3.5.1.jar`，且 JDK 已安装。Docker 镜像已预装，无需额外配置。

**Q：AI 诊断不工作？**
A：确认 Ollama 已启动（`ollama serve`）且模型已下载（`ollama pull qwen3:30b`）。

**Q：Oracle ORA-01017 用户名密码错误？**
A：SYSDBA 用户需勾选 Web UI 的 "SYSDBA" 复选框，或 CLI 中输入 `sys as sysdba`。

**Q：风险建议仅供参考？**
A：内置阈值基于通用最佳实践，请结合实际业务评估。

---

## 致谢

本项目参考了以下项目，特此感谢：

- [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git)
- [Zhh9126/SQL-SERVER-CHECK](https://github.com/Zhh9126/SQL-SERVER-CHECK.git)

## 支持项目

> ❤️ 感谢每一位支持者的认可与鼓励。
>
> DBCheck 专业版为专有商业软件，保留一切权利。未经著作权人书面授权，不得复制、修改、反编译、分发或用于任何商业再分发。
>
> 如果项目曾帮助过您，欢迎支持；如果您选择不赞助，也完全没有关系。一个 Star、一条建议、一次 Bug 反馈，甚至一句鼓励的话，都是推动项目前进的动力。
>
> 尊重每一种选择，也感谢每一位使用者。

<img src="snapshot/pay.png" alt="赞助二维码" width="800" />

<img src="snapshot/dbcheck-badge-800w.png" alt="DBCheck 支持者徽章" width="800" />

> 赞助时请备注姓名或昵称 ❤️

### 赞助者列表

| 日期 | 昵称 | 编号 |
|------|------|------|
| 2026-04-28 | \*ck | No.000001 |
| 2026-04-29 | \*嵘 | No.000002 |
| 2026-05-04 | \*\*政 | No.000003 |
| 2026-06-02 | \*\*月光 | No.000004 |
| 2026-06-03 | \*树 | No.000005 |
| 2026-06-07 | \*0518 | No.000006 |
| 2026-06-17 | \*轩 | No.000007 |
| 2026-06-18 | \*云 | No.000008 |
| 2026-06-18 | \*lnet | No.000009 |
| 2026-06-18 | \**威 | No.000010 |
| 2026-06-19 | \**良 | No.000011 |
| 2026-06-19 | \***予怀 | No.000012 |
---

> 作者：[Jack Ge](https://github.com/fiyo) &nbsp;|&nbsp; 官网：[https://dbcheck.top](https://dbcheck.top) &nbsp;|&nbsp; 邮箱：sdfiyon@gmail.com
