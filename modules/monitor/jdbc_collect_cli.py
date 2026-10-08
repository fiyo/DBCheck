# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
modules/monitor/jdbc_collect_cli.py — 监控大屏 JDBC 批量采集子进程

作用：
  在**未被 gevent monkey-patch** 的干净子进程里，对 gbase / db2 / clickhouse
  等「无 python 原生驱动、必须走 JDBC」的数据库类型，一次连接执行本轮全部
  采集 SQL（连接会话 / 慢查询 / 性能计数 / 容量 Top），结果以 JSON 打印到
  stdout 后退出。

  主进程（web_ui / monitor engine / screen_collector 所在、gevent 已 patch 的
  进程）的 MonitorEngine._jdbc_run_batch 通过 subprocess 调用本 CLI，从而：
    - 绝不在主进程内 startJVM（否则 JPype/JVM 与 gevent hub 死锁 → 整个界面卡死）；
    - 每轮每实例只冷启动一次 JVM，全部 SQL 共享一次连接，摊薄开销。

  与 modules/jdbc_test_cli.py / jdbc_metrics_cli.py 同源设计：
  输入经 stdin 传入单个 JSON 对象，结果取 stdout 的「最后一个以 { 开头的行」。

入参 JSON 结构::

    {
      "db_type": "gbase" | "db2" | "clickhouse" | "oracle_jdbc" | ...,
      "host": "...", "port": 1521, "user": "...", "password": "***",
      "database": "...", "jdbc_url": "", "ssl": false,
      "service_name": "...", "sid": "...", "use_sid": false, "sysdba": false,
      "driver_version": "", "gbase_server_name": "gbase01",
      "queries": ["SELECT ...", "SELECT ..."]   # 有序，结果按 q0/q1/... 回填
    }

    Oracle（oracle_jdbc）必须透传 service_name / sid / use_sid / sysdba：
    缺 service_name 时 build_jdbc_url 会回落到 database 字段（监控路径把真实
    服务名放在此处），再回落硬编码默认 ORCLCDB；sys 用户需 sysdba=true 触发
    internal_logon=sysdba，否则 ORA-28009。

出参 JSON 结构（qN 与 queries 下标一一对应）::

    {"q0": {"rows": [{...}], "error": null}, "q1": {"rows": [], "error": "SQL失败..."}}
    {"fatal": "连接失败原因"}        # 建连失败时全部查询共用该错误
"""

import sys
import os
import json


def _ensure_project_root_on_path():
    """开发态直接执行本文件时确保项目根目录在 sys.path 上。

    冻结态由 PyInstaller 处理导入，无需干预。
    """
    if getattr(sys, 'frozen', False):
        return
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _root not in sys.path:
        sys.path.insert(0, _root)


# 必须在任何 modules.* 导入之前调用（sys.path 自举无法依赖尚未加载的模块）
_ensure_project_root_on_path()

from modules.jdbc_connector import open_jdbc_connection


def _cell(v):
    """驱动返回值 → JSON 可序列化的 python 原生值。

    jaydebeapi 已把绝大多数 JDBC 类型转成 python 原生值；残余的 JPype
    包装对象（java.lang.String 等）统一转 str，其余不可序列化值置 None。
    """
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    try:
        return str(v)
    except Exception:
        return None


def _collect(payload):
    """执行批量采集，返回 {qN: {'rows': [...], 'error': str|None}}。"""
    db_type = str(payload.get('db_type') or '').lower()
    queries = payload.get('queries') or []
    out = {}

    conn, meta = open_jdbc_connection(
        db_type,
        payload.get('host'),
        int(payload.get('port') or 0),
        payload.get('user') or '',
        payload.get('password') or '',
        driver_version=payload.get('driver_version') or '',
        jdbc_url=payload.get('jdbc_url') or None,
        database=payload.get('database') or None,
        service_name=payload.get('service_name') or None,
        sid=payload.get('sid') or None,
        use_sid=bool(payload.get('use_sid', False)),
        gbase_server_name=payload.get('gbase_server_name') or None,
        # sys 用户必须以 SYSDBA 身份登录（与插件 connect 行为一致），
        # 漏传 internal_logon 会导致 ORA-28009 / 连不上。
        properties={'internal_logon': 'sysdba'} if payload.get('sysdba') else None,
    )
    if conn is None:
        err = (meta or {}).get('error') or 'JDBC 连接失败'
        return {'q%d' % i: {'rows': [], 'error': err} for i in range(len(queries))}

    try:
        for i, sql in enumerate(queries):
            try:
                cur = conn.cursor()
                try:
                    cur.execute(sql)
                    cols = None
                    try:
                        # 列名强制 str：jaydebeapi/JPype 的 description 列名可能
                        # 是 java.lang.String 包装对象，直接当 JSON dict 键会
                        # TypeError（keys must be str ... not java.lang.String）。
                        cols = [str(d[0]) for d in (cur.description or [])]
                    except Exception:
                        cols = None
                    raw = cur.fetchall()
                finally:
                    try:
                        cur.close()
                    except Exception:
                        pass

                rows = []
                for r in raw or []:
                    vals = list(r.values()) if isinstance(r, dict) else list(r)
                    rows.append({
                        (cols[j] if cols and j < len(cols) else 'c%d' % j): _cell(v)
                        for j, v in enumerate(vals)
                    })
                out['q%d' % i] = {'rows': rows, 'error': None}
            except Exception as e:  # noqa: BLE001 — 单条 SQL 失败只降级该项
                out['q%d' % i] = {'rows': [], 'error': str(e)[:300]}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception as e:  # noqa: BLE001
        print(json.dumps({'fatal': 'bad payload: %s' % e}))
        return 2

    try:
        result = _collect(payload)
    except Exception as e:  # noqa: BLE001
        result = {'fatal': str(e)[:300]}

    # 仅这一行是结构化结果；驱动/插件的调试 print 也可能进 stdout，
    # 调用方只解析「最后一个以 { 开头的行」，即本行。
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
