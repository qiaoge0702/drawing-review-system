# -*- coding: utf-8 -*-
"""SW API 侦察 - 第7步（收尾）：视图实体→2D坐标 端到端提取验证"""
import sys, traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11202轴套.SLDPRT"
TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2025\templates\gb_a3.drwdot"
pythoncom.CoInitialize()

def p(m): print(m, flush=True)

TYPE_NAMES = {3001: "LINE", 3002: "CIRCLE", 3003: "ELLIPSE", 3004: "INTERSECTION",
              3005: "BCURVE", 3006: "PCURVE", 3007: "SP_CURVE", 3008: "TRIM_CURVE"}

try:
    sw = win32com.client.Dispatch("SldWorks.Application")
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    sw.OpenDoc6(SRC, 1, 2, "", errors, warnings)
    drw = sw.NewDocument(TPL, 0, 0.0, 0.0)
    view = drw.CreateDrawViewFromModelView3(SRC, "*前视", 0.15, 0.15, 0)
    drw.ForceRebuild3(True)

    comp = view.GetVisibleComponents[0]
    edges = view.GetVisibleEntities2(comp, 1)  # silhouette edges
    p(f"silhouette edges: {len(edges) if edges else 0}")

    # 视图变换：3D模型坐标 → 视图2D坐标
    xform = view.ModelToViewTransform
    p(f"ModelToViewTransform: {xform is not None}")
    mu = sw.GetMathUtility
    p(f"MathUtility: {mu is not None}")

    hist = {}
    sample_done = False
    for e in (edges or []):
        curve = e.GetCurve
        try:
            cid = curve.Identity
        except Exception:
            cid = "err"
        tname = TYPE_NAMES.get(cid, str(cid))
        hist[tname] = hist.get(tname, 0) + 1

        if not sample_done and cid in (3001, 3002):
            sample_done = True
            try:
                # 边参数范围并采样中点，做 3D→2D 变换验证
                cp = e.GetCurveParams3
                p(f"  样本边[{tname}] GetCurveParams3: {str(cp)[:140]}")
                if cid == 3002:
                    circle = curve.CircleParams
                    p(f"  圆心=({circle[0]:.4f},{circle[1]:.4f},{circle[2]:.4f}) R={circle[6]:.4f}")
                    pt = mu.CreatePoint([circle[0], circle[1], circle[2]])
                    pt2 = pt.MultiplyTransform(xform)
                    arr = pt2.ArrayData
                    p(f"  变换后2D坐标: ({arr[0]:.4f}, {arr[1]:.4f})  [图纸坐标,米]")
            except Exception as ex:
                p(f"  [ERR] 样本提取: {str(ex)[:120]}")
    p(f"edge 类型分布: {hist}")

    # 比例尺与单位
    p(f"视图比例: {view.ScaleDecimal}")

    sw.CloseAllDocuments(True)
    p("[DONE]")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
