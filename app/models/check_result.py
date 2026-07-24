"""
审查结果模型模块
定义审查问题、摘要、结果等模型
"""

from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class IssueSeverity(str, Enum):
    """
    问题严重等级枚举
    
    用于区分问题的紧急程度和处理优先级
    """
    CRITICAL = "critical"      # 严重 - 必须修改，无法通过审查
    WARNING = "warning"        # 警告 - 建议修改，可能影响性能或安全
    INFO = "info"              # 提示 - 仅供参考，最佳实践建议
    
    @classmethod
    def get_display_name(cls, severity: "IssueSeverity") -> str:
        """获取显示名称"""
        display_names = {
            cls.CRITICAL: "严重",
            cls.WARNING: "警告",
            cls.INFO: "提示"
        }
        return display_names.get(severity, "未知")
    
    @classmethod
    def get_priority(cls, severity: "IssueSeverity") -> int:
        """获取优先级数值（数值越小优先级越高）"""
        priorities = {
            cls.CRITICAL: 1,
            cls.WARNING: 2,
            cls.INFO: 3
        }
        return priorities.get(severity, 99)


class IssueCategory(str, Enum):
    """
    问题类别枚举
    
    用于对问题进行分类，便于统计和筛选
    """
    STRUCTURE = "structure"        # 结构问题
    REGULATION = "regulation"      # 法规问题
    SAFETY = "safety"              # 安全问题
    PROCESS = "process"            # 工艺问题
    DIMENSION = "dimension"        # 尺寸问题
    MATERIAL = "material"          # 材料问题
    WELD = "weld"                  # 焊接问题
    DRAWING = "drawing"            # 图纸规范问题
    OTHER = "other"                # 其他
    
    @classmethod
    def get_display_name(cls, category: "IssueCategory") -> str:
        """获取显示名称"""
        display_names = {
            cls.STRUCTURE: "结构",
            cls.REGULATION: "法规",
            cls.SAFETY: "安全",
            cls.PROCESS: "工艺",
            cls.DIMENSION: "尺寸",
            cls.MATERIAL: "材料",
            cls.WELD: "焊接",
            cls.DRAWING: "图纸规范",
            cls.OTHER: "其他"
        }
        return display_names.get(category, "未知")


class IssueLocation(BaseModel):
    """
    问题位置模型
    
    精确定位问题在图纸中的位置
    """
    description: Optional[str] = Field(
        default=None,
        description="位置描述",
        examples=["副车架第3根横梁", "罐体前封头"]
    )
    entity_id: Optional[str] = Field(
        default=None,
        description="关联实体ID"
    )
    layer_name: Optional[str] = Field(
        default=None,
        description="所在图层"
    )
    coordinates: Optional[tuple] = Field(
        default=None,
        description="坐标位置(X,Y,Z)"
    )
    bounding_box: Optional[Dict[str, float]] = Field(
        default=None,
        description="边界框(min_x, min_y, max_x, max_y)"
    )


class Issue(BaseModel):
    """
    审查问题模型
    
    描述一个具体的审查发现
    """
    id: str = Field(..., description="问题唯一ID")
    category: IssueCategory = Field(..., description="问题类别")
    severity: IssueSeverity = Field(..., description="严重等级")
    title: str = Field(..., description="问题标题", max_length=200)
    description: str = Field(..., description="详细描述")
    location: Optional[IssueLocation] = Field(
        default=None,
        description="位置信息"
    )
    detail: Optional[str] = Field(
        default=None,
        description="技术细节（计算过程、参数等）"
    )
    suggestion: str = Field(..., description="修改建议")
    standard: Optional[str] = Field(
        default=None,
        description="参考标准/规范",
        examples=["GB 1589-2016", "QC/T 453", "JB/T 5943"]
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="置信度(0-1)"
    )
    rule_id: Optional[str] = Field(
        default=None,
        description="触发该问题的规则ID"
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="创建时间"
    )
    
    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """验证ID格式"""
        if not v or len(v.strip()) < 3:
            raise ValueError("问题ID必须至少3个字符")
        return v.strip()
    
    def is_critical(self) -> bool:
        """是否为严重问题"""
        return self.severity == IssueSeverity.CRITICAL
    
    def get_full_description(self) -> str:
        """获取完整描述（用于报告）"""
        parts = [
            f"[{self.severity.value.upper()}] {self.title}",
            f"类别: {IssueCategory.get_display_name(self.category)}",
            f"描述: {self.description}"
        ]
        if self.detail:
            parts.append(f"技术细节: {self.detail}")
        if self.suggestion:
            parts.append(f"建议: {self.suggestion}")
        if self.standard:
            parts.append(f"参考标准: {self.standard}")
        return "\n".join(parts)


class CheckSummary(BaseModel):
    """
    审查摘要模型
    
    统计审查结果的整体情况
    """
    total_issues: int = Field(..., ge=0, description="问题总数")
    critical_count: int = Field(..., ge=0, description="严重问题数")
    warning_count: int = Field(..., ge=0, description="警告数")
    info_count: int = Field(..., ge=0, description="提示数")
    category_distribution: Dict[str, int] = Field(
        default_factory=dict,
        description="按类别分布"
    )
    rule_coverage: Optional[Dict[str, Any]] = Field(
        default=None,
        description="规则覆盖情况"
    )
    
    @field_validator("critical_count", "warning_count", "info_count")
    @classmethod
    def validate_counts(cls, v: int, info) -> int:
        """验证各等级计数非负"""
        if v < 0:
            raise ValueError("计数不能为负数")
        return v
    
    @field_validator("total_issues")
    @classmethod
    def validate_total(cls, v: int, info) -> int:
        """验证总数与分项之和一致"""
        data = info.data
        expected = data.get("critical_count", 0) + data.get("warning_count", 0) + data.get("info_count", 0)
        if v != expected:
            raise ValueError(f"总数({v})与分项之和({expected})不一致")
        return v
    
    def has_critical_issues(self) -> bool:
        """是否存在严重问题"""
        return self.critical_count > 0
    
    def get_severity_ratio(self) -> Dict[str, float]:
        """获取各等级占比"""
        if self.total_issues == 0:
            return {"critical": 0.0, "warning": 0.0, "info": 0.0}
        return {
            "critical": self.critical_count / self.total_issues,
            "warning": self.warning_count / self.total_issues,
            "info": self.info_count / self.total_issues
        }


class CheckResult(BaseModel):
    """
    审查结果完整模型
    
    包含一次完整审查的所有信息
    """
    drawing_id: str = Field(..., description="图纸ID")
    file_name: Optional[str] = Field(default=None, description="文件名")
    vehicle_type: Optional[str] = Field(default=None, description="识别车型")
    check_time: datetime = Field(
        default_factory=datetime.now,
        description="审查时间"
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="审查耗时(秒)"
    )
    summary: CheckSummary = Field(..., description="审查摘要")
    issues: List[Issue] = Field(default_factory=list, description="问题列表")
    passed: bool = Field(..., description="是否通过审查")
    score: float = Field(
        ...,
        ge=0,
        le=100,
        description="评分(0-100)"
    )
    remarks: Optional[str] = Field(
        default=None,
        description="备注/总体评价"
    )
    
    @field_validator("score")
    @classmethod
    def validate_score(cls, v: float, info) -> float:
        """验证评分合理性"""
        data = info.data
        passed = data.get("passed")
        
        # 如果有严重问题，分数不应超过60
        summary = data.get("summary")
        if summary and summary.has_critical_issues() and v > 60:
            raise ValueError("存在严重问题时，评分不应超过60分")
        
        # 通过审查的最低分数要求
        if passed and v < 60:
            raise ValueError("通过审查的评分不应低于60分")
        
        return round(v, 2)
    
    @field_validator("passed")
    @classmethod
    def validate_passed(cls, v: bool, info) -> bool:
        """验证通过状态"""
        data = info.data
        summary = data.get("summary")
        if summary and summary.has_critical_issues() and v:
            raise ValueError("存在严重问题时，审查不能通过")
        return v
    
    def get_issues_by_severity(self, severity: IssueSeverity) -> List[Issue]:
        """按严重等级筛选问题"""
        return [issue for issue in self.issues if issue.severity == severity]
    
    def get_issues_by_category(self, category: IssueCategory) -> List[Issue]:
        """按类别筛选问题"""
        return [issue for issue in self.issues if issue.category == category]
    
    def get_sorted_issues(self) -> List[Issue]:
        """获取排序后的问题列表（按严重等级）"""
        return sorted(
            self.issues,
            key=lambda x: IssueSeverity.get_priority(x.severity)
        )
    
    def generate_report_summary(self) -> str:
        """生成报告摘要文本"""
        lines = [
            f"图纸: {self.file_name or self.drawing_id}",
            f"审查时间: {self.check_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"识别车型: {self.vehicle_type or '未识别'}",
            f"审查结果: {'通过' if self.passed else '未通过'}",
            f"综合评分: {self.score:.1f}/100",
            "",
            "问题统计:",
            f"  - 严重: {self.summary.critical_count}",
            f"  - 警告: {self.summary.warning_count}",
            f"  - 提示: {self.summary.info_count}",
            f"  - 总计: {self.summary.total_issues}"
        ]
        return "\n".join(lines)
