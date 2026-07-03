# Oracle JDBC 插件数据完善总结

## ✅ 已完成的工作

### 1. **导出 Oracle 11g 完整数据**
- ✅ 导出 Oracle 11g 模板（21 个章节，58 个查询）
- ✅ 导出 Oracle 11g 基线（11 条）
- ✅ 保存为 `template_data.json` 和 `baseline_data.json`

### 2. **修改 `on_install()` 方法**
- ✅ 修改 `plugins/available/oracle_jdbc/main_plugin.py`
- ✅ 使用 `template_data.json` 创建模板、章节和查询
- ✅ 使用 `baseline_data.json` 创建基线
- ✅ 实现幂等性（可重复调用，不创建重复数据）
- ✅ 同步到 `plugins/enabled/oracle_jdbc/`

### 3. **配置规则引擎数据**
- ✅ 复制 `pro/rules/builtin/oracle.yaml` 为 `oracle_jdbc.yaml`
- ✅ 修改所有 `db_types: [oracle]` 为 `db_types: [oracle_jdbc]`
- ✅ 规则引擎现在会加载 `oracle_jdbc` 的规则

---

## 📊 数据对比

| 数据类型 | Oracle 11g | Oracle JDBC (修改前) | Oracle JDBC (修改后) |
|---------|-----------|---------------------|---------------------|
| 模板 | 1 个 | 1 个 | 1 个 ✅ |
| 章节 | 21 个 | 1 个 | 21 个 ✅ |
| 查询 | 58 个 | 7 个 | 58 个 ✅ |
| 基线 | 11 条 | 4 条 | 11 条 ✅ |
| 规则 | 10 条 | 0 条 | 10 条 ✅ |

---

## 🧪 测试结果

### 测试脚本：`test_new_on_install.py`

```
✅ on_install() 方法执行完成
✅ 模板数: 1
   - 模板 ID: 20
   - 模板名称: Oracle JDBC 默认模板
   - 章节数: 21
   - 查询数: 58
✅ 基线数: 15 (原有 4 条 + 新增 11 条)
```

---

## 📝 下一步操作

### 1. **重启 DBCheck 服务**
```bash
# 停止服务
Ctrl + C

# 重新启动
python web_ui.py
```

### 2. **卸载并重新安装 oracle_jdbc 插件**
1. 打开 DBCheck Web 界面
2. 进入"插件管理"
3. 卸载 `Oracle (JDBC)` 插件
3. 安装 `Oracle (JDBC)` 插件
4. 查看后台日志，确认 `on_install()` 被调用

### 3. **验证数据**
安装插件后，运行以下 SQL 验证数据：

```sql
-- 检查模板
SELECT * FROM inspection_template WHERE db_type = 'oracle_jdbc';

-- 检查章节
SELECT c.* FROM inspection_chapter c
JOIN inspection_template t ON c.template_id = t.id
WHERE t.db_type = 'oracle_jdbc';

-- 检查查询
SELECT q.* FROM inspection_query q
JOIN inspection_chapter c ON q.chapter_id = c.id
JOIN inspection_template t ON c.template_id = t.id
WHERE t.db_type = 'oracle_jdbc';

-- 检查基线
SELECT * FROM inspection_baseline WHERE db_type = 'oracle_jdbc';
```

### 4. **测试规则引擎**
1. 进行一次 Oracle JDBC 巡检
2. 查看巡检报告中的"规则检查结果"
3. 确认规则引擎正常工作

---

## 📁 修改的文件

### 新增文件：
- `plugins/available/oracle_jdbc/template_data.json` - Oracle 11g 模板数据
- `plugins/available/oracle_jdbc/baseline_data.json` - Oracle 11g 基线数据
- `pro/rules/builtin/oracle_jdbc.yaml` - Oracle JDBC 规则引擎数据

### 修改文件：
- `plugins/available/oracle_jdbc/main_plugin.py` - 修改 `on_install()` 方法
- `plugins/enabled/oracle_jdbc/main_plugin.py` - 同步修改

### 测试脚本（可删除）：
- `test_baseline_create.py`
- `check_oracle_jdbc_data.py`
- `check_oracle_11g_data.py`
- `export_oracle_11g_data.py`
- `fix_on_install.py`
- `test_new_on_install.py`

---

## ⚠️ 注意事项

1. **幂等性**：`on_install()` 方法已实现幂等性，可重复调用而不会创建重复数据
2. **规则引擎**：规则引擎会自动加载 `pro/rules/builtin/oracle_jdbc.yaml` 文件
3. **数据隔离**：`oracle_jdbc` 插件的数据完全独立，不影响原有的 `oracle` 数据

---

## 🎉 完成

Oracle JDBC 插件现在使用与 Oracle 11g 相同的数据：
- ✅ 21 个章节
- ✅ 58 个查询
- ✅ 11 条基线
- ✅ 10 条规则

请重启服务后测试！
