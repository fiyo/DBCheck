/**
 * 巡检配置管理 - 前端逻辑
 * 功能：模板列表、模板编辑、章节管理、SQL 查询管理、导入/导出、拖拽排序
 */

// ==================== 全局状态 ====================
let currentInspectionTemplateId = null;
let currentInspectionChapterId = null;
let inspectionQueryUnsaved = false;  // 查询内联编辑是否有未保存内容
let currentInspectionIsPreset = false;  // 当前编辑的模板是否为预置模板


// ==================== 页面初始化 ====================

/**
 * 显示巡检配置管理页面
 */
function showInspectionConfigPage() {
    // 不再重复隐藏/显示页面 — showPage() 已经处理过了
    const page = document.getElementById('page-inspection-config');
    if (page) page.style.display = 'flex';
    // 不直接用 getI18N()，交给 applyI18N() 统一处理
    // 仅确保页面显示，标题由 showPage() + applyI18N() 负责
    showInspectionTemplateList();
}


/**
 * 显示模板列表页面
 */
async function showInspectionTemplateList() {
    if (currentInspectionChapterId && inspectionQueryUnsaved) {
        const confirmed = await showConfirmDialog('未保存', '当前有未保存的修改，确定要返回列表吗？');
        if (!confirmed) return;
    }
    const listPage = document.getElementById('inspection-template-list-page');
    const editPage = document.getElementById('inspection-template-edit-page');
    if (listPage) listPage.style.display = 'block';
    if (editPage) editPage.style.display = 'none';
    currentInspectionChapterId = null;
    inspectionQueryUnsaved = false;
    currentInspectionIsPreset = false;
    loadInspectionTemplates();
}


// ==================== 模板列表 ====================

async function loadInspectionTemplates() {
    try {
        const res = await fetch('/api/inspection/templates');
        const data = await res.json();
        if (data.success) {
            renderInspectionTemplateList(data.data);
        } else {
            toast(data.message || '加载模板列表失败', 'error');
        }
    } catch (e) {
        toast('加载模板列表失败: ' + e.message, 'error');
    }
}

function renderInspectionTemplateList(templates) {
    const container = document.getElementById('inspection-template-list');
    if (!container) return;
    if (!templates || templates.length === 0) {
        container.innerHTML = '<div class="empty-state">暂无巡检模板，请点击"创建模板"按钮添加。</div>';
        return;
    }
    let html = '';
    templates.forEach(t => {
        const dbTypeMap = {
            'mysql': 'MySQL', 'postgresql': 'PostgreSQL', 'oracle': 'Oracle',
            'sqlserver': 'SQL Server', 'dm8': 'DM8 达梦', 'tidb': 'TiDB', 'ivorysql': 'IvorySQL'
        };
        const dbTypeIconMap = {
            'mysql': '🐬', 'postgresql': '🦴', 'oracle': '🔶',
            'sqlserver': '🗄️', 'dm8': '💎', 'tidb': '🌊', 'ivorysql': '🐘'
        };
        const dbTypeLabel = dbTypeMap[t.db_type] || t.db_type;
        const dbTypeIcon = dbTypeIconMap[t.db_type] || '📋';
        const isDefault = t.is_default;
        const isPreset = t.is_preset == 1;
        const presetLabel = isPreset ? '<span class="card-tag card-tag--preset">预置</span>' : '';
        const defaultLabel = isDefault ? '<span class="card-tag card-tag--default">默认</span>' : '';
        const versionLabel = t.version && t.version !== 'v1' ? `<span class="card-version">${escapeHtml(t.version)}</span>` : '';
        html += `
        <div class="inspection-template-card" data-preset="${isPreset ? '1' : '0'}" onclick="editInspectionTemplate(${t.id})">
            <div class="card-accent-bar"></div>
            <div class="card-content">
                <div class="card-top">
                    <div class="card-title-group">
                        <span class="card-icon">${dbTypeIcon}</span>
                        <h3>${escapeHtml(t.template_name)}</h3>
                        ${versionLabel}
                    </div>
                    <div class="card-tags">${presetLabel}${defaultLabel}</div>
                </div>
                ${t.description ? `<p class="card-desc">${escapeHtml(t.description)}</p>` : ''}
                <div class="card-stats">
                    <span class="stat-item"><span class="stat-num">${t.chapter_count || 0}</span> 章节</span>
                    <span class="stat-dot"></span>
                    <span class="stat-item"><span class="stat-num">${t.query_count || 0}</span> 查询</span>
                    <span class="stat-dot"></span>
                    <span class="stat-item stat-type">${dbTypeLabel}</span>
                </div>
            </div>
            <div class="card-actions">
                <button class="card-action-btn card-action-btn--export" onclick="event.stopPropagation(); exportInspectionTemplate(${t.id})">导出</button>
                ${!isPreset ? `<button class="card-action-btn card-action-btn--delete" onclick="event.stopPropagation(); deleteInspectionTemplate(${t.id}, '${escapeHtml(t.template_name)}')">删除</button>` : ''}
            </div>
        </div>`;
    });
    container.innerHTML = html;
}


// ==================== 模板编辑 ====================

function editInspectionTemplate(templateId) {
    const listPage = document.getElementById('inspection-template-list-page');
    const editPage = document.getElementById('inspection-template-edit-page');
    if (listPage) listPage.style.display = 'none';
    if (editPage) editPage.style.display = 'block';
    currentInspectionTemplateId = templateId;
    if (templateId) {
        loadInspectionTemplateDetail(templateId);
    } else {
        clearInspectionTemplateEditForm();
    }
}

async function loadInspectionTemplateDetail(templateId) {
    try {
        const res = await fetch(`/api/inspection/templates/${templateId}`);
        const data = await res.json();
        if (data.success) {
            fillInspectionTemplateEditForm(data.data);
            loadInspectionChapters(templateId);
        } else {
            toast(data.message || '加载模板详情失败', 'error');
        }
    } catch (e) {
        toast('加载模板详情失败: ' + e.message, 'error');
    }
}

function fillInspectionTemplateEditForm(template) {
    const dbTypeEl = document.getElementById('inspection-edit-db-type');
    const nameEl = document.getElementById('inspection-edit-name');
    const versionEl = document.getElementById('inspection-edit-version');
    const descEl = document.getElementById('inspection-edit-desc');
    const defaultEl = document.getElementById('inspection-edit-default');
    currentInspectionIsPreset = template.is_preset == 1;
    if (dbTypeEl) dbTypeEl.value = template.db_type || '';
    if (nameEl) {
        nameEl.value = template.template_name || '';
        nameEl.readOnly = currentInspectionIsPreset;
        nameEl.disabled = currentInspectionIsPreset;
    }
    if (versionEl) {
        versionEl.value = template.version || 'v1';
        versionEl.readOnly = currentInspectionIsPreset;
        versionEl.disabled = currentInspectionIsPreset;
    }
    if (descEl) descEl.value = template.description || '';
    if (defaultEl) defaultEl.checked = template.is_default == 1;
}

function clearInspectionTemplateEditForm() {
    const dbTypeEl = document.getElementById('inspection-edit-db-type');
    const nameEl = document.getElementById('inspection-edit-name');
    const versionEl = document.getElementById('inspection-edit-version');
    const descEl = document.getElementById('inspection-edit-desc');
    const defaultEl = document.getElementById('inspection-edit-default');
    if (dbTypeEl) dbTypeEl.value = '';
    if (nameEl) { nameEl.value = ''; nameEl.readOnly = false; nameEl.disabled = false; }
    if (versionEl) { versionEl.value = 'v1'; versionEl.readOnly = false; versionEl.disabled = false; }
    if (descEl) descEl.value = '';
    if (defaultEl) defaultEl.checked = false;
    currentInspectionIsPreset = false;
    const chapterList = document.getElementById('inspection-chapter-list');
    if (chapterList) chapterList.innerHTML = '<div class="empty-state">暂无章节，请点击顶部"添加章节"按钮添加。</div>';
    clearInspectionChapterDetail();
}

async function saveInspectionTemplate() {
    const dbTypeEl = document.getElementById('inspection-edit-db-type');
    const nameEl = document.getElementById('inspection-edit-name');
    const versionEl = document.getElementById('inspection-edit-version');
    const descEl = document.getElementById('inspection-edit-desc');
    const defaultEl = document.getElementById('inspection-edit-default');
    const dbType = dbTypeEl ? dbTypeEl.value : '';
    const templateName = nameEl ? nameEl.value.trim() : '';
    const version = versionEl ? versionEl.value.trim() : 'v1';
    const description = descEl ? descEl.value.trim() : '';
    const isDefault = defaultEl ? (defaultEl.checked ? 1 : 0) : 0;
    if (!dbType || !templateName) {
        toast('数据库类型和模板名称是必填项', 'error');
        return;
    }
    try {
        let res, data;
        if (currentInspectionTemplateId) {
            res = await fetch(`/api/inspection/templates/${currentInspectionTemplateId}`, {
                method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({db_type: dbType, template_name: templateName, version: version, description: description, is_default: isDefault})
            });
        } else {
            res = await fetch('/api/inspection/templates', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({db_type: dbType, template_name: templateName, version: version, description: description, is_default: isDefault})
            });
        }
        data = await res.json();
        if (data.success) {
            toast('模板保存成功', 'success');
            if (!currentInspectionTemplateId && data.data && data.data.id) {
                currentInspectionTemplateId = data.data.id;
                loadInspectionChapters(currentInspectionTemplateId);
            }
        } else {
            toast(data.message || '保存模板失败', 'error');
        }
    } catch (e) {
        toast('保存模板失败: ' + e.message, 'error');
    }
}

async function deleteInspectionTemplate(templateId, templateName) {
    const confirmed = await showConfirmDialog('确认删除', `确定要删除模板"${templateName}"吗？此操作不可撤销。`);
    if (!confirmed) return;
    try {
        const res = await fetch(`/api/inspection/templates/${templateId}`, {method: 'DELETE'});
        const data = await res.json();
        if (data.success) {
            toast('模板删除成功', 'success');
            showInspectionTemplateList();
        } else {
            toast(data.message || '删除模板失败', 'error');
        }
    } catch (e) {
        toast('删除模板失败: ' + e.message, 'error');
    }
}


// ==================== 章节管理 ====================

async function loadInspectionChapters(templateId) {
    try {
        const res = await fetch(`/api/inspection/templates/${templateId}/chapters`);
        const data = await res.json();
        if (data.success) {
            renderInspectionChapters(data.data);
        } else {
            toast(data.message || '加载章节列表失败', 'error');
        }
    } catch (e) {
        toast('加载章节列表失败: ' + e.message, 'error');
    }
}

function renderInspectionChapters(chapters) {
    const container = document.getElementById('inspection-chapter-list');
    if (!container) return;
    if (!chapters || chapters.length === 0) {
        container.innerHTML = '<div class="empty-state">暂无章节，请点击顶部"添加章节"按钮添加。</div>';
        return;
    }
    let html = '<ul class="chapter-sortable">';
    chapters.forEach((ch, idx) => {
        html += `
        <li class="chapter-item" data-chapter-id="${ch.id}" onclick="editInspectionChapter(${ch.id})">
            <div class="chapter-info">
                <span class="chapter-number">${ch.chapter_number}</span>
                <div class="chapter-text">
                    <span class="chapter-title">${escapeHtml(ch.chapter_title_zh)}</span>
                    ${ch.chapter_title_en ? `<span class="chapter-title-en">${escapeHtml(ch.chapter_title_en)}</span>` : ''}
                </div>
            </div>
            <span class="chapter-query-count">${ch.query_count || 0}</span>
            <div class="chapter-item-actions">
                <button class="btn-delete-x" title="删除" onclick="event.stopPropagation(); deleteInspectionChapter(${ch.id}, '${escapeHtml(ch.chapter_title_zh)}')">✕</button>
            </div>
        </li>`;
    });
    html += '</ul>';
    container.innerHTML = html;

    setTimeout(function() {
        const listEl = container.querySelector('.chapter-sortable');
        if (listEl && window.Sortable) {
            try {
                window._inspectionChapterSortable = new Sortable(listEl, {
                    animation: 150,
                    ghostClass: 'chapter-sortable-ghost',
                    onEnd: function() {
                        const items = listEl.querySelectorAll('[data-chapter-id]');
                        const ids = Array.from(items).map(el => parseInt(el.getAttribute('data-chapter-id')));
                        fetch('/api/inspection/chapters/reorder', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({template_id: currentInspectionTemplateId, chapter_ids: ids})
                        }).then(r => r.json()).then(d => {
                            if (!d.success) toast('排序保存失败', 'error');
                        }).catch(e => toast('排序保存失败: ' + e.message, 'error'));
                    }
                });
            } catch(e) { /* ignore */ }
        }
    }, 100);
}

function addInspectionChapter() {
    if (!currentInspectionTemplateId) {
        toast('请先保存模板', 'error');
        return;
    }
    showInspectionChapterEditForm(null);
}

async function editInspectionChapter(chapterId) {
    if (inspectionQueryUnsaved) {
        const confirmed = await showConfirmDialog('未保存', '当前有未保存的修改，确定要切换吗？');
        if (!confirmed) return;
    }
    try {
        const res = await fetch(`/api/inspection/chapters/${chapterId}`);
        const data = await res.json();
        if (data.success) {
            showInspectionChapterEditForm(data.data);
        } else {
            toast(data.message || '加载章节详情失败', 'error');
        }
    } catch (e) {
        toast('加载章节详情失败: ' + e.message, 'error');
    }
}

function showInspectionChapterEditForm(chapter) {
    const detailContainer = document.getElementById('inspection-chapter-detail');
    if (!detailContainer) return;
    if (chapter) {
        currentInspectionChapterId = chapter.id;
        detailContainer.innerHTML = `
            <div class="chapter-edit-form">
                <div class="form-group"><label>章节序号</label><input type="number" id="inspection-chapter-number" class="form-input" value="${chapter.chapter_number || ''}" /></div>
                <div class="form-group"><label>章节标题（中文）</label><input type="text" id="inspection-chapter-title-zh" class="form-input" value="${escapeHtml(chapter.chapter_title_zh || '')}" /></div>
                <div class="form-group"><label>章节标题（英文）</label><input type="text" id="inspection-chapter-title-en" class="form-input" value="${escapeHtml(chapter.chapter_title_en || '')}" /></div>
                <div class="form-group"><label>章节描述</label><textarea id="inspection-chapter-desc" class="form-textarea">${escapeHtml(chapter.description || '')}</textarea></div>
                <div class="form-group"><label><input type="checkbox" id="inspection-chapter-enabled" ${chapter.enabled != 0 ? 'checked' : ''} /> 启用</label></div>
                <div class="form-actions"><button class="btn btn-primary btn-sm" onclick="saveInspectionChapter()">保存章节</button></div>
            </div>
            <div id="inspection-query-list"></div>`;
        loadInspectionQueries(chapter.id);
    } else {
        currentInspectionChapterId = null;
        detailContainer.innerHTML = `
            <div class="chapter-edit-form">
                <div class="form-group"><label>章节序号</label><input type="number" id="inspection-chapter-number" class="form-input" value="" /></div>
                <div class="form-group"><label>章节标题（中文）</label><input type="text" id="inspection-chapter-title-zh" class="form-input" value="" /></div>
                <div class="form-group"><label>章节标题（英文）</label><input type="text" id="inspection-chapter-title-en" class="form-input" value="" /></div>
                <div class="form-group"><label>章节描述</label><textarea id="inspection-chapter-desc" class="form-textarea"></textarea></div>
                <div class="form-group"><label><input type="checkbox" id="inspection-chapter-enabled" checked /> 启用</label></div>
                <div class="form-actions"><button class="btn btn-primary btn-sm" onclick="saveInspectionChapter()">保存章节</button></div>
            </div>`;
    }
}

async function saveInspectionChapter() {
    const numberEl = document.getElementById('inspection-chapter-number');
    const titleZhEl = document.getElementById('inspection-chapter-title-zh');
    const titleEnEl = document.getElementById('inspection-chapter-title-en');
    const descEl = document.getElementById('inspection-chapter-desc');
    const enabledEl = document.getElementById('inspection-chapter-enabled');
    const chapterNumber = numberEl ? numberEl.value : '';
    const titleZh = titleZhEl ? titleZhEl.value.trim() : '';
    const titleEn = titleEnEl ? titleEnEl.value.trim() : '';
    const description = descEl ? descEl.value.trim() : '';
    const enabled = enabledEl ? (enabledEl.checked ? 1 : 0) : 1;
    if (!chapterNumber || !titleZh) {
        toast('章节序号和章节标题（中文）是必填项', 'error');
        return;
    }
    if (!currentInspectionTemplateId) {
        toast('请先保存模板', 'error');
        return;
    }
    try {
        let res, data;
        if (currentInspectionChapterId) {
            res = await fetch(`/api/inspection/chapters/${currentInspectionChapterId}`, {
                method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({chapter_number: parseInt(chapterNumber), chapter_title_zh: titleZh, chapter_title_en: titleEn, description: description, enabled: enabled})
            });
        } else {
            res = await fetch(`/api/inspection/templates/${currentInspectionTemplateId}/chapters`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({chapter_number: parseInt(chapterNumber), chapter_title_zh: titleZh, chapter_title_en: titleEn, description: description, enabled: enabled})
            });
        }
        data = await res.json();
        if (data.success) {
            toast('章节保存成功', 'success');
            loadInspectionChapters(currentInspectionTemplateId);
            if (!currentInspectionChapterId && data.data && data.data.id) {
                currentInspectionChapterId = data.data.id;
                loadInspectionQueries(currentInspectionChapterId);
            }
        } else {
            toast(data.message || '保存章节失败', 'error');
        }
    } catch (e) {
        toast('保存章节失败: ' + e.message, 'error');
    }
}

async function deleteInspectionChapter(chapterId, chapterTitle) {
    const confirmed = await showConfirmDialog('确认删除', `确定要删除章节"${chapterTitle}"吗？相关的 SQL 查询也会被删除。`);
    if (!confirmed) return;
    try {
        const res = await fetch(`/api/inspection/chapters/${chapterId}`, {method: 'DELETE'});
        const data = await res.json();
        if (data.success) {
            toast('章节删除成功', 'success');
            loadInspectionChapters(currentInspectionTemplateId);
            clearInspectionChapterDetail();
        } else {
            toast(data.message || '删除章节失败', 'error');
        }
    } catch (e) {
        toast('删除章节失败: ' + e.message, 'error');
    }
}

function clearInspectionChapterDetail() {
    const detailContainer = document.getElementById('inspection-chapter-detail');
    if (detailContainer) {
        detailContainer.innerHTML = '<div class="empty-state">请在左侧选择章节，或点击顶部"添加章节"创建新章节。</div>';
    }
    const queryList = document.getElementById('inspection-query-list');
    if (queryList) queryList.innerHTML = '';
    currentInspectionChapterId = null;
    inspectionQueryUnsaved = false;
}


// ==================== SQL 查询管理（内联编辑） ====================

async function loadInspectionQueries(chapterId) {
    try {
        const res = await fetch(`/api/inspection/chapters/${chapterId}/queries`);
        const data = await res.json();
        if (data.success) {
            renderInspectionQueries(data.data);
        } else {
            toast(data.message || '加载查询列表失败', 'error');
        }
    } catch (e) {
        toast('加载查询列表失败: ' + e.message, 'error');
    }
}

function renderInspectionQueries(queries) {
    const container = document.getElementById('inspection-query-list');
    if (!container) return;
    if (!queries || queries.length === 0) {
        container.innerHTML = '<div class="empty-state">暂无 SQL 查询，请点击"添加查询"按钮添加。</div>';
        return;
    }
    let html = '<h4>SQL 查询列表</h4>';
    html += '<button class="btn btn-xs btn-primary" onclick="addInspectionQuery()">添加查询</button>';
    html += '<ul class="query-list">';
    queries.forEach(q => {
        html += `
        <li class="query-item" data-query-id="${q.id}">
            <div class="query-item-header">
                <span class="query-key">${escapeHtml(q.query_key)}</span>
                <span class="query-enabled">${q.enabled ? '✅' : '❌'}</span>
                <div class="query-item-actions">
                    <button class="btn btn-xs btn-ghost" onclick="editInspectionQueryInline(${q.id})">编辑</button>
                    <button class="btn btn-xs btn-danger" onclick="deleteInspectionQuery(${q.id}, '${escapeHtml(q.query_key)}')">删除</button>
                </div>
            </div>
            <div class="query-sql-preview">${escapeHtmlForTemplate(q.query_sql.substring(0, 100))}${q.query_sql.length > 100 ? '...' : ''}</div>
            <div class="query-inline-edit" id="query-edit-${q.id}" style="display:none;"></div>
        </li>`;
    });
    html += '</ul>';
    container.innerHTML = html;
}

function addInspectionQuery() {
    if (!currentInspectionChapterId) {
        toast('请先选择一个章节', 'error');
        return;
    }
    if (inspectionQueryUnsaved) {
        showConfirmDialog('未保存', '当前有未保存的修改，确定要放弃吗？').then(confirmed => {
            if (confirmed) {
                inspectionQueryUnsaved = false;
                showInlineQueryForm(null);
            }
        });
        return;
    }
    showInlineQueryForm(null);
}

async function editInspectionQueryInline(queryId) {
    if (inspectionQueryUnsaved) {
        const confirmed = await showConfirmDialog('未保存', '当前有未保存的修改，确定要放弃吗？');
        if (!confirmed) return;
    }
    document.querySelectorAll('.query-inline-edit').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.query-item-editing').forEach(el => el.classList.remove('query-item-editing'));
    const editContainer = document.getElementById('query-edit-' + queryId);
    if (!editContainer) return;
    try {
        const res = await fetch(`/api/inspection/queries/${queryId}`);
        const data = await res.json();
        if (data.success) {
            renderQueryInlineEditForm(data.data, editContainer, false);
            editContainer.style.display = 'block';
            const li = editContainer.closest('.query-item');
            if (li) li.classList.add('query-item-editing');
            inspectionQueryUnsaved = false;
        } else {
            toast(data.message || '加载查询详情失败', 'error');
        }
    } catch (e) {
        toast('加载查询详情失败: ' + e.message, 'error');
    }
}

function renderQueryInlineEditForm(query, container, isNew) {
    const q = query || {};
    const key    = q.query_key    || '';
    const sql    = q.query_sql    || '';
    const descZh = q.query_description_zh || '';
    const descEn = q.query_description_en || '';
    const enabled = q.enabled !== 0;
    let html = '<div class="inline-edit-form">';
    html += `<div class="form-group"><label>查询键名</label><input type="text" class="form-input inline-q-key" value="${escapeHtml(key)}" oninput="inspectionQueryUnsaved=true" /></div>`;
    html += `<div class="form-group"><label>SQL 语句</label><textarea class="form-textarea inline-q-sql" style="height:100px;font-family:monospace;" oninput="inspectionQueryUnsaved=true">${escapeHtmlForTemplate(sql)}</textarea></div>`;
    html += `<div class="form-group"><label>描述（中文）</label><input type="text" class="form-input inline-q-desc-zh" value="${escapeHtml(descZh)}" oninput="inspectionQueryUnsaved=true" /></div>`;
    html += `<div class="form-group"><label>描述（英文）</label><input type="text" class="form-input inline-q-desc-en" value="${escapeHtml(descEn)}" oninput="inspectionQueryUnsaved=true" /></div>`;
    html += `<div class="form-group"><label><input type="checkbox" class="inline-q-enabled" ${enabled ? 'checked' : ''} onchange="inspectionQueryUnsaved=true" /> 启用</label></div>`;
    html += '<div class="form-actions">';
    html += `  <button class="btn btn-primary btn-sm" onclick="saveInspectionQueryInline(${q.id || 'null'}, ${isNew})">保存</button>`;
    html += `  <button class="btn btn-sm" onclick="cancelInspectionQueryInline(${q.id || 'null'})">取消</button>`;
    html += '</div></div>';
    container.innerHTML = html;
    setTimeout(function() {
        const sqlEl = container.querySelector('.inline-q-sql');
        if (sqlEl && window.CodeMirror) {
            try {
                const editor = window.CodeMirror.fromTextArea(sqlEl, {
                    mode: 'text/x-sql', theme: 'dracula',
                    lineNumbers: true, lineWrapping: true,
                    indentWithTabs: false, indentUnit: 4
                });
                editor.setSize('100%', '100px');
                editor.on('change', function() { inspectionQueryUnsaved = true; });
                container._codeMirror = editor;
            } catch (e) { /* ignore */ }
        }
    }, 100);
}

function showInlineQueryForm(query) {
    const container = document.getElementById('inspection-query-list');
    if (!container) return;
    const isNew = query === null;
    const q = query || {};
    const key    = isNew ? '' : (q.query_key    || '');
    const sql    = isNew ? '' : (q.query_sql    || '');
    const descZh = isNew ? '' : (q.query_description_zh || '');
    const descEn = isNew ? '' : (q.query_description_en || '');
    const enabled = isNew ? true : (q.enabled !== 0);
    container.innerHTML = '<div class="inline-edit-form">'
        + `<div class="form-group"><label>查询键名</label><input type="text" class="form-input inline-q-key" value="${escapeHtml(key)}" oninput="inspectionQueryUnsaved=true" /></div>`
        + `<div class="form-group"><label>SQL 语句</label><textarea class="form-textarea inline-q-sql" style="height:100px;font-family:monospace;" oninput="inspectionQueryUnsaved=true">${escapeHtmlForTemplate(sql)}</textarea></div>`
        + `<div class="form-group"><label>描述（中文）</label><input type="text" class="form-input inline-q-desc-zh" value="${escapeHtml(descZh)}" oninput="inspectionQueryUnsaved=true" /></div>`
        + `<div class="form-group"><label>描述（英文）</label><input type="text" class="form-input inline-q-desc-en" value="${escapeHtml(descEn)}" oninput="inspectionQueryUnsaved=true" /></div>`
        + `<div class="form-group"><label><input type="checkbox" class="inline-q-enabled" ${enabled ? 'checked' : ''} onchange="inspectionQueryUnsaved=true" /> 启用</label></div>`
        + '<div class="form-actions">'
        + `  <button class="btn btn-primary btn-sm" onclick="saveInspectionQueryInline('${isNew ? 'null' : q.id}', ${isNew})">保存</button>`
        + `  <button class="btn btn-sm" onclick="cancelInspectionQueryInline('${isNew ? 'null' : q.id}')">取消</button>`
        + '</div></div>';
    const formDiv = container.querySelector('.inline-edit-form');
    if (formDiv) {
        setTimeout(function() {
            const sqlEl = formDiv.querySelector('.inline-q-sql');
            if (sqlEl && window.CodeMirror) {
                try {
                    const editor = window.CodeMirror.fromTextArea(sqlEl, {
                        mode: 'text/x-sql', theme: 'dracula',
                        lineNumbers: true, lineWrapping: true,
                        indentWithTabs: false, indentUnit: 4
                    });
                    editor.setSize('100%', '100px');
                    editor.on('change', function() { inspectionQueryUnsaved = true; });
                    formDiv._codeMirror = editor;
                } catch (e) { /* ignore */ }
            }
        }, 100);
    }
    inspectionQueryUnsaved = false;
}

async function saveInspectionQueryInline(queryId, isNew) {
    const keyEl    = document.querySelector('.inline-q-key');
    const enabledEl = document.querySelector('.inline-q-enabled');
    const descZhEl = document.querySelector('.inline-q-desc-zh');
    const descEnEl = document.querySelector('.inline-q-desc-en');
    const cm = document.querySelector('.inline-edit-form')?._codeMirror;
    const sqlEl     = document.querySelector('.inline-q-sql');
    const queryKey   = keyEl    ? keyEl.value.trim()    : '';
    const querySql   = cm       ? cm.getValue().trim()   : (sqlEl ? sqlEl.value.trim() : '');
    const descZh     = descZhEl ? descZhEl.value.trim()  : '';
    const descEn     = descEnEl ? descEnEl.value.trim()  : '';
    const enabled     = enabledEl ? (enabledEl.checked ? 1 : 0) : 1;
    if (!queryKey || !querySql) {
        toast('查询键名和 SQL 语句是必填项', 'error');
        return;
    }
    if (!currentInspectionChapterId) {
        toast('章节 ID 丢失，请重新选择章节', 'error');
        return;
    }
    try {
        let res, data;
        if (!isNew && queryId && queryId !== 'null') {
            res = await fetch(`/api/inspection/queries/${queryId}`, {
                method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({query_key: queryKey, query_sql: querySql, query_description_zh: descZh, query_description_en: descEn, enabled: enabled})
            });
        } else {
            res = await fetch(`/api/inspection/chapters/${currentInspectionChapterId}/queries`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({query_key: queryKey, query_sql: querySql, query_description_zh: descZh, query_description_en: descEn, enabled: enabled})
            });
        }
        data = await res.json();
        if (data.success) {
            toast('查询保存成功', 'success');
            inspectionQueryUnsaved = false;
            loadInspectionQueries(currentInspectionChapterId);
        } else {
            toast(data.message || '保存查询失败', 'error');
        }
    } catch (e) {
        toast('保存查询失败: ' + e.message, 'error');
    }
}

function cancelInspectionQueryInline(queryId) {
    if (inspectionQueryUnsaved) {
        showConfirmDialog('未保存', '当前有未保存的修改，确定要放弃吗？').then(confirmed => {
            if (confirmed) {
                inspectionQueryUnsaved = false;
                loadInspectionQueries(currentInspectionChapterId);
            }
        });
        return;
    }
    loadInspectionQueries(currentInspectionChapterId);
}

async function deleteInspectionQuery(queryId, queryKey) {
    const confirmed = await showConfirmDialog('确认删除', `确定要删除查询"${queryKey}"吗？`);
    if (!confirmed) return;
    try {
        const res = await fetch(`/api/inspection/queries/${queryId}`, {method: 'DELETE'});
        const data = await res.json();
        if (data.success) {
            toast('查询删除成功', 'success');
            loadInspectionQueries(currentInspectionChapterId);
        } else {
            toast(data.message || '删除查询失败', 'error');
        }
    } catch (e) {
        toast('删除查询失败: ' + e.message, 'error');
    }
}


// ==================== 导入/导出 ====================

async function exportInspectionTemplate(templateId) {
    try {
        const res = await fetch(`/api/inspection/templates/${templateId}/export`);
        const data = await res.json();
        if (data.success) {
            const jsonStr = JSON.stringify(data.data, null, 2);
            const blob = new Blob([jsonStr], {type: 'application/json'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `inspection_template_${templateId}.json`;
            a.click();
            URL.revokeObjectURL(url);
            toast('模板导出成功', 'success');
        } else {
            toast(data.message || '导出模板失败', 'error');
        }
    } catch (e) {
        toast('导出模板失败: ' + e.message, 'error');
    }
}

function importInspectionTemplate() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async function() {
        const file = input.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = async function(e) {
            try {
                const templateConfig = JSON.parse(e.target.result);
                const res = await fetch('/api/inspection/templates/import', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({template_config: templateConfig, overwrite: false})
                });
                const data = await res.json();
                if (data.success) {
                    toast('模板导入成功', 'success');
                    loadInspectionTemplates();
                } else {
                    toast(data.message || '导入模板失败', 'error');
                }
            } catch (e) {
                toast('导入模板失败: ' + e.message, 'error');
            }
        };
        reader.readAsText(file);
    };
    input.click();
}


// ==================== 工具函数 ====================

function getI18N(key) {
    return (window.I18N && window.I18N[key] !== undefined) ? window.I18N[key] : undefined;
}

function escapeHtml(str) {
    if (!str) return '';
    const map = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
    return str.replace(/[&<>"']/g, function(m) { return map[m]; });
}

// 安全转义：HTML 转义 + 模板字符串 $ 转义（防止 ${var} 被 JS 插值）
function escapeHtmlForTemplate(str) {
    if (!str) return '';
    const map = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
    return str.replace(/[&<>"']/g, function(m) { return map[m]; }).replace(/\$/g, '$$');
}


// ==================== 初始化 ====================
window.showInspectionConfigPage = showInspectionConfigPage;
window.showInspectionTemplateList = showInspectionTemplateList;
window.editInspectionTemplate = editInspectionTemplate;
window.saveInspectionTemplate = saveInspectionTemplate;
window.deleteInspectionTemplate = deleteInspectionTemplate;
window.addInspectionChapter = addInspectionChapter;
window.editInspectionChapter = editInspectionChapter;
window.saveInspectionChapter = saveInspectionChapter;
window.deleteInspectionChapter = deleteInspectionChapter;
window.addInspectionQuery = addInspectionQuery;
window.editInspectionQueryInline = editInspectionQueryInline;
window.deleteInspectionQuery = deleteInspectionQuery;
window.exportInspectionTemplate = exportInspectionTemplate;
window.importInspectionTemplate = importInspectionTemplate;
