# -*- coding: utf-8 -*-
"""
OceanBase 树形导航 2013 直连诊断脚本（仅本地排查用，不进产品、不影响 web_ui）

用途：
  OceanBase 树形导航点击节点仍报
  (2013, 'Lost connection to MySQL server during query')，
  已确认不是旧进程（代码已改为 INFORMATION_SCHEMA 优先、SHOW 兜底，但仍 2013）。
  本脚本用与 web_ui 树形分支完全相同的连接参数，逐条试查询，
  坐实到底哪条查询触发 2013。

用法：
  python diag_oceanbase.py <ds_id>
  python diag_oceanbase.py            # 不传 ds_id 则自动挑第一个 db_type=='oceanbase' 的实例

获取 ds_id 的方法：
  在 web_ui 左侧树形导航点击 OceanBase 节点，观察浏览器/后端网络请求路径：
      /api/pro/datasources/<ds_id>/databases
  其中的 <ds_id> 即为实例 ID。

依赖：仅 pymysql 和项目内 pro 模块，不引入额外依赖。
"""

import os
import sys
import argparse
import traceback

# 确保脚本所在目录（DBCheck 根目录）在 sys.path 中，便于 `from pro import ...`
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)


def _resolve_ds_id(arg_ds_id):
    """解析 ds_id：优先用命令行参数，否则自动挑第一个 oceanbase 实例。

    枚举 API 取自 instance_manager（get_instance_manager().get_all_instances）。
    """
    from pro import get_instance_manager
    mgr = get_instance_manager()
    if arg_ds_id:
        return arg_ds_id
    # 枚举所有实例（仅用于筛选 oceanbase 类型拿 id，密码脱敏与否不影响）
    all_insts = mgr.get_all_instances(mask_password=False)
    ob_insts = [i for i in all_insts if (i.get('db_type') or '').lower() == 'oceanbase']
    if ob_insts:
        return ob_insts[0].get('id')
    return None


def _main():
    parser = argparse.ArgumentParser(
        description='OceanBase 树形 2013 直连诊断脚本（仅本地排查，不进产品）'
    )
    parser.add_argument(
        'ds_id', nargs='?', default=None,
        help='OceanBase 数据源实例 ID；不传则自动挑第一个 oceanbase 实例。'
             '可在 web_ui 点节点时，从网络请求路径 '
             '/api/pro/datasources/<ds_id>/databases 取得 <ds_id>。'
    )
    args = parser.parse_args()

    from pro import get_instance_manager
    mgr = get_instance_manager()

    ds_id = _resolve_ds_id(args.ds_id)
    if not ds_id:
        print("[DIAG] 未找到任何 db_type=='oceanbase' 的数据源实例。")
        print("[DIAG] 获取 ds_id 的方法：在 web_ui 左侧树形导航点击 OceanBase 节点，")
        print("[DIAG] 观察浏览器开发者工具 / 后端的网络请求路径：")
        print("[DIAG]     /api/pro/datasources/<ds_id>/databases")
        print("[DIAG] 其中的 <ds_id> 即为实例 ID。然后运行：")
        print("[DIAG]     python diag_oceanbase.py <ds_id>")
        return

    inst = mgr.get_instance_decrypted(ds_id)
    if not inst:
        print(f"[DIAG] 数据源不存在: {ds_id}")
        return

    db_type = (inst.get('db_type') or '').lower()
    if db_type != 'oceanbase':
        print(f"[DIAG] ds_id={ds_id} 的 db_type={db_type!r}，不是 oceanbase，"
              f"本脚本仅用于 OceanBase 排查。")
        return

    host = inst.get('host', '')
    port = int(inst.get('port', 2881) or 2881)
    user = inst.get('user', '')
    pwd = inst.get('password') or ''
    # 以下构造必须与 web_ui 树形分支一致
    _ob_tenant = inst.get('tenant', '') or ''
    _ob_user = user + '@' + _ob_tenant if _ob_tenant else user

    import pymysql
    print(f"[DIAG] pymysql 版本: {pymysql.__version__}")
    print(f"[DIAG] 解析出的连接参数: host={host} port={port} "
          f"user={_ob_user} tenant={_ob_tenant!r}")

    # 同一连接上下文：每次查询新建连接，参数同上
    def _connect(database=None):
        if database is not None:
            return pymysql.connect(
                host, port, user=_ob_user, password=pwd,
                database=database, connect_timeout=10, charset='utf8mb4'
            )
        return pymysql.connect(
            host, port, user=_ob_user, password=pwd,
            connect_timeout=10, charset='utf8mb4'
        )

    def _try(label, sql, params=None, database=None):
        conn = None
        try:
            conn = _connect(database=database)
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            print(f"  [OK] {label} -> {len(rows)} 行")
            return rows
        except Exception as e:
            print(f"  [FAIL] {label} -> {type(e).__name__}: {e}")
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    db_default = inst.get('database') or 'sys'
    print(f"[DIAG] 默认 database（用于按库查询）: {db_default!r}")
    print("[DIAG] 逐条试查询（每次新建连接，参数与 web_ui 树形分支一致）：")

    # 1
    _try("SELECT VERSION()", "SELECT VERSION()")
    # 2
    _try("SELECT 1", "SELECT 1")
    # 3 INFORMATION_SCHEMA.SCHEMATA（不指定 database）
    _try(
        "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME",
        "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME",
    )
    # 4 SHOW DATABASES（不指定 database）
    _try("SHOW DATABASES", "SHOW DATABASES")
    # 5 INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=%s（指定 database）
    _try(
        "SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=%s",
        "SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=%s",
        params=(db_default,),
        database=db_default,
    )
    # 6 SHOW FULL TABLES FROM `db`（db 反引号转义）
    _db_safe = db_default.replace('`', '``')
    _try(
        f"SHOW FULL TABLES FROM `{_db_safe}`",
        f"SHOW FULL TABLES FROM `{_db_safe}`",
        database=db_default,
    )

    print("[DIAG] 诊断完成。以上 [FAIL] 中若出现 2013 即命中触发查询。")


if __name__ == '__main__':
    # 顶层包一层 try，避免静默崩溃，全局异常打印 traceback
    try:
        _main()
    except Exception:
        traceback.print_exc()
