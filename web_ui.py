# coding: utf-8
#
# Copyright (c) 2024 DBCheck Contributors
# sdfiyon@gmail.com
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

# ── 延迟导入调度器和通知器（避免循环依赖）─────────────────────
_scheduler = None

def _get_scheduler():
    global _scheduler
    if _scheduler is None:
        from scheduler import get_scheduler
        _scheduler = get_scheduler()
    return _scheduler

# async_mode='threading' 最稳定，跨平台/打包零兼容问题，
# 满足 DBCheck Web UI 低并发使用场景（单用户/少量连接）。
# 不依赖 gevent/eventlet，避免打包后版本冲突。
socketio = SocketIO(cors_allowed_origins='*', async_mode='threading')

# ── 本地模块 ──────────────────────────────────────────────
try:
    import main_mysql, main_pg, main_dm, main_oracle_full, main_sqlserver, main_tidb
except ImportError:
    main_mysql = main_pg = main_dm = main_oracle_full = main_sqlserver = main_tidb = None

app = Flask(__name__, template_folder='web_templates', static_folder='web_templates', static_url_path='/')
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
        ext_name = _t('webui.mysql_report_filename').format(ip=db_info['ip'], name=label_name, ts=timestamp)
        file_name = ext_name + '.docx'
        ofile = os.path.join(reports_dir, file_name)

        # ── 脱敏处理（如用户开启了脱敏导出）───────────────────
        if db_info.get('desensitize'):
            from desensitize import apply_desensitization
            ret = apply_desensitization(ret)

        savedoc = mod.saveDoc(context=ret, ofile=ofile, ifile=ifile, inspector_name=inspector_name)
        if not savedoc.contextsave():
            raise RuntimeError(_t('webui.err_report_generate'))
        _emit('log', {'msg': _t('webui.log_report_ok').format(fname=file_name)})

        if task:
            task['status'] = 'done'
            task['report_file'] = ofile
            task['report_name'] = file_name

        # ── 保存历史记录用于趋势分析 ──────────────────────────
        try:
            from analyzer import HistoryManager
            hm = HistoryManager(os.path.dirname(os.path.abspath(__file__)))
            hm.save_snapshot(
                db_type='mysql',
                host=db_info['ip'],
                port=db_info['port'],
                label=db_info.get('name', db_info['ip']),
                context=ret
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] 历史记录保存失败: {e}"})

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
        ext_name = _t('webui.pg_report_filename').format(ip=db_info['ip'], name=label_name, ts=timestamp)
        file_name = ext_name + '.docx'
        ofile = os.path.join(reports_dir, file_name)

        # ── 脱敏处理（如用户开启了脱敏导出）───────────────────
        if db_info.get('desensitize'):
            from desensitize import apply_desensitization
            ret = apply_desensitization(ret)

        savedoc = mod.saveDoc(context=ret, ofile=ofile, ifile=ifile, inspector_name=inspector_name)
        if not savedoc.contextsave():
            raise RuntimeError(_t('webui.err_report_generate'))
        _emit('log', {'msg': _t('webui.log_report_ok').format(fname=file_name)})

        if task:
            task['status'] = 'done'
            task['report_file'] = ofile
            task['report_name'] = file_name

        # ── 保存历史记录用于趋势分析 ──────────────────────────
        try:
            from analyzer import HistoryManager
            hm = HistoryManager(os.path.dirname(os.path.abspath(__file__)))
            hm.save_snapshot(
                db_type='pg',
                host=db_info['ip'],
                port=db_info['port'],
                label=db_info.get('name', db_info['ip']),
                context=ret
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] 历史记录保存失败: {e}"})

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
        # 如果都没指定，默认用 orcl 作为 SID
        if not args.sid and not args.servicename:
            args.sid = db_info.get('database', 'orcl')
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
        # 脱敏导出
        args.desensitize = bool(db_info.get('desensitize', False))

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
        label_name = db_info.get('name', 'DM8')
        ofile = os.path.join(reports_dir, _t('webui.dm_report_filename').format(ip=db_info['ip'], name=label_name, ts=_dt.now().strftime('%Y%m%d_%H%M%S')) + '.docx')
        ifile = mod.create_word_template(inspector_name)
        # ── 脱敏处理（如用户开启了脱敏导出）───────────────────
        if db_info.get('desensitize'):
            from desensitize import apply_desensitization
            context = apply_desensitization(context)

        saver = mod.saveDoc(context, ofile, ifile, inspector_name, H=data.H, P=data.P)
        if not saver.contextsave():
            raise RuntimeError(_t('webui.err_report_failed'))
        _emit('log', {'msg': _t('webui.log_report_done').format(ts=_ts(), fname=os.path.basename(ofile))})

        if task:
            task['status'] = 'done'
            task['report_name'] = os.path.basename(ofile)
            task['report_file'] = ofile

        # ── 保存历史记录用于趋势分析 ──────────────────────────
        try:
            from analyzer import HistoryManager
            hm = HistoryManager(os.path.dirname(os.path.abspath(__file__)))
            hm.save_snapshot(
                db_type='dm',
                host=db_info['ip'],
                port=db_info['port'],
                label=db_info.get('name', db_info['ip']),
                context=context
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] 历史记录保存失败: {e}"})

        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id,
                       'ai_advice': context.get('ai_advice', '')})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': _t('webui.err_inspection').format(task='DM8', e=f"{e}\n{traceback.format_exc()}")})
        if task:
            task['status'] = 'error'


def run_sqlserver_task(task_id, db_info, inspector_name):
    """SQL Server Web UI 巡检任务"""
    emit = socketio.emit
    task = tasks.get(task_id)
    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': _t('webui.log_sqlserver_start').format(ts=_ts())})

    if not main_sqlserver:
        _emit('error', {'msg': _t('webui.err_sqlserver_module')})
        return

    try:
        import main_sqlserver as mod
        _emit('log', {'msg': _t('webui.log_connecting').format(ts=_ts(), host=db_info['ip'], port=db_info['port'])})
        ok, ver = test_sqlserver_connection(
            db_info['ip'],
            db_info['port'],
            db_info['user'],
            db_info['password'],
            db_info.get('database', 'master')
        )
        if not ok:
            raise RuntimeError(_t('webui.err_db_connect').format(ver=ver))
        _emit('log', {'msg': _t('webui.log_connected').format(ts=_ts(), ver=ver)})

        ssh_info = {}
        if db_info.get('ssh_host'):
            ssh_info = {k: db_info[k] for k in ('ssh_host', 'ssh_port', 'ssh_user', 'ssh_password', 'ssh_key_file') if k in db_info}

        _emit('log', {'msg': _t('webui.log_executing_sql').format(ts=_ts())})
        # 创建 DBCheckSQLServer 实例
        inspector = mod.DBCheckSQLServer(
            host=db_info['ip'],
            port=int(db_info['port']),
            user=db_info['user'],
            password=db_info['password'],
            database=db_info.get('database'),
            label=db_info.get('name') or db_info.get('ip', 'SQLServer'),
            inspector=inspector_name,
            ssh_host=db_info.get('ssh_host'),
            ssh_user=db_info.get('ssh_user'),
            ssh_password=db_info.get('ssh_password'),
            ssh_key_file=db_info.get('ssh_key_file')
        )

        # ── stdout 重定向：捕获 checkdb() 内部的 AI 诊断等 print 输出 ───
        import builtins as _bi
        _orig_sqlserver_print = _bi.print
        def _web_sqlserver_print(*_a, **_kw):
            _sep = _kw.get('sep', ' ')
            _msg = _sep.join(str(x) for x in _a)
            _msg_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', _msg)
            if _msg_clean.strip():
                _emit('log', {'msg': _msg_clean})
            _orig_sqlserver_print(*_a, **_kw)
        _bi.print = _web_sqlserver_print
        try:
            ret = inspector.checkdb()
        finally:
            _bi.print = _orig_sqlserver_print

        if not ret:
            raise RuntimeError(_t('webui.err_checkdb_false'))

        # AI 诊断结果
        if inspector.data.get('ai_advice'):
            _emit('log', {'msg': _t('webui.log_ai_done').format(ts=_ts())})
        if task:
            task['ai_advice'] = inspector.data.get('ai_advice', '')

        # 生成报告文件
        _emit('log', {'msg': _t('webui.log_generating_report').format(ts=_ts())})
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        _dt = __import__('datetime').datetime
        label_name = db_info.get('name', 'SQLServer')
        ofile = os.path.join(reports_dir, _t('webui.sqlserver_report_filename').format(ip=db_info['ip'], name=label_name, ts=_dt.now().strftime('%Y%m%d_%H%M%S')) + '.docx')

        if inspector.report_path and os.path.exists(inspector.report_path):
            # checkdb 已生成报告，直接使用
            ofile = inspector.report_path
        else:
            # 手动生成报告
            generator = mod.WordTemplateGeneratorSQLServer(inspector.data)
            generator.generate(ofile)

        _emit('log', {'msg': _t('webui.log_report_done').format(ts=_ts(), fname=os.path.basename(ofile))})

        if task:
            task['status'] = 'done'
            task['report_name'] = os.path.basename(ofile)
            task['report_file'] = ofile

        # ── 保存历史记录用于趋势分析 ──────────────────────────
        try:
            from analyzer import HistoryManager
            hm = HistoryManager(os.path.dirname(os.path.abspath(__file__)))
            hm.save_snapshot(
                db_type='sqlserver',
                host=db_info['ip'],
                port=db_info['port'],
                label=db_info.get('name', db_info['ip']),
                context=inspector.data
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] 历史记录保存失败: {e}"})

        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id,
                       'ai_advice': inspector.data.get('ai_advice', '')})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': _t('webui.err_inspection').format(task='SQL Server', e=f"{e}\n{traceback.format_exc()}")})
        if task:
            task['status'] = 'error'

# ── TiDB 巡检任务 ──────────────────────────────────────────
def run_tidb_task(task_id, db_info, inspector_name):
    """TiDB 巡检 Web UI 任务"""
    emit = socketio.emit
    task = tasks.get(task_id)
    def _emit(event, data):
        msg = data.get('msg', '')
        if msg and task is not None:
            task.setdefault('log', []).append(msg)
        emit(event, data, room=task_id)

    _emit('log', {'msg': _t('webui.log_tidb_start').format(ts=_ts())})

    if not main_tidb:
        _emit('error', {'msg': _t('webui.err_tidb_module')})
        return

    try:
        import main_tidb as mod
        _emit('log', {'msg': _t('webui.log_connecting').format(ts=_ts(), host=db_info['ip'], port=db_info['port'])})
        ok, ver = test_tidb_connection(db_info['ip'], db_info['port'], db_info['user'], db_info['password'], db_info.get('database'))
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

        # ── stdout 重定向：捕获 checkdb() 内部的 AI 诊断 print 输出 ──
        import builtins as _bi
        _orig_tidb_print = _bi.print
        def _web_tidb_print(*_a, **_kw):
            _sep = _kw.get('sep', ' ')
            _msg = _sep.join(str(x) for x in _a)
            _msg_clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', _msg)
            if _msg_clean.strip():
                _emit('log', {'msg': _msg_clean})
            _orig_tidb_print(*_a, **_kw)
        _bi.print = _web_tidb_print
        try:
            ret = data.checkdb('builtin')
        finally:
            _bi.print = _orig_tidb_print

        if not ret:
            raise RuntimeError(_t('webui.err_checkdb_false'))

        # ── 生成 Word 报告 ──────────────────────────────────────────
        _emit('log', {'msg': _t('webui.log_generating_report').format(ts=_ts())})
        label_name = db_info.get('name', db_info.get('ip', 'unknown'))
        ret.update({"co_name": [{'CO_NAME': label_name}]})
        ret.update({"port": [{'PORT': db_info['port']}]})
        ret.update({"ip": [{'IP': db_info['ip']}]})

        inspector_name = db_info.get('inspector_name') or 'Jack'
        ifile = mod.create_word_template_tidb(inspector_name)
        if not ifile:
            raise RuntimeError(_t('webui.err_template_create'))

        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        ext_name = _t('webui.tidb_report_filename').format(ip=db_info['ip'], name=label_name, ts=timestamp)
        file_name = ext_name + '.docx'
        ofile = os.path.join(reports_dir, file_name)

        # ── 脱敏处理（如用户开启了脱敏导出）────────────────────────
        if db_info.get('desensitize'):
            from desensitize import apply_desensitization
            ret = apply_desensitization(ret)

        savedoc = mod.saveDoc(context=ret, ofile=ofile, ifile=ifile, inspector_name=inspector_name)
        if not savedoc.contextsave():
            raise RuntimeError(_t('webui.err_report_generate'))
        _emit('log', {'msg': _t('webui.log_report_ok').format(fname=file_name)})

        if task:
            task['status'] = 'done'
            task['report_file'] = ofile
            task['report_name'] = file_name

        # ── 保存历史记录用于趋势分析 ──────────────────────────
        try:
            from analyzer import HistoryManager
            hm = HistoryManager(os.path.dirname(os.path.abspath(__file__)))
            hm.save_snapshot(
                db_type='tidb',
                host=db_info['ip'],
                port=db_info['port'],
                label=db_info.get('name', db_info['ip']),
                context=ret
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] 历史记录保存失败: {e}"})

        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': _t('webui.err_inspection').format(task='TiDB', e=f"{e}\n{traceback.format_exc()}")})
        if task:
            task['status'] = 'error'


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
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
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
        elif db_type == 'pg':
            import psycopg2
            conn = psycopg2.connect(
                host=db_info['host'], port=int(db_info['port']),
                user=db_info['user'], password=db_info['password'],
                database=db_info.get('database', 'postgres')
            )
            db_label = 'PostgreSQL'
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
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
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
        elif db_type == 'pg':
            import psycopg2
            conn = psycopg2.connect(
                host=db_info['host'], port=int(db_info['port']),
                user=db_info['user'], password=db_info['password'],
                database=db_info.get('database', 'postgres')
            )
            db_label = 'PostgreSQL'
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


def test_sqlserver_connection(host, port, user, password, database='master'):
    """测试 SQL Server 连接"""
    try:
        import pyodbc
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={host},{port};"
            f"UID={user};"
            f"PWD={password};"
            f"TrustServerCertificate=yes;"
            f"Encrypt=yes;"
        )
        if database:
            conn_str += f"Database={database};"
        conn = pyodbc.connect(conn_str, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT @@VERSION")
        ver = cur.fetchone()[0]
        ver = ver.split('\n')[0] if ver else 'Unknown'
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
    # 检测 Pro 模块是否可用
    pro_available = False
    try:
        from pro import get_rule_engine
        get_rule_engine()
        pro_available = True
    except Exception:
        pass
    return render_template('index.html', version=__version__, lang=lang, i18n_data=i18n_data,
                           pro_available=pro_available)


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
        raw_instances = hm.list_instances()
        instances = []
        for inst in raw_instances:
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
    elif db_type == 'sqlserver':
        ok, msg = test_sqlserver_connection(data['host'], data['port'], data['user'], data['password'], data.get('database', 'master'))
    elif db_type == 'tidb':
        ok, msg = test_tidb_connection(data['host'], data['port'], data['user'], data['password'], data.get('database'))
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
                'password':  instance.get('password', ''),
                'database':  'master' if db_type == 'sqlserver' else ('DAMENG' if db_type == 'dm' else (instance.get('database') or ('' if db_type == 'tidb' else 'postgres'))),
                'service_name': instance.get('service_name', None),
                'name':      instance.get('name', ''),
                'desensitize': bool(data.get('desensitize', False)),
            }
        else:
            # 原有逻辑：使用手动输入的连接信息
            db_info = {
                'ip':        data.get('host', ''),
                'port':      int(data.get('port', 0) or 0),
                'user':      data.get('user', ''),
                'password':  data.get('password', ''),
                'database':  'master' if db_type == 'sqlserver' else ('DAMENG' if db_type == 'dm' else (data.get('database') or ('' if db_type == 'tidb' else 'postgres'))),
                'service_name': data.get('service_name', None),
                'sid':       data.get('sid', None),
                'output_dir': data.get('output_dir', None),
                'zip':       data.get('zip', False),
                'name':      data.get('name', ''),
                'desensitize': bool(data.get('desensitize', False)),
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
            'mysql':      run_mysql_task,
            'pg':         run_pg_task,
            'oracle_full':run_oracle_full_task,
            'dm':         run_dm_task,
            'sqlserver':  run_sqlserver_task,
            'tidb':       run_tidb_task,
        }.get(db_type, run_mysql_task), args=(task_id, db_info, inspector_name))
        t.daemon = True
        t.start()
        return jsonify({'ok': True, 'task_id': task_id})
    except Exception as e:
        import traceback, sys
        traceback.print_exc(file=sys.stdout)
        return jsonify({'ok': False, 'msg': repr(e)})


@app.route('/api/start_config_baseline', methods=['POST'])
def api_start_config_baseline():
    """启动配置基线检查任务"""
    try:
        data = request.json
        db_type = data.get('db_type', 'mysql')
        if db_type not in ('mysql', 'pg'):
            return jsonify({'ok': False, 'msg': 'Only MySQL and PostgreSQL are supported'})

        db_info = {
            'host': data.get('host', ''),
            'port': int(data.get('port', 0) or (3306 if db_type == 'mysql' else 5432)),
            'user': data.get('user', ''),
            'password': data.get('password', ''),
            'database': data.get('database') or ('postgres' if db_type == 'pg' else ''),
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


@app.route('/api/start_index_health', methods=['POST'])
def api_start_index_health():
    """启动索引健康分析任务"""
    try:
        data = request.json
        db_type = data.get('db_type', 'mysql')
        if db_type not in ('mysql', 'pg'):
            return jsonify({'ok': False, 'msg': 'Only MySQL and PostgreSQL are supported'})

        db_info = {
            'host': data.get('host', ''),
            'port': int(data.get('port', 0) or (3306 if db_type == 'mysql' else 5432)),
            'user': data.get('user', ''),
            'password': data.get('password', ''),
            'database': data.get('database') or ('postgres' if db_type == 'pg' else ''),
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

# ══════════════════════════════════════════════════════════════
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

        # 如果指定了数据源，只保存 datasource_id（密码在执行时解密）
        datasource_id = data.get('datasource_id')
        if datasource_id:
            job_cfg = {
                'id': job_id,
                'name': data.get('name', '定时巡检'),
                'inspector_name': data.get('inspector_name', 'Jack'),
                'notify_on_done': bool(data.get('notify_on_done', True)),
                'cron': cron,
                'enabled': True,
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
    """测试邮件发送"""
    try:
        data = request.json or {}
        from notifier import EmailNotifier
        cfg = data.get('email', {})
        notifier = EmailNotifier(cfg)
        ok, msg = notifier.test_connection()
        return jsonify({'ok': ok, 'msg': msg})
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
    """延迟导入并初始化 RAGManager"""
    try:
        from rag.manager import RAGManager
        return RAGManager()
    except Exception as e:
        return None

@app.route('/api/rag/documents', methods=['GET'])
def api_rag_list_documents():
    mgr = _get_rag_manager()
    if mgr is None:
        return jsonify({'ok': False, 'error': 'RAG 模块未加载，请检查 Ollama 是否运行'})
    try:
        docs = mgr.list_documents()
        return jsonify({'ok': True, 'documents': docs})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/rag/documents', methods=['POST'])
def api_rag_upload_document():
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
        ok, message = mgr.add_document(tmp.name, db_type, title)
        os.unlink(tmp.name)
        if ok:
            return jsonify({'ok': True, 'message': message})
        else:
            return jsonify({'ok': False, 'error': message})
    except Exception as e:
        import traceback; traceback.print_exc()
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

@app.route('/api/rag/ollama-status', methods=['GET'])
def api_rag_ollama_status():
    try:
        from rag.embeddings import OllamaEmbedding
        emb = OllamaEmbedding()
        # 简单测试：向量化一个测试字符串
        vec = emb.embed_text('test')
        if vec and len(vec) > 0:
            return jsonify({'ok': True, 'model': emb.model, 'dim': len(vec)})
        return jsonify({'ok': False, 'error': '返回向量为空'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


# ── Pro 专业版 API ──────────────────────────────────────────
@app.route('/api/pro/status', methods=['GET'])
def api_pro_status():
    """获取 Pro 版本状态"""
    try:
        from pro import get_license_manager, is_pro, get_edition
        from pro import get_instance_manager
        import pro.version as pro_version

        lm = get_license_manager()
        im = get_instance_manager()

        license_info = lm.verify()
        stats = im.get_statistics()

        return jsonify({
            'ok': True,
            'is_pro': is_pro(),
            'edition': get_edition(),
            'version': pro_version.__version__,
            'release_date': getattr(pro_version, '__release_date__', ''),
            'license': {
                'valid': license_info.get('valid', False),
                'type': license_info.get('type', 'community'),
                'expires': license_info.get('expires', ''),
                'max_instances': license_info.get('max_instances', 0),
                'features': license_info.get('features', ['basic']),
            },
            'instances': stats,
        })
    except ImportError:
        return jsonify({
            'ok': True,
            'is_pro': False,
            'edition': 'community',
            'version': 'community',
            'license': {'valid': False, 'type': 'community', 'features': ['basic']},
            'instances': {'total_instances': 0},
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/license/activate', methods=['POST'])
def api_pro_license_activate():
    """激活许可证"""
    try:
        from pro import get_license_manager
        data = request.get_json()
        license_key = data.get('license_key', '')

        if not license_key:
            return jsonify({'ok': False, 'error': '请输入许可证密钥'})

        lm = get_license_manager()
        result = lm.activate(license_key)

        if result.get('success'):
            return jsonify({'ok': True, 'message': '许可证激活成功'})
        else:
            return jsonify({'ok': False, 'error': result.get('message', '激活失败')})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/license/deactivate', methods=['POST'])
def api_pro_license_deactivate():
    """注销许可证"""
    try:
        from pro import get_license_manager
        lm = get_license_manager()
        result = lm.deactivate()
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

        return jsonify({'ok': True, 'instance': inst})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500



        # 根据数据库类型测试连接
        test_funcs = {
            'mysql': test_mysql_connection,
            'tidb': test_tidb_connection,
            'postgresql': test_pg_connection,
            'oracle': test_oracle_connection,
            'dm': test_dm_connection,
            'sqlserver': test_sqlserver_connection,
        }

        test_func = test_funcs.get(instance.db_type)
        if not test_func:
            return jsonify({'ok': False, 'error': f'不支持的数据库类型: {instance.db_type}'})

        if instance.db_type == 'oracle':
            ok, msg = test_func(instance.host, instance.port, instance.user, instance.password, instance.service_name)
        elif instance.db_type == 'sqlserver':
            ok, msg = test_func(instance.host, instance.port, instance.user, instance.password)
        elif instance.db_type == 'dm':
            ok, msg = test_func(instance.host, instance.port, instance.user, instance.password)
        else:
            ok, msg = test_func(instance.host, instance.port, instance.user, instance.password)

        return jsonify({'ok': ok, 'message': msg})
    except ImportError as e:
        return jsonify({'ok': False, 'error': f'Pro 模块未安装: {str(e)}'})
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/groups/<path:group_name>', methods=['DELETE'])
def api_pro_delete_group(group_name):
    """删除分组"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        result = im.delete_group(group_name)
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
        return jsonify({'ok': True, 'datasource': inst})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
            tags=data.get('tags', []),
            group=data.get('group', 'default'),
            description=data.get('description', ''),
        )
        im = get_instance_manager()
        result = im.add_instance(inst)
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>', methods=['DELETE'])
def api_pro_datasource_delete(instance_id):
    """删除数据源"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        result = im.delete_instance(instance_id)
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/<instance_id>/test', methods=['POST'])
def api_pro_datasource_test(instance_id):
    """测试数据源连接"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        result = im.test_connection(instance_id)
        return jsonify(result)
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pro/datasources/export', methods=['GET'])
def api_pro_datasources_export():
    """导出数据源 CSV"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        csv_content = im.export_csv()
        return jsonify({'ok': True, 'csv': csv_content})
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
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
    except ImportError:
        return jsonify({'ok': False, 'error': 'Pro 模块未安装'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500



if __name__ == '__main__':
    port = 5003
    print(_t('webui.startup_msg').format(port=port))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
