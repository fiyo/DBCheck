# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck 数据管理模块
管理 DBCheck 自身的数据文件：备份、还原、升级检测
"""

import os
import shutil
import sqlite3
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_BACKUPS_DIR = os.path.join(BASE_DIR, 'data_backups')

# DBCheck 自身数据文件清单（相对路径）
CRITICAL_FILES = [
    {'path': 'data/history.db', 'name': '巡检快照历史', 'category': 'data', 'required': True},
    {'path': 'data/server_history.db', 'name': '服务器巡检历史', 'category': 'data', 'required': True},
    {'path': 'data/inspection.db', 'name': '巡检配置数据库', 'category': 'config', 'required': True},
    {'path': '.db_key', 'name': '密码加密密钥', 'category': 'security', 'required': True},
    {'path': 'user_management/db/um_rbac.db', 'name': 'RBAC权限管理数据库', 'category': 'security', 'required': True},
]

CRITICAL_DIRS = [
    {'path': 'pro_data', 'name': 'Pro版数据目录', 'category': 'pro', 'required': True},
]


def _file_size_fmt(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _dir_size(dirpath: str) -> int:
    """计算目录总大小"""
    total = 0
    if not os.path.exists(dirpath):
        return 0
    for dirpath_inner, _, filenames in os.walk(dirpath):
        for f in filenames:
            fp = os.path.join(dirpath_inner, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _file_md5(filepath: str) -> str:
    """计算文件MD5"""
    if not os.path.exists(filepath):
        return ''
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_data_files_info() -> List[Dict[str, Any]]:
    """获取所有数据文件信息（大小、是否存在、修改时间等）"""
    result = []

    for item in CRITICAL_FILES:
        full_path = os.path.join(BASE_DIR, item['path'])
        exists = os.path.exists(full_path)
        info = {
            'path': item['path'],
            'name': item['name'],
            'category': item['category'],
            'required': item['required'],
            'exists': exists,
            'size': 0,
            'size_fmt': '0 B',
            'modified': '',
            'md5': '',
        }
        if exists:
            stat = os.stat(full_path)
            info['size'] = stat.st_size
            info['size_fmt'] = _file_size_fmt(stat.st_size)
            info['modified'] = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            info['md5'] = _file_md5(full_path)
        result.append(info)

    for item in CRITICAL_DIRS:
        full_path = os.path.join(BASE_DIR, item['path'])
        exists = os.path.exists(full_path)
        size = _dir_size(full_path) if exists else 0
        result.append({
            'path': item['path'],
            'name': item['name'],
            'category': item['category'],
            'required': item['required'],
            'exists': exists,
            'size': size,
            'size_fmt': _file_size_fmt(size),
            'modified': datetime.fromtimestamp(os.path.getmtime(full_path)).strftime('%Y-%m-%d %H:%M:%S') if exists else '',
            'md5': '',
            'file_count': len(list(Path(full_path).rglob('*'))) if exists else 0,
        })

    return result


def backup_data(backup_name: str = None) -> Dict[str, Any]:
    """
    备份所有数据文件到 data_backups/ 目录下
    返回备份信息
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if backup_name:
        backup_dir = os.path.join(DATA_BACKUPS_DIR, backup_name)
    else:
        backup_dir = os.path.join(DATA_BACKUPS_DIR, f"backup_{timestamp}")

    os.makedirs(backup_dir, exist_ok=True)
    copied = []
    errors = []
    total_size = 0

    # 备份文件
    for item in CRITICAL_FILES:
        src = os.path.join(BASE_DIR, item['path'])
        if not os.path.exists(src):
            errors.append(f"{item['path']}: 文件不存在")
            continue
        # 保持目录结构
        dst = os.path.join(backup_dir, item['path'])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copy2(src, dst)
            size = os.path.getsize(src)
            total_size += size
            copied.append({'path': item['path'], 'name': item['name'], 'size': size})
        except Exception as e:
            errors.append(f"{item['path']}: {str(e)}")

    # 备份目录
    for item in CRITICAL_DIRS:
        src = os.path.join(BASE_DIR, item['path'])
        if not os.path.exists(src):
            errors.append(f"{item['path']}: 目录不存在")
            continue
        dst = os.path.join(backup_dir, item['path'])
        try:
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            size = _dir_size(src)
            total_size += size
            copied.append({'path': item['path'], 'name': item['name'], 'size': size})
        except Exception as e:
            errors.append(f"{item['path']}: {str(e)}")

    # 写入备份元信息
    meta = {
        'backup_time': datetime.now().isoformat(),
        'backup_name': os.path.basename(backup_dir),
        'items': [{'path': c['path'], 'name': c['name'], 'size': c['size']} for c in copied],
        'total_size': total_size,
    }
    meta_path = os.path.join(backup_dir, '.backup_meta.json')
    import json
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {
        'success': len(copied) > 0 and len(errors) == 0,
        'backup_dir': backup_dir,
        'backup_name': os.path.basename(backup_dir),
        'copied': copied,
        'errors': errors,
        'total_size': total_size,
        'total_size_fmt': _file_size_fmt(total_size),
        'message': f"备份完成，共 {len(copied)} 个项目，{errors and len(errors)} 个错误" if errors else f"备份完成，共 {len(copied)} 个项目",
    }


def _get_db_tables(db_path: str) -> Dict[str, Dict]:
    """获取数据库的表结构信息"""
    conn = sqlite3.connect(db_path)
    tables = {}
    for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
        name, sql = row
        tables[name] = {'sql': sql}
        cols_info = conn.execute(f"PRAGMA table_info('{name}')").fetchall()
        pk_cols = [col[1] for col in cols_info if col[5]]
        tables[name]['primary_key'] = pk_cols
        tables[name]['columns'] = [col[1] for col in cols_info]
    conn.close()
    return tables


def _get_unique_keys(conn, table_name: str) -> List[List[str]]:
    """获取表的所有唯一键列组合"""
    unique_keys = []
    for idx in conn.execute(f"PRAGMA index_list('{table_name}')"):
        idx_name, unique = idx[1], idx[2]
        if unique:
            cols = []
            for col in conn.execute(f"PRAGMA index_info('{idx_name}')"):
                cols.append(col[2])
            if cols:
                unique_keys.append(cols)
    return unique_keys


def _smart_restore_db(backup_db: str, current_db: str) -> Dict[str, Any]:
    """智能还原单个数据库文件：对比表结构和数据，只恢复缺失内容"""
    backup_tables = _get_db_tables(backup_db)
    current_tables = _get_db_tables(current_db)

    restored_tables = []
    restored_rows = 0
    skipped_tables = []

    # 1) 备份中有但当前没有的表 → 直接恢复整表
    for table_name, table_info in backup_tables.items():
        if table_name not in current_tables:
            backup_conn = sqlite3.connect(backup_db)
            current_conn = sqlite3.connect(current_db)
            try:
                current_conn.execute(table_info['sql'])
                columns = table_info['columns']
                rows = backup_conn.execute(f"SELECT * FROM [{table_name}]").fetchall()
                if rows:
                    placeholders = ','.join(['?' for _ in columns])
                    current_conn.executemany(
                        f"INSERT INTO [{table_name}] VALUES ({placeholders})",
                        rows
                    )
                    restored_rows += len(rows)
                current_conn.commit()
                restored_tables.append(table_name)
            except Exception:
                try:
                    current_conn.execute(f"ATTACH DATABASE '{backup_db}' AS src")
                    current_conn.execute(f"CREATE TABLE [{table_name}] AS SELECT * FROM src.[{table_name}]")
                    current_conn.commit()
                    current_conn.execute("DETACH DATABASE src")
                    restored_tables.append(table_name)
                except Exception:
                    pass
            finally:
                backup_conn.close()
                current_conn.close()
        else:
            skipped_tables.append(table_name)

    # 2) 两张库都有的表 → 对比数据，只插入缺失行
    backup_conn = sqlite3.connect(backup_db)
    current_conn = sqlite3.connect(current_db)
    try:
        for table_name in backup_tables:
            if table_name not in current_tables:
                continue
            bk_info = backup_tables[table_name]
            columns = bk_info['columns']
            if not columns:
                continue

            match_cols = bk_info.get('primary_key', [])
            if not match_cols:
                unique_keys = _get_unique_keys(backup_conn, table_name)
                if unique_keys:
                    match_cols = unique_keys[0]
            if not match_cols:
                continue

            sub_selects = ' AND '.join([f"src.[{c}] = tgt.[{c}]" for c in match_cols])
            try:
                current_conn.execute(f"ATTACH DATABASE '{backup_db}' AS src")
                stmt = f"INSERT INTO [{table_name}] SELECT {','.join(['[' + c + ']' for c in columns])} FROM src.[{table_name}] AS src WHERE NOT EXISTS (SELECT 1 FROM [{table_name}] AS tgt WHERE {sub_selects})"
                result = current_conn.execute(stmt)
                count = result.rowcount if result.rowcount is not None else 0
                restored_rows += count
                if count > 0:
                    restored_tables.append(f"{table_name}({count} rows)")
                current_conn.commit()
                current_conn.execute("DETACH DATABASE src")
            except Exception:
                pass
    finally:
        backup_conn.close()
        current_conn.close()

    return {
        'restored_tables': restored_tables,
        'restored_rows': restored_rows,
        'skipped_tables': skipped_tables
    }


def restore_data(backup_name: str, selected_items: List[str] = None) -> Dict[str, Any]:
    """
    从指定备份智能还原数据文件
    backup_name: 备份目录名（data_backups/ 下的子目录名）
    selected_items: 可选，只还原指定的文件路径列表
    """
    backup_dir = os.path.join(DATA_BACKUPS_DIR, backup_name)
    if not os.path.exists(backup_dir):
        return {'success': False, 'message': f'备份目录不存在: {backup_name}'}

    # 读元信息
    meta_path = os.path.join(backup_dir, '.backup_meta.json')
    meta = None
    if os.path.exists(meta_path):
        import json
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)

    # 确定要还原的项目
    if selected_items:
        items_to_restore = selected_items
    else:
        all_items = [f['path'] for f in CRITICAL_FILES] + [d['path'] for d in CRITICAL_DIRS]
        items_to_restore = all_items

    restored = []
    skipped = []
    errors = []
    db_restore_details = []

    for item_path in items_to_restore:
        src = os.path.join(backup_dir, item_path)
        dst = os.path.join(BASE_DIR, item_path)

        if not os.path.exists(src):
            errors.append(f"{item_path}: 备份中不存在此文件")
            continue

        try:
            if os.path.isdir(src):
                # 目录：合并复制，只补充缺失文件
                if not os.path.exists(dst):
                    os.makedirs(dst, exist_ok=True)
                added = []
                for root, dirs, files in os.walk(src):
                    rel = os.path.relpath(root, src)
                    dst_dir = os.path.join(dst, rel)
                    os.makedirs(dst_dir, exist_ok=True)
                    for fname in files:
                        s = os.path.join(root, fname)
                        d = os.path.join(dst_dir, fname)
                        if not os.path.exists(d):
                            shutil.copy2(s, d)
                            added.append(os.path.join(rel, fname))
                if added:
                    restored.append(f"{item_path} ({len(added)} 个新文件)")
                else:
                    skipped.append(item_path)
            elif src.endswith('.db'):
                # SQLite 数据库：智能比对还原
                result = _smart_restore_db(src, dst)
                if result['restored_tables'] or result['restored_rows'] > 0:
                    restored.append(f"{item_path} (新表:{len(result['restored_tables'])}, 新行:{result['restored_rows']})")
                    db_restore_details.append({'db': item_path, **result})
                else:
                    skipped.append(item_path)
            else:
                # 非数据库文件：如果目标已存在，先创建还原前备份再覆盖
                if os.path.exists(dst):
                    pre_backup = dst + '.restore_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S')
                    shutil.copy2(dst, pre_backup)
                os.makedirs(os.path.dirname(dst) if os.path.dirname(dst) else '.', exist_ok=True)
                shutil.copy2(src, dst)
                restored.append(item_path)
        except Exception as e:
            errors.append(f"{item_path}: {str(e)}")

    msg_parts = [f'{len(restored)} 项恢复']
    if skipped:
        msg_parts.append(f'{len(skipped)} 项跳过')
    if errors:
        msg_parts.append(f'{len(errors)} 项错误')
    return {
        'success': len(errors) == 0,
        'backup_name': backup_name,
        'restored': restored,
        'skipped': skipped,
        'errors': errors,
        'db_details': db_restore_details,
        'message': f"智能还原完成: {', '.join(msg_parts)}",
    }


def list_backups() -> List[Dict[str, Any]]:
    """列出所有可用备份"""
    if not os.path.exists(DATA_BACKUPS_DIR):
        return []

    backups = []
    for entry in sorted(os.listdir(DATA_BACKUPS_DIR), reverse=True):
        backup_dir = os.path.join(DATA_BACKUPS_DIR, entry)
        if not os.path.isdir(backup_dir):
            continue

        meta_path = os.path.join(backup_dir, '.backup_meta.json')
        meta = None
        if os.path.exists(meta_path):
            import json
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)

        size = _dir_size(backup_dir)
        stat = os.stat(backup_dir)

        backups.append({
            'name': entry,
            'path': backup_dir,
            'size': size,
            'size_fmt': _file_size_fmt(size),
            'created': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            'item_count': len(meta.get('items', [])) if meta else 0,
            'total_size': meta.get('total_size', 0) if meta else 0,
        })

    return backups


def delete_backup(backup_name: str) -> Dict[str, Any]:
    """删除指定备份"""
    backup_dir = os.path.join(DATA_BACKUPS_DIR, backup_name)
    if not os.path.exists(backup_dir):
        return {'success': False, 'message': f'备份不存在: {backup_name}'}

    try:
        shutil.rmtree(backup_dir)
        return {'success': True, 'message': f'已删除备份: {backup_name}'}
    except Exception as e:
        return {'success': False, 'message': f'删除失败: {str(e)}'}


def check_upgrade_ready() -> Dict[str, Any]:
    """
    检查升级前数据状态，生成备份建议
    """
    files_info = get_data_files_info()
    missing = [f for f in files_info if not f['exists'] and f.get('required', False)]
    total_size = sum(f['size'] for f in files_info if f['exists'])
    existing_backups = list_backups()
    latest_backup = existing_backups[0] if existing_backups else None

    # 判断是否需要备份
    needs_backup = False
    if total_size > 0:
        if not latest_backup:
            needs_backup = True
        else:
            # 检查最新备份是否包含关键数据
            latest_time = datetime.strptime(latest_backup['created'], '%Y-%m-%d %H:%M:%S')
            now = datetime.now()
            if (now - latest_time).days >= 7:
                needs_backup = True

    return {
        'files': files_info,
        'missing_required': missing,
        'total_data_size': total_size,
        'total_data_size_fmt': _file_size_fmt(total_size),
        'backup_count': len(existing_backups),
        'latest_backup': latest_backup,
        'needs_backup': needs_backup,
        'recommend_backup': needs_backup or total_size > 0,
    }
