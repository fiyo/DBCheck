#!/usr/bin/env python3
"""精准修复 index.html 第880行 deleteDatasource onclick 语法错误"""

with open('D:/DBCheck/web_templates/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 第880行 = lines[879]
print("修复前 120-160:", repr(lines[879][120:160]))

# 文件中原始内容（从字符分析得知）：
# 位置 132: \ (ASCII 92)
# 位置 133: ' (ASCII 39) 
# ... + ds.id + ...
# 位置 146: \ (ASCII 92)
# 位置 147: ' (ASCII 39)
# 位置 148: ) (ASCII 41)
# 即文件中字符串为：\' + ds.id + \')

# 在 Python 字符串中，要匹配文件中的 \ 需要写成 \\，要匹配 ' 需要写成 '
old_middle = "\\' + ds.id + \\'"
new_middle = "&#39;" + ds.id + "&#39;"
print("old_middle:", repr(old_middle))
print("found:", old_middle in lines[879])

if old_middle in lines[879]:
    lines[879] = lines[879].replace(old_middle, new_middle)
    print("✅ 修复成功!")
    print("修复后 120-160:", repr(lines[879][120:160]))
else:
    # 直接查找 deleteDatasource( 位置
    idx = lines[879].find('deleteDatasource(')
    if idx >= 0:
        # 找 '  )" 的位置
        # 文件中是 \'  )
        sub = lines[879][idx+16:idx+50]
        print("deleteDatasource( 后面的内容:", repr(sub))

with open('D:/DBCheck/web_templates/index.html', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print("文件已保存")
