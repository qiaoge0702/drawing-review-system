# -*- coding: utf-8 -*-
"""合并 S-1 终验结果进 results.json / spike.log"""
import json, os, glob, time

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

with open(os.path.join(OUT, "results.json"), encoding="utf-8") as f:
    results = json.load(f)

with open(os.path.join(OUT, "results_s1g.json"), encoding="utf-8") as f:
    s1g = json.load(f)

results["S-1"] = {
    "verdict": "VALIDATED",
    "insert_view": "OK (CreateDrawViewFromModelView3 path-string, '*前视')",
    "import_dims": "OK InsertModelAnnotations2(32768,True,0,True,True,True) -> 14 dims",
    "enum_dims": f"before={s1g['dims_before']} after={s1g['dims_after']}",
    "delete": s1g["delete_test"],
    "select_view": s1g.get("select_view"),
    "ima3_note": "IMA3 全组合 0 dims; IMA2 为正确路径; 枚举值 32768/524288 (swconst.tlb 实测)",
    "script": "spike_s1g.py",
}
results["S-2"]["png_with_view"] = {"ok": True, "sec": 0.3, "KB": 4,
    "visual": "图框/标题栏/视图轮廓清晰可见 (S1g-snapshot.png); 1:100 比例下尺寸数字位图不可读(布局问题)"}
results["S-2"]["verdict"] = "VALIDATED"
results["S-3"]["verdict"] = "VALIDATED"
results["_verdict_doc"] = "VERDICT.md"

with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)

with open(os.path.join(OUT, "spike.log"), "a", encoding="utf-8") as f:
    f.write(f"\n[{time.strftime('%H:%M:%S')}] S-1 终验完成: VALIDATED (14 dims, delete 5/5); S-2 补验 VALIDATED; 详见 VERDICT.md / results_s1g.json / spike_s1g.log\n")

print("merged OK")
