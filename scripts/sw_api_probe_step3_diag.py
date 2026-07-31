# -*- coding: utf-8 -*-
"""根因诊断：LB26.11000 视图坐标为什么 100 米级
逐视图打印：ScaleDecimal / ModelToViewTransform 原始 16 元 /
第一条边 GetCurveParams3 原始坐标（米） vs apply_xform 变换后（毫米）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.generators.sw_drawing import _open_doc
from app.generators.view_extractor import apply_xform

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11000底架焊合.SLDASM"


def main():
    import win32com.client
    sw_app = win32com.client.Dispatch("SldWorks.Application")
    cfg = get_settings().sw
    try:
        doc = _open_doc(sw_app, SRC)
        assert doc is not None, "open failed"
        drw = sw_app.NewDocument(cfg.drawing_template, 0, 0.0, 0.0)
        assert drw is not None, "drawing create failed"
        for name in ("front", "top"):
            sw_view_name = cfg.predefined_view_names[name]
            pos = cfg.view_insert_positions.get(name, [0.15, 0.15])
            view = drw.CreateDrawViewFromModelView3(SRC, sw_view_name, pos[0], pos[1], 0)
            drw.ForceRebuild3(True)
            try:
                drw.ResolveAllLightWeightComponents(True)
            except Exception:
                pass
            arr = list(view.ModelToViewTransform.ArrayData)
            print(f"\n=== {name} ({sw_view_name}) ===")
            print(f"ScaleDecimal={view.ScaleDecimal}")
            print("arr=", [round(a, 6) for a in arr])
            print(f"arr[12](scale elem)={arr[12]}")
            comps = list(view.GetVisibleComponents or [])
            print(f"components={len(comps)}")
            for ci, comp in enumerate(comps[:2]):
                edges = view.GetVisibleEntities2(comp, 1)
                edges = list(edges) if edges else []
                print(f" comp{ci}: edges={len(edges)}")
                for edge in edges[:2]:
                    cp = edge.GetCurveParams3
                    if cp and len(cp) >= 6:
                        p1 = apply_xform(arr, cp[0], cp[1], cp[2])
                        p2 = apply_xform(arr, cp[3], cp[4], cp[5])
                        print(f"  raw(m): ({cp[0]:.4f},{cp[1]:.4f},{cp[2]:.4f}) -> ({cp[3]:.4f},{cp[4]:.4f},{cp[5]:.4f})")
                        print(f"  xform(mm): ({p1[0]*1000:.1f},{p1[1]*1000:.1f}) -> ({p2[0]*1000:.1f},{p2[1]*1000:.1f})")
                # 组件自身变换（装配体关键嫌疑）
                try:
                    ct = comp.Transform2
                    if ct is not None:
                        print(f"  comp{ci} Transform2:", [round(a, 4) for a in list(ct.ArrayData)])
                    else:
                        print(f"  comp{ci} Transform2=None")
                except Exception as e:
                    print(f"  comp{ci} Transform2 err: {e}")
    finally:
        try:
            sw_app.CloseAllDocuments(True)
        except Exception:
            pass


main()
