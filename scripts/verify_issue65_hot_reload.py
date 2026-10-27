# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>

"""定时任务配置热加载回归断言（#65 附带能力）。

场景：用户手工编辑 data/scheduler_jobs.json 后，调度器应自动跟随，
      不再出现「界面显示已改、实际仍按老时间跑」的不一致。

三条生效路径：
  ① 后台守护线程（30s 轮询 mtime）
  ② 触发前自检（下次触发即用新配置）
  ③ 显式重载接口 POST /api/scheduler/reload

运行：python scripts/verify_issue65_hot_reload.py
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apscheduler.schedulers.background import BackgroundScheduler
from modules.web import scheduler as sch

FAILS = []


def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (' | ' + detail if detail else ''))
    if not cond:
        FAILS.append(name)


def fields(job):
    return {x.name: str(x) for x in job.trigger.fields} if job else {}


_fd, _tmp = tempfile.mkstemp(suffix='.json')
os.close(_fd)
_orig_cfg, _orig_mtime = sch.CONFIG_FILE, getattr(sch, '_config_mtime', None)
sch.CONFIG_FILE = _tmp


def write(cfgs):
    with open(_tmp, 'w', encoding='utf-8') as f:
        json.dump(cfgs, f, ensure_ascii=False, indent=2)


def cfg_of(jid='job-h'):
    return {
        'id': jid, 'name': '热加载任务', 'enabled': True,
        'inspector_name': '王五', 'notify_on_done': False,
        'cron': {'hour': '9', 'minute': '0', 'day': '*'},
        'db_info': {'db_type': 'mysql', 'host': 'h', 'port': 3306,
                    'user': 'u', 'password': 'p', 'database': 'd'},
    }


sm = None
try:
    CFG = cfg_of()
    write([CFG])
    sm = sch.SchedulerManager.__new__(sch.SchedulerManager)
    sm.scheduler = BackgroundScheduler(daemon=True)
    sm.scheduler.start()
    sm._config_mtime = sch._config_mtime()
    sm.add_job(CFG, restore=True)
    j0 = sm.scheduler.get_job('job-h')
    check('初始注册为每天 9:00', fields(j0).get('day_of_week') == '*', str(fields(j0)))

    print('\n[1] 显式重载接口路径（reload_from_disk）')
    time.sleep(0.05)
    new = cfg_of()
    new['cron'] = {'day_of_week': 'mon', 'hour': '9', 'minute': '0'}
    new['name'] = '改名后的任务'
    write([new])
    sm.reload_from_disk()
    j1 = sm.scheduler.get_job('job-h')
    check('重载后触发器变为每周一', fields(j1).get('day_of_week') == 'mon', str(fields(j1)))
    check('重载后 job 名称已更新', j1.name == '改名后的任务', j1.name)
    check('重载后仍只有一个任务', len(sm.scheduler.get_jobs()) == 1,
          'jobs=%d' % len(sm.scheduler.get_jobs()))

    print('\n[2] 触发前自检路径（_job_func 执行前比对 mtime）')
    time.sleep(0.05)
    new2 = cfg_of()
    new2['cron'] = {'day_of_week': 'fri', 'hour': '20', 'minute': '30'}
    new2['inspector_name'] = '赵六'
    write([new2])
    check('改文件后 mtime 变化被识别', sm._config_changed() is True)
    captured = {}
    orig_core = sch._run_inspection_core

    def _capture(job_id, db_info, inspector_name, notify_on_done, handle=None):
        captured.update({'job_id': job_id, 'db_info': db_info,
                         'inspector_name': inspector_name,
                         'notify_on_done': notify_on_done})
    sch._run_inspection_core = _capture
    try:
        sm._job_func('job-h', {'db_type': 'mysql', 'host': 'OLD-HOST'},
                     'Jack', False)
        # 巡检在子线程中执行，等待其完成（最多 5 秒）
        _deadline = time.time() + 5
        while not captured and time.time() < _deadline:
            time.sleep(0.05)
    finally:
        sch._run_inspection_core = orig_core
    check('巡检已按新配置启动', bool(captured), 'captured=%s' % json.dumps(captured, ensure_ascii=False))
    check('触发时使用了文件里的新巡检人员', captured.get('inspector_name') == '赵六',
          str(captured.get('inspector_name')))
    check('触发时使用了文件里的新连接信息',
          (captured.get('db_info') or {}).get('host') == 'h',
          json.dumps(captured.get('db_info'), ensure_ascii=False))
    check('触发后 mtime 基线已更新', sm._config_changed() is False)

    print('\n[3] 文件中禁用 / 删除的任务会被摘除')
    time.sleep(0.05)
    dis = cfg_of()
    dis['enabled'] = False
    write([dis])
    sm.reload_from_disk()
    check('文件中置为禁用的任务已从调度器摘除',
          sm.scheduler.get_job('job-h') is None)
    check('禁用后调度器为空', len(sm.scheduler.get_jobs()) == 0)

    time.sleep(0.05)
    write([cfg_of('job-keep'), cfg_of('job-drop')])
    sm.reload_from_disk()
    check('新任务已注册', sm.scheduler.get_job('job-keep') is not None)
    write([cfg_of('job-keep')])
    sm.reload_from_disk()
    check('文件中删除的任务已从调度器摘除',
              sm.scheduler.get_job('job-drop') is None)
    check('保留的任务仍在', sm.scheduler.get_job('job-keep') is not None)
    check('最终只剩 1 个任务', len(sm.scheduler.get_jobs()) == 1,
          'jobs=%d' % len(sm.scheduler.get_jobs()))

    print('\n[4] 幂等：重复重载无副作用')
    before = len(sm.scheduler.get_jobs())
    sm.reload_from_disk()
    sm.reload_from_disk()
    check('重复重载后任务数不变', len(sm.scheduler.get_jobs()) == before,
          'before=%d after=%d' % (before, len(sm.scheduler.get_jobs())))

    print('\n[5] API 路由与前端入口')
    app_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'modules', 'web', 'app.py'), encoding='utf-8').read()
    check('已注册 /api/scheduler/reload', "/api/scheduler/reload" in app_src)
    check('已注册 reload 方法', "def api_scheduler_reload" in app_src)
finally:
    if sm is not None:
        try:
            sm.scheduler.shutdown(wait=False)
        except Exception:
            pass
    sch.CONFIG_FILE = _orig_cfg
    try:
        os.remove(_tmp)
    except Exception:
        pass

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), '; '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')
