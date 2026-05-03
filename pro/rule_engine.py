# -*- coding: utf-8 -*-
"""
DBCheck Pro 插件规则引擎
支持 YAML 规则描述，用户无需修改 Python 代码即可添加/禁用检查规则。

安全策略：
- 不使用 exec()，使用 eval() 并在白名单沙箱中执行
- 仅允许访问 params 和 context 中的变量
- ast 预检查：拒绝import、getattr、setattr 等危险操作
"""

import os
import ast
import re
import yaml
from typing import Dict, List, Any, Optional, Tuple


# ── 危险操作黑名单（ast 预检查）──────────────────────
_DANGER_AST_NODES = (
    ast.Import, ast.ImportFrom,
    ast.Call,   # 禁止函数调用（如 __import__()，getattr() 等）
)
# 实际上我们需要允许一些安全函数调用（min, max, len 等）
# 所以不对 Call 节点做全局拒绝，而是在 _eval_expr 中做白名单


def _safe_eval(expr: str, context: Dict, params: Dict) -> Tuple[bool, str]:
    """
    在安全沙箱中执行表达式。
    仅允许：比较运算、算术运算、逻辑运算、取值操作。
    禁止：import、函数调用（除白名单外）、属性访问链。

    Returns:
        (result: bool, error_msg: str)
    """
    # 构建白名单 locals
    locals_dict = {}
    locals_dict.update(params)

    # 允许的安全函数
    safe_builtins = {
        'min': min, 'max': max, 'len': len,
        'int': int, 'float': float, 'str': str,
        'abs': abs, 'round': round,
        'any': any, 'all': all,
        'sum': sum,
    }

    try:
        # ast 预检查
        tree = ast.parse(expr, mode='eval')
        for node in ast.walk(tree):
            # 拒绝 import
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return False, "不允许 import 操作"
            # 拒绝属性访问（防止 __class__.__bases__ 等逃逸）
            if isinstance(node, ast.Attribute):
                return False, "不允许属性访问"
            # 拒绝调用（防止 __import__() 等）
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in safe_builtins:
                    continue  # 白名单函数允许
                return False, f"不允许函数调用: {ast.dump(func)}"

        result = eval(tree, {'__builtins__': safe_builtins}, locals_dict)
        return bool(result), ""
    except Exception as e:
        return False, str(e)


def _resolve_param(param_expr: str, context: Dict) -> Any:
    """
    解析参数表达式，支持：
    - 字面量：90, "hello", 1.5
    - context 取值："${context.key.subkey}"
    """
    if not isinstance(param_expr, str):
        return param_expr

    # 匹配 ${context.xxx.yyy} 格式
    m = re.match(r'^\$\{(.+)\}$', param_expr.strip())
    if m:
        expr = m.group(1)
        # 支持 context.key 或 context.key[0].subkey 格式
        parts = expr.split('.')
        if parts[0] == 'context':
            obj = context
            for part in parts[1:]:
                if obj is None:
                    return None
                if isinstance(obj, dict):
                    obj = obj.get(part)
                elif isinstance(obj, list) and part.isdigit():
                    idx = int(part)
                    obj = obj[idx] if idx < len(obj) else None
                else:
                    obj = getattr(obj, part, None)
            return obj
        return None

    # 尝试解析为字面量
    try:
        return int(param_expr)
    except (ValueError, TypeError):
        pass
    try:
        return float(param_expr)
    except (ValueError, TypeError):
        pass
    return param_expr


def _format_message(template: str, context: Dict, params: Dict, extra: Dict) -> str:
    """格式化消息模板，支持 {key} 变量替换"""
    fmt_dict = {}
    fmt_dict.update(params)
    fmt_dict.update(extra)
    fmt_dict['context'] = context

    # 先解析所有值为实际值
    resolved = {}
    for k, v in fmt_dict.items():
        resolved[k] = _resolve_param(v, context) if isinstance(v, str) else v

    try:
        return template.format(**resolved)
    except Exception:
        return template


class RuleEngine:
    """
    插件规则引擎。
    加载 YAML 规则文件，对巡检 context 执行规则检查。
    """

    def __init__(self, rules_dir: str = None):
        if rules_dir is None:
            # 默认相对于本文件
            self.rules_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rules')
        else:
            self.rules_dir = rules_dir

        self.builtin_rules: List[Dict] = []
        self.custom_rules: List[Dict] = []
        self.overrides: Dict = {}

        self._load_all()

    def _load_all(self):
        """加载所有规则"""
        builtin_dir = os.path.join(self.rules_dir, 'builtin')
        custom_dir = os.path.join(self.rules_dir, 'custom')
        override_file = os.path.join(self.rules_dir, 'overrides.yaml')

        self.builtin_rules = self._load_dir(builtin_dir)
        self.custom_rules = self._load_dir(custom_dir)
        self.overrides = self._load_overrides(override_file)

    @staticmethod
    def _load_dir(directory: str) -> List[Dict]:
        """加载目录下所有 YAML 规则文件"""
        rules = []
        if not os.path.isdir(directory):
            return rules
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith(('.yaml', '.yml')):
                continue
            fpath = os.path.join(directory, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                if data and 'rules' in data:
                    for rule in data['rules']:
                        rule['_source'] = fpath
                        rules.append(rule)
            except Exception as e:
                print(f"[RuleEngine] 加载规则文件失败 {fname}: {e}")
        return rules

    @staticmethod
    def _load_overrides(filepath: str) -> Dict:
        """加载规则覆盖配置（禁用哪些规则）"""
        if not os.path.isfile(filepath):
            return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    def get_enabled_rules(self, db_type: str = None) -> List[Dict]:
        """
        获取启用的规则列表。
        合并内置规则和自定义规则，应用 overrides 配置。
        """
        all_rules = self.builtin_rules + self.custom_rules
        result = []

        disabled_ids = set(self.overrides.get('disabled_ids', []))

        for rule in all_rules:
            # 检查 db_type 过滤
            if db_type and rule.get('db_types'):
                if db_type not in rule['db_types']:
                    continue

            # 检查是否禁用
            rule_id = rule.get('id', '')
            if rule_id in disabled_ids:
                continue

            # 检查 enabled 字段
            if not rule.get('enabled', True):
                continue

            result.append(rule)

        # 按 priority 排序（high > medium > low）
        priority_order = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
        result.sort(key=lambda r: priority_order.get(r.get('priority', 'medium'), 1))

        return result

    def analyze(self, db_type: str, context: Dict) -> List[Dict]:
        """
        对 context 执行所有适用规则，返回 issues 列表。
        issues 格式兼容 analyzer.py 输出。
        """
        rules = self.get_enabled_rules(db_type)
        issues = []

        for rule in rules:
            issue = self._run_rule(rule, db_type, context)
            if issue:
                issues.append(issue)

        return issues

    def _run_rule(self, rule: Dict, db_type: str, context: Dict) -> Optional[Dict]:
        """
        执行单条规则。
        返回 issue dict 或 None（未触发）。
        """
        rule_id = rule.get('id', '')
        condition = rule.get('condition', '')
        params_raw = rule.get('params', {})

        if not condition:
            return None

        # 解析 params（支持 ${context.xxx} 引用）
        resolved_params = {}
        for k, v in params_raw.items():
            resolved_params[k] = _resolve_param(v, context)

        # 计算额外变量（如百分比等）
        extra = {}
        try:
            # 自动计算常用百分比
            for k, v in resolved_params.items():
                if k.endswith('_pct') or k.endswith('_percent'):
                    continue  # 已经是百分比
            # 如果有 a 和 b，自动计算 a_pct = a/b*100
            for k in list(resolved_params.keys()):
                if k.startswith('max_') and 'conn' in k:
                    # 自动计算 conn_pct
                    if 'max_conn' in resolved_params and 'max_used' in resolved_params:
                        mc = resolved_params['max_conn']
                        mu = resolved_params['max_used']
                        if mc and mc > 0:
                            extra['conn_pct'] = mu / mc * 100
        except Exception:
            pass

        # 执行条件表达式
        ok, err = _safe_eval(condition, context, {**resolved_params, **extra})

        if not ok:
            return None

        # 条件触发，生成 issue
        severity = rule.get('severity', 'medium')

        # 中英文消息
        lang = context.get('_lang', 'zh')
        if lang == 'en' and 'message_en' in rule:
            message = rule['message_en']
        else:
            message = rule.get('message_zh', rule.get('message', ''))

        fix_sql = rule.get('fix_sql', '')

        # 格式化消息和 fix_sql
        fmt_vars = {**resolved_params, **extra}
        try:
            message = message.format(**fmt_vars)
        except Exception:
            pass
        try:
            fix_sql = fix_sql.format(**fmt_vars)
        except Exception:
            pass

        return {
            'col1': rule.get('name_zh', rule.get('name', rule_id)),
            'col2': self._severity_label(severity, lang),
            'col3': message,
            'col4': self._priority_label(rule.get('priority', 'medium'), lang),
            'col5': rule.get('owner', 'DBA'),
            'fix_sql': fix_sql,
            '_rule_id': rule_id,   # 内部标记，报告生成时去除
        }

    @staticmethod
    def _severity_label(severity: str, lang: str) -> str:
        labels = {
            'high':   {'zh': '高风险', 'en': 'High Risk'},
            'medium': {'zh': '中风险', 'en': 'Medium Risk'},
            'low':    {'zh': '低风险', 'en': 'Low Risk'},
            'info':   {'zh': '建议',   'en': 'Suggestion'},
        }
        return labels.get(severity, {}).get(lang, severity)

    @staticmethod
    def _priority_label(priority: str, lang: str) -> str:
        labels = {
            'high':   {'zh': '高', 'en': 'High'},
            'medium': {'zh': '中', 'en': 'Medium'},
            'low':    {'zh': '低', 'en': 'Low'},
        }
        return labels.get(priority, {}).get(lang, priority)

    # ── 规则管理 API（供 web_ui.py 调用）────────────────

    def list_rules(self, db_type: str = None) -> List[Dict]:
        """列出所有规则（含启用状态）"""
        all_rules = self.builtin_rules + self.custom_rules
        disabled_ids = set(self.overrides.get('disabled_ids', []))

        result = []
        for rule in all_rules:
            if db_type and rule.get('db_types'):
                if db_type not in rule['db_types']:
                    continue
            rule_copy = rule.copy()
            is_disabled = rule.get('id', '') in disabled_ids
            rule_copy['_disabled'] = is_disabled
            rule_copy['enabled'] = not is_disabled  # 添加 enabled 字段
            result.append(rule_copy)
        return result

    def get_rule(self, rule_id: str) -> Dict | None:
        """按 ID 获取单条规则（含合并后的 enabled 状态）"""
        for rule in self.list_rules():
            if rule.get('id') == rule_id:
                return rule
        return None

    def toggle_rule(self, rule_id: str, enabled: bool) -> bool:
        """启用/禁用规则（写入 overrides.yaml）"""
        overrides = dict(self.overrides)
        disabled = set(overrides.get('disabled_ids', []))

        if enabled:
            disabled.discard(rule_id)
        else:
            disabled.add(rule_id)

        overrides['disabled_ids'] = list(disabled)
        overrides['updated_at'] = __import__('datetime').datetime.now().isoformat()

        override_file = os.path.join(self.rules_dir, 'overrides.yaml')
        os.makedirs(os.path.dirname(override_file), exist_ok=True)
        with open(override_file, 'w', encoding='utf-8') as f:
            yaml.dump(overrides, f, allow_unicode=True, sort_keys=False)

        self.overrides = overrides
        return True

    def save_custom_rule(self, rule: Dict) -> bool:
        """保存自定义规则到 custom/ 目录"""
        rule_id = rule.get('id', '')
        if not rule_id:
            return False

        custom_dir = os.path.join(self.rules_dir, 'custom')
        os.makedirs(custom_dir, exist_ok=True)

        # 读取现有 custom 规则
        all_custom = []
        for fname in sorted(os.listdir(custom_dir)):
            if not fname.endswith(('.yaml', '.yml')):
                continue
            fpath = os.path.join(custom_dir, fname)
            with open(fpath, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if data and 'rules' in data:
                all_custom.extend(data['rules'])

        # 去重（按 id）
        all_custom = [r for r in all_custom if r.get('id') != rule_id]
        all_custom.append(rule)

        # 写入文件（一个规则一个文件，或合并到一个文件）
        out_file = os.path.join(custom_dir, f"{rule_id}.yaml")
        with open(out_file, 'w', encoding='utf-8') as f:
            yaml.dump({'rules': [rule]}, f, allow_unicode=True, sort_keys=False)

        self._load_all()
        return True

    def delete_custom_rule(self, rule_id: str) -> bool:
        """删除自定义规则"""
        custom_dir = os.path.join(self.rules_dir, 'custom')
        target = os.path.join(custom_dir, f"{rule_id}.yaml")
        if os.path.isfile(target):
            os.remove(target)
            self._load_all()
            return True
        return False


# ── 便捷函数（供 analyzer.py 调用）─────────────────────────

_cache: Optional[RuleEngine] = None

def get_rule_engine() -> RuleEngine:
    """获取全局 RuleEngine 实例（单例）"""
    global _cache
    if _cache is None:
        _cache = RuleEngine()
    return _cache


def analyze_with_plugins(db_type: str, context: Dict) -> List[Dict]:
    """
    供 analyzer.py 调用的便捷函数。
    执行所有插件规则，返回 issues 列表。
    """
    try:
        engine = get_rule_engine()
        return engine.analyze(db_type, context)
    except Exception as e:
        print(f"[RuleEngine] 插件规则执行失败: {e}")
        return []


if __name__ == '__main__':
    # 测试
    engine = RuleEngine()
    print(f"已加载 {len(engine.builtin_rules)} 条内置规则，{len(engine.custom_rules)} 条自定义规则")

    # 测试规则执行
    test_context = {
        'max_connections': [{'Value': '200'}],
        'max_used_connections': [{'Value': '180'}],
    }
    issues = engine.analyze('mysql', test_context)
    print(f"测试触发 {len(issues)} 条规则")
    for iss in issues:
        print(f"  - {iss['col1']}: {iss['col3'][:60]}")
