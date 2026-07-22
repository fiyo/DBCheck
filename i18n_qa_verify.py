# -*- coding: utf-8 -*-
"""
i18n_qa_verify.py
=================
DBCheck 多语言「全量对齐」独立验收脚本（T6：前端模板盲区）

背景
----
一致性脚本 i18n_consistency_check.py 已覆盖：
  · 9 语言 key 集合 parity（与 ZI 一致）
  · 拉丁系无「未译」中文（has_cjk(v) and v == ZI[k]）
  · menu.* 已译

但它**没覆盖**的是：前端模板实际引用了哪些 key、这些 key 在每种语言
字典里是否真的存在。运行时 applyI18N() 会把 [data-i18n] 元素的 textContent
设为 i18n(key)；而前端 i18n(key) = I18N[key] !== undefined ? I18N[key] : undefined。
I18N 即 get_all_translations(lang)。若某 key 在某语言字典里缺失，i18n(key)
返回 undefined，applyI18N() 会把界面写成字面量 "undefined" —— 这正是本次验收
要抓的盲区。

本脚本验证 5 项：
  1. key parity 复核（en 多余 key 记为 warning 不 fail）
  2. 拉丁系无残留中文（en/es/fr/de/ru 任一 value 含 CJK 即 FAIL）
  3. 前端模板 key 存在性（核心）：扫描 index.html 与 user_management/admin.html，
     提取所有 data-i18n* 与 i18n('...')/i18n("...") 字面量引用的 key，
     断言每一个都存在于 9 种语言字典（get_all_translations(lang).get(key) 非空）。
     专项：webui.nav_*（侧边栏）与 menu.* 在 9 语言均存在且拉丁系非中文。
  4. 占位符保护：含 {host}/{port}/{elapsed:.1f} 等占位符的 key，译文须原样保留。
  5. menu 渲染模拟：对每个 menu.<code> 模拟 i18n(menu_name) 字典 lookup。

退出码：0 = 通过（仅 warning）；1 = 发现源码级问题（key 缺失 / 占位符破坏 / 残留中文）。

用法：python i18n_qa_verify.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from i18n import ZI, EN, ZH_TW, JA, KO, ES, FR, DE, RU, get_all_translations

# 9 种语言字典（zh 为基准 ZI）
LANG_DICTS = {
    'zh': ZI,
    'en': EN,
    'zh_tw': ZH_TW,
    'ja': JA,
    'ko': KO,
    'es': ES,
    'fr': FR,
    'de': DE,
    'ru': RU,
}
# 除 zh 外的 8 种目标语言
TARGET_LANGS = ['en', 'zh_tw', 'ja', 'ko', 'es', 'fr', 'de', 'ru']
# 拉丁系（译文必须非中文）
LATIN = {'en', 'es', 'fr', 'de', 'ru'}
# CJK 系目标语言（允许同形 kanji）
CJK_TARGET = {'ja', 'ko', 'zh_tw'}

# CJK 统一表意文字范围（任务指定 \u4e00-\u9fff）
_CJK_START, _CJK_END = 0x4E00, 0x9FFF

# 待扫描的前端模板
TEMPLATE_FILES = [
    os.path.join('web_templates', 'index.html'),
    os.path.join('web_templates', 'user_management', 'admin.html'),
]


def has_cjk(s):
    """判断字符串是否含 CJK 统一表意文字。"""
    if not isinstance(s, str):
        return False
    return any(_CJK_START <= ord(ch) <= _CJK_END for ch in s)


def get(lang, key):
    """与前端一致：get_all_translations(lang).get(key)。"""
    return get_all_translations(lang).get(key)


# ─────────────────────────────────────────────────────────────────────────
# 1) key parity 复核
# ─────────────────────────────────────────────────────────────────────────
def check_parity():
    errors, warnings = [], []
    zi_keys = set(ZI.keys())
    print("\n[1] key parity 复核（对照 ZI / zh）")
    print("    ZI key 总数: %d" % len(zi_keys))
    for lang in TARGET_LANGS:
        dk = set(LANG_DICTS[lang].keys())
        if lang == 'en':
            # en 为完整英文字典，允许多余 key；缺失必须 0
            missing = zi_keys - dk
            extra = dk - zi_keys
            if missing:
                errors.append("[parity] en 缺失 %d 个 key（必须 0）：%s"
                              % (len(missing), sorted(list(missing))[:10]))
            if extra:
                warnings.append("[parity] en 多余 %d 个 key（允许，warning）：%s"
                                % (len(extra), sorted(list(extra))[:10]))
            print("    [en   ] 缺失 %d / 多余 %d" % (len(missing), len(extra)))
        else:
            if dk == zi_keys:
                print("    [%-5s] OK  (%d keys)" % (lang, len(dk)))
            else:
                missing = zi_keys - dk
                extra = dk - zi_keys
                errors.append("[parity] %s key 集合与 ZI 不一致：缺失 %d / 多余 %d"
                              % (lang, len(missing), len(extra)))
                if missing:
                    errors.append("        缺失示例: %s" % sorted(list(missing))[:10])
                if extra:
                    errors.append("        多余示例: %s" % sorted(list(extra))[:10])
                print("    [%-5s] FAIL 缺失 %d / 多余 %d" % (lang, len(missing), len(extra)))
    return errors, warnings


# ─────────────────────────────────────────────────────────────────────────
# 2) 拉丁系无残留中文
# ─────────────────────────────────────────────────────────────────────────
def check_latin_cjk():
    errors, warnings = [], []
    print("\n[2] 拉丁系无残留中文（en/es/fr/de/ru，任一 value 含 CJK 即 FAIL）")
    for lang in sorted(LATIN):
        d = LANG_DICTS[lang]
        bad = [k for k, v in d.items() if isinstance(v, str) and has_cjk(v)]
        if bad:
            errors.append("[latin_cjk] %s 含 CJK 的 value 共 %d 条（拉丁系必须 0 残留）"
                          % (lang, len(bad)))
            for k in bad[:15]:
                errors.append("        %s = %r" % (k, d[k]))
            print("    [%-5s] FAIL: %d 条含 CJK" % (lang, len(bad)))
        else:
            print("    [%-5s] OK (0 CJK)" % lang)
    return errors, warnings


# ─────────────────────────────────────────────────────────────────────────
# 3) 前端模板 key 存在性（核心）
# ─────────────────────────────────────────────────────────────────────────
# 提取规则：
#   · data-i18n*="..."           （含 data-i18n / data-i18n-title / -ph / -placeholder）
#   · i18n('...') / i18n("...")   （仅字面量字符串参数，排除 i18n(var) 动态调用）
TPL_ATTR_RE = re.compile(r'data-i18n[a-z_-]*=["\']([^"\']+)["\']')
TPL_FN_RE = re.compile(r'\bi18n\(\s*["\']([^"\']+)["\']\s*\)')


def extract_template_keys():
    keys = set()
    per_file = {}
    for f in TEMPLATE_FILES:
        fp = os.path.join(HERE, f)
        if not os.path.exists(fp):
            print("    [警告] 模板文件不存在: %s" % f)
            per_file[f] = set()
            continue
        with open(fp, 'r', encoding='utf-8') as fh:
            text = fh.read()
        fk = set()
        for pat in (TPL_ATTR_RE, TPL_FN_RE):
            for m in pat.finditer(text):
                k = m.group(1).strip()
                if k:
                    fk.add(k)
        per_file[f] = fk
        keys |= fk
        print("    %s : 提取到 %d 个引用 key" % (f, len(fk)))
    return keys, per_file


def check_template_keys(keys):
    errors, warnings = [], []
    print("\n[3] 前端模板 key 存在性（核心）")
    print("    引用 key 总数(去重): %d" % len(keys))
    missing_by_lang = {l: [] for l in TARGET_LANGS}
    empty_by_lang = {l: [] for l in TARGET_LANGS}
    for key in sorted(keys):
        for lang in TARGET_LANGS:
            val = get(lang, key)
            if val is None:
                missing_by_lang[lang].append(key)
            elif isinstance(val, str) and val.strip() == '':
                empty_by_lang[lang].append(key)
    for lang in TARGET_LANGS:
        if missing_by_lang[lang]:
            errors.append("[template] %s 缺失 %d 个模板引用 key（applyI18N 会显示 undefined）：%s"
                          % (lang, len(missing_by_lang[lang]),
                             missing_by_lang[lang][:20]))
        if empty_by_lang[lang]:
            warnings.append("[template] %s 有 %d 个模板 key 值为空字符串：%s"
                            % (lang, len(empty_by_lang[lang]),
                               empty_by_lang[lang][:20]))
        print("    [%-5s] 缺失 %d / 空值 %d"
              % (lang, len(missing_by_lang[lang]), len(empty_by_lang[lang])))
    return errors, warnings


def check_special_groups():
    """webui.nav_*（侧边栏）与 menu.* 专项：9 语言存在 + 拉丁系非中文。"""
    errors, warnings = [], []
    print("\n[3b] 专项：webui.nav_* 与 menu.* 在 9 语言的存在性与拉丁系非中文")
    nav_keys = [k for k in ZI if k.startswith('webui.nav')]
    menu_keys = [k for k in ZI if k.startswith('menu.')]
    print("    webui.nav_* key 数: %d | menu.* key 数: %d"
          % (len(nav_keys), len(menu_keys)))
    for label, group in (('webui.nav_*', nav_keys), ('menu.*', menu_keys)):
        for lang in TARGET_LANGS:
            d = LANG_DICTS[lang]
            miss = [k for k in group if get(lang, k) is None]
            if miss:
                errors.append("[special:%s] %s 缺失 %d 个 key：%s"
                              % (label, lang, len(miss), miss[:10]))
            if lang in LATIN:
                cjk = [k for k in group
                       if isinstance(get(lang, k), str) and has_cjk(get(lang, k))]
                if cjk:
                    errors.append("[special:%s] %s 有 %d 个 key 仍含中文(拉丁系必须非中文)：%s"
                                  % (label, lang, len(cjk), cjk[:10]))
        print("    %-12s 9 语言存在性 + 拉丁系非中文：%s"
              % (label, "OK" if all(get(l, k) is not None for l in TARGET_LANGS for k in group)
                 and all(not (l in LATIN and isinstance(get(l, k), str) and has_cjk(get(l, k)))
                         for l in TARGET_LANGS for k in group) else "FAIL"))
    return errors, warnings


# ─────────────────────────────────────────────────────────────────────────
# 4) 占位符保护
# ─────────────────────────────────────────────────────────────────────────
PLACEHOLDER_RE = re.compile(r'\{[^}]*\}')


def check_placeholders():
    errors, warnings = [], []
    print("\n[4] 占位符保护（{host}/{port}/{elapsed:.1f} 等须原样保留）")
    ph_keys = [k for k, v in ZI.items()
               if isinstance(v, str) and PLACEHOLDER_RE.search(v)]
    print("    ZI 中含占位符的 key 数: %d" % len(ph_keys))
    broken = []
    for k in ph_keys:
        tokens = set(PLACEHOLDER_RE.findall(ZI[k]))
        for lang in TARGET_LANGS:
            tv = get(lang, k)
            if tv is None:
                continue  # 缺失已在 [3] 报告
            for tok in tokens:
                if tok not in tv:
                    broken.append((lang, k, tok))
    if broken:
        errors.append("[placeholder] 占位符被破坏 %d 处（机翻可能破坏了 {..} 占位符）"
                      % len(broken))
        for lang, k, tok in broken[:20]:
            errors.append("        [%s] %s 缺失占位符 %s" % (lang, k, tok))
        print("    FAIL: %d 处占位符缺失" % len(broken))
    else:
        print("    OK: 所有占位符在 9 语言均原样保留")
    return errors, warnings


# ─────────────────────────────────────────────────────────────────────────
# 5) menu 渲染模拟
# ─────────────────────────────────────────────────────────────────────────
def check_menu_render():
    errors, warnings = [], []
    print("\n[5] menu 渲染模拟（对每个 menu.<code> 模拟 i18n(menu_name) 字典 lookup）")
    menu_keys = [k for k in ZI if k.startswith('menu.')]
    for lang in TARGET_LANGS:
        miss = sum(1 for k in menu_keys if get(lang, k) is None)
        cjk = sum(1 for k in menu_keys
                  if lang in LATIN and isinstance(get(lang, k), str) and has_cjk(get(lang, k)))
        if miss:
            errors.append("[menu_render] %s 有 %d 个 menu.* key 缺失（渲染 undefined）" % (lang, miss))
        if cjk:
            errors.append("[menu_render] %s 有 %d 个 menu.* key 仍含中文(拉丁系)" % (lang, cjk))
    # 抽样展示
    sample = menu_keys[:8]
    print("    抽样（menu 渲染结果）：")
    for k in sample:
        parts = ["%s=%r" % (l, get(l, k)) for l in TARGET_LANGS]
        print("      %-22s -> %s" % (k, " | ".join(parts)))
    return errors, warnings


# ─────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("i18n QA 验收（T6：前端模板盲区）—— i18n_qa_verify.py")
    print("=" * 70)
    print("目标语言(对照 zh): %s" % ", ".join(TARGET_LANGS))
    print("拉丁系: %s" % ", ".join(sorted(LATIN)))

    all_errors, all_warnings = [], []

    e, w = check_parity(); all_errors += e; all_warnings += w
    e, w = check_latin_cjk(); all_errors += e; all_warnings += w

    keys, per_file = extract_template_keys()
    e, w = check_template_keys(keys); all_errors += e; all_warnings += w
    e, w = check_special_groups(); all_errors += e; all_warnings += w

    e, w = check_placeholders(); all_errors += e; all_warnings += w
    e, w = check_menu_render(); all_errors += e; all_warnings += w

    # 去重（保持顺序）
    seen, dedup_errors = set(), []
    for x in all_errors:
        if x not in seen:
            seen.add(x)
            dedup_errors.append(x)

    print("\n" + "=" * 70)
    print("验收汇总")
    print("=" * 70)
    print("模板引用 key 总数(去重): %d" % len(keys))
    print("9 语言全覆盖(每个模板 key 均存在): %s"
          % ("是" if not any(get(l, k) is None for l in TARGET_LANGS for k in keys) else "否"))
    print("拉丁系残留中文: %s"
          % ("0" if not any(isinstance(get(l, k), str) and has_cjk(get(l, k))
                            for l in LATIN for k in ZI) else "存在(见上)"))
    print("错误数: %d | 警告数: %d" % (len(dedup_errors), len(all_warnings)))

    if dedup_errors:
        print("\n❌ 验收未通过，发现 %d 个错误（源码级问题，应路由 Engineer）："
              % len(dedup_errors))
        for x in dedup_errors:
            print("   " + x)
    if all_warnings:
        print("\n⚠ 警告（不阻断，记录供参考）：")
        for x in all_warnings:
            print("   " + x)
    if not dedup_errors:
        print("\n✅ 全部验收通过（仅警告则不计失败）。")
    print("=" * 70)

    raise SystemExit(1 if dedup_errors else 0)


if __name__ == '__main__':
    main()
