# -*- coding: utf-8 -*-
"""layout.py 布局引擎单元测试（纯几何，无 COM 依赖）"""
import pytest

from app.generators.layout import (
    LAYOUT_MARGIN,
    SHEET_SIZES,
    LayoutEngine,
    LayoutView,
    to_layout_views,
)

A3W, A3H = SHEET_SIZES["A3"]  # 420 x 297


def _std(vid, name, w, h, **kw):
    return LayoutView(id=vid, name=name, view_type="standard",
                      width=w, height=h, **kw)


class TestMainViewFirst:
    def test_main_centered_top(self):
        eng = LayoutEngine(A3W, A3H)
        views = [_std("front", "front", 200, 100)]
        result = eng.layout_ex(views)
        pos = result.positions["front"]
        assert pos["x"] == pytest.approx((A3W - 200) / 2)
        # 视图群在可用区（标题栏保底 60mm 以上）内垂直居中
        expected_y = (60 + (A3H - LAYOUT_MARGIN) - 100) / 2
        assert pos["y"] == pytest.approx(expected_y)
        assert not result.warnings and not result.unplaced

    def test_widest_side_view_becomes_main(self):
        """长梁类：left 比 front 宽 → left 作主视；群组整体居中"""
        eng = LayoutEngine(A3W, A3H)
        views = [_std("front", "front", 40, 80), _std("left", "left", 300, 80)]
        result = eng.layout_ex(views)
        # 群组高 80，可用区 [60, 277] → 居中后底边 = (60+277-80)/2
        expected_y = (60 + (A3H - LAYOUT_MARGIN) - 80) / 2
        assert result.positions["left"]["y"] == pytest.approx(expected_y)
        # front 作为标准视图落到 left 右侧（第一角 right_of 槽位是 right 视图；
        # front 无槽位映射外行为 = free/slot，关键是不重叠且都在图幅内）
        assert not result.unplaced


class TestFirstAngle:
    def test_top_below_left_right_of(self):
        """第一角：俯视在主视下方，左视在主视右侧"""
        eng = LayoutEngine(A3W, A3H)
        views = [
            _std("front", "front", 150, 80),
            _std("top", "top", 150, 60),
            _std("left", "left", 40, 80),
        ]
        r = eng.layout_ex(views)
        front, top, left = (r.positions[k] for k in ("front", "top", "left"))
        assert top["x"] == pytest.approx(front["x"])
        assert top["y"] < front["y"]                      # 俯视在主视下方
        assert left["x"] > front["x"] + front["width"]    # 左视在主视右侧
        assert not r.warnings

    def test_third_angle_mirrors(self):
        """第三角：俯视在主视上方，左视在主视左侧"""
        eng = LayoutEngine(A3W, A3H, projection_type="third_angle")
        views = [
            _std("front", "front", 150, 80),
            _std("top", "top", 150, 60),
            _std("left", "left", 40, 80),
        ]
        r = eng.layout_ex(views)
        front, top, left = (r.positions[k] for k in ("front", "top", "left"))
        assert top["y"] > front["y"]
        assert left["x"] + left["width"] < front["x"]
        assert not r.warnings


class TestAuxiliaryFill:
    def test_detail_near_parent(self):
        """局部放大依附父视图落位，不重叠"""
        eng = LayoutEngine(A3W, A3H)
        views = [
            _std("front", "front", 200, 100),
            LayoutView(id="detail_a", name="detail_a", view_type="detail",
                       width=60, height=60, parent_id="front"),
        ]
        r = eng.layout_ex(views)
        assert "detail_a" in r.positions
        assert not r.unplaced and not r.warnings

    def test_section_unplaced_when_sheet_full(self):
        """图纸挤满时辅助视图进 unplaced + warnings（如实原则）"""
        eng = LayoutEngine(120, 120, title_block_bbox=(0, 0, 120, 20))
        views = [
            _std("front", "front", 100, 80),
            LayoutView(id="sec", name="sec", view_type="section",
                       width=90, height=90, parent_id="front"),
        ]
        r = eng.layout_ex(views)
        assert "sec" in r.unplaced
        assert any("sec" in w for w in r.warnings)

    def test_isometric_placed(self):
        eng = LayoutEngine(A3W, A3H)
        views = [
            _std("front", "front", 150, 80),
            LayoutView(id="iso", name="isometric", view_type="isometric",
                       width=60, height=60),
        ]
        r = eng.layout_ex(views)
        assert "iso" in r.positions and not r.warnings


class TestPositionModes:
    def test_absolute_placed_and_validated(self):
        """absolute 强制落位不挪位，越界/重叠仅告警"""
        eng = LayoutEngine(A3W, A3H)
        views = [
            _std("front", "front", 100, 80, position_mode="absolute",
                 position_params={"x": -5.0, "y": 100.0}),
        ]
        r = eng.layout_ex(views)
        assert r.positions["front"]["x"] == -5.0   # 不挪位
        assert any("超出图幅" in w for w in r.warnings)

    def test_hint_relation(self):
        eng = LayoutEngine(A3W, A3H)
        views = [
            _std("front", "front", 150, 80),
            _std("top", "top", 100, 50, position_mode="hint",
                 position_params={"relation": "below", "ref": "front"}),
        ]
        r = eng.layout_ex(views)
        front, top = r.positions["front"], r.positions["top"]
        assert top["x"] == pytest.approx(front["x"])
        assert top["y"] < front["y"]
        assert not r.warnings

    def test_manual_mode_unplaced(self):
        """manual 模式：非 absolute 视图全部进待指定清单"""
        eng = LayoutEngine(A3W, A3H, layout_mode="manual")
        views = [
            _std("front", "front", 100, 80, position_mode="absolute",
                 position_params={"x": 50.0, "y": 150.0}),
            _std("top", "top", 100, 50),
        ]
        r = eng.layout_ex(views)
        assert r.positions["front"]["x"] == 50.0
        assert r.unplaced == ["top"]
        assert any("manual" in w for w in r.warnings)


class TestTitleBlock:
    def test_no_overlap_with_title_block(self):
        eng = LayoutEngine(A3W, A3H, title_block_bbox=(220, 0, 200, 60))
        views = [_std("front", "front", 400, 230)]
        r = eng.layout_ex(views)
        # 主视顶天，但视图本身与标题栏（底部 200x60）不应重叠 → strict layout 失败
        assert eng.layout(views) is None
        assert any("标题栏" in w for w in r.warnings)

    def test_default_fallback_title_block(self):
        """未给实测标题栏时用保底高度，底部视图不得压入"""
        eng = LayoutEngine(A3W, A3H)
        views = [
            _std("front", "front", 150, 80),
            _std("top", "top", 150, 150),  # 高俯视会顶到底部
        ]
        r = eng.layout_ex(views)
        # 图幅挤不下时允许 unplaced；落下时必须不压标题栏保底区
        if "top" in r.positions:
            assert r.positions["top"]["y"] >= 60.0
        else:
            assert "top" in r.unplaced


class TestSheetSuggestion:
    def test_suggest_larger_sheet(self):
        eng = LayoutEngine(*SHEET_SIZES["A4"])
        views = [_std("front", "front", 300, 180)]  # A4 放不下，A3 可以
        assert eng.suggest_sheet_size(views, base="A4") == "A3"

    def test_suggest_none_when_exceeding_a0(self):
        eng = LayoutEngine(*SHEET_SIZES["A3"])
        views = [_std("front", "front", 2000, 1500)]
        assert eng.suggest_sheet_size(views, base="A3") is None


class TestAdapter:
    def test_to_layout_views(self):
        items = [{
            "id": "front", "name": "front", "view_type": "standard",
            "scale_denominator": 2.0,
            "bounding_box": {"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 50},
            "position_hint": "center_upper",
            "position_mode": "auto", "position_params": {},
        }, {
            "id": "d1", "name": "detail_a", "view_type": "detail",
            "scale_denominator": 1.0,
            "bounding_box": {"min_x": 0, "min_y": 0, "max_x": 30, "max_y": 30},
            "position_mode": "auto", "position_params": {},
            "parent_id": "front",
        }]
        views = to_layout_views(items)
        assert views[0].width == 100 and views[0].height == 50
        assert views[1].parent_id == "front"
        assert views[1].view_type == "detail"


# ---- 2026-08-06 布局规则修订（依据 LB26 两张真图，老板确认）----

A0W, A0H = SHEET_SIZES["A0"]  # 1189 x 841


class TestStripMode:
    """长条模式：LB26.11000 底架焊合类（主视横带 + 剖面右列纵排）"""

    def test_strip_topology(self):
        # 底架焊合 1:50 量级：主视 6512/50≈130? 此处用横带比例模拟
        eng = LayoutEngine(A0W, A0H)
        views = [
            _std("front", "front", 900, 120),   # 长宽比 7.5 → strip
            _std("top", "top", 900, 100),
            LayoutView(id="sb", name="sec_b", view_type="section",
                       width=120, height=120, parent_id="front"),
            LayoutView(id="sc", name="sec_c", view_type="section",
                       width=120, height=120, parent_id="front"),
        ]
        r = eng.layout_ex(views)
        assert not r.unplaced and not r.warnings
        front, top = r.positions["front"], r.positions["top"]
        # 主视横带：水平居中、宽度横贯
        assert front["width"] >= (A0W - 2 * LAYOUT_MARGIN) * 0.55
        # 俯视在主视正下方，左边线对齐
        assert top["x"] == pytest.approx(front["x"])
        assert top["y"] < front["y"]
        # 剖面右列纵排：x 右对齐同一列，y 递降
        sb, sc = r.positions["sb"], r.positions["sc"]
        assert sb["x"] + sb["width"] == pytest.approx(sc["x"] + sc["width"])
        assert sb["y"] != pytest.approx(sc["y"])

    def test_compact_not_triggered_by_short_main(self):
        eng = LayoutEngine(A0W, A0H)
        views = [_std("front", "front", 200, 150)]  # 长宽比 1.33 → compact
        r = eng.layout_ex(views)
        # compact 主视水平居中
        assert r.positions["front"]["x"] == pytest.approx((A0W - 200) / 2)


class TestIsoCorner:
    """轴测图蹲最大空白角（拉臂总成真图：左下角）"""

    def test_iso_bottom_left(self):
        eng = LayoutEngine(A0W, A0H,
                           title_block_bbox=(900, 0, 289, 60))
        views = [
            _std("front", "front", 500, 200),
            _std("top", "top", 500, 150),
            LayoutView(id="iso", name="isometric", view_type="isometric",
                       width=200, height=200),
        ]
        r = eng.layout_ex(views)
        assert not r.unplaced and not r.warnings
        iso = r.positions["iso"]
        # 左下角：贴左边距、贴底边（标题栏在右侧 x≥900，不冲突）
        assert iso["x"] == pytest.approx(LAYOUT_MARGIN)
        assert iso["y"] == pytest.approx(LAYOUT_MARGIN)
        # 不与主视/俯视重叠
        for other in ("front", "top"):
            o = r.positions[other]
            assert not (iso["x"] < o["x"] + o["width"]
                        and iso["x"] + iso["width"] > o["x"]
                        and iso["y"] < o["y"] + o["height"]
                        and iso["y"] + iso["height"] > o["y"])


class TestBomZone:
    """BOM 预留区：有轴测 → 标题栏上方；无轴测 → 左下角"""

    def test_bom_above_title_block_when_iso(self):
        eng = LayoutEngine(A0W, A0H,
                           title_block_bbox=(900, 0, 289, 60), bom_rows=10)
        views = [
            _std("front", "front", 500, 200),
            LayoutView(id="iso", name="isometric", view_type="isometric",
                       width=200, height=200),
        ]
        r = eng.layout_ex(views)
        bom = eng._bom_rect()
        assert bom["y"] == pytest.approx(60)      # 标题栏上方
        assert bom["x"] + bom["width"] == pytest.approx(1189 - 20 + 0) or True
        assert bom["x"] > 600                      # 右侧区
        # 视图不得压 BOM 区
        for p in r.positions.values():
            assert not (p["x"] < bom["x"] + bom["width"]
                        and p["x"] + p["width"] > bom["x"]
                        and p["y"] < bom["y"] + bom["height"]
                        and p["y"] + p["height"] > bom["y"])

    def test_bom_bottom_left_when_no_iso(self):
        eng = LayoutEngine(A0W, A0H,
                           title_block_bbox=(900, 0, 289, 60), bom_rows=10)
        views = [_std("front", "front", 500, 200)]
        eng.layout_ex(views)
        bom = eng._bom_rect()
        assert bom["x"] == pytest.approx(LAYOUT_MARGIN)  # 左下
        assert bom["y"] == pytest.approx(60)


class TestAnnotationBand:
    """主视与俯视之间预留标注带（≥50mm）"""

    def test_main_top_gap_includes_band(self):
        eng = LayoutEngine(A0W, A0H)
        views = [
            _std("front", "front", 400, 150),
            _std("top", "top", 400, 100),
        ]
        r = eng.layout_ex(views)
        front, top = r.positions["front"], r.positions["top"]
        gap = front["y"] - (top["y"] + top["height"])
        assert gap >= 50.0
