#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
MariaDB 数据库巡检模块 - 基于 BaseInspectionEngine 重构版本

MariaDB 与 MySQL 协议/参数高度兼容，本模块以**最小改动、最大化复用 MySQL 实现**
为原则：连接逻辑（pymysql）与 MySQL 完全一致，仅 db_type 标识为 'mariadb'，
以便报告模板、基线、规则引擎等按 MariaDB 维度分发。

使用方式：
    from main_mariadb import MariaDBInspector
    inspector = MariaDBInspector(host, port, user, password, database, ssh_info)
    ok, ver = inspector.connect()
    if ok:
        inspector.collect_data()
        inspector.generate_report(output_file, inspector_name)
"""

import os
from inspection_engine import BaseInspectionEngine


class MariaDBInspector(BaseInspectionEngine):
    """
    MariaDB 数据库巡检器 - 继承 BaseInspectionEngine

    只需实现 connect() 方法，其他逻辑全部在基类中！
    connect() 复用 MySQL（pymysql）连接逻辑，因为 MariaDB 与 MySQL 协议兼容。
    """

    def __init__(self, host, port, user, password, database=None, ssh_info=None, template_id=None):
        """
        初始化 MariaDB 巡检器

        :param host: MariaDB 服务器 IP 地址或主机名
        :param port: MariaDB 服务端口（默认 3306）
        :param user: MariaDB 登录用户名
        :param password: MariaDB 登录密码
        :param database: 要连接的数据库名（可选）
        :param ssh_info: SSH 连接信息字典（可选）
        :param template_id: 巡检模板 ID（可选，指定后使用对应模板的 SQL）
        """
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'mariadb'

    def connect(self):
        """
        连接 MariaDB 数据库
        复用 MySQL 的 pymysql 连接逻辑（MariaDB 与 MySQL 协议/参数高度兼容）

        返回:
            (ok, version) - ok 为 True 时 version 是版本号（形如 '10.6.12-MariaDB'），否则是错误信息
        """
        import pymysql
        try:
            self.conn = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database or 'mysql',
                charset='utf8mb4',
                connect_timeout=10,
                read_timeout=60
            )
            self.cursor = self.conn.cursor()
            self.cursor.execute("SELECT VERSION()")
            ver = self.cursor.fetchone()[0]
            return True, ver
        except Exception as e:
            return False, str(e)

    def _customize_queries(self, sql_dict):
        """覆盖 MySQL 不兼容的 SQL 查询（MariaDB 专用）

        MariaDB 与 MySQL 协议兼容，但系统表/字段结构有差异：
        - mysql.user 没有 account_locked / password_lifetime（MariaDB 10.4+ 存于 mysql.global_priv JSON）
        - performance_schema.data_lock_waits / data_locks 在 MariaDB < 10.6 不存在，改用 information_schema.INNODB_LOCK_WAITS / INNODB_TRX
        - performance_schema.global_variables 在 MariaDB < 10.10 不存在，改用 SHOW GLOBAL VARIABLES
        - mysql.role_edges 在 MariaDB 为 mysql.roles_mapping
        - information_schema.INNODB_DATAFILES / INNODB_TABLESPACES 表名跨版本不稳定（<10.6 为 INNODB_SYS_*，>=10.6 去掉前缀），且 INNODB_* 表在 information_schema.TABLES 目录中登记不可靠；已改为用「能否真正 SELECT」做运行时探测（_resolve_innodb_table），两者皆不可用时降级为空结果避免 WARN
        - SHOW BINARY LOGS 在 binlog 未开启时报 1381，改用 @@log_bin 探针避免报错
        """
        # 安全信息 - 用户章节（去掉 MySQL 专有列）
        if 'mysql_users' in sql_dict:
            sql_dict['mysql_users'] = (
                "SELECT user AS col1, host AS col2, Grant_priv AS col3, "
                "plugin AS col4, password_expired AS col5, is_role AS col6 "
                "FROM mysql.user "
                "WHERE user NOT IN ('mysql.infoschema','mysql.session','mysql.sys','root') "
                "ORDER BY user;"
            )
        if 'password_expiry' in sql_dict:
            sql_dict['password_expiry'] = (
                "SELECT user, host, password_expired "
                "FROM mysql.user "
                "WHERE password_expired='Y';"
            )
        if 'user_list' in sql_dict:
            sql_dict['user_list'] = (
                "SELECT user, host, "
                "authentication_string IS NOT NULL AS has_password, "
                "password_expired, is_role AS is_role_account, plugin "
                "FROM mysql.user "
                "WHERE user != '' "
                "ORDER BY user, host;"
            )

        # InnoDB 锁等待（MariaDB < 10.6 使用 information_schema）
        if 'innodb_lock_chain' in sql_dict:
            sql_dict['innodb_lock_chain'] = (
                "SELECT r.trx_id AS waiting_trx_id, r.trx_mysql_thread_id AS waiting_thread, "
                "LEFT(COALESCE(r.trx_query, ''), 200) AS waiting_query, "
                "r.trx_state AS waiting_state, "
                "b.trx_id AS blocking_trx_id, b.trx_mysql_thread_id AS blocking_thread, "
                "LEFT(COALESCE(b.trx_query, ''), 200) AS blocking_query "
                "FROM information_schema.INNODB_TRX r "
                "JOIN information_schema.INNODB_LOCK_WAITS w ON r.trx_id = w.requesting_trx_id "
                "JOIN information_schema.INNODB_TRX b ON w.blocking_trx_id = b.trx_id "
                "ORDER BY r.trx_started;"
            )
        if 'lock_waits' in sql_dict:
            sql_dict['lock_waits'] = (
                "SELECT * FROM information_schema.INNODB_LOCK_WAITS LIMIT 20;"
            )
        if 'lock_summary' in sql_dict:
            # INNODB_LOCK_WAITS 仅有 requesting/blocking trx_id 与 lock_id，
            # 没有 lock_mode/lock_type 列（任何 MariaDB 版本都没有），
            # 改为按「被阻塞事务」汇总锁等待数量，跨版本稳定。
            sql_dict['lock_summary'] = (
                "SELECT blocking_trx_id AS blocked_by, COUNT(*) AS wait_count "
                "FROM information_schema.INNODB_LOCK_WAITS "
                "GROUP BY blocking_trx_id "
                "ORDER BY wait_count DESC;"
            )

        # 用户权限审计 - 角色关系（MariaDB 用 roles_mapping）
        if 'role_edges' in sql_dict:
            sql_dict['role_edges'] = (
                "SELECT * FROM mysql.roles_mapping ORDER BY User;"
            )

        # 系统变量（MariaDB < 10.10 无 performance_schema.global_variables）
        if 'key_vars' in sql_dict:
            sql_dict['key_vars'] = (
                "SHOW GLOBAL VARIABLES WHERE Variable_name IN ("
                "'innodb_buffer_pool_size','innodb_log_file_size','max_connections',"
                "'tmp_table_size','max_heap_table_size','thread_cache_size',"
                "'table_open_cache','open_files_limit','innodb_flush_log_at_trx_commit',"
                "'sync_binlog','log_bin','slow_query_log','long_query_time')"
            )

        # InnoDB 表空间：表名随 MariaDB 版本变化，且 information_schema.TABLES 目录
        # 对 INNODB_* 表登记不可靠，故用「能否真正 SELECT」做运行时探测：
        #   < 10.6 : information_schema.INNODB_SYS_DATAFILES / INNODB_SYS_TABLESPACES
        #   >= 10.6: information_schema.INNODB_DATAFILES      / INNODB_TABLESPACES（去掉 _SYS_）
        # 两者皆不可用时 df_tbl/ts_tbl 为 None，降级为空结果 SQL，章节不再 WARN。
        df_tbl = self._resolve_innodb_table('INNODB_DATAFILES', 'INNODB_SYS_DATAFILES')
        ts_tbl = self._resolve_innodb_table('INNODB_TABLESPACES', 'INNODB_SYS_TABLESPACES')
        if 'innodb_datafiles' in sql_dict:
            sql_dict['innodb_datafiles'] = (
                "SELECT * FROM information_schema.%s;" % df_tbl
                if df_tbl else
                "SELECT 'INNODB_DATAFILES_UNAVAILABLE' AS note WHERE 1=0;"
            )
        if 'innodb_tablespaces' in sql_dict:
            sql_dict['innodb_tablespaces'] = (
                "SELECT * FROM information_schema.%s;" % ts_tbl
                if ts_tbl else
                "SELECT 'INNODB_TABLESPACES_UNAVAILABLE' AS note WHERE 1=0;"
            )

        # Binlog 状态（binlog 未开启时 SHOW BINARY LOGS 报 1381，改用探针避免报错）
        if 'binlog_status' in sql_dict:
            sql_dict['binlog_status'] = (
                "SELECT @@log_bin AS log_bin, @@log_bin_basename AS binlog_basename;"
            )

        # 服务器标识：MariaDB 没有 MySQL 的 server_uuid 变量（GTID 概念），
        # 改用 server_id 作为 MariaDB 实例的服务器标识填充该章节。
        if 'server_uuid' in sql_dict:
            sql_dict['server_uuid'] = "SHOW VARIABLES LIKE 'server_id';"

        # 索引使用情况：MySQL 用 performance_schema.table_io_waits_summary_by_index_usage，
        # MariaDB 无此表，改用 information_schema.INDEX_STATISTICS（需 userstat 插件）。
        # 未启用 userstat 时该表不存在，探测不到则优雅置空（不报错、不 WARN）。
        _userstat = self._resolve_userstat()
        if 'index_stats' in sql_dict:
            sql_dict['index_stats'] = (
                "SELECT table_schema, table_name, index_name, rows_read AS rows_accessed "
                "FROM information_schema.INDEX_STATISTICS "
                "ORDER BY rows_read DESC LIMIT 20;"
                if _userstat else
                "SELECT 'USERSTAT_DISABLED' AS note WHERE 1=0;"
            )
        if 'unused_indexes' in sql_dict:
            sql_dict['unused_indexes'] = (
                "SELECT table_schema, table_name, index_name, rows_read "
                "FROM information_schema.INDEX_STATISTICS "
                "WHERE rows_read = 0 AND index_name IS NOT NULL "
                "ORDER BY table_schema, table_name;"
                if _userstat else
                "SELECT 'USERSTAT_DISABLED' AS note WHERE 1=0;"
            )

        # 复制通道状态：MySQL 8.0 用 SHOW REPLICA STATUS，MariaDB 用 SHOW SLAVE STATUS
        if 'repl_channels' in sql_dict:
            sql_dict['repl_channels'] = "SHOW SLAVE STATUS;"

        # 7.2/7.3/7.4 复制状态：MariaDB 用 SHOW SLAVE STATUS（MySQL 8.0 才用 REPLICA）。
        # seed 里曾误带客户端指令 \G，已去除；此处再显式覆盖一次，确保 mariadb 不依赖重新 seed。
        if 'replication_lag' in sql_dict:
            sql_dict['replication_lag'] = "SHOW SLAVE STATUS;"
        if 'slave_io_running' in sql_dict:
            sql_dict['slave_io_running'] = "SHOW SLAVE STATUS;"
        if 'slave_status' in sql_dict:
            sql_dict['slave_status'] = "SHOW SLAVE STATUS;"
        # 7.1 主库 Binlog 位置：MariaDB 10.5.2+ 起 SHOW MASTER STATUS 改名 SHOW BINLOG STATUS（旧名为别名）。
        # binlog 未启用时该命令本就返回空；此时给出友好提示而非「数据缺失」，避免误判为 bug。
        if 'master_status' in sql_dict:
            if self._resolve_binlog_enabled():
                sql_dict['master_status'] = "SHOW BINLOG STATUS;"
            else:
                sql_dict['master_status'] = (
                    "SELECT '二进制日志未启用（log_bin=OFF），无 Binlog 位点可展示' AS 说明;"
                )
        # 9.1 缓冲池实例数：MariaDB 10.5.1+ 移除多缓冲池实例，innodb_buffer_pool_instances 变量不再暴露。
        # 改用 information_schema.INNODB_BUFFER_POOL_STATS 取真实单缓冲池统计（POOL_ID 恒为 0）。
        _bps = self._resolve_buffer_pool_stats()
        if 'buffer_pool_instances' in sql_dict:
            sql_dict['buffer_pool_instances'] = (
                "SELECT POOL_ID AS pool_id, POOL_SIZE AS pool_size_pages, "
                "FREE_BUFFERS AS free_buffers, DATABASE_PAGES AS database_pages, "
                "OLD_DATABASE_PAGES AS old_database_pages "
                "FROM information_schema.INNODB_BUFFER_POOL_STATS;"
                if _bps else
                "SELECT 'NO_MULTI_INSTANCE' AS note WHERE 1=0;"
            )

        # 单库巡检：在 MariaDB 原有覆写之后，对结果再做单库过滤。
        from inspection_engine import scope_mysql_schema
        scope_mysql_schema(sql_dict, self.database)

    def _resolve_innodb_table(self, modern, legacy):
        """解析 MariaDB 的 information_schema InnoDB 表名。

        MariaDB 版本间表名不稳定（10.6+ 去掉 _SYS_ 前缀，部分版本两者皆不登记
        于 information_schema.TABLES 目录），故以「能否真正 SELECT」为准做探测，
        比查目录更可靠。

        :param modern: 10.6+ 表名，如 'INNODB_DATAFILES'
        :param legacy: 旧版本表名，如 'INNODB_SYS_DATAFILES'
        :return: 实际可查询的表名；两者皆不可用时返回 None（调用方降级为空结果）
        """
        conn = getattr(self, 'conn', None)
        if conn is None:
            # 无连接无法探测，保守兜底 modern（巡检执行期会再真实试查）
            return modern
        for tbl in (modern, legacy):
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM information_schema.%s LIMIT 0" % tbl)
                cur.fetchall()
                cur.close()
                return tbl
            except Exception:
                try:
                    cur.close()
                except Exception:
                    pass
        return None

    def _resolve_userstat(self):
        """探测 MariaDB 的 userstat 插件是否启用。

        MariaDB 的索引使用统计在 information_schema.INDEX_STATISTICS，
        需先启用 userstat 插件（INSTALL SONAME 'userstat'），否则该表不存在。
        用「能否真正 SELECT」探测，比假设版本更可靠。

        :return: True 表示 INDEX_STATISTICS 可查；False 表示未启用 userstat
        """
        conn = getattr(self, 'conn', None)
        if conn is None:
            return False
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM information_schema.INDEX_STATISTICS LIMIT 0")
            cur.fetchall()
            cur.close()
            return True
        except Exception:
            try:
                cur.close()
            except Exception:
                pass
            return False

    def _resolve_buffer_pool_stats(self):
        """探测 MariaDB 的 information_schema.INNODB_BUFFER_POOL_STATS 是否可查。

        MariaDB 10.5+ 为单缓冲池（多实例已移除），该表存在且 POOL_ID=0。
        用真实 SELECT 探测，连接为 None 时返回 False。
        """
        conn = getattr(self, 'conn', None)
        if conn is None:
            return False
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM information_schema.INNODB_BUFFER_POOL_STATS LIMIT 0")
            cur.fetchall()
            cur.close()
            return True
        except Exception:
            try:
                cur.close()
            except Exception:
                pass
            return False

    def _resolve_binlog_enabled(self):
        """探测 MariaDB 是否启用了二进制日志（log_bin）。

        7.1 主库 Binlog 位置章节在 binlog 未启用时本就无数据。
        此处用真实 SELECT @@log_bin 探测；连接为 None 时保守返回 True
        （让 _customize_queries 走 SHOW BINLOG STATUS，空结果按现有逻辑呈现）。
        """
        conn = getattr(self, 'conn', None)
        if conn is None:
            return True
        try:
            cur = conn.cursor()
            cur.execute("SELECT @@log_bin")
            row = cur.fetchone()
            cur.close()
            if row is None:
                return True
            val = row[0]
            if isinstance(val, (bytes, bytearray)):
                val = val.decode()
            # @@log_bin 返回整数 1/0（布尔变量经 @@ 读取即 1/0；'ON'/'OFF' 仅出现在
            # SHOW VARIABLES 的展示中）。同时兼容字符串 'ON'/'OFF'/'TRUE'/'FALSE' 表示，
            # 避免一律比对 'ON' 导致 query 成功时永远返回 False。
            s = str(val).strip().upper()
            return s in ('ON', '1', 'TRUE') or (s.isdigit() and int(s) != 0)
        except Exception:
            try:
                cur.close()
            except Exception:
                pass
            return True


# ── 保留原有 API 兼容性（供 web_ui.py / run_inspection.py 旧代码调用）────────
def getData(ip, port, user, password, ssh_info=None, template_id=None, database=None):
    """
    原有 API - 创建 MariaDBInspector 实例

    注意：这个函数在重构过程中保留，用于兼容 run_inspection.py 中的旧代码。
    新代码应该直接使用 MariaDBInspector 类。
    """
    inspector = MariaDBInspector(ip, port, user, password, database, ssh_info, template_id)
    ok, ver = inspector.connect()
    if not ok:
        return None
    # 为了兼容旧代码，返回一个对象，其中包含 conn_db2 属性
    class CompatWrapper:
        def __init__(self, inspector):
            self.inspector = inspector
            self.conn_db2 = inspector.conn
        def checkdb(self, sqlfile=''):
            self.inspector.collect_data()
            return self.inspector.context
        def generate_report(self, output_file, inspector_name="Jack"):
            """委托给 inspector.generate_report()"""
            return self.inspector.generate_report(output_file, inspector_name)
    return CompatWrapper(inspector)


def create_word_template(inspector_name):
    """原有 API - 创建 Word 模板"""
    import tempfile
    from docx import Document
    doc = Document()
    fd, path = tempfile.mkstemp(suffix='.docx')
    os.close(fd)
    doc.save(path)
    return path


def saveDoc(context, ofile, ifile, inspector_name):
    """原有 API - 保存 Word 报告（空壳，供极端旧版兼容）"""
    class CompatWrapper:
        def __init__(self, context, ofile):
            self.context = context
            self.ofile = ofile
        def contextsave(self):
            from docx import Document
            doc = Document()
            doc.save(self.ofile)
            return True
    return CompatWrapper(context, ofile)

def main():
    """MariaDB 巡检 CLI 入口"""
    import getpass

    print(u"MariaDB 数据库巡检")
    print(u"=" * 50)

    host = input(u"主机地址 [localhost]: ") or "localhost"
    port = int(input(u"端口 [3306]: ") or 3306)
    user = input(u"用户名: ")
    if not user:
        print(u"用户名不能为空"); return
    password = getpass.getpass(u"密码: ")
    database = input(u"数据库名 [mysql]: ") or "mysql"

    inspector = MariaDBInspector(host, port, user, password, database)
    ok, ver = inspector.connect()
    if not ok:
        print(u"连接失败: {}".format(ver)); return
    print(u"连接成功: {}".format(ver))

    inspector.collect_data()
    name = "{}_{}".format(host, port)
    output = "MariaDB_Inspection_Report_{}.docx".format(name)
    inspector.generate_report(output, name)
    print(u"报告已生成: {}".format(output))


if __name__ == '__main__':
    main()
