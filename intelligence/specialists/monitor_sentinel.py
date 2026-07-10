# -*- coding: utf-8 -*-
"""运行监控哨兵：第一时间发现实时监控异常。

指标底座分两层：
  1. 宿主机真实资源（eBPF 级近似）：CPU 指令/IO 等待拆解、内存、交换、磁盘 IO 吞吐与时延；
  2. 数据库层细粒度指标：连接/活跃线程、慢查询速率、主从延迟、死锁、缓冲命中、事务回滚、QPS/TPS。

阈值按维度细化，命中即产出带数值与处置建议的发现。
"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist

# ── 阈值表（百分比 / 绝对数 / MBps / ms）。thr=预警，crit=严重 ──
# kind 用于区分宿主机(host)与数据库(db)资源，便于分组与标签。
CHECKS = [
    # ══ 宿主机资源（eBPF 级真实资源）══
    dict(key="host_cpu", kind="host", label="宿主机 CPU", unit="%",
         thr=85.0, crit=95.0, title="宿主机 CPU 使用率偏高",
         suggestion="确认瓶颈是否由数据库进程引起；必要时扩容实例或迁移负载。"),
    dict(key="host_cpu_iowait", kind="host", label="CPU IO 等待", unit="%",
         thr=15.0, crit=30.0, title="CPU 出现明显 IO 等待",
         suggestion="磁盘 IO 可能成为瓶颈，结合磁盘吞吐与时延排查慢 SQL 物理读。"),
    dict(key="host_mem", kind="host", label="宿主机内存", unit="%",
         thr=85.0, crit=95.0, title="宿主机内存使用率偏高",
         suggestion="关注数据库缓冲池与连接内存占用，防止触发 swap 导致性能骤降。"),
    dict(key="host_swap_pct", kind="host", label="Swap 使用", unit="%",
         thr=5.0, crit=20.0, title="系统发生 Swap 交换",
         suggestion="Swap 会严重拖慢数据库，需降低内存压力或增加物理内存。"),
    dict(key="host_disk_read_mb_s", kind="host", label="磁盘读吞吐", unit="MB/s",
         thr=200.0, crit=500.0, title="磁盘读吞吐偏高",
         suggestion="检查是否有大量物理读/全表扫描，优化索引与缓冲池以降低读压力。"),
    dict(key="host_disk_write_mb_s", kind="host", label="磁盘写吞吐", unit="MB/s",
         thr=200.0, crit=500.0, title="磁盘写吞吐偏高",
         suggestion="检查 redo/undo 与批量写入，评估存储带宽与 WAL 配置。"),
    dict(key="host_disk_await_ms", kind="host", label="磁盘 IO 时延", unit="ms",
         thr=10.0, crit=30.0, title="磁盘 IO 响应时延偏高",
         suggestion="存储层出现拥塞，建议排查 IO 调度策略与底层磁盘健康状态。",
         attr_key="host_disk_top_io_procs"),
    # eBPF 内核级真实时延（块设备服务时间 p99，微秒）；精度高于 psutil 聚合 await
    dict(key="host_disk_latency_us_p99", kind="host", label="磁盘 IO 时延(p99)", unit="µs",
         thr=5000.0, crit=20000.0, title="磁盘 IO 时延 p99 偏高（内核级实测）",
         suggestion="eBPF 内核级实测块设备服务时间 p99 偏高；定位 host_disk_top_io_procs 中的高 IO 进程"
                    "（多为数据库物理读/写），优化其 IO 模式或升级存储。",
         attr_key="host_disk_top_io_procs"),
    # ══ 数据库连接与并发 ══
    dict(key="threads_connected", kind="db", label="MySQL 连接数", unit="",
         thr=400, crit=800, title="数据库连接数偏高",
         suggestion="检查连接泄漏与连接池配置，必要时提升 max_connections。"),
    dict(key="threads_running", kind="db", label="MySQL 活跃线程", unit="",
         thr=32, crit=64, title="MySQL 活跃线程堆积",
         suggestion="出现并发执行堆积，定位慢 SQL 或锁等待来源。"),
    dict(key="total_sessions", kind="db", label="SQL Server 会话数", unit="",
         thr=300, crit=500, title="数据库会话数偏高",
         suggestion="检查空闲会话与连接池配置，及时回收长空闲连接。"),
    dict(key="active_sessions", kind="db", label="活跃会话", unit="",
         thr=50, crit=100, title="活跃会话堆积",
         suggestion="定位阻塞源与会话长时间运行的原因，避免会话风暴。"),
    dict(key="user_sessions", kind="db", label="Oracle/达梦 用户会话", unit="",
         thr=300, crit=500, title="用户会话数偏高",
         suggestion="检查连接池与空闲会话回收，必要时调整 sessions/processes 参数。"),
    # ══ SQL 与复制健康 ══
    dict(key="rate_slow_queries", kind="db", label="慢查询增速", unit="/s",
         thr=1.0, crit=5.0, title="慢查询增速偏高",
         suggestion="排查新增慢 SQL，结合 SQL 治理专员做改写与索引优化。"),
    dict(key="seconds_behind_master", kind="db", label="主从延迟", unit="s",
         thr=30.0, crit=120.0, title="主从复制延迟偏大",
         suggestion="检查从库负载与网络，定位复制瓶颈（大事务/并行回放）。"),
    dict(key="replay_lag_sec", kind="db", label="PG 复制延迟", unit="s",
         thr=30.0, crit=120.0, title="PostgreSQL 复制延迟偏大",
         suggestion="检查从库回放能力与网络，定位复制瓶颈。"),
    # ══ 锁与事务 ══
    dict(key="deadlocks", kind="db", label="死锁", unit="",
         thr=1, crit=5, title="检测到死锁",
         suggestion="结合锁分析专员定位等待链，优化事务粒度与加锁顺序。"),
    dict(key="rate_xact_rollback", kind="db", label="事务回滚速率", unit="/s",
         thr=5.0, crit=20.0, title="事务回滚率偏高",
         suggestion="检查应用异常处理与唯一约束/外键冲突，降低无效回滚。"),
    # ══ 缓存效率 ══
    dict(key="cache_miss_pct", kind="db", label="缓冲未命中率", unit="%",
         thr=5.0, crit=15.0, title="缓冲池命中率偏低",
         suggestion="增大共享缓冲池或优化索引以降低物理读。"),
]


def _read_latest(instance_id: str) -> dict:
    """安全读取目标数据源最近一次监控快照，失败返回空。"""
    try:
        from pro.metrics_collector import get_collector

        c = get_collector()
        store = getattr(c, "store", None)
        if store is not None:
            return store.get_latest(instance_id) or {}
    except Exception:
        pass
    try:
        import os

        from pro.metrics_collector import MetricsStore

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(base, "data", "pro_metrics.db")
        if os.path.exists(db_path):
            return MetricsStore(db_path).get_latest(instance_id) or {}
    except Exception:
        pass
    return {}


def _derive_metrics(snap: dict) -> dict:
    """计算派生指标（缓冲未命中率等），就地返回快照副本。"""
    out = dict(snap)
    # 缓冲未命中率（PG：blks_read / (blks_read + blks_hit)）
    br = snap.get("blks_read")
    bh = snap.get("blks_hit")
    if isinstance(br, (int, float)) and isinstance(bh, (int, float)) and (br + bh) > 0:
        out["cache_miss_pct"] = round(br / (br + bh) * 100, 2)
    return out


class MonitorSentinel(Specialist):
    id = "monitor_sentinel"
    name = "运行监控哨兵"
    description = "紧盯宿主机真实资源与数据库细粒度指标，第一时间发现 CPU、IO、内存、连接、锁、复制等异常波动并预警。"
    tags = ["monitor", "anomaly"]

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        snap = _read_latest(ctx.target)
        out: List[Finding] = []
        meta = ctx.inputs.get("target_meta", {})
        instance_name = meta.get("instance_name", "")
        ts = snap.get("ts", "") if isinstance(snap, dict) else ""

        if not snap:
            detail = "该数据源尚未产生实时监控快照，无法做异常比对。"
            if instance_name:
                detail = f"{instance_name} 尚未产生实时监控快照，无法做异常比对。"
            out.append(
                Finding(
                    source=self.id,
                    category="anomaly",
                    severity="info",
                    title="监控数据暂未就绪",
                    detail=detail,
                    suggestion="确认监控采集已开启并稳定运行后重试。",
                )
            )
            return out

        snap = _derive_metrics(snap)
        prefix = f"{instance_name} " if instance_name else ""

        for chk in CHECKS:
            val = snap.get(chk["key"])
            if not isinstance(val, (int, float)):
                continue
            thr = chk["thr"]
            crit = chk["crit"]
            if val < thr:
                continue
            severity = "critical" if val >= crit else "warning"
            unit = chk["unit"]
            val_s = f"{val}{unit}" if unit else f"{val}"
            detail = f"最新采样 {chk['label']} = {val_s}（预警 {thr}{unit}，严重 {crit}{unit}）。"
            if ts:
                detail += f" 采样时间：{ts}。"
            if instance_name:
                detail = f"{instance_name} {detail}"
            tags = ["anomaly", chk["kind"], chk["key"]]
            # eBPF 内核级归因：命中且存在按进程的资源 TOP 列表时，附加上下文
            attr_key = chk.get("attr_key")
            if attr_key:
                procs = snap.get(attr_key)
                if isinstance(procs, list) and procs:
                    lines = []
                    for p in procs[:5]:
                        comm = p.get("comm", "?")
                        pid = p.get("pid", "?")
                        if attr_key == "host_disk_top_io_procs":
                            lines.append(f"    - {comm}(pid {pid}): {p.get('ms', 0)} ms / {p.get('ios', 0)} IO")
                        else:
                            lines.append(f"    - {comm}(pid {pid}): {p.get('ms', 0)} ms / {p.get('samples', 0)} samples")
                    if lines:
                        detail = detail.rstrip() + "\n    归因 Top 进程：\n" + "\n".join(lines)
            out.append(
                Finding(
                    source=self.id,
                    category="anomaly",
                    severity=severity,
                    title=chk["title"],
                    detail=detail,
                    suggestion=chk["suggestion"],
                    tags=tags,
                )
            )

        if not out:
            detail = "最近一次采样各核心指标均在合理区间。"
            if ts:
                detail += f"（采样时间：{ts}）"
            if instance_name:
                detail = f"{instance_name} {detail}"
            # 若有宿主机资源，额外给出资源水位摘要，便于巡检上下文
            host_keys = ["host_cpu", "host_mem", "host_disk_read_mb_s", "host_disk_write_mb_s"]
            parts = []
            for k in host_keys:
                v = snap.get(k)
                if isinstance(v, (int, float)):
                    parts.append(f"{k}={v}")
            if parts:
                detail += " 资源水位：" + "，".join(parts) + "。"
            # 标注本次宿主采集数据源精度
            src = snap.get("host_collector_source")
            if src == "ebpf":
                detail += " 宿主指标由 eBPF 内核级采集（块设备 IO 时延/进程归因精度更高）。"
            elif src == "psutil":
                detail += " 宿主指标由 psutil 用户态采集（无 eBPF，磁盘时延为聚合近似值）。"
            elif src == "unavailable":
                detail += " 宿主指标暂不可用（目标机未安装 psutil 或 SSH 不可达）。"
            out.append(
                Finding(
                    source=self.id,
                    category="anomaly",
                    severity="info",
                    title="实时监控未见明显异常",
                    detail=detail,
                    suggestion="保持监控，异常将由本能力主动发现。",
                )
            )
        return out
