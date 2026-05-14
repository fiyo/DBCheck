# 数据库巡检工具 - DBCheck

支持对 **MySQL**、**PostgreSQL**、**Oracle** 和 **达梦 DM8** 四种主流关系型数据库进行自动化健康巡检，生成格式规范的 Microsoft Word 报告，帮助 DBA 和运维人员快速掌握数据库运行状况、发现潜在风险。

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

| 维度 | MySQL | PostgreSQL | Oracle | DM8 |
|------|:-----:|:----------:|:------:|:----:|
| 连接资源 | ✅ | ✅ | ✅ | ✅ |
| 缓存性能 | ✅ | ✅ | ✅ | ✅ |
| 查询效率 | ✅ | ✅ | ✅ | ✅ |
| 日志告警 | ✅ | ✅ | ✅ | ✅ |
| 安全审计 | ✅ | ✅ | ✅ | ✅ |
| 复制/DG | ✅ | ✅ | ✅ | — |
| 配置优化 | ✅ | ✅ | ✅ | ✅ |
| 表空间 | — | — | ✅ | ✅ |
| SGA/PGA 内存 | — | — | ✅ | ✅ |
| Redo 日志 | — | — | ✅ | ✅ |
| 备份与归档 | — | — | ✅ | ✅ |
| RAC 集群 | — | — | ✅ | — |
| ASM 磁盘组 | — | — | ✅ | — |
| Undo 管理 | — | — | ✅ | ✅ |
| Data Guard | — | — | ✅ | — |
| DM8 特有视图 | — | — | — | ✅ |

---

## 四大核心能力

| 能力 | 说明 |
|------|------|
| 📊 历史趋势分析 | 同一数据库多次巡检数据自动汇聚，生成指标趋势折线图，与上次对比发现变化 |
| 🤖 AI 智能诊断 | 基于巡检指标调用本地 Ollama，生成个性化优化建议 |
| 🔍 80+ 条增强规则 | 覆盖四种数据库全维度风险检测（MySQL 18+ / PG 16+ / Oracle 20+ / DM8 16+） |
| 🦞 OpenClaw Skill | AI 助手一句话完成巡检，零操作生成报告 |

---

## 五种使用方式

| 方式 | 说明 |
|------|------|
| 🖥️ 命令行 | `python main.py`，终端交互，适合熟悉命令行的用户 |
| 🌐 Web UI | `python web_ui.py`，浏览器图形界面，支持趋势图和 AI 诊断配置 |
| 🤖 OpenClaw Skill | 告诉 AI 助手"帮我巡检 XX 库"，零操作自动完成 |
| 📦 打包部署 | PyInstaller 打包成分发版，给团队成员使用 |

---

## 功能特性

### 数据库巡检

| 维度 | MySQL | PostgreSQL | Oracle | DM8 |
|------|:-----:|:----------:|:------:|:----:|
| 基本信息（版本/实例/数据库） | ✅ | ✅ | ✅ | ✅ |
| 会话与连接状态 | ✅ | ✅ | ✅ | ✅ |
| 内存与缓存配置 | ✅ | ✅ | ✅ | ✅ |
| 表空间使用情况 | — | — | ✅ | ✅ |
| SGA / PGA 内存分析 | — | — | ✅ | ✅ |
| Redo 日志与状态 | — | — | ✅ | ✅ |
| 归档与备份检查 | — | — | ✅ | ✅ |
| 关键参数配置 | ✅ | ✅ | ✅ | ✅ |
| 无效对象检测 | ✅ | ✅ | ✅ | ✅ |
| 用户安全审计 | ✅ | ✅ | ✅ | ✅ |
| Top SQL / 慢查询 | ✅ | ✅ | ✅ | ✅ |
| 主从复制 / Data Guard | ✅ | ✅ | ✅ | — |
| RAC 集群信息 | — | — | ✅ | — |
| ASM 磁盘组 | — | — | ✅ | — |
| Undo 表空间管理 | — | — | ✅ | ✅ |
| 回收站 / 闪回恢复区 | — | — | ✅ | ✅ |
| Profile 密码策略 | — | — | ✅ | — |
| 等待事件 TOP | — | — | ✅ | ✅ |
| 统计信息陈旧检测 | — | — | ✅ | ✅ |
| 分区表信息 | — | — | ✅ | ✅ |
| 数据文件状态 | — | — | ✅ | ✅ |
| DM8 缓冲池详情 | — | — | — | ✅ |

### 配置基线检查

> 根据数据库规模、内存和负载自动计算推荐配置值，与当前值对比，识别配置偏差。

#### MySQL（22 个参数）

| 参数 | 说明 | 推荐值依据 |
|------|------|-----------|
| innodb_buffer_pool_size | InnoDB 缓冲池大小 | 总内存的 60–80% |
| max_connections | 最大连接数 | 历史峰值的 1.5 倍 |
| tmp_table_size / max_heap_table_size | 临时表/内存表大小 | 64MB；两者应保持一致 |
| innodb_log_file_size | Redo 日志文件大小 | 256MB–1GB（按数据量） |
| innodb_log_buffer_size | 日志缓冲区大小 | 16MB |
| sync_binlog | Binlog 同步频率 | 高并发写入建议设为 1 |
| innodb_flush_log_at_trx_commit | 事务提交日志刷盘策略 | 1（最严格、最安全）|
| table_open_cache / table_definition_cache | 表缓存/表定义缓存 | 2× 表数量 / 线程数 |
| thread_cache_size | 线程缓存大小 | 50+ 或 CPU 核心数的 2–4 倍 |
| innodb_thread_concurrency | InnoDB 线程并发数 | CPU 核心数的 2 倍 |
| innodb_io_capacity / io_capacity_max | I/O 容量（SSD/HDD）| 20000 / 200 |
| max_allowed_packet | 最大数据包大小 | 16MB–64MB |
| wait_timeout / interactive_timeout | 空闲连接超时 | 300–600 秒 |
| sort_buffer_size / join_buffer_size | 排序/join 缓冲区 | 2–4MB / 1–2MB |
| long_query_time | 慢查询阈值 | 1–2 秒 |

#### PostgreSQL（21 个参数）

| 参数 | 说明 | 推荐值依据 |
|------|------|-----------|
| shared_buffers | 共享缓冲区大小 | 总内存的 25% |
| effective_cache_size | 有效缓存大小 | 总内存的 75% |
| maintenance_work_mem | 维护操作内存 | 256MB–1GB |
| work_mem | 工作内存 | (总内存 × 0.25) / max_connections |
| max_connections | 最大连接数 | 200–1000 |
| temp_buffers / wal_buffers | 临时/WAL 缓冲区 | 8MB / 16MB |
| checkpoint_completion_target | 检查点完成目标 | 0.9 |
| max_wal_size / min_wal_size | WAL 大小边界 | 2GB / 256MB |
| random_page_cost | 随机页成本 | 1.1（SSD）/ 4.0（HDD）|
| effective_io_concurrency | 有效 I/O 并发数 | 200（SSD）|
| shared_preload_libraries | 预加载库 | 建议包含 pg_stat_statements |
| track_activities / track_counts | 统计追踪 | on |
| track_io_timing / track_functions | I/O/函数追踪 | on / pl |
| autovacuum | 自动清理 | on |
| log_min_duration_statement | 慢查询日志阈值 | 1000–3000ms |

#### Oracle（12 个参数）

| 参数 | 说明 | 推荐值依据 |
|------|------|-----------|
| memory_target | 内存目标（SGA+PGA）| 物理内存的 85% |
| sga_target / pga_aggregate_target | SGA / PGA 目标 | 总内存的 60% / 25% |
| processes | 最大进程数 | 150 或 CPU 核心数 × 50 |
| open_cursors / session_cached_cursors | 游标设置 | 300–500 / 50 |
| log_buffer | 日志缓冲区大小 | 8–64MB |
| undo_retention | Undo 保留时间 | 3600 秒 |
| fast_start_mttr_target | MTTR 目标 | 300 秒 |
| db_file_multiblock_read_count | 多块读计数 | 128 |
| statistics_level | 统计级别 | TYPICAL |
| control_file_record_keep_time | 控制文件记录保留天数 | 7 天 |

#### SQL Server（6 个参数）

| 参数 | 说明 | 推荐值依据 |
|------|------|-----------|
| max server memory (MB) | 最大服务器内存 | 物理内存的 85% |
| cost threshold for parallelism | 并行开销阈值 | 25–50 |
| max degree of parallelism | 最大并行度 | CPU 核心数的一半 |
| fill factor (%) | 填充因子 | 80–90% |
| recovery interval (min) | 恢复间隔 | 60 分钟 |
| backup compression default | 备份压缩默认 | 1（开启）|

#### DM8（7 个参数）

| 参数 | 说明 | 推荐值依据 |
|------|------|-----------|
| MEMORY_TARGET | 内存目标 | 物理内存的 85% |
| SGA_TARGET / PGA_TARGET | SGA / PGA 目标 | 总内存的 60% / 25% |
| MAX_SESSIONS / OPEN_CURSORS | 会话/游标限制 | 1000 / 500 |
| UNDO_RETENTION | Undo 保留时间 | 3600 秒 |
| BUFFER | 缓冲池大小 | 数据库大小的 30% |

#### TiDB（9 个参数）

| 参数 | 说明 | 推荐值依据 |
|------|------|-----------|
| innodb_buffer_pool_size | InnoDB 缓冲池大小 | 总内存的 70% |
| max_connections | 最大连接数 | 3000 |
| tmp_table_size / max_heap_table_size | 临时/内存表大小 | 256MB；两者应保持一致 |
| innodb_log_file_size / innodb_log_buffer_size | 日志文件/缓冲区 | 256MB / 64MB |
| max_allowed_packet | 最大包大小 | 64MB |
| tidb_hash_join_concurrency / tidb_index_lookup_concurrency | 算子并发数 | 均为 5 |

### 索引健康分析

> 对所有支持的数据库进行三类索引问题检测——缺失索引、冗余/重复索引、长期未使用索引，并生成可操作的修复建议。

#### MySQL

| 分析类型 | 数据来源 | 说明 |
|---------|---------|------|
| 缺失索引 | performance_schema.events_statements_summary_by_digest + table_statistics | 识别高扫描查询和缺少主键的表 |
| 冗余索引 | information_schema.STATISTICS | 查找同列的重复索引 |
| 未使用索引 | performance_schema.table_statistics | 检测读写均为 0 的索引 |

#### PostgreSQL

| 分析类型 | 数据来源 | 说明 |
|---------|---------|------|
| 缺失索引 | pg_stat_statements | 识别高行扫描查询 |
| 冗余索引 | pg_indexes（indexdef 解析）| 查找相同或左前缀匹配的索引对 |
| 未使用索引 | pg_stat_user_indexes（idx_scan=0）| 检测从未扫描的索引 |

#### Oracle

| 分析类型 | 数据来源 | 说明 |
|---------|---------|------|
| 未使用索引 | v$object_usage（MONITORING USAGE）| 检测从未使用的索引 |
| 冗余索引 | dba_ind_columns | 查找首列相同的索引对 |

#### SQL Server

| 分析类型 | 数据来源 | 说明 |
|---------|---------|------|
| 未使用索引 | sys.dm_db_index_usage_stats | 检测 user_seeks+user_scans 为 0 的索引 |
| 冗余索引 | sys.indexes + sys.index_columns | 查找首列相同的索引对 |

#### DM8

| 分析类型 | 数据来源 | 说明 |
|---------|---------|------|
| 冗余索引 | USER_IND_COLUMNS | 查找相同或包含列的索引对 |

#### TiDB

| 分析类型 | 数据来源 | 说明 |
|---------|---------|------|
| 冗余索引 | information_schema.STATISTICS | 查找相同或左前缀匹配的索引对 |

### 系统资源监控

- **CPU**：使用率、核心数、频率
- **内存**：总量、使用量、可用量、使用率
- **磁盘**：各挂载点容量及使用率
- **采集方式**：本地直采或 SSH 远程采集（支持密码/密钥认证）；达梦 DM8 支持 SSH 采集（失败时自动降级为本地采集器）

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

#### DM8（16+ 条规则）

| 维度 | 规则示例 |
|------|---------|
| 表空间 | 使用率 >90%（含自动扩展计算）|
| 内存 | 各缓冲池（KEEP/RECYCLE/FAST/NORMAL/ROLL）配置异常 |
| 会话 | 连接数接近上限 / 长时间运行会话 |
| 事务 | 阻塞事务检测 / 事务等待 |
| 备份 | 备份集缺失 / 备份超时 |
| 参数 | 关键参数（INSTANCE_MODE, COMPATIBLE_VERSION 等）配置检查 |
| 安全 | 用户空密码 / 权限过宽 / 审计未开启 |
| 对象 | 无效对象 / 统计信息陈旧 / 分区表信息 |
| 归档 | 归档模式未开启 / 归档日志堆积 |

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
- **Python**：3.10 及以上
- **通用依赖**：pymysql、psycopg2-binary、python-docx、docxtpl、paramiko、psutil、openpyxl、pandas、flask、flask_socketio
- **Oracle 依赖**：`oracledb`（推荐）或 `cx_Oracle`（需要 Oracle Instant Client）
- **DM8 依赖**：`dmpython`（pip install dmpython）
- **MySQL 权限**：查询 information_schema、performance_schema、mysql 库的只读权限
- **PostgreSQL 权限**：查询 pg_stat_* 系列系统视图及 pg_roles 的只读权限
- **Oracle 权限**：查询 v$* 视图 / dba_* 视图的只读权限；支持 SYSDBA 特权连接
- **DM8 权限**：查询 V$* 系统视图 / DBA_* 管理视图的只读权限；默认端口 5236；连接用户即 Schema（无需 database 参数）
- **SSH（可选）**：用于远程采集系统资源（MySQL / PostgreSQL / Oracle / DM8）；DM8 SSH 采集失败时自动降级为本地采集器

### 安装依赖

```bash
pip install pymysql psycopg2-binary paramiko=4.0.0 openpyxl docxtpl python-docx pandas psutil flask oracledb dmpython flask_socketio

> 💡 DM8 驱动安装提示：
> - `dmpython`：达梦官方提供的纯 Python 驱动（pip install dmpython），推荐使用
> - 连接参数说明：主机 + 端口（默认 5236）+ 用户名（无 database 参数，用户即 Schema）
> - DM8 的 V$ 视图列名与 Oracle 有较大差异，工具已做针对性适配
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
  🗄️  数据库自动化巡检工具  v2.3  统一入口
==================================================
    🐬  1 │ MySQL      MySQL 数据库健康巡检与报告生成
    🐘  2 │ PostgreSQL PostgreSQL 数据库健康巡检与报告生成
    🔴  3 │ Oracle     Oracle 数据库健康巡检与报告生成
    🟡  4 │ DM8        达梦 DM8 数据库健康巡检与报告生成
    📋  5 │ 生成批量巡检模板   生成 MySQL / PostgreSQL / Oracle / DM8 批量巡检 Excel 模板
    🌐  6 │ 启动 Web UI     浏览器可视化操作界面
        7 │ 退出
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
| 1 | 选择数据库类型（🐬 MySQL / 🐘 PostgreSQL / 🔴 Oracle / 🟡 DM8）|
| 2 | 填写连接信息，Oracle 需额外填写服务名/SID，DM8 无需填写 database 名 |
| 3 | 支持在线测试数据库连接（含 SYSDBA 特权验证）|
| 4 | 配置 SSH 采集系统资源（可选，DM8 支持 SSH 采集，失败时自动降级）|
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

#### Skill 文件结构

```
dbcheck/skill/dbcheck/
├── SKILL.md           # Skill 说明
├── security.md        # 安全说明
└── scripts/
    ├── run_inspection.py   # 非交互式入口
    ├── main_mysql.py       # MySQL 巡检逻辑
    ├── main_pg.py         # PostgreSQL 巡检逻辑
    ├── main_oracle.py     # Oracle 巡检逻辑
    ├── main_dm.py         # 达梦 DM8 巡检逻辑
    ├── analyzer.py        # 智能风险分析引擎
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

生成的 Word 报告包含以下章节（DM8 报告结构示例）：

| 章节 | 内容（DM8）|
|------|------|
| 封面 | 数据库名称、服务器地址、版本、主机名、启动时间、巡检人员、平台、报告时间 |
| 第1章 | 数据库基本信息（版本/实例名/服务器版本等）|
| 第2章 | 巡检执行摘要（执行时间、耗时、异常项统计）|
| 第3章 | 表空间使用情况（含各数据文件使用率）|
| 第4章 | 会话与事务（活动会话列表 + 阻塞事务）|
| 第5章 | 内存分析（SGA 缓冲池 + DM8 各缓冲池详情）|
| 第6章 | 重做日志与归档（Redo 状态 + 归档日志）|
| 第7章 | 系统资源监控（CPU/内存/磁盘，本地采集）|
| 第8章 | 对象与用户安全（无效对象/用户列表）|
| 第9章 | 备份与归档（备份集 + 备份设备）|
| 第10章 | 风险与建议（智能分析出的潜在问题及修复 SQL）|
| 第11章 | 报告说明（标准免责声明与建议）|

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

---

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
*图 1：选择数据库类型（MySQL 🐬 / PostgreSQL 🐘 / Oracle 🔴 / DM8 🟡）*

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
> 本项目由 [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git) 改进而来，在原 MySQL 支持的基础上新增了 PostgreSQL、Oracle、DM8 支持。


目前部分功能仍在持续完善中，欢迎共同参与功能开发以及反馈问题与建议。
