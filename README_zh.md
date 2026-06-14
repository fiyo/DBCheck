# 数据库巡检工具 - DBCheck

![logo](snapshot/dbcheck_logo_info.png)

DBCheck 是一款开源、跨平台的数据库自动化健康巡检工具，支持 MySQL、PostgreSQL、Oracle、SQL Server、达梦 DM8、TiDB、IvorySQL 及 YashanDB 崖山九种主流关系型数据库。工具通过执行预定义的 SQL 检查项与系统资源采集，自动生成格式规范的 Microsoft Word 巡检报告，并提供 SQL 编辑器、远程终端、巡检配置管理、基线配置管理、历史趋势分析、AI 智能诊断、索引健康分析、慢查询深度分析、服务器巡检、分享链接等高级功能。DBCheck 旨在将 DBA 从重复、耗时的手工巡检工作中解放出来，提升数据库运维效率与风险发现能力。
> 官方网站：https://dbcheck.top

> Language: [English](./README.md) | [中文](./README_zh.md)

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)](https://dbcheck.top)
[![Version](https://img.shields.io/badge/版本-v2.5.11-blue.svg)]()
[![License](https://img.shields.io/badge/开源协议-MIT-green.svg)]()
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![AI](https://img.shields.io/badge/AI-Ollama-orange.svg)]()
[![RAG](https://img.shields.io/badge/RAG-知识库增强-red.svg)]()
[![WebUI](https://img.shields.io/badge/WebUI-Flask-success.svg)]()
[![WeChat](https://img.shields.io/badge/公众号-山东Oracle用户组-brightgreen?logo=WeChat)]()
[![WebSite](https://img.shields.io/badge/网址-www.dbcheck.top-green.svg)](https://dbcheck.top)
[![Docker Pulls](https://img.shields.io/docker/pulls/jackge12345/dbcheck?style=flat-square&label=Docker%20下载量)](https://hub.docker.com/r/jackge12345/dbcheck)

---

## 九大核心能力

| 能力 | 说明 |
|------|------|
| 🗄️ 数据源集中管理 | 多数据库实例统一管理，支持分组、批量巡检、连接测试、CSV 导入导出 |
| 📊 历史趋势分析 | 同一数据库多次巡检数据自动汇聚，生成指标趋势折线图，与上次对比发现变化 |
| 🤖 AI 智能诊断 | 基于巡检指标调用本地 Ollama，生成个性化优化建议 |
| 🔍 160+ 条增强规则 | 覆盖九种数据库全维度风险检测（MySQL 35+条 / PG 27+条 / Oracle 20+条 / SQL Server 15+条 / DM8 16+条 / TiDB 18+条 / IvorySQL 27+条 / YashanDB 15+条 / KingbaseES 19+条）—包括 28 条慢查询深度分析规则 |
| 🖥️ 服务器巡检 | 全面检查服务器硬件和系统资源状态，生成专业的服务器巡检报告 |
| 🔗 分享链接 | 一键生成在线分享链接，支持服务器巡检和数据库巡检报告分享 |
| 📡 实时监控 | 跨数据源实时慢查询与活跃连接监控，支持自动刷新、热力图、CSV 导出 |
| 💻 SQL 编辑器 | 内置交互式 SQL 编辑器，直接在 Web UI 中编写、执行、调试 SQL 查询 |
| 🖥️ 远程终端 | 基于 SSH 的远程终端，在浏览器中直接管理数据库服务器 |
| ⚙️ 巡检配置管理 | 灵活管理巡检章节，可自由添加、删除、调整顺序、启用/禁用巡检项目 |
| 📐 基线配置管理 | 可视化基线配置管理，通过 Web UI 自定义推荐值、阈值和合规规则 |

---

## SQL 编辑器

> 内置交互式 SQL 编辑器—直接在 Web UI 中编写、执行、调试 SQL 查询，无需切换工具。

| 特性 | 说明 |
|------|------|
| 多数据库支持 | MySQL / PostgreSQL / Oracle / SQL Server / DM8 / TiDB / IvorySQL / YashanDB / KingbaseES |
| 语法高亮 | SQL 语句彩色编码，提升可读性 |
| 结果网格 | 查询结果以可滚动表格展示，带行号 |
| 执行历史 | 当前会话内保留近期查询记录 |
| 错误反馈 | 数据库错误翻译为友好的可操作提示 |

---

## 远程终端

> 基于 SSH 的远程终端集成到 Web UI—在浏览器中直接管理数据库服务器。

| 特性 | 说明 |
|------|------|
| SSH 连接 | 支持密码和密钥认证 |
| 标签管理 | 多个终端会话分标签管理 |
| 全屏模式 | 展开全屏进行长时间命令行操作 |
| 会话保持 | 页面导航期间保持终端状态 |

---

## 实时监控 📡

> DBCheck 提供跨数据源的实时慢查询和活跃连接监控——无需安装 Agent，纯浏览器端轮询采集，配合专业仪表盘可视化展示。

### 实时慢查询监控

| 特性 | 说明 |
|------|------|
| 多数据源聚合 | 自动聚合所有已配置数据源的 Top SQL，统一展示在同一表格中 |
| 智能时间格式化 | 执行耗时按场景自动格式化：亚秒级显示毫秒、中等显示秒、重度显示分钟 |
| 数据库类型识别 | 根据数据源名称自动识别数据库类型，显示彩色标签（MySQL=蓝、PG=紫、TiDB=橙、Oracle=红、DM8=绿、KingbaseES=蓝） |
| 严重度指示器 | 可视化严重度圆点——🔴 高危（>60s）、🟡 中等（>10s）、🟢 低危——一眼识别风险等级 |
| 排序与筛选 | 支持按平均耗时、最大耗时、总耗时、执行次数排序；可按数据源、时间范围、SQL 关键字筛选 |
| CSV 导出 | 一键导出当前慢查询数据为 CSV 文件，UTF-8 BOM 编码，Excel 直接打开无乱码 |
| 自动刷新 | 可配置轮询间隔（5-60 秒），实时倒计时指示器，支持手动启动/停止 |

### 活跃连接监控

| 特性 | 说明 |
|------|------|
| 连接使用率柱状图 | 可视化展示各数据源当前连接数与最大连接数的对比，按使用率分色（绿色 <50%、黄色 50-80%、红色 >80%） |
| 12 小时热力图 | 按小时聚合的历史连接使用率热力图，5 级色彩编码，快速识别使用规律 |
| 活跃会话 TOP 10 | 实时展示最活跃的 10 个会话，包含用户、状态、持续时间和 SQL 预览，支持按数据源筛选 |
| 阻塞会话检测 | 概览卡片高亮阻塞会话，显示受影响的数据源名称 |
| 空闲连接追踪 | 监控最长空闲连接，以分钟为单位显示持续时间 |
| 自动刷新 | 与慢查询监控共享轮询引擎，一键启停控制两个页面 |

### 使用方式

1. 在 Web UI 侧边栏导航至 **📡 实时监控**
2. 点击页面标题右侧的 **▶ 启动** 按钮开始监控
3. 通过启动/停止按钮旁的数字输入框设置轮询间隔（5-60 秒）
4. 使用筛选条件按数据源、时间范围或 SQL 关键字精确查看
5. 点击 **⏹ 停止** 按钮暂停监控；数据保持展示直到页面刷新

---

## 巡检配置管理

> 灵活的巡检章节管理—可自由定制每种数据库的巡检范围，无需修改代码。巡检引擎已升级为配置驱动：章节根据当前配置自动生成，替代了之前的硬编码方式。

| 特性 | 说明 |
|------|------|
| 章节管理 | 按数据库类型添加、删除、调整顺序、启用/禁用巡检章节 |
| 可视化开关 | 一键启用/禁用单个巡检项 |
| 配置持久化 | 保存到配置文件，重启后保持不变 |
| 动态报告生成 | Word 报告章节根据当前配置生成—仅启用的项目出现在报告中 |

---

## 基线配置管理

> 通过 Web UI 进行可视化基线配置管理—为每种数据库类型自定义推荐值、阈值和合规规则。

与下方的**配置基线合规检查**功能相辅相成。除了内置基线，你现在可以：

| 特性 | 说明 |
|------|------|
| 编辑阈值 | 通过 Web UI 调整推荐值和告警/严重阈值 |
| 添加自定义规则 | 在内置集之外定义新的基线参数 |
| 按库覆盖 | 为不同实例或环境应用不同的基线 |
| 可视化操作 | 所有变更通过 Web UI 完成—无需修改代码 |

---

## 服务器巡检 🖥️

> 全面检查服务器运行状态，包括 CPU、内存、磁盘、网络、服务等关键指标，生成专业的服务器巡检报告。

### 功能概述

服务器巡检功能可以独立于数据库巡检运行，专注于服务器硬件和系统资源的健康检查：

- **CPU 监控**：使用率、核心数、频率、负载均衡
- **内存分析**：总量、使用量、可用量、使用率、Swap 状态
- **磁盘检查**：各挂载点容量、使用率、I/O 性能
- **网络监控**：网络接口状态、流量统计、连接数
- **服务状态**：关键服务运行状态检测
- **进程分析**：Top 进程资源占用排名

### Web UI 操作

在 Web UI 的 **🖥️ 服务器巡检** 页面：

| 功能 | 说明 |
|------|------|
| 一键巡检 | 选择目标服务器，一键执行全面巡检 |
| 实时进度 | SSE 推送巡检进度，实时查看检测结果 |
| 报告预览 | 巡检完成后在线预览服务器状态报告 |
| 历史记录 | 查看历史服务器巡检报告列表 |
| 分享链接 | 生成在线分享链接，方便团队协作 |

### 报告内容

服务器巡检报告包含以下章节：

| 章节 | 内容 |
|------|------|
| 基本信息 | 服务器名称、IP 地址、操作系统、运行时间 |
| CPU 状态 | 使用率、核心数、频率、负载情况 |
| 内存状态 | 总量、使用量、可用量、Swap 使用情况 |
| 磁盘状态 | 各分区容量、使用率、I/O 性能指标 |
| 网络状态 | 网络接口、流量统计、连接数 |
| 服务状态 | 关键服务运行状态 |
| 进程分析 | Top 进程资源占用排名 |
| 综合评分 | 基于各项指标的综合健康评分 |

---

## 分享链接功能 🔗

> 一键生成在线分享链接，无需登录即可查看巡检报告，支持服务器巡检和数据库巡检报告分享。

### 功能特点

| 特性 | 说明 |
|------|------|
| 在线查看 | 通过链接直接在浏览器中查看完整报告 |
| 权限隔离 | 分享链接只能查看当前报告，无其他页面访问权限 |
| 访问统计 | 自动记录链接访问次数 |
| 随时删除 | 支持删除已分享的链接，立即失效 |
| 双语支持 | 分享页面自动适配中英文界面 |

### 使用场景

- **团队协作**：将巡检报告分享给团队成员，无需传递文件
- **领导汇报**：生成链接发送给领导审阅，无需安装软件
- **问题讨论**：在会议中直接打开链接讨论巡检发现的问题
- **归档备忘**：保存链接作为历史记录，随时可查

### Web UI 操作

#### 分享报告

1. 在 **📋 巡检历史** 或 **🖥️ 服务器巡检历史** 页面
2. 点击报告对应的 **🔗 分享** 按钮
3. 系统自动生成分享链接
4. 复制链接发送给需要查看的人

#### 管理分享链接

在 **🔗 分享管理** 页面：

| 功能 | 说明 |
|------|------|
| 查看列表 | 显示所有已分享的链接，包含标题、类型、访问次数 |
| 复制链接 | 一键复制分享链接到剪贴板 |
| 删除链接 | 删除分享链接，链接立即失效 |
| 访问统计 | 查看每个链接的访问次数 |

### 分享链接格式

```
http://localhost:5003/share/{share_id}
```

- `share_id`：12 位唯一标识符，自动生成
- 链接无需登录即可访问
- 只能查看分享的报告，无法访问其他功能页面


### API 接口

| 接口 | 方法 |
|------|------|
| `/api/server_inspect_share` | POST |
| `/api/db_inspect_share` | POST |
| `/share/<share_id>` | GET |
| `/api/share/<share_id>` | GET |
| `/api/share/<share_id>` | DELETE |
| `/api/shares` | GET |

---
## 🌍 多语言支持

DBCheck 支持**中文（默认）**和**英文**两种语言，界面文本随语言切换自动更新。

### 命令行语言切换

```bash
python main.py                    # 默认中文
python main.py --lang en         # 切换为英文
python main.py --lang zh         # 切换为中文（显式指定）
```

> Web UI 右上角也有 🌐 切换按钮，点击即可中英文切换，切换结果自动保存。

### 语言说明

| 参数 | 语言 |
|------|------|
| `--lang zh` | 中文 |
| `--lang en` | English |
| 不指定 | 中文 |

> **注意**：`--lang` 参数仅在当前会话临时生效，不会覆盖已保存的语言设置。Web UI 中切换语言会持久化到 `dbc_config.json`，下次启动 Web UI 时自动加载。

### 手动修改默认语言

如需在不启动程序的情况下修改默认语言，可直接编辑配置文件：

```json
// dbc_config.json
{
    "language": "zh"   // "zh" = 中文, "en" = English
}
```

配置文件位于 `main.py` 同级目录下。

---

## 🌓 主题切换

DBCheck Web UI 支持**暗色**和**亮色**两种主题。点击顶栏 ☀️/🌙 按钮即可即时切换—偏好会保存在浏览器中，下次访问自动恢复。

| 特性 | 说明 |
|------|------|
| 默认主题 | 暗色（GitHub 风格配色） |
| 亮色主题 | 高对比度浅色变体，适合明亮环境 |
| 持久化 | 存储在浏览器 `localStorage`，页面刷新不丢失 |
| 零配置 | 无需 CLI 参数或修改配置文件 |

## AI 辅助 · 问题发现即处理

### 🤖 AI 智能诊断

调用本地 **Ollama**（完全离线），基于当次巡检的指标数据（连接数、缓存命中率、慢查询数、安全风险等），自动生成结构化的优化建议。报告独立成章，Markdown 格式内容自动渲染为 Word 样式（加粗、代码块、列表、标题序号），方便直接转发给团队或领导审阅。

| 后端 | 特点 |
|------|------|---------|
| `ollama` | 本地运行，零成本，无网络依赖 |
| `openai` | 云端 API（兼容 OpenAI / DeepSeek），需要网络和 API Key |
| `disabled` | 不调用 AI（默认） |

> **在线模型开关**：在 Web UI 的 **AI 诊断设置** 页面勾选"启用在线模型"即可解锁云端后端（OpenAI、DeepSeek 等）。默认关闭时仅允许本地 Ollama，巡检数据不出本机。
>
> ⚠️ **安全说明**：在线模式关闭时，AI 诊断仅支持本地 Ollama（localhost:11434）。开启在线模式后，数据将通过网络发送至配置的云端 API—请确认您的 API 服务商隐私政策符合要求。

### 🔍 风险与建议

每条风险对应一张卡片，包含：**风险等级（高/中/低）→ 问题描述 → 修复 SQL（可直接复制执行）→ 优先级与负责人**。报告自动汇总，一眼看清全部待处理项。

---

## 九种使用方式


| 方式 | 说明 |
|------|------|
| 🖥️ 命令行 | `python main.py`，终端交互，适合熟悉命令行的用户 |
| 🌐 Web UI | `python web_ui.py`，浏览器图形界面，支持趋势图和 AI 诊断配置 |
| 💬 AI 对话巡检 | 打开 Web UI 右下角 AI 面板，自然语言发起巡检，零操作 |
| 🤖 OpenClaw Skill | 告诉 AI 助手"帮我巡检 XX 库"，零操作自动完成 |
| 📦 打包部署 | PyInstaller 打包成分发版，给团队成员使用 |
| 🔗 分享链接 | 一键生成在线分享链接，无需登录即可查看巡检报告 |

## 功能特性

### 数据库巡检

> 支持九种主流关系型数据库的全面巡检，覆盖 160+ 条增强规则（含 IvorySQL 27+ 条、YashanDB 15+ 条）。


| 维度 | MySQL | PostgreSQL | Oracle | SQL Server | DM8 | TiDB | IvorySQL | YashanDB | KingbaseES |
|------|:-----:|:----------:|:------:|:-----------:|:---:|:----:|:---------:|:----:|:----:|
| 基本信息（版本/实例/数据库） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 会话与连接状态 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 内存与缓存配置 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 表空间使用情况 | --- | --- | ✅ | ✅ | ✅ | --- | --- | ✅ |
| SGA / PGA 内存分析 | --- | --- | ✅ | --- | ✅ | --- | --- | ✅ |
| Redo 日志与状态 | --- | --- | ✅ | --- | ✅ | --- | ✅ | --- |
| 归档与备份检查 | --- | --- | ✅ | ✅ | ✅ | --- | --- | ✅ |
| 关键参数配置 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 无效对象检测 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 用户安全审计 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Top SQL / 慢查询 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 主从复制 / Data Guard | ✅ | ✅ | --- | --- | --- | ✅ | ✅ | --- |
| RAC 集群信息 | --- | --- | ✅ | --- | --- | --- | --- | --- |
| ASM 磁盘组 | --- | --- | ✅ | --- | --- | --- | --- | --- |
| Undo 表空间管理 | --- | --- | ✅ | --- | ✅ | --- | --- | ✅ |
| 回收站 / 闪回恢复区 | --- | --- | ✅ | --- | ✅ | --- | ✅ | --- |
| Profile 密码策略 | --- | --- | ✅ | --- | --- | --- | --- | --- |
| 等待事件 TOP | --- | --- | ✅ | ✅ | ✅ | --- | ✅ | --- |
| 锁与阻塞检测 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 统计信息陈旧检测 | --- | --- | ✅ | ✅ | ✅ | ✅ | ✅ | --- |
| 分区表信息 | --- | --- | ✅ | ✅ | ✅ | ✅ | ✅ | --- |
| 数据文件状态 | --- | --- | ✅ | ✅ | ✅ | --- | ✅ | --- |
| DM8 缓冲池详情 | --- | --- | --- | --- | ✅ | --- | --- | --- |
| 调度与亲和性策略 | --- | --- | --- | --- | --- | ✅ | --- | --- |

### 服务器巡检

> 全面检查服务器硬件和系统资源状态，生成专业的服务器巡检报告。

| 检查维度 | 说明 |
|----------|------|
| CPU | 使用率、核心数、频率、负载情况 |
| 内存 | 总量、使用量、可用量、使用率、Swap 状态 |
| 磁盘 | 各挂载点容量、使用率、I/O 性能 |
| 网络 | 网络接口状态、流量统计、连接数 |
| 服务 | 关键服务运行状态检测 |
| 进程 | Top 进程资源占用排名 |
| 综合评分 | 基于各项指标的综合健康评分 |

### 分享链接

> 一键生成在线分享链接，支持服务器巡检和数据库巡检报告分享。

| 功能 | 说明 |
|------|------|
| 在线查看 | 通过链接直接在浏览器中查看完整报告 |
| 权限隔离 | 分享链接只能查看当前报告，无其他页面访问权限 |
| 访问统计 | 自动记录链接访问次数 |
| 随时删除 | 支持删除已分享的链接，立即失效 |

### 数据源管理 🗄️

> 统一管理所有数据库实例，支持分组管理、批量巡检、连接测试，大幅提升运维效率。

#### 核心功能

| 功能 | 说明 |
|------|------|
| 多数据库支持 | MySQL / PostgreSQL / Oracle / SQL Server / DM8 / TiDB / IvorySQL / YashanDB / KingbaseES |
| 实例信息 | 支持自定义标签、分组、端口、用户名 |
| Oracle 专属 | 服务名/SID 配置、SYSDBA 特权连接 |
| 连接测试 | 一键测试数据库连接，实时返回结果 |
| 分组管理 | 按业务/环境分组，支持自定义颜色标签 |
| CSV 导入导出 | 批量导入/导出数据源配置 |

#### Web UI 数据源管理

| 功能 | 说明 |
|------|------|
| 添加数据源 | 选择数据库类型 → 填写连接信息 → 保存 |
| 编辑数据源 | 修改任意字段，密码可选择性更新 |
| 删除数据源 | 支持二次确认，防止误删 |
| 连接测试 | 对已保存的数据源进行连接验证 |
| 分组筛选 | 按分组过滤显示，快速定位目标实例 |
| 批量导入 | CSV 文件批量导入数据源 |
| 批量导出 | 一键导出所有数据源为 CSV |

#### 分组管理

| 功能 | 说明 |
|------|------|
| 创建分组 | 支持自定义名称和颜色 |
| 编辑分组 | 修改名称和颜色 |
| 删除分组 | 非 default 分组可删除 |
| 颜色标签 | 每个分组可设置不同颜色，便于区分 |

### 配置基线检查

> 根据数据库规模、内存和负载自动计算推荐配置值，与当前值对比，识别配置偏差。

#### MySQL（22 个参数）

| 参数 | 说明 |
|------|------|
| innodb_buffer_pool_size | InnoDB 缓冲池大小 |
| max_connections | 最大连接数 |
| tmp_table_size / max_heap_table_size | 临时表/内存表大小 |
| innodb_log_file_size | Redo 日志文件大小 |
| innodb_log_buffer_size | 日志缓冲区大小 |
| sync_binlog | Binlog 同步频率 |
| innodb_flush_log_at_trx_commit | 事务提交日志刷盘策略 |
| table_open_cache / table_definition_cache | 表缓存/表定义缓存 |
| thread_cache_size | 线程缓存大小 |
| innodb_thread_concurrency | InnoDB 线程并发数 |
| innodb_io_capacity / io_capacity_max | I/O 容量（SSD/HDD） |
| max_allowed_packet | 最大数据包大小 |
| wait_timeout / interactive_timeout | 空闲连接超时 |
| sort_buffer_size / join_buffer_size | 排序/join 缓冲区 |
| long_query_time | 慢查询阈值 |

#### PostgreSQL（21 个参数）

| 参数 | 说明 |
|------|------|
| shared_buffers | 共享缓冲区大小 |
| effective_cache_size | 有效缓存大小 |
| maintenance_work_mem | 维护操作内存 |
| work_mem | 工作内存 |
| max_connections | 最大连接数 |
| temp_buffers / wal_buffers | 临时/WAL 缓冲区 |
| checkpoint_completion_target | 检查点完成目标 |
| max_wal_size / min_wal_size | WAL 大小边界 |
| random_page_cost | 随机页成本 |
| effective_io_concurrency | 有效 I/O 并发数 |
| shared_preload_libraries | 预加载库 |
| track_activities / track_counts | 统计追踪 |
| track_io_timing / track_functions | I/O/函数追踪 |
| autovacuum | 自动清理 |
| log_min_duration_statement | 慢查询日志阈值 |

#### Oracle（12 个参数）

| 参数 | 说明 |
|------|------|
| memory_target | 内存目标（SGA+PGA） |
| sga_target / pga_aggregate_target | SGA / PGA 目标 |
| processes | 最大进程数 |
| open_cursors / session_cached_cursors | 游标设置 |
| log_buffer | 日志缓冲区大小 |
| undo_retention | Undo 保留时间 |
| fast_start_mttr_target | MTTR 目标 |
| db_file_multiblock_read_count | 多块读计数 |
| statistics_level | 统计级别 |
| control_file_record_keep_time | 控制文件记录保留天数 |

#### SQL Server（6 个参数）

| 参数 | 说明 |
|------|------|
| max server memory (MB) | 最大服务器内存 |
| cost threshold for parallelism | 并行开销阈值 |
| max degree of parallelism | 最大并行度 |
| fill factor (%) | 填充因子 |
| recovery interval (min) | 恢复间隔 |
| backup compression default | 备份压缩默认 |

#### DM8（7 个参数）

| 参数 | 说明 |
|------|------|
| MEMORY_TARGET | 内存目标 |
| SGA_TARGET / PGA_TARGET | SGA / PGA 目标 |
| MAX_SESSIONS / OPEN_CURSORS | 会话/游标限制 |
| UNDO_RETENTION | Undo 保留时间 |
| BUFFER | 缓冲池大小 |

#### TiDB（9 个参数）

| 参数 | 说明 |
|------|------|
| innodb_buffer_pool_size | InnoDB 缓冲池大小 |
| max_connections | 最大连接数 |
| tmp_table_size / max_heap_table_size | 临时/内存表大小 |
| innodb_log_file_size / innodb_log_buffer_size | 日志文件/缓冲区 |
| max_allowed_packet | 最大包大小 |
| tidb_hash_join_concurrency / tidb_index_lookup_concurrency | 算子并发数 |

#### YashanDB（8 个参数）

| 参数 | 说明 |
|------|------|
| BUFFER_POOL_SIZE | 缓冲池大小 |
| MAX_CONNECTIONS | 最大连接数 |
| SORT_BUF_SIZE | 排序缓冲区大小 |
| HASH_AREA_SIZE | Hash 区域大小 |
| OPTIMIZER_MODE | 优化器模式 |
| LOG_BUF_SIZE | 日志缓冲区大小 |
| ARCHIVE_LOG_MODE | 归档日志模式 |
| COMPATIBLE_MODE | 兼容模式 |

#### KingbaseES（7 个参数）

| 参数 | 说明 | 推荐值依据 |
|-----------|-------------|------------------------|
| max_connections | 最大连接数 | 200–1000 |
| shared_buffers | 共享缓冲区大小 | 内存的 25% |
| effective_cache_size | 有效缓存大小 | 内存的 75% |
| maintenance_work_mem | 维护内存 | 256MB–1GB |
| work_mem | 工作内存 | (总内存 × 0.25) / max_connections |
| autovacuum | 自动清理 | on |
| log_min_duration_statement | 慢查询阈值 | 1000–3000ms |

### 索引健康分析

> 对所有支持的数据库进行三类索引问题检测—缺失索引、冗余/重复索引、长期未使用索引，并生成可操作的修复建议。

#### MySQL

| 分析类型 | 数据来源 |
|---------|---------|
| 缺失索引 | performance_schema.events_statements_summary_by_digest + table_statistics |
| 冗余索引 | information_schema.STATISTICS |
| 未使用索引 | performance_schema.table_statistics |

#### PostgreSQL

| 分析类型 | 数据来源 |
|---------|---------|
| 缺失索引 | pg_stat_statements |
| 冗余索引 | pg_indexes（indexdef 解析） |
| 未使用索引 | pg_stat_user_indexes（idx_scan=0） |

#### Oracle

| 分析类型 | 数据来源 |
|---------|---------|
| 未使用索引 | v$object_usage（MONITORING USAGE） |
| 冗余索引 | dba_ind_columns |

#### SQL Server

| 分析类型 | 数据来源 |
|---------|---------|
| 未使用索引 | sys.dm_db_index_usage_stats |
| 冗余索引 | sys.indexes + sys.index_columns |

#### DM8

| 分析类型 | 数据来源 |
|---------|---------|
| 冗余索引 | USER_IND_COLUMNS |

#### TiDB

| 分析类型 | 数据来源 |
|---------|---------|
| 冗余索引 | information_schema.STATISTICS |

#### YashanDB

| 分析类型 | 数据来源 |
|---------|---------|
| 冗余索引 | DBA_IND_COLUMNS |

### 系统资源监控

- **CPU**：使用率、核心数、频率
- **内存**：总量、使用量、可用量、使用率
- **磁盘**：各挂载点容量及使用率
- **采集方式**：本地直采或 SSH 远程采集（支持密码/密钥认证，默认端口 22）；达梦 DM8 支持 SSH 采集（失败时自动降级为本地采集器）

### 问题列表

巡检完成后，Web UI 提供直观的问题列表视图：

| 功能 | 说明 |
|------|------|
| 📊 风险统计 | 自动统计高/中/低风险项数量，用颜色区分 |
| 🔍 问题详情 | 每条问题显示等级、描述、修复建议 |
| 📋 一键复制 | 修复 SQL 可一键复制到剪贴板 |
| ▶ 在线修复 | 点击"执行修复"直接执行（高危操作需二次确认） |
| 🌐 双语支持 | 中英文界面自动切换 |

**历史报告问题列表：**

| 功能 | 说明 |
|------|------|
| 📁 历史查看 | 点击历史报告的"问题列表"按钮查看 |
| 📊 趋势对比 | 查看多次巡检的问题变化 |
| 🔗 一键跳转 | 从问题直接跳转到相关指标详情 |

### 智能风险分析

自动检测数据库潜在风险，**每条风险附带可执行的修复 SQL，支持一键修复**：


#### KingbaseES

| 分析类型 | 数据来源 |
|---------|---------|
| 缺失索引 | pg_stat_statements |
| 冗余索引 | pg_indexes（indexdef 解析） |
| 未使用索引 | pg_stat_user_indexes（idx_scan=0） |

#### 一键修复 🔧

> 告别手动复制粘贴 SQL，直接在 Web UI 中点击执行修复脚本。

| 功能 | 说明 |
|------|------|
| 一键执行 | 巡检报告中的每条风险附带「执行修复」按钮，点击即可直接执行 |
| 危险 SQL 二次确认 | DELETE、DROP、TRUNCATE 等高危操作执行前自动弹出确认框 |
| 多数据库支持 | MySQL / PostgreSQL / Oracle / SQL Server / DM8 / TiDB / IvorySQL / YashanDB / KingbaseES |
| 执行日志 | 所有修复操作记录到日志，支持审计回溯 |
| 错误友好提示 | 常见数据库错误自动翻译为中文友好提示 |

执行流程：
```
风险发现 → 查看修复 SQL → 点击「执行修复」→ 危险操作二次确认 → 一键执行 → 执行结果反馈
```

#### MySQL（18+ 条规则）

| 维度 | 说明 |
|------|---------|
| 连接数 | 使用率 >90% 高危 / >80% 中危 |
| 内存 | InnoDB 缓冲池偏小（<数据总量 60%） |
| 磁盘 | 使用率 >85% 警告 / >95% 高危 |
| 查询 | 长时间运行 SQL（>60s）、慢查询日志未开启 |
| 锁 | 锁等待比例偏高 |
| 安全 | 用户空密码、root@% 暴露、字符集非 UTF8 |
| 复制 | 主从延迟 >30s、复制状态异常 |
| 其他 | binlog 未开启、查询缓存残留、异常中止连接过多 |

#### PostgreSQL（16+ 条规则）

| 维度 | 说明 |
|------|---------|
| 连接 | 连接数接近上限、超级用户过多 |
| 缓存 | 缓存命中率偏低（<80%）、shared_buffers 偏小 |
| 性能 | dead tuples 大量累积、长时间运行 SQL |
| 安全 | 公开 schema 权限过宽 |
| 归档 | 归档模式未开启 |
| 其他 | 磁盘/内存/CPU 资源告警 |

#### Oracle（20+ 条规则）

| 维度 | 说明 |
|------|---------|
| 表空间 | 使用率 >90%（含自动扩展计算） |
| TEMP | 临时表空间使用率偏高 |
| 会话 | 数接近上限 / 进程超限 / 锁阻塞 |
| 内存 | SGA 占物理内存比例过高 |
| Redo | Redo 日志组异常 / 切换频繁 |
| 备份 | 归档模式未开启 / RMAN 备份缺失 |
| DG | MRP 未运行 / 保护模式偏低 |
| ASM | 磁盘组空间不足 / 离线磁盘 |
| FRA | 闪回恢复区使用率偏高 |
| 对象 | 无效对象过多 / 统计信息陈旧 |
| 安全 | Profile 密码策略宽松 / 审计未开启 |
| 其他 | open_cursors 偏小 / 回收站占用 / 数据文件脱机 |

#### DM8（16+ 条规则）

| 维度 | 说明 |
|------|------|
| 表空间 | 使用率 >90%（含自动扩展计算） |
| 内存 | 各缓冲池（KEEP/RECYCLE/FAST/NORMAL/ROLL）配置异常 |
| 会话 | 连接数接近上限 / 长时间运行会话 |
| 事务 | 阻塞事务检测 / 事务等待 |
| 备份 | 备份集缺失 / 备份超时 |
| 参数 | 关键参数（INSTANCE_MODE, COMPATIBLE_VERSION 等）配置检查 |
| 安全 | 用户空密码 / 权限过宽 / 审计未开启 |
| 对象 | 无效对象 / 统计信息陈旧 / 分区表信息 |
| 归档 | 归档模式未开启 / 归档日志堆积 |

#### SQL Server（15+ 条规则）

| 维度 | 说明 |
|------|------|
| 连接数 | 当前连接数接近最大连接数上限 |
| 会话 | 活动会话数异常 / 长时间运行会话 |
| 等待 | 等待统计 TOP10 / 等待类型分析 |
| 锁 | 当前锁信息 / 锁等待与阻塞链 |
| 死锁 | 死锁历史检测 / 阻塞进程分析 |
| 备份 | 最近备份缺失 / 备份类型检查 |
| 数据库 | 数据库状态 / 恢复模式 / 文件大小 |
| 内存 | 内存 clerk 使用 / 缓冲池命中率 |
| 性能 | Top SQL 按 CPU/IO/执行时间排序 |

#### TiDB（18+ 条规则）

| 维度 | 说明 |
|------|------|
| 连接数 | 使用率 >90% 高危 / >80% 中危 |
| 内存 | TiDB 内存配置异常 |
| 磁盘 | 使用率 >85% 警告 / >95% 高危 |
| 查询 | 长时间运行 SQL（>60s）、慢查询日志未开启 |
| 锁 | 锁等待事件 / 死锁检测 |
| 安全 | 用户空密码、root@% 暴露、字符集非 UTF8 |
| 复制 | TiCDC/PD 心跳异常 / Follower 延迟 |
| 调度 | Placement Rules 配置异常 / 亲和性策略 |
| 统计 | 统计信息陈旧 / 自动分析未开启 |
| 其他 | binlog 未开启、异常中止连接过多、系统 CPU/内存压力 |

### 🔒 P0 锁诊断增强（v2.4.4）

> 深度锁分析，结构化报告输出—阻塞链可视化、死锁统计、长事务检测、可执行的修复脚本—现已覆盖全部九种数据库引擎。

#### 诊断维度

| 章节 | 关注点 |
|------|--------|
| **4.1 锁阻塞链** | 识别多层等待链中的根阻塞源 |
| **4.2 死锁统计** | 累计死锁次数与 trace 分析 |
| **4.3 长事务检测** | 检测超过 60 秒阈值的事务 |

#### 各数据库覆盖

| 数据库 | 新增 SQL 模板 | 锁分析维度 | 因果模板 |
|----------|:-------------------:|--------------------------|:----------------:|
| **MySQL** | +4（InnoDB 锁链 / 死锁 / 长事务 / 锁类型统计） | 5-维度（阻塞源、被阻塞、锁类型、持续时间、对象） | 6 |
| **PostgreSQL** | +4（阻塞链 / 死锁 / 长事务 / 锁类型分布） | 5-维度（阻塞PID、被阻塞PID、锁模式、持续时间、关系） | 5 |
| **Oracle** | +3（v$lock×v$session 联查阻塞会话 / 死锁统计 / 长事务） | 5-维度（SID、SERIAL#、锁类型、对象、秒数） | 5 |
| **DM8** | +3（V$LOCK 分析 / V$TRXWAIT 链 / V$TRX 长事务） | 5-维度（TRX_ID、LTYPE、LMODE、BLOCKED、持续时间） | 5 |
| **SQL Server** | 原生集成（sys.dm_exec_requests 阻塞链 / deadlock_xml / 长运行会话） | 5-维度 | 4 |
| **TiDB** | 原生集成（CLUSTER_PROCESSLIST / DEADLOCKS / TIDB_TRX） | 5-维度 | 4 |
| **IvorySQL** | +4（基于 PG 协议，复用 PG 锁分析逻辑） | 5-维度（同 PostgreSQL） | 5 |
| **YashanDB** | +3（V$LOCK 分析 / V$TRXWAIT 链 / V$TRX 长事务） | 5-维度（同 Oracle） | 5 |
| **KingbaseES** | +4（基于 PG 协议，复用 PG 锁分析逻辑） | 5-维度（同 PostgreSQL） | 5 |



> **说明**：SQL Server 和 TiDB 的锁诊断通过系统 DMV / Cluster 表原生集成，无需额外 SQL 模板。全部九种引擎均在 Word 报告中输出结构化锁分析章节。

### 历史趋势分析 📊

> 多次巡检同一个数据库，自动汇聚指标数据，生成趋势图，发现悄然发生的变化。

- 每次巡检后，关键指标（内存使用率、连接数、QPS、CPU 等）自动写入本地 **SQLite 数据库**（`db_history.db`），进程重启后历史记录不丢失
- 同一数据库（IP + 端口 + 类型）多次巡检数据聚合保留，每个实例最多 30 条历史快照
- SQLite 存储封装在 `SQLiteHistoryManager` 中；原有 `HistoryManager` API 完全保留，调用方无需任何改动
- 自动降级：SQLite 不可用时（权限、文件锁定等）自动回退到内存模式，不阻塞巡检流程
- Web UI 提供**趋势分析页面**，绘制指标折线图，带警戒线标注
- 与上次巡检逐项对比：变化量带颜色箭头（↑ 变差 / ↓ 好转）

### AI 智能诊断 🤖

> 基于巡检数据，调用本地 Ollama 大模型生成个性化优化建议，从"发现问题"升级到"解决问题"。

AI 诊断与智能分析的关系：

|  | 智能分析 |
| --- | --- |
| 原理 | 固定规则，离线判断 |
| 速度 | 毫秒级 |
| 结果 | 确定性结论 + 修复 SQL |
| 调用 | 每次巡检自动执行 |

**AI 后端配置（Web UI 可视化设置）：**

| 参数 | 说明 |
|------|------|
| 在线模型 | 复选框—勾选后可使用云端 API（OpenAI / DeepSeek）；默认关闭 |
| 后端类型 | `ollama`、`openai` 或 `disabled` |
| API 地址 | Ollama: `http://localhost:11434`；OpenAI: `https://api.openai.com/v1` |
| API Key | `openai` 后端必须（OpenAI / DeepSeek 密钥） |
| 模型名称 | 如 `qwen3:30b`（Ollama）、`gpt-4o-mini`（OpenAI）、`deepseek-chat`（DeepSeek） |
| 超时时间 | 默认 600 秒（大模型冷启动较慢） |

> ⚠️ 在线模式关闭时，非 localhost 的 API 地址会被代码自动拒绝，防止敏感数据外传。

## AI 对话巡检 💬

DBCheck 支持自然语言交互，无需手动操作即可发起巡检。

打开右下角 **AI 助手面板**，直接输入：

```
巡检 MySQL-主库
对 Oracle 做完整巡检
查看 PostgreSQL 的锁等待
```

系统会自动：

- 解析意图，自动匹配数据源
- 启动巡检任务，实时显示简洁动画进度
- 完成后推送 Word 报告下载链接

<p align="center">
  <img src="snapshot/chat_demo.png" width="600" alt="AI Chat Demo">
</p>

---

### 慢查询深度分析 🔍

> 不仅检测慢查询，DBCheck 还从执行计划、I/O 模式、锁等待、临时表使用等多个维度进行深度剖析，并将结果注入 AI 诊断，生成精准的根因分析优化建议。

#### 功能概述

当数据库出现慢查询症状时，DBCheck 会跨多个性能维度采集 Top N 最差性能语句，执行自动化风险规则分析，然后调用 AI advisor 生成针对性的优化建议。

#### 各数据库采集维度

每种数据库都有针对其性能模型优化的查询语句：

| 数据库 | 数据来源 |
|--------|----------|
| **MySQL** | `performance_schema.events_statements_summary_by_digest` |
| **PostgreSQL** | `pg_stat_statements` |
| **Oracle** | `v$sql` |
| **SQL Server** | `sys.dm_exec_query_stats` |
| **DM8** | `V$SQL` |
| **TiDB** | `information_schema.cluster_slow_query` |
| **IvorySQL** | `pg_stat_statements`（复用 PG 采集逻辑） |
| **YashanDB** | `V$SQL`（Oracle 兼容） |
| **KingbaseES** | `pg_stat_statements`（PG 兼容） |


#### 与巡检流程的集成

```
checkdb() 执行顺序：
1. getData() → 执行 SQL 巡检查询
2. checkdb() → 智能风险分析
3. 慢查询深度分析 ← 新增（AI 诊断之后自动执行）
4. context['slow_query_result'] → smart_analyze_* 执行风险规则评估
5. AI Advisor → 注入 slow_query_top3 + slow_query_count 指标
```

`SlowQueryResult` 标准化容器统一了各数据库分析器的输出格式，确保下游处理逻辑与数据库类型无关。

#### 增强的风险规则

巡检引擎新增了针对慢查询的数据库特定规则：

**MySQL（新增规则 17+ 条）：**
- `performance_schema` 未开启检测
- 全表扫描语句检测
- 锁等待比例阈值检测
- AI 诊断注入慢查询发现结果

**PostgreSQL（新增规则 11+ 条）：**
- `pg_stat_statements` 扩展未开启检测
- 高延迟语句检测
- 高 I/O 语句检测
- 长查询阈值检测
- AI 诊断注入慢查询发现结果

#### AI 诊断增强

`build_slow_query_ai_prompt()` 函数生成专项诊断 Prompt，AI advisor 接收到：

- **slow_query_top3**：影响最大的三条慢查询（按延迟/I/O/执行频率排序）
- **slow_query_count**：采集到的慢查询总数

使 AI 能够提供精确到单条语句的优化建议，而非泛泛而谈。

#### 报告中的呈现

慢查询分析结果以风险卡片形式出现在报告的风险建议章节，每条风险标注严重等级（🔴 高危 / 🟡 中危 / 🟢 低危），并附带可直接执行的修复 SQL。

---

## 定时巡检与自动通知 ⏰📧🔔

> 通过 Cron 表达式配置周期巡检任务，DBCheck 自动执行并在完成后即时向团队推送邮件报告或 Webhook 告警。

### 定时调度

Web UI 提供独立的 **⏰ 定时巡检** 页面：

| 功能 | 说明 |
|------|------|
| Cron 表达式 | 自由配置秒/分/时/日/月/周 |
| 快捷预设 | 每天凌晨2点 / 工作日上午9点 / 每周一9点 / 每月1号3点 |
| 单任务通知开关 | 每个任务独立控制完成后是否发送通知 |
| 一键立即执行 | 不等待定时器，随时手动触发任务 |
| 持久化任务配置 | 任务保存到 `scheduler_jobs.json`，服务重启后自动恢复 |

### 通知推送

Web UI 提供独立的 **📧🔔 通知设置** 页面：

#### 邮件报告（SMTP）

| 功能 | 说明 |
|------|------|
| SMTP 服务器 | 支持 126、163、QQ、企业邮箱等 |
| 端口 | 465（SSL 隐式）或 587（TLS 明文） |
| TLS | 根据端口自动检测，可手动配置 |
| 收件人 | 支持多个，逗号分隔 |
| 触发时机 | 每次定时巡检成功后自动发送 |
| 附件 | Word 巡检报告作为附件直接发送 |

#### Webhook 告警

| 功能 | 说明 |
|------|------|
| 类型 | 企业微信（Markdown）、钉钉（Markdown + @）、自定义 JSON |
| 触发时机 | 每次定时巡检完成后发送，成功失败均推送 |
| 失败告警 | 包含错误信息，成功时附带报告文件 |
| 自定义模板 | 支持变量：`{label}`、`{db_type}`、`{status}`、`{error}`、`{report_file}` |

#### 环境变量覆盖

敏感信息可通过环境变量注入，无需写入配置文件：

| 变量 | 说明 |
|------|------|
| `SMTP_HOST` | SMTP 服务器地址 |
| `SMTP_PORT` | SMTP 端口 |
| `SMTP_USER` | 用户名 / 邮箱 |
| `SMTP_PASSWORD` | 密码或授权码 |
| `SMTP_USE_TLS` | 启用 TLS（`true`/`false`） |
| `SMTP_FROM_NAME` | 发件人显示名称 |
| `WEBHOOK_URL` | Webhook 地址 |
| `WEBHOOK_TYPE` | `wecom` / `dingtalk` / `custom` |

> 环境变量优先于 `notifier_config.json`，适合安全部署场景，无需在磁盘存储明文凭证。

---

## RAG 知识库 📚

> 上传数据库官方文档和运维手册，DBCheck 自动将内容向量化，AI 诊断时自动检索相关知识，生成更精准的优化建议。

### 功能概述

RAG 知识库让 AI 诊断能够参考你自己的文档资料：

- **上传文档**：支持 PDF、Word（.docx）、Markdown（.md）、TXT、HTML 等格式
- **智能分块**：按段落语义分割，可配置分块大小和重叠字符数
- **向量存储**：基于 SQLite 的向量库，查询全本地运行
- **Ollama 集成**：使用 `nomic-embed-text` 生成向量嵌入
- **AI 增强诊断**：AI 诊断时自动检索知识库相关内容，注入 Prompt 提升诊断准确率

### 支持的文档格式

| 格式 | 扩展名 |
|------|--------|
| PDF | `.pdf` |
| Word | `.docx` |
| Markdown | `.md` |
| 文本 | `.txt` |
| HTML | `.html` / `.htm` |

### 工作原理

```
文档上传 → 语义分块 → Ollama 向量化 → 向量存储
                                    ↓
        AI 诊断 ← 知识检索 ← Top-K 相似度搜索
```

### Web UI 集成

Web UI 中的 **📚 RAG 知识库** 页面提供：

- **Ollama 状态检测**：页面加载时自动检测连接状态
- **上传文档**：选择文件 + 数据库类型 + 标题，上传后自动分块并向量化
- **文档列表**：展示已上传文档（标题、数据库类型、文件大小、分块数、状态）
- **删除文档**：一键删除，使用 toast 确认弹窗

### API 接口

| 接口 | 方法 |
|------|------|
| `/api/rag/documents` | GET |
| `/api/rag/documents` | POST |
| `/api/rag/documents/<doc_id>` | DELETE |
| `/api/rag/ollama-status` | GET |

### 快速上手

1. **拉取 Ollama Embedding 模型**
   ```bash
   ollama pull nomic-embed-text
   ```

2. **启动 Ollama**（如果尚未运行）
   ```bash
   ollama serve
   ```

3. **打开 Web UI** → 进入 **📚 RAG 知识库** 页面
   ```bash
   python web_ui.py
   ```

4. **上传文档**：选择数据库官方文档（Oracle 管理手册、MySQL 参考手册等），选择对应的数据库类型

5. **执行 AI 诊断**：诊断时系统自动检索知识库相关内容

### Ollama 依赖模型

| 模型 | 用途 |
|------|------|
| `nomic-embed-text` | 文档向量化 |
| `qwen3:30b`（或类似） | AI 诊断大模型 |

---

## Oracle AWR 报告分析 📊

> 上传 Oracle AWR HTML 报告（由 awrrpt.sql 生成），DBCheck 自动解析报告内容，提取关键性能指标，生成结构化的 Word 分析报告，并支持 AI 智能诊断。

### 功能概述

AWR 报告分析功能让 DBA 无需手动阅读冗长的 AWR HTML 报告：

- **智能解析**：自动提取 AWR HTML 报告中的元数据（数据库名、实例名、版本、RAC 状态）和所有章节数据
- **元数据识别**：自动识别 Snap 范围、数据库版本、实例信息及 RAC 集群状态
- **Word 报告生成**：将 AWR 数据渲染为格式规范的 Word 文档，包含封面、章节数据表和分析摘要
- **AI 诊断建议**：启用 AI 后，自动从 AWR 数据中提取关键指标（负载、效率、缓存、I/O）构建诊断摘要，调用 AI 生成优化建议
- **Markdown 渲染**：AI 输出的 Markdown 内容自动渲染为 Word 样式（加粗、代码块、列表、标题序号）
- **进度指示**：报告生成过程分步显示进度（解析 AWR → AI 诊断 → 生成报告），避免等待焦虑

### 报告章节

AWR 分析报告自动包含以下章节（根据 AWR 原始报告内容动态生成）：

| 章节 | 内容 |
|------|------|
| 元数据摘要 | 数据库名、实例名、版本、RAC 状态、Snap 时间范围 |
| Load Profile | 每秒/每事务负载指标 |
| Instance Efficiency | 缓存命中率、库缓存命中率等效率指标 |
| 等待事件 Top | 按等待时间排序的 Top 等待事件 |
| SQL 统计 | 按执行次数/耗时/逻辑读的 Top SQL |
| 表空间 I/O | 各表空间读写统计 |
| 共享池与库缓存 | 共享池内存使用、库缓存命中率 |
| Latch 统计 | Top Latch 争用分析 |
| 缓冲池 Advisor | 缓冲池大小建议 |
| AI 诊断建议 | 基于 AWR 关键指标的 AI 优化建议（可选） |

### 使用方式

1. 在 Web UI 侧边栏导航至 **📊 AWR 报告分析**
2. 点击或拖拽 AWR HTML 文件到上传区域
3. 查看解析结果（元数据 + 章节摘要）
4. 点击「📝 生成分析报告」下载 Word 文档

---

## 环境要求

- **操作系统**：Linux / macOS / Windows
- **Python**：3.10 及以上
- **通用依赖**：pymysql、psycopg2-binary、python-docx、docxtpl、paramiko、psutil、openpyxl、pandas、flask、flask_socketio
- **Oracle 依赖**：`oracledb`（推荐）或 `cx_Oracle`（需要 Oracle Instant Client）
- **DM8 依赖**：`dmpython`（pip install dmpython）
- **SQL Server 依赖**：`pyodbc` + ODBC Driver 17（Windows/Linux 均支持）
- **MySQL 权限**：查询 information_schema、performance_schema、mysql 库的只读权限
- **PostgreSQL 权限**：查询 pg_stat_* 系列系统视图及 pg_roles 的只读权限
- **Oracle 权限**：查询 v$* 视图 / dba_* 视图的只读权限；支持 SYSDBA 特权连接（Web UI 复选框一键启用）
- **SQL Server 权限**：查询 sys.databases、sys.master_files、sys.dm_* 系列动态管理视图的只读权限
- **DM8 权限**：查询 V$* 系统视图 / DBA_* 管理视图的只读权限；默认端口 5236；连接用户即 Schema（无需 database 参数）
- **TiDB 依赖**：`pymysql`（与 MySQL 相同—TiDB 使用 MySQL 协议；默认端口 **4000**）
- **IvorySQL 依赖**：`psycopg2-binary`（与 PostgreSQL 相同—IvorySQL 使用 PostgreSQL 协议；默认端口 **5432**）

- **TiDB 权限**：查询 information_schema、performance_schema、mysql 库的只读权限（与 MySQL 完全一致）
- **IvorySQL 权限**：查询 pg_stat_* 系列系统视图及 pg_roles 的只读权限（与 PostgreSQL 完全相同）
- **YashanDB 依赖**：`yashandb`（pip install yashandb）
- **YashanDB 权限**：查询 V$* 系统视图的只读权限（Oracle 兼容；默认端口 **9088**）

- **SSH（可选）**：用于远程采集系统资源（MySQL / PostgreSQL / Oracle / DM8）；默认端口 22；DM8 SSH 采集失败时自动降级为本地采集器

### 安装依赖

```bash
pip install -r requirements.txt
```

> 💡 **数据库驱动说明：**
>
> - **Oracle**：`oracledb`（推荐，纯 Python 实现，无需 Instant Client）
> - **DM8**：`dmpython`（达梦官方驱动）
> - **SQL Server**：需额外安装 [ODBC Driver 17](https://docs.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)

---

## 快速开始

### 🐳 Docker 快速开始（推荐）

**一行命令启动，无需手动安装依赖！**

```bash
# Docker Hub（国际用户）
docker pull jackge12345/dbcheck:2.5.11

# GHCR（国内用户，推荐）
docker pull ghcr.io/fiyo/dbcheck:2.5.11

# 拉取并运行（基础版：MySQL / PG / Oracle / SQL Server / YashanDB）
docker run -d -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_reports:/app/reports \
  --name dbcheck \
  jackge12345/dbcheck:2.5.11

# 访问 http://localhost:5003
```

**特性：**
| 特性 | 说明 |
|------|------|
| 📦 预装依赖 | 所有 Python 依赖已预装，开箱即用 |
| 🔒 数据安全 | 数据和报告通过 volume 持久化，删除容器不丢数据 |
| 🚀 快速启动 | 无需安装 Python、数据库驱动，秒级启动 |
| 🔖 版本可控 | 指定版本号（v2.5.11），避免依赖冲突 |

**使用 docker-compose（更推荐）：**

```bash
# 下载 docker-compose.yml（或手动创建）
curl -o docker-compose.yml https://raw.githubusercontent.com/fiyo/DBCheck/main/docker-compose.yml

# 启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

**DM8 达梦数据库支持：**

`dmpython` 需要 DM8 客户端库，无法在基础镜像中预装。启用 DM8 支持有两种方式：

**方式一：运行时安装（推荐）**
```bash
# 1. 拉取并运行
docker pull jackge12345/dbcheck:2.5.11
docker run -d -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_reports:/app/reports \
  --name dbcheck \
  jackge12345/dbcheck:2.5.11

# 2. 进入容器安装 dmpython
docker exec -it dbcheck pip install dmpython
```

**方式二：自行构建含 DM8 的镜像**

详见 [docs/enable-dm8.md](docs/enable-dm8.md)

**镜像版本说明：**
| 标签 | 说明 |
|------|------|
| `jackge12345/dbcheck:latest` | 最新版（不含 DM8 客户端库）|
| `jackge12345/dbcheck:2.5.11` | 指定版本 |

---

### Web UI（可视化界面）

启动 Web 服务后，在浏览器访问 **http://localhost:5003** 即可通过图形界面完成所有巡检操作。

```bash
python web_ui.py
```

> 🔐 **默认账号**：用户名 `dbcheck`，密码 `dbcheck`。首次登录后请在用户中心修改密码。

**Web UI 操作步骤：**

| 步骤 | 功能 |
|:---:|------|
| 1 | 🗄️ 数据源管理：添加、编辑、删除、测试数据库连接，支持分组管理 |
| 2 | 选择数据库类型（🐬 MySQL / 🐘 PostgreSQL / 🔴 Oracle / 🟠 SQL Server / 🟡 DM8 / 🐬 TiDB / 🐘 IvorySQL / 🔵 YashanDB / 🟣 KingbaseES） |
| 3 | 填写连接信息，Oracle 需额外填写服务名/SID，DM8 无需填写 database 名 |
| 4 | 支持在线测试数据库连接（含 SYSDBA 特权验证，Web UI 复选框一键启用） |
| 5 | 配置 SSH 采集系统资源（可选，默认端口 22；DM8 支持 SSH 采集，失败时自动降级） |
| 6 | 填写巡检人员姓名（默认为 dbcheck），如需脱敏导出可勾选「🔒 脱敏导出报告」选项 |
| 7 | 确认信息后一键执行，实时查看日志进度（SSE 推送） |
| 8 | 巡检完成，在线预览智能分析 + AI 诊断结果 |
| 9 | 📋 问题列表：查看所有发现的问题，支持按风险等级筛选，可一键复制或执行修复 SQL |
| 10 | 📊 历史趋势分析：查看同一数据库多次巡检的指标趋势 |
| 11 | ⏰ 定时巡检：配置 Cron 表达式实现自动周期巡检 |
| 12 | 📧🔔 通知设置：配置邮件 / Webhook 告警推送 |
| 13 | 📚 RAG 知识库：上传和管理数据库文档，增强 AI 诊断能力 |
| 14 | 🖥️ 服务器巡检：全面检查服务器硬件和系统资源状态，生成服务器巡检报告 |
| 15 | 🔗 分享管理：管理已分享的报告链接，支持查看、复制、删除 |

#### 问题列表功能

巡检完成后，点击"📋 问题列表"按钮即可查看所有发现的问题：

| 功能 | 说明 |
|------|------|
| 问题分类 | 按高/中/低风险等级自动分类统计 |
| 问题详情 | 显示问题描述、风险等级、修复建议 |
| 一键复制 | 修复 SQL 可一键复制到剪贴板 |
| 在线修复 | 点击"执行修复"直接执行 SQL（高危操作需二次确认） |
| 历史报告 | 支持查看历史报告的问题列表 |

**风险等级说明：**

| 等级 | 颜色 |
|------|------|
| 🔴 高风险 | 红色 |
| 🟡 中风险 | 橙色 |
| 🟢 低风险 | 绿色 |

```bash
python main.py
```

主入口菜单提供十个选项：

```
python main.py --lang zh

  ██████╗ ██████╗  ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗
  ██╔══██╗██╔══██╗██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝
  ██║  ██║██████╔╝██║     ███████║██║     ██║     █████╔╝
  ██║  ██║██╔══██╗██║     ██╔══██║██╔══╝  ██║     ██╔═██╗
  ██████╔╝██████╔╝╚██████╗██║  ██║███████╗╚██████╗██║  ██╗
  ╚═════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝
          🗄️  数据库自动化巡检工具  v2.5.11  主菜单
  ─────────────────────────────────────────────────
    🐬  1 │ MySQL（5.6/5.7/8.0+）
    🐘  2 │ PostgreSQL（10+）
    🔴  3 │ Oracle（12c+）
    🟠  4 │ SQL Server（2012+）
    🟡  5 │ DM8 达梦（DM8+）
    🐬  6 │ TiDB（6.5+ / MySQL 8.0+ 兼容）
    🐘  7 │ IvorySQL（4.5.3+ / PG+Oracle 兼容）
    🔵  8 │ YashanDB 崖山（2.0+ / Oracle 兼容）
    🟣  9 │ KingbaseES 金仓（V8+ / PostgreSQL 兼容）
  ─────────────────────────────────────────────────
    📋  10 │ 批量生成巡检模板
    ❌  0 │ 退出
    
  ─────────────────────────────────────────────────

请选择 (1-10, 0退出): 
```

#### 单机巡检流程（以 Oracle 全面巡检为例）

1. 选择 **3** 进入 Oracle 巡检菜单
2. 选择 **1** 进行单机巡检
3. 根据提示填写：
   - 巡检名称
   - 数据库 IP / 端口（默认 1521）/ 服务名或 SID
   - 用户名（支持 SYSDBA 身份，Web UI 提供复选框，CLI 支持 `sys as sysdba` 语法）/ 密码
   - SSH 信息（可选，默认端口 22，用于采集系统资源）
4. 工具自动执行 42 项 SQL 检查 → 采集系统信息 → 智能风险分析 → AI 诊断（可选）
5. 生成 Word 巡检报告

#### 批量巡检

1. 先通过选项 **4** 生成对应的 Excel 批量巡检模板
2. 在模板中填写多个数据库实例的连接信息
3. 选择 **2** 批量巡检，程序自动依次巡检所有实例


### OpenClaw Skill（AI 助手直连）

本项目已发布为 [ClawHub](https://clawhub.ai/skills/dbcheck) 上的 OpenClaw Skill，接入 AI 助手后可通过自然语言直接触发巡检，无需手动操作命令行或 Web UI。

#### 安装方式

在 OpenClaw 客户端执行：

```bash
clawhub install dbcheck
```

#### 使用方式

安装后，直接告诉 AI 助手你想做的事，例如：

> "帮我巡检一下 Oracle 生产库，IP 是 localhost，用户名 sys as sysdba"

AI 助手会自动加载 Skill，按步骤询问缺少的信息（端口、服务名、巡检人员姓名等），然后调用巡检脚本生成 Word 报告。

#### 支持的指令

| 指令示例 | 说明 |
|---------|------|
| 帮我巡检一下 MySQL 库 | 单机 MySQL 巡检 |
| 帮我巡检一下 PostgreSQL 库 | 单机 PG 巡检 |
| 帮我巡检一下 Oracle 库 | 单机 Oracle 巡检 |
| 巡检 localhost 的 Oracle | 指定 IP 的快速巡检 |
| 生成一份数据库巡检报告 | 触发完整巡检流程 |

> "帮我巡检一下 IvorySQL 库" | 单机 IvorySQL 巡检 |

#### Skill 文件结构

```
dbcheck/skill/dbcheck/
├── SKILL.md           # Skill 说明
├── security.md        # 安全说明
└── scripts/
    ├── run_inspection.py   # 非交互式入口
    ├── main_mysql.py       # MySQL 巡检逻辑
    ├── main_pg.py         # PostgreSQL 巡检逻辑
    ├── main_oracle_full.py # Oracle 巡检逻辑（20+ 巡检项）
    ├── main_sqlserver.py   # SQL Server 巡检逻辑
    ├── main_dm.py         # 达梦 DM8 巡检逻辑
    ├── main_tidb.py        # TiDB 巡检逻辑
    ├── main_ivorysql.py   # IvorySQL 巡检逻辑（复用 PG 引擎）
    ├── analyzer.py        # 智能风险分析引擎
    ├── slow_query_analyzer.py  # 慢查询深度分析引擎（MySQL/PG/Oracle/SQLServer/DM8/YashanDB）
    └── main.py             # 统一菜单入口
```

> ⚠️ **安全提示**：Skill 凭据仅用于建立本地连接，不会发送到任何第三方。AI 诊断仅使用本地 Ollama。

---

## 打包部署

使用 PyInstaller 配置文件 `dbcheck.spec` 进行打包，将所有依赖、模板文件、项目模块全部打入单个 exe 文件：

```bash
cd D:\DBCheck

# 清理旧构建（Windows）
rd /s /q build dist __pycache__ 2>nul

# 打包
pyinstaller dbcheck.spec
```

> Linux/macOS 上请使用 `rm -rf build dist __pycache__` 清理。

打包后执行：

```bash
cd dist
dbcheck.exe         # Windows
./dbcheck           # Linux/macOS
```

双击即可运行完整版程序，包含所有数据库驱动、Word 模板、Web UI 页面模板，无需安装 Python 环境。

---

## 报告结构

生成的 Word 报告包含以下章节（Oracle 巡检报告示例）：

| 章节 | 内容（Oracle 巡检） |
|------|------|
| 封面 | 数据库名称、服务器地址、版本、主机名、启动时间、巡检人员、平台、报告时间 |
| 第1章 | OS 主机信息（CPU/内存/磁盘） |
| 第2章 | 数据库基本信息（版本/实例名/数据库名） |
| 第3章 | 表空间（永久 + 临时，含自动扩展） |
| 第4章 | SGA / PGA 内存分析 |
| 第5章 | 关键参数配置 |
| 第6章 | Undo 表空间管理 |
| 第7章 | 重做日志（Redo） |
| 第8章 | 归档与备份 |
| 第9章 | Data Guard 状态 |
| 第10章 | RAC 集群信息 |
| 第11章 | ASM 磁盘组 |
| 第12章 | 会话与连接（含等待事件 TOP5） |
| 第13章 | 性能指标（含 AWR 快照分析） |
| 第14章 | Alert 日志分析 |
| 第15章 | 用户与安全 |
| 第16章 | 无效对象与统计信息 |
| 第17章 | 分区表信息 |
| 第18章 | FRA 闪回恢复区 |
| 第19章 | 回收站 |
| 第20章 | 风险与建议（智能分析问题明细 + 修复 SQL 速查表） |
| 第21章 | AI 诊断建议（Markdown 自动渲染为 Word 格式，含序号标题、代码块、列表） |
| 第22章 | 报告说明 |

> 不同数据库类型的报告结构略有差异，但均包含封面、基本信息、性能分析、风险建议、AI 诊断、报告说明六大模块。

---

## REST API（v2.4.3+）

DBCheck 提供 REST API 供 CI/CD、监控系统等外部工具调用。所有 API 需通过 **API Key** 认证，在 Web UI 的 **API Key 管理** 页面创建。

### 快速开始

```bash
# 1. 健康检查
curl http://localhost:5003/api/v1/health

# 2. 触发巡检（同步等待结果）
curl -X POST http://localhost:5003/api/v1/inspect \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"db_type":"mysql","host":"192.168.1.100","port":3306,"user":"root","password":"****"}'


# 3. IvorySQL 巡检（使用 PostgreSQL 协议）
curl -X POST http://localhost:5003/api/v1/inspect \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"db_type":"ivorysql","host":"192.168.1.300","port":5432,"user":"postgres","password":"****"}'

# 3. 异步巡检
curl -X POST http://localhost:5003/api/v1/inspect \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"db_type":"oracle","host":"192.168.1.200","service_name":"ORCL","user":"system","password":"****","mode":"async"}'

# 4. 查询任务结果
curl -H "X-API-Key: YOUR_API_KEY" http://localhost:5003/api/v1/inspect/{task_id}
```

### 接口列表

| 方法 | 路径 |
|------|------|
| `GET` | `/api/v1/health` |
| `POST` | `/api/v1/inspect` |
| `GET` | `/api/v1/inspect/{task_id}` |
| `GET` | `/api/v1/inspects` |
| `POST` | `/api/server_inspect_share` |
| `POST` | `/api/db_inspect_share` |
| `GET` | `/share/<share_id>` |
| `GET` | `/api/share/<share_id>` |
| `DELETE` | `/api/share/<share_id>` |
| `GET` | `/api/shares` |

### 请求参数

| 参数 | 类型 |
|------|------|
| `db_type` | string |
| `host` | string |
| `port` | int |
| `user` | string |
| `password` | string |
| `database` | string |
| `service_name` | string |
| `sysdba` | bool |
| `mode` | string |
| `timeout` | int |
| `ssh` | object |

### 安全建议

- 生产环境使用 HTTPS 反向代理（nginx）
- 定期轮换 API Key
- 内网部署，防火墙限制访问
- API Key 创建后仅显示一次，请妥善保管

---

## 常见问题

### 通用问题

1. **部分内容为空或缺失**
   模板渲染出现兼容性问题时，程序会自动切换至备用渲染模式，仍可生成包含所有关键数据的完整报告，不影响使用。

2. **连接失败**
   检查数据库是否允许远程访问、用户权限是否充足、防火墙是否放行对应端口。

3. **SSH 采集失败**
   确认 SSH 服务正常运行（默认端口 22）、认证信息正确。部分精简版 Linux 可能缺少 `lscpu` 等命令，导致部分 CPU 信息显示为"未获取"，属正常现象。

4. **AI 诊断不生效**
   - 确认已在 Web UI「AI 诊断设置」中保存了有效配置
   - 确保 Ollama 已启动：`ollama serve`
   - 确保模型已下载：`ollama pull qwen3:30b`（建议大模型，冷启动慢）

5. **风险建议仅供参考**
   内置阈值基于通用最佳实践，实际场景中请结合业务负载综合评估。

### Oracle 专项

6. **ORA-01017 用户名/口令无效**
   - 如果使用 SYSDBA 身份，Web UI 请勾选「SYSDBA」复选框；CLI 请输入 `sys as sysdba`（完整格式），工具会自动解析并使用正确的特权模式连接
   - 确认密码正确（注意大小写）

7. **ORA-00904 / ORA-00942 标识符无效**
   部分高级视图/列在不同 Oracle 版本中可能不存在（如 11g vs 19c）。工具已做兼容处理，少数不兼容的项目会标记为⚠跳过，不影响整体巡检。

8. **需要安装 Oracle 客户端吗？**
   - 使用 `oracledb` 驱动（推荐）：不需要，纯 Python 实现
   - 使用 `cx_Oracle` 驱动：需要下载 [Oracle Instant Client](https://www.oracle.com/database/technologies/instant-client.html)

9. **Oracle 版本支持**
   支持 **11g R2、12c、19c、21c** 及以上版本。SQL 模板已做跨版本兼容处理。

### DM8 专项

10. **连接失败（returned a result with an exception set）**
    - dmPython 为惰性连接，连接对象创建成功不代表真正连通，需通过游标执行探测 SQL 才能确认
    - 工具已内置自动探测逻辑，如仍失败请检查：端口是否正确（默认 5236）、用户密码是否正确、服务器是否允许该 IP 访问

11. **提示"无效的列名"**
    - DM8 的 V$ 视图列名与 Oracle 有较大差异，工具已针对 DM8 实测列名做过适配，如仍有报错请截图发给我们补充

12. **SSH 采集功能不可用**
    - 受限于达梦服务器 OpenSSH 版本（端口 2022），SSH 采集暂时禁用。系统资源信息将使用本地采集器，本地与达梦服务器信息不一致属正常现象。

13. **报告中的"服务器主机名/平台"是本机信息**
    - SSH 采集禁用后的已知限制，达梦服务器系统信息采集依赖 SSH 通道，后续版本将尝试修复

### SQL Server 专项

14. **连接失败**
    - 确认 SQL Server 服务允许远程连接（SQL Server Configuration Manager → Network Configuration → TCP/IP 已启用）
    - 确认防火墙已放行 1433 端口（或自定义端口）
    - 确认使用了正确的认证方式（Windows 认证或 SQL Server 混合认证）

15. **pyodbc 安装成功但连接失败**
    - 需要安装 ODBC Driver 17：`curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -` 后安装对应版本的 mssql-server
    - Linux 上可能需要：`curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | tee /etc/apt/sources.list.d/mssql-release.list`

16. **SQL Server 版本支持**
    - 支持 **SQL Server 2012、2014、2016、2017、2019、2022** 及以上版本

---

## 界面截图

![首页](snapshot/webui0.png)

![步骤一：选择数据库类型](snapshot/webui1.png)
*图 1：选择数据库类型（MySQL 🐬 / PostgreSQL 🐘 / Oracle 🔴 / SQL Server 🟠 / DM8 🟡 / TiDB 🐬 / IvorySQL 🐘 / YashanDB 🔵）*

![步骤二：填写连接信息](snapshot/webui2.png)
*图 2：填写数据库连接信息*

![步骤三：在线连接测试数据库连接](snapshot/webui3.png)
*图 3：在线连接测试数据库连接*

![步骤四：SSH 连接配置](snapshot/webui5.png)
*图 4：SSH 连接配置（可选，默认端口 22）*

![步骤五：巡检人员](snapshot/webui6.png)
*图 5：巡检人员配置（默认为 dbcheck）*

![步骤六：确认巡检信息](snapshot/webui7.png)
*图 6：确认巡检信息*

![步骤七：执行巡检](snapshot/webui8.png)
*图 7：一键巡检，实时预览巡检进度*

![报告下载](snapshot/webui9.png)
*图 8：巡检完成后直接下载 Word 报告*

![问题列表](snapshot/webui18.png)
*巡检完成后自动生成问题列表，并可一键修复*

![历史报告](snapshot/webui10.png)
*图 9：历史报告列表页，支持按名称、大小、时间浏览*

![历史趋势分析](snapshot/webui12.png)
*图 10：历史趋势分析*

![AI 诊断配置](snapshot/webui13.png)
*图 11：AI 诊断配置，可完全本地运行，无需 API Key，数据不出本机。*

![数据源管理](snapshot/webui15.png)
*数据源管理*

![规则引擎](snapshot/webui16.png)
*规则引擎*

![RAG 知识库](snapshot/webui17.png)
*RAG 知识库*


![Clawhub dbcheck skill](snapshot/skill0.png)
*图 12：dbcheck 已发布到 Clawhub*

![QClaw](snapshot/skill1.png)
*图 13：在 QClaw 等支持 OpenClaw Skills 的软件中使用 dbcheck。*

![Reports](snapshot/report.png)
*图 14：AI 诊断报告（Markdown 自动渲染为 Word 格式）。*

![Server Inspection](snapshot/server_inspect.png)
*图 15：服务器巡检报告。*

![Share Management](snapshot/share_management.png)
*图 16：分享管理页面。*

![Share View](snapshot/share_view.png)
*图 17: 分享查看页面。*
---
## 鸣谢

> 本项目参考了以下项目，感谢原项目作者的付出：

* [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git)
* [Zhh9126/SQL-SERVER-CHECK](https://github.com/Zhh9126/SQL-SERVER-CHECK.git)

部分功能仍在快速迭代中，将来会增加更多的数据库类型，也会增强自身功能，欢迎共同参与功能开发以及反馈问题与建议。

---

## 捐赠支持

DBCheck 从初版到功能完善，历经了大量版本迭代和实测打磨。如果这个工具对你的工作有帮助，欢迎通过以下方式支持项目持续迭代：

<img src="snapshot/pay.png" alt="PayPal 捐赠二维码" width="600" />

> 捐赠时备注你的名字或昵称，让我们知道谁在支持这个项目 ❤️
> 
> 官方网站：https://dbcheck.top
> 
> 联系邮箱：sdfiyon@gmail.com

## 捐赠者名单

感谢每一位支持者的信任与鼓励！❤️

| 日期 | 姓名/昵称 |编号|
|------|-----------|-----|
| 2026-4-28 | *ck |<img src="snapshot/DBCheck-000001.png" alt="PayPal 捐赠二维码" width="100" />No.000001|
| 2026-4-29 | *嵘 |<img src="snapshot/DBCheck-000002.png" alt="PayPal 捐赠二维码" width="100" />No.000002|
| 2026-5-4 | **政 |<img src="snapshot/DBCheck-000003.png" alt="PayPal 捐赠二维码" width="100" />No.000003|
| 2026-6-2 | **月光 | <img src="snapshot/DBCheck-000004.png" alt="PayPal 捐赠二维码" width="100" />No.000004|
| 2026-6-3 | *树 |<img src="snapshot/DBCheck-000005.png" alt="PayPal 捐赠二维码" width="100" />No.000005|
| 2026-6-7 | *0518 |<img src="snapshot/DBCheck-000006.png" alt="PayPal 捐赠二维码" width="100" />No.000006|
| *期待你的支持！* |  |

> 如已捐赠但未出现在此名单中，请联系 sdfiyon@gmail.com 补充。
