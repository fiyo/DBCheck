"""
DBCheck 插件类型分类器（双类型支持）

单一事实来源：旧版 plugin_loader 与新版 plugin_core 共用本模块的
PluginType 枚举与 detect_plugin_type()，避免判定逻辑漂移。

类型模型（详见 docs/design/rule-plugin-architecture.md）：
    - inspection : 巡检插件（旧模型，入口 main_file=main_plugin.py，含 db_type）
    - rule       : 规则插件（新模型，入口 entry=__init__.py，register() 注册）

顶层字段约定：plugin.json 的 "type" 显式声明 "inspection" | "rule"；
缺省（旧插件无 type 字段）一律兜底为 inspection，向前兼容。
"""

from enum import Enum
from typing import Dict, Any


class PluginType(Enum):
    """插件加载 / 生命周期模型类型。"""

    INSPECTION = "inspection"  # 巡检插件：模板/基线 + *Inspector / register()
    RULE = "rule"              # 规则插件：仅 register() 进 PluginRegistry，跳过模板/基线

    def __str__(self) -> str:
        return self.value


# 共享常量（两套加载器共用，避免魔法字符串散落）
PLUGIN_TYPE_INSPECTION = PluginType.INSPECTION.value  # "inspection"
PLUGIN_TYPE_RULE = PluginType.RULE.value              # "rule"

# plugin.json 顶层字段名
META_TYPE = "type"
META_ENTRY = "entry"
META_MAIN_FILE = "main_file"
META_DB_TYPE = "db_type"
META_DB_TYPES = "db_types"

# 规则插件缺省入口
DEFAULT_RULE_ENTRY = "__init__.py"
# 巡检插件缺省主文件
DEFAULT_INSPECTION_MAIN_FILE = "main_plugin.py"


def detect_plugin_type(meta: Dict[str, Any]) -> PluginType:
    """
    根据 plugin.json 元数据判定插件类型。

    判定优先级（与 docs/design/rule-plugin-architecture.md 第 2.2 节一致）：
        1. meta["type"] == "rule"    -> RULE
        2. meta["type"] == "inspection" -> INSPECTION
        3. type 缺省（旧插件）：
           a. 有 main_file 或 db_type/db_types 且**无** entry -> INSPECTION
           b. 有 entry（如 __init__.py）且**无** db_type  -> RULE
           c. 兜底 -> INSPECTION（满足“未声明类型默认按巡检插件处理”硬约束）

    Args:
        meta: 解析后的 plugin.json 字典（允许非 dict，将兜底为 inspection）

    Returns:
        PluginType（INSPECTION 或 RULE）
    """
    if not isinstance(meta, dict):
        return PluginType.INSPECTION

    explicit = meta.get(META_TYPE)
    if explicit == PLUGIN_TYPE_RULE:
        return PluginType.RULE
    if explicit == PLUGIN_TYPE_INSPECTION:
        return PluginType.INSPECTION

    # 缺省分支：依据结构特征推断
    has_entry = bool(meta.get(META_ENTRY))
    has_main_or_db = bool(
        meta.get(META_MAIN_FILE)
        or meta.get(META_DB_TYPE)
        or meta.get(META_DB_TYPES)
    )

    if has_main_or_db and not has_entry:
        return PluginType.INSPECTION

    if has_entry and not meta.get(META_DB_TYPE):
        return PluginType.RULE

    # 兜底：未声明类型一律按巡检插件处理（向前兼容 oracle_jdbc 等旧插件）
    return PluginType.INSPECTION


def is_rule_plugin(meta: Dict[str, Any]) -> bool:
    """便捷判断：是否为规则插件。"""
    return detect_plugin_type(meta) == PluginType.RULE
