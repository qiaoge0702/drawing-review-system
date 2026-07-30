"""
生成流水线单元测试

使用"底座"（mock执行器）验证流水线核心逻辑，不依赖真实SW环境。
"""

import pytest
import asyncio
import json
from pathlib import Path
from datetime import datetime

from app.generators.pipeline import GeneratePipeline, STEP_CONFIGS
from app.generators.models import StepContext
from app.models.generation import (
    TaskConfig,
    TaskResult,
    StepResult,
    StepStatus,
    StepName,
    PipelineState,
    ArtifactType,
)
from app.core.exceptions import GenerationException, ErrorCode


class MockExecutor:
    """模拟步骤执行器 - 底座"""
    
    def __init__(self, success: bool = True, delay: float = 0.01, output: dict = None):
        self.success = success
        self.delay = delay
        self.output = output or {"mock": True}
        self.call_count = 0
    
    async def __call__(self, ctx: StepContext):
        self.call_count += 1
        await asyncio.sleep(self.delay)
        
        if not self.success:
            raise GenerationException(
                "Mock execution failed",
                error_code=ErrorCode.GEN_STEP_FAILED,
                task_id=ctx.task_id,
                step=ctx.step,
            )
        
        # 保存输出到文件
        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "result.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(self.output, f)
        
        return self.output


class TestGeneratePipeline:
    """生成流水线测试"""
    
    @pytest.fixture
    def pipeline(self, tmp_path):
        """创建测试流水线"""
        storage = tmp_path / "generate_storage"
        return GeneratePipeline(storage_root=storage)
    
    @pytest.fixture
    def mock_executors(self):
        """创建模拟执行器"""
        return {
            StepName.SW_LOAD: MockExecutor(success=True, output={"file_type": "assembly"}),
            StepName.GEOMETRY_PARSE: MockExecutor(success=True, output={"bom": []}),
            StepName.VIEW_PROJECT: MockExecutor(success=True, output={"views": []}),
            StepName.DIMENSION: MockExecutor(success=True, output={"dimensions": []}),
            StepName.BOM_GENERATE: MockExecutor(success=True, output={"bom_table": []}),
            StepName.TECH_REQUIREMENT: MockExecutor(success=True, output={"requirements": []}),
            StepName.DXF_BUILD: MockExecutor(success=True, output={"dxf_path": "test.dxf"}),
            StepName.REVIEW: MockExecutor(success=True, output={"issues": []}),
        }
    
    def test_step_configs_count(self):
        """测试步骤配置数量"""
        assert len(STEP_CONFIGS) == 8
    
    def test_step_configs_order(self):
        """测试步骤配置顺序"""
        expected_names = [
            StepName.SW_LOAD,
            StepName.GEOMETRY_PARSE,
            StepName.VIEW_PROJECT,
            StepName.DIMENSION,
            StepName.BOM_GENERATE,
            StepName.TECH_REQUIREMENT,
            StepName.DXF_BUILD,
            StepName.REVIEW,
        ]
        actual_names = [cfg.name for cfg in STEP_CONFIGS]
        assert actual_names == expected_names
    
    @pytest.mark.asyncio
    async def test_full_pipeline_success(self, pipeline, mock_executors):
        """测试完整流水线成功执行"""
        # 注册模拟执行器
        for step_name, executor in mock_executors.items():
            pipeline.register_executor(step_name, executor)
        
        # 创建测试源文件
        source_file = pipeline.storage_root / "test.SLDASM"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("mock")
        
        config = TaskConfig()
        result = await pipeline.run(
            task_id="test_001",
            source_file=str(source_file),
            config=config,
        )
        
        # 验证结果
        assert result.task_id == "test_001"
        assert result.status == PipelineState.COMPLETED
        assert len(result.steps) == 8
        assert result.progress == 100
        assert result.is_completed is True
        assert result.is_failed is False
        
        # 验证每个步骤都执行了
        for i, step in enumerate(result.steps, 1):
            assert step.step == i
            assert step.status == StepStatus.COMPLETED
            assert step.duration_ms >= 0
        
        # 验证执行器被调用
        for executor in mock_executors.values():
            assert executor.call_count == 1
    
    @pytest.mark.asyncio
    async def test_pipeline_step_failure(self, pipeline):
        """测试步骤失败处理"""
        # 注册一个会失败的执行器
        failing_executor = MockExecutor(success=False)
        pipeline.register_executor(StepName.SW_LOAD, failing_executor)
        
        source_file = pipeline.storage_root / "test.SLDPRT"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("mock")
        
        result = await pipeline.run(
            task_id="test_002",
            source_file=str(source_file),
            config=TaskConfig(),
        )
        
        # 验证失败状态
        assert result.status == PipelineState.ERROR
        assert result.is_failed is True
        assert result.error is not None
        assert "Step 1 failed" in result.error
        
        # 验证只有第一步被执行
        assert len(result.steps) == 1
        assert result.steps[0].status == StepStatus.ERROR
        assert "Mock execution failed" in result.steps[0].error
    
    @pytest.mark.asyncio
    async def test_pipeline_checkpoint(self, pipeline, mock_executors):
        """测试检查点功能"""
        # 第一次执行
        for step_name, executor in mock_executors.items():
            pipeline.register_executor(step_name, executor)
        
        source_file = pipeline.storage_root / "test.SLDASM"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("mock")
        
        result1 = await pipeline.run(
            task_id="test_003",
            source_file=str(source_file),
            config=TaskConfig(),
        )
        
        # 验证检查点存在（完成状态才返回非 None）
        assert pipeline._load_checkpoint("test_003", 1) is not None
        assert pipeline._load_checkpoint("test_003", 8) is not None
        
        # 第二次执行相同任务（应从检查点加载）
        result2 = await pipeline.run(
            task_id="test_003",
            source_file=str(source_file),
            config=TaskConfig(),
        )
        
        # 验证结果一致
        assert result2.status == PipelineState.COMPLETED
        assert len(result2.steps) == 8
        
        # 验证执行器只被调用一次（检查点命中）
        for executor in mock_executors.values():
            assert executor.call_count == 1
    
    @pytest.mark.asyncio
    async def test_rerun_from_step(self, pipeline, mock_executors):
        """测试从指定步骤重跑"""
        for step_name, executor in mock_executors.items():
            pipeline.register_executor(step_name, executor)
        
        source_file = pipeline.storage_root / "test.SLDASM"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("mock")
        
        # 首次执行
        await pipeline.run(
            task_id="test_004",
            source_file=str(source_file),
            config=TaskConfig(),
        )
        
        # 重置执行器计数
        for executor in mock_executors.values():
            executor.call_count = 0
        
        # 从第4步重跑
        result = await pipeline.rerun_from(
            task_id="test_004",
            from_step=4,
        )
        
        # 验证结果
        assert result.status == PipelineState.COMPLETED
        
        # 验证前3步没有重新执行（检查点命中）
        assert mock_executors[StepName.SW_LOAD].call_count == 0
        assert mock_executors[StepName.GEOMETRY_PARSE].call_count == 0
        assert mock_executors[StepName.VIEW_PROJECT].call_count == 0
        
        # 验证第4步及以后重新执行
        assert mock_executors[StepName.DIMENSION].call_count == 1
        assert mock_executors[StepName.DXF_BUILD].call_count == 1
        assert mock_executors[StepName.REVIEW].call_count == 1
    
    @pytest.mark.asyncio
    async def test_task_config_save_load(self, pipeline):
        """测试任务配置保存和加载"""
        config = TaskConfig(
            drawing_type="weldment",
            views=["front", "top"],
            scale="1:2",
        )
        
        pipeline._save_task_config("test_005", config)
        loaded = pipeline._load_task_config("test_005")
        
        assert loaded.drawing_type == config.drawing_type
        assert loaded.views == config.views
        assert loaded.scale == config.scale
    
    def test_guess_artifact_type(self):
        """测试产物类型猜测"""
        assert GeneratePipeline._guess_artifact_type(".json") == ArtifactType.JSON
        assert GeneratePipeline._guess_artifact_type(".svg") == ArtifactType.SVG
        assert GeneratePipeline._guess_artifact_type(".png") == ArtifactType.PNG
        assert GeneratePipeline._guess_artifact_type(".dxf") == ArtifactType.DXF
        assert GeneratePipeline._guess_artifact_type(".txt") == ArtifactType.TXT
        assert GeneratePipeline._guess_artifact_type(".unknown") == ArtifactType.JSON


class TestPipelineEdgeCases:
    """流水线边界情况测试"""
    
    @pytest.fixture
    def pipeline(self, tmp_path):
        """创建测试流水线"""
        storage = tmp_path / "generate_storage"
        return GeneratePipeline(storage_root=storage)
    
    @pytest.mark.asyncio
    async def test_empty_source_file(self, pipeline):
        """测试空源文件路径"""
        # Step1执行器会验证source_file，空路径会在执行时失败
        pipeline.register_executor(StepName.SW_LOAD, MockExecutor())
        
        result = await pipeline.run(
            task_id="test_edge_001",
            source_file="",
            config=TaskConfig(),
        )
        
        # 空路径导致Step1失败
        assert result.status == PipelineState.ERROR
        assert result.is_failed is True
    
    @pytest.mark.asyncio
    async def test_nonexistent_source_file(self, pipeline):
        """测试不存在的源文件"""
        pipeline.register_executor(StepName.SW_LOAD, MockExecutor())
        
        result = await pipeline.run(
            task_id="test_edge_002",
            source_file="/nonexistent/file.SLDPRT",
            config=TaskConfig(),
        )
        
        assert result.status == PipelineState.ERROR
        assert result.is_failed is True
    
    @pytest.mark.asyncio
    async def test_step_timeout(self, pipeline):
        """测试步骤超时"""
        # 创建一个慢执行器（超过Step1的60秒超时）
        slow_executor = MockExecutor(delay=100.0)
        pipeline.register_executor(StepName.SW_LOAD, slow_executor)
        
        source_file = pipeline.storage_root / "test.SLDPRT"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("mock")
        
        result = await pipeline.run(
            task_id="test_edge_003",
            source_file=str(source_file),
            config=TaskConfig(),
        )
        
        assert result.status == PipelineState.ERROR
        assert result.steps[0].status == StepStatus.ERROR
        assert "timeout" in result.steps[0].error.lower()


class TestCheckpointRobustness:
    """检查点健壮性测试（审查 S9 缺口补齐）"""

    @pytest.fixture
    def pipeline(self, tmp_path):
        storage = tmp_path / "generate_storage"
        return GeneratePipeline(storage_root=storage)

    def _make_source(self, pipeline) -> str:
        source_file = pipeline.storage_root / "test.SLDASM"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("mock")
        return str(source_file)

    def _register_all_mock(self, pipeline, **overrides):
        executors = {}
        for cfg in STEP_CONFIGS:
            executors[cfg.name] = overrides.get(cfg.name, MockExecutor(success=True))
            pipeline.register_executor(cfg.name, executors[cfg.name])
        return executors

    @pytest.mark.asyncio
    async def test_failed_step_not_saved_as_checkpoint(self, pipeline):
        """失败步骤不写检查点，重跑时必须重新执行而非加载"""
        failing = MockExecutor(success=False)
        # Step1 retryable=True, max_retries=2 → 首次失败即终止的步骤用 Step2 验证
        executors = self._register_all_mock(
            pipeline, **{StepName.GEOMETRY_PARSE: failing}
        )
        source = self._make_source(pipeline)

        result1 = await pipeline.run("t_fail", source, TaskConfig())
        assert result1.status == PipelineState.ERROR
        assert result1.steps[1].is_failed

        # 失败步骤不应产生可用检查点
        assert pipeline._load_checkpoint("t_fail", 2) is None
        # 成功的前序步骤检查点应存在
        assert pipeline._load_checkpoint("t_fail", 1) is not None

        # 修复执行器后重跑：Step2 必须重新执行，而非被当已完成加载
        fixed = MockExecutor(success=True)
        pipeline.register_executor(StepName.GEOMETRY_PARSE, fixed)
        result2 = await pipeline.run("t_fail", source, TaskConfig())
        assert result2.status == PipelineState.COMPLETED
        assert fixed.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_count_accumulates(self, pipeline):
        """retryable 步骤按 max_retries 重试且计数累加（审查 B5 回归）"""
        failing = MockExecutor(success=False)
        self._register_all_mock(pipeline, **{StepName.SW_LOAD: failing})
        source = self._make_source(pipeline)

        result = await pipeline.run("t_retry", source, TaskConfig())
        assert result.status == PipelineState.ERROR
        # max_retries=2 → 共 3 次尝试
        assert failing.call_count == 3
        assert result.steps[0].execution_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_step_no_retry(self, pipeline):
        """非 retryable 步骤失败不重试"""
        failing = MockExecutor(success=False)
        self._register_all_mock(pipeline, **{StepName.GEOMETRY_PARSE: failing})
        source = self._make_source(pipeline)

        result = await pipeline.run("t_noretry", source, TaskConfig())
        assert result.status == PipelineState.ERROR
        assert failing.call_count == 1

    @pytest.mark.asyncio
    async def test_corrupted_checkpoint_triggers_rerun(self, pipeline):
        """检查点 JSON 损坏时视为无检查点重跑（审查 B6 回归）"""
        executors = self._register_all_mock(pipeline)
        source = self._make_source(pipeline)

        result1 = await pipeline.run("t_corrupt", source, TaskConfig())
        assert result1.status == PipelineState.COMPLETED

        # 损坏 Step3 的检查点
        cp = pipeline.storage_root / "t_corrupt" / "step_3" / "checkpoint.json"
        cp.write_text("{broken json", encoding="utf-8")

        for ex in executors.values():
            ex.call_count = 0
        result2 = await pipeline.run("t_corrupt", source, TaskConfig())
        assert result2.status == PipelineState.COMPLETED
        # Step3 重新执行，Step1/2 复用检查点
        assert executors[StepName.VIEW_PROJECT].call_count == 1
        assert executors[StepName.SW_LOAD].call_count == 0
        assert executors[StepName.GEOMETRY_PARSE].call_count == 0
