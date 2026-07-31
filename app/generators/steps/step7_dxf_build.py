"""
Step 7: DXF 构建

坐标系约定（2026-07-31 老板确认，专业范式：模型空间 --scale/平移--> 图纸空间）：
- Step3 entities 为视图局部坐标（原点 = 视图包围盒左下角，实际尺寸 mm）
- Step3 layout.view_positions 为图纸坐标（A3 图幅 mm，已含比例）
- 落图公式：图纸坐标 = view_position + 实体局部坐标 × scale_factor，
  scale_factor 由视图 scale 字段（GB 标准比例字符串 "1:N"）解析为 1/N
- DXF 单位：$INSUNITS=4（毫米）

M2 范围（严格遵循 docs/plans/04-二维生成可视化模块.md 第六节契约）：
- 图幅：A3 横向（420×297mm），画图框 + 标题栏（图框层）；标题栏为简单矩形+文字，
  不做复杂 block 定义。标题栏内容（图号/名称/比例/材料）从 Step2/5 产物取，
  取不到留空不编造（诚实原则，同 step5）
- 视图落图（消费 Step3 views.json，必需）：
  * line → LINE / circle → CIRCLE / arc → ARC（半径同样乘 scale_factor），均落"轮廓线"层
    角度约定：Step3 未产出 arc 实体（M2 真实案例仅 line/circle）；此处按
    {"cx","cy","r","start_angle","end_angle"}（度，逆时针）落图，与 ezdxf ARC
    约定一致。若后续 Step3 arc 采用顺时针约定，需在此处交换 start/end 并注释说明
  * hidden_lines → "隐藏线"层（HIDDEN 线型）；center_lines → "中心线"层（CENTER 线型）
- 标注落图（消费 Step4 dimensions.json，可缺）：M2 简化方案——不用 DXF DIMENSION
  实体，用三线+文字组合绘制（延伸线×2 + 标注线 + TEXT 写 value+公差），全部落"标注"层。
  理由：ezdxf DIMENSION 实体渲染依赖 CAD 内核（各软件重算标注几何），
  三线方案为纯基本实体，跨软件（AutoCAD/中望/ODA 查看器）所见即所得。
  标注 position 为视图局部坐标，优先按 view_name 字段定位所属视图
  （associated_entities 前缀 "<view>_e<n>" 解析为回退，兼容旧数据），
  套用同一落图公式（含比例）；无法定位时按原坐标落图并 warning
- BOM 表格落图（消费 Step5 bom.json，可缺）：按 position/style 画表格线 +
  单元格 TEXT，落"BOM"层，列宽均分
- 技术要求落图（消费 Step6 tech_requirements.json，可缺）：TEXT 逐行落
  "技术要求"层，行高 = font_size×1.5（style 缺省时 font_size 取默认 3.5）
- 文字：TEXT height 用 mm 值；中文用 ezdxf 默认 style，不折腾字体文件
- 图层：严格按契约 9 层（0/轮廓线/隐藏线/中心线/标注/剖面线/BOM/技术要求/图框）
- 输出：output/drawing.dxf（R2010），返回 dxf_structure 契约
  （entity_counts 实测统计、layers 实测列出、header 含 dxfversion；M2 未用
  block 定义 → blocks 为空列表，诚实原则）

ezdxf 纯本地，不依赖 SW COM，可在无 SW 环境单测。
异常红线：缺 Step3 views → SWException；ezdxf 保存异常 → SWException(GEN_STEP_FAILED)
上抛，禁止静默失败。Step4/5/6 缺失 → logger.warning 降级跳过对应层，不报错。
"""

import json
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import ezdxf

from app.generators.models import StepContext
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# 契约 9 层（docs/plans/04 第六节）：(name, color, linetype)
# linetype 用 ezdxf 内置标准线型名（setup=True 载入）：HIDDEN/CENTER
_CONTRACT_LAYERS: List[Tuple[str, int, str]] = [
    ("0", 7, "CONTINUOUS"),
    ("轮廓线", 0, "CONTINUOUS"),
    ("隐藏线", 8, "HIDDEN"),
    ("中心线", 1, "CENTER"),
    ("标注", 3, "CONTINUOUS"),
    ("剖面线", 2, "CONTINUOUS"),
    ("BOM", 4, "CONTINUOUS"),
    ("技术要求", 5, "CONTINUOUS"),
    ("图框", 6, "CONTINUOUS"),
]

_LAYER_OUTLINE = "轮廓线"
_LAYER_HIDDEN = "隐藏线"
_LAYER_CENTER = "中心线"
_LAYER_DIM = "标注"
_LAYER_BOM = "BOM"
_LAYER_TECH = "技术要求"
_LAYER_FRAME = "图框"

# A3 横向（mm）
_SHEET_W = 420.0
_SHEET_H = 297.0
_FRAME_MARGIN = 10.0
# 标题栏：右下角，宽×高（mm）
_TITLE_W = 180.0
_TITLE_H = 40.0

# 文字默认字高（mm）
_DIM_FONT_SIZE = 3.5
_TECH_FONT_SIZE = 3.5
_TITLE_FONT_SIZE = 5.0
# 标注延伸线长度（mm，M2 简化：无被测点，画固定长度延伸线）
_DIM_EXT_LEN = 5.0


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
    logger.warning(f"[Task:{ctx.task_id}] step7 {label} missing, "
                   f"degraded: skip related layer drawing")
    return None


def _parse_scale(scale: Any) -> float:
    """
    解析 GB 比例字符串 → 图纸/局部 比例因子：
    "1:N"（缩小）→ 1/N；"N:1"（放大）→ N；缺省/非法 → 1.0
    """
    if isinstance(scale, str) and ":" in scale:
        a, b = scale.split(":", 1)
        try:
            num, den = float(a), float(b)
            if num > 0 and den > 0:
                return num / den
        except ValueError:
            pass
    return 1.0


def _translate(point: Tuple[float, float], view_pos: Dict[str, Any],
               scale: float) -> Tuple[float, float]:
    """
    落图公式：图纸坐标 = view_position + 实体局部坐标 × scale_factor
    （Step3 契约：实体已归一化到视图左下角原点，view_positions 为图纸坐标）
    """
    return (view_pos["x"] + point[0] * scale,
            view_pos["y"] + point[1] * scale)


class DxfBuildExecutor:
    """
    Step 7 执行器: DXF 构建

    输入: ctx.previous_results[3]/output/views.json（必需）；
          ctx.previous_results[4]/output/dimensions.json（可缺，降级）；
          ctx.previous_results[5]/output/bom.json（可缺，降级）；
          ctx.previous_results[6]/output/tech_requirements.json（可缺，降级）；
          ctx.previous_results[2]（可选，标题栏信息来源）
    输出: {"dxf_structure": {header/layers/entity_counts/blocks}}，
          落盘 output/drawing.dxf（R2010）
    异常: 缺 views / 检查点损坏 / ezdxf 保存失败 → SWException(GEN_STEP_FAILED)
    """

    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        views_data = _load_upstream(ctx, 3, ("views.json",), required=True, label="views")
        dims_data = _load_upstream(ctx, 4, ("dimensions.json",), required=False, label="dimensions")
        bom_data = _load_upstream(ctx, 5, ("bom.json",), required=False, label="bom")
        tech_data = _load_upstream(ctx, 6, ("tech_requirements.json",), required=False,
                                   label="tech_requirements")
        geometry = _load_upstream(ctx, 2, ("geometry.json", "bom.json"),
                                  required=False, label="geometry(title info)")

        counts: Dict[str, int] = {}

        def _bump(key: str, n: int = 1) -> None:
            counts[key] = counts.get(key, 0) + n

        doc = ezdxf.new("R2010", setup=True)
        doc.header["$INSUNITS"] = 4  # 毫米
        for name, color, linetype in _CONTRACT_LAYERS:
            if name in doc.layers:
                layer = doc.layers.get(name)
                layer.dxf.color = color
                layer.dxf.linetype = linetype
            else:
                doc.layers.add(name, color=color, linetype=linetype)
        msp = doc.modelspace()

        # ---- 1. 图框 + 标题栏（图框层）----
        self._draw_frame(msp, geometry, counts)

        # ---- 2. 视图落图（轮廓线/隐藏线/中心线层）----
        layout_positions = (views_data.get("layout") or {}).get("view_positions") or {}
        for view in views_data.get("views") or []:
            self._draw_view(msp, view, layout_positions, counts, ctx)

        # ---- 3. 标注落图（标注层，可缺降级）----
        if dims_data:
            views_by_name = {v.get("name"): v for v in views_data.get("views") or []}
            self._draw_dimensions(msp, dims_data.get("dimensions") or [],
                                  views_by_name, layout_positions, counts, ctx)

        # ---- 4. BOM 表格落图（BOM 层，可缺降级）----
        if bom_data and bom_data.get("bom_table"):
            self._draw_bom_table(msp, bom_data["bom_table"], counts)

        # ---- 5. 技术要求落图（技术要求层，可缺降级）----
        if tech_data and tech_data.get("tech_requirements"):
            self._draw_tech_requirements(msp, tech_data["tech_requirements"], counts)

        # ---- 6. 保存 + 契约返回 ----
        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        dxf_file = output_dir / "drawing.dxf"
        try:
            doc.saveas(dxf_file)
        except Exception as e:
            raise SWException(
                f"Failed to save DXF: {dxf_file}: {e}",
                error_code=ErrorCode.GEN_STEP_FAILED,
                task_id=ctx.task_id,
                step=ctx.step,
                detail=str(e),
            )

        # 实测图层列表（契约字段：name/color/linetype）
        layers_out = [
            {
                "name": name,
                "color": doc.layers.get(name).dxf.color,
                "linetype": doc.layers.get(name).dxf.linetype,
            }
            for name, _, _ in _CONTRACT_LAYERS
        ]

        result: Dict[str, Any] = {
            "dxf_structure": {
                "header": {
                    "dxfversion": doc.dxfversion,
                    "sheet_size": "A3",
                    "orientation": "landscape",
                    "units": "mm",
                },
                "layers": layers_out,
                "entity_counts": counts,
                # M2 未做 block 定义（标题栏为矩形+文字），诚实返回空
                "blocks": [],
            },
            "dxf_file": str(dxf_file),
        }

        logger.info(f"[Task:{ctx.task_id}] DXF built: {sum(counts.values())} entities "
                    f"({counts}) -> {dxf_file}")
        return result

    # ------------------------------------------------------------------
    # 绘制子模块
    # ------------------------------------------------------------------

    def _add_text(self, msp, text: str, x: float, y: float, height: float,
                  layer: str, counts: Dict[str, int]) -> None:
        """TEXT 落图：height 用 mm 值，中文用 ezdxf 默认 style"""
        entity = msp.add_text(str(text), height=height,
                              dxfattribs={"layer": layer})
        entity.set_placement((x, y))
        counts["text"] = counts.get("text", 0) + 1

    def _add_line(self, msp, p1, p2, layer, counts) -> None:
        msp.add_line(p1, p2, dxfattribs={"layer": layer})
        counts["line"] = counts.get("line", 0) + 1

    def _draw_frame(self, msp, geometry: Optional[Dict[str, Any]],
                    counts: Dict[str, int]) -> None:
        """A3 横向图框 + 右下角标题栏（简单矩形+文字，不做 block 定义）"""
        x0, y0 = _FRAME_MARGIN, _FRAME_MARGIN
        x1, y1 = _SHEET_W - _FRAME_MARGIN, _SHEET_H - _FRAME_MARGIN
        for p1, p2 in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                       ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            self._add_line(msp, p1, p2, _LAYER_FRAME, counts)

        # 标题栏信息：诚实原则，取不到留空不编造
        title = self._title_info(geometry)

        tx0, ty0 = x1 - _TITLE_W, y0
        tx1, ty1 = x1, y0 + _TITLE_H
        # 外框 + 三行分隔
        for p1, p2 in (((tx0, ty0), (tx1, ty0)), ((tx1, ty0), (tx1, ty1)),
                       ((tx1, ty1), (tx0, ty1)), ((tx0, ty1), (tx0, ty0))):
            self._add_line(msp, p1, p2, _LAYER_FRAME, counts)
        row_h = _TITLE_H / 4.0
        for i in range(1, 4):
            y = ty0 + i * row_h
            self._add_line(msp, (tx0, y), (tx1, y), _LAYER_FRAME, counts)

        pad = 2.0
        rows = [
            ("图号", title["drawing_number"]),
            ("名称", title["name"]),
            ("比例", title["scale"]),
            ("材料", title["material"]),
        ]
        for i, (label, value) in enumerate(rows):
            y = ty1 - (i + 1) * row_h + (row_h - _TITLE_FONT_SIZE) / 2.0
            text = f"{label}: {value}" if value else f"{label}:"
            self._add_text(msp, text, tx0 + pad, y, _TITLE_FONT_SIZE,
                           _LAYER_FRAME, counts)

    @staticmethod
    def _title_info(geometry: Optional[Dict[str, Any]]) -> Dict[str, str]:
        """标题栏内容：图号=Step2 顶层 bom path 文件名去扩展名，名称=bom name；
        材料取 materials 首个值；取不到一律空字符串（诚实原则）"""
        info = {"drawing_number": "", "name": "", "scale": "1:1", "material": ""}
        if not isinstance(geometry, dict):
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
            first = next(iter(materials.values()))
            info["material"] = str(first) if first else ""
        return info

    def _draw_view(self, msp, view: Dict[str, Any],
                   layout_positions: Dict[str, Any], counts: Dict[str, int],
                   ctx: StepContext) -> None:
        name = view.get("name")
        view_pos = layout_positions.get(name)
        bbox = view.get("bounding_box") or {}
        if view_pos is None or "min_x" not in bbox:
            logger.warning(f"[Task:{ctx.task_id}] step7 view '{name}' missing "
                           f"view_position or bounding_box, skipped")
            return
        scale = _parse_scale(view.get("scale"))

        for ent in view.get("entities") or []:
            etype = ent.get("type")
            if etype == "line":
                self._add_line(
                    msp,
                    _translate((ent["x1"], ent["y1"]), view_pos, scale),
                    _translate((ent["x2"], ent["y2"]), view_pos, scale),
                    _LAYER_OUTLINE, counts)
            elif etype == "circle":
                center = _translate((ent["cx"], ent["cy"]), view_pos, scale)
                msp.add_circle(center, radius=ent["r"] * scale,
                               dxfattribs={"layer": _LAYER_OUTLINE})
                counts["circle"] = counts.get("circle", 0) + 1
            elif etype == "arc":
                # 约定：Step3 arc 角度为度、逆时针，与 ezdxf ARC 一致；
                # 若 Step3 改为顺时针约定，需交换 start/end（见模块 docstring）
                center = _translate((ent["cx"], ent["cy"]), view_pos, scale)
                msp.add_arc(center, radius=ent["r"] * scale,
                            start_angle=ent["start_angle"],
                            end_angle=ent["end_angle"],
                            dxfattribs={"layer": _LAYER_OUTLINE})
                counts["arc"] = counts.get("arc", 0) + 1
            else:
                logger.warning(f"[Task:{ctx.task_id}] step7 unknown entity "
                               f"type {etype!r} in view '{name}', skipped")

        for hidden in view.get("hidden_lines") or []:
            self._add_line(
                msp,
                _translate((hidden["x1"], hidden["y1"]), view_pos, scale),
                _translate((hidden["x2"], hidden["y2"]), view_pos, scale),
                _LAYER_HIDDEN, counts)
        for center_line in view.get("center_lines") or []:
            self._add_line(
                msp,
                _translate((center_line["x1"], center_line["y1"]), view_pos, scale),
                _translate((center_line["x2"], center_line["y2"]), view_pos, scale),
                _LAYER_CENTER, counts)

    def _infer_view_name(self, dim: Dict[str, Any],
                         views_by_name: Dict[str, Any]) -> Optional[str]:
        """定位标注所属视图：优先用 Step4 新增的 view_name 字段（M2 修复包后
        契约字段）；缺失/无效时回退 associated_entities 前缀（'<view>_e<n>'）
        解析，兼容旧 dimensions.json 数据"""
        vn = dim.get("view_name")
        if isinstance(vn, str) and vn in views_by_name:
            return vn
        for ref in dim.get("associated_entities") or []:
            if isinstance(ref, str) and "_e" in ref:
                prefix = ref.rsplit("_e", 1)[0]
                if prefix in views_by_name:
                    return prefix
        return None

    def _draw_dimensions(self, msp, dimensions: List[Dict[str, Any]],
                         views_by_name: Dict[str, Any],
                         layout_positions: Dict[str, Any],
                         counts: Dict[str, int], ctx: StepContext) -> None:
        """
        三线+文字方案：延伸线×2 + 标注线 + TEXT（value+公差），全落"标注"层。
        不用 DXF DIMENSION 实体（渲染依赖 CAD 内核，三线方案跨软件所见即所得）
        """
        for dim in dimensions:
            pos = dim.get("position") or {}
            if not all(k in pos for k in ("x1", "y1", "x2", "y2")):
                logger.warning(f"[Task:{ctx.task_id}] step7 dim "
                               f"{dim.get('id')!r} missing position, skipped")
                continue

            # 标注 position 为视图局部坐标：能推断所属视图则套落图公式（含比例），
            # 否则按原坐标落图（可能已是图纸坐标）
            view_name = self._infer_view_name(dim, views_by_name)
            view = views_by_name.get(view_name) if view_name else None
            view_pos = layout_positions.get(view_name) if view_name else None
            bbox = (view or {}).get("bounding_box") or {}
            if view_pos is not None and "min_x" in bbox:
                dim_scale = _parse_scale((view or {}).get("scale"))

                def tr(p):
                    return _translate(p, view_pos, dim_scale)
            else:
                if view_name is None:
                    logger.warning(f"[Task:{ctx.task_id}] step7 dim "
                                   f"{dim.get('id')!r} view not inferred, "
                                   f"drawn at raw coords")
                def tr(p):
                    return p

            p1 = tr((pos["x1"], pos["y1"]))
            p2 = tr((pos["x2"], pos["y2"]))
            # 标注线
            self._add_line(msp, p1, p2, _LAYER_DIM, counts)
            # 延伸线×2：垂直于标注线方向的固定长度短线（M2 简化，无被测点）
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            length = math.hypot(dx, dy)
            if length > 1e-9:
                nx, ny = -dy / length, dx / length
            else:
                nx, ny = 0.0, 1.0
            for p in (p1, p2):
                self._add_line(msp, p,
                               (p[0] + nx * _DIM_EXT_LEN, p[1] + ny * _DIM_EXT_LEN),
                               _LAYER_DIM, counts)
            # 文字：value + 公差
            tx = pos.get("text_x", (pos["x1"] + pos["x2"]) / 2.0)
            ty = pos.get("text_y", (pos["y1"] + pos["y2"]) / 2.0)
            txy = tr((tx, ty))
            self._add_text(msp, self._dim_text(dim), txy[0], txy[1],
                           _DIM_FONT_SIZE, _LAYER_DIM, counts)
            counts["dimension"] = counts.get("dimension", 0) + 1

    @staticmethod
    def _dim_text(dim: Dict[str, Any]) -> str:
        value = dim.get("value", "")
        try:
            value_text = f"{float(value):g}"
        except (TypeError, ValueError):
            value_text = str(value)
        tol = dim.get("tolerance") or {}
        upper, lower = tol.get("upper"), tol.get("lower")
        if isinstance(upper, (int, float)) and isinstance(lower, (int, float)):
            if abs(upper + lower) < 1e-9:
                return f"{value_text}±{abs(upper):g}"
            return f"{value_text} +{upper:g}/{lower:g}"
        return value_text

    def _draw_bom_table(self, msp, bom_table: Dict[str, Any],
                        counts: Dict[str, int]) -> None:
        """BOM 表格线 + 单元格 TEXT（列宽均分），落"BOM"层"""
        pos = bom_table.get("position") or {}
        style = bom_table.get("style") or {}
        x, y = float(pos.get("x", 0.0)), float(pos.get("y", 0.0))
        width = float(pos.get("width", 400.0))
        header_h = float(style.get("header_height", 20.0))
        row_h = float(style.get("row_height", 15.0))
        font = float(style.get("font_size", 3.5))

        columns: List[str] = list(bom_table.get("columns") or [])
        rows: List[List[Any]] = list(bom_table.get("rows") or [])
        if not columns:
            return
        col_w = width / len(columns)
        total_h = header_h + row_h * len(rows)

        # 外框 + 横线 + 竖线
        for p1, p2 in (((x, y), (x + width, y)), ((x + width, y), (x + width, y + total_h)),
                       ((x + width, y + total_h), (x, y + total_h)), ((x, y + total_h), (x, y))):
            self._add_line(msp, p1, p2, _LAYER_BOM, counts)
        # 表头下横线 + 各行分隔
        for i in range(len(rows) + 1):
            ly = y + total_h - header_h - i * row_h
            self._add_line(msp, (x, ly), (x + width, ly), _LAYER_BOM, counts)
        for i in range(1, len(columns)):
            lx = x + i * col_w
            self._add_line(msp, (lx, y), (lx, y + total_h), _LAYER_BOM, counts)

        pad = 1.0
        # 表头（顶部一行）
        header_y = y + total_h - header_h + (header_h - font) / 2.0
        for ci, col in enumerate(columns):
            self._add_text(msp, str(col), x + ci * col_w + pad, header_y,
                           font, _LAYER_BOM, counts)
        # 数据行（自上而下）
        for ri, row in enumerate(rows):
            cell_y = y + total_h - header_h - (ri + 1) * row_h + (row_h - font) / 2.0
            for ci in range(len(columns)):
                value = row[ci] if ci < len(row) else ""
                self._add_text(msp, str(value), x + ci * col_w + pad, cell_y,
                               font, _LAYER_BOM, counts)

    def _draw_tech_requirements(self, msp, tech: Dict[str, Any],
                                counts: Dict[str, int]) -> None:
        """技术要求 TEXT 逐行落图，行高 = font_size×1.5，落"技术要求"层"""
        pos = tech.get("position") or {}
        style = tech.get("style") or {}
        font = float(style.get("font_size", _TECH_FONT_SIZE))
        line_h = font * 1.5
        x = float(pos.get("x", 0.0))
        y_top = float(pos.get("y", 0.0)) + float(pos.get("height", 0.0))
        for i, line in enumerate(tech.get("content") or []):
            self._add_text(msp, str(line), x, y_top - (i + 1) * line_h,
                           font, _LAYER_TECH, counts)
