# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""工作流任务运行器 + 周期调度（「工作流任务」运行时的引擎）。

设计要点：
* ``TaskRunner`` 单例：以独立后台线程执行 Workflow 引擎，支持并发上限
  （默认 3）、优雅停止（cooperative cancellation，复用 Workflow.run 的取消点）、
  运行结果落库、进程重启恢复。
* ``WorkflowScheduler``：基于 croniter 的守护线程，按每个任务的 cron 表达式周期触发；
  进程刚启动时不会立即触发，避免所有 cron 任务在启动瞬间集中跑。
* 取消是「协作式」的：置位取消 Event 后，引擎会跑完当前节点、在下一节点前中止
  后续节点（已落库的动作不可回滚）。UI 应提示「停止 = 不再执行后续节点」。
"""

from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from croniter import croniter
    _HAS_CRONITER = True
except Exception:  # croniter 缺失时仅禁用调度，不影响手动启停
    croniter = None  # type: ignore
    _HAS_CRONITER = False

from .workflow import Step, Workflow
from .hub import prepare_instance_inputs
from .workflow_store import get_workflow
from .workflow_task_store import (
    STATUS_ERROR,
    STATUS_RUNNING,
    STATUS_STOPPED,
    get_task,
    list_tasks,
    update_status,
)
from .workflow_run_store import (
    RUN_CANCELLED,
    RUN_DONE,
    RUN_ERROR,
    add_run,
    update_run,
)

DEFAULT_CONCURRENCY = 3
_SCHED_INTERVAL_SEC = 30
_STOP_JOIN_TIMEOUT = 120


class TaskRunner:
    """单例。后台执行工作流任务并维护状态机。"""

    _instance: Optional["TaskRunner"] = None

    def __new__(cls) -> "TaskRunner":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._lock = threading.RLock()
        self._active: Dict[int, Dict[str, Any]] = {}  # task_id -> {thread, cancel, run_id}
        self._sem = threading.Semaphore(DEFAULT_CONCURRENCY)
        self._bootstrapped = False
        self._sched_thread: Optional[threading.Thread] = None
        self._sched_stop = threading.Event()
        self._sched_last_tick: Dict[int, datetime] = {}

    # ── 启动挂接 ───────────────────────────────────────
    def bootstrap(self) -> None:
        """应用启动时调用一次：恢复残留状态并拉起调度守护线程。幂等。"""
        with self._lock:
            if self._bootstrapped:
                return
            self._bootstrapped = True
        # 重启恢复：把残留 running 的任务置为 stopped（内存 handle 已丢失）
        try:
            for t in list_tasks():
                if t["status"] == STATUS_RUNNING:
                    update_status(t["id"], STATUS_STOPPED)
        except Exception:
            pass
        # 拉起调度守护线程
        if _HAS_CRONITER:
            self._sched_stop.clear()
            self._sched_thread = threading.Thread(
                target=self._scheduler_loop, name="wf-scheduler", daemon=True
            )
            self._sched_thread.start()

    # ── 启动 / 调度入口 ─────────────────────────────────
    def start(self, task_id: int) -> bool:
        """手动启动（trigger='manual'）。已在运行则忽略。返回是否成功发起。"""
        with self._lock:
            if task_id in self._active:
                return False
            task = get_task(task_id)
            if not task or task["status"] == STATUS_RUNNING:
                return False
            self._spawn_locked(task_id, "manual")
        return True

    def run_once(self, task_id: int, trigger: str = "cron") -> bool:
        """供调度器调用：仅当未运行才触发。"""
        with self._lock:
            if task_id in self._active:
                return False
            task = get_task(task_id)
            if not task:
                return False
            self._spawn_locked(task_id, trigger)
        return True

    def _spawn_locked(self, task_id: int, trigger: str) -> None:
        """调用方必须已持 ``_lock``。登记活跃记录、申请并发额度、启动线程。"""
        cancel = threading.Event()
        run = add_run(task_id, trigger)
        update_status(task_id, STATUS_RUNNING, last_run_at=run["started_at"])
        t = threading.Thread(
            target=self._run_thread,
            args=(task_id, run["id"], cancel, trigger),
            name="wf-task-%d" % task_id,
            daemon=True,
        )
        self._active[task_id] = {
            "thread": t,
            "cancel": cancel,
            "run_id": run["id"],
        }
        self._sem.acquire()  # 阻塞直到有并发额度（排队中状态已写 running）
        t.start()

    def _run_thread(self, task_id: int, run_id: int, cancel: threading.Event, trigger: str) -> None:
        try:
            self._execute(task_id, run_id, cancel)
        finally:
            self._sem.release()
            with self._lock:
                self._active.pop(task_id, None)

    def _execute(self, task_id: int, run_id: int, cancel: threading.Event) -> None:
        started = time.time()
        task = get_task(task_id)
        if not task:
            return
        wf = get_workflow(task["workflow_id"])
        if not wf:
            update_status(task_id, STATUS_ERROR, last_error="工作流不存在")
            update_run(run_id, RUN_ERROR, error="工作流不存在")
            return
        steps = [
            Step(
                id=str(s.get("id")),
                kind=s.get("kind", "specialist"),
                ref=s.get("ref", "") or "",
                args=s.get("args") or {},
                label=s.get("label") or str(s.get("id")),
            )
            for s in wf["steps"]
        ]
        edges = [
            (str(e[0]), str(e[1]))
            for e in wf["edges"]
            if isinstance(e, (list, tuple)) and len(e) == 2
        ]
        engine = Workflow(steps, edges)
        # 注入数据源连接信息 + 历史巡检报告，供各专家（如深度巡检专员）实时分析
        wf_inputs = prepare_instance_inputs(task["instance_id"])
        try:
            result = engine.run(
                goal=task.get("goal") or "",
                instance_id=task["instance_id"],
                inputs=wf_inputs,
                cancel_event=cancel,
            )
            dur = int((time.time() - started) * 1000)
            cancelled = bool(result.get("cancelled", False))
            ctx = result.get("ctx") or {}
            findings_count = len(ctx.get("findings", []) or [])
            plan_count = len(ctx.get("plan", []) or [])
            summary = self._summarize(result)
            if cancelled:
                update_status(task_id, STATUS_STOPPED)
                update_run(
                    run_id, RUN_CANCELLED, duration_ms=dur,
                    findings_count=findings_count, plan_count=plan_count,
                    summary=summary, outputs=result.get("outputs", []),
                )
            else:
                update_status(task_id, STATUS_STOPPED, last_duration_ms=dur)
                update_run(
                    run_id, RUN_DONE, duration_ms=dur,
                    findings_count=findings_count, plan_count=plan_count,
                    summary=summary, outputs=result.get("outputs", []),
                )
        except Exception as e:  # 引擎级异常 → 任务置 error，运行记录置 error
            dur = int((time.time() - started) * 1000)
            update_status(task_id, STATUS_ERROR, last_error=str(e))
            update_run(run_id, RUN_ERROR, duration_ms=dur, error=str(e))

    @staticmethod
    def _summarize(result: Dict[str, Any]) -> str:
        findings = (result.get("ctx") or {}).get("findings", []) or []
        items = []
        for f in findings[:5]:
            if isinstance(f, dict):
                items.append("[%s] %s" % (f.get("severity", ""), f.get("title", "")))
            elif hasattr(f, "severity"):
                items.append("[%s] %s" % (getattr(f, "severity", ""), getattr(f, "title", "")))
        return "；".join(items) or "无发现"

    # ── 停止 ───────────────────────────────────────────
    def stop(self, task_id: int) -> bool:
        with self._lock:
            rec = self._active.get(task_id)
            if not rec:
                # 无活跃线程：若状态机残留 running（如异常残留），置 stopped
                t = get_task(task_id)
                if t and t["status"] == STATUS_RUNNING:
                    update_status(task_id, STATUS_STOPPED)
                    return True
                return False
            cancel = rec["cancel"]
            thread = rec["thread"]
        cancel.set()  # 协作式取消：当前节点完成后中止后续
        thread.join(timeout=_STOP_JOIN_TIMEOUT)
        with self._lock:
            self._active.pop(task_id, None)
        return True

    # ── 调度循环 ───────────────────────────────────────
    def _scheduler_loop(self) -> None:
        while not self._sched_stop.is_set():
            try:
                self._sched_tick()
            except Exception:
                pass
            self._sched_stop.wait(_SCHED_INTERVAL_SEC)

    def _sched_tick(self) -> None:
        if not _HAS_CRONITER:
            return
        now = datetime.now()
        try:
            tasks = list_tasks()
        except Exception:
            return
        for t in tasks:
            cron = (t.get("cron") or "").strip()
            if not cron:
                continue
            tid = t["id"]
            last = self._sched_last_tick.get(tid)
            if last is None:
                # 首次只记录基准时刻，避免启动瞬间集中触发
                self._sched_last_tick[tid] = now
                continue
            try:
                nxt = croniter(cron, last).get_next(datetime)  # type: ignore[arg-type]
                if nxt <= now:
                    self.run_once(tid, "cron")
                    self._sched_last_tick[tid] = now
                else:
                    self._sched_last_tick[tid] = last
            except Exception:
                continue


def get_runner() -> TaskRunner:
    return TaskRunner()
