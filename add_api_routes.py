# -*- coding: utf-8 -*-
"""向 web_ui.py 插入新的 Pro API 路由（数据源管理 + 规则管理）"""
import os

fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web_ui.py')
content = open(fpath, 'r', encoding='utf-8').read()

new_routes = '''
# ══════════════════════════════════════════════════════════════
#  Pro 数据源管理 API
# ══════════════════════════════════════════════════════════════

@app.route('/api/pro/datasources', methods=['GET'])
def api_pro_datasources():
    """获取数据源列表"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        instances = im.get_all_instances(mask_password=True)
        return jsonify({'ok': True, 'datasources': instances})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>', methods=['GET'])
def api_pro_datasource(instance_id):
    """获取单个数据源"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        inst = im.get_instance(instance_id, mask_password=False)
        if not inst:
            return jsonify({'ok': False, 'error': '数据源不存在'})
        return jsonify({'ok': True, 'datasource': inst})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources', methods=['POST'])
def api_pro_datasource_add():
    """新增数据源"""
    try:
        from pro import get_instance_manager
        from pro.instance_manager import DatabaseInstance
        import uuid

        data = request.get_json()
        inst = DatabaseInstance(
            id=str(uuid.uuid4())[:12],
            name=data.get('name', ''),
            db_type=data.get('db_type', 'mysql'),
            host=data.get('host', ''),
            port=int(data.get('port', 3306)),
            user=data.get('user', ''),
            password=data.get('password', ''),
            service_name=data.get('service_name', ''),
            tags=data.get('tags', []),
            group=data.get('group', 'default'),
            description=data.get('description', ''),
        )
        im = get_instance_manager()
        result = im.add_instance(inst)
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>', methods=['PUT'])
def api_pro_datasource_update(instance_id):
    """更新数据源"""
    try:
        from pro import get_instance_manager
        data = request.get_json()
        im = get_instance_manager()
        result = im.update_instance(instance_id, data)
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>', methods=['DELETE'])
def api_pro_datasource_delete(instance_id):
    """删除数据源"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        result = im.delete_instance(instance_id)
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>/test', methods=['POST'])
def api_pro_datasource_test(instance_id):
    """测试数据源连接"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        result = im.test_connection(instance_id)
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/export', methods=['GET'])
def api_pro_datasources_export():
    """导出数据源 CSV"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        csv_content = im.export_csv()
        return jsonify({'ok': True, 'csv': csv_content})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/import', methods=['POST'])
def api_pro_datasources_import():
    """导入数据源 CSV"""
    try:
        from pro import get_instance_manager
        data = request.get_json()
        csv_content = data.get('csv_content', '')
        im = get_instance_manager()
        result = im.batch_add_from_csv(csv_content)
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  Pro 规则管理 API
# ══════════════════════════════════════════════════════════════

@app.route('/api/pro/rules', methods=['GET'])
def api_pro_rules():
    """获取规则列表"""
    try:
        from pro.rule_engine import get_rule_engine
        db_type = request.args.get('db_type', None)
        engine = get_rule_engine()
        rules = engine.list_rules(db_type)
        return jsonify({'ok': True, 'rules': rules})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/rules', methods=['POST'])
def api_pro_rules_add():
    """新增自定义规则"""
    try:
        from pro.rule_engine import get_rule_engine
        data = request.get_json()
        engine = get_rule_engine()
        rule_id = data.get('id', '')
        if not rule_id:
            return jsonify({'ok': False, 'error': '规则 ID 不能为空'})
        ok = engine.save_custom_rule(data)
        if ok:
            return jsonify({'ok': True, 'message': '规则已保存'})
        return jsonify({'ok': False, 'error': '保存失败'})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/rules/<rule_id>', methods=['DELETE'])
def api_pro_rules_delete(rule_id):
    """删除自定义规则"""
    try:
        from pro.rule_engine import get_rule_engine
        engine = get_rule_engine()
        ok = engine.delete_custom_rule(rule_id)
        if ok:
            return jsonify({'ok': True, 'message': '规则已删除'})
        return jsonify({'ok': False, 'error': '规则不存在'})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/rules/<rule_id>/toggle', methods=['POST'])
def api_pro_rules_toggle(rule_id):
    """启用/禁用规则"""
    try:
        from pro.rule_engine import get_rule_engine
        data = request.get_json()
        enabled = bool(data.get('enabled', True))
        engine = get_rule_engine()
        engine.toggle_rule(rule_id, enabled)
        return jsonify({'ok': True, 'message': '设置已保存'})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


'''

# 在 "if __name__ == '__main__':" 前插入
marker = "\nif __name__ == '__main__':"
if marker in content:
    content = content.replace(marker, new_routes + marker, 1)
    print('[OK] 插入新 API 路由')
else:
    print('[WARN] 未找到 "if __name__ == \'__main__\':" 标记')

open(fpath, 'w', encoding='utf-8').write(content)
print('\n写入完成，验证语法...')

# 验证
import subprocess
result = subprocess.run(
    ['python', '-c', 'import web_ui; print("语法 OK")'],
    capture_output=True, text=True,
    cwd=os.path.dirname(os.path.abspath(__file__))
)
print('stdout:', result.stdout)
print('stderr:', result.stderr[:500] if result.stderr else '')
