# -*- coding: utf-8 -*-
"""诊断探针：捕获 3 个新 API 的真实 COM 异常（只诊断，不改方案）"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL = ROOT / "LB26拉臂装置" / "LB26.00000拉臂总成.SLDASM"
TEMPLATE = ROOT / "app" / "resources" / "LB26-template.drwdot"


def show(label, fn):
    try:
        r = fn()
        print(f"[{label}] OK -> {r!r}")
        return r
    except Exception as e:
        print(f"[{label}] FAIL -> {e!r}")
        return None


def main():
    import win32com.client
    from app.generators.sw_drawing import (
        _open_doc, _delete_placeholder_views, _try_insert_view, _mc)

    sw = win32com.client.GetActiveObject("SldWorks.Application")
    model = _open_doc(sw, str(MODEL), read_only=True)
    drw = sw.NewDocument(str(TEMPLATE), 0, 0, 0)
    w = []
    _delete_placeholder_views(drw, w, "probe")
    parent = _try_insert_view(
        drw, str(MODEL), "front", ["*前视", "*Front"], 0.21, 0.16, w, "probe")
    print("parent:", parent is not None, "warnings:", w)
    outline = _mc(parent.GetOutline)
    x1, y1, x2, y2 = (float(v) for v in outline[:4])
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    r = min(x2 - x1, y2 - y1) / 4

    # API 1: Create1stAngleViews2（新图纸上试，避免与已有视图干扰）
    drw2 = sw.NewDocument(str(TEMPLATE), 0, 0, 0)
    _delete_placeholder_views(drw2, [], "probe")
    show("Create1stAngleViews2",
         lambda: drw2.Create1stAngleViews2(str(MODEL)))

    # API 2（官方 9 参签名已拿到）：CreateDetailViewAt3(X,Y,Z,Style,Scale1,Scale2,Label,Showtype,FullOutline)
    drw.ActivateView(parent.Name)
    show("CreateCircleByRadius",
         lambda: drw.SketchManager.CreateCircleByRadius(cx, cy, 0, r))
    det = show("CreateDetailViewAt3_9arg",
               lambda: drw.CreateDetailViewAt3(0.34, 0.07, 0.0, 0, 2.0, 1.0, "A", 1, False))
    print("detail view created:", det is not None)

    # API 3（官方签名已拿到）：6 参 + 调用前选中剖切线
    # CreateSectionViewAt4(X, Y, Z, SectionLabel:str, Options:int, ExcludedComponents:obj)
    seg = show("CreateLine",
               lambda: drw.SketchManager.CreateLine(x1 - 0.01, cy, 0.0, x2 + 0.01, cy, 0.0))
    sel = show("SelectByID2_line",
               lambda: drw.Extension.SelectByID2(
                   "", "SKETCHSEGMENT", cx, cy, 0, False, 0, None, 0))
    show("CreateSectionViewAt4_6arg_none",
         lambda: drw.CreateSectionViewAt4(0.08, 0.06, 0.0, "A", 0, None))
    show("CreateSectionViewAt4_6arg_emptytuple",
         lambda: drw.CreateSectionViewAt4(0.08, 0.10, 0.0, "B", 0, ()))
    import pythoncom
    empty_arr = win32com.client.VARIANT(
        pythoncom.VT_BYREF | pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [])
    show("CreateSectionViewAt4_6arg_emptyarray",
         lambda: drw.CreateSectionViewAt4(0.08, 0.14, 0.0, "C", 0, empty_arr))
    print("select_ok:", sel)

    # 探针信息：drw 类型与相关方法存在性
    print("drw type:", type(drw))
    for m in ("Create1stAngleViews2", "CreateDetailViewAt3", "CreateSectionViewAt4",
              "CreateDetailViewAt4", "CreateSectionViewAt5"):
        print(f"  has {m}:", hasattr(drw, m))

    try:
        sw.CloseDoc(_mc(drw2.GetTitle))
    except Exception:
        pass
    try:
        sw.CloseDoc(_mc(drw.GetTitle))
    except Exception:
        pass
    try:
        sw.CloseDoc(_mc(model.GetTitle))
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
