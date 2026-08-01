# -*- coding: utf-8 -*-
"""M2 收口修复包2 真机验证

项1：LB26.11202轴套 隐藏线差集提取（线框全集 − HLR 可见集，应得 1 条隐藏线：内孔隐藏圆）
项2：LB26.11000底架焊合 Step2 材料/单重提取（非空率统计）+ Step5 BOM 前几行打印
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.generators.sw_drawing import extract_views_sync
from app.generators.models import StepContext
from app.models.generation import StepName
from app.generators.steps.step2_geometry_parse import GeometryParseExecutor
from app.generators.steps.step5_bom_generate import BomGenerateExecutor

PART = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11202轴套.SLDPRT"
ASM = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11000底架焊合.SLDASM"
WORK = Path(r"E:\147\workspaces\drawing-review-system\temp\integration_test\pkg2_verify")


def p(m):
    print(m, flush=True)


def verify_hidden_lines():
    p("=== 项1：LB26.11202轴套 隐藏线差集（前视）===")
    result = extract_views_sync(PART, ["front"])
    view = result["views"][0]
    n_ent = len(view["entities"])
    hidden = view["hidden_lines"]
    p(f"可见实体: {n_ent}，隐藏线: {len(hidden)} 条")
    for h in hidden:
        p(f"  hidden: {h}")
    if result["warnings"]:
        p(f"warnings: {result['warnings']}")
    ok = len(hidden) == 1
    p(f"[{'PASS' if ok else 'FAIL'}] 差集应得 1 条隐藏边（内孔；会话状态决定 circle 或 line）")
    return ok


async def verify_material_mass():
    p("\n=== 项2：LB26.11000底架焊合 Step2 材料/单重 + Step5 BOM ===")
    ctx2 = StepContext(
        task_id="pkg2-verify", step=2, step_name=StepName.GEOMETRY_PARSE,
        work_dir=WORK / "step_2",
        parameters={"source_file": ASM}, previous_results={},
    )
    r2 = await GeometryParseExecutor()(ctx2)
    bom = r2["bom"]
    active = [b for b in bom if not b["is_suppressed"]]
    n_mat = sum(1 for b in active if b["material"])
    n_mass = sum(1 for b in active if b["mass"] != "")
    p(f"bom 条目: {len(bom)}（非抑制 {len(active)}）")
    p(f"材料非空率: {n_mat}/{len(active)} = {n_mat / max(len(active), 1):.0%}")
    p(f"单重非空率: {n_mass}/{len(active)} = {n_mass / max(len(active), 1):.0%}")
    p(f"total_mass={r2['total_mass']} kg, materials={r2['materials']}")
    if r2.get("warnings"):
        p(f"warnings: {r2['warnings']}")

    ctx5 = StepContext(
        task_id="pkg2-verify", step=5, step_name=StepName.BOM_GENERATE,
        work_dir=WORK / "step_5",
        parameters={"source_file": ASM}, previous_results={2: r2},
    )
    r5 = await BomGenerateExecutor()(ctx5)
    tbl = r5["bom_table"]
    p(f"\n[Step5] BOM rows={len(tbl['rows'])}")
    p("  " + " | ".join(tbl["columns"]))
    for row in tbl["rows"][:10]:
        p("  " + " | ".join(str(c) for c in row))
    ok = n_mat > 0 and n_mass > 0
    p(f"[{'PASS' if ok else 'FAIL'}] 材料/单重非空率应 > 0")
    return ok


ok1 = verify_hidden_lines()
ok2 = asyncio.run(verify_material_mass())
p(f"\n[DONE] hidden={'PASS' if ok1 else 'FAIL'} material_mass={'PASS' if ok2 else 'FAIL'}")
sys.exit(0 if (ok1 and ok2) else 1)
