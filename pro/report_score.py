# -*- coding: utf-8 -*-
"""
DBCheck Pro Report Score
专业版报告评分系统
为每次巡检计算 0-100 健康分，输出评分报告
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
import json


@dataclass
class ScoreItem:
    """评分项"""
    category: str  # 评分类别
    name: str      # 指标名称
    score: int     # 得分 (0-100)
    weight: float  # 权重
    details: str   # 详细说明
    suggestion: str = ""  # 优化建议


@dataclass
class ScoreReport:
    """评分报告"""
    instance_id: str
    instance_name: str
    db_type: str
    total_score: int           # 总分 (0-100)
    risk_level: str             # 风险等级: critical/high/medium/low/none
    risk_count: int             # 风险项总数
    risk_breakdown: Dict[str, int]  # 风险分布
    categories: List[ScoreItem]  # 各分类评分
    summary: str                # 总结
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "instance_name": self.instance_name,
            "db_type": self.db_type,
            "total_score": self.total_score,
            "risk_level": self.risk_level,
            "risk_count": self.risk_count,
            "risk_breakdown": self.risk_breakdown,
            "categories": [
                {
                    "category": c.category,
                    "name": c.name,
                    "score": c.score,
                    "weight": c.weight,
                    "details": c.details,
                    "suggestion": c.suggestion
                }
                for c in self.categories
            ],
            "summary": self.summary,
            "created_at": self.created_at
        }


class ReportScorer:
    """报告评分器"""

    # 权重配置
    WEIGHTS = {
        "performance": 0.25,      # 性能 25%
        "security": 0.25,         # 安全 25%
        "configuration": 0.20,    # 配置 20%
        "capacity": 0.15,         # 容量 15%
        "availability": 0.15,     # 可用性 15%
    }

    # 风险等级阈值
    RISK_THRESHOLDS = {
        "critical": (0, 30),      # 0-30 严重
        "high": (31, 50),         # 31-50 高危
        "medium": (51, 70),       # 51-70 中危
        "low": (71, 85),          # 71-85 低危
        "none": (86, 100),        # 86-100 健康
    }

    def __init__(self):
        self.category_scores: Dict[str, List[ScoreItem]] = {}

    def add_item(
        self,
        category: str,
        name: str,
        score: int,
        details: str,
        suggestion: str = "",
        weight: float = None
    ) -> "ReportScorer":
        """添加评分项"""
        if weight is None:
            weight = self.WEIGHTS.get(category, 0.1)

        item = ScoreItem(
            category=category,
            name=name,
            score=max(0, min(100, score)),  # 限制在 0-100
            weight=weight,
            details=details,
            suggestion=suggestion
        )

        if category not in self.category_scores:
            self.category_scores[category] = []
        self.category_scores[category].append(item)

        return self

    def calculate_total_score(self) -> int:
        """计算总分"""
        total_weighted_score = 0
        total_weight = 0

        for category, items in self.category_scores.items():
            if not items:
                continue

            category_weight = self.WEIGHTS.get(category, 0.1)
            category_avg = sum(item.score for item in items) / len(items)

            total_weighted_score += category_avg * category_weight
            total_weight += category_weight

        if total_weight == 0:
            return 0

        # 归一化
        return int(total_weighted_score / total_weight * (1 / (total_weight / len(self.WEIGHTS))))

    def get_category_score(self, category: str) -> int:
        """获取分类得分"""
        items = self.category_scores.get(category, [])
        if not items:
            return 0
        return int(sum(item.score for item in items) / len(items))

    def count_risks(self) -> Dict[str, int]:
        """统计风险项"""
        breakdown = {
            "critical": 0,  # 0-30分
            "high": 0,      # 31-50分
            "medium": 0,    # 51-70分
            "low": 0,       # 71-85分
            "passed": 0,   # 86-100分
        }

        for items in self.category_scores.values():
            for item in items:
                if item.score <= 30:
                    breakdown["critical"] += 1
                elif item.score <= 50:
                    breakdown["high"] += 1
                elif item.score <= 70:
                    breakdown["medium"] += 1
                elif item.score <= 85:
                    breakdown["low"] += 1
                else:
                    breakdown["passed"] += 1

        return breakdown

    def get_risk_level(self, total_score: int) -> str:
        """根据总分获取风险等级"""
        for level, (min_score, max_score) in self.RISK_THRESHOLDS.items():
            if min_score <= total_score <= max_score:
                return level
        return "none"

    def get_total_risk_count(self) -> int:
        """获取总风险项数量"""
        breakdown = self.count_risks()
        return breakdown["critical"] + breakdown["high"] + breakdown["medium"]

    def generate_report(
        self,
        instance_id: str,
        instance_name: str,
        db_type: str
    ) -> ScoreReport:
        """生成评分报告"""
        total_score = self.calculate_total_score()
        risk_level = self.get_risk_level(total_score)
        risk_breakdown = self.count_risks()

        # 收集所有评分项
        all_items = []
        for items in self.category_scores.values():
            all_items.extend(items)

        # 生成总结
        summary = self._generate_summary(total_score, risk_level, risk_breakdown)

        return ScoreReport(
            instance_id=instance_id,
            instance_name=instance_name,
            db_type=db_type,
            total_score=total_score,
            risk_level=risk_level,
            risk_count=self.get_total_risk_count(),
            risk_breakdown=risk_breakdown,
            categories=all_items,
            summary=summary
        )

    def _generate_summary(
        self,
        total_score: int,
        risk_level: str,
        risk_breakdown: Dict[str, int]
    ) -> str:
        """生成总结"""
        level_descriptions = {
            "critical": "数据库健康状况堪忧，存在严重风险，必须立即处理！",
            "high": "数据库存在较多风险项，需要尽快优化和修复。",
            "medium": "数据库整体运行正常，但存在一些需要关注的优化点。",
            "low": "数据库健康状况良好，仅有少量可优化项。",
            "none": "数据库健康状况优秀，所有指标均在理想范围内。",
        }

        summary_parts = [level_descriptions.get(risk_level, "")]

        # 添加风险详情
        if risk_breakdown["critical"] > 0:
            summary_parts.append(f"其中严重问题 {risk_breakdown['critical']} 项，")
        if risk_breakdown["high"] > 0:
            summary_parts.append(f"高危问题 {risk_breakdown['high']} 项，")

        if summary_parts[-1].endswith("，"):
            summary_parts[-1] = summary_parts[-1][:-1] + "。"

        summary_parts.append(f"综合评分 {total_score} 分。")

        return "".join(summary_parts)

    def reset(self) -> "ReportScorer":
        """重置评分器"""
        self.category_scores = {}
        return self


class InspectionDataScorer:
    """巡检数据评分器 - 从巡检结果数据计算评分"""

    def __init__(self):
        self.scorer = ReportScorer()

    def score_from_inspection_data(
        self,
        instance_id: str,
        instance_name: str,
        db_type: str,
        inspection_data: Dict[str, Any]
    ) -> ScoreReport:
        """从巡检数据生成评分报告"""

        # 性能评分
        self._score_performance(inspection_data)

        # 安全评分
        self._score_security(inspection_data)

        # 配置评分
        self._score_configuration(inspection_data)

        # 容量评分
        self._score_capacity(inspection_data)

        # 可用性评分
        self._score_availability(inspection_data)

        return self.scorer.generate_report(instance_id, instance_name, db_type)

    def _score_performance(self, data: Dict[str, Any]):
        """性能评分"""
        # 连接池使用率
        conn_usage = data.get("connection_pool_usage", 0)
        if conn_usage > 80:
            self.scorer.add_item(
                "performance",
                "连接池使用率",
                max(0, 100 - conn_usage),
                f"当前连接池使用率: {conn_usage}%",
                "建议增加连接池大小或优化慢查询"
            )
        elif conn_usage > 50:
            self.scorer.add_item(
                "performance",
                "连接池使用率",
                100 - int((conn_usage - 50) * 2),
                f"当前连接池使用率: {conn_usage}%",
                "使用率适中，可持续关注"
            )
        else:
            self.scorer.add_item(
                "performance",
                "连接池使用率",
                100,
                f"当前连接池使用率: {conn_usage}%",
                "使用率良好"
            )

        # 慢查询数量
        slow_count = data.get("slow_query_count", 0)
        if slow_count > 100:
            self.scorer.add_item(
                "performance",
                "慢查询数量",
                max(0, 30 - (slow_count - 100) // 10),
                f"慢查询数量: {slow_count}",
                "慢查询过多，建议优化或创建索引"
            )
        elif slow_count > 10:
            self.scorer.add_item(
                "performance",
                "慢查询数量",
                max(0, 70 - slow_count),
                f"慢查询数量: {slow_count}",
                "存在少量慢查询，建议关注"
            )
        else:
            self.scorer.add_item(
                "performance",
                "慢查询数量",
                100 if slow_count == 0 else 90,
                f"慢查询数量: {slow_count}",
                "慢查询数量正常"
            )

        # 缓存命中率
        cache_hit = data.get("cache_hit_ratio", 0)
        if cache_hit < 70:
            self.scorer.add_item(
                "performance",
                "缓存命中率",
                cache_hit,
                f"缓存命中率: {cache_hit}%",
                "建议增加缓存或优化查询模式"
            )
        elif cache_hit < 85:
            self.scorer.add_item(
                "performance",
                "缓存命中率",
                70 + (cache_hit - 70),
                f"缓存命中率: {cache_hit}%",
                "命中率一般，可适当优化"
            )
        else:
            self.scorer.add_item(
                "performance",
                "缓存命中率",
                100,
                f"缓存命中率: {cache_hit}%",
                "命中率良好"
            )

    def _score_security(self, data: Dict[str, Any]):
        """安全评分"""
        # 密码策略
        password_policy = data.get("password_policy_enabled", True)
        weak_passwords = data.get("weak_password_count", 0)

        if not password_policy:
            self.scorer.add_item(
                "security",
                "密码策略",
                20,
                "密码策略未启用",
                "建议启用密码策略，设置最小长度、复杂度要求"
            )
        elif weak_passwords > 0:
            self.scorer.add_item(
                "security",
                "弱密码检测",
                max(0, 50 - weak_passwords * 5),
                f"发现 {weak_passwords} 个弱密码",
                "建议立即修改弱密码"
            )
        else:
            self.scorer.add_item(
                "security",
                "密码策略",
                100,
                "密码策略已启用且无弱密码",
                "密码安全状况良好"
            )

        # 权限审计
        excessive_privs = data.get("excessive_privileges_count", 0)
        if excessive_privs > 10:
            self.scorer.add_item(
                "security",
                "权限审计",
                max(0, 30 - (excessive_privs - 10)),
                f"发现 {excessive_privs} 个过度授权账户",
                "建议审查并回收不必要的权限"
            )
        elif excessive_privs > 0:
            self.scorer.add_item(
                "security",
                "权限审计",
                max(0, 70 - excessive_privs * 3),
                f"发现 {excessive_privs} 个过度授权账户",
                "建议定期审查权限"
            )
        else:
            self.scorer.add_item(
                "security",
                "权限审计",
                100,
                "未发现过度授权账户",
                "权限分配合理"
            )

        # 敏感数据
        sensitive_exposed = data.get("sensitive_data_exposed", False)
        if sensitive_exposed:
            self.scorer.add_item(
                "security",
                "敏感数据",
                40,
                "发现未加密的敏感字段",
                "建议对敏感字段加密或脱敏"
            )
        else:
            self.scorer.add_item(
                "security",
                "敏感数据",
                100,
                "敏感数据保护良好",
                "未发现敏感数据泄露"
            )

    def _score_configuration(self, data: Dict[str, Any]):
        """配置评分"""
        # 配置基线合规
        baseline_compliance = data.get("baseline_compliance", 100)
        if baseline_compliance < 50:
            self.scorer.add_item(
                "configuration",
                "基线合规率",
                baseline_compliance,
                f"基线合规率: {baseline_compliance}%",
                "存在多项配置偏离基线，建议立即修复"
            )
        elif baseline_compliance < 80:
            self.scorer.add_item(
                "configuration",
                "基线合规率",
                baseline_compliance,
                f"基线合规率: {baseline_compliance}%",
                "部分配置偏离基线，建议逐步优化"
            )
        else:
            self.scorer.add_item(
                "configuration",
                "基线合规率",
                baseline_compliance,
                f"基线合规率: {baseline_compliance}%",
                "配置符合基线要求"
            )

        # 索引健康
        missing_index_count = data.get("missing_index_count", 0)
        duplicate_index_count = data.get("duplicate_index_count", 0)

        total_index_issues = missing_index_count + duplicate_index_count
        if total_index_issues > 20:
            self.scorer.add_item(
                "configuration",
                "索引健康",
                max(0, 30 - (total_index_issues - 20)),
                f"缺失索引: {missing_index_count}, 冗余索引: {duplicate_index_count}",
                "索引问题较多，建议优化索引结构"
            )
        elif total_index_issues > 5:
            self.scorer.add_item(
                "configuration",
                "索引健康",
                max(0, 70 - total_index_issues * 2),
                f"缺失索引: {missing_index_count}, 冗余索引: {duplicate_index_count}",
                "存在少量索引问题，可逐步优化"
            )
        else:
            self.scorer.add_item(
                "configuration",
                "索引健康",
                100 if total_index_issues == 0 else 90,
                f"缺失索引: {missing_index_count}, 冗余索引: {duplicate_index_count}",
                "索引结构良好"
            )

    def _score_capacity(self, data: Dict[str, Any]):
        """容量评分"""
        # 表空间使用率
        tablespace_usage = data.get("tablespace_usage", 0)
        if tablespace_usage > 90:
            self.scorer.add_item(
                "capacity",
                "表空间使用率",
                max(0, 100 - (tablespace_usage - 90) * 10),
                f"表空间使用率: {tablespace_usage}%",
                "容量即将耗尽，必须立即扩容或清理"
            )
        elif tablespace_usage > 70:
            self.scorer.add_item(
                "capacity",
                "表空间使用率",
                100 - (tablespace_usage - 70),
                f"表空间使用率: {tablespace_usage}%",
                "容量使用较高，建议规划扩容"
            )
        else:
            self.scorer.add_item(
                "capacity",
                "表空间使用率",
                100,
                f"表空间使用率: {tablespace_usage}%",
                "容量充足"
            )

        # 连接数使用率
        max_connections = data.get("max_connections", 100)
        current_connections = data.get("current_connections", 0)
        if max_connections > 0:
            conn_ratio = (current_connections / max_connections) * 100
        else:
            conn_ratio = 0

        if conn_ratio > 80:
            self.scorer.add_item(
                "capacity",
                "最大连接数使用率",
                max(0, 100 - (conn_ratio - 80)),
                f"连接数使用率: {conn_ratio:.1f}% ({current_connections}/{max_connections})",
                "连接数接近上限，建议增加 max_connections"
            )
        else:
            self.scorer.add_item(
                "capacity",
                "最大连接数使用率",
                100,
                f"连接数使用率: {conn_ratio:.1f}% ({current_connections}/{max_connections})",
                "连接数充足"
            )

    def _score_availability(self, data: Dict[str, Any]):
        """可用性评分"""
        # 数据库版本
        version = data.get("version", "")
        eol_version = data.get("eol_version", False)

        if eol_version:
            self.scorer.add_item(
                "availability",
                "版本支持状态",
                20,
                f"当前版本 {version} 已停止支持",
                "建议升级到支持的版本以获取安全更新"
            )
        else:
            self.scorer.add_item(
                "availability",
                "版本支持状态",
                100,
                f"当前版本 {version} 处于支持周期内",
                "版本状态正常"
            )

        # 主从复制状态 (MySQL/PostgreSQL)
        replication_status = data.get("replication_status", "unknown")
        if replication_status == "ok":
            self.scorer.add_item(
                "availability",
                "主从复制状态",
                100,
                "主从复制状态正常",
                "复制健康"
            )
        elif replication_status == "lag":
            self.scorer.add_item(
                "availability",
                "主从复制状态",
                60,
                "主从复制存在延迟",
                "建议检查从库延迟原因"
            )
        elif replication_status == "failed":
            self.scorer.add_item(
                "availability",
                "主从复制状态",
                10,
                "主从复制已断开",
                "必须立即处理复制故障"
            )
        # 未配置复制的情况不算扣分

        # 备份状态
        backup_enabled = data.get("backup_enabled", True)
        backup_age_days = data.get("backup_age_days", 0)

        if not backup_enabled:
            self.scorer.add_item(
                "availability",
                "备份策略",
                0,
                "未配置自动备份",
                "必须配置备份策略"
            )
        elif backup_age_days > 7:
            self.scorer.add_item(
                "availability",
                "备份策略",
                max(0, 80 - backup_age_days * 5),
                f"最新备份时间: {backup_age_days} 天前",
                "建议增加备份频率"
            )
        else:
            self.scorer.add_item(
                "availability",
                "备份策略",
                100,
                f"最新备份时间: {backup_age_days} 天前",
                "备份策略正常"
            )


# 评分结果格式化
def format_score_report(report: ScoreReport) -> str:
    """格式化评分报告为文本"""
    lines = [
        "=" * 50,
        "DBCheck Pro 健康评分报告",
        "=" * 50,
        f"实例名称: {report.instance_name}",
        f"数据库类型: {report.db_type}",
        f"评分时间: {report.created_at}",
        "-" * 50,
        f"【综合评分】{report.total_score} 分 ({report.risk_level.upper()})",
        f"风险项总数: {report.risk_count}",
        f"  - 严重: {report.risk_breakdown.get('critical', 0)}",
        f"  - 高危: {report.risk_breakdown.get('high', 0)}",
        f"  - 中危: {report.risk_breakdown.get('medium', 0)}",
        f"  - 低危: {report.risk_breakdown.get('low', 0)}",
        "-" * 50,
        "【评分详情】",
    ]

    current_category = ""
    for item in report.categories:
        if item.category != current_category:
            current_category = item.category
            lines.append(f"\n>> {current_category.upper()}")
            lines.append("-" * 30)

        status = "✓" if item.score >= 70 else "✗"
        lines.append(f"{status} {item.name}: {item.score}分")
        lines.append(f"   {item.details}")
        if item.suggestion:
            lines.append(f"   建议: {item.suggestion}")

    lines.extend([
        "-" * 50,
        "【总结】",
        report.summary,
        "=" * 50,
    ])

    return "\n".join(lines)
