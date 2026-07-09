#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck Professional — 专有商业软件，保留一切权利（Proprietary Software, All Rights Reserved）.
# See LICENSE for full license text.
#

"""
数据库巡检配置 API 端点。

这个模块包含了所有与巡检配置相关的 API 端点，包括：
- 巡检模板的 CRUD 操作
- 章节的 CRUD 操作
- SQL 查询的 CRUD 操作
- 导入/导出功能
- SQL 测试功能
"""

import os
import json
import traceback
from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename

from inspection_dal import (
    get_db_connection,
    create_template,
    get_template,
    get_all_templates,
    get_templates_by_db_type,
    get_default_template,
    update_template,
    delete_template,
    create_chapter,
    get_chapter,
    get_chapters_by_template,
    update_chapter,
    delete_chapter,
    reorder_chapters,
    create_query,
    get_query,
    get_queries_by_chapter,
    update_query,
    delete_query,
    reorder_queries,
    export_template,
    import_template,
)


# 创建 Blueprint
inspection_bp = Blueprint('inspection', __name__, url_prefix='/api/inspection')


# ==================== 巡检模板操作 ====================

@inspection_bp.route('/templates', methods=['GET'])
def api_get_templates():
    """
    获取所有巡检模板。
    
    Query 参数：
    - db_type: 数据库类型（可选，如果提供则只返回该类型的模板）
    
    :return: JSON 响应，包含模板列表
    """
    try:
        db_type = request.args.get('db_type')
        
        if db_type:
            templates = get_templates_by_db_type(db_type)
        else:
            templates = get_all_templates()
        
        return jsonify({
            'success': True,
            'data': templates
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取模板列表失败: {str(e)}'
        }), 500


@inspection_bp.route('/templates', methods=['POST'])
def api_create_template():
    """
    创建新的巡检模板。
    
    JSON 参数：
    - db_type: 数据库类型
    - template_name: 模板名称
    - description: 模板描述（可选）
    - is_default: 是否默认模板（可选，默认 0）
    
    :return: JSON 响应，包含新创建的模板 ID
    """
    try:
        data = request.get_json()
        
        db_type = data.get('db_type')
        template_name = data.get('template_name')
        description = data.get('description')
        is_default = data.get('is_default', 0)
        
        if not db_type or not template_name:
            return jsonify({
                'success': False,
                'message': 'db_type 和 template_name 是必填项'
            }), 400
        
        template_id = create_template(
            db_type=db_type,
            template_name=template_name,
            description=description,
            is_default=is_default
        )
        
        return jsonify({
            'success': True,
            'data': {
                'id': template_id
            }
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'创建模板失败: {str(e)}'
        }), 500


@inspection_bp.route('/templates/<int:template_id>', methods=['GET'])
def api_get_template_detail(template_id):
    """
    获取指定 ID 的巡检模板详情（包含章节和查询）。
    
    :param template_id: 模板 ID
    :return: JSON 响应，包含模板详情
    """
    try:
        template = get_template(template_id)
        
        if not template:
            return jsonify({
                'success': False,
                'message': f'模板不存在: {template_id}'
            }), 404
        
        # 获取章节信息
        chapters = get_chapters_by_template(template_id)
        
        # 获取每个章节的查询信息
        for chapter in chapters:
            queries = get_queries_by_chapter(chapter['id'])
            chapter['queries'] = queries
        
        template['chapters'] = chapters
        
        return jsonify({
            'success': True,
            'data': template
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取模板详情失败: {str(e)}'
        }), 500


@inspection_bp.route('/templates/<int:template_id>', methods=['PUT'])
def api_update_template(template_id):
    """
    更新巡检模板。
    
    JSON 参数（所有字段都是可选的）：
    - template_name: 新的模板名称
    - description: 新的模板描述
    - is_default: 是否默认模板
    
    :param template_id: 模板 ID
    :return: JSON 响应，表示更新是否成功
    """
    try:
        data = request.get_json()
        
        template_name = data.get('template_name')
        description = data.get('description')
        is_default = data.get('is_default')
        
        # 预置模板不允许修改名称
        template = get_template(template_id)
        if template and template.get('is_preset', 0) == 1 and template_name is not None:
            return jsonify({
                'success': False,
                'message': '预置模板不能修改名称'
            }), 403
        
        success = update_template(
            template_id=template_id,
            template_name=template_name,
            description=description,
            is_default=is_default
        )
        
        if not success:
            return jsonify({
                'success': False,
                'message': f'模板不存在: {template_id}'
            }), 404
        
        return jsonify({
            'success': True,
            'message': '模板更新成功'
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'更新模板失败: {str(e)}'
        }), 500


@inspection_bp.route('/templates/<int:template_id>', methods=['DELETE'])
def api_delete_template(template_id):
    """
    删除巡检模板（由于外键约束，相关的章节和查询也会被删除）。
    预置模板不能删除。
    
    :param template_id: 模板 ID
    :return: JSON 响应，表示删除是否成功
    """
    try:
        # 预置模板不允许删除
        template = get_template(template_id)
        if template and template.get('is_preset', 0) == 1:
            return jsonify({
                'success': False,
                'message': '预置模板不能删除'
            }), 403
        
        success = delete_template(template_id)
        
        if not success:
            return jsonify({
                'success': False,
                'message': f'模板不存在: {template_id}'
            }), 404
        
        return jsonify({
            'success': True,
            'message': '模板删除成功'
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'删除模板失败: {str(e)}'
        }), 500


# ==================== 章节操作 ====================

@inspection_bp.route('/templates/<int:template_id>/chapters', methods=['GET'])
def api_get_chapters(template_id):
    """
    获取指定模板的所有章节。
    
    :param template_id: 模板 ID
    :return: JSON 响应，包含章节列表
    """
    try:
        chapters = get_chapters_by_template(template_id)
        
        return jsonify({
            'success': True,
            'data': chapters
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取章节列表失败: {str(e)}'
        }), 500


@inspection_bp.route('/templates/<int:template_id>/chapters', methods=['POST'])
def api_create_chapter(template_id):
    """
    为指定模板添加新的章节。
    
    JSON 参数：
    - chapter_number: 章节序号
    - chapter_title_zh: 章节标题（中文）
    - chapter_title_en: 章节标题（英文，可选）
    - description: 章节描述（可选）
    - enabled: 是否启用（可选，默认 1）
    - sort_order: 排序字段（可选，默认 0）
    
    :param template_id: 模板 ID
    :return: JSON 响应，包含新创建的章节 ID
    """
    try:
        data = request.get_json()
        
        chapter_number = data.get('chapter_number')
        chapter_title_zh = data.get('chapter_title_zh')
        chapter_title_en = data.get('chapter_title_en')
        description = data.get('description')
        enabled = data.get('enabled', 1)
        sort_order = data.get('sort_order', 0)
        
        if not chapter_number or not chapter_title_zh:
            return jsonify({
                'success': False,
                'message': 'chapter_number 和 chapter_title_zh 是必填项'
            }), 400
        
        chapter_id = create_chapter(
            template_id=template_id,
            chapter_number=chapter_number,
            chapter_title_zh=chapter_title_zh,
            chapter_title_en=chapter_title_en,
            description=description,
            enabled=enabled,
            sort_order=sort_order
        )
        
        return jsonify({
            'success': True,
            'data': {
                'id': chapter_id
            }
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'创建章节失败: {str(e)}'
        }), 500


@inspection_bp.route('/chapters/<int:chapter_id>', methods=['GET'])
def api_get_chapter_detail(chapter_id):
    """
    获取指定 ID 的章节详情（包含查询）。
    
    :param chapter_id: 章节 ID
    :return: JSON 响应，包含章节详情
    """
    try:
        chapter = get_chapter(chapter_id)
        
        if not chapter:
            return jsonify({
                'success': False,
                'message': f'章节不存在: {chapter_id}'
            }), 404
        
        # 获取查询信息
        queries = get_queries_by_chapter(chapter_id)
        chapter['queries'] = queries
        
        return jsonify({
            'success': True,
            'data': chapter
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取章节详情失败: {str(e)}'
        }), 500


@inspection_bp.route('/chapters/<int:chapter_id>', methods=['PUT'])
def api_update_chapter(chapter_id):
    """
    更新章节。
    
    JSON 参数（所有字段都是可选的）：
    - chapter_number: 新的章节序号
    - chapter_title_zh: 新的章节标题（中文）
    - chapter_title_en: 新的章节标题（英文）
    - description: 新的章节描述
    - enabled: 是否启用
    - sort_order: 新的排序字段
    
    :param chapter_id: 章节 ID
    :return: JSON 响应，表示更新是否成功
    """
    try:
        data = request.get_json()
        
        chapter_number = data.get('chapter_number')
        chapter_title_zh = data.get('chapter_title_zh')
        chapter_title_en = data.get('chapter_title_en')
        description = data.get('description')
        enabled = data.get('enabled')
        sort_order = data.get('sort_order')
        
        success = update_chapter(
            chapter_id=chapter_id,
            chapter_number=chapter_number,
            chapter_title_zh=chapter_title_zh,
            chapter_title_en=chapter_title_en,
            description=description,
            enabled=enabled,
            sort_order=sort_order
        )
        
        if not success:
            return jsonify({
                'success': False,
                'message': f'章节不存在: {chapter_id}'
            }), 404
        
        return jsonify({
            'success': True,
            'message': '章节更新成功'
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'更新章节失败: {str(e)}'
        }), 500


@inspection_bp.route('/chapters/<int:chapter_id>', methods=['DELETE'])
def api_delete_chapter(chapter_id):
    """
    删除章节（由于外键约束，相关的查询也会被删除）。
    
    :param chapter_id: 章节 ID
    :return: JSON 响应，表示删除是否成功
    """
    try:
        success = delete_chapter(chapter_id)
        
        if not success:
            return jsonify({
                'success': False,
                'message': f'章节不存在: {chapter_id}'
            }), 404
        
        return jsonify({
            'success': True,
            'message': '章节删除成功'
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'删除章节失败: {str(e)}'
        }), 500


@inspection_bp.route('/chapters/reorder', methods=['POST'])
def api_reorder_chapters():
    """
    重新排序章节。
    
    JSON 参数：
    - template_id: 模板 ID
    - chapter_ids: 按新的顺序排序的章节 ID 列表
    
    :return: JSON 响应，表示重新排序是否成功
    """
    try:
        data = request.get_json()
        
        template_id = data.get('template_id')
        chapter_ids = data.get('chapter_ids')
        
        if not template_id or not chapter_ids:
            return jsonify({
                'success': False,
                'message': 'template_id 和 chapter_ids 是必填项'
            }), 400
        
        success = reorder_chapters(template_id, chapter_ids)
        
        return jsonify({
            'success': True,
            'message': '章节重新排序成功'
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'章节重新排序失败: {str(e)}'
        }), 500


# ==================== SQL 查询操作 ====================

@inspection_bp.route('/chapters/<int:chapter_id>/queries', methods=['GET'])
def api_get_queries(chapter_id):
    """
    获取指定章节的所有 SQL 查询。
    
    :param chapter_id: 章节 ID
    :return: JSON 响应，包含查询列表
    """
    try:
        queries = get_queries_by_chapter(chapter_id)
        
        return jsonify({
            'success': True,
            'data': queries
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取查询列表失败: {str(e)}'
        }), 500


@inspection_bp.route('/chapters/<int:chapter_id>/queries', methods=['POST'])
def api_create_query(chapter_id):
    """
    为指定章节添加新的 SQL 查询。
    
    JSON 参数：
    - query_key: 查询键名
    - query_sql: SQL 语句
    - query_description_zh: 查询描述（中文，可选）
    - query_description_en: 查询描述（英文，可选）
    - enabled: 是否启用（可选，默认 1）
    - sort_order: 排序字段（可选，默认 0）
    
    :param chapter_id: 章节 ID
    :return: JSON 响应，包含新创建的查询 ID
    """
    try:
        data = request.get_json()
        
        query_key = data.get('query_key')
        query_sql = data.get('query_sql')
        query_description_zh = data.get('query_description_zh')
        query_description_en = data.get('query_description_en')
        enabled = data.get('enabled', 1)
        sort_order = data.get('sort_order', 0)
        
        if not query_key or not query_sql:
            return jsonify({
                'success': False,
                'message': 'query_key 和 query_sql 是必填项'
            }), 400
        
        query_id = create_query(
            chapter_id=chapter_id,
            query_key=query_key,
            query_sql=query_sql,
            query_description_zh=query_description_zh,
            query_description_en=query_description_en,
            enabled=enabled,
            sort_order=sort_order
        )
        
        return jsonify({
            'success': True,
            'data': {
                'id': query_id
            }
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'创建查询失败: {str(e)}'
        }), 500


@inspection_bp.route('/queries/<int:query_id>', methods=['GET'])
def api_get_query_detail(query_id):
    """
    获取指定 ID 的 SQL 查询详情。
    
    :param query_id: 查询 ID
    :return: JSON 响应，包含查询详情
    """
    try:
        query = get_query(query_id)
        
        if not query:
            return jsonify({
                'success': False,
                'message': f'查询不存在: {query_id}'
            }), 404
        
        return jsonify({
            'success': True,
            'data': query
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取查询详情失败: {str(e)}'
        }), 500


@inspection_bp.route('/queries/<int:query_id>', methods=['PUT'])
def api_update_query(query_id):
    """
    更新 SQL 查询。
    
    JSON 参数（所有字段都是可选的）：
    - query_key: 新的查询键名
    - query_sql: 新的 SQL 语句
    - query_description_zh: 新的查询描述（中文）
    - query_description_en: 新的查询描述（英文）
    - enabled: 是否启用
    - sort_order: 新的排序字段
    
    :param query_id: 查询 ID
    :return: JSON 响应，表示更新是否成功
    """
    try:
        data = request.get_json()
        
        query_key = data.get('query_key')
        query_sql = data.get('query_sql')
        query_description_zh = data.get('query_description_zh')
        query_description_en = data.get('query_description_en')
        enabled = data.get('enabled')
        sort_order = data.get('sort_order')
        
        success = update_query(
            query_id=query_id,
            query_key=query_key,
            query_sql=query_sql,
            query_description_zh=query_description_zh,
            query_description_en=query_description_en,
            enabled=enabled,
            sort_order=sort_order
        )
        
        if not success:
            return jsonify({
                'success': False,
                'message': f'查询不存在: {query_id}'
            }), 404
        
        return jsonify({
            'success': True,
            'message': '查询更新成功'
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'更新查询失败: {str(e)}'
        }), 500


@inspection_bp.route('/queries/<int:query_id>', methods=['DELETE'])
def api_delete_query(query_id):
    """
    删除 SQL 查询。
    
    :param query_id: 查询 ID
    :return: JSON 响应，表示删除是否成功
    """
    try:
        success = delete_query(query_id)
        
        if not success:
            return jsonify({
                'success': False,
                'message': f'查询不存在: {query_id}'
            }), 404
        
        return jsonify({
            'success': True,
            'message': '查询删除成功'
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'删除查询失败: {str(e)}'
        }), 500


@inspection_bp.route('/queries/reorder', methods=['POST'])
def api_reorder_queries():
    """
    重新排序 SQL 查询。
    
    JSON 参数：
    - chapter_id: 章节 ID
    - query_ids: 按新的顺序排序的查询 ID 列表
    
    :return: JSON 响应，表示重新排序是否成功
    """
    try:
        data = request.get_json()
        
        chapter_id = data.get('chapter_id')
        query_ids = data.get('query_ids')
        
        if not chapter_id or not query_ids:
            return jsonify({
                'success': False,
                'message': 'chapter_id 和 query_ids 是必填项'
            }), 400
        
        success = reorder_queries(chapter_id, query_ids)
        
        return jsonify({
            'success': True,
            'message': '查询重新排序成功'
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'查询重新排序失败: {str(e)}'
        }), 500


# ==================== 导入/导出功能 ====================

@inspection_bp.route('/templates/<int:template_id>/export', methods=['GET'])
def api_export_template(template_id):
    """
    导出巡检模板为 JSON 格式。
    
    :param template_id: 模板 ID
    :return: JSON 响应，包含模板配置；或者文件下载
    """
    try:
        template_config = export_template(template_id)
        
        # 返回 JSON 响应
        return jsonify({
            'success': True,
            'data': template_config
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'导出模板失败: {str(e)}'
        }), 500


@inspection_bp.route('/templates/import', methods=['POST'])
def api_import_template():
    """
    从 JSON 格式导入巡检模板。
    
    JSON 参数：
    - template_config: 模板配置（字典）
    - overwrite: 如果模板已存在，是否覆盖（可选，默认 False）
    
    :return: JSON 响应，包含导入的模板 ID
    """
    try:
        data = request.get_json()
        
        template_config = data.get('template_config')
        overwrite = data.get('overwrite', False)
        
        if not template_config:
            return jsonify({
                'success': False,
                'message': 'template_config 是必填项'
            }), 400
        
        template_id = import_template(template_config, overwrite=overwrite)
        
        return jsonify({
            'success': True,
            'data': {
                'id': template_id
            }
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'导入模板失败: {str(e)}'
        }), 500


# ==================== SQL 测试功能 ====================

@inspection_bp.route('/test-sql', methods=['POST'])
def api_test_sql():
    """
    测试 SQL 语句（在指定数据库上执行）。
    
    JSON 参数：
    - db_type: 数据库类型
    - instance_id: 数据源 ID（可选，如果提供则使用已配置的数据源）
    - connection: 数据库连接信息（可选，如果不使用 instance_id）
    - sql: 要测试的 SQL 语句
    
    :return: JSON 响应，包含 SQL 执行结果
    """
    try:
        data = request.get_json()
        
        db_type = data.get('db_type')
        instance_id = data.get('instance_id')
        connection = data.get('connection')
        sql = data.get('sql')
        
        if not db_type or not sql:
            return jsonify({
                'success': False,
                'message': 'db_type 和 sql 是必填项'
            }), 400
        
        # TODO: 实现 SQL 测试功能
        # 1. 如果提供了 instance_id，则从数据库获取连接信息
        # 2. 如果提供了 connection，则使用提供的连接信息
        # 3. 连接到数据库
        # 4. 执行 SQL 语句
        # 5. 返回结果
        
        return jsonify({
            'success': False,
            'message': 'SQL 测试功能尚未实现'
        }), 501
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'SQL 测试失败: {str(e)}'
        }), 500


# ==================== 基线配置操作 ====================

@inspection_bp.route('/baselines', methods=['GET'])
def api_get_baselines():
    """
    获取基线配置列表。
    
    Query 参数：
    - db_type: 数据库类型（可选，如果提供则只返回该类型的基线配置）
    - enabled_only: 是否只返回启用的配置（可选，默认 true）
    
    :return: JSON 响应，包含基线配置列表
    """
    try:
        from inspection_dal import get_baselines_by_db_type
        
        db_type = request.args.get('db_type')
        enabled_only = request.args.get('enabled_only', 'true').lower() == 'true'
        
        if db_type:
            baselines = get_baselines_by_db_type(db_type, enabled_only)
        else:
            # 如果没有指定 db_type，返回所有基线配置
            from inspection_dal import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            
            if enabled_only:
                cursor.execute("SELECT * FROM inspection_baseline WHERE enabled = 1 ORDER BY db_type, param_name")
            else:
                cursor.execute("SELECT * FROM inspection_baseline ORDER BY db_type, param_name")
            
            baselines = [dict(row) for row in cursor.fetchall()]
            conn.close()
        
        return jsonify({
            'success': True,
            'data': baselines
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取基线配置失败: {str(e)}'
        }), 500


@inspection_bp.route('/baselines', methods=['POST'])
def api_create_baseline():
    """
    创建新的基线配置。
    
    JSON 参数：
    - db_type: 数据库类型
    - param_name: 参数名称
    - query_sql: 查询 SQL
    - operator: 运算符（可选，默认 '='）
    - expected_value: 期望值（可选）
    - expected_value_min: 期望值最小值（可选，用于 BETWEEN）
    - expected_value_max: 期望值最大值（可选，用于 BETWEEN）
    - risk_level: 风险等级（可选，默认 'MEDIUM'）
    - description_zh: 描述（中文，可选）
    - description_en: 描述（英文，可选）
    
    :return: JSON 响应，包含新创建的基线配置 ID
    """
    try:
        from inspection_dal import create_baseline
        
        data = request.get_json()
        
        db_type = data.get('db_type')
        param_name = data.get('param_name')
        query_sql = data.get('query_sql')
        
        if not db_type or not param_name or not query_sql:
            return jsonify({
                'success': False,
                'message': 'db_type, param_name 和 query_sql 是必填项'
            }), 400
        
        baseline_id = create_baseline(
            db_type=db_type,
            param_name=param_name,
            query_sql=query_sql,
            operator=data.get('operator', '='),
            expected_value=data.get('expected_value'),
            expected_value_min=data.get('expected_value_min'),
            expected_value_max=data.get('expected_value_max'),
            risk_level=data.get('risk_level', 'MEDIUM'),
            description_zh=data.get('description_zh'),
            description_en=data.get('description_en')
        )
        
        return jsonify({
            'success': True,
            'message': '基线配置创建成功',
            'data': {'id': baseline_id}
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'创建基线配置失败: {str(e)}'
        }), 500


@inspection_bp.route('/baselines/<int:baseline_id>', methods=['PUT'])
def api_update_baseline(baseline_id):
    """
    更新基线配置。
    
    JSON 参数：
    - param_name: 新的参数名称（可选）
    - query_sql: 新的查询 SQL（可选）
    - operator: 新的运算符（可选）
    - expected_value: 新的期望值（可选）
    - expected_value_min: 新的期望值最小值（可选）
    - expected_value_max: 新的期望值最大值（可选）
    - risk_level: 新的风险等级（可选）
    - description_zh: 新的描述（中文，可选）
    - description_en: 新的描述（英文，可选）
    - enabled: 是否启用（可选）
    
    :return: JSON 响应，包含更新结果
    """
    try:
        from inspection_dal import update_baseline
        
        data = request.get_json()
        
        success = update_baseline(
            baseline_id=baseline_id,
            param_name=data.get('param_name'),
            query_sql=data.get('query_sql'),
            operator=data.get('operator'),
            expected_value=data.get('expected_value'),
            expected_value_min=data.get('expected_value_min'),
            expected_value_max=data.get('expected_value_max'),
            risk_level=data.get('risk_level'),
            description_zh=data.get('description_zh'),
            description_en=data.get('description_en'),
            enabled=data.get('enabled')
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': '基线配置更新成功'
            })
        else:
            return jsonify({
                'success': False,
                'message': '基线配置不存在'
            }), 404
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'更新基线配置失败: {str(e)}'
        }), 500


@inspection_bp.route('/baselines/<int:baseline_id>', methods=['DELETE'])
def api_delete_baseline(baseline_id):
    """
    删除基线配置。
    
    :param baseline_id: 基线配置 ID
    :return: JSON 响应，包含删除结果
    """
    try:
        from inspection_dal import delete_baseline
        
        success = delete_baseline(baseline_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': '基线配置删除成功'
            })
        else:
            return jsonify({
                'success': False,
                'message': '基线配置不存在'
            }), 404
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'删除基线配置失败: {str(e)}'
        }), 500


@inspection_bp.route('/baselines/init', methods=['POST'])
def api_init_baselines():
    """
    首次初始化默认基线配置（仅在各 db_type 无数据时插入，幂等操作）。

    :return: JSON 响应，包含初始化结果
    """
    try:
        from inspection_dal import init_default_baselines

        init_default_baselines()

        return jsonify({
            'success': True,
            'message': '默认基线配置初始化成功'
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'初始化默认基线配置失败: {str(e)}'
        }), 500


@inspection_bp.route('/baselines/reset', methods=['POST'])
def api_force_reset_baselines():
    """
    手动强制重置基线配置。清空指定（或全部）db_type 的基线后重新插入默认值。
    注意：此操作不可逆，用户自定义的基线将被清除。

    :return: JSON 响应，包含重置结果
    """
    try:
        from inspection_dal import force_reset_baselines

        data = request.get_json(silent=True) or {}
        db_type = data.get('db_type')
        force_reset_baselines(db_type=db_type)

        if db_type:
            msg = f'{db_type} 基线配置已重置'
        else:
            msg = '所有基线配置已重置'

        return jsonify({
            'success': True,
            'message': msg
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'基线配置重置失败: {str(e)}'
        }), 500
