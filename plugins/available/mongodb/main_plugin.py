#!/usr/bin/env python3
# -*- coding:utf-8 -*-

"""
MongoDB 数据库巡检插件
继承 BaseInspectionEngine，实现 mongodb 数据库巡检
"""

import os
import json
import sys
from pathlib import Path

# 添加项目根目录到路径，以便导入 BaseInspectionEngine
_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root))

from inspection_engine import BaseInspectionEngine


class MongodbInspector(BaseInspectionEngine):
    """
    MongoDB 巡检器
    继承 BaseInspectionEngine，覆盖 collect_data() 方法以支持 MongoDB 命令
    """
    
    def __init__(self, host, port, user=None, password=None, database=None, ssh_info=None, template_id=None):
        """
        初始化 MongoDB 巡检器
        
        :param host: MongoDB 服务器 IP 地址或主机名
        :param port: MongoDB 服务端口
        :param user: MongoDB 登录用户名（可选）
        :param password: MongoDB 登录密码（可选）
        :param database: 要连接的数据库名（可选，默认 'admin'）
        :param ssh_info: SSH 连接信息字典（可选）
        :param template_id: 巡检模板 ID（可选）
        """
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'mongodb'
        self.client = None
        self.db = None
    
    def connect(self):
        """
        连接 MongoDB 数据库
        
        返回:
            (ok, version) - ok 为 True 时 version 是版本号，否则是错误信息
        """
        try:
            import pymongo
            from pymongo import MongoClient
            
            # 构建连接 URI
            if self.user and self.password:
                uri = f"mongodb://{self.user}:{self.password}@{self.host}:{self.port}/{self.database or 'admin'}"
            else:
                uri = f"mongodb://{self.host}:{self.port}/{self.database or 'admin'}"
            
            self.client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            self.db = self.client[self.database or 'admin']
            
            # 获取版本信息
            version_info = self.db.command('buildInfo')
            version = version_info.get('version', 'unknown')
            
            print(f"[MongoDB] 连接成功，版本: {version}")
            return True, version
            
        except Exception as e:
            print(f"[MongoDB] 连接失败: {e}")
            return False, str(e)
    
    def get_template_id(self):
        """
        返回 inspection_template 表的 template_id
        
        返回:
            template_id: int
        """
        try:
            from inspection_dal import get_templates_by_db_type
            templates = get_templates_by_db_type("mongodb")
            return templates[0]['id'] if templates else None
        except Exception as e:
            print(f"[MongoDB] 获取模板 ID 失败: {e}")
            return None
    
    def _load_chapters_from_db(self):
        """从 sql_templates.json 加载章节结构（覆盖父类方法）"""
        try:
            import json
            from pathlib import Path
            
            # 加载 sql_templates.json
            template_path = Path(__file__).parent / 'sql_templates.json'
            if not template_path.exists():
                print("[WARN] sql_templates.json 不存在")
                self.context['_chapters'] = []
                return []
            
            with open(template_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 转换格式
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
    
    def collect_data(self, sql_templates=''):
        """
        采集数据 - 覆盖父类方法，使用 MongoDB 命令
        """
        print("\n[MongoDB] 开始采集数据...")
        
        # 1. 连接数据库
        ok, version = self.connect()
        if not ok:
            return False, version
        
        # 统一保存版本号到 context
        self.context['version'] = [{'VERSION': version}]
        
        # 2. 执行 MongoDB 命令并采集数据
        try:
            # 版本信息
            version_info = self.db.command('buildInfo')
            self.context['mongodb_version'] = [{
                'VERSION': version_info.get('version', 'unknown'),
                'GIT_VERSION': version_info.get('gitVersion', 'unknown')
            }]
            
            # 服务器状态
            server_status = self.db.command('serverStatus')
            self.context['mongodb_server_status'] = [{
                'VERSION': version,
                'OPCOUNTERS': json.dumps(server_status.get('opcounters', {})),
                'MEMORY': json.dumps(server_status.get('mem', {})),
                'CONNECTIONS': json.dumps(server_status.get('connections', {}))
            }]
            
            # 数据库统计信息
            db_stats = self.db.command('dbStats')
            self.context['mongodb_db_stats'] = [{
                'DB_NAME': self.database or 'admin',
                'DATA_SIZE': db_stats.get('dataSize', 0),
                'STORAGE_SIZE': db_stats.get('storageSize', 0),
                'INDEX_SIZE': db_stats.get('indexSize', 0),
                'NUM_COLLECTIONS': db_stats.get('collections', 0),
                'NUM_INDEXES': db_stats.get('indexes', 0)
            }]
            
            # 采集更多有价值的数据
            self._collect_additional_data()
            
            print("[MongoDB] 数据采集完成")
            return self.context
            
        except Exception as e:
            print(f"[MongoDB] 数据采集失败: {e}")
            import traceback
            traceback.print_exc()
            return False, str(e)
    
    def _collect_additional_data(self):
        """采集更多有价值的数据"""
        try:
            # 复制集状态（如果配置了复制集）
            try:
                repl_status = self.db.command('replSetGetStatus')
                self.context['mongodb_repl_status'] = [{
                    'STATE': repl_status.get('myState', 'unknown'),
                    'MEMBERS': json.dumps(repl_status.get('members', []))
                }]
            except Exception:
                # 不是复制集，忽略
                pass
            
            # 慢查询信息
            profile_info = self.db.command('profile', -1)  # 获取当前 profiling 级别
            self.context['mongodb_profile'] = [{
                'LEVEL': profile_info.get('was', 0),
                'SLOW_MS': profile_info.get('slowms', 100)
            }]
            
            # 索引使用情况（采集慢查询）
            try:
                # 尝试获取慢查询集合
                if 'system.profile' in self.db.list_collection_names():
                    slow_queries = list(self.db['system.profile'].find().limit(10))
                    if slow_queries:
                        self.context['mongodb_slow_queries'] = [{
                            'COUNT': len(slow_queries),
                            'SAMPLE': json.dumps(slow_queries[0], default=str)
                        }]
            except Exception:
                pass
            
            # 数据库列表
            db_list = self.client.list_database_names()
            db_info_list = []
            for db_name in db_list:
                if db_name in ['admin', 'local', 'config']:
                    continue
                try:
                    db_stats = self.client[db_name].command('dbStats')
                    db_info_list.append({
                        'DB_NAME': db_name,
                        'DATA_SIZE': db_stats.get('dataSize', 0),
                        'STORAGE_SIZE': db_stats.get('storageSize', 0),
                        'INDEX_SIZE': db_stats.get('indexSize', 0)
                    })
                except Exception:
                    pass
            
            if db_info_list:
                self.context['mongodb_databases'] = db_info_list
            
            print("[MongoDB] 额外数据采集完成")
            
        except Exception as e:
            print(f"[MongoDB] 采集额外数据失败: {e}")


# ── 保留原有 API 兼容性（供 web_ui.py 旧代码调用）────────────────────
def getData(ip, port, user, password, ssh_info=None, template_id=None):
    """
    原有 API - 创建 MongodbInspector 实例
    
    :param ip: MongoDB 服务器 IP 地址
    :param port: MongoDB 服务端口
    :param user: MongoDB 登录用户名
    :param password: MongoDB 登录密码
    :param ssh_info: SSH 连接信息字典（可选）
    :param template_id: 巡检模板 ID（可选）
    """
    database = ssh_info.get('database', 'admin') if ssh_info else 'admin'
    inspector = MongodbInspector(ip, port, user, password, database, ssh_info, template_id)
    return inspector.collect_data()


if __name__ == '__main__':
    # 测试代码
    import sys
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
            print(f"采集数据完成，context keys: {list(context.keys())}")
        else:
            print(f"连接失败: {version}")
    else:
        print("用法: python main_plugin.py <ip> <port> [user] [password]")
