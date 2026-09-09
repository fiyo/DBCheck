# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck
"""
阶段 1（MCP 工具箱化 + 风险元数据）验收脚本。

用法：
    python scripts/verify_phase1.py            # 跑全部（纯逻辑 + 隔离集成 + 三步验证）
    python scripts/verify_phase1.py live       # 仅隔离集成（独立子进程，临时 data 目录，零污染）
    python scripts/verify_phase1.py steps      # 仅三步验证（compileall / import / discover_plugins）

覆盖点：
- 注册表含 7 个工具，每工具风险元数据齐全；阶段 1 全 read 且免审批
- build_tools() 输出含 MCP annotations + x-risk 扩展
- 写操作+需审批 → 中央门控返回 APPROVAL_REQUIRED（阶段 2 接入点）
- 可见性断言 + 审计在 MCP 派发链路生效（越权返回 RESOURCE_NOT_VISIBLE 并留痕）
- 三步验证：compileall → import modules.web.app → discover_plugins()==11
"""

import os
import sys
import json
import subprocess
import tempfile
import shutil
import traceback
import hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # D:/DBCheck
PY = sys.executable

EXPECTED_TOOLS = [
    "dbcheck.list_instances",
    "dbcheck.run_inspection",
    "dbcheck.slow_queries",
    "dbcheck.lock_tree",
    "dbcheck.index_health",
    "dbcheck.baseline_check",
    "dbcheck.ai_diagnose",
]


def _report(results):
    passed = sum(1 for _, ok, _ in results if ok is True)
    failed = sum(1 for _, ok, _ in results if ok is False)
    deferred = sum(1 for _, ok, _ in results if ok is None)
    print("\n==== 阶段 1 验收结果 ====")
    for name, ok, detail in results:
        tag = "PASS" if ok is True else ("DEFER" if ok is None else "FAIL")
        print(f"[{tag}] {name}" + (f" :: {detail}" if detail else ""))
    print(f"\n汇总：通过 {passed} / 失败 {failed} / 延后 {deferred}（共 {len(results)} 项）")
    return 0 if failed == 0 else 1


# ───────────────────────────────────────────── 纯逻辑矩阵
def pure_logic():
    sys.path.insert(0, ROOT)
    from modules.mcp_server.registry import get_tool_specs, get_tool_spec, list_tools_by_domain
    from modules.mcp_server.server import build_tools, dispatch_tool
    results = []

    def check(name, cond, detail=''):
        results.append((name, bool(cond), detail))

    specs = get_tool_specs()
    check("注册表含 7 个工具", len(specs) == 7, f"实际 {len(specs)}")
    check("工具名与规划一致",
          sorted(s["name"] for s in specs) == sorted(EXPECTED_TOOLS))
    for s in specs:
        risk = s["risk"]
        for k in ("risk_level", "access_mode", "requires_approval", "destructive",
                  "reversible", "runs_on", "side_effects"):
            check(f"风险元数据齐全: {s['name']}.{k}", k in risk)
        check(f"阶段1全 read 且免审批: {s['name']}",
              risk["access_mode"] == "read" and risk["requires_approval"] is False)
        check(f"annotations.readOnlyHint=True: {s['name']}",
              s["annotations"]["readOnlyHint"] is True)
        check(f"含 x-risk 扩展: {s['name']}", bool(s.get("x-risk")))

    # build_tools 产物校验
    tools = build_tools()
    check("build_tools 返回 7 个工具对象", len(tools) == 7)
    for t in tools:
        check(f"tools/list 含 annotations: {t['name']}", "annotations" in t)
        check(f"tools/list 含 x-risk: {t['name']}", "x-risk" in t)

    # 领域检索
    check("list_tools_by_domain('ai') 含 ai_diagnose",
          "dbcheck.ai_diagnose" in list_tools_by_domain("ai"))

    # 中央门控：写操作+需审批 → APPROVAL_REQUIRED
    import modules.mcp_server.registry as R
    fake = {
        "name": "dbcheck._fake_write", "title": "x", "domain": "x",
        "handler_key": "x", "description": "x", "tags": [],
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "risk": {
            "risk_level": "high", "access_mode": "write", "requires_approval": True,
            "destructive": True, "reversible": False, "runs_on": "db",
            "side_effects": "test", "tags": [],
        },
    }
    R._TOOL_SPECS.append(fake)
    try:
        res = dispatch_tool("dbcheck._fake_write", {}, None)
        check("写操作+需审批 → APPROVAL_REQUIRED",
              res.get("error_code") == "APPROVAL_REQUIRED", str(res.get("error_code")))
    finally:
        R._TOOL_SPECS.pop()

    # 未知工具
    res = dispatch_tool("dbcheck.nope", {}, None)
    check("未知工具 → unknown tool", "unknown tool" in res.get("error", ""))

    # 门控放行：read 工具免审批，自动派发（用一个真实 read 工具校验不返回 APPROVAL）
    res = dispatch_tool("dbcheck.list_instances", {}, None)
    check("read 工具不触发审批门控", res.get("error_code") != "APPROVAL_REQUIRED")
    return results


# ───────────────────────────────────────────── 隔离集成（临时 data 目录）
def run_live():
    sys.path.insert(0, ROOT)
    import sqlite3 as _sql
    import modules.core.paths as P
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix='dbcheck_p1_')
    data = os.path.join(tmp, 'data')
    os.makedirs(os.path.join(data, 'user_db'), exist_ok=True)
    os.makedirs(os.path.join(data, 'pro_data'), exist_ok=True)
    real_key = os.path.join(ROOT, 'data', '.db_key')
    if os.path.exists(real_key):
        shutil.copy(real_key, os.path.join(data, '.db_key'))

    # 必须在导入任何业务模块前重指向临时目录
    P.DATA_DIR = Path(data)
    P.PRO_DATA_DIR = Path(data) / 'pro_data'
    P.USER_DB_DIR = Path(data) / 'user_db'
    P.INSPECTION_DB = Path(data) / 'inspection.db'
    P.DOC_KB_DB = Path(data) / 'doc_kb.db'
    P.REPORTS_DIR = Path(data) / 'reports'
    P.BACKUPS_DIR = Path(data) / 'backups'
    P.LOG_DIR = Path(data) / 'logs'
    P.DB_KEY_PATH = Path(data) / '.db_key'

    sys.path.insert(0, ROOT)
    results = []
    S_id = None
    try:
        from modules.access_schema import ensure_schema
        from modules.user_management.models.db_manager import DBManager
        from modules.access import (get_principal, set_owner, remove_owner,
                                    ENTITY_INSTANCE, SCOPE_PRIVATE, Principal)
        from modules.pro import get_instance_manager
        from modules.pro.instance_manager import DatabaseInstance
        from modules.mcp_server.server import dispatch_tool, build_tools

        db = DBManager()
        ensure_schema(str(P.USER_DB_DIR / 'um_rbac.db'))

        d1 = db.query_one("SELECT id FROM um_department WHERE tenant_id=1 AND code='default'")['id']
        db.execute("INSERT INTO um_user(username,password,status,tenant_id,department_id,is_tenant_admin)"
                   " VALUES(?,?,?,?,?,?)", ('p1_admin', 'x', 1, 1, d1, 1))
        db.execute("INSERT INTO um_tenant(code,name) VALUES(?,?)", ('p1t2', 'P1 Tenant 2'))
        t2 = db.query_one("SELECT id FROM um_tenant WHERE code='p1t2'")['id']
        db.execute("INSERT INTO um_department(tenant_id,code,name) VALUES(?,?,?)",
                   (t2, 'p1d2', 'P1 Dept 2'))
        d2 = db.query_one("SELECT id FROM um_department WHERE tenant_id=? AND code='p1d2'", (t2,))['id']
        for uname, tid, did in (('p1_A', 1, d1), ('p1_B', 1, d1), ('p1_C', t2, d2)):
            db.execute("INSERT INTO um_user(username,password,status,tenant_id,department_id,is_tenant_admin)"
                       " VALUES(?,?,?,?,?,?)", (uname, 'x', 1, tid, did, 0))
        A_id = db.query_one("SELECT id FROM um_user WHERE username='p1_A'")['id']
        B_id = db.query_one("SELECT id FROM um_user WHERE username='p1_B'")['id']
        C_id = db.query_one("SELECT id FROM um_user WHERE username='p1_C'")['id']
        A = get_principal(A_id); B = get_principal(B_id); C = get_principal(C_id)

        im = get_instance_manager()
        S = DatabaseInstance(id='PHASE1TEST_S', name='PHASE1TEST_S', db_type='mysql',
                             host='127.0.0.1', port=1, user='u', password='p')
        r = im.add_instance(S)
        S_id = r.get('instance_id') or S.id
        sid = str(S_id)
        set_owner(ENTITY_INSTANCE, S_id, A, SCOPE_PRIVATE)

        # tools/list 经 MCP 派发生成，含 7 工具 + 风险元数据
        bt = build_tools()
        results.append(("L1 build_tools 含 7 工具且均带 x-risk",
                        len(bt) == 7 and all('x-risk' in t for t in bt), f"工具数={len(bt)}"))

        # A 可见，C 跨租户不可见
        ra = dispatch_tool('dbcheck.list_instances', {}, A)
        rb = dispatch_tool('dbcheck.list_instances', {}, B)
        rc = dispatch_tool('dbcheck.list_instances', {}, C)
        a_ids = [str(x['id']) for x in ra.get('instances', [])]
        c_ids = [str(x['id']) for x in rc.get('instances', [])]
        results.append(("L2 拥有者 A 的 list 含 S", sid in a_ids, f"A可见={len(a_ids)}"))
        results.append(("L2 跨租户 C 的 list 不含 S", sid not in c_ids, f"C可见={len(c_ids)}"))

        # C 越权调分析工具 → RESOURCE_NOT_VISIBLE（先于子进程，零 DB 连接）
        for tool in ('dbcheck.slow_queries', 'dbcheck.lock_tree',
                     'dbcheck.index_health', 'dbcheck.baseline_check',
                     'dbcheck.ai_diagnose'):
            rr = dispatch_tool(tool, {'instance_id': sid}, C)
            passed = (rr.get('ok') is False
                      and rr.get('error_code') == 'RESOURCE_NOT_VISIBLE')
            results.append((f"L3 越权 {tool} → RESOURCE_NOT_VISIBLE", passed,
                            str(rr.get('error_code'))))

        # A 调 read 工具不触发审批门控（可见性通过，进入 handler）
        rr = dispatch_tool('dbcheck.slow_queries', {'instance_id': sid}, A)
        results.append(("L4 拥有者 A 调 read 工具不返回 APPROVAL_REQUIRED",
                        rr.get('error_code') != 'APPROVAL_REQUIRED',
                        f"error_code={rr.get('error_code')}"))

        # 审计：C 的越权访问在 um_audit_log 留 deny 痕
        denies = db.query_all("SELECT * FROM um_audit_log WHERE resource_type='instance' "
                              "AND resource_id=? AND result='deny'", (sid,))
        results.append(("L5 越权访问审计留痕(deny)", len(denies) >= 1,
                        f"deny条数={len(denies)}"))
    except Exception:
        results.append(("LIVE-EXCEPTION", False, traceback.format_exc()[-1500:]))
    finally:
        try:
            db = DBManager()
            for u in ('p1_admin', 'p1_A', 'p1_B', 'p1_C'):
                db.execute("DELETE FROM um_user WHERE username=?", (u,))
            db.execute("DELETE FROM um_resource_owner WHERE entity_type='instance' "
                       "AND entity_id=?", (str(S_id) if S_id else 'none',))
            db.execute("DELETE FROM um_department WHERE code='p1d2'")
            db.execute("DELETE FROM um_tenant WHERE code='p1t2'")
            if S_id:
                try:
                    get_instance_manager().delete_instance(S_id)
                except Exception:
                    pass
                try:
                    remove_owner(ENTITY_INSTANCE, S_id)
                except Exception:
                    pass
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
    return results


# ───────────────────────────────────────────── 三步验证
def run_steps():
    import os as _os
    results = []
    env = dict(_os.environ)
    env['DBCHECK_ROOT'] = ROOT

    try:
        cp = subprocess.run([PY, '-m', 'compileall', '-q',
                             _os.path.join(ROOT, 'modules'),
                             _os.path.join(ROOT, 'web_ui.py')],
                            capture_output=True, text=True, cwd=ROOT)
        results.append(("C5-1 compileall 通过", cp.returncode == 0,
                        cp.stderr[-500:] if cp.returncode else ''))
    except Exception as e:
        results.append(("C5-1 compileall", False, str(e)))

    try:
        imp = subprocess.run([PY, '-c',
            'import sys,os; sys.path.insert(0, os.environ["DBCHECK_ROOT"]);'
            ' import modules.web.app; print("IMPORT_OK")'],
            capture_output=True, text=True, cwd=ROOT, timeout=240, env=env)
        ok = 'IMPORT_OK' in imp.stdout
        results.append(("C5-2 import modules.web.app 通过", ok,
                        imp.stderr[-800:] if not ok else ''))
    except Exception as e:
        results.append(("C5-2 import modules.web.app", False, str(e)))

    try:
        dp = subprocess.run([PY, '-c',
            'import sys,os; sys.path.insert(0, os.environ["DBCHECK_ROOT"]);'
            ' from modules.pluginkit.loader import discover_plugins;'
            ' print(len(discover_plugins()))'],
            capture_output=True, text=True, cwd=ROOT, timeout=120, env=env)
        out = dp.stdout.strip()
        n = int(out) if out.isdigit() else -1
        results.append(("C5-3 discover_plugins()==11", n == 11, f"返回 {n}"))
    except Exception as e:
        results.append(("C5-3 discover_plugins", False, str(e)))
    return results


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'

    def _extract_json(text):
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith('['):
                try:
                    return json.loads(line)
                except Exception:
                    pass
        return None

    if mode == 'live':
        print(json.dumps(run_live(), ensure_ascii=False))
    elif mode == 'steps':
        print(json.dumps(run_steps(), ensure_ascii=False))
    else:
        res = pure_logic()
        try:
            out = subprocess.check_output([PY, __file__, 'live'], cwd=ROOT, text=True)
            parsed = _extract_json(out)
            if parsed:
                res += parsed
            else:
                res.append(("LIVE-SUBPROCESS", False, "无法解析 live 输出"))
        except Exception as e:
            res.append(("LIVE-SUBPROCESS", False, str(e)))
        try:
            out2 = subprocess.check_output([PY, __file__, 'steps'], cwd=ROOT, text=True)
            parsed2 = _extract_json(out2)
            if parsed2:
                res += parsed2
            else:
                res.append(("STEPS-SUBPROCESS", False, "无法解析 steps 输出"))
        except Exception as e:
            res.append(("STEPS-SUBPROCESS", False, str(e)))
        sys.exit(_report(res))
