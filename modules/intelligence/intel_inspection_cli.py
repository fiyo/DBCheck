#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""智能诊断中心 · 深度巡检隔离子进程入口。

背景
----
智能诊断中心的「深度巡检分析专员」(inspection_expert) 会调用
``run_target_inspection`` 实时跑巡检引擎。对于 HGDB / DB2 / SQL Server(JDBC) /
oracle_jdbc 这类依赖 JPype 在**当前进程内**启动 JVM 的数据源，JVM 的原生线程会
把 gevent 协作式服务器的 hub 钉死，导致整个 Web 界面冻结——前端表现为「深度巡检
分析专员」一直卡在「工作中」。

这与「测试连接卡死」「开始巡检卡死」是同根因，只是发生在智能诊断中心的另一调用
路径上。

本 CLI 把整段 ``run_target_inspection`` 放到**未被 gevent monkey-patch 的干净子
进程**里执行，主进程（SSE 流）只负责 spawn、用协作式轮询读取 stdout 中的结果行，
再做超时兜底。JVM 只活在子进程里，主进程 hub 不受影响。

协议
----
- 子进程由 ``<python|dbcheck.exe> --intelligence-inspection-cli`` 启动；
- 入参以单个 JSON 对象经 stdin 传入（避免密码出现在进程列表）；
- 结果行格式：``__DBCHECK_INTEL_INSP_RESULT__{...json...}``；
- 进程内设置 ``DBCheck_INTEL_INSP_SUBPROCESS=1``，防止 ``run_target_inspection``
  内部再次委派出子进程造成递归。

入参 JSON 结构::

    {
      "db_type": "hgdb",
      "instance": { "host": "...", "port": 5866, "user": "...", "password": "...", ... },
      "inspector_name": "Jack",
      "template_id": null
    }
"""

import json
import os
import sys

RESULT_PREFIX = "__DBCHECK_INTEL_INSP_RESULT__"

# 本 CLI 需要隔离执行的 JVM 数据库类型（与 web/app.py 的 JVM_INSPECTION_DB_TYPES 对齐）
INTEL_JVM_DB_TYPES = ('hgdb', 'db2', 'sqlserver_jdbc', 'oracle_jdbc', 'dm', 'gbase', 'clickhouse', 'uxdb')


def _ensure_project_root_on_path():
    """开发态直接执行本文件时确保项目根目录（含 modules 包）在 sys.path 上。"""
    if getattr(sys, 'frozen', False):
        return
    # __file__ = <root>/modules/intelligence/intel_inspection_cli.py
    # 上溯三级 => <root>
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _root not in sys.path:
        sys.path.insert(0, _root)


def _tcp_preflight(host, port, timeout=8):
    """JVM 之前先做一次纯 Python 的 TCP 连通性探测。

    不可达/端口未开是最常见的失败场景，用 socket 先探可在数秒内给出准确原因，
    不必等 JVM 冷启动 + JDBC 驱动自身超时（可能长达 20 秒以上）。
    """
    import socket

    if not host:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    try:
        sock = socket.create_connection((str(host), port), timeout=timeout)
        sock.close()
        return None
    except socket.timeout:
        return (f'无法连接 {host}:{port}（TCP 握手超时 {timeout} 秒）。'
                f'请检查主机地址、端口是否正确，以及防火墙/安全组是否放行。')
    except OSError as e:
        return (f'无法连接 {host}:{port}（{e.strerror or e}）。'
                f'请确认数据库服务已启动并在该端口监听。')


def _emit(result):
    """把结构化结果序列化成一行 stdout 输出。"""
    try:
        line = RESULT_PREFIX + json.dumps(result, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        # 兜底必须带异常信息：否则只会看到"结果序列化失败"，无法定位
        # 是哪个字段/键类型触发（如 JPype java.lang.String 键）
        line = RESULT_PREFIX + json.dumps(
            {"ok": False, "error": f"结果序列化失败: {type(e).__name__}: {e}",
             "auto_analyze": []},
            ensure_ascii=False)
    sys.stdout.write(line + "\n")
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


def main(argv=None):
    """子进程入口：stdin 读 JSON，运行巡检，stdout 输出结果行。"""
    _ensure_project_root_on_path()

    # 标记已隔离：防止 run_target_inspection 内部再次委派出子进程（递归）
    os.environ['DBCheck_INTEL_INSP_SUBPROCESS'] = '1'

    raw = ''
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        raw = ''

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "error": f"巡检子进程入参 JSON 解析失败: {e}",
               "auto_analyze": []})
        return 2

    db_type = (payload.get('db_type') or '').strip()
    if db_type not in INTEL_JVM_DB_TYPES:
        _emit({"ok": False, "error": f"暂不支持的数据库类型：{db_type}",
               "auto_analyze": []})
        return 2

    instance = payload.get('instance') or {}
    inspector_name = payload.get('inspector_name') or 'Jack'
    template_id = payload.get('template_id')

    # TCP 预检：自定义 jdbc_url 可能指向多主机/故障转移，此时跳过预检
    _kw = instance.get('jdbc_url')
    if not _kw:
        _preflight_err = _tcp_preflight(
            instance.get('host') or instance.get('ip'), instance.get('port'))
        if _preflight_err:
            _emit({"ok": False, "error": _preflight_err, "auto_analyze": []})
            return 0

    try:
        from modules.intelligence.inspection_runner import _run_target_inspection_inline
        result = _run_target_inspection_inline(db_type, instance, inspector_name, template_id)
    except Exception as e:  # noqa: BLE001
        import traceback
        result = {
            "ok": False,
            "error": f"{e}\n{traceback.format_exc()}",
            "auto_analyze": [],
            "db_type": db_type,
        }

    if not isinstance(result, dict):
        result = {"ok": False, "error": "巡检引擎返回了非预期结果", "auto_analyze": []}
    _emit(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
