#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""针对性修复 index.html：
1. 替换 loadDataSources 函数体（加入分组支持）
2. 在模态框中插入分组字段
3. 在 saveDatasource 中加入 group 字段
4. 在 editDatasource 中填充分组
5. 删除旧函数 loadProInstances / loadProGroups / 占位函数
6. 在 </script> 前插入缺失的 JS 函数
"""

FILE = r'D:\DBCheck\web_templates\index.html'

with open(FILE, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# ── 辅助：按行号范围提取并替换 ─────────────────────
def replace_lines(lines, start_pat, end_pat, new_lines):
    """找到 start_pat 所在行，一直替换到 end_pat 所在行（不含 end_pat 行）"""
    si = None
    ei = None
    for i, ln in enumerate(lines):
        if start_pat in ln and si is None:
            si = i
        if si is not None and end_pat in ln:
            ei = i
            break
    if si is not None and ei is not None:
        return lines[:si] + new_lines + lines[ei:]
    return lines

# ════════════════════════════════════════════════════
# 1. 替换 loadDataSources 函数
# ════════════════════════════════════════════════════
start_pat = '      // ─── Data Sources Page'
new_func_lines = '''      // ─── Data Sources Page ──────────────────────
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
      }
'''.splitlines(True)

# 找到旧函数的起始和结束位置
si = None
ei = None
brace_count = 0
for i, ln in enumerate(lines):
    if '      // ─── Data Sources Page' in ln:
        si = i
    if si is not None:
        brace_count += ln.count('{') - ln.count('}')
        if brace_count < 0:  # 函数结束
            ei = i + 1
            break
if si is not None and ei is not None:
    lines = lines[:si] + new_func_lines + lines[ei:]
    print(f'1. loadDataSources+新函数: 替换成功 (行 {si+1}-{ei+1})')
else:
    print('1. loadDataSources: 未找到函数范围')

# ════════════════════════════════════════════════════
# 2. 在模态框 ds-service-wrap 后插入分组字段
# ════════════════════════════════════════════════════
insert_lines = None
for i, ln in enumerate(lines):
    # 找 ds-service-wrap 的结束 </div>' + 行
    if "id=\"ds-service-wrap\"" in ln and "display:none" in ln:
        # 找到这个 div 结束的位置
        j = i
        while j < len(lines):
            if "</div>' +" in lines[j] or '<\/div>\' +' in lines[j]:
                # 检查是否是 ds-service-wrap 的结束
                # 在它后面插入分组字段
                insert_at = j + 1
                new_html = "              '<div><label style=\"font-size:12px;color:var(--text-muted)\">分组</label>' +\n                '<input id=\"ds-group\" style=\"width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px\" placeholder=\"default\"></div>' +\n"
                lines.insert(insert_at, new_html)
                print('2. 模态框分组字段: 插入成功')
                break
            j += 1
        break

# ════════════════════════════════════════════════════
# 3. 在 saveDatasource 的 payload 中加入 group
# ════════════════════════════════════════════════════
for i, ln in enumerate(lines):
    if 'password: document.getElementById(\'ds-password\')' in ln or 'password: document.getElementById("ds-password")' in ln:
        # 在这一行后面加一行
        j = i + 1
        while j < len(lines) and '};' not in lines[j]:
            j += 1
        if j < len(lines):
            # 在 }; 前插入 group 行
            indent = '         '
            lines.insert(j, indent + 'group: document.getElementById(\'ds-group\').value.trim() || \'default\',\n')
            print('3. saveDatasource() group 字段: 插入成功')
            break
        break

# ════════════════════════════════════════════════════
# 4. 在 editDatasource 中填充分组字段
# ════════════════════════════════════════════════════
for i, ln in enumerate(lines):
    if 'ds-password\').placeholder = \'（不修改请留空）\'' in ln or 'ds-password").placeholder' in ln:
        j = i + 1
        # 插入分组赋值
        indent = '            '
        lines.insert(j, indent + "const groupEl = document.getElementById('ds-group');\n")
        lines.insert(j+1, indent + "if (groupEl) groupEl.value = ds.group || 'default';\n")
        print('4. editDatasource() 分组填充: 插入成功')
        break

# ════════════════════════════════════════════════════
# 5. 删除旧函数 loadProInstances / loadProGroups
# ════════════════════════════════════════════════════
new_lines = []
i = 0
while i < len(lines):
    ln = lines[i]
    # 跳过 loadProInstances 函数
    if 'async function loadProInstances()' in ln:
        depth = 0
        while i < len(lines):
            depth += lines[i].count('{') - lines[i].count('}')
            i += 1
            if depth <= 0 and '{' in lines[i-1] and '}' in lines[i-1]:
                break
        continue
    # 跳过 loadProGroups 函数
    if 'async function loadProGroups()' in ln:
        depth = 0
        while i < len(lines):
            depth += lines[i].count('{') - lines[i].count('}')
            i += 1
            if depth <= 0 and '{' in lines[i-1] and '}' in lines[i-1]:
                break
        continue
    # 跳过占位函数行
    if '// 实例操作占位函数' in ln or '// 占位函数' in ln:
        while i < len(lines) and '};' not in lines[i] and 'window.' in lines[i]:
            i += 1
        if i < len(lines):
            i += 1  # 跳过最后一个 };
        continue
    if 'window.testInstance = ' in ln or 'window.editInstance = ' in ln or 'window.deleteInstance = ' in ln:
        i += 1
        continue
    if 'window.showAddInstanceModal = ' in ln or 'window.showAddGroupModal = ' in ln:
        i += 1
        continue
    if 'window.loadProInstancesByGroup = ' in ln:
        i += 1
        continue
    new_lines.append(ln)
    i += 1
if len(new_lines) < len(lines):
    lines = new_lines
    print('5. 旧函数/占位函数: 删除成功')
else:
    print('5. 旧函数: 未找到或已删除')

# ════════════════════════════════════════════════════
# 6. 在 </script> 前插入 showAddGroupModalFromDatasource
# ════════════════════════════════════════════════════
new_fn = """
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
for i, ln in enumerate(lines):
    if '</script>' in ln:
        lines.insert(i, new_fn)
        print('6. showAddGroupModalFromDatasource(): 插入成功')
        break

# 写入文件
with open(FILE, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('\n✅ 修复完成，已写入', FILE)
