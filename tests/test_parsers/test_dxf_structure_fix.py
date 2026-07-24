"""
DXF 结构修复测试

测试 _fix_dxf_structure() 和相关容错逻辑，覆盖 LibreDWG 导出 DXF 的
典型结构问题：MTEXT 续行、缺失 ENTITIES 段、句柄错乱等。

测试数据来源：temp/箱体底架.dxf（LibreDWG 0.13.3 从真实 DWG 转换）
"""

import pytest
from pathlib import Path

from app.parsers.dxf_parser import DXFParser, _fix_dxf_structure


class TestFixDXFStructure:
    """测试 _fix_dxf_structure() 文本层修复"""

    def test_fix_returns_valid_path(self, libredwg_dxf):
        """修复后应返回一个存在的文件路径"""
        fixed = _fix_dxf_structure(libredwg_dxf)
        assert fixed is not None
        assert Path(fixed).exists()
        assert Path(fixed).stat().st_size > 0

    def test_fix_produces_entities_section(self, libredwg_dxf):
        """修复后的文件应包含 ENTITIES 段"""
        fixed = _fix_dxf_structure(libredwg_dxf)
        assert fixed is not None

        with open(fixed, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # 查找 ENTITIES 段
        has_entities = False
        for i in range(len(lines) - 3):
            if (
                lines[i].strip() == "0"
                and lines[i + 1].strip() == "SECTION"
                and lines[i + 2].strip() == "2"
                and lines[i + 3].strip() == "ENTITIES"
            ):
                has_entities = True
                break

        assert has_entities, "修复后的文件中没有 ENTITIES 段"

    def test_fix_entities_section_has_entities(self, libredwg_dxf):
        """修复后 ENTITIES 段应包含绘图实体"""
        fixed = _fix_dxf_structure(libredwg_dxf)
        assert fixed is not None

        with open(fixed, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # 定位 ENTITIES 段
        entities_start = -1
        for i in range(len(lines) - 3):
            if (
                lines[i].strip() == "0"
                and lines[i + 1].strip() == "SECTION"
                and lines[i + 2].strip() == "2"
                and lines[i + 3].strip() == "ENTITIES"
            ):
                entities_start = i + 4
                break

        assert entities_start >= 0, "未找到 ENTITIES 段"

        # 统计绘图实体数
        drawing_types = {
            "LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE",
            "TEXT", "MTEXT", "DIMENSION", "INSERT", "HATCH",
            "SPLINE", "ELLIPSE", "POINT", "LEADER", "MLEADER",
        }
        entity_count = 0
        for i in range(entities_start, len(lines) - 1):
            if lines[i].strip() == "0" and lines[i + 1].strip() in drawing_types:
                entity_count += 1

        assert entity_count > 100, f"ENTITIES 段实体数过少: {entity_count}"


class TestDXFParserLoading:
    """测试 DXFParser 的多级容错加载链"""

    def test_load_libredwg_dxf(self, libredwg_dxf):
        """DXFParser 应能成功加载 LibreDWG 导出的复杂 DXF"""
        parser = DXFParser(libredwg_dxf)
        parser._load_file()

        assert parser.doc is not None
        assert parser.msp is not None

    def test_modelspace_has_entities(self, libredwg_dxf):
        """加载后模型空间应包含实体（通过兜底逻辑恢复）"""
        parser = DXFParser(libredwg_dxf)
        parser._load_file()

        entity_count = sum(1 for _ in parser.msp)
        assert entity_count > 0, "模型空间为空，兜底逻辑未生效"

    def test_modelspace_entity_count_reasonable(self, libredwg_dxf):
        """实体数量应在合理范围内（箱体底架图纸约 10000 个实体）"""
        parser = DXFParser(libredwg_dxf)
        parser._load_file()

        entity_count = sum(1 for _ in parser.msp)
        # 宽松范围，避免因版本差异导致误判
        assert 5000 < entity_count < 20000, f"实体数 {entity_count} 超出预期范围"

    def test_parse_returns_valid_drawing(self, libredwg_dxf):
        """完整 parse() 应返回有效的 Drawing 对象"""
        parser = DXFParser(libredwg_dxf)
        drawing = parser.parse()

        assert drawing is not None
        assert drawing.info.file_name == "箱体底架.dxf"
        assert drawing.entities.get_total_entity_count() > 0
        assert drawing.entities.layer_count > 0
        assert drawing.extents.width > 0
        assert drawing.extents.height > 0


class TestExtentsCalculation:
    """测试图纸范围计算的分位数过滤"""

    def test_extents_filter_outliers(self, libredwg_dxf):
        """_calculate_extents_manual() 应过滤异常坐标点"""
        parser = DXFParser(libredwg_dxf)
        parser._load_file()

        extents = parser._calculate_extents_manual()

        # 异常坐标在 100000+，过滤后宽高应在合理范围
        # 箱体底架实际尺寸约 40000~50000mm
        assert extents.width < 100000, f"宽度 {extents.width} 过大，异常值未过滤"
        assert extents.height < 100000, f"高度 {extents.height} 过大，异常值未过滤"
        assert extents.width > 1000, f"宽度 {extents.width} 过小"
        assert extents.height > 1000, f"高度 {extents.height} 过小"

    def test_extents_reasonable_aspect_ratio(self, libredwg_dxf):
        """过滤后的宽高比应合理（不应是极端细长条）"""
        parser = DXFParser(libredwg_dxf)
        parser._load_file()

        extents = parser._calculate_extents_manual()
        ratio = extents.width / extents.height

        # 宽高比在 0.1~10 之间算合理
        assert 0.1 < ratio < 10, f"宽高比 {ratio:.2f} 异常"


class TestPopulateModelspaceFromBlocks:
    """测试 _populate_modelspace_from_blocks() 兜底逻辑"""

    def test_populate_fills_empty_modelspace(self, libredwg_dxf):
        """当模型空间为空时，应从 BLOCKS 段恢复实体"""
        parser = DXFParser(libredwg_dxf)
        parser._load_file()

        # _populate_modelspace_from_blocks 已在 _load_file 中被调用
        # 验证模型空间有实体
        entity_count = sum(1 for _ in parser.msp)
        assert entity_count > 0, "模型空间为空，_populate_modelspace_from_blocks 未生效"

    def test_populate_brings_drawing_entities(self, libredwg_dxf):
        """恢复的实体应包含绘图实体（LINE, ARC 等），而非只有维度块"""
        parser = DXFParser(libredwg_dxf)
        parser._load_file()

        from collections import Counter
        type_counts = Counter()
        for ent in parser.msp:
            type_counts[ent.dxftype()] += 1

        # 应有 LINE 实体
        assert type_counts.get("LINE", 0) > 0, "模型空间没有 LINE 实体"
        # 应有 ARC 或 CIRCLE 实体
        assert (type_counts.get("ARC", 0) + type_counts.get("CIRCLE", 0)) > 0, \
            "模型空间没有 ARC/CIRCLE 实体"
