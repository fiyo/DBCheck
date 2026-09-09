# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""国产库专家：针对 DM8/HGDB/Kingbase/OceanBase/TiDB 等国产库特有风险。

阶段 C 专家域扩展（规划文档 4.4 C）。triggers 命中或目标库型为国产库时介入。
"""

from __future__ import annotations

from typing import List

from ..context import SharedContext, Finding
from ..specialist import Specialist

# 受支持的国产库类型标识（小写）
_NATIVE_TYPES = {
    "dm", "dm8", "hgdb", "kingbase", "kingbasees",
    "oceanbase", "tidb", "yashandb", "uxdb", "gbase",
}


class NativeDbExpert(Specialist):
    id = "native_db"
    name = "国产库专家"
    description = "针对 DM8/HGDB/Kingbase/OceanBase/TiDB 等国产库的特有参数、兼容性与运维风险给出建议。"
    tags = ["native_db", "dm8", "hgdb", "kingbase", "oceanbase", "tidb"]
    domain = "rootcause"
    deps = ["rootcause_expert"]
    triggers = ["dm8", "hgdb", "kingbase", "oceanbase", "tidb", "native_db", "yashandb"]

    @staticmethod
    def _db_type_of(ctx: SharedContext) -> str:
        meta = ctx.inputs.get("target_meta") or {}
        inst = ctx.inputs.get("target_instance") or {}
        return (meta.get("db_type") or inst.get("db_type") or "").lower()

    def analyze(self, ctx: SharedContext) -> List[Finding]:
        db_type = self._db_type_of(ctx)
        is_native = db_type in _NATIVE_TYPES
        rel = [f for f in ctx.findings
               if set(f.tags or []) & {"dm8", "hgdb", "kingbase", "oceanbase",
                                       "tidb", "native_db", "yashandb"}]
        if not (is_native or rel):
            return [
                Finding(
                    source=self.id,
                    category="risk",
                    severity="info",
                    title="国产库专家待命中",
                    detail=f"目标库型 {db_type or '未知'} 非国产库，无国产库特有风险。",
                    suggestion="当目标为 DM8/HGDB/Kingbase/OceanBase/TiDB 等时，本能力将自动介入。",
                    tags=["native_db"],
                )
            ]
        out: List[Finding] = []
        if is_native:
            out.append(
                Finding(
                    source=self.id,
                    category="plan",
                    severity="warning",
                    title=f"国产库 {db_type} 运维提示",
                    detail="国产库在参数默认值、大小写敏感、兼容模式、备份恢复机制上"
                           "与 MySQL/Oracle 存在差异。",
                    suggestion="核对国产库官方最佳实践（字符集/归档模式/兼容模式/系统表差异），"
                              "变更前在备库验证。",
                    tags=["native_db", db_type],
                )
            )
        for f in rel:
            out.append(
                Finding(
                    source=self.id,
                    category="plan",
                    severity=f.severity,
                    title=f"国产库视角：{f.title}",
                    detail=f.detail,
                    suggestion="结合国产库特性复核该问题（兼容模式/系统表差异），"
                              "避免直接套用通用库经验。",
                    tags=["native_db", "dm8"],
                )
            )
        return out
