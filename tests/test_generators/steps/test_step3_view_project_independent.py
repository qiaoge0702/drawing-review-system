"""
Step 3 视图投影执行器 - 独立验证测试（tester 补充，非 dev 自写）

STL/trimesh 回退路径已删除（2026-08-01）；本文件仅保留引擎无关用例：
- 不支持的视图名 → SWException(GEN_UNSUPPORTED_FEATURE)
- 失败路径：run_sw 抛 SWException 原样上抛、views.json 不落盘
- 缺源文件错误上下文（task_id/step）
"""

from pathlib import Path

import pytest

from app.generators.models import StepContext
from app.generators.steps import step3_view_project
from app.generators.steps.step3_view_project import ViewProjectExecutor
from app.models.generation import StepName
from app.core.exceptions import SWException, ErrorCode


def _make_ctx(tmp_path: Path, source_file: Path, views=None) -> StepContext:
    params = {"source_file": str(source_file)}
    if views is not None:
        params["views"] = views
    return StepContext(
        task_id="test-step3-ind",
        step=3,
        step_name=StepName.VIEW_PROJECT,
        work_dir=tmp_path,
        parameters=params,
        previous_results={},
    )


class TestUnsupportedViews:
    @pytest.mark.asyncio
    async def test_unsupported_view_raises_gen_unsupported_feature(self, tmp_path):
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src, views=["front", "iso"])

        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_UNSUPPORTED_FEATURE

    @pytest.mark.asyncio
    async def test_case_sensitive_view_name_rejected(self, tmp_path):
        """"Front"（大写）不在允许集合内，必须抛错而非静默忽略"""
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src, views=["Front"])

        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_UNSUPPORTED_FEATURE


class TestFailurePaths:
    @pytest.mark.asyncio
    async def test_sw_exception_from_export_propagates(self, tmp_path, monkeypatch):
        """run_sw 抛出 SWException（如 SW 未安装）时必须原样上抛，不得改写为空数据"""
        async def failing_run_sw(func, *args):
            raise SWException(
                "SW unavailable", error_code=ErrorCode.GEN_SW_NOT_AVAILABLE
            )

        monkeypatch.setattr(step3_view_project, "run_sw", failing_run_sw)
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)

        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE
        # 失败路径禁止落盘契约 JSON（避免下游拿到残缺产物）
        assert not (tmp_path / "output" / "views.json").exists()

    @pytest.mark.asyncio
    async def test_missing_source_file_error_carries_context(self, tmp_path):
        ctx = _make_ctx(tmp_path, tmp_path / "nope.sldprt")
        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        err = exc_info.value
        assert err.error_code == ErrorCode.GEN_INVALID_FILE
        assert getattr(err, "task_id", None) == "test-step3-ind"
        assert getattr(err, "step", None) == 3
