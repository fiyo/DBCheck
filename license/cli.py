# -*- coding: utf-8 -*-
"""
DBCheck License CLI
许可证命令行工具 — 用于激活、注销、查看状态
用法:
  python -m license.cli status
  python -m license.cli activate <license_key>
  python -m license.cli deactivate
  python -m license.cli generate --type per_instance --contact user@example.com
"""

import argparse
import sys
import os

# 将当前目录加入路径，以便导入同级模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from license.validator import get_validator, LICENSE_TYPES


def cmd_status():
    """查看许可证状态"""
    v = get_validator()
    result = v.verify()
    print("\n[ DBCheck 许可证状态 ]")
    print("  版本    : %s" % result["edition"].upper())
    print("  状态    : %s" % ("已激活" if result["valid"] else "未激活/已过期"))
    if result["valid"]:
        print("  类型    : %s" % result.get("type", "N/A"))
        print("  到期日  : %s" % result.get("expires", "N/A"))
        print("  实例上限: %s" % result.get("max_instances", "N/A"))
        print("  功能    : %s" % ", ".join(result.get("features", [])))
    else:
        print("  原因    : %s" % result.get("message", "未知"))
    print()


def cmd_activate(key: str):
    """激活许可证"""
    if not key or not key.strip():
        print("错误: 请提供许可证密钥", file=sys.stderr)
        print("用法: python -m license.cli activate <license_key>", file=sys.stderr)
        sys.exit(1)

    v = get_validator()
    result = v.activate(key.strip())

    print()
    if result.get("success"):
        print("激活成功！")
        print("  类型   : %s" % result["data"].get("type"))
        print("  到期日 : %s" % result["data"].get("expires"))
        print("  实例上限: %s" % result["data"].get("max_instances"))
    else:
        print("激活失败: %s" % result.get("message"))
        sys.exit(1)


def cmd_deactivate():
    """注销许可证"""
    v = get_validator()
    result = v.deactivate()
    if result.get("success"):
        print("注销成功，许可证已移除。")
    else:
        print("注销失败: %s" % result.get("message"))
        sys.exit(1)


def cmd_generate(args):
    """生成许可证（直接调用 generator）"""
    from license.generator import generate_license, print_license
    key = generate_license(
        license_type=args.type,
        contact=args.contact or "",
        days=args.days,
        instances=args.instances,
    )
    print_license(key, args.type, args.contact or "", args.days, args.instances)


def main():
    parser = argparse.ArgumentParser(
        description="DBCheck 许可证管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser("status", help="查看当前许可证状态")

    # activate
    act = sub.add_parser("activate", help="激活许可证")
    act.add_argument("key", help="许可证密钥", nargs="?", default="")
    act.add_argument("-k", "--key", dest="key_opt", help="许可证密钥（长参数）")

    # deactivate
    sub.add_parser("deactivate", help="注销当前许可证")

    # generate
    gen = sub.add_parser("generate", help="生成新许可证（需私钥）")
    gen.add_argument("-t", "--type", required=True,
                     choices=list(LICENSE_TYPES.keys()),
                     help="许可证类型")
    gen.add_argument("-c", "--contact", default="", help="联系人/公司")
    gen.add_argument("-d", "--days", type=int, default=None, help="有效期天数")
    gen.add_argument("-i", "--instances", type=int, default=None, help="实例上限")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "activate":
        key = args.key or args.key_opt or ""
        cmd_activate(key)
    elif args.command == "deactivate":
        cmd_deactivate()
    elif args.command == "generate":
        cmd_generate(args)


if __name__ == "__main__":
    main()
