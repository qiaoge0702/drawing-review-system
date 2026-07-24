"""
材料提取器模块
从DXF文件中提取结构化材料数据，用于人机结合审查

提取内容：
1. BOM 明细表 - 从 INSERT 块属性提取
2. 尺寸标注汇总 - 从 DIMENSION 实体提取
3. 文字内容分类 - 从 TEXT/MTEXT 按内容自动分类
4. 焊接符号清单 - 从 MLEADER/LEADER 识别焊接信息
"""

import re
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

import ezdxf

logger = logging.getLogger(__name__)


# ─── 常量 ───

# DIMENSION 类型映射（dimtype 低位）
DIMENSION_TYPES = {
    0: "linear",        # 线性标注
    1: "aligned",       # 对齐标注
    2: "angular",       # 角度标注
    3: "diameter",      # 直径标注
    4: "radius",        # 半径标注
    5: "angular3p",     # 三点角度标注
    6: "ordinate",      # 坐标标注
}

# BOM 常见属性标签映射（中英文）
BOM_ATTR_TAGS = {
    "item": ["件号", "序号", "ITEM", "PART", "NO", "ITEM_NO", "PART_NO", "序"],
    "name": ["名称", "零件名称", "部件名称", "NAME", "PART_NAME", "DESCRIPTION", "DESC", "名称描述"],
    "qty": ["数量", "QTY", "QUANTITY", "COUNT", "NUM", "数量N"],
    "material": ["材料", "材质", "MATERIAL", "MAT", "材料牌号"],
    "spec": ["规格", "SPEC", "SPECIFICATION", "尺寸", "SIZE", "规格型号"],
    "weight": ["重量", "WEIGHT", "质量", "MASS", "单重"],
    "remark": ["备注", "REMARK", "NOTE", "NOTES", "说明"],
}

# 焊接符号关键词
WELD_KEYWORDS = [
    "焊", "角焊", "对接", "塞焊", "点焊", "坡口",
    "weld", "fillet", "butt", "plug", "spot",
    "K焊", "V焊", "U焊", "I焊",
    "焊脚", "焊缝", "满焊", "断焊", "围焊",
    "探伤", "UT", "RT", "MT", "PT",
]

# 技术要求关键词
TECH_REQ_KEYWORDS = [
    "技术要求", "技术条件", "要求", "未注", "一般公差",
    "GB/T", "JB/T", "GB", "JB",
    "淬火", "回火", "调质", "正火", "退火",
    "表面处理", "发黑", "镀锌", "镀铬", "喷漆", "涂装",
    "Ra", "粗糙度",
    "倒角", "倒钝", "锐边倒钝",
    "未注圆角", "未注倒角",
]

# 标题栏关键词（用于排除标题栏文字）
TITLE_BLOCK_KEYWORDS = [
    "图样名称", "图号", "比例", "材料", "设计", "审核", "批准",
    "日期", "单位", "重量", "图样代号",
]


@dataclass
class BOMItem:
    """BOM 明细表条目"""
    item_no: str = ""        # 件号
    name: str = ""           # 名称
    qty: str = ""            # 数量
    material: str = ""       # 材料
    spec: str = ""           # 规格
    weight: str = ""         # 重量
    remark: str = ""         # 备注
    block_name: str = ""      # 来源块名
    insert_point: Tuple[float, float] = (0, 0)

    def to_dict(self) -> dict:
        return {
            "item_no": self.item_no,
            "name": self.name,
            "qty": self.qty,
            "material": self.material,
            "spec": self.spec,
            "weight": self.weight,
            "remark": self.remark,
            "block_name": self.block_name,
        }


@dataclass
class DimensionItem:
    """尺寸标注条目"""
    dim_type: str = ""       # 标注类型
    measurement: str = ""    # 测量值
    text: str = ""            # 标注文字
    layer: str = ""           # 图层
    insert_point: Tuple[float, float] = (0, 0)

    def to_dict(self) -> dict:
        return {
            "dim_type": self.dim_type,
            "measurement": self.measurement,
            "text": self.text,
            "layer": self.layer,
        }


@dataclass
class TextItem:
    """文字内容条目"""
    content: str = ""
    category: str = "other"   # tech_requirement / title_block / dimension / annotation / other
    text_type: str = "text"  # text / mtext
    x: float = 0
    y: float = 0
    height: float = 0

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "category": self.category,
            "text_type": self.text_type,
        }


@dataclass
class WeldItem:
    """焊接符号条目"""
    text: str = ""            # 焊接文字内容
    weld_type: str = ""       # 焊缝类型推断
    location: Tuple[float, float] = (0, 0)
    source_type: str = ""     # mleader / leader / insert / text / mtext
    layer: str = ""
    block_name: str = ""      # 来源块名（INSERT 类型时）

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "weld_type": self.weld_type,
            "source_type": self.source_type,
            "layer": self.layer,
        }


class MaterialExtractor:
    """
    材料提取器

    从 DXF 模型空间提取四类结构化材料数据：
    1. BOM 明细表（INSERT + ATTRIB）
    2. 尺寸标注汇总（DIMENSION）
    3. 文字内容分类（TEXT/MTEXT）
    4. 焊接符号清单（MLEADER/LEADER + INSERT）
    """

    def __init__(self, doc: ezdxf.document.Drawing, msp: ezdxf.layouts.Modelspace):
        self.doc = doc
        self.msp = msp

    def extract_all(self) -> Dict[str, Any]:
        """提取所有材料数据"""
        logger.info("开始提取材料数据...")

        bom = self.extract_bom()
        dimensions = self.extract_dimensions()
        texts = self.extract_classified_texts()
        welds = self.extract_weld_symbols()

        result = {
            "bom": {
                "items": [item.to_dict() for item in bom],
                "count": len(bom),
            },
            "dimensions": {
                "items": [item.to_dict() for item in dimensions],
                "count": len(dimensions),
            },
            "texts": {
                "tech_requirements": [t.to_dict() for t in texts if t.category == "tech_requirement"],
                "annotations": [t.to_dict() for t in texts if t.category == "annotation"],
                "title_block": [t.to_dict() for t in texts if t.category == "title_block"],
                "other": [t.to_dict() for t in texts if t.category == "other"],
                "total_count": len(texts),
            },
            "welds": {
                "items": [item.to_dict() for item in welds],
                "count": len(welds),
            },
        }

        logger.info(
            f"材料提取完成: BOM {len(bom)} 条, "
            f"标注 {len(dimensions)} 条, "
            f"文字 {len(texts)} 条, "
            f"焊接 {len(welds)} 条"
        )
        return result

    # ═══════════════════════════════════════════
    # BOM 提取
    # ═══════════════════════════════════════════

    def extract_bom(self) -> List[BOMItem]:
        """
        从 INSERT 实体的 ATTRIB 属性提取 BOM 明细表

        策略：
        1. 遍历所有 INSERT 实体
        2. 对每个 INSERT，检查是否有 ATTRIB（块属性）
        3. 将属性标签映射到 BOM 字段
        """
        bom_items: List[BOMItem] = []

        for entity in self.msp:
            if entity.dxftype() != "INSERT":
                continue

            try:
                # 获取块属性
                attrs = self._get_insert_attribs(entity)
                if not attrs:
                    continue

                # 尝试将属性映射到 BOM 字段
                bom = self._map_attribs_to_bom(attrs, entity)
                if bom and (bom.item_no or bom.name):
                    bom_items.append(bom)

            except Exception as e:
                logger.debug(f"提取 INSERT BOM 失败: {e}")

        # 按件号排序
        bom_items.sort(key=lambda b: self._natural_sort_key(b.item_no))

        logger.info(f"BOM 提取: {len(bom_items)} 条")
        return bom_items

    def _get_insert_attribs(self, entity) -> Dict[str, str]:
        """获取 INSERT 实体的所有属性（ATTRIB）"""
        attrs: Dict[str, str] = {}
        try:
            if hasattr(entity, "attribs"):
                for attrib in entity.attribs:
                    tag = attrib.dxf.tag.strip()
                    value = ""
                    try:
                        value = attrib.dxf.text.strip()
                    except Exception:
                        pass
                    if tag and value:
                        attrs[tag] = value
        except Exception:
            pass
        return attrs

    def _map_attribs_to_bom(self, attrs: Dict[str, str], entity) -> Optional[BOMItem]:
        """将属性字典映射到 BOM 字段"""
        bom = BOMItem()

        try:
            bom.block_name = entity.dxf.name
            insert = entity.dxf.insert
            bom.insert_point = (insert.x, insert.y)
        except Exception:
            pass

        # 对每个 BOM 字段，尝试匹配属性标签
        for field, tags in BOM_ATTR_TAGS.items():
            for attr_tag, attr_value in attrs.items():
                tag_upper = attr_tag.upper().replace(" ", "_")
                for keyword in tags:
                    kw_upper = keyword.upper().replace(" ", "_")
                    if kw_upper in tag_upper or tag_upper in kw_upper:
                        if field == "item":
                            bom.item_no = attr_value
                        elif field == "name":
                            bom.name = attr_value
                        elif field == "qty":
                            bom.qty = attr_value
                        elif field == "material":
                            bom.material = attr_value
                        elif field == "spec":
                            bom.spec = attr_value
                        elif field == "weight":
                            bom.weight = attr_value
                        elif field == "remark":
                            bom.remark = attr_value
                        break

        return bom

    @staticmethod
    def _natural_sort_key(s: str) -> tuple:
        """自然排序键（数字部分按数值排序）"""
        def try_int(part):
            try:
                return (0, int(part))
            except ValueError:
                return (1, part)

        parts = re.split(r"(\d+)", s or "")
        return tuple(try_int(p) for p in parts)

    # ═══════════════════════════════════════════
    # 尺寸标注汇总
    # ═══════════════════════════════════════════

    def extract_dimensions(self) -> List[DimensionItem]:
        """
        从 DIMENSION 实体提取标注汇总

        提取：标注类型、测量值、标注文字、图层、位置
        """
        dim_items: List[DimensionItem] = []

        for entity in self.msp:
            if entity.dxftype() != "DIMENSION":
                continue

            try:
                dim = DimensionItem()

                # 标注类型
                dim_type_code = getattr(entity.dxf, "dimtype", 0)
                # dimtype 的低位是类型，高位是标记位
                dim.dim_type = DIMENSION_TYPES.get(
                    dim_type_code & 0x0F, f"unknown({dim_type_code})"
                )

                # 测量值
                try:
                    if hasattr(entity, "get_measurement"):
                        measurement = entity.get_measurement()
                        if measurement is not None:
                            dim.measurement = f"{measurement:.2f}"
                except Exception:
                    pass

                # 标注文字
                try:
                    if hasattr(entity, "get_text"):
                        dim.text = entity.get_text() or ""
                except Exception:
                    pass

                # 如果没有文字，用测量值
                if not dim.text and dim.measurement:
                    dim.text = dim.measurement

                # 图层
                dim.layer = getattr(entity.dxf, "layer", "")

                # 位置
                try:
                    insert = entity.dxf.insert
                    dim.insert_point = (insert.x, insert.y)
                except Exception:
                    pass

                if dim.text or dim.measurement:
                    dim_items.append(dim)

            except Exception as e:
                logger.debug(f"提取标注失败: {e}")

        logger.info(f"标注提取: {len(dim_items)} 条")
        return dim_items

    # ═══════════════════════════════════════════
    # 文字内容分类
    # ═══════════════════════════════════════════

    def extract_classified_texts(self) -> List[TextItem]:
        """
        从 TEXT/MTEXT 提取所有文字并自动分类

        分类规则：
        - tech_requirement: 技术要求（含"技术要求"等关键词）
        - title_block: 标题栏信息（含"图样名称"等关键词）
        - dimension: 尺寸标注文字（纯数字/角度/直径等）
        - annotation: 注释说明（含中文描述的非技术要求文字）
        - other: 其他
        """
        text_items: List[TextItem] = []

        for entity in self.msp:
            entity_type = entity.dxftype()
            if entity_type not in ("TEXT", "MTEXT"):
                continue

            try:
                # 获取文字内容
                if entity_type == "TEXT":
                    content = str(entity.dxf.text).strip()
                    x = entity.dxf.insert.x
                    y = entity.dxf.insert.y
                    height = entity.dxf.height
                    text_type = "text"
                else:  # MTEXT
                    content = self._clean_mtext(entity.text)
                    x = entity.dxf.insert.x
                    y = entity.dxf.insert.y
                    height = getattr(entity.dxf, "char_height", 5)
                    text_type = "mtext"

                if not content:
                    continue

                # 分类
                category = self._classify_text(content)

                item = TextItem(
                    content=content,
                    category=category,
                    text_type=text_type,
                    x=x,
                    y=y,
                    height=height,
                )
                text_items.append(item)

            except Exception as e:
                logger.debug(f"提取文字失败: {e}")

        logger.info(f"文字提取: {len(text_items)} 条")
        return text_items

    def _clean_mtext(self, text: str) -> str:
        """清理 MTEXT 格式代码"""
        # 解码 \U+XXXX Unicode 转义序列为实际字符
        def decode_unicode(match):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return match.group(0)

        text = re.sub(r"\\U\+([0-9A-Fa-f]{4})", decode_unicode, text)

        # 移除 AutoCAD 格式代码
        text = re.sub(r"\\[A-Za-z]+\d*", "", text)
        text = re.sub(r"\{[^}]*;", "", text)
        text = text.replace("{", "")
        text = text.replace("}", "")
        text = text.replace("\\P", "\n")
        return text.strip()

    def _classify_text(self, content: str) -> str:
        """
        分类文字内容

        返回: tech_requirement / title_block / dimension / annotation / other
        """
        content_upper = content.upper()

        # 1. 技术要求
        for kw in TECH_REQ_KEYWORDS:
            if kw.upper() in content_upper:
                return "tech_requirement"

        # 2. 标题栏信息
        for kw in TITLE_BLOCK_KEYWORDS:
            if kw in content:
                return "title_block"

        # 3. 尺寸标注文字（纯数字或带单位/公差符号）
        stripped = content.strip()
        if re.match(r"^[+-]?\d+\.?\d*(mm|°|φ|Φ|R|M\d+)?$|^[+-]?\d+\.?\d*\^", stripped, re.I):
            return "dimension"

        # 角度标注
        if re.match(r"^\d+°\d*'$|^\d+°\d+'?\d*\"$", stripped):
            return "dimension"

        # 直径/半径标注
        if re.match(r"^[φΦRrM]\d+", stripped):
            return "dimension"

        # 4. 注释说明（包含中文字符的非空文字）
        if re.search(r"[\u4e00-\u9fff]", content) and len(content) > 2:
            return "annotation"

        # 5. 其他
        return "other"

    # ═══════════════════════════════════════════
    # 焊接符号识别
    # ═══════════════════════════════════════════

    def extract_weld_symbols(self) -> List[WeldItem]:
        """
        从 MLEADER/LEADER 和焊接符号 INSERT 块识别焊接信息

        策略：
        1. 遍历 MLEADER/LEADER 实体，提取文字内容
        2. 检查文字是否包含焊接关键词
        3. 同时检查 INSERT 块名是否包含焊接相关词
        """
        weld_items: List[WeldItem] = []

        # 1. 从 MLEADER 提取
        for entity in self.msp:
            if entity.dxftype() != "MLEADER":
                continue

            try:
                text = ""
                ctx = entity.context
                if ctx and hasattr(ctx, "has_text") and ctx.has_text:
                    text = self._clean_mtext(ctx.text)

                if text and self._is_weld_text(text):
                    weld = WeldItem(
                        text=text,
                        weld_type=self._guess_weld_type(text),
                        source_type="mleader",
                        layer=getattr(entity.dxf, "layer", ""),
                    )
                    weld_items.append(weld)

            except Exception as e:
                logger.debug(f"提取 MLEADER 焊接信息失败: {e}")

        # 2. 从 LEADER 提取
        for entity in self.msp:
            if entity.dxftype() != "LEADER":
                continue

            try:
                # LEADER 可能没有文字，检查是否有注释
                text = ""
                annotation = entity.annotation
                if annotation:
                    if hasattr(annotation, "text"):
                        text = str(annotation.text)
                    elif hasattr(annotation, "dxf") and hasattr(annotation.dxf, "text"):
                        text = str(annotation.dxf.text)

                if text and self._is_weld_text(text):
                    weld = WeldItem(
                        text=text,
                        weld_type=self._guess_weld_type(text),
                        source_type="leader",
                        layer=getattr(entity.dxf, "layer", ""),
                    )
                    weld_items.append(weld)

            except Exception as e:
                logger.debug(f"提取 LEADER 焊接信息失败: {e}")

        # 3. 从 INSERT 块名提取（焊接符号通常是特定名称的块）
        for entity in self.msp:
            if entity.dxftype() != "INSERT":
                continue

            try:
                block_name = entity.dxf.name.upper()

                # 检查块名是否包含焊接相关词
                weld_block_keywords = ["WELD", "焊接", "焊缝", "FILLET", "BUTT"]
                if any(kw in block_name for kw in weld_block_keywords):
                    # 尝试获取属性文字
                    attrs = self._get_insert_attribs(entity)
                    text = " ".join(attrs.values()) if attrs else block_name

                    if self._is_weld_text(text) or any(kw in block_name for kw in weld_block_keywords):
                        weld = WeldItem(
                            text=text,
                            weld_type=self._guess_weld_type(text),
                            source_type="insert",
                            block_name=entity.dxf.name if hasattr(entity, "dxf") and hasattr(entity.dxf, "name") else "",
                            layer=getattr(entity.dxf, "layer", ""),
                        )
                        # 设置位置
                        try:
                            insert = entity.dxf.insert
                            weld.location = (insert.x, insert.y)
                        except Exception:
                            pass
                        weld_items.append(weld)

            except Exception as e:
                logger.debug(f"提取 INSERT 焊接块失败: {e}")

        # 4. 从 TEXT/MTEXT 中提取焊接相关文字
        for entity in self.msp:
            entity_type = entity.dxftype()
            if entity_type not in ("TEXT", "MTEXT"):
                continue

            try:
                if entity_type == "TEXT":
                    content = str(entity.dxf.text).strip()
                else:
                    content = self._clean_mtext(entity.text)

                if content and self._is_weld_text(content):
                    # 避免重复（MLEADER/LEADER 可能已提取过相同文字）
                    if not any(w.text == content for w in weld_items):
                        weld = WeldItem(
                            text=content,
                            weld_type=self._guess_weld_type(content),
                            source_type=entity_type.lower(),
                            layer=getattr(entity.dxf, "layer", ""),
                        )
                        try:
                            insert = entity.dxf.insert
                            weld.location = (insert.x, insert.y)
                        except Exception:
                            pass
                        weld_items.append(weld)

            except Exception as e:
                logger.debug(f"提取文字焊接信息失败: {e}")

        logger.info(f"焊接符号提取: {len(weld_items)} 条")
        return weld_items

    def _is_weld_text(self, text: str) -> bool:
        """判断文字是否与焊接相关"""
        text_upper = text.upper()
        for kw in WELD_KEYWORDS:
            if kw.upper() in text_upper:
                return True
        return False

    def _guess_weld_type(self, text: str) -> str:
        """根据文字内容推断焊缝类型"""
        text_lower = text.lower()

        if "角焊" in text or "fillet" in text_lower or "K焊" in text:
            return "fillet"
        elif "对接" in text or "butt" in text_lower or "V焊" in text or "U焊" in text:
            return "butt"
        elif "塞焊" in text or "plug" in text_lower:
            return "plug"
        elif "点焊" in text or "spot" in text_lower:
            return "spot"
        elif "满焊" in text:
            return "full"
        elif "断焊" in text or "段焊" in text:
            return "intermittent"
        elif "围焊" in text:
            return "around"
        else:
            return "unknown"
