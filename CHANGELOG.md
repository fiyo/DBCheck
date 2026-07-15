# Changelog

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
