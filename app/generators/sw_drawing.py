"""
SW 工程图视图引擎封装（COM 边界层）

架构（docs/SW-API侦察报告-2026-07-30 已定，不得更改）：
    SW模型 → 新建工程图(gb_a3模板) → 插入预定义视图(*前视/*俯视/*左视)
    → ForceRebuild3 → GetVisibleEntities2(comp, 1) 读 Edge 边（1=Edge，swViewEntityType_e）
    → view_extractor 判别类型/取参数/手动矩阵转 2D → 契约视图字典

纪律：
- 一切 COM 调用经 sw_com.run_sw 排队到 COM 线程（本模块只提供同步函数）
- 文档用完 CloseAllDocuments(True)
- 隐藏线（2026-07-31 差集法，sw_api_probe_hidden.py 真机验证 + 包2 复测修正）：
  线框(1)读 Edge(1)+SilhouetteEdge(4) 并集全集 B − HLR(2)读并集可见集 A = 隐藏边
  （隐藏边在不同会话状态下可能计入 Edge 差 6−5=1 或 Silhouette 差 6−5=1，
  并集【多重集/Counter】差集两种状态均正确——真机存在同 LineParams 轮廓线在
  线框模式下多返回一份的情况，集合差集会漏）；边身份用 view_extractor.edge_param_key
  （params3→params2→GetEndParams+Evaluate 参数链 + 圆心半径/LineParams），容差 1e-6。
  【绑定方式修正】探针结论"SetDisplayMode4 必须早期绑定"真机复测推翻：
  SetDisplayMode4 在早期/晚期绑定下均报"非选择性的参数"，已废弃的 SetDisplayMode3
  晚期绑定实测可用；且早期绑定（makepy）下边对象 GetCurveParams3/Params2 行为异常、
  OpenDoc6 返回 tuple，故保持 pkg1 的 plain Dispatch 晚期绑定。
  性能开销：每视图多 2 次显示切换 + 最多 3 次 ForceRebuild3
- 装配体：先 ResolveAllLightWeightComponents(True)，逐组件 GetVisibleComponents 读取
"""

import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import get_settings
from app.core.exceptions import SWException, ErrorCode
from app.generators.view_extractor import (
    extract_view_entities, bounding_box_of, edge_param_key,
)

logger = logging.getLogger(__name__)

_VIEW_DISPLAY = {"front": "主视图", "top": "俯视图", "left": "左视图"}

# swDisplayMode_e：线框=1（Edge 全集，含隐藏边），HLR=2（仅可见边）
_DISPLAY_WIREFRAME = 1
_DISPLAY_HLR = 2


def _open_doc(sw_app: Any, path: str) -> Any:
    """OpenDoc6(零件/装配自动识别)；晚期绑定用 VARIANT 取错误码，
    早期绑定对象 byref 参数直接传 int（VARIANT 会 TypeError，回退直传）"""
    doc_type = 2 if path.lower().endswith((".sldasm",)) else 1
    try:
        import pythoncom
        import win32com.client
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        return sw_app.OpenDoc6(path, doc_type, 2, "", errors, warnings)
    except (ImportError, TypeError):
        return sw_app.OpenDoc6(path, doc_type, 2, "", 0, 0)


def _set_display_mode(view: Any, mode: int) -> Any:
    """SetDisplayMode3 优先（真机实测晚期绑定可用）；SetDisplayMode4 两种绑定下
    均报"非选择性的参数"（2026-07-31 复测推翻探针结论），仅作兜底尝试"""
    try:
        return view.SetDisplayMode3(False, mode, False, False)
    except Exception:
        return view.SetDisplayMode4(False, mode, False, False)


def _get_display_mode(view: Any) -> Optional[int]:
    """读视图当前显示模式（GetDisplayMode3）；取不到返回 None（跳过恢复步骤）"""
    try:
        mode = view.GetDisplayMode3(True)
        if isinstance(mode, (list, tuple)):
            mode = mode[0]
        mode = int(mode)
        return mode if 1 <= mode <= 6 else None
    except Exception:
        return None


def _read_edges(view: Any, comps: Sequence[Any],
                type_codes: Sequence[int] = (1,),
                dedup: bool = False) -> List[List[Any]]:
    """
    逐组件读视图边实体（swViewEntityType_e：Edge=1，SilhouetteEdge=4）。
    默认仅 Edge(1)（可见边主实体，保持 pkg1 行为）；隐藏线差集另传 (1, 4) 取并集。
    真机实测（2026-07-31）：隐藏边在不同 SW 会话状态下可能计入 Edge 读数差
    （6−5=1）或 SilhouetteEdge 读数差（6−5=1），并集多重集差集两种状态均正确。
    dedup=True 时同一曲线的重复引用按 edge_param_key 去重（仅用于主实体路径）。
    """
    edges_per_comp: List[List[Any]] = []
    for comp in comps:
        merged: List[Any] = []
        seen: set = set()
        for type_code in type_codes:
            try:
                edges = view.GetVisibleEntities2(comp, type_code)
            except Exception as e:
                logger.debug(f"GetVisibleEntities2(type={type_code}) failed: {e}")
                continue
            for e in (edges or []):
                if dedup:
                    k = _edge_key(e)
                    if k is not None:
                        if k in seen:
                            continue
                        seen.add(k)
                merged.append(e)
        edges_per_comp.append(merged)
    return edges_per_comp


def _edge_key(edge: Any) -> Optional[Tuple[float, ...]]:
    """边身份哈希：复用 view_extractor.edge_param_key（与实体参数化同一参数来源，
    禁止另写一套）；取不到 → None（差集匹配时按不可匹配处理）"""
    return edge_param_key(edge, digits=6)


def _read_edges_with_hidden_diff(
    view: Any, drw: Any, comps: Sequence[Any]
) -> Tuple[List[List[Any]], List[List[Any]]]:
    """
    差集法隐藏边提取（sw_api_probe_hidden.py 真机验证模式）：
      ① 视图设线框(1) → 读 Edge(1)+Silhouette(4) 并集全集 B（含隐藏边）
      ② 视图设 HLR(2)  → 读并集可见集 A；Edge(1) 子集作为视图主实体（pkg1 行为不变）
      ③ 恢复视图原显示模式
    B − A = 隐藏边：多重集（Counter）差集——真机存在同 LineParams 轮廓线在
    线框模式下多返回一份的情况，集合差集会漏，必须按出现次数取超额实例。
    任何异常向上抛，由调用方降级为 warning + 按可见边继续。

    性能备注：相对单纯可见边读取，每视图多两次显示切换 + 最多三次 ForceRebuild3，
    大装配体上耗时可观。
    """
    orig_mode = _get_display_mode(view)
    try:
        _set_display_mode(view, _DISPLAY_WIREFRAME)
        drw.ForceRebuild3(True)
        wire = _read_edges(view, comps, (1, 4))
        _set_display_mode(view, _DISPLAY_HLR)
        drw.ForceRebuild3(True)
        visible_all = _read_edges(view, comps, (1, 4))
    finally:
        if orig_mode is not None:
            try:
                _set_display_mode(view, orig_mode)
                drw.ForceRebuild3(True)
            except Exception as e:
                logger.warning(f"restore display mode failed: {e}")

    # 视图主实体：仅 Edge(1)（现有可见边逻辑不变；SilhouetteEdge 在本机无端点
    # 参数可取，混入主实体会污染几何）
    visible = _read_edges(view, comps, (1,))

    hidden_per_comp: List[List[Any]] = []
    for w_edges, v_edges in zip(wire, visible_all):
        # key 取不到（None）的边不参与匹配：可见集不计入，线框集不误判为隐藏
        remaining: Counter = Counter(
            k for k in (_edge_key(e) for e in v_edges) if k is not None)
        hidden: List[Any] = []
        for e in w_edges:
            k = _edge_key(e)
            if k is None:
                continue
            if remaining.get(k, 0) > 0:
                remaining[k] -= 1
            else:
                hidden.append(e)  # 超额实例 = 隐藏边
        hidden_per_comp.append(hidden)
    return visible, hidden_per_comp


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

    # 隐藏线差集始终启用：SetDisplayMode3 晚期绑定真机实测可用（SetDisplayMode4
    # 两种绑定下均报"非选择性的参数"；早期绑定边对象参数接口行为异常，见模块 docstring）。
    # 单视图提取失败 → warning + 按可见边继续，禁止让整步失败。
    hidden_enabled = True

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
            scale_decimal = float(view.ScaleDecimal or 1.0)  # 仅用于 scale 字段展示，不参与实体坐标

            # 隐藏线差集：线框全集 − HLR 可见集；失败 → warning + 按可见边继续（不阻塞）
            if hidden_enabled:
                try:
                    edges_per_comp, hidden_per_comp = _read_edges_with_hidden_diff(
                        view, drw, comps)
                except Exception as e:
                    logger.warning(f"{name}: hidden diff extraction failed: {e}")
                    warnings.append(
                        f"{name}: hidden_lines 提取失败（{str(e)[:80]}），按可见边继续")
                    edges_per_comp = _read_edges(view, comps)
                    hidden_per_comp = []
            else:
                edges_per_comp = _read_edges(view, comps)
                hidden_per_comp = []

            entities, notes = extract_view_entities(
                edges_per_comp, arr, cfg.spline_sample_points)
            if not entities:
                raise SWException(
                    f"No entities extracted for view: {name}",
                    error_code=ErrorCode.GEN_STEP_FAILED,
                )
            warnings.extend(f"{name}: {n}" for n in notes)

            hidden_entities, hnotes = extract_view_entities(
                hidden_per_comp, arr, cfg.spline_sample_points)
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
