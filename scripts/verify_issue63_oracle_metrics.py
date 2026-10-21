# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>

"""Issue #63 回归断言：Oracle 大屏 QPS 防噪 + 慢查询三列口径。

运行：python scripts/verify_issue63_oracle_metrics.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.monitor import queries as mq
from modules.monitor.screen_metrics import ScreenCollector

FAILS = []


def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (' | ' + detail if detail else ''))
    if not cond:
        FAILS.append(name)


def _snap(**kw):
    s = {'id': 'i1', 'ts': time.time(), 'counters': {'exec_count': 0}}
    s.update(kw)
    return s


def make_collector():
    c = ScreenCollector.__new__(ScreenCollector)
    import threading
    from collections import deque
    c._lock = threading.RLock()
    c._prev = {}
    c._snap = {}
    c._spark = {}
    return c


print('[1] 慢查询 SQL 口径拆分')
main_sql = mq.ORACLE_SLOW_QUERY_SQL
fb_sql = mq.ORACLE_SLOW_QUERY_FALLBACK_SQL
check('主 SQL 平均耗时 = 累计 / 执行次数',
      'elapsed_time / 1000000 / NULLIF(s.executions, 0)' in main_sql)
check('主 SQL 最大耗时取单次最慢列', 'max_elapsed_time / 1000000' in main_sql)
check('主 SQL 总耗时仍为累计值', main_sql.count('s.elapsed_time / 1000000, 3') == 1)
check('过滤未执行 SQL', 's.executions > 0' in main_sql)
check('module 为 NULL 不被误过滤', "NVL(s.module, 'x')" in main_sql)
check('只取父游程', 's.child_number = 0' in main_sql)
check('降级版不含 max_elapsed_time', 'max_elapsed_time' not in fb_sql)
check('降级版 max 退化为 avg', fb_sql.count('NULLIF(s.executions, 0)') == 2)

print('[2] 字典注册正确（不得互相覆盖）')
check('主字典 oracle 指向主 SQL',
      mq.SLOW_QUERY_TEMPLATES.get('oracle') is mq.ORACLE_SLOW_QUERY_SQL)
check('fallback 字典 oracle 指向降级 SQL',
      mq.SLOW_QUERY_FALLBACK_TEMPLATES.get('oracle') is mq.ORACLE_SLOW_QUERY_FALLBACK_SQL)
check('oracle_jdbc 别名归一到 oracle（_DB_TYPE_ALIASES）',
      mq._DB_TYPE_ALIASES.get('oracle_jdbc') == 'oracle'
      and mq.SLOW_QUERY_TEMPLATES['oracle'] is mq.ORACLE_SLOW_QUERY_SQL)

print('[3] QPS 速率基础语义')
check('计数器回退不再兜成 0（返回 None）',
      ScreenCollector._rate({'exec_count': 100}, {'exec_count': 50}, 15, 'qps') is None)
check('正常增量速率正确',
      abs(ScreenCollector._rate({'exec_count': 100}, {'exec_count': 400}, 15, 'qps') - 20.0) < 1e-6)
check('计数缺失返回 None',
      ScreenCollector._rate({'questions': None}, {'questions': 10}, 15, 'qps') is None)

print('[4] QPS 三层防噪')
c = make_collector()
t0 = time.time()
# ① 正常样本
c._commit_snap(_snap(ts=t0, counters={'exec_count': 1000}, instance_number=1))
c._commit_snap(_snap(ts=t0 + 15, counters={'exec_count': 1600}, instance_number=1))
check('正常样本 QPS 正确计算',
      c._snap['i1'].get('qps') == 40.0, 'qps=%s' % c._snap['i1'].get('qps'))
check('正常样本无异常标记', not c._snap['i1'].get('rate_note'))

# ② 实例切换（RAC / 负载均衡跨实例）
c._commit_snap(_snap(ts=t0 + 30, counters={'exec_count': 2200}, instance_number=2))
check('实例切换样本作废不产生 QPS', c._snap['i1'].get('qps') is None)
check('实例切换有标记', c._snap['i1'].get('rate_note') == 'instance_changed',
      str(c._snap['i1'].get('rate_note')))

# ③ 时间差抖动
c._commit_snap(_snap(ts=t0 + 31, counters={'exec_count': 3000}, instance_number=2))
c._commit_snap(_snap(ts=t0 + 33, counters={'exec_count': 9000}, instance_number=2))
check('dt 过小样本作废', c._snap['i1'].get('qps') is None)
check('dt 过小有标记', c._snap['i1'].get('rate_note') == 'dt_too_small',
      str(c._snap['i1'].get('rate_note')))

# ④ 尖峰封顶
c2 = make_collector()
c2._commit_snap(_snap(ts=t0, counters={'exec_count': 0}, instance_number=1))
c2._commit_snap(_snap(ts=t0 + 15, counters={'exec_count': 9000000}, instance_number=1))
check('超阈值 QPS 不进快照', c2._snap['i1'].get('qps') is None)
check('超阈值原值留档', c2._snap['i1'].get('qps_raw') == 600000.0,
      'raw=%s' % c2._snap['i1'].get('qps_raw'))
check('超阈值有标记', c2._snap['i1'].get('rate_note') == 'spike')
check('超阈值不进迷你趋势', not c2._spark.get('i1'))

# ⑤ 计数器归零（重启）
c3 = make_collector()
c3._commit_snap(_snap(ts=t0, counters={'exec_count': 500000}, instance_number=1))
c3._commit_snap(_snap(ts=t0 + 15, counters={'exec_count': 100}, instance_number=1))
check('重启归零标记 counter_reset',
      c3._snap['i1'].get('rate_note') == 'counter_reset',
      str(c3._snap['i1'].get('rate_note')))
check('重启归零不显示假 0', c3._snap['i1'].get('qps') is None)

print('[5] 首轮无 prev 时不崩溃')
c4 = make_collector()
c4._commit_snap(_snap(ts=t0, counters={'exec_count': 10}, instance_number=1))
check('首轮 QPS 为空', c4._snap['i1'].get('qps') is None)

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), '; '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')