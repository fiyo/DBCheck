# -*- coding: utf-8 -*-
"""
Redis 巡检公共逻辑（单机 / 集群共享采集）。

仅定义 RedisCommonMixin，不依赖 BaseInspectionEngine：
  - 本模块被 redis/main_plugin.py 与 redis-cluster/main_plugin.py 通过
    importlib 按文件绝对路径 + 唯一模块名加载，避免与同名模块互相污染。
  - 采集方法统一把结果写入 self.context[<key>]（list[dict]，供报告表格渲染），
    与 mongodb 插件约定一致。

约定（由具体 Inspector 在运行时提供）：
  - self.client   : redis.Redis 或 redis.cluster.RedisCluster 实例
  - self.context  : dict（来自 BaseInspectionEngine）
  - self._tag     : str（日志前缀，如 'Redis' / 'RedisCluster'）
"""

import traceback
from datetime import datetime


class RedisCommonMixin:
    """单机与集群共享的 Redis 采集逻辑。"""

    # ──────────────────────────────────────────────
    # 工具
    # ──────────────────────────────────────────────
    @staticmethod
    def _to_number(v, default=0):
        try:
            if v is None or v == '':
                return default
            return int(v)
        except (ValueError, TypeError):
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

    @staticmethod
    def _mb(v):
        """字节转 MB（保留两位小数）。"""
        return round(RedisCommonMixin._to_number(v) / 1024.0 / 1024.0, 2)

    @staticmethod
    def _fmt_ts(epoch):
        try:
            return datetime.utcfromtimestamp(int(epoch)).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return ''

    # ──────────────────────────────────────────────
    # 采集：版本
    # ──────────────────────────────────────────────
    def _collect_version(self):
        try:
            info = self.client.info('server')
            version = info.get('redis_version', 'unknown')
            self.context['redis_version'] = [{
                'REDIS_VERSION': version,
                'REDIS_MODE': info.get('redis_mode', 'standalone'),
                'ARCH_BITS': info.get('arch_bits', ''),
                'OS': info.get('os', ''),
                'UPTIME_DAYS': round(int(info.get('uptime_in_seconds', 0)) / 86400.0, 2),
                'PROCESS_ID': info.get('process_id', ''),
            }]
            print(f"[{self._tag}] 版本: {version} (mode={info.get('redis_mode', 'standalone')})")
        except Exception as e:
            print(f"[{self._tag}] 采集版本失败: {e}")
            self.context['redis_version'] = [{'REDIS_VERSION': 'unknown', 'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 采集：服务概览
    # ──────────────────────────────────────────────
    def _collect_server(self):
        try:
            info = self.client.info('server')
            self.context['redis_server'] = [{
                'RUN_ID': info.get('run_id', ''),
                'TCP_PORT': info.get('tcp_port', ''),
                'REDIS_MODE': info.get('redis_mode', ''),
                'EXECUTABLE': info.get('executable', ''),
                'CONFIG_FILE': info.get('config_file', ''),
                'UP_TIME_DAYS': info.get('uptime_in_days', ''),
                'HZ': info.get('hz', ''),
            }]
        except Exception as e:
            print(f"[{self._tag}] 采集服务概览失败: {e}")
            self.context['redis_server'] = [{'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 采集：内存
    # ──────────────────────────────────────────────
    def _collect_memory(self):
        try:
            m = self.client.info('memory')
            maxmem = self._to_number(m.get('maxmemory', 0))
            self.context['redis_memory'] = [{
                'USED_MB': self._mb(m.get('used_memory', 0)),
                'USED_RSS_MB': self._mb(m.get('used_memory_rss', 0)),
                'PEAK_MB': self._mb(m.get('used_memory_peak', 0)),
                'MAXMEMORY_MB': self._mb(maxmem) if maxmem else 0,
                'MAXMEMORY_POLICY': m.get('maxmemory_policy', ''),
                'FRAG_RATIO': m.get('mem_fragmentation_ratio', 0),
                'FRAGMENTATION_BYTES': m.get('mem_fragmentation_bytes', ''),
                'TOTAL_SYSTEM_MB': self._mb(m.get('total_system_memory', 0)),
            }]
        except Exception as e:
            print(f"[{self._tag}] 采集内存失败: {e}")
            self.context['redis_memory'] = [{'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 采集：客户端与连接
    # ──────────────────────────────────────────────
    def _collect_clients(self):
        try:
            c = self.client.info('clients')
            maxclients = self._to_number(c.get('maxclients', 0))
            connected = self._to_number(c.get('connected_clients', 0))
            self.context['redis_clients'] = [{
                'CONNECTED_CLIENTS': connected,
                'BLOCKED_CLIENTS': c.get('blocked_clients', 0),
                'MAXCLIENTS': maxclients,
                'LONGEST_OUTPUT_LIST': c.get('client_longest_output_list', 0),
                'BIGGEST_INPUT_BUF': c.get('client_biggest_input_buf', 0),
                'USED_MEMORY_CLIENTS_MB': self._mb(c.get('used_memory_clients', 0))
                if c.get('used_memory_clients') is not None else '',
            }]
            # 连接数使用率（供规则引擎自动计算 conn_pct）
            if maxclients:
                self.context['redis_conn_pct'] = round(connected / maxclients * 100, 2)
        except Exception as e:
            print(f"[{self._tag}] 采集客户端失败: {e}")
            self.context['redis_clients'] = [{'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 采集：持久化
    # ──────────────────────────────────────────────
    def _collect_persistence(self):
        try:
            p = self.client.info('persistence')
            self.context['redis_persistence'] = [{
                'RDB_LAST_BGSAVE_STATUS': p.get('rdb_last_bgsave_status', ''),
                'RDB_LAST_SAVE_TIME': self._fmt_ts(p.get('rdb_last_save_time', 0)),
                'RDB_CHANGES_SINCE_LAST_SAVE': p.get('rdb_changes_since_last_save', 0),
                'RDB_BGSAVE_IN_PROGRESS': p.get('rdb_bgsave_in_progress', 0),
                'AOF_ENABLED': p.get('aof_enabled', 0),
                'AOF_LAST_BGREWRITE_STATUS': p.get('aof_last_bgrewrite_status', ''),
                'AOF_LAST_WRITE_STATUS': p.get('aof_last_write_status', ''),
                'AOF_CURRENT_SIZE_MB': self._mb(p.get('aof_current_size', 0)),
                'AOF_BASE_SIZE_MB': self._mb(p.get('aof_base_size', 0)),
            }]
        except Exception as e:
            print(f"[{self._tag}] 采集持久化失败: {e}")
            self.context['redis_persistence'] = [{'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 采集：性能统计
    # ──────────────────────────────────────────────
    def _collect_stats(self):
        try:
            s = self.client.info('stats')
            self.context['redis_stats'] = [{
                'TOTAL_CONNECTIONS_RECEIVED': s.get('total_connections_received', 0),
                'TOTAL_COMMANDS_PROCESSED': s.get('total_commands_processed', 0),
                'INSTANTANEOUS_OPS_PER_SEC': s.get('instantaneous_ops_per_sec', 0),
                'INSTANTANEOUS_INPUT_KBPS': s.get('instantaneous_input_kbps', 0),
                'INSTANTANEOUS_OUTPUT_KBPS': s.get('instantaneous_output_kbps', 0),
                'TOTAL_NET_INPUT_BYTES': s.get('total_net_input_bytes', 0),
                'TOTAL_NET_OUTPUT_BYTES': s.get('total_net_output_bytes', 0),
                'EXPIRED_KEYS': s.get('expired_keys', 0),
                'EVICTED_KEYS': s.get('evicted_keys', 0),
                'KEYSPACE_HITS': s.get('keyspace_hits', 0),
                'KEYSPACE_MISSES': s.get('keyspace_misses', 0),
                'REJECTED_CONNECTIONS': s.get('rejected_connections', 0),
            }]
        except Exception as e:
            print(f"[{self._tag}] 采集性能统计失败: {e}")
            self.context['redis_stats'] = [{'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 采集：复制与高可用
    # ──────────────────────────────────────────────
    def _collect_replication(self):
        try:
            r = self.client.info('replication')
            self.context['redis_replication'] = [{
                'ROLE': r.get('role', ''),
                'CONNECTED_SLAVES': r.get('connected_slaves', 0),
                'MASTER_REPL_OFFSET': r.get('master_repl_offset', ''),
                'REPL_BACKLOG_SIZE_MB': self._mb(r.get('repl_backlog_size', 0)),
                'MASTER_LINK_STATUS': r.get('master_link_status', ''),
                'MASTER_HOST': r.get('master_host', ''),
                'MASTER_PORT': r.get('master_port', ''),
                'SLAVE_READ_ONLY': r.get('slave_read_only', ''),
                'REPLICA_READ_ONLY': r.get('replica_read_only', ''),
            }]
        except Exception as e:
            print(f"[{self._tag}] 采集复制失败: {e}")
            self.context['redis_replication'] = [{'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 采集：CPU
    # ──────────────────────────────────────────────
    def _collect_cpu(self):
        try:
            cpu = self.client.info('cpu')
            self.context['redis_cpu'] = [{
                'USED_CPU_SYS': cpu.get('used_cpu_sys', 0),
                'USED_CPU_USER': cpu.get('used_cpu_user', 0),
                'USED_CPU_SYS_CHILDREN': cpu.get('used_cpu_sys_children', 0),
                'USED_CPU_USER_CHILDREN': cpu.get('used_cpu_user_children', 0),
            }]
        except Exception as e:
            print(f"[{self._tag}] 采集 CPU 失败: {e}")
            self.context['redis_cpu'] = [{'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 采集：键空间
    # ──────────────────────────────────────────────
    def _collect_keyspace(self):
        try:
            ks = self.client.info('keyspace')
            rows = []
            if isinstance(ks, dict):
                for db, stat in ks.items():
                    if isinstance(stat, dict):
                        rows.append({
                            'DB': db,
                            'KEYS': stat.get('keys', 0),
                            'EXPIRES': stat.get('expires', 0),
                            'AVG_TTL': stat.get('avg_ttl', 0),
                        })
            self.context['redis_keyspace'] = rows if rows else \
                [{'DB': '-', 'KEYS': 0, 'EXPIRES': 0, 'AVG_TTL': 0}]
        except Exception as e:
            print(f"[{self._tag}] 采集键空间失败: {e}")
            self.context['redis_keyspace'] = [{'DB': '-', 'KEYS': 0, 'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 采集：慢查询
    # ──────────────────────────────────────────────
    def _collect_slowlog(self):
        try:
            logs = self.client.slowlog_get(10)
            rows = []
            for entry in logs:
                rows.append({
                    'ID': entry.id,
                    'TIME': self._fmt_ts(entry.start_time),
                    'DURATION_MS': round(entry.duration / 1000.0, 3),
                    'COMMAND': (entry.command or '')[:200],
                })
            self.context['redis_slowlog'] = rows
        except Exception as e:
            print(f"[{self._tag}] 采集慢查询失败: {e}")
            self.context['redis_slowlog'] = [{'ID': '-', 'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 基线：关键配置
    # ──────────────────────────────────────────────
    def _check_baselines(self):
        """采集关键 CONFIG 参数，存入 redis_config（供报告与规则引擎使用）。"""
        try:
            keys = ['maxmemory', 'maxmemory-policy', 'maxclients', 'appendonly',
                    'requirepass', 'save', 'stop-writes-on-bgsave-error',
                    'lazyfree-lazy-expire', 'activedefrag', 'tcp-keepalive',
                    'timeout', 'hz', 'cluster-enabled']
            cfg = self.client.config_get('*')
            rows = []
            for k in keys:
                v = cfg.get(k, '')
                if k == 'requirepass':
                    v = '******' if v else ''
                rows.append({'PARAM_NAME': k, 'PARAM_VALUE': v})
            self.context['redis_config'] = rows
        except Exception as e:
            print(f"[{self._tag}] 采集配置失败: {e}")
            self.context['redis_config'] = [{'PARAM_NAME': 'requirepass', 'PARAM_VALUE': ''}]

    # ──────────────────────────────────────────────
    # 集群采集（仅 redis-cluster 调用）
    # ──────────────────────────────────────────────
    def _collect_cluster(self):
        try:
            cinfo = self.client.cluster('info')
            if isinstance(cinfo, str):
                cinfo = dict(kv.split(':', 1) for kv in cinfo.strip().split('\n')
                             if ':' in kv)
            self.context['redis_cluster_info'] = [{
                'CLUSTER_STATE': cinfo.get('cluster_state', ''),
                'CLUSTER_SLOTS_ASSIGNED': cinfo.get('cluster_slots_assigned', ''),
                'CLUSTER_SLOTS_OK': cinfo.get('cluster_slots_ok', ''),
                'CLUSTER_SLOTS_FAIL': cinfo.get('cluster_slots_fail', ''),
                'CLUSTER_SLOTS_PFAIL': cinfo.get('cluster_slots_pfail', ''),
                'CLUSTER_KNOWN_NODES': cinfo.get('cluster_known_nodes', ''),
                'CLUSTER_SIZE': cinfo.get('cluster_size', ''),
                'CLUSTER_CURRENT_EPOCH': cinfo.get('cluster_current_epoch', ''),
            }]
        except Exception as e:
            print(f"[{self._tag}] 采集集群概览失败: {e}")
            self.context['redis_cluster_info'] = [{'CLUSTER_STATE': 'unknown', 'ERROR': str(e)[:200]}]

        try:
            nodes = self.client.cluster('nodes')
            rows = []
            if isinstance(nodes, str):
                for ln in [n for n in nodes.strip().split('\n') if n]:
                    parts = ln.split()
                    if len(parts) < 8:
                        continue
                    node_id, addr, flags = parts[0], parts[1], parts[2]
                    role = 'master' if 'master' in flags else ('slave' if 'slave' in flags else flags)
                    link = parts[7] if len(parts) > 7 else ''
                    slots = ' '.join(parts[8:]) if len(parts) > 8 else ''
                    rows.append({
                        'NODE_ID': node_id[:8] + '...',
                        'ADDR': addr,
                        'ROLE': role,
                        'LINK': link,
                        'SLOTS': slots[:120],
                    })
            else:
                for nid, nd in nodes.items():
                    flags = nd.get('flags', '')
                    role = 'master' if 'master' in flags else ('slave' if 'slave' in flags else flags)
                    rows.append({
                        'NODE_ID': nid[:8] + '...',
                        'ADDR': f"{nd.get('host', '')}:{nd.get('port', '')}",
                        'ROLE': role,
                        'LINK': nd.get('link-state', ''),
                        'SLOTS': str(nd.get('slots', ''))[:120],
                    })
            self.context['redis_cluster_nodes'] = rows
        except Exception as e:
            print(f"[{self._tag}] 采集集群节点失败: {e}")
            self.context['redis_cluster_nodes'] = [{'NODE_ID': '-', 'ERROR': str(e)[:200]}]

    # ──────────────────────────────────────────────
    # 智能分析 + AI 诊断（采集完成后调用）
    # ──────────────────────────────────────────────
    def run_intelligent_analysis(self):
        """采集完成后补充风险分析（auto_analyze）与 AI 诊断（ai_advice）。

        原因：redis 巡检链路只走 collect_data() + generate_report()，
        未走基类 run() 的智能分析 / AI 诊断步骤；若不在此补充，
        报告第14章（风险与建议）与第15章（AI 诊断）将始终为空。
        """
        import os
        import json as _json

        # 1) 智能分析：运行内置规则引擎
        try:
            from analyzer import smart_analyze_redis, smart_analyze_redis_cluster
            if self.db_type == 'redis-cluster':
                self.context['auto_analyze'] = list(smart_analyze_redis_cluster(self.context))
            else:
                self.context['auto_analyze'] = list(smart_analyze_redis(self.context))
        except Exception as e:
            print(f"[{self._tag}] 智能分析失败: {e}")
            self.context['auto_analyze'] = []

        # 2) AI 诊断（可选，依赖 dbc_config.json 配置；未配置时为空，报告显示「未启用」）
        self.context['ai_advice'] = ''
        try:
            from analyzer import AIAdvisor
            # 项目根目录的 dbc_config.json（插件自身目录下没有该文件，需向上回溯查找）
            _here = os.path.dirname(os.path.abspath(__file__))
            cfg_path = None
            _cur = _here
            for _ in range(6):
                _cand = os.path.join(_cur, 'dbc_config.json')
                if os.path.exists(_cand):
                    cfg_path = _cand
                    break
                _parent = os.path.dirname(_cur)
                if _parent == _cur:
                    break
                _cur = _parent
            if not cfg_path:
                cfg_path = os.path.join(_here, 'dbc_config.json')
            ai_cfg = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    ai_cfg = _json.load(f).get('ai', {})
            _online_enabled = ai_cfg.get('online_enabled', False)
            if _online_enabled:
                _adv_backend = ai_cfg.get('online_backend', 'openai')
                _adv_api_url = ai_cfg.get('online_api_url') or None
                _adv_model = ai_cfg.get('online_model') or None
            else:
                _adv_backend = ai_cfg.get('backend')
                _adv_api_url = ai_cfg.get('api_url')
                _adv_model = ai_cfg.get('model')
            advisor = AIAdvisor(
                backend=_adv_backend,
                api_key=ai_cfg.get('api_key'),
                api_url=_adv_api_url,
                model=_adv_model
            )
            if advisor.enabled:
                label = self.context.get('co_name', [{}])[0].get('DB_NAME', 'Unknown')
                ai_advice = advisor.diagnose(
                    self.db_type, label, self.context,
                    self.context.get('auto_analyze', []),
                    lang=getattr(self, '_lang', 'zh')
                )
                self.context['ai_advice'] = ai_advice
        except Exception as e:
            print(f"[{self._tag}] AI 诊断跳过: {e}")
            self.context['ai_advice'] = ''

        # 3) 健康评分依据（供 web_ui 计算 health_score / risk_level）
        self.context['risk_count'] = len(self.context.get('auto_analyze', []))
