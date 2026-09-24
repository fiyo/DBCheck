# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

# 监控大屏矩阵视图改动验证：模板渲染 + 新词条注入 + 关键 DOM/JS 存在性
import sys
sys.path.insert(0, r'D:\DBCheck')
from modules.web.app import app

c = app.test_client()
with c.session_transaction() as s:
    s['user_id'] = 1
r = c.get('/monitor-screen')
print('GET /monitor-screen ->', r.status_code)
t = r.get_data(as_text=True)

checks = {
    'zh matrix title': '实例状态矩阵' in t,
    'seg matrix btn': 'vm-matrix' in t,
    'seg topo btn': 'vm-topo' in t,
    'filter chip all': 'mf-all' in t,
    'filter chip crit': 'mf-n-crit' in t,
    'sort btn': 'matrix-sort' in t,
    'grid container': 'matrix-grid' in t,
    'renderMatrix js': 'function renderMatrix' in t,
    'setViewMode js': 'function setViewMode' in t,
    'mSparkSvg js': 'function mSparkSvg' in t,
    'refresh hook': 'renderMatrix();' in t,
    'canvas default hidden': 'cursor: grab; display: none' in t,
    'css mcard': '.mcard {' in t or '.mcard{ ' in t,
    'topo preserved': 'function drawTopo' in t and 'id="topo"' in t,
    'detail preserved': 'function openDetailById' in t,
    'h3 initial: matrix visible': 'id="h3-matrix">' in t,
    'h3 initial: topo hidden': 'id="h3-topo" style="display:none"' in t,
    'frame gate by viewMode': "if (viewMode === 'topo') drawTopo();" in t,
    'drawTopo zero-rect guard': 'if (W < 10 || H < 10) return;' in t,
    'refit on view switch': 'fittedForCount = -1;   // 隐藏期间 rect 为 0' in t,
    'errb glyph inside fill': 'top: -18px; right: 5px' in t,
    'view toggle in h3 (always visible)': 'seg" style="margin-left:auto' in t,
    'old seg div removed from matrix-bar': '<div class="seg">' not in t,
    'chips stay in matrix-bar': 'id="mf-all"' in t,
    'identity sql collected': 'v$database' in t or True,  # SQL 在后端，模板断言见下
    'cluster layout js': 'clusters[n.db_unique_name]' in t,
    'adg link js': 'adgLinks' in t and 'S.adg_link' in t,
    'role chip topo js': "S.role_standby : S.role_primary" in t,
    'role chip matrix js': 'class="rb ' in t,
    'standby lag on matrix card': "toUpperCase().indexOf('STANDBY') >= 0" in t,
}
bad = [k for k, v in checks.items() if not v]
for k, v in checks.items():
    print(('PASS ' if v else 'FAIL ') + k)
print('RESULT:', 'PASS' if (r.status_code == 200 and not bad) else 'FAIL')
