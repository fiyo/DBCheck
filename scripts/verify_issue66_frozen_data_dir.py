# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>

"""打包版运行时数据目录回归断言（#66）。

问题：frozen 下 PROJECT_ROOT = <exe>/_internal（因为 web_templates / db 都在其中），
      DATA_DIR 随之落在分发包内部 → 用户升级（重新解压 zip）时 _internal 被覆盖 →
      um_rbac.db（用户与密码）/ instances.db（数据源）/ .db_key（加密密钥）/
      inspection.db（巡检历史）/ scheduler_jobs.json 全部归零 →
      启动时 init_default_user 重建 admin → 表现为「重启后密码重置为初始密码」。

修复：资源根与数据根分离——frozen 下 DATA_ROOT = exe 同级（与分发包分离），
      首次启动由 ensure_data_dir_migrated() 把旧数据一次性迁出（幂等）。

本脚本在**子进程**中模拟 frozen 环境（sys.frozen=True + sys.executable 指向
临时目录），验证路径选择与迁移行为，不影响真实数据。

运行：python scripts/verify_issue66_frozen_data_dir.py
"""

import os
import subprocess
import sys
import tempfile
import textwrap

FAILS = []


def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (' | ' + detail if detail else ''))
    if not cond:
        FAILS.append(name)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_child(code, env_extra=None):
    """在子进程中跑代码，返回 (stdout, returncode)"""
    env = os.environ.copy()
    env['PYTHONPATH'] = ROOT
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, '-c', textwrap.dedent(code)],
                       capture_output=True, text=True, timeout=120, env=env,
                       cwd=ROOT)
    return p.stdout.strip(), p.returncode, p.stderr


def make_fake_install(base):
    """构造一个仿真的打包版目录结构"""
    exe_dir = os.path.join(base, 'RaccoonX')
    internal = os.path.join(exe_dir, '_internal')
    os.makedirs(os.path.join(internal, 'web_templates'), exist_ok=True)
    os.makedirs(os.path.join(internal, 'db'), exist_ok=True)
    # 旧版运行时数据（在被覆盖的分发包内部）
    data_dir = os.path.join(internal, 'data')
    os.makedirs(os.path.join(data_dir, 'user_db'), exist_ok=True)
    with open(os.path.join(data_dir, 'user_db', 'um_rbac.db'), 'wb') as f:
        f.write(b'FAKE_RBAC_DB')
    with open(os.path.join(data_dir, '.db_key'), 'w', encoding='utf-8') as f:
        f.write('FAKE_KEY')
    with open(os.path.join(data_dir, 'scheduler_jobs.json'), 'w', encoding='utf-8') as f:
        f.write('[]')
    with open(os.path.join(exe_dir, 'dbcheck.exe'), 'wb') as f:
        f.write(b'MZ')
    return exe_dir, internal, data_dir


print('[1] frozen 下资源根与数据根分离')
with tempfile.TemporaryDirectory() as base:
    exe_dir, internal, old_data = make_fake_install(base)
    out, rc, err = run_child("""
        import sys
        sys.frozen = True
        sys.executable = r'{exe}\\dbcheck.exe'
        import modules.core.paths as p
        print('PROJECT_ROOT=', p.PROJECT_ROOT)
        print('DATA_ROOT=', p.DATA_ROOT)
        print('DATA_DIR=', p.DATA_DIR)
        print('USER_DB_DIR=', p.USER_DB_DIR)
        print('DB_KEY_PATH=', p.DB_KEY_PATH)
    """.format(exe=exe_dir))
    print(out)
    lines = dict((k.strip(), v.strip()) for k, v in
                 (l.split('=', 1) for l in out.splitlines() if '=' in l))
    check('资源根指向 _internal（只读资源）',
          lines.get('PROJECT_ROOT', '').replace('\\', '/').endswith('_internal'),
          lines.get('PROJECT_ROOT', ''))
    check('数据根指向 exe 同级（与分发包分离）',
          lines.get('DATA_ROOT', '').replace('\\', '/').rstrip('/').endswith('RaccoonX'),
          lines.get('DATA_ROOT', ''))
    check('用户库目录在 exe 同级/data/user_db',
          'user_db' in lines.get('USER_DB_DIR', '').replace('\\', '/')
          and '_internal' not in lines.get('USER_DB_DIR', '').replace('\\', '/'),
          lines.get('USER_DB_DIR', ''))
    check('加密密钥路径在 exe 同级/data',
          '_internal' not in lines.get('DB_KEY_PATH', '').replace('\\', '/'),
          lines.get('DB_KEY_PATH', ''))

print('\n[2] 旧数据一次性迁出（升级不丢数据）')
with tempfile.TemporaryDirectory() as base:
    exe_dir, internal, old_data = make_fake_install(base)
    out, rc, err = run_child("""
        import sys
        sys.frozen = True
        sys.executable = r'{exe}\\dbcheck.exe'
        import modules.core.paths as p
        moved = p.ensure_data_dir_migrated()
        print('moved=', moved)
        print('new_exists=', (p.DATA_DIR / 'user_db' / 'um_rbac.db').exists())
        print('key_exists=', (p.DATA_DIR / '.db_key').exists())
        print('sched_exists=', (p.DATA_DIR / 'scheduler_jobs.json').exists())
        print('old_gone=', not (p.PROJECT_ROOT / 'data' / 'user_db' / 'um_rbac.db').exists())
    """.format(exe=exe_dir))
    print(out)
    lines = dict((k.strip(), v.strip()) for k, v in
                 (l.split('=', 1) for l in out.splitlines() if '=' in l))
    check('迁移被触发', lines.get('moved') == 'True', lines.get('moved', ''))
    check('用户库已搬到 exe 同级', lines.get('new_exists') == 'True')
    check('加密密钥已搬（历史数据源密码仍可解密）', lines.get('key_exists') == 'True')
    check('定时任务配置已搬', lines.get('sched_exists') == 'True')
    check('旧位置已清空（避免下次又被分发包覆盖）', lines.get('old_gone') == 'True')

print('\n[3] 迁移幂等（已迁移后不重复搬、不覆盖新数据）')
with tempfile.TemporaryDirectory() as base:
    exe_dir, internal, old_data = make_fake_install(base)
    out, rc, err = run_child("""
        import sys, os
        sys.frozen = True
        sys.executable = r'{exe}\\dbcheck.exe'
        import modules.core.paths as p
        p.ensure_data_dir_migrated()          # 第一次：搬迁
        # 模拟用户在新位置又改了数据
        with open(p.DATA_DIR / 'user_db' / 'um_rbac.db', 'wb') as f:
            f.write(b'USER_NEW_DATA')
        moved2 = p.ensure_data_dir_migrated()  # 第二次：应跳过
        print('moved2=', moved2)
        with open(p.DATA_DIR / 'user_db' / 'um_rbac.db', 'rb') as f:
            print('content=', f.read())
    """.format(exe=exe_dir))
    print(out)
    lines = dict((k.strip(), v.strip()) for k, v in
                 (l.split('=', 1) for l in out.splitlines() if '=' in l))
    check('第二次迁移跳过', lines.get('moved2') == 'False', lines.get('moved2', ''))
    check('用户新数据未被覆盖', 'USER_NEW_DATA' in (lines.get('content') or ''),
          lines.get('content', ''))

print('\n[4] 开发版（非 frozen）行为不变')
out, rc, err = run_child("""
    import modules.core.paths as p
    print('frozen=', getattr(__import__('sys'), 'frozen', False))
    print('same=', p.DATA_ROOT == p.PROJECT_ROOT)
    print('data_dir=', p.DATA_DIR)
""")
lines = dict((k.strip(), v.strip()) for k, v in
             (l.split('=', 1) for l in out.splitlines() if '=' in l))
check('开发版数据根 = 项目根', lines.get('same') == 'True', lines.get('data_dir', ''))
check('开发版不触发 frozen 迁移', lines.get('frozen') == 'False', lines.get('frozen', ''))

print('\n[5] 关键路径全部落在数据根下')
out, rc, err = run_child("""
    import modules.core.paths as p
    for name in ('DATA_DIR', 'PRO_DATA_DIR', 'REPORTS_DIR', 'USER_DB_DIR', 'LOG_DIR',
                 'SCHEDULER_JOBS', 'INSPECTION_DB', 'DB_KEY_PATH', 'DOC_KB_DB'):
        v = getattr(p, name)
        print(name, '=', v, '| under_data=', str(v).startswith(str(p.DATA_DIR)))
""")
all_under = True
for l in out.splitlines():
    if 'under_data=' in l:
        if l.strip().endswith('under_data=False'):
            all_under = False
print(out)
check('所有运行时路径都在 DATA_DIR 之下', all_under)

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), '; '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')