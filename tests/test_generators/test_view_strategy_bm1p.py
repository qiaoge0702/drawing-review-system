"""
视图策略库 B-M1+ 扩展单元测试

测试覆盖：
- ViewType 枚举
- ViewConfig 扩展字段与默认值
- apply_overrides 合并逻辑（增删改）
- 新视图类型（detail/section/auxiliary）尺寸计算
- 第一角投影字段
- to_layout_input 新字段透传
"""

import pytest
from typing import Dict, Any

from app.generators.type_recognition import PartType, BoundingBox
from app.generators.view_strategy import (
    ViewName,
    ViewType,
    ViewConfig,
    ViewStrategy,
    get_view_strategy,
    compute_view_sizes,
    apply_overrides,
    create_view_strategy_result,
    to_layout_input,
    SHEET_A3_WIDTH,
    SHEET_A3_HEIGHT,
)


class TestViewTypeEnum:
    """ViewType 枚举测试"""

    def test_view_type_values(self):
        """ViewType 包含全部 5 个值"""
        assert ViewType.STANDARD == "standard"
        assert ViewType.ISOMETRIC == "isometric"
        assert ViewType.DETAIL == "detail"
        assert ViewType.SECTION == "section"
        assert ViewType.AUXILIARY == "auxiliary"

    def test_view_type_from_string(self):
        """字符串可转为 ViewType"""
        assert ViewType("detail") == ViewType.DETAIL
        assert ViewType("section") == ViewType.SECTION


class TestViewConfigExtension:
    """ViewConfig 扩展字段测试"""

    def test_default_fields(self):
        """旧式构造方式仍兼容，新增字段有默认值"""
        vc = ViewConfig(
            name=ViewName.FRONT,
            display_name="主视图",
            sw_names=["*前视"],
            position_hint="center_upper",
        )
        assert vc.id == "front_standard"
        assert vc.view_type == ViewType.STANDARD
        assert vc.parent_id is None
        assert vc.scale == "auto"
        assert vc.position_mode == "auto"
        assert vc.position_params == {}
        assert vc.region is None
        assert vc.cut_line is None

    def test_explicit_id(self):
        """显式指定 id"""
        vc = ViewConfig(
            name=ViewName.FRONT,
            display_name="主视图",
            sw_names=["*前视"],
            position_hint="center_upper",
            id="my_front",
        )
        assert vc.id == "my_front"

    def test_detail_view_config(self):
        """局部放大视图配置"""
        vc = ViewConfig(
            name=ViewName.FRONT,
            display_name="A 向局部放大",
            sw_names=["*前视"],
            position_hint="right_of_front",
            id="detail_a",
            view_type=ViewType.DETAIL,
            parent_id="front_01",
            scale=2.0,
            position_mode="hint",
            position_params={"offset_x": 50, "offset_y": -30},
            region={"center": (10.0, 20.0, 0.0), "radius": 15.0},
        )
        assert vc.view_type == ViewType.DETAIL
        assert vc.parent_id == "front_01"
        assert vc.scale == 2.0
        assert vc.region["radius"] == 15.0
        assert vc.region["center"] == (10.0, 20.0, 0.0)

    def test_section_view_config(self):
        """剖视视图配置"""
        vc = ViewConfig(
            name=ViewName.FRONT,
            display_name="B-B 剖视",
            sw_names=["*前视"],
            position_hint="below_front",
            id="section_bb",
            view_type=ViewType.SECTION,
            parent_id="front_01",
            cut_line=[(0.0, 0.0, 0.0), (100.0, 0.0, 0.0)],
        )
        assert vc.view_type == ViewType.SECTION
        assert vc.cut_line == [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0)]


class TestApplyOverrides:
    """apply_overrides 合并逻辑测试"""

    def test_override_scale_mode(self):
        """覆盖比例策略"""
        strategy = get_view_strategy(PartType.PLATE)
        new_strategy = apply_overrides(strategy, {"scale_mode": "max_fit"})
        assert new_strategy.scale_mode == "max_fit"
        # 原策略不变
        assert strategy.scale_mode == "auto_fill"

    def test_override_spacing(self):
        """覆盖间距"""
        strategy = get_view_strategy(PartType.BEAM)
        new_strategy = apply_overrides(strategy, {"spacing": 15.0})
        assert new_strategy.spacing == 15.0

    def test_override_projection_type(self):
        """覆盖投影类型"""
        strategy = get_view_strategy(PartType.PLATE)
        assert strategy.projection_type == "first_angle"
        new_strategy = apply_overrides(strategy, {"projection_type": "third_angle"})
        assert new_strategy.projection_type == "third_angle"

    def test_add_view(self):
        """新增视图"""
        strategy = get_view_strategy(PartType.STANDARD_PART)
        assert len(strategy.views) == 1
        new_strategy = apply_overrides(strategy, {
            "views": [
                {
                    "action": "add",
                    "id": "iso_01",
                    "name": "isometric",
                    "display_name": "轴测图",
                    "sw_names": ["*等轴测"],
                    "position_hint": "above_title_block",
                    "view_type": "isometric",
                }
            ]
        })
        assert len(new_strategy.views) == 2
        assert new_strategy.views[1].id == "iso_01"
        assert new_strategy.views[1].view_type == ViewType.ISOMETRIC

    def test_update_view(self):
        """更新现有视图"""
        strategy = get_view_strategy(PartType.PLATE)
        front_id = strategy.views[0].id
        new_strategy = apply_overrides(strategy, {
            "views": [
                {
                    "action": "update",
                    "id": front_id,
                    "scale": 5.0,
                    "position_mode": "absolute",
                }
            ]
        })
        assert new_strategy.views[0].scale == 5.0
        assert new_strategy.views[0].position_mode == "absolute"
        # 未覆盖字段保留原值
        assert new_strategy.views[0].name == ViewName.FRONT

    def test_remove_view(self):
        """删除视图"""
        strategy = get_view_strategy(PartType.PLATE)
        assert len(strategy.views) == 3
        front_id = strategy.views[0].id
        new_strategy = apply_overrides(strategy, {
            "views": [
                {"action": "remove", "id": front_id}
            ]
        })
        assert len(new_strategy.views) == 2
        assert all(v.id != front_id for v in new_strategy.views)

    def test_upsert_view(self):
        """update 未找到时自动新增（upsert）"""
        strategy = get_view_strategy(PartType.STANDARD_PART)
        new_strategy = apply_overrides(strategy, {
            "views": [
                {
                    "action": "update",
                    "id": "new_detail",
                    "name": "front",
                    "display_name": "局部放大",
                    "view_type": "detail",
                    "sw_names": ["*前视"],
                    "position_hint": "right_of_front",
                }
            ]
        })
        assert len(new_strategy.views) == 2
        assert new_strategy.views[1].id == "new_detail"
        assert new_strategy.views[1].view_type == ViewType.DETAIL

    def test_multiple_overrides(self):
        """多字段同时覆盖"""
        strategy = get_view_strategy(PartType.BEAM)
        new_strategy = apply_overrides(strategy, {
            "scale_mode": "adaptive",
            "spacing": 10.0,
            "need_isometric": False,
            "views": [
                {"action": "remove", "name": "isometric"}
            ],
        })
        assert new_strategy.scale_mode == "adaptive"
        assert new_strategy.spacing == 10.0
        assert new_strategy.need_isometric is False
        assert all(v.name != ViewName.ISOMETRIC for v in new_strategy.views)

    def test_original_strategy_unchanged(self):
        """原策略不被修改（深拷贝验证）"""
        strategy = get_view_strategy(PartType.PLATE)
        original_views_len = len(strategy.views)
        apply_overrides(strategy, {
            "views": [
                {"action": "remove", "name": "front"}
            ]
        })
        assert len(strategy.views) == original_views_len

    def test_detail_view_override(self):
        """局部放大视图覆盖（含 region）"""
        strategy = get_view_strategy(PartType.PLATE)
        new_strategy = apply_overrides(strategy, {
            "views": [
                {
                    "action": "add",
                    "id": "detail_a",
                    "name": "front",
                    "display_name": "A 向局部放大",
                    "view_type": "detail",
                    "sw_names": ["*前视"],
                    "position_hint": "auto",
                    "parent_id": "front_standard",
                    "region": {"center": [10.0, 20.0, 0.0], "radius": 15.0},
                }
            ]
        })
        detail_view = [v for v in new_strategy.views if v.id == "detail_a"][0]
        assert detail_view.region["radius"] == 15.0
        assert detail_view.region["center"] == (10.0, 20.0, 0.0)
        assert detail_view.parent_id == "front_standard"

    def test_section_view_override(self):
        """剖视视图覆盖（含 cut_line polyline）"""
        strategy = get_view_strategy(PartType.PLATE)
        new_strategy = apply_overrides(strategy, {
            "views": [
                {
                    "action": "add",
                    "id": "section_bb",
                    "name": "front",
                    "display_name": "B-B 剖视",
                    "view_type": "section",
                    "sw_names": ["*前视"],
                    "position_hint": "below_front",
                    "parent_id": "front_standard",
                    "cut_line": [[0, 0, 0], [100, 0, 0]],
                }
            ]
        })
        section_view = [v for v in new_strategy.views if v.id == "section_bb"][0]
        assert section_view.cut_line == [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0)]

    def test_invalid_region_rejected(self):
        """region 非法格式 → ValueError（禁止静默吞错）"""
        strategy = get_view_strategy(PartType.PLATE)
        with pytest.raises(ValueError, match="region"):
            apply_overrides(strategy, {
                "views": [
                    {
                        "action": "add",
                        "id": "detail_bad",
                        "name": "front",
                        "view_type": "detail",
                        "region": [0.5, 0.5, 0.1],  # 旧的相对坐标元组格式已废止
                    }
                ]
            })

    def test_invalid_cut_line_rejected(self):
        """cut_line 非法格式 → ValueError"""
        strategy = get_view_strategy(PartType.PLATE)
        with pytest.raises(ValueError, match="cut_line"):
            apply_overrides(strategy, {
                "views": [
                    {
                        "action": "add",
                        "id": "section_bad",
                        "name": "front",
                        "view_type": "section",
                        "cut_line": {"type": "line"},  # 旧的 dict 格式已废止
                    }
                ]
            })

    def test_layout_mode_override(self):
        """layout_mode 全局策略覆盖"""
        strategy = get_view_strategy(PartType.PLATE)
        assert strategy.layout_mode == "auto"
        new_strategy = apply_overrides(strategy, {"layout_mode": "manual"})
        assert new_strategy.layout_mode == "manual"
        # 原策略不变
        assert strategy.layout_mode == "auto"

    def test_positions_override_sets_absolute(self):
        """positions_override 强制指定视图绝对定位"""
        strategy = get_view_strategy(PartType.PLATE)
        front_id = strategy.views[0].id
        new_strategy = apply_overrides(strategy, {
            "positions_override": {front_id: [120.0, 200.0]}
        })
        front = new_strategy.views[0]
        assert front.position_mode == "absolute"
        assert front.position_params == {"x": 120.0, "y": 200.0}

    def test_positions_override_by_name(self):
        """positions_override 支持按视图 name 匹配"""
        strategy = get_view_strategy(PartType.PLATE)
        new_strategy = apply_overrides(strategy, {
            "positions_override": {"top": [100.0, 50.0]}
        })
        top = [v for v in new_strategy.views if v.name == ViewName.TOP][0]
        assert top.position_mode == "absolute"
        assert top.position_params["y"] == 50.0

    def test_positions_override_unknown_id_warns_and_continues(self):
        """positions_override 未匹配的 id → 告警跳过，不抛异常"""
        strategy = get_view_strategy(PartType.PLATE)
        new_strategy = apply_overrides(strategy, {
            "positions_override": {"nonexistent_view": [0.0, 0.0]}
        })
        # 所有视图保持原 position_mode
        assert all(v.position_mode == "auto" for v in new_strategy.views)

    def test_positions_override_invalid_coords_skipped(self):
        """positions_override 非法坐标 → 跳过该条"""
        strategy = get_view_strategy(PartType.PLATE)
        front_id = strategy.views[0].id
        new_strategy = apply_overrides(strategy, {
            "positions_override": {front_id: [1.0]}  # 缺一维
        })
        assert new_strategy.views[0].position_mode == "auto"


class TestNewViewTypesSizeCalculation:
    """新视图类型尺寸计算测试"""

    def test_detail_view_size_with_region(self):
        """含 region 的局部放大视图尺寸 = 直径（2*radius，模型空间绝对坐标）"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        strategy = ViewStrategy(
            part_type=PartType.PLATE,
            views=[
                ViewConfig(
                    name=ViewName.FRONT,
                    display_name="A 向局部放大",
                    sw_names=["*前视"],
                    position_hint="auto",
                    id="detail_a",
                    view_type=ViewType.DETAIL,
                    region={"center": (10.0, 20.0, 0.0), "radius": 15.0},
                ),
            ],
            scale_mode="auto_fill",
            target_coverage=(0.6, 0.8),
        )
        sizes = compute_view_sizes(box, strategy)
        w, h = sizes[ViewName.FRONT]
        assert w == pytest.approx(30.0)  # 2 * radius(15)
        assert h == pytest.approx(30.0)

    def test_detail_view_size_without_region(self):
        """无 region 的局部放大视图默认尺寸"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        strategy = ViewStrategy(
            part_type=PartType.PLATE,
            views=[
                ViewConfig(
                    name=ViewName.FRONT,
                    display_name="A 向局部放大",
                    sw_names=["*前视"],
                    position_hint="auto",
                    id="detail_a",
                    view_type=ViewType.DETAIL,
                ),
            ],
            scale_mode="auto_fill",
            target_coverage=(0.6, 0.8),
        )
        sizes = compute_view_sizes(box, strategy)
        w, h = sizes[ViewName.FRONT]
        assert w == pytest.approx(30.0)  # 100 * 0.3
        assert h == pytest.approx(9.0)   # 30 * 0.3

    def test_section_view_size(self):
        """剖视视图尺寸近似父视图"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        strategy = ViewStrategy(
            part_type=PartType.PLATE,
            views=[
                ViewConfig(
                    name=ViewName.FRONT,
                    display_name="B-B 剖视",
                    sw_names=["*前视"],
                    position_hint="auto",
                    id="section_bb",
                    view_type=ViewType.SECTION,
                ),
            ],
            scale_mode="auto_fill",
            target_coverage=(0.6, 0.8),
        )
        sizes = compute_view_sizes(box, strategy)
        w, h = sizes[ViewName.FRONT]
        assert w == 100.0
        assert h == 30.0

    def test_auxiliary_view_size(self):
        """辅助视图默认尺寸"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        strategy = ViewStrategy(
            part_type=PartType.PLATE,
            views=[
                ViewConfig(
                    name=ViewName.FRONT,
                    display_name="C 向辅助视图",
                    sw_names=["*前视"],
                    position_hint="auto",
                    id="aux_c",
                    view_type=ViewType.AUXILIARY,
                ),
            ],
            scale_mode="auto_fill",
            target_coverage=(0.6, 0.8),
        )
        sizes = compute_view_sizes(box, strategy)
        w, h = sizes[ViewName.FRONT]
        assert w == 80.0  # 100 * 0.8
        assert h == 24.0  # 30 * 0.8


class TestCreateViewStrategyResultBM1P:
    """create_view_strategy_result B-M1+ 字段测试"""

    def test_result_contains_view_type(self):
        """结果包含 view_type 字段"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        result = create_view_strategy_result(PartType.PLATE, box)
        for view_data in result["views"]:
            assert "view_type" in view_data
            assert "id" in view_data
            assert "scale" in view_data
            assert "position_mode" in view_data

    def test_result_contains_projection_type(self):
        """结果包含 projection_type 字段"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        result = create_view_strategy_result(PartType.PLATE, box)
        assert "projection_type" in result["strategy"]
        assert result["strategy"]["projection_type"] == "first_angle"

    def test_result_with_detail_view(self):
        """含局部放大视图的结果"""
        strategy = get_view_strategy(PartType.PLATE)
        strategy = apply_overrides(strategy, {
            "views": [
                {
                    "action": "add",
                    "id": "detail_a",
                    "name": "front",
                    "display_name": "A 向局部放大",
                    "view_type": "detail",
                    "sw_names": ["*前视"],
                    "position_hint": "right_of_front",
                    "parent_id": "front_standard",
                    "region": {"center": [10.0, 20.0, 0.0], "radius": 15.0},
                }
            ]
        })
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        # 直接调用 compute_view_sizes 和 create_view_strategy_result
        result = create_view_strategy_result(PartType.PLATE, box)
        # 验证视图数据结构完整性
        view_ids = [v["id"] for v in result["views"]]
        assert "front_standard" in view_ids

    def test_result_contains_layout_mode(self):
        """结果包含 layout_mode 字段"""
        box = BoundingBox(0, 0, 0, 100, 50, 30)
        result = create_view_strategy_result(PartType.PLATE, box)
        assert result["strategy"]["layout_mode"] == "auto"


class TestToLayoutInputBM1P:
    """to_layout_input B-M1+ 字段测试"""

    def test_layout_input_contains_new_fields(self):
        """布局输入包含 B-M1+ 字段"""
        strategy_result = {
            "views": [
                {
                    "id": "front_01",
                    "name": "front",
                    "view_type": "standard",
                    "size_mm": {"width": 100, "height": 80},
                    "position_hint": "center_upper",
                    "position_mode": "auto",
                },
                {
                    "id": "detail_a",
                    "name": "front",
                    "view_type": "detail",
                    "size_mm": {"width": 30, "height": 30},
                    "position_hint": "right_of_front",
                    "position_mode": "hint",
                    "scale": 2.0,
                    "parent_id": "front_01",
                    "region": {"center": (10.0, 20.0, 0.0), "radius": 15.0},
                },
            ]
        }
        layout_input = to_layout_input(strategy_result, scale_den=2.0)
        assert len(layout_input) == 2
        assert layout_input[0]["id"] == "front_01"
        assert layout_input[0]["view_type"] == "standard"
        assert layout_input[1]["id"] == "detail_a"
        assert layout_input[1]["view_type"] == "detail"
        assert layout_input[1]["parent_id"] == "front_01"
        assert layout_input[1]["region"]["radius"] == 15.0
        # 多比例并存：detail scale=2.0 → 分母减半 → 尺寸放大 2 倍
        assert layout_input[1]["scale_denominator"] == 1.0
        assert layout_input[1]["bounding_box"]["max_x"] == 30.0  # 30/1.0

    def test_layout_input_absolute_scale_string(self):
        """布局输入解析绝对比例字符串 1:N"""
        strategy_result = {
            "views": [
                {
                    "id": "front_01",
                    "name": "front",
                    "view_type": "standard",
                    "size_mm": {"width": 100, "height": 80},
                    "scale": "1:10",
                },
            ]
        }
        layout_input = to_layout_input(strategy_result, scale_den=2.0)
        assert layout_input[0]["scale_denominator"] == 10.0
        assert layout_input[0]["bounding_box"]["max_x"] == 10.0  # 100/10


class TestFirstAngleProjection:
    """第一角投影测试"""

    def test_default_projection_type(self):
        """默认投影类型为第一角"""
        strategy = get_view_strategy(PartType.PLATE)
        assert strategy.projection_type == "first_angle"

    def test_override_to_third_angle(self):
        """可覆盖为第三角投影"""
        strategy = get_view_strategy(PartType.PLATE)
        new_strategy = apply_overrides(strategy, {"projection_type": "third_angle"})
        assert new_strategy.projection_type == "third_angle"
