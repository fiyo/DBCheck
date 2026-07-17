"""容灾备份模块（基于 autobackup 引擎，in-process 集成）。

autobackup 以 vendored 单文件形式存在于 vendor/autobackup.py，
本模块通过 import 其函数直接驱动备份，无需 Docker / 网络编排。
"""

from modules.disaster_recovery import engine, routes, scheduler_hook

__all__ = ["engine", "routes", "scheduler_hook"]
