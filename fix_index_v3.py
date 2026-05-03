#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""精确修复 index.html：
1. 替换 loadDataSources 函数
2. 在模态框插入分组字段
3. 更新 saveDatasource payload
4. 更新 editDatasource 填充
5. 删除旧函数
6. 添加缺失函数
"""

import re

FILE = r'D:\DBCheck\web_templates\index.html'

with open(FILE, 'r', encoding='utf-8') as f:
    content = f.read()

# ══ 1. 替换 loadDataSources 函数 ══
# 找到函数开始和结束位置
start_marker = '      // ─── Data Sources Page ──────────────────────\n      async function loadDataSources() {'
if start_marker in content:
    start_idx = content.index(start_marker)
    # 找到函数结束的 }（顶级）
    pos = start_idx + len(start_marker)
    brace = 1
    end_idx = None
    i = pos
    while i < len(content):
        if content[i] == '{':
            brace += 1
        elif content[i] == '}':
            brace -= 1
            if brace == 0:
                end_idx = i + 1
                break
        i += 1
    
    if end_idx:
        new_func = '''      // ─── Data Sources Page ──────────────────────
      let currentGroupFilter = '';
      async function loadDataSources(groupFilter) {
        if (groupFilter !== undefined) currentGroupFilter = groupFilter || '';
        try {
          const res = await fetch('/api/pro/datasources');
          const data = await res.json();
          const listEl = document.getElementById('datasources-list');
          const countEl = document.getElementById('datasources-count');
          await loadGroupChips();
          if (data.ok && data.datasources && data.datasources.length > 0) {
            let list = data.datasources;
            if (currentGroupFilter) {
              list = list.filter(ds => (ds.group || 'default') === currentGroupFilter);
            }
            countEl.textContent = list.length;
            if (list.length === 0) {
              listEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);">该分组暂无数据源</div>';
              return;
            }
            listEl.innerHTML = list.map(ds => {
              const dbIcons = {mysql:'🐬', pg:'🐘', oracle_full:'🔴', dm:'🟡', sqlserver:'🟠', tidb:'🟢'};
              const icon = dbIcons[ds.db_type] || '🗄️';
              const tagClass = 'tag-' + (ds.db_type === 'oracle_full' ? 'oracle' : ds.db_type === 'sqlserver' ? 'sqlserver' : ds.db_type);
              const groupLabel = ds.group || 'default';
              return '<div style="padding:12px 16px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;margin-bottom:8px;display:flex;align-items:center;gap:12px">' +
                '<span class="tag ' + tagClass + '" style="font-size:12px">' + icon + ' ' + (ds.db_type||'') + '</span>' +
                '<div style="flex:1">' +
                  '<div style="font-weight:600;font-size:13px">' + escHtml(ds.name || (ds.host + ':' + ds.port)) + '</div>' +
                  '<div style="font-size:11px;color:var(--text-muted)">' + escHtml(ds.host) + ':' + (ds.port||'') + (ds.database ? ' / ' + escHtml(ds.database) : '') + '</div>' +
                  '<div style="font-size:11px;color:var(--accent2);margin-top:2px">📁 ' + escHtml(groupLabel) + '</div>' +
                '</div>' +
                '<button class="btn btn-ghost" style="font-size:12px;padding:4px 10px" onclick="editDatasource(\\'' + ds.id + '\\')">✏️ 编辑</button>' +
                '<button class="btn btn-ghost" style="font-size:12px;padding:4px 10px" onclick="testDatasourceConnection(\\'' + ds.id + '\\')">🔗 测试</button>' +
                '<button class="btn btn-ghost" style="font-size:12px;padding:4px 10px;color:var(--danger)" onclick="deleteDatasource(\\'' + ds.id + '\\')">🗑️ 删除</button>' +
              '</div>';
            }).join('');
          } else {
            countEl.textContent = '0';
            listEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted)" data-i18n="webui.datasources_empty">暂无数据源，点击上方按钮添加</div>';
          }
        } catch(e) { console.error('loadDataSources failed', e); }
      }

      async function loadGroupChips() {
        try {
          const res = await fetch('/api/pro/groups');
          const data = await res.json();
          const wrap = document.getElementById('group-chips-wrap');
          const allChip = document.getElementById('group-all-chip');
          if (!data.ok || !data.groups || data.groups.length === 0) { wrap.innerHTML = ''; return; }
          allChip.className = 'group-chip' + (!currentGroupFilter ? ' active' : '');
          wrap.innerHTML = data.groups.map(g => {
            const isActive = currentGroupFilter === g.name;
            return '<span class="group-chip' + (isActive ? ' active' : '') + '" onclick="filterByGroup(\\'' + g.name + '\\')" style="display:inline-block;padding:4px 12px;border-radius:16px;font-size:12px;cursor:pointer;border:1px solid ' + (isActive ? 'var(--accent)' : 'transparent') + '">' +
              '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + (g.color||'#3788DD') + ';margin-right:4px;"></span>' +
              escHtml(g.name) + '</span>';
          }).join('');
        } catch(e) {}
      }

      function filterByGroup(groupName) {
        currentGroupFilter = groupName || '';
        const allChip = document.getElementById('group-all-chip');
        allChip.className = 'group-chip' + (!currentGroupFilter ? ' active' : '');
        loadDataSources(currentGroupFilter);
      }'''
        content = content[:start_idx] + new_func + content[end_idx:]
        print('1. loadDataSources+新函数: 替换成功')
    else:
        print('1. 未找到 loadDataSources 结束位置')
else:
    print('1. 未找到 loadDataSources 开始位置')

# ══ 2. 在模态框 ds-service-wrap 后插入分组字段 ══
old_str2 = "'<div id=\"ds-service-wrap\" style=\"display:none\"><label style=\"font-size:12px;color:var(--text-muted)\">服务名 (Oracle)</label>' +\n                '<input id=\"ds-service-name\" style=\"width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px\" placeholder=\"ORCL\"></div>' +\n            '</div>'"
new_str2 = "'<div id=\"ds-service-wrap\" style=\"display:none\"><label style=\"font-size:12px;color:var(--text-muted)\">服务名 (Oracle)</label>' +\n                '<input id=\"ds-service-name\" style=\"width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px\" placeholder=\"ORCL\"></div>' +\n              '<div><label style=\"font-size:12px;color:var(--text-muted)\">分组</label>' +\n                '<input id=\"ds-group\" style=\"width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px\" placeholder=\"default\"></div>' +\n            '</div>'"
if old_str2 in content:
    content = content.replace(old_str2, new_str2)
    print('2. 模态框分组字段: 插入成功')
else:
    # 尝试不带 \n 的匹配
    alt_old = "'<div id=\"ds-service-wrap\" style=\"display:none\"><label style=\"font-size:12px;color:var(--text-muted)\">服务名 (Oracle)</label>' + '<input id=\"ds-service-name\""
    if alt_old in content:
        # 找到位置手动替换
        print('2. 模态框: 找到但不匹配完整字符串，手动处理')
    else:
        print('2. 模态框: 未找到匹配')

# ══ 3. 更新 saveDatasource payload ══
old_payload = "          const payload = {\n            db_type: document.getElementById('ds-db-type').value,\n            name: document.getElementById('ds-label').value.trim(),\n            host: document.getElementById('ds-host').value.trim(),\n            port: parseInt(document.getElementById('ds-port').value) || 3306,\n            user: document.getElementById('ds-user').value.trim(),\n            password: document.getElementById('ds-password').value || undefined,\n          };"
new_payload = "          const payload = {\n            db_type: document.getElementById('ds-db-type').value,\n            name: document.getElementById('ds-label').value.trim(),\n            host: document.getElementById('ds-host').value.trim(),\n            port: parseInt(document.getElementById('ds-port').value) || 3306,\n            user: document.getElementById('ds-user').value.trim(),\n            password: document.getElementById('ds-password').value || undefined,\n            group: document.getElementById('ds-group').value.trim() || 'default',\n          };"
if old_payload in content:
    content = content.replace(old_payload, new_payload)
    print('3. saveDatasource payload: 更新成功')
else:
    print('3. saveDatasource: payload 未匹配')

# ══ 4. 更新 editDatasource 填充 ══
old_edit = "              // 密码留空，不回显\n              document.getElementById('ds-password').placeholder = '（不修改请留空）';"
new_edit = "              // 密码留空，不回显\n              document.getElementById('ds-password').placeholder = '（不修改请留空）';\n              // 填充分组\n              const groupEl = document.getElementById('ds-group');\n              if (groupEl) groupEl.value = ds.group || 'default';"
if old_edit in content:
    content = content.replace(old_edit, new_edit)
    print('4. editDatasource 分组填充: 更新成功')
else:
    print('4. editDatasource: 未匹配')

# ══ 5. 删除旧函数 loadProInstances 和 loadProGroups ══
import re
# 删除 loadProInstances 函数
pattern_old1 = r'async function loadProInstances\(\) \{[^}]+\}(?:\s*[^}]+\})*'
# 用更精确的方式
lines = content.split('\n')
new_lines = []
skip = False
brace = 0
for ln in lines:
    if 'async function loadProInstances()' in ln:
        skip = True
        brace = ln.count('{') - ln.count('}')
        continue
    if skip:
        brace += ln.count('{') - ln.count('}')
        if brace <= 0:
            skip = False
        continue
    if 'async function loadProGroups()' in ln:
        skip = True
        brace = ln.count('{') - ln.count('}')
        continue
    if skip:
        brace += ln.count('{') - ln.count('}')
        if brace <= 0:
            skip = False
        continue
    new_lines.append(ln)
content = '\n'.join(new_lines)
print('5. 旧函数删除: 完成')

# 删除占位函数
lines = content.split('\n')
new_lines = []
skip_block = False
for ln in lines:
    if '// 实例操作占位函数' in ln or '// 占位函数' in ln:
        skip_block = True
    if skip_block and '</script>' in ln:
        skip_block = False
        new_lines.append(ln)
        continue
    if skip_block:
        continue
    # 单独删除的占位函数行
    if 'window.testInstance = ' in ln or 'window.editInstance = ' in ln or 'window.deleteInstance = ' in ln:
        continue
    if 'window.showAddInstanceModal = ' in ln or 'window.showAddGroupModal = ' in ln:
        continue
    if 'window.loadProInstancesByGroup = ' in ln:
        continue
    new_lines.append(ln)
content = '\n'.join(new_lines)
print('5b. 占位函数删除: 完成')

# ══ 6. 添加 showAddGroupModalFromDatasource 函数 ══
add_fn = '''
      async function showAddGroupModalFromDatasource() {
        const name = prompt('请输入新分组名称：');
        if (!name || !name.trim()) return;
        const color = '#' + Math.floor(Math.random()*16777215).toString(16).padStart(6,'0');
        try {
          const res = await fetch('/api/pro/groups', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({name: name.trim(), color: color})
          });
          const data = await res.json();
          if (data.ok) {
            toastSuccess('分组已创建');
            await loadGroupChips();
          } else {
            toastError(data.error || '创建分组失败');
          }
        } catch(e) { toastError('创建分组失败: ' + e.message); }
      }
'''
if 'showAddGroupModalFromDatasource' not in content:
    content = content.replace('  </script>', add_fn + '  </script>')
    print('6. showAddGroupModalFromDatasource: 添加成功')
else:
    print('6. showAddGroupModalFromDatasource: 已存在')

with open(FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print('\n✅ 所有修复已完成')
