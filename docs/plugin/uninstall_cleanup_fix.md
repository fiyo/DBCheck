# Oracle JDBC 插件卸载数据清理修复总结

## 问题描述
用户反馈：卸载 `oracle_jdbc` 插件后，"Oracle JDBC 默认模板"没有被删除。

## 根本原因
1. **`delete_template()` 默认不删除预置模板**  
   - `is_preset=1` 的模板需要传入 `force=True` 才能删除
   
2. **卸载时 `on_uninstall()` 没有被调用**  
   - `plugin_market.py` 的 `uninstall()` 方法在插件未启用时，直接返回"未启用，无需卸载"
   - 没有执行数据清理逻辑

## 修复方案

### 1. 修改 `plugin.json`，添加 `cleanup` 配置
**文件**：`plugins/available/oracle_jdbc/plugin.json`

```json
{
  "name": "Oracle (JDBC)",
  "cleanup": {
    "db_types": ["oracle_jdbc"],
    "data_types": ["template", "baseline"]
  }
}
```

**作用**：指定卸载时要清理的数据类型和数据库类型。

---

### 2. 修改 `plugin_market.py` 的 `uninstall()` 方法
**文件**：`plugin_market.py`（第 536-628 行）

**修改内容**：
1. 读取 `plugin.json` 中的 `cleanup` 配置
2. 根据配置清理数据（不依赖插件实例）
3. 调用 `delete_template()` 时传入 `force=True`（删除预置模板）
4. 无论插件是否启用，都执行数据清理

**关键代码**：
```python
# 清理模板数据
if 'template' in data_types:
    for db_type in db_types:
        templates = get_templates_by_db_type(db_type, db_path=DEFAULT_DB_PATH)
        if templates:
            for t in templates:
                # 强制删除（包括预置模板）
                delete_template(t['id'], db_path=DEFAULT_DB_PATH, force=True)
```

---

### 3. 修改 `plugin_market.py` 的 `install()` 方法
**文件**：`plugin_market.py`（第 288-440 行）

**修改内容**：
在插件启用后（复制到 `enabled/` 目录并加载成功），调用 `on_install()` 方法。

**修改位置**：
1. 直接从 `available/` 启用时（第 311-333 行）
2. 从市场下载安装时（第 401-435 行）
3. 从本地安装时（第 514-534 行）

---

### 4. 修复 `on_install()` 方法的参数名错误
**文件**：`plugins/available/oracle_jdbc/main_plugin.py`（第 483-491 行）

**错误**：
```python
template_id = create_template(
    template_name_zh=template_info.get('template_name_zh', ''),  # ❌ 错误参数名
    ...
)
```

**修复**：
```python
template_id = create_template(
    template_name=template_info.get('template_name_zh', ''),  # ✅ 正确参数名
    ...
)
```

---

## 测试验证

### 测试脚本
`test_full_install_uninstall.py` - 完整的安装+卸载测试

### 测试结果
```
✅ 测试通过！
   - 安装时数据初始化成功（on_install 工作）
   - 卸载时数据清理成功（uninstall 工作）
```

### 数据对比

| 数据类型 | 安装后 | 卸载后 |
|---------|--------|--------|
| 模板数 | 1 | 0 |
| 章节数 | 21 | 0 |
| 查询数 | 52 | 0 |
| 基线数 | 11 | 0 |

---

## 修改文件清单

### 1. 插件代码
- `plugins/available/oracle_jdbc/main_plugin.py` - 修复 `on_install()` 参数名
- `plugins/available/oracle_jdbc/plugin.json` - 添加 `cleanup` 配置
- `plugins/enabled/oracle_jdbc/` - 同步修改

### 2. 平台代码
- `plugin_market.py` - 修改 `install()` 和 `uninstall()` 方法

### 3. 测试脚本（可删除）
- `test_full_install_uninstall.py` - 完整的安装+卸载测试

---

## 使用说明

### 安装插件
1. 打开 DBCheck Web 界面
2. 进入"插件管理"
3. 点击"安装"按钮（插件 ID: `oracle_jdbc`）
4. 安装成功后，会自动创建模板、章节、查询和基线数据

### 卸载插件
1. 打开 DBCheck Web 界面
2. 进入"插件管理"
3. 点击"卸载"按钮（插件 ID: `oracle_jdbc`）
4. 卸载成功后，会自动清理模板、章节、查询和基线数据

---

## 注意事项

1. **`cleanup` 配置是可选的**  
   - 如果插件没有 `cleanup` 配置，则尝试调用 `on_uninstall()` 方法
   - 建议所有插件都添加 `cleanup` 配置，确保卸载时数据被清理

2. **`force=True` 会删除预置模板**  
   - 如果插件创建了 `is_preset=1` 的模板，卸载时必须传入 `force=True`
   - 否则模板不会被删除

3. **测试通过后再部署**  
   - 修改插件代码后，先运行测试脚本验证
   - 确保安装和卸载都能正确工作

---

## 后续优化建议

1. **在插件管理界面显示数据清理状态**  
   - 卸载时，显示"正在清理数据..."
   - 卸载完成后，显示"数据已清理"

2. **支持自定义数据清理逻辑**  
   - 在 `plugin.json` 中添加 `cleanup_sql` 字段
   - 支持自定义 SQL 语句清理数据

3. **添加卸载确认提示**  
   - 卸载插件时，弹出确认提示："卸载后会删除插件创建的数据，是否继续？"
   - 防止误操作

---

## 总结

本次修复确保了：
1. ✅ 安装插件时，自动初始化数据（`on_install()`）
2. ✅ 卸载插件时，自动清理数据（`uninstall()` 或 `cleanup` 配置）
3. ✅ 预置模板也能被删除（`force=True`）
4. ✅ 无论插件是否启用，都能清理数据

现在插件系统已经完整支持了生命周期管理（安装时初始化 + 卸载时清理）。
