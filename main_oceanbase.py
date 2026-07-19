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
OceanBase（MySQL 租户）巡检模块 - 基于 BaseInspectionEngine 重构版本

OceanBase 社区版 MySQL 租户与 MySQL 协议/参数高度兼容，本模块以
**最小改动、最大化复用 MySQL 实现** 为原则：
- 连接逻辑（pymysql，默认端口 2881，database=租户名）复用 MySQL 形态；
- 仅 db_type 标识为 'oceanbase'，以便报告模板、基线、规则引擎按 OceanBase 维度分发；
- 通过 _customize_queries() 注入 OceanBase 专有动态性能视图查询
  （GV$OB_PROCESSLIST / DBA_OB_TENANTS / DBA_OB_UNITS / SHOW PARAMETERS 等）。

使用方式：
    from main_oceanbase import OceanBaseInspector
    inspector = OceanBaseInspector(host, port=2881, user, password, database=tenant_name)
    ok, ver = inspector.connect()
    if ok:
        inspector.collect_data()
        inspector.generate_report(output_file, inspector_name)

# TODO: Oracle 租户扩展点
#   后续企业版 Oracle 租户（db_type='oceanbase_oracle'）应新建
#   OceanBaseOracleInspector(BaseInspectionEngine) 实现一套独立连接/采集逻辑，
#   复用本文件的差异 SQL 注入思路，集中放在 main_oceanbase_oracle.py。
#   规则目录预留 pro/rules/builtin/oceanbase_oracle.yaml（空壳 + 注释）。本期不实现。
"""

import os
from inspection_engine import BaseInspectionEngine


class OceanBaseInspector(BaseInspectionEngine):
    """
    OceanBase（MySQL 租户）巡检器 - 继承 BaseInspectionEngine

    只需实现 connect() 方法（pymysql，端口默认 2881，database=租户名），
    其余逻辑（collect_data / generate_report / 规则 / 基线）全部在基类中完成！
    """

    DEFAULT_PORT = 2881  # OceanBase MySQL 租户默认端口

    def __init__(self, host, port=2881, user='root', password='',
                 database=None, ssh_info=None, template_id=None):
        """
        初始化 OceanBase 巡检器

        :param host: OceanBase 服务器 IP 地址或主机名
        :param port: OceanBase 服务端口（默认 2881，社区版 MySQL 租户）
        :param user: 登录用户名
        :param password: 登录密码（明文传入，基类统一走现有加密/解密机制）
        :param database: 租户名（tenant name），建议业务租户；sys 租户可管理多租户视图
        :param ssh_info: SSH 连接信息字典（可选）
        :param template_id: 巡检模板 ID（可选，指定后使用对应模板的 SQL）
        """
        super().__init__(host, port, user, password, database, ssh_info, template_id)
        self.db_type = 'oceanbase'

    def connect(self):
        """
        连接 OceanBase MySQL 租户（pymysql，端口默认 2881）

        返回:
            (ok, version) - ok 为 True 时 version 是版本号
            （形如 '5.7.25-OceanBase-...' / '4.2.1.0-OceanBase'），否则是错误信息
        """
        import pymysql
        try:
            # OceanBase MySQL 租户默认端口 2881；database 指向租户名（tenant）
            # OceanBase MySQL 租户：库名（database）为空时直接连到租户（不指定 database），
            # 与 test_mysql_connection 行为一致；不应强制 'sys'（sys 是独立管理租户，
            # 非业务租户内的合法库，强制会连接失败）。
            conn_params = dict(
                host=self.host,
                port=int(self.port) or self.DEFAULT_PORT,
                user=self.user,
                password=self.password,
                charset='utf8mb4',
                connect_timeout=10,
                read_timeout=60,
            )
            if self.database:
                conn_params['database'] = self.database
            self.conn = pymysql.connect(**conn_params)
            self.cursor = self.conn.cursor()
            self.cursor.execute("SELECT VERSION()")
            ver = self.cursor.fetchone()[0]
            return True, ver
        except Exception as e:
            return False, str(e)

    def _customize_queries(self, sql_dict):
        """
        覆盖 MySQL 不兼容的 SQL 查询（OceanBase 专有差异注入）。

        OceanBase MySQL 租户与 MySQL 协议兼容，但系统表/动态性能视图（GV$OB_*）
        与 MySQL 不同，且 OceanBase 巡检模板整份复用了 MYSQL_DEFAULT_CHAPTERS 中
        的 21 章 MySQL 专有查询——这些查询在 OceanBase 上大多不兼容，会在 collect_data
        阶段触发 WARNING 并跳过对应章节。本方法集中覆盖这些 key，使章节保留但不再报错。

        覆盖分类：
        A. ob_* 核心章节：加 `oceanbase.` schema 前缀（修 No database selected）
        B. 用户/密码章节：去掉 OceanBase 没有的 `password_lifetime` 列
        C. key_vars：改用 SHOW GLOBAL VARIABLES（OB 无 performance_schema.global_variables）
        D. 锁章节：用 oceanbase.GV$OB_PROCESSLIST 检测阻塞/等待会话

        E 类（复制/perf_schema/innodb 系统表/事件）的 12 个 key 已从
        OCEANBASE_DEFAULT_CHAPTERS 章节定义中剔除，不再需要占位覆盖——
        sql_dict 中不会出现这些 key，报告渲染逻辑会自动跳过空查询章节。

        这些差异 SQL 注入后，collect_data 会把结果写入 context，
        供 oceanbase.yaml 的专有规则（primary_zone 单点 / locality 未配置 /
        资源单元不均衡 / 合并转储参数异常 / 副本数不足）消费。
        """
        # 1. 活跃会话：覆盖 MySQL 的 processlist 查询
        #    注：当前 OB 未对 GV$OB_PROCESSLIST 报 No database selected（无需 oceanbase. 前缀），
        #    且既有测试 test_customize_queries 断言其精确等于该串，故此处保持与 MySQL 模板一致的写法。
        if 'processlist' in sql_dict:
            sql_dict['processlist'] = "SELECT * FROM GV$OB_PROCESSLIST"

        # ── A. ob_* 核心章节：加 oceanbase. schema 前缀，修 No database selected ──
        # 此前连 OB 时未带 database 参数，导致 DBA_OB_*/GV$OB_* 视图报 1046 No database selected。
        # 2. OceanBase 专有：租户拓扑（primary_zone / locality）
        #    DBA_OB_TENANTS 在 sys 租户可见全部租户；业务租户仅见自身。
        sql_dict['ob_tenant_info'] = (
            "SELECT TENANT_ID, TENANT_NAME, PRIMARY_ZONE, LOCALITY "
            "FROM oceanbase.DBA_OB_TENANTS"
        )

        # 3. OceanBase 专有：资源单元分布（按 Zone / 资源池）
        #    注意：oceanbase.DBA_OB_UNITS 仅 sys 租户可见，普通业务租户连接会报
        #    'Table oceanbase.dba_ob_units doesn't exist'（用户正是普通租户）。
        #    降级为安全空查询占位（与 E 类一致），避免报错并让章节保留（仅无数据）。
        #    若用户后续用 sys 租户连接，可在该方向再迭代补充真实列。
        sql_dict['ob_unit_info'] = (
            "SELECT 'N/A' AS note FROM DUAL WHERE 1=0"
        )

        # 4. OceanBase 专有：合并 / 转储参数（必须用 OB 语法的 SHOW PARAMETERS，
        #    SHOW VARIABLES 查不到 memstore_limit_percentage 等 OB 专有参数）
        sql_dict['ob_merge_params'] = (
            "SHOW PARAMETERS LIKE 'freeze_trigger_percentage'"
        )
        sql_dict['ob_merge_params_major'] = (
            "SHOW PARAMETERS LIKE 'major_compact_trigger'"
        )
        sql_dict['ob_memstore_params'] = (
            "SHOW PARAMETERS LIKE 'memstore_limit_percentage'"
        )
        sql_dict['ob_resource_params'] = (
            "SHOW PARAMETERS LIKE 'resource_hard_limit'"
        )

        # 5. OceanBase 专有：副本位置（按 库/表/Zone/角色 统计分区副本分布）
        #    注意：DBA_OB_TABLEGROUPS 没有 REPLICA_COUNT 列（旧 SQL 会报
        #    Unknown column 'REPLICA_COUNT'）。真实副本/位置信息在
        #    oceanbase.DBA_OB_TABLE_LOCATIONS（业务租户可见），展示表分区副本位置。
        sql_dict['ob_replica_info'] = (
            "SELECT DATABASE_NAME, TABLE_NAME, ZONE, ROLE, REPLICA_TYPE, "
            "COUNT(*) AS tablet_cnt "
            "FROM oceanbase.DBA_OB_TABLE_LOCATIONS "
            "GROUP BY DATABASE_NAME, TABLE_NAME, ZONE, ROLE, REPLICA_TYPE "
            "LIMIT 200"
        )

        # 6. OceanBase 专有：Server 节点 / Zone 分布（监控多维下钻用）
        #    注意：oceanbase.GV$OB_SERVERS 仅 sys 租户可见，普通业务租户连接会报
        #    'Table oceanbase.gv$ob_servers doesn't exist'（用户正是普通租户）。
        #    降级为安全空查询占位（与 E 类一致），避免报错并让章节保留（仅无数据）。
        #    若用户后续用 sys 租户连接，可在该方向再迭代补充真实列。
        sql_dict['ob_server_stat'] = (
            "SELECT 'N/A' AS note FROM DUAL WHERE 1=0"
        )

        # ── B. 用户/密码章节：去掉 OceanBase mysql.user 没有的 password_lifetime 列 ──
        # password_expiry：OB 的 mysql.user 无 password_lifetime 列，去掉该列与 OR 条件
        if 'password_expiry' in sql_dict:
            sql_dict['password_expiry'] = (
                "SELECT user, host, password_expired "
                "FROM mysql.user WHERE password_expired='Y'"
            )

        # user_list：OB 的 mysql.user 无 password_lifetime、authentication_string 列，
        # 去掉这些依赖，仅保留通用可用列。
        if 'user_list' in sql_dict:
            sql_dict['user_list'] = (
                "SELECT user, host, account_locked, plugin "
                "FROM mysql.user WHERE user != '' ORDER BY user, host"
            )

        # ── C. key_vars：改用 SHOW GLOBAL VARIABLES（OB 无 performance_schema.global_variables）──
        # 注：OceanBase SHOW GLOBAL VARIABLES 的过滤列名为 variable_name（小写）。
        # 若真实实例报未知列，请将 WHERE 子句的 variable_name 改为大写 Variable_name。
        if 'key_vars' in sql_dict:
            sql_dict['key_vars'] = (
                "SHOW GLOBAL VARIABLES WHERE variable_name IN "
                "('innodb_buffer_pool_size','innodb_log_file_size','max_connections',"
                "'query_cache_size','tmp_table_size','max_heap_table_size','thread_cache_size',"
                "'table_open_cache','open_files_limit','innodb_flush_log_at_trx_commit',"
                "'sync_binlog','log_bin','slow_query_log','long_query_time')"
            )

        # ── D. 锁章节：OceanBase 无 performance_schema 锁表，改用 oceanbase.GV$OB_PROCESSLIST ──
        # OceanBase 通过 GV$OB_PROCESSLIST 的 STATE（如 'WAITING'）/TIME 等列识别阻塞/等待会话；
        # 该视图【无 WAIT_EVENT 列】（WAIT_EVENT 属于 GV$OB_SQL_AUDIT 的等待事件字段），
        # 仅使用其真实存在的列。依据 OceanBase 官方文档（CE 4.x GV$OB_PROCESSLIST 字段说明），
        # 租户列名为 TENANT（varchar(128)，访问的租户名称），并不存在 TENANT_NAME 列——
        # 真实实例已报 1054 Unknown column 'TENANT_NAME'，故此处使用 TENANT。
        # 其余用到的列 SVR_IP/USER/ID/STATE/TIME/SQL_ID 经官方文档逐一核对均真实存在。
        if 'innodb_lock_chain' in sql_dict:
            sql_dict['innodb_lock_chain'] = (
                "SELECT SVR_IP, TENANT, USER, ID, STATE, "
                "LEFT(COALESCE(SQL_ID,''),40) AS sql_id, TIME AS wait_seconds "
                "FROM oceanbase.GV$OB_PROCESSLIST WHERE STATE='WAITING' LIMIT 50"
            )

        if 'lock_waits' in sql_dict:
            sql_dict['lock_waits'] = (
                "SELECT SVR_IP, TENANT, ID, USER, STATE, TIME "
                "FROM oceanbase.GV$OB_PROCESSLIST WHERE STATE='WAITING' LIMIT 20"
            )

        if 'lock_summary' in sql_dict:
            sql_dict['lock_summary'] = (
                "SELECT STATE, COUNT(*) AS cnt "
                "FROM oceanbase.GV$OB_PROCESSLIST GROUP BY STATE"
            )

        return sql_dict


# ── 保留原有 API 兼容性（供 web_ui.py / run_inspection.py 旧代码调用）────────
def getData(ip, port=2881, user='root', password='', database=None,
           ssh_info=None, template_id=None):
    """
    原有 API - 创建 OceanBaseInspector 实例

    注意：这个函数在重构过程中保留，用于兼容 web_ui.py / run_inspection.py 中的旧代码。
    新代码应该直接使用 OceanBaseInspector 类。
    """
    inspector = OceanBaseInspector(ip, port, user, password, database, ssh_info, template_id)
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
    """原有 API - 创建 Word 模板（OceanBase 复用 mysql 模板形态）"""
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
    """OceanBase 巡检 CLI 入口"""
    import getpass

    print(u"OceanBase（MySQL 租户）数据库巡检")
    print(u"=" * 50)

    host = input(u"主机地址 [localhost]: ") or "localhost"
    port = int(input(u"端口 [2881]: ") or 2881)
    user = input(u"用户名: ")
    if not user:
        print(u"用户名不能为空")
        return
    password = getpass.getpass(u"密码: ")
    database = input(u"租户名 [sys]: ") or "sys"

    inspector = OceanBaseInspector(host, port, user, password, database)
    ok, ver = inspector.connect()
    if not ok:
        print(u"连接失败: {}".format(ver))
        return
    print(u"连接成功: {}".format(ver))

    inspector.collect_data()
    name = "{}_{}".format(host, port)
    output = "OceanBase_Inspection_Report_{}.docx".format(name)
    inspector.generate_report(output, name)
    print(u"报告已生成: {}".format(output))


if __name__ == '__main__':
    main()
