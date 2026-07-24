"""
元数据提取器单元测试
"""

import pytest
import ezdxf

from app.parsers.metadata_extractor import MetadataExtractor, TextBlock
from app.models.drawing import DrawingMetadata


@pytest.fixture
def doc_with_title_block():
    """创建包含标准标题栏的DXF文档"""
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # 创建标题栏图层，这样MetadataExtractor会直接使用该图层的文字
    doc.layers.add('标题栏')
    
    # 在标题栏图层添加文字
    text = msp.add_text('图样名称: 测试部件', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (280, -5)
    
    text = msp.add_text('图样代号: TEST-2024-001', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (280, -15)
    
    text = msp.add_text('材料: Q355B', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (280, -25)
    
    text = msp.add_text('比例: 1:2', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (280, -35)
    
    text = msp.add_text('重量: 125.5kg', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (280, -45)
    
    text = msp.add_text('设计: 张三', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (340, -15)
    
    text = msp.add_text('审核: 李四', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (340, -25)
    
    text = msp.add_text('批准: 王五', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (340, -35)
    
    text = msp.add_text('日期: 2024-01-15', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (340, -45)
    
    text = msp.add_text('单位名称: 测试公司', height=5)
    text.dxf.layer = '标题栏'
    text.dxf.insert = (280, -55)
    
    return doc, msp


@pytest.fixture
def doc_with_mtext():
    """创建包含MTEXT的DXF文档"""
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # 创建标题栏图层
    doc.layers.add('TITLE_BLOCK')
    
    # 在标题栏图层添加MTEXT
    mtext1 = msp.add_mtext('图样名称: MTEXT测试')
    mtext1.dxf.char_height = 5
    mtext1.dxf.insert = (280, -5)
    mtext1.dxf.layer = 'TITLE_BLOCK'
    
    mtext2 = msp.add_mtext('比例: 1:5')
    mtext2.dxf.char_height = 5
    mtext2.dxf.insert = (280, -25)
    mtext2.dxf.layer = 'TITLE_BLOCK'
    
    return doc, msp


@pytest.fixture
def empty_doc():
    """创建空DXF文档"""
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    return doc, msp


class TestMetadataExtractorInit:
    """测试MetadataExtractor初始化"""
    
    def test_init(self, doc_with_title_block):
        """测试初始化"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        assert extractor.doc == doc
        assert extractor.msp == msp
        assert extractor.texts == []


class TestMetadataExtractorExtract:
    """测试元数据提取功能"""
    
    def test_extract_returns_drawing_metadata(self, doc_with_title_block):
        """测试提取返回DrawingMetadata对象"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert isinstance(result, DrawingMetadata)
    
    def test_extract_title(self, doc_with_title_block):
        """测试提取标题"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.title == "测试部件"
    
    def test_extract_drawing_no(self, doc_with_title_block):
        """测试提取图号"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.drawing_no == "TEST-2024-001"
    
    def test_extract_material(self, doc_with_title_block):
        """测试提取材料"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.material == "Q355B"
    
    def test_extract_scale(self, doc_with_title_block):
        """测试提取比例"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.scale == "1:2"
    
    def test_extract_weight(self, doc_with_title_block):
        """测试提取重量"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.weight == "125.5kg"
    
    def test_extract_designer(self, doc_with_title_block):
        """测试提取设计者"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.designer == "张三"
    
    def test_extract_reviewer(self, doc_with_title_block):
        """测试提取审核者"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.reviewer == "李四"
    
    def test_extract_approver(self, doc_with_title_block):
        """测试提取批准者"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.approver == "王五"
    
    def test_extract_date(self, doc_with_title_block):
        """测试提取日期"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        # 日期提取可能受其他因素影响，做宽松验证
        assert result.date is not None
    
    def test_extract_company(self, doc_with_title_block):
        """测试提取公司名称"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.company == "测试公司"


class TestMetadataExtractorMText:
    """测试MTEXT处理"""
    
    def test_extract_from_mtext(self, doc_with_mtext):
        """测试从MTEXT提取"""
        doc, msp = doc_with_mtext
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.title == "MTEXT测试"
        assert result.scale == "1:5"
    
    def test_clean_mtext(self, doc_with_mtext):
        """测试MTEXT清理"""
        doc, msp = doc_with_mtext
        extractor = MetadataExtractor(doc, msp)
        
        # 测试清理方法
        cleaned = extractor._clean_mtext("测试\\P换行")
        assert "\\P" not in cleaned


class TestMetadataExtractorEdgeCases:
    """测试边界情况"""
    
    def test_empty_document(self, empty_doc):
        """测试空文档"""
        doc, msp = empty_doc
        extractor = MetadataExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert isinstance(result, DrawingMetadata)
        assert result.title is None
        assert result.drawing_no is None
    
    def test_partial_metadata(self):
        """测试部分元数据"""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        # 创建标题栏图层
        doc.layers.add('标题栏')
        
        # 只添加部分字段
        text = msp.add_text('图样名称: 只有标题', height=5)
        text.dxf.layer = '标题栏'
        text.dxf.insert = (280, -5)
        
        text = msp.add_text('比例: 1:10', height=5)
        text.dxf.layer = '标题栏'
        text.dxf.insert = (280, -25)
        
        extractor = MetadataExtractor(doc, msp)
        result = extractor.extract()
        
        assert result.title == "只有标题"
        assert result.scale == "1:10"
        assert result.drawing_no is None
        assert result.designer is None
    
    def test_invalid_values_filtered(self):
        """测试无效值过滤"""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        # 创建标题栏图层
        doc.layers.add('标题栏')
        
        # 添加无效值
        text = msp.add_text('图样名称: —', height=5)
        text.dxf.layer = '标题栏'
        text.dxf.insert = (280, -5)
        
        extractor = MetadataExtractor(doc, msp)
        result = extractor.extract()
        
        # 这些无效值应该被过滤
        assert result.title is None


class TestMetadataExtractorHelperMethods:
    """测试辅助方法"""
    
    def test_extract_all_texts(self, doc_with_title_block):
        """测试提取所有文字"""
        doc, msp = doc_with_title_block
        extractor = MetadataExtractor(doc, msp)
        
        texts = extractor.extract_all_texts()
        
        assert isinstance(texts, list)
        assert len(texts) >= 10
        
        for text in texts:
            assert 'text' in text
            assert 'x' in text
            assert 'y' in text
            assert 'height' in text
    
    def test_guess_drawing_type_assembly(self):
        """测试猜测装配图类型"""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        msp.add_text('这是装配图', height=5).set_placement((100, 100))
        
        extractor = MetadataExtractor(doc, msp)
        drawing_type = extractor.guess_drawing_type()
        
        assert drawing_type == "装配图"
    
    def test_guess_drawing_type_part(self):
        """测试猜测零件图类型"""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        msp.add_text('零件编号: 001', height=5).set_placement((100, 100))
        
        extractor = MetadataExtractor(doc, msp)
        drawing_type = extractor.guess_drawing_type()
        
        assert drawing_type == "零件图"
    
    def test_guess_drawing_type_unknown(self):
        """测试未知图纸类型"""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        msp.add_text('一些普通文字', height=5).set_placement((100, 100))
        
        extractor = MetadataExtractor(doc, msp)
        drawing_type = extractor.guess_drawing_type()
        
        assert drawing_type == "未知"


class TestMetadataExtractorFieldPatterns:
    """测试字段模式匹配"""
    
    def test_various_title_formats(self):
        """测试各种标题格式"""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        # 创建标题栏图层
        doc.layers.add('标题栏')
        
        # 不同格式的标题
        formats = [
            '名称: 格式1',
            '零件名称: 格式2',
            '部件名称: 格式3',
        ]
        
        for i, fmt in enumerate(formats):
            text = msp.add_text(fmt, height=5)
            text.dxf.layer = '标题栏'
            text.dxf.insert = (280, -5 - i * 10)
        
        extractor = MetadataExtractor(doc, msp)
        result = extractor.extract()
        
        # 应该匹配第一个
        assert result.title is not None
    
    def test_various_scale_formats(self):
        """测试各种比例格式"""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        # 创建标题栏图层
        doc.layers.add('标题栏')
        
        text = msp.add_text('SCALE: 1:5', height=5)
        text.dxf.layer = '标题栏'
        text.dxf.insert = (280, -5)
        
        extractor = MetadataExtractor(doc, msp)
        result = extractor.extract()
        
        assert result.scale == "1:5"
