# -*- coding: utf-8 -*-
"""C2 修复独立复核：读 drawing.dxf，输出 INSUNITS + 各层坐标范围"""
import ezdxf
from collections import defaultdict

doc = ezdxf.readfile(r"temp/integration_test/real_case/step_7/output/drawing.dxf")
print("INSUNITS:", doc.header.get("$INSUNITS"))
layer_pts = defaultdict(list)
for e in doc.modelspace():
    t = e.dxftype()
    pts = []
    if t == "LINE":
        pts = [(e.dxf.start.x, e.dxf.start.y)]
    elif t == "CIRCLE":
        pts = [(e.dxf.center.x, e.dxf.center.y)]
    elif t == "TEXT":
        pts = [(e.dxf.insert.x, e.dxf.insert.y)]
    layer_pts[e.dxf.layer].extend(pts)
for l, pts in layer_pts.items():
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    print(f"{l}: n={len(pts)} x=[{min(xs):.1f},{max(xs):.1f}] y=[{min(ys):.1f},{max(ys):.1f}]")
