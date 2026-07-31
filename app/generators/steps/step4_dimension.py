"""
Step 4: 尺寸标注

M2 范围（规则驱动，非 AI）：
- 从 Step3 views.json 各视图 entities 提取可标注特征：
  * 外廓长/宽/高：由视图 bounding_box 推导水平/垂直线性标注
  * 圆（circle）→ 直径标注（prefix ⌀）
  * 弧（arc）  → 半径标注（prefix R）
- 线性标注位置：延伸线取实体端点（外廓标注取 bounding_box 角点），
  标注线偏移视图 bounding_box 外 dimension_offset（默认 10mm），文字居中
- 公差：默认未注公差 grade（dimension_config 指定，默认 IT14），
  confidence=1.0（规则生成）。AI 公差推荐属 M4，本步不实现
- 重叠检测：同视图同朝向标注线区间相交 → overlaps[]（severity=warning），
  placement_score = 1 - 重叠数 / 标注总数

纯几何计算，不依赖 SW COM（无需 run_sw），可在无 SW 环境单测。
契约：docs/plans/04-二维生成可视化模块.md 第三节（dimensions.json）
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.generators.models import StepContext
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# dimension_config 默认值
_DEFAULT_TOLERANCE_GRADE = "IT14"
_DEFAULT_TOLERANCE_UPPER = 0.5
_DEFAULT_TOLERANCE_LOWER = -0.5
_DEFAULT_OFFSET = 10.0

# 标注线共线判定容差（mm）
_COLLINEAR_TOL = 1.0


def _parse_dimension_config(ctx: StepContext) -> Dict[str, Any]:
    """解析并校验 dimension_config 参数，非法值显式报错"""
    cfg = ctx.parameters.get("dimension_config") or {}
    if not isinstance(cfg, dict):
        raise SWException(
            f"dimension_config must be a dict, got {type(cfg).__name__}",
            error_code=ErrorCode.GEN_INVALID_FILE,
            task_id=ctx.task_id,
            step=ctx.step,
        )

    grade = cfg.get("default_tolerance_grade", _DEFAULT_TOLERANCE_GRADE)
    if not isinstance(grade, str) or not grade.strip():
        raise SWException(
            f"dimension_config.default_tolerance_grade must be non-empty str, got {grade!r}",
            error_code=ErrorCode.GEN_INVALID_FILE,
            task_id=ctx.task_id,
            step=ctx.step,
        )

    offset = cfg.get("dimension_offset", _DEFAULT_OFFSET)
    if not isinstance(offset, (int, float)) or isinstance(offset, bool) or offset <= 0:
        raise SWException(
            f"dimension_config.dimension_offset must be positive number, got {offset!r}",
            error_code=ErrorCode.GEN_INVALID_FILE,
            task_id=ctx.task_id,
            step=ctx.step,
        )

    upper = cfg.get("default_tolerance_upper", _DEFAULT_TOLERANCE_UPPER)
    lower = cfg.get("default_tolerance_lower", _DEFAULT_TOLERANCE_LOWER)
    for name, val in (("default_tolerance_upper", upper), ("default_tolerance_lower", lower)):
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise SWException(
                f"dimension_config.{name} must be a number, got {val!r}",
                error_code=ErrorCode.GEN_INVALID_FILE,
                task_id=ctx.task_id,
                step=ctx.step,
            )

    return {
        "grade": grade.strip(),
        "offset": float(offset),
        "upper": float(upper),
        "lower": float(lower),
    }


def _load_views(ctx: StepContext) -> Dict[str, Any]:
    """获取 Step3 产物：优先内存 previous_results[3]，回退 output/views.json 检查点"""
    upstream = ctx.previous_results.get(3)
    if isinstance(upstream, dict) and upstream.get("views"):
        return upstream

    views_file = ctx.get_output_path("views.json")
    if views_file.exists():
        try:
            data = json.loads(views_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise SWException(
                f"Failed to parse views checkpoint: {views_file}: {e}",
                error_code=ErrorCode.GEN_STEP_FAILED,
                task_id=ctx.task_id,
                step=ctx.step,
                detail=str(e),
            )
        if isinstance(data, dict) and data.get("views"):
            logger.info(f"[Task:{ctx.task_id}] step4 loaded views from checkpoint {views_file}")
            return data

    raise SWException(
        "Step4 requires Step3 views result (previous_results[3] or output/views.json)",
        error_code=ErrorCode.GEN_STEP_FAILED,
        task_id=ctx.task_id,
        step=ctx.step,
    )


def _entity_ref(view_name: str, index: int) -> str:
    return f"{view_name}_e{index}"


def _make_dimension(dim_id: str, dim_type: str, value: float,
                    position: Dict[str, float], associated: List[str],
                    cfg: Dict[str, Any], prefix: Optional[str] = None) -> Dict[str, Any]:
    dim: Dict[str, Any] = {
        "id": dim_id,
        "type": dim_type,
        "value": round(float(value), 4),
        "unit": "mm",
        "tolerance": {
            "upper": cfg["upper"],
            "lower": cfg["lower"],
            "grade": cfg["grade"],
        },
        "position": {k: round(float(v), 4) for k, v in position.items()},
        "associated_entities": associated,
        "is_automatic": True,
        "confidence": 1.0,
    }
    if prefix is not None:
        dim["prefix"] = prefix
    return dim


def extract_view_dimensions(view: Dict[str, Any], cfg: Dict[str, Any],
                            id_start: int) -> Tuple[List[Dict[str, Any]], int]:
    """
    从单个视图提取标注（纯几何，可单测）

    Returns: (dimensions, next_id_start)
    """
    name = view.get("name", "unknown")
    entities = view.get("entities")
    bb = view.get("bounding_box")
    if not entities:
        raise SWException(
            f"View '{name}' has empty entities, cannot dimension",
            error_code=ErrorCode.GEN_STEP_FAILED,
        )
    if not bb:
        raise SWException(
            f"View '{name}' missing bounding_box, cannot dimension",
            error_code=ErrorCode.GEN_STEP_FAILED,
        )

    offset = cfg["offset"]
    dims: List[Dict[str, Any]] = []
    seq = id_start

    def next_id() -> str:
        nonlocal seq
        d = f"dim_{seq:03d}"
        seq += 1
        return d

    # 1) 外廓线性标注：bounding_box 宽（下方水平标注）+ 高（左侧垂直标注）
    width = bb["max_x"] - bb["min_x"]
    height = bb["max_y"] - bb["min_y"]
    if width <= 0 or height <= 0:
        raise SWException(
            f"View '{name}' has degenerate bounding_box (w={width}, h={height})",
            error_code=ErrorCode.GEN_STEP_FAILED,
        )

    line_indices = [i for i, e in enumerate(entities) if e.get("type") == "line"]
    outline_refs = [_entity_ref(name, i) for i in line_indices] or [_entity_ref(name, 0)]

    # 宽度标注：延伸线取底边两个角点，标注线在 bbox 下方 offset
    dim_y = bb["min_y"] - offset
    dims.append(_make_dimension(
        next_id(), "linear", width,
        {"x1": bb["min_x"], "y1": dim_y, "x2": bb["max_x"], "y2": dim_y,
         "text_x": (bb["min_x"] + bb["max_x"]) / 2, "text_y": dim_y},
        outline_refs, cfg))

    # 高度标注：延伸线取左边两个角点，标注线在 bbox 左侧 offset
    dim_x = bb["min_x"] - offset
    dims.append(_make_dimension(
        next_id(), "linear", height,
        {"x1": dim_x, "y1": bb["min_y"], "x2": dim_x, "y2": bb["max_y"],
         "text_x": dim_x, "text_y": (bb["min_y"] + bb["max_y"]) / 2},
        outline_refs, cfg))

    # 2) 圆 → 直径标注；弧 → 半径标注
    for i, e in enumerate(entities):
        etype = e.get("type")
        if etype == "circle":
            cx, cy, r = e["cx"], e["cy"], e["r"]
            if r <= 0:
                raise SWException(
                    f"View '{name}' circle entity {i} has non-positive radius: {r}",
                    error_code=ErrorCode.GEN_STEP_FAILED,
                )
            dims.append(_make_dimension(
                next_id(), "diameter", 2 * r,
                {"x1": cx - r, "y1": cy, "x2": cx + r, "y2": cy,
                 "text_x": cx, "text_y": cy + r + offset},
                [_entity_ref(name, i)], cfg, prefix="⌀"))
        elif etype == "arc":
            cx, cy, r = e["cx"], e["cy"], e["r"]
            if r <= 0:
                raise SWException(
                    f"View '{name}' arc entity {i} has non-positive radius: {r}",
                    error_code=ErrorCode.GEN_STEP_FAILED,
                )
            dims.append(_make_dimension(
                next_id(), "radius", r,
                {"x1": cx, "y1": cy, "x2": cx + r, "y2": cy,
                 "text_x": cx + r / 2, "text_y": cy + offset},
                [_entity_ref(name, i)], cfg, prefix="R"))
        elif etype == "line":
            continue  # 外廓标注已覆盖；单段线特征识别属后续里程碑
        else:
            logger.warning(f"[step4] view '{name}' unknown entity type at {i}: {etype!r}, skipped")

    return dims, seq


def detect_overlaps(view: Dict[str, Any],
                    dims: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    同视图标注重叠检测（纯几何，可单测）

    规则：同朝向（水平/垂直）标注线近似共线且区间相交 → 重叠。
    水平标注用标注线 y 坐标 + x 区间；垂直标注用 x 坐标 + y 区间。
    """
    entries = []
    for d in dims:
        p = d["position"]
        if abs(p["y1"] - p["y2"]) < _COLLINEAR_TOL:  # 水平标注线
            lo, hi = sorted((p["x1"], p["x2"]))
            entries.append((d["id"], "h", p["y1"], lo, hi))
        elif abs(p["x1"] - p["x2"]) < _COLLINEAR_TOL:  # 垂直标注线
            lo, hi = sorted((p["y1"], p["y2"]))
            entries.append((d["id"], "v", p["x1"], lo, hi))
        else:  # 斜向（直径/半径）用线段包围盒参与检测
            lo, hi = sorted((p["x1"], p["x2"]))
            entries.append((d["id"], "h", (p["y1"] + p["y2"]) / 2, lo, hi))

    overlaps: List[Dict[str, Any]] = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            id_a, ori_a, coord_a, lo_a, hi_a = entries[i]
            id_b, ori_b, coord_b, lo_b, hi_b = entries[j]
            if ori_a != ori_b:
                continue
            if abs(coord_a - coord_b) >= _COLLINEAR_TOL:
                continue
            if min(hi_a, hi_b) - max(lo_a, lo_b) > 0:
                overlaps.append({
                    "dim_ids": [id_a, id_b],
                    "severity": "warning",
                })
    return overlaps


class DimensionExecutor:
    """
    Step 4 执行器: 尺寸标注

    输入: ctx.previous_results[3]（Step3 内存结果）或 output/views.json 检查点；
          ctx.parameters["dimension_config"]（可选：default_tolerance_grade /
          dimension_offset / default_tolerance_upper / default_tolerance_lower）
    输出: {"dimensions": [...], "placement_score": float, "overlaps": [...]}，
          落盘 output/dimensions.json
    异常: 缺 views 输入 / 空 entities → SWException（禁止静默返回空数据）
    """

    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        cfg = _parse_dimension_config(ctx)
        views_data = _load_views(ctx)
        views = views_data["views"]

        logger.info(f"[Task:{ctx.task_id}] Dimensioning {len(views)} views "
                    f"(grade={cfg['grade']}, offset={cfg['offset']})")

        all_dims: List[Dict[str, Any]] = []
        all_overlaps: List[Dict[str, Any]] = []
        seq = 1
        for view in views:
            view_dims, seq = extract_view_dimensions(view, cfg, seq)
            overlaps = detect_overlaps(view, view_dims)
            for ov in overlaps:
                logger.warning(f"[Task:{ctx.task_id}] dimension overlap in view "
                               f"'{view.get('name')}': {ov['dim_ids']}")
            all_dims.extend(view_dims)
            all_overlaps.extend(overlaps)

        if not all_dims:
            raise SWException(
                "No dimensions extracted from views",
                error_code=ErrorCode.GEN_STEP_FAILED,
                task_id=ctx.task_id,
                step=ctx.step,
            )

        placement_score = round(1.0 - len(all_overlaps) / len(all_dims), 4)
        result: Dict[str, Any] = {
            "dimensions": all_dims,
            "placement_score": placement_score,
            "overlaps": all_overlaps,
        }

        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        dims_file = output_dir / "dimensions.json"
        with open(dims_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"[Task:{ctx.task_id}] Dimensioning done: {len(all_dims)} dims, "
                    f"{len(all_overlaps)} overlaps, score={placement_score} -> {dims_file}")
        return result
