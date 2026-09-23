# Changelog

## v26.9.24.0 (2026-09-23)
- **修复 AI 聊天助手「正在巡检…」永远不结束（HGDB / 国产库必现）**
  - **现象**：在 AI 助手聊天框发「巡检 XXX 连接数」后，界面持续转圈「正在巡检」无结果返回；发「列一下所有数据源」答非所问（LLM 凭空编造）。
  - **根因**：聊天两条链路（SSE 流式 `_stream_inspection_response`、非流式 `/api/chat`）启动巡检时**漏写 `db_info['_db_type']`**，而正式巡检入口 `_run_inspection_subprocess` 已正确写入；`run_inspection_task` 取到 None 直接 `return`，却**未把 `tasks[id]['status']` 置为 error**，前端轮询永远拿到 `running` 无限转圈；同时 JVM 类数据库（HGDB/DB2/SQLServer-JDBC 等）此前没接到子进程通道，会退化到进程内起 JVM 钉死 gevent hub。错误事件用 socketio 单播发到聊天未 join 的 room，用户也看不到。
  - **修复**：新增统一分发函数 `_start_chat_inspection_task`（与正式巡检入口对齐）——补 `_db_type` + `JVM_INSPECTION_DB_TYPES` 命中走 `_run_inspection_subprocess` 子进程 + 分发异常兜底 `status='error'`；`run_inspection_task` 缺 `_db_type` 早返回处补 `status='error'` 双保险；删除两处内联 `task_func_map`。
- **新增「平台数据查询」意图（答非所问 → 直接查平台）**
  - 规则命中「列举词（列出/查看/有哪些/所有/数量…）+ 目标词（数据源/实例/连接…）」时，直接调用 `get_instance_manager().get_all_instances(mask_password=True)` 返回真实清单表格（名称/类型/地址），不再交给 LLM 泛答；SSE 与非流式两路均优先于问答分类接入。
  - `parse_intent` 的 db_type 枚举补全 hgdb/kingbase/ivorysql/mariadb/mongodb/highgo 等，并补 `highgo→hgdb`、`kingbasees→kingbase` 归一化，降低 LLM 猜类型概率。
- **AI 助手贯通智能诊断中心与工作流（P1）**
  - 聊天意图分类扩展为 diagnose / workflow / inspect / qa 四类：「诊断 XX」「根因分析」「启动 XX 工作流」等可直接在聊天框触发智能诊断中心（`hub.dispatch / dispatch_stream` 逐事件流式播报协调员/进度/重规划/Reviewer 结论）或启动工作流任务；SSE 与非流式两路均接入。inspect 关键词移除「诊断」消歧，避免误判为普通巡检。
  - 前端聊天流式渲染补 `type=qa` 分支（此前平台查询 SSE 响应被静默丢弃）。
  - 聊天里「巡检某个具体问题」（如「巡检连接数」「分析一下锁等待」「看下内存使用」）自动识别为定向分析，**直通智能诊断中心**而非全量巡检任务；与诊断中心复用同一 `detect_focus_topic`（主题词+意图词双命中，广谱目标仍走普通巡检），确定性规则不依赖 LLM。
- **智能诊断中心新增第 13 专家「定向分析专员」（focus_analyst）**
  - 解决「让分析一个具体问题却甩出一堆无关结论」：主题+意图双命中进入定向模式（连接/锁/慢查询/内存/容量/复制/缓存/计算/IO 共 9 类主题），编排固定「运行监控 → 深度巡检 → 定向分析」并**抑制重规划**追加无关专家；确定性编排优先于 AI 编排。
  - 三层分析降级：BIC-QA 已配置优先知识库分析 → 未配置走已配置 AI 模型从巡检结果分析 → 均不可用给原始相关数据，绝不编造。
  - **定向数据精准抽取**：按 checkdb 巡检结果 context 键名正则只抽取对应主题数据段（如连接 → `sessions`/`hgdb_conn_summary`），不再把全库巡检数据一股脑喂给分析模型；键名覆盖全部库型（MySQL/PG/Oracle/DM 内置 + hgdb/uxdb/db2/sqlserver/redis/mongodb/clickhouse 插件，30 组矩阵回归）。
  - 无关发现不丢弃：折叠进「其他发现」（前端默认收起可展开）；诊断结果与 AI 聊天入口同步生效。
- **修复 HGDB 等 JPype 插件库型定向分析拿不到巡检数据**
  - **根因**：插件路径巡检返回未透出 `context`，且 HGDB 走 JPype/JDBC 采集的行字典**键是 `java.lang.String` 包装对象**，子进程回传 `json.dumps` 序列化崩溃 → 兜底 `ok=False` → 定向分析拿空数据。
  - **修复**：巡检内联与插件路径均透出 `context`（`_sanitize_for_json` 递归转换，dict 键强制转原生类型）；CLI 序列化兜底错误带上异常详情，便于定位。
- **UI 打磨**
  - 智能诊断中心专家卡片：长标题最多两行显示（不再截成一行看不全）；执行顺序角标移到左下角并改亮色橙渐变；定向分析专员卡片图标/主题色；定向分析结论横幅 + 「其他发现」折叠渲染。

## v26.9.23.0 (2026-09-23)
- **受限容器环境 OpenBLAS 多线程创建被拦截，导致容器启动即崩溃（Docker 部署修复）**
  - **现象**：在国网云桌面、企业安全容器、定制 seccomp / LXC 嵌套虚拟化 / K8s 等受限容器里，用 Docker 部署 RaccoonX（DBCheck）启动即报 `OpenBLAS blas_thread_init: pthread_create failed for thread N of M: Operation not permitted`。
  - **根因**：numpy 1.26 内置的 OpenBLAS 在 `import` 阶段按宿主机 CPU 数预建 BLAS 线程池，每建一个线程都要调 `clone()` 系统调用；受限容器底层直接拒绝 `clone()`，导入即崩。该拦截**与 `ulimit` / `--privileged` 无关**——日志里 `RLIMIT_NPROC` 明明是 `65535` 也照样崩，加 `--ulimit nproc=-1` 或 `--privileged` 均无效。
  - **修复**：镜像层（`deploy/Dockerfile`）与启动入口（`web_ui.py`）在 numpy 导入前，默认将 `OPENBLAS_NUM_THREADS` / `OMP_NUM_THREADS` / `MKL_NUM_THREADS` / `NUMEXPR_NUM_THREADS` 设为 `1`，从根上让 OpenBLAS 不建线程池（单线程模式下完全不调 `clone()`）。本项目 numpy 仅用于 openpyxl / 报表等轻量数值场景，单线程几乎无性能损失；CPU 密集场景可用 `-e OPENBLAS_NUM_THREADS=4` 覆盖默认值。
  - **影响范围**：仅影响容器 / 受限 Linux 部署；Windows 直装、普通 Linux 直装不受影响。旧镜像（v26.9.20.1 及之前）需重新拉取 `jackge12345/dbcheck:v26.9.23.0`。

## v26.9.20.1 (2026-09-20)
- 锁定 `JPype1<1.6` 兼容 Java 8（避免 CI 拉到 1.7.1 在 JDK8 机器 `startJVM` 崩溃导致测试连接子进程无结果）；拆分 Oracle 测试连接为独立模块，测试连接子进程不再 import 整个 Flask 应用。

## v26.9.20.0 (2026-09-20)
- RaccoonX（浣巡）品牌升级、SQL 审计 MVP2、智能诊断中心迭代、公告 banner 系统、打赏二维码上线。

## v26.9.15.0 (2026-09-15)
- **告警邮件/Webhook 通知（重磅，678aa95）**：新增 `modules/monitor/alert_notify.py` 告警状态机，完全复用设置页既有邮件 SMTP / 企业微信/钉钉 Webhook 通知配置；**状态迁移才发送**（正常→告警、warn/crit 等级变化、告警→恢复），同一告警绝不重发；服务启动时已处于异常的实例**合并发送一封摘要邮件**（防重启刷屏且不漏报）；多实例同轮告警 SMTP 串行发送防并发拒信；通知主题一眼明确问题（`[DBCheck][宕机] 实例(地址) 错误摘要`）。修复采集快照缺 `id` 字段导致状态机空转的缺陷。
- **通知密码加密根修（678aa95）**：根治「解密邮件密码失败」——`_save_config` 整节点替换曾把首次生成的加密密钥冲掉致密文永久解不开；现显式保留密钥、配置文件原子写（tmp+os.replace）、解密路径不再生成密钥、配置持续损坏时拒绝保存（防清空其他配置节点）、失败提示记忆化只打一次且可操作。
- **监控大屏连线重构（678aa95）**：列主干与主机→实例双竖线合并为**单主干母线**（消除叠加错位），主干恒为主题色、分支短线按节点状态着色（故障只红自身支线并闪烁），流动粒子全线等间距封顶 6 颗（永不交叠）；实例卡片改用**真实数据库 logo**（contain 拟合 + emoji 回退）、错误改右上角斜三角角标（点详情看完整错误）；卡片文案 `fitText` 自适应截断防溢出（319c664）。
- **采集增强（678aa95 / a0a5fef）**：新增 MongoDB / Redis **原生采集通道**（`native_collect.py`，pymongo / redis-py）；GBase8s / DB2 / ClickHouse 补齐复制（repl）与锁等待（locks）指标 SQL；HGDB 等慢查询依赖缺失（如无 `pg_stat_statements` 扩展）按实例记忆化**静默降级**，次轮直达 fallback 不再刷错误日志；SSH 跳板信息在大屏展示。
- **数据源修复（2aea899）**：编辑数据源时库名不回填且保存误变为新增。
- **首页入口**：Hero 区新增「监控大屏」快捷按钮（主题色描边 + 呼吸绿点，9 语言 i18n）。
- **版本统一**：各源文件版本标记 v26.9.14.1 → v26.9.15.0（version.py / version.json / Dockerfile / build 脚本 / CI / login.js / login.html / skill `dbcheck` `scripts/version.py` / deploy 脚本 / release_append）。

## v26.8.20.0 (2026-08-20)
- **版本统一**：各源文件版本标记 v26.8.17.0 → v26.8.20.0（version.py / version.json / Dockerfile `VERSION.txt` / build 脚本 / CI / login.js / skill `dbcheck` `scripts/version.py` / deploy 脚本），消除版本漂移。
- **JDBC 统一连接层（重磅）**：新增 `modules/jdbc_connector.py` 统一连接层，连接测试与 8 类 JDBC 巡检全部隔离到干净子进程执行（`jdbc_test_cli` / `jdbc_inspection_cli`），16 类数据库统一 JDBC 接入（PG 系 / MySQL 系批量收口），根治 gevent 下进程内 JVM/JPype 引发的界面卡死与死锁。
- **JDBC 驱动版本选择**：`driver_registry` 支持按版本解析驱动 jar，5 个 JDBC 插件 `driver_version` 透传横向铺开，DM / GBase 接入驱动管理，修复 clickhouse / uxdb 测试路由。
- **MySQL 系巡检报告修复（d0e3950）**：InnoDB 表空间列表 + 数据库级别权限章节去行转列（改为横向单行表）；修复 MySQL 巡检 InnoDB 表空间查询失败（Unknown table，运行时按版本自适应探测 modern/legacy 表名）。
- **打包 / Docker 修正（7f84b0d）**：P0/P1 打包与 Docker 脚本修正；清理临时脚本并新增 `_tmp_*` 忽略规则（f77ceb8）。
- **详情见**：`docs/release/v26.8.20.0-release-notes.md`。

## v26.8.13.0 (2026-08-13)
- **版本统一**：各源文件版本标记 v26.8.12.1 → v26.8.13.0（version.py / version.json / Dockerfile `VERSION.txt` / build 脚本 / login.js / skill `dbcheck` `scripts/version.py` / README + README_zh 徽章 / deploy 脚本 / release 说明），消除版本漂移。
- **安全加固**：SQL 编辑器强制只读（仅放行 SELECT / SHOW / EXPLAIN 等查询类命令，多语句含非只读整体拒绝 HTTP 403）+ 新增 Redis 只读命令分支（拒绝 DEL / FLUSH* / SET 等写删管理命令）（commit a7ad729）。
- **SQL Server JDBC 连接修复**：`encrypt=false` 不再附 `trustServerCertificate`，服务器强制加密时自动回退 TLS（commit 1a88ba7）。

## v26.8.11.0 (2026-08-11)
- **修复 GitHub Issue #46：MariaDB 10.3 巡检报语法错误**：MariaDB 10.3/10.4 不支持 `SHOW BINLOG STATUS`（该命令 10.5.2+ 才引入），`modules/entrypoints/main_mariadb.py` 在 `log_bin=ON` 时把 `master_status` 替换为该语句，导致执行巡检报 1064 语法错误；统一改为 `SHOW MASTER STATUS`（所有 MariaDB 版本可用，且为 `SHOW BINLOG STATUS` 的别名）。（详见 commit 87b499c）
- **版本统一**：各源文件版本标记 v26.8.10.x → v26.8.11.0（version.py / version.json / Dockerfile `VERSION.txt` / build 脚本 / login.js / skill `dbcheck` `scripts/version.py` / README + README_zh 徽章），消除此前 `version.py`(v26.8.10.2) 与 `version.json`(v26.8.10.3) 的版本漂移。

## v26.8.10.1 (2026-08-10)
- **新增 DBCheck MCP Server（首发，Spike）**：新增 `modules/mcp_server/` 零第三方依赖包，以 JSON-RPC 2.0 over stdio 暴露 `dbcheck.list_instances` 与 `dbcheck.run_inspection` 两个 tool，可被 WorkBuddy / Codex / Claude Desktop 等 MCP 客户端直接调用；stdout 仅承载协议流（散落 print 全量重定向 stderr），响应统一 `ensure_ascii=True` 以规避 Windows 管道 cp936 解码导致的中文乱码；鉴权默认关闭，可用 `DBCHECK_MCP_REQUIRE_AUTH=1` + `DBCHECK_MCP_API_KEY` 开启。
- **首页实例下拉框为空 + 图表「ECharts未加载」修复（f00d04a）**：`initMonitor()` 在 `echarts` 未定义时直接 `return`，致其后 `loadMonitorSummary()` 永不执行、实例列表被连带清空；改为仅降级图表区并继续加载实例列表。同时 ECharts 改为本地 `/static/js/echarts.min.js` 优先、失败回退 CDN（新增 5.5.1 离线包），`loadMonitorSummary()` 抽出 `_fetchMonitorInstances()` 支持 `metrics/summary` → `datasources` 双端点回退且不再静默吞异常，`ensureMonChart()` 补 echarts 守卫。
- **`deploy/release.sh` 版本号校验修复**：正则 `^[0-9]+\.[0-9]+\.[0-9]+$` 仅接受三段版本，四段版本（如 `26.8.10.1`）会被误判为格式错误而直接退出；改为 `^[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$`，与 `release.ps1` 行为对齐。
- **版本号更新**：各源文件版本标记 v26.8.9.1 → v26.8.10.1（version.py / version.json / Dockerfile / build 脚本 / CI / login.js / skill `dbcheck` `scripts/version.py` / deploy 脚本）。
- **详情见**：`docs/release/v26.8.10.1-release-notes.md`（含 WorkBuddy / Codex / Claude Desktop 三种客户端的 MCP 接入配置）。

## v26.8.9.1 (2026-08-08)
- **Web 启动控制台 Banner（cfc2d9d）**：复用 cli.py 的 DBCheck ASCII 图案（仅图案，不含菜单文字）注入 Web 启动流程，图案下方补充版本 / 版权 / 许可证信息；注入 `app.py:main()` 首行，`web_ui.py` 保持 63 行 shim 不变；含 Windows ANSI 兼容与 UnicodeEncodeError 兜底。
- **Oracle 双模板遮蔽修复（a16a29c）**：删除 `plugins/available/oracle_jdbc/sql_templates.json`（历史遗留 7 条裸 list），让 oracle 走 `template_data.json` 的 21 章 / 52 条完整模板。
- **社区版「巡检编排」菜单 404 修复（dc13149）**：原 `pro_available` 被规则引擎探测误判为 True，致社区版显示 flow 菜单且 `/flow` 仅专业版注册而 404；改为 `pro_available=bool(is_pro())` 仅门控 flow，新增 `rules_available` 门控规则引擎，社区版规则引擎 UI 不受影响。
- **版本号更新**：各源文件版本标记 v26.8.8.1 → v26.8.9.1（version.py / version.json / Dockerfile / build 脚本 / CI / login.js / skill `dbcheck` `scripts/version.py` / deploy 脚本）。

## v26.8.8.1 (2026-08-08)
- **定时巡检入口路径错位（`24e126f`）**：`run.py` 以 `PROJECT_ROOT` 作根去加载 `main_xxx.py`，但入口脚本实际在 `modules/entrypoints/`，导致定时任务加载失败（`#43`/`#44`）；新增 `ENTRYPOINT_DIR` 并全量改用，12 个 `run_*` 一并修正。
- **定时巡检报告为空（`bfaa817`）**：各 `run_*` 以空 context 调 `saveDoc.contextsave()` 仅生成空壳 docx；改为统一 `data.generate_report(ofile, inspector_name)` 真实渲染（含误判为已正确的 `run_yashandb`，本次补齐）。
- **补充分发 6 库 + 3 元组解包（`8448aa8`）**：`scheduler.py` 原只分发 6 种内置库，MariaDB/OceanBase/IvorySQL/YashanDB/GBASE/KingbaseES 报「不支持」；补全 6 个分支，并将 `report_file, _ =` 改为 `report_file, *_ =` 修复 `too many values to unpack`。
- **支持插件类型 + 统一报告落盘（`3d30c13`）**：重构为 `runner_map`（13 键）+ `_run_plugin_inspection()`（discover_plugins→按 db_type 匹配已启用插件→main_class 实例化→connect/collect_data/generate_report）；插件类型报告由 `history/<db_type>/` 统一改落 `paths.REPORTS_DIR`（`data/reports`），新增渲染失败兜底断言。
- **深色主题可读性（`a5f1761`）**：`web_templates/index.html` 定时巡检任务卡片 4 处颜色改为 `color: var(--text)`，深色模式下文字/按钮可辨。
- **版本号更新**：各源文件版本标记 v26.8.7.1 → v26.8.8.1（version.py / version.json / Dockerfile / build 脚本 / CI / login.js）。
- **详情见**：`docs/release/v26.8.8.1-release-notes.md`。

## v26.8.7.1 (2026-08-07)
- **版本号更新**：各源文件版本标记 → v26.8.7.1（version.py / version.json / skill `dbcheck` `scripts/version.py` / README + README_zh 徽章 / Dockerfile `VERSION.txt` / `scripts/build.sh` + `scripts/build-multiarch.sh` / CHANGELOG 顶段）；顺带修正此前 `version.py (v26.8.3.1)` 与 `version.json (v26.8.6.1)` 不一致造成的版本漂移。
- **修复用户中心改密双重哈希 (`2c13a9d`)**：`change_password()` 先 `bcrypt` 一次、`user_service.update_user()` 内部再 `bcrypt` 一次，库内存 `bcrypt(bcrypt(pw))`，登录只单次校验 → 新旧密码均无法登录；改为传明文交由 service 层统一单次哈希。
- **修复 SQL Server(JDBC) 路由 (`4ff35a8`)**：显式 `connection_mode='jdbc'` 在 JDBC 环境不可用时静默穿透到 pyodbc 兜底（报 `unixODBC Can't open lib 'SQL Server'`）；改为显式 jdbc 直接报具体原因、`auto` 仍 JDBC 优先→ODBC 兜底，`sqlserver_jdbc` 缺省一律按 jdbc；`main_sqlserver_dual._PROJECT_ROOT` 改走 `paths.PROJECT_ROOT` 并新增 `jdbc_unavailable_reason()`；`main_sqlserver.py` 零修改。
- **补齐 KingbaseES 默认基线 (`51a6bdd`)**：`_builtin_default_baselines()` 新增 `kingbase` 段（13 条，复用 PostgreSQL 基线）；新增 `ensure_missing_db_type_baselines()` 按 db_type 计数>0 整段跳过、纯增量零重置，老库启动自动补齐且不影响自定义基线。
- **`.db_key` 迁移至 `data/` (`d7754ff`)**：`paths.DB_KEY_PATH` 锚定 `DATA_DIR/.db_key`（原 `PROJECT_ROOT/.db_key` 在 frozen 下重装会丢密钥）；新增 `DB_KEY_PATH_LEGACY`，`instance_manager._get_fernet()` 首次启动 `shutil.copy2` 幂等迁移且保留旧文件，历史密码可继续解密。
- **修复巡检配置库路径与密码解密运行时错误 (`4540dee`)**：`inspection.db` frozen 下 `unable to open database file`（统一 `paths.INSPECTION_DB` + 确保父目录 + legacy 迁移）；`._decrypt_pwd` 失败返空串而非密文，新增 `get_all_instances_decrypted()` 供 `metrics_collector`/`monitor/engine` 用明文密码，修复首页实时监控 Oracle JDBC `ORA-01005`/`ORA-28000` 无数据。
- **插件模板/基线种子兼容 (`21ffd36`)**：loader 同时识别 `sql_templates.json`/`template_data.json` 与 `baselines.json`/`baseline_data.json`；新增 `seed_enabled_plugins_data()` 启动时幂等播种，修复打包后 `data/` 为空导致 5 个 JDBC 插件模板缺失。
- **其他 (`d4b6940`)**：修复 `web_templates/index.html` 结果展示相关问题（2 行）。
- **详情见**：`docs/release/v26.8.7.1-release-notes.md`。

## v26.8.3.1 (2026-08-03)
- **版本号更新**：各源文件版本标记 v26.8.2.2 → v26.8.3.1（version.py / version.json / skill `dbcheck` `scripts/version.py` / README + README_zh 徽章 / Dockerfile `VERSION.txt` / `scripts/build.sh` + `scripts/build-multiarch.sh` / CHANGELOG 顶段）。

## v26.8.2.2 (2026-08-02)
- **版本号更新**：各源文件版本标记 v26.8.2.1 → v26.8.2.2（version.py / version.json / skill `dbcheck` `scripts/version.py` / README + README_zh 徽章 / Dockerfile `VERSION.txt` / `scripts/build.sh` + `scripts/build-multiarch.sh` / CHANGELOG 顶段）。
- **巡检报告**：隐藏风险表的「修复建议 SQL」列（保留数据仅移除渲染，含 Oracle 引擎镜像），AI 诊断章新增免责声明「鉴于 AI 分析可能存在错误，请仔细确认 AI 给出的分析结果」（9 语言）。
- **AI 诊断**：修复 `_call_openai` 静默吞错（HTTP 200 带 error / 空 choices / 空 content 改为显式抛错），失败时向 Web 错误卡与 Word 第 8 章展示真实原因；修正 Web 端陈旧的「请检查 Ollama 服务状态」提示为覆盖在线模型 + Ollama 的准确文案（9 语言）。

## v26.8.2.1 (2026-08-02)
- **版本号更新**：各源文件版本标记 v26.8.1.1 → v26.8.2.1（version.py / version.json / skill `dbcheck` `scripts/version.py` / README + README_zh 徽章 / Dockerfile `VERSION.txt` / CHANGELOG 顶段）。

## v26.8.1.1 (2026-08-01)
- **版本号更新**：各源文件版本标记 v26.7.30.1 → v26.8.1.1（version.py / version.json / skill `dbcheck` `scripts/version.py` / README + README_zh 徽章 / Dockerfile `VERSION.txt` / CHANGELOG 顶段）。

## v26.7.30.1 (2026-07-30)
- **版本统一**：各源文件版本标记 v26.7.29.2 → v26.7.30.1（version.py / version.json / skill `dbcheck` `scripts/version.py` / README + README_zh 徽章 / CHANGELOG 顶段），官网（website 分支）同步更新至 v26.7.30.1（index.html meta/og 描述、hero 版本徽章、新增 website `version.json`）。
- **VI 视觉升级与登录页重设计**：整体配色由藏青→科技绿改为藏青→浅蓝（绿色仅作点缀）；SVG 图标系统替代 Font Awesome；官网 hero 区突出 IP 吉祥物浣熊；Web UI 登录页重写为左右分栏布局（品牌区吉祥物 + 玻璃拟态登录卡片）并接入 9 语言 i18n 切换；侧边栏/登录页 logo 去白底并放大。

## v26.7.29.2 (2026-07-29)
- **修复容灾备份（DR）模块镜像依赖缺失**：`requirements-docker.txt` 补回 `requests` 与 `croniter` 两个 DR 模块的硬依赖，修复因镜像 venv 缺包导致 DR 蓝图被静默吞掉、`POST /api/dr/plans` 报 405（前端保存备份计划失败）的问题。
- **版本统一**：各源文件版本标记 v26.7.29.1 → v26.7.29.2（version.py / version.json / Dockerfile / skill `dbcheck` `_meta` + `_skillhub_meta` + `scripts/version.py` / README + README_zh 徽章 / CHANGELOG 顶段）。

## v26.7.29.1 (2026-07-29)
- **新增瀚高 HGDB 巡检（JDBC）**：新增 `plugins/available/hgdb_jdbc` 巡检插件（PostgreSQL 14.20 内核，JDBC 接入，默认端口 5866，默认库 highgo，复用 PG 模板与规则引擎），数据库类型 19 → 20；新增 `pro/rules/builtin/hgdb.yaml`（12 条风险规则），SQL 编辑器 psycopg2 路径接入 HGDB（列库 / 列表视图 / 执行 SQL）；中英文 README 补充 HGDB 支持说明、内置插件表与巡检覆盖表。
- **优炫 UXDB 规则引擎生效修复**：`uxdb_jdbc` 的 `collect_data` 接入 `analyze_with_plugins('uxdb', context)`，使既有 `uxdb.yaml` 规则在巡检时真正触发（此前规则存在但不触发）。
- **版本统一**：各源文件版本标记 v26.7.28.1 → v26.7.29.1（version.py / version.json / Dockerfile / skill `dbcheck` `_meta` + `_skillhub_meta` + `scripts/version.py` / README + README_zh 徽章 / CHANGELOG 顶段），官网（website 分支）同步更新至 v26.7.29.1。

## v26.7.28.1 (2026-07-28)
- **版本统一**：各源文件版本标记 v26.7.26.1 → v26.7.28.1（version.py / version.json / Dockerfile / skill `dbcheck` `_meta` + `_skillhub_meta` + `scripts/version.py` / README + README_zh 徽章 / CHANGELOG 顶段）
- **智能诊断能力下沉社区版**：智能诊断中心、诊断历史、eBPF 内核级监控由专业版下沉至社区版免费提供（多专员协作诊断、诊断结论落库溯源、块设备 IO/CPU 内核级归因）
- **新增优炫数据库（UXDB）巡检**：新增 `plugins/available/uxdb_jdbc` 巡检插件（PostgreSQL 兼容系，JDBC 接入，默认端口 33060，复用 PG 模板与规则引擎），数据库类型 18 → 19
- **其它优化**：首页实例分布图重设计（左侧自定义图例 + 右侧独立圆环）、菜单与权限配置 `menu.flow` 国际化补全、浅色主题打磨、9 语言 i18n 对齐、社区版全面开源（Apache-2.0 + 强化署名）、CI license-header 校验与 GitHub Actions 运行环境升级至 Node 24

## v26.7.26.1 (2026-07-26)
- **版本统一**：各源文件版本标记 v26.7.24.0 → v26.7.26.1（version.py / version.json / Dockerfile / README + README_zh 章节 / CHANGELOG 条目）；官网（website 分支）同步更新至 v26.7.26.1。
- **新增 ClickHouse 巡检支持**：新增 `plugins/available/clickhouse_jdbc` 巡检插件（JPype1 + clickhouse-jdbc 驱动，默认端口 8123），覆盖库/表/对象查询与 SQL 在线执行；新增 `pro/rules/builtin/clickhouse.yaml`（15 条风险规则）与基线配置（9 项参数），复用 `pro.rule_engine` 智能风险分析，支持一键修复 SQL；SQL 编辑器支持 ClickHouse 语法高亮与执行。数据库类型 17 → 18，巡检规则 300+（含 ClickHouse 15 条）。

## v26.7.24.0 (2026-07-24)
- **版本号统一**：各源文件版本标识 v26.7.21.1 → v26.7.24.0（version.py / version.json / Dockerfile / skill `dbcheck` `_meta` + `_skillhub_meta` + `scripts/version.py` / README + README_zh 徽章 / CHANGELOG 顶段）
- **新增 Redis 单机与集群巡检**：新增 `plugins/available/redis` 与 `redis-cluster` 巡检插件（采集 11+ 章节、集群 seed-node 自动发现、规则引擎 `redis.yaml`/`redis-cluster.yaml`、智能分析 + AI 诊断接入），详见公众号文章《DBCheck v26.7.24.0：新增 Redis 单机与集群巡检，KV 版图正式补齐》

## v26.7.21.1 (2026-07-19)
- **版本号统一**：各源文件版本标识 v26.7.19.1 → v26.7.21.1（version.py / version.json / Dockerfile / skill `dbcheck` `_meta` + `_skillhub_meta` + `scripts/version.py` / README + README_zh 徽章 / CHANGELOG 顶段）
- **合并 main → professional（MongoDB 完整功能）**：将 main 分支的 MongoDB 巡检全链路合并至 professional 分支
  - **插件接入钩子**：`pro/metrics_collector.py`（mongodb 计数器 + `_connect` 分支 + `_collect_mongodb`）、`index_health.py`（`analyze_mongodb_indexes`）、`slow_query_analyzer.py`（`MongoDBSlowQueryAnalyzer`）、`inspection_engine.py`（db_type 名称映射 + `MONGO_SECTION_TITLES` + `baseline_results` 白名单修复）、`pro/instance_manager.py`（MongoDB 专用连接配置列）、`web_ui.py`（路由/表单/logo/`smart_analyze` 透传）、`web_templates/index.html`（菜单/表单/NoSQL 徽章）、`i18n`（MongoDB 文案）、`builtin_registry.json`（mongodb 注册）、`requirements.txt`（`pymongo>=4.6`）
  - **插件资产**：`plugins/available/mongodb` 整体升级为社区版（含 80 条 mongodb.yaml 规则）、`pro/rules/builtin/mongodb.yaml`、`templates/mongodb_wordtemplates_v1.0.docx`、`scripts/mongo-test-standalone.*`、`analyzer.py` 新增 `smart_analyze_mongodb`
  - **intelligence/ 专有模块与其它 db_type 逻辑完整保留**

## v26.7.17.1 (2026-07-17)
- **版本号统一**：各源文件版本标识 v26.7.15.1 → v26.7.17.1（version.py / version.json / Dockerfile / skill `dbcheck` `_meta` + `_skillhub_meta` + `scripts/version.py` / README + README_zh 徽章 / CHANGELOG 顶段）
- **合并 main → professional**：将 main 分支的容灾备份模块、NOTICE 合规声明、OceanBase 巡检全链路合并至 professional 分支
  - **容灾备份模块**：基于 autobackup 引擎的定时备份与调度（MySQL/MariaDB/PostgreSQL/文件），含 Cron 调度、保留天数清理、Webhook 通知、健康度评分
  - **OceanBase 巡检全链路**：新增 `main_oceanbase.py` 巡检实现、oceanbase.yaml 规则、Word 模板、监控扩展、UI 全链路支持（dbIcons/dbLogos/portMap/userMap + compat_tag）
  - **规则引擎修复**：`eval(tree)` 替换为 `compile(tree) + eval(code)` 安全求值；新增 `_safe_eval_param()` 参数表达式安全求值
  - **监控扩展**：`pro/metrics_collector.py` 追加 oceanbase 注册与 `_collect_oceanbase()` 方法
- **NOTICE 合规声明**：新增 NOTICE 文件，声明 vendored autobackup 引擎的 MIT 许可

## v26.7.15.1 (2026-07-15)
- **版本号统一**：各源文件版本标识 v26.7.13.1 → v26.7.15.1（version.py / version.json / Dockerfile / skill `dbcheck` `_meta` + `_skillhub_meta` + `scripts/version.py` / README + README_zh 徽章 / CHANGELOG 顶段）
- **专业版 README 更新**：新增「专业版专属能力」详节与「社区版 vs 专业版 · 核心能力对比」表；标题/简介校正为 Professional / Commercial 以匹配专有版性质
- 同步 main 的 MariaDB 原生巡检支持、插件双类型模型（巡检/规则）、插件市场 UI 优化与巡检结果页修复（详见 main 分支发版说明）

## v26.7.13.1 (2026-07-13)
- **同步 main 分支通用修复至 professional**：
  - 修复 `web_ui.py` 巡检目标识别中嵌套引号 f-string 在 Python ≤3.11 下的 SyntaxError
  - 修复打包后 RBAC 初始化失败 `no such table: um_user`（RBAC 库路径改为单层 `dirname(__file__)`，与 `db_manager` 一致；spec `data_dirs` 增加 `db` 目录随包）
  - 清理 PyInstaller `hiddenimports` 无效/错误条目：`flask_cors`、`dmpython`、`python_docx`(保留 `docx`)、`click._bashcomplete`、`defusedxml`、`gevent.wsgi`/`gevent.http`；`runtime-hook-gevent.py` 移除 gevent 1.4+ 已删除子模块导入（哑弹）
  - `yasdb` 驱动改为可选懒加载（缺失时友好报错，不再 `sys.exit` 终止 web 进程）；`monitor_engine.py` 监控路径 `import yasdb` 加 `try/except ImportError` 保护
  - `.gitignore` 增加 `templates/` Word 模板白名单，模板文件纳入版本库
- 同步 main 的 MariaDB / 双类型支持与多项修复（详见下记历史版本说明）

## v26.7.11.1 (2026-07-11)

### 🐛 修复
- **#28 AI 诊断测试连接 500**：主分支（main）「AI 诊断设置」页面点「测试连接」报 500（`TypeError: _probe_openai_model() missing 3 required positional arguments`）。根因为 `@app.route('/api/test_openai')` 装饰器误挂在辅助函数 `_probe_openai_model` 上，移除误挂装饰器后恢复正常
- **实时慢查询 / 活跃连接监控数据源下拉框空**：移植 professional 分支已有的占位逻辑，下拉框不再空白
- **#29 添加 Oracle 数据源 DPY-3015**：扩展 oracledb thin→thick 回退判定（命中 `DPY-3015`），自动切 thick 模式（Oracle Instant Client）
- **#26 插件市场安装 Oracle JDBC 插件回滚**：修复 `load_plugin()` 未将插件目录加入 `sys.path`，改用 `load_plugin_with_error()` 透出真实异常

### 🔧 工程
- 版本号同步：各源文件版本标识 v26.7.11.1 → v26.7.13.1

## v26.7.8.1 (2026-07-08)
- **Oracle (JDBC) 插件路由修正**：`oracle_jdbc` 类型数据源的实时监控改为统一走插件 JDBC 连接（JPype + ojdbc8.jar），彻底不再走 python `oracledb`，避免 Oracle 11g 在无 Oracle 客户端环境下连接失败；监控深采逻辑 `_collect_oracle()` 原样复用（插件 `JdbcConnectionWrapper` 为 DB-API 2.0 兼容）
- **jdbc_url 全链路打通**：前端添加数据源 / 巡检表单新增 `jdbc_url` 输入框；后端测试连接与保存路由补齐 `jdbc_url` 透传；`DatabaseInstance` 新增 `jdbc_url` 字段并落库；插件 `get_connection()` / `test_connection()` 支持完整 JDBC URL（EZConnect / TNS 描述符 / TCPS 原样直连）
- **默认端口修复**：`oracle_jdbc` 插件默认端口由误用的 3306 修正为 1521（plugin.json + api_v1 映射），默认用户 system
- **文案修正**：服务名 / SID 输入框占位提示由"留空则使用 SID"改为"填写服务名或 SID"

## v2.10.0 (2026-07-07)
- 新增「实时监控采集器」：采集器随 Web 进程启动，基于 APScheduler 每 30s 采集一次
- 通用探针：对所有数据库类型做 TCP 连通探测，输出可用性 + 响应延迟
- 深采指标：MySQL/TiDB、PostgreSQL/PG/Kingbase、Oracle、达梦 DM8、SQL Server 采集连接数、QPS/TPS、复制延迟等；计数器型指标自动差分算速率
- 实时推送：通过 flask-socketio 的 `metrics` 事件（room=monitor）推流，前端「实时监控」区 ECharts 实时刷新
- 存储：新增 `metrics_snapshot` 时序表（SQLite），按实例环形裁剪最近 2000 个快照
- 健壮性：连接超时 3s、单实例连续失败断路器退避、单指标采集失败不影响整体循环

### 🐛 修复 (2026-07-08)

- **采集器连接修复**：修复 `mask_password=True` 导致采集器用脱敏密码（`****`）连不上库的问题，改为 `False` 使用真实密码
- **SQL Server / Oracle (JDBC) 深采**：补充 pyodbc 连接分支、新增 `_collect_sqlserver`（会话状态、性能计数器、IO 统计），SQL Server 现在可采集连接数 / 吞吐 / 延迟等深采指标；Oracle (JDBC) 深采分支连通
- **首页监控图表**：
  - `multi()` 扫描全部历史快照键名（不再只看首快照），修复切换实例后吞吐/连接数图表为空
  - 时间轴只显示 `HH:mm`，图例置顶滚动避免与坐标轴重叠
  - 合并 resize 监听器并加防抖 + `requestAnimationFrame`，修复窗口缩放后布局错乱
  - 修复 `innerHTML` 覆盖 canvas 导致切实例失效，改用 ECharts `title` + 污染检测自动重建容器
  - `setOption` 使用 `notMerge:true`，避免切换实例残留旧数据
- **非深采实例空图优化**：不支持深采（或深采临时失败）的实例，原空白图表改为展示「端口可用性」时间线（可达/不可达）与「连通性诊断」仪表盘（可用率 + 真实失败原因），页面不再留白

## v2.9.0 (2026-07-07)

### 🚀 可视化大屏升级（健康态势大屏）

#### ✨ 新增功能
- **健康态势大屏（ECharts 真实数据）**：首页新增「🩺 健康态势大屏」，引入 ECharts 5.5.1，四张真实数据图表
  - 综合健康评分仪表盘（global health score）
  - 风险等级分布环图（critical / high / medium / low / healthy）
  - 健康评分趋势线（近 30 天，来自 `instance_trend` 真实聚合）
  - 实例健康矩阵（各实例最新巡检，按风险等级着色）
- 首页「健康评分」卡片简化为紧凑摘要（分数 + 等级 + 风险标签 + 实例数），与下方大屏主视觉分工、去重
- 主题色自适应：图表颜色读取 CSS 变量，自动适配深/浅色主题；ECharts 未加载时优雅降级提示

#### 🔧 后端
- `/api/pro/dashboard` 移除假数据（随机分类模拟），改为返回真实趋势（`trend`）与实例矩阵（`instances`）

#### 📝 工程
- 版本升级至 v2.9.0

---

## v2.8.2 (2026-07-07)

### 🚀 DM8 离线存储检查增强

#### ✨ 新增功能
- **数据块损坏分析（零侵权）**：基于通用二进制信号识别可疑坏块——全零页（ZERO_PAGE）、整页单一字节异常填充（CONSTANT_FILL）、文件末页不足页大小（TRUNCATED）；不读取任何 DM8 页头私有偏移，规避 GPL 协议风险
  - 坏块自动归属表空间（复用 dm.ctl 解析结果）
  - 输出损坏页清单：数据文件、物理页号、文件偏移、损坏类型、所属表空间
  - 损坏率统计（全零 / 异常填充 / 截断占比）
- **报告落盘 reports 目录**：DM8 离线检查 Word 报告生成至 `reports/`，纳入统一报告列表管理
- **Web UI 查看坏块**：结果页新增「数据块损坏」标签页，展示坏块统计卡片与清单表

#### 📝 文档
- 中英文 README 新增 DM8 离线存储检查功能说明

---

## v2.8.1 (2026-07-06)

### 🚀 DM8 离线存储检查（新增模块）

#### ✨ 新增功能
- **DM8 离线存储健康检查**：数据库实例无需启动即可检查达梦 DM8 存储健康
  - 本地检查 + 通过 SSH 远程检查（paramiko）
  - 8 步流水线：验证目录 → 发现文件 → 检测页大小 → 分析数据文件 → 解析控制文件 → 交叉校验 → 检查 SYSTEM.DBF → 目录级诊断
  - 独立实现，不使用第三方逆向代码，零协议风险
  - 生成 Word 格式报告（含本机 / SSH 模式及连接信息）

#### 🐛 修复
- 修复侧边栏导航项不显示问题（菜单权限系统隐藏未注册菜单）

---

## v2.8.0 (2026-07-03)

### 🚀 插件体系重构（Phase 1：插件完全独立）

#### ✨ 新增功能
- **插件生命周期管理**：新增 `on_install()` 和 `on_uninstall()` 生命周期方法
  - 插件安装时自动初始化模板、基线、规则引擎等数据
  - 插件卸载时自动清理所有关联数据（模板、基线、规则）
  - 支持插件完全独立，不依赖平台初始化逻辑
- **插件数据独立存储**：
  - 插件自带 `template_data.json`（巡检模板数据）
  - 插件自带 `baseline_data.json`（基线配置数据）
  - 插件自带规则引擎文件（如 `oracle_jdbc.yaml`）
- **插件卸载数据清理**：
  - 支持 `plugin.json` 配置 `cleanup` 字段，指定卸载时清理的数据库类型和数据类型
  - 自动删除插件创建的预置模板（`is_preset=1`，需 `force=True`）
  - 自动删除插件创建的基线配置

#### 🔧 技术改进
- 插件基类（`InspectionPlugin`）新增 `on_install(db_path)` 和 `on_uninstall(db_path)` 方法
- 插件市场（`PluginMarket`）安装/卸载时自动调用生命周期方法
- 插件配置支持 `cleanup` 配置段，定义卸载清理规则

---

### ✨ 新增数据库插件

#### MongoDB 插件
- 支持 MongoDB 4.0+ 数据库连接和巡检
- 基于 PyMongo 驱动
- 提供基础巡检模板（连接状态、数据库状态、慢查询等）

#### Oracle JDBC 插件
- 支持 Oracle 11g/12c/19c/21c+ 实例
- 基于 JDBC (JPype) 连接，数据驱动运行模式
- **完整移植 Oracle 11g 巡检模板**：
  - 21 个巡检章节（数据库概况、实例状态、表空间、内存、进程、锁等待、AWR、备份、安全等）
  - 58 个 SQL 查询
  - 11 条基线配置
  - 完整规则引擎文件（`oracle_jdbc.yaml`）
- 显示名称优化：改为 "Oracle (JDBC)"，避免与原 Oracle 插件混淆

---

### 🐛 修复问题

#### 插件系统
- 修复插件安装后模板和基线数据未创建问题
  - 修复 `sql_templates.json` 格式错误（改为正确数组格式）
  - 修复 `json` 模块未导入问题
  - 修复 `on_install()` 不幂等问题（重复调用不再创建重复数据）
- 修复卸载插件后模板未删除问题
  - 修复 `delete_template()` 默认不删除 `is_preset=1` 模板（传入 `force=True`）
  - 修复卸载时插件实例可能不在内存中的问题（改为读取 `plugin.json` 的 `cleanup` 配置）
- 修复插件显示名称重复问题（Oracle 与原 Oracle 11g 冲突）

#### 数据库
- 修复数据库文件路径问题（根目录出现 `inspection.db`，应为 `data/inspection.db`）
- 更新 `.gitignore`，忽略根目录的 `inspection.db`

---

### 📚 文档和代码质量

#### 文档整理
- 创建 `docs/` 目录，按功能分类存放开发文档：
  - `docs/design/` - 设计文档
  - `docs/release/` - 版本发布记录
  - `docs/plugin/` - 插件开发文档
  - `docs/deploy/` - 部署文档
  - `docs/install/` - 安装文档
- 移动根目录的开发文档（除 `README.md` 和 `CHANGELOG.md`）

#### 代码清理
- 删除临时调试脚本（`__pycache__`、`.pyc` 文件、probe 脚本等）
- 删除无用的临时测试/调试/修复脚本
- 提交 JDBC 驱动文件（`drivers/ojdbc6.jar`、`drivers/ojdbc8.jar`）

---

### 🔧 优化改进
- 插件市场交互优化：安装插件后自动初始化数据，卸载后自动清理数据
- 国际化优化：新增 `oracle_jdbc` 显示名称和描述（`i18n/zh.py`）
- 版本号更新：`version.json` 更新为 `v2.8.0`

---

## v2.6.3 (2026-06-24)

### Phase 1：流式输出 + Markdown 渲染（基础设施）

#### 🐛 修复问题
- 修复 Ollama 流式读取问题（`resp.read(1)` 逐字节读取不可靠 → 改为 `resp.read(4096)` 缓冲读取）
- 修复空 chunk 发送到前端问题（Ollama 中间状态帧 `response: ""` 不再发送）
- 修复 `index.html` 中 7 处 JS 语法错误（正则 `<` `>` 改用 `new RegExp()` 避免 HTML 解析器误判）
- 增加流式 fallback 逻辑：流式返回空内容时，自动用非流式模式重试
- 新增调试日志（`[AI Stream]` 前缀），方便定位前后端问题）

#### ✨ 新增功能
- AI 聊天 Markdown 渲染正常（加粗、代码块、标题、列表、链接、表格、水平分割线）
- SSE 流式输出正常（`start → chunk → done`）
- Thinking... 提示：等待 AI 回复时显示 "💭 Thinking..."
- 思考过程展示：qwen3 模型的 thinking 字段实时推送到前端，可折叠查看

### Phase 2：多轮对话优化（会话历史持久化 + LLM 摘要）

#### ✨ 新增功能
- **会话历史持久化**：聊天历史保存到 `pro.db` 的 `chat_history` 表，Flask 重启不丢失
- **懒加载**：`_get_chat_session()` 优先内存，缺失时从 DB 加载
- **LLM 摘要**：历史超过 20 条时，自动用 LLM 摘要旧消息，节省 token
- **DB 同步**：清空会话时同时删除 DB 记录，保持一致性

#### 🔧 优化改进
- `_add_to_history()` 同时写入内存和 DB，保证一致性
- `_summarize_history_if_needed()` 保留最近 6 条原文 + 摘要，平衡上下文完整性和 token 消耗

### Phase 3：AI 智能意图识别 + 巡检深度集成

#### ✨ 新增功能
- **自动意图分类**：AI 自动识别用户消息是「问答」还是「巡检」，无需手动切换模式
  - 关键词匹配（巡检/检查/诊断/连接数/锁等待/慢查询...）→ 走巡检
  - LLM fallback 分类（关键词未命中时调用 LLM 判断）
- **巡检直接执行**：
  - 解析用户自然语言 → 匹配数据源 → 执行简单查询或启动全库巡检
  - 简单查询（连接数/锁等待/慢查询）：直接返回结果，通过 SSE 展示
  - 全库巡检：启动后台任务，前端轮询进度
  - 多数据源时弹出选择按钮让用户确认
- **SSE 新事件类型**：`inspect_result`（查询结果）、`inspect_start`（任务启动）、`inspect_ask`（需选数据源）

#### 🔧 Markdown 渲染增强
- 新增 `####` 四级标题渲染
- 新增 `---` / `***` / `___` 水平分割线渲染
- 新增 `| col | col |` 表格渲染
- 加粗正则优化（避免误匹配斜体）

---

## v2.6.2 (2026-06-23)

### 插件系统核心功能

#### ✨ 新增功能
- 插件注册表（`plugin_registry.json`）+ 插件元数据（`plugin.json`）
- 插件安装 / 卸载 / 启用 / 禁用 / 配置
- Web UI 插件管理页面（`/plugin-manager`）
- 插件与巡检引擎解耦，支持独立开发和分发

#### 🔧 优化改进
- 插件市场交互（浏览、安装、更新）
- 插件依赖检查和冲突检测

---

## v2.6.1 (2026-06-20)

### AI 聊天基础功能

#### ✨ 新增功能
- AI 聊天侧边栏（可折叠）
- 支持 Ollama 本地 LLM（qwen3 等）
- 支持 OpenAI 兼容的远程 API
- 会话历史（内存，重启丢失）

---

## v2.6.0 (2026-06-18)

### 首个公开版本

#### ✨ 新增功能
- Web UI（Flask + Jinja2 + Bootstrap 5）
- 多数据库支持（MySQL/PostgreSQL/Oracle/SQL Server/TiDB/DM8/GBase 8s 等）
- 巡检模板管理
- SQL 编辑器
- 巡检任务调度
- 报告生成（HTML/PDF）
- 驱动下载和 ODBC 检测
