# coding: utf-8
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
RaccoonX Web UI - Flask 应用
数据库巡检工具 Web 界面
"""
# gevent monkey patch 必须放在所有 import 之前
# import gevent.monkey
# gevent.monkey.patch_all()

from modules.core.paths import PROJECT_ROOT
import os, sys, platform, threading, datetime, json, uuid, time, re, random, sqlite3, secrets
import signal
import traceback
import io
from pathlib import Path

# ── B 方案：会话/JWT 密钥默认每次启动随机生成（进程级稳定）──────────
# 重启后旧 Flask 会话 cookie 与 JWT 均失效 → 必须重新登录。
# 生产环境可设环境变量 DBCheck_SECRET_KEY / JWT_SECRET 固定密钥
# （多实例/集群部署时必须一致，否则互不相认）。
if not os.environ.get('DBCheck_SECRET_KEY'):
    os.environ['DBCheck_SECRET_KEY'] = secrets.token_hex(32)
if not os.environ.get('JWT_SECRET'):
    os.environ['JWT_SECRET'] = secrets.token_hex(32)

# 全局基础目录（开发模式用 __file__，PyInstaller 打包后用 exe 目录）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = str(PROJECT_ROOT)

# ── 确保项目根目录在 sys.path（支持各种启动方式）──────────────────
_script_dir = BASE_DIR
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# 旧路径一次性迁移（幂等；仅当检测到遗留旧路径时触发，异常降级不阻断启动）
from modules.core import paths
# 巡检配置库统一走中央路径（paths 为纯路径模块，无循环导入风险）。
# 切勿用 BASE_DIR 拼 data/inspection.db：frozen 下 BASE_DIR=<exe>，
# 而真实运行时数据目录是 <exe>/_internal/data。
from modules.core.paths import INSPECTION_DB
paths.ensure_migrated()

from flask import Flask, request, jsonify, render_template, Response, send_file, make_response, session
from modules.config.version import __version__, EDITION
from modules.core.brand import PRODUCT_NAME_EN, PRODUCT_NAME_ZH, PRODUCT_FULL_NAME, PRODUCT_TAGLINE_EN
from flask_socketio import SocketIO, emit, join_room, leave_room
import socket
from i18n import t as _t


# ── 启动 Banner（与 CLI 保持一致：仅图案 + 版权信息，不含菜单）──────
# Web 端不需要 CLI 的菜单行，因此只复用 cli.print_banner() 的 ASCII 图案部分，
# 图案下方补充版本 / 版权 / 许可证信息。
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"


def _enable_ansi() -> None:
    """Windows 旧终端开启 ANSI 颜色支持（失败静默降级为无色输出）。"""
    try:
        import ctypes
        if os.name == "nt":
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


def _print_startup_banner() -> None:
    """打印 Web UI 启动 Banner：DBCheck ASCII 图案 + 版权信息。

    必须是 main() 内的第一段控制台输出（早于下方插件加载日志），
    与 CLI 先显示 banner 的行为保持一致。
    注：web_ui.py 在 import 阶段会先打印若干初始化日志，
    故 banner 实际出现在这些导入日志之后、main() 业务日志之前。
    GBK 等窄编码控制台下 █ / ╗ 等字符可能无法编码，
    此时回退为 errors='replace' 的安全输出，绝不因此中断启动。
    """
    _enable_ansi()
    banner = f"""
{CYAN}{BOLD}  ██████╗ ██████╗  ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗
  ██╔══██╗██╔══██╗██╔════╝██║  ██║██╔════╝██╔════╝██║  ██╔╝
  ██║  ██║██████╔╝██║     ███████║██║     ██║     █████╔╝
  ██║  ██║██╔══██╗██║     ██╔══██║██║     ██║     ██╔═██╗
  ██████╔╝██████╔╝╚██████╗██║  ██║███████╗╚██████╗██║  ██╗
  ╚═════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝{RESET}
{CYAN}{BOLD}DBCheck {__version__} ({EDITION}){RESET}
{DIM}Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>{RESET}
{DIM}Licensed under Apache-2.0 · https://github.com/fiyo/DBCheck{RESET}
"""
    try:
        print(banner)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or 'ascii'
        print(banner.encode(enc, errors='replace').decode(enc))


# ── GBase 8s JDBC 支持（jaydebeapi 需要 JAVA_HOME）─────────────────
_GBASE_JAVA_CANDIDATES = [
    os.environ.get('JAVA_HOME', ''),
    'C:\\Program Files\\Java\\jdk-11',
    'C:\\Program Files\\Java\\jdk-17',
    'C:\\Program Files\\Java\\jdk-1.8',
    'C:\\Program Files\\Eclipse Adoptium\\jdk-11',
    'C:\\Program Files\\Eclipse Adoptium\\jdk-17',
]
for _j in _GBASE_JAVA_CANDIDATES:
    if _j and os.path.isdir(_j):
        os.environ['JAVA_HOME'] = _j
        _jvm_dir = os.path.join(_j, 'bin', 'server')
        if os.path.isdir(_jvm_dir):
            os.environ['PATH'] = _jvm_dir + os.pathsep + os.environ.get('PATH', '')
        break
# ───────────────────────────────────────────────────────────────────────

# ── 延迟导入调度器和通知器（避免循环依赖）─────────────────────
_scheduler = None

def _get_scheduler():
    global _scheduler
    if _scheduler is None:
        from modules.web.scheduler import get_scheduler
        _scheduler = get_scheduler()
    return _scheduler

# ── i18n key 翻译辅助（供任务函数/API 函数共用）────────────────────
_I18N_MAP = {
    'report.risk_high': '高风险', 'report.risk_mid': '中风险', 'report.risk_low': '低风险',
    'report.risk_suggest': '建议', 'report.risk_suggestion': '建议',
    'report.risk_dba': 'DBA',
    'report.risk_tablespace': '表空间',
    'report.risk_ts_high': '表空间使用率过高', 'report.risk_ts_mid': '表空间使用率偏高',
    'report.risk_invalid_obj': '无效对象', 'report.risk_invalid_desc': '存在无效对象',
    'report.risk_locked': '账户锁定', 'report.risk_locked_desc': '存在锁定账户',
    'report.risk_alert': 'Alert日志错误', 'report.risk_alert_desc': '近7天存在错误日志',
    'report.risk_fix_ts': '查询表空间使用情况',
    'report.risk_fix_alert': '检查Alert日志具体内容',
    'report.risk_fix_locked': '查看锁定账户',
    'report.risk_fix_sql': '修复 SQL',
    'report.severity_high': '高', 'report.severity_mid': '中', 'report.severity_low': '低',
    'report.health_excellent': '优秀', 'report.health_good': '良好',
    'report.health_fair': '一般', 'report.health_attention': '需关注',
}
def _tr(s):
    """翻译 i18n key（如 report.risk_high → 高风险），非 key 原样返回"""
    if not s: return ''
    return _I18N_MAP.get(s, s)

def _parse_report_filename(name: str):
    """从报告文件名提取 db_type, host, label"""
    mapping = [
        ('MySQL巡检报告_', 'mysql'),
        ('TDSQL-C MySQL巡检报告_', 'tdsqlc_mysql'),
        ('PostgreSQL巡检报告_', 'postgresql'),
        ('Oracle巡检报告_', 'oracle'),
        ('DM8巡检报告_', 'dm'),
        ('达梦巡检报告_', 'dm'),
        ('SQLServer巡检报告_', 'sqlserver'),
        ('TiDB巡检报告_', 'tidb'),
        ('IvorySQL巡检报告_', 'ivorysql'),
        ('YashanDB巡检报告_', 'yashandb'),
        ('GBase 8s巡检报告_', 'gbase'),
    ]
    name_no_ext = name.replace('.docx', '')
    for prefix, db_type in mapping:
        if name.startswith(prefix):
            rest = name_no_ext[len(prefix):]  # e.g. "192.168.42.220_ORACLE19_20260517212935"
            # ts 是末尾14位数字
            parts = rest.rsplit('_', 1)
            if len(parts) == 2 and len(parts[1]) == 14 and parts[1].isdigit():
                middle = parts[0]  # "192.168.42.220_ORACLE19"
                sub = middle.split('_', 1)
                host = sub[0]
                label = sub[1] if len(sub) > 1 else ''
                return db_type, host, label
            # 无法解析 ts，至少返回 host
            idx = rest.find('_')
            if idx > 0:
                host = rest[:idx]
                return db_type, host, ''
            return db_type, '', ''
    return '', '', ''


def _sync_delete_trend_for_report(filename: str):
    """删除报告时同步删除对应的趋势数据（若无剩余报告）"""
    try:
        from modules.inspection.analyzer import HistoryManager
        script_dir = BASE_DIR
        hm = HistoryManager(script_dir)
        reports_dir = str(paths.REPORTS_DIR)

        db_type, host, label = _parse_report_filename(filename)
        if not db_type or not host:
            return

        # 查找匹配的实例
        instances = hm.list_instances()
        matched = []
        for inst in instances:
            if inst.get('db_type') == db_type and inst.get('host') == host:
                if not label or inst.get('label') == label:
                    matched.append(inst)
        if not matched:
            return

        # 检查该实例是否还有剩余报告
        for inst in matched:
            inst_key = inst.get('key', '')
            inst_label = inst.get('label', '')
            has_report = False
            if os.path.isdir(reports_dir):
                for f in os.listdir(reports_dir):
                    if f.endswith('.docx') and not f.startswith('~$'):
                        dt, h, lb = _parse_report_filename(f)
                        if dt == db_type and h == host:
                            if not inst_label or lb == inst_label:
                                has_report = True
                                break
            if not has_report:
                hm.delete_instance(inst_key)
    except Exception:
        pass

# async_mode 决定：默认 gevent；未装 gevent 时回退 threading（保证本地也能跑）。
# 允许通过启动参数 --async-mode <mode> 覆盖——开发期指定 threading 可规避 gevent
# 接管 SIGINT 导致的 Ctrl+C 失效（详见 main() 信号处理说明）。
# 注意：socketio 在模块导入时即创建（下方 SocketIO(...)），故参数须在导入期从
# sys.argv 解析，不能等 main() 解析。
_ALLOWED_ASYNC_MODES = ('gevent', 'threading', 'eventlet', 'asgi')


def _resolve_async_mode():
    try:
        import gevent  # noqa: F401
        _gevent_ok = True
    except ImportError:
        _gevent_ok = False
    default = 'gevent' if _gevent_ok else 'threading'

    override = None
    try:
        _args = sys.argv[1:]
        if '--async-mode' in _args:
            _i = _args.index('--async-mode')
            if _i + 1 < len(_args):
                override = _args[_i + 1].strip().lower()
    except Exception:
        override = None

    if not override:
        return default
    if override not in _ALLOWED_ASYNC_MODES:
        print(f"[警告] 未知 --async-mode '{override}'，回退为 {default}")
        return default
    if override == 'gevent' and not _gevent_ok:
        print("[警告] 指定 gevent 但未安装，回退 threading")
        return 'threading'
    return override


_socketio_async_mode = _resolve_async_mode()
socketio = SocketIO(cors_allowed_origins='*', async_mode=_socketio_async_mode)

# ── 强制退出机制（Ctrl+C / 关闭窗口 必杀，独立于 gevent/Python signal） ──
import threading as _shutdown_threading
import time as _shutdown_time

_CTRL_HANDLER_KEEPALIVE = None  # 模块级保活 Win32 回调，防止被 GC 回收后失效

# 活跃子进程表（巡检/测试连接子进程）：主进程 Ctrl+C / os._exit 前必须整树杀掉，
# 否则 JVM 子进程变孤儿继续占用资源/端口（表现为「进程结束不掉/重启端口被占」）。
_ACTIVE_PROCS: set = set()
_ACTIVE_PROCS_LOCK = _shutdown_threading.Lock()


def _hard_exit():
    """C 层立即终止整个进程（含所有后台/非 daemon 线程）。

    等价于关闭终端窗口，干净且不阻塞——这是解决 Ctrl+C 无反应的最终手段。
    """
    os._exit(0)


def _request_shutdown(sig=None, frame=None):
    """请求关闭：先清理活跃子进程（防孤儿 JVM），再立即强制退出。

    清理环节异常绝不允许阻断退出——Ctrl+C 场景下 os._exit(0) 是唯一
    必达目标（子进程残留最坏由系统回收，而进程不退是用户最痛的点）。
    """
    try:
        _kill_active_subprocesses()
    except Exception:  # noqa: BLE001
        pass
    _hard_exit()


def _kill_active_subprocesses():
    """整树杀掉所有活跃子进程（巡检/测试连接 JVM 子进程）。

    主进程走 os._exit(0) 强杀时不会触发任何清理钩子，若巡检子进程
    （jdbc_inspection_cli，含 JVM）还在跑会变孤儿残留。这里在退出前
    显式 kill，避免「Ctrl+C 后进程看似没退干净 / 重启端口被占」。
    """
    if not _ACTIVE_PROCS:
        return
    with _ACTIVE_PROCS_LOCK:
        _procs = list(_ACTIVE_PROCS)
    for _p in _procs:
        try:
            if _p.poll() is None:
                _kill_process_tree(_p)
        except Exception:  # noqa: BLE001
            pass
    # 处理完毕：从注册表移除（含已退出的项，避免残留）
    with _ACTIVE_PROCS_LOCK:
        for _p in _procs:
            _ACTIVE_PROCS.discard(_p)


def _signal_handler(sig, frame):
    """处理 Ctrl+C / Ctrl+Break / SIGTERM：立即强制退出。

    严禁调用任何会 import 模块或 join 线程的清理逻辑（如 unload_all_plugins），
    否则 Ctrl+C 会“看似无效”。这里只做极简强制退出。
    本函数定义在模块级，供主线程周期性重注册使用。
    """
    try:
        print(f"\n\n[主程序] 收到退出信号 {sig}，正在强制退出...")
    except Exception:
        pass
    _request_shutdown()


def _install_python_signals():
    """在主线程注册 Python 标准信号处理器 → os._exit(0)。

    必须在主线程调用（signal.signal 仅主线程可用）。
    gevent/flask-socketio 在运行期可能覆盖 C 层 SIGINT 处理器，
    故由主线程等待循环周期性重注册以持续抢回退出入口。
    """
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
        if platform.system().lower() == 'windows':
            signal.signal(signal.SIGBREAK, _signal_handler)
    except (ValueError, OSError):
        pass


def _console_ctrl_handler(ctrl_type):
    """Windows 控制台 Ctrl+C / Ctrl+Break / 关闭窗口 事件处理器（在独立 OS 线程中运行）。"""
    _request_shutdown()
    return True


def _register_console_ctrl_handler():
    """注册 / 重注册 Windows 控制台级 Ctrl+C 处理器（OS 层，绕开 gevent/Python signal 接管）。

    先移除旧的再添加新的，保证幂等、不产生重复项；
    后注册者 LIFO 顺序下「最先被调用」，从而始终优先于服务自身注册的 handler。
    """
    global _CTRL_HANDLER_KEEPALIVE
    if platform.system().lower() != 'windows':
        return
    try:
        import ctypes
        import ctypes.wintypes
        _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        _CTRL_CB = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.DWORD)
        if _CTRL_HANDLER_KEEPALIVE is not None:
            try:
                _kernel32.SetConsoleCtrlHandler(_CTRL_HANDLER_KEEPALIVE, False)
            except Exception:
                pass
        cb = _CTRL_CB(_console_ctrl_handler)
        _kernel32.SetConsoleCtrlHandler(cb, True)
        _CTRL_HANDLER_KEEPALIVE = cb  # 模块级保活，绝不被 GC
    except Exception:
        pass


def _ctrl_keepalive_loop():
    """持续保活：每 2 秒重注册 Windows 控制台 Ctrl handler，确保本 handler
    在 LIFO 顺序中始终排最前（最先被调用）。这是解决「刚启动能停、运行一阵后
    停不了」的关键——socketio/gevent 在运行期可能再次注册控制台 handler，
    将其挤到我们前面；周期性重注册可保证我们永远抢占最前位置。"""
    while True:
        try:
            _shutdown_time.sleep(2)
            _register_console_ctrl_handler()
        except Exception:
            pass


def _start_ctrl_keepalive():
    """启动控制台 Ctrl handler 持续保活线程（daemon，不阻塞退出）。"""
    try:
        _shutdown_threading.Thread(target=_ctrl_keepalive_loop, daemon=True).start()
    except Exception:
        pass


# ── Git Bash / MSYS2 兜底：stdin 监听（SetConsoleCtrlHandler 在伪终端下无效）──
# 根因：Git Bash(mintty/MSYS2) 里 `python web_ui.py` 启动的是【原生 Windows 进程】，
# 它的 stdin/stdout 是 MSYS2 管道而非 Windows 控制台。于是：
#   - mintty 的 Ctrl+C 走 MSYS2 的模拟信号体系，无法投递给原生子进程；
#   - Windows 的 SetConsoleCtrlHandler 因为根本没有真实控制台事件而永不触发；
#   - 主线程也就收不到 SIGINT，_signal_handler / _console_ctrl_handler 全部落空。
# 兜底做法：直接从 fd 0 读原始字节，收到 ETX(0x03，即 Ctrl+C 的原始字符) 或
# FS(0x1c，Ctrl+\) 即强杀；读到 EOF（终端被关闭 / 管道断开）同样强杀。
_STDIN_WATCHDOG_GRACE_SEC = 3.0  # EOF 宽限期：启动即 EOF 视为非交互式重定向，不退出
_STDIN_KILL_BYTES = (b'\x03', b'\x1c')  # Ctrl+C / Ctrl+\


def _stdin_is_watchable():
    """判断 stdin 是否值得监听（交互式终端或 MSYS2 伪终端）。

    返回 False 的典型场景：stdin 被重定向到文件 / NUL、PyInstaller 无控制台
    窗口模式、stdin 不可用——这些情况下监听毫无意义且可能误杀，必须跳过。
    """
    try:
        stdin = getattr(sys, 'stdin', None)
        if stdin is None:
            return False
        if stdin.fileno() != 0:
            return False
    except Exception:
        return False
    try:
        if stdin.isatty():
            return True
    except Exception:
        pass
    # Git Bash 下原生 python 的 stdin 是管道，isatty() 为 False；
    # 但 MSYSTEM（MINGW64/MINGW32/MSYS）会被子进程继承，可据此判定为伪终端。
    return bool(os.environ.get('MSYSTEM'))


def _stdin_watchdog_loop():
    """阻塞读取 fd 0 的原始字节流，捕获 Ctrl+C 字符或 EOF → 强制退出。

    只读不写，不消费任何业务输入（本程序无 input() 交互），因此不影响功能。
    任何异常都视为"stdin 不可用"，安静地结束本线程，绝不影响服务运行。
    """
    started_at = _shutdown_time.monotonic()
    while True:
        try:
            chunk = os.read(0, 1)
        except Exception:
            return  # stdin 已失效（被关闭/无控制台），放弃监听即可
        if not chunk:
            # EOF：终端窗口被关闭或管道断开 → 强杀，避免残留进程占用 5003 端口。
            # 但启动瞬间就 EOF 说明 stdin 本就是重定向/空设备，此时不能退出。
            if _shutdown_time.monotonic() - started_at < _STDIN_WATCHDOG_GRACE_SEC:
                return
            _request_shutdown()
            return
        if chunk in _STDIN_KILL_BYTES:
            try:
                print("\n\n[主程序] 检测到 Ctrl+C（终端输入），正在强制退出...")
            except Exception:
                pass
            _request_shutdown()
            return


def _start_stdin_watchdog():
    """启动 stdin 退出监听线程（daemon，不阻塞退出）。非交互式环境自动跳过。"""
    if not _stdin_is_watchable():
        return
    try:
        _shutdown_threading.Thread(target=_stdin_watchdog_loop, daemon=True).start()
    except Exception:
        pass


def _signal_rearm_loop():
    """gevent 协程循环：周期性重装 Python 层 SIGINT/SIGTERM 处理器。

    gevent hub 与 flask-socketio 在运行期可能改写 C 层信号处理器，把退出入口
    夺走。本协程运行在【主线程的 hub】上，故 signal.signal() 合法可用；每 2 秒
    抢回一次，保证 Ctrl+C 始终指向 _signal_handler → os._exit(0)。
    """
    while True:
        try:
            gevent.sleep(2)
            _install_python_signals()
        except Exception:
            return


def _start_signal_rearm():
    """在 gevent 模式下派生信号重装协程（hub 启动后自动开始运行）。"""
    if _socketio_async_mode != 'gevent':
        return
    try:
        gevent.spawn(_signal_rearm_loop)
    except Exception:
        pass

# ── 本地模块 ──────────────────────────────────────────────
try:
    import modules.entrypoints.main_mysql as main_mysql
    import modules.entrypoints.main_pg as main_pg
    import modules.entrypoints.main_dm as main_dm
    import modules.entrypoints.main_oracle_full as main_oracle_full
    import modules.entrypoints.main_sqlserver as main_sqlserver
    import modules.entrypoints.main_tidb as main_tidb
    import modules.entrypoints.main_ivorysql as main_ivorysql
    import modules.entrypoints.main_kingbase as main_kingbase
except ImportError:
    main_mysql = main_pg = main_dm = main_oracle_full = main_sqlserver = main_tidb = main_ivorysql = None

# 静态资源已收口到 assets/web/（阶段5：原 web_templates 下的 static/、icons/ 及各 png/ico/js 等）。
# 因 static_url_path='/'，前端所有绝对引用（/static/...、/icons/...、/xxx.png 等）自动映射到 static_folder 根。
app = Flask(__name__, template_folder=str(PROJECT_ROOT / 'web_templates'), static_folder=str(PROJECT_ROOT / 'assets' / 'web'), static_url_path='/')
# 会话密钥取自环境变量 DBCheck_SECRET_KEY（已在启动时为未设置的情况种入进程级随机值）。
# 重启后旧会话 cookie 失效，需重新登录；生产可固定该环境变量保持登录态。
app.config['SECRET_KEY'] = os.environ['DBCheck_SECRET_KEY']
socketio.init_app(app)

# ── 实时监控采集器（v2.10）────────────────────────────────────
# 采集器与 web_ui 同进程运行，复用同一 socketio 实时推流。
_monitor_started = False
try:
    from modules.pro.metrics_collector import MetricsCollector, get_collector, set_collector
    _collector = MetricsCollector(socketio=socketio)
    set_collector(_collector)
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    # signal=False: 不让 APScheduler 注册自己的 SIGINT/SIGTERM 处理器（否则会覆盖本程序的
    #   强制退出入口，且它只优雅停调度、不退出进程，导致 Ctrl+C 无反应）。
    # daemon=True: 允许进程在主线程强杀时不被其后台线程阻塞。
    _monitor_sched = BackgroundScheduler(signal=False, daemon=True)
    _monitor_sched.add_job(_collector.tick, IntervalTrigger(seconds=30))
    _monitor_sched.start()
    _collector.running = True
    _monitor_started = True
    print('[monitor] 实时监控采集器已启动（间隔 30s）', flush=True)
except Exception as _mon_e:
    print('[monitor] 采集器启动失败: %s' % _mon_e, flush=True)


@app.route('/api/pro/metrics/summary')
def api_pro_metrics_summary():
    """所有实例的最新指标快照概览（供前端实时监控区初始化）。"""
    from modules.pro import get_instance_manager
    c = get_collector()
    im = get_instance_manager()
    out = []
    try:
        for inst in im.get_all_instances(mask_password=True):
            iid = inst.get('id') or inst.get('instance_id')
            latest = c.store.get_latest(iid) if c else {}
            skip = ('instance_id', 'name', 'db_type', 'ts', 'available',
                    'latency_ms', 'status', 'error', 'deep_error')
            out.append({
                'instance_id': iid,
                'name': inst.get('name'),
                'db_type': inst.get('db_type'),
                'available': latest.get('available'),
                'latency_ms': latest.get('latency_ms'),
                'status': latest.get('status'),
                'metrics': {k: v for k, v in latest.items() if k not in skip},
                'error': latest.get('error') or latest.get('deep_error'),
                'last_ts': latest.get('ts'),
            })
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)[:200], 'instances': []})
    return jsonify({'ok': True, 'running': bool(c and c.running), 'instances': out})


@app.route('/api/pro/metrics/<instance_id>')
def api_pro_metrics_detail(instance_id):
    """单个实例的近期快照序列（供图表回填历史）。"""
    c = get_collector()
    limit = request.args.get('limit', 120, type=int)
    if not c:
        return jsonify({'ok': False, 'msg': 'collector not started', 'series': []})
    try:
        series = c.store.get_recent(instance_id, limit)
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)[:200], 'series': []})
    return jsonify({'ok': True, 'instance_id': instance_id, 'series': series})


@app.before_request
def _enforce_grayscale():
    import datetime as _dt
    now = _dt.datetime.now()
    g = now.month == 5 and 19 <= now.day <= 21
    from flask import g as _flask_g
    _flask_g._grayscale = g


@app.after_request
def _inject_grayscale(response):
    from flask import g as _flask_g
    if not getattr(_flask_g, '_grayscale', False):
        return response
    if 'text/html' not in response.content_type:
        return response
    body = response.get_data(as_text=True)
    inject_css = '''
<style id="grayscale-enforce">
  body { filter: grayscale(100%) !important; }
  button[onclick*="toggleTheme"] { pointer-events: none !important; opacity: 0.5 !important; }
</style>
'''
    if '</head>' in body:
        body = body.replace('</head>', inject_css + '\n</head>', 1)
    response.set_data(body)
    response.headers['Content-Length'] = len(response.get_data())
    return response

def _verify_agreement_integrity():
    import hashlib, base64, re, os, json, datetime
    # Apache / community 文案哈希集合
    _H_C = {
        "68414d8168619c5d5c315a94ea6efd16c64935efe9a76b7ba169f7baa3b32457",
        "79d7de3fc9ba834dc69b76513f0f62bf0fa4a5e3491b28f363590a9cf49683fa",
        "aed50ad76e3761e55641c6cb973397d1acc3db01cbb4989886385415ff7dfe84",
    }
    # Proprietary / professional 文案哈希集合
    _H_P = {
        "529f397bf4e575409a55973abb5241995bdc68d1b92ddab675353aebc1113211",
        "99ba9245b30f43d72c29f6b4ab6b108f0b3849394e2c884a3b8ef93762dd120e",
        "3196cf07f6d8379855d0884ab440afb56d8f5aa0a0e18e82390d9c62f073b0af",
    }
    _KEY = "x9K#pL2mQvR7tBnW"
    _C = "mqPrzMjDEoX80Lem/q3SzZ6ay8XFx9fl4VYWdTcqCzQTGaK69Km4zbT73d/a7Irv1d7Rp5PMvorr3LeIwaT58p+4+8bK6tTF8JPuuJfC47HlmK2PzqSQxrXJ/NHg+4jf7t7smJnVlo7R9LqYw6fe3ZG+xsb+09bQzZ7SspPY6r/Wh6ON0aiN8Lbjy9jIzojL1N/Es5foiInq87aK6KrD8Z+d8czMwNrQ/pLpgZD547L3lq2O06mK1bnJwt/Vzo3X+g=="
    _M1 = "HFwtAy8pXAs+BDFSKyUcNgFKKEIcKQ=="
    _M2 = "H0sqWgMvUwE0XmMHRGdH"
    def _d(s):
        x = base64.b64decode(s); kb = _KEY.encode()
        return bytes(c ^ kb[i % len(kb)] for i, c in enumerate(x)).decode('utf-8')
    def _h(t):
        return hashlib.sha256(t.encode('utf-8')).hexdigest()
    def _warn():
        try:
            print("\n" + "="*60)
            print(_d(_C))
            print("="*60 + "\n")
        except Exception:
            pass
        try:
            _blk = str(PROJECT_ROOT)
            _lf = os.path.join(_blk, 'data', 'agreement_violation.json')
            os.makedirs(os.path.dirname(_lf), exist_ok=True)
            _n = 1
            if os.path.exists(_lf):
                try:
                    with open(_lf, 'r', encoding='utf-8') as _f:
                        _n = int(json.load(_f).get('count', 0)) + 1
                except Exception:
                    _n = 1
            with open(_lf, 'w', encoding='utf-8') as _f:
                json.dump({'count': _n, 'last': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, _f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    try:
        _blk = str(PROJECT_ROOT)
        _tpl = os.path.join(_blk, 'web_templates', 'index.html')
        _self = open(__file__, 'r', encoding='utf-8', errors='ignore').read()
        _ok = True

        # 1) 授权数组校验：优先匹配社区版 var _a_oss，其次专业版 var _a
        _arr_found = False
        if os.path.exists(_tpl):
            _src = open(_tpl, 'r', encoding='utf-8', errors='ignore').read()
            _m = re.search(r'var\s+_a_oss\s*=\s*\[(.*?)\];', _src, re.S) \
              or re.search(r'var\s+_a\s*=\s*\[(.*?)\];', _src, re.S)
            if _m:
                _arr_found = True
                _got = set()
                for _s in re.findall(r'"([A-Za-z0-9+/=]+)"', _m.group(1)):
                    try:
                        _got.add(_h(base64.b64decode(_s).decode('utf-8')))
                    except Exception:
                        pass
                if not (_H_C.issubset(_got) or _H_P.issubset(_got)):
                    _ok = False

        # 2) 开源社区版未内置专有协议 blob（index.html 无 var _a / var _a_oss）。
        #    此时改为校验「纪念日强制灰度」设计是否保留——这才是真正要守护的红线。
        #    若灰度强制逻辑被移除，才判定为条款被破坏。
        if not _arr_found:
            if not ('def _enforce_grayscale' in _self
                    and 'def _inject_grayscale' in _self
                    and '19 <= now.day <= 21' in _self):
                _ok = False

        # 3) 自身守卫标记必须存在
        if _d(_M1) not in _self or _d(_M2) not in _self:
            _ok = False
        if not _ok:
            _warn()
    except Exception:
        pass

# ── REST API v1 ─────────────────────────────────────────────
from modules.web.api import api_v1, _ADMIN_TOKEN
app.register_blueprint(api_v1)

# ── 智能诊断中心（自专业版同步，社区常显）──
from modules.intelligence.views import intelligence_bp
app.register_blueprint(intelligence_bp)

def get_admin_token():
    return _ADMIN_TOKEN

# 全局任务状态
tasks = {}

# RAG 管理器全局单例
_rag_manager = None

# AI 聊天会话历史（key: session_id, value: list of {role, content}）
_chat_sessions = {}
_CHAT_HISTORY_LIMIT = 20  # 每个会话最多保留 20 条消息

# ── 用户认证 ───────────────────────────────────────────────
from modules.web.auth import init_default_user, register_auth_routes
init_default_user()
register_auth_routes(app)

# ── RBAC 用户管理模块注册 ──────────────────────────────────
try:
    from modules.user_management.routes.auth_routes import auth_bp as um_auth_bp
    from modules.user_management.routes.user_routes import user_bp as um_user_bp
    from modules.user_management.routes.role_routes import role_bp as um_role_bp
    from modules.user_management.routes.menu_routes import menu_bp as um_menu_bp

    app.register_blueprint(um_auth_bp)
    app.register_blueprint(um_user_bp)
    app.register_blueprint(um_role_bp)
    app.register_blueprint(um_menu_bp)

    # RBAC 登录页面
    @app.route('/um/login')
    def um_login_page():
        from flask import render_template
        resp = make_response(render_template('user_management/login.html',
                                   product_name=PRODUCT_NAME_EN,
                                   product_name_zh=PRODUCT_NAME_ZH,
                                   product_full_name=PRODUCT_FULL_NAME,
                                   product_tagline=PRODUCT_TAGLINE_EN))
        # 禁止缓存登录页，避免浏览器复用旧模板导致登录回跳循环
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    # RBAC 系统管理页面
    @app.route('/um/admin')
    @app.route('/um/')
    @app.route('/um')
    def um_admin_page():
        from flask import render_template
        return render_template('user_management/admin.html')

    # 初始化 RBAC 种子数据（仅首次）
    def _init_rbac_seed():
        import os
        seed_flag = str(paths.PRO_DATA_DIR / '.rbac_seeded')
        if not os.path.exists(seed_flag):
            try:
                from modules.user_management.seed import init_seed_data
                init_seed_data()
                with open(seed_flag, 'w') as f:
                    f.write('1')
            except Exception as e:
                print(f"  [WARN] RBAC 种子数据初始化失败: {e}")

    _init_rbac_seed()
    print("  [OK] RBAC 用户管理模块已加载")
except ImportError as e:
    print(f"  [WARN] RBAC 用户管理模块加载失败: {e}")

# ── Pro 专业版 Web 扩展点（专业版注册 flow，社区 no-op；与 main 逐字节一致）──
from modules.pro import register_web_extensions
register_web_extensions(app)

# ── 容灾备份（autobackup 引擎，in-process）──────────────────
try:
    from modules.user_management.seed import sync_menus
    sync_menus()
except Exception as e:
    print(f"  [WARN] 菜单同步失败: {e}")

try:
    from modules.web.api import register_disaster_recovery
    register_disaster_recovery(app)
    print("  [OK] 容灾备份模块已加载（autobackup）")
except Exception as e:
    print(f"  [WARN] 容灾备份模块加载失败: {e}")

# ── SQL 审核（内置模块 MVP1：解析 + 规则 + 评分，不执行）──
try:
    from modules.sqlaudit import register_sql_audit
    register_sql_audit(app)
    print("  [OK] SQL 审核模块已加载")
except Exception as e:
    print(f"  [WARN] SQL 审核模块加载失败: {e}")

# ── DocKB 官方文档知识库（内置模块，AI 诊断参考数据源）──
try:
    from modules.doc_kb import register_doc_kb
    register_doc_kb(app)
    print("  [OK] DocKB 官方文档知识库已加载")
except Exception as e:
    print(f"  [WARN] DocKB 模块加载失败: {e}")

# ── 工具函数 ───────────────────────────────────────────────
def _ts():
    return datetime.datetime.now().strftime('%H:%M:%S')

def escHtml(s):
    if s is None: return ''
    return (str(s)
        .replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        .replace('"','&quot;').replace("'",'&#39;'))

def get_task(task_id: str) -> dict:
    """获取任务信息"""
    return tasks.get(task_id)

def format_bytes(n):
    try:
        n = int(n)
        for u in ['B','KB','MB','GB','TB']:
            if n < 1024: return f"{n:.1f}{u}"
            n /= 1024
        return f"{n:.1f}PB"
    except: return str(n)

def get_reports():
    reports_dir = str(paths.REPORTS_DIR)
    reports = []
    # 读取 pro_history.db 中的风险统计，key 为报告文件名
    risk_map = {}
    try:
        import sqlite3
        pro_db = str(paths.PRO_DATA_DIR / 'pro_history.db')
        if os.path.isfile(pro_db):
            conn = sqlite3.connect(pro_db)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='inspection_history'")
            if cursor.fetchone():
                cursor.execute("SELECT report_path, auto_analyze, health_score, risk_count, risk_level FROM inspection_history")
                for row in cursor.fetchall():
                    report_path, auto_analyze_json, health_score, risk_count, risk_level = row
                    if report_path:
                        fname = os.path.basename(report_path)
                        high = mid = low = 0
                        if auto_analyze_json:
                            try:
                                import json as _json
                                items = _json.loads(auto_analyze_json)
                                for it in items:
                                    lvl = str(it.get('col4', '') or it.get('col2', ''))
                                    if '高' in lvl or 'high' in lvl.lower():
                                        high += 1
                                    elif '中' in lvl or 'mid' in lvl.lower():
                                        mid += 1
                                    else:
                                        low += 1
                            except Exception:
                                pass
                        risk_map[fname] = {
                            'high': high, 'mid': mid, 'low': low,
                            'health_score': health_score,
                            'risk_level': risk_level,
                            'auto_analyze': auto_analyze_json
                        }
            conn.close()
    except Exception:
        pass

    if os.path.isdir(reports_dir):
        try:
            files = [f for f in os.listdir(reports_dir)
                     if f.endswith('.docx') and not f.startswith('~$')
                     and not f.startswith('服务器巡检_')]
        except Exception:
            files = []
        for f in sorted(files, key=lambda x: os.path.getmtime(os.path.join(reports_dir, x)), reverse=True):
            fp = os.path.join(reports_dir, f)
            try:
                size = os.path.getsize(fp)
                mtime = os.path.getmtime(fp)
            except Exception:
                continue
            db_type = 'DM8' if 'DM8' in f or '达梦' in f else \
                      'Oracle' if 'Oracle' in f else \
                      'PostgreSQL' if 'PG' in f or 'PostgreSQL' in f else 'MySQL'
            stats = risk_map.get(f, {})
            reports.append({
                'name': f, 'size': size, 'mtime': mtime, 'db_type': db_type,
                'high': stats.get('high', 0),
                'mid': stats.get('mid', 0),
                'low': stats.get('low', 0),
                'health_score': stats.get('health_score'),
                'risk_level': stats.get('risk_level', ''),
                'auto_analyze': stats.get('auto_analyze', '')
            })
    return {'files': reports}


# ── 远程终端 API ──────────────────────────────────────────

@app.route('/api/shell/instances', methods=['GET'])
def api_shell_instances():
    """返回所有 ssh_enabled=1 的实例列表"""
    try:
        from modules.pro import get_instance_manager
        from modules.access import principal_from_session, filter_visible
        im = get_instance_manager()
        user = principal_from_session()
        instances = filter_visible(
            user, im.get_all_instances(mask_password=True), 'instance')
        result = []
        for inst in instances:
            if inst.get('ssh_enabled'):
                result.append({
                    'id': inst['id'],
                    'name': inst.get('name', inst['id']),
                    'ssh_host': inst.get('ssh_host', ''),
                    'ssh_port': inst.get('ssh_port', 22),
                    'ssh_user': inst.get('ssh_user', ''),
                })
        return jsonify({'ok': True, 'instances': result})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500

# ── 通用巡检任务（MySQL/PG/TiDB/SQLServer/IvorySQL/DM8） ──
def _web_log_is_console_only(msg):
    """判断一条日志是否仅输出到后端控制台、不推送到前端巡检日志。

    这些内部输出不应出现在 Web UI 的前端巡检日志中，仅留在后端控制台便于排查：
    - 后台采集内部日志 [metrics]
    - 连接状态类日志（连接成功/连接失败，既有规则，本次未改动）
    - 内部调试日志 [DEBUG] 开头（如 AI 诊断前 version 信息、基线检查、章节追加等）
    - AI 诊断框架日志 [AIAdvisor] 开头（RAG 知识库启用/初始化等）
    - 插件加载/注册日志 [Plugin] / [插件] 开头
    - 各 JDBC 插件注册成功提示（含「插件注册成功」，如
      [ClickHouse]/[DB2]/[HGDB]/[Oracle JDBC]/[UXDB]）
    - AI 诊断调用提示（含「正在调用 AI 诊断」，如
      「🤖 正在调用 AI 诊断（ollama / qwen3:8b）...」）
    - 报告生成内部内存监控日志 [Report] 开头（防御性过滤；当前其在拦截器恢复后执行，
      本身不会混入前端，此处一并屏蔽以防未来路径变化）

    注意：正常的巡检进度信息（如 [MySQL] 开始巡检...、📊 开始执行巡检 SQL...、
    🔍 正在进行慢查询深度分析...、✅ 慢查询深度分析完成...、[WARN]/[ERROR]/[INFO] 等）
    不会被上述规则过滤，仍会推送到前端。
    """
    _m = msg.lstrip()
    # 后台采集内部日志
    if _m.startswith('[metrics]'):
        return True
    # 连接成功类日志仅输出到后端控制台（避免刷屏）；连接失败属错误信息，
    # 必须推送到前端巡检日志——巡检失败时用户排查的第一手依据（原规则把
    # 连接失败也过滤，导致「巡检失败却没有任何错误日志」）。
    if '连接成功' in msg:
        return True
    # 内部调试日志
    if _m.startswith('[DEBUG]'):
        return True
    # AI 诊断框架日志
    if _m.startswith('[AIAdvisor]'):
        return True
    # 插件加载/注册日志
    if _m.startswith('[Plugin]') or _m.startswith('[插件]'):
        return True
    # 各 JDBC 插件注册成功提示（[X] 插件注册成功）
    if '插件注册成功' in msg:
        return True
    # AI 诊断调用提示
    if '正在调用 AI 诊断' in msg:
        return True
    # 报告生成内部内存监控日志（防御性）
    if _m.startswith('[Report]'):
        return True
    return False


def run_inspection_task(task_id, db_info, inspector_name, template_id=None, chapter_ids=None):
    """
    通用数据库巡检任务函数
    支持: MySQL, PostgreSQL, TiDB, SQL Server, IvorySQL, DM8
    通过 task_configs 字典配置各数据库类型的差异参数
    """
    db_type = db_info.pop('_db_type', None)
    if not db_type:
        socketio.emit('error', {'msg': '缺少数据库类型'}, room=task_id)
        return

    # KingbaseES 数据库名修正：若未设置或为 postgres（从 PG 复制而来），则改为 kingbase
    if db_type == 'kingbase':
        _db = db_info.get('database') or ''
        if _db in ('', 'postgres'):
            db_info['database'] = 'kingbase'

    # UXDB 数据库名修正：若未设置或为 postgres（从 PG 复制而来），则改为 uxdb
    if db_type == 'uxdb':
        _db = db_info.get('database') or ''
        if _db in ('', 'postgres'):
            db_info['database'] = 'uxdb'

    task_configs = {
        'mysql': dict(
            module_name='main_mysql',
            connect_test=test_mysql_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database'), info.get('driver_version', '')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database'),
                                        'driver_version': info.get('driver_version', '')}),
            conn_attr='conn_db2',
            smart_analyze='smart_analyze_mysql',
            filename_key='webui.mysql_report_filename',
            history_db_type='mysql',
            instance_prefix='mysql',
            error_task_name='MySQL',
            log_start_key='webui.log_mysql_start',
            err_module_key='webui.err_mysql_module',
            label_default='unknown',
            db_name_default='mysql',
        ),
        'mariadb': dict(
            module_name='main_mariadb',
            connect_test=test_mysql_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database'), info.get('driver_version', '')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database'),
                                        'driver_version': info.get('driver_version', '')}),
            conn_attr='conn_db2',
            smart_analyze='smart_analyze_mariadb',
            filename_key='webui.mariadb_report_filename',
            history_db_type='mariadb',
            instance_prefix='mariadb',
            error_task_name='MariaDB',
            log_start_key='webui.log_mariadb_start',
            err_module_key='webui.err_mariadb_module',
            label_default='MariaDB',
            db_name_default='mysql',
        ),
        'oceanbase': dict(
            module_name='main_oceanbase',
            connect_test=test_mysql_connection,
            connect_test_args=lambda info: [info['ip'], info['port'],
                                            (info['user'] + '@' + info['tenant']) if info.get('tenant') else info['user'],
                                            info['password'], info.get('database', 'sys'), info.get('driver_version', '')],
            getdata_args=lambda info: ([info['ip'], info['port'],
                                        (info['user'] + '@' + info['tenant']) if info.get('tenant') else info['user'],
                                        info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database', 'sys'),
                                        'driver_version': info.get('driver_version', '')}),
            conn_attr='conn_db2',
            smart_analyze='smart_analyze_mysql',
            filename_key='webui.oceanbase_report_filename',
            history_db_type='oceanbase',
            instance_prefix='oceanbase',
            error_task_name='OceanBase',
            log_start_key='webui.log_oceanbase_start',
            err_module_key='webui.err_oceanbase_module',
            label_default='OceanBase',
            db_name_default='sys',
        ),
        'pg': dict(
            module_name='main_pg',
            connect_test=test_pg_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'postgres'), info.get('driver_version', '')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database', 'postgres'),
                                        'driver_version': info.get('driver_version', '')}),
            conn_attr='conn_db2',
            smart_analyze='smart_analyze_pg',
            filename_key='webui.pg_report_filename',
            history_db_type='pg',
            instance_prefix='pg',
            error_task_name='PostgreSQL',
            log_start_key='webui.log_pg_start',
            err_module_key='webui.err_pg_module',
            label_default='unknown',
            db_name_default='postgres',
        ),
        'tidb': dict(
            module_name='main_tidb',
            connect_test=test_tidb_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'mysql'), info.get('driver_version', '')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database'),
                                        'driver_version': info.get('driver_version', '')}),
            conn_attr='conn_db2',
            smart_analyze='smart_analyze_tidb',
            filename_key='webui.tidb_report_filename',
            history_db_type='tidb',
            instance_prefix='tidb',
            error_task_name='TiDB',
            log_start_key='webui.log_tidb_start',
            err_module_key='webui.err_tidb_module',
            label_default='unknown',
            db_name_default='mysql',
        ),
        'sqlserver': dict(
            module_name='main_sqlserver',
            connect_test=test_sqlserver_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'master')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database'), 'label': info.get('name')}),
            conn_attr='conn_db2',
            smart_analyze='smart_analyze_sqlserver',
            filename_key='webui.sqlserver_report_filename',
            history_db_type='sqlserver',
            instance_prefix='sqlserver',
            error_task_name='SQL Server',
            log_start_key='webui.log_sqlserver_start',
            err_module_key='webui.err_sqlserver_module',
            label_default='SQLServer',
            db_name_default='master',
        ),
        # ── SQL Server (JDBC 双轨) —— P1/T-014 ──
        #   与 'sqlserver' 共用 main_sqlserver_dual.getData，按 connection_mode 路由：
        #     - 'odbc'  → main_sqlserver.SQLServerInspector（默认，向后兼容）
        #     - 'jdbc'  → plugins/available/sqlserver_jdbc.MssqlJdbcInspector
        #     - 'auto'  → 优先 JDBC（探测 jar + JPype1），失败回退 ODBC
        #   UI 前端可在「新增 SQL Server 实例」表单中通过 connection_mode 下拉框选择；
        #   后端通过 ssh_info['connection_mode'] 字段透传。
        #   注意：本类型缺省 / 空串一律按 'jdbc' 处理（类型语义即 JDBC，兼容旧实例）；
        #   显式 'jdbc' 时环境不可用会直接报错，不会静默回退 ODBC。
        'sqlserver_jdbc': dict(
            module_name='main_sqlserver_dual',
            connect_test=test_sqlserver_jdbc_connection,
            connect_test_args=lambda info: [
                info['ip'], int(info['port']), info['user'], info['password'],
                info.get('database', 'master'),
                {
                    'connection_mode': info.get('connection_mode') or 'jdbc',
                    'jdbc_url': info.get('jdbc_url', '') or '',
                    'instance_name': info.get('instance_name', '') or '',
                    'encrypt': bool(info.get('encrypt', False)),
                    'trust_server_certificate': bool(info.get('trust_server_certificate', True)),
                },
            ],
            getdata_args=lambda info: ([info['ip'], int(info['port']), info['user'], info['password']],
                                       {
                                           'ssh_info': {
                                               'database': info.get('database', 'master'),
                                               'connection_mode': info.get('connection_mode') or 'jdbc',
                                               'jdbc_url': info.get('jdbc_url', '') or '',
                                               'instance_name': info.get('instance_name', '') or '',
                                               'encrypt': bool(info.get('encrypt', False)),
                                               'trust_server_certificate': bool(info.get('trust_server_certificate', True)),
                                           },
                                           'template_id': template_id,
                                           'connection_mode': info.get('connection_mode') or 'jdbc',
                                           'label': info.get('name'),
                                       }),
            conn_attr='conn',
            smart_analyze='smart_analyze_sqlserver',
            filename_key='webui.sqlserver_jdbc_report_filename',
            history_db_type='sqlserver_jdbc',
            instance_prefix='sqlserver_jdbc',
            error_task_name='SQL Server (JDBC)',
            log_start_key='webui.log_sqlserver_jdbc_start',
            err_module_key='webui.err_sqlserver_jdbc_module',
            label_default='SQLServer-JDBC',
            db_name_default='master',
        ),
        'ivorysql': dict(
            module_name='main_ivorysql',
            connect_test=test_ivorysql_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'ivorysql'), info.get('driver_version', '')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database', 'ivorysql'),
                                        'driver_version': info.get('driver_version', '')}),
            conn_attr='conn_db2',
            smart_analyze='smart_analyze_ivorysql',
            filename_key='webui.ivorysql_report_filename',
            history_db_type='ivorysql',
            instance_prefix='ivorysql',
            error_task_name='IvorySQL',
            log_start_key='webui.log_ivorysql_start',
            err_module_key='webui.err_ivorysql_module',
            label_default='unknown',
            db_name_default='ivorysql',
        ),
        'kingbase': dict(
            module_name='main_kingbase',
            connect_test=test_kingbase_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'kingbase'), info.get('driver_version', '')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database', 'kingbase'),
                                        'driver_version': info.get('driver_version', '')}),
            conn_attr='conn_db2',
            smart_analyze='smart_analyze_kingbase',
            filename_key='webui.kingbase_report_filename',
            history_db_type='kingbase',
            instance_prefix='kingbase',
            error_task_name='KingbaseES',
            log_start_key='webui.log_kingbase_start',
            err_module_key='webui.err_kingbase_module',
            label_default='unknown',
            db_name_default='kingbase',
        ),
        'dm': dict(
            module_name='main_dm',
            connect_test=test_dm_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('driver_version','')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'db_name': info.get('database', 'DAMENG'),
                                        'driver_version': info.get('driver_version','')}),
            conn_attr='conn_db',
            smart_analyze='smart_analyze_dm',
            filename_key='webui.dm_report_filename',
            history_db_type='dm',
            instance_prefix='dm',
            error_task_name='DM8',
            log_start_key='webui.log_dm_start',
            err_module_key='webui.err_dm_module',
            label_default='DM8',
            db_name_default='DAMENG',
        ),
        'yashandb': dict(
            module_name='main_yashandb',
            connect_test=test_yashandb_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'yashandb'), info.get('driver_version', '')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database', 'yashandb'),
                                        'driver_version': info.get('driver_version', '')}),
            conn_attr='conn_db',
            smart_analyze='smart_analyze_yashandb',
            filename_key='webui.yashandb_report_filename',
            history_db_type='yashandb',
            instance_prefix='yashandb',
            error_task_name='YashanDB',
            log_start_key='webui.log_yashandb_start',
            err_module_key='webui.err_yashandb_module',
            label_default='YashanDB',
            db_name_default='YASHANDB',
        ),
        'gbase': dict(
            module_name='main_gbase',
            connect_test=test_gbase_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'testdb'), info.get('gbase_server_name', 'gbase01'), info.get('driver_version','')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                           {'ssh_info': {}, 'template_id': template_id,
                                            'database': info.get('database', 'testdb'),
                                            'gbase_server_name': info.get('gbase_server_name', 'gbase01'),
                                            'driver_version': info.get('driver_version','')}),
            conn_attr='conn_db2',
            smart_analyze='smart_analyze_gbase',
            filename_key='webui.gbase_report_filename',
            history_db_type='gbase',
            instance_prefix='gbase',
            error_task_name='GBase 8s',
            log_start_key='webui.log_gbase_start',
            err_module_key='webui.err_gbase_module',
            label_default='unknown',
            db_name_default='gbase01',
        ),
        'oracle': dict(
            module_name='main_oracle_full',
            connect_test=test_oracle_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info.get('user', 'sys'), info['password'],
                                            info.get('service_name') or info.get('sid') or info.get('database', 'orcl'),
                                            bool(info.get('sysdba', info.get('user', 'sys').upper() == 'SYS'))],
            getdata_args=lambda info: ([info['ip'], info['port'], info.get('user', 'sys'), info['password']],
                                       {'ssh_info': {}, 'service_name': info.get('service_name'),
                                        'sid': info.get('sid') or info.get('database', 'orcl'),
                                        'sysdba': bool(info.get('sysdba', info.get('user', 'sys').upper() == 'SYS')),
                                        'inspector_name': None, 'desensitize': info.get('desensitize', False),
                                        'template_id': template_id}),
            conn_attr=None,  # Oracle 连接在 single_inspection 内部管理，无需检查 conn_db
            smart_analyze='smart_analyze_oracle',
            filename_key='webui.oracle_report_filename',
            history_db_type='oracle',
            instance_prefix='oracle',
            error_task_name='Oracle',
            log_start_key='webui.log_oracle_start',
            err_module_key='webui.err_oracle_module',
            label_default='ORACLE',
            db_name_default='orcl',
        ),
    }

    emit = socketio.emit
    task = tasks.get(task_id)
    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    cfg = task_configs.get(db_type)
    if not cfg:
        # 尝试从插件系统加载配置
        try:
            from modules.pluginkit.loader import get_plugin_task_config
            cfg = get_plugin_task_config(db_type)
            if cfg:
                # 插件配置，需要动态导入模块
                import importlib.util
                plugin_path = cfg.get('plugin_path')
                if plugin_path:
                    main_file = cfg.get('main_file', 'main_plugin.py')
                    main_path = os.path.join(plugin_path, main_file)
                    spec = importlib.util.spec_from_file_location(f"plugin_{db_type}", main_path)
                    if spec:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        cfg['module'] = mod
        except Exception as e:
            print(f"[WebUI] 加载插件配置失败: {e}")
        
        if not cfg:
            err_msg = f'不支持的数据库类型: {db_type}'
            if task:
                task['status'] = 'error'
                task['error_msg'] = err_msg
            _emit('error', {'msg': err_msg})
            _emit('done', {'msg': err_msg, 'task_id': task_id})
            return

    _emit('log', {'msg': _t(cfg['log_start_key']).format(ts=_ts())})
    _emit('inspection_step', {'step': 0, 'msg': _t('webui.log_connecting').format(ts=_ts(), host=db_info['ip'], port=db_info['port'])})

    try:
        # 插件类型：使用动态导入的模块
        if cfg.get('module'):
            mod = cfg['module']
        else:
            # 内置类型：常规导入（entrypoints 包下的 main_* 模块，绝对导入）
            mod = __import__('modules.entrypoints.' + cfg['module_name'], fromlist=['_'])
        
        _emit('log', {'msg': _t('webui.log_connecting').format(ts=_ts(), host=db_info['ip'], port=db_info['port'])})

        ok, ver = cfg['connect_test'](*cfg['connect_test_args'](db_info))
        if not ok:
            raise RuntimeError(_t('webui.err_db_connect').format(ver=ver))
        _emit('log', {'msg': _t('webui.log_connected').format(ts=_ts(), ver=ver)})
        _emit('inspection_step', {'step': 1, 'msg': _t('webui.log_executing_sql').format(ts=_ts())})

        # SSH
        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host', 'ssh_port', 'ssh_user', 'ssh_password', 'ssh_key_file', 'ssh_ebpf') if k in db_info}

        # MongoDB 专用参数透传到 ssh_info（供插件 getData → MongoConnectionConfig 使用）
        if db_type == 'mongodb':
            for _mk in ('database', 'connect_mode', 'auth_source', 'auth_mechanism',
                        'replica_set', 'tls', 'tls_ca_file', 'tls_cert_key_file',
                        'tls_allow_invalid_certs'):
                if _mk in db_info:
                    ssh_info[_mk] = db_info[_mk]
        # Redis / Redis Cluster 专用参数透传到 ssh_info（供插件 getData 使用）
        if db_type in ('redis', 'redis-cluster'):
            for _mk in ('database', 'seed_nodes'):
                if _mk in db_info:
                    ssh_info[_mk] = db_info[_mk]
        # SQL Server (JDBC) 专用参数透传到 ssh_info（P1/T-014）
        #   - connection_mode: 'odbc' / 'jdbc' / 'auto'，控制 main_sqlserver_dual 路由
        #   - jdbc_url / instance_name / encrypt / trust_server_certificate: JDBC 连接参数
        if db_type == 'sqlserver_jdbc':
            for _mk in ('database', 'connection_mode', 'jdbc_url', 'instance_name',
                        'encrypt', 'trust_server_certificate'):
                if _mk in db_info:
                    ssh_info[_mk] = db_info[_mk]

        # 激活 print 拦截器（需在 getData 之前就激活，因为 Oracle 的日志在 getData 内部产生）
        import builtins as _bi
        _orig_print = _bi.print
        def _web_print(*_a, **_kw):
            _sep = _kw.get('sep', ' ')
            _msg = _sep.join(str(x) for x in _a)
            _msg_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', _msg)
            # 后台采集/内部日志（如 [metrics]）及连接状态日志（连接成功/连接失败）仅输出到后端控制台，不推送到前端巡检日志
            if _msg_clean.strip() and not _web_log_is_console_only(_msg_clean):
                _emit('log', {'msg': _msg_clean})
            _orig_print(*_a, **_kw)
        _bi.print = _web_print

        try:
            # getData
            _pos, _kw = cfg['getdata_args'](db_info)
            if ssh_info:
                _kw['ssh_info'] = ssh_info
            data = mod.getData(*_pos, **_kw)

            # 章节可选：将前端传入的 chapter_ids 注入到检查器实例（兼容所有内置/插件入口）。
            # 内置入口的 CompatWrapper.inspector 与插件引擎均暴露 chapter_ids 属性，
            # collect_data / _render_context / _load_chapters_from_db 已消费该字段：
            #   None        -> 全量巡检（不过滤）
            #   [int, ...]  -> 仅巡检选中章节
            if chapter_ids is not None:
                _insp = getattr(data, 'inspector', None)
                if _insp is None and hasattr(data, 'chapter_ids'):
                    _insp = data
                if _insp is not None and hasattr(_insp, 'chapter_ids'):
                    _insp.chapter_ids = chapter_ids

            if data is None or (cfg['conn_attr'] and getattr(data, cfg['conn_attr'], None) is None):
                raise RuntimeError(_t('webui.err_getdata_none'))

            # AI诊断
            _emit('inspection_step', {'step': 2, 'msg': _t('webui.log_analyzing').format(ts=_ts())})

            context = data.checkdb('builtin')

        finally:
            _bi.print = _orig_print

        if not context:
            raise RuntimeError(_t('webui.err_checkdb_false'))

        if context.get('ai_advice'):
            _emit('log', {'msg': _t('webui.log_ai_done').format(ts=_ts())})
        if task:
            task['ai_advice'] = context.get('ai_advice', '')

        # 生成报告
        _emit('inspection_step', {'step': 3, 'msg': _t('webui.log_generating_report').format(ts=_ts())})
        label_name = db_info.get('name', cfg['label_default'])
        db_name = db_info.get('database') or cfg['db_name_default']

        context['co_name'] = [{'CO_NAME': db_name}]
        context['port'] = [{'PORT': db_info['port']}]
        context['ip'] = [{'IP': db_info['ip']}]

        inspector_nm = db_info.get('inspector_name') or inspector_name or 'Jack'

        reports_dir = str(paths.REPORTS_DIR)
        os.makedirs(reports_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename_tmpl = _t(cfg['filename_key'])
        ext_name = filename_tmpl.format(ip=db_info['ip'], name=label_name, ts=timestamp)
        file_name = ext_name + '.docx'
        ofile = os.path.join(reports_dir, file_name)

        if db_info.get('desensitize'):
            from modules.desensitize import apply_desensitization
            context = apply_desensitization(context)

        ofile_result = data.generate_report(ofile, inspector_nm)
        print(f"[DEBUG] generate_report 返回: {ofile_result}")
        print(f"[DEBUG] ofile 路径: {ofile}")
        if ofile_result and os.path.exists(ofile_result):
            print(f"[DEBUG] 文件确实存在，大小: {os.path.getsize(ofile_result)}")
        if not ofile_result:
            # 补充诊断：文件是否存在、目录是否可写，便于定位
            _writable = os.access(reports_dir, os.W_OK)
            print(f"[ERROR] generate_report 返回空 ({ofile_result})；"
                  f"目标路径={ofile}；目录可写={_writable}；"
                  f"文件已存在={os.path.exists(ofile)}")
            raise RuntimeError(_t('webui.err_report_generate'))
        _emit('log', {'msg': _t('webui.log_report_ok').format(fname=file_name)})
        print(f"[REPORT] 报告已生成: {ofile_result}")
        print(f"[REPORT] 文件是否存在: {os.path.exists(ofile_result)}")

        # 智能分析
        try:
            _analyzer_mod = __import__('modules.inspection.analyzer', fromlist=['_'])
            # 保留 checkdb()/基线检查已产生的风险，避免被 analyzer 空结果覆盖
            existing_auto = context.get('auto_analyze') or []
            analyzer_issues = getattr(_analyzer_mod, cfg['smart_analyze'])(context) or []
            seen = {item.get('col1', '') for item in existing_auto if isinstance(item, dict)}
            auto_analyze = list(existing_auto)
            for item in analyzer_issues:
                if isinstance(item, dict) and item.get('col1', '') not in seen:
                    auto_analyze.append(item)
                    seen.add(item.get('col1', ''))
            # ── 插件系统：执行插件 SQL + 分析 ──
            try:
                from modules.pluginkit.core import run_plugin_inspections_for_db
                # 构建 SQL 执行器（使用当前检查器的连接）
                def _exec_plugin_sql(sql):
                    cur = getattr(data, 'conn', None)
                    if cur is None:
                        try:
                            cur = getattr(data, 'cursor', None)
                        except Exception:
                            pass
                    if cur is None:
                        return {"headers": [], "rows": [], "_error": "无可用数据库连接"}
                    try:
                        cur.execute(sql)
                        cols = [d[0] for d in cur.description] if cur.description else []
                        rows = [list(r) for r in cur.fetchall()]
                        return {"headers": cols, "rows": rows}
                    except Exception as e:
                        return {"headers": [], "rows": [], "_error": str(e)}

                plugin_issues = run_plugin_inspections_for_db(
                    cfg['history_db_type'], context, execute_sql=_exec_plugin_sql)
                if plugin_issues:
                    for item in plugin_issues:
                        if isinstance(item, dict) and item.get('col1', '') not in seen:
                            auto_analyze.append(item)
                            seen.add(item.get('col1', ''))
                    _emit('log', {'msg': f"[插件] {len(plugin_issues)} 个插件发现附加风险"})
            except Exception as e:
                _emit('log', {'msg': f"[插件] 执行跳过: {e}"})
            if task:
                task['auto_analyze'] = auto_analyze
            _emit('log', {'msg': f"[智能分析] 完成，发现 {len(auto_analyze)} 个可优化项"})
        except Exception as e:
            # 即使 analyzer 调用失败，也尝试保留 context 中已有的 auto_analyze
            auto_analyze = context.get('auto_analyze') or []
            if task:
                task['auto_analyze'] = auto_analyze
            _emit('log', {'msg': f"[警告] 智能分析失败: {e}；保留已有 {len(auto_analyze)} 项"})

        # 历史快照
        try:
            from modules.inspection.analyzer import HistoryManager
            hm = HistoryManager(BASE_DIR)
            hm.save_snapshot(
                db_type=cfg['history_db_type'],
                host=db_info['ip'],
                port=db_info['port'],
                label=db_info.get('name', db_info['ip']),
                context=context
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] 历史快照保存失败: {e}"})

        # 计算评分和风险等级（供前端展示使用）
        risk_count = context.get('risk_count', 0)
        if not risk_count:
            issues = context.get('issues', [])
            risk_count = len(issues) if isinstance(issues, list) else 0

        health_status = context.get('health_status', '')
        if '优秀' in health_status or 'Excellent' in health_status:
            health_score = 100
        elif '良好' in health_status or 'Good' in health_status:
            health_score = 80
        elif '一般' in health_status or 'Fair' in health_status:
            health_score = 60
        elif '需关注' in health_status or 'Attention' in health_status:
            health_score = 40
        else:
            health_score = 100 - min(risk_count * 5, 50)

        if health_score >= 85:
            risk_level = 'healthy'
        elif health_score >= 70:
            risk_level = 'low'
        elif health_score >= 50:
            risk_level = 'medium'
        elif health_score >= 30:
            risk_level = 'high'
        else:
            risk_level = 'critical'

        # 先设置 task['result']，确保前端一定能拿到结果（不受 Pro 记录保存影响）
        if task:
            _aa = task.get('auto_analyze') or []
            _safe_issues = []
            for item in _aa:
                if isinstance(item, dict):
                    _safe_issues.append({
                        'level': _tr(item.get('col2', '')),
                        'description': _tr(item.get('col1', '')),
                        'suggestion': _tr(item.get('col3', '')),
                    })
            task['result'] = {
                'db_type': cfg['history_db_type'],
                'host': db_info['ip'],
                'port': db_info['port'],
                'label': label_name,
                'health_score': health_score,
                'health_status': health_status,
                'risk_count': risk_count,
                'risk_level': risk_level,
                'finished_at': datetime.datetime.now().isoformat(),
                'issues': _safe_issues,
                'report_file': ofile,
                'report_name': file_name,
                'ai_advice': context.get('ai_advice', ''),
            }
            # status 必须在 result 之后设置，避免前端轮询到 done 但 result 还是空的
            task['status'] = 'done'
            task['report_file'] = ofile
            task['report_name'] = file_name
            # report_path 与 report_file 同源（子进程隔离路径的 DONE 行与主进程
            # /api/task_status 都依赖 task['report_path']；此处与 Pro 记录保持一致）。
            task['report_path'] = ofile

        # Pro巡检记录（失败不影响前端结果展示）
        try:
            from modules.pro import get_instance_manager
            im = get_instance_manager()
            # 优先使用数据源ID，便于后续按数据源做协同诊断与历史查询；
            # 兼容旧版手动输入模式（无 datasource_id）时退回到 hash(ip:port)
            task_record = tasks.get(task_id, {})
            datasource_id = task_record.get('datasource_id')
            if datasource_id:
                instance_id = datasource_id
            else:
                import hashlib
                raw = f"{cfg['instance_prefix']}-{db_info['ip']}-{db_info['port']}".encode()
                instance_id = hashlib.md5(raw).hexdigest()[:12]
            im.record_inspection(
                instance_id=instance_id,
                instance_name=label_name,
                db_type=cfg['history_db_type'],
                health_score=health_score,
                risk_count=risk_count,
                risk_level=risk_level,
                report_path=ofile,
                duration=0,
                host=db_info.get('ip', ''),
                auto_analyze=auto_analyze if auto_analyze else []
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] Pro 巡检记录保存失败: {e}"})

        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id,
                       'ai_advice': context.get('ai_advice', '')})
    except Exception as e:
        import traceback
        _tb = traceback.format_exc()
        # 巡检失败：错误信息必须可见（此前 traceback 走 stdout 非事件行被主进程
        # 忽略、error 事件前端无监听 → 「巡检失败却没有任何错误日志」）。
        # 双通道：控制台保留原始堆栈 + log 事件推送前端日志面板。
        try:
            sys.stdout.write(_tb + '\n')
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        _err_summary = _t('webui.err_inspection').format(task=cfg['error_task_name'], e=str(e))
        _emit('log', {'msg': _err_summary})
        _emit('log', {'msg': f'[{cfg["error_task_name"]}] 巡检失败堆栈:\n{_tb}'})
        _emit('error', {'msg': f'{_err_summary}\n{_tb}'})
        if task:
            task['status'] = 'error'
            task['error_msg'] = str(e)
# ── 配置基线检查任务 ────────────────────────────────────────
def run_config_task(task_id, db_info, output_format='txt'):
    """配置基线检查 Web UI 任务"""
    emit = socketio.emit
    task = tasks.get(task_id)

    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': f"[{_ts()}] Starting Config Baseline check..."})

    try:
        db_type = db_info.get('db_type', 'mysql')
        reports_dir = str(paths.REPORTS_DIR)
        os.makedirs(reports_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

        if db_type == 'mysql':
            import pymysql
            conn = pymysql.connect(
                host=db_info['host'], port=int(db_info['port']),
                user=db_info['user'], password=db_info['password'],
                charset='utf8mb4'
            )
            db_label = 'MySQL'
        elif db_type == 'oceanbase':
            import pymysql
            conn = pymysql.connect(
                host=db_info['host'], port=int(db_info.get('port') or 2881),
                user=db_info['user'], password=db_info['password'],
                database=db_info.get('database', 'sys'),
                charset='utf8mb4'
            )
            db_label = 'OceanBase'
        elif db_type in ('pg', 'ivorysql', 'kingbase'):
            import psycopg2
            conn = psycopg2.connect(
                host=db_info['host'], port=int(db_info['port']),
                user=db_info['user'], password=db_info['password'],
                database=db_info.get('database', 'postgres')
            )
            db_label = 'IvorySQL' if db_type == 'ivorysql' else 'PostgreSQL'
        else:
            raise ValueError(f"Unsupported db_type: {db_type}")

        _emit('log', {'msg': f"[{_ts()}] Connected to {db_label}, analyzing configuration..."})

        from modules.inspection.config_baseline import get_config_baseline, format_config_baseline_report
        report = get_config_baseline(db_type, conn)
        conn.close()

        _emit('log', {'msg': f"[{_ts()}] Generating {output_format.upper()} report..."})

        label = db_info.get('label', db_info.get('host', 'unknown'))
        if output_format == 'pdf':
            from modules.web.pdf_export import generate_config_baseline_pdf_report
            file_name = f"{db_label}配置基线报告_{label}_{timestamp}.pdf"
            ofile = os.path.join(reports_dir, file_name)
            success, result = generate_config_baseline_pdf_report(report, ofile, db_type)
            if not success:
                raise RuntimeError(result)
        else:
            report_text = format_config_baseline_report(report, db_type)
            file_name = f"{db_label}配置基线报告_{label}_{timestamp}.txt"
            ofile = os.path.join(reports_dir, file_name)
            with open(ofile, 'w', encoding='utf-8') as f:
                f.write(report_text)
            # 打印到日志
            for line in report_text.split('\n'):
                if line.strip():
                    _emit('log', {'msg': line})

        _emit('log', {'msg': f"[{_ts()}] Report generated: {file_name}"})

        if task:
            task['status'] = 'done'
            task['report_file'] = ofile
            task['report_name'] = file_name

        _emit('done', {'msg': f"Config Baseline check completed: {file_name}", 'task_id': task_id})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': f"Config Baseline check failed: {e}\n{traceback.format_exc()}"})


# ── 索引健康分析任务 ────────────────────────────────────────
def run_index_task(task_id, db_info, output_format='txt'):
    """索引健康分析 Web UI 任务"""
    emit = socketio.emit
    task = tasks.get(task_id)

    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': f"[{_ts()}] Starting Index Health Analysis..."})

    try:
        db_type = db_info.get('db_type', 'mysql')
        reports_dir = str(paths.REPORTS_DIR)
        os.makedirs(reports_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

        if db_type == 'mysql':
            import pymysql
            conn = pymysql.connect(
                host=db_info['host'], port=int(db_info['port']),
                user=db_info['user'], password=db_info['password'],
                charset='utf8mb4'
            )
            db_label = 'MySQL'
        elif db_type == 'oceanbase':
            import pymysql
            conn = pymysql.connect(
                host=db_info['host'], port=int(db_info.get('port') or 2881),
                user=db_info['user'], password=db_info['password'],
                database=db_info.get('database', 'sys'),
                charset='utf8mb4'
            )
            db_label = 'OceanBase'
        elif db_type in ('pg', 'ivorysql', 'kingbase'):
            import psycopg2
            conn = psycopg2.connect(
                host=db_info['host'], port=int(db_info['port']),
                user=db_info['user'], password=db_info['password'],
                database=db_info.get('database', 'postgres')
            )
            db_label = 'IvorySQL' if db_type == 'ivorysql' else 'PostgreSQL'
        else:
            raise ValueError(f"Unsupported db_type: {db_type}")

        _emit('log', {'msg': f"[{_ts()}] Connected to {db_label}, analyzing indexes..."})

        from modules.inspection.index_health import get_index_health, format_index_health_report
        report = get_index_health(db_type, conn)
        conn.close()

        _emit('log', {'msg': f"[{_ts()}] Generating {output_format.upper()} report..."})

        label = db_info.get('label', db_info.get('host', 'unknown'))
        if output_format == 'pdf':
            from modules.web.pdf_export import generate_index_health_pdf_report
            file_name = f"{db_label}索引健康分析_{label}_{timestamp}.pdf"
            ofile = os.path.join(reports_dir, file_name)
            success, result = generate_index_health_pdf_report(report, ofile, db_type)
            if not success:
                raise RuntimeError(result)
        else:
            report_text = format_index_health_report(report, db_type)
            file_name = f"{db_label}索引健康分析_{label}_{timestamp}.txt"
            ofile = os.path.join(reports_dir, file_name)
            with open(ofile, 'w', encoding='utf-8') as f:
                f.write(report_text)
            for line in report_text.split('\n'):
                if line.strip():
                    _emit('log', {'msg': line})

        _emit('log', {'msg': f"[{_ts()}] Report generated: {file_name}"})

        if task:
            task['status'] = 'done'
            task['report_file'] = ofile
            task['report_name'] = file_name

        _emit('done', {'msg': f"Index Health Analysis completed: {file_name}", 'task_id': task_id})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': f"Index Health Analysis failed: {e}\n{traceback.format_exc()}"})


# ── 连接测试函数 ────────────────────────────────────────────
def test_mysql_connection(host, port, user, password, database=None, driver_version=''):
    """测试 MySQL 连接（统一 JDBC 子进程优先，回退 pymysql）。"""
    return run_jdbc_test_subprocess('mysql', {
        'host': host, 'port': port, 'user': user, 'password': password,
        'database': database,
    }, extra_kwargs={'driver_version': driver_version})


def test_mariadb_connection(host, port, user, password, database=None, driver_version=''):
    """测试 MariaDB 连接（统一 JDBC 子进程优先，回退 pymysql）。

    独立于 test_mysql_connection：MariaDB 走 jdbc:mariadb:// + org.mariadb.jdbc.Driver，
    复用 mysql 会把测试路由到 MySQL 链路（catalog=mysql），登记了 MariaDB 专用驱动的
    用户会误报「mysql 驱动未找到」。
    """
    return run_jdbc_test_subprocess('mariadb', {
        'host': host, 'port': port, 'user': user, 'password': password,
        'database': database,
    }, extra_kwargs={'driver_version': driver_version})


def test_oceanbase_connection(host, port, user, password, database=None, driver_version=''):
    """测试 OceanBase 连接（统一 JDBC 子进程优先，回退 pymysql）。

    独立于 test_mysql_connection：OceanBase 走 jdbc:oceanbase:// + com.oceanbase.jdbc.Driver；
    user 需含租户拼接（root@tenant），由调用方（_ct_oceanbase）负责拼好后传入。
    """
    return run_jdbc_test_subprocess('oceanbase', {
        'host': host, 'port': port, 'user': user, 'password': password,
        'database': database,
    }, extra_kwargs={'driver_version': driver_version})

def test_tidb_connection(host, port, user, password, database=None, driver_version=''):
    """测试 TiDB 连接（统一 JDBC 子进程优先，回退 pymysql）。"""
    return run_jdbc_test_subprocess('tidb', {
        'host': host, 'port': port, 'user': user, 'password': password,
        'database': database,
    }, extra_kwargs={'driver_version': driver_version})

def test_pg_connection(host, port, user, password, database='postgres', driver_version=''):
    """测试 PostgreSQL 连接（统一 JDBC 子进程隔离；JDBC 不可用回退 psycopg2）。

    JVM 在独立子进程内执行，避免主进程 gevent hub 被钉死；与巡检引擎
    同一套统一连接层（modules.jdbc_connector.open_jdbc_connection）。
    """
    return run_jdbc_test_subprocess('pg', {
        'host': host, 'port': port, 'user': user, 'password': password,
        'database': database,
    }, extra_kwargs={'driver_version': driver_version})

def test_kingbase_connection(host, port, user, password, database='kingbase', driver_version=''):
    """测试 KingbaseES 连接（统一 JDBC 子进程隔离；JDBC 不可用回退 psycopg2）。

    KingbaseES V8 官方驱动 com.kingbase8.jdbc.Driver / jdbc:kingbase8://；
    与巡检引擎同一套统一连接层。
    """
    return run_jdbc_test_subprocess('kingbase', {
        'host': host, 'port': port, 'user': user, 'password': password,
        'database': database,
    }, extra_kwargs={'driver_version': driver_version})


def test_ivorysql_connection(host, port, user, password, database='ivorysql', driver_version=''):
    """测试 IvorySQL 连接（统一 JDBC：兼容 PG 协议，复用 PostgreSQL 驱动）。

    与其它 JDBC 类型一致，在独立子进程内建连（JVM 不进入主进程，
    避免钉死 gevent hub）。驱动 jar 优先驱动管理登记的版本，
    否则回退 drivers/postgresql/ 自动发现。
    """
    return run_jdbc_test_subprocess('ivorysql', {
        'host': host, 'port': port, 'user': user, 'password': password,
        'database': database,
    }, extra_kwargs={'driver_version': driver_version})

def _find_oracle_client_lib_dir(platform_key=None):
    """查找 Oracle Client 的 lib 目录，支持根目录和 lib/ 子目录

    搜索顺序：
    1. drivers/oracle_client/<platform>/ 根目录
    2. drivers/oracle_client/<platform>/lib/ 子目录
    3. drivers/oracle_client/<platform>/ 下递归搜索标记文件

    Returns:
        str or None: 找到的 lib 目录路径，如果没找到返回 None
    """
    import platform as _pl
    if platform_key is None:
        sys_name = _pl.system().lower()
        arch = _pl.machine().lower()
        if sys_name == 'windows':
            platform_key = 'windows_x64'
        elif sys_name == 'linux':
            platform_key = 'linux_x64'
        elif sys_name == 'darwin':
            platform_key = 'darwin_arm64' if arch in ('arm64', 'aarch64') else 'darwin_x64'
        else:
            return None

    # 确定标记文件
    if 'windows' in platform_key:
        marker = 'oci.dll'
    elif 'linux' in platform_key:
        marker = 'libclntsh.so'
    else:
        marker = 'libclntsh.dylib'

    # 基础目录
    import sys
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = BASE_DIR

    client_dir = os.path.join(base_dir, 'drivers', 'oracle_client', platform_key)

    if not os.path.isdir(client_dir):
        return None

    # 1. 先检查根目录
    if os.path.isfile(os.path.join(client_dir, marker)):
        return client_dir

    # 2. 再检查 lib/ 子目录（Oracle 完整客户端常见结构）
    lib_dir = os.path.join(client_dir, 'lib')
    if os.path.isdir(lib_dir) and (
        os.path.isfile(os.path.join(lib_dir, marker)) or
        os.path.isfile(os.path.join(lib_dir, marker + '.11.1')) or
        any(f.startswith(marker) for f in os.listdir(lib_dir) if marker in f)
    ):
        return lib_dir

    # 3. 自动发现子目录（递归搜索，处理 instantclient_xx_x 子目录）
    for root, dirs, files in os.walk(client_dir):
        if marker in files:
            return root

    return None


def test_oracle_connection(host, port, user, password, service_name='ORCL', sysdba=False, jdbc_url=None):
    try:
        import oracledb
        # 解析 "user as sysdba" 语法
        _user = user.strip()
        _mode = oracledb.SYSDBA if (sysdba or re.search(r'\bas\s+sysdba\b', _user, re.IGNORECASE)) else None
        _user = re.sub(r'\s+as\s+sysdba\b', '', _user, flags=re.IGNORECASE).strip()
        _jdbc = (jdbc_url or '').strip()
        if _jdbc and _jdbc.lstrip().upper().startswith('(DESCRIPTION'):
            # 纯 TNS 描述符：oracledb 可直接作为 dsn 使用
            kw = dict(user=_user, password=password, dsn=_jdbc)
        else:
            kw = dict(user=_user, password=password, host=host, port=int(port),
                      service_name=service_name, tcp_connect_timeout=15)
        if _mode is not None:
            kw['mode'] = _mode
        try:
            try:
                conn = oracledb.connect(**kw)
            except TypeError as te:
                if 'tcp_connect_timeout' in str(te):
                    kw.pop('tcp_connect_timeout', None)
                    conn = oracledb.connect(**kw)
                else:
                    raise
        except Exception as e:
            err_str = str(e)
            if 'DPY-3010' in err_str or 'DPY-3015' in err_str:
                # thin mode 不支持 11g / 老密码验证器(0x939)，尝试 thick mode
                _ok = False
                try:
                    oracledb.init_oracle_client()
                    _ok = True
                except Exception:
                    pass
                if not _ok:
                    # 使用辅助函数查找 Oracle Client 目录（支持 lib/ 子目录）
                    _lib_dir = _find_oracle_client_lib_dir()
                    if _lib_dir:
                        try:
                            oracledb.init_oracle_client(lib_dir=_lib_dir)
                            _ok = True
                        except Exception:
                            pass
                if not _ok:
                    return False, 'Oracle 11g 需要 Instant Client，请将包解压到 drivers/oracle_client/ 对应平台目录（支持根目录或 lib/ 子目录）'
                conn = oracledb.connect(**kw)
            else:
                raise
        cur = conn.cursor()
        cur.execute("SELECT BANNER FROM V$VERSION WHERE ROWNUM=1")
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, ver
    except Exception as e:
        return False, str(e)

def test_dm_connection(host, port, user, password, driver_version=''):
    # 优先使用 JDBC 驱动（纯 Java，无需达梦原生客户端 libdmcrypt），
    # 驱动 jar 优先「数据库驱动管理」中登记的指定版本，否则
    # 置于 <项目根>/drivers/dm8/DmJdbcDriver*.jar。无原生客户端依赖，
    # 从根本上规避 -70089 Encryption module failed to load。
    try:
        from modules.jdbc_test_cli import _find_dm_jdbc_jar
        _jar = _find_dm_jdbc_jar()
    except Exception:  # noqa: BLE001 - 导入失败则按无 JDBC 处理，走 dmPython 回退
        _jar = None
    if _jar:
        return run_jdbc_test_subprocess('dm', {
            'host': host, 'port': port, 'user': user, 'password': password,
        }, extra_kwargs={'driver_version': driver_version})

    # 回退：dmPython（需要本机安装达梦客户端原生库 libdmcrypt.so）
    try:
        import dmPython
        conn = dmPython.connect(user=user, password=password, server=host, port=int(port))
        cur = conn.cursor()
        cur.execute("SELECT STATUS$ FROM V$INSTANCE")
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, ver
    except Exception as e:
        err = str(e)
        if 'No module named' in err and 'dmPython' in err:
            return False, ('未安装 dmPython 驱动，且 drivers/dm8/ 下未找到 JDBC 驱动'
                           '（DmJdbcDriver*.jar），无法测试达梦连接。请将达梦 JDBC 驱动'
                           '放入 drivers/dm8/（推荐，免装客户端）。')
        if '-70089' in err or 'Encryption module' in err or 'dmCrypt' in err:
            return False, ('达梦客户端原生库(dmCrypt)未加载，无法用 dmPython 连接。推荐将达梦 JDBC '
                           '驱动 DmJdbcDriver*.jar 放入 drivers/dm8/（免装客户端即可测试）；'
                           '或在运行本服务的机器安装 DM8 客户端（与数据库同大版本），并将其 bin 目录加入 '
                           'LD_LIBRARY_PATH(Linux)/PATH(Windows)后重启服务。'
                           f'（原始错误：{err}）')
        return False, err


def test_yashandb_connection(host, port, user, password, database='yashandb', driver_version=''):
    """测试 YashanDB 连接（统一 JDBC 子进程隔离；JDBC 不可用回退 yasdb）。

    YashanDB 官方驱动 com.yashandb.jdbc.Driver / jdbc:yashandb://；
    与巡检引擎同一套统一连接层。
    """
    return run_jdbc_test_subprocess('yashandb', {
        'host': host, 'port': port, 'user': user, 'password': password,
        'database': database,
    }, extra_kwargs={'driver_version': driver_version})


def test_gbase_connection(host, port, user, password, database='testdb', gbase_server_name='gbase01', driver_version=''):
    """测试 GBase 8s 连接（JDBC 模式，使用 jaydebeapi）"""
    try:
        from modules.entrypoints.main_gbase import test_gbase_jdbc_connection
        return test_gbase_jdbc_connection(host, port, user, password, database, gbase_server_name, driver_version)
    except Exception as e:
        return False, f"导入 main_gbase 失败：{e}"

def _get_sqlserver_driver():
    """获取系统已安装的 SQL Server ODBC 驱动（按优先级排序）"""
    try:
        import pyodbc
        installed = [d for d in pyodbc.drivers() if d.strip()]
        preferred = ['ODBC Driver 18 for SQL Server', 'ODBC Driver 17 for SQL Server',
                     'ODBC Driver 13 for SQL Server', 'SQL Server']
        for d in preferred:
            if d in installed:
                return d
        if installed:
            return installed[0]
    except Exception:
        pass
    return 'ODBC Driver 17 for SQL Server'


def _build_sqlserver_conn_str(host, port, user, password, database=None, timeout=10):
    """
    构建 SQL Server ODBC 连接字符串（动态检测驱动）
    密码为 None/空串时省略 PWD（Windows 认证）；
    密码中含分号时用单引号包裹，避免断串。
    """
    driver = _get_sqlserver_driver()
    # 详细调试
    print(f"[SQL Server] password 类型: {type(password)}, 值长度: {len(str(password)) if password else 0}, repr: {repr(password)}")
    # 拼 UID（用户名）
    uid_part = ";UID=" + str(user) if user else ""
    # 拼 PWD（密码为 None 或空字符串时完全省略，触发 Windows 认证）
    pwd_part = ""
    if password and str(password):
        _pwd = str(password)
        if ';' in _pwd:
            pwd_part = ";PWD='" + _pwd + "'"
        else:
            pwd_part = ";PWD=" + _pwd
    conn_str = ("DRIVER={" + driver + "};"
                "SERVER=" + str(host) + "," + str(port) +
                uid_part + pwd_part +
                ";TrustServerCertificate=yes;Connection Timeout=" + str(timeout))
    if database:
        conn_str += ";DATABASE=" + str(database)
    # 调试输出（密码打码）
    _debug_pwd = "***" if password else "(empty/None)"
    print(f"[SQL Server] 连接字符串调试: DRIVER={{{driver}}};SERVER={host},{port};UID={user};PWD={_debug_pwd};DB={database}")
    return conn_str


def test_sqlserver_connection(host, port, user, password, database='master'):
    """测试 SQL Server 连接（调用 main_sqlserver 的 connect 方法，支持多驱动 fallback）"""
    try:
        from modules.entrypoints.main_sqlserver import SQLServerInspector
        inspector = SQLServerInspector(host, int(port), user, password, database)
        ok, ver = inspector.connect()
        if ok:
            return True, ver
        else:
            return False, ver
    except Exception as e:
        return False, str(e)


def test_sqlserver_jdbc_connection(host, port, user, password, database='master',
                                   jdbc_opts=None, **kwargs):
    """测试 SQL Server JDBC 连接（双轨：odbc / jdbc / auto）。

    Args:
        host / port / user / password / database: 标准连接参数
        jdbc_opts: 可选 dict，JDBC 选项包。task_configs['sqlserver_jdbc'] 的
            connect_test_args 以**第 6 个位置参数**传入该 dict（**kwargs 只能吸收
            关键字参数，无法吸收位置参数，故需显式形参）。
        connection_mode: 'odbc' | 'jdbc' | 'auto'（缺省/空串 → 'jdbc'）
            - 'jdbc'：强制 JDBC。环境不可用时**直接返回错误，绝不回退 ODBC**。
            - 'auto'：JDBC 优先，环境不可用或连接失败时回退 ODBC。
            - 'odbc'：直接走 pyodbc。
        jdbc_url / instance_name / encrypt / trust_server_certificate: JDBC 专用

    Returns:
        (ok, msg)：ok=True 时 msg 是版本可读串（如 'Microsoft SQL Server 2019'）
    """
    # 合并两种传参风格：关键字参数（老调用/测试）与位置 dict（task_configs 调用）。
    # jdbc_opts 优先级更高，因为它是任务配置显式构造的完整选项包。
    opts = {**kwargs, **(jdbc_opts or {})}
    # 本函数只服务 sqlserver_jdbc 类型：缺省 / 空串一律按 'jdbc' 处理（向后兼容旧实例）。
    # 需要 ODBC 的场景由 'sqlserver' 类型或显式 connection_mode='odbc' 覆盖。
    connection_mode = (opts.get('connection_mode') or 'jdbc').strip().lower()
    try:
        # 走双轨入口（main_sqlserver_dual 自动按 mode 路由 JDBC/ODBC）
        from modules.entrypoints.main_sqlserver_dual import jdbc_unavailable_reason

        # 显式 'jdbc'：环境不满足必须直接报错，**绝不静默回退 ODBC**，
        # 否则用户会看到与所选模式无关的 unixODBC 'SQL Server' 报错（误导性极强）。
        if connection_mode == 'jdbc':
            _reason = jdbc_unavailable_reason()
            if _reason:
                return False, f'JDBC 驱动/JVM 环境不可用: {_reason}'

        # 'auto' 时探测，不可用则回退 ODBC；'jdbc' 走到此处说明环境已就绪
        if connection_mode == 'jdbc' or (
                connection_mode == 'auto' and jdbc_unavailable_reason() is None):
            # 用 dual getData 触发 connect + 立即断开
            from modules.entrypoints.main_sqlserver_dual import SQLServerDualInspector
            try:
                insp = SQLServerDualInspector(
                    host=host, port=int(port), user=user, password=password,
                    database=database, connection_mode=connection_mode,
                    ssh_info={
                        'database': database,
                        'jdbc_url': opts.get('jdbc_url', '') or '',
                        'instance_name': opts.get('instance_name', '') or '',
                        'encrypt': bool(opts.get('encrypt', False)),
                        'trust_server_certificate': bool(opts.get('trust_server_certificate', True)),
                    },
                )
                ok, msg = insp.connect()
                try:
                    insp.disconnect()
                except Exception:
                    pass
                return ok, msg
            except Exception as e:
                if connection_mode == 'jdbc':
                    return False, f'JDBC 连接失败: {e}'
                # auto 模式：回退 ODBC
        # odbc 模式 或 auto 回退
        return test_sqlserver_connection(host, port, user, password, database)
    except Exception as e:
        return False, f'test_sqlserver_jdbc_connection 异常: {e}'


def test_plugin_connection(db_type, host, port, user, password, **kwargs):
    """
    测试插件数据库类型的连接
    
    参数:
        db_type: 数据库类型（插件定义的db_type）
        host: 主机地址
        port: 端口
        user: 用户名
        password: 密码
        **kwargs: 其他参数（如service_name, sysdba等）
    
    返回:
        (ok, msg) - ok为True表示连接成功
    """
    try:
        from modules.pluginkit.loader import discover_plugins
        plugins = discover_plugins()
        
        # 查找匹配的插件
        plugin_info = None
        for p in plugins:
            if p.get('enabled') and p.get('db_type') == db_type:
                plugin_info = p
                break
        
        if not plugin_info:
            return False, f'插件 {db_type} 未启用或不存在'
        
        # 动态导入插件模块
        import importlib.util
        plugin_dir = plugin_info.get('path')
        if not plugin_dir or not os.path.exists(plugin_dir):
            return False, f'插件目录不存在: {plugin_dir}'
        
        # 插件主文件应该是 main_plugin.py
        plugin_path = os.path.join(plugin_dir, 'main_plugin.py')
        if not os.path.exists(plugin_path):
            return False, f'插件主文件不存在: {plugin_path}'
        
        # 导入插件模块
        spec = importlib.util.spec_from_file_location("plugin_module", plugin_path)
        if spec is None:
            return False, f'无法加载插件模块: {plugin_path}'
        
        plugin_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin_module)
        
        # 从插件信息中获取主类名（优先使用 plugin.json 中的 main_class 字段）
        main_class_name = plugin_info.get('main_class')
        
        # 检查插件是否提供测试连接函数
        if hasattr(plugin_module, 'test_connection'):
            # 调用插件的测试连接函数
            return plugin_module.test_connection(host, port, user, password, **kwargs)
        else:
            # 插件没有提供测试连接函数，尝试创建插件类的实例并连接
            # 优先使用 plugin.json 中的 main_class 字段
            plugin_class = None
            if main_class_name:
                if hasattr(plugin_module, main_class_name):
                    plugin_class = getattr(plugin_module, main_class_name)
            
            # 如果 plugin.json 中没有 main_class 字段，则使用查找逻辑
            if plugin_class is None:
                for attr_name in dir(plugin_module):
                    attr = getattr(plugin_module, attr_name)
                    if isinstance(attr, type) and hasattr(attr, '__init__'):
                        # 检查是否是插件主类，排除基类
                        if attr_name == 'BaseInspectionEngine':
                            continue
                        if 'Inspector' in attr_name or 'Plugin' in attr_name or 'Inspection' in attr_name:
                            plugin_class = attr
                            break
            
            if plugin_class is None:
                return False, f'插件 {db_type} 没有提供测试连接函数，也没有找到插件主类'
            
            # 创建插件实例并测试连接
            # 这里假设插件类的构造函数接受(host, port, user, password, database)参数
            service_name = kwargs.get('service_name', '')
            try:
                instance = plugin_class(host, port, user, password, service_name)
                if hasattr(instance, 'connect'):
                    ok, msg = instance.connect()
                    return ok, msg
                else:
                    return False, f'插件 {db_type} 的主类没有 connect 方法'
            except Exception as e:
                return False, f'插件 {db_type} 连接测试失败: {e}'
    
    except ImportError as e:
        return False, f'插件加载失败: {e}'
    except Exception as e:
        return False, f'插件连接测试失败: {e}'


# ═══════════════════════════════════════════════════════════════════════════
#  连接测试注册表（真插件化基础设施）
#  把 /api/test_db 与 /api/pro/datasources/test-connection 两处 elif 链统一为
#  "注册表 + 插件 connect_test 字段" 查表。新增复用已有协议的插件只需在
#  plugin.json 写 connect_test: "mysql"/"pg"/...，无需在此改 elif。
# ═══════════════════════════════════════════════════════════════════════════
from modules.driver_registry import JDBC_PLUGIN_TO_CATALOG
from modules.db_types.dbtype_registry import (
    CONNECTION_TESTERS,
    get_db_meta,
    resolve_connection_tester,
    load_all_db_types,
    register_connection_tester,
)


def _conn_ok(flavor):
    """按路由风格返回成功响应（regular 用 msg，pro 用 message）。"""
    if flavor == 'pro':
        return {'ok': True, 'message': '连接成功'}
    return {'ok': True, 'msg': '连接成功'}


def _parse_plugin_conn_result(db_type, ok, msg):
    """调用插件实例的 parse_connection_result()（无侵入式架构），返回附加字段。"""
    extra = {}
    try:
        from modules.pluginkit.loader import get_plugin_instance
        plugin = get_plugin_instance(db_type)
        if plugin and hasattr(plugin, 'parse_connection_result'):
            r = plugin.parse_connection_result(ok, msg)
            if r and isinstance(r, dict):
                extra.update(r)
    except Exception as e:
        app.logger.warning(f"调用插件 {db_type} 的 parse_connection_result() 失败: {e}")
    return extra


# —— 各 db_type 连接测试处理器（flavor 感知：'regular' / 'pro'） —— #
def _ct_mysql(data, flavor):
    if flavor == 'regular':
        ok, msg = test_mysql_connection(data['host'], data['port'], data['user'],
                                        data['password'], data.get('database'))
        return {'ok': ok, 'msg': msg}
    import pymysql
    _db = data.get('database') or None
    _kw = dict(host=data['host'], port=data['port'], user=data['user'],
               password=data['password'], connect_timeout=10)
    if _db:
        _kw['database'] = _db
    pymysql.connect(**_kw).close()
    return _conn_ok('pro')


def _ct_mariadb(data, flavor):
    """MariaDB 连接测试：regular 走 MariaDB JDBC 子进程（jdbc:mariadb:// +
    org.mariadb.jdbc.Driver，类型 'mariadb'）；pro 走 pymysql 直连。

    历史 bug：曾复用 _ct_mysql → 测试连接被路由到 MySQL 链路（jdbc:mysql:// +
    mysql catalog），登记了 MariaDB 专用驱动的用户会误报「mysql 驱动未找到」。
    """
    if flavor == 'regular':
        ok, msg = test_mariadb_connection(data['host'], data['port'], data['user'],
                                          data['password'], data.get('database'),
                                          data.get('driver_version', '') or '')
        return {'ok': ok, 'msg': msg}
    import pymysql
    _db = data.get('database') or None
    _kw = dict(host=data['host'], port=data['port'], user=data['user'],
               password=data['password'], connect_timeout=10)
    if _db:
        _kw['database'] = _db
    pymysql.connect(**_kw).close()
    return _conn_ok('pro')


def _ct_oceanbase(data, flavor):
    _ob_user = data['user'] + '@' + data['tenant'] if data.get('tenant') else data['user']
    if flavor == 'regular':
        ok, msg = test_oceanbase_connection(data['host'], data['port'], _ob_user,
                                            data['password'], data.get('database'),
                                            data.get('driver_version', '') or '')
        return {'ok': ok, 'msg': msg}
    import pymysql
    _db = data.get('database') or 'sys'
    pymysql.connect(host=data['host'], port=data['port'], user=_ob_user,
                    password=data['password'], database=_db, connect_timeout=10).close()
    return _conn_ok('pro')


def _ct_pg(data, flavor, db_default='postgres'):
    # 注意：data.get('database') 可能是空串/None（前端未填），必须 or db_default 兜底，
    # 否则子进程测试函数里 '' or 'postgres' 会退到 PG 默认库，误报 database not exist。
    _db = data.get('database') or db_default
    if flavor == 'regular':
        ok, msg = test_pg_connection(data['host'], data['port'], data['user'],
                                     data['password'], _db, data.get('driver_version', '') or '')
        return {'ok': ok, 'msg': msg}
    import psycopg2
    psycopg2.connect(host=data['host'], port=data['port'], user=data['user'],
                     password=data['password'], dbname=_db, connect_timeout=10).close()
    return _conn_ok('pro')


def _ct_ivorysql(data, flavor):
    """IvorySQL 连接测试：regular 走 IvorySQL JDBC 子进程（jdbc:postgresql:// + PG 驱动，
    类型 'ivorysql' 保证 catalog 与默认库名正确）；pro 走 psycopg2 直连。"""
    _db = data.get('database') or 'ivorysql'
    if flavor == 'regular':
        ok, msg = test_ivorysql_connection(data['host'], data['port'], data['user'],
                                           data['password'], _db, data.get('driver_version', '') or '')
        return {'ok': ok, 'msg': msg}
    import psycopg2
    psycopg2.connect(host=data['host'], port=data['port'], user=data['user'],
                     password=data['password'], dbname=_db, connect_timeout=10).close()
    return _conn_ok('pro')


def _ct_kingbase(data, flavor):
    """KingbaseES 连接测试：regular 走 KingbaseES JDBC 子进程（jdbc:kingbase8:// +
    com.kingbase8.jdbc.Driver，类型 'kingbase'）；pro 走 psycopg2 直连。

    历史 bug：曾复用 _ct_pg → regular 分支硬编码 test_pg_connection，把 Kingbase
    测试连接路由到 PostgreSQL 链路（PG 驱动 + 默认库 postgres），Kingbase 服务器
    无 postgres 库即报 FATAL: database "postgres" does not exist。
    """
    _db = data.get('database') or 'kingbase'
    if flavor == 'regular':
        ok, msg = test_kingbase_connection(data['host'], data['port'], data['user'],
                                           data['password'], _db, data.get('driver_version', '') or '')
        return {'ok': ok, 'msg': msg}
    import psycopg2
    psycopg2.connect(host=data['host'], port=data['port'], user=data['user'],
                     password=data['password'], dbname=_db, connect_timeout=10).close()
    return _conn_ok('pro')


def _normalize_oracle_dsn(dsn, host, port):
    """规范化 Oracle TNS 描述符中的占位符。

    用户常把 DSN 写成 ``(DESCRIPTION=(HOST=host)(PORT=1521)(CONNECT_DATA=...))``，
    其中 ``HOST=host`` 是占位符，需要替换为表单中填写的真实地址。若 DSN 里
    已经写了明确的非占位符地址，则保持不变。
    """
    import re as _re

    if not (dsn and str(dsn).strip().upper().startswith('(DESCRIPTION')):
        return dsn

    def _replace_host(m):
        val = m.group(1).strip().lower()
        if val in ('host', 'localhost', '127.0.0.1', ''):
            return f'(HOST={host})'
        return m.group(0)

    def _replace_port(m):
        val = m.group(1).strip()
        if val.lower() == 'port':
            return f'(PORT={port})'
        try:
            int(val)
        except (TypeError, ValueError):
            return f'(PORT={port})'
        return m.group(0)

    dsn = _re.sub(r'\(HOST\s*=\s*([^)]*)\)', _replace_host, dsn, flags=_re.IGNORECASE)
    dsn = _re.sub(r'\(PORT\s*=\s*([^)]*)\)', _replace_port, dsn, flags=_re.IGNORECASE)
    return dsn


def _ct_oracle_pro(data):
    """复刻原 /api/pro/datasources/test-connection 中 oracle 分支（含 SSH / thick mode）。

    修复点：
    1. 用户填写 DSN 描述符且启用 SSH 时，必须把 DSN 里的 HOST/PORT 替换为 SSH 隧道
       本地端口，否则 oracledb 仍按原 DSN 直连，导致"配了 SSH 仍超时"。
    2. 未启用 SSH 时，DSN 里的占位符（如 HOST=host）也要替换为表单填写的真实地址，
       否则会出现"填了主机地址却仍连 host"的误导性超时。
    3. 隧道本地监听 127.0.0.1，DSN 用 127.0.0.1 避免 localhost 被解析到 IPv6。
    4. 错误提示区分"未配 SSH"、"已配 SSH 但隧道后仍连不上"以及 DSN 占位符问题。
    5. thick mode 失败等异常路径也确保关闭 SSH 隧道。
    """
    import oracledb
    import re as _re

    _jdbc = (data.get('jdbc_url') or '').strip()
    _has_dsn = bool(_jdbc and _jdbc.lstrip().upper().startswith('(DESCRIPTION'))
    if _has_dsn:
        # 先把 DSN 里的占位符（HOST=host / PORT=port）换成表单真实地址
        dsn = _normalize_oracle_dsn(_jdbc, data['host'], int(data['port']))
        # 防御性检查：若还有明显占位符未替换，立即给出明确错误，避免连到不存在的 host
        if _re.search(r'\(HOST\s*=\s*host\s*\)', dsn, flags=_re.IGNORECASE):
            return {'ok': False, 'error': f'DSN 中 HOST 占位符未解析（当前 DSN: {dsn}），请检查连接串或清空 DSN 使用上方主机地址'}
    else:
        dsn = f"{data['host']}:{data['port']}/{data.get('service_name', '')}" if data.get('service_name') \
            else f"{data['host']}:{data['port']}"

    ssh_host = data.get('ssh_host', '')
    _tunnel = None
    try:
        if ssh_host:
            try:
                from modules.ssh import SSHTunnel
                _tunnel = SSHTunnel(
                    ssh_host=ssh_host,
                    ssh_port=int(data.get('ssh_port', 22)),
                    ssh_user=data.get('ssh_user', 'root'),
                    ssh_password=data.get('ssh_password', ''),
                    remote_host=data['host'],
                    remote_port=int(data['port']),
                )
                _tunnel.__enter__()
                _local = _tunnel.local_port
                if _has_dsn:
                    # 把 DSN 中的 HOST/PORT 替换为隧道本地端点，确保流量走 SSH
                    dsn = _re.sub(r'\(HOST\s*=\s*[^)]+\)', '(HOST=127.0.0.1)', dsn, flags=_re.IGNORECASE)
                    dsn = _re.sub(r'\(PORT\s*=\s*\d+\)', f'(PORT={_local})', dsn, flags=_re.IGNORECASE)
                else:
                    dsn = f"127.0.0.1:{_local}/{data.get('service_name', '')}" if data.get('service_name') \
                        else f"127.0.0.1:{_local}"
            except Exception as te:
                return {'ok': False, 'error': f'SSH 隧道建立失败: {te}'}

        params = {"user": data['user'], "password": data['password'], "dsn": dsn,
                  "tcp_connect_timeout": 15}
        if data.get('sysdba'):
            params["mode"] = oracledb.SYSDBA

        def _try_connect():
            try:
                conn = oracledb.connect(**params)
            except TypeError as te:
                # 极个别 oracledb 旧版本不认 tcp_connect_timeout，去掉后重试（子进程超时仍兜底）
                if 'tcp_connect_timeout' in str(te):
                    params.pop('tcp_connect_timeout', None)
                    conn = oracledb.connect(**params)
                else:
                    raise
            conn.close()

        try:
            _try_connect()
            return {'ok': True, 'message': '连接成功'}
        except Exception as e:
            err_msg = str(e)
            if 'DPY-3010' in err_msg or 'DPY-3015' in err_msg:
                _thick_ok = False
                try:
                    oracledb.init_oracle_client()
                    _thick_ok = True
                except Exception:
                    pass
                if not _thick_ok:
                    _lib_dir = _find_oracle_client_lib_dir()
                    if _lib_dir:
                        try:
                            oracledb.init_oracle_client(lib_dir=_lib_dir)
                            _thick_ok = True
                        except Exception:
                            pass
                if not _thick_ok:
                    try:
                        import json
                        with open(os.path.join(BASE_DIR, 'dbc_config.json')) as f:
                            _cfg = json.load(f)
                        _lib_dir = _cfg.get('oracle_client_lib_dir', '')
                        if _lib_dir and os.path.isdir(_lib_dir):
                            oracledb.init_oracle_client(lib_dir=_lib_dir)
                            _thick_ok = True
                    except Exception:
                        pass
                if not _thick_ok:
                    return {'ok': False, 'error': 'Oracle 11g 及以下版本需要 Oracle Instant Client。'
                                                '请通过左侧导航"Oracle Client"设置页，点击"一键下载并安装"按钮自动下载安装。'}
                try:
                    _try_connect()
                    return {'ok': True, 'message': '连接成功'}
                except Exception as e2:
                    return {'ok': False, 'error': f'Oracle 连接失败（thick mode）: {e2}'}
            elif 'unexpected keyword argument' in err_msg.lower() or type(e).__name__ == 'TypeError':
                # 驱动连接参数错误（如传入了 oracledb 不认识的 kwarg），不应误判为「连接超时」
                return {'ok': False, 'error': f'Oracle 驱动连接参数错误: {type(e).__name__}: {err_msg[:300]}'}
            elif 'timed out' in err_msg.lower() or 'timeout' in err_msg.lower():
                # 把实际用于连接的 DSN/地址 + oracledb 原始异常暴露出来，方便排查：
                # 真 TCP 超时（DPY-6001/ORA-12170）vs 握手/权限问题被误判为超时（如 SYSDBA+服务名）
                _safe_dsn = _re.sub(r'(PASSWORD\s*=\s*)[^)]+', r'\1***', str(dsn),
                                    flags=_re.IGNORECASE) if isinstance(dsn, str) else str(dsn)
                _detail = f'（原始异常: {type(e).__name__}: {err_msg[:400]}）'
                if ssh_host:
                    return {'ok': False, 'error': f'连接超时，SSH 隧道已建立但无法访问 Oracle（实际 DSN: {_safe_dsn}）{_detail}，'
                                                f'请检查数据库监听地址、Service Name/SID 及防火墙'}
                return {'ok': False, 'error': f'连接超时，Oracle 可能无法直连（实际 DSN: {_safe_dsn}）{_detail}，'
                                              f'请在数据源中配置 SSH，或确认上方主机地址/端口/服务名/SYSDBA 是否正确'}
            else:
                return {'ok': False, 'error': str(e)}
    finally:
        if _tunnel:
            _tunnel.close()


def _ct_oracle(data, flavor):
    """Oracle 连接测试 —— 走子进程隔离。

    oracledb thin 模式与 gevent monkey-patch 不兼容（连接会挂死），且启用 Instant
    Client 时的 thick/OCI 原生库同样会在被 patch 的主进程里钉死 hub。无论 regular
    还是 pro、是否配置 SSH，统一在干净子进程内执行：子进程复用 ``_ct_oracle_pro``
    处理 SSH 隧道与 thick 回退，杜绝在主进程冻结整个界面（同 hgdb/db2 的修复思路）。
    """
    _kwargs = dict(
        service_name=data.get('service_name', '') or '',
        sysdba=bool(data.get('sysdba', False)),
        jdbc_url=data.get('jdbc_url') or None,
        ssh_host=data.get('ssh_host', '') or '',
        ssh_port=int(data.get('ssh_port', 22) or 22),
        ssh_user=data.get('ssh_user', '') or 'root',
        ssh_password=data.get('ssh_password', '') or '',
    )
    ok, msg = run_jdbc_test_subprocess('oracle', data, _kwargs)
    return _jdbc_conn_result(ok, msg, flavor, 'Oracle')


def _ct_dm(data, flavor):
    _dv = data.get('driver_version', '') or ''
    if flavor == 'regular':
        ok, msg = test_dm_connection(data['host'], data['port'], data['user'],
                                     data['password'], _dv)
        return {'ok': ok, 'msg': msg}
    # pro flavor：优先 JDBC（免达梦原生客户端），回退 dmPython
    try:
        from modules.jdbc_test_cli import _find_dm_jdbc_jar
        _jar = _find_dm_jdbc_jar()
    except Exception:  # noqa: BLE001
        _jar = None
    if _jar:
        ok, msg = run_jdbc_test_subprocess('dm', data)
        # 前端数据源测试读取 data.msg；必须用 msg 携带 JDBC 驱动信息，
        # 不能用 _conn_ok('pro')（其返回 message='连接成功'，会被前端丢弃）。
        if ok:
            return {'ok': True, 'msg': msg}
        return {'ok': False, 'error': msg}
    import dmPython
    try:
        conn = dmPython.connect(server=data['host'], port=int(data['port']),
                                 user=data['user'], password=data['password'])
        conn.close()
    except Exception as de:
        if 'exception set' in str(de):
            return {'ok': False, 'error': f'达梦连接失败，请检查用户名（默认SYSDBA）、密码和端口（{data["host"]}:{data["port"]}）'}
        raise
    return _conn_ok('pro')


def _ct_sqlserver(data, flavor):
    if flavor == 'regular':
        ok, msg = test_sqlserver_connection(data['host'], data['port'], data['user'],
                                            data['password'], data.get('database', 'master'))
        return {'ok': ok, 'msg': msg}
    import pyodbc
    conn_str = _build_sqlserver_conn_str(data['host'], data['port'], data['user'],
                                         data['password'], timeout=10)
    pyodbc.connect(conn_str).close()
    return _conn_ok('pro')


def _ct_sqlserver_jdbc(data, flavor):
    """SQL Server (JDBC) 连接测试：按 connection_mode 透传到双轨测试方法。

    缺省 / 空串 → 'jdbc'（本类型语义即 JDBC；旧实例无该字段时向后兼容）。
    """
    _mode = (data.get('connection_mode') or 'jdbc').strip().lower()
    _host = data['host']
    _port = int(data['port'])
    _db = data.get('database', 'master') or 'master'

    def _odbc():
        """ODBC 路径不涉及 JVM，可留在主进程（pyodbc 自带 timeout=10）。"""
        return test_sqlserver_jdbc_connection(
            _host, _port, data['user'], data['password'], _db,
            {'connection_mode': 'odbc'})

    if _mode == 'odbc':
        ok, msg = _odbc()
        return _jdbc_conn_result(ok, msg, flavor, 'SQL Server (JDBC)')

    # jdbc / auto：JDBC 部分必须隔离到子进程，否则 JVM 会冻结整个 gevent 服务
    _kwargs = dict(database=_db,
                   jdbc_url=data.get('jdbc_url') or None,
                   instance_name=data.get('instance_name') or '',
                   encrypt=bool(data.get('encrypt', False)),
                   trust_server_certificate=bool(data.get('trust_server_certificate', True)),
                   driver_version=data.get('driver_version') or None)
    ok, msg = run_jdbc_test_subprocess('sqlserver_jdbc', data, _kwargs)
    if not ok and _mode == 'auto':
        ok2, msg2 = _odbc()
        if ok2:
            return _jdbc_conn_result(True, msg2, flavor, 'SQL Server (JDBC)')
    return _jdbc_conn_result(ok, msg, flavor, 'SQL Server (JDBC)')


def _ct_tidb(data, flavor):
    if flavor == 'regular':
        ok, msg = test_tidb_connection(data['host'], data['port'], data['user'],
                                       data['password'], data.get('database'), data.get('driver_version', '') or '')
        return {'ok': ok, 'msg': msg}
    import pymysql
    _db = data.get('database') or None
    _kw = dict(host=data['host'], port=data['port'], user=data['user'],
               password=data['password'], connect_timeout=10)
    if _db:
        _kw['database'] = _db
    pymysql.connect(**_kw).close()
    return _conn_ok('pro')


def _ct_yashandb(data, flavor):
    if flavor == 'regular':
        ok, msg = test_yashandb_connection(data['host'], data['port'], data['user'],
                                           data['password'], data.get('database') or 'yashandb',
                                           data.get('driver_version', '') or '')
        return {'ok': ok, 'msg': msg}
    try:
        import yasdb
        yasdb.connect(host=data['host'], port=int(data['port']), user=data['user'],
                      password=data['password']).close()
    except ImportError as e:
        return {'ok': False, 'error': f'yasdb 驱动未安装: {str(e)}'}
    return _conn_ok('pro')


def _ct_gbase(data, flavor):
    _dv = data.get('driver_version', '') or ''
    _gserver = data.get('gbase_server_name', 'gbase01') or 'gbase01'
    _db = data.get('database', 'testdb') or 'testdb'
    if flavor == 'regular':
        ok, msg = test_gbase_connection(data['host'], data['port'], data['user'], data['password'],
                                        _db, _gserver, _dv)
        return {'ok': ok, 'msg': msg}
    # pro flavor：与 dm/sqlserver_jdbc 一样，把 JVM 隔离到子进程，避免钉死 gevent hub。
    # 同时把 driver_version / database / server 透传给 jdbc_test_cli._test_gbase_jdbc。
    _ok, _msg = run_jdbc_test_subprocess('gbase', {
        'host': data['host'], 'port': data['port'],
        'user': data['user'], 'password': data['password'],
    }, extra_kwargs={
        'database': _db,
        'gbase_server_name': _gserver,
        'driver_version': _dv,
    })
    if not _ok:
        return {'ok': False, 'error': _msg}
    return {'ok': True, 'msg': _msg}


def _ct_redis(data, flavor):
    if flavor == 'regular':
        ok, msg = test_plugin_connection(data['db_type'], data['host'], data['port'], data['user'],
                                         data['password'], database=data.get('database', ''),
                                         seed_nodes=data.get('seed_nodes', ''))
        if ok is not None:
            return {'ok': ok, 'msg': msg}
        return {'ok': False, 'msg': _t('webui.err_unknown_db_type')}
    ok, msg = test_plugin_connection(data['db_type'], data['host'], data['port'], data['user'],
                                     data['password'], database=data.get('database', ''),
                                     seed_nodes=data.get('seed_nodes', ''))
    if ok:
        return {'ok': True, 'message': msg}
    return {'ok': False, 'error': msg} if msg else \
        {'ok': False, 'error': f"不支持的数据库类型: {data['db_type']}"}


def _ct_mongodb(data, flavor):
    _kwargs = dict(
        database=data.get('database', 'admin'),
        connect_mode=data.get('connect_mode', 'standard'),
        auth_source=data.get('auth_source', 'admin'),
        auth_mechanism=data.get('auth_mechanism', ''),
        replica_set=data.get('replica_set', ''),
        tls=bool(data.get('tls', False)),
        tls_ca_file=data.get('tls_ca_file', ''),
        tls_cert_key_file=data.get('tls_cert_key_file', ''),
        tls_allow_invalid_certs=bool(data.get('tls_allow_invalid_certs', False)),
    )
    if flavor == 'regular':
        ok, msg = test_plugin_connection(data['db_type'], data['host'], data['port'], data['user'],
                                         data['password'], **_kwargs)
        if ok is not None:
            result = {'ok': ok, 'msg': msg}
            result.update(_parse_plugin_conn_result(data['db_type'], ok, msg))
            return result
        return {'ok': False, 'msg': _t('webui.err_unknown_db_type')}
    ok, msg = test_plugin_connection(data['db_type'], data['host'], data['port'], data['user'],
                                     data['password'], **_kwargs)
    if ok:
        return {'ok': True, 'message': msg}
    return {'ok': False, 'error': msg} if msg else \
        {'ok': False, 'error': f"不支持的数据库类型: {data['db_type']}"}


# ══════════════════════════════════════════════════════════════════
# JDBC 连接测试：子进程隔离
# ══════════════════════════════════════════════════════════════════
# 【问题】点「测试连接」后整个 Web 界面卡死（HGDB / DB2 / SQL Server(JDBC)）。
# 【根因】两层叠加：
#   1) Web 服务默认跑在 gevent 协作式服务器上（_socketio_async_mode='gevent'，
#      见本文件 _resolve_async_mode）。gevent 是单线程协程模型，且本进程**没有**
#      执行 monkey.patch_all()，因此任何不主动让出执行权的调用都会把整个 hub
#      钉死——不只是当前请求转圈，而是所有 HTTP/WebSocket 请求全部停摆，
#      用户观感就是「整个界面卡死」。
#   2) 这三类数据源都通过 JPype 在**当前进程内**启动 JVM 再调
#      DriverManager.getConnection。JVM 启动本身要数秒，且跑在原生 OS 线程上，
#      连接阶段完全在 C/Java 层阻塞，Python 层既无法让出也无法中断。
#      IvorySQL 之所以不卡，是因为它走原生 psycopg2 且带 connect_timeout=10，
#      正常几十毫秒就返回，用户感知不到。
# 【解法】把 JVM 彻底赶出主进程：
#   - 主进程只负责 spawn 子进程（同一个 exe 加 --jdbc-test-cli）+ 协作式轮询等待；
#   - 等待期间用 gevent.sleep 让出执行权 → 界面全程可用；
#   - 超时直接杀子进程树，JVM 随之消失，主进程不受任何残留影响。
JDBC_SUBPROCESS_DB_TYPES = ('hgdb', 'db2', 'sqlserver_jdbc', 'oracle_jdbc', 'oracle', 'ivorysql', 'pg', 'kingbase', 'yashandb', 'mysql', 'mariadb', 'tidb', 'oceanbase')
JDBC_TEST_TIMEOUT = 30  # 秒；需覆盖 JVM 冷启动(3~10s) + JDBC 登录超时(10~15s)

# 需要整条巡检任务隔离到子进程的数据库类型（均依赖进程内 JVM/JPype）。
# 与 driver_registry.JDBC_PLUGIN_TO_CATALOG 对齐：6 个 JDBC 插件 + 核心内置 dm/gbase/ivorysql，
# 任何新增 JDBC 类型必须同步加入，否则主进程内启 JVM 会钉死 gevent hub。
JVM_INSPECTION_DB_TYPES = ('hgdb', 'db2', 'sqlserver_jdbc', 'oracle_jdbc', 'dm', 'gbase', 'clickhouse', 'uxdb', 'ivorysql', 'pg', 'kingbase', 'yashandb', 'mysql', 'mariadb', 'tidb', 'oceanbase')
JDBC_INSPECTION_TIMEOUT = 3600  # 巡检任务整体硬超时（秒）


def _cooperative_sleep(seconds):
    """让出执行权且不冻结 gevent hub。

    gevent 模式下用 gevent.sleep（会切回 hub 处理其它请求）；其余模式退化为
    time.sleep。注意本进程未 monkey-patch，time.sleep 会真实阻塞 hub，
    因此这个分支判断不能省。
    """
    if _socketio_async_mode == 'gevent':
        try:
            import gevent as _gv
            _gv.sleep(seconds)
            return
        except Exception:
            pass
    time.sleep(seconds)


def _kill_process_tree(proc):
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


def _jdbc_cli_command():
    """返回启动隔离 CLI 的命令行。

    冻结态：``<dbcheck.exe> --jdbc-test-cli``（web_ui.py 在单实例锁之前拦截）；
    开发态：``<python> modules/jdbc_test_cli.py``。
    """
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--jdbc-test-cli']
    return [sys.executable, os.path.join(str(PROJECT_ROOT), 'modules', 'jdbc_test_cli.py')]


def run_jdbc_test_subprocess(db_type, data, extra_kwargs=None, timeout=JDBC_TEST_TIMEOUT):
    """在独立进程中执行一次 JDBC 连接测试。

    Args:
        db_type: 'hgdb' | 'db2' | 'sqlserver_jdbc'
        data: 前端提交的连接参数 dict（host/port/user/password ...）
        extra_kwargs: 透传给插件 test_connection 的关键字参数
        timeout: 硬超时（秒），超时即杀进程树

    Returns:
        (ok: bool, msg: str)
    """
    import subprocess as _sp
    import tempfile as _tf
    from modules.jdbc_test_cli import RESULT_PREFIX

    payload = {
        'db_type': db_type,
        'host': data.get('host'),
        'port': data.get('port'),
        'user': data.get('user'),
        'password': data.get('password'),
        'kwargs': extra_kwargs or {},
    }

    # 参数走 stdin 临时文件而非命令行：避免密码出现在进程列表/任务管理器里；
    # 输出走临时文件而非管道：管道满会阻塞子进程，且读管道会阻塞 gevent hub。
    _in_fd, _in_path = _tf.mkstemp(prefix='dbc_jdbc_in_', suffix='.json')
    _out_fd, _out_path = _tf.mkstemp(prefix='dbc_jdbc_out_', suffix='.log')
    os.close(_out_fd)
    proc = None
    try:
        with os.fdopen(_in_fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=True)

        env = os.environ.copy()
        env['DBCheck_NO_GEVENT_PATCH'] = '1'   # 子进程绝不能被 monkey-patch
        env['PYTHONIOENCODING'] = 'utf-8'

        _kw = {}
        if os.name == 'nt':
            # CREATE_NO_WINDOW：冻结版是控制台程序，不加会闪黑框
            _kw['creationflags'] = (getattr(_sp, 'CREATE_NO_WINDOW', 0x08000000)
                                    | getattr(_sp, 'CREATE_NEW_PROCESS_GROUP', 0x00000200))
        else:
            _kw['start_new_session'] = True  # 独立进程组，便于整树 kill

        with open(_in_path, 'r', encoding='utf-8') as fin, \
                open(_out_path, 'w', encoding='utf-8', errors='replace') as fout:
            proc = _sp.Popen(_jdbc_cli_command(), stdin=fin, stdout=fout,
                             stderr=_sp.STDOUT, env=env, cwd=str(PROJECT_ROOT), **_kw)
            # 注册到活跃子进程表：主进程退出时整树清理（同步等待中遇 Ctrl+C 同样要杀）
            with _ACTIVE_PROCS_LOCK:
                _ACTIVE_PROCS.add(proc)

            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if time.monotonic() >= deadline:
                    _kill_process_tree(proc)
                    return False, (f'连接测试超时（已超过 {timeout} 秒）。'
                                   f'请检查主机地址、端口与网络连通性，'
                                   f'并确认 JDBC 驱动 jar 与 Java 运行环境已就绪。')
                _cooperative_sleep(0.05)

        # 子进程可能在写完结果后才退出，输出文件此时已完整
        try:
            with open(_out_path, 'r', encoding='utf-8', errors='replace') as f:
                out = f.read()
        except Exception:
            out = ''

        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith(RESULT_PREFIX):
                try:
                    r = json.loads(line[len(RESULT_PREFIX):])
                    return bool(r.get('ok')), str(r.get('msg') or '')
                except Exception as e:  # noqa: BLE001
                    return False, f'连接测试结果解析失败: {e}'

        tail = (out or '').strip().splitlines()[-5:]
        return False, ('连接测试子进程未返回结果'
                       + (f'：{" | ".join(tail)}' if tail else f'（退出码 {proc.returncode}）'))
    except Exception as e:  # noqa: BLE001
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc)
        return False, f'连接测试子进程启动失败: {e}'
    finally:
        with _ACTIVE_PROCS_LOCK:
            if proc is not None:
                _ACTIVE_PROCS.discard(proc)
        for _p in (_in_path, _out_path):
            try:
                os.remove(_p)
            except Exception:
                pass


def _insp_cli_command():
    """返回启动隔离巡检 CLI 的命令行（复用与 JDBC 测试 CLI 相同模式）。"""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--jdbc-inspection-cli']
    return [sys.executable, os.path.join(str(PROJECT_ROOT), 'modules', 'jdbc_inspection_cli.py')]


def _run_inspection_subprocess(task_id, db_type, db_info, inspector_name,
                               template_id=None, chapter_ids=None,
                               timeout=JDBC_INSPECTION_TIMEOUT):
    """在独立子进程中执行整条 JDBC 巡检任务。

    主进程只负责 spawn 子进程、读取 stdout 事件行并通过 socketio 转发给前端；
    等待期间用协作式睡眠让出 gevent hub，界面始终可响应。JVM 只活在子进程里，
    即使原生线程死锁，主进程也不受影响，且可在超时时直接杀进程树。

    Args:
        task_id: 任务 UUID
        db_type: 数据库类型，需在 JVM_INSPECTION_DB_TYPES 中
        db_info: 连接信息 dict（同 api_start_inspection 构造）
        inspector_name: 巡检人名称
        template_id: 模板 ID
        chapter_ids: 章节 ID 列表或 None
        timeout: 硬超时秒数，默认 1 小时
    """
    import subprocess as _sp
    import tempfile as _tf
    from modules.jdbc_inspection_cli import RESULT_PREFIX, DONE_PREFIX

    payload = {
        'task_id': task_id,
        'db_type': db_type,
        'db_info': db_info,
        'inspector_name': inspector_name,
        'template_id': template_id,
        'chapter_ids': chapter_ids,
    }

    _in_fd, _in_path = _tf.mkstemp(prefix='dbc_jdbc_insp_in_', suffix='.json')
    _out_fd, _out_path = _tf.mkstemp(prefix='dbc_jdbc_insp_out_', suffix='.log')
    os.close(_out_fd)
    proc = None

    def _forward_event(event, data):
        """把子进程事件转发到前端并写入本地任务日志。"""
        try:
            socketio.emit(event, data, room=task_id)
        except Exception:
            pass
        task = tasks.get(task_id)
        if task is not None:
            if event == 'log' and isinstance(data, dict):
                msg = data.get('msg', '')
                if msg:
                    task.setdefault('log', []).append(msg)
            elif event == 'done':
                task['status'] = 'done'
                task.setdefault('report_path', data.get('report_path'))
                task.setdefault('ai_advice', data.get('ai_advice'))
            elif event == 'error':
                task['status'] = 'error'
                task['error_msg'] = data.get('msg', '')

    try:
        with os.fdopen(_in_fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=True)

        env = os.environ.copy()
        env['DBCheck_NO_GEVENT_PATCH'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'

        _kw = {}
        if os.name == 'nt':
            _kw['creationflags'] = (getattr(_sp, 'CREATE_NO_WINDOW', 0x08000000)
                                    | getattr(_sp, 'CREATE_NEW_PROCESS_GROUP', 0x00000200))
        else:
            _kw['start_new_session'] = True

        with open(_in_path, 'r', encoding='utf-8') as fin, \
                open(_out_path, 'w', encoding='utf-8', errors='replace') as fout:
            proc = _sp.Popen(_insp_cli_command(), stdin=fin, stdout=fout,
                             stderr=_sp.STDOUT, env=env, cwd=str(PROJECT_ROOT), **_kw)
            # 注册到活跃子进程表：主进程 Ctrl+C 退出时整树清理，防 JVM 孤儿残留
            with _ACTIVE_PROCS_LOCK:
                _ACTIVE_PROCS.add(proc)

        # ── 流式转发：轮询期间增量读取临时文件，实时把日志推给前端 ──
        # 之前是「等子进程退出后整文件读取」，导致前端日志全部堆积到巡检结束才弹出。
        # 子进程每行都 flush()，临时文件随巡检推进实时增长；这里每 0.1s 增量读取
        # 新增行并转发，日志即可与后端执行同步显示，而非结束时一次性爆发。
        done_seen = False
        _processed_lines = 0

        def _handle_line(_ln):
            nonlocal done_seen
            _ln = _ln.strip()
            if not _ln:
                return
            if _ln.startswith(RESULT_PREFIX):
                try:
                    _ev = json.loads(_ln[len(RESULT_PREFIX):])
                    _forward_event(_ev.get('event'), _ev.get('data'))
                    if _ev.get('event') == 'done':
                        done_seen = True
                except Exception:
                    pass
            elif _ln.startswith(DONE_PREFIX):
                try:
                    _final = json.loads(_ln[len(DONE_PREFIX):])
                    _t = tasks.get(task_id)
                    if _t:
                        _t['status'] = _final.get('status', _t.get('status', 'done'))
                        _t['report_path'] = _final.get('report_path') or _t.get('report_path')
                        _t['ai_advice'] = _final.get('ai_advice') or _t.get('ai_advice')
                        _t['error_msg'] = _final.get('error_msg') or _t.get('error_msg')
                        # 子进程内存独立，以下字段必须由结束行同步回主进程，
                        # 否则 /api/task_status 返回空 result → 前端健康评分 0、结果不展示。
                        if _final.get('result') is not None:
                            _t['result'] = _final['result']
                        if _final.get('auto_analyze') is not None:
                            _t['auto_analyze'] = _final['auto_analyze']
                        if _final.get('report_file'):
                            _t['report_file'] = _final['report_file']
                        if _final.get('report_name'):
                            _t['report_name'] = _final['report_name']
                except Exception:
                    pass

        def _drain_new():
            nonlocal _processed_lines
            try:
                with open(_out_path, 'r', encoding='utf-8', errors='replace') as _f:
                    _text = _f.read()
            except Exception:
                return  # 子进程仍持有文件写入句柄，偶发共享冲突时下一轮重试
            # 始终丢弃末段元素：它要么是结尾空串（整文件以 \n 结束），
            # 要么是尚未写完的半行；真正的完整行都在它之前，避免漏转发/重复。
            _parts = _text.split('\n')[:-1]
            for _ln in _parts[_processed_lines:]:
                _handle_line(_ln)
            _processed_lines = len(_parts)

        deadline = time.monotonic() + timeout
        while True:
            _drain_new()
            if done_seen:
                break
            if proc.poll() is not None:
                _drain_new()  # 子进程已退出：再收尾读取一次确保不丢尾部日志
                break
            if time.monotonic() >= deadline:
                _kill_process_tree(proc)
                err_msg = (f'巡检任务超时（已超过 {timeout} 秒）。'
                           f'请检查目标数据库是否可连接，或 JVM/JDBC 驱动环境是否正常。')
                _forward_event('error', {'msg': err_msg})
                _forward_event('done', {'msg': err_msg, 'task_id': task_id})
                task = tasks.get(task_id)
                if task:
                    task['status'] = 'error'
                    task['error_msg'] = err_msg
                return
            _cooperative_sleep(0.1)

        if not done_seen:
            err_msg = '巡检子进程未返回结束事件'
            _forward_event('error', {'msg': err_msg})
            _forward_event('done', {'msg': err_msg, 'task_id': task_id})
            task = tasks.get(task_id)
            if task:
                task['status'] = 'error'
                task['error_msg'] = err_msg

    except Exception as e:
        err_msg = f'巡检子进程启动失败: {e}'
        _forward_event('error', {'msg': err_msg})
        _forward_event('done', {'msg': err_msg, 'task_id': task_id})
        task = tasks.get(task_id)
        if task:
            task['status'] = 'error'
            task['error_msg'] = err_msg
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc)
    finally:
        with _ACTIVE_PROCS_LOCK:
            if proc is not None:
                _ACTIVE_PROCS.discard(proc)
        for _p in (_in_path, _out_path):
            try:
                os.remove(_p)
            except Exception:
                pass


def _jdbc_conn_result(ok, msg, flavor, db_type):
    """把 (ok, msg) 归一为两种 flavor 各自的响应结构。"""
    if flavor == 'regular':
        return {'ok': bool(ok), 'msg': msg}
    if ok:
        return {'ok': True, 'message': msg}
    return {'ok': False, 'error': msg or f'{db_type} 连接测试失败'}


def _ct_hgdb(data, flavor):
    """HGDB（瀚高）JDBC 连接测试 —— 走子进程隔离。

    此前 hgdb 未注册测试器，落到 _plugin_conn_fallback 在主进程内起 JVM，
    是「点测试连接界面卡死」的直接来源之一。
    """
    _kwargs = dict(database=data.get('database', '') or '',
                   jdbc_url=data.get('jdbc_url') or None,
                   driver_version=data.get('driver_version') or None)
    ok, msg = run_jdbc_test_subprocess('hgdb', data, _kwargs)
    return _jdbc_conn_result(ok, msg, flavor, 'hgdb')


def _ct_oracle_jdbc(data, flavor):
    """Oracle (JDBC) 连接测试 —— 走子进程隔离。

    与 hgdb/db2/sqlserver_jdbc 同源：oracle_jdbc 依赖 JPype 在进程内启动 JVM，
    若在主进程（已被 gevent monkey-patch）里发起连接，hub 会被原生线程钉死、
    HTTP 响应写不回，表现为「点测试连接后整个界面卡死」。此前 oracle_jdbc 既
    未加入 JDBC_SUBPROCESS_DB_TYPES、也未注册连接测试器，落到插件回退在主进程
    起 JVM，正是本 bug 的直接原因。隔离到干净子进程后即可正常返回。
    """
    _kwargs = dict(
        service_name=data.get('service_name', '') or 'ORCL',
        sysdba=bool(data.get('sysdba', False)),
        jdbc_url=data.get('jdbc_url') or None,
        driver_version=data.get('driver_version') or None,
        use_sid=bool(data.get('use_sid', False)),
    )
    ok, msg = run_jdbc_test_subprocess('oracle_jdbc', data, _kwargs)
    return _jdbc_conn_result(ok, msg, flavor, 'Oracle (JDBC)')


def _ct_clickhouse(data, flavor):
    """ClickHouse (JDBC) 连接测试 —— 走子进程隔离。

    与 oracle_jdbc 同源问题：clickhouse_jdbc 插件依赖 JPype 启 JVM，主进程
    （gevent monkey-patch）内执行会钉死 hub。此前该类型未注册 tester，落到
    _plugin_conn_fallback 的主进程路径 → 点测试连接界面卡死。统一收口子进程。
    """
    _kwargs = dict(database=data.get('database', '') or 'default',
                   jdbc_url=data.get('jdbc_url') or None,
                   ssl=bool(data.get('ssl', False)),
                   driver_version=data.get('driver_version') or None)
    ok, msg = run_jdbc_test_subprocess('clickhouse', data, _kwargs)
    return _jdbc_conn_result(ok, msg, flavor, 'ClickHouse (JDBC)')


def _ct_uxdb(data, flavor):
    """UXDB (JDBC) 连接测试 —— 走子进程隔离（JPype 启 JVM 钉死 gevent hub）。"""
    _kwargs = dict(database=data.get('database', ''),
                   jdbc_url=data.get('jdbc_url') or None,
                   driver_version=data.get('driver_version') or None)
    ok, msg = run_jdbc_test_subprocess('uxdb', data, _kwargs)
    return _jdbc_conn_result(ok, msg, flavor, 'UXDB (JDBC)')


def _ct_db2(data, flavor):
    _kwargs = dict(database=data.get('database', ''),
                   jdbc_url=data.get('jdbc_url') or None,
                   ssl=bool(data.get('ssl', False)),
                   driver_version=data.get('driver_version') or None)
    ok, msg = run_jdbc_test_subprocess('db2', data, _kwargs)
    return _jdbc_conn_result(ok, msg, flavor, 'db2')


def _plugin_conn_fallback(data, flavor):
    """未知/未注册类型的连接测试兜底（复用既有 test_plugin_connection）。"""
    db_type = data.get('db_type')
    _kwargs = dict(service_name=data.get('service_name', ''),
                   sysdba=bool(data.get('sysdba', False)),
                   jdbc_url=data.get('jdbc_url') or None,
                   driver_version=data.get('driver_version') or None)
    # 防御性守卫：任何 *_jdbc 类型若未注册到 CONNECTION_TESTERS，绝不在主进程
    # 调插件的 test_connection——那会在 gevent 进程内 startJVM，原生线程钉死 hub
    # 导致整个 Web 界面卡死。一律降级到子进程隔离测试：jdbc_test_cli 按 db_type
    # 分发，未知类型返回干净的错误 JSON，主进程始终保持响应，绝不冻结。
    if db_type and db_type.endswith('_jdbc') and db_type not in CONNECTION_TESTERS:
        ok, msg = run_jdbc_test_subprocess(db_type, data, _kwargs)
        if flavor == 'regular':
            result = {'ok': ok, 'msg': msg}
            result.update(_parse_plugin_conn_result(db_type, ok, msg))
            return result
        return {'ok': ok, 'message': msg} if ok else {'ok': False, 'error': msg}
    if flavor == 'regular':
        ok, msg = test_plugin_connection(db_type, data['host'], data['port'], data['user'],
                                         data['password'], **_kwargs)
        if ok is not None:
            result = {'ok': ok, 'msg': msg}
            result.update(_parse_plugin_conn_result(db_type, ok, msg))
            return result
        return {'ok': False, 'msg': _t('webui.err_unknown_db_type')}
    ok, msg = test_plugin_connection(db_type, data['host'], data['port'], data['user'],
                                     data['password'], **_kwargs)
    if ok:
        return {'ok': True, 'message': msg}
    return {'ok': False, 'error': msg} if msg else \
        {'ok': False, 'error': f"不支持的数据库类型: {db_type}"}


def _dispatch_conn_test(db_type, data, flavor):
    """统一连接测试分发：注册表 -> 插件 connect_test 复用 -> 兜底。

    返回响应 dict；若所有路径都无法处理返回 None（调用方应返回未知类型错误）。
    """
    tester = CONNECTION_TESTERS.get(db_type)
    if tester:
        return tester(data, flavor)
    meta = get_db_meta(db_type)
    rt = resolve_connection_tester(meta) if meta else None
    if rt:
        return rt(data, flavor)
    return _plugin_conn_fallback(data, flavor)


def _conn_test_with_timeout(db_type, data, flavor, timeout=None):
    """在独立 OS 线程中执行连接测试，规避 C 层 DB 驱动（oracledb / dmPython /
    psycopg2 / pyodbc）在 gevent 模式下阻塞事件循环 hub，导致响应写不回、
    前端报 `Failed to fetch` 的问题。

    gevent 的 monkey-patch 拦截不到这些驱动的 C 层 socket 调用，故在其阻塞期间
    hub 被钉死、当前请求的响应无法写回；浏览器侧等到自身超时即 `Failed to fetch`。
    把测试放进真实 OS 线程后，hub 可正常让出，超时（或驱动自身无超时导致的长时间
    挂起）都会被本函数兜底为明确的错误 JSON，而非让前端挂死。

    Args:
        timeout: 兜底超时秒数。缺省按类型自适应——JDBC 子进程类型需要覆盖
            JVM 冷启动，故留出比 JDBC_TEST_TIMEOUT 更宽的余量，确保内层
            子进程超时（可给出精确原因）先于外层线程超时触发。
    """
    if timeout is None:
        timeout = (JDBC_TEST_TIMEOUT + 10) if db_type in JDBC_SUBPROCESS_DB_TYPES else 20
    result = {}
    done = threading.Event()

    def _runner():
        try:
            r = _dispatch_conn_test(db_type, data, flavor)
            result['value'] = r if r is not None else {
                'ok': False, 'error': f'不支持的数据库类型: {db_type}'}
        except Exception as e:  # noqa: BLE001 - 连接测试异常需转成前端可读错误
            result['value'] = {'ok': False, 'error': str(e)}
        finally:
            done.set()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()

    # 协作式等待：本进程未执行 gevent monkey-patch，直接 done.wait(timeout) 是真实
    # OS 级阻塞，会把 gevent hub 钉死整整 timeout 秒——期间所有请求（含其它页面、
    # WebSocket 心跳）全部停摆，用户看到的就是「整个界面卡死」。
    # 改为轮询 + 主动让出执行权后，等待期间界面保持可用。
    _deadline = time.monotonic() + timeout
    while not done.is_set():
        if time.monotonic() >= _deadline:
            return {'ok': False,
                    'error': f'连接测试超时（已超过 {timeout} 秒），请检查主机地址、端口与网络连通性'}
        _cooperative_sleep(0.05)
    return result.get('value', {'ok': False, 'error': '连接测试未返回结果'})


# 注册内置类型与已知特殊插件的连接测试处理器
register_connection_tester('mysql', _ct_mysql)
register_connection_tester('mariadb', _ct_mariadb)
register_connection_tester('oceanbase', _ct_oceanbase)
register_connection_tester('pg', _ct_pg)
register_connection_tester('ivorysql', _ct_ivorysql)
register_connection_tester('kingbase', _ct_kingbase)
register_connection_tester('oracle', _ct_oracle)
register_connection_tester('dm', _ct_dm)
register_connection_tester('sqlserver', _ct_sqlserver)
register_connection_tester('tidb', _ct_tidb)
register_connection_tester('yashandb', _ct_yashandb)
register_connection_tester('gbase', _ct_gbase)
register_connection_tester('redis', _ct_redis)
register_connection_tester('redis-cluster', _ct_redis)
register_connection_tester('mongodb', _ct_mongodb)
register_connection_tester('db2', _ct_db2)
register_connection_tester('sqlserver_jdbc', _ct_sqlserver_jdbc)
# hgdb 此前未注册 → 落到 _plugin_conn_fallback 在主进程内起 JVM 导致界面卡死
register_connection_tester('hgdb', _ct_hgdb)
# oracle_jdbc 同理：必须隔离到子进程，否则 JPype/JVM 在 monkey-patch 进程内冻结 hub
register_connection_tester('oracle_jdbc', _ct_oracle_jdbc)
# clickhouse / uxdb 同为 JPype 插件，此前未注册 tester → 主进程起 JVM 卡死，收口子进程
register_connection_tester('clickhouse', _ct_clickhouse)
register_connection_tester('uxdb', _ct_uxdb)


def test_ssh_connection(host, port=22, username='root', password=None, key_file=None):
    """测试 SSH 连接，返回 (ok: bool, msg: str)"""
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if key_file and os.path.isfile(key_file):
            pkey = paramiko.RSAKey.from_private_key_file(key_file)
            client.connect(hostname=host, port=int(port), username=username,
                           pkey=pkey, timeout=10, look_for_keys=False, allow_agent=False,
                           disabled_algorithms={'pubkeys': ['ssh-rsa']})
        elif password is not None:
            client.connect(hostname=host, port=int(port), username=username,
                           password=password, timeout=10, look_for_keys=False, allow_agent=False,
                           disabled_algorithms={'pubkeys': ['ssh-rsa']})
        else:
            try:
                client.connect(hostname=host, port=int(port), username=username,
                               timeout=10, look_for_keys=False, allow_agent=False,
                               disabled_algorithms={'pubkeys': ['ssh-rsa']})
            except paramiko.AuthenticationException:
                return True, _t('webui.ssh_reachable_auth_fail')
        client.close()
        return True, _t('webui.ssh_ok')

    except Exception as e:
        err_msg = str(e)
        if "timed out" in err_msg.lower() or "connection refused" in err_msg.lower():
            return False, _t('webui.ssh_refused').format(err=err_msg)
        return False, _t('webui.ssh_fail').format(err=err_msg)


# ── 路由 ────────────────────────────────────────────────────
@app.route('/')
def index():
    """首页 - 需要登录（Flask session 或 RBAC JWT 均可）"""
    # 检查登录状态（Flask session 或 RBAC token）
    from flask import session, redirect, request
    user_id = session.get('user_id')
    if not user_id:
        # Cookie 未携带时，允许前端登录后通过 ?token= 携带 JWT 一次性建立会话，
        # 解决部分浏览器 / 跨主机(localhost↔127.0.0.1)场景下同源 Cookie 未被发送、
        # 导致登录成功后又被服务端守卫 302 踢回登录页的循环问题。
        tok = request.args.get('token', '') or ''
        if not tok and request.headers.get('Authorization', '').startswith('Bearer '):
            tok = request.headers['Authorization'].split(' ', 1)[1]
        if tok:
            try:
                from modules.user_management.utils.jwt_util import decode_token
                payload = decode_token(tok)
                user_id = payload.get('user_id')
                if user_id:
                    session['user_id'] = user_id
                    session['username'] = payload.get('username', '')
                    session['user_roles'] = payload.get('roles', [])
                    session['is_admin'] = 'admin' in payload.get('roles', [])
                    # 显式标记 session 已修改，确保本次 ?token= 兑换后 Set-Cookie 一定下发，
                    # 避免某些 Flask 版本/配置下因未检测到变更而不写入会话 Cookie。
                    session.modified = True
            except Exception:
                user_id = None
        if not user_id:
            return redirect('/um/login')
    
    # 获取用户角色信息（直接从 session 读，登录时已写入）
    is_admin = session.get('is_admin', False)
    user_role = 'admin' if is_admin else 'user'
    print(f'[DEBUG] index(): user_id={session.get("user_id")}, is_admin={is_admin}, user_role={user_role}', flush=True)
    # 注入当前语言到前端（页面加载时就知道语言，无需额外请求）
    try:
        from i18n import get_lang, get_all_translations, get_language_display
        lang = get_lang()
        i18n_data = get_all_translations(lang)
    except Exception:
        lang = 'zh'
        i18n_data = {}
    # 检测 Pro 模块是否可用
    pro_available = False
    try:
        from modules.pro import is_pro
        pro_available = bool(is_pro())
    except Exception:
        pass
    # 规则引擎：社区版与专业版均可用，独立于 Pro flow
    rules_available = False
    try:
        from modules.pro import get_rule_engine
        get_rule_engine()
        rules_available = True
    except Exception:
        pass
    resp = make_response(render_template('index.html', version=__version__, edition=EDITION, lang=lang, i18n_data=i18n_data,
                           pro_available=pro_available,
                           rules_available=rules_available,
                           admin_token=get_admin_token(),
                           user_role=user_role,
                           product_name=PRODUCT_NAME_EN,
                           product_name_zh=PRODUCT_NAME_ZH,
                           product_full_name=PRODUCT_FULL_NAME,
                           product_tagline=PRODUCT_TAGLINE_EN))
    # 禁止缓存首页模板，避免浏览器复用旧版 index.html（旧 checkLogin 逻辑）导致登录回跳循环
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.route('/api/i18n')
def api_i18n():
    """返回当前语言的翻译数据"""
    try:
        from i18n import get_lang, get_all_translations, get_language_display
        lang = get_lang()
        return jsonify({
            'ok': True,
            'lang': lang,
            'display': get_language_display(lang),
            'data': get_all_translations(lang),
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/set_lang', methods=['POST'])
def api_set_lang():
    """设置语言并持久化到 dbc_config.json"""
    data = request.json or {}
    lang = data.get('lang', 'zh')
    try:
        from i18n import set_lang, get_language_display
        set_lang(lang, persist=True)
        return jsonify({'ok': True, 'lang': lang, 'display': get_language_display(lang)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/reports')
def api_reports():
    try:
        return jsonify(get_reports())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/report_detail/<path:filename>')
def api_report_detail(filename):
    """获取报告详情（用于分享）"""
    try:
        reports_dir = str(paths.REPORTS_DIR)
        fp = os.path.join(reports_dir, filename)
        if not os.path.isfile(fp):
            return jsonify({'ok': False, 'msg': '报告文件不存在'}), 404

        # 从 pro_history.db 获取详情
        result = {'filename': filename, 'db_type': '', 'host': ''}
        try:
            pro_db = str(paths.PRO_DATA_DIR / 'pro_history.db')
            if os.path.isfile(pro_db):
                conn = sqlite3.connect(pro_db)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='inspection_history'")
                if cursor.fetchone():
                    # 先尝试用 report_path 匹配
                    cursor.execute(
                        "SELECT report_path, auto_analyze, health_score, risk_count, risk_level, db_type, instance_name, inspect_time, host FROM inspection_history WHERE report_path LIKE ?",
                        ('%' + filename,))
                    row = cursor.fetchone()
                    # 如果没找到，尝试用文件名中的时间戳匹配
                    if not row:
                        # 从文件名提取时间戳，如 Oracle巡检报告_192.168.42.220_ORACLE19_20260517212935.docx
                        import re
                        ts_match = re.search(r'(\d{14})', filename)
                        if ts_match:
                            ts = ts_match.group(1)
                            # 转换为 inspect_time 格式: 2026-05-17T21:29:35
                            dt_str = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
                            cursor.execute(
                                "SELECT report_path, auto_analyze, health_score, risk_count, risk_level, db_type, instance_name, inspect_time, host FROM inspection_history WHERE inspect_time LIKE ? ORDER BY id DESC LIMIT 1",
                                (dt_str[:13] + '%',))
                            row = cursor.fetchone()
                    # 如果还没找到，用最新的记录
                    if not row:
                        cursor.execute(
                            "SELECT report_path, auto_analyze, health_score, risk_count, risk_level, db_type, instance_name, inspect_time, host FROM inspection_history ORDER BY id DESC LIMIT 1")
                        row = cursor.fetchone()
                    if row:
                        report_path, auto_analyze_json, health_score, risk_count, risk_level, db_type_db, instance_name, inspect_time, host = row
                        result['health_score'] = health_score
                        result['risk_level'] = risk_level
                        result['risk_count'] = risk_count
                        result['db_type'] = db_type_db or ''
                        result['host'] = host or ''
                        # 如果 host 为空，尝试从文件名提取 IP
                        if not result['host']:
                            import re
                            _ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', filename)
                            if _ip_match:
                                result['host'] = _ip_match.group(1)
                        result['inspect_time'] = inspect_time or ''
                        if auto_analyze_json:
                            try:
                                items = json.loads(auto_analyze_json)
                                # auto_analyze 结构: list of dicts with keys col1(描述), col2(等级), col3(建议)
                                # col1/col2/col3 可能存的是 i18n key，需要翻译
                                result['issues'] = [
                                    {'level': _tr(it.get('col2', '')), 'description': _tr(it.get('col1', '')), 'suggestion': _tr(it.get('col3', ''))}
                                    for it in items
                                ]
                            except Exception:
                                pass
                conn.close()
        except Exception:
            pass

        # 推断数据库类型（如果数据库中没有）
        if not result['db_type']:
            result['db_type'] = 'DM8' if 'DM8' in filename or '达梦' in filename else \
                                'Oracle' if 'Oracle' in filename else \
                                'PostgreSQL' if 'PG' in filename or 'PostgreSQL' in filename else 'MySQL'
        if not result['inspect_time']:
            result['inspect_time'] = datetime.datetime.fromtimestamp(os.path.getmtime(fp)).strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({'ok': True, 'result': result})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500

@app.route('/api/download/<task_id>')
def api_download_by_task(task_id):
    task = tasks.get(task_id)
    if not task or not task.get('report_file'):
        return "Report not found", 404
    return send_file(task['report_file'], as_attachment=True,
                     download_name=task.get('report_name', 'report.docx'))

@app.route('/api/download_pdf/<task_id>')
def api_download_pdf_by_task(task_id):
    """将巡检报告 DOCX 转换为 PDF 并提供下载（复用 pdf_export.convert_docx_to_pdf）。

    生成的 PDF 会缓存在报告同目录（与 DOCX 同名、扩展名改为 .pdf）。
    再次下载时若 PDF 已存在且比 DOCX 新，则直接复用，无需重复转换。
    """
    task = tasks.get(task_id)
    if not task or not task.get('report_file'):
        return jsonify({'ok': False, 'msg': '未找到对应的巡检任务或报告'}), 404
    docx_path = task['report_file']
    if not os.path.isfile(docx_path):
        return jsonify({'ok': False, 'msg': '报告文件不存在'}), 404

    pdf_path = os.path.splitext(docx_path)[0] + '.pdf'
    # 缓存命中：PDF 已存在且不过期（mtime >= docx）
    if not (os.path.isfile(pdf_path) and os.path.getmtime(pdf_path) >= os.path.getmtime(docx_path)):
        try:
            from modules.web.pdf_export import convert_docx_to_pdf
        except ImportError:
            return jsonify({'ok': False, 'msg': 'PDF 转换模块不可用（pdf_export 未加载）'}), 500
        ok, result = convert_docx_to_pdf(docx_path)
        if not ok:
            return jsonify({
                'ok': False,
                'msg': 'PDF 生成失败：' + str(result) +
                      '。请确认已安装依赖：pip install -r requirements.txt（reportlab / python-docx）。'
            }), 503
        pdf_path = result

    if not os.path.isfile(pdf_path):
        return jsonify({'ok': False, 'msg': 'PDF 文件生成失败'}), 500

    base = task.get('report_name', 'report.docx')
    pdf_name = os.path.splitext(base)[0] + '.pdf'
    return send_file(pdf_path, as_attachment=True, download_name=pdf_name)

@app.route('/api/download_file')
def api_download_file():
    name = request.args.get('name', '')
    reports_dir = str(paths.REPORTS_DIR)
    fp = os.path.join(reports_dir, name)
    if not os.path.isfile(fp):
        return "File not found", 404
    return send_file(fp, as_attachment=True, download_name=name)

@app.route('/api/delete_report', methods=['POST'])
def api_delete_report():
    """删除指定报告文件"""
    try:
        data = request.get_json() or {}
        name = data.get('name', '')
        if not name:
            return jsonify({'ok': False, 'error': _t('webui.reports_delete_name_required')}), 400
        reports_dir = str(paths.REPORTS_DIR)
        fp = os.path.join(reports_dir, name)
        if not os.path.isfile(fp):
            return jsonify({'ok': False, 'error': _t('webui.reports_file_not_found')}), 404
        os.remove(fp)
        # 数据库巡检报告 → 同步删除 history.db 趋势数据
        if not name.startswith('服务器巡检_'):
            try:
                _sync_delete_trend_for_report(name)
            except Exception:
                pass
        # 服务器巡检报告 → 同步删除 server_inspection_history 记录
        else:
            try:
                from modules.server.inspect import delete_server_inspection_by_filename
                delete_server_inspection_by_filename(name)
            except Exception:
                pass
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/history_instances', methods=['GET'])
def api_history_instances():
    """返回历史趋势库中所有已记录（含快照）的数据库实例列表。

    每次巡检完成都会通过 :class:`HistoryManager` 的 ``save_snapshot`` 把关键指标
    写入 ``<base_dir>/data/history.db``（``history_instances`` + ``snapshots``
    两张表），因此趋势实例列表以「历史表」为准即可。

    旧实现曾额外用 ``_parse_report_filename`` 按一组**固定报告前缀**过滤
     ``.docx`` 报告文件，导致 HGDB / DB2 / sqlserver_jdbc / oracle_jdbc 等
    不在前缀表中的实例被整体过滤掉、列表永远为空。趋势分析依赖的是快照数据，
    与 Word 报告文件是否存在无关，故移除该文件级过滤。
    """
    try:
        from modules.inspection.analyzer import HistoryManager
        from modules.access import principal_from_session, filter_visible
        hm = HistoryManager(BASE_DIR)
        user = principal_from_session()
        raw_instances = filter_visible(
            user, hm.list_instances(), 'history_instance', id_key='key')
        instances = [{
            'key': inst.get('key', ''),
            'db_type': inst.get('db_type', ''),
            'host': inst.get('host', ''),
            'port': str(inst.get('port', '')),
            'label': inst.get('label', inst.get('key', '')),
            'snapshot_count': inst.get('snapshots_count', 0),
            'last_time': inst.get('last_time', ''),
            'last_health': inst.get('last_health', _t('webui.health_unknown')),
            'last_risk': inst.get('last_risk', 0),
        } for inst in raw_instances]
        return jsonify({'ok': True, 'instances': instances})
    except Exception as e:
        return jsonify({'ok': False, 'instances': [], 'error': str(e)})

@app.route('/api/trend', methods=['GET'])
def api_trend():
    """返回指定数据库实例的历史趋势数据"""
    db_type = request.args.get('db_type', '')
    host = request.args.get('host', '')
    port = request.args.get('port', '')
    if not host or not port:
        return jsonify({'ok': False, 'error': _t('webui.err_missing_host_port')})
    try:
        from modules.inspection.analyzer import HistoryManager
        from modules.inspection.db_history import _db_key
        from modules.access import principal_from_session, assert_visible
        script_dir = BASE_DIR
        hm = HistoryManager(script_dir)
        # 趋势数据按 host/port 直接取，必须先按历史实例的主键过可见性判定，
        # 否则任何登录用户都能靠猜 host:port 拉到他人的历史指标。
        user = principal_from_session()
        inst_key = _db_key(db_type, host, port)
        ok, err = assert_visible(user, 'history_instance', inst_key)
        if not ok:
            return jsonify({'ok': False, **err}), 403
        trend = hm.get_trend(db_type, host, int(port))
        comparison = hm.get_comparison(db_type, host, int(port))
        return jsonify({'ok': True, 'trend': trend, 'comparison': comparison})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/ai_config', methods=['GET'])
def api_ai_config():
    cfg_path = os.path.join(BASE_DIR, 'dbc_config.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        ai_cfg = cfg.get('ai', {})
        # 确保返回的配置包含 online_enabled 字段
        ai_cfg.setdefault('online_enabled', False)
        ai_cfg.setdefault('online_backend', 'openai')
        ai_cfg.setdefault('online_api_url', 'https://api.openai.com/v1')
        ai_cfg.setdefault('online_model', 'gpt-4o-mini')
        # 注：本地单用户工具，配置存于本机 dbc_config.json，直接返回真实 api_key
        # 以便前端在重新打开界面时回填已保存的值（不再脱敏成 '***'）。
        return jsonify(ai_cfg)
    return jsonify({
        'enabled': False, 'backend': 'disabled', 'model': '',
        'online_enabled': False, 'online_backend': 'openai',
        'online_api_url': '', 'online_model': '',
        'api_key': '', 'api_url': ''
    })

@app.route('/api/ai_config', methods=['POST'])
def api_save_ai_config():
    data = request.json or {}
    cfg_path = os.path.join(BASE_DIR, 'dbc_config.json')

    # 加载现有配置作为基础
    existing = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            existing = json.load(f)

    # 获取现有 ai 配置
    ai_existing = existing.get('ai', {})

    # 合并：如果 api_key 为空字符串，保留旧的 api_key（防止误覆盖）
    if 'api_key' in data and not data['api_key'] and ai_existing.get('api_key'):
        data['api_key'] = ai_existing['api_key']

    # 深度合并：保持已有配置中未提交的字段
    for key in ('rag',):
        if key not in data and key in ai_existing:
            data[key] = ai_existing[key]

    # 写回 dbc_config.json 的 ai 字段
    existing['ai'] = data
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=4)
    return jsonify({'ok': True, 'msg': _t('webui.ai_config_saved')})

@app.route('/api/config', methods=['GET'])
def api_get_config():
    cfg_path = os.path.join(BASE_DIR, 'dbc_config.json')
    if not os.path.exists(cfg_path):
        return jsonify({})
    with open(cfg_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    return jsonify({
        'oracle_client_lib_dir': cfg.get('oracle_client_lib_dir', ''),
        'yashandb_driver_lib_dir': cfg.get('yashandb_driver_lib_dir', ''),
        'language': cfg.get('language', 'zh'),
        'notification': cfg.get('notification', {'enabled': False}),
        'show_ai_assistant': cfg.get('show_ai_assistant', True)
    })

@app.route('/api/config', methods=['POST'])
def api_save_config():
    data = request.json or {}
    cfg_path = os.path.join(BASE_DIR, 'dbc_config.json')
    existing = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    if 'oracle_client_lib_dir' in data:
        existing['oracle_client_lib_dir'] = data['oracle_client_lib_dir']
    if 'yashandb_driver_lib_dir' in data:
        existing['yashandb_driver_lib_dir'] = data['yashandb_driver_lib_dir']
    if 'notification' in data:
        existing['notification'] = data['notification']
    if 'show_ai_assistant' in data:
        existing['show_ai_assistant'] = bool(data['show_ai_assistant'])
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=4)
    return jsonify({'ok': True})

# ─── 插件市场镜像配置 API ─────────────────────────────

@app.route('/api/plugin_market/registry_urls', methods=['GET'])
def api_get_plugin_market_registry_urls():
    """获取当前插件市场 registry URLs"""
    try:
        from modules.pluginkit.market import get_market, DEFAULT_REGISTRY_URLS
        market = get_market()
        urls = market.get_registry_urls()
        return jsonify({'ok': True, 'urls': urls, 'defaults': DEFAULT_REGISTRY_URLS})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/plugin_market/registry_urls', methods=['POST'])
def api_save_plugin_market_registry_urls():
    """保存用户自定义插件市场 registry URLs"""
    data = request.json or {}
    urls = data.get('urls', [])
    if not isinstance(urls, list) or len(urls) == 0:
        return jsonify({'ok': False, 'error': 'urls 必须是非空列表'}), 400
    try:
        from modules.pluginkit.market import get_market
        market = get_market()
        ok = market.set_registry_urls(urls)
        if ok:
            return jsonify({'ok': True, 'msg': '插件市场镜像地址已保存'})
        else:
            return jsonify({'ok': False, 'error': '保存失败'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/plugin_market/registry_urls/reset', methods=['POST'])
def api_reset_plugin_market_registry_urls():
    """重置为默认镜像地址"""
    try:
        from modules.pluginkit.market import get_market, DEFAULT_REGISTRY_URLS
        market = get_market()
        market.set_registry_urls(DEFAULT_REGISTRY_URLS)
        return jsonify({'ok': True, 'msg': '已重置为默认镜像地址', 'urls': DEFAULT_REGISTRY_URLS})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/plugin_market/save_builtin_registry', methods=['POST'])
def api_save_builtin_registry():
    """保存当前市场数据到内置 registry.json（离线兜底）"""
    try:
        from modules.pluginkit.market import get_market
        market = get_market()
        result = market.save_builtin_registry()
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ─── Oracle Client 下载 & 状态 API ─────────────────────────────
def _get_oracle_platform_key():
    """根据系统返回 oracle_client 子目录名"""
    import platform as _pl
    sys_name = _pl.system().lower()
    arch = _pl.machine().lower()
    if sys_name == 'windows':
        return 'windows_x64'
    elif sys_name == 'linux':
        return 'linux_x64'
    elif sys_name == 'darwin':
        return 'darwin_arm64' if arch in ('arm64', 'aarch64') else 'darwin_x64'
    return 'windows_x64'  # fallback


# Oracle Instant Client 下载进度（全局，供 download 和 status 接口共享）
_oracle_client_download_progress = {'progress': 0, 'status': 'idle', 'message': '', 'error': None}

# 所有驱动下载进度（全局）
_all_drivers_download_progress = {'progress': 0, 'status': 'idle', 'message': '', 'error': None}


def _check_oracle_client_installed(platform_key=None):
    """检查 Oracle Instant Client 是否已安装，返回 dict"""
    import sys
    if platform_key is None:
        platform_key = _get_oracle_platform_key()
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = BASE_DIR
    install_dir = os.path.join(base_dir, 'drivers', 'oracle_client', platform_key)

    # 检测标记文件（基本库 + 核心运行时）
    if platform_key == 'windows_x64':
        marker = os.path.join(install_dir, 'oci.dll')
        core_marker = os.path.join(install_dir, 'oraociei.dll')
    elif platform_key == 'linux_x64':
        marker = os.path.join(install_dir, 'libclntsh.so')
        core_marker = os.path.join(install_dir, 'libociei.so')
    else:
        marker = os.path.join(install_dir, 'libclntsh.dylib')
        core_marker = os.path.join(install_dir, 'libociei.dylib')

    installed = os.path.exists(marker) and os.path.exists(core_marker)
    version = 'unknown'
    missing_core = os.path.exists(marker) and not os.path.exists(core_marker)

    # 尝试从文件内容中检测版本
    if installed:
        readme = os.path.join(install_dir, 'README.md')
        if os.path.exists(readme):
            try:
                import re
                with open(readme, 'r', encoding='utf-8', errors='ignore') as _f:
                    content = _f.read()
                    m = re.search(r'(\d+\.\d+)', content)
                    if m:
                        version = m.group(1)
            except Exception:
                pass

    return {
        'installed': installed,
        'missing_core': missing_core,
        'platform': platform_key,
        'version': version,
        'install_dir': install_dir
    }


@app.route('/api/oracle_client_status', methods=['GET'])
def api_oracle_client_status():
    """获取 Oracle Instant Client 安装状态"""
    platform_key = request.args.get('platform') or None
    result = _check_oracle_client_installed(platform_key)
    return jsonify(result)


@app.route('/api/yashandb_driver_status', methods=['GET'])
def api_yashandb_driver_status():
    """获取 YashanDB 驱动安装状态"""
    try:
        import modules.ingest.drivers as download_drivers
        import importlib
        importlib.reload(download_drivers)
        result = download_drivers.check_all_drivers()
        yashandb = result.get('yashandb', {})
        return jsonify({
            'installed': yashandb.get('installed', False),
            'platform': yashandb.get('platform', ''),
            'install_dir': yashandb.get('install_dir', ''),
            'version': 'unknown'
        })
    except Exception as e:
        return jsonify({'installed': False, 'platform': '', 'install_dir': '', 'version': 'unknown', 'error': str(e)})


@app.route('/api/sqlserver_odbc_status', methods=['GET'])
def api_sqlserver_odbc_status():
    """获取 SQL Server ODBC 驱动安装状态，检测系统架构并提示安装"""
    try:
        import pyodbc
        installed = [d for d in pyodbc.drivers() if d.strip()]
        preferred = ['ODBC Driver 18 for SQL Server', 'ODBC Driver 17 for SQL Server',
                     'ODBC Driver 13 for SQL Server', 'SQL Server']
        best = None
        for d in preferred:
            if d in installed:
                best = d
                break

        # 检测系统架构
        import platform, sys, os
        machine = platform.machine().lower()
        if machine in ('amd64', 'x86_64'):
            sys_arch = 'x64'
            msi_name = 'msodbcsql_x64.msi'
        elif machine in ('x86', 'i386', 'i686'):
            sys_arch = 'x86'
            msi_name = 'msodbcsql_x86.msi'
        elif machine in ('arm64', 'aarch64'):
            sys_arch = 'arm64'
            msi_name = 'msodbcsql_arm64.msi'
        else:
            sys_arch = machine
            msi_name = None

        # 检查本地 drivers/sqlserver/ 下是否有对应的 MSI
        # 路径逻辑：PyInstaller 打包后用 exe 所在目录，开发模式下用 __file__ 所在目录
        local_msi_path = None
        if msi_name:
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = BASE_DIR
            msi_path = os.path.join(base_dir, 'drivers', 'sqlserver', msi_name)
            if os.path.isfile(msi_path):
                local_msi_path = msi_path

        download_url = 'https://learn.microsoft.com/zh-cn/sql/connect/odbc/download-odbc-driver-for-sql-server'

        return jsonify({
            'ok': True,
            'available': installed,
            'using': best or (installed[0] if installed else ''),
            'best_driver': best,
            'system_arch': sys_arch,
            'msi_name': msi_name,
            'local_msi_available': local_msi_path is not None,
            'local_msi_path': local_msi_path,
            'download_url': download_url
        })
    except Exception as e:
        return jsonify({'ok': False, 'available': [], 'using': '', 'best_driver': None, 'error': str(e)})


@app.route('/api/driver_status', methods=['GET'])
def api_driver_status():
    """获取所有数据库客户端驱动的安装状态（合并）"""
    import sys, os, platform
    result = {
        'oracle': {'ok': False, 'installed': False},
        'yashandb': {'ok': False, 'installed': False},
        'odbc': {'ok': False, 'installed': False}
    }
    # 1. Oracle Instant Client
    try:
        platform_key = request.args.get('platform') or None
        oracle_status = _check_oracle_client_installed(platform_key)
        result['oracle'] = {'ok': True}
        result['oracle'].update(oracle_status)
    except Exception as e:
        result['oracle']['error'] = str(e)
    # 2. YashanDB Driver
    try:
        import modules.ingest.drivers as download_drivers
        import importlib
        importlib.reload(download_drivers)
        dr = download_drivers.check_all_drivers()
        y = dr.get('yashandb', {})
        result['yashandb'] = {
            'ok': True,
            'installed': y.get('installed', False),
            'platform': y.get('platform', ''),
            'install_dir': y.get('install_dir', '')
        }
    except Exception as e:
        result['yashandb']['error'] = str(e)
    # 3. SQL Server ODBC Driver
    try:
        import pyodbc
        installed = [d for d in pyodbc.drivers() if d.strip()]
        preferred = ['ODBC Driver 18 for SQL Server', 'ODBC Driver 17 for SQL Server',
                     'ODBC Driver 13 for SQL Server', 'SQL Server']
        best = next((d for d in preferred if d in installed), None)
        machine = platform.machine().lower()
        if machine in ('amd64', 'x86_64'):
            sys_arch, msi_name = 'x64', 'msodbcsql_x64.msi'
        elif machine in ('x86', 'i386', 'i686'):
            sys_arch, msi_name = 'x86', 'msodbcsql_x86.msi'
        elif machine in ('arm64', 'aarch64'):
            sys_arch, msi_name = 'arm64', 'msodbcsql_arm64.msi'
        else:
            sys_arch, msi_name = machine, None
        local_msi_path = None
        if msi_name:
            base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else BASE_DIR
            msi_path = os.path.join(base_dir, 'drivers', 'sqlserver', msi_name)
            if os.path.isfile(msi_path):
                local_msi_path = msi_path
        result['odbc'] = {
            'ok': True,
            'installed': len(installed) > 0,
            'available': installed,
            'using': best or (installed[0] if installed else ''),
            'best_driver': best,
            'system_arch': sys_arch,
            'msi_name': msi_name,
            'local_msi_available': local_msi_path is not None,
            'local_msi_path': local_msi_path,
            'download_url': 'https://learn.microsoft.com/zh-cn/sql/connect/odbc/download-odbc-driver-for-sql-server'
        }
    except Exception as e:
        result['odbc']['error'] = str(e)
    return jsonify(result)


@app.route('/api/oracle_client_download', methods=['POST'])
def api_oracle_client_download():
    """
    触发 Oracle Instant Client 自动下载
    返回下载状态，前端通过轮询 oracle_client_status 获取完成状态
    """
    data = request.json or {}
    platform_key = data.get('platform') or None

    # 检查是否已安装
    status = _check_oracle_client_installed(platform_key)
    if status['installed']:
        return jsonify({'ok': True, 'already_installed': True, 'message': f"Oracle Instant Client {status['version']} 已安装", **status})

    import threading, json as _json, sys

    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = BASE_DIR

    # 重置全局下载状态
    _oracle_client_download_progress.update({'progress': 0, 'status': 'downloading', 'message': '正在启动下载...', 'error': None})

    def _do_download():
        try:
            sys_path_added = False
            script_dir = BASE_DIR
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
                sys_path_added = True

            # 强制重新加载模块，确保使用最新代码（避免 Python 模块缓存）
            import importlib
            import modules.ingest.oracle_client as download_oracle_client
            importlib.reload(download_oracle_client)
            download_instant_client = download_oracle_client.download_instant_client

            def on_progress(status_str, progress, total, msg):
                _oracle_client_download_progress['status'] = status_str
                _oracle_client_download_progress['progress'] = progress
                _oracle_client_download_progress['message'] = msg

            result = download_instant_client(
                platform_key=platform_key,
                target_dir=base_dir,
                progress_callback=on_progress
            )

            if result['success']:
                _oracle_client_download_progress['status'] = 'done'
                _oracle_client_download_progress['progress'] = 100
                _oracle_client_download_progress['message'] = f"Oracle Instant Client {result['version']} 安装成功"
            else:
                _oracle_client_download_progress['status'] = 'error'
                _oracle_client_download_progress['error'] = result.get('error', '未知错误')
                _oracle_client_download_progress['message'] = result.get('error', '下载失败')

        except Exception as e:
            _oracle_client_download_progress['status'] = 'error'
            _oracle_client_download_progress['error'] = str(e)
            _oracle_client_download_progress['message'] = f'下载异常: {e}'
        finally:
            if sys_path_added and script_dir in sys.path:
                sys.path.remove(script_dir)

    # 在后台线程中下载
    t = threading.Thread(target=_do_download, daemon=True)
    t.start()

    return jsonify({'ok': True, 'message': '下载已启动', 'platform': platform_key or _get_oracle_platform_key()})


@app.route('/api/oracle_client_download_status', methods=['GET'])
def api_oracle_client_download_status():
    """获取下载进度（配合 SSE 或轮询使用）"""
    # 检查安装状态
    status = _check_oracle_client_installed()
    # 返回真实下载进度（来自全局变量）
    progress = _oracle_client_download_progress.copy()
    return jsonify({
        'installed': status['installed'],
        'version': status['version'],
        'platform': status['platform'],
        'download_progress': progress['progress'],
        'download_status': progress['status'],
        'download_message': progress['message'],
        'download_error': progress['error'],
    })


@app.route('/api/download_all_drivers', methods=['POST'])
def api_download_all_drivers():
    """
    一键下载所有驱动：
    1. 下载 drivers.zip（通用驱动）
    2. 下载对应平台的 Oracle Instant Client
    """
    import threading, sys

    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = BASE_DIR

    # 重置全局下载状态
    global _all_drivers_download_progress
    _all_drivers_download_progress = {
        'progress': 0,
        'status': 'downloading',
        'message': '正在准备下载...',
        'error': None
    }

    def _do_download_all():
        try:
            import importlib
            import modules.ingest.drivers as download_drivers
            import modules.ingest.oracle_client as download_oracle_client

            # 强制重新加载模块
            importlib.reload(download_drivers)
            importlib.reload(download_oracle_client)

            # 步骤1：下载 drivers.zip
            _all_drivers_download_progress['message'] = '正在下载通用驱动包（drivers.zip）...'
            _all_drivers_download_progress['progress'] = 10

            def on_drivers_progress(status_str, progress, total, msg):
                _all_drivers_download_progress['message'] = f'通用驱动：{msg}'
                _all_drivers_download_progress['progress'] = int(progress * 0.5)  # 前 50%

            result_drivers = download_drivers.download_drivers(
                target_dir=base_dir,
                progress_callback=on_drivers_progress
            )

            if not result_drivers['success']:
                _all_drivers_download_progress['status'] = 'error'
                _all_drivers_download_progress['error'] = result_drivers.get('error', '下载通用驱动失败')
                _all_drivers_download_progress['message'] = f"通用驱动下载失败：{result_drivers.get('error')}"
                return

            _all_drivers_download_progress['progress'] = 50
            _all_drivers_download_progress['message'] = '通用驱动下载完成，正在下载 Oracle Instant Client...'

            # 步骤2：下载 Oracle Instant Client
            def on_oracle_progress(status_str, progress, total, msg):
                _all_drivers_download_progress['message'] = f'Oracle Client：{msg}'
                _all_drivers_download_progress['progress'] = 50 + int(progress * 0.5)  # 后 50%

            result_oracle = download_oracle_client.download_instant_client(
                target_dir=base_dir,
                progress_callback=on_oracle_progress
            )

            if not result_oracle['success'] and not result_oracle.get('error', '').startswith('已安装'):
                _all_drivers_download_progress['status'] = 'error'
                _all_drivers_download_progress['error'] = result_oracle.get('error', '下载 Oracle Client 失败')
                _all_drivers_download_progress['message'] = f"Oracle Client 下载失败：{result_oracle.get('error')}"
                return

            # 完成
            _all_drivers_download_progress['status'] = 'done'
            _all_drivers_download_progress['progress'] = 100
            _all_drivers_download_progress['message'] = '[OK] 所有驱动下载并安装成功！'

        except Exception as e:
            _all_drivers_download_progress['status'] = 'error'
            _all_drivers_download_progress['error'] = str(e)
            _all_drivers_download_progress['message'] = f'下载异常：{e}'

    # 在后台线程中下载
    t = threading.Thread(target=_do_download_all, daemon=True)
    t.start()

    return jsonify({'ok': True, 'message': '开始下载所有驱动...'})


@app.route('/api/download_all_drivers_status', methods=['GET'])
def api_download_all_drivers_status():
    """获取所有驱动下载进度"""
    progress = _all_drivers_download_progress.copy()
    return jsonify({
        'download_progress': progress['progress'],
        'download_status': progress['status'],
        'download_message': progress['message'],
        'download_error': progress['error'],
    })


@app.route('/api/test_db', methods=['POST'])
def api_test_db():
    data = request.json
    db_type = data.get('db_type', 'mysql')
    # 统一连接测试分发：注册表 -> 插件 connect_test 复用 -> 兜底
    # 经线程隔离 + 硬超时包装，避免 C 层驱动在 gevent 下钉死 hub 致前端 Failed to fetch
    return jsonify(_conn_test_with_timeout(db_type, data, 'regular'))


# ---------------------------------------------------------------------------
# 本地 Ollama 访问的健壮性辅助（与 modules/inspection/analyzer.py 保持一致）
# 规避：系统 HTTP 代理拦截本机 LAN IP → 404；Windows 本机 LAN IP 自连 loopback 被拒。
# ---------------------------------------------------------------------------
_OLLAMA_LOCAL_HOSTS = None


def _ollama_is_local_host(host):
    global _OLLAMA_LOCAL_HOSTS
    h = (host or '').lower().strip()
    if h in ('localhost', '127.0.0.1', '::1', '[::1]', '0.0.0.0') or h.startswith('127.'):
        return True
    if _OLLAMA_LOCAL_HOSTS is None:
        s = {'localhost', '127.0.0.1', '::1', '[::1]', '0.0.0.0'}
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                s.add(info[4][0])
        except Exception:
            pass
        try:
            _sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _sk.connect(('8.8.8.8', 80))
            s.add(_sk.getsockname()[0])
            _sk.close()
        except Exception:
            pass
        _OLLAMA_LOCAL_HOSTS = s
    return h in _OLLAMA_LOCAL_HOSTS


def _ollama_normalize_url(api_url):
    """本机 Ollama 地址归一化为 127.0.0.1（远程地址保持原样）"""
    if not api_url:
        return api_url
    m = re.match(r'^(https?://)([^/:]+)(.*)$', api_url.strip())
    if not m:
        return api_url
    scheme, host, rest = m.group(1), m.group(2), m.group(3)
    if _ollama_is_local_host(host):
        return scheme + '127.0.0.1' + rest
    return api_url


def _ollama_no_proxy_opener():
    import urllib.request
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


@app.route('/api/test_ollama', methods=['POST'])
def api_test_ollama():
    """测试 Ollama 连接"""
    import urllib.request, json as _json
    data = request.json or {}
    api_url = (data.get('api_url') or 'http://localhost:11434').rstrip('/')
    model   = data.get('model') or 'qwen2.5:7b'

    # 本机地址归一化为 127.0.0.1，并绕过系统代理直连
    api_url = _ollama_normalize_url(api_url)
    opener = _ollama_no_proxy_opener()

    # 先测 /api/tags（列出模型）
    tags_url = api_url + '/api/tags'
    try:
        req = urllib.request.Request(tags_url, headers={'Content-Type': 'application/json'})
        with opener.open(req, timeout=10) as resp:
            body = resp.read().decode('utf-8')
            try:
                result = _json.loads(body)
                models = result.get('models', [])
                model_names = [m.get('name', '') for m in models]
                if model_names:
                    return jsonify({'ok': True, 'msg': _t('webui.ollama_models_found').format(models=', '.join(model_names))})
                return jsonify({'ok': True, 'msg': _t('webui.ollama_no_models')})
            except _json.JSONDecodeError:
                return jsonify({'ok': False, 'msg': _t('webui.err_data_format').format(body=body[:200])})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:200]
        return jsonify({'ok': False, 'msg': f'HTTP {e.code}: {body}'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': _t('webui.err_conn_failed').format(e=e)})


def _probe_openai_model(api_url, api_key, model):
    """退化探测：用用户填写的 model 向 /chat/completions 发一次最小请求，
    验证该模型是否真实可用（用于 /models 端点不可用或返回空时）。"""
    import urllib.request, json as _json
    chat_url = api_url + '/chat/completions'
    payload = _json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': 'hi'}],
        'max_tokens': 1,
        'stream': False,
    }).encode('utf-8')
    req = urllib.request.Request(chat_url, data=payload, headers={
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}'
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode('utf-8', errors='ignore')
            try:
                result = _json.loads(body)
            except _json.JSONDecodeError:
                result = {}
            if isinstance(result, dict) and result.get('choices'):
                return jsonify({'ok': True, 'msg': _t('webui.ai_test_ok')})
            return jsonify({'ok': False, 'msg': (_t('webui.ai_test_fail') or '测试失败') + ': 模型 "%s" 未返回有效响应' % model})
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')[:300]
        err_lower = err_body.lower()
        # 模型不存在类错误：404/400 + 响应体含 model not found / does not exist 等
        is_model_err = (e.code in (404, 400)) and (
            'model' in err_lower and ('not found' in err_lower or 'does not exist' in err_lower or '无此模型' in err_lower)
        )
        if is_model_err:
            return jsonify({'ok': False, 'msg': '模型 "%s" 不可用或不存在: %s' % (model, err_body[:150])})
        return jsonify({'ok': False, 'msg': 'HTTP %d: %s' % (e.code, err_body)})
    except Exception as e:
        return jsonify({'ok': False, 'msg': _t('webui.err_conn_failed').format(e=e)})


@app.route('/api/test_openai', methods=['POST'])
def api_test_openai():
    """测试 OpenAI / 兼容 API 连接（OpenAI、DeepSeek、Azure 等），
    并校验用户填写的 model 是否真实存在于可用模型中。"""
    import urllib.request, json as _json
    data = request.json or {}
    api_url = (data.get('api_url') or 'https://api.openai.com/v1').rstrip('/')
    api_key = data.get('api_key', '')
    model = data.get('model') or 'gpt-4o-mini'

    if not api_key:
        return jsonify({'ok': False, 'msg': _t('webui.ai_err_no_key')})

    # 先用 /models 端点验证 API Key 是否有效，并校验用户填写的 model
    test_url = api_url + '/models'
    try:
        req = urllib.request.Request(test_url, headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode('utf-8')
            try:
                result = _json.loads(body)
                models = result.get('data', [])
                if isinstance(models, list) and len(models) > 0:
                    model_ids = [m.get('id', '') for m in models]
                    if model in model_ids:
                        return jsonify({'ok': True, 'msg': _t('webui.ai_test_ok') + '，可用模型: ' + ', '.join(model_ids[:5])})
                    # 模型不在可用列表中 → 明确失败，不再误报"连接成功"
                    return jsonify({'ok': False, 'msg': '模型名称 "%s" 不在可用模型列表中，可用模型示例: %s' % (model, ', '.join(model_ids[:5]))})
                # /models 返回空列表，退化到真实 chat 探测
                return _probe_openai_model(api_url, api_key, model)
            except _json.JSONDecodeError:
                # /models 返回非标准格式，退化到真实 chat 探测
                return _probe_openai_model(api_url, api_key, model)
    except urllib.error.HTTPError as e:
        # /models 调用失败（如某些兼容网关无此端点），退化到真实 chat 探测
        return _probe_openai_model(api_url, api_key, model)
    except Exception as e:
        return jsonify({'ok': False, 'msg': _t('webui.err_conn_failed').format(e=e)})


@app.route('/api/test_ssh', methods=['POST'])
def api_test_ssh():
    """测试 SSH 连接"""
    data = request.json
    ok, msg = test_ssh_connection(
        data.get('ssh_host', ''),
        data.get('ssh_port', 22),
        data.get('ssh_user', 'root'),
        data.get('ssh_password') or None,
        data.get('ssh_key_file') or None
    )
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/plugin-logo/<db_type>')
def plugin_logo(db_type):
    """从插件目录读取 logo.png（前端 db_type 下拉图标）。404 时前端回退 emoji。"""
    try:
        from modules.pluginkit.loader import discover_plugins
        for p in discover_plugins():
            if p.get('enabled') and p.get('db_type') == db_type:
                logo = os.path.join(p.get('path') or '', 'logo.png')
                if os.path.exists(logo):
                    return send_file(logo, mimetype='image/png')
                break
    except Exception:
        pass
    return '', 404


@app.route('/api/start_inspection', methods=['POST'])
def api_start_inspection():
    try:
        data = request.json
        db_type = data.get('db_type', 'mysql')
        inspector_name = data.get('inspector_name', data.get('inspector', 'Jack'))

        # 章节可选：读取前端勾选的章节 ID 列表
        # None/不传 -> 全量巡检（等价旧行为）；[] -> 非法（必须至少选一个章节）；[int,...] -> 仅选中章
        chapter_ids = data.get('chapter_ids', None)
        if isinstance(chapter_ids, list) and len(chapter_ids) == 0:
            return jsonify({'code': 1, 'message': '请至少选择一个章节'}), 400

        # 支持通过数据源ID获取连接信息
        datasource_id = data.get('datasource_id')
        if datasource_id:
            from modules.pro import get_instance_manager
            im = get_instance_manager()
            instance = im.get_instance_decrypted(datasource_id)
            if not instance:
                return jsonify({'ok': False, 'msg': '数据源不存在'})
            # 使用数据源的连接信息
            _meta = get_db_meta(db_type)
            _default_db = _meta.default_database if _meta else 'postgres'
            db_info = {
                'ip':        instance.get('host', ''),
                'port':      int(instance.get('port', 0) or 0),
                'user':      instance.get('user', ''),
                'tenant':    instance.get('tenant', '') or '',
                'password':  instance.get('password', ''),
                'database':  instance.get('database') or _default_db,
                'service_name': instance.get('service_name', None),
                'sysdba':    bool(instance.get('sysdba', False)),  # ← 新增（确保是布尔值）
                'name':      instance.get('name', ''),
                'desensitize': bool(data.get('desensitize', False)),
            }
            # 修复：缺省库名为空串的类型（MySQL 家族 / redis 等）若库名被旧 bug 误写为
            # 'postgres'（PG 默认残留），归零为空，连系统时回退到 'mysql'
            if _meta and _meta.default_database == '' and db_info.get('database') == 'postgres':
                db_info['database'] = ''
            # MongoDB 专用参数从数据源实例透传
            if db_type == 'mongodb':
                for _mk in ('connect_mode', 'auth_source', 'auth_mechanism', 'replica_set',
                            'tls', 'tls_ca_file', 'tls_cert_key_file', 'tls_allow_invalid_certs'):
                    if instance.get(_mk) is not None:
                        db_info[_mk] = instance[_mk]
            # Redis / Redis Cluster 专用参数透传
            if db_type in ('redis', 'redis-cluster'):
                db_info['seed_nodes'] = instance.get('seed_nodes', '')
            # SQL Server (JDBC) 双轨驱动参数透传
            if db_type == 'sqlserver_jdbc':
                for _mk in ('connection_mode', 'jdbc_url', 'instance_name',
                            'encrypt', 'trust_server_certificate'):
                    if instance.get(_mk) is not None:
                        db_info[_mk] = instance[_mk]
                # 旧实例可能没有 connection_mode 字段：sqlserver_jdbc 类型缺省走 jdbc
                if db_info.get('connection_mode') is None:
                    db_info['connection_mode'] = 'jdbc'
            # JDBC 驱动管理：从数据源实例透传 驱动版本(driver_version) 与 Oracle SID 模式(use_sid)
            # （判定用单一注册源 JDBC_PLUGIN_TO_CATALOG，覆盖 6 插件 + 核心内置 dm/gbase）
            if db_type in JDBC_PLUGIN_TO_CATALOG:
                if instance.get('driver_version') is not None:
                    db_info['driver_version'] = instance.get('driver_version')
                if db_type == 'oracle_jdbc':
                    db_info['use_sid'] = bool(instance.get('use_sid', False))
        else:
            # 原有逻辑：使用手动输入的连接信息
            _meta = get_db_meta(db_type)
            _default_db = _meta.default_database if _meta else 'postgres'
            db_info = {
                'ip':        data.get('host', ''),
                'port':      int(data.get('port', 0) or 0),
                'user':      data.get('user', ''),
                'tenant':    data.get('tenant', '') or '',
                'password':  data.get('password', ''),
                'database':  data.get('database') or _default_db,
                'service_name': data.get('service_name', None),
                'sysdba':    bool(data.get('sysdba', False)),  # ← 新增（确保是布尔值）
                'sid':       data.get('sid', None),
                'output_dir': data.get('output_dir', None),
                'zip':       data.get('zip', False),
                'name':      data.get('name', ''),
                'desensitize': bool(data.get('desensitize', False)),
            }
            # 修复：缺省库名为空串的类型（MySQL 家族 / redis 等）若库名被旧 bug 误写为
            # 'postgres'（PG 默认残留），归零为空，连系统时回退到 'mysql'
            if _meta and _meta.default_database == '' and db_info.get('database') == 'postgres':
                db_info['database'] = ''
            # MongoDB 专用参数透传（通过 db_info → ssh_info 传递给插件 getData）
            if db_type == 'mongodb':
                db_info['connect_mode'] = data.get('connect_mode', 'standard')
                db_info['auth_source'] = data.get('auth_source', 'admin')
                db_info['auth_mechanism'] = data.get('auth_mechanism', '')
                db_info['replica_set'] = data.get('replica_set', '')
                db_info['tls'] = bool(data.get('tls', False))
                db_info['tls_ca_file'] = data.get('tls_ca_file', '')
            # Redis / Redis Cluster 专用参数透传
            if db_type in ('redis', 'redis-cluster'):
                db_info['seed_nodes'] = data.get('seed_nodes', '')
                db_info['tls_cert_key_file'] = data.get('tls_cert_key_file', '')
                db_info['tls_allow_invalid_certs'] = bool(data.get('tls_allow_invalid_certs', False))
            # SQL Server (JDBC) 双轨驱动参数透传
            if db_type == 'sqlserver_jdbc':
                for _mk in ('connection_mode', 'jdbc_url', 'instance_name',
                            'encrypt', 'trust_server_certificate'):
                    if data.get(_mk) is not None:
                        db_info[_mk] = data[_mk]
                # 未显式传入 connection_mode 时：sqlserver_jdbc 类型缺省走 jdbc
                if db_info.get('connection_mode') is None:
                    db_info['connection_mode'] = 'jdbc'
            # JDBC 驱动管理：手动输入的 驱动版本(driver_version) 与 Oracle SID 模式(use_sid)
            # （判定用单一注册源 JDBC_PLUGIN_TO_CATALOG，覆盖 6 插件 + 核心内置 dm/gbase）
            if db_type in JDBC_PLUGIN_TO_CATALOG:
                if data.get('driver_version') is not None:
                    db_info['driver_version'] = data.get('driver_version')
                if db_type == 'oracle_jdbc':
                    db_info['use_sid'] = bool(data.get('use_sid', False))

        if data.get('ssh_host'):
            db_info.update({
                'ssh_host':     data.get('ssh_host', ''),
                'ssh_port':     int(data.get('ssh_port', 22)),
                'ssh_user':     data.get('ssh_user', 'root'),
                'ssh_password': data.get('ssh_password', ''),
                'ssh_key_file': data.get('ssh_key_file', ''),
                'ssh_ebpf':     data.get('ssh_ebpf', True),
            })

        task_id = str(uuid.uuid4())
        template_id = data.get('template_id') or None
        tasks[task_id] = {
            'id':            task_id,
            'db_type':       db_type,
            'db_info':       db_info,
            'datasource_id': data.get('datasource_id') or None,
            'template_id':   template_id,
            'chapter_ids':   chapter_ids,
            'inspector':     inspector_name,
            'status':        'running',
            'started_at':    datetime.datetime.now().isoformat()
        }
        db_info['_db_type'] = db_type
        # JVM 类型（HGDB/DB2/SQL Server-JDBC/oracle_jdbc）必须隔离到子进程执行，
        # 否则进程内 JVM 会钉死 gevent hub，导致「开始巡检」后界面/控制台均无输出。
        if db_type in JVM_INSPECTION_DB_TYPES:
            t = threading.Thread(
                target=_run_inspection_subprocess,
                args=(task_id, db_type, db_info, inspector_name, template_id, chapter_ids))
        else:
            t = threading.Thread(
                target=run_inspection_task,
                args=(task_id, db_info, inspector_name, template_id, chapter_ids))
        t.daemon = True
        t.start()
        return jsonify({'ok': True, 'task_id': task_id})
    except Exception as e:
        import traceback, sys
        traceback.print_exc(file=sys.stdout)
        return jsonify({'ok': False, 'msg': repr(e)})



from modules.inspection.dal import (
    get_all_templates, get_template, get_templates_by_db_type,
    create_template, update_template,
    delete_template, get_chapters_by_template, get_chapter, create_chapter,
    update_chapter, delete_chapter, reorder_chapters,
    get_queries_by_chapter, get_query, create_query, update_query,
    delete_query, export_template, import_template,
    init_database as icfg_init_db,
    create_baseline, get_baselines_by_db_type, get_baseline,
    update_baseline, delete_baseline, init_default_baselines, force_reset_baselines,
)

# ── 基线配置 API ───────────────────────────────────────────

@app.route('/api/inspection/baselines', methods=['GET'])
def api_list_baselines():
    """获取基线配置列表"""
    try:
        db_type = request.args.get('db_type', '')
        if db_type:
            rows = get_baselines_by_db_type(db_type, enabled_only=False)
        else:
            conn = __import__('modules.inspection.dal', fromlist=['_']).get_db_connection()
            rows = [dict(r) for r in conn.execute(
                'SELECT * FROM inspection_baseline ORDER BY db_type, param_name'
            ).fetchall()]
            conn.close()
        return jsonify({'success': True, 'data': rows})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/baselines', methods=['POST'])
def api_create_baseline():
    """新增基线配置"""
    try:
        d = request.json
        bid = create_baseline(
            db_type=d.get('db_type', ''), param_name=d.get('param_name', ''),
            query_sql=d.get('query_sql', ''), operator=d.get('operator', '='),
            expected_value=d.get('expected_value'),
            expected_value_min=d.get('expected_value_min'),
            expected_value_max=d.get('expected_value_max'),
            risk_level=d.get('risk_level', 'MEDIUM'),
            description_zh=d.get('description_zh'),
            description_en=d.get('description_en'),
        )
        return jsonify({'success': True, 'message': '创建成功', 'data': {'id': bid}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/baselines/<int:bid>', methods=['GET'])
def api_get_baseline(bid):
    """获取单条基线配置"""
    try:
        row = get_baseline(bid)
        if row:
            return jsonify({'success': True, 'data': row})
        return jsonify({'success': False, 'message': '未找到该基线配置'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/baselines/<int:bid>', methods=['PUT'])
def api_update_baseline(bid):
    """更新基线配置"""
    try:
        d = request.json
        update_baseline(
            bid,
            param_name=d.get('param_name'),
            query_sql=d.get('query_sql'),
            operator=d.get('operator'),
            expected_value=d.get('expected_value'),
            expected_value_min=d.get('expected_value_min'),
            expected_value_max=d.get('expected_value_max'),
            risk_level=d.get('risk_level'),
            description_zh=d.get('description_zh'),
            description_en=d.get('description_en'),
        )
        return jsonify({'success': True, 'message': '更新成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/baselines/<int:bid>', methods=['DELETE'])
def api_delete_baseline(bid):
    """删除基线配置"""
    try:
        delete_baseline(bid)
        return jsonify({'success': True, 'message': '删除成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/baselines/init', methods=['POST'])
def api_init_default_baselines():
    """初始化默认基线配置（仅在首次使用该 db_type 时自动插入，不会覆盖已有数据）"""
    try:
        init_default_baselines()
        return jsonify({'success': True, 'message': '默认基线配置初始化成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/baselines/reset', methods=['POST'])
def api_force_reset_baselines():
    """手动强制重置基线配置。清空指定（或全部）db_type 的基线后重新插入默认值。
    注意：此操作不可逆，用户自定义的基线将被清除。"""
    try:
        icfg_init_db()
        data = request.get_json(silent=True) or {}
        db_type = data.get('db_type')
        force_reset_baselines(db_type=db_type)
        if db_type:
            msg = f'{db_type} 基线配置已重置'
        else:
            msg = '所有基线配置已重置'
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})



# ── 服务器巡检阈值 API ──────────────
@app.route('/api/server_thresholds', methods=['GET'])
def api_get_server_thresholds():
    """获取服务器巡检阈值配置"""
    try:
        import sqlite3, os
        db_path = str(INSPECTION_DB)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT key, value, value_str, description_zh, description_en FROM server_thresholds ORDER BY key')
        rows = cur.fetchall()
        conn.close()
        data = []
        for r in rows:
            data.append({
                'key': r['key'],
                'value': r['value'],
                'value_str': r['value_str'],
                'description_zh': r['description_zh'],
                'description_en': r['description_en'],
            })
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/server_thresholds', methods=['POST'])
def api_save_server_thresholds():
    """保存服务器巡检阈值配置（批量更新）"""
    try:
        import sqlite3, os, datetime
        db_path = str(INSPECTION_DB)
        items = request.get_json(force=True)
        if not isinstance(items, list):
            return jsonify({'success': False, 'message': '请求数据必须是数组'}), 400

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for item in items:
            k = item.get('key')
            v = item.get('value')
            if k is None:
                continue
            cur.execute(
                'UPDATE server_thresholds SET value=?, updated_at=? WHERE key=?',
                (v, now, k)
            )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '服务器阈值配置已保存'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ── 巡检模板 API ───────────────────────────────────────────

@app.route('/api/inspection/templates', methods=['GET'])
def api_list_icfg_templates():
    """获取巡检模板列表，支持按 db_type 过滤"""
    try:
        db_type = request.args.get('db_type')
        # 前端 db_type 简称 → 数据库 template 全称映射
        _DB_TYPE_MAP = {'pg': 'postgresql', 'dm': 'dm8', 'ivorysql': 'ivorysql'}
        if db_type:
            db_type = _DB_TYPE_MAP.get(db_type, db_type)
            rows = get_templates_by_db_type(db_type)
        else:
            rows = get_all_templates()
        return jsonify({'success': True, 'data': rows})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/templates', methods=['POST'])
def api_create_icfg_template():
    """创建巡检模板"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        d = request.json
        tid = create_template(
            db_type=d.get('db_type', ''),
            template_name=d.get('template_name', ''),
            template_name_en=d.get('template_name_en', ''),
            version=d.get('version', 'v1'),
            description=d.get('description', ''),
            is_default=1 if d.get('is_default') else 0,
        )
        return jsonify({'success': True, 'data': {'id': tid}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/templates/<int:tid>', methods=['GET'])
def api_get_icfg_template(tid):
    """获取巡检模板详情"""
    try:
        row = get_template(tid)
        if row:
            return jsonify({'success': True, 'data': row})
        return jsonify({'success': False, 'message': '模板不存在'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/templates/<int:tid>', methods=['PUT'])
def api_update_icfg_template(tid):
    """更新巡检模板"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        d = request.json
        result = update_template(
            tid,
            template_name=d.get('template_name'),
            version=d.get('version'),
            description=d.get('description'),
            is_default=d.get('is_default'),
        )
        if not result:
            return jsonify({'success': False, 'message': '预置模板不能修改名称和版本'})
        return jsonify({'success': True, 'message': '模板更新成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/templates/<int:tid>', methods=['DELETE'])
def api_delete_icfg_template(tid):
    """删除巡检模板（级联删除章节和查询）"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        delete_template(tid)
        return jsonify({'success': True, 'message': '模板删除成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/templates/<int:tid>/export', methods=['GET'])
def api_export_icfg_template(tid):
    """导出巡检模板（含章节和查询）"""
    try:
        data = export_template(tid)
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/templates/import', methods=['POST'])
def api_import_icfg_template():
    """导入巡检模板"""
    try:
        d = request.json
        result = import_template(
            d.get('template_config', {}),
            overwrite=d.get('overwrite', False),
        )
        return jsonify({'success': True, 'message': '模板导入成功 (ID: %d)' % result, 'data': {'id': result}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ── 章节管理 API ───────────────────────────────────────────

@app.route('/api/inspection/templates/<int:tid>/chapters', methods=['GET'])
def api_list_icfg_chapters(tid):
    """获取模板下的章节列表"""
    try:
        rows = get_chapters_by_template(tid)
        for ch in rows:
            queries = get_queries_by_chapter(ch['id'])
            ch['query_count'] = len(queries)
        return jsonify({'success': True, 'data': rows})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/templates/<int:tid>/chapters', methods=['POST'])
def api_create_icfg_chapter(tid):
    """在模板下创建章节"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        d = request.json
        cid = create_chapter(
            template_id=tid,
            chapter_number=d.get('chapter_number', 1),
            chapter_title_zh=d.get('chapter_title_zh', ''),
            chapter_title_en=d.get('chapter_title_en', ''),
            description=d.get('description', ''),
            enabled=1 if d.get('enabled', 1) else 0,
        )
        return jsonify({'success': True, 'data': {'id': cid}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/chapters/<int:cid>', methods=['GET'])
def api_get_icfg_chapter(cid):
    """获取章节详情"""
    try:
        row = get_chapter(cid)
        if row:
            return jsonify({'success': True, 'data': row})
        return jsonify({'success': False, 'message': '章节不存在'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/chapters/<int:cid>', methods=['PUT'])
def api_update_icfg_chapter(cid):
    """更新章节"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        d = request.json
        update_chapter(
            cid,
            chapter_number=d.get('chapter_number'),
            chapter_title_zh=d.get('chapter_title_zh'),
            chapter_title_en=d.get('chapter_title_en'),
            description=d.get('description'),
            enabled=d.get('enabled'),
        )
        return jsonify({'success': True, 'message': '章节更新成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/chapters/<int:cid>', methods=['DELETE'])
def api_delete_icfg_chapter(cid):
    """删除章节（级联删除查询）"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        delete_chapter(cid)
        return jsonify({'success': True, 'message': '章节删除成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/chapters/reorder', methods=['POST'])
def api_reorder_icfg_chapters():
    """章节拖拽排序"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        d = request.json
        template_id = d.get('template_id')
        chapter_ids = d.get('chapter_ids', [])
        reorder_chapters(template_id, chapter_ids)
        return jsonify({'success': True, 'message': '排序保存成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ── SQL 查询管理 API ───────────────────────────────────────

@app.route('/api/inspection/chapters/<int:cid>/queries', methods=['GET'])
def api_list_icfg_queries(cid):
    """获取章节下的查询列表"""
    try:
        rows = get_queries_by_chapter(cid)
        return jsonify({'success': True, 'data': rows})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/chapters/<int:cid>/queries', methods=['POST'])
def api_create_icfg_query(cid):
    """在章节下创建查询"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        d = request.json
        qid = create_query(
            chapter_id=cid,
            query_key=d.get('query_key', ''),
            query_sql=d.get('query_sql', ''),
            query_description_zh=d.get('query_description_zh', ''),
            query_description_en=d.get('query_description_en', ''),
            enabled=1 if d.get('enabled', 1) else 0,
        )
        return jsonify({'success': True, 'data': {'id': qid}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/queries/<int:qid>', methods=['GET'])
def api_get_icfg_query(qid):
    """获取查询详情"""
    try:
        row = get_query(qid)
        if row:
            return jsonify({'success': True, 'data': row})
        return jsonify({'success': False, 'message': '查询不存在'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/queries/<int:qid>', methods=['PUT'])
def api_update_icfg_query(qid):
    """更新查询"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        d = request.json
        update_query(
            qid,
            query_key=d.get('query_key'),
            query_sql=d.get('query_sql'),
            query_description_zh=d.get('query_description_zh'),
            query_description_en=d.get('query_description_en'),
            enabled=d.get('enabled'),
        )
        return jsonify({'success': True, 'message': '查询更新成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/queries/<int:qid>', methods=['DELETE'])
def api_delete_icfg_query(qid):
    """删除查询"""
    try:
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        # 写操作仅管理员（巡检配置管理：创建/保存/删除）
        if not session.get("is_admin", False):
            return jsonify({"success": False, "message": "只有管理员才能执行此操作"}), 403
        delete_query(qid)
        return jsonify({'success': True, 'message': '查询删除成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})



@app.route('/api/start_config_baseline', methods=['POST'])
def api_start_config_baseline():
    """启动配置基线检查任务"""
    try:
        data = request.json
        db_type = data.get('db_type', 'mysql')
        if db_type not in ('mysql', 'pg', 'ivorysql', 'oceanbase'):
            return jsonify({'ok': False, 'msg': 'Only MySQL, PostgreSQL and IvorySQL are supported'})

        db_info = {
            'host': data.get('host', ''),
            'port': int(data.get('port', 0) or (3306 if db_type == 'mysql' else 2881 if db_type == 'oceanbase' else 5432)),
            'user': data.get('user', ''),
            'password': data.get('password', ''),
            'database': data.get('database') or ('ivorysql' if db_type == 'ivorysql' else ('postgres' if db_type == 'pg' else '')),
            'label': data.get('name', data.get('host', 'unknown')),
            'db_type': db_type,
        }

        output_format = data.get('output_format', 'txt')

        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            'id': task_id,
            'db_type': f'config_{db_type}',
            'db_info': db_info,
            'status': 'running',
            'started_at': datetime.datetime.now().isoformat()
        }
        t = threading.Thread(target=run_config_task, args=(task_id, db_info, output_format))
        t.daemon = True
        t.start()
        return jsonify({'ok': True, 'task_id': task_id})
    except Exception as e:
        import traceback, sys
        traceback.print_exc(file=sys.stdout)
        return jsonify({'ok': False, 'msg': repr(e)})


@app.route('/api/test_server_ssh', methods=['POST'])
def api_test_server_ssh():
    """测试服务器 SSH 连接"""
    try:
        data = request.json or {}
        from modules.server.inspect import test_ssh_connection
        ok, msg = test_ssh_connection(
            ssh_host=data.get('ssh_host', ''),
            ssh_port=int(data.get('ssh_port', 22)),
            ssh_user=data.get('ssh_user', 'root'),
            ssh_password=data.get('ssh_password', ''),
            ssh_key_file=data.get('ssh_key_file', ''),
        )
        return jsonify({'ok': ok, 'msg': msg})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/server_inspect', methods=['POST'])
def api_start_server_inspect():
    """启动服务器巡检任务（支持本地/远程）"""
    try:
        data = request.json or {}
        host_type = data.get('host_type', 'remote').strip()

        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            'id': task_id,
            'db_type': 'server',
            'status': 'running',
            'started_at': datetime.datetime.now().isoformat(),
            'log': [],
        }

        if host_type == 'local':
            ssh_info = {'host_type': 'local'}
            t = threading.Thread(target=_run_server_inspect_task, args=(task_id, ssh_info))
            t.daemon = True
            t.start()
            return jsonify({'ok': True, 'task_id': task_id})
        else:
            ssh_host = data.get('ssh_host', '').strip()
            if not ssh_host:
                return jsonify({'ok': False, 'msg': '请填写 SSH 主机地址'})
            ssh_info = {
                'host_type': 'remote',
                'ssh_host': ssh_host,
                'ssh_port': int(data.get('ssh_port', 22)),
                'ssh_user': data.get('ssh_user', 'root'),
                'ssh_password': data.get('ssh_password', ''),
                'ssh_key_file': data.get('ssh_key_file', ''),
            }
            t = threading.Thread(target=_run_server_inspect_task, args=(task_id, ssh_info))
            t.daemon = True
            t.start()
            return jsonify({'ok': True, 'task_id': task_id})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


def _run_server_inspect_task(task_id, ssh_info):
    """后台执行服务器巡检"""
    emit = socketio.emit
    task = tasks.get(task_id)

    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    try:
        host_type = ssh_info.get('host_type', 'remote')

        if host_type == 'local':
            _emit('log', {'msg': f"[{_ts()}] 🖥️ 开始本机巡检..."})
            try:
                from modules.server.inspect import run_local_inspection, generate_server_report
                _emit('log', {'msg': f"[{_ts()}] 📡 正在采集本机系统信息..."})
                result = run_local_inspection()
            except Exception as e:
                _emit('error', {'msg': f"[{_ts()}] ❌ 本机巡检异常: {e}"})
                if task:
                    task['status'] = 'error'
                    task['error'] = str(e)
                return
        else:
            _emit('log', {'msg': f"[{_ts()}] 🖥️ 开始服务器巡检: {ssh_info['ssh_host']}:{ssh_info['ssh_port']}"})

            try:
                from modules.server.inspect import run_server_inspection, generate_server_report

                _emit('log', {'msg': f"[{_ts()}] 🔗 正在建立 SSH 连接..."})
                result = run_server_inspection(
                    ssh_host=ssh_info['ssh_host'],
                    ssh_port=ssh_info['ssh_port'],
                    ssh_user=ssh_info['ssh_user'],
                    ssh_password=ssh_info['ssh_password'],
                    ssh_key_file=ssh_info['ssh_key_file'],
                )
            except Exception as e:
                _emit('error', {'msg': f"[{_ts()}] ❌ 巡检异常: {e}"})
                if task:
                    task['status'] = 'error'
                    task['error'] = str(e)
                return

        if 'error' in result:
            _emit('error', {'msg': f"[{_ts()}] ❌ {result['error']}"})
            if task:
                task['status'] = 'error'
                task['error'] = result['error']
            return

        hostname = result.get('hostname', 'unknown')
        _emit('log', {'msg': f"[{_ts()}] [OK] 连接成功，主机名: {hostname}"})
        _emit('log', {'msg': f"[{_ts()}] 📊 健康评分: {result.get('health_score', 0)} 分 ({result.get('health_status', '')})"})

        for issue in result.get('issues', []):
            _emit('log', {'msg': f"[{_ts()}] [WARN] {issue}"})

        # 网络检测日志
        net = result.get('network', {})
        if net.get('ping'):
            for target, info in net['ping'].items():
                if info.get('ok'):
                    _emit('log', {'msg': f"[{_ts()}] 🌐 Ping {target}: {info.get('latency_ms', 0):.1f} ms"})
                else:
                    _emit('log', {'msg': f"[{_ts()}] ❌ Ping {target}: 超时"})

        # 服务状态日志
        services = result.get('services', [])
        running_count = sum(1 for s in services if s.get('status') == 'running')
        stopped_count = sum(1 for s in services if s.get('status') == 'stopped')
        if services:
            _emit('log', {'msg': f"[{_ts()}] 🔧 服务状态: {running_count} 运行中, {stopped_count} 已停止"})

        _emit('log', {'msg': f"[{_ts()}] 📄 正在生成巡检报告..."})
        ok, report_path = generate_server_report(result)

        if ok:
            _emit('log', {'msg': f"[{_ts()}] [OK] 报告已生成: {os.path.basename(report_path)}"})
        else:
            _emit('log', {'msg': f"[{_ts()}] [WARN] 报告生成失败: {report_path}"})
            report_path = None

        # 保存巡检历史
        try:
            from modules.server.inspect import save_server_inspection
            host = result.get('host', 'localhost') if ssh_info.get('host_type') == 'local' else ssh_info.get('ssh_host', 'unknown')
            port = 0 if ssh_info.get('host_type') == 'local' else ssh_info.get('ssh_port', 22)
            save_server_inspection(
                host=host,
                port=port,
                result=result,
                report_path=report_path or '',
            )
            _emit('log', {'msg': f"[{_ts()}] 💾 巡检历史已保存"})
        except Exception as e:
            _emit('log', {'msg': f"[{_ts()}] [WARN] 历史保存失败: {e}"})

        if task:
            task['status'] = 'done'
            task['result'] = result
            task['report_file'] = report_path
            task['report_name'] = os.path.basename(report_path) if report_path else ''

    except Exception as e:
        import traceback
        traceback.print_exc()
        _emit('error', {'msg': f"[{_ts()}] ❌ 巡检异常: {e}"})
        if task:
            task['status'] = 'error'
            task['error'] = str(e)


# ── 服务器巡检历史 API ──────────────────────────────────────────

@app.route('/api/server_inspect_history', methods=['GET'])
def api_server_inspect_history():
    """获取服务器巡检历史列表"""
    try:
        from modules.server.inspect import get_server_inspection_history
        host = request.args.get('host', '').strip() or None
        limit = int(request.args.get('limit', 50))
        history = get_server_inspection_history(host=host, limit=limit)
        return jsonify({'ok': True, 'history': history})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/server_inspect_history/<int:record_id>', methods=['GET'])
def api_server_inspection_detail(record_id):
    """获取单条巡检详情"""
    try:
        from modules.server.inspect import get_server_inspection_detail
        record = get_server_inspection_detail(record_id)
        if not record:
            return jsonify({'ok': False, 'msg': '记录不存在'}), 404
        return jsonify({'ok': True, 'record': record})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/server_inspect_history/<int:record_id>', methods=['DELETE'])
def api_delete_server_inspection(record_id):
    """删除巡检历史记录"""
    try:
        from modules.server.inspect import delete_server_inspection
        ok = delete_server_inspection(record_id)
        return jsonify({'ok': ok})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/server_inspect_share', methods=['POST'])
def api_server_inspect_share():
    """生成服务器巡检分享链接"""
    try:
        data = request.json or {}
        result = data.get('result', {})
        if not result:
            return jsonify({'ok': False, 'msg': '缺少巡检结果数据'})
        from modules.server.inspect import create_share
        title = f"服务器巡检 - {result.get('hostname', result.get('host', 'unknown'))}"
        share_id = create_share('server_inspect', title, result)
        share_url = f"/share/{share_id}"
        return jsonify({'ok': True, 'share_id': share_id, 'share_url': share_url})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/db_inspect_share', methods=['POST'])
def api_db_inspect_share():
    """生成数据库巡检分享链接"""
    try:
        data = request.json or {}
        result = data.get('result', {})
        task_id = data.get('task_id', '')
        if not result:
            return jsonify({'ok': False, 'msg': '缺少巡检结果数据'})
        # 翻译 issues 中的 i18n key，避免分享页面显示原始 key
        if 'issues' in result and isinstance(result.get('issues'), list):
            for item in result['issues']:
                if isinstance(item, dict):
                    if 'level' in item:
                        item['level'] = _tr(item['level'])
                    if 'description' in item:
                        item['description'] = _tr(item['description'])
                    if 'suggestion' in item:
                        item['suggestion'] = _tr(item['suggestion'])

        from modules.server.inspect import create_share
        db_type = result.get('db_type', '数据库')
        host = result.get('host', result.get('ip', 'unknown'))
        title = f"{db_type}巡检 - {host}"
        share_data = {'task_id': task_id, 'result': result}
        share_id = create_share('db_inspect', title, share_data)
        share_url = f"/share/{share_id}"
        return jsonify({'ok': True, 'share_id': share_id, 'share_url': share_url})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/share/<share_id>')
def view_share(share_id):
    """查看分享的巡检报告（独立页面，无导航）"""
    from modules.server.inspect import get_share
    share = get_share(share_id)
    if not share:
        return render_template('index.html'), 404
    return render_template('share.html',
                         share_id=share_id,
                         share_type=share['share_type'],
                         title=share['title'],
                         data_json=json.dumps(share['data'], ensure_ascii=False),
                         created_at=share['created_at'])


@app.route('/api/share/<share_id>', methods=['GET'])
def api_get_share(share_id):
    """获取分享数据的 API"""
    from modules.server.inspect import get_share
    share = get_share(share_id)
    if not share:
        return jsonify({'ok': False, 'msg': '分享链接不存在或已过期'})
    return jsonify({'ok': True, **share})


@app.route('/api/share/<share_id>', methods=['DELETE'])
def api_delete_share(share_id):
    """删除分享链接"""
    from modules.server.inspect import delete_share
    ok = delete_share(share_id)
    return jsonify({'ok': ok})


@app.route('/api/shares', methods=['GET'])
def api_list_shares():
    """获取所有分享链接列表"""
    try:
        from modules.server.inspect import list_shares
        shares = list_shares()
        return jsonify({'ok': True, 'shares': shares})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/start_index_health', methods=['POST'])
def api_start_index_health():
    """启动索引健康分析任务"""
    try:
        data = request.json
        db_type = data.get('db_type', 'mysql')
        if db_type not in ('mysql', 'pg', 'ivorysql', 'oceanbase'):
            return jsonify({'ok': False, 'msg': 'Only MySQL, PostgreSQL and IvorySQL are supported'})

        db_info = {
            'host': data.get('host', ''),
            'port': int(data.get('port', 0) or (3306 if db_type == 'mysql' else 2881 if db_type == 'oceanbase' else 5432)),
            'user': data.get('user', ''),
            'password': data.get('password', ''),
            'database': data.get('database') or ('ivorysql' if db_type == 'ivorysql' else ('postgres' if db_type == 'pg' else '')),
            'label': data.get('name', data.get('host', 'unknown')),
            'db_type': db_type,
        }

        output_format = data.get('output_format', 'txt')

        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            'id': task_id,
            'db_type': f'index_{db_type}',
            'db_info': db_info,
            'status': 'running',
            'started_at': datetime.datetime.now().isoformat()
        }
        t = threading.Thread(target=run_index_task, args=(task_id, db_info, output_format))
        t.daemon = True
        t.start()
        return jsonify({'ok': True, 'task_id': task_id})
    except Exception as e:
        import traceback, sys
        traceback.print_exc(file=sys.stdout)
        return jsonify({'ok': False, 'msg': repr(e)})


@app.route('/api/task_status/<task_id>')
def api_task_status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({'ok': False, 'msg': _t('webui.task_not_found')}), 404
    offset = int(request.args.get('offset', 0))
    log_list = task.get('log', [])
    result = task.get('result', {})

    # 翻译 result.issues 中的 i18n key，确保前端拿到的是中文
    if isinstance(result, dict) and 'issues' in result and isinstance(result.get('issues'), list):
        for item in result['issues']:
            if isinstance(item, dict):
                if 'level' in item:
                    item['level'] = _tr(item['level'])
                if 'description' in item:
                    item['description'] = _tr(item['description'])
                if 'suggestion' in item:
                    item['suggestion'] = _tr(item['suggestion'])

    resp = {
        'ok': True,
        'status': task.get('status', 'running'),
        'log': log_list[offset:],
        'offset': len(log_list),
        'auto_analyze': task.get('auto_analyze', []),
        'result': result if isinstance(result, dict) else {},
        'report_file': task.get('report_file', ''),
        'report_name': task.get('report_name', ''),
    }
    if isinstance(result, dict):
        resp.update(result)
    return jsonify(resp)


# 定时调度 API
# ══════════════════════════════════════════════════════════════

@app.route('/api/scheduler/jobs', methods=['GET'])
def api_scheduler_list():
    """列出所有定时任务"""
    try:
        sm = _get_scheduler()
        return jsonify({'ok': True, 'jobs': sm.list_jobs()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/scheduler/jobs', methods=['POST'])
def api_scheduler_add():
    """添加定时任务"""
    try:
        data = request.json
        if not data:
            return jsonify({'ok': False, 'error': 'No data provided'}), 400

        # 验证必需字段
        job_id = data.get('id') or str(uuid.uuid4())
        cron = data.get('cron', {})
        if not cron:
            return jsonify({'ok': False, 'error': 'Cron expression required'}), 400

        # 如果指定了数据源，检查 Pro 模块是否可用
        datasource_id = data.get('datasource_id')
        if datasource_id:
            try:
                from modules.pro import get_instance_manager
            except ImportError:
                return jsonify({'ok': False, 'error': '使用数据源需要安装 Pro 模块，请先安装 Pro 版本'}), 400

            job_cfg = {
                'id': job_id,
                'name': data.get('name', '定时巡检'),
                'inspector_name': data.get('inspector_name', 'Jack'),
                'notify_on_done': bool(data.get('notify_on_done', True)),
                'cron': cron,
                'enabled': True,
                'template_id': data.get('template_id') or None,
                'db_info': {
                    'datasource_id': datasource_id,
                    'label': data.get('label', datasource_id),
                }
            }
        else:
            job_cfg = {
                'id': job_id,
                'name': data.get('name', '定时巡检'),
                'db_type': data.get('db_type', 'mysql'),
                'inspector_name': data.get('inspector_name', 'Jack'),
                'notify_on_done': bool(data.get('notify_on_done', True)),
                'cron': cron,
                'enabled': True,
                'template_id': data.get('template_id') or None,
                'db_info': {
                    'label': data.get('label', ''),
                    'db_type': data.get('db_type', 'mysql'),
                    'host': data.get('host', ''),
                    'port': int(data.get('port', 0) or 3306),
                    'user': data.get('user', ''),
                    'password': data.get('password', ''),
                    'database': data.get('database', ''),
                    'service_name': data.get('service_name', None),
                    'sid': data.get('sid', None),
                    'ssh_host': data.get('ssh_host', None),
                    'ssh_port': int(data.get('ssh_port', 22) or 22),
                    'ssh_user': data.get('ssh_user', None),
                    'ssh_password': data.get('ssh_password', ''),
                    'ssh_key_file': data.get('ssh_key_file', ''),
                    # JDBC 驱动管理：驱动版本与 Oracle SID 模式（前端 saveJob 已透传，此前被丢弃）
                    'driver_version': data.get('driver_version') or None,
                    'use_sid': bool(data.get('use_sid', False)),
                }
            }

        sm = _get_scheduler()
        success = sm.add_job(job_cfg)
        if success:
            return jsonify({'ok': True, 'job_id': job_id, 'msg': 'Task added successfully'})
        else:
            return jsonify({'ok': False, 'error': 'Failed to add task (check cron expression)'}), 400
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/scheduler/jobs/<job_id>', methods=['DELETE'])
def api_scheduler_delete(job_id):
    """删除定时任务"""
    try:
        sm = _get_scheduler()
        success = sm.remove_job(job_id)
        return jsonify({'ok': success, 'msg': 'Task deleted' if success else 'Task not found'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/scheduler/jobs/<job_id>/toggle', methods=['POST'])
def api_scheduler_toggle(job_id):
    """启用/禁用定时任务"""
    try:
        data = request.json
        enabled = bool(data.get('enabled', True))
        sm = _get_scheduler()
        sm.toggle_job(job_id, enabled)
        return jsonify({'ok': True, 'enabled': enabled})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/scheduler/jobs/<job_id>/run', methods=['POST'])
def api_scheduler_run_now(job_id):
    """立即执行定时任务（手动触发）"""
    try:
        sm = _get_scheduler()
        success = sm.run_job_now(job_id)
        return jsonify({'ok': success, 'msg': 'Task triggered' if success else 'Task not found'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
# 通知配置 API
# ══════════════════════════════════════════════════════════════

@app.route('/api/notifier/config', methods=['GET'])
def api_notifier_get():
    """获取通知配置（隐藏密码）"""
    try:
        from modules.notify import get_notifier_config
        return jsonify({'ok': True, 'config': get_notifier_config()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/notifier/config', methods=['POST'])
def api_notifier_save():
    """保存通知配置"""
    try:
        data = request.json or {}
        from modules.notify import save_notifier_config
        save_notifier_config(
            email_cfg=data.get('email'),
            webhook_cfg=data.get('webhook')
        )
        return jsonify({'ok': True, 'msg': 'Configuration saved'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/notifier/test-email', methods=['POST'])
def api_notifier_test_email():
    """测试邮件发送（真正发送测试邮件）"""
    try:
        data = request.json or {}
        from modules.notify import EmailNotifier, _load_config

        cfg = data.get('email', {})

        # 密码是 *** 或空时，从已保存配置加载真实密码
        saved = _load_config()
        saved_email = saved.get('email', {})
        if not cfg.get('password') or cfg.get('password') == '***':
            cfg['password'] = saved_email.get('password', '')

        notifier = EmailNotifier(cfg)

        # 收件人：优先用请求中的 test_recipients，否则用配置中的 recipients
        recipients = data.get('test_recipients') or notifier.recipients
        if not recipients:
            return jsonify({'ok': False, 'msg': '请填写收件人邮箱地址'}), 400

        ok, error_msg = notifier.send_test(recipients)
        if ok:
            return jsonify({'ok': True, 'msg': '测试邮件已发送，请查收收件箱'})
        return jsonify({'ok': False, 'msg': error_msg or '邮件发送失败，请检查 SMTP 配置'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


@app.route('/api/notifier/test-webhook', methods=['POST'])
def api_notifier_test_webhook():
    """测试 Webhook"""
    try:
        data = request.json or {}
        from modules.notify import WebhookNotifier
        cfg = data.get('webhook', {})
        notifier = WebhookNotifier(cfg)
        ok, msg = notifier.test_connection()
        return jsonify({'ok': ok, 'msg': msg})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500

# ── 启动 ────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════
#  RAG 知识库 API
# ═══════════════════════════════════════════════════════

def _get_rag_manager():
    """延迟导入并初始化 RAGManager — 全局单例，确保进度字典共享"""
    global _rag_manager
    if _rag_manager is None:
        try:
            from modules.rag.manager import RAGManager
            _rag_manager = RAGManager()
        except Exception as e:
            return None
    return _rag_manager

@app.route('/api/rag/documents', methods=['GET'])
def api_rag_list_documents():
    mgr = _get_rag_manager()
    if mgr is None:
        return jsonify({'ok': False, 'error': 'RAG 模块未加载，请检查 Embedding 服务连接'})
    try:
        docs = mgr.list_documents()
        return jsonify({'ok': True, 'documents': docs})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/rag/documents', methods=['POST'])
def api_rag_upload_document():
    """异步上传文档 — 立即返回 task_id，前端轮询进度"""
    mgr = _get_rag_manager()
    if mgr is None:
        return jsonify({'ok': False, 'error': 'RAG 模块未加载'})
    try:
        if 'file' not in request.files:
            return jsonify({'ok': False, 'error': '未收到文件'})
        f = request.files['file']
        db_type = request.form.get('db_type', 'all')
        title = request.form.get('title', '')
        if not title:
            title = f.filename
        # 保存到临时文件
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(f.filename)[1])
        f.save(tmp.name)
        tmp.close()
        # 启动异步上传，立即返回 task_id（临时文件由后台线程清理）
        task_id = mgr.start_upload(tmp.name, db_type, title, delete_after=True)
        return jsonify({'ok': True, 'task_id': task_id})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/rag/upload-progress/<task_id>', methods=['GET'])
def api_rag_upload_progress(task_id):
    """查询上传任务进度"""
    mgr = _get_rag_manager()
    if mgr is None:
        return jsonify({'ok': False, 'error': 'RAG 模块未加载'})
    try:
        prog = mgr.get_upload_progress(task_id)
        if prog is None:
            return jsonify({'ok': False, 'error': '任务不存在'})
        return jsonify({'ok': True, **prog})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/rag/documents/<path:doc_id>', methods=['DELETE'])
def api_rag_delete_document(doc_id):
    mgr = _get_rag_manager()
    if mgr is None:
        return jsonify({'ok': False, 'error': 'RAG 模块未加载'})
    try:
        ok, _ = mgr.delete_document(doc_id)
        return jsonify({'ok': ok})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# ═══════════════════════════════════════════════════════════
#  实时监控 API
# ═══════════════════════════════════════════════════════════

@app.route('/api/monitor/slow-queries', methods=['GET'])
def api_monitor_slow_queries():
    """获取各数据源慢查询数据"""
    try:
        from modules.monitor.engine import get_monitor_engine
        engine = get_monitor_engine()
        data = engine.get_slow_queries()
        # 转换为列表格式方便前端展示
        items = []
        if data:
            for iid, d in data.items():
                for row in d.get('data', []):
                    items.append({
                        'instance_id': iid,
                        'source': d.get('label', iid),
                        'label': d.get('label', iid),
                        'db_type': d.get('db_type', ''),
                        'error': d.get('error'),
                        'ts': d.get('ts', 0),
                        **row,
                    })
        else:
            # 监控未启动/未采集：补占位项，使前端下拉框初始即可选全部数据源
            try:
                from modules.pro.instance_manager import get_instance_manager
                im = get_instance_manager()
                for inst in im.get_all_instances(mask_password=False):
                    if not inst.get('enabled', True):
                        continue
                    if not inst.get('host'):
                        continue
                    iid = inst.get('id')
                    label = f"{inst.get('name', iid)} ({inst.get('host', '?')}:{inst.get('port', '?')})"
                    items.append({
                        'instance_id': iid,
                        'source': label,
                        'label': label,
                        'db_type': inst.get('db_type', ''),
                        'error': None,
                        'ts': 0,
                        'data': [],
                    })
            except Exception:
                pass
        return jsonify({'ok': True, 'items': items, 'status': engine.get_status()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/monitor/connections', methods=['GET'])
def api_monitor_connections():
    """获取各数据源连接数据"""
    try:
        from modules.monitor.engine import get_monitor_engine
        engine = get_monitor_engine()
        data = engine.get_connections()
        items = []
        if data:
            for iid, d in data.items():
                items.append({
                    'instance_id': iid,
                    'source': d.get('label', iid),
                    'label': d.get('label', iid),
                    'db_type': d.get('db_type', ''),
                    'error': d.get('error'),
                    'ts': d.get('ts', 0),
                    'total': d.get('total', 0),
                    'max_conn': d.get('max_conn', 0),
                    'max_connections': d.get('max_conn', 0),
                    'usage_pct': d.get('usage_pct', 0),
                    'connections': d.get('connections', {}),
                    'sessions': d.get('data', []),
                })
        else:
            # 监控未启动/未采集：补占位项，使前端下拉框初始即可选全部数据源
            try:
                from modules.pro.instance_manager import get_instance_manager
                im = get_instance_manager()
                for inst in im.get_all_instances(mask_password=False):
                    if not inst.get('enabled', True):
                        continue
                    if not inst.get('host'):
                        continue
                    iid = inst.get('id')
                    label = f"{inst.get('name', iid)} ({inst.get('host', '?')}:{inst.get('port', '?')})"
                    items.append({
                        'instance_id': iid,
                        'source': label,
                        'label': label,
                        'db_type': inst.get('db_type', ''),
                        'error': None,
                        'ts': 0,
                        'total': 0,
                        'max_conn': 0,
                        'max_connections': 0,
                        'usage_pct': 0,
                        'connections': {},
                        'sessions': [],
                    })
            except Exception:
                pass
        return jsonify({'ok': True, 'items': items, 'status': engine.get_status()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/monitor/history', methods=['GET'])
def api_monitor_history():
    """获取连接数历史记录（用于热力图）"""
    try:
        from modules.monitor.engine import get_monitor_engine
        engine = get_monitor_engine()
        raw_history = engine.get_conn_history()
        # 后端格式: [{ts, instances: [{id, label, total, max_conn, db_type}]}, ...]
        # 前端期望: [{source, history: [{timestamp, active, total, max_conn, db_type}]}, ...]
        if not raw_history:
            return jsonify({'ok': True, 'history': []})
        # 获取所有数据源（按最新快照的顺序）
        latest = raw_history[-1]
        source_order = []
        source_map = {}
        for inst in latest.get('instances', []):
            src = inst.get('label', inst.get('id', '?'))
            if src not in source_map:
                source_order.append(src)
                source_map[src] = {'db_type': inst.get('db_type', ''), 'max_conn': inst.get('max_conn', 0)}
        # 按数据源聚合
        result = []
        for src in source_order:
            hist_entries = []
            for snapshot in raw_history:
                ts = snapshot.get('ts', 0)
                # 在该时间戳找到对应数据源
                found = None
                for inst in snapshot.get('instances', []):
                    if inst.get('label', inst.get('id', '?')) == src:
                        found = inst
                        break
                if found:
                    total = found.get('total', 0)
                    hist_entries.append({
                        'timestamp': ts,
                        'active': total,
                        'total': total,
                        'max_conn': found.get('max_conn', 0),
                    })
                elif not found and hist_entries:
                    # 数据源已消失，补 null
                    hist_entries.append({'timestamp': ts, 'active': None, 'total': None, 'max_conn': 0})
            result.append({
                'source': src,
                'db_type': source_map[src]['db_type'],
                'history': hist_entries,
            })
        return jsonify({'ok': True, 'history': result})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/monitor/debug', methods=['GET'])
def api_monitor_debug():
    """调试：查看监控引擎内部原始数据"""
    try:
        from modules.monitor.engine import get_monitor_engine
        engine = get_monitor_engine()
        slow = engine.get_slow_queries()
        conn = engine.get_connections()
        status = engine.get_status()
        # 精简输出，只看关键字段
        slow_summary = {}
        for iid, d in slow.items():
            slow_summary[iid] = {
                'label': d.get('label'),
                'error': d.get('error'),
                'data_count': len(d.get('data', [])),
                'ts': d.get('ts', 0),
            }
        conn_summary = {}
        for iid, d in conn.items():
            conn_summary[iid] = {
                'label': d.get('label'),
                'error': d.get('error'),
                'total': d.get('total', 0),
                'max_conn': d.get('max_conn', 0),
                'data_count': len(d.get('data', [])),
            }
        return jsonify({'ok': True, 'status': status, 'slow': slow_summary, 'conn': conn_summary})
    except Exception as e:
        import traceback
        return jsonify({'ok': False, 'error': str(e), 'traceback': traceback.format_exc()})


@app.route('/api/monitor/config', methods=['POST'])
def api_monitor_config():
    """配置监控（启动/停止/修改间隔）"""
    try:
        from modules.monitor.engine import get_monitor_engine
        engine = get_monitor_engine()
        body = request.get_json() or {}
        action = body.get('action')
        if action == 'start':
            interval = body.get('interval')
            engine.start(interval=interval)
        elif action == 'stop':
            engine.stop()
        elif action == 'set_interval':
            interval = body.get('interval')
            if interval:
                engine.set_interval(int(interval))
        elif action == 'trigger':
            engine.trigger_collect()
        return jsonify({'ok': True, 'status': engine.get_status()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# ═══════════════════════════════════════════════════════════
#  AWR 报告分析 API
# ═══════════════════════════════════════════════════════════

@app.route('/api/awr/upload', methods=['POST'])
def api_awr_upload():
    """上传 AWR HTML 报告并解析"""
    try:
        if 'file' not in request.files:
            return jsonify({'ok': False, 'error': '未选择文件'})
        f = request.files['file']
        if not f.filename or f.filename == '':
            return jsonify({'ok': False, 'error': '未选择文件'})
        # 白名单校验
        if not (f.filename.lower().endswith('.html') or f.filename.lower().endswith('.htm')):
            return jsonify({'ok': False, 'error': '仅支持 .html/.htm 格式的 AWR 报告'})

        import tempfile, shutil
        awr_dir = str(paths.AWR_UPLOADS_DIR)
        os.makedirs(awr_dir, exist_ok=True)

        # 保存上传文件
        ext = '.htm' if f.filename.lower().endswith('.htm') else '.html'
        task_id = str(uuid.uuid4())[:8]
        saved_name = f'awr_{task_id}{ext}'
        saved_path = os.path.join(awr_dir, saved_name)
        f.save(saved_path)

        # 校验文件大小（50MB 限制）
        if os.path.getsize(saved_path) > 50 * 1024 * 1024:
            os.remove(saved_path)
            return jsonify({'ok': False, 'error': '文件大小超过 50MB 限制'})

        # 解析 AWR
        from modules.web.awr_parser import parse_awr_report
        awr_data = parse_awr_report(saved_path)

        meta = awr_data.get('metadata', {})
        # 统计解析到的章节
        chapters_found = []
        for key in ['load_profile', 'instance_efficiency', 'time_model', 'fg_wait_events',
                     'system_stats', 'latch_stats', 'file_io', 'sga_memory', 'top_sql',
                     'segment_stats', 'object_stats', 'sql_plan_changes', 'ash']:
            val = awr_data.get(key, {})
            has_data = False
            if isinstance(val, dict):
                for v in val.values():
                    if isinstance(v, list) and v:
                        has_data = True
                        break
                    if isinstance(v, dict) and v.get('rows'):
                        has_data = True
                        break
            elif isinstance(val, list) and val:
                has_data = True
            if has_data:
                chapters_found.append(key)

        return jsonify({
            'ok': True,
            'task_id': task_id,
            'filename': f.filename,
            'metadata': meta,
            'chapters_found': chapters_found,
            'chapter_count': len(chapters_found),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)})


def _build_awr_ai_summary(awr_data, meta):
    """从 AWR 解析数据中构建 AI 诊断摘要（实现已抽至 awr_parser.build_awr_ai_summary，
    与 BIC-QA AWR 分析共用同一实现；保留此别名以兼容既有调用点）。"""
    from modules.web.awr_parser import build_awr_ai_summary
    return build_awr_ai_summary(awr_data, meta)


def _awr_report_steps():
    """AWR 报告生成步骤定义"""
    cfg = _load_ai_config()
    _online_on = cfg.get('online_enabled', False)
    ai_backend = cfg.get('online_backend', 'openai') if _online_on else cfg.get('backend', 'ollama')
    ai_on = ai_backend in ('ollama', 'openai')
    if ai_backend == 'openai' and not _online_on:
        ai_on = False
    steps = ['正在解析 AWR 数据...', '正在生成 Word 报告...', '正在打包下载文件...']
    if ai_on:
        steps.insert(1, 'AI 智能诊断中...')
    return steps


def _run_awr_report_task(report_task_id, awr_task_id):
    """AWR 报告生成的后台工作线程"""
    task = tasks.get(report_task_id)
    if not task:
        return
    steps = task['steps']
    awr_dir = str(paths.AWR_UPLOADS_DIR)
    import glob
    files = glob.glob(os.path.join(awr_dir, f'awr_{awr_task_id}.*'))
    if not files:
        task['status'] = 'error'
        task['error'] = 'AWR 文件不存在或已过期'
        return
    awr_file = files[0]

    try:
        # Step 1: 解析
        task['current_step'] = 0
        from modules.web.awr_parser import parse_awr_report
        awr_data = parse_awr_report(awr_file)

        # Step 2: AI 诊断（如有）
        if len(steps) > 3:
            task['current_step'] = 1
            try:
                meta = awr_data.get('metadata', {})
                ai_summary = _build_awr_ai_summary(awr_data, meta)
                ai_prompt = (
                    "你是一位 Oracle 数据库性能优化专家。请根据以下 AWR 报告关键指标，"
                    "给出具体的性能优化建议（按优先级排序，每条建议包含问题描述和具体操作）：\n\n"
                    f"{ai_summary}\n\n"
                    "请分析以上 AWR 数据，给出优化建议："
                )
                ai_result = _call_llm(ai_prompt, '你是 Oracle 数据库性能调优专家，擅长通过 AWR 报告分析数据库性能问题。')
                if ai_result and ai_result.strip():
                    awr_data['ai_diagnosis'] = ai_result.strip()
            except Exception:
                pass

        # Step 3: 生成 Word
        task['current_step'] = len(steps) - 2
        reports_dir = str(paths.REPORTS_DIR)
        os.makedirs(reports_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        meta = awr_data.get('metadata', {})
        db_name = meta.get('db_name', 'awr')
        snap = meta.get('snap_range', 'unknown')
        report_name = f'DBCheck_AWR_Analysis_{db_name}_{snap}_{timestamp}.docx'.replace('/', '_').replace(' ', '_')
        output_path = os.path.join(reports_dir, report_name)

        from modules.web.build_awr_word_report import build_awr_word_report
        result_path = build_awr_word_report(awr_data, output_path, source_filename=os.path.basename(awr_file))

        # Step 4: 完成
        task['current_step'] = len(steps) - 1
        task['status'] = 'done'
        task['report_file'] = result_path
        task['report_name'] = report_name
    except Exception as e:
        import traceback
        traceback.print_exc()
        task['status'] = 'error'
        task['error'] = str(e)


@app.route('/api/awr/report/<awr_task_id>', methods=['POST'])
def api_awr_generate_report(awr_task_id):
    """启动 AWR Word 报告异步生成任务，返回 report_task_id 供轮询"""
    try:
        awr_dir = str(paths.AWR_UPLOADS_DIR)
        import glob
        files = glob.glob(os.path.join(awr_dir, f'awr_{awr_task_id}.*'))
        if not files:
            return jsonify({'ok': False, 'error': 'AWR 文件不存在或已过期'})

        report_task_id = str(uuid.uuid4())
        steps = _awr_report_steps()
        tasks[report_task_id] = {
            'id': report_task_id,
            'type': 'awr_report',
            'status': 'running',
            'current_step': 0,
            'steps': steps,
            'started_at': datetime.datetime.now().isoformat(),
        }
        t = threading.Thread(target=_run_awr_report_task, args=(report_task_id, awr_task_id))
        t.daemon = True
        t.start()
        return jsonify({'ok': True, 'task_id': report_task_id, 'steps': steps})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/awr/report_status/<report_task_id>', methods=['GET'])
def api_awr_report_status(report_task_id):
    """查询 AWR 报告生成进度"""
    task = tasks.get(report_task_id)
    if not task:
        return jsonify({'ok': False, 'error': '任务不存在'}), 404
    return jsonify({
        'ok': True,
        'status': task.get('status', 'running'),
        'current_step': task.get('current_step', 0),
        'steps': task.get('steps', []),
        'report_file': task.get('report_file'),
        'report_name': task.get('report_name'),
        'error': task.get('error'),
    })


@app.route('/api/awr/status/<task_id>', methods=['GET'])
def api_awr_status(task_id):
    """查询 AWR 任务文件是否存在"""
    awr_dir = str(paths.AWR_UPLOADS_DIR)
    import glob
    files = glob.glob(os.path.join(awr_dir, f'awr_{task_id}.*'))
    if files:
        return jsonify({'ok': True, 'exists': True})
    return jsonify({'ok': True, 'exists': False})


@app.route('/api/rag/ollama-status', methods=['GET'])
def api_rag_ollama_status():
    """向后兼容：检查当前 Embedding 后端连接状态，同时返回 backend 类型"""
    mgr = _get_rag_manager()
    if mgr is None:
        return jsonify({'ok': False, 'error': 'RAG 模块未加载'})
    try:
        # 读取当前 backend 配置
        cfg_path = os.path.join(BASE_DIR, 'dbc_config.json')
        backend = 'ollama'
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f).get('ai', {})
            backend = cfg.get('backend', 'ollama')
        except Exception:
            pass

        ok, msg = mgr.check_embedding_connection()
        import re
        result = {'ok': ok, 'backend': backend}
        if ok:
            m = re.search(r"模型: (.+?), 维度: (\d+)", msg)
            if m:
                result['model'] = m.group(1)
                result['dim'] = int(m.group(2))
            result['msg'] = msg
        else:
            result['error'] = msg
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


# ── Pro 专业版 API ──────────────────────────────────────────
@app.route('/api/pro/status', methods=['GET'])
def api_pro_status():
    """获取 Pro 版本状态（无需许可证验证）"""
    try:
        from modules.pro import is_pro, get_edition
        from modules.pro import get_instance_manager
        from modules.config.version import __version__ as pro_version_str

        im = get_instance_manager()
        stats = im.get_statistics()
        pro_flag = is_pro()
        edition_name = get_edition()

        return jsonify({
            'ok': True,
            'is_pro': pro_flag,
            'edition': edition_name,
            'version': pro_version_str,
            'release_date': '',
            'license': {
                'valid': pro_flag,
                'type': edition_name,
                'expires': '',
                'max_instances': -1 if pro_flag else 0,
                'features': ['all'] if pro_flag else [],
            },
            'instances': stats,
        })
    except ImportError:
        return jsonify({
            'ok': True,
            'is_pro': False,
            'edition': 'community',
            'version': '2.3.8',
            'license': {'valid': False, 'type': 'community', 'features': []},
            'instances': {'total_instances': 0},
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/groups', methods=['GET'])
def api_pro_groups():
    """获取所有分组"""
    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
        groups = im.get_all_groups()
        # 兼容对象和字典两种格式
        result = []
        for g in groups:
            if isinstance(g, dict):
                result.append(g)
            else:
                result.append(g.to_dict())
        return jsonify({
            'ok': True,
            'groups': result,
        })
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/groups', methods=['POST'])
def api_pro_add_group():
    """添加分组"""
    try:
        from modules.pro import get_instance_manager, InstanceGroup
        data = request.get_json()
        group = InstanceGroup(
            name=data.get('name', ''),
            description=data.get('description', ''),
            color=data.get('color', '#378ADD'),
        )
        im = get_instance_manager()
        result = im.add_group(group)
        return jsonify(result)
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/groups/<path:group_name>', methods=['DELETE'])
def api_pro_delete_group(group_name):
    """删除分组"""
    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
        result = im.delete_group(group_name)
        return jsonify(result)
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/statistics', methods=['GET'])
def api_pro_statistics():
    """获取全局统计（按当前身份重算，避免通过聚合数字反推他人资产）"""
    try:
        from modules.pro import get_instance_manager
        from modules.access import principal_from_session, filter_visible
        im = get_instance_manager()
        stats = im.get_statistics()
        user = principal_from_session()
        if user is not None and not user.is_anonymous and not user.is_admin:
            visible = filter_visible(
                user, im.get_all_instances(mask_password=True), 'instance')
            by_type, by_group = {}, {}
            for inst in visible:
                by_type[inst.get('db_type', '')] = by_type.get(inst.get('db_type', ''), 0) + 1
                grp = inst.get('group', 'default')
                by_group[grp] = by_group.get(grp, 0) + 1
            stats['total_instances'] = len(visible)
            stats['enabled_instances'] = len([i for i in visible if i.get('enabled')])
            stats['by_type'] = by_type
            stats['by_group'] = by_group
        stats['global_health_score'] = im.get_global_health_score()
        return jsonify({'ok': True, 'statistics': stats})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/health-score', methods=['GET'])
def api_pro_health_score():
    """获取全局健康评分"""
    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
        score = im.get_global_health_score()
        return jsonify({
            'ok': True,
            'score': score,
            'level': 'critical' if score <= 30 else 'high' if score <= 50 else 'medium' if score <= 70 else 'low' if score <= 85 else 'healthy',
        })
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/dashboard', methods=['GET'])
def api_pro_dashboard():
    """获取首页健康评分仪表盘数据"""
    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()

        # 获取全局评分
        score = im.get_global_health_score()
        level = 'critical' if score <= 30 else 'high' if score <= 50 else 'medium' if score <= 70 else 'low' if score <= 85 else 'healthy'

        # 获取风险统计（从最近巡检记录）
        conn = sqlite3.connect(im.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 获取所有实例最新巡检的风险分布
        cursor.execute("""
            SELECT h.risk_level, COUNT(*) as cnt
            FROM (
                SELECT instance_id, MAX(inspect_time) as latest
                FROM inspection_history GROUP BY instance_id
            ) latest
            JOIN inspection_history h
              ON h.instance_id = latest.instance_id
             AND h.inspect_time = latest.latest
            GROUP BY h.risk_level
        """)
        risk_rows = cursor.fetchall()
        risk_breakdown = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'healthy': 0}
        for row in risk_rows:
            risk_breakdown[row['risk_level'] or 'healthy'] = row['cnt']

        # 获取最新巡检记录
        cursor.execute("""
            SELECT h.instance_id, h.instance_name, h.db_type, h.inspect_time,
                   h.health_score, h.risk_count, h.risk_level
            FROM (
                SELECT instance_id, MAX(inspect_time) as latest
                FROM inspection_history GROUP BY instance_id
            ) latest
            JOIN inspection_history h
              ON h.instance_id = latest.instance_id
             AND h.inspect_time = latest.latest
            LIMIT 10
        """)
        latest_rows = cursor.fetchall()

        # 聚合健康分趋势（按日平均，近 30 天）——来自 instance_trend 真实数据
        cursor.execute("""
            SELECT date, AVG(health_score) as avg_score
            FROM instance_trend
            GROUP BY date ORDER BY date DESC LIMIT 30
        """)
        trend_rows = cursor.fetchall()

        # 实例矩阵（各实例最新巡检真实数据）
        instances = [{
            'instance_id': r['instance_id'],
            'instance_name': r['instance_name'],
            'db_type': r['db_type'],
            'health_score': r['health_score'],
            'risk_level': r['risk_level'],
            'inspect_time': r['inspect_time'],
        } for r in latest_rows]

        conn.close()

        trend = [{'date': r['date'], 'score': round(r['avg_score'])} for r in reversed(trend_rows)]

        # 总实例数和已巡检实例数
        stats = im.get_statistics()
        total_instances = stats.get('total_instances', 0)
        inspected_count = len(latest_rows)

        return jsonify({
            'ok': True,
            'total_score': score,
            'level': level,
            'risk_breakdown': risk_breakdown,
            'trend': trend,
            'instances': instances,
            'total_instances': total_instances,
            'inspected_count': inspected_count,
            'has_history': inspected_count > 0,
        })

    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        import traceback; traceback.print_exc(file=sys.stdout)
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/history', methods=['GET'])
def api_pro_inspection_history():
    """获取巡检历史（派生数据跟随数据源归属）"""
    try:
        from modules.pro import get_instance_manager
        from modules.access import principal_from_session, filter_visible, assert_visible
        instance_id = request.args.get('instance_id')
        limit = int(request.args.get('limit', 100))
        im = get_instance_manager()
        user = principal_from_session()
        if instance_id:
            ok, err = assert_visible(user, 'instance', instance_id)
            if not ok:
                return jsonify({'ok': False, **err}), 403
        else:
            # 未指定数据源时，只保留当前身份可见数据源的历史
            visible_ids = {
                i.get('id') for i in filter_visible(
                    user, im.get_all_instances(mask_password=True), 'instance')
            }
            history = [
                h for h in im.get_inspection_history(None, limit)
                if h.get('instance_id') in visible_ids
            ]
            return jsonify({'ok': True, 'history': history})
        history = im.get_inspection_history(instance_id, limit)
        return jsonify({'ok': True, 'history': history})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/trend/<instance_id>', methods=['GET'])
def api_pro_instance_trend(instance_id):
    """获取实例健康趋势"""
    try:
        from modules.pro import get_instance_manager
        days = int(request.args.get('days', 30))
        im = get_instance_manager()
        trend = im.get_instance_trend(instance_id, days)
        return jsonify({'ok': True, 'trend': trend})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/instances/import', methods=['POST'])
def api_pro_import_instances():
    """从 CSV 批量导入实例"""
    try:
        from modules.pro import get_instance_manager
        data = request.get_json()
        csv_content = data.get('csv_content', '')

        if not csv_content:
            return jsonify({'ok': False, 'error': '请提供 CSV 内容'})

        im = get_instance_manager()
        before = {i.get('id') for i in im.get_all_instances()}
        result = im.batch_add_from_csv(csv_content)
        try:
            from modules.access import principal_from_session, set_owner
            user = principal_from_session()
            for iid in {i.get('id') for i in im.get_all_instances()} - before:
                set_owner('instance', iid, user)
        except Exception as _own_err:
            print('[access] 导入数据源归属登记失败: ' + str(_own_err))
        return jsonify(result)
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  Pro 数据源管理 API
# ══════════════════════════════════════════════════════════════

@app.route('/api/pro/datasources', methods=['GET'])
def api_pro_datasources():
    """获取数据源列表（按当前身份过滤：租户硬隔离 + scope 软隔离）"""
    try:
        from modules.pro import get_instance_manager
        from modules.access import principal_from_session, filter_visible
        im = get_instance_manager()
        user = principal_from_session()
        instances = filter_visible(
            user, im.get_all_instances(mask_password=True), 'instance')
        return jsonify({'ok': True, 'datasources': instances})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>', methods=['GET'])
def api_pro_datasource(instance_id):
    """获取单个数据源（无权访问时返回 403 RESOURCE_NOT_VISIBLE）"""
    try:
        from modules.pro import get_instance_manager
        from modules.access import principal_from_session, assert_visible
        im = get_instance_manager()
        user = principal_from_session()
        ok, err = assert_visible(user, 'instance', instance_id)
        if not ok:
            return jsonify({'ok': False, **err}), 403
        inst = im.get_instance(instance_id, mask_password=False)
        if not inst:
            return jsonify({'ok': False, 'error': '数据源不存在'})
        return jsonify({'ok': True, 'datasource': inst})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>/decrypt', methods=['GET'])
def api_pro_datasource_decrypt(instance_id):
    """获取单个数据源（解密密码，供表单回填）

    明文密码是最高敏感数据，必须先过 PDP 可见性判定再解密。
    """
    try:
        from modules.pro import get_instance_manager
        from modules.pro.instance_manager import _looks_like_encrypted_pwd
        from modules.access import principal_from_session, assert_visible
        im = get_instance_manager()
        user = principal_from_session()
        ok, err = assert_visible(user, 'instance', instance_id)
        if not ok:
            return jsonify({'ok': False, **err}), 403
        inst = im.get_instance_decrypted(instance_id)
        if not inst:
            return jsonify({'ok': False, 'error': '数据源不存在'})
        # 检查密码是否成功解密：对比"解密前的原始值"与"解密后的值"。
        # 只有当原始值确实是密文形态、且解密结果非空且不再是密文时，
        # 才算解密成功。.db_key 变更导致解密失败时 _decrypt_pwd 返回 ''，
        # 此处会判定为 false，前端据此清空密码框并提示重新输入，
        # 避免把密文当明文回填并送去数据库认证。
        raw = im.get_instance(instance_id, mask_password=False) or {}
        raw_pwd = raw.get('password') or ''
        pwd = inst.get('password') or ''
        if _looks_like_encrypted_pwd(raw_pwd):
            password_decrypted = bool(pwd) and not _looks_like_encrypted_pwd(pwd)
        else:
            # 原始值本就不是密文（明文旧数据 / 未设置密码），无需解密
            password_decrypted = True
        if not password_decrypted:
            # 解密失败：绝不把密文回传给前端
            inst['password'] = ''
        return jsonify({
            'ok': True,
            'datasource': inst,
            'password_decrypted': password_decrypted,
        })
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources', methods=['POST'])
def api_pro_datasource_add():
    """新增数据源"""
    try:
        from modules.pro import get_instance_manager
        from modules.pro.instance_manager import DatabaseInstance
        import uuid

        data = request.get_json()
        _db_type = data.get('db_type', 'mysql')
        inst = DatabaseInstance(
            id=str(uuid.uuid4())[:12],
            name=data.get('name', ''),
            db_type=_db_type,
            host=data.get('host', ''),
            port=int(data.get('port', 3306)),
            user=data.get('user', ''),
            password=data.get('password', ''),
            service_name=data.get('service_name', ''),
            database=data.get('database', ''),
            sysdba=bool(data.get('sysdba', False)),
            jdbc_url=data.get('jdbc_url', ''),
            ssh_host=data.get('ssh_host', ''),
            ssh_port=int(data.get('ssh_port', 22)),
            ssh_user=data.get('ssh_user', ''),
            ssh_password=data.get('ssh_password', ''),
            ssh_key_file=data.get('ssh_key_file', ''),
            ssh_enabled=bool(data.get('ssh_enabled', False)),
            gbase_server_name=data.get('gbase_server_name', ''),
            tenant=data.get('tenant', ''),
            seed_nodes=data.get('seed_nodes', ''),
            # SQL Server (JDBC) 类型缺省走 jdbc；其它类型保持 odbc（向后兼容）
            connection_mode=data.get('connection_mode') or ('jdbc' if _db_type == 'sqlserver_jdbc' else 'odbc'),
            encrypt=bool(data.get('encrypt', False)) if _db_type == 'sqlserver_jdbc' else False,
            trust_server_certificate=bool(data.get('trust_server_certificate', True)) if _db_type == 'sqlserver_jdbc' else True,
            driver_version=data.get('driver_version', '') or '',
            use_sid=bool(data.get('use_sid', False)),
            tags=data.get('tags', []),
            group=data.get('group', 'default'),
            description=data.get('description', ''),
        )
        im = get_instance_manager()
        result = im.add_instance(inst)
        # 登记归属：新建数据源默认 private（含密码/地址/SSH，最高敏感）
        if result.get('ok'):
            try:
                from modules.access import principal_from_session, set_owner
                set_owner('instance', result.get('instance_id') or inst.id,
                          principal_from_session())
            except Exception as _own_err:
                print('[access] 数据源归属登记失败: ' + str(_own_err))
        return jsonify(result)
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>', methods=['PUT'])
def api_pro_datasource_update(instance_id):
    """更新数据源（写操作：必须是拥有者或租户管理员）"""
    try:
        from modules.pro import get_instance_manager
        from modules.access import principal_from_session, assert_visible
        data = request.get_json()
        im = get_instance_manager()
        user = principal_from_session()
        ok, err = assert_visible(user, 'instance', instance_id)
        if not ok:
            return jsonify({'ok': False, **err}), 403
        result = im.update_instance(instance_id, data)
        return jsonify(result)
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/db_types', methods=['GET'])
def api_db_types():
    """返回所有可用的数据库类型（内置 + 插件）。

    统一从 dbtype_registry.load_all_db_types() 读元数据，不再硬编码内置字典。
    新增插件类型只要启用即在下拉中出现，无需改此函数。
    """
    result = []
    # 原生驱动类型白名单（走非 JDBC 驱动）：其余一律视为 JDBC 驱动。
    # 由业务事实决定，不在 dbtype_registry 另加字段。新增 JDBC 类数据库无需改这里。
    _NATIVE_DB_TYPES = {
        'tdsqlc_mysql', 'oracle', 'sqlserver',
        'redis', 'redis-cluster', 'mongodb',
    }
    for meta in load_all_db_types():
        result.append({
            'value': meta.db_type,
            'label': meta.label,
            'is_jdbc': meta.db_type not in _NATIVE_DB_TYPES,
            'description': meta.description,
            'icon': meta.icon,
            'emoji': meta.emoji,
            'is_plugin': meta.is_plugin,
            'port': meta.port,
            'user': meta.user,
            'compat_tag': meta.compat_tag,
            'default_database': meta.default_database,
            'show_database_field': meta.show_database_field,
            'sql_editor': meta.sql_editor,
            'protocol': meta.protocol,
        })
    return jsonify(result)



@app.route('/api/pro/datasources/<instance_id>', methods=['DELETE'])
def api_pro_datasource_delete(instance_id):
    """删除数据源，同时清理 history.db 趋势数据"""
    try:
        from modules.pro import get_instance_manager
        from modules.access import principal_from_session, assert_visible, remove_owner
        im = get_instance_manager()
        user = principal_from_session()
        ok, err = assert_visible(user, 'instance', instance_id)
        if not ok:
            return jsonify({'ok': False, **err}), 403
        # 先获取实例信息（删除前）
        inst = im.get_instance(instance_id, mask_password=False)
        # 执行删除（instance_manager 内部已清 inspection_history + instance_trend）
        result = im.delete_instance(instance_id)
        # 同步清理归属登记表，避免主键垃圾堆积
        if result.get('ok'):
            try:
                remove_owner('instance', instance_id)
            except Exception:
                pass
        # 同步清理 history.db（旧趋势系统）
        if result.get('ok') and inst:
            try:
                from modules.inspection.analyzer import HistoryManager
                script_dir = BASE_DIR
                hm = HistoryManager(script_dir)
                db_type = inst.get('db_type', '')
                host = inst.get('host', '')
                port = int(inst.get('port', 3306))
                for i in hm.list_instances():
                    if (i.get('db_type') == db_type and
                            i.get('host') == host and
                            int(i.get('port', 0)) == port):
                        hm.delete_instance(i.get('key', ''))
            except Exception as e:
                print('清理 history.db 失败: ' + str(e))
        return jsonify(result)
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>/test', methods=['POST'])
def api_pro_datasource_test(instance_id):
    """测试数据源连接（会用到真实明文密码，必须先过可见性判定）"""
    try:
        from modules.pro import get_instance_manager
        from modules.access import principal_from_session, assert_visible
        im = get_instance_manager()
        user = principal_from_session()
        ok, err = assert_visible(user, 'instance', instance_id)
        if not ok:
            return jsonify({'ok': False, **err}), 403
        result = im.test_connection(instance_id)
        return jsonify(result)
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/test-connection', methods=['POST'])
def api_pro_datasources_test_conn():
    """测试数据库连接（直接传参）

    统一连接测试分发：注册表 -> 插件 connect_test 复用 -> 兜底。
    新增复用 MySQL/PG/Oracle 等协议的插件只需在 plugin.json 写 connect_test
    字段即可接入，无需在此追加 elif 分支。
    """
    try:
        data = request.get_json() or {}
        db_type = data.get('db_type', 'mysql')
        host = data.get('host', '')

        if not host:
            return jsonify({'ok': False, 'error': '请输入主机地址'})

        result = _conn_test_with_timeout(db_type, data, 'pro')
        return jsonify(result)
    except ImportError as e:
        return jsonify({'ok': False, 'error': f'驱动未安装: {e}'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/pro/datasources/export', methods=['GET'])
def api_pro_datasources_export():
    """导出数据源 CSV（仅导出当前身份可见的数据源）"""
    try:
        from modules.pro import get_instance_manager
        from modules.access import principal_from_session, filter_visible
        im = get_instance_manager()
        user = principal_from_session()
        visible = filter_visible(
            user, im.get_all_instances(mask_password=True), 'instance')
        # export_csv() 是全量导出，这里按可见集合重新生成，避免越权带走他人资产
        import csv
        import io
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'name', 'db_type', 'host', 'port', 'user', 'password',
            'service_name', 'sysdba', 'group', 'tags', 'description'
        ])
        writer.writeheader()
        for inst in visible:
            writer.writerow({
                'name': inst.get('name', ''),
                'db_type': inst.get('db_type', ''),
                'host': inst.get('host', ''),
                'port': inst.get('port', ''),
                'user': inst.get('user', ''),
                'password': '',  # 永不导出密码
                'service_name': inst.get('service_name', ''),
                'sysdba': inst.get('sysdba', 0),
                'group': inst.get('group', 'default'),
                'tags': ','.join(inst.get('tags') or []),
                'description': inst.get('description', ''),
            })
        return jsonify({'ok': True, 'csv': output.getvalue()})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/import', methods=['POST'])
def api_pro_datasources_import():
    """导入数据源 CSV（新建的数据源登记到当前身份名下）"""
    try:
        from modules.pro import get_instance_manager
        data = request.get_json()
        csv_content = data.get('csv_content', '')
        im = get_instance_manager()
        before = {i.get('id') for i in im.get_all_instances()}
        result = im.batch_add_from_csv(csv_content)
        try:
            from modules.access import principal_from_session, set_owner
            user = principal_from_session()
            for iid in {i.get('id') for i in im.get_all_instances()} - before:
                set_owner('instance', iid, user)
        except Exception as _own_err:
            print('[access] 导入数据源归属登记失败: ' + str(_own_err))
        return jsonify(result)
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  Oracle 连接辅助函数（厚模式回退）
# ══════════════════════════════════════════════════════════════

def _connect_oracle_thick_fallback(user, password, dsn, sysdba=False):
    """
    Oracle 连接，自动处理 thin → thick mode 回退（用于 11g 及以下）
    返回 conn 对象，失败则抛异常
    """
    import oracledb
    params = {"user": user, "password": password, "dsn": dsn}
    if sysdba:
        params["mode"] = oracledb.SYSDBA

    try:
        return oracledb.connect(**params)
    except Exception as e:
        err_msg = str(e)
        if 'DPY-3010' not in err_msg and 'DPY-3015' not in err_msg:
            raise
        # thin mode 不支持 11g / 老密码验证器(0x939)，尝试 thick mode
        _thick_ok = False
        try:
            oracledb.init_oracle_client()
            _thick_ok = True
        except Exception:
            pass
        # 使用辅助函数查找 Oracle Client 目录（支持 lib/ 子目录）
        if not _thick_ok:
            _lib_dir = _find_oracle_client_lib_dir()
            if _lib_dir:
                try:
                    oracledb.init_oracle_client(lib_dir=_lib_dir)
                    _thick_ok = True
                except Exception:
                    pass
        # 尝试用户配置的路径
        if not _thick_ok:
            try:
                import json
                with open('dbc_config.json') as f:
                    _cfg = json.load(f)
                _lib_dir = _cfg.get('oracle_client_lib_dir', '')
                if _lib_dir and os.path.isdir(_lib_dir):
                    oracledb.init_oracle_client(lib_dir=_lib_dir)
                    _thick_ok = True
            except Exception:
                pass
        if not _thick_ok:
            raise RuntimeError('Oracle 11g 及以下版本需要 Oracle Instant Client。请通过左侧导航"Oracle Client"设置页下载安装。')
        return oracledb.connect(**params)


# ══════════════════════════════════════════════════════════════
#  SQL 编辑器 API（数据库树 / 对象列表）
# ══════════════════════════════════════════════════════════════

@app.route('/api/pro/datasources/<ds_id>/databases', methods=['GET'])
def api_ds_databases(ds_id):
    """返回某数据源的数据库列表（会用真实密码连库，必须先过可见性判定）"""
    from modules.pro import get_instance_manager
    from modules.access import principal_from_session, assert_visible
    mgr = get_instance_manager()
    user = principal_from_session()
    ok, err = assert_visible(user, 'instance', ds_id)
    if not ok:
        return jsonify({'ok': False, **err}), 403
    inst = mgr.get_instance_decrypted(ds_id)
    if not inst:
        return jsonify({'error': f'数据源不存在: {ds_id}'}), 404

    db_type = inst.get('db_type', '').lower().replace('oracle_full', 'oracle').replace('_jdbc', '')
    if db_type == 'pg':
        db_type = 'postgresql'
    host     = inst.get('host', '')
    port     = int(inst.get('port', 3306))
    user     = inst.get('user', '')
    pwd      = inst.get('password') or ''
    timeout  = 10

    try:
        databases = []
        if db_type == 'oceanbase':
            import pymysql
            _ob_tenant = inst.get('tenant', '') or ''
            _ob_user = user + '@' + _ob_tenant if _ob_tenant else user
            # OceanBase MySQL 租户：用户名须带 @tenant 才能路由到正确租户；
            # 查询主用 INFORMATION_SCHEMA（与 MySQL/TiDB 一致），SHOW DATABASES
            # 在部分 OceanBase 环境会触发 2013，作为兜底。
            conn = pymysql.connect(host=host, port=port, user=_ob_user, password=pwd,
                                   connect_timeout=timeout, charset='utf8mb4')
            cur = conn.cursor()
            try:
                cur.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME")
                databases = [r[0] for r in cur.fetchall()]
            except Exception:
                cur.execute("SHOW DATABASES")
                databases = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type in ('mysql', 'tidb', 'mariadb'):
            import pymysql
            conn = pymysql.connect(host=host, port=port, user=user, password=pwd,
                                   connect_timeout=timeout, charset='utf8mb4')
            cur = conn.cursor()
            cur.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME")
            databases = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type in ('postgresql', 'ivorysql', 'kingbase', 'hgdb'):
            import psycopg2
            # kingbase 默认库名为 kingbase，HGDB 默认库名为 highgo，PG/IvorySQL 为 postgres
            _pg_default_db = 'kingbase' if db_type == 'kingbase' else ('highgo' if db_type == 'hgdb' else ('ivorysql' if db_type == 'ivorysql' else 'postgres'))
            conn = psycopg2.connect(host=host, port=port, user=user, password=pwd,
                                    dbname=_pg_default_db, connect_timeout=timeout)
            cur = conn.cursor()
            cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
            databases = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type == 'gbase':
            # GBase 8s 使用 JDBC 连接（统一走 main_gbase.connect_gbase_jdbc 共享入口，与 Inspector 一致；避免内联 jaydebeapi 绕开驱管理）
            from modules.entrypoints.main_gbase import connect_gbase_jdbc
            _db = inst.get('database', 'gbase01') or 'gbase01'
            _server = inst.get('gbase_server_name', 'gbase01') or 'gbase01'
            _drv = inst.get('driver_version', '') or ''
            try:
                conn, _jdbc_basename = connect_gbase_jdbc(host, int(port), user, pwd,
                                                          database=_db, gbase_server_name=_server,
                                                          driver_version=_drv)
            except Exception as _e:
                raise Exception(f'GBase 8s JDBC 内联连接失败：{_e}')
            cur = conn.cursor()
            # GBase 8s 用 sysmaster:sysdatabases 跨库查询所有数据库
            cur.execute("SELECT name FROM sysmaster:sysdatabases ORDER BY name")
            databases = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type == 'sqlserver':
            try:
                conn_str = _build_sqlserver_conn_str(host, port, user, pwd, timeout=timeout)
                import pyodbc
                conn = pyodbc.connect(conn_str)
                cur = conn.cursor()
                cur.execute("SELECT name FROM sys.databases ORDER BY name")
                databases = [r[0] for r in cur.fetchall()]
                conn.close()
            except Exception as e:
                print(f"[WARN] SQL Server 获取数据库列表失败: {e}")
                databases = []
        elif db_type == 'oracle':
            import oracledb
            svc = inst.get('service_name','') or ''
            sid = inst.get('sid','') or ''
            if svc:
                dsn = oracledb.makedsn(host=host, port=int(port), service_name=svc)
            elif sid:
                dsn = oracledb.makedsn(host=host, port=int(port), sid=sid)
            else:
                return jsonify({'error': 'Oracle 数据源未配置 service_name 或 sid'}), 400
            sysdba = inst.get('sysdba', False)
            conn = _connect_oracle_thick_fallback(user, pwd, dsn, sysdba=sysdba)
            cur = conn.cursor()
            cur.execute("SELECT username FROM all_users ORDER BY username")
            databases = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type == 'dm':
            import dmPython as dm
            conn = dm.connect(user=user, password=pwd, server=host, port=port)
            cur = conn.cursor()
            cur.execute("SELECT username FROM all_users ORDER BY username")
            system_dbs = ('SYSDBA', 'SYSAUDITOR', 'SYS', 'SYSSESSION', 'INFORMATION_SCHEMA')
            databases = [r[0] for r in cur.fetchall() if r[0].upper() not in system_dbs]
            conn.close()
        elif db_type == 'yashandb':
            import yasdb
            conn = yasdb.connect(host=host, port=int(port), user=user, password=pwd)
            cur = conn.cursor()
            cur.execute("SELECT username FROM SYS.ALL_USERS ORDER BY username")
            databases = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type == 'db2':
            from plugins.available.db2_jdbc.main_plugin import get_connection
            # DB2 连接时需指定库；优先用底层 JDBC 的 getMetaData().getCatalogs()
            # 列出实例下的所有数据库（DB2 的 catalog 即 database）。
            db_name = inst.get('database') or 'testdb'
            conn = get_connection(host, int(port), user, pwd, database=db_name)
            try:
                meta = conn.jdbc_conn.getMetaData()
                rs = meta.getCatalogs()
                databases = []
                while rs.next():
                    _c = rs.getString(1)
                    if _c:  # DB2 getCatalogs() 的 catalog 列为 Java null，跳过
                        databases.append(str(_c))
                try:
                    rs.close()
                except Exception:
                    pass
                if not databases:
                    databases = [db_name]
            except Exception:
                # 兜底：DB2 需在连接时指定库，列出已配置库为可接受的最小实现
                databases = [db_name]
            finally:
                conn.close()
        elif db_type == 'mongodb':
            # MongoDB：复用插件 connection_config 构建 URI/client，按 importlib 懒加载规避同名污染
            import importlib.util, os
            _plugin_dir = os.path.join(str(PROJECT_ROOT), 'plugins', 'available', 'mongodb')
            _spec = importlib.util.spec_from_file_location(
                "mongodb_connection_config",
                os.path.join(_plugin_dir, "connection_config.py"),
            )
            _CC = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_CC)
            MongoConnectionConfig = _CC.MongoConnectionConfig
            cfg = MongoConnectionConfig.from_ssh_info(inst)
            cfg.host = host
            cfg.port = int(port) if port else 27017
            cfg.user = user or ""
            cfg.password = pwd or ""
            cfg.database = inst.get('database') or 'admin'
            uri = cfg.build_uri()
            kwargs = cfg.build_client_kwargs()
            from pymongo import MongoClient
            client = MongoClient(uri, **kwargs)
            try:
                databases = client.list_database_names()
                if not databases:
                    databases = [inst.get('database') or 'admin']
            except Exception:
                # 连接失败：不再静默伪装成 admin（否则用户以为可连，
                # 点进 objects 才暴露连接被拒），直接交由外层返回真实错误
                client.close()
                raise
            client.close()
        elif db_type == 'clickhouse':
            from plugins.available.clickhouse_jdbc.main_plugin import get_connection
            conn = get_connection(host, int(port), user, pwd, database='default')
            try:
                cur = conn.cursor()
                cur.execute("SHOW DATABASES")
                databases = [r[0] for r in cur.fetchall()]
                cur.close()
            finally:
                conn.close()
        elif db_type == 'redis':
            import redis
            _kw = dict(
                host=host,
                port=int(port) or 6379,
                password=pwd or None,
                socket_timeout=10,
                socket_connect_timeout=10,
                decode_responses=True,
                encoding_errors='replace',
                protocol=2,
            )
            if user:
                _kw['username'] = user
            r = redis.Redis(**_kw)
            try:
                r.ping()
                try:
                    _count = int((r.config_get('databases') or {}).get('databases', 16))
                except Exception:
                    _count = 16
                databases = []
                for i in range(_count):
                    try:
                        _c = redis.Redis(**_kw)
                        _c.select(i)
                        if _c.dbsize() > 0:
                            databases.append(str(i))
                        _c.close()
                    except Exception:
                        pass
                if not databases:
                    databases = [str(inst.get('database') or 0)]
            finally:
                r.close()
        else:
            return jsonify({'error': f'暂不支持该数据库类型: {db_type}'}), 400

        return jsonify({'databases': databases, 'db_type': db_type})
    except Exception as e:
        return jsonify({'error': str(e), 'db_type': db_type}), 500


@app.route('/api/pro/datasources/<ds_id>/objects', methods=['GET'])
def api_ds_objects(ds_id):
    """返回某数据库下的表、视图等对象"""
    database = request.args.get('database', '').strip()
    if not database:
        return jsonify({'error': '缺少 database 参数'}), 400

    from modules.pro import get_instance_manager
    from modules.access import principal_from_session, assert_visible
    mgr = get_instance_manager()
    user = principal_from_session()
    ok, err = assert_visible(user, 'instance', ds_id)
    if not ok:
        return jsonify({'ok': False, **err}), 403
    inst = mgr.get_instance_decrypted(ds_id)
    if not inst:
        return jsonify({'error': f'数据源不存在: {ds_id}'}), 404

    db_type = inst.get('db_type', '').lower().replace('oracle_full', 'oracle').replace('_jdbc', '')
    if db_type == 'pg':
        db_type = 'postgresql'
    host    = inst.get('host', '')
    port    = int(inst.get('port', 3306))
    user    = inst.get('user', '')
    pwd     = inst.get('password') or ''
    timeout = 10

    try:
        tables, views = [], []
        if db_type == 'oceanbase':
            import pymysql
            _ob_tenant = inst.get('tenant', '') or ''
            _ob_user = user + '@' + _ob_tenant if _ob_tenant else user
            _db_safe = database.replace('`', '``')   # 反引号转义，防注入
            conn = pymysql.connect(host=host, port=port, user=_ob_user, password=pwd,
                                   database=database, connect_timeout=timeout, charset='utf8mb4')
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME",
                    (database,)
                )
                for row in cur.fetchall():
                    if row[1] == 'BASE TABLE':
                        tables.append(row[0])
                    elif row[1] == 'VIEW':
                        views.append(row[0])
            except Exception:
                cur.execute(f"SHOW FULL TABLES FROM `{_db_safe}`")
                for row in cur.fetchall():
                    if row[1] == 'BASE TABLE':
                        tables.append(row[0])
                    elif row[1] == 'VIEW':
                        views.append(row[0])
            conn.close()
        elif db_type in ('mysql', 'tidb', 'mariadb'):
            import pymysql
            conn = pymysql.connect(host=host, port=port, user=user, password=pwd,
                                   database=database, connect_timeout=timeout, charset='utf8mb4')
            cur = conn.cursor()
            cur.execute(
                "SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME",
                (database,)
            )
            for row in cur.fetchall():
                if row[1] == 'BASE TABLE':
                    tables.append(row[0])
                elif row[1] == 'VIEW':
                    views.append(row[0])
            conn.close()
        elif db_type in ('postgresql', 'ivorysql', 'kingbase', 'hgdb'):
            import psycopg2
            conn = psycopg2.connect(host=host, port=port, user=user, password=pwd,
                                    dbname=database, connect_timeout=timeout)
            cur = conn.cursor()
            # 查所有非系统 schema 的表
            cur.execute(
                "SELECT schemaname, tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog','information_schema','pg_toast') "
                "ORDER BY schemaname, tablename"
            )
            tables = [r[0] + '.' + r[1] if r[0] != 'public' else r[1] for r in cur.fetchall()]
            cur.execute(
                "SELECT schemaname, viewname FROM pg_catalog.pg_views "
                "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
                "ORDER BY schemaname, viewname"
            )
            views = [r[0] + '.' + r[1] if r[0] != 'public' else r[1] for r in cur.fetchall()]
            conn.close()
        elif db_type == 'gbase':
            # GBase 8s 使用 JDBC 连接（统一走 main_gbase.connect_gbase_jdbc 共享入口，与 Inspector 一致）
            from modules.entrypoints.main_gbase import connect_gbase_jdbc
            _db = database or 'gbase01'
            _server = inst.get('gbase_server_name', 'gbase01') or 'gbase01'
            _drv = inst.get('driver_version', '') or ''
            try:
                conn, _jdbc_basename = connect_gbase_jdbc(host, int(port), user, pwd,
                                                          database=_db, gbase_server_name=_server,
                                                          driver_version=_drv)
            except Exception as _e:
                raise Exception(f'GBase 8s JDBC 内联连接失败：{_e}')
            cur = conn.cursor()
            # GBase 8s 用 systables 系统表获取表/视图列表
            cur.execute(
                "SELECT tabname, tabtype FROM systables "
                "WHERE tabtype IN ('T','V') ORDER BY tabname"
            )
            for row in cur.fetchall():
                if row[1] == 'T':
                    tables.append(row[0])
                elif row[1] == 'V':
                    views.append(row[0])
            conn.close()
        elif db_type == 'sqlserver':
            conn_str = _build_sqlserver_conn_str(host, port, user, pwd, database=database, timeout=timeout)
            import pyodbc
            conn = pyodbc.connect(conn_str)
            cur = conn.cursor()
            cur.execute(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE='BASE TABLE' AND TABLE_CATALOG=? ORDER BY TABLE_NAME",
                (database,)
            )
            tables = [r[0] for r in cur.fetchall()]
            cur.execute(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE='VIEW' AND TABLE_CATALOG=? ORDER BY TABLE_NAME",
                (database,)
            )
            views = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type == 'oracle':
            import oracledb
            svc = inst.get('service_name','') or ''
            sid = inst.get('sid','') or ''
            if svc:
                dsn = oracledb.makedsn(host=host, port=int(port), service_name=svc)
            elif sid:
                dsn = oracledb.makedsn(host=host, port=int(port), sid=sid)
            else:
                return jsonify({'error': 'Oracle 数据源未配置 service_name 或 sid'}), 400
            sysdba = inst.get('sysdba', False)
            if sysdba:
                conn = oracledb.connect(user=user, password=pwd, dsn=dsn, mode=oracledb.AuthMode.SYSDBA)
            else:
                conn = oracledb.connect(user=user, password=pwd, dsn=dsn)
            cur = conn.cursor()
            owner = (database or '').upper()
            cur.execute(
                "SELECT table_name FROM all_tables WHERE owner = :owner ORDER BY table_name",
                {'owner': owner}
            )
            tables = [r[0] for r in cur.fetchall()]
            cur.execute(
                "SELECT view_name FROM all_views WHERE owner = :owner ORDER BY view_name",
                {'owner': owner}
            )
            views = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type == 'dm':
            import dmPython as dm
            conn = dm.connect(user=user, password=pwd, server=host, port=port)
            cur = conn.cursor()
            owner = (database or '').upper()
            try:
                cur.execute(
                    'SELECT table_name FROM ALL_TABLES WHERE owner = ? ORDER BY table_name',
                    (owner,)
                )
                tables = [r[0] for r in cur.fetchall()]
            except Exception:
                pass
            try:
                cur.execute(
                    'SELECT view_name FROM ALL_VIEWS WHERE owner = ? ORDER BY view_name',
                    (owner,)
                )
                views = [r[0] for r in cur.fetchall()]
            except Exception:
                pass
            conn.close()
        elif db_type == 'yashandb':
            import yasdb
            conn = yasdb.connect(host=host, port=int(port), user=user, password=pwd)
            cur = conn.cursor()
            owner = (database or '').upper()
            try:
                cur.execute(
                    'SELECT table_name FROM SYS.ALL_TABLES WHERE owner = ? ORDER BY table_name',
                    (owner,)
                )
                tables = [r[0] for r in cur.fetchall()]
            except Exception:
                pass
            try:
                cur.execute(
                    'SELECT view_name FROM SYS.ALL_VIEWS WHERE owner = ? ORDER BY view_name',
                    (owner,)
                )
                views = [r[0] for r in cur.fetchall()]
            except Exception:
                pass
            conn.close()
        elif db_type == 'db2':
            from plugins.available.db2_jdbc.main_plugin import get_connection
            # DB2 系统目录 SYSCAT.TABLES 拆分表(T)/视图(V)，排除系统 schema
            conn = get_connection(host, int(port), user, pwd, database=database)
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT TABNAME, TYPE FROM SYSCAT.TABLES "
                    "WHERE TABSCHEMA NOT LIKE 'SYS%' ORDER BY TABNAME"
                )
                for row in cur.fetchall():
                    if row[1] == 'T':
                        tables.append(row[0])
                    elif row[1] == 'V':
                        views.append(row[0])
                cur.close()
            except Exception:
                pass
            finally:
                conn.close()
        elif db_type == 'mongodb':
            # MongoDB：复用插件 connection_config 构建 URI/client，按 importlib 懒加载规避同名污染
            import importlib.util, os
            _plugin_dir = os.path.join(str(PROJECT_ROOT), 'plugins', 'available', 'mongodb')
            _spec = importlib.util.spec_from_file_location(
                "mongodb_connection_config",
                os.path.join(_plugin_dir, "connection_config.py"),
            )
            _CC = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_CC)
            MongoConnectionConfig = _CC.MongoConnectionConfig
            cfg = MongoConnectionConfig.from_ssh_info(inst)
            cfg.host = host
            cfg.port = int(port) if port else 27017
            cfg.user = user or ""
            cfg.password = pwd or ""
            cfg.database = inst.get('database') or 'admin'
            uri = cfg.build_uri()
            kwargs = cfg.build_client_kwargs()
            from pymongo import MongoClient
            client = MongoClient(uri, **kwargs)
            try:
                db = client[database]
                try:
                    tables = db.list_collection_names(filter={'type': 'standard'})
                    views = db.list_collection_names(filter={'type': 'view'})
                except Exception:
                    # 老版本 pymongo 不识别 filter 参数，退化为列出全部集合
                    tables = db.list_collection_names()
                    views = []
            finally:
                client.close()
        elif db_type == 'clickhouse':
            from plugins.available.clickhouse_jdbc.main_plugin import get_connection
            _db_safe = str(database).replace("'", "''")
            conn = get_connection(host, int(port), user, pwd, database=database or 'default')
            try:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT name, engine FROM system.tables "
                    f"WHERE database = '{_db_safe}' ORDER BY name"
                )
                for name, engine in cur.fetchall():
                    if 'View' in (engine or ''):
                        views.append(name)
                    else:
                        tables.append(name)
                cur.close()
            finally:
                conn.close()
        elif db_type == 'redis':
            import redis
            _db_idx = int(database) if str(database).isdigit() else int(inst.get('database') or 0)
            _kw = dict(
                host=host,
                port=int(port) or 6379,
                password=pwd or None,
                db=_db_idx,
                socket_timeout=10,
                socket_connect_timeout=10,
                decode_responses=True,
                encoding_errors='replace',
                protocol=2,
            )
            if user:
                _kw['username'] = user
            r = redis.Redis(**_kw)
            try:
                r.ping()
                _seen = 0
                _limit = 500
                _cursor = 0
                while _seen < _limit:
                    _cursor, _keys = r.scan(cursor=_cursor, count=200)
                    for _k in _keys:
                        if _seen >= _limit:
                            break
                        tables.append(_k)
                        _seen += 1
                    if _cursor == 0:
                        break
                views = []
            finally:
                r.close()
        else:
            return jsonify({'error': f'暂不支持该数据库类型: {db_type}'}), 400

        return jsonify({
            'database': database,
            'db_type':  db_type,
            'tables':   sorted(tables),
            'views':    sorted(views),
        })
    except Exception as e:
        return jsonify({'error': str(e), 'db_type': db_type}), 500


# ══════════════════════════════════════════════════════════════
#  SQL 编辑器 - 执行查询 API
# ══════════════════════════════════════════════════════════════

# 游标存储（key: cursor_id, value: dict with conn, cursor, columns, offset）
_sql_cursors = {}
_SQL_CURSOR_TTL = 300  # 游标超时 5 分钟


def _cleanup_expired_cursors():
    """清理过期的 SQL 游标"""
    global _sql_cursors
    now = time.time()
    expired = [k for k, v in _sql_cursors.items() if now - v.get('created_at', 0) > _SQL_CURSOR_TTL]
    for k in expired:
        try:
            _sql_cursors[k]['cursor'].close()
            _sql_cursors[k]['conn'].close()
        except Exception:
            pass
        del _sql_cursors[k]


import re as _re

# ── SQL 编辑器只读白名单（仅允许查询类命令）────────────────────
_SQL_READONLY_ALLOW = frozenset({
    'SELECT', 'SHOW', 'EXPLAIN', 'WITH', 'DESCRIBE', 'DESC', 'VALUES',
})


def _strip_sql_comments_and_literals(sql):
    """去掉注释与字符串字面量，便于安全切分语句（仅用于静态白名单检查，不改变语义）。"""
    out = []
    i, n = 0, len(sql)
    in_str = None
    while i < n:
        c = sql[i]
        if in_str:
            if c == in_str:
                if i + 1 < n and sql[i + 1] == in_str:  # 转义引号 '' / ""
                    i += 2; continue
                in_str = None
            i += 1; continue
        if c in ("'", '"', '`'):
            in_str = c; out.append(c); i += 1; continue
        if c == '-' and i + 1 < n and sql[i + 1] == '-':  # 行注释 --
            j = sql.find('\n', i); i = n if j == -1 else j; continue
        if c == '#':  # MySQL 行注释 #
            j = sql.find('\n', i); i = n if j == -1 else j; continue
        if c == '/' and i + 1 < n and sql[i + 1] == '*':  # 块注释 /* */
            j = sql.find('*/', i + 2); i = n if j == -1 else j + 2; continue
        out.append(c); i += 1
    return ''.join(out)


def _assert_readonly_sql(sql):
    """校验 SQL 仅含只读查询命令；否则抛出 ValueError（含拒绝原因）。

    静态白名单：剥离注释/字符串后按分号切分，每条语句首关键词必须命中白名单。
    任意一条非只读语句（含注入的第二条 DROP/DELETE 等）都会被整体拒绝。
    """
    cleaned = _strip_sql_comments_and_literals(sql)
    stmts = [s.strip() for s in cleaned.split(';') if s.strip()]
    if not stmts:
        raise ValueError('未检测到有效 SQL 语句')
    for idx, st in enumerate(stmts, 1):
        m = _re.match(r'\w+', st)
        if not m:
            raise ValueError(f'第 {idx} 条语句无法识别，已拒绝执行（只读模式）')
        lead = m.group(0).upper()
        if lead not in _SQL_READONLY_ALLOW:
            raise ValueError(
                f'只读模式：不允许执行「{lead}」类命令（仅允许 '
                f'SELECT / SHOW / EXPLAIN / WITH / DESCRIBE / VALUES 等查询命令）'
            )
    return True


# ── Redis 只读命令管控（仅允许查询类，禁止写/删除/管理类）──────
_REDIS_WRITE_COMMANDS = frozenset({
    # 键/删除
    'DEL', 'UNLINK', 'FLUSHALL', 'FLUSHDB', 'SWAPDB', 'MOVE', 'COPY', 'RESTORE', 'MIGRATE',
    # 字符串写
    'SET', 'SETEX', 'SETNX', 'PSETEX', 'GETSET', 'GETEX', 'GETDEL', 'MSET', 'MSETNX',
    'APPEND', 'INCR', 'INCRBY', 'INCRBYFLOAT', 'DECR', 'DECRBY', 'SETBIT', 'SETRANGE',
    'BITFIELD', 'BITOP',
    # 过期
    'EXPIRE', 'EXPIREAT', 'PEXPIRE', 'PEXPIREAT', 'PERSIST',
    # 重命名
    'RENAME', 'RENAMENX',
    # List
    'LPUSH', 'LPUSHX', 'RPUSH', 'RPUSHX', 'LPOP', 'RPOP', 'RPOPLPUSH', 'LINSERT',
    'LSET', 'LTRIM', 'LMOVE', 'BLMOVE', 'BLPOP', 'BRPOP', 'BRPOPLPUSH',
    # Set
    'SADD', 'SREM', 'SPOP', 'SMOVE', 'SDIFFSTORE', 'SINTERSTORE', 'SUNIONSTORE',
    # Sorted Set
    'ZADD', 'ZREM', 'ZINCRBY', 'ZPOPMIN', 'ZPOPMAX', 'ZINTERSTORE', 'ZUNIONSTORE',
    'ZDIFFSTORE', 'ZRANGESTORE',
    # Hash
    'HSET', 'HSETNX', 'HMSET', 'HDEL', 'HINCRBY', 'HINCRBYFLOAT',
    # Stream
    'XADD', 'XDEL', 'XTRIM', 'XGROUP', 'XACK', 'XCLAIM', 'XAUTOCLAIM', 'XSETID',
    # 脚本/函数（可写）
    'EVAL', 'EVALSHA', 'FCALL', 'FUNCTION', 'SCRIPT',
    # 地理/聚合写
    'GEOADD', 'GEORADIUS', 'GEORADIUSBYMEMBER', 'GEOSEARCHSTORE',
    'PFADD', 'PFMERGE', 'SORT',
    # 管理/危险
    'CLIENT', 'CONFIG', 'DEBUG', 'SHUTDOWN', 'SAVE', 'BGSAVE', 'BGREWRITEAOF',
    'REPLICAOF', 'SLAVEOF', 'FAILOVER', 'RESET', 'ACL', 'MODULE', 'CLUSTER',
})


def _split_redis_command(sql):
    """将单行 Redis 命令切分为参数列表（兼容引号）。"""
    import shlex
    try:
        return shlex.split(sql)
    except ValueError:
        return sql.split()


def _format_redis_value(v, _depth=0):
    """将 redis-py 返回（多为 bytes/嵌套）转为可展示字符串。"""
    if isinstance(v, bytes):
        try:
            return v.decode('utf-8', 'replace')
        except Exception:
            return repr(v)
    if isinstance(v, (list, tuple)):
        if _depth > 4:
            return '[...]'
        return '[' + ', '.join(_format_redis_value(x, _depth + 1) for x in v) + ']'
    if isinstance(v, dict):
        if _depth > 4:
            return '{...}'
        return '{' + ', '.join(
            f'{_format_redis_value(k, _depth + 1)}: {_format_redis_value(val, _depth + 1)}'
            for k, val in v.items()
        ) + '}'
    if v is None:
        return '(nil)'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    return str(v)


@app.route('/api/execute_sql', methods=['POST'])
def api_execute_sql():
    """
    SQL 编辑器执行查询
    参数:
        - instance_id: 数据源 ID
        - sql: SQL 语句（初次执行时提供）
        - database: 数据库名（可选）
        - cursor_id: 游标 ID（翻页时提供，无需 sql）
    """
    # 清理过期游标
    _cleanup_expired_cursors()

    data = request.get_json()
    cursor_id = data.get('cursor_id')

    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
    except ImportError:
        return jsonify({'error': 'Pro 模块未安装'}), 500

    # ── 游标翻页 ──────────────────────────────────────────────
    if cursor_id and cursor_id in _sql_cursors:
        cur_info = _sql_cursors[cursor_id]
        cur_info['created_at'] = time.time()  # 刷新过期时间
        try:
            cursor = cur_info['cursor']
            rows = cursor.fetchmany(200)
            has_more = len(rows) >= 200
            new_cursor_id = cursor_id if has_more else None
            return jsonify({
                'columns': cur_info['columns'],
                'rows': rows,
                'cursor_id': new_cursor_id,
                'has_more': has_more,
                'row_count': len(rows)
            })
        except Exception as e:
            # 游标失效
            try:
                cur_info['cursor'].close()
                cur_info['conn'].close()
            except Exception:
                pass
            _sql_cursors.pop(cursor_id, None)
            return jsonify({'error': f'游标已失效: {e}'}), 400

    # ── 新查询 ────────────────────────────────────────────────
    instance_id = data.get('instance_id', '')
    sql = data.get('sql', '').strip()
    database = data.get('database', '').strip()

    if not instance_id:
        return jsonify({'error': '数据源 ID 不能为空'}), 400
    if not sql:
        return jsonify({'error': 'SQL 不能为空'}), 400

    db_info = im.get_instance_decrypted(instance_id)
    if not db_info:
        return jsonify({'error': '数据源不存在'}), 404

    db_type = (db_info.get('db_type', '') or '').lower().replace('oracle_full', 'oracle').replace('_jdbc', '')
    if db_type == 'pg':
        db_type = 'postgresql'
    host = db_info.get('host', '')
    port = int(db_info.get('port', 3306))
    user = db_info.get('user', '')
    pwd = db_info.get('password') or ''

    try:
        conn = None
        cursor = None
        if db_type in ('redis', 'redis-cluster'):
            # ── Redis：仅允许只读命令 ────────────────────────────────
            _tokens = _split_redis_command(sql)
            if not _tokens:
                return jsonify({'error': '命令不能为空'}), 400
            _cmd = _tokens[0].upper()
            if _cmd in _REDIS_WRITE_COMMANDS:
                return jsonify({
                    'error': f'只读模式：不允许执行「{_cmd}」写/删除/管理类命令'
                }), 403
            _db_raw = str(db_info.get('database') or '0')
            _db_idx = int(_db_raw) if _db_raw.isdigit() else 0
            _kw = dict(
                host=host, port=int(port) or 6379, password=pwd or None,
                db=_db_idx, socket_timeout=10, socket_connect_timeout=10,
                decode_responses=False, encoding_errors='replace', protocol=2,
            )
            if user:
                _kw['username'] = user
            if db_type == 'redis-cluster':
                from redis.cluster import RedisCluster
                _r = RedisCluster(**_kw)
            else:
                import redis
                _r = redis.Redis(**_kw)
            try:
                _raw = _r.execute_command(*_tokens)
            finally:
                try:
                    _r.close()
                except Exception:
                    pass
            _val = _format_redis_value(_raw)
            if len(_val) > 20000:
                _val = _val[:20000] + ' ...(结果已截断)'
            return jsonify({
                'columns': ['command', 'result'],
                'rows': [[sql, _val]],
                'row_count': 1, 'has_more': False,
            })

        # ── 关系型数据库：强制只读模式，禁止 DELETE/UPDATE/INSERT/DROP/ALTER 等写与 DDL ──
        try:
            _assert_readonly_sql(sql)
        except ValueError as e:
            return jsonify({'error': str(e)}), 403

        if db_type in ('mysql', 'tidb', 'mariadb', 'oceanbase', 'tdsqlc_mysql'):
            import pymysql
            db_name = database or 'INFORMATION_SCHEMA'
            conn = pymysql.connect(
                host=host, port=port, user=user, password=pwd,
                database=db_name, charset='utf8mb4', connect_timeout=10
            )
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(200)
            has_more = len(rows) >= 200

        elif db_type in ('postgresql', 'ivorysql', 'kingbase', 'hgdb'):
            import psycopg2
            db_name = database or ('highgo' if db_type == 'hgdb' else ('ivorysql' if db_type == 'ivorysql' else 'postgres'))
            conn = psycopg2.connect(
                host=host, port=port, user=user, password=pwd,
                dbname=db_name, connect_timeout=10
            )
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(200)
            has_more = len(rows) >= 200

        elif db_type == 'oracle':
            import oracledb
            svc = db_info.get('service_name', '') or ''
            sid = db_info.get('sid', '') or ''
            if svc:
                dsn = oracledb.makedsn(host=host, port=int(port), service_name=svc)
            elif sid:
                dsn = oracledb.makedsn(host=host, port=int(port), sid=sid)
            else:
                dsn = f"{host}:{port}/orcl"
            sysdba = db_info.get('sysdba', False)
            conn = _connect_oracle_thick_fallback(user, pwd, dsn, sysdba=sysdba)
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(200)
            has_more = len(rows) >= 200

        elif db_type == 'sqlserver':
            conn_str = _build_sqlserver_conn_str(host, port, user, pwd, database=database, timeout=10)
            import pyodbc
            conn = pyodbc.connect(conn_str)
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(200)
            has_more = len(rows) >= 200

        elif db_type == 'dm':
            import dmPython
            conn = dmPython.connect(user=user, password=pwd, server=host, port=port)
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(200)
            has_more = len(rows) >= 200

        elif db_type == 'yashandb':
            import yasdb
            conn = yasdb.connect(host=host, port=int(port), user=user, password=pwd)
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(200)
            has_more = len(rows) >= 200

        elif db_type == 'db2':
            from plugins.available.db2_jdbc.main_plugin import get_connection
            db_name = database or 'testdb'
            conn = get_connection(host, port, user, pwd, database=db_name)
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(200)
            has_more = len(rows) >= 200

        elif db_type == 'clickhouse':
            from plugins.available.clickhouse_jdbc.main_plugin import get_connection
            db_name = database or 'default'
            conn = get_connection(host, int(port), user, pwd, database=db_name)
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(200)
            has_more = len(rows) >= 200

        else:
            return jsonify({'error': f'不支持的数据库类型: {db_type}'}), 400

        # 判断是否为 SELECT 查询（有列描述说明是查询）
        if not columns:
            # DDL/DML，执行成功但没有返回结果集
            if conn:
                conn.commit()
                cursor.close()
                conn.close()
            return jsonify({'columns': [], 'rows': [], 'message': '执行成功', 'row_count': 0})

        # 构建结果
        row_dicts = []
        for row in rows:
            if isinstance(row, dict):
                row_dicts.append([row.get(c) for c in columns])
            else:
                row_dicts.append(list(row))

        total_count = len(row_dicts)
        new_cursor_id = None
        if has_more:
            new_cursor_id = str(uuid.uuid4())
            _sql_cursors[new_cursor_id] = {
                'conn': conn,
                'cursor': cursor,
                'columns': columns,
                'created_at': time.time()
            }
        else:
            cursor.close()
            conn.close()

        return jsonify({
            'columns': columns,
            'rows': row_dicts,
            'cursor_id': new_cursor_id,
            'has_more': has_more,
            'row_count': total_count
        })

    except Exception as e:
        # 清理连接
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  Pro 规则管理 API
# ══════════════════════════════════════════════════════════════

@app.route('/api/pro/rules', methods=['GET'])
def api_pro_rules():
    """获取规则列表"""
    try:
        from modules.pro.rule_engine import get_rule_engine
        db_type = request.args.get('db_type', None)
        engine = get_rule_engine()
        rules = engine.list_rules(db_type)
        return jsonify({'ok': True, 'rules': rules})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/rules', methods=['POST'])
def api_pro_rules_add():
    """新增自定义规则"""
    try:
        from modules.pro.rule_engine import get_rule_engine
        data = request.get_json()
        engine = get_rule_engine()
        rule_id = data.get('id', '')
        if not rule_id:
            return jsonify({'ok': False, 'error': '规则 ID 不能为空'})
        ok = engine.save_custom_rule(data)
        if ok:
            return jsonify({'ok': True, 'message': '规则已保存'})
        return jsonify({'ok': False, 'error': '保存失败'})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/rules/<rule_id>', methods=['DELETE'])
def api_pro_rules_delete(rule_id):
    """删除自定义规则"""
    try:
        from modules.pro.rule_engine import get_rule_engine
        engine = get_rule_engine()
        ok = engine.delete_custom_rule(rule_id)
        if ok:
            return jsonify({'ok': True, 'message': '规则已删除'})
        return jsonify({'ok': False, 'error': '规则不存在'})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/rules/<rule_id>/toggle', methods=['POST'])
def api_pro_rules_toggle(rule_id):
    """启用/禁用规则（真正的切换：取反当前状态）"""
    try:
        from modules.pro.rule_engine import get_rule_engine
        engine = get_rule_engine()
        # 先查当前状态，再取反
        rule = engine.get_rule(rule_id)
        if rule is None:
            return jsonify({'ok': False, 'error': '规则不存在'}), 404
        new_enabled = not rule.get('enabled', False)
        engine.toggle_rule(rule_id, new_enabled)
        return jsonify({'ok': True, 'enabled': new_enabled, 'message': '设置已保存'})
    except ImportError as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  备份管理 API
# ══════════════════════════════════════════════════════════════

@app.route('/api/pro/backup/run', methods=['POST'])
def api_pro_backup_run():
    """执行备份"""
    try:
        from modules.pro.backup import get_backup_manager
        from modules.pro.instance_manager import _looks_like_encrypted_pwd
        data = request.get_json()
        conn_info = data.get('conn_info', {})
        pwd = conn_info.get('password', '')

        # 诊断信息（密文识别复用 instance_manager 的准确判定，
        # 旧的 "len>50 且含 = 且含 /" 启发式会漏判不含 '/' 的密文）
        diag = {
            'password_length': len(pwd),
            'password_masked': (pwd[:2] + '***') if pwd else '(empty)',
            'likely_encrypted': _looks_like_encrypted_pwd(pwd),
        }

        bm = get_backup_manager()
        result = bm.backup(
            instance_id=data.get('instance_id', ''),
            db_type=data.get('db_type', ''),
            conn_info=conn_info,
            backup_type=data.get('backup_type', 'full'),
            databases=data.get('databases', None),
            tables=data.get('tables', None),
            instance_name=data.get('instance_name', ''),
        )
        resp = {'ok': result.success, 'message': result.message,
                'result': result.to_dict()}
        if not result.success:
            resp['diagnostic'] = diag
            # 附加 raw 输出用于诊断
            raw = result.to_dict()
            if raw.get('message') and 'mysql:' in raw.get('message', ''):
                resp['diagnostic']['mysql_ok'] = True
            resp['cmd_hint'] = (
                f"docker exec {conn_info.get('docker',{}).get('container','?')} "
                f"mysql -hlocalhost -P3306 -u{conn_info.get('user','?')} "
                f"-p*** -e 'SHOW DATABASES' -- 在宿主机执行验证"
            ) if conn_info.get('exec_mode') == 'docker' else None
        return jsonify(resp)
    except ImportError as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/backup/list', methods=['GET'])
def api_pro_backup_list():
    """备份文件列表"""
    try:
        from modules.pro.backup import get_backup_manager
        instance_id = request.args.get('instance_id', '')
        db_type = request.args.get('db_type', '')
        bm = get_backup_manager()
        backups = bm.list_backups(instance_id, db_type)
        return jsonify({'ok': True, 'backups': backups})
    except ImportError as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/backup/delete', methods=['POST'])
def api_pro_backup_delete():
    """删除备份"""
    try:
        data = request.get_json()
        timestamp = data.get('timestamp', '')
        instance_id = data.get('instance_id', '')
        import shutil
        path = os.path.join("backups", instance_id, timestamp)
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
            return jsonify({'ok': True, 'message': '已删除'})
        return jsonify({'ok': False, 'error': '备份不存在'}), 404
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/backup/restore', methods=['POST'])
def api_pro_backup_restore():
    """恢复备份"""
    try:
        from modules.pro.backup import get_backup_manager
        data = request.get_json()
        bm = get_backup_manager()
        # 解析相对路径为绝对路径
        backup_file = data.get('backup_file', '')
        if backup_file and not os.path.isabs(backup_file):
            backup_file = os.path.join("backups", backup_file)
        result = bm.restore(
            backup_file=backup_file,
            db_type=data.get('db_type', ''),
            conn_info=data.get('conn_info', {}),
            target_db=data.get('target_db', None),
        )
        return jsonify({'ok': result.success, 'message': result.message})
    except ImportError as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/backup/history', methods=['GET'])
def api_pro_backup_history():
    """备份历史记录"""
    try:
        from modules.pro.backup import get_backup_manager
        instance_id = request.args.get('instance_id', None)
        limit = int(request.args.get('limit', 50))
        bm = get_backup_manager()
        history = bm.get_history(instance_id, limit)
        return jsonify({'ok': True, 'history': history})
    except ImportError as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/backup/statistics', methods=['GET'])
def api_pro_backup_statistics():
    """备份统计"""
    try:
        from modules.pro.backup import get_backup_manager
        bm = get_backup_manager()
        stats = bm.get_statistics()
        return jsonify({'ok': True, 'statistics': stats})
    except ImportError as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/backup/files', methods=['GET'])
def api_pro_backup_files():
    """获取磁盘上的备份文件列表"""
    try:
        instance_id = request.args.get('instance_id', '')
        db_type = request.args.get('db_type', '')
        from modules.pro.backup import get_backup_manager
        bm = get_backup_manager()
        backups = bm.list_backups(instance_id, db_type)
        return jsonify({'ok': True, 'backups': backups})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/backup/download/<path:filepath>', methods=['GET'])
def api_pro_backup_download(filepath):
    """下载备份文件"""
    try:
        import os
        # 安全检查：限制在 backups 目录内
        full_path = os.path.abspath(os.path.join("backups", filepath))
        if not full_path.startswith(os.path.abspath("backups")):
            return jsonify({'ok': False, 'error': '非法路径'}), 403
        if not os.path.exists(full_path):
            return jsonify({'ok': False, 'error': '文件不存在'}), 404
        from flask import send_file
        return send_file(full_path, as_attachment=True)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500
def api_pro_backup_config():
    """备份配置"""
    try:
        from modules.pro.backup import get_backup_manager
        bm = get_backup_manager()
        if request.method == 'GET':
            return jsonify({'ok': True, 'config': bm.config})
        else:
            data = request.get_json()
            bm.save_config(data)
            return jsonify({'ok': True, 'message': '配置已保存'})
    except ImportError as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Pro 模块加载失败: ' + str(e)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/backup/history/<int:record_id>', methods=['DELETE'])
def api_pro_backup_history_delete(record_id):
    """删除备份历史记录"""
    try:
        import sqlite3
        db_file = str(paths.PRO_DATA_DIR / "backup_history.db")
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM backup_history WHERE id=?", (record_id,))
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'message': '已删除'})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/backup/docker-containers', methods=['GET'])
def api_pro_backup_docker_containers():
    """获取运行中的 Docker 容器列表"""
    try:
        import subprocess, json
        result = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            return jsonify({'ok': False, 'error': 'Docker 未运行或未安装'})
        out = result.stdout or ""
        containers = []
        for line in out.strip().split("\n"):
            if not line:
                continue
            try:
                c = json.loads(line)
                name = c.get("Names", "")
                image = c.get("Image", "")
                containers.append({"name": name, "image": image})
            except Exception:
                pass
        return jsonify({'ok': True, 'containers': containers})
    except FileNotFoundError:
        return jsonify({'ok': False, 'error': 'Docker 未安装'})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  SQL 执行 API（一键修复）
# ══════════════════════════════════════════════════════════════

# SQL 执行日志表（记录所有执行操作）
_SQL_EXEC_LOG_TABLE_CREATED = False

def _ensure_sql_exec_log_table():
    """确保 SQL 执行日志表存在"""
    global _SQL_EXEC_LOG_TABLE_CREATED
    if _SQL_EXEC_LOG_TABLE_CREATED:
        return

    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
        # 使用 pro.db
        db_path = str(paths.PRO_DATA_DIR / 'pro.db')
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sql_execution_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                datasource_id TEXT NOT NULL,
                datasource_name TEXT,
                sql_text TEXT NOT NULL,
                affected_rows INTEGER DEFAULT 0,
                execution_time REAL,
                status TEXT DEFAULT 'success',
                error_message TEXT,
                executed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                client_ip TEXT
            )
        """)
        conn.commit()
        conn.close()
        _SQL_EXEC_LOG_TABLE_CREATED = True
    except Exception as e:
        print(f"[SQL Exec Log] 创建日志表失败: {e}")


def _log_sql_execution(datasource_id: str, datasource_name: str, sql: str,
                       affected: int, exec_time: float, status: str,
                       error: str = None):
    """记录 SQL 执行日志"""
    _ensure_sql_exec_log_table()

    try:
        db_path = str(paths.PRO_DATA_DIR / 'pro.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sql_execution_log
            (datasource_id, datasource_name, sql_text, affected_rows,
             execution_time, status, error_message, client_ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (datasource_id, datasource_name, sql, affected, exec_time,
              status, error, request.remote_addr if request else 'unknown'))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[SQL Exec Log] 记录日志失败: {e}")


def _translate_error(error_msg: str, db_type: str) -> str:
    """
    将数据库原始错误转换为友好中文提示
    """
    error_lower = error_msg.lower()

    # MySQL 常见错误
    if db_type in ('mysql', 'tidb'):
        # Unknown thread id
        if 'unknown thread id' in error_lower:
            return '该线程不存在或已结束，无需 KILL'
        # 外键约束
        if 'cannot delete' in error_lower or 'foreign key constraint' in error_lower:
            return '无法删除：该记录被其他数据引用，请先删除关联数据'
        if 'cannot add' in error_lower and 'foreign key constraint' in error_lower:
            return '无法添加：关联数据不存在'
        # 权限不足
        if 'access denied' in error_lower:
            return '权限不足，请使用有权限的用户连接'
        # 连接错误
        if 'connect' in error_lower and ('timeout' in error_lower or 'refused' in error_lower):
            return '无法连接到数据库，请检查连接信息'
        # 语法错误
        if 'syntax' in error_lower:
            return f'SQL 语法错误：{error_msg}'

    # PostgreSQL 常见错误
    elif db_type in ('postgresql', 'pg'):
        if 'permission denied' in error_lower:
            return '权限不足，请使用有权限的用户操作'
        if 'connection' in error_lower and ('timeout' in error_lower or 'refused' in error_lower):
            return '无法连接到数据库，请检查连接信息'
        if 'syntax error' in error_lower:
            return f'SQL 语法错误：{error_msg}'
        if 'foreign key constraint' in error_lower:
            return '无法操作：该记录被其他数据引用'

    # Oracle 常见错误
    elif db_type in ('oracle',):
        if 'ora-00054' in error_lower:
            return '资源正忙，该对象被锁定了'
        if 'ora-00001' in error_lower:
            return '唯一约束冲突，数据已存在'
        if 'ora-00942' in error_lower:
            return '表或视图不存在'
        if 'ora-01031' in error_lower:
            return '权限不足'
        if 'connection' in error_lower:
            return '无法连接到数据库，请检查连接信息'

    # SQL Server 常见错误
    elif db_type == 'sqlserver':
        if 'permission' in error_lower:
            return '权限不足，请使用有权限的用户操作'
        if 'connection' in error_lower:
            return '无法连接到数据库，请检查连接信息'
        if 'syntax' in error_lower:
            return f'SQL 语法错误：{error_msg}'

    # DM 达梦数据库
    elif db_type == 'dm':
        if 'permission denied' in error_lower:
            return '权限不足，请使用有权限的用户操作'
        if 'connection' in error_lower:
            return '无法连接到数据库，请检查连接信息'
        if 'syntax error' in error_lower:
            return f'SQL 语法错误：{error_msg}'

    # 默认返回原始错误
    return error_msg


def _detect_dangerous_sql(sql: str) -> tuple:
    """
    检测危险 SQL 操作
    返回: (is_dangerous: bool, warning_message: str)
    """
    sql_upper = sql.upper().strip()

    # 危险操作关键词
    dangerous_keywords = {
        'DROP': '删除对象（表/库/索引等）',
        'TRUNCATE': '清空表数据',
        'DELETE': '删除数据',
        'GRANT': '授权操作',
        'REVOKE': '撤销权限',
        'ALTER USER': '修改用户',
        'DROP USER': '删除用户',
        'SHUTDOWN': '关闭数据库',
    }

    for keyword, desc in dangerous_keywords.items():
        # 检查是否包含关键词（避免误判，要求前面不是字母）
        pattern = r'(^|\W)' + keyword.replace(' ', r'\s+') + r'(\W|$)'
        if re.search(pattern, sql_upper):
            return True, f'检测到危险操作: {keyword}（{desc}）'

    return False, ''


@app.route('/api/inspection/execute-sql', methods=['POST'])
def api_inspection_execute_sql():
    """
    执行修复 SQL
    参数:
        - datasource_id: 数据源 ID
        - sql: 要执行的 SQL
        - confirm: 是否已确认危险操作（可选）
    """
    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})

    data = request.get_json()
    datasource_id = data.get('datasource_id', '')
    sql = data.get('sql', '').strip()
    confirm = data.get('confirm', False)

    # 1. 参数校验
    if not datasource_id:
        return jsonify({'ok': False, 'error': '数据源 ID 不能为空'})
    if not sql:
        return jsonify({'ok': False, 'error': 'SQL 不能为空'})

    # 2. 危险操作检测
    is_dangerous, warning_msg = _detect_dangerous_sql(sql)
    if is_dangerous and not confirm:
        return jsonify({
            'ok': False,
            'need_confirm': True,
            'warning': warning_msg + '，请确认是否继续执行。'
        })

    # 3. 获取数据源连接信息
    db_info = im.get_instance_decrypted(datasource_id)
    if not db_info:
        return jsonify({'ok': False, 'error': '数据源不存在'})

    db_type = db_info.get('db_type', '').lower().replace('oracle_full', 'oracle').replace('_jdbc', '')
    datasource_name = db_info.get('name', datasource_id)

    # 4. 执行 SQL
    start_time = time.time()
    affected = 0
    status = 'success'
    error_msg = None

    try:
        def _split_sql(sql_str):
            """按分号拆分为单条语句，跳过空语句"""
            parts = sql_str.split(';')
            result = []
            for p in parts:
                p = p.strip()
                if p:
                    result.append(p)
            return result

        if db_type in ('mysql', 'tidb', 'mariadb', 'oceanbase', 'tdsqlc_mysql'):
            import pymysql
            conn = pymysql.connect(
                host=db_info.get('host', ''),
                port=int(db_info.get('port', 3306)),
                user=db_info.get('user', ''),
                password=db_info.get('password', ''),
                charset='utf8mb4',
                connect_timeout=10
            )
            cursor = conn.cursor()
            statements = _split_sql(sql)
            total_affected = 0
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
            conn.commit()
            affected = total_affected
            cursor.close()
            conn.close()

        elif db_type in ('postgresql', 'pg', 'ivorysql'):
            import psycopg2
            conn = psycopg2.connect(
                host=db_info.get('host', ''),
                port=int(db_info.get('port', 5432)),
                user=db_info.get('user', ''),
                password=db_info.get('password', ''),
                database=db_info.get('database', 'postgres'),
                connect_timeout=10
            )
            cursor = conn.cursor()
            statements = _split_sql(sql)
            total_affected = 0
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
            conn.commit()
            affected = total_affected
            cursor.close()
            conn.close()

        elif db_type == 'oracle':
            import oracledb as _od
            host_o = db_info.get('host')
            port_o = db_info.get('port', 1521)
            svc_o = db_info.get('service_name') or 'orcl'
            dsn_o = _od.makedsn(host_o, port_o, service_name=svc_o) if host_o and port_o else db_info.get('service_name', '')
            sysdba_o = db_info.get('sysdba', False)
            conn = _connect_oracle_thick_fallback(db_info.get('user', ''), db_info.get('password', ''), dsn_o, sysdba=sysdba_o)
            cursor = conn.cursor()
            statements = _split_sql(sql)
            total_affected = 0
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
            conn.commit()
            affected = total_affected
            cursor.close()
            conn.close()

        elif db_type == 'sqlserver':
            conn_str = _build_sqlserver_conn_str(
                db_info.get('host'), db_info.get('port'),
                db_info.get('user'), db_info.get('password'),
                database='master', timeout=30
            )
            import pyodbc
            conn = pyodbc.connect(conn_str)
            cursor = conn.cursor()
            statements = _split_sql(sql)
            total_affected = 0
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
            conn.commit()
            affected = total_affected
            cursor.close()
            conn.close()

        elif db_type == 'dm':
            import dmPython
            conn = dmPython.connect(
                user=db_info.get('user', ''),
                password=db_info.get('password', ''),
                server=db_info.get('host', ''),
                port=int(db_info.get('port', 5236))
            )
            cursor = conn.cursor()
            statements = _split_sql(sql)
            total_affected = 0
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
            conn.commit()
            affected = total_affected
            cursor.close()
            conn.close()

        elif db_type == 'yashandb':
            import yasdb
            conn = yasdb.connect(
                host=db_info.get('host', ''),
                port=int(db_info.get('port', 1688)),
                user=db_info.get('user', ''),
                password=db_info.get('password', '')
            )
            cursor = conn.cursor()
            statements = _split_sql(sql)
            total_affected = 0
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
            conn.commit()
            affected = total_affected
            cursor.close()
            conn.close()

        elif db_type == 'db2':
            from plugins.available.db2_jdbc.main_plugin import get_connection
            conn = get_connection(
                db_info.get('host', ''), db_info.get('port'),
                db_info.get('user', ''), db_info.get('password', ''),
                database=db_info.get('database', ''))
            cursor = conn.cursor()
            statements = _split_sql(sql)
            total_affected = 0
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
            conn.commit()
            affected = total_affected
            cursor.close()
            conn.close()

        elif db_type == 'clickhouse':
            from plugins.available.clickhouse_jdbc.main_plugin import get_connection
            conn = get_connection(
                db_info.get('host', ''), db_info.get('port'),
                db_info.get('user', ''), db_info.get('password', ''),
                database=db_info.get('database', '') or 'default')
            cursor = conn.cursor()
            statements = _split_sql(sql)
            total_affected = 0
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
            conn.commit()
            affected = total_affected
            cursor.close()
            conn.close()

        else:
            return jsonify({'ok': False, 'error': f'不支持的数据库类型: {db_type}'})

        exec_time = time.time() - start_time

        # 5. 记录执行日志
        _log_sql_execution(datasource_id, datasource_name, sql, affected, exec_time, status)

        return jsonify({
            'ok': True,
            'affected': affected,
            'exec_time': round(exec_time, 2),
            'message': f'执行成功，影响 {affected} 行'
        })

    except Exception as e:
        exec_time = time.time() - start_time
        status = 'failed'
        error_msg = str(e)

        # 转换为友好错误提示
        friendly_msg = _translate_error(error_msg, db_type)

        # 记录失败日志
        _log_sql_execution(datasource_id, datasource_name, sql, affected, exec_time, status, error_msg)

        return jsonify({'ok': False, 'error': friendly_msg})


@app.route('/api/inspection/sql-logs', methods=['GET'])
def api_inspection_sql_logs():
    """获取 SQL 执行日志"""
    try:
        db_path = str(paths.PRO_DATA_DIR / 'pro.db')
        if not os.path.exists(db_path):
            return jsonify({'ok': True, 'logs': []})

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, datasource_id, datasource_name, sql_text, affected_rows,
                   execution_time, status, error_message, executed_at, client_ip
            FROM sql_execution_log
            ORDER BY executed_at DESC
            LIMIT 100
        """)
        rows = cursor.fetchall()
        conn.close()

        logs = []
        for row in rows:
            logs.append({
                'id': row[0],
                'datasource_id': row[1],
                'datasource_name': row[2],
                'sql_text': row[3],
                'affected_rows': row[4],
                'execution_time': row[5],
                'status': row[6],
                'error_message': row[7],
                'executed_at': row[8],
                'client_ip': row[9]
            })

        return jsonify({'ok': True, 'logs': logs})

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


# ══════════════════════════════════════════════════════════════
#  AI 聊天巡检 API
# ══════════════════════════════════════════════════════════════

def _load_ai_config():
    """加载 AI 配置"""
    cfg_path = os.path.join(BASE_DIR, 'dbc_config.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            return json.load(f).get('ai', {})
    return {
        'backend': 'ollama',
        'online_enabled': False,
        'online_backend': 'openai',
        'api_key': '',
        'api_url': 'http://localhost:11434',
        'online_api_url': 'https://api.openai.com/v1',
        'online_model': 'gpt-4o-mini',
        'model': 'qwen3:8b',
        'timeout': 600
    }


def _call_llm(prompt: str, system: str = '', stream_callback=None) -> str:
    """调用 LLM API 生成文本（支持 Ollama 和 OpenAI 协议兼容的远程模型）
    
    Args:
        stream_callback: 流式回调函数，接收 chunk 字符串。如果提供，则流式输出
    """
    cfg = _load_ai_config()
    backend = cfg.get('backend', 'ollama')
    timeout = int(cfg.get('timeout', 600))
    # 在线模型已启用时，优先走在线后端（online_enabled 开关生效）
    if cfg.get('online_enabled', False) and backend != 'openai':
        backend = cfg.get('online_backend', 'openai')

    if backend == 'ollama':
        return _call_llm_ollama(cfg, prompt, system, timeout, stream_callback)
    elif backend == 'openai':
        online_enabled = cfg.get('online_enabled', False)
        if not online_enabled:
            return '[在线模型未启用，请在 AI 设置中开启"启用在线模型"]'
        return _call_llm_openai(cfg, prompt, system, timeout, stream_callback)
    else:
        return '[AI 后端未启用]'


def _call_llm_ollama(cfg: dict, prompt: str, system: str, timeout: int, stream_callback=None) -> str:
    """调用 Ollama API 生成文本（支持流式）

    Args:
        stream_callback: 流式回调函数，接收 chunk 字符串。如果提供，则返回完整响应；否则逐块调用 callback
    """
    api_url = cfg.get('api_url', 'http://localhost:11434').rstrip('/')
    model = cfg.get('model', 'qwen3:8b')

    # 本机地址归一化为 127.0.0.1，并绕过系统代理直连（见 _ollama_normalize_url）
    api_url = _ollama_normalize_url(api_url)

    url = api_url + '/api/generate'
    payload = {
        'model': model,
        'prompt': prompt,
        'stream': True,  # 改为 True 支持流式
    }
    if system:
        payload['system'] = system

    try:
        import urllib.request
        import http.client

        opener = _ollama_no_proxy_opener()
        if stream_callback:
            parsed_url = urllib.parse.urlparse(url)
            conn = http.client.HTTPConnection(parsed_url.hostname, parsed_url.port or 80, timeout=timeout)
            body = json.dumps(payload).encode('utf-8')
            conn.request('POST', parsed_url.path, body=body, headers={'Content-Type': 'application/json'})
            resp = conn.getresponse()

            full_response = ''
            # 使用缓冲读取替代逐字节读取（更可靠）
            buf = b''
            while True:
                data = resp.read(4096)
                if not data:
                    break
                buf += data
                # 按行切分处理
                while b'\n' in buf:
                    line_bytes, buf = buf.split(b'\n', 1)
                    line = line_bytes.decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                    try:
                        jdata = json.loads(line)
                        if 'response' in jdata:
                            ct = jdata['response']
                            if ct:
                                full_response += ct
                                stream_callback(ct)
                        if jdata.get('done', False):
                            conn.close()
                            return full_response.strip()
                    except json.JSONDecodeError:
                        pass
            conn.close()
            # 如果流式读取提前结束但有内容，返回已有内容
            return full_response.strip() if full_response.strip() else ''
        else:
            # 非流式模式（兼容旧代码）
            payload['stream'] = False
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                        headers={'Content-Type': 'application/json'})
            with opener.open(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                return result.get('response', '').strip()
    except Exception as e:
        return f'[Ollama 调用失败: {e}]'


def _call_llm_openai(cfg: dict, prompt: str, system: str, timeout: int, stream_callback=None) -> str:
    """调用 OpenAI 协议兼容的远程 API 生成文本（支持流式）
    
    Args:
        stream_callback: 流式回调函数，接收 chunk 字符串
    """
    api_url = cfg.get('online_api_url', 'https://api.openai.com/v1').rstrip('/')
    model = cfg.get('online_model', 'gpt-4o-mini')
    api_key = cfg.get('api_key', '')

    if not api_url.endswith('/v1'):
        if '/v1/' in api_url:
            api_url = api_url[:api_url.index('/v1') + 3]
        else:
            api_url = api_url + '/v1'
    url = api_url + '/chat/completions'

    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})

    payload = {
        'model': model,
        'messages': messages,
        'temperature': 0.3,
        'stream': stream_callback is not None,  # 流式模式
    }

    try:
        if stream_callback:
            # 流式模式：使用 requests 库（需要安装）
            try:
                import requests
            except ImportError:
                return '[流式模式需要安装 requests 库：pip install requests]'
            
            headers = {'Content-Type': 'application/json'}
            if api_key:
                headers['Authorization'] = f'Bearer {api_key}'
            
            full_response = ''
            with requests.post(url, json=payload, headers=headers, stream=True, timeout=timeout) as r:
                for line in r.iter_lines():
                    if line:
                        line = line.decode('utf-8')
                        if line.startswith('data: '):
                            data_str = line[6:]
                            if data_str == '[DONE]':
                                break
                            try:
                                data = json.loads(data_str)
                                choices = data.get('choices', [])
                                if choices:
                                    delta = choices[0].get('delta', {})
                                    chunk = delta.get('content', '')
                                    if chunk:
                                        full_response += chunk
                                        stream_callback(chunk)
                            except json.JSONDecodeError:
                                pass
            return full_response.strip()
        else:
            # 非流式模式（兼容旧代码）
            import urllib.request
            headers = {'Content-Type': 'application/json'}
            if api_key:
                headers['Authorization'] = f'Bearer {api_key}'
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                        headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                choices = result.get('choices', [])
                if choices:
                    return choices[0].get('message', {}).get('content', '').strip()
                return ''
    except Exception as e:
        return f'[OpenAI API 调用失败: {e}]'


# ══════════════════════════════════════════════════════════════
#  AI 聊天会话管理 & RAG 问答
# ══════════════════════════════════════════════════════════════

def _get_chat_session(session_id: str) -> list:
    """获取或创建聊天会话历史"""
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = []
    return _chat_sessions[session_id]


def _add_to_history(session_id: str, role: str, content: str):
    """添加消息到会话历史，超出限制时截断"""
    history = _get_chat_session(session_id)
    history.append({'role': role, 'content': content})
    # 保留最近 N 条
    if len(history) > _CHAT_HISTORY_LIMIT:
        _chat_sessions[session_id] = history[-_CHAT_HISTORY_LIMIT:]


def _clear_chat_session(session_id: str):
    """清空指定会话历史"""
    _chat_sessions.pop(session_id, None)


def _format_conversation_history(session_id: str) -> str:
    """将会话历史格式化为 LLM 可读的对话文本"""
    history = _get_chat_session(session_id)
    if not history:
        return ''
    lines = []
    for msg in history[-10:]:  # 最近 10 条
        role_label = '用户' if msg['role'] == 'user' else 'AI'
        lines.append(f"{role_label}: {msg['content']}")
    return '\n'.join(lines)


def _classify_chat_intent(user_message: str) -> str:
    """
    轻量级意图分类：判断用户问题是「巡检执行」还是「知识问答」
    返回: 'inspect' | 'qa'
    策略：先关键词匹配，再 fallback 到 LLM
    """
    msg = user_message.strip().lower()

    # 巡检关键词
    inspect_keywords = [
        '巡检', '检查', '诊断', '全库', '完整', '报告',
        'inspect', 'diagnose', 'check', 'scan', 'report',
        '连接数', '锁等待', '慢查询',
        'connection', 'lock', 'slow query',
        '启动巡检', '开始巡检', '执行巡检',
        'mysql-', 'pg-', 'oracle-', 'tidb-', 'dm-', 'sqlserver-',
    ]
    for kw in inspect_keywords:
        if kw in msg:
            return 'inspect'

    # 问答关键词（数据库知识类 + 上下文查询）
    qa_keywords = [
        # 数据库知识
        '怎么', '如何', '为什么', '是什么', '有哪些', '推荐', '最佳实践',
        '如何优化', '怎么办', '什么原因', '怎么解决',
        'how to', 'what is', 'why', 'best practice', 'recommend',
        '慢查询', '索引', '事务', '锁', '备份', '恢复',
        'innodb', 'buffer pool', 'wal', 'redo', 'undo',
        'vacuum', 'autovacuum', '归档',
        # v2.6.2: 上下文感知类问题
        '当前', '页面', '哪个', '什么页', '在哪里', '选中', '正在',
        '你好', 'hi', 'hello', '帮助', 'help',
    ]
    for kw in qa_keywords:
        if kw in msg:
            return 'qa'

    # 默认：尝试用 LLM 判断
    try:
        cfg = _load_ai_config()
        backend = cfg.get('backend', 'ollama')
        if backend in ('ollama', 'openai'):
            system_prompt = """你是一个意图分类器。用户发来一条消息给数据库巡检助手。
请判断这是「巡检执行」（需要连接数据库执行巡检操作）还是「知识问答」（询问数据库运维知识）。
只输出 inspect 或 qa，不要输出其他内容。"""
            response = _call_llm(user_message, system_prompt)
            if 'qa' in response.lower():
                return 'qa'
        return 'qa'  # v2.6.2: 默认走问答模式，巡检需要明确意图
    except Exception:
        return 'qa'  # v2.6.2: 异常时默认问答，避免误判为巡检报错


def _answer_chat_qa(session_id: str, user_message: str, db_type: str = None, context: dict = None) -> dict:
    """
    基于 RAG 知识库回答数据库运维问题（v2.6.2 支持上下文感知）

    Args:
        context: 前端上下文，包含 page, datasource_id, datasource_name, db_type, sql_content, template_id
    """
    cfg = _load_ai_config()
    backend = cfg.get('backend', 'ollama')
    rag_cfg = cfg.get('rag', {})
    rag_enabled = rag_cfg.get('enabled', True) if isinstance(rag_cfg, dict) else True

    # 检查 AI 后端是否可用
    ai_available = backend in ('ollama', 'openai')
    if backend == 'openai' and not cfg.get('online_enabled', False):
        ai_available = False

    if not ai_available:
        return {
            'ok': True,
            'answer': '💡 AI 后端未启用。请在「AI 诊断」设置中配置 Ollama 或 OpenAI 后端，才能使用知识库问答功能。',
            'rag_used': False,
            'rag_disabled': True,
        }

    # 检查 RAG
    if not rag_enabled:
        return {
            'ok': True,
            'answer': '💡 RAG 知识库未启用。请在「AI 诊断」设置中开启 RAG 知识库功能，以获得基于文档的智能问答。\n\n> 即使未启用知识库，我也可以尝试基于自身知识回答您的问题。',
            'rag_used': False,
            'rag_disabled': True,
        }

    # 尝试 RAG 检索
    rag_context = ''
    rag_used = False
    try:
        from modules.rag import RAGRetriever, VectorStore, OllamaEmbedding, OpenAIEmbedding
        vs = VectorStore()
        stats = vs.get_collection_stats()
        if stats.get('total_chunks', 0) > 0:
            if backend == 'ollama':
                emb = OllamaEmbedding()
            else:
                emb = OpenAIEmbedding(
                    api_url=cfg.get('online_api_url', 'https://api.openai.com/v1'),
                    model=cfg.get('online_model', 'text-embedding-3-small'),
                    api_key=cfg.get('api_key', ''),
                )
            retriever = RAGRetriever(vs, emb)
            rag_context = retriever.retrieve_for_chat(
                user_message, db_type=db_type, top_k=5)
            rag_used = bool(rag_context)
    except Exception:
        pass  # RAG 失败不影响回答

    # 构建 Prompt（v2.6.2：注入上下文）
    system_prompt = """你是 DBCheck 数据库运维智能助手，专门帮助用户解答数据库运维、性能优化、故障排查等方面的问题。

回答规则：
1. 如果知识库中有相关文档，优先参考知识库内容回答
2. 如果没有知识库参考，基于自身知识回答
3. 回答要简洁实用，多给具体建议和命令示例
4. 涉及 SQL 时注明适用的数据库类型
5. 使用 Markdown 格式排版
6. 如果问题与数据库运维无关，礼貌地说明能力范围"""

    # v2.6.2：将上下文注入 system_prompt
    if context:
        ctx_parts = []
        # 优先用中文页面名，fallback 到英文 ID
        page_title = context.get('page_title', '')
        page = context.get('page', '')
        if page_title and page_title != page:
            ctx_parts.append(f'当前页面：{page_title}')
        elif page:
            ctx_parts.append(f'当前页面：{page}')
        ds_name = context.get('datasource_name', '')
        if ds_name:
            ctx_parts.append(f'当前数据源：{ds_name}')
        ctx_db_type = context.get('db_type', '')
        if ctx_db_type:
            ctx_parts.append(f'数据库类型：{ctx_db_type}')
        sql_content = context.get('sql_content', '')
        if sql_content and len(sql_content.strip()) > 0:
            sql_preview = sql_content[:500] + ('...' if len(sql_content) > 500 else '')
            ctx_parts.append(f'当前 SQL 编辑器内容：\n```sql\n{sql_preview}\n```')
        template_id = context.get('template_id', None)
        if template_id:
            ctx_parts.append(f'当前巡检模板 ID：{template_id}')

        if ctx_parts:
            system_prompt += '\n\n## 当前上下文（用户当前正在操作的界面信息）\n' + '\n'.join(ctx_parts) + \
                '\n请根据上下文，给出更贴合用户当前场景的回答。'

    conversation = _format_conversation_history(session_id)
    if conversation:
        prompt = f"""## 历史对话
{conversation}

## 当前问题
{user_message}"""
    else:
        prompt = user_message

    if rag_context:
        prompt = f"""{prompt}

{rag_context}

请结合上述知识库内容回答用户的问题。"""
    else:
        prompt = f"""{prompt}

（本次未检索到相关知识库文档，请基于自身知识回答）"""

    # 调用 LLM
    try:
        answer = _call_llm(prompt, system_prompt)
        # 保存到会话历史
        _add_to_history(session_id, 'user', user_message)
        _add_to_history(session_id, 'ai', answer)
        return {'ok': True, 'answer': answer, 'rag_used': rag_used, 'rag_disabled': False}
    except Exception as e:
        return {'ok': True, 'answer': f'❌ LLM 调用失败: {e}\n\n请检查 AI 后端服务是否正常运行。', 'rag_used': False, 'rag_disabled': False}


def parse_intent(user_message: str) -> dict:
    """解析用户意图，返回结构化信息"""
    system_prompt = """你是一个数据库巡检助手。用户会用自然语言描述巡检需求。
请从用户输入中提取以下字段，以 JSON 格式输出：
{
  "db_type": "mysql|pg|oracle|dm|sqlserver|tidb|unknown",
  "db_name": "数据源名称（如 MySQL-01）或空字符串",
  "scope": "connection_count|lock_wait|slow_queries|all",
  "need_report": true或false
}
只输出 JSON，不要输出其他内容。"""

    prompt = f'输入："{user_message}"'
    response = _call_llm(prompt, system_prompt)

    # 解析 JSON
    try:
        # 尝试提取 JSON
        import re
        match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            data = json.loads(response)

        # 标准化返回值
        db_type = data.get('db_type', 'unknown')
        if db_type == 'postgresql':
            db_type = 'pg'
        elif db_type == 'sqlserver':
            db_type = 'sqlserver'

        scope = data.get('scope', 'all')
        need_report = data.get('need_report', scope == 'all')

        return {
            'db_type': db_type,
            'db_name': data.get('db_name', ''),
            'scope': scope,
            'need_report': need_report,
            'raw': data,
        }
    except Exception:
        # 解析失败，返回默认值
        return {
            'db_type': 'unknown',
            'db_name': '',
            'scope': 'all',
            'need_report': True,
            'raw': {},
        }


def list_instances_by_type(db_type: str):
    """列出指定 db_type 的所有数据源（名称列表）"""
    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
        instances = im.get_all_instances(mask_password=False)
        return [inst.get('name', '') for inst in instances if inst.get('db_type') == db_type]
    except Exception:
        return []


def match_datasource(db_name: str):
    """从 Pro InstanceManager 按名称匹配数据源，返回解密后的连接信息"""
    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
        instances = im.get_all_instances(mask_password=False)

        if not db_name:
            return None

        # 模糊匹配（忽略大小写）
        db_name_lower = db_name.lower()
        matched_inst = None
        for inst in instances:
            if inst.get('name', '').lower() == db_name_lower:
                matched_inst = inst
                break
            if db_name_lower in inst.get('name', '').lower():
                matched_inst = inst
                break

        if not matched_inst:
            return None

        # 通过 ID 获取解密后的完整信息（包含解密后的密码）
        inst_id = matched_inst.get('id')
        if inst_id:
            decrypted = im.get_instance_decrypted(inst_id)
            if decrypted:
                return decrypted

        return matched_inst
    except Exception:
        return None


def execute_simple_query(db_info: dict, db_type: str, scope: str) -> str:
    """执行简单查询，返回格式化文本"""
    results = []

    try:
        if db_type == 'mysql':
            import pymysql
            conn = pymysql.connect(
                host=db_info.get('host', ''),
                port=int(db_info.get('port', 3306)),
                user=db_info.get('user', ''),
                password=db_info.get('password', ''),
                charset='utf8mb4',
                connect_timeout=10
            )
            cur = conn.cursor()

            if scope == 'connection_count':
                cur.execute("SHOW STATUS LIKE 'Threads_connected'")
                row = cur.fetchone()
                results.append(f'当前连接数: {row[1] if row else "未知"}')

                cur.execute("SHOW STATUS LIKE 'Max_used_connections'")
                row = cur.fetchone()
                results.append(f'历史最大连接数: {row[1] if row else "未知"}')

                cur.execute("SHOW VARIABLES LIKE 'max_connections'")
                row = cur.fetchone()
                results.append(f'最大连接数限制: {row[1] if row else "未知"}')

            elif scope == 'slow_queries':
                cur.execute("SHOW GLOBAL VARIABLES LIKE 'slow_query_log'")
                row = cur.fetchone()
                results.append(f'慢查询日志: {"开启" if row and row[1] == "ON" else "关闭"}')

                cur.execute("SHOW GLOBAL STATUS LIKE 'Slow_queries'")
                row = cur.fetchone()
                results.append(f'慢查询数量: {row[1] if row else "0"}')

                cur.execute("SHOW GLOBAL VARIABLES LIKE 'long_query_time'")
                row = cur.fetchone()
                results.append(f'慢查询阈值: {row[1] if row else "10"} 秒')

            elif scope == 'lock_wait':
                cur.execute("SHOW ENGINE INNODB STATUS")
                row = cur.fetchone()
                if row:
                    status = row[2] if len(row) > 2 else ''
                    # 提取锁等待信息
                    import re
                    lock_match = re.search(r'(\d+) lock struct.*?(\d+) row lock', status, re.I)
                    if lock_match:
                        results.append(f'InnoDB 锁结构数: {lock_match.group(1)}')
                        results.append(f'InnoDB 行锁数: {lock_match.group(2)}')
                    else:
                        results.append('未检测到锁等待')

            cur.close()
            conn.close()

        elif db_type in ('pg', 'ivorysql', 'kingbase'):
            import psycopg2
            conn = psycopg2.connect(
                host=db_info.get('host', ''),
                port=int(db_info.get('port', 5432)),
                user=db_info.get('user', ''),
                password=db_info.get('password', ''),
                database=db_info.get('database', 'postgres'),
                connect_timeout=10
            )
            cur = conn.cursor()

            if scope == 'connection_count':
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
                row = cur.fetchone()
                results.append(f'当前活跃连接数: {row[0] if row else 0}')

                cur.execute("SELECT setting FROM pg_settings WHERE name = 'max_connections'")
                row = cur.fetchone()
                results.append(f'最大连接数限制: {row[0] if row else "未知"}')

                cur.execute("SELECT count(*) FROM pg_stat_activity")
                row = cur.fetchone()
                results.append(f'总连接数: {row[0] if row else 0}')

            elif scope == 'slow_queries':
                cur.execute("SHOW log_min_duration_statement")
                row = cur.fetchone()
                results.append(f'慢查询阈值: {row[0] if row else "未设置"}')

            elif scope == 'lock_wait':
                cur.execute("""SELECT l.pid, l.mode, l.granted, a.datname, a.query
                                FROM pg_locks l
                                JOIN pg_stat_activity a ON l.pid = a.pid
                                WHERE NOT l.granted""")
                rows = cur.fetchall()
                if rows:
                    results.append(f'等待中的锁: {len(rows)} 个')
                    for row in rows[:5]:
                        results.append(f'  PID {row[0]}: {row[1]} - {row[3]}')
                else:
                    results.append('未检测到锁等待')

            cur.close()
            conn.close()

        elif db_type == 'oracle':
            try:
                import oracledb
                conn = oracledb.connect(
                    user=db_info.get('user', ''),
                    password=db_info.get('password', ''),
                    host=db_info.get('host', ''),
                    port=int(db_info.get('port', 1521)),
                    service_name=db_info.get('service_name') or db_info.get('sid', 'orcl')
                )
                cur = conn.cursor()

                if scope == 'connection_count':
                    cur.execute("SELECT count(*) FROM v$session WHERE status = 'ACTIVE'")
                    row = cur.fetchone()
                    results.append(f'当前活跃会话数: {row[0] if row else 0}')

                    cur.execute("SELECT value FROM v$parameter WHERE name = 'sessions'")
                    row = cur.fetchone()
                    results.append(f'最大会话数限制: {row[0] if row else "未知"}')

                elif scope == 'lock_wait':
                    cur.execute("""SELECT s.sid, s.serial#, l.type, l.lmode, l.request
                                    FROM v$session s, v$lock l
                                    WHERE s.sid = l.sid AND l.request > 0""")
                    rows = cur.fetchall()
                    if rows:
                        results.append(f'等待中的锁: {len(rows)} 个')
                        for row in rows[:5]:
                            results.append(f'  SID {row[0]}: {row[2]} (mode={row[3]})')
                    else:
                        results.append('未检测到锁等待')

                cur.close()
                conn.close()
            except Exception as e:
                results.append(f'Oracle 连接失败: {e}')

        elif db_type == 'dm':
            try:
                import dmPython
                conn = dmPython.connect(
                    user=db_info.get('user', ''),
                    password=db_info.get('password', ''),
                    server=db_info.get('host', ''),
                    port=int(db_info.get('port', 5236))
                )
                cur = conn.cursor()

                if scope == 'connection_count':
                    cur.execute("SELECT COUNT(*) FROM V$INSTANCE")
                    row = cur.fetchone()
                    results.append(f'达梦实例: {"正常" if row and row[0] > 0 else "异常"}')

                    cur.execute("SELECT COUNT(*) FROM V$SESSIONS")
                    row = cur.fetchone()
                    results.append(f'当前会话数: {row[0] if row else 0}')

                cur.close()
                conn.close()
            except Exception as e:
                results.append(f'达梦连接失败: {e}')

        elif db_type == 'sqlserver':
            try:
                import pyodbc
                conn_str = _build_sqlserver_conn_str(
                    db_info.get('host'), db_info.get('port', 1433),
                    db_info.get('user', ''), db_info.get('password', ''), timeout=10
                )
                conn = pyodbc.connect(conn_str, timeout=10)
                cur = conn.cursor()

                if scope == 'connection_count':
                    cur.execute("SELECT count(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1")
                    row = cur.fetchone()
                    results.append(f'当前用户会话数: {row[0] if row else 0}')

                    cur.execute("SELECT value FROM sys.configurations WHERE name = 'user connections'")
                    row = cur.fetchone()
                    results.append(f'最大连接数配置: {row[0] if row else "动态"}')

                elif scope == 'lock_wait':
                    cur.execute("""SELECT r.session_id, r.blocking_session_id, t.text
                                    FROM sys.dm_exec_requests r
                                    CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
                                    WHERE r.blocking_session_id > 0""")
                    rows = cur.fetchall()
                    if rows:
                        results.append(f'阻塞会话: {len(rows)} 个')
                        for row in rows[:5]:
                            results.append(f'  Session {row[0]} 被 {row[1]} 阻塞')
                    else:
                        results.append('未检测到阻塞')

                cur.close()
                conn.close()
            except Exception as e:
                results.append(f'SQL Server 连接失败: {e}')

        elif db_type == 'tidb':
            import pymysql
            conn = pymysql.connect(
                host=db_info.get('host', ''),
                port=int(db_info.get('port', 4000)),
                user=db_info.get('user', ''),
                password=db_info.get('password', ''),
                charset='utf8mb4',
                connect_timeout=10
            )
            cur = conn.cursor()

            if scope == 'connection_count':
                cur.execute("SHOW STATUS LIKE 'Threads_connected'")
                row = cur.fetchone()
                results.append(f'当前连接数: {row[1] if row else "未知"}')

            cur.close()
            conn.close()

    except Exception as e:
        results.append(f'查询执行失败: {e}')

    if not results:
        return '未获取到数据'

    return '\n'.join(results)


def _stream_inspection_response(data, message, session_id, chat_context):
    """巡检意图：解析→匹配数据源→执行巡检，通过SSE返回结果"""
    import time

    # 1. 解析意图
    intent = parse_intent(message)
    db_type = intent.get('db_type', 'unknown')
    db_name = intent.get('db_name', '')
    scope = intent.get('scope', 'all')
    need_report = intent.get('need_report', scope == 'all')

    print(f'[AI Stream] 巡检意图: db_type={db_type}, db_name={db_name}, scope={scope}', flush=True)

    # 2. 匹配数据源
    ds = None
    matched_name = None

    if db_name:
        ds = match_datasource(db_name)
        matched_name = db_name

    # 名称匹配失败，尝试按 db_type 筛选
    if not ds and db_type != 'unknown' and db_type:
        candidates = list_instances_by_type(db_type)
        if len(candidates) == 1:
            ds = match_datasource(candidates[0])
            matched_name = candidates[0]
        elif len(candidates) > 1:
            names_str = '、'.join(candidates)
            yield f"data: {json.dumps({'type': 'inspect_ask', 'message': f'找到 {len(candidates)} 个{db_type.upper()}数据源：{names_str}，请选择要巡检的实例。', 'candidates': candidates, 'db_type': db_type}, ensure_ascii=False)}\n\n"
            return

    if not ds:
        # 尝试从请求体获取连接参数
        ds = {
            'host': data.get('host', ''),
            'port': data.get('port', 3306),
            'user': data.get('user', ''),
            'password': data.get('password', ''),
            'database': data.get('database', ''),
            'service_name': data.get('service_name', ''),
            'sid': data.get('sid', ''),
        }

    if not ds or not ds.get('host'):
        if db_name and db_type != 'unknown' and db_type:
            candidates = list_instances_by_type(db_type)
            if candidates:
                names_str = '、'.join(candidates)
                yield f"data: {json.dumps({'type': 'inspect_ask', 'message': f'未找到「{db_name}」，可用的{db_type.upper()}数据源有：{names_str}。', 'candidates': candidates, 'db_type': db_type}, ensure_ascii=False)}\n\n"
                return
        _warn = {
            'type': 'error',
            'message': '[WARN] 无法确定巡检目标。请指定数据源名称（如"巡检 MySQL-01 的连接数"）或在上下文中选择数据源。',
        }
        yield f"data: {json.dumps(_warn, ensure_ascii=False)}\n\n"
        return

    # 3. 执行简单查询或启动巡检任务
    if scope in ('connection_count', 'lock_wait', 'slow_queries') and not need_report:
        try:
            result = execute_simple_query(ds, db_type, scope)
            yield f"data: {json.dumps({'type': 'inspect_result', 'message': result, 'scope': scope, 'db_name': matched_name or ds.get('name', '')}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'[WARN] 执行查询失败: {e}'}, ensure_ascii=False)}\n\n"
        return

    # 全库巡检：启动任务
    task_id = str(uuid.uuid4())
    db_info = {
        'ip': ds.get('host', ''),
        'port': int(ds.get('port', 3306)),
        'user': ds.get('user', ''),
        'password': ds.get('password', ''),
        'database': ds.get('database') or ('highgo' if db_type == 'hgdb' else ('ivorysql' if db_type == 'ivorysql' else ('postgres' if db_type == 'pg' else ('DAMENG' if db_type == 'dm' else '')))),
        'service_name': ds.get('service_name') or ds.get('sid'),
        'name': ds.get('name', db_name or ds.get('host', '')),
    }
    # SQL Server (JDBC) 双轨驱动参数透传
    if db_type == 'sqlserver_jdbc':
        # 旧实例缺失该字段时缺省走 jdbc（显式配置的 odbc/auto 仍保留）
        db_info['connection_mode'] = ds.get('connection_mode') or 'jdbc'
        db_info['jdbc_url'] = ds.get('jdbc_url') or ''
        db_info['encrypt'] = bool(ds.get('encrypt', False))
        db_info['trust_server_certificate'] = bool(ds.get('trust_server_certificate', True))

    inspector_name = data.get('inspector_name', 'Jack')
    tasks[task_id] = {
        'id': task_id,
        'db_type': db_type,
        'db_info': db_info,
        'inspector': inspector_name,
        'status': 'running',
        'started_at': datetime.datetime.now().isoformat(),
    }

    task_func_map = {
        'mysql': run_inspection_task,
        'pg': run_inspection_task,
        'oracle': run_inspection_task,
        'dm': run_inspection_task,
        'sqlserver': run_inspection_task,
        'tidb': run_inspection_task,
        'ivorysql': run_inspection_task,
        'kingbase': run_inspection_task,
        'yashandb': run_inspection_task,
        'gbase': run_inspection_task,
        'oceanbase': run_inspection_task,
    }
    task_func = task_func_map.get(db_type, run_inspection_task)

    t = threading.Thread(target=task_func, args=(task_id, db_info, inspector_name))
    t.daemon = True
    t.start()

    # 通过 SSE 告诉前端巡检已启动
    yield f"data: {json.dumps({'type': 'inspect_start', 'task_id': task_id, 'message': f'🔍 已启动 **{matched_name or db_name or db_type}** 的巡检任务...', 'db_name': matched_name or ds.get('name', ''), 'host': ds.get('host', ''), 'port': ds.get('port', ''), 'db_type': db_type}, ensure_ascii=False)}\n\n"


@app.route('/api/chat/stream', methods=['POST'])
def api_chat_stream():
    """SSE 流式返回 AI 回复（v2.6.3+）自动意图分类"""
    try:
        data = request.get_json() or {}
        message = data.get('message', '')
        session_id = data.get('session_id', 'default')

        # 提取上下文
        chat_context = {
            'page': data.get('page', 'unknown'),
            'page_title': data.get('page_title', ''),
            'datasource_id': data.get('datasource_id', ''),
            'datasource_name': data.get('datasource_name', ''),
            'db_type': data.get('db_type', ''),
            'sql_content': data.get('sql_content', ''),
            'template_id': data.get('template_id', None),
        }

        if not message:
            return jsonify({'error': '消息不能为空'}), 400

        # ═══ 意图分类（自动判断问答 vs 巡检）═══
        chat_intent = _classify_chat_intent(message)
        print(f'[AI Stream] 意图分类: "{message[:40]}" → {chat_intent}', flush=True)

        # ═══ 巡检模式：直接执行巡检 ═══
        if chat_intent == 'inspect':
            return Response(
                _stream_inspection_response(data, message, session_id, chat_context),
                mimetype='text/event-stream'
            )

        # ═══ 问答模式：流式 LLM 回复（原有逻辑）═══
        cfg = _load_ai_config()
        backend = cfg.get('backend', 'ollama')
        rag_cfg = cfg.get('rag', {})
        rag_enabled = rag_cfg.get('enabled', True) if isinstance(rag_cfg, dict) else True

        # 检查 AI 后端是否可用
        ai_available = backend in ('ollama', 'openai')
        if backend == 'openai' and not cfg.get('online_enabled', False):
            ai_available = False

        if not ai_available:
            def gen_error():
                yield f"data: {json.dumps({'type': 'error', 'message': 'AI 后端未启用'}, ensure_ascii=False)}\n\n"
            return Response(gen_error(), mimetype='text/event-stream')

        system_prompt = """你是 DBCheck 数据库运维智能助手，专门帮助用户解答数据库运维、性能优化、故障排查等方面的问题。

回答规则：
1. 如果知识库中有相关文档，优先参考知识库内容回答
2. 如果没有知识库参考，基于自身知识回答
3. 回答要简洁实用，多给具体建议和命令示例
4. 涉及 SQL 时注明适用的数据库类型
5. 使用 Markdown 格式排版（支持代码块、表格、加粗等）
6. 如果问题与数据库运维无关，礼貌地说明能力范围"""
        
        # 注入上下文
        if chat_context.get('page'):
            ctx_parts = []
            page_title = chat_context.get('page_title', '')
            if page_title:
                ctx_parts.append(f'当前页面：{page_title}')
            else:
                ctx_parts.append(f'当前页面：{chat_context["page"]}')
            ds_name = chat_context.get('datasource_name', '')
            if ds_name:
                ctx_parts.append(f'当前数据源：{ds_name}')
            db_type = chat_context.get('db_type', '')
            if db_type:
                ctx_parts.append(f'数据库类型：{db_type}')
            sql_content = chat_context.get('sql_content', '')
            if sql_content and len(sql_content.strip()) > 0:
                sql_preview = sql_content[:500] + ('...' if len(sql_content) > 500 else '')
                ctx_parts.append(f'当前 SQL 编辑器内容：\n```sql\n{sql_preview}\n```')
            template_id = chat_context.get('template_id', None)
            if template_id:
                ctx_parts.append(f'当前巡检模板 ID：{template_id}')
            
            if ctx_parts:
                system_prompt += '\n\n## 当前上下文（用户当前正在操作的界面信息）\n' + '\n'.join(ctx_parts) + \
                    '\n请根据上下文，给出更贴合用户当前场景的回答。'
        
        # 构建完整 prompt
        conversation = _format_conversation_history(session_id)
        if conversation:
            prompt = f"## 历史对话\n{conversation}\n\n## 当前问题\n{message}"
        else:
            prompt = message
        
        # RAG 检索
        rag_context = ''
        try:
            from modules.rag import RAGRetriever, VectorStore, OllamaEmbedding, OpenAIEmbedding
            vs = VectorStore()
            stats = vs.get_collection_stats()
            if stats.get('total_chunks', 0) > 0 and rag_enabled:
                if backend == 'ollama':
                    emb = OllamaEmbedding()
                else:
                    emb = OpenAIEmbedding(
                        api_url=cfg.get('online_api_url', 'https://api.openai.com/v1'),
                        model=cfg.get('online_model', 'text-embedding-3-small'),
                        api_key=cfg.get('api_key', ''),
                    )
                retriever = RAGRetriever(vs, emb)
                rag_context = retriever.retrieve_for_chat(message, db_type=chat_context.get('db_type'), top_k=5)
        except Exception:
            pass
        
        if rag_context:
            prompt = f"{prompt}\n\n{rag_context}\n\n请结合上述知识库内容回答用户的问题。"
        else:
            prompt = f"{prompt}\n\n（本次未检索到相关知识库文档，请基于自身知识回答）"
        
        # SSE 生成器（使用 queue + thread 实现真正的流式输出）
        import threading
        from queue import Queue

        def generate():
            q = Queue()
            full_chunks = []
            import sys

            print(f'[AI Stream] 后端启动, backend={backend}, model={cfg.get("model", cfg.get("online_model", "unknown"))}', flush=True)
            print(f'[AI Stream] prompt长度={len(prompt)}, system_prompt长度={len(system_prompt)}', flush=True)

            def stream_callback(chunk):
                """LLM 流式回调：将 chunk 放入队列"""
                full_chunks.append(chunk)
                q.put(('chunk', chunk))
                print(f'[AI Stream] chunk: {chunk!r}', flush=True)

            def run_llm():
                """在子线程中调用 LLM"""
                try:
                    print(f'[AI Stream] 开始调用 _call_llm...', flush=True)
                    result = _call_llm(prompt, system_prompt, stream_callback=stream_callback)
                    print(f'[AI Stream] _call_llm 返回: result={result!r}, len(full_chunks)={len(full_chunks)}', flush=True)
                    q.put(('done', result))
                except Exception as e:
                    print(f'[AI Stream] _call_llm 异常: {e}', file=sys.stderr, flush=True)
                    import traceback
                    traceback.print_exc()
                    q.put(('error', str(e)))

            # 启动 LLM 调用线程
            t = threading.Thread(target=run_llm, daemon=True)
            t.start()

            # 发送开始标记
            yield f"data: {json.dumps({'type': 'start'})}\n\n"

            # 从队列读取 chunk 并流式发送
            while True:
                msg_type, data = q.get()
                if msg_type == 'chunk':
                    chunk_data = json.dumps({'type': 'chunk', 'content': data}, ensure_ascii=False)
                    yield f"data: {chunk_data}\n\n"
                elif msg_type == 'done':
                    # 保存会话历史
                    full_text = ''.join(full_chunks) if full_chunks else (data or '')

                    # Fallback：如果流式返回空内容，尝试非流式调用
                    if not full_text.strip() and not str(data or '').startswith('['):
                        print(f'[AI Stream] 流式返回空内容, 执行 fallback 非流式调用...', flush=True)
                        try:
                            fallback_result = _call_llm(prompt, system_prompt, stream_callback=None)
                            print(f'[AI Stream] fallback 返回: {fallback_result!r}', flush=True)
                            if fallback_result and not fallback_result.startswith('['):
                                # 将完整结果作为单个 chunk 发送
                                fb_chunk = json.dumps({'type': 'chunk', 'content': fallback_result}, ensure_ascii=False)
                                yield f"data: {fb_chunk}\n\n"
                                full_text = fallback_result
                        except Exception as fb_err:
                            print(f'[AI Stream] fallback 失败: {fb_err}', file=sys.stderr, flush=True)
                            if not full_text:
                                full_text = f'[AI 生成失败，请检查 Ollama 服务是否正常运行]'

                    print(f'[AI Stream] 完成, full_text长度={len(full_text)}, 内容预览: {full_text[:200]!r}', flush=True)
                    _add_to_history(session_id, 'user', message)
                    _add_to_history(session_id, 'ai', full_text)
                    # 发送完成标记
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                elif msg_type == 'error':
                    error_data = json.dumps({'type': 'error', 'message': data}, ensure_ascii=False)
                    yield f"data: {error_data}\n\n"
                    break

            t.join(timeout=5)
        
        return Response(generate(), mimetype='text/event-stream')
    
    except Exception as e:
        print(f'[AI Stream] 顶层异常: {e}', file=sys.stderr, flush=True)
        error_json = json.dumps({'type': 'error', 'message': str(e)})
        return Response(f"data: {error_json}\n\n", mimetype='text/event-stream')


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """处理自然语言请求：支持知识问答和巡检执行两种模式（v2.6.2 支持上下文感知）"""
    try:
        data = request.get_json() or {}
        message = data.get('message', '')
        session_id = data.get('session_id', 'default')

        # v2.6.2：提取前端上下文
        chat_context = {
            'page': data.get('page', 'unknown'),
            'datasource_id': data.get('datasource_id', ''),
            'datasource_name': data.get('datasource_name', ''),
            'db_type': data.get('db_type', ''),
            'sql_content': data.get('sql_content', ''),
            'template_id': data.get('template_id', None),
        }

        if not message:
            return jsonify({'ok': False, 'type': 'error', 'message': '请输入您的问题'})

        # 0. 清空对话特殊指令
        if message.strip() in ('/clear', '/清空', '/new'):
            _clear_chat_session(session_id)
            return jsonify({
                'ok': True,
                'type': 'text',
                'message': '[OK] 对话已清空，开始新的对话。',
                'cleared': True,
            })

        # 0.5 分类意图：巡检执行 vs 知识问答
        chat_intent = _classify_chat_intent(message)

        # ─── 知识问答模式 ───
        if chat_intent == 'qa':
            qa_db_type = None
            # 尝试从消息中提取 db_type（用于 RAG 过滤）
            intent = parse_intent(message)
            qa_db_type = intent.get('db_type')
            if qa_db_type == 'unknown':
                qa_db_type = None

            result = _answer_chat_qa(session_id, message, db_type=qa_db_type, context=chat_context)
            resp = {
                'ok': True,
                'type': 'qa',
                'message': result['answer'],
                'rag_used': result['rag_used'],
                'rag_disabled': result['rag_disabled'],
            }
            return jsonify(resp)

        # ─── 巡检执行模式（原有逻辑）───
        # 1. 解析意图
        intent = parse_intent(message)
        db_type = intent.get('db_type', 'unknown')
        db_name = intent.get('db_name', '')
        scope = intent.get('scope', 'all')
        need_report = intent.get('need_report', scope == 'all')

        # 2. 匹配数据源
        ds = None
        matched_name = None  # 记录实际匹配到的数据源名称

        if db_name:
            ds = match_datasource(db_name)
            matched_name = db_name

        # 如果名称匹配失败，尝试按 db_type 筛选
        if not ds and db_type != 'unknown' and db_type:
            candidates = list_instances_by_type(db_type)
            if len(candidates) == 1:
                # 只有一个，直接选
                ds = match_datasource(candidates[0])
                matched_name = candidates[0]
            elif len(candidates) > 1:
                # 多个候选，询问用户
                names_str = '、'.join(candidates)
                return jsonify({
                    'ok': False,
                    'type': 'ask',
                    'message': _t('webui.chat_ask_multiple').format(
                        db_type=db_type.upper(), count=len(candidates), names=names_str),
                    'intent': intent,
                    'candidates': candidates,
                    'db_type': db_type,
                })

        if not ds:
            # 尝试从请求体获取连接参数
            ds = {
                'host': data.get('host', ''),
                'port': data.get('port', 3306),
                'user': data.get('user', ''),
                'password': data.get('password', ''),
                'database': data.get('database', ''),
                'service_name': data.get('service_name', ''),
                'sid': data.get('sid', ''),
            }

        # 如果既没有匹配到数据源，也没有提供连接信息，返回提示
        if not ds or not ds.get('host'):
            # 如果 db_name 不为空但匹配失败，列出同类型数据源
            if db_name and db_type != 'unknown' and db_type:
                candidates = list_instances_by_type(db_type)
                if candidates:
                    names_str = '、'.join(candidates)
                    return jsonify({
                        'ok': False,
                        'type': 'ask',
                        'message': _t('webui.chat_ask_not_found').format(
                            db_name=db_name, db_type=db_type.upper(), names=names_str),
                        'intent': intent,
                        'candidates': candidates,
                        'db_type': db_type,
                    })
            return jsonify({
                'ok': False,
                'type': 'error',
                'message': _t('webui.chat_ask_no_name'),
                'intent': intent,
            })

        # 3. 根据 scope 执行查询或启动巡检
        if scope in ('connection_count', 'lock_wait', 'slow_queries') and not need_report:
            # 简单查询，直接返回文本
            result = execute_simple_query(ds, db_type, scope)
            return jsonify({
                'ok': True,
                'type': 'text',
                'message': result,
                'intent': intent,
            })
        else:
            # 全库巡检，启动任务
            task_id = str(uuid.uuid4())

            # 构建 db_info 格式（与现有逻辑一致）
            db_info = {
                'ip': ds.get('host', ''),
                'port': int(ds.get('port', 3306)),
                'user': ds.get('user', ''),
                'password': ds.get('password', ''),
                'database': ds.get('database') or ('highgo' if db_type == 'hgdb' else ('ivorysql' if db_type == 'ivorysql' else ('postgres' if db_type == 'pg' else ('DAMENG' if db_type == 'dm' else '')))),
                'service_name': ds.get('service_name') or ds.get('sid'),
                'name': ds.get('name', db_name or ds.get('host', '')),
            }
            # SQL Server (JDBC) 双轨驱动参数透传
            if db_type == 'sqlserver_jdbc':
                # 旧实例缺失该字段时缺省走 jdbc（显式配置的 odbc/auto 仍保留）
                db_info['connection_mode'] = ds.get('connection_mode') or 'jdbc'
                db_info['jdbc_url'] = ds.get('jdbc_url') or ''
                db_info['encrypt'] = bool(ds.get('encrypt', False))
                db_info['trust_server_certificate'] = bool(ds.get('trust_server_certificate', True))

            inspector_name = data.get('inspector_name', 'Jack')

            tasks[task_id] = {
                'id': task_id,
                'db_type': db_type,
                'db_info': db_info,
                'inspector': inspector_name,
                'status': 'running',
                'started_at': datetime.datetime.now().isoformat(),
            }

            # 启动巡检线程
            db_info['_db_type'] = db_type
            task_func_map = {
                'mysql': run_inspection_task,
                'pg': run_inspection_task,
                'oracle': run_inspection_task,
                'dm': run_inspection_task,
                'sqlserver': run_inspection_task,
                'tidb': run_inspection_task,
                'ivorysql': run_inspection_task,
                'kingbase': run_inspection_task,
                'yashandb': run_inspection_task,
                'gbase':   run_inspection_task,
                'oceanbase': run_inspection_task,
            }
            task_func = task_func_map.get(db_type, run_inspection_task)
            t = threading.Thread(target=task_func, args=(task_id, db_info, inspector_name))
            t.daemon = True
            t.start()

            return jsonify({
                'ok': True,
                'type': 'report',
                'task_id': task_id,
                'message': f'已启动 {db_name or db_type} 的巡检任务，请稍候...',
                'intent': intent,
                'matched_datasource': {
                    'name': ds.get('name', ''),
                    'host': ds.get('host', ''),
                    'port': ds.get('port', ''),
                    'db_type': db_type,
                },
            })

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        return jsonify({'ok': False, 'type': 'error', 'message': f'处理失败: {e}'})


@app.route('/api/chat/task/<task_id>')
def api_chat_task_status(task_id):
    """查询聊天巡检任务状态"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'ok': False, 'error': '任务不存在'}), 404

    offset = int(request.args.get('offset', 0))
    log_list = task.get('log', [])

    result = {
        'ok': True,
        'status': task.get('status', 'running'),
        'log': log_list[offset:],
        'offset': len(log_list),
    }

    # 如果任务完成，添加报告信息
    if task.get('status') == 'done' and task.get('report_file'):
        result['report_file'] = task.get('report_file')
        result['report_name'] = task.get('report_name', 'report.docx')

    # 如果任务出错，附加错误信息
    if task.get('status') == 'error' and task.get('error_msg'):
        result['error_msg'] = task.get('error_msg')

    return jsonify(result)


# ══════════════════════════════════════════════════════════════
#  巡检结果 API
# ══════════════════════════════════════════════════════════════

@app.route('/api/inspection/<task_id>/datasource', methods=['GET'])
def api_inspection_datasource(task_id):
    """获取巡检任务的数据源信息"""
    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})

    # 获取任务信息
    task = get_task(task_id)
    if not task:
        return jsonify({'ok': False, 'error': '任务不存在'})

    datasource_id = task.get('datasource_id')
    if not datasource_id:
        return jsonify({'ok': False, 'error': '任务无数据源信息'})

    # 获取数据源详情
    datasource = im.get_instance_decrypted(datasource_id)
    if not datasource:
        return jsonify({'ok': False, 'error': '数据源不存在'})

    return jsonify({
        'ok': True,
        'datasource_id': datasource_id,
        'datasource': {
            'name': datasource.get('name', ''),
            'db_type': datasource.get('db_type', ''),
            'host': datasource.get('host', ''),
            'port': datasource.get('port', 0)
        }
    })


@app.route('/api/inspection/<task_id>/issues', methods=['GET'])
def api_inspection_issues(task_id):
    """获取巡检发现的问题列表（含 fix_sql）"""
    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})

    # 获取任务信息
    task = get_task(task_id)
    if not task:
        return jsonify({'ok': False, 'error': '任务不存在'})

    # 从任务上下文中获取 issues
    issues = task.get('auto_analyze', [])
    return jsonify({'ok': True, 'issues': issues})


# ══════════════════════════════════════════════════════════════
#  SocketIO 事件
# ══════════════════════════════════════════════════════════════

@socketio.on('connect')
def on_connect():
    pass

@socketio.on('join')
def on_join(data):
    task_id = data.get('task_id')
    if task_id:
        join_room(task_id)
        socketio.emit('log', {'msg': _t('webui.ws_connected_waiting').format(ts=_ts())}, room=task_id)


# ══════════════════════════════════════════════════════════════
#  远程终端 Socket.IO 事件
# ══════════════════════════════════════════════════════════════

# 活跃终端会话: {sid: {'ssh': SSHClient, 'channel': Channel, 'thread': Thread, 'instance_id': str}}
_remote_sessions = {}
_remote_sessions_lock = threading.Lock()


def _remote_shell_worker(sid, instance_id, ssh_host, ssh_port, ssh_user, ssh_password, ssh_key_file):
    """在原生线程中执行 paramiko SSH 连接与读取循环。

    源码模式未打 gevent monkey patch，paramiko 的 socket IO 会阻塞 gevent hub；
    因此远程终端的阻塞操作必须放到原生线程，避免卡死整个 WebSocket 事件循环。
    """
    import paramiko
    import time

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        if ssh_key_file and os.path.isfile(ssh_key_file):
            client.connect(hostname=ssh_host, port=ssh_port, username=ssh_user,
                           key_filename=ssh_key_file, timeout=10,
                           look_for_keys=False, allow_agent=False,
                           disabled_algorithms={'pubkeys': ['ssh-rsa']})
        elif ssh_password:
            client.connect(hostname=ssh_host, port=ssh_port, username=ssh_user,
                           password=ssh_password, timeout=10,
                           look_for_keys=False, allow_agent=False,
                           disabled_algorithms={'pubkeys': ['ssh-rsa']})
        else:
            socketio.emit('remote_shell_status', {'status': 'error', 'msg': 'SSH 密码或密钥文件为空'}, room=sid)
            return
    except Exception as e:
        socketio.emit('remote_shell_status', {'status': 'error', 'msg': f'SSH 连接失败: {e}'}, room=sid)
        return

    try:
        channel = client.invoke_shell(term='xterm', width=120, height=30)
    except Exception as e:
        socketio.emit('remote_shell_status', {'status': 'error', 'msg': f'打开 SSH Shell 失败: {e}'}, room=sid)
        client.close()
        return

    # 通知前端已连接
    socketio.emit('remote_shell_status', {'status': 'connected', 'msg': f'已连接 {ssh_user}@{ssh_host}:{ssh_port}'}, room=sid)

    # 读取循环：在原生线程中阻塞读取不影响 gevent 事件循环
    while True:
        if channel.closed:
            break
        if channel.recv_ready():
            try:
                recv_data = channel.recv(4096).decode('utf-8', errors='replace')
                socketio.emit('remote_shell_output', {'data': recv_data}, room=sid)
            except Exception:
                break
        time.sleep(0.05)

    # 连接断开清理
    with _remote_sessions_lock:
        session = _remote_sessions.pop(sid, None)
    if session:
        try:
            session['channel'].close()
        except Exception:
            pass
        try:
            session['ssh'].close()
        except Exception:
            pass
    socketio.emit('remote_shell_status', {'status': 'disconnected', 'msg': '连接已断开'}, room=sid)


@socketio.on('remote_shell_connect')
def on_remote_shell_connect(data):
    """SSH 连接远程服务器（前置校验后交给原生线程执行阻塞 IO）"""
    instance_id = data.get('instance_id', '')
    if not instance_id:
        socketio.emit('remote_shell_status', {'status': 'error', 'msg': '缺少实例 ID'}, room=request.sid)
        return

    try:
        from modules.pro import get_instance_manager
        im = get_instance_manager()
        inst = im.get_instance_decrypted(instance_id)
        if not inst:
            socketio.emit('remote_shell_status', {'status': 'error', 'msg': '数据源不存在'}, room=request.sid)
            return
        if not inst.get('ssh_enabled'):
            socketio.emit('remote_shell_status', {'status': 'error', 'msg': '该数据源未启用 SSH'}, room=request.sid)
            return

        ssh_host = inst.get('ssh_host', '')
        ssh_port = int(inst.get('ssh_port', 22))
        ssh_user = inst.get('ssh_user', '')
        ssh_password = inst.get('ssh_password', '') or ''
        ssh_key_file = inst.get('ssh_key_file', '') or ''

        sid = request.sid

        # 关闭已有会话（在主线程/gevent 协程中执行，避免跨线程操作 dict）
        with _remote_sessions_lock:
            old = _remote_sessions.pop(sid, None)
        if old:
            try:
                old['channel'].close()
                old['ssh'].close()
            except Exception:
                pass
            old['thread'].join(timeout=3)

        # 在原生线程中执行 paramiko 阻塞连接，避免卡死 gevent hub
        t = threading.Thread(
            target=_remote_shell_worker,
            args=(sid, instance_id, ssh_host, ssh_port, ssh_user, ssh_password, ssh_key_file),
            daemon=True
        )
        t.start()

    except Exception as e:
        import traceback
        traceback.print_exc()
        socketio.emit('remote_shell_status', {'status': 'error', 'msg': str(e) or 'SSH 连接异常'}, room=request.sid)


@socketio.on('remote_shell_input')
def on_remote_shell_input(data):
    """接收前端终端输入，发送到 SSH channel"""
    sid = request.sid
    with _remote_sessions_lock:
        session = _remote_sessions.get(sid)
    if session:
        try:
            session['channel'].send(data.get('data', ''))
        except Exception:
            pass


@socketio.on('remote_shell_resize')
def on_remote_shell_resize(data):
    """终端窗口大小调整"""
    sid = request.sid
    with _remote_sessions_lock:
        session = _remote_sessions.get(sid)
    if not session:
        return
    try:
        cols = int(data.get('cols', 80))
        rows = int(data.get('rows', 24))
        session['channel'].resize_pty(width=cols, height=rows)
    except Exception:
        pass


@socketio.on('remote_shell_disconnect')
def on_remote_shell_disconnect():
    """断开 SSH 连接"""
    sid = request.sid
    with _remote_sessions_lock:
        session = _remote_sessions.pop(sid, None)
    if session:
        try:
            session['channel'].close()
            session['ssh'].close()
        except Exception:
            pass
        session['thread'].join(timeout=3)
        socketio.emit('remote_shell_status', {'status': 'disconnected', 'msg': '已断开'}, room=sid)


@socketio.on('disconnect')
def on_disconnect():
    """前端断开时清理 SSH 会话"""
    sid = request.sid
    with _remote_sessions_lock:
        session = _remote_sessions.pop(sid, None)
    if session:
        try:
            session['channel'].close()
            session['ssh'].close()
        except Exception:
            pass
        session['thread'].join(timeout=3)


def _setup_driver_paths():
    """自动配置数据库客户端驱动 PATH，让 yasdb/oracle 能找到底层 C 库。
    
    驱动目录结构（按平台子目录组织）：
      drivers/yashandb/lib/
        windows-x64/   yascli.dll + 依赖
        windows-x86/   yascli.dll + 依赖（32位 Python）
        linux-x64/     libyascli.so
        linux-arm/     libyascli.so
      drivers/oracle_client/
        windows-x64/
        linux-x64/
        ...
    """
    base_dir = Path(__file__).resolve().parent
    drivers_dir = base_dir / 'drivers'

    system = platform.system().lower()
    is_windows = system == 'windows'
    machine = platform.machine().lower()

    # 精确判断平台+位数（yashandb 和 oracle 均需区分）
    if is_windows:
        # struct.calcsize('P') 返回指针字节数，8=64位，4=32位
        import struct
        bits = struct.calcsize('P') * 8
        yasdb_plat = 'windows-x64' if bits == 64 else 'windows-x86'
        oracle_plat = 'windows_x64' if bits == 64 else 'windows_x86'
    elif system == 'linux':
        if machine in ('aarch64', 'arm64', 'armv8l'):
            yasdb_plat = 'linux-arm'
            oracle_plat = 'linux_arm'
        else:
            yasdb_plat = 'linux-x64'
            oracle_plat = 'linux_x64'
    elif system == 'darwin':
        if machine in ('arm64', 'aarch64'):
            yasdb_plat = 'darwin-arm64'
            oracle_plat = 'darwin_arm64'
        else:
            yasdb_plat = 'darwin-x64'
            oracle_plat = 'darwin_x64'
    else:
        return

    def _add_path(dirpath):
        """将目录加入 DLL/SO 搜索路径"""
        if not dirpath.exists():
            return
        if is_windows:
            try:
                os.add_dll_directory(str(dirpath))
            except Exception:
                pass
            os.environ['PATH'] = str(dirpath) + os.pathsep + os.environ.get('PATH', '')
        else:
            os.environ['LD_LIBRARY_PATH'] = str(dirpath) + ':' + os.environ.get('LD_LIBRARY_PATH', '')

    # Oracle Instant Client
    _add_path(drivers_dir / 'oracle_client' / oracle_plat)

    # YashanDB Client
    # yacli.py (ctypesgen 生成) 在模块级别执行 load_library("yascli")，
    # 它有自己的 DLL 搜索逻辑，不受 PATH 或 os.add_dll_directory 影响。
    # 搜索顺序：other_dirs → yacli.py 同目录 → find_library → 平台路径 → CWD
    # 由于模块级加载无法提前调用 add_library_search_dirs，
    # 解决方案：将 DLL/SO 复制到 yacli.py 同目录下
    #
    # 目录结构：drivers/yashandb/lib/<platform>/lib/*.dll（解压后含 bin/include/lib 三级）
    # 真正的库文件在平台子目录下的 lib/ 里，例如：
    #   drivers/yashandb/lib/windows-x64/lib/yascli.dll
    yashandb_lib = drivers_dir / 'yashandb' / 'lib' / yasdb_plat / 'lib'
    if yashandb_lib.exists():
        # 查找 yacli.py 的安装目录
        yacli_dir = None
        for p in sys.path:
            candidate = os.path.join(p, 'yasdb', 'libs')
            if os.path.exists(os.path.join(candidate, 'yacli.py')):
                yacli_dir = candidate
                break
        if yacli_dir:
            import shutil
            # Windows: 复制 .dll；Linux: 复制 .so*
            patterns = ['*.dll'] if is_windows else ['*.so', '*.so.*']
            for pat in patterns:
                for lib_file in yashandb_lib.glob(pat):
                    dst = os.path.join(yacli_dir, lib_file.name)
                    if not os.path.exists(dst):
                        try:
                            shutil.copy2(str(lib_file), dst)
                            print(f'[DBCheck] YashanDB: copied {lib_file.name} -> {dst}')
                        except Exception as e:
                            print(f'[DBCheck] YashanDB: failed to copy {lib_file.name}: {e}')
        # 同时加入 PATH（部分依赖 DLL 不在 yacli.py 目录，靠 PATH 兜底）
        _add_path(yashandb_lib)


def _get_gbase_driver():
    """
    ⚠️ DEPRECATED：保留仅作向下兼容。GBase JDBC 驱动解析已统一走到
    modules.entrypoints.main_gbase.resolve_gbase_driver()(优先驱动管理，
    否则回退 drivers/gbase/ 自动发现)。新代码请直接调用该函数。
    """
    import os, glob
    driver_dir = os.path.join(BASE_DIR, 'drivers', 'gbase')
    if not os.path.isdir(driver_dir):
        print(f"[GBase] 驱动目录不存在: {driver_dir}")
        return None
    pattern = os.path.join(driver_dir, '*.jar')
    jars = glob.glob(pattern)
    if not jars:
        print(f"[GBase] 驱动目录中没有找到 .jar 文件: {driver_dir}")
        return None
    print(f"[GBase] 找到驱动文件: {jars}")
    return jars  # 返回列表，不是字符串


# ── 数据管理 API ───────────────────────────────────────────

@app.route('/api/home_stats', methods=['GET'])
def api_home_stats():
    """首页综合统计：巡检次数、备份信息、基线条数、数据源等"""
    result = {
        'inspection_by_db': {},      # 各库巡检总次数
        'inspection_total': 0,
        'server_inspection_total': 0,
        'backup_count': 0,
        'backup_latest': None,
        'baseline_by_db': {},         # 各库基线条数
        'baseline_total': 0,
        'datasource_by_db': {},       # 各库数据源数
        'datasource_total': 0,
        'scheduled_jobs': 0,
        'scheduled_active': 0,
        'rag_docs': 0,
        'rag_chunks': 0,
        'template_count': 0,
    }
    try:
        # 运行时数据目录统一走中央路径：frozen 下为 <exe>/_internal/data，
        # 与各写入方（dal / server.inspect / db_history / 插件）保持一致。
        # 1. 巡检次数（Pro版 data/history.db 中的 snapshots JOIN history_instances）
        history_db = str(paths.DATA_DIR / 'history.db')
        if os.path.exists(history_db):
            conn = sqlite3.connect(history_db)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) as cnt FROM snapshots
            """)
            row = cur.fetchone()
            result['inspection_total'] = row['cnt'] if row else 0

            # 按 db_type 统计（snapshots 无 db_type，需 JOIN history_instances）
            cur.execute("""
                SELECT hi.db_type, COUNT(s.id) as cnt
                FROM snapshots s
                JOIN history_instances hi ON s.instance_key = hi.key
                GROUP BY hi.db_type
            """)
            for r in cur.fetchall():
                result['inspection_by_db'][r['db_type']] = r['cnt']
            conn.close()

        # 2. 服务器巡检次数
        server_db = str(paths.DATA_DIR / 'server_history.db')
        if os.path.exists(server_db):
            try:
                conn = sqlite3.connect(server_db)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) as cnt FROM server_inspection_history")
                row = cur.fetchone()
                result['server_inspection_total'] = row['cnt'] if row else 0
                conn.close()
            except Exception:
                pass

        # 3. 备份信息
        try:
            from modules.web.data_manager import list_backups
            backups = list_backups()
            result['backup_count'] = len(backups)
            if backups:
                result['backup_latest'] = backups[0].get('name')
        except Exception:
            pass

        # 4. 基线配置
        inspection_db = str(INSPECTION_DB)
        if os.path.exists(inspection_db):
            conn = sqlite3.connect(inspection_db)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT db_type, COUNT(*) as cnt
                FROM inspection_baseline
                GROUP BY db_type
            """)
            for r in cur.fetchall():
                result['baseline_by_db'][r['db_type']] = r['cnt']
            cur.execute("SELECT COUNT(*) as cnt FROM inspection_baseline")
            row = cur.fetchone()
            result['baseline_total'] = row['cnt'] if row else 0
            conn.close()

        # 5. 数据源（Pro版）
        try:
            from modules.pro import get_instance_manager
            im = get_instance_manager()
            stats = im.get_statistics()
            result['datasource_total'] = stats.get('total_instances', 0)
            result['datasource_by_db'] = {
                'mysql': stats.get('mysql', 0),
                'pg': stats.get('pg', 0),
                'oracle': stats.get('oracle', 0),
                'sqlserver': stats.get('sqlserver', 0),
                'dm': stats.get('dm', 0),
                'tidb': stats.get('tidb', 0),
            }
        except Exception:
            pass

        # 6. 定时任务
        try:
            from modules.web.app import _get_scheduler
            sm = _get_scheduler()
            jobs = sm.list_jobs()
            result['scheduled_jobs'] = len(jobs)
            result['scheduled_active'] = sum(1 for j in jobs if j.get('enabled'))
        except Exception:
            pass

        # 7. RAG 知识库
        try:
            from modules.web.app import _get_rag_manager
            mgr = _get_rag_manager()
            if mgr:
                docs = mgr.list_documents()
                result['rag_docs'] = len(docs)
                result['rag_chunks'] = sum(d.get('chunks', 0) for d in docs)
        except Exception:
            pass

        # 8. 巡检模板（含各模板章节数）
        if os.path.exists(inspection_db):
            conn = sqlite3.connect(inspection_db)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM inspection_template")
            row = cur.fetchone()
            result['template_count'] = row['cnt'] if row else 0

            # 各模板章节数
            cur.execute("""
                SELECT t.id, t.template_name_zh, t.db_type, COUNT(c.id) as chapter_count
                FROM inspection_template t
                LEFT JOIN inspection_chapter c ON t.id = c.template_id
                GROUP BY t.id
                ORDER BY t.db_type, t.id
            """)
            result['template_chapters'] = [
                {'id': r['id'], 'name': r['template_name_zh'],
                 'db_type': r['db_type'], 'chapters': r['chapter_count']}
                for r in cur.fetchall()
            ]
            conn.close()

        # 9. 规则引擎统计
        try:
            from modules.pro.rule_engine import get_rule_engine
            engine = get_rule_engine()
            all_rules = engine.builtin_rules + engine.custom_rules
            result['rule_engine_total'] = len(all_rules)
            result['rule_builtin_count'] = len(engine.builtin_rules)
            result['rule_custom_count'] = len(engine.custom_rules)
            # 按 db_type 统计
            db_types = ['mysql', 'mariadb', 'postgresql', 'pg', 'oracle', 'dm8', 'dm', 'sqlserver', 'tidb', 'ivorysql', 'yashandb', 'kingbase']
            rule_by_db = {}
            for dt in db_types:
                rules_for_db = engine.list_rules(db_type=dt)
                if rules_for_db:
                    rule_by_db[dt] = len(rules_for_db)
            result['rule_engine_by_db'] = rule_by_db
        except Exception:
            result['rule_engine_total'] = 0
            result['rule_builtin_count'] = 0
            result['rule_custom_count'] = 0
            result['rule_engine_by_db'] = {}

    except Exception as e:
        pass

    return jsonify({'success': True, 'stats': result})


@app.route('/api/data_management/files', methods=['GET'])
def api_data_management_files():
    """获取数据文件信息"""
    try:
        from modules.web.data_manager import get_data_files_info
        files = get_data_files_info()
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/data_management/backups', methods=['GET'])
def api_data_management_backups():
    """列出所有备份"""
    try:
        from modules.web.data_manager import list_backups
        backups = list_backups()
        return jsonify({'success': True, 'backups': backups})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/data_management/backup', methods=['POST'])
def api_data_management_backup():
    """创建数据备份"""
    try:
        from modules.web.data_manager import backup_data
        data = request.get_json(silent=True) or {}
        backup_name = data.get('backup_name', '')
        result = backup_data(backup_name=backup_name or None)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/data_management/restore', methods=['POST'])
def api_data_management_restore():
    """从备份还原数据"""
    try:
        from modules.web.data_manager import restore_data
        data = request.get_json() or {}
        backup_name = data.get('backup_name', '')
        selected_items = data.get('selected_items')
        if not backup_name:
            return jsonify({'success': False, 'message': '请指定备份名称'})
        result = restore_data(backup_name, selected_items)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/data_management/backup/<path:name>', methods=['DELETE'])
def api_data_management_delete_backup(name):
    """删除指定备份"""
    try:
        from modules.web.data_manager import delete_backup
        result = delete_backup(name)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/data_management/upgrade_check', methods=['GET'])
def api_data_management_upgrade_check():
    """检测升级状态"""
    try:
        from modules.web.data_manager import check_upgrade_ready
        result = check_upgrade_ready()
        return jsonify({'success': True, **result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ─── 随机语录 API ───
import random as _random

_quotes_cache = None
_quotes_lock = None

def _load_quotes():
    """懒加载语录库（只加载一次）"""
    global _quotes_cache
    if _quotes_cache is not None:
        return _quotes_cache
    try:
        import json as _json
        _quotes_file = str(paths.QUOTES_JSON)
        with open(_quotes_file, 'r', encoding='utf-8') as f:
            data = _json.load(f)
        _quotes_cache = data.get('quotes', [])
    except Exception:
        _quotes_cache = []
    return _quotes_cache

@app.route('/version.json', methods=['GET'])
def api_version_json():
    """返回版本信息 JSON，供登录页动态加载版本号"""
    # PyInstaller 打包后 __file__ 指向 _internal 目录，需用 sys.executable 定位
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
        vpath = os.path.join(base, 'version.json')
    else:
        vpath = str(paths.VERSION_JSON)
    if os.path.exists(vpath):
        return send_file(vpath, mimetype='application/json')
    return jsonify({'version': 'v2.5.1'})


@app.route('/api/quotes/random', methods=['GET'])
def api_random_quote():
    """返回一条随机语录"""
    quotes = _load_quotes()
    if not quotes:
        return jsonify({'ok': False, 'msg': '语录库为空'})
    q = _random.choice(quotes)
    return jsonify({'ok': True, 'quote': q})


# ═══════════════════════════════════════════════════════════════
#  DM8 离线存储健康检查
# ═══════════════════════════════════════════════════════════════

dm8_offline_tasks = {}


@app.route('/api/dm8_offline/check', methods=['POST'])
def api_dm8_offline_check():
    """启动 DM8 离线存储健康检查（支持本地/远程 SSH）"""
    try:
        data = request.json
        db_dir = data.get('db_dir', '').strip()
        page_size = int(data.get('page_size', 0) or 0)
        host_type = data.get('host_type', 'local').strip()

        if not db_dir:
            return jsonify({'ok': False, 'msg': '请指定 DM8 数据文件目录'})

        if host_type == 'local':
            if not os.path.isdir(db_dir):
                return jsonify({'ok': False, 'msg': f'目录不存在: {db_dir}'})
        else:
            # 远程模式：验证 SSH 参数
            ssh_host = data.get('ssh_host', '').strip()
            if not ssh_host:
                return jsonify({'ok': False, 'msg': '请填写 SSH 主机地址'})

        task_id = str(uuid.uuid4())
        dm8_offline_tasks[task_id] = {
            'id': task_id,
            'db_dir': db_dir,
            'page_size': page_size,
            'host_type': host_type,
            'status': 'running',
            'started_at': datetime.datetime.now().isoformat(),
        }

        def _run_offline_check(tid, params):
            try:
                from modules.ingest.dm8_offline import (
                    DM8OfflineHealthChecker, DM8RemoteHealthChecker
                )
                if params.get('host_type') == 'remote':
                    checker = DM8RemoteHealthChecker(
                        db_dir=params['db_dir'],
                        ssh_host=params['ssh_host'],
                        ssh_port=int(params.get('ssh_port', 22)),
                        ssh_user=params.get('ssh_user', 'root'),
                        ssh_password=params.get('ssh_password', ''),
                        ssh_key_file=params.get('ssh_key_file', ''),
                        page_size=params.get('page_size', 0),
                    )
                else:
                    checker = DM8OfflineHealthChecker(
                        params['db_dir'], params.get('page_size', 0)
                    )

                result = checker.run()
                dm8_offline_tasks[tid]['status'] = 'done'
                dm8_offline_tasks[tid]['result'] = result
            except Exception as e:
                dm8_offline_tasks[tid]['status'] = 'error'
                dm8_offline_tasks[tid]['error'] = str(e)
                dm8_offline_tasks[tid]['traceback'] = traceback.format_exc()
            finally:
                if 'checker' in dir():
                    try:
                        checker.close()
                    except Exception:
                        pass

        check_params = {
            'db_dir': db_dir,
            'page_size': page_size,
            'host_type': host_type,
            'ssh_host': data.get('ssh_host', ''),
            'ssh_port': data.get('ssh_port', 22),
            'ssh_user': data.get('ssh_user', 'root'),
            'ssh_password': data.get('ssh_password', ''),
            'ssh_key_file': data.get('ssh_key_file', ''),
        }

        t = threading.Thread(target=_run_offline_check,
                             args=(task_id, check_params))
        t.daemon = True
        t.start()

        return jsonify({'ok': True, 'task_id': task_id})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/dm8_offline/status/<task_id>', methods=['GET'])
def api_dm8_offline_status(task_id):
    """查询 DM8 离线检查任务状态"""
    task = dm8_offline_tasks.get(task_id)
    if not task:
        return jsonify({'ok': False, 'msg': '任务不存在'})
    return jsonify({
        'ok': True,
        'status': task['status'],
        'result': task.get('result'),
        'error': task.get('error'),
    })


@app.route('/api/dm8_offline/report/<task_id>', methods=['GET'])
def api_dm8_offline_report(task_id):
    """获取 DM8 离线检查的 Word 报告（同时落盘到 reports 目录）"""
    task = dm8_offline_tasks.get(task_id)
    if not task or task['status'] != 'done':
        return jsonify({'ok': False, 'msg': '报告尚未就绪'})

    from modules.ingest.dm8_offline import generate_offline_report_word
    from flask import send_file

    result = task['result']
    mode = result.get('mode', 'local')

    # 生成文件名
    if mode == 'remote':
        ssh_host = result.get('ssh_host', 'remote')
        filename = f'DM8离线存储检查报告_SSH_{ssh_host}_{task_id[:8]}.docx'
    else:
        filename = f'DM8离线存储检查报告_本机_{task_id[:8]}.docx'

    # 落盘到 reports 目录（与其他巡检报告统一管理）
    reports_dir = str(paths.REPORTS_DIR)
    os.makedirs(reports_dir, exist_ok=True)
    ofile = os.path.join(reports_dir, filename)

    try:
        report_path = generate_offline_report_word(result, ofile)
        with open(report_path, 'rb') as f:
            file_data = f.read()
        return send_file(
            io.BytesIO(file_data),
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'生成报告失败: {e}'})


# ─────────────────────────────────────────────────────────────────────────────
# JDBC 驱动管理 API（Stage A：仅注册表 + 上传/删除/激活，不改插件）
# ─────────────────────────────────────────────────────────────────────────────
# 单文件上传硬上限 200MB（多数 JDBC 驱动 jar ≤ 100MB）
MAX_DRIVER_UPLOAD_BYTES = 200 * 1024 * 1024

@app.route('/api/drivers/types', methods=['GET'])
def api_drivers_types():
    """返回 db_type 清单；?hidden=1 返回已隐藏类型（回收站）"""
    try:
        from modules import driver_registry as dr
        if request.args.get('hidden', '0') in ('1', 'true', 'yes'):
            types = dr.list_hidden_db_types()
        else:
            types = dr.list_db_types()
        # 统计每个类型的已上传驱动数
        result = []
        for t in types:
            drivers = dr.list_drivers(t['key'])
            t2 = dict(t)
            t2['driver_count'] = len(drivers)
            t2['active_version'] = next((d['version'] for d in drivers if d['is_active']), '')
            result.append(t2)
        return jsonify({'ok': True, 'types': result})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/api/drivers/types/<db_type>/delete', methods=['POST'])
def api_drivers_types_delete(db_type):
    """隐藏（删除）某个数据库类型，同时清理该类型下所有驱动"""
    try:
        # 仅管理员可删除数据库/产品类型
        if not session.get('is_admin', False):
            return jsonify({'ok': False, 'msg': '只有管理员才能删除数据库类型', 'error': 'permission_denied'}), 403
        from modules import driver_registry as dr
        ok, msg = dr.hide_db_type(db_type)
        return jsonify({'ok': ok, 'msg': msg})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/api/drivers/types/<db_type>/restore', methods=['POST'])
def api_drivers_types_restore(db_type):
    """恢复之前隐藏的数据库类型"""
    try:
        from modules import driver_registry as dr
        ok, msg = dr.unhide_db_type(db_type)
        return jsonify({'ok': ok, 'msg': msg})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/api/drivers/types', methods=['POST'])
def api_drivers_types_create():
    """新增一个用户自定义数据库类型"""
    try:
        from modules import driver_registry as dr
        data = request.get_json(force=True, silent=True) or {}
        key = (data.get('key') or '').strip()
        name_zh = (data.get('name_zh') or '').strip()
        name_en = (data.get('name_en') or '').strip()
        driver_class_hint = (data.get('driver_class_hint') or '').strip()
        is_jdbc = bool(data.get('is_jdbc', True))
        ok, msg = dr.add_custom_db_type(key, name_zh, name_en, driver_class_hint, is_jdbc)
        return jsonify({'ok': ok, 'msg': msg})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/api/drivers/types/<db_type>/delete-custom', methods=['POST'])
def api_drivers_types_delete_custom(db_type):
    """真删用户自定义数据库类型（同时清理其下所有驱动）"""
    try:
        # 仅管理员可删除数据库/产品类型
        if not session.get('is_admin', False):
            return jsonify({'ok': False, 'msg': '只有管理员才能删除数据库类型', 'error': 'permission_denied'}), 403
        from modules import driver_registry as dr
        ok, msg = dr.delete_custom_db_type(db_type)
        return jsonify({'ok': ok, 'msg': msg})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/api/drivers/list', methods=['GET'])
def api_drivers_list():
    """列出某 db_type 的所有已登记驱动"""
    try:
        from modules import driver_registry as dr
        db_type = request.args.get('db_type', '').strip()
        if not db_type:
            return jsonify({'ok': False, 'error': 'db_type 不能为空'})
        drivers = dr.list_drivers(db_type)
        return jsonify({'ok': True, 'drivers': drivers, 'db_type': db_type})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/api/drivers/upload', methods=['POST'])
def api_drivers_upload():
    """
    上传新的 JDBC 驱动 jar
    表单字段: file (jar), db_type, version, driver_class (可选，自动用 hint 兜底), note (可选)
    """
    try:
        from modules import driver_registry as dr
        if 'file' not in request.files:
            return jsonify({'ok': False, 'error': '未收到文件'})
        f = request.files['file']
        # Flask 的 f.filename 是 Content-Disposition 里的原始文件名；curl 没传时
        # 会给临时名（如 tmp.XXXX.jar）。若文件名不像 jar，回退到占位名。
        orig_name = (f.filename or '').strip()
        if not orig_name or not orig_name.lower().endswith('.jar'):
            # 用 db_type + version 推断默认名
            from modules import driver_registry as dr
            hint = next((t['driver_class_hint'] for t in dr.list_db_types() if t['key'] == db_type), db_type or 'driver')
            # 从 hint 推导：com.mysql.cj.jdbc.Driver → mysql
            vendor = db_type or 'driver'
            version_safe = re.sub(r'[^A-Za-z0-9._\-+]', '_', version) if version else '1.0'
            orig_name = f'{vendor}-{version_safe}.jar'
            print(f'[driver_upload] 使用兜底文件名: {orig_name}')
        if not orig_name:
            return jsonify({'ok': False, 'error': '文件名为空'})

        # 立即校验 Content-Length，防超大上传
        cl = request.content_length or 0
        if cl > MAX_DRIVER_UPLOAD_BYTES:
            return jsonify({'ok': False, 'error': f'文件过大（>{MAX_DRIVER_UPLOAD_BYTES // 1024 // 1024}MB）'})

        db_type = (request.form.get('db_type') or '').strip()
        version = (request.form.get('version') or '').strip()
        driver_class = (request.form.get('driver_class') or '').strip()
        note = (request.form.get('note') or '').strip()

        # driver_class 为空时用 catalog 提示兜底
        if not driver_class:
            hint = next((t['driver_class_hint'] for t in dr.list_db_types() if t['key'] == db_type), '')
            driver_class = hint

        # 保存到临时文件后传给 driver_registry.add_driver
        import tempfile, os
        suffix = os.path.splitext(orig_name)[1] or '.jar'
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        f.save(tmp.name)
        tmp.close()

        ok, msg, new_id = dr.add_driver(
            db_type=db_type, version=version, driver_class=driver_class,
            src_path=tmp.name, original_filename=orig_name, note=note
        )
        # 清理临时文件（add_driver 成功后已移走）
        try:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)
        except Exception:
            pass

        if not ok:
            return jsonify({'ok': False, 'error': msg})
        return jsonify({'ok': True, 'msg': msg, 'id': new_id})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/api/drivers/<int:driver_id>/delete', methods=['POST'])
def api_drivers_delete(driver_id):
    """删除驱动（同时移走 jar 文件）"""
    try:
        from modules import driver_registry as dr
        ok, msg = dr.delete_driver(driver_id)
        return jsonify({'ok': ok, 'msg': msg})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/api/drivers/<int:driver_id>/activate', methods=['POST'])
def api_drivers_activate(driver_id):
    """设为该 db_type 的默认驱动"""
    try:
        from modules import driver_registry as dr
        ok, msg = dr.activate_driver(driver_id)
        return jsonify({'ok': ok, 'msg': msg})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]})


@app.route('/api/system/shutdown', methods=['POST'])
def api_system_shutdown():
    """本机强制退出（Git Bash/伪终端下 Ctrl+C 无效时的兜底通道）。

    背景：mintty/MSYS2 伪终端里 Ctrl+C 的模拟信号无法投递到原生 Windows
    Python，且无真实控制台事件——五层信号防线全部落空。此接口供本机
    手动触发退出：`curl -X POST http://127.0.0.1:5003/api/system/shutdown
    -H "X-Admin-Token: <启动时打印的管理令牌>"`。

    安全：仅允许本机回环来源 + X-Admin-Token 校验（随机 64 hex，启动时
    打印在控制台），局域网内无法调用。
    """
    try:
        from modules.web.api import _ADMIN_TOKEN
        _ra = (request.remote_addr or '')
        if _ra not in ('127.0.0.1', '::1'):
            return jsonify({'ok': False, 'error': '仅允许本机调用'}), 403
        if request.headers.get('X-Admin-Token', '') != _ADMIN_TOKEN:
            return jsonify({'ok': False, 'error': '管理令牌无效'}), 403
    except Exception:  # noqa: BLE001
        return jsonify({'ok': False, 'error': '校验失败'}), 500
    # 先回 200 再强杀：daemon 线程 os._exit(0) 不阻塞也不等待
    _shutdown_threading.Thread(target=_request_shutdown, daemon=True).start()
    return jsonify({'ok': True, 'msg': '正在退出...'})


def main():
    # ── 信号处理：确保 Ctrl+C / 关闭窗口 / SIGTERM 都能立即退出 ──
    # 设计要点（修复 gevent 模式 Ctrl+C 失效）：
    #   - gevent 的信号 watcher 必须挂在「运行 hub 的线程（主线程）」。把 server 放到
    #     子线程会让 hub 在子线程，导致 gevent.signal_handler 注册被 ValueError 吞掉、
    #     主线程的 signal.signal 重注册因无 hub 也无效——这正是之前「运行一阵后停不了」的根因。
    #   - 因此 server 直接在【主线程】运行，gevent hub 在主线程，所有信号机制才有效。
    #   - Windows 控制台处理器（OS 层）绕开一切 Python/gevent 接管，作为最可靠必杀。

    # ── 启动 Banner：必须是第一段控制台输出（早于下方插件加载日志）──
    _print_startup_banner()

    _setup_driver_paths()
    # ── 初始化插件系统 ──
    # 主加载器切换为 plugin_core.load_plugins()（双类型支持）：
    # 规则插件按 entry 载 __init__.py 并 register() 注册进 PluginRegistry；
    # 巡检插件保持原路径。仅加载 enabled 目录，避免把 available 中的未启用插件注册进来。
    try:
        from modules.pluginkit.core import load_plugins
        _enabled_dir = os.path.join(
            str(PROJECT_ROOT), 'plugins', 'enabled'
        )
        loaded_count = load_plugins(_enabled_dir)
        if loaded_count:
            print(f"[插件] 已加载 {loaded_count} 个插件（规则插件经 PluginRegistry 注册）")
    except Exception as e:
        print(f"[插件] 初始化跳过: {e}")

    # 启动时为已启用插件补齐模板/基线数据（打包发布后 data/ 为空库时必需）
    try:
        from modules.pluginkit.loader import seed_enabled_plugins_data
        seeded = seed_enabled_plugins_data(_enabled_dir)
        if seeded:
            print(f"[插件] 已补齐 {seeded} 个插件的模板/基线数据")
    except Exception as e:
        print(f"[插件] 模板/基线种子数据初始化跳过: {e}")

    # 驱动管理登记种子导入（打包分发后 drivers.db 为空库时，从随包
    # modules/config/drivers_seed.json 恢复用户打包前的驱动登记；幂等）。
    # 随后再扫描 drivers/ 目录自动登记：Docker/exe 分发时种子 JSON 未必随包
    # （本地生成、被 git 忽略），只要 jar 在，驱动管理页面即可开箱即用。
    try:
        from modules.driver_registry import seed_driver_registry, scan_driver_dirs
        _seeded_drivers = seed_driver_registry()
        if _seeded_drivers:
            print(f"[驱动] 已从随包种子导入 {_seeded_drivers} 条驱动登记")
        _scanned_drivers = scan_driver_dirs()
        if _scanned_drivers:
            print(f"[驱动] 已扫描 drivers/ 目录自动登记 {_scanned_drivers} 条驱动")
    except Exception as e:
        print(f"[驱动] 驱动登记种子导入跳过: {e}")

    # DocKB 官方文档知识库策展种子自动播种（幂等：仅追加缺失事实，
    # 不清空/覆盖用户数据）。data/doc_kb.db 不随包分发，Docker/exe 首次
    # 启动若不播种则 AI 诊断的官方事实召回为空。
    try:
        from modules.doc_kb.models import seed_from_json as seed_doc_kb
        _doc_kb_seeded = seed_doc_kb()
        if _doc_kb_seeded:
            print(f"[DocKB] 已自动播种官方文档知识库 {_doc_kb_seeded} 条事实")
    except Exception as e:
        print(f"[DocKB] 官方文档知识库种子播种跳过: {e}")

    # 确保巡检配置库存在：data/ 是运行时目录（不随包发布），打包后首次启动
    # 时 inspection.db 并不存在，需在此建库建表并写入预设模板/阈值/基线，
    # 否则「巡检配置管理」相关接口会报 "unable to open database file"。
    try:
        from modules.inspection.dal import init_database as _init_inspection_db
        _init_inspection_db()
    except Exception as _e:
        print(f"[startup] 初始化 inspection.db 失败（降级继续）: {_e}")

    _verify_agreement_integrity()

    port = 5003
    print(_t('webui.startup_msg').format(port=port))
    print("[提示] 按 Ctrl+C 停止服务\n")
    # Git Bash (mintty/MSYS2) 伪终端：Ctrl+C 的 MSYS2 模拟信号无法投递到原生
    # Windows Python，且没有真实控制台事件 → 五层信号处理全部落空。提前告知
    # 用户可用方案，避免「按 Ctrl+C 结束不掉进程」的困惑。
    if os.environ.get('MSYSTEM') and not getattr(sys, 'frozen', False):
        print("[提示] 当前运行在 Git Bash/MSYS2 伪终端下，Ctrl+C 可能无法结束本进程（mintty 信号无法投递到原生 Windows Python）。")
        print("       可选退出方式：")
        print("         ① 改用 PowerShell 或 cmd 启动（Ctrl+C 直接生效）；")
        print("         ② Git Bash 下用 `winpty python web_ui.py` 启动；")
        print("         ③ 本机触发退出：curl -X POST http://127.0.0.1:5003/api/system/shutdown -H \"X-Admin-Token: <上面的管理令牌>\"")
        print("         ④ 强杀：taskkill /F /PID <pid>（tasklist | findstr python 查 pid）\n")

    # 1) Windows 控制台级处理器（OS 层，cmd/PowerShell 关闭窗口/Ctrl+C 必杀，
    #    gevent 吞不掉、PyInstaller 同样有效）+ 持续保活确保永远抢在最前
    _register_console_ctrl_handler()
    _start_ctrl_keepalive()

    # 2) 标准 Python 信号（非 gevent 模式下主线程注册即生效）
    _install_python_signals()

    # 2.5) Git Bash / MSYS2 伪终端兜底：那里没有真实 Windows 控制台事件，
    #      第 1 步的 SetConsoleCtrlHandler 永不触发、SIGINT 也投递不到原生进程，
    #      只能靠直接读 stdin 的 Ctrl+C 原始字符（0x03）与 EOF 来强杀。
    _start_stdin_watchdog()

    # 3) gevent 模式：必须在【主线程】用 gevent.signal_handler 注册，因为它的信号 watcher
    #    挂在当前线程（主线程）的 hub 上。server 即将在主线程运行，hub 会在此线程启动，
    #    此处注册即可成为 SIGINT/SIGTERM 的权威退出入口（直接 os._exit(0)）。
    #    注意：gevent.signal_handler 调用时会传 (sig, frame)，故 _request_shutdown 已兼容该签名。
    if _socketio_async_mode == 'gevent':
        try:
            gevent.signal_handler(signal.SIGINT, _request_shutdown)
            gevent.signal_handler(signal.SIGTERM, _request_shutdown)
            if platform.system().lower() == 'windows':
                gevent.signal_handler(signal.SIGBREAK, _request_shutdown)
        except Exception:
            pass

        # 3.5) hub 起来后周期性抢回 Python 层信号处理器，防止运行期被 gevent/
        #      flask-socketio 改写导致「刚启动能停、跑一阵停不了」。
        _start_signal_rearm()

    # 4) 主线程运行 server（gevent hub 在此线程），Ctrl+C 由 1~3.5 接管 → os._exit(0)
    try:
        socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        _request_shutdown()
    except BaseException as _e:  # 含 SystemExit/GreenletExit：一律强杀，绝不留残进程
        try:
            print(f"[主程序] server 退出: {type(_e).__name__}: {_e}")
        except Exception:
            pass
    _request_shutdown()


if __name__ == '__main__':
    main()
