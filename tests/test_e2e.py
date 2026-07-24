"""
端到端测试：DXF 解析 -> PNG 渲染 全链路

验证从 DXF 文件加载到 PNG 图片生成的完整流程，
包括结构修复、容错加载、分位数边界裁剪、PNG 渲染。

注意：DWG -> DXF 转换需要 LibreDWG 安装，单独测试。
"""

import pytest
from pathlib import Path
from PIL import Image

from app.parsers.dxf_parser import DXFParser
from app.renderers.dxf_renderer import DXFRenderer


class TestEndToEndPipeline:
    """DXF -> 解析 -> 渲染 端到端测试"""

    def test_parse_and_render_simple(self, create_test_dxf, tmp_path):
        """简单 DXF 的解析和渲染全链路"""
        # Step 1: 解析
        parser = DXFParser(create_test_dxf)
        drawing = parser.parse()

        assert drawing is not None
        assert drawing.entities.get_total_entity_count() > 0

        # Step 2: 渲染
        output_path = tmp_path / "e2e_simple.png"
        renderer = DXFRenderer()
        png_path = renderer.render(create_test_dxf, output_path, dpi=100)

        assert png_path.exists()
        with Image.open(png_path) as img:
            assert img.format == "PNG"
            assert img.width > 0
            assert img.height > 0

    def test_parse_and_render_real_dxf(self, libredwg_dxf, tmp_path):
        """真实复杂 DXF 的解析和渲染全链路

        覆盖：结构修复 -> 容错加载 -> 模型空间恢复 -> 分位数边界 -> PNG 渲染
        """
        # Step 1: 解析
        parser = DXFParser(libredwg_dxf)
        drawing = parser.parse()

        assert drawing is not None
        assert drawing.entities.get_total_entity_count() > 1000, \
            f"实体数 {drawing.entities.get_total_entity_count()} 过少"
        assert drawing.entities.layer_count > 0
        assert drawing.extents.width > 0
        assert drawing.extents.height > 0

        # 验证解析的实体类型
        assert drawing.entities.line_count > 0, "没有 LINE 实体"

        # Step 2: 渲染
        output_path = tmp_path / "e2e_real.png"
        renderer = DXFRenderer()
        png_path = renderer.render(libredwg_dxf, output_path, dpi=150)

        assert png_path.exists()
        assert png_path.stat().st_size > 10000  # 至少 10KB

        with Image.open(png_path) as img:
            assert img.format == "PNG"
            assert img.width > 500
            assert img.height > 500
            ratio = img.width / img.height
            assert 0.1 < ratio < 10, \
                f"PNG 宽高比 {ratio:.2f} 异常"

    def test_entity_distribution_reasonable(self, libredwg_dxf):
        """真实 DXF 的实体分布应合理（LINE 最多，有尺寸标注）"""
        parser = DXFParser(libredwg_dxf)
        drawing = parser.parse()

        entities = drawing.entities

        # LINE 应是主要实体
        assert entities.line_count > 100, f"LINE 数 {entities.line_count} 过少"

        # 应有尺寸标注
        assert entities.dimension_count > 0, "没有 DIMENSION 实体"

        # 应有文字
        assert (entities.text_count + entities.mtext_count) > 0, "没有文字实体"

    def test_extents_consistent_between_parse_and_render(self, libredwg_dxf, tmp_path):
        """解析的图纸范围与渲染的边界应一致"""
        # 解析
        parser = DXFParser(libredwg_dxf)
        parser._load_file()
        extents = parser._calculate_extents_manual()

        # 渲染
        renderer = DXFRenderer()
        bounds = renderer._compute_render_bounds(parser.msp)

        assert bounds is not None
        assert extents is not None

        # 两者应大致一致（都使用分位数过滤，允许一定误差）
        x_min, x_max, y_min, y_max = bounds
        width_diff = abs((x_max - x_min) - extents.width) / max(extents.width, 1)
        height_diff = abs((y_max - y_min) - extents.height) / max(extents.height, 1)

        assert width_diff < 0.5, f"宽度差异 {width_diff:.1%} 过大"
        assert height_diff < 0.5, f"高度差异 {height_diff:.1%} 过大"
