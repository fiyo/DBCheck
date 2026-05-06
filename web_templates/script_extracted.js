
// ─── State ───
let dbType = 'mysql';
let sshEnabled = false;
let currentTaskId = null;
let pollTimer = null;
let logOffset = 0;

// ─── Page Navigation ───
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
  const titles = {
    home:    ['首页',     '欢迎使用 DBCheck 数据库巡检工具'],
    wizard:  ['新建巡检', '逐步配置，一键生成报告'],
    reports: ['历史报告', '所有已生成的巡检报告'],
    trend:   ['趋势分析', '同一数据库多次巡检的历史指标趋势'],
    ai:      ['AI 诊断设置', '配置大语言模型，巡检后自动生成优化建议'],
  };
  const t = titles[name] || [name, ''];
  document.getElementById('topbar-title').textContent = t[0];
  document.getElementById('topbar-sub').textContent = t[1];
  if (name === 'reports') loadReports();
  if (name === 'trend') loadTrendInstances();
  if (name === 'ai') loadAIConfig();
}

function startWizard() {
  dbType = 'mysql';
  sshEnabled = false;
  currentTaskId = null;
  logOffset = 0;
  selectDbType('mysql');
  gotoStep(1);
  showPage('wizard');
  // reset run panel
  document.getElementById('confirm-panel').style.display = '';
  document.getElementById('run-panel').style.display = 'none';
  document.getElementById('result-panel').style.display = 'none';
  document.getElementById('done-btns').style.display = 'none';
  document.getElementById('log-box').innerHTML = '';
}

// ─── Steps ───
const STEP_COUNT = 5;
function gotoStep(n) {
  for (let i = 1; i <= STEP_COUNT; i++) {
    document.getElementById('step' + i).style.display = (i === n) ? '' : 'none';
    const s = document.getElementById('s' + i);
    s.className = 'step' + (i < n ? ' done' : i === n ? ' active' : '');
    if (i > 1) {
      const sl = document.getElementById('sl' + i);
      if (sl) sl.className = 'step-line' + (i <= n ? ' done' : '');
    }
  }
  if (n === 5) fillConfirmPanel();
}

// ─── DB Type ───
function selectDbType(type) {
  dbType = type;
  document.getElementById('tc-mysql').classList.toggle('selected', type === 'mysql');
  document.getElementById('tc-pg').classList.toggle('selected', type === 'pg');
  document.getElementById('chk-mysql').textContent = type === 'mysql' ? '✓' : '';
  document.getElementById('chk-pg').textContent   = type === 'pg'    ? '✓' : '';
  // 默认端口 & 用户名
  document.getElementById('f-port').value = type === 'mysql' ? 3306 : 5432;
  document.getElementById('f-user').value = type === 'mysql' ? 'root' : 'postgres';
  document.getElementById('f-database-wrap').style.display = type === 'pg' ? '' : 'none';
}

// ─── Test DB ───
async function testDb() {
  const el = document.getElementById('db-test-status');
  el.innerHTML = '<div class="status-banner info"><span class="spinner"></span> 正在测试连接...</div>';
  try {
    const res = await fetch('/api/test_db', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        db_type: dbType,
        host:     document.getElementById('f-host').value.trim(),
        port:     parseInt(document.getElementById('f-port').value) || (dbType==='mysql'?3306:5432),
        user:     document.getElementById('f-user').value.trim(),
        password: document.getElementById('f-password').value,
        database: document.getElementById('f-database').value.trim() || 'postgres',
      })
    });
    const data = await res.json();
    if (data.ok) {
      el.innerHTML = `<div class="status-banner ok">✅ 连接成功：${escHtml(data.msg)}</div>`;
    } else {
      el.innerHTML = `<div class="status-banner err">❌ 连接失败：${escHtml(data.msg)}</div>`;
    }
  } catch(e) {
    el.innerHTML = `<div class="status-banner err">❌ 请求失败：${escHtml(e.message)}</div>`;
  }
}

// ─── SSH Toggle ───
function toggleSsh() {
  sshEnabled = !sshEnabled;
  document.getElementById('ssh-toggle').className = 'toggle' + (sshEnabled ? ' on' : '');
  document.getElementById('ssh-toggle-sub').textContent = sshEnabled ? '当前：已启用' : '当前：未启用';
  document.getElementById('ssh-fields').className = 'ssh-fields form-grid' + (sshEnabled ? ' visible' : '');
  document.getElementById('btn-test-ssh').style.display = sshEnabled ? '' : 'none';
  if (sshEnabled && !document.getElementById('f-ssh-host').value) {
    document.getElementById('f-ssh-host').value = document.getElementById('f-host').value;
  }
}

function onSshAuthChange() {
  const mode = document.getElementById('f-ssh-auth').value;
  document.getElementById('f-ssh-password-wrap').style.display = mode === 'password' ? '' : 'none';
  document.getElementById('f-ssh-key-wrap').style.display = mode === 'key' ? '' : 'none';
}

// ─── Test SSH ───
async function testSsh() {
  const el = document.getElementById('ssh-test-status');
  el.innerHTML = '<div class="status-banner info"><span class="spinner"></span> 正在测试 SSH...</div>';
  try {
    const res = await fetch('/api/test_ssh', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        ssh_host:     document.getElementById('f-ssh-host').value.trim(),
        ssh_port:     parseInt(document.getElementById('f-ssh-port').value) || 22,
        ssh_user:     document.getElementById('f-ssh-user').value.trim(),
        ssh_password: document.getElementById('f-ssh-password').value,
        ssh_key_file: document.getElementById('f-ssh-key').value.trim(),
      })
    });
    const data = await res.json();
    el.innerHTML = data.ok
      ? `<div class="status-banner ok">✅ ${escHtml(data.msg)}</div>`
      : `<div class="status-banner err">❌ ${escHtml(data.msg)}</div>`;
  } catch(e) {
    el.innerHTML = `<div class="status-banner err">❌ 请求失败：${escHtml(e.message)}</div>`;
  }
}

// ─── Confirm Panel ───
function fillConfirmPanel() {
  const typeLabel = dbType === 'mysql' ? '🐬 MySQL' : '🐘 PostgreSQL';
  const name = document.getElementById('f-name').value.trim() || (dbType==='mysql'?'MySQL_Server':'PG_Server');
  const host = document.getElementById('f-host').value.trim() || 'localhost';
  const port = document.getElementById('f-port').value || (dbType==='mysql'?'3306':'5432');
  const inspector = document.getElementById('f-inspector').value.trim() || 'Jack';
  const sshLabel = sshEnabled ? `${document.getElementById('f-ssh-host').value || host}:${document.getElementById('f-ssh-port').value||22}` : '未配置（仅数据库指标）';

  document.getElementById('cf-dbtype').innerHTML = `<span class="tag ${dbType==='mysql'?'tag-mysql':'tag-pg'}">${typeLabel}</span>`;
  document.getElementById('cf-name').textContent = name;
  document.getElementById('cf-conn').textContent = `${host}:${port}`;
  document.getElementById('cf-ssh').textContent = sshLabel;
  document.getElementById('cf-inspector').textContent = inspector;
}

// ─── Start Inspection ───
async function startInspection() {
  const btn = document.getElementById('btn-run');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 启动中...';

  const name = document.getElementById('f-name').value.trim() || (dbType==='mysql'?'MySQL_Server':'PG_Server');
  const host = document.getElementById('f-host').value.trim() || 'localhost';
  const port = parseInt(document.getElementById('f-port').value) || (dbType==='mysql'?3306:5432);
  const user = document.getElementById('f-user').value.trim() || (dbType==='mysql'?'root':'postgres');
  const password = document.getElementById('f-password').value;
  const database = document.getElementById('f-database').value.trim() || 'postgres';
  const inspector = document.getElementById('f-inspector').value.trim() || 'Jack';

  const payload = { db_type: dbType, name, host, port, user, password, database, inspector_name: inspector };
  if (sshEnabled) {
    payload.ssh_host     = document.getElementById('f-ssh-host').value.trim() || host;
    payload.ssh_port     = parseInt(document.getElementById('f-ssh-port').value) || 22;
    payload.ssh_user     = document.getElementById('f-ssh-user').value.trim() || 'root';
    payload.ssh_password = document.getElementById('f-ssh-password').value;
    payload.ssh_key_file = document.getElementById('f-ssh-key').value.trim();
  }

  try {
    const res = await fetch('/api/start_inspection', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.msg || '启动失败');

    currentTaskId = data.task_id;
    logOffset = 0;

    // 切换到运行面板
    document.getElementById('confirm-panel').style.display = 'none';
    document.getElementById('run-panel').style.display = '';
    document.getElementById('progress-bar').style.width = '10%';

    pollStatus();
  } catch(e) {
    btn.disabled = false;
    btn.innerHTML = '🚀 开始巡检';
    alert('启动失败: ' + e.message);
  }
}

// ─── Poll Status ───
function pollStatus() {
  if (pollTimer) clearTimeout(pollTimer);
  if (!currentTaskId) return;

  fetch(`/api/task_status/${currentTaskId}?offset=${logOffset}`)
    .then(r => r.json())
    .then(data => {
      if (!data.ok) return;

      // 追加日志
      const box = document.getElementById('log-box');
      data.log.forEach(line => {
        const d = document.createElement('div');
        d.className = 'log-line' + (line.includes('✅') ? ' ok' : line.includes('❌') ? ' err' : line.includes('▶') || line.includes('📊') || line.includes('📝') ? ' info' : '');
        d.textContent = line;
        box.appendChild(d);
      });
      box.scrollTop = box.scrollHeight;
      logOffset = data.offset;

      // 进度模拟
      const status = data.status;
      const bar = document.getElementById('progress-bar');
      if (status === 'running')  bar.style.width = Math.min(85, parseFloat(bar.style.width||10) + 8) + '%';
      if (status === 'done')     bar.style.width = '100%';

      // 状态文字
      const st = document.getElementById('run-status-text');
      const sp = document.getElementById('run-spinner');
      if (status === 'pending' || status === 'running') {
        st.textContent = status === 'pending' ? '等待执行...' : '巡检进行中，请稍候...';
        pollTimer = setTimeout(pollStatus, 800);
      } else if (status === 'done') {
        st.textContent = '✅ 巡检完成！';
        sp.style.display = 'none';
        document.getElementById('result-panel').style.display = '';

        // 智能分析摘要
        let analyzeHtml = '';
        const issues = data.auto_analyze || [];
        const highRisk = issues.filter(i => i.col2 === '高风险').length;
        const midRisk  = issues.filter(i => i.col2 === '中风险').length;
        if (issues.length > 0) {
          analyzeHtml = `
          <div class="result-card" style="border-left:3px solid ${highRisk > 0 ? '#f44336' : midRisk > 0 ? '#ff9800' : '#4caf50'}">
            <div class="ri">${highRisk > 0 ? '🔴' : midRisk > 0 ? '🟡' : '🟢'}</div>
            <div class="rd">
              <h3>智能分析发现问题 ${issues.length} 项</h3>
              <p>高风险 ${highRisk} · 中风险 ${midRisk} · 低风险/建议 ${issues.length - highRisk - midRisk}</p>
            </div>
          </div>`;
        }

        // AI 诊断结果
        let aiHtml = '';
        const ai = data.ai_advice;
        if (ai && ai.trim()) {
          // 渲染 Markdown-like 内容（换行 + 列表）
          const lines = escHtml(ai).split('\n');
          const bodyHtml = lines.map(line => {
            if (line.startsWith('- ') || line.startsWith('* '))
              return '<li>' + line.slice(2) + '</li>';
            if (line.trim()) return '<p>' + line + '</p>';
            return '';
          }).join('');
          aiHtml = `
          <div class="result-card" style="background:linear-gradient(135deg,rgba(56,139,253,.06),rgba(124,58,237,.06));border-left:3px solid #7c3aed">
            <div class="ri">🤖</div>
            <div class="rd" style="flex:1">
              <h3 style="color:#7c3aed">AI 智能诊断建议</h3>
              <div style="font-size:12px;color:var(--text-muted);margin-top:2px">基于巡检数据自动生成，仅供参考</div>
            </div>
            <div style="margin-left:auto;flex-shrink:0">
              <button class="btn btn-ghost" onclick="toggleAiAdvice()" style="font-size:12px;padding:7px 14px">${ai.length > 120 ? '展开' : '收起'}</button>
            </div>
          </div>
          <div id="ai-advice-body" style="display:${ai.length > 120 ? 'none' : 'block'};margin-top:8px;padding:12px 16px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;font-size:13px;line-height:1.8;color:var(--text-main)">
            ${bodyHtml}
          </div>
          <scr" + "ipt>function toggleAiAdvice(){const b=document.getElementById('ai-advice-body');const btn=b.previousElementSibling.querySelector('button');if(b.style.display==='none'){b.style.display='block';btn.textContent='收起'}else{b.style.display='none';btn.textContent='展开'}}</scr" + "ipt>`;
        }

        document.getElementById('result-panel').innerHTML = `
          <div class="result-card">
            <div class="ri">📄</div>
            <div class="rd">
              <h3>报告已生成</h3>
              <p>${escHtml(data.report_name || '')}</p>
            </div>
            <a class="btn btn-success" href="/api/download/${currentTaskId}" download>⬇ 下载报告</a>
          </div>
          ${analyzeHtml}
          ${aiHtml}`;
        document.getElementById('done-btns').style.display = '';
      } else if (status === 'error') {
        st.textContent = '❌ 巡检失败';
        sp.style.display = 'none';
        document.getElementById('done-btns').style.display = '';
      }
    })
    .catch(() => {
      pollTimer = setTimeout(pollStatus, 2000);
    });
}

// ─── Reports ───
async function loadReports() {
  const list = document.getElementById('reports-list');
  list.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:32px">正在加载...</div>';
  try {
    const res = await fetch('/api/reports');
    const data = await res.json();
    if (!data.files || data.files.length === 0) {
      list.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:48px">暂无历史报告</div>';
      return;
    }
    list.innerHTML = data.files.map(f => {
      const isMySQL = f.name.includes('MySQL');
      const isPG    = f.name.includes('PostgreSQL') || f.name.includes('Postgres');
      const tagHtml = isMySQL ? '<span class="tag tag-mysql">MySQL</span>' : isPG ? '<span class="tag tag-pg">PostgreSQL</span>' : '';
      const size = f.size > 1024*1024 ? (f.size/1024/1024).toFixed(1)+' MB' : (f.size/1024).toFixed(1)+' KB';
      const dt = new Date(f.mtime*1000).toLocaleString('zh-CN');
      return `
        <div class="report-item">
          <div class="ri-icon">📄</div>
          <div class="ri-info">
            <div class="ri-name">${escHtml(f.name)} ${tagHtml}</div>
            <div class="ri-meta">${dt} · ${size}</div>
          </div>
          <a class="btn btn-ghost" style="font-size:12px;padding:7px 14px"
             href="/api/download_file?name=${encodeURIComponent(f.name)}" download>⬇ 下载</a>
        </div>`;
    }).join('');
  } catch(e) {
    list.innerHTML = `<div class="status-banner err">加载失败: ${escHtml(e.message)}</div>`;
  }
}


// ─── Trend Analysis ───────────────────────────────────────────────
let trendInstances = [];

async function loadTrendInstances() {
  const sel = document.getElementById('trend-instance-select');
  sel.innerHTML = '<option value="">-- 加载中 --</option>';
  try {
    const res = await fetch('/api/history_instances');
    const data = await res.json();
    trendInstances = data.instances || [];
    if (trendInstances.length === 0) {
      sel.innerHTML = '<option value="">-- 暂无历史记录，请先执行巡检 --</option>';
      document.getElementById('trend-empty').style.display = 'block';
      document.getElementById('trend-chart-card').style.display = 'none';
      return;
    }
    sel.innerHTML = trendInstances.map((inst, i) =>
      `<option value="${i}">${inst.db_type.toUpperCase()} · ${inst.label || inst.host} (${inst.host}:${inst.port}) · ${inst.snapshots_count} 次记录</option>`
    ).join('');
    document.getElementById('trend-empty').style.display = 'none';
  } catch(e) {
    sel.innerHTML = '<option value="">-- 加载失败 --</option>';
  }
}

function onTrendInstanceChange() {
  document.getElementById('trend-chart-card').style.display = 'none';
  document.getElementById('trend-comparison').style.display = 'none';
}

async function loadTrendData() {
  const sel = document.getElementById('trend-instance-select');
  const idx = parseInt(sel.value);
  if (isNaN(idx) || !trendInstances[idx]) return;
  const inst = trendInstances[idx];

  try {
    const res = await fetch(`/api/trend?db_type=${inst.db_type}&host=${encodeURIComponent(inst.host)}&port=${inst.port}`);
    const data = await res.json();
    if (!data.ok || !data.trend || !data.trend.labels) {
      document.getElementById('trend-empty').style.display = 'block';
      document.getElementById('trend-chart-card').style.display = 'none';
      return;
    }

    // 渲染对比
    renderComparison(data.comparison, inst);

    // 渲染趋势图
    renderTrendCharts(data.trend, inst);

    document.getElementById('trend-empty').style.display = 'none';
    document.getElementById('trend-chart-card').style.display = 'block';
  } catch(e) {
    console.error(e);
  }
}

function renderComparison(cmp, inst) {
  const el = document.getElementById('trend-comparison');
  if (!cmp || !cmp.curr) { el.style.display = 'none'; return; }
  const metricLabels = {
    mem_usage: '内存使用率', cpu_usage: 'CPU 使用率',
    disk_usage_max: '磁盘使用率', connections: '当前连接数',
    cache_hit_ratio: '缓存命中率', queries_total: '累计查询数',
    max_used_connections: '历史最大连接数', risk_count: '风险项数量',
  };
  const diffRows = Object.entries(cmp.diff || {})
    .filter(([k]) => metricLabels[k])
    .map(([k, v]) => {
      const isGood = (k === 'risk_count' || k === 'mem_usage' || k === 'disk_usage_max') ? v < 0 : v > 0;
      const isPercent = ['mem_usage','cpu_usage','disk_usage_max','cache_hit_ratio'].includes(k);
      const arrow = v > 0 ? '▲' : v < 0 ? '▼' : '─';
      const color = v === 0 ? 'var(--text-muted)' : isGood ? 'var(--accent)' : 'var(--danger)';
      const unit = isPercent ? '%' : '';
      return `<tr>
        <td style="padding:6px 12px;color:var(--text-muted)">${metricLabels[k]}</td>
        <td style="padding:6px 12px">${cmp.prev[k] !== undefined ? cmp.prev[k] + unit : 'N/A'}</td>
        <td style="padding:6px 12px">${cmp.curr[k] !== undefined ? cmp.curr[k] + unit : 'N/A'}</td>
        <td style="padding:6px 12px;color:${color};font-weight:600">${arrow} ${Math.abs(v)}${unit}</td>
      </tr>`;
    }).join('');

  el.innerHTML = `
    <div class="card" style="background:var(--surface2)">
      <div class="card-title" style="font-size:13.5px">
        <span>🔄</span> 与上次巡检对比
        <span style="font-size:12px;color:var(--text-muted);margin-left:auto">${cmp.prev_ts} → ${cmp.curr_ts}</span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="border-bottom:1px solid var(--border)">
          <th style="padding:6px 12px;text-align:left;color:var(--text-muted);font-weight:500">指标</th>
          <th style="padding:6px 12px;text-align:left;color:var(--text-muted);font-weight:500">上次</th>
          <th style="padding:6px 12px;text-align:left;color:var(--text-muted);font-weight:500">本次</th>
          <th style="padding:6px 12px;text-align:left;color:var(--text-muted);font-weight:500">变化</th>
        </tr></thead>
        <tbody>${diffRows || '<tr><td colspan="4" style="padding:16px;text-align:center;color:var(--text-muted)">暂无可对比指标</td></tr>'}</tbody>
      </table>
    </div>`;
  el.style.display = 'block';
}

function renderTrendCharts(trend, inst) {
  const grid = document.getElementById('trend-charts-grid');
  const metricConfigs = [
    { key: 'mem_usage',           label: '内存使用率 (%)',    color: '#388bfd', warn: 80 },
    { key: 'cpu_usage',           label: 'CPU 使用率 (%)',    color: '#e3b341', warn: 80 },
    { key: 'disk_usage_max',      label: '磁盘使用率最大值 (%)', color: '#f85149', warn: 80 },
    { key: 'connections',         label: '当前连接数',         color: '#2ea043', warn: null },
    { key: 'cache_hit_ratio',     label: '缓存命中率 (%)',    color: '#a371f7', warn: 95 },
    { key: 'risk_count',          label: '风险项数量',         color: '#e3b341', warn: 3 },
    { key: 'max_used_connections',label: '历史最大连接数',     color: '#79c0ff', warn: null },
  ];

  const labels = trend.labels || [];
  const shortLabels = labels.map(l => l.slice(5)); // MM-DD HH:mm

  grid.innerHTML = '';
  metricConfigs.forEach(cfg => {
    const vals = (trend.metrics || {})[cfg.key];
    if (!vals || vals.every(v => v === 0)) return;

    const canvasId = 'chart-' + cfg.key;
    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'background:var(--surface2);border-radius:10px;padding:16px';
    wrapper.innerHTML = `<div style="font-size:13px;color:var(--text-muted);margin-bottom:8px">${cfg.label}</div><canvas id="${canvasId}" height="160"></canvas>`;
    grid.appendChild(wrapper);

    // 用纯 SVG/Canvas 自绘折线图（不依赖 Chart.js）
    requestAnimationFrame(() => drawLineChart(canvasId, shortLabels, vals, cfg));
  });
}

function drawLineChart(canvasId, labels, values, cfg) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.offsetWidth || 300;
  const H = 160;
  canvas.width = W;
  canvas.height = H;
  const pad = { top: 10, right: 10, bottom: 30, left: 40 };
  const chartW = W - pad.left - pad.right;
  const chartH = H - pad.top - pad.bottom;

  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const range = maxV - minV || 1;

  const xStep = values.length > 1 ? chartW / (values.length - 1) : chartW;

  function toX(i) { return pad.left + i * xStep; }
  function toY(v) { return pad.top + chartH - ((v - minV) / range) * chartH; }

  ctx.clearRect(0, 0, W, H);

  // 网格线
  ctx.strokeStyle = '#30363d';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (chartH / 4) * i;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + chartW, y); ctx.stroke();
    const val = maxV - (range / 4) * i;
    ctx.fillStyle = '#8b949e';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(val.toFixed(val < 10 ? 1 : 0), pad.left - 4, y + 3);
  }

  // 警戒线
  if (cfg.warn !== null) {
    const warnY = toY(cfg.warn);
    if (warnY >= pad.top && warnY <= pad.top + chartH) {
      ctx.strokeStyle = 'rgba(227,179,65,0.4)';
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(pad.left, warnY); ctx.lineTo(pad.left + chartW, warnY); ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // 渐变填充
  const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH);
  grad.addColorStop(0, cfg.color + '40');
  grad.addColorStop(1, cfg.color + '05');
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.moveTo(toX(0), toY(values[0]));
  values.forEach((v, i) => { if (i > 0) ctx.lineTo(toX(i), toY(v)); });
  ctx.lineTo(toX(values.length - 1), pad.top + chartH);
  ctx.lineTo(toX(0), pad.top + chartH);
  ctx.closePath();
  ctx.fill();

  // 折线
  ctx.strokeStyle = cfg.color;
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.beginPath();
  values.forEach((v, i) => {
    if (i === 0) ctx.moveTo(toX(i), toY(v)); else ctx.lineTo(toX(i), toY(v));
  });
  ctx.stroke();

  // 数据点
  values.forEach((v, i) => {
    ctx.fillStyle = cfg.color;
    ctx.beginPath(); ctx.arc(toX(i), toY(v), 3, 0, Math.PI * 2); ctx.fill();
  });

  // X 轴标签（每隔几个显示一个）
  const step = Math.max(1, Math.floor(labels.length / 4));
  ctx.fillStyle = '#8b949e';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'center';
  labels.forEach((l, i) => {
    if (i % step === 0 || i === labels.length - 1) {
      ctx.fillText(l.slice(-5), toX(i), H - 6);
    }
  });
}


// ─── AI Config ───────────────────────────────────────────────────
async function loadAIConfig() {
  try {
    const res = await fetch('/api/ai_config');
    const data = await res.json();
    const cfg = data.config || {};
    document.getElementById('ai-backend').value = cfg.backend || 'disabled';
    document.getElementById('ai-api-key').value = cfg.api_key === '***' ? '' : (cfg.api_key || '');
    document.getElementById('ai-api-url').value = cfg.api_url || '';
    document.getElementById('ai-model').value = cfg.model || '';
    if (cfg.api_url && cfg.api_url.includes('localhost')) {
      document.getElementById('ai-ollama-url').value = cfg.api_url;
      document.getElementById('ai-ollama-model').value = cfg.model || '';
    }
    onAIBackendChange();
  } catch(e) { console.error(e); }
}

function onAIBackendChange() {
  const b = document.getElementById('ai-backend').value;
  document.getElementById('ai-openai-fields').style.display  = b === 'openai'  ? 'block' : 'none';
  document.getElementById('ai-ollama-fields').style.display  = b === 'ollama'  ? 'block' : 'none';
  document.getElementById('ai-save-banner').style.display = 'none';
}

async function saveAIConfig() {
  const backend = document.getElementById('ai-backend').value;
  let cfg = { backend };
  if (backend === 'openai') {
    cfg.api_key = document.getElementById('ai-api-key').value;
    cfg.api_url = document.getElementById('ai-api-url').value || 'https://api.openai.com/v1';
    cfg.model   = document.getElementById('ai-model').value || 'gpt-4o-mini';
  } else if (backend === 'ollama') {
    cfg.api_key = '';
    cfg.api_url = document.getElementById('ai-ollama-url').value || 'http://localhost:11434';
    cfg.model   = document.getElementById('ai-ollama-model').value || 'qwen2.5:7b';
  }
  try {
    const res = await fetch('/api/ai_config', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg) });
    const data = await res.json();
    const banner = document.getElementById('ai-save-banner');
    if (data.ok) {
      banner.className = 'status-banner ok';
      banner.textContent = '✅ ' + data.msg;
    } else {
      banner.className = 'status-banner err';
      banner.textContent = '❌ 保存失败: ' + data.msg;
    }
    banner.style.display = 'flex';
    setTimeout(() => { banner.style.display = 'none'; }, 4000);
  } catch(e) {
    console.error(e);
  }
}


// ─── Utils ───
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ─── Init ───
showPage('home');
loadTrendInstances();
loadAIConfig();
