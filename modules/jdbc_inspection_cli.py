#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""JDBC 数据库巡检任务隔离子进程入口。

背景
----
HGDB / DB2 / SQL Server(JDBC) / oracle_jdbc 等插件依赖 JPype 在**当前进程内**
启动 JVM 并调用 ``DriverManager.getConnection``。Web 主进程跑在 gevent 协作式
服务器上，JVM 的原生线程会把 gevent hub 钉死，导致点击「开始巡检」后：

- 前端日志面板空白；
- 后端控制台无输出；
- 整个界面失去响应。

这与「测试连接卡死」是同根因，但发生在 ``run_inspection_task`` 的巡检执行路径。

本 CLI 把整个 ``run_inspection_task`` 放到**未被 gevent monkey-patch 的干净子进程**
里执行，主进程只负责 spawn、读取 stdout 中的事件行、转发给前端 WebSocket/SSE。

协议
----
- 子进程由 ``<python|dbcheck.exe> --jdbc-inspection-cli`` 启动；
- 入参以单个 JSON 对象经 stdin 传入（避免密码出现在进程列表）；
- 子进程内把 ``socketio.emit`` 劫持为 stdout 事件行输出；
- 事件行格式：``__DBCHECK_INSP_EVENT__{"event":"log","data":{...}}``；
- 结束行：``__DBCHECK_INSP_DONE__{"status":"done|error", ...}``。

入参 JSON 结构::

    {
      "task_id": "uuid",
      "db_type": "hgdb",
      "db_info": {
        "ip": "127.0.0.1",
        "port": 5866,
        "user": "highgo",
        "password": "***",
        "database": "highgo",
        ...
      },
      "inspector_name": "Jack",
      "template_id": null,
      "chapter_ids": null
    }
"""

import json
import os
import sys
import traceback

RESULT_PREFIX = "__DBCHECK_INSP_EVENT__"
DONE_PREFIX = "__DBCHECK_INSP_DONE__"

# 本 CLI 需要隔离执行的 JVM 数据库类型
# 全部走 JDBC 的巡检类型：6 个 JDBC 插件 + 核心内置 dm/gbase/ivorysql（与 driver_registry.JDBC_PLUGIN_TO_CATALOG 对齐）
JVM_INSPECTION_DB_TYPES = ('hgdb', 'db2', 'sqlserver_jdbc', 'oracle_jdbc', 'dm', 'gbase', 'clickhouse', 'uxdb', 'ivorysql', 'halodb', 'pg', 'kingbase', 'yashandb', 'mysql', 'mariadb', 'tidb', 'oceanbase')


def _ensure_project_root_on_path():
    """开发态直接执行本文件时确保项目根目录在 sys.path 上。"""
    if getattr(sys, 'frozen', False):
        return
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)


def _tcp_preflight(host, port, timeout=8):
    """JDBC 之前先做一次纯 Python 的 TCP 连通性探测。

    与 ``jdbc_test_cli._tcp_preflight`` 逻辑一致：主机不可达/端口未开是最常见的
    失败场景，用 socket 先探一下可在几秒内给出准确原因，不必等 JVM 冷启动+
    JDBC 驱动自身超时（可能长达 20 秒以上）。
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


def _emit_line(event, data):
    """把 socketio 事件序列化成一行 stdout 输出。

    编码安全：ensure_ascii=True 保证输出纯 ASCII（中文变 \\uXXXX，读侧
    json.loads 自动还原），并用 buffer 直写绕开 TextIOWrapper——
    PyInstaller 冻结版子进程不尊重 PYTHONIOENCODING，文本写会按
    Windows ANSI(GBK) 落盘，主进程按 UTF-8 读即成乱码。
    """
    try:
        line = RESULT_PREFIX + json.dumps({'event': event, 'data': data}, ensure_ascii=True)
    except Exception:  # noqa: BLE001
        line = RESULT_PREFIX + json.dumps({'event': event, 'data': {}})
    _write_stdout(line + '\n')


def _write_stdout(text):
    """按 UTF-8 直写 stdout buffer，失败退回文本写。"""
    try:
        sys.stdout.buffer.write(text.encode('utf-8'))
        sys.stdout.buffer.flush()
    except Exception:  # noqa: BLE001
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except Exception:
            pass


def _make_emitter(task_id):
    """构造替换 ``socketio.emit`` 的可调用对象。

    ``run_inspection_task`` 内的事件调用形如 ``emit(event, data, room=task_id)``；
    我们忽略 room，只把事件写到 stdout，供主进程转发给前端。
    """
    def _emit(event, data, **kwargs):
        # 不转发 room/skip_sid 等 kwargs
        _emit_line(event, data)
    return _emit


def main(argv=None):
    """子进程入口：stdin 读 JSON，执行巡检任务，stdout 输出事件流。"""
    _ensure_project_root_on_path()

    raw = ''
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        raw = ''

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as e:  # noqa: BLE001
        _emit_line('error', {'msg': f'巡检子进程入参 JSON 解析失败: {e}'})
        _emit_line('done', {'msg': '巡检子进程入参 JSON 解析失败', 'task_id': None})
        return 2

    task_id = payload.get('task_id')
    db_type = (payload.get('db_type') or '').strip()
    db_info = payload.get('db_info') or {}
    db_info['_db_type'] = db_type

    if not task_id:
        _emit_line('error', {'msg': '巡检子进程缺少 task_id'})
        _emit_line('done', {'msg': '巡检子进程缺少 task_id', 'task_id': task_id})
        return 2

    if db_type not in JVM_INSPECTION_DB_TYPES:
        _emit_line('error', {'msg': f'巡检子进程暂不支持的数据库类型: {db_type}'})
        _emit_line('done', {'msg': f'不支持的数据库类型: {db_type}', 'task_id': task_id})
        return 2

    # 导入主应用模块（会初始化 Flask/SocketIO，但在子进程里不会被 patch）
    try:
        import modules.web.app as app
    except Exception as e:  # noqa: BLE001
        _emit_line('error', {'msg': f'巡检子进程无法加载应用模块: {e}'})
        _emit_line('done', {'msg': f'无法加载应用模块: {e}', 'task_id': task_id})
        return 2

    # 劫持 socketio.emit，使 run_inspection_task 的日志/事件直接写到 stdout
    app.socketio.emit = _make_emitter(task_id)

    # 初始化任务记录（与主进程 tasks 结构保持一致）
    app.tasks[task_id] = {
        'id': task_id,
        'db_type': db_type,
        'db_info': db_info,
        'inspector': payload.get('inspector_name', 'Jack'),
        'template_id': payload.get('template_id'),
        'chapter_ids': payload.get('chapter_ids'),
        'status': 'running',
        'log': [],
    }

    # TCP 预检：自定义 jdbc_url 可能指向多主机/故障转移，此时跳过预检
    _kw = db_info.get('jdbc_url') or db_info.get('ssh_info', {}).get('jdbc_url')
    if not _kw:
        _preflight_err = _tcp_preflight(db_info.get('ip') or db_info.get('host'), db_info.get('port'))
        if _preflight_err:
            _emit_line('error', {'msg': _preflight_err})
            _emit_line('done', {'msg': _preflight_err, 'task_id': task_id})
            try:
                _write_stdout(DONE_PREFIX + json.dumps(
                    {'status': 'error', 'task_id': task_id, 'error_msg': _preflight_err},
                    ensure_ascii=True) + '\n')
            except Exception:
                pass
            return 0

    def _task_snapshot(_t):
        """把子进程 task 中前端需要的关键字段提取出来，供主进程合并。

        主进程 /api/task_status 依赖 task['result'] / task['auto_analyze'] /
        task['report_file'] / task['report_name'] 展示健康评分与巡检结果。
        子进程内存独立，这些字段必须通过 stdout 结束行同步回去。
        """
        _report_path = _t.get('report_path') if _t else None
        if not _report_path:
            # 防御性兜底：run_inspection_task 主线写的是 report_file，
            # 二者同源（均为生成的 docx 绝对路径），避免 report_path 缺失时
            # 调度器误判为「Word 报告渲染失败」。
            _report_path = _t.get('report_file') if _t else None
        if not _report_path:
            _res = _t.get('result') if _t else None
            if isinstance(_res, dict):
                _report_path = _res.get('report_file')
        return {
            'status': _t.get('status', 'done') if _t else 'error',
            'task_id': task_id,
            'report_path': _report_path,
            'ai_advice': _t.get('ai_advice') if _t else None,
            'error_msg': _t.get('error_msg') if _t else None,
            'result': _t.get('result') if _t else None,
            'auto_analyze': _t.get('auto_analyze') if _t else None,
            'report_file': _t.get('report_file') if _t else None,
            'report_name': _t.get('report_name') if _t else None,
        }

    final_status = {'status': 'error', 'task_id': task_id}
    try:
        app.run_inspection_task(
            task_id,
            db_info,
            payload.get('inspector_name', 'Jack'),
            payload.get('template_id'),
            payload.get('chapter_ids'),
        )
        task = app.tasks.get(task_id, {})
        final_status = _task_snapshot(task)
    except SystemExit:
        # run_inspection_task 内部某些分支可能直接 sys.exit，视为异常结束
        task = app.tasks.get(task_id, {})
        final_status = _task_snapshot(task)
        final_status['status'] = task.get('status', 'error')
        final_status['error_msg'] = task.get('error_msg') or '巡检子进程异常退出'
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        _emit_line('error', {'msg': f'巡检子进程执行异常: {e}\n{tb}'})
        final_status = {
            'status': 'error',
            'task_id': task_id,
            'error_msg': str(e),
        }

    # 输出最终结束行，主进程据此判定任务完成
    try:
        _write_stdout(DONE_PREFIX + json.dumps(final_status, ensure_ascii=True) + '\n')
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
