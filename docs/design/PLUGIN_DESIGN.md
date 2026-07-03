# DBCheck 插件系统与插件市场设计

> **版本**: v1.0 草案  
> **日期**: 2026-06-18  
> **状态**: 设计阶段

---

## 目录

1. [概述与目标](#1-概述与目标)
2. [插件能做什么](#2-插件能做什么)
3. [插件包结构](#3-插件包结构)
4. [插件生命周期](#4-插件生命周期)
5. [扩展点 API 设计](#5-扩展点-api-设计)
6. [插件市场架构](#6-插件市场架构)
7. [安全模型](#7-安全模型)
8. [分期路线图](#8-分期路线图)

---

## 1. 概述与目标

### 为什么需要插件系统

DBCheck 已支持 10 种数据库，但用户需求远超我们能覆盖的范围：

- 某银行需要定制「等保 2.0 合规检查」插件
- 某公司内网用飞书，需要 Webhook 通知插件
- Oracle DBA 想分享自己写的「ASM 磁盘组健康检查」给社区
- 某人写了个 TiDB Region 热点分析的检查，别人也需要

**插件系统的核心价值：让 DBCheck 从一个「功能完备的工具」演进为「可无限扩展的平台」。**

### 设计原则

| 原则 | 说明 |
|------|------|
| **零侵入安装** | 拖入文件夹即可安装，无需重启 |
| **安全隔离** | 插件崩溃不影响主程序，敏感操作需声明权限 |
| **渐进复杂度** | 简单插件 10 行代码搞定，复杂插件支持完整 API |
| **社区驱动** | 插件市场低门槛发布，用户评价和下载量驱动质量 |

---

## 2. 插件能做什么

### 2.1 插件类型矩阵

| 类型 | 说明 | 优先级 | 示例 |
|------|------|:---:|------|
| 🔍 **巡检规则** | 新增数据库检查项 | P0 | ASM 磁盘组检查、TiKV Region 健康度 |
| 📊 **报告模板** | 自定义 Word/HTML/PDF 报告 | P0 | 带公司 Logo 的定制报告、纯英文模板 |
| 🔔 **通知渠道** | 巡检完成后推送 | P1 | 飞书、钉钉、Slack、Telegram、短信 |
| 🔗 **外部集成** | 对接第三方系统 | P1 | Grafana 数据源、Prometheus Exporter、CMDB 同步 |
| 🎨 **可视化** | Web UI 定制面板 | P2 | 自定义仪表盘、数据库拓扑图 |
| 📤 **导出格式** | 自定义导出 | P2 | CSV Schema 导出、Markdown 报告 |
| 🤖 **AI 后端** | 自定义 AI 模型 | P2 | 对接企业私有模型、HuggingFace 模型 |
| 🧩 **数据源** | 新增数据库类型 | P2 | 社区贡献的 Couchbase / MongoDB 支持 |

### 2.2 插件市场分类

```
插件市场
├── 巡检规则
│   ├── MySQL 增强检查 (社区)
│   ├── PostgreSQL 死元组深度分析 (社区)
│   ├── Oracle ASM 健康检查 (官方)
│   └── 等保 2.0 合规包 (企业)
├── 通知渠道
│   ├── 飞书通知 (社区)
│   ├── 钉钉通知 (社区)
│   ├── Slack 通知 (官方)
│   └── 短信通知 (企业)
├── 报告模板
│   ├── 银行版报告 (企业)
│   ├── 医疗版报告 (社区)
│   └── 英文精简版 (官方)
├── 外部集成
│   ├── Grafana 数据源 (社区)
│   ├── Prometheus Exporter (官方)
│   └── ServiceNow 集成 (企业)
└── 主题/UI
    ├── 暗夜主题增强 (社区)
    └── 移动端适配 (社区)
```

---

## 3. 插件包结构

### 3.1 文件结构

```
my-dbcheck-plugin/
├── plugin.json          # 插件清单（必需）
├── __init__.py          # 插件入口（必需）
├── checker.py           # 巡检规则（可选）
├── notifier.py          # 通知实现（可选）
├── template.docx        # 报告模板（可选）
├── static/              # 前端资源（可选）
│   ├── dashboard.js
│   └── style.css
├── i18n/                # 国际化（可选）
│   ├── zh.json
│   └── en.json
├── requirements.txt     # Python 依赖（可选）
└── README.md            # 说明文档（必需）
```

### 3.2 plugin.json 清单

```json
{
  "name": "dbcheck-plugin-asm-health",
  "version": "1.0.0",
  "title": "Oracle ASM 磁盘组健康检查",
  "description": "检查 ASM 磁盘组使用率、冗余状态、再平衡进度",
  "author": {
    "name": "Jack Ge",
    "email": "jack@example.com",
    "url": "https://github.com/fiyo"
  },
  "icon": "static/icon.png",
  "homepage": "https://github.com/fiyo/dbcheck-plugin-asm-health",
  "license": "MIT",
  "keywords": ["oracle", "asm", "storage"],
  "categories": ["inspection"],
  "dbcheck": {
    "minVersion": "2.5.0",
    "maxVersion": "3.x"
  },
  "capabilities": {
    "inspections": ["oracle_asm_health"],
    "reports": [],
    "notifiers": [],
    "integrations": []
  },
  "permissions": [
    "database:oracle:read",
    "file:report:write"
  ],
  "dependencies": {
    "python": ">=3.10",
    "dbcheck": ">=2.5.0"
  },
  "entry": "__init__.py",
  "i18n": {
    "zh": "i18n/zh.json",
    "en": "i18n/en.json"
  }
}
```

### 3.3 插件入口 (__init__.py)

```python
"""
Oracle ASM 磁盘组健康检查插件
"""
from dbcheck.plugin import InspectionPlugin, register

class ASMHealthCheck(InspectionPlugin):
    """ASM 磁盘组健康检查"""

    # 插件元信息
    id = "oracle_asm_health"
    name = "ASM 磁盘组健康检查"
    version = "1.0.0"
    db_types = ["oracle"]  # 适用的数据库类型
    risk_levels = ["LOW", "MEDIUM", "HIGH"]

    def get_queries(self):
        """返回需要执行的 SQL 列表"""
        return [
            {
                "key": "asm_diskgroup_usage",
                "sql": """
                    SELECT name, total_mb, free_mb,
                           ROUND((1 - free_mb / NULLIF(total_mb, 0)) * 100, 1) AS used_pct
                    FROM v$asm_diskgroup
                """,
                "desc": "ASM 磁盘组使用率"
            },
            {
                "key": "asm_rebalance",
                "sql": """
                    SELECT group_number, operation, state, power, est_minutes
                    FROM v$asm_operation
                """,
                "desc": "ASM 再平衡状态"
            }
        ]

    def analyze(self, context):
        """分析查询结果，返回风险列表"""
        risks = []
        # 检查磁盘组使用率
        for row in context.get("asm_diskgroup_usage", {}).get("rows", []):
            if row.get("used_pct", 0) > 85:
                risks.append({
                    "level": "HIGH" if row["used_pct"] > 95 else "MEDIUM",
                    "title": f'ASM 磁盘组 {row["name"]} 使用率 {row["used_pct"]}%',
                    "suggestion": "建议扩容或迁移数据",
                    "fix_sql": None  # ASM 操作太危险，不提供自动修复
                })
        # 检查再平衡
        reb_rows = context.get("asm_rebalance", {}).get("rows", [])
        if reb_rows:
            for row in reb_rows:
                risks.append({
                    "level": "MEDIUM",
                    "title": f'ASM 再平衡进行中: {row.get("operation")} (预计 {row.get("est_minutes")} 分钟)',
                    "suggestion": "再平衡期间注意 IO 压力",
                    "fix_sql": None
                })
        return risks

# 注册插件
register(ASMHealthCheck())
```

---

## 4. 插件生命周期

### 4.1 状态机

```
下载 → [已下载] → 安装 → [已安装] → 启用 → [运行中]
                    ↑                    ↓
                     ←──── 禁用 ──── [已禁用]
                    ↓
                 卸载 → [已删除]
```

### 4.2 安装方式

| 方式 | 说明 | 适用场景 |
|------|------|---------|
| **插件市场一键安装** | Web UI 点安装，自动下载解压 | 普通用户 |
| **本地文件夹** | 拖入 `plugins/` 目录，自动检测 | 开发调试 |
| **Git URL** | 输入 GitHub 地址，自动克隆 | 尝鲜未上架的插件 |
| **pip install** | `pip install dbcheck-plugin-xxx` | 高级用户 |
| **Docker volume** | `-v ./my-plugins:/app/plugins` | Docker 部署 |

### 4.3 运行时加载流程

```
1. DBCheck 启动
2. 扫描 plugins/ 目录下的所有 plugin.json
3. 读取 manifest，检查版本兼容性
4. 加载 __init__.py，实例化插件类
5. 调用 validate() 验证插件完整性
6. 调用 on_load() 初始化钩子
7. 注册扩展点到内核
8. 插件就绪 ✓
```

### 4.4 热加载/卸载

```python
# 启用插件
POST /api/plugins/{plugin_id}/enable

# 禁用插件
POST /api/plugins/{plugin_id}/disable

# 卸载插件
DELETE /api/plugins/{plugin_id}

# 列出所有插件
GET /api/plugins
```

---

## 5. 扩展点 API 设计

### 5.1 核心扩展点

```
DBCheck 内核
├── inspection_plugins    # 巡检规则扩展
├── report_plugins        # 报告模板扩展
├── notifier_plugins      # 通知渠道扩展
├── integration_plugins   # 外部集成扩展
├── viz_plugins           # 可视化扩展
├── export_plugins        # 导出格式扩展
├── ai_plugins            # AI 后端扩展
└── datasource_plugins    # 数据源扩展
```

### 5.2 InspectionPlugin 基类

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

@dataclass
class RiskItem:
    level: str          # HIGH / MEDIUM / LOW
    title: str
    description: str = ""
    suggestion: str = ""
    fix_sql: Optional[str] = None
    category: str = "plugin"
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class InspectionQuery:
    key: str
    sql: str
    desc_zh: str
    desc_en: str = ""
    db_type: str = ""   # 限制数据库类型，空=全部

class InspectionPlugin(ABC):
    """巡检规则插件基类"""

    id: str = ""
    name: str = ""
    version: str = "0.1.0"
    db_types: List[str] = []      # 适用数据库
    risk_levels: List[str] = ["LOW", "MEDIUM", "HIGH"]

    @abstractmethod
    def get_queries(self) -> List[InspectionQuery]:
        """返回需要执行的 SQL 列表"""
        ...

    @abstractmethod
    def analyze(self, context: Dict[str, Any]) -> List[RiskItem]:
        """分析查询结果"""
        ...

    def validate(self) -> bool:
        """插件自检"""
        return bool(self.id and self.get_queries())

    def on_load(self):
        """加载时回调"""
        pass

    def on_unload(self):
        """卸载时回调"""
        pass
```

### 5.3 NotifierPlugin 基类

```python
@dataclass
class InspectionResult:
    db_type: str
    host: str
    port: int
    database: str
    risk_count: int
    health_score: float
    report_file: str
    report_name: str
    finished_at: str
    summary: Dict[str, Any]

class NotifierPlugin(ABC):
    """通知渠道插件基类"""

    id: str = ""
    name: str = ""
    config_schema: Dict[str, Any] = {}  # JSON Schema

    @abstractmethod
    def send(self, result: InspectionResult, config: Dict[str, str]) -> bool:
        """发送通知"""
        ...

    def get_config_ui(self) -> Optional[Dict]:
        """返回前端配置表单定义（可选）"""
        return None
```

### 5.4 插件注册器

```python
# dbcheck/plugin/registry.py

class PluginRegistry:
    """全局插件注册表"""

    _inspections: Dict[str, InspectionPlugin] = {}
    _notifiers: Dict[str, NotifierPlugin] = {}
    _reports: Dict[str, ReportPlugin] = {}
    _integrations: Dict[str, IntegrationPlugin] = {}

    @classmethod
    def register_inspection(cls, plugin: InspectionPlugin):
        cls._inspections[plugin.id] = plugin

    @classmethod
    def get_inspections_for_db(cls, db_type: str) -> List[InspectionPlugin]:
        return [p for p in cls._inspections.values()
                if not p.db_types or db_type in p.db_types]

# 便捷装饰器
def register(plugin):
    """注册插件到全局注册表"""
    if isinstance(plugin, InspectionPlugin):
        PluginRegistry.register_inspection(plugin)
    elif isinstance(plugin, NotifierPlugin):
        PluginRegistry.register_notifier(plugin)
    # ...
```

### 5.5 巡检引擎集成点

```python
# main_oracle_full.py (改造后)

from dbcheck.plugin.registry import PluginRegistry

class OracleInspector:
    def run_inspection(self):
        # 1. 执行内置巡检
        results = self._run_builtin_inspections()

        # 2. 执行插件巡检
        for plugin in PluginRegistry.get_inspections_for_db("oracle"):
            try:
                queries = plugin.get_queries()
                for q in queries:
                    data = self._execute_query(q.sql)
                    results[q.key] = data
                risks = plugin.analyze(results)
                all_risks.extend(risks)
            except Exception as e:
                logger.warning(f"插件 [{plugin.id}] 执行失败: {e}")

        return results, all_risks
```

---

## 6. 插件市场架构

### 6.1 总体架构

```
┌──────────────────────────────────────────────────┐
│                 DBCheck Web UI                    │
│  ┌────────────┐  ┌──────────┐  ┌──────────────┐  │
│  │ 插件浏览    │  │ 我的插件  │  │ 开发者中心    │  │
│  │ 搜索/排行   │  │ 启用/禁用 │  │ 发布/更新    │  │
│  └────────────┘  └──────────┘  └──────────────┘  │
└──────────────────────┬───────────────────────────┘
                       │ REST API
┌──────────────────────▼───────────────────────────┐
│              DBCheck Plugin Server                │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ 插件索引  │ │ 下载服务  │ │ 统计/评分        │  │
│  └──────────┘ └──────────┘ └──────────────────┘  │
└──────────────────────┬───────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────┐
│             插件市场存储（方案三选一）                │
│                                                   │
│  方案A: GitHub Releases + registry.json (MVP)     │
│  方案B: 独立 API 服务器 (中期)                      │
│  方案C: 插件市场平台 (长期)                          │
└───────────────────────────────────────────────────┘
```

### 6.2 MVP 方案：GitHub 驱动的插件市场（推荐）

**为什么选这个**：零成本、零运维、即时可用，VS Code 插件市场也是 GitHub 驱动。

```
流程：
1. 开发者  fork 官方插件模板仓库
2. 开发者  开发插件 → 推到自己的 GitHub
3. 开发者  在官方 registry 仓库提 PR，加一条插件索引
4. CI      自动验证 plugin.json + 代码签名
5. 自动    合并后，插件出现在 DBCheck 插件市场
6. 用户    在 Web UI 浏览、搜索、一键安装
```

**registry.json 索引文件**（托管在 `fiyo/dbcheck-plugins` 仓库）：

```json
{
  "version": "1",
  "updated": "2026-06-18T12:00:00Z",
  "plugins": [
    {
      "id": "oracle-asm-health",
      "name": "Oracle ASM 磁盘组健康检查",
      "version": "1.0.0",
      "author": "Jack Ge",
      "description": "检查 ASM 磁盘组使用率、冗余状态、再平衡进度",
      "download": "https://github.com/fiyo/dbcheck-plugin-asm-health/releases/download/v1.0.0/plugin.zip",
      "homepage": "https://github.com/fiyo/dbcheck-plugin-asm-health",
      "category": "inspection",
      "db_types": ["oracle"],
      "min_dbcheck_version": "2.5.0",
      "license": "MIT",
      "downloads": 1280,
      "rating": 4.8,
      "reviews": 23,
      "verified": true
    }
  ]
}
```

### 6.3 用户端 Web UI

```
┌─────────────────────────────────────────────────────┐
│  插件市场                          🔍 搜索插件...    │
│─────────────────────────────────────────────────────│
│  [全部] [巡检规则] [通知] [报告] [集成] [主题]       │
│                                                     │
│  ┌─────────────────────────┐ ┌────────────────────┐ │
│  │ 🔍 ASM 磁盘组健康检查    │ │ 🔔 飞书通知          │ │
│  │ Jack Ge  ⭐4.8  1.2k↓  │ │ 官方  ⭐4.9  5.6k↓  │ │
│  │ Oracle ASM 监控         │ │ 巡检完成推送飞书     │ │
│  │ [安装]                  │ │ [已安装] [禁用]     │ │
│  └─────────────────────────┘ └────────────────────┘ │
│  ┌─────────────────────────┐ ┌────────────────────┐ │ │
│  │ 📊 等保2.0合规包         │ │ 📝 银行版报告模板    │ │
│  │ 安全社区  ⭐4.6  890↓   │ │ 某银行DBA ⭐4.7 320↓│ │
│  │ 等保检查项合集           │ │ 定制Word报告         │ │
│  │ [安装]                   │ │ [安装]              │ │
│  └─────────────────────────┘ └────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### 6.4 开发者工作流

```bash
# 1. 克隆官方模板
git clone https://github.com/fiyo/dbcheck-plugin-template.git my-plugin
cd my-plugin

# 2. 修改 plugin.json + 写代码
vim plugin.json
vim checker.py

# 3. 本地测试
dbcheck plugin test ./    # 内置测试命令

# 4. 打包
dbcheck plugin pack ./    # 生成 my-plugin-1.0.0.zip

# 5. 在 GitHub 创建仓库 + 发布 Release
# 上传 my-plugin-1.0.0.zip 到 Release

# 6. 在 registry 仓库提 PR
# 编辑 registry.json，加一条索引

# 7. CI 自动验证 → 合并 → 上架 🎉
```

---

## 7. 安全模型

### 7.1 权限声明

插件必须在 `plugin.json` 中声明所需权限，DBCheck 运行时可拦截：

| 权限 | 说明 | 风险 |
|------|------|:---:|
| `database:*:read` | 读取所有数据库 | 低 |
| `database:*:write` | 写入所有数据库 | 高 |
| `database:mysql:read` | 只读 MySQL | 低 |
| `file:report:write` | 写报告文件 | 中 |
| `file:config:read` | 读配置文件 | 中 |
| `network:outbound` | 发起网络请求 | 中 |
| `system:exec` | 执行系统命令 | 高 |
| `ai:model:local` | 调用本地 AI 模型 | 低 |

### 7.2 运行时隔离

```
┌─────────────────────────────────────────┐
│              DBCheck 主进程              │
│  ┌─────────────────────────────────┐    │
│  │         插件沙箱 (每个插件独立)     │    │
│  │  · 超时限制 (30s)                 │    │
│  │  · 内存限制 (256MB)               │    │
│  │  · 文件访问白名单                 │    │
│  │  · 网络请求拦截                   │    │
│  │  · 数据库操作审计                 │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

### 7.3 代码签名

```
发布流程：
1. 开发者提交 PR 到 registry
2. CI 自动扫描代码（静态分析 + 依赖审计）
3. DBCheck 官方 review
4. 通过后，官方私钥签名 plugin.zip
5. 用户安装时，DBCheck 验证签名
```

---

## 8. 分期路线图

### 第一阶段：基础设施（2026 Q4，4周）

| 任务 | 工作量 | 产出 |
|------|:---:|------|
| `plugin.json` 规范定义 | 2天 | 文档 + JSON Schema |
| `PluginRegistry` + `InspectionPlugin` 基类 | 3天 | `dbcheck/plugin/` 模块 |
| 插件目录扫描 + `plugin.json` 解析 | 2天 | 启动时自动加载 |
| 巡检引擎集成点 | 2天 | 各库 `main_*.py` 调用插件 |
| 插件「安装/启用/禁用/卸载」API | 3天 | REST API 四件套 |
| Web UI 插件管理页面 | 3天 | 我的插件列表 + 开关 |
| 热加载/热卸载 | 2天 | 不停机管理插件 |
| 单元测试 + 示例插件 | 3天 | `demo-plugin` |

### 第二阶段：插件市场 MVP（2027 Q1，3周）

| 任务 | 工作量 | 产出 |
|------|:---:|------|
| `registry.json` 规范 + 示例仓库 | 1天 | `fiyo/dbcheck-plugins` |
| 插件市场 API 服务 | 2天 | 搜索/列表/详情 |
| Web UI 插件市场页面 | 3天 | 浏览 + 一键安装 |
| CI 自动验证流水线 (GitHub Actions) | 2天 | PR → 自动检查 → 合并 |
| Web UI 开发者中心（发布插件） | 2天 | 发布向导 |
| 插件下载统计 | 1天 | 下载量计数 |
| 3个官方插件上架 | 1周 | ASM 健康、飞书通知、等保合规 |

### 第三阶段：生态运营（2027 Q2+）

| 任务 | 说明 |
|------|------|
| 插件开发者文档完整版 | 教程 + API 参考 + 最佳实践 |
| 插件评分/评价系统 | 社区驱动的质量保障 |
| 官方认证插件标识 | 官方验证过的插件打 ✅ |
| 企业插件私有市场 | 企业内部插件市场部署方案 |
| 插件开发大赛 | 激励社区贡献 |

---

### 附录：对接现有功能清单

| 现有功能 | 插件化改造 |
|---------|-----------|
| 160+ 内置规则 | 保持内置，同时支持插件新增规则 |
| `pro/rules/builtin/*.yaml` | 不变，插件规则追加到规则引擎 |
| 通知（邮件/Webhook） | 内置保留，插件可新增渠道（飞书等） |
| 报告模板 `templates/` | 保留，插件可追加模板 |
| AI 后端（Ollama） | 保留，插件可新增后端（企业私有模型） |
| 配置基线 | 插件可追加新的基线检查项 |

---

> **下一步**：确认此设计方案后，先实现第一阶段 `InspectionPlugin` 基类 + 插件注册表 + 巡检引擎集成，快速跑通一个 demo 插件。  
> 联系：Jack Ge &nbsp;|&nbsp; GitHub: [fiyo/DBCheck](https://github.com/fiyo/DBCheck)
