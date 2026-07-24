"""
DXF -> PNG 渲染模块
将 DXF 文件渲染为高清 PNG 图片，供 AI 视觉分析使用

渲染策略：
1. 优先使用 ezdxf + matplotlib 渲染（高质量、可控）
2. 回退使用 Pillow 简单渲染

容错：
- 通过 DXFParser 的容错链读取文件（含 _fix_dxf_structure）
- 渲染失败时返回错误信息而非崩溃
"""

import io
import logging
import math
import tempfile
from pathlib import Path
from typing import Optional, Union

import ezdxf
import ezdxf.recover
from ezdxf.addons.drawing import RenderContext, Frontend, config as drawing_config
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.layouts import Modelspace

from app.core.config import settings
from app.core.exceptions import DesignReviewException, ErrorCode
from app.parsers.dxf_parser import _fix_dxf_structure as _fix_dxf, DXFParser

logger = logging.getLogger(__name__)


class RenderingError(DesignReviewException):
    """渲染异常"""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(
            message,
            error_code=ErrorCode.DXF_PARSE_ERROR,
            detail=detail
        )


class DXFRenderer:
    """
    DXF -> PNG 渲染器

    Usage:
        renderer = DXFRenderer()
        png_path = renderer.render("/path/to/file.dxf", output_dir="/tmp")
    """

    def __init__(self):
        self.dpi = 200
        self.bg_color = "#FFFFFF"
        self.fg_color = "#000000"

    def render(
        self,
        dxf_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        dpi: int = 200,
    ) -> Path:
        """
        将 DXF 文件渲染为 PNG

        Args:
            dxf_path: DXF 文件路径
            output_path: 输出 PNG 路径（默认同名 .png）
            dpi: 渲染分辨率

        Returns:
            PNG 文件路径

        Raises:
            RenderingError: 渲染失败
        """
        dxf_path = Path(dxf_path).resolve()
        if not dxf_path.exists():
            raise RenderingError(
                f"DXF 文件不存在: {dxf_path}",
                detail=str(dxf_path)
            )

        if output_path is None:
            output_path = dxf_path.parent / (dxf_path.stem + ".png")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self.dpi = dpi

        logger.info(f"开始渲染 DXF -> PNG: {dxf_path.name} -> {output_path.name}")

        # 读取 DXF 文件（使用容错链）
        doc, msp = self._read_dxf_safe(dxf_path)
        if doc is None:
            raise RenderingError(
                "无法读取 DXF 文件进行渲染",
                detail=f"文件: {dxf_path}"
            )

        # 尝试方案1: ezdxf matplotlib backend
        try:
            self._render_with_matplotlib(doc, msp, output_path)
            logger.info(f"matplotlib 渲染成功: {output_path}")
            return output_path
        except Exception as e:
            logger.warning(f"matplotlib 渲染失败: {e}")

        # 尝试方案2: matplotlib 直接绘制
        try:
            self._render_with_matplotlib_direct(msp, output_path)
            logger.info(f"matplotlib 直接渲染成功: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"所有渲染方案均失败: {e}")
            raise RenderingError(
                "DXF 渲染为 PNG 失败",
                detail=str(e)
            )

    def _read_dxf_safe(
        self,
        dxf_path: Path
    ) -> tuple:
        """
        安全读取 DXF 文件（复用 DXFParser 的完整容错链）

        返回: (doc, msp) 或 (None, None)
        """
        try:
            parser = DXFParser(dxf_path)
            parser._load_file()
            if parser.doc and parser.msp:
                entity_count = sum(1 for _ in parser.msp)
                logger.info(f"渲染器加载成功，模型空间实体数: {entity_count}")
                return parser.doc, parser.msp
        except Exception as e:
            logger.warning(f"DXFParser 加载失败: {e}")

        return None, None

    def _setup_cjk_font(self):
        """
        配置支持中文的 matplotlib 字体。
        依次尝试常见的中文字体，找到第一个可用的。
        """
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import font_manager

        # macOS / Linux / Windows 常见中文字体
        candidates = [
            "PingFang SC",
            "Hiragino Sans GB",
            "STHeiti",
            "Microsoft YaHei",
            "SimHei",
            "WenQuanYi Micro Hei",
            "Arial Unicode MS",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
        ]

        available = set(f.name for f in font_manager.fontManager.ttflist)
        for font_name in candidates:
            if font_name in available:
                try:
                    matplotlib.rcParams['font.family'] = ['sans-serif']
                    matplotlib.rcParams['font.sans-serif'] = [font_name]
                    matplotlib.rcParams['axes.unicode_minus'] = False
                    logger.info(f"使用字体: {font_name}")
                    return font_name
                except Exception:
                    continue
        logger.warning("未找到合适的中文字体，文字可能显示为方框")
        return None

    def _render_with_matplotlib(self, doc, msp, output_path: Path) -> None:
        """
        使用 ezdxf 的 matplotlib backend 渲染

        这是 ezdxf 官方推荐的渲染方式，质量最高
        """
        import matplotlib
        matplotlib.use("Agg")  # 无头模式
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import RenderContext, Frontend

        # 配置中文字体
        self._setup_cjk_font()

        fig = plt.figure(figsize=(16, 12), dpi=self.dpi)
        ax = fig.add_subplot(111)
        ax.set_aspect("equal")
        ax.set_facecolor(self.bg_color)

        ctx = RenderContext(doc)
        out = MatplotlibBackend(ax)
        # ezdxf 1.4 需要使用 Frontend 来驱动渲染
        cfg = drawing_config.Configuration()
        frontend = Frontend(ctx, out, config=cfg)
        frontend.draw_layout(msp)

        # 移除坐标轴
        ax.set_axis_off()
        plt.tight_layout(pad=0)
        fig.savefig(
            str(output_path),
            dpi=self.dpi,
            bbox_inches="tight",
            pad_inches=0,
            facecolor=self.bg_color,
        )
        plt.close(fig)

    def _compute_render_bounds(self, msp, lower_percentile=5.0, upper_percentile=95.0, margin=0.05):
        """
        计算渲染视图边界，使用分位数过滤异常坐标点。

        LibreDWG 导出的 DXF 常常包含远离主体的稀疏坐标点（如异常 INSERT、
        图框符号等），会导致整图被压得很小。这里默认用 5%-95% 分位数聚焦
        主体绘图区域，再加 5% 边距。只使用实体的关键点，避免 CIRCLE/ARC
        的 c±r 近似放大范围。
        """
        import math

        xs = []
        ys = []

        for entity in msp:
            try:
                etype = entity.dxftype()
                if etype == "LINE":
                    xs.extend([entity.dxf.start.x, entity.dxf.end.x])
                    ys.extend([entity.dxf.start.y, entity.dxf.end.y])
                elif etype == "CIRCLE":
                    c = entity.dxf.center
                    xs.append(c.x)
                    ys.append(c.y)
                elif etype == "ARC":
                    c = entity.dxf.center
                    r = entity.dxf.radius
                    sa = math.radians(entity.dxf.start_angle)
                    ea = math.radians(entity.dxf.end_angle)
                    xs.extend([c.x, c.x + r * math.cos(sa), c.x + r * math.cos(ea)])
                    ys.extend([c.y, c.y + r * math.sin(sa), c.y + r * math.sin(ea)])
                elif etype == "LWPOLYLINE":
                    for p in entity.get_points():
                        xs.append(p[0])
                        ys.append(p[1])
                elif etype == "POLYLINE":
                    for v in entity.vertices:
                        xs.append(v.dxf.location.x)
                        ys.append(v.dxf.location.y)
                elif etype == "ELLIPSE":
                    c = entity.dxf.center
                    xs.append(c.x)
                    ys.append(c.y)
                elif etype in ("TEXT", "MTEXT", "INSERT", "POINT"):
                    xs.append(entity.dxf.insert.x)
                    ys.append(entity.dxf.insert.y)
            except Exception:
                continue

        if len(xs) < 2 or len(ys) < 2:
            return None

        def percentile_bounds(values, lo_pct, hi_pct):
            values = sorted(values)
            n = len(values)
            lo = int(n * lo_pct / 100)
            hi = int(n * hi_pct / 100)
            lo = max(0, min(lo, n - 1))
            hi = max(0, min(hi, n - 1))
            if hi <= lo:
                hi = n - 1
            return values[lo], values[hi]

        x_min, x_max = percentile_bounds(xs, lower_percentile, upper_percentile)
        y_min, y_max = percentile_bounds(ys, lower_percentile, upper_percentile)

        x_margin = (x_max - x_min) * margin
        y_margin = (y_max - y_min) * margin
        return (
            x_min - x_margin, x_max + x_margin,
            y_min - y_margin, y_max + y_margin,
        )

    def _render_with_matplotlib_direct(self, msp, output_path: Path) -> None:
        """
        直接用 matplotlib 绘制实体（回退方案）

        手动遍历实体并绘制，不依赖 ezdxf 的 drawing addon
        """
        import math
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
        from matplotlib.patches import Circle, Arc as MplArc, Polygon

        # 配置中文字体
        self._setup_cjk_font()

        fig, ax = plt.subplots(figsize=(16, 12), dpi=self.dpi)
        ax.set_aspect("equal")
        ax.set_facecolor(self.bg_color)
        ax.set_axis_off()

        entity_count = 0

        for entity in msp:
            try:
                etype = entity.dxftype()

                if etype == "LINE":
                    start = entity.dxf.start
                    end = entity.dxf.end
                    ax.plot(
                        [start.x, end.x],
                        [start.y, end.y],
                        color=self.fg_color,
                        linewidth=0.5,
                    )
                    entity_count += 1

                elif etype == "CIRCLE":
                    center = entity.dxf.center
                    radius = entity.dxf.radius
                    circle = Circle(
                        (center.x, center.y),
                        radius,
                        fill=False,
                        edgecolor=self.fg_color,
                        linewidth=0.5,
                    )
                    ax.add_patch(circle)
                    entity_count += 1

                elif etype == "ARC":
                    center = entity.dxf.center
                    radius = entity.dxf.radius
                    start_angle = entity.dxf.start_angle
                    end_angle = entity.dxf.end_angle
                    arc = MplArc(
                        (center.x, center.y),
                        2 * radius,
                        2 * radius,
                        angle=0,
                        theta1=start_angle,
                        theta2=end_angle,
                        edgecolor=self.fg_color,
                        linewidth=0.5,
                    )
                    ax.add_patch(arc)
                    entity_count += 1

                elif etype == "LWPOLYLINE":
                    points = [(p[0], p[1]) for p in entity.get_points()]
                    if len(points) >= 2:
                        xs = [p[0] for p in points]
                        ys = [p[1] for p in points]
                        if entity.closed:
                            xs.append(xs[0])
                            ys.append(ys[0])
                        ax.plot(xs, ys, color=self.fg_color, linewidth=0.5)
                    entity_count += 1

                elif etype == "POLYLINE":
                    points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
                    if len(points) >= 2:
                        xs = [p[0] for p in points]
                        ys = [p[1] for p in points]
                        if entity.is_closed:
                            xs.append(xs[0])
                            ys.append(ys[0])
                        ax.plot(xs, ys, color=self.fg_color, linewidth=0.5)
                    entity_count += 1

                elif etype in ("TEXT", "MTEXT"):
                    # 文字简化处理：绘制位置标记
                    insert = entity.dxf.insert
                    text = entity.text if etype == "MTEXT" else entity.dxf.text
                    if text:
                        short_text = text[:20] + "..." if len(text) > 20 else text
                        ax.text(
                            insert.x, insert.y,
                            short_text,
                            fontsize=4,
                            color=self.fg_color,
                        )
                    entity_count += 1

                elif etype == "ELLIPSE":
                    center = entity.dxf.center
                    major = entity.dxf.major_axis
                    ratio = entity.dxf.ratio
                    width = 2 * math.sqrt(major.x ** 2 + major.y ** 2)
                    height = width * ratio
                    angle = math.degrees(math.atan2(major.y, major.x))
                    ellipse = matplotlib.patches.Ellipse(
                        (center.x, center.y),
                        width, height,
                        angle=angle,
                        fill=False,
                        edgecolor=self.fg_color,
                        linewidth=0.5,
                    )
                    ax.add_patch(ellipse)
                    entity_count += 1

            except Exception as e:
                logger.debug(f"渲染实体 {etype} 失败: {e}")

        logger.debug(f"直接渲染了 {entity_count} 个实体")

        # 使用分位数边界裁剪异常点，使主体居中
        bounds = self._compute_render_bounds(msp)
        if bounds:
            x_min, x_max, y_min, y_max = bounds
            ax.set_xlim(x_min, x_max)
            ax.set_ylim(y_min, y_max)
        else:
            ax.autoscale()

        plt.tight_layout(pad=0)
        fig.savefig(
            str(output_path),
            dpi=self.dpi,
            bbox_inches="tight",
            pad_inches=0.1,
            facecolor=self.bg_color,
        )
        plt.close(fig)


# 全局便捷函数
_renderer_instance: Optional[DXFRenderer] = None


def get_renderer() -> DXFRenderer:
    """获取渲染器单例"""
    global _renderer_instance
    if _renderer_instance is None:
        _renderer_instance = DXFRenderer()
    return _renderer_instance


def render_dxf_to_png(
    dxf_path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = 200,
) -> Path:
    """
    便捷函数：DXF -> PNG 渲染

    Args:
        dxf_path: DXF 文件路径
        output_path: 输出路径
        dpi: 分辨率

    Returns:
        PNG 文件路径
    """
    return get_renderer().render(dxf_path, output_path, dpi)
