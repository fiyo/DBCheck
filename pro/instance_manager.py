# -*- coding: utf-8 -*-
"""
DBCheck Pro Instance Manager
专业版多实例管理模块
支持实例分组、标签管理、批量巡检、汇总报告
"""

import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import hashlib


@dataclass
class DatabaseInstance:
    """数据库实例"""
    id: str
    name: str
    db_type: str  # mysql, postgresql, oracle, sqlserver, dm, tidb
    host: str
    port: int
    user: str
    password: str = ""  # 加密存储
    service_name: str = ""  # Oracle 专用
    tags: List[str] = None  # 标签列表
    group: str = "default"  # 分组
    enabled: bool = True
    description: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DatabaseInstance":
        """从字典创建"""
        return cls(**data)


class InstanceGroup:
    """实例分组"""

    def __init__(self, name: str, description: str = "", color: str = "#378ADD"):
        self.name = name
        self.description = description
        self.color = color
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "color": self.color,
            "created_at": self.created_at
        }


class InstanceManager:
    """实例管理器"""

    def __init__(self, data_dir: str = "pro_data"):
        self.data_dir = data_dir
        self.instances_file = os.path.join(data_dir, "instances.json")
        self.groups_file = os.path.join(data_dir, "groups.json")
        self.db_file = os.path.join(data_dir, "pro_history.db")

        # 确保数据目录存在
        os.makedirs(data_dir, exist_ok=True)

        # 初始化存储
        self._instances: Dict[str, DatabaseInstance] = {}
        self._groups: Dict[str, InstanceGroup] = {}
        self._load_data()

        # 初始化数据库
        self._init_database()

    def _load_data(self):
        """加载数据"""
        # 加载实例
        if os.path.exists(self.instances_file):
            try:
                with open(self.instances_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for inst_data in data.get("instances", []):
                        inst = DatabaseInstance.from_dict(inst_data)
                        self._instances[inst.id] = inst
            except Exception:
                pass

        # 加载分组
        if os.path.exists(self.groups_file):
            try:
                with open(self.groups_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for grp_data in data.get("groups", []):
                        grp = InstanceGroup(**grp_data)
                        self._groups[grp.name] = grp
            except Exception:
                pass

        # 默认分组
        if not self._groups:
            self._groups["default"] = InstanceGroup("default", "默认分组", "#888888")
            self._groups["production"] = InstanceGroup("production", "生产环境", "#E24B4A")
            self._groups["test"] = InstanceGroup("test", "测试环境", "#639922")

    def _save_data(self):
        """保存数据"""
        # 保存实例
        instances_data = {
            "instances": [inst.to_dict() for inst in self._instances.values()]
        }
        with open(self.instances_file, "w", encoding="utf-8") as f:
            json.dump(instances_data, f, indent=2, ensure_ascii=False)

        # 保存分组
        groups_data = {
            "groups": [grp.to_dict() for grp in self._groups.values()]
        }
        with open(self.groups_file, "w", encoding="utf-8") as f:
            json.dump(groups_data, f, indent=2, ensure_ascii=False)

    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        # 巡检历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inspection_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                instance_name TEXT,
                db_type TEXT,
                inspect_time TEXT,
                health_score INTEGER,
                risk_count INTEGER,
                risk_level TEXT,
                report_path TEXT,
                duration REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 实例健康趋势表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS instance_trend (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                date TEXT NOT NULL,
                health_score INTEGER,
                risk_count INTEGER,
                connection_time REAL,
                query_count INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(instance_id, date)
            )
        """)

        conn.commit()
        conn.close()

    def _generate_id(self, name: str, db_type: str) -> str:
        """生成唯一ID"""
        raw = f"{name}-{db_type}-{datetime.now().isoformat()}".encode()
        return hashlib.md5(raw).hexdigest()[:12]

    def add_instance(self, instance: DatabaseInstance) -> Dict[str, Any]:
        """添加实例"""
        if not instance.id:
            instance.id = self._generate_id(instance.name, instance.db_type)

        if instance.id in self._instances:
            return {"success": False, "message": "实例ID已存在"}

        self._instances[instance.id] = instance
        self._save_data()

        return {"success": True, "message": "实例添加成功", "instance_id": instance.id}

    def update_instance(self, instance_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """更新实例"""
        if instance_id not in self._instances:
            return {"success": False, "message": "实例不存在"}

        instance = self._instances[instance_id]
        for key, value in updates.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        instance.updated_at = datetime.now().isoformat()

        self._save_data()
        return {"success": True, "message": "实例更新成功"}

    def delete_instance(self, instance_id: str) -> Dict[str, Any]:
        """删除实例"""
        if instance_id not in self._instances:
            return {"success": False, "message": "实例不存在"}

        del self._instances[instance_id]
        self._save_data()
        return {"success": True, "message": "实例删除成功"}

    def get_instance(self, instance_id: str) -> Optional[DatabaseInstance]:
        """获取实例"""
        return self._instances.get(instance_id)

    def get_all_instances(self) -> List[DatabaseInstance]:
        """获取所有实例"""
        return list(self._instances.values())

    def get_instances_by_group(self, group: str) -> List[DatabaseInstance]:
        """按分组获取实例"""
        return [inst for inst in self._instances.values() if inst.group == group]

    def get_instances_by_tag(self, tag: str) -> List[DatabaseInstance]:
        """按标签获取实例"""
        return [inst for inst in self._instances.values() if tag in inst.tags]

    def get_instances_by_type(self, db_type: str) -> List[DatabaseInstance]:
        """按数据库类型获取实例"""
        return [inst for inst in self._instances.values() if inst.db_type == db_type]

    def get_enabled_instances(self) -> List[DatabaseInstance]:
        """获取启用的实例"""
        return [inst for inst in self._instances.values() if inst.enabled]

    # 分组管理
    def add_group(self, group: InstanceGroup) -> Dict[str, Any]:
        """添加分组"""
        if group.name in self._groups:
            return {"success": False, "message": "分组已存在"}

        self._groups[group.name] = group
        self._save_data()
        return {"success": True, "message": "分组添加成功"}

    def delete_group(self, group_name: str) -> Dict[str, Any]:
        """删除分组"""
        if group_name == "default":
            return {"success": False, "message": "默认分组不能删除"}

        if group_name in self._groups:
            # 将该分组的实例移到默认分组
            for inst in self._instances.values():
                if inst.group == group_name:
                    inst.group = "default"

            del self._groups[group_name]
            self._save_data()
            return {"success": True, "message": "分组删除成功"}

        return {"success": False, "message": "分组不存在"}

    def get_all_groups(self) -> List[InstanceGroup]:
        """获取所有分组"""
        return list(self._groups.values())

    # 统计信息
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = len(self._instances)
        enabled = len([i for i in self._instances.values() if i.enabled])

        # 按类型统计
        by_type = {}
        for inst in self._instances.values():
            by_type[inst.db_type] = by_type.get(inst.db_type, 0) + 1

        # 按分组统计
        by_group = {}
        for inst in self._instances.values():
            by_group[inst.group] = by_group.get(inst.group, 0) + 1

        return {
            "total_instances": total,
            "enabled_instances": enabled,
            "by_type": by_type,
            "by_group": by_group,
            "total_groups": len(self._groups)
        }

    # 巡检历史记录
    def record_inspection(
        self,
        instance_id: str,
        instance_name: str,
        db_type: str,
        health_score: int,
        risk_count: int,
        risk_level: str,
        report_path: str,
        duration: float
    ) -> Dict[str, Any]:
        """记录巡检历史"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        try:
            # 插入历史记录
            cursor.execute("""
                INSERT INTO inspection_history
                (instance_id, instance_name, db_type, inspect_time, health_score,
                 risk_count, risk_level, report_path, duration)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                instance_id, instance_name, db_type, datetime.now().isoformat(),
                health_score, risk_count, risk_level, report_path, duration
            ))

            # 更新趋势数据
            today = datetime.now().strftime("%Y-%m-%d")
            cursor.execute("""
                INSERT OR REPLACE INTO instance_trend
                (instance_id, date, health_score, risk_count)
                VALUES (?, ?, ?, ?)
            """, (instance_id, today, health_score, risk_count))

            conn.commit()
            return {"success": True, "message": "巡检记录已保存"}

        except Exception as e:
            conn.rollback()
            return {"success": False, "message": f"记录失败: {str(e)}"}
        finally:
            conn.close()

    def get_inspection_history(
        self,
        instance_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """获取巡检历史"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if instance_id:
            cursor.execute("""
                SELECT * FROM inspection_history
                WHERE instance_id = ?
                ORDER BY inspect_time DESC
                LIMIT ?
            """, (instance_id, limit))
        else:
            cursor.execute("""
                SELECT * FROM inspection_history
                ORDER BY inspect_time DESC
                LIMIT ?
            """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_instance_trend(self, instance_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """获取实例健康趋势"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM instance_trend
            WHERE instance_id = ?
            ORDER BY date DESC
            LIMIT ?
        """, (instance_id, days))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_global_health_score(self) -> int:
        """计算全局健康评分"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        # 获取最近一次巡检的每个实例的健康分
        cursor.execute("""
            SELECT instance_id, MAX(inspect_time) as latest, health_score
            FROM inspection_history
            GROUP BY instance_id
        """)

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return 0

        total_score = sum(row[2] for row in rows if row[2] is not None)
        return int(total_score / len(rows))

    # 批量操作
    def batch_add_from_csv(self, csv_content: str) -> Dict[str, Any]:
        """从CSV批量导入实例"""
        import csv
        import io

        added = 0
        errors = []

        reader = csv.DictReader(io.StringIO(csv_content))
        for row in reader:
            try:
                instance = DatabaseInstance(
                    id=self._generate_id(row.get("name", ""), row.get("db_type", "mysql")),
                    name=row.get("name", ""),
                    db_type=row.get("db_type", "mysql"),
                    host=row.get("host", ""),
                    port=int(row.get("port", 3306)),
                    user=row.get("user", ""),
                    password=row.get("password", ""),
                    service_name=row.get("service_name", ""),
                    tags=row.get("tags", "").split(","),
                    group=row.get("group", "default"),
                    description=row.get("description", "")
                )
                result = self.add_instance(instance)
                if result["success"]:
                    added += 1
                else:
                    errors.append(f"{row.get('name', 'unknown')}: {result['message']}")
            except Exception as e:
                errors.append(f"{row.get('name', 'unknown')}: {str(e)}")

        return {
            "success": True,
            "added": added,
            "errors": errors,
            "message": f"成功导入 {added} 个实例"
        }


# 全局单例
_instance_manager: Optional[InstanceManager] = None


def get_instance_manager() -> InstanceManager:
    """获取实例管理器单例"""
    global _instance_manager
    if _instance_manager is None:
        _instance_manager = InstanceManager()
    return _instance_manager
