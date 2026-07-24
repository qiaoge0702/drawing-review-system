"""
DXF 渲染器测试

测试 DXFRenderer 的核心功能：
- _compute_render_bounds() 分位数边界裁剪
- render() 生成有效 PNG 图片
- PNG 尺寸合理性
"""

import pytest
from pathlib import Path
from PIL import Image

from app.parsers.dxf_parser import DXFParser
from app.renderers.dxf_renderer import DXFRenderer


class TestComputeRenderBounds:
    """测试 _compute_render_bounds() 分位数边界计算"""

    def test_bounds_with_outliers(self, dxf_with_outliers):
        """包含异常坐标时，边界应过滤极端值"""
        parser = DXFParser(dxf_with_outliers)
        parser._load_file()

        renderer = DXFRenderer()
        bounds = renderer._compute_render_bounds(parser.msp)

        assert bounds is not None
        x_min, x_max, y_min, y_max = bounds

        # 主体图形在 0~100 范围，异常值在 500000+
        # 分位数过滤后，范围应远小于异常值范围
        assert x_max < 10000, f"X max={x_max} 过大，异常值未过滤"
        assert x_min > -10000, f"X min={x_min} 过小，异常值未过滤"
        assert y_max < 10000, f"Y max={y_max} 过大，异常值未过滤"
        assert y_min > -10000, f"Y min={y_min} 过小，异常值未过滤"

    def test_bounds_reasonable_aspect_ratio(self, dxf_with_outliers):
        """过滤后宽高比应合理"""
        parser = DXFParser(dxf_with_outliers)
        parser._load_file()

        renderer = DXFRenderer()
        bounds = renderer._compute_render_bounds(parser.msp)

        assert bounds is not None
        x_min, x_max, y_min, y_max = bounds
        ratio = (x_max - x_min) / (y_max - y_min)

        assert 0.1 < ratio < 10, f"宽高比 {ratio:.2f} 异常"

    def test_bounds_with_real_dxf(self, libredwg_dxf):
        """真实 DXF 的渲染边界应合理"""
        parser = DXFParser(libredwg_dxf)
        parser._load_file()

        renderer = DXFRenderer()
        bounds = renderer._compute_render_bounds(parser.msp)

        assert bounds is not None
        x_min, x_max, y_min, y_max = bounds

        # 过滤异常值后，范围应在合理范围内
        assert (x_max - x_min) < 100000, "X 范围过大"
        assert (y_max - y_min) < 100000, "Y 范围过大"


class TestDXFRenderer:
    """测试 DXFRenderer.render() 渲染"""

    def test_render_simple_dxf(self, create_test_dxf, tmp_path):
        """渲染简单 DXF 应生成有效 PNG"""
        output_path = tmp_path / "output.png"

        renderer = DXFRenderer()
        result = renderer.render(create_test_dxf, output_path, dpi=100)

        assert result is not None
        assert result.exists()
        assert result.stat().st_size > 0

        # 验证是有效 PNG 图片
        with Image.open(result) as img:
            assert img.format == "PNG"
            assert img.width > 0
            assert img.height > 0

    def test_render_with_outliers(self, dxf_with_outliers, tmp_path):
        """渲染含异常坐标的 DXF，PNG 不应是极端细长条"""
        output_path = tmp_path / "output.png"

        renderer = DXFRenderer()
        result = renderer.render(dxf_with_outliers, output_path, dpi=100)

        assert result is not None
        assert result.exists()

        with Image.open(result) as img:
            # 不应是极端细长条（宽高比在 0.1~10 之间）
            ratio = img.width / img.height
            assert 0.1 < ratio < 10, \
                f"PNG 尺寸 {img.width}x{img.height}，宽高比 {ratio:.2f} 异常"

    def test_render_real_dxf(self, libredwg_dxf, tmp_path):
        """渲染真实复杂 DXF 应生成有效 PNG"""
        output_path = tmp_path / "real_output.png"

        renderer = DXFRenderer()
        result = renderer.render(libredwg_dxf, output_path, dpi=150)

        assert result is not None
        assert result.exists()
        assert result.stat().st_size > 10000  # 至少 10KB

        with Image.open(result) as img:
            assert img.format == "PNG"
            assert img.width > 500, f"PNG 宽度 {img.width} 过小"
            assert img.height > 500, f"PNG 高度 {img.height} 过小"

            # 宽高比应合理
            ratio = img.width / img.height
            assert 0.1 < ratio < 10, \
                f"PNG 尺寸 {img.width}x{img.height}，宽高比 {ratio:.2f} 异常"
