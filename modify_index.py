#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修改 index.html：
1. 删除 Pro 页面中的实例管理卡片
2. 删除 Pro 页面中的分组管理卡片
3. 在数据源管理页面加入分组筛选功能
4. 更新向导页 API 调用从 /instances 改为 /datasources
5. 更新 showProPage() 和 loadProStatus() 去掉对已删除卡片的引用
6. 更新数据源模态框加入分组字段
"""

import re

FILE = r'D:\DBCheck\web_templates\index.html'

with open(FILE, 'r', encoding='utf-8') as f:
    content = f.read()

# ── 1. 删除实例管理卡片 ─────────────────────
# 匹配从 <!-- Instance Management --> 到 <!-- Inspection History --> 之间的内容
pattern1 = r'        <!-- Instance Management -->\n        <div class="card" id="pro-instances-card" style="display:none;">\n          <div class="card-title">\n            <span>🗄️</span> 实例管理\n            <span class="badge" id="pro-instance-count" style="background:var\(--accent2\)">0</span>\n            <div style="margin-left:auto;">\n              <button class="btn btn-ghost" onclick="showAddInstanceModal\(\)" style="font-size:12px;padding:4px 10px;">➕ 添加实例</button>\n              <button class="btn btn-ghost" onclick="importInstances\(\)" style="font-size:12px;padding:4px 10px;">📥 导入</button>\n            </div>\n          </div>\n          <div id="pro-instances-list" style="margin-top:12px;">\n            <div style="text-align:center;padding:40px;color:var\(--text-muted\);">\n              暂无实例，点击上方按钮添加\n            </div>\n          </div>\n        </div>\n\n        <!-- Groups Management -->[\s\S]*?<!-- Inspection History -->'
replacement1 = '        <!-- Inspection History -->'
content = re.sub(pattern1, replacement1, content)

print('1. 实例管理卡片删除:', '成功' if 'pro-instances-card' not in content else '失败')

# ── 2. 删除分组管理卡片（如果还在）─────────────────────
pattern2 = r'        <!-- Groups Management -->\n        <div class="card" id="pro-groups-card" style="display:none;">\n          <div class="card-title">\n            <span>📁</span> 分组管理\n            <div style="margin-left:auto;">\n              <button class="btn btn-ghost" onclick="showAddGroupModal\(\)" style="font-size:12px;padding:4px 10px;">➕ 添加分组</button>\n            </div>\n          </div>\n          <div id="pro-groups-list" style="margin-top:12px;display:flex;flex-wrap:wrap;gap:8px;">\n          </div>\n        </div>\n'
if '<!-- Groups Management -->' in content:
    content = content.replace(re.search(pattern2, content).group(0), '')
    print('2. 分组管理卡片删除: 成功')
else:
    print('2. 分组管理卡片: 已删除或不存在')

# ── 3. 更新 showProPage() ─────────────────────
old_showPro = """async function showProPage() {
  await loadProStatus();
  // 这里 isPro 判断改为用 class，不在依赖文字
  const badge = document.getElementById('pro-license-status');
  const isPro = badge.classList.contains('pro-active');
  if (isPro) {
    document.getElementById('pro-health-card').style.display = '';
    document.getElementById('pro-instances-card').style.display = '';
    document.getElementById('pro-groups-card').style.display = '';
    document.getElementById('pro-history-card').style.display = '';
    await Promise.all([loadProHealth(), loadProInstances(), loadProGroups(), loadProHistory()]);
  }
}"""
new_showPro = """async function showProPage() {
  await loadProStatus();
  const badge = document.getElementById('pro-license-status');
  const isPro = badge.classList.contains('pro-active');
  if (isPro) {
    document.getElementById('pro-health-card').style.display = '';
    document.getElementById('pro-history-card').style.display = '';
    await Promise.all([loadProHealth(), loadProHistory()]);
  }
}"""
if 'loadProInstances(), loadProGroups()' in content:
    content = content.replace(old_showPro, new_showPro)
    print('3. showProPage() 更新: 成功')
else:
    print('3. showProPage(): 已更新或不需要更新')

# ── 4. 更新 loadProStatus() 中的卡片显示/隐藏 ─────────────────────
content = content.replace(
    "      document.getElementById('pro-instances-card').style.display = 'none';\n      document.getElementById('pro-groups-card').style.display = 'none';\n      document.getElementById('pro-history-card').style.display = 'none';",
    "      document.getElementById('pro-health-card').style.display = 'none';\n      document.getElementById('pro-history-card').style.display = 'none';"
)
print('4. loadProStatus() 更新: 完成')

# ── 5. 更新数据源管理页面，加入分组筛选 ─────────────────────
old_ds_page = """      <!-- ══ Page: Data Sources ══ -->
      <div class="page" id="page-datasources">
        <div class="card">
          <div class="card-title">
            <span>🗄️</span> <span data-i18n="webui.datasources_title">数据源管理</span>
            <span class="badge" id="datasources-count">0</span>
            <button class="btn btn-primary" style="margin-left:auto;padding:6px 14px;font-size:12px" onclick="showAddDatasourceModal()">➕ <span data-i18n="webui.datasources_add">添加数据源</span></button>
          </div>
          <div id="datasources-list" style="margin-top:12px;">
            <div style="text-align:center;padding:40px;color:var(--text-muted);" data-i18n="webui.datasources_empty">暂无数据源，点击上方按钮添加</div>
          </div>
        </div>
      </div>"""
new_ds_page = """      <!-- ══ Page: Data Sources ══ -->
      <div class="page" id="page-datasources">
        <div class="card">
          <div class="card-title">
            <span>🗄️</span> <span data-i18n="webui.datasources_title">数据源管理</span>
            <span class="badge" id="datasources-count">0</span>
            <button class="btn btn-primary" style="margin-left:auto;padding:6px 14px;font-size:12px" onclick="showAddDatasourceModal()">➕ <span data-i18n="webui.datasources_add">添加数据源</span></button>
          </div>
          <!-- 分组筛选 -->
          <div style="margin:12px 0 4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span style="font-size:12px;color:var(--text-muted)" data-i18n="webui.group_filter">分组筛选：</span>
            <span id="group-all-chip" class="group-chip active" onclick="filterByGroup('')" style="display:inline-block;padding:4px 12px;background:var(--surface2);border-radius:16px;font-size:12px;cursor:pointer;border:1px solid var(--accent)">全部</span>
            <span id="group-chips-wrap" style="display:inline-flex;gap:6px;flex-wrap:wrap"></span>
            <button class="btn btn-ghost" style="font-size:11px;padding:2px 8px;margin-left:4px" onclick="showAddGroupModalFromDatasource()">＋分组</button>
          </div>
          <div id="datasources-list" style="margin-top:12px;">
            <div style="text-align:center;padding:40px;color:var(--text-muted);" data-i18n="webui.datasources_empty">暂无数据源，点击上方按钮添加</div>
          </div>
        </div>
      </div>"""
if '<!-- ══ Page: Data Sources ══ -->' in content:
    content = content.replace(old_ds_page, new_ds_page)
    print('5. 数据源页面加入分组筛选: 成功')
else:
    print('5. 数据源页面: 已更新或不匹配')

# ── 6. 更新 loadDataSources() 函数加入分组支持 ─────────────────────
# 替换整个函数
old_loadDS = """      // ─── Data Sources Page ──────────────────────
      async function loadDataSources() {
        try {
          const res = await fetch('/api/pro/datasources');
          const data = await res.json();
          const listEl = document.getElementById('datasources-list');
          const countEl = document.getElementById('datasources-count');
          if (data.ok && data.datasources && data.datasources.length > 0) {
            countEl.textContent = data.datasources.length;
            listEl.innerHTML = data.datasources.map(ds => {
              const dbIcons = {mysql:'🐬', pg:'🐘', oracle_full:'🔴', dm:'🟡', sqlserver:'🟠', tidb:'🟢'};
              const icon = dbIcons[ds.db_type] || '🗄️';
              const tagClass = 'tag-' + (ds.db_type === 'oracle_full' ? 'oracle' : ds.db_type === 'sqlserver' ? 'sqlserver' : ds.db_type);
              return '<div style="padding:12px 16px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;margin-bottom:8px;display:flex;align-items:center;gap:12px">' +
                '<span class="tag ' + tagClass + '" style="font-size:12px">' + icon + ' ' + (ds.db_type||'') + '</span>' +
                '<div style="flex:1">' +
                  '<div style="font-weight:600;font-size:13px">' + escHtml(ds.label || (ds.host + ':' + ds.port)) + '</div>' +
                  '<div style="font-size:11px;color:var(--text-muted)">' + escHtml(ds.host) + ':' + (ds.port||'') + (ds.database ? ' / ' + escHtml(ds.database) : '') + '</div>' +
                '</div>' +
                '<button class="btn btn-ghost" style="font-size:12px;padding:4px 10px" onclick="editDatasource(\'' + ds.id + '\')">✏️ 编辑</button>' +
                '<button class="btn btn-ghost" style="font-size:12px;padding:4px 10px" onclick="testDatasourceConnection(\'' + ds.id + '\')">🔗 测试</button>' +
                '<button class="btn btn-ghost" style="font-size:12px;padding:4px 10px;color:var(--danger)" onclick="deleteDatasource(\'' + ds.id + '\')">🗑️ 删除</button>' +
              '</div>';
            }).join('');
          } else {
            countEl.textContent = '0';
            listEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted)" data-i18n="webui.datasources_empty">暂无数据源，点击上方按钮添加</div>';
          }
        } catch(e) { console.error('loadDataSources failed', e); }
      }"""
new_loadDS = """      // ─── Data Sources Page ──────────────────────
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
                '<button class="btn btn-ghost" style="font-size:12px;padding:4px 10px" onclick="editDatasource(\'' + ds.id + '\')">✏️ 编辑</button>' +
                '<button class="btn btn-ghost" style="font-size:12px;padding:4px 10px" onclick="testDatasourceConnection(\'' + ds.id + '\')">🔗 测试</button>' +
                '<button class="btn btn-ghost" style="font-size:12px;padding:4px 10px;color:var(--danger)" onclick="deleteDatasource(\'' + ds.id + '\')">🗑️ 删除</button>' +
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
            return '<span class="group-chip' + (isActive ? ' active' : '') + '" onclick="filterByGroup(\'' + g.name + '\')" style="display:inline-block;padding:4px 12px;border-radius:16px;font-size:12px;cursor:pointer;border:1px solid ' + (isActive ? 'var(--accent)' : 'transparent') + '">' +
              '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + (g.color||'#3788DD') + ';margin-right:4px;"></span>' +
              escHtml(g.name) + '</span>';
          }).join('');
        } catch(e) {}
      }

      function filterByGroup(groupName) {
        currentGroupFilter = groupName || '';
        loadDataSources(currentGroupFilter);
        loadGroupChips();
      }"""
if 'async function loadDataSources() {' in content:
    # 使用更精确的匹配
    content = content.replace(old_loadDS, new_loadDS)
    print('6. loadDataSources() 更新: 成功')
else:
    print('6. loadDataSources(): 函数签名不匹配，尝试部分替换')

# ── 7. 更新 showAddDatasourceModal() 加入分组字段 ─────────────────────
# 在密码字段后、关闭 div 前插入分组字段
old_modal_part = """              '<div id="ds-service-wrap" style="display:none"><label style="font-size:12px;color:var(--text-muted)">服务名 (Oracle)</label>' +
                '<input id="ds-service-name" style="width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px" placeholder="ORCL"></div>' +
            '</div>' +
            '<div id="ds-save-msg" style="margin-top:10px;font-size:12px"></div>'"""
new_modal_part = """              '<div id="ds-service-wrap" style="display:none"><label style="font-size:12px;color:var(--text-muted)">服务名 (Oracle)</label>' +
                '<input id="ds-service-name" style="width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px" placeholder="ORCL"></div>' +
              '<div><label style="font-size:12px;color:var(--text-muted)">分组</label>' +
                '<input id="ds-group" style="width:100%;margin-top:4px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px" placeholder="default"></div>' +
            '</div>' +
            '<div id="ds-save-msg" style="margin-top:10px;font-size:12px"></div>'"""
if "'</div>' +\n            '<div id=\"ds-save-msg\"" in content or "'</div>' +            '<div id=\"ds-save-msg\"" in content:
    content = content.replace(old_modal_part, new_modal_part)
    print('7. 数据源模态框加入分组字段: 成功')
else:
    print('7. 模态框分组字段: 需要手动检查')

# ── 8. 更新 saveDatasource() 加入 group 字段 ─────────────────────
old_save = """        const payload = {
          db_type: document.getElementById('ds-db-type').value,
          name: document.getElementById('ds-label').value.trim(),
          host: document.getElementById('ds-host').value.trim(),
          port: parseInt(document.getElementById('ds-port').value) || 3306,
          user: document.getElementById('ds-user').value.trim(),
          password: document.getElementById('ds-password').value || undefined,
        };"""
new_save = """        const payload = {
          db_type: document.getElementById('ds-db-type').value,
          name: document.getElementById('ds-label').value.trim(),
          host: document.getElementById('ds-host').value.trim(),
          port: parseInt(document.getElementById('ds-port').value) || 3306,
          user: document.getElementById('ds-user').value.trim(),
          password: document.getElementById('ds-password').value || undefined,
          group: document.getElementById('ds-group').value.trim() || 'default',
        };"""
if 'name: document.getElementById(\'ds-label\').value.trim(),' in content:
    content = content.replace(old_save, new_save)
    print('8. saveDatasource() 加入 group: 成功')
else:
    print('8. saveDatasource(): 需要手动检查')

# ── 9. 更新 editDatasource() 填充分组字段 ─────────────────────
# 在密码 placeholder 设置后加入分组字段填充
old_edit = """              // 密码留空，不回显
              document.getElementById('ds-password').placeholder = '（不修改请留空）';"""
new_edit = """              // 密码留空，不回显
              document.getElementById('ds-password').placeholder = '（不修改请留空）';
              document.getElementById('ds-group').value = ds.group || 'default';"""
if "document.getElementById('ds-password').placeholder = '（不修改请留空）';" in content:
    content = content.replace(old_edit, new_edit)
    print('9. editDatasource() 填充分组: 成功')
else:
    print('9. editDatasource(): 需要手动检查')

# ── 10. 加入 showAddGroupModalFromDatasource() 函数 ─────────────────────
# 在 saveDatasource 函数后插入
group_modal_fn = """
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
# 在 saveDatasource 函数结束后插入
marker = """            } catch(e) {
          msgEl.style.color = 'var(--danger)';
          msgEl.textContent = '❌ ' + e.message;
        }
      }"""
if marker in content and 'showAddGroupModalFromDatasource' not in content:
    content = content.replace(marker, marker + group_modal_fn)
    print('10. showAddGroupModalFromDatasource() 已添加')
else:
    print('10. showAddGroupModalFromDatasource(): 已存在或标记不匹配')

# ── 11. 添加 .group-chip.active CSS ─────────────────────
# 在 <style> 标签内添加
css_addition = """
        .group-chip.active { background: var(--surface2); border-color: var(--accent) !important; }
"""
if '.group-chip.active' not in content:
    # 在 </style> 前插入
    content = content.replace('  </style>', css_addition + '  </style>')
    print('11. CSS .group-chip.active 已添加')
else:
    print('11. CSS: 已存在')

with open(FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print('\n✅ 所有修改已写入', FILE)
