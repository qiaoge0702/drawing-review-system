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
  * 材料/单重：Step2 bom 条目的 material/mass（真机 SW COM 提取，kg）；
    聚合时取同图号首见非空值；取不到 → 空字符串（诚实原则，禁止编造）
  * 总重：单重×数量，kg 保留 3 位小数（单重空则空）
  * 备注：name 含标准件前缀（GB/T、JB/T、HG/T、Q/ 等，见 _PURCHASED_PREFIXES）
    → "外购"（外购件识别）
- 排序：装配件（非外购）在前、标准件（外购）在后，同级按图号字典序
- position/style：契约默认值，可由 ctx.parameters["bom_config"] 覆盖
  （非法值显式报错，与 step4 _parse_dimension_config 同款模式）
  * position 默认图幅内定位（图纸坐标，原点图框左下角，Y 向上，mm）：
    标题栏正上方、右对齐图框（标题栏位于图框右下角，高 40mm 从 y=10 起，
    BOM 表底边贴标题栏顶边 y=50）；height 由 executor 按实际行数动态覆盖
    （header_height + rows*row_height），config 给的静态 height 不生效

纯数据处理，不依赖 SW COM，可在无 SW 环境单测。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from app.generators.models import StepContext
from app.generators.steps.step3_view_project import _SHEET_SIZES
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# 契约固定列
_COLUMNS = ["序号", "图号", "名称", "数量", "材料", "单重", "总重", "备注"]

# position/style 契约默认值
# position：图幅内定位——标题栏正上方、右对齐图框（图纸坐标 mm，A3 横向有效
# 范围 [10,410]×[10,287]）；height 为占位值，executor 按实际行数动态覆盖
_DEFAULT_POSITION = {"x": 240.0, "y": 50.0, "width": 160.0, "height": 200.0}
_DEFAULT_STYLE = {
    "header_height": 20.0,
    "row_height": 15.0,
    "font_size": 3.5,
    "border_width": 0.25,
}
# 列宽（mm，与 _COLUMNS 8 列一一对应）与文字对齐
_DEFAULT_COLUMN_WIDTHS = [15.0, 45.0, 45.0, 15.0, 20.0, 20.0, 20.0, 20.0]
_DEFAULT_TEXT_ALIGN = "left"

# 外购件（标准件）识别前缀集合（GB/T、JB/T、HG/T、企业标准 Q/ 等）
_PURCHASED_PREFIXES = ("GB/T", "GB╱T", "JB/T", "JB╱T", "HG/T", "Q/")
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
    style["column_widths"] = list(_DEFAULT_COLUMN_WIDTHS)
    style["text_align"] = _DEFAULT_TEXT_ALIGN
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
        if "column_widths" in style_override:
            cw = style_override["column_widths"]
            if (not isinstance(cw, (list, tuple))
                    or len(cw) != len(_COLUMNS)
                    or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                           or v <= 0 for v in cw)):
                raise SWException(
                    f"bom_config.style.column_widths must be a positive number array "
                    f"of length {len(_COLUMNS)}, got {cw!r}",
                    error_code=ErrorCode.GEN_INVALID_FILE,
                    task_id=ctx.task_id,
                    step=ctx.step,
                )
            style["column_widths"] = [float(v) for v in cw]
        if "text_align" in style_override:
            ta = style_override["text_align"]
            if not isinstance(ta, str) or not ta.strip():
                raise SWException(
                    f"bom_config.style.text_align must be a non-empty str, got {ta!r}",
                    error_code=ErrorCode.GEN_INVALID_FILE,
                    task_id=ctx.task_id,
                    step=ctx.step,
                )
            style["text_align"] = ta.strip()

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
    return any(tag in name for tag in _PURCHASED_PREFIXES)


def aggregate_bom(bom: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    BOM 聚合（纯数据，可单测）

    - 排除 is_suppressed=True 组件
    - 按图号聚合，数量累加
    - 排序：装配件在前、外购件在后，同级按图号字典序
    - 材料/单重：取同图号首见非空值；单重空 → 总重空

    Returns: 未加序号的行列表 [{drawing_number, name, quantity, purchased,
             material, mass}, ...]（mass 为 float kg 或 ""）
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
        material = str(item.get("material") or "").strip()
        mass = item.get("mass")
        mass = float(mass) if isinstance(mass, (int, float)) and not isinstance(mass, bool) else ""
        if group is None:
            groups[drawing_number] = {
                "drawing_number": drawing_number,
                "name": name,
                "quantity": qty,
                "purchased": _is_purchased(name),
                "material": material,
                "mass": mass,
            }
        else:
            group["quantity"] += qty
            if _is_purchased(name):
                group["purchased"] = True
            # 同图号首见非空值优先；后续非空值与首个不一致 → 告警（取首个）
            if not group["material"] and material:
                group["material"] = material
            elif group["material"] and material and material != group["material"]:
                logger.warning(
                    f"[step5] 同图号 {drawing_number} 材料不一致: "
                    f"'{group['material']}' vs '{material}'，取首见非空值")
            if group["mass"] == "" and mass != "":
                group["mass"] = mass
            elif group["mass"] != "" and mass != "" and mass != group["mass"]:
                logger.warning(
                    f"[step5] 同图号 {drawing_number} 单重不一致: "
                    f"{group['mass']} vs {mass} kg，取首见非空值")

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

        # 材料/单重：Step2 真机数据，缺失 → 空字符串（诚实原则）；总重 = 单重×数量
        rows: List[List[Any]] = []
        for idx, g in enumerate(aggregated, start=1):
            material = g["material"]
            unit_weight: Any = round(g["mass"], 3) if g["mass"] != "" else ""
            total_weight: Any = (
                round(unit_weight * g["quantity"], 3) if unit_weight != "" else "")
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

        # 图幅适配：sheet 取 Step3 layout.sheet_size（缺省 A3）；默认定位右对齐图框
        layout = (ctx.previous_results.get(3) or {}).get("layout") or {}
        sheet_w, sheet_h = _SHEET_SIZES.get(
            layout.get("sheet_size"), _SHEET_SIZES["A3"])
        position, style = cfg["position"], cfg["style"]
        pos_override = (ctx.parameters.get("bom_config") or {}).get("position") or {}
        if "x" not in pos_override:
            position["x"] = sheet_w - 20.0 - position["width"]
        warnings: List[str] = []
        # 表体不得超出图幅有效区（仅默认定位自动适配；覆盖定位由调用方负责）：
        # 先压缩行高（下限 font×1.2），仍超限 → 截断；均如实写入 warnings
        max_h = sheet_h - 10.0 - position["y"]
        if "x" not in pos_override and "y" not in pos_override:
            height = style["header_height"] + style["row_height"] * len(rows)
            if height > max_h:
                min_row_h = round(style["font_size"] * 1.2, 4)
                fit_row_h = (max_h - style["header_height"]) / len(rows)
                if fit_row_h >= min_row_h:
                    warnings.append(
                        f"BOM 行高压缩 {style['row_height']}→{round(fit_row_h, 4)}mm "
                        f"以适配 {layout.get('sheet_size', 'A3')} 图幅")
                    style["row_height"] = round(fit_row_h, 4)
                else:
                    keep = max(1, int((max_h - style["header_height"]) // min_row_h))
                    warnings.append(
                        f"BOM {len(rows)} 行超出图幅有效区，截断为 {keep} 行 "
                        f"（行高下限 {min_row_h}mm）")
                    style["row_height"] = min_row_h
                    rows = rows[:keep]
        # 列宽：图号/名称列按内容长度换算（汉字≈font，ASCII≈0.55×font），超出当前
        # 份额时加宽（Step7 归一化到表宽），禁止静默压穿
        cw = list(style["column_widths"])
        font = style["font_size"]
        total_cw = sum(cw)
        width = position["width"]

        def _text_w(s: Any) -> float:
            return sum(font if ord(c) > 127 else 0.55 * font for c in str(s)) + 2.0

        for ci, header in ((1, "图号"), (2, "名称")):
            need = max([_text_w(header)] + [_text_w(r[ci]) for r in rows])
            allotted = cw[ci] / total_cw * width
            if need > allotted:
                cw[ci] = need * total_cw / width
                warnings.append(f"{header}列加宽至内容所需 {round(need, 2)}mm")
        style["column_widths"] = [round(v, 4) for v in cw]

        for w in warnings:
            logger.warning(f"[Task:{ctx.task_id}] step5: {w}")

        # BOM 高度按实际行数动态计算（此处 rows>=1，空表已在前面抛错），
        # 覆盖 config 给的静态 height，保证表格始终贴在标题栏正上方
        position["height"] = (
            style["header_height"] + style["row_height"] * len(rows)
        )

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
        if warnings:
            result["warnings"] = warnings

        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        bom_file = output_dir / "bom.json"
        with open(bom_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"[Task:{ctx.task_id}] BOM generated: {len(rows)} rows "
                    f"(source {len(bom)} items) -> {bom_file}")
        return result
