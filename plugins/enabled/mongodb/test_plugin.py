#!/usr/bin/env python3
# -*- coding:utf-8 -*-

"""
MongoDB 插件测试脚本 - 只测试数据采集功能
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# 导入 MongoDB 插件
from plugins.available.mongodb.main_plugin import MongodbInspector

def test_connect():
    """测试连接功能"""
    print("=== 测试 MongoDB 连接 ===")
    
    # 使用默认的 localhost:27017
    inspector = MongodbInspector('localhost', 27017)
    
    ok, version = inspector.connect()
    if ok:
        print(f"✅ 连接成功，版本: {version}")
        return inspector
    else:
        print(f"❌ 连接失败: {version}")
        return None

def test_collect_data(inspector):
    """测试数据采集功能"""
    print("\n=== 测试数据采集 ===")
    
    context = inspector.collect_data()
    if isinstance(context, tuple) and len(context) == 2 and context[0] == False:
        print(f"❌ 数据采集失败: {context[1]}")
        return False
    
    print(f"✅ 数据采集成功，context keys: {list(context.keys())}")
    
    # 打印采集的数据
    for key, value in context.items():
        if key.startswith('_'):
            continue
        print(f"  - {key}: {len(value)} 行")
        if len(value) > 0:
            print(f"    示例: {value[0]}")
    
    return True

def test_load_chapters(inspector):
    """测试章节加载功能"""
    print("\n=== 测试章节加载 ===")
    
    chapters = inspector._load_chapters_from_db()
    print(f"✅ 加载了 {len(chapters)} 个章节")
    
    for ch in chapters:
        print(f"  - 章节 {ch['chapter_number']}: {ch['chapter_title_zh']}")
        for q in ch['queries']:
            print(f"      - 查询: {q['key']} ({q['desc_zh']})")
    
    return True

if __name__ == '__main__':
    print("MongoDB 插件测试")
    print("=" * 50)
    
    # 测试连接
    inspector = test_connect()
    if not inspector:
        print("\n⚠️ 无法连接 MongoDB，请确认:")
        print("  1. MongoDB 已启动")
        print("  2. 地址为 localhost:27017")
        print("  3. 或者修改脚本中的连接参数")
        sys.exit(1)
    
    # 测试章节加载
    test_load_chapters(inspector)
    
    # 测试数据采集
    test_collect_data(inspector)
    
    print("\n=== 测试完成 ===")
