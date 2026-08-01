"""
Step 2: 几何解析

从 Step 1 的输出中提取 BOM、材料、质量等几何信息。
复用现有 SWParser 的 get_bom 能力。

BOM 条目字段（2026-07-31 包2 扩展）：
    level / name / path / quantity / is_suppressed / type
    material: str   材料名（IPartDoc.GetMaterialPropertyName2，配置相关；
                    真机实证：材料接口在 IPartDoc 而非 IComponent2）；
                    取不到 → 空字符串 + warnings（诚实原则，禁止编造）
    mass: float|""  单件质量 kg（组件 ModelDoc2 的 MassProperty，SW 内部 SI 单位）；
                    装配条目/取不到 → 空字符串 + warnings
Step5 聚合时取同图号首见 material/mass，BOM 表"单重/总重"按 kg 保留 3 位小数。
"""

import logging
import math
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.generators.models import StepContext
from app.generators.sw_com import run_sw
from app.parsers.sw_parser import SWParser
from app.core.exceptions import GenerationException, ErrorCode

logger = logging.getLogger(__name__)


def _walk_components(root: Any):
    """与 sw_parser._traverse_bom 同序的组件先序遍历（根 → 逐子组件递归）"""
    yield root
    try:
        children = root.GetChildren
    except Exception:
        return
    try:
        if isinstance(children, tuple):
            iterable = children
        else:
            iterable = (children.Item(i + 1) for i in range(children.Count))
        for child in iterable:
            if child:
                yield from _walk_components(child)
    except Exception:
        return


_sw_typelib = None


def _early_bind_partdoc(doc: Any) -> Any:
    """
    晚期绑定 ModelDoc2 → 早期绑定 IPartDoc（材料接口所在；晚期绑定调用报
    "非选择性的参数"）。EnsureModule 失败/非零件文档 → None（调用方留空）。
    """
    global _sw_typelib
    try:
        if _sw_typelib is None:
            from win32com.client import gencache
            _sw_typelib = gencache.EnsureModule(
                "{83A33D31-27C5-11CE-BFD4-00400513BB57}", 0, 33, 0)
        return _sw_typelib.IPartDoc(doc._oleobj_)
    except Exception:
        return None


def _comp_material(comp: Any) -> str:
    """
    组件材料名（真机实证 2026-07-31）：材料接口在 IPartDoc 上（返回
    (材料名, 材料库名) 二元组），IComponent2.GetMaterialPropertyName2 晚期绑定
    不存在、IModelDoc2 上无此方法。取不到（装配条目/标准件未设材料/接口异常）
    → 空字符串（如实留空，不报错不编造）。
    """
    doc = None
    try:
        doc = comp.GetModelDoc2
    except Exception:
        return ""
    if doc is None:
        return ""
    cfg = ""
    try:
        cfg = comp.ReferencedConfiguration or ""
    except Exception:
        pass
    # 路径 1：晚期绑定直调（mock 友好；真机多数报"非选择性的参数"）
    try:
        ret = doc.GetMaterialPropertyName2(cfg)
        name = ret[0] if isinstance(ret, (list, tuple)) else ret
        return str(name).strip() if name else ""
    except Exception:
        pass
    # 路径 2：早期绑定 IPartDoc（真机主路径）
    try:
        pdoc = _early_bind_partdoc(doc)
        if pdoc is None:
            return ""
        ret = pdoc.GetMaterialPropertyName2(cfg)
        name = ret[0] if isinstance(ret, (list, tuple)) else ret
        return str(name).strip() if name else ""
    except Exception:
        return ""


def _comp_mass_kg(comp: Any) -> Optional[float]:
    """
    组件单件质量（kg，SW 内部 SI 单位）。
    路径：comp.GetModelDoc2 → Extension.CreateMassProperty2（回退 CreateMassProperty）→ Mass。
    取不到（装配条目/无模型文档等）→ None。
    """
    try:
        doc = comp.GetModelDoc2
        if doc is None:
            return None
        ext = doc.Extension
        mp = None
        try:
            mp = ext.CreateMassProperty2(
                0, getattr(comp, "ReferencedConfiguration", "") or "")
        except Exception:
            try:
                mp = ext.CreateMassProperty
            except Exception:
                return None
        if mp is None:
            return None
        mass = float(mp.Mass)
        return mass if math.isfinite(mass) and mass >= 0 else None
    except Exception:
        return None


def _enrich_bom_material_mass(parser: Any, filepath: str, bom: List[Dict[str, Any]]) -> List[str]:
    """
    【同步/COM线程】真机材料/单重提取（best-effort）：
    按 sw_parser._traverse_bom 同序遍历组件树，逐条填充 bom 条目的
    material / mass 字段；同路径组件质量缓存复用。取不到 → 空 + warnings，禁止编造。
    """
    warnings: List[str] = []
    try:
        doc = parser.open_document(filepath)
        names = doc.GetConfigurationNames
        config = doc.GetConfigurationByName(names[0])
        root = config.GetRootComponent
    except Exception as e:
        msg = f"材料/单重提取整体跳过（组件树不可用: {str(e)[:80]}）"
        logger.warning(msg)
        for item in bom:
            item.setdefault("material", "")
            item.setdefault("mass", "")
        return [msg]

    comps = list(_walk_components(root))
    if len(comps) != len(bom):
        warnings.append(
            f"组件树遍历数({len(comps)})与 BOM 条目数({len(bom)})不一致，"
            "material/mass 按索引对齐可能错位")
        logger.warning(warnings[-1])

    mass_cache: Dict[str, Optional[float]] = {}
    n_mat = n_mass = 0
    n_active = 0
    for item, comp in zip(bom, comps):
        if item.get("is_suppressed"):
            item.setdefault("material", "")
            item.setdefault("mass", "")
            continue
        n_active += 1
        mat = _comp_material(comp)
        item["material"] = mat
        path = item.get("path") or ""
        if path in mass_cache:
            mass = mass_cache[path]
        else:
            mass = _comp_mass_kg(comp)
            mass_cache[path] = mass
        item["mass"] = round(mass, 6) if mass is not None else ""
        if mat:
            n_mat += 1
        if mass is not None:
            n_mass += 1

    if n_active:
        if n_mat < n_active:
            w = f"材料取不到: {n_active - n_mat}/{n_active} 条（已留空）"
            warnings.append(w)
            logger.warning(w)
        if n_mass < n_active:
            w = f"单重取不到: {n_active - n_mass}/{n_active} 条（已留空）"
            warnings.append(w)
            logger.warning(w)
        logger.info(f"material/mass enriched: {n_mat}/{n_active} 材料, {n_mass}/{n_active} 单重")
    return warnings


def _get_bom_sync(filepath: str) -> Tuple[list, List[str]]:
    """【同步/COM线程】提取 BOM + 材料/单重富化，parser 单次创建即释放"""
    parser = SWParser()
    try:
        bom = parser.get_bom(filepath)
        warnings = _enrich_bom_material_mass(parser, filepath, bom)
        return bom, warnings
    finally:
        try:
            parser.close_document(filepath)
        except Exception as e:
            logger.warning(f"Failed to close document: {e}")
        try:
            parser.quit()
        except Exception as e:
            logger.warning(f"Failed to quit SW parser: {e}")


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
            # 获取 BOM（COM 调用卸载到专用线程）
            bom, enrich_warnings = await self._extract_bom(ctx, source_file)
            
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
                "warnings": enrich_warnings,
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
    
    async def _extract_bom(self, ctx: StepContext, filepath: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        """提取 BOM 表（失败时抛错，不静默返回空表）"""
        logger.debug(f"[Task:{ctx.task_id}] Extracting BOM from {filepath}")
        
        raw_bom, warnings = await run_sw(_get_bom_sync, filepath)
        
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
                # 材料/单重（真机提取；取不到为空，诚实原则）
                "material": item.get("material") or "",
                "mass": item.get("mass") if isinstance(item.get("mass"), (int, float)) else "",
            })
        
        return bom, warnings
    
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
        """分析材料分布（按真机提取的 material 字段统计条数）"""
        materials: Dict[str, Any] = {}
        for item in bom:
            mat = item.get("material") or ""
            if mat:
                materials[mat] = materials.get(mat, 0) + 1
        return materials
    
    @staticmethod
    def _calculate_total_mass(bom: List[Dict[str, Any]]) -> float:
        """计算总质量（kg）：Σ 单件质量 × 数量；单重缺失条目跳过"""
        total = 0.0
        for item in bom:
            mass = item.get("mass")
            if isinstance(mass, (int, float)) and not isinstance(mass, bool):
                total += float(mass) * int(item.get("quantity", 1) or 1)
        return round(total, 3)
