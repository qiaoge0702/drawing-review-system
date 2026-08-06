# -*- coding: utf-8 -*-
"""布局引擎（纯几何，无 COM 依赖）

方案B B-M1+ 重写：主视优先 + 辅助视图填充 + 重叠检测。

职责边界：
- 只算坐标，不碰 COM（插视图 / GetOutline 实测由 step3 的 sw_drawing 层执行）
- 输入为 view_strategy.to_layout_input() 风格的视图项（各视图已按自身比例缩放）
- 输出 positions + warnings + unplaced；取不到/放不下如实进 warnings，禁止编造

图幅序列为配置项（2026-08-06 老板确认：上限可扩，默认 A4→A0 横向升序）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---- 图幅序列（配置项；单位 mm，横向 landscape，(宽, 高)，升级升序）----
SHEET_SIZES: "Dict[str, Tuple[float, float]]" = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}

LAYOUT_MARGIN = 20.0          # 视图区边距
LAYOUT_GAP_DEFAULT = 25.0     # 视图默认间距
TITLE_BLOCK_FALLBACK = 60.0   # 标题栏保底高度（未实测时）

# 第一角投影（GB）：视图名 → 相对主视方位
_FIRST_ANGLE_SLOT = {
    "front": "main",
    "top": "below",
    "bottom": "above",
    "left": "right_of",
    "right": "left_of",
    "back": "far_right",
}
# 第三角投影：水平镜像
_THIRD_ANGLE_SLOT = {
    "front": "main",
    "top": "above",
    "bottom": "below",
    "left": "left_of",
    "right": "right_of",
    "back": "far_left",
}


@dataclass
class LayoutView:
    """布局输入视图项（尺寸已按自身比例缩放到图面 mm）"""
    id: str
    name: str
    view_type: str = "standard"       # standard/isometric/detail/section/auxiliary
    width: float = 100.0
    height: float = 100.0
    position_mode: str = "auto"       # auto/hint/absolute
    position_hint: str = ""
    position_params: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None


@dataclass
class LayoutResult:
    positions: Dict[str, Dict[str, float]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    unplaced: List[str] = field(default_factory=list)


def _rects_overlap(a: Dict[str, float], b: Dict[str, float]) -> bool:
    return (
        a["x"] < b["x"] + b["width"] and a["x"] + a["width"] > b["x"]
        and a["y"] < b["y"] + b["height"] and a["y"] + a["height"] > b["y"]
    )


class LayoutEngine:
    """主视优先 + 辅助填充布局引擎（纯几何）"""

    def __init__(
        self,
        sheet_w: float,
        sheet_h: float,
        spacing: float = LAYOUT_GAP_DEFAULT,
        margin: float = LAYOUT_MARGIN,
        title_block_bbox: Optional[Tuple[float, float, float, float]] = None,
        projection_type: str = "first_angle",
        layout_mode: str = "auto",
    ) -> None:
        self.sheet_w = sheet_w
        self.sheet_h = sheet_h
        self.spacing = spacing
        self.margin = margin
        self.title_block_bbox = title_block_bbox
        self.projection_type = projection_type
        self.layout_mode = layout_mode

    # ---- 公共入口 ----

    def layout(self, views: List[LayoutView]) -> Optional[Dict[str, Dict[str, float]]]:
        """严格布局：全部视图合法落位则返回 positions，否则 None（供比例/图幅重试循环）"""
        result = self.layout_ex(views)
        if result.unplaced or result.warnings:
            return None
        return result.positions

    def layout_ex(self, views: List[LayoutView]) -> LayoutResult:
        """完整布局：absolute 强制落位（违例仅告警），auto/hint 约束布局，放不下进 unplaced"""
        result = LayoutResult()
        by_id = {v.id: v for v in views}

        # 1. absolute 视图先行（人工干预优先，不挪位，违例仅告警）
        for v in views:
            if v.position_mode != "absolute":
                continue
            x = float(v.position_params.get("x", 0.0))
            y = float(v.position_params.get("y", 0.0))
            pos = self._make_pos(x, y, v.width, v.height)
            result.positions[v.id] = pos
            self._collect_violations(v.id, pos, result.positions, result.warnings)

        # manual 模式：其余视图不自动落位，输出待指定清单（前端拖拽 L3 后置）
        if self.layout_mode == "manual":
            for v in views:
                if v.id not in result.positions:
                    result.unplaced.append(v.id)
            if result.unplaced:
                result.warnings.append(
                    f"manual 布局模式：{len(result.unplaced)} 个视图待用户指定位置"
                )
            return result

        # 2. 主视落位（上方槽位有视图时主视下移，为上方视图留位）
        slot_map = (_THIRD_ANGLE_SLOT if self.projection_type == "third_angle"
                    else _FIRST_ANGLE_SLOT)
        main = self._find_main_view(views)
        if main is None:
            result.warnings.append("无视图可布局")
            return result
        if main.id not in result.positions:
            above_h = max(
                (v.height for v in views
                 if v.id not in result.positions
                 and v.view_type == "standard"
                 and slot_map.get(v.name) == "above"),
                default=None,
            )
            result.positions[main.id] = self._place_main(main, above_h)
        main_pos = result.positions[main.id]
        for v in views:
            if v.id in result.positions or v.view_type != "standard":
                continue
            pos = self._place_standard(v, slot_map, main_pos, result)
            if pos is None:
                result.unplaced.append(v.id)
                result.warnings.append(f"标准视图 {v.id} 放不下，未落位")
            else:
                result.positions[v.id] = pos

        # 4. 轴测图：右上空白区
        for v in views:
            if v.id in result.positions or v.view_type != "isometric":
                continue
            pos = self._place_free(v, result, prefer="right")
            if pos is None:
                result.unplaced.append(v.id)
                result.warnings.append(f"轴测图 {v.id} 放不下，未落位")
            else:
                result.positions[v.id] = pos

        # 5. 辅助视图（detail/section/auxiliary）：依附父视图填充
        for v in views:
            if v.id in result.positions:
                continue
            parent_pos = result.positions.get(v.parent_id or "") or main_pos
            pos = self._place_auxiliary(v, parent_pos, result)
            if pos is None:
                result.unplaced.append(v.id)
                result.warnings.append(
                    f"辅助视图 {v.id}（父视图 {v.parent_id or main.id}）放不下，未落位"
                )
            else:
                result.positions[v.id] = pos

        # 6. 全局校验（absolute 已告警过的不重复）
        for vid, pos in result.positions.items():
            if by_id[vid].position_mode != "absolute":
                self._collect_violations(vid, pos, result.positions, result.warnings,
                                         skip_id=vid)
        return result

    def suggest_sheet_size(
        self, views: List[LayoutView], base: str = "A4"
    ) -> Optional[str]:
        """从 base 起沿升级序列找第一个能放下的图幅；都放不下返回 None"""
        names = list(SHEET_SIZES.keys())
        start = names.index(base) if base in names else 0
        for name in names[start:]:
            w, h = SHEET_SIZES[name]
            engine = LayoutEngine(
                w, h, self.spacing, self.margin,
                self.title_block_bbox, self.projection_type, self.layout_mode,
            )
            if engine.layout(views) is not None:
                return name
        return None

    # ---- 内部：落位 ----

    def _place_main(
        self, v: LayoutView, above_h: Optional[float] = None
    ) -> Dict[str, float]:
        """主视：水平居中贴视图区顶部；上方槽位有视图时下移留位"""
        x = (self.sheet_w - v.width) / 2.0
        y = self.sheet_h - self.margin - v.height
        if above_h is not None:
            y -= above_h + self.spacing
        return self._make_pos(x, y, v.width, v.height)

    def _place_standard(
        self,
        v: LayoutView,
        slot_map: Dict[str, str],
        main_pos: Dict[str, float],
        result: LayoutResult,
    ) -> Optional[Dict[str, float]]:
        # hint 模式：优先用户方位提示
        if v.position_mode == "hint" and v.position_params.get("relation"):
            pos = self._place_by_relation(v, v.position_params, result)
            if pos is not None:
                return pos
            result.warnings.append(f"视图 {v.id} 的 hint 位置冲突，降级 auto 布局")
        slot = slot_map.get(v.name)
        if slot == "main":
            # front/left/right 中最宽者已作主视；其余主视候选作侧视摆主视右侧
            slot = "right_of"
        if slot:
            return self._place_in_slot(v, slot, main_pos, result)
        return self._place_free(v, result)

    def _place_in_slot(
        self,
        v: LayoutView,
        slot: str,
        main_pos: Dict[str, float],
        result: LayoutResult,
    ) -> Optional[Dict[str, float]]:
        gap = self.spacing
        if slot == "below":
            x, y = main_pos["x"], main_pos["y"] - gap - v.height
        elif slot == "above":
            x, y = main_pos["x"], main_pos["y"] + main_pos["height"] + gap
        elif slot == "right_of":
            x = main_pos["x"] + main_pos["width"] + gap
            y = main_pos["y"] + (main_pos["height"] - v.height) / 2.0
        elif slot == "left_of":
            x = main_pos["x"] - gap - v.width
            y = main_pos["y"] + (main_pos["height"] - v.height) / 2.0
        elif slot in ("far_right", "far_left"):
            # 后视：沿侧视链再外推一格
            side = self._rightmost(result.positions) if slot == "far_right" \
                else self._leftmost(result.positions)
            if side is None:
                return self._place_free(v, result)
            if slot == "far_right":
                x = side["x"] + side["width"] + gap
            else:
                x = side["x"] - gap - v.width
            y = main_pos["y"] + (main_pos["height"] - v.height) / 2.0
        else:
            return self._place_free(v, result)
        pos = self._make_pos(x, y, v.width, v.height)
        if self._fits(pos, result.positions):
            return pos
        # 槽位冲突：降级自由填充
        return self._place_free(v, result)

    def _place_by_relation(
        self,
        v: LayoutView,
        params: Dict[str, Any],
        result: LayoutResult,
    ) -> Optional[Dict[str, float]]:
        """hint 模式：{"relation": below/above/left_of/right_of, "ref": 视图id}"""
        ref = result.positions.get(str(params.get("ref", "")))
        if ref is None:
            return None
        relation = params["relation"]
        gap = self.spacing
        if relation == "below":
            x, y = ref["x"], ref["y"] - gap - v.height
        elif relation == "above":
            x, y = ref["x"], ref["y"] + ref["height"] + gap
        elif relation == "right_of":
            x = ref["x"] + ref["width"] + gap
            y = ref["y"] + (ref["height"] - v.height) / 2.0
        elif relation == "left_of":
            x = ref["x"] - gap - v.width
            y = ref["y"] + (ref["height"] - v.height) / 2.0
        else:
            return None
        pos = self._make_pos(x, y, v.width, v.height)
        return pos if self._fits(pos, result.positions) else None

    def _place_auxiliary(
        self,
        v: LayoutView,
        parent_pos: Dict[str, float],
        result: LayoutResult,
    ) -> Optional[Dict[str, float]]:
        """辅助视图：hint 优先 → 父视图四邻候选 → 全局扫描，按离父视图距离打分"""
        if v.position_mode == "hint" and v.position_params.get("relation"):
            pos = self._place_by_relation(v, v.position_params, result)
            if pos is not None:
                return pos
            result.warnings.append(f"辅助视图 {v.id} 的 hint 位置冲突，降级 auto 布局")
        candidates: List[Dict[str, float]] = []
        gap = self.spacing
        sides = [
            (parent_pos["x"] + parent_pos["width"] + gap,
             parent_pos["y"] + (parent_pos["height"] - v.height) / 2.0),
            (parent_pos["x"] - gap - v.width,
             parent_pos["y"] + (parent_pos["height"] - v.height) / 2.0),
            (parent_pos["x"], parent_pos["y"] + parent_pos["height"] + gap),
            (parent_pos["x"], parent_pos["y"] - gap - v.height),
        ]
        for x, y in sides:
            candidates.append(self._make_pos(x, y, v.width, v.height))
        candidates.extend(self._scan_free_positions(v, result))
        best: Optional[Dict[str, float]] = None
        best_d = float("inf")
        pcx = parent_pos["x"] + parent_pos["width"] / 2.0
        pcy = parent_pos["y"] + parent_pos["height"] / 2.0
        for pos in candidates:
            if not self._fits(pos, result.positions):
                continue
            d = ((pos["x"] + v.width / 2.0 - pcx) ** 2
                 + (pos["y"] + v.height / 2.0 - pcy) ** 2)
            if d < best_d:
                best, best_d = pos, d
        return best

    def _place_free(
        self,
        v: LayoutView,
        result: LayoutResult,
        prefer: str = "left",
    ) -> Optional[Dict[str, float]]:
        candidates = self._scan_free_positions(v, result)
        if not candidates:
            return None
        if prefer == "right":
            return max(candidates, key=lambda p: p["x"])
        return min(candidates, key=lambda p: p["x"])

    def _scan_free_positions(
        self, v: LayoutView, result: LayoutResult
    ) -> List[Dict[str, float]]:
        """按间距步长网格扫描图幅空白区，返回全部合法候选"""
        found: List[Dict[str, float]] = []
        step = max(self.spacing, 10.0)
        y = self.sheet_h - self.margin - v.height
        while y >= self.margin:
            x = self.margin
            while x + v.width <= self.sheet_w - self.margin:
                pos = self._make_pos(x, y, v.width, v.height)
                if self._fits(pos, result.positions):
                    found.append(pos)
                x += step
            y -= step
        return found

    # ---- 内部：校验 ----

    def _title_block_rect(self) -> Dict[str, float]:
        if self.title_block_bbox is not None:
            x, y, w, h = self.title_block_bbox
            return {"x": x, "y": y, "width": w, "height": h}
        return {"x": 0.0, "y": 0.0, "width": self.sheet_w,
                "height": TITLE_BLOCK_FALLBACK}

    def _fits(
        self, pos: Dict[str, float], placed: Dict[str, Dict[str, float]]
    ) -> bool:
        if pos["x"] < 0 or pos["y"] < 0:
            return False
        if pos["x"] + pos["width"] > self.sheet_w:
            return False
        if pos["y"] + pos["height"] > self.sheet_h:
            return False
        if _rects_overlap(pos, self._title_block_rect()):
            return False
        return not any(_rects_overlap(pos, p) for p in placed.values())

    def _collect_violations(
        self,
        vid: str,
        pos: Dict[str, float],
        placed: Dict[str, Dict[str, float]],
        warnings: List[str],
        skip_id: Optional[str] = None,
    ) -> None:
        if pos["x"] < 0 or pos["y"] < 0 \
                or pos["x"] + pos["width"] > self.sheet_w \
                or pos["y"] + pos["height"] > self.sheet_h:
            warnings.append(f"视图 {vid} 超出图幅边界")
        if _rects_overlap(pos, self._title_block_rect()):
            warnings.append(f"视图 {vid} 与标题栏禁放区重叠")
        for oid, other in placed.items():
            if oid != vid and oid != skip_id and _rects_overlap(pos, other):
                warnings.append(f"视图 {vid} 与 {oid} 重叠")

    # ---- 内部：工具 ----

    def _find_main_view(self, views: List[LayoutView]) -> Optional[LayoutView]:
        """主视：standard 中 front/left/right 最宽者（长梁类长视图作主视）"""
        standards = [v for v in views if v.view_type == "standard"]
        candidates = [v for v in standards if v.name in ("front", "left", "right")]
        pool = candidates or standards or views
        return max(pool, key=lambda v: v.width) if pool else None

    @staticmethod
    def _rightmost(placed: Dict[str, Dict[str, float]]) -> Optional[Dict[str, float]]:
        return max(placed.values(), key=lambda p: p["x"] + p["width"],
                   default=None)

    @staticmethod
    def _leftmost(placed: Dict[str, Dict[str, float]]) -> Optional[Dict[str, float]]:
        return min(placed.values(), key=lambda p: p["x"], default=None)

    @staticmethod
    def _make_pos(x: float, y: float, w: float, h: float) -> Dict[str, float]:
        return {"x": round(x, 4), "y": round(y, 4),
                "width": round(w, 4), "height": round(h, 4)}


def to_layout_views(layout_input: List[Dict[str, Any]]) -> List[LayoutView]:
    """view_strategy.to_layout_input() 产物 → LayoutView 列表适配器"""
    views: List[LayoutView] = []
    for item in layout_input:
        box = item.get("bounding_box", {})
        views.append(LayoutView(
            id=item["id"],
            name=item["name"],
            view_type=item.get("view_type", "standard"),
            width=box.get("max_x", 100.0) - box.get("min_x", 0.0),
            height=box.get("max_y", 100.0) - box.get("min_y", 0.0),
            position_mode=item.get("position_mode", "auto"),
            position_hint=item.get("position_hint", ""),
            position_params=item.get("position_params", {}),
            parent_id=item.get("parent_id"),
        ))
    return views
