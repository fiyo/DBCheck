# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck
"""
中央日志中枢（loghub）
====================

把各子系统（巡检 / 定时巡检 / 智能诊断 / 监控 / 系统）的运行日志统一汇聚到一个
带「分类（category）」标签的线程安全环形缓冲，供前端「运行日志」页按分类过滤查看。

根因与定位
----------
此前监控引擎在后台线程里用 ``print('[Monitor] ...')`` 直接写共享 stdout，与巡检任务
（同为进程内线程）的 print 输出交错，导致「监控日志混入巡检日志 / 控制台」。本模块的
做法是：

- 各子系统改用 ``logging.getLogger('dbcheck.<category>')`` 输出；一个挂在 root 上的
  :class:`LogHubHandler` 捕获全部记录并按 logger 名派生 category，写入缓冲。
- 监控（``dbcheck.monitor``）默认**不**输出到共享控制台 StreamHandler（由
  :class:`MonitorConsoleFilter` 过滤），只落 hub + 专属 ``monitor.log`` 文件，从根上
  杜绝其污染巡检 / 控制台。
- 巡检 / 诊断等仍照常写控制台与任务日志，便于现场排查。

分类
----
- ``inspection`` 巡检（含服务器巡检）
- ``scheduled`` 定时巡检（调度器）
- ``diagnosis`` 智能诊断
- ``monitor``   监控
- ``system``    系统（其它未归类日志）
"""

import logging
import threading
import time
from collections import deque

# logger 名 -> 分类 key（最长前缀优先，见 _category_of）
CATEGORY_BY_LOGGER_PREFIX = (
    ('dbcheck.monitor', 'monitor'),
    ('dbcheck.inspection', 'inspection'),
    ('dbcheck.diagnosis', 'diagnosis'),
    ('dbcheck.scheduled', 'scheduled'),
    ('scheduler', 'scheduled'),
)
DEFAULT_CATEGORY = 'system'

# 分类 key -> 前端展示名
CATEGORY_LABELS = {
    'inspection': '巡检',
    'scheduled': '定时巡检',
    'diagnosis': '智能诊断',
    'monitor': '监控',
    'system': '系统',
}

# 前端过滤芯片的展示顺序
CATEGORY_ORDER = ['inspection', 'scheduled', 'diagnosis', 'monitor', 'system']

_MAX_RECORDS = 3000
_lock = threading.Lock()
_buffer = deque(maxlen=_MAX_RECORDS)


def _category_of(record):
    name = (record.name or '').lower()
    for prefix, cat in CATEGORY_BY_LOGGER_PREFIX:
        if name == prefix or name.startswith(prefix + '.'):
            return cat
    return DEFAULT_CATEGORY


class LogHubHandler(logging.Handler):
    """把日志记录（带 category 标签）写入进程内环形缓冲。"""

    def emit(self, record):
        try:
            msg = self.format(record)
            if not msg:
                return
            with _lock:
                _buffer.append({
                    'ts': record.created,                       # 浮点时间戳
                    'time': time.strftime('%Y-%m-%d %H:%M:%S',
                                          time.localtime(record.created)),
                    'category': _category_of(record),
                    'level': record.levelname,
                    'logger': record.name,
                    'msg': msg,
                })
        except Exception:
            pass


class MonitorConsoleFilter(logging.Filter):
    """挂到 root 的 StreamHandler 上，丢弃 ``dbcheck.monitor`` 记录，
    使监控日志不污染共享控制台（巡检 / 系统日志所在的地方）。"""

    def filter(self, record):
        return not (record.name or '').lower().startswith('dbcheck.monitor')


_initialized = False


def init_loghub():
    """安装 hub handler + 监控控制台过滤器 + 监控专属文件。幂等。"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()

    # 1) hub handler（捕获全部记录，含 monitor），避免重复安装
    hub = LogHubHandler()
    hub.setFormatter(logging.Formatter('%(message)s'))
    hub.setLevel(logging.DEBUG)
    root.addHandler(hub)

    # 2) 给 root 已有的 StreamHandler 加 MonitorConsoleFilter（过滤监控日志）。
    #    若 root 当前无 StreamHandler，则监控改用 logging 后本就不会打印到控制台，
    #    此处 filter 作为冗余保险同样无害。
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, LogHubHandler):
            h.addFilter(MonitorConsoleFilter())

    # 3) 监控专属文件（始终落盘，便于排障，且不在控制台刷屏）
    try:
        from modules.core import paths
        from pathlib import Path
        mon_path = Path(str(paths.PRO_DATA_DIR)) / 'monitor.log'
        mon_path.parent.mkdir(parents=True, exist_ok=True)
        mh = logging.FileHandler(str(mon_path), encoding='utf-8', delay=True)
        mh.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        mh.setLevel(logging.INFO)
        mon = logging.getLogger('dbcheck.monitor')
        mon.addHandler(mh)
        mon.setLevel(logging.INFO)
        mon.propagate = True          # 仍需 propagate 以便进入 hub
    except Exception:
        pass

    # 4) 确保各分类 logger 存在且层级合理
    logging.getLogger('dbcheck.monitor').setLevel(logging.INFO)
    for lg_name in ('dbcheck.inspection', 'dbcheck.diagnosis', 'dbcheck.scheduled'):
        logging.getLogger(lg_name).setLevel(logging.DEBUG)


def query_logs(categories=None, levels=None, limit=500, search=None, reverse=True):
    """按分类 / 级别 / 关键字过滤环形缓冲中的日志。

    Args:
        categories: 分类 key 列表，如 ['inspection','monitor']；空/None 表示全部。
        levels:     级别名列表（大写），如 ['INFO','ERROR']；含 'ALL' 或空表示全部。
        limit:      返回条数上限。
        search:     关键字（不区分大小写）子串匹配 msg。
        reverse:    True=最新在前。
    """
    with _lock:
        items = list(_buffer)
    if categories:
        wanted = set(categories)
        items = [x for x in items if x['category'] in wanted]
    if levels:
        wanted_lv = {l.upper() for l in levels if l and l.upper() != 'ALL'}
        if wanted_lv:
            items = [x for x in items if x['level'].upper() in wanted_lv]
    if search:
        s = search.lower()
        items = [x for x in items if s in x['msg'].lower()]
    if reverse:
        items = list(reversed(items))
    if limit and limit > 0:
        items = items[:limit]
    return items


def get_categories():
    """返回前端过滤芯片所需的分类列表（含展示名）。"""
    return [{'key': k, 'label': CATEGORY_LABELS.get(k, k)} for k in CATEGORY_ORDER]
