"""
Step 2: 几何解析

从 Step 1 的输出中提取 BOM、材料、质量等几何信息。
复用现有 SWParser 的 get_bom 能力。
"""

import logging
from pathlib import Path
from typing import Dict, Any, List

from app.generators.models import StepContext
from app.parsers.sw_parser import SWParser
from app.core.exceptions import GenerationException, ErrorCode

logger = logging.getLogger(__name__)


class GeometryParseExecutor:
    """
    Step 2 执行器: 解析几何信息
    
    输入: Step 1 的输出（assembly.json 或 part.json）
    输出: {
        "bom": [...],           # BOM表
        "materials": {...},     # 材料统计
        "total_mass": 0.0,      # 总质量
        "bounding_box": {...},  # 总体边界盒
    }
    """
    
    def __init__(self):
        self._parser: SWParser | None = None
    
    async def __call__(self, ctx: StepContext) -> Dict[str, Any]:
        """
        执行几何解析
        
        Args:
            ctx: 步骤上下文
            
        Returns:
            包含 BOM、材料、质量等信息的字典
        """
        source_file = ctx.parameters.get("source_file", "")
        step1_data = ctx.previous_results.get(1, {})
        
        logger.info(f"[Task:{ctx.task_id}] Parsing geometry for {source_file}")
        
        try:
            # 初始化 SW 连接
            self._parser = SWParser()
            
            # 获取 BOM
            bom = await self._extract_bom(ctx, source_file)
            
            # 统计材料
            materials = self._analyze_materials(bom)
            
            # 计算总质量
            total_mass = self._calculate_total_mass(bom)
            
            # 构建结果
            result = {
                "bom": bom,
                "bom_summary": {
                    "total_items": len(bom),
                    "unique_items": len(set(item["name"] for item in bom)),
                    "standard_parts": sum(1 for item in bom if "GB/T" in item["name"]),
                    "custom_parts": sum(1 for item in bom if "GB/T" not in item["name"]),
                },
                "materials": materials,
                "total_mass": total_mass,
            }
            
            # 如果是零件，添加零件特有信息
            if step1_data.get("file_type") == "part":
                result["part_info"] = {
                    "mass": step1_data.get("mass", 0),
                    "bounding_box": step1_data.get("bounding_box", (0, 0, 0)),
                    "material": step1_data.get("material", {}),
                }
            
            # 保存结果
            output_dir = ctx.get_output_path("")
            output_dir.mkdir(parents=True, exist_ok=True)
            
            import json
            bom_file = output_dir / "bom.json"
            with open(bom_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            
            logger.info(
                f"[Task:{ctx.task_id}] Geometry parsed: "
                f"{result['bom_summary']['total_items']} items, "
                f"{result['bom_summary']['unique_items']} unique"
            )
            
            return result
            
        except Exception as e:
            logger.exception(f"[Task:{ctx.task_id}] Failed to parse geometry: {e}")
            raise GenerationException(
                f"Failed to parse geometry: {e}",
                error_code=ErrorCode.GEN_STEP_FAILED,
                task_id=ctx.task_id,
                step=ctx.step,
                step_name="geometry_parse",
                detail=str(e),
            )
        finally:
            if self._parser:
                try:
                    self._parser.close_document(source_file)
                except Exception as e:
                    logger.warning(f"[Task:{ctx.task_id}] Failed to close document: {e}")
    
    async def _extract_bom(self, ctx: StepContext, filepath: str) -> List[Dict[str, Any]]:
        """提取 BOM 表"""
        logger.debug(f"[Task:{ctx.task_id}] Extracting BOM from {filepath}")
        
        try:
            raw_bom = self._parser.get_bom(filepath)
            
            # 标准化 BOM 数据
            bom = []
            for item in raw_bom:
                bom.append({
                    "level": item.get("level", 0),
                    "name": item.get("name", ""),
                    "path": item.get("path", ""),
                    "quantity": item.get("quantity", 1),
                    "is_suppressed": item.get("is_suppressed", False),
                    # 尝试识别零件类型
                    "type": self._guess_part_type(item.get("name", "")),
                })
            
            return bom
            
        except Exception as e:
            logger.error(f"[Task:{ctx.task_id}] BOM extraction failed: {e}")
            return []
    
    @staticmethod
    def _guess_part_type(name: str) -> str:
        """根据名称猜测零件类型"""
        name_upper = name.upper()
        
        if "GB/T" in name_upper or "GB" in name_upper:
            return "standard"
        if any(kw in name_upper for kw in ["焊合", "焊接", "WELD"]):
            return "weldment"
        if any(kw in name_upper for kw in ["钣金", "SHEET", "PLATE"]):
            return "sheet_metal"
        if any(kw in name_upper for kw in ["轴", "SHAFT", "销", "PIN"]):
            return "machined"
        if ".SLDASM" in name_upper:
            return "assembly"
        
        return "custom"
    
    @staticmethod
    def _analyze_materials(bom: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析材料分布"""
        # 这里简化处理，实际应从 SW 中提取材料属性
        materials = {}
        
        for item in bom:
            # 从路径或名称中提取材料信息（简化）
            name = item["name"]
            # 实际实现中应查询 SW 材料属性
            pass
        
        return materials
    
    @staticmethod
    def _calculate_total_mass(bom: List[Dict[str, Any]]) -> float:
        """计算总质量"""
        # 简化实现，实际应从 SW 中提取质量属性
        # 这里返回占位值
        return 0.0
