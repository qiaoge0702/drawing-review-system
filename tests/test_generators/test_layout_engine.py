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
        assert pos["y"] == pytest.approx(A3H - LAYOUT_MARGIN - 100)
        assert not result.warnings and not result.unplaced

    def test_widest_side_view_becomes_main(self):
        """长梁类：left 比 front 宽 → left 作主视"""
        eng = LayoutEngine(A3W, A3H)
        views = [_std("front", "front", 40, 80), _std("left", "left", 300, 80)]
        result = eng.layout_ex(views)
        main_y = A3H - LAYOUT_MARGIN - 80
        assert result.positions["left"]["y"] == pytest.approx(main_y)
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
