"""
数据模型模块
定义车辆、结构、图纸、审查结果等核心数据模型
"""

from .vehicle import VehicleType, VehicleInfo, ChassisParams
from .structure import (
    SectionType,
    MaterialSpec,
    SectionProperty,
    Longeron,
    CrossBeam,
    Connector,
    Weld,
    Subframe,
    VanBody,
    TankBody,
    Superstructure,
)
from .drawing import (
    DrawingInfo,
    DrawingMetadata,
    LayerInfo,
    DrawingExtents,
    ExtractedEntities,
    Drawing,
)
from .check_result import (
    IssueSeverity,
    IssueCategory,
    Issue,
    CheckSummary,
    CheckResult,
)
from .generation import (
    DrawingType,
    StepName,
    StepStatus,
    PipelineState,
    ArtifactType,
    Artifact,
    StepResult,
    TaskConfig,
    TaskResult,
    GenerateSettings,
)

__all__ = [
    # Vehicle
    "VehicleType",
    "VehicleInfo",
    "ChassisParams",
    # Structure
    "SectionType",
    "MaterialSpec",
    "SectionProperty",
    "Longeron",
    "CrossBeam",
    "Connector",
    "Weld",
    "Subframe",
    "VanBody",
    "TankBody",
    "Superstructure",
    # Drawing
    "DrawingInfo",
    "DrawingMetadata",
    "LayerInfo",
    "DrawingExtents",
    "ExtractedEntities",
    "Drawing",
    # Check Result
    "IssueSeverity",
    "IssueCategory",
    "Issue",
    "CheckSummary",
    "CheckResult",
    # Generation
    "DrawingType",
    "StepName",
    "StepStatus",
    "PipelineState",
    "ArtifactType",
    "Artifact",
    "StepResult",
    "TaskConfig",
    "TaskResult",
    "GenerateSettings",
]
