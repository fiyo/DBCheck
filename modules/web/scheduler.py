# coding: utf-8
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
DBCheck 定时调度模块
===================
基于 apscheduler 的后台定时任务调度，支持：
- Cron 表达式配置（秒/分/时/日/月/周）
- 持久化任务配置到 JSON 文件
- 巡检完成后触发邮件/钉钉/企业微信通知
"""
from modules.core.paths import PROJECT_ROOT
import os, sys, json, datetime, threading, logging, time, signal
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore

SCRIPT_DIR = str(PROJECT_ROOT)
from modules.core import paths
from pathlib import Path
paths.ensure_migrated()

CONFIG_FILE = str(paths.SCHEDULER_JOBS)
LOG_FILE = str(paths.SCHEDULER_LOG)

# ── 日志配置 ────────────────────────────────────────────────
logger = logging.getLogger('scheduler')
logger.setLevel(logging.INFO)
log_path = Path(LOG_FILE)
log_path.parent.mkdir(parents=True, exist_ok=True)
_handler = logging.FileHandler(LOG_FILE, encoding='utf-8', mode='a')
_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
logger.addHandler(_handler)


def _normalize_cron_expression(expr):
    """将常见 cron 变体规范化为 APScheduler 的 CronTrigger.from_crontab 接受的
    5 字段标准表达式（分 时 日 月 周）。仅做语法层面的容错转换，不改变字段语义：
      - '?' 视作 '*'（Quartz 中 ? 等价于 * 在 day-of-month / day-of-week 位）
      - 6 字段：去掉首段（秒），保留后 5 段（分 时 日 月 周）—— 兼容 Quartz/Spring
      - 7 字段：去掉首段（秒）与末段（年），保留中段 5 —— 兼容含年的写法
      - 其余字段数原样返回，交由 from_crontab 校验并报错
    返回规范化后的表达式字符串。
    """
    if not expr:
        return expr
    e = expr.strip().replace('?', '*')
    parts = e.split()
    if len(parts) == 6:
        parts = parts[1:]          # 去掉秒字段
    elif len(parts) == 7:
        parts = parts[1:-1]        # 去掉秒字段与年字段
    return ' '.join(parts)


# ── 并发巡检排除机制 ────────────────────────────────────────
# 多个定时任务可能在同一次触发中并发执行（例如同一分钟命中多个 cron）。
# 巡检过程涉及数据库连接、报告渲染（docx/docxtpl/openpyxl）、importlib 动态
# 加载入口模块等共享资源，并发时可能产生竞态甚至死锁，表现为“只打印开始、
# 既不完成也不失败、也不生成报告”的黑洞。
#
# 这里用信号量把同时执行的巡检数限制为 1（严格串行），并配备看门狗超时，
# 确保任何情况下都不会出现静默挂起、无任何结果日志的情况。
# 如需允许 N 个并发，把 _INSPECTION_MAX_CONCURRENT 调大即可。
_INSPECTION_MAX_CONCURRENT = 1        # 同时执行的巡检数上限（1 = 严格串行）
_INSPECTION_ACQUIRE_TIMEOUT = 1800    # 排队等待执行名额的最长时间（秒）
_INSPECTION_WATCHDOG_TIMEOUT = 1800  # 单次巡检执行的最长时限（秒），超时强制释放名额
_inspection_semaphore = threading.Semaphore(_INSPECTION_MAX_CONCURRENT)

_scheduler = None  # 全局调度器实例（延迟初始化）


def _load_jobs():
    """从 JSON 文件加载任务配置"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning('加载任务配置失败: %s', e)
    return []


def _save_jobs(jobs):
    """保存任务配置到 JSON 文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error('保存任务配置失败: %s', e)


def _run_plugin_inspection(db_info, inspector_name, ssh_info):
    """为插件类型（如 hgdb_jdbc）执行定时巡检。

    通过 plugin.json 中的 main_class 实例化 inspector，走
    connect -> collect_data -> generate_report 标准流程。
    """
    db_type = db_info.get('db_type')
    if not db_type:
        raise ValueError('db_type 为空')

    from modules.pluginkit.loader import discover_plugins
    plugins = discover_plugins()
    plugin_meta = None
    for p in plugins:
        if p.get('enabled') and p.get('db_type') == db_type:
            plugin_meta = p
            break

    if not plugin_meta:
        raise ValueError('不支持的数据库类型: %s' % db_type)

    plugin_path = plugin_meta.get('path')
    main_file = plugin_meta.get('main_file', 'main_plugin.py')
    main_class = plugin_meta.get('main_class')
    if not plugin_path or not main_class:
        raise ValueError('插件元数据不完整: %s' % db_type)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'scheduled_plugin_%s' % db_type,
        os.path.join(plugin_path, main_file)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    inspector_cls = getattr(module, main_class, None)
    if not inspector_cls:
        raise ValueError('插件主类未找到: %s.%s' % (db_type, main_class))

    host = db_info.get('host', '')
    port = db_info.get('port', 3306)
    user = db_info.get('user', '')
    password = db_info.get('password', '')
    database = db_info.get('database') or db_info.get('default_database') or ''

    inspector = inspector_cls(
        host, port, user, password,
        database=database,
        ssh_info=ssh_info
    )
    ok, msg = inspector.connect()
    if not ok:
        raise RuntimeError('插件数据库连接失败: %s' % msg)

    inspector.collect_data()

    # Generate output filename consistent with built-in runners
    from datetime import datetime
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_label = ''.join(c if c.isalnum() or c in '-_' else '_' for c in (db_info.get('label') or db_type))
    output_dir = str(paths.REPORTS_DIR)
    os.makedirs(output_dir, exist_ok=True)
    ofile = os.path.join(output_dir, '%s_%s_%s.docx' % (db_type, safe_label, ts))

    report_file = inspector.generate_report(ofile, inspector_name)
    return report_file


def _kill_tree(proc):
    """跨平台杀掉整个进程树（含 JVM 孙进程），避免子进程超时后 JVM 残留。"""
    try:
        pid = proc.pid
    except Exception:
        return
    try:
        if os.name == 'nt':
            os.system('taskkill /F /T /PID %d' % pid)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_plugin_inspection_subprocess(db_info, inspector_name):
    """在干净子进程中执行 JVM 插件类型（hgdb / db2 / sqlserver_jdbc / oracle_jdbc）
    的定时巡检，规避「进程内 JVM 与 gevent hub 死锁导致整个界面卡死」。

    复用 ``modules/jdbc_inspection_cli.py``：该 CLI 在未被 gevent monkey-patch 的
    子进程里调用 ``app.run_inspection_task``，主进程（调度器线程）只负责 spawn、
    读取 stdout 结束行（__DBCHECK_INSP_DONE__）并取回 ``report_path`` 供通知使用。

    本函数假定 ``db_info['db_type']`` 已在调用方确认属于 JVM 类型。
    """
    import subprocess as _sp
    from modules.jdbc_inspection_cli import (
        JVM_INSPECTION_DB_TYPES, RESULT_PREFIX, DONE_PREFIX,
    )

    db_type = db_info.get('db_type')
    if db_type not in JVM_INSPECTION_DB_TYPES:
        # 非 JVM 插件类型走原进程内路径（进程内无上下文可回传）
        return _run_plugin_inspection(db_info, inspector_name, None), None

    # 构造 jdbc_inspection_cli 期望的 db_info 字段（ip/port/user/password/database/name）
    mapped = {
        'ip': db_info.get('host') or db_info.get('ip') or '',
        'port': int(db_info.get('port') or 0),
        'user': db_info.get('user') or '',
        'password': db_info.get('password') or '',
        'database': db_info.get('database') or '',
        'name': db_info.get('label') or db_info.get('name') or db_type or 'unknown',
        'inspector_name': inspector_name,
    }
    for _k in ('connection_mode', 'jdbc_url', 'instance_name',
               'encrypt', 'trust_server_certificate',
               'ssh_host', 'ssh_port', 'ssh_user', 'ssh_password', 'ssh_key_file'):
        if _k in db_info and db_info[_k] is not None:
            mapped[_k] = db_info[_k]

    payload = {
        'task_id': 'sched-%s-%s' % (db_type, datetime.datetime.now().strftime('%Y%m%d%H%M%S')),
        'db_type': db_type,
        'db_info': mapped,
        'inspector_name': inspector_name,
        'template_id': None,
        'chapter_ids': None,
    }
    raw = json.dumps(payload, ensure_ascii=True)

    if getattr(sys, 'frozen', False):
        cmd = [sys.executable, '--jdbc-inspection-cli']
    else:
        cmd = [sys.executable,
               os.path.join(str(PROJECT_ROOT), 'modules', 'jdbc_inspection_cli.py')]

    env = os.environ.copy()
    env['DBCheck_NO_GEVENT_PATCH'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'

    _kw = {}
    if os.name == 'nt':
        _kw['creationflags'] = (getattr(_sp, 'CREATE_NO_WINDOW', 0x08000000)
                                | getattr(_sp, 'CREATE_NEW_PROCESS_GROUP', 0x00000200))
    else:
        _kw['start_new_session'] = True

    _timeout = 1500  # 25 分钟硬上限，远长于正常巡检与看门狗时限
    proc = None
    final = None
    try:
        proc = _sp.Popen(cmd, stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                         env=env, cwd=str(PROJECT_ROOT), **_kw)
        try:
            proc.stdin.write(raw.encode('utf-8'))
            proc.stdin.close()
        except Exception:
            pass

        # 逐行读取 stdout，直到结束行（__DBCHECK_INSP_DONE__）或进程退出
        try:
            for _line in proc.stdout:
                _s = _line.decode('utf-8', errors='replace') if isinstance(_line, bytes) else _line
                _s = _s.strip()
                if _s.startswith(DONE_PREFIX):
                    try:
                        final = json.loads(_s[len(DONE_PREFIX):])
                    except Exception:
                        final = None
                    break
        except Exception:
            pass

        _deadline = time.monotonic() + _timeout
        _rc = None
        try:
            _rc = proc.wait(timeout=max(1, _deadline - time.monotonic()))
        except Exception:
            _rc = None
        if _rc is None:
            _kill_tree(proc)
            raise RuntimeError('插件巡检子进程超时（>%ds），已终止' % _timeout)
    finally:
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)

    if final is None:
        _rc = proc.returncode if proc is not None else '????'
        raise RuntimeError('插件巡检子进程未返回结束行（返回码 %s）' % _rc)

    if final.get('status') != 'done':
        raise RuntimeError(final.get('error_msg') or '插件巡检执行失败')

    # 同时回传子进程的巡检结果（含统一问题清单 auto_analyze）。
    # 历史 bug：此处曾只返回 report_path，把巡检上下文丢弃，导致定时通知统计不到
    # 问题数（issue_count=None → 消息中「问题数」缺失或显示为 0），而 docx 报告与
    # 「巡检历史 → 问题列表」都显示真实问题数，三处口径不一致。
    # 子进程内的 app.run_inspection_task 已按 collect_issues 口径完成统一汇总
    # （task['auto_analyze']），主进程直接复用即可保证四处完全一致。
    return final.get('report_path'), final


def _on_watchdog_timeout(job_id, db_info, state, guard):
    """看门狗触发：单次巡检超过时限，强制释放执行名额并记录超时失败。

    注意：线程无法被强制杀死，这里只负责把信号量名额释放出来，
    让排队的其它巡检能够继续；当前卡住的线程会在其阻塞调用返回后自然结束
    （daemon 线程，不会阻碍进程退出）。
    """
    db_type = db_info.get('db_type', 'unknown')
    with guard:
        if state['released']:
            return
        state['released'] = True
    _inspection_semaphore.release()
    logger.error('[%s] 巡检超时（超过 %ds，排除机制看门狗已强制释放执行名额）: %s',
                 job_id, _INSPECTION_WATCHDOG_TIMEOUT, db_type)


def _run_inspection(job_id, db_info, inspector_name, notify_on_done):
    """
    并发巡检排除机制入口（在独立线程中运行）。

    通过信号量把同时执行的巡检数限制为 _INSPECTION_MAX_CONCURRENT（默认 1，
    即严格串行），避免多个定时任务同分钟并发触发时因共享资源竞态/死锁而
    出现“只打印开始、既不完成也不失败、也不生成报告”的黑洞。

    若等待执行名额超过 _INSPECTION_ACQUIRE_TIMEOUT，则跳过本次巡检（不堆积）；
    若单次巡检执行超过 _INSPECTION_WATCHDOG_TIMEOUT，看门狗会强制释放名额并
    记录超时失败，保证任何情况下都有明确的结果日志，而不是静默挂起。
    """
    acquired = _inspection_semaphore.acquire(timeout=_INSPECTION_ACQUIRE_TIMEOUT)
    if not acquired:
        logger.warning(
            '[%s] 巡检被跳过：并发排除机制，等待执行名额超时（%ds）',
            job_id, _INSPECTION_ACQUIRE_TIMEOUT,
        )
        return

    guard = threading.Lock()
    state = {'released': False}

    def _release():
        with guard:
            if not state['released']:
                state['released'] = True
                _inspection_semaphore.release()

    timer = threading.Timer(
        _INSPECTION_WATCHDOG_TIMEOUT,
        _on_watchdog_timeout,
        args=(job_id, db_info, state, guard),
    )
    timer.start()
    try:
        _run_inspection_core(job_id, db_info, inspector_name, notify_on_done)
    finally:
        timer.cancel()
        _release()


def _run_inspection_core(job_id, db_info, inspector_name, notify_on_done):
    """
    执行巡检并发送通知（在独立线程中运行）

    参数:
        job_id:      任务ID（用于日志）
        db_info:     数据库连接信息字典
        inspector_name: 巡检人员
        notify_on_done: 是否在完成后发送通知
    """
    from modules.inspection.run import (
        run_mysql, run_mariadb, run_oceanbase, run_pg,
        run_oracle_full, run_dm, run_sqlserver, run_tidb,
        run_ivorysql, run_yashandb, run_gbase, run_kingbase
    )
    from modules.notify import EmailNotifier, WebhookNotifier

    # 如果指定了 datasource_id，从 Pro 模块获取完整连接信息（解密密码）
    if db_info.get('datasource_id'):
        try:
            from modules.pro import get_instance_manager
            im = get_instance_manager()
            ds = im.get_instance_decrypted(db_info['datasource_id'])
            if ds:
                db_info = ds.copy()
                db_info['label'] = db_info.get('name', db_info.get('host', ''))
            else:
                raise ValueError('数据源不存在: ' + db_info['datasource_id'])
        except ImportError:
            raise ValueError('Pro 模块未安装，无法使用数据源')
        except Exception as e:
            raise ValueError('获取数据源失败: ' + str(e))

    db_type = db_info.get('db_type', 'mysql')
    logger.info('[%s] 定时巡检开始: %s %s:%s', job_id, db_type,
                db_info.get('host'), db_info.get('port'))
    logger.info(f'巡检后是否发送通知：{notify_on_done}')

    report_file = None
    error_msg = None
    # 巡检结果上下文（内置巡检器返回的第三项），用于统计问题数、生成分享报告
    inspection_ctx = None
    # 统一问题清单（已按 collect_issues 口径汇总）。子进程路径在巡检进程内已完成
    # 统一口径（与 docx 报告/巡检历史同源），主进程直接复用，避免用「精简上下文」
    # 二次推导造成新的数量漂移；None 表示未取得，通知将不展示问题数而非误报 0。
    notification_issues = None

    try:
        # SSH 信息（如果有）
        ssh_info = None
        if db_info.get('ssh_host'):
            ssh_info = {
                'ssh_host':     db_info.get('ssh_host'),
                'ssh_port':     int(db_info.get('ssh_port', 22)),
                'ssh_user':     db_info.get('ssh_user', 'root'),
                'ssh_password': db_info.get('ssh_password', ''),
                'ssh_key_file': db_info.get('ssh_key_file', ''),
                'ssh_key_password': db_info.get('ssh_key_password', ''),
            }

        # Build runner map for built-in db_types (oracle_full -> oracle_full, oracle -> oracle_full)
        runner_map = {
            'mysql': run_mysql,
            'mariadb': run_mariadb,
            'oceanbase': run_oceanbase,
            'pg': run_pg,
            'oracle': run_oracle_full,
            'oracle_full': run_oracle_full,
            'dm': run_dm,
            'sqlserver': run_sqlserver,
            'tidb': run_tidb,
            'ivorysql': run_ivorysql,
            'yashandb': run_yashandb,
            'gbase': run_gbase,
            'kingbase': run_kingbase,
        }

        # 分派优先级：JVM/JDBC 类型一律子进程隔离（最高优先级）。
        # 背景：内置类型引擎 connect() 已统一走 JDBC（jaydebeapi/JVM），若在主进程
        # （调度器线程，与 gevent hub 同进程）内启动 JVM，会与 hub 死锁 → 定时巡检
        # 触发即整个界面卡死/HTTP 无响应（2026-08-20 实测：23:45 定时 ivorysql 巡检
        # 触发后服务僵死）。与插件类型（hgdb/db2）同根因，统一走
        # jdbc_inspection_cli 干净子进程，主进程只 spawn + 读 report_path。
        from modules.jdbc_inspection_cli import JVM_INSPECTION_DB_TYPES
        if db_type in JVM_INSPECTION_DB_TYPES:
            _sub_ret = _run_plugin_inspection_subprocess(db_info, inspector_name)
            if isinstance(_sub_ret, (tuple, list)):
                report_file = _sub_ret[0] if _sub_ret else None
                if len(_sub_ret) > 1:
                    inspection_ctx = _sub_ret[1]
            else:
                report_file = _sub_ret
            # 子进程已回传统一问题清单 auto_analyze（与巡检历史/报告同源），
            # 直接作为通知的问题清单，确保「通知 == 巡检历史 == docx 报告」。
            if isinstance(inspection_ctx, dict):
                # result 中携带健康评分/风险等级/问题列表等展示字段，提到顶层，
                # 供 _create_report_share 复用（其按 context 顶层字段取值）。
                _res = inspection_ctx.get('result')
                if isinstance(_res, dict):
                    for _k, _v in _res.items():
                        inspection_ctx.setdefault(_k, _v)
                _aa = inspection_ctx.get('auto_analyze')
                if isinstance(_aa, list):
                    notification_issues = [it for it in _aa if isinstance(it, dict)]
        elif db_type in runner_map:
            # 非 JVM 内置类型（oracle=oracledb / sqlserver=pyodbc 原生驱动）主进程安全
            _runner_ret = runner_map[db_type](db_info, inspector_name, ssh_info)
            if isinstance(_runner_ret, (tuple, list)):
                report_file = _runner_ret[0] if _runner_ret else None
                if len(_runner_ret) > 2:
                    inspection_ctx = _runner_ret[2]
            else:
                report_file = _runner_ret
        else:
            # 其余插件类型：JVM 依赖型已在上面拦截，此处仅原生驱动插件
            report_file = _run_plugin_inspection(db_info, inspector_name, ssh_info)

        if not report_file:
            raise RuntimeError('Word 报告渲染失败')

        logger.info('[%s] 巡检完成: %s', job_id, report_file)

        # 发送通知
        if notify_on_done:
            _send_notifications(job_id, db_info, report_file, error=None,
                                context=inspection_ctx, issues=notification_issues)

    except Exception as e:
        error_msg = str(e)
        logger.error('[%s] 巡检失败: %s', job_id, error_msg)
        # 即使失败也发送通知（告警）
        if notify_on_done:
            _send_notifications(job_id, db_info, report_file, error=error_msg,
                                context=inspection_ctx, issues=notification_issues)


def _collect_issues(db_type, context, issues=None):
    """从巡检结果中汇总问题列表（统计口径与 Web UI「智能分析」一致）。

    实现统一收敛到 modules.inspection.analyzer.collect_issues，避免定时通知
    与「数据库巡检历史 → 问题列表」两处口径漂移。
    返回元素为 dict 的问题列表。

    issues 不为 None 时，表示调用方（子进程路径）已经拿到与报告/历史同源的
    统一问题清单，此时直接复用，不再二次推导，避免口径再次漂移。
    """
    if issues is not None:
        return [it for it in issues if isinstance(it, dict)]

    if not isinstance(context, dict):
        return []

    try:
        from modules.inspection.analyzer import collect_issues
        return collect_issues(db_type, context)
    except Exception as e:
        logger.warning('智能分析统计问题数失败: %s', e)

    # 兜底：至少保留上下文里已有的问题，保证通知不出现“0 个问题”的误报
    items = []
    for key in ('auto_analyze', 'issues'):
        seq = context.get(key)
        if isinstance(seq, (list, tuple)):
            items.extend([it for it in seq if isinstance(it, dict)])
    return items


def _risk_level_from_count(count):
    """按问题数粗略划分风险等级（供分享报告展示）"""
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        return 'low'
    if count <= 5:
        return 'low'
    if count <= 15:
        return 'medium'
    return 'high'


def _report_base_url():
    """确定分享报告链接的基址，优先级：
    1) notification.report_base_url（或 .env 的 REPORT_BASE_URL）
    2) 浏览器最近访问 Web UI 的地址（app.config['REMEMBERED_BASE_URL']）
    3) 自动探测本机出口 IP + 默认端口 5003
    """
    from modules.notify import _load_config, get_report_base_url
    base = ''
    try:
        cfg = _load_config()
        if isinstance(cfg, dict):
            base = str(cfg.get('report_base_url') or '').strip().rstrip('/')
    except Exception:
        base = ''
    if not base:
        try:
            from modules.web.app import app as _app
            base = (_app.config.get('REMEMBERED_BASE_URL') or '').rstrip('/')
        except Exception:
            base = ''
    if not base:
        base = get_report_base_url()
    return base


def _create_report_share(job_id, db_info, report_file, issues, context=None):
    """为定时巡检结果创建只读分享报告，返回可访问的分享链接（失败返回 ''）。

    复用 Web UI 的分享机制（share_type='db_inspect'，页面 /share/<id>）；
    链接基址取 notification.report_base_url，未配置时自动探测本机地址。
    """
    try:
        from modules.notify import build_share_url
        base = _report_base_url()
        if not base:
            logger.warning('[%s] 未配置报告访问基址且无法自动探测，通知中将不包含报告链接', job_id)
            return ''

        label = db_info.get('label', db_info.get('host', '巡检'))
        db_type = db_info.get('db_type', '')
        issue_count = len(issues or [])

        result = {
            'db_type': db_type,
            'host': db_info.get('host', ''),
            'instance_name': label,
            'risk_count': issue_count,
            'risk_level': _risk_level_from_count(issue_count),
            'issues': [
                {
                    'level': it.get('level', '') or it.get('severity', ''),
                    'description': it.get('description') or it.get('title') or it.get('col1', ''),
                    'suggestion': it.get('suggestion') or it.get('advice', ''),
                }
                for it in (issues or [])
            ],
            'filename': os.path.basename(report_file) if report_file else '',
            'finished_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        if isinstance(context, dict):
            for k in ('health_score', 'risk_level', 'hostname', 'summary'):
                if context.get(k) is not None:
                    result[k] = context.get(k)

        from modules.server.inspect import create_share
        share_id = create_share('db_inspect', 'DBCheck 巡检报告 - %s' % label, {'result': result})
        url = build_share_url(base, share_id)
        logger.info('[%s] 已生成分享报告链接: %s', job_id, url)
        return url
    except Exception as e:
        logger.warning('[%s] 生成分享报告链接失败: %s', job_id, e)
        return ''


def _send_notifications(job_id, db_info, report_file, error=None, context=None,
                        issues=None):
    """发送邮件和 Webhook 通知"""
    from modules.notify import EmailNotifier, WebhookNotifier, _load_config

    label = db_info.get('label', db_info.get('host', '未知'))
    db_type = db_info.get('db_type', 'unknown')
    status = '失败' if error else '完成'

    # 统一从 dbc_config.json 的 notification 节点加载配置（与 Web UI 保存/测试同一来源）。
    # 历史 bug：此处曾直接读取旧文件 notifier_config.json，而配置实际保存在
    # dbc_config.json 中，导致定时巡检的邮件/Webhook 通知永远读不到配置、静默不发。
    cfg = _load_config()

    # 统计问题数并生成分享报告链接（失败不影响通知发送）
    # issues 显式传入（子进程路径）时直接复用统一清单，数量必然与报告/历史一致；
    # 否则按上下文即时汇总。两者都拿不到时才置 None，通知不展示问题数，
    # 而不是误报为 0。
    _explicit_issues = issues is not None
    issues = _collect_issues(db_type, context, issues)
    if _explicit_issues or isinstance(context, dict):
        issue_count = len(issues)
    else:
        issue_count = None
    # 仅在有实际报告文件且非失败时生成分享链接（失败告警无报告可看）
    report_url = _create_report_share(job_id, db_info, report_file, issues, context) \
        if (report_file and not error) else ''
    logger.info('[%s] 本次巡检问题数: %s', job_id, issue_count if issue_count is not None else '未知')

    # 发送邮件通知（只要配置了收件人就发送，不强制要求 enabled 字段）
    email_cfg = cfg.get('email', {})
    if email_cfg.get('recipients') and not error:
        try:
            notifier = EmailNotifier(cfg['email'])
            notifier.send_report(label, db_type, report_file,
                                 issue_count=issue_count, report_url=report_url)
            logger.info('[%s] 邮件通知已发送', job_id)
        except Exception as e:
            logger.error('[%s] 邮件发送失败: %s', job_id, e)

    # 发送 Webhook 告警（成功/失败均发送；只要配置了 URL 就发送，
    # 仅当显式设置 enabled=false 时才禁用，与邮件的判定语义保持一致）
    webhook_cfg = cfg.get('webhook', {})
    webhook_enabled = webhook_cfg.get('enabled', True) is not False
    if webhook_cfg.get('url') and webhook_enabled:
        try:
            notifier = WebhookNotifier(webhook_cfg)
            ok = notifier.send_alert(
                label=label,
                db_type=db_type,
                status=status,
                error=error,
                report_file=report_file,
                issue_count=issue_count,
                report_url=report_url
            )
            if ok:
                logger.info('[%s] Webhook 通知已发送（状态: %s）', job_id, status)
            else:
                logger.error('[%s] Webhook 通知发送失败（状态: %s），请检查代理/证书/URL 配置',
                             job_id, status)
        except Exception as e:
            logger.error('[%s] Webhook 发送失败: %s', job_id, e)


class SchedulerManager:
    """定时调度管理器（单例）"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        # APScheduler 配置
        jobstores = {'default': MemoryJobStore()}
        job_defaults = {
            'coalesce': True,       # 合并错过的执行
            'max_instances': 1,      # 同一任务最多一个实例
            'misfire_grace_time': 300  # 5分钟内可补执行
        }
        self.scheduler = BackgroundScheduler(
            jobstores=jobstores,
            job_defaults=job_defaults,
            timezone=datetime.timezone(datetime.timedelta(hours=8))  # 北京时间
        )
        self.scheduler.start()
        logger.info('调度器启动成功')
        
        # 恢复持久化的任务
        self._restore_jobs()
    
    def _restore_jobs(self):
        """从配置文件恢复任务"""
        jobs = _load_jobs()
        for job_cfg in jobs:
            if job_cfg.get('enabled', True):
                self.add_job(job_cfg, restore=True)
    
    def _job_func(self, job_id, db_info, inspector_name, notify_on_done):
        """任务执行函数（包装器）"""
        # 在独立线程中运行，避免阻塞调度器
        t = threading.Thread(
            target=_run_inspection,
            args=(job_id, db_info, inspector_name, notify_on_done),
            daemon=True
        )
        t.start()
    
    def add_job(self, config, restore=False):
        """
        添加定时任务
        
        参数:
            config: 任务配置字典，包含:
                - id: str, 任务ID
                - name: str, 任务名称
                - db_type: str, 数据库类型
                - db_info: dict, 数据库连接信息
                - cron: dict, Cron 配置 {second, minute, hour, day, month, day_of_week}
                - enabled: bool, 是否启用
                - inspector_name: str, 巡检人员
                - notify_on_done: bool, 完成后是否发送通知
        
        返回:
            bool: 是否添加成功
        """
        job_id = config.get('id')
        if not job_id:
            return False

        # 任务是否启用：禁用任务仅持久化配置，不加入调度器
        enabled = config.get('enabled', True)

        # 构建 CronTrigger
        cron = config.get('cron', {})
        expr = cron.get('expression')
        trigger_kwargs = {}
        if not expr:
            # 非自定义表达式：从结构化字段（秒/分/时/日/月/周）构建；
            # 若全部为空则视为无效配置，提前返回。
            for unit in ('second', 'minute', 'hour', 'day', 'month', 'day_of_week'):
                if unit in cron and cron[unit] not in (None, '*'):
                    trigger_kwargs[unit] = cron[unit]
            if not trigger_kwargs:
                logger.warning('任务 %s 没有有效的 cron 配置', job_id)
                return False

        try:
            if expr:
                # 自定义 cron：支持标准 5 字段（分 时 日 月 周），并兼容 Quartz/Spring
                # 的 6 字段（秒 分 时 日 月 周，含 '?'）与 7 字段（含年）。先规范化再交给
                # APScheduler 校验；仍非法则抛异常，被下方 except 捕获并返回 400。
                trigger = CronTrigger.from_crontab(_normalize_cron_expression(expr))
            else:
                trigger = CronTrigger(**trigger_kwargs)

            # 校验通过后再移除同 ID 的旧任务，避免非法配置挤掉已有任务
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            if enabled:
                self.scheduler.add_job(
                    func=self._job_func,
                    trigger=trigger,
                    job_id=job_id,
                    args=[job_id, config['db_info'], config.get('inspector_name', 'DBCheck'),
                          config.get('notify_on_done', True)],
                    name=config.get('name', job_id),
                    replace_existing=True
                )
                logger.info('添加定时任务: %s (%s)', job_id, config.get('name', ''))
            else:
                logger.info('定时任务 %s 已禁用，仅保存配置', job_id)
            
            # 持久化（非恢复模式才保存）
            if not restore:
                jobs = _load_jobs()
                # 替换或追加
                existing_idx = next((i for i, j in enumerate(jobs) if j['id'] == job_id), -1)
                if existing_idx >= 0:
                    jobs[existing_idx] = config
                else:
                    jobs.append(config)
                _save_jobs(jobs)
            
            return True
        except Exception as e:
            logger.error('添加任务失败 %s: %s', job_id, e)
            return False
    
    def update_job(self, job_id, config):
        """
        更新已存在的定时任务配置

        与 add_job 的差异：保留任务原有的启用/禁用状态；当前端未重新输入密码
        （留空或返回掩码 ***）时沿用旧密码，避免把真实密码覆盖为空值。

        参数:
            job_id: 任务ID
            config: 新的任务配置（结构同 add_job）

        返回:
            bool: 是否更新成功（任务不存在或 cron 非法时返回 False）
        """
        jobs = _load_jobs()
        old = next((j for j in jobs if j.get('id') == job_id), None)
        if old is None:
            return False

        cfg = dict(config)
        cfg['id'] = job_id
        # 编辑任务不应改变其启用/禁用状态，继承原值
        cfg['enabled'] = old.get('enabled', True)

        # 密码未修改时沿用旧值（数据源模式无 password 字段，不受影响）
        old_db = old.get('db_info', {}) or {}
        new_db = dict(cfg.get('db_info', {}) or {})
        new_pwd = new_db.get('password')
        if (not new_pwd or new_pwd == '***') and old_db.get('password'):
            new_db['password'] = old_db['password']
        cfg['db_info'] = new_db

        return self.add_job(cfg)

    def remove_job(self, job_id):
        """
        移除定时任务
        
        返回:
            bool: 是否移除成功
        """
        try:
            # 先尝试从调度器移除（任务可能已过期或被禁用）
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info('从调度器移除任务: %s', job_id)
            
            # 无论调度器里有没有，都从 JSON 里删除（避免遗留）
            jobs = _load_jobs()
            original_count = len(jobs)
            jobs = [j for j in jobs if j['id'] != job_id]
            if len(jobs) < original_count:
                _save_jobs(jobs)
                logger.info('从 JSON 删除任务: %s', job_id)
                return True
            return False
        except Exception as e:
            logger.error('移除任务失败 %s: %s', job_id, e)
            return False
    
    def list_jobs(self):
        """
        列出所有定时任务

        返回:
            list: 任务配置列表（包含调度器中的运行状态）
        """
        jobs = _load_jobs()
        scheduled_ids = {job.id for job in self.scheduler.get_jobs()}

        result = []
        for job_cfg in jobs:
            job_cfg = dict(job_cfg)  # 拷贝
            job_cfg['running'] = job_cfg['id'] in scheduled_ids
            # 隐藏敏感信息
            if 'password' in job_cfg.get('db_info', {}):
                job_cfg['db_info'] = dict(job_cfg['db_info'])
                job_cfg['db_info']['password'] = '***'
            # 如果使用了数据源，获取数据源名称用于显示
            if job_cfg.get('db_info', {}).get('datasource_id'):
                ds_id = job_cfg['db_info']['datasource_id']
                try:
                    from modules.pro import get_instance_manager
                    im = get_instance_manager()
                    ds = im.get_instance(ds_id, mask_password=False)
                    if ds:
                        job_cfg['db_info']['host'] = ds.get('name') or ds.get('host', ds_id)
                        job_cfg['db_info']['db_type'] = ds.get('db_type', '')
                        job_cfg['db_info']['port'] = ds.get('port', '')
                        job_cfg['db_info']['user'] = ds.get('user', '')
                        job_cfg['db_info']['password'] = ''
                except Exception:
                    pass
            result.append(job_cfg)
        return result
    
    def toggle_job(self, job_id, enabled):
        """
        启用/禁用任务
        
        参数:
            job_id: 任务ID
            enabled: True=启用, False=禁用
        """
        jobs = _load_jobs()
        for job_cfg in jobs:
            if job_cfg['id'] == job_id:
                job_cfg['enabled'] = enabled
                _save_jobs(jobs)
                if enabled:
                    self.add_job(job_cfg)
                else:
                    if self.scheduler.get_job(job_id):
                        self.scheduler.remove_job(job_id)
                return True
        return False
    
    def run_job_now(self, job_id):
        """
        立即执行一次任务（手动触发）
        
        返回:
            bool: 是否触发成功
        """
        jobs = _load_jobs()
        for job_cfg in jobs:
            if job_cfg['id'] == job_id:
                self._job_func(
                    job_id,
                    job_cfg['db_info'],
                    job_cfg.get('inspector_name', 'DBCheck'),
                    job_cfg.get('notify_on_done', True)
                )
                return True
        return False
    
    def shutdown(self):
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info('调度器已关闭')


# ── 全局调度器实例（延迟初始化）──────────────────────────────
_scheduler = None

def get_scheduler():
    """获取全局调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerManager()
    return _scheduler


if __name__ == '__main__':
    # 测试：列出所有任务
    sm = get_scheduler()
    for job in sm.list_jobs():
        print(job)
