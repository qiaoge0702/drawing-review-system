# -*- coding: utf-8 -*-
"""探针：枚举 IDrawingDoc / Extension 的真实方法名"""
import win32com.client as wc, pythoncom, os
pythoncom.CoInitialize()
sw = wc.GetObject(None, "SldWorks.Application")
drw, e = (lambda r: (r[0], r[1] if len(r) > 1 else 0))(sw.OpenDoc6(
    r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.00000拉臂总成.SLDDRW", 3, 1, "", 0, 0)) if isinstance(sw.OpenDoc6(
    r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.00000拉臂总成.SLDDRW", 3, 1, "", 0, 0), tuple) else (sw.OpenDoc6(
    r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.00000拉臂总成.SLDDRW", 3, 1, "", 0, 0), 0)
drwD = wc.CastTo(drw, "IDrawingDoc")
print("IDrawingDoc sheet methods:", [m for m in dir(drwD) if "heet" in m])
print("IDrawingDoc view methods:", [m for m in dir(drwD) if "iew" in m or "Draw" in m])
ext = drw.Extension
print("Extension anno methods:", [m for m in dir(ext) if "nnot" in m or "odel" in m])
sw.CloseDoc(drw.GetTitle() if callable(drw.GetTitle) else drw.GetTitle)
