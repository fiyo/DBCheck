# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
全局子进程登记表
================

主进程退出（Ctrl+C / os._exit 强杀）时不会触发任何清理钩子，
此时仍在运行的子进程（尤其是含 JVM 的 ``jdbc_inspection_cli`` 巡检子进程）
会变成孤儿，继续占用内存与端口——表现为「进程结束不掉 / 重启端口被占」。

本模块提供跨模块共享的子进程登记表：

- ``register_proc(proc)``   子进程创建后登记
- ``unregister_proc(proc)`` 子进程结束后注销
- ``kill_all()``            退出前整树杀掉所有登记在册的子进程

抽成独立模块的原因：``modules.web.app`` 与 ``modules.web.scheduler`` 都要用，
而两者之间存在循环依赖（app 延迟导入 scheduler，scheduler 内又需引用 app），
因此登记表必须放在两者之外的公共层。
"""

import os
import signal
import subprocess as _sp
import threading

# 登记表（进程内共享）
_ACTIVE_PROCS = set()
_LOCK = threading.Lock()


def register_proc(proc):
    """登记活跃子进程（幂等）"""
    if proc is None:
        return
    with _LOCK:
        _ACTIVE_PROCS.add(proc)


def unregister_proc(proc):
    """注销子进程"""
    if proc is None:
        return
    with _LOCK:
        _ACTIVE_PROCS.discard(proc)


def active_count():
    """当前登记的活跃子进程数"""
    with _LOCK:
        return len(_ACTIVE_PROCS)


def kill_process_tree(proc):
    """强杀子进程及其派生进程（JVM 可能另起子进程）。"""
    if proc is None:
        return
    try:
        if os.name == 'nt':
            _flags = getattr(_sp, 'CREATE_NO_WINDOW', 0x08000000)
            _sp.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                    creationflags=_flags, timeout=10)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def kill_all():
    """整树杀掉所有登记在册的活跃子进程。

    退出前调用；任何异常都不允许阻断退出流程（退出才是第一优先级）。
    返回被杀掉的进程数。
    """
    with _LOCK:
        procs = list(_ACTIVE_PROCS)
    killed = 0
    for p in procs:
        try:
            if p.poll() is None:
                kill_process_tree(p)
                killed += 1
        except Exception:
            pass
    # 处理完毕：从注册表移除（含已退出的项，避免残留）
    with _LOCK:
        for p in procs:
            _ACTIVE_PROCS.discard(p)
    return killed
