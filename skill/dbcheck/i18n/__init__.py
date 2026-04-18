# -*- coding: utf-8 -*-
"""
DBCheck i18n 模块
=================
提供多语言支持，所有面向用户的字符串均通过 t(key) 获取。
默认语言从 dbc_config.json 读取，也可通过启动参数 --lang 覆盖。

用法：
    from i18n import t, set_lang, get_lang
    print(t("cli.main_menu_title"))
"""

import os
import json

from .zh import ZI
from .en import EN

# ── 配置路径 ────────────────────────────────────────────────────────────────
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

    :param lang:     'zh' 或 'en'
    :param persist:  是否写入 dbc_config.json（Web UI 保存时为 True，
                     CLI --lang 参数覆盖时为 False，不影响配置文件）
    """
    lang = 'en' if str(lang).lower().startswith('en') else 'zh'
    if persist:
        cfg = _load_config()
        cfg['language'] = lang
        _save_config(cfg)
    else:
        # CLI 模式：全局变量覆盖，不写文件
        global _override_lang
        _override_lang = lang


# ── 翻译查询 ────────────────────────────────────────────────────────────────

def t(key, lang=None):
    """
    根据 key 返回翻译后的字符串。

    :param key:  翻译 key，如 "cli.main_menu_title"，"report.health_excellent"
    :param lang: 指定语言（可选，默认从 get_lang() 获取）
    :return:     翻译字符串，未找到时返回原 key
    """
    if lang is None:
        lang = get_lang()

    data = EN if lang.startswith('en') else ZI
    val = data.get(key)

    if val is not None:
        return str(val)

    # 回退到中文
    val = ZI.get(key)
    if val is not None:
        return str(val)

    return key


# ── 便捷函数（供 Web UI 使用）───────────────────────────────────────────────

def get_all_translations(lang=None):
    """返回指定语言的全部翻译字典"""
    if lang is None:
        lang = get_lang()
    return EN if lang.startswith('en') else ZI


def get_language_display(lang=None):
    """返回语言对应的显示名称"""
    if lang is None:
        lang = get_lang()
    return "English" if lang.startswith('en') else "中文"
