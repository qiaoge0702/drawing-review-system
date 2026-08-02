"""
Step 3: 视图投影

技术路线（2026-08-01 老板批示：SW 原生导出 DXF，取代逐边提取；SW API 原生优先）：
- 唯一路径（sw_api）：sw_drawing.export_dxf_sync 插入三视图后 SW 原生 SaveAs
  导出 DXF，view_extractor.parse_exported_dxf 按视图区域/线型归一化为契约 entities

引擎选择：ctx.parameters["engine"] 仅支持 "sw_api"（默认）；
SW 不可用 → 直接 SWException(GEN_SW_NOT_AVAILABLE)，无回退路径
（2026-08-01 老板铁律：SW API 原生优先，STL/trimesh 回退路径已删除）

契约不变：views.json 结构与 docs/plans/04 第二节一致

坐标系约定（2026-07-31 老板确认，专业范式：模型空间 --scale/平移--> 图纸空间）：
- entities/hidden_lines/center_lines：视图局部坐标（实际尺寸 mm），
  原点 = 视图包围盒左下角（本模块 _build_layout 统一归一化，两条引擎路径同一契约）
- scale：GB 标准比例字符串（如 "1:50"），由布局引擎按图幅可用区域自动计算
- layout.view_positions：图纸坐标（图幅 mm，已含比例；图幅 A3→A0 自动选型），
  第一角布局：俯视在主视正下方、左视在主视正右方
  Step7 落图公式：图纸坐标 = view_position + 实体局部坐标 × (1/比例分母)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.generators.models import StepContext
from app.generators.sw_com import run_sw
from app.generators.view_extractor import bounding_box_of, parse_exported_dxf
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# ---- 布局常量（单位：mm；图纸坐标系 = 横向图幅，y 向上）----
# GB A 系列横向图幅（选型按 A3→A0 升序）
_SHEET_SIZES = {
    "A3": (420.0, 297.0), "A2": (594.0, 420.0),
    "A1": (841.0, 594.0), "A0": (1189.0, 841.0),
}
_BASE_SHEET = "A3"      # 比例决策基准图幅（保持既有比例行为不漂移）
_LAYOUT_MARGIN = 20.0   # 视图区边距
_LAYOUT_GAP_X = 40.0    # 视图水平间距（含标注空间）
_LAYOUT_GAP_Y = 20.0    # 视图垂直间距（含标注空间）
# BOM 估计（图幅选型用，与 step5 默认值对齐）
_BOM_BASE_Y = 50.0      # BOM 表底边（标题栏顶）图纸 y
_BOM_HEADER_EST = 20.0
_BOM_ROW_H_EST = 15.0
_FRAME_MARGIN = 10.0
# GB 标准缩小比例系列（1:N），自动比例向下取该系列
_GB_SCALES = (1, 2, 2.5, 5, 10, 20, 50, 100)

# 视图定义：投影平面 (u轴, v轴) 取三维坐标分量索引（X=0, Y=1, Z=2）
# front: 沿 -Y 看，u=X v=Z；top: 沿 -Z 看，u=X v=Y；left: 沿 +X 看，u=Y v=Z
_VIEW_DEFS = {
    "front": {"display_name": "主视图", "axes": (0, 2)},
    "top": {"display_name": "俯视图", "axes": (0, 1)},
    "left": {"display_name": "左视图", "axes": (1, 2)},
}


def _shift_entities(entities: List[Dict[str, Any]], dx: float, dy: float) -> None:
    """实体坐标原地平移 (-dx, -dy)；按键存在性处理 line/circle/arc"""
    for e in entities:
        for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
            if kx in e and ky in e:
                e[kx] = round(e[kx] - dx, 4)
                e[ky] = round(e[ky] - dy, 4)


def _normalize_view(view: Dict[str, Any], task_id: str = "") -> None:
    """
    实体坐标归一化：减去包围盒 min，原点对齐视图左下角。
    entities/hidden_lines/center_lines 同步平移；bounding_box min 归零、max 变为宽高。
    """
    entities = view.get("entities") or []
    if not entities:
        raise SWException(
            f"View '{view.get('name')}' has no entities to normalize",
            error_code=ErrorCode.GEN_STEP_FAILED,
            task_id=task_id,
            step=3,
        )
    bb = bounding_box_of(entities)
    dx, dy = bb["min_x"], bb["min_y"]
    if dx or dy:
        _shift_entities(entities, dx, dy)
        _shift_entities(view.get("hidden_lines") or [], dx, dy)
        _shift_entities(view.get("center_lines") or [], dx, dy)
    view["bounding_box"] = {
        "min_x": 0.0, "min_y": 0.0,
        "max_x": round(bb["max_x"] - dx, 4),
        "max_y": round(bb["max_y"] - dy, 4),
    }


def _pos(x: float, y: float, w: float, h: float) -> Dict[str, Any]:
    return {"x": round(x, 4), "y": round(y, 4),
            "width": round(w, 4), "height": round(h, 4)}


def _first_angle_positions(sizes: List[Tuple[str, float, float]],
                           den: float, sheet_w: float,
                           sheet_h: float) -> Optional[Dict[str, Any]]:
    """第一角布局：主视锚定左上；俯视在主视正下方（x 对齐）、左视在主视
    正右方（顶对齐）；其余视图主列下方平铺。y 向上。超界 → None（重试）"""
    scaled = {n: (w0 / den, h0 / den) for n, w0, h0 in sizes}
    order = [n for n, _, _ in sizes]
    anchor = "front" if "front" in scaled else order[0]
    aw, ah = scaled[anchor]
    if (aw > sheet_w - 2 * _LAYOUT_MARGIN
            or ah > sheet_h - 2 * _LAYOUT_MARGIN):
        return None
    ax, ay = _LAYOUT_MARGIN, sheet_h - _LAYOUT_MARGIN - ah
    positions: Dict[str, Any] = {anchor: _pos(ax, ay, aw, ah)}
    bottom = ay
    if anchor == "front":
        if "top" in scaled:
            tw, th = scaled["top"]
            ty = ay - _LAYOUT_GAP_Y - th
            if ty < _LAYOUT_MARGIN:
                return None
            positions["top"] = _pos(ax, ty, tw, th)
            bottom = ty
        if "left" in scaled:
            lw, lh = scaled["left"]
            if ax + aw + _LAYOUT_GAP_X + lw > sheet_w - _LAYOUT_MARGIN:
                return None
            positions["left"] = _pos(ax + aw + _LAYOUT_GAP_X,
                                     ay + ah - lh, lw, lh)
    x, y, row_h = _LAYOUT_MARGIN, bottom - _LAYOUT_GAP_Y, 0.0
    for name in order:
        if name in positions:
            continue
        w, h = scaled[name]
        if x + w > sheet_w - _LAYOUT_MARGIN and x > _LAYOUT_MARGIN:
            x, y, row_h = _LAYOUT_MARGIN, y - row_h - _LAYOUT_GAP_Y, 0.0
        if y - h < _LAYOUT_MARGIN:
            return None
        positions[name] = _pos(x, y - h, w, h)
        x += w + _LAYOUT_GAP_X
        row_h = max(row_h, h)
    return positions


def _compute_scale_denominator(views: List[Dict[str, Any]], task_id: str = "",
                               sheet: str = _BASE_SHEET) -> float:
    """
    自动比例：按“全部视图适配单图幅”计算——对 GB 标准比例系列从小到大
    模拟排布，第一个让所有视图（含间距/换行）落进指定图幅可用区的比例胜出；
    超出 1:100 仍装不下 → 截断并 warning。空视图/全零尺寸 → SWException。

    sheet：比例决策基准图幅（缺省 A3 保持既有行为；图幅升级后应按最终图幅
    重算，避免“A3 比例放到 A0 图幅”导致视图缩成小簇——2026-08-01 实测根因）
    """
    if not views:
        raise SWException(
            "No views for scale computation",
            error_code=ErrorCode.GEN_STEP_FAILED,
            task_id=task_id,
            step=3,
        )
    sizes = [
        (v["name"],
         v["bounding_box"]["max_x"] - v["bounding_box"]["min_x"],
         v["bounding_box"]["max_y"] - v["bounding_box"]["min_y"])
        for v in views
    ]
    if max(max(w, h) for _, w, h in sizes) <= 0:
        raise SWException(
            f"Degenerate view size for scale computation: {sizes}",
            error_code=ErrorCode.GEN_STEP_FAILED,
            task_id=task_id,
            step=3,
        )
    bw, bh = _SHEET_SIZES[sheet]
    for n in _GB_SCALES:
        if _first_angle_positions(sizes, float(n), bw, bh) is not None:
            return float(n)
    logger.warning(f"[Task:{task_id}] step3: views do not fit {sheet} "
                   f"even at 1:{_GB_SCALES[-1]}, clamped (layout may overflow)")
    return float(_GB_SCALES[-1])


def _select_sheet(sizes: List[Tuple[str, float, float]], den: float,
                  bom_rows: int = 0, task_id: str = "") -> str:
    """图幅选型：A3→A0 取首个同时装下 视图第一角布局 与 BOM 估计高度 的图幅"""
    bom_h = (_BOM_HEADER_EST + _BOM_ROW_H_EST * bom_rows) if bom_rows else 0.0
    for name, (w, h) in _SHEET_SIZES.items():
        if _first_angle_positions(sizes, den, w, h) is None:
            continue
        if bom_h and _BOM_BASE_Y + bom_h > h - _FRAME_MARGIN:
            continue
        return name
    logger.warning(f"[Task:{task_id}] step3: content exceeds A0, "
                   f"fallback to A0 (layout may overflow)")
    return "A0"


def _build_layout(views: List[Dict[str, Any]], task_id: str = "",
                  bom_rows: int = 0) -> Dict[str, Any]:
    """
    布局引擎：1) 实体归一化 2) 基准图幅（A3）模拟第一角布局取 GB 比例
    3) A3→A0 图幅选型（含 BOM 估计高度） 4) 输出第一角 view_positions
    """
    for vw in views:
        _normalize_view(vw, task_id)
    sizes = [
        (vw["name"],
         vw["bounding_box"]["max_x"] - vw["bounding_box"]["min_x"],
         vw["bounding_box"]["max_y"] - vw["bounding_box"]["min_y"])
        for vw in views
    ]
    den = _compute_scale_denominator(views, task_id)
    sheet = _select_sheet(sizes, den, bom_rows, task_id)
    if sheet != _BASE_SHEET:
        # 图幅被 BOM/布局升级到更大图幅：按最终图幅重算比例，
        # 否则“A3 适配的比例”放到 A0 上视图缩成一小簇（2026-08-01 老板验收根因）
        den2 = _compute_scale_denominator(views, task_id, sheet)
        if den2 != den:
            logger.info(f"[Task:{task_id}] step3: sheet upgraded {_BASE_SHEET}->"
                        f"{sheet}, scale recomputed 1:{den:g} -> 1:{den2:g}")
            den = den2
    scale_str = f"1:{den:g}"
    for vw in views:
        vw["scale"] = scale_str
    w, h = _SHEET_SIZES[sheet]
    positions = _first_angle_positions(sizes, den, w, h)
    if positions is None:
        # 比例截断 1:100 仍超图幅：告警后按超大图幅排布（允许溢出，不静默）
        logger.warning(f"[Task:{task_id}] step3: layout overflows {sheet} "
                       f"at 1:{den:g}, positions may exceed sheet")
        positions = _first_angle_positions(sizes, den, 1e6, 1e6)
    logger.info(f"[Task:{task_id}] step3 layout: sheet={sheet}, scale={scale_str}, "
                f"positions={ {k: (p['x'], p['y']) for k, p in positions.items()} }")
    return {"sheet_size": sheet, "orientation": "landscape",
            "view_positions": positions}


class ViewProjectExecutor:
    """
    Step 3 执行器: 视图投影

    输入: ctx.parameters["source_file"]、ctx.parameters["views"]（默认 front/top/left）、
          ctx.parameters["engine"]（仅支持 "sw_api"，默认）
    输出: {"views": [...], "layout": {...}}，完整 JSON 落盘 output/views.json
    SW 不可用 → 直接 SWException(GEN_SW_NOT_AVAILABLE)，无回退路径
    """

    @staticmethod
    def _estimate_bom_rows(previous_results: Dict[int, Any]) -> int:
        """估计聚合后 BOM 行数（同图号去重、排除抑制件），用于图幅选型；异常 → 0"""
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
        view_names = ctx.parameters.get("views")
        if view_names is None:
            view_names = ["front", "top", "left"]
        elif isinstance(view_names, list) and len(view_names) == 0:
            raise SWException(
                "views must not be empty",
                error_code=ErrorCode.GEN_INVALID_FILE,
                task_id=ctx.task_id,
                step=ctx.step,
            )
        for name in view_names:
            if name not in _VIEW_DEFS:
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

        # SW 原生导出 DXF（唯一路径）：COM 导出 → ezdxf 解析归一化
        from app.generators import sw_drawing  # 延迟导入，无 SW 环境可加载本模块
        try:
            logger.info(f"[Task:{ctx.task_id}] SW native DXF export {view_names} from {source_file}")
            sw_result = await run_sw(
                sw_drawing.export_dxf_sync, source_file, list(view_names),
                str(output_dir), bom_rows, ctx.task_id)
            for w in sw_result.get("warnings", []):
                logger.warning(f"[Task:{ctx.task_id}] step3: {w}")
        except SWException:
            raise
        except Exception as e:
            logger.exception(f"[Task:{ctx.task_id}] SW native DXF export failed: {e}")
            raise SWException(
                f"SW native DXF export failed: {e}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
                task_id=ctx.task_id,
                step=ctx.step,
                detail=str(e),
            )

        # 纯 Python 解析归一化（不碰 SW）：区域分配 + 线型分类 + 坐标换算
        parse_result = parse_exported_dxf(
            sw_result["dxf_path"], sw_result["positions"],
            sw_result["scale_den"], list(view_names), ctx.task_id)
        warnings = list(sw_result.get("warnings") or []) + \
            list(parse_result.get("warnings") or [])
        for w in parse_result.get("warnings", []):
            logger.warning(f"[Task:{ctx.task_id}] step3: {w}")
        result: Dict[str, Any] = {
            "views": parse_result["views"],
            "layout": {"sheet_size": sw_result["sheet"],
                       "orientation": "landscape",
                       # 契约：view_positions 直接用实际插入位置
                       "view_positions": sw_result["positions"]},
        }
        if warnings:
            result["warnings"] = warnings

        views_file = output_dir / "views.json"
        with open(views_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"[Task:{ctx.task_id}] SW API projection done -> {views_file}")
        return result
