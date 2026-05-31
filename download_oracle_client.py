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
# Oracle 使用 OTN (Oracle Technology Network) 下载，需要通过浏览器获取临时链接
# 这里使用 Oracle CDN 的公共下载路径

VERSION = "23.26"  # instantclient-basic 版本号

DOWNLOAD_CONFIG = {
    "windows_x64": {
        "url": (
            "https://download.oracle.com/otn-pub/otn_software/jdbc/instantclient/"
            f"oracle-instantclient-basic-nt.x64-{VERSION.replace('.', '_')}.zip"
        ),
        "filename": f"oracle-instantclient-basic-nt.x64-{VERSION.replace('.', '_')}.zip",
        "ext": ".zip",
    },
    "linux_x64": {
        "url": (
            "https://download.oracle.com/otn-pub/otn_software/jdbc/instantclient/"
            f"instantclient-basic-linux.x64-{VERSION.replace('.', '_')}.zip"
        ),
        "filename": f"instantclient-basic-linux.x64-{VERSION.replace('.', '_')}.zip",
        "ext": ".zip",
    },
    "darwin_x64": {
        "url": (
            "https://download.oracle.com/otn-pub/otn_software/jdbc/instantclient/"
            f"instantclient-basic-macos.x64-{VERSION.replace('.', '_')}.zip"
        ),
        "filename": f"instantclient-basic-macos.x64-{VERSION.replace('.', '_')}.zip",
        "ext": ".zip",
    },
    "darwin_arm64": {
        "url": (
            "https://download.oracle.com/otn-pub/otn_software/jdbc/instantclient/"
            f"instantclient-basic-macos.arm64-{VERSION.replace('.', '_')}.zip"
        ),
        "filename": f"instantclient-basic-macos.arm64-{VERSION.replace('.', '_')}.zip",
        "ext": ".zip",
    },
}


def detect_platform():
    """检测当前平台，返回对应的平台标识"""
    system = platform.system().lower()
    arch = platform.machine().lower()

    if system == "windows":
        return "windows_x64"
    elif system == "linux":
        return "linux_x64"
    elif system == "darwin":
        if arch in ("arm64", "aarch64"):
            return "darwin_arm64"
        return "darwin_x64"
    else:
        raise ValueError(f"不支持的操作系统: {system}")


def _urlretrieve_with_headers(url, filename, headers=None, callback=None):
    """使用 urllib 下载文件，支持自定义 headers 和进度回调"""
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 401 or e.code == 403:
            # Oracle OTN 需要认证 cookie，尝试通过 Oracle 官网获取
            raise RuntimeError(
                "Oracle 官网需要登录才能下载。请手动访问 "
                "https://www.oracle.com/database/technologies/instant-client/downloads.html "
                "下载后解压到 DBCheck/oracle_client/ 对应平台目录。"
            )
        raise

    total = response.headers.get('Content-Length')
    total = int(total) if total else 0
    downloaded = 0
    buf_size = 8192

    with open(filename, 'wb') as f:
        while True:
            chunk = response.read(buf_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if callback:
                callback(downloaded, total)

    response.close()


def download_instant_client(platform_key=None, target_dir=None, progress_callback=None):
    """
    下载并解压 Oracle Instant Client Basic

    Args:
        platform_key: 平台标识，如 'windows_x64', 'linux_x64', 'darwin_x64', 'darwin_arm64'
                      为 None 时自动检测
        target_dir: 目标目录，为 None 时使用当前目录下的 oracle_client/<platform>
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

    if platform_key not in DOWNLOAD_CONFIG:
        return {
            'success': False, 'platform': platform_key, 'version': '', 'install_dir': '',
            'error': f'不支持的平台: {platform_key}'
        }

    base_dir = Path(target_dir) if target_dir else Path(__file__).resolve().parent
    install_dir = base_dir / 'oracle_client' / platform_key
    cfg = DOWNLOAD_CONFIG[platform_key]

    result = {
        'success': False,
        'platform': platform_key,
        'version': VERSION,
        'install_dir': str(install_dir),
        'error': None
    }

    # 检查是否已安装
    if install_dir.exists():
        if platform_key == 'windows_x64':
            marker = install_dir / 'oci.dll'
        elif platform_key == 'linux_x64':
            marker = install_dir / 'libclntsh.so'
        else:
            marker = install_dir / 'libclntsh.dylib'

        if marker.exists():
            result['success'] = True
            result['error'] = f'Oracle Instant Client {VERSION} 已安装在 {install_dir}'
            return result

    # 创建目录
    install_dir.mkdir(parents=True, exist_ok=True)

    # 下载
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix='dbcheck_oracle_')
        tmp_file = os.path.join(tmp_dir, cfg['filename'])

        if progress_callback:
            progress_callback('downloading', 0, 100, f'正在下载 Oracle Instant Client {VERSION} ({platform_key})...')

        # Oracle OTN 下载需要特定的 headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }

        _urlretrieve_with_headers(
            cfg['url'], tmp_file, headers=headers,
            callback=lambda d, t: progress_callback('downloading', int(d / max(t, 1) * 100), 100, f'下载中... {int(d / max(t, 1) * 100)}%') if progress_callback else None
        )

        if progress_callback:
            progress_callback('extracting', 60, 100, '正在解压...')

        # 解压
        if cfg['ext'] == '.zip':
            with zipfile.ZipFile(tmp_file, 'r') as zf:
                # Oracle Instant Client ZIP 通常有顶层目录 instantclient_*
                top_dir = None
                for name in zf.namelist():
                    if name and '/' in name:
                        top_dir = name.split('/')[0]
                        break
                    elif name and not name.endswith('/'):
                        # 没有顶层目录，文件直接在根目录
                        top_dir = ''
                        break

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
        else:
            # tar.gz / tar.xz
            import tarfile
            with tarfile.open(tmp_file, 'r:*') as tf:
                tf.extractall(install_dir)

        # 清理临时文件
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

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
                progress_callback('done', 100, 100, f'Oracle Instant Client {VERSION} 安装成功')
        else:
            result['error'] = '下载完成但未能找到必要的库文件，请检查下载内容'

    except RuntimeError as e:
        result['error'] = str(e)
    except Exception as e:
        result['error'] = f'下载失败: {e}'

    # 清理临时文件（失败时）
    if tmp_dir and os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return result


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
    install_dir = base / 'oracle_client' / platform_key

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
    parser.add_argument('--platform', choices=['windows_x64', 'linux_x64', 'darwin_x64', 'darwin_arm64'],
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
