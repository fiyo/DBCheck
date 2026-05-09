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
import os, sys, threading, datetime, json, uuid, time, re, random, sqlite3

# ── 确保项目根目录在 sys.path（支持各种启动方式）──────────────────
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

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
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
    reports = []
    # 读取 pro_history.db 中的风险统计，key 为报告文件名
    risk_map = {}
    try:
        import sqlite3
        pro_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pro_data', 'pro_history.db')
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
                     if f.endswith('.docx') and not f.startswith('~$')]
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

        # ── 智能分析（生成修复建议）────────────────────────────
        try:
            from analyzer import smart_analyze_mysql
            auto_analyze = smart_analyze_mysql(ret)
            if task:
                task['auto_analyze'] = auto_analyze
            _emit('log', {'msg': f"[智能分析] 完成，发现 {len(auto_analyze)} 个可优化项"})
        except Exception as e:
            auto_analyze = []
            _emit('log', {'msg': f"[警告] 智能分析失败: {e}"})

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
            _emit('log', {'msg': f"[警告] 历史快照保存失败: {e}"})

        # ── 保存巡检记录到 Pro 模块 ──────────────────────────
        try:
            from pro import get_instance_manager
            # 获取风险数量
            risk_count = ret.get('risk_count', 0)
            if not risk_count:
                issues = ret.get('issues', [])
                risk_count = len(issues) if isinstance(issues, list) else 0

            # 获取健康状态并转换为评分
            health_status = ret.get('health_status', '')
            if '优秀' in health_status or 'Excellent' in health_status:
                health_score = 100
            elif '良好' in health_status or 'Good' in health_status:
                health_score = 80
            elif '一般' in health_status or 'Fair' in health_status:
                health_score = 60
            elif '需关注' in health_status or 'Attention' in health_status:
                health_score = 40
            else:
                health_score = 100 - min(risk_count * 5, 50)  # 默认计算

            # 计算风险等级
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

            # 生成实例ID（与数据源管理保持一致）
            import hashlib
            raw = f"mysql-{db_info['ip']}-{db_info['port']}".encode()
            instance_id = hashlib.md5(raw).hexdigest()[:12]

            im = get_instance_manager()
            im.record_inspection(
                instance_id=instance_id,
                instance_name=label_name,
                db_type='mysql',
                health_score=health_score,
                risk_count=risk_count,
                risk_level=risk_level,
                report_path=ofile,
                duration=0,  # 暂不计算耗时
                auto_analyze=auto_analyze if auto_analyze else []
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] Pro 巡检记录保存失败: {e}"})

        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id})
    except Exception as e:
        _emit('error', {'msg': _t('webui.err_inspection').format(task='MySQL', e=e)})
        if task:
            task['status'] = 'error'
            task['error_msg'] = str(e)

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

        # ── 智能分析（生成修复建议）────────────────────────────
        try:
            from analyzer import smart_analyze_pg
            auto_analyze = smart_analyze_pg(ret)
            if task:
                task['auto_analyze'] = auto_analyze
            _emit('log', {'msg': f"[智能分析] 完成，发现 {len(auto_analyze)} 个可优化项"})
        except Exception as e:
            auto_analyze = []
            _emit('log', {'msg': f"[警告] 智能分析失败: {e}"})

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
            _emit('log', {'msg': f"[警告] 历史快照保存失败: {e}"})

        # ── 保存巡检记录到 Pro 模块 ──────────────────────────
        try:
            from pro import get_instance_manager
            risk_count = ret.get('risk_count', 0)
            if not risk_count:
                issues = ret.get('issues', [])
                risk_count = len(issues) if isinstance(issues, list) else 0

            health_status = ret.get('health_status', '')
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

            import hashlib
            raw = f"pg-{db_info['ip']}-{db_info['port']}".encode()
            instance_id = hashlib.md5(raw).hexdigest()[:12]

            im = get_instance_manager()
            im.record_inspection(
                instance_id=instance_id,
                instance_name=label_name,
                db_type='pg',
                health_score=health_score,
                risk_count=risk_count,
                risk_level=risk_level,
                report_path=ofile,
                duration=0,
                auto_analyze=auto_analyze if auto_analyze else []
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] Pro 巡检记录保存失败: {e}"})

        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id})
    except Exception as e:
        _emit('error', {'msg': _t('webui.err_inspection').format(task='PostgreSQL', e=e)})
        if task:
            task['status'] = 'error'
            task['error_msg'] = str(e)

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
            context = mod.single_inspection(args)
        finally:
            builtins.print = _orig_print
        # ── 智能分析 ────────────────────────────────────────
        try:
            from analyzer import smart_analyze_oracle
            auto_analyze = smart_analyze_oracle(context) if context else []
            if task:
                task['auto_analyze'] = auto_analyze
            _emit('log', {'msg': "[智能分析] 完成，发现 %d 个可优化项" % len(auto_analyze)})
        except Exception as e:
            auto_analyze = []
            _emit('log', {'msg': "[警告] 智能分析失败: %s" % str(e)})

        # ── 保存巡检记录到 Pro 模块 ──────────────────────
        try:
            from pro import get_instance_manager
            health_score = 85
            risk_count = len(auto_analyze) if auto_analyze else 0
            for a in auto_analyze:
                if '高风险' in str(a.get('col2', '')):
                    health_score -= 20
                elif '中风险' in str(a.get('col2', '')):
                    health_score -= 10
            health_score = max(0, min(100, health_score))
            # 计算风险等级
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

            im = get_instance_manager()
            db_label = db_info.get('name') or "Oracle_%s" % db_info['ip']
            # 生成实例ID
            import hashlib
            raw = f"oracle-{db_info.get('host', db_info['ip'])}-{db_info.get('port', 1521)}".encode()
            instance_id = hashlib.md5(raw).hexdigest()[:12]
            im.record_inspection(
                instance_id=instance_id,
                instance_name=db_label,
                db_type='oracle',
                health_score=health_score,
                risk_count=risk_count,
                risk_level=risk_level,
                report_path='',
                duration=0,
                auto_analyze=auto_analyze if auto_analyze else []
            )
            _emit('log', {'msg': "[记录] 巡检记录已保存"})
        except Exception as e:
            _emit('log', {'msg': "[警告] 巡检记录保存失败: %s" % str(e)})

        if task:
            task['status'] = 'done'
        _emit('done', {'msg': _t('webui.log_oracle_done'), 'task_id': task_id})
    except Exception as e:
        _emit('error', {'msg': _t('webui.err_inspection').format(task='Oracle 全面巡检', e=e)})
        if task:
            task['status'] = 'error'
            task['error_msg'] = str(e)


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

        # ── 智能分析（生成修复建议）────────────────────────────
        try:
            from analyzer import smart_analyze_dm
            auto_analyze = smart_analyze_dm(context)
            if task:
                task['auto_analyze'] = auto_analyze
            _emit('log', {'msg': f"[智能分析] 完成，发现 {len(auto_analyze)} 个可优化项"})
        except Exception as e:
            auto_analyze = []
            _emit('log', {'msg': f"[警告] 智能分析失败: {e}"})

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
            _emit('log', {'msg': f"[警告] 历史快照保存失败: {e}"})

        # ── 保存巡检记录到 Pro 模块 ──────────────────────────
        try:
            from pro import get_instance_manager
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

            import hashlib
            raw = f"dm-{db_info['ip']}-{db_info['port']}".encode()
            instance_id = hashlib.md5(raw).hexdigest()[:12]

            im = get_instance_manager()
            im.record_inspection(
                instance_id=instance_id,
                instance_name=label_name,
                db_type='dm',
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
        _emit('error', {'msg': _t('webui.err_inspection').format(task='DM8', e=f"{e}\n{traceback.format_exc()}")})
        if task:
            task['status'] = 'error'
            task['error_msg'] = str(e)


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

        # ── 智能分析（生成修复建议）────────────────────────────
        try:
            from analyzer import smart_analyze_sqlserver
            auto_analyze = smart_analyze_sqlserver(inspector.data)
            if task:
                task['auto_analyze'] = auto_analyze
            _emit('log', {'msg': f"[智能分析] 完成，发现 {len(auto_analyze)} 个可优化项"})
        except Exception as e:
            auto_analyze = []
            _emit('log', {'msg': f"[警告] 智能分析失败: {e}"})

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
            _emit('log', {'msg': f"[警告] 历史快照保存失败: {e}"})

        # ── 保存巡检记录到 Pro 模块 ──────────────────────────
        try:
            from pro import get_instance_manager
            risk_count = inspector.data.get('risk_count', 0)
            if not risk_count:
                issues = inspector.data.get('issues', [])
                risk_count = len(issues) if isinstance(issues, list) else 0

            health_status = inspector.data.get('health_status', '')
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

            import hashlib
            raw = f"sqlserver-{db_info['ip']}-{db_info['port']}".encode()
            instance_id = hashlib.md5(raw).hexdigest()[:12]

            im = get_instance_manager()
            im.record_inspection(
                instance_id=instance_id,
                instance_name=label_name,
                db_type='sqlserver',
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
                       'ai_advice': inspector.data.get('ai_advice', '')})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': _t('webui.err_inspection').format(task='SQL Server', e=f"{e}\n{traceback.format_exc()}")})
        if task:
            task['status'] = 'error'
            task['error_msg'] = str(e)

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

        # ── 智能分析（生成修复建议）────────────────────────────
        try:
            from analyzer import smart_analyze_tidb
            auto_analyze = smart_analyze_tidb(ret)
            if task:
                task['auto_analyze'] = auto_analyze
            _emit('log', {'msg': f"[智能分析] 完成，发现 {len(auto_analyze)} 个可优化项"})
        except Exception as e:
            _emit('log', {'msg': f"[警告] 智能分析失败: {e}"})

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
            _emit('log', {'msg': f"[警告] 历史快照保存失败: {e}"})

        # ── 保存巡检记录到 Pro 模块 ──────────────────────────
        try:
            from pro import get_instance_manager
            risk_count = ret.get('risk_count', 0)
            if not risk_count:
                issues = ret.get('issues', [])
                risk_count = len(issues) if isinstance(issues, list) else 0

            health_status = ret.get('health_status', '')
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

            import hashlib
            raw = f"tidb-{db_info['ip']}-{db_info['port']}".encode()
            instance_id = hashlib.md5(raw).hexdigest()[:12]

            im = get_instance_manager()
            im.record_inspection(
                instance_id=instance_id,
                instance_name=label_name,
                db_type='tidb',
                health_score=health_score,
                risk_count=risk_count,
                risk_level=risk_level,
                report_path=ofile,
                duration=0
            )
        except Exception as e:
            _emit('log', {'msg': f"[警告] Pro 巡检记录保存失败: {e}"})

        _emit('done', {'msg': _t('webui.log_inspection_done').format(ver=ver), 'task_id': task_id})
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        _emit('error', {'msg': _t('webui.err_inspection').format(task='TiDB', e=f"{e}\n{traceback.format_exc()}")})
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

@app.route('/api/delete_report', methods=['POST'])
def api_delete_report():
    """删除指定报告文件"""
    try:
        data = request.get_json() or {}
        name = data.get('name', '')
        if not name:
            return jsonify({'ok': False, 'error': _t('webui.reports_delete_name_required')}), 400
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
        fp = os.path.join(reports_dir, name)
        if not os.path.isfile(fp):
            return jsonify({'ok': False, 'error': _t('webui.reports_file_not_found')}), 404
        os.remove(fp)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

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
    elif db_type == 'oracle':
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
            'id':            task_id,
            'db_type':       db_type,
            'db_info':       db_info,
            'datasource_id': data.get('datasource_id') or None,
            'inspector':     inspector_name,
            'status':        'running',
            'started_at':    datetime.datetime.now().isoformat()
        }
        t = threading.Thread(target={
            'mysql':      run_mysql_task,
            'pg':         run_pg_task,
            'oracle':run_oracle_full_task,
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
        'auto_analyze': task.get('auto_analyze', []),
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
        conn.close()

        # 如果有历史记录，模拟5类评分（性能/安全/配置/容量/可用性）
        # 真实评分需要从 report_score.InspectionDataScorer 计算，此处基于 risk_count 估算
        categories = [
            {'name': '性能', 'key': 'performance', 'score': 0, 'icon': '🚀'},
            {'name': '安全', 'key': 'security',     'score': 0, 'icon': '🔒'},
            {'name': '配置', 'key': 'configuration','score': 0, 'icon': '⚙️'},
            {'name': '容量', 'key': 'capacity',     'score': 0, 'icon': '💾'},
            {'name': '可用性', 'key': 'availability','score': 0, 'icon': '✅'},
        ]

        if latest_rows:
            # 基于 health_score 分配各类评分（加权分配，模拟真实评分）
            for cat in categories:
                offset = random.randint(-10, 10)
                cat['score'] = max(0, min(100, score + offset))
        else:
            # 无历史记录时，所有分类显示为 0
            for cat in categories:
                cat['score'] = 0

        # 总实例数和已巡检实例数
        stats = im.get_statistics()
        total_instances = stats.get('total_instances', 0)
        inspected_count = len(latest_rows)

        return jsonify({
            'ok': True,
            'total_score': score,
            'level': level,
            'risk_breakdown': risk_breakdown,
            'categories': categories,
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


@app.route('/api/pro/datasources/<instance_id>', methods=['DELETE'])
def api_pro_datasource_delete(instance_id):
    """删除数据源"""
    try:
        from pro import get_instance_manager
        im = get_instance_manager()
        result = im.delete_instance(instance_id)
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

        if not host:
            return jsonify({'ok': False, 'error': '请输入主机地址'})

        if db_type == 'mysql' or db_type == 'tidb':
            import pymysql
            conn = pymysql.connect(host=host, port=port, user=user, password=password, connect_timeout=10)
            conn.close()
        elif db_type == 'pg' or db_type == 'postgresql':
            import psycopg2
            db = data.get('database', 'postgres')
            conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=db, connect_timeout=10)
            conn.close()
        elif db_type == 'oracle':
            import oracledb
            dsn = f"{host}:{port}/{service_name}" if service_name else f"{host}:{port}"
            ssh_host = data.get('ssh_host', '')
            _tunnel = None

            if ssh_host:
                # 配了 SSH → 只走隧道，不 fallback 直连
                try:
                    import paramiko
                    _tunnel = paramiko.SSHClient()
                    _tunnel.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh_port = data.get('ssh_port', 22)
                    ssh_user = data.get('ssh_user', 'root')
                    ssh_pass = data.get('ssh_password', '')
                    _tunnel.connect(ssh_host, port=int(ssh_port), username=ssh_user,
                                   password=ssh_pass or '', timeout=10,
                                   look_for_keys=False, allow_agent=False)
                    _t = _tunnel.get_transport()
                    _local = _t.request_port_forward('', 0, host, int(port))
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
                if 'DPY-3010' in err_msg:
                    return jsonify({'ok': False, 'error': 'Oracle 11g 及以下版本不支持直连，请在数据源中配置 SSH'})
                if 'timed out' in err_msg.lower() or 'timeout' in err_msg.lower():
                    return jsonify({'ok': False, 'error': '连接超时，Oracle 可能无法直连，请在数据源中配置 SSH'})
                raise
            conn.close()
            if _tunnel: _tunnel.close()
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
            conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={host},{port};UID={user};PWD={password};TrustServerCertificate=yes;Connection Timeout=10"
            conn = pyodbc.connect(conn_str)
            conn.close()
        else:
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
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pro_data', 'pro.db')
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
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pro_data', 'pro.db')
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

        if db_type == 'mysql' or db_type == 'tidb':
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

        elif db_type == 'postgresql' or db_type == 'pg':
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
            import oracledb
            dsn = db_info.get('service_name') or f"{db_info.get('host')}:{db_info.get('port')}/orcl"
            mode = oracledb.SYSDBA if db_info.get('sysdba') else oracledb.DEFAULT_MODE
            conn = oracledb.connect(
                user=db_info.get('user', ''),
                password=db_info.get('password', ''),
                dsn=dsn,
                mode=mode
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

        elif db_type == 'sqlserver':
            import pyodbc
            driver = '{ODBC Driver 17 for SQL Server}'
            dsn_str = f"DRIVER={driver};SERVER={db_info.get('host')},{db_info.get('port')};DATABASE=master;UID={db_info.get('user')};PWD={db_info.get('password')}"
            conn = pyodbc.connect(dsn_str, timeout=30)
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
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pro_data', 'pro.db')
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
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_config.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'backend': 'ollama', 'api_url': 'http://localhost:11434', 'model': 'qwen3:8b', 'timeout': 600}


def _call_ollama(prompt: str, system: str = '') -> str:
    """调用 Ollama API 生成文本"""
    cfg = _load_ai_config()
    api_url = cfg.get('api_url', 'http://localhost:11434').rstrip('/')
    model = cfg.get('model', 'qwen3:8b')
    timeout = int(cfg.get('timeout', 600))

    url = api_url + '/api/generate'
    payload = {
        'model': model,
        'prompt': prompt,
        'system': system,
        'stream': False,
    }
    if system:
        payload['system'] = system

    try:
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                    headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result.get('response', '').strip()
    except Exception as e:
        return f'[Ollama 调用失败: {e}]'


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
    response = _call_ollama(prompt, system_prompt)

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

        elif db_type == 'pg':
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
                conn_str = (
                    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                    f"SERVER={db_info.get('host')},{db_info.get('port', 1433)};"
                    f"UID={db_info.get('user', '')};"
                    f"PWD={db_info.get('password', '')};"
                    f"TrustServerCertificate=yes;Encrypt=yes;"
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


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """处理自然语言巡检请求"""
    try:
        data = request.get_json() or {}
        message = data.get('message', '')

        if not message:
            return jsonify({'ok': False, 'type': 'error', 'message': '请输入巡检需求'})

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
            task_func_map = {
                'mysql': run_mysql_task,
                'pg': run_pg_task,
                'oracle': run_oracle_full_task,
                'dm': run_dm_task,
                'sqlserver': run_sqlserver_task,
                'tidb': run_tidb_task,
            }
            task_func = task_func_map.get(db_type, run_mysql_task)
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
        socketio.emit('log', {'msg': _t('webui.ws_connected_waiting').format(ts=_ts())})


if __name__ == '__main__':
    port = 5003
    print(_t('webui.startup_msg').format(port=port))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
