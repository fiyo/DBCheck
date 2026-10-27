# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>

"""定时任务「改配置不生效 / 禁用了还在跑」回归断言（#65）。

根因：APScheduler 3.x 的 job 主键参数是 ``id=``，项目里写成了 ``job_id=``，
该参数被并入 ``**trigger_args`` 静默丢弃 → 每个任务真实 id 是随机 UUID →
``get_job(job_id)`` 永远 None → ``remove_job`` 从不执行 → 旧任务残留、
改配置不生效、禁用后仍按旧时间触发、list_jobs 的 running 状态永远为 False。

运行：python scripts/verify_issue65_scheduler_job_id.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apscheduler.schedulers.background import BackgroundScheduler
from modules.web import scheduler as sch

FAILS = []


def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (' | ' + detail if detail else ''))
    if not cond:
        FAILS.append(name)


def fields(job):
    return {f.name: str(f) for f in job.trigger.fields} if job else {}


# ── 隔离：临时配置文件，不动真实任务配置 ──
_orig_load, _orig_save = sch._load_jobs, sch._save_jobs
STATE = {'jobs': []}


def _load():
    return json.loads(json.dumps(STATE['jobs']))


def _save(jobs):
    STATE['jobs'] = json.loads(json.dumps(jobs))


sch._load_jobs = _load
sch._save_jobs = _save

sm = None
try:
    sm = sch.SchedulerManager.__new__(sch.SchedulerManager)
    sm.scheduler = BackgroundScheduler(daemon=True)
    sm.scheduler.start()

    base = {
        'id': 'job-1', 'name': '测试任务', 'enabled': True,
        'db_info': {'db_type': 'mysql', 'host': 'h', 'port': 3306,
                    'user': 'u', 'password': 'p', 'database': 'd'},
        'inspector_name': '张三', 'notify_on_done': True,
    }

    print('[1] 任务以业务 id 注册（不再是随机 UUID）')
    cfg1 = dict(base)
    cfg1['cron'] = {'hour': '9', 'minute': '0', 'day': '*'}
    ok1 = sm.add_job(cfg1)
    job = sm.scheduler.get_job('job-1')
    check('add_job 返回 True', ok1 is True)
    check('调度器中能按业务 id 查到任务', job is not None)
    check('触发器为每天 9:00',
          job is not None and fields(job).get('day_of_week') == '*'
          and fields(job).get('hour') == '9', str(fields(job)))

    print('[2] 修改调度配置后新配置真正生效')
    cfg2 = dict(base)
    cfg2['cron'] = {'day_of_week': 'mon', 'hour': '9', 'minute': '0'}
    ok2 = sm.update_job('job-1', cfg2)
    job2 = sm.scheduler.get_job('job-1')
    check('update_job 返回 True', ok2 is True)
    check('触发器已变为每周一 9:00',
          job2 is not None and fields(job2).get('day_of_week') == 'mon',
          str(fields(job2)))
    check('调度器中只有一个任务（无重复注册）', len(sm.scheduler.get_jobs()) == 1,
          'jobs=%d' % len(sm.scheduler.get_jobs()))
    check('持久化配置的 cron 已更新',
          STATE['jobs'][0].get('cron', {}).get('day_of_week') == 'mon',
          json.dumps(STATE['jobs'][0].get('cron'), ensure_ascii=False))
    check('列表返回的 cron 已是每周一',
          sm.list_jobs()[0].get('cron', {}).get('day_of_week') == 'mon')

    print('[3] 禁用 / 启用真的作用于调度器')
    sm.toggle_job('job-1', False)
    check('禁用后已从调度器摘除', sm.scheduler.get_job('job-1') is None)
    check('禁用后调度器无残留任务', len(sm.scheduler.get_jobs()) == 0,
          'jobs=%d' % len(sm.scheduler.get_jobs()))
    sm.toggle_job('job-1', True)
    job3 = sm.scheduler.get_job('job-1')
    check('重新启用后回到调度器', job3 is not None)
    check('重新启用后触发器仍是每周一', fields(job3).get('day_of_week') == 'mon',
          str(fields(job3)))
    check('list_jobs 的 running 状态正确（启用中为 True）',
          sm.list_jobs()[0].get('running') is True)

    print('[4] 删除任务后调度器中彻底移除')
    sm.remove_job('job-1')
    check('删除后调度器无该任务', sm.scheduler.get_job('job-1') is None)
    check('删除后调度器为空', len(sm.scheduler.get_jobs()) == 0)

    print('[5] 孤儿 job 清理（历史随机 id 残留）')
    # 模拟历史 bug：往调度器塞一个随机 UUID 的 job
    sm.scheduler.add_job(func=lambda: None, trigger='interval', seconds=60,
                          id='9a4c3ca8af054adcbbb502f79bcf9e57')
    check('模拟：调度器中存在随机 id 孤儿',
          sm.scheduler.get_job('9a4c3ca8af054adcbbb502f79bcf9e57') is not None)
    cfg6 = dict(base)
    cfg6['cron'] = {'hour': '8', 'minute': '0'}
    sm.add_job(cfg6)
    check('孤儿 job 已被清理',
          sm.scheduler.get_job('9a4c3ca8af054adcbbb502f79bcf9e57') is None)
    check('正常任务未被误删', sm.scheduler.get_job('job-1') is not None)
    check('清理后只剩 1 个任务', len(sm.scheduler.get_jobs()) == 1,
          'jobs=%d' % len(sm.scheduler.get_jobs()))

    print('[6] 巡检人员不再硬编码 Jack')
    check('调度配置保留自定义巡检人员',
          STATE['jobs'][0].get('inspector_name') == '张三',
          str(STATE['jobs'][0].get('inspector_name')))
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'web_templates', 'index.html'), encoding='utf-8').read()
    check('前端存在巡检人员输入框', 'id="job-inspector"' in html)
    check('saveJob 不再硬编码 Jack（定时任务路径）',
          "inspector_name: 'Jack'" not in html)
    check('编辑时回填巡检人员', "inspEl.value = job.inspector_name" in html)
finally:
    if sm is not None:
        try:
            sm.scheduler.shutdown(wait=False)
        except Exception:
            pass
    sch._load_jobs, sch._save_jobs = _orig_load, _orig_save

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), '; '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')
