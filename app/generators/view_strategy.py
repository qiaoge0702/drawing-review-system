"""
视图策略库 (B-M1 智能骨架)

按零件类型定义视图组合、比例策略、布局参数。
主视方向：选投影包围盒长宽比最大的方向（长梁侧立面水平摆放）
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.generators.type_recognition import BoundingBox, PartType

logger = logging.getLogger(__name__)


# GB标准比例序列（从大到小，即分母从小到大）
GB_SCALE_RATIOS = [1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 100.0]

# A3图幅尺寸（单位：mm）
SHEET_A3_WIDTH = 420.0
SHEET_A3_HEIGHT = 297.0

# 布局间距参数（单位：mm）
LAYOUT_MARGIN = 20.0           # 视图区边距
LAYOUT_GAP_MIN = 20.0          # 最小间距
LAYOUT_GAP_MAX = 30.0          # 最大间距
LAYOUT_GAP_DEFAULT = 25.0      # 默认间距

# 各类型视图占比目标
TARGET_COVERAGE = {
    PartType.STANDARD_PART: (0.4, 0.6),  # 40-60%
    PartType.PLATE: (0.6, 0.8),          # 60-80%
    PartType.BEAM: (0.6, 0.9),           # 60-90%（长梁尽量大）
    PartType.WELDMENT: (0.6, 0.9),       # 60-90%
    PartType.ASSEMBLY: (0.6, 0.9),       # 60-90%
}


class ViewName(str, Enum):
    """标准视图名"""
    FRONT = "front"           # 主视
    TOP = "top"               # 俯视
    LEFT = "left"             # 左视
    RIGHT = "right"           # 右视
    ISOMETRIC = "isometric"   # 轴测图


# SW预定义视图名映射（中英文环境）
SW_PREDEFINED_VIEWS = {
    ViewName.FRONT: ["*前视", "*Front"],
    ViewName.TOP: ["*上视", "*Top"],
    ViewName.LEFT: ["*左视", "*Left"],
    ViewName.RIGHT: ["*右视", "*Right"],
    ViewName.ISOMETRIC: ["*等轴测", "*Isometric"],
}


@dataclass
class ViewConfig:
    """单个视图的配置"""
    name: ViewName
    display_name: str
    sw_names: List[str]       # SW预定义视图名（中英文）
    position_hint: str        # 位置提示（用于布局算法）


@dataclass
class ViewStrategy:
    """视图策略（按零件类型）"""
    part_type: PartType
    views: List[ViewConfig]           # 视图组合
    scale_mode: str                   # 比例策略：auto_fill / max_fit / adaptive
    target_coverage: Tuple[float, float]  # 目标占图幅比例范围
    need_isometric: bool = False      # 是否需要轴测图
    spacing: float = LAYOUT_GAP_DEFAULT  # 视图间距


# 标准视图定义
VIEW_FRONT = ViewConfig(
    name=ViewName.FRONT,
    display_name="主视图",
    sw_names=SW_PREDEFINED_VIEWS[ViewName.FRONT],
    position_hint="center_upper",  # 中上居中
)

VIEW_TOP = ViewConfig(
    name=ViewName.TOP,
    display_name="俯视图",
    sw_names=SW_PREDEFINED_VIEWS[ViewName.TOP],
    position_hint="below_front",   # 主视正下方
)

VIEW_LEFT = ViewConfig(
    name=ViewName.LEFT,
    display_name="左视图",
    sw_names=SW_PREDEFINED_VIEWS[ViewName.LEFT],
    position_hint="right_of_front",  # 主视右侧
)

VIEW_RIGHT = ViewConfig(
    name=ViewName.RIGHT,
    display_name="右视图",
    sw_names=SW_PREDEFINED_VIEWS[ViewName.RIGHT],
    position_hint="right_of_front",
)

VIEW_ISOMETRIC = ViewConfig(
    name=ViewName.ISOMETRIC,
    display_name="轴测图",
    sw_names=SW_PREDEFINED_VIEWS[ViewName.ISOMETRIC],
    position_hint="bottom_left",   # 左下角
)


# 各类型视图策略定义
VIEW_STRATEGIES: Dict[PartType, ViewStrategy] = {
    PartType.STANDARD_PART: ViewStrategy(
        part_type=PartType.STANDARD_PART,
        views=[VIEW_FRONT],
        scale_mode="adaptive",
        target_coverage=TARGET_COVERAGE[PartType.STANDARD_PART],
        need_isometric=False,
    ),
    PartType.PLATE: ViewStrategy(
        part_type=PartType.PLATE,
        views=[VIEW_FRONT, VIEW_TOP, VIEW_LEFT],
        scale_mode="auto_fill",
        target_coverage=TARGET_COVERAGE[PartType.PLATE],
        need_isometric=False,
    ),
    PartType.BEAM: ViewStrategy(
        part_type=PartType.BEAM,
        views=[VIEW_FRONT, VIEW_RIGHT, VIEW_TOP, VIEW_ISOMETRIC],
        scale_mode="max_fit",
        target_coverage=TARGET_COVERAGE[PartType.BEAM],
        need_isometric=True,
        spacing=LAYOUT_GAP_DEFAULT,
    ),
    PartType.WELDMENT: ViewStrategy(
        part_type=PartType.WELDMENT,
        views=[VIEW_FRONT, VIEW_TOP, VIEW_LEFT, VIEW_ISOMETRIC],
        scale_mode="max_fit",
        target_coverage=TARGET_COVERAGE[PartType.WELDMENT],
        need_isometric=True,
        spacing=LAYOUT_GAP_DEFAULT,
    ),
    PartType.ASSEMBLY: ViewStrategy(
        part_type=PartType.ASSEMBLY,
        views=[VIEW_FRONT, VIEW_RIGHT, VIEW_TOP, VIEW_ISOMETRIC],
        scale_mode="max_fit",
        target_coverage=TARGET_COVERAGE[PartType.ASSEMBLY],
        need_isometric=True,
        spacing=LAYOUT_GAP_DEFAULT,
    ),
}


def get_view_strategy(part_type: PartType) -> ViewStrategy:
    """获取指定零件类型的视图策略"""
    return VIEW_STRATEGIES.get(part_type, VIEW_STRATEGIES[PartType.PLATE])


def compute_main_view_direction(box: BoundingBox) -> ViewName:
    """
    计算主视方向
    
    选择投影包围盒长宽比最大的方向（长梁侧立面水平摆放）
    
    Returns:
        ViewName: 推荐的主视方向
    """
    # 各方向投影尺寸（宽×高）
    projections = {
        ViewName.FRONT: (box.dx, box.dz),   # 沿-Y看：u=X v=Z
        ViewName.TOP: (box.dx, box.dy),     # 沿-Z看：u=X v=Y
        ViewName.LEFT: (box.dy, box.dz),    # 沿+X看：u=Y v=Z
        ViewName.RIGHT: (box.dy, box.dz),   # 沿-X看：u=Y v=Z
    }
    
    # 选择长宽比最大的方向
    best_view = ViewName.FRONT
    best_ratio = 0.0
    
    for view, (w, h) in projections.items():
        if h <= 0:
            continue
        ratio = w / h
        if ratio > best_ratio:
            best_ratio = ratio
            best_view = view
    
    return best_view


def compute_view_sizes(
    box: BoundingBox,
    strategy: ViewStrategy,
) -> Dict[ViewName, Tuple[float, float]]:
    """
    计算各视图的尺寸（未缩放，单位mm）
    
    Returns:
        Dict[ViewName, (width, height)]: 各视图尺寸
    """
    sizes = {}
    
    for view in strategy.views:
        if view.name == ViewName.FRONT:
            sizes[view.name] = (box.dx, box.dz)
        elif view.name == ViewName.TOP:
            sizes[view.name] = (box.dx, box.dy)
        elif view.name in (ViewName.LEFT, ViewName.RIGHT):
            sizes[view.name] = (box.dy, box.dz)
        elif view.name == ViewName.ISOMETRIC:
            # 轴测图尺寸估算：取长方体的等轴测投影近似
            # 等轴测投影下，三个方向都有分量
            iso_w = box.dx * 0.866 + box.dy * 0.866  # X和Y在水平面投影
            iso_h = box.dz + box.dx * 0.5 + box.dy * 0.5  # Z垂直，X/Y有垂直分量
            sizes[view.name] = (iso_w, iso_h)
    
    return sizes


def select_scale_for_sheet(
    view_sizes: Dict[ViewName, Tuple[float, float]],
    sheet_width: float,
    sheet_height: float,
    strategy: ViewStrategy,
    max_retries: int = 3,
) -> Tuple[float, int]:
    """
    为图幅选择最大可用比例
    
    从比例序列中选"全部视图能放进图幅"的最大比例（即比例分母最小）
    
    Args:
        view_sizes: 各视图未缩放尺寸
        sheet_width: 图幅宽
        sheet_height: 图幅高
        strategy: 视图策略
        max_retries: 最大重算次数
    
    Returns:
        Tuple[scale_denominator, retry_count]: 选中的比例分母和实际重算次数
    """
    spacing = strategy.spacing
    
    # 根据视图数量估算需要的空间
    view_count = len(view_sizes)
    
    for retry in range(max_retries + 1):
        for den in GB_SCALE_RATIOS:
            # 按比例缩放后的尺寸
            scaled_sizes = {
                name: (w / den, h / den)
                for name, (w, h) in view_sizes.items()
            }
            
            # 估算总占用空间（简化估算）
            total_width = 0.0
            total_height = 0.0
            
            # 按第一角投影估算布局
            if ViewName.FRONT in scaled_sizes:
                fw, fh = scaled_sizes[ViewName.FRONT]
                total_width = max(total_width, fw)
                total_height = max(total_height, fh)
                
                # 俯视图在主视下方
                if ViewName.TOP in scaled_sizes:
                    tw, th = scaled_sizes[ViewName.TOP]
                    total_height += spacing + th
                    total_width = max(total_width, tw)
                
                # 左/右视图在主视右侧
                if ViewName.LEFT in scaled_sizes or ViewName.RIGHT in scaled_sizes:
                    side_view = ViewName.LEFT if ViewName.LEFT in scaled_sizes else ViewName.RIGHT
                    sw, sh = scaled_sizes[side_view]
                    total_width += spacing + sw
                    total_height = max(total_height, sh)
                
                # 轴测图在左下角
                if ViewName.ISOMETRIC in scaled_sizes:
                    iw, ih = scaled_sizes[ViewName.ISOMETRIC]
                    # 轴测图不与其他视图重叠，单独区域
                    total_width = max(total_width, iw + spacing)
                    total_height = max(total_height, ih + spacing)
            else:
                # 无主视图时，简单累加
                for w, h in scaled_sizes.values():
                    total_width += w + spacing
                    total_height = max(total_height, h)
            
            # 加上边距
            total_width += 2 * LAYOUT_MARGIN
            total_height += 2 * LAYOUT_MARGIN
            
            # 检查是否适合图幅
            if total_width <= sheet_width and total_height <= sheet_height:
                return den, retry
        
        # 如果都不适合，尝试增大间距策略（减小间距）
        if retry < max_retries:
            spacing = max(LAYOUT_GAP_MIN, spacing - 5.0)
            logger.debug(f"Scale selection retry {retry + 1}: reducing spacing to {spacing}")
    
    # 所有重算都失败，返回最小比例
    logger.warning(f"Could not fit views even at 1:{GB_SCALE_RATIOS[-1]}, using min scale")
    return GB_SCALE_RATIOS[-1], max_retries


def compute_adaptive_scale(
    view_sizes: Dict[ViewName, Tuple[float, float]],
    sheet_width: float,
    sheet_height: float,
    target_coverage: Tuple[float, float],
) -> float:
    """
    计算自适应比例（用于单视图的标准件）
    
    使视图占图幅的40-60%
    """
    target_min, target_max = target_coverage
    
    for den in GB_SCALE_RATIOS:
        scaled_area = sum((w / den) * (h / den) for w, h in view_sizes.values())
        sheet_area = sheet_width * sheet_height
        coverage = scaled_area / sheet_area
        
        if target_min <= coverage <= target_max:
            return den
    
    # 回退到中间值
    return 10.0


def get_sw_view_name(view_name: ViewName, attempt: int = 0) -> Optional[str]:
    """
    获取SW预定义视图名（中英文环境适配）
    
    Args:
        view_name: 标准视图名
        attempt: 尝试次数（0=中文，1=英文）
    
    Returns:
        SW视图名，或None（超过尝试次数）
    """
    names = SW_PREDEFINED_VIEWS.get(view_name, [])
    if attempt < len(names):
        return names[attempt]
    return None


def create_view_strategy_result(
    part_type: PartType,
    box: BoundingBox,
    sheet_width: float = SHEET_A3_WIDTH,
    sheet_height: float = SHEET_A3_HEIGHT,
) -> Dict[str, Any]:
    """
    创建完整的视图策略结果
    
    Returns:
        Dict包含：views, scale, sheet_size, strategy等
    """
    strategy = get_view_strategy(part_type)
    
    # 确定主视方向
    main_direction = compute_main_view_direction(box)
    
    # 计算各视图尺寸
    view_sizes = compute_view_sizes(box, strategy)
    
    # 选择比例
    if strategy.scale_mode == "adaptive":
        scale_den = compute_adaptive_scale(
            view_sizes, sheet_width, sheet_height, strategy.target_coverage
        )
    else:
        scale_den, _ = select_scale_for_sheet(
            view_sizes, sheet_width, sheet_height, strategy
        )
    
    # 组装结果
    views_data = []
    for view in strategy.views:
        vw, vh = view_sizes.get(view.name, (100.0, 100.0))
        views_data.append({
            "name": view.name.value,
            "display_name": view.display_name,
            "sw_names": view.sw_names,
            "size_mm": {"width": round(vw, 4), "height": round(vh, 4)},
            "position_hint": view.position_hint,
        })
    
    return {
        "part_type": part_type.value,
        "strategy": {
            "scale_mode": strategy.scale_mode,
            "target_coverage": strategy.target_coverage,
            "spacing": strategy.spacing,
        },
        "main_view_direction": main_direction.value,
        "scale_denominator": scale_den,
        "scale": f"1:{scale_den:g}",
        "sheet_size": {"width": sheet_width, "height": sheet_height},
        "views": views_data,
        "view_sizes": {
            name.value: {"width": round(w, 4), "height": round(h, 4)}
            for name, (w, h) in view_sizes.items()
        },
    }


def to_layout_input(
    strategy_result: Dict[str, Any],
    scale_den: float,
) -> List[Dict[str, Any]]:
    """
    将策略结果转换为布局引擎输入格式
    
    Returns:
        List[Dict]: 视图列表，每项包含name和bounding_box
    """
    views = []
    for view_data in strategy_result.get("views", []):
        size = view_data.get("size_mm", {"width": 100, "height": 100})
        scaled_w = size["width"] / scale_den
        scaled_h = size["height"] / scale_den
        views.append({
            "name": view_data["name"],
            "bounding_box": {
                "min_x": 0.0,
                "min_y": 0.0,
                "max_x": scaled_w,
                "max_y": scaled_h,
            },
            "position_hint": view_data.get("position_hint", ""),
        })
    return views
