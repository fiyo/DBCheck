# MongoDB 巡检插件使用说明

## 功能简介

本插件为 DBCheck 添加 MongoDB 数据库巡检功能（v2.0），支持：

- 连接 MongoDB 实例（单机、副本集、分片集群）
- 标准 / SRV (mongodb+srv://) 连接模式
- TLS/SSL 加密连接
- 认证机制选择 (SCRAM-SHA-256 / SCRAM-SHA-1)
- 版本适配 (5.0+/6.0+/7.0+/8.0+)
- 12+ 章节巡检内容、20+ 采集项
- 17 条基线参数检查 (getParameter 批量获取)
- 慢查询采集 (system.profile Top N)

## 安装步骤

### 1. 安装依赖

```bash
pip install pymongo>=4.6
pip install python-docx>=1.0
```

### 2. 安装插件

将 `mongodb` 目录复制到 DBCheck 的 `plugins/available/` 目录：

```bash
cp -r mongodb/ /path/to/DBCheck/plugins/available/
```

### 3. 启用插件

运行以下 Python 命令启用插件：

```python
from plugin_loader import enable_plugin
enable_plugin('mongodb')
```

或者，手动将 `plugins/available/mongodb/` 目录复制到 `plugins/enabled/` 目录。

### 4. 初始化巡检模板

```bash
python init_mongodb_template.py --force
```

## 配置 MongoDB 连接

在 DBCheck Web UI 中添加 MongoDB 数据源时，需要填写以下信息：

### 基本连接
- **主机地址**：MongoDB 服务器 IP 或主机名
- **端口**：MongoDB 服务端口（默认 27017）
- **用户名**：MongoDB 登录用户名
- **密码**：MongoDB 登录密码
- **数据库**：要连接的数据库名（默认 `admin`）

### 连接模式
- **标准连接**：使用 `mongodb://` 协议直连
- **SRV 连接**：使用 `mongodb+srv://` 协议（通过 DNS SRV 记录自动发现节点，端口被忽略）

### 认证配置
- **认证源 (authSource)**：认证所用的数据库（默认 `admin`）
- **认证机制 (authMechanism)**：`SCRAM-SHA-256`（推荐）或 `SCRAM-SHA-1`

### 副本集
- **副本集名称**：可选，指定后连接 URI 会带上 `replicaSet` 参数

### TLS/SSL
- **TLS 开关**：勾选后展开以下选项
  - **CA 证书路径**：`tlsCAFile`
  - **客户端证书路径**：`tlsCertificateKeyFile`
  - **允许无效证书**：仅用于测试环境

## 巡检内容

本插件支持以下巡检章节（12+ 章节，20+ 采集项）：

1. **数据库版本信息** - MongoDB 版本、Git 版本、OpenSSL 版本等
2. **服务器状态概要** - 运行时间、连接数、内存、网络流量
3. **数据库统计** - 数据大小、存储大小、索引大小、集合数量
4. **安全配置** - 用户列表、角色列表、权限
5. **性能指标** - 操作计数器、全局锁、WiredTiger 缓存
6. **副本集状态** - 成员健康、状态、延迟（非副本集则空）
7. **分片集群状态** - 分片列表（非分片则空）
8. **慢查询与 Profiler** - Profiler 级别、Top 10 慢查询
9. **基线配置检查** - 17 条 getParameter 参数检查
10. **网络与连接池** - 网络流量统计
11. **事务与锁** - 事务提交/中止/超时统计
12. **存储引擎与压缩** - WiredTiger block manager 状态

## 基线检查参数

| 参数名 | 说明 | 最低版本 |
|--------|------|----------|
| authenticationMechanisms | 认证机制应包含 SCRAM-SHA-256 | 5.0+ |
| enableLocalhostAuthBypass | 应禁用 localhost 认证绕过 | 5.0+ |
| writeConcernMajorityJournalDefault | majority 写关注应等待 journal | 5.0+ |
| wiredTigerCacheSizeGB | WiredTiger 缓存 >= 1GB | 5.0+ |
| javascriptEnabled | 生产环境应禁用 JS 执行 | 5.0+ |
| clusterAuthMode | 副本集应配置集群认证 | 5.0+ |
| logLevel | 日志级别 <= 1 | 5.0+ |
| slowOpThresholdMs | 慢查询阈值 <= 100ms | 5.0~6.x |
| slowQuerySampler | 慢查询采样阈值 <= 100ms | 7.0+ |
| tlsMode | 应启用 TLS 加密传输 | 5.0+ |
| auditLogDestination | 应配置审计日志目标 | 5.0+ |
| maxConnections | 最大连接数 >= 1000 | 5.0+ |
| networkMessageCompressors | 应启用网络消息压缩 | 5.0+ |
| enableMajorityReadConcern | 应启用多数派读关注 | 5.0+ |
| diagnosticDataCollectionEnabled | 应启用 FTDC 诊断数据 | 5.0+ |
| ttlMonitorEnabled | 应启用 TTL 监控 | 5.0+ |
| traceExceptions | 生产环境应关闭异常追踪 | 5.0+ |

## 版本兼容性

| 版本 | 支持状态 | 说明 |
|------|----------|------|
| 8.0+ | 完全支持 | 所有功能 |
| 7.0+ | 完全支持 | slowOpThresholdMs → slowQuerySampler |
| 6.0+ | 完全支持 | 所有基线参数 |
| 5.0+ | 完全支持 | 所有基线参数 |
| 4.x | 基础采集 | 跳过基线检查，仅采集基础数据 |
| 3.x 及以下 | 基础采集 | 同 4.x |

## 扩展开发

如果需要添加新的巡检内容：

1. 修改 `main_plugin.py` 中的 `collect_data()` 方法，添加新的 `_collect_*` 方法
2. 修改 `sql_templates.json`，添加对应的章节和查询定义
3. 修改 `baselines.json`，添加新的基线参数
4. 修改 `version_adapter.py`，添加版本适配逻辑

## 报告模板

本期复用通用 fallback 渲染，不做专用 .docx 模板。如果不存在专用模板，插件会使用默认模板。

## 常见问题

### 1. 连接失败

- 确认 MongoDB 实例正在运行
- 确认主机地址和端口正确
- 如果启用了认证，确认用户名和密码正确
- 确认认证源 (authSource) 正确
- 如果使用 TLS，确认证书路径正确

### 2. 权限不足

- 确认用户具有 `clusterMonitor`、`readAnyDatabase` 等角色
- `usersInfo` / `rolesInfo` 需要 `clusterAdmin` 或 `userAdmin` 角色
- `getParameter` 需要 `clusterManager` 或 `hostManager` 角色

### 3. 副本集/分片信息为空

- 副本集状态仅在副本集模式下可采集
- 分片信息仅在 mongos 路由节点上可采集

### 4. 慢查询为空

- 确认 Profiler 已开启（`db.setProfilingLevel(1, {slowms: 100})`）
- `system.profile` 集合需要有数据

## 作者

DBCheck Team

## 版本

v2.0.0 (2026-07-19)
