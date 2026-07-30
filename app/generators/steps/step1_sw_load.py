"""
Step 1: 3D模型加载

通过 pywin32 + SW COM API 加载 SolidWorks 文件，提取基本信息。
复用现有 app.parsers.sw_parser 的能力。

COM 调用统一经 app.generators.sw_com.run_sw 在专用线程执行，
parser 在单次调用内创建并显式释放（quit），避免资源泄漏。
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any

from app.generators.models import StepContext
from app.generators.sw_com import run_sw
from app.parsers.sw_parser import SWParser
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)


def _load_sw_file_sync(source_file: str, output_dir: str) -> Dict[str, Any]:
    """
    【同步/COM线程】加载 SW 文件并提取信息

    parser 生命周期：本函数内创建，finally 中 close_document + quit，
    不跨调用持有，杜绝 SW 应用句柄泄漏。
    """
    parser = SWParser()
    try:
        ext = Path(source_file).suffix.lower()
        if ext == ".sldasm":
            result = _parse_assembly(parser, source_file)
        elif ext == ".sldprt":
            result = _parse_part(parser, source_file)
        else:
            raise SWException(
                f"Unsupported file type: {ext}",
                error_code=ErrorCode.GEN_INVALID_FILE,
            )

        # 保存完整数据到输出目录
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        json_file = out / ("assembly.json" if ext == ".sldasm" else "part.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return result
    finally:
        try:
            parser.close_document(source_file)
        except Exception as e:
            logger.warning(f"Failed to close document: {e}")
        try:
            parser.quit()
        except Exception as e:
            logger.warning(f"Failed to quit SW parser: {e}")


def _parse_assembly(parser: SWParser, filepath: str) -> Dict[str, Any]:
    """【同步/COM线程】解析装配体"""
    assembly = parser.parse_assembly(filepath)
    return {
        "file_type": "assembly",
        "name": assembly.name,
        "path": assembly.path,
        "component_count": len(assembly.components),
        "components": [
            {
                "name": comp.name,
                "path": comp.path,
                "instance_id": comp.instance_id,
                "quantity": comp.quantity,
                "is_suppressed": comp.is_suppressed,
                "is_hidden": comp.is_hidden,
            }
            for comp in assembly.components
        ],
    }


def _parse_part(parser: SWParser, filepath: str) -> Dict[str, Any]:
    """【同步/COM线程】解析零件"""
    part = parser.parse_part(filepath)
    return {
        "file_type": "part",
        "name": part.name,
        "path": part.path,
        "material": {
            "name": part.material.name,
            "description": part.material.description,
        },
        "mass": part.mass,
        "bounding_box": part.bounding_box,
        "feature_count": len(part.features),
        "features": [
            {
                "name": feat.name,
                "type": feat.feature_type,
            }
            for feat in part.features[:50]  # 限制数量
        ],
    }


class SWLoadExecutor:
    """
    Step 1 执行器: 加载 SolidWorks 文件

    输入: ctx.parameters["source_file"]
    输出: {
        "assembly": {...} | "part": {...},
        "snapshot_path": None  # M1 占位，M2 接入 SW SaveAsImage
    }
    """

    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        source_file = ctx.parameters.get("source_file", "")
        if not source_file:
            raise SWException(
                "Source file not specified",
                error_code=ErrorCode.GEN_INVALID_FILE,
                task_id=ctx.task_id,
                step=ctx.step,
            )

        source_path = Path(source_file)
        if not source_path.exists():
            raise SWException(
                f"Source file not found: {source_file}",
                error_code=ErrorCode.GEN_INVALID_FILE,
                task_id=ctx.task_id,
                step=ctx.step,
            )

        logger.info(f"[Task:{ctx.task_id}] Loading SW file: {source_file}")

        output_dir = ctx.get_output_path("")
        try:
            result = await run_sw(_load_sw_file_sync, source_file, str(output_dir))
        except SWException:
            raise  # 保留原始错误码（如 GEN_INVALID_FILE），避免误判重试
        except Exception as e:
            logger.exception(f"[Task:{ctx.task_id}] Failed to load SW file: {e}")
            raise SWException(
                f"Failed to load SW file: {e}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
                task_id=ctx.task_id,
                step=ctx.step,
                detail=str(e),
            )

        # M1 占位：不产出假截图路径（审查 B4），M2 接入 SW SaveAsImage
        result["snapshot_path"] = None

        logger.info(f"[Task:{ctx.task_id}] SW file loaded successfully")
        return result
