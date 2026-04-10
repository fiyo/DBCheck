#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库巡检工具 Web UI 入口
==========================
基于 Flask 提供 Web 界面，将所有命令行交互转换为可视化操作。
启动: python web_ui.py
默认访问: http://localhost:5000
"""

import os
import sys
import json
import threading
import time
import uuid
import queue
import traceback
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

app = Flask(__name__, template_folder=os.path.join(SCRIPT_DIR, 'web_templates'))

# 任务状态存储（内存级，生产可改 Redis）
tasks = {}


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def test_mysql_connection(host, port, user, password):
    """测试 MySQL 连接，返回 (ok: bool, msg: str)"""
    try:
        import pymysql
        conn = pymysql.connect(
            host=host, port=int(port), user=user, password=password,
            charset='utf8mb4', connect_timeout=10
        )
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, f"MySQL {ver}"
    except Exception as e:
        return False, str(e)


def test_pg_connection(host, port, user, password, database='postgres'):
    """测试 PostgreSQL 连接，返回 (ok: bool, msg: str)"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=host, port=int(port), user=user, password=password,
            dbname=database or 'postgres', client_encoding='UTF8', connect_timeout=10
        )
        cur = conn.cursor()
        cur.execute("SELECT version()")
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, ver
    except Exception as e:
        return False, str(e)


def test_ssh_connection(ssh_host, ssh_port, ssh_user, ssh_password, ssh_key_file):
    """测试 SSH 连接，返回 (ok: bool, msg: str)"""
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(hostname=ssh_host, port=int(ssh_port), username=ssh_user, timeout=10)
        if ssh_key_file and os.path.exists(ssh_key_file):
            kwargs['key_filename'] = ssh_key_file
        else:
            kwargs['password'] = ssh_password
        client.connect(**kwargs)
        client.close()
        return True, f"SSH 连接成功 {ssh_host}:{ssh_port}"
    except Exception as e:
        return False, str(e)


def run_mysql_task(task_id, db_info, inspector_name):
    """在后台线程中执行 MySQL 巡检"""
    task = tasks[task_id]
    log = task['log']

    def emit(msg):
        log.append(msg)
        task['last_update'] = time.time()

    try:
        emit(f"[{_ts()}] ▶ 开始 MySQL 巡检: {db_info['name']}")
        task['status'] = 'running'

        # 动态导入，避免阻塞主线程
        import importlib.util
        spec = importlib.util.spec_from_file_location("main_mysql", os.path.join(SCRIPT_DIR, "main_mysql.py"))
        mod = importlib.util.module_from_spec(spec)
        # 注入 infos 兼容对象，替代命令行解析（Web 模式下无命令行参数）
        class _FakeInfos:
            label = db_info.get('name', 'DBCheck')
            sqltemplates = 'builtin'
            batch = False
        mod.infos = _FakeInfos()
        spec.loader.exec_module(mod)
        mod.infos = _FakeInfos()   # exec_module 后再赋一次，确保模块内引用有效

        emit(f"[{_ts()}] 🔍 创建报告模板...")
        ifile = mod.create_word_template(inspector_name)
        if not ifile:
            raise RuntimeError("Word 模板创建失败")

        dir_path = os.path.join(SCRIPT_DIR, "reports")
        os.makedirs(dir_path, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_name = f"MySQL巡检报告_{db_info['name']}_{timestamp}.docx"
        ofile = os.path.join(dir_path, file_name)

        emit(f"[{_ts()}] 🔍 测试数据库连接 {db_info['ip']}:{db_info['port']}...")
        ok, ver = test_mysql_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'])
        if not ok:
            raise RuntimeError(f"数据库连接失败: {ver}")
        emit(f"[{_ts()}] ✅ 数据库连接成功: {ver}")

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host','ssh_port','ssh_user','ssh_password','ssh_key_file') if k in db_info}

        emit(f"[{_ts()}] 📊 开始执行巡检 SQL...")
        data = mod.getData(db_info['ip'], db_info['port'], db_info['user'], db_info['password'], ssh_info)
        if data is None or data.conn_db2 is None:
            raise RuntimeError("无法建立数据库连接，getData 返回 None")

        ret = data.checkdb('builtin')
        if not ret:
            raise RuntimeError("巡检执行失败（checkdb 返回空）")

        ret.update({"co_name": [{'CO_NAME': db_info['name']}]})
        ret.update({"port": [{'PORT': db_info['port']}]})
        ret.update({"ip": [{'IP': db_info['ip']}]})

        emit(f"[{_ts()}] 📝 正在渲染 Word 报告...")
        savedoc = mod.saveDoc(context=ret, ofile=ofile, ifile=ifile, inspector_name=inspector_name)
        success = savedoc.contextsave()

        if success:
            emit(f"[{_ts()}] ✅ 报告生成成功: {file_name}")
            task['status'] = 'done'
            task['report_file'] = ofile
            task['report_name'] = file_name
        else:
            raise RuntimeError("Word 报告渲染失败")

        try:
            if os.path.exists(ifile):
                os.remove(ifile)
        except Exception:
            pass

    except Exception as e:
        emit(f"[{_ts()}] ❌ 巡检失败: {e}")
        emit(traceback.format_exc())
        task['status'] = 'error'
        task['error'] = str(e)


def run_pg_task(task_id, db_info, inspector_name):
    """在后台线程中执行 PostgreSQL 巡检"""
    task = tasks[task_id]
    log = task['log']

    def emit(msg):
        log.append(msg)
        task['last_update'] = time.time()

    try:
        emit(f"[{_ts()}] ▶ 开始 PostgreSQL 巡检: {db_info['name']}")
        task['status'] = 'running'

        import importlib.util
        spec = importlib.util.spec_from_file_location("main_pg", os.path.join(SCRIPT_DIR, "main_pg.py"))
        mod = importlib.util.module_from_spec(spec)
        # 注入 infos 兼容对象，替代命令行解析（Web 模式下无命令行参数）
        class _FakeInfos:
            label = db_info.get('name', 'DBCheck')
            sqltemplates = 'builtin'
            batch = False
        mod.infos = _FakeInfos()
        spec.loader.exec_module(mod)
        mod.infos = _FakeInfos()   # exec_module 后再赋一次，确保模块内引用有效

        emit(f"[{_ts()}] 🔍 创建报告模板...")
        ifile = mod.create_word_template(inspector_name)
        if not ifile:
            raise RuntimeError("Word 模板创建失败")

        dir_path = os.path.join(SCRIPT_DIR, "reports")
        os.makedirs(dir_path, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_name = f"PostgreSQL巡检报告_{db_info['name']}_{timestamp}.docx"
        ofile = os.path.join(dir_path, file_name)

        emit(f"[{_ts()}] 🔍 测试数据库连接 {db_info['ip']}:{db_info['port']}...")
        ok, ver = test_pg_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'], db_info.get('database', 'postgres'))
        if not ok:
            raise RuntimeError(f"数据库连接失败: {ver}")
        emit(f"[{_ts()}] ✅ 数据库连接成功: {ver[:80]}")

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host','ssh_port','ssh_user','ssh_password','ssh_key_file') if k in db_info}

        emit(f"[{_ts()}] 📊 开始执行巡检 SQL...")
        data = mod.getData(db_info['ip'], db_info['port'], db_info['user'], db_info['password'],
                           database=db_info.get('database', 'postgres'), ssh_info=ssh_info)
        if data is None or data.conn_db2 is None:
            raise RuntimeError("无法建立数据库连接，getData 返回 None")

        ret = data.checkdb('builtin')
        if not ret:
            raise RuntimeError("巡检执行失败（checkdb 返回空）")

        ret.update({"co_name": [{'CO_NAME': db_info['name']}]})
        ret.update({"port": [{'PORT': db_info['port']}]})
        ret.update({"ip": [{'IP': db_info['ip']}]})

        emit(f"[{_ts()}] 📝 正在渲染 Word 报告...")
        savedoc = mod.saveDoc(context=ret, ofile=ofile, ifile=ifile, inspector_name=inspector_name)
        success = savedoc.contextsave()

        if success:
            emit(f"[{_ts()}] ✅ 报告生成成功: {file_name}")
            task['status'] = 'done'
            task['report_file'] = ofile
            task['report_name'] = file_name
        else:
            raise RuntimeError("Word 报告渲染失败")

        try:
            if os.path.exists(ifile):
                os.remove(ifile)
        except Exception:
            pass

    except Exception as e:
        emit(f"[{_ts()}] ❌ 巡检失败: {e}")
        emit(traceback.format_exc())
        task['status'] = 'error'
        task['error'] = str(e)


def _ts():
    return datetime.now().strftime('%H:%M:%S')


# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/test_db', methods=['POST'])
def api_test_db():
    """测试数据库连接"""
    data = request.json
    db_type = data.get('db_type', 'mysql')
    host = data.get('host', 'localhost')
    port = data.get('port', 3306)
    user = data.get('user', 'root')
    password = data.get('password', '')
    database = data.get('database', 'postgres')

    if db_type == 'mysql':
        ok, msg = test_mysql_connection(host, port, user, password)
    else:
        ok, msg = test_pg_connection(host, port, user, password, database)

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
    """启动巡检任务"""
    data = request.json
    db_type = data.get('db_type', 'mysql')
    inspector_name = data.get('inspector_name', 'Jack') or 'Jack'

    db_info = {
        'name':        data.get('name', f'{db_type.upper()}_Server'),
        'ip':          data.get('host', 'localhost'),
        'port':        data.get('port', 3306 if db_type == 'mysql' else 5432),
        'user':        data.get('user', 'root'),
        'password':    data.get('password', ''),
        'database':    data.get('database', 'postgres'),
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
        'status':      'pending',
        'log':         [],
        'report_file': None,
        'report_name': None,
        'error':       None,
        'created_at':  time.time(),
        'last_update': time.time(),
    }

    target = run_mysql_task if db_type == 'mysql' else run_pg_task
    t = threading.Thread(target=target, args=(task_id, db_info, inspector_name), daemon=True)
    t.start()

    return jsonify({'ok': True, 'task_id': task_id})


@app.route('/api/task_status/<task_id>')
def api_task_status(task_id):
    """轮询任务状态与日志"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'ok': False, 'msg': '任务不存在'})

    offset = int(request.args.get('offset', 0))
    new_logs = task['log'][offset:]

    return jsonify({
        'ok':          True,
        'status':      task['status'],
        'log':         new_logs,
        'offset':      offset + len(new_logs),
        'report_name': task.get('report_name'),
        'error':       task.get('error'),
    })


@app.route('/api/download/<task_id>')
def api_download(task_id):
    """下载生成的报告"""
    task = tasks.get(task_id)
    if not task or not task.get('report_file'):
        return jsonify({'ok': False, 'msg': '报告不存在'}), 404

    report_file = task['report_file']
    if not os.path.exists(report_file):
        return jsonify({'ok': False, 'msg': '报告文件不存在'}), 404

    return send_file(
        report_file,
        as_attachment=True,
        download_name=task['report_name'],
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )


@app.route('/api/reports')
def api_reports():
    """列出 reports/ 目录下所有报告"""
    dir_path = os.path.join(SCRIPT_DIR, 'reports')
    if not os.path.exists(dir_path):
        return jsonify({'ok': True, 'files': []})

    files = []
    for f in sorted(os.listdir(dir_path), reverse=True):
        if f.endswith('.docx'):
            full = os.path.join(dir_path, f)
            files.append({
                'name': f,
                'size': os.path.getsize(full),
                'mtime': os.path.getmtime(full),
            })
    return jsonify({'ok': True, 'files': files[:50]})


@app.route('/api/download_file')
def api_download_file():
    """按文件名下载 reports 目录内的报告"""
    filename = request.args.get('name', '')
    if not filename or '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'ok': False, 'msg': '无效文件名'}), 400

    dir_path = os.path.join(SCRIPT_DIR, 'reports')
    full = os.path.join(dir_path, filename)
    if not os.path.exists(full):
        return jsonify({'ok': False, 'msg': '文件不存在'}), 404

    return send_file(
        full,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )


# ──────────────────────────────────────────────
# 启动
# ──────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("=" * 55)
    print("   数据库巡检工具 Web UI")
    print(f"   访问地址: http://localhost:{port}")
    print("=" * 55)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
