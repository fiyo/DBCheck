# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
modules/intelligence/compliance.py — 信创合规态势引擎（P2）

职责：
  - 对纳管实例做「信创化程度」盘点，输出态势看板 + 合规报告。
  - 判定口径：每条 db_type 在 driver_registry.DB_TYPE_CATALOG 有 xinchuang 权威默认，
    用户可在前端覆盖（xinchuang_overrides 表），覆盖优先。
  - 评分：加权合规率（命中「生产/核心/主库/prod/core/primary」标签的实例权重更高，
    因其替换风险与业务影响更大），0–100 分。该加权为启发式，已在报告注明。
  - 待替换清单：foreign 实例 + 国产替代映射（REPLACEMENT_MAP）。

读模型：直接只读 instances.db（与 access.py 取数据源口径一致），不触发 InstanceManager 的重流程。
"""

import os
import json
import sqlite3
import threading
from typing import List, Dict, Any, Optional, Tuple

from modules.core.paths import DATA_DIR, PRO_DATA_DIR
from modules.driver_registry import xinchuang_default, normalize_db_type, DB_TYPE_KEYS, DB_TYPE_CATALOG

# ── 常量 ──────────────────────────────────────────────
COMPLIANCE_DB = str(DATA_DIR / 'compliance.db')

# 命中这些标签的实例视为「生产/核心」，在合规评分中权重加倍。
_PROD_TAG_KEYWORDS = ('生产', '核心', '主库', 'prod', 'core', 'primary', 'primary')

# 国外（foreign）数据库 → 国产替代建议（db_type 列表，空列表表示暂无明确单库替代）。
REPLACEMENT_MAP: Dict[str, List[str]] = {
    'oracle': ['dm', 'kingbase', 'yashandb', 'oceanbase'],
    'mysql': ['tidb', 'oceanbase', 'doris'],
    'mariadb': ['tidb', 'oceanbase'],
    'postgresql': ['opengauss', 'highgo', 'ivorysql'],
    'sqlserver': ['dm', 'kingbase'],
    'db2': ['dm', 'kingbase'],
    'sybase': ['dm'],
    'informix': ['dm', 'kingbase'],
    'greenplum': ['opengauss', 'highgo'],
    'clickhouse': ['starrocks', 'doris'],
    'sqlite3': [],
    'mongodb': [],
    'elasticsearch': [],
    'hive': [],
}

# 仅在报告里展示给用户看的「中文名」缓存（避免重复查 catalog）。
_DB_TYPE_NAME_ZH = {d['key']: d['name_zh'] for d in DB_TYPE_CATALOG}

# 替代库展示名（诊断中心联动提示条用，优先于 catalog 原文，保证中文直观）。
REPLACEMENT_DISPLAY = {
    'dm': '达梦', 'kingbase': '金仓', 'yashandb': '崖山', 'oceanbase': 'OceanBase',
    'tidb': 'TiDB', 'doris': 'Doris', 'opengauss': 'openGauss', 'highgo': '瀚高',
    'ivorysql': 'IvorySQL', 'gbase': 'GBase', 'uxdb': '优炫', 'shentong': '神通',
}

_LOCK = threading.Lock()


# ── SQLite 连接 ───────────────────────────────────────
def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(COMPLIANCE_DB), exist_ok=True)
    c = sqlite3.connect(COMPLIANCE_DB, timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    return c


def init_db() -> None:
    """建表（幂等）。xinchuang_overrides 允许用户覆盖某 db_type 的信创判定。"""
    c = _conn()
    try:
        c.execute('''
        CREATE TABLE IF NOT EXISTS xinchuang_overrides (
            db_type    TEXT PRIMARY KEY NOT NULL,
            value      INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        ''')
        c.commit()
    finally:
        c.close()


def get_override_map() -> Dict[str, bool]:
    """返回 {db_type(lower): bool} 的覆盖映射。"""
    init_db()
    c = _conn()
    try:
        rows = c.execute('SELECT db_type, value FROM xinchuang_overrides').fetchall()
        return {r['db_type'].lower(): bool(r['value']) for r in rows}
    finally:
        c.close()


def set_override(db_type: str, value: bool) -> Dict[str, Any]:
    if not db_type:
        return {'ok': False, 'error': 'db_type 不能为空'}
    dt = normalize_db_type(db_type)
    init_db()
    c = _conn()
    try:
        c.execute(
            'INSERT INTO xinchuang_overrides (db_type, value, updated_at) '
            'VALUES (?, ?, datetime(\'now\', \'localtime\')) '
            'ON CONFLICT(db_type) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
            (dt, 1 if value else 0),
        )
        c.commit()
        return {'ok': True, 'db_type': dt, 'value': bool(value)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        c.close()


def clear_override(db_type: str) -> Dict[str, Any]:
    if not db_type:
        return {'ok': False, 'error': 'db_type 不能为空'}
    dt = normalize_db_type(db_type)
    c = _conn()
    try:
        c.execute('DELETE FROM xinchuang_overrides WHERE db_type=?', (dt,))
        c.commit()
        return {'ok': True, 'db_type': dt, 'cleared': c.rowcount > 0}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        c.close()


def classify(db_type: str) -> str:
    """返回 'domestic' / 'foreign' / 'neutral'。

    - neutral：db_type 既不在 catalog（自定义/未知类型）也无覆盖，无法判定。
    - 覆盖优先于 catalog 默认。
    """
    dt = normalize_db_type(db_type)
    if not dt:
        return 'neutral'
    ov = get_override_map()
    if dt in ov:
        return 'domestic' if ov[dt] else 'foreign'
    if dt not in DB_TYPE_KEYS:
        return 'neutral'
    return 'domestic' if xinchuang_default(dt) else 'foreign'


def tag_db_type(db_type: str) -> Dict[str, Any]:
    """给单个 db_type 打「信创合规」标签，供诊断中心联动展示。

    返回：原始类型、归一化类型、判定（domestic/foreign/neutral）、
    是否国产、国产替代建议（db_type 列表 + 中文名）、中文名。
    """
    dt = normalize_db_type(db_type)
    cls = classify(dt)
    repl = REPLACEMENT_MAP.get(dt, [])
    return {
        'db_type': db_type,
        'normalized': dt,
        'classification': cls,
        'is_domestic': cls == 'domestic',
        'is_foreign': cls == 'foreign',
        'replacement': repl,
        'replacement_labels': [REPLACEMENT_DISPLAY.get(k, _DB_TYPE_NAME_ZH.get(k, k)) for k in repl],
        'label_zh': _DB_TYPE_NAME_ZH.get(dt, dt),
    }


# ── 数据源（只读 instances.db）────────────────────────
def load_instances() -> List[Dict[str, Any]]:
    """读取全部纳管实例（只读 instances.db）。失败返回空列表而非抛错。"""
    path = str(PRO_DATA_DIR / 'instances.db')
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT id, name, db_type, host, "group", tags, enabled FROM instances'
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                'id': r['id'],
                'name': r['name'],
                'db_type': (r['db_type'] or '').lower(),
                'host': r['host'],
                'group': r['group'] or 'default',
                'tags': r['tags'] or '[]',
                'enabled': r['enabled'],
            })
        conn.close()
        return out
    except Exception as e:
        print(f'[compliance] 读取 instances.db 失败: {e}')
        return []


def _weight_of(tags: Any) -> float:
    """生产/核心实例权重加倍。tags 可能是 JSON 字符串或列表。"""
    if not tags:
        return 1.0
    if isinstance(tags, str):
        try:
            tags = json.loads(tags or '[]')
        except Exception:
            tags = []
    if not isinstance(tags, list):
        return 1.0
    low = [str(t).lower() for t in tags]
    for kw in _PROD_TAG_KEYWORDS:
        if kw in low:
            return 2.0
    return 1.0


def build_overview(instances: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """构建信创合规态势总览。"""
    if instances is None:
        instances = load_instances()

    weighted_total = 0.0
    weighted_domestic = 0.0
    by_type: Dict[str, Dict[str, Any]] = {}
    risk_instances: List[Dict[str, Any]] = []
    domestic_n = foreign_n = neutral_n = 0

    for inst in instances:
        dt = normalize_db_type(inst.get('db_type'))
        cls = classify(dt)
        w = _weight_of(inst.get('tags'))
        weighted_total += w
        if cls == 'domestic':
            weighted_domestic += w
            domestic_n += 1
        elif cls == 'foreign':
            foreign_n += 1
            risk_instances.append({
                'id': inst.get('id'),
                'name': inst.get('name'),
                'host': inst.get('host'),
                'db_type': dt,
                'db_type_name': _DB_TYPE_NAME_ZH.get(dt, dt),
                'group': inst.get('group') or 'default',
                'enabled': inst.get('enabled', 1),
                'tags': inst.get('tags'),
                'suggestions': REPLACEMENT_MAP.get(dt, []),
            })
        else:
            neutral_n += 1

        bt = by_type.setdefault(dt, {
            'db_type': dt,
            'name_zh': _DB_TYPE_NAME_ZH.get(dt, dt),
            'total': 0, 'domestic': 0, 'foreign': 0, 'neutral': 0,
            'xinchuang': xinchuang_default(dt) if dt in DB_TYPE_KEYS else None,
        })
        bt['total'] += 1
        bt[cls] += 1

    compliance_rate = round(weighted_domestic / weighted_total * 100, 1) if weighted_total else 0.0
    score = int(round(compliance_rate))

    by_type_list = sorted(by_type.values(), key=lambda x: (-x['total'], x['db_type']))
    # 替代建议中文名
    for ri in risk_instances:
        ri['suggestion_names'] = [_DB_TYPE_NAME_ZH.get(s, s) for s in ri['suggestions']]

    return {
        'total': len(instances),
        'weighted_total': round(weighted_total, 1),
        'domestic': domestic_n,
        'foreign': foreign_n,
        'neutral': neutral_n,
        'compliance_rate': compliance_rate,
        'score': score,
        'by_type': by_type_list,
        'risk_instances': risk_instances,
        'replacement_map': REPLACEMENT_MAP,
        'generated_at': _now(),
    }


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── 报告生成 ─────────────────────────────────────────
def build_report(overview: Optional[Dict[str, Any]] = None, fmt: str = 'html') -> str:
    """生成信创合规报告。fmt='html' 返回自包含 HTML；fmt='json' 返回 JSON 字符串。"""
    if overview is None:
        overview = build_overview()

    if fmt == 'json':
        return json.dumps(overview, ensure_ascii=False, indent=2)

    rate = overview['compliance_rate']
    score = overview['score']
    score_color = '#16A34A' if score >= 70 else ('#D97706' if score >= 40 else '#DC2626')
    score_word = '良好' if score >= 70 else ('一般' if score >= 40 else '偏低')

    # 按类型分布行
    type_rows = ''
    for b in overview['by_type']:
        cls = '国产' if b['xinchuang'] else ('待定' if b['xinchuang'] is None else '国外')
        type_rows += (
            f"<tr><td>{_esc(b['name_zh'])}</td><td>{_esc(b['db_type'])}</td>"
            f"<td>{b['total']}</td><td>{b['domestic']}</td><td>{b['foreign']}</td>"
            f"<td>{b['neutral']}</td><td>{cls}</td></tr>"
        )

    # 待替换清单
    risk_rows = ''
    if overview['risk_instances']:
        for r in overview['risk_instances']:
            sugg = '、'.join(_esc(s) for s in r['suggestion_names']) or '暂无明确单库替代，建议评估国产分布式/多模方案'
            risk_rows += (
                f"<tr><td>{_esc(r['name'])}</td><td>{_esc(r['host'])}</td>"
                f"<td>{_esc(r['db_type_name'])}</td><td>{_esc(r['group'])}</td>"
                f"<td>{sugg}</td></tr>"
            )
    else:
        risk_rows = '<tr><td colspan="5" style="text-align:center;color:#16A34A">✓ 无国外数据库实例，信创化已全部达成</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>信创合规态势报告</title>
<style>
 body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;margin:0;padding:32px;color:#1f2937;background:#f8fafc}}
 h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#6b7280;font-size:13px;margin-bottom:24px}}
 .score{{display:flex;align-items:center;gap:24px;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:22px 26px;margin-bottom:22px}}
 .score .big{{font-size:46px;font-weight:800;line-height:1}} .score .meta{{font-size:13px;color:#6b7280}}
 .grid{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}}
 .card{{flex:1;min-width:130px;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px}}
 .card .n{{font-size:26px;font-weight:800}} .card .l{{font-size:12px;color:#6b7280;margin-top:4px}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;font-size:13px;margin-bottom:22px}}
 th{{background:#f1f5f9;text-align:left;padding:10px 12px;color:#475569;font-weight:600}}
 td{{padding:9px 12px;border-top:1px solid #e5e7eb}}
 h2{{font-size:16px;margin:0 0 10px}}
 .bar{{height:10px;border-radius:6px;background:linear-gradient(90deg,#16A34A 0%,#16A34A {rate}%,#e5e7eb {rate}%)}}

 .note{{font-size:12px;color:#6b7280;line-height:1.7;background:#fff;border:1px dashed #cbd5e1;border-radius:10px;padding:14px 16px}}
</style></head><body>
<h1>信创合规态势报告</h1>
<div class="sub">生成时间：{_esc(overview['generated_at'])} ｜ RaccoonX 浣巡 · 信创合规引擎</div>

<div class="score">
  <div class="big" style="color:{score_color}">{score}</div>
  <div>
    <div style="font-size:14px;font-weight:600">信创合规评分（加权合规率）· {score_word}</div>
    <div class="meta">合规率 {rate}% ｜ 生产/核心实例权重加倍（启发式）</div>
    <div class="bar" style="margin-top:10px;width:260px"></div>
  </div>
</div>

<div class="grid">
  <div class="card"><div class="n">{overview['total']}</div><div class="l">纳管实例总数</div></div>
  <div class="card"><div class="n" style="color:#16A34A">{overview['domestic']}</div><div class="l">国产（信创）</div></div>
  <div class="card"><div class="n" style="color:#DC2626">{overview['foreign']}</div><div class="l">国外（待替换）</div></div>
  <div class="card"><div class="n" style="color:#6b7280">{overview['neutral']}</div><div class="l">待定（未知类型）</div></div>
</div>

<h2>库型分布</h2>
<table><thead><tr><th>类型</th><th>db_type</th><th>总数</th><th>国产</th><th>国外</th><th>待定</th><th>判定</th></tr></thead>
<tbody>{type_rows}</tbody></table>

<h2>国外数据库待替换清单</h2>
<table><thead><tr><th>实例</th><th>主机</th><th>类型</th><th>业务组</th><th>建议国产替代</th></tr></thead>
<tbody>{risk_rows}</tbody></table>

<div class="note">
  <b>说明：</b>① 信创判定以 driver_registry 的权威默认清单为基准，用户可在「信创合规」页对单库类型覆盖（覆盖优先）。
  ② 评分 = 国产实例加权数 / 全部实例加权数 × 100；命中「生产/核心/主库/prod/core/primary」标签的实例权重 ×2，
  因其替换风险与业务影响更大，该加权为启发式口径。③ 替代建议为方向性参考，实际选型需结合版本、生态与兼容性评估。
</div>
</body></html>"""
    return html


def _esc(s: Any) -> str:
    if s is None:
        return ''
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))
