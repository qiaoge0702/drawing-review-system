"""
实体提取器单元测试
"""

import pytest
import ezdxf

from app.parsers.entity_extractor import EntityExtractor
from app.models.drawing import ExtractedEntities, LayerInfo


@pytest.fixture
def sample_doc():
    """创建包含各种实体的DXF文档"""
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # 添加各种实体
    msp.add_line((0, 0), (100, 100))
    msp.add_circle((50, 50), 25)
    msp.add_arc((50, 50), 30, 0, 90)
    msp.add_lwpolyline([(0, 0), (50, 0), (50, 50), (0, 50)], close=True)
    msp.add_text('测试文字', height=5).set_placement((10, 10))
    mtext = msp.add_mtext('多行\\n文字')
    mtext.dxf.char_height = 5
    mtext.dxf.insert = (20, 20)
    msp.add_ellipse((50, 50), (30, 0), 0.5)
    
    # 添加标注
    msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(100, 0))
    
    # 添加填充
    hatch = msp.add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)
    
    return doc, msp


class TestEntityExtractorInit:
    """测试EntityExtractor初始化"""
    
    def test_init(self, sample_doc):
        """测试初始化"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        assert extractor.doc == doc
        assert extractor.msp == msp


class TestEntityExtractorExtract:
    """测试实体提取功能"""
    
    def test_extract_returns_extracted_entities(self, sample_doc):
        """测试提取返回ExtractedEntities对象"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert isinstance(result, ExtractedEntities)
    
    def test_extract_counts_lines(self, sample_doc):
        """测试直线计数"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.line_count >= 1
    
    def test_extract_counts_circles(self, sample_doc):
        """测试圆计数"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.circle_count >= 1
    
    def test_extract_counts_arcs(self, sample_doc):
        """测试圆弧计数"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.arc_count >= 1
    
    def test_extract_counts_lwpolylines(self, sample_doc):
        """测试轻量多段线计数"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.lwpolyline_count >= 1
    
    def test_extract_counts_texts(self, sample_doc):
        """测试文字计数"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.text_count >= 1
        assert result.mtext_count >= 1
    
    def test_extract_counts_ellipses(self, sample_doc):
        """测试椭圆计数"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.ellipse_count >= 1
    
    def test_extract_counts_dimensions(self, sample_doc):
        """测试标注计数"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.dimension_count >= 1
    
    def test_extract_counts_hatches(self, sample_doc):
        """测试填充计数"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.hatch_count >= 1
    
    def test_extract_extracts_layers(self, sample_doc):
        """测试图层提取"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.layer_count >= 1
        assert len(result.layers) == result.layer_count
        assert all(isinstance(layer, LayerInfo) for layer in result.layers)
    
    def test_extract_extracts_entities_by_type(self, sample_doc):
        """测试按类型分组提取实体"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert 'line' in result.entities
        assert 'circle' in result.entities
        assert len(result.entities['line']) >= 1
        assert len(result.entities['circle']) >= 1
    
    def test_extract_total_count(self, sample_doc):
        """测试实体总数"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        total = result.get_total_entity_count()
        assert total > 0
        assert total == (
            result.line_count +
            result.circle_count +
            result.arc_count +
            result.lwpolyline_count +
            result.text_count +
            result.mtext_count +
            result.ellipse_count +
            result.dimension_count +
            result.hatch_count
        )


class TestEntityExtractorEntityData:
    """测试实体数据提取"""
    
    def test_extract_line_data(self, sample_doc):
        """测试直线数据提取"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        lines = result.entities.get('line', [])
        
        assert len(lines) >= 1
        line = lines[0]
        assert 'start' in line
        assert 'end' in line
        assert 'length' in line
        assert isinstance(line['start'], tuple)
        assert isinstance(line['end'], tuple)
    
    def test_extract_circle_data(self, sample_doc):
        """测试圆数据提取"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        circles = result.entities.get('circle', [])
        
        assert len(circles) >= 1
        circle = circles[0]
        assert 'center' in circle
        assert 'radius' in circle
        assert 'diameter' in circle
    
    def test_extract_text_data(self, sample_doc):
        """测试文字数据提取"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        texts = result.entities.get('text', [])
        
        assert len(texts) >= 1
        text = texts[0]
        assert 'text' in text
        assert 'insert' in text
        assert 'height' in text


class TestEntityExtractorHelperMethods:
    """测试辅助方法"""
    
    def test_get_entities_in_layer(self, sample_doc):
        """测试按图层获取实体"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        # 获取0图层的实体
        entities = extractor.get_entities_in_layer('0')
        
        assert isinstance(entities, list)
        assert len(entities) > 0
    
    def test_get_entities_by_type(self, sample_doc):
        """测试按类型获取实体"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        lines = extractor.get_entities_by_type('LINE')
        circles = extractor.get_entities_by_type('CIRCLE')
        
        assert len(lines) >= 1
        assert len(circles) >= 1
    
    def test_get_text_entities(self, sample_doc):
        """测试获取所有文字实体"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        texts = extractor.get_text_entities()
        
        assert isinstance(texts, list)
        assert len(texts) >= 1  # 至少有一个文字实体
    
    def test_get_line_segments(self, sample_doc):
        """测试获取线段列表"""
        doc, msp = sample_doc
        extractor = EntityExtractor(doc, msp)
        
        segments = extractor.get_line_segments()
        
        assert isinstance(segments, list)
        assert len(segments) >= 1
        # 每个segment是(start, end)元组
        for segment in segments:
            assert len(segment) == 2
            assert len(segment[0]) == 2  # (x, y)
            assert len(segment[1]) == 2


class TestEntityExtractorEdgeCases:
    """测试边界情况"""
    
    def test_empty_document(self):
        """测试空文档"""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        extractor = EntityExtractor(doc, msp)
        
        result = extractor.extract()
        
        assert result.get_total_entity_count() == 0
        assert not result.has_entities()
    
    def test_document_with_only_blocks(self):
        """测试只包含块引用的文档"""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        # 创建块定义
        block = doc.blocks.new('TEST_BLOCK')
        block.add_line((0, 0), (10, 10))
        
        # 插入块引用
        msp.add_blockref('TEST_BLOCK', (0, 0))
        
        extractor = EntityExtractor(doc, msp)
        result = extractor.extract()
        
        assert result.insert_count >= 1
