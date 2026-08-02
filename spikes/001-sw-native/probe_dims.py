# -*- coding: utf-8 -*-
"""探针：遍历零件特征，统计 DisplayDimension 数量及 marked-for-drawing 状态"""
import sys, pythoncom
import win32com.client as wc

def P(x):
    return x() if callable(x) else x

def wrap(disp, ifname, mod):
    if disp is None:
        return None
    kls = getattr(mod, ifname)
    raw = disp._oleobj_.QueryInterface(kls.CLSID, pythoncom.IID_IDispatch)
    return kls(raw)

pythoncom.CoInitialize()
sw = wc.GetObject(None, "SldWorks.Application")
mod = __import__(type(sw).__module__, fromlist=['IDrawingDoc'])

for path in [r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.00001旋转轴.SLDPRT",
             r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.00003隔套.SLDPRT"]:
    r = sw.OpenDoc6(path, 1, 1, "", 0, 0)
    doc = r[0] if isinstance(r, tuple) else r
    if not doc:
        print(path, "OPEN FAIL"); continue
    title = P(doc.GetTitle)
    sw.ActivateDoc3(title, False, 0, 0)
    act = sw.ActiveDoc
    mdoc = wrap(act, 'IModelDoc2', mod)
    feat = wrap(mdoc.FirstFeature(), 'IFeature', mod)
    n_feat, n_dim, n_marked, names = 0, 0, 0, []
    while feat:
        n_feat += 1
        dd = wrap(feat.GetFirstDisplayDimension(), 'IDisplayDimension', mod)
        while dd:
            n_dim += 1
            marked = "?"
            try:
                dim = wrap(dd.GetDimension2(0), 'IDimension', mod)
                for cand in ('IsMarkedForDrawing', 'MarkedForDrawing'):
                    if hasattr(dim, cand):
                        marked = P(getattr(dim, cand)); break
            except Exception as e:
                marked = f"ex"
            if marked is True:
                n_marked += 1
            if len(names) < 10:
                try:
                    names.append((P(dd.GetNameForSelection), marked))
                except Exception:
                    names.append(("?", marked))
            try:
                nxt = feat.GetNextDisplayDimension(dd)
            except Exception:
                nxt = None
            dd = wrap(nxt, 'IDisplayDimension', mod)
        feat = wrap(feat.GetNextFeature(), 'IFeature', mod)
    print(f"{title}: features={n_feat} dims={n_dim} marked={n_marked}")
    print("  sample:", names)
    sw.CloseDoc(title)
print("DONE")
