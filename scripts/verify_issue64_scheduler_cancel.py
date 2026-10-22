# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>

"""定时巡检「中止当前执行」回归断言（#64）。

运行：python scripts/verify_issue64_scheduler_cancel.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.web import scheduler as sch

FAILS = []


def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (' | ' + detail if detail else ''))
    if not cond:
        FAILS.append(name)


class FakeProc:
    """模拟子进程句柄"""

    def __init__(self, alive=True):
        self.alive = alive
        self.pid = 123456
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.killed = True
        self.alive = False


print('[1] 运行登记表')
sch._unregister_run('job-x')
handle = sch._register_run('job-x')
check('登记后可取到句柄', sch.get_run_handle('job-x') is handle)
snap = sch.running_snapshot()
check('概览含该任务', 'job-x' in snap)
check('概览带 started_at', 'started_at' in snap.get('job-x', {}))
sch._unregister_run('job-x')
check('注销后取不到句柄', sch.get_run_handle('job-x') is None)
check('注销后概览不含', 'job-x' not in sch.running_snapshot())

print('[2] 取消信号与子进程终止')
h = sch._register_run('job-y')
proc = FakeProc()
h.proc = proc
killed = h.cancel()
check('cancel 置位信号', h.cancel_event.is_set())
check('持有子进程时判定为已终止', killed is True)

h2 = sch._register_run('job-z')
check('无子进程时返回 False（需等待调用返回）', h2.cancel() is False)
check('无子进程时信号仍置位', h2.cancel_event.is_set())

print('[3] 阶段检查点')
h3 = sch._register_run('job-w')
sch._check_cancelled('job-w', h3, '测试阶段')   # 未取消 → 不抛
check('未取消时检查点放行', True)
h3.cancel_event.set()
raised = False
try:
    sch._check_cancelled('job-w', h3, '测试阶段')
except sch.InspectionCancelled:
    raised = True
check('已取消时检查点抛 InspectionCancelled', raised)
for jid in ('job-y', 'job-z', 'job-w'):
    sch._unregister_run(jid)

print('[4] SchedulerManager.cancel_job')
sm = sch.SchedulerManager.__new__(sch.SchedulerManager)   # 不触发 APScheduler 启动
ok, msg = sm.cancel_job('job-none')
check('未执行的任务返回 False', ok is False, msg)
check('未执行给出明确说明', '未在执行' in msg, msg)

h4 = sch._register_run('job-run')
ok2, msg2 = sm.cancel_job('job-run')
check('执行中的任务返回 True', ok2 is True, msg2)
check('执行中已置取消信号', h4.cancel_event.is_set())
check('原生驱动给出等待说明', '数据库' in msg2 or '子进程' in msg2, msg2)
sch._unregister_run('job-run')

print('[5] 排队阶段可中断（不占用执行名额）')
sem_before = sch._inspection_semaphore._value
h5 = sch._register_run('job-q')
h5.cancel_event.set()
sch._run_inspection('job-q', {'db_type': 'mysql', 'host': 'x'}, 'tester', False, handle=h5)
check('已取消时立即返回不执行', True)
check('信号量未被占用', sch._inspection_semaphore._value == sem_before,
      'before=%s after=%s' % (sem_before, sch._inspection_semaphore._value))
sch._unregister_run('job-q')

print('[6] list_jobs 暴露执行中状态')
jobs = sch._load_jobs()
if not jobs:
    print('  SKIP  无本地任务配置，跳过 list_jobs 断言')
else:
    class _FakeAPS:
        def get_jobs(self):
            return []

    sm2 = sch.SchedulerManager.__new__(sch.SchedulerManager)
    sm2.scheduler = _FakeAPS()
    jid = jobs[0]['id']
    sch._register_run(jid)
    listed = sm2.list_jobs()
    item = next((j for j in listed if j['id'] == jid), None)
    check('list_jobs 含 executing 字段', item is not None and 'executing' in item)
    check('执行中时 executing=True', bool(item and item.get('executing')))
    sch._unregister_run(jid)
    listed2 = sm2.list_jobs()
    item2 = next((j for j in listed2 if j['id'] == jid), None)
    check('结束后 executing=False', not item2.get('executing'))

print('[7] API 路由存在')
try:
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'modules', 'web', 'app.py'), encoding='utf-8').read()
    check('已注册 /api/scheduler/jobs/<job_id>/cancel',
          "/api/scheduler/jobs/<job_id>/cancel" in src)
    check('已注册 cancel 方法', "def api_scheduler_cancel" in src)
except Exception as e:
    check('读取 app.py 失败', False, str(e))

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), '; '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')