# -*- coding: utf-8 -*-
"""SW API 侦察 - 第8步（最终）：变换矩阵手动应用 + 完整2D轮廓提取演示"""
import sys, traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11202轴套.SLDPRT"
TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2025\templates\gb_a3.drwdot"
pythoncom.CoInitialize()

def p(m): print(m, flush=True)

def apply_xform(arr, x, y, z):
    # SW MathTransform: 4x4 行主序 [r00 r01 r02 tx r10 r11 r12 ty r20 r21 r22 tz 0 0 0 scale]
    r = [
        arr[0]*x + arr[1]*y + arr[2]*z + arr[3],
        arr[4]*x + arr[5]*y + arr[6]*z + arr[7],
        arr[8]*x + arr[9]*y + arr[10]*z + arr[11],
    ]
    s = arr[12] if len(arr) > 12 else 1.0
    return [v / s if s and s != 1 else v for v in r]

try:
    sw = win32com.client.Dispatch("SldWorks.Application")
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    sw.OpenDoc6(SRC, 1, 2, "", errors, warnings)
    drw = sw.NewDocument(TPL, 0, 0.0, 0.0)
    view = drw.CreateDrawViewFromModelView3(SRC, "*前视", 0.15, 0.15, 0)
    drw.ForceRebuild3(True)

    comp = view.GetVisibleComponents[0]
    edges = view.GetVisibleEntities2(comp, 1)
    arr = list(view.ModelToViewTransform.ArrayData)
    p(f"变换矩阵维度: {len(arr)}, 视图比例: {view.ScaleDecimal}")

    out = []
    for e in (edges or []):
        curve = e.GetCurve
        cid = getattr(curve, "Identity", "err")
        if cid == 3002:
            circle = curve.CircleParams
            c2d = apply_xform(arr, circle[0], circle[1], circle[2])
            out.append(("circle", round(c2d[0], 4), round(c2d[1], 4), "R=", round(circle[6]*view.ScaleDecimal, 4)))
        elif cid == 3001:
            lp = curve.LineParams
            if lp:
                p1 = apply_xform(arr, lp[0], lp[1], lp[2])
                p2 = apply_xform(arr, lp[3], lp[4], lp[5])
                out.append(("line", [round(v, 4) for v in p1[:2]], [round(v, 4) for v in p2[:2]]))
        else:
            out.append((f"type_{cid}",))
    for o in out:
        p(f"  2D实体: {o}")

    sw.CloseAllDocuments(True)
    p("[DONE]")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
