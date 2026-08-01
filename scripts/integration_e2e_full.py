# -*- coding: utf-8 -*-
"""LB26.11000 底架焊合 — M2 端到端全链验证（Step2→7）

产物: temp/integration_test/real_case/step_*/output/
最终: step_7/output/drawing.dxf（老板用 SW 打开目视验收）
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.generators.models import StepContext
from app.models.generation import StepName
from app.generators.steps.step2_geometry_parse import GeometryParseExecutor
from app.generators.steps.step3_view_project import ViewProjectExecutor
from app.generators.steps.step4_dimension import DimensionExecutor
from app.generators.steps.step5_bom_generate import BomGenerateExecutor
from app.generators.steps.step6_tech_requirement import TechRequirementExecutor
from app.generators.steps.step7_dxf_build import DxfBuildExecutor

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11000底架焊合.SLDASM"
WORK = Path(r"E:\147\workspaces\drawing-review-system\temp\integration_test\real_case")

STEPS = [
    (2, StepName.GEOMETRY_PARSE, GeometryParseExecutor),
    (3, StepName.VIEW_PROJECT, ViewProjectExecutor),
    (4, StepName.DIMENSION, DimensionExecutor),
    (5, StepName.BOM_GENERATE, BomGenerateExecutor),
    (6, StepName.TECH_REQUIREMENT, TechRequirementExecutor),
    (7, StepName.DXF_BUILD, DxfBuildExecutor),
]


async def main():
    prev: dict = {}
    for num, name, cls in STEPS:
        ctx = StepContext(
            task_id="real-e2e",
            step=num,
            step_name=name,
            work_dir=WORK / f"step_{num}",
            parameters={"source_file": SRC, "views": ["front", "top", "left"], "engine": "sw_api"},
            previous_results=prev,
        )
        r = await cls()(ctx)
        prev[num] = r
        if num == 2:
            mats = sum(1 for b in r["bom"] if b.get("material"))
            print(f"[Step2] bom={len(r['bom'])} 材料非空={mats} total_mass={r['total_mass']}")
        elif num == 3:
            for v in r["views"]:
                print(f"[Step3] {v['display_name']}: entities={len(v['entities'])} "
                      f"hidden={len(v.get('hidden_lines', []))} scale={v.get('scale')}")
        elif num == 4:
            print(f"[Step4] dims={len(r['dimensions'])} score={r['placement_score']} overlaps={len(r['overlaps'])}")
        elif num == 5:
            rows = r["bom_table"]["rows"]
            filled = sum(1 for row in rows if row[4] != "")
            print(f"[Step5] rows={len(rows)} 材料填充={filled} pos={r['bom_table']['position']}")
        elif num == 6:
            print(f"[Step6] template={r['tech_requirements']['template_id']} "
                  f"lines={len(r['tech_requirements']['content'])}")
        elif num == 7:
            print(f"[Step7] entity_counts={r['dxf_structure']['entity_counts']}")
    print(f"\nDXF: {WORK / 'step_7/output/drawing.dxf'}")


asyncio.run(main())
