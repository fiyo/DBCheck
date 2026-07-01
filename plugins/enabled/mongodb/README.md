# MongoDB 巡检插件使用说明

## 功能简介

本插件为 DBCheck 添加 MongoDB 数据库巡检功能，支持：
- 连接 MongoDB 实例（单机或副本集）
- 采集版本信息、服务器状态、数据库统计等数据
- 生成 Word 格式巡检报告

## 安装步骤

### 1. 安装依赖

```bash
pip install pymongo>=4.0
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

## 配置 MongoDB 连接

在 DBCheck Web UI 中添加 MongoDB 数据源时，需要填写以下信息：

- **主机地址**：MongoDB 服务器 IP 或主机名
- **端口**：MongoDB 服务端口（默认 27017）
- **用户名**：（可选）MongoDB 登录用户名
- **密码**：（可选）MongoDB 登录密码
- **数据库**：要连接的数据库名（默认 `admin`）

## 测试插件

### 1. 启动 MongoDB 实例

如果没有 MongoDB 实例，可以使用 Docker 快速启动一个：

```bash
docker run -d -p 27017:27017 --name test-mongo mongo:latest
```

### 2. 运行测试脚本

```bash
cd /path/to/DBCheck/plugins/available/mongodb/
python test_plugin.py
```

### 3. 在 DBCheck 中使用

启动 DBCheck Web UI，在“数据源管理”中添加 MongoDB 数据源，然后执行巡检任务。

## 巡检内容

本插件目前支持以下巡检内容：

1. **数据库版本信息** - MongoDB 版本、Git 版本等
2. **服务器状态** - 操作计数器、内存使用、连接数等
3. **数据库统计** - 数据大小、存储大小、索引大小、集合数量等

## 扩展开发

如果需要添加新的巡检内容，可以修改 `main_plugin.py` 中的 `collect_data()` 方法，添加新的 MongoDB 命令。

同时，需要修改 `sql_templates.json`，添加对应的章节和查询定义。

## 报告模板

本插件使用 Word 模板生成报告。如果需要自定义报告格式，可以修改 `templates/mongodb_wordtemplates_v1.0.docx` 文件。

如果不存在专用模板，插件会使用默认模板。

## 常见问题

### 1. 连接失败

- 确认 MongoDB 实例正在运行
- 确认主机地址和端口正确
- 如果启用了认证，确认用户名和密码正确

### 2. 报告生成失败

- 确认已安装 `python-docx` 库
- 确认有写入输出文件的权限

### 3. 插件无法加载

- 确认插件目录结构正确
- 确认 `plugin.json` 文件格式正确
- 查看 DBCheck 日志获取详细错误信息

## 作者

DBCheck Team

## 版本

v1.0.0 (2026-07-01)
