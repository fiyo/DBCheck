# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""协同诊断中枢 · 实时巡检引擎调度。

让「深度巡检分析专员」直接调用 DBCheck 巡检引擎，为目标数据源
实时产出一份巡检报告（getData → checkdb → 智能分析），再交给专员解析，
从而保证诊断结论与所选数据源的真实状态严格相关。
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from typing import Any, Dict, List, Optional

from modules.core import paths

# 确保项目根目录在 sys.path（与 web_ui 运行环境一致）。
# 一律以 modules.core.paths.PROJECT_ROOT 为准，禁止用 __file__ 上溯推算：
# intelligence/ 迁入 modules/intelligence/ 后，__file__ 上溯两级只到
# D:/DBCheck/modules，插入的并非项目根，本注释所述保障实际失效。
_SCRIPT_DIR = str(paths.PROJECT_ROOT)


def _db_default(db_type: str) -> str:
    return {
        "mysql": "mysql",
        "mariadb": "mysql",
        "pg": "postgres",
        "oracle": "orcl",
        "oracle_jdbc": "orcl",
        "dm": "DAMENG",
        "sqlserver": "master",
        "tidb": "mysql",
        "oceanbase": "sys",
        "ivorysql": "ivorysql",
        "kingbase": "kingbase",
        "yashandb": "YASHANDB",
        "gbase": "testdb",
    }.get(db_type, "")


def _build_db_info(db_type: str, instance: Dict[str, Any]) -> Dict[str, Any]:
    """把实例管理器的解密信息映射成巡检引擎需要的 db_info。"""
    label = instance.get("name") or instance.get("host") or "unknown"
    return {
        "host": instance.get("host", ""),
        "port": int(instance.get("port", 0) or 0),
        "user": instance.get("user", ""),
        "password": instance.get("password", ""),
        "database": instance.get("database") or _db_default(db_type),
        "service_name": instance.get("service_name"),
        "sid": instance.get("sid"),
        "sysdba": bool(instance.get("sysdba", False)),
        "name": instance.get("name", ""),
        "label": label,
    }


def _score(context: Dict[str, Any]):
    """依据巡检上下文计算健康评分 / 风险等级（与 web_ui 流程一致）。"""
    risk_count = context.get("risk_count", 0)
    if not risk_count:
        issues = context.get("issues", [])
        risk_count = len(issues) if isinstance(issues, list) else 0
    health_status = context.get("health_status", "")
    if "优秀" in health_status or "Excellent" in health_status:
        health_score = 100
    elif "良好" in health_status or "Good" in health_status:
        health_score = 80
    elif "一般" in health_status or "Fair" in health_status:
        health_score = 60
    elif "需关注" in health_status or "Attention" in health_status:
        health_score = 40
    else:
        health_score = 100 - min(risk_count * 5, 50)
    if health_score >= 85:
        risk_level = "healthy"
    elif health_score >= 70:
        risk_level = "low"
    elif health_score >= 50:
        risk_level = "medium"
    elif health_score >= 30:
        risk_level = "high"
    else:
        risk_level = "critical"
    return health_score, risk_count, risk_level, health_status


def _sanitize_for_json(obj: Any) -> Any:
    """把巡检 context 递归转换为 JSON 安全类型。

    巡检 checkdb 结果可能含有 datetime / Decimal / bytes / set 等非 JSON 原生
    类型，直接 json.dumps 会抛异常。智能诊断中心把巡检结果经子进程 stdout
    回传时依赖 json 序列化（见 intel_inspection_cli._emit），此处统一转字符串，
    既保证子进程回传不崩，也让定向分析专员只做字符串呈现，无副作用。

    注意 dict 的键也要转：JDBC/JPype 插件（HGDB/DB2/SQLServer 等 JVM 类型）
    采集出的行字典键可能是 java.lang.String 包装对象，json.dumps 会抛
    "keys must be str, int, float, bool or None, not java.lang.String"，
    导致整个子进程巡检结果退化成 ok=False。
    """
    if isinstance(obj, dict):
        return {
            (k if isinstance(k, (str, int, float, bool)) or k is None else str(k)):
                _sanitize_for_json(v)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float, str)) or obj is None:
        return obj
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", "replace")
        except Exception:
            return repr(obj)
    return str(obj)


# 插件类型 db_type → smart_analyze_* 名称（当插件 get_task_config 未声明 smart_analyze 时的兜底）
_PLUGIN_ANALYZER_MAP = {
    "uxdb": "smart_analyze_pg",
    "redis": "smart_analyze_redis",
    "redis-cluster": "smart_analyze_redis_cluster",
    "mongodb": "smart_analyze_mongodb",
    "clickhouse": "smart_analyze_clickhouse",
    "db2": "smart_analyze_db2",
}


def _build_plugin_info(db_type: str, instance: Dict[str, Any]) -> Dict[str, Any]:
    """把实例管理器字典映射为插件 connect_test/getData 期望的 pinfo。

    与 web_ui.py:580-877 的特例对齐：
      - uxdb：database 为空或 'postgres' 时置 'uxdb'
      - mongodb：透传 connect_mode/auth_source/auth_mechanism/replica_set/tls*
      - redis/redis-cluster：透传 database/seed_nodes
    其余字段（ip/host/port/user/password/name/ssh_*）原样透传。
    """
    db = instance.get("database")
    if db_type == "mongodb" and not db:
        db = "admin"
    elif not db:
        db = _db_default(db_type)
    pinfo: Dict[str, Any] = {
        "ip": instance.get("host", ""),
        "host": instance.get("host", ""),
        "port": int(instance.get("port", 0) or 0),
        "user": instance.get("user", ""),
        "password": instance.get("password", ""),
        "name": instance.get("name", ""),
        "database": db,
        "template_id": instance.get("template_id"),
    }
    for _k in ("ssh_host", "ssh_port", "ssh_user", "ssh_password", "ssh_key_file"):
        if _k in instance:
            pinfo[_k] = instance[_k]
    # 类型特例
    if db_type == "uxdb":
        if not pinfo.get("database") or pinfo.get("database") == "postgres":
            pinfo["database"] = "uxdb"
    if db_type == "mongodb":
        for _mk in ("connect_mode", "auth_source", "auth_mechanism",
                    "replica_set", "tls", "tls_ca_file", "tls_cert_key_file",
                    "tls_allow_invalid_certs"):
            if _mk in instance:
                pinfo[_mk] = instance[_mk]
    if db_type in ("redis", "redis-cluster"):
        for _mk in ("database", "seed_nodes"):
            if _mk in instance:
                pinfo[_mk] = instance[_mk]
    return pinfo


def _run_plugin_inspection(
    db_type: str,
    instance: Dict[str, Any],
    goal: str = "",
    inspector_name: str = "Jack",
) -> Optional[Dict[str, Any]]:
    """插件类型实时巡检（复用 plugin_loader 已验证路径，与 Web UI 同源）。

    成功返回与内置路径同构的 report dict；找不到可用插件返回 None（交由主流程回退
    「暂不支持」）；插件连接/采集/分析失败则返回 ok=False 的同构 report（软降级，
    由上层 inspection_expert 走「回退历史报告」，诊断整体不崩溃）。
    """
    instance_name = instance.get("name") or instance.get("host") or ""

    # 配置加载（驱动缺失可能导致插件模块导入失败 → 软降级为 ok=False report）
    try:
        from modules.pluginkit.loader import get_plugin_task_config

        cfg = get_plugin_task_config(db_type)
    except Exception as e:
        return {
            "ok": False,
            "error": f"插件配置加载失败：{e}",
            "auto_analyze": [],
            "db_type": db_type,
            "instance_name": instance_name,
        }
    if not cfg:
        return None

    try:
        import importlib.util
        import os

        plugin_path = cfg.get("plugin_path")
        main_file = cfg.get("main_file", "main_plugin.py")
        if not plugin_path:
            raise RuntimeError("插件路径缺失")
        main_path = os.path.join(plugin_path, main_file)
        spec = importlib.util.spec_from_file_location(f"plugin_{db_type}", main_path)
        if spec is None:
            raise RuntimeError("插件模块加载失败")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        pinfo = _build_plugin_info(db_type, instance)

        # 连接测试（与 web_ui.py:855 一致）
        ok, ver = cfg["connect_test"](*cfg["connect_test_args"](pinfo))
        if not ok:
            raise RuntimeError(f"连接失败：{ver}")

        # 数据采集（与 web_ui.py:894-905 一致）
        _pos, _kw = cfg["getdata_args"](pinfo)
        data = mod.getData(*_pos, **_kw)
        conn_attr = cfg.get("conn_attr")
        if conn_attr and getattr(data, conn_attr, None) is None:
            raise RuntimeError("数据采集失败（无可用连接）")

        # 产出巡检上下文（优先 getData→checkdb，退化到 collect_data）
        if hasattr(data, "checkdb") and callable(data.checkdb):
            context = data.checkdb("builtin")
        elif hasattr(mod, "collect_data"):
            cd = mod.collect_data("")
            if isinstance(cd, tuple) and len(cd) == 2 and cd[0] is False:
                raise RuntimeError(cd[1])
            context = cd if isinstance(cd, dict) else {}
        else:
            context = {}
        if not context:
            raise RuntimeError("巡检上下文为空")

        context["co_name"] = [{"CO_NAME": pinfo.get("database") or pinfo.get("name", "")}]
        context["port"] = [{"PORT": pinfo.get("port")}]
        context["ip"] = [{"IP": pinfo.get("ip")}]

        # 智能分析（优先插件声明的 smart_analyze，缺失回退 _PLUGIN_ANALYZER_MAP）
        analyzer_name = cfg.get("smart_analyze") or _PLUGIN_ANALYZER_MAP.get(db_type)
        auto_analyze: List[Dict[str, Any]] = []
        if analyzer_name:
            import modules.inspection.analyzer as _analyzer

            try:
                auto_analyze = list(getattr(_analyzer, analyzer_name)(context) or [])
            except Exception:
                auto_analyze = []

        # 插件附加规则（尽力而为，失败不影响主流程）
        try:
            from modules.pluginkit.core import run_plugin_inspections_for_db

            plugin_issues = run_plugin_inspections_for_db(cfg["history_db_type"], context)
            if plugin_issues:
                auto_analyze = auto_analyze + list(plugin_issues)
        except Exception:
            pass

        health_score, risk_count, risk_level, health_status = _score(context)
        return {
            "ok": True,
            "db_type": db_type,
            "instance_name": instance_name,
            "auto_analyze": auto_analyze,
            "report_file": None,
            "report_name": None,
            "health_score": health_score,
            "risk_count": risk_count,
            "risk_level": risk_level,
            "health_status": health_status,
            "ai_advice": context.get("ai_advice", ""),
            # 与内置 runner 路径对齐：透出 checkdb 巡检结果（含各章节数据段，
            # 如 HGDB 的 hgdb_conn_summary / hgdb_connections），供定向分析专员
            # 按问题主题精准抽取对应数据段；同样经 _sanitize_for_json 保证
            # JVM 子进程 stdout 回传时 JSON 序列化不失败。
            "context": _sanitize_for_json(context),
            "error": None,
        }
    except Exception as e:
        # 软降级：任何连接/采集/分析异常都返回 ok=False 同构 report，不抛到上层
        return {
            "ok": False,
            "error": str(e),
            "auto_analyze": [],
            "db_type": db_type,
            "instance_name": instance_name,
        }


def _run_target_inspection_inline(
    db_type: str,
    instance: Dict[str, Any],
    inspector_name: str = "Jack",
    template_id=None,
) -> Dict[str, Any]:
    """为目标数据源实时运行巡检引擎，返回结构化结果（进程内版本）。

    仅由 ``run_target_inspection``（主进程委派子进程时）或隔离子进程 CLI 直接调用；
    不要在 gevent 主进程里直接调用本函数处理 JVM 类型，否则会触发 hub 冻结。
    """
    if _SCRIPT_DIR not in sys.path:
        sys.path.insert(0, _SCRIPT_DIR)

    import modules.inspection.run as ri

    _RUNNER_MAP = {
        "mysql": ri.run_mysql,
        "mariadb": ri.run_mariadb,
        "pg": ri.run_pg,
        "oracle": ri.run_oracle_full,
        "oracle_jdbc": ri.run_oracle_full,
        "dm": ri.run_dm,
        "sqlserver": ri.run_sqlserver,
        "tidb": ri.run_tidb,
        "ivorysql": ri.run_ivorysql,
        "oceanbase": ri.run_oceanbase,
        "kingbase": ri.run_kingbase,
        "yashandb": ri.run_yashandb,
        "gbase": ri.run_gbase,
    }
    _ENGINE_DB_TYPE = {
        "oracle_jdbc": "oracle",
    }
    _ANALYZER_MAP = {
        "mysql": "smart_analyze_mysql",
        "mariadb": "smart_analyze_mariadb",
        "pg": "smart_analyze_pg",
        "oracle": "smart_analyze_oracle",
        "dm": "smart_analyze_dm",
        "sqlserver": "smart_analyze_sqlserver",
        "tidb": "smart_analyze_tidb",
        "ivorysql": "smart_analyze_ivorysql",
        "kingbase": "smart_analyze_kingbase",
        "yashandb": "smart_analyze_yashandb",
        "oceanbase": "smart_analyze_mysql",
        "gbase": "smart_analyze_gbase",
    }

    runner = _RUNNER_MAP.get(db_type)
    if runner is not None:
        db_info = _build_db_info(db_type, instance)
        engine_db_type = _ENGINE_DB_TYPE.get(db_type, db_type)

        try:
            result = runner(db_info, inspector_name, None)
            # result 形如 (report_file, report_name, context)
            if not isinstance(result, tuple) or len(result) < 3:
                return {"ok": False, "error": "巡检引擎未返回上下文", "auto_analyze": []}
            ofile, fname, context = result[0], result[1], result[2]
            if not context:
                return {"ok": False, "error": "巡检引擎返回空上下文", "auto_analyze": []}

            # 智能分析（与 web_ui 巡检流程一致）
            auto_analyze: List[Dict[str, Any]] = []
            analyzer_name = _ANALYZER_MAP.get(engine_db_type)
            try:
                import modules.inspection.analyzer as analyzer

                fn = getattr(analyzer, analyzer_name, None)
                if fn:
                    auto_analyze = list(fn(context) or [])
            except Exception:
                auto_analyze = []

            # 插件附加风险（尽力而为，失败不影响主流程）
            try:
                from modules.pluginkit.core import run_plugin_inspections_for_db

                plugin_issues = run_plugin_inspections_for_db(engine_db_type, context)
                if plugin_issues:
                    auto_analyze = auto_analyze + list(plugin_issues)
            except Exception:
                pass

            health_score, risk_count, risk_level, health_status = _score(context)
            return {
                "ok": True,
                "db_type": db_type,
                "instance_name": db_info.get("label", ""),
                "auto_analyze": auto_analyze,
                "report_file": ofile,
                "report_name": fname,
                "health_score": health_score,
                "risk_count": risk_count,
                "risk_level": risk_level,
                "health_status": health_status,
                "ai_advice": context.get("ai_advice", ""),
                # 透出 checkdb 巡检结果（含各章节数据段），供智能诊断中心定向分析专员
                # 按问题主题精准抽取对应数据段；不影响既有调用方（仅新增字段）。
                # 经 _sanitize_for_json 转 JSON 安全类型，避免子进程 stdout 回传时
                # 因 datetime/Decimal 等非原生类型导致序列化失败（见 intel_inspection_cli._emit）。
                "context": _sanitize_for_json(context),
                "error": None,
            }
        except Exception as e:  # 实时巡检失败，交由上层回退历史报告
            return {
                "ok": False,
                "error": str(e),
                "auto_analyze": [],
                "db_type": db_type,
                "instance_name": db_info.get("label", ""),
            }

    # ── 插件路径（复用 plugin_loader 已验证机制，与 Web UI 同源）──
    plugin_report = _run_plugin_inspection(db_type, instance, "", inspector_name)
    if plugin_report is not None:
        return plugin_report

    # ── 暂不支持兜底（保持原文案）──
    return {"ok": False, "error": f"暂不支持的数据库类型：{db_type}", "auto_analyze": []}


# ══════════════════════════════════════════════════════════════════
# 智能诊断中心 · 深度巡检子进程隔离
# ══════════════════════════════════════════════════════════════════
# 【问题】智能诊断中心「深度巡检分析专员」调用 run_target_inspection 实时跑巡检引擎，
#        对于 HGDB / DB2 / SQL Server(JDBC) / oracle_jdbc 这类依赖 JPype 在**当前进程内**
#        启动 JVM 的数据源，JVM 原生线程会把 gevent 协作式服务器的 hub 钉死，整个 Web
#        界面冻结；前端表现为「深度巡检分析专员」一直卡在「工作中」。
#        这与「测试连接卡死」「开始巡检卡死」是同根因，只是发生在智能诊断中心的另一路径。
# 【解法】把 JVM 类型巡检彻底赶出主进程：
#   - 主进程只负责 spawn 子进程（同一个 exe 加 --intelligence-inspection-cli）
#     + 协作式轮询读取 stdout 结果行；
#   - 等待期间用 gevent.sleep 让出执行权 → 界面全程可用；
#   - 超时直接杀子进程树，JVM 随之消失，主进程不受任何残留影响。
INTEL_JVM_DB_TYPES = ('hgdb', 'db2', 'sqlserver_jdbc', 'oracle_jdbc', 'dm', 'gbase', 'clickhouse', 'uxdb')
INTEL_INSPECTION_TIMEOUT = 1800  # 智能诊断深度巡检整体硬超时（秒）
_INTEL_RESULT_PREFIX = "__DBCHECK_INTEL_INSP_RESULT__"


def _intel_cooperative_sleep(seconds):
    """让出执行权且不冻结 gevent hub。

    与 web/app.py 的 _cooperative_sleep 同义：gevent 模式下用 gevent.sleep
    切回 hub 处理其它请求；其余模式退化为 time.sleep。本进程未 monkey-patch，
    time.sleep 会真实阻塞 hub，故该分支判断不能省。
    """
    # 复用 app.py 的协作式睡眠（若已加载），避免两处实现漂移
    try:
        from modules.web.app import _cooperative_sleep as _app_sleep
        _app_sleep(seconds)
        return
    except Exception:
        pass
    try:
        import gevent as _gv
        _gv.sleep(seconds)
        return
    except Exception:
        pass
    time.sleep(seconds)


def _intel_kill_process_tree(proc):
    """强杀子进程及其派生进程（JVM 可能另起子进程）。"""
    import subprocess as _sp
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


def _intel_cli_command():
    """返回启动智能诊断巡检隔离 CLI 的命令行。

    冻结态：``<dbcheck.exe> --intelligence-inspection-cli``（web_ui.py 在单实例锁之前拦截）；
    开发态：``<python> modules/intelligence/intel_inspection_cli.py``。
    """
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--intelligence-inspection-cli']
    return [sys.executable,
            os.path.join(str(paths.PROJECT_ROOT), 'modules', 'intelligence',
                         'intel_inspection_cli.py')]


def run_target_inspection(
    db_type: str,
    instance: Dict[str, Any],
    inspector_name: str = "Jack",
    template_id=None,
) -> Dict[str, Any]:
    """为目标数据源实时运行巡检引擎，返回结构化结果。

    对外接口（被 inspection_expert / mcp_server 调用）。JVM 类型（hgdb / db2 /
    sqlserver_jdbc / oracle_jdbc）在**未处于已隔离子进程**时，自动委派到干净子进程
    执行，避免 JPype JVM 把 gevent 主进程 hub 钉死导致界面冻结。已隔离场景下
    （由 intel_inspection_cli 调用，env 标记 ``DBCheck_INTEL_INSP_SUBPROCESS=1``）
    直接走进程内版本，不递归再起子进程。

    非 JVM 类型（mysql / pg / dm / 各类原生驱动插件等）保持原进程内行为不变。
    """
    if _SCRIPT_DIR not in sys.path:
        sys.path.insert(0, _SCRIPT_DIR)

    # JVM 类型且当前不在隔离子进程内 → 委派子进程
    if (db_type in INTEL_JVM_DB_TYPES
            and os.environ.get('DBCheck_INTEL_INSP_SUBPROCESS') != '1'):
        return _run_target_inspection_subprocess(
            db_type, instance, inspector_name, template_id)

    return _run_target_inspection_inline(db_type, instance, inspector_name, template_id)


def _run_target_inspection_subprocess(
    db_type: str,
    instance: Dict[str, Any],
    inspector_name: str = "Jack",
    template_id=None,
    timeout: int = INTEL_INSPECTION_TIMEOUT,
) -> Dict[str, Any]:
    """在独立子进程中执行 JVM 类型的深度巡检，返回与内联版本同构的结果字典。

    主进程只负责 spawn 子进程、协作式轮询 stdout 结果行；等待期间让出 gevent hub，
    界面始终可响应。JVM 只活在子进程里，超时可直接杀进程树。
    """
    import subprocess as _sp
    import tempfile as _tf

    payload = {
        'db_type': db_type,
        'instance': instance,
        'inspector_name': inspector_name,
        'template_id': template_id,
    }

    _in_fd, _in_path = _tf.mkstemp(prefix='dbc_intel_in_', suffix='.json')
    _out_fd, _out_path = _tf.mkstemp(prefix='dbc_intel_out_', suffix='.log')
    os.close(_out_fd)
    proc = None
    try:
        with os.fdopen(_in_fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=True)

        env = os.environ.copy()
        env['DBCheck_NO_GEVENT_PATCH'] = '1'     # 子进程绝不能被 monkey-patch
        env['DBCheck_INTEL_INSP_SUBPROCESS'] = '1'  # 防止子进程内部递归委派
        env['PYTHONIOENCODING'] = 'utf-8'

        _kw = {}
        if os.name == 'nt':
            _kw['creationflags'] = (getattr(_sp, 'CREATE_NO_WINDOW', 0x08000000)
                                    | getattr(_sp, 'CREATE_NEW_PROCESS_GROUP', 0x00000200))
        else:
            _kw['start_new_session'] = True

        with open(_in_path, 'r', encoding='utf-8') as fin, \
                open(_out_path, 'w', encoding='utf-8', errors='replace') as fout:
            proc = _sp.Popen(_intel_cli_command(), stdin=fin, stdout=fout,
                             stderr=_sp.STDOUT, env=env,
                             cwd=str(paths.PROJECT_ROOT), **_kw)

        deadline = time.monotonic() + timeout
        while True:
            if proc.poll() is not None:
                break
            if time.monotonic() >= deadline:
                _intel_kill_process_tree(proc)
                return {
                    "ok": False,
                    "error": (f"实时巡检超时（已超过 {timeout} 秒），请检查目标数据库是否可连接，"
                              f"或 JVM/JDBC 驱动环境是否正常。"),
                    "auto_analyze": [],
                    "db_type": db_type,
                    "instance_name": instance.get("name") or instance.get("host") or "",
                }
            _intel_cooperative_sleep(0.1)

        # 子进程已退出，输出文件此时已完整，读取并提取结果行
        try:
            with open(_out_path, 'r', encoding='utf-8', errors='replace') as f:
                out = f.read()
        except Exception:
            out = ''

        result = None
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith(_INTEL_RESULT_PREFIX):
                try:
                    result = json.loads(line[len(_INTEL_RESULT_PREFIX):])
                except Exception:
                    result = None
                break

        if result is None:
            tail = (out or '').strip().splitlines()[-8:]
            return {
                "ok": False,
                "error": ("实时巡检子进程未返回有效结果"
                          + (f"：{' | '.join(tail)}" if tail
                             else f"（退出码 {proc.returncode}）")),
                "auto_analyze": [],
                "db_type": db_type,
                "instance_name": instance.get("name") or instance.get("host") or "",
            }
        return result
    except Exception as e:  # noqa: BLE001
        if proc is not None and proc.poll() is None:
            _intel_kill_process_tree(proc)
        return {
            "ok": False,
            "error": f"实时巡检子进程启动失败：{e}",
            "auto_analyze": [],
            "db_type": db_type,
            "instance_name": instance.get("name") or instance.get("host") or "",
        }
    finally:
        for _p in (_in_path, _out_path):
            try:
                os.remove(_p)
            except Exception:
                pass
