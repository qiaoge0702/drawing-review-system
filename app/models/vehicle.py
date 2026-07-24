"""
车辆模型模块
定义车型枚举、车辆信息、底盘参数等模型
"""

from typing import Optional, List
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class VehicleType(str, Enum):
    """
    车型枚举
    
    覆盖专用车辆主要类型，每种类型对应不同的审查重点
    """
    VAN = "van"                    # 厢式运输车
    TANK = "tank"                  # 罐式运输车
    CRANE = "crane"                # 随车起重运输车
    COMPACTOR = "compactor"        # 压缩式垃圾车
    MIXER = "mixer"                # 混凝土搅拌车
    DUMP = "dump"                  # 自卸车
    WRECKER = "wrecker"            # 清障车
    AERIAL = "aerial"              # 高空作业车
    
    @classmethod
    def get_display_name(cls, vehicle_type: "VehicleType") -> str:
        """获取车型显示名称"""
        display_names = {
            cls.VAN: "厢式运输车",
            cls.TANK: "罐式运输车",
            cls.CRANE: "随车起重运输车",
            cls.COMPACTOR: "压缩式垃圾车",
            cls.MIXER: "混凝土搅拌车",
            cls.DUMP: "自卸车",
            cls.WRECKER: "清障车",
            cls.AERIAL: "高空作业车",
        }
        return display_names.get(vehicle_type, "未知车型")
    
    @classmethod
    def get_recognition_features(cls, vehicle_type: "VehicleType") -> List[str]:
        """获取车型识别特征列表"""
        features = {
            cls.VAN: ["corrugated_lines", "door_frame", "rectangular_outline"],
            cls.TANK: ["circular_arc", "head_curve", "baffle_lines"],
            cls.CRANE: ["boom_structure", "outrigger", "slewing_ring"],
            cls.COMPACTOR: ["compressor", "hopper", "container"],
            cls.MIXER: ["mixing_drum", "spiral_blade", "discharge_chute"],
            cls.DUMP: ["hydraulic_cylinder", "hinge", "tailgate"],
            cls.WRECKER: ["tow_boom", "winch", "under_lift"],
            cls.AERIAL: ["aerial_platform", "hydraulic_arm", "outrigger"],
        }
        return features.get(vehicle_type, [])


class VehicleInfo(BaseModel):
    """
    车辆信息模型
    
    包含车型、底盘信息、基本参数等
    """
    vehicle_type: VehicleType = Field(
        ...,
        description="车型"
    )
    vehicle_type_name: str = Field(
        ...,
        description="车型显示名称"
    )
    chassis_brand: Optional[str] = Field(
        default=None,
        description="底盘品牌",
        examples=["解放", "东风", "重汽"]
    )
    chassis_model: Optional[str] = Field(
        default=None,
        description="底盘型号",
        examples=["CA1250P62K1L7T3E6", "DFH1250A"]
    )
    wheelbase: Optional[float] = Field(
        default=None,
        ge=0,
        description="轴距(mm)"
    )
    total_mass: Optional[float] = Field(
        default=None,
        ge=0,
        description="总质量(kg)"
    )
    
    @field_validator("vehicle_type_name", mode="before")
    @classmethod
    def set_vehicle_type_name(cls, v: Optional[str], info) -> str:
        """自动设置车型显示名称"""
        if v:
            return v
        vehicle_type = info.data.get("vehicle_type")
        if vehicle_type:
            return VehicleType.get_display_name(vehicle_type)
        return "未知车型"


class ChassisParams(BaseModel):
    """
    底盘参数模型
    
    包含底盘几何参数和承载能力参数
    """
    wheelbase: float = Field(
        ...,
        gt=0,
        description="轴距(mm)",
        examples=[4500, 5000, 5700]
    )
    track_front: float = Field(
        ...,
        gt=0,
        description="前轮距(mm)"
    )
    track_rear: float = Field(
        ...,
        gt=0,
        description="后轮距(mm)"
    )
    frame_width: float = Field(
        ...,
        gt=0,
        description="车架宽度(mm)",
        examples=[860, 1000]
    )
    frame_height: float = Field(
        ...,
        gt=0,
        description="车架高度(mm)",
        examples=[280, 320]
    )
    frame_thickness: Optional[float] = Field(
        default=None,
        gt=0,
        description="车架纵梁厚度(mm)"
    )
    max_load: float = Field(
        ...,
        gt=0,
        description="最大允许总质量(kg)"
    )
    front_overhang: Optional[float] = Field(
        default=None,
        ge=0,
        description="前悬(mm)"
    )
    rear_overhang: Optional[float] = Field(
        default=None,
        ge=0,
        description="后悬(mm)"
    )
    
    @field_validator("track_rear")
    @classmethod
    def validate_track_consistency(cls, v: float, info) -> float:
        """验证轮距合理性"""
        track_front = info.data.get("track_front")
        if track_front and abs(v - track_front) > 500:
            raise ValueError(
                f"前后轮距差异过大: 前轮距={track_front}mm, 后轮距={v}mm"
            )
        return v
    
    def calculate_approximate_length(self) -> float:
        """估算整车长度(mm)"""
        length = self.wheelbase
        if self.front_overhang:
            length += self.front_overhang
        if self.rear_overhang:
            length += self.rear_overhang
        return length
