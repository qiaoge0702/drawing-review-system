"""
视图实体提取与契约映射（纯函数，不依赖 SW COM，可独立单测）

输入为 COM 边界返回的"鸭子类型"对象（edge.GetCurve / curve.Identity /
LineParams / CircleParams / GetCurveParams3 / Evaluate），
输出为契约 views.json 实体字典（line / circle / arc）。

关键坑（侦察报告 2026-07-30 + 2026-07-31 真机根因修正）：
- curve.Identity 是属性不是方法
- MultiplyTransform 在 pywin32 不可用 → 手动矩阵 apply_xform
- MathTransform.ArrayData 官方布局（16 元）：[0-8] 旋转 3×3（行主序）、
  [9-11] 平移（视图在图纸上的放置位置，米）、[12] 视图比例（图纸:模型）。
  【2026-07-31 根因修正】曾按 4×4 行主序误读（tx=arr[3], ty=arr[7], 除以 arr[12]），
  在 1:1 零件上隐形；遇到模板 1:50 视图时 y≡x 且坐标放大 50 倍（LB26.11000 出现
  100000×100000mm 假 bbox）。契约实体 = 模型实际尺寸 mm：只取旋转分量，
  图纸放置平移弃用（Step3 统一归一化），视图比例弃用（比例由 Step3 布局引擎决策）
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
    SW MathTransform 手动应用（2026-07-31 真机根因修正版）
    ArrayData 官方布局: [0-8] 旋转 3×3 行主序, [9-11] 图纸放置平移(米), [12] 视图比例。
    契约实体 = 视图局部实际尺寸（米，调用方 ×1000）：只取旋转分量；
    平移（图纸放置位置）与视图比例均弃用——比例是 Step3 布局引擎的决策。
    """
    rx = arr[0] * x + arr[1] * y + arr[2] * z
    ry = arr[3] * x + arr[4] * y + arr[5] * z
    return rx, ry


def _pt2d(arr: Sequence[float], p3: Sequence[float]) -> Tuple[float, float]:
    """3D 点 → 视图 2D（毫米）"""
    ux, uy = apply_xform(arr, float(p3[0]), float(p3[1]), float(p3[2]))
    return round(ux * M_TO_MM, 4), round(uy * M_TO_MM, 4)


def _curve_params3(edge: Any) -> Optional[Sequence[float]]:
    """GetCurveParams3: [sx,sy,sz, ex,ey,ez, uStart,uEnd, ...]，取不到返回 None。
    【2026-07-31 真机实测】SW2025 工程图视图边（Edge/SilhouetteEdge）上该接口
    返回 CDispatch 或直接报错（本机恒不可用），调用链必须走 params2/evaluate 回退。"""
    try:
        cp = edge.GetCurveParams3
        if cp and not isinstance(cp, str) and len(cp) >= 8:
            return cp
    except Exception as e:
        logger.debug(f"GetCurveParams3 unavailable: {e}")
    return None


def _curve_params2(edge: Any) -> Optional[Sequence[float]]:
    """GetCurveParams2: 布局同 params3（真机实测 Edge(1) 边可用，SilhouetteEdge(4) 不可用）"""
    try:
        cp = edge.GetCurveParams2
        if cp and not isinstance(cp, str) and len(cp) >= 8:
            return cp
    except Exception as e:
        logger.debug(f"GetCurveParams2 unavailable: {e}")
    return None


def _endpoints_via_evaluate(curve: Any) -> Optional[Tuple[Sequence[float], Sequence[float]]]:
    """curve.GetEndParams() 参数范围 + curve.Evaluate(u) 求端点（真机实测 SilhouetteEdge 可用）"""
    try:
        ep = curve.GetEndParams()
        u0, u1 = float(ep[0]), float(ep[1])
        if (not (math.isfinite(u0) and math.isfinite(u1)) or u1 <= u0
                or u1 - u0 > 100.0):  # 无限线（真机 GetEndParams 返回 ±10000）→ 不可用
            return None
        p0 = curve.Evaluate(u0)
        p1 = curve.Evaluate(u1)
        return p0[0:3], p1[0:3]
    except Exception as e:
        logger.debug(f"GetEndParams/Evaluate endpoints unavailable: {e}")
        return None


def _edge_endpoints(edge: Any, curve: Any) -> Optional[Tuple[Sequence[float], Sequence[float]]]:
    """边真实起终点：params3 → params2 → GetEndParams+Evaluate → LineParams（root/dir 兜底）"""
    cp = _curve_params3(edge) or _curve_params2(edge)
    if cp is not None:
        return cp[0:3], cp[3:6]
    ep = _endpoints_via_evaluate(curve)
    if ep is not None:
        return ep
    try:
        lp = curve.LineParams  # [root x,y,z, dir x,y,z]（probe_step8 用法；兜底，非真实端点）
        if lp and len(lp) >= 6:
            return lp[0:3], lp[3:6]
    except Exception:
        pass
    return None


def _endpoints_from_curve(edge: Any, curve: Any) -> Optional[Tuple[Sequence[float], Sequence[float]]]:
    """边界的真实起终点（params3 → params2 → evaluate → LineParams 兜底）"""
    return _edge_endpoints(edge, curve)


def _sample_spline(edge: Any, curve: Any, arr: Sequence[float],
                   samples: int) -> Optional[List[Tuple[float, float]]]:
    """样条/交线边：按参数范围均匀采样 → 2D 折线点列（params3 → params2 → GetEndParams）"""
    cp = _curve_params3(edge) or _curve_params2(edge)
    if cp is not None:
        u0, u1 = float(cp[6]), float(cp[7])
    else:
        try:
            ep = curve.GetEndParams()
            u0, u1 = float(ep[0]), float(ep[1])
        except Exception:
            return None
        if u1 - u0 > 100.0:  # 无限线（真机 ±10000）→ 不可采样
            return None
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


def edge_param_key(edge: Any, digits: int = 6) -> Optional[Tuple[float, ...]]:
    """
    边身份哈希（差集法隐藏线匹配用；与实体参数化共用同一参数来源链）：
      - 曲线类型码 cid（3001 LINE / 3002 CIRCLE / ...）
      - 起终点（params3 → params2 → GetEndParams+Evaluate 链，米，round 到 digits 位）
      - 圆/弧（CIRCLE=3002）附加圆心 + 半径（避免同端点不同圆误判同边）
      - 线（LINE=3001）端点取不到时附加 LineParams root+dir（区分不同直线）
    【2026-07-31 真机实测】本机 SW2025 视图边 GetCurveParams3 恒不可用
    （返回 CDispatch），SilhouetteEdge 仅 LineParams + Evaluate 可用；
    同一曲线在线框/HLR 两模式下参数一致，可跨模式匹配。
    完全取不到参数 → None（调用方按不可匹配处理，禁止强行造 key）。
    """
    try:
        curve = edge.GetCurve
        cid = curve.Identity
    except Exception:
        return None

    parts: List[float] = [float(cid)]
    ep = _edge_endpoints(edge, curve)
    got_params = ep is not None
    if ep is not None:
        parts.extend(float(v) for v in ep[0])
        parts.extend(float(v) for v in ep[1])
    try:
        if cid == CIRCLE:
            cpr = curve.CircleParams  # [cx,cy,cz, ax,ay,az, radius]
            parts.extend(float(cpr[i]) for i in (0, 1, 2, 3, 4, 5, 6))
            got_params = True
        elif cid == LINE and ep is None:
            lp = curve.LineParams  # [root, dir]：无端点时的区分依据
            if lp and len(lp) >= 6:
                parts.extend(float(v) for v in lp[0:6])
                got_params = True
    except Exception:
        pass
    if not got_params:
        return None
    return tuple(round(v, digits) for v in parts)


def edge_to_entities(edge: Any, arr: Sequence[float],
                     spline_samples: int = 50) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    单条视图边 → 契约实体列表（坐标为实际尺寸 mm，不含视图比例）。

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
        # 诚实标注：端点来自 LineParams root/dir 兑底（非真实段端点）时如实上报。
        # 真机实测（2026-07-31）：SilhouetteEdge 在本机无端点参数可取
        # （params3/params2 不可用，GetEndParams 返回 ±10000 无限线），
        # 只能用 LineParams 近似，几何范围不准确。
        approx = (_curve_params3(edge) is None and _curve_params2(edge) is None
                  and _endpoints_via_evaluate(curve) is None)
        (x1, y1), (x2, y2) = _pt2d(arr, ep[0]), _pt2d(arr, ep[1])
        note = f"{tname}: endpoints approximated from LineParams(root/dir)" if approx else None
        return [{"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2}], note

    if cid == CIRCLE:
        try:
            cp = curve.CircleParams  # [cx,cy,cz, ax,ay,az, radius]
            center = _pt2d(arr, cp[0:3])
            # 半径 = 实际尺寸 mm（不乘视图比例，比例由 Step3 布局引擎决策）
            radius = round(float(cp[6]) * M_TO_MM, 4)
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
            ents, note = edge_to_entities(edge, arr, spline_samples)
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
