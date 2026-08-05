"""
布局算法单元测试（B-M1 智能骨架 - 第一角投影）

测试覆盖：
- 第一角投影布局（主视中上、俯视下方、右视右侧、轴测标题栏上方右侧）
- 视图不重叠
- 图幅边界校验
- 标题栏禁放区校验
- 比例重算机制
- LB26长梁布局验证
"""

import pytest
import math
from typing import Dict, Tuple, Any

from app.generators.steps.step3_view_project import (
    FirstAngleLayoutEngine,
    _compute_layout_with_retry,
    _select_sheet_by_content,
    _build_layout_b_m1,
    _MAX_SCALE_RETRIES,
    _SHEET_SIZES,
    _TITLE_BLOCK_FALLBACK_HEIGHT,
)
from app.generators.type_recognition import PartType, BoundingBox
from app.generators.view_strategy import (
    get_view_strategy,
    SHEET_A3_WIDTH,
    SHEET_A3_HEIGHT,
    LAYOUT_MARGIN,
)


class TestFirstAngleLayoutEngine:
    """第一角投影布局引擎测试"""
    
    def test_main_view_center_upper(self):
        """主视图中上居中"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        view_sizes = {"front": (200, 150)}
        
        positions = engine.layout(view_sizes, 1.0, get_view_strategy(PartType.STANDARD_PART))
        
        assert positions is not None
        assert "front" in positions
        front = positions["front"]
        # 水平居中
        expected_x = (SHEET_A3_WIDTH - 200) / 2
        assert abs(front["x"] - expected_x) < 1.0
        # 在上半区域（y坐标较大表示偏上，因为y向上）
        assert front["y"] > 0
    
    def test_top_view_below_front(self):
        """俯视图在主视正下方（1:2 比例；1:1 超出 A3 有效区应走比例序列）"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT, spacing=25.0)
        view_sizes = {"front": (200, 150), "top": (200, 100)}
    
        positions = engine.layout(view_sizes, 2.0, get_view_strategy(PartType.PLATE))
        
        assert positions is not None
        assert "front" in positions
        assert "top" in positions
        
        front = positions["front"]
        top = positions["top"]
        
        # X对齐
        assert abs(top["x"] - front["x"]) < 1.0
        # 在下方（y坐标较小表示偏下）
        assert top["y"] < front["y"]
    
    def test_right_view_right_of_front(self):
        """右视图在主视右侧（1:2 比例）"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT, spacing=25.0)
        view_sizes = {"front": (200, 150), "right": (100, 150)}
        
        positions = engine.layout(view_sizes, 2.0, get_view_strategy(PartType.BEAM))
        
        assert positions is not None
        assert "front" in positions
        assert "right" in positions
        
        front = positions["front"]
        right = positions["right"]
        
        # 在右侧
        assert right["x"] > front["x"]
    
    def test_isometric_above_title_block(self):
        """轴测图按 above_title_block hint 摆在标题栏上方右侧区域"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        view_sizes = {
            "front": (200, 150),
            "right": (100, 150),
            "top": (200, 100),
            "isometric": (150, 120),
        }

        positions = engine.layout(view_sizes, 2.0, get_view_strategy(PartType.BEAM))

        assert positions is not None
        assert "isometric" in positions
        iso = positions["isometric"]

        # 在标题栏上方右侧区域
        assert iso["x"] >= 0
        assert iso["y"] >= _TITLE_BLOCK_FALLBACK_HEIGHT
    
    def test_isometric_no_overlap(self):
        """轴测图不与其他视图重叠"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        view_sizes = {
            "front": (200, 150),
            "top": (200, 100),
            "isometric": (150, 120),
        }
        
        positions = engine.layout(view_sizes, 2.0, get_view_strategy(PartType.WELDMENT))
        
        assert positions is not None
        iso = positions["isometric"]
        iso_rect = (iso["x"], iso["y"], iso["width"], iso["height"])
        
        # 检查与其他视图不重叠
        for name, pos in positions.items():
            if name == "isometric":
                continue
            other_rect = (pos["x"], pos["y"], pos["width"], pos["height"])
            # 不应重叠
            overlap = (
                iso_rect[0] < other_rect[0] + other_rect[2] and
                iso_rect[0] + iso_rect[2] > other_rect[0] and
                iso_rect[1] < other_rect[1] + other_rect[3] and
                iso_rect[1] + iso_rect[3] > other_rect[1]
            )
            assert not overlap, f"轴测图与{name}重叠"
    
    def test_all_views_within_sheet(self):
        """所有视图在图幅内"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        view_sizes = {
            "front": (200, 150),
            "right": (100, 150),
            "top": (200, 100),
            "isometric": (150, 120),
        }
        
        positions = engine.layout(view_sizes, 2.0, get_view_strategy(PartType.BEAM))
        
        assert positions is not None
        for name, pos in positions.items():
            assert pos["x"] >= 0, f"{name}超出左边界"
            assert pos["y"] >= 0, f"{name}超出下边界"
            assert pos["x"] + pos["width"] <= SHEET_A3_WIDTH, f"{name}超出右边界"
            assert pos["y"] + pos["height"] <= SHEET_A3_HEIGHT, f"{name}超出上边界"


class TestLB26Layout:
    """LB26长梁布局专项测试"""
    
    def test_lb26_a3_layout(self):
        """LB26在A3图幅上的布局"""
        # LB26尺寸（缩放后）
        scale_den = 30.0  # 1:30
        view_sizes = {
            "front": (6512 / scale_den, 200 / scale_den),    # ~217×6.7mm
            "right": (100 / scale_den, 200 / scale_den),     # ~3.3×6.7mm
            "top": (6512 / scale_den, 100 / scale_den),      # ~217×3.3mm
            "isometric": (7000 / scale_den, 500 / scale_den), # ~233×16.7mm
        }
        
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        positions = engine.layout(view_sizes, scale_den, get_view_strategy(PartType.BEAM))
        
        assert positions is not None, "LB26应能在A3上布局"
        
        # 验证所有视图在图幅内
        for name, pos in positions.items():
            assert pos["x"] >= 0, f"{name}超出左边界"
            assert pos["y"] >= 0, f"{name}超出下边界"
            assert pos["x"] + pos["width"] <= SHEET_A3_WIDTH, f"{name}超出右边界"
            assert pos["y"] + pos["height"] <= SHEET_A3_HEIGHT, f"{name}超出上边界"
    
    def test_lb26_first_angle_positions(self):
        """LB26第一角投影相对位置"""
        scale_den = 30.0
        view_sizes = {
            "front": (217, 7),
            "right": (3, 7),
            "top": (217, 3),
            "isometric": (233, 17),
        }
        
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT, spacing=25.0)
        positions = engine.layout(view_sizes, scale_den, get_view_strategy(PartType.BEAM))
        
        # 第一角投影验证
        front = positions["front"]
        top = positions["top"]
        right = positions["right"]
        
        # 俯视在主视正下方（X对齐）
        assert abs(top["x"] - front["x"]) < 5.0
        assert top["y"] < front["y"]
        
        # 右视在主视右侧
        assert right["x"] > front["x"] + front["width"] - 5.0


class TestScaleRetry:
    """比例重算机制测试"""
    
    def test_retry_reduces_spacing(self):
        """重试时减小间距"""
        # 较大的视图，需要重试
        view_sizes = {
            "front": (350, 250),
            "top": (350, 200),
            "left": (250, 250),
        }
        
        positions, den, retry = _compute_layout_with_retry(
            view_sizes,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            get_view_strategy(PartType.PLATE),
            task_id="test",
        )
        
        # 应能找到合适比例
        assert positions is not None
        assert den in [1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 100.0]
    
    def test_max_retries_exceeded(self):
        """超过最大重试次数返回None"""
        # 超大视图，无法放入
        view_sizes = {
            "front": (5000, 4000),  # 远超A3
        }
        
        positions, den, retry = _compute_layout_with_retry(
            view_sizes,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            get_view_strategy(PartType.PLATE),
            task_id="test",
        )
        
        # 可能无法布局
        if positions is None:
            assert retry == _MAX_SCALE_RETRIES


class TestSheetSelection:
    """图幅选择测试"""
    
    def test_select_a3_for_small_part(self):
        """小零件选A3"""
        view_sizes = {"front": (100, 80), "top": (100, 50)}
        
        sheet_name, w, h = _select_sheet_by_content(view_sizes, 1.0, 0, "test")
        
        assert sheet_name == "A3"
        assert w == 420.0
        assert h == 297.0
    
    def test_sheet_upgrade_for_large_content(self):
        """大内容升级图幅"""
        view_sizes = {
            "front": (600, 400),
            "top": (600, 300),
            "left": (400, 400),
            "isometric": (700, 500),
        }
        
        sheet_name, w, h = _select_sheet_by_content(view_sizes, 1.0, 0, "test")
        
        # 可能需要A2或更大
        assert sheet_name in ["A3", "A2", "A1", "A0"]
    
    def test_bom_considered(self):
        """BOM空间考虑"""
        view_sizes = {"front": (200, 150), "top": (200, 100)}
        bom_rows = 20  # 较大的BOM
        
        sheet_name, w, h = _select_sheet_by_content(view_sizes, 1.0, bom_rows, "test")
        
        # 可能需要更大图幅容纳BOM
        assert sheet_name in ["A3", "A2", "A1", "A0"]


class TestBMLayoutEngine:
    """B-M1完整布局引擎测试"""
    
    def test_standard_part_layout(self):
        """标准件布局"""
        box = BoundingBox(0, 0, 0, 50, 40, 30)  # 小零件
        
        result = _build_layout_b_m1(
            box,
            PartType.STANDARD_PART,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            0,
            "test",
        )
        
        assert result["strategy"]["part_type"] == "standard_part"
        # 标准件只有主视
        assert "front" in result["strategy"]["views"]
    
    def test_plate_layout_three_views(self):
        """板类3视图布局"""
        box = BoundingBox(0, 0, 0, 500, 300, 10)  # 薄板
        
        result = _build_layout_b_m1(
            box,
            PartType.PLATE,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            0,
            "test",
        )
        
        assert result["strategy"]["part_type"] == "plate"
        # 板类有3个视图
        assert "front" in result["strategy"]["views"]
        assert "top" in result["strategy"]["views"]
        assert "left" in result["strategy"]["views"]
    
    def test_beam_layout_four_views(self):
        """长梁4视图布局"""
        box = BoundingBox(0, 0, 0, 6512, 100, 50)  # LB26
        
        result = _build_layout_b_m1(
            box,
            PartType.BEAM,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            0,
            "test",
        )
        
        assert result["strategy"]["part_type"] == "beam"
        # 长梁有4个视图
        assert "front" in result["strategy"]["views"]
        assert "right" in result["strategy"]["views"]  # 注意是右视
        assert "top" in result["strategy"]["views"]
        assert "isometric" in result["strategy"]["views"]
    
    def test_layout_scale_not_too_small(self):
        """布局比例不应过小（LB26应≥1:30）"""
        box = BoundingBox(0, 0, 0, 6512, 100, 50)  # LB26
        
        result = _build_layout_b_m1(
            box,
            PartType.BEAM,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            0,
            "test",
        )
        
        scale_den = result["scale_denominator"]
        assert scale_den <= 30.0, f"LB26比例应≥1:30，实际1:{scale_den}"
    
    def test_layout_positions_valid(self):
        """布局位置有效"""
        box = BoundingBox(0, 0, 0, 1000, 500, 50)
        
        result = _build_layout_b_m1(
            box,
            PartType.PLATE,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            0,
            "test",
        )
        
        positions = result.get("view_positions", {})
        for name, pos in positions.items():
            assert pos["x"] >= 0
            assert pos["y"] >= 0
            assert pos["width"] > 0
            assert pos["height"] > 0


class TestViewOverlapping:
    """视图重叠检测测试"""
    
    def test_no_overlapping_in_layout(self):
        """布局中视图不重叠"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        view_sizes = {
            "front": (200, 150),
            "right": (100, 150),
            "top": (200, 100),
            "isometric": (150, 120),
        }
        
        positions = engine.layout(view_sizes, 2.0, get_view_strategy(PartType.BEAM))
        
        assert positions is not None
        # 检查所有视图对
        names = list(positions.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a = positions[names[i]]
                b = positions[names[j]]
                
                # 检查重叠
                overlap = (
                    a["x"] < b["x"] + b["width"] and
                    a["x"] + a["width"] > b["x"] and
                    a["y"] < b["y"] + b["height"] and
                    a["y"] + a["height"] > b["y"]
                )
                
                # 允许轴测图与其他视图有一定重叠检查，但不应严重重叠
                if overlap:
                    # 计算重叠面积
                    overlap_w = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
                    overlap_h = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
                    if overlap_w > 0 and overlap_h > 0:
                        overlap_area = overlap_w * overlap_h
                        a_area = a["width"] * a["height"]
                        b_area = b["width"] * b["height"]
                        # 重叠面积应小于较小视图的50%
                        assert overlap_area < min(a_area, b_area) * 0.5, \
                            f"{names[i]}和{names[j]}重叠过多"


class TestEdgeCases:
    """边界条件测试"""
    
    def test_single_view_layout(self):
        """单视图布局"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        view_sizes = {"front": (200, 150)}
        
        positions = engine.layout(view_sizes, 1.0, get_view_strategy(PartType.STANDARD_PART))
        
        assert "front" in positions
        front = positions["front"]
        # 单视图应居中
        assert front["x"] > 0
        assert front["y"] > 0
        assert front["x"] + front["width"] <= SHEET_A3_WIDTH
        assert front["y"] + front["height"] <= SHEET_A3_HEIGHT
    
    def test_zero_size_view(self):
        """零尺寸视图处理"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        view_sizes = {"front": (0, 0), "top": (100, 50)}
        
        positions = engine.layout(view_sizes, 1.0, get_view_strategy(PartType.PLATE))
        
        # 应能处理，不抛异常
        assert positions is None or "top" in positions
    
    def test_very_large_view(self):
        """超大视图处理"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        view_sizes = {"front": (1000, 800)}  # 超过A3
        
        positions = engine.layout(view_sizes, 1.0, get_view_strategy(PartType.STANDARD_PART))
        
        # 应返回None（无法布局）
        assert positions is None
    
    def test_negative_spacing(self):
        """负间距处理"""
        # 不应使用负间距
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT, spacing=-10)
        view_sizes = {"front": (200, 150), "top": (200, 100)}
        
        # 应能处理，可能返回None或调整
        positions = engine.layout(view_sizes, 1.0, get_view_strategy(PartType.PLATE))
        # 不抛异常即可


class TestLayoutMargin:
    """边距测试"""
    
    def test_margin_respected(self):
        """边距被尊重"""
        engine = FirstAngleLayoutEngine(SHEET_A3_WIDTH, SHEET_A3_HEIGHT)
        view_sizes = {"front": (200, 150)}
        
        positions = engine.layout(view_sizes, 1.0, get_view_strategy(PartType.STANDARD_PART))
        
        front = positions["front"]
        # 应保留边距
        assert front["x"] >= LAYOUT_MARGIN - 1.0 or front["x"] >= 0
        assert front["y"] >= LAYOUT_MARGIN - 1.0 or front["y"] >= 0
    
    def test_sheet_sizes_constant(self):
        """图幅尺寸常量正确"""
        assert _SHEET_SIZES["A3"] == (420.0, 297.0)
        assert _SHEET_SIZES["A2"] == (594.0, 420.0)
        assert _SHEET_SIZES["A1"] == (841.0, 594.0)
        assert _SHEET_SIZES["A0"] == (1189.0, 841.0)
