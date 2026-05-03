# -*- coding: utf-8 -*-
"""给 analyzer.py 中所有 smart_analyze_* 函数末尾插入插件规则调用"""
import os

fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analyzer.py')
content = open(fpath, 'r', encoding='utf-8').read()

# 要处理的 (函数名, db_type) 列表
targets = [
    ('smart_analyze_mysql', 'mysql'),
    ('smart_analyze_pg', 'postgresql'),
    ('smart_analyze_oracle', 'oracle'),
]

# 插件代码块（注意用 \\n 而不是 \n，因为 Python 字符串转义）
plugin_lines = [
    '    # ── 插件规则检查（Pro 版）' + '─' * 30,
    '    try:',
    '        from pro.rule_engine import analyze_with_plugins',
    '        plugin_issues = analyze_with_plugins("{db_type}", context)',
    '        if plugin_issues:',
    '            issues.extend(plugin_issues)',
    '    except Exception:',
    '        pass',
    '',
]
# 注意：下面用 format 填入 db_type

for func_name, db_type in targets:
    start = content.find('def ' + func_name + '(')
    if start < 0:
        print('  [跳过] 未找到函数: ' + func_name)
        continue

    rest = content[start+1:]
    next_def = rest.find('\ndef ')
    if next_def < 0:
        func_body = content[start:]
        func_end = len(content)
    else:
        func_body = content[start:start+1+next_def]
        func_end = start + 1 + next_def

    # 在 "    return issues" 前插入
    marker = '\n    return issues'
    if marker not in func_body:
        print('  [跳过] ' + func_name + ' 中未找到 "return issues"')
        continue

    # 构建插件代码
    block = '\n'.join([L.replace('{db_type}', db_type) for L in plugin_lines])

    new_body = func_body.replace(marker, '\n' + block + '    return issues', 1)
    content = content[:start] + new_body + content[func_end:]
    print('  [OK] ' + func_name + ' (' + db_type + ')')

open(fpath, 'w', encoding='utf-8').write(content)
print('\n写入 analyzer.py 完成')
