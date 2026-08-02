# -*- coding: utf-8 -*-
"""Step3 原子子步执行器（诊断用）：每个 COM 阶段停下等回车。
用法: python scripts/step3_substeps.py  然后按提示逐步 Enter。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.generators.sw_drawing import _open_doc, _read_edges_with_hidden_diff, _read_edges
from app.generators.view_extractor import extract_view_entities

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11000底架焊合.SLDASM"


def pause(tag: str):
    print(f"\n===== [{tag}] 完成，等待指令（Enter 继续）=====", flush=True)
    sys.stdin.readline()


def main():
    import win32com.client
    cfg = get_settings().sw

    print("[S1] Dispatch SW ...", flush=True)
    sw = win32com.client.Dispatch("SldWorks.Application")
    sw.Visible = True
    print(f"[S1] SW connected, Visible=True", flush=True)
    doc = _open_doc(sw, SRC)
    print(f"[S1] 装配体已打开: {doc is not None}", flush=True)
    pause("S1 连接+打开装配")

    print(f"[S2] NewDocument(template={cfg.drawing_template!r}) ...", flush=True)
    drw = sw.NewDocument(cfg.drawing_template, 0, 0.0, 0.0)
    print(f"[S2] 工程图已创建: {drw is not None}", flush=True)
    pause("S2 新建工程图")

    for name in ("front", "top", "left"):
        sw_view_name = cfg.predefined_view_names[name]
        pos = cfg.view_insert_positions.get(name, [0.15, 0.15])
        print(f"[S3-{name}] CreateDrawViewFromModelView3({sw_view_name!r}) ...", flush=True)
        view = drw.CreateDrawViewFromModelView3(SRC, sw_view_name, pos[0], pos[1], 0)
        print(f"[S3-{name}] 视图已插入: {view is not None}", flush=True)
        drw.ForceRebuild3(True)
        print(f"[S3-{name}] ForceRebuild3 完成", flush=True)
        try:
            drw.ResolveAllLightWeightComponents(True)
            print(f"[S3-{name}] ResolveAllLightWeightComponents 完成", flush=True)
        except Exception as e:
            print(f"[S3-{name}] Resolve skipped: {e}", flush=True)
        comps = view.GetVisibleComponents or []
        print(f"[S3-{name}] GetVisibleComponents: {len(comps)} 个组件", flush=True)
        pause(f"S3-{name} 插入+解析")

        print(f"[S3-{name}-read] 读边（含隐藏线差集）...", flush=True)
        try:
            edges_per_comp, hidden_per_comp = _read_edges_with_hidden_diff(view, drw, comps)
        except Exception as e:
            print(f"[S3-{name}-read] hidden diff failed: {e}, 按可见边继续", flush=True)
            edges_per_comp, hidden_per_comp = _read_edges(view, comps), []
        arr = list(view.ModelToViewTransform.ArrayData)
        entities, notes = extract_view_entities(edges_per_comp, arr, cfg.spline_sample_points)
        hidden_entities, hnotes = extract_view_entities(hidden_per_comp, arr, cfg.spline_sample_points)
        print(f"[S3-{name}-read] entities={len(entities)} hidden={len(hidden_entities)} notes={notes+hnotes}", flush=True)
        pause(f"S3-{name} 实体提取")

    print("[S4] 关闭所有文档 ...", flush=True)
    sw.CloseAllDocuments(True)
    print("[S4] done. Step3 原子子步全部通过。", flush=True)


if __name__ == "__main__":
    main()
