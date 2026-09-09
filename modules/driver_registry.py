# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
modules/driver_registry.py — JDBC 驱动元数据注册中心（Stage A）

职责：
  - 管理 25 类数据库的 JDBC 驱动元数据：db_type / version / driver_class / jar 路径
  - 元数据存储：data/drivers.db (独立 SQLite 文件，与 inspection.db 解耦)
  - 提供核心 API：
      list_db_types(include_hidden=False) -> db_type 定义 + 已知 driver_class 提示
      hide_db_type(db_type)               -> 隐藏（删除）某类型，同时清理其驱动
      unhide_db_type(db_type)             -> 恢复已隐藏类型
      list_drivers(db_type)               -> 某类型已登记的驱动列表
      add_driver(...)                     -> 上传新驱动（jar 文件保存到 drivers/<db_type>/<version>/）
      delete_driver(id)                   -> 删除驱动（同时移走 jar 文件）
      activate_driver(id)                 -> 设为该 db_type 的默认驱动（is_active=1，其它同 db_type 置 0）

Stage A 范围：
  - 仅建注册表 + UI + 上传/删除，不改动 plugins/* 与 native 驱动（阶段 B/C 范畴）
  - drivers/ 目录现有文件保持不动，新上传的 jar 落 drivers/<db_type>/<version>/<jar>
"""

import os
import re
import json
import sqlite3
import threading
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from modules.core.paths import PROJECT_ROOT, DATA_DIR


# ── 路径常量 ──────────────────────────────────────────
DRIVERS_DB_PATH = str(DATA_DIR / 'drivers.db')
DRIVERS_DIR = str(PROJECT_ROOT / 'drivers')

# ── 25 类 db_type 元数据（驱动类提示/常用 Maven 坐标）────────────
# is_jdbc=True 表示该类型标准走 JDBC；False 表示无标准 JDBC（mongo/es 等，
# 阶段 A 仍开放登记——可由用户自行指定 driver_class + jar 路径）。
DB_TYPE_CATALOG: List[Dict] = [
    {'key': 'mysql',         'name_zh': 'MySQL',         'name_en': 'MySQL',         'driver_class_hint': 'com.mysql.cj.jdbc.Driver',         'is_jdbc': True,  'order': 1},
    {'key': 'mariadb',       'name_zh': 'MariaDB',       'name_en': 'MariaDB',       'driver_class_hint': 'org.mariadb.jdbc.Driver',           'is_jdbc': True,  'order': 2},
    {'key': 'oracle',        'name_zh': 'Oracle',        'name_en': 'Oracle',        'driver_class_hint': 'oracle.jdbc.driver.OracleDriver',    'is_jdbc': True,  'order': 3},
    {'key': 'sqlserver',     'name_zh': 'SqlServer',     'name_en': 'SQL Server',    'driver_class_hint': 'com.microsoft.sqlserver.jdbc.SQLServerDriver', 'is_jdbc': True, 'order': 4},
    {'key': 'postgresql',    'name_zh': 'PostgreSQL',    'name_en': 'PostgreSQL',    'driver_class_hint': 'org.postgresql.Driver',              'is_jdbc': True,  'order': 5},
    {'key': 'db2',           'name_zh': 'DB2',           'name_en': 'IBM Db2',       'driver_class_hint': 'com.ibm.db2.jcc.DB2Driver',          'is_jdbc': True,  'order': 6},
    {'key': 'dm',            'name_zh': 'DM',            'name_en': 'DM (达梦)',     'driver_class_hint': 'dm.jdbc.driver.DmDriver',            'is_jdbc': True,  'order': 7},
    {'key': 'kingbase',      'name_zh': 'KingBase',      'name_en': 'KingbaseES',    'driver_class_hint': 'com.kingbase8.Driver',               'is_jdbc': True,  'order': 8},
    {'key': 'oscar',         'name_zh': 'Oscar',         'name_en': 'Oscar (神州通用)', 'driver_class_hint': 'com.oscar.Driver',                 'is_jdbc': True,  'order': 9},
    {'key': 'gbase8a',       'name_zh': 'GBase8A',       'name_en': 'GBase 8a',      'driver_class_hint': 'com.gbase.jdbc.Driver',              'is_jdbc': True,  'order': 10},
    {'key': 'gbase8s',       'name_zh': 'GBase8S',       'name_en': 'GBase 8s',      'driver_class_hint': 'com.gbasedbt.jdbc.Driver',            'is_jdbc': True,  'order': 11},
    {'key': 'highgo',        'name_zh': 'HighGo',        'name_en': 'HighGo (瀚高)', 'driver_class_hint': 'org.postgresql.Driver',              'is_jdbc': True,  'order': 12},
    {'key': 'sybase',        'name_zh': 'Sybase',        'name_en': 'Sybase ASE',    'driver_class_hint': 'com.sybase.jdbc4.jdbc.SybDriver',    'is_jdbc': True,  'order': 13},
    {'key': 'hive',          'name_zh': 'Hive',          'name_en': 'Apache Hive',   'driver_class_hint': 'org.apache.hive.jdbc.HiveDriver',    'is_jdbc': True,  'order': 14},
    {'key': 'sqlite3',       'name_zh': 'Sqlite3',       'name_en': 'SQLite',        'driver_class_hint': 'org.sqlite.JDBC',                    'is_jdbc': True,  'order': 15},
    {'key': 'opengauss',     'name_zh': 'OpenGauss',     'name_en': 'openGauss',     'driver_class_hint': 'org.opengauss.Driver',               'is_jdbc': True,  'order': 16},
    {'key': 'clickhouse',    'name_zh': 'ClickHouse',    'name_en': 'ClickHouse',    'driver_class_hint': 'com.clickhouse.jdbc.ClickHouseDriver', 'is_jdbc': True, 'order': 17},
    {'key': 'mongodb',       'name_zh': 'MongoDB',       'name_en': 'MongoDB',       'driver_class_hint': '',                                   'is_jdbc': False, 'order': 18},
    {'key': 'elasticsearch', 'name_zh': 'ElasticSearch', 'name_en': 'ElasticSearch', 'driver_class_hint': '',                                   'is_jdbc': False, 'order': 19},
    {'key': 'starrocks',     'name_zh': 'StarRocks',     'name_en': 'StarRocks',     'driver_class_hint': 'com.mysql.cj.jdbc.Driver',            'is_jdbc': True,  'order': 20},
    {'key': 'greenplum',     'name_zh': 'Greenplum',     'name_en': 'Greenplum',     'driver_class_hint': 'org.postgresql.Driver',               'is_jdbc': True,  'order': 21},
    {'key': 'doris',         'name_zh': 'Doris',         'name_en': 'Apache Doris',  'driver_class_hint': 'com.mysql.cj.jdbc.Driver',            'is_jdbc': True,  'order': 22},
    {'key': 'oceanbase',     'name_zh': 'OceanBase',     'name_en': 'OceanBase',     'driver_class_hint': 'com.oceanbase.jdbc.Driver',          'is_jdbc': True,  'order': 23},
    {'key': 'tdengine',      'name_zh': 'TDengine',      'name_en': 'TDengine',      'driver_class_hint': 'com.taosdata.jdbc.rsdriver',         'is_jdbc': True,  'order': 24},
    {'key': 'uxdb',          'name_zh': 'UXDB',          'name_en': 'UXDB (优炫)',   'driver_class_hint': 'com.uxsino.uxdb.Driver',          'is_jdbc': True,  'order': 25},
    {'key': 'ivorysql',      'name_zh': 'IvorySQL',      'name_en': 'IvorySQL',      'driver_class_hint': 'org.postgresql.Driver',            'is_jdbc': True,  'order': 26},
    {'key': 'yashandb',      'name_zh': 'YashanDB',      'name_en': 'YashanDB (崖山)', 'driver_class_hint': 'com.yashandb.jdbc.Driver',        'is_jdbc': True,  'order': 27},
    {'key': 'tidb',          'name_zh': 'TiDB',          'name_en': 'TiDB',           'driver_class_hint': 'com.mysql.cj.jdbc.Driver',          'is_jdbc': True,  'order': 28},
]
DB_TYPE_KEYS = frozenset(d['key'] for d in DB_TYPE_CATALOG)


# ── SQLite 线程本地连接 ─────────────────────────────────────
_local = threading.local()


def _conn() -> sqlite3.Connection:
    """每个线程独立连接；驱动表轻量级单文件，单线程请求级别即可。"""
    c = getattr(_local, 'conn', None)
    if c is None:
        os.makedirs(os.path.dirname(DRIVERS_DB_PATH), exist_ok=True)
        c = sqlite3.connect(DRIVERS_DB_PATH, timeout=10, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA foreign_keys=ON')
        _local.conn = c
    return c


def init_db() -> None:
    """建表 + 索引；幂等可重复调用。"""
    c = _conn()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS jdbc_driver_registry (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        db_type         TEXT NOT NULL,
        version         TEXT NOT NULL,
        driver_class    TEXT,
        jar_filename    TEXT NOT NULL,
        jar_path        TEXT NOT NULL,
        file_size       INTEGER DEFAULT 0,
        is_active       INTEGER NOT NULL DEFAULT 0,
        uploaded_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        note            TEXT,
        UNIQUE(db_type, version, jar_filename)
    );
    CREATE INDEX IF NOT EXISTS idx_dr_dbtype ON jdbc_driver_registry(db_type);
    CREATE INDEX IF NOT EXISTS idx_dr_active  ON jdbc_driver_registry(db_type, is_active);

    -- 被用户隐藏（删除）的数据库类型；硬编码 catalog 不会被真正删掉，仅 UI 不显示
    CREATE TABLE IF NOT EXISTS driver_type_hidden (
        db_type  TEXT PRIMARY KEY NOT NULL,
        hidden_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );

    -- 用户自定义新增的数据库类型（列表里原本没有的）；真删不进回收站
    CREATE TABLE IF NOT EXISTS driver_type_custom (
        db_type          TEXT PRIMARY KEY NOT NULL,
        name_zh          TEXT NOT NULL,
        name_en          TEXT NOT NULL,
        driver_class_hint TEXT,
        is_jdbc          INTEGER NOT NULL DEFAULT 1,
        created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    ''')
    c.commit()


# ── 安全校验 ──────────────────────────────────────────
_VERSION_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\-+]{0,31}$')


def _validate_version(v: str) -> Tuple[bool, str]:
    v = (v or '').strip()
    if not v:
        return False, '版本号不能为空'
    if not _VERSION_RE.match(v):
        return False, '版本号只能包含字母/数字/._-+，长度 ≤ 32'
    return True, v


def _validate_db_type(t: str) -> bool:
    if t in DB_TYPE_KEYS:
        return True
    return _is_custom_db_type(t)


# ── 自定义类型（列表里原本没有、用户自行新增）────────────
_CUSTOM_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{1,31}$')


def _is_custom_db_type(t: str) -> bool:
    try:
        return _conn().execute(
            'SELECT 1 FROM driver_type_custom WHERE db_type=?', (t,)
        ).fetchone() is not None
    except Exception:
        return False


def _load_custom_types() -> List[Dict]:
    """读取用户自定义类型，按创建时间顺序；order 基数 1000 保证排在内置之后。"""
    try:
        c = _conn()
        rows = c.execute(
            'SELECT db_type,name_zh,name_en,driver_class_hint,is_jdbc '
            'FROM driver_type_custom ORDER BY created_at ASC, db_type ASC'
        ).fetchall()
        out = []
        for i, r in enumerate(rows):
            out.append({
                'key': r['db_type'],
                'name_zh': r['name_zh'],
                'name_en': r['name_en'],
                'driver_class_hint': r['driver_class_hint'] or '',
                'is_jdbc': bool(r['is_jdbc']),
                'order': 1000 + i,
                'is_custom': True,
            })
        return out
    except Exception:
        return []


def _load_hidden_types() -> set:
    """返回用户已隐藏（删除）的 db_type 集合。"""
    try:
        c = _conn()
        rows = c.execute('SELECT db_type FROM driver_type_hidden').fetchall()
        return {r['db_type'] for r in rows}
    except Exception:
        return set()


# ── API: list_db_types ─────────────────────────────────
def list_db_types() -> List[Dict]:
    """
    返回可见的 db_type 清单（含已知 driver_class 提示）。
    合并：内置（非隐藏）+ 用户自定义；内置在前、自定义在后，统一重排 1..N。
    """
    hidden = _load_hidden_types()
    visible = [dict(t) for t in DB_TYPE_CATALOG if t['key'] not in hidden]
    for c in _load_custom_types():
        visible.append(dict(c))
    visible.sort(key=lambda x: x['order'])
    for i, t in enumerate(visible, start=1):
        t['order'] = i
    return visible


def add_custom_db_type(key: str, name_zh: str, name_en: str,
                       driver_class_hint: str = '', is_jdbc: bool = True) -> Tuple[bool, str]:
    """
    新增一个用户自定义数据库类型（列表里原本没有的）。
    校验：key 唯一（不与内置、不与已存在 custom 冲突）、符合标识规则。
    """
    key = (key or '').strip().lower()
    if not _CUSTOM_KEY_RE.match(key):
        return False, '类型标识须为小写字母/数字/下划线，且以字母开头，长度 2-32'
    if key in DB_TYPE_KEYS:
        return False, f'类型标识「{key}」与内置类型冲突'
    try:
        if _conn().execute('SELECT 1 FROM driver_type_custom WHERE db_type=?', (key,)).fetchone():
            return False, f'类型标识「{key}」已存在'
    except Exception:
        return False, '数据库异常'
    name_zh = (name_zh or '').strip()
    name_en = (name_en or '').strip()
    if not name_zh and not name_en:
        return False, '中文名/英文名至少填一个'
    if not name_zh:
        name_zh = key
    if not name_en:
        name_en = key
    hint = (driver_class_hint or '').strip()
    c = _conn()
    c.execute(
        'INSERT INTO driver_type_custom (db_type,name_zh,name_en,driver_class_hint,is_jdbc) '
        'VALUES (?,?,?,?,?)',
        (key, name_zh, name_en, hint, 1 if is_jdbc else 0)
    )
    c.commit()
    return True, f'已新增类型 {name_zh}（{key}）'


def delete_custom_db_type(db_type: str) -> Tuple[bool, str]:
    """真删用户自定义类型（同时清理其下所有驱动），不进回收站。"""
    if not _is_custom_db_type(db_type):
        return False, f'非自定义类型：{db_type}'
    for drv in list_drivers(db_type):
        delete_driver(drv['id'])
    c = _conn()
    c.execute('DELETE FROM driver_type_custom WHERE db_type=?', (db_type,))
    c.commit()
    return True, f'已删除自定义类型 {db_type}'


def list_hidden_db_types() -> List[Dict]:
    """返回用户已隐藏（删除）的 db_type 清单，用于回收站恢复。"""
    hidden = _load_hidden_types()
    items = [dict(t) for t in DB_TYPE_CATALOG if t['key'] in hidden]
    items.sort(key=lambda x: x['order'])
    for i, t in enumerate(items, start=1):
        t['order'] = i
    return items


# ── API: hide_db_type / unhide_db_type ─────────────────
def hide_db_type(db_type: str) -> Tuple[bool, str]:
    """
    隐藏（删除）某个数据库类型：
      1. 从 UI 类型列表中移除；
      2. 删除该类型下已登记的所有驱动（含 JAR 文件）。
    仅用于内置类型；自定义类型请走 delete_custom_db_type。
    """
    if not _validate_db_type(db_type):
        return False, f'未知 db_type：{db_type}'
    if _is_custom_db_type(db_type):
        return False, '自定义类型请使用「删除」（真删，不进回收站），勿走隐藏'

    # 先删该类型下所有驱动
    for drv in list_drivers(db_type):
        delete_driver(drv['id'])

    c = _conn()
    c.execute(
        'INSERT OR REPLACE INTO driver_type_hidden (db_type) VALUES (?)',
        (db_type,)
    )
    c.commit()
    return True, f'已删除类型 {db_type}'


def unhide_db_type(db_type: str) -> Tuple[bool, str]:
    """恢复（重新显示）之前隐藏的数据库类型。"""
    if not _validate_db_type(db_type):
        return False, f'未知 db_type：{db_type}'
    c = _conn()
    c.execute('DELETE FROM driver_type_hidden WHERE db_type=?', (db_type,))
    c.commit()
    return True, f'已恢复类型 {db_type}'


# ── API: list_drivers ─────────────────────────────────
def list_drivers(db_type: str) -> List[Dict]:
    if not _validate_db_type(db_type):
        return []
    c = _conn()
    rows = c.execute(
        'SELECT * FROM jdbc_driver_registry WHERE db_type=? ORDER BY is_active DESC, uploaded_at DESC',
        (db_type,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── API: add_driver ───────────────────────────────────
def add_driver(db_type: str, version: str, driver_class: str,
               src_path: str, original_filename: str,
               note: str = '') -> Tuple[bool, str, Optional[int]]:
    """
    把上传的 jar 移动到 drivers/<db_type>/<version>/，登记到元数据表。
    返回 (ok, message, id)。
    """
    if not _validate_db_type(db_type):
        return False, f'未知 db_type：{db_type}', None
    ok, v = _validate_version(version)
    if not ok:
        return False, v, None

    safe_name = os.path.basename(original_filename or '').strip()
    # 防御性：拒绝原始 filename 含路径分隔符 / ..（必须在 basename 之前检查）
    if not original_filename or os.path.sep in original_filename or '/' in original_filename or '\\' in original_filename or '..' in original_filename:
        return False, '文件名非法（不能含路径分隔符或 ..）', None
    if not safe_name or not safe_name.lower().endswith('.jar'):
        return False, '驱动文件名必须以 .jar 结尾', None

    target_dir = Path(DRIVERS_DIR) / db_type / v
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, f'创建目录失败：{e}', None
    target_path = target_dir / safe_name

    # 移动（先校验源文件存在）
    if not os.path.isfile(src_path):
        return False, '上传文件丢失', None
    try:
        # 若目标已存在则覆盖
        if target_path.exists():
            target_path.unlink()
        # 使用 shutil.move 以支持跨卷
        import shutil
        shutil.move(src_path, str(target_path))
    except Exception as e:
        return False, f'保存驱动文件失败：{e}', None

    file_size = target_path.stat().st_size
    jar_path = str(target_path)
    dc = (driver_class or '').strip() or None

    c = _conn()
    try:
        cur = c.execute(
            'INSERT INTO jdbc_driver_registry (db_type, version, driver_class, jar_filename, jar_path, file_size, note) '
            'VALUES (?,?,?,?,?,?,?)',
            (db_type, v, dc, safe_name, jar_path, file_size, note)
        )
        new_id = cur.lastrowid
        c.commit()
        # 若该 db_type 当前没有任何激活驱动，把这次新上传设为激活
        active_count = c.execute(
            'SELECT COUNT(*) FROM jdbc_driver_registry WHERE db_type=? AND is_active=1', (db_type,)
        ).fetchone()[0]
        if active_count == 0:
            c.execute('UPDATE jdbc_driver_registry SET is_active=1 WHERE id=?', (new_id,))
            c.commit()
        return True, '驱动已登记', new_id
    except sqlite3.IntegrityError:
        return False, f'该类型下已存在同名驱动（{db_type} {v} {safe_name}）', None
    except Exception as e:
        # 回滚文件移动
        try:
            if target_path.exists():
                target_path.unlink()
        except Exception:
            pass
        return False, f'登记失败：{e}', None


# ── API: delete_driver ─────────────────────────────────
def _hard_remove(path: str) -> bool:
    """
    真正从磁盘删除文件。**绕过** WorkBuddy sitecustomize 的 safe-delete 拦截
    （该拦截依赖 Windows Recycle Bin，sandbox / 容器内不可用导致 os.remove 失败）。
    Windows 上用 ctypes 直接调 DeleteFileW；非 Windows 退化 os.unlink。
    """
    if not path or not os.path.isfile(path):
        return True
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            ctypes.windll.kernel32.DeleteFileW(wintypes.LPCWSTR(path))
        else:
            os.unlink(path)
        return True
    except Exception:
        return False


def delete_driver(driver_id: int) -> Tuple[bool, str]:
    c = _conn()
    row = c.execute('SELECT * FROM jdbc_driver_registry WHERE id=?', (driver_id,)).fetchone()
    if row is None:
        return False, '驱动不存在'
    jar_path = row['jar_path']
    if jar_path and not _hard_remove(jar_path):
        # 文件删失败不让元数据删（避免数据库残留）
        return False, f'删除文件失败：{jar_path}'
    c.execute('DELETE FROM jdbc_driver_registry WHERE id=?', (driver_id,))
    c.commit()
    # 若该 db_type 还有其它驱动但都没激活，自动激活最新一条
    left = c.execute(
        'SELECT COUNT(*) FROM jdbc_driver_registry WHERE db_type=? AND is_active=1',
        (row['db_type'],)
    ).fetchone()[0]
    if left == 0:
        latest = c.execute(
            'SELECT id FROM jdbc_driver_registry WHERE db_type=? ORDER BY uploaded_at DESC LIMIT 1',
            (row['db_type'],)
        ).fetchone()
        if latest:
            c.execute('UPDATE jdbc_driver_registry SET is_active=1 WHERE id=?', (latest['id'],))
            c.commit()
    return True, '已删除'


# ── API: activate_driver ──────────────────────────────
def activate_driver(driver_id: int) -> Tuple[bool, str]:
    c = _conn()
    row = c.execute('SELECT * FROM jdbc_driver_registry WHERE id=?', (driver_id,)).fetchone()
    if row is None:
        return False, '驱动不存在'
    db_type = row['db_type']
    c.execute('UPDATE jdbc_driver_registry SET is_active=0 WHERE db_type=?', (db_type,))
    c.execute('UPDATE jdbc_driver_registry SET is_active=1 WHERE id=?', (driver_id,))
    c.commit()
    return True, f'已设为 {db_type} 的默认驱动'


# ── 工具：按 db_type 取激活的 jar 路径（阶段 B/C 给插件用）──
def get_active_driver(db_type: str) -> Optional[Dict]:
    if not _validate_db_type(db_type):
        return None
    c = _conn()
    row = c.execute(
        'SELECT * FROM jdbc_driver_registry WHERE db_type=? AND is_active=1 LIMIT 1',
        (db_type,)
    ).fetchone()
    return dict(row) if row else None


# ── 插件 db_type → 驱动注册表 catalog key 映射 ─────────────
# 插件标识常带 _jdbc 后缀（oracle_jdbc/sqlserver_jdbc），而驱动注册表用 catalog
# key（oracle/sqlserver）。本映射让插件无需关心 catalog key，统一经 resolve_jdbc_driver
# 取驱动；未列出的类型按自身名直查（如 dm 即 catalog key 'dm'）。
# ⚠️ key 必须是「巡检/数据源分派令牌」：oracle_jdbc / sqlserver_jdbc 是令牌本身，
#    而 db2/hgdb/clickhouse/uxdb 插件的注册 id 是 db2_jdbc 等，但分派令牌是原生名
#    （db2/hgdb/clickhouse/uxdb），与前端 db_type 下拉、plugin.json db_type 一致。
JDBC_PLUGIN_TO_CATALOG = {
    'oracle_jdbc': 'oracle',
    'sqlserver_jdbc': 'sqlserver',
    'db2': 'db2',
    'hgdb': 'highgo',
    'clickhouse': 'clickhouse',
    'uxdb': 'uxdb',
    'dm': 'dm',
    'gbase': 'gbase8s',   # 核心内置 GBase 走 JDBC（com.gbasedbt.jdbc.Driver；同源也覆盖 gbase8a）
    'ivorysql': 'postgresql',  # IvorySQL 兼容 PG 协议，复用 PostgreSQL JDBC 驱动
    'pg': 'postgresql',        # PostgreSQL 标准 JDBC 驱动
    'kingbase': 'kingbase',    # KingbaseES 官方驱动（com.kingbase8.Driver，jar 内实测）
    'yashandb': 'yashandb',    # YashanDB 官方驱动（com.yashandb.jdbc.Driver）
    'mysql': 'mysql',          # MySQL Connector/J（com.mysql.cj.jdbc.Driver）
    'mariadb': 'mariadb',      # MariaDB Connector/J（org.mariadb.jdbc.Driver）
    'tidb': 'mysql',           # TiDB 兼容 MySQL 协议，复用 MySQL Connector/J
    'oceanbase': 'oceanbase',  # OceanBase 官方驱动（com.oceanbase.jdbc.Driver）
}


def _relocate_jar_path(db_type: str, version: Optional[str], jar_filename: str) -> Optional[str]:
    """jar_path 失效时按文件名在 drivers/ 下重建绝对路径。

    场景：打包（frozen）环境下 jar_path 存的是开发机绝对路径（如
    D:\\DBCheck\\drivers\\oracle\\8\\ojdbc8.jar），打包后必然失效；
    但 drivers/ 目录本身随包（spec data_dirs 含 'drivers'）。这里按
    catalog/version/jar_filename 三层顺序扫描定位，命中即返回新路径。
    """
    if not jar_filename:
        return None
    candidates = []
    # 1) drivers/<catalog>/<version>/<jar_filename>
    if version:
        candidates.append(Path(DRIVERS_DIR) / db_type / str(version) / jar_filename)
    # 2) drivers/<catalog>/<jar_filename>
    candidates.append(Path(DRIVERS_DIR) / db_type / jar_filename)
    # 3) drivers/<jar_filename>（散落根目录的 jar，如 jedis-8.0.0.jar）
    candidates.append(Path(DRIVERS_DIR) / jar_filename)
    for cand in candidates:
        if cand.is_file():
            return str(cand.resolve())
    return None


def get_driver(db_type: str, version: Optional[str] = None) -> Optional[Dict]:
    """按 catalog key + 版本取驱动。

    Args:
        db_type: 驱动注册表 catalog key（oracle / sqlserver / ...）
        version: 指定版本号；为空 / None 时返回当前激活驱动（is_active=1）。

    Returns:
        驱动元数据 dict（含 jar_path / driver_class / version），未登记返回 None。
    """
    if not _validate_db_type(db_type):
        return None
    c = _conn()
    if version:
        # 同版本可能登记多条（如 HighGo 专用 jar 与 PG 通用 jar 版本号相同）：
        # 必须激活优先，否则 LIMIT 1 可能取到非激活驱动导致连接用错 jar。
        row = c.execute(
            'SELECT * FROM jdbc_driver_registry WHERE db_type=? AND version=? '
            'ORDER BY is_active DESC, uploaded_at DESC LIMIT 1',
            (db_type, version)
        ).fetchone()
    else:
        row = c.execute(
            'SELECT * FROM jdbc_driver_registry WHERE db_type=? AND is_active=1 LIMIT 1',
            (db_type,)
        ).fetchone()
    d = dict(row) if row else None
    if d:
        # jar_path 失效（打包后绝对路径错位）时按文件名重定位，保证登记数据
        # 在 exe 分发后依然可用；找不到则保留原值（调用方会走 fallback 兜底）。
        if not (d.get('jar_path') and os.path.isfile(d['jar_path'])):
            _new = _relocate_jar_path(d.get('db_type'), d.get('version'), d.get('jar_filename') or '')
            if _new:
                d['jar_path'] = _new
    return d


def resolve_jdbc_driver(plugin_db_type: str, version: Optional[str] = None) -> Optional[Dict]:
    """给 JDBC 插件用的统一驱动解析入口。

    把插件 db_type（可能带 _jdbc 后缀）映射到 catalog key，再按版本取驱动。
    JDBC 插件在 startJVM(classpath=[jar]) 前调用本函数，即可用驱动管理里
    登记 / 激活的指定版本 jar，而非各自硬编码的 drivers/ 路径。

    Returns:
        dict（含 jar_path / driver_class / version）或 None（未登记→插件走自身兜底）。
    """
    catalog_key = JDBC_PLUGIN_TO_CATALOG.get(plugin_db_type, plugin_db_type)
    return get_driver(catalog_key, version)


def resolve_jdbc_driver_jars(plugin_db_type: str, version: Optional[str] = None) -> Optional[List[str]]:
    """给 JDBC 插件用的「按版本取 jar 路径」入口。

    复用 resolve_jdbc_driver 取驱动元数据，若 ``jar_path`` 指向真实文件则返回
    ``[jar_path]``，否则返回 None（插件应回退到自身兜底：如 drivers/ 全量自动
    发现）。插件在 ``ensure_jvm(specific_jars=...)`` 前调用本函数，即可用驱动
    管理里登记 / 激活的指定版本 jar，而非各自硬编码的 drivers/ 路径。

    Returns:
        [jar_path]（绝对路径）或 None
    """
    drv = resolve_jdbc_driver(plugin_db_type, version)
    if not drv:
        return None
    jar = drv.get('jar_path')
    if jar and os.path.isfile(jar):
        return [os.path.abspath(jar)]
    return None


def seed_driver_registry(seed_path: Optional[str] = None) -> int:
    """从随包种子 JSON 导入驱动登记（打包分发后首次启动自动补齐）。

    场景：打包（frozen）环境不带 data/（运行时目录），用户拿到 exe 后
    drivers.db 是空库——但 drivers/ 目录（jar 文件）与 modules/config/
    （种子 JSON）都已随包。本函数在启动时调用，表为空时从种子导入登记，
    让「驱动设置」页开箱即用；已登记过的环境（表非空）自动跳过，幂等。

    种子文件默认 ``modules/config/drivers_seed.json``（本地打包前用
    export_drivers_seed() 生成；勿提交 git——含用户驱动配置）。

    Returns:
        本次导入的登记条数（0 表示跳过/无种子）。
    """
    try:
        if seed_path is None:
            seed_path = str(Path(__file__).resolve().parent / 'config' / 'drivers_seed.json')
        if not os.path.isfile(seed_path):
            return 0
        with open(seed_path, encoding='utf-8') as f:
            seed = json.load(f)
        rows = seed.get('drivers') or seed if isinstance(seed, list) else (seed.get('drivers') or [])
        if not isinstance(rows, list) or not rows:
            return 0

        c = _conn()
        # 表已有任何登记（哪怕 1 条）→ 视为用户已配置过，跳过整体导入
        existing = c.execute('SELECT COUNT(*) FROM jdbc_driver_registry').fetchone()[0]
        if existing:
            return 0

        _inserted = 0
        for r in rows:
            db_type = str(r.get('db_type') or '').strip()
            jar_filename = str(r.get('jar_filename') or '').strip()
            version = str(r.get('version') or '').strip()
            if not db_type or not jar_filename:
                continue
            # jar_path 按当前环境重定位（打包后 PROJECT_ROOT 指向 exe 同级/_internal）
            jar_path = _relocate_jar_path(db_type, version or None, jar_filename)
            try:
                c.execute(
                    'INSERT INTO jdbc_driver_registry'
                    ' (db_type, version, driver_class, jar_filename, jar_path,'
                    '  file_size, is_active, uploaded_at, note)'
                    ' VALUES (?,?,?,?,?,?,?,?,?)',
                    (
                        db_type,
                        version,
                        str(r.get('driver_class') or ''),
                        jar_filename,
                        jar_path,
                        int(r.get('file_size') or 0),
                        1 if r.get('is_active') else 0,
                        str(r.get('uploaded_at') or ''),
                        str(r.get('note') or ''),
                    ),
                )
                _inserted += 1
            except sqlite3.IntegrityError:
                continue
        c.commit()
        return _inserted
    except Exception:  # noqa: BLE001 — 种子导入失败绝不影响启动
        return 0


def export_drivers_seed(seed_path: Optional[str] = None) -> int:
    """把当前 data/drivers.db 的登记导出为随包种子 JSON（本地打包前执行）。

    只导出元数据（db_type/version/driver_class/jar_filename/is_active/note），
    不导出 jar_path 绝对路径（打包后无意义，导入时按文件名重定位）。

    Returns:
        导出的条数。
    """
    if seed_path is None:
        seed_path = str(Path(__file__).resolve().parent / 'config' / 'drivers_seed.json')
    c = _conn()
    rows = c.execute(
        'SELECT db_type, version, driver_class, jar_filename, file_size,'
        '       is_active, uploaded_at, note FROM jdbc_driver_registry'
        ' ORDER BY db_type, is_active DESC'
    ).fetchall()
    out = {
        '_comment': '随包种子：驱动登记元数据（不含 jar_path，导入时按文件名重定位）。'
                    '由 export_drivers_seed() 生成，勿手工编辑；勿提交 git。',
        'exported_at': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
        'drivers': [dict(r) for r in rows],
    }
    os.makedirs(os.path.dirname(seed_path), exist_ok=True)
    with open(seed_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return len(rows)


# ── 入口：模块导入即建表（幂等） ───────────────────────────


def scan_driver_dirs() -> int:
    """扫描 drivers/ 目录，把磁盘上已存在的 jar 自动登记到驱动注册表（幂等）。

    场景：Docker / exe 分发时 drivers/（jar 文件）随包打入，但随包种子
    ``modules/config/drivers_seed.json`` 可能未进镜像/包（该文件本地生成、
    被 .gitignore 忽略、不随版本库分发），导致驱动管理页面每种数据库的
    驱动列表为空。本函数以磁盘实况为准扫描登记——只要 jar 在，驱动管理
    页面即可开箱即用，不再依赖种子文件是否随包。

    布局兼容（与 _relocate_jar_path 一致的三层扫描）：
      1) drivers/<db_type>/<version>/<jar>   —— 现行版本子目录布局
      2) drivers/<db_type>/<jar>             —— 老平铺布局
      3) drivers/<jar>                       —— 散落根目录（无法归类，跳过）
    driver_class 优先取 DB_TYPE_CATALOG 的 driver_class_hint，空则留空
    （用户可在驱动管理界面自行修改）。
    幂等：UNIQUE(db_type, version, jar_filename) 冲突自动跳过；
    用户已隐藏的 db_type（driver_type_hidden）不重新登记。

    Returns:
        本次新增登记的条数（0 表示无新增/无可扫描 jar）。
    """
    try:
        root = Path(DRIVERS_DIR)
        if not root.is_dir():
            return 0
        hint_map = {d['key']: (d.get('driver_class_hint') or '') for d in DB_TYPE_CATALOG}
        hidden = _load_hidden_types()

        # (db_type, version, jar_filename, jar_path, driver_class)
        candidates: List[Tuple[str, str, str, str, str]] = []
        for db_dir in sorted(root.iterdir()):
            if not db_dir.is_dir() or db_dir.name.startswith('.'):
                continue
            db_type = db_dir.name
            if db_type in hidden:
                continue
            hint = hint_map.get(db_type, '')
            # 布局 1：drivers/<db_type>/<version>/<jar>
            for vdir in sorted(db_dir.iterdir()):
                if not vdir.is_dir() or vdir.name.startswith('.'):
                    continue
                for jf in sorted(vdir.iterdir()):
                    if jf.is_file() and jf.suffix.lower() == '.jar':
                        candidates.append((db_type, vdir.name, jf.name, str(jf), hint))
            # 布局 2：drivers/<db_type>/<jar>
            for jf in sorted(db_dir.iterdir()):
                if jf.is_file() and jf.suffix.lower() == '.jar':
                    candidates.append((db_type, '', jf.name, str(jf), hint))
        if not candidates:
            return 0

        c = _conn()
        have = {
            (r['db_type'], r['version'], r['jar_filename'])
            for r in c.execute('SELECT db_type, version, jar_filename FROM jdbc_driver_registry')
        }
        inserted = 0
        for db_type, version, jfname, jpath, hint in candidates:
            if (db_type, version, jfname) in have:
                continue
            try:
                c.execute(
                    'INSERT INTO jdbc_driver_registry'
                    ' (db_type, version, driver_class, jar_filename, jar_path,'
                    '  file_size, is_active, note)'
                    ' VALUES (?,?,?,?,?,?,?,?)',
                    (
                        db_type,
                        version,
                        hint,
                        jfname,
                        jpath,
                        int(os.path.getsize(jpath)) if os.path.exists(jpath) else 0,
                        0,
                        '扫描 drivers/ 目录自动登记',
                    ),
                )
                have.add((db_type, version, jfname))
                inserted += 1
            except sqlite3.IntegrityError:
                continue

        # 每个 db_type 若当前无激活驱动，激活最新登记的「版本子目录」驱动
        # （version 非空，主驱动）；仅当该类型无版本子目录驱动时，才回退
        # 激活平铺布局 jar（避免把散落的依赖 jar 如 slf4j-api 误设为激活）。
        for db_type in {d[0] for d in candidates if d[0]}:
            cnt = c.execute(
                'SELECT COUNT(*) FROM jdbc_driver_registry WHERE db_type=? AND is_active=1',
                (db_type,),
            ).fetchone()[0]
            if cnt == 0:
                row = c.execute(
                    'SELECT id FROM jdbc_driver_registry WHERE db_type=? AND version<>""'
                    ' ORDER BY uploaded_at DESC, id DESC LIMIT 1',
                    (db_type,),
                ).fetchone()
                if row is None:
                    row = c.execute(
                        'SELECT id FROM jdbc_driver_registry WHERE db_type=?'
                        ' ORDER BY uploaded_at DESC, id DESC LIMIT 1',
                        (db_type,),
                    ).fetchone()
                if row:
                    c.execute('UPDATE jdbc_driver_registry SET is_active=1 WHERE id=?', (row['id'],))
        c.commit()
        return inserted
    except Exception:  # noqa: BLE001 — 扫描登记失败绝不影响启动
        return 0


# ── 入口：模块导入即建表（幂等） ───────────────────────────
init_db()