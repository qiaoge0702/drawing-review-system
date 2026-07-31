# -*- coding: utf-8 -*-
"""Step3 SW API 版 - 真机集成验证（LB26.11202轴套）"""
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.generators.steps.step3_view_project import ViewProjectExecutor
from app.generators.models import StepContext
from app.models.generation import StepName

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11202轴套.SLDPRT"
WORK = Path(r"E:\147\workspaces\drawing-review-system\temp\integration_test\step_3")

async def main():
    WORK.mkdir(parents=True, exist_ok=True)
    ctx = StepContext(
        task_id="integration-sw-api",
        step=3,
        step_name=StepName.VIEW_PROJECT,
        work_dir=WORK,
        parameters={"source_file": SRC, "views": ["front", "top", "left"], "engine": "sw_api"},
    )
    ex = ViewProjectExecutor()
    result = await ex(ctx)
    for v in result["views"]:
        n_line = sum(1 for e in v["entities"] if e["type"] == "line")
        n_circle = sum(1 for e in v["entities"] if e["type"] == "circle")
        n_arc = sum(1 for e in v["entities"] if e["type"] == "arc")
        print(f"[{v['display_name']}] entities={len(v['entities'])} (line={n_line} circle={n_circle} arc={n_arc}) "
              f"hidden={len(v.get('hidden_lines', []))} bbox={v['bounding_box']}")
    if result.get("warnings"):
        print("WARNINGS:", json.dumps(result["warnings"], ensure_ascii=False, indent=2))
    print("views.json:", WORK / "output" / "views.json")

asyncio.run(main())
