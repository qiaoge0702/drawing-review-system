# -*- coding: utf-8 -*-
"""SW API 侦察 - 第4步：全 edge 类型普查（属性方式）+ 圆参数提取"""
import sys, traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.10000底架.SLDASM"
pythoncom.CoInitialize()

def p(m): print(m, flush=True)

# swCurveTypes_e 参考
TYPE_NAMES = {3001: "LINE", 3002: "CIRCLE", 3003: "ELLIPSE", 3004: "INTERSECTION",
              3005: "BCURVE", 3006: "PCURVE", 3007: "SP_CURVE", 3008: "TRIM_CURVE"}

try:
    sw = win32com.client.Dispatch("SldWorks.Application")
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.OpenDoc6(SRC, 2, 2, "", errors, warnings)
    doc.ResolveAllLightWeightComponents(True)
    comps = doc.GetComponents(False)
    custom = [c for c in (comps or []) if c.Name2.startswith("LB26")]

    checked = 0
    for c in custom:
        if checked >= 3:
            break
        mdl = c.GetModelDoc2
        if mdl is None or mdl.GetType != 1:
            continue
        bodies = mdl.GetBodies2(0, True)
        if not bodies:
            continue
        checked += 1
        p(f"--- {c.Name2} ---")
        body = bodies[0]
        edges = body.GetEdges() or []
        hist = {}
        circle_sample = None
        for e in edges:
            curve = e.GetCurve
            try:
                cid = curve.Identity
            except Exception:
                cid = "err"
            tname = TYPE_NAMES.get(cid, str(cid))
            hist[tname] = hist.get(tname, 0) + 1
            if cid == 3002 and circle_sample is None:
                circle_sample = (e, curve)
        p(f"  edge类型分布: {hist} (共{len(edges)})")

        if circle_sample:
            e, curve = circle_sample
            cp = curve.CircleParams
            # CircleParams: (cx, cy, cz, ax, ay, az, radius)
            p(f"  圆参数: 圆心=({cp[0]:.4f},{cp[1]:.4f},{cp[2]:.4f}), 半径={cp[6]:.4f}")
            # 边的参数范围
            try:
                v = e.GetCurveParams2
                p(f"  GetCurveParams2 长度={len(v)}, 前6={v[:6]}")
            except Exception as ex:
                p(f"  GetCurveParams2 ERR: {str(ex)[:80]}")
        else:
            p("  无整圆边")

    sw.CloseAllDocuments(True)
    p("[DONE]")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
