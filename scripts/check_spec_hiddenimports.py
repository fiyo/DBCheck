#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck

"""
check_spec_hiddenimports.py

防回归检查：PyInstaller spec 的 ``hiddenimports`` 里写的是 **Python 导入名**，
不是 PyPI 包名。二者不一致时（例如 ``JPype1`` / ``jpype``），PyInstaller 会打印
``ERROR: Hidden import 'JPype1' not found`` 并**静默跳过该项**——构建照样成功，
但该模块及其自带 hook 不会进依赖图，冻结版可能缺件（JPype 的 org.jpype.jar
与 _jpype 扩展就是这么漏的）。

本脚本用纯标准库静态解析三个平台的 spec，做三类检查：

1. **包名误用**：条目命中「包名 ≠ 导入名」映射表时直接 FAIL，并给出正确导入名。
2. **模块路径合法性**：必须是合法的 dotted identifier（不含连字符 / 空格 /
   版本说明符等）。
3. **可解析性（尽力而为）**：顶层模块能被 ``importlib.util.find_spec`` 找到、
   或属于标准库 / 项目内模块 / requirements 里已声明包的正确导入名时通过；
   其余（未安装的传递依赖、平台专属子模块）只 WARN，不阻断 CI。
   加 ``--strict`` 可把 WARN 升级为 FAIL，供本地装齐依赖后自查。

另外校验每个平台都必须显式声明 JDBC 后端（``jaydebeapi`` / ``jpype``），
避免有人删掉这两项后靠动态扫描碰运气。

用法
----
    python scripts/check_spec_hiddenimports.py            # CI：仅包名误用/非法路径/缺项会 FAIL
    python scripts/check_spec_hiddenimports.py --strict   # 本地：WARN 也 FAIL

Exit codes
----------
* 0 -> 全部通过
* 1 -> 存在必须修复的问题
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import keyword
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_NAMES = ("dbcheck_linux.spec", "dbcheck_windows.spec", "dbcheck_macos.spec")
REQUIREMENTS = ROOT / "deploy" / "requirements.txt"

# 只收录「PyPI 包名 != Python 导入名」的包，键与值均按规范化形式比较
# （小写、连字符转下划线）。包名与模块名相同的（flask / gevent / redis 等）
# 不需要列入，写出来本来就是对的。
PACKAGE_MODULE_ALIASES = {
    "jpype1": "jpype",
    "pillow": "PIL",
    "pyyaml": "yaml",
    "python_docx": "docx",
    "python_dotenv": "dotenv",
    "beautifulsoup4": "bs4",
    "psycopg2_binary": "psycopg2",
    "pyjwt": "jwt",
    "opencv_python": "cv2",
    "scikit_learn": "sklearn",
    "mysql_connector_python": "mysql.connector",
    "protobuf": "google.protobuf",
}

# 冻结版 JDBC 后端必需；三个平台都必须显式声明，别指望 PyInstaller 扫到动态加载的插件。
REQUIRED_MODULES = ("jaydebeapi", "jpype")


def normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def read_declared_modules() -> set[str]:
    """从 requirements.txt 收集包名对应的正确导入名（应用别名表）。"""
    declared: set[str] = set()
    if not REQUIREMENTS.exists():
        return declared
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", ".")):
            continue
        # 去掉版本说明符 / extras / 环境标记
        pkg = line.split("[", 1)[0]
        for sep in ("<", ">", "=", "!", "~", ";", " "):
            pkg = pkg.split(sep, 1)[0]
        pkg = pkg.strip()
        if not pkg:
            continue
        key = normalize(pkg)
        target = PACKAGE_MODULE_ALIASES.get(key, key)
        declared.add(target.split(".", 1)[0].lower())
    return declared


def local_top_level_modules() -> set[str]:
    """项目内顶层包 / 模块名（无需安装即可认定合法）。

    部分顶层包是 namespace package（没有 __init__.py），因此只要目录里含
    .py 文件就认定为项目内模块，避免把它们误报成「环境未装」。
    """
    names: set[str] = set()
    for child in ROOT.iterdir():
        if child.name.startswith((".", "_")):
            continue
        if child.is_dir():
            if (child / "__init__.py").exists() or any(child.glob("*.py")):
                names.add(child.name)
        elif child.is_file() and child.suffix == ".py":
            names.add(child.stem)
    return names


def extract_hiddenimports(spec_path: Path) -> tuple[list[str], int, list[str]]:
    """返回 (字符串条目, 动态项数量, 解析错误)。"""
    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))
    items: list[str] = []
    dynamic = 0
    errors: list[str] = []
    found_analysis = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "Analysis"):
            continue
        found_analysis = True
        kw = next((k for k in node.keywords if k.arg == "hiddenimports"), None)
        if kw is None:
            errors.append("Analysis() 缺少 hiddenimports 参数")
            continue
        if not isinstance(kw.value, ast.List):
            errors.append("hiddenimports 不是字面量列表，无法静态解析")
            continue
        for elt in kw.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                items.append(elt.value)
            elif isinstance(elt, ast.Starred):
                dynamic += 1  # collect_submodules / collect_all 等运行时展开
            else:
                errors.append(f"hiddenimports 含非字符串字面量: {ast.dump(elt)[:80]}")
    if not found_analysis:
        errors.append("未找到 Analysis() 调用")
    return items, dynamic, errors


def is_valid_module_path(name: str) -> bool:
    if not name or name.startswith(".") or name.endswith("."):
        return False
    for part in name.split("."):
        if not part.isidentifier() or keyword.iskeyword(part):
            return False
    return True


def resolvable(top: str, declared: set[str], local: set[str]) -> bool:
    if top in sys.stdlib_module_names or top.lower() in local or top.lower() in declared:
        return True
    try:
        return importlib.util.find_spec(top) is not None
    except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="把 WARN 也视为失败")
    args = parser.parse_args()

    # 脚本从 scripts/ 启动时 sys.path[0] 不是仓库根，补上才能解析 namespace package
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    declared = read_declared_modules()
    local = local_top_level_modules()
    failures: list[str] = []
    warnings: list[str] = []

    for spec_name in SPEC_NAMES:
        path = ROOT / "build" / spec_name
        if not path.exists():
            failures.append(f"{spec_name}: 文件不存在")
            continue
        try:
            items, dynamic, errors = extract_hiddenimports(path)
        except SyntaxError as exc:
            failures.append(f"{spec_name}: spec 语法错误 {exc}")
            continue
        failures.extend(f"{spec_name}: {e}" for e in errors)
        print(f"[spec] {spec_name}: {len(items)} 个静态条目, {dynamic} 个动态展开项")

        tops: set[str] = set()
        for item in items:
            if not is_valid_module_path(item):
                failures.append(f"{spec_name}: 非法模块路径 {item!r}")
                continue
            top = item.split(".", 1)[0]
            tops.add(top.lower())
            key = normalize(top)
            if key in PACKAGE_MODULE_ALIASES:
                correct = PACKAGE_MODULE_ALIASES[key]
                failures.append(
                    f"{spec_name}: {item!r} 是 PyPI 包名，不是 Python 导入名；"
                    f"应写 {correct!r}（PyInstaller 会静默跳过写错的 hiddenimport）"
                )
                continue
            if not resolvable(top, declared, local):
                warnings.append(
                    f"{spec_name}: {item!r} 顶层模块 {top!r} 在当前环境无法解析"
                    "（未安装的传递依赖 / 平台专属模块，CI 不阻断）"
                )
        for required in REQUIRED_MODULES:
            if required not in tops:
                failures.append(
                    f"{spec_name}: 缺少必需的 JDBC 后端 hiddenimport {required!r}"
                )

    print(f"[env] 标准库 {len(sys.stdlib_module_names)} 项 / 项目内 {len(local)} 项 "
          f"/ requirements 推导 {len(declared)} 项")

    if warnings:
        print(f"\n[WARN] {len(warnings)} 条（环境相关，不阻断）:")
        for w in warnings[:12]:
            print("  -", w)
        if len(warnings) > 12:
            print(f"  ... 另有 {len(warnings) - 12} 条")

    if failures:
        print(f"\n[FAIL] {len(failures)} 条必须修复:", file=sys.stderr)
        for f in failures:
            print("  -", f, file=sys.stderr)
        return 1

    if warnings and args.strict:
        print("\n[FAIL] --strict 模式下 WARN 升级为失败", file=sys.stderr)
        return 1

    print("\n[OK] 三个平台 spec 的 hiddenimports 检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
