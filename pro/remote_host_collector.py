# -*- coding: utf-8 -*-
"""远端宿主资源采集器（自包含，可在目标机直接运行）。

本模块把「宿主级资源采集」做成一站式、可独立运行的单元：
  - 既可在中心机本地被 `from pro import remote_host_collector` 调用；
  - 也可把整份源码通过 SSH 推到目标数据库服务器，以
    `python3 - <<'DBCH_EOF' ... DBCH_EOF` 的方式内联执行。
    目标机只需 Python3 + psutil（想启用 eBPF 内核级指标再加 bcc + root），
    **无需在目标机部署任何 DBCheck 代码**。

输出 JSON（stdout 仅一行 JSON），字段分两类：
  1) psutil 基础指标（聚合视图）：
       host_cpu_user/system/iowait/idle/cpu
       host_mem / host_mem_used_gb / host_mem_total_gb / host_swap_pct
       host_load1 / host_load5 / host_load15
       host_disk_read_bytes / write_bytes / read_count / write_count
       host_disk_read_ms / write_ms   （累计计数器，速率由中心机差分）
  2) eBPF 内核级指标（仅 Linux + root + bcc 可用时填充）：
       host_disk_latency_us_p50 / p95 / p99  （块设备 IO 时延百分位，微秒）
       host_disk_iops                          （窗口内 IO 次数 / 秒）
       host_disk_top_io_procs                 （按进程的磁盘 IO 归因）
       host_cpu_top_procs                      （按进程的 CPU 占用归因）
  3) host_collector_source：'ebpf' / 'psutil' / 'unavailable'，标记实际数据源。

所有 bcc 导入、eBPF 程序加载、附着、perf 轮询都在 try/except 中，
任一环节失败都安全降级，绝不影响 psutil 基础指标的输出。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from collections import defaultdict


# ── psutil 基础指标 ────────────────────────────────────────────────────────
def _collect_psutil() -> dict:
    """采集宿主机基础资源（psutil 用户态聚合视图）。

    返回空字典表示 psutil 不可用（目标机未安装），由调用方决定降级策略。
    """
    try:
        import psutil
    except Exception:
        return {}
    m: dict = {}
    # CPU 时间拆解（user / system / iowait / idle）
    try:
        ct = psutil.cpu_times_percent(interval=None)
        user = float(getattr(ct, 'user', 0) or 0)
        system = float(getattr(ct, 'system', 0) or 0)
        iowait = float(getattr(ct, 'iowait', 0) or 0)
        idle = float(getattr(ct, 'idle', 0) or 0)
        m['host_cpu_user'] = round(user, 1)
        m['host_cpu_system'] = round(system, 1)
        m['host_cpu_iowait'] = round(iowait, 1)
        m['host_cpu_idle'] = round(idle, 1)
        m['host_cpu'] = round(100.0 - idle, 1)
    except Exception:
        pass
    # 内存 / 交换
    try:
        vm = psutil.virtual_memory()
        m['host_mem'] = round(float(vm.percent), 1)
        m['host_mem_used_gb'] = round(vm.used / (1024 ** 3), 1)
        m['host_mem_total_gb'] = round(vm.total / (1024 ** 3), 1)
        sm = psutil.swap_memory()
        m['host_swap_pct'] = round(float(sm.percent), 1)
    except Exception:
        pass
    # 系统负载（Unix 可用）
    try:
        la = psutil.getloadavg()
        m['host_load1'] = round(float(la[0]), 2)
        m['host_load5'] = round(float(la[1]), 2)
        m['host_load15'] = round(float(la[2]), 2)
    except Exception:
        pass
    # 磁盘 IO 计数器（累计值，速率在中心机侧差分）
    try:
        d = psutil.disk_io_counters()
        if d:
            m['host_disk_read_bytes'] = int(getattr(d, 'read_bytes', 0) or 0)
            m['host_disk_write_bytes'] = int(getattr(d, 'write_bytes', 0) or 0)
            m['host_disk_read_count'] = int(getattr(d, 'read_count', 0) or 0)
            m['host_disk_write_count'] = int(getattr(d, 'write_count', 0) or 0)
            m['host_disk_read_ms'] = int(getattr(d, 'read_time', 0) or 0)
            m['host_disk_write_ms'] = int(getattr(d, 'write_time', 0) or 0)
    except Exception:
        pass
    return m


# ── eBPF（自包含，目标机可选）────────────────────────────────────────────
# bcc 是可选依赖：仅 Linux + root + 已安装 bcc 时可用；其余环境静默降级。
_BCC_AVAILABLE = False
try:
    from bcc import BPF  # type: ignore
    _BCC_AVAILABLE = True
except Exception:  # pragma: no cover - 无 bcc 环境静默不可用
    BPF = None  # type: ignore


# 磁盘 IO 时延：在 blk_account_io_start 记录起始时间戳，在
# blk_account_io_done 计算 delta（device service time），并附带发起 IO 的
# 进程 pid / 命令名（在 start 当下用 bpf_get_current_* 取，避免 completion
# 时读取 request 内部结构带来的跨内核版本兼容风险）。
_DISK_C = r"""
#include <uapi/linux/ptrace.h>

struct val_t {
    u32 pid;
    char comm[TASK_COMM_LEN];
};

struct data_t {
    u32 pid;
    char comm[TASK_COMM_LEN];
    u64 delta;   // ns，device service time
};

BPF_HASH(start_ts, struct request *, u64);
BPF_HASH(who_by_req, struct request *, struct val_t);
BPF_PERF_OUTPUT(events);

int trace_pid_start(struct pt_regs *ctx, struct request *req) {
    struct val_t who = {};
    who.pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&who.comm, sizeof(who.comm));
    who_by_req.update(&req, &who);
    u64 ts = bpf_ktime_get_ns();
    start_ts.update(&req, &ts);
    return 0;
}

int trace_req_completion(struct pt_regs *ctx, struct request *req) {
    u64 *tsp = start_ts.lookup(&req);
    if (tsp == 0) {
        return 0;
    }
    u64 ts = bpf_ktime_get_ns();
    struct val_t *wp = who_by_req.lookup(&req);
    struct data_t data = {};
    if (wp != 0) {
        data.pid = wp->pid;
        __builtin_memcpy(&data.comm, wp->comm, sizeof(data.comm));
    }
    data.delta = ts - *tsp;
    events.perf_submit(ctx, &data, sizeof(data));
    start_ts.delete(&req);
    who_by_req.delete(&req);
    return 0;
}
"""

# 进程 CPU 在 CPU 占用时间：基于 sched:sched_switch 跟踪点，在每次
# 进程被切出 CPU 时，用「切出时间戳 - 切入时记录的起始时间戳」得到
# 该进程这一段的 on-CPU 时间。
_CPU_C = r"""
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

struct cpu_data_t {
    u32 pid;
    char comm[TASK_COMM_LEN];
    u64 delta;   // ns，在 CPU 上的真实占用时间
};

BPF_HASH(start_cpu, u32, u64);
BPF_PERF_OUTPUT(cpu_events);

int on_switch(struct pt_regs *ctx, bool preempt,
              struct task_struct *prev, struct task_struct *next) {
    u64 ts = bpf_ktime_get_ns();

    // 记录 next 的切入起始时间
    u32 nkey = next->pid;
    start_cpu.update(&nkey, &ts);

    // 计算 prev 的 on-CPU 时间
    u32 pkey = prev->pid;
    u64 *sp = start_cpu.lookup(&pkey);
    if (sp != 0) {
        struct cpu_data_t d = {};
        d.pid = pkey;
        bpf_probe_read_kernel_str(&d.comm, sizeof(d.comm), prev->comm);
        d.delta = ts - *sp;
        cpu_events.perf_submit(ctx, &d, sizeof(d));
        start_cpu.delete(&pkey);
    }
    return 0;
}
"""


def _ebpf_available() -> bool:
    """eBPF 采集是否可能工作：必须 Linux + root + bcc 可导入。"""
    if not _BCC_AVAILABLE:
        return False
    if platform.system() != "Linux":
        return False
    try:
        return os.geteuid() == 0
    except Exception:
        return False


def _pct(sorted_vals: list, p: float) -> float:
    """取已排序列表的百分位值（微秒）。空列表返回 0.0。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1))))
    return float(sorted_vals[idx])


class _EbpfCollector:
    """eBPF 宿主资源采集器（单次采集内使用，采集完即释放）。"""

    def __init__(self) -> None:
        self._disk_ok = False
        self._cpu_ok = False
        self._b_disk = None
        self._b_cpu = None
        self._disk_perf_open = False
        self._cpu_perf_open = False
        self._disk_deltas_ns: list = []
        self._disk_by_proc: dict = defaultdict(lambda: [0, 0])  # pid_comm -> [ios, total_ns]
        self._cpu_by_proc: dict = defaultdict(lambda: [0, 0])  # pid_comm -> [samples, total_ns]

    # ── 启动 ──
    def start(self) -> bool:
        if not _ebpf_available():
            return False
        self._try_start_disk()
        self._try_start_cpu()
        return self._disk_ok or self._cpu_ok

    def _try_start_disk(self) -> None:
        try:
            b = BPF(text=_DISK_C)
            b.attach_kprobe(event="blk_account_io_start", fn_name="trace_pid_start")
            b.attach_kprobe(event="blk_account_io_done", fn_name="trace_req_completion")
            self._b_disk = b
            self._disk_ok = True
        except Exception:
            self._disk_ok = False
            self._b_disk = None

    def _try_start_cpu(self) -> None:
        try:
            b = BPF(text=_CPU_C)
            b.attach_tracepoint(category="sched", name="sched_switch", fn_name="on_switch")
            self._b_cpu = b
            self._cpu_ok = True
        except Exception:
            self._cpu_ok = False
            self._b_cpu = None

    def stop(self) -> None:
        for attr in ("_b_disk", "_b_cpu"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.cleanup()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._disk_ok = False
        self._cpu_ok = False

    # ── perf 回调 ──
    def _on_disk_event(self, cpu, data, size) -> None:  # type: ignore
        try:
            ev = self._b_disk["events"].event(data)
            self._disk_deltas_ns.append(int(ev.delta))
            key = f"{int(ev.pid)}:{ev.comm.decode('utf-8', 'replace')}"
            rec = self._disk_by_proc[key]
            rec[0] += 1
            rec[1] += int(ev.delta)
        except Exception:
            pass

    def _on_cpu_event(self, cpu, data, size) -> None:  # type: ignore
        try:
            ev = self._b_cpu["cpu_events"].event(data)
            key = f"{int(ev.pid)}:{ev.comm.decode('utf-8', 'replace')}"
            rec = self._cpu_by_proc[key]
            rec[0] += 1
            rec[1] += int(ev.delta)
        except Exception:
            pass

    # ── 采集窗口 ──
    def collect(self, window: float = 1.0) -> dict:
        out: dict = {}
        if not (self._disk_ok or self._cpu_ok):
            return out

        self._disk_deltas_ns = []
        self._disk_by_proc = defaultdict(lambda: [0, 0])
        self._cpu_by_proc = defaultdict(lambda: [0, 0])

        deadline = time.time() + max(0.2, window)
        if self._disk_ok:
            try:
                if not self._disk_perf_open:
                    self._b_disk["events"].open_perf_buffer(self._on_disk_event)
                    self._disk_perf_open = True
                while time.time() < deadline:
                    self._b_disk.perf_buffer_poll(timeout=100)
            except Exception:
                pass
        if self._cpu_ok:
            try:
                if not self._cpu_perf_open:
                    self._b_cpu["cpu_events"].open_perf_buffer(self._on_cpu_event)
                    self._cpu_perf_open = True
                while time.time() < deadline:
                    self._b_cpu.perf_buffer_poll(timeout=100)
            except Exception:
                pass

        if self._disk_deltas_ns:
            ds = sorted(self._disk_deltas_ns)
            out["host_disk_latency_us_p50"] = round(_pct(ds, 50), 1)
            out["host_disk_latency_us_p95"] = round(_pct(ds, 95), 1)
            out["host_disk_latency_us_p99"] = round(_pct(ds, 99), 1)
            out["host_disk_iops"] = round(len(ds) / max(0.2, window), 1)

        if self._disk_by_proc:
            top = sorted(
                ({"pid": int(k.split(":", 1)[0]), "comm": k.split(":", 1)[1],
                  "ios": v[0], "ms": round(v[1] / 1e6, 1)}
                 for k, v in self._disk_by_proc.items()),
                key=lambda x: x["ms"], reverse=True,
            )[:8]
            out["host_disk_top_io_procs"] = top

        if self._cpu_by_proc:
            top = sorted(
                ({"pid": int(k.split(":", 1)[0]), "comm": k.split(":", 1)[1],
                  "samples": v[0], "ms": round(v[1] / 1e6, 1)}
                 for k, v in self._cpu_by_proc.items()),
                key=lambda x: x["ms"], reverse=True,
            )[:8]
            out["host_cpu_top_procs"] = top

        return out


def _collect_ebpf(window: float = 0.5) -> dict:
    """加载 eBPF 程序、采集一个窗口、立即释放。不可用则返回空字典。"""
    if not _ebpf_available():
        return {}
    c = _EbpfCollector()
    try:
        if not c.start():
            return {}
        return c.collect(window=window)
    except Exception:
        return {}
    finally:
        try:
            c.stop()
        except Exception:
            pass


def collect_host(use_ebpf: bool = True, window: float = 0.5) -> dict:
    """采集宿主机资源，返回可直接合并进 snapshot 的 dict。

    - psutil 基础指标始终尝试（目标机未装 psutil 则返回 unavailable）；
    - use_ebpf 为真且环境满足（Linux + root + bcc）时叠加 eBPF 内核级指标，
      并据此把 host_collector_source 标记为 'ebpf'，否则为 'psutil'。
    """
    m = _collect_psutil()
    if not m:
        return {"host_collector_source": "unavailable"}
    src = "psutil"
    if use_ebpf:
        try:
            em = _collect_ebpf(window)
            if em:
                m.update(em)
                src = "ebpf"
        except Exception:
            pass
    m["host_collector_source"] = src
    return m


def main():
    ap = argparse.ArgumentParser(description="DBCheck 远端宿主资源采集")
    ap.add_argument("--window", type=float, default=0.5,
                    help="eBPF 采集窗口（秒）")
    ap.add_argument("--no-ebpf", action="store_true",
                    help="禁用 eBPF，仅采集 psutil 基础指标")
    args = ap.parse_args()
    try:
        out = collect_host(use_ebpf=not args.no_ebpf, window=args.window)
    except Exception:
        out = {}
    # stdout 仅输出一行 JSON，便于中心机解析（错误信息走 stderr）
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
