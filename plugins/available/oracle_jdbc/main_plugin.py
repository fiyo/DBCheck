#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Oracle JDBC 插件 - 通过 JPype + JDBC 驱动连接 Oracle 数据库
支持 Oracle 11g/12c/19c/21c+
"""

import os
import traceback
from typing import Any, Dict, List, Optional

# 注意：不在此文件导入 inspection_engine 或 plugin_core
# 避免循环导入，插件注册通过 plugin_adapter.py 完成


# ── JDBC 连接包装器（兼容 Python DB-API 2.0）─────────────────────────────
class JdbcCursorWrapper:
    """包装 JDBC Statement/ResultSet，提供类似 Python DB-API 的 cursor 接口"""
    
    def __init__(self, connection):
        self.conn = connection
        self.stmt = connection.createStatement()
        self.rs = None
        self.description = None
        self._rowcount = -1
    
    def execute(self, sql):
        """执行 SQL（自动判断查询/更新）"""
        sql_upper = sql.strip().upper()
        if sql_upper.startswith('SELECT') or sql_upper.startswith('WITH'):
            self.rs = self.stmt.executeQuery(sql)
            # 获取列信息
            meta = self.rs.getMetaData()
            col_count = meta.getColumnCount()
            self.description = tuple(
                (meta.getColumnName(i+1), meta.getColumnTypeName(i+1), None, None, None, None, None)
                for i in range(col_count)
            )
        else:
            self._rowcount = self.stmt.executeUpdate(sql)
    
    def fetchall(self):
        """获取所有行"""
        if not self.rs:
            return []
        rows = []
        meta = self.rs.getMetaData()
        col_count = meta.getColumnCount()
        while self.rs.next():
            row = tuple(self._convert_java_obj(self.rs.getObject(i+1)) for i in range(col_count))
            rows.append(row)
        return rows
    
    def fetchone(self):
        """获取一行"""
        if not self.rs:
            return None
        if self.rs.next():
            meta = self.rs.getMetaData()
            col_count = meta.getColumnCount()
            return tuple(self._convert_java_obj(self.rs.getObject(i+1)) for i in range(col_count))
        return None
    
    def _convert_java_obj(self, obj):
        """将 Java 对象转换为 Python 对象"""
        if obj is None:
            return None
        # 尝试转换为 Python 类型
        try:
            # 数字类型
            if hasattr(obj, 'intValue'):
                return obj.intValue()
            if hasattr(obj, 'longValue'):
                return obj.longValue()
            if hasattr(obj, 'doubleValue'):
                return obj.doubleValue()
            # 字符串类型
            return str(obj)
        except:
            return str(obj)
    
    def close(self):
        """关闭游标"""
        if self.rs:
            self.rs.close()
        if self.stmt:
            self.stmt.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


class JdbcConnectionWrapper:
    """包装 JDBC Connection，提供类似 Python DB-API 的 connection 接口"""
    
    def __init__(self, jdbc_conn):
        self.jdbc_conn = jdbc_conn
    
    def cursor(self):
        """返回包装后的 cursor 对象"""
        return JdbcCursorWrapper(self.jdbc_conn)
    
    def close(self):
        """关闭连接"""
        self.jdbc_conn.close()
    
    def commit(self):
        """提交事务"""
        self.jdbc_conn.commit()
    
    def rollback(self):
        """回滚事务"""
        self.jdbc_conn.rollback()


# ── Oracle JDBC 巡检器 ─────────────────────────────────────────────────────────
from inspection_engine import BaseInspectionEngine

class OracleJdbcInspector(BaseInspectionEngine):
    """Oracle JDBC 巡检器"""

    def __init__(self, host, port, user, password, service_name='ORCL',
                 ssh_info=None, template_id=None, sysdba=False):
        """
        初始化 Oracle JDBC 巡检器

        :param host: Oracle 服务器 IP 地址
        :param port: Oracle 服务端口（默认 1521）
        :param user: Oracle 登录用户名
        :param password: Oracle 登录密码
        :param service_name: 服务名（默认 ORCL）
        :param ssh_info: SSH 连接信息（可选，用于远程执行命令）
        :param template_id: 巡检模板 ID（可选）
        :param sysdba: 是否以 SYSDBA 身份连接（默认 False）
        """
        # 调用父类初始化（不传递 db_type，它在父类中不存在）
        super().__init__(host, int(port), user, password, database=service_name, ssh_info=ssh_info, template_id=template_id)
        # 必须设置 db_type（父类初始化后默认为 None）
        self.db_type = 'oracle_jdbc'
        self.service_name = service_name
        self.sysdba = sysdba
        self.conn = None
        self.cursor = None
        self.checkdb_result = []
        self.jdbc_driver_path = self._find_jdbc_driver()

    def _find_jdbc_driver(self):
        """查找 JDBC 驱动文件路径"""
        # 优先使用 ojdbc8.jar（支持 Oracle 11g+）
        drivers = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'drivers', 'ojdbc8.jar'),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'drivers', 'ojdbc6.jar'),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'drivers', 'ojdbc8.jar'),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'drivers', 'ojdbc6.jar'),
        ]
        for driver in drivers:
            if os.path.exists(driver):
                return driver
        return None

    def connect(self):
        """
        连接 Oracle 数据库（使用 JPype 启动 JVM，通过 JDBC 连接）
        支持 SYSDBA 身份连接（sys 用户必须使用）

        :return: (ok, msg) 元组
        """
        try:
            import jpype
            import jpype.imports

            print(f"[Oracle JDBC] 连接参数: host={self.host}, port={self.port}, user={self.user}, service_name={self.service_name}, sysdba={self.sysdba}")  # ← 调试

            # 1. 启动 JVM（如果尚未启动）
            if not jpype.isJVMStarted():
                if not self.jdbc_driver_path or not os.path.exists(self.jdbc_driver_path):
                    return False, "JDBC 驱动未找到，请下载 ojdbc8.jar 并放入 drivers/ 目录"
                jpype.startJVM(classpath=[self.jdbc_driver_path])

            # 2. 注册 JDBC 驱动
            from java.sql import DriverManager
            from oracle.jdbc.driver import OracleDriver
            from java.util import Properties
            jpype.JClass('oracle.jdbc.driver.OracleDriver')()

            # 3. 构建连接 URL
            url = f"jdbc:oracle:thin:@//{self.host}:{self.port}/{self.service_name}"

            # 4. 建立连接（支持 SYSDBA 身份）
            if self.sysdba:
                print(f"[Oracle JDBC] 使用 SYSDBA 身份连接...")  # ← 调试
                # sys 用户必须以 SYSDBA 身份登录
                props = Properties()
                props.setProperty("user", self.user)
                props.setProperty("password", self.password)
                props.setProperty("internal_logon", "sysdba")
                jdbc_conn = DriverManager.getConnection(url, props)
                print(f"[Oracle JDBC] 以 SYSDBA 身份连接成功")
            else:
                print(f"[Oracle JDBC] 使用普通身份连接...")  # ← 调试
                jdbc_conn = DriverManager.getConnection(url, self.user, self.password)
            
            # 5. 包装 JDBC 连接（兼容 Python DB-API 2.0）
            self.conn = JdbcConnectionWrapper(jdbc_conn)
            self.raw_jdbc_conn = jdbc_conn  # 保存原始连接（用于关闭）
            self.cursor = self.conn.cursor()  # 创建游标

            # 6. 获取版本信息（使用 Python DB-API 风格）
            self.cursor.execute("SELECT version FROM v$instance")
            version_row = self.cursor.fetchone()
            version = version_row[0] if version_row else 'unknown'

            # 7. 保存版本到 context（供 AI 诊断使用）
            self.context['version'] = [{'VERSION': version}]

            print(f"[Oracle JDBC] 连接成功，版本: {version}")
            return True, version

        except Exception as e:
            error_msg = f"JDBC 连接失败: {e}"
            print(f"[Oracle JDBC] {error_msg}")
            traceback.print_exc()
            return False, error_msg

    def disconnect(self):
        """关闭数据库连接"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
            print("[Oracle JDBC] 连接已关闭")
        except Exception as e:
            print(f"[Oracle JDBC] 关闭连接失败: {e}")

    def get_template_id(self):
        """
        返回 inspection_template 表的 template_id

        :return: template_id: int
        """
        try:
            from inspection_dal import get_templates_by_db_type
            templates = get_templates_by_db_type("oracle_jdbc")
            return templates[0]['id'] if templates else None
        except Exception as e:
            print(f"[Oracle JDBC] 获取模板 ID 失败: {e}")
            return None

    def checkdb(self, sqlfile=''):
        """
        智能分析 - 返回采集的数据 Context，并添加 AI 诊断结果

        :param sqlfile: SQL 模板文件路径（保留参数兼容性）
        :return: Context 字典
        """
        print("\n[Oracle JDBC] 开始智能分析...")

        # 如果数据还没有采集，先采集数据
        if not self.context.get('database_info'):
            ok, msg = self.connect()
            if not ok:
                print(f"[Oracle JDBC] 连接失败: {msg}")
                return self.context
            # 调用父类的 collect_data（从 inspection.db 加载 SQL 模板）
            super().collect_data()

        # AI 诊断逻辑（使用模板中的 query_key）
        print("[Oracle JDBC] 执行 AI 诊断...")

        diagnosis_results = []

        # 1. 检查表空间使用率
        tablespace_usage = self.context.get('tablespace_usage', [])
        for ts in tablespace_usage:
            used_pct = ts.get('USED_PCT', ts.get('used_pct', 0))
            if used_pct:
                try:
                    if float(used_pct) > 85:
                        diagnosis_results.append({
                            'check_item': '表空间使用率',
                            'tablespace': ts.get('TABLESPACE_NAME', ts.get('tablespace_name')),
                            'diagnosis': f'警告：表空间 {ts.get("TABLESPACE_NAME", ts.get("tablespace_name"))} 使用率 {used_pct}%，超过85%阈值',
                            'severity': 'HIGH'
                        })
                except (ValueError, TypeError):
                    pass

        # 2. 检查无效对象
        invalid_objects = self.context.get('invalid_objects', [])
        if invalid_objects and len(invalid_objects) > 0:
            diagnosis_results.append({
                'check_item': '无效对象',
                'diagnosis': f'发现 {len(invalid_objects)} 个无效对象，建议重新编译',
                'severity': 'MEDIUM'
            })

        # 3. 检查锁等待
        lock_waiters = self.context.get('lock_wait', [])
        if lock_waiters and len(lock_waiters) > 0:
            diagnosis_results.append({
                'check_item': '锁等待',
                'diagnosis': f'发现 {len(lock_waiters)} 个锁等待事件，建议检查阻塞会话',
                'severity': 'HIGH'
            })

        # 保存诊断结果到 context
        self.context['ai_diagnosis'] = diagnosis_results
        self.checkdb_result = diagnosis_results

        print(f"[Oracle JDBC] AI 诊断完成，发现 {len(diagnosis_results)} 个问题")

        return self.context


# ── 测试连接函数（供 plugin_loader.py 使用）─────────────────────────────
def test_connection(host, port, user, password, service_name='ORCL', sysdba=False):
    """
    测试 Oracle JDBC 连接

    :param host: Oracle 服务器 IP 地址
    :param port: Oracle 服务端口
    :param user: Oracle 登录用户名
    :param password: Oracle 登录密码
    :param service_name: 服务名（可选，默认 ORCL）
    :param sysdba: 是否使用 SYSDBA 模式（可选，默认 False）
    :return: (ok, msg) 元组
    """
    try:
        inspector = OracleJdbcInspector(host, int(port), user, password, service_name,
                                         template_id=None, sysdba=sysdba)
        ok, msg = inspector.connect()
        inspector.disconnect()
        return ok, msg
    except Exception as e:
        return False, str(e)


# ── 数据源获取函数（供 web_ui.py 使用）─────────────────────────────
def getData(ip, port, user, password, database='ORCL', ssh_info=None, template_id=None, service_name=None, sysdba=False):
    """
    获取 Oracle JDBC 数据源

    :param ip: Oracle 服务器 IP 地址
    :param port: Oracle 服务端口
    :param user: Oracle 登录用户名
    :param password: Oracle 登录密码
    :param database: 数据库名（可选，默认值 ORCL）
    :param ssh_info: SSH 连接信息（可选）
    :param template_id: 巡检模板 ID（可选）
    :param service_name: 服务名（可选，优先级高于 database）
    :param sysdba: 是否使用 SYSDBA 模式（可选，默认 False）
    :return: OracleJdbcInspector 对象，失败返回 None
    """
    db_name = service_name or database or 'ORCL'
    inspector = OracleJdbcInspector(ip, int(port), user, password, db_name, ssh_info, template_id, sysdba=sysdba)
    ok, msg = inspector.connect()
    if not ok:
        print(f"[Oracle JDBC] 连接失败: {msg}")
        return None
    inspector.conn_db2 = True
    return inspector


# ── 任务配置函数（供 plugin_loader.py 使用）─────────────────────────────
def get_task_config():
    """
    返回 web_ui.py 所需的任务配置
    使 Oracle JDBC 插件能正确集成到巡检流程中
    """
    return {
        'connect_test': test_connection,
        'connect_test_args': lambda info: [
            info['ip'], info['port'], info['user'], info['password'],
            info.get('service_name', 'ORCL'), bool(info.get('sysdba', False))
        ],
        'getdata_args': lambda info, template_id=None: (
            [info['ip'], info['port'], info['user'], info['password']],
            {'ssh_info': {}, 'template_id': template_id,
             'service_name': info.get('service_name', 'ORCL'),
             'sysdba': bool(info.get('sysdba', False))}
        ),
        'conn_attr': 'conn_db2',
        'filename_key': 'webui.oracle_jdbc_report_filename',
        'history_db_type': 'oracle_jdbc',
        'instance_prefix': 'oracle_jdbc',
        'error_task_name': 'Oracle JDBC',
        'log_start_key': 'webui.log_oracle_jdbc_start',
        'err_module_key': 'webui.err_oracle_jdbc_module',
        'label_default': 'unknown',
        'db_name_default': 'ORCL',
    }

# ── 连接结果解析函数（供 web_ui.py 动态调用）─────────────────────────────
def parse_connection_result(ok: bool, msg: Any) -> Dict[str, Any]:
    """
    解析 Oracle JDBC 连接测试结果，提取版本号。
    此函数会被 web_ui.py 动态调用，实现无侵入式架构。
    
    参数：
        ok: 连接是否成功
        msg: 连接返回的消息（可能是字符串、异常对象等）
    
    返回：
        字典，包含额外信息（如 {'oracle_major_version': 19}）
    """
    result = {}
    if ok and msg:
        import re
        msg_str = str(msg)  # 转换 java.lang.String 为 Python str
        m = re.search(r'(\d{2})\.', msg_str)
        if m:
            result['oracle_major_version'] = int(m.group(1))
    return result


# ── 注册插件（无侵入式架构）─────────────────────────────────────────────
# 直接在文件中定义适配器类，避免模块导入问题
try:
    from plugin_core import InspectionPlugin, register
    
    class OracleJdbcPluginAdapter(InspectionPlugin):
        """Oracle JDBC 插件适配器（实现标准接口）"""
        def __init__(self, parse_func=None):
            self.id = 'oracle_jdbc'
            self.name = 'Oracle JDBC'
            self.version = '1.0.0'
            self.db_types = ['oracle_jdbc']
            self.author = 'DBCheck Team'
            self.description = 'Oracle 数据库 JDBC 巡检插件'
            self._parse_func = parse_func
            super().__init__()
        
        def parse_connection_result(self, ok: bool, msg: Any) -> Dict[str, Any]:
            """实现 InspectionPlugin 接口，解析连接结果"""
            if self._parse_func:
                return self._parse_func(ok, msg)
            return {}
        
        def get_queries(self) -> List[Any]:
            return []
        
        def analyze(self, context: Dict[str, Any]) -> List[Any]:
            return []
        
        def on_install(self, db_path: str = None):
            """插件安装时调用：初始化模板和基线数据（参照 Oracle 11g）"""
            print(f"[Oracle JDBC] 开始初始化数据（参照 Oracle 11g）...")
            try:
                import json
                from inspection_dal import (
                    get_templates_by_db_type,
                    create_template,
                    create_chapter,
                    create_query,
                    create_baseline
                )
                
                # 1. 创建模板（从 template_data.json 读取）
                print(f"[Oracle JDBC] 步骤 1/3: 创建模板...")
                
                template_path = os.path.join(os.path.dirname(__file__), 'template_data.json')
                if not os.path.isfile(template_path):
                    print(f"[Oracle JDBC] 错误：未找到 template_data.json")
                    return
                
                with open(template_path, 'r', encoding='utf-8') as f:
                    template_data = json.load(f)
                
                # 检查是否已存在模板（幂等性）
                existing_templates = get_templates_by_db_type('oracle_jdbc', db_path=db_path)
                
                if existing_templates:
                    # 使用已存在的默认模板
                    template_id = existing_templates[0]['id']
                    print(f"[Oracle JDBC] 模板已存在，使用现有模板: {template_id}")
                else:
                    # 创建新模板
                    template_info = template_data['template']
                    template_id = create_template(
                        db_type=template_info['db_type'],
                        template_name=template_info.get('template_name_zh', ''),  # 注意：参数名是 template_name
                        template_name_en=template_info.get('template_name_en', ''),
                        description=template_info.get('description', ''),
                        is_default=template_info.get('is_default', 1),
                        is_preset=template_info.get('is_preset', 1),
                        db_path=db_path
                    )
                    print(f"[Oracle JDBC] 创建模板: {template_id}")
                
                # 2. 创建章节和查询（从 template_data.json 读取）
                print(f"[Oracle JDBC] 步骤 2/3: 创建章节和查询...")
                
                chapters_data = template_data.get('chapters', [])
                print(f"[Oracle JDBC] 共有 {len(chapters_data)} 个章节")
                
                for chapter_data in chapters_data:
                    chapter_number = chapter_data['chapter_number']
                    chapter_title_zh = chapter_data['chapter_title_zh']
                    
                    # 检查是否已存在章节（幂等性）
                    from inspection_dal import get_db_connection
                    conn = get_db_connection(db_path) if db_path else get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id FROM inspection_chapter 
                        WHERE template_id = ? AND chapter_number = ?
                    """, (template_id, chapter_number))
                    existing_chapter = cursor.fetchone()
                    
                    if existing_chapter:
                        # 使用已存在的章节
                        chapter_id = existing_chapter[0]
                        print(f"[Oracle JDBC]   章节 {chapter_number} 已存在，使用现有章节: {chapter_id}")
                    else:
                        # 创建新章节
                        chapter_id = create_chapter(
                            template_id=template_id,
                            chapter_number=chapter_number,
                            chapter_title_zh=chapter_data.get('chapter_title_zh', ''),
                            chapter_title_en=chapter_data.get('chapter_title_en', ''),
                            description=chapter_data.get('description', ''),
                            db_path=db_path
                        )
                        print(f"[Oracle JDBC]   创建章节 {chapter_number}: {chapter_title_zh} (ID: {chapter_id})")
                    
                    # 创建查询
                    queries_data = chapter_data.get('queries', [])
                    for query_data in queries_data:
                        try:
                            query_id = create_query(
                                chapter_id=chapter_id,
                                query_key=query_data['query_key'],
                                query_sql=query_data['query_sql'],
                                query_description_zh=query_data.get('query_description_zh', ''),
                                query_description_en=query_data.get('query_description_en', ''),
                                db_path=db_path
                            )
                        except Exception as e:
                            # 查询可能已存在（幂等性）
                            if 'UNIQUE constraint' in str(e):
                                pass  # 忽略已存在的查询
                            else:
                                print(f"[Oracle JDBC]     创建查询失败: {query_data['query_key']} - {e}")
                    
                    conn.close()
                
                print(f"[Oracle JDBC] 章节和查询创建完成")
                
                # 3. 创建基线（从 baseline_data.json 读取）
                print(f"[Oracle JDBC] 步骤 3/3: 创建基线...")
                
                baseline_path = os.path.join(os.path.dirname(__file__), 'baseline_data.json')
                if not os.path.isfile(baseline_path):
                    print(f"[Oracle JDBC] 警告：未找到 baseline_data.json")
                else:
                    with open(baseline_path, 'r', encoding='utf-8') as f:
                        baseline_data = json.load(f)
                    
                    print(f"[Oracle JDBC] 共有 {len(baseline_data)} 条基线")
                    
                    for baseline_item in baseline_data:
                        try:
                            baseline_id = create_baseline(
                                db_type=baseline_item['db_type'],
                                param_name=baseline_item['param_name'],
                                query_sql=baseline_item.get('query_sql'),
                                operator=baseline_item.get('operator', '='),
                                expected_value=baseline_item.get('expected_value'),
                                expected_value_min=baseline_item.get('expected_value_min'),
                                expected_value_max=baseline_item.get('expected_value_max'),
                                risk_level=baseline_item.get('risk_level', 'LOW'),
                                description_zh=baseline_item.get('description_zh'),
                                description_en=baseline_item.get('description_en'),
                                db_path=db_path
                            )
                            print(f"[Oracle JDBC]   创建基线: {baseline_item['param_name']} (ID: {baseline_id})")
                        except Exception as e:
                            # 基线可能已存在（幂等性）
                            if 'UNIQUE constraint' in str(e):
                                pass  # 忽略已存在的基线
                            else:
                                print(f"[Oracle JDBC]   创建基线失败: {baseline_item['param_name']} - {e}")
                    
                    print(f"[Oracle JDBC] 基线创建完成")
                
                print(f"[Oracle JDBC] 数据初始化完成（参照 Oracle 11g）")
            except Exception as e:
                print(f"[Oracle JDBC] 数据初始化失败: {e}")
                import traceback
                traceback.print_exc()
        def on_uninstall(self, db_path: str = None):
            """插件卸载时调用：清理模板和基线数据（插件独立，不依赖平台）"""
            print(f"[Oracle JDBC] 开始清理数据...")
            try:
                from inspection_dal import (
                    get_db_connection,
                    get_templates_by_db_type,
                    get_baselines_by_db_type,
                    delete_template,
                    delete_baseline
                )
                
                # 1. 清理模板数据（仅清理 oracle_jdbc 的模板）
                templates = get_templates_by_db_type('oracle_jdbc')
                if templates:
                    print(f"[Oracle JDBC] 清理 {len(templates)} 个模板...")
                    for t in templates:
                        template_id = t['id']
                        try:
                            delete_template(template_id, db_path=db_path)
                            print(f"[Oracle JDBC] 删除模板: {t.get('template_name_zh', template_id)} (ID: {template_id})")
                        except Exception as e:
                            print(f"[Oracle JDBC] 删除模板 {template_id} 失败: {e}")
                    print(f"[Oracle JDBC] 模板清理完成")
                
                # 2. 清理基线数据（仅清理 oracle_jdbc 的基线）
                baselines = get_baselines_by_db_type('oracle_jdbc')
                if baselines:
                    print(f"[Oracle JDBC] 清理 {len(baselines)} 条基线...")
                    for b in baselines:
                        baseline_id = b['id']
                        try:
                            delete_baseline(baseline_id, db_path=db_path)
                            print(f"[Oracle JDBC] 删除基线: {b.get('param_name', baseline_id)} (ID: {baseline_id})")
                        except Exception as e:
                            print(f"[Oracle JDBC] 删除基线 {baseline_id} 失败: {e}")
                    print(f"[Oracle JDBC] 基线清理完成")
                
                print(f"[Oracle JDBC] 数据清理完成（插件独立）")
            except Exception as e:
                print(f"[Oracle JDBC] 数据清理失败: {e}")
                import traceback
                traceback.print_exc()
    
    # 注册插件
    adapter = OracleJdbcPluginAdapter(parse_func=parse_connection_result)
    register(adapter)
    print(f"[Oracle JDBC] 插件注册成功（无侵入式架构）")
    
except Exception as e:
    print(f"[Oracle JDBC] 插件注册失败: {e}")
