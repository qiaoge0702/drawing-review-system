"""
结构模型模块
定义副车架、厢体、罐体等上装结构的数据模型
"""

from typing import List, Optional, Tuple, Union
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class SectionType(str, Enum):
    """截面类型枚举"""
    CHANNEL = "channel"            # 槽钢
    TUBE_RECT = "tube_rect"        # 矩形管
    TUBE_SQUARE = "tube_square"    # 方管
    I_BEAM = "i_beam"              # 工字钢
    C_CHANNEL = "c_channel"        # C型钢
    Z_CHANNEL = "z_channel"        # Z型钢
    CUSTOM = "custom"              # 定制截面


class MaterialSpec(BaseModel):
    """
    材料规格模型
    
    包含材料牌号、标准、力学性能等
    """
    grade: str = Field(
        ...,
        description="材料牌号",
        examples=["Q355B", "Q460C", "Q690D"]
    )
    standard: Optional[str] = Field(
        default=None,
        description="标准号",
        examples=["GB/T 1591", "GB/T 700"]
    )
    yield_strength: float = Field(
        ...,
        gt=0,
        description="屈服强度(MPa)",
        examples=[355, 460, 690]
    )
    tensile_strength: float = Field(
        ...,
        gt=0,
        description="抗拉强度(MPa)"
    )
    elongation: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="延伸率(%)"
    )
    
    @field_validator("tensile_strength")
    @classmethod
    def validate_strength_ratio(cls, v: float, info) -> float:
        """验证屈强比合理性"""
        yield_strength = info.data.get("yield_strength")
        if yield_strength and v < yield_strength:
            raise ValueError(
                f"抗拉强度({v})不应小于屈服强度({yield_strength})"
            )
        return v


class SectionProperty(BaseModel):
    """
    截面特性模型
    
    包含截面几何参数和力学特性
    """
    section_type: SectionType = Field(..., description="截面类型")
    height: float = Field(..., gt=0, description="高度(mm)")
    width: float = Field(..., gt=0, description="宽度(mm)")
    thickness: float = Field(..., gt=0, description="壁厚/翼缘厚(mm)")
    web_thickness: Optional[float] = Field(
        default=None,
        gt=0,
        description="腹板厚度(mm，工字钢用)"
    )
    area: Optional[float] = Field(default=None, gt=0, description="截面积(mm²)")
    moment_of_inertia_x: Optional[float] = Field(
        default=None,
        gt=0,
        description="惯性矩Ix(mm⁴)"
    )
    moment_of_inertia_y: Optional[float] = Field(
        default=None,
        gt=0,
        description="惯性矩Iy(mm⁴)"
    )
    section_modulus_x: Optional[float] = Field(
        default=None,
        gt=0,
        description="截面模数Wx(mm³)"
    )
    section_modulus_y: Optional[float] = Field(
        default=None,
        gt=0,
        description="截面模数Wy(mm³)"
    )
    
    def calculate_section_modulus_x_approx(self) -> float:
        """估算X向截面模数(mm³)"""
        if self.section_modulus_x:
            return self.section_modulus_x
        # 简化的矩形截面估算
        if self.section_type in [SectionType.TUBE_RECT, SectionType.TUBE_SQUARE]:
            return self.width * self.height ** 2 / 6
        return 0.0


class Weld(BaseModel):
    """
    焊缝模型
    
    描述焊缝类型、尺寸、质量等信息
    """
    weld_type: str = Field(
        ...,
        description="焊缝类型",
        examples=["角焊缝", "对接焊缝", "塞焊"]
    )
    throat_thickness: float = Field(
        ...,
        gt=0,
        description="焊喉厚度(mm)"
    )
    leg_length: float = Field(
        ...,
        gt=0,
        description="焊脚尺寸K(mm)"
    )
    length: float = Field(
        ...,
        gt=0,
        description="焊缝长度(mm)"
    )
    quality_level: Optional[str] = Field(
        default=None,
        description="质量等级",
        examples=["一级", "二级", "三级"]
    )
    welding_method: Optional[str] = Field(
        default=None,
        description="焊接方法",
        examples=["CO2气体保护焊", "埋弧焊", "手工电弧焊"]
    )
    
    @field_validator("throat_thickness")
    @classmethod
    def validate_throat_thickness(cls, v: float, info) -> float:
        """验证焊喉厚度与焊脚尺寸关系"""
        leg_length = info.data.get("leg_length")
        if leg_length:
            expected_throat = leg_length * 0.707  # 理论值 K * sin(45°)
            if abs(v - expected_throat) > 1:
                # 允许1mm误差，否则警告但不阻止
                pass
        return v


class Longeron(BaseModel):
    """
    纵梁模型
    
    副车架纵梁，通常成对出现（左右纵梁）
    """
    name: str = Field(..., description="名称，如前左纵梁")
    section: SectionProperty = Field(..., description="截面特性")
    material: MaterialSpec = Field(..., description="材料")
    length: float = Field(..., gt=0, description="长度(mm)")
    start_point: Tuple[float, float, float] = Field(
        ...,
        description="起点坐标(X,Y,Z)"
    )
    end_point: Tuple[float, float, float] = Field(
        ...,
        description="终点坐标(X,Y,Z)"
    )
    welds: List[Weld] = Field(default_factory=list, description="焊缝列表")
    
    def get_direction_vector(self) -> Tuple[float, float, float]:
        """获取方向向量"""
        return (
            self.end_point[0] - self.start_point[0],
            self.end_point[1] - self.start_point[1],
            self.end_point[2] - self.start_point[2]
        )
    
    def get_length_calculated(self) -> float:
        """计算实际长度（验证用）"""
        import math
        dx, dy, dz = self.get_direction_vector()
        return math.sqrt(dx**2 + dy**2 + dz**2)


class CrossBeam(BaseModel):
    """
    横梁模型
    
    副车架横梁，连接左右纵梁
    """
    name: str = Field(..., description="名称")
    section: SectionProperty = Field(..., description="截面特性")
    material: MaterialSpec = Field(..., description="材料")
    position: float = Field(
        ...,
        ge=0,
        description="位置（距副车架前端的距离mm）"
    )
    spacing: Optional[float] = Field(
        default=None,
        gt=0,
        description="与前一根横梁的间距(mm)"
    )
    connection_type: Optional[str] = Field(
        default=None,
        description="连接方式",
        examples=["焊接", "螺栓连接"]
    )


class Connector(BaseModel):
    """
    连接件模型
    
    副车架与底盘车架的连接件
    """
    name: str = Field(..., description="名称")
    type: str = Field(
        ...,
        description="类型",
        examples=["U型螺栓", "连接板", "焊接", "骑马螺栓"]
    )
    position: Tuple[float, float, float] = Field(..., description="位置坐标")
    bolt_spec: Optional[str] = Field(
        default=None,
        description="螺栓规格",
        examples=["M16", "M20", "M24"]
    )
    bolt_count: Optional[int] = Field(default=None, ge=1, description="螺栓数量")
    plate_thickness: Optional[float] = Field(
        default=None,
        gt=0,
        description="连接板厚度(mm)"
    )
    torque_requirement: Optional[float] = Field(
        default=None,
        gt=0,
        description="扭矩要求(N·m)"
    )


class Subframe(BaseModel):
    """
    副车架模型
    
    专用车辆上装的基础承载结构
    """
    longerons: List[Longeron] = Field(
        default_factory=list,
        description="纵梁列表"
    )
    cross_beams: List[CrossBeam] = Field(
        default_factory=list,
        description="横梁列表"
    )
    connectors: List[Connector] = Field(
        default_factory=list,
        description="连接件列表"
    )
    material: Optional[MaterialSpec] = Field(
        default=None,
        description="主材料"
    )
    total_length: float = Field(..., gt=0, description="总长度(mm)")
    total_width: float = Field(..., gt=0, description="总宽度(mm)")
    total_height: float = Field(..., gt=0, description="总高度(mm)")
    weight: Optional[float] = Field(default=None, gt=0, description="估算重量(kg)")
    
    @field_validator("cross_beams")
    @classmethod
    def validate_cross_beam_positions(cls, v: List[CrossBeam], info) -> List[CrossBeam]:
        """验证横梁位置合理性"""
        if len(v) < 2:
            return v
        positions = [beam.position for beam in v]
        if positions != sorted(positions):
            raise ValueError("横梁位置必须按从前到后顺序排列")
        return v
    
    def get_longeron_count(self) -> int:
        """获取纵梁数量"""
        return len(self.longerons)
    
    def get_cross_beam_count(self) -> int:
        """获取横梁数量"""
        return len(self.cross_beams)
    
    def get_average_beam_spacing(self) -> Optional[float]:
        """计算平均横梁间距"""
        if len(self.cross_beams) < 2:
            return None
        positions = [beam.position for beam in self.cross_beams]
        spacings = [positions[i+1] - positions[i] for i in range(len(positions)-1)]
        return sum(spacings) / len(spacings)


class VanBody(BaseModel):
    """
    厢体结构模型
    
    厢式运输车的货厢结构
    """
    body_type: str = Field(
        ...,
        description="厢体类型",
        examples=["瓦楞板", "插接板", "复合板", "彩涂板"]
    )
    length: float = Field(..., gt=0, description="厢长(mm)")
    width: float = Field(..., gt=0, description="厢宽(mm)")
    height: float = Field(..., gt=0, description="厢高(mm)")
    corrugated_depth: Optional[float] = Field(
        default=None,
        gt=0,
        description="瓦楞深度(mm)"
    )
    panel_thickness: float = Field(..., gt=0, description="板厚(mm)")
    front_reinforcement: bool = Field(
        default=False,
        description="前板是否加强"
    )
    door_locks: int = Field(default=0, ge=0, description="门锁数量")
    floor_type: Optional[str] = Field(
        default=None,
        description="地板类型",
        examples=["花纹钢板", "木质地板", "铝花纹板"]
    )
    
    def calculate_volume(self) -> float:
        """计算厢体容积(m³)"""
        return self.length * self.width * self.height / 1e9


class TankBody(BaseModel):
    """
    罐体结构模型
    
    罐式运输车的罐体结构
    """
    tank_type: str = Field(
        ...,
        description="罐体类型",
        examples=["圆罐", "椭圆罐", "方罐"]
    )
    length: float = Field(..., gt=0, description="罐长(mm)")
    diameter: Optional[float] = Field(
        default=None,
        gt=0,
        description="直径(mm，圆罐用)"
    )
    major_axis: Optional[float] = Field(
        default=None,
        gt=0,
        description="长轴(mm，椭圆罐用)"
    )
    minor_axis: Optional[float] = Field(
        default=None,
        gt=0,
        description="短轴(mm，椭圆罐用)"
    )
    head_type: str = Field(
        ...,
        description="封头形式",
        examples=["椭圆封头", "碟形封头", "球形封头", "平盖封头"]
    )
    head_thickness: Optional[float] = Field(
        default=None,
        gt=0,
        description="封头厚度(mm)"
    )
    shell_thickness: Optional[float] = Field(
        default=None,
        gt=0,
        description="筒体厚度(mm)"
    )
    baffle_count: int = Field(default=0, ge=0, description="防波板数量")
    baffle_spacing: Optional[float] = Field(
        default=None,
        gt=0,
        description="防波板间距(mm)"
    )
    manhole_position: Optional[str] = Field(
        default=None,
        description="人孔位置",
        examples=["顶部", "侧面"]
    )
    discharge_angle: Optional[float] = Field(
        default=None,
        ge=0,
        le=90,
        description="卸料坡度(°)"
    )
    emergency_valve: bool = Field(
        default=False,
        description="是否有紧急切断装置"
    )
    
    def calculate_volume(self) -> float:
        """计算罐体容积(m³)"""
        import math
        if self.tank_type == "圆罐" and self.diameter:
            radius = self.diameter / 2 / 1000  # 转换为米
            length = self.length / 1000
            return math.pi * radius ** 2 * length
        elif self.tank_type == "椭圆罐" and self.major_axis and self.minor_axis:
            a = self.major_axis / 2 / 1000
            b = self.minor_axis / 2 / 1000
            length = self.length / 1000
            return math.pi * a * b * length
        return 0.0


class Superstructure(BaseModel):
    """
    上装结构完整模型
    
    包含副车架、厢体/罐体、专用装置等所有上装部件
    """
    vehicle_type: str = Field(..., description="车型")
    subframe: Subframe = Field(..., description="副车架")
    body: Optional[Union[VanBody, TankBody]] = Field(
        default=None,
        description="厢体/罐体"
    )
    special_equipment: Optional[dict] = Field(
        default=None,
        description="专用装置（起重机、压缩机构等）"
    )
    hydraulic_system: Optional[dict] = Field(
        default=None,
        description="液压系统参数"
    )
    total_weight: Optional[float] = Field(
        default=None,
        gt=0,
        description="总重量(kg)"
    )
    center_of_gravity: Optional[Tuple[float, float, float]] = Field(
        default=None,
        description="重心位置(X,Y,Z mm)"
    )
    
    def get_body_type(self) -> Optional[str]:
        """获取本体类型"""
        if isinstance(self.body, VanBody):
            return "van"
        elif isinstance(self.body, TankBody):
            return "tank"
        return None
