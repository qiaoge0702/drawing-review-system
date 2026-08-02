# -*- coding: utf-8 -*-
"""单步执行器：逐步骤跑 LB26 端到端，每步可人工确认。
用法: python scripts/run_single_step.py <step_num 2-7>
previous_results 从 temp/integration_test/real_case/step_*/output/*.json 重建。
"""
import asyncio
import json
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

STEPS = {
    2: (StepName.GEOMETRY_PARSE, GeometryParseExecutor),
    3: (StepName.VIEW_PROJECT, ViewProjectExecutor),
    4: (StepName.DIMENSION, DimensionExecutor),
    5: (StepName.BOM_GENERATE, BomGenerateExecutor),
    6: (StepName.TECH_REQUIREMENT, TechRequirementExecutor),
    7: (StepName.DXF_BUILD, DxfBuildExecutor),
}
# 每步的产物文件名（用于重建 previous_results）
ARTIFACT = {2: "bom.json", 3: "views.json", 4: "dimensions.json",
            5: "bom.json", 6: "tech_requirements.json", 7: "drawing.dxf"}


def load_prev(upto: int) -> dict:
    prev = {}
    for n in range(2, upto):
        f = WORK / f"step_{n}" / "output" / ARTIFACT[n]
        if n == 7 or not f.exists():
            continue
        prev[n] = json.loads(f.read_text(encoding="utf-8"))
    return prev


async def run(step: int):
    name, cls = STEPS[step]
    ctx = StepContext(
        task_id="real-e2e",
        step=step,
        step_name=name,
        work_dir=WORK / f"step_{step}",
        parameters={"source_file": SRC, "views": ["front", "top", "left"],
                    "engine": "sw_api"},
        previous_results=load_prev(step),
    )
    r = await cls()(ctx)
    print(f"\n[Step{step}] DONE keys={list(r.keys())}")
    if step == 2:
        bom = r.get("bom", [])
        mats = r.get("materials", {})
        print(f"  bom={len(bom)} materials={len(mats)} total_mass={r.get('total_mass')}")
    elif step == 3:
        for v in r["views"]:
            print(f"  {v.get('view_name')}: entities={len(v.get('entities', []))} "
                  f"hidden={len(v.get('hidden_lines', []))} scale={v.get('scale')} "
                  f"bbox={v.get('bounding_box')}")
        print(f"  layout={json.dumps(r.get('layout'), ensure_ascii=False)}")
        if r.get("warnings"):
            print(f"  warnings={r['warnings']}")
    elif step == 4:
        print(f"  dims={len(r['dimensions'])} score={r.get('placement_score')}")
    elif step == 5:
        bt = r["bom_table"]
        print(f"  rows={len(bt['rows'])} pos={bt['position']}")
        if r.get("warnings"):
            print(f"  warnings={r['warnings']}")
    elif step == 6:
        print(f"  template={r['tech_requirements']['template_id']}")
    elif step == 7:
        print(f"  counts={r['dxf_structure']['entity_counts']}")


if __name__ == "__main__":
    asyncio.run(run(int(sys.argv[1])))
