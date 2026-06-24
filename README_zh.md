# DBCheck — 开源智能数据库巡检工具

![logo](snapshot/dbcheck_logo_info.png)

DBCheck 是一款开源、跨平台的数据库自动化健康巡检工具，支持 **10 种主流关系型数据库**，通过执行预定义的巡检 SQL 并采集系统资源，自动生成标准化的 Word 巡检报告。同时提供 SQL 编辑器、远程终端、可配置巡检章节、配置基线管理、历史趋势分析、AI 智能诊断、索引健康分析、慢查询深度分析、服务器巡检、分享链接、数据脱敏导出等高级功能。

> **注意**：本文及 DBCheck 软件中包含第三方的软件名称、logo、商标、徽章等均为第三方公司或机构所有，本文以及 DBCheck 软件中展示仅表示本软件支持对接相应的数据库或平台，并不暗示与其有任何关联或合作。

> 官网：[https://dbcheck.top](https://dbcheck.top) &nbsp;|&nbsp; 邮箱：sdfiyon@gmail.com
> 
> Language: [English](./README.md) | 语言：[中文](./README_zh.md)

[![Version](https://img.shields.io/badge/版本-v2.6.0-blue.svg)]()
[![License](https://img.shields.io/badge/开源协议-MIT-green.svg)]()
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
| PostgreSQL | psycopg2 | 5432 | 10+ |
| Oracle | oracledb（纯 Python，无需客户端） | 1521 | 11g R2 / 12c / 19c / 21c+ |
| SQL Server | pyodbc + ODBC Driver 17 | 1433 | 2012+ |
| DM8（达梦） | dmpython | 5236 | 国产数据库 |
| TiDB | pymysql（MySQL 协议） | 4000 | 6.5+ |
| IvorySQL | psycopg2（PG 协议） | 5333 | PG + Oracle 双兼容 |
| YashanDB（崖山） | yashandb | 1688 | Oracle 兼容，国产数据库 |
| KingbaseES（人大金仓） | psycopg2（PG 协议） | 54321 | 国产数据库 |
| GBase 8s | JDBC（jaydebeapi + JDK） | 9088 | 国产数据库 |

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

访问 **http://localhost:5003**，默认账号密码均为 `dbcheck`（首次登录后请在账户中心修改密码）。

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
| 📋 数据库巡检 | 覆盖 10 种数据库，160+ 条增强规则，自动生成 Word 报告 |
| 🔍 慢查询深度分析 | 关联执行计划、I/O 模式、锁等待等维度，AI 辅助根因分析 |
| 🔒 锁诊断 | 阻塞链可视化、死锁统计、长事务检测，含可执行修复脚本 |
| 📊 索引健康分析 | 检测缺失索引、冗余索引、长期未使用索引 |
| ⚙️ 配置基线检查 | 各库关键参数当前值与推荐值对比分析 |
| 📈 历史趋势分析 | 多轮巡检数据聚合，生成趋势折线图，前后对比变化 |
| 🤖 AI 智能诊断 | 基于本地 Ollama，根据巡检指标自动生成优化建议 |
| 💬 AI 对话巡检 | Web UI 右下角 AI 面板，自然语言发起巡检 |
| 📡 实时监控 | 慢查询 + 活跃连接实时监控，热力图可视化 |
| 🖥️ 服务器巡检 | CPU/内存/磁盘/网络/进程全面检查 |
| 🔗 分享链接 | 一键生成在线分享链接，免登录查看报告 |
| ⏰ 定时任务 | Cron 表达式定期巡检，完成后自动邮件/Webhook 通知 |
| 📚 RAG 知识库 | 上传运维文档，AI 诊断时自动检索相关知识 |
| 📊 AWR 报告分析 | 上传 Oracle AWR HTML 报告，自动生成 Word 分析报告 |
| 📝 SQL 编辑器 | Web UI 内置，语法高亮，结果表格，执行历史 |
| 🖥️ 远程终端 | 基于 SSH，多标签页，全屏模式 |

---

## 数据库巡检

### 各库巡检覆盖

| 巡检维度 | MySQL | PG | Oracle | SQL Server | DM8 | TiDB | IvorySQL | YashanDB | KingbaseES | GBase 8s |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 基本信息（版本/实例/库） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 会话与连接 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 内存与缓存 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 表空间 | — | — | ✅ | ✅ | ✅ | — | — | ✅ | — | ✅ |
| SGA/PGA 内存 | — | — | ✅ | — | ✅ | — | — | ✅ | — | — |
| Redo 日志 | — | — | ✅ | — | ✅ | — | ✅ | — | — | — |
| 归档与备份 | — | — | ✅ | ✅ | ✅ | — | — | ✅ | — | — |
| 关键参数配置 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 无效对象 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 用户安全审计 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Top SQL / 慢查询 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 主从复制 / Data Guard | ✅ | ✅ | — | — | — | ✅ | ✅ | — | ✅ | — |
| RAC 集群 | — | — | ✅ | — | — | — | — | — | — | — |
| 锁与阻塞检测 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 对象统计信息 | — | — | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| 分区表信息 | — | — | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | — |
| Chunks/磁盘存储 | — | — | — | — | — | — | — | — | — | ✅ |
| 逻辑日志/检查点 | — | — | — | — | — | — | — | — | — | ✅ |


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

---

## 智能风险分析

自动检测各类数据库潜在风险，**每条风险附带可执行修复 SQL，支持一键执行**。

### 风险规则统计

| 数据库 | 规则数 | 覆盖维度 |
|--------|:---:|------|
| MySQL | 35+ | 连接、内存、磁盘、慢查询、锁、安全、复制 |
| PostgreSQL | 27+ | 连接、缓存、性能、安全、归档、死元组 |
| Oracle | 20+ | 表空间、TEMP、会话、SGA、Redo、DG、ASM、安全 |
| SQL Server | 15+ | 连接、会话、等待、锁、死锁、备份、内存 |
| DM8 | 16+ | 表空间、内存池、会话、事务、备份、安全 |
| TiDB | 18+ | 连接、内存、磁盘、慢查询、锁、安全、Placement |
| IvorySQL | 27+ | 与 PostgreSQL 相同 |
| YashanDB | 15+ | 连接、内存、表空间、锁、备份、安全 |
| KingbaseES | 19+ | 连接、缓存、性能、安全、归档、统计信息 |
| GBase 8s | 6+ | 连接、dbspace、日志、内存、密码策略 |

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

Web UI 内置交互式 SQL 编辑器，支持全部 10 种数据库，语法高亮、结果表格、错误友好提示。

### 实时监控

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
- SQL Server：6 项参数（内存、并行度、备份压缩等）
- DM8：7 项参数（内存目标、会话数、缓冲池等）
- TiDB：9 项参数（buffer pool、连接数、并发度等）
- YashanDB：8 项参数（缓冲池、连接、日志等）
- KingbaseES：7 项参数（连接、缓冲、vacuum 等）
- GBase 8s：9 项参数（MAXCONNECTIONS、SHMVIRTSIZE、BUFFERS、LOGSMAX 等）

### 巡检章节管理

可配置驱动，每种数据库可独立添加/删除/排序/启停巡检章节，Word 报告动态生成。

### AWR 报告分析

上传 Oracle AWR HTML 报告，自动解析关键性能指标，生成结构化 Word 分析报告，支持 AI 辅助诊断。

### RAG 知识库

上传 PDF / Word / Markdown / TXT 文档，自动向量化，AI 诊断时自动检索相关知识，生成更精准的建议。

### 多语言与主题

- 支持中文（默认）和英文，CLI 参数和 Web UI 均可切换
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
> DBCheck 始终坚持开源、免费、自由使用。赞助完全出于自愿，仅用于支持项目的长期维护与持续发展。
>
> 如果项目曾帮助过您，欢迎支持；如果您选择不赞助，也完全没有关系。一个 Star、一条建议、一次 Bug 反馈，甚至一句鼓励的话，都是推动项目前进的动力。
>
> 尊重每一种选择，也感谢每一位使用者。

<img src="snapshot/pay.png" alt="赞助二维码" width="800" />

<img src="snapshot/dbcheck-badge.png" alt="DBCheck 支持者徽章" width="400" />

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
