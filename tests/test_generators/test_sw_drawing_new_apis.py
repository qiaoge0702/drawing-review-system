# -*- coding: utf-8 -*-
"""sw_drawing 新增 SW 原生视图 API 封装的 mock 层测试（调用序列断言，无需真机）"""
from unittest.mock import MagicMock

from app.generators.sw_drawing import (
    create_first_angle_views,
    create_detail_view,
    create_section_view,
    _DET_STYLE_STANDARD,
    _DET_CIRCLE_SHOW,
)


class TestFirstAngleViews:
    def test_success_returns_true(self):
        """真机实证：返回 bool，True=成功（视图对象由调用方枚举）"""
        drw = MagicMock()
        drw.Create1stAngleViews2.return_value = True
        warnings = []
        ok = create_first_angle_views(drw, r"C:\m.sldasm", warnings)
        drw.Create1stAngleViews2.assert_called_once_with(r"C:\m.sldasm")
        assert ok is True
        assert not warnings

    def test_failure_warns_and_returns_false(self):
        """失败 → False + 如实 warning（回退逐视图插入由调用方负责）"""
        drw = MagicMock()
        drw.Create1stAngleViews2.side_effect = Exception("COM error")
        warnings = []
        ok = create_first_angle_views(drw, r"C:\m.sldasm", warnings)
        assert ok is False
        assert len(warnings) == 1 and "回退" in warnings[0]


class TestDetailView:
    def test_call_sequence(self):
        """激活父视图 → 画圆 → CreateDetailViewAt3（参数为米 + 标准类型 + 倍率）"""
        drw = MagicMock()
        parent = MagicMock()
        parent.Name = "Drawing View1"
        drw.CreateDetailViewAt3.return_value = "detail_view"
        warnings = []
        view = create_detail_view(
            drw, parent,
            center_x_m=0.1, center_y_m=0.05, radius_m=0.02,
            pos_x_m=0.3, pos_y_m=0.2, scale_ratio=2.0,
            warnings=warnings,
        )
        drw.ActivateView.assert_called_once_with("Drawing View1")
        drw.SketchManager.CreateCircleByRadius.assert_called_once_with(
            0.1, 0.05, 0, 0.02)
        drw.CreateDetailViewAt3.assert_called_once_with(
            0.3, 0.2, 0.0, _DET_STYLE_STANDARD, 2.0, 1.0,
            "A", _DET_CIRCLE_SHOW, False)
        assert view == "detail_view"
        assert not warnings

    def test_circle_failure_warns(self):
        drw = MagicMock()
        parent = MagicMock()
        parent.Name = "V1"
        drw.SketchManager.CreateCircleByRadius.return_value = None
        warnings = []
        view = create_detail_view(drw, parent, 0, 0, 0.01, 0.2, 0.2, 2.0, warnings)
        assert view is None
        assert len(warnings) == 1


class TestSectionView:
    def test_polyline_drawn_per_segment(self):
        """cut_line 折线逐段 CreateLine，然后 CreateSectionViewAt4"""
        drw = MagicMock()
        parent = MagicMock()
        parent.Name = "V1"
        drw.CreateSectionViewAt4.return_value = "sec_view"
        warnings = []
        pts = [(0.0, 0.0), (0.1, 0.0), (0.1, 0.1)]
        view = create_section_view(drw, parent, pts, 0.3, 0.15, warnings)
        assert drw.SketchManager.CreateLine.call_count == 2
        drw.SketchManager.CreateLine.assert_any_call(0.0, 0.0, 0.0, 0.1, 0.0, 0.0)
        drw.SketchManager.CreateLine.assert_any_call(0.1, 0.0, 0.0, 0.1, 0.1, 0.0)
        drw.CreateSectionViewAt4.assert_called_once_with(
            0.3, 0.15, 0.0, "A", 0, None)
        assert view == "sec_view"

    def test_too_few_points_skips(self):
        """剖切线 <2 点 → 不碰 COM，如实 warning"""
        drw = MagicMock()
        warnings = []
        view = create_section_view(drw, MagicMock(), [(0.0, 0.0)], 0, 0, warnings)
        assert view is None
        drw.SketchManager.CreateLine.assert_not_called()
        assert "剖切线" in warnings[0]
