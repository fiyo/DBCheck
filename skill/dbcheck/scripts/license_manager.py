#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import json
import base64
import hashlib
from datetime import datetime, timedelta
import platform

class SimpleCrypto:
    """简单的加密解密类"""
    
    def __init__(self, secret="ODB_SECRET_2024"):
        self.secret = secret.encode('utf-8')
    
    def _xor_encrypt_decrypt(self, data, key):
        """使用XOR进行加密/解密"""
        key_len = len(key)
        result = bytearray()
        for i, byte in enumerate(data):
            result.append(byte ^ key[i % key_len])
        return bytes(result)
    
    def encrypt(self, text):
        """加密文本"""
        data = text.encode('utf-8')
        # 生成密钥
        key = hashlib.sha256(self.secret).digest()[:32]
        # XOR加密
        encrypted = self._xor_encrypt_decrypt(data, key)
        # Base64编码
        return base64.b64encode(encrypted).decode('utf-8')
    
    def decrypt(self, token):
        """解密文本"""
        try:
            # Base64解码
            encrypted = base64.b64decode(token.encode('utf-8'))
            # 生成密钥
            key = hashlib.sha256(self.secret).digest()[:32]
            # XOR解密
            decrypted = self._xor_encrypt_decrypt(encrypted, key)
            return decrypted.decode('utf-8')
        except Exception as e:
            raise ValueError(f"解密失败: {e}")

class LicenseValidator:
    """许可证验证类"""

    def __init__(self):
        # 获取可执行文件所在目录
        if getattr(sys, 'frozen', False):
            # 打包后的可执行文件
            self.exe_dir = os.path.dirname(sys.executable)
        else:
            # 脚本运行
            self.exe_dir = os.path.dirname(os.path.abspath(__file__))
        
        # 许可证文件路径 - 优先在可执行文件同目录查找
        self.license_file = os.path.join(self.exe_dir, "mysql_inspector.lic")
        self.trial_days = 36500  # 100年试用期
        self.crypto = SimpleCrypto()
        self._init_license_system()

    def _parse_datetime(self, date_string):
        """解析日期字符串，兼容Python 3.6"""
        try:
            # 尝试Python 3.7+的fromisoformat
            return datetime.fromisoformat(date_string)
        except AttributeError:
            # Python 3.6兼容：手动解析ISO格式
            try:
                if 'T' in date_string:
                    date_part, time_part = date_string.split('T')
                    year, month, day = map(int, date_part.split('-'))
                    time_parts = time_part.split(':')
                    hour, minute = int(time_parts[0]), int(time_parts[1])
                    second = int(time_parts[2].split('.')[0]) if len(time_parts) > 2 else 0
                    return datetime(year, month, day, hour, minute, second)
                else:
                    date_part, time_part = date_string.split(' ')
                    year, month, day = map(int, date_part.split('-'))
                    hour, minute, second = map(int, time_part.split(':'))
                    return datetime(year, month, day, hour, minute, second)
            except Exception as e:
                print(f"日期解析错误: {e}")
                return datetime.now()

    def _format_datetime(self, dt):
        """格式化日期为字符串，兼容Python 3.6"""
        return dt.strftime('%Y-%m-%dT%H:%M:%S')

    def _init_license_system(self):
        """初始化许可证系统"""
        # 检查许可证文件是否存在，如果不存在则创建
        if not os.path.exists(self.license_file):
            print(f"⚠️  许可证文件不存在，将在以下位置创建: {self.license_file}")
            self._create_trial_license()
        else:
            print(f"✅ 找到许可证文件: {self.license_file}")

    def _create_trial_license(self):
        """创建试用许可证"""
        try:
            create_time = datetime.now()
            
            try:
                expire_time = create_time + timedelta(days=self.trial_days)
                if expire_time.year > 9999:
                    expire_time = datetime(9999, 12, 31)
                    print("⚠️  许可证日期超出范围，已调整为9999-12-31")
            except OverflowError:
                expire_time = datetime(2099, 12, 31)
                print("⚠️  许可证日期溢出，已调整为2099-12-31")
            
            license_data = {
                "type": "PERMANENT",
                "create_time": self._format_datetime(create_time),
                "expire_time": self._format_datetime(expire_time),
                "machine_id": self._get_machine_id(),
                "signature": self._generate_signature("PERMANENT")
            }
            encrypted_data = self.crypto.encrypt(json.dumps(license_data))
            
            # 确保目录存在
            os.makedirs(os.path.dirname(self.license_file), exist_ok=True)
            
            with open(self.license_file, 'w') as f:
                f.write(encrypted_data)
            
            print(f"✅ 试用许可证已创建: {self.license_file}")
            
        except Exception as e:
            print(f"❌ 创建许可证失败: {e}")
            # 在临时目录创建许可证作为备选
            import tempfile
            temp_license = os.path.join(tempfile.gettempdir(), "mysql_inspector.lic")
            print(f"⚠️  尝试在临时目录创建许可证: {temp_license}")
            self.license_file = temp_license
            self._create_trial_license()

    def _get_machine_id(self):
        """获取机器标识"""
        try:
            machine_info = f"{platform.node()}-{platform.system()}-{platform.release()}"
            return hashlib.md5(machine_info.encode()).hexdigest()[:16]
        except:
            return "unknown_machine"

    def _generate_signature(self, license_type):
        """生成许可证签名"""
        if license_type == "PERMANENT":
            key = "ODB2024PERM"
        else:
            key = "ODB2024TRL"
        signature_data = f"{license_type}-{datetime.now().strftime('%Y%m%d')}-{key}"
        return hashlib.sha256(signature_data.encode()).hexdigest()

    def _verify_signature(self, license_data):
        """验证许可证签名"""
        try:
            expected_signature = self._generate_signature(license_data["type"])
            return license_data["signature"] == expected_signature
        except:
            return False

    def validate_license(self):
        """验证许可证有效性"""
        try:
            if not os.path.exists(self.license_file):
                return False, f"许可证文件不存在: {self.license_file}", 0

            with open(self.license_file, 'r') as f:
                encrypted_data = f.read().strip()
            
            if not encrypted_data:
                return False, "许可证文件为空", 0
            
            decrypted_data = self.crypto.decrypt(encrypted_data)
            license_data = json.loads(decrypted_data)

            if not self._verify_signature(license_data):
                return False, "许可证签名无效", 0

            expire_time = self._parse_datetime(license_data["expire_time"])
            remaining_days = (expire_time - datetime.now()).days

            if remaining_days < 0:
                return False, "许可证已过期", 0

            license_type = license_data.get("type", "PERMANENT")
            
            if license_type == "PERMANENT":
                return True, "永久版许可证有效", 99999
            else:
                return True, f"{license_type}版许可证有效，剩余 {remaining_days} 天", remaining_days

        except Exception as e:
            return False, f"许可证验证失败: {str(e)}", 0

    def get_license_info(self):
        """获取许可证信息"""
        is_valid, message, remaining_days = self.validate_license()
        return {
            "is_valid": is_valid,
            "message": message,
            "remaining_days": remaining_days,
            "license_file": self.license_file
        }
