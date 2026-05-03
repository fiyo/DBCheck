# -*- coding: utf-8 -*-
"""修复 analyzer.py 中插件规则调用的 db_type 参数"""
import os

fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analyzer.py')
content = open(fpath, 'r', encoding='utf-8').read()

fixes = [
    # (错误的 db_type, 正确的 db_type, 所在函数特征)
    ('"mysql"', "'postgresql'", 'def smart_analyze_pg'),
    ('"postgresql"', "'oracle'", 'def smart_analyze_oracle'),
]

for wrong, right, _ in fixes:
    # 只修复对应函数体内的错误
    # 找函数起始
    idx = content.find("def smart_analyze_pg(")
    if idx > 0 and wrong in content[idx:idx+500]:
        content = content[:idx] + content[idx:].replace(wrong, right, 1)
        print('修复 PG: ' + wrong + ' -> ' + right)

    idx = content.find("def smart_analyze_oracle(")
    if idx > 0 and wrong in content[idx:idx+500]:
        content = content[:idx] + content[idx:].replace(wrong, right, 1)
        print('修复 Oracle: ' + wrong + ' -> ' + right)

open(fpath, 'w', encoding='utf-8').write(content)
print('写入完成，验证中...')

# 验证
from io import StringIO
import sys
output = StringIO()
sys.stdout = output
sys.stderr = output

import subprocess
result = subprocess.run(['grep', '-n', 'analyze_with_plugins', fpath], capture_output=True, text=True)
print(result.stdout)
print(result.stderr)
