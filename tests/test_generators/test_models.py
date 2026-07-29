"""
生成模型单元测试

测试 generation.py 中的数据模型，使用"底座"（mock数据）验证。
"""

import pytest
from datetime import datetime
from pathlib import Path

from app.models.generation import (
    DrawingType,
    StepName,
    StepStatus,
    PipelineState,
    ArtifactType,
    Artifact,
    StepResult,
    TaskConfig,
    TaskResult,
    GenerateSettings,
)


class TestDrawingType:
    """图纸类型枚举测试"""
    
    def test_drawing_type_values(self):
        """测试图纸类型值"""
        assert DrawingType.TOTAL_ASSEMBLY == "total_assembly"
        assert DrawingType.SUB_ASSEMBLY == "sub_assembly"
        assert DrawingType.PART == "part"
        assert DrawingType.WELDMENT == "weldment"


class TestStepName:
    """步骤名称枚举测试"""
    
    def test_step_name_count(self):
        """测试步骤数量"""
        steps = list(StepName)
        assert len(steps) == 8
    
    def test_step_name_order(self):
        """测试步骤顺序"""
        expected = [
            "sw_load",
            "geometry_parse",
            "view_project",
            "dimension",
            "bom_generate",
            "tech_requirement",
            "dxf_build",
            "review",
        ]
        actual = [s.value for s in StepName]
        assert actual == expected


class TestArtifact:
    """产物模型测试"""
    
    def test_create_artifact(self):
        """测试创建产物"""
        artifact = Artifact(
            id="test_001",
            name="assembly.json",
            type=ArtifactType.JSON,
            path="/tmp/test/assembly.json",
            size=1024,
        )
        assert artifact.id == "test_001"
        assert artifact.name == "assembly.json"
        assert artifact.size == 1024
    
    def test_verify_nonexistent_file(self):
        """测试验证不存在的文件"""
        artifact = Artifact(
            id="test_002",
            name="missing.txt",
            type=ArtifactType.TXT,
            path="/nonexistent/file.txt",
            size=100,
        )
        assert artifact.verify() is False
    
    def test_verify_existing_file(self, tmp_path):
        """测试验证存在的文件"""
        test_file = tmp_path / "test.json"
        test_file.write_text('{"test": true}')
        
        artifact = Artifact(
            id="test_003",
            name="test.json",
            type=ArtifactType.JSON,
            path=str(test_file),
            size=test_file.stat().st_size,
        )
        assert artifact.verify() is True
    
    def test_verify_size_mismatch(self, tmp_path):
        """测试文件大小不匹配"""
        test_file = tmp_path / "test.json"
        test_file.write_text('{"test": true}')
        
        artifact = Artifact(
            id="test_004",
            name="test.json",
            type=ArtifactType.JSON,
            path=str(test_file),
            size=99999,  # 错误的大小
        )
        assert artifact.verify() is False


class TestStepResult:
    """步骤结果模型测试"""
    
    def test_create_success_result(self):
        """测试创建成功结果"""
        result = StepResult(
            step=1,
            name=StepName.SW_LOAD,
            status=StepStatus.COMPLETED,
            duration_ms=1500,
            output_data={"name": "test_assembly"},
        )
        assert result.is_success is True
        assert result.is_failed is False
        assert result.step == 1
    
    def test_create_failed_result(self):
        """测试创建失败结果"""
        result = StepResult(
            step=2,
            name=StepName.GEOMETRY_PARSE,
            status=StepStatus.ERROR,
            duration_ms=500,
            error="Parse failed",
        )
        assert result.is_success is False
        assert result.is_failed is True
        assert result.error == "Parse failed"
    
    def test_default_values(self):
        """测试默认值"""
        result = StepResult(
            step=1,
            name=StepName.SW_LOAD,
        )
        assert result.status == StepStatus.PENDING
        assert result.duration_ms == 0
        assert result.artifacts == []
        assert result.execution_count == 0


class TestTaskConfig:
    """任务配置模型测试"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = TaskConfig()
        assert config.drawing_type == DrawingType.WELDMENT
        assert config.target_format == "dxf"
        assert config.views == ["front", "top", "left"]
        assert config.scale == "auto"
        assert config.include_bom is True
        assert config.tolerance_level == "normal"
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = TaskConfig(
            drawing_type=DrawingType.PART,
            views=["front", "section"],
            scale="1:2",
            tolerance_level="precise",
        )
        assert config.drawing_type == DrawingType.PART
        assert config.views == ["front", "section"]
        assert config.scale == "1:2"
    
    def test_invalid_view_type(self):
        """测试无效视图类型"""
        with pytest.raises(ValueError, match="不支持的视图类型"):
            TaskConfig(views=["front", "invalid_view"])
    
    def test_valid_view_types(self):
        """测试有效视图类型"""
        valid_views = ["front", "top", "left", "right", "back", "bottom", "section"]
        config = TaskConfig(views=valid_views)
        assert config.views == valid_views


class TestTaskResult:
    """任务结果模型测试"""
    
    def test_create_task_result(self):
        """测试创建任务结果"""
        result = TaskResult(
            task_id="gen_test_001",
            source_file="/path/to/test.SLDPRT",
        )
        assert result.task_id == "gen_test_001"
        assert result.status == PipelineState.QUEUED
        assert result.progress == 0
        assert result.current_step == 0
    
    def test_completed_task(self):
        """测试已完成任务"""
        result = TaskResult(
            task_id="gen_test_002",
            status=PipelineState.COMPLETED,
            source_file="/path/to/test.SLDPRT",
            total_duration_ms=5000,
        )
        assert result.is_completed is True
        assert result.is_failed is False
        assert result.total_duration_ms == 5000
    
    def test_failed_task(self):
        """测试失败任务"""
        result = TaskResult(
            task_id="gen_test_003",
            status=PipelineState.ERROR,
            source_file="/path/to/test.SLDPRT",
            error="Pipeline failed",
        )
        assert result.is_completed is False
        assert result.is_failed is True
        assert result.error == "Pipeline failed"
    
    def test_task_with_steps(self):
        """测试带步骤结果的任务"""
        steps = [
            StepResult(step=1, name=StepName.SW_LOAD, status=StepStatus.COMPLETED),
            StepResult(step=2, name=StepName.GEOMETRY_PARSE, status=StepStatus.COMPLETED),
        ]
        result = TaskResult(
            task_id="gen_test_004",
            source_file="/path/to/test.SLDPRT",
            steps=steps,
            current_step=2,
            progress=25,
        )
        assert len(result.steps) == 2
        assert result.current_step == 2
        assert result.progress == 25


class TestGenerateSettings:
    """生成设置模型测试"""
    
    def test_default_settings(self):
        """测试默认设置"""
        settings = GenerateSettings()
        assert settings.auto_advance is True
        assert settings.show_intermediate is True
        assert settings.preview_quality == "medium"
        assert settings.max_concurrent_steps == 1
        assert settings.sw_timeout_seconds == 300
    
    def test_custom_settings(self):
        """测试自定义设置"""
        settings = GenerateSettings(
            auto_advance=False,
            preview_quality="high",
            max_concurrent_steps=2,
            cleanup_after_days=14,
        )
        assert settings.auto_advance is False
        assert settings.preview_quality == "high"
        assert settings.max_concurrent_steps == 2
        assert settings.cleanup_after_days == 14
    
    def test_invalid_concurrent_steps(self):
        """测试无效并发数"""
        with pytest.raises(ValueError):
            GenerateSettings(max_concurrent_steps=0)
        
        with pytest.raises(ValueError):
            GenerateSettings(max_concurrent_steps=5)
