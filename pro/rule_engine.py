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


# ── 允许在 condition 中安全调用的值方法（仅字符串/常用不可变方法，
#    禁止任何危险属性/方法，如 __class__ / __bases__ / eval / exec 等）────────
_SAFE_METHODS = {
    'get',
    'upper', 'lower', 'strip', 'lstrip', 'rstrip',
    'replace', 'startswith', 'endswith', 'split', 'rsplit',
    'title', 'capitalize', 'count', 'find', 'index',
    'isdigit', 'isalpha', 'isnumeric', 'isalnum', 'isspace',
}


def _int(v, default: int = 0) -> int:
    """规则引擎安全整型解析：容忍逗号/百分号/空白，解析失败回退 default。

    供 YAML condition 中以 _int(x) 形式调用（如 yasdb tablespace 规则）。
    """
    try:
        return int(str(v).replace(',', '').replace('%', '').strip())
    except (ValueError, TypeError):
        return default


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
    仅允许：比较运算、算术运算、逻辑运算、取值操作、白名单 builtins，
           以及白名单安全方法调用（如 .upper()/.strip()/.startswith() 等）。
    禁止：import、dunder 属性（__x__）、白名单外的属性/方法/函数调用。

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
        '_int': _int,
    }

    try:
        # ast 预检查
        tree = ast.parse(expr, mode='eval')
        for node in ast.walk(tree):
            # 拒绝 import
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return False, "不允许 import 操作"
            # 属性访问：仅允许白名单安全方法，禁止 dunder / 未知属性
            if isinstance(node, ast.Attribute):
                attr = node.attr
                if attr.startswith('__') and attr.endswith('__'):
                    return False, f"不允许的属性访问: {attr}"
                if attr not in _SAFE_METHODS:
                    return False, f"不允许的属性访问: {attr}"
                continue  # 白名单方法（通常是 .upper()/.strip()）放行
            # 函数/方法调用
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    if func.id in safe_builtins:
                        continue
                    return False, f"不允许函数调用: {func.id}"
                if isinstance(func, ast.Attribute):
                    if func.attr in _SAFE_METHODS:
                        continue
                    return False, f"不允许的方法调用: {func.attr}"
                return False, f"不允许的函数调用: {ast.dump(func)}"

        # 注意：eval() 接收的是 code object，不能直接传 AST 节点
        # （eval(ast_node) 在 CPython 下会抛
        #  "eval() arg 1 must be a string, bytes or code object"）。
        # 必须先 compile() 成 code object 才能执行——这是规则引擎此前
        #  对所有 condition 都静默失败（"全库 YAML 未生效"）的真正根因之一。
        code = compile(tree, '<rule_condition>', 'eval')
        result = eval(code, {'__builtins__': safe_builtins}, locals_dict)
        return bool(result), ""
    except Exception as e:
        return False, str(e)


class _SafeContext:
    """递归安全包装：同时支持属性与下标访问，dict/list 自动递归包装。

    让 YAML 参数表达式里的 `context.x` / `context['x']` / `context.x[0].y`
    在 context 是普通 dict 时也能正确取值。
    同时委托 dict/list 的内置方法（如 .get()/.items()），使
    `context.get('key')` 调用底层真实方法而非被误当作 key 查找。
    """

    def __init__(self, data):
        self._d = data

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if isinstance(self._d, dict):
            # 若 name 是 dict 内置方法（如 get/keys/values），委托给真实方法，
            # 否则按 key 查找（使 context.x == context['x']）
            if hasattr(type(self._d), name):
                attr = getattr(self._d, name)
                if callable(attr):
                    def _method(*args, **kwargs):
                        return _wrap(attr(*args, **kwargs))
                    return _method
                return _wrap(attr)
            return _wrap(self._d.get(name))
        return _wrap(getattr(self._d, name, None))

    def __getitem__(self, key):
        if isinstance(self._d, (dict, list)):
            try:
                return _wrap(self._d[key])
            except (KeyError, IndexError, TypeError):
                return None
        return None

    def __contains__(self, key):
        try:
            return key in self._d
        except TypeError:
            return False

    def __len__(self):
        try:
            return len(self._d)
        except TypeError:
            return 0

    def __bool__(self):
        try:
            return bool(self._d)
        except TypeError:
            return False

    def __repr__(self):
        return repr(self._d)


def _wrap(v):
    if isinstance(v, (dict, list)):
        return _SafeContext(v)
    return v


def _unwrap(v):
    if isinstance(v, _SafeContext):
        return _unwrap(v._d)
    if isinstance(v, dict):
        return {k: _unwrap(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_unwrap(x) for x in v]
    return v


# 参数表达式允许的安全 builtins（比条件更宽松，但仍是白名单）
# 注意 _int 已在文件上方定义为模块级函数，可直接引用
_PARAM_BUILTINS = {
    'int': int, 'float': float, 'str': str,
    'len': len, 'min': min, 'max': max, 'abs': abs, 'round': round,
    'any': any, 'all': all, 'sum': sum, 'bool': bool,
    '_int': _int,
}


def _safe_eval_param(expr: str, context: Dict) -> Any:
    """安全求值参数表达式（${context...} 内部内容）。

    允许：比较/算术/逻辑运算、条件表达式(IfExp)、下标、非 dunder 属性访问、
         白名单 builtins（int/len/_int/...）与 _SAFE_METHODS 方法调用。
    禁止：import、dunder 属性（__x__）、白名单外的函数/方法调用。
    返回解包后的原生值；任何失败返回 None（与 gbase 表达式内部的 else 兜底配合）。
    """
    try:
        tree = ast.parse(expr, mode='eval')
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return None
            if isinstance(node, ast.Attribute):
                if node.attr.startswith('__') and node.attr.endswith('__'):
                    return None
                continue
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    if func.id not in _PARAM_BUILTINS:
                        return None
                elif isinstance(func, ast.Attribute):
                    if func.attr not in _SAFE_METHODS:
                        return None
                else:
                    return None
        code = compile(tree, '<rule_param>', 'eval')
        result = eval(code, {'__builtins__': _PARAM_BUILTINS}, {'context': _SafeContext(context)})
        result = _unwrap(result)
        # 数值字符串转数值（SHOW VARIABLES 等返回的 Value 多为字符串，如 '200'/'95'）
        if isinstance(result, str):
            try:
                return int(result)
            except (ValueError, TypeError):
                pass
            try:
                return float(result)
            except (ValueError, TypeError):
                pass
        return result
    except Exception:
        return None


def _resolve_param(param_expr: str, context: Dict) -> Any:
    """
    解析参数表达式，支持：
    - 字面量：90, "hello", 1.5
    - 完整 Python 表达式（${context...}）：三元/方法调用/下标等，
      交由 _safe_eval_param 在白名单沙箱中安全求值
    """
    if not isinstance(param_expr, str):
        return param_expr
    m = re.match(r'^\$\{(.+)\}$', param_expr.strip())
    if m:
        return _safe_eval_param(m.group(1), context)
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

        # 计算额外变量（如连接数使用率 conn_pct 等）
        extra = {}
        try:
            # 自动计算连接数使用率 conn_pct：
            # 当有 max_conn 且存在表示"已用连接"的参数（max_used 或 cur_conn）时计算
            if 'max_conn' in resolved_params:
                mc = resolved_params['max_conn']
                mu = resolved_params.get('max_used', resolved_params.get('cur_conn', None))
                if mu is not None and mc and mc > 0:
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
