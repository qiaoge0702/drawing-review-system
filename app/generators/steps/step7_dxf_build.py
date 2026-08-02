"""
Step 7: 图纸收尾（方案B重写 2026-08-02：SW 原生真图纸，取代 ezdxf 拼装 DXF）

技术路线（SW API 原生优先铁律；ezdxf 拼装 DXF 全部逻辑已删除）：
- 输入 Step3-6 检查点；在 Step3 的中间 SLDDRW 上继续（sw_drawing.finalize_drawing_sync）：
  OpenDoc6(静默可写) → CustomPropertyManager 写标题栏自定义属性
  （企业模板标题栏 $PRPSHEET 链接自动回填）→ Extension.SaveAs 另存
  SLDDRW/DWG/PDF → PNG 终图快照
- 标题栏字段（如实原则：取不到留空 + warnings，禁止编造）：
  * 图号/名称：Step2 顶层 BOM 首项（path stem / name）
  * 材料：Step2 materials 唯一材料直填，多材料（焊合惯例）→ "见明细表"
  * 重量：Step2 BOM 单件 mass×数量 求和（kg，3 位小数）；无数值 → 留空
  * 比例：Step3 实际 scale（禁止写死）
- Step4/5/6 本期仍是旧执行器产物（后续里程碑重写），本步不消费其数据
- 检查点输出新增 slddrw_path/dwg_path/pdf_path/final_snapshot_path

异常红线：缺 Step3 产物或 drawing_path → SWException；COM/另存失败 →
SWException 上抛，禁止静默失败。
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.generators.models import StepContext
from app.generators.sw_com import run_sw
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# 标题栏字段 → 企业模板自定义属性名（spike 001 S-3 实证：模板自定义属性
# Number/Description/Material/Weight/公司名称 存在，$PRP 数据源可用）
_TITLE_PROPERTY_MAP = {
    "drawing_number": "Number",
    "name": "Description",
    "material": "Material",
    "weight": "Weight",
    "scale": "Scale",
}


def _load_upstream(ctx: StepContext, step_no: int, filenames: Tuple[str, ...],
                   required: bool, label: str) -> Optional[Dict[str, Any]]:
    """
    获取前序步骤产物：优先内存 previous_results[step_no]，
    回退 output/<filename> 检查点（同 step4/5 统一模式）。
    required=True 且缺失 → SWException；否则 warning 返回 None（降级）
    """
    upstream = ctx.previous_results.get(step_no)
    if isinstance(upstream, dict) and upstream:
        return upstream

    for name in filenames:
        checkpoint = ctx.get_output_path(name)
        if checkpoint.exists():
            try:
                data = json.loads(checkpoint.read_text(encoding="utf-8"))
            except Exception as e:
                raise SWException(
                    f"Failed to parse {label} checkpoint: {checkpoint}: {e}",
                    error_code=ErrorCode.GEN_STEP_FAILED,
                    task_id=ctx.task_id,
                    step=ctx.step,
                    detail=str(e),
                )
            if isinstance(data, dict) and data:
                logger.info(f"[Task:{ctx.task_id}] step7 loaded {label} "
                            f"from checkpoint {checkpoint}")
                return data

    if required:
        raise SWException(
            f"Step7 requires Step{step_no} {label} result "
            f"(previous_results[{step_no}] or output/{filenames[0]})",
            error_code=ErrorCode.GEN_STEP_FAILED,
            task_id=ctx.task_id,
            step=ctx.step,
        )
    logger.warning(f"[Task:{ctx.task_id}] step7 {label} missing, degraded")
    return None


def title_block_info(geometry: Optional[Dict[str, Any]],
                     views_data: Optional[Dict[str, Any]] = None,
                     warnings: Optional[List[str]] = None) -> Dict[str, str]:
    """标题栏：图号/名称取 Step2 顶层 bom；比例=Step3 实际 scale（禁止写死）；
    材料唯一直填/多材料→"见明细表"；重量=BOM mass×数量求和 kg（3 位小数）。
    取不到一律留空（诚实原则，禁止编造）"""
    info = {"drawing_number": "", "name": "", "scale": "",
            "material": "", "weight": ""}
    for view in (views_data or {}).get("views") or []:
        scale = view.get("scale")
        if isinstance(scale, str) and scale.strip():
            info["scale"] = scale.strip()
            break
    if not isinstance(geometry, dict):
        if warnings is not None:
            warnings.append("Step2 几何数据缺失，标题栏图号/名称/材料/重量留空（如实上报）")
        return info
    bom = geometry.get("bom") or []
    if bom and isinstance(bom[0], dict):
        from pathlib import Path
        path = str(bom[0].get("path") or "")
        if path:
            info["drawing_number"] = Path(path).stem
        info["name"] = str(bom[0].get("name") or "")
    materials = geometry.get("materials") or {}
    if isinstance(materials, dict) and materials:
        # 兼容两种形态：{件名: 材料}（值有效）/ {材料: 计数}（键有效）
        vals = {str(v).strip() for v in materials.values()
                if isinstance(v, str) and v.strip()}
        if not vals:
            vals = {str(k).strip() for k in materials
                    if isinstance(k, str) and k.strip()}
        if len(vals) == 1:
            info["material"] = next(iter(vals))
        elif len(vals) > 1:
            info["material"] = "见明细表"
    # 重量：BOM 单件 mass(kg) × quantity 求和；无数值 → 留空 + warning
    total = 0.0
    counted = 0
    for item in bom:
        if not isinstance(item, dict) or item.get("is_suppressed"):
            continue
        mass = item.get("mass")
        if not isinstance(mass, (int, float)):
            continue
        qty = item.get("quantity")
        total += float(mass) * (float(qty) if isinstance(qty, (int, float))
                                and qty > 0 else 1.0)
        counted += 1
    if counted:
        info["weight"] = f"{total:.3f}"
    elif warnings is not None:
        warnings.append("Step2 BOM 无可用单件质量，标题栏重量留空（如实上报）")
    return info


class DxfBuildExecutor:
    """
    Step 7 执行器: 图纸收尾（方案B重写；类名/接口签名不变）

    输入: ctx.previous_results[3]/output/views.json（必需，含 drawing_path）；
          ctx.previous_results[2]/output/geometry.json（可选，标题栏信息来源）
    输出: {"title_block": {...}, "slddrw_path", "dwg_path", "pdf_path",
           "final_snapshot_path", "properties_applied", "warnings"?}，
          落盘 output/drawing.slddrw / drawing.dwg / drawing.pdf /
          final_snapshot.png
    异常: 缺 Step3 产物或 drawing_path / COM 失败 → SWException 上抛
    """

    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        views_data = _load_upstream(ctx, 3, ("views.json",), required=True,
                                    label="views")
        geometry = _load_upstream(ctx, 2, ("geometry.json", "bom.json"),
                                  required=False, label="geometry(title info)")

        warnings: List[str] = []
        drawing_path = (views_data or {}).get("drawing_path")
        if not drawing_path:
            raise SWException(
                "Step7 requires Step3 drawing_path (方案B中间 SLDDRW)；"
                "Step3 检查点缺失或为旧 DXF 契约，请重跑 Step3",
                error_code=ErrorCode.GEN_STEP_FAILED,
                task_id=ctx.task_id,
                step=ctx.step,
            )

        title = title_block_info(geometry, views_data, warnings)
        properties = {_TITLE_PROPERTY_MAP[k]: v for k, v in title.items()}

        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)

        from app.generators import sw_drawing  # 延迟导入，无 SW 环境可加载本模块
        try:
            logger.info(f"[Task:{ctx.task_id}] SW native drawing finalize: "
                        f"{drawing_path}")
            fin = await run_sw(sw_drawing.finalize_drawing_sync,
                               drawing_path, properties, str(output_dir),
                               ctx.task_id)
        except SWException:
            raise
        except Exception as e:
            logger.exception(f"[Task:{ctx.task_id}] drawing finalize failed: {e}")
            raise SWException(
                f"drawing finalize failed: {e}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
                task_id=ctx.task_id,
                step=ctx.step,
                detail=str(e),
            )

        warnings.extend(fin.get("warnings") or [])
        for w in warnings:
            logger.warning(f"[Task:{ctx.task_id}] step7: {w}")

        result: Dict[str, Any] = {
            "title_block": title,
            "slddrw_path": fin["slddrw_path"],
            "dwg_path": fin["dwg_path"],
            "pdf_path": fin["pdf_path"],
            "final_snapshot_path": fin["final_snapshot_path"],
            "properties_applied": fin.get("properties_applied") or [],
        }
        if warnings:
            result["warnings"] = warnings
        logger.info(f"[Task:{ctx.task_id}] drawing finalized -> "
                    f"{result['slddrw_path']} (+dwg/pdf/png)")
        return result
