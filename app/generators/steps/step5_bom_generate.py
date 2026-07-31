"""
Step 5: BOM 生成

M2 范围（规则驱动，非 AI）：
- 输入 Step2 产物 bom（组件树递归展开：同一零件可能出现多次）：
  * is_suppressed=True 的组件排除
  * 同图号组件聚合，数量累加
- 列映射（契约 docs/plans/04-二维生成可视化模块.md 第四节）：
  * 序号：聚合后行号（从 1 开始）
  * 图号：path 文件名去扩展名优先，回退 name
  * 名称：组件 name
  * 数量：聚合后总数
  * 材料/单重：Step2 无单件数据 → 留空字符串（诚实原则，禁止编造）
  * 总重：单重×数量（单重空则空）
  * 备注：name 含 "GB/T" → "外购"（外购件识别）
- 排序：装配件（非外购）在前、标准件（外购）在后，同级按图号字典序
- position/style：契约默认值，可由 ctx.parameters["bom_config"] 覆盖
  （非法值显式报错，与 step4 _parse_dimension_config 同款模式）

纯数据处理，不依赖 SW COM，可在无 SW 环境单测。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from app.generators.models import StepContext
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# 契约固定列
_COLUMNS = ["序号", "图号", "名称", "数量", "材料", "单重", "总重", "备注"]

# position/style 契约默认值
_DEFAULT_POSITION = {"x": 50.0, "y": 600.0, "width": 400.0, "height": 200.0}
_DEFAULT_STYLE = {
    "header_height": 20.0,
    "row_height": 15.0,
    "font_size": 3.5,
    "border_width": 0.25,
}

# 外购件（标准件）识别标记
_PURCHASED_TAG = "GB/T"
_PURCHASED_REMARK = "外购"


def _parse_bom_config(ctx: StepContext) -> Dict[str, Any]:
    """解析并校验 bom_config 参数（position/style 覆盖），非法值显式报错"""
    cfg = ctx.parameters.get("bom_config") or {}
    if not isinstance(cfg, dict):
        raise SWException(
            f"bom_config must be a dict, got {type(cfg).__name__}",
            error_code=ErrorCode.GEN_INVALID_FILE,
            task_id=ctx.task_id,
            step=ctx.step,
        )

    position = dict(_DEFAULT_POSITION)
    pos_override = cfg.get("position")
    if pos_override is not None:
        if not isinstance(pos_override, dict):
            raise SWException(
                f"bom_config.position must be a dict, got {type(pos_override).__name__}",
                error_code=ErrorCode.GEN_INVALID_FILE,
                task_id=ctx.task_id,
                step=ctx.step,
            )
        for key in ("x", "y", "width", "height"):
            if key not in pos_override:
                continue
            val = pos_override[key]
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise SWException(
                    f"bom_config.position.{key} must be a number, got {val!r}",
                    error_code=ErrorCode.GEN_INVALID_FILE,
                    task_id=ctx.task_id,
                    step=ctx.step,
                )
            position[key] = float(val)

    style = dict(_DEFAULT_STYLE)
    style_override = cfg.get("style")
    if style_override is not None:
        if not isinstance(style_override, dict):
            raise SWException(
                f"bom_config.style must be a dict, got {type(style_override).__name__}",
                error_code=ErrorCode.GEN_INVALID_FILE,
                task_id=ctx.task_id,
                step=ctx.step,
            )
        for key in _DEFAULT_STYLE:
            if key not in style_override:
                continue
            val = style_override[key]
            if not isinstance(val, (int, float)) or isinstance(val, bool) or val <= 0:
                raise SWException(
                    f"bom_config.style.{key} must be a positive number, got {val!r}",
                    error_code=ErrorCode.GEN_INVALID_FILE,
                    task_id=ctx.task_id,
                    step=ctx.step,
                )
            style[key] = float(val)

    return {"position": position, "style": style}


def _load_bom(ctx: StepContext) -> Dict[str, Any]:
    """获取 Step2 产物：优先内存 previous_results[2]，回退 output/geometry.json 检查点"""
    upstream = ctx.previous_results.get(2)
    if isinstance(upstream, dict) and upstream.get("bom"):
        return upstream

    for name in ("geometry.json", "step2_geometry.json"):
        checkpoint = ctx.get_output_path(name)
        if checkpoint.exists():
            try:
                data = json.loads(checkpoint.read_text(encoding="utf-8"))
            except Exception as e:
                raise SWException(
                    f"Failed to parse geometry checkpoint: {checkpoint}: {e}",
                    error_code=ErrorCode.GEN_STEP_FAILED,
                    task_id=ctx.task_id,
                    step=ctx.step,
                    detail=str(e),
                )
            if isinstance(data, dict) and data.get("bom"):
                logger.info(f"[Task:{ctx.task_id}] step5 loaded bom from checkpoint {checkpoint}")
                return data

    raise SWException(
        "Step5 requires Step2 bom result (previous_results[2] or output/geometry.json)",
        error_code=ErrorCode.GEN_STEP_FAILED,
        task_id=ctx.task_id,
        step=ctx.step,
    )


def _extract_drawing_number(item: Dict[str, Any]) -> str:
    """图号提取：path 文件名去扩展名优先，回退 name"""
    path = item.get("path") or ""
    if path:
        stem = Path(str(path)).stem.strip()
        if stem:
            return stem
    return str(item.get("name") or "").strip()


def _is_purchased(name: str) -> bool:
    return _PURCHASED_TAG in name


def aggregate_bom(bom: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    BOM 聚合（纯数据，可单测）

    - 排除 is_suppressed=True 组件
    - 按图号聚合，数量累加
    - 排序：装配件在前、外购件在后，同级按图号字典序
    - 材料/单重：Step2 无单件数据 → 空字符串；单重空 → 总重空

    Returns: 未加序号的行列表 [{"drawing_number", "name", "quantity", "remark"}, ...]
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for item in bom:
        if not isinstance(item, dict):
            logger.warning(f"[step5] skip non-dict bom entry: {item!r}")
            continue
        if item.get("is_suppressed"):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            logger.warning(f"[step5] skip bom entry with empty name: {item!r}")
            continue
        drawing_number = _extract_drawing_number(item)
        qty = item.get("quantity", 1)
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            logger.warning(f"[step5] invalid quantity {qty!r} for '{name}', treat as 1")
            qty = 1

        group = groups.get(drawing_number)
        if group is None:
            groups[drawing_number] = {
                "drawing_number": drawing_number,
                "name": name,
                "quantity": qty,
                "purchased": _is_purchased(name),
            }
        else:
            group["quantity"] += qty
            if _is_purchased(name):
                group["purchased"] = True

    rows = sorted(groups.values(),
                  key=lambda g: (g["purchased"], g["drawing_number"]))
    return rows


class BomGenerateExecutor:
    """
    Step 5 执行器: BOM 生成

    输入: ctx.previous_results[2]（Step2 内存结果）或 output/geometry.json 检查点；
          ctx.parameters["bom_config"]（可选：position / style 覆盖）
    输出: {"bom_table": {columns, rows, position, style},
           "source_total_items": int}，落盘 output/bom.json
    异常: 缺 Step2 输入 / 聚合后空表 → SWException（禁止静默返回空数据）
    """

    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        cfg = _parse_bom_config(ctx)
        geometry = _load_bom(ctx)
        bom = geometry["bom"]

        logger.info(f"[Task:{ctx.task_id}] Generating BOM from {len(bom)} source items")

        aggregated = aggregate_bom(bom)
        if not aggregated:
            raise SWException(
                "BOM aggregation produced empty table (all items suppressed or invalid)",
                error_code=ErrorCode.GEN_STEP_FAILED,
                task_id=ctx.task_id,
                step=ctx.step,
            )

        # 诚实原则：Step2 无单件材料/单重数据 → 空字符串；单重空 → 总重空
        unit_weight: Any = ""
        material: Any = ""
        rows: List[List[Any]] = []
        for idx, g in enumerate(aggregated, start=1):
            total_weight = round(unit_weight * g["quantity"], 4) if unit_weight != "" else ""
            remark = _PURCHASED_REMARK if g["purchased"] else ""
            rows.append([
                idx,
                g["drawing_number"],
                g["name"],
                g["quantity"],
                material,
                unit_weight,
                total_weight,
                remark,
            ])

        result: Dict[str, Any] = {
            "bom_table": {
                "columns": list(_COLUMNS),
                "rows": rows,
                "position": cfg["position"],
                "style": cfg["style"],
            },
            # 质量门禁对齐：Step2 原始条目数，便于 BOM 一致性核对
            "source_total_items": len(bom),
        }

        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        bom_file = output_dir / "bom.json"
        with open(bom_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"[Task:{ctx.task_id}] BOM generated: {len(rows)} rows "
                    f"(source {len(bom)} items) -> {bom_file}")
        return result
