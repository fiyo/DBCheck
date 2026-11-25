# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""品牌常量（L1 展示层）。

品牌升级原则：对外展示名 RaccoonX / 浣巡；内部代号 dbcheck 保持不变。
本模块仅承载展示层品牌名，不承载任何内部标识符（包名、命令、路径、变量等）。
"""

# 对外英文产品名（展示层，全语言一致，不参与翻译）
PRODUCT_NAME_EN = "RaccoonX"
# 对外中文产品名
PRODUCT_NAME_ZH = "浣巡"
# 完整品牌名（中英文组合）
PRODUCT_FULL_NAME = "浣巡（RaccoonX）智能巡检平台"
# 完整中文名（登录页品牌区展示；简称仍为「浣巡」，二者并行不冲突）
PRODUCT_FULL_NAME_ZH = "小浣熊智能数据库巡检平台"
# 英文标语（与标题搭配：RaccoonX · Database Inspector）
PRODUCT_TAGLINE_EN = "Database Inspector"
