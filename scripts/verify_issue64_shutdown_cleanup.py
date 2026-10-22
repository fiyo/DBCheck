# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>

"""退出清理加固回归断言（#64 补充：采集器退出即停 + 子进程不留孤儿）。

运行：python scripts/verify_issue64_shutdown_cleanup.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.core import proc_registry as pr

FAILS = []


def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (' | ' + detail if detail else ''))
    if not cond:
        FAILS.append(name)


class FakeProc:
    def __init__(self, alive=True):
        self.alive = alive
        self.pid = 999999
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.killed = True
        self.alive = False


print('[1] 公共子进程登记表')
p1 = FakeProc()
pr.register_proc(p1)
check('登记后计数为 1', pr.active_count() == 1, 'count=%d' % pr.active_count())
pr.register_proc(p1)  # 幂等
check('重复登记不重复计数', pr.active_count() == 1, 'count=%d' % pr.active_count())
pr.unregister_proc(p1)
check('注销后计数为 0', pr.active_count() == 0, 'count=%d' % pr.active_count())
pr.unregister_proc(None)
check('注销 None 不报错', True)

print('[2] kill_all 清理登记在册的子进程')
p2 = FakeProc()
pr.register_proc(p2)
killed = pr.kill_all()
check('kill_all 返回被杀数', killed == 1, 'killed=%d' % killed)
check('子进程已被 kill', p2.killed)
check('清理后登记表为空', pr.active_count() == 0)

print('[3] 调度器子进程已接入公共登记表')
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'modules', 'web', 'scheduler.py'), encoding='utf-8').read()
check('scheduler 登记子进程', 'register_proc(proc)' in src)
check('scheduler 注销子进程', 'unregister_proc(proc)' in src)

print('[4] 退出钩子调用链')
app_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'modules', 'web', 'app.py'), encoding='utf-8').read()
check('存在 _stop_background_collectors', 'def _stop_background_collectors' in app_src)
check('退出时调用采集器清理', '_stop_background_collectors()' in app_src)
check('退出时调用 kill_all', 'kill_all()' in app_src)
for name in ('get_monitor_engine', 'get_screen_collector', 'get_scheduler'):
    check('清理函数覆盖 %s' % name, name in app_src)

print('[4b] 退出清理不得触发懒加载（get_scheduler 会启动调度器）')
fn = app_src.split('def _stop_background_collectors', 1)[1].split('\ndef ', 1)[0]
check('未调用 get_scheduler()', 'get_scheduler()' not in fn)
check('未调用 get_monitor_engine()', 'get_monitor_engine()' not in fn)
check('未调用 get_screen_collector()', 'get_screen_collector()' not in fn)
check('调度器实例取自模块变量', "'_scheduler'" in fn)
check('实时监控实例取自模块变量', "'_monitor_engine'" in fn)
check('大屏实例取自模块变量', "'_screen_collector'" in fn)

print('[5] 采集器线程均为 daemon（退出即停，无需运行期开关）')
for rel, needle in (('modules/monitor/screen_metrics.py', 'daemon=True'),
                    ('modules/monitor/engine.py', 'daemon=True'),
                    ('modules/web/app.py', 'daemon=True')):
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *rel.split('/'))
    s = open(p, encoding='utf-8').read()
    check('%s 含 daemon=True' % rel, needle in s)

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), '; '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')