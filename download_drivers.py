# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck Professional — 专有商业软件，保留一切权利（Proprietary Software, All Rights Reserved）.
# See LICENSE for full license text.
#

"""
DBCheck 驱动一键下载工具
从 GitHub Releases 下载完整的 drivers.zip（包含 Oracle Instant Client + YashanDB 客户端）
解压后用户开箱即用，无需手动到各官网下载。
"""
import os
import sys
import json
import time
import platform
import shutil
import zipfile
import tempfile
import urllib.request
import urllib.error
import re
from pathlib import Path

DRIVERS_ZIP_URLS = [
    "https://github.com/fiyo/DBCheck/releases/download/drivers/drivers.zip",
    "https://atomgit.com/wfgyj/DBCheck/releases/download/drivers/drivers.zip",
]
DRIVERS_DIR = "drivers"


def _urlretrieve_with_progress(url, filename, callback=None):
    """带进度回调的下载函数"""
    req = urllib.request.Request(url)
    req.add_header(
        'User-Agent',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    )

    response = urllib.request.urlopen(req, timeout=30)
    total = response.headers.get('Content-Length')
    total = int(total) if total else 0
    downloaded = 0
    start_time = time.time()

    with open(filename, 'wb') as f:
        while True:
            chunk = response.read(8192)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)

            elapsed = time.time() - start_time
            avg_speed = downloaded / elapsed if elapsed > 0 else 0

            if callback and elapsed % 1 < 0.3:
                if avg_speed >= 1024 * 1024:
                    speed_str = f'{avg_speed / 1024 / 1024:.1f} MB/s'
                else:
                    speed_str = f'{avg_speed / 1024:.1f} KB/s'
                dl_str = f'{downloaded / 1024 / 1024:.1f} MB' if downloaded >= 1024 * 1024 else f'{downloaded / 1024:.0f} KB'
                callback(downloaded, total, speed_str, dl_str)

    response.close()
    return downloaded


def download_drivers(target_dir=None, progress_callback=None):
    """
    从 GitHub Releases 下载 drivers.zip 并解压到 DBCheck/drivers/

    Args:
        target_dir: DBCheck 根目录，默认脚本所在目录
        progress_callback: func(stage, percent, max, message)

    Returns:
        dict: {'success': bool, 'install_dir': str, 'error': str or None,
               'oracle_installed': bool, 'yashandb_installed': bool}
    """
    base_dir = Path(target_dir) if target_dir else Path(__file__).resolve().parent
    drivers_dir = base_dir / DRIVERS_DIR

    result = {
        'success': False,
        'install_dir': str(drivers_dir),
        'error': None,
        'oracle_installed': False,
        'yashandb_installed': False,
    }

    tmp_dir = None
    try:
        # 检查是否已经有完整的驱动
        if drivers_dir.exists():
            oracle_ok = (drivers_dir / 'oracle_client').exists()
            yashandb_ok = (drivers_dir / 'yashandb').exists()
            if oracle_ok or yashandb_ok:
                result['success'] = True
                result['oracle_installed'] = oracle_ok
                result['yashandb_installed'] = yashandb_ok
                return result

        tmp_dir = tempfile.mkdtemp(prefix='dbcheck_drivers_')
        tmp_zip = os.path.join(tmp_dir, 'drivers.zip')

        if progress_callback:
            progress_callback('downloading', 0, 100, '正在从 GitHub Releases 下载驱动包...')

        print(f'[DBCheck Drivers] 正在下载: {DRIVERS_ZIP_URLS[0]}')
        last_err = None
        for url in DRIVERS_ZIP_URLS:
            try:
                _urlretrieve_with_progress(
                    url, tmp_zip,
                    callback=lambda d, t, speed='', dl='': (
                        progress_callback('downloading', int(d / max(t, 1) * 100) if t else 0, 100,
                                          f'下载中 {dl} {speed}')
                        if progress_callback else None
                    )
                )
                break  # 成功就跳出
            except Exception as dl_err:
                last_err = dl_err
                print(f'[DBCheck Drivers] {url[:60]}... 失败: {dl_err}')
                continue
        else:
            # 所有 URL 都失败了
            raise RuntimeError(
                f'下载失败（{last_err}），请检查网络连接。\n\n'
                f'如无法自动下载，可手动访问以下链接下载 drivers.zip：\n'
                f'  {DRIVERS_ZIP_URLS[0]}\n'
                f'下载后解压到 DBCheck/drivers/ 目录即可。'
            )

        # 验证
        file_size = os.path.getsize(tmp_zip)
        if file_size < 10 * 1024 * 1024:
            raise RuntimeError(
                f'下载的文件过小 ({file_size:,} 字节)，文件可能不完整。\n'
                f'请检查网络后重试，或手动下载：{DRIVERS_ZIP_URLS[0]}'
            )

        if progress_callback:
            progress_callback('extracting', 70, 100, '正在解压驱动包...')

        # 解压
        print(f'[DBCheck Drivers] 正在解压到: {drivers_dir}')
        with zipfile.ZipFile(tmp_zip, 'r') as zf:
            zf.extractall(drivers_dir)

        # 展平：如果解压后出现 drivers/ 嵌套目录，把内容移出来
        nested_drivers = drivers_dir / 'drivers'
        if nested_drivers.exists() and nested_drivers.is_dir():
            print(f'[DBCheck Drivers] 检测到嵌套 drivers/ 目录，正在展平...')
            for item in nested_drivers.iterdir():
                target = drivers_dir / item.name
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True) if item.is_dir() else os.remove(target)
                shutil.move(str(item), str(target))
            shutil.rmtree(nested_drivers, ignore_errors=True)
            print(f'[DBCheck Drivers] 展平完成')

        # 清理临时文件
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir = None

        # 验证驱动完整性
        oracle_dir = drivers_dir / 'oracle_client'
        yashandb_dir = drivers_dir / 'yashandb'

        oracle_ok = oracle_dir.exists()
        yashandb_ok = yashandb_dir.exists()

        # 检查 Oracle 当前平台的关键文件
        current_platform = _detect_platform_key()
        if oracle_ok and current_platform:
            oracle_platform_dir = oracle_dir / current_platform
            if current_platform == 'windows_x64':
                marker = oracle_platform_dir / 'oci.dll'
            elif current_platform == 'linux_x64':
                marker = oracle_platform_dir / 'libclntsh.so'
            else:
                marker = oracle_platform_dir / 'libclntsh.dylib'
            oracle_ok = marker.exists()

        result['success'] = oracle_ok or yashandb_ok
        result['oracle_installed'] = oracle_ok
        result['yashandb_installed'] = yashandb_ok

        if progress_callback:
            msgs = []
            if oracle_ok:
                msgs.append('Oracle Client ✅')
            if yashandb_ok:
                msgs.append('YashanDB Client ✅')
            progress_callback('done', 100, 100, f'驱动安装完成: {", ".join(msgs)}')

        return result

    except RuntimeError as e:
        result['error'] = str(e)
        return result
    except Exception as e:
        result['error'] = f'下载失败: {e}'
        return result
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _detect_platform_key():
    """检测当前平台标识"""
    system = platform.system().lower()
    arch = platform.machine().lower()
    if system == "windows":
        return "windows_x64"
    elif system == "linux":
        return "linux_x64"
    elif system == "darwin":
        return "darwin_arm64" if arch in ("arm64", "aarch64") else "darwin_x64"
    return None


def check_all_drivers(base_dir=None):
    """
    检查所有驱动的安装状态

    Returns:
        dict: {
            'oracle': {'installed': bool, 'platform': str, 'version': str, 'install_dir': str},
            'yashandb': {'installed': bool, 'install_dir': str}
        }
    """
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    drivers_dir = base / DRIVERS_DIR

    # Oracle
    oracle_result = {'installed': False, 'platform': '', 'version': 'unknown', 'install_dir': ''}
    oracle_dir = drivers_dir / 'oracle_client'
    if oracle_dir.exists():
        current_platform = _detect_platform_key()
        if current_platform:
            plat_dir = oracle_dir / current_platform
            oracle_result['platform'] = current_platform
            oracle_result['install_dir'] = str(plat_dir)
            if current_platform == 'windows_x64':
                marker = plat_dir / 'oci.dll'
            elif current_platform == 'linux_x64':
                marker = plat_dir / 'libclntsh.so'
            else:
                marker = plat_dir / 'libclntsh.dylib'
            oracle_result['installed'] = marker.exists()

    # YashanDB（按平台子目录组织）
    yashandb_dir = drivers_dir / 'yashandb'
    yashandb_result = {'installed': False, 'install_dir': str(yashandb_dir), 'platform': ''}
    if yashandb_dir.exists():
        current_platform = _detect_platform_key()
        # 映射 oracle 风格 key → yashandb 子目录名
        _yasdb_plat_map = {
            'windows_x64': 'windows-x64',
            'windows_x86': 'windows-x86',
            'linux_x64':   'linux-x64',
            'linux_arm':   'linux-arm',
            'darwin_x64':  'darwin-x64',
            'darwin_arm64': 'darwin-arm64',
        }
        yasdb_plat = _yasdb_plat_map.get(current_platform, '')
        # 真正的库文件在平台子目录下的 lib/ 里：lib/<platform>/lib/*.dll
        yasdb_lib_dir = yashandb_dir / 'lib' / yasdb_plat / 'lib' if yasdb_plat else None
        yashandb_result['platform'] = yasdb_plat
        if yasdb_lib_dir and yasdb_lib_dir.exists():
            if current_platform and current_platform.startswith('windows'):
                marker = yasdb_lib_dir / 'yascli.dll'
            else:
                marker = yasdb_lib_dir / 'libyascli.so'
            yashandb_result['installed'] = marker.exists()
            yashandb_result['install_dir'] = str(yasdb_lib_dir)
        else:
            # 子目录不存在，检查根目录是否有文件（兼容旧结构）
            yashandb_result['installed'] = any(yashandb_dir.iterdir())

    return {'oracle': oracle_result, 'yashandb': yashandb_result}


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='DBCheck 驱动一键下载工具')
    parser.add_argument('--target', help='DBCheck 根目录（默认脚本所在目录）')
    parser.add_argument('--check', action='store_true', help='仅检查驱动安装状态')
    parser.add_argument('--json', action='store_true', help='以 JSON 格式输出结果')
    args = parser.parse_args()

    if args.check:
        result = check_all_drivers(args.target)
    else:
        result = download_drivers(args.target)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.check:
            oracle = result['oracle']
            yashandb = result['yashandb']
            oracle_status = f"Oracle Client {'✅' if oracle['installed'] else '❌'} ({oracle['platform']})"
            yashandb_status = f"YashanDB Client {'✅' if yashandb['installed'] else '❌'}"
            print(f"驱动状态检查:\n  {oracle_status}\n  {yashandb_status}")
        elif result.get('success'):
            msgs = []
            if result.get('oracle_installed'):
                msgs.append('Oracle Client ✅')
            if result.get('yashandb_installed'):
                msgs.append('YashanDB Client ✅')
            print(f"✅ 驱动安装完成: {', '.join(msgs)}")
            print(f"   路径: {result['install_dir']}")
        else:
            print(f"❌ 失败: {result['error']}")
            sys.exit(1)
