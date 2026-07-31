"""
Step 6: 技术要求

M2 范围（规则驱动模板系统，非 AI）：
- 模板库：模块级内置模板（weldment_general 焊接件通用 / machining_general 机加工件通用），
  变量占位语法统一 {var_name}，模板含默认变量表
- 模板选择：ctx.parameters["template_id"] 显式指定，缺省 = weldment_general（LB26 焊接件场景）；
  未知 template_id → SWException
- 变量覆盖：ctx.parameters["tech_variables"] 字典覆盖默认变量；
  模板中出现但既无默认值也未被覆盖 → SWException（禁止静默留空/原样输出占位符）；
  多余覆盖变量（模板未用）→ logger.warning 记录但不报错
- position：契约默认值（图幅内定位：图框左下角空白区，图纸坐标 mm，
  原点图框左下角、Y 向上），可 ctx.parameters["tech_config"]["position"] 覆盖；
  style：默认 {"font_size": 3.5, "line_spacing": 1.5}，可 tech_config.style 覆盖
  （非法值显式报错，与 step4/5 同款模式）
- 输出顶层结构严格按契约，附加 available_templates（模板 id 列表）

纯文本处理，不依赖 SW COM，可在无 SW 环境单测。
契约：docs/plans/04-二维生成可视化模块.md 第五节
"""

import json
import logging
import re
from typing import Any, Dict, List

from app.generators.models import StepContext
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)

# 默认模板（LB26 为焊接件场景）
_DEFAULT_TEMPLATE_ID = "weldment_general"

# position 契约默认值：图框左下角空白区（图纸坐标 mm，A3 横向有效范围
# [10,410]×[10,287]）
_DEFAULT_POSITION = {"x": 20.0, "y": 20.0, "width": 200.0, "height": 120.0}

# style 契约默认值（font_size 单位 mm，line_spacing 为行距倍数）
_DEFAULT_STYLE = {"font_size": 3.5, "line_spacing": 1.5}

# 变量占位符语法 {var_name}
_VAR_PATTERN = re.compile(r"\{(\w+)\}")

# 模板库（模块级内置数据结构，M2 规则驱动，非外部文件）
TECH_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "weldment_general": {
        "template_id": "weldment_general",
        "template_name": "焊接件通用模板",
        "variables": {
            "grade": "二级",
            "size": "5",
            "stress_relief": "消除应力",
            "ndt": "UT-2级",
        },
        "content_template": [
            "1.焊接应符合GB/T 985.1规定",
            "2.焊缝质量等级：{grade}（GB/T 19418）",
            "3.角焊缝焊脚尺寸不小于{size}mm",
            "4.焊后应进行{stress_relief}处理",
            "5.焊缝按{ndt}进行无损检测",
            "6.焊缝表面不得有裂纹、气孔、夹渣等缺陷",
            "7.未注焊缝均为连续角焊缝",
            "8.焊后清除焊渣与飞溅，焊缝外观应平整光滑",
        ],
    },
    "machining_general": {
        "template_id": "machining_general",
        "template_name": "机加工件通用模板",
        "variables": {
            "tolerance_grade": "m",
            "chamfer": "1",
            "fillet": "2",
            "roughness": "6.3",
            "surface_treatment": "发黑",
        },
        "content_template": [
            "1.未注线性尺寸公差按GB/T 1804-{tolerance_grade}",
            "2.未注倒角C{chamfer}",
            "3.未注圆角R{fillet}",
            "4.各加工表面粗糙度Ra{roughness}",
            "5.表面处理：{surface_treatment}",
            "6.锐边倒钝，去除毛刺",
        ],
    },
}


def _parse_tech_config(ctx: StepContext) -> Dict[str, Dict[str, Any]]:
    """解析并校验 tech_config 参数（position/style 覆盖），非法值显式报错"""
    cfg = ctx.parameters.get("tech_config") or {}
    if not isinstance(cfg, dict):
        raise SWException(
            f"tech_config must be a dict, got {type(cfg).__name__}",
            error_code=ErrorCode.GEN_INVALID_FILE,
            task_id=ctx.task_id,
            step=ctx.step,
        )

    position = dict(_DEFAULT_POSITION)
    pos_override = cfg.get("position")
    if pos_override is not None:
        if not isinstance(pos_override, dict):
            raise SWException(
                f"tech_config.position must be a dict, got {type(pos_override).__name__}",
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
                    f"tech_config.position.{key} must be a number, got {val!r}",
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
                f"tech_config.style must be a dict, got {type(style_override).__name__}",
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
                    f"tech_config.style.{key} must be a positive number, got {val!r}",
                    error_code=ErrorCode.GEN_INVALID_FILE,
                    task_id=ctx.task_id,
                    step=ctx.step,
                )
            style[key] = float(val)

    return {"position": position, "style": style}


def _parse_tech_variables(ctx: StepContext) -> Dict[str, str]:
    """解析并校验 tech_variables 覆盖参数"""
    overrides = ctx.parameters.get("tech_variables") or {}
    if not isinstance(overrides, dict):
        raise SWException(
            f"tech_variables must be a dict, got {type(overrides).__name__}",
            error_code=ErrorCode.GEN_INVALID_FILE,
            task_id=ctx.task_id,
            step=ctx.step,
        )
    return {str(k): str(v) for k, v in overrides.items()}


def _select_template(ctx: StepContext) -> Dict[str, Any]:
    """按 template_id 选择模板，未知 id 显式报错"""
    template_id = ctx.parameters.get("template_id") or _DEFAULT_TEMPLATE_ID
    if not isinstance(template_id, str):
        raise SWException(
            f"template_id must be a str, got {type(template_id).__name__}",
            error_code=ErrorCode.GEN_INVALID_FILE,
            task_id=ctx.task_id,
            step=ctx.step,
        )
    template = TECH_TEMPLATES.get(template_id)
    if template is None:
        raise SWException(
            f"Unknown tech requirement template_id: {template_id!r}, "
            f"available: {sorted(TECH_TEMPLATES)}",
            error_code=ErrorCode.GEN_INVALID_FILE,
            task_id=ctx.task_id,
            step=ctx.step,
        )
    return template


def render_template(template: Dict[str, Any],
                    overrides: Dict[str, str],
                    ctx: StepContext = None) -> Dict[str, Any]:
    """
    渲染模板：变量解析（覆盖 > 默认），缺失变量显式报错（可单测）

    Returns: {"variables": {实际使用的变量表}, "content": [渲染后条目]}
    """
    content_template: List[str] = template["content_template"]
    defaults: Dict[str, str] = template.get("variables") or {}

    required = set()
    for line in content_template:
        required.update(_VAR_PATTERN.findall(line))

    # 多余覆盖变量：模板未使用 → warning，不报错
    extra = set(overrides) - required
    if extra:
        logger.warning(
            f"[tech_requirement] unused tech_variables overrides ignored: {sorted(extra)}"
        )

    variables: Dict[str, str] = {}
    missing = []
    for name in sorted(required):
        if name in overrides:
            variables[name] = overrides[name]
        elif name in defaults:
            variables[name] = str(defaults[name])
        else:
            missing.append(name)

    if missing:
        raise SWException(
            f"Template '{template['template_id']}' requires variables {missing} "
            f"but neither default nor override provided",
            error_code=ErrorCode.GEN_STEP_FAILED,
            task_id=ctx.task_id if ctx else None,
            step=ctx.step if ctx else None,
        )

    content = [line.format(**variables) for line in content_template]
    return {"variables": variables, "content": content}


class TechRequirementExecutor:
    """
    Step 6 执行器: 技术要求

    输入: ctx.parameters["template_id"]（可选，缺省 weldment_general）；
          ctx.parameters["tech_variables"]（可选，覆盖默认变量）；
          ctx.parameters["tech_config"]["position"]（可选，覆盖默认位置）
    输出: {"tech_requirements": {template_id/template_name/variables/content/position},
           "available_templates": [...]}，落盘 output/tech_requirements.json
    异常: 未知 template_id / 变量缺失 / config 非法 → SWException
    """

    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        tech_cfg = _parse_tech_config(ctx)
        overrides = _parse_tech_variables(ctx)
        template = _select_template(ctx)

        logger.info(f"[Task:{ctx.task_id}] Rendering tech requirements "
                    f"with template '{template['template_id']}'")

        rendered = render_template(template, overrides, ctx)

        result: Dict[str, Any] = {
            "tech_requirements": {
                "template_id": template["template_id"],
                "template_name": template["template_name"],
                "variables": rendered["variables"],
                "content": rendered["content"],
                "position": tech_cfg["position"],
                "style": tech_cfg["style"],
            },
            "available_templates": sorted(TECH_TEMPLATES),
        }

        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "tech_requirements.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"[Task:{ctx.task_id}] Tech requirements done: "
                    f"{len(rendered['content'])} items -> {out_file}")
        return result
