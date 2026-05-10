# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck REST API v1

提供 CI/CD、监控系统等外部调用接口，通过 API Key 认证。

安全设计：
  - X-API-Key 请求头认证
  - 自动从 .env 加载 API_KEY
  - 空密钥拒绝所有请求（默认安全）
  - 支持同步/异步两种模式
  - 结构化 JSON 输出（风险分数、检查项、修复建议）
"""

import os
import json
import uuid
import time
import hashlib
import secrets
import threading
import traceback
import sqlite3
from datetime import datetime
from flask import Blueprint, request, jsonify

# ── API 密钥管理 ─────────────────────────────────────────────


# ── Blueprint ─────────────────────────────────────────────────

api_v1 = Blueprint('api_v1', __name__, url_prefix='/api/v1')

# 任务存储（内存，重启丢失）
_inspect_tasks = {}  # task_id -> dict
_inspect_lock = threading.Lock()


# ── API Key 存储（SQLite）────────────────────────────────────

def _get_key_db():
    """获取 key 数据库连接"""
    db_path = os.path.join(os.path.dirname(__file__), 'pro_data', 'api_keys.db')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS api_keys (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        key_hash TEXT NOT NULL UNIQUE,
        key_prefix TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        is_active INTEGER DEFAULT 1
    )''')
    conn.commit()
    return conn


def _hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def _generate_key():
    return 'dk-' + secrets.token_hex(24)  # dk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx


def _verify_api_key(req_key):
    """验证 API Key，返回 (ok, error_response)"""
    if not req_key:
        return False, jsonify({
            'ok': False, 'error': '缺少 API Key，请在 X-API-Key 请求头中提供',
            'error_code': 'MISSING_API_KEY',
        }), 401

    conn = _get_key_db()
    try:
        row = conn.execute(
            'SELECT * FROM api_keys WHERE key_hash=? AND is_active=1', (_hash_key(req_key),)
        ).fetchone()
        if row:
            conn.execute('UPDATE api_keys SET last_used_at=? WHERE id=?',
                        (datetime.now().isoformat(), row['id']))
            conn.commit()
            return True, None
        return False, jsonify({
            'ok': False, 'error': 'API Key 无效或已禁用',
            'error_code': 'INVALID_API_KEY',
        }), 401
    finally:
        conn.close()


# ── 管理员令牌（Web UI 管理用）─────────────────────────────────

_ADMIN_TOKEN = secrets.token_hex(32)
print(f"\n  🔐 API Key 管理令牌: {_ADMIN_TOKEN}\n")
print("  Web UI 中已自动注入，无需手动配置。\n")


def _require_admin():
    """Web UI 管理员验证"""
    token = request.headers.get('X-Admin-Token', '')
    if token != _ADMIN_TOKEN:
        return jsonify({'ok': False, 'error': '无管理权限'}), 403
    return None


def _require_api_key():
    """验证 API Key（从 SQLite 数据库查询）"""
    req_key = request.headers.get('X-API-Key', '') or request.headers.get('Authorization', '').replace('Bearer ', '')
    ok, err = _verify_api_key(req_key)
    return err if not ok else None


# ── 健康检查 ──────────────────────────────────────────────────

@api_v1.route('/health', methods=['GET'])
def api_v1_health():
    """健康检查（不需要 API Key）"""
    return jsonify({
        'ok': True,
        'status': 'healthy',
        'version': _get_version(),
        'timestamp': datetime.now().isoformat(),
        'active_tasks': len(_inspect_tasks),
    })


def _get_version():
    try:
        with open(os.path.join(os.path.dirname(__file__), 'version.py'), 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('__version__'):
                    return line.split('=')[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return 'unknown'


# ── 触发巡检 ──────────────────────────────────────────────────

@api_v1.route('/inspect', methods=['POST'])
def api_v1_inspect():
    """
    触发数据库巡检

    请求头：
        X-API-Key: your-api-key

    请求体：
        {
            "db_type": "mysql|pg|oracle|dm|sqlserver|tidb",
            "host": "192.168.1.100",
            "port": 3306,
            "user": "root",
            "password": "your_password",
            "database": "testdb",           // PG/TiDB 需要，可选
            "service_name": "ORCL",         // Oracle 需要，可选
            "sysdba": false,                // Oracle SYSDBA 模式，可选
            "inspector": "CI/CD",           // 巡检人标识，可选
            "mode": "sync",                 // sync=同步等待结果，async=返回task_id
            "timeout": 300,                 // 同步模式超时（秒），默认 300
            "ssh": {                        // SSH 跳板，可选
                "host": "10.0.0.1",
                "port": 22,
                "user": "oracle",
                "password": "ssh_password"
            }
        }

    响应（异步模式）：
        {
            "ok": true,
            "task_id": "uuid",
            "status": "running",
            "message": "巡检已触发"
        }

    响应（同步模式）：
        {
            "ok": true,
            "task_id": "uuid",
            "status": "completed",          // completed | failed
            "duration_seconds": 45.2,
            "result": {
                "db_type": "mysql",
                "host": "192.168.1.100:3306",
                "version": "8.0.35",
                "total_score": 85,
                "risk_level": "medium",
                "risk_breakdown": {"low": 5, "medium": 3, "high": 1, "critical": 0},
                "categories": [
                    {"name": "基础信息", "score": 90, "status": "pass"},
                    ...
                ],
                "findings": [
                    {
                        "category": "性能",
                        "item": "慢查询数量",
                        "value": "15",
                        "threshold": "5",
                        "severity": "high",
                        "suggestion": "建议分析慢查询并优化索引"
                    },
                    ...
                ],
                "report_path": "/reports/report_xxx.docx"
            }
        }
    """
    auth_err = _require_api_key()
    if auth_err:
        return auth_err

    try:
        body = request.get_json()
        if not body:
            return jsonify({'ok': False, 'error': '请求体为空', 'error_code': 'EMPTY_BODY'}), 400

        # 参数校验
        db_type = body.get('db_type', '').strip()
        host = body.get('host', '').strip()
        if not db_type or not host:
            return jsonify({
                'ok': False,
                'error': '缺少必填参数 db_type 和 host',
                'error_code': 'MISSING_PARAMS',
            }), 400

        valid_types = ['mysql', 'pg', 'postgresql', 'oracle', 'dm', 'sqlserver', 'tidb']
        if db_type == 'postgresql':
            db_type = 'pg'
        if db_type not in valid_types:
            return jsonify({
                'ok': False,
                'error': f'不支持的数据库类型: {db_type}',
                'error_code': 'INVALID_DB_TYPE',
                'valid_types': valid_types,
            }), 400

        port = int(body.get('port', _default_port(db_type)))
        user = body.get('user', _default_user(db_type))
        password = body.get('password', '')
        inspector = body.get('inspector', 'api-v1')
        mode = body.get('mode', 'sync')
        timeout = int(body.get('timeout', 300))
        ssh = body.get('ssh')

        task_id = str(uuid.uuid4())[:8]
        task_info = {
            'task_id': task_id,
            'db_type': db_type,
            'host': host,
            'port': port,
            'user': user,
            'inspector': inspector,
            'status': 'running',
            'created_at': datetime.now().isoformat(),
            'result': None,
            'error': None,
        }
        with _inspect_lock:
            _inspect_tasks[task_id] = task_info

        # 提交线程执行巡检
        thread = threading.Thread(
            target=_run_inspect_thread,
            args=(task_id, db_type, host, port, user, password, inspector, body, ssh),
            daemon=True,
        )
        thread.start()

        if mode == 'async':
            return jsonify({
                'ok': True,
                'task_id': task_id,
                'status': 'running',
                'message': '巡检已触发',
            })

        # 同步模式：轮询等待结果
        deadline = time.time() + timeout
        while time.time() < deadline:
            with _inspect_lock:
                task = _inspect_tasks.get(task_id, {})
                status = task.get('status', 'running')
                if status in ('completed', 'failed'):
                    resp = {
                        'ok': status == 'completed',
                        'task_id': task_id,
                        'status': status,
                        'duration_seconds': round(time.time() - _parse_iso(task.get('created_at', '')), 1),
                    }
                    if task.get('error'):
                        resp['error'] = task['error']
                    if task.get('result'):
                        resp['result'] = task['result']
                    return jsonify(resp)
            time.sleep(0.5)

        # 超时
        return jsonify({
            'ok': False,
            'task_id': task_id,
            'status': 'timeout',
            'error': f'巡检超时（{timeout}秒），请用 task_id 查询结果',
            'error_code': 'TIMEOUT',
        }), 202

    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e), 'error_code': 'INTERNAL_ERROR'}), 500


def _parse_iso(iso_str):
    try:
        return datetime.fromisoformat(iso_str).timestamp()
    except Exception:
        return time.time()


def _default_port(db_type):
    return {'mysql': 3306, 'pg': 5432, 'oracle': 1521, 'dm': 5236, 'sqlserver': 1433, 'tidb': 4000}.get(db_type, 3306)


def _default_user(db_type):
    return {'mysql': 'root', 'pg': 'postgres', 'oracle': 'system', 'dm': 'SYSDBA', 'sqlserver': 'sa', 'tidb': 'root'}.get(db_type, 'root')


# ── 查询任务状态 ──────────────────────────────────────────────

@api_v1.route('/inspect/<task_id>', methods=['GET'])
def api_v1_inspect_result(task_id):
    """查询巡检任务结果"""
    auth_err = _require_api_key()
    if auth_err:
        return auth_err

    with _inspect_lock:
        task = _inspect_tasks.get(task_id)
        if not task:
            return jsonify({'ok': False, 'error': '任务不存在', 'error_code': 'TASK_NOT_FOUND'}), 404

        resp = {
            'ok': True,
            'task_id': task_id,
            'db_type': task.get('db_type'),
            'host': task.get('host'),
            'status': task.get('status'),
            'created_at': task.get('created_at'),
        }
        if task.get('error'):
            resp['error'] = task['error']
        if task.get('result'):
            resp['result'] = task['result']
        return jsonify(resp)


# ── 列出任务 ──────────────────────────────────────────────────

@api_v1.route('/inspects', methods=['GET'])
def api_v1_inspect_list():
    """列出最近巡检任务（最多50条）"""
    auth_err = _require_api_key()
    if auth_err:
        return auth_err

    limit = min(int(request.args.get('limit', 20)), 50)
    with _inspect_lock:
        items = sorted(_inspect_tasks.values(), key=lambda t: t.get('created_at', ''), reverse=True)[:limit]
        result = []
        for t in items:
            item = {
                'task_id': t['task_id'],
                'db_type': t.get('db_type'),
                'host': t.get('host'),
                'status': t.get('status'),
                'created_at': t.get('created_at'),
            }
            if t.get('result'):
                item['total_score'] = t['result'].get('total_score')
                item['risk_level'] = t['result'].get('risk_level')
            if t.get('error'):
                item['error'] = t['error']
            result.append(item)
        return jsonify({'ok': True, 'tasks': result, 'count': len(result)})


# ── 巡检执行线程 ──────────────────────────────────────────────

def _run_inspect_thread(task_id, db_type, host, port, user, password, inspector, body, ssh):
    """后台执行巡检，完成后将结果写入 _inspect_tasks"""
    start_time = time.time()
    try:
        print(f"\n[API v1] 巡检: {db_type}://{user}@{host}:{port}", flush=True)
        # 通过现有巡检函数执行
        result = _execute_inspect(db_type, host, port, user, password, inspector, body, ssh)
        duration = round(time.time() - start_time, 1)

        structured = _parse_inspect_result(db_type, host, port, result, duration)

        with _inspect_lock:
            _inspect_tasks[task_id]['status'] = 'completed'
            _inspect_tasks[task_id]['result'] = structured
    except Exception as e:
        traceback.print_exc()
        with _inspect_lock:
            _inspect_tasks[task_id]['status'] = 'failed'
            _inspect_tasks[task_id]['error'] = str(e)


def _execute_inspect(db_type, host, port, user, password, inspector, body, ssh):
    """
    调用 run_inspection 模块执行巡检。
    返回 (risk_data_dict, report_path) 或出错。
    """
    import run_inspection as ri

    db_info = {
        'host': host,
        'port': port,
        'user': user,
        'password': password,
        'label': f'API-{inspector}',
    }

    if db_type in ('pg', 'postgresql'):
        db_info['database'] = body.get('database', 'postgres')
    if db_type == 'oracle':
        db_info['service_name'] = body.get('service_name', '')
        db_info['sid'] = body.get('sid', '')
        db_info['sysdba'] = body.get('sysdba', False)
    if db_type == 'tidb':
        db_info['database'] = body.get('database', '')

    ssh_info = None
    if ssh:
        ssh_info = {
            'host': ssh.get('host', ''),
            'port': ssh.get('port', 22),
            'user': ssh.get('user', ''),
            'password': ssh.get('password', ''),
            'key_file': ssh.get('key_file', ''),
        }

    # 根据数据库类型路由
    runner_map = {
        'mysql': ri.run_mysql,
        'pg': ri.run_pg,
        'oracle': ri.run_oracle_full,
        'dm': ri.run_dm,
        'sqlserver': ri.run_sqlserver,
        'tidb': ri.run_tidb,
    }
    runner = runner_map.get(db_type)
    if not runner:
        raise ValueError(f'不支持的数据库类型: {db_type}')

    return runner(db_info, inspector, ssh_info)


def _parse_inspect_result(db_type, host, port, raw_result, duration):
    """
    解析巡检结果，整理为结构化 JSON。
    raw_result 是 run_inspection.run() 返回的 (risk_data, report_path) 元组，
    或直接从 run_inspection 输出的数据中提取。
    """
    result = {
        'db_type': db_type,
        'host': f'{host}:{port}',
        'duration_seconds': duration,
        'inspected_at': datetime.now().isoformat(),
        'total_score': 100,
        'risk_level': 'pass',
        'risk_breakdown': {'low': 0, 'medium': 0, 'high': 0, 'critical': 0},
        'categories': [],
        'findings': [],
        'report_path': None,
    }

    try:
        if isinstance(raw_result, tuple) and len(raw_result) >= 1:
            risk_data = raw_result[0]
            if len(raw_result) >= 2:
                result['report_path'] = raw_result[1]

            if isinstance(risk_data, dict):
                # 提取风险数据
                total = risk_data.get('total_score', risk_data.get('score', 100))
                result['total_score'] = total
                result['risk_level'] = _score_to_level(total)

                breakdown = risk_data.get('risk_breakdown', {})
                if breakdown:
                    result['risk_breakdown'] = {
                        'low': breakdown.get('low', 0),
                        'medium': breakdown.get('medium', 0),
                        'high': breakdown.get('high', 0),
                        'critical': breakdown.get('critical', 0),
                    }

                # 分类评分
                cats = risk_data.get('categories', [])
                for c in cats:
                    if isinstance(c, dict):
                        result['categories'].append({
                            'name': c.get('name', ''),
                            'score': c.get('score', 100),
                            'status': _score_to_level(c.get('score', 100)),
                        })

                # 具体发现
                findings = risk_data.get('findings', risk_data.get('issues', []))
                for f in findings:
                    if isinstance(f, dict):
                        result['findings'].append({
                            'category': f.get('category', ''),
                            'item': f.get('item', f.get('name', '')),
                            'value': str(f.get('value', '')),
                            'threshold': str(f.get('threshold', '')),
                            'severity': f.get('severity', 'low'),
                            'suggestion': f.get('suggestion', f.get('fix', '')),
                        })
    except Exception:
        pass

    return result


def _score_to_level(score):
    score = int(score) if score is not None else 100
    if score >= 90:
        return 'pass'
    elif score >= 70:
        return 'low'
    elif score >= 50:
        return 'medium'
    elif score >= 30:
        return 'high'
    else:
        return 'critical'


# ── 管理员接口（Web UI 用）───────────────────────────────────

@api_v1.route('/admin/keys', methods=['GET'])
def admin_list_keys():
    """列出所有 API Key（管理端）"""
    auth_err = _require_admin()
    if auth_err:
        return auth_err
    conn = _get_key_db()
    try:
        rows = conn.execute(
            'SELECT id, name, key_prefix, created_at, last_used_at, is_active FROM api_keys ORDER BY created_at DESC'
        ).fetchall()
        keys = [dict(r) for r in rows]
        return jsonify({'ok': True, 'keys': keys})
    finally:
        conn.close()


@api_v1.route('/admin/keys', methods=['POST'])
def admin_create_key():
    """创建 API Key（管理端）"""
    auth_err = _require_admin()
    if auth_err:
        return auth_err
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '名称不能为空'}), 400

    raw_key = _generate_key()
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:10]
    key_id = str(uuid.uuid4())[:8]

    conn = _get_key_db()
    try:
        conn.execute(
            'INSERT INTO api_keys (id, name, key_hash, key_prefix, created_at) VALUES (?,?,?,?,?)',
            (key_id, name, key_hash, key_prefix, datetime.now().isoformat())
        )
        conn.commit()
        return jsonify({'ok': True, 'key': raw_key, 'id': key_id, 'name': name})
    except sqlite3.IntegrityError:
        return jsonify({'ok': False, 'error': '创建失败，请重试'}), 500
    finally:
        conn.close()


@api_v1.route('/admin/keys/<key_id>', methods=['DELETE'])
def admin_delete_key(key_id):
    """删除 API Key（管理端）"""
    auth_err = _require_admin()
    if auth_err:
        return auth_err
    conn = _get_key_db()
    try:
        conn.execute('DELETE FROM api_keys WHERE id=?', (key_id,))
        conn.commit()
        return jsonify({'ok': True})
    finally:
        conn.close()


# ── 管理员令牌注入 ──────────────────────────────────────────

@api_v1.route('/admin/token', methods=['GET'])
def admin_get_token():
    """Web UI 获取管理员令牌（仅 localhost）"""
    if request.remote_addr not in ('127.0.0.1', 'localhost', '::1'):
        return jsonify({'ok': False, 'error': '仅限本地访问'}), 403
    return jsonify({'ok': True, 'token': _ADMIN_TOKEN})
