# -*- coding: utf-8 -*-
"""
SW API 技术侦察 - 第2步：轻化组件解析 + tessellation 三种调用方式对比
"""
import sys, time, traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.10000底架.SLDASM"

pythoncom.CoInitialize()
t0 = time.time()

def p(msg):
    print(msg, flush=True)

try:
    sw = win32com.client.Dispatch("SldWorks.Application")
    p(f"[A0] SW 连接: {sw.RevisionNumber}")

    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    # options=2 (swOpenDocOptions_ReadOnly) + 静默
    doc = sw.OpenDoc6(SRC, 2, 2, "", errors, warnings)
    p(f"[A1] 打开: {doc is not None}, errors={errors.value}, warnings={warnings.value}")

    # 解析全部轻化组件
    t1 = time.time()
    n_resolved = doc.ResolveAllLightWeightComponents(True)
    p(f"[A2] ResolveAllLightWeightComponents 返回: {n_resolved}, 耗时 {time.time()-t1:.1f}s")

    comps = doc.GetComponents(False)
    p(f"[A3] 组件数: {len(comps) if comps else 0}")

    # 只挑 LB26 自制件（跳过 GB/JB 标准件）
    custom = [c for c in (comps or []) if c.Name2.startswith("LB26")]
    p(f"[A4] LB26 自制件: {len(custom)}")

    tested = 0
    for c in custom:
        if tested >= 4:
            break
        mdl = c.GetModelDoc2
        if mdl is None or mdl.GetType != 1:
            continue
        part = mdl
        bodies = part.GetBodies2(0, True)
        if not bodies:
            p(f"[SKIP] {c.Name2}: 无实体")
            continue
        body = bodies[0]
        tested += 1
        p(f"--- {c.Name2} ---")

        # 方式1: IBody2.GetTessellation
        try:
            tess = body.GetTessellation(None)
            if tess is None:
                p("  [M1] GetTessellation 返回 None")
            else:
                verts = tess.GetVertices
                nverts = len(verts) // 3 if verts else 0
                p(f"  [M1] GetTessellation OK: 顶点~{nverts}")
        except Exception as e:
            p(f"  [M1-ERR] GetTessellation: {str(e)[:80]}")

        # 方式2: IBody2.GetTessTriangles(正规化标志)
        try:
            tris = body.GetTessTriangles(True)
            n = len(tris) // 9 if tris else 0
            p(f"  [M2] GetTessTriangles(True) OK: 三角形~{n}")
        except Exception as e:
            p(f"  [M2-ERR] GetTessTriangles(True): {str(e)[:80]}")

        # 方式3: Face2.GetTessTriangles
        try:
            faces = body.GetFaces()
            total = 0
            for f in (faces or []):
                t = f.GetTessTriangles(True)
                if t:
                    total += len(t) // 9
            p(f"  [M3] Face级 GetTessTriangles: 面数={len(faces) if faces else 0}, 三角形~{total}")
        except Exception as e:
            p(f"  [M3-ERR] Face级: {str(e)[:80]}")

        # C. edge 曲线类型识别
        try:
            edges = body.GetEdges()
            types = {}
            for e in (edges or []):
                try:
                    curve = e.GetCurve
                    cid = curve.Identity()
                    types[cid] = types.get(cid, 0) + 1
                except Exception:
                    types["err"] = types.get("err", 0) + 1
            p(f"  [C] edges={len(edges) if edges else 0}, 曲线类型分布: {types}")
        except Exception as e:
            p(f"  [C-ERR] {str(e)[:80]}")

    sw.CloseAllDocuments(True)
    p(f"[DONE] 总耗时 {time.time()-t0:.1f}s")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
