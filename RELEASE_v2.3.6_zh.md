# DBCheck v2.6 - 版本发布说明

## 新功能

### 慢查询深度分析
- 新增 `slow_query_analyzer.py` 核心模块，支持各数据库慢查询深度分析
- MySQL / PostgreSQL / Oracle / DM8 / SQL Server / TiDB 均支持慢查询分析
- `checkdb()` 流程中 AI 诊断之后自动执行慢查询分析
- analyzer.py 新增风险规则（MySQL 规则17+，PG 规则11+）

### 数据库历史指标存储升级
- 历史数据从 `history.json` 迁移至 SQLite（`db_history.py` 的 `SQLiteHistoryManager`）
- 查询性能提升，支持更大规模历史数据
- `history.json` 旧文件可安全删除（不再被读取）

---

## 改进

### 报告样式优化

#### TiDB 巡检报告（main_tidb.py）
- 第 1-6 章表格宽度自适应 100%（`autofit = True`）
- 表头统一样式：蓝色背景（`#336699`）+ 白色粗体 + 居中
- 新增 `_style_header(table)` 函数，6 个表格统一调用
- 修复 `_add_config_table` 中调用不存在的 `_set_table_header` 方法的问题

#### DM8 巡检报告（main_dm.py）
- 第 17 章格式规范化：`## 重点关注` / `## 优化建议` / `## 整体评价` 作为二级标题，自动添加序号（17.1 / 17.2 / 17.3）
- 列表项 `- 问题定位` / `- 原因分析` / `- 修复方案` 去掉 `-` 并加粗显示
- Markdown 粗体（`**等待事件 Top5**`）正确转为 Word 加粗格式

### i18n 国际化完善
- 修复 `col3` / `fix_sql` 翻译映射问题（全角中文标点匹配）
- 修复 MySQL / PG `WordTemplateGenerator` 类缺失 `_t` 方法的问题
- 修复 `main_pg.py` `getData` 类缺失 `_t` 方法的问题
- 修复 f-string 异常解析 bug（`f"message: {e}"` 在异常消息含 `{}` 时报错，改为 `%s` 格式）

### 打包配置（dbcheck.spec）
- `datas` 新增 `i18n/` 目录（之前仅作为 hiddenimports，运行时会缺少翻译文件）
- `hiddenimports` 新增：`pdf_export`、`index_health`、`config_baseline`
- 删除过时残留文件 `main.spec`（`project_root` 写死 Linux 路径，仅含 MySQL 单模块）

---

## 修复

### TiDB（main_tidb.py）
- 修复全局替换 `table.autofit = True` 时缩进被破坏的问题（12 空格替代 8 空格，影响第 917-1395 行共 14 处）
- 已同步修复至 `skill/dbcheck/scripts/main_tidb.py`

### DM8（main_dm.py）
- 修复 SyntaxError：`unterminated string literal`（第 1442 行换行符被写入字符串）

### f-string 异常解析
- 所有 `f"message: {e}"` 改为 `%s` 格式，修复异常消息含 `{}` 时报错的问题
- 影响范围：`analyzer.py`、`main_mysql.py`、`main_pg.py` 等

---

## 其他

- 新增 `.gitattributes`，统一项目行尾符为 LF，消除 Windows/Linux 混用导致的 diff 噪音
- `skill/dbcheck/scripts/` 同步最新改动：`version.py`、`desensitize.py`
- `web_ui.py` Flask 应用 SSE 推送任务进度（已有功能，本次无改动）
- AI 诊断继续仅支持本地 Ollama（`localhost:11434`），符合安全合规要求

---

## 升级建议

1. **删除旧文件**：可安全删除项目根目录下的 `history.json`（已被 SQLite 替代）
2. **重新生成 Word 模板**：如报告样式未生效，删除 `templates/wordtemplates_v2.0.docx` 让其重新生成
3. **PyInstaller 打包**：请使用 `dbcheck.spec`（不要用已删除的 `main.spec`）

---

*DBCheck 团队 | 2026-04-30*
