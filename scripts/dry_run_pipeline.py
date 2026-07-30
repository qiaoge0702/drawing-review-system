"""
M1 门禁验证脚本：流水线空跑

注册全部 8 个占位执行器（Step1/2 也用占位，绕开真实 SW 依赖），
验证状态机、检查点、产物收集、rerun_from 单步重跑链路。

用法: python scripts/dry_run_pipeline.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.generators.pipeline import GeneratePipeline
from app.generators.steps.placeholders import (
    PlaceholderExecutor,
    ViewProjectExecutor,
    DimensionExecutor,
    BomGenerateExecutor,
    TechRequirementExecutor,
    DxfBuildExecutor,
    ReviewExecutor,
)
from app.models.generation import TaskConfig, StepName, PipelineState


async def main() -> int:
    storage = Path(tempfile.mkdtemp(prefix="dry_run_"))
    pipeline = GeneratePipeline(storage_root=storage)

    # 全部 8 步注册占位执行器（Step1/2 用通用占位，绕开 SW 依赖）
    pipeline.register_executor(StepName.SW_LOAD, type("P1", (PlaceholderExecutor,), {"step_name": StepName.SW_LOAD})())
    pipeline.register_executor(StepName.GEOMETRY_PARSE, type("P2", (PlaceholderExecutor,), {"step_name": StepName.GEOMETRY_PARSE})())
    pipeline.register_executor(StepName.VIEW_PROJECT, ViewProjectExecutor())
    pipeline.register_executor(StepName.DIMENSION, DimensionExecutor())
    pipeline.register_executor(StepName.BOM_GENERATE, BomGenerateExecutor())
    pipeline.register_executor(StepName.TECH_REQUIREMENT, TechRequirementExecutor())
    pipeline.register_executor(StepName.DXF_BUILD, DxfBuildExecutor())
    pipeline.register_executor(StepName.REVIEW, ReviewExecutor())

    task_id = "dry-run-001"
    result = await pipeline.run(task_id, "dummy.sldasm", TaskConfig())

    assert result.status == PipelineState.COMPLETED, f"pipeline failed: {result.error}"
    assert len(result.steps) == 8, f"expected 8 steps, got {len(result.steps)}"
    assert result.progress == 100, f"progress={result.progress}"
    assert all(s.is_success for s in result.steps), "some step failed"
    total_artifacts = sum(len(s.artifacts) for s in result.steps)
    assert total_artifacts >= 8, f"expected >=8 artifacts, got {total_artifacts}"
    print(f"[OK] 空跑完成: 8步全部成功, 产物 {total_artifacts} 个")

    # 验证单步重跑（从 Step5 重跑，检查点应保留 1-4）
    result2 = await pipeline.rerun_from(task_id, from_step=5)
    assert result2.status == PipelineState.COMPLETED, f"rerun failed: {result2.error}"
    assert len(result2.steps) == 8
    # 前 4 步应来自检查点（started_at 与首轮一致），Step5 起为新执行
    for s1, s2 in zip(result.steps[:4], result2.steps[:4]):
        assert s1.started_at == s2.started_at, f"step {s2.step} 未复用检查点"
    step5_rerun = next(s for s in result2.steps if s.step == 5)
    step5_first = next(s for s in result.steps if s.step == 5)
    assert step5_rerun.started_at != step5_first.started_at, "step5 未重新执行"
    print("[OK] 单步重跑完成: Step1-4 复用检查点, Step5-8 重新执行")

    print(f"[OK] 存储目录: {storage}")
    print("\n=== M1 门禁『流水线可空跑』验证通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
