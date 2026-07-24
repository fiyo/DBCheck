"""
DBCheck 插件加载器
支持数据库类型插件的热插拔，无需修改核心代码即可添加新数据库支持。
"""

import os
import sys
import json
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Type, Any

# 双类型支持：类型分类器（巡检插件 / 规则插件），两套加载器共用同一判定逻辑
from plugin_type import detect_plugin_type, PluginType

# 插件目录
PLUGIN_DIR = Path(__file__).parent / "plugins"
ENABLED_DIR = PLUGIN_DIR / "enabled"
AVAILABLE_DIR = PLUGIN_DIR / "available"
REGISTRY_FILE = PLUGIN_DIR / "plugin_registry.json"

# 插件元数据缓存
_plugin_cache: Dict[str, Dict] = {}
_plugin_classes: Dict[str, Type[Any]] = {}
_plugin_modules: Dict[str, Any] = {}


def _load_registry() -> Dict:
    """加载插件注册表"""
    if REGISTRY_FILE.exists():
        with open(REGISTRY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"plugins": {}, "schema_version": "1.0"}


def _save_registry(registry: Dict) -> None:
    """保存插件注册表"""
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, 'w', encoding='utf-8') as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)


def discover_plugins() -> List[Dict]:
    """
    发现所有可用插件
    
    Returns:
        插件元数据列表，每个元素包含：
        - name: 插件名称
        - db_type: 数据库类型标识（单一类型）
        - db_types: 数据库类型标识列表（多类型支持）
        - version: 版本
        - description: 描述
        - author: 作者
        - enabled: 是否启用
        - path: 插件路径
    """
    plugins = []
    
    # 扫描 available 目录
    if AVAILABLE_DIR.exists():
        for plugin_dir in AVAILABLE_DIR.iterdir():
            if not plugin_dir.is_dir():
                continue
            plugin_json = plugin_dir / "plugin.json"
            if plugin_json.exists():
                try:
                    with open(plugin_json, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    meta['path'] = str(plugin_dir)
                    # 检查是否已启用
                    enabled_path = ENABLED_DIR / plugin_dir.name
                    meta['enabled'] = enabled_path.exists()
                    
                    # 支持多数据库类型插件
                    if 'db_types' in meta:
                        # 多数据库类型插件，为每个 db_type 创建一个条目
                        for db_type in meta['db_types']:
                            plugin_entry = meta.copy()
                            plugin_entry['db_type'] = db_type
                            plugins.append(plugin_entry)
                    else:
                        # 单一数据库类型插件
                        plugins.append(meta)
                except Exception as e:
                    print(f"[Plugin] 读取插件元数据失败: {plugin_dir.name}, 错误: {e}")
    
    return plugins


def enable_plugin(plugin_name: str, auto_init: bool = True) -> bool:
    """
    启用插件（创建符号链接或复制），并自动初始化模板和基线数据
    
    Args:
        plugin_name: 插件名称（目录名）
        auto_init: 是否自动初始化模板和基线数据（默认 True）
    
    Returns:
        是否成功
    """
    src = AVAILABLE_DIR / plugin_name
    dst = ENABLED_DIR / plugin_name

    # 读取清单判定插件类型：规则插件跳过模板/基线初始化（用户硬约束）
    ptype = PluginType.INSPECTION
    src_json = src / "plugin.json"
    if src_json.exists():
        try:
            with open(src_json, 'r', encoding='utf-8') as f:
                ptype = detect_plugin_type(json.load(f))
        except Exception:
            pass
    _is_rule = (ptype == PluginType.RULE)

    if not src.exists():
        print(f"[Plugin] 插件不存在: {plugin_name}")
        return False

    if dst.exists():
        print(f"[Plugin] 插件已启用: {plugin_name}")
        if auto_init and not _is_rule:
            # 即使已启用，也尝试初始化（幂等）；规则插件跳过模板/基线
            _init_plugin_data(dst, auto_init=auto_init)
        return True, f"插件已启用: {plugin_name}"

    try:
        # Windows 下创建符号链接需要管理员权限，所以直接复制
        import shutil
        shutil.copytree(src, dst)
        print(f"[Plugin] 已启用插件: {plugin_name}")

        # 自动初始化模板和基线数据（规则插件跳过）
        if auto_init and not _is_rule:
            _init_plugin_data(dst, auto_init=auto_init)

        return True, f"插件启用成功: {plugin_name}"
    except Exception as e:
        print(f"[Plugin] 启用插件失败: {plugin_name}, 错误: {e}")
        return False, f"启用插件失败: {e}"


def _init_plugin_data(plugin_dir: Path, auto_init: bool = True) -> None:
    """
    初始化插件数据（模板和基线）
    在 enable_plugin() 中自动调用
    
    Args:
        plugin_dir: 插件目录（已启用的）
        auto_init: 是否自动初始化
    """
    if not auto_init:
        return
    
    print(f"[Plugin] 开始初始化插件数据: {plugin_dir.name}")
    
    # 1. 初始化巡检模板
    _init_plugin_templates(plugin_dir)
    
    # 2. 初始化基线配置
    _init_plugin_baselines(plugin_dir)
    
    print(f"[Plugin] 插件数据初始化完成: {plugin_dir.name}")


def _init_plugin_templates(plugin_dir: Path) -> None:
    """
    从 sql_templates.json 初始化巡检模板
    
    Args:
        plugin_dir: 插件目录
    """
    import sys
    import json
    
    # 添加项目根目录到路径
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    sql_templates_path = plugin_dir / "sql_templates.json"
    if not sql_templates_path.exists():
        print(f"[Plugin] 插件无 sql_templates.json，跳过模板初始化")
        return
    
    try:
        from inspection_dal import (
            get_db_connection,
            create_template,
            create_chapter,
            create_query,
            get_templates_by_db_type,
        )
        
        with open(sql_templates_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 获取 db_type
        plugin_json_path = plugin_dir / "plugin.json"
        if plugin_json_path.exists():
            with open(plugin_json_path, 'r', encoding='utf-8') as f:
                plugin_meta = json.load(f)
                db_type = plugin_meta.get('db_type', plugin_dir.name)
        else:
            db_type = plugin_dir.name
        
        # 检查是否已有模板（幂等）
        existing = get_templates_by_db_type(db_type)
        if existing:
            print(f"[Plugin] 模板已存在（ID: {existing[0]['id']}），跳过")
            return
        
        # 创建模板（is_default=1：供 get_default_template 命中，报告才能渲染章节）
        template_name_zh = f"{db_type.upper()} 默认巡检模板"
        template_name_en = f"{db_type.upper()} Default Inspection Template"
        template_id = create_template(db_type, template_name_zh, template_name_en, is_default=1)
        print(f"[Plugin] 已创建模板（ID: {template_id}）")
        
        # 创建章节和查询
        chapters = config.get('chapters', [])
        for ch_data in chapters:
            chapter_number = ch_data.get('chapter_number', 1)
            chapter_title_zh = ch_data.get('chapter_title_zh', '未命名章节')
            chapter_title_en = ch_data.get('chapter_title_en', '')
            
            chapter_id = create_chapter(
                template_id, chapter_number,
                chapter_title_zh, chapter_title_en
            )
            print(f"[Plugin]   已创建章节：{chapter_title_zh} (ID: {chapter_id})")
            
            # 创建查询
            queries = ch_data.get('queries', [])
            for q_data in queries:
                query_key = q_data.get('key', '')
                query_sql = q_data.get('command', q_data.get('query_sql', ''))
                query_description_zh = q_data.get('desc_zh', '')
                query_description_en = q_data.get('desc_en', '')
                sort_order = q_data.get('sort_order', 1)
                
                # 检查是否已存在相同的 query_key（幂等性）
                import sqlite3
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id FROM inspection_query 
                    WHERE chapter_id = ? AND query_key = ?
                """, (chapter_id, query_key))
                existing_query = cursor.fetchone()
                
                if existing_query:
                    print(f"[Plugin]     跳过已存在的查询：{query_key}")
                    continue
                
                query_id = create_query(
                    chapter_id, query_key, query_sql,
                    query_description_zh, query_description_en,
                    sort_order
                )
                print(f"[Plugin]     已创建查询：{query_key} (ID: {query_id})")
        
        print(f"[Plugin] 模板初始化完成：{template_name_zh}")
        
    except Exception as e:
        print(f"[Plugin] 模板初始化失败: {e}")


def _init_plugin_baselines(plugin_dir: Path) -> None:
    """
    从 baselines.json 初始化基线配置
    
    Args:
        plugin_dir: 插件目录
    """
    import sys
    import json
    
    # 添加项目根目录到路径
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    baselines_path = plugin_dir / "baselines.json"
    if not baselines_path.exists():
        print(f"[Plugin] 插件无 baselines.json，跳过基线初始化")
        return
    
    try:
        from inspection_dal import (
            get_db_connection,
            create_baseline,
            get_baselines_by_db_type,
        )
        
        with open(baselines_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        db_type = config.get('db_type', plugin_dir.name)
        baselines = config.get('baselines', [])
        
        if not baselines:
            print(f"[Plugin] baselines.json 中无基线数据，跳过")
            return
        
        # 检查是否已有基线（幂等）
        existing = get_baselines_by_db_type(db_type)
        if existing:
            print(f"[Plugin] 基线已存在（{len(existing)} 条），跳过")
            return
        
        # 创建基线
        conn = get_db_connection()
        cursor = conn.cursor()
        
        created_count = 0
        for bl_data in baselines:
            param_name = bl_data.get('param_name', '')
            operator = bl_data.get('operator', '==')
            expected_value = str(bl_data.get('expected_value', ''))
            risk_level = bl_data.get('risk_level', 'LOW')
            description_zh = bl_data.get('description_zh', '')
            description_en = bl_data.get('description_en', '')
            
            # 检查是否已存在
            cursor.execute(
                "SELECT id FROM inspection_baseline WHERE db_type = ? AND param_name = ?",
                (db_type, param_name)
            )
            if cursor.fetchone():
                continue
            
            # 创建基线
            cursor.execute(
                """INSERT INTO inspection_baseline 
                   (db_type, param_name, operator, expected_value, 
                    risk_level, description_zh, description_en)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (db_type, param_name, operator, expected_value,
                 risk_level, description_zh, description_en)
            )
            created_count += 1
        
        conn.commit()
        conn.close()
        
        print(f"[Plugin] 基线初始化完成：{db_type}，新增 {created_count} 条")
        
    except Exception as e:
        print(f"[Plugin] 基线初始化失败: {e}")


def disable_plugin(plugin_name: str) -> bool:
    """
    禁用插件
    
    Args:
        plugin_name: 插件名称
    
    Returns:
        是否成功
    """
    dst = ENABLED_DIR / plugin_name

    if not dst.exists():
        print(f"[Plugin] 插件未启用: {plugin_name}")
        return True

    try:
        # 类型分叉：规则插件需从 PluginRegistry 注销（无模板/基线可清理，仅 rmtree）
        ptype = PluginType.INSPECTION
        dst_json = dst / "plugin.json"
        if dst_json.exists():
            try:
                with open(dst_json, 'r', encoding='utf-8') as f:
                    ptype = detect_plugin_type(json.load(f))
            except Exception:
                pass
        if ptype == PluginType.RULE:
            try:
                from plugin_core import unload_plugin as _core_unload_plugin
                _core_unload_plugin(str(dst))
            except Exception as e:
                print(f"[Plugin] 规则插件注销失败: {plugin_name}, 错误: {e}")

        import shutil
        shutil.rmtree(dst)
        print(f"[Plugin] 已禁用插件: {plugin_name}")
        return True
    except Exception as e:
        print(f"[Plugin] 禁用插件失败: {plugin_name}, 错误: {e}")
        return False


def load_enabled_plugins() -> Dict[str, Type[Any]]:
    """
    加载所有已启用的插件
    
    Returns:
        字典：db_type -> 巡检器类
    """
    global _plugin_classes, _plugin_modules
    _plugin_classes.clear()
    _plugin_modules.clear()
    
    if not ENABLED_DIR.exists():
        return _plugin_classes
    
    for plugin_dir in ENABLED_DIR.iterdir():
        if not plugin_dir.is_dir():
            continue
        
        plugin_json = plugin_dir / "plugin.json"
        if not plugin_json.exists():
            continue
        
        try:
            with open(plugin_json, 'r', encoding='utf-8') as f:
                meta = json.load(f)

            # 类型分叉：规则插件不再要求 main_plugin.py，委托 plugin_core 加载并注册
            if detect_plugin_type(meta) == PluginType.RULE:
                try:
                    from plugin_core import load_plugin as _core_load_plugin
                    _core_load_plugin(str(plugin_dir))
                    print(f"[Plugin] 已加载规则插件: {meta.get('name', plugin_dir.name)}")
                except Exception as e:
                    print(f"[Plugin] 规则插件加载失败: {plugin_dir.name}, 错误: {e}")
                # 规则插件不经 _plugin_classes（按 entry 注册进 PluginRegistry），直接跳过
                continue

            # ── 以下为巡检插件原有逻辑（完全不变）──
            # 支持多数据库类型
            db_types = meta.get('db_types', [meta.get('db_type')])
            main_file = meta.get('main_file', 'main_plugin.py')
            
            # 动态导入插件主文件
            main_path = plugin_dir / main_file
            if not main_path.exists():
                print(f"[Plugin] 插件主文件不存在: {main_path}")
                continue
            
            # 导入模块
            spec = importlib.util.spec_from_file_location(
                f"plugin_{plugin_dir.name}", main_path
            )
            if spec is None:
                print(f"[Plugin] 无法加载插件: {plugin_dir.name}")
                continue
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 查找巡检器类（类名以 Inspector 结尾）
            inspector_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and attr_name.endswith('Inspector'):
                    inspector_class = attr
                    break
            
            if inspector_class:
                # 为每个 db_type 注册巡检器类
                for db_type in db_types:
                    if db_type:
                        _plugin_classes[db_type] = inspector_class
                        _plugin_modules[db_type] = module
                        print(f"[Plugin] 已加载插件: {meta.get('name', plugin_dir.name)} ({db_type})")
            else:
                print(f"[Plugin] 插件中未找到 Inspector 类: {plugin_dir.name}")
                
        except Exception as e:
            print(f"[Plugin] 加载插件失败: {plugin_dir.name}, 错误: {e}")
    
    return _plugin_classes


def get_plugin_inspector(db_type: str) -> Optional[Type[Any]]:
    """
    获取指定数据库类型的巡检器类
    
    Args:
        db_type: 数据库类型标识
    
    Returns:
        巡检器类，未找到返回 None
    """
    if not _plugin_classes:
        load_enabled_plugins()
    return _plugin_classes.get(db_type)


def get_plugin_task_config(db_type: str) -> Optional[Dict]:
    """
    获取指定数据库类型的插件任务配置
    用于 web_ui.py 动态构建 task_configs
    
    Args:
        db_type: 数据库类型标识
    
    Returns:
        任务配置字典，结构与 web_ui.py 中的 task_configs 一致
        未找到返回 None
    """
    if not ENABLED_DIR.exists():
        return None
    
    # 查找提供该 db_type 的插件
    for plugin_dir in ENABLED_DIR.iterdir():
        if not plugin_dir.is_dir():
            continue
        
        plugin_json = plugin_dir / "plugin.json"
        if not plugin_json.exists():
            continue
        
        try:
            with open(plugin_json, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            
            if meta.get('db_type') != db_type:
                continue
            
            # 动态导入插件主文件
            main_file = meta.get('main_file', 'main_plugin.py')
            main_path = plugin_dir / main_file
            
            if not main_path.exists():
                continue
            
            # 导入模块
            spec = importlib.util.spec_from_file_location(
                f"plugin_{db_type}", main_path
            )
            if spec is None:
                continue
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 查找 get_task_config 函数
            if hasattr(module, 'get_task_config'):
                config = module.get_task_config()
                # 添加插件路径信息（供 web_ui.py 动态导入模块）
                if not config.get('plugin_path'):
                    config['plugin_path'] = str(plugin_dir)
                if not config.get('main_file'):
                    config['main_file'] = main_file
                return config
            
            # 如果没有 get_task_config 函数，尝试自动构建配置
            # 查找 Inspector 类
            inspector_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and attr_name.endswith('Inspector'):
                    inspector_class = attr
                    break
            
            if inspector_class:
                # 自动构建基础配置
                config = {
                    'module_name': str(main_path),
                    'inspector_class': inspector_class,
                    'plugin_path': str(plugin_dir),
                    'conn_attr': 'conn_db2',
                    'filename_key': f'webui.{db_type}_report_filename',
                    'history_db_type': db_type,
                    'instance_prefix': db_type,
                    'error_task_name': meta.get('name', db_type),
                    'log_start_key': f'webui.log_{db_type}_start',
                    'err_module_key': f'webui.err_{db_type}_module',
                    'label_default': 'unknown',
                    'db_name_default': 'default',
                }
                
                # 如果插件提供了 test_connection 函数，使用它
                if hasattr(module, 'test_connection'):
                    config['connect_test'] = module.test_connection
                    config['connect_test_args'] = lambda info: [info]
                
                return config
            
        except Exception as e:
            print(f"[Plugin] 加载插件配置失败: {db_type}, 错误: {e}")
            continue
    
    return None


def get_all_plugin_task_configs() -> Dict[str, Dict]:
    """
    获取所有已启用插件任务配置
    
    Returns:
        字典：db_type -> 任务配置
    """
    configs = {}
    if not ENABLED_DIR.exists():
        return configs
    
    for plugin_dir in ENABLED_DIR.iterdir():
        if not plugin_dir.is_dir():
            continue
        
        plugin_json = plugin_dir / "plugin.json"
        if not plugin_json.exists():
            continue
        
        try:
            with open(plugin_json, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            
            db_type = meta.get('db_type')
            if db_type:
                config = get_plugin_task_config(db_type)
                if config:
                    configs[db_type] = config
        except Exception:
            continue
    
    return configs


def get_plugin_instance(db_type: str) -> Optional[Any]:
    """
    获取指定数据库类型的插件实例（从 PluginRegistry）
    用于 web_ui.py 动态调用插件方法（无侵入式架构）
    
    Args:
        db_type: 数据库类型标识（如 'oracle_jdbc'）
    
    Returns:
        插件实例（InspectionPlugin 对象），未找到返回 None
    """
    try:
        from plugin_core import PluginRegistry

        # 从注册表中获取插件（优先按注册 id 精确匹配，如 'oracle_jdbc' / 'db2_jdbc'）
        plugin_info = PluginRegistry._all_plugins.get(db_type)
        if plugin_info:
            plugin = PluginRegistry._inspections.get(db_type)
            if not plugin:
                plugin = PluginRegistry._notifiers.get(db_type)
            return plugin

        # 兜底：按 db_types 令牌匹配。
        # 形如 db2 插件 id='db2_jdbc' 但巡检分派令牌仍为 'db2'，
        # 外部可能以令牌 'db2' 调用本函数，此时直接按 id 命中不到，需回退到 db_types 检索。
        for pid, info in PluginRegistry._all_plugins.items():
            if db_type in (info.get('db_types') or []):
                plugin = PluginRegistry._inspections.get(pid)
                if not plugin:
                    plugin = PluginRegistry._notifiers.get(pid)
                return plugin

        return None
    except Exception as e:
        print(f"[Plugin] 获取插件实例失败: {e}")
        return None


def install_plugin(plugin_package_path: str) -> bool:
    """
    安装插件（从 zip 或目录）
    
    Args:
        plugin_package_path: 插件包路径（.zip）或目录路径
    
    Returns:
        是否成功
    """
    import shutil
    import zipfile
    
    path = Path(plugin_package_path)
    
    if path.suffix == '.zip':
        # 解压 zip 包
        try:
            with zipfile.ZipFile(path, 'r') as zip_ref:
                # 获取顶层目录名
                top_dir = zip_ref.namelist()[0].split('/')[0]
                extract_to = AVAILABLE_DIR / top_dir
                zip_ref.extractall(AVAILABLE_DIR)
            print(f"[Plugin] 已解压插件到: {extract_to}")
            return True
        except Exception as e:
            print(f"[Plugin] 解压插件失败: {e}")
            return False
    elif path.is_dir():
        # 复制目录
        try:
            dst = AVAILABLE_DIR / path.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(path, dst)
            print(f"[Plugin] 已安装插件到: {dst}")
            return True
        except Exception as e:
            print(f"[Plugin] 安装插件失败: {e}")
            return False
    else:
        print(f"[Plugin] 不支持的插件包格式: {path}")
        return False


def create_sample_plugin(plugin_name: str, db_type: str, output_dir: str = None) -> bool:
    """
    创建示例插件（用于开发参考）
    
    Args:
        plugin_name: 插件名称
        db_type: 数据库类型标识
        output_dir: 输出目录（默认 plugins/available/）
    
    Returns:
        是否成功
    """
    if output_dir is None:
        output_dir = AVAILABLE_DIR
    else:
        output_dir = Path(output_dir)
    
    plugin_dir = output_dir / plugin_name
    if plugin_dir.exists():
        print(f"[Plugin] 插件目录已存在: {plugin_dir}")
        return False
    
    plugin_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建 plugin.json
    meta = {
        "name": plugin_name,
        "db_type": db_type,
        "version": "1.0.0",
        "description": f"{plugin_name} 数据库巡检插件",
        "author": "Your Name",
        "main_file": "main_plugin.py",
        "requirements": [],
        "sql_templates": "sql_templates.json"
    }
    
    with open(plugin_dir / "plugin.json", 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    
    # 创建 main_plugin.py 模板
    main_template = f'''"""
{{plugin_name}} 数据库巡检插件
继承 BaseInspectionEngine，实现 {{db_type}} 数据库巡检
"""

import sys
from pathlib import Path

# 添加项目根目录到路径，以便导入 BaseInspectionEngine
_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root))

from inspection_engine import BaseInspectionEngine


class {plugin_name.replace('_', ' ').title().replace(' ', '')}Inspector(BaseInspectionEngine):
    """
    {{plugin_name}} 巡检器
    继承 BaseInspectionEngine，只需实现 connect() 和 get_template_id()
    """
    
    def __init__(self, config):
        super().__init__(config)
        self.db_type = "{db_type}"
    
    def connect(self):
        """
        连接 {{plugin_name}} 数据库
        
        Returns:
            (ok: bool, version: str)
        """
        # TODO: 实现数据库连接逻辑
        # 示例：
        # import pymysql  # 根据实际驱动修改
        # try:
        #     self.conn = pymysql.connect(
        #         host=self.config.get('host'),
        #         port=self.config.get('port'),
        #         user=self.config.get('user'),
        #         password=self.config.get('password'),
        #         database=self.config.get('database')
        #     )
        #     # 获取版本信息
        #     cur = self.conn.cursor()
        #     cur.execute("SELECT version()")
        #     version = cur.fetchone()[0]
        #     cur.close()
        #     return True, version
        # except Exception as e:
        #     print(f"数据库连接失败: {{e}}")
        #     return False, str(e)
        
        raise NotImplementedError("请实现 connect() 方法")
    
    def get_template_id(self):
        """
        返回 inspection_template 表的 template_id
        
        Returns:
            template_id: int
        """
        # TODO: 返回对应的模板 ID
        # 可以通过 inspection_dal.py 的接口查询
        # 示例：
        # from inspection_dal import get_template_by_db_type
        # template = get_template_by_db_type("{db_type}")
        # return template['id'] if template else None
        
        raise NotImplementedError("请实现 get_template_id() 方法")
    
    def getData(self, *args, **kwargs):
        """
        兼容旧接口的 getData() 函数
        供 web_ui.py 调用
        """
        return self.run_inspection(*args, **kwargs)


def getData(*args, **kwargs):
    """
    兼容旧接口的全局函数
    供 web_ui.py 动态导入调用
    """
    inspector = {plugin_name.replace('_', ' ').title().replace(' ', '')}Inspector(kwargs.get('config', {{}}))
    return inspector.getData(*args, **kwargs)
'''
    
    with open(plugin_dir / "main_plugin.py", 'w', encoding='utf-8') as f:
        f.write(main_template)
    
    # 创建 sql_templates.json 模板
    sql_templates = {
        "chapters": [
            {
                "chapter_name": "数据库信息",
                "sort_order": 1,
                "queries": [
                    {
                        "query_key": "db_version",
                        "query_sql": "SELECT version()",
                        "sort_order": 1
                    }
                ]
            }
        ]
    }
    
    with open(plugin_dir / "sql_templates.json", 'w', encoding='utf-8') as f:
        json.dump(sql_templates, f, ensure_ascii=False, indent=2)
    
    # 创建 requirements.txt
    with open(plugin_dir / "requirements.txt", 'w', encoding='utf-8') as f:
        f.write("# 在此列出插件依赖的 Python 包\\n")
        f.write("# 例如：\\n")
        f.write("# pymongo\\n")
    
    print(f"[Plugin] 已创建示例插件: {plugin_dir}")
    print(f"[Plugin] 请编辑以下文件完成插件开发：")
    print(f"  - {plugin_dir / 'plugin.json'}")
    print(f"  - {plugin_dir / 'main_plugin.py'}")
    print(f"  - {plugin_dir / 'sql_templates.json'}")
    return True


if __name__ == '__main__':
    # 测试：创建示例插件
    create_sample_plugin("mongodb", "mongodb")
    print("\\n发现插件：")
    plugins = discover_plugins()
    for p in plugins:
        print(f"  - {p['name']} ({p['db_type']}), 启用: {p['enabled']}")

def get_plugin_module(db_type: str):
    """
    获取指定数据库类型插件的主模块（已动态导入）。
    供实时监控等模块调用插件提供的连接工厂（如 get_connection）。

    Args:
        db_type: 数据库类型标识（如 'oracle_jdbc'）

    Returns:
        插件模块对象；未加载或不存在返回 None
    """
    if not _plugin_classes:
        load_enabled_plugins()
    return _plugin_modules.get(db_type)


def get_all_plugin_db_types() -> List[str]:
    """
    获取所有插件提供的数据库类型
    
    Returns:
        数据库类型标识列表
    """
    if not _plugin_classes:
        load_enabled_plugins()
    return list(_plugin_classes.keys())

