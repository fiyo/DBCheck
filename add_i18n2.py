# -*- coding: utf-8 -*-
"""正确追加 i18n 翻译键"""
import os

def add_keys(fpath, new_keys):
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 去掉末尾的 }
    content = content.rstrip()
    if content.endswith('}'):
        content = content[:-1].rstrip()
    # 确保最后一行的最后一个字符不是逗号（如果不是，添加一个）
    lines = content.split('\n')
    last = lines[-1].rstrip()
    if not last.endswith(','):
        lines[-1] = last + ','
    content = '\n'.join(lines)

    # 添加新键
    for key in new_keys:
        content += '\n' + key

    content += '\n}\n'

    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(content)
    print('已更新', os.path.basename(fpath))

base = os.path.dirname(os.path.abspath(__file__))

zh = [
    '    # ── 数据源管理 ──',
    '    "webui.nav_datasources": "数据源",',
    '    "webui.datasources_title": "数据源管理",',
    '    "webui.datasources_empty": "暂无数据源，点击上方按钮添加",',
    '    "webui.datasources_add": "添加数据源",',
    '    "webui.datasources_edit": "编辑数据源",',
    '    "webui.datasources_delete": "删除",',
    '    "webui.datasources_test": "测试",',
    '    "webui.datasources_testing": "正在测试...",',
    '    "webui.datasources_test_ok": "连接成功",',
    '    "webui.datasources_test_fail": "连接失败",',
    '    "webui.datasources_import": "导入 CSV",',
    '    "webui.datasources_export": "导出 CSV",',
    '    "webui.datasources_csv_hint": "支持: name,db_type,host,port,user,password,service_name,group,tags,description",',
    '    "webui.datasources_csv_placeholder": "粘贴 CSV 内容...",',
    '    "webui.datasources_field_name": "名称",',
    '    "webui.datasources_field_type": "类型",',
    '    "webui.datasources_field_host": "主机",',
    '    "webui.datasources_field_port": "端口",',
    '    "webui.datasources_field_user": "用户",',
    '    "webui.datasources_field_password": "密码",',
    '    "webui.datasources_field_service": "服务名",',
    '    "webui.datasources_field_group": "分组",',
    '    "webui.datasources_field_tags": "标签",',
    '    "webui.datasources_field_desc": "描述",',
    '    # ── 规则管理 ──',
    '    "webui.nav_rules": "规则",',
    '    "webui.rules_title": "规则管理",',
    '    "webui.rules_empty": "暂无自定义规则",',
    '    "webui.rules_add": "新增规则",',
    '    "webui.rules_builtin": "内置",',
    '    "webui.rules_custom": "自定义",',
    '    "webui.rules_enabled": "已启用",',
    '    "webui.rules_disabled": "已禁用",',
    '    "webui.rules_severity_high": "高风险",',
    '    "webui.rules_severity_medium": "中风险",',
    '    "webui.rules_severity_low": "低风险",',
    '    "webui.rules_severity_info": "建议",',
    '    "webui.rules_filter_all": "全部",',
    '    "webui.rules_filter_builtin": "内置",',
    '    "webui.rules_filter_custom": "自定义",',
    '    "webui.rules_confirm_delete": "确定删除该自定义规则吗？",',
    '    # ── 通用 ──',
    '    "webui.edition_badge": "社区版",',
    '    "webui.edition_badge_pro": "专业版",',
]

en = [
    '    # ── Datasource Management ──',
    '    "webui.nav_datasources": "Datasources",',
    '    "webui.datasources_title": "Datasource Management",',
    '    "webui.datasources_empty": "No datasources yet",',
    '    "webui.datasources_add": "Add Datasource",',
    '    "webui.datasources_edit": "Edit Datasource",',
    '    "webui.datasources_delete": "Delete",',
    '    "webui.datasources_test": "Test",',
    '    "webui.datasources_testing": "Testing...",',
    '    "webui.datasources_test_ok": "Connected",',
    '    "webui.datasources_test_fail": "Failed",',
    '    "webui.datasources_import": "Import CSV",',
    '    "webui.datasources_export": "Export CSV",',
    '    "webui.datasources_csv_hint": "Supports: name,db_type,host,port,user,password,service_name,group,tags,description",',
    '    "webui.datasources_csv_placeholder": "Paste CSV content...",',
    '    "webui.datasources_field_name": "Name",',
    '    "webui.datasources_field_type": "Type",',
    '    "webui.datasources_field_host": "Host",',
    '    "webui.datasources_field_port": "Port",',
    '    "webui.datasources_field_user": "User",',
    '    "webui.datasources_field_password": "Password",',
    '    "webui.datasources_field_service": "Service Name",',
    '    "webui.datasources_field_group": "Group",',
    '    "webui.datasources_field_tags": "Tags",',
    '    "webui.datasources_field_desc": "Description",',
    '    # ── Rule Management ──',
    '    "webui.nav_rules": "Rules",',
    '    "webui.rules_title": "Rule Management",',
    '    "webui.rules_empty": "No custom rules",',
    '    "webui.rules_add": "Add Rule",',
    '    "webui.rules_builtin": "Built-in",',
    '    "webui.rules_custom": "Custom",',
    '    "webui.rules_enabled": "Enabled",',
    '    "webui.rules_disabled": "Disabled",',
    '    "webui.rules_severity_high": "High Risk",',
    '    "webui.rules_severity_medium": "Medium Risk",',
    '    "webui.rules_severity_low": "Low Risk",',
    '    "webui.rules_severity_info": "Suggestion",',
    '    "webui.rules_filter_all": "All",',
    '    "webui.rules_filter_builtin": "Built-in",',
    '    "webui.rules_filter_custom": "Custom",',
    '    "webui.rules_confirm_delete": "Are you sure you want to delete this rule?",',
    '    # ── General ──',
    '    "webui.edition_badge": "Community",',
    '    "webui.edition_badge_pro": "Pro",',
]

add_keys(os.path.join(base, 'i18n', 'zh.py'), zh)
add_keys(os.path.join(base, 'i18n', 'en.py'), en)

# 验证
from io import StringIO
import sys
errors = []
for fname in ['zh', 'en']:
    try:
        mod = __import__('i18n.' + fname, fromlist=['ZI'])
        print(f'{fname}: OK')
    except SyntaxError as e:
        errors.append((fname, e))

if errors:
    for fname, e in errors:
        print(f'{fname}: SYNTAX ERROR - {e}')
else:
    print('\n所有 i18n 文件语法正确')
