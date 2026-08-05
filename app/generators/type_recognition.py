"""
零件类型识别模块 (B-M1 智能骨架)

基于包围盒几何特征和文件名关键词，按优先级判定零件类型：
standard_part > beam > plate > weldment > assembly

判定结果包含类型和判定依据，写入 result.json，禁止黑箱。
"""

import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PartType(str, Enum):
    """零件类型枚举"""
    STANDARD_PART = "standard_part"  # 标准件（螺栓/螺母/垫圈/轴承等）
    BEAM = "beam"                    # 长梁/杆类（细长特征）
    PLATE = "plate"                  # 板类/法兰（薄板特征）
    WELDMENT = "weldment"            # 焊接小总成（装配体且零件数≤50）
    ASSEMBLY = "assembly"            # 复杂装配（装配体且零件数>50）


@dataclass
class BoundingBox:
    """三维包围盒（单位：mm）"""
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float
    
    @property
    def dx(self) -> float:
        """X方向边长"""
        return self.max_x - self.min_x
    
    @property
    def dy(self) -> float:
        """Y方向边长"""
        return self.max_y - self.min_y
    
    @property
    def dz(self) -> float:
        """Z方向边长"""
        return self.max_z - self.min_z
    
    @property
    def edges(self) -> Tuple[float, float, float]:
        """返回三条边长，已排序（小→大）"""
        edges = sorted([self.dx, self.dy, self.dz])
        return edges[0], edges[1], edges[2]  # min, mid, max
    
    @property
    def max_edge(self) -> float:
        """最大边长"""
        return self.edges[2]
    
    @property
    def min_edge(self) -> float:
        """最小边长"""
        return self.edges[0]
    
    @property
    def mid_edge(self) -> float:
        """中间边长"""
        return self.edges[1]


# 默认视图建议（按零件类型）：view_type/name/display_name/position_hint/scale
# scale="auto" 由布局引擎试算；局部放大/剖视等辅助视图由用户覆盖追加，默认 2.0 放大
DEFAULT_VIEW_SUGGESTIONS: Dict[PartType, List[Dict[str, Any]]] = {
    PartType.STANDARD_PART: [
        {"view_type": "standard", "name": "front", "display_name": "主视图",
         "position_hint": "center_upper", "scale": "auto"},
    ],
    PartType.PLATE: [
        {"view_type": "standard", "name": "front", "display_name": "主视图",
         "position_hint": "center_upper", "scale": "auto"},
        {"view_type": "standard", "name": "top", "display_name": "俯视图",
         "position_hint": "below_front", "scale": "auto"},
        {"view_type": "standard", "name": "left", "display_name": "左视图",
         "position_hint": "right_of_front", "scale": "auto"},
    ],
    PartType.BEAM: [
        {"view_type": "standard", "name": "front", "display_name": "主视图",
         "position_hint": "center_upper", "scale": "auto"},
        {"view_type": "standard", "name": "right", "display_name": "右视图",
         "position_hint": "right_of_front", "scale": "auto"},
        {"view_type": "standard", "name": "top", "display_name": "俯视图",
         "position_hint": "below_front", "scale": "auto"},
        {"view_type": "isometric", "name": "isometric", "display_name": "轴测图",
         "position_hint": "above_title_block", "scale": "auto"},
    ],
    PartType.WELDMENT: [
        {"view_type": "standard", "name": "front", "display_name": "主视图",
         "position_hint": "center_upper", "scale": "auto"},
        {"view_type": "standard", "name": "top", "display_name": "俯视图",
         "position_hint": "below_front", "scale": "auto"},
        {"view_type": "standard", "name": "left", "display_name": "左视图",
         "position_hint": "right_of_front", "scale": "auto"},
        {"view_type": "isometric", "name": "isometric", "display_name": "轴测图",
         "position_hint": "above_title_block", "scale": "auto"},
    ],
    PartType.ASSEMBLY: [
        {"view_type": "standard", "name": "front", "display_name": "主视图",
         "position_hint": "center_upper", "scale": "auto"},
        {"view_type": "standard", "name": "right", "display_name": "右视图",
         "position_hint": "right_of_front", "scale": "auto"},
        {"view_type": "standard", "name": "top", "display_name": "俯视图",
         "position_hint": "below_front", "scale": "auto"},
        {"view_type": "isometric", "name": "isometric", "display_name": "轴测图",
         "position_hint": "above_title_block", "scale": "auto"},
    ],
}


@dataclass
class TypeRecognitionResult:
    """类型识别结果"""
    part_type: PartType
    reason: str                    # 判定依据
    priority: int                  # 判定优先级（越小越优先）
    bounding_box: Optional[BoundingBox] = None
    component_count: Optional[int] = None
    filename: Optional[str] = None
    suggested_views: List[Dict[str, Any]] = field(default_factory=list)  # B-M1+ 默认视图建议


# 标准件关键词（中英文）
STANDARD_PART_KEYWORDS = [
    # 中文
    "螺栓", "螺母", "垫圈", "轴承", "螺钉", "螺柱", "垫片",
    "挡圈", "销", "键", "铆钉", "油嘴", "油杯", "密封圈",
    # 英文
    "bolt", "nut", "washer", "bearing", "screw", "stud",
    "gasket", "ring", "pin", "key", "rivet", "seal",
]

# 判定优先级（越小越优先）
TYPE_PRIORITY = {
    PartType.STANDARD_PART: 1,
    PartType.BEAM: 2,
    PartType.PLATE: 3,
    PartType.WELDMENT: 4,
    PartType.ASSEMBLY: 5,
}

# 特征阈值（单位：mm）
STANDARD_PART_MAX_EDGE = 100.0      # 标准件最大边长阈值
PLATE_THICKNESS_RATIO = 5.0         # 板类厚度判定比例（最小边 < 次小边/5）
BEAM_SLENDER_RATIO = 5.0            # 长梁细长判定比例（最大边 > 次小边×5）
WELDMENT_MAX_COMPONENTS = 50        # 焊接小总成最大零件数


def _is_standard_part_by_filename(filename: str) -> bool:
    """根据文件名判断是否标准件"""
    if not filename:
        return False
    lower_name = filename.lower()
    return any(kw in lower_name for kw in STANDARD_PART_KEYWORDS)


def _is_standard_part_by_size(box: BoundingBox) -> bool:
    """根据包围盒判断是否标准件（最大边<100mm）"""
    return box.max_edge < STANDARD_PART_MAX_EDGE


def _is_plate(box: BoundingBox) -> bool:
    """
    判断是否为板类/法兰
    规则：最小边（厚度）< 次小边/5（薄板特征）
    """
    min_e, mid_e, _ = box.edges
    return min_e < mid_e / PLATE_THICKNESS_RATIO


def _is_beam(box: BoundingBox) -> bool:
    """
    判断是否为长梁/杆类
    规则：最大边 > 次小边×5（细长特征）
    """
    _, mid_e, max_e = box.edges
    return max_e > mid_e * BEAM_SLENDER_RATIO


def recognize_part_type(
    filename: str,
    bounding_box: Optional[BoundingBox] = None,
    is_assembly: bool = False,
    component_count: Optional[int] = None,
) -> TypeRecognitionResult:
    """
    识别零件类型
    
    判定优先级：standard_part > beam > plate > weldment > assembly
    
    Args:
        filename: 文件名（用于关键词匹配）
        bounding_box: 三维包围盒（单位mm）
        is_assembly: 是否为装配体
        component_count: 装配体零件数（仅装配体有效）
    
    Returns:
        TypeRecognitionResult: 类型识别结果（含判定依据）
    """
    candidates: List[Tuple[PartType, str, int]] = []
    
    # 1. 标准件判定（最高优先级）
    if _is_standard_part_by_filename(filename):
        candidates.append((
            PartType.STANDARD_PART,
            f"文件名含标准件关键词（{filename}）",
            TYPE_PRIORITY[PartType.STANDARD_PART]
        ))
    elif bounding_box and _is_standard_part_by_size(bounding_box):
        candidates.append((
            PartType.STANDARD_PART,
            f"包围盒最大边={bounding_box.max_edge:.2f}mm < {STANDARD_PART_MAX_EDGE}mm",
            TYPE_PRIORITY[PartType.STANDARD_PART]
        ))
    
    # 2. 长梁判定（次高优先级）
    if bounding_box and _is_beam(bounding_box):
        edges = bounding_box.edges
        candidates.append((
            PartType.BEAM,
            f"细长特征：最大边={edges[2]:.2f}mm > 次小边×{BEAM_SLENDER_RATIO}={edges[1]*BEAM_SLENDER_RATIO:.2f}mm",
            TYPE_PRIORITY[PartType.BEAM]
        ))
    
    # 3. 板类判定
    if bounding_box and _is_plate(bounding_box):
        edges = bounding_box.edges
        candidates.append((
            PartType.PLATE,
            f"薄板特征：最小边={edges[0]:.2f}mm < 次小边/{PLATE_THICKNESS_RATIO}={edges[1]/PLATE_THICKNESS_RATIO:.2f}mm",
            TYPE_PRIORITY[PartType.PLATE]
        ))
    
    # 4. 装配体相关判定（仅对装配体）
    if is_assembly:
        if component_count is not None and component_count <= WELDMENT_MAX_COMPONENTS:
            # 焊接小总成近似：装配体且零件数≤50（无焊缝API时按此近似）
            candidates.append((
                PartType.WELDMENT,
                f"装配体零件数={component_count} ≤ {WELDMENT_MAX_COMPONENTS}（无焊缝API时的近似依据）",
                TYPE_PRIORITY[PartType.WELDMENT]
            ))
        else:
            # 复杂装配
            count_str = f"={component_count}" if component_count is not None else "未知"
            candidates.append((
                PartType.ASSEMBLY,
                f"装配体零件数{count_str} > {WELDMENT_MAX_COMPONENTS}",
                TYPE_PRIORITY[PartType.ASSEMBLY]
            ))
    
    # 选择优先级最高的类型
    if candidates:
        candidates.sort(key=lambda x: x[2])  # 按优先级排序
        selected = candidates[0]
        return TypeRecognitionResult(
            part_type=selected[0],
            reason=selected[1],
            priority=selected[2],
            bounding_box=bounding_box,
            component_count=component_count,
            filename=filename,
            suggested_views=deepcopy(DEFAULT_VIEW_SUGGESTIONS.get(selected[0], [])),
        )
    
    # 默认回退为板类（兜底）
    return TypeRecognitionResult(
        part_type=PartType.PLATE,
        reason="无明确特征匹配，默认板类",
        priority=TYPE_PRIORITY[PartType.PLATE],
        bounding_box=bounding_box,
        component_count=component_count,
        filename=filename,
        suggested_views=deepcopy(DEFAULT_VIEW_SUGGESTIONS[PartType.PLATE]),
    )


def recognize_from_sw_model(
    filename: str,
    sw_box: Optional[Tuple[float, ...]] = None,
    is_assembly: bool = False,
    component_count: Optional[int] = None,
) -> TypeRecognitionResult:
    """
    从SW模型数据识别零件类型
    
    Args:
        filename: 模型文件名
        sw_box: SW API返回的包围盒（6元组，单位：米）
        is_assembly: 是否为装配体
        component_count: 装配体零件数
    
    Returns:
        TypeRecognitionResult: 类型识别结果
    """
    bounding_box = None
    if sw_box and len(sw_box) >= 6:
        # SW单位是米，转换为mm
        bounding_box = BoundingBox(
            min_x=sw_box[0] * 1000.0,
            min_y=sw_box[1] * 1000.0,
            min_z=sw_box[2] * 1000.0,
            max_x=sw_box[3] * 1000.0,
            max_y=sw_box[4] * 1000.0,
            max_z=sw_box[5] * 1000.0,
        )
    
    return recognize_part_type(filename, bounding_box, is_assembly, component_count)


def to_dict(result: TypeRecognitionResult) -> Dict[str, Any]:
    """将识别结果转换为字典（用于写入result.json）"""
    data = {
        "type": result.part_type.value,
        "reason": result.reason,
        "priority": result.priority,
    }
    if result.bounding_box:
        data["bounding_box"] = {
            "dx": round(result.bounding_box.dx, 4),
            "dy": round(result.bounding_box.dy, 4),
            "dz": round(result.bounding_box.dz, 4),
            "edges": {
                "min": round(result.bounding_box.min_edge, 4),
                "mid": round(result.bounding_box.mid_edge, 4),
                "max": round(result.bounding_box.max_edge, 4),
            }
        }
    if result.component_count is not None:
        data["component_count"] = result.component_count
    if result.filename:
        data["filename"] = result.filename
    if result.suggested_views:
        data["suggested_views"] = result.suggested_views
    return data
