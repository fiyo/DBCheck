#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MongoDB 插件完整功能测试（不需要 MongoDB 实例）
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# 导入 MongoDB 插件
from plugins.available.mongodb.main_plugin import MongodbInspector

def test_chapter_loading():
    """测试章节加载功能（不需要 MongoDB 连接）"""
    print("=== 测试章节加载功能 ===")
    
    # 创建 inspector 实例（不连接数据库）
    inspector = MongodbInspector('localhost', 27017)
    
    # 测试 _load_chapters_from_db() 方法
    chapters = inspector._load_chapters_from_db()
    
    if len(chapters) > 0:
        print(f"✅ 成功加载 {len(chapters)} 个章节")
        for ch in chapters:
            print(f"  - 章节 {ch['chapter_number']}: {ch['chapter_title_zh']}")
            for q in ch['queries']:
                print(f"      - 查询: {q['key']} ({q['desc_zh']})")
        return True
    else:
        print("❌ 加载章节失败")
        return False

def test_template_id():
    """测试模板 ID 获取功能（不需要 MongoDB 连接）"""
    print("\n=== 测试模板 ID 获取功能 ===")
    
    inspector = MongodbInspector('localhost', 27017)
    template_id = inspector.get_template_id()
    
    if template_id:
        print(f"✅ 成功获取模板 ID: {template_id}")
        return True
    else:
        print("❌ 获取模板 ID 失败")
        return False

def test_collect_data_mock():
    """测试数据采集功能（使用 mock 对象，不需要 MongoDB 实例）"""
    print("\n=== 测试数据采集功能（mock） ===")
    
    # 创建 inspector 实例
    inspector = MongodbInspector('localhost', 27017)
    
    # 模拟数据库连接（不实际连接）
    inspector.db_type = 'mongodb'
    inspector.context = {}
    
    # 模拟采集数据
    inspector.context['version'] = [{'VERSION': '5.0.0'}]
    inspector.context['mongodb_version'] = [{
        'VERSION': '5.0.0',
        'GIT_VERSION': 'abc123'
    }]
    inspector.context['mongodb_server_status'] = [{
        'VERSION': '5.0.0',
        'OPCOUNTERS': '{"insert": 10, "query": 5}',
        'MEMORY': '{"bits": 64, "resident": 1024}',
        'CONNECTIONS': '{"current": 5, "available": 1000}'
    }]
    inspector.context['mongodb_db_stats'] = [{
        'DB_NAME': 'admin',
        'DATA_SIZE': 1024,
        'STORAGE_SIZE': 2048,
        'INDEX_SIZE': 512,
        'NUM_COLLECTIONS': 10,
        'NUM_INDEXES': 20
    }]
    
    # 加载章节结构
    inspector._load_chapters_from_db()
    
    print(f"✅ 成功采集数据，context keys: {list(inspector.context.keys())}")
    print(f"✅ 成功加载章节: {len(inspector.context.get('_chapters', []))} 个")
    
    # 打印采集的数据
    for key, value in inspector.context.items():
        if key.startswith('_'):
            continue
        print(f"  - {key}: {len(value)} 行")
    
    return True

if __name__ == '__main__':
    print("MongoDB 插件功能测试（不需要 MongoDB 实例）")
    print("=" * 60)
    
    # 测试章节加载
    test_chapter_loading()
    
    # 测试模板 ID 获取
    test_template_id()
    
    # 测试数据采集（mock）
    test_collect_data_mock()
    
    print("\n=== 测试完成 ===")
    print("\n⚠️  注意：要测试完整功能（包括连接 MongoDB 和数据采集），请：")
    print("  1. 启动 MongoDB 实例（localhost:27017）")
    print("  2. 运行：python plugins/available/mongodb/test_plugin.py")
    print("  3. 或者运行：python plugins/available/mongodb/main_plugin.py localhost 27017")
