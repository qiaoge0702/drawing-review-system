# -*- coding: utf-8 -*-
"""SW 中文版预定义视图名探查：ModelViewManager 可用视图枚举"""
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
    part = sw.OpenDoc6(SRC, 1, 2, "", errors, warnings)

    # 枚举模型的命名视图
    mvm = part.ModelViewManager
    try:
        names = mvm.GetModelViewNames
        p(f"GetModelViewNames: {names}")
    except Exception as e:
        p(f"[ERR] GetModelViewNames: {str(e)[:100]}")

    drw = sw.NewDocument(TPL, 0, 0.0, 0.0)
    x = 0.10
    ok_views = []
    for vname in ["*前视", "*俯视", "*左视", "*上视", "*下视", "*右视", "*后视",
                  "*Top", "*Bottom", "*Front", "*Left", "*Right"]:
        v = drw.CreateDrawViewFromModelView3(SRC, vname, x, 0.15, 0)
        p(f"  {vname}: {'OK' if v else 'FAIL'}")
        if v:
            ok_views.append(vname)
            x += 0.10
    p(f"可用: {ok_views}")
    sw.CloseAllDocuments(True)
    p("[DONE]")
except Exception:
    p("[FATAL] " + traceback.format_exc())
finally:
    pythoncom.CoUninitialize()
