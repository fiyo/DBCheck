#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
为 MongoDB 在 inspection.db 中创建模板和章节
读取 plugins/available/mongodb/sql_templates.json 配置
支持 --force 参数强制重新创建

v2.0: 适配新章节结构（12+ 章节、20+ 采集项）
支持 category / condition 字段传递
"""

import os
import sys
import json
import argparse

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from inspection_dal import (
    init_database,
    create_template,
    create_chapter,
    create_query,
    get_templates_by_db_type,
    delete_template,  # 使用 delete_template（支持级联删除）
    DEFAULT_DB_PATH,
)


def load_template_config(plugin_dir=None):
    """从 sql_templates.json 加载模板配置"""
    if plugin_dir is None:
        plugin_dir = os.path.join(os.path.dirname(__file__), 'plugins', 'available', 'mongodb')
    
    config_path = os.path.join(plugin_dir, 'sql_templates.json')
    
    if not os.path.exists(config_path):
        print(f"[WARN] 配置文件不存在: {config_path}")
        return None
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    print(f"[OK] 已加载模板配置: {config_path}")
    return config


def init_mongodb_template(db_path=DEFAULT_DB_PATH, plugin_dir=None, force=False):
    """为 MongoDB 创建巡检模板和章节
    
    Args:
        db_path: 数据库路径
        plugin_dir: 插件目录（包含 sql_templates.json）
        force: 是否强制重新创建（删除现有模板）
    
    Returns:
        template_id: 成功返回模板ID，失败返回 None
    """
    print("=== 初始化 MongoDB 巡检模板 ===")
    
    # 1. 加载模板配置
    config = load_template_config(plugin_dir)
    if not config:
        print("[ERROR] 无法加载模板配置")
        return None
    
    # 2. 初始化数据库（确保表存在）
    init_database(db_path)
    print("[OK] 数据库已初始化")
    
    # 3. 检查是否已有 mongodb 模板
    existing = get_templates_by_db_type("mongodb", db_path)
    
    if existing and force:
        # 强制重新创建：删除现有模板
        print(f"[INFO] 强制重新创建，删除现有模板（ID: {existing[0]['id']}）...")
        try:
            # delete_template() 支持级联删除（由于外键约束）
            # force=True 允许删除预置模板
            success = delete_template(existing[0]['id'], db_path, force=True)
            if success:
                print(f"[OK] 已删除现有模板")
                existing = None  # 清空，后面会重新创建
            else:
                print(f"[ERROR] 删除模板失败")
                return None
        except Exception as e:
            print(f"[ERROR] 删除模板失败: {e}")
            return None
    
    if existing and not force:
        print(f"[WARN] MongoDB 模板已存在（ID: {existing[0]['id']}）")
        print(f"[INFO] 使用现有模板（ID: {existing[0]['id']}）")
        print(f"[TIP] 如需重新创建，请使用 --force 参数")
        return existing[0]['id']
    
    # 4. 创建模板
    template_id = create_template(
        db_type="mongodb",
        template_name="MongoDB 默认巡检模板",
        template_name_en="MongoDB Default Inspection Template",
        is_default=1,
        db_path=db_path
    )
    print(f"[OK] 已创建模板（ID: {template_id}）")
    
    # 5. 从配置文件创建章节和查询
    chapters = config.get('chapters', [])
    
    for ch_data in chapters:
        # 创建章节
        chapter_id = create_chapter(
            template_id=template_id,
            chapter_number=ch_data['chapter_number'],
            chapter_title_zh=ch_data['chapter_title_zh'],
            chapter_title_en=ch_data['chapter_title_en'],
            description=ch_data.get('chapter_title_zh', ''),  # 使用中文标题作为描述
            db_path=db_path
        )
        print(f"[OK] 已创建章节：{ch_data['chapter_title_zh']} (ID: {chapter_id})")
        
        # 创建查询
        for idx, q_data in enumerate(ch_data.get('queries', []), 1):
            # v2.0: 支持 category / condition 字段（拼入 query_description）
            desc_zh = q_data.get('desc_zh', '')
            category = q_data.get('category', '')
            condition = q_data.get('condition', '')
            if category:
                desc_zh = f"[{category}] {desc_zh}" if desc_zh else f"[{category}]"
            if condition:
                desc_zh = f"{desc_zh} (条件: {condition})" if desc_zh else f"条件: {condition}"

            query_id = create_query(
                chapter_id=chapter_id,
                query_key=q_data['key'],
                query_sql=q_data.get('command', ''),  # MongoDB 使用 command 而不是 query_sql
                query_description_zh=desc_zh,
                query_description_en=q_data.get('desc_en', ''),
                sort_order=q_data.get('sort_order', idx),
                db_path=db_path
            )
            print(f"  [OK] 已创建查询：{q_data['key']} (ID: {query_id})")
    
    print("\n=== MongoDB 巡检模板初始化完成 ===")
    print(f"模板 ID：{template_id}")
    print(f"章节数：{len(chapters)}")
    print(f"查询数：{sum(len(ch.get('queries', [])) for ch in chapters)}")
    
    return template_id


if __name__ == '__main__':
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='初始化 MongoDB 巡检模板')
    parser.add_argument('--db-path', default=DEFAULT_DB_PATH, help='数据库路径')
    parser.add_argument('--plugin-dir', help='插件目录（包含 sql_templates.json）')
    parser.add_argument('--force', action='store_true', help='强制重新创建（删除现有模板）')
    
    args = parser.parse_args()
    
    print(f"使用数据库：{args.db_path}")
    if args.plugin_dir:
        print(f"使用插件目录：{args.plugin_dir}")
    if args.force:
        print(f"⚠️  强制重新创建模式")
    
    template_id = init_mongodb_template(
        db_path=args.db_path,
        plugin_dir=args.plugin_dir,
        force=args.force
    )
    
    if template_id:
        print(f"\n✅ MongoDB 模板初始化成功（ID: {template_id}）")
    else:
        print(f"\n❌ MongoDB 模板初始化失败")
        sys.exit(1)
