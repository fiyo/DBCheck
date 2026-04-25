# coding: utf-8
#
# Copyright (c) 2024 DBCheck Contributors
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License.
# See LICENSE or visit https://opensource.org/licenses/MIT for full license text.
#
"""
DBCheck Web UI - Flask 应用
数据库巡检工具 Web 界面
"""
import os, sys, threading, datetime, json, uuid, time, re
from flask import Flask, request, jsonify, render_template, Response, send_file
from version import __version__
from flask_socketio import SocketIO, emit
import socket
from i18n import t as _t

# async_mode='threading' 最稳定，跨平台/打包零兼容问题，
# 满足 DBCheck Web UI 低并发使用场景（单用户/少量连接）。
# 不依赖 gevent/eventlet，避免打包后版本冲突。
socketio = SocketIO(cors_allowed_origins='*', async_mode='threading')

# ── 本地模块 ──────────────────────────────────────────────
try:
    import main_mysql, main_pg, main_dm, main_oracle_full
except ImportError:
    main_mysql = main_pg = main_dm = main_oracle_full = None

app = Flask(__name__, template_folder='web_templates', static_folder='web_templates')
app.config['SECRET_KEY'] = os.urandom(24)
socketio.init_app(app)

# 全局任务状态
tasks = {}

# ── 工具函数 ───────────────────────────────────────────────
def _ts():
    return datetime.datetime.now().strftime('%H:%M:%S')

def escHtml(s):
    if s is None: return ''
    return (str(s)
        .replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        .replace('"','&quot;').replace("'",'&#39;'))

def format_bytes(n):
    try:
        n = int(n)
        for u in ['B','KB','MB','GB','TB']:
            if n < 1024: return f"{n:.1f}{u}"
            n /= 1024
        return f"{n:.1f}PB"
    except: return str(n)

def get_reports():
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
    reports = []
    if os.path.isdir(reports_dir):
        try:
            files = [f for f in os.listdir(reports_dir)
                     if f.endswith('.docx') and not f.startswith('~$')]
        except Exception:
            files = []
        for f in sorted(files, key=lambda x: os.path.getmtime(os.path.join(reports_dir, x)), reverse=True):
            fp = os.path.join(reports_dir, f)
            try:
                size = os.path.getsize(fp)
                mtime = os.path.getmtime(fp)   # Unix 时间戳（秒），前端负责格式化
            except Exception:
                continue
            db_type = 'DM8' if 'DM8' in f or '达梦' in f else \
                      'Oracle' if 'Oracle' in f else \
                      'PostgreSQL' if 'PG' in f or 'PostgreSQL' in f else 'MySQL'
            reports.append({'name': f, 'size': size, 'mtime': mtime, 'db_type': db_type})
    return {'files': reports}

# ── 巡检任务 ───────────────────────────────────────────────
def run_mysql_task(task_id, db_info, inspector_name):
    emit = socketio.emit
    task = tasks.get(task_id)
    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': _t('webui.log_mysql_start').format(ts=_ts())})

    if not main_mysql:
        _emit('error', {'msg': _t('webui.err_mysql_module')})
        return

    try:
        import main_mysql as mod
        _emit('log', {'msg': _t('webui.log_connecting').format(ts=_ts(), host=db_info['ip'], port=db_info['port'])})
        ok, ver = test_mysql_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'])
        if not ok:
            raise RuntimeError(_t('webui.err_db_connect').format(ver=ver))
        _emit('log', {'msg': _t('webui.log_connected').format(ts=_ts(), ver=ver)})

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host','ssh_port','ssh_user','ssh_password','ssh_key_file') if k in db_info}

        _emit('log', {'msg': _t('webui.log_executing_sql').format(ts=_ts())})
        data = mod.getData(db_info['ip'], db_info['port'], db_info['user'], db_info['password'], ssh_info)
        if data is None or data.conn_db2 is None:
            raise RuntimeError(_t('webui.err_getdata_none'))

        # ── stdout 重定向：捕获 checkdb() 内部的 AI 诊断等 print 输出 ───
        import builtins as _bi
        _orig_mysql_print = _bi.print
        def _web_mysql_print(*_a, **_kw):
            _sep = _kw.get('sep', ' ')
            _msg = _sep.join(str(x) for x in _a)
            _msg_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', _msg)
            if _msg_clean.strip():
                _emit('log', {'msg': _msg_clean})
            _orig_mysql_print(*_a, **_kw)
        _bi.print = _web_mysql_print
        try:
            ret = data.checkdb('builtin')
        finally:
            _bi.print = _orig_mysql_print

        if not ret:
            raise RuntimeError(_t('webui.err_checkdb_false'))



        # ── 生成 Word 报告 ───────────────────────────────────
        _emit('log', {'msg': _t('webui.log_generating_report').format(ts=_ts())})
        label_name = db_info.get('name', db_info.get('ip', 'unknown'))
        ret.update({"co_name": [{'CO_NAME': label_name}]})
        ret.update({"port": [{'PORT': db_info['port']}]})
        ret.update({"ip": [{'IP': db_info['ip']}]})

        inspector_name = db_info.get('inspector_name') or 'Jack'
        ifile = mod.create_word_template(inspector_name)
        if not ifile:
            raise RuntimeError(_t('webui.err_template_create'))

        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        ext_name = _t('webui.mysql_report_filename').format(name=label_name, ts=timestamp)
        file_name = ext_name + '.docx'
        ofile = os.path.join(reports_dir, file_name)

        savedoc = mod.saveDoc(context=ret, ofile=ofile, ifile=ifile, inspector_name=inspector_name)
        if not savedoc.contextsave():
            raise RuntimeError(_t('webui.err_report_generate'))
        _emit('log', {'msg': _t('webui.log_report_ok').format(fname=file_name)})

        if task:
            task['status'] = 'done'
            task['report_file'] = ofile
            task['report_name'] = file_name
        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id})
    except Exception as e:
        _emit('error', {'msg': _t('webui.err_inspection').format(task='MySQL', e=e)})
        if task:
            task['status'] = 'error'

def run_pg_task(task_id, db_info, inspector_name):
    emit = socketio.emit
    task = tasks.get(task_id)
    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': _t('webui.log_pg_start').format(ts=_ts())})

    if not main_pg:
        _emit('error', {'msg': _t('webui.err_pg_module')})
        return

    try:
        import main_pg as mod
        _emit('log', {'msg': _t('webui.log_connecting').format(ts=_ts(), host=db_info['ip'], port=db_info['port'])})
        ok, ver = test_pg_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'], db_info.get('database', 'postgres'))
        if not ok:
            raise RuntimeError(_t('webui.err_db_connect').format(ver=ver))
        _emit('log', {'msg': _t('webui.log_connected').format(ts=_ts(), ver=ver)})

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host','ssh_port','ssh_user','ssh_password','ssh_key_file') if k in db_info}

        _emit('log', {'msg': _t('webui.log_executing_sql').format(ts=_ts())})
        data = mod.getData(db_info['ip'], db_info['port'], db_info['user'], db_info['password'],
                           database=db_info.get('database', 'postgres'), ssh_info=ssh_info)
        if data is None or data.conn_db2 is None:
            raise RuntimeError(_t('webui.err_getdata_none'))

        # ── stdout 重定向：捕获 checkdb() 内部的 AI 诊断等 print 输出 ───
        import builtins as _bi
        _orig_pg_print = _bi.print
        def _web_pg_print(*_a, **_kw):
            _sep = _kw.get('sep', ' ')
            _msg = _sep.join(str(x) for x in _a)
            _msg_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', _msg)
            if _msg_clean.strip():
                _emit('log', {'msg': _msg_clean})
            _orig_pg_print(*_a, **_kw)
        _bi.print = _web_pg_print
        try:
            ret = data.checkdb('builtin')
        finally:
            _bi.print = _orig_pg_print

        if not ret:
            raise RuntimeError(_t('webui.err_checkdb_false'))
        if task:
            task['status'] = 'done'
        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id})
    except Exception as e:
        _emit('error', {'msg': _t('webui.err_inspection').format(task='PostgreSQL', e=e)})
        if task:
            task['status'] = 'error'

def run_oracle_full_task(task_id, db_info, inspector_name):
    """Oracle 全面巡检（增强版）Web UI 任务"""
    emit = socketio.emit
    task = tasks.get(task_id)
    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': _t('webui.log_oracle_start').format(ts=_ts())})

    if not main_oracle_full:
        _emit('error', {'msg': _t('webui.err_oracle_module')})
        return

    try:
        import main_oracle_full as mod

        # ── 构造 args 命名空间 ─────────────────────────────────
        class _Args:
            pass
        args = _Args()
        args.host        = db_info.get('ip', '')
        args.port        = int(db_info.get('port', 1521) or 1521)
        args.user        = db_info.get('user', 'sys')
        args.password    = db_info.get('password', '')
        # Oracle 连接方式：优先 service_name，其次 sid
        args.servicename = db_info.get('service_name') or None
        args.sid         = db_info.get('sid') or None
        # 如果都没指定，默认用 ORCL 作为 SID
        if not args.sid and not args.servicename:
            args.sid = db_info.get('database', 'ORCL')
        # 解析 "user as sysdba" 语法，分离真实用户名和 SYSDBA 标识
        _raw_user = db_info.get('user', 'sys').strip()
        _sysdba_from_user = bool(re.search(r'\bas\s+sysdba\b', _raw_user, re.IGNORECASE))
        _real_user = re.sub(r'\s+as\s+sysdba\b', '', _raw_user, flags=re.IGNORECASE).strip()
        args.user = _real_user
        # sys 用户默认以 SYSDBA 登录（除非用户名已含 as sysdba 则不再重复覆盖）
        args.sysdba = bool(db_info.get('sysdba', _sysdba_from_user or _real_user.upper() == 'SYS'))
        # SSH
        args.ssh_host  = db_info.get('ssh_host') or None
        args.ssh_port  = int(db_info.get('ssh_port', 22) or 22)
        args.ssh_user  = db_info.get('ssh_user') or None
        args.ssh_pass  = db_info.get('ssh_password') or None
        # 输出
        args.output     = db_info.get('output_dir') or None
        args.zip        = bool(db_info.get('zip', False))
        # 巡检人
        args.inspector  = inspector_name or ''

        service_desc = args.servicename or f"SID={args.sid}"
        _emit('log', {'msg': f"[{_ts()}] 连接 Oracle {args.host}:{args.port}/{service_desc}..."})

        ok, ver = test_oracle_connection(args.host, args.port, args.user, args.password, args.servicename or args.sid, args.sysdba)
        if not ok:
            raise RuntimeError(_t('webui.err_db_connect').format(ver=ver))
        _emit('log', {'msg': _t('webui.log_connected').format(ts=_ts(), ver=ver)})

        _emit('log', {'msg': _t('webui.log_oracle_inspecting').format(ts=_ts())})

        # ── 将 mod.single_inspection 中的 print 输出重定向到 WebUI 日志 ──────
        import builtins
        _orig_print = builtins.print

        def _web_print(*args_list, **kwargs):
            sep = kwargs.get('sep', ' ')
            msg = sep.join(str(a) for a in args_list)
            # 去掉 ANSI 转义码再发送
            msg_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', msg)
            _emit('log', {'msg': msg_clean})
            # 同时写回原 print（服务器 stdout）
            _orig_print(*args_list, **kwargs)

        builtins.print = _web_print
        try:
            mod.single_inspection(args)
        finally:
            builtins.print = _orig_print

        if task:
            task['status'] = 'done'
        _emit('done', {'msg': _t('webui.log_oracle_done'), 'task_id': task_id})
    except Exception as e:
        _emit('error', {'msg': _t('webui.err_inspection').format(task='Oracle 全面巡检', e=e)})
        if task:
            task['status'] = 'error'


def run_dm_task(task_id, db_info, inspector_name):
    emit = socketio.emit
    task = tasks.get(task_id)
    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': _t('webui.log_dm_start').format(ts=_ts())})

    if not main_dm:
        _emit('error', {'msg': _t('webui.err_dm_module')})
        return

    try:
        import main_dm as mod
        _emit('log', {'msg': _t('webui.log_connecting').format(ts=_ts(), host=db_info['ip'], port=db_info['port'])})
        ok, ver = test_dm_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'])
        if not ok:
            raise RuntimeError(_t('webui.err_db_connect').format(ver=ver))
        _emit('log', {'msg': _t('webui.log_connected').format(ts=_ts(), ver=ver)})

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host','ssh_port','ssh_user','ssh_password','ssh_key_file') if k in db_info}

        _emit('log', {'msg': _t('webui.log_executing_sql').format(ts=_ts())})
        # 传 db_name（getData 第5参数），CLI 模式默认 DAMENG
        data = mod.getData(db_info['ip'], db_info['port'], db_info['user'], db_info['password'],
                           db_name=db_info.get('database', 'DAMENG'), ssh_info=ssh_info)
        if data is None or data.conn_db is None:
            raise RuntimeError(_t('webui.err_getdata_none'))
        _emit('log', {'msg': _t('webui.log_dm_analyzing').format(ts=_ts())})
        # ── stdout 重定向：捕获 checkdb() 内部的 AI 诊断等 print 输出 ───
        import builtins as _bi
        _orig_dm_print = _bi.print
        def _web_dm_print(*_a, **_kw):
            _sep = _kw.get('sep', ' ')
            _msg = _sep.join(str(x) for x in _a)
            _msg_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', _msg)
            if _msg_clean.strip():
                _emit('log', {'msg': _msg_clean})
            _orig_dm_print(*_a, **_kw)
        _bi.print = _web_dm_print
        try:
            context = data.checkdb('builtin')
        finally:
            _bi.print = _orig_dm_print

        if not context:
            raise RuntimeError(_t('webui.err_checkdb_empty'))

        # 修正 co_name、dm_version 和 dm_instance（checkdb 内部查询结果可能为空）
        context['co_name'] = [{'DB_NAME': db_info.get('database') or 'DAMENG'}]
        context['dm_version'] = [{'BANNER': _t('webui.dm_banner')}]
        # dm_instance 用于第1章表格，确保不为空
        if not context.get('dm_instance'):
            context['dm_instance'] = [{'INSTANCE_NAME': db_info.get('database') or 'DAMENG'}]

        # AI 诊断结果（checkdb 内部已执行）
        if context.get('ai_advice'):
            _emit('log', {'msg': _t('webui.log_ai_done').format(ts=_ts())})
        if task:
            task['ai_advice'] = context.get('ai_advice', '')

        # 生成报告文件
        _emit('log', {'msg': _t('webui.log_generating_report').format(ts=_ts())})
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        _dt = __import__('datetime').datetime
        ofile = os.path.join(reports_dir, f"DM8_Report_{_dt.now().strftime('%Y%m%d_%H%M%S')}.docx")
        ifile = mod.create_word_template(inspector_name)
        saver = mod.saveDoc(context, ofile, ifile, inspector_name, H=data.H, P=data.P)
        if not saver.contextsave():
            raise RuntimeError(_t('webui.err_report_failed'))
        _emit('log', {'msg': _t('webui.log_report_done').format(ts=_ts(), fname=os.path.basename(ofile))})

        if task:
            task['status'] = 'done'
            task['report_name'] = os.path.basename(ofile)
            task['report_file'] = ofile
        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id,
                       'ai_advice': context.get('ai_advice', '')})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': _t('webui.err_inspection').format(task='DM8', e=f"{e}\n{traceback.format_exc()}")})
        if task:
            task['status'] = 'error'

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

def test_pg_connection(host, port, user, password, database='postgres'):
    try:
        import psycopg2
        conn = psycopg2.connect(host=host, port=int(port), user=user, password=password,
                                database=database, connect_timeout=10)
        ver = psycopg2.extensions.parse_version_only(conn.server_version)
        conn.close()
        return True, f"PostgreSQL {ver}"
    except Exception as e:
        return False, str(e)

def test_oracle_connection(host, port, user, password, service_name='ORCL', sysdba=False):
    try:
        import oracledb
        # 解析 "user as sysdba" 语法
        _user = user.strip()
        _mode = oracledb.SYSDBA if (sysdba or re.search(r'\bas\s+sysdba\b', _user, re.IGNORECASE)) else None
        _user = re.sub(r'\s+as\s+sysdba\b', '', _user, flags=re.IGNORECASE).strip()
        kw = dict(user=_user, password=password, host=host, port=int(port), service_name=service_name)
        if _mode is not None:
            kw['mode'] = _mode
        conn = oracledb.connect(**kw)
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
        elif password:
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
    # 注入当前语言到前端（页面加载时就知道语言，无需额外请求）
    try:
        from i18n import get_lang, get_all_translations, get_language_display
        lang = get_lang()
        i18n_data = get_all_translations(lang)
    except Exception:
        lang = 'zh'
        i18n_data = {}
    return render_template('index.html', version=__version__, lang=lang, i18n_data=i18n_data)


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
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
    fp = os.path.join(reports_dir, name)
    if not os.path.isfile(fp):
        return "File not found", 404
    return send_file(fp, as_attachment=True, download_name=name)

@app.route('/api/history_instances', methods=['GET'])
def api_history_instances():
    """返回所有有历史记录的数据库实例列表"""
    try:
        from analyzer import HistoryManager
        script_dir = os.path.dirname(os.path.abspath(__file__))
        hm = HistoryManager(script_dir)
        instances = []
        for key, val in hm._data.items():
            snapshots = val.get('snapshots', [])
            last = snapshots[-1] if snapshots else {}
            instances.append({
                'key': key,
                'db_type': val.get('db_type', ''),
                'host': val.get('host', ''),
                'port': str(val.get('port', '')),
                'label': val.get('label', key),
                'snapshot_count': len(snapshots),
                'last_time': last.get('ts', ''),
                'last_health': last.get('health_status', _t('webui.health_unknown')),
                'last_risk': last.get('risk_count', 0),
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
        script_dir = os.path.dirname(os.path.abspath(__file__))
        hm = HistoryManager(script_dir)
        trend = hm.get_trend(db_type, host, int(port))
        comparison = hm.get_comparison(db_type, host, int(port))
        return jsonify({'ok': True, 'trend': trend, 'comparison': comparison})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/ai_config', methods=['GET'])
def api_ai_config():
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_config.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return jsonify(cfg)
    return jsonify({'enabled': False, 'url': '', 'backend': '', 'model': ''})

@app.route('/api/ai_config', methods=['POST'])
def api_save_ai_config():
    data = request.json or {}
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_config.json')
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return jsonify({'ok': True, 'msg': _t('webui.ai_config_saved')})

@app.route('/api/test_db', methods=['POST'])
def api_test_db():
    data = request.json
    db_type = data.get('db_type', 'mysql')

    if db_type == 'mysql':
        ok, msg = test_mysql_connection(data['host'], data['port'], data['user'], data['password'], data.get('database'))
    elif db_type == 'pg':
        ok, msg = test_pg_connection(data['host'], data['port'], data['user'], data['password'], data.get('database', 'postgres'))
    elif db_type == 'oracle_full':
        ok, msg = test_oracle_connection(data['host'], data['port'], data['user'], data['password'], data.get('service_name', 'ORCL'), bool(data.get('sysdba')))
    elif db_type == 'dm':
        ok, msg = test_dm_connection(data['host'], data['port'], data['user'], data['password'])
    else:
        return jsonify({'ok': False, 'msg': _t('webui.err_unknown_db_type')})

    return jsonify({'ok': ok, 'msg': msg})


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


@app.route('/api/test_ssh', methods=['POST'])
def api_test_ssh():
    """测试 SSH 连接"""
    data = request.json
    ok, msg = test_ssh_connection(
        data.get('ssh_host', ''),
        data.get('ssh_port', 22),
        data.get('ssh_user', 'root'),
        data.get('ssh_password', ''),
        data.get('ssh_key_file', '')
    )
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/start_inspection', methods=['POST'])
def api_start_inspection():
    try:
        data = request.json
        db_type = data.get('db_type', 'mysql')
        inspector_name = data.get('inspector_name', data.get('inspector', 'Jack'))

        db_info = {
            'ip':        data.get('host', ''),
            'port':      int(data.get('port', 0) or 0),
            'user':      data.get('user', ''),
            'password':  data.get('password', ''),
            'database':  'DAMENG' if db_type == 'dm' else (data.get('database') or 'postgres'),
            'service_name': data.get('service_name', None),
            'sid':       data.get('sid', None),
            'output_dir': data.get('output_dir', None),
            'zip':       data.get('zip', False),
        }

        if data.get('ssh_host'):
            db_info.update({
                'ssh_host':     data.get('ssh_host', ''),
                'ssh_port':     int(data.get('ssh_port', 22)),
                'ssh_user':     data.get('ssh_user', 'root'),
                'ssh_password': data.get('ssh_password', ''),
                'ssh_key_file': data.get('ssh_key_file', ''),
            })

        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            'id':          task_id,
            'db_type':     db_type,
            'db_info':     db_info,
            'inspector':   inspector_name,
            'status':      'running',
            'started_at':  datetime.datetime.now().isoformat()
        }
        t = threading.Thread(target={
            'mysql': run_mysql_task,
            'pg':    run_pg_task,
            'oracle_full':run_oracle_full_task,
            'dm':    run_dm_task,
        }.get(db_type, run_mysql_task), args=(task_id, db_info, inspector_name))
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
    return jsonify({
        'ok': True,
        'status': task.get('status', 'running'),
        'log': log_list[offset:],
        'offset': len(log_list),
    })


# ── WebSocket 事件 ──────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    pass

@socketio.on('join')
def on_join(data):
    task_id = data.get('task_id')
    if task_id:
        socketio.emit('log', {'msg': _t('webui.ws_connected_waiting').format(ts=_ts())})

# ── 启动 ────────────────────────────────────────────────────
if __name__ == '__main__':
    port = 5003
    print(_t('webui.startup_msg').format(port=port))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
