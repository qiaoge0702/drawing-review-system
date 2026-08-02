# -*- coding: utf-8 -*-
"""Spike 001 v4: 全程 gen_py 早期绑定 + CastTo 接口转换 + P() 属性怪癖兼容"""
import os, time, json, traceback
import win32com.client as wc
import pythoncom

BASE = r"E:\147\workspaces\drawing-review-system"
CASE_DIR = os.path.join(BASE, "LB26拉臂装置")
SLDDRW = os.path.join(CASE_DIR, "LB26.00000拉臂总成.SLDDRW")
SLDASM = os.path.join(CASE_DIR, "LB26.00000拉臂总成.SLDASM")
OUT = os.path.join(BASE, "spikes", "001-sw-native", "output")
os.makedirs(OUT, exist_ok=True)

results = {"S-0": {}, "S-1": {}, "S-2": {}, "S-3": {}}
log_lines = []
def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    log_lines.append(line); print(line, flush=True)

def P(x):
    return x() if callable(x) else x

def U(ret):
    if isinstance(ret, tuple):
        return ret[0], (ret[1] if len(ret) > 1 else 0)
    return ret, 0

def count_dims(v):
    n, d = 0, v.GetFirstDisplayDimension5()
    while d:
        n += 1
        d = d.GetNext5()
    return n

def save_report():
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(OUT, "spike.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

def main():
    pythoncom.CoInitialize()
    try:
        sw = wc.GetObject(None, "SldWorks.Application")
        src = "existing"
    except Exception:
        sw = wc.gencache.EnsureDispatch("SldWorks.Application")
        sw.Visible = True
        src = "new"
    log(f"SW connected ({src})")

    # ---------- S-0 recon ----------
    log("=== S-0 recon LB26.SLDDRW ===")
    drw, e = U(sw.OpenDoc6(SLDDRW, 3, 1, "", 0, 0))
    if drw:
        try:
            drwD = wc.CastTo(drw, "IDrawingDoc")
            recon = {"sheets": []}
            sheet = drwD.GetFirstSheet()
            while sheet:
                sname = P(sheet.GetName)
                views = sheet.GetViews()
                vinfo = []
                if views:
                    for v in views:
                        vinfo.append({"name": P(v.GetName2), "dims": count_dims(v)})
                tbl = sheet.GetTableAnnotations()
                recon["sheets"].append({"name": sname, "views": vinfo,
                                        "tables": len(tbl) if tbl else 0})
                sheet = sheet.GetNextSheet()
            results["S-0"] = recon
            log("S-0: " + json.dumps(recon, ensure_ascii=False)[:600])
        except Exception:
            results["S-0"]["exception"] = traceback.format_exc()
            log("S-0 recon EX")

        # ---------- S-3 template ----------
        log("=== S-3 save .drwdot ===")
        tpl = os.path.join(OUT, "LB26-template.drwdot")
        ok, ee = U(drw.Extension.SaveAs(tpl, 0, 1, None, 0, 0))
        results["S-3"]["save_template"] = f"{'OK' if ok else 'FAIL'} errs={ee}"
        log(f"S-3: {results['S-3']['save_template']}")
        sw.CloseDoc(P(drw.GetTitle))
    else:
        results["S-0"]["open"] = "FAIL"
    save_report()

    # ---------- S-1 dims ----------
    log("=== S-1 new drawing + view + dims ===")
    tpl_path = os.path.join(OUT, "LB26-template.drwdot")
    if not os.path.exists(tpl_path):
        results["S-1"]["skip"] = "no template"
    else:
        try:
            asm, e = U(sw.OpenDoc6(SLDASM, 2, 1, "", 0, 0))
            results["S-1"]["open_asm"] = "OK" if asm else "FAIL"
            drw2m, _ = U(sw.NewDocument(tpl_path, 0, 0.0, 0.0)) if True else (None, 0)
            drw2m = sw.NewDocument(tpl_path, 0, 0.0, 0.0)
            drw2 = wc.CastTo(drw2m, "IDrawingDoc") if drw2m else None
            results["S-1"]["new_drawing"] = "OK" if drw2 else "FAIL"
            if asm and drw2:
                sw.ActivateDoc3(P(asm.GetTitle), False, 0, 0)
                sw.ActivateDoc3(P(drw2m.GetTitle), False, 0, 0)
                view = drw2.CreateDrawViewFromModelView3(asm, "*Front", 0.15, 0.15, 0)
                results["S-1"]["insert_view"] = "OK" if view else "FAIL"
                if view:
                    vname = P(view.GetName2)
                    log(f"S-1 view: {vname}")
                    before = count_dims(view)
                    view_arr = wc.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [view])
                    try:
                        r = drw2m.Extension.InsertModelAnnotations3(1, 32767, False, view_arr, True, True)
                        results["S-1"]["insert_annotations"] = f"ret={r}"
                    except Exception as ex1:
                        results["S-1"]["insert_annotations"] = f"EX1 {ex1}"
                        try:
                            r = drw2m.Extension.InsertModelAnnotations3(0, 32767, True, None, True, True)
                            results["S-1"]["insert_annotations_alt"] = f"ret={r}"
                        except Exception as ex2:
                            results["S-1"]["insert_annotations_alt"] = f"EX2 {ex2}"
                    after = count_dims(view)
                    results["S-1"]["dims_before"] = before
                    results["S-1"]["dims_after"] = after
                    log(f"S-1 dims: before={before} after={after}")
                    deleted, d, targets = 0, view.GetFirstDisplayDimension5(), []
                    while d and len(targets) < 5:
                        targets.append(d); d = d.GetNext5()
                    for t in targets:
                        try:
                            ok = drw2m.Extension.SelectByID2(P(t.GetNameForSelection), "DIMENSION", 0, 0, 0, False, 0, None, 0)
                            if ok:
                                drw2m.Extension.DeleteSelection2(1)
                                deleted += 1
                        except Exception as exd:
                            log(f"S-1 del EX: {exd}")
                    results["S-1"]["delete_test"] = f"deleted={deleted}/5 remain={count_dims(view)}"
                    log(f"S-1 delete: {results['S-1']['delete_test']}")

                    # ---------- S-2 snapshot ----------
                    log("=== S-2 snapshot of S-1 drawing ===")
                    for ext_name in ("png", "pdf"):
                        out_file = os.path.join(OUT, f"S1-snapshot.{ext_name}")
                        t0 = time.time()
                        ok, ee = U(drw2m.Extension.SaveAs(out_file, 0, 1, None, 0, 0))
                        dt = time.time() - t0
                        size = os.path.getsize(out_file) if os.path.exists(out_file) else 0
                        results["S-2"][ext_name] = {"ok": bool(ok), "sec": round(dt, 1), "KB": size // 1024}
                        log(f"S-2 {ext_name}: ok={ok} {dt:.1f}s {size//1024}KB")
                sw.CloseDoc(P(drw2m.GetTitle))
            if asm:
                sw.CloseDoc(P(asm.GetTitle))
        except Exception:
            results["S-1"]["exception"] = traceback.format_exc()

    save_report()
    log("=== DONE ===")

if __name__ == "__main__":
    main()
