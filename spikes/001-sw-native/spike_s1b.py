# -*- coding: utf-8 -*-
"""Spike 001 S-1b: 零件级尺寸导入/删除终验（装配体无标注尺寸，改用零件）"""
import os, time, json, traceback
import win32com.client as wc
import pythoncom

BASE = r"E:\147\workspaces\drawing-review-system"
CASE_DIR = os.path.join(BASE, "LB26拉臂装置")
SLDASM = os.path.join(CASE_DIR, "LB26.00001旋转轴.SLDPRT")
DOC_TYPE = 1  # swDocPART
OUT = os.path.join(BASE, "spikes", "001-sw-native", "output")
TPL = os.path.join(OUT, "LB26-template.drwdot")

results = {}
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

def wrap(disp, ifname, mod):
    if disp is None:
        return None
    kls = getattr(mod, ifname)
    raw = disp._oleobj_.QueryInterface(kls.CLSID, pythoncom.IID_IDispatch)
    return kls(raw)

def first_dim(v, mod):
    return wrap(v.GetFirstDisplayDimension5(), 'IDisplayDimension', mod)

def next_dim(d, mod):
    return wrap(d.GetNext5(), 'IDisplayDimension', mod)

def count_dims(v, mod):
    n, d = 0, wrap(v.GetFirstDisplayDimension5(), 'IDisplayDimension', mod)
    while d:
        n += 1
        d = wrap(d.GetNext5(), 'IDisplayDimension', mod)
    return n

def save_report():
    with open(os.path.join(OUT, "results_s1b.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(OUT, "spike_s1b.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

def main():
    pythoncom.CoInitialize()
    sw = wc.GetObject(None, "SldWorks.Application")
    log(f"SW connected pid={sw.GetProcessID()}")
    log(f"asm exists={os.path.exists(SLDASM)} tpl exists={os.path.exists(TPL)}")

    drw2m, asm = None, None
    try:
        # 1) 新建图纸（用 S-3 产物模板；若无则退回 SW 默认模板）
        tpl = TPL if os.path.exists(TPL) else ""
        r = sw.NewDocument(tpl, 0, 0.0, 0.0)
        drw2m = r[0] if isinstance(r, tuple) else r
        results["new_drawing"] = "OK" if drw2m else "FAIL"
        log(f"new_drawing: {results['new_drawing']}")
        if not drw2m:
            return
        drw_title = P(drw2m.GetTitle)
        log(f"drw title: {drw_title}")

        # 2) 插视图：第一参数为模型文件路径字符串；先确保装配体已加载
        asm, e = U(sw.OpenDoc6(SLDASM, DOC_TYPE, 1, "", 0, 0))
        results["open_asm"] = "OK" if asm else "FAIL"
        log(f"open_asm: {results['open_asm']} err={e}")
        view = drw2m.CreateDrawViewFromModelView3(SLDASM, "*前视", 0.15, 0.15, 0)
        results["insert_view"] = "OK" if view else "FAIL_NULL"
        log(f"insert_view: {results['insert_view']}")
        if not view:
            return
        vname = P(view.GetName2)
        log(f"view name: {vname}")

        # 3) NewDocument 返回动态派发对象，typeinfo 检索坏掉（方法解析为 None）。
        #    绕行：ActivateDoc 后经 gen_py sw.ActiveDoc 拿 IModelDoc2，再 CastTo IDrawingDoc
        sw.ActivateDoc3(drw_title, False, 0, 0)
        act = sw.ActiveDoc
        results["active_doc"] = "OK" if act else "FAIL"
        # CastTo 会因 GetTypeInfo 坏掉而失败；绕过 EnsureDispatch，手工 QueryInterface + gen_py 类包装
        import sys
        mod = __import__(type(sw).__module__, fromlist=['IDrawingDoc'])
        drw2 = wrap(act, 'IDrawingDoc', mod)
        results["cast_idrawingdoc"] = "OK" if drw2 else "FAIL"
        log(f"cast: {results['cast_idrawingdoc']}")
        if not drw2:
            return
        # 经由 gen_py IDrawingDoc 拿视图（GetFirstView=图纸自身, GetNextView=第一真视图）
        v0 = wrap(drw2.GetFirstView(), 'IView', mod)
        view = wrap(v0.GetNextView(), 'IView', mod) if v0 else None
        while view and P(view.GetName2) != vname:
            view = wrap(view.GetNextView(), 'IView', mod)
        results["refind_view"] = "OK" if view else "FAIL"
        log(f"refind_view: {results['refind_view']}")
        if not view:
            return
        # 设比例让视图可见（快照质量验证用）
        try:
            done_scale = False
            for sc_meth in ('SetScale2', 'SetScale'):
                if hasattr(view, sc_meth):
                    getattr(view, sc_meth)(0.2, False)
                    done_scale = True; break
            if not done_scale:
                view.ScaleRatio = (1.0, 5.0)
            mdoc0 = wrap(act, 'IModelDoc2', mod)
            mdoc0.ForceRebuild3(True)
            results["set_scale"] = "OK"
        except Exception as exs:
            results["set_scale"] = f"EX {exs}"
        log(f"set_scale: {results['set_scale']}")
        # 探针：gen_py 类上确认 InsertModelAnnotations3 存在
        probe = [m for m in dir(type(drw2)) if "Model" in m or "nnot" in m]
        results["probe_idrawingdoc_methods"] = probe
        log(f"probe IDrawingDoc: {probe}")

        # 4) InsertModelAnnotations3(Option, Types, AllTypes, Views, MarkedOnly, UsePlacement) 共 6 参
        before = count_dims(view, mod)
        results["dims_before"] = before
        inserted = None
        # swconst 实测：swInsertDimensionsMarkedForDrawing=32768, NotMarked=524288, Dimensions=8
        # Option/Types 都是 swInsertAnnotation_e 位掩码
        OPT_MARKED, OPT_NOTMARKED, DIM_ALL = 32768, 524288, 8|32768|524288
        annos = []
        for opt, tag in ((OPT_MARKED, 'marked'), (OPT_NOTMARKED, 'not_marked')):
            key = f"insert_annotations_{tag}"
            try:
                inserted = drw2.InsertModelAnnotations3(opt, DIM_ALL, True, True, True, False)
                results[key] = f"OK n={len(inserted) if inserted else 0}"
                log(f"IMA3 {key}: {results[key]}")
                if inserted:
                    annos.extend(inserted)
            except Exception as ex1:
                results[key] = f"EX {ex1}"
                log(f"IMA3 {key} EX: {ex1}")
        results["annos_returned"] = len(annos)
        # 探测返回对象支持的接口与方法
        dims_info = []
        if annos:
            a0 = annos[0]
            for ifn in ('IDisplayDimension', 'IAnnotation', 'IDimension'):
                try:
                    kls = getattr(mod, ifn)
                    raw = a0._oleobj_.QueryInterface(kls.CLSID, pythoncom.IID_IDispatch)
                    obj = kls(raw)
                    meths = [m for m in dir(type(obj)) if not m.startswith('_')]
                    dims_info.append({"iface": ifn, "ok": True,
                                      "name_like": [m for m in meths if 'Name' in m or 'Select' in m or 'Type' in m]})
                except Exception as exi:
                    dims_info.append({"iface": ifn, "ok": False, "ex": str(exi)[:120]})
        results["iface_probe"] = dims_info
        log(f"iface probe: {json.dumps(dims_info, ensure_ascii=False)[:500]}")
        # 用 IAnnotation 取名字
        ret_names = []
        for a in annos[:8]:
            try:
                ann = wrap(a, 'IAnnotation', mod)
                nm = None
                for cand in ('GetName', 'GetNameForSelection'):
                    if hasattr(ann, cand):
                        nm = P(getattr(ann, cand)); break
                ret_names.append(nm)
            except Exception as exn:
                ret_names.append(f"EX {exn}")
        results["returned_dims"] = ret_names
        log(f"returned names: {ret_names}")
        # ForceRebuild 后再枚举视图尺寸
        try:
            mdoc = wrap(act, 'IModelDoc2', mod)
            mdoc.ForceRebuild3(True)
        except Exception as exr:
            log(f"rebuild EX: {exr}")
        results["dims_enum_after_rebuild"] = count_dims(view, mod)
        log(f"dims enum after rebuild: {results['dims_enum_after_rebuild']}")

        after = count_dims(view, mod)
        results["dims_after"] = after
        log(f"dims: before={before} after={after}")

        # 5) 删除验证：SelectByID2 + DeleteSelection2
        deleted, tested = 0, 0
        d, targets = first_dim(view, mod), []
        while d and len(targets) < 5:
            targets.append(d); d = next_dim(d, mod)
        names = []
        # 若视图中枚举不到尺寸，直接用 IMA3 返回的尺寸对象做删除验证
        if not targets and annos:
            tmp = []
            for a in annos[:5]:
                try:
                    tmp.append(wrap(a, 'IDisplayDimension', mod))
                except Exception:
                    tmp.append(wrap(a, 'IAnnotation', mod))
            targets = tmp
            results["delete_source"] = "IMA3_returned"
        else:
            results["delete_source"] = "view_enum"
        ext = wrap(act, 'IModelDoc2', mod).Extension
        for t in targets:
            try:
                nm = None
                for cand in ('GetNameForSelection', 'GetName'):
                    if hasattr(t, cand):
                        nm = P(getattr(t, cand)); break
                names.append(nm)
                ok = ext.SelectByID2(nm, "DIMENSION", 0, 0, 0, False, 0, None, 0)
                tested += 1
                if ok:
                    ext.DeleteSelection2(1)
                    deleted += 1
            except Exception as exd:
                log(f"del EX: {exd}")
        results["dim_names_sample"] = names
        results["delete_test"] = f"deleted={deleted}/{tested} remain={count_dims(view, mod)}"
        log(f"delete: {results['delete_test']} names={names}")

        # 6) S-2 补验：对有视图的图纸导出 PNG
        out_png = os.path.join(OUT, "S1b-snapshot.png")
        t0 = time.time()
        ok, ee = U(ext.SaveAs(out_png, 0, 1, None, 0, 0))
        dt = time.time() - t0
        size = os.path.getsize(out_png) if os.path.exists(out_png) else 0
        results["snapshot_png"] = {"ok": bool(ok), "sec": round(dt, 1), "KB": size // 1024}
        log(f"png: ok={ok} {dt:.1f}s {size//1024}KB")
    except Exception:
        results["exception"] = traceback.format_exc()
        log("FATAL EX")
    finally:
        # 只关我们自己打开的文档，不关别人的
        try:
            if asm:
                sw.CloseDoc(P(asm.GetTitle))
                log("closed asm we opened")
        except Exception as exc:
            log(f"close asm EX (leave it): {exc}")
        if drw2m:
            try:
                sw.CloseDoc(P(drw2m.GetTitle))
                log("closed our temp drawing")
            except Exception as exc:
                log(f"close EX (leave it): {exc}")
        save_report()
        log("=== DONE ===")

if __name__ == "__main__":
    main()
