# SPDX-License-Identifier: Apache-2.0
"""端到端验证：用真实 HGDB 实例跑 run_target_inspection，检查 context 透出与定向抽取。"""
import os
import sys

sys.path.insert(0, ".")

from modules.pro.instance_manager import get_instance_manager

im = get_instance_manager()
inst = None
for i in im.get_all_instances():
    if "hgdb" in str(i.get("name", "")).lower() or str(i.get("db_type", "")).lower() == "hgdb":
        inst = im.get_instance_decrypted(i.get("id"))
        break

if inst is None:
    print("NO_HGDB_INSTANCE")
    sys.exit(1)

print("INSTANCE", inst.get("name"), inst.get("db_type"), inst.get("host"), inst.get("port"))

from modules.intelligence.inspection_runner import run_target_inspection

res = run_target_inspection(inst.get("db_type") or "hgdb", inst)
print("OK", res.get("ok"), "| error:", res.get("error"))
ctx = res.get("context")
print("CONTEXT_TYPE", type(ctx).__name__, "| keys:", len(ctx) if isinstance(ctx, dict) else 0)
if isinstance(ctx, dict):
    print("CONTEXT_KEYS", sorted(ctx.keys())[:30])

    # 定向抽取回归：连接主题应抽到 hgdb_conn_summary / hgdb_connections
    from modules.intelligence.planner import detect_focus_topic
    from modules.intelligence.specialists.focus_analyst import (
        _extract_context_section, FOCUS_CONTEXT_PATTERNS,
    )

    topic = detect_focus_topic("巡检一下数据库连接数")
    print("TOPIC", topic["id"] if topic else None)
    lines = _extract_context_section(ctx, FOCUS_CONTEXT_PATTERNS[topic["id"]])
    print("CONN_LINES", len(lines))
    for ln in lines[:8]:
        print("  ", ln)
    if not lines:
        print("EXTRACT_EMPTY")
    else:
        print("E2E_PASS")
