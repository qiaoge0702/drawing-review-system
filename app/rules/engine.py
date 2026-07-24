"""
规则引擎模块
基于 GB 1589-2016 等标准进行硬校验

当前实现：
1. 外廓尺寸限值校验（GB 1589-2016）
2. 图纸规范检查（标题栏、比例、材料标注）
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field

from app.models.check_result import Issue, IssueSeverity, IssueCategory, IssueLocation

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# 规则定义
# ═══════════════════════════════════════════

class VehicleDimensionRule(BaseModel):
    """外廓尺寸规则"""
    max_overall_length_mm: float = 18000
    max_overall_width_mm: float = 2550
    max_overall_height_mm: float = 4000
    standards: List[str] = Field(default_factory=lambda: ["GB 1589-2016"])
    notes: str = "汽车及挂车外廓尺寸限值"


class DrawingStandardRule(BaseModel):
    """图纸规范规则"""
    title_block_required: bool = True
    scale_required: bool = True
    tolerance_table_required: bool = True
    weld_symbol_required: bool = True
    material_mark_required: bool = True
    dimension_complete: bool = True
    standards: List[str] = Field(default_factory=lambda: ["GB/T 14689", "GB/T 14690"])
    notes: str = "工程制图标准"


class RuleEngineConfig(BaseModel):
    """规则引擎配置"""
    vehicle_dimensions: VehicleDimensionRule = Field(default_factory=VehicleDimensionRule)
    drawing_standards: DrawingStandardRule = Field(default_factory=DrawingStandardRule)


# ═══════════════════════════════════════════
# 规则引擎
# ═══════════════════════════════════════════

class RuleEngine:
    """
    规则引擎
    
    从 JSON 加载规则，对解析后的图纸数据进行硬校验
    """
    
    def __init__(self, rules_path: Optional[Path] = None):
        self.rules_path = rules_path or Path(__file__).parent / "default_rules.json"
        self.config = RuleEngineConfig()
        self._load_rules()
    
    def _load_rules(self):
        """加载规则文件"""
        if not self.rules_path.exists():
            logger.warning(f"规则文件不存在: {self.rules_path}，使用默认规则")
            return
        
        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 加载外廓尺寸规则
            vd = data.get("vehicle_dimensions", {})
            self.config.vehicle_dimensions = VehicleDimensionRule(**vd)
            
            # 加载图纸规范规则
            ds = data.get("drawing_standards", {})
            self.config.drawing_standards = DrawingStandardRule(**ds)
            
            logger.info(f"规则加载成功: {self.rules_path.name}")
            
        except Exception as e:
            logger.error(f"规则加载失败: {e}，使用默认规则")
    
    def check_drawing(self, drawing_info: Dict[str, Any]) -> List[Issue]:
        """
        对图纸执行所有规则检查
        
        Args:
            drawing_info: 图纸信息字典，包含 extents, metadata, entity_counts 等
            
        Returns:
            问题列表
        """
        issues: List[Issue] = []
        
        # 1. 外廓尺寸校验
        issues.extend(self._check_vehicle_dimensions(drawing_info))
        
        # 2. 图纸规范检查
        issues.extend(self._check_drawing_standards(drawing_info))
        
        logger.info(f"规则检查完成: {len(issues)} 个问题")
        return issues
    
    def _check_vehicle_dimensions(self, drawing_info: Dict[str, Any]) -> List[Issue]:
        """外廓尺寸校验 (GB 1589-2016)"""
        issues: List[Issue] = []
        rule = self.config.vehicle_dimensions
        extents = drawing_info.get("extents", {})
        
        width = extents.get("width", 0)
        height = extents.get("height", 0)
        
        # 长度检查（图纸宽度通常对应车辆长度）
        if width > rule.max_overall_length_mm:
            issues.append(Issue(
                id="RULE_DIM_001",
                severity=IssueSeverity.CRITICAL,
                category=IssueCategory.REGULATION,
                title="整车长度超限",
                description=f"图纸宽度 {width:.0f}mm 超过 GB 1589-2016 限值 {rule.max_overall_length_mm:.0f}mm",
                suggestion=f"调整设计使长度 ≤ {rule.max_overall_length_mm}mm，或申请超限运输许可",
                standard="GB 1589-2016",
                confidence=1.0,
                location=IssueLocation(
                    x=extents.get("min_x", 0) + width / 2,
                    y=extents.get("min_y", 0) + height / 2,
                ),
            ))
        
        # 宽度检查
        if height > rule.max_overall_width_mm:
            issues.append(Issue(
                id="RULE_DIM_002",
                severity=IssueSeverity.CRITICAL,
                category=IssueCategory.REGULATION,
                title="整车宽度超限",
                description=f"图纸高度 {height:.0f}mm 超过 GB 1589-2016 限值 {rule.max_overall_width_mm:.0f}mm",
                suggestion=f"调整设计使宽度 ≤ {rule.max_overall_width_mm}mm",
                standard="GB 1589-2016",
                confidence=1.0,
            ))
        
        # 高度检查（如果有 Z 向尺寸）
        # 注：2D 图纸通常不体现整车高度，需结合多视图判断
        
        return issues
    
    def _check_drawing_standards(self, drawing_info: Dict[str, Any]) -> List[Issue]:
        """图纸规范检查"""
        issues: List[Issue] = []
        rule = self.config.drawing_standards
        metadata = drawing_info.get("metadata", {})
        
        # 标题栏检查
        if rule.title_block_required:
            title = metadata.get("title", "")
            if not title or title.strip() == "":
                issues.append(Issue(
                    id="RULE_STD_001",
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.DRAWING,
                    title="缺少图样名称",
                    description="标题栏中未检测到图样名称",
                    suggestion="在标题栏中填写图样名称",
                    standard="GB/T 14689",
                    confidence=0.9,
                ))
        
        # 比例检查
        if rule.scale_required:
            scale = metadata.get("scale", "")
            if not scale or scale.strip() == "":
                issues.append(Issue(
                    id="RULE_STD_002",
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.DRAWING,
                    title="缺少比例标注",
                    description="标题栏中未检测到比例信息",
                    suggestion="在标题栏中填写绘图比例",
                    standard="GB/T 14690",
                    confidence=0.9,
                ))
        
        # 材料标注检查
        if rule.material_mark_required:
            material = metadata.get("material", "")
            if not material or material.strip() == "":
                issues.append(Issue(
                    id="RULE_STD_003",
                    severity=IssueSeverity.INFO,
                    category=IssueCategory.MATERIAL,
                    title="缺少材料标注",
                    description="标题栏中未检测到材料信息",
                    suggestion="在标题栏中填写材料牌号（如 Q345B）",
                    standard="GB/T 14689",
                    confidence=0.8,
                ))
        
        # 尺寸标注完整性检查
        if rule.dimension_complete:
            entity_counts = drawing_info.get("entity_counts", {})
            dim_count = entity_counts.get("dimension", 0)
            if dim_count == 0:
                issues.append(Issue(
                    id="RULE_STD_004",
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.DIMENSION,
                    title="缺少尺寸标注",
                    description="图纸中未检测到任何尺寸标注",
                    suggestion="添加必要的定形尺寸和定位尺寸",
                    standard="GB/T 4458",
                    confidence=0.95,
                ))
        
        return issues


# ═══════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════

_engine: Optional[RuleEngine] = None


def get_rule_engine() -> RuleEngine:
    """获取全局规则引擎实例"""
    global _engine
    if _engine is None:
        _engine = RuleEngine()
    return _engine


def check_drawing(drawing_info: Dict[str, Any]) -> List[Issue]:
    """对图纸执行规则检查（便捷函数）"""
    return get_rule_engine().check_drawing(drawing_info)
