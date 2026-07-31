"""
SW 工程图视图引擎封装（COM 边界层）

架构（docs/SW-API侦察报告-2026-07-30 已定，不得更改）：
    SW模型 → 新建工程图(gb_a3模板) → 插入预定义视图(*前视/*俯视/*左视)
    → ForceRebuild3 → GetVisibleEntities2(comp, 1) 读 silhouette 边
    → view_extractor 判别类型/取参数/手动矩阵转 2D → 契约视图字典

纪律：
- 一切 COM 调用经 sw_com.run_sw 排队到 COM 线程（本模块只提供同步函数）
- 文档用完 CloseAllDocuments(True)
- 隐藏线：尝试类型码 0/2 + 显示模式切换；取不到如实写入 warnings，禁止静默降级
- 装配体：先 ResolveAllLightWeightComponents(True)，逐组件 GetVisibleComponents 读取
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import get_settings
from app.core.exceptions import SWException, ErrorCode
from app.generators.view_extractor import extract_view_entities, bounding_box_of

logger = logging.getLogger(__name__)

_VIEW_DISPLAY = {"front": "主视图", "top": "俯视图", "left": "左视图"}


def _open_doc(sw_app: Any, path: str) -> Any:
    """OpenDoc6(零件/装配自动识别)；pywin32 可用时用 VARIANT 取错误码"""
    doc_type = 2 if path.lower().endswith((".sldasm",)) else 1
    try:
        import pythoncom
        import win32com.client
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        return sw_app.OpenDoc6(path, doc_type, 2, "", errors, warnings)
    except ImportError:
        return sw_app.OpenDoc6(path, doc_type, 2, "", 0, 0)


def _try_hidden_entities(view: Any, drw: Any, comp: Any) -> Tuple[Optional[List[Any]], str]:
    """
    隐藏线攻关：依次尝试 GetVisibleEntities2 类型码 0/2，
    再切换视图显示模式为 HIDDEN_LINES_GRAYED(2) 重建后重试。
    返回 (edges 或 None, 诊断信息)。
    """
    attempts: List[str] = []
    for code in (0, 2):
        try:
            r = view.GetVisibleEntities2(comp, code)
            if r:
                return list(r), f"type码{code}取到{len(r)}条"
            attempts.append(f"type码{code}返回空")
        except Exception as e:
            attempts.append(f"type码{code}异常:{str(e)[:80]}")
    # 显示模式切换（SetDisplayMode3(fastHLR, displayMode, faceted, cosmeticHighQuality)）
    try:
        view.SetDisplayMode3(True, 2, False, False)
        drw.ForceRebuild3(True)
        for code in (0, 2):
            try:
                r = view.GetVisibleEntities2(comp, code)
                if r:
                    return list(r), f"显示模式切换后type码{code}取到{len(r)}条"
            except Exception as e:
                attempts.append(f"显示模式切换后type码{code}异常:{str(e)[:80]}")
        attempts.append("显示模式切换后仍无隐藏线实体")
    except Exception as e:
        attempts.append(f"显示模式切换异常:{str(e)[:80]}")
    return None, "; ".join(attempts)


def _format_scale(scale_decimal: float) -> str:
    if not scale_decimal or scale_decimal <= 0:
        return "1:1"
    if scale_decimal >= 1:
        return f"{round(scale_decimal):g}:1"
    return f"1:{round(1 / scale_decimal):g}"


def extract_views_sync(source_file: str, view_names: Sequence[str],
                       sw_app: Any = None) -> Dict[str, Any]:
    """
    【同步/COM线程】SW 工程图视图提取全流程。

    Args:
        source_file: SW 零件/装配文件路径
        view_names: ["front","top","left"] 子集
        sw_app: 注入的 SW Application（测试用）；None 时自行 Dispatch

    Returns:
        {"views": [...], "warnings": [...]}（views 结构与契约一致，不含 layout）

    Raises:
        SWException(GEN_SW_NOT_AVAILABLE): SW 不可用/工程图创建失败
        SWException(GEN_STEP_FAILED): 视图插入失败或无任何实体
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

    try:
        doc = _open_doc(sw_app, source_file)
        if doc is None:
            raise SWException(
                f"Failed to open document: {source_file}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )
        drw = sw_app.NewDocument(cfg.drawing_template, 0, 0.0, 0.0)
        if drw is None:
            raise SWException(
                f"Failed to create drawing from template: {cfg.drawing_template}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
            )

        views: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for name in view_names:
            sw_view_name = cfg.predefined_view_names[name]
            pos = cfg.view_insert_positions.get(name, [0.15, 0.15])
            view = drw.CreateDrawViewFromModelView3(source_file, sw_view_name, pos[0], pos[1], 0)
            if view is None:
                raise SWException(
                    f"Failed to insert predefined view {sw_view_name} for {name}",
                    error_code=ErrorCode.GEN_STEP_FAILED,
                )
            drw.ForceRebuild3(True)
            # 装配体：解析轻化组件后逐组件读取
            try:
                drw.ResolveAllLightWeightComponents(True)
            except Exception as e:
                logger.debug(f"ResolveAllLightWeightComponents skipped: {e}")

            comps = view.GetVisibleComponents or []
            arr = list(view.ModelToViewTransform.ArrayData)
            scale_decimal = float(view.ScaleDecimal or 1.0)

            edges_per_comp: List[List[Any]] = []
            hidden_per_comp: List[List[Any]] = []
            for comp in comps:
                edges = view.GetVisibleEntities2(comp, 1)  # silhouette 边
                edges_per_comp.append(list(edges) if edges else [])
                hedges, diag = _try_hidden_entities(view, drw, comp)
                if hedges:
                    hidden_per_comp.append(hedges)
                else:
                    warnings.append(f"{name}: hidden_lines 取不到（{diag}）")

            entities, notes = extract_view_entities(
                edges_per_comp, arr, scale_decimal, cfg.spline_sample_points)
            if not entities:
                raise SWException(
                    f"No entities extracted for view: {name}",
                    error_code=ErrorCode.GEN_STEP_FAILED,
                )
            warnings.extend(f"{name}: {n}" for n in notes)

            hidden_entities, hnotes = extract_view_entities(
                hidden_per_comp, arr, scale_decimal, cfg.spline_sample_points)
            warnings.extend(f"{name}: hidden {n}" for n in hnotes)

            views.append({
                "name": name,
                "display_name": _VIEW_DISPLAY[name],
                "projection": "first_angle",
                "entities": entities,
                "hidden_lines": hidden_entities,
                "center_lines": [],   # 契约预留（中心线提取未立项，如实留空）
                "section_hatch": None,  # M3 剖面线预留
                "bounding_box": bounding_box_of(entities),
                "scale": _format_scale(scale_decimal),
            })
        return {"views": views, "warnings": warnings}
    finally:
        if own_app:
            try:
                sw_app.CloseAllDocuments(True)
            except Exception as e:
                logger.warning(f"CloseAllDocuments failed: {e}")
