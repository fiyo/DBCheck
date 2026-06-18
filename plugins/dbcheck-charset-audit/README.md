# 字符集与排序规则审计

DBCheck 官方插件 —— 支持全部 10 种数据库的字符集和排序规则检查。

## 覆盖范围

| 数据库 | 检查内容 |
|--------|---------|
| MySQL / TiDB | 表级 `table_collation`、库级 `DEFAULT_CHARACTER_SET_NAME` |
| PostgreSQL / IvorySQL / KingbaseES | 库级编码（`pg_database`）、列级排序规则 |
| Oracle / DM8 / YashanDB | `NLS_CHARACTERSET` 参数、列级 `CHARACTER_SET_NAME` |
| SQL Server | 库级 `collation_name`、列级 `collation_name` |
| GBase 8s | `sysdbslocale` 语言环境 |

## 风险规则

| 等级 | 触发条件 |
|:---:|---------|
| HIGH | 数据库或表使用非 UTF8 字符集/排序规则 |
| MEDIUM | 超过 50 个列/表使用非标准排序规则 |
| LOW | 少数列使用非标准排序规则 |

## 安装

DBCheck Web UI → 插件市场 → 搜索「字符集」→ 一键安装。

或手动安装：
```bash
cp -r dbcheck-charset-audit /path/to/DBCheck/plugins/
```
