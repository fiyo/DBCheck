#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向 zh.py 和 en.py 添加缺失的 webui.group_filter 翻译键"""

zh_file = r'D:\DBCheck\i18n\zh.py'
en_file = r'D:\DBCheck\i18n\en.py'

# 读取文件
with open(zh_file, 'r', encoding='utf-8') as f:
    zh = f.read()

with open(en_file, 'r', encoding='utf-8') as f:
    en = f.read()

# 向 zh.py 插入
if "'webui.group_filter'" not in zh:
    zh = zh.replace(
        "'webui.datasources_add': '添加数据源',\n",
        "'webui.datasources_add': '添加数据源',\n    'webui.group_filter': '分组筛选：',\n"
    )
    with open(zh_file, 'w', encoding='utf-8') as f:
        f.write(zh)
    print('zh.py: webui.group_filter 添加成功')
else:
    print('zh.py: webui.group_filter 已存在')

# 向 en.py 插入
if "'webui.group_filter'" not in en:
    en = en.replace(
        "'webui.datasources_add': 'Add Datasource',\n",
        "'webui.datasources_add': 'Add Datasource',\n    'webui.group_filter': 'Group Filter: ', \n"
    )
    with open(en_file, 'w', encoding='utf-8') as f:
        f.write(en)
    print('en.py: webui.group_filter 添加成功')
else:
    print('en.py: webui.group_filter 已存在')
