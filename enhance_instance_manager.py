# -*- coding: utf-8 -*-
"""增强 instance_manager.py：密码加密、连接测试、CSV 导出"""
import os

fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pro', 'instance_manager.py')
content = open(fpath, 'r', encoding='utf-8').read()

# 1. 在 import hashlib 后插入加密 import
old_imports = "import hashlib\n\n"
new_imports = """import hashlib
import base64

# Fernet 密码加密
try:
    from cryptography.fernet import Fernet
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False
    Fernet = None

def _get_fernet():
    if not _FERNET_AVAILABLE:
        return None
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, '.db_key')
    if not os.path.exists(key_file):
        key = Fernet.generate_key()
        with open(key_file, 'wb') as f:
            f.write(key)
    else:
        with open(key_file, 'rb') as f:
            key = f.read()
    return Fernet(key)

def _encrypt_pwd(password: str) -> str:
    if not password:
        return password
    f = _get_fernet()
    if f is None:
        return password
    return base64.b64encode(f.encrypt(password.encode())).decode()

def _decrypt_pwd(encrypted: str) -> str:
    if not encrypted:
        return encrypted
    f = _get_fernet()
    if f is None:
        return encrypted
    try:
        return f.decrypt(base64.b64decode(encrypted.encode())).decode()
    except Exception:
        return encrypted

"""

if old_imports in content:
    content = content.replace(old_imports, new_imports, 1)
    print('[OK] 插入加密 import')
else:
    print('[WARN] 未找到 import hashlib 位置')

# 2. 找到 class InstanceManager 的位置，在 _generate_id 方法后插入新方法
# 找到 "    def add_instance(self, instance: DatabaseInstance)" 并在其前插入
old_add = "    def add_instance(self, instance: DatabaseInstance) -> Dict[str, Any]:"
new_add = """    def export_csv(self) -> str:
        \"\"\"导出所有实例为 CSV 格式（密码为空）\"\"\"
        import csv
        import io
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'name', 'db_type', 'host', 'port', 'user', 'password',
            'service_name', 'group', 'tags', 'description'
        ])
        writer.writeheader()
        for inst in self._instances.values():
            row = {
                'name': inst.name,
                'db_type': inst.db_type,
                'host': inst.host,
                'port': inst.port,
                'user': inst.user,
                'password': '',  # 不导出明文密码
                'service_name': inst.service_name,
                'group': inst.group,
                'tags': ','.join(inst.tags or []),
                'description': inst.description,
            }
            writer.writerow(row)
        return output.getvalue()

    def test_connection(self, instance_id: str) -> dict:
        \"\"\"测试实例连接，返回 {'ok': bool, 'message': str}\"\"\"
        inst = self._instances.get(instance_id)
        if not inst:
            return {'ok': False, 'message': '实例不存在'}

        password = _decrypt_pwd(inst.password)
        db_type = inst.db_type.lower()

        try:
            if db_type == 'mysql':
                import pymysql
                conn = pymysql.connect(
                    host=inst.host, port=inst.port,
                    user=inst.user, password=password,
                    connect_timeout=10,
                )
                conn.close()
                return {'ok': True, 'message': '连接成功 (MySQL %s:%d)' % (inst.host, inst.port)}

            elif db_type == 'postgresql':
                import psycopg2
                conn = psycopg2.connect(
                    host=inst.host, port=inst.port,
                    user=inst.user, password=password,
                    connect_timeout=10,
                )
                conn.close()
                return {'ok': True, 'message': '连接成功 (PostgreSQL %s:%d)' % (inst.host, inst.port)}

            elif db_type == 'oracle':
                import oracledb
                dsn = inst.service_name or '%s:%d/orcl' % (inst.host, inst.port)
                conn = oracledb.connect(user=inst.user, password=password, dsn=dsn)
                conn.close()
                return {'ok': True, 'message': '连接成功 (Oracle %s)' % dsn}

            elif db_type == 'sqlserver':
                import pyodbc
                driver = '{ODBC Driver 17 for SQL Server}'
                dsn = 'DRIVER=%s;SERVER=%s,%d;DATABASE=master;UID=%s;PWD=%s' % (
                    driver, inst.host, inst.port, inst.user, password)
                conn = pyodbc.connect(dsn, timeout=10)
                conn.close()
                return {'ok': True, 'message': '连接成功 (SQL Server %s:%d)' % (inst.host, inst.port)}

            elif db_type == 'dm':
                try:
                    import dmPython
                    dsn = '%s:%d' % (inst.host, inst.port)
                    conn = dmPython.connect(user=inst.user, password=password, server=dsn)
                    conn.close()
                    return {'ok': True, 'message': '连接成功 (DM %s:%d)' % (inst.host, inst.port)}
                except ImportError:
                    return {'ok': False, 'message': 'dmPython 驱动未安装'}

            elif db_type == 'tidb':
                import pymysql
                conn = pymysql.connect(
                    host=inst.host, port=inst.port,
                    user=inst.user, password=password,
                    connect_timeout=10,
                )
                conn.close()
                return {'ok': True, 'message': '连接成功 (TiDB %s:%d)' % (inst.host, inst.port)}

            else:
                return {'ok': False, 'message': '不支持的数据库类型: %s' % db_type}

        except ImportError as e:
            return {'ok': False, 'message': '驱动未安装: %s' % str(e)}
        except Exception as e:
            return {'ok': False, 'message': '连接失败: %s' % str(e)}

    def add_instance(self, instance: DatabaseInstance) -> Dict[str, Any]:
        \"\"\"添加实例（密码自动加密）\"\"\"
        if not instance.id:
            instance.id = self._generate_id(instance.name, instance.db_type)
        if instance.id in self._instances:
            return {"success": False, "message": "实例ID已存在"}
        # 加密密码
        instance.password = _encrypt_pwd(instance.password)
        self._instances[instance.id] = instance
        self._save_data()
        return {"success": True, "message": "实例添加成功", "instance_id": instance.id}

    def update_instance(self, instance_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        \"\"\"更新实例（密码变更时自动加密）\"\"\"
        if instance_id not in self._instances:
            return {"success": False, "message": "实例不存在"}
        instance = self._instances[instance_id]
        for key, value in updates.items():
            if hasattr(instance, key):
                # 密码变更时加密
                if key == 'password' and value:
                    value = _encrypt_pwd(value)
                setattr(instance, key, value)
        instance.updated_at = datetime.now().isoformat()
        self._save_data()
        return {"success": True, "message": "实例更新成功"}

    def get_all_instances(self, mask_password: bool = True) -> List[Dict]:
        \"\"\"获取所有实例，密码脱敏\"\"\"
        result = []
        for inst in self._instances.values():
            d = inst.to_dict()
            if mask_password and d.get('password'):
                d['password'] = '********'
            result.append(d)
        return result

    def get_instance(self, instance_id: str, mask_password: bool = True) -> Optional[Dict]:
        \"\"\"获取单个实例，密码脱敏\"\"\"
        inst = self._instances.get(instance_id)
        if not inst:
            return None
        d = inst.to_dict()
        if mask_password and d.get('password'):
            d['password'] = '********'
        return d

"""

if old_add in content:
    content = content.replace(old_add, new_add, 1)
    print('[OK] 替换 add_instance 方法')
else:
    print('[WARN] 未找到 add_instance 方法位置')

open(fpath, 'w', encoding='utf-8').write(content)
print('\n写入完成，验证中...')

# 验证语法
import subprocess
r = subprocess.run(['python', '-c', 'from pro.instance_manager import InstanceManager; print("语法 OK")'], capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__)))
print(r.stdout, r.stderr)
