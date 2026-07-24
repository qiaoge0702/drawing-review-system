"""
图纸模型模块
定义图纸信息、元数据、图层、实体等模型
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field, field_validator


class DrawingInfo(BaseModel):
    """
    图纸文件信息模型
    
    包含文件的基本信息（名称、大小、类型等）
    """
    file_name: str = Field(..., description="文件名")
    file_path: str = Field(..., description="文件路径")
    file_size: int = Field(..., ge=0, description="文件大小(字节)")
    file_type: str = Field(
        ...,
        description="文件类型",
        examples=["dxf", "dwg", "pdf"]
    )
    created_at: datetime = Field(..., description="创建时间")
    modified_at: datetime = Field(..., description="修改时间")
    
    @field_validator("file_type")
    @classmethod
    def validate_file_type(cls, v: str) -> str:
        """验证文件类型"""
        allowed = ["dxf", "dwg", "pdf"]
        v_lower = v.lower()
        if v_lower not in allowed:
            raise ValueError(f"不支持的文件类型: {v}，仅支持 {allowed}")
        return v_lower
    
    def get_size_mb(self) -> float:
        """获取文件大小(MB)"""
        return self.file_size / (1024 * 1024)
    
    def get_file_extension(self) -> str:
        """获取文件扩展名"""
        return Path(self.file_name).suffix.lower()


class DrawingMetadata(BaseModel):
    """
    图纸元数据模型
    
    从标题栏提取的信息
    """
    title: Optional[str] = Field(default=None, description="图样名称")
    drawing_no: Optional[str] = Field(default=None, description="图样代号/图号")
    material: Optional[str] = Field(default=None, description="材料标记")
    scale: Optional[str] = Field(default=None, description="比例", examples=["1:1", "1:2", "2:1"])
    weight: Optional[str] = Field(default=None, description="重量")
    designer: Optional[str] = Field(default=None, description="设计")
    reviewer: Optional[str] = Field(default=None, description="审核")
    approver: Optional[str] = Field(default=None, description="批准")
    date: Optional[str] = Field(default=None, description="日期")
    company: Optional[str] = Field(default=None, description="单位名称")
    project_name: Optional[str] = Field(default=None, description="项目名称")
    version: Optional[str] = Field(default=None, description="版本号")
    
    def is_complete(self) -> bool:
        """检查关键元数据是否完整"""
        required_fields = [self.title, self.drawing_no, self.designer]
        return all(field is not None and field.strip() for field in required_fields)


class LayerInfo(BaseModel):
    """
    图层信息模型
    
    描述DXF/DWG文件中的图层属性
    """
    name: str = Field(..., description="图层名称")
    color: int = Field(default=7, description="颜色索引(AutoCAD颜色号)")
    is_on: bool = Field(default=True, description="是否打开")
    is_frozen: bool = Field(default=False, description="是否冻结")
    is_locked: bool = Field(default=False, description="是否锁定")
    linetype: str = Field(default="Continuous", description="线型")
    lineweight: Optional[int] = Field(default=None, description="线宽")
    
    def get_color_rgb(self) -> Optional[tuple]:
        """
        获取颜色RGB值
        
        返回AutoCAD标准颜色表的RGB值
        """
        # AutoCAD标准颜色表(部分常用颜色)
        color_table = {
            1: (255, 0, 0),      # 红
            2: (255, 255, 0),    # 黄
            3: (0, 255, 0),      # 绿
            4: (0, 255, 255),    # 青
            5: (0, 0, 255),      # 蓝
            6: (255, 0, 255),    # 品红
            7: (255, 255, 255),  # 白/黑(取决于背景)
            8: (128, 128, 128),  # 灰
            9: (192, 192, 192),  # 浅灰
        }
        return color_table.get(self.color)


class DrawingExtents(BaseModel):
    """
    图纸范围模型
    
    描述图纸的空间范围
    """
    min_x: float = Field(..., description="最小X坐标")
    min_y: float = Field(..., description="最小Y坐标")
    min_z: float = Field(default=0.0, description="最小Z坐标")
    max_x: float = Field(..., description="最大X坐标")
    max_y: float = Field(..., description="最大Y坐标")
    max_z: float = Field(default=0.0, description="最大Z坐标")
    
    @property
    def width(self) -> float:
        """图纸宽度"""
        return self.max_x - self.min_x
    
    @property
    def height(self) -> float:
        """图纸高度"""
        return self.max_y - self.min_y
    
    @property
    def depth(self) -> float:
        """图纸深度"""
        return self.max_z - self.min_z
    
    @property
    def center(self) -> tuple:
        """图纸中心点"""
        return (
            (self.min_x + self.max_x) / 2,
            (self.min_y + self.max_y) / 2,
            (self.min_z + self.max_z) / 2
        )
    
    def is_valid(self) -> bool:
        """验证范围是否有效"""
        return self.width > 0 and self.height > 0
    
    def contains_point(self, x: float, y: float, z: float = 0) -> bool:
        """检查点是否在范围内"""
        return (
            self.min_x <= x <= self.max_x and
            self.min_y <= y <= self.max_y and
            self.min_z <= z <= self.max_z
        )


class ExtractedEntities(BaseModel):
    """
    提取的实体统计模型
    
    统计DXF/DWG文件中各类实体的数量
    """
    layer_count: int = Field(default=0, ge=0, description="图层数量")
    line_count: int = Field(default=0, ge=0, description="直线数量")
    circle_count: int = Field(default=0, ge=0, description="圆数量")
    arc_count: int = Field(default=0, ge=0, description="圆弧数量")
    polyline_count: int = Field(default=0, ge=0, description="多段线数量")
    lwpolyline_count: int = Field(default=0, ge=0, description="轻量多段线数量")
    dimension_count: int = Field(default=0, ge=0, description="标注数量")
    text_count: int = Field(default=0, ge=0, description="单行文字数量")
    mtext_count: int = Field(default=0, ge=0, description="多行文字数量")
    insert_count: int = Field(default=0, ge=0, description="块引用数量")
    hatch_count: int = Field(default=0, ge=0, description="填充数量")
    ellipse_count: int = Field(default=0, ge=0, description="椭圆数量")
    spline_count: int = Field(default=0, ge=0, description="样条曲线数量")
    
    layers: List[LayerInfo] = Field(default_factory=list, description="图层列表")
    entities: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=dict,
        description="按类型分组的实体数据"
    )
    
    def get_total_entity_count(self) -> int:
        """获取实体总数"""
        return (
            self.line_count +
            self.circle_count +
            self.arc_count +
            self.polyline_count +
            self.lwpolyline_count +
            self.dimension_count +
            self.text_count +
            self.mtext_count +
            self.insert_count +
            self.hatch_count +
            self.ellipse_count +
            self.spline_count
        )
    
    def has_entities(self) -> bool:
        """检查是否包含任何实体"""
        return self.get_total_entity_count() > 0


class Drawing(BaseModel):
    """
    图纸完整模型
    
    整合图纸的所有信息
    """
    info: DrawingInfo = Field(..., description="文件信息")
    metadata: DrawingMetadata = Field(
        default_factory=DrawingMetadata,
        description="图纸元数据"
    )
    extents: DrawingExtents = Field(..., description="图纸范围")
    entities: ExtractedEntities = Field(
        default_factory=ExtractedEntities,
        description="提取的实体"
    )
    raw_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="原始DXF数据（用于调试）"
    )
    
    def get_drawing_scale(self) -> Optional[float]:
        """
        解析图纸比例
        
        从scale字段提取比例数值
        """
        if not self.metadata.scale:
            return None
        
        scale_str = self.metadata.scale.strip()
        
        # 处理 "1:100" 格式
        if ":" in scale_str:
            parts = scale_str.split(":")
            if len(parts) == 2:
                try:
                    numerator = float(parts[0].strip())
                    denominator = float(parts[1].strip())
                    return numerator / denominator if denominator != 0 else None
                except ValueError:
                    return None
        
        # 处理纯数字
        try:
            return float(scale_str)
        except ValueError:
            return None
    
    def get_main_layers(self) -> List[LayerInfo]:
        """
        获取主要图层
        
        排除系统图层和空图层
        """
        system_layers = ["0", "Defpoints", "*Paper_Space"]
        return [
            layer for layer in self.entities.layers
            if layer.name not in system_layers and not layer.name.startswith("*")
        ]
    
    def estimate_drawing_type(self) -> str:
        """
        估算图纸类型
        
        基于实体组成判断图纸类型
        """
        entities = self.entities
        total = entities.get_total_entity_count()
        
        if total == 0:
            return "empty"
        
        # 计算各类实体占比
        dimension_ratio = entities.dimension_count / total
        text_ratio = (entities.text_count + entities.mtext_count) / total
        insert_ratio = entities.insert_count / total
        
        # 判断逻辑
        if dimension_ratio > 0.3:
            return "detail_drawing"  # 详图（标注多）
        elif text_ratio > 0.2:
            return "assembly_drawing"  # 装配图（文字说明多）
        elif insert_ratio > 0.3:
            return "layout_drawing"  # 布局图（块引用多）
        else:
            return "general_drawing"  # 一般图纸
