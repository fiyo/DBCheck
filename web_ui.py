# coding: utf-8
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck Web UI - Flask 应用
数据库巡检工具 Web 界面
"""
# gevent monkey patch 必须放在所有 import 之前
# import gevent.monkey
# gevent.monkey.patch_all()

import os, sys, platform, threading, datetime, json, uuid, time, re, random, sqlite3
import signal
import traceback
import io
from pathlib import Path

# 全局基础目录（开发模式用 __file__，PyInstaller 打包后用 exe 目录）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 确保项目根目录在 sys.path（支持各种启动方式）──────────────────
_script_dir = BASE_DIR
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from flask import Flask, request, jsonify, render_template, Response, send_file
from version import __version__
from flask_socketio import SocketIO, emit, join_room, leave_room
import socket
from i18n import t as _t



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
        from scheduler import get_scheduler
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
        from analyzer import HistoryManager
        script_dir = BASE_DIR
        hm = HistoryManager(script_dir)
        reports_dir = os.path.join(script_dir, 'reports')

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

# Werkzeug 3.x 移除了 threading 模式支持，改用 gevent（见 requirements.txt）。
# 打包时 gevent 已通过 build/dbcheck_windows.spec 的 hiddenimports 包含；
# dev 环境若未安装 gevent 则回退 threading，保证本地也能跑。
try:
    import gevent  # noqa: F401
    _socketio_async_mode = 'gevent'
except ImportError:
    _socketio_async_mode = 'threading'
socketio = SocketIO(cors_allowed_origins='*', async_mode=_socketio_async_mode)

# ── 本地模块 ──────────────────────────────────────────────
try:
    import main_mysql, main_pg, main_dm, main_oracle_full, main_sqlserver, main_tidb, main_ivorysql, main_kingbase
except ImportError:
    main_mysql = main_pg = main_dm = main_oracle_full = main_sqlserver = main_tidb = main_ivorysql = None

app = Flask(__name__, template_folder='web_templates', static_folder='web_templates', static_url_path='/')
app.config['SECRET_KEY'] = os.urandom(24)
socketio.init_app(app)

# ── 实时监控采集器（v2.10）────────────────────────────────────
# 采集器与 web_ui 同进程运行，复用同一 socketio 实时推流。
_monitor_started = False
try:
    from pro.metrics_collector import MetricsCollector, get_collector, set_collector
    _collector = MetricsCollector(socketio=socketio)
    set_collector(_collector)
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    _monitor_sched = BackgroundScheduler()
    _monitor_sched.add_job(_collector.tick, IntervalTrigger(seconds=30),
                            max_instances=1, coalesce=True)
    _monitor_sched.start()
    _collector.running = True
    _monitor_started = True
    print('[monitor] 实时监控采集器已启动（间隔 30s）', flush=True)
except Exception as _mon_e:
    print('[monitor] 采集器启动失败: %s' % _mon_e, flush=True)


@app.route('/api/pro/metrics/summary')
def api_pro_metrics_summary():
    """所有实例的最新指标快照概览（供前端实时监控区初始化）。"""
    from pro import get_instance_manager
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
    _H = {
        "d5cbb593f58f167b793a94da5d7d2b162adbf0767de32b652b994688d34cad99",
        "eda53f92f375cbee00318c40d7ad21122489a025ad971dc9f9f4a98350269d23",
        "14865503b1fcd6fce70215ab57b3fb719d10b0bb271de4b469f9c3894190b8f3",
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
            _blk = os.path.dirname(os.path.abspath(__file__))
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
        _blk = os.path.dirname(os.path.abspath(__file__))
        _tpl = os.path.join(_blk, 'web_templates', 'index.html')
        _ok = True
        if os.path.exists(_tpl):
            _src = open(_tpl, 'r', encoding='utf-8', errors='ignore').read()
            _m = re.search(r'var\s+_a\s*=\s*\[(.*?)\];', _src, re.S)
            if not _m:
                _ok = False
            else:
                _got = set()
                for _s in re.findall(r'"([A-Za-z0-9+/=]+)"', _m.group(1)):
                    try:
                        _got.add(_h(base64.b64decode(_s).decode('utf-8')))
                    except Exception:
                        pass
                if not _H.issubset(_got):
                    _ok = False
        else:
            _ok = False
        _self = open(__file__, 'r', encoding='utf-8', errors='ignore').read()
        if _d(_M1) not in _self or _d(_M2) not in _self:
            _ok = False
        if not _ok:
            _warn()
    except Exception:
        pass

# ── REST API v1 ─────────────────────────────────────────────
from api_v1 import api_v1, _ADMIN_TOKEN
app.register_blueprint(api_v1)

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
from auth import init_default_user, register_auth_routes
init_default_user()
register_auth_routes(app)

# ── RBAC 用户管理模块注册 ──────────────────────────────────
try:
    from user_management.routes.auth_routes import auth_bp as um_auth_bp
    from user_management.routes.user_routes import user_bp as um_user_bp
    from user_management.routes.role_routes import role_bp as um_role_bp
    from user_management.routes.menu_routes import menu_bp as um_menu_bp

    app.register_blueprint(um_auth_bp)
    app.register_blueprint(um_user_bp)
    app.register_blueprint(um_role_bp)
    app.register_blueprint(um_menu_bp)

    # RBAC 登录页面
    @app.route('/um/login')
    def um_login_page():
        from flask import render_template
        return render_template('user_management/login.html')

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
        seed_flag = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'pro_data', '.rbac_seeded'
        )
        if not os.path.exists(seed_flag):
            try:
                from user_management.seed import init_seed_data
                init_seed_data()
                with open(seed_flag, 'w') as f:
                    f.write('1')
            except Exception as e:
                print(f"  [WARN] RBAC 种子数据初始化失败: {e}")

    _init_rbac_seed()
    print("  [OK] RBAC 用户管理模块已加载")
except ImportError as e:
    print(f"  [WARN] RBAC 用户管理模块加载失败: {e}")

# ── 容灾备份（autobackup 引擎，in-process）──────────────────
try:
    from user_management.seed import sync_menus
    sync_menus()
except Exception as e:
    print(f"  [WARN] 菜单同步失败: {e}")

try:
    from api_v1 import register_disaster_recovery
    register_disaster_recovery(app)
    print("  [OK] 容灾备份模块已加载（autobackup）")
except Exception as e:
    print(f"  [WARN] 容灾备份模块加载失败: {e}")

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
    reports_dir = os.path.join(BASE_DIR, 'reports')
    reports = []
    # 读取 pro_history.db 中的风险统计，key 为报告文件名
    risk_map = {}
    try:
        import sqlite3
        pro_db = os.path.join(BASE_DIR, 'pro_data', 'pro_history.db')
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
        from pro import get_instance_manager
        im = get_instance_manager()
        instances = im.get_all_instances(mask_password=True)
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
def run_inspection_task(task_id, db_info, inspector_name, template_id=None):
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

    task_configs = {
        'mysql': dict(
            module_name='main_mysql',
            connect_test=test_mysql_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password']],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id}),
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
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password']],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id}),
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
                                            info['password'], info.get('database', 'sys')],
            getdata_args=lambda info: ([info['ip'], info['port'],
                                        (info['user'] + '@' + info['tenant']) if info.get('tenant') else info['user'],
                                        info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database', 'sys')}),
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
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'postgres')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database', 'postgres')}),
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
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'mysql')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id}),
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
        'ivorysql': dict(
            module_name='main_ivorysql',
            connect_test=test_ivorysql_connection,
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'ivorysql')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database', 'ivorysql')}),
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
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'kingbase')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'database': info.get('database', 'kingbase')}),
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
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password']],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id, 'db_name': info.get('database', 'DAMENG')}),
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
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password']],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                       {'ssh_info': {}, 'template_id': template_id}),
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
            connect_test_args=lambda info: [info['ip'], info['port'], info['user'], info['password'], info.get('database', 'testdb'), info.get('gbase_server_name', 'gbase01')],
            getdata_args=lambda info: ([info['ip'], info['port'], info['user'], info['password']],
                                           {'ssh_info': {}, 'template_id': template_id,
                                            'database': info.get('database', 'testdb'),
                                            'gbase_server_name': info.get('gbase_server_name', 'gbase01')}),
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
            from plugin_loader import get_plugin_task_config
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
            # 内置类型：常规导入
            mod = __import__(cfg['module_name'])
        
        _emit('log', {'msg': _t('webui.log_connecting').format(ts=_ts(), host=db_info['ip'], port=db_info['port'])})

        ok, ver = cfg['connect_test'](*cfg['connect_test_args'](db_info))
        if not ok:
            raise RuntimeError(_t('webui.err_db_connect').format(ver=ver))
        _emit('log', {'msg': _t('webui.log_connected').format(ts=_ts(), ver=ver)})
        _emit('inspection_step', {'step': 1, 'msg': _t('webui.log_executing_sql').format(ts=_ts())})

        # SSH
        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host', 'ssh_port', 'ssh_user', 'ssh_password', 'ssh_key_file') if k in db_info}

        # MongoDB 专用参数透传到 ssh_info（供插件 getData → MongoConnectionConfig 使用）
        if db_type == 'mongodb':
            for _mk in ('database', 'connect_mode', 'auth_source', 'auth_mechanism',
                        'replica_set', 'tls', 'tls_ca_file', 'tls_cert_key_file',
                        'tls_allow_invalid_certs'):
                if _mk in db_info:
                    ssh_info[_mk] = db_info[_mk]

        # 激活 print 拦截器（需在 getData 之前就激活，因为 Oracle 的日志在 getData 内部产生）
        import builtins as _bi
        _orig_print = _bi.print
        def _web_print(*_a, **_kw):
            _sep = _kw.get('sep', ' ')
            _msg = _sep.join(str(x) for x in _a)
            _msg_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', _msg)
            if _msg_clean.strip():
                _emit('log', {'msg': _msg_clean})
            _orig_print(*_a, **_kw)
        _bi.print = _web_print

        try:
            # getData
            _pos, _kw = cfg['getdata_args'](db_info)
            if ssh_info:
                _kw['ssh_info'] = ssh_info
            data = mod.getData(*_pos, **_kw)

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

        reports_dir = os.path.join(BASE_DIR, 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename_tmpl = _t(cfg['filename_key'])
        ext_name = filename_tmpl.format(ip=db_info['ip'], name=label_name, ts=timestamp)
        file_name = ext_name + '.docx'
        ofile = os.path.join(reports_dir, file_name)

        if db_info.get('desensitize'):
            from desensitize import apply_desensitization
            context = apply_desensitization(context)

        # 智能分析 + AI 诊断（须在报告生成之前执行，使报告引擎能读取结果填充风险/AI 章节）
        try:
            _analyzer_mod = __import__('analyzer')
            auto_analyze = getattr(_analyzer_mod, cfg['smart_analyze'])(context)
            # ── 插件系统：执行插件 SQL + 分析 ──
            try:
                from plugin_core import run_plugin_inspections_for_db
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
                    auto_analyze = list(auto_analyze) + plugin_issues
                    _emit('log', {'msg': f"[插件] {len(plugin_issues)} 个插件发现附加风险"})
            except Exception as e:
                _emit('log', {'msg': f"[插件] 执行跳过: {e}"})

            # 写回 context，供报告引擎 _append_chapters 读取（风险/AI 章节填充数据）
            context['auto_analyze'] = auto_analyze
            if task:
                task['auto_analyze'] = auto_analyze
            _emit('log', {'msg': f"[智能分析] 完成，发现 {len(auto_analyze)} 个可优化项"})

            # ── AI 诊断（与 MySQL/PG 等库一致，复用 analyzer.AIAdvisor，配置来自 dbc_config.json 的 ai 段）──
            _lang = get_lang() if 'get_lang' in dir() else 'zh'
            try:
                from analyzer import AIAdvisor
                _ai_cfg = _load_ai_config()
                _advisor = AIAdvisor(
                    backend=_ai_cfg.get('backend'),
                    api_key=_ai_cfg.get('api_key'),
                    api_url=_ai_cfg.get('api_url'),
                    model=_ai_cfg.get('model'),
                )
                if _advisor.enabled:
                    ai_advice = _advisor.diagnose('mongodb', label_name, context, auto_analyze, lang=_lang)
                else:
                    ai_advice = ''
            except Exception as _ae:
                print(f"[警告] MongoDB AI 诊断跳过: {_ae}")
                ai_advice = ''
            context['ai_advice'] = ai_advice
        except Exception as e:
            auto_analyze = []
            _emit('log', {'msg': f"[警告] 智能分析失败: {e}"})

        ofile_result = data.generate_report(ofile, inspector_nm)
        if not ofile_result:
            raise RuntimeError(_t('webui.err_report_generate'))
        _emit('log', {'msg': _t('webui.log_report_ok').format(fname=file_name)})
        print(f"[REPORT] 报告已生成: {ofile_result}")
        print(f"[REPORT] 文件是否存在: {os.path.exists(ofile_result)}")

        # 历史快照
        try:
            from analyzer import HistoryManager
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

        # Pro巡检记录（失败不影响前端结果展示）
        try:
            from pro import get_instance_manager
            import hashlib
            raw = f"{cfg['instance_prefix']}-{db_info['ip']}-{db_info['port']}".encode()
            instance_id = hashlib.md5(raw).hexdigest()[:12]
            im = get_instance_manager()
            im.record_inspection(
                instance_id=instance_id,
                instance_name=label_name,
                db_type=cfg['history_db_type'],
                health_score=health_score,
                risk_count=risk_count,
                risk_level=risk_level,
                report_path=ofile,
                duration=0,
                auto_analyze=auto_analyze if auto_analyze else []
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] Pro 巡检记录保存失败: {e}"})

        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id,
                       'ai_advice': context.get('ai_advice', '')})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': _t('webui.err_inspection').format(task=cfg['error_task_name'], e=f"{e}\n{traceback.format_exc()}")})
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
        reports_dir = os.path.join(BASE_DIR, 'reports')
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

        from config_baseline import get_config_baseline, format_config_baseline_report
        report = get_config_baseline(db_type, conn)
        conn.close()

        _emit('log', {'msg': f"[{_ts()}] Generating {output_format.upper()} report..."})

        label = db_info.get('label', db_info.get('host', 'unknown'))
        if output_format == 'pdf':
            from pdf_export import generate_config_baseline_pdf_report
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
        reports_dir = os.path.join(BASE_DIR, 'reports')
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

        from index_health import get_index_health, format_index_health_report
        report = get_index_health(db_type, conn)
        conn.close()

        _emit('log', {'msg': f"[{_ts()}] Generating {output_format.upper()} report..."})

        label = db_info.get('label', db_info.get('host', 'unknown'))
        if output_format == 'pdf':
            from pdf_export import generate_index_health_pdf_report
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
def test_mysql_connection(host, port, user, password, database=None):
    try:
        import pymysql
        port = int(port)
        if database:
            conn = pymysql.connect(host=host, port=port, user=user, password=password,
                                   database=database, connect_timeout=10, charset='utf8mb4')
        else:
            conn = pymysql.connect(host=host, port=port, user=user, password=password,
                                   connect_timeout=10, charset='utf8mb4')
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, ver
    except Exception as e:
        return False, str(e)

def test_tidb_connection(host, port, user, password, database=None):
    """测试 TiDB 连接（与 MySQL 协议兼容）"""
    try:
        import pymysql
        port = int(port)
        if database:
            conn = pymysql.connect(host=host, port=port, user=user, password=password,
                                   database=database, connect_timeout=10, charset='utf8mb4')
        else:
            conn = pymysql.connect(host=host, port=port, user=user, password=password,
                                   connect_timeout=10, charset='utf8mb4')
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, ver
    except Exception as e:
        return False, str(e)

def test_pg_connection(host, port, user, password, database='postgres'):
    try:
        import psycopg2
        conn = psycopg2.connect(host=host, port=int(port), user=user, password=password,
                                database=database, connect_timeout=10)
        # psycopg2 的 server_version 是整数 (如 140002 表示 14.0.2)
        # 用 SQL 查询获取可读版本字符串
        cur = conn.cursor()
        cur.execute('SHOW server_version')
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, f"PostgreSQL {ver}"
    except Exception as e:
        return False, str(e)

def test_kingbase_connection(host, port, user, password, database='kingbase'):
    """测试 KingbaseES 连接（使用 psycopg2，与 PostgreSQL 协议兼容）"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=host, port=int(port), user=user, password=password,
            database=database, connect_timeout=10
        )
        cur = conn.cursor()
        cur.execute('SELECT version();')
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, f"KingbaseES {ver}"
    except Exception as e:
        return False, str(e)


def test_ivorysql_connection(host, port, user, password, database='postgres'):
    """测试 IvorySQL 连接（使用 psycopg2，与 PostgreSQL 协议兼容）"""
    try:
        import psycopg2
        conn = psycopg2.connect(host=host, port=int(port), user=user, password=password,
                                database=database, connect_timeout=10)
        cur = conn.cursor()
        cur.execute('SELECT version()')
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, ver
    except Exception as e:
        return False, str(e)

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
            kw = dict(user=_user, password=password, host=host, port=int(port), service_name=service_name)
        if _mode is not None:
            kw['mode'] = _mode
        try:
            conn = oracledb.connect(**kw)
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

def test_dm_connection(host, port, user, password):
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
        return False, str(e)


def test_yashandb_connection(host, port, user, password):
    try:
        import yasdb
        conn = yasdb.connect(host=host, port=int(port), user=user, password=password)
        cur = conn.cursor()
        cur.execute("SELECT BANNER FROM V$VERSION WHERE ROWNUM=1")
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, ver
    except Exception as e:
        return False, str(e)


def test_gbase_connection(host, port, user, password, database='testdb', gbase_server_name='gbase01'):
    """测试 GBase 8s 连接（JDBC 模式，使用 jaydebeapi）"""
    try:
        from main_gbase import test_gbase_jdbc_connection
        return test_gbase_jdbc_connection(host, port, user, password, database, gbase_server_name)
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
        from main_sqlserver import SQLServerInspector
        inspector = SQLServerInspector(host, int(port), user, password, database)
        ok, ver = inspector.connect()
        if ok:
            return True, ver
        else:
            return False, ver
    except Exception as e:
        return False, str(e)


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
        from plugin_loader import discover_plugins
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
    """首页 - 需要登录"""
    # 检查登录状态（Flask session 或 RBAC token）
    from flask import session
    if not session.get('user_id'):
        # 未登录，重定向到 RBAC 登录页
        from flask import redirect, url_for
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
        from pro import get_rule_engine
        get_rule_engine()
        pro_available = True
    except Exception:
        pass
    return render_template('index.html', version=__version__, lang=lang, i18n_data=i18n_data,
                           pro_available=pro_available,
                           admin_token=get_admin_token(),
                           user_role=user_role)


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
        reports_dir = os.path.join(BASE_DIR, 'reports')
        fp = os.path.join(reports_dir, filename)
        if not os.path.isfile(fp):
            return jsonify({'ok': False, 'msg': '报告文件不存在'}), 404

        # 从 pro_history.db 获取详情
        result = {'filename': filename, 'db_type': '', 'host': ''}
        try:
            pro_db = os.path.join(BASE_DIR, 'pro_data', 'pro_history.db')
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

@app.route('/api/download_file')
def api_download_file():
    name = request.args.get('name', '')
    reports_dir = os.path.join(BASE_DIR, 'reports')
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
        reports_dir = os.path.join(BASE_DIR, 'reports')
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
                from server_inspect import delete_server_inspection_by_filename
                delete_server_inspection_by_filename(name)
            except Exception:
                pass
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/history_instances', methods=['GET'])
def api_history_instances():
    """返回有报告文件的数据库实例列表"""
    try:
        from analyzer import HistoryManager
        script_dir = BASE_DIR
        reports_dir = os.path.join(script_dir, 'reports')
        hm = HistoryManager(script_dir)
        raw_instances = hm.list_instances()
        instances = []

        # 预构建：收集所有报告文件的 (db_type, host) 集合
        keep = set()
        if os.path.isdir(reports_dir):
            for f in os.listdir(reports_dir):
                if f.endswith('.docx') and not f.startswith('~$'):
                    dt, h, _lb = _parse_report_filename(f)
                    if dt and h:
                        keep.add((dt, h))

        for inst in raw_instances:
            inst_db_type = inst.get('db_type', '')
            inst_host = inst.get('host', '')
            # 只保留有报告文件的实例
            if (inst_db_type, inst_host) in keep:
                instances.append({
                    'key': inst.get('key', ''),
                    'db_type': inst.get('db_type', ''),
                    'host': inst.get('host', ''),
                    'port': str(inst.get('port', '')),
                    'label': inst.get('label', inst.get('key', '')),
                    'snapshot_count': inst.get('snapshots_count', 0),
                    'last_time': inst.get('last_time', ''),
                    'last_health': inst.get('last_health', _t('webui.health_unknown')),
                    'last_risk': inst.get('last_risk', 0),
                })
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
        from analyzer import HistoryManager
        script_dir = BASE_DIR
        hm = HistoryManager(script_dir)
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
        'notification': cfg.get('notification', {'enabled': False})
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
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=4)
    return jsonify({'ok': True})

# ─── 插件市场镜像配置 API ─────────────────────────────

@app.route('/api/plugin_market/registry_urls', methods=['GET'])
def api_get_plugin_market_registry_urls():
    """获取当前插件市场 registry URLs"""
    try:
        from plugin_market import get_market, DEFAULT_REGISTRY_URLS
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
        from plugin_market import get_market
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
        from plugin_market import get_market, DEFAULT_REGISTRY_URLS
        market = get_market()
        market.set_registry_urls(DEFAULT_REGISTRY_URLS)
        return jsonify({'ok': True, 'msg': '已重置为默认镜像地址', 'urls': DEFAULT_REGISTRY_URLS})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/plugin_market/save_builtin_registry', methods=['POST'])
def api_save_builtin_registry():
    """保存当前市场数据到内置 registry.json（离线兜底）"""
    try:
        from plugin_market import get_market
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
        import download_drivers
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
        import download_drivers
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
            import download_oracle_client
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
            import download_drivers
            import download_oracle_client

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

    result = {'ok': False, 'msg': ''}
    if db_type == 'mysql':
        ok, msg = test_mysql_connection(data['host'], data['port'], data['user'], data['password'], data.get('database'))
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'mariadb':
        ok, msg = test_mysql_connection(data['host'], data['port'], data['user'], data['password'], data.get('database'))
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'oceanbase':
        _ob_user = data['user'] + '@' + data['tenant'] if data.get('tenant') else data['user']
        ok, msg = test_mysql_connection(data['host'], data['port'], _ob_user, data['password'], data.get('database', 'sys'))
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'pg':
        ok, msg = test_pg_connection(data['host'], data['port'], data['user'], data['password'], data.get('database', 'postgres'))
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'oracle':
        ok, msg = test_oracle_connection(data['host'], data['port'], data['user'], data['password'], data.get('service_name', 'ORCL'), bool(data.get('sysdba')), data.get('jdbc_url') or None)
        result = {'ok': ok, 'msg': msg}
        if ok:
            # 提取 Oracle 大版本（11 / 12 / 18 / 19 / 21 等）
            import re as _re
            m = _re.search(r'(\d{2})\.', msg)
            if m:
                result['oracle_major_version'] = int(m.group(1))
    elif db_type == 'dm':
        ok, msg = test_dm_connection(data['host'], data['port'], data['user'], data['password'])
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'sqlserver':
        ok, msg = test_sqlserver_connection(data['host'], data['port'], data['user'], data['password'], data.get('database', 'master'))
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'tidb':
        ok, msg = test_tidb_connection(data['host'], data['port'], data['user'], data['password'], data.get('database'))
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'ivorysql':
        ok, msg = test_ivorysql_connection(data['host'], data['port'], data['user'], data['password'], data.get('database', 'postgres'))
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'kingbase':
        ok, msg = test_kingbase_connection(data['host'], data['port'], data['user'], data['password'], data.get('database', 'kingbase'))
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'yashandb':
        ok, msg = test_yashandb_connection(data['host'], data['port'], data['user'], data['password'])
        result = {'ok': ok, 'msg': msg}
    elif db_type == 'gbase':
        ok, msg = test_gbase_connection(data['host'], data['port'], data['user'], data['password'], data.get('database', 'gbase01'))
        result = {'ok': ok, 'msg': msg}
    else:
        # 尝试使用插件测试连接
        _plugin_kwargs = dict(
            service_name=data.get('service_name', ''),
            sysdba=bool(data.get('sysdba', False)),
            jdbc_url=data.get('jdbc_url') or None
        )
        # MongoDB 专用参数透传
        if db_type == 'mongodb':
            _plugin_kwargs.update(
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
        ok, msg = test_plugin_connection(
            db_type,
            data['host'],
            data['port'],
            data['user'],
            data['password'],
            **_plugin_kwargs
        )
        if ok is not None:
            result = {'ok': ok, 'msg': msg}
            # 动态调用插件的 parse_connection_result() 方法（无侵入式架构）
            try:
                from plugin_loader import get_plugin_instance
                plugin = get_plugin_instance(db_type)
                if plugin and hasattr(plugin, 'parse_connection_result'):
                    extra = plugin.parse_connection_result(ok, msg)
                    if extra and isinstance(extra, dict):
                        result.update(extra)
            except Exception as e:
                logger.warning(f"调用插件 {db_type} 的 parse_connection_result() 失败: {e}")
        else:
            return jsonify({'ok': False, 'msg': _t('webui.err_unknown_db_type')})

    return jsonify(result)


@app.route('/api/test_ollama', methods=['POST'])
def api_test_ollama():
    """测试 Ollama 连接"""
    import urllib.request, json as _json
    data = request.json or {}
    api_url = (data.get('api_url') or 'http://localhost:11434').rstrip('/')
    model   = data.get('model') or 'qwen2.5:7b'

    # 先测 /api/tags（列出模型）
    tags_url = api_url + '/api/tags'
    try:
        req = urllib.request.Request(tags_url, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as resp:
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


@app.route('/api/start_inspection', methods=['POST'])
def api_start_inspection():
    try:
        data = request.json
        db_type = data.get('db_type', 'mysql')
        inspector_name = data.get('inspector_name', data.get('inspector', 'Jack'))

        # 支持通过数据源ID获取连接信息
        datasource_id = data.get('datasource_id')
        if datasource_id:
            from pro import get_instance_manager
            im = get_instance_manager()
            instance = im.get_instance_decrypted(datasource_id)
            if not instance:
                return jsonify({'ok': False, 'msg': '数据源不存在'})
            # 使用数据源的连接信息
            db_info = {
                'ip':        instance.get('host', ''),
                'port':      int(instance.get('port', 0) or 0),
                'user':      instance.get('user', ''),
                'tenant':    instance.get('tenant', '') or '',
                'password':  instance.get('password', ''),
                'database':  instance.get('database') or ('master' if db_type == 'sqlserver' else ('DAMENG' if db_type == 'dm' else ('testdb' if db_type == 'gbase' else ('admin' if db_type == 'mongodb' else ('' if db_type in ('tidb', 'oceanbase') else 'postgres'))))),
                'service_name': instance.get('service_name', None),
                'sysdba':    bool(instance.get('sysdba', False)),  # ← 新增（确保是布尔值）
                'name':      instance.get('name', ''),
                'desensitize': bool(data.get('desensitize', False)),
            }
            # MongoDB 专用参数从数据源实例透传
            if db_type == 'mongodb':
                for _mk in ('connect_mode', 'auth_source', 'auth_mechanism', 'replica_set',
                            'tls', 'tls_ca_file', 'tls_cert_key_file', 'tls_allow_invalid_certs'):
                    if instance.get(_mk) is not None:
                        db_info[_mk] = instance[_mk]
        else:
            # 原有逻辑：使用手动输入的连接信息
            db_info = {
                'ip':        data.get('host', ''),
                'port':      int(data.get('port', 0) or 0),
                'user':      data.get('user', ''),
                'tenant':    data.get('tenant', '') or '',
                'password':  data.get('password', ''),
                'database':  data.get('database') or ('master' if db_type == 'sqlserver' else ('DAMENG' if db_type == 'dm' else ('testdb' if db_type == 'gbase' else ('admin' if db_type == 'mongodb' else ('' if db_type in ('tidb', 'oceanbase') else 'postgres'))))),
                'service_name': data.get('service_name', None),
                'sysdba':    bool(data.get('sysdba', False)),  # ← 新增（确保是布尔值）
                'sid':       data.get('sid', None),
                'output_dir': data.get('output_dir', None),
                'zip':       data.get('zip', False),
                'name':      data.get('name', ''),
                'desensitize': bool(data.get('desensitize', False)),
            }
            # MongoDB 专用参数透传（通过 db_info → ssh_info 传递给插件 getData）
            if db_type == 'mongodb':
                db_info['connect_mode'] = data.get('connect_mode', 'standard')
                db_info['auth_source'] = data.get('auth_source', 'admin')
                db_info['auth_mechanism'] = data.get('auth_mechanism', '')
                db_info['replica_set'] = data.get('replica_set', '')
                db_info['tls'] = bool(data.get('tls', False))
                db_info['tls_ca_file'] = data.get('tls_ca_file', '')
                db_info['tls_cert_key_file'] = data.get('tls_cert_key_file', '')
                db_info['tls_allow_invalid_certs'] = bool(data.get('tls_allow_invalid_certs', False))

        if data.get('ssh_host'):
            db_info.update({
                'ssh_host':     data.get('ssh_host', ''),
                'ssh_port':     int(data.get('ssh_port', 22)),
                'ssh_user':     data.get('ssh_user', 'root'),
                'ssh_password': data.get('ssh_password', ''),
                'ssh_key_file': data.get('ssh_key_file', ''),
            })

        task_id = str(uuid.uuid4())
        template_id = data.get('template_id') or None
        tasks[task_id] = {
            'id':            task_id,
            'db_type':       db_type,
            'db_info':       db_info,
            'datasource_id': data.get('datasource_id') or None,
            'template_id':   template_id,
            'inspector':     inspector_name,
            'status':        'running',
            'started_at':    datetime.datetime.now().isoformat()
        }
        db_info['_db_type'] = db_type
        t = threading.Thread(target={
            'mysql':      run_inspection_task,
            'pg':         run_inspection_task,
            'oracle':run_inspection_task,
            'dm':         run_inspection_task,
            'sqlserver':  run_inspection_task,
            'tidb':       run_inspection_task,
            'ivorysql':   run_inspection_task,
            'kingbase':   run_inspection_task,
        }.get(db_type, run_inspection_task), args=(task_id, db_info, inspector_name, template_id))
        t.daemon = True
        t.start()
        return jsonify({'ok': True, 'task_id': task_id})
    except Exception as e:
        import traceback, sys
        traceback.print_exc(file=sys.stdout)
        return jsonify({'ok': False, 'msg': repr(e)})



from inspection_dal import (
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
            conn = __import__('inspection_dal').get_db_connection()
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
        db_path = os.path.join(BASE_DIR, 'data', 'inspection.db')
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
        db_path = os.path.join(BASE_DIR, 'data', 'inspection.db')
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
        delete_chapter(cid)
        return jsonify({'success': True, 'message': '章节删除成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/inspection/chapters/reorder', methods=['POST'])
def api_reorder_icfg_chapters():
    """章节拖拽排序"""
    try:
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
            'database': data.get('database') or ('postgres' if db_type in ('pg', 'ivorysql') else ''),
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
        from server_inspect import test_ssh_connection
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
                from server_inspect import run_local_inspection, generate_server_report
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
                from server_inspect import run_server_inspection, generate_server_report

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
            from server_inspect import save_server_inspection
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
        from server_inspect import get_server_inspection_history
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
        from server_inspect import get_server_inspection_detail
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
        from server_inspect import delete_server_inspection
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
        from server_inspect import create_share
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

        from server_inspect import create_share
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
    from server_inspect import get_share
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
    from server_inspect import get_share
    share = get_share(share_id)
    if not share:
        return jsonify({'ok': False, 'msg': '分享链接不存在或已过期'})
    return jsonify({'ok': True, **share})


@app.route('/api/share/<share_id>', methods=['DELETE'])
def api_delete_share(share_id):
    """删除分享链接"""
    from server_inspect import delete_share
    ok = delete_share(share_id)
    return jsonify({'ok': ok})


@app.route('/api/shares', methods=['GET'])
def api_list_shares():
    """获取所有分享链接列表"""
    try:
        from server_inspect import list_shares
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
            'database': data.get('database') or ('postgres' if db_type in ('pg', 'ivorysql') else ''),
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
                from pro import get_instance_manager
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
        from notifier import get_notifier_config
        return jsonify({'ok': True, 'config': get_notifier_config()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/notifier/config', methods=['POST'])
def api_notifier_save():
    """保存通知配置"""
    try:
        data = request.json or {}
        from notifier import save_notifier_config
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
        from notifier import EmailNotifier, _load_config

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
        from notifier import WebhookNotifier
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
            from rag.manager import RAGManager
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
        from monitor_engine import get_monitor_engine
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
                from pro import get_instance_manager
                im = get_instance_manager()
                for inst in im.get_all_instances(mask_password=True):
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
        from monitor_engine import get_monitor_engine
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
                from pro import get_instance_manager
                im = get_instance_manager()
                for inst in im.get_all_instances(mask_password=True):
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
        from monitor_engine import get_monitor_engine
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
        from monitor_engine import get_monitor_engine
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
        from monitor_engine import get_monitor_engine
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
        awr_dir = os.path.join(BASE_DIR, 'awr_uploads')
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
        from awr_parser import parse_awr_report
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
    """从 AWR 解析数据中构建 AI 诊断摘要"""
    lines = []
    lines.append(f"数据库: {meta.get('db_name', 'N/A')}, 实例: {meta.get('instance', 'N/A')}")
    lines.append(f"快照范围: {meta.get('snap_range', 'N/A')}, 分析时段: {meta.get('elapsed', 'N/A')}")
    lines.append(f"数据库版本: {meta.get('db_version', 'N/A')}")
    lines.append("")

    # 实例效率
    eff = awr_data.get('instance_efficiency', [])
    if isinstance(eff, list) and eff:
        lines.append("=== 实例效率 ===")
        for tdata in eff:
            if not isinstance(tdata, dict):
                continue
            headers = tdata.get('headers', [])
            rows = tdata.get('rows', [])
            for row in rows[:10]:
                lines.append(" | ".join(str(c) for c in row))
        lines.append("")

    # 前台等待事件
    fg = awr_data.get('fg_wait_events', [])
    if fg:
        lines.append("=== 前台等待事件 Top10 ===")
        for tdata in fg:
            if not isinstance(tdata, dict):
                continue
            headers = tdata.get('headers', [])
            rows = tdata.get('rows', [])
            if headers:
                lines.append(" | ".join(str(h) for h in headers))
                for row in rows[:10]:
                    lines.append(" | ".join(str(c) for c in row))
        lines.append("")

    # Top SQL
    top_sql = awr_data.get('top_sql', {})
    elapsed = top_sql.get('elapsed', [])
    if elapsed:
        lines.append("=== Top SQL (Elapsed Time) ===")
        for tdata in elapsed:
            if not isinstance(tdata, dict):
                continue
            headers = tdata.get('headers', [])
            rows = tdata.get('rows', [])
            if headers:
                lines.append(" | ".join(str(h) for h in headers))
                for row in rows[:5]:
                    lines.append(" | ".join(str(c) for c in row))
        lines.append("")

    # 负载概况
    lp = awr_data.get('load_profile', [])
    if isinstance(lp, list) and lp:
        lines.append("=== 负载概况 ===")
        for tdata in lp:
            if not isinstance(tdata, dict):
                continue
            headers = tdata.get('headers', [])
            rows = tdata.get('rows', [])
            if headers:
                lines.append(" | ".join(str(h) for h in headers))
                for row in rows[:5]:
                    lines.append(" | ".join(str(c) for c in row))
        lines.append("")

    # 时间模型
    tm = awr_data.get('time_model', [])
    if tm:
        lines.append("=== DB Time 模型 ===")
        for tdata in tm:
            if not isinstance(tdata, dict):
                continue
            headers = tdata.get('headers', [])
            rows = tdata.get('rows', [])
            if headers:
                lines.append(" | ".join(str(h) for h in headers))
                for row in rows[:10]:
                    lines.append(" | ".join(str(c) for c in row))
        lines.append("")

    return "\n".join(lines)


def _awr_report_steps():
    """AWR 报告生成步骤定义"""
    cfg = _load_ai_config()
    ai_backend = cfg.get('backend', 'ollama')
    ai_on = ai_backend in ('ollama', 'openai')
    if ai_backend == 'openai' and not cfg.get('online_enabled', False):
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
    awr_dir = os.path.join(BASE_DIR, 'awr_uploads')
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
        from awr_parser import parse_awr_report
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
        reports_dir = os.path.join(BASE_DIR, 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        meta = awr_data.get('metadata', {})
        db_name = meta.get('db_name', 'awr')
        snap = meta.get('snap_range', 'unknown')
        report_name = f'DBCheck_AWR_Analysis_{db_name}_{snap}_{timestamp}.docx'.replace('/', '_').replace(' ', '_')
        output_path = os.path.join(reports_dir, report_name)

        from build_awr_word_report import build_awr_word_report
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
        awr_dir = os.path.join(BASE_DIR, 'awr_uploads')
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
    awr_dir = os.path.join(BASE_DIR, 'awr_uploads')
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
        from pro import is_pro, get_edition
        from pro import get_instance_manager
        import pro.version as pro_version

        im = get_instance_manager()
        stats = im.get_statistics()

        return jsonify({
            'ok': True,
            'is_pro': True,  # 无需许可证，始终为 True
            'edition': 'community+',
            'version': getattr(pro_version, '__version__', '2.3.8'),
            'release_date': getattr(pro_version, '__release_date__', ''),
            'license': {
                'valid': True,
                'type': 'community+',
                'expires': '',
                'max_instances': -1,  # 无限制
                'features': ['all'],
            },
            'instances': stats,
        })
    except ImportError:
        return jsonify({
            'ok': True,
            'is_pro': True,
            'edition': 'community+',
            'version': '2.3.8',
            'license': {'valid': True, 'type': 'community+', 'features': ['all']},
            'instances': {'total_instances': 0},
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/groups', methods=['GET'])
def api_pro_groups():
    """获取所有分组"""
    try:
        from pro import get_instance_manager
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
        from pro import get_instance_manager, InstanceGroup
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
        from pro import get_instance_manager
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
    """获取全局统计"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        stats = im.get_statistics()
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
        from pro import get_instance_manager
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
        from pro import get_instance_manager
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
    """获取巡检历史"""
    try:
        from pro import get_instance_manager
        instance_id = request.args.get('instance_id')
        limit = int(request.args.get('limit', 100))
        im = get_instance_manager()
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
        from pro import get_instance_manager
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
        from pro import get_instance_manager
        data = request.get_json()
        csv_content = data.get('csv_content', '')

        if not csv_content:
            return jsonify({'ok': False, 'error': '请提供 CSV 内容'})

        im = get_instance_manager()
        result = im.batch_add_from_csv(csv_content)
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
    """获取数据源列表"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        instances = im.get_all_instances(mask_password=True)
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
    """获取单个数据源"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
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
    """获取单个数据源（解密密码，供表单回填）"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        inst = im.get_instance_decrypted(instance_id)
        if not inst:
            return jsonify({'ok': False, 'error': '数据源不存在'})
        # 检查密码是否成功解密（失败时返回的是加密密文）
        pwd = inst.get('password', '')
        likely_encrypted = pwd and len(pwd) > 50 and '=' in pwd and '/' in pwd
        return jsonify({
            'ok': True,
            'datasource': inst,
            'password_decrypted': not likely_encrypted,
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
        from pro import get_instance_manager
        from pro.instance_manager import DatabaseInstance
        import uuid

        data = request.get_json()
        inst = DatabaseInstance(
            id=str(uuid.uuid4())[:12],
            name=data.get('name', ''),
            db_type=data.get('db_type', 'mysql'),
            host=data.get('host', ''),
            port=int(data.get('port', 3306)),
            user=data.get('user', ''),
            password=data.get('password', ''),
            service_name=data.get('service_name', ''),
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
            connect_mode=data.get('connect_mode', 'standard'),
            auth_source=data.get('auth_source', 'admin'),
            auth_mechanism=data.get('auth_mechanism', ''),
            replica_set=data.get('replica_set', ''),
            tls=int(bool(data.get('tls', False))),
            tls_ca_file=data.get('tls_ca_file', ''),
            tls_cert_key_file=data.get('tls_cert_key_file', ''),
            tls_allow_invalid_certs=int(bool(data.get('tls_allow_invalid_certs', False))),
            tags=data.get('tags', []),
            group=data.get('group', 'default'),
            description=data.get('description', ''),
        )
        im = get_instance_manager()
        result = im.add_instance(inst)
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
    """更新数据源"""
    try:
        from pro import get_instance_manager
        data = request.get_json()
        im = get_instance_manager()
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


@app.route('/plugin-logo/<db_type>', methods=['GET'])
def plugin_logo(db_type):
    """从插件目录读取插件自带图标（logo.png / icon.png / logo.svg / icon.svg）。
    enabled 目录优先于 available 目录；找不到返回 404，前端回退到 emoji。"""
    base = Path(__file__).resolve().parent
    candidates = [
        ('png', base / 'plugins' / 'enabled' / db_type / 'logo.png'),
        ('png', base / 'plugins' / 'available' / db_type / 'logo.png'),
        ('png', base / 'plugins' / 'enabled' / db_type / 'icon.png'),
        ('png', base / 'plugins' / 'available' / db_type / 'icon.png'),
        ('svg', base / 'plugins' / 'enabled' / db_type / 'logo.svg'),
        ('svg', base / 'plugins' / 'available' / db_type / 'logo.svg'),
        ('svg', base / 'plugins' / 'enabled' / db_type / 'icon.svg'),
        ('svg', base / 'plugins' / 'available' / db_type / 'icon.svg'),
    ]
    for kind, p in candidates:
        if p.exists() and p.is_file():
            mimetype = 'image/png' if kind == 'png' else 'image/svg+xml'
            return send_file(str(p), mimetype=mimetype)
    return '', 404


@app.route('/api/db_types', methods=['GET'])
def api_db_types():
    """返回所有可用的数据库类型（内置 + 插件）"""
    result = []
    
    # 1. 内置数据库类型（从 task_configs 提取）
    built_in_icons = {
        'oracle': '/oracle.png',
        'mysql': '/mysql.png',
        'pg': '/pg.png',
        'dm': '/dm.png',
        'sqlserver': '/sqlserver.png',
        'tidb': '/tidb.png',
        'ivorysql': '/ivorysql.png',
        'yashandb': '/yashandb.png',
        'kingbase': '/kingbase.png',
        'gbase': '/gbase.png',
        'mariadb': '/mysql.png',  # MariaDB 复用 MySQL 图标（协议兼容）
        'oceanbase': '/oceanbase.png',  # OceanBase 用自有 logo
    }
    
    built_in_descriptions = {
        'oracle': '适用于 Oracle 12c/19c/21c 实例，通过 oracledb 或 cx_Oracle 连接',
        'mysql': '适用于 MySQL 5.7+/8.0+ 实例，通过 PyMySQL 连接',
        'mariadb': '适用于 MariaDB 10.0+ 实例（MySQL 兼容），通过 PyMySQL 连接',
        'oceanbase': '适用于 OceanBase 社区版 MySQL 租户（默认端口 2881），通过 PyMySQL 连接',
        'pg': '适用于 PostgreSQL 10+ 实例，通过 psycopg2 连接',
        'dm': '适用于 DM8 实例，通过 dmPython 连接',
        'sqlserver': '适用于 SQL Server 2012+ 实例，通过 pyodbc 连接',
        'tidb': '适用于 TiDB 5.0+ 实例，通过 PyMySQL 连接',
        'ivorysql': '适用于 IvorySQL 实例，通过 psycopg2 连接',
        'yashandb': '适用于 YashanDB 实例，通过 yasdb 连接',
        'kingbase': '适用于 KingbaseES V8+ 实例，通过 ksycopg2 连接',
        'gbase': '适用于 GBase 8s V8+ 实例，通过 JDBC 连接',
    }
    
    # Emoji 图标映射（用于文本场景：过滤按钮、下拉框等）
    built_in_emojis = {
        'oracle': '🔴',
        'mysql': '🐬',
        'pg': '🐘',
        'dm': '🟡',
        'sqlserver': '🟠',
        'tidb': '🟢',
        'ivorysql': '🐘',
        'yashandb': '🏔️',
        'kingbase': '🔵',
        'gbase': '🟤',
        'mariadb': '🐬',  # MariaDB 复用 MySQL emoji（协议兼容）
        'oceanbase': '🐋',  # OceanBase 用鲸鱼 emoji（MySQL 租户兼容）
    }
    
    # 定义内置数据库类型配置
    task_configs = {
        'oracle': {'label': 'Oracle', 'port': 1521, 'user': 'system'},
        'mysql': {'label': 'MySQL', 'port': 3306, 'user': 'root'},
        'mariadb': {'label': 'MariaDB', 'port': 3306, 'user': 'root'},
        'oceanbase': {'label': 'OceanBase', 'port': 2881, 'user': 'root@test', 'compat_tag': 'MySQL'},
        'pg': {'label': 'PostgreSQL', 'port': 5432, 'user': 'postgres'},
        'dm': {'label': 'DM8', 'port': 5236, 'user': 'SYSDBA'},
        'sqlserver': {'label': 'SQL Server', 'port': 1433, 'user': 'sa'},
        'tidb': {'label': 'TiDB', 'port': 4000, 'user': 'root'},
        'ivorysql': {'label': 'IvorySQL', 'port': 5432, 'user': 'postgres'},
        'yashandb': {'label': 'YashanDB', 'port': 1688, 'user': 'sys'},
        'kingbase': {'label': 'KingbaseES', 'port': 54321, 'user': 'system'},
        'gbase': {'label': 'GBase 8s', 'port': 9088, 'user': 'gbasedbt'},
    }
    
    for db_type, cfg in task_configs.items():
        result.append({
            'value': db_type,
            'label': cfg.get('label', db_type),
            'description': built_in_descriptions.get(db_type, ''),
            'icon': built_in_icons.get(db_type, ''),
            'emoji': built_in_emojis.get(db_type, '📊'),
            'is_plugin': False,
            'port': cfg.get('port', 3306),
            'user': cfg.get('user', 'root'),
            'compat_tag': cfg.get('compat_tag', ''),
        })
    
    # 2. 插件提供的数据库类型
    try:
        from plugin_loader import discover_plugins
        plugins = discover_plugins()
        for plugin in plugins:
            if plugin.get('enabled') and plugin.get('db_type'):
                # 从插件 manifest 读取自定义端口和用户名
                result.append({
                    'value': plugin.get('db_type'),
                    'label': plugin.get('name', plugin.get('db_type')),
                    'description': plugin.get('description', ''),
                    'icon': '/plugin-logo/' + plugin.get('db_type'),  # 插件图标统一从插件目录 /plugin-logo/<db_type> 读取，404 时前端回退 emoji
                    'emoji': plugin.get('emoji', '🧩'),  # 插件默认 emoji
                    'is_plugin': True,
                    'port': plugin.get('default_port', 3306),   # 插件可自定义端口
                    'user': plugin.get('default_user', 'root'), # 插件可自定义用户名
                    'compat_tag': plugin.get('compat_tag', ''),  # NoSQL / MySQL 等兼容性标签
                })
    except Exception as e:
        print(f"[API] 加载插件数据库类型失败: {e}")
    
    return jsonify(result)



@app.route('/api/pro/datasources/<instance_id>', methods=['DELETE'])
def api_pro_datasource_delete(instance_id):
    """删除数据源，同时清理 history.db 趋势数据"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        # 先获取实例信息（删除前）
        inst = im.get_instance(instance_id, mask_password=False)
        # 执行删除（instance_manager 内部已清 inspection_history + instance_trend）
        result = im.delete_instance(instance_id)
        # 同步清理 history.db（旧趋势系统）
        if result.get('ok') and inst:
            try:
                from analyzer import HistoryManager
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
    """测试数据源连接"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
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
    """测试数据库连接（直接传参）"""
    try:
        data = request.get_json()
        db_type = data.get('db_type', 'mysql')
        host = data.get('host', '')
        port = data.get('port', 3306)
        user = data.get('user', '')
        password = data.get('password', '')
        service_name = data.get('service_name', '')
        jdbc_url = data.get('jdbc_url')
        # MongoDB 专用连接参数（透传给插件连接测试）
        mongo_kwargs = {
            'connect_mode': data.get('connect_mode', 'standard'),
            'auth_source': data.get('auth_source', 'admin'),
            'auth_mechanism': data.get('auth_mechanism', ''),
            'replica_set': data.get('replica_set', ''),
            'tls': bool(data.get('tls', False)),
            'tls_ca_file': data.get('tls_ca_file', ''),
            'tls_cert_key_file': data.get('tls_cert_key_file', ''),
            'tls_allow_invalid_certs': bool(data.get('tls_allow_invalid_certs', False)),
            'database': data.get('database', 'admin'),
        }

        if not host:
            return jsonify({'ok': False, 'error': '请输入主机地址'})

        if db_type == 'oceanbase':
            import pymysql
            ob_user = user + '@' + data['tenant'] if data.get('tenant') else user
            ob_db = data.get('database') or 'sys'
            conn = pymysql.connect(host=host, port=port, user=ob_user, password=password, database=ob_db, connect_timeout=10)
            conn.close()
        elif db_type in ('mysql', 'tidb', 'mariadb'):
            import pymysql
            conn = pymysql.connect(host=host, port=port, user=user, password=password, connect_timeout=10)
            conn.close()
        elif db_type in ('pg', 'postgresql', 'ivorysql', 'kingbase'):
            import psycopg2
            db = data.get('database', 'postgres' if db_type != 'kingbase' else 'kingbase')
            conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=db, connect_timeout=10)
            conn.close()
        elif db_type == 'oracle':
            import oracledb
            _jdbc = (data.get('jdbc_url') or '').strip()
            if _jdbc and _jdbc.lstrip().upper().startswith('(DESCRIPTION'):
                # 纯 TNS 描述符：oracledb 可直接作为 dsn 使用
                dsn = _jdbc
            else:
                dsn = f"{host}:{port}/{service_name}" if service_name else f"{host}:{port}"
            ssh_host = data.get('ssh_host', '')
            _tunnel = None

            if ssh_host:
                # 配了 SSH → 只走隧道，不 fallback 直连
                try:
                    from ssh_tunnel import SSHTunnel
                    
                    _tunnel = SSHTunnel(
                        ssh_host=ssh_host,
                        ssh_port=int(data.get('ssh_port', 22)),
                        ssh_user=data.get('ssh_user', 'root'),
                        ssh_password=data.get('ssh_password', ''),
                        remote_host=host,
                        remote_port=int(port)
                    )
                    _tunnel.__enter__()
                    _local = _tunnel.local_port
                    dsn = f"localhost:{_local}/{service_name}" if service_name else f"localhost:{_local}"
                except Exception as te:
                    return jsonify({'ok': False, 'error': f'SSH 隧道建立失败: {te}'})

            try:
                params = {"user": user, "password": password, "dsn": dsn}
                if data.get('sysdba'):
                    params["mode"] = oracledb.SYSDBA
                conn = oracledb.connect(**params)
            except Exception as e:
                err_msg = str(e)
                if 'DPY-3010' in err_msg or 'DPY-3015' in err_msg:
                    # thin mode 不支持 Oracle 11g 及以下 / 老密码验证器(0x939)，尝试 thick mode
                    _thick_ok = False
                    # 1. 先尝试自动检测
                    try:
                        oracledb.init_oracle_client()
                        _thick_ok = True
                    except Exception:
                        pass
                    # 2. 自动检测失败，尝试 DBCheck 内置的 Instant Client
                    if not _thick_ok:
                        # 使用辅助函数查找 Oracle Client 目录（支持 lib/ 子目录）
                        _lib_dir = _find_oracle_client_lib_dir()
                        if _lib_dir:
                            try:
                                oracledb.init_oracle_client(lib_dir=_lib_dir)
                                _thick_ok = True
                            except Exception:
                                pass
                    # 3. 内置 Client 失败，尝试读用户配置的路径
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
                        return jsonify(
                            ok=False,
                            error='Oracle 11g 及以下版本需要 Oracle Instant Client。'
                                  '请通过左侧导航"Oracle Client"设置页，点击"一键下载并安装"按钮自动下载安装。'
                        )
                    try:
                        conn = oracledb.connect(**params)
                    except Exception as e2:
                        return jsonify(ok=False, error=f'Oracle 连接失败（thick mode）: {e2}')
                elif 'timed out' in err_msg.lower() or 'timeout' in err_msg.lower():
                    return jsonify(ok=False, error='连接超时，Oracle 可能无法直连，请在数据源中配置 SSH')
                else:
                    return jsonify(ok=False, error=str(e))
            conn.close()
            if _tunnel:
                _tunnel.close()
        elif db_type == 'dm':
            import dmPython
            try:
                conn = dmPython.connect(server=host, port=int(port), user=user, password=password)
                conn.close()
            except Exception as de:
                if 'exception set' in str(de):
                    return jsonify({'ok': False, 'error': f'达梦连接失败，请检查用户名（默认SYSDBA）、密码和端口（{host}:{port}）'})
                raise
            conn.close()
        elif db_type == 'sqlserver':
            import pyodbc
            conn_str = _build_sqlserver_conn_str(host, port, user, password, timeout=10)
            conn = pyodbc.connect(conn_str)
            conn.close()
        elif db_type == 'yashandb':
            try:
                import yasdb
                conn = yasdb.connect(host=host, port=int(port), user=user, password=password)
                conn.close()
            except ImportError as e:
                return jsonify({'ok': False, 'error': f'yasdb 驱动未安装: {str(e)}'})
        elif db_type == 'gbase':
            gbase_server = data.get('gbase_server_name', 'gbase01')
            result = test_gbase_connection(host, int(port), user, password, data.get('database', 'gbase01'), gbase_server)
            if not result[0]:
                return jsonify({'ok': False, 'error': result[1]})
        else:
            # 尝试使用插件测试连接
            ok, msg = test_plugin_connection(
                db_type,
                host,
                port,
                user,
                password,
                service_name=service_name,
                sysdba=data.get('sysdba', False),
                jdbc_url=jdbc_url,
                **mongo_kwargs
            )
            if ok:
                return jsonify({'ok': True, 'message': msg})
            else:
                if msg:  # msg不为None，说明是插件返回的错误
                    return jsonify({'ok': False, 'error': msg})
                else:  # msg为None，说明不是插件数据库类型
                    return jsonify({'ok': False, 'error': f'不支持的数据库类型: {db_type}'})

        return jsonify({'ok': True, 'message': '连接成功'})
    except ImportError as e:
        return jsonify({'ok': False, 'error': f'驱动未安装: {e}'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/pro/datasources/export', methods=['GET'])
def api_pro_datasources_export():
    """导出数据源 CSV"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        csv_content = im.export_csv()
        return jsonify({'ok': True, 'csv': csv_content})
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
    """导入数据源 CSV"""
    try:
        from pro import get_instance_manager
        data = request.get_json()
        csv_content = data.get('csv_content', '')
        im = get_instance_manager()
        result = im.batch_add_from_csv(csv_content)
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
    """返回某数据源的数据库列表"""
    from pro import get_instance_manager
    mgr = get_instance_manager()
    inst = mgr.get_instance_decrypted(ds_id)
    if not inst:
        return jsonify({'error': f'数据源不存在: {ds_id}'}), 404

    db_type = inst.get('db_type', '').lower().replace('oracle_full', 'oracle')
    if db_type == 'pg':
        db_type = 'postgresql'
    host     = inst.get('host', '')
    port     = int(inst.get('port', 3306))
    user     = inst.get('user', '')
    pwd      = inst.get('password') or ''
    timeout  = 10

    try:
        databases = []
        if db_type in ('mysql', 'tidb', 'mariadb', 'oceanbase'):
            import pymysql
            conn = pymysql.connect(host=host, port=port, user=user, password=pwd,
                                   connect_timeout=timeout, charset='utf8mb4')
            cur = conn.cursor()
            cur.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME")
            databases = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type in ('postgresql', 'ivorysql', 'kingbase'):
            import psycopg2
            # kingbase 默认库名为 kingbase，PG/IvorySQL 为 postgres
            _pg_default_db = 'kingbase' if db_type == 'kingbase' else 'postgres'
            conn = psycopg2.connect(host=host, port=port, user=user, password=pwd,
                                    dbname=_pg_default_db, connect_timeout=timeout)
            cur = conn.cursor()
            cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
            databases = [r[0] for r in cur.fetchall()]
            conn.close()
        elif db_type == 'gbase':
            # GBase 8s 使用 JDBC 连接（jaydebeapi）
            import jaydebeapi, os
            _jdbc_driver = _get_gbase_driver()
            print(f"[GBase] _get_gbase_driver() 返回: {_jdbc_driver}")
            if not _jdbc_driver:
                raise Exception('GBase 8s JDBC 驱动未找到，请将驱动 jar 文件放到 drivers/gbase/ 目录下')
            _db = inst.get('database', 'gbase01') or 'gbase01'
            _server = inst.get('gbase_server_name', 'gbase01') or 'gbase01'
            _jdbc_url  = f"jdbc:gbasedbt-sqli://{host}:{int(port)}/{_db}:GBASEDBTSERVER={_server};"
            print(f"[GBase] JDBC URL: {_jdbc_url}")
            print(f"[GBase] jars 参数: {_jdbc_driver}")
            conn = jaydebeapi.connect('com.gbasedbt.jdbc.Driver', _jdbc_url, [user, pwd], _jdbc_driver)
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

    from pro import get_instance_manager
    mgr = get_instance_manager()
    inst = mgr.get_instance_decrypted(ds_id)
    if not inst:
        return jsonify({'error': f'数据源不存在: {ds_id}'}), 404

    db_type = inst.get('db_type', '').lower().replace('oracle_full', 'oracle')
    if db_type == 'pg':
        db_type = 'postgresql'
    host    = inst.get('host', '')
    port    = int(inst.get('port', 3306))
    user    = inst.get('user', '')
    pwd     = inst.get('password') or ''
    timeout = 10

    try:
        tables, views = [], []
        if db_type in ('mysql', 'tidb', 'mariadb', 'oceanbase'):
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
        elif db_type in ('postgresql', 'ivorysql', 'kingbase'):
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
            # GBase 8s 使用 JDBC 连接（jaydebeapi）
            import jaydebeapi, os
            _jdbc_driver = _get_gbase_driver()
            print(f"[GBase] _get_gbase_driver() 返回: {_jdbc_driver}")
            if not _jdbc_driver:
                raise Exception('GBase 8s JDBC 驱动未找到，请将驱动 jar 文件放到 drivers/gbase/ 目录下')
            _db = database or 'gbase01'
            _server = inst.get('gbase_server_name', 'gbase01') or 'gbase01'
            _jdbc_url  = f"jdbc:gbasedbt-sqli://{host}:{int(port)}/{_db}:GBASEDBTSERVER={_server};"
            print(f"[GBase] JDBC URL: {_jdbc_url}")
            print(f"[GBase] jars 参数: {_jdbc_driver}")
            conn = jaydebeapi.connect('com.gbasedbt.jdbc.Driver', _jdbc_url, [user, pwd], _jdbc_driver)
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
        from pro import get_instance_manager
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

    db_type = db_info.get('db_type', '').replace('oracle_full', 'oracle')
    if db_type == 'pg':
        db_type = 'postgresql'
    host = db_info.get('host', '')
    port = int(db_info.get('port', 3306))
    user = db_info.get('user', '')
    pwd = db_info.get('password') or ''

    try:
        conn = None
        cursor = None
        if db_type in ('mysql', 'tidb', 'mariadb', 'oceanbase'):
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

        elif db_type in ('postgresql', 'ivorysql', 'kingbase'):
            import psycopg2
            db_name = database or 'postgres'
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
        from pro.rule_engine import get_rule_engine
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
        from pro.rule_engine import get_rule_engine
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
        from pro.rule_engine import get_rule_engine
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
        from pro.rule_engine import get_rule_engine
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
        from pro.backup import get_backup_manager
        data = request.get_json()
        conn_info = data.get('conn_info', {})
        pwd = conn_info.get('password', '')

        # 诊断信息
        diag = {
            'password_length': len(pwd),
            'password_masked': (pwd[:2] + '***') if pwd else '(empty)',
            'likely_encrypted': pwd and len(pwd) > 50 and '=' in pwd and '/' in pwd,
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
        from pro.backup import get_backup_manager
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
        from pro.backup import get_backup_manager
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
        from pro.backup import get_backup_manager
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
        from pro.backup import get_backup_manager
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
        from pro.backup import get_backup_manager
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
        from pro.backup import get_backup_manager
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
        db_file = os.path.join("pro_data", "backup_history.db")
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
        from pro import get_instance_manager
        im = get_instance_manager()
        # 使用 pro.db
        db_path = os.path.join(BASE_DIR, 'pro_data', 'pro.db')
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
        db_path = os.path.join(BASE_DIR, 'pro_data', 'pro.db')
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
        from pro import get_instance_manager
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

    db_type = db_info.get('db_type', '').lower()
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

        if db_type in ('mysql', 'tidb', 'mariadb', 'oceanbase'):
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
        db_path = os.path.join(BASE_DIR, 'pro_data', 'pro.db')
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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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
        from rag import RAGRetriever, VectorStore, OllamaEmbedding, OpenAIEmbedding
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
        from pro import get_instance_manager
        im = get_instance_manager()
        instances = im.get_all_instances(mask_password=False)
        return [inst.get('name', '') for inst in instances if inst.get('db_type') == db_type]
    except Exception:
        return []


def match_datasource(db_name: str):
    """从 Pro InstanceManager 按名称匹配数据源，返回解密后的连接信息"""
    try:
        from pro import get_instance_manager
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
        'database': ds.get('database') or ('postgres' if db_type == 'pg' else ('DAMENG' if db_type == 'dm' else '')),
        'service_name': ds.get('service_name') or ds.get('sid'),
        'name': ds.get('name', db_name or ds.get('host', '')),
    }

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
            from rag import RAGRetriever, VectorStore, OllamaEmbedding, OpenAIEmbedding
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
                'database': ds.get('database') or ('postgres' if db_type == 'pg' else ('DAMENG' if db_type == 'dm' else '')),
                'service_name': ds.get('service_name') or ds.get('sid'),
                'name': ds.get('name', db_name or ds.get('host', '')),
            }

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
        from pro import get_instance_manager
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
        from pro import get_instance_manager
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


@socketio.on('remote_shell_connect')
def on_remote_shell_connect(data):
    """SSH 连接远程服务器"""
    instance_id = data.get('instance_id', '')
    if not instance_id:
        socketio.emit('remote_shell_status', {'status': 'error', 'msg': '缺少实例 ID'}, room=request.sid)
        return

    try:
        from pro import get_instance_manager
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

        # 关闭已有会话
        if request.sid in _remote_sessions:
            try:
                _remote_sessions[request.sid]['channel'].close()
                _remote_sessions[request.sid]['ssh'].close()
            except Exception:
                pass
            _remote_sessions[request.sid]['thread'].join(timeout=3)
            del _remote_sessions[request.sid]

        import paramiko
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
                socketio.emit('remote_shell_status', {'status': 'error', 'msg': 'SSH 密码或密钥文件为空'}, room=request.sid)
                return
        except Exception as e:
            socketio.emit('remote_shell_status', {'status': 'error', 'msg': f'SSH 连接失败: {e}'}, room=request.sid)
            return

        # 打开一个 shell 通道
        channel = client.invoke_shell(term='xterm', width=120, height=30)

        # 在后台任务前捕获 sid（request context 不传递到后台线程）
        sid = request.sid

        # 启动读取线程（使用 socketio.start_background_task 确保 emit 在正确上下文中）
        def read_loop():
            import time
            # 等待 shell 启动并输出欢迎信息/提示符
            time.sleep(0.5)
            socketio.emit('remote_shell_status', {'status': 'connected', 'msg': f'已连接 {ssh_user}@{ssh_host}:{ssh_port}'}, room=sid)

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
            if sid in _remote_sessions:
                try:
                    channel.close()
                    client.close()
                except Exception:
                    pass
                _remote_sessions.pop(sid, None)
                socketio.emit('remote_shell_status', {'status': 'disconnected', 'msg': '连接已断开'}, room=sid)

        t = socketio.start_background_task(target=read_loop)

        _remote_sessions[sid] = {
            'ssh': client,
            'channel': channel,
            'thread': t,
            'instance_id': instance_id
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        socketio.emit('remote_shell_status', {'status': 'error', 'msg': str(e)}, room=request.sid)


@socketio.on('remote_shell_input')
def on_remote_shell_input(data):
    """接收前端终端输入，发送到 SSH channel"""
    sid = request.sid
    if sid in _remote_sessions:
        try:
            _remote_sessions[sid]['channel'].send(data.get('data', ''))
        except Exception:
            pass


@socketio.on('remote_shell_resize')
def on_remote_shell_resize(data):
    """终端窗口大小调整"""
    sid = request.sid
    if sid not in _remote_sessions:
        return
    try:
        cols = int(data.get('cols', 80))
        rows = int(data.get('rows', 24))
        _remote_sessions[sid]['channel'].resize_pty(width=cols, height=rows)
    except Exception:
        pass


@socketio.on('remote_shell_disconnect')
def on_remote_shell_disconnect():
    """断开 SSH 连接"""
    sid = request.sid
    if sid in _remote_sessions:
        try:
            _remote_sessions[sid]['channel'].close()
            _remote_sessions[sid]['ssh'].close()
        except Exception:
            pass
        _remote_sessions[sid]['thread'].join(timeout=3)
        del _remote_sessions[sid]
        socketio.emit('remote_shell_status', {'status': 'disconnected', 'msg': '已断开'}, room=sid)


@socketio.on('disconnect')
def on_disconnect():
    """前端断开时清理 SSH 会话"""
    sid = request.sid
    if sid in _remote_sessions:
        try:
            _remote_sessions[sid]['channel'].close()
            _remote_sessions[sid]['ssh'].close()
        except Exception:
            pass
        _remote_sessions[sid]['thread'].join(timeout=3)
        del _remote_sessions[sid]


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
    自动查找 GBase 8s JDBC 驱动 jar 文件。
    查找 drivers/gbase/ 目录下的所有 .jar 文件，
    返回路径列表（jaydebeapi 推荐用列表）。
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
        base_dir = BASE_DIR

        # 1. 巡检次数（Pro版 data/history.db 中的 snapshots JOIN history_instances）
        history_db = os.path.join(base_dir, 'data', 'history.db')
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
        server_db = os.path.join(base_dir, 'data', 'server_history.db')
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
            from data_manager import list_backups
            backups = list_backups()
            result['backup_count'] = len(backups)
            if backups:
                result['backup_latest'] = backups[0].get('name')
        except Exception:
            pass

        # 4. 基线配置
        inspection_db = os.path.join(base_dir, 'data', 'inspection.db')
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
            from pro import get_instance_manager
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
            from web_ui import _get_scheduler
            sm = _get_scheduler()
            jobs = sm.list_jobs()
            result['scheduled_jobs'] = len(jobs)
            result['scheduled_active'] = sum(1 for j in jobs if j.get('enabled'))
        except Exception:
            pass

        # 7. RAG 知识库
        try:
            from web_ui import _get_rag_manager
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
            from pro.rule_engine import get_rule_engine
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
        from data_manager import get_data_files_info
        files = get_data_files_info()
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/data_management/backups', methods=['GET'])
def api_data_management_backups():
    """列出所有备份"""
    try:
        from data_manager import list_backups
        backups = list_backups()
        return jsonify({'success': True, 'backups': backups})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/data_management/backup', methods=['POST'])
def api_data_management_backup():
    """创建数据备份"""
    try:
        from data_manager import backup_data
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
        from data_manager import restore_data
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
        from data_manager import delete_backup
        result = delete_backup(name)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/data_management/upgrade_check', methods=['GET'])
def api_data_management_upgrade_check():
    """检测升级状态"""
    try:
        from data_manager import check_upgrade_ready
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
        _quotes_file = os.path.join(BASE_DIR, 'dbcheck-quotes.json')
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
    else:
        base = BASE_DIR
    vpath = os.path.join(base, 'version.json')
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
                from dm8_offline_check import (
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

    from dm8_offline_check import generate_offline_report_word
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
    reports_dir = os.path.join(BASE_DIR, 'reports')
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


if __name__ == '__main__':
    # ── 信号处理：确保 Ctrl+C 能正确退出 ──
    def signal_handler(sig, frame):
        """处理 Ctrl+C 信号"""
        print("\n\n[主程序] 收到退出信号，正在关闭...")
        # 清理资源（如果有）
        try:
            from plugin_loader import unload_all_plugins
            unload_all_plugins()
        except:
            pass
        print("[主程序] 已退出")
        os._exit(0)  # 使用 os._exit(0) 强制退出
    
    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # kill 命令
    
    if platform.system().lower() == 'windows':
        # Windows 需要额外处理
        signal.signal(signal.SIGBREAK, signal_handler)  # Ctrl+Break
    
    _setup_driver_paths()
    # ── 初始化插件系统 ──
    # 主加载器切换为 plugin_core.load_plugins()（双类型支持）：
    # 规则插件按 entry 载 __init__.py 并 register() 注册进 PluginRegistry；
    # 巡检插件保持原路径。仅加载 enabled 目录，避免把 available 中的未启用插件注册进来。
    try:
        from plugin_core import load_plugins
        _enabled_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'plugins', 'enabled'
        )
        loaded_count = load_plugins(_enabled_dir)
        if loaded_count:
            print(f"[插件] 已加载 {loaded_count} 个插件（规则插件经 PluginRegistry 注册）")
    except Exception as e:
        print(f"[插件] 初始化跳过: {e}")
    port = 5003
    print(_t('webui.startup_msg').format(port=port))
    print("[提示] 按 Ctrl+C 停止服务\n")
    
    _verify_agreement_integrity()
    try:
        socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        print("\n[主程序] 收到 KeyboardInterrupt，正在退出...")
        os._exit(0)
