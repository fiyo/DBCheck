"""容灾备份调度器生命周期管理。

在 DBCheck 启动时拉起 autobackup 的 Scheduler（croniter + 守护线程），
按 config.yaml 中的计划定时执行备份。计划变更后调用 reload_scheduler 重建。
"""

import threading

from modules.disaster_recovery.vendor.autobackup import Scheduler

_scheduler = None
_lock = threading.Lock()


def start_scheduler():
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.is_running:
            return _scheduler
        from modules.disaster_recovery import engine

        cfg = engine._load_config()
        if not cfg.get("tasks"):
            return None
        sched = Scheduler(
            cfg, engine._get_notifier(cfg), engine._get_logger(), engine._get_history()
        )
        sched.start_background()
        _scheduler = sched
        return sched


def stop_scheduler():
    global _scheduler
    with _lock:
        if _scheduler is not None:
            try:
                _scheduler.stop()
            except Exception:
                pass
            _scheduler = None


def reload_scheduler():
    stop_scheduler()
    return start_scheduler()


def get_scheduler():
    return _scheduler
