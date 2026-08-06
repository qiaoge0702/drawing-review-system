# -*- coding: utf-8 -*-
"""Spike：真机验证 3 个 SW 原生视图 API（局部放大/剖视/第一角批量）

核对项（B-M1 验收门禁）：
1. 局部放大草图圆坐标系（图纸 vs 父视图草图）
2. CreateDetailViewAt3 是否需先选中圆（SelectByID2）
3. CreateSectionViewAt4 剖切方向与 ExcludedComponents=None 可接受性

用法：SW 2025 已由老板启动（空启动）→ python spikes/spike_detail_section.py
纪律：OpenDoc 必带 Silent+ReadOnly；不强杀 SW；产物写 temp/，不碰样本资产；
     弹窗卡死 → 停手喊人，禁止自动关窗。
"""
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL = ROOT / "LB26拉臂装置" / "LB26.00000拉臂总成.SLDASM"
TEMPLATE = ROOT / "app" / "resources" / "LB26-template.drwdot"
OUT = ROOT / "temp" / "spike_detail_section"

results = {"checks": {}, "warnings": [], "errors": []}


def main() -> None:
    import win32com.client
    from app.generators.sw_drawing import (
        _open_doc,
        _delete_placeholder_views,
        _try_insert_view,
        _save_as_png,
        create_first_angle_views,
        create_detail_view,
        create_section_view,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    warns = results["warnings"]

    print("[1/7] 附加到运行中的 SolidWorks ...")
    sw = win32com.client.GetActiveObject("SldWorks.Application")
    sw.Visible = True

    print("[2/7] Silent+ReadOnly 打开 LB26 总成 ...")
    model = _open_doc(sw, str(MODEL), read_only=True)
    if model is None:
        results["errors"].append("模型打开失败")
        return

    print("[3/7] 企业模板建图纸 + 删占位视图 + 插父视图 ...")
    drw = sw.NewDocument(str(TEMPLATE), 0, 0, 0)
    if drw is None:
        results["errors"].append("NewDocument 失败")
        return
    _delete_placeholder_views(drw, warns, "spike")
    parent = _try_insert_view(
        drw, str(MODEL), "front", ["*前视", "*Front"], 0.21, 0.16, warns, "spike")
    if parent is None:
        results["errors"].append("父视图插入失败")
        return

    from app.generators.sw_drawing import _mc
    outline = _mc(parent.GetOutline)  # (x1, y1, x2, y2) 米，图纸坐标
    x1, y1, x2, y2 = (float(v) for v in outline[:4])
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    r = min(x2 - x1, y2 - y1) / 4.0
    print(f"      父视图轮廓: x[{x1:.3f},{x2:.3f}] y[{y1:.3f},{y2:.3f}]，"
          f"试验圆心=({cx:.3f},{cy:.3f}) r={r:.3f}")

    print("[4/7] 核对项 1+2：局部放大（先试不选中，失败再选中重试 ≤1 次）...")
    det = create_detail_view(drw, parent, cx, cy, r, 0.34, 0.07, 2.0, warns, "spike")
    results["checks"]["detail_no_preselect"] = det is not None
    if det is None:
        # 尝试 B：先 SelectByID2 选中圆再调 API（重试上限内）
        try:
            name = parent.Name
            drw.ActivateView(name)
            drw.SketchManager.CreateCircleByRadius(cx, cy, 0, r)
            ok = drw.Extension.SelectByID2(
                "", "SKETCHSEGMENT", cx + r, cy, 0, False, 0, None, 0)
            det = drw.CreateDetailViewAt3(0.34, 0.07, 0.0, 1, 2.0, parent)
            results["checks"]["detail_with_preselect"] = det is not None
            results["checks"]["detail_preselect_selectok"] = bool(ok)
        except Exception as e:
            results["checks"]["detail_with_preselect"] = False
            results["warnings"].append(f"局部放大选中重试异常: {e}")

    print("[5/7] 核对项 3：剖视（水平剖切线横穿父视图）...")
    sec = create_section_view(
        drw, parent, [(x1 - 0.01, cy), (x2 + 0.01, cy)], 0.08, 0.06, warns, "spike")
    results["checks"]["section"] = sec is not None

    print("[6/7] 快照 PNG + Create1stAngleViews2 批量视图 ...")
    _save_as_png(drw, str(OUT / "sheet1_detail_section.png"), warns, "spike-1")
    drw2 = sw.NewDocument(str(TEMPLATE), 0, 0, 0)
    if drw2 is not None:
        _delete_placeholder_views(drw2, warns, "spike")
        views = create_first_angle_views(drw2, str(MODEL), warns, "spike")
        results["checks"]["first_angle_batch_count"] = len(views)
        if views:
            _save_as_png(drw2, str(OUT / "sheet2_first_angle.png"), warns, "spike-2")
        sw.CloseDoc(drw2.GetTitle())

    print("[7/7] 清理：不保存关闭文档 ...")
    sw.CloseDoc(drw.GetTitle())
    sw.CloseDoc(model.GetTitle())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        results["errors"].append(traceback.format_exc())
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))
