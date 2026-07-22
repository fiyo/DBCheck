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

# 类型分类器（双类型支持）：巡检插件 / 规则插件
# plugin_type 仅依赖 enum/typing，无反向依赖，可安全在模块顶层导入。
from plugin_type import PluginType, detect_plugin_type

logger = logging.getLogger('plugin')

# 最近一次 load_plugin() 失败的真实原因（根因）。
# load_plugin() 保持「只返回 manifest（None 表示失败）」的向后兼容契约，
# 需要向用户/开发者展示根因时，请改用 load_plugin_with_error() 或读取本变量。
_last_load_error: Optional[str] = None

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
        """加载时回调（插件启用时调用）"""
        pass

    def on_unload(self):
        """卸载时回调（插件禁用时调用）"""
        pass

    def on_install(self, db_path: str = None):
        """
        安装时回调（插件安装时调用，用于初始化数据）。
        
        参数：
            db_path: 数据库文件路径，如果为 None，则使用默认路径
        """
        pass

    def on_uninstall(self, db_path: str = None):
        """
        卸载时回调（插件卸载时调用，用于清理数据）。
        
        参数：
            db_path: 数据库文件路径，如果为 None，则使用默认路径
        """
        pass

    # ── 可选方法：连接测试相关 ─────────────────────────────

    def parse_connection_result(self, ok: bool, msg: Any) -> Dict[str, Any]:
        """
        解析连接测试结果，返回额外信息（如版本号）。
        插件可以重写此方法以提供特定的解析逻辑。
        
        参数：
            ok: 连接是否成功
            msg: 连接返回的消息（可能是字符串、异常对象等）
        
        返回：
            字典，包含额外信息（如 {'oracle_major_version': 19}）
        """
        return {}

    def get_version_from_connection(self, connection: Any) -> Optional[str]:
        """
        从数据库连接中提取版本号。
        插件可以重写此方法以提供特定的版本提取逻辑。
        
        参数：
            connection: 数据库连接对象（可能是 JDBC、cx_Oracle 等）
        
        返回：
            版本字符串（如 "19.3.0.0.0"），如果无法提取则返回 None
        """
        return None

    def get_connection_test_extra_config(self) -> Dict[str, Any]:
        """
        返回连接测试的额外配置。
        插件可以重写此方法以提供特定的配置（如特殊的连接参数）。
        
        返回：
            字典，包含额外配置（如 {'need_sysdba': True}）
        """
        return {}


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
    def get_plugin_instance(cls, plugin_id: str) -> Optional[InspectionPlugin]:
        """
        获取插件实例（用于调用插件方法）
        返回 InspectionPlugin 对象，如果未找到则返回 None
        """
        return cls._inspections.get(plugin_id)

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
    """
    扫描插件目录，返回找到的 plugin.json 路径列表。
    扫描 plugins/ 直目录、plugins/available/ 和 plugins/enabled/ 两个子目录。
    """
    if plugins_dir is None:
        plugins_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plugins')
    if not os.path.isdir(plugins_dir):
        return []
    results = []

    # 1. 扫描 plugins/ 直目录（向后兼容）
    for entry in os.listdir(plugins_dir):
        plugin_dir = os.path.join(plugins_dir, entry)
        if not os.path.isdir(plugin_dir):
            continue
        manifest_path = os.path.join(plugin_dir, 'plugin.json')
        if os.path.isfile(manifest_path):
            results.append(plugin_dir)

    # 2. 扫描 plugins/available/ 和 plugins/enabled/ 子目录
    #    同一 basename 若同时存在于 available/ 与 enabled/，
    #    仅保留 enabled/ 下的路径（enabled/ 为运行副本，是活动源），
    #    避免重复加载同一插件导致注册表中出现重复记录（见 DB2 重复 Bug）。
    found = {}  # basename -> 优先的 plugin_dir
    for subdir in ('available', 'enabled'):
        sub_path = os.path.join(plugins_dir, subdir)
        if not os.path.isdir(sub_path):
            continue
        for entry in os.listdir(sub_path):
            plugin_dir = os.path.join(sub_path, entry)
            if not os.path.isdir(plugin_dir):
                continue
            manifest_path = os.path.join(plugin_dir, 'plugin.json')
            if os.path.isfile(manifest_path):
                # enabled/ 在后遍历，若同 basename 同时存在则覆盖为 enabled/ 路径
                found[entry] = plugin_dir
    results.extend(found.values())

    return results


def load_plugin(plugin_dir: str) -> Optional[Dict]:
    """加载单个插件目录，返回 manifest 或 None。

    失败时返回 None，并通过模块级变量 _last_load_error 记录真实异常原因，
    调用方可用 load_plugin_with_error() 一并取回。
    """
    global _last_load_error
    _last_load_error = None

    manifest_path = os.path.join(plugin_dir, 'plugin.json')
    if not os.path.isfile(manifest_path):
        logger.warning(f"跳过 {plugin_dir}: 缺少 plugin.json")
        return None

    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except Exception as e:
        _last_load_error = f"解析 plugin.json 失败: {type(e).__name__}: {e}"
        logger.warning(f"解析 plugin.json 失败: {e}")
        return None

    # 版本检查
    min_ver = manifest.get('dbcheck', {}).get('minVersion', '0')
    # TODO: 实际版本比较

    # 安装 Python 依赖（如果有）
    req_path = os.path.join(plugin_dir, 'requirements.txt')

    # plugin_id 采用目录 basename（稳定、无中文括号/空格），
    # 用于模块命名与 fallback 注册键，避免显示名不稳定导致的幽灵记录。
    plugin_id = os.path.basename(plugin_dir)
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
    # 支持两种插件结构：
    # 1. 标准 Python 包：有 __init__.py
    # 2. 简单模式：有 main_plugin.py（无 __init__.py）
    init_path = os.path.join(plugin_dir, '__init__.py')
    main_plugin_path = os.path.join(plugin_dir, 'main_plugin.py')
    
    entry_file = None
    if os.path.isfile(init_path):
        entry_file = init_path
    elif os.path.isfile(main_plugin_path):
        entry_file = main_plugin_path
    else:
        _last_load_error = f"插件 {plugin_id} 缺少入口文件（__init__.py / main_plugin.py）"
        logger.warning(f"跳过 {plugin_id}: 缺少 __init__.py 或 main_plugin.py")
        return None

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            f"dbcheck_plugin_{plugin_id.replace('-', '_')}",
            entry_file
        )
        module = importlib.util.module_from_spec(spec)
        # 将插件目录临时加入 sys.path，使其顶层 import 能正确解析同目录下的模块
        # （例如 Oracle JDBC 插件自带的 inspection_engine.py），
        # 不再依赖「项目根目录是否已被加入 sys.path」——之前正是因此导致安装时加载失败并回滚。
        # 执行完毕后立即移除，避免污染全局 sys.path。
        sys.path.insert(0, plugin_dir)
        # 快照导入前的注册表键集合，用于判定「本次 import 是否触发了 register()」
        # （插件的注册 id 可能 ≠ manifest 的 name，如 oracle_jdbc 的 id='oracle_jdbc'、
        # 而 plugin.json name='Oracle (JDBC)'，因此不能以 plugin_id 是否存在来判定）。
        registered_before = set(PluginRegistry._all_plugins.keys())
        try:
            spec.loader.exec_module(module)
        finally:
            if plugin_dir in sys.path:
                sys.path.remove(plugin_dir)
    except Exception as e:
        _last_load_error = f"插件 {plugin_id} 加载失败: {type(e).__name__}: {e}"
        logger.warning(f"加载插件 {plugin_id} 失败: {e}")
        return None

    # 同步插件类型到注册表：register() 在模块导入时写入 _all_plugins 的默认值
    # 为 "inspection"，此处按 detect_plugin_type(manifest) 覆盖/补充正确的 type 字段，
    # 使 api_v1 列表读取 manifest.get('type') 时与 plugin.json 声明一致。
    try:
        ptype_str = detect_plugin_type(manifest).value
        # 计算本次 import 新注册的 id（真实注册键，可能 ≠ manifest name），统一同步 type，
        # 避免 id≠name 的插件产生键为 manifest name 的幽灵记录。
        new_ids = set(PluginRegistry._all_plugins.keys()) - registered_before
        if new_ids:
            for rid in new_ids:
                PluginRegistry._all_plugins[rid]['type'] = ptype_str
        else:
            # 模块未主动 register()（极少数情况）：按 manifest name 补充记录，保证列表可见
            db_types = manifest.get('db_types')
            if not db_types and manifest.get('db_type'):
                db_types = [manifest['db_type']]
            PluginRegistry._all_plugins[plugin_id] = {
                'id': plugin_id,
                'name': manifest.get('name', plugin_id),
                'version': manifest.get('version', '0.1.0'),
                'type': ptype_str,
                'db_types': db_types or [],
                'author': manifest.get('author', ''),
                'description': manifest.get('description', ''),
            }
    except Exception as e:
        logger.warning(f"插件 {plugin_id} 类型同步失败: {e}")

    return manifest


def load_plugin_with_error(plugin_dir: str) -> tuple:
    """
    加载插件并返回 (manifest, error)，向后兼容 load_plugin()。

    返回：
        manifest: 成功时为插件清单 dict；失败时为 None
        error:    成功时为 None；失败时为真实异常信息字符串
                  （例如 "ModuleNotFoundError: No module named 'inspection_engine'"）

    说明：load_plugin() 仍只返回 manifest（None 表示失败），现有
        `if load_plugin(x):` 用法不受影响；本函数额外把失败根因带出来，
        供调用方（如插件市场）向用户/开发者展示真实原因。
    """
    global _last_load_error
    _last_load_error = None
    manifest = load_plugin(plugin_dir)
    return manifest, _last_load_error


def load_plugins(plugins_dir: str = None) -> int:
    """加载所有插件，返回成功加载的数量"""
    dirs = find_plugins(plugins_dir)
    count = 0
    for d in dirs:
        if load_plugin(d):
            count += 1
    logger.info(f"插件加载完成: {count}/{len(dirs)} 成功")
    return count


def unload_plugin(plugin_dir: str) -> bool:
    """
    卸载单个插件（按 plugin.json 的 name 作为 plugin_id）：
        1. 从 PluginRegistry 注销（_inspections / _notifiers / _all_plugins）
        2. （预留）规则引擎移除钩子：本期无 YAML 规则源接入（T4 推迟），
           若有 rules/*.yaml 未来由 pro/rule_engine 的 unload_plugin_rules 处理，
           此处保持最小改动，不引入破坏性逻辑。

    供卸载路径（api_v1 DELETE /plugins/<id>、plugin_market.uninstall、plugin_loader.disable_plugin）
    复用，统一注销出口。

    Args:
        plugin_dir: 已启用插件目录的绝对路径

    Returns:
        是否成功（缺少 plugin.json 视为失败）
    """
    manifest_path = os.path.join(plugin_dir, 'plugin.json')
    if not os.path.isfile(manifest_path):
        logger.warning(f"unload_plugin 跳过: 缺少 plugin.json ({plugin_dir})")
        return False

    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except Exception as e:
        logger.warning(f"unload_plugin 解析 plugin.json 失败: {e}")
        return False

    plugin_id = manifest.get('name', os.path.basename(plugin_dir))
    PluginRegistry.unregister(plugin_id)

    # 规则移除占位：本期规则插件仅经 register() 入 PluginRegistry（路径 a），
    # 无 YAML 规则源（路径 b 推迟），故无需额外清理 RuleEngine。
    logger.info(f"插件卸载: 已注销 {plugin_id}")
    return True


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
