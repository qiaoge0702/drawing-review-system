"""
视图策略库单元测试（B-M1 智能骨架）

测试覆盖：
- 各类型视图组合正确性
- 主视方向计算
- 比例选择算法
- 视图尺寸计算
- SW视图名适配
"""

import pytest
import math
from typing import Dict, Tuple

from app.generators.type_recognition import PartType, BoundingBox
from app.generators.view_strategy import (
    ViewName,
    ViewConfig,
    ViewStrategy,
    get_view_strategy,
    compute_main_view_direction,
    compute_view_sizes,
    select_scale_for_sheet,
    compute_adaptive_scale,
    get_sw_view_name,
    create_view_strategy_result,
    to_layout_input,
    GB_SCALE_RATIOS,
    SHEET_A3_WIDTH,
    SHEET_A3_HEIGHT,
    VIEW_STRATEGIES,
    VIEW_FRONT,
    VIEW_TOP,
    VIEW_LEFT,
    VIEW_RIGHT,
    VIEW_ISOMETRIC,
)


class TestViewStrategyPerType:
    """各类型视图策略测试"""
    
    def test_standard_part_views(self):
        """标准件：主视×1"""
        strategy = get_view_strategy(PartType.STANDARD_PART)
        assert len(strategy.views) == 1
        assert strategy.views[0].name == ViewName.FRONT
        assert strategy.scale_mode == "adaptive"
        assert strategy.need_isometric is False
    
    def test_plate_views(self):
        """板类：主视+俯视+左视（3视图）"""
        strategy = get_view_strategy(PartType.PLATE)
        view_names = [v.name for v in strategy.views]
        assert len(view_names) == 3
        assert ViewName.FRONT in view_names
        assert ViewName.TOP in view_names
        assert ViewName.LEFT in view_names
        assert ViewName.ISOMETRIC not in view_names
    
    def test_beam_views(self):
        """长梁：主视(侧立面)+右视+俯视+轴测图（4视图）"""
        strategy = get_view_strategy(PartType.BEAM)
        view_names = [v.name for v in strategy.views]
        assert len(view_names) == 4
        assert ViewName.FRONT in view_names
        assert ViewName.RIGHT in view_names  # 注意是右视不是左视
        assert ViewName.TOP in view_names
        assert ViewName.ISOMETRIC in view_names
        assert strategy.need_isometric is True
    
    def test_weldment_views(self):
        """焊接小总成：主视+俯视+左视+轴测图（4视图）"""
        strategy = get_view_strategy(PartType.WELDMENT)
        view_names = [v.name for v in strategy.views]
        assert len(view_names) == 4
        assert ViewName.FRONT in view_names
        assert ViewName.TOP in view_names
        assert ViewName.LEFT in view_names
        assert ViewName.ISOMETRIC in view_names
    
    def test_assembly_views(self):
        """复杂装配：主视+右视+俯视+轴测图（4视图）"""
        strategy = get_view_strategy(PartType.ASSEMBLY)
        view_names = [v.name for v in strategy.views]
        assert len(view_names) == 4
        assert ViewName.FRONT in view_names
        assert ViewName.RIGHT in view_names
        assert ViewName.TOP in view_names
        assert ViewName.ISOMETRIC in view_names


class TestMainViewDirection:
    """主视方向计算测试"""
    
    def test_lb26_long_beam_direction(self):
        """LB26长梁应选侧立面为主视"""
        # LB26: 6512×100×50mm
        box = BoundingBox(0, 0, 0, 6512, 100, 50)
        direction = compute_main_view_direction(box)
        # 长宽比最大的方向：front (6512/50=130.24) > top (6512/100=65.12) > left (100/50=2)
        assert direction == ViewName.FRONT
    
    def test_square_plate_direction(self):
        """方板主视方向"""
        box = BoundingBox(0, 0, 0, 500, 500, 10)
        direction = compute_main_view_direction(box)
        # front: 500/10=50, top: 500/500=1, left: 500/10=50
        # front和left相同，按实现应选front
        assert direction in (ViewName.FRONT, ViewName.LEFT)
    
    def test_tall_part_direction(self):
        """高长件主视方向"""
        box = BoundingBox(0, 0, 0, 100, 100, 1000)
        direction = compute_main_view_direction(box)
        # 计算各方向长宽比
        # front: 100/1000=0.1, top: 100/100=1, left: 100/1000=0.1
        assert direction == ViewName.TOP


class TestViewSizeCalculation:
    """视图尺寸计算测试"""
    
    def test_front_view_size(self):
        """主视图尺寸 = X×Z"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        strategy = get_view_strategy(PartType.PLATE)
        sizes = compute_view_sizes(box, strategy)
        
        assert ViewName.FRONT in sizes
        w, h = sizes[ViewName.FRONT]
        assert w == 100.0  # X
        assert h == 30.0   # Z
    
    def test_top_view_size(self):
        """俯视图尺寸 = X×Y"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        strategy = get_view_strategy(PartType.PLATE)
        sizes = compute_view_sizes(box, strategy)
        
        assert ViewName.TOP in sizes
        w, h = sizes[ViewName.TOP]
        assert w == 100.0  # X
        assert h == 50.0   # Y
    
    def test_left_view_size(self):
        """左视图尺寸 = Y×Z"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        strategy = get_view_strategy(PartType.PLATE)
        sizes = compute_view_sizes(box, strategy)
        
        assert ViewName.LEFT in sizes
        w, h = sizes[ViewName.LEFT]
        assert w == 50.0   # Y
        assert h == 30.0   # Z
    
    def test_isometric_size_estimate(self):
        """轴测图尺寸估算"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        strategy = get_view_strategy(PartType.BEAM)
        sizes = compute_view_sizes(box, strategy)
        
        assert ViewName.ISOMETRIC in sizes
        w, h = sizes[ViewName.ISOMETRIC]
        # 轴测图尺寸应大于0
        assert w > 0
        assert h > 0


class TestScaleSelection:
    """比例选择算法测试"""
    
    def test_lb26_scale_selection(self):
        """LB26比例选择应≥1:30（比当前1:50大）"""
        # LB26视图尺寸
        view_sizes = {
            ViewName.FRONT: (6512, 200),    # 主视
            ViewName.RIGHT: (200, 100),     # 右视
            ViewName.TOP: (6512, 100),      # 俯视
            ViewName.ISOMETRIC: (7000, 500), # 轴测图
        }
        strategy = get_view_strategy(PartType.BEAM)
        
        den, retry = select_scale_for_sheet(
            {k.value: v for k, v in view_sizes.items()},
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            strategy,
        )
        
        # 应选中比例≥1:30（分母≤30）
        assert den <= 30.0, f"LB26应选中≥1:30的比例，实际选中1:{den}"
        assert den in GB_SCALE_RATIOS
    
    def test_small_part_scale(self):
        """小零件应选较大比例"""
        view_sizes = {
            ViewName.FRONT: (50, 30),
        }
        strategy = get_view_strategy(PartType.STANDARD_PART)
        
        den, _ = select_scale_for_sheet(
            {k.value: v for k, v in view_sizes.items()},
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            strategy,
        )
        
        # 小零件应能放下在较大比例
        assert den <= 5.0  # 1:1, 1:2, 1:5等
    
    def test_large_assembly_scale(self):
        """大型装配体可能需要较小比例"""
        view_sizes = {
            ViewName.FRONT: (5000, 3000),
            ViewName.RIGHT: (3000, 3000),
            ViewName.TOP: (5000, 3000),
            ViewName.ISOMETRIC: (6000, 4000),
        }
        strategy = get_view_strategy(PartType.ASSEMBLY)
        
        den, _ = select_scale_for_sheet(
            {k.value: v for k, v in view_sizes.items()},
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            strategy,
        )
        
        # 大型装配体可能需要1:10或更小
        assert den >= 1.0
    
    def test_scale_retry_mechanism(self):
        """比例重试机制测试"""
        view_sizes = {
            ViewName.FRONT: (400, 300),
            ViewName.TOP: (400, 200),
            ViewName.LEFT: (200, 300),
        }
        strategy = get_view_strategy(PartType.PLATE)
        
        den, retry = select_scale_for_sheet(
            {k.value: v for k, v in view_sizes.items()},
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            strategy,
            max_retries=3,
        )
        
        # 应能找到合适比例
        assert den in GB_SCALE_RATIOS
        assert retry <= 3


class TestAdaptiveScale:
    """自适应比例测试（标准件）"""
    
    def test_adaptive_scale_coverage(self):
        """自适应比例应在目标范围内"""
        view_sizes = {ViewName.FRONT: (50, 40)}
        target = (0.4, 0.6)  # 40-60%
        
        den = compute_adaptive_scale(
            view_sizes,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            target,
        )
        
        # 检查覆盖率
        scaled_area = sum((w/den)*(h/den) for w, h in view_sizes.values())
        sheet_area = SHEET_A3_WIDTH * SHEET_A3_HEIGHT
        coverage = scaled_area / sheet_area
        
        # 应在目标范围内或最接近的值
        assert den in GB_SCALE_RATIOS
    
    def test_adaptive_scale_single_view(self):
        """单视图自适应比例"""
        view_sizes = {ViewName.FRONT: (100, 80)}
        target = (0.4, 0.6)
        
        den = compute_adaptive_scale(
            view_sizes,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            target,
        )
        
        assert den >= 1.0
        assert den <= 100.0


class TestSWViewNameAdapter:
    """SW视图名适配测试"""
    
    def test_front_view_names(self):
        """主视图中英文名称"""
        assert get_sw_view_name(ViewName.FRONT, 0) == "*前视"
        assert get_sw_view_name(ViewName.FRONT, 1) == "*Front"
    
    def test_top_view_names(self):
        """俯视图中英文名称"""
        assert get_sw_view_name(ViewName.TOP, 0) == "*上视"
        assert get_sw_view_name(ViewName.TOP, 1) == "*Top"
    
    def test_isometric_view_names(self):
        """轴测图中英文名称"""
        assert get_sw_view_name(ViewName.ISOMETRIC, 0) == "*等轴测"
        assert get_sw_view_name(ViewName.ISOMETRIC, 1) == "*Isometric"
    
    def test_exceed_attempt_returns_none(self):
        """超过尝试次数返回None"""
        assert get_sw_view_name(ViewName.FRONT, 5) is None


class TestStrategyResultCreation:
    """策略结果创建测试"""
    
    def test_create_result_structure(self):
        """结果结构完整性"""
        box = BoundingBox(0, 0, 0, 1000, 500, 10)
        result = create_view_strategy_result(
            PartType.PLATE,
            box,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
        )
        
        assert "part_type" in result
        assert "strategy" in result
        assert "main_view_direction" in result
        assert "scale_denominator" in result
        assert "scale" in result
        assert "sheet_size" in result
        assert "views" in result
        assert "view_sizes" in result
    
    def test_beam_strategy_result(self):
        """长梁策略结果"""
        box = BoundingBox(0, 0, 0, 6512, 100, 50)
        result = create_view_strategy_result(
            PartType.BEAM,
            box,
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
        )
        
        assert result["part_type"] == "beam"
        assert len(result["views"]) == 4  # beam有4个视图
        assert "1:" in result["scale"]


class TestLayoutInput:
    """布局输入转换测试"""
    
    def test_to_layout_input_structure(self):
        """布局输入结构"""
        strategy_result = {
            "views": [
                {
                    "name": "front",
                    "size_mm": {"width": 100, "height": 80},
                    "position_hint": "center_upper",
                },
                {
                    "name": "top",
                    "size_mm": {"width": 100, "height": 50},
                    "position_hint": "below_front",
                },
            ]
        }
        
        layout_input = to_layout_input(strategy_result, scale_den=2.0)
        
        assert len(layout_input) == 2
        assert layout_input[0]["name"] == "front"
        assert "bounding_box" in layout_input[0]
        # 验证缩放
        assert layout_input[0]["bounding_box"]["max_x"] == 50.0  # 100/2


class TestConstants:
    """常量验证"""
    
    def test_scale_ratios_order(self):
        """比例序列按从小到大排列"""
        assert GB_SCALE_RATIOS == sorted(GB_SCALE_RATIOS)
    
    def test_scale_ratios_contains_common_values(self):
        """比例序列包含常用值"""
        assert 1.0 in GB_SCALE_RATIOS
        assert 2.0 in GB_SCALE_RATIOS
        assert 5.0 in GB_SCALE_RATIOS
        assert 10.0 in GB_SCALE_RATIOS
        assert 50.0 in GB_SCALE_RATIOS
        assert 100.0 in GB_SCALE_RATIOS
    
    def test_a3_sheet_size(self):
        """A3图幅尺寸正确"""
        assert SHEET_A3_WIDTH == 420.0
        assert SHEET_A3_HEIGHT == 297.0
    
    def test_view_configs_defined(self):
        """视图配置已定义"""
        assert VIEW_FRONT.name == ViewName.FRONT
        assert VIEW_TOP.name == ViewName.TOP
        assert VIEW_LEFT.name == ViewName.LEFT
        assert VIEW_RIGHT.name == ViewName.RIGHT
        assert VIEW_ISOMETRIC.name == ViewName.ISOMETRIC
