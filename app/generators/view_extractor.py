"""
SW 原生导出 DXF 的解析归一化层（纯 Python + ezdxf，不碰 SW COM，可独立单测）

方案（2026-08-01 老板批示，取代逐边提取）：SW 插入三视图后 SaveAs 导出 DXF，
本模块负责把 raw_export.dxf 归一化为契约 views.json 的视图字典。

输入：DXF 路径 + 各视图图幅区域（layout 实际插入位置，图纸 mm）+ 比例分母 den
输出：views 列表（name/display_name/projection/entities/hidden_lines/
      center_lines/section_hatch/bounding_box/scale），与旧契约逐字段一致

坐标换算：
- 读取 $INSUNITS/$MEASUREMENT 头变量，实体坐标先按 $INSUNITS → mm 换算系数
  归一化到 mm（2026-08-01 单位归一化修复；4=mm 时系数 1，行为不变）
- 视图局部实际尺寸 mm = (图纸坐标mm − 区域原点) × den
- 再按视图实体包围盒左下角归一（原点对齐，契约约定，与 _build_layout 归一一致）

线型/图层分类映射（2026-08-01 真机实测校正）：
- SW2025 DXF 导出忽略视图显示模式：HLR/HLV 导出内容一致，视图几何全部落在
  layer 0 / Continuous（隐藏线不经 DXF 线型输出）→ hidden_lines 如实为空 + warning
- 有效线型取实体 dxf.linetype，"BYLAYER"/"BYBLOCK" 时回溯图层线型
- 名称含 "HIDDEN"（虚线）  → hidden_lines
- 名称含 "CENTER"（点划线）→ center_lines
- 其余（CONTINUOUS 等实线）→ entities

实体类型支持：LINE / CIRCLE / ARC 直接映射；LWPOLYLINE / SPLINE / ELLIPSE
离散为折线（如实 note）；其余类型跳过并 note。禁止静默丢弃。
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# 区域分配容差（图纸 mm）：实体包围盒中心落在区域外扩该范围内仍归属该区域
_REGION_MARGIN_MM = 2.0
# 折线离散弦差（图纸 mm）
_FLATTEN_DIST_MM = 0.05

# $INSUNITS → 每单位 mm 数（DXF 头变量单位码表，常用项；缺省按 mm + warning）
_INSUNITS_TO_MM = {
    0: 1.0,        # Unitless（按 mm 处理，如实 warning）
    1: 25.4,       # 英寸
    2: 304.8,      # 英尺
    4: 1.0,        # 毫米
    5: 10.0,       # 厘米
    6: 1000.0,     # 米（SW COM 内部单位）
    7: 1.0e6,      # 千米
    8: 2.54e-5,    # 微英寸
    9: 0.0254,     # 密耳
    10: 914400.0,  # 码
    12: 1.0e-6,    # 纳米
    13: 1.0e-3,    # 微米
    14: 0.1,       # 分米
}


def _insunits_factor(insunits: Any) -> Tuple[float, Optional[str]]:
    """$INSUNITS → (到 mm 的换算系数, 未识别时 warning 文案)"""
    try:
        code = int(insunits)
    except (TypeError, ValueError):
        return 1.0, f"$INSUNITS={insunits!r} 无法解析，坐标按 mm 解析（如实上报）"
    if code in _INSUNITS_TO_MM:
        if code == 0:
            return 1.0, ("导出 DXF $INSUNITS=0（无单位声明），坐标按 mm 解析"
                         "（如实上报）")
        return _INSUNITS_TO_MM[code], None
    return 1.0, (f"导出 DXF $INSUNITS={code} 未识别，坐标按 mm 解析"
                 "（如实上报）")


def _scale_entities(entities: List[Dict[str, Any]], factor: float) -> None:
    """实体坐标原地 ×factor（单位归一化用）；按键存在性处理 line/circle/arc"""
    for e in entities:
        for k in ("x1", "y1", "x2", "y2", "cx", "cy", "r"):
            if k in e:
                e[k] = round(e[k] * factor, 6)

_VIEW_DISPLAY = {"front": "主视图", "top": "俯视图", "left": "左视图"}

# 图框/标题栏图层（2026-08-01 真机实测，SW2025 GB 模板导出）：
# 图幅边框/标题栏格/分区刻度线落在图层 "5"（视图几何在图层 0）。
# 这些图幅级线条中心可能落入视图区域，污染区域分配与包围盒
# （实测：front 宽被图框线拉成 3000mm（实际 1542.5）、top 高被边框线拉成
# 8210mm（实际 6512）），必须剔除，如实上报数量
_SHEET_FORMAT_LAYERS = frozenset({"5"})


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


def _classify_linetype(name: str) -> str:
    """线型名 → 分类：hidden / center / entity"""
    up = (name or "").upper()
    if "HIDDEN" in up:
        return "hidden"
    if "CENTER" in up:
        return "center"
    return "entity"


def _entity_bbox(ent: Dict[str, Any]) -> Tuple[float, float, float, float]:
    if ent["type"] == "line":
        return (min(ent["x1"], ent["x2"]), min(ent["y1"], ent["y2"]),
                max(ent["x1"], ent["x2"]), max(ent["y1"], ent["y2"]))
    return (ent["cx"] - ent["r"], ent["cy"] - ent["r"],
            ent["cx"] + ent["r"], ent["cy"] + ent["r"])


def _dxf_to_contract(entity: Any, flatten_dist: float
                     ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    单个 ezdxf 实体 → 契约实体列表（图纸坐标，未换算）

    Returns:
        (entities, note)  note 非空 = 离散化/跳过说明（如实上报）
    """
    dxftype = entity.dxftype()
    if dxftype == "LINE":
        d = entity.dxf
        return [{"type": "line", "x1": d.start.x, "y1": d.start.y,
                 "x2": d.end.x, "y2": d.end.y}], None
    if dxftype == "CIRCLE":
        d = entity.dxf
        return [{"type": "circle", "cx": d.center.x, "cy": d.center.y,
                 "r": d.radius}], None
    if dxftype == "ARC":
        d = entity.dxf
        return [{"type": "arc", "cx": d.center.x, "cy": d.center.y,
                 "r": d.radius, "start_angle": round(d.start_angle, 4),
                 "end_angle": round(d.end_angle, 4)}], None
    if dxftype in ("LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"):
        try:
            if dxftype == "LWPOLYLINE":
                # LWPolyline 无 flattening()，直接取顶点（SW 导出多为直段）
                pts = [(round(p[0], 6), round(p[1], 6))
                       for p in entity.get_points()]
            else:
                pts = [(round(p[0], 6), round(p[1], 6))
                       for p in entity.flattening(flatten_dist)]
        except Exception as e:
            return [], f"{dxftype}: flattening failed: {e}"
        if len(pts) < 2:
            return [], f"{dxftype}: flattening produced <2 points"
        entities = [
            {"type": "line", "x1": pts[i][0], "y1": pts[i][1],
             "x2": pts[i + 1][0], "y2": pts[i + 1][1]}
            for i in range(len(pts) - 1)
        ]
        return entities, f"{dxftype}: discretized to {len(entities)} segments"
    return [], f"{dxftype}: unsupported entity type, skipped"


def _assign_region(cx: float, cy: float,
                   regions: Dict[str, Dict[str, float]]) -> Optional[str]:
    """按包围盒中心分配视图区域（外扩 _REGION_MARGIN_MM）；无匹配 → None"""
    for name, r in regions.items():
        if (r["x"] - _REGION_MARGIN_MM <= cx <= r["x"] + r["width"] + _REGION_MARGIN_MM
                and r["y"] - _REGION_MARGIN_MM <= cy
                <= r["y"] + r["height"] + _REGION_MARGIN_MM):
            return name
    return None


def _to_local(ent: Dict[str, Any], ox: float, oy: float, den: float) -> Dict[str, Any]:
    """图纸坐标 → 视图局部实际尺寸 mm：减去区域原点后乘比例分母"""
    out = dict(ent)
    for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
        if kx in out:
            out[kx] = round((out[kx] - ox) * den, 4)
            out[ky] = round((out[ky] - oy) * den, 4)
    if "r" in out:
        out["r"] = round(out["r"] * den, 4)
    return out


def _shift_entities(entities: List[Dict[str, Any]], dx: float, dy: float) -> None:
    """实体坐标原地平移 (-dx, -dy)；按键存在性处理 line/circle/arc"""
    for e in entities:
        for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
            if kx in e and ky in e:
                e[kx] = round(e[kx] - dx, 4)
                e[ky] = round(e[ky] - dy, 4)


def parse_exported_dxf(dxf_path: str,
                       positions: Dict[str, Dict[str, float]],
                       scale_den: float,
                       view_names: Sequence[str],
                       task_id: str = "") -> Dict[str, Any]:
    """
    解析 SW 导出的图纸 DXF → 契约视图列表

    Args:
        dxf_path: raw_export.dxf 路径
        positions: layout 实际插入位置 {name: {x,y,width,height}}（图纸 mm）
        scale_den: 比例分母（1:N 的 N）
        view_names: 视图名列表（顺序即输出顺序）
        task_id: 日志/异常上下文

    Returns:
        {"views": [...], "warnings": [...]}

    Raises:
        SWException(GEN_STEP_FAILED): DXF 读取失败或任一视图区域无实体（禁假成功）
    """
    try:
        import ezdxf
    except ImportError as e:
        raise SWException(
            f"ezdxf unavailable: {e}",
            error_code=ErrorCode.GEN_STEP_FAILED, task_id=task_id, step=3)
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as e:
        raise SWException(
            f"Failed to read exported DXF {dxf_path}: {e}",
            error_code=ErrorCode.GEN_STEP_FAILED, task_id=task_id, step=3,
            detail=str(e))

    warnings: List[str] = []
    insunits = doc.header.get("$INSUNITS", 4)
    measurement = doc.header.get("$MEASUREMENT", 1)
    unit_factor, unit_warn = _insunits_factor(insunits)
    if unit_warn:
        warnings.append(unit_warn)
    elif unit_factor != 1.0:
        warnings.append(
            f"导出 DXF $INSUNITS={insunits}（非 4=mm，$MEASUREMENT={measurement}），"
            f"实体坐标 ×{unit_factor:g} 归一化到 mm 后解析")
    logger.info(f"[Task:{task_id}] step3 parse: $INSUNITS={insunits} "
                f"$MEASUREMENT={measurement} unit_factor={unit_factor:g}")

    # 图层线型表（BYLAYER 回溯用）
    layer_linetype: Dict[str, str] = {}
    for layer in doc.layers:
        layer_linetype[layer.dxf.name] = layer.dxf.linetype or "CONTINUOUS"

    regions = {n: positions[n] for n in view_names if n in positions}
    buckets: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        n: {"entity": [], "hidden": [], "center": []} for n in regions}
    unassigned = 0
    notes: List[str] = []
    skipped_sheet_format = 0

    for entity in doc.modelspace():
        if entity.dxf.layer in _SHEET_FORMAT_LAYERS:
            skipped_sheet_format += 1
            continue
        lt = entity.dxf.linetype or "BYLAYER"
        if lt.upper() in ("BYLAYER", "BYBLOCK"):
            lt = layer_linetype.get(entity.dxf.layer, "CONTINUOUS")
        cls = _classify_linetype(lt)
        # 折线离散弦差随单位换算（弦差定义在图纸 mm）
        ents, note = _dxf_to_contract(entity, _FLATTEN_DIST_MM / unit_factor)
        if note:
            notes.append(note)
        if unit_factor != 1.0:
            _scale_entities(ents, unit_factor)
        for ent in ents:
            bb = _entity_bbox(ent)
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            region = _assign_region(cx, cy, regions)
            if region is None:
                unassigned += 1
                continue
            r = regions[region]
            buckets[region][cls].append(_to_local(ent, r["x"], r["y"], scale_den))

    if skipped_sheet_format:
        warnings.append(
            f"{skipped_sheet_format} 个图框/标题栏线条（sheet format 图层，"
            f"非视图几何）已剔除（如实上报）")
    if unassigned:
        warnings.append(f"{unassigned} 个实体不在任何视图区域内，已跳过（如实上报）")
    if notes:
        # 同类 note 汇总，避免刷屏
        summary: Dict[str, int] = {}
        for n in notes:
            summary[n] = summary.get(n, 0) + 1
        warnings.extend(f"{k} ×{v}" if v > 1 else k for k, v in summary.items())

    views: List[Dict[str, Any]] = []
    for name in view_names:
        if name not in regions:
            raise SWException(
                f"View '{name}' has no insertion position",
                error_code=ErrorCode.GEN_STEP_FAILED, task_id=task_id, step=3)
        b = buckets[name]
        if not b["entity"]:
            raise SWException(
                f"View '{name}' region has no entities in exported DXF",
                error_code=ErrorCode.GEN_STEP_FAILED, task_id=task_id, step=3)
        # 归一化：原点对齐视图实体包围盒左下角（契约约定）
        bb = bounding_box_of(b["entity"])
        dx, dy = bb["min_x"], bb["min_y"]
        if dx or dy:
            _shift_entities(b["entity"], dx, dy)
            _shift_entities(b["hidden"], dx, dy)
            _shift_entities(b["center"], dx, dy)
        bbox = {"min_x": 0.0, "min_y": 0.0,
                "max_x": round(bb["max_x"] - dx, 4),
                "max_y": round(bb["max_y"] - dy, 4)}
        if not b["hidden"]:
            warnings.append(
                f"{name}: hidden_lines 为空（导出 DXF 无 HIDDEN 线型实体，如实上报）")
        views.append({
            "name": name,
            "display_name": _VIEW_DISPLAY.get(name, name),
            "projection": "first_angle",
            "entities": b["entity"],
            "hidden_lines": b["hidden"],
            "center_lines": b["center"],
            "section_hatch": None,  # M3 剖面线预留
            "bounding_box": bbox,
            "scale": f"1:{scale_den:g}",
        })
    return {"views": views, "warnings": warnings}
