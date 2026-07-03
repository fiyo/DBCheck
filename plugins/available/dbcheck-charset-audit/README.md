# ASCII字符集表检测（Demo）

DBCheck 插件入门示例，展示 InspectionPlugin 开发流程。

## 功能

检测数据库中非ASCII字符集的表，评估多语言兼容性风险：
- 支持 MySQL（information_schema.tables）
- 支持 PostgreSQL（pg_catalog.pg_tables）
- 支持 Oracle / DM8（后续扩展）

## 安装

在 DBCheck Web UI → 插件市场 → 搜索「ASCII」→ 一键安装。

或手动安装：
```bash
cp -r demo-ascii-table-check /path/to/DBCheck/plugins/
```

## 规则

| 风险等级 | 触发条件 |
|---------|---------|
| MEDIUM | 超过 50 个非标准字符集的表 |
| LOW | 1~50 个非标准字符集的表 |

## 开发者

本插件同时是 DBCheck 插件开发的教学示例，完整代码见 `__init__.py`。
