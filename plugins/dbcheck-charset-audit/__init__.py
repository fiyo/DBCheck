"""
DBCheck 官方插件 —— 数据库字符集与排序规则检查

检测各数据库中非标准字符集/排序规则的使用情况：
- MySQL/TiDB: 检查 information_schema.tables 的 table_collation
- PostgreSQL/IvorySQL/KingbaseES: 检查数据库编码和列级排序规则
- Oracle/DM8/YashanDB: 检查 NLS 字符集参数
- SQL Server: 检查数据库和列级排序规则
- GBase 8s: 检查数据库语言环境
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plugin_core import InspectionPlugin, InspectionQuery, RiskItem, register


class CharsetChecker(InspectionPlugin):
    id = "dbcheck-charset-audit"
    name = "字符集与排序规则审计"
    version = "1.0.0"
    db_types = [
        "mysql", "tidb",
        "postgresql", "ivorysql", "kingbase",
        "oracle", "dm8", "yashandb",
        "sqlserver",
        "gbase",
    ]
    author = "DBCheck Team"
    description = "检测各数据库非标准字符集和排序规则，评估跨平台兼容性和多语言支持风险"

    def get_queries(self):
        return [
            # ── MySQL / TiDB ──
            InspectionQuery(
                key="charset_non_utf_tables_mysql",
                sql="""
                    SELECT table_schema, table_name, table_collation
                    FROM information_schema.tables
                    WHERE table_collation IS NOT NULL
                      AND table_collation NOT LIKE 'utf8%'
                      AND table_collation NOT LIKE 'utf8mb4%'
                      AND table_collation NOT LIKE 'latin1%'
                      AND table_schema NOT IN ('mysql', 'sys', 'performance_schema', 'information_schema')
                    ORDER BY table_schema, table_name
                    LIMIT 200
                """,
                desc_zh="非UTF8字符集的表（MySQL/TiDB）",
                desc_en="Tables with non-UTF8 collation (MySQL/TiDB)",
                db_type="mysql"
            ),
            InspectionQuery(
                key="charset_db_default_mysql",
                sql="""
                    SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME
                    FROM information_schema.SCHEMATA
                    WHERE SCHEMA_NAME NOT IN ('mysql', 'sys', 'performance_schema', 'information_schema')
                      AND DEFAULT_CHARACTER_SET_NAME NOT IN ('utf8', 'utf8mb4', 'utf8mb3')
                """,
                desc_zh="非UTF8默认字符集的数据库（MySQL/TiDB）",
                desc_en="Databases with non-UTF8 default charset (MySQL/TiDB)",
                db_type="mysql"
            ),

            # ── PostgreSQL / IvorySQL / KingbaseES ──
            InspectionQuery(
                key="charset_pg_encoding",
                sql="""
                    SELECT datname, pg_encoding_to_char(encoding) AS encoding,
                           datcollate, datctype
                    FROM pg_catalog.pg_database
                    WHERE encoding != pg_char_to_encoding('UTF8')
                    ORDER BY datname
                """,
                desc_zh="非UTF8编码的数据库（PG/Kingbase/IvorySQL）",
                desc_en="Databases with non-UTF8 encoding (PG/Kingbase/IvorySQL)",
                db_type="postgresql"
            ),
            InspectionQuery(
                key="charset_pg_collation",
                sql="""
                    SELECT nspname AS schema_name, relname AS table_name,
                           attname AS column_name,
                           pg_catalog.format_type(atttypid, atttypmod) AS data_type,
                           coll.collname AS collation
                    FROM pg_catalog.pg_attribute a
                    JOIN pg_catalog.pg_class c ON a.attrelid = c.oid
                    JOIN pg_catalog.pg_namespace n ON c.relnamespace = n.oid
                    LEFT JOIN pg_catalog.pg_collation coll ON a.attcollation = coll.oid
                    WHERE nspname NOT IN ('pg_catalog', 'information_schema')
                      AND a.attcollation != 0
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                      AND c.relkind = 'r'
                      AND coll.collname NOT IN ('C', 'POSIX', 'default', 'ucs_basic')
                      AND coll.collname NOT LIKE 'en_US%'
                    ORDER BY nspname, relname, a.attnum
                    LIMIT 200
                """,
                desc_zh="非默认排序规则的字符列（PG/Kingbase/IvorySQL）",
                desc_en="Columns with non-default collation (PG/Kingbase/IvorySQL)",
                db_type="postgresql"
            ),

            # ── Oracle / DM8 / YashanDB ──
            InspectionQuery(
                key="charset_oracle_nls",
                sql="""
                    SELECT parameter, value
                    FROM nls_database_parameters
                    WHERE parameter IN ('NLS_CHARACTERSET', 'NLS_NCHAR_CHARACTERSET',
                                        'NLS_LANGUAGE', 'NLS_TERRITORY')
                    ORDER BY parameter
                """,
                desc_zh="NLS字符集参数（Oracle/DM8/YashanDB）",
                desc_en="NLS charset parameters (Oracle/DM8/YashanDB)",
                db_type="oracle"
            ),
            InspectionQuery(
                key="charset_oracle_non_al32utf8",
                sql="""
                    SELECT owner, table_name, column_name, character_set_name
                    FROM all_tab_columns
                    WHERE character_set_name IS NOT NULL
                      AND character_set_name NOT IN ('AL32UTF8', 'AL16UTF16', 'UTF8')
                    ORDER BY owner, table_name
                    FETCH FIRST 200 ROWS ONLY
                """,
                desc_zh="非UTF8字符集的列（Oracle/DM8/YashanDB）",
                desc_en="Columns with non-UTF8 charset (Oracle/DM8/YashanDB)",
                db_type="oracle"
            ),

            # ── SQL Server ──
            InspectionQuery(
                key="charset_sqlserver_db",
                sql="""
                    SELECT name, collation_name
                    FROM sys.databases
                    WHERE collation_name NOT LIKE 'Chinese_PRC_CI_AS%'
                      AND collation_name NOT LIKE 'SQL_Latin1_General_CP1_CI_AS%'
                    ORDER BY name
                """,
                desc_zh="非标准排序规则的数据库（SQL Server）",
                desc_en="Databases with non-standard collation (SQL Server)",
                db_type="sqlserver"
            ),
            InspectionQuery(
                key="charset_sqlserver_columns",
                sql="""
                    SELECT OBJECT_SCHEMA_NAME(c.object_id) AS schema_name,
                           OBJECT_NAME(c.object_id) AS table_name,
                           c.name AS column_name,
                           c.collation_name
                    FROM sys.columns c
                    JOIN sys.tables t ON c.object_id = t.object_id
                    WHERE c.collation_name IS NOT NULL
                      AND c.collation_name NOT LIKE 'Chinese_PRC_CI_AS%'
                      AND c.collation_name NOT LIKE 'SQL_Latin1_General_CP1_CI_AS%'
                    ORDER BY schema_name, table_name
                    OFFSET 0 ROWS FETCH NEXT 200 ROWS ONLY
                """,
                desc_zh="非标准排序规则的列（SQL Server）",
                desc_en="Columns with non-standard collation (SQL Server)",
                db_type="sqlserver"
            ),

            # ── GBase 8s ──
            InspectionQuery(
                key="charset_gbase_locale",
                sql="SELECT * FROM sysmaster:sysdbslocale",
                desc_zh="数据库语言环境（GBase 8s）",
                desc_en="Database locale info (GBase 8s)",
                db_type="gbase"
            ),
        ]

    def analyze(self, context):
        risks = []

        # 统一入口：检测所有以 charset_ 开头的 key
        for key, data in context.items():
            if not key.startswith("charset_"):
                continue
            if not isinstance(data, dict):
                continue
            rows = data.get("rows", [])
            headers = data.get("headers", [])
            if not rows:
                continue

            row_count = len(rows)

            # MySQL 表级排序规则
            if key == "charset_non_utf_tables_mysql":
                if row_count > 100:
                    risks.append(RiskItem(
                        level="HIGH",
                        title=f"发现 {row_count} 个表使用非 UTF8 排序规则",
                        description="大量表的排序规则非 utf8/utf8mb4，存在跨平台兼容性风险。"
                                    "使用 GBK/GB2312 等字符集的表在迁移时可能出现乱码。",
                        suggestion="1) 评估这些表是否可以用 utf8mb4 替代；2) 使用 ALTER TABLE ... CONVERT TO CHARACTER SET utf8mb4 转换",
                        category="charset"
                    ))
                elif row_count > 10:
                    risks.append(RiskItem(
                        level="MEDIUM",
                        title=f"发现 {row_count} 个表使用非标准排序规则",
                        suggestion="建议逐个评估，统一为 utf8mb4 字符集",
                        category="charset"
                    ))

            # MySQL 数据库级默认字符集
            elif key == "charset_db_default_mysql":
                dbs = [f'{r[0]}({r[1]})' for r in rows] if rows else []
                if dbs:
                    risks.append(RiskItem(
                        level="HIGH",
                        title=f"{len(dbs)} 个数据库默认字符集非 UTF8",
                        description="涉及的数据库：" + ", ".join(dbs[:10]),
                        suggestion="使用 ALTER DATABASE ... CHARACTER SET utf8mb4 更改默认字符集",
                        category="charset"
                    ))

            # PG 数据库编码
            elif key == "charset_pg_encoding":
                dbs = [f'{r[0]}({r[1]})' for r in rows] if rows else []
                if dbs:
                    risks.append(RiskItem(
                        level="HIGH",
                        title=f"{len(dbs)} 个数据库编码非 UTF8",
                        description="涉及的数据库：" + ", ".join(dbs[:10]),
                        suggestion="建议使用 UTF8 编码重建数据库或迁移数据",
                        category="charset"
                    ))

            # PG 列排序规则
            elif key == "charset_pg_collation":
                if row_count > 50:
                    risks.append(RiskItem(
                        level="MEDIUM",
                        title=f"发现 {row_count} 个字符列使用非标准排序规则",
                        suggestion="检查是否必须使用这些排序规则，统一为 default/C/POSIX",
                        category="charset"
                    ))
                elif row_count > 0:
                    risks.append(RiskItem(
                        level="LOW",
                        title=f"发现 {row_count} 个字符列使用非标准排序规则（数量较少）",
                        category="charset"
                    ))

            # Oracle NLS 参数
            elif key == "charset_oracle_nls":
                params = {r[0]: r[1] for r in rows if len(r) >= 2}
                charset = params.get("NLS_CHARACTERSET", "")
                if charset and charset not in ("AL32UTF8", "UTF8"):
                    risks.append(RiskItem(
                        level="HIGH",
                        title=f"数据库字符集为 {charset}，非 AL32UTF8",
                        description="非 UTF8 字符集的 Oracle 数据库在多语言环境下可能导致数据损坏或乱码",
                        suggestion="建议迁移到 AL32UTF8 字符集数据库",
                        category="charset"
                    ))

            # Oracle 列级字符集
            elif key == "charset_oracle_non_al32utf8":
                if row_count > 0:
                    risks.append(RiskItem(
                        level="MEDIUM",
                        title=f"发现 {row_count} 个列使用非 UTF8 字符集",
                        suggestion="检查这些列的字符集是否必要，考虑转换为 AL32UTF8",
                        category="charset"
                    ))

            # SQL Server 数据库排序规则
            elif key == "charset_sqlserver_db":
                dbs = [f'{r[0]}({r[1]})' for r in rows] if rows else []
                if dbs:
                    risks.append(RiskItem(
                        level="MEDIUM",
                        title=f"{len(dbs)} 个数据库使用非标准排序规则",
                        description="涉及的数据库：" + ", ".join(dbs[:10]),
                        suggestion="对于中文环境，建议使用 Chinese_PRC_CI_AS 排序规则",
                        category="charset"
                    ))

            # SQL Server 列排序规则
            elif key == "charset_sqlserver_columns":
                if row_count > 50:
                    risks.append(RiskItem(
                        level="MEDIUM",
                        title=f"发现 {row_count} 个字符列使用非标准排序规则",
                        suggestion="检查排序规则不一致是否影响查询性能或结果正确性",
                        category="charset"
                    ))

            # GBase 语言环境
            elif key == "charset_gbase_locale":
                risks.append(RiskItem(
                    level="LOW",
                    title=f"GBase 8s 数据库语言环境（{row_count} 条记录）",
                    description="GBase 8s 的字符集通过 DB_LOCALE 和 CLIENT_LOCALE 环境变量控制，请检查配置",
                    suggestion="确保 DB_LOCALE 设置为 zh_CN.utf8 等合适的语言环境",
                    category="charset"
                ))

        return risks


# 注册插件
register(CharsetChecker())
