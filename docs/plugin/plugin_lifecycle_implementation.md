# 插件生命周期管理完善总结

## 修改内容

### 1. 修改 `plugin_core.py`
- 修改 `InspectionPlugin` 基类中的 `on_install()` 和 `on_uninstall()` 方法，添加 `db_path` 参数
- 这样插件可以访问数据库，初始化和清理数据

### 2. 修改 `plugin_market.py`
- 修改 `install()` 函数，在调用插件的 `on_install()` 方法时传入 `db_path` 参数
- 修改 `uninstall()` 函数，在调用插件的 `on_uninstall()` 方法时传入 `db_path` 参数
- 这样插件可以知道数据库文件路径，进行数据操作

### 3. 完善 `oracle_jdbc` 插件的 `on_install()` 方法
- 实现完整的数据初始化逻辑
- 创建模板（使用 `create_template()`）
- 创建章节（使用 `create_chapter()`）
- 从 `sql_templates.json` 读取 SQL 模板，创建查询（使用 `create_query()`）
- 初始化基线数据（使用 `init_default_baselines()`）

### 4. 完善 `oracle_jdbc` 插件的 `on_uninstall()` 方法
- 使其接收 `db_path` 参数
- 使用 `delete_template()` 和 `delete_baseline()` 函数清理数据
- 这样卸载插件时，会自动清理模板和基线数据

### 5. 同步文件
- 将 `available/oracle_jdbc/main_plugin.py` 同步到 `enabled/oracle_jdbc/main_plugin.py`

## 测试步骤

1. 重启 DBCheck 服务
2. 卸载 `oracle_jdbc` 插件（如果已安装）
3. 安装 `oracle_jdbc` 插件
4. 检查数据库中的 `inspection_template`、`inspection_chapter`、`inspection_query` 表，确认有 `oracle_jdbc` 的数据
5. 卸载 `oracle_jdbc` 插件
6. 检查数据库中的表，确认 `oracle_jdbc` 的数据已被清理

## 注意事项

- `on_install()` 方法中调用了 `init_default_baselines()` 函数，它会为所有数据库类型初始化基线数据
- 如果只需要初始化 `oracle_jdbc` 的基线数据，需要修改 `init_default_baselines()` 函数，或者创建新的函数
- 当前实现会为所有数据库类型初始化基线数据，这可能是正确的行为（因为其他插件可能还没有初始化基线数据）
