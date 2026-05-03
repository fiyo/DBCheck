#!/usr/bin/env python3
"""修复 index.html 中 loadDataSources 函数的 deleteDatasource JS 语法错误"""

with open('D:/DBCheck/web_templates/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')

print(f"总行数: {len(lines)}")
print(f"第 880 行 (索引 879): {repr(lines[879])}")

# 文件中实际内容（repr 输出）：
# ' onclick="deleteDatasource(\\\' + ds.id + \\\')">🗑️ 删除</button>\' +'
# 即: onclick="deleteDatasource(\' + ds.id + \')">🗑️ 删除</button>'
# 修复: onclick="deleteDatasource(&#39;" + ds.id + "&#39;)"
for i, line in enumerate(lines):
    if 'deleteDatasource' in line and i == 879:
        print(f"\n找到目标行 {i+1}")
        print(f"当前内容: {repr(line[100:180])}")
        # old: onclick="deleteDatasource(\' + ds.id + \')">  (文件中 \')
        # Python 字符串: onclick="deleteDatasource(\\' + ds.id + \\')">  (\\\' = \')
        # new: onclick="deleteDatasource(&#39;" + ds.id + "&#39;)"
        old = r' onclick="deleteDatasource(\' + ds.id + \')">'
        new = r' onclick="deleteDatasource(&#39;" + ds.id + "&#39;)">'
        print(f"old pattern: {repr(old)}")
        print(f"new pattern: {repr(new)}")
        print(f"old in line: {old in line}")
        if old in line:
            lines[i] = line.replace(old, new)
            print(f"✅ 修复成功!")
            print(f"新内容: {repr(lines[i][100:180])}")
        else:
            print("❌ 未匹配，直接替换")
            # 直接用字符串操作修复
            idx = line.find(r"deleteDatasource(\' + ds.id + \')")
            if idx >= 0:
                part1 = line[:idx] + r'deleteDatasource(&#39;" + ds.id + "&#39;)'
                part2 = line[idx+len(r"deleteDatasource(\' + ds.id + \')"):]
                lines[i] = part1 + part2
                print(f"直接替换后: {repr(lines[i][100:180])}")
            else:
                # 找 onclick 后到 "> 之间的内容
                s = line.find('onclick="')
                e = line.find('">', s)
                if s >= 0 and e >= 0:
                    print(f"onclick 区间: {repr(line[s:e+2])}")
                    print(f"区间长度: {e-s}")

with open('D:/DBCheck/web_templates/index.html', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print("\n文件已保存")
