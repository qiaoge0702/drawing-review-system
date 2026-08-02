"""
SW 原生真图纸引擎封装（COM 边界层）

方案B（2026-08-02，取代 DXF 线稿路线；SW API 原生优先铁律）：

  create_drawing_sync（Step3 建图纸+真视图）：
    SW模型 → OpenDoc6(只读+静默) → 读模型包围盒 → 布局引擎按企业模板
    实际图幅算比例/第一角位置（禁止 1:100 失真）→ NewDocument(.drwdot 企业模板)
    → CreateDrawViewFromModelView3 × N（中文预定义视图名，第一角）
    → SetDisplayMode3 隐藏线可见 + 设视图比例 → 迭代重定位
    → Extension.SaveAs 保存中间 SLDDRW + PNG 真图快照 → CloseAllDocuments

  finalize_drawing_sync（Step7 收尾）：
    OpenDoc6(Step3 SLDDRW, 静默可写) → CustomPropertyManager 写标题栏属性
    （图号/名称/材料/重量/比例，取不到的字段由调用方如实留空）
    → Extension.SaveAs 另存 SLDDRW/DWG/PDF + PNG 终图快照 → CloseAllDocuments

纪律：
- 一切 COM 调用经 sw_com.run_sw 排队到 COM 线程（本模块只提供同步函数）
- 文档用完 CloseAllDocuments(True)；禁止杀 SW 进程；OpenDoc 必带 Silent
- 失败/异常禁止静默：warnings 如实上报；视图插入失败 → SWException
- SW 单位 = 米；图纸坐标换算米制；layout 引擎输出 mm → 插入时 /1000
- spike 001 定调：中文版预定义视图名必须中文（config.predefined_view_names，
  禁止硬编码 "*Front"）；COM 对象方法调用走晚期绑定（NewDocument/ActiveDoc
  返回对象 typeinfo 损坏，gen_py CastTo/wrap 不可靠——生产代码先例为直接调）
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import get_settings
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# swDisplayMode_e：3 = HiddenLinesVisible（虚线显示隐藏线）
_DISPLAY_HLV = 3
# swSaveAsOptions_e：1 = Silent
_SAVEAS_SILENT = 1
# 迭代重定位收敛判据（米）= 0.2mm
_POS_TOL_M = 2e-4


def _open_doc(sw_app: Any, path: str, read_only: bool = True) -> Any:
    """OpenDoc6(零件/装配/工程图自动识别)；晚期绑定用 VARIANT 取错误码，
    早期绑定对象 byref 参数直接传 int（VARIANT 会 TypeError，回退直传）。
    必带 Silent(1) 抑制模态提示框防 COM 挂死；read_only 追加 ReadOnly(2)"""
    lower = path.lower()
    if lower.endswith(".slddrw"):
        doc_type = 3
    elif lower.endswith(".sldasm"):
        doc_type = 2
    else:
        doc_type = 1
    # options: 1=Silent | 2=ReadOnly
    _OPTS = 1 | (2 if read_only else 0)
    try:
        import pythoncom
        import win32com.client
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        return sw_app.OpenDoc6(path, doc_type, _OPTS, "", errors, warnings)
    except (ImportError, TypeError):
        return sw_app.OpenDoc6(path, doc_type, _OPTS, "", 0, 0)


def _get_model_box(doc: Any, source_file: str,
                   warnings: List[str]) -> Optional[Tuple[float, ...]]:
    """
    读模型包围盒（单次 API，米）：零件 GetPartBox / 装配 GetBox。
    取不到 → None（调用方走视图轮廓回退），如实 warning，禁止静默。
    """
    is_asm = source_file.lower().endswith(".sldasm")
    try:
        box = doc.GetBox(False) if is_asm else doc.GetPartBox(True)
        if box and len(box) >= 6:
            vals = tuple(float(v) for v in box[0:6])
            if vals[3] > vals[0] and vals[4] > vals[1] and vals[5] > vals[2]:
                return vals
    except Exception as e:
        logger.debug(f"model box API failed: {e}")
    warnings.append("模型包围盒 API 不可用，回退为按视图轮廓测量（如实上报）")
    return None


def _view_sizes_from_box(box: Sequence[float], view_names: Sequence[str]
                         ) -> List[Tuple[str, float, float]]:
    """模型包围盒(米) → 各视图 宽/高(mm)（front=X×Z, top=X×Y, left=Y×Z）"""
    dx = (box[3] - box[0]) * 1000.0
    dy = (box[4] - box[1]) * 1000.0
    dz = (box[5] - box[2]) * 1000.0
    dims = {"front": (dx, dz), "top": (dx, dy), "left": (dy, dz)}
    return [(n, *dims[n]) for n in view_names]


def _compute_layout(sizes: List[Tuple[str, float, float]], bom_rows: int,
                    task_id: str) -> Dict[str, Any]:
    """复用 step3 布局引擎（延迟导入避免模块级耦合）：比例/图幅/第一角位置"""
    from app.generators.steps import step3_view_project as s3
    views = [{"name": n,
              "bounding_box": {"min_x": 0.0, "min_y": 0.0,
                               "max_x": w, "max_y": h}}
             for n, w, h in sizes]
    den = s3._compute_scale_denominator(views, task_id)
    sheet = s3._select_sheet(sizes, den, bom_rows, task_id)
    if sheet != s3._BASE_SHEET:
        # 图幅升级后按最终图幅重算比例（2026-08-01 老板验收根因：
        # A3 适配比例放到 A0 图幅 → 视图缩成左上角一小簇）
        den = s3._compute_scale_denominator(views, task_id, sheet)
    sw, sh = s3._SHEET_SIZES[sheet]
    positions = s3._first_angle_positions(sizes, den, sw, sh)
    if positions is None:
        # 与 _build_layout 同一策略：告警后按超大图幅排布（允许溢出，不静默）
        logger.warning(f"[Task:{task_id}] step3: layout overflows {sheet} "
                       f"at 1:{den:g}, positions may exceed sheet")
        positions = s3._first_angle_positions(sizes, den, 1e6, 1e6)
    return {"scale_den": den, "sheet": sheet, "positions": positions}


def _sheet_name_of(sheet_w: float, sheet_h: float) -> str:
    """实际图幅尺寸(mm) → GB 图幅名；不匹配 → '宽x高' 自定义名（如实）"""
    from app.generators.steps import step3_view_project as s3
    for name, (w, h) in s3._SHEET_SIZES.items():
        if abs(sheet_w - w) < 1.0 and abs(sheet_h - h) < 1.0:
            return name
    return f"{sheet_w:g}x{sheet_h:g}"


def _layout_on_sheet(sizes: List[Tuple[str, float, float]], sheet_w: float,
                     sheet_h: float, task_id: str) -> Dict[str, Any]:
    """按实际图幅(mm)算 GB 比例 + 第一角位置（方案B：企业模板图幅固定，
    比例自适应图幅，禁止出现 1:100 失真）"""
    from app.generators.steps import step3_view_project as s3
    den = float(s3._GB_SCALES[-1])
    for n in s3._GB_SCALES:
        if s3._first_angle_positions(sizes, float(n), sheet_w, sheet_h) is not None:
            den = float(n)
            break
    else:
        logger.warning(f"[Task:{task_id}] step3: views do not fit "
                       f"{sheet_w:g}x{sheet_h:g} even at 1:{den:g}, clamped")
    positions = s3._first_angle_positions(sizes, den, sheet_w, sheet_h)
    if positions is None:
        logger.warning(f"[Task:{task_id}] step3: layout overflows "
                       f"{sheet_w:g}x{sheet_h:g} at 1:{den:g}, "
                       f"positions may exceed sheet")
        positions = s3._first_angle_positions(sizes, den, 1e6, 1e6)
    return {"scale_den": den, "positions": positions}


def _get_sheet_size_mm(drw: Any, warnings: List[str],
                       task_id: str = "") -> Optional[Tuple[float, float]]:
    """读工程图当前图纸页实际尺寸（米→mm）。ISheet.GetSize 晚期绑定
    byref double；读不到 → None（调用方回退布局引擎图幅）+ 如实 warning"""
    try:
        import pythoncom
        import win32com.client
        sheet = drw.GetCurrentSheet()
        vw = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_R8, 0.0)
        vh = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_R8, 0.0)
        ret = sheet.GetSize(vw, vh)
        # 早期绑定返回元组 (ret, w, h)；晚期绑定写回 VARIANT
        if isinstance(ret, (tuple, list)) and len(ret) >= 3:
            w_m, h_m = float(ret[-2]), float(ret[-1])
        else:
            w_m, h_m = float(vw.value), float(vh.value)
        if w_m > 0 and h_m > 0:
            return w_m * 1000.0, h_m * 1000.0
    except Exception as e:
        logger.debug(f"[Task:{task_id}] GetSize failed: {e}")
    warnings.append("工程图图纸页尺寸读取失败，按布局引擎选定图幅排布（如实上报）")
    return None


def _set_display_mode(view: Any, mode: int) -> Any:
    """SetDisplayMode3（晚期绑定真机实测可用）；SetDisplayMode4 两种绑定下
    均报"非选择性的参数"（2026-07-31 复测），仅作兜底尝试"""
    try:
        return view.SetDisplayMode3(False, mode, False, False)
    except Exception:
        return view.SetDisplayMode4(False, mode, False, False)


def _set_view_scale(view: Any, den: float, name: str, warnings: List[str]) -> None:
    """设视图比例 = 1/den（ScaleDecimal 晚期绑定属性写，真机实测可用；
    IView.SetScale2 本版 gen_py 不存在——spike 001 定调）；失败如实 warning"""
    try:
        view.ScaleDecimal = 1.0 / den
    except Exception as e:
        warnings.append(f"{name}: 视图比例设置失败（{str(e)[:60]}），按模板默认比例")


def _set_view_position(view: Any, dx_m: float, dy_m: float, name: str,
                       warnings: List[str]) -> None:
    """按增量平移视图（米）：Position 锚点语义与轮廓中心不一致（真机实测），
    用 当前 Position + (目标轮廓中心 − 实测轮廓中心) 的增量法，与锚点语义无关。
    失败如实 warning"""
    try:
        cur = [float(v) for v in view.Position]
        target = [cur[0] + dx_m, cur[1] + dy_m]
        try:
            # 真机实测（2026-08-01 probe_pos）：Position 必须传 VT_ARRAY|VT_R8
            # 的 VARIANT safearray；直传 list/tuple 列集错误（x 被置 0、y 取 x）
            import pythoncom
            import win32com.client
            view.Position = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_R8, target)
        except ImportError:
            view.Position = target
    except Exception as e:
        warnings.append(f"{name}: 视图位置设置失败（{str(e)[:60]}），位置可能偏移")


def _save_as(drw: Any, path: str, warnings: List[str], label: str) -> None:
    """Extension.SaveAs 静默保存/导出（SLDDRW/DWG/PDF/PNG 同通道）；
    失败回退 SaveAs，仍失败 → SWException(GEN_STEP_FAILED)"""
    try:
        import pythoncom
        import win32com.client
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        # exportData：不需要时必须传 VT_DISPATCH 空 VARIANT，直传 None 会报"类型不匹配"
        no_data = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        ok = drw.Extension.SaveAs(path, 0, _SAVEAS_SILENT, no_data,
                                  errors, warns)
    except Exception as e:
        logger.debug(f"Extension.SaveAs failed ({e}), fallback to SaveAs")
        ok = drw.SaveAs(path)
    if not ok:
        raise SWException(
            f"{label} export failed: {path}",
            error_code=ErrorCode.GEN_STEP_FAILED,
        )


def _measure_outlines(inserted: Dict[str, Any], view_names: Sequence[str],
                      task_id: str) -> Dict[str, Tuple[float, float, float, float]]:
    """视图轮廓实测（GetOutline，米）；任一失败 → SWException（禁止静默）"""
    outlines: Dict[str, Tuple[float, float, float, float]] = {}
    for name in view_names:
        try:
            outlines[name] = tuple(float(v) for v in inserted[name].GetOutline)
        except Exception as e:
            raise SWException(
                f"Cannot measure view outline for {name}: {e}",
                error_code=ErrorCode.GEN_STEP_FAILED, detail=str(e))
    return outlines


def _reposition_views(inserted: Dict[str, Any], view_names: Sequence[str],
                      positions: Dict[str, Any], drw: Any,
                      warnings: List[str]) -> None:
    """迭代重定位：每轮 实测轮廓→按增量平移→重建→复测，最多 3 轮收敛
    （真机实测：Position 设值、比例缩放绕锚点等均可能有非预期副作用，
    单次增量不一定一次到位；收敛判据 0.2mm）"""
    for _round in range(3):
        max_delta = 0.0
        for name in view_names:
            p = positions[name]
            try:
                cur = tuple(float(v) for v in inserted[name].GetOutline)
            except Exception as e:
                warnings.append(
                    f"{name}: 轮廓复测失败（{str(e)[:60]}），位置可能偏移")
                continue
            dx_m = (p["x"] + p["width"] / 2) / 1000.0 - (cur[0] + cur[2]) / 2
            dy_m = (p["y"] + p["height"] / 2) / 1000.0 - (cur[1] + cur[3]) / 2
            max_delta = max(max_delta, abs(dx_m), abs(dy_m))
            if abs(dx_m) > _POS_TOL_M or abs(dy_m) > _POS_TOL_M:
                _set_view_position(inserted[name], dx_m, dy_m, name, warnings)
        drw.ForceRebuild3(True)
        if max_delta <= _POS_TOL_M:
            break


def _final_positions(inserted: Dict[str, Any], view_names: Sequence[str],
                     outlines: Dict[str, Tuple[float, float, float, float]],
                     recomputed: Optional[Dict[str, Any]], sheet_w: float,
                     sheet_h: float, warnings: List[str]) -> Dict[str, Any]:
    """最终区域：优先实测轮廓（转 mm）；复测失败的用重算位置；
    重叠/出界检查如实 warning"""
    for name in view_names:
        try:
            outlines[name] = tuple(float(v) for v in inserted[name].GetOutline)
        except Exception as e:
            warnings.append(
                f"{name}: 最终轮廓复测失败（{str(e)[:60]}），按重算位置上报")
            del outlines[name]
    final: Dict[str, Any] = {}
    for name in view_names:
        if recomputed is not None and name not in outlines:
            final[name] = recomputed[name]
        else:
            ol = outlines[name]
            final[name] = {
                "x": round(ol[0] * 1000.0, 4), "y": round(ol[1] * 1000.0, 4),
                "width": round((ol[2] - ol[0]) * 1000.0, 4),
                "height": round((ol[3] - ol[1]) * 1000.0, 4)}
    names = list(view_names)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = final[names[i]], final[names[j]]
            if (a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
                    and a["y"] < b["y"] + b["height"]
                    and b["y"] < a["y"] + a["height"]):
                warnings.append(f"{names[i]}/{names[j]} 视图区域重叠（如实上报）")
    for name in view_names:
        p = final[name]
        if (p["x"] < 0 or p["y"] < 0 or p["x"] + p["width"] > sheet_w
                or p["y"] + p["height"] > sheet_h):
            warnings.append(f"{name}: 视图区域超出图幅边界（如实上报）")
    return final


def _dispatch_sw(sw_app: Any = None) -> Tuple[Any, bool]:
    """取 SW Application；sw_app 注入（测试）时原样返回"""
    if sw_app is not None:
        return sw_app, False
    try:
        import win32com.client
        return win32com.client.Dispatch("SldWorks.Application"), True
    except Exception as e:
        raise SWException(
            f"SolidWorks COM unavailable: {e}",
            error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            detail=str(e),
        )


def create_drawing_sync(source_file: str, view_names: Sequence[str],
                        output_dir: str, bom_rows: int = 0,
                        task_id: str = "",
                        sw_app: Any = None) -> Dict[str, Any]:
    """
    【同步/COM线程】方案B Step3：企业模板建真 SLDDRW + 三视图 + PNG 快照。

    Args:
        source_file: SW 零件/装配文件路径
        view_names: ["front","top","left"] 子集
        output_dir: step 输出目录（drawing.slddrw / snapshot.png 落盘处）
        bom_rows: BOM 估计行数（图幅选型回退路径用）
        task_id: 日志上下文
        sw_app: 注入的 SW Application（测试用）；None 时自行 Dispatch

    Returns:
        {"drawing_path", "snapshot_path", "sheet", "sheet_width",
         "sheet_height", "scale_den", "positions", "view_sizes", "warnings"}
        positions/view_sizes 单位为图纸 mm；view_sizes = 视图实际尺寸（未缩放）

    Raises:
        SWException(GEN_SW_NOT_AVAILABLE): SW 不可用/文档或工程图创建失败
        SWException(GEN_STEP_FAILED): 视图插入失败/保存失败
    """
    from pathlib import Path
    cfg = get_settings().sw
    sw_app, _own = _dispatch_sw(sw_app)

    warnings: List[str] = []
    try:
        doc = _open_doc(sw_app, source_file)
        if doc is None:
            raise SWException(
                f"Failed to open document: {source_file}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )

        # 1) 模型包围盒 → 视图尺寸（mm）；取不到走视图轮廓回退（下方）
        box = _get_model_box(doc, source_file, warnings)
        sizes: Optional[List[Tuple[str, float, float]]] = (
            _view_sizes_from_box(box, view_names) if box else None)

        # 2) 企业模板建图纸（方案B：.drwdot，图框/标题栏随模板带出）
        template = cfg.enterprise_template
        drw = sw_app.NewDocument(template, 0, 0.0, 0.0)
        if drw is None:
            raise SWException(
                f"Failed to create drawing from template: {template}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )

        # 3) 读模板实际图幅 → 布局（比例自适应图幅，禁止 1:100 失真）；
        #    读不到回退布局引擎（A3→A0 选型）
        sheet_size = _get_sheet_size_mm(drw, warnings, task_id)
        if sheet_size is not None and sizes is not None:
            sheet_w, sheet_h = sheet_size
            lay = _layout_on_sheet(sizes, sheet_w, sheet_h, task_id)
            den, positions = lay["scale_den"], lay["positions"]
            sheet = _sheet_name_of(sheet_w, sheet_h)
        else:
            lay = _compute_layout(sizes, bom_rows, task_id) if sizes is not None \
                else None
            if lay is not None:
                den, sheet, positions = (lay["scale_den"], lay["sheet"],
                                         lay["positions"])
                from app.generators.steps import step3_view_project as s3
                sheet_w, sheet_h = s3._SHEET_SIZES[sheet]
            else:
                den, sheet, positions = 1.0, "A3", None
                from app.generators.steps import step3_view_project as s3
                sheet_w, sheet_h = s3._SHEET_SIZES[sheet]

        # 4) 插入预定义视图（中文版视图名走 config；插入锚点 = 区域中心，米）
        inserted: Dict[str, Any] = {}
        for name in view_names:
            sw_view_name = cfg.predefined_view_names[name]
            if positions is not None:
                p = positions[name]
                cx_m = (p["x"] + p["width"] / 2) / 1000.0
                cy_m = (p["y"] + p["height"] / 2) / 1000.0
            else:
                pos = cfg.view_insert_positions.get(name, [0.15, 0.15])
                cx_m, cy_m = pos[0], pos[1]
            view = drw.CreateDrawViewFromModelView3(
                source_file, sw_view_name, cx_m, cy_m, 0)
            if view is None:
                raise SWException(
                    f"Failed to insert predefined view {sw_view_name} for {name}",
                    error_code=ErrorCode.GEN_STEP_FAILED,
                )
            inserted[name] = view
        drw.ForceRebuild3(True)

        # 5) 包围盒回退：用视图轮廓(米)实测尺寸 → 按实际图幅重算比例/位置
        if sizes is None:
            sizes = []
            outlines0 = _measure_outlines(inserted, view_names, task_id)
            for name in view_names:
                ol = outlines0[name]
                sizes.append((name, (ol[2] - ol[0]) * 1000.0,
                              (ol[3] - ol[1]) * 1000.0))
            lay = _layout_on_sheet(sizes, sheet_w, sheet_h, task_id)
            den, positions = lay["scale_den"], lay["positions"]
            for name in view_names:
                _set_view_scale(inserted[name], den, name, warnings)
            drw.ForceRebuild3(True)
            _reposition_views(inserted, view_names, positions, drw, warnings)
            drw.ForceRebuild3(True)
        else:
            for name in view_names:
                _set_view_scale(inserted[name], den, name, warnings)
            drw.ForceRebuild3(True)

        # 6) 隐藏线可见（虚线显示隐藏线）
        for name in view_names:
            try:
                _set_display_mode(inserted[name], _DISPLAY_HLV)
            except Exception as e:
                warnings.append(
                    f"{name}: 隐藏线显示模式设置失败（{str(e)[:60]}），如实上报")
        drw.ForceRebuild3(True)

        # 7) 视图轮廓实测（米→mm）：模型包围盒与视图实际几何范围可能不一致
        # （实测 LB26 差异显著），布局区域必须以实测轮廓为准重算第一角位置
        outlines = _measure_outlines(inserted, view_names, task_id)
        measured = [(n, (outlines[n][2] - outlines[n][0]) * 1000.0,
                     (outlines[n][3] - outlines[n][1]) * 1000.0)
                    for n in view_names]
        from app.generators.steps import step3_view_project as s3
        # measured 已是图纸 mm（1:den 缩放后），_first_angle_positions 内部会再除
        # den —— 此处必须传 den=1.0 避免双重缩放（2026-08-01 集成实测根因）
        positions2 = s3._first_angle_positions(measured, 1.0, sheet_w, sheet_h)
        logger.info(f"[Task:{task_id}] step3 measured outlines(mm): "
                    f"{ {n: [round(v*1000,1) for v in outlines[n]] for n in outlines} }")
        logger.info(f"[Task:{task_id}] step3 recomputed positions2: {positions2}")
        if positions2 is not None:
            _reposition_views(inserted, view_names, positions2, drw, warnings)
        else:
            logger.warning(f"[Task:{task_id}] step3: measured layout overflows "
                           f"{sheet} at 1:{den:g}, positions may overlap")
            warnings.append(
                f"实测视图轮廓超出 {sheet} 图幅布局能力，视图可能重叠/出界（如实上报）")
        positions = _final_positions(inserted, view_names, outlines,
                                     positions2, sheet_w, sheet_h, warnings)

        # 8) 保存中间 SLDDRW + PNG 真图快照（静默）→ 关文档
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        slddrw_path = str(out / "drawing.slddrw")
        snapshot_path = str(out / "snapshot.png")
        _save_as(drw, slddrw_path, warnings, "SLDDRW")
        _save_as(drw, snapshot_path, warnings, "PNG snapshot")
        logger.info(f"[Task:{task_id}] SW native drawing done -> {slddrw_path} "
                    f"(sheet={sheet}, scale=1:{den:g}, views={view_names})")
        # view_sizes：视图实际尺寸（未缩放 mm）= 图纸轮廓 × den
        view_sizes = {n: {"width": round((outlines[n][2] - outlines[n][0])
                                         * 1000.0 * den, 4),
                          "height": round((outlines[n][3] - outlines[n][1])
                                          * 1000.0 * den, 4)}
                      for n in outlines}
        return {"drawing_path": slddrw_path,
                "snapshot_path": snapshot_path,
                "sheet": sheet,
                "sheet_width": sheet_w, "sheet_height": sheet_h,
                "scale_den": den,
                "positions": positions,
                "view_sizes": view_sizes,
                "warnings": warnings}
    finally:
        try:
            sw_app.CloseAllDocuments(True)
        except Exception as e:
            logger.warning(f"CloseAllDocuments failed: {e}")


def finalize_drawing_sync(slddrw_path: str, properties: Dict[str, str],
                          output_dir: str, task_id: str = "",
                          sw_app: Any = None) -> Dict[str, Any]:
    """
    【同步/COM线程】方案B Step7：在 Step3 的 SLDDRW 上收尾。

    打开 Step3 图纸（静默、可写）→ CustomPropertyManager 写标题栏自定义属性
    （$PRPSHEET 链接随模板自动回填；值为空字符串的属性跳过不写）
    → Extension.SaveAs 另存 SLDDRW/DWG/PDF + PNG 终图快照 → 关文档。

    Args:
        slddrw_path: Step3 产出的中间 SLDDRW 路径
        properties: 标题栏属性 {属性名: 值}（调用方组装，取不到的留空）
        output_dir: step 输出目录（drawing.slddrw/dwg/pdf/final_snapshot.png）
        task_id: 日志上下文
        sw_app: 注入的 SW Application（测试用）

    Returns:
        {"slddrw_path", "dwg_path", "pdf_path", "final_snapshot_path",
         "properties_applied", "warnings"}

    Raises:
        SWException(GEN_SW_NOT_AVAILABLE): SW 不可用/图纸打不开
        SWException(GEN_STEP_FAILED): 另存失败
    """
    from pathlib import Path
    sw_app, _own = _dispatch_sw(sw_app)

    warnings: List[str] = []
    try:
        # 可写打开（要改自定义属性）；Silent 必带
        drw = _open_doc(sw_app, slddrw_path, read_only=False)
        if drw is None:
            raise SWException(
                f"Failed to open drawing: {slddrw_path}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )

        # 1) 标题栏自定义属性：Set2 覆盖已定义属性；不存在则 Add3 追加。
        # 单个属性失败只 warning（如实），不阻断收尾（其他属性/导出继续）
        applied: List[str] = []
        try:
            cpm = drw.Extension.CustomPropertyManager("")
        except Exception as e:
            cpm = None
            warnings.append(f"CustomPropertyManager 不可用（{str(e)[:60]}），"
                            f"标题栏属性未写入（如实上报）")
        for name, value in (properties or {}).items():
            if value is None or str(value) == "":
                continue  # 调用方诚实留空；跳过不写，禁止编造
            if cpm is None:
                break
            ok = False
            try:
                # Set2 返回 bool（已定义属性覆盖写入）
                ok = bool(cpm.Set2(name, str(value)))
            except Exception:
                ok = False
            if not ok:
                try:
                    # Add3(FieldName, FieldType=30 文本, FieldValue, Overwrite=2 yes)
                    cpm.Add3(name, 30, str(value), 2)
                    ok = True
                except Exception as e:
                    warnings.append(f"标题栏属性 {name} 写入失败"
                                    f"（{str(e)[:60]}），如实上报")
            if ok:
                applied.append(name)
        drw.ForceRebuild3(True)

        # 2) 另存 SLDDRW/DWG/PDF + PNG 终图快照（全部静默）
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        final_slddrw = str(out / "drawing.slddrw")
        dwg_path = str(out / "drawing.dwg")
        pdf_path = str(out / "drawing.pdf")
        png_path = str(out / "final_snapshot.png")
        _save_as(drw, final_slddrw, warnings, "SLDDRW")
        _save_as(drw, dwg_path, warnings, "DWG")
        _save_as(drw, pdf_path, warnings, "PDF")
        _save_as(drw, png_path, warnings, "PNG snapshot")
        logger.info(f"[Task:{task_id}] drawing finalized -> {final_slddrw} "
                    f"(dwg/pdf/png alongside, props={applied})")
        return {"slddrw_path": final_slddrw,
                "dwg_path": dwg_path,
                "pdf_path": pdf_path,
                "final_snapshot_path": png_path,
                "properties_applied": applied,
                "warnings": warnings}
    finally:
        try:
            sw_app.CloseAllDocuments(True)
        except Exception as e:
            logger.warning(f"CloseAllDocuments failed: {e}")
