# -*- coding: utf-8 -*-
"""SW API 侦察 - 第6步：工程图视图实体读取（修正签名）"""
import sys, traceback
import pythoncom
import win32com.client

SRC = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.11202轴套.SLDPRT"
TPL = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2025\templates\gb_a3.drwdot"
pythoncom.CoInitialize()

def p(m): print(m, flush=True)

try:
    sw = win32com.client.Dispatch("SldWorks.Application")
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    sw.OpenDoc6(SRC, 1, 2, "", errors, warnings)
    drw = sw.NewDocument(TPL, 0, 0.0, 0.0)
    view = drw.CreateDrawViewFromModelView3(SRC, "*前视", 0.15, 0.15, 0)
    p(f"视图: {view is not None}, 名称={view.Name if view else '-'}")

    # 强制重建工程图（新插入视图可能未生成交线数据）
    try:
        ok = drw.ForceRebuild3(True)
        p(f"[OK] ForceRebuild3: {ok}")
    except Exception as ex:
        p(f"[ERR] ForceRebuild3: {str(ex)[:100]}")
    try:
        drw.ActivateView(view.Name)
        drw.ViewZoomtofit2()
        p("[OK] 视图激活 + 缩放")
    except Exception as ex:
        p(f"[ERR] 激活: {str(ex)[:100]}")

    # 视图内组件
    try:
        comps = view.GetVisibleComponents
        p(f"[OK] GetVisibleComponents: {len(comps) if comps else 0} 个")
        comp0 = comps[0] if comps else None
    except Exception as ex:
        p(f"[ERR] GetVisibleComponents: {str(ex)[:100]}")
        comp0 = None

    # GetVisibleEntityCount(Component) - 传真实组件
    if comp0 is not None:
        for label, fn in [
            ("GetVisibleEntityCount(comp)", lambda: view.GetVisibleEntityCount(comp0)),
            ("GetVisibleEntities2(comp, 0)", lambda: view.GetVisibleEntities2(comp0, 0)),
            ("GetVisibleEntities2(comp, 1)", lambda: view.GetVisibleEntities2(comp0, 1)),
        ]:
            try:
                r = fn()
                p(f"[OK] {label}: {str(r)[:160]}")
            except Exception as ex:
                p(f"[ERR] {label}: {str(ex)[:100]}")

    sw.CloseAllDocuments(True)
    p("[DONE]")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
