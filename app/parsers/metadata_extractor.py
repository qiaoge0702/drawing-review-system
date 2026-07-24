"""
标题栏元数据提取器模块
从DXF图纸中提取标题栏信息
"""

import re
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

import ezdxf

from app.models.drawing import DrawingMetadata

logger = logging.getLogger(__name__)


@dataclass
class TextBlock:
    """文本块，用于标题栏分析"""
    text: str
    x: float
    y: float
    height: float
    is_mtext: bool = False


class MetadataExtractor:
    """
    标题栏元数据提取器
    
    从DXF图纸的标题栏区域提取标准信息：
    - 图样名称
    - 图样代号
    - 材料标记
    - 比例
    - 重量
    - 设计/审核/批准
    - 日期
    - 单位名称
    """
    
    # 标题栏字段关键词映射
    FIELD_PATTERNS = {
        'title': [
            r'图样名称[：:]?\s*(.+)',
            r'名称[：:]?\s*(.+)',
            r'零件名称[：:]?\s*(.+)',
            r'部件名称[：:]?\s*(.+)',
        ],
        'drawing_no': [
            r'图样代号[：:]?\s*(.+)',
            r'图号[：:]?\s*(.+)',
            r'代号[：:]?\s*(.+)',
            r'编号[：:]?\s*(.+)',
        ],
        'material': [
            r'材料标记[：:]?\s*(.+)',
            r'材料[：:]?\s*(.+)',
            r'材质[：:]?\s*(.+)',
        ],
        'scale': [
            r'比例[：:]?\s*(.+)',  
            r'SCALE[：:]?\s*(.+)',
        ],
        'weight': [
            r'重量[：:]?\s*(.+)',
            r'WEIGHT[：:]?\s*(.+)',
            r'质量[：:]?\s*(.+)',
        ],
        'designer': [
            r'设计[：:]?\s*(.+)',
            r'设计人[：:]?\s*(.+)',
            r'DESIGNER[：:]?\s*(.+)',
            r'DESIGNED[：:]?\s*(.+)',
        ],
        'reviewer': [
            r'审核[：:]?\s*(.+)',
            r'校核[：:]?\s*(.+)',
            r'REVIEWER[：:]?\s*(.+)',
            r'CHECKED[：:]?\s*(.+)',
        ],
        'approver': [
            r'批准[：:]?\s*(.+)',
            r'审批[：:]?\s*(.+)',
            r'APPROVER[：:]?\s*(.+)',
            r'APPROVED[：:]?\s*(.+)',
        ],
        'date': [
            r'日期[：:]?\s*(.+)',
            r'DATE[：:]?\s*(.+)',
        ],
        'company': [
            r'单位名称[：:]?\s*(.+)',
            r'单位[：:]?\s*(.+)',
            r'COMPANY[：:]?\s*(.+)',
            r'企业名称[：:]?\s*(.+)',
        ],
    }
    
    # 标题栏常见图层名
    TITLE_BLOCK_LAYERS = [
        '标题栏', 'TITLE', 'TITLEBLOCK', 'TITLE_BLOCK',
        '图框', 'BORDER', 'FRAME', '图签',
    ]
    
    def __init__(self, doc: ezdxf.document.Drawing, msp: ezdxf.layouts.Modelspace):
        """
        初始化提取器
        
        Args:
            doc: ezdxf文档对象
            msp: 模型空间
        """
        self.doc = doc
        self.msp = msp
        self.texts: List[TextBlock] = []
    
    def extract(self) -> DrawingMetadata:
        """
        提取标题栏元数据
        
        Returns:
            DrawingMetadata: 提取的元数据
        """
        logger.debug("开始提取标题栏元数据")
        
        # 1. 收集所有文字实体
        self._collect_texts()
        
        # 2. 识别标题栏区域
        title_block_texts = self._identify_title_block()
        
        # 3. 提取各字段
        metadata = DrawingMetadata(
            title=self._extract_field('title', title_block_texts),
            drawing_no=self._extract_field('drawing_no', title_block_texts),
            material=self._extract_field('material', title_block_texts),
            scale=self._extract_field('scale', title_block_texts),
            weight=self._extract_field('weight', title_block_texts),
            designer=self._extract_field('designer', title_block_texts),
            reviewer=self._extract_field('reviewer', title_block_texts),
            approver=self._extract_field('approver', title_block_texts),
            date=self._extract_field('date', title_block_texts),
            company=self._extract_field('company', title_block_texts),
        )
        
        logger.debug(f"元数据提取完成: {metadata.model_dump()}")
        return metadata
    
    def _collect_texts(self) -> None:
        """收集所有文字实体"""
        self.texts = []
        
        for entity in self.msp:
            entity_type = entity.dxftype()
            
            try:
                if entity_type == 'TEXT':
                    text = TextBlock(
                        text=str(entity.dxf.text).strip(),
                        x=entity.dxf.insert.x,
                        y=entity.dxf.insert.y,
                        height=entity.dxf.height,
                        is_mtext=False
                    )
                    self.texts.append(text)
                    
                elif entity_type == 'MTEXT':
                    # MTEXT可能包含格式代码，需要清理
                    raw_text = entity.text
                    clean_text = self._clean_mtext(raw_text)
                    
                    text = TextBlock(
                        text=clean_text.strip(),
                        x=entity.dxf.insert.x,
                        y=entity.dxf.insert.y,
                        height=getattr(entity.dxf, 'char_height', 5),
                        is_mtext=True
                    )
                    self.texts.append(text)
                    
            except Exception as e:
                logger.debug(f"提取文字实体失败: {e}")
        
        logger.debug(f"收集到 {len(self.texts)} 个文字实体")
    
    def _clean_mtext(self, text: str) -> str:
        """清理MTEXT格式代码"""
        # 移除AutoCAD格式代码，如 {\fSimSun|b0|i0|c134|p2;
        text = re.sub(r'\\[A-Z]+\d*', '', text)
        text = re.sub(r'\{[^}]*;', '', text)
        text = text.replace('}', '')
        text = text.replace('\\P', '\n')  # 段落分隔符
        return text.strip()
    
    def _identify_title_block(self) -> List[TextBlock]:
        """
        识别标题栏区域的文字
        
        策略：
        1. 优先查找标题栏图层
        2. 回退：查找图纸右下角区域的文字
        """
        # 策略1: 按图层筛选
        title_block_layer_texts = self._get_texts_in_title_block_layers()
        if title_block_layer_texts:
            logger.debug(f"从标题栏图层找到 {len(title_block_layer_texts)} 个文字")
            return title_block_layer_texts
        
        # 策略2: 按位置筛选（右下角区域）
        bottom_right_texts = self._get_texts_in_bottom_right()
        if bottom_right_texts:
            logger.debug(f"从右下角区域找到 {len(bottom_right_texts)} 个文字")
            return bottom_right_texts
        
        # 策略3: 返回所有文字
        logger.debug("未识别到标题栏区域，使用所有文字")
        return self.texts
    
    def _get_texts_in_title_block_layers(self) -> List[TextBlock]:
        """获取标题栏图层中的文字"""
        result = []
        
        for entity in self.msp:
            entity_type = entity.dxftype()
            if entity_type not in ('TEXT', 'MTEXT'):
                continue
            
            try:
                layer = entity.dxf.layer.upper()
                if any(tb_layer.upper() in layer for tb_layer in self.TITLE_BLOCK_LAYERS):
                    if entity_type == 'TEXT':
                        result.append(TextBlock(
                            text=str(entity.dxf.text).strip(),
                            x=entity.dxf.insert.x,
                            y=entity.dxf.insert.y,
                            height=entity.dxf.height,
                            is_mtext=False
                        ))
                    else:  # MTEXT
                        result.append(TextBlock(
                            text=self._clean_mtext(entity.text).strip(),
                            x=entity.dxf.insert.x,
                            y=entity.dxf.insert.y,
                            height=getattr(entity.dxf, 'char_height', 5),
                            is_mtext=True
                        ))
            except Exception:
                pass
        
        return result
    
    def _get_texts_in_bottom_right(self) -> List[TextBlock]:
        """获取图纸右下角区域的文字"""
        if not self.texts:
            return []
        
        # 计算图纸范围
        min_x = min(t.x for t in self.texts)
        max_x = max(t.x for t in self.texts)
        min_y = min(t.y for t in self.texts)
        max_y = max(t.y for t in self.texts)
        
        width = max_x - min_x
        height = max_y - min_y
        
        # 定义右下角区域（右30%，下30%）
        # 右下角：x在右侧30%范围内，y在底部30%范围内
        right_threshold = max_x - width * 0.3
        bottom_threshold = max_y - height * 0.3
        
        return [
            t for t in self.texts
            if t.x >= right_threshold and t.y <= bottom_threshold
        ]
    
    def _extract_field(self, field_name: str, texts: List[TextBlock]) -> Optional[str]:
        """
        提取指定字段
        
        Args:
            field_name: 字段名
            texts: 待搜索的文字列表
            
        Returns:
            提取的值，未找到返回None
        """
        patterns = self.FIELD_PATTERNS.get(field_name, [])
        
        for text_block in texts:
            text = text_block.text
            
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    value = match.group(1).strip()
                    # 清理值
                    value = self._clean_value(value)
                    if value:
                        return value
        
        return None
    
    def _clean_value(self, value: str) -> str:
        """清理提取的值"""
        # 移除常见分隔符
        value = re.sub(r'^[：:\s]+', '', value)
        value = re.sub(r'[\n\r]+', ' ', value)
        value = value.strip()
        
        # 移除常见无效值
        invalid_values = ['', '—', '-', '/', '无', 'None', 'N/A']
        if value in invalid_values:
            return ''
        
        return value
    
    def extract_all_texts(self) -> List[Dict[str, Any]]:
        """
        提取所有文字（用于调试）
        
        Returns:
            文字列表，包含位置和样式信息
        """
        if not self.texts:
            self._collect_texts()
        
        return [
            {
                'text': t.text,
                'x': t.x,
                'y': t.y,
                'height': t.height,
                'is_mtext': t.is_mtext,
            }
            for t in self.texts
        ]
    
    def guess_drawing_type(self) -> str:
        """
        猜测图纸类型
        
        Returns:
            图纸类型描述
        """
        if not self.texts:
            self._collect_texts()
        
        all_text = ' '.join(t.text for t in self.texts).upper()
        
        # 关键词匹配
        type_keywords = {
            '装配图': ['装配', 'ASSEMBLY', '总装'],
            '零件图': ['零件', 'PART'],
            '焊接图': ['焊接', 'WELD'],
            '布置图': ['布置', 'LAYOUT', '总布置'],
            '原理图': ['原理', 'SCHEMATIC', '液压原理'],
            '外形图': ['外形', 'OUTLINE', '外观'],
        }
        
        for dtype, keywords in type_keywords.items():
            if any(kw in all_text for kw in keywords):
                return dtype
        
        return "未知"
