# -*- coding: utf-8 -*-
"""getData() 连接失败时应抛出真实错误（回归测试，对应 Bug 修复）。

历史 Bug：三个巡检模块的 getData() 在 inspector.connect() 失败时，把
( ok=False, ver=<真实错误> ) 中的真实错误丢弃、直接 return None，
导致上层只能看到笼统的"连接失败/获取数据为空"。

修复后：connect() 返回 (False, ver) 时，getData() 直接
raise ConnectionError("<DB> 连接失败: " + str(ver))，即把真实错误原文抛出。

本测试用 monkeypatch 替换 *Inspector.connect 为返回 (False, "REAL_DB_ERROR_xxx")
的假实现（无需真实数据库），验证：
  1. 抛出的是 ConnectionError
  2. 异常消息中包含 "REAL_DB_ERROR_xxx"（真实错误原文被原样抛出，而非被吞掉）

覆盖 MySQL / MariaDB / TiDB 三个库。
"""
import main_mysql
import main_mariadb
import main_tidb
import pytest

# connect() 失败时返回的（真实错误）假值；修复前该值会被丢弃。
FAKE_REAL_ERROR = "REAL_DB_ERROR_xxx"


def _fake_connect(self):
    """模拟数据库连接失败，返回 (False, 真实错误字符串)。"""
    return False, FAKE_REAL_ERROR


def test_mysql_getdata_raises_real_error(monkeypatch):
    monkeypatch.setattr(main_mysql.MySQLInspector, "connect", _fake_connect)
    with pytest.raises(ConnectionError) as exc:
        main_mysql.getData("127.0.0.1", 3306, "user", "pass")
    assert FAKE_REAL_ERROR in str(exc.value)


def test_mariadb_getdata_raises_real_error(monkeypatch):
    monkeypatch.setattr(main_mariadb.MariaDBInspector, "connect", _fake_connect)
    with pytest.raises(ConnectionError) as exc:
        main_mariadb.getData("127.0.0.1", 3306, "user", "pass")
    assert FAKE_REAL_ERROR in str(exc.value)


def test_tidb_getdata_raises_real_error(monkeypatch):
    monkeypatch.setattr(main_tidb.TiDBInspector, "connect", _fake_connect)
    with pytest.raises(ConnectionError) as exc:
        main_tidb.getData("127.0.0.1", 4000, "user", "pass")
    assert FAKE_REAL_ERROR in str(exc.value)


def test_mysql_getdata_success_path_not_affected(monkeypatch):
    """成功路径不受影响：connect 返回 (True, ver) 时应返回包装对象。"""
    monkeypatch.setattr(
        main_mysql.MySQLInspector,
        "connect",
        lambda self: (True, "8.0.36"),
    )
    wrapper = main_mysql.getData("127.0.0.1", 3306, "user", "pass")
    assert wrapper is not None
    assert hasattr(wrapper, "conn_db2")


def test_tidb_getdata_raises_even_with_database_arg(monkeypatch):
    """带 database 参数的连接失败场景同样抛出真实错误。"""
    monkeypatch.setattr(main_tidb.TiDBInspector, "connect", _fake_connect)
    with pytest.raises(ConnectionError) as exc:
        main_tidb.getData("127.0.0.1", 4000, "user", "pass", database="mydb")
    assert FAKE_REAL_ERROR in str(exc.value)
