# -*- coding: utf-8 -*-
"""
DBCheck i18n 模块
=================
提供多语言支持，所有面向用户的字符串均通过 t(key) 获取。
默认语言从 dbc_config.json 读取，也可通过启动参数 --lang 覆盖。

支持语言：zh(简体中文) / en(English) / zh_tw(繁體中文) / ja(日本語) / ko(한국어) / es(Español) / fr(Français) / de(Deutsch) / ru(Русский)

用法：
    from i18n import t, set_lang, get_lang
    print(t("cli.main_menu_title"))
"""

import os
import json

from .zh import ZI
from .en import EN
from .zh_tw import ZH_TW
from .ja import JA
from .ko import KO
from .es import ES
from .fr import FR
from .de import DE
from .ru import RU

# ── 语言注册表（新增语言只需在此登记）────────────────────────────────────
_LANGS = {
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

# 语言显示名（下拉框使用）
_LANG_DISPLAY = {
    'zh': '中文',
    'en': 'English',
    'zh_tw': '繁體中文',
    'ja': '日本語',
    'ko': '한국어',
    'es': 'Español',
    'fr': 'Français',
    'de': 'Deutsch',
    'ru': 'Русский',
}

# 常见别名 -> 标准代码
_LANG_ALIASES = {
    'zh': 'zh', 'chinese': 'zh', 'zh-cn': 'zh', 'cn': 'zh', 'zho': 'zh',
    'en': 'en', 'english': 'en', 'en-us': 'en', 'eng': 'en',
    'zh_tw': 'zh_tw', 'zh-tw': 'zh_tw', 'zh-hant': 'zh_tw', 'traditional': 'zh_tw', 'cht': 'zh_tw',
    'ja': 'ja', 'jp': 'ja', 'japanese': 'ja', '日本语': 'ja', 'jpn': 'ja',
    'ko': 'ko', 'kr': 'ko', 'korean': 'ko', '朝鲜语': 'ko', 'kor': 'ko',
    'es': 'es', 'spanish': 'es', 'español': 'es', 'spa': 'es',
    'fr': 'fr', 'french': 'fr', 'français': 'fr', 'fra': 'fr',
    'de': 'de', 'german': 'de', 'deutsch': 'de', 'deu': 'de', 'ger': 'de',
    'ru': 'ru', 'russian': 'ru', 'русский': 'ru', 'rus': 'ru',
}

# ── 配置路径 ───────────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(os.path.dirname(_SCRIPT_DIR), 'dbc_config.json')

# 全局语言覆盖（CLI --lang 参数临时设置，不写文件）
_override_lang = None


# ── 语言配置读写 ────────────────────────────────────────────────────────────

def _load_config():
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_config(cfg):
    with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)



def _normalize_lang(lang):
    """将用户输入（含别名）规范为注册表中的标准代码。"""
    if lang is None:
        return None
    lang = str(lang).strip().lower()
    return _LANG_ALIASES.get(lang, lang)


def get_lang():
    """
    获取当前语言。
    优先级：CLI --lang 参数 > dbc_config.json 的 language 字段 > 'zh'
    """
    if _override_lang:
        return _override_lang
    return _load_config().get('language', 'zh')


def set_lang(lang, persist=True):
    """
    设置当前语言。

    :param lang:    'zh' / 'en' / 'zh_tw' / 'ja' / 'ko' / 'es' / 'fr' / 'de' / 'ru'（或别名）
    :param persist:  是否写入 dbc_config.json（Web UI 保存时为 True，
                    CLI --lang 参数覆盖时为 False，不影响配置文件）
    """
    lang = _normalize_lang(lang) or 'zh'
    if persist:
        cfg = _load_config()
        cfg['language'] = lang
        _save_config(cfg)
    else:
        # CLI 模式：全局变量覆盖，不写文件
        global _override_lang
        _override_lang = lang


# ── 翻译查询 ────────────────────────────────────────────────────────────────

def t(key, lang=None, default=None):
    """
    根据 key 返回翻译后的字符串。

    :param key:     翻译 key，如 "cli.main_menu_title"，"report.health_excellent"
    :param lang:    指定语言（可选，默认从 get_lang() 获取）
    :param default: 未找到时返回的默认值。可以是一个翻译 key（会递归查找），也可以是纯文本。
    :return:        翻译字符串，未找到时返回 default 的翻译结果或原 key
    """
    if lang is None:
        lang = get_lang()
    lang = _normalize_lang(lang)

    data = _LANGS.get(lang, ZI)
    val = data.get(key)

    if val is not None:
        return str(val)

    # 回退到简体中文
    val = ZI.get(key)
    if val is not None:
        return str(val)

    # default 可能也是一个翻译 key，先尝试翻译它
    if default is not None:
        default_val = _LANGS.get(lang, ZI).get(default)
        if default_val is not None:
            return str(default_val)
        default_val = ZI.get(default)
        if default_val is not None:
            return str(default_val)
        return str(default)

    return key


# ── 便捷函数（供 Web UI 使用）───────────────────────────────────────────────

def get_all_translations(lang=None):
    """返回指定语言的全部翻译字典"""
    if lang is None:
        lang = get_lang()
    lang = _normalize_lang(lang)
    return _LANGS.get(lang, ZI)


def get_language_display(lang=None):
    """返回语言对应的显示名称"""
    if lang is None:
        lang = get_lang()
    lang = _normalize_lang(lang)
    return _LANG_DISPLAY.get(lang, '中文')
