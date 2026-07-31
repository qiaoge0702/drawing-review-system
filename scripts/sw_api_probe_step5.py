# -*- coding: utf-8 -*-
"""SW API 侦察 - 第5步（决定性）：工程图视图作为投影引擎
验证：新建工程图 → 插入模型预定义视图 → GetVisibleEntities2 读取可见/隐藏实体
"""
import sys, time, traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11202轴套.SLDPRT"
FALLBACK = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11202轴套.SLDPRT"

pythoncom.CoInitialize()

def p(m): print(m, flush=True)

import os
src = SRC if os.path.exists(SRC) else FALLBACK
p(f"目标零件: {src} (存在={os.path.exists(src)})")

try:
    sw = win32com.client.Dispatch("SldWorks.Application")
    p(f"SW: {sw.RevisionNumber}")

    # 打开零件
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    part = sw.OpenDoc6(src, 1, 2, "", errors, warnings)
    p(f"零件打开: {part is not None}, err={errors.value}")

    # 新建工程图（国标 A3 模板）
    tpl = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2025\templates\gb_a3.drwdot"
    p(f"工程图模板: {tpl} (存在={os.path.exists(tpl)})")

    t1 = time.time()
    drw = sw.NewDocument(tpl, 0, 0.0, 0.0)
    p(f"工程图创建: {drw is not None}, 耗时 {time.time()-t1:.1f}s")

    # 插入前视图（中文版 SW 预定义视图名为中文）
    t1 = time.time()
    view = None
    for vname in ["*前视", "*Front", "*主视"]:
        view = drw.CreateDrawViewFromModelView3(src, vname, 0.15, 0.15, 0)
        p(f"  尝试视图名 {vname}: {view is not None}")
        if view is not None:
            break
    p(f"前视图插入: {view is not None}, 耗时 {time.time()-t1:.1f}s")
    if view is None:
        p("[FATAL] 视图插入失败")
        sw.CloseAllDocuments(True)
        sys.exit(0)

    # 读取可见实体: GetVisibleEntities2(Component, EntityType, HiddenLinesVisible?)
    # EntityType: 0=edge? 先试签名变体
    for label, fn in [
        ("GetVisibleEntityCount(None)", lambda: view.GetVisibleEntityCount(None)),
        ("GetVisibleEntities2(None, 0)", lambda: view.GetVisibleEntities2(None, 0)),
        ("GetVisibleEntities2(None, 0, False)", lambda: view.GetVisibleEntities2(None, 0, False)),
        ("GetVisibleEntities2(None, 0, True)", lambda: view.GetVisibleEntities2(None, 0, True)),
    ]:
        try:
            r = fn()
            if isinstance(r, tuple):
                p(f"[OK] {label}: tuple len={len(r)}, 预览={str(r)[:150]}")
            else:
                p(f"[OK] {label}: {str(r)[:150]}")
        except Exception as ex:
            p(f"[ERR] {label}: {str(ex)[:100]}")

    sw.CloseAllDocuments(True)
    p("[DONE]")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
