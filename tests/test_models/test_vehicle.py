"""
车辆模型单元测试
"""

import pytest
from pydantic import ValidationError

from app.models.vehicle import VehicleType, VehicleInfo, ChassisParams


class TestVehicleType:
    """测试车型枚举"""
    
    def test_vehicle_type_values(self):
        """测试车型枚举值"""
        assert VehicleType.VAN == "van"
        assert VehicleType.TANK == "tank"
        assert VehicleType.CRANE == "crane"
        assert VehicleType.COMPACTOR == "compactor"
        assert VehicleType.MIXER == "mixer"
        assert VehicleType.DUMP == "dump"
        assert VehicleType.WRECKER == "wrecker"
        assert VehicleType.AERIAL == "aerial"
    
    def test_get_display_name(self):
        """测试获取显示名称"""
        assert VehicleType.get_display_name(VehicleType.VAN) == "厢式运输车"
        assert VehicleType.get_display_name(VehicleType.TANK) == "罐式运输车"
        assert VehicleType.get_display_name(VehicleType.CRANE) == "随车起重运输车"
    
    def test_get_recognition_features(self):
        """测试获取识别特征"""
        van_features = VehicleType.get_recognition_features(VehicleType.VAN)
        assert "corrugated_lines" in van_features
        assert "door_frame" in van_features
        
        tank_features = VehicleType.get_recognition_features(VehicleType.TANK)
        assert "circular_arc" in tank_features
        assert "head_curve" in tank_features


class TestVehicleInfo:
    """测试车辆信息模型"""
    
    def test_create_vehicle_info(self):
        """测试创建车辆信息"""
        vehicle = VehicleInfo(
            vehicle_type=VehicleType.VAN,
            vehicle_type_name="厢式运输车"
        )
        
        assert vehicle.vehicle_type == VehicleType.VAN
        assert vehicle.vehicle_type_name == "厢式运输车"
    
    def test_auto_set_vehicle_type_name(self):
        """测试自动设置车型显示名称"""
        vehicle = VehicleInfo(
            vehicle_type=VehicleType.TANK,
            vehicle_type_name=""  # 空值应该被自动设置
        )
        
        # 验证器会在验证时自动设置
        # 注意：这里需要实际调用验证
        vehicle = VehicleInfo.model_validate({
            "vehicle_type": "tank",
            "vehicle_type_name": ""
        })
        assert vehicle.vehicle_type_name == "罐式运输车"
    
    def test_optional_fields(self):
        """测试可选字段"""
        vehicle = VehicleInfo(
            vehicle_type=VehicleType.CRANE,
            vehicle_type_name="随车起重运输车",
            chassis_brand="解放",
            chassis_model="CA1250",
            wheelbase=4500,
            total_mass=25000
        )
        
        assert vehicle.chassis_brand == "解放"
        assert vehicle.chassis_model == "CA1250"
        assert vehicle.wheelbase == 4500
        assert vehicle.total_mass == 25000
    
    def test_invalid_wheelbase(self):
        """测试无效轴距"""
        with pytest.raises(ValidationError) as exc_info:
            VehicleInfo(
                vehicle_type=VehicleType.VAN,
                vehicle_type_name="厢式运输车",
                wheelbase=-100  # 负数应该失败
            )
        
        assert "wheelbase" in str(exc_info.value)


class TestChassisParams:
    """测试底盘参数模型"""
    
    def test_create_chassis_params(self):
        """测试创建底盘参数"""
        params = ChassisParams(
            wheelbase=4500,
            track_front=1800,
            track_rear=1800,
            frame_width=860,
            frame_height=280,
            max_load=25000
        )
        
        assert params.wheelbase == 4500
        assert params.track_front == 1800
        assert params.track_rear == 1800
        assert params.frame_width == 860
        assert params.frame_height == 280
        assert params.max_load == 25000
    
    def test_optional_fields(self):
        """测试可选字段"""
        params = ChassisParams(
            wheelbase=4500,
            track_front=1800,
            track_rear=1800,
            frame_width=860,
            frame_height=280,
            max_load=25000,
            frame_thickness=8,
            front_overhang=1200,
            rear_overhang=1800
        )
        
        assert params.frame_thickness == 8
        assert params.front_overhang == 1200
        assert params.rear_overhang == 1800
    
    def test_validate_track_consistency(self):
        """测试轮距一致性验证"""
        # 轮距差异过大应该失败
        with pytest.raises(ValidationError) as exc_info:
            ChassisParams(
                wheelbase=4500,
                track_front=1800,
                track_rear=2500,  # 差异700mm，超过500mm限制
                frame_width=860,
                frame_height=280,
                max_load=25000
            )
        
        assert "轮距" in str(exc_info.value)
    
    def test_valid_track_difference(self):
        """测试有效轮距差异"""
        # 轮距差异在允许范围内
        params = ChassisParams(
            wheelbase=4500,
            track_front=1800,
            track_rear=2000,  # 差异200mm，在500mm限制内
            frame_width=860,
            frame_height=280,
            max_load=25000
        )
        
        assert params.track_rear == 2000
    
    def test_calculate_approximate_length(self):
        """测试估算整车长度"""
        params = ChassisParams(
            wheelbase=4500,
            track_front=1800,
            track_rear=1800,
            frame_width=860,
            frame_height=280,
            max_load=25000,
            front_overhang=1200,
            rear_overhang=1800
        )
        
        length = params.calculate_approximate_length()
        assert length == 7500  # 4500 + 1200 + 1800
    
    def test_calculate_length_without_overhang(self):
        """测试无悬长时的长度计算"""
        params = ChassisParams(
            wheelbase=4500,
            track_front=1800,
            track_rear=1800,
            frame_width=860,
            frame_height=280,
            max_load=25000
        )
        
        length = params.calculate_approximate_length()
        assert length == 4500  # 只有轴距
    
    def test_negative_values_rejected(self):
        """测试拒绝负值"""
        with pytest.raises(ValidationError):
            ChassisParams(
                wheelbase=-100,
                track_front=1800,
                track_rear=1800,
                frame_width=860,
                frame_height=280,
                max_load=25000
            )
    
    def test_zero_values_rejected(self):
        """测试拒绝零值"""
        with pytest.raises(ValidationError):
            ChassisParams(
                wheelbase=0,  # gt=0 要求必须大于0
                track_front=1800,
                track_rear=1800,
                frame_width=860,
                frame_height=280,
                max_load=25000
            )
