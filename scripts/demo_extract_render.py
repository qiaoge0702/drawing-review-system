# -*- coding: utf-8 -*-
"""演示：从 SW 工程图视图提取 2D 实体并渲染为 SVG，老板可直接看效果"""
import sys, os, traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11202轴套.SLDPRT"
TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2025\templates\gb_a3.drwdot"
OUT = r"E:\147\workspaces\drawing-review-system\output\demo"
pythoncom.CoInitialize()

def p(m): print(m, flush=True)

def apply_xform(arr, x, y, z):
    r = [arr[0]*x+arr[1]*y+arr[2]*z+arr[3],
         arr[4]*x+arr[5]*y+arr[6]*z+arr[7],
         arr[8]*x+arr[9]*y+arr[10]*z+arr[11]]
    s = arr[12] if len(arr) > 12 else 1.0
    return [v / s if s and s != 1 else v for v in r]

try:
    sw = win32com.client.Dispatch("SldWorks.Application")
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    sw.OpenDoc6(SRC, 1, 2, "", errors, warnings)
    drw = sw.NewDocument(TPL, 0, 0.0, 0.0)

    all_entities = []
    for vname, label in [("*前视", "主视图"), ("*俯视", "俯视图"), ("*左视", "左视图")]:
        view = drw.CreateDrawViewFromModelView3(SRC, vname, 0.15, 0.15, 0)
        if view is None:
            p(f"[WARN] {label} 插入失败")
            continue
        drw.ForceRebuild3(True)
        comp = view.GetVisibleComponents[0]
        edges = view.GetVisibleEntities2(comp, 1) or []
        arr = list(view.ModelToViewTransform.ArrayData)
        scale = view.ScaleDecimal
        n = 0
        for e in edges:
            curve = e.GetCurve
            cid = getattr(curve, "Identity", None)
            if cid == 3002:
                cp = curve.CircleParams
                c = apply_xform(arr, cp[0], cp[1], cp[2])
                all_entities.append({"view": label, "type": "circle",
                                     "cx": c[0], "cy": c[1], "r": cp[6]*scale})
                n += 1
            elif cid == 3001:
                lp = curve.LineParams
                if lp:
                    p1 = apply_xform(arr, lp[0], lp[1], lp[2])
                    p2 = apply_xform(arr, lp[3], lp[4], lp[5])
                    all_entities.append({"view": label, "type": "line",
                                         "x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                    n += 1
        p(f"[OK] {label}: {n} 个实体 (比例 {scale})")
        drw.ActivateView(view.Name)
        # 删视图换下一个位置（简化：直接都画在同一张 SVG 不同偏移）

    sw.CloseAllDocuments(True)

    # 渲染 SVG
    os.makedirs(OUT, exist_ok=True)
    xs, ys = [], []
    for en in all_entities:
        if en["type"] == "circle":
            xs += [en["cx"]-en["r"], en["cx"]+en["r"]]; ys += [en["cy"]-en["r"], en["cy"]+en["r"]]
        else:
            xs += [en["x1"], en["x2"]]; ys += [en["y1"], en["y2"]]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    W, H, M = 800, 600, 40
    sx = (W-2*M)/(maxx-minx) if maxx > minx else 1
    sy = (H-2*M)/(maxy-miny) if maxy > miny else 1
    s = min(sx, sy)
    def tx(x): return M + (x-minx)*s
    def ty(y): return H - (M + (y-miny)*s)  # Y翻转

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" style="background:#fff">']
    parts.append(f'<text x="{M}" y="25" font-size="16" fill="#333">LB26.11202轴套 - SW API 提取演示（{len(all_entities)} 实体）</text>')
    for en in all_entities:
        if en["type"] == "circle":
            parts.append(f'<circle cx="{tx(en["cx"]):.1f}" cy="{ty(en["cy"]):.1f}" r="{en["r"]*s:.1f}" fill="none" stroke="#000" stroke-width="1.5"/>')
        else:
            parts.append(f'<line x1="{tx(en["x1"]):.1f}" y1="{ty(en["y1"]):.1f}" x2="{tx(en["x2"]):.1f}" y2="{ty(en["y2"]):.1f}" stroke="#000" stroke-width="1.5"/>')
    parts.append('</svg>')
    svg_path = os.path.join(OUT, "demo_轴套_前视图.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    p(f"[DONE] SVG: {svg_path}")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
