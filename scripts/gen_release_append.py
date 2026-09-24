# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
"""发版时从 CHANGELOG.md 动态生成 GitHub Release 追加说明。

用法: python3 gen_release_append.py <tag>   # 如 v26.9.25.0

输出（stdout）:
  --- + ✨ 主要更新（CHANGELOG 中该版本段落） + 🐳 Docker 镜像（tag 动态注入）
  + ⚠️ 安装注意事项 + 📦 二进制包说明

背景: 此前 .github/release_append.md 是静态文件，v26.9.20.0 生成后从未更新，
导致每次 GitHub Release 的「主要更新」与 Docker tag 都是旧内容（Issue 反馈 2026-09-24）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INSTALL_NOTES = """## ⚠️ 安装注意事项
- **macOS**：当前分发包**未做 Apple 公证（二进制未签名）**。首次打开若被 Gatekeeper 拦截，请右键点击 App / 可执行文件 →「打开」并在弹窗中确认即可运行；如需彻底消除提示，后续可接入 Apple 开发者证书做正式公证。
- **Windows**：解压后双击 `start.bat` 启动，浏览器访问 http://localhost:5003 。
- **Linux**：客户端安装包暂未发布，当前仅提供 Windows / macOS 安装包；Linux 用户请直接使用上方 Docker 镜像。

## 📦 二进制包
下方 Assets 提供 Windows / macOS 客户端压缩包；需要源码编译的请下载 `Source code`。
"""


def extract_changelog_section(tag: str) -> str:
    """从 CHANGELOG.md 提取 `## <tag>` 到下一个 `## ` 之间的段落。"""
    text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().split(' ')[0:2] == ['##', tag] or ln.strip() == f'## {tag}':
            start = i + 1
            break
    if start is None:
        return ''
    body = []
    for ln in lines[start:]:
        if ln.startswith('## '):
            break
        body.append(ln)
    while body and not body[-1].strip():
        body.pop()
    return '\n'.join(body).strip()


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else ''
    if not tag:
        print('usage: gen_release_append.py <tag>', file=sys.stderr)
        return 2
    ver = tag.lstrip('v')
    section = extract_changelog_section(tag)
    out = ['---', '']
    out.append('## ✨ 主要更新')
    if section:
        out.append(section)
    else:
        out.append(f'> 本版本（{tag}）的更新说明缺失，请参阅仓库 [CHANGELOG.md](https://github.com/fiyo/DBCheck/blob/main/CHANGELOG.md)。')
    out.append('')
    out.append('## 🐳 Docker 镜像')
    out.append('')
    out.append('推荐使用 Docker Hub（国内可用）：')
    out.append('')
    out.append('```bash')
    out.append(f'docker pull jackge12345/dbcheck:{ver}')
    out.append('docker pull jackge12345/dbcheck:latest')
    out.append('```')
    out.append('')
    out.append('或 GitHub Container Registry：')
    out.append('')
    out.append('```bash')
    out.append(f'docker pull ghcr.io/fiyo/dbcheck:{ver}')
    out.append('docker pull ghcr.io/fiyo/dbcheck:latest')
    out.append('```')
    out.append('')
    out.append('运行示例：')
    out.append('')
    out.append('```bash')
    out.append(f'docker run -d -p 5003:5003 --name raccoonx jackge12345/dbcheck:{ver}')
    out.append('```')
    out.append('')
    out.append('> 镜像同时支持 `linux/amd64` 与 `linux/arm64`（ARM64 信创主机）。')
    out.append('')
    out.append(INSTALL_NOTES.rstrip())
    out.append('')
    print('\n'.join(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
