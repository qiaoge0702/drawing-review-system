"""
SW 原生真图纸引擎封装（COM 边界层）+ B-M1 智能骨架集成

方案B（2026-08-02，取代 DXF 线稿路线；SW API 原生优先铁律）：

  create_drawing_sync（Step3 建图纸+真视图）：
    SW模型 → OpenDoc6(只读+静默) → 读模型包围盒 → B-M1类型识别 → 视图策略
    → 布局引擎按企业模板实际图幅算比例/第一角位置（禁止 1:100 失真）
    → NewDocument(.drwdot 企业模板)
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
- B-M1集成：类型识别结果写入type_info，视图策略驱动视图组合
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import get_settings
from app.core.exceptions import SWException, ErrorCode

# B-M1 智能骨架导入
from app.generators.type_recognition import (
    PartType,
    BoundingBox,
    recognize_from_sw_model,
    to_dict as type_result_to_dict,
)
from app.generators.view_strategy import (
    ViewName,
    ViewStrategy,
    get_view_strategy,
    compute_view_sizes,
    select_scale_for_sheet,
    get_sw_view_name,
    GB_SCALE_RATIOS,
    SHEET_A3_WIDTH,
    SHEET_A3_HEIGHT,
    LAYOUT_MARGIN,
    LAYOUT_GAP_DEFAULT,
)
from app.generators.steps.step3_view_project import (
    FirstAngleLayoutEngine,
    measure_title_block_rect,
)

logger = logging.getLogger(__name__)

# swDisplayMode_e：3 = HiddenLinesVisible（虚线显示隐藏线）
_DISPLAY_HLV = 3
# swSaveAsOptions_e：1 = Silent
_SAVEAS_SILENT = 1
# 迭代重定位收敛判据（米）= 0.2mm
_POS_TOL_M = 2e-4

# 最大比例重算次数
_MAX_LAYOUT_RETRIES = 3


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


def _get_component_count(doc: Any, is_assembly: bool) -> Optional[int]:
    """获取装配体零件数（B-M1焊接小总成判定用）"""
    if not is_assembly:
        return None
    try:
        # 尝试获取组件数量
        comps = doc.GetComponents(False)
        if comps:
            return len(comps)
    except Exception as e:
        logger.debug(f"GetComponents failed: {e}")
    try:
        # 备选：通过特征树估算
        feat = doc.FirstFeature
        count = 0
        while feat:
            if feat.GetTypeName() == "Reference":
                count += 1
            feat = feat.GetNextFeature
        return count if count > 0 else None
    except Exception as e:
        logger.debug(f"Feature tree traversal failed: {e}")
    return None


def _next_scale_den(den: float) -> float:
    """GB 比例系列中 den 的下一档（更小比例），末档 → 沿用末档"""
    seq = list(GB_SCALE_RATIOS)
    try:
        i = seq.index(den)
    except ValueError:
        return den
    return float(seq[min(i + 1, len(seq) - 1)])


def _layout_fill_ratio(positions: Dict[str, Any], sheet_w: float,
                       sheet_h: float) -> float:
    """视图占图幅比 = 全部视图联合包围盒面积 / 图幅面积（老板 2026-08-02
    定调 70-85% 饱满带）"""
    if not positions:
        return 0.0
    x0 = min(p["x"] for p in positions.values())
    y0 = min(p["y"] for p in positions.values())
    x1 = max(p["x"] + p["width"] for p in positions.values())
    y1 = max(p["y"] + p["height"] for p in positions.values())
    return ((x1 - x0) * (y1 - y0)) / (sheet_w * sheet_h)


def _pick_scale_measured_impl(measured_sizes, den_cur, sheet_w, sheet_h,
                              task_id, warnings,
                              strategy: Optional[ViewStrategy] = None,
                              title_block_bbox: Optional[Tuple[float, float, float, float]] = None):
    """按实测视图尺寸（当前 1:den_cur 下的图纸 mm）从 GB 比例系列从大到小
    模拟排布，选占图幅 <=85% 的最大比例（老板 2026-08-02 定调 70-85% 饱满
    带）；<70% 如实 warning；末档仍装不下 → 截断 + warning。

    本函数无 SW 视图对象，走纯估算路径（仅供图幅/比例选型）。"""
    if strategy is None:
        strategy = get_view_strategy(PartType.PLATE)
    engine = FirstAngleLayoutEngine(
        sheet_w, sheet_h, strategy.spacing, title_block_bbox=title_block_bbox
    )
    best = None
    for n in GB_SCALE_RATIOS:
        ratio = den_cur / float(n)  # 当前 1:den_cur → 目标 1:n 的线性缩放比
        sizes_n = {name: (w * ratio, h * ratio)
                   for name, (w, h) in measured_sizes.items()}
        pos = engine.layout(sizes_n, 1.0, strategy)
        if pos is None:
            continue
        fill = _layout_fill_ratio(pos, sheet_w, sheet_h)
        if fill <= 0.85:
            if fill < 0.70:
                warnings.append(f"比例 1:{n:g} 视图占图幅 {fill:.0%} < 70%"
                                f"（更大一档装不下/超 85%，如实上报）")
            logger.info(f"[Task:{task_id}] measured scale pick 1:{n:g} "
                        f"fill={fill:.0%}")
            best = float(n)
            break
        logger.info(f"[Task:{task_id}] scale 1:{n:g} fill {fill:.0%} > 85%, "
                    f"try smaller")
    if best is None:
        warnings.append(f"实测视图尺寸在 1:{GB_SCALE_RATIOS[-1]:g} 仍装不下 "
                        f"{sheet_w:g}x{sheet_h:g}，按最小比例截断（如实上报）")
        best = float(GB_SCALE_RATIOS[-1])
    return best


def _view_sizes_from_box_b_m1(
    box: Sequence[float],
    part_type: PartType,
) -> Dict[str, Tuple[float, float]]:
    """
    B-M1: 模型包围盒(米) → 各视图尺寸(mm)（按类型策略）
    """
    # 创建BoundingBox
    bbox = BoundingBox(
        min_x=box[0] * 1000.0,
        min_y=box[1] * 1000.0,
        min_z=box[2] * 1000.0,
        max_x=box[3] * 1000.0,
        max_y=box[4] * 1000.0,
        max_z=box[5] * 1000.0,
    )
    
    strategy = get_view_strategy(part_type)
    view_sizes_raw = compute_view_sizes(bbox, strategy)
    
    # 转换为字符串键
    return {name.value: (w, h) for name, (w, h) in view_sizes_raw.items()}


def _compute_layout_b_m1(
    source_file: str,
    box: Sequence[float],
    is_assembly: bool,
    component_count: Optional[int],
    sheet_w: float,
    sheet_h: float,
    task_id: str,
    warnings: List[str],
    title_block_bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Dict[str, Any]:
    """
    B-M1 完整布局计算：类型识别 → 视图策略 → 比例选择 → 第一角布局
    """
    # 1. 类型识别
    type_result = recognize_from_sw_model(
        filename=source_file,
        sw_box=box,
        is_assembly=is_assembly,
        component_count=component_count,
    )

    logger.info(
        f"[Task:{task_id}] B-M1 type recognition: {type_result.part_type.value}, "
        f"reason: {type_result.reason}"
    )

    # 2. 获取视图策略
    strategy = get_view_strategy(type_result.part_type)

    # 3. 计算视图尺寸
    view_sizes = _view_sizes_from_box_b_m1(box, type_result.part_type)

    # 4. 选择比例（带重试）
    scale_den = GB_SCALE_RATIOS[-1]  # 默认最小比例
    positions = None
    spacing = strategy.spacing
    engine = FirstAngleLayoutEngine(
        sheet_w, sheet_h, spacing, title_block_bbox=title_block_bbox
    )

    for retry in range(_MAX_LAYOUT_RETRIES + 1):
        for den in GB_SCALE_RATIOS:
            positions = engine.layout(view_sizes, den, strategy)
            if positions is not None:
                scale_den = den
                break

        if positions is not None:
            break

        # 重算：减小间距
        if retry < _MAX_LAYOUT_RETRIES:
            spacing = max(20.0, spacing - 5.0)
            engine.spacing = spacing
            logger.warning(
                f"[Task:{task_id}] B-M1 layout retry {retry + 1}, "
                f"spacing={spacing}"
            )

    if positions is None:
        logger.error(f"[Task:{task_id}] B-M1 layout failed, using fallback")
        warnings.append("B-M1布局失败，使用回退布局（可能重叠/出界，如实上报）")
        scale_den = GB_SCALE_RATIOS[-1]
        engine.sheet_w = 1e6
        engine.sheet_h = 1e6
        positions = engine.layout(view_sizes, scale_den, strategy) or {}

    return {
        "type_info": type_result_to_dict(type_result),
        "view_sizes": view_sizes,
        "scale_den": scale_den,
        "scale": f"1:{scale_den:g}",
        "positions": positions,
        "strategy_obj": strategy,
        "strategy": {
            "part_type": type_result.part_type.value,
            "views": [v.name.value for v in strategy.views],
            "scale_mode": strategy.scale_mode,
        },
    }


def _mc(x: Any) -> Any:
    """gen_py 无参方法可能暴露为方法对象而非属性值：可调则调用取值
    （探针 exp 系列 mc() 模式，真机实证需要）"""
    return x() if callable(x) else x


def _wrap(obj: Any, iface: str) -> Any:
    """spike 001 定调：NewDocument/ActiveDoc 返回对象 typeinfo 损坏，
    CastTo 不可靠；手工 QueryInterface + 实例化 gen_py 类作为兜底 wrap"""
    try:
        import pythoncom
        import win32com.client
        from win32com.client import gencache
        gencache.EnsureModule("{83A33D31-27C5-11CE-BFD4-00400513BB57}", 0, 33, 0)
        cls = getattr(gencache.EnsureModule("{83A33D31-27C5-11CE-BFD4-00400513BB57}", 0, 33, 0), iface)
        unk = obj._oleobj_.QueryInterface(cls.CLSID, pythoncom.IID_IDispatch)
        return cls(unk)
    except Exception:
        return obj


def _get_sheet_size_mm(drw: Any, warnings: List[str],
                       task_id: str = "") -> Optional[Tuple[float, float]]:
    """读工程图当前图纸页实际尺寸（米→mm）。使用 wrap 早期绑定
    ISheet.GetProperties 读取尺寸；读不到 → None（回退布局引擎图幅）+ 如实 warning"""
    try:
        ddoc = _wrap(drw, "IDrawingDoc")
        snames = _mc(ddoc.GetSheetNames)
        if not snames:
            raise RuntimeError("no sheets")
        sheet = ddoc.Sheet(snames[0])
        props = _mc(sheet.GetProperties)
        # props = (TemplateIn?, PaperSize, ScaleIn1, ScaleIn2, FirstAngle, Width_m, Height_m)
        if isinstance(props, (tuple, list)) and len(props) >= 7:
            w_m, h_m = float(props[-2]), float(props[-1])
            if w_m > 0 and h_m > 0:
                return w_m * 1000.0, h_m * 1000.0
    except Exception as e:
        logger.debug(f"[Task:{task_id}] GetProperties failed: {e}")
    warnings.append("工程图图纸页尺寸读取失败，按布局引擎选定图幅排布（如实上报）")
    return None


def _delete_placeholder_views(drw: Any, warnings: List[str],
                              task_id: str = "") -> int:
    """删除模板自带空占位视图（缺陷2：LB26-template.drwdot 预置 4 个空
    工程图视图，渲染为空方框）。建图后、插真视图前枚举当前图纸页全部视图
    并删除（此时页面上只有模板占位视图，不会误删真视图）。
    逐个 SelectByID2(DRAWINGVIEW) + EditDelete；单个失败只 warning（如实）。"""
    deleted = 0
    try:
        ddoc = _wrap(drw, "IDrawingDoc")
        snames = _mc(ddoc.GetSheetNames)
        if not snames:
            return 0
        sheet = ddoc.Sheet(snames[0])
        views = _mc(sheet.GetViews)
    except Exception as e:
        warnings.append(f"模板占位视图枚举失败（{str(e)[:60]}），"
                        f"空视图方框可能残留（如实上报）")
        return 0
    names: List[str] = []
    for v in (views or []):
        try:
            names.append(str(_mc(v.Name)))
        except Exception:
            pass
    logger.info(f"[Task:{task_id}] template placeholder views: {names}")
    for name in names:
        try:
            try:
                # Callout 参数（第8参）必须传 VT_DISPATCH 空 VARIANT，
                # 直传 None 报"类型不匹配"（真机实证 2026-08-02）
                import pythoncom
                import win32com.client
                no_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
            except ImportError:
                no_callout = None
            ok = drw.Extension.SelectByID2(name, "DRAWINGVIEW",
                                           0, 0, 0, False, 0, no_callout, 0)
            if ok:
                drw.EditDelete()
                deleted += 1
            else:
                warnings.append(f"占位视图 {name} 选中失败，未删除（如实上报）")
        except Exception as e:
            warnings.append(f"占位视图 {name} 删除失败（{str(e)[:60]}），如实上报")
    try:
        drw.ForceRebuild3(True)
    except Exception:
        pass
    logger.info(f"[Task:{task_id}] deleted {deleted}/{len(names)} "
                f"placeholder views")
    return deleted


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


def _save_as_png(drw: Any, path: str, warnings: List[str], label: str = "PNG snapshot") -> None:
    """PNG 整图导出：先缩放到整张图纸页，再 SaveAs PNG，避免窗口/视图裁切。

    根因：Extension.SaveAs PNG 默认导出当前视图可见区域，若图纸超出窗口
    则会被裁切。导出前调用 `Extension.ViewZoomToSheet()` 让当前视图完整
    显示整张图纸页，再 SaveAs 即可得到完整 PNG。
    """
    try:
        drw.Extension.ViewZoomToSheet()
    except Exception as e:
        logger.debug(f"ViewZoomToSheet 失败 ({e})，继续 SaveAs PNG")
    _save_as(drw, path, warnings, label)


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


def _try_insert_view(
    drw: Any,
    source_file: str,
    view_name: str,
    sw_view_names: List[str],
    cx_m: float,
    cy_m: float,
    warnings: List[str],
    task_id: str,
) -> Any:
    """
    尝试插入视图（中英文环境适配，最多2次）
    
    Returns:
        视图对象，或None（插入失败）
    """
    for attempt, sw_name in enumerate(sw_view_names[:2]):  # 最多2次
        try:
            view = drw.CreateDrawViewFromModelView3(
                source_file, sw_name, cx_m, cy_m, 0)
            if view is not None:
                return view
        except Exception as e:
            logger.debug(f"[Task:{task_id}] View insert attempt {attempt} failed: {e}")
    
    warnings.append(f"{view_name}: 视图插入失败（中英文环境均不可用，≤2次），跳过")
    return None


def create_drawing_sync(
    source_file: str,
    view_names: Optional[Sequence[str]] = None,
    output_dir: str = "",
    bom_rows: int = 0,
    task_id: str = "",
    sw_app: Any = None,
    use_b_m1: bool = True,
) -> Dict[str, Any]:
    """
    【同步/COM线程】方案B Step3：企业模板建真 SLDDRW + B-M1智能布局 + 视图 + PNG 快照。

    Args:
        source_file: SW 零件/装配文件路径
        view_names: 视图名列表（B-M1模式下由策略决定，此参数忽略）
        output_dir: step 输出目录（drawing.slddrw / snapshot.png 落盘处）
        bom_rows: BOM 估计行数（图幅选型回退路径用）
        task_id: 日志上下文
        sw_app: 注入的 SW Application（测试用）；None 时自行 Dispatch
        use_b_m1: 是否使用B-M1智能布局（默认True）

    Returns:
        {"drawing_path", "snapshot_path", "sheet", "sheet_width",
         "sheet_height", "scale_den", "positions", "view_sizes", "warnings",
         "type_info"}  # B-M1新增type_info
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

        # 读取模型信息
        is_assembly = source_file.lower().endswith(".sldasm")
        box = _get_model_box(doc, source_file, warnings)
        component_count = _get_component_count(doc, is_assembly) if is_assembly else None

        # 1) 企业模板建图纸（只建一次；读实际图幅 → B-M1 布局）
        template = cfg.enterprise_template
        drw = sw_app.NewDocument(template, 0, 0.0, 0.0)
        if drw is None:
            raise SWException(
                f"Failed to create drawing from template: {template}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )

        # 1b) 删除模板自带空占位视图（渲染为空方框），再插真视图
        _delete_placeholder_views(drw, warnings, task_id)

        # 1c) 读模板实际图幅；读不到回退 A3 常量
        sheet_size = _get_sheet_size_mm(drw, warnings, task_id)
        if sheet_size is not None:
            sheet_w, sheet_h = sheet_size
        else:
            sheet_w, sheet_h = SHEET_A3_WIDTH, SHEET_A3_HEIGHT
        sheet = _sheet_name_of(sheet_w, sheet_h)

        # 1d) 实测标题栏禁放区（一次传入布局引擎）
        title_block_bbox = measure_title_block_rect(drw, sheet_w, sheet_h)

        # 2) B-M1 布局计算
        if use_b_m1 and box:
            # B-M1 布局计算
            layout_result = _compute_layout_b_m1(
                source_file=source_file,
                box=box,
                is_assembly=is_assembly,
                component_count=component_count,
                sheet_w=sheet_w,
                sheet_h=sheet_h,
                task_id=task_id,
                warnings=warnings,
                title_block_bbox=title_block_bbox,
            )

            type_info = layout_result["type_info"]
            view_sizes_data = layout_result["view_sizes"]
            den = layout_result["scale_den"]
            positions = layout_result["positions"]
            strategy = layout_result["strategy_obj"]
            actual_view_names = list(positions.keys()) if positions else ["front"]

            logger.info(
                f"[Task:{task_id}] B-M1 layout: type={type_info['type']}, "
                f"scale=1:{den}, views={actual_view_names}"
            )
        else:
            # 回退到旧逻辑
            type_info = {"type": "unknown", "reason": "B-M1 disabled or no box"}
            if box:
                dx = (box[3] - box[0]) * 1000.0
                dy = (box[4] - box[1]) * 1000.0
                dz = (box[5] - box[2]) * 1000.0
                view_sizes_data = {
                    "front": (dx, dz),
                    "top": (dx, dy),
                    "left": (dy, dz),
                }
            else:
                view_sizes_data = {}

            actual_view_names = list(view_names) if view_names else ["front", "top", "left"]
            den = 10.0
            positions = None
            strategy = get_view_strategy(PartType.PLATE)
        inserted: Dict[str, Any] = {}
        for view_name in actual_view_names:
            # 获取SW视图名（中英文）
            view_enum = None
            try:
                view_enum = ViewName(view_name)
            except ValueError:
                view_enum = ViewName.FRONT
            
            sw_names = [get_sw_view_name(view_enum, 0), get_sw_view_name(view_enum, 1)]
            sw_names = [n for n in sw_names if n]  # 过滤None
            
            # 获取位置
            if positions and view_name in positions:
                p = positions[view_name]
                cx_m = (p["x"] + p["width"] / 2) / 1000.0
                cy_m = (p["y"] + p["height"] / 2) / 1000.0
            else:
                # 默认位置
                pos = cfg.view_insert_positions.get(view_name, [0.15, 0.15])
                cx_m, cy_m = pos[0], pos[1]
            
            # 插入视图
            view = _try_insert_view(
                drw, source_file, view_name, sw_names,
                cx_m, cy_m, warnings, task_id
            )
            
            if view is not None:
                inserted[view_name] = view
            else:
                # 轴测图失败不阻塞
                if view_name == "isometric":
                    logger.warning(f"[Task:{task_id}] Isometric view skipped")
                else:
                    raise SWException(
                        f"Failed to insert view {view_name}",
                        error_code=ErrorCode.GEN_STEP_FAILED,
                    )
        
        drw.ForceRebuild3(True)

        # 5) 设置视图比例（估算比例，第 5b 步按实测重选）
        iso_den = den  # 轴测图比例分母（5b 步可能调小一档）
        for name, view in inserted.items():
            _set_view_scale(view, den, name, warnings)
        drw.ForceRebuild3(True)

        # 5b) 实测驱动修正（2026-08-03 老板验收：估算尺寸与 SW 实际视图轴向
        #     可能不一致——本模型长度沿 Z、Y 向上，估算轴向映射不可靠）：
        #     实测轮廓 → 俯视图竖放（深>宽）则旋转90°横放（参照 LB26 参考图
        #     俯视横放主视正下方）→ 按实测尺寸重选比例（占图幅≤85%的最大比例，
        #     低于 70% 如实 warning）→ 重设比例 → 重排 → 重定位
        if inserted:
            import math
            # 保留 5b 前有效布局：万一重排失败，禁止空 positions 跳过重定位
            pre_positions = positions
            outlines0 = _measure_outlines(inserted, list(inserted.keys()), task_id)
            for name in list(inserted.keys()):
                if name != "top":
                    continue
                o = outlines0[name]
                w_mm = (o[2] - o[0]) * 1000.0
                h_mm = (o[3] - o[1]) * 1000.0
                if h_mm > w_mm:  # 俯视图竖放 → 旋转 90° 横放
                    try:
                        inserted[name].Angle = math.pi / 2
                        logger.info(f"[Task:{task_id}] top view rotated 90° "
                                    f"({w_mm:.0f}x{h_mm:.0f} -> landscape)")
                    except Exception as e:
                        warnings.append(f"top: 俯视图旋转90°失败（{str(e)[:60]}），"
                                        f"按竖放排布（如实上报）")
            drw.ForceRebuild3(True)
            # 按实测尺寸重选比例（老板定调：占图幅 70-85% 的最大比例）
            outlines0 = _measure_outlines(inserted, list(inserted.keys()), task_id)
            measured_sizes = {
                n: ((o[2] - o[0]) * 1000.0, (o[3] - o[1]) * 1000.0)
                for n, o in outlines0.items()
            }
            den0 = den
            den = _pick_scale_measured_impl(
                measured_sizes, den0, sheet_w, sheet_h, task_id, warnings,
                strategy=strategy, title_block_bbox=title_block_bbox,
            )
            if abs(den - den0) > 1e-9:
                logger.info(f"[Task:{task_id}] scale re-picked by measured "
                            f"outlines: 1:{den0:g} -> 1:{den:g}")
                for name, view in inserted.items():
                    _set_view_scale(view, den, name, warnings)
                drw.ForceRebuild3(True)
                outlines0 = _measure_outlines(inserted, list(inserted.keys()),
                                              task_id)
                measured_sizes = {
                    n: ((o[2] - o[0]) * 1000.0, (o[3] - o[1]) * 1000.0)
                    for n, o in outlines0.items()
                }
            # 5b-2) 用新布局引擎按实测轮廓重排（传入 SW 视图对象走实测流程）
            engine = FirstAngleLayoutEngine(
                sheet_w, sheet_h, strategy.spacing,
                title_block_bbox=title_block_bbox,
            )
            try:
                # 将图纸尺寸还原为模型尺寸（引擎按 scale_den 缩放）
                model_sizes = {
                    n: (w * den, h * den)
                    for n, (w, h) in measured_sizes.items()
                }
                positions = engine.layout(
                    model_sizes, den, strategy,
                    view_objects=inserted, drawing=drw,
                )
                iso_den = den
                # 引擎内部可能因压标题栏而调小轴测比例，回读实际值
                if "isometric" in inserted:
                    try:
                        iso_den = round(1.0 / float(inserted["isometric"].ScaleDecimal))
                    except Exception:
                        pass
            except SWException:
                # 逃生口修复：按 GB 比例序列继续往小比例降档重试
                seq = list(GB_SCALE_RATIOS)
                start = seq.index(den) if den in seq else 0
                positions = None
                for try_den in seq[start + 1:]:
                    for name, view in inserted.items():
                        _set_view_scale(view, try_den, name, warnings)
                    drw.ForceRebuild3(True)
                    outlines0 = _measure_outlines(inserted,
                                                  list(inserted.keys()), task_id)
                    measured_sizes = {
                        n: ((o[2] - o[0]) * 1000.0, (o[3] - o[1]) * 1000.0)
                        for n, o in outlines0.items()
                    }
                    model_sizes = {
                        n: (w * try_den, h * try_den)
                        for n, (w, h) in measured_sizes.items()
                    }
                    try:
                        positions = engine.layout(
                            model_sizes, try_den, strategy,
                            view_objects=inserted, drawing=drw,
                        )
                        den = try_den
                        iso_den = try_den
                        if "isometric" in inserted:
                            try:
                                iso_den = round(1.0 / float(inserted["isometric"].ScaleDecimal))
                            except Exception:
                                pass
                        logger.info(f"[Task:{task_id}] measured layout "
                                    f"fallback OK: 1:{den:g}")
                        break
                    except SWException:
                        continue
                if positions is None:
                    warnings.append(
                        f"实测轮廓在最小比例 1:{GB_SCALE_RATIOS[-1]:g} 仍超出 "
                        f"{sheet} 布局能力，保留最后一次有效布局（如实上报）")
                    positions = pre_positions or {}

        # 6) 隐藏线可见
        for name, view in inserted.items():
            try:
                _set_display_mode(view, _DISPLAY_HLV)
            except Exception as e:
                warnings.append(
                    f"{name}: 隐藏线显示模式设置失败（{str(e)[:60]}），如实上报")
        drw.ForceRebuild3(True)

        # 7) 迭代重定位（真机实测：插入点/比例缩放绕锚点有非预期副作用，
        #    按 实测轮廓→增量平移→重建→复测 收敛到布局位置）→ 最终实测
        if inserted:
            if positions:
                _reposition_views(inserted, list(inserted.keys()),
                                  positions, drw, warnings)
                drw.ForceRebuild3(True)
            outlines = _measure_outlines(inserted, list(inserted.keys()), task_id)
            positions = _final_positions(
                inserted, list(inserted.keys()), outlines,
                positions, sheet_w, sheet_h, warnings
            )
        else:
            outlines = {}
            positions = {}

        # 8) 保存中间 SLDDRW + PNG
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        slddrw_path = str(out / "drawing.slddrw")
        snapshot_path = str(out / "snapshot.png")
        _save_as(drw, slddrw_path, warnings, "SLDDRW")
        _save_as_png(drw, snapshot_path, warnings, "PNG snapshot")
        
        logger.info(
            f"[Task:{task_id}] SW native drawing done -> {slddrw_path} "
            f"(sheet={sheet}, scale=1:{den:g}, views={list(inserted.keys())})"
        )
        
        # view_sizes：视图实际尺寸（未缩放 mm）= 图纸轮廓 × 各自比例分母
        # （轴测图比主视小一档，见 5b 步 iso_den）
        view_sizes_result = {}
        for name in inserted.keys():
            if name in outlines:
                ol = outlines[name]
                d = iso_den if name == "isometric" else den
                view_sizes_result[name] = {
                    "width": round((ol[2] - ol[0]) * 1000.0 * d, 4),
                    "height": round((ol[3] - ol[1]) * 1000.0 * d, 4),
                }
        
        return {
            "drawing_path": slddrw_path,
            "snapshot_path": snapshot_path,
            "sheet": sheet,
            "sheet_width": sheet_w,
            "sheet_height": sheet_h,
            "scale_den": den,
            "positions": positions,
            "view_sizes": view_sizes_result,
            "warnings": warnings,
            "type_info": type_info,  # B-M1新增
        }
    finally:
        try:
            sw_app.CloseAllDocuments(True)
        except Exception as e:
            logger.warning(f"CloseAllDocuments failed: {e}")


def _sheet_name_of(sheet_w: float, sheet_h: float) -> str:
    """实际图幅尺寸(mm) → GB 图幅名"""
    for name, (w, h) in _SHEET_SIZES.items():
        if abs(sheet_w - w) < 1.0 and abs(sheet_h - h) < 1.0:
            return name
    return f"{sheet_w:g}x{sheet_h:g}"


# 图幅尺寸表（用于_sheet_name_of）
_SHEET_SIZES = {
    "A3": (SHEET_A3_WIDTH, SHEET_A3_HEIGHT),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}


def _read_model_mass_kg(model_doc: Any, warnings: List[str],
                        task_id: str = "") -> Optional[float]:
    """读模型质量（kg，IModelDocExtension.CreateMassProperty → Mass，单位 kg）。
    取不到 → None + 如实 warning（禁止编造）。"""
    try:
        mp = _mc(model_doc.Extension.CreateMassProperty)
        mass = float(_mc(mp.Mass))
        if mass > 0:
            return mass
    except Exception as e:
        logger.debug(f"[Task:{task_id}] CreateMassProperty failed: {e}")
    warnings.append("模型质量读取失败（CreateMassProperty 不可用），"
                    "标题栏重量留空（如实上报）")
    return None


def finalize_drawing_sync(slddrw_path: str, properties: Dict[str, str],
                          model_path: str, output_dir: str, task_id: str = "",
                          sw_app: Any = None) -> Dict[str, Any]:
    """
    【同步/COM线程】方案B Step7：在 Step3 的 SLDDRW 上收尾。

    缺陷3修复（step7 属性键已改 `"质量"`（老板实证模板绑 `$PRPSHEET:{质量}`），保留勿回退）：
      1) OpenDoc6 打开模型（opts=1 Silent 可写；内存改属性，绝不保存模型文件）
      2) CustomPropertyManager 写模型级自定义属性（中文名，值由调用方组装）
      3) 打开 Step3 图纸 → ForceRebuild3 → $PRPSHEET 自动带出标题栏
      4) Extension.SaveAs 另存 SLDDRW/DWG/PDF + PNG 终图快照
      5) CloseAllDocuments(True)：内存修改随关闭丢弃，模型文件不受污染

    Args:
        slddrw_path: Step3 产出的中间 SLDDRW 路径
        properties: 模型级自定义属性 {中文属性名: 值}（取不到的留空跳过）
        model_path: 视图引用的模型文件路径（$PRPSHEET 数据源）；空则跳过
                    模型属性写入 + 如实 warning
        output_dir: step 输出目录（drawing.slddrw/dwg/pdf/final_snapshot.png）
        task_id: 日志上下文
        sw_app: 注入的 SW Application（测试用）

    Returns:
        {"slddrw_path", "snapshot_path", "properties_applied",
         "properties_readback", "warnings"}
        （B-M1 修复：返回键统一为 snapshot_path，与 step7_dxf_build 执行器一致）

    Raises:
        SWException(GEN_SW_NOT_AVAILABLE): SW 不可用/图纸打不开
        SWException(GEN_STEP_FAILED): 另存失败
    """
    from pathlib import Path
    sw_app, _own = _dispatch_sw(sw_app)

    warnings: List[str] = []
    applied: List[str] = []
    readback: Dict[str, str] = {}
    try:
        # 1) 模型级自定义属性（$PRPSHEET 数据源）
        props = {k: str(v) for k, v in (properties or {}).items()
                 if v is not None and str(v) != ""}
        if model_path:
            model_doc = _open_doc(sw_app, model_path, read_only=False)
            if model_doc is None:
                raise SWException(
                    f"Failed to open model: {model_path}",
                    error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
                )
            # 重量回退：调用方取不到时，从模型 MassProperty 实测（kg）
            if "质量" not in props or not props["质量"]:
                mass = _read_model_mass_kg(model_doc, warnings, task_id)
                if mass is not None:
                    props["质量"] = f"{mass:.3f}"
                    logger.info(f"[Task:{task_id}] model mass readback: "
                                f"{mass:.3f} kg")
            try:
                mcpm = model_doc.Extension.CustomPropertyManager("")
            except Exception as e:
                mcpm = None
                warnings.append(f"模型 CustomPropertyManager 不可用"
                                f"（{str(e)[:60]}），标题栏属性未写入（如实上报）")
            for name, value in props.items():
                if mcpm is None:
                    break
                ok = False
                try:
                    ok = bool(mcpm.Set2(name, value))
                except Exception:
                    ok = False
                if not ok:
                    try:
                        mcpm.Add3(name, 30, value, 2)
                        ok = True
                    except Exception as e:
                        warnings.append(f"模型属性 {name} 写入失败"
                                        f"（{str(e)[:60]}），如实上报")
                if ok:
                    applied.append(name)
            # 验证写入
            if mcpm is not None:
                try:
                    wcpm = _wrap(mcpm, "ICustomPropertyManager")
                    for name in applied:
                        for getter in (lambda: wcpm.Get3(name, True),
                                       lambda: wcpm.Get2(name),
                                       lambda: wcpm.Get5(name, True)):
                            try:
                                r = getter()
                            except Exception:
                                continue
                            vals = r if isinstance(r, (tuple, list)) else (r,)
                            hit = next((str(v) for v in vals
                                        if isinstance(v, str) and v), None)
                            if hit:
                                readback[name] = hit
                                break
                except Exception:
                    pass
            if len(readback) != len(applied):
                warnings.append(f"模型属性写入后验证：{len(readback)}/{len(applied)} 项可回读"
                                f"（$PRPSHEET 标题栏可能带不出，如实上报）")
        else:
            warnings.append("缺少模型路径，$PRPSHEET 标题栏属性未写入（如实上报）")

        # 2) 打开 Step3 图纸，重建让 $PRPSHEET 解析带出标题栏
        drw = _open_doc(sw_app, slddrw_path, read_only=False)
        if drw is None:
            raise SWException(
                f"Failed to open drawing: {slddrw_path}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )
        drw.ForceRebuild3(True)

        # 3) 另存 SLDDRW/DWG/PDF + PNG
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        final_slddrw = str(out / "drawing.slddrw")
        png_path = str(out / "snapshot.png")
        _save_as(drw, final_slddrw, warnings, "SLDDRW")
        _save_as_png(drw, png_path, warnings, "PNG snapshot")
        logger.info(f"[Task:{task_id}] drawing finalized -> {final_slddrw} "
                    f"(skeleton slddrw + snapshot, props={applied}, "
                    f"readback={readback})")
        return {"slddrw_path": final_slddrw,
                "snapshot_path": png_path,
                "properties_applied": applied,
                "properties_readback": readback,
                "warnings": warnings}
    finally:
        try:
            sw_app.CloseAllDocuments(True)
        except Exception as e:
            logger.warning(f"CloseAllDocuments failed: {e}")


def export_final_sync(slddrw_path: str, output_dir: str, task_id: str = "",
                      sw_app: Any = None) -> Dict[str, Any]:
    """
    【同步/COM线程】Step4 完成后终版全格式导出：SLDDRW→DWG/PDF/PNG。

    Args:
        slddrw_path: 终版 SLDDRW 路径（如 step4_final.slddrw）
        output_dir: 导出文件落盘目录
        task_id: 日志上下文
        sw_app: 注入的 SW Application（测试用）

    Returns:
        {"slddrw_path", "dwg_path", "pdf_path", "snapshot_path", "warnings"}
    """
    from pathlib import Path
    sw_app, _own = _dispatch_sw(sw_app)
    warnings: List[str] = []

    try:
        drw = _open_doc(sw_app, slddrw_path, read_only=False)
        if drw is None:
            raise SWException(
                f"Failed to open drawing for export: {slddrw_path}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )
        drw.ForceRebuild3(True)

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        dwg_path = str(out / "drawing.dwg")
        pdf_path = str(out / "drawing.pdf")
        png_path = str(out / "final_snapshot.png")
        _save_as(drw, dwg_path, warnings, "DWG")
        _save_as(drw, pdf_path, warnings, "PDF")
        _save_as_png(drw, png_path, warnings, "PNG snapshot")
        logger.info(f"[Task:{task_id}] final export done -> dwg/pdf/png in {output_dir}")
        return {
            "slddrw_path": slddrw_path,
            "dwg_path": dwg_path,
            "pdf_path": pdf_path,
            "snapshot_path": png_path,
            "warnings": warnings,
        }
    finally:
        try:
            sw_app.CloseAllDocuments(True)
        except Exception as e:
            logger.warning(f"CloseAllDocuments failed: {e}")
