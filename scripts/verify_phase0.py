# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck
"""
阶段 0（多租户数据隔离底座）验收脚本 —— 对照规划文档 3.4 六条标准。

用法：
    python scripts/verify_phase0.py            # 跑全部（纯逻辑 + 隔离集成 + 三步验证）
    python scripts/verify_phase0.py live       # 仅隔离集成（独立子进程，临时 data 目录，零污染）
    python scripts/verify_phase0.py steps      # 仅三步验证（compileall / import / discover_plugins）

说明：
- 纯逻辑矩阵 + 隔离集成（live）覆盖 标准 1~4；
- steps 覆盖 标准 5（compileall -> import web.app -> discover_plugins()==11）；
- 标准 6（删除数据源走 pending_approval）属阶段 2 治理闭环，本脚本标注为 DEFER，
  删除操作当前已按 owner/admin 做写门控（assert_visible 写判定）。
"""

import os
import sys
import json
import subprocess
import tempfile
import shutil
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # D:/DBCheck
PY = sys.executable


def _report(results):
    passed = sum(1 for _, ok, _ in results if ok is True)
    failed = sum(1 for _, ok, _ in results if ok is False)
    deferred = sum(1 for _, ok, _ in results if ok is None)
    print("\n==== 阶段 0 验收结果 ====")
    for name, ok, detail in results:
        tag = "PASS" if ok is True else ("DEFER" if ok is None else "FAIL")
        print(f"[{tag}] {name}" + (f" :: {detail}" if detail else ""))
    print(f"\n汇总：通过 {passed} / 失败 {failed} / 延后 {deferred}（共 {len(results)} 项）")
    return 0 if failed == 0 else 1


# ───────────────────────────────────────────── 纯逻辑矩阵（标准 1/2 算法）
def pure_logic():
    sys.path.insert(0, ROOT)
    from modules.access import Principal, visible_to, can
    results = []

    def check(name, cond, detail=''):
        results.append((name, bool(cond), detail))

    A = Principal(id=1, username='A', tenant_id=1, department_id=1)
    B = Principal(id=2, username='B', tenant_id=1, department_id=1)
    C = Principal(id=3, username='C', tenant_id=2, department_id=9)
    admin = Principal(id=99, username='admin', tenant_id=1, department_id=1,
                     is_tenant_admin=True)
    S_priv = {'owner_user_id': 1, 'owner_tenant_id': 1,
              'owner_department_id': 1, 'scope': 'private'}
    S_dept = dict(S_priv); S_dept['scope'] = 'department'
    S_ent = dict(S_priv); S_ent['scope'] = 'enterprise'

    # 标准 1：私有 -> 同部门不可见、跨租户更不可见
    check("C1 私有：同部门 B 不可见", not visible_to(B, S_priv))
    check("C1 私有：跨租户 C 不可见", not visible_to(C, S_priv))
    # 标准 2：部门级 -> 同部门可见、跨部门/跨租户不可见
    check("C2 部门级：同部门 B 可见", visible_to(B, S_dept))
    check("C2 部门级：跨租户 C 不可见", not visible_to(C, S_dept))
    # 企业级：租户内可见，但跨租户硬边界仍生效
    check("企业级：同租户 B 可见", visible_to(B, S_ent))
    check("企业级：跨租户 C 仍不可见（租户硬边界）", not visible_to(C, S_ent))
    # 管理员：本租户内全可见，但跨租户不可见
    check("管理员：可见同租户私有", visible_to(admin, S_priv))
    check("管理员：不可见跨租户（租户硬边界）",
          not visible_to(admin, {'owner_user_id': 3, 'owner_tenant_id': 2,
                                 'owner_department_id': 9, 'scope': 'private'}))
    # can()：写操作要求拥有者/管理员
    check("写操作：非拥有者被拒", not can(B, 'delete', S_priv))
    check("写操作：拥有者允许", can(A, 'delete', S_priv))
    check("写操作：管理员允许", can(admin, 'delete', S_priv))
    return results


# ───────────────────────────────────────────── 隔离集成（标准 1~4，临时 data 目录）
def run_live():
    sys.path.insert(0, ROOT)
    import sqlite3 as _sql
    import modules.core.paths as P
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix='dbcheck_p0_')
    data = os.path.join(tmp, 'data')
    os.makedirs(os.path.join(data, 'user_db'), exist_ok=True)
    os.makedirs(os.path.join(data, 'pro_data'), exist_ok=True)
    # 拷贝真实 .db_key 以便实例密码加密可用
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
        from modules.access import (get_principal, filter_visible, set_owner,
                                    remove_owner, ENTITY_INSTANCE, SCOPE_PRIVATE,
                                    SCOPE_DEPARTMENT, Principal)
        from modules.pro import get_instance_manager
        from modules.pro.instance_manager import DatabaseInstance
        from modules.mcp_server.auth import resolve_principal
        from modules.mcp_server.tools import run_inspection_tool
        import hashlib

        db = DBManager()  # 触发 schema.sql + ensure_schema（命中临时库）
        ensure_schema(str(P.USER_DB_DIR / 'um_rbac.db'))

        d1 = db.query_one("SELECT id FROM um_department WHERE tenant_id=1 AND code='default'")['id']
        # 引导管理员（让 A/B/C 成为普通用户）
        db.execute("INSERT INTO um_user(username,password,status,tenant_id,department_id,is_tenant_admin)"
                   " VALUES(?,?,?,?,?,?)", ('p0_admin', 'x', 1, 1, d1, 1))
        # 租户2 + 部门2（给跨租户用户 C）
        db.execute("INSERT INTO um_tenant(code,name) VALUES(?,?)", ('p0t2', 'P0 Tenant 2'))
        t2 = db.query_one("SELECT id FROM um_tenant WHERE code='p0t2'")['id']
        db.execute("INSERT INTO um_department(tenant_id,code,name) VALUES(?,?,?)",
                   (t2, 'p0d2', 'P0 Dept 2'))
        d2 = db.query_one("SELECT id FROM um_department WHERE tenant_id=? AND code='p0d2'", (t2,))['id']

        for uname, tid, did in (('p0_A', 1, d1), ('p0_B', 1, d1), ('p0_C', t2, d2)):
            db.execute("INSERT INTO um_user(username,password,status,tenant_id,department_id,is_tenant_admin)"
                       " VALUES(?,?,?,?,?,?)", (uname, 'x', 1, tid, did, 0))
        A_id = db.query_one("SELECT id FROM um_user WHERE username='p0_A'")['id']
        B_id = db.query_one("SELECT id FROM um_user WHERE username='p0_B'")['id']
        C_id = db.query_one("SELECT id FROM um_user WHERE username='p0_C'")['id']
        A = get_principal(A_id); B = get_principal(B_id); C = get_principal(C_id)

        im = get_instance_manager()
        S = DatabaseInstance(id='PHASE0TEST_S', name='PHASE0TEST_S', db_type='mysql',
                             host='127.0.0.1', port=3306, user='u', password='p')
        r = im.add_instance(S)
        S_id = r.get('instance_id') or S.id
        sid = str(S_id)

        # 标准 1：私有
        set_owner(ENTITY_INSTANCE, S_id, A, SCOPE_PRIVATE)
        rows = im.get_all_instances(mask_password=True)
        vb = [str(x['id']) for x in filter_visible(B, rows, ENTITY_INSTANCE)]
        vc = [str(x['id']) for x in filter_visible(C, rows, ENTITY_INSTANCE)]
        results.append(("C1 隔离-私有：同部门 B 列表不含 S", sid not in vb, f"B可见={len(vb)}"))
        results.append(("C1 隔离-私有：跨租户 C 列表不含 S", sid not in vc, f"C可见={len(vc)}"))

        # 标准 2：升部门级
        set_owner(ENTITY_INSTANCE, S_id, A, SCOPE_DEPARTMENT)
        vb = [str(x['id']) for x in filter_visible(B, rows, ENTITY_INSTANCE)]
        vc = [str(x['id']) for x in filter_visible(C, rows, ENTITY_INSTANCE)]
        results.append(("C2 隔离-部门：同部门 B 列表含 S", sid in vb, f"B可见={len(vb)}"))
        results.append(("C2 隔离-部门：跨租户 C 列表不含 S", sid not in vc, f"C可见={len(vc)}"))

        # 标准 3：MCP 身份绑定 + 过滤（先回到私有，确保 B 越权）
        set_owner(ENTITY_INSTANCE, S_id, A, SCOPE_PRIVATE)
        # 临时库里建 api_keys 表（与 modules/web/api.py 同构，含 user_id）
        import sqlite3 as _sql
        _ak = str(P.PRO_DATA_DIR / 'api_keys.db')
        _c = _sql.connect(_ak)
        _c.execute("""CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT,
            is_active INTEGER DEFAULT 1, user_id INTEGER)""")
        _c.commit(); _c.close()
        raw_key = 'p0_test_key_B_' + str(B_id)
        khash = hashlib.sha256(raw_key.encode()).hexdigest()
        _c = _sql.connect(_ak)
        _c.execute("INSERT INTO api_keys(id,name,key_hash,key_prefix,created_at,user_id)"
                   " VALUES(?,?,?,?,?,?)", ('p0kB', 'p0keyB', khash, raw_key[:10], '2026-01-01', B_id))
        _c.commit(); _c.close()
        okp, principal, reason = resolve_principal(raw_key)
        results.append(("C3 MCP：Key 解析出身份 B", okp and principal is not None and principal.id == B_id, reason))
        vb2 = [str(x['id']) for x in filter_visible(principal, rows, ENTITY_INSTANCE)]
        results.append(("C3a MCP：B 的 list 不含 S", sid not in vb2, f"B可见={len(vb2)}"))
        rr = run_inspection_tool(S_id, principal=principal)
        results.append(("C3b MCP：run_inspection(S) 返回 RESOURCE_NOT_VISIBLE",
                        (rr.get('ok') is False and rr.get('error_code') == 'RESOURCE_NOT_VISIBLE'),
                        str(rr.get('error_code'))))

        # 标准 4：审计留痕（越权访问写了 deny 行）
        audits = db.query_all("SELECT * FROM um_audit_log WHERE resource_type='instance'"
                              " AND resource_id=? AND result='deny'", (sid,))
        results.append(("C4 审计：越权访问在 um_audit_log 留痕(deny)", len(audits) >= 1, f"deny条数={len(audits)}"))
    except Exception:
        results.append(("LIVE-EXCEPTION", False, traceback.format_exc()[-1500:]))
    finally:
        try:
            db = DBManager()
            for u in ('p0_admin', 'p0_A', 'p0_B', 'p0_C'):
                db.execute("DELETE FROM um_user WHERE username=?", (u,))
            db.execute("DELETE FROM um_resource_owner WHERE entity_type='instance'"
                       " AND entity_id=?", (str(S_id) if S_id else 'none',))
            try:
                _cc = _sql.connect(str(P.PRO_DATA_DIR / 'api_keys.db'))
                _cc.execute("DELETE FROM api_keys WHERE name='p0keyB'")
                _cc.commit(); _cc.close()
            except Exception:
                pass
            db.execute("DELETE FROM um_department WHERE code='p0d2'")
            db.execute("DELETE FROM um_tenant WHERE code='p0t2'")
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


# ───────────────────────────────────────────── 三步验证（标准 5）
def run_steps():
    import os as _os
    results = []
    env = dict(_os.environ)
    env['DBCHECK_ROOT'] = ROOT

    # step1 compileall
    try:
        cp = subprocess.run([PY, '-m', 'compileall', '-q',
                             _os.path.join(ROOT, 'modules'),
                             _os.path.join(ROOT, 'web_ui.py')],
                            capture_output=True, text=True, cwd=ROOT)
        results.append(("C5-1 compileall 通过", cp.returncode == 0,
                        cp.stderr[-500:] if cp.returncode else ''))
    except Exception as e:
        results.append(("C5-1 compileall", False, str(e)))

    # step2 import web.app
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

    # step3 discover_plugins()==11
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
    if mode == 'live':
        print(json.dumps(run_live(), ensure_ascii=False))
    elif mode == 'steps':
        print(json.dumps(run_steps(), ensure_ascii=False))
    else:
        def _extract_json(text):
            for line in reversed(text.splitlines()):
                line = line.strip()
                if line.startswith('['):
                    try:
                        return json.loads(line)
                    except Exception:
                        pass
            return None

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
        # 标准 6 归属阶段 2
        res.append(("C6 删除数据源走 pending_approval", None,
                    "阶段 2 治理闭环；当前删除已按 owner/admin 做写门控(assert_visible 写判定)"))
        sys.exit(_report(res))
