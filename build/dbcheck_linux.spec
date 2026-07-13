# -*- mode: python ; coding: utf-8 -*-
# DBCheck Linux 打包配置

import os

block_cipher = None

# Build script cd's to project root before calling pyinstaller.
# So CWD == project root directory.
PROJECT_DIR = os.getcwd()

# Directories to include as data
# NOTE: 'data' is a runtime directory (SQLite DBs), not packaged.
data_dirs = [
    'web_templates', 'i18n', 'templates',
    'rag', 'pro', 'pro_data',
    'drivers',
    'plugins',          # oracle_jdbc 等插件由 plugin_loader 动态加载，需随包复制
    'user_management',  # RBAC 蓝图模板(html)与初始化库(schema)需随包复制
    'db',               # RBAC 建表脚本 user_management_schema.sql 需随包复制
]

# JSON config files
data_files = [
    'dbc_config.json',
    'scheduler_jobs.json',
    'version.json',
    'builtin_registry.json',  # 插件市场回退数据（plugin_market.py）
    'dbcheck-quotes.json',  # web_ui.py 读取的协议/格言文案
]

# Build datas list with absolute paths
datas = [(os.path.join(PROJECT_DIR, d), d) for d in data_dirs]
datas += [(os.path.join(PROJECT_DIR, f), f) for f in data_files]

a = Analysis(
    [os.path.join(PROJECT_DIR, 'web_ui.py')],
    pathex=[PROJECT_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'flask',
        'pymysql', 'pymysql.constants', 'pymysql.constants.CLIENT',
        'psycopg2', 'psycopg2._psycopg',
        'oracledb',
        'pyodbc',
        'yasdb',
        'paramiko', 'paramiko.transport', 'paramiko.auth_handler',
        'jinja2', 'jinja2.ext',
        'docx',
        'openpyxl',
        'psutil', 'psutil._psutil_linux', 'psutil._linux',
        'charset_normalizer', 'charset_normalizer.md__mypyc',
        'certifi',
        'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.backends',
        'cryptography.hazmat.bindings', 'cryptography.hazmat.primitives',
        'cryptography.utils',
        'bcrypt',
        'markupsafe', 'markupsafe._speedups',
        'werkzeug', 'werkzeug._internal', 'werkzeug.utils', 'werkzeug.wrappers',
        'itsdangerous',
        'click', 'click._compat',
        'blinker',
        'cffi', 'cffi.api', 'cffi.backend_ctypes',
        'six',
        'idna',
        'urllib3', 'urllib3.util', 'urllib3.util.ssl_',
        'et_xmlfile', 'et_xmlfile.xmlfile',
        'yaml', 'yaml.composer', 'yaml.constructor', 'yaml.cyaml',
        'dotenv',
        'asyncio',
        'gevent', 'gevent.monkey', 'gevent.socket', 'gevent.pywsgi',
        'gevent.local', 'gevent.hub',
        'gevent.server', 'gevent._greenlet_primitives',
        'greenlet',
        'engineio.async_drivers.gevent',
        'socketio.async_server.gevent',
        # App modules
        'main', 'main_mysql', 'main_pg', 'main_oracle_full',
        'main_dm', 'main_sqlserver', 'main_tidb', 'main_ivorysql', 'main_yashandb', 'main_kingbase',
        'analyzer', 'config_baseline', 'server_inspect',
        'run_inspection', 'inspection_init_db', 'inspection_engine',
        'inspection_dal', 'inspection_api', 'api_v1',
        'auth', 'notifier', 'scheduler', 'db_history',
        'monitor_engine', 'monitor_queries', 'pdf_export',
        'slow_query_analyzer', 'ssh_tunnel', 'desensitize',
        'index_health', 'version', 'mod_logger',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='dbcheck',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name='DBCheck-Linux',
    upx=False,
    upx_exclude=[],
    bootloader_ignore_signals=False,
    target_arch=None,
    strip=False,
    debug=False,
)
