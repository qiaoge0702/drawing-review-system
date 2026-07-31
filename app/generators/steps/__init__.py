"""
生成步骤执行器

每个步骤是一个独立的执行器，接收 StepContext，返回输出数据。
"""

from app.generators.steps.step1_sw_load import SWLoadExecutor
from app.generators.steps.step2_geometry_parse import GeometryParseExecutor
from app.generators.steps.step3_view_project import ViewProjectExecutor
from app.generators.steps.step4_dimension import DimensionExecutor
from app.generators.steps.step5_bom_generate import BomGenerateExecutor
from app.generators.steps.step6_tech_requirement import TechRequirementExecutor
from app.generators.steps.step7_dxf_build import DxfBuildExecutor
from app.generators.steps.placeholders import ReviewExecutor

__all__ = [
    "SWLoadExecutor",
    "GeometryParseExecutor",
    "ViewProjectExecutor",
    "DimensionExecutor",
    "BomGenerateExecutor",
    "TechRequirementExecutor",
    "DxfBuildExecutor",
    "ReviewExecutor",
]
