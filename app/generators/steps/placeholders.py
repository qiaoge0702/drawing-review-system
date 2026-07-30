"""
Step 3-8: 占位执行器（M1 骨架）

M1 阶段仅提供流水线"可空跑"能力：每个占位执行器不实现真实业务逻辑，
仅产出占位标记 + 透传前序步骤摘要，供状态机/检查点/重跑机制端到端验证。

M2 起将逐文件替换为真实执行器：
- step3_view_project.py    视图投影
- step4_dimension.py       尺寸标注
- step5_bom_generate.py    BOM生成
- step6_tech_requirement.py 技术要求
- step7_dxf_build.py       DXF构建
- step8_review.py          审查闭环
"""

import json
import logging
from typing import Dict, Any

from app.generators.models import StepContext
from app.models.generation import StepName

logger = logging.getLogger(__name__)


class PlaceholderExecutor:
    """
    占位执行器基类

    输出: {
        "placeholder": True,
        "step": <步骤编号>,
        "step_name": <步骤名称>,
        "upstream_summary": {前序步骤编号: 状态}
    }
    """

    step_name: StepName  # 子类指定

    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        logger.info(
            f"[Task:{ctx.task_id}] Step {ctx.step} ({self.step_name.value}) "
            f"placeholder executed (no-op)"
        )

        upstream_summary = {
            str(step_num): (data.get("status", "unknown") if isinstance(data, dict) else "unknown")
            for step_num, data in ctx.previous_results.items()
        }

        result: Dict[str, Any] = {
            "placeholder": True,
            "step": ctx.step,
            "step_name": self.step_name.value,
            "upstream_summary": upstream_summary,
        }

        # 落盘占位产物，验证产物收集链路
        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        placeholder_file = output_dir / f"step{ctx.step}_placeholder.json"
        with open(placeholder_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return result


class ViewProjectExecutor(PlaceholderExecutor):
    """Step 3: 视图投影（占位）"""
    step_name = StepName.VIEW_PROJECT


class DimensionExecutor(PlaceholderExecutor):
    """Step 4: 尺寸标注（占位）"""
    step_name = StepName.DIMENSION


class BomGenerateExecutor(PlaceholderExecutor):
    """Step 5: BOM生成（占位）"""
    step_name = StepName.BOM_GENERATE


class TechRequirementExecutor(PlaceholderExecutor):
    """Step 6: 技术要求（占位）"""
    step_name = StepName.TECH_REQUIREMENT


class DxfBuildExecutor(PlaceholderExecutor):
    """Step 7: DXF构建（占位）"""
    step_name = StepName.DXF_BUILD


class ReviewExecutor(PlaceholderExecutor):
    """Step 8: 审查闭环（占位）"""
    step_name = StepName.REVIEW
