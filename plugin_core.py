"""
DBCheck 插件系统核心 —— 基类 + 注册表 + 加载器 + 沙箱

用法：
    from plugin_core import (
        InspectionPlugin, NotifierPlugin, RiskItem, InspectionQuery,
        PluginRegistry, register, load_plugins, get_plugin_manager
    )

插件包结构：
    plugins/my-plugin/
        plugin.json      # 清单（必需）
        __init__.py       # 入口，调用 register()
        checker.py        # 巡检逻辑
        README.md         # 说明
"""

import os, sys, json, logging, traceback, time
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from functools import wraps

logger = logging.getLogger('plugin')

# ───────────────────────────────────────────────────────────────
# 数据类
# ───────────────────────────────────────────────────────────────

@dataclass
class RiskItem:
    """一条风险"""
    level: str              # HIGH / MEDIUM / LOW
    title: str
    description: str = ""
    suggestion: str = ""
    fix_sql: Optional[str] = None
    category: str = "plugin"

@dataclass
class InspectionQuery:
    """一条巡检 SQL"""
    key: str
    sql: str
    desc_zh: str
    desc_en: str = ""
    db_type: str = ""

# ───────────────────────────────────────────────────────────────
# 插件基类
# ───────────────────────────────────────────────────────────────

class InspectionPlugin(ABC):
    """巡检规则插件基类"""

    id: str = ""
    name: str = ""
    version: str = "0.1.0"
    db_types: List[str] = []       # 适用数据库，空=全部
    author: str = ""
    description: str = ""

    def get_queries(self) -> List[InspectionQuery]:
        """返回需要执行的 SQL 列表"""
        return []

    def analyze(self, context: Dict[str, Any]) -> List[RiskItem]:
        """
        分析查询结果，返回风险列表。
        context: { query_key: {"headers": [...], "rows": [[...], ...]} }
        """
        return []

    def validate(self) -> bool:
        """插件自检"""
        return bool(self.id)

    def on_load(self):
        """加载时回调"""
        pass

    def on_unload(self):
        """卸载时回调"""
        pass


class NotifierPlugin(ABC):
    """通知渠道插件基类"""

    id: str = ""
    name: str = ""

    @abstractmethod
    def send(self, result: Dict[str, Any], config: Dict[str, str]) -> bool:
        """
        发送通知。
        result: 巡检结果字典
        config: 用户配置（如 webhook_url）
        """
        ...

    def get_config_schema(self) -> Dict[str, Any]:
        """返回 JSON Schema 格式的配置定义，前端自动渲染表单"""
        return {}


# ───────────────────────────────────────────────────────────────
# 注册表
# ───────────────────────────────────────────────────────────────

class PluginRegistry:
    """全局插件注册表（单例）"""

    _inspections: Dict[str, InspectionPlugin] = {}
    _notifiers: Dict[str, NotifierPlugin] = {}
    _all_plugins: Dict[str, Any] = {}  # id → manifest dict

    @classmethod
    def register_inspection(cls, plugin: InspectionPlugin):
        cls._inspections[plugin.id] = plugin
        cls._all_plugins[plugin.id] = {
            'id': plugin.id,
            'name': plugin.name,
            'version': plugin.version,
            'type': 'inspection',
            'db_types': plugin.db_types,
            'author': plugin.author,
            'description': plugin.description,
        }
        logger.info(f"插件注册: inspection/{plugin.id} v{plugin.version}")

    @classmethod
    def register_notifier(cls, plugin: NotifierPlugin):
        cls._notifiers[plugin.id] = plugin
        cls._all_plugins[plugin.id] = {
            'id': plugin.id,
            'name': plugin.name,
            'type': 'notifier',
        }
        logger.info(f"插件注册: notifier/{plugin.id}")

    @classmethod
    def get_inspections_for_db(cls, db_type: str) -> List[InspectionPlugin]:
        """获取适用于某数据库类型的所有巡检插件"""
        return [p for p in cls._inspections.values()
                if not p.db_types or db_type in p.db_types]

    @classmethod
    def list_inspections(cls) -> List[Dict]:
        return list(cls._inspections.keys())

    @classmethod
    def list_notifiers(cls) -> List[Dict]:
        return list(cls._notifiers.keys())

    @classmethod
    def list_all(cls) -> List[Dict]:
        return list(cls._all_plugins.values())

    @classmethod
    def unregister(cls, plugin_id: str):
        cls._inspections.pop(plugin_id, None)
        cls._notifiers.pop(plugin_id, None)
        cls._all_plugins.pop(plugin_id, None)
        logger.info(f"插件注销: {plugin_id}")

    @classmethod
    def clear(cls):
        cls._inspections.clear()
        cls._notifiers.clear()
        cls._all_plugins.clear()


# ───────────────────────────────────────────────────────────────
# 便捷注册器
# ───────────────────────────────────────────────────────────────

def register(plugin):
    """注册插件装饰器/函数"""
    if isinstance(plugin, InspectionPlugin):
        PluginRegistry.register_inspection(plugin)
    elif isinstance(plugin, NotifierPlugin):
        PluginRegistry.register_notifier(plugin)
    return plugin


# ───────────────────────────────────────────────────────────────
# 插件加载器
# ───────────────────────────────────────────────────────────────

def find_plugins(plugins_dir: str = None) -> List[str]:
    """扫描插件目录，返回找到的 plugin.json 路径列表"""
    if plugins_dir is None:
        plugins_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plugins')
    if not os.path.isdir(plugins_dir):
        return []
    results = []
    for entry in os.listdir(plugins_dir):
        plugin_dir = os.path.join(plugins_dir, entry)
        if not os.path.isdir(plugin_dir):
            continue
        manifest_path = os.path.join(plugin_dir, 'plugin.json')
        if os.path.isfile(manifest_path):
            results.append(plugin_dir)
    return results


def load_plugin(plugin_dir: str) -> Optional[Dict]:
    """加载单个插件目录，返回 manifest 或 None"""
    manifest_path = os.path.join(plugin_dir, 'plugin.json')
    if not os.path.isfile(manifest_path):
        logger.warning(f"跳过 {plugin_dir}: 缺少 plugin.json")
        return None

    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except Exception as e:
        logger.warning(f"解析 plugin.json 失败: {e}")
        return None

    plugin_id = manifest.get('name', os.path.basename(plugin_dir))

    # 版本检查
    min_ver = manifest.get('dbcheck', {}).get('minVersion', '0')
    # TODO: 实际版本比较

    # 安装 Python 依赖（如果有）
    req_path = os.path.join(plugin_dir, 'requirements.txt')
    if os.path.isfile(req_path):
        try:
            import subprocess
            subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-r', req_path, '--quiet'],
                check=False, timeout=60
            )
        except Exception:
            logger.warning(f"插件 {plugin_id} 依赖安装失败，继续加载")

    # 动态导入
    init_path = os.path.join(plugin_dir, '__init__.py')
    if not os.path.isfile(init_path):
        logger.warning(f"跳过 {plugin_id}: 缺少 __init__.py")
        return None

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            f"dbcheck_plugin_{plugin_id.replace('-', '_')}",
            init_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        logger.warning(f"加载插件 {plugin_id} 失败: {e}")
        return None

    return manifest


def load_plugins(plugins_dir: str = None) -> int:
    """加载所有插件，返回成功加载的数量"""
    dirs = find_plugins(plugins_dir)
    count = 0
    for d in dirs:
        if load_plugin(d):
            count += 1
    logger.info(f"插件加载完成: {count}/{len(dirs)} 成功")
    return count


# ───────────────────────────────────────────────────────────────
# 插件执行沙箱
# ───────────────────────────────────────────────────────────────

def run_plugin_inspections_safe(
    plugins: List[InspectionPlugin],
    context: Dict[str, Any],
    timeout: float = 30.0
) -> List[RiskItem]:
    """安全执行所有插件的 analyze()，单个超时/异常不影响其他"""
    all_risks = []
    for p in plugins:
        try:
            start = time.time()
            risks = p.analyze(context)
            elapsed = time.time() - start
            if elapsed > timeout:
                logger.warning(f"插件 [{p.id}] 执行超时 ({elapsed:.1f}s)，结果丢弃")
                continue
            all_risks.extend(risks)
        except Exception as e:
            logger.warning(f"插件 [{p.id}] 执行异常: {e}")
    return all_risks


def get_plugin_manager():
    """获取插件管理器实例（供 Web UI 使用）"""
    return PluginManager()


def run_plugin_inspections_for_db(db_type: str, context: dict, execute_sql=None) -> list:
    """
    为一轮巡检执行所有匹配的插件分析。
    context: 内置检查结果字典
    execute_sql: 可选回调 fn(sql: str) -> {"headers": [...], "rows": [[...]]}
                 如果提供，会先执行插件的 get_queries() 并把结果注入 context
    返回 RiskItem 列表，可直接合并到 issues 中。
    """
    plugins = PluginRegistry.get_inspections_for_db(db_type)
    if not plugins:
        return []

    # 执行插件自带的 SQL 查询
    for p in plugins:
        for q in p.get_queries():
            if q.db_type and q.db_type != db_type:
                continue
            if execute_sql:
                try:
                    result = execute_sql(q.sql)
                    context[q.key] = result
                except Exception as e:
                    logger.warning(f"插件 [{p.id}] SQL 执行失败 ({q.key}): {e}")
                    context[q.key] = {"headers": [], "rows": [], "_error": str(e)}

    all_risks = run_plugin_inspections_safe(plugins, context)
    # RiskItem → dict（兼容现有 issues 格式）
    return [
        {
            'level': r.level,
            'title': r.title,
            'description': r.description,
            'suggestion': r.suggestion,
            'fix_sql': r.fix_sql,
            'category': r.category,
        }
        for r in all_risks
    ]


def init_plugins(plugins_dir: str = None) -> int:
    """初始化插件系统：加载所有插件并返回成功数。应在 app 启动时调用。"""
    return load_plugins(plugins_dir)


class PluginManager:
    """插件管理器 —— Web UI API 的后端"""

    def list_plugins(self) -> List[Dict]:
        return PluginRegistry.list_all()

    def get_plugin(self, plugin_id: str) -> Optional[Dict]:
        return PluginRegistry._all_plugins.get(plugin_id)

    def enable_plugin(self, plugin_id: str) -> bool:
        # 目前所有已加载的插件默认启用
        return plugin_id in PluginRegistry._all_plugins

    def disable_plugin(self, plugin_id: str) -> bool:
        return True  # TODO: 实际禁用逻辑

    def uninstall_plugin(self, plugin_id: str) -> bool:
        PluginRegistry.unregister(plugin_id)
        return True

    def install_from_registry(self, download_url: str) -> bool:
        """从市场安装插件"""
        # TODO: 下载 + 解压 + 加载
        return False
