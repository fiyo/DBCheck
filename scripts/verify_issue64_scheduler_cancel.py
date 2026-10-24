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
check('中止后提示已禁用该任务', '禁用' in msg2, msg2)
sch._unregister_run('job-run')

print('[4b] 中止即禁用（到点不再自动启动）')
# 用临时配置文件做隔离测试，不动真实任务配置
import json
import tempfile

_orig_load = sch._load_jobs
_orig_save = sch._save_jobs
_tmp_path = None
try:
    _fd, _tmp_path = tempfile.mkstemp(suffix='.json')
    os.close(_fd)
    with open(_tmp_path, 'w', encoding='utf-8') as f:
        json.dump([{'id': 'job-d', 'name': 'D', 'enabled': True,
                    'db_info': {'db_type': 'mysql', 'host': 'h', 'port': 3306},
                    'cron': {'minute': '0'}}], f, ensure_ascii=False)

    def _load_stub():
        with open(_tmp_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save_stub(jobs):
        with open(_tmp_path, 'w', encoding='utf-8') as f:
            json.dump(jobs, f, ensure_ascii=False)

    sch._load_jobs = _load_stub
    sch._save_jobs = _save_stub

    class _FakeAPS2:
        def __init__(self):
            self.removed = []

        def get_jobs(self):
            return []

        def get_job(self, jid):
            return object()  # 假装调度器里有这个 job

        def remove_job(self, jid):
            self.removed.append(jid)

    sm_b = sch.SchedulerManager.__new__(sch.SchedulerManager)
    sm_b.scheduler = _FakeAPS2()
    h5 = sch._register_run('job-d')
    ok3, msg3 = sm_b.cancel_job('job-d')
    check('中止返回 True', ok3 is True, msg3)
    saved = _load_stub()
    check('中止后 enabled 被持久化为 False', saved[0].get('enabled') is False)
    check('任务已从调度器摘除（到点不再触发）', 'job-d' in sm_b.scheduler.removed)
    check('列表中 enabled 呈禁用', sm_b.list_jobs()[0].get('enabled') is False)
    sch._unregister_run('job-d')
finally:
    sch._load_jobs = _orig_load
    sch._save_jobs = _orig_save
    try:
        if _tmp_path and os.path.exists(_tmp_path):
            os.remove(_tmp_path)
    except Exception:
        pass

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
    check('执行中 enabled 仍为真实值（不伪造禁用）',
          item is not None and item.get('enabled') == jobs[0].get('enabled', True))
    check('不再输出 enabled_saved 字段', item is not None and 'enabled_saved' not in item)
    sch._unregister_run(jid)
    listed2 = sm2.list_jobs()
    item2 = next((j for j in listed2 if j['id'] == jid), None)
    check('结束后 executing=False', not item2.get('executing'))
    check('结束后 enabled 仍为真实值', item2.get('enabled') == jobs[0].get('enabled', True))

print('[6b] 执行中不再重复触发')
h6 = sch._register_run('job-dup')
sm3 = sch.SchedulerManager.__new__(sch.SchedulerManager)
started = []
sm3._job_func = lambda *a, **k: started.append(a)
# 直接调用原型方法验证跳过逻辑
orig = sch.SchedulerManager._job_func
orig(sm3, 'job-dup', {'db_type': 'mysql'}, 'tester', False)
check('已在执行时直接跳过（不启动线程）', not started)
sch._unregister_run('job-dup')

print('[7] API 路由存在')
try:
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'modules', 'web', 'app.py'), encoding='utf-8').read()
    check('已注册 /api/scheduler/jobs/<job_id>/cancel',
          "/api/scheduler/jobs/<job_id>/cancel" in src)
    check('已注册 cancel 方法', "def api_scheduler_cancel" in src)
except Exception as e:
    check('读取 app.py 失败', False, str(e))

print('[8] 禁用与中止语义区分（前端提示 + 后端不打断当前执行）')
html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'web_templates', 'index.html'), encoding='utf-8').read()
check('禁用按钮带悬浮说明', '禁用后到点不再自动启动' in html)
check('执行中禁用有明确提示文案', '当前这次巡检仍会执行完' in html)
check('提示引导使用「中止」', '想立即停止请点「中止」' in html)
check('状态徽标含「执行中 · 已禁用」组合态', '执行中 · 已禁用' in html)
check('中止按钮带说明', '立即结束当前这次巡检' in html)

# 后端：禁用只摘调度器，不触碰运行句柄（当前执行继续）
class _FakeAPS3:
    def __init__(self):
        self.removed = []

    def get_jobs(self):
        return []

    def get_job(self, jid):
        return object()

    def remove_job(self, jid):
        self.removed.append(jid)

_orig_load3 = sch._load_jobs
_orig_save3 = sch._save_jobs
_tmp3 = None
try:
    import tempfile
    _fd, _tmp3 = tempfile.mkstemp(suffix='.json')
    os.close(_fd)
    with open(_tmp3, 'w', encoding='utf-8') as f:
        json.dump([{'id': 'job-t', 'name': 'T', 'enabled': True,
                    'db_info': {'db_type': 'mysql', 'host': 'h', 'port': 3306},
                    'cron': {'minute': '0'}}], f, ensure_ascii=False)

    def _load3():
        with open(_tmp3, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save3(jobs):
        with open(_tmp3, 'w', encoding='utf-8') as f:
            json.dump(jobs, f, ensure_ascii=False)

    sch._load_jobs = _load3
    sch._save_jobs = _save3

    sm_c = sch.SchedulerManager.__new__(sch.SchedulerManager)
    sm_c.scheduler = _FakeAPS3()
    h6 = sch._register_run('job-t')
    sm_c.toggle_job('job-t', False)
    check('禁用后 enabled=False（持久化）', _load3()[0].get('enabled') is False)
    check('禁用后任务从调度器摘除（到点不再触发）', 'job-t' in sm_c.scheduler.removed)
    check('禁用不打断当前执行（取消信号未置位）', not h6.cancel_event.is_set())
    check('禁用不终止运行句柄（执行仍在跑）', sch.get_run_handle('job-t') is h6)
    sch._unregister_run('job-t')
finally:
    sch._load_jobs = _orig_load3
    sch._save_jobs = _orig_save3
    try:
        if _tmp3 and os.path.exists(_tmp3):
            os.remove(_tmp3)
    except Exception:
        pass

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), '; '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')