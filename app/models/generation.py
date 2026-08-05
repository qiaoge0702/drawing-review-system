"""
图纸生成相关数据模型

复用现有模型体系，扩展生成流程所需的数据结构。
"""

from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field, field_validator


class DrawingType(str, Enum):
    """图纸类型"""
    TOTAL_ASSEMBLY = "total_assembly"      # 总装配图
    SUB_ASSEMBLY = "sub_assembly"          # 部件装配图
    PART = "part"                          # 零件图
    WELDMENT = "weldment"                  # 焊接件图


class StepName(str, Enum):
    """生成步骤名称"""
    SW_LOAD = "sw_load"                    # 1. 3D模型加载
    GEOMETRY_PARSE = "geometry_parse"      # 2. 几何解析
    VIEW_PROJECT = "view_project"          # 3. 视图投影
    DIMENSION = "dimension"                # 4. 尺寸标注
    BOM_GENERATE = "bom_generate"          # 5. BOM生成
    TECH_REQUIREMENT = "tech_requirement"  # 6. 技术要求
    DXF_BUILD = "dxf_build"                # 7. DXF构建
    REVIEW = "review"                      # 8. 审查闭环


class StepStatus(str, Enum):
    """步骤状态"""
    PENDING = "pending"                    # 等待执行
    RUNNING = "running"                    # 执行中
    COMPLETED = "completed"                # 已完成
    ERROR = "error"                        # 执行失败
    SKIPPED = "skipped"                    # 已跳过


class PipelineState(str, Enum):
    """流水线整体状态"""
    QUEUED = "queued"                      # 排队中
    RUNNING = "running"                    # 运行中
    PAUSED = "paused"                      # 已暂停
    COMPLETED = "completed"                # 已完成
    ERROR = "error"                        # 出错


class ArtifactType(str, Enum):
    """产物类型"""
    JSON = "json"
    SVG = "svg"
    PNG = "png"
    DXF = "dxf"
    TXT = "txt"


class Artifact(BaseModel):
    """步骤产物"""
    id: str = Field(..., description="产物唯一ID")
    name: str = Field(..., description="产物名称")
    type: ArtifactType = Field(..., description="产物类型")
    path: str = Field(..., description="文件路径")
    size: int = Field(default=0, ge=0, description="文件大小(字节)")
    checksum: Optional[str] = Field(default=None, description="SHA256校验和")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    def verify(self) -> bool:
        """校验产物完整性"""
        if not self.path:
            return False
        p = Path(self.path)
        if not p.exists():
            return False
        if self.size > 0 and p.stat().st_size != self.size:
            return False
        return True


class StepResult(BaseModel):
    """步骤执行结果"""
    step: int = Field(..., ge=1, le=8, description="步骤编号")
    name: StepName = Field(..., description="步骤名称")
    status: StepStatus = Field(default=StepStatus.PENDING)
    duration_ms: int = Field(default=0, ge=0, description="执行耗时(ms)")
    artifacts: List[Artifact] = Field(default_factory=list)
    output_data: Optional[Dict[str, Any]] = Field(default=None, description="输出数据")
    logs: List[str] = Field(default_factory=list)
    error: Optional[str] = Field(default=None)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    execution_count: int = Field(default=0, ge=0, description="执行次数")
    
    @property
    def is_success(self) -> bool:
        return self.status == StepStatus.COMPLETED
    
    @property
    def is_failed(self) -> bool:
        return self.status == StepStatus.ERROR


class TaskConfig(BaseModel):
    """生成任务配置"""
    drawing_type: DrawingType = Field(default=DrawingType.WELDMENT)
    target_format: Literal["dxf", "dwg"] = Field(default="dxf")
    views: List[str] = Field(default_factory=lambda: ["front", "top", "left"])
    scale: str = Field(default="auto", description="auto 或具体比例如 1:2")
    include_bom: bool = Field(default=True)
    include_tech_requirements: bool = Field(default=True)
    tolerance_level: Literal["rough", "normal", "precise"] = Field(default="normal")

    # B-M1+ 用户覆盖字段（设计文档 §3.x：GenerateRequest 透传至 Step3 策略）
    part_type_override: Optional[str] = Field(
        default=None,
        description="零件类型强制覆盖（standard_part/beam/plate/weldment/assembly）"
    )
    views_override: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="视图列表覆盖：按 id 匹配 → 字段级 patch / 新增 id 追加 / 显式 remove 删除"
    )
    layout_mode: Optional[Literal["auto", "manual"]] = Field(
        default=None,
        description="全局布局策略：auto=约束布局自动填充，manual=用户辅助指定位置"
    )
    positions_override: Optional[Dict[str, List[float]]] = Field(
        default=None,
        description="绝对定位覆盖：{视图id: [x, y]}（图纸 mm），绕过约束布局"
    )
    projection_type_override: Optional[Literal["first_angle", "third_angle"]] = Field(
        default=None,
        description="投影类型覆盖：first_angle=GB第一角（默认），third_angle=第三角"
    )

    @field_validator("views")
    @classmethod
    def validate_views(cls, v: List[str]) -> List[str]:
        allowed = ["front", "top", "left", "right", "back", "bottom", "section"]
        for view in v:
            if view not in allowed:
                raise ValueError(f"不支持的视图类型: {view}，允许: {allowed}")
        return v

    @field_validator("part_type_override")
    @classmethod
    def validate_part_type_override(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"standard_part", "beam", "plate", "weldment", "assembly"}
        if v not in allowed:
            raise ValueError(f"不支持的零件类型覆盖: {v}，允许: {sorted(allowed)}")
        return v

    @field_validator("positions_override")
    @classmethod
    def validate_positions_override(
        cls, v: Optional[Dict[str, List[float]]]
    ) -> Optional[Dict[str, List[float]]]:
        if v is None:
            return v
        for view_id, pos in v.items():
            if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                raise ValueError(
                    f"positions_override[{view_id!r}] 必须为 [x, y] 二元组，实际: {pos!r}"
                )
            for coord in pos:
                if not isinstance(coord, (int, float)):
                    raise ValueError(
                        f"positions_override[{view_id!r}] 坐标必须为数值，实际: {coord!r}"
                    )
        return v


class TaskResult(BaseModel):
    """生成任务结果"""
    task_id: str = Field(..., description="任务唯一ID")
    status: PipelineState = Field(default=PipelineState.QUEUED)
    progress: int = Field(default=0, ge=0, le=100, description="总进度 0-100")
    current_step: int = Field(default=0, ge=0, le=8)
    steps: List[StepResult] = Field(default_factory=list)
    config: TaskConfig = Field(default_factory=TaskConfig)
    source_file: str = Field(..., description="源SW文件路径")
    output_file: Optional[str] = Field(default=None, description="输出文件路径")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    error: Optional[str] = Field(default=None)
    total_duration_ms: int = Field(default=0, ge=0)
    
    @property
    def is_completed(self) -> bool:
        return self.status == PipelineState.COMPLETED
    
    @property
    def is_failed(self) -> bool:
        return self.status == PipelineState.ERROR


class GenerateSettings(BaseModel):
    """生成系统设置"""
    auto_advance: bool = Field(default=True, description="完成一步自动下一步")
    show_intermediate: bool = Field(default=True, description="显示中间产物")
    preview_quality: Literal["low", "medium", "high"] = Field(default="medium")
    max_concurrent_steps: int = Field(default=1, ge=1, le=4)
    sw_timeout_seconds: int = Field(default=300, ge=30, le=600)
    cleanup_after_days: int = Field(default=7, ge=1, le=30)
