"""
视图策略库 (B-M1 智能骨架)

按零件类型定义视图组合、比例策略、布局参数。
主视方向：选投影包围盒长宽比最大的方向（长梁侧立面水平摆放）

B-M1+ 扩展：
- 新增视图类型：局部放大（detail）、剖视（section）、辅助视图（auxiliary）
- 每个视图独立比例（多比例并存）
- 用户覆盖参数（views_override / part_type_override / positions_override）
- 第一角投影（GB标准，使用 Create1stAngleViews2）
"""

import logging
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from app.generators.type_recognition import BoundingBox, PartType
from app.core.config import get_settings

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
    """标准视图名（几何方位）"""
    FRONT = "front"           # 主视
    TOP = "top"               # 俯视
    LEFT = "left"             # 左视
    RIGHT = "right"           # 右视
    BACK = "back"             # 后视
    BOTTOM = "bottom"         # 仰视
    ISOMETRIC = "isometric"   # 轴测图


class ViewType(str, Enum):
    """视图类型（B-M1+ 扩展）"""
    STANDARD = "standard"      # 标准正交视图
    ISOMETRIC = "isometric"    # 轴测图
    DETAIL = "detail"          # 局部放大视图
    SECTION = "section"        # 剖视视图
    AUXILIARY = "auxiliary"    # 辅助视图


# SW预定义视图名映射（中英文环境）
SW_PREDEFINED_VIEWS = {
    ViewName.FRONT: ["*前视", "*Front"],
    ViewName.TOP: ["*上视", "*Top"],
    ViewName.LEFT: ["*左视", "*Left"],
    ViewName.RIGHT: ["*右视", "*Right"],
    ViewName.BACK: ["*后视", "*Back"],
    ViewName.BOTTOM: ["*下视", "*Bottom"],
    ViewName.ISOMETRIC: ["*等轴测", "*Isometric"],
}


def _resolve_sw_predefined_views() -> Dict[ViewName, List[str]]:
    """合并硬编码默认值与 settings.sw.predefined_view_names 可选配置。
    配置项支持 Dict[str, str] 或字符串中的逗号分隔列表；缺省保持现有
    中英文候选顺序。"""
    cfg = get_settings().sw
    cfg_names = getattr(cfg, "predefined_view_names", None) or {}
    if not cfg_names:
        return SW_PREDEFINED_VIEWS
    merged: Dict[ViewName, List[str]] = {
        k: list(v) for k, v in SW_PREDEFINED_VIEWS.items()
    }
    for key, val in cfg_names.items():
        try:
            vn = ViewName(key)
        except ValueError:
            logger.warning(f"忽略未知预定义视图名配置项: {key}")
            continue
        if not val:
            continue
        parts = [p.strip() for p in str(val).split(",") if p.strip()]
        merged[vn] = parts + [p for p in merged[vn] if p not in parts]
    return merged


@dataclass
class ViewConfig:
    """单个视图的配置（B-M1+ 扩展字段）"""
    # 原有字段（保持兼容）
    name: ViewName
    display_name: str
    sw_names: List[str]       # SW预定义视图名（中英文）
    position_hint: str        # 位置提示（用于布局算法）

    # B-M1+ 新增字段
    id: str = field(default="")
    view_type: ViewType = field(default=ViewType.STANDARD)
    parent_id: Optional[str] = field(default=None)
    scale: Union[str, float] = field(default="auto")
    """视图比例："auto"=布局引擎自动试算；数值=相对主比例的放大倍数（2.0→分母减半，
    如主视 1:50 时局部放大得 1:25）；字符串 "1:N"=绝对比例分母"""
    position_mode: str = field(default="auto")  # "auto"（约束布局）/ "hint"（用户提示）/ "absolute"（绝对坐标）
    position_params: Dict[str, Any] = field(default_factory=dict)
    region: Optional[Dict[str, Any]] = field(default=None)
    """局部放大区域（DETAIL 专用）：{"center": (x,y,z), "radius": float}，模型空间绝对坐标（mm）"""
    cut_line: Optional[List[Tuple[float, ...]]] = field(default=None)
    """剖切线 polyline（SECTION 专用，模型空间绝对坐标，单位 mm）"""

    def __post_init__(self):
        """自动补全 id（若未提供）"""
        if not self.id:
            self.id = f"{self.name.value}_{self.view_type.value}"


@dataclass
class ViewStrategy:
    """视图策略（按零件类型）"""
    part_type: PartType
    views: List[ViewConfig]           # 视图组合（可动态增删）
    scale_mode: str                   # 比例策略：auto_fill / max_fit / adaptive
    target_coverage: Tuple[float, float]  # 目标占图幅比例范围
    need_isometric: bool = False      # 是否需要轴测图
    spacing: float = LAYOUT_GAP_DEFAULT  # 视图间距
    projection_type: str = field(default="first_angle")  # "first_angle" | "third_angle"
    layout_mode: str = field(default="auto")  # "auto"（约束布局自动填充）| "manual"（用户辅助指定位置）


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
    position_hint="above_title_block",   # 标题栏上方右侧区域
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
        if view.view_type == ViewType.DETAIL:
            # 局部放大视图：region 为模型空间绝对坐标，占位尺寸 = 直径（2*radius）
            if view.region is not None:
                radius = float(view.region.get("radius", 0.0))
                if radius > 0:
                    sizes[view.name] = (radius * 2.0, radius * 2.0)
                else:
                    sizes[view.name] = (box.dx * 0.3, box.dz * 0.3)
            else:
                sizes[view.name] = (box.dx * 0.3, box.dz * 0.3)
        elif view.view_type == ViewType.SECTION:
            # 剖视视图：近似取父视图同尺寸（后续可由 parent_id 精确计算）
            if view.name == ViewName.FRONT:
                sizes[view.name] = (box.dx, box.dz)
            elif view.name == ViewName.TOP:
                sizes[view.name] = (box.dx, box.dy)
            elif view.name in (ViewName.LEFT, ViewName.RIGHT):
                sizes[view.name] = (box.dy, box.dz)
            else:
                sizes[view.name] = (box.dx, box.dz)
        elif view.view_type == ViewType.AUXILIARY:
            # 辅助视图：默认取主视尺寸作为占位
            sizes[view.name] = (box.dx * 0.8, box.dz * 0.8)
        elif view.name == ViewName.FRONT:
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
    names = _resolve_sw_predefined_views().get(view_name, [])
    if attempt < len(names):
        return names[attempt]
    return None


def _normalize_region(data: Any) -> Optional[Dict[str, Any]]:
    """校验并归一化 region 字段：{"center": (x,y,z), "radius": float}"""
    if data is None:
        return None
    if not isinstance(data, dict) or "radius" not in data:
        raise ValueError(
            f"region 必须为 {{'center': (x,y,z), 'radius': float}} 字典，实际: {data!r}"
        )
    center = data.get("center")
    if center is not None:
        center = tuple(float(v) for v in center)
    return {"center": center, "radius": float(data["radius"])}


def _normalize_cut_line(data: Any) -> Optional[List[Tuple[float, ...]]]:
    """校验并归一化 cut_line 字段：剖切线 polyline（模型空间坐标）"""
    if data is None:
        return None
    if not isinstance(data, (list, tuple)):
        raise ValueError(f"cut_line 必须为点列表，实际: {data!r}")
    return [tuple(float(v) for v in p) for p in data]


def _create_view_config_from_dict(data: Dict[str, Any]) -> ViewConfig:
    """从字典创建 ViewConfig（供 apply_overrides 使用）"""
    name_str = data.get("name", "front")
    name = ViewName(name_str) if isinstance(name_str, str) else name_str

    view_type_str = data.get("view_type", "standard")
    view_type = ViewType(view_type_str) if isinstance(view_type_str, str) else view_type_str

    return ViewConfig(
        name=name,
        display_name=data.get("display_name", name.value),
        sw_names=data.get("sw_names", SW_PREDEFINED_VIEWS.get(name, [f"*{name.value}"])),
        position_hint=data.get("position_hint", "auto"),
        id=data.get("id", ""),
        view_type=view_type,
        parent_id=data.get("parent_id"),
        scale=data.get("scale", "auto"),
        position_mode=data.get("position_mode", "auto"),
        position_params=data.get("position_params", {}),
        region=_normalize_region(data.get("region")),
        cut_line=_normalize_cut_line(data.get("cut_line")),
    )


def _update_view_config(view: ViewConfig, data: Dict[str, Any]) -> ViewConfig:
    """用字典增量更新 ViewConfig（保留未覆盖字段）"""
    kwargs: Dict[str, Any] = {}
    for key in (
        "name", "display_name", "sw_names", "position_hint",
        "id", "view_type", "parent_id", "scale", "position_mode",
        "position_params", "region", "cut_line",
    ):
        if key not in data:
            continue
        val = data[key]
        if key == "name" and isinstance(val, str):
            val = ViewName(val)
        elif key == "view_type" and isinstance(val, str):
            val = ViewType(val)
        elif key == "region":
            val = _normalize_region(val)
        elif key == "cut_line":
            val = _normalize_cut_line(val)
        kwargs[key] = val
    return replace(view, **kwargs)


def apply_overrides(strategy: ViewStrategy, overrides: Dict[str, Any]) -> ViewStrategy:
    """
    合并用户覆盖参数到视图策略（深拷贝，不修改原策略）

    支持的覆盖项：
    - views: List[Dict] 视图列表覆盖（增删改）
      每个 Dict 支持：
        - id / name: 视图标识（必填）
        - action: "add" | "update" | "remove"（默认 "update"）
        - 其他 ViewConfig 字段
    - scale_mode: str
    - spacing: float
    - target_coverage: Tuple[float, float]
    - need_isometric: bool
    - projection_type: str

    Returns:
        ViewStrategy: 应用覆盖后的新策略
    """
    # 深拷贝视图列表
    new_views = [deepcopy(v) for v in strategy.views]

    # 提取非视图覆盖
    strategy_kwargs: Dict[str, Any] = {
        "part_type": strategy.part_type,
        "views": new_views,
        "scale_mode": overrides.get("scale_mode", strategy.scale_mode),
        "target_coverage": tuple(overrides["target_coverage"]) if "target_coverage" in overrides else strategy.target_coverage,
        "need_isometric": overrides.get("need_isometric", strategy.need_isometric),
        "spacing": overrides.get("spacing", strategy.spacing),
        "projection_type": overrides.get("projection_type", strategy.projection_type),
        "layout_mode": overrides.get("layout_mode", strategy.layout_mode),
    }

    new_strategy = ViewStrategy(**strategy_kwargs)

    # 应用视图级覆盖
    for view_override in overrides.get("views", []):
        action = view_override.get("action", "update")
        view_id = view_override.get("id") or view_override.get("name")
        if not view_id:
            logger.warning(f"跳过无标识的视图覆盖项: {view_override}")
            continue

        if action == "remove":
            new_strategy.views = [
                v for v in new_strategy.views
                if v.id != view_id and v.name.value != view_id
            ]
        elif action == "add":
            new_view = _create_view_config_from_dict(view_override)
            new_strategy.views.append(new_view)
        elif action == "update":
            updated = False
            for i, view in enumerate(new_strategy.views):
                if view.id == view_id or view.name.value == view_id:
                    new_strategy.views[i] = _update_view_config(view, view_override)
                    updated = True
                    break
            if not updated:
                # 未找到则新增（upsert 语义）
                new_view = _create_view_config_from_dict(view_override)
                new_strategy.views.append(new_view)
        else:
            logger.warning(f"未知视图覆盖动作: {action}")

    # positions_override：{视图id: [x, y]} → 强制绝对定位，绕过约束布局
    for view_id, pos in (overrides.get("positions_override") or {}).items():
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            logger.warning(f"positions_override[{view_id!r}] 非法坐标，跳过: {pos!r}")
            continue
        hit = False
        for i, view in enumerate(new_strategy.views):
            if view.id == view_id or view.name.value == view_id:
                new_strategy.views[i] = replace(
                    view,
                    position_mode="absolute",
                    position_params={"x": float(pos[0]), "y": float(pos[1])},
                )
                hit = True
                break
        if not hit:
            logger.warning(f"positions_override 未匹配到视图: {view_id}")

    return new_strategy


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
    resolved_names = _resolve_sw_predefined_views()
    views_data = []
    for view in strategy.views:
        vw, vh = view_sizes.get(view.name, (100.0, 100.0))
        view_data: Dict[str, Any] = {
            "id": view.id,
            "name": view.name.value,
            "display_name": view.display_name,
            "view_type": view.view_type.value,
            "sw_names": resolved_names.get(view.name, view.sw_names),
            "size_mm": {"width": round(vw, 4), "height": round(vh, 4)},
            "position_hint": view.position_hint,
            "scale": view.scale if view.scale != "auto" else f"1:{scale_den:g}",
            "position_mode": view.position_mode,
            "position_params": view.position_params,
        }
        if view.parent_id is not None:
            view_data["parent_id"] = view.parent_id
        if view.region is not None:
            view_data["region"] = view.region
        if view.cut_line is not None:
            view_data["cut_line"] = view.cut_line
        views_data.append(view_data)

    return {
        "part_type": part_type.value,
        "strategy": {
            "scale_mode": strategy.scale_mode,
            "target_coverage": strategy.target_coverage,
            "spacing": strategy.spacing,
            "projection_type": strategy.projection_type,
            "layout_mode": strategy.layout_mode,
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


def resolve_scale_denominator(value: Any, main_den: float) -> float:
    """解析视图比例字段 → 比例分母（供布局引擎多比例并存使用）。

    - None / "auto" → 主比例分母
    - 数值（如 2.0）→ 相对主比例的放大倍数，分母 = 主分母 / 倍数
      （如主视 1:50、局部放大 scale=2.0 → 1:25）
    - 字符串 "1:N" → 绝对比例分母 N
    非法输入回退主比例分母，不抛异常（如实回退，禁止编造）。
    """
    if value is None or value == "auto":
        return float(main_den)
    if isinstance(value, (int, float)):
        return float(main_den) / float(value) if float(value) > 0 else float(main_den)
    if isinstance(value, str) and value.startswith("1:"):
        try:
            den = float(value.split(":", 1)[1])
            return den if den > 0 else float(main_den)
        except ValueError:
            return float(main_den)
    return float(main_den)


def to_layout_input(
    strategy_result: Dict[str, Any],
    scale_den: float,
) -> List[Dict[str, Any]]:
    """
    将策略结果转换为布局引擎输入格式

    Returns:
        List[Dict]: 视图列表，每项包含 id/name/view_type/bounding_box（已按各自
        比例缩放）/position_hint/position_mode/position_params，及可选
        parent_id/region/cut_line
    """
    views = []
    for view_data in strategy_result.get("views", []):
        size = view_data.get("size_mm", {"width": 100, "height": 100})
        # 多比例并存：每个视图按自己的比例分母缩放
        per_den = resolve_scale_denominator(view_data.get("scale"), scale_den)
        scaled_w = size["width"] / per_den
        scaled_h = size["height"] / per_den
        layout_item: Dict[str, Any] = {
            "id": view_data.get("id", view_data["name"]),
            "name": view_data["name"],
            "view_type": view_data.get("view_type", "standard"),
            "scale_denominator": per_den,
            "bounding_box": {
                "min_x": 0.0,
                "min_y": 0.0,
                "max_x": scaled_w,
                "max_y": scaled_h,
            },
            "position_hint": view_data.get("position_hint", ""),
            "position_mode": view_data.get("position_mode", "auto"),
            "position_params": view_data.get("position_params", {}),
        }
        if "parent_id" in view_data:
            layout_item["parent_id"] = view_data["parent_id"]
        if "region" in view_data:
            layout_item["region"] = view_data["region"]
        if "cut_line" in view_data:
            layout_item["cut_line"] = view_data["cut_line"]
        views.append(layout_item)
    return views
