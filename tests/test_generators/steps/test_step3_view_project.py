"""
Step 3 视图投影执行器单元测试（STL/trimesh 回退路径已删除，仅保留引擎无关用例）
"""

from pathlib import Path

import pytest

from app.generators.models import StepContext
from app.generators.steps import step3_view_project
from app.generators.steps.step3_view_project import ViewProjectExecutor
from app.models.generation import StepName
from app.core.exceptions import SWException, ErrorCode


def _make_ctx(tmp_path: Path, source_file: Path) -> StepContext:
    return StepContext(
        task_id="test-step3",
        step=3,
        step_name=StepName.VIEW_PROJECT,
        work_dir=tmp_path,
        parameters={"source_file": str(source_file)},
        previous_results={1: {"file_type": "part"}},
    )


class TestViewProjectExecutor:
    @pytest.mark.asyncio
    async def test_sw_unavailable_raises(self, tmp_path, monkeypatch):
        """SW 调用失败必须抛 GEN_SW_NOT_AVAILABLE，禁止静默空数据"""
        async def failing_run_sw(func, *args):
            raise RuntimeError("SW not installed")

        monkeypatch.setattr(step3_view_project, "run_sw", failing_run_sw)
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)

        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE

    @pytest.mark.asyncio
    async def test_unsupported_engine_rejected(self, tmp_path):
        """engine 参数仅支持 sw_api；stl/auto 已删除"""
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        for bad in ("stl", "auto"):
            ctx = _make_ctx(tmp_path, src)
            ctx.parameters["engine"] = bad
            with pytest.raises(SWException) as exc_info:
                await ViewProjectExecutor()(ctx)
            assert exc_info.value.error_code == ErrorCode.GEN_UNSUPPORTED_FEATURE

    @pytest.mark.asyncio
    async def test_missing_source_file(self, tmp_path):
        ctx = _make_ctx(tmp_path, tmp_path / "nonexistent.sldprt")
        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_INVALID_FILE

    def test_scale_computation_rejects_degenerate(self):
        """比例计算输入异常（空视图/零尺寸）→ SWException，禁止静默"""
        from app.generators.steps.step3_view_project import _compute_scale_denominator
        with pytest.raises(SWException):
            _compute_scale_denominator([])
        zero_view = {"name": "front",
                     "bounding_box": {"min_x": 0.0, "min_y": 0.0,
                                      "max_x": 0.0, "max_y": 0.0}}
        with pytest.raises(SWException):
            _compute_scale_denominator([zero_view])
