#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
MongoDB / Redis 原生驱动采集通道（监控大屏）。

JDBC/DBAPI 通道覆盖不了的 NoSQL 类型（mongodb / redis / redis-cluster）
走各自官方 Python 驱动（pymongo / redis-py）在主进程内直连采集：
  - 连接数 / 客户端数（mongo serverStatus.connections、redis INFO clients）
  - QPS 计数（mongo opcounters、redis total_commands_processed → 统一 'questions'，
    供 screen_metrics._rate 差值计算，与 SQL 库共用同一套速率逻辑）
  - 容量（mongo listDatabases sizeOnDisk、redis 内存使用率）
  - 复制延迟（mongo replSetGetStatus 主从 optime 差、redis INFO replication slave lag）
  - 慢查条数（redis SLOWLOG LEN；mongo system.profile 默认关闭 → 0）

单实例采集失败返回结构化 err（经 engine._friendly_db_error 翻译为可读文案），
不影响其它实例；不抛异常（调用方无需 try 包裹）。

注：主进程内 pymongo/redis-py 均为纯 socket 客户端，不涉及 JVM，
与 gevent 兼容性风险低；连接超时统一压到 5s，避免拖慢 15s 采样轮。
"""

import importlib.util
import os
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NATIVE_DB_TYPES = frozenset(('mongodb', 'redis', 'redis-cluster'))

_REQ_TIMEOUT = 5  # 单次采集硬超时（s）


def _dead(err):
    """失败快照模板（alive=False）。"""
    return {'alive': False, 'err': err, 'conn': None, 'counters': {},
            'tbs': None, 'repl_lag_s': None, 'lock_waits': None, 'slowq': 0}


def collect_native(inst):
    """原生通道采集入口。返回结构化 dict（见 _dead 字段），不抛异常。

    counters 约定：QPS 差值统一挂在 'questions' 键上（与 mysql family 的
    SHOW GLOBAL STATUS Questions 命名对齐，_rate 的 (('questions',),) 组合命中）。
    """
    db_type = (inst.get('db_type') or '').lower()
    label = "%s (%s:%s)" % (inst.get('name', ''), inst.get('host', '?'), inst.get('port', '?'))
    try:
        if db_type == 'mongodb':
            return _collect_mongo(inst)
        if db_type in ('redis', 'redis-cluster'):
            return _collect_redis(inst)
        return _dead('不支持的类型: %s' % db_type)
    except Exception as e:
        # 原始异常进服务端日志，面向大屏只给友好文案
        print('[screen][native] %s 采集失败: %r' % (label, e), flush=True)
        try:
            from modules.monitor.engine import _friendly_db_error
            return _dead(_friendly_db_error(e))
        except Exception:
            return _dead('数据库连接或查询异常，请检查实例配置或查看服务端日志')


# ═══════════════════════════════════════════════════════════
#  MongoDB
# ═══════════════════════════════════════════════════════════

def _mongo_client(inst):
    """复用 mongodb 插件的连接配置构建器（与 app.py 数据源浏览同源）。"""
    from pymongo import MongoClient
    _plugin = os.path.join(_PROJECT_ROOT, 'plugins', 'available', 'mongodb', 'connection_config.py')
    spec = importlib.util.spec_from_file_location('screen_mongo_cc', _plugin)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = mod.MongoConnectionConfig.from_ssh_info(inst)
    cfg.host = inst.get('host')
    cfg.port = int(inst.get('port') or 27017)
    cfg.user = inst.get('user') or ''
    cfg.password = inst.get('password') or ''
    cfg.database = inst.get('database') or 'admin'
    return MongoClient(cfg.build_uri(), **cfg.build_client_kwargs()), cfg


def _collect_mongo(inst):
    client, cfg = _mongo_client(inst)
    try:
        s = client.admin.command('serverStatus')

        # 连接数（usage_pct 与 SQL 库口径一致：current / max）
        conns = s.get('connections') or {}
        cur = int(conns.get('current') or 0)
        mx = cur + int(conns.get('available') or 0)
        conn = {'total': cur, 'max_conn': mx,
                'usage_pct': round(cur / mx * 100, 1) if mx else 0,
                'active': cur, 'idle': 0, 'blocked': 0}

        # QPS 计数：opcounters 全量求和（单进程内已含 command 路由）
        ops = s.get('opcounters') or {}
        counters = {'questions': sum(int(ops.get(k) or 0) for k in
                                     ('insert', 'query', 'update', 'delete', 'getmore', 'command'))}

        # 缓存命中（wiredTiger cache，best effort）
        wt = (s.get('wiredTiger') or {}).get('cache') or {}
        req = int(wt.get('pages requested from cache') or 0)
        rd = int(wt.get('pages read into cache') or 0)
        if req:
            counters['hit_base'] = req
            counters['hit'] = max(req - rd, 0)

        # 容量：listDatabases 带 sizeOnDisk（低开销，免逐库 dbStats）
        tbs = None
        try:
            ld = client.admin.command({'listDatabases': 1, 'nameOnly': False})
            rows = [{'name': d.get('name'),
                     'total_mb': round(float(d.get('sizeOnDisk') or 0) / 1048576, 1),
                     'free_mb': None, 'free_pct': None}
                    for d in (ld.get('databases') or [])]
            rows = sorted(rows, key=lambda x: -(x['total_mb'] or 0))[:10]
            tbs = rows or None
        except Exception:
            pass

        # 复制延迟：副本集 PRIMARY 与 SECONDARY 的 optime 差，取最差值；单机无副本集 → None
        lag = None
        try:
            rs = client.admin.command('replSetGetStatus')
            members = rs.get('members') or []
            prim = next((m for m in members if m.get('stateStr') == 'PRIMARY'), None)
            if prim and prim.get('optimeDate'):
                lags = [(prim['optimeDate'] - m['optimeDate']).total_seconds()
                        for m in members
                        if m.get('stateStr') == 'SECONDARY' and m.get('optimeDate')]
                lag = int(max(lags)) if lags else None
        except Exception:
            pass

        # 慢查：system.profile 默认关闭（采集不到 → 0，不报错）
        slowq = 0
        try:
            slowq = int(client[cfg.database].system.profile.count_documents({}))
        except Exception:
            pass

        return {'alive': True, 'err': None, 'conn': conn, 'counters': counters,
                'tbs': tbs, 'repl_lag_s': lag, 'lock_waits': None, 'slowq': slowq}
    finally:
        try:
            client.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
#  Redis（单机 / 集群）
# ═══════════════════════════════════════════════════════════

def _redis_client(inst):
    import redis
    db_type = (inst.get('db_type') or '').lower()
    kw = dict(host=inst.get('host'), port=int(inst.get('port') or 6379),
              password=inst.get('password') or None,
              socket_timeout=_REQ_TIMEOUT, socket_connect_timeout=_REQ_TIMEOUT,
              decode_responses=True, encoding_errors='replace', protocol=2)
    if inst.get('user'):
        kw['username'] = inst.get('user')
    if db_type == 'redis-cluster':
        from redis.cluster import RedisCluster
        # seed_nodes（"host:port,host:port,..."）优先，取首个种子做发现入口
        seeds = inst.get('seed_nodes')
        if seeds:
            first = str(seeds).split(',')[0].strip()
            h, _, p = first.partition(':')
            kw['host'] = h or kw['host']
            if p.isdigit():
                kw['port'] = int(p)
        kw.pop('db', None)
        return RedisCluster(**kw)
    db_raw = str(inst.get('database') or '0')
    kw['db'] = int(db_raw) if db_raw.isdigit() else 0
    return redis.Redis(**kw)


def _info_nodes(r):
    """统一单机/集群 INFO 返回：[{节点 info dict}, ...]。

    redis-py 单机 info() 返回嵌套 section dict（含 'clients' 等键）；
    RedisCluster.info() 返回 {node_name: info_dict}（各节点同构）。
    """
    info = r.info()
    if 'clients' in info and isinstance(info.get('clients'), dict):
        return [info]
    return [v for v in info.values() if isinstance(v, dict)]


def _collect_redis(inst):
    r = _redis_client(inst)
    try:
        nodes = _info_nodes(r)

        # 多节点聚合（集群）：客户端数/命令计数求和，内存求和，maxclients 任一为 0 视为不限
        tot = blk = cmds = hits = miss = used = 0
        maxc = None
        for nd in nodes:
            c = nd.get('clients') or {}
            st = nd.get('stats') or {}
            m = nd.get('memory') or {}
            tot += int(c.get('connected_clients') or 0)
            blk += int(c.get('blocked_clients') or 0)
            cmds += int(st.get('total_commands_processed') or 0)
            hits += int(st.get('keyspace_hits') or 0)
            miss += int(st.get('keyspace_misses') or 0)
            used += int(m.get('used_memory') or 0)
            mc = int(c.get('maxclients') or 0)
            if maxc is None or (mc and mc < maxc):
                maxc = mc if mc else maxc
        if maxc is None:
            maxc = 0

        conn = {'total': tot, 'max_conn': maxc,
                'usage_pct': round(tot / maxc * 100, 1) if maxc else 0,
                'active': tot, 'idle': 0, 'blocked': blk}

        counters = {'questions': cmds}
        if hits + miss:
            counters['hit'] = hits
            counters['hit_base'] = hits + miss

        # 内存容量：maxmemory=0（不限）时不产 tbs 行，避免无意义的 100% 空闲
        maxmem = sum(int((nd.get('memory') or {}).get('maxmemory') or 0) for nd in nodes)
        tbs = None
        if maxmem:
            tbs = [{'name': 'memory',
                    'total_mb': round(maxmem / 1048576, 1),
                    'free_mb': round(max(0, maxmem - used) / 1048576, 1),
                    'free_pct': round((maxmem - used) / maxmem * 100, 1)}]

        # 复制延迟：仅单机主从拓扑取值（master 侧 slaveX.lag 秒）；集群拓扑 → None
        lag = None
        if len(nodes) == 1:
            repl = nodes[0].get('replication') or {}
            role = str(repl.get('role') or '')
            if role == 'master':
                lags = [int(v['lag']) for k, v in repl.items()
                        if k.startswith('slave') and isinstance(v, dict)
                        and v.get('lag') is not None]
                lag = max(lags) if lags else None
            elif role == 'slave' and str(repl.get('master_link_status') or '').lower() != 'up':
                # 从库断链：以一个超大延迟触发 warn/crit 展示（比静默 None 更真实）
                lag = 999999

        # 慢查条数：单机 int；集群返回 {node: int} → 取全节点之和
        slowq = 0
        try:
            sl = r.slowlog_len()
            slowq = sum(int(v) for v in sl.values()) if isinstance(sl, dict) else int(sl or 0)
        except Exception:
            pass

        return {'alive': True, 'err': None, 'conn': conn, 'counters': counters,
                'tbs': tbs, 'repl_lag_s': lag, 'lock_waits': None, 'slowq': slowq}
    finally:
        try:
            r.close()
        except Exception:
            pass
