# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
Oracle Instant Client 自动下载工具
支持 Windows x64、Linux x64、macOS x64/arm64
"""
import os
import sys
import json
import platform
import shutil
import zipfile
import tempfile
import urllib.request
import urllib.error
import re
from pathlib import Path

# Oracle Instant Client Basic 下载配置
# Oracle 使用 OTN (Oracle Technology Network) 下载
#
# URL 发现策略（三层兜底）：
#   1. 从 Oracle 官网下载页面自动抓取最新下载链接
#   2. 抓取失败时使用硬编码的最新已知 URL
#   3. 硬编码 URL 也 404 时提示用户手动下载
#
# 这样即使 Oracle 未来再次变更 URL 格式，软件仍可通过在线发现获取新链接

# 每个平台的 Instant Client 版本配置
# 键名与 detect_platform() 返回值一致
PLATFORM_VERSIONS = {
    "windows_x64":  {"version": "23.26.2.0.0", "version_dir": "2326200"},
    "windows_nt":   {"version": "21.22.0.0.0",  "version_dir": "2122000"},
    "windows_ia64": {"version": "10.2.0.3.0",  "version_dir": "1020300"},
    "linux_x64":   {"version": "23.26.2.0.0", "version_dir": "2326200"},
    "darwin_x64":  {"version": "19.16.0.0.0dbru", "version_dir": "1916000"},
    "darwin_arm64": {"version": "23.26.1.0.0", "version_dir": "2326100"},
}

def _get_platform_info(platform_key):
    """返回 (version, version_dir) 元组，找不到时返回 (None, None)"""
    info = PLATFORM_VERSIONS.get(platform_key)
    if not info:
        return None, None
    return info["version"], info["version_dir"]


# AtomGit 镜像下载 URL（首选）—— 国内用户高速下载，不受 Oracle 授权限制
# 注意：Release 附件需要手动在 AtomGit 网站上传
ATOMGIT_DOWNLOAD_URLS = {
    "windows_x64":  "https://atomgit.com/wfgyj/DBCheck/releases/download/instantclient/instantclient-basic-windows.x64-23.26.2.0.0.zip",
    "windows_nt":    "https://atomgit.com/wfgyj/DBCheck/releases/download/instantclient/instantclient-basic-nt-21.22.0.0.0dbru.zip",
    "windows_ia64": "https://atomgit.com/wfgyj/DBCheck/releases/download/instantclient/instantclient-basic-win-ia64-10.2.0.3.0.zip",
    "linux_x64":    "https://atomgit.com/wfgyj/DBCheck/releases/download/instantclient/instantclient-basic-linux.x64-23.26.2.0.0.zip",
    "darwin_x64":   "https://atomgit.com/wfgyj/DBCheck/releases/download/instantclient/instantclient-basic-macos.x64-19.16.0.0.0dbru.dmg",
    "darwin_arm64": "https://atomgit.com/wfgyj/DBCheck/releases/download/instantclient/instantclient-basic-macos.arm64-23.26.1.0.0.dmg",
}

# 百度网盘备用下载（手动）—— 当 AtomGit 无法访问时使用
# 代码无法直接下载网盘文件，需要提供手动下载指引
BAIDU_PAN_URL = "https://pan.baidu.com/s/1Gq9QrXN-Wv979cGcYfeEew?pwd=cray"
BAIDU_PAN_CODE = "cray"
BAIDU_FILENAMES = {
    "windows_x64":  "instantclient-basic-windows.x64-23.26.2.0.0.zip",
    "windows_nt":    "instantclient-basic-nt-21.22.0.0.0dbru.zip",
    "windows_ia64": "instantclient-basic-win-ia64-10.2.0.3.0.zip",
    "linux_x64":    "instantclient-basic-linux.x64-23.26.2.0.0.zip",
    "darwin_x64":   "instantclient-basic-macos.x64-19.16.0.0.0dbru.dmg",
    "darwin_arm64": "instantclient-basic-macos.arm64-23.26.1.0.0.dmg",
}

# Oracle 官方下载页面（按平台）
ORACLE_DOWNLOAD_PAGES = {
    "windows_x64":  "https://www.oracle.com/database/technologies/instant-client/winx64-64-downloads.html",
    "windows_nt":    "https://www.oracle.com/database/technologies/instant-client/win32-32-downloads.html",
    "windows_ia64": "https://www.oracle.com/database/technologies/instant-client/winx64-64-downloads.html",
    "linux_x64":    "https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html",
    "darwin_x64":   "https://www.oracle.com/database/technologies/instant-client/macos-intel-x64-downloads.html",
    "darwin_arm64": "https://www.oracle.com/database/technologies/instant-client/macos-arm64-downloads.html",
}

# 硬编码兜底 URL（Oracle URL 格式）—— Oracle 页面抓取失败时使用
FALLBACK_URLS = {}
for _pk, _info in PLATFORM_VERSIONS.items():
    _ver = _info["version"]
    _vdir = _info["version_dir"]
    if "linux" in _pk:
        FALLBACK_URLS[_pk] = (
            f"https://download.oracle.com/otn_software/linux/instantclient/"
            f"{_vdir}/instantclient-basic-linux.x64-{_ver}.zip"
        )
    elif "darwin" in _pk:
        _ext = "dmg" if _ver.endswith("dmg") or _ver.endswith("dbru") else "dmg"
        # macOS 文件名格式特殊，直接拼文件名
        if "arm64" in _pk:
            _fname = f"instantclient-basic-macos.arm64-{_ver}.dmg"
        else:
            _fname = f"instantclient-basic-macos.x64-{_ver}.dmg"
        FALLBACK_URLS[_pk] = (
            f"https://download.oracle.com/otn_software/mac/instantclient/"
            f"{_vdir}/{_fname}"
        )
    else:
        # Windows 平台
        if _pk == "windows_ia64":
            _fname = f"instantclient-basic-win-ia64-{_ver}.zip"
        elif _pk == "windows_nt":
            _fname = f"instantclient-basic-nt-{_ver}.zip"
        else:
            _fname = f"instantclient-basic-windows.x64-{_ver}.zip"
        FALLBACK_URLS[_pk] = (
            f"https://download.oracle.com/otn_software/nt/instantclient/"
            f"{_vdir}/{_fname}"
        )


def _discover_download_url(platform_key):
    """
    从 Oracle 官网下载页面自动抓取 instantclient-basic ZIP 的下载 URL。
    返回 (url, filename) 或 (None, None) 如果抓取失败。

    平台页面结构：Oracle 下载页面用 <a> 标签包含 download= 查询参数指向实际文件，
    例如 <a href="/pre-fm-redirect/instantclient/...?platform_id=...&download=...">
    download 参数的值就是 download.oracle.com 下的相对路径。
    """
    page_url = ORACLE_DOWNLOAD_PAGES.get(platform_key)
    if not page_url:
        return None, None

    # 确定平台对应的文件名关键词（用于从 Oracle 页面抓取真实下载链接）
    if platform_key == "windows_x64":
        keyword = "instantclient-basic-windows.x64"
    elif platform_key == "windows_nt":
        keyword = "instantclient-basic-nt"
    elif platform_key == "windows_ia64":
        keyword = "instantclient-basic-win-ia64"
    elif platform_key == "linux_x64":
        keyword = "instantclient-basic-linux.x64"
    elif platform_key == "darwin_x64":
        keyword = "instantclient-basic-macos.x64"
    elif platform_key == "darwin_arm64":
        keyword = "instantclient-basic-macos.arm64"
    else:
        return None, None

    req = urllib.request.Request(page_url)
    req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

    try:
        response = urllib.request.urlopen(req, timeout=15)
        html = response.read().decode('utf-8', errors='ignore')
        response.close()
    except Exception:
        return None, None

    # 策略1: 从 download= 查询参数中提取路径
    # 匹配: download=/otn_software/nt/instantclient/2326200/instantclient-basic-windows.x64-23.26.2.0.0.zip
    pattern_download = re.compile(
        r'download=([^\s&"\'<>]+\binstantclient-basic[^\s"\'<>]+\.\w+)',
        re.IGNORECASE
    )
    for match in pattern_download.finditer(html):
        path = match.group(1).strip()
        # 取第一个路径段作为文件名匹配
        filename = path.rsplit('/', 1)[-1] if '/' in path else path
        if keyword in filename:
            # 移除末尾可能多带的 &platform_id=xxx
            filename_clean = re.sub(r'[&?].*$', '', filename)
            dl_url = f"https://download.oracle.com{path}"
            return dl_url, filename_clean

    # 策略2: 直接匹配 download.oracle.com 完整 URL
    pattern_direct = re.compile(
        r'https?://download\.oracle\.com(/[^\s"\'<>]*' + re.escape(keyword) + r'[^\s"\'<>]*\.\w+)'
    )
    for match in pattern_direct.finditer(html):
        path = match.group(1).strip()
        dl_url = f"https://download.oracle.com{path}"
        filename = path.rsplit('/', 1)[-1] if '/' in path else path
        filename_clean = re.sub(r'[&?].*$', '', filename)
        return dl_url, filename_clean

    return None, None


def _get_download_config(platform_key):
    """
    获取下载配置，三层优先级：
      1. GitHub 镜像（不受 Oracle 授权限制，直接下载）
      2. Oracle 官网在线发现
      3. Oracle 硬编码兜底
    返回 dict with 'url', 'filename', 'ext'.
    """
    # 1. AtomGit 镜像（首选）—— 国内用户高速下载
    atomgit_url = ATOMGIT_DOWNLOAD_URLS.get(platform_key, "")
    if atomgit_url:
        filename = atomgit_url.rsplit('/', 1)[-1] if '/' in atomgit_url else atomgit_url
        _, ext = os.path.splitext(filename)
        return {
            'url': atomgit_url,
            'filename': filename,
            'ext': ext,
            'source': 'atomgit',
        }

    # 2. 尝试 Oracle 官网在线发现
    discovered_url, discovered_filename = _discover_download_url(platform_key)
    if discovered_url and discovered_filename:
        return {
            'url': discovered_url,
            'filename': discovered_filename,
            'ext': '.zip',
            'source': 'discovered',
        }

    # 3. 使用 Oracle 硬编码兜底
    fallback_url = FALLBACK_URLS.get(platform_key)
    if not fallback_url:
        return None

    # 从 URL 中提取文件名（兜底时使用 AtomGit URL 构造文件名）
    version, _ = _get_platform_info(platform_key)
    if not version:
        return None
    if platform_key == "windows_x64":
        filename = f"instantclient-basic-windows.x64-{version}.zip"
    elif platform_key == "windows_nt":
        filename = f"instantclient-basic-nt-{version}.zip"
    elif platform_key == "windows_ia64":
        filename = f"instantclient-basic-win-ia64-{version}.zip"
    elif platform_key == "linux_x64":
        filename = f"instantclient-basic-linux.x64-{version}.zip"
    elif platform_key == "darwin_x64":
        filename = f"instantclient-basic-macos.x64-{version}.dmg"
    elif platform_key == "darwin_arm64":
        filename = f"instantclient-basic-macos.arm64-{version}.dmg"
    else:
        return None

    return {
        'url': fallback_url,
        'filename': filename,
        'ext': '.zip',
        'source': 'fallback',
    }


def detect_platform():
    """检测当前平台，返回对应的平台标识"""
    system = platform.system().lower()
    arch = platform.machine().lower()

    if system == "windows":
        # 架构判断优先级：ARM64 > IA64 (Itanium) > AMD64 (x64) > x86 (nt/32-bit)
        if arch in ("arm64", "aarch64"):
            return "windows_arm64"   # 暂不支持，预留
        elif arch in ("ia64", "itanium"):
            return "windows_ia64"
        elif arch in ("amd64", "x86_64", "x64"):
            return "windows_x64"
        else:
            # x86 32-bit 视为 nt 平台
            return "windows_nt"
    elif system == "linux":
        return "linux_x64"
    elif system == "darwin":
        if arch in ("arm64", "aarch64"):
            return "darwin_arm64"
        return "darwin_x64"
    else:
        raise ValueError(f"不支持的操作系统: {system}")


def _urlretrieve_with_headers(url, filename, headers=None, callback=None):
    """使用 urllib 下载文件，支持自定义 headers 和进度回调

    带下载速度检测：如果速度低于 30KB/s 持续 10 秒，主动中断并抛出 SlowDownloadError。
    """
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 401 or e.code == 403:
            raise RuntimeError(
                "Oracle 官网需要登录才能下载。请手动访问 "
                "https://www.oracle.com/database/technologies/instant-client/downloads.html "
                "下载后解压到 DBCheck/drivers/oracle_client/ 对应平台目录。"
            )
        raise

    total = response.headers.get('Content-Length')
    total = int(total) if total else 0
    downloaded = 0
    buf_size = 8192
    import time
    start_time = time.time()
    last_callback_time = start_time
    max_initial_wait = 8  # 8 秒内如果速度低于阈值就中断
    min_speed = 20 * 1024  # 20 KB/s 最低可接受速度

    with open(filename, 'wb') as f:
        while True:
            chunk = response.read(buf_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)

            now = time.time()
            elapsed = now - start_time

            # 每 1 秒回调一次，显示真实速度和已下载量
            if now - last_callback_time >= 1:
                avg_speed = downloaded / elapsed if elapsed > 0 else 0
                if avg_speed >= 1024 * 1024:
                    speed_str = f'{avg_speed / 1024 / 1024:.1f} MB/s'
                else:
                    speed_str = f'{avg_speed / 1024:.1f} KB/s'
                dl_str = f'{downloaded / 1024 / 1024:.1f} MB' if downloaded >= 1024 * 1024 else f'{downloaded / 1024:.0f} KB'
                if callback:
                    callback(downloaded, total, speed_str, dl_str)
                last_callback_time = now

            # 8 秒后检查速度：如果低于 20KB/s，直接中断
            if elapsed > max_initial_wait and avg_speed < min_speed:
                response.close()
                raise RuntimeError(
                    f'下载速度过慢（{speed_str}），已自动中断。\n\n'
                    '请改用百度网盘手动下载（详见下方指引）。'
                )

            # 硬超时：GitHub 在国内下载 135MB，如果超过 300 秒就中断
            if elapsed > 300:
                response.close()
                raise RuntimeError(
                    f'下载超时（已超过 5 分钟，仅下载 {dl_str}），已自动中断。\n\n'
                    '请改用百度网盘手动下载（详见下方指引）。'
                )

    response.close()


def download_instant_client(platform_key=None, target_dir=None, progress_callback=None):
    """
    下载并解压 Oracle Instant Client Basic

    Args:
        platform_key: 平台标识，如 'windows_x64', 'linux_x64', 'darwin_x64', 'darwin_arm64'
                      为 None 时自动检测
        target_dir: 目标目录，为 None 时使用当前目录下的 drivers/oracle_client/<platform>
        progress_callback: 进度回调函数 func(downloaded, total)

    Returns:
        dict: {
            'success': bool,
            'platform': str,
            'version': str,
            'install_dir': str,
            'error': str or None
        }
    """
    if platform_key is None:
        try:
            platform_key = detect_platform()
        except ValueError as e:
            return {'success': False, 'platform': '', 'version': '', 'install_dir': '', 'error': str(e)}

    if platform_key not in ORACLE_DOWNLOAD_PAGES:
        return {
            'success': False, 'platform': platform_key, 'version': '', 'install_dir': '',
            'error': f'不支持的平台: {platform_key}'
        }

    base_dir = Path(target_dir) if target_dir else Path(__file__).resolve().parent
    install_dir = base_dir / 'drivers' / 'oracle_client' / platform_key
    version, _ = _get_platform_info(platform_key)
    if not version:
        version = "unknown"
    cfg = _get_download_config(platform_key)
    if cfg is None:
        return {
            'success': False, 'platform': platform_key, 'version': '', 'install_dir': '',
            'error': f'无法获取 {platform_key} 的下载配置'
        }

    source_name = {'atomgit': 'AtomGit 镜像', 'discovered': 'Oracle 在线发现', 'fallback': 'Oracle 硬编码兜底'}.get(cfg.get('source', '?'), '?')
    print(f'[DBCheck Oracle Download] platform={platform_key}, source={cfg.get("source","?")} ({source_name}), url={cfg["url"][:80]}...')

    result = {
        'success': False,
        'platform': platform_key,
        'version': version,
        'install_dir': str(install_dir),
        'error': None
    }

    # 检查是否已安装（先尝试展平嵌套子目录）
    if install_dir.exists():
        _flatten_nested_client_dir(install_dir)

        if platform_key == 'windows_x64':
            marker = install_dir / 'oci.dll'
        elif platform_key == 'linux_x64':
            marker = install_dir / 'libclntsh.so'
        else:
            marker = install_dir / 'libclntsh.dylib'

        if marker.exists():
            result['success'] = True
            result['error'] = f'Oracle Instant Client {version} 已安装在 {install_dir}'
            return result

    # 创建目录
    install_dir.mkdir(parents=True, exist_ok=True)

    # 下载
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix='dbcheck_oracle_')
        tmp_file = os.path.join(tmp_dir, cfg['filename'])

        # 使用发现的版本号（可能是最新版的）
        dl_version_match = re.search(r'(\d+\.\d+(?:\.\d+)*)\.\w+$', cfg['filename'])
        dl_version = dl_version_match.group(1) if dl_version_match else version

        if progress_callback:
            source_tag = '(在线获取链接中...)' if cfg.get('source') == 'discovered' else '(使用缓存链接)'
            progress_callback('downloading', 0, 100, f'正在下载 Oracle Instant Client {dl_version} {source_tag}')

        # Oracle OTN 下载需要特定的 headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }

        try:
            _urlretrieve_with_headers(
                cfg['url'], tmp_file, headers=headers,
                callback=lambda d, t, speed='', dl='': progress_callback('downloading', int(d / max(t, 1) * 100) if t else 0, 100, f'下载中 {dl} {speed}') if progress_callback else None
            )
        except Exception as dl_err:
            # GitHub 下载失败（网络不可达、超时等），提示使用百度网盘手动下载
            if cfg.get('source') == 'github':
                _baidu_manual_filename = BAIDU_FILENAMES.get(platform_key, cfg['filename'])
                raise RuntimeError(
                    f'GitHub 下载失败（{dl_err}），请改用百度网盘手动下载：\n\n'
                    f'  1. 浏览器打开: {BAIDU_PAN_URL}\n'
                    f'  2. 输入提取码: {BAIDU_PAN_CODE}\n'
                    f'  3. 下载文件: {_baidu_manual_filename}\n'
                    f'  4. 解压到: {install_dir}\n'
                    f'     (确保 oci.dll/libclntsh 等文件直接在该目录下)\n'
                    f'  5. 解压完成后点击"检查安装状态"确认\n\n'
                    f'如需安装到自定义目录，解压到: <DBCheck目录>/drivers/oracle_client/{platform_key}/'
                )
            raise

        # 验证下载的文件
        file_size = os.path.getsize(tmp_file)
        if file_size < 5 * 1024 * 1024:  # Instant Client 至少 5MB
            download_page = ORACLE_DOWNLOAD_PAGES.get(platform_key, 'https://www.oracle.com/database/technologies/instant-client/downloads.html')
            _baidu_filename = BAIDU_FILENAMES.get(platform_key, cfg['filename'])
            raise RuntimeError(
                f'下载的文件过小 ({file_size:,} 字节)，Oracle 要求通过浏览器接受授权协议后才能下载。\n\n'
                '=== 方案一：百度网盘手动下载（推荐） ===\n'
                f'  1. 浏览器打开: {BAIDU_PAN_URL}\n'
                f'  2. 输入提取码: {BAIDU_PAN_CODE}\n'
                f'  3. 下载文件: {_baidu_filename}\n'
                f'  4. 解压到: {install_dir}\n'
                f'  5. 解压完成后点击"检查安装状态"确认\n\n'
                '=== 方案二：Oracle 官网手动下载 ===\n'
                f'  1. 浏览器打开: {download_page}\n'
                f'  2. 勾选同意协议，下载 Instant Client Basic ZIP\n'
                f'  3. 解压到: {install_dir}\n'
                f'     (确保 oci.dll/libclntsh 等文件直接在该目录下)'
            )

        if progress_callback:
            progress_callback('extracting', 70, 100, '正在验证并解压...')

        # 解压
        if cfg['ext'] == '.zip':
            with zipfile.ZipFile(tmp_file, 'r') as zf:
                # Oracle Instant Client ZIP 通常有顶层目录 instantclient_*
                # 修复 bug：不能从 META-INF/ 开始，要找到真正的客户端目录
                top_dir = None
                for name in zf.namelist():
                    # 跳过 META-INF/ 目录（JAR 签名文件）
                    if name.startswith('META-INF/'):
                        continue
                    # 找到第一个 instantclient_* 目录
                    if name.startswith('instantclient_'):
                        top_dir = name.split('/')[0]
                        break
                    # 如果没有顶层目录，文件直接在根目录
                    elif name and not name.endswith('/'):
                        top_dir = ''
                        break

                # 如果没找到 instantclient_* 目录，可能是 ZIP 格式不同
                # 兜底：使用第一个有子目录的条目作为 top_dir
                if top_dir is None:
                    for name in zf.namelist():
                        if '/' in name and name.count('/') >= 1:
                            top_dir = name.split('/')[0]
                            break

                print(f'[DEBUG] 检测到顶层目录: {top_dir}')

                if top_dir:
                    # 提取顶层目录下的所有内容到目标目录
                    for member in zf.infolist():
                        if member.filename.startswith(top_dir + '/'):
                            # 去掉顶层目录前缀
                            rel_path = member.filename[len(top_dir) + 1:]
                            if not rel_path:
                                continue
                            target_path = install_dir / rel_path
                            if member.is_dir():
                                target_path.mkdir(parents=True, exist_ok=True)
                            else:
                                target_path.parent.mkdir(parents=True, exist_ok=True)
                                with zf.open(member) as src, open(target_path, 'wb') as dst:
                                    shutil.copyfileobj(src, dst)
                else:
                    # 没有顶层目录，直接解压
                    zf.extractall(install_dir)

        elif cfg['ext'] == '.dmg':
            # macOS DMG 格式：挂载后复制文件到目标目录
            import subprocess
            mount_point = None
            try:
                # 挂载 DMG
                dmg_basename = os.path.basename(tmp_file)
                result = subprocess.run(
                    ['hdiutil', 'attach', tmp_file, '-nobrowse', '-mountpoint', '/Volumes/dbcheck_tmp_mount'],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode != 0:
                    raise RuntimeError(f'挂载 DMG 失败: {result.stderr}')
                mount_point = '/Volumes/dbcheck_tmp_mount'

                # 找到 Instant Client 顶层目录
                dmg_root = Path(mount_point)
                client_dir = None
                for item in dmg_root.iterdir():
                    if item.name.startswith('instantclient_'):
                        client_dir = item
                        break
                if client_dir is None:
                    # 没找到 instantclient_* 目录，复制所有内容
                    client_dir = dmg_root

                # 复制所有文件到目标目录
                for item in client_dir.iterdir():
                    dest = install_dir / item.name
                    if item.is_dir():
                        if dest.exists():
                            shutil.rmtree(dest)
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)
            finally:
                # 卸载 DMG
                if mount_point:
                    subprocess.run(['hdiutil', 'detach', mount_point],
                                   capture_output=True, timeout=10)
        else:
            # tar.gz / tar.xz
            import tarfile
            with tarfile.open(tmp_file, 'r:*') as tf:
                tf.extractall(install_dir)

        # 清理临时文件
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # 解压后再次展平（处理解压后文件在 instantclient_* 子目录的情况）
        _flatten_nested_client_dir(install_dir)

        # 验证
        if platform_key == 'windows_x64':
            marker = install_dir / 'oci.dll'
        elif platform_key == 'linux_x64':
            marker = install_dir / 'libclntsh.so'
        else:
            marker = install_dir / 'libclntsh.dylib'

        if marker.exists():
            result['success'] = True
            if progress_callback:
                progress_callback('done', 100, 100, f'Oracle Instant Client {version} 安装成功')
        else:
            # 调试：列出 install_dir 下的文件，帮助诊断
            try:
                files = list(install_dir.iterdir())[:10]
                file_list = ', '.join(f.name for f in files)
                result['error'] = f'下载完成但未能找到必要的库文件（{marker.name}）。目录内容: {file_list}'
            except Exception:
                result['error'] = '下载完成但未能找到必要的库文件，请检查下载内容'

    except RuntimeError as e:
        result['error'] = str(e)
    except Exception as e:
        result['error'] = f'下载失败: {e}'

    # 清理临时文件（失败时）
    if tmp_dir and os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return result


def _flatten_nested_client_dir(install_dir):
    """用户手动解压 ZIP 后，文件通常在 instantclient_* 子目录下。
    检测并自动将文件"展平"到 install_dir 根目录。
    """
    install_dir = Path(install_dir)
    if not install_dir.exists():
        return
    nested = None
    for item in install_dir.iterdir():
        if item.is_dir() and item.name.startswith('instantclient_'):
            nested = item
            break
    if nested is None:
        return
    # 把子目录里的所有文件移到父目录
    for item in nested.iterdir():
        dest = install_dir / item.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    # 移除空子目录
    try:
        shutil.rmtree(nested)
    except Exception:
        pass


def check_installation(platform_key=None, base_dir=None):
    """
    检查 Oracle Instant Client 是否已安装

    Returns:
        dict: {'installed': bool, 'platform': str, 'version': str, 'install_dir': str}
    """
    if platform_key is None:
        try:
            platform_key = detect_platform()
        except ValueError:
            return {'installed': False, 'platform': '', 'version': '', 'install_dir': ''}

    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    install_dir = base / 'drivers' / 'oracle_client' / platform_key

    # 自动展平：处理用户手动解压后文件在 instantclient_* 子目录的情况
    _flatten_nested_client_dir(install_dir)

    if platform_key == 'windows_x64':
        marker = install_dir / 'oci.dll'
    elif platform_key == 'linux_x64':
        marker = install_dir / 'libclntsh.so'
    else:
        marker = install_dir / 'libclntsh.dylib'

    installed = marker.exists()

    # 尝试从 README 或文件名中获取版本
    version = ''
    if installed:
        readme = install_dir / 'README.md'
        if readme.exists():
            content = readme.read_text(encoding='utf-8', errors='ignore')
            m = re.search(r'(\d+\.\d+)', content)
            if m:
                version = m.group(1)
        # 从文件名检测版本
        if not version:
            for f in install_dir.iterdir():
                m = re.search(r'instantclient[_-]*(\d+\.\d+)', str(f.name))
                if m:
                    version = m.group(1)
                    break

    return {
        'installed': installed,
        'platform': platform_key,
        'version': version or 'unknown',
        'install_dir': str(install_dir)
    }


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Oracle Instant Client 自动下载工具')
    parser.add_argument('--platform', choices=['windows_x64', 'windows_nt', 'windows_ia64', 'linux_x64', 'darwin_x64', 'darwin_arm64'],
                        help='目标平台（默认自动检测）')
    parser.add_argument('--target', help='DBCheck 根目录（默认脚本所在目录）')
    parser.add_argument('--check', action='store_true', help='仅检查安装状态')
    parser.add_argument('--json', action='store_true', help='以 JSON 格式输出结果')
    args = parser.parse_args()

    if args.check:
        result = check_installation(args.platform, args.target)
    else:
        result = download_instant_client(args.platform, args.target)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get('success') or result.get('installed'):
            status = '安装成功' if result.get('success') else '已安装'
            print(f"✅ Oracle Instant Client {result['version']} {status}")
            print(f"   平台: {result['platform']}")
            print(f"   路径: {result['install_dir']}")
        else:
            print(f"❌ 失败: {result['error']}")
            sys.exit(1)
