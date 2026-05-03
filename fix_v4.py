#!/usr/bin/env python3
import sys

with open('D:/DBCheck/web_templates/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"行数: {len(lines)}")
print(f"第880行部分内容: {repr(lines[879][120:165])}")

# 文件中的内容是： \ + ' + 空格 + + 空格 + ds.id 空格 + + 空格 + \ + '
# Python 字符串中 \\ 代表一个 \，' 代表一个 '
old = "\\' + ds.id + \\'"
new = "&#39;\" + ds.id + \"&#39;"

print(f"\n查找: {repr(old)}")
print(f"替换为: {repr(new)}")
print(f"匹配: {old in lines[879]}")

if old in lines[879]:
    lines[879] = lines[879].replace(old, new)
    with open('D:/DBCheck/web_templates/index.html', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print("\n✅ 第880行修复成功！")
    print(f"修复后内容: {repr(lines[879][120:165])}")
else:
    print("\n❌ 未找到匹配，显示上下文：")
    idx = lines[879].find('deleteDatasource(')
    print(repr(lines[879][idx:idx+50]))
