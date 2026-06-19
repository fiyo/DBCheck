# -*- mode: python ; coding: utf-8 -*-
# DBCheck Windows 打包配置

import os

block_cipher = None

# Build script cd's to project root before calling pyinstaller.
# So CWD == project root directory.
PROJECT_DIR = os.getcwd()

# Directories to include as data
data_dirs = [
    'web_templates', 'static', 'i18n', 'templates',
    'data', 'rag', 'pro', 'pro_data',
]

# JSON config files
data_files = [
    'dbc_config.json',
    'scheduler_jobs.json',
    'version.json',
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
        'flask', 'flask_cors',
        'pymysql', 'pymysql.constants', 'pymysql.constants.CLIENT',
        'psycopg2', 'psycopg2._psycopg',
        'oracledb',
        'pyodbc',
        'dmpython', 'dmpython.exceptions',
        'paramiko', 'paramiko.transport', 'paramiko.auth_handler',
        'jinja2', 'jinja2.ext',
        'python_docx', 'docx',
        'openpyxl',
        'psutil', 'psutil._psutil_windows', 'psutil._pswindows',
        'charset_normalizer', 'charset_normalizer.md__mypyc',
        'certifi',
        'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.backends',
        'cryptography.hazmat.bindings', 'cryptography.hazmat.primitives',
        'cryptography.utils',
        'bcrypt',
        'markupsafe', 'markupsafe._speedups',
        'werkzeug', 'werkzeug._internal', 'werkzeug.utils', 'werkzeug.wrappers',
        'itsdangerous',
        'click', 'click._compat', 'click._bashcomplete',
        'blinker',
        'cffi', 'cffi.api', 'cffi.backend_ctypes',
        'six',
        'idna',
        'urllib3', 'urllib3.util', 'urllib3.util.ssl_',
        'et_xmlfile', 'et_xmlfile.xmlfile',
        'defusedxml', 'defusedxml.ElementTree',
        'yaml', 'yaml.composer', 'yaml.constructor', 'yaml.cyaml',
        'dotenv',
        'asyncio',
        'gevent', 'gevent.monkey', 'gevent.socket', 'gevent.pywsgi',
        'gevent.wsgi', 'gevent.http', 'gevent.local', 'gevent.hub',
        'gevent.server', 'gevent._greenlet_primitives',
        'greenlet',
        'engineio.async_drivers.gevent',
        'socketio.async_server.gevent',
        # App modules
        'main', 'main_mysql', 'main_pg', 'main_oracle_full',
        'main_dm', 'main_sqlserver', 'main_tidb', 'main_ivorysql',
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
    upx=True,
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
    name='DBCheck-Windows',
    upx=True,
    upx_exclude=[],
    bootloader_ignore_signals=False,
    target_arch=None,
    strip=False,
    debug=False,
)
