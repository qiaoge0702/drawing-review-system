"""
零件类型识别模块单元测试（B-M1 智能骨架）

测试覆盖：
- 5类零件类型识别（标准件、长梁、板类、焊接小总成、复杂装配）
- 判定依据验证
- 优先级冲突处理
- 边界条件
"""

import pytest
from typing import Tuple

from app.generators.type_recognition import (
    PartType,
    BoundingBox,
    TypeRecognitionResult,
    recognize_part_type,
    recognize_from_sw_model,
    to_dict,
    _is_standard_part_by_filename,
    _is_standard_part_by_size,
    _is_plate,
    _is_beam,
    STANDARD_PART_KEYWORDS,
    STANDARD_PART_MAX_EDGE,
    PLATE_THICKNESS_RATIO,
    BEAM_SLENDER_RATIO,
    WELDMENT_MAX_COMPONENTS,
)


class TestStandardPartDetection:
    """标准件识别测试"""
    
    def test_detect_by_filename_bolt(self):
        """文件名含'螺栓'应识别为标准件"""
        result = recognize_part_type("M16螺栓.sldprt")
        assert result.part_type == PartType.STANDARD_PART
        assert "螺栓" in result.reason
        assert result.priority == 1
    
    def test_detect_by_filename_nut(self):
        """文件名含'螺母'应识别为标准件"""
        result = recognize_part_type("六角螺母.sldprt")
        assert result.part_type == PartType.STANDARD_PART
        assert "螺母" in result.reason
    
    def test_detect_by_filename_washer(self):
        """文件名含'垫圈'应识别为标准件"""
        result = recognize_part_type("弹簧垫圈.sldprt")
        assert result.part_type == PartType.STANDARD_PART
    
    def test_detect_by_filename_english(self):
        """英文文件名应识别为标准件"""
        result = recognize_part_type("bolt_m16.sldprt")
        assert result.part_type == PartType.STANDARD_PART
        assert "bolt" in result.reason.lower()
    
    def test_detect_by_filename_bearing(self):
        """文件名含'bearing'应识别为标准件"""
        result = recognize_part_type("6205_bearing.sldprt")
        assert result.part_type == PartType.STANDARD_PART
    
    def test_detect_by_size_small_part(self):
        """包围盒最大边<100mm应识别为标准件"""
        box = BoundingBox(0, 0, 0, 50, 60, 80)  # max_edge=80mm
        result = recognize_part_type("small_part.sldprt", box)
        assert result.part_type == PartType.STANDARD_PART
        assert "80.00mm" in result.reason
        assert "100.0mm" in result.reason
    
    def test_not_standard_by_size(self):
        """包围盒最大边≥100mm不应因尺寸识别为标准件"""
        box = BoundingBox(0, 0, 0, 100, 100, 150)  # max_edge=150mm
        result = recognize_part_type("large_part.sldprt", box)
        # 不应是标准件（基于尺寸），但可能是其他类型
        assert result.part_type != PartType.STANDARD_PART or "文件名" in result.reason


class TestBeamDetection:
    """长梁/杆类识别测试"""
    
    def test_detect_lb26_long_beam(self):
        """LB26长梁识别测试（6512mm长）"""
        # 模拟LB26尺寸：长6512mm，宽~100mm，高~50mm
        box = BoundingBox(0, 0, 0, 6512, 100, 50)
        result = recognize_part_type("LB26_beam.sldprt", box)
        assert result.part_type == PartType.BEAM
        assert "细长特征" in result.reason
        assert "6512.00mm" in result.reason
    
    def test_detect_beam_ratio(self):
        """最大边>次小边×5应识别为长梁"""
        box = BoundingBox(0, 0, 0, 500, 50, 30)  # 500 > 50×5=250
        result = recognize_part_type("long_rod.sldprt", box)
        assert result.part_type == PartType.BEAM
        assert result.priority == 2
    
    def test_not_beam_ratio(self):
        """最大边≤次小边×5不应识别为长梁"""
        box = BoundingBox(0, 0, 0, 200, 50, 40)  # 200 <= 50×5=250
        result = recognize_part_type("short_part.sldprt", box)
        assert result.part_type != PartType.BEAM


class TestPlateDetection:
    """板类/法兰识别测试"""
    
    def test_detect_thin_plate(self):
        """最小边<次小边/5应识别为板类"""
        # 薄板：1000×500×10mm，10 < 500/5=100
        box = BoundingBox(0, 0, 0, 1000, 500, 10)
        result = recognize_part_type("plate.sldprt", box)
        assert result.part_type == PartType.PLATE
        assert "薄板特征" in result.reason
        assert "10.00mm" in result.reason
    
    def test_detect_flange(self):
        """法兰识别测试"""
        box = BoundingBox(0, 0, 0, 300, 300, 20)  # 20 < 300/5=60
        result = recognize_part_type("flange_300.sldprt", box)
        assert result.part_type == PartType.PLATE
    
    def test_not_plate_thick(self):
        """厚板不应因薄板特征识别为板类（可能是其他类型或默认）"""
        box = BoundingBox(0, 0, 0, 100, 100, 50)  # 50 >= 100/5=20
        result = recognize_part_type("thick_block.sldprt", box)
        # 不是长梁也不是标准件，会回退到默认板类
        # 验证不是因薄板特征被识别
        assert "薄板特征" not in result.reason


class TestWeldmentDetection:
    """焊接小总成识别测试"""
    
    def test_detect_weldment_small(self):
        """装配体且零件数≤50应识别为焊接小总成"""
        result = recognize_part_type(
            "weldment_small.sldasm",
            is_assembly=True,
            component_count=25
        )
        assert result.part_type == PartType.WELDMENT
        assert "零件数=25" in result.reason
        assert "近似依据" in result.reason
    
    def test_detect_weldment_boundary(self):
        """边界值：零件数=50应识别为焊接小总成"""
        result = recognize_part_type(
            "weldment_50.sldasm",
            is_assembly=True,
            component_count=50
        )
        assert result.part_type == PartType.WELDMENT
    
    def test_not_weldment_large(self):
        """装配体且零件数>50应识别为复杂装配"""
        result = recognize_part_type(
            "large_assembly.sldasm",
            is_assembly=True,
            component_count=100
        )
        assert result.part_type == PartType.ASSEMBLY
        assert "100" in result.reason


class TestAssemblyDetection:
    """复杂装配识别测试"""
    
    def test_detect_large_assembly(self):
        """大型装配体识别"""
        result = recognize_part_type(
            "complex_machine.sldasm",
            is_assembly=True,
            component_count=200
        )
        assert result.part_type == PartType.ASSEMBLY
    
    def test_assembly_without_count(self):
        """装配体无零件数信息应识别为复杂装配"""
        result = recognize_part_type(
            "unknown_assembly.sldasm",
            is_assembly=True,
            component_count=None
        )
        assert result.part_type == PartType.ASSEMBLY
        assert "未知" in result.reason


class TestPriorityRules:
    """优先级规则测试"""
    
    def test_standard_part_priority_over_beam(self):
        """标准件优先级高于长梁"""
        # 小螺栓但形状细长
        box = BoundingBox(0, 0, 0, 80, 10, 10)  # 80mm长，细长但<100mm
        result = recognize_part_type("long_bolt.sldprt", box)
        # 应识别为标准件（基于尺寸），而非长梁
        assert result.part_type == PartType.STANDARD_PART
        assert result.priority == 1
    
    def test_beam_priority_over_plate(self):
        """长梁优先级高于板类"""
        # 既是长梁又是薄板的情况
        box = BoundingBox(0, 0, 0, 1000, 100, 5)  # 长梁+薄板
        result = recognize_part_type("beam_plate.sldprt", box)
        # 应识别为长梁（优先级2）而非板类（优先级3）
        assert result.part_type == PartType.BEAM
        assert result.priority == 2
    
    def test_filename_priority_over_geometry(self):
        """文件名关键词优先级高于几何特征"""
        # 大螺栓（>100mm）但文件名含螺栓
        box = BoundingBox(0, 0, 0, 200, 50, 50)  # max_edge=200mm
        result = recognize_part_type("special_bolt.sldprt", box)
        # 文件名优先级更高
        assert result.part_type == PartType.STANDARD_PART
        assert "文件名" in result.reason


class TestSWModelIntegration:
    """SW模型数据集成测试"""
    
    def test_recognize_from_sw_box(self):
        """从SW包围盒识别（单位转换：米→mm）"""
        # SW返回单位是米
        sw_box = (0.0, 0.0, 0.0, 6.512, 0.1, 0.05)  # LB26尺寸（米）
        result = recognize_from_sw_model("LB26.sldprt", sw_box)
        assert result.part_type == PartType.BEAM
        assert result.bounding_box is not None
        assert result.bounding_box.dx == 6512.0  # 转换为mm
    
    def test_recognize_assembly_with_components(self):
        """带零件数的装配体识别"""
        sw_box = (0.0, 0.0, 0.0, 1.0, 0.5, 0.3)
        result = recognize_from_sw_model(
            "assembly.sldasm",
            sw_box,
            is_assembly=True,
            component_count=30
        )
        assert result.part_type == PartType.WELDMENT
        assert result.component_count == 30


class TestToDict:
    """结果序列化测试"""
    
    def test_to_dict_full(self):
        """完整结果转字典"""
        box = BoundingBox(0, 0, 0, 100, 50, 10)
        result = recognize_part_type("test.sldprt", box, component_count=5)
        data = to_dict(result)
        
        assert data["type"] == result.part_type.value
        assert "reason" in data
        assert "priority" in data
        assert "bounding_box" in data
        assert data["bounding_box"]["dx"] == 100.0
        assert "edges" in data["bounding_box"]


class TestEdgeCases:
    """边界条件测试"""
    
    def test_zero_size_box(self):
        """零尺寸包围盒处理"""
        box = BoundingBox(0, 0, 0, 0, 0, 0)
        result = recognize_part_type("degenerate.sldprt", box)
        # 应能处理，不抛异常
        assert isinstance(result.part_type, PartType)
    
    def test_negative_box(self):
        """负坐标包围盒处理"""
        box = BoundingBox(-100, -50, -20, 0, 0, 0)
        result = recognize_part_type("negative.sldprt", box)
        assert result.bounding_box is not None
        assert result.bounding_box.dx == 100.0
    
    def test_empty_filename(self):
        """空文件名处理"""
        result = recognize_part_type("")
        # 不应抛异常，应有默认行为
        assert isinstance(result.part_type, PartType)
    
    def test_none_box(self):
        """None包围盒处理"""
        result = recognize_part_type("unknown.sldprt", None)
        assert result.bounding_box is None
        # 应有回退逻辑


class TestHelperFunctions:
    """辅助函数测试"""
    
    def test_is_standard_part_by_filename_true(self):
        """文件名检测正确识别"""
        assert _is_standard_part_by_filename("bolt.sldprt") is True
        assert _is_standard_part_by_filename("M16_BOLT.STEP") is True
    
    def test_is_standard_part_by_filename_false(self):
        """文件名检测正确排除"""
        assert _is_standard_part_by_filename("beam.sldprt") is False
        assert _is_standard_part_by_filename("plate.sldprt") is False
    
    def test_is_standard_part_by_size_true(self):
        """尺寸检测正确识别"""
        box = BoundingBox(0, 0, 0, 50, 60, 70)
        assert _is_standard_part_by_size(box) is True
    
    def test_is_standard_part_by_size_false(self):
        """尺寸检测正确排除"""
        box = BoundingBox(0, 0, 0, 100, 100, 100)
        assert _is_standard_part_by_size(box) is False
    
    def test_is_plate_true(self):
        """板类检测正确识别"""
        box = BoundingBox(0, 0, 0, 1000, 500, 10)  # 10 < 500/5=100
        assert _is_plate(box) is True
    
    def test_is_plate_false(self):
        """板类检测正确排除"""
        box = BoundingBox(0, 0, 0, 100, 100, 50)  # 50 >= 100/5=20
        assert _is_plate(box) is False
    
    def test_is_beam_true(self):
        """长梁检测正确识别"""
        box = BoundingBox(0, 0, 0, 500, 50, 40)  # 500 > 50×5=250
        assert _is_beam(box) is True
    
    def test_is_beam_false(self):
        """长梁检测正确排除"""
        box = BoundingBox(0, 0, 0, 200, 50, 40)  # 200 <= 50×5=250
        assert _is_beam(box) is False


class TestConstants:
    """常量验证"""
    
    def test_standard_part_keywords(self):
        """标准件关键词列表完整"""
        assert "螺栓" in STANDARD_PART_KEYWORDS
        assert "螺母" in STANDARD_PART_KEYWORDS
        assert "bolt" in STANDARD_PART_KEYWORDS
        assert "nut" in STANDARD_PART_KEYWORDS
    
    def test_threshold_values(self):
        """阈值常量正确"""
        assert STANDARD_PART_MAX_EDGE == 100.0
        assert PLATE_THICKNESS_RATIO == 5.0
        assert BEAM_SLENDER_RATIO == 5.0
        assert WELDMENT_MAX_COMPONENTS == 50
