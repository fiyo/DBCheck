# coding: utf-8
"""
DBCheck Web UI - Flask 应用
数据库巡检工具 Web 界面
"""
import os, sys, threading, datetime, json, uuid, time, re
from flask import Flask, request, jsonify, render_template, Response, send_file
from flask_socketio import SocketIO, emit
import socket

# async_mode='threading' 最稳定，跨平台/打包零兼容问题，
# 满足 DBCheck Web UI 低并发使用场景（单用户/少量连接）。
# 不依赖 gevent/eventlet，避免打包后版本冲突。
socketio = SocketIO(cors_allowed_origins='*', async_mode='threading')

# ── 本地模块 ──────────────────────────────────────────────
try:
    import main_mysql, main_pg, main_oracle, main_dm
except ImportError:
    main_mysql = main_pg = main_oracle = main_dm = None

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
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fp)).strftime('%Y-%m-%d %H:%M')
            except Exception:
                continue
            db_type = 'DM8' if 'DM8' in f or '达梦' in f else \
                      'Oracle' if 'Oracle' in f else \
                      'PostgreSQL' if 'PG' in f or 'PostgreSQL' in f else 'MySQL'
            reports.append({'name': f, 'size': format_bytes(size), 'mtime': mtime, 'db_type': db_type})
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

    _emit('log', {'msg': f"[{_ts()}] [MySQL] 开始巡检..."})

    if not main_mysql:
        _emit('error', {'msg': 'MySQL 模块未安装'})
        return

    try:
        import main_mysql as mod
        _emit('log', {'msg': f"[{_ts()}] 连接 {db_info['ip']}:{db_info['port']}..."})
        ok, ver = test_mysql_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'])
        if not ok:
            raise RuntimeError(f"数据库连接失败: {ver}")
        _emit('log', {'msg': f"[{_ts()}] ✅ 数据库连接成功: {ver}"})

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host','ssh_port','ssh_user','ssh_password','ssh_key_file') if k in db_info}

        _emit('log', {'msg': f"[{_ts()}] 📊 开始执行巡检 SQL..."})
        data = mod.getData(db_info['ip'], db_info['port'], db_info['user'], db_info['password'], ssh_info)
        if data is None or data.conn_db2 is None:
            raise RuntimeError("无法建立数据库连接，getData 返回 None")
        ret = data.checkdb('builtin')
        if not ret:
            raise RuntimeError("checkdb 返回 False")
        if task:
            task['status'] = 'done'
        _emit('done', {'msg': f'巡检完成: {ver}', 'task_id': task_id})
    except Exception as e:
        _emit('error', {'msg': f"[MySQL] 巡检异常: {e}"})
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

    _emit('log', {'msg': f"[{_ts()}] [PostgreSQL] 开始巡检..."})

    if not main_pg:
        _emit('error', {'msg': 'PostgreSQL 模块未安装'})
        return

    try:
        import main_pg as mod
        _emit('log', {'msg': f"[{_ts()}] 连接 {db_info['ip']}:{db_info['port']}..."})
        ok, ver = test_pg_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'], db_info.get('database', 'postgres'))
        if not ok:
            raise RuntimeError(f"数据库连接失败: {ver}")
        _emit('log', {'msg': f"[{_ts()}] ✅ 数据库连接成功: {ver}"})

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host','ssh_port','ssh_user','ssh_password','ssh_key_file') if k in db_info}

        _emit('log', {'msg': f"[{_ts()}] 📊 开始执行巡检 SQL..."})
        data = mod.getData(db_info['ip'], db_info['port'], db_info['user'], db_info['password'],
                           database=db_info.get('database', 'postgres'), ssh_info=ssh_info)
        if data is None or data.conn_db2 is None:
            raise RuntimeError("无法建立数据库连接，getData 返回 None")
        ret = data.checkdb('builtin')
        if not ret:
            raise RuntimeError("checkdb 返回 False")
        if task:
            task['status'] = 'done'
        _emit('done', {'msg': f'巡检完成: {ver}', 'task_id': task_id})
    except Exception as e:
        _emit('error', {'msg': f"[PostgreSQL] 巡检异常: {e}"})
        if task:
            task['status'] = 'error'

def run_oracle_task(task_id, db_info, inspector_name):
    emit = socketio.emit
    task = tasks.get(task_id)
    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': f"[{_ts()}] [Oracle] 开始巡检..."})

    if not main_oracle:
        _emit('error', {'msg': 'Oracle 模块未安装'})
        return

    try:
        import main_oracle as mod
        _emit('log', {'msg': f"[{_ts()}] 连接 {db_info['ip']}:{db_info['port']}..."})
        service_name = db_info.get('service_name') or 'ORCL'
        ok, ver = test_oracle_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'], service_name)
        if not ok:
            raise RuntimeError(f"数据库连接失败: {ver}")
        _emit('log', {'msg': f"[{_ts()}] ✅ 数据库连接成功: {ver}"})

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host','ssh_port','ssh_user','ssh_password','ssh_key_file') if k in db_info}

        _emit('log', {'msg': f"[{_ts()}] 📊 开始执行巡检 SQL..."})
        data = mod.getData(db_info['ip'], db_info['port'], db_info['user'], db_info['password'],
                           service_name=service_name, ssh_info=ssh_info)
        if data is None or data.conn_db2 is None:
            raise RuntimeError("无法建立数据库连接，getData 返回 None")
        ret = data.checkdb('builtin')
        if not ret:
            raise RuntimeError("checkdb 返回 False")
        if task:
            task['status'] = 'done'
        _emit('done', {'msg': f'巡检完成: {ver}', 'task_id': task_id})
    except Exception as e:
        _emit('error', {'msg': f"[Oracle] 巡检异常: {e}"})
        if task:
            task['status'] = 'error'

def run_dm_task(task_id, db_info, inspector_name):
    print(f"[DEBUG] db_info: {db_info}")
    emit = socketio.emit
    task = tasks.get(task_id)
    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': f"[{_ts()}] [DM8] 开始巡检..."})

    if not main_dm:
        _emit('error', {'msg': 'DM8 模块未安装'})
        return

    try:
        import main_dm as mod
        _emit('log', {'msg': f"[{_ts()}] 连接 {db_info['ip']}:{db_info['port']}..."})
        ok, ver = test_dm_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'])
        if not ok:
            raise RuntimeError(f"数据库连接失败: {ver}")
        _emit('log', {'msg': f"[{_ts()}] ✅ 数据库连接成功: {ver}"})

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host','ssh_port','ssh_user','ssh_password','ssh_key_file') if k in db_info}

        _emit('log', {'msg': f"[{_ts()}] 📊 开始执行巡检 SQL..."})
        # 传 db_name（getData 第5参数），CLI 模式默认 DAMENG
        data = mod.getData(db_info['ip'], db_info['port'], db_info['user'], db_info['password'],
                           db_name=db_info.get('database', 'DAMENG'), ssh_info=ssh_info)
        if data is None or data.conn_db is None:
            raise RuntimeError("无法建立数据库连接，getData 返回空")
        _emit('log', {'msg': f"[{_ts()}] 📊 执行增强智能分析..."})
        context = data.checkdb('builtin')
        if not context:
            raise RuntimeError("checkdb 返回空")

        # 修正 co_name、dm_version 和 dm_instance（checkdb 内部查询结果可能为空）
        context['co_name'] = [{'DB_NAME': db_info.get('database') or 'DAMENG'}]
        context['dm_version'] = [{'BANNER': '达梦 DM8'}]
        # dm_instance 用于第1章表格，确保不为空
        if not context.get('dm_instance'):
            context['dm_instance'] = [{'INSTANCE_NAME': db_info.get('database') or 'DAMENG'}]

        # AI 诊断结果（checkdb 内部已执行）
        if context.get('ai_advice'):
            _emit('log', {'msg': f"[{_ts()}] 🤖 AI 诊断完成"})
        if task:
            task['ai_advice'] = context.get('ai_advice', '')

        # 生成报告文件
        _emit('log', {'msg': f"[{_ts()}] 📄 生成 Word 报告..."})
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        _dt = __import__('datetime').datetime
        ofile = os.path.join(reports_dir, f"DM8_Report_{_dt.now().strftime('%Y%m%d_%H%M%S')}.docx")
        ifile = mod.create_word_template(inspector_name)
        saver = mod.saveDoc(context, ofile, ifile, inspector_name, H=data.H, P=data.P)
        if not saver.contextsave():
            raise RuntimeError("报告生成失败")
        _emit('log', {'msg': f"[{_ts()}] ✅ 报告已生成: {os.path.basename(ofile)}"})

        if task:
            task['status'] = 'done'
            task['report_name'] = os.path.basename(ofile)
            task['report_file'] = ofile
        _emit('done', {'msg': f'巡检完成: {ver}', 'task_id': task_id,
                       'ai_advice': context.get('ai_advice', '')})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': f"[DM8] 巡检异常: {e}\n{traceback.format_exc()}"})
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

def test_oracle_connection(host, port, user, password, service_name='ORCL'):
    try:
        import oracledb
        conn = oracledb.connect(user=user, password=password,
                                host=host, port=int(port), service_name=service_name)
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
                return True, f"SSH 主机可达，但认证失败（请确认密码或密钥）"
        client.close()
        return True, "SSH 连接成功"

    except Exception as e:
        err_msg = str(e)
        if "timed out" in err_msg.lower() or "connection refused" in err_msg.lower():
            return False, f"无法连接 SSH: 请检查主机地址和端口 ({err_msg})"
        return False, f"SSH 连接失败: {err_msg}"


# ── 路由 ────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

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
    return jsonify([])

@app.route('/api/ai_config', methods=['GET'])
def api_ai_config():
    return jsonify({'enabled': False, 'url': ''})

@app.route('/api/test_db', methods=['POST'])
def api_test_db():
    data = request.json
    db_type = data.get('db_type', 'mysql')

    if db_type == 'mysql':
        ok, msg = test_mysql_connection(data['host'], data['port'], data['user'], data['password'], data.get('database'))
    elif db_type == 'pg':
        ok, msg = test_pg_connection(data['host'], data['port'], data['user'], data['password'], data.get('database', 'postgres'))
    elif db_type == 'oracle':
        ok, msg = test_oracle_connection(data['host'], data['port'], data['user'], data['password'], data.get('service_name', 'ORCL'))
    elif db_type == 'dm':
        ok, msg = test_dm_connection(data['host'], data['port'], data['user'], data['password'])
    else:
        return jsonify({'ok': False, 'msg': '未知数据库类型'})

    return jsonify({'ok': ok, 'msg': msg})


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
            'oracle':run_oracle_task,
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
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
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
        socketio.emit('log', {'msg': f'[{_ts()}] 已连接，正在等待任务...'})

# ── 启动 ────────────────────────────────────────────────────
if __name__ == '__main__':
    port = 5003
    print(f"DBCheck Web UI 启动中: http://localhost:{port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
