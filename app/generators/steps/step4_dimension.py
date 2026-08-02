"""
Step 4: 尺寸标注

M2 范围（规则驱动，非 AI）：
- 从 Step3 views.json 各视图 entities 提取可标注特征：
  * 外廓长/宽/高：由视图 bounding_box 推导水平/垂直线性标注
  * 圆（circle）→ 直径标注（prefix ⌀）
  * 弧（arc）  → 半径标注（prefix R）
- 线性标注位置：延伸线取实体端点（外廓标注取 bounding_box 角点），
  标注线偏移视图 bounding_box 外 dimension_offset（默认 10mm），文字居中
- 坐标系声明：dimensions[].position 为**视图局部坐标**（与 views.json entities
  同坐标系：原点 = 视图 bounding_box 左下角，实际尺寸 mm），落图变换同
  Step7 视图公式（图纸坐标 = view_position + position × scale_factor）；
  view_name 字段标识所属视图（Step7 优先用其定位，associated_entities 前缀
  解析为回退）
- 公差：默认未注公差 grade（dimension_config 指定，默认 IT14），
  confidence=1.0（规则生成）。AI 公差推荐属 M4，本步不实现
- 保守放置（2026-08-01 老板定调：AI 做能做的，做不到留给人补）：
  * 候选标注按尺寸值从大到小依次尝试放置（大尺寸优先占位）
  * 冲突检测（任一命中即放弃该标注，进待人工清单）：
      1. 与已放置标注重叠（标注线+文字包围盒，图纸坐标判定）
      2. 标注文字与任一视图实体包围盒重叠
      3. 超出所属视图标注区域（视图区外扩 _VIEW_ALLOWANCE mm）或图幅有效区
  * 已放置标注保证互不重叠、在所属视图标注区域内，position 仍为视图局部
    坐标，Step7 落图变换链不变
  * placement_score 保留计算（1 - 重叠数/标注数），仅作指标，不再作为
    强行放置依据
- 未放置标注 → result["pending_manual"] + output/pending_manual.txt
  （待人工标注清单，供设计员照单补标）

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

# 图幅尺寸（mm，横向）；与 step7_dxf_build._SHEET_SIZES 保持一致
_SHEET_SIZES = {
    "A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0),
    "A1": (841.0, 594.0), "A0": (1189.0, 841.0),
}
# 图幅有效区内缩边距（mm）
_SHEET_BORDER = 10.0
# 视图标注区域：视图实体包围盒外扩（图纸 mm），标注须整体落在该区域内
_VIEW_ALLOWANCE = 25.0
# 标注文字估算尺寸（图纸 mm；文字真实大小不随视图比例缩放）
_TEXT_HEIGHT = 3.5
_TEXT_CHAR_WIDTH = 2.5


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
                    cfg: Dict[str, Any], prefix: Optional[str] = None,
                    view_name: Optional[str] = None) -> Dict[str, Any]:
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
        # 视图局部坐标（原点 = 视图 bbox 左下角，实际尺寸 mm；见模块 docstring）
        "position": {k: round(float(v), 4) for k, v in position.items()},
        "associated_entities": associated,
        "is_automatic": True,
        "confidence": 1.0,
    }
    if view_name is not None:
        dim["view_name"] = view_name
    if prefix is not None:
        dim["prefix"] = prefix
    return dim


def extract_view_dimensions(view: Dict[str, Any], cfg: Dict[str, Any],
                            id_start: int) -> Tuple[List[Dict[str, Any]], int]:
    """
    从单个视图提取标注候选（纯几何，可单测）

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
        outline_refs, cfg, view_name=name))

    # 高度标注：延伸线取左边两个角点，标注线在 bbox 左侧 offset
    dim_x = bb["min_x"] - offset
    dims.append(_make_dimension(
        next_id(), "linear", height,
        {"x1": dim_x, "y1": bb["min_y"], "x2": dim_x, "y2": bb["max_y"],
         "text_x": dim_x, "text_y": (bb["min_y"] + bb["max_y"]) / 2},
        outline_refs, cfg, view_name=name))

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
                [_entity_ref(name, i)], cfg, prefix="⌀", view_name=name))
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
                [_entity_ref(name, i)], cfg, prefix="R", view_name=name))
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
    保守放置策略下对已放置标注运行，用于校验与契约兼容（正常应为空）。
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


# ---------------------------------------------------------------------------
# 保守放置（冲突检测）
# ---------------------------------------------------------------------------

def _parse_scale_factor(view: Dict[str, Any]) -> float:
    """视图 scale（GB 比例字符串 "1:N"）→ scale_factor = 1/N；缺省/非法 → 1.0"""
    scale = view.get("scale")
    if isinstance(scale, str) and ":" in scale:
        try:
            num, den = scale.split(":", 1)
            factor = float(num) / float(den)
            if factor > 0:
                return factor
        except (TypeError, ValueError):
            pass
    return 1.0


def _rects_overlap(a: Tuple[float, float, float, float],
                   b: Tuple[float, float, float, float],
                   eps: float = 1e-6) -> bool:
    """矩形严格相交判定（仅边界相接不算重叠）"""
    return (min(a[2], b[2]) - max(a[0], b[0]) > eps and
            min(a[3], b[3]) - max(a[1], b[1]) > eps)


def _text_extent(dim: Dict[str, Any]) -> Tuple[float, float]:
    """
    估算标注文字宽/高（图纸 mm）

    垂直标注（标注线竖直）文字竖排：包围盒宽高互换。
    """
    text = f"{dim.get('prefix') or ''}{dim['value']:g}"
    w, h = max(len(text), 1) * _TEXT_CHAR_WIDTH, _TEXT_HEIGHT
    p = dim.get("position") or {}
    if abs(p.get("x1", 0.0) - p.get("x2", 1.0)) < _COLLINEAR_TOL:
        return h, w
    return w, h


def _line_sheet_rect(dim: Dict[str, Any], view_pos: Tuple[float, float],
                     scale_factor: float,
                     margin: float = 0.5) -> Tuple[float, float, float, float]:
    """标注线段（不含文字）在图纸坐标系下的包围盒，外扩 margin 便于相交判定"""
    p = dim["position"]
    vx, vy = view_pos
    xs = [vx + p["x1"] * scale_factor, vx + p["x2"] * scale_factor]
    ys = [vy + p["y1"] * scale_factor, vy + p["y2"] * scale_factor]
    return (min(xs) - margin, min(ys) - margin,
            max(xs) + margin, max(ys) + margin)


def _line_orientation(dim: Dict[str, Any]) -> str:
    """标注线朝向：h 水平 / v 垂直 / o 斜向（径向）"""
    p = dim["position"]
    if abs(p["y1"] - p["y2"]) < _COLLINEAR_TOL:
        return "h"
    if abs(p["x1"] - p["x2"]) < _COLLINEAR_TOL:
        return "v"
    return "o"


def _dim_sheet_rect(dim: Dict[str, Any], view_pos: Tuple[float, float],
                    scale_factor: float) -> Tuple[float, float, float, float]:
    """
    标注在图纸坐标系下的包围盒 = 标注线段包围盒 ∪ 文字包围盒

    标注线坐标按视图比例缩放；文字为真实图纸尺寸（不缩放）。
    """
    p = dim["position"]
    vx, vy = view_pos
    pts_x = [vx + p["x1"] * scale_factor, vx + p["x2"] * scale_factor]
    pts_y = [vy + p["y1"] * scale_factor, vy + p["y2"] * scale_factor]
    tw, th = _text_extent(dim)
    tx = vx + p["text_x"] * scale_factor
    ty = vy + p["text_y"] * scale_factor
    pts_x += [tx - tw / 2, tx + tw / 2]
    pts_y += [ty - th / 2, ty + th / 2]
    return min(pts_x), min(pts_y), max(pts_x), max(pts_y)


def _text_sheet_rect(dim: Dict[str, Any], view_pos: Tuple[float, float],
                     scale_factor: float,
                     own_rect: Optional[Tuple[float, float, float, float]] = None
                     ) -> Tuple[float, float, float, float]:
    """
    标注文字在图纸坐标系下的包围盒

    文字位于标注线远离视图实体的一侧（ drafting 惯例）：给定 own_rect 时，
    将文字包围盒沿"视图实体包围盒中心 → 文字锚点"主导轴方向外移半个字高，
    避免小比例视图下文字包围盒跨界压到实体包围盒边。
    """
    p = dim["position"]
    vx, vy = view_pos
    tw, th = _text_extent(dim)
    tx = vx + p["text_x"] * scale_factor
    ty = vy + p["text_y"] * scale_factor
    if own_rect is not None:
        cx = (own_rect[0] + own_rect[2]) / 2
        cy = (own_rect[1] + own_rect[3]) / 2
        dx, dy = tx - cx, ty - cy
        if dx or dy:
            if abs(dx) >= abs(dy):
                tx += (1.0 if dx > 0 else -1.0) * (th / 2 + 0.5)
            else:
                ty += (1.0 if dy > 0 else -1.0) * (th / 2 + 0.5)
    return tx - tw / 2, ty - th / 2, tx + tw / 2, ty + th / 2


def _suggest_direction(dim: Dict[str, Any]) -> str:
    """待人工标注建议放置方向：水平/垂直/角度（直径、半径等径向标注）"""
    if dim["type"] in ("diameter", "radius"):
        return "角度"
    p = dim["position"]
    if abs(p["y1"] - p["y2"]) < _COLLINEAR_TOL:
        return "水平"
    if abs(p["x1"] - p["x2"]) < _COLLINEAR_TOL:
        return "垂直"
    return "角度"


def place_dimensions(candidates: List[Dict[str, Any]],
                     view_rects: Dict[str, Tuple[float, float, float, float]],
                     view_transforms: Dict[str, Tuple[Tuple[float, float], float]],
                     sheet_rect: Optional[Tuple[float, float, float, float]]
                     ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    保守放置：候选按尺寸值从大到小排序，逐一冲突检测，全部通过才放置。

    冲突规则（任一命中 → 放弃，进 pending）：
      1. 标注包围盒与已放置标注包围盒重叠
      2. 标注文字与任一视图实体包围盒重叠
      3. 标注包围盒超出所属视图标注区域（视图区外扩 _VIEW_ALLOWANCE）
         或超出图幅有效区（sheet_rect 为 None 时跳过图幅检查）

    view_rects: view_name → 图纸坐标实体包围盒
    view_transforms: view_name → ((vx, vy), scale_factor)
    Returns: (placed, pending)
    """
    ordered = sorted(candidates, key=lambda d: d["value"], reverse=True)
    placed: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    # 已放置标注的冲突判定信息：(文字包围盒, 标注线包围盒, 朝向)
    placed_rects: List[Tuple[Tuple[float, float, float, float],
                             Tuple[float, float, float, float], str]] = []

    for dim in ordered:
        name = dim.get("view_name")
        view_pos, scale = view_transforms.get(name, ((0.0, 0.0), 1.0))
        rect = _dim_sheet_rect(dim, view_pos, scale)
        text_rect = _text_sheet_rect(dim, view_pos, scale,
                                     view_rects.get(name))

        # 规则 3a：图幅有效区
        if sheet_rect is not None and not (
                sheet_rect[0] <= rect[0] and rect[2] <= sheet_rect[2] and
                sheet_rect[1] <= rect[1] and rect[3] <= sheet_rect[3]):
            pending.append(dim)
            continue

        # 规则 3b：所属视图标注区域
        own = view_rects.get(name)
        if own is not None:
            region = (own[0] - _VIEW_ALLOWANCE, own[1] - _VIEW_ALLOWANCE,
                      own[2] + _VIEW_ALLOWANCE, own[3] + _VIEW_ALLOWANCE)
            if not (region[0] <= rect[0] and rect[2] <= region[2] and
                    region[1] <= rect[1] and rect[3] <= region[3]):
                pending.append(dim)
                continue

        # 规则 2：文字与任一视图实体包围盒重叠
        if any(_rects_overlap(text_rect, vr) for vr in view_rects.values()):
            pending.append(dim)
            continue

        # 规则 1：与已放置标注重叠
        # 细化判定（避免角部十字交叉误杀）：
        #   a. 本方文字 vs 对方任何部分（标注线/文字）
        #   b. 对方文字 vs 本方标注线
        #   c. 同朝向标注线共线且区间相交（叠线）
        line_rect = _line_sheet_rect(dim, view_pos, scale)
        ori = _line_orientation(dim)
        conflict = False
        for p_text, p_line, p_ori in placed_rects:
            if _rects_overlap(text_rect, p_line) or _rects_overlap(text_rect, p_text):
                conflict = True
                break
            if _rects_overlap(p_text, line_rect):
                conflict = True
                break
            if ori != "o" and ori == p_ori:
                if ori == "h":
                    if (abs(line_rect[1] - p_line[1]) < _COLLINEAR_TOL and
                            min(line_rect[2], p_line[2]) - max(line_rect[0], p_line[0]) > 0):
                        conflict = True
                        break
                else:
                    if (abs(line_rect[0] - p_line[0]) < _COLLINEAR_TOL and
                            min(line_rect[3], p_line[3]) - max(line_rect[1], p_line[1]) > 0):
                        conflict = True
                        break
        if conflict:
            pending.append(dim)
            continue

        placed.append(dim)
        placed_rects.append((text_rect, line_rect, ori))

    return placed, pending


def _pending_entry(dim: Dict[str, Any]) -> Dict[str, Any]:
    """待人工标注清单条目（只加字段不改契约：独立数组，不进 dimensions[]）"""
    entry: Dict[str, Any] = {
        "id": dim["id"],
        "type": dim["type"],
        "value": dim["value"],
        "unit": dim["unit"],
        "tolerance": dim["tolerance"],
        "view_name": dim.get("view_name"),
        "suggested_direction": _suggest_direction(dim),
    }
    if dim.get("prefix") is not None:
        entry["prefix"] = dim["prefix"]
    return entry


def _render_pending_txt(pending: List[Dict[str, Any]]) -> str:
    """人类可读待人工标注清单（表格化，供设计员照单补标）"""
    lines = [
        "待人工标注清单（Step4 保守放置：以下尺寸因位置冲突未自动放置，请人工补标）",
        f"共 {len(pending)} 条",
        "",
        f"{'序号':<5}{'标注ID':<10}{'视图':<10}{'类型':<10}{'数值':<12}{'公差':<18}{'建议方向':<8}",
        "-" * 76,
    ]
    for i, e in enumerate(pending, 1):
        tol = e["tolerance"]
        tol_str = f"{tol['grade']} ({tol['upper']:+g}/{tol['lower']:+g})"
        value_str = f"{e.get('prefix') or ''}{e['value']:g} {e['unit']}"
        lines.append(
            f"{i:<5}{e['id']:<10}{(e.get('view_name') or '-'):<10}{e['type']:<10}"
            f"{value_str:<12}{tol_str:<18}{e['suggested_direction']:<8}")
    return "\n".join(lines) + "\n"


class DimensionExecutor:
    """
    Step 4 执行器: 尺寸标注（保守放置）

    输入: ctx.previous_results[3]（Step3 内存结果）或 output/views.json 检查点；
          ctx.parameters["dimension_config"]（可选：default_tolerance_grade /
          dimension_offset / default_tolerance_upper / default_tolerance_lower）
    输出: {"dimensions": [...已放置...], "placement_score": float,
           "overlaps": [...], "placed_count": int, "pending_count": int,
           "pending_manual": [...待人工...]}，
          落盘 output/dimensions.json + output/pending_manual.txt
    异常: 缺 views 输入 / 空 entities → SWException（禁止静默返回空数据）
    """

    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        cfg = _parse_dimension_config(ctx)
        views_data = _load_views(ctx)
        views = views_data["views"]
        layout = views_data.get("layout") or {}

        logger.info(f"[Task:{ctx.task_id}] Dimensioning {len(views)} views "
                    f"(grade={cfg['grade']}, offset={cfg['offset']})")

        # 1) 提取候选标注（视图局部坐标）
        candidates: List[Dict[str, Any]] = []
        seq = 1
        for view in views:
            view_dims, seq = extract_view_dimensions(view, cfg, seq)
            candidates.extend(view_dims)

        if not candidates:
            raise SWException(
                "No dimensions extracted from views",
                error_code=ErrorCode.GEN_STEP_FAILED,
                task_id=ctx.task_id,
                step=ctx.step,
            )

        # 2) 准备图纸坐标系参照：视图实体包围盒 + 变换 + 图幅有效区
        layout_positions = layout.get("view_positions") or {}
        view_rects: Dict[str, Tuple[float, float, float, float]] = {}
        view_transforms: Dict[str, Tuple[Tuple[float, float], float]] = {}
        for view in views:
            name = view.get("name", "unknown")
            scale = _parse_scale_factor(view)
            lp = layout_positions.get(name) or {}
            vx, vy = float(lp.get("x", 0.0)), float(lp.get("y", 0.0))
            view_transforms[name] = ((vx, vy), scale)
            if lp.get("width") is not None and lp.get("height") is not None:
                view_rects[name] = (vx, vy, vx + float(lp["width"]),
                                    vy + float(lp["height"]))
            else:
                bb = view.get("bounding_box") or {}
                view_rects[name] = (
                    vx + bb.get("min_x", 0.0) * scale,
                    vy + bb.get("min_y", 0.0) * scale,
                    vx + bb.get("max_x", 0.0) * scale,
                    vy + bb.get("max_y", 0.0) * scale,
                )

        sheet_rect: Optional[Tuple[float, float, float, float]] = None
        sheet_name = layout.get("sheet_size")
        if sheet_name in _SHEET_SIZES:
            sw, sh = _SHEET_SIZES[sheet_name]
            sheet_rect = (_SHEET_BORDER, _SHEET_BORDER,
                          sw - _SHEET_BORDER, sh - _SHEET_BORDER)

        # 3) 保守放置：大尺寸优先，冲突即放弃
        placed, pending = place_dimensions(candidates, view_rects,
                                           view_transforms, sheet_rect)

        # 4) 放置校验（契约字段保留）：已放置标注应无重叠
        all_overlaps: List[Dict[str, Any]] = []
        for view in views:
            name = view.get("name", "unknown")
            view_placed = [d for d in placed if d.get("view_name") == name]
            all_overlaps.extend(detect_overlaps(view, view_placed))
        for ov in all_overlaps:
            logger.warning(f"[Task:{ctx.task_id}] dimension overlap after "
                           f"conservative placement: {ov['dim_ids']}")

        # clamp 到 [0,1]；评分保留计算，仅作指标，不影响放置
        placement_score = max(0.0, round(1.0 - len(all_overlaps) / len(placed), 4)) \
            if placed else 0.0

        pending_manual = [_pending_entry(d) for d in pending]
        result: Dict[str, Any] = {
            "dimensions": placed,
            "placement_score": placement_score,
            "overlaps": all_overlaps,
            "placed_count": len(placed),
            "pending_count": len(pending),
            "pending_manual": pending_manual,
        }

        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        dims_file = output_dir / "dimensions.json"
        with open(dims_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        txt_file = output_dir / "pending_manual.txt"
        txt_file.write_text(_render_pending_txt(pending_manual), encoding="utf-8")

        logger.info(f"[Task:{ctx.task_id}] Dimensioning done: placed={len(placed)}, "
                    f"pending={len(pending)}")
        logger.info(f"[Task:{ctx.task_id}] overlaps={len(all_overlaps)}, "
                    f"score={placement_score} -> {dims_file}; "
                    f"pending list -> {txt_file}")
        return result
