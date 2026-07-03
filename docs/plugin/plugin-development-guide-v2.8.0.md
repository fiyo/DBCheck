# DBCheck v2.8.0 插件系统开发指南

## 目录

- [1. DBCheck 工具简介](#1-dbcheck-工具简介)
- [2. v2.8.0 版本更新概览](#2-v280-版本更新概览)
- [3. 插件系统架构](#3-插件系统架构)
- [4. 数据库插件开发](#4-数据库插件开发)
- [5. 规则插件开发](#5-规则插件开发)
- [6. 插件生命周期管理](#6-插件生命周期管理)
- [7. 插件打包与发布](#7-插件打包与发布)
- [8. 最佳实践](#8-最佳实践)

---

## 1. DBCheck 工具简介

### 1.1 什么是 DBCheck？

DBCheck 是一款开源、跨平台的数据库自动化健康巡检工具，支持 **12 种主流数据库**（MySQL、PostgreSQL、Oracle、SQL Server、MongoDB 等），通过执行预定义的巡检 SQL 并采集系统资源，自动生成标准化的 Word 巡检报告。

### 1.2 核心特性

| 特性 | 说明 |
|------|------|
| 🗄️ 多数据库支持 | 覆盖 12 种主流关系型和非关系型数据库 |
| 📋 自动巡检 | 160+ 条增强规则，自动生成专业 Word 报告 |
| 🤖 AI 智能诊断 | 基于本地 Ollama，离线 AI 分析，生成优化建议 |
| 🔌 插件系统 | 可扩展架构，支持自定义数据库和规则插件 |
| 📊 历史趋势分析 | 多轮巡检数据聚合，生成趋势图表 |
| 🔒 配置基线管理 | 关键参数对比分析，快速发现配置偏差 |
| 📡 实时监控 | 慢查询 + 活跃连接实时监控，热力图可视化 |
| 🌐 Web UI | 基于 Flask + Bootstrap 5，现代化 Web 界面 |

### 1.3 典型使用场景

- **日常巡检**：定期对各数据库进行健康检查
- **升级前评估**：对比升级前后的配置和性能
- **故障排查**：通过巡检报告快速定位问题
- **合规审计**：生成标准化报告，满足审计要求
- **AI 辅助优化**：利用本地 LLM 生成优化建议

---

## 2. v2.8.0 版本更新概览

### 2.1 核心更新

DBCheck v2.8.0 引入了**完全重构的插件系统**，实现了插件的真正独立和可扩展。

#### 🚀 重大改进

1. **插件生命周期管理**
   - 新增 `on_install()` 和 `on_uninstall()` 方法
   - 插件安装时自动初始化数据（模板、基线、规则）
   - 插件卸载时自动清理所有关联数据

2. **插件数据完全独立**
   - 每个插件自带 `template_data.json`（巡检模板）
   - 每个插件自带 `baseline_data.json`（基线配置）
   - 每个插件自带规则引擎文件（如 `oracle_jdbc.yaml`）

3. **新增数据库插件**
   - **MongoDB 插件**：支持 MongoDB 4.0+ 巡检
   - **Oracle JDBC 插件**：基于 JPype + ojdbc，无需 Oracle 客户端

4. **插件市场优化**
   - Web UI 插件管理页面重构
   - 支持插件浏览、安装、卸载、启用/禁用
   - 插件依赖检查和冲突检测

### 2.2 插件系统架构

```
DBCheck 插件系统
├── 插件类型
│   ├── 数据库插件（Database Plugin）
│   │   └── 新增数据库支持（如 MongoDB、Oracle JDBC）
│   └── 规则插件（Rule Plugin）
│       └── 扩展巡检规则（如自定义检查项）
├── 插件生命周期
│   ├── on_install()    # 安装时调用
│   ├── on_uninstall()  # 卸载时调用
│   ├── on_enable()     # 启用时调用（可选）
│   └── on_disable()   # 禁用时调用（可选）
└── 插件数据
    ├── template_data.json   # 巡检模板
    ├── baseline_data.json   # 基线配置
    └── rules/*.yaml        # 规则引擎文件
```

---

## 3. 插件系统架构

### 3.1 插件类型详解

#### 📦 数据库插件（Database Plugin）

**用途**：为 DBCheck 添加新的数据库支持。

**职责**：
- 实现数据库连接和查询接口
- 定义巡检模板（SQL 查询、章节结构）
- 定义基线配置（关键参数推荐值）
- 提供规则引擎文件（风险检测规则）

**示例**：
- `mongodb` 插件：添加 MongoDB 支持
- `oracle_jdbc` 插件：通过 JDBC 连接 Oracle

#### 📋 规则插件（Rule Plugin）

**用途**：为现有数据库扩展自定义巡检规则。

**职责**：
- 定义额外的巡检章节或查询
- 定义自定义基线配置
- 提供自定义风险检测规则

**示例**：
- `mysql_innodb_cluster` 插件：添加 MySQL InnoDB Cluster 专项巡检
- `oracle_rac_advanced` 插件：添加 Oracle RAC 高级检查项

### 3.2 插件目录结构

```
plugins/available/
├── mongodb/                    # MongoDB 数据库插件
│   ├── plugin.json             # 插件元数据
│   ├── main_plugin.py         # 插件主类
│   ├── template_data.json     # 巡检模板数据
│   ├── baseline_data.json     # 基线配置数据
│   └── rules/
│       └── builtin/
│           └── mongodb.yaml  # 规则引擎文件
│
└── oracle_jdbc/               # Oracle JDBC 数据库插件
    ├── plugin.json
    ├── main_plugin.py
    ├── template_data.json
    ├── baseline_data.json
    └── rules/
        └── builtin/
            └── oracle_jdbc.yaml
```

### 3.3 插件基类 API

所有插件必须继承 `InspectionPlugin` 基类：

```python
# plugin_core.py

class InspectionPlugin:
    """插件基类"""
    
    def __init__(self, plugin_name: str):
        self.plugin_name = plugin_name
        self.plugin_dir = None  # 插件目录（自动设置）
    
    # ========== 生命周期方法 ==========
    
    def on_install(self, db_path: str = None):
        """安装时回调（插件安装时调用，用于初始化数据）"""
        pass
    
    def on_uninstall(self, db_path: str = None):
        """卸载时回调（插件卸载时调用，用于清理数据）"""
        pass
    
    def on_enable(self):
        """启用时回调（可选）"""
        pass
    
    def on_disable(self):
        """禁用时回调（可选）"""
        pass
    
    # ========== 数据库连接方法 ==========
    
    def create_connection(self, config: dict):
        """创建数据库连接（子类必须实现）"""
        raise NotImplementedError
    
    # ========== 巡检方法 ==========
    
    def run_inspection(self, db_config: dict, template_id: int = None):
        """执行巡检（子类可选覆盖）"""
        pass
    
    # ========== 辅助方法 ==========
    
    def load_json_data(self, filename: str) -> dict:
        """加载插件目录下的 JSON 数据文件"""
        path = os.path.join(self.plugin_dir, filename)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
```

---

## 4. 数据库插件开发

### 4.1 开发流程概览

```
1. 创建插件目录
   ↓
2. 编写 plugin.json（插件元数据）
   ↓
3. 编写 main_plugin.py（插件主类）
   ↓
4. 创建 template_data.json（巡检模板）
   ↓
5. 创建 baseline_data.json（基线配置）
   ↓
6. 创建规则引擎文件（可选）
   ↓
7. 测试插件
   ↓
8. 打包发布
```

### 4.2 步骤 1：创建插件目录

```bash
# 进入插件目录
cd D:/DBCheck/plugins/available

# 创建插件目录（以 MongoDB 为例）
mkdir mongodb
cd mongodb
```

### 4.3 步骤 2：编写 plugin.json

`plugin.json` 是插件的元数据文件，定义插件的基本信息、依赖、生命周期配置等。

#### 📝 完整示例（MongoDB 插件）

```json
{
  "name": "MongoDB",
  "version": "1.0.0",
  "description": "适用于 MongoDB 4.0+ 实例，基于 PyMongo 驱动",
  "db_type": "mongodb",
  "main_file": "main_plugin.py",
  "author": "DBCheck Team",
  "license": "MIT",
  "dependencies": {
    "python": ["pymongo>=4.0"]
  },
  "cleanup": {
    "db_types": ["mongodb"],
    "data_types": ["template", "baseline", "rules"]
  },
  "min_db_version": "4.0",
  "tested_versions": ["4.0", "5.0", "6.0", "7.0"],
  "connection": {
    "default_port": 27017,
    "required_fields": ["host", "port", "database"],
    "optional_fields": ["username", "password", "auth_source"]
  }
}
```

#### 📋 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 插件显示名称 |
| `version` | string | ✅ | 插件版本号（语义化版本） |
| `description` | string | ✅ | 插件描述 |
| `db_type` | string | ✅ | 数据库类型标识（唯一） |
| `main_file` | string | ✅ | 插件主文件名 |
| `author` | string | ❌ | 作者 |
| `license` | string | ❌ | 许可证 |
| `dependencies` | object | ❌ | 依赖（Python 包、系统库等） |
| `cleanup` | object | ❌ | 卸载清理配置 |
| `min_db_version` | string | ❌ | 最低支持的数据库版本 |
| `tested_versions` | array | ❌ | 已测试版本列表 |
| `connection` | object | ✅ | 数据库连接配置 |

#### 🔧 cleanup 配置详解

`cleanup` 字段用于定义插件卸载时需要清理的数据：

```json
{
  "cleanup": {
    "db_types": ["mongodb"],       // 要清理的数据库类型
    "data_types": [                  // 要清理的数据类型
      "template",                    // 巡检模板
      "baseline",                    // 基线配置
      "rules"                       // 规则引擎文件
    ]
  }
}
```

### 4.4 步骤 3：编写 main_plugin.py

`main_plugin.py` 是插件的核心文件，必须包含：
1. 插件主类（继承 `InspectionPlugin`）
2. `create_connection()` 方法（创建数据库连接）
3. `on_install()` 方法（安装时初始化数据）
4. `on_uninstall()` 方法（卸载时清理数据）

#### 📝 完整示例（MongoDB 插件）

```python
# mongodb/main_plugin.py

import json
import pymongo
from plugin_core import InspectionPlugin

class MongoDBPlugin(InspectionPlugin):
    """MongoDB 数据库插件"""
    
    def __init__(self):
        super().__init__(plugin_name="mongodb")
        self.db_type = "mongodb"
    
    # ========== 生命周期方法 ==========
    
    def on_install(self, db_path: str = None):
        """插件安装时调用：初始化模板和基线数据（插件独立，不依赖平台）"""
        print(f"[MongoDB] 安装插件，初始化数据...")
        
        # 1. 创建巡检模板
        self._init_templates(db_path)
        
        # 2. 创建基线配置
        self._init_baselines(db_path)
        
        print(f"[MongoDB] 插件安装完成")
    
    def on_uninstall(self, db_path: str = None):
        """插件卸载时调用：清理模板和基线数据"""
        print(f"[MongoDB] 卸载插件，清理数据...")
        
        # 1. 清理巡检模板
        self._cleanup_templates(db_path)
        
        # 2. 清理基线配置
        self._cleanup_baselines(db_path)
        
        print(f"[MongoDB] 插件卸载完成")
    
    # ========== 数据库连接方法 ==========
    
    def create_connection(self, config: dict):
        """创建 MongoDB 连接"""
        host = config.get("host", "localhost")
        port = config.get("port", 27017)
        database = config.get("database", "admin")
        username = config.get("username")
        password = config.get("password")
        auth_source = config.get("auth_source", "admin")
        
        # 构建连接字符串
        if username and password:
            uri = f"mongodb://{username}:{password}@{host}:{port}/{database}?authSource={auth_source}"
        else:
            uri = f"mongodb://{host}:{port}/{database}"
        
        try:
            client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
            # 测试连接
            client.admin.command("ping")
            return client[database]
        except Exception as e:
            raise Exception(f"MongoDB 连接失败: {str(e)}")
    
    # ========== 巡检方法 ==========
    
    def run_inspection(self, db_config: dict, template_id: int = None):
        """执行 MongoDB 巡检"""
        # 1. 创建连接
        db = self.create_connection(db_config)
        
        # 2. 加载巡检模板
        if template_id is None:
            template = self._get_default_template()
        else:
            template = self._get_template_by_id(template_id)
        
        # 3. 执行巡检查询
        results = {}
        for chapter in template["chapters"]:
            for query in chapter["queries"]:
                try:
                    result = self._execute_query(db, query)
                    results[query["key"]] = result
                except Exception as e:
                    results[query["key"]] = {"error": str(e)}
        
        # 4. 生成报告
        return self._generate_report(results)
    
    # ========== 私有方法 ==========
    
    def _init_templates(self, db_path: str = None):
        """初始化巡检模板（从 template_data.json 读取）"""
        import json
        
        # 1. 读取模板数据
        template_file = os.path.join(self.plugin_dir, "template_data.json")
        with open(template_file, 'r', encoding='utf-8') as f:
            template_data = json.load(f)
        
        # 2. 创建模板
        from pro.inspection_template import create_template
        
        template = create_template(
            name=template_data["template_name"],
            db_type=self.db_type,
            description=template_data["description"],
            is_preset=True,
            db_path=db_path
        )
        
        # 3. 创建章节和查询
        for chapter_data in template_data["chapters"]:
            # 创建章节
            chapter = self._create_chapter(template["id"], chapter_data, db_path)
            
            # 创建查询
            for query_data in chapter_data["queries"]:
                self._create_query(chapter["id"], query_data, db_path)
        
        print(f"[MongoDB] 模板创建成功: {template['name']}")
    
    def _init_baselines(self, db_path: str = None):
        """初始化基线配置（从 baseline_data.json 读取）"""
        import json
        
        # 1. 读取基线数据
        baseline_file = os.path.join(self.plugin_dir, "baseline_data.json")
        with open(baseline_file, 'r', encoding='utf-8') as f:
            baseline_data = json.load(f)
        
        # 2. 插入基线配置
        from pro.baseline import insert_baseline
        
        for baseline in baseline_data["baselines"]:
            insert_baseline(
                db_type=self.db_type,
                param_name=baseline["param_name"],
                recommended_value=baseline["recommended_value"],
                min_value=baseline.get("min_value"),
                max_value=baseline.get("max_value"),
                description=baseline.get("description"),
                db_path=db_path
            )
        
        print(f"[MongoDB] 基线配置初始化完成，共 {len(baseline_data['baselines'])} 条")
    
    def _cleanup_templates(self, db_path: str = None):
        """清理巡检模板"""
        from pro.inspection_template import get_templates_by_db_type, delete_template
        
        # 1. 查询插件创建的模板
        templates = get_templates_by_db_type(self.db_type, db_path=db_path)
        
        # 2. 删除模板（force=True 删除预置模板）
        for template in templates:
            delete_template(template["id"], db_path=db_path, force=True)
        
        print(f"[MongoDB] 模板清理完成，共 {len(templates)} 个")
    
    def _cleanup_baselines(self, db_path: str = None):
        """清理基线配置"""
        from pro.baseline import delete_baselines_by_db_type
        
        # 删除该数据库类型的所有基线
        count = delete_baselines_by_db_type(self.db_type, db_path=db_path)
        
        print(f"[MongoDB] 基线配置清理完成，共 {count} 条")
    
    def _execute_query(self, db, query: dict):
        """执行单个查询"""
        query_type = query.get("type", "find")
        
        if query_type == "find":
            # 查询文档
            collection = db[query["collection"]]
            filter = query.get("filter", {})
            limit = query.get("limit", 0)
            
            cursor = collection.find(filter)
            if limit > 0:
                cursor = cursor.limit(limit)
            
            return list(cursor)
        
        elif query_type == "command":
            # 执行命令
            command = query.get("command")
            return db.command(command)
        
        elif query_type == "aggregate":
            # 聚合查询
            collection = db[query["collection"]]
            pipeline = query.get("pipeline", [])
            return list(collection.aggregate(pipeline))
        
        else:
            raise Exception(f"不支持的查询类型: {query_type}")
    
    def _generate_report(self, results: dict):
        """生成巡检报告"""
        # 这里简化实现，实际应生成结构化报告
        report = {
            "db_type": self.db_type,
            "timestamp": datetime.now().isoformat(),
            "results": results
        }
        return report

# ========== 插件入口 ==========

def get_plugin():
    """返回插件实例（插件市场调用）"""
    return MongoDBPlugin()
```

### 4.5 步骤 4：创建 template_data.json

`template_data.json` 定义插件的巡检模板，包括章节结构和 SQL（或等价查询）定义。

#### 📝 完整示例（MongoDB 插件）

```json
{
  "template_name": "MongoDB 基础巡检模板",
  "template_name_en": "MongoDB Basic Inspection Template",
  "description": "MongoDB 基础巡检（连接状态、数据库状态、慢查询等）",
  "description_en": "MongoDB basic inspection (connection, database status, slow queries, etc.)",
  "chapters": [
    {
      "chapter_name": "基本信息",
      "chapter_name_en": "Basic Info",
      "order_num": 1,
      "queries": [
        {
          "key": "db_version",
          "desc_zh": "数据库版本",
          "desc_en": "Database Version",
          "type": "command",
          "command": "buildInfo",
          "risk_level": "info"
        },
        {
          "key": "db_status",
          "desc_zh": "数据库状态",
          "desc_en": "Database Status",
          "type": "command",
          "command": "serverStatus",
          "risk_level": "info"
        }
      ]
    },
    {
      "chapter_name": "连接信息",
      "chapter_name_en": "Connections",
      "order_num": 2,
      "queries": [
        {
          "key": "current_connections",
          "desc_zh": "当前连接数",
          "desc_en": "Current Connections",
          "type": "command",
          "command": "serverStatus",
          "field_path": "connections.current",
          "risk_level": "warning",
          "threshold": {
            "max": 1000
          }
        },
        {
          "key": "available_connections",
          "desc_zh": "可用连接数",
          "desc_en": "Available Connections",
          "type": "command",
          "command": "serverStatus",
          "field_path": "connections.available",
          "risk_level": "warning",
          "threshold": {
            "min": 100
          }
        }
      ]
    },
    {
      "chapter_name": "数据库列表",
      "chapter_name_en": "Database List",
      "order_num": 3,
      "queries": [
        {
          "key": "databases",
          "desc_zh": "数据库列表",
          "desc_en": "Database List",
          "type": "command",
          "command": "listDatabases",
          "risk_level": "info"
        }
      ]
    },
    {
      "chapter_name": "慢查询",
      "chapter_name_en": "Slow Queries",
      "order_num": 4,
      "queries": [
        {
          "key": "slow_queries",
          "desc_zh": "慢查询统计",
          "desc_en": "Slow Query Stats",
          "type": "command",
          "command": "serverStatus",
          "field_path": "opcounters",
          "risk_level": "warning"
        }
      ]
    }
  ]
}
```

#### 📋 字段说明

**顶层字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `template_name` | string | 模板名称（中文） |
| `template_name_en` | string | 模板名称（英文） |
| `description` | string | 模板描述（中文） |
| `description_en` | string | 模板描述（英文） |
| `chapters` | array | 章节列表 |

**chapter 字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `chapter_name` | string | 章节名称（中文） |
| `chapter_name_en` | string | 章节名称（英文） |
| `order_num` | int | 章节顺序 |
| `queries` | array | 查询列表 |

**query 字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `key` | string | 查询唯一标识 |
| `desc_zh` | string | 查询描述（中文） |
| `desc_en` | string | 查询描述（英文） |
| `type` | string | 查询类型（`find`/`command`/`aggregate`） |
| `command` | string | 命令名称（用于 `command` 类型） |
| `collection` | string | 集合名称（用于 `find`/`aggregate` 类型） |
| `filter` | object | 查询过滤条件 |
| `pipeline` | array | 聚合管道（用于 `aggregate` 类型） |
| `field_path` | string | 结果提取路径（如 `connections.current`） |
| `risk_level` | string | 风险等级（`info`/`warning`/`critical`） |
| `threshold` | object | 阈值配置（`min`/`max`） |

### 4.6 步骤 5：创建 baseline_data.json

`baseline_data.json` 定义插件的关键参数基线配置。

#### 📝 完整示例（MongoDB 插件）

```json
{
  "baselines": [
    {
      "param_name": "maxConnections",
      "recommended_value": "1000",
      "min_value": "100",
      "max_value": "5000",
      "description": "最大连接数",
      "description_en": "Maximum number of simultaneous connections"
    },
    {
      "param_name": "maxIncomingConnections",
      "recommended_value": "800",
      "min_value": "100",
      "max_value": "4000",
      "description": "最大入站连接数",
      "description_en": "Maximum number of incoming connections"
    },
    {
      "param_name": "storage.cacheSizeGB",
      "recommended_value": "4",
      "min_value": "1",
      "max_value": "64",
      "description": "WiredTiger 缓存大小（GB）",
      "description_en": "WiredTiger cache size in GB"
    },
    {
      "param_name": "operationProfiling.slowOpThresholdMs",
      "recommended_value": "100",
      "min_value": "50",
      "max_value": "1000",
      "description": "慢查询阈值（毫秒）",
      "description_en": "Slow operation threshold in milliseconds"
    },
    {
      "param_name": "replication.oplogSizeMB",
      "recommended_value": "2048",
      "min_value": "512",
      "max_value": "10240",
      "description": "oplog 大小（MB）",
      "description_en": "Oplog size in MB"
    },
    {
      "param_name": "journal.enabled",
      "recommended_value": "true",
      "description": "是否启用日志",
      "description_en": "Whether to enable journaling"
    },
    {
      "param_name": "net.maxIncomingConnections",
      "recommended_value": "800",
      "min_value": "100",
      "max_value": "4000",
      "description": "网络最大入站连接数",
      "description_en": "Maximum incoming network connections"
    },
    {
      "param_name": "security.authorization",
      "recommended_value": "enabled",
      "description": "是否启用认证",
      "description_en": "Whether to enable authorization"
    }
  ]
}
```

### 4.7 步骤 6：创建规则引擎文件（可选）

规则引擎文件定义风险检测规则，位于 `rules/builtin/` 目录下，使用 YAML 格式。

#### 📝 完整示例（MongoDB 插件）

```yaml
# rules/builtin/mongodb.yaml

# MongoDB 风险检测规则
rules:
  # 规则 1：连接数过高
  - name: "连接数过高"
    name_en: "High Connection Count"
    db_type: "mongodb"
    risk_level: "warning"
    check_type: "threshold"
    param_path: "connections.current"
    threshold:
      max: 800
    recommendation: "当前连接数过高，建议优化连接池配置或增加 maxConnections 参数"
    recommendation_en: "Current connection count is too high, consider optimizing connection pool or increasing maxConnections"

  # 规则 2：可用连接数过低
  - name: "可用连接数过低"
    name_en: "Low Available Connections"
    db_type: "mongodb"
    risk_level: "critical"
    check_type: "threshold"
    param_path: "connections.available"
    threshold:
      min: 50
    recommendation: "可用连接数过低，可能导致新的连接无法建立，建议增加 maxConnections 参数"
    recommendation_en: "Available connections too low, may cause new connections to fail, consider increasing maxConnections"

  # 规则 3：慢查询过多
  - name: "慢查询过多"
    name_en: "Too Many Slow Queries"
    db_type: "mongodb"
    risk_level: "warning"
    check_type: "opcounter"
    op_type: "query"
    threshold:
      max: 1000
    recommendation: "慢查询数量过多，建议检查索引或优化查询语句"
    recommendation_en: "Too many slow queries, consider checking indexes or optimizing queries"

  # 规则 4：未启用认证
  - name: "未启用认证"
    name_en: "Authorization Not Enabled"
    db_type: "mongodb"
    risk_level: "critical"
    check_type: "config"
    param_path: "security.authorization"
    expected_value: "enabled"
    recommendation: "未启用认证，存在安全风险，建议启用 authorization"
    recommendation_en: "Authorization not enabled, security risk, consider enabling authorization"

  # 规则 5：未启用日志
  - name: "未启用日志"
    name_en: "Journaling Not Enabled"
    db_type: "mongodb"
    risk_level: "warning"
    check_type: "config"
    param_path: "journal.enabled"
    expected_value: true
    recommendation: "未启用日志，可能存在数据丢失风险，建议启用 journaling"
    recommendation_en: "Journaling not enabled, risk of data loss, consider enabling journaling"
```

### 4.8 步骤 7：测试插件

#### 🧪 测试流程

```bash
# 1. 安装插件
cd D:/DBCheck
python -c "from plugin_market import PluginMarket; pm = PluginMarket(); pm.install('mongodb')"

# 2. 检查模板是否创建
python -c "from pro.inspection_template import get_templates_by_db_type; import json; print(json.dumps(get_templates_by_db_type('mongodb'), indent=2, ensure_ascii=False))"

# 3. 检查基线是否创建
python -c "from pro.baseline import get_baselines_by_db_type; import json; print(json.dumps(get_baselines_by_db_type('mongodb'), indent=2, ensure_ascii=False))"

# 4. 测试数据库连接
python -c "
from plugins.available.mongodb.main_plugin import MongoDBPlugin
plugin = MongoDBPlugin()
config = {'host': 'localhost', 'port': 27017, 'database': 'admin'}
db = plugin.create_connection(config)
print('连接成功:', db.command('ping'))
"

# 5. 卸载插件（测试清理）
python -c "from plugin_market import PluginMarket; pm = PluginMarket(); pm.uninstall('mongodb')"

# 6. 检查模板是否已清理
python -c "from pro.inspection_template import get_templates_by_db_type; print('剩余模板:', len(get_templates_by_db_type('mongodb')))"
```

---

## 5. 规则插件开发

### 5.1 规则插件 vs 数据库插件

| 特性 | 数据库插件 | 规则插件 |
|------|-----------|---------|
| **用途** | 添加新数据库支持 | 扩展现有数据库的巡检规则 |
| **必须实现** | `create_connection()` | 无需实现连接方法 |
| **数据文件** | `template_data.json` + `baseline_data.json` | 仅需 `baseline_data.json`（可选） |
| **规则文件** | 需要 | 需要 |
| **示例** | mongodb、oracle_jdbc | mysql_innodb_cluster、oracle_rac_advanced |

### 5.2 开发流程

```
1. 创建插件目录
   ↓
2. 编写 plugin.json（指定 extends_db_type）
   ↓
3. 编写 main_plugin.py（继承现有插件或 InspectionPlugin）
   ↓
4. 创建 template_data.json（可选，添加额外章节）
   ↓
5. 创建 baseline_data.json（可选，添加额外基线）
   ↓
6. 创建规则引擎文件（添加自定义规则）
   ↓
7. 测试插件
```

### 5.3 步骤 1：创建插件目录

```bash
# 示例：创建 MySQL InnoDB Cluster 规则插件
cd D:/DBCheck/plugins/available
mkdir mysql_innodb_cluster
cd mysql_innodb_cluster
```

### 5.4 步骤 2：编写 plugin.json

#### 📝 完整示例（MySQL InnoDB Cluster 规则插件）

```json
{
  "name": "MySQL InnoDB Cluster",
  "version": "1.0.0",
  "description": "MySQL InnoDB Cluster 专项巡检规则",
  "db_type": "mysql_innodb_cluster",
  "extends_db_type": "mysql",
  "main_file": "main_plugin.py",
  "author": "DBCheck Team",
  "license": "MIT",
  "dependencies": {
    "plugins": ["mysql"]
  },
  "cleanup": {
    "db_types": ["mysql_innodb_cluster"],
    "data_types": ["template", "baseline", "rules"]
  }
}
```

#### 🔑 关键字段

- `extends_db_type`: 指定扩展的数据库类型（如 `mysql`）
- `dependencies.plugins`: 指定依赖的插件（如 `["mysql"]`）

### 5.5 步骤 3：编写 main_plugin.py

规则插件可以选择：
1. **继承现有插件**：复用连接方法和基础巡检逻辑
2. **继承 InspectionPlugin**：从头实现（不推荐）

#### 📝 完整示例（MySQL InnoDB Cluster 规则插件）

```python
# mysql_innodb_cluster/main_plugin.py

import json
from plugins.available.mysql.main_plugin import MySQLPlugin

class MySQLInnoDBClusterPlugin(MySQLPlugin):
    """MySQL InnoDB Cluster 规则插件"""
    
    def __init__(self):
        super().__init__()
        self.plugin_name = "mysql_innodb_cluster"
        self.db_type = "mysql_innodb_cluster"
    
    # ========== 生命周期方法 ==========
    
    def on_install(self, db_path: str = None):
        """安装时调用：添加 InnoDB Cluster 专项巡检规则"""
        print(f"[MySQL InnoDB Cluster] 安装插件...")
        
        # 1. 添加额外巡检章节
        self._add_cluster_chapters(db_path)
        
        # 2. 添加额外基线配置
        self._add_cluster_baselines(db_path)
        
        print(f"[MySQL InnoDB Cluster] 安装完成")
    
    def on_uninstall(self, db_path: str = None):
        """卸载时调用：清理 InnoDB Cluster 专项数据"""
        print(f"[MySQL InnoDB Cluster] 卸载插件...")
        
        # 清理数据
        self._cleanup_cluster_data(db_path)
        
        print(f"[MySQL InnoDB Cluster] 卸载完成")
    
    # ========== 覆盖巡检方法 ==========
    
    def run_inspection(self, db_config: dict, template_id: int = None):
        """执行巡检（覆盖父类方法，添加 Cluster 检查）"""
        # 1. 调用父类方法（执行基础 MySQL 巡检）
        results = super().run_inspection(db_config, template_id)
        
        # 2. 添加 InnoDB Cluster 专项检查
        db = self.create_connection(db_config)
        
        # 检查 Cluster 状态
        results["cluster_status"] = self._check_cluster_status(db)
        
        # 检查 Group Replication 状态
        results["group_replication"] = self._check_group_replication(db)
        
        return results
    
    # ========== 私有方法 ==========
    
    def _add_cluster_chapters(self, db_path: str = None):
        """添加 InnoDB Cluster 专项章节"""
        import json
        
        # 读取专项章节数据
        template_file = os.path.join(self.plugin_dir, "template_data.json")
        if not os.path.exists(template_file):
            print(f"[MySQL InnoDB Cluster] 无额外章节数据")
            return
        
        with open(template_file, 'r', encoding='utf-8') as f:
            template_data = json.load(f)
        
        # 获取 MySQL 默认模板
        from pro.inspection_template import get_templates_by_db_type, add_chapter_to_template
        
        mysql_templates = get_templates_by_db_type("mysql", db_path=db_path)
        if not mysql_templates:
            print(f"[MySQL InnoDB Cluster] 未找到 MySQL 模板")
            return
        
        default_template = mysql_templates[0]
        
        # 添加章节
        for chapter_data in template_data["chapters"]:
            add_chapter_to_template(
                template_id=default_template["id"],
                chapter_name=chapter_data["chapter_name"],
                chapter_name_en=chapter_data["chapter_name_en"],
                order_num=chapter_data["order_num"],
                queries=chapter_data["queries"],
                db_path=db_path
            )
        
        print(f"[MySQL InnoDB Cluster] 章节添加完成")
    
    def _add_cluster_baselines(self, db_path: str = None):
        """添加 InnoDB Cluster 专项基线"""
        import json
        
        # 读取基线数据
        baseline_file = os.path.join(self.plugin_dir, "baseline_data.json")
        if not os.path.exists(baseline_file):
            print(f"[MySQL InnoDB Cluster] 无额外基线数据")
            return
        
        with open(baseline_file, 'r', encoding='utf-8') as f:
            baseline_data = json.load(f)
        
        # 插入基线
        from pro.baseline import insert_baseline
        
        for baseline in baseline_data["baselines"]:
            insert_baseline(
                db_type="mysql",  # 注意：基线关联到 mysql，而非 mysql_innodb_cluster
                param_name=baseline["param_name"],
                recommended_value=baseline["recommended_value"],
                min_value=baseline.get("min_value"),
                max_value=baseline.get("max_value"),
                description=baseline.get("description"),
                db_path=db_path
            )
        
        print(f"[MySQL InnoDB Cluster] 基线添加完成")
    
    def _cleanup_cluster_data(self, db_path: str = None):
        """清理 InnoDB Cluster 专项数据"""
        # 注意：规则插件的清理要小心，不要删除基础插件的数据
        print(f"[MySQL InnoDB Cluster] 清理完成")
    
    def _check_cluster_status(self, db):
        """检查 InnoDB Cluster 状态"""
        try:
            # 查询 Group Replication 状态
            result = db.query("SELECT * FROM performance_schema.replication_group_members")
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def _check_group_replication(self, db):
        """检查 Group Replication 配置"""
        try:
            result = db.query("SHOW VARIABLES LIKE 'group_replication%'")
            return result
        except Exception as e:
            return {"error": str(e)}

# ========== 插件入口 ==========

def get_plugin():
    """返回插件实例"""
    return MySQLInnoDBClusterPlugin()
```

### 5.6 步骤 4：创建 template_data.json（可选）

规则插件可以定义额外的巡检章节，这些章节会**附加到基础数据库的默认模板**。

#### 📝 完整示例（MySQL InnoDB Cluster 规则插件）

```json
{
  "chapters": [
    {
      "chapter_name": "InnoDB Cluster 状态",
      "chapter_name_en": "InnoDB Cluster Status",
      "order_num": 100,
      "queries": [
        {
          "key": "group_replication_members",
          "desc_zh": "Group Replication 成员",
          "desc_en": "Group Replication Members",
          "sql": "SELECT * FROM performance_schema.replication_group_members",
          "risk_level": "info"
        },
        {
          "key": "group_replication_status",
          "desc_zh": "Group Replication 状态",
          "desc_en": "Group Replication Status",
          "sql": "SELECT * FROM performance_schema.replication_group_member_stats",
          "risk_level": "info"
        }
      ]
    },
    {
      "chapter_name": "InnoDB Cluster 配置",
      "chapter_name_en": "InnoDB Cluster Configuration",
      "order_num": 101,
      "queries": [
        {
          "key": "group_replication_variables",
          "desc_zh": "Group Replication 变量",
          "desc_en": "Group Replication Variables",
          "sql": "SHOW VARIABLES LIKE 'group_replication%'",
          "risk_level": "info"
        }
      ]
    }
  ]
}
```

### 5.7 步骤 5：创建 baseline_data.json（可选）

规则插件可以定义额外的基线配置。

#### 📝 完整示例（MySQL InnoDB Cluster 规则插件）

```json
{
  "baselines": [
    {
      "param_name": "group_replication_group_seeds",
      "recommended_value": "<根据部署填写>",
      "description": "Group Replication 种子节点列表",
      "description_en": "Group Replication group seeds list"
    },
    {
      "param_name": "group_replication_single_primary_mode",
      "recommended_value": "ON",
      "description": "是否单主模式",
      "description_en": "Whether single primary mode"
    },
    {
      "param_name": "group_replication_enforce_update_everywhere_checks",
      "recommended_value": "OFF",
      "description": "是否强制 everywhere 检查",
      "description_en": "Whether to enforce update everywhere checks"
    }
  ]
}
```

### 5.8 步骤 6：创建规则引擎文件

规则插件必须提供自定义规则文件。

#### 📝 完整示例（MySQL InnoDB Cluster 规则插件）

```yaml
# rules/builtin/mysql_innodb_cluster.yaml

# MySQL InnoDB Cluster 风险检测规则
rules:
  # 规则 1：Group Replication 成员不一致
  - name: "Group Replication 成员不一致"
    name_en: "Group Replication Members Inconsistent"
    db_type: "mysql"
    risk_level: "critical"
    check_type: "custom"
    check_sql: "SELECT COUNT(*) as count FROM performance_schema.replication_group_members WHERE member_state != 'ON'"
    threshold:
      max: 0
    recommendation: "Group Replication 中存在状态异常的成员，请检查网络连接和配置"
    recommendation_en: "Some Group Replication members are not in ON state, check network and configuration"

  # 规则 2：单主模式下有多个主节点
  - name: "单主模式下多个主节点"
    name_en: "Multiple Primary Nodes in Single Primary Mode"
    db_type: "mysql"
    risk_level: "critical"
    check_type: "custom"
    check_sql: "SELECT COUNT(*) as count FROM performance_schema.replication_group_members WHERE member_role = 'PRIMARY'"
    threshold:
      max: 1
    recommendation: "单主模式下存在多个主节点，可能存在脑裂风险"
    recommendation_en: "Multiple primary nodes in single primary mode, possible split-brain risk"

  # 规则 3：Group Replication 延迟过高
  - name: "Group Replication 延迟过高"
    name_en: "Group Replication Lag Too High"
    db_type: "mysql"
    risk_level: "warning"
    check_type: "custom"
    check_sql: "SELECT MAX(COUNT_TRANSACTIONS_BEHIND) as lag FROM performance_schema.replication_group_member_stats"
    threshold:
      max: 10
    recommendation: "Group Replication 延迟过高，可能影响数据一致性"
    recommendation_en: "Group Replication lag is too high, may affect data consistency"
```

---

## 6. 插件生命周期管理

### 6.1 生命周期方法详解

#### 🔧 on_install(db_path)

**调用时机**：插件安装时（通过插件市场或 API）

**职责**：
1. 创建巡检模板（从 `template_data.json` 读取）
2. 创建基线配置（从 `baseline_data.json` 读取）
3. 复制规则引擎文件到规则目录

**注意事项**：
- ✅ **幂等性**：重复调用不会创建重复数据（先查询是否已存在）
- ✅ **错误处理**：某一步失败应回滚已创建的数据
- ✅ **日志输出**：使用 `print()` 输出安装进度

#### 🔧 on_uninstall(db_path)

**调用时机**：插件卸载时（通过插件市场或 API）

**职责**：
1. 删除巡检模板（使用 `force=True` 删除预置模板）
2. 删除基线配置
3. 删除规则引擎文件

**注意事项**：
- ✅ **彻底清理**：确保所有关联数据都已删除
- ✅ **容错处理**：某些数据可能已被手动删除，应捕获异常

#### 🔧 on_enable()

**调用时机**：插件启用时

**用途**：执行启用前的检查（如依赖是否满足）

#### 🔧 on_disable()

**调用时机**：插件禁用时

**用途**：执行禁用前的清理（如停止后台任务）

### 6.2 生命周期调用流程

```
用户点击"安装"按钮
   ↓
PluginMarket.install(plugin_name)
   ↓
1. 检查依赖
2. 检查冲突
3. 复制插件文件到 installed/ 目录
4. 加载插件实例
5. 调用 plugin.on_install(db_path)   ← 在这里初始化数据
6. 更新插件状态为"已安装"
   ↓
安装完成
```

```
用户点击"卸载"按钮
   ↓
PluginMarket.uninstall(plugin_name)
   ↓
1. 加载插件实例（如果内存中没有，从 plugin.json 读取 cleanup 配置）
2. 调用 plugin.on_uninstall(db_path)   ← 在这里清理数据
3. 删除 installed/ 目录下的插件文件
4. 更新插件状态为"未安装"
   ↓
卸载完成
```

### 6.3 cleanup 配置详解

`plugin.json` 中的 `cleanup` 配置用于**卸载时插件实例不在内存中**的场景。

#### 🔍 问题场景

```
用户安装插件 → 重启 DBCheck → 插件实例不在内存中
                     ↓
           用户点击"卸载"按钮
                     ↓
           无法通过 plugin.on_uninstall() 清理数据
                     ↓
           需要读取 plugin.json 的 cleanup 配置
```

#### 📝 cleanup 配置示例

```json
{
  "cleanup": {
    "db_types": ["mongodb"],
    "data_types": ["template", "baseline", "rules"]
  }
}
```

#### 🔧 插件市场卸载逻辑

```python
# plugin_market.py (简化代码)

def uninstall(self, plugin_name: str):
    """卸载插件"""
    # 1. 尝试调用插件的 on_uninstall()
    plugin = self.get_plugin_instance(plugin_name)
    
    if plugin:
        # 插件实例在内存中，直接调用
        plugin.on_uninstall(db_path=self.default_db_path)
    else:
        # 插件实例不在内存中，读取 cleanup 配置
        plugin_config = self._load_plugin_json(plugin_name)
        
        if "cleanup" in plugin_config:
            cleanup = plugin_config["cleanup"]
            db_types = cleanup.get("db_types", [])
            data_types = cleanup.get("data_types", [])
            
            # 清理模板
            if "template" in data_types:
                for db_type in db_types:
                    templates = get_templates_by_db_type(db_type, db_path=self.default_db_path)
                    for t in templates:
                        delete_template(t["id"], db_path=self.default_db_path, force=True)
            
            # 清理基线
            if "baseline" in data_types:
                for db_type in db_types:
                    delete_baselines_by_db_type(db_type, db_path=self.default_db_path)
            
            # 清理规则
            if "rules" in data_types:
                for db_type in db_types:
                    delete_rules_by_db_type(db_type)
    
    # 2. 删除插件文件
    self._delete_plugin_files(plugin_name)
    
    print(f"插件 {plugin_name} 卸载完成")
```

---

## 7. 插件打包与发布

### 7.1 插件打包

#### 📦 打包为 ZIP 文件

```bash
# 进入插件目录
cd D:/DBCheck/plugins/available/mongodb

# 打包为 ZIP
zip -r mongodb_v1.0.0.zip *

# 上传到插件市场（或分享给其他用户）
```

#### 📦 打包规范

**必须包含的文件**：
- ✅ `plugin.json`
- ✅ `main_plugin.py`
- ✅ `template_data.json`（数据库插件必须）
- ✅ `baseline_data.json`（数据库插件必须）
- ✅ `rules/builtin/*.yaml`（必须）

**可选包含的文件**：
- ❌ 测试文件（`test_*.py`）
- ❌ 临时文件（`.pyc`、`.DS_Store`）
- ❌ 开发文档（`README.md` 可选）

### 7.2 插件发布

#### 🌐 发布到 DBCheck 插件市场

1. **Fork DBCheck 仓库**
2. **将插件提交到 `plugins/available/` 目录**
3. **提交 Pull Request**
4. **等待审核**

#### 📤 自行分发

1. **GitHub Release**：在您的 GitHub 仓库创建 Release，上传插件 ZIP
2. **分享 ZIP 文件**：用户下载后，解压到 `plugins/available/` 目录
3. **文档说明**：提供安装和使用文档

---

## 8. 最佳实践

### 8.1 插件开发规范

#### ✅ DO（推荐做法）

1. **幂等性**：`on_install()` 可重复调用，不会创建重复数据
2. **彻底清理**：`on_uninstall()` 清理所有关联数据
3. **错误处理**：捕获异常，输出友好错误信息
4. **日志输出**：使用 `print()` 输出安装/卸载进度
5. **版本兼容**：在 `plugin.json` 中指定 `min_db_version`
6. **文档完善**：提供 `README.md` 说明使用方法

#### ❌ DON'T（不推荐做法）

1. **硬编码路径**：使用 `self.plugin_dir` 获取插件目录
2. **依赖全局状态**：插件应完全独立，不依赖平台全局变量
3. **忽略错误**：捕获异常后应记录日志或抛出明确错误
4. **数据泄漏**：卸载时必须清理所有数据

### 8.2 性能优化

#### 🚀 巡检查询优化

1. **限制结果集大小**：使用 `limit` 避免返回过多数据
2. **只查询必要字段**：避免 `SELECT *`
3. **使用索引**：确保查询使用索引
4. **批量查询**：合并多个小查询

#### 🚀 模板初始化优化

1. **批量插入**：使用 `executemany()` 批量插入数据
2. **事务处理**：在事务中执行初始化，失败则回滚
3. **进度提示**：数据量大时输出进度提示

### 8.3 调试技巧

#### 🐛 调试 on_install()

```bash
# 1. 手动调用 on_install()
cd D:/DBCheck
python -c "
from plugins.available.mongodb.main_plugin import MongoDBPlugin
plugin = MongoDBPlugin()
plugin.on_install(db_path='data/inspection.db')
"

# 2. 查看数据库中的数据
sqlite3 data/inspection.db "SELECT * FROM inspection_templates WHERE db_type='mongodb';"
```

#### 🐛 调试 on_uninstall()

```bash
# 1. 手动调用 on_uninstall()
cd D:/DBCheck
python -c "
from plugins.available.mongodb.main_plugin import MongoDBPlugin
plugin = MongoDBPlugin()
plugin.on_uninstall(db_path='data/inspection.db')
"

# 2. 确认数据已清理
sqlite3 data/inspection.db "SELECT * FROM inspection_templates WHERE db_type='mongodb';"
```

#### 🐛 查看插件市场日志

```bash
# 插件市场的操作会输出到控制台
cd D:/DBCheck
python web_ui.py

# 观察控制台输出的插件安装/卸载日志
```

---

## 9. 常见问题（FAQ）

### 9.1 插件安装后模板未创建？

**可能原因**：
1. `template_data.json` 格式错误
2. `on_install()` 方法未实现或实现错误
3. 数据库连接失败

**解决方法**：
```bash
# 手动测试 on_install()
python -c "
from plugins.available.mongodb.main_plugin import MongoDBPlugin
plugin = MongoDBPlugin()
plugin.on_install(db_path='data/inspection.db')
"
```

### 9.2 插件卸载后模板未删除？

**可能原因**：
1. `on_uninstall()` 方法未实现
2. 卸载时插件实例不在内存中，且 `plugin.json` 缺少 `cleanup` 配置
3. `delete_template()` 未传入 `force=True`（无法删除 `is_preset=1` 的模板）

**解决方法**：
- 确保 `on_uninstall()` 正确实现
- 在 `plugin.json` 中添加 `cleanup` 配置
- 删除模板时传入 `force=True`

### 9.3 插件无法加载？

**可能原因**：
1. `plugin.json` 中 `main_file` 字段错误
2. `main_plugin.py` 中插件类名错误
3. 依赖未安装

**解决方法**：
```bash
# 检查插件元数据
cat plugins/available/mongodb/plugin.json

# 检查依赖
pip install pymongo>=4.0
```

---

## 10. 总结

### 10.1 本文涵盖内容

✅ DBCheck 工具简介  
✅ v2.8.0 版本更新概览  
✅ 插件系统架构（数据库插件 + 规则插件）  
✅ 数据库插件开发详细步骤（6 步）  
✅ 规则插件开发详细步骤（6 步）  
✅ 插件生命周期管理  
✅ 插件打包与发布  
✅ 最佳实践与常见问题  

### 10.2 下一步学习

- 📚 阅读现有插件源码（`plugins/available/mongodb/`、`plugins/available/oracle_jdbc/`）
- 📚 阅读插件核心代码（`plugin_core.py`、`plugin_market.py`）
- 📚 阅读规则引擎代码（`pro/rules/`）
- 🧪 动手开发一个简单的规则插件

### 10.3 获取帮助

- 🌐 官网：[https://dbcheck.top](https://dbcheck.top)
- 📧 邮箱：sdfiyon@gmail.com
- 💬 微信公众号：山东Oracle用户组
- 🐙 GitHub：[fiyo/DBCheck](https://github.com/fiyo/DBCheck)

---

**文档版本**：v1.0  
**更新日期**：2026-07-03  
**适用版本**：DBCheck v2.8.0+
