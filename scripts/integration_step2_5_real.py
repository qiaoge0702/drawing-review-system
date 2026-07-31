# -*- coding: utf-8 -*-
"""Step2→5 真机集成验证（LB26.11000底架焊合.SLDASM）

串跑真实流水线：Step2 几何/BOM 提取（SW COM）→ Step3 视图投影（SW API）
→ Step4 尺寸标注 → Step5 BOM 生成，产物落 temp/integration_test/real_case/。
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

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11000底架焊合.SLDASM"
WORK = Path(r"E:\147\workspaces\drawing-review-system\temp\integration_test\real_case")


def make_ctx(step: int, step_name: StepName, prev: dict) -> StepContext:
    return StepContext(
        task_id="integration-real-case",
        step=step,
        step_name=step_name,
        work_dir=WORK / f"step_{step}",
        parameters={"source_file": SRC, "views": ["front", "top", "left"], "engine": "sw_api"},
        previous_results=prev,
    )


async def main():
    prev: dict = {}

    # Step2: 几何解析 + BOM 提取
    ctx2 = make_ctx(2, StepName.GEOMETRY_PARSE, prev)
    r2 = await GeometryParseExecutor()(ctx2)
    prev[2] = r2
    bom = r2["bom"]
    print(f"[Step2] bom items={len(bom)}, summary={r2['bom_summary']}, total_mass={r2['total_mass']}")

    # Step3: 视图投影
    ctx3 = make_ctx(3, StepName.VIEW_PROJECT, prev)
    r3 = await ViewProjectExecutor()(ctx3)
    prev[3] = r3
    for v in r3["views"]:
        kinds = {}
        for e in v["entities"]:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
        print(f"[Step3] {v['display_name']}: entities={len(v['entities'])} {kinds} "
              f"bbox=({v['bounding_box']['min_x']},{v['bounding_box']['min_y']})-"
              f"({v['bounding_box']['max_x']},{v['bounding_box']['max_y']})")
    if r3.get("warnings"):
        print(f"[Step3] warnings: {len(r3['warnings'])} 条（隐藏线/采样问题，详见 views.json）")

    # Step4: 尺寸标注
    ctx4 = make_ctx(4, StepName.DIMENSION, prev)
    r4 = await DimensionExecutor()(ctx4)
    prev[4] = r4
    kinds = {}
    for d in r4["dimensions"]:
        kinds[d["type"]] = kinds.get(d["type"], 0) + 1
    print(f"[Step4] dimensions={len(r4['dimensions'])} {kinds}, "
          f"placement_score={r4['placement_score']}, overlaps={len(r4['overlaps'])}")

    # Step5: BOM 生成
    ctx5 = make_ctx(5, StepName.BOM_GENERATE, prev)
    r5 = await BomGenerateExecutor()(ctx5)
    prev[5] = r5
    tbl = r5["bom_table"]
    print(f"[Step5] BOM rows={len(tbl['rows'])} (source {r5['source_total_items']} items)")
    print("  columns:", tbl["columns"])
    for row in tbl["rows"]:
        print("   ", row)

    print(f"\n产物目录: {WORK}")
    print("  step_3/output/views.json  step_4/output/dimensions.json  step_5/output/bom.json")


asyncio.run(main())
