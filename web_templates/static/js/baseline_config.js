// DBCheck - 基线配置管理前端逻辑
// 文件: static/js/baseline_config.js
// 所有函数均为全局函数，供 index.html 中的 onclick 调用

// ========== 加载基线配置列表 ==========
function loadBaselines(dbType = '') {
    let url = '/api/inspection/baselines';
    if (dbType) {
        url += `?db_type=${dbType}`;
    }

    const tbody = document.getElementById('baseline-table-body');
    if (tbody) tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:32px">加载中...</td></tr>';

    fetch(url)
        .then(r => r.json())
        .then(response => {
            if (response.success) {
                renderBaselineTable(response.data);
            } else {
                if (tbody) tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--danger)">加载失败: ${escapeHtml(response.message || '')}</td></tr>`;
            }
        })
        .catch(err => {
            if (tbody) tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--danger)">请求失败: ${escapeHtml(err.message)}</td></tr>`;
        });
}

// ========== 按数据库类型筛选 ==========
function filterBaselines() {
    const sel = document.getElementById('baseline-filter-db-type');
    if (sel) loadBaselines(sel.value);
}

// ========== 渲染表格 ==========
function renderBaselineTable(baselines) {
    const tbody = document.getElementById('baseline-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!baselines || baselines.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:32px">暂无基线配置</td></tr>';
        return;
    }

    baselines.forEach(bl => {
        const id = bl.id;
        const dbType = escapeHtml(bl.db_type || '');
        const paramName = escapeHtml(bl.param_name || '');
        const operator = escapeHtml(bl.operator || '=');
        const expected = bl.expected_value ? escapeHtml(String(bl.expected_value)) :
            (bl.expected_value_min || bl.expected_value_max) ? `${bl.expected_value_min || ''} ~ ${bl.expected_value_max || ''}` : '';
        const riskBadge = getRiskBadge(bl.risk_level);
        const enabledBadge = (bl.enabled == 1) ?
            '<span style="color:var(--success);font-weight:600;">启用</span>' :
            '<span style="color:var(--text-muted)">禁用</span>';

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${dbType}</td>
            <td>${paramName}</td>
            <td>${operator}</td>
            <td>${expected}</td>
            <td>${riskBadge}</td>
            <td>${enabledBadge}</td>
            <td>
                <button class="btn btn-sm btn-ghost" onclick="editBaseline(${id})" style="font-size:12px;padding:4px 8px">编辑</button>
                <button class="btn btn-sm btn-ghost" onclick="deleteBaseline(${id})" style="font-size:12px;padding:4px 8px;color:var(--danger)">删除</button>
            </td>`;
        tbody.appendChild(tr);
    });
}

// ========== 风险等级徽章 ==========
function getRiskBadge(riskLevel) {
    const map = {
        'LOW':      '<span style="color:var(--info)">低</span>',
        'MEDIUM':   '<span style="color:var(--warn)">中</span>',
        'HIGH':     '<span style="color:var(--danger)">高</span>',
        'CRITICAL': '<span style="color:#ff0000;font-weight:700">严重</span>'
    };
    return map[riskLevel] || '<span style="color:var(--text-muted)">未知</span>';
}

// ========== 显示添加/编辑模态框 ==========
function showBaselineModal(baselineId) {
    resetBaselineForm();
    const modal = document.getElementById('baselineModal');
    const title = document.getElementById('baselineModalLabel');
    if (!modal) return;

    if (baselineId) {
        if (title) title.textContent = '编辑基线配置';
        document.getElementById('baseline-id').value = baselineId;
        // 加载数据
        fetch(`/api/inspection/baselines/${baselineId}`)
            .then(r => r.json())
            .then(resp => {
                if (resp.success) {
                    const bl = resp.data;
                    const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
                    setVal('baseline-db-type', bl.db_type);
                    setVal('baseline-param-name', bl.param_name);
                    setVal('baseline-query-sql', bl.query_sql);
                    setVal('baseline-operator', bl.operator);
                    setVal('baseline-expected-value', bl.expected_value);
                    setVal('baseline-expected-value-min', bl.expected_value_min);
                    setVal('baseline-expected-value-max', bl.expected_value_max);
                    setVal('baseline-risk-level', bl.risk_level);
                    setVal('baseline-description-zh', bl.description_zh);
                    setVal('baseline-description-en', bl.description_en);
                    const chk = document.getElementById('baseline-enabled');
                    if (chk) chk.checked = (bl.enabled == 1);
                }
            });
    } else {
        if (title) title.textContent = '添加基线配置';
    }

    modal.style.display = 'flex';
}

// ========== 关闭模态框 ==========
function closeBaselineModal() {
    const modal = document.getElementById('baselineModal');
    if (modal) modal.style.display = 'none';
}

// ========== 重置表单 ==========
function resetBaselineForm() {
    const ids = ['baseline-id','baseline-db-type','baseline-param-name','baseline-query-sql',
                  'baseline-operator','baseline-expected-value','baseline-expected-value-min',
                  'baseline-expected-value-max','baseline-risk-level',
                  'baseline-description-zh','baseline-description-en'];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        if (el.type === 'checkbox') el.checked = true;
        else el.value = '';
    });
    const op = document.getElementById('baseline-operator');
    if (op) op.value = '=';
    const rl = document.getElementById('baseline-risk-level');
    if (rl) rl.value = 'MEDIUM';
    const en = document.getElementById('baseline-enabled');
    if (en) en.checked = true;
}

// ========== 保存基线配置 ==========
function saveBaseline() {
    const getData = (id) => {
        const el = document.getElementById(id);
        return el ? el.value.trim() : '';
    };
    const dbType    = getData('baseline-db-type');
    const paramName  = getData('baseline-param-name');
    const querySql  = getData('baseline-query-sql');

    if (!dbType || !paramName || !querySql) {
        showBaselineToast('数据库类型、参数名称和查询 SQL 是必填项', 'error');
        return;
    }

    const id = getData('baseline-id');
    const isEdit = !!id;
    const url = isEdit ? `/api/inspection/baselines/${id}` : '/api/inspection/baselines';
    const method = isEdit ? 'PUT' : 'POST';

    const body = {
        db_type:           dbType,
        param_name:         paramName,
        query_sql:          querySql,
        operator:           getData('baseline-operator') || '=',
        expected_value:      getData('baseline-expected-value') || null,
        expected_value_min:  getData('baseline-expected-value-min') || null,
        expected_value_max:  getData('baseline-expected-value-max') || null,
        risk_level:          getData('baseline-risk-level') || 'MEDIUM',
        description_zh:      getData('baseline-description-zh') || null,
        description_en:      getData('baseline-description-en') || null,
        enabled:             (() => { const el = document.getElementById('baseline-enabled'); return el && el.checked ? 1 : 0; })()
    };

    fetch(url, {
        method: method,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    })
    .then(r => r.json())
    .then(resp => {
        if (resp.success) {
            showBaselineToast(isEdit ? '基线配置更新成功' : '基线配置创建成功', 'success');
            closeBaselineModal();
            loadBaselines();
        } else {
            showBaselineToast('保存失败: ' + (resp.message || '未知错误'), 'error');
        }
    })
    .catch(err => {
        showBaselineToast('保存失败: ' + err.message, 'error');
    });
}

// ========== 编辑基线配置 ==========
function editBaseline(id) {
    showBaselineModal(id);
}

// ========== 删除基线配置 ==========
function deleteBaseline(id) {
    if (!confirm('确定要删除此基线配置吗？')) return;

    fetch(`/api/inspection/baselines/${id}`, {method: 'DELETE'})
        .then(r => r.json())
        .then(resp => {
            if (resp.success) {
                showBaselineToast('基线配置删除成功', 'success');
                loadBaselines();
            } else {
                showBaselineToast('删除失败: ' + (resp.message || '未知错误'), 'error');
            }
        })
        .catch(err => {
            showBaselineToast('删除失败: ' + err.message, 'error');
        });
}

// ========== 初始化默认基线配置 ==========
function initDefaultBaselines() {
    if (!confirm('确定要初始化默认基线配置吗？\n这可能会创建重复的基线配置。')) return;

    fetch('/api/inspection/baselines/init', {method: 'POST'})
        .then(r => r.json())
        .then(resp => {
            if (resp.success) {
                showBaselineToast('默认基线配置初始化成功', 'success');
                loadBaselines();
            } else {
                showBaselineToast('初始化失败: ' + (resp.message || '未知错误'), 'error');
            }
        })
        .catch(err => {
            showBaselineToast('初始化失败: ' + err.message, 'error');
        });
}

// ========== Toast 提示 ==========
function showBaselineToast(msg, type) {
    // 复用 index.html 已有的 toastSuccess / toastError（如果存在）
    if (type === 'success' && typeof toastSuccess === 'function') {
        toastSuccess(msg);
        return;
    }
    if (type === 'error' && typeof toastError === 'function') {
        toastError(msg);
        return;
    }
    // fallback
    alert((type === 'error' ? '错误: ' : '成功: ') + msg);
}

// ========== HTML 转义 ==========
function escapeHtml(text) {
    if (text == null) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
