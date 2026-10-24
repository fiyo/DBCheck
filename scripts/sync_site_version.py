# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>

"""官网版本号一致性校验 / 同步脚本。

背景
----
官网页面上**可见**的版本号（徽标、Docker 命令里的镜像 tag、下载中心链接与文案）
已改为运行时从 ``version.json`` 读取——发版只需改 ``version.json`` 一处。

但两处无法动态化，仍需发版时同步：
  1. ``<meta name="description">`` / ``<meta property="og:description">``
     —— 爬虫与微信/社交分享卡片不执行 JS，只能是静态文本；
  2. 站内静态兜底值（``#verBadge`` 的初始文本、JS 注释里的示例）。

本脚本以主仓 ``modules/config/version.json`` 为唯一事实来源，检查并（可选）同步
官网仓的 ``version.json`` 与 ``index.html``。

用法
----
    python scripts/sync_site_version.py                 # 仅校验，差异时打印建议
    python scripts/sync_site_version.py --fix           # 校验并就地修正
    python scripts/sync_site_version.py --site D:/DBCheck-website
    python scripts/sync_site_version.py --site D:/DBCheck-website --fix
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_SITE = r'D:\DBCheck-website'
# 官网仓里这些位置无法运行时动态化，必须与主仓版本号保持一致
STATIC_PATTERNS = [
    ('meta description', re.compile(r'(<meta content="RaccoonX )v?\d+\.\d+\.\d+\.\d+( 是)')),
    ('og description', re.compile(r'(<meta content="开源跨平台数据库智能巡检与实时监控平台 )v?\d+\.\d+\.\d+\.\d+(，)')),
    ('verBadge 兜底', re.compile(r'(<span id="verBadge">)v?\d+\.\d+\.\d+\.\d+(</span>)')),
    ('动态占位初始值', re.compile(r'(data-ver-(tag|badge)>)v?\d+\.\d+\.\d+\.\d+')),
]


def repo_version():
    """主仓版本号（唯一事实来源）"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'modules', 'config', 'version.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f).get('version', '')


def check(site_dir, fix=False):
    ver = repo_version()
    if not ver:
        print('ERROR: 主仓版本号读取失败')
        return 1
    bare = ver.lstrip('v')
    print('主仓版本号: %s' % ver)

    changed = []
    # ── 官网 version.json ──
    vjson_path = os.path.join(site_dir, 'version.json')
    if not os.path.exists(vjson_path):
        print('ERROR: 找不到 %s' % vjson_path)
        return 1
    with open(vjson_path, encoding='utf-8') as f:
        vjson = json.load(f)
    if vjson.get('version') != ver:
        changed.append('version.json: %s -> %s' % (vjson.get('version'), ver))
        if fix:
            vjson['version'] = ver
            with open(vjson_path, 'w', encoding='utf-8', newline='') as f:
                f.write(json.dumps(vjson, ensure_ascii=False, indent=2) + '\n')
    else:
        print('  OK  version.json = %s' % ver)

    # ── 官网 index.html 静态位置 ──
    html_path = os.path.join(site_dir, 'index.html')
    if not os.path.exists(html_path):
        print('ERROR: 找不到 %s' % html_path)
        return 1
    with open(html_path, encoding='utf-8') as f:
        html = f.read()
    for label, pat in STATIC_PATTERNS:
        hits = list(pat.finditer(html))
        if not hits:
            changed.append('index.html: 未找到「%s」占位（结构可能已变，需人工核对）' % label)
            continue
        for m in hits:
            old = m.group(0)
            if label == '动态占位初始值':
                # data-ver-tag 是 Docker tag（不带 v），data-ver-badge 是文案（带 v）
                new = m.group(1) + (bare if m.group(2) == 'tag' else ver)
            else:
                new = m.group(1) + ver + m.group(2)
            if old != new:
                changed.append('index.html[%s]: %s -> %s' % (label, old, new))
                if fix:
                    html = html.replace(old, new)

    if fix and changed:
        with open(html_path, 'w', encoding='utf-8', newline='') as f:
            f.write(html)

    if changed:
        print('\n发现 %d 处差异：' % len(changed))
        for c in changed:
            print('  - %s' % c)
        if not fix:
            print('\n加上 --fix 可自动修正。')
        return 1 if not fix else 0
    print('\n全部一致，无需改动。')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='官网版本号一致性校验 / 同步')
    ap.add_argument('--site', default=DEFAULT_SITE, help='官网仓目录（默认 %s）' % DEFAULT_SITE)
    ap.add_argument('--fix', action='store_true', help='就地修正差异')
    args = ap.parse_args()
    sys.exit(check(args.site, args.fix))
