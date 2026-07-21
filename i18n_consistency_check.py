# -*- coding: utf-8 -*-
"""
i18n 一致性检查脚本（验证用，第一阶段）

校验目标：
  1. 结构性对齐：
       · dict(ZI) 拷贝语言（zh_tw/ja/ko/es/fr/de/ru）key 集合必须与 ZI 完全一致（==）。
       · en 为完整英文字典（非 dict(ZI) 拷贝，且本阶段按任务要求“不要动它们，只追加
         menu.*”），其相对 ZI 的 key 漂移（缺失/多余）作为【警告】输出，不报错退出。
  2. menu.* 必须已译：
       · en/es/fr/de/ru（拉丁系）：值必须 != ZI 中文源（已真正翻译）。
       · ja/ko/zh_tw（CJK 目标语言，译文可能与中文同形）：仅校验 key 存在。
         —— 任一拉丁系 menu.* 未译（== 中文源）即视为错误并退出非 0。
  3. CJK 未译仅作警告：对“值含 CJK 且仍 == ZI 值”的非 menu.* key，输出警告，
     但不报错退出（ja/ko/es/fr/de/ru 此刻仍有大量未译，属第二阶段 T4 工作）。

退出码：0 = 通过（仅警告）；1 = 存在 menu.* 未译等错误。
"""

from i18n import ZI, EN, ZH_TW, JA, KO, ES, FR, DE, RU

LANG_DICTS = {
    'en': EN,
    'zh_tw': ZH_TW,
    'ja': JA,
    'ko': KO,
    'es': ES,
    'fr': FR,
    'de': DE,
    'ru': RU,
}

# 这些语言基于 dict(ZI) 拷贝，要求 key 集合与 ZI 严格一致
STRICT_PARITY_LANGS = {'zh_tw', 'ja', 'ko', 'es', 'fr', 'de', 'ru'}
# 这些 CJK 目标语言的 menu.* 译文可能与中文同形，仅校验 key 存在
CJK_TARGET_LANGS = {'ja', 'ko', 'zh_tw'}

# CJK 统一表意文字范围（用于判定“值是否仍为中文”）
_CJK_START, _CJK_END = 0x4E00, 0x9FFF


def has_cjk(s):
    """判断字符串是否含有 CJK 统一表意文字。"""
    if not isinstance(s, str):
        return False
    return any(_CJK_START <= ord(ch) <= _CJK_END for ch in s)


def main():
    errors = []
    warnings = []

    zi_keys = set(ZI.keys())
    menu_keys = [k for k in ZI if k.startswith('menu.')]

    print("=" * 60)
    print("i18n 一致性检查（第一阶段：结构性对齐 + menu.* 翻译）")
    print("=" * 60)
    print("ZI key 总数: %d | menu.* key 数: %d" % (len(zi_keys), len(menu_keys)))

    # ── 1) key 集合 parity ─────────────────────────────────────
    print("\n[1] key 集合 parity 检查：")
    for lang, d in LANG_DICTS.items():
        dk = set(d.keys())
        if lang in STRICT_PARITY_LANGS:
            if dk == zi_keys:
                print("  [%-6s] OK  (%d keys)" % (lang, len(dk)))
            else:
                missing = zi_keys - dk
                extra = dk - zi_keys
                errors.append("[%s] key 集合与 ZI 不一致：缺失 %d，多余 %d"
                              % (lang, len(missing), len(extra)))
                if missing:
                    errors.append("        缺失示例: %s" % sorted(list(missing))[:10])
                print("  [%-6s] FAIL (缺失 %d / 多余 %d)" % (lang, len(missing), len(extra)))
        else:
            # en：完整英文字典，非 dict(ZI) 拷贝，其 key 漂移作为警告
            drift_m = zi_keys - dk
            drift_e = dk - zi_keys
            if drift_m or drift_e:
                warnings.append("[%s] key 漂移(完整英文字典非 dict(ZI) 拷贝，超出本阶段范围): "
                                "缺失 %d / 多余 %d" % (lang, len(drift_m), len(drift_e)))
                print("  [%-6s] 完整英文字典：%d keys；相对 ZI 漂移 缺失 %d / 多余 %d（警告，不报错）"
                      % (lang, len(dk), len(drift_m), len(drift_e)))
            else:
                print("  [%-6s] OK  (%d keys)" % (lang, len(dk)))

    # ── 2) menu.* 必须已译 ────────────────────────────────────
    print("\n[2] menu.* 翻译检查（必须已译，否则报错）：")
    for lang, d in LANG_DICTS.items():
        untranslated = []
        for mk in menu_keys:
            if mk not in d:
                errors.append("[%s] menu.* 缺少 key: %s" % (lang, mk))
                untranslated.append(mk)
                continue
            # CJK 目标语言（ja/ko/zh_tw）译文可能与中文同形，仅校验 key 存在
            if lang in CJK_TARGET_LANGS:
                continue
            if d[mk] == ZI[mk]:
                errors.append("[%s] menu.* 未翻译(==中文源): %s = %r"
                              % (lang, mk, d[mk]))
                untranslated.append(mk)
        if untranslated:
            print("  [%-6s] FAIL: %d 个 menu.* 未译" % (lang, len(untranslated)))
        else:
            print("  [%-6s] OK  (全部 %d 个 menu.* 已译)" % (lang, len(menu_keys)))

    # ── 3) CJK 未译仅作警告 ──────────────────────────────────
    print("\n[3] 非 menu.* 的 CJK 未译警告（值含中文且等于中文源；不报错）：")
    for lang, d in LANG_DICTS.items():
        cnt = 0
        samples = []
        for k in ZI:
            if k.startswith('menu.'):
                continue
            v = d.get(k)
            if v is None:
                continue
            if has_cjk(v) and v == ZI[k]:
                cnt += 1
                if len(samples) < 25:
                    samples.append(k)
        if cnt:
            warnings.append("[%s] CJK 未译: %d 条" % (lang, cnt))
        print("  [%-6s] CJK 未译警告: %d 条" % (lang, cnt))
        for s in samples:
            print("           - %s" % s)
        if cnt > len(samples):
            print("           … 其余 %d 条略" % (cnt - len(samples)))

    # ── 汇总 ─────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if errors:
        print("❌ 检查未通过，存在 %d 个错误：" % len(errors))
        for e in errors:
            print("   " + e)
        raise SystemExit(1)
    print("✅ 全部检查通过：")
    print("   · dict(ZI) 语言 key 集合 parity 一致（%d keys）" % len(zi_keys))
    print("   · %d 个 menu.* 在所有语言均已翻译/覆盖" % len(menu_keys))
    if warnings:
        wtotal = sum(int(w.split(':')[1].split()[0]) for w in warnings if 'CJK' in w)
        print("   · 共 %d 条语言级警告（en key 漂移 + CJK 未译；属第二阶段 T4 或既有漂移，不报错）"
              % len(warnings))
    print("=" * 60)


if __name__ == '__main__':
    main()
