# -*- coding: utf-8 -*-
"""SW API 侦察 - 第3步：edge 曲线类型识别修复 + 圆参数提取"""
import sys, traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.10000底架.SLDASM"
pythoncom.CoInitialize()

def p(m): print(m, flush=True)

try:
    sw = win32com.client.Dispatch("SldWorks.Application")
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.OpenDoc6(SRC, 2, 2, "", errors, warnings)
    doc.ResolveAllLightWeightComponents(True)
    comps = doc.GetComponents(False)
    custom = [c for c in (comps or []) if c.Name2.startswith("LB26")]

    # 取轴套（有圆柱面，必有圆边）
    target = None
    for c in custom:
        if "轴套" in c.Name2:
            mdl = c.GetModelDoc2
            if mdl is not None and mdl.GetType == 1:
                target = (c.Name2, mdl)
                break
    name, part = target
    p(f"目标零件: {name}")
    body = part.GetBodies2(0, True)[0]
    edges = body.GetEdges()
    p(f"edges: {len(edges)}")

    e0 = edges[0]
    curve = e0.GetCurve

    # 尝试1: 方法调用带括号
    for label, fn in [
        ("curve.Identity()", lambda: curve.Identity()),
        ("curve.Identity 属性", lambda: curve.Identity),
        ("e0.GetCurveParams2", lambda: e0.GetCurveParams2),
        ("curve.CircleParams", lambda: curve.CircleParams),
        ("curve.IsCircle", lambda: curve.IsCircle()),
        ("curve.IsLine", lambda: curve.IsLine()),
        ("curve.LineParams", lambda: curve.LineParams),
    ]:
        try:
            r = fn()
            p(f"[OK] {label}: {str(r)[:120]}")
        except Exception as ex:
            p(f"[ERR] {label}: {str(ex)[:100]}")

    # 尝试 start/end point
    try:
        sp = e0.GetStartVertex
        ep = e0.GetEndVertex
        p(f"[OK] StartVertex: {sp.GetPoint}, EndVertex: {ep.GetPoint}")
    except Exception as ex:
        p(f"[ERR] vertices: {str(ex)[:100]}")

    sw.CloseAllDocuments(True)
    p("[DONE]")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
