#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
字符集与排序规则审计插件
检测各数据库非标准字符集和排序规则

支持数据库类型：MySQL/TiDB/PostgreSQL/Oracle/DM8/SQL Server/YashanDB/Kingbase/IvorySQL/GBase 8s
"""

import os
import sys
import json
from pathlib import Path

# 添加项目根目录到路径
_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root))

from inspection_engine import BaseInspectionEngine


class CharsetAuditInspector(BaseInspectionEngine):
    """
    字符集与排序规则审计巡检器
    支持多种数据库类型
    """
    
    def __init__(self, host, port, user=None, password=None, database=None, ssh_info=None, template_id=None):
        """
        初始化巡检器
        
        :param host: 数据库服务器 IP 地址或主机名
        :param port: 数据库服务端口
        :param user: 登录用户名
        :param password: 登录密码
        :param database: 数据库名
        :param ssh_info: SSH 连接信息
        :param template_id: 巡检模板 ID
        """
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        # db_type 会在 web_ui.py 中根据选择的数据库类型设置
        self.client = None
    
    def connect(self):
        """
        连接数据库 - 需要根据 db_type 动态选择连接方法
        
        返回:
            (ok, version) - ok 为 True 时 version 是版本号，否则是错误信息
        """
        try:
            if self.db_type in ['mysql', 'tidb']:
                import pymysql
                self.client = pymysql.connect(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database=self.database or 'mysql'
                )
                cur = self.client.cursor()
                cur.execute("SELECT VERSION()")
                version = cur.fetchone()[0]
                cur.close()
                return True, version
                
            elif self.db_type in ['postgresql', 'ivorysql', 'kingbase']:
                import psycopg2
                self.client = psycopg2.connect(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database=self.database or 'postgres'
                )
                cur = self.client.cursor()
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]
                cur.close()
                return True, version
                
            elif self.db_type in ['oracle', 'dm8', 'yashandb']:
                # Oracle/DM8/YashanDB 连接逻辑
                # 这里需要使用相应的驱动
                return False, f"暂不支持 {self.db_type} 的连接"
                
            elif self.db_type == 'sqlserver':
                # SQL Server 连接逻辑
                return False, f"暂不支持 {self.db_type} 的连接"
                
            elif self.db_type == 'gbase':
                # GBase 8s 连接逻辑
                return False, f"暂不支持 {self.db_type} 的连接"
                
            else:
                return False, f"不支持的数据库类型: {self.db_type}"
                
        except Exception as e:
            return False, str(e)
    
    def get_template_id(self):
        """
        返回巡检模板 ID
        
        返回:
            template_id: int
        """
        # 字符集审计使用通用的模板 ID
        # 或者根据 db_type 返回不同的模板 ID
        try:
            from inspection_dal import get_templates_by_db_type
            templates = get_templates_by_db_type(self.db_type)
            return templates[0]['id'] if templates else None
        except Exception as e:
            print(f"[CharsetAudit] 获取模板 ID 失败: {e}")
            return None
    
    def collect_data(self, sql_templates=''):
        """
        采集数据 - 根据 db_type 动态选择 SQL 查询
        """
        print(f"\n[CharsetAudit] 开始采集数据 (db_type={self.db_type})...")
        
        # 1. 连接数据库
        ok, version = self.connect()
        if not ok:
            return False, version
        
        # 保存版本信息
        self.context['version'] = [{'VERSION': version}]
        
        # 2. 根据 db_type 执行相应的 SQL 查询
        try:
            if self.db_type in ['mysql', 'tidb']:
                self._collect_mysql_data()
            elif self.db_type in ['postgresql', 'ivorysql', 'kingbase']:
                self._collect_postgresql_data()
            elif self.db_type in ['oracle', 'dm8', 'yashandb']:
                self._collect_oracle_data()
            elif self.db_type == 'sqlserver':
                self._collect_sqlserver_data()
            elif self.db_type == 'gbase':
                self._collect_gbase_data()
            
            # 3. 风险评估
            self._analyze_risks()
            
            print("[CharsetAudit] 数据采集完成")
            return self.context
            
        except Exception as e:
            print(f"[CharsetAudit] 数据采集失败: {e}")
            import traceback
            traceback.print_exc()
            return False, str(e)
    
    def _collect_mysql_data(self):
        """采集 MySQL/TiDB 数据"""
        cur = self.client.cursor()
        
        # 非 UTF8 字符集的表
        cur.execute("""
            SELECT table_schema, table_name, table_collation
            FROM information_schema.tables
            WHERE table_collation IS NOT NULL
              AND table_collation NOT LIKE 'utf8%'
              AND table_collation NOT LIKE 'utf8mb4%'
              AND table_collation NOT LIKE 'latin1%'
              AND table_schema NOT IN ('mysql', 'sys', 'performance_schema', 'information_schema')
            ORDER BY table_schema, table_name
            LIMIT 200
        """)
        rows = cur.fetchall()
        if rows:
            self.context['charset_non_utf_tables'] = [
                {'TABLE_SCHEMA': r[0], 'TABLE_NAME': r[1], 'TABLE_COLLATION': r[2]}
                for r in rows
            ]
        
        # 非 UTF8 默认字符集的数据库
        cur.execute("""
            SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME
            FROM information_schema.SCHEMATA
            WHERE SCHEMA_NAME NOT IN ('mysql', 'sys', 'performance_schema', 'information_schema')
              AND DEFAULT_CHARACTER_SET_NAME NOT IN ('utf8', 'utf8mb4', 'utf8mb3')
        """)
        rows = cur.fetchall()
        if rows:
            self.context['charset_db_default'] = [
                {'CHARSET': r[0], 'COLLATION': r[1]}
                for r in rows
            ]
        
        cur.close()
    
    def _collect_postgresql_data(self):
        """采集 PostgreSQL 数据"""
        cur = self.client.cursor()
        
        # 非 UTF8 编码的数据库
        cur.execute("""
            SELECT datname, pg_encoding_to_char(encoding) AS encoding,
                   datcollate, datctype
            FROM pg_catalog.pg_database
            WHERE encoding != pg_char_to_encoding('UTF8')
            ORDER BY datname
        """)
        rows = cur.fetchall()
        if rows:
            self.context['charset_pg_encoding'] = [
                {'DATNAME': r[0], 'ENCODING': r[1], 'DATCOLLATE': r[2], 'DATCTYPE': r[3]}
                for r in rows
            ]
        
        cur.close()
    
    def _collect_oracle_data(self):
        """采集 Oracle 数据"""
        # TODO: 实现 Oracle 数据采集
        pass
    
    def _collect_sqlserver_data(self):
        """采集 SQL Server 数据"""
        # TODO: 实现 SQL Server 数据采集
        pass
    
    def _collect_gbase_data(self):
        """采集 GBase 8s 数据"""
        # TODO: 实现 GBase 数据采集
        pass
    
    def _analyze_risks(self):
        """风险评估"""
        risks = []
        
        # MySQL 表级排序规则
        if 'charset_non_utf_tables' in self.context:
            rows = self.context['charset_non_utf_tables']
            row_count = len(rows)
            
            if row_count > 100:
                risks.append({
                    'level': 'HIGH',
                    'title': f'发现 {row_count} 个表使用非 UTF8 排序规则',
                    'description': '大量表的排序规则非 utf8/utf8mb4，存在跨平台兼容性风险。',
                    'suggestion': '1) 评估这些表是否可以用 utf8mb4 替代；2) 使用 ALTER TABLE ... CONVERT TO CHARACTER SET utf8mb4 转换'
                })
            elif row_count > 10:
                risks.append({
                    'level': 'MEDIUM',
                    'title': f'发现 {row_count} 个表使用非标准排序规则',
                    'suggestion': '建议逐个评估，统一为 utf8mb4 字符集'
                })
        
        # MySQL 数据库级默认字符集
        if 'charset_db_default' in self.context:
            rows = self.context['charset_db_default']
            if rows:
                risks.append({
                    'level': 'HIGH',
                    'title': f'{len(rows)} 个数据库默认字符集非 UTF8',
                    'description': '涉及的数据库：' + ', '.join([r['CHARSET'] for r in rows[:10]]),
                    'suggestion': '使用 ALTER DATABASE ... CHARACTER SET utf8mb4 更改默认字符集'
                })
        
        # 保存风险评估结果
        self.context['risks'] = risks
        print(f"[CharsetAudit] 风险评估完成，发现 {len(risks)} 个风险")


# 兼容旧接口的全局函数
def getData(ip, port, user, password, ssh_info=None, template_id=None):
    """
    兼容旧接口的 getData() 函数
    """
    database = ssh_info.get('database', 'mysql') if ssh_info else 'mysql'
    inspector = CharsetAuditInspector(ip, port, user, password, database, ssh_info, template_id)
    return inspector.collect_data()


if __name__ == '__main__':
    # 测试代码
    print("字符集审计插件")
    print("用法: python main_plugin.py <ip> <port> [user] [password] [db_type]")
