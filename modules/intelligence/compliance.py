# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
modules/intelligence/compliance.py — 信创合规态势引擎（P2）

职责：
  - 对纳管实例做「信创化程度」盘点，输出态势看板 + 合规报告。
  - 判定口径：每条 db_type 在 driver_registry.DB_TYPE_CATALOG 有 xinchuang 权威默认，
    用户可在前端覆盖（xinchuang_overrides 表），覆盖优先。
  - 评分：加权合规率（命中「生产/核心/主库/prod/core/primary」标签的实例权重更高，
    因其替换风险与业务影响更大），0–100 分。该加权为启发式，已在报告注明。
  - 待替换清单：foreign 实例 + 国产替代迁移决策（REPLACEMENT_GUIDE：同源/兼容/适配、改动量、差异、理由）。

读模型：直接只读 instances.db（与 access.py 取数据源口径一致），不触发 InstanceManager 的重流程。
"""

import os
import json
import sqlite3
import threading
from typing import List, Dict, Any, Optional, Tuple

from modules.core.paths import DATA_DIR, PRO_DATA_DIR
from modules.driver_registry import xinchuang_default, normalize_db_type, DB_TYPE_KEYS, DB_TYPE_CATALOG

# ── 常量 ──────────────────────────────────────────────
COMPLIANCE_DB = str(DATA_DIR / 'compliance.db')

# 命中这些标签的实例视为「生产/核心」，在合规评分中权重加倍。
_PROD_TAG_KEYWORDS = ('生产', '核心', '主库', 'prod', 'core', 'primary', 'primary')

# 国外（foreign）数据库 → 国产替代迁移决策元数据。
# 每个候选含：target(候选 db_type)、relation(同源/兼容/适配)、compat(兼容度 高/中/低)、
# effort(迁移改动量 少/中/多)、reason(一句话推荐理由)、diff(主要差异点)、priority(同 source 内推荐序，1=首选)。
# - 同源：同内核分支（如 PostgreSQL→瀚高/openGauss/IvorySQL），改动少；
# - 兼容：独立代码但语法/协议层兼容（如 Oracle→达梦、MySQL→TiDB）；
# - 适配：需重写评估（如 MongoDB、ClickHouse）。
# certified(国测) 不在此静态存储，运行时按 is_certified() 动态计算（用户可在「库型维护」维护）。
REPLACEMENT_GUIDE: Dict[str, List[Dict[str, Any]]] = {
    'oracle': [
        {'target': 'dm', 'relation': '兼容', 'compat': '高', 'effort': '中',
         'reason': '语法高度兼容 Oracle（PL/SQL、包、存储过程），党政金融案例多',
         'diff': '分布式/高可用需单独评估；个别系统包需改写', 'priority': 1},
        {'target': 'kingbase', 'relation': '兼容', 'compat': '中', 'effort': '中',
         'reason': '提供 Oracle 兼容模式，迁移工具链较全',
         'diff': '部分 Oracle 专属函数需适配；调优参数不同', 'priority': 2},
        {'target': 'yashandb', 'relation': '兼容', 'compat': '中', 'effort': '中',
         'reason': '崖山 DB 主打 Oracle 高度兼容，集中式+分布式一体',
         'diff': '生态相对年轻，第三方工具需验证', 'priority': 3},
        {'target': 'oceanbase', 'relation': '兼容', 'compat': '中', 'effort': '多',
         'reason': 'MySQL/Oracle 双兼容，分布式原生',
         'diff': '分布式架构改造大；运维体系不同', 'priority': 4},
    ],
    'mysql': [
        {'target': 'tidb', 'relation': '兼容', 'compat': '高', 'effort': '少',
         'reason': 'MySQL 协议兼容，应用基本无感迁移',
         'diff': '部分 MySQL 特性不支持（如部分存储引擎）；分布式事务', 'priority': 1},
        {'target': 'oceanbase', 'relation': '兼容', 'compat': '中', 'effort': '中',
         'reason': 'MySQL 兼容模式，金融级分布式',
         'diff': '运维与调优体系不同', 'priority': 2},
        {'target': 'doris', 'relation': '适配', 'compat': '中', 'effort': '多',
         'reason': '分析型场景替代，OLAP 能力强',
         'diff': '非事务型，TP 业务需另选；SQL 方言差异', 'priority': 3},
    ],
    'mariadb': [
        {'target': 'tidb', 'relation': '兼容', 'compat': '高', 'effort': '少',
         'reason': '与 MySQL 同协议栈，TiDB 可平滑承接',
         'diff': '同 MySQL 注意事项；部分引擎特性不支持', 'priority': 1},
        {'target': 'oceanbase', 'relation': '兼容', 'compat': '中', 'effort': '中',
         'reason': 'MySQL 兼容模式承接', 'diff': '运维体系不同', 'priority': 2},
    ],
    'postgresql': [
        {'target': 'opengauss', 'relation': '同源', 'compat': '高', 'effort': '少',
         'reason': '均基于 PostgreSQL 内核（同源分支），语法高度一致',
         'diff': '企业级特性/驱动细节有差异；部分扩展需评估', 'priority': 1},
        {'target': 'highgo', 'relation': '同源', 'compat': '高', 'effort': '少',
         'reason': '瀚高基于 PostgreSQL，高度兼容，党政案例多',
         'diff': '个别扩展/插件需确认', 'priority': 2},
        {'target': 'ivorysql', 'relation': '同源', 'compat': '高', 'effort': '少',
         'reason': 'IvorySQL 是 PostgreSQL 兼容分支并兼容 Oracle',
         'diff': '生态较新，需验证', 'priority': 3},
    ],
    'sqlserver': [
        {'target': 'dm', 'relation': '适配', 'compat': '中', 'effort': '多',
         'reason': '达梦可承接 OLTP，T-SQL 差异较大',
         'diff': 'T-SQL 存储过程、函数需较大改写', 'priority': 1},
        {'target': 'kingbase', 'relation': '适配', 'compat': '中', 'effort': '多',
         'reason': '金仓通用兼容可承接部分 SQL Server 负载',
         'diff': 'T-SQL 方言差异，迁移工作量高', 'priority': 2},
    ],
    'db2': [
        {'target': 'dm', 'relation': '适配', 'compat': '中', 'effort': '多',
         'reason': '达梦可承接 OLTP，SQL 方言需适配',
         'diff': '存储过程/工具链差异大', 'priority': 1},
        {'target': 'kingbase', 'relation': '适配', 'compat': '中', 'effort': '多',
         'reason': '金仓通用兼容承接', 'diff': 'DB2 专有特性需评估', 'priority': 2},
    ],
    'sybase': [
        {'target': 'dm', 'relation': '适配', 'compat': '中', 'effort': '多',
         'reason': '达梦承接 Sybase ASE 的 OLTP 场景',
         'diff': 'T-SQL 差异，需改写', 'priority': 1},
    ],
    'informix': [
        {'target': 'dm', 'relation': '适配', 'compat': '中', 'effort': '多',
         'reason': '达梦可承接 Informix 交易场景',
         'diff': '4GL/存储过程需重写', 'priority': 1},
        {'target': 'kingbase', 'relation': '适配', 'compat': '中', 'effort': '多',
         'reason': '金仓通用兼容承接', 'diff': '专有特性需评估', 'priority': 2},
    ],
    'greenplum': [
        {'target': 'opengauss', 'relation': '适配', 'compat': '中', 'effort': '中',
         'reason': 'openGauss 分布式版可承接 MPP 分析',
         'diff': 'MPP 架构与函数差异', 'priority': 1},
        {'target': 'highgo', 'relation': '适配', 'compat': '中', 'effort': '中',
         'reason': '瀚高企业版可承接分析负载', 'diff': '分布式能力需评估', 'priority': 2},
    ],
    'clickhouse': [
        {'target': 'starrocks', 'relation': '适配', 'compat': '中', 'effort': '中',
         'reason': 'StarRocks 国产 MPP，亚秒级分析，语法接近',
         'diff': '部分 CH 函数/Keeper 机制不同', 'priority': 1},
        {'target': 'doris', 'relation': '适配', 'compat': '中', 'effort': '中',
         'reason': 'Doris 国产 OLAP，兼容 MySQL 协议易接入',
         'diff': '部分 CH 专属函数需改写', 'priority': 2},
    ],
    'sqlite3': [],
    'mongodb': [],
    'elasticsearch': [],
    'hive': [],
}

# 暂无明确单库替代的国外类型：给出方向性建议（实际选型需结合业务读写模型评估）。
REPLACEMENT_EMPTY_NOTE: Dict[str, str] = {
    'sqlite3': '嵌入式轻量库，建议评估国产嵌入式/轻量关系库，或随应用整体迁移',
    'mongodb': '文档型 NoSQL，建议评估国产多模/文档数据库，并核对驱动与聚合语法',
    'elasticsearch': '检索引擎，建议评估国产搜索/向量数据库，关注分词与集群能力',
    'hive': '离线数仓，建议评估国产湖仓一体方案，关注 SQL 方言与生态工具',
}

# 仅在报告里展示给用户看的「中文名」缓存（避免重复查 catalog）。
_DB_TYPE_NAME_ZH = {d['key']: d['name_zh'] for d in DB_TYPE_CATALOG}

# 替代库展示名（诊断中心联动提示条用，优先于 catalog 原文，保证中文直观）。
REPLACEMENT_DISPLAY = {
    'dm': '达梦', 'kingbase': '金仓', 'yashandb': '崖山', 'oceanbase': 'OceanBase',
    'tidb': 'TiDB', 'doris': 'Doris', 'opengauss': 'openGauss', 'highgo': '瀚高',
    'ivorysql': 'IvorySQL', 'gbase': 'GBase', 'uxdb': '优炫', 'shentong': '神通',
}

# ── 国测（国家信息安全测评中心·安全可靠测评，原信创目录）通过名单 ──
# 默认基于公开信息：达梦(DM)、人大金仓(Kingbase)、OceanBase、神舟通用(神通)、
# 南大通用(GBase)、瀚高(HighGo) 等均已通过国家级安全可靠测评。
# 名单随官方公布动态更新，用户可在「信创合规 → 库型维护」界面维护，
# 覆盖默认常量（见 xinchuang_certified 表 + is_certified()）。
GUOCE_CERTIFIED_DEFAULT = {
    'dm', 'kingbase', 'oceanbase', 'shentong', 'gbase', 'highgo',
}

# 国测（国家信息安全测评中心·安全可靠测评，原信创目录）维护说明。
# 默认名单基于公开信息，用户在「信创合规 → 库型维护」界面按最新官方公布维护。
GUOCE_NOTE = ('国测 = 国家信息安全测评中心·安全可靠测评（原信创目录）。默认名单基于公开信息，'
              '请在「信创合规 → 库型维护」界面按最新官方公布维护。')

# ── 自定义库型初始数据（固化为内置初始数据，不在 driver_registry.catalog 内）──
# 来源：用户手工添加并标记国测的国产库型，固化为首次启动即存在的初始数据，
# 用户可在「库型维护」界面继续增删改。
# xinchuang=1 国产 / 0 国外；certified=1 已通过国测。
CUSTOM_DB_TYPE_SEED = [
    {'db_type': 'Vastbase', 'name_zh': '海量数据库', 'xinchuang': 1, 'certified': 1},
    {'db_type': 'TDSQL', 'name_zh': '腾讯 TDSQL', 'xinchuang': 1, 'certified': 1},
    {'db_type': 'He3DB', 'name_zh': '热璞数据库', 'xinchuang': 1, 'certified': 1},
    {'db_type': 'HaloDB', 'name_zh': 'Halo 数据库', 'xinchuang': 1, 'certified': 1},
    {'db_type': 'NetrixDB', 'name_zh': 'Netrix 数据库', 'xinchuang': 1, 'certified': 1},
]

_LOCK = threading.Lock()


# ── SQLite 连接 ───────────────────────────────────────
def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(COMPLIANCE_DB), exist_ok=True)
    c = sqlite3.connect(COMPLIANCE_DB, timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    return c


def init_db() -> None:
    """建表（幂等）。

    - xinchuang_overrides：仅用于内置 catalog 库型的信创判定覆盖（1 国产 / 0 国外）。
    - xinchuang_certified：仅用于内置 catalog 库型的「国测通过」标记（覆盖默认常量）。
    - xinchuang_custom_types：统一管理「用户自定义库型」（不在 driver_registry.catalog 内），
      含中文名(name_zh) + 信创分类(xinchuang) + 国测(certified) 三维度。
      启动时把历史旧表中非 catalog 的自定义记录迁移进来，并把 CUSTOM_DB_TYPE_SEED 固化。
    """
    c = _conn()
    try:
        c.execute('''
        CREATE TABLE IF NOT EXISTS xinchuang_overrides (
            db_type    TEXT PRIMARY KEY NOT NULL,
            value      INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        ''')
        c.execute('''
        CREATE TABLE IF NOT EXISTS xinchuang_certified (
            db_type    TEXT PRIMARY KEY NOT NULL,
            value      INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        ''')
        c.execute('''
        CREATE TABLE IF NOT EXISTS xinchuang_custom_types (
            db_type    TEXT PRIMARY KEY NOT NULL,
            name_zh    TEXT NOT NULL DEFAULT '',
            xinchuang  INTEGER NOT NULL DEFAULT 1,
            certified  INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        ''')
        _migrate_and_seed_custom_types(c)
        c.commit()
    finally:
        c.close()


def _migrate_and_seed_custom_types(c: sqlite3.Connection) -> None:
    """固化 CUSTOM_DB_TYPE_SEED（优先标准大小写），再把历史 xinchuang_certified /
    xinchuang_overrides 中「非 catalog 的自定义库型」迁移进 xinchuang_custom_types
    （大小写不敏感去重，seed 优先），随后删除旧两表中非 catalog 的记录，避免双源冲突。"""
    known_lower = {d['key'].lower() for d in DB_TYPE_CATALOG}
    existing = {r['db_type'].lower() for r in c.execute('SELECT db_type FROM xinchuang_custom_types').fetchall()}

    # 1) 先固化种子（标准大小写优先，避免被历史小写记录覆盖）
    for s in CUSTOM_DB_TYPE_SEED:
        kl = normalize_db_type(s['db_type']).lower()
        if kl and kl not in existing:
            c.execute('INSERT OR IGNORE INTO xinchuang_custom_types (db_type, name_zh, xinchuang, certified) VALUES (?,?,?,?)',
                      (s['db_type'], s['name_zh'], s['xinchuang'], s['certified']))
            existing.add(kl)

    # 2) 迁移旧 certified 表（value=1 且非 catalog，且 seed 未覆盖）→ 国产 + 国测
    for (k,) in c.execute('SELECT db_type FROM xinchuang_certified WHERE value=1').fetchall():
        kl = normalize_db_type(k).lower()
        if kl and kl not in known_lower and kl not in existing:
            c.execute('INSERT OR IGNORE INTO xinchuang_custom_types (db_type, name_zh, xinchuang, certified) VALUES (?,?,1,1)',
                      (k, k))
            existing.add(kl)

    # 3) 迁移旧 overrides 表（非 catalog，记录国产/国外判定）
    for r in c.execute('SELECT db_type, value FROM xinchuang_overrides').fetchall():
        k, v = r['db_type'], r['value']
        kl = normalize_db_type(k).lower()
        if kl and kl not in known_lower:
            if kl in existing:
                c.execute('UPDATE xinchuang_custom_types SET xinchuang=? WHERE lower(db_type)=?',
                          (1 if v else 0, kl))
            else:
                c.execute('INSERT OR IGNORE INTO xinchuang_custom_types (db_type, name_zh, xinchuang, certified) VALUES (?,?,?,0)',
                          (k, k, 1 if v else 0))
                existing.add(kl)

    # 4) 清理旧两表中非 catalog 的自定义记录（已迁移，避免双源）
    if known_lower:
        ph = ','.join('?' * len(known_lower))
        c.execute(f'DELETE FROM xinchuang_certified WHERE lower(db_type) NOT IN ({ph})', tuple(known_lower))
        c.execute(f'DELETE FROM xinchuang_overrides WHERE lower(db_type) NOT IN ({ph})', tuple(known_lower))


def get_override_map() -> Dict[str, bool]:
    """返回 {db_type(lower): bool} 的覆盖映射。"""
    init_db()
    c = _conn()
    try:
        rows = c.execute('SELECT db_type, value FROM xinchuang_overrides').fetchall()
        return {r['db_type'].lower(): bool(r['value']) for r in rows}
    finally:
        c.close()


def set_override(db_type: str, value: bool) -> Dict[str, Any]:
    if not db_type:
        return {'ok': False, 'error': 'db_type 不能为空'}
    dt = normalize_db_type(db_type)
    init_db()
    c = _conn()
    try:
        c.execute(
            'INSERT INTO xinchuang_overrides (db_type, value, updated_at) '
            'VALUES (?, ?, datetime(\'now\', \'localtime\')) '
            'ON CONFLICT(db_type) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
            (dt, 1 if value else 0),
        )
        c.commit()
        return {'ok': True, 'db_type': dt, 'value': bool(value)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        c.close()


def clear_override(db_type: str) -> Dict[str, Any]:
    if not db_type:
        return {'ok': False, 'error': 'db_type 不能为空'}
    dt = normalize_db_type(db_type)
    c = _conn()
    try:
        cur = c.execute('DELETE FROM xinchuang_overrides WHERE lower(db_type)=?', (dt.lower(),))
        c.commit()
        return {'ok': True, 'db_type': dt, 'cleared': cur.rowcount > 0}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        c.close()


def get_certified_map() -> Dict[str, bool]:
    """返回用户维护的国测通过覆盖 {db_type(lower): bool}（用于大小写不敏感查找）。"""
    init_db()
    c = _conn()
    try:
        rows = c.execute('SELECT db_type, value FROM xinchuang_certified').fetchall()
        return {r['db_type'].lower(): bool(r['value']) for r in rows}
    finally:
        c.close()


def is_certified(db_type: str) -> bool:
    """某 db_type 是否通过国测。

    - 内置 catalog 库型：xinchuang_certified 覆盖优先于 GUOCE_CERTIFIED_DEFAULT 默认常量。
    - 自定义库型（不在 catalog）：以 xinchuang_custom_types.certified 为准。
    """
    dt = normalize_db_type(db_type)
    if not dt:
        return False
    ov = get_certified_map()
    if dt.lower() in ov:
        return ov[dt.lower()]
    if dt.lower() in {k.lower() for k in DB_TYPE_KEYS}:
        return dt.lower() in GUOCE_CERTIFIED_DEFAULT
    return _custom_certified(dt)


def set_certified(db_type: str, value: bool) -> Dict[str, Any]:
    if not db_type:
        return {'ok': False, 'error': 'db_type 不能为空'}
    dt = normalize_db_type(db_type)
    init_db()
    c = _conn()
    try:
        c.execute(
            'INSERT INTO xinchuang_certified (db_type, value, updated_at) '
            'VALUES (?, ?, datetime(\'now\', \'localtime\')) '
            'ON CONFLICT(db_type) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
            (dt, 1 if value else 0),
        )
        c.commit()
        return {'ok': True, 'db_type': dt, 'value': bool(value)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        c.close()


def clear_certified(db_type: str) -> Dict[str, Any]:
    if not db_type:
        return {'ok': False, 'error': 'db_type 不能为空'}
    dt = normalize_db_type(db_type)
    c = _conn()
    try:
        cur = c.execute('DELETE FROM xinchuang_certified WHERE lower(db_type)=?', (dt.lower(),))
        c.commit()
        return {'ok': True, 'db_type': dt, 'cleared': cur.rowcount > 0}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        c.close()


def get_custom_types() -> List[Dict[str, Any]]:
    """返回所有用户自定义库型 [{db_type, name_zh, xinchuang, certified}]（保留原始大小写）。"""
    init_db()
    c = _conn()
    try:
        rows = c.execute(
            'SELECT db_type, name_zh, xinchuang, certified FROM xinchuang_custom_types ORDER BY db_type'
        ).fetchall()
        return [
            {'db_type': r['db_type'], 'name_zh': r['name_zh'],
             'xinchuang': bool(r['xinchuang']), 'certified': bool(r['certified'])}
            for r in rows
        ]
    finally:
        c.close()


def add_custom_type(db_type: str, name_zh: str, xinchuang: bool, certified: bool) -> Dict[str, Any]:
    """添加 / 更新一个自定义库型（不在 driver_registry.catalog 内）。

    三维度一次性写入：name_zh(中文名)、xinchuang(1 国产/0 国外)、certified(国测)。
    """
    dt = normalize_db_type(db_type)
    if not dt:
        return {'ok': False, 'error': 'db_type 不能为空'}
    if dt.lower() in {k.lower() for k in DB_TYPE_KEYS}:
        return {'ok': False, 'error': '该库型已在内置目录中，无需自定义'}
    # 国外库不允许标记国测（国测 = 国家信创测评，仅面向国产数据库），前端已联动禁用，
    # 此处再次兜底，避免接口被直接调用绕过。
    if not xinchuang:
        certified = False
    init_db()
    c = _conn()
    try:
        c.execute(
            'INSERT INTO xinchuang_custom_types (db_type, name_zh, xinchuang, certified, updated_at) '
            'VALUES (?, ?, ?, ?, datetime(\'now\', \'localtime\')) '
            'ON CONFLICT(db_type) DO UPDATE SET name_zh=excluded.name_zh, '
            'xinchuang=excluded.xinchuang, certified=excluded.certified, updated_at=excluded.updated_at',
            (dt, (name_zh or dt).strip(), 1 if xinchuang else 0, 1 if certified else 0),
        )
        c.commit()
        return {'ok': True, 'db_type': dt}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        c.close()


def set_custom_certified(db_type: str, value: bool) -> Dict[str, Any]:
    """切换某自定义库型的国测标记（仅对 xinchuang_custom_types 生效）。

    国外库（xinchuang=0）不允许标记国测，value=True 时拒绝（前端已不显示该按钮，
    此处兜底避免接口被直接调用绕过）。
    """
    dt = normalize_db_type(db_type)
    if not dt:
        return {'ok': False, 'error': 'db_type 不能为空'}
    init_db()
    c = _conn()
    try:
        row = c.execute('SELECT xinchuang FROM xinchuang_custom_types WHERE lower(db_type)=?',
                        (dt.lower(),)).fetchone()
        if row is None:
            return {'ok': False, 'error': '该自定义库型不存在'}
        if not row['xinchuang'] and value:
            return {'ok': False, 'error': '国外库不能标记国测'}
        cur = c.execute(
            'UPDATE xinchuang_custom_types SET certified=?, updated_at=datetime(\'now\', \'localtime\') '
            'WHERE lower(db_type)=?',
            (1 if value else 0, dt.lower()),
        )
        c.commit()
        return {'ok': True, 'db_type': dt, 'updated': cur.rowcount > 0}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        c.close()


def clear_custom_type(db_type: str) -> Dict[str, Any]:
    """删除一个自定义库型（从 xinchuang_custom_types 移除）。"""
    dt = normalize_db_type(db_type)
    if not dt:
        return {'ok': False, 'error': 'db_type 不能为空'}
    init_db()
    c = _conn()
    try:
        cur = c.execute('DELETE FROM xinchuang_custom_types WHERE lower(db_type)=?', (dt.lower(),))
        c.commit()
        return {'ok': True, 'db_type': dt, 'cleared': cur.rowcount > 0}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        c.close()


def _custom_classify(dt: str):
    """返回自定义库型的信创判定（True 国产 / False 国外）；不存在返回 None。"""
    init_db()
    c = _conn()
    try:
        row = c.execute('SELECT xinchuang FROM xinchuang_custom_types WHERE lower(db_type)=?',
                        (dt.lower(),)).fetchone()
        return bool(row['xinchuang']) if row else None
    finally:
        c.close()


def _custom_certified(dt: str) -> bool:
    """返回自定义库型是否国测（不存在返回 False）。"""
    init_db()
    c = _conn()
    try:
        row = c.execute('SELECT certified FROM xinchuang_custom_types WHERE lower(db_type)=?',
                        (dt.lower(),)).fetchone()
        return bool(row['certified']) if row else False
    finally:
        c.close()


def _suggestions_for(dt: str) -> List[Dict[str, Any]]:
    """返回某被替换库 → 候选国产库的迁移决策元数据。

    每条含：key(候选 db_type)、label(中文名)、certified(国测)、
    relation(同源/兼容/适配)、compat(兼容度)、effort(改动少/中/多)、
    diff(主要差异)、reason(推荐理由)、priority(同 source 内推荐序)。
    默认按 priority 排序；前端可按 同源/国测/改动少 重新排序。
    """
    raw = REPLACEMENT_GUIDE.get(dt, [])
    detail = [
        {
            'key': g['target'],
            'label': REPLACEMENT_DISPLAY.get(g['target'], _DB_TYPE_NAME_ZH.get(g['target'], g['target'])),
            'certified': is_certified(g['target']),
            'relation': g.get('relation', ''),
            'compat': g.get('compat', ''),
            'effort': g.get('effort', ''),
            'diff': g.get('diff', ''),
            'reason': g.get('reason', ''),
            'priority': g.get('priority', 99),
        }
        for g in raw
    ]
    detail.sort(key=lambda x: x['priority'])
    return detail


def classify(db_type: str) -> str:
    """返回 'domestic' / 'foreign' / 'neutral'。

    - 内置 catalog 库型：以 xinchuang_default + xinchuang_overrides 覆盖为基准。
    - 自定义库型（不在 catalog）：以 xinchuang_custom_types.xinchuang 为准。
    - neutral：既不在 catalog 也无自定义记录，无法判定。
    """
    dt = normalize_db_type(db_type)
    if not dt:
        return 'neutral'
    ov = get_override_map()
    if dt.lower() in ov:
        return 'domestic' if ov[dt.lower()] else 'foreign'
    if dt.lower() not in DB_TYPE_KEYS:
        cc = _custom_classify(dt)
        if cc is not None:
            return 'domestic' if cc else 'foreign'
        return 'neutral'
    return 'domestic' if xinchuang_default(dt) else 'foreign'


def tag_db_type(db_type: str) -> Dict[str, Any]:
    """给单个 db_type 打「信创合规」标签，供诊断中心联动展示。

    返回：原始类型、归一化类型、判定（domestic/foreign/neutral）、
    是否国产、国产替代建议（国测优先排序，含 certified 标记）、中文名。
    """
    dt = normalize_db_type(db_type)
    cls = classify(dt)
    detail = _suggestions_for(dt)
    return {
        'db_type': db_type,
        'normalized': dt,
        'classification': cls,
        'is_domestic': cls == 'domestic',
        'is_foreign': cls == 'foreign',
        'replacement': [d['key'] for d in detail],
        'replacement_labels': [d['label'] for d in detail],
        'replacement_detail': detail,
        'label_zh': _DB_TYPE_NAME_ZH.get(dt, dt),
    }


# ── 数据源（只读 instances.db）────────────────────────
def load_instances() -> List[Dict[str, Any]]:
    """读取全部纳管实例（只读 instances.db）。失败返回空列表而非抛错。"""
    path = str(PRO_DATA_DIR / 'instances.db')
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT id, name, db_type, host, "group", tags, enabled FROM instances'
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                'id': r['id'],
                'name': r['name'],
                'db_type': (r['db_type'] or '').lower(),
                'host': r['host'],
                'group': r['group'] or 'default',
                'tags': r['tags'] or '[]',
                'enabled': r['enabled'],
            })
        conn.close()
        return out
    except Exception as e:
        print(f'[compliance] 读取 instances.db 失败: {e}')
        return []


def _weight_of(tags: Any) -> float:
    """生产/核心实例权重加倍。tags 可能是 JSON 字符串或列表。"""
    if not tags:
        return 1.0
    if isinstance(tags, str):
        try:
            tags = json.loads(tags or '[]')
        except Exception:
            tags = []
    if not isinstance(tags, list):
        return 1.0
    low = [str(t).lower() for t in tags]
    for kw in _PROD_TAG_KEYWORDS:
        if kw in low:
            return 2.0
    return 1.0


def build_overview(instances: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """构建信创合规态势总览。"""
    if instances is None:
        instances = load_instances()

    weighted_total = 0.0
    weighted_domestic = 0.0
    by_type: Dict[str, Dict[str, Any]] = {}
    risk_instances: List[Dict[str, Any]] = []
    domestic_n = foreign_n = neutral_n = 0

    for inst in instances:
        dt = normalize_db_type(inst.get('db_type'))
        cls = classify(dt)
        w = _weight_of(inst.get('tags'))
        weighted_total += w
        if cls == 'domestic':
            weighted_domestic += w
            domestic_n += 1
        elif cls == 'foreign':
            foreign_n += 1
            risk_instances.append({
                'id': inst.get('id'),
                'name': inst.get('name'),
                'host': inst.get('host'),
                'db_type': dt,
                'db_type_name': _DB_TYPE_NAME_ZH.get(dt, dt),
                'group': inst.get('group') or 'default',
                'enabled': inst.get('enabled', 1),
                'tags': inst.get('tags'),
                'suggestions': _suggestions_for(dt),
                'empty_note': REPLACEMENT_EMPTY_NOTE.get(dt, ''),
            })
        else:
            neutral_n += 1

        bt = by_type.setdefault(dt, {
            'db_type': dt,
            'name_zh': _DB_TYPE_NAME_ZH.get(dt, dt),
            'total': 0, 'domestic': 0, 'foreign': 0, 'neutral': 0,
            'xinchuang': xinchuang_default(dt) if dt in DB_TYPE_KEYS else None,
        })
        bt['total'] += 1
        bt[cls] += 1

    compliance_rate = round(weighted_domestic / weighted_total * 100, 1) if weighted_total else 0.0
    score = int(round(compliance_rate))

    by_type_list = sorted(by_type.values(), key=lambda x: (-x['total'], x['db_type']))
    # 替代建议中文名（来自国测优先排序后的 detail）
    for ri in risk_instances:
        ri['suggestion_names'] = [s['label'] for s in ri['suggestions']]

    # 国测维护候选（供前端「库型维护」界面使用）：所有内置国产库（xinchuang=True），
    # 另附用户自定义库型中已标记国测的（来自 xinchuang_custom_types），支持清单外维护。
    known = {d['key'] for d in DB_TYPE_CATALOG}
    guoce_options = [
        {'key': d['key'], 'db_type': d['key'], 'name_zh': d['name_zh'], 'certified': is_certified(d['key'])}
        for d in DB_TYPE_CATALOG if d.get('xinchuang')
    ]
    for ct in get_custom_types():
        if ct['certified']:
            guoce_options.append({
                'key': ct['db_type'], 'db_type': ct['db_type'], 'name_zh': ct['name_zh'],
                'certified': True, 'custom': True,
            })

    return {
        'total': len(instances),
        'weighted_total': round(weighted_total, 1),
        'domestic': domestic_n,
        'foreign': foreign_n,
        'neutral': neutral_n,
        'compliance_rate': compliance_rate,
        'score': score,
        'by_type': by_type_list,
        'risk_instances': risk_instances,
        'replacement_map': {k: [g['target'] for g in v] for k, v in REPLACEMENT_GUIDE.items()},
        'guoce_options': guoce_options,
        'guoce_note': GUOCE_NOTE,
        'generated_at': _now(),
    }


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── 报告生成 ─────────────────────────────────────────
def build_report(overview: Optional[Dict[str, Any]] = None, fmt: str = 'html') -> str:
    """生成信创合规报告。fmt='html' 返回自包含 HTML；fmt='json' 返回 JSON 字符串。"""
    if overview is None:
        overview = build_overview()

    if fmt == 'json':
        return json.dumps(overview, ensure_ascii=False, indent=2)

    rate = overview['compliance_rate']
    score = overview['score']
    score_color = '#16A34A' if score >= 70 else ('#D97706' if score >= 40 else '#DC2626')
    score_word = '良好' if score >= 70 else ('一般' if score >= 40 else '偏低')

    # 按类型分布行
    type_rows = ''
    for b in overview['by_type']:
        cls = '国产' if b['xinchuang'] else ('待定' if b['xinchuang'] is None else '国外')
        type_rows += (
            f"<tr><td>{_esc(b['name_zh'])}</td><td>{_esc(b['db_type'])}</td>"
            f"<td>{b['total']}</td><td>{b['domestic']}</td><td>{b['foreign']}</td>"
            f"<td>{b['neutral']}</td><td>{cls}</td></tr>"
        )

    # 待替换清单
    risk_rows = ''
    if overview['risk_instances']:
        for r in overview['risk_instances']:
            if r['suggestions']:
                sugg_parts = []
                for s in r['suggestions']:
                    rel = f" · {_esc(s.get('relation', ''))}" if s.get('relation') else ''
                    eff = f" · 改动{_esc(s.get('effort', ''))}" if s.get('effort') else ''
                    guoce = ' ✓国测' if s.get('certified') else ''
                    sugg_parts.append(
                        f"<div style='margin:3px 0'><b>{_esc(s['label'])}</b>{rel}{eff}{guoce}"
                        f"<div style='color:#6b7280;font-size:12px'>{_esc(s.get('reason', ''))}</div></div>"
                    )
                sugg = ''.join(sugg_parts)
            else:
                sugg = _esc(r.get('empty_note') or '暂无明确单库替代，建议评估国产分布式/多模方案')
            risk_rows += (
                f"<tr><td>{_esc(r['name'])}</td><td>{_esc(r['host'])}</td>"
                f"<td>{_esc(r['db_type_name'])}</td><td>{_esc(r['group'])}</td>"
                f"<td>{sugg}</td></tr>"
            )
    else:
        risk_rows = '<tr><td colspan="5" style="text-align:center;color:#16A34A">✓ 无国外数据库实例，信创化已全部达成</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>信创合规态势报告</title>
<style>
 body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;margin:0;padding:32px;color:#1f2937;background:#f8fafc}}
 h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#6b7280;font-size:13px;margin-bottom:24px}}
 .score{{display:flex;align-items:center;gap:24px;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:22px 26px;margin-bottom:22px}}
 .score .big{{font-size:46px;font-weight:800;line-height:1}} .score .meta{{font-size:13px;color:#6b7280}}
 .grid{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}}
 .card{{flex:1;min-width:130px;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px}}
 .card .n{{font-size:26px;font-weight:800}} .card .l{{font-size:12px;color:#6b7280;margin-top:4px}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;font-size:13px;margin-bottom:22px}}
 th{{background:#f1f5f9;text-align:left;padding:10px 12px;color:#475569;font-weight:600}}
 td{{padding:9px 12px;border-top:1px solid #e5e7eb}}
 h2{{font-size:16px;margin:0 0 10px}}
 .bar{{height:10px;border-radius:6px;background:linear-gradient(90deg,#16A34A 0%,#16A34A {rate}%,#e5e7eb {rate}%)}}

 .note{{font-size:12px;color:#6b7280;line-height:1.7;background:#fff;border:1px dashed #cbd5e1;border-radius:10px;padding:14px 16px}}
</style></head><body>
<h1>信创合规态势报告</h1>
<div class="sub">生成时间：{_esc(overview['generated_at'])} ｜ RaccoonX 浣巡 · 信创合规引擎</div>

<div class="score">
  <div class="big" style="color:{score_color}">{score}</div>
  <div>
    <div style="font-size:14px;font-weight:600">信创合规评分（加权合规率）· {score_word}</div>
    <div class="meta">合规率 {rate}% ｜ 生产/核心实例权重加倍（启发式）</div>
    <div class="bar" style="margin-top:10px;width:260px"></div>
  </div>
</div>

<div class="grid">
  <div class="card"><div class="n">{overview['total']}</div><div class="l">纳管实例总数</div></div>
  <div class="card"><div class="n" style="color:#16A34A">{overview['domestic']}</div><div class="l">国产（信创）</div></div>
  <div class="card"><div class="n" style="color:#DC2626">{overview['foreign']}</div><div class="l">国外（待替换）</div></div>
  <div class="card"><div class="n" style="color:#6b7280">{overview['neutral']}</div><div class="l">待定（未知类型）</div></div>
</div>

<h2>库型分布</h2>
<table><thead><tr><th>类型</th><th>db_type</th><th>总数</th><th>国产</th><th>国外</th><th>待定</th><th>判定</th></tr></thead>
<tbody>{type_rows}</tbody></table>

<h2>国外数据库待替换清单</h2>
<table><thead><tr><th>实例</th><th>主机</th><th>类型</th><th>业务组</th><th>建议国产替代</th></tr></thead>
<tbody>{risk_rows}</tbody></table>

<div class="note">
  <b>说明：</b>① 信创判定以 driver_registry 的权威默认清单为基准，用户可在「信创合规」页对单库类型覆盖（覆盖优先）。
  ② 评分 = 国产实例加权数 / 全部实例加权数 × 100；命中「生产/核心/主库/prod/core/primary」标签的实例权重 ×2，
  因其替换风险与业务影响更大，该加权为启发式口径。③ 替代建议为方向性参考，实际选型需结合版本、生态与兼容性评估。
</div>
</body></html>"""
    return html


def _esc(s: Any) -> str:
    if s is None:
        return ''
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))
