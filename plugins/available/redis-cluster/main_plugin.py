#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Redis 集群巡检插件 v1.0
继承 BaseInspectionEngine + RedisCommonMixin，实现 Redis Cluster 巡检。

特性：
  - redis.cluster.RedisCluster 直连（startup_nodes 来自 seed_nodes 或单节点）
  - 复用单机全部 INFO 采集（集群节点同样支持 INFO）
  - CLUSTER INFO / CLUSTER NODES 集群专属采集
  - 风险分析复用 pro/rule_engine（pro/rules/builtin/redis-cluster.yaml）
"""

import os
import sys
import json
import traceback
from datetime import datetime
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

_plugin_dir = str(Path(__file__).parent)
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from inspection_engine import (
    BaseInspectionEngine,
    LocalSystemInfoCollector,
    RemoteSystemInfoCollector,
    get_host_disk_usage,
)
import importlib.util

# 集群插件复用单机插件的 redis_common（按绝对路径 + 唯一模块名加载，避免污染）
_spec = importlib.util.spec_from_file_location(
    "redis_common",
    os.path.join(_plugin_dir, "..", "redis", "redis_common.py"),
)
_CC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_CC)
RedisCommonMixin = _CC.RedisCommonMixin


class RedisClusterInspector(RedisCommonMixin, BaseInspectionEngine):
    """Redis 集群巡检器。"""

    def __init__(self, host, port, user=None, password=None, database=None,
                 ssh_info=None, template_id=None):
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'redis-cluster'
        self._tag = 'RedisCluster'
        self.client = None

    # ──────────────────────────────────────────────
    # 工具：解析 seed_nodes
    # ──────────────────────────────────────────────
    def _seed_nodes_list(self):
        raw = (self.ssh_info or {}).get('seed_nodes', '') if isinstance(self.ssh_info, dict) else ''
        raw = raw or (self.database or '')
        if not raw:
            return []
        try:
            if isinstance(raw, (list, tuple)):
                items = raw
            elif isinstance(raw, str):
                if raw.strip().startswith('['):
                    items = json.loads(raw)
                else:
                    items = [x.strip() for x in raw.split(',') if x.strip()]
            else:
                return []
            nodes = []
            for it in items:
                if isinstance(it, dict):
                    nodes.append({'host': it.get('host'), 'port': int(it.get('port', 6379))})
                elif isinstance(it, str) and ':' in it:
                    h, p = it.rsplit(':', 1)
                    nodes.append({'host': h.strip(), 'port': int(p.strip())})
            return nodes
        except Exception as e:
            print(f"[{self._tag}] 解析 seed_nodes 失败: {e}")
            return []

    # ════════════════════════════════════════════════
    # 连接层
    # ════════════════════════════════════════════════
    def connect(self):
        self._acl_warning = ''
        try:
            import redis
            from redis.cluster import RedisCluster, ClusterNode
            seeds = self._seed_nodes_list() or [{'host': self.host, 'port': int(self.port) or 6379}]
            # redis-py >=5 接受 dict 列表，>=8 要求 ClusterNode 实例；统一包装以兼容两版本
            startup = [ClusterNode(host=d['host'], port=d['port']) for d in seeds]
            _kw = dict(
                startup_nodes=startup,
                password=self.password or None,
                decode_responses=True,
                socket_timeout=10,
                require_full_coverage=False,  # redis>=8 重命名自 skip_full_coverage_check（允许槽未全分配）
                encoding_errors='replace',
                protocol=2,  # 强制 RESP2，兼容老版本/代理后端
            )
            # Redis 6.0+ ACL：user 有值时传 username（传统纯密码模式留空即可）
            if self.user:
                _kw['username'] = self.user
            self.client = RedisCluster(**_kw)
            if not self.client.ping():
                return False, 'PING failed'
            info = self.client.info('server')
            version = info.get('redis_version', 'unknown')
            self.context['version'] = [{'VERSION': version}]
            self.context['db_type'] = 'redis-cluster'
            # Redis < 6.0 不支持 ACL：redis-py 会静默降级为纯密码认证，用户名被忽略
            try:
                _v = [int(x) for x in version.split('.')[:2]]
            except (ValueError, IndexError):
                _v = [9, 9]  # 版本解析失败按新版处理，不提示
            if self.user and _v < [6, 0]:
                self._acl_warning = (
                    f'该 Redis 版本({version})不支持 ACL，'
                    f'用户名"{self.user}"已被忽略，仅使用密码认证'
                )
                print(f"[{self._tag}] WARNING {self._acl_warning}")
            print(f"[{self._tag}] 连接成功，版本: {version}，种子节点: {seeds}")
            return True, version
        except Exception as e:
            print(f"[{self._tag}] 连接失败: {e}")
            return False, str(e)

    def test_connection(self):
        try:
            ok, version = self.connect()
            if ok:
                msg = f"Redis Cluster {version}"
                if getattr(self, '_acl_warning', ''):
                    msg += f"（⚠️ {self._acl_warning}）"
                return True, msg
            return False, version
        except Exception as e:
            return False, str(e)

    # ════════════════════════════════════════════════
    # 模板 / 章节（兜底）
    # ════════════════════════════════════════════════
    def _load_chapters_from_db(self):
        try:
            tp = Path(__file__).parent / 'sql_templates.json'
            if not tp.exists():
                self.context['_chapters'] = []
                return []
            with open(tp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            chapters = [{
                'chapter_number': ch.get('chapter_number', 0),
                'chapter_title_zh': ch.get('chapter_title_zh', ''),
                'chapter_title_en': ch.get('chapter_title_en', ''),
                'queries': ch.get('queries', []),
            } for ch in data.get('chapters', [])]
            self.context['_chapters'] = chapters
            print(f"[{self._tag}] 已从 sql_templates.json 加载 {len(chapters)} 个章节")
            return chapters
        except Exception as e:
            print(f"[{self._tag}] 加载章节失败: {e}")
            self.context['_chapters'] = []
            return []

    def _collect_system_info(self):
        try:
            if getattr(self, 'ssh_info', None) and self.ssh_info.get('ssh_host'):
                _collector = RemoteSystemInfoCollector(
                    host=self.ssh_info['ssh_host'], port=self.ssh_info.get('ssh_port', 22),
                    username=self.ssh_info.get('ssh_user', 'root'),
                    password=self.ssh_info.get('ssh_password'),
                    key_file=self.ssh_info.get('ssh_key_file'),
                )
                if not _collector.connect():
                    _collector = LocalSystemInfoCollector()
            else:
                _collector = LocalSystemInfoCollector()
            _sys_info = _collector.get_system_info()
            _disk_list = _sys_info.get('disk_list') or _sys_info.get('disk') or get_host_disk_usage()
            if isinstance(_disk_list, dict):
                _disk_list = list(_disk_list.values())
            _sys_info['disk_list'] = _disk_list
            self.context.update({"system_info": _sys_info})
        except Exception as e:
            print(f"[{self._tag}] 系统信息采集失败: {e}")
            self.context.update({"system_info": {
                'platform': '未知', 'boot_time': '未知',
                'cpu': {}, 'memory': {},
                'disk_list': [{'device': 'C:', 'mountpoint': 'C:\\', 'fstype': 'NTFS',
                               'total_gb': 0, 'used_gb': 0, 'free_gb': 0, 'usage_percent': 0}],
            }})

    # ════════════════════════════════════════════════
    # 数据采集
    # ════════════════════════════════════════════════
    def collect_data(self, sql_templates=''):
        print(f"\n[{self._tag}] 开始采集数据...")
        ok, version = self.connect()
        if not ok:
            return False, version

        self.context['version'] = [{'VERSION': version}]
        self.context['db_type'] = 'redis-cluster'

        try:
            steps = [
                ('_collect_version', '版本信息'),
                ('_collect_server', '服务概览'),
                ('_collect_memory', '内存使用'),
                ('_collect_clients', '客户端与连接'),
                ('_collect_persistence', '持久化'),
                ('_collect_stats', '性能统计'),
                ('_collect_replication', '复制与高可用'),
                ('_collect_cpu', 'CPU'),
                ('_collect_keyspace', '键空间'),
                ('_collect_cluster', '集群状态'),
                ('_collect_slowlog', '慢查询'),
                ('_check_baselines', '配置基线'),
            ]
            total = len(steps) + 1
            for i, (method, label) in enumerate(steps, 1):
                self.print_progress_bar(i, total, prefix=f'[{self._tag}]',
                                        suffix=f'{label} ({i}/{len(steps)})')
                getattr(self, method)()

            self.print_progress_bar(total, total, prefix=f'[{self._tag}]', suffix='系统资源')
            self._collect_system_info()

            # 智能分析 + AI 诊断（补全报告第14/15章所需数据）
            self.run_intelligent_analysis()

            print(f"[{self._tag}] 数据采集完成，context keys: {list(self.context.keys())}")
            return self.context
        except Exception as e:
            print(f"[{self._tag}] 数据采集失败: {e}")
            traceback.print_exc()
            return False, str(e)
        finally:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass


# ════════════════════════════════════════════════
# 插件任务配置
# ════════════════════════════════════════════════
def get_task_config():
    return {
        'module_name': 'main_plugin',
        'plugin_path': str(Path(__file__).parent),
        'main_file': 'main_plugin.py',
        'connect_test': _plugin_test_connection,
        'connect_test_args': lambda info: [info],
        'getdata_args': lambda info: (
            [info.get('ip', ''), int(info.get('port', 6379) or 6379),
             info.get('user', ''), info.get('password', '')],
            {
                'ssh_info': {
                    'database': info.get('database', ''),
                    'seed_nodes': info.get('seed_nodes', ''),
                },
                'template_id': info.get('template_id'),
            }
        ),
        'conn_attr': '',
        'filename_key': 'webui.redis_cluster_report_filename',
        'history_db_type': 'redis-cluster',
        'instance_prefix': 'redis-cluster',
        'error_task_name': 'RedisCluster',
        'log_start_key': 'webui.log_redis_cluster_start',
        'err_module_key': 'webui.err_redis_cluster_module',
        'label_default': 'RedisCluster',
        'db_name_default': '',
        'smart_analyze': 'smart_analyze_redis_cluster',
    }


def _plugin_test_connection(info: dict):
    try:
        # 集群连接需 seed_nodes（来自 info 或 database 兜底）
        ssh_info = {'seed_nodes': info.get('seed_nodes', '') or info.get('database', '')}
        inspector = RedisClusterInspector(
            host=info.get('ip', info.get('host', '')),
            port=int(info.get('port', 6379) or 6379),
            user=info.get('user', ''),
            password=info.get('password', ''),
            ssh_info=ssh_info,
        )
        return inspector.test_connection()
    except Exception as e:
        return False, str(e)


def getData(ip, port, user, password, ssh_info=None, template_id=None):
    database = (ssh_info or {}).get('database', '')
    inspector = RedisClusterInspector(ip, port, user, password, database, ssh_info, template_id)

    class CompatWrapper:
        def __init__(self, inspector):
            self.inspector = inspector
            self.client = inspector.client

        def checkdb(self, sqlfile=''):
            result = self.inspector.collect_data()
            return result if isinstance(result, dict) else None

        def generate_report(self, output_file, inspector_name="Jack"):
            return self.inspector.generate_report(output_file, inspector_name)

    return CompatWrapper(inspector)


def test_connection(host, port, user, password, **kwargs):
    info = {
        'ip': host, 'host': host,
        'port': int(port or 6379),
        'user': user or '',
        'password': password or '',
        'database': kwargs.get('database', ''),
        'seed_nodes': kwargs.get('seed_nodes', '') or kwargs.get('database', ''),
    }
    return _plugin_test_connection(info)


if __name__ == '__main__':
    if len(sys.argv) > 2:
        ip = sys.argv[1]
        port = int(sys.argv[2])
        user = sys.argv[3] if len(sys.argv) > 3 else None
        password = sys.argv[4] if len(sys.argv) > 4 else None
        seed = sys.argv[5] if len(sys.argv) > 5 else ''
        inspector = RedisClusterInspector(ip, port, user, password, ssh_info={'seed_nodes': seed})
        ok, version = inspector.connect()
        if ok:
            print(f"连接成功，版本: {version}")
            context = inspector.collect_data()
            if isinstance(context, dict):
                print(f"采集完成，context keys: {list(context.keys())}")
            else:
                print(f"采集失败: {context}")
        else:
            print(f"连接失败: {version}")
    else:
        print("用法: python main_plugin.py <ip> <port> [user] [password] [seed_nodes]")
