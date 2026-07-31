"""
Step 3: 视图投影

技术路线（2026-07-30 老板批准 SW API 路线，docs/SW-API侦察报告-2026-07-30）：
- 主路径（sw_api）：SW 工程图视图作为投影引擎（sw_drawing + view_extractor），
  输出带真实几何参数（真圆/真线）的契约 entities
- 回退路径（stl）：STL + trimesh 正交投影（无 SW 环境备用，不删除）

引擎选择：ctx.parameters["engine"] = "sw_api" | "stl" | "auto"（默认 auto：
先试 SW API，SW 不可用（GEN_SW_NOT_AVAILABLE）时回退 STL；其他错误直接上抛）

契约不变：views.json 结构与 docs/plans/04 第二节一致
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from app.generators.models import StepContext
from app.generators.sw_com import run_sw
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

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


def _build_layout(views: List[Dict[str, Any]]) -> Dict[str, Any]:
    """简单平铺布局：A3 横向，视图从左到右排列，超宽换行"""
    positions: Dict[str, Any] = {}
    x = y = 20.0
    row_h = 0.0
    for vw in views:
        bb = vw["bounding_box"]
        w = round(bb["max_x"] - bb["min_x"], 4)
        h = round(bb["max_y"] - bb["min_y"], 4)
        if x + w > 400.0 and x > 20.0:
            x = 20.0
            y += row_h + 20.0
            row_h = 0.0
        positions[vw["name"]] = {"x": x, "y": y, "width": w, "height": h}
        x += w + 40.0
        row_h = max(row_h, h)
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
                result: Dict[str, Any] = {"views": views, "layout": _build_layout(views)}
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
        return {"views": views, "layout": _build_layout(views)}
