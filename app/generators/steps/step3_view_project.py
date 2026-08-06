"""
Step 3: 视图投影（方案B重写 2026-08-02：SW 原生真图纸骨架 + B-M1智能布局）

技术路线（SW API 原生优先铁律；DXF 拆线路径已删除，view_extractor 不再被引用）：
- 唯一路径（sw_api）：sw_drawing.create_drawing_sync —— 读模型包围盒 →
  类型识别 → 视图策略 → 布局引擎按企业模板实际图幅算比例/第一角摆位 
  → NewDocument(.drwdot 企业模板)
  → CreateDrawViewFromModelView3 × N（中文预定义视图名，第一角）→ 隐藏线可见
  → 迭代重定位 → 保存中间 SLDDRW + PNG 真图快照

引擎选择：ctx.parameters["engine"] 仅支持 "sw_api"（默认）；
SW 不可用 → 直接 SWException(GEN_SW_NOT_AVAILABLE)，无回退路径

契约：views.json 原契约字段只加不改（views/layout 保留；方案B 不再产出
ezdxf 拆线 entities，entities/hidden_lines/center_lines 如实为空列表——
真视图在 SLDDRW 里，由 SW 原生维护，禁止编造线稿数据）；
新增字段：drawing_path/snapshot_path/scale_denominator/sheet_size/type_info

坐标系约定（模型空间 --scale/平移--> 图纸空间）：
- scale：GB 标准比例字符串（如 "1:50"），由布局引擎按企业模板实际图幅自动计算
- layout.view_positions：图纸坐标（图幅 mm，已含比例，实测轮廓为准），
  第一角布局：俯视在主视正下方、左视在主视正右方
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.generators.models import StepContext
from app.generators.sw_com import run_sw
from app.core.exceptions import SWException, ErrorCode

# B-M1 智能骨架模块导入
from app.generators.type_recognition import (
    PartType,
    BoundingBox,
    recognize_from_sw_model,
    to_dict as type_result_to_dict,
)
from app.generators.view_strategy import (
    ViewName,
    ViewStrategy,
    get_view_strategy,
    compute_main_view_direction,
    compute_view_sizes,
    select_scale_for_sheet,
    compute_adaptive_scale,
    create_view_strategy_result,
    to_layout_input,
    GB_SCALE_RATIOS,
    SHEET_A3_WIDTH,
    SHEET_A3_HEIGHT,
    LAYOUT_MARGIN,
    LAYOUT_GAP_DEFAULT,
    resolve_scale_denominator,
)
from app.generators.layout import (
    LayoutEngine as _PureLayoutEngine,
    LayoutView as _LayoutView,
)

logger = logging.getLogger(__name__)

# ---- 布局常量（单位：mm；图纸坐标系 = 横向图幅，y 向上）----
# GB A 系列横向图幅（选型按 A3→A0 升序）
_SHEET_SIZES = {
    "A3": (SHEET_A3_WIDTH, SHEET_A3_HEIGHT),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}
_BASE_SHEET = "A3"  # 比例决策基准图幅

# 第一角投影布局参数
_FIRST_ANGLE_GAP_X = 25.0  # 水平间距（主视-侧视）
_FIRST_ANGLE_GAP_Y = 25.0  # 垂直间距（主视-俯视）

# 标题栏禁放区：优先从企业模板 .drwdot 的 TitleBlock.GetBoundingBox 实测，
# 测不到时采用保底高度（mm）
_TITLE_BLOCK_FALLBACK_HEIGHT = 60.0
_BOM_HEADER_EST = 20.0
_BOM_ROW_H_EST = 15.0
_FRAME_MARGIN = 10.0

# 最大比例重算次数
_MAX_SCALE_RETRIES = 3

# Position 锚点修正收敛判据（米）= 0.2mm
_POS_TOL_M = 2e-4


def _next_scale_den(den: float) -> float:
    """GB 比例系列中 den 的下一档（更小比例）；末档 → 沿用末档"""
    seq = list(GB_SCALE_RATIOS)
    try:
        i = seq.index(den)
    except ValueError:
        return den
    return float(seq[min(i + 1, len(seq) - 1)])


def measure_title_block_rect(
    drawing: Any,
    sheet_w: float,
    sheet_h: float,
    fallback_height: float = _TITLE_BLOCK_FALLBACK_HEIGHT,
) -> Tuple[float, float, float, float]:
    """
    从企业模板 .drwdot 的 TitleBlock.GetBoundingBox 实测标题栏禁放区（mm）。
    测不到则返回全宽、保底高度的底部禁区，确保任何视图不得进入标题栏区。
    """
    try:
        tb = getattr(drawing, "TitleBlock", None)
        if tb is None:
            try:
                sheet = drawing.GetCurrentSheet()
                tb = getattr(sheet, "TitleBlock", None)
            except Exception:
                tb = None
        if tb is not None:
            box = tb.GetBoundingBox()
            if box and len(box) >= 4:
                vals = [float(v) * 1000.0 for v in box[:4]]
                x1, y1, x2, y2 = vals
                x = min(x1, x2)
                y = min(y1, y2)
                w = abs(x2 - x1)
                h = abs(y2 - y1)
                if w > 0 and h > 0:
                    return (x, y, w, h)
    except Exception:
        pass
    return (0.0, 0.0, sheet_w, fallback_height)


class FirstAngleLayoutEngine:
    """
    第一角投影布局引擎（B-M1）

    摆位规则由 ViewStrategy.views[].position_hint 驱动：
    - center_upper：主视中上居中
    - below_front：主视正下方
    - right_of_front：主视右侧
    - above_title_block：标题栏上方右侧区域
    """

    def __init__(
        self,
        sheet_width: float,
        sheet_height: float,
        spacing: float = _FIRST_ANGLE_GAP_X,
        title_block_bbox: Optional[Tuple[float, float, float, float]] = None,
    ):
        self.sheet_w = sheet_width
        self.sheet_h = sheet_height
        self.spacing = spacing
        self.margin = LAYOUT_MARGIN
        self.title_block_bbox = title_block_bbox

    def layout(
        self,
        view_sizes: Dict[str, Tuple[float, float]],
        scale_den: float,
        strategy: ViewStrategy,
        view_objects: Optional[Dict[str, Any]] = None,
        drawing: Optional[Any] = None,
        max_rearrange: int = 2,
    ) -> Optional[Dict[str, Any]]:
        """
        按 ViewConfig.position_hint 计算第一角投影布局。

        若传入 view_objects（已插入的 SW 视图对象），则：
        1. 按 hint 计算理想位置；
        2. 设置 Position；
        3. GetOutline 实测轮廓并修正 Position 锚点；
        4. 校验标题栏禁放区与图幅边界；
        5. 若压标题栏，优先缩小轴测比例或整体上移；
        6. 超过 max_rearrange 次仍未解决 → 抛出 SWException（禁止静默）。

        未传入 view_objects 时退化为纯几何估算，超限返回 None。
        """
        scaled: Dict[str, Tuple[float, float]] = {
            name: (w / scale_den, h / scale_den)
            for name, (w, h) in view_sizes.items()
        }

        positions = self._compute_ideal_positions(scaled, strategy)
        if positions is None:
            return None

        if not view_objects:
            if not self._validate_positions(positions):
                return None
            return positions

        # ---- 实测驱动路径 ----
        iso_den = scale_den
        self._apply_positions_and_correct(view_objects, positions, drawing)
        measured = self._measure_positions(view_objects, positions)

        if self._validate_positions(measured):
            return measured

        # 重排：优先缩小轴测比例或上移
        for _ in range(max_rearrange):
            adjusted = False

            if "isometric" in view_objects and "isometric" in positions:
                iso_pos = measured["isometric"]
                tb = self._title_block_rect()
                tb_top = tb[1] + tb[3]

                # 冲突来源：压标题栏或越下边界
                if (
                    self._rect_intersects_title_block(iso_pos)
                    or iso_pos["y"] < tb_top + self.margin
                ):
                    new_iso_den = _next_scale_den(iso_den)
                    if new_iso_den != iso_den:
                        self._set_view_scale(
                            view_objects["isometric"], new_iso_den
                        )
                        iso_den = new_iso_den
                        scaled["isometric"] = (
                            view_sizes["isometric"][0] / iso_den,
                            view_sizes["isometric"][1] / iso_den,
                        )
                        adjusted = True
                    else:
                        # 已无更小比例，尝试整体上移
                        shift = (
                            tb_top + self.margin
                            - positions["isometric"]["y"]
                        )
                        if shift > 0:
                            positions["isometric"]["y"] += shift
                            if (
                                positions["isometric"]["y"]
                                + positions["isometric"]["height"]
                                > self.sheet_h - self.margin
                            ):
                                raise SWException(
                                    "Isometric view cannot fit above title block "
                                    "after rearrangement",
                                    error_code=ErrorCode.GEN_STEP_FAILED,
                                )
                            adjusted = True
                elif iso_pos["y"] < self.margin:
                    # 非标题栏导致的下边界冲突，尝试上移
                    shift = self.margin - iso_pos["y"]
                    positions["isometric"]["y"] += shift
                    adjusted = True

            if adjusted:
                # 比例变化后需要重新计算所有理想位置
                positions = self._compute_ideal_positions(scaled, strategy)
                if positions is None:
                    raise SWException(
                        "Layout recomputation failed during rearrangement",
                        error_code=ErrorCode.GEN_STEP_FAILED,
                    )
                self._apply_positions_and_correct(
                    view_objects, positions, drawing
                )
                measured = self._measure_positions(view_objects, positions)
                if self._validate_positions(measured):
                    return measured
            else:
                break

        raise SWException(
            "Layout violates title block or sheet boundary after "
            f"{max_rearrange} rearrangement attempts",
            error_code=ErrorCode.GEN_STEP_FAILED,
        )

    def _compute_ideal_positions(
        self,
        scaled: Dict[str, Tuple[float, float]],
        strategy: ViewStrategy,
    ) -> Optional[Dict[str, Any]]:
        """委托纯几何布局引擎（app.generators.layout）计算理想位置。

        多比例并存：各视图按自身 scale 字段（resolve_scale_denominator）
        相对主比例修正尺寸；positions 键保持视图名（下游契约不变）。
        """
        views: List[_LayoutView] = []
        by_name = {vc.name.value: vc for vc in strategy.views}
        for name, (w, h) in scaled.items():
            vc = by_name.get(name)
            if vc is not None:
                # 多比例：主比例尺寸 × (主分母 / 本分母)
                per_den = resolve_scale_denominator(vc.scale, 1.0)
                factor = 1.0 / per_den if per_den > 0 else 1.0
                parent_name = None
                if vc.parent_id:
                    parent_vc = next(
                        (c for c in strategy.views if c.id == vc.parent_id), None
                    )
                    parent_name = parent_vc.name.value if parent_vc else None
                views.append(_LayoutView(
                    id=name,
                    name=name,
                    view_type=vc.view_type.value,
                    width=w * factor,
                    height=h * factor,
                    position_mode=vc.position_mode,
                    position_hint=vc.position_hint,
                    position_params=dict(vc.position_params),
                    parent_id=parent_name,
                ))
            else:
                # strategy 未声明的视图兜底：isometric 按轴测，其余按标准视图
                views.append(_LayoutView(
                    id=name,
                    name=name,
                    view_type="isometric" if name == "isometric" else "standard",
                    width=w,
                    height=h,
                ))

        engine = _PureLayoutEngine(
            self.sheet_w,
            self.sheet_h,
            spacing=self.spacing,
            margin=self.margin,
            title_block_bbox=self.title_block_bbox,
            projection_type=getattr(strategy, "projection_type", "first_angle"),
            layout_mode=getattr(strategy, "layout_mode", "auto"),
        )
        return engine.layout(views)

    def _title_block_rect(self) -> Tuple[float, float, float, float]:
        """返回标题栏禁放区 (x, y, w, h)，未提供则使用全宽保底高度"""
        if self.title_block_bbox is not None:
            return self.title_block_bbox
        return (0.0, 0.0, self.sheet_w, _TITLE_BLOCK_FALLBACK_HEIGHT)

    def _rect_intersects_title_block(self, pos: Dict[str, Any]) -> bool:
        """判断视图矩形是否与标题栏禁放区相交"""
        return self._is_overlap(
            (pos["x"], pos["y"], pos["width"], pos["height"]),
            self._title_block_rect(),
        )

    def _find_main_view(
        self, scaled: Dict[str, Tuple[float, float]]
    ) -> Optional[str]:
        """确定主视图名称：front/left/right 中最宽者（长梁类长视图作主视）"""
        candidates = [n for n in ("front", "left", "right") if n in scaled]
        if candidates:
            return max(candidates, key=lambda n: scaled[n][0])
        return next(iter(scaled.keys())) if scaled else None

    def _make_pos(self, x: float, y: float, w: float, h: float) -> Dict[str, Any]:
        """创建位置字典"""
        return {
            "x": round(x, 4),
            "y": round(y, 4),
            "width": round(w, 4),
            "height": round(h, 4),
        }

    def _is_overlap(
        self,
        rect1: Tuple[float, float, float, float],
        rect2: Tuple[float, float, float, float],
    ) -> bool:
        """检查两个矩形是否重叠 (x, y, w, h)"""
        x1, y1, w1, h1 = rect1
        x2, y2, w2, h2 = rect2
        return (
            x1 < x2 + w2 and x1 + w1 > x2 and
            y1 < y2 + h2 and y1 + h1 > y2
        )

    def _validate_positions(self, positions: Dict[str, Any]) -> bool:
        """校验所有视图在图幅内且不与标题栏禁放区相交"""
        for name, pos in positions.items():
            if pos["x"] < 0 or pos["y"] < 0:
                return False
            if pos["x"] + pos["width"] > self.sheet_w:
                return False
            if pos["y"] + pos["height"] > self.sheet_h:
                return False
            if self._rect_intersects_title_block(pos):
                return False
        return True

    def _set_view_scale(self, view: Any, den: float) -> None:
        """设置视图比例（ScaleDecimal = 1/den）"""
        try:
            view.ScaleDecimal = 1.0 / den
        except Exception:
            pass

    def _set_view_position(self, view: Any, x_m: float, y_m: float) -> None:
        """设置视图 Position（米），兼容 VARIANT safearray"""
        try:
            import pythoncom
            import win32com.client
            view.Position = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_R8, [x_m, y_m]
            )
        except Exception:
            view.Position = [x_m, y_m]

    def _measure_outlines(
        self,
        view_objects: Dict[str, Any],
        names,
    ) -> Dict[str, Tuple[float, float, float, float]]:
        """GetOutline 实测轮廓（米）；任一失败 → SWException（禁止静默）"""
        outlines: Dict[str, Tuple[float, float, float, float]] = {}
        for name in names:
            try:
                outlines[name] = tuple(
                    float(v) for v in view_objects[name].GetOutline
                )
            except Exception as e:
                raise SWException(
                    f"Cannot measure view outline for {name}: {e}",
                    error_code=ErrorCode.GEN_STEP_FAILED,
                    detail=str(e),
                )
        return outlines

    def _apply_positions_and_correct(
        self,
        view_objects: Dict[str, Any],
        positions: Dict[str, Any],
        drawing: Optional[Any],
    ) -> None:
        """设 Position → 重建 → GetOutline 实测 → 按轮廓中心修正锚点"""
        names = [n for n in positions if n in view_objects]

        for name in names:
            pos = positions[name]
            cx_m = (pos["x"] + pos["width"] / 2.0) / 1000.0
            cy_m = (pos["y"] + pos["height"] / 2.0) / 1000.0
            self._set_view_position(view_objects[name], cx_m, cy_m)

        if drawing is not None:
            try:
                drawing.ForceRebuild3(True)
            except Exception:
                pass

        for _ in range(3):
            max_delta = 0.0
            outlines = self._measure_outlines(view_objects, names)
            for name in names:
                ol = outlines[name]
                cx_m = (ol[0] + ol[2]) / 2.0
                cy_m = (ol[1] + ol[3]) / 2.0
                target = positions[name]
                tx_m = (target["x"] + target["width"] / 2.0) / 1000.0
                ty_m = (target["y"] + target["height"] / 2.0) / 1000.0
                dx = tx_m - cx_m
                dy = ty_m - cy_m
                max_delta = max(max_delta, abs(dx), abs(dy))
                if abs(dx) > _POS_TOL_M or abs(dy) > _POS_TOL_M:
                    cur = [float(v) for v in view_objects[name].Position]
                    self._set_view_position(
                        view_objects[name], cur[0] + dx, cur[1] + dy
                    )
            if drawing is not None:
                try:
                    drawing.ForceRebuild3(True)
                except Exception:
                    pass
            if max_delta <= _POS_TOL_M:
                break

    def _measure_positions(
        self,
        view_objects: Dict[str, Any],
        positions: Dict[str, Any],
    ) -> Dict[str, Any]:
        """以 GetOutline 实测结果（转 mm）作为最终位置，未实测视图保留计算值"""
        names = [n for n in positions if n in view_objects]
        outlines = self._measure_outlines(view_objects, names)
        measured: Dict[str, Any] = {}
        for name in names:
            ol = outlines[name]
            measured[name] = {
                "x": round(ol[0] * 1000.0, 4),
                "y": round(ol[1] * 1000.0, 4),
                "width": round((ol[2] - ol[0]) * 1000.0, 4),
                "height": round((ol[3] - ol[1]) * 1000.0, 4),
            }
        for name, pos in positions.items():
            if name not in measured:
                measured[name] = dict(pos)
        return measured


def _compute_layout_with_retry(
    view_sizes: Dict[str, Tuple[float, float]],
    sheet_w: float,
    sheet_h: float,
    strategy: ViewStrategy,
    task_id: str = "",
) -> Tuple[Optional[Dict[str, Any]], float, int]:
    """
    计算布局（带比例重试）
    
    Returns:
        Tuple[positions, scale_den, retry_count]
    """
    engine = FirstAngleLayoutEngine(sheet_w, sheet_h, strategy.spacing)
    
    # 从大到小尝试比例
    for retry in range(_MAX_SCALE_RETRIES + 1):
        for den in GB_SCALE_RATIOS:
            positions = engine.layout(view_sizes, den, strategy)
            if positions is not None:
                return positions, den, retry
        
        # 重算：减小间距
        if retry < _MAX_SCALE_RETRIES:
            new_spacing = max(20.0, strategy.spacing - 5.0)
            engine.spacing = new_spacing
            logger.warning(
                f"[Task:{task_id}] step3: layout retry {retry + 1}, "
                f"spacing reduced to {new_spacing}"
            )
    
    # 所有重算失败
    logger.error(f"[Task:{task_id}] step3: layout failed after {_MAX_SCALE_RETRIES} retries")
    return None, GB_SCALE_RATIOS[-1], _MAX_SCALE_RETRIES


def _select_sheet_by_content(
    view_sizes: Dict[str, Tuple[float, float]],
    scale_den: float,
    bom_rows: int = 0,
    task_id: str = "",
) -> Tuple[str, float, float]:
    """
    根据内容选择图幅
    
    Returns:
        Tuple[sheet_name, width, height]
    """
    strategy = get_view_strategy(PartType.BEAM)  # 使用最复杂的布局策略测试
    engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
    
    for name, (w, h) in _SHEET_SIZES.items():
        engine.sheet_w = w
        engine.sheet_h = h
        positions = engine.layout(view_sizes, scale_den, strategy)
        if positions is None:
            continue
        
        # 检查BOM空间（标题栏顶使用保底高度，实测值由调用方传入 title_block_bbox）
        if bom_rows > 0:
            bom_h = _BOM_HEADER_EST + _BOM_ROW_H_EST * bom_rows
            if _TITLE_BLOCK_FALLBACK_HEIGHT + bom_h > h - _FRAME_MARGIN:
                continue
        
        return name, w, h
    
    logger.warning(f"[Task:{task_id}] step3: content exceeds A0, fallback to A0")
    return "A0", _SHEET_SIZES["A0"][0], _SHEET_SIZES["A0"][1]


def _build_layout_b_m1(
    bounding_box: BoundingBox,
    part_type: PartType,
    sheet_w: float = SHEET_A3_WIDTH,
    sheet_h: float = SHEET_A3_HEIGHT,
    bom_rows: int = 0,
    task_id: str = "",
) -> Dict[str, Any]:
    """
    B-M1 智能布局引擎
    
    1. 类型识别 → 2. 视图策略 → 3. 比例选择 → 4. 第一角布局
    """
    strategy = get_view_strategy(part_type)
    
    # 计算主视方向
    main_direction = compute_main_view_direction(bounding_box)
    logger.info(f"[Task:{task_id}] step3: main view direction = {main_direction.value}")
    
    # 计算各视图尺寸
    view_sizes_raw = compute_view_sizes(bounding_box, strategy)
    
    # 转换为字符串键
    view_sizes = {name.value: (w, h) for name, (w, h) in view_sizes_raw.items()}
    
    # 选择比例
    if strategy.scale_mode == "adaptive":
        # 自适应比例（标准件单视图）
        scale_den = compute_adaptive_scale(
            view_sizes_raw, sheet_w, sheet_h, strategy.target_coverage
        )
        retry_count = 0
        engine = FirstAngleLayoutEngine(sheet_w, sheet_h, strategy.spacing)
        positions = engine.layout(view_sizes, scale_den, strategy)
    else:
        # 最大适配比例
        positions, scale_den, retry_count = _compute_layout_with_retry(
            view_sizes, sheet_w, sheet_h, strategy, task_id
        )
    
    if positions is None:
        logger.warning(f"[Task:{task_id}] step3: layout overflows, using fallback")
        # 回退：最小比例，超大图幅
        scale_den = GB_SCALE_RATIOS[-1]
        engine = FirstAngleLayoutEngine(1e6, 1e6, strategy.spacing)
        positions = engine.layout(view_sizes, scale_den, strategy)
    
    scale_str = f"1:{scale_den:g}"
    
    logger.info(
        f"[Task:{task_id}] step3 B-M1 layout: "
        f"type={part_type.value}, scale={scale_str}, "
        f"sheet={sheet_w}x{sheet_h}, retries={retry_count}"
    )
    
    return {
        "sheet_size": f"{sheet_w:g}x{sheet_h:g}",
        "sheet_width": sheet_w,
        "sheet_height": sheet_h,
        "orientation": "landscape",
        "scale": scale_str,
        "scale_denominator": scale_den,
        "view_positions": positions,
        "strategy": {
            "part_type": part_type.value,
            "views": [v.name.value for v in strategy.views],
            "scale_mode": strategy.scale_mode,
            "main_direction": main_direction.value,
        },
        "retry_count": retry_count,
    }


# ---- 以下兼容旧代码的接口 ----

def _compute_scale_denominator(
    views: List[Dict[str, Any]],
    task_id: str = "",
    sheet: str = _BASE_SHEET,
) -> float:
    """
    兼容旧接口：按视图计算比例分母
    """
    if not views:
        raise SWException(
            "No views for scale computation",
            error_code=ErrorCode.GEN_STEP_FAILED,
            task_id=task_id,
            step=3,
        )
    
    sizes = [
        (
            v["name"],
            v["bounding_box"]["max_x"] - v["bounding_box"]["min_x"],
            v["bounding_box"]["max_y"] - v["bounding_box"]["min_y"],
        )
        for v in views
    ]
    
    if max(max(w, h) for _, w, h in sizes) <= 0:
        raise SWException(
            f"Degenerate view size for scale computation: {sizes}",
            error_code=ErrorCode.GEN_STEP_FAILED,
            task_id=task_id,
            step=3,
        )
    
    sheet_w, sheet_h = _SHEET_SIZES.get(sheet, _SHEET_SIZES[_BASE_SHEET])
    
    # 使用B-M1引擎计算
    strategy = get_view_strategy(PartType.PLATE)
    positions, scale_den, _ = _compute_layout_with_retry(
        {n: (w, h) for n, w, h in sizes},
        sheet_w,
        sheet_h,
        strategy,
        task_id,
    )
    
    if positions is None:
        logger.warning(f"[Task:{task_id}] step3: views do not fit {sheet}")
        return GB_SCALE_RATIOS[-1]
    
    return scale_den


def _select_sheet(
    sizes: List[Tuple[str, float, float]],
    den: float,
    bom_rows: int = 0,
    task_id: str = "",
) -> str:
    """兼容旧接口：选择图幅"""
    view_sizes = {n: (w, h) for n, w, h in sizes}
    sheet_name, _, _ = _select_sheet_by_content(view_sizes, den, bom_rows, task_id)
    return sheet_name


def _first_angle_positions(
    sizes: List[Tuple[str, float, float]],
    den: float,
    sheet_w: float,
    sheet_h: float,
) -> Optional[Dict[str, Any]]:
    """兼容旧接口：第一角布局"""
    view_sizes = {n: (w, h) for n, w, h in sizes}
    strategy = get_view_strategy(PartType.PLATE)
    engine = FirstAngleLayoutEngine(sheet_w, sheet_h, strategy.spacing)
    return engine.layout(view_sizes, den, strategy)


class ViewProjectExecutor:
    """
    Step 3 执行器: 视图投影（B-M1智能骨架集成）

    输入: ctx.parameters["source_file"]、ctx.parameters["views"]（可选，由类型策略决定）、
          ctx.parameters["engine"]（仅支持 "sw_api"，默认）
    输出: {"views": [...], "layout": {...}, "drawing_path", "snapshot_path",
           "scale_denominator", "sheet_size", "type_info"}，完整 JSON 落盘 output/views.json
    SW 不可用 → 直接 SWException(GEN_SW_NOT_AVAILABLE)，无回退路径
    """

    @staticmethod
    def _estimate_bom_rows(previous_results: Dict[int, Any]) -> int:
        """估计聚合后 BOM 行数"""
        try:
            geom = previous_results.get(2) or {}
            keys = set()
            for item in geom.get("bom") or []:
                if not isinstance(item, dict) or item.get("is_suppressed"):
                    continue
                stem = Path(str(item.get("path") or "")).stem.strip()
                keys.add(stem or str(item.get("name") or ""))
            return len(keys)
        except Exception:
            return 0

    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        source_file = ctx.parameters.get("source_file", "")
        if not source_file or not Path(source_file).exists():
            raise SWException(
                f"Source file not found: {source_file}",
                error_code=ErrorCode.GEN_INVALID_FILE,
                task_id=ctx.task_id,
                step=ctx.step,
            )

        # views 参数（可选）：提供时必须合法（契约只加不改；缺省由类型策略决定）
        view_names = ctx.parameters.get("views")
        if view_names is not None:
            _SUPPORTED_VIEWS = {"front", "top", "left", "right", "isometric"}
            if isinstance(view_names, list) and len(view_names) == 0:
                raise SWException(
                    "views must not be empty",
                    error_code=ErrorCode.GEN_INVALID_FILE,
                    task_id=ctx.task_id,
                    step=ctx.step,
                )
            for name in view_names:
                if name not in _SUPPORTED_VIEWS:
                    raise SWException(
                        f"Unsupported view: {name}",
                        error_code=ErrorCode.GEN_UNSUPPORTED_FEATURE,
                        task_id=ctx.task_id,
                        step=ctx.step,
                    )

        engine = ctx.parameters.get("engine", "sw_api")
        if engine != "sw_api":
            raise SWException(
                f"Unsupported engine: {engine}",
                error_code=ErrorCode.GEN_UNSUPPORTED_FEATURE,
                task_id=ctx.task_id,
                step=ctx.step,
            )

        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        bom_rows = self._estimate_bom_rows(ctx.previous_results)

        # 方案B：SW 原生建真 SLDDRW（B-M1智能布局集成）
        from app.generators import sw_drawing
        try:
            logger.info(
                f"[Task:{ctx.task_id}] SW native drawing with B-M1 from {source_file}"
            )
            # 传入B-M1标志，让sw_drawing使用新布局
            sw_result = await run_sw(
                sw_drawing.create_drawing_sync,
                source_file,
                None,  # views由B-M1策略决定
                str(output_dir),
                bom_rows,
                ctx.task_id,
                None,
                True,  # use_b_m1=True
            )
            for w in sw_result.get("warnings", []):
                logger.warning(f"[Task:{ctx.task_id}] step3: {w}")
        except SWException:
            raise
        except Exception as e:
            logger.exception(
                f"[Task:{ctx.task_id}] SW native drawing create failed: {e}"
            )
            raise SWException(
                f"SW native drawing create failed: {e}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
                task_id=ctx.task_id,
                step=ctx.step,
                detail=str(e),
            )

        # 组装 views.json：契约字段只加不改
        den = sw_result["scale_den"]
        scale_str = f"1:{den:g}"
        positions = sw_result["positions"]
        view_sizes = sw_result.get("view_sizes") or {}
        type_info = sw_result.get("type_info", {})
        
        # 获取实际使用的视图名
        view_names = list(positions.keys()) if positions else ["front", "top", "left"]
        
        views: List[Dict[str, Any]] = []
        for name in view_names:
            vsz = view_sizes.get(name) or {}
            views.append({
                "name": name,
                "display_name": self._get_display_name(name),
                "projection": "first_angle",
                "entities": [],
                "hidden_lines": [],
                "center_lines": [],
                "section_hatch": None,
                "bounding_box": {
                    "min_x": 0.0,
                    "min_y": 0.0,
                    "max_x": vsz.get("width", 0.0),
                    "max_y": vsz.get("height", 0.0),
                },
                "scale": scale_str,
            })
        
        warnings = list(sw_result.get("warnings") or [])
        result: Dict[str, Any] = {
            "views": views,
            "layout": {
                "sheet_size": sw_result["sheet"],
                "orientation": "landscape",
                "view_positions": positions,
            },
            "drawing_path": sw_result["drawing_path"],
            "snapshot_path": sw_result["snapshot_path"],
            "scale_denominator": den,
            "sheet_size": sw_result["sheet"],
            "type_info": type_info,  # B-M1新增
        }
        if warnings:
            result["warnings"] = warnings

        views_file = output_dir / "views.json"
        with open(views_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"[Task:{ctx.task_id}] SW native drawing done -> {views_file}")
        return result
    
    def _get_display_name(self, view_name: str) -> str:
        """获取视图显示名"""
        names = {
            "front": "主视图",
            "top": "俯视图",
            "left": "左视图",
            "right": "右视图",
            "isometric": "轴测图",
        }
        return names.get(view_name, view_name)
