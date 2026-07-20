#!/usr/bin/env python3
# -*- coding:utf-8 -*-

"""
MongoDB 数据库巡检插件 v2.0
继承 BaseInspectionEngine，实现 MongoDB 数据库巡检

支持特性：
  - 标准 / SRV (mongodb+srv://) 连接模式
  - TLS/SSL 加密连接
  - 副本集 / 分片集群感知
  - 认证机制选择 (SCRAM-SHA-256 / SCRAM-SHA-1)
  - 版本适配 (5.0+/6.0+/7.0+)
  - 15+ 基线参数检查 (getParameter 批量)
  - 12+ 章节、20+ 采集项
"""

import os
import sys
import json
import traceback
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径，以便导入 BaseInspectionEngine
_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root))

# 插件自身目录也加入 sys.path（loader 动态加载时不会自动加），以便裸导入同级模块
_plugin_dir = str(Path(__file__).parent)
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from inspection_engine import BaseInspectionEngine
from connection_config import MongoConnectionConfig
from version_adapter import MongoVersionAdapter


class MongodbInspector(BaseInspectionEngine):
    """MongoDB 巡检器（v2.0）。

    继承 BaseInspectionEngine，覆盖 connect() / collect_data() / _check_baselines()，
    使用 pymongo 的 db.command() 替代 cursor.execute。

    Attributes:
        client: pymongo.MongoClient 实例
        db: pymongo.Database 实例（指向 self.database）
        version_adapter: MongoVersionAdapter 版本适配器
        conn_config: MongoConnectionConfig 连接配置
    """

    def __init__(self, host, port, user=None, password=None, database=None,
                 ssh_info=None, template_id=None):
        """初始化 MongoDB 巡检器。

        Args:
            host: MongoDB 服务器 IP 地址或主机名
            port: MongoDB 服务端口
            user: MongoDB 登录用户名（可选）
            password: MongoDB 登录密码（可选）
            database: 要连接的数据库名（可选，默认 'admin'）
            ssh_info: SSH 连接信息字典（可选，也传递 MongoDB 专用参数）
            template_id: 巡检模板 ID（可选）
        """
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'mongodb'
        self.client = None
        self.db = None
        self.version_adapter = None
        self.conn_config = None

    # ════════════════════════════════════════════════════════════
    # 连接层
    # ════════════════════════════════════════════════════════════

    def connect(self):
        """连接 MongoDB 数据库。

        使用 MongoConnectionConfig 构建 URI 和 client kwargs，
        支持 TLS/SRV/replicaSet/authSource/authMechanism。

        Returns:
            (ok, version): ok 为 True 时 version 是版本号字符串，
                           ok 为 False 时 version 是错误信息
        """
        try:
            from pymongo import MongoClient
            from pymongo.errors import PyMongoError

            # 从 ssh_info 构建连接配置
            self.conn_config = MongoConnectionConfig.from_ssh_info(self.ssh_info or {})
            self.conn_config.host = self.host
            self.conn_config.port = int(self.port) if self.port else 27017
            self.conn_config.user = self.user or ""
            self.conn_config.password = self.password or ""
            if self.database:
                self.conn_config.database = self.database

            # 构建 URI 和 client kwargs
            uri = self.conn_config.build_uri()
            client_kwargs = self.conn_config.build_client_kwargs()

            print(f"[MongoDB] 连接 URI: {self._safe_uri(uri)}")
            self.client = MongoClient(uri, **client_kwargs)
            self.db = self.client[self.conn_config.database]

            # 测试连接 + 获取版本
            version_info = self.db.command('buildInfo')
            version = version_info.get('version', 'unknown')

            # 初始化版本适配器
            self.version_adapter = MongoVersionAdapter(version)

            if self.version_adapter.is_unsupported:
                print(f"[WARN] MongoDB {self.version_adapter.version_label()} 版本较低，"
                      f"将跳过基线检查，仅采集基础数据")
            else:
                print(f"[MongoDB] 连接成功，版本: {version}")

            return True, version

        except Exception as e:
            print(f"[MongoDB] 连接失败: {e}")
            return False, str(e)

    def test_connection(self):
        """测试连接（供 web_ui.py 插件自动配置调用）。

        Returns:
            (ok: bool, msg: str)
        """
        try:
            ok, version = self.connect()
            if ok:
                return True, f"MongoDB {version}"
            return False, version
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _safe_uri(uri: str) -> str:
        """隐藏 URI 中的密码部分，用于日志输出。"""
        import re
        return re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', uri)

    # ════════════════════════════════════════════════════════════
    # 模板 / 章节
    # ════════════════════════════════════════════════════════════

    def get_template_id(self):
        """返回 inspection_template 表的 template_id。

        Returns:
            template_id: int 或 None
        """
        try:
            from inspection_dal import get_templates_by_db_type
            templates = get_templates_by_db_type("mongodb")
            return templates[0]['id'] if templates else None
        except Exception as e:
            print(f"[MongoDB] 获取模板 ID 失败: {e}")
            return None

    def _load_chapters_from_db(self):
        """从 sql_templates.json 加载章节结构（覆盖父类方法）。"""
        try:
            template_path = Path(__file__).parent / 'sql_templates.json'
            if not template_path.exists():
                print("[WARN] sql_templates.json 不存在")
                self.context['_chapters'] = []
                return []

            with open(template_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            chapters = []
            for ch in data.get('chapters', []):
                chapter = {
                    'chapter_number': ch.get('chapter_number', 0),
                    'chapter_title_zh': ch.get('chapter_title_zh', ''),
                    'chapter_title_en': ch.get('chapter_title_en', ''),
                    'queries': ch.get('queries', [])
                }
                chapters.append(chapter)

            self.context['_chapters'] = chapters
            print("[OK] 已从 sql_templates.json 加载 %d 个章节" % len(chapters))
            return chapters

        except Exception as e:
            print("[WARN] 加载章节结构失败: %s" % e)
            self.context['_chapters'] = []
            return []

    # ════════════════════════════════════════════════════════════
    # 数据采集
    # ════════════════════════════════════════════════════════════

    def collect_data(self, sql_templates=''):
        """采集数据 - 覆盖父类方法，使用 MongoDB 命令。

        采集流程：
        1. 连接数据库
        2. 采集版本信息
        3. 采集服务器状态（连接/内存/操作计数器/锁/WiredTiger）
        4. 采集数据库统计 / 集合信息
        5. 采集安全信息（用户/角色）
        6. 采集副本集 / 分片信息
        7. 采集慢查询 / Profiler
        8. 执行基线检查

        Returns:
            成功返回 self.context (dict)，失败返回 (False, error_msg)
        """
        print("\n[MongoDB] 开始采集数据...")

        # 1. 连接数据库
        ok, version = self.connect()
        if not ok:
            return False, version

        # 统一保存版本号到 context（报告头用）
        self.context['version'] = [{'VERSION': version}]
        self.context['db_type'] = 'mongodb'

        # 2. 采集各项数据
        try:
            # 基础信息
            self._collect_version()
            self._collect_server_status()
            self._collect_connections()
            self._collect_memory()
            self._collect_db_stats()
            self._collect_collections()

            # 安全信息
            self._collect_users()
            self._collect_roles()

            # 性能指标
            self._collect_opcounters()
            self._collect_global_lock()
            self._collect_wired_tiger()

            # 高可用
            self._collect_repl_status()
            self._collect_shards()
            self._collect_sharded_dbs(self.context)
            self._collect_sharded_collections(self.context)
            self._collect_chunk_distribution(self.context)
            self._collect_balancer_status(self.context)

            # 慢查询 / Profiler
            self._collect_profile()
            self._collect_slow_queries()

            # 基线检查
            self._check_baselines()

            print("[MongoDB] 数据采集完成，context keys: %s" % list(self.context.keys()))
            return self.context

        except Exception as e:
            print(f"[MongoDB] 数据采集失败: {e}")
            traceback.print_exc()
            return False, str(e)
        finally:
            # 关闭连接
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass

    def _safe_command(self, command, default=None, db=None):
        """安全执行 MongoDB 命令，失败时返回默认值。

        Args:
            command: 命令字典或命令名字符串
            default: 失败时的默认返回值
            db: 使用的数据库对象（默认 self.db）

        Returns:
            命令结果，失败时返回 default
        """
        target_db = db or self.db
        try:
            if isinstance(command, str):
                return target_db.command(command)
            else:
                return target_db.command(command)
        except Exception as e:
            err_str = str(e).lower()
            if 'not authorized' in err_str or 'unauthorized' in err_str:
                print(f"[WARN] 权限不足: {command}")
            return default

    def _collect_version(self):
        """采集 MongoDB 版本信息 (buildInfo)。"""
        try:
            version_info = self.db.command('buildInfo')
            self.context['mongodb_version'] = [{
                'VERSION': version_info.get('version', 'unknown'),
                'GIT_VERSION': version_info.get('gitVersion', 'unknown'),
                'OPENSSL_VERSION': version_info.get('openssl', {}).get('running', 'unknown') if isinstance(version_info.get('openssl'), dict) else 'unknown',
                'MODULES': ','.join(version_info.get('modules', [])) if version_info.get('modules') else 'none',
                'DEBUG': version_info.get('debug', False),
            }]
            print(f"[OK] 版本信息: {version_info.get('version', 'unknown')}")
        except Exception as e:
            print(f"[WARN] 采集版本信息失败: {e}")
            self.context['mongodb_version'] = [{'ERROR': str(e)[:200]}]

    def _collect_server_status(self):
        """采集服务器状态概要 (serverStatus)。"""
        try:
            status = self.db.command('serverStatus')
            # 存储完整状态供其他 _collect_* 方法复用
            self._server_status = status

            self.context['mongodb_server_status'] = [{
                'HOST': status.get('host', 'unknown'),
                'VERSION': status.get('version', 'unknown'),
                'UPTIME': round(status.get('uptime', 0), 1),
                'UPTIME_ESTIMATED': round(status.get('uptimeEstimate', 0), 1),
                'LOCAL_TIME': str(status.get('localTime', '')),
                'CONNECTIONS_CURRENT': status.get('connections', {}).get('current', 0),
                'CONNECTIONS_AVAILABLE': status.get('connections', {}).get('available', 0),
                'OPCOUNTERS_INSERT': status.get('opcounters', {}).get('insert', 0),
                'OPCOUNTERS_QUERY': status.get('opcounters', {}).get('query', 0),
                'OPCOUNTERS_UPDATE': status.get('opcounters', {}).get('update', 0),
                'OPCOUNTERS_DELETE': status.get('opcounters', {}).get('delete', 0),
                'MEM_RESIDENT': status.get('mem', {}).get('resident', 0),
                'MEM_VIRTUAL': status.get('mem', {}).get('virtual', 0),
                'MEM_MAPPED': status.get('mem', {}).get('mapped', 0),
                'NETWORK_BYTES_IN': status.get('network', {}).get('bytesIn', 0),
                'NETWORK_BYTES_OUT': status.get('network', {}).get('bytesOut', 0),
                'NETWORK_NUM_REQUESTS': status.get('network', {}).get('numRequests', 0),
            }]
            print("[OK] 服务器状态")
        except Exception as e:
            print(f"[WARN] 采集服务器状态失败: {e}")
            self.context['mongodb_server_status'] = [{'ERROR': str(e)[:200]}]
            self._server_status = {}

    def _collect_connections(self):
        """采集连接信息 (serverStatus.connections)。"""
        try:
            status = getattr(self, '_server_status', None)
            if status is None:
                status = self.db.command('serverStatus')
                self._server_status = status
            conns = status.get('connections', {})
            self.context['mongodb_connections'] = [{
                'CURRENT': conns.get('current', 0),
                'AVAILABLE': conns.get('available', 0),
                'TOTAL_CREATED': conns.get('totalCreated', 0),
                'ACTIVE': conns.get('active', 0),
                'EXHAUST_IS_MASTER': conns.get('exhaustIsMaster', 0),
                'EXHAUST_OTHERS': conns.get('exhaustOthers', 0),
                'USAGE_PCT': round(
                    conns.get('current', 0) / max(
                        conns.get('current', 0) + conns.get('available', 1), 1) * 100, 2
                ),
            }]
            print("[OK] 连接信息")
        except Exception as e:
            print(f"[WARN] 采集连接信息失败: {e}")
            self.context['mongodb_connections'] = [{'ERROR': str(e)[:200]}]

    def _collect_memory(self):
        """采集内存使用情况 (serverStatus.mem + wiredTiger.cache)。"""
        try:
            status = getattr(self, '_server_status', None)
            if status is None:
                status = self.db.command('serverStatus')
                self._server_status = status

            mem = status.get('mem', {})
            wt_cache = status.get('wiredTiger', {}).get('cache', {})

            self.context['mongodb_memory'] = [{
                'MEM_RESIDENT_MB': mem.get('resident', 0),
                'MEM_VIRTUAL_MB': mem.get('virtual', 0),
                'MEM_MAPPED_MB': mem.get('mapped', 0),
                'MEM_MAPPED_WITH_JOURNAL_MB': mem.get('mappedWithJournal', 0),
                'WT_CACHE_MAX_BYTES': wt_cache.get('maximum bytes configured', 0),
                'WT_CACHE_USED_BYTES': wt_cache.get('bytes currently in the cache', 0),
                'WT_CACHE_DIRTY_BYTES': wt_cache.get('tracked dirty bytes in the cache', 0),
                'WT_CACHE_READ_INTO': wt_cache.get('bytes read into cache', 0),
                'WT_CACHE_WRITTEN_FROM': wt_cache.get('bytes written from cache', 0),
                'WT_CACHE_EVICTIONS': wt_cache.get('pages evicted by application threads', 0),
            }]
            print("[OK] 内存信息")
        except Exception as e:
            print(f"[WARN] 采集内存信息失败: {e}")
            self.context['mongodb_memory'] = [{'ERROR': str(e)[:200]}]

    def _collect_db_stats(self):
        """采集数据库统计信息 (dbStats)。"""
        try:
            stats = self.db.command('dbStats')
            self.context['mongodb_db_stats'] = [{
                'DB_NAME': self.conn_config.database if self.conn_config else 'admin',
                'DATA_SIZE': stats.get('dataSize', 0),
                'STORAGE_SIZE': stats.get('storageSize', 0),
                'INDEX_SIZE': stats.get('indexSize', 0),
                'NUM_COLLECTIONS': stats.get('collections', 0),
                'NUM_VIEWS': stats.get('views', 0),
                'NUM_OBJECTS': stats.get('objects', 0),
                'AVG_OBJ_SIZE': round(stats.get('avgObjSize', 0), 2),
                'INDEXES': stats.get('indexes', 0),
                'SCALE_FACTOR': stats.get('scaleFactor', 1),
            }]
            print("[OK] 数据库统计")
        except Exception as e:
            print(f"[WARN] 采集数据库统计失败: {e}")
            self.context['mongodb_db_stats'] = [{'ERROR': str(e)[:200]}]

    def _collect_collections(self):
        """采集集合列表及各集合统计 (list_collection_names + $collStats)。"""
        try:
            coll_names = self.db.list_collection_names()
            coll_infos = []

            for name in coll_names:
                if name.startswith('system.'):
                    continue
                try:
                    coll_stats = self.db.command('collStats', name)
                    coll_infos.append({
                        'COLLECTION_NAME': name,
                        'COUNT': coll_stats.get('count', 0),
                        'SIZE': coll_stats.get('size', 0),
                        'STORAGE_SIZE': coll_stats.get('storageSize', 0),
                        'TOTAL_INDEX_SIZE': coll_stats.get('totalIndexSize', 0),
                        'INDEXES': coll_stats.get('nindexes', 0),
                        'AVG_OBJ_SIZE': round(coll_stats.get('avgObjSize', 0), 2),
                        'CAPPED': coll_stats.get('capped', False),
                    })
                except Exception:
                    # 权限不足或集合被删除时跳过
                    coll_infos.append({
                        'COLLECTION_NAME': name,
                        'COUNT': 0,
                        'SIZE': 0,
                        'STORAGE_SIZE': 0,
                        'TOTAL_INDEX_SIZE': 0,
                        'INDEXES': 0,
                        'AVG_OBJ_SIZE': 0,
                        'CAPPED': False,
                        'ERROR': 'stats_unavailable',
                    })

            self.context['mongodb_collections'] = coll_infos if coll_infos else [{
                'COLLECTION_NAME': '(no collections)',
                'COUNT': 0,
                'SIZE': 0,
                'STORAGE_SIZE': 0,
                'TOTAL_INDEX_SIZE': 0,
                'INDEXES': 0,
            }]
            print(f"[OK] 集合信息: {len(coll_names)} 个集合")
        except Exception as e:
            print(f"[WARN] 采集集合信息失败: {e}")
            self.context['mongodb_collections'] = [{'ERROR': str(e)[:200]}]

    def _collect_users(self):
        """采集用户列表 (usersInfo)。"""
        try:
            result = self.db.command('usersInfo')
            users = result.get('users', [])
            user_list = []
            for u in users:
                roles = u.get('roles', [])
                role_strs = []
                for r in roles:
                    role_strs.append(f"{r.get('role', '')}@{r.get('db', '')}")
                user_list.append({
                    'USER': u.get('user', ''),
                    'DB': u.get('db', ''),
                    'ROLES': ','.join(role_strs) if role_strs else 'none',
                })

            self.context['mongodb_users'] = user_list if user_list else [{
                'USER': '(no users)',
                'DB': '',
                'ROLES': '',
            }]
            print(f"[OK] 用户信息: {len(users)} 个用户")
        except Exception as e:
            err_str = str(e).lower()
            if 'not authorized' in err_str or 'unauthorized' in err_str:
                self.context['mongodb_users'] = [{'ERROR': 'not authorized'}]
                print("[WARN] 采集用户信息: 权限不足")
            else:
                self.context['mongodb_users'] = [{'ERROR': str(e)[:200]}]
                print(f"[WARN] 采集用户信息失败: {e}")

    def _collect_roles(self):
        """采集角色列表 (rolesInfo)。"""
        try:
            result = self.db.command('rolesInfo', showBuiltinRoles=True)
            roles = result.get('roles', [])
            role_list = []
            for r in roles:
                privs = r.get('privileges', [])
                inherited = r.get('inheritedPrivileges', [])
                role_list.append({
                    'ROLE': r.get('role', ''),
                    'DB': r.get('db', ''),
                    'IS_BUILTIN': r.get('isBuiltin', False),
                    'ROLES_COUNT': len(r.get('roles', [])),
                    'PRIVILEGES_COUNT': len(privs),
                    'INHERITED_PRIVILEGES_COUNT': len(inherited),
                })

            self.context['mongodb_roles'] = role_list if role_list else [{
                'ROLE': '(no roles)',
                'DB': '',
                'IS_BUILTIN': False,
                'ROLES_COUNT': 0,
                'PRIVILEGES_COUNT': 0,
                'INHERITED_PRIVILEGES_COUNT': 0,
            }]
            print(f"[OK] 角色信息: {len(roles)} 个角色")
        except Exception as e:
            err_str = str(e).lower()
            if 'not authorized' in err_str or 'unauthorized' in err_str:
                self.context['mongodb_roles'] = [{'ERROR': 'not authorized'}]
                print("[WARN] 采集角色信息: 权限不足")
            else:
                self.context['mongodb_roles'] = [{'ERROR': str(e)[:200]}]
                print(f"[WARN] 采集角色信息失败: {e}")

    def _collect_opcounters(self):
        """采集操作计数器 (serverStatus.opcounters)。"""
        try:
            status = getattr(self, '_server_status', None)
            if status is None:
                status = self.db.command('serverStatus')
                self._server_status = status
            ops = status.get('opcounters', {})
            self.context['mongodb_opcounters'] = [{
                'INSERT': ops.get('insert', 0),
                'QUERY': ops.get('query', 0),
                'UPDATE': ops.get('update', 0),
                'DELETE': ops.get('delete', 0),
                'GETMORE': ops.get('getmore', 0),
                'COMMAND': ops.get('command', 0),
            }]
            # 也采集复制操作计数器
            repl_ops = status.get('opcountersRepl', {})
            if repl_ops:
                self.context['mongodb_opcounters'][0].update({
                    'REPL_INSERT': repl_ops.get('insert', 0),
                    'REPL_QUERY': repl_ops.get('query', 0),
                    'REPL_UPDATE': repl_ops.get('update', 0),
                    'REPL_DELETE': repl_ops.get('delete', 0),
                })
            print("[OK] 操作计数器")
        except Exception as e:
            print(f"[WARN] 采集操作计数器失败: {e}")
            self.context['mongodb_opcounters'] = [{'ERROR': str(e)[:200]}]

    def _collect_global_lock(self):
        """采集全局锁状态 (serverStatus.globalLock)。"""
        try:
            status = getattr(self, '_server_status', None)
            if status is None:
                status = self.db.command('serverStatus')
                self._server_status = status
            gl = status.get('globalLock', {})
            active = gl.get('activeClients', {})
            current = gl.get('currentQueue', {})
            self.context['mongodb_global_lock'] = [{
                'TOTAL_TIME': gl.get('totalTime', 0),
                'ACTIVE_CLIENTS_TOTAL': active.get('total', 0),
                'ACTIVE_CLIENTS_READERS': active.get('readers', 0),
                'ACTIVE_CLIENTS_WRITERS': active.get('writers', 0),
                'CURRENT_QUEUE_TOTAL': current.get('total', 0),
                'CURRENT_QUEUE_READERS': current.get('readers', 0),
                'CURRENT_QUEUE_WRITERS': current.get('writers', 0),
            }]
            print("[OK] 全局锁状态")
        except Exception as e:
            print(f"[WARN] 采集全局锁状态失败: {e}")
            self.context['mongodb_global_lock'] = [{'ERROR': str(e)[:200]}]

    def _collect_wired_tiger(self):
        """采集 WiredTiger 存储引擎状态 (serverStatus.wiredTiger)。"""
        try:
            status = getattr(self, '_server_status', None)
            if status is None:
                status = self.db.command('serverStatus')
                self._server_status = status
            wt = status.get('wiredTiger', {})
            cache = wt.get('cache', {})

            self.context['mongodb_wired_tiger'] = [{
                'CACHE_MAX_BYTES': cache.get('maximum bytes configured', 0),
                'CACHE_USED_BYTES': cache.get('bytes currently in the cache', 0),
                'CACHE_DIRTY_BYTES': cache.get('tracked dirty bytes in the cache', 0),
                'CACHE_READ_INTO': cache.get('bytes read into cache', 0),
                'CACHE_WRITTEN_FROM': cache.get('bytes written from cache', 0),
                'CACHE_PAGES_EVICTED': cache.get('pages evicted by application threads', 0),
                'CACHE_PAGES_READ': cache.get('pages read into cache', 0),
                'CACHE_PAGES_WRITTEN': cache.get('pages written from cache', 0),
                'CACHE_HIT_RATIO_PCT': self._calc_cache_hit_ratio(cache),
                'CONCURRENT_TRANSACTIONS_WRITE_OUT': wt.get('transaction', {}).get('transaction checkpoint currently generating', 0),
                'CONCURRENT_TRANSACTIONS_READ_AVAIL': wt.get('concurrentTransactions', {}).get('read', {}).get('available', 0),
                'CONCURRENT_TRANSACTIONS_WRITE_AVAIL': wt.get('concurrentTransactions', {}).get('write', {}).get('available', 0),
            }]
            print("[OK] WiredTiger 状态")
        except Exception as e:
            print(f"[WARN] 采集 WiredTiger 状态失败: {e}")
            self.context['mongodb_wired_tiger'] = [{'ERROR': str(e)[:200]}]

    @staticmethod
    def _calc_cache_hit_ratio(cache: dict) -> float:
        """计算 WiredTiger 缓存命中率。

        Args:
            cache: wiredTiger.cache 字典

        Returns:
            缓存命中率百分比（0-100），计算失败返回 0.0
        """
        try:
            read_into = cache.get('bytes read into cache', 0)
            written_from = cache.get('bytes written from cache', 0)
            total_io = read_into + written_from
            if total_io == 0:
                return 100.0
            # 命中率 = 1 - (从磁盘读入的字节 / 总IO字节) * 100
            ratio = (1 - read_into / max(total_io, 1)) * 100
            return round(max(0.0, min(100.0, ratio)), 2)
        except Exception:
            return 0.0

    def _collect_repl_status(self):
        """采集副本集状态 (replSetGetStatus)。

        非副本集时 replSetGetStatus 抛异常，静默跳过，context 值为空列表。
        """
        try:
            repl_status = self.db.command('replSetGetStatus')
            members = repl_status.get('members', [])
            member_list = []
            for m in members:
                member_list.append({
                    'NAME': m.get('name', ''),
                    'STATE_STR': m.get('stateStr', 'unknown'),
                    'STATE': m.get('state', 0),
                    'HEALTH': m.get('health', 0),
                    'UPTIME': m.get('uptime', 0),
                    'OPTIME': str(m.get('optime', '')),
                    'LAST_HEARTBEAT': str(m.get('lastHeartbeat', '')),
                    'PING_MS': round(m.get('pingMs', 0), 2),
                    'SYNCING_TO': m.get('syncingTo', ''),
                    'ELECTION_TIME': str(m.get('electionTime', '')),
                })

            self.context['mongodb_repl_status'] = [{
                'SET_NAME': repl_status.get('set', ''),
                'MY_STATE': repl_status.get('myState', 'unknown'),
                'MY_STATE_STR': self._repl_state_str(repl_status.get('myState', 0)),
                'MEMBERS_COUNT': len(members),
                'MEMBERS': json.dumps(member_list, default=str),
                'TERMIN': repl_status.get('term', 0),
                'SYNC_SOURCE_HOST': repl_status.get('syncSourceHost', ''),
                'SYNC_SOURCE_ID': repl_status.get('syncSourceId', -1),
                'ELECTION_CANDIDATE_METRICS': str(repl_status.get('electionCandidateMetrics', '')),
            }]
            print(f"[OK] 副本集状态: {repl_status.get('set', '')} ({len(members)} 个成员)")
        except Exception as e:
            err_str = str(e).lower()
            if 'not running with --replset' in err_str or 'noreplset' in err_str or 'no replset' in err_str:
                print("[INFO] 非副本集模式，跳过 replSetGetStatus")
            elif 'not authorized' in err_str or 'unauthorized' in err_str:
                print("[WARN] 采集副本集状态: 权限不足")
            else:
                print(f"[INFO] 跳过副本集状态: {e}")
            self.context['mongodb_repl_status'] = []

    @staticmethod
    def _repl_state_str(state: int) -> str:
        """将副本集成员状态数字转为可读字符串。"""
        state_map = {
            0: 'STARTUP',
            1: 'PRIMARY',
            2: 'SECONDARY',
            3: 'RECOVERING',
            4: 'STARTUP2',
            5: 'UNKNOWN',
            6: 'ARBITER',
            7: 'DOWN',
            8: 'ROLLBACK',
            9: 'REMOVED',
            10: 'DRAINING',
            11: 'RBD',
        }
        return state_map.get(state, 'UNKNOWN')

    def _collect_shards(self):
        """采集分片信息 (listShards)。

        非分片集群时抛异常，静默跳过，context 值为空列表。
        """
        try:
            result = self.client.admin.command('listShards')
            shards = result.get('shards', [])
            shard_list = []
            for s in shards:
                shard_list.append({
                    '_ID': s.get('_id', ''),
                    'HOST': s.get('host', ''),
                    'STATE': s.get('state', 0),
                    'TAGS': ','.join(s.get('tags', [])) if s.get('tags') else '',
                })

            self.context['mongodb_shards'] = shard_list if shard_list else []
            print(f"[OK] 分片信息: {len(shards)} 个分片")
        except Exception as e:
            err_str = str(e).lower()
            if 'no such command' in err_str or 'not a sharded' in err_str or 'could not find' in err_str:
                print("[INFO] 非分片集群，跳过 listShards")
            elif 'not authorized' in err_str or 'unauthorized' in err_str:
                print("[WARN] 采集分片信息: 权限不足")
            else:
                print(f"[INFO] 跳过分片信息: {e}")
            self.context['mongodb_shards'] = []

    # ════════════════════════════════════════════════════════════
    # 分片集群采集（sh.status() 等效，访问 self.client.config.*）
    # ════════════════════════════════════════════════════════════

    def _collect_sharded_dbs(self, context):
        """已开启分片的数据库列表 -> context['mongodb_sharded_dbs']"""
        try:
            rows = list(self.client.config.databases.find({'partitioned': True}))
            context['mongodb_sharded_dbs'] = [
                {'DB': r.get('_id', ''), 'PRIMARY': r.get('primary', ''),
                 'PARTITIONED': bool(r.get('partitioned', False))}
                for r in rows
            ]
        except Exception:
            context['mongodb_sharded_dbs'] = []

    def _collect_sharded_collections(self, context):
        """分片集合与片键 -> context['mongodb_sharded_collections']"""
        try:
            rows = list(self.client.config.collections.find())
            out = []
            for r in rows:
                key = r.get('key')
                out.append({
                    'NS': r.get('_id', ''),
                    'KEY': str(key) if key is not None else '',
                    'UNIQUE': bool(r.get('unique', False)),
                    'LASTMOD': str(r.get('lastmod', '')),
                })
            context['mongodb_sharded_collections'] = out
        except Exception:
            context['mongodb_sharded_collections'] = []

    def _collect_chunk_distribution(self, context):
        """Chunk 按分片分布 + Top10 集合 -> context['mongodb_chunk_distribution'] / ['mongodb_chunk_top_collections']"""
        try:
            by_shard = list(self.client.config.chunks.aggregate(
                [{'$group': {'_id': '$shard', 'count': {'$sum': 1}}}], allowDiskUse=True))
            context['mongodb_chunk_distribution'] = [
                {'SHARD': g.get('_id', ''), 'CHUNK_COUNT': g.get('count', 0)} for g in by_shard
            ]
        except Exception:
            context['mongodb_chunk_distribution'] = []
        try:
            by_ns = list(self.client.config.chunks.aggregate(
                [{'$group': {'_id': '$ns', 'count': {'$sum': 1}}},
                 {'$sort': {'count': -1}}, {'$limit': 10}], allowDiskUse=True))
            context['mongodb_chunk_top_collections'] = [
                {'NS': g.get('_id', ''), 'CHUNK_COUNT': g.get('count', 0)} for g in by_ns
            ]
        except Exception:
            context['mongodb_chunk_top_collections'] = []

    def _collect_balancer_status(self, context):
        """Balancer 状态 -> context['mongodb_balancer_status']"""
        try:
            doc = self.client.config.settings.find_one({'_id': 'balancer'})
            if doc is None:
                # 兜底：部分版本可用 adminCommand
                try:
                    bal = self.client.admin.command('balancer', 1)
                    stopped = not bool(bal.get('ok', 0)) or bal.get('stopped', False)
                    doc = {'stopped': stopped, 'mode': 'full' if not stopped else 'off'}
                except Exception:
                    doc = {}
            context['mongodb_balancer_status'] = [{
                'BALANCER': 'disabled' if doc.get('stopped', False) else 'enabled',
                'MODE': str(doc.get('mode', 'full')),
                'STOPPED': bool(doc.get('stopped', False)),
                'ACTIVE_WINDOW': str(doc.get('activeWindow', '') or ''),
            }]
        except Exception:
            context['mongodb_balancer_status'] = []

    def _collect_profile(self):
        """采集 Profiler 级别 (profile -1)。"""
        try:
            profile_info = self.db.command('profile', -1)
            self.context['mongodb_profile'] = [{
                'LEVEL': profile_info.get('was', 0),
                'SLOW_MS': profile_info.get('slowms', 100),
                'SAMPLE_RATE': profile_info.get('sampleRate', 1.0),
                'CURRENT_LEVEL': self._profile_level_str(profile_info.get('was', 0)),
            }]
            print(f"[OK] Profiler 级别: {self._profile_level_str(profile_info.get('was', 0))}")
        except Exception as e:
            print(f"[WARN] 采集 Profiler 级别失败: {e}")
            self.context['mongodb_profile'] = [{'ERROR': str(e)[:200]}]

    @staticmethod
    def _profile_level_str(level: int) -> str:
        """将 Profiler 级别数字转为可读字符串。"""
        return {0: 'OFF', 1: 'SLOW_OPS', 2: 'ALL_OPS'}.get(level, 'UNKNOWN')

    def _collect_slow_queries(self):
        """采集慢查询 (system.profile Top N)。

        需要 Profiler 开启（level >= 1）才能采集到数据。
        """
        try:
            # 检查 system.profile 是否存在
            if 'system.profile' not in self.db.list_collection_names():
                self.context['mongodb_slow_queries'] = [{
                    'INFO': 'profiler_not_enabled',
                    'COUNT': 0,
                }]
                print("[INFO] system.profile 不存在，Profiler 未开启")
                return

            # 查询 Top 10 慢查询（按执行时间降序）
            pipeline = [
                {'$sort': {'millis': -1}},
                {'$limit': 10},
                {'$project': {
                    '_id': 0,
                    'TS': '$ts',
                    'OP': '$op',
                    'NS': '$ns',
                    'MILLIS': '$millis',
                    'CLIENT': '$client',
                    'QUERY': '$query',
                    'COMMAND': '$command',
                    'PLAN_SUMMARY': '$planSummary',
                    'NRETURNED': '$nreturned',
                    'NFETCHED': '$nfetched',
                    'KEYS_EXAMINED': '$keysExamined',
                    'DOCS_EXAMINED': '$docsExamined',
                    'RESLEN': '$reslen',
                }}
            ]
            slow_docs = list(self.db['system.profile'].aggregate(pipeline))

            if slow_docs:
                slow_list = []
                for doc in slow_docs:
                    slow_list.append({
                        'TS': str(doc.get('TS', '')),
                        'OP': doc.get('OP', ''),
                        'NS': doc.get('NS', ''),
                        'MILLIS': doc.get('MILLIS', 0),
                        'CLIENT': doc.get('CLIENT', ''),
                        'QUERY': json.dumps(doc.get('QUERY', doc.get('COMMAND', {})), default=str)[:500],
                        'PLAN_SUMMARY': doc.get('PLAN_SUMMARY', ''),
                        'NRETURNED': doc.get('NRETURNED', 0),
                        'KEYS_EXAMINED': doc.get('KEYS_EXAMINED', 0),
                        'DOCS_EXAMINED': doc.get('DOCS_EXAMINED', 0),
                        'RESLEN': doc.get('RESLEN', 0),
                    })
                self.context['mongodb_slow_queries'] = slow_list
                print(f"[OK] 慢查询: {len(slow_list)} 条 (Top 10)")
            else:
                self.context['mongodb_slow_queries'] = [{
                    'INFO': 'no_slow_queries',
                    'COUNT': 0,
                }]
                print("[OK] 慢查询: 无记录")

        except Exception as e:
            err_str = str(e).lower()
            if 'not authorized' in err_str or 'unauthorized' in err_str:
                self.context['mongodb_slow_queries'] = [{'ERROR': 'not authorized'}]
                print("[WARN] 采集慢查询: 权限不足")
            else:
                self.context['mongodb_slow_queries'] = [{'ERROR': str(e)[:200]}]
                print(f"[WARN] 采集慢查询失败: {e}")

    # ════════════════════════════════════════════════════════════
    # 基线检查（覆盖父类方法）
    # ════════════════════════════════════════════════════════════

    def _check_baselines(self):
        """执行基线配置检查，结果存入 self.context['baseline_results']。

        MongoDB 基线检查使用 db.command({getParameter: 1, ...}) 批量获取参数，
        失败时降级为逐参数获取。不使用 cursor.execute（与关系型数据库不同）。

        4.x 版本跳过基线检查（version_adapter.is_unsupported）。
        """
        # 版本检查：4.x 不执行基线检查
        if self.version_adapter and self.version_adapter.is_unsupported:
            print(f"[WARN] MongoDB {self.version_adapter.version_label()} 不支持基线检查，跳过")
            self.context['baseline_results'] = []
            return

        try:
            # 优先从 baselines.json 加载基线配置
            baselines = self._load_baselines()
            if not baselines:
                # 降级到 inspection_dal
                try:
                    from inspection_dal import get_baselines_by_db_type
                    baselines = get_baselines_by_db_type(self.db_type, enabled_only=True)
                except Exception as e:
                    print(f"[WARN] 加载基线配置失败: {e}")
                    self.context['baseline_results'] = []
                    return

            if not baselines:
                print(f"[INFO] 未找到 {self.db_type} 的基线配置")
                self.context['baseline_results'] = []
                return

            # 收集所有需要检查的参数名
            param_names = [bl['param_name'] for bl in baselines]

            # 批量获取参数值
            param_values = self._batch_get_parameters(param_names)

            # 逐条对比
            results = []
            for bl in baselines:
                param_name = bl['param_name']
                operator = bl.get('operator', '==')
                expected_value = bl.get('expected_value', '')
                expected_min = bl.get('expected_value_min', '')
                expected_max = bl.get('expected_value_max', '')
                risk_level = bl.get('risk_level', 'MEDIUM')
                desc_zh = bl.get('description_zh', '')
                desc_en = bl.get('description_en', '')

                current_value = param_values.get(param_name, 'N/A')

                # 版本适配：跳过不支持的参数
                if self.version_adapter and not self.version_adapter.supports_param(param_name):
                    results.append({
                        'param_name': param_name,
                        'current_value': 'N/A (版本不支持)',
                        'expected_value': expected_value,
                        'operator': operator,
                        'status': 'SKIP',
                        'risk_level': risk_level,
                        'description_zh': desc_zh,
                        'description_en': desc_en,
                        'message': f'当前版本 {self.version_adapter.version_label()} 不支持此参数',
                    })
                    continue

                # 将当前值转为字符串
                current_str = self._stringify_value(current_value)

                # 执行对比
                status = self._compare_mongo_baseline(
                    current_str, operator, expected_value, expected_min, expected_max
                )

                results.append({
                    'param_name': param_name,
                    'current_value': current_str,
                    'expected_value': expected_value,
                    'expected_value_min': expected_min,
                    'expected_value_max': expected_max,
                    'operator': operator,
                    'status': status,
                    'risk_level': risk_level,
                    'description_zh': desc_zh,
                    'description_en': desc_en,
                })

            self.context['baseline_results'] = results
            print(f"[OK] 基线检查完成: {len(results)} 条")

        except Exception as e:
            print(f"[WARN] 基线检查失败: {e}")
            traceback.print_exc()
            self.context['baseline_results'] = []

    def _load_baselines(self):
        """从 baselines.json 加载基线配置。

        Returns:
            基线列表，每条包含 param_name/operator/expected_value 等字段
        """
        try:
            baseline_path = Path(__file__).parent / 'baselines.json'
            if not baseline_path.exists():
                return []

            with open(baseline_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            baselines = data.get('baselines', [])
            # 确保每条基线都有必需字段
            result = []
            for bl in baselines:
                if bl.get('param_name'):
                    result.append({
                        'param_name': bl['param_name'],
                        'operator': bl.get('operator', '=='),
                        'expected_value': bl.get('expected_value', ''),
                        'expected_value_min': bl.get('expected_value_min', ''),
                        'expected_value_max': bl.get('expected_value_max', ''),
                        'risk_level': bl.get('risk_level', 'MEDIUM'),
                        'description_zh': bl.get('description_zh', ''),
                        'description_en': bl.get('description_en', ''),
                    })
            return result
        except Exception as e:
            print(f"[WARN] 加载 baselines.json 失败: {e}")
            return []

    def _batch_get_parameters(self, param_names: list) -> dict:
        """批量获取 MongoDB 参数值 (getParameter)。

        先尝试批量获取，失败则降级为逐参数获取。

        Args:
            param_names: 参数名列表

        Returns:
            {param_name: value} 字典，获取失败的参数值为 'N/A'
        """
        result = {}

        # 先尝试批量获取
        try:
            # 构建批量 getParameter 命令
            cmd = {'getParameter': 1}
            for name in param_names:
                cmd[name] = 1

            batch_result = self.db.command(cmd)

            # 提取返回值
            for name in param_names:
                if name in batch_result:
                    result[name] = batch_result[name]
                else:
                    result[name] = 'N/A'

            print(f"[OK] 批量获取 {len(param_names)} 个参数")
            return result

        except Exception as e:
            print(f"[WARN] 批量 getParameter 失败，降级为逐参数: {e}")

        # 降级：逐参数获取
        for name in param_names:
            try:
                single_result = self.db.command({'getParameter': 1, name: 1})
                if name in single_result:
                    result[name] = single_result[name]
                else:
                    result[name] = 'N/A'
            except Exception as e:
                err_str = str(e).lower()
                if 'not authorized' in err_str or 'unauthorized' in err_str:
                    print(f"[WARN] 获取参数 {name}: 权限不足")
                else:
                    print(f"[WARN] 获取参数 {name} 失败: {e}")
                result[name] = 'N/A'

        return result

    @staticmethod
    def _stringify_value(value) -> str:
        """将参数值转为字符串表示。

        Args:
            value: 参数值（可能是 int/float/bool/str/list 等）

        Returns:
            字符串表示
        """
        if value is None:
            return 'N/A'
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, list):
            return ','.join(str(v) for v in value)
        return str(value)

    @staticmethod
    def _compare_mongo_baseline(current: str, operator: str,
                                 expected: str, expected_min: str,
                                 expected_max: str) -> str:
        """对比 MongoDB 基线值。

        支持的 operator: ==, !=, >=, <=, >, <, LIKE

        Args:
            current: 当前值字符串
            operator: 比较操作符
            expected: 期望值
            expected_min: 最小值（范围比较）
            expected_max: 最大值（范围比较）

        Returns:
            'PASS' / 'FAIL' / 'SKIP' / 'UNKNOWN'
        """
        if current in ('N/A', 'N/A (版本不支持)', ''):
            return 'SKIP'

        try:
            if operator == 'LIKE':
                # 字符串包含
                return 'PASS' if expected.lower() in current.lower() else 'FAIL'

            # 尝试数值比较
            try:
                cur_num = float(current)
                exp_num = float(expected) if expected else 0
            except (ValueError, TypeError):
                # 字符串比较
                cur_num = None
                exp_num = None

            if operator == '==':
                if cur_num is not None:
                    return 'PASS' if cur_num == exp_num else 'FAIL'
                return 'PASS' if current.lower() == expected.lower() else 'FAIL'
            elif operator == '!=':
                if cur_num is not None:
                    return 'PASS' if cur_num != exp_num else 'FAIL'
                return 'PASS' if current.lower() != expected.lower() else 'FAIL'
            elif operator == '>=':
                if cur_num is not None:
                    return 'PASS' if cur_num >= exp_num else 'FAIL'
                return 'UNKNOWN'
            elif operator == '<=':
                if cur_num is not None:
                    return 'PASS' if cur_num <= exp_num else 'FAIL'
                return 'UNKNOWN'
            elif operator == '>':
                if cur_num is not None:
                    return 'PASS' if cur_num > exp_num else 'FAIL'
                return 'UNKNOWN'
            elif operator == '<':
                if cur_num is not None:
                    return 'PASS' if cur_num < exp_num else 'FAIL'
                return 'UNKNOWN'
            else:
                return 'UNKNOWN'

        except Exception:
            return 'UNKNOWN'


# ════════════════════════════════════════════════════════════
# 插件任务配置（供 plugin_loader / web_ui 调用）
# ════════════════════════════════════════════════════════════

def get_task_config():
    """返回插件任务配置（供 plugin_loader.get_plugin_task_config 调用）。

    Returns:
        配置字典，结构与 web_ui.py task_configs 一致
    """
    return {
        'module_name': 'main_plugin',
        'plugin_path': str(Path(__file__).parent),
        'main_file': 'main_plugin.py',
        'connect_test': _plugin_test_connection,
        'connect_test_args': lambda info: [info],
        'getdata_args': lambda info: (
            [info.get('ip', ''), int(info.get('port', 27017) or 27017),
             info.get('user', ''), info.get('password', '')],
            {
                'ssh_info': {
                    'database': info.get('database', 'admin'),
                    'connect_mode': info.get('connect_mode', 'standard'),
                    'auth_source': info.get('auth_source', 'admin'),
                    'auth_mechanism': info.get('auth_mechanism', ''),
                    'replica_set': info.get('replica_set', ''),
                    'tls': info.get('tls', False),
                    'tls_ca_file': info.get('tls_ca_file', ''),
                    'tls_cert_key_file': info.get('tls_cert_key_file', ''),
                    'tls_allow_invalid_certs': info.get('tls_allow_invalid_certs', False),
                },
                'template_id': info.get('template_id'),
            }
        ),
        'conn_attr': '',  # MongoDB getData 返回包装对象，跳过 conn_attr 检查
        'filename_key': 'webui.mongodb_report_filename',
        'history_db_type': 'mongodb',
        'instance_prefix': 'mongodb',
        'error_task_name': 'MongoDB',
        'log_start_key': 'webui.log_mongodb_start',
        'err_module_key': 'webui.err_mongodb_module',
        'label_default': 'MongoDB',
        'db_name_default': 'admin',
        'smart_analyze': 'smart_analyze_mongodb',
    }


def _plugin_test_connection(info: dict):
    """插件连接测试函数（供 web_ui 调用）。

    Args:
        info: 包含 ip/port/user/password/database 等连接信息的字典

    Returns:
        (ok: bool, msg: str)
    """
    try:
        inspector = MongodbInspector(
            host=info.get('ip', info.get('host', '')),
            port=int(info.get('port', 27017) or 27017),
            user=info.get('user', ''),
            password=info.get('password', ''),
            database=info.get('database', 'admin'),
            ssh_info={
                'database': info.get('database', 'admin'),
                'connect_mode': info.get('connect_mode', 'standard'),
                'auth_source': info.get('auth_source', 'admin'),
                'auth_mechanism': info.get('auth_mechanism', ''),
                'replica_set': info.get('replica_set', ''),
                'tls': info.get('tls', False),
                'tls_ca_file': info.get('tls_ca_file', ''),
                'tls_cert_key_file': info.get('tls_cert_key_file', ''),
                'tls_allow_invalid_certs': info.get('tls_allow_invalid_certs', False),
            },
        )
        return inspector.test_connection()
    except Exception as e:
        return False, str(e)


# ════════════════════════════════════════════════════════════
# API 兼容性（供 web_ui.py 旧代码调用）
# ════════════════════════════════════════════════════════════

def getData(ip, port, user, password, ssh_info=None, template_id=None):
    """原有 API - 创建 MongodbInspector 实例并返回包装对象。

    与 MySQL getData 模式一致：返回 CompatWrapper 对象，
    web_ui.py 通过 wrapper.checkdb('builtin') 触发采集并获取 context。
    连接测试已在 getData 之前由 connect_test 完成，此处不再重复连接。

    Args:
        ip: MongoDB 服务器 IP 地址
        port: MongoDB 服务端口
        user: MongoDB 登录用户名
        password: MongoDB 登录密码
        ssh_info: SSH 连接信息字典（也包含 MongoDB 专用参数）
        template_id: 巡检模板 ID（可选）

    Returns:
        CompatWrapper 对象（总是返回，连接/采集失败由 checkdb 返回 None 处理）
    """
    database = (ssh_info or {}).get('database', 'admin')
    inspector = MongodbInspector(ip, port, user, password, database, ssh_info, template_id)

    class CompatWrapper:
        """兼容 web_ui.py 调用约定的包装对象。"""
        def __init__(self, inspector):
            self.inspector = inspector
            self.client = inspector.client  # conn_attr（当前设为空跳过检查）
        def checkdb(self, sqlfile=''):
            result = self.inspector.collect_data()
            if isinstance(result, dict):
                return result
            return None
        def generate_report(self, output_file, inspector_name="Jack"):
            return self.inspector.generate_report(output_file, inspector_name)

    return CompatWrapper(inspector)


def test_connection(host, port, user, password, **kwargs):
    """连接测试入口（供 web_ui test_plugin_connection 调用）。

    接受位置参数 (host, port, user, password) + 关键字参数 (MongoDB 专用参数)，
    与 test_plugin_connection 的调用约定兼容。

    Args:
        host: 主机地址
        port: 端口
        user: 用户名
        password: 密码
        **kwargs: MongoDB 专用参数 (connect_mode, auth_source, auth_mechanism,
                  replica_set, tls, tls_ca_file, tls_cert_key_file,
                  tls_allow_invalid_certs, database 等)

    Returns:
        (ok: bool, msg: str)
    """
    info = {
        'ip': host,
        'host': host,
        'port': int(port or 27017),
        'user': user or '',
        'password': password or '',
        'database': kwargs.get('database', 'admin'),
        'connect_mode': kwargs.get('connect_mode', 'standard'),
        'auth_source': kwargs.get('auth_source', 'admin'),
        'auth_mechanism': kwargs.get('auth_mechanism', ''),
        'replica_set': kwargs.get('replica_set', ''),
        'tls': kwargs.get('tls', False),
        'tls_ca_file': kwargs.get('tls_ca_file', ''),
        'tls_cert_key_file': kwargs.get('tls_cert_key_file', ''),
        'tls_allow_invalid_certs': kwargs.get('tls_allow_invalid_certs', False),
    }
    return _plugin_test_connection(info)


if __name__ == '__main__':
    # 命令行测试
    if len(sys.argv) > 2:
        ip = sys.argv[1]
        port = int(sys.argv[2])
        user = sys.argv[3] if len(sys.argv) > 3 else None
        password = sys.argv[4] if len(sys.argv) > 4 else None

        inspector = MongodbInspector(ip, port, user, password)
        ok, version = inspector.connect()
        if ok:
            print(f"连接成功，版本: {version}")
            context = inspector.collect_data()
            if isinstance(context, dict):
                print(f"采集数据完成，context keys: {list(context.keys())}")
            else:
                print(f"采集数据失败: {context}")
        else:
            print(f"连接失败: {version}")
    else:
        print("用法: python main_plugin.py <ip> <port> [user] [password]")
