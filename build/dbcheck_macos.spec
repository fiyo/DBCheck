# -*- mode: python ; coding: utf-8 -*-
# RaccoonX macOS 打包配置

import os

block_cipher = None

# Build script cd's to project root before calling pyinstaller.
# So CWD == project root directory.
PROJECT_DIR = os.getcwd()

# Directories to include as data
# NOTE: 'data' is a runtime directory (SQLite DBs), not packaged.
data_dirs = [
    'web_templates', 'i18n', 'templates',
    'assets',           # 静态资源(web/品牌logo/db_logos)随包复制，static_folder 依赖
    'modules/rag', 'modules/pro', 'data/pro_data',
    'drivers',
    'plugins',          # oracle_jdbc 等插件由 plugin_loader 动态加载，需随包复制
    'modules/user_management',  # RBAC 蓝图模板(html)与初始化库(schema)需随包复制
    'modules/config',   # builtin_registry/builtin_types/version 等 JSON 配置需随包复制
    'db',               # user_management_schema.sql 建表脚本需随包复制
]

# JSON config files
data_files = [
    'dbc_config.json',
    ('modules/config/version.json', 'version.json'),  # 重定向到 exe 同目录（frozen 模式 /version.json 路由读 base/version.json）
    # builtin_registry.json / dbcheck-quotes.json 位于 modules/config/ 下，
    # 已随上面的 data_dirs 整体打包，此处无需重复列出（根目录原本就没有这两个文件）。
]

# Build datas list with absolute paths
# 注意：data/pro_data 这类运行时目录是空目录，git 不跟踪，CI 全新 checkout 下不存在。
# 这里对 data/ 开头的目录自动创建，其余缺失目录降级为 WARN 跳过，
# 避免 PyInstaller 报 "ERROR: Unable to find '<dir>' when adding binary and data files"。
datas = []
for d in data_dirs:
    src = os.path.join(PROJECT_DIR, d)
    if not os.path.exists(src):
        if d.replace('\\', '/').startswith('data/'):
            os.makedirs(src, exist_ok=True)
            print(f"[spec] created runtime dir: {d}")
        else:
            print(f"[WARN] data dir not found, skipped: {src}")
            continue
    datas.append((src, d))
for item in data_files:
    if isinstance(item, tuple):
        f, dst = item
    else:
        f, dst = item, item
    src = os.path.join(PROJECT_DIR, f)
    if os.path.exists(src):
        datas.append((src, dst))
    else:
        print(f"[WARN] data file not found, skipped: {src}")

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
        'redis',  # Redis / Redis Cluster 插件动态 import，需显式 hiddenimport 才能打进冻结版
        'paramiko', 'paramiko.transport', 'paramiko.auth_handler',
        'jinja2', 'jinja2.ext',
        'docx',
        'openpyxl',
        'psutil', 'psutil._psutil_osx', 'psutil._psutil_posix',
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
        # App modules（已统一迁入 modules/ 命名空间；列举各子包，PyInstaller 递归收集全部子模块）
        'modules.entrypoints', 'modules.inspection', 'modules.web',
        'modules.pro', 'modules.rag', 'modules.user_management',
        'modules.desensitize', 'modules.notify', 'modules.monitor',
        'modules.server', 'modules.skill', 'modules.ssh', 'modules.ingest',
        'modules.intelligence', 'modules.core', 'modules.config',
        'modules.db_types', 'modules.disaster_recovery', 'modules.pluginkit',
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
    name='RaccoonX-macOS',
    upx=False,
    upx_exclude=[],
    bootloader_ignore_signals=False,
    target_arch=None,
    strip=False,
    debug=False,
)
