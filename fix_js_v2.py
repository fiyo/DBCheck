#!/usr/bin/env python3
"""精准修复 index.html 第879行（0索引）—— onclick 语法错误"""

with open('D:/DBCheck/web_templates/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

line880 = lines[879]  # 行号879 = 第880行
print("修复前第880行尾端:", repr(line880[-50:]))

# 查找问题核心： ')">🗑️ 前面的 \'
# 文件中实际是：...)">🗑️ 删除</button>
# Python repr 中： \" ) \" >  🗑  🗑️ [其余]
# 思路：直接把该行中 从 onclick="deleteDatasource 到 "> 之间的内容替换掉

old_onclick = 'onclick="deleteDatasource(\\' + ds.id + \')">'   # 找这个模式
new_onclick = 'onclick="deleteDatasource(&#39;" + ds.id + "&#39;)">'   # 替换为

if old_onclick in line880:
    lines[879] = line880.replace(old_onclick, new_onclick)
    print("✅ 修复成功！")
else:
    # 尝试文本 up to \')"> 
    # 直接从文件中提取该部分
    idx = line880.find('deleteDatasource(')
    if idx >= 0:
        # 找到 \')"> 的位置
        end_idx = line880.find('\')">', idx)
        if end_idx >= 0:
            before = line880[:idx]
            after = line880[end_idx+4:]  # skip \')">
            new_mid = 'deleteDatasource(&#39;" + ds.id + "&#39;)" )">"   # 不对，这样不行
            print(f"手动模式：idx={idx}, end_idx={end_idx}")
            print(f"before: {repr(before)}")
            print(f"after: {repr(after)}")
        else:
            print("未找到 ')>\"")

with open('D:/DBCheck/web_templates/index.html', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print("文件已保存（如有修复会自动写入）")
