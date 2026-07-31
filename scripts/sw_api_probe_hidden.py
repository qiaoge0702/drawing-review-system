# -*- coding: utf-8 -*-
"""隐藏线攻关探针：差集法验证（HLV 模式读数 - HLR 模式读数 = 隐藏边）

依据老板提供的官方文档：
- swViewEntityType_e: Edge=1, Vertex=2, Face=3, SilhouetteEdge=4（无隐藏边专属类型）
- GetVisibleEntities2 只返回"可见"实体 → 视图设为 HLV 后隐藏边才可见
- SetDisplayMode3 已废弃 → 用 SetDisplayMode4(UseParent, Mode, Facetted, Edges)
- swDisplayMode_e 具体值未知 → 遍历 1..6 实测每种模式的 Edge 读数

案例: LB26.11202轴套（有内孔，HLR 下应少于 HLV）
"""
import sys
import traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11202轴套.SLDPRT"
TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2025\templates\gb_a3.drwdot"
pythoncom.CoInitialize()


def p(m):
    print(m, flush=True)


def count_edges(view):
    """读取视图全部组件的 Edge(1)/SilhouetteEdge(4) 实体数"""
    comps = view.GetVisibleComponents
    n_edge = n_sil = 0
    for comp in comps if comps else []:
        try:
            edges = view.GetVisibleEntities2(comp, 1)
            n_edge += len(edges) if edges else 0
        except Exception as e:
            p(f"    [WARN] GetVisibleEntities2(comp,1): {str(e)[:80]}")
        try:
            sils = view.GetVisibleEntities2(comp, 4)
            n_sil += len(sils) if sils else 0
        except Exception as e:
            p(f"    [WARN] GetVisibleEntities2(comp,4): {str(e)[:80]}")
    return n_edge, n_sil


try:
    # 早期绑定：EnsureModule 加载 makepy 生成模块后包装 Dispatch
    # （EnsureDispatch 对运行中的 SW 实例取 GetTypeInfo 会失败，绕开）
    from win32com.client import gencache
    mod = gencache.EnsureModule("{83A33D31-27C5-11CE-BFD4-00400513BB57}", 0, 33, 0)
    sw = mod.ISldWorks(win32com.client.Dispatch("SldWorks.Application")._oleobj_)
    sw.OpenDoc6(SRC, 1, 2, "", 0, 0)
    drw = sw.NewDocument(TPL, 0, 0.0, 0.0)

    view = drw.CreateDrawViewFromModelView3(SRC, "*前视", 0.15, 0.15, 0)
    drw.ForceRebuild3(False)
    p(f"视图创建: {'OK' if view else 'FAIL'}")
    p(f"组件数: {len(view.GetVisibleComponents) if view.GetVisibleComponents else 0}")

    # 遍历显示模式 1..6，实测 Edge/Silhouette 读数
    p("\n=== 显示模式实测（早期绑定 SetDisplayMode4，失败回退 SetDisplayMode3）===")
    results = {}
    for mode in range(1, 7):
        try:
            try:
                ok = view.SetDisplayMode4(False, mode, False, False)
            except Exception:
                ok = view.SetDisplayMode3(False, mode, False, False)
            drw.ForceRebuild3(False)
            n_edge, n_sil = count_edges(view)
            results[mode] = (ok, n_edge, n_sil)
            p(f"  Mode={mode}: set_ok={ok}  Edge(1)={n_edge}  Silhouette(4)={n_sil}")
        except Exception as e:
            p(f"  Mode={mode}: [ERR] {str(e)[:100]}")

    # 判定：哪个模式是 HLV（Edge 数显著多于其他）
    if results:
        counts = {m: r[1] for m, r in results.items() if r[0]}
        if counts:
            mx = max(counts, key=counts.get)
            mn = min(counts, key=counts.get)
            p(f"\n判定: Mode={mx} 边数最多({counts[mx]})，疑似 HLV；"
              f"Mode={mn} 边数最少({counts[mn]})，疑似 HLR/线框")
            if counts[mx] > counts[mn]:
                p(f"差集法可行: 隐藏边 ≈ {counts[mx] - counts[mn]} 条")
            else:
                p("差集法无效: 各模式读数无差异")

    sw.CloseAllDocuments(True)
    p("\n[DONE]")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
