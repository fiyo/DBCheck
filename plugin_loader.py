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

# 插件目录
PLUGIN_DIR = Path(__file__).parent / "plugins"
ENABLED_DIR = PLUGIN_DIR / "enabled"
AVAILABLE_DIR = PLUGIN_DIR / "available"
REGISTRY_FILE = PLUGIN_DIR / "plugin_registry.json"

# 插件元数据缓存
_plugin_cache: Dict[str, Dict] = {}
_plugin_classes: Dict[str, Type[Any]] = {}


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


def enable_plugin(plugin_name: str) -> bool:
    """
    启用插件（创建符号链接或复制）
    
    Args:
        plugin_name: 插件名称（目录名）
    
    Returns:
        是否成功
    """
    src = AVAILABLE_DIR / plugin_name
    dst = ENABLED_DIR / plugin_name
    
    if not src.exists():
        print(f"[Plugin] 插件不存在: {plugin_name}")
        return False
    
    if dst.exists():
        print(f"[Plugin] 插件已启用: {plugin_name}")
        return True
    
    try:
        # Windows 下创建符号链接需要管理员权限，所以直接复制
        import shutil
        shutil.copytree(src, dst)
        print(f"[Plugin] 已启用插件: {plugin_name}")
        return True
    except Exception as e:
        print(f"[Plugin] 启用插件失败: {plugin_name}, 错误: {e}")
        return False


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
    global _plugin_classes
    _plugin_classes.clear()
    
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

def get_all_plugin_db_types() -> List[str]:
    """
    获取所有插件提供的数据库类型
    
    Returns:
        数据库类型标识列表
    """
    if not _plugin_classes:
        load_enabled_plugins()
    return list(_plugin_classes.keys())

