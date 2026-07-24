"""
规则引擎单元测试
"""

import pytest
from app.rules.engine import RuleEngine, VehicleDimensionRule, DrawingStandardRule


class TestVehicleDimensionRule:
    """测试外廓尺寸规则"""

    def test_default_limits(self):
        rule = VehicleDimensionRule()
        assert rule.max_overall_length_mm == 18000
        assert rule.max_overall_width_mm == 2550
        assert "GB 1589-2016" in rule.standards


class TestDrawingStandardRule:
    """测试图纸规范规则"""

    def test_default_requirements(self):
        rule = DrawingStandardRule()
        assert rule.title_block_required is True
        assert rule.scale_required is True


class TestRuleEngine:
    """测试规则引擎"""

    def test_load_default_rules(self):
        engine = RuleEngine()
        assert engine.config.vehicle_dimensions.max_overall_length_mm == 18000

    def test_check_dimensions_over_limit(self):
        engine = RuleEngine()
        drawing_info = {
            "extents": {"width": 20000, "height": 3000},
            "metadata": {"title": "测试", "scale": "1:1", "material": "Q345B"},
            "entity_counts": {"dimension": 5},
        }
        issues = engine.check_drawing(drawing_info)
        critical = [i for i in issues if i.severity.value == "critical"]
        assert len(critical) >= 1
        assert "长度超限" in critical[0].title

    def test_check_dimensions_within_limit(self):
        engine = RuleEngine()
        drawing_info = {
            "extents": {"width": 12000, "height": 2500},
            "metadata": {"title": "测试", "scale": "1:1", "material": "Q345B"},
            "entity_counts": {"dimension": 5},
        }
        issues = engine.check_drawing(drawing_info)
        critical = [i for i in issues if i.severity.value == "critical"]
        assert len(critical) == 0

    def test_check_missing_title(self):
        engine = RuleEngine()
        drawing_info = {
            "extents": {"width": 10000, "height": 2000},
            "metadata": {"title": "", "scale": "1:1", "material": "Q345B"},
            "entity_counts": {"dimension": 5},
        }
        issues = engine.check_drawing(drawing_info)
        titles = [i.title for i in issues]
        assert "缺少图样名称" in titles

    def test_check_missing_scale(self):
        engine = RuleEngine()
        drawing_info = {
            "extents": {"width": 10000, "height": 2000},
            "metadata": {"title": "测试", "scale": "", "material": "Q345B"},
            "entity_counts": {"dimension": 5},
        }
        issues = engine.check_drawing(drawing_info)
        titles = [i.title for i in issues]
        assert "缺少比例标注" in titles

    def test_check_missing_material(self):
        engine = RuleEngine()
        drawing_info = {
            "extents": {"width": 10000, "height": 2000},
            "metadata": {"title": "测试", "scale": "1:1", "material": ""},
            "entity_counts": {"dimension": 5},
        }
        issues = engine.check_drawing(drawing_info)
        titles = [i.title for i in issues]
        assert "缺少材料标注" in titles

    def test_check_missing_dimensions(self):
        engine = RuleEngine()
        drawing_info = {
            "extents": {"width": 10000, "height": 2000},
            "metadata": {"title": "测试", "scale": "1:1", "material": "Q345B"},
            "entity_counts": {"dimension": 0},
        }
        issues = engine.check_drawing(drawing_info)
        titles = [i.title for i in issues]
        assert "缺少尺寸标注" in titles

    def test_all_checks_pass(self):
        engine = RuleEngine()
        drawing_info = {
            "extents": {"width": 12000, "height": 2500},
            "metadata": {"title": "厢体底架", "scale": "1:10", "material": "Q345B"},
            "entity_counts": {"dimension": 20},
        }
        issues = engine.check_drawing(drawing_info)
        assert len(issues) == 0
