# -*- coding: utf-8 -*-
"""
DBCheck License Generator
离线许可证生成器 — 供销售系统/管理员使用
用法:
  python -m license.generator --type per_instance --contact user@example.com --days 365
  python -m license.generator --type enterprise --contact corp@company.com
"""

import argparse
import base64
import hashlib
import json
import sys
import uuid
from datetime import datetime, timedelta
from typing import Optional

# 与 license/validator.py 保持同步
LICENSE_TYPES = {
    "trial":        {"max_instances": 3,  "days": 14,  "features": ["basic"]},
    "per_instance": {"max_instances": 1,  "days": 365, "features": ["basic", "security", "compliance"]},
    "per_team":     {"max_instances": 50, "days": 365, "features": ["basic", "security", "compliance", "advanced"]},
    "enterprise":  {"max_instances": -1, "days": 365, "features": ["all"]},
}

# 私钥（必须与 license/validator.py 中的 DEFAULT_SECRET_KEY 相同）
SECRET_KEY = "DBCheck-Pro-SecretKey-2025"


def generate_license(
    license_type: str,
    contact: str = "",
    days: Optional[int] = None,
    instances: Optional[int] = None,
    secret_key: str = SECRET_KEY,
) -> str:
    """
    生成离线许可证密钥

    参数:
        license_type: trial / per_instance / per_team / enterprise
        contact:      联系人和公司信息（会写入许可证）
        days:         有效期天数（默认根据类型自动计算）
        instances:    实例数量上限（仅 per_instance 有效）
        secret_key:   签名私钥
    返回:
        完整的许可证密钥（BASE64(payload).HMAC16）
    """
    if license_type not in LICENSE_TYPES:
        raise ValueError(
            "无效的许可证类型: %s（可选: %s）"
            % (license_type, ", ".join(LICENSE_TYPES.keys()))
        )

    type_info = LICENSE_TYPES[license_type]
    if days is None:
        days = type_info["days"]
    if instances is None:
        instances = type_info["max_instances"]

    # 构建 payload
    payload = {
        "id":       str(uuid.uuid4()),
        "type":     license_type,
        "instances": instances,
        "contact":  contact,
        "issued":   datetime.now().isoformat(),
        "expiry":   (datetime.now() + timedelta(days=days)).isoformat(),
    }

    # 编码 + 签名
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    token = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
    sig = hashlib.sha256((token + secret_key).encode()).hexdigest()[:16]

    return "%s.%s" % (token, sig)


def print_license(license_key: str, license_type: str, contact: str,
                  days: Optional[int], instances: Optional[int]):
    """格式化输出许可证信息"""
    try:
        token = license_key.rsplit(".", 1)[0]
        payload_json = base64.b64decode(token.encode()).decode()
        payload = json.loads(payload_json)
        expiry = payload.get("expiry", "N/A")
        lic_id = payload.get("id", "N/A")
    except Exception:
        expiry = "解析失败"
        lic_id = "N/A"

    type_info = LICENSE_TYPES.get(license_type, {})
    inst_label = instances if instances is not None else type_info.get("max_instances", "N/A")

    print("\n" + "=" * 60)
    print("  DBCheck Pro 许可证生成成功")
    print("=" * 60)
    print("  许可证ID : %s" % lic_id)
    print("  类型     : %s" % license_type)
    print("  联系     : %s" % (contact or "未填写"))
    print("  实例上限 : %s" % inst_label)
    print("  有效期   : %s" % (days or type_info.get("days", "N/A")))
    print("  到期日   : %s" % expiry)
    print("-" * 60)
    print("  许可证密钥:")
    print("  %s" % license_key)
    print("=" * 60)
    print("\n  将上述密钥发送给用户完成激活。\n")


def main():
    parser = argparse.ArgumentParser(
        description="DBCheck Pro 离线许可证生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m license.generator -t per_instance -c alice@company.com -d 365
  python -m license.generator -t enterprise  -c corp@company.com
  python -m license.generator -t trial      -c test@test.com
        """,
    )
    parser.add_argument("-t", "--type", "--type",
                        dest="license_type", required=True,
                        choices=list(LICENSE_TYPES.keys()),
                        help="许可证类型")
    parser.add_argument("-c", "--contact",
                        dest="contact", default="",
                        help="联系人/公司信息")
    parser.add_argument("-d", "--days", type=int,
                        dest="days", default=None,
                        help="有效期天数（默认按类型自动计算）")
    parser.add_argument("-i", "--instances", type=int,
                        dest="instances", default=None,
                        help="实例数量上限（仅 per_instance 有效）")
    parser.add_argument("-k", "--key",
                        dest="secret_key", default=SECRET_KEY,
                        help="签名私钥（默认使用内置密钥）")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="仅输出许可证密钥，不显示摘要")

    args = parser.parse_args()

    try:
        key = generate_license(
            license_type=args.license_type,
            contact=args.contact,
            days=args.days,
            instances=args.instances,
            secret_key=args.secret_key,
        )
        if args.quiet:
            print(key)
        else:
            print_license(key, args.license_type, args.contact,
                          args.days, args.instances)
    except Exception as e:
        print("错误: %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
