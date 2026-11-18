# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
统一时序存储 —— 全系统唯一一份监控数据。

背景：
  改造前有三套互不相通的存储：
    1. ``MetricsCollector.store``（SQLite ``metrics_snapshot``，30s 一点）
       → 首页实时监控、大屏宿主资源折线；
    2. ``MonitorEngine`` 的 ``_slow_queries`` / ``_connections`` /
       ``_conn_history``（纯内存，10s）→ 首页实时慢查询 / 活跃连接 / 热力图；
    3. ``ScreenCollector`` 的 ``_snap`` / ``_prev`` / ``_spark`` / ``_trend``
       （纯内存，15s）→ 大屏 overview。
  同一时刻三个页面看到的其实是三轮不同采集的结果，既重复建连，又互相
  对不上时间。

本模块把它们合并成一种帧结构，所有读取方只经 ``UnifiedStore`` 投影：

    frame = {
      'iid', 'ts'(ISO), 'ts_epoch',
      'home':   {首页 payload：即时指标 + host_* + available/latency_ms/...},
      'screen': {大屏快照：conn/slowq/qps/tps/tbs/status/conn_rows/...},
      'conn':   {MonitorEngine 连接采集结果},
      'slow':   {MonitorEngine 慢查询采集结果},
    }

  - 内存 ``_frames`` 保存每个实例的最新一帧（低延迟，供大屏 / API）；
  - SQLite ``metrics_snapshot`` 保存完整帧的时序（供首页图表回填、
    大屏宿主资源历史序列），沿用原表结构与环形裁剪，旧数据可平滑读取；
  - ``_trend`` / ``_spark`` / ``_conn_history`` 等派生序列也在此统一维护。

兼容：所有对外方法名与返回结构保持旧类口径不变，前端与 API 零改动。
"""

import json
import os
import threading
from collections import deque

# 与 ScreenCollector 原口径一致
SPARK_POINTS = 60
TREND_POINTS = 60
CONN_HISTORY_POINTS = 72


class UnifiedStore:
    """内存最新帧 + SQLite 时序；读方法按各页面原有口径投影。"""

    def __init__(self, db_path=None):
        # 延迟导入，避免 app.py 启动期的模块环（metrics_collector 会导入
        # connection_factory，后者导入 monitor.queries）。
        from modules.pro.metrics_collector import MetricsStore
        if db_path is None:
            from modules.core import paths
            db_path = os.path.join(str(paths.DATA_DIR), 'pro_metrics.db')
        self.db_path = db_path
        self.ts = MetricsStore(db_path)
        self._lock = threading.RLock()
        self._frames = {}                 # iid -> 最新 frame
        self._spark = {}                  # iid -> deque(qps)
        self._trend = deque(maxlen=TREND_POINTS)
        self._conn_history = deque(maxlen=CONN_HISTORY_POINTS)
        self._last_collect_ts = 0.0
        self._rate_prev = {}          # iid -> 上一帧计数器（大屏速率差值状态）
        self.running = False
        self.interval = 15

    # ── 写入 ────────────────────────────────────────────────
    def write_frame(self, frame):
        """提交一帧：内存立即生效 + SQLite 落时序（保留环形裁剪）。"""
        iid = frame.get('iid')
        if not iid:
            return
        screen = frame.get('screen') or {}
        with self._lock:
            self._frames[iid] = frame
            qps = screen.get('qps')
            if qps is not None and not screen.get('pending'):
                self._spark.setdefault(iid, deque(maxlen=SPARK_POINTS)).append(qps)
            self._last_collect_ts = frame.get('ts_epoch') or 0.0
        try:
            # 完整帧入库：首页图表与大屏宿主序列都从这一份数据投影。
            self.ts.save_snapshot(iid, frame.get('ts') or '', frame)
        except Exception:
            # 落库失败绝不影响内存最新帧（大屏仍可用）。
            pass

    def append_trend(self, point):
        with self._lock:
            self._trend.append(point)

    def append_conn_history(self, point):
        with self._lock:
            self._conn_history.append(point)

    def note_round(self, ts_epoch=None):
        with self._lock:
            self._last_collect_ts = ts_epoch or self._last_collect_ts

    def set_pending(self, iid, screen):
        """只写内存的 pending 占位帧：让大屏首轮采集完成前就有结构可渲染。

        不落 SQLite，避免每轮多写一个无指标的时间点污染首页图表。
        """
        with self._lock:
            self._frames[str(iid)] = {
                'iid': str(iid), 'ts': '', 'ts_epoch': 0.0,
                'home': {}, 'screen': screen, 'conn': {}, 'slow': {},
                'host_res': None, 'pending': True,
            }

    def get_rate_prev(self, iid):
        with self._lock:
            return self._rate_prev.get(str(iid))

    def set_rate_prev(self, iid, prev):
        with self._lock:
            self._rate_prev[str(iid)] = prev

    def drop_rate_prev(self, iid):
        with self._lock:
            self._rate_prev.pop(str(iid), None)

    # ── 首页实时监控口径 ──────────────────────────────────────
    @staticmethod
    def _home_of(frame):
        """从帧里取出首页 payload；兼容改造前直接存 home 字典的老数据。"""
        if not isinstance(frame, dict):
            return {}
        home = frame.get('home')
        if isinstance(home, dict):
            return home
        if 'iid' in frame or 'screen' in frame:
            return {}
        return frame  # 老版本 payload 就是首页快照本身

    def get_latest(self, instance_id):
        """首页 summary / 大屏宿主资源读的就是这个 dict（含 host_*）。"""
        with self._lock:
            frame = self._frames.get(str(instance_id))
        if frame is not None:
            return dict(self._home_of(frame))
        try:
            return dict(self._home_of(self.ts.get_latest(str(instance_id))))
        except Exception:
            return {}

    def get_recent(self, instance_id, limit=120):
        """首页图表回填 / 大屏宿主历史序列：[{'ts', 'metrics': 首页 payload}]。"""
        try:
            rows = self.ts.get_recent(str(instance_id), limit)
        except Exception:
            return []
        return [{'ts': r.get('ts'), 'metrics': self._home_of(r.get('metrics'))}
                for r in rows]

    # ── 大屏口径 ────────────────────────────────────────────
    def get_snapshot(self):
        """``/api/dashboard/overview`` 的快照：screen 字段 + spark + host_res。"""
        with self._lock:
            frames = dict(self._frames)
            sparks = {k: list(v) for k, v in self._spark.items()}
        out = {}
        for iid, frame in frames.items():
            d = dict(frame.get('screen') or {})
            d['spark'] = sparks.get(iid, [])
            d['host_res'] = frame.get('host_res')
            out[iid] = d
        return out

    def get_trend(self):
        with self._lock:
            return list(self._trend)

    def get_status(self):
        with self._lock:
            return {
                'running': bool(self.running),
                'interval': int(self.interval),
                'last_collect_ts': self._last_collect_ts,
                'data_source_count': len(self._frames),
            }

    # ── 首页实时慢查询 / 活跃连接 / 热力图口径 ─────────────────
    def get_connections(self):
        with self._lock:
            return {iid: dict((f.get('conn') or {}))
                    for iid, f in self._frames.items()}

    def get_slow_queries(self):
        with self._lock:
            return {iid: dict((f.get('slow') or {}))
                    for iid, f in self._frames.items()}

    def get_conn_history(self):
        with self._lock:
            return list(self._conn_history)

    # ── 内部：供统一采集器读上一帧（速率差值 / 宿主节流）────────
    def save_snapshot(self, instance_id, ts, snapshot):
        """兼容旧 MetricsStore 调用签名：直接把首页 payload 包成一帧。"""
        self.write_frame({
            'iid': str(instance_id), 'ts': ts, 'ts_epoch': 0.0,
            'home': snapshot if isinstance(snapshot, dict) else {},
            'screen': {}, 'conn': {}, 'slow': {}, 'host_res': None,
        })

    def get_frame(self, instance_id):
        with self._lock:
            return self._frames.get(str(instance_id))

    def drop_instances(self, keep_ids):
        """删除已移除实例的残留帧，与旧 ScreenCollector._round 行为一致。"""
        keep = set(str(x) for x in keep_ids)
        with self._lock:
            for stale in [k for k in self._frames if k not in keep]:
                self._frames.pop(stale, None)
                self._spark.pop(stale, None)
                self._rate_prev.pop(stale, None)
