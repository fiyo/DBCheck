# 数据库巡检工具 - DBCheck

支持对 **MySQL**、**PostgreSQL** 和 **Oracle** 三种主流关系型数据库进行自动化健康巡检，生成格式规范的 Microsoft Word 报告，帮助 DBA 和运维人员快速掌握数据库运行状况、发现潜在风险。

> 本项目由 [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git) 改进而来，在原 MySQL 支持的基础上新增了 PostgreSQL 和 Oracle 支持。

## AI 辅助 · 问题发现即处理

### 🤖 AI 智能诊断

调用本地 **Ollama**（完全离线），基于当次巡检的指标数据（连接数、缓存命中率、慢查询数、安全风险等），自动生成结构化的优化建议。报告独立成章，方便直接转发给团队或领导审阅。

| 后端 | 特点 | 适用场景 |
|------|------|---------|
| `ollama` | 本地运行，零成本，无网络依赖 | 内网环境、数据安全要求高 |
| `disabled` | 不调用 AI（默认） | 离线环境 / 无需 AI |

> ⚠️ **安全说明**：AI 诊断功能仅支持本地 Ollama（localhost:11434），巡检数据不会发送到任何第三方服务。代码层已做硬性限制，即使配置文件被篡改为远程地址也会自动降级为禁用状态。

### 🔍 风险与建议

每条风险对应一张卡片，包含：**风险等级（高/中/低）→ 问题描述 → 修复 SQL（可直接复制执行）→ 优先级与负责人**。报告自动汇总，一眼看清全部待处理项。

| 维度 | MySQL | PostgreSQL | Oracle |
|------|:-----:|:----------:|:------:|
| 连接资源 | ✅ | ✅ | ✅ |
| 缓存性能 | ✅ | ✅ | ✅ |
| 查询效率 | ✅ | ✅ | ✅ |
| 日志告警 | ✅ | ✅ | ✅ |
| 安全审计 | ✅ | ✅ | ✅ |
| 复制/DG | ✅ | ✅ | ✅ |
| 配置优化 | ✅ | ✅ | ✅ |
| 表空间 | — | — | ✅ |
| SGA/PGA 内存 | — | — | ✅ |
| Redo 日志 | — | — | ✅ |
| RAC 集群 | — | — | ✅ |
| ASM 磁盘组 | — | — | ✅ |
| Undo 管理 | — | — | ✅ |
| Data Guard | — | — | ✅ |

---

## 四大核心能力

| 能力 | 说明 |
|------|------|
| 📊 历史趋势分析 | 同一数据库多次巡检数据自动汇聚，生成指标趋势折线图，与上次对比发现变化 |
| 🤖 AI 智能诊断 | 基于巡检指标调用本地 Ollama，生成个性化优化建议 |
| 🔍 50+ 条增强规则 | 覆盖三种数据库全维度风险检测（MySQL 18+ / PG 16+ / Oracle 20+） |
| 🦞 OpenClaw Skill | AI 助手一句话完成巡检，零操作生成报告 |

---

## 四种使用方式

| 方式 | 说明 |
|------|------|
| 🖥️ 命令行 | `python main.py`，终端交互，适合熟悉命令行的用户 |
| 🌐 Web UI | `python web_ui.py`，浏览器图形界面，支持趋势图和 AI 诊断配置 |
| 🤖 OpenClaw Skill | 告诉 AI 助手"帮我巡检 XX 库"，零操作自动完成 |
| 📦 打包部署 | PyInstaller 打包成分发版，给团队成员使用 |

---

## 功能特性

### 数据库巡检

| 维度 | MySQL | PostgreSQL | Oracle |
|------|:-----:|:----------:|:------:|
| 基本信息（版本/实例/数据库） | ✅ | ✅ | ✅ |
| 会话与连接状态 | ✅ | ✅ | ✅ |
| 内存与缓存配置 | ✅ | ✅ | ✅ |
| 表空间使用情况 | — | — | ✅ |
| SGA / PGA 内存分析 | — | — | ✅ |
| Redo 日志与状态 | — | — | ✅ |
| 归档与备份检查 | — | — | ✅ |
| 关键参数配置 | ✅ | ✅ | ✅ |
| 无效对象检测 | ✅ | ✅ | ✅ |
| 用户安全审计 | ✅ | ✅ | ✅ |
| Top SQL / 慢查询 | ✅ | ✅ | ✅ |
| 主从复制 / Data Guard | ✅ | ✅ | ✅ |
| RAC 集群信息 | — | — | ✅ |
| ASM 磁盘组 | — | — | ✅ |
| Undo 表空间管理 | — | — | ✅ |
| 回收站 / 闪回恢复区 | — | — | ✅ |
| Profile 密码策略 | — | — | ✅ |
| 等待事件 TOP | — | — | ✅ |
| 统计信息陈旧检测 | — | — | ✅ |
| 分区表信息 | — | — | ✅ |
| 数据文件状态 | — | — | ✅ |

### 系统资源监控

- **CPU**：使用率、核心数、频率
- **内存**：总量、使用量、可用量、使用率
- **磁盘**：各挂载点容量及使用率
- **采集方式**：本地直采或 SSH 远程采集（支持密码/密钥认证）

### 智能风险分析

自动检测数据库潜在风险，**每条风险附可执行的修复 SQL**，可直接复制到数据库执行：

#### MySQL（18+ 条规则）

| 维度 | 规则示例 |
|------|---------|
| 连接数 | 使用率 >90% 高危 / >80% 中危 |
| 内存 | InnoDB 缓冲池偏小（<数据总量 60%）|
| 磁盘 | 使用率 >85% 警告 / >95% 高危 |
| 查询 | 长时间运行 SQL（>60s）、慢查询日志未开启 |
| 锁 | 锁等待比例偏高 |
| 安全 | 用户空密码、root@% 暴露、字符集非 UTF8 |
| 复制 | 主从延迟 >30s、复制状态异常 |
| 其他 | binlog 未开启、查询缓存残留、异常中止连接过多 |

#### PostgreSQL（16+ 条规则）

| 维度 | 规则示例 |
|------|---------|
| 连接 | 连接数接近上限、超级用户过多 |
| 缓存 | 缓存命中率偏低（<80%）、shared_buffers 偏小 |
| 性能 | dead tuples 大量累积、长时间运行 SQL |
| 安全 | 公开 schema 权限过宽 |
| 归档 | 归档模式未开启 |
| 其他 | 磁盘/内存/CPU 资源告警 |

#### Oracle（20+ 条规则）

| 维度 | 规则示例 |
|------|---------|
| 表空间 | 使用率 >90%（含自动扩展计算）|
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

### 历史趋势分析 📊

> 多次巡检同一个数据库，自动汇聚指标数据，生成趋势图，发现悄然发生的变化。

- 每次巡检后，关键指标（内存使用率、连接数、QPS、CPU 等）自动写入本地 `history.json`
- 同一数据库（IP + 端口 + 类型）多次巡检数据聚合保留，最多 30 条历史记录
- Web UI 提供**趋势分析页面**，绘制指标折线图，带警戒线标注
- 与上次巡检逐项对比：变化量带颜色箭头（↑ 变差 / ↓ 好转）

### AI 智能诊断 🤖

> 基于巡检数据，调用本地 Ollama 大模型生成个性化优化建议，从"发现问题"升级到"解决问题"。

AI 诊断与智能分析的关系：

| | 智能分析 | AI 诊断 |
|---|---|---|
| 原理 | 固定规则，离线判断 | 本地大模型推理，个性化输出 |
| 速度 | 毫秒级 | 取决于模型响应时间 |
| 结果 | 确定性结论 + 修复 SQL | 自然语言优化建议 |
| 调用 | 每次巡检自动执行 | 按需调用（可关闭） |

**AI 后端配置（Web UI 可视化设置）：**

| 参数 | 说明 |
|------|------|
| 后端类型 | `ollama` 或 `disabled` |
| API 地址 | 默认 `http://localhost:11434`（仅允许 localhost）|
| 模型名称 | 如 `qwen3:8b`、`llama3` 等 |

> ⚠️ 出于安全考虑，非 localhost 的 API 地址会被代码自动拒绝，防止敏感数据外传。

---

## 环境要求

- **操作系统**：Linux / macOS / Windows
- **Python**：3.6 及以上
- **通用依赖**：pymysql、psycopg2-binary、python-docx、docxtpl、paramiko、psutil、openpyxl、pandas、flask
- **Oracle 依赖**：`oracledb`（推荐）或 `cx_Oracle`（需要 Oracle Instant Client）
- **MySQL 权限**：查询 information_schema、performance_schema、mysql 库的只读权限
- **PostgreSQL 权限**：查询 pg_stat_* 系列系统视图及 pg_roles 的只读权限
- **Oracle 权限**：查询 v$* 视图 / dba_* 视图的只读权限；支持 SYSDBA 特权连接
- **SSH（可选）**：用于远程采集系统资源

### 安装依赖

```bash
pip install pymysql psycopg2-binary paramiko openpyxl docxtpl python-docx pandas psutil flask oracledb
```

> 💡 Oracle 驱动安装提示：
> - `oracledb`：纯 Python 实现，无需 Instant Client，推荐使用
> - `cx_Oracle`：需要额外下载 [Oracle Instant Client](https://www.oracle.com/database/technologies/instant-client.html) 并配置环境变量

---

## 快速开始

```bash
python main.py
```

主入口菜单提供六个选项：

```
==================================================
  🗄️  数据库自动化巡检工具  v2.2  统一入口
==================================================
    🐬  1 │ MySQL      MySQL 数据库健康巡检与报告生成
    🐘  2 │ PostgreSQL PostgreSQL 数据库健康巡检与报告生成
    🔴  3 │ Oracle     Oracle 数据库健康巡检与报告生成
    📋  4 │ 生成批量巡检模板   生成 MySQL / PostgreSQL / Oracle 批量巡检 Excel 模板
    🌐  5 │ 启动 Web UI     浏览器可视化操作界面
        6 │ 退出
==================================================
```

1. 输入 **1~3**，进入对应数据库类型的巡检功能菜单
2. 输入 **4**，选择要生成的模板类型（MySQL / PostgreSQL / Oracle）
3. 输入 **5**，启动 Web UI 服务
4. 输入 **6** 退出工具

#### 单机巡检流程（以 Oracle 为例）

1. 选择 **3** 进入 Oracle 巡检菜单
2. 选择 **1** 进行单机巡检
3. 根据提示填写：
   - 巡检名称
   - 数据库 IP / 端口（默认 1521）/ 服务名或 SID
   - 用户名（支持 `sys as sysdba` 等特权身份）/ 密码
   - SSH 信息（可选，用于采集系统资源）
4. 工具自动执行 42 项 SQL 检查 → 采集系统信息 → 智能风险分析 → AI 诊断（可选）
5. 生成 Word 巡检报告

#### 批量巡检

1. 先通过选项 **4** 生成对应的 Excel 批量巡检模板
2. 在模板中填写多个数据库实例的连接信息
3. 选择 **2** 批量巡检，程序自动依次巡检所有实例

### Web UI（可视化界面）

启动 Web 服务后，在浏览器访问 **http://localhost:5000** 即可通过图形界面完成所有巡检操作。

```bash
python web_ui.py
```

**Web UI 操作步骤：**

| 步骤 | 功能 |
|:---:|------|
| 1 | 选择数据库类型（🐬 MySQL / 🐘 PostgreSQL / 🔴 Oracle）|
| 2 | 填写连接信息，Oracle 需额外填写服务名/SID |
| 3 | 支持在线测试数据库连接（含 SYSDBA 特权验证）|
| 4 | 配置 SSH 采集系统资源（可选）|
| 5 | 填写巡检人员姓名 |
| 6 | 确认信息后一键执行，实时查看日志进度（SSE 推送）|
| 7 | 巡检完成，在线预览智能分析 + AI 诊断结果 |
| 8 | 📊 历史趋势分析：查看同一数据库多次巡检的指标趋势 |
| 9 | 🤖 AI 诊断设置：配置本地 Ollama 参数 |
| 10 | 下载 Word 报告，随时查阅历史报告 |

### OpenClaw Skill（AI 助手直连）

本项目已发布为 [ClawHub](https://clawhub.ai/skills/dbcheck) 上的 OpenClaw Skill，接入 AI 助手后可通过自然语言直接触发巡检，无需手动操作命令行或 Web UI。

#### 安装方式

在 OpenClaw 客户端执行：

```bash
clawhub install dbcheck
```

#### 使用方式

安装后，直接告诉 AI 助手你想做的事，例如：

> "帮我巡检一下 Oracle 生产库，IP 是 192.168.1.10，用户名 sys as sysdba"

AI 助手会自动加载 Skill，按步骤询问缺少的信息（端口、服务名、巡检人员姓名等），然后调用巡检脚本生成 Word 报告。

#### 支持的指令

| 指令示例 | 说明 |
|---------|------|
| 帮我巡检一下 MySQL 库 | 单机 MySQL 巡检 |
| 帮我巡检一下 PostgreSQL 库 | 单机 PG 巡检 |
| 帮我巡检一下 Oracle 库 | 单机 Oracle 巡检 |
| 巡检 192.168.1.10 的 Oracle | 指定 IP 的快速巡检 |
| 生成一份数据库巡检报告 | 触发完整巡检流程 |

#### Skill 文件结构

```
~/.workbuddy/skills/dbcheck/
├── SKILL.md           # Skill 说明
├── security.md        # 安全说明
└── scripts/
    ├── run_inspection.py   # 非交互式入口
    ├── main_mysql.py       # MySQL 巡检逻辑
    ├── main_pg.py         # PostgreSQL 巡检逻辑
    ├── main_oracle.py     # Oracle 巡检逻辑
    ├── analyzer.py        # 智能风险分析引擎
    └── main.py             # 统一菜单入口
```

> ⚠️ **安全提示**：Skill 凭据仅用于建立本地连接，不会发送到任何第三方。AI 诊断仅使用本地 Ollama。

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
    --hidden-import oracledb \
    main.py
```

打包后在 `dist` 目录下执行：

```bash
cd dist
./dbcheck          # Linux/macOS
dbcheck.exe         # Windows
```

---

## 报告结构

生成的 Word 报告包含以下章节（以 Oracle 为例）：

| 章节 | 内容 |
|------|------|
| 封面 | 8 行基本信息表：数据库名称、服务器地址、版本、主机名、启动时间、巡检人员、平台、报告时间 |
| 第1章 | 数据库基本信息（版本/实例/角色/归档模式等）|
| 第2章 | 实例与运行状态 |
| 第3章 | 表空间使用情况（含使用率和自动扩展计算）|
| 第4章 | SGA / PGA 内存分析 |
| 第5章 | 会话与锁等待 |
| 第6章 | Redo 日志与归档 |
| 第7章 | 系统资源监控（CPU/内存/磁盘）|
| 第8章 | 对象与用户安全（无效对象/用户列表）|
| 第9章 | 关键参数配置 |
| 第10章 | Data Guard / ADG 状态（如适用）|
| 第11章 | ASM 磁盘组（如适用）|
| 第12章 | Top CPU SQL |
| 第13章 | 等待事件分类统计 |
| 第14章 | 陈旧统计信息 |
| **风险与建议** | 智能分析出的潜在问题及修复 SQL（20+ 条卡片）|
| **AI 诊断** | 本地大模型生成的个性化优化建议（可选）|
| **报告说明** | 6 条标准免责声明与建议 |

> 不同数据库类型的报告结构略有差异，但均包含封面、基本信息、性能分析、风险建议、AI 诊断、报告说明六大模块。

---

## 常见问题

### 通用问题

1. **部分内容为空或缺失**
   模板渲染出现兼容性问题时，程序会自动切换至备用渲染模式，仍可生成包含所有关键数据的完整报告，不影响使用。

2. **连接失败**
   检查数据库是否允许远程访问、用户权限是否充足、防火墙是否放行对应端口。

3. **SSH 采集失败**
   确认 SSH 服务正常运行、认证信息正确。部分精简版 Linux 可能缺少 `lscpu` 等命令，导致部分 CPU 信息显示为"未获取"，属正常现象。

4. **AI 诊断不生效**
   - 确认已在 Web UI「AI 诊断设置」中保存了有效配置
   - 确保 Ollama 已启动：`ollama serve`
   - 确保模型已下载：`ollama pull qwen3:8b`

5. **风险建议仅供参考**
   内置阈值基于通用最佳实践，实际场景中请结合业务负载综合评估。

### Oracle 专项

6. **ORA-01017 用户名/口令无效**
   - 如果使用 SYSDBA 身份，用户名应输入 `sys as sysdba`（完整格式），工具会自动解析并使用正确的特权模式连接
   - 确认密码正确（注意大小写）

7. **ORA-00904 / ORA-00942 标识符无效**
   部分高级视图/列在不同 Oracle 版本中可能不存在（如 11g vs 19c）。工具已做兼容处理，少数不兼容的项目会标记为⚠跳过，不影响整体巡检。

8. **需要安装 Oracle 客户端吗？**
   - 使用 `oracledb` 驱动（推荐）：不需要，纯 Python 实现
   - 使用 `cx_Oracle` 驱动：需要下载 [Oracle Instant Client](https://www.oracle.com/database/technologies/instant-client.html)

9. **Oracle 版本支持**
   支持 **11g R2、12c、19c、21c** 及以上版本。SQL 模板已做跨版本兼容处理。

---

## 界面截图

![步骤一：选择数据库类型](snapshot/webui1.png)
*图 1：选择数据库类型（MySQL 🐬 / PostgreSQL 🐘 / Oracle 🔴）*

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

![历史趋势分析](snapshot/webui12.png)
*图 11：历史趋势分析*

![AI 诊断配置](snapshot/webui13.png)
*图 12：AI 诊断配置，可完全本地运行，无需 API Key，数据不出本机。*

![Clawhub dbcheck skill](snapshot/skill0.png)
*图 13：dbcheck 已发布到 Clawhub*

![QClaw](snapshot/skill1.png)
*图 14：在 QClaw 等支持 OpenClaw Skills 的软件中使用 dbcheck。*

---

## 鸣谢

感谢 [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git) 作者的贡献！

目前部分功能仍在持续完善中，欢迎共同参与功能开发以及反馈问题与建议。
