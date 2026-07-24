"""
结构模型单元测试
"""

import pytest
from pydantic import ValidationError
import math

from app.models.structure import (
    SectionType, MaterialSpec, SectionProperty, Longeron, CrossBeam,
    Connector, Weld, Subframe, VanBody, TankBody, Superstructure
)


class TestSectionType:
    """测试截面类型枚举"""
    
    def test_section_type_values(self):
        """测试截面类型枚举值"""
        assert SectionType.CHANNEL == "channel"
        assert SectionType.TUBE_RECT == "tube_rect"
        assert SectionType.TUBE_SQUARE == "tube_square"
        assert SectionType.I_BEAM == "i_beam"
        assert SectionType.C_CHANNEL == "c_channel"
        assert SectionType.Z_CHANNEL == "z_channel"
        assert SectionType.CUSTOM == "custom"


class TestMaterialSpec:
    """测试材料规格模型"""
    
    def test_create_material_spec(self):
        """测试创建材料规格"""
        material = MaterialSpec(
            grade="Q355B",
            standard="GB/T 1591",
            yield_strength=355,
            tensile_strength=490
        )
        
        assert material.grade == "Q355B"
        assert material.standard == "GB/T 1591"
        assert material.yield_strength == 355
        assert material.tensile_strength == 490
    
    def test_validate_strength_ratio(self):
        """测试强度比例验证"""
        # 抗拉强度小于屈服强度应该失败
        with pytest.raises(ValidationError) as exc_info:
            MaterialSpec(
                grade="Q355B",
                yield_strength=355,
                tensile_strength=300  # 小于屈服强度
            )
        
        assert "抗拉强度" in str(exc_info.value)
    
    def test_valid_strength_ratio(self):
        """测试有效强度比例"""
        material = MaterialSpec(
            grade="Q355B",
            yield_strength=355,
            tensile_strength=490  # 大于屈服强度
        )
        
        assert material.tensile_strength == 490
    
    def test_elongation_range(self):
        """测试延伸率范围"""
        # 延伸率超过100%应该失败
        with pytest.raises(ValidationError):
            MaterialSpec(
                grade="Q355B",
                yield_strength=355,
                tensile_strength=490,
                elongation=150  # 超过100
            )


class TestSectionProperty:
    """测试截面特性模型"""
    
    def test_create_section_property(self):
        """测试创建截面特性"""
        section = SectionProperty(
            section_type=SectionType.TUBE_RECT,
            height=100,
            width=50,
            thickness=5
        )
        
        assert section.section_type == SectionType.TUBE_RECT
        assert section.height == 100
        assert section.width == 50
        assert section.thickness == 5
    
    def test_calculate_section_modulus_x_approx(self):
        """测试估算截面模数"""
        section = SectionProperty(
            section_type=SectionType.TUBE_RECT,
            height=100,
            width=50,
            thickness=5
        )
        
        wx = section.calculate_section_modulus_x_approx()
        expected = 50 * 100 ** 2 / 6  # 83333.33
        assert abs(wx - expected) < 0.1
    
    def test_calculate_with_existing_modulus(self):
        """测试已有截面模数时返回现有值"""
        section = SectionProperty(
            section_type=SectionType.TUBE_RECT,
            height=100,
            width=50,
            thickness=5,
            section_modulus_x=100000
        )
        
        wx = section.calculate_section_modulus_x_approx()
        assert wx == 100000


class TestLongeron:
    """测试纵梁模型"""
    
    def test_create_longeron(self):
        """测试创建纵梁"""
        section = SectionProperty(
            section_type=SectionType.TUBE_RECT,
            height=100,
            width=50,
            thickness=5
        )
        material = MaterialSpec(
            grade="Q355B",
            yield_strength=355,
            tensile_strength=490
        )
        
        longeron = Longeron(
            name="前左纵梁",
            section=section,
            material=material,
            length=5000,
            start_point=(0, 0, 0),
            end_point=(5000, 0, 0)
        )
        
        assert longeron.name == "前左纵梁"
        assert longeron.length == 5000
    
    def test_get_direction_vector(self):
        """测试获取方向向量"""
        longeron = Longeron(
            name="测试纵梁",
            section=SectionProperty(
                section_type=SectionType.TUBE_RECT,
                height=100,
                width=50,
                thickness=5
            ),
            material=MaterialSpec(
                grade="Q355B",
                yield_strength=355,
                tensile_strength=490
            ),
            length=5000,
            start_point=(0, 0, 0),
            end_point=(5000, 100, 50)
        )
        
        vector = longeron.get_direction_vector()
        assert vector == (5000, 100, 50)
    
    def test_get_length_calculated(self):
        """测试计算长度"""
        longeron = Longeron(
            name="测试纵梁",
            section=SectionProperty(
                section_type=SectionType.TUBE_RECT,
                height=100,
                width=50,
                thickness=5
            ),
            material=MaterialSpec(
                grade="Q355B",
                yield_strength=355,
                tensile_strength=490
            ),
            length=5000,
            start_point=(0, 0, 0),
            end_point=(3000, 4000, 0)  # 3-4-5三角形，长度5000
        )
        
        calculated = longeron.get_length_calculated()
        assert abs(calculated - 5000) < 0.1


class TestSubframe:
    """测试副车架模型"""
    
    def test_create_subframe(self):
        """测试创建副车架"""
        section = SectionProperty(
            section_type=SectionType.TUBE_RECT,
            height=100,
            width=50,
            thickness=5
        )
        material = MaterialSpec(
            grade="Q355B",
            yield_strength=355,
            tensile_strength=490
        )
        
        longeron1 = Longeron(
            name="左纵梁",
            section=section,
            material=material,
            length=5000,
            start_point=(0, 400, 0),
            end_point=(5000, 400, 0)
        )
        longeron2 = Longeron(
            name="右纵梁",
            section=section,
            material=material,
            length=5000,
            start_point=(0, -400, 0),
            end_point=(5000, -400, 0)
        )
        
        cross_beam1 = CrossBeam(
            name="前横梁",
            section=section,
            material=material,
            position=500
        )
        cross_beam2 = CrossBeam(
            name="后横梁",
            section=section,
            material=material,
            position=4500
        )
        
        subframe = Subframe(
            longerons=[longeron1, longeron2],
            cross_beams=[cross_beam1, cross_beam2],
            connectors=[],
            total_length=5000,
            total_width=860,
            total_height=100
        )
        
        assert subframe.get_longeron_count() == 2
        assert subframe.get_cross_beam_count() == 2
    
    def test_validate_cross_beam_positions(self):
        """测试横梁位置验证"""
        section = SectionProperty(
            section_type=SectionType.TUBE_RECT,
            height=100,
            width=50,
            thickness=5
        )
        material = MaterialSpec(
            grade="Q355B",
            yield_strength=355,
            tensile_strength=490
        )
        
        # 横梁位置不按顺序应该失败
        with pytest.raises(ValidationError) as exc_info:
            Subframe(
                longerons=[],
                cross_beams=[
                    CrossBeam(name="后横梁", section=section, material=material, position=2000),
                    CrossBeam(name="前横梁", section=section, material=material, position=500),
                ],
                connectors=[],
                total_length=5000,
                total_width=860,
                total_height=100
            )
        
        assert "顺序" in str(exc_info.value)
    
    def test_get_average_beam_spacing(self):
        """测试计算平均横梁间距"""
        section = SectionProperty(
            section_type=SectionType.TUBE_RECT,
            height=100,
            width=50,
            thickness=5
        )
        material = MaterialSpec(
            grade="Q355B",
            yield_strength=355,
            tensile_strength=490
        )
        
        subframe = Subframe(
            longerons=[],
            cross_beams=[
                CrossBeam(name="横梁1", section=section, material=material, position=0),
                CrossBeam(name="横梁2", section=section, material=material, position=1000),
                CrossBeam(name="横梁3", section=section, material=material, position=2000),
            ],
            connectors=[],
            total_length=5000,
            total_width=860,
            total_height=100
        )
        
        avg_spacing = subframe.get_average_beam_spacing()
        assert avg_spacing == 1000


class TestVanBody:
    """测试厢体模型"""
    
    def test_create_van_body(self):
        """测试创建厢体"""
        body = VanBody(
            body_type="瓦楞板",
            length=6000,
            width=2400,
            height=2500,
            corrugated_depth=20,
            panel_thickness=1.5,
            front_reinforcement=True,
            door_locks=4
        )
        
        assert body.body_type == "瓦楞板"
        assert body.length == 6000
        assert body.width == 2400
        assert body.height == 2500
        assert body.front_reinforcement is True
        assert body.door_locks == 4
    
    def test_calculate_volume(self):
        """测试计算容积"""
        body = VanBody(
            body_type="瓦楞板",
            length=6000,
            width=2400,
            height=2500,
            panel_thickness=1.5
        )
        
        volume = body.calculate_volume()
        expected = 6000 * 2400 * 2500 / 1e9  # 36 m³
        assert abs(volume - expected) < 0.001


class TestTankBody:
    """测试罐体模型"""
    
    def test_create_circular_tank(self):
        """测试创建圆罐"""
        tank = TankBody(
            tank_type="圆罐",
            length=5000,
            diameter=2000,
            head_type="椭圆封头",
            baffle_count=2,
            baffle_spacing=1500
        )
        
        assert tank.tank_type == "圆罐"
        assert tank.diameter == 2000
        assert tank.baffle_count == 2
    
    def test_create_elliptical_tank(self):
        """测试创建椭圆罐"""
        tank = TankBody(
            tank_type="椭圆罐",
            length=5000,
            major_axis=2200,
            minor_axis=1800,
            head_type="碟形封头"
        )
        
        assert tank.tank_type == "椭圆罐"
        assert tank.major_axis == 2200
        assert tank.minor_axis == 1800
    
    def test_calculate_circular_volume(self):
        """测试计算圆罐容积"""
        tank = TankBody(
            tank_type="圆罐",
            length=5000,
            diameter=2000,
            head_type="椭圆封头"
        )
        
        volume = tank.calculate_volume()
        # V = π * r² * L = π * 1² * 5 = 15.7 m³
        expected = math.pi * (1 ** 2) * 5
        assert abs(volume - expected) < 0.1
    
    def test_calculate_elliptical_volume(self):
        """测试计算椭圆罐容积"""
        tank = TankBody(
            tank_type="椭圆罐",
            length=5000,
            major_axis=2200,
            minor_axis=1800,
            head_type="碟形封头"
        )
        
        volume = tank.calculate_volume()
        # V = π * a * b * L = π * 1.1 * 0.9 * 5
        expected = math.pi * 1.1 * 0.9 * 5
        assert abs(volume - expected) < 0.1
    
    def test_discharge_angle_range(self):
        """测试卸料坡度范围"""
        # 超过90度应该失败
        with pytest.raises(ValidationError):
            TankBody(
                tank_type="圆罐",
                length=5000,
                diameter=2000,
                head_type="椭圆封头",
                discharge_angle=100  # 超过90
            )


class TestSuperstructure:
    """测试上装结构模型"""
    
    def test_create_superstructure(self):
        """测试创建上装结构"""
        section = SectionProperty(
            section_type=SectionType.TUBE_RECT,
            height=100,
            width=50,
            thickness=5
        )
        material = MaterialSpec(
            grade="Q355B",
            yield_strength=355,
            tensile_strength=490
        )
        
        subframe = Subframe(
            longerons=[],
            cross_beams=[],
            connectors=[],
            total_length=5000,
            total_width=860,
            total_height=100
        )
        
        van_body = VanBody(
            body_type="瓦楞板",
            length=6000,
            width=2400,
            height=2500,
            panel_thickness=1.5
        )
        
        superstructure = Superstructure(
            vehicle_type="van",
            subframe=subframe,
            body=van_body,
            total_weight=5000,
            center_of_gravity=(2500, 0, 1500)
        )
        
        assert superstructure.vehicle_type == "van"
        assert superstructure.get_body_type() == "van"
    
    def test_get_body_type_tank(self):
        """测试获取罐体类型"""
        tank_body = TankBody(
            tank_type="圆罐",
            length=5000,
            diameter=2000,
            head_type="椭圆封头"
        )
        
        superstructure = Superstructure(
            vehicle_type="tank",
            subframe=Subframe(
                longerons=[],
                cross_beams=[],
                connectors=[],
                total_length=5000,
                total_width=860,
                total_height=100
            ),
            body=tank_body
        )
        
        assert superstructure.get_body_type() == "tank"
    
    def test_get_body_type_none(self):
        """测试无本体时返回None"""
        superstructure = Superstructure(
            vehicle_type="crane",
            subframe=Subframe(
                longerons=[],
                cross_beams=[],
                connectors=[],
                total_length=5000,
                total_width=860,
                total_height=100
            ),
            body=None
        )
        
        assert superstructure.get_body_type() is None
