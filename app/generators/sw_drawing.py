"""
SW 原生导出 DXF 引擎封装（COM 边界层）

方案（2026-08-01 老板批示，取代逐边提取；SW API 原生优先铁律）：
    SW模型 → OpenDoc6(只读+静默) → 读模型包围盒 → 复用 step3 布局引擎
    （比例/图幅/第一角位置）→ NewDocument(对应图幅模板)
    → CreateDrawViewFromModelView3 × N（预定义视图，插入位置=布局坐标）
    → SetDisplayMode3 隐藏线可见 + 设比例 → Extension.SaveAs 导出 DXF(静默)
    → CloseAllDocuments
共 7 类 COM 调用，无逐边提取；DXF 解析归一化见 view_extractor.parse_exported_dxf。

纪律：
- 一切 COM 调用经 sw_com.run_sw 排队到 COM 线程（本模块只提供同步函数）
- 文档用完 CloseAllDocuments(True)；禁止杀 SW 进程
- 失败/异常禁止静默：warnings 如实上报；视图插入失败 → SWException
- SW 单位 = 米；图纸坐标换算米制；layout 引擎输出 mm → 插入时 /1000
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import get_settings
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

_VIEW_DISPLAY = {"front": "主视图", "top": "俯视图", "left": "左视图"}
# swDisplayMode_e：3 = HiddenLinesVisible（虚线显示隐藏线）
_DISPLAY_HLV = 3
# swSaveAsOptions_e：1 = Silent
_SAVEAS_SILENT = 1


def _open_doc(sw_app: Any, path: str) -> Any:
    """OpenDoc6(零件/装配自动识别)；晚期绑定用 VARIANT 取错误码，
    早期绑定对象 byref 参数直接传 int（VARIANT 会 TypeError，回退直传）"""
    doc_type = 2 if path.lower().endswith((".sldasm",)) else 1
    # options: 2=ReadOnly | 1=Silent(抑制“旧版本文件”等模态提示框，防 COM 挂死)
    _OPTS = 3
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


def _set_display_mode(view: Any, mode: int) -> Any:
    """SetDisplayMode3（晚期绑定真机实测可用）；SetDisplayMode4 两种绑定下
    均报"非选择性的参数"（2026-07-31 复测），仅作兜底尝试"""
    try:
        return view.SetDisplayMode3(False, mode, False, False)
    except Exception:
        return view.SetDisplayMode4(False, mode, False, False)


def _set_view_scale(view: Any, den: float, name: str, warnings: List[str]) -> None:
    """设视图比例 = 1/den（ScaleDecimal 晚期绑定属性写）；失败如实 warning"""
    try:
        view.ScaleDecimal = 1.0 / den
    except Exception as e:
        warnings.append(f"{name}: 视图比例设置失败（{str(e)[:60]}），按模板默认比例导出")


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


def _save_as_dxf(drw: Any, dxf_path: str, warnings: List[str]) -> None:
    """Extension.SaveAs 静默导出 DXF；失败回退 SaveAs，仍失败 → 抛异常"""
    try:
        import pythoncom
        import win32com.client
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        # exportData：不需要时必须传 VT_DISPATCH 空 VARIANT，直传 None 会报"类型不匹配"
        no_data = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        ok = drw.Extension.SaveAs(dxf_path, 0, _SAVEAS_SILENT, no_data,
                                  errors, warns)
    except Exception as e:
        logger.debug(f"Extension.SaveAs failed ({e}), fallback to SaveAs")
        ok = drw.SaveAs(dxf_path)
    if not ok:
        raise SWException(
            f"DXF export failed: {dxf_path}",
            error_code=ErrorCode.GEN_STEP_FAILED,
        )


def export_dxf_sync(source_file: str, view_names: Sequence[str],
                    output_dir: str, bom_rows: int = 0,
                    task_id: str = "",
                    sw_app: Any = None) -> Dict[str, Any]:
    """
    【同步/COM线程】SW 原生导出 DXF 全流程。

    Args:
        source_file: SW 零件/装配文件路径
        view_names: ["front","top","left"] 子集
        output_dir: step 输出目录（raw_export.dxf 落盘处）
        bom_rows: BOM 估计行数（图幅选型用）
        task_id: 日志上下文
        sw_app: 注入的 SW Application（测试用）；None 时自行 Dispatch

    Returns:
        {"dxf_path", "sheet", "scale_den", "positions", "warnings"}
        positions = 各视图实际插入位置（图纸 mm，x/y 为区域左下角）

    Raises:
        SWException(GEN_SW_NOT_AVAILABLE): SW 不可用/文档或工程图创建失败
        SWException(GEN_STEP_FAILED): 视图插入失败/DXF 导出失败
    """
    cfg = get_settings().sw
    own_app = sw_app is None
    if own_app:
        try:
            import win32com.client
            sw_app = win32com.client.Dispatch("SldWorks.Application")
        except Exception as e:
            raise SWException(
                f"SolidWorks COM unavailable: {e}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
                detail=str(e),
            )

    warnings: List[str] = []
    try:
        doc = _open_doc(sw_app, source_file)
        if doc is None:
            raise SWException(
                f"Failed to open document: {source_file}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )

        # 1) 模型包围盒 → 视图尺寸（mm）
        box = _get_model_box(doc, source_file, warnings)
        sizes: Optional[List[Tuple[str, float, float]]] = (
            _view_sizes_from_box(box, view_names) if box else None)

        # 2) 布局决策需要 sizes；包围盒取不到时先按 1:1 占位，
        #    插入后用视图轮廓实测再重算（下方回退分支）
        den = 1.0
        sheet = "A3"
        positions: Optional[Dict[str, Any]] = None
        if sizes is not None:
            layout = _compute_layout(sizes, bom_rows, task_id)
            den, sheet, positions = (layout["scale_den"], layout["sheet"],
                                     layout["positions"])

        # 3) 按图幅选模板（缺省 gb_a3）
        template = cfg.drawing_templates.get(sheet) or cfg.drawing_template
        drw = sw_app.NewDocument(template, 0, 0.0, 0.0)
        if drw is None:
            raise SWException(
                f"Failed to create drawing from template: {template}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )

        # 4) 插入预定义视图（插入锚点 = 区域中心，SW API 用米）
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

        # 5) 包围盒回退：用视图轮廓(米)实测尺寸 → 重算布局 → 设比例并重定位
        if sizes is None:
            sizes = []
            for name in view_names:
                try:
                    ol = inserted[name].GetOutline  # [minx,miny,maxx,maxy] 米
                    sizes.append((name, (ol[2] - ol[0]) * 1000.0,
                                  (ol[3] - ol[1]) * 1000.0))
                except Exception as e:
                    raise SWException(
                        f"Cannot measure view outline for {name}: {e}",
                        error_code=ErrorCode.GEN_STEP_FAILED,
                        detail=str(e),
                    )
            layout = _compute_layout(sizes, bom_rows, task_id)
            den, positions = layout["scale_den"], layout["positions"]
            if layout["sheet"] != sheet:
                warnings.append(
                    f"视图轮廓回退重算图幅 {layout['sheet']} 与已建工程图 {sheet} "
                    f"不一致，按 {sheet} 导出（如实上报）")
            for name in view_names:
                _set_view_scale(inserted[name], den, name, warnings)
            drw.ForceRebuild3(True)
            # 比例生效后复测轮廓，按增量平移到目标位置
            for name in view_names:
                p = positions[name]
                try:
                    ol = tuple(float(v) for v in inserted[name].GetOutline)
                except Exception as e:
                    warnings.append(
                        f"{name}: 轮廓复测失败（{str(e)[:60]}），位置可能偏移")
                    continue
                dx_m = (p["x"] + p["width"] / 2) / 1000.0 - (ol[0] + ol[2]) / 2
                dy_m = (p["y"] + p["height"] / 2) / 1000.0 - (ol[1] + ol[3]) / 2
                _set_view_position(inserted[name], dx_m, dy_m, name, warnings)
            drw.ForceRebuild3(True)
        else:
            for name in view_names:
                _set_view_scale(inserted[name], den, name, warnings)
            drw.ForceRebuild3(True)

        # 6) 隐藏线可见（虚线显示）。真机实测（2026-08-01 probe_hlv/hlr）：
        # SW2025 DXF 导出忽略视图显示模式，HLR/HLV 导出内容一致且全部落在
        # layer 0 / Continuous —— 隐藏线无法经 DXF 线型获得，如实 warning 由
        # 解析层上报（hidden_lines 空列表，禁止编造）。
        for name in view_names:
            try:
                _set_display_mode(inserted[name], _DISPLAY_HLV)
            except Exception as e:
                warnings.append(
                    f"{name}: 隐藏线显示模式设置失败（{str(e)[:60]}），如实上报")
        drw.ForceRebuild3(True)

        # 7) 视图轮廓实测（GetOutline，米→mm）：模型包围盒与视图实际几何范围
        # 可能不一致（实测 LB26 差异显著），布局区域必须以实测轮廓为准，
        # 否则实体区域分配会大量丢失。实测尺寸重算第一角位置后重定位视图。
        measured: List[Tuple[str, float, float]] = []
        outlines: Dict[str, Tuple[float, float, float, float]] = {}
        for name in view_names:
            try:
                ol = tuple(float(v) for v in inserted[name].GetOutline)
                outlines[name] = ol
                measured.append((name, (ol[2] - ol[0]) * 1000.0,
                                 (ol[3] - ol[1]) * 1000.0))
            except Exception as e:
                raise SWException(
                    f"Cannot measure view outline for {name}: {e}",
                    error_code=ErrorCode.GEN_STEP_FAILED, detail=str(e))
        from app.generators.steps import step3_view_project as s3
        sw_mm, sh_mm = s3._SHEET_SIZES[sheet]
        # measured 已是图纸 mm（1:den 缩放后），_first_angle_positions 内部会再除
        # den —— 此处必须传 den=1.0 避免双重缩放（2026-08-01 集成实测根因）
        positions2 = s3._first_angle_positions(measured, 1.0, sw_mm, sh_mm)
        logger.info(f"[Task:{task_id}] step3 measured outlines(mm): "
                    f"{ {n: [round(v*1000,1) for v in outlines[n]] for n in outlines} }")
        logger.info(f"[Task:{task_id}] step3 recomputed positions2: {positions2}")
        if positions2 is not None:
            # 迭代重定位：每轮 实测轮廓→按增量平移→重建→复测，最多 3 轮收敛
            # （真机实测：Position 设值、比例缩放绕锚点等均可能有非预期副作用，
            #  单次增量不一定一次到位；收敛判据 0.2mm）
            for _round in range(3):
                max_delta = 0.0
                for name in view_names:
                    p = positions2[name]
                    try:
                        cur = tuple(float(v) for v in inserted[name].GetOutline)
                    except Exception as e:
                        warnings.append(
                            f"{name}: 轮廓复测失败（{str(e)[:60]}），位置可能偏移")
                        continue
                    dx_m = (p["x"] + p["width"] / 2) / 1000.0 - (cur[0] + cur[2]) / 2
                    dy_m = (p["y"] + p["height"] / 2) / 1000.0 - (cur[1] + cur[3]) / 2
                    max_delta = max(max_delta, abs(dx_m), abs(dy_m))
                    if abs(dx_m) > 2e-4 or abs(dy_m) > 2e-4:
                        _set_view_position(inserted[name], dx_m, dy_m,
                                           name, warnings)
                drw.ForceRebuild3(True)
                if max_delta <= 2e-4:
                    break
            for name in view_names:
                try:
                    outlines[name] = tuple(
                        float(v) for v in inserted[name].GetOutline)
                except Exception as e:
                    warnings.append(
                        f"{name}: 最终轮廓复测失败（{str(e)[:60]}），按重算位置上报")
                    del outlines[name]
        else:
            logger.warning(f"[Task:{task_id}] step3: measured layout overflows "
                           f"{sheet} at 1:{den:g}, positions may overlap")
            warnings.append(
                f"实测视图轮廓超出 {sheet} 图幅布局能力，视图可能重叠/出界（如实上报）")

        # 最终区域：优先实测轮廓（转 mm）；复测失败的用重算位置
        final_positions: Dict[str, Any] = {}
        for name in view_names:
            if positions2 is not None and name not in outlines:
                final_positions[name] = positions2[name]
            else:
                ol = outlines[name]
                final_positions[name] = {
                    "x": round(ol[0] * 1000.0, 4), "y": round(ol[1] * 1000.0, 4),
                    "width": round((ol[2] - ol[0]) * 1000.0, 4),
                    "height": round((ol[3] - ol[1]) * 1000.0, 4)}
        # 重叠/出界检查（如实 warning，不静默）
        names = list(view_names)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = final_positions[names[i]], final_positions[names[j]]
                if (a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
                        and a["y"] < b["y"] + b["height"]
                        and b["y"] < a["y"] + a["height"]):
                    warnings.append(
                        f"{names[i]}/{names[j]} 视图区域重叠（如实上报）")
        for name in view_names:
            p = final_positions[name]
            if (p["x"] < 0 or p["y"] < 0 or p["x"] + p["width"] > sw_mm
                    or p["y"] + p["height"] > sh_mm):
                warnings.append(f"{name}: 视图区域超出图幅边界（如实上报）")
        positions = final_positions

        # 8) 导出 DXF（静默）→ 关文档
        from pathlib import Path
        dxf_path = str(Path(output_dir) / "raw_export.dxf")
        _save_as_dxf(drw, dxf_path, warnings)
        logger.info(f"[Task:{task_id}] SW native DXF export done -> {dxf_path} "
                    f"(sheet={sheet}, scale=1:{den:g}, views={view_names})")
        return {"dxf_path": dxf_path, "sheet": sheet, "scale_den": den,
                "positions": positions, "warnings": warnings}
    finally:
        try:
            sw_app.CloseAllDocuments(True)
        except Exception as e:
            logger.warning(f"CloseAllDocuments failed: {e}")
