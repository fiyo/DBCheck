# -*- coding: utf-8 -*-
"""DBCheck 测试套件共享固件（conftest）。

- 把仓库根目录加入 sys.path，使测试无论以何种 cwd 运行都能
  `import inspection_engine` / `import main_tidb`。
- 提供从 data/inspection.db 加载真实查询的固件；该 db 被 .gitignore 忽略、
  不入库，因此依赖它的集成测试在缺 db 时自动 skip（单测不受影响）。
"""
import os
import sys
from pathlib import Path

import pytest
import sqlite3

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

INSPECTION_DB = REPO_ROOT / "data" / "inspection.db"

TARGET_KEYS = (
    "db_size",
    "table_size",
    "stale_tables",
    "table_fragmentation",
    "db_privileges",
    "index_stats",
    "unused_indexes",
)


def _load_sql_dict(db_type):
    """从 inspection.db 读取某 db_type 的 {query_key: sql} 字典（换行压成空格）。"""
    conn = sqlite3.connect(str(INSPECTION_DB))
    try:
        rows = conn.execute(
            """
            SELECT q.query_key, q.query_sql
            FROM inspection_query q
            JOIN inspection_chapter ch ON q.chapter_id = ch.id
            JOIN inspection_template t ON ch.template_id = t.id
            WHERE t.db_type = ? AND q.enabled = 1
            """,
            (db_type,),
        ).fetchall()
    finally:
        conn.close()
    return {k: v.replace("\n", " ").replace("\r", " ").strip() for k, v in rows if v}


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def inspection_db_path():
    if not INSPECTION_DB.exists():
        pytest.skip(
            "inspection.db 不存在（被 .gitignore 忽略，未入库），跳过集成测试: %s" % INSPECTION_DB
        )
    return INSPECTION_DB


@pytest.fixture
def mysql_sql_dict(inspection_db_path):
    sd = _load_sql_dict("mysql")
    if not sd:
        pytest.skip("mysql 查询为空，跳过集成测试")
    return sd


@pytest.fixture
def mariadb_sql_dict(inspection_db_path):
    sd = _load_sql_dict("mariadb")
    if not sd:
        pytest.skip("mariadb 查询为空，跳过集成测试")
    return sd


@pytest.fixture
def tidb_sql_dict(inspection_db_path):
    sd = _load_sql_dict("tidb")
    if not sd:
        pytest.skip("tidb 查询为空，跳过集成测试")
    return sd
