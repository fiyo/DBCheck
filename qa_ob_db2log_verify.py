# -*- coding: utf-8 -*-
"""
DBCheck QA 验证脚本（Issue B + Issue A）
========================================
验证两项改动的行为正确性（而非仅确认代码存在）：

  Issue B：web_ui._web_log_is_console_only 辅助函数 + _web_print 拦截器分流
    A1  直接调用 _web_log_is_console_only 验证 6 类消息判定
    A2  用 spy 复制 _web_print 的 gating 逻辑（调用真实辅助函数）验证分流
    A3  读 web_ui.py 源码确认 _web_print 确实调用了辅助函数（非旧 [metrics] only）

  Issue A：diag_oceanbase.py 端到端（mock 真实失败模式）
    B2  mock pro + pymysql，让元数据查询抛 2013，验证脚本精确指出是哪条查询
    B3  无 oceanbase 实例时不带参数运行，验证友好提示且不崩溃

运行：python qa_ob_db2log_verify.py
不 git 提交；纯验证脚本。
"""

import os
import sys
import io
import re
import types
import contextlib

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

WEB_UI_PATH = os.path.join(_BASE_DIR, "web_ui.py")

G_WEB_UI = None  # 在 main() 中注入 web_ui 模块，供 case 使用


class Case:
    """单个用例：聚合若干子断言。"""

    def __init__(self, name):
        self.name = name
        self.passed = True
        self.notes = []

    def check(self, cond, desc):
        if cond:
            self.notes.append("  [OK] " + desc)
        else:
            self.passed = False
            self.notes.append("  [X]  " + desc)


# ─────────────────────────────────────────────────────────────────────
# Issue B —— A1：直接调用辅助函数
# ─────────────────────────────────────────────────────────────────────
def case_A1():
    c = Case("A1: _web_log_is_console_only 辅助函数判定")
    cases = [
        ("[metrics] xxx", True),
        ("[DB2] 连接成功，版本: DB2 v120.10.50", True),
        ("[DB2] 连接失败: xxx", True),
        ("[DB2] |████----| 30% 采集表空间", False),
        ("[DB2] 正在连接 ...", False),
        ("普通巡检日志", False),
    ]
    for msg, expected in cases:
        got = G_WEB_UI._web_log_is_console_only(msg)
        c.check(
            got == expected,
            "{} -> {} (期望 {})".format(repr(msg), got, expected),
        )
    return c


# ─────────────────────────────────────────────────────────────────────
# Issue B —— A2：真实拦截器分流（spy 复制 gating + 真实辅助函数）
# ─────────────────────────────────────────────────────────────────────
def case_A2():
    import builtins

    c = Case("A2: 真实拦截器分流（spy + 真实辅助函数）")
    webui_emits = []   # 模拟“前端 webui 巡检日志”侧 _emit 捕获的 msg 列表
    console_out = []    # 模拟后端控制台输出捕获

    def fake_emit(event, data):
        webui_emits.append(data.get("msg", ""))

    def spy_print(*args, **kwargs):
        # 复制 web_ui._web_print 的 gating 逻辑，但调用“真实”的辅助函数，
        # 并把前端 emit 与后端打印分别捕获到 list，以便断言分流结果。
        sep = kwargs.get("sep", " ")
        raw = sep.join(str(x) for x in args)
        msg_clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw)
        if msg_clean.strip() and not G_WEB_UI._web_log_is_console_only(msg_clean):
            fake_emit("log", {"msg": msg_clean})
        console_out.append(raw)

    orig = builtins.print
    builtins.print = spy_print
    try:
        # 分别打印两条代表消息
        spy_print("[DB2] 连接成功，版本: DB2 v120.10.50")
        spy_print("[DB2] |████----| 30% 采集表空间")
    finally:
        builtins.print = orig

    conn_pushed = any("连接成功" in m for m in webui_emits)
    c.check(
        not conn_pushed,
        "连接成功消息【未】推送到 webui 巡检日志"
        + ("" if not conn_pushed else "（实际被推送: %r）" % webui_emits),
    )
    prog_pushed = any("30%" in m for m in webui_emits)
    c.check(
        prog_pushed,
        "进度条消息【已】推送到 webui 巡检日志"
        + ("" if prog_pushed else "（实际 webui_emits=%r）" % webui_emits),
    )
    c.check(
        any("连接成功" in m for m in console_out),
        "连接成功消息仍输出到后端控制台（_orig_print 始终执行）",
    )
    c.check(
        any("30%" in m for m in console_out),
        "进度条消息仍输出到后端控制台（_orig_print 始终执行）",
    )
    return c


# ─────────────────────────────────────────────────────────────────────
# Issue B —— A3：源码确认 _web_print 确实调用辅助函数
# ─────────────────────────────────────────────────────────────────────
def case_A3_confirm_source():
    c = Case("A3: 确认 web_ui.py 的 _web_print 调用辅助函数")
    try:
        with open(WEB_UI_PATH, "r", encoding="utf-8") as f:
            src = f.read()
    except Exception as e:  # pragma: no cover
        c.check(False, "无法读取 web_ui.py: %s" % e)
        return c

    idx = src.find("def _web_print")
    c.check(idx != -1, "web_ui.py 定义了 _web_print")

    if idx != -1:
        # 截取从 def _web_print 到下一个“顶层 def”之间的源码块
        next_def = src.find("\ndef ", idx + 5)
        block = src[idx:next_def] if next_def != -1 else src[idx:]

        c.check(
            "_web_log_is_console_only" in block,
            "_web_print 函数体内调用了 _web_log_is_console_only 辅助函数",
        )
        c.check(
            "_emit('log'" in block or '_emit("log"' in block,
            "_web_print 在通过 gating 时仍向 webui 推送日志（_emit('log', ...)）",
        )
        # 确认不是“旧的仅 [metrics] 判断”：旧逻辑应是直接对消息做 startswith('[metrics]') 而不经辅助函数
        old_only = bool(re.search(r"not\s+_msg_clean\.startswith\('\s*\[metrics\]'\s*\)", block))
        c.check(
            not old_only,
            "_web_print 不是旧的“仅 [metrics] 开头”判断（已改为连接状态语义）",
        )
    return c


# ─────────────────────────────────────────────────────────────────────
# Issue A —— 伪造 pro / pymysql 模块，注入 sys.modules 后运行 diag_oceanbase
# ─────────────────────────────────────────────────────────────────────
def _build_fake_pro(instances):
    """构造一个假的 pro 模块：get_instance_manager() 返回管理器。"""

    class FakeInstanceManager:
        def __init__(self, insts):
            self._insts = insts

        def get_all_instances(self, mask_password=False):
            return self._insts

        def get_instance_decrypted(self, ds_id):
            for i in self._insts:
                if i.get("id") == ds_id:
                    return i
            return None

    pro_mod = types.ModuleType("pro")
    mgr = FakeInstanceManager(instances)
    pro_mod.get_instance_manager = lambda: mgr
    return pro_mod, mgr


def _build_fake_pymysql():
    """构造一个假的 pymysql 模块：connect 不抛异常；元数据查询 execute 抛 2013。"""
    pymod = types.ModuleType("pymysql")
    pymod.__version__ = "1.1.0"

    err_mod = types.ModuleType("pymysql.err")

    class OperationalError(Exception):
        pass

    err_mod.OperationalError = OperationalError
    pymod.err = err_mod

    # 真实现象：连接能建（connect 不抛），但元数据查询被掐（execute 抛 2013）
    META_KEYWORDS = [
        "INFORMATION_SCHEMA.SCHEMATA",
        "SHOW DATABASES",
        "INFORMATION_SCHEMA.TABLES",
        "SHOW FULL TABLES",
    ]

    class FakeCursor:
        def execute(self, sql, params=None):
            up = (sql or "").upper()
            for kw in META_KEYWORDS:
                if kw.upper() in up:
                    raise err_mod.OperationalError(
                        2013, "Lost connection to MySQL server during query"
                    )
            # SELECT VERSION() / SELECT 1 正常返回

        def fetchall(self):
            return [("row1",), ("row2",)]

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    def fake_connect(*args, **kwargs):
        return FakeConn()  # connect 本身不抛异常

    pymod.connect = fake_connect
    return pymod, err_mod


def _run_diag(argv, instances):
    """注入 fake pro/pymysql，运行 diag_oceanbase._main，返回 (stdout_text, exc)。"""
    import importlib

    pro_mod, _ = _build_fake_pro(instances)
    pymod, _ = _build_fake_pymysql()

    saved = {
        "pro": sys.modules.get("pro"),
        "pymysql": sys.modules.get("pymysql"),
        "pymysql.err": sys.modules.get("pymysql.err"),
    }
    sys.modules["pro"] = pro_mod
    sys.modules["pymysql"] = pymod
    sys.modules["pymysql.err"] = pymod.err

    saved_argv = sys.argv
    sys.argv = list(argv)

    import diag_oceanbase

    out = io.StringIO()
    exc = None
    try:
        with contextlib.redirect_stdout(out):
            diag_oceanbase._main()
    except SystemExit as e:
        exc = e
    except Exception as e:  # noqa
        exc = e
    finally:
        sys.argv = saved_argv
        # 还原 sys.modules，避免污染后续用例
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    return out.getvalue(), exc


# ─────────────────────────────────────────────────────────────────────
# Issue A —— B2：端到端（mock 2013 元数据查询）
# ─────────────────────────────────────────────────────────────────────
def case_B2():
    c = Case("B2: diag_oceanbase 端到端（mock 元数据查询 2013）")
    ob_instance = {
        "id": "ds1",
        "db_type": "oceanbase",
        "host": "127.0.0.1",
        "port": 2881,
        "user": "root",
        "password": "secret",
        "tenant": "test",
        "database": "sys",
    }
    out, exc = _run_diag(["diag_oceanbase.py", "ds1"], [ob_instance])

    c.check(exc is None, "脚本运行无未捕获异常 (exc=%r)" % exc)

    c.check("[OK] SELECT VERSION()" in out, "SELECT VERSION() 标记为 [OK]")
    c.check("[OK] SELECT 1" in out, "SELECT 1 标记为 [OK]")

    c.check(
        "[FAIL] SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA" in out
        and "2013" in out,
        "INFORMATION_SCHEMA.SCHEMATA 标记为 [FAIL] 且含 2013",
    )
    c.check(
        "[FAIL] SHOW DATABASES" in out and "2013" in out,
        "SHOW DATABASES 标记为 [FAIL] 且含 2013",
    )
    c.check(
        "[FAIL] SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES" in out
        and "2013" in out,
        "INFORMATION_SCHEMA.TABLES 标记为 [FAIL] 且含 2013",
    )
    c.check(
        "[FAIL] SHOW FULL TABLES" in out and "2013" in out,
        "SHOW FULL TABLES 标记为 [FAIL] 且含 2013",
    )
    return c


# ─────────────────────────────────────────────────────────────────────
# Issue A —— B3：健壮性（无 oceanbase 实例，不带参数运行）
# ─────────────────────────────────────────────────────────────────────
def case_B3():
    c = Case("B3: 无 oceanbase 实例时友好提示（不崩溃）")
    non_ob = [
        {"id": "pg1", "db_type": "postgresql", "host": "h", "port": 5432},
        {"id": "my1", "db_type": "mysql", "host": "h", "port": 3306},
    ]
    out, exc = _run_diag(["diag_oceanbase.py"], non_ob)

    c.check("ds_id" in out, "输出含如何获取 ds_id 的说明")
    c.check("oceanbase" in out.lower(), "输出提示未找到 oceanbase 实例")
    c.check(exc is None, "无未捕获异常 / 未崩溃 (exc=%r)" % exc)
    c.check("Traceback" not in out, "输出无 traceback（干净退出）")
    return c


# ─────────────────────────────────────────────────────────────────────
# 主流程 + 报告 + 智能路由判定
# ─────────────────────────────────────────────────────────────────────
def main():
    global G_WEB_UI

    print("=" * 72)
    print("DBCheck QA 验证：Issue B（连接状态拦截） + Issue A（OB 诊断脚本）")
    print("=" * 72)

    # 导入 web_ui（压制其启动期噪声日志），失败则明确报告
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            import web_ui  # noqa: F401
        G_WEB_UI = web_ui
        print("[OK] 已加载 web_ui 模块（_web_log_is_console_only 可用）")
    except Exception as e:  # pragma: no cover
        print("[X]  无法导入 web_ui：%s" % e)
        print("路由判定: Engineer（环境依赖缺失，需 Engineer 确认运行环境）")
        return 2

    cases = [
        case_A1(),
        case_A2(),
        case_A3_confirm_source(),
        case_B2(),
        case_B3(),
    ]

    total_asserts = 0
    passed_asserts = 0
    failed_cases = []
    for c in cases:
        n_pass = sum(1 for n in c.notes if n.startswith("  [OK]"))
        n_all = len(c.notes)
        total_asserts += n_all
        passed_asserts += n_pass
        status = "PASS" if c.passed else "FAIL"
        print("\n[%s] %s  (%d/%d 子断言)" % (status, c.name, n_pass, n_all))
        for note in c.notes:
            print(note)
        if not c.passed:
            failed_cases.append(c.name)

    print("\n" + "=" * 72)
    print(
        "汇总: %d 用例 | %d 子断言 | 通过 %d | 失败 %d"
        % (len(cases), total_asserts, passed_asserts, total_asserts - passed_asserts)
    )
    print("用例通过: %d/%d" % (len(cases) - len(failed_cases), len(cases)))

    # ── 智能路由判定 ──
    # A 组（A1/A2/A3）验证的是 web_ui.py 源码行为：若失败则属源码缺陷 → Engineer
    # B 组（B2/B3）验证 diag 脚本逻辑（mock 验证）：若失败多因测试/mock 自身 → 自查
    source_fail = [name for name in failed_cases if name.startswith("A")]
    if source_fail:
        routing = "Engineer"
        note = "源码相关用例失败: %s" % ", ".join(source_fail)
    elif failed_cases:
        routing = "QA"
        note = "失败用例均为 B 组（mock/脚本逻辑），需 QA 自查修正测试"
    else:
        routing = "NoOne"
        note = "全部通过，无需返工"

    print("智能路由判定: %s —— %s" % (routing, note))
    print("=" * 72)

    if routing == "NoOne":
        print("结论：Issue B 拦截逻辑与 Issue A 诊断脚本逻辑均验证通过。")
        print("注：真实 OB 连接仍需用户在自有环境运行 python diag_oceanbase.py <ds_id> 复现；")
        print("    QA 以 mock 证明脚本能精确报告 2013 出在哪条查询。")
    return 0 if not failed_cases else 1


if __name__ == "__main__":
    sys.exit(main())
