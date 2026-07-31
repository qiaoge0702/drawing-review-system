"""
Step 3: 视图投影

技术路线（2026-07-30 老板批准 SW API 路线，docs/SW-API侦察报告-2026-07-30）：
- 主路径（sw_api）：SW 工程图视图作为投影引擎（sw_drawing + view_extractor），
  输出带真实几何参数（真圆/真线）的契约 entities
- 回退路径（stl）：STL + trimesh 正交投影（无 SW 环境备用，不删除）

引擎选择：ctx.parameters["engine"] = "sw_api" | "stl" | "auto"（默认 auto：
先试 SW API，SW 不可用（GEN_SW_NOT_AVAILABLE）时回退 STL；其他错误直接上抛）

契约不变：views.json 结构与 docs/plans/04 第二节一致

坐标系约定（2026-07-31 老板确认，专业范式：模型空间 --scale/平移--> 图纸空间）：
- entities/hidden_lines/center_lines：视图局部坐标（实际尺寸 mm），
  原点 = 视图包围盒左下角（本模块 _build_layout 统一归一化，两条引擎路径同一契约）
- scale：GB 标准比例字符串（如 "1:50"），由布局引擎按图幅可用区域自动计算
- layout.view_positions：图纸坐标（A3 图幅 mm，已含比例），
  Step7 落图公式：图纸坐标 = view_position + 实体局部坐标 × (1/比例分母)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.generators.models import StepContext
from app.generators.sw_com import run_sw
from app.generators.view_extractor import bounding_box_of
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# ---- 布局常量（单位：mm；图纸坐标系 = A3 横向图幅）----
_SHEET_W = 420.0        # A3 横向宽
_SHEET_H = 297.0        # A3 横向高
_LAYOUT_MARGIN = 20.0   # 视图区起始边距
_LAYOUT_GAP_X = 40.0    # 视图水平间距
_LAYOUT_GAP_Y = 20.0    # 视图换行垂直间距
# GB 标准缩小比例系列（1:N），自动比例向下取该系列
_GB_SCALES = (1, 2, 2.5, 5, 10, 20, 50, 100)

# 视图定义：投影平面 (u轴, v轴) 取三维坐标分量索引（X=0, Y=1, Z=2）
# front: 沿 -Y 看，u=X v=Z；top: 沿 -Z 看，u=X v=Y；left: 沿 +X 看，u=Y v=Z
_VIEW_DEFS = {
    "front": {"display_name": "主视图", "axes": (0, 2)},
    "top": {"display_name": "俯视图", "axes": (0, 1)},
    "left": {"display_name": "左视图", "axes": (1, 2)},
}


def _export_stl_sync(source_file: str, stl_path: str) -> str:
    """【同步/COM线程】打开 SW 文档并另存为 STL 文件"""
    from app.parsers.sw_parser import SWParser  # 延迟导入，避免无 SW 环境时模块加载失败

    parser = SWParser()
    try:
        doc = parser.open_document(source_file)
        if doc is None:
            raise SWException(
                f"Failed to open document: {source_file}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )
        # SaveAs3(Name, Version=swSaveAsCurrentVersion, Options)
        doc.SaveAs3(str(stl_path), 0, 0)
        if not Path(stl_path).exists():
            raise SWException(
                f"STL export failed: {stl_path}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )
        return stl_path
    finally:
        try:
            parser.close_document(source_file)
        except Exception as e:
            logger.warning(f"Failed to close document: {e}")
        try:
            parser.quit()
        except Exception as e:
            logger.warning(f"Failed to quit SW parser: {e}")


def project_mesh(mesh: Any, view_name: str) -> Dict[str, Any]:
    """
    正交投影提取 2D 轮廓（纯几何，不依赖 SW，可单测）

    将 mesh 三角面片投影到视图平面，shapely unary_union 合并后取外轮廓，
    离散为 line 实体序列，并计算 bounding_box。
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    u, v = _VIEW_DEFS[view_name]["axes"]
    polys = []
    for tri in mesh.triangles:
        poly = Polygon([(float(p[u]), float(p[v])) for p in tri])
        if not poly.is_empty and poly.area > 1e-12:
            polys.append(poly.buffer(0) if not poly.is_valid else poly)
    if not polys:
        raise SWException(
            f"Empty projection for view: {view_name}",
            error_code=ErrorCode.GEN_STEP_FAILED,
        )

    merged = unary_union(polys)
    geoms = list(merged.geoms) if merged.geom_type in ("MultiPolygon", "GeometryCollection") else [merged]

    entities: List[Dict[str, Any]] = []
    for geom in geoms:
        if geom.geom_type != "Polygon" or geom.is_empty:
            continue
        coords = list(geom.exterior.coords)
        for i in range(len(coords) - 1):
            (x1, y1), (x2, y2) = coords[i], coords[i + 1]
            entities.append({
                "type": "line",
                "x1": round(x1, 4), "y1": round(y1, 4),
                "x2": round(x2, 4), "y2": round(y2, 4),
            })
    if not entities:
        raise SWException(
            f"No outline entities for view: {view_name}",
            error_code=ErrorCode.GEN_STEP_FAILED,
        )

    xs = [e["x1"] for e in entities] + [e["x2"] for e in entities]
    ys = [e["y1"] for e in entities] + [e["y2"] for e in entities]
    return {
        "name": view_name,
        "display_name": _VIEW_DEFS[view_name]["display_name"],
        "projection": "first_angle",
        "entities": entities,
        "hidden_lines": [],   # M2 占位（结构预留）
        "center_lines": [],   # M2 占位（结构预留）
        "section_hatch": None,  # M2 占位（契约键预留，剖面线 M3 实现）
        "bounding_box": {
            "min_x": round(min(xs), 4), "min_y": round(min(ys), 4),
            "max_x": round(max(xs), 4), "max_y": round(max(ys), 4),
        },
        "scale": "1:1",
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


def _tile_positions(sizes: List[Tuple[str, float, float]],
                    den: float) -> Dict[str, Any]:
    """按缩放后尺寸平铺排布（起始边距 20mm，超宽换行），返回图纸坐标 positions"""
    positions: Dict[str, Any] = {}
    x = y = _LAYOUT_MARGIN
    row_h = 0.0
    for name, w0, h0 in sizes:
        w = round(w0 / den, 4)
        h = round(h0 / den, 4)
        if x + w > _SHEET_W - _LAYOUT_MARGIN and x > _LAYOUT_MARGIN:
            x = _LAYOUT_MARGIN
            y += row_h + _LAYOUT_GAP_Y
            row_h = 0.0
        positions[name] = {
            "x": round(x, 4), "y": round(y, 4), "width": w, "height": h}
        x += w + _LAYOUT_GAP_X
        row_h = max(row_h, h)
    return positions


def _positions_fit(positions: Dict[str, Any]) -> bool:
    """全部视图（含宽高范围）都落在图幅可用区内"""
    eps = 1e-6
    return all(
        p["x"] >= -eps and p["y"] >= -eps
        and p["x"] + p["width"] <= _SHEET_W - _LAYOUT_MARGIN + eps
        and p["y"] + p["height"] <= _SHEET_H - _LAYOUT_MARGIN + eps
        for p in positions.values()
    )


def _compute_scale_denominator(views: List[Dict[str, Any]], task_id: str = "") -> float:
    """
    自动比例：按“全部视图适配单图幅”计算——对 GB 标准比例系列从小到大
    模拟排布，第一个让所有视图（含间距/换行）落进 A3 可用区的比例胜出；
    超出 1:100 仍装不下 → 截断并 warning。空视图/全零尺寸 → SWException。
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
    for n in _GB_SCALES:
        if _positions_fit(_tile_positions(sizes, float(n))):
            return float(n)
    logger.warning(f"[Task:{task_id}] step3: views do not fit A3 even at "
                   f"1:{_GB_SCALES[-1]}, clamped (layout may overflow)")
    return float(_GB_SCALES[-1])


def _build_layout(views: List[Dict[str, Any]], task_id: str = "") -> Dict[str, Any]:
    """
    布局引擎（比例决策点）：
    1) 各视图实体归一化（原点 = 视图左下角，实际尺寸 mm）
    2) 自动比例：模拟排布取第一个整图幅适配的 GB 标准比例
    3) view_positions 输出图纸坐标：按缩放后尺寸平铺（起始边距 20mm，超宽换行）
    """
    for vw in views:
        _normalize_view(vw, task_id)
    den = _compute_scale_denominator(views, task_id)
    scale_str = f"1:{den:g}"
    for vw in views:
        vw["scale"] = scale_str
    sizes = [
        (vw["name"],
         vw["bounding_box"]["max_x"] - vw["bounding_box"]["min_x"],
         vw["bounding_box"]["max_y"] - vw["bounding_box"]["min_y"])
        for vw in views
    ]
    positions = _tile_positions(sizes, den)
    logger.info(f"[Task:{task_id}] step3 layout: scale={scale_str}, "
                f"positions={ {k: (p['x'], p['y']) for k, p in positions.items()} }")
    return {"sheet_size": "A3", "orientation": "landscape", "view_positions": positions}


class ViewProjectExecutor:
    """
    Step 3 执行器: 视图投影

    输入: ctx.parameters["source_file"]、ctx.parameters["views"]（默认 front/top/left）、
          ctx.parameters["engine"]（默认 auto）
    输出: {"views": [...], "layout": {...}}，完整 JSON 落盘 output/views.json
    无 SW 环境 → auto 模式回退 STL 路径；sw_api 模式直接 SWException(GEN_SW_NOT_AVAILABLE)
    """

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

        engine = ctx.parameters.get("engine", "auto")
        if engine not in ("sw_api", "stl", "auto"):
            raise SWException(
                f"Unsupported engine: {engine}",
                error_code=ErrorCode.GEN_UNSUPPORTED_FEATURE,
                task_id=ctx.task_id,
                step=ctx.step,
            )

        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1) SW API 主路径
        if engine in ("sw_api", "auto"):
            from app.generators import sw_drawing  # 延迟导入，无 SW 环境可加载本模块
            try:
                logger.info(f"[Task:{ctx.task_id}] SW API projection {view_names} from {source_file}")
                sw_result = await run_sw(
                    sw_drawing.extract_views_sync, source_file, list(view_names))
                views = sw_result["views"]
                for w in sw_result.get("warnings", []):
                    logger.warning(f"[Task:{ctx.task_id}] step3: {w}")
                result: Dict[str, Any] = {"views": views,
                                          "layout": _build_layout(views, ctx.task_id)}
                if sw_result.get("warnings"):
                    result["warnings"] = sw_result["warnings"]
                views_file = output_dir / "views.json"
                with open(views_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                logger.info(f"[Task:{ctx.task_id}] SW API projection done -> {views_file}")
                return result
            except SWException as e:
                if engine == "sw_api" or e.error_code != ErrorCode.GEN_SW_NOT_AVAILABLE:
                    raise
                logger.warning(
                    f"[Task:{ctx.task_id}] SW API unavailable ({e.message}), "
                    "fallback to STL projection")
            except Exception as e:
                logger.exception(f"[Task:{ctx.task_id}] SW API projection failed: {e}")
                if engine == "sw_api":
                    raise SWException(
                        f"SW API projection failed: {e}",
                        error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
                        task_id=ctx.task_id,
                        step=ctx.step,
                        detail=str(e),
                    )
                logger.warning(f"[Task:{ctx.task_id}] fallback to STL projection")

        # 2) STL 回退路径
        result = await self._project_via_stl(ctx, source_file, view_names, output_dir)
        views_file = output_dir / "views.json"
        with open(views_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"[Task:{ctx.task_id}] View projection done: {len(result['views'])} views -> {views_file}")
        return result

    async def _project_via_stl(self, ctx: StepContext, source_file: str,
                               view_names: List[str], output_dir: Path) -> Dict[str, Any]:
        """STL/trimesh 回退路径（无 SW 环境备用）"""
        stl_path = output_dir / "model.stl"

        # 1) SW 导出 STL（COM 线程）
        try:
            await run_sw(_export_stl_sync, source_file, str(stl_path))
        except SWException:
            raise
        except Exception as e:
            logger.exception(f"[Task:{ctx.task_id}] STL export failed: {e}")
            raise SWException(
                f"STL export failed: {e}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
                task_id=ctx.task_id,
                step=ctx.step,
                detail=str(e),
            )

        # 2) trimesh 加载 + 三向正交投影
        import trimesh  # 延迟导入，保持无 trimesh 环境下模块可加载
        try:
            loaded = trimesh.load(str(stl_path))
            if isinstance(loaded, trimesh.Trimesh):
                mesh = loaded
            elif isinstance(loaded, trimesh.Scene):
                mesh = loaded.to_mesh()
            else:
                raise TypeError(f"Unsupported geometry type: {type(loaded).__name__}")
        except Exception as e:
            logger.exception(f"Failed to load STL geometry: {e}")
            raise SWException(
                f"Failed to load STL geometry: {e}",
                error_code=ErrorCode.GEN_STEP_FAILED,
                task_id=ctx.task_id,
                step=ctx.step,
                detail=str(e),
            )

        views = [project_mesh(mesh, name) for name in view_names]
        return {"views": views, "layout": _build_layout(views, ctx.task_id)}
