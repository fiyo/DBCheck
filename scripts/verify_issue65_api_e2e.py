# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>

"""端到端验证（进程内 test_client）：走真实 API 复现 #65 两个用户反馈场景。

场景 1：定时任务无「巡检人员」输入框 → 报告巡检人员恒为 Jack
场景 2：先建「每天 9:00」→ 改为「每周一 9:00」→ 旧配置仍在生效

用内存态的配置读写（monkeypatch scheduler 的 _load_jobs/_save_jobs），
不触碰真实的 data/scheduler_jobs.json。

运行：python scripts/verify_issue65_api_e2e.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.web import scheduler as sch

FAILS = []


def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (' | ' + detail if detail else ''))
    if not cond:
        FAILS.append(name)


# ── 隔离：内存态任务配置 ──
_orig_load, _orig_save = sch._load_jobs, sch._save_jobs
STATE = {'jobs': []}
sch._load_jobs = lambda: json.loads(json.dumps(STATE['jobs']))
sch._save_jobs = lambda jobs: STATE.__setitem__('jobs', json.loads(json.dumps(jobs)))

try:
    from modules.web.app import app
    app.config['TESTING'] = True
    client = app.test_client()
    jid = 'e2e-issue65'

    print('== 登录 ==')
    token = None
    for u, p in (('admin', 'admin123'), ('admin', 'admin123')):
        r = client.post('/api/auth/login', json={'username': u, 'password': p})
        d = r.get_json() or {}
        if d.get('ok'):
            token = d.get('token') or (d.get('data') or {}).get('token')
            if token:
                break
    if not token:
        print('  SKIP  登录失败，跳过端到端验证')
        sys.exit(0)
    H = {'Authorization': 'Bearer ' + token}
    print('  登录成功')

    print('\n== 场景 1：创建任务并传巡检人员 ==')
    r = client.post('/api/scheduler/jobs', headers=H, json={
        'id': jid, 'name': 'E2E 定时任务', 'db_type': 'mysql',
        'host': '127.0.0.1', 'port': 3306, 'user': 'root', 'password': 'x',
        'database': 'test', 'inspector_name': '李四', 'notify_on_done': False,
        'cron': {'hour': '9', 'minute': '0', 'day': '*'},
    })
    d = r.get_json() or {}
    check('创建任务成功', d.get('ok') is True, json.dumps(d, ensure_ascii=False)[:120])

    r = client.get('/api/scheduler/jobs', headers=H)
    jobs = (r.get_json() or {}).get('jobs') or []
    job = next((j for j in jobs if j.get('id') == jid), None)
    check('任务出现在列表中', job is not None)
    if job:
        check('巡检人员已按提交值保存', job.get('inspector_name') == '李四',
              str(job.get('inspector_name')))
        check('running 状态为 True（调度器里真实存在）', job.get('running') is True,
              'running=%s' % job.get('running'))
        check('cron 存为每天 9:00',
              (job.get('cron') or {}).get('hour') == '9'
              and (job.get('cron') or {}).get('day_of_week') in (None, '*'),
              json.dumps(job.get('cron'), ensure_ascii=False))

    print('\n== 场景 2：改为每周一 9:00 ==')
    r = client.put('/api/scheduler/jobs/' + jid, headers=H, json={
        'name': 'E2E 定时任务', 'db_type': 'mysql',
        'host': '127.0.0.1', 'port': 3306, 'user': 'root', 'password': 'x',
        'database': 'test', 'inspector_name': '李四', 'notify_on_done': False,
        'cron': {'day_of_week': 'mon', 'hour': '9', 'minute': '0'},
    })
    d = r.get_json() or {}
    check('更新接口返回 ok', d.get('ok') is True, json.dumps(d, ensure_ascii=False)[:120])

    sm = sch.get_scheduler()
    j = sm.scheduler.get_job(jid)
    f = {x.name: str(x) for x in j.trigger.fields} if j else {}
    check('调度器中的触发器已变为每周一', f.get('day_of_week') == 'mon', str(f))
    check('调度器中只有一个该任务（无重复注册）',
          len([x for x in sm.scheduler.get_jobs() if x.id == jid]) == 1)

    r = client.get('/api/scheduler/jobs', headers=H)
    job2 = next((x for x in ((r.get_json() or {}).get('jobs') or []) if x.get('id') == jid), None)
    if job2:
        check('列表中的 cron 已变为每周一',
              (job2.get('cron') or {}).get('day_of_week') == 'mon',
              json.dumps(job2.get('cron'), ensure_ascii=False))

    print('\n== 场景 3：禁用后到点不再触发 ==')
    r = client.post('/api/scheduler/jobs/%s/toggle' % jid, headers=H, json={'enabled': False})
    check('禁用接口返回 ok', (r.get_json() or {}).get('ok') is True)
    check('禁用后已从调度器摘除', sm.scheduler.get_job(jid) is None)
    r = client.get('/api/scheduler/jobs', headers=H)
    job3 = next((x for x in ((r.get_json() or {}).get('jobs') or []) if x.get('id') == jid), None)
    if job3:
        check('禁用后 running 变为 False', job3.get('running') is False,
              'running=%s' % job3.get('running'))

    print('\n== 场景 4：巡检人员缺省时取当前登录用户 ==')
    r = client.post('/api/scheduler/jobs', headers=H, json={
        'id': 'e2e-issue65-b', 'name': 'E2E 无巡检人员', 'db_type': 'mysql',
        'host': '127.0.0.1', 'port': 3306, 'user': 'root', 'password': 'x',
        'database': 'test', 'notify_on_done': False,
        'cron': {'hour': '10', 'minute': '0'},
    })
    r = client.get('/api/scheduler/jobs', headers=H)
    jobb = next((x for x in ((r.get_json() or {}).get('jobs') or []) if x.get('id') == 'e2e-issue65-b'), None)
    check('未填巡检人员时不落硬编码 Jack',
          jobb is not None and jobb.get('inspector_name') != 'Jack',
          str(jobb.get('inspector_name')) if jobb else 'None')
finally:
    sch._load_jobs, sch._save_jobs = _orig_load, _orig_save

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), '; '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')
