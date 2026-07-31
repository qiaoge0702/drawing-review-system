# -*- coding: utf-8 -*-
"""C2 修复后真实案例 Step7 重建 + 图幅内验证"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.generators.models import StepContext
from app.models.generation import StepName
from app.generators.steps.step7_dxf_build import DxfBuildExecutor

WORK = Path(r"E:\147\workspaces\drawing-review-system\temp\integration_test\real_case")

prev = {}
for n, f in [(2, "step_2/output/bom.json"), (3, "step_3/output/views.json"),
             (4, "step_4/output/dimensions.json"), (5, "step_5/output/bom.json")]:
    prev[n] = json.loads((WORK / f).read_text(encoding="utf-8"))

v3 = prev[3]
print("Step3 scale:", [v["scale"] for v in v3["views"]])
print("view_positions:", {k: (round(p["x"], 1), round(p["y"], 1), round(p["width"], 1), round(p["height"], 1))
                          for k, p in v3["layout"]["view_positions"].items()})
print("bbox front:", v3["views"][0]["bounding_box"])


async def run():
    ctx = StepContext(task_id="real-e2e", step=7, step_name=StepName.DXF_BUILD,
                      work_dir=WORK / "step_7", parameters={}, previous_results=prev)
    return await DxfBuildExecutor()(ctx)


r = asyncio.run(run())
print("entity_counts:", r["dxf_structure"]["entity_counts"])

import ezdxf
doc = ezdxf.readfile(str(WORK / "step_7/output/drawing.dxf"))
print("INSUNITS:", doc.header.get("$INSUNITS"))
msp = doc.modelspace()
xs, ys = [], []
for e in msp:
    t = e.dxftype()
    if t == "LINE":
        xs += [e.dxf.start.x, e.dxf.end.x]
        ys += [e.dxf.start.y, e.dxf.end.y]
    elif t in ("CIRCLE", "TEXT"):
        p = e.dxf.center if t == "CIRCLE" else e.dxf.insert
        xs.append(p.x)
        ys.append(p.y)
print("X range:", round(min(xs), 1), "->", round(max(xs), 1))
print("Y range:", round(min(ys), 1), "->", round(max(ys), 1))
print("DXF:", WORK / "step_7/output/drawing.dxf")
