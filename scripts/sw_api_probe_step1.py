# -*- coding: utf-8 -*-
"""
SW API 技术侦察 - 第1步：文档打开 + 组件结构 + tessellation 可用性探查
目标：验证三件事的可用性（用 LB26.10000底架.SLDASM）
  A. 装配体能否打开并遍历组件
  B. Body2.GetTessellation 三角网格提取（顶点/三角形/法线）
  C. Edge 级数据（圆弧识别的基础）
"""
import sys, time, traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.10000底架.SLDASM"

pythoncom.CoInitialize()
t0 = time.time()
log = []

def p(msg):
    log.append(msg)
    print(msg, flush=True)

try:
    sw = win32com.client.Dispatch("SldWorks.Application")
    p(f"[A0] SW 连接成功, 版本: {sw.RevisionNumber}")

    # 打开装配体（只读，避免锁文件）
    # OpenDoc6(FileName, Type, Options, Configuration, &Errors, &Warnings)
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.OpenDoc6(SRC, 2, 1, "", errors, warnings)  # type=2 assembly, options=1 read-only
    p(f"[A1] OpenDoc6 返回: {doc is not None}, errors={errors.value}, warnings={warnings.value}")
    if doc is None:
        p("[FATAL] 文档打开失败")
        sys.exit(1)
    p(f"[A2] 文档标题: {doc.GetTitle}, 耗时 {time.time()-t0:.1f}s")

    # A. 组件遍历
    assy = doc  # AssemblyDoc
    comps = assy.GetComponents(False)  # False = 不含轻化未解析? ToplevelOnly=False
    n = len(comps) if comps else 0
    p(f"[A3] 组件数量(GetComponents): {n}")
    names = []
    for c in (comps or [])[:10]:
        names.append(c.Name2)
    p(f"[A4] 前10组件: {names}")

    # B/C. 取第一个零件的 body 做 tessellation 探查
    body_found = 0
    tess_ok = 0
    edge_ok = 0
    for c in (comps or []):
        try:
            mdl = c.GetModelDoc2
            if mdl is None:
                continue
            if mdl.GetType != 1:  # 1=part
                continue
            part = mdl
            bodies = part.GetBodies2(0, True)  # swSolidBody, visible only
            if not bodies:
                continue
            body = bodies[0]
            body_found += 1
            # B. tessellation
            try:
                tess = body.GetTessellation(None)
                if tess is not None:
                    verts = tess.GetVertices  # property
                    faces = tess.GetFaceIds
                    fins = tess.GetFins
                    tess_ok += 1
                    if tess_ok == 1:
                        vcount = len(verts) // 3 if verts else 0
                        p(f"[B1] 首个 body tessellation OK: 顶点数~{vcount}, face数={len(faces) if faces else 0}")
                        # 三角形索引
                        tris = tess.GetTriangleIndices if hasattr(tess, 'GetTriangleIndices') else None
                        p(f"[B2] GetTriangleIndices 可用: {tris is not None}")
            except Exception as e:
                p(f"[B-ERR] {c.Name2}: {e}")
            # C. edges
            try:
                edges = body.GetEdges()
                if edges:
                    edge_ok += 1
                    if edge_ok == 1:
                        e0 = edges[0]
                        curve = e0.GetCurve
                        ctype = None
                        try:
                            ctype = curve.Identity()  # 3001=line 3002=circle?
                        except Exception:
                            pass
                        p(f"[C1] 首个 body edges OK: {len(edges)} 条, edge[0] curve.Identity={ctype}")
            except Exception as e:
                p(f"[C-ERR] {c.Name2}: {e}")
            if body_found >= 5:
                break
        except Exception as e:
            p(f"[ERR] 组件 {c.Name2}: {e}")
    p(f"[SUM] bodies探查={body_found}, tess成功={tess_ok}, edges成功={edge_ok}")

    # D. 特征遍历探查（第一个零件）
    try:
        for c in (comps or []):
            mdl = c.GetModelDoc2
            if mdl is None or mdl.GetType != 1:
                continue
            feat = mdl.FirstFeature
            feats = []
            while feat is not None and len(feats) < 15:
                feats.append(f"{feat.Name}:{feat.GetTypeName2}")
                feat = feat.GetNextFeature
            p(f"[D1] 零件 {c.Name2} 特征: {feats}")
            break
    except Exception as e:
        p(f"[D-ERR] {e}")

    doc = None
    sw.CloseAllDocuments(True)
    p(f"[DONE] 总耗时 {time.time()-t0:.1f}s")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
