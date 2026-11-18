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

SSH 隧道：显式开启 ``ssh_tunnel`` + ``ssh_host`` 时，MongoDB 由
:func:`open_mongo_connection`、Redis 由 :func:`open_redis_connection`
建本地端口转发并把 host/port 改写为 ``127.0.0.1:local_port``（与巡检
BaseInspectionEngine、数据源测试连接同口径），隧道与客户端同生命周期关闭。

Redis Cluster 额外处理：redis-py 会按 CLUSTER SLOTS 拓扑发现结果直连各成员
真实地址，只转发种子节点会绕过隧道。这里用官方 ``address_remap`` 回调把
每个成员地址映射到**各自**的本地转发端口（懒建隧道），由 :class:`TunnelGroup`
统一关闭，保证集群全部流量都经隧道；``mongodb+srv`` / replicaSet 与隧道互斥，
MongoDB 侧已在建隧道前拦截。
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
            'tbs': None, 'repl_lag_s': None, 'lock_waits': None, 'slowq': 0,
            'conn_rows': [], 'slow_rows': []}


class TunnelGroup:
    """一组 SSH 隧道（Redis Cluster 每个成员一条），实现 ``close()``。

    直接挂到 ``ManagedConnection.tunnel`` 上即可与客户端连接同生命周期关闭；
    重复 close 安全，单条隧道关闭失败不影响其它隧道。
    """

    def __init__(self, tunnels=None):
        self.tunnels = list(tunnels or [])

    def add(self, tunnel):
        if tunnel is not None:
            self.tunnels.append(tunnel)
        return tunnel

    def close(self):
        for t in self.tunnels:
            try:
                t.close()
            except Exception:
                pass
        self.tunnels = []


def collect_native(inst, shared=None):
    """原生通道采集入口。返回结构化 dict（见 _dead 字段），不抛异常。

    ``shared``：统一采集器在本轮已建好的唯一连接，两种形式：

      - ``(kind, client, cfg)``：``kind`` ∈ ``{'mongo', 'redis'}``（推荐）；
      - ``(client, cfg)``：旧版 MongoDB 二元组，按 mongo 解释（向后兼容）。

    传入时本函数 **不负责关闭** 连接（生命周期归统一采集器，含 SSH 隧道）；
    独立调用传 None，自行建连并在结束时关闭连接与隧道，行为与旧版一致。

    counters 约定：QPS 差值统一挂在 'questions' 键上（与 mysql family 的
    SHOW GLOBAL STATUS Questions 命名对齐，_rate 的 (('questions',),) 组合命中）。
    """
    db_type = (inst.get('db_type') or '').lower()
    label = "%s (%s:%s)" % (inst.get('name', ''), inst.get('host', '?'), inst.get('port', '?'))
    shared_client = None
    if shared is not None:
        try:
            if len(shared) == 3 and isinstance(shared[0], str):
                shared_client = (shared[1], shared[2])
            else:
                shared_client = (shared[0], shared[1])
        except Exception:
            shared_client = None
    try:
        if db_type == 'mongodb':
            return _collect_mongo(inst, shared=shared_client)
        if db_type in ('redis', 'redis-cluster'):
            return _collect_redis(inst, shared=shared_client)
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

def _mongo_client(inst, host=None, port=None, direct=False):
    """复用 mongodb 插件的连接配置构建器（与 app.py 数据源浏览同源）。

    ``host``/``port`` 可由调用方覆盖为 SSH 隧道的本地转发端点；
    ``direct=True`` 时追加 ``directConnection=true``，把 pymongo 固定在
    该端点上，避免拓扑发现拿到成员真实地址后绕过隧道直连。
    """
    from pymongo import MongoClient
    _plugin = os.path.join(_PROJECT_ROOT, 'plugins', 'available', 'mongodb', 'connection_config.py')
    spec = importlib.util.spec_from_file_location('screen_mongo_cc', _plugin)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = mod.MongoConnectionConfig.from_ssh_info(inst)
    cfg.host = host if host is not None else inst.get('host')
    cfg.port = int(port if port is not None else (inst.get('port') or 27017))
    cfg.user = inst.get('user') or ''
    cfg.password = inst.get('password') or ''
    cfg.database = inst.get('database') or 'admin'
    uri = cfg.build_uri()
    kwargs = cfg.build_client_kwargs()
    if direct:
        # srv / replicaSet 的冲突已由 validate_mongo_tunnel_mode 在建隧道前
        # 拦下，此处不会与 directConnection 互斥。
        uri += ('&' if '?' in uri else '?') + 'directConnection=true'
    return MongoClient(uri, **kwargs), cfg


def open_mongo_connection(inst):
    """按统一口径建立 MongoDB 连接，返回 ``(ManagedConnection, cfg)``。

    与巡检 ``BaseInspectionEngine``、数据源测试连接（app.py）同口径：
    显式开启 ``ssh_tunnel`` 且配了 ``ssh_host`` 时先建本地端口转发，
    再把 ``host``/``port`` 改写为 ``127.0.0.1:local_port`` 交给 pymongo；
    隧道挂在 ``ManagedConnection`` 上，与 MongoClient 同生命周期关闭，
    避免 paramiko 转发线程泄漏。建隧道失败直接抛错，绝不静默回退直连。
    """
    from modules.monitor.connection_factory import (
        ManagedConnection, uses_db_tunnel, resolve_endpoint,
        validate_mongo_tunnel_mode)

    tunnel = None
    host = inst.get('host')
    port = int(inst.get('port') or 27017)
    direct = False
    if uses_db_tunnel(inst):
        validate_mongo_tunnel_mode(inst)
        tunnel, host, port = resolve_endpoint(inst, host, port)
        direct = True
    try:
        client, cfg = _mongo_client(inst, host=host, port=port, direct=direct)
    except Exception:
        if tunnel is not None:
            try:
                tunnel.close()
            except Exception:
                pass
        raise
    return ManagedConnection(client, tunnel), cfg


def _collect_mongo(inst, shared=None):
    if shared is not None:
        client, cfg = shared
        managed = None
    else:
        # 独立调用路径同样支持 SSH 隧道；统一采集器走 shared，复用它在本轮
        # 建好的唯一一条连接（隧道生命周期归统一采集器）。
        managed, cfg = open_mongo_connection(inst)
        client = managed.conn
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
                'tbs': tbs, 'repl_lag_s': lag, 'lock_waits': None, 'slowq': slowq,
                'conn_rows': [], 'slow_rows': []}
    finally:
        if managed is not None:
            # 同时关闭 MongoClient 与 SSH 隧道（独立调用路径）
            managed.close()


# ═══════════════════════════════════════════════════════════
#  Redis（单机 / 集群）
# ═══════════════════════════════════════════════════════════

def _redis_client(inst, host=None, port=None, address_remap=None):
    """构建 redis-py 客户端。

    ``host``/``port`` 可由 :func:`open_redis_connection` 覆盖为 SSH 隧道的
    本地转发端点；``address_remap`` 仅用于 redis-cluster + 隧道（把拓扑发现
    出的每个成员地址映射到各自的本地端口）。
    """
    import redis
    db_type = (inst.get('db_type') or '').lower()
    kw = dict(host=host if host is not None else inst.get('host'),
              port=int(port if port is not None else (inst.get('port') or 6379)),
              password=inst.get('password') or None,
              socket_timeout=_REQ_TIMEOUT, socket_connect_timeout=_REQ_TIMEOUT,
              decode_responses=True, encoding_errors='replace', protocol=2)
    if inst.get('user'):
        kw['username'] = inst.get('user')
    if db_type == 'redis-cluster':
        from redis.cluster import RedisCluster
        # seed_nodes（"host:port,host:port,..."）优先，取首个种子做发现入口。
        # 隧道下忽略 seed_nodes：本地端点才是唯一的发现入口，其余成员由
        # address_remap 按拓扑发现结果懒建隧道。
        seeds = inst.get('seed_nodes')
        if seeds and address_remap is None:
            first = str(seeds).split(',')[0].strip()
            h, _, p = first.partition(':')
            kw['host'] = h or kw['host']
            if p.isdigit():
                kw['port'] = int(p)
        kw.pop('db', None)
        if address_remap is not None:
            import inspect
            if 'address_remap' not in inspect.signature(RedisCluster.__init__).parameters:
                raise RuntimeError(
                    '当前 redis-py 版本不支持 address_remap，无法让 Redis Cluster '
                    '经 SSH 隧道连接（拓扑发现会直连成员真实地址绕过隧道）；'
                    '请升级 redis>=5.0.0。')
            kw['address_remap'] = address_remap
        return RedisCluster(**kw)
    db_raw = str(inst.get('database') or '0')
    kw['db'] = int(db_raw) if db_raw.isdigit() else 0
    return redis.Redis(**kw)


def open_redis_connection(inst):
    """按统一口径建立 Redis 连接，返回 ``(ManagedConnection, None)``。

    与巡检 / 数据源测试 / MongoDB 通道同口径：显式 ``ssh_tunnel`` +
    ``ssh_host`` 时先建本地端口转发，再把 host/port 改写为
    ``127.0.0.1:local_port`` 交给 redis-py；建隧道失败直接抛错，绝不静默
    回退直连。隧道与客户端连接同生命周期关闭（ManagedConnection）。

    Redis Cluster：种子节点走上述本地端点；CLUSTER SLOTS 发现的每个成员
    由 ``address_remap`` 回调懒建一条独立隧道并映射到各自的本地端口，
    全部挂到 :class:`TunnelGroup`，保证没有任何一条流量绕过隧道。
    """
    from modules.monitor.connection_factory import (
        ManagedConnection, uses_db_tunnel, resolve_endpoint)

    raw_type = (inst.get('db_type') or '').lower()
    orig_host = inst.get('host')
    orig_port = int(inst.get('port') or 6379)
    host, port = orig_host, orig_port
    group = None
    remap = None

    if uses_db_tunnel(inst):
        seed_tunnel, host, port = resolve_endpoint(inst, orig_host, orig_port)
        group = TunnelGroup([seed_tunnel])
        if raw_type == 'redis-cluster':
            # 种子节点已在上面映射好；后续成员按 (host, port) 懒建并缓存，
            # 避免 redis-py 每次重定向都重复开 SSH 连接。
            cache = {(str(orig_host), orig_port): (host, port)}

            def remap(addr):
                rhost, rport = str(addr[0]), int(addr[1])
                key = (rhost, rport)
                if key in cache:
                    return cache[key]
                tunnel, lhost, lport = resolve_endpoint(inst, rhost, rport)
                if tunnel is None:
                    raise RuntimeError(
                        'Redis Cluster 节点 %s:%s 未能建立 SSH 隧道，拒绝直连绕过隧道'
                        % (rhost, rport))
                group.add(tunnel)
                cache[key] = (lhost, lport)
                return (lhost, lport)

    try:
        client = _redis_client(inst, host=host, port=port, address_remap=remap)
    except Exception:
        if group is not None:
            group.close()
        raise
    return ManagedConnection(client, group), None


def _info_nodes(r):
    """统一单机/集群 INFO 返回：[{节点 info dict}, ...]。

    redis-py 单机 info() 返回嵌套 section dict（含 'clients' 等键）；
    RedisCluster.info() 返回 {node_name: info_dict}（各节点同构）。
    """
    info = r.info()
    if 'clients' in info and isinstance(info.get('clients'), dict):
        return [info]
    return [v for v in info.values() if isinstance(v, dict)]


def _collect_redis(inst, shared=None):
    if shared is not None:
        r, _cfg = shared
        managed = None
    else:
        # 独立调用路径同样支持 SSH 隧道；统一采集器走 shared，复用它在本轮
        # 建好的唯一一条连接（隧道生命周期归统一采集器）。
        managed, _cfg = open_redis_connection(inst)
        r = managed.conn
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

        # 连接会话明细：CLIENT LIST（单机 list[dict]；集群 {node: list} 扁平化），截顶 50。
        # 仅采集汇总连接数时也能给大屏「连接会话」tab 提供逐客户端明细（非仅空状态）。
        conn_rows = []
        try:
            cl = r.client_list()
            if isinstance(cl, dict):
                for _v in cl.values():
                    if isinstance(_v, list):
                        conn_rows.extend(_v)
            elif isinstance(cl, list):
                conn_rows = cl
            conn_rows = conn_rows[:50]
        except Exception:
            conn_rows = []

        # 慢查询明细：SLOWLOG GET（duration 单位微秒 → 毫秒；集群同 client_list 结构）。
        # 失败/空时回落为空（大屏对该类型显示中性说明，而非「不支持」）。
        slow_rows = []
        try:
            slg = r.slowlog_get(50)
            raw = []
            if isinstance(slg, dict):
                for _v in slg.values():
                    if isinstance(_v, list):
                        raw.extend(_v)
            elif isinstance(slg, list):
                raw = slg
            for _e in raw[:50]:
                if isinstance(_e, dict):
                    _dur = _e.get('duration') or 0
                    try:
                        _dur = round(int(_dur) / 1000.0, 3)
                    except Exception:
                        _dur = 0
                    _cmd = _e.get('command')
                    if isinstance(_cmd, (list, tuple)):
                        try:
                            _cmd = ' '.join(map(str, _cmd))
                        except Exception:
                            _cmd = str(_cmd)
                    slow_rows.append({
                        'id': _e.get('id'),
                        'time': str(_e.get('time')) if _e.get('time') is not None else '',
                        'duration_ms': _dur,
                        'command': _cmd,
                    })
        except Exception:
            slow_rows = []

        return {'alive': True, 'err': None, 'conn': conn, 'counters': counters,
                'tbs': tbs, 'repl_lag_s': lag, 'lock_waits': None, 'slowq': slowq,
                'conn_rows': conn_rows, 'slow_rows': slow_rows}
    finally:
        if managed is not None:
            # 同时关闭 redis 客户端与全部 SSH 隧道（独立调用路径）
            managed.close()
