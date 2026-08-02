# -*- coding: utf-8 -*-
"""直接解析 swconst.tlb，导出 swInsert* 枚举值"""
import pythoncom
tlb = pythoncom.LoadTypeLib(r"E:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\swconst.tlb")
for i in range(tlb.GetTypeInfoCount()):
    ti = tlb.GetTypeInfo(i)
    name = tlb.GetDocumentation(i)[0]
    if "nsert" not in name:
        continue
    ta = ti.GetTypeAttr()
    print(f"=== {name} (vars={ta.cVars}) ===")
    for v in range(ta.cVars):
        vd = ti.GetVarDesc(v)
        vid = vd.memid
        vname = ti.GetDocumentation(vid)[0]
        val = vd.value
        print(f"  {vname} = {val}")
