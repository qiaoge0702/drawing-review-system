"""
DXF解析器单元测试
"""

import pytest
from pathlib import Path
from datetime import datetime

from app.parsers.dxf_parser import DXFParser, parse_dxf, ParseOptions, DXFParserError
from app.core.exceptions import DXFParseException, ErrorCode


class TestDXFParserInit:
    """测试DXFParser初始化"""
    
    def test_init_with_valid_path(self, create_test_dxf):
        """测试使用有效路径初始化"""
        parser = DXFParser(create_test_dxf)
        assert parser.file_path == create_test_dxf.resolve()
        assert parser.doc is None
        assert parser.msp is None
    
    def test_init_with_nonexistent_file(self):
        """测试使用不存在的文件初始化应抛出异常"""
        with pytest.raises(DXFParseException) as exc_info:
            DXFParser("/nonexistent/path/file.dxf")
        
        assert exc_info.value.error_code == ErrorCode.SYS_FILE_NOT_FOUND
        assert "文件不存在" in exc_info.value.message
    
    def test_init_with_directory(self, tmp_path):
        """测试使用目录路径初始化应抛出异常"""
        with pytest.raises(DXFParseException) as exc_info:
            DXFParser(tmp_path)
        
        assert exc_info.value.error_code == ErrorCode.SYS_FILE_NOT_FOUND
        assert "路径不是文件" in exc_info.value.message


class TestDXFParserFileValidation:
    """测试文件验证"""
    
    def test_file_size_validation(self, tmp_path, monkeypatch):
        """测试文件大小验证"""
        # 创建一个超过限制大小的文件
        from app.core.config import settings
        
        large_file = tmp_path / "too_large.dxf"
        large_file.write_bytes(b" " * (settings.dxf.max_file_size_mb * 1024 * 1024 + 1))
        
        with pytest.raises(DXFParseException) as exc_info:
            DXFParser(large_file)
        
        assert exc_info.value.error_code == ErrorCode.VAL_RANGE_ERROR
        assert "文件过大" in exc_info.value.message


class TestDXFParserParse:
    """测试DXF解析功能"""
    
    def test_parse_valid_dxf(self, create_test_dxf):
        """测试解析有效DXF文件"""
        parser = DXFParser(create_test_dxf)
        drawing = parser.parse()
        
        assert drawing is not None
        assert drawing.info.file_name == "test.dxf"
        assert drawing.info.file_type == "dxf"
        assert drawing.info.file_size > 0
        assert isinstance(drawing.info.created_at, datetime)
        assert isinstance(drawing.info.modified_at, datetime)
    
    def test_parse_extracts_entities(self, create_test_dxf):
        """测试解析提取实体"""
        parser = DXFParser(create_test_dxf)
        drawing = parser.parse()
        
        # 验证实体统计
        assert drawing.entities.line_count >= 1
        assert drawing.entities.circle_count >= 1
        assert drawing.entities.lwpolyline_count >= 1
        assert drawing.entities.text_count >= 1
        assert drawing.entities.arc_count >= 1
        assert drawing.entities.mtext_count >= 1
        assert drawing.entities.get_total_entity_count() > 0
    
    def test_parse_extracts_layers(self, create_test_dxf):
        """测试解析提取图层"""
        parser = DXFParser(create_test_dxf)
        drawing = parser.parse()
        
        assert drawing.entities.layer_count >= 1
        assert len(drawing.entities.layers) == drawing.entities.layer_count
    
    def test_parse_calculates_extents(self, create_test_dxf):
        """测试解析计算图纸范围"""
        parser = DXFParser(create_test_dxf)
        drawing = parser.parse()
        
        assert drawing.extents.width > 0
        assert drawing.extents.height > 0
        assert drawing.extents.is_valid()
    
    def test_parse_extracts_metadata(self, create_test_dxf):
        """测试解析提取元数据"""
        parser = DXFParser(create_test_dxf)
        drawing = parser.parse()
        
        # 验证元数据提取功能正常工作（由于测试文件没有标题栏图层，可能提取不到具体值）
        assert drawing.metadata is not None
    
    def test_parse_empty_dxf(self, empty_dxf):
        """测试解析空DXF文件"""
        parser = DXFParser(empty_dxf)
        drawing = parser.parse()
        
        assert drawing.entities.get_total_entity_count() == 0
        assert drawing.entities.layer_count >= 1  # 至少有0图层
    
    def test_parse_corrupted_file(self, corrupted_dxf):
        """测试解析损坏的文件"""
        parser = DXFParser(corrupted_dxf)
        
        with pytest.raises(DXFParseException) as exc_info:
            parser.parse()
        
        assert exc_info.value.error_code == ErrorCode.DXF_PARSE_ERROR
    
    def test_parse_with_options(self, create_test_dxf):
        """测试使用自定义选项解析"""
        parser = DXFParser(create_test_dxf)
        
        options = ParseOptions(
            extract_metadata=False,
            extract_entities=True,
            calculate_extents=True
        )
        drawing = parser.parse(options)
        
        # 元数据应该为空（未提取）
        assert drawing.metadata.title is None
        # 但实体应该被提取
        assert drawing.entities.get_total_entity_count() > 0


class TestDXFParserMethods:
    """测试DXFParser其他方法"""
    
    def test_get_dxf_version(self, create_test_dxf):
        """测试获取DXF版本"""
        parser = DXFParser(create_test_dxf)
        parser.parse()
        
        version = parser.get_dxf_version()
        assert version is not None
        assert version.startswith("AC")  # AutoCAD版本格式
    
    def test_get_layer_count(self, create_test_dxf):
        """测试获取图层数量"""
        parser = DXFParser(create_test_dxf)
        parser.parse()
        
        count = parser.get_layer_count()
        assert count >= 1
    
    def test_get_layer_names(self, create_test_dxf):
        """测试获取图层名称列表"""
        parser = DXFParser(create_test_dxf)
        parser.parse()
        
        names = parser.get_layer_names()
        assert isinstance(names, list)
        assert "0" in names  # 默认图层


class TestParseDxfFunction:
    """测试parse_dxf便捷函数"""
    
    def test_parse_dxf_function(self, create_test_dxf):
        """测试便捷函数"""
        drawing = parse_dxf(create_test_dxf)
        
        assert drawing is not None
        assert drawing.info.file_name == "test.dxf"
    
    def test_parse_dxf_with_kwargs(self, create_test_dxf):
        """测试便捷函数传递参数"""
        drawing = parse_dxf(create_test_dxf, extract_metadata=False)
        
        assert drawing.metadata.title is None


class TestDXFParserPerformance:
    """测试解析器性能"""
    
    def test_parse_large_file(self, large_dxf):
        """测试解析大文件"""
        import time
        
        parser = DXFParser(large_dxf)
        
        start = time.time()
        drawing = parser.parse()
        elapsed = time.time() - start
        
        # 应该在合理时间内完成（5秒内）
        assert elapsed < 5.0
        assert drawing.entities.line_count == 1000
