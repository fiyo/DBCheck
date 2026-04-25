# -*- mode: python ; coding: utf-8 -*-
import os
import secrets  # 仅保留这一个导入（生成密钥）

block_cipher = None

# 项目根目录（保持不变）
project_root = '/root/open_dbcheck_v2.5'

# 加密关键：直接生成 16 字节二进制密钥（AES-128，旧版本无兼容问题）
ENCRYPT_KEY = secrets.token_bytes(16)  # 无需手动生成，脚本自动生成（每次打包一致）

a = Analysis(
    ['main.py'],
    pathex=[project_root],
    binaries=[],
    datas=[
        (os.path.join(project_root, 'templates', 'sqltemplates.ini'), 'templates'),

    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=None,  # 关键1：设为 None，避免触发密钥文件生成
    key=ENCRYPT_KEY,  # 关键2：直接传 16 字节二进制密钥（绕开 Bug）
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MYSQL',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # 保持禁用 UPX
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None
)
