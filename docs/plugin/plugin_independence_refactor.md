# 插件独立化改造总结

## 问题描述
用户指出：插件跟平台的初始化基线和模板不应该走一个方法，插件是独立的，不应该跟平台有耦合。

## 解决方案

### 1. 创建插件独立的配置文件

#### `sql_templates.json`
- 位置：`plugins/available/oracle_jdbc/sql_templates.json`
- 内容：Oracle JDBC 插件专用的 SQL 模板
- 格式：`{ "query_key": "SQL 语句" }`

示例内容：
```json
{
  "version": "SELECT version FROM v$instance",
  "instance_name": "SELECT instance_name, host_name, status FROM v$instance",
  "tablespace_usage": "SELECT ..."
}
```

#### `baseline.json`
- 位置：`plugins/available/oracle_jdbc/baseline.json`
- 内容：Oracle JDBC 插件专用的基线配置
- 格式：数组，每个元素是一个基线配置对象

示例内容：
```json
[
  {
    "param_name": "version",
    "query_sql": "SELECT version FROM v$instance",
    "operator": ">=",
    "expected_value": "11.2.0.4",
    "risk_level": "LOW",
    "description_zh": "Oracle 版本应不低于 11.2.0.4"
  }
]
```

### 2. 修改 `on_install()` 方法

**修改前**（耦合平台）：
```python
def on_install(self, db_path=None):
    # 调用平台的函数
    init_default_baselines(db_path=db_path)  # ❌ 耦合平台
```

**修改后**（独立）：
```python
def on_install(self, db_path=None):
    # 1. 创建模板（插件独立定义）
    template_id = create_template(...)
    
    # 2. 创建章节（插件独立定义）
    chapter_id = create_chapter(...)
    
    # 3. 从插件独立的 sql_templates.json 读取并插入查询
    with open('sql_templates.json', 'r') as f:
        sql_templates = json.load(f)
    for key, sql in sql_templates.items():
        create_query(...)
    
    # 4. 从插件独立的 baseline.json 读取并插入基线
    with open('baseline.json', 'r') as f:
        baseline_configs = json.load(f)
    for config in baseline_configs:
        create_baseline(...)
```

### 3. 修改 `on_uninstall()` 方法

**修改后**（独立）：
```python
def on_uninstall(self, db_path=None):
    # 1. 清理模板数据（仅清理 oracle_jdbc 的模板）
    templates = get_templates_by_db_type('oracle_jdbc')
    for t in templates:
        delete_template(t['id'], db_path=db_path)
    
    # 2. 清理基线数据（仅清理 oracle_jdbc 的基线）
    baselines = get_baselines_by_db_type('oracle_jdbc')
    for b in baselines:
        delete_baseline(b['id'], db_path=db_path)
```

## 关键改进

### 1. 完全独立
- ✅ 插件有自己的 `sql_templates.json` 和 `baseline.json`
- ✅ 插件不调用平台的 `init_default_baselines()` 函数
- ✅ 插件只操作属于自己的数据（db_type='oracle_jdbc'）

### 2. 使用标准 DAL 函数
- ✅ 使用 `create_template()` 创建模板
- ✅ 使用 `create_chapter()` 创建章节
- ✅ 使用 `create_query()` 创建查询
- ✅ 使用 `create_baseline()` 创建基线
- ✅ 使用 `delete_template()` 删除模板
- ✅ 使用 `delete_baseline()` 删除基线

### 3. 数据隔离
- ✅ `on_install()` 只创建 `db_type='oracle_jdbc'` 的数据
- ✅ `on_uninstall()` 只删除 `db_type='oracle_jdbc'` 的数据
- ✅ 不影响其他数据库类型的模板和基线

## 文件清单

### 新增文件
1. `plugins/available/oracle_jdbc/sql_templates.json` - SQL 模板配置
2. `plugins/available/oracle_jdbc/baseline.json` - 基线配置

### 修改文件
1. `plugins/available/oracle_jdbc/main_plugin.py` - 完善 `on_install()` 和 `on_uninstall()`
2. `plugins/enabled/oracle_jdbc/main_plugin.py` - 同步修改
3. `plugins/enabled/oracle_jdbc/sql_templates.json` - 同步新增
4. `plugins/enabled/oracle_jdbc/baseline.json` - 同步新增

## 测试步骤

1. **重启 DBCheck 服务**
2. **卸载 oracle_jdbc 插件**（如果已安装）
3. **安装 oracle_jdbc 插件**
4. **检查数据库**（SQLite `data/inspection.db`）：
   - `inspection_template` 表应有 `db_type='oracle_jdbc'` 的模板
   - `inspection_chapter` 表应有对应章节
   - `inspection_query` 表应有从 `sql_templates.json` 读取的查询
   - `inspection_baseline` 表应有从 `baseline.json` 读取的基线
5. **卸载插件**，确认上述数据被清理

## 优势

1. **完全独立**：插件不依赖平台的初始化逻辑
2. **易于维护**：SQL 模板和基线配置在 JSON 文件中，易于修改
3. **数据隔离**：插件只操作自己的数据，不影响其他插件
4. **可扩展性**：其他插件可以复用这个模式

## 注意事项

- 当前 `sql_templates.json` 只包含 7 个基础 SQL 查询
- 可以根据需要添加更多 SQL 查询到 `sql_templates.json`
- `baseline.json` 只包含 4 条基础基线配置
- 可以根据需要添加更多基线配置到 `baseline.json`
