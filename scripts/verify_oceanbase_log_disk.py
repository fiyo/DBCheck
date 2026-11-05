# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""OceanBase 日志盘（Clog）容量巡检验证脚本。

覆盖四层，全部跑真实代码（不打桩、不复制 SQL 字符串）：

1. 采集层：`_customize_queries` 必须注入 ob_log_disk / ob_log_disk_limit /
   ob_log_disk_recycle 三个 key，且 SQL 走 GV$OB_UNITS（不是仅 sys 可见的
   DBA_OB_UNITS）、分母是 LOG_DISK_SIZE、按 USED_PCT 倒序。
2. 规则层：用真实 RuleEngine + 真实 oceanbase.yaml，对六种场景断言触发与否。
3. 报告层：OCEANBASE_DEFAULT_CHAPTERS 末尾必须是第 18 章且含三个 key，
   且模板不得有空章节、章节号必须连续 1..N（否则模板与报告章节号会错位）。
4. 存量库补齐：删掉临时库里的该章节后调用 ensure_oceanbase_log_disk_chapter，
   断言能插入 3 条、再调一次返回 0（幂等）。

用法：
    PYTHONPATH="C:/Users/31474/AppData/Roaming/Python/Python312/site-packages" \
    "C:/Program Files (x86)/Python/python.exe" scripts/verify_oceanbase_log_disk.py
"""

import os
import sys
import shutil
import sqlite3
import tempfile
from decimal import Decimal
from types import SimpleNamespace

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PASS = []
FAIL = []


def check(name, ok, detail=''):
    if ok:
        PASS.append(name)
        print(f'  PASS  {name}')
    else:
        FAIL.append((name, detail))
        print(f'  FAIL  {name}  {detail}')


# ── 1. 采集层 ────────────────────────────────────────────────────────────────
def test_collect_layer():
    print('\n[1] 采集层：_customize_queries 注入')
    from modules.entrypoints.main_oceanbase import OceanBaseInspector

    # 只调用方法体，不构造实例（避免触发连接/加解密）
    sql_dict = OceanBaseInspector._customize_queries(SimpleNamespace(), {})
    for k in ('ob_log_disk', 'ob_log_disk_limit', 'ob_log_disk_recycle'):
        check(f'注入 {k}', k in sql_dict and bool(sql_dict.get(k)))

    q = (sql_dict.get('ob_log_disk') or '')
    qu = q.upper()
    check('ob_log_disk 用 GV$OB_UNITS', 'GV$OB_UNITS' in qu)
    check('ob_log_disk 不用 DBA_OB_UNITS（仅 sys 可见）', 'DBA_OB_UNITS' not in qu)
    check('ob_log_disk 分母是 LOG_DISK_SIZE', 'LOG_DISK_SIZE' in qu and 'NULLIF(LOG_DISK_SIZE, 0)' in qu)
    check('ob_log_disk 计算 USED_PCT', 'AS USED_PCT' in qu)
    check('ob_log_disk 按 USED_PCT 倒序', 'ORDER BY USED_PCT DESC' in qu)
    check('ob_log_disk 过滤分母为 0 的行', 'WHERE LOG_DISK_SIZE > 0' in qu)
    check('ob_log_disk_limit 查写入拒绝阈值',
          'log_disk_utilization_limit_threshold' in (sql_dict.get('ob_log_disk_limit') or ''))
    check('ob_log_disk_recycle 查回收水位',
          'log_disk_utilization_threshold' in (sql_dict.get('ob_log_disk_recycle') or '')
          and 'limit' not in (sql_dict.get('ob_log_disk_recycle') or ''))
    check('覆盖既有 key 时未破坏 ob_tenant_info', 'ob_tenant_info' in sql_dict)


# ── 2. 规则层 ────────────────────────────────────────────────────────────────
def _ctx(rows, limit=None, recycle=None):
    """构造与采集结果同形的 context（可传 Decimal 模拟 JDBC 返回）。"""
    ctx = {'ob_log_disk': rows}
    if limit is not None:
        ctx['ob_log_disk_limit'] = [{'name': 'log_disk_utilization_limit_threshold', 'value': str(limit)}]
    if recycle is not None:
        ctx['ob_log_disk_recycle'] = [{'name': 'log_disk_utilization_threshold', 'value': str(recycle)}]
    return ctx


def _row(tenant_id, pct, used=100, size=1000):
    return {'TENANT_ID': tenant_id, 'SVR_IP': '10.0.0.1', 'SVR_PORT': 2882, 'ZONE': 'zone1',
            'LOG_DISK_IN_USE': used, 'LOG_DISK_SIZE': size, 'USED_PCT': pct}


def _issues(ctx):
    from modules.pro.rule_engine import get_rule_engine
    got = get_rule_engine().analyze('oceanbase', ctx)
    return {i.get('_rule_id'): i for i in got}


def test_rules():
    print('\n[2] 规则层：真实 RuleEngine 六场景')
    from modules.pro.rule_engine import get_rule_engine

    engine = get_rule_engine()
    ids = {r.get('id') for r in engine.get_enabled_rules('oceanbase')}
    for rid in ('ob_log_disk_limit_reached', 'ob_log_disk_above_recycle',
                'ob_log_disk_limit_relaxed', 'ob_log_disk_unavailable'):
        check(f'规则已加载启用 {rid}', rid in ids)

    # a) 96.5% / 阈值 95 → 命中写入拒绝，且不重复报回收水位
    iss = _issues(_ctx([_row(1002, Decimal('96.50'))], limit=95, recycle=80))
    hit = 'ob_log_disk_limit_reached' in iss
    check('a 96.5%@95 命中「已达写入拒绝阈值」', hit, str(sorted(iss)))
    check('a 同时命中时不重复报「超过回收水位」', 'ob_log_disk_above_recycle' not in iss)
    if hit:
        msg = iss['ob_log_disk_limit_reached']['col3']
        check('a 消息含实际使用率 96.50', '96.5' in msg, msg)
        check('a 消息含租户号 1002', '1002' in msg, msg)
        check('a 消息含实际阈值 95', '95' in msg, msg)
        check('a 消息不再残留占位符 {', '{' not in msg, msg)
        check('a 修复建议含应急与根治两条', 'log_disk_utilization_limit_threshold' in
              iss['ob_log_disk_limit_reached']['fix_sql'])

    # b) 85% / 阈值 95 → 只报回收水位
    iss = _issues(_ctx([_row(1002, 85)], limit=95, recycle=80))
    check('b 85%@95 命中「超过回收水位」', 'ob_log_disk_above_recycle' in iss, str(sorted(iss)))
    check('b 85% 不误报「已达写入拒绝阈值」', 'ob_log_disk_limit_reached' not in iss)

    # c) 96% 但运维把阈值调到 98 → 不误报写入拒绝，改报「阈值被放宽」
    iss = _issues(_ctx([_row(1002, 96)], limit=98, recycle=80))
    check('c 96%@98 不误报写入拒绝', 'ob_log_disk_limit_reached' not in iss, str(sorted(iss)))
    # 96% 仍远高于回收水位 80%，「clog 回收跟不上」是事实，该报，与「阈值被放宽」并存
    check('c 96%@98 仍报回收水位（压力确实高）',
          'ob_log_disk_above_recycle' in iss, str(sorted(iss)))
    check('c 命中「阈值被放宽」', 'ob_log_disk_limit_relaxed' in iss)
    if 'ob_log_disk_limit_relaxed' in iss:
        check('c 放宽规则消息含 98', '98' in iss['ob_log_disk_limit_relaxed']['col3'])

    # d) 一个副本 97%、其余 10% → 必须按副本判（不取平均）
    iss = _issues(_ctx([_row(1002, 97), _row(1002, 10), _row(1002, 10)], limit=95, recycle=80))
    check('d 单副本 97% 即命中（不取平均）', 'ob_log_disk_limit_reached' in iss)
    if 'ob_log_disk_limit_reached' in iss:
        check('d 消息取最危险副本 97', '97' in iss['ob_log_disk_limit_reached']['col3'])

    # e) 取不到参数（OB 3.x / 权限不足）→ 回退默认 95，仍然能报
    iss = _issues(_ctx([_row(1, 96)]))
    check('e 无参数数据时回退默认 95 仍命中', 'ob_log_disk_limit_reached' in iss)
    if 'ob_log_disk_limit_reached' in iss:
        check('e 回退阈值 95 出现在消息中', '95' in iss['ob_log_disk_limit_reached']['col3'])

    # f) 完全没采到数据 → info 提示（不能静默通过）
    iss = _issues(_ctx([]))
    check('f 无数据时命中「未能采集到日志盘容量」', 'ob_log_disk_unavailable' in iss)
    check('f 无数据时不误报写入拒绝', 'ob_log_disk_limit_reached' not in iss)
    if 'ob_log_disk_unavailable' in iss:
        check('f 提示语明确「不是没问题」', '不是' in iss['ob_log_disk_unavailable']['col3'])

    # g) 健康水位 30% → 一条都不报
    iss = _issues(_ctx([_row(1002, 30)], limit=95, recycle=80))
    log_rules = [k for k in iss if k.startswith('ob_log_disk')]
    check('g 30% 健康水位零误报', not log_rules, str(log_rules))

    # h) 四条条件必须「求值无异常」——而不是靠异常吞掉变成 False。
    #    规则引擎把 params 放在 eval 的 locals，生成式取不到 locals 会抛 NameError
    #    并被静默吞掉（全仓 71 条推导式规则因此全部失效），所以这里逐条验证
    #    condition 求值不报错，防止将来有人把条件改回 any(... for ...)。
    from modules.pro.rule_engine import _safe_eval
    for rule in engine.get_enabled_rules('oceanbase'):
        if not rule.get('id', '').startswith('ob_log_disk'):
            continue
        from modules.pro.rule_engine import _resolve_param
        _params = {k: _resolve_param(v, _ctx([_row(1002, 96)], limit=95, recycle=80))
                   for k, v in (rule.get('params') or {}).items()}
        _ok, _err = _safe_eval(rule['condition'], _ctx([_row(1002, 96)]), _params)
        check(f"h {rule['id']} 条件求值无异常", _err == '', _err)


def test_context_iteration():
    """_SafeContext 若缺 __iter__，YAML 里的列表推导会死循环把巡检挂死。"""
    print('\n[2b] 规则引擎：context 可安全迭代')
    from modules.pro.rule_engine import _resolve_param, _SafeContext

    ctx = _ctx([_row(1002, 96, used=960, size=1000), _row(1002, 12)])
    got = _resolve_param("${max([float(u['USED_PCT']) for u in context.ob_log_disk])}", ctx)
    check('列表推导可迭代 context 列表', got == 96.0, repr(got))
    sc = _SafeContext({'a': 1})
    check('_SafeContext 支持 dict 迭代', sorted(list(sc)) == ['a'])
    check('_SafeContext 对非容器不炸', list(_SafeContext(None)) == [])


# ── 3. 报告层 ────────────────────────────────────────────────────────────────
def test_chapter():
    print('\n[3] 报告层：第 18 章（模板与报告章节号一致性）')
    from modules.inspection.init_db import OCEANBASE_DEFAULT_CHAPTERS, OCEANBASE_LOG_DISK_CHAPTER

    ch = OCEANBASE_DEFAULT_CHAPTERS[-1]
    check('章节已追加到末尾', ch is OCEANBASE_LOG_DISK_CHAPTER)
    check('章节号 18', ch.get('chapter_number') == 18, str(ch.get('chapter_number')))
    keys = [q['key'] for q in ch.get('queries') or []]
    check('章节含三个 query_key',
          keys == ['ob_log_disk', 'ob_log_disk_limit', 'ob_log_disk_recycle'], str(keys))
    check('章节标题中英齐备', bool(ch.get('chapter_title_zh')) and bool(ch.get('chapter_title_en')))
    check('章节号无重复', len({c['chapter_number'] for c in OCEANBASE_DEFAULT_CHAPTERS}) ==
          len(OCEANBASE_DEFAULT_CHAPTERS))
    # 关键一致性：模板只要还有一个空章节，报告（跳过空章节 + 连续重排）的章节号就会
    # 与模板错位。OB 曾因 4 个 MySQL 骨架空壳章节导致 22 → 18 的长期错位。
    _empty = [c['chapter_title_zh'] for c in OCEANBASE_DEFAULT_CHAPTERS if not c.get('queries')]
    check('模板不含空章节（否则报告会跳章重排）', not _empty, str(_empty))
    _nums = [c['chapter_number'] for c in OCEANBASE_DEFAULT_CHAPTERS]
    check('章节号连续 1..N', _nums == list(range(1, len(_nums) + 1)), str(_nums))
    check('日志盘为末章，编号 = 模板章数',
          ch.get('chapter_number') == len(OCEANBASE_DEFAULT_CHAPTERS),
          f"{ch.get('chapter_number')} vs {len(OCEANBASE_DEFAULT_CHAPTERS)}")
    # 章节里必须存**真实 SQL**（模板页展示的 = 实际执行的）；
    # v26.11.5.0 初版曾存占位 SQL，用户在模板页看到 `SELECT 'N/A' ...` 误以为
    # 巡检没按模板执行——已改为真实 SQL + 存量库迁移。
    from modules.inspection.init_db import OCEANBASE_LOG_DISK_PLACEHOLDER_SQL
    _markers = {
        'ob_log_disk': ['GV$OB_UNITS', 'USED_PCT', 'LOG_DISK_SIZE'],
        'ob_log_disk_limit': ['SHOW PARAMETERS', 'log_disk_utilization_limit_threshold'],
        'ob_log_disk_recycle': ['SHOW PARAMETERS', "LIKE 'log_disk_utilization_threshold'"],
    }
    for q in ch.get('queries') or []:
        check(f"章节 {q['key']} 存的是真实 SQL（非占位）",
              q['sql'].strip() != OCEANBASE_LOG_DISK_PLACEHOLDER_SQL.strip())
        for m in _markers.get(q['key'], []):
            check(f"章节 {q['key']} 的 SQL 含 {m}", m in q['sql'], q['sql'][:80])
    # 采集钩子与章节的 SQL 必须同源：钩子只对「缺失或仍是占位」的 key 注入同一份
    from modules.entrypoints.main_oceanbase import OceanBaseInspector
    import inspect as _inspect
    _src = _inspect.getsource(OceanBaseInspector._customize_queries)
    for q in ch.get('queries') or []:
        check(f"钩子含 {q['key']} 的兜底注入", q['key'] in _src and 'OCEANBASE_LOG_DISK_PLACEHOLDER_SQL' in _src)


# ── 4. 存量库幂等补齐 ────────────────────────────────────────────────────────
def test_migration():
    print('\n[4] 存量库：ensure_oceanbase_log_disk_chapter 幂等补齐')
    from modules.inspection.dal import (get_db_connection, get_default_template,
                                        ensure_oceanbase_log_disk_chapter)

    # 用真实库的副本模拟「存量老库」：dal 的播种把 init_default_templates 的
    # db_path 硬编码成了默认库，空库根本拿不到模板，只能复制现有库再删章。
    from modules.core.paths import PROJECT_ROOT
    src_db = os.path.join(str(PROJECT_ROOT), 'data', 'inspection.db')
    if not os.path.exists(src_db):
        print(f'  SKIP  未找到 {src_db}，跳过存量库补齐验证')
        return

    # 先对默认库跑一次幂等迁移（与启动时同款调用），保证副本里能拿到日志盘章节；
    # 该调用只做加法，不动任何已有章节/查询。
    from modules.inspection.dal import ensure_oceanbase_no_empty_chapters
    _pruned = ensure_oceanbase_no_empty_chapters()
    check('默认库空壳章节迁移可执行（返回 0 或 4）', _pruned in (0, 4), str(_pruned))
    seeded = ensure_oceanbase_log_disk_chapter()
    check('默认库幂等迁移可执行（返回 0 或 3）', seeded in (0, 3), str(seeded))

    tmpdir = tempfile.mkdtemp(prefix='dbcheck_ob_logdisk_')
    tmp_db = os.path.join(tmpdir, 'inspection.db')
    try:
        shutil.copy2(src_db, tmp_db)
        # 与启动流程同序：先剪空壳章节归一编号，再谈补齐（顺序反了编号会错位）
        from modules.inspection.dal import ensure_oceanbase_no_empty_chapters
        ensure_oceanbase_no_empty_chapters(db_path=tmp_db)
        tmpl = get_default_template('oceanbase', tmp_db)
        check('副本库有 OceanBase 默认模板', bool(tmpl))
        if not tmpl:
            return

        # 模拟「老库」：把日志盘章节连查询一起删掉（新装才有）
        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute(
            "SELECT c.id FROM inspection_chapter c JOIN inspection_query q ON q.chapter_id=c.id "
            "WHERE c.template_id=? AND q.query_key='ob_log_disk'", (tmpl['id'],))
        rows = cur.fetchall()
        check('副本库内确实存在日志盘章节（本次改动已播种）', bool(rows))
        for (cid,) in rows:
            cur.execute("DELETE FROM inspection_query WHERE chapter_id=?", (cid,))
            cur.execute("DELETE FROM inspection_chapter WHERE id=?", (cid,))
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM inspection_query q JOIN inspection_chapter c "
                    "ON q.chapter_id=c.id WHERE c.template_id=? AND q.query_key='ob_log_disk'",
                    (tmpl['id'],))
        check('删除后老库确实缺失该章节', cur.fetchone()[0] == 0)
        conn.close()

        n1 = ensure_oceanbase_log_disk_chapter(db_path=tmp_db)
        check('首次补齐返回 3 条', n1 == 3, str(n1))
        n2 = ensure_oceanbase_log_disk_chapter(db_path=tmp_db)
        check('再次调用幂等返回 0', n2 == 0, str(n2))

        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute("SELECT c.chapter_number, q.query_key FROM inspection_query q "
                    "JOIN inspection_chapter c ON q.chapter_id=c.id "
                    "WHERE c.template_id=? AND q.query_key LIKE 'ob_log_disk%' "
                    "ORDER BY q.query_key", (tmpl['id'],))
        got = cur.fetchall()
        conn.close()
        check('补齐后章节号为 18', bool(got) and all(r[0] == 18 for r in got), str(got))
        check('补齐后三个 key 齐全', [r[1] for r in got] ==
              ['ob_log_disk', 'ob_log_disk_limit', 'ob_log_disk_recycle'], str(got))
        conn.close()

        # ── 占位 SQL 迁移：模拟 v26.11.5.0 初版播种的老库（SQL 为占位）──
        from modules.inspection.init_db import (OCEANBASE_LOG_DISK_CHAPTER,
                                                OCEANBASE_LOG_DISK_PLACEHOLDER_SQL)
        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        for q in OCEANBASE_LOG_DISK_CHAPTER['queries']:
            cur.execute(
                "UPDATE inspection_query SET query_sql=? WHERE id IN ("
                " SELECT q.id FROM inspection_query q JOIN inspection_chapter c ON q.chapter_id=c.id"
                " WHERE c.template_id=? AND q.query_key=?)",
                (OCEANBASE_LOG_DISK_PLACEHOLDER_SQL, tmpl['id'], q['key']))
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM inspection_query q JOIN inspection_chapter c "
                    "ON q.chapter_id=c.id WHERE c.template_id=? AND q.query_key LIKE 'ob_log_disk%' "
                    "AND TRIM(q.query_sql)=?", (tmpl['id'], OCEANBASE_LOG_DISK_PLACEHOLDER_SQL.strip()))
        check('已把三个查询还原成老库的占位 SQL', cur.fetchone()[0] == 3)
        conn.close()

        n3 = ensure_oceanbase_log_disk_chapter(db_path=tmp_db)
        check('迁移调用幂等返回 0（key 已存在，不再插章）', n3 == 0, str(n3))

        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute("SELECT q.query_key, TRIM(q.query_sql) FROM inspection_query q "
                    "JOIN inspection_chapter c ON q.chapter_id=c.id "
                    "WHERE c.template_id=? AND q.query_key LIKE 'ob_log_disk%'", (tmpl['id'],))
        rows = dict(cur.fetchall())
        conn.close()
        check('迁移后三个 key 的 SQL 均不再是占位',
              all(v.strip() != OCEANBASE_LOG_DISK_PLACEHOLDER_SQL.strip() for v in rows.values()),
              str(rows))
        check('迁移后 SQL 与章节定义一致',
              all(rows.get(q['key'], '').strip() == q['sql'].strip()
                  for q in OCEANBASE_LOG_DISK_CHAPTER['queries']), str(rows))

        # ── [5] 空壳章节迁移：模拟「从 MySQL 骨架继承的 OB 老库」──
        # 老库有 4 个 0 查询的空壳章节（性能分析 / 复制状态检查 / 索引使用情况 /
        # 主从复制延迟），正是模板 22 章 ↔ 报告 18 章错位的根因。
        print('\n[5] 空壳章节迁移：ensure_oceanbase_no_empty_chapters')
        from modules.inspection.dal import ensure_oceanbase_no_empty_chapters
        from modules.inspection.init_db import OCEANBASE_PRUNABLE_CHAPTER_TITLES

        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        # 还原成老库形态：按「合并后的最终位置」逐位分配 1..22，
        # 4/7/13/14 位留给空壳章节，原有 18 章顺次落入其余位置。
        cur.execute("SELECT id, chapter_title_zh FROM inspection_chapter "
                    "WHERE template_id=? ORDER BY chapter_number", (tmpl['id'],))
        _rows = cur.fetchall()
        cur.executemany("UPDATE inspection_chapter SET chapter_number=? WHERE id=?",
                        [(100000 + i, r[0]) for i, r in enumerate(_rows)])
        _target = [4, 7, 13, 14]
        _ei = 0
        for _newno in range(1, 23):
            if _newno in _target:
                cur.execute("INSERT INTO inspection_chapter (template_id, chapter_number, "
                            "chapter_title_zh, chapter_title_en, description) VALUES (?,?,?,?,?)",
                            (tmpl['id'], _newno,
                             OCEANBASE_PRUNABLE_CHAPTER_TITLES[_target.index(_newno)],
                             'legacy empty chapter', 'legacy'))
            else:
                cur.execute("UPDATE inspection_chapter SET chapter_number=? WHERE id=?",
                            (_newno, _rows[_ei][0]))
                _ei += 1
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM inspection_chapter WHERE template_id=?", (tmpl['id'],))
        _total_old = cur.fetchone()[0]
        cur.execute("SELECT chapter_number FROM inspection_chapter WHERE template_id=? "
                    "AND chapter_title_zh='日志盘容量（Clog）'", (tmpl['id'],))
        _ld_old = (cur.fetchone() or [None])[0]
        conn.close()
        check('模拟老库：4 个空壳章节就位', _total_old == 22, str(_total_old))
        check('模拟老库：日志盘章节被挤到 22', _ld_old == 22, str(_ld_old))

        n4 = ensure_oceanbase_no_empty_chapters(db_path=tmp_db)
        check('删除空壳章节数为 4', n4 == 4, str(n4))
        n5 = ensure_oceanbase_no_empty_chapters(db_path=tmp_db)
        check('再次调用幂等返回 0', n5 == 0, str(n5))

        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute("SELECT chapter_number, chapter_title_zh FROM inspection_chapter "
                    "WHERE template_id=? ORDER BY chapter_number", (tmpl['id'],))
        _after = cur.fetchall()
        conn.close()
        _nums = [r[0] for r in _after]
        check('空壳章节已删除（22 → 18 章）', len(_after) == 18, str(len(_after)))
        check('章节号重排为连续 1..18', _nums == list(range(1, 19)), str(_nums))
        check('日志盘章节落到第 18 章（与报告一致）',
              _after[-1][1] == '日志盘容量（Clog）' and _after[-1][0] == 18, str(_after[-1]))

        # 用户自建空章节（标题不在白名单）必须保留
        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute("INSERT INTO inspection_chapter (template_id, chapter_number, "
                    "chapter_title_zh, chapter_title_en) VALUES (?,?,?,?)",
                    (tmpl['id'], 19, '我的自定义章节', 'My custom chapter'))
        conn.commit()
        conn.close()
        n6 = ensure_oceanbase_no_empty_chapters(db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM inspection_chapter WHERE template_id=? "
                    "AND chapter_title_zh='我的自定义章节'", (tmpl['id'],))
        _kept = cur.fetchone()[0]
        conn.close()
        check('用户自建空章节被保留', _kept == 1, str(_kept))
        check('保留时迁移返回 0（无空壳可删）', n6 == 0, str(n6))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print('=' * 68)
    print('OceanBase 日志盘（Clog）容量巡检 — 验证')
    print('=' * 68)
    test_collect_layer()
    test_rules()
    test_context_iteration()
    test_chapter()
    test_migration()

    print('\n' + '=' * 68)
    print(f'PASS {len(PASS)}  FAIL {len(FAIL)}')
    for name, detail in FAIL:
        print(f'  ✗ {name}  {detail}')
    print('=' * 68)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
