#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""针对性修复 index.html 的剩余问题：
1. 更新 loadDataSources() 加入分组筛选支持
2. 在 showAddDatasourceModal() 中插入分组字段
3. 更新 saveDatasource() 加入 group 字段
4. 更新 editDatasource() 填充分组字段
5. 删除旧函数 loadProInstances / loadProGroups / 占位函数
6. 在 saveDatasource() 后添加新函数 loadGroupChips / filterByGroup / showAddGroupModalFromDatasource
"""

import re

FILE = r'D:\DBCheck\web_templates\index.html'

with open(FILE, 'r', encoding='utf-8') as f:
    lines = f.readlines()

content = ''.join(lines)

# ══════════════════════════════════════════════════
# 1. 替换 loadDataSources() 函数
# ══════════════════════════════════════════════════
old_loadDS_start = '      // ─── Data Sources Page ────────────────────────\n      async function loadDataSources() {'
new_loadDS = '''      // ─── Data Sources Page ────────────────────────
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
            const borderStyle = isActive ? 'border:1px solid var(--accent)' : 'border:1px solid transparent';
            return '<span class="group-chip' + (isActive ? ' active' : '') + '" onclick="filterByGroup(\\'' + g.name + '\\')" style="display:inline-block;padding:4px 12px;border-radius:16px;font-size:12px;cursor:pointer;' + borderStyle + '">' +
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

if old_loadDS_start in content:
    # 找到函数结束位置（下一个 // ─── 或 function 开头）
    start_idx = content.index(old_loadDS_start)
    # 找到函数结束的大括号
    # 找下一个顶级注释或函数定义
    rest = content[start_idx:]
    # 找 }
    brace_count = 0
    in_func = False
    end_idx = None
    lines_rest = rest.split('\n')
    for i, ln in enumerate(lines_rest):
        if 'async function loadDataSources() {' in ln:
            in_func = True
            brace_count = 0
        if in_func:
            brace_count += ln.count('{') - ln.count('}')
            if brace_count <= 0 and '}' in ln and i > 0:
                end_idx = start_idx + sum(len(l)+1 for l in lines_rest[:i+1])
                break
    if end_idx:
        content = content[:start_idx] + new_loadDS + content[end_idx:]
        print('1. loadDataSources() + 新函数: 替换成功')
    else:
        print('1. 未找到 loadDataSources 结束位置')
else:
    print('1. 未找到 loadDataSources 签名')

# ══════════════════════════════════════════════════
# 2. 在数据源模态框中插入分组字段
# ══════════════════════════════════════════════════
old_modal_ds_service = "'<div id=\"ds-service-wrap\" style=\"display:none\"><label style=\"font-size:12px;color:var(--text-muted)\">服务名 (Oracle)</label>' +\n                '<input id=\"ds-service-name\" style=\"width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px\" placeholder=\"ORCL\"></div>' +\n            '</div>'"
new_modal_ds_service = "'<div id=\"ds-service-wrap\" style=\"display:none\"><label style=\"font-size:12px;color:var(--text-muted)\">服务名 (Oracle)</label>' +\n                '<input id=\"ds-service-name\" style=\"width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px\" placeholder=\"ORCL\"></div>' +\n              '<div><label style=\"font-size:12px;color:var(--text-muted)\">分组</label>' +\n                '<input id=\"ds-group\" style=\"width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px\" placeholder=\"default\"></div>' +\n            '</div>'"

if "'<div id=\"ds-service-wrap\"" in content or "'<div id=\\\"ds-service-wrap\\\"" in content:
    # 使用正则表达式匹配
    pattern = r\"'<div id=\\\"ds-service-wrap\\\" style=\\\"display:none\\\"><label style=\\\"font-size:12px;color:var\(--text-muted\)\\\">服务名 \\\(Oracle\)</label>' \+\n                '<input id=\\\"ds-service-name\\\""
    replacement = ...  # 太复杂，换种方式
    print('2. 模态框分组字段: 使用正则方式')
else:
    print('2. 模态框: 需要手动检查')

# 换一种更可靠的方式：直接查找并替换
import re
# 匹配 ds-service-wrap 到 </div>' + '\n            <div id="ds-save-msg"' 之间的内容
pattern2 = r\"(<div id=\"ds-service-wrap\" style=\"display:none\"><label style=\"font-size:12px;color:var\(--text-muted\)\">服务名 \(Oracle\)</label>' \+\n                '<input id=\"ds-service-name\" style=\"width:100%;margin-top:4px;padding:8px 10px;background:var\(--surface2\);border:1px solid var\(--border\);border-radius:8px;color:var\(--text\);font-size:13px\" placeholder=\"ORCL\"></div>' \+)\n            '</div>' \+\"
replacement2 = """'<div id="ds-service-wrap" style="display:none"><label style="font-size:12px;color:var(--text-muted)">服务名 (Oracle)</label>' +
                '<input id="ds-service-name" style="width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px" placeholder="ORCL"></div>' +
              '<div><label style="font-size:12px;color:var(--text-muted)">分组</label>' +
                '<input id="ds-group" style="width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px" placeholder="default"></div>' +
            '</div>' +"""
new_content = re.sub(pattern2, replacement2, content, count=1)
if new_content != content:
    content = new_content
    print('2. 模态框分组字段: 插入成功')
else:
    print('2. 模态框分组字段: 未匹配')

# ══════════════════════════════════════════════════
# 3. 更新 saveDatasource() 加入 group 字段
# ══════════════════════════════════════════════════
old_save = """          const payload = {
            db_type: document.getElementById('ds-db-type').value,
            name: document.getElementById('ds-label').value.trim(),
            host: document.getElementById('ds-host').value.trim(),
            port: parseInt(document.getElementById('ds-port').value) || 3306,
            user: document.getElementById('ds-user').value.trim(),
            password: document.getElementById('ds-password').value || undefined,
          };"""
new_save = """          const payload = {
            db_type: document.getElementById('ds-db-type').value,
            name: document.getElementById('ds-label').value.trim(),
            host: document.getElementById('ds-host').value.trim(),
            port: parseInt(document.getElementById('ds-port').value) || 3306,
            user: document.getElementById('ds-user').value.trim(),
            password: document.getElementById('ds-password').value || undefined,
            group: document.getElementById('ds-group').value.trim() || 'default',
          };"""
if '\n          const payload = {' in content:
    content = content.replace(old_save, new_save)
    print('3. saveDatasource() group 字段: 更新成功')
else:
    print('3. saveDatasource(): payload 未找到')

# ══════════════════════════════════════════════════
# 4. 更新 editDatasource() 填充分组字段
# ══════════════════════════════════════════════════
old_edit_fill = "              // 密码留空，不回显\n              document.getElementById('ds-password').placeholder = '（不修改请留空）';"""
new_edit_fill = """              // 密码留空，不回显
              document.getElementById('ds-password').placeholder = '（不修改请留空）';
              // 填充分组
              const groupEl = document.getElementById('ds-group');
              if (groupEl) groupEl.value = ds.group || 'default';"""
if '// 密码留空，不回显' in content:
    content = content.replace(old_edit_fill, new_edit_fill)
    print('4. editDatasource() 分组填充: 更新成功')
else:
    print('4. editDatasource(): 未找到匹配')

# ══════════════════════════════════════════════════
# 5. 删除旧函数 loadProInstances / loadProGroups
# ══════════════════════════════════════════════════
# 删除 loadProInstances 函数
pattern_del1 = r'\nasync function loadProInstances\(\) \{[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}[^}]+\}'
# 太复杂，直接按行删除
lines = content.split('\n')
new_lines = []
skip_until = None
for i, ln in enumerate(lines):
    if skip_until:
        if skip_until in ln and ln.strip().endswith('}'):
            skip_until = None
        continue
    if 'async function loadProInstances() {' in ln:
        skip_until = '}'  # 跳过整个函数
        continue
    if 'async function loadProGroups() {' in ln:
        skip_until = '}'
        continue
    new_lines.append(ln)
content = '\n'.join(new_lines)
print('5. 旧函数删除: 完成')

# ══════════════════════════════════════════════════
# 6. 删除占位函数
# ══════════════════════════════════════════════════
lines = content.split('\n')
new_lines = []
skip_block = False
for ln in lines:
    if '// 实例操作占位函数' in ln or '// 占位函数' in ln:
        skip_block = True
    if skip_block and ln.strip() == '</script>':
        skip_block = False
        new_lines.append(ln)
        continue
    if skip_block:
        continue
    new_lines.append(ln)
content = '\n'.join(new_lines)
print('6. 占位函数删除: 完成')

# ══════════════════════════════════════════════════
# 7. 添加 showAddGroupModalFromDatasource 函数
# ══════════════════════════════════════════════════
# 在 </script> 前插入
add_fn = """
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
"""
if 'showAddGroupModalFromDatasource' not in content:
    content = content.replace('  </script>', add_fn + '\n  </script>')
    print('7. showAddGroupModalFromDatasource(): 添加成功')
else:
    print('7. showAddGroupModalFromDatasource(): 已存在')

with open(FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print('\n✅ 修复脚本执行完成')
