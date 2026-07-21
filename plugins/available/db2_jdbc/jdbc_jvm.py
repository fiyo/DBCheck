#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Db2 JDBC 共享 JVM 管理模块（单例）。

职责：
  - 在项目根目录 drivers/**/*.jar 中一次性收集所有 JDBC 驱动 jar；
  - 首次调用 ensure_jvm() 时启动 JVM，并将全部驱动 jar 加入 classpath；
  - 若 JVM 已由其它插件（如 oracle_jdbc）先行启动，则通过反射把缺失的
    jar 动态追加到系统类加载器的 classpath，无需重启 JVM；
  - 提供统一的 Db2 驱动注册入口，供 main_plugin 在 connect() 时调用。

设计要点（来自现场已验证配方）：
  - 单一事实来源：所有 JDBC 驱动统一放在 <root>/drivers/ 下，
    ensure_jvm() 用 glob 一次性收集，避免各插件单独指定 jar 导致冲突。
  - JVM 进程级单例：jpype.isJVMStarted() 守护，重复调用 ensure_jvm() 安全。
  - 目标 JDK 为 1.8（plugin.json min_java_version=1.8）：
    AppClassLoader 为 URLClassLoader，反射 addURL 追加 classpath 可行。

该模块被设计为“自包含 + 可复用”：既被 db2_jdbc 自身使用，也可被其它
JDBC 类插件复用，避免多处各起 JVM 导致冲突。
"""

import glob
import os
from typing import List, Optional

import jpype
from jpype import JClass


# 项目根目录：本文件位于 <root>/plugins/available/db2_jdbc/jdbc_jvm.py
# 向上三级（db2_jdbc -> available -> plugins -> root）即项目根
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_PLUGIN_DIR, "..", "..", ".."))
_DRIVERS_DIR = os.path.join(_PROJECT_ROOT, "drivers")


def _discover_driver_jars() -> List[str]:
    """递归收集 drivers/ 下所有 *.jar 的绝对路径。

    Returns:
        按字典序排序的 jar 绝对路径列表（可能为空）。
    """
    if not os.path.isdir(_DRIVERS_DIR):
        return []
    jars = glob.glob(os.path.join(_DRIVERS_DIR, "**", "*.jar"), recursive=True)
    jars = [os.path.abspath(j) for j in jars if os.path.isfile(j)]
    return sorted(jars)


def _dedup_jars(*jar_lists: Optional[List[str]]) -> List[str]:
    """合并并去重 jar 路径（保持首次出现顺序）。"""
    seen = set()
    result: List[str] = []
    for lst in jar_lists:
        for j in (lst or []):
            aj = os.path.abspath(j)
            if aj not in seen:
                seen.add(aj)
                result.append(aj)
    return result


def _is_on_classpath(jar: str) -> bool:
    """判断给定 jar 是否已在系统类加载器的 classpath 中。

    若无法判定（如非 URLClassLoader，JDK 9+ 的 AppClassLoader），返回 False，
    交由 _append_to_classpath 尝试追加（重复 addURL 无害）。
    """
    try:
        cl = JClass("java.lang.ClassLoader").getSystemClassLoader()
        try:
            urls = cl.getURLs()
        except Exception:
            return False
        jar_norm = os.path.normcase(os.path.abspath(jar))
        for u in urls:
            p = u.getPath()  # file:/.../x.jar -> /.../x.jar
            if os.path.normcase(os.path.abspath(p)) == jar_norm:
                return True
        return False
    except Exception:
        return False


def _find_add_url_method(classloader) -> "object":
    """在系统类加载器类层级上查找 addURL(java.net.URL) 声明方法。

    不同 JDK / 厂商的 AppClassLoader 实现不同：JDK 8 的 AppClassLoader 继承自
    URLClassLoader（提供 addURL），逐层向上查找可兼容封装场景。

    Returns:
        java.lang.reflect.Method 对象（已 setAccessible）

    Raises:
        RuntimeError: 类层级上均找不到 addURL 方法时。
    """
    URL = JClass("java.net.URL")
    cls = classloader.getClass()
    while cls is not None:
        try:
            return cls.getDeclaredMethod("addURL", URL.getClass())
        except Exception:
            cls = cls.getSuperclass()
    raise RuntimeError("无法在系统类加载器上找到 addURL(java.net.URL) 方法（请确认使用 JDK 8）")


def _require_jars_on_classpath(jars: List[str]) -> None:
    """JDK 9+ 无法在运行时追加 classpath，这里校验关键驱动类是否可加载。

    以 db2jcc4 为例：若 com.ibm.db2.jcc.DB2Driver 可加载，说明驱动 jar 已在
    startJVM 时通过 classpath 参数注入，无需动作；否则给出明确错误（需改用
    JDK 8，或在 JVM 首次启动时就由 ensure_jvm() 注入全部驱动 jar）。
    """
    try:
        JClass("com.ibm.db2.jcc.DB2Driver")
    except Exception:
        raise RuntimeError(
            "JVM 已启动但未能在 classpath 中找到 Db2 驱动"
            "（com.ibm.db2.jcc.DB2Driver），且当前 JDK 不支持运行时 addURL。"
            "请使用 JDK 8，或在 JVM 首次启动时通过 ensure_jvm() 注入驱动 jar。"
        )


def _append_to_classpath(jars: List[str]) -> None:
    """将缺失的 jar 动态追加到已运行的 JVM classpath（反射调用 addURL）。

    JDK 9+ 的 AppClassLoader 不再是 URLClassLoader，运行时 addURL 不可用；此时
    jar 只能在 startJVM 时通过 classpath 参数注入。若 JVM 已由 ensure_jvm 首次
    启动，jar 已在 classpath 中，这里做一次可达性校验后直接跳过（不报错），保证
    ensure_jvm() 在 JDK 8 与 JDK 11+ 上均为幂等。
    """
    if not jars:
        return
    # JDK 8：AppClassLoader 为 URLClassLoader，可运行时 addURL
    try:
        cl = JClass("java.lang.ClassLoader").getSystemClassLoader()
        add_method = _find_add_url_method(cl)
    except RuntimeError:
        # JDK 9+：不支持运行时 addURL —— 校验 jar 是否已在 classpath，是则跳过
        _require_jars_on_classpath(jars)
        return
    add_method.setAccessible(True)
    URL = JClass("java.net.URL")
    added = 0
    for jar in jars:
        if _is_on_classpath(jar):
            continue
        url = URL("file:" + os.path.abspath(jar))
        add_method.invoke(cl, url)
        added += 1
    if added:
        print(f"[db2_jdbc.jvm] 已动态追加 {added} 个 jar 到运行中的 JVM classpath")


def ensure_jvm(extra_jars: Optional[List[str]] = None) -> List[str]:
    """确保 JVM 已启动且所有 JDBC 驱动 jar 在 classpath 中。

    幂等：多次调用安全。首次启动 JVM；若 JVM 已存在则只补充缺失的 jar。

    Args:
        extra_jars: 额外需要加入 classpath 的 jar 路径（可选），例如插件
            自带驱动；会与 drivers/ 下自动发现的 jar 合并去重。

    Returns:
        实际置于 classpath 的全部 jar 绝对路径列表。
    """
    base_jars = _discover_driver_jars()
    all_jars = _dedup_jars(base_jars, extra_jars)

    if not jpype.isJVMStarted():
        if not all_jars:
            raise RuntimeError(
                f"未找到任何 JDBC 驱动 jar（已扫描 {_DRIVERS_DIR}）。"
                "请确认 drivers/db2/db2jcc4.jar 等驱动文件已就位。"
            )
        jpype.startJVM(classpath=all_jars, convertStrings=True)
        print(f"[db2_jdbc.jvm] JVM 已启动，classpath 含 {len(all_jars)} 个 jar")
    else:
        _append_to_classpath(all_jars)

    return all_jars


def register_db2_driver() -> None:
    """向 DriverManager 注册 Db2 JDBC 驱动（幂等）。

    需在 ensure_jvm() 之后调用。重复注册无害。
    """
    if not jpype.isJVMStarted():
        raise RuntimeError("register_db2_driver() 调用前必须先 ensure_jvm()")
    # 触发驱动类静态初始化（JDBC 4 下 DriverManager 也会自动发现，
    # 此处显式加载以保证驱动一定就绪）。
    JClass("java.lang.Class").forName("com.ibm.db2.jcc.DB2Driver")


def get_driver_jars() -> List[str]:
    """返回当前已发现的全部 JDBC 驱动 jar（不启动 JVM）。"""
    return _discover_driver_jars()


__all__ = ["ensure_jvm", "register_db2_driver", "get_driver_jars"]
