"""
视图实体提取与契约映射（纯函数，不依赖 SW COM，可独立单测）

输入为 COM 边界返回的"鸭子类型"对象（edge.GetCurve / curve.Identity /
LineParams / CircleParams / GetCurveParams3 / Evaluate），
输出为契约 views.json 实体字典（line / circle / arc）。

关键坑（侦察报告 2026-07-30）：
- curve.Identity 是属性不是方法
- MultiplyTransform 在 pywin32 不可用 → 手动矩阵 apply_xform
- 矩阵 16 维，第 13 元素（索引 12）为缩放
- INTERSECTION(3004) 等样条边：GetCurveParams3 参数范围 + Evaluate 采样离散折线
- SW 模型坐标单位为米 → 契约输出毫米（×1000）
"""

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# SW 曲线类型码（swCurveTypes_e / curve.Identity 返回值）
CURVE_TYPE_NAMES = {
    3001: "LINE", 3002: "CIRCLE", 3003: "ELLIPSE", 3004: "INTERSECTION",
    3005: "BCURVE", 3006: "PCURVE", 3007: "SP_CURVE", 3008: "TRIM_CURVE",
}
LINE = 3001
CIRCLE = 3002

M_TO_MM = 1000.0
_FULL_CIRCLE_TOL_M = 1e-9  # 起终点重合判定（米）


def apply_xform(arr: Sequence[float], x: float, y: float, z: float) -> Tuple[float, float]:
    """
    SW MathTransform 手动应用（pywin32 无 MultiplyTransform，侦察 probe_step8 验证版）
    4x4 行主序: [r00 r01 r02 tx  r10 r11 r12 ty  r20 r21 r22 tz  0 0 0 scale]
    返回视图 2D 坐标（米）。
    """
    rx = arr[0] * x + arr[1] * y + arr[2] * z + arr[3]
    ry = arr[4] * x + arr[5] * y + arr[6] * z + arr[7]
    s = arr[12] if len(arr) > 12 else 1.0
    if s and s != 1.0:
        rx, ry = rx / s, ry / s
    return rx, ry


def _pt2d(arr: Sequence[float], p3: Sequence[float]) -> Tuple[float, float]:
    """3D 点 → 视图 2D（毫米）"""
    ux, uy = apply_xform(arr, float(p3[0]), float(p3[1]), float(p3[2]))
    return round(ux * M_TO_MM, 4), round(uy * M_TO_MM, 4)


def _curve_params3(edge: Any) -> Optional[Sequence[float]]:
    """GetCurveParams3: [sx,sy,sz, ex,ey,ez, uStart,uEnd, ...]，取不到返回 None"""
    try:
        cp = edge.GetCurveParams3
        if cp and len(cp) >= 8:
            return cp
    except Exception as e:
        logger.debug(f"GetCurveParams3 unavailable: {e}")
    return None


def _endpoints_from_curve(edge: Any, curve: Any) -> Optional[Tuple[Sequence[float], Sequence[float]]]:
    """边界的真实起终点（优先 GetCurveParams3，退化用 LineParams 前两段）"""
    cp = _curve_params3(edge)
    if cp is not None:
        return cp[0:3], cp[3:6]
    try:
        lp = curve.LineParams  # [root x,y,z, dir x,y,z]（probe_step8 用法）
        if lp and len(lp) >= 6:
            return lp[0:3], lp[3:6]
    except Exception:
        pass
    return None


def _sample_spline(edge: Any, curve: Any, arr: Sequence[float],
                   samples: int) -> Optional[List[Tuple[float, float]]]:
    """样条/交线边：按参数范围均匀采样 → 2D 折线点列"""
    cp = _curve_params3(edge)
    if cp is None:
        return None
    u0, u1 = float(cp[6]), float(cp[7])
    if not math.isfinite(u0) or not math.isfinite(u1) or u1 <= u0:
        return None
    pts = []
    try:
        for i in range(samples + 1):
            u = u0 + (u1 - u0) * i / samples
            p = curve.Evaluate(u)  # COM 边界；mock 以同名方法模拟
            pts.append(_pt2d(arr, p[0:3]))
    except Exception as e:
        logger.debug(f"Spline sampling failed: {e}")
        return None
    return pts


def edge_to_entities(edge: Any, arr: Sequence[float], scale_decimal: float = 1.0,
                     spline_samples: int = 50) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    单条视图边 → 契约实体列表。

    Returns:
        (entities, note)  note 非空表示该边未能提取（如实上报，不静默）
    """
    try:
        curve = edge.GetCurve
        cid = curve.Identity  # 属性，不是方法
    except Exception as e:
        return [], f"edge curve inaccessible: {e}"

    tname = CURVE_TYPE_NAMES.get(cid, f"TYPE_{cid}")

    if cid == LINE:
        ep = _endpoints_from_curve(edge, curve)
        if ep is None:
            return [], f"{tname}: no endpoints"
        (x1, y1), (x2, y2) = _pt2d(arr, ep[0]), _pt2d(arr, ep[1])
        return [{"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2}], None

    if cid == CIRCLE:
        try:
            cp = curve.CircleParams  # [cx,cy,cz, ax,ay,az, radius]
            center = _pt2d(arr, cp[0:3])
            radius = round(float(cp[6]) * M_TO_MM * scale_decimal, 4)
        except Exception as e:
            return [], f"CIRCLE params failed: {e}"
        ep = _endpoints_from_curve(edge, curve)
        full = True
        if ep is not None:
            d = math.dist([float(v) for v in ep[0]], [float(v) for v in ep[1]])
            full = d < _FULL_CIRCLE_TOL_M
        if full or ep is None:
            return [{"type": "circle", "cx": center[0], "cy": center[1], "r": radius}], None
        (sx, sy), (ex, ey) = _pt2d(arr, ep[0]), _pt2d(arr, ep[1])
        start_angle = round(math.degrees(math.atan2(sy - center[1], sx - center[0])), 4)
        end_angle = round(math.degrees(math.atan2(ey - center[1], ex - center[0])), 4)
        return [{"type": "arc", "cx": center[0], "cy": center[1], "r": radius,
                 "start_angle": start_angle, "end_angle": end_angle}], None

    # 样条/椭圆/交线等：采样离散为折线（line 序列）
    pts = _sample_spline(edge, curve, arr, spline_samples)
    if not pts or len(pts) < 2:
        return [], f"{tname}: sampling failed"
    entities = [
        {"type": "line", "x1": pts[i][0], "y1": pts[i][1],
         "x2": pts[i + 1][0], "y2": pts[i + 1][1]}
        for i in range(len(pts) - 1)
    ]
    return entities, f"{tname}: discretized to {len(entities)} segments"


def extract_view_entities(edges_per_comp: Sequence[Sequence[Any]], arr: Sequence[float],
                          scale_decimal: float = 1.0,
                          spline_samples: int = 50) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    多组件边集合 → 契约实体列表（装配体逐组件提取，实体扁平合并）

    Returns:
        (entities, notes)  notes 记录跳过/离散化信息（如实上报）
    """
    entities: List[Dict[str, Any]] = []
    notes: List[str] = []
    for ci, edges in enumerate(edges_per_comp):
        for edge in (edges or []):
            ents, note = edge_to_entities(edge, arr, scale_decimal, spline_samples)
            entities.extend(ents)
            if note:
                notes.append(f"comp{ci}: {note}")
    return entities, notes


def bounding_box_of(entities: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """由实体列表计算 2D 包围盒（支持 line/arc/circle）"""
    xs: List[float] = []
    ys: List[float] = []
    for e in entities:
        if e["type"] == "line":
            xs += [e["x1"], e["x2"]]
            ys += [e["y1"], e["y2"]]
        elif e["type"] in ("circle", "arc"):
            xs += [e["cx"] - e["r"], e["cx"] + e["r"]]
            ys += [e["cy"] - e["r"], e["cy"] + e["r"]]
    return {
        "min_x": round(min(xs), 4), "min_y": round(min(ys), 4),
        "max_x": round(max(xs), 4), "max_y": round(max(ys), 4),
    }
