# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
单轮采集的连接复用作用域 —— 消除「每条 SQL 各建一次连接」的重复采集。

背景：
  统一采集器之前，首页实时监控 / MonitorEngine / ScreenCollector 三条循环
  各自建连；而 MonitorEngine 的 ``_connect_and_query`` 更是**每条 SQL 都
  新建并关闭一条连接**（慢查询、连接数、大屏 stat/tbs/repl/locks 共 6~10
  条 SQL），单实例 30s 内可达 10~20 次握手。

方案：
  统一采集器在一个实例的一轮采集开始时，先建立**唯一一条**连接（含可选
  SSH 隧道），把它绑定到当前线程的 ``instance_id`` 上；随后本轮内所有
  采集通道（首页深采、_collect_slow、_collect_conn、collect_extra）都通过
  ``current()/borrowed()`` 借用这条连接，不再重复建连，也不负责关闭；
  整轮结束后由统一采集器统一 ``managed.close()`` 释放连接与隧道。

线程安全：
  ThreadPoolExecutor 里每个任务绑定自己的 instance_id，绑定存于
  ``threading.local()``，不同线程的绑定互不干扰；任务结束必须 ``unbind()``，
  避免同一线程后续处理其它实例时误借到上一条连接。

边界：
  - 借用连接只在同一线程内有效，绝不跨线程传递 DBAPI 连接对象。
  - ``BorrowedConnection.close()`` 是 no-op：旧采集函数里的 ``finally:
    managed.close()`` 保持原样即可，不会提前关掉共享连接。
  - 未绑定时返回 None，调用方按原有逻辑自行建连（兼容独立调用、
    单元测试、脚本等不经过统一采集器的路径）。
"""

import threading

_local = threading.local()


class BorrowedConnection:
    """借用中的 ManagedConnection 视图：``close()`` 不真正关闭共享连接。"""

    def __init__(self, conn):
        self.conn = conn

    def close(self):
        # 生命周期归统一采集器所有，这里必须 no-op。
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _bindings():
    d = getattr(_local, 'bindings', None)
    if d is None:
        d = {}
        _local.bindings = d
    return d


def bind(instance_id, managed):
    """绑定当前线程这一轮使用的 ManagedConnection，返回它本身。"""
    _bindings()[str(instance_id)] = managed
    return managed


def current(instance_id):
    """返回当前线程为 ``instance_id`` 绑定的 ManagedConnection，未绑定返回 None。"""
    return _bindings().get(str(instance_id))


def borrowed(instance_id):
    """返回可安全交给旧采集函数的借用视图，未绑定返回 None。"""
    managed = current(instance_id)
    if managed is None or getattr(managed, 'conn', None) is None:
        return None
    return BorrowedConnection(managed.conn)


def unbind(instance_id):
    """解除绑定；不关闭连接（由统一采集器统一关闭）。"""
    _bindings().pop(str(instance_id), None)


def clear():
    """清空当前线程全部绑定（防御性清理，供任务收尾使用）。"""
    _local.bindings = {}
