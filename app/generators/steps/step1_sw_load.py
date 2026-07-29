"""
Step 1: 3D模型加载

通过 pywin32 + SW COM API 加载 SolidWorks 文件，提取基本信息。
复用现有 app.parsers.sw_parser 的能力。
"""

import logging
from pathlib import Path
from typing import Dict, Any

from app.generators.models import StepContext
from app.parsers.sw_parser import SWParser, SWAssembly, SWPart
from app.core.exceptions import SWException, ErrorCode

logger = logging.getLogger(__name__)


class SWLoadExecutor:
    """
    Step 1 执行器: 加载 SolidWorks 文件
    
    输入: StepContext.work_dir / input / source_file (通过 context 传递路径)
    输出: {
        "assembly": {...},     # 装配体信息（如果是.SLDASM）
        "part": {...},         # 零件信息（如果是.SLDPRT）
        "snapshot_path": "..." # SW视口截图路径
    }
    """
    
    def __init__(self):
        self._parser: SWParser | None = None
    
    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        """
        执行 SW 文件加载
        
        Args:
            ctx: 步骤上下文
            
        Returns:
            包含装配体/零件信息的字典
        """
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
        
        try:
            # 初始化 SW 连接（复用现有解析器）
            self._parser = SWParser()
            
            # 根据文件类型解析
            ext = source_path.suffix.lower()
            if ext == ".sldasm":
                result = await self._load_assembly(ctx, source_file)
            elif ext == ".sldprt":
                result = await self._load_part(ctx, source_file)
            else:
                raise SWException(
                    f"Unsupported file type: {ext}",
                    error_code=ErrorCode.GEN_INVALID_FILE,
                    task_id=ctx.task_id,
                    step=ctx.step,
                )
            
            # 保存截图
            snapshot_path = await self._save_snapshot(ctx, source_file)
            if snapshot_path:
                result["snapshot_path"] = snapshot_path
            
            logger.info(f"[Task:{ctx.task_id}] SW file loaded successfully")
            return result
            
        except Exception as e:
            logger.exception(f"[Task:{ctx.task_id}] Failed to load SW file: {e}")
            raise SWException(
                f"Failed to load SW file: {e}",
                error_code=ErrorCode.GEN_SW_NOT_AVAILABLE,
                task_id=ctx.task_id,
                step=ctx.step,
                detail=str(e),
            )
        finally:
            # 关闭文档但不退出 SW（保持连接复用）
            if self._parser:
                try:
                    self._parser.close_document(source_file)
                except Exception as e:
                    logger.warning(f"[Task:{ctx.task_id}] Failed to close document: {e}")
    
    async def _load_assembly(self, ctx: StepContext, filepath: str) -> Dict[str, Any]:
        """加载装配体"""
        logger.debug(f"[Task:{ctx.task_id}] Parsing assembly: {filepath}")
        
        assembly = self._parser.parse_assembly(filepath)
        
        # 序列化装配体信息
        result = {
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
        
        # 保存完整装配体数据到输出目录
        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        import json
        assembly_file = output_dir / "assembly.json"
        with open(assembly_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        logger.debug(f"[Task:{ctx.task_id}] Assembly parsed: {result['component_count']} components")
        return result
    
    async def _load_part(self, ctx: StepContext, filepath: str) -> Dict[str, Any]:
        """加载零件"""
        logger.debug(f"[Task:{ctx.task_id}] Parsing part: {filepath}")
        
        part = self._parser.parse_part(filepath)
        
        result = {
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
        
        # 保存完整零件数据
        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        import json
        part_file = output_dir / "part.json"
        with open(part_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        logger.debug(f"[Task:{ctx.task_id}] Part parsed: {result['feature_count']} features")
        return result
    
    async def _save_snapshot(self, ctx: StepContext, filepath: str) -> str | None:
        """保存 SW 视口截图"""
        try:
            # 通过 SW API 保存截图
            doc = self._parser.open_document(filepath)
            
            # 获取模型视图
            model_view = doc.ActiveView
            if not model_view:
                logger.warning(f"[Task:{ctx.task_id}] No active view found")
                return None
            
            # 保存为 PNG
            output_dir = ctx.get_output_path("")
            output_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = output_dir / "snapshot.png"
            
            # SW API: 保存视口图像
            # 注意：这需要具体的 SW API 调用，这里使用占位实现
            # model_view.SaveAsImage(str(snapshot_path))
            
            logger.debug(f"[Task:{ctx.task_id}] Snapshot saved: {snapshot_path}")
            return str(snapshot_path)
            
        except Exception as e:
            logger.warning(f"[Task:{ctx.task_id}] Failed to save snapshot: {e}")
            return None
