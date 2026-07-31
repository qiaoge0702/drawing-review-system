"""
Step 7 DXF 构建执行器单元测试

不依赖 SW 环境：构造 Step3-6 mock 产物驱动执行器，
生成后用 ezdxf.readfile 回读校验（"DXF 可打开"的程序化验证）：
- 契约 9 图层全部存在
- entity_counts 与回读一致（line/circle/arc 数量匹配输入 entities 数）
- modelspace 非空、文字实体存在
- 坐标平移公式正确性：落图坐标 = view_position + (local - bbox.min)
- 缺 views → SWException；缺 dimensions/bom/tech → 降级正常完成
- 产物落盘 drawing.dxf 存在且非空
"""

from pathlib import Path

import ezdxf
import pytest

from app.generators.models import StepContext
from app.generators.steps.step7_dxf_build import (
    DxfBuildExecutor,
    _CONTRACT_LAYERS,
    _translate,
)
from app.models.generation import StepName
from app.core.exceptions import SWException, ErrorCode


# ----------------------------------------------------------------------
# Mock 产物
# ----------------------------------------------------------------------

def _views_result(scale: str = "1:1"):
    """Step3 新契约产物：实体已归一化（bbox 原点 = 视图左下角，实际尺寸 mm）"""
    return {
        "views": [
            {
                "name": "front",
                "display_name": "主视图",
                "projection": "first_angle",
                "entities": [
                    {"type": "line", "x1": 0.0, "y1": 0.0, "x2": 100.0, "y2": 0.0},
                    {"type": "line", "x1": 100.0, "y1": 0.0, "x2": 100.0, "y2": 60.0},
                    {"type": "circle", "cx": 50.0, "cy": 30.0, "r": 10.0},
                    {"type": "arc", "cx": 60.0, "cy": 40.0, "r": 5.0,
                     "start_angle": 0.0, "end_angle": 90.0},
                ],
                "hidden_lines": [
                    {"x1": 10.0, "y1": 30.0, "x2": 90.0, "y2": 30.0},
                ],
                "center_lines": [
                    {"x1": 50.0, "y1": -10.0, "x2": 50.0, "y2": 70.0},
                ],
                "section_hatch": None,
                "bounding_box": {"min_x": 0.0, "min_y": 0.0,
                                 "max_x": 100.0, "max_y": 60.0},
                "scale": scale,
            }
        ],
        "layout": {
            "sheet_size": "A3",
            "orientation": "landscape",
            "view_positions": {
                "front": {"x": 100.0, "y": 150.0, "width": 120.0, "height": 80.0},
            },
        },
        "warnings": [],
    }


def _dimensions_result():
    return {
        "dimensions": [
            {
                "id": "dim_001",
                "type": "linear",
                "value": 100.0,
                "unit": "mm",
                "tolerance": {"upper": 0.5, "lower": -0.5, "grade": "IT14"},
                "position": {"x1": 0.0, "y1": -5.0, "x2": 100.0, "y2": -5.0,
                             "text_x": 50.0, "text_y": -8.0},
                "associated_entities": ["front_e0", "front_e1"],
                "is_automatic": True,
                "confidence": 1.0,
            }
        ],
        "placement_score": 1.0,
        "overlaps": [],
    }


def _bom_result():
    return {
        "bom_table": {
            "columns": ["序号", "图号", "名称", "数量"],
            "rows": [
                [1, "LB26.11001", "连接板", 2],
                [2, "LB26.11002", "支架", 1],
            ],
            "position": {"x": 230.0, "y": 200.0, "width": 160.0, "height": 50.0},
            "style": {"header_height": 8.0, "row_height": 6.0,
                      "font_size": 3.5, "border_width": 0.25},
        },
        "source_total_items": 3,
    }


def _tech_result():
    return {
        "tech_requirements": {
            "template_id": "weldment_general",
            "template_name": "焊接件通用模板",
            "variables": {},
            "content": ["1.焊接应符合GB/T 985.1规定", "2.焊缝质量等级：二级"],
            "position": {"x": 20.0, "y": 200.0, "width": 150.0, "height": 60.0},
        },
        "available_templates": ["weldment_general"],
    }


def _geometry_result():
    return {
        "bom": [{"level": 0, "name": "LB26.11000底架焊合",
                 "path": "C:/asm/LB26.11000.SLDASM", "quantity": 1,
                 "is_suppressed": False}],
        "materials": {"LB26.11001": "Q235"},
        "total_mass": 0.0,
    }


def _make_ctx(tmp_path: Path, previous: dict = None) -> StepContext:
    return StepContext(
        task_id="test-step7",
        step=7,
        step_name=StepName.DXF_BUILD,
        work_dir=tmp_path,
        parameters={},
        previous_results=previous or {},
    )


def _full_ctx(tmp_path: Path) -> StepContext:
    return _make_ctx(tmp_path, {
        2: _geometry_result(),
        3: _views_result(),
        4: _dimensions_result(),
        5: _bom_result(),
        6: _tech_result(),
    })


# ----------------------------------------------------------------------
# 测试
# ----------------------------------------------------------------------

class TestFullBuild:
    @pytest.mark.asyncio
    async def test_full_build_contract_and_readback(self, tmp_path):
        """全量输入：契约结构 + drawing.dxf 落盘 + 回读校验"""
        ctx = _full_ctx(tmp_path)
        result = await DxfBuildExecutor()(ctx)

        structure = result["dxf_structure"]
        assert structure["header"]["dxfversion"] == "AC1024"  # R2010
        assert structure["blocks"] == []

        # drawing.dxf 落盘且非空
        dxf_file = tmp_path / "output" / "drawing.dxf"
        assert dxf_file.exists() and dxf_file.stat().st_size > 0

        # 回读（"DXF 可打开"的程序化验证）
        doc = ezdxf.readfile(dxf_file)
        # 契约 9 图层全部存在
        # 注：契约“轮廓线”层 color=0（BYBLOCK），但 DXF 图层颜色不允许 0，
        # ezdxf 自动回退为 7（白）；此处校验实际落库值，其余层按契约颜色
        for name, color, _ in _CONTRACT_LAYERS:
            assert name in doc.layers, f"missing layer {name}"
            expected = 7 if color == 0 else color
            assert doc.layers.get(name).dxf.color == expected

        msp = doc.modelspace()
        read_counts = {}
        for e in msp:
            key = e.dxftype().lower()
            read_counts[key] = read_counts.get(key, 0) + 1

        # modelspace 非空、文字实体存在
        assert sum(read_counts.values()) > 0
        assert read_counts.get("text", 0) > 0

        # entity_counts 与回读一致
        counts = structure["entity_counts"]
        assert read_counts.get("line", 0) == counts.get("line", 0)
        assert read_counts.get("circle", 0) == counts.get("circle", 0)
        assert read_counts.get("arc", 0) == counts.get("arc", 0)
        assert read_counts.get("text", 0) == counts.get("text", 0)

        # 视图实体数匹配输入：1 个视图 = 2 line + 1 circle + 1 arc
        assert counts["circle"] == 1
        assert counts["arc"] == 1
        # line = 视图 2 + 隐藏线 1 + 中心线 1 + 图框 4 + 标题栏(4+3) + 标注 3 + BOM 表格
        assert counts["line"] >= 2 + 1 + 1 + 4 + 7 + 3

        # 标注：1 个 dim = 标注线 1 + 延伸线 2 + 文字 1
        assert counts["dimension"] == 1

    @pytest.mark.asyncio
    async def test_coordinate_translation(self, tmp_path):
        """落图公式正确性：图纸坐标 = view_position + 局部坐标 × scale_factor"""
        ctx = _full_ctx(tmp_path)
        await DxfBuildExecutor()(ctx)
        doc = ezdxf.readfile(tmp_path / "output" / "drawing.dxf")

        view_pos = {"x": 100.0, "y": 150.0}

        # 视图第一条 line 实体（轮廓线层）：局部 (0,0) → (100,150)
        lines = [e for e in doc.modelspace()
                 if e.dxftype() == "LINE" and e.dxf.layer == "轮廓线"]
        assert len(lines) == 2
        expected_start = _translate((0.0, 0.0), view_pos, 1.0)  # (100, 150)
        found = any(
            abs(e.dxf.start.x - expected_start[0]) < 1e-6
            and abs(e.dxf.start.y - expected_start[1]) < 1e-6
            for e in lines
        )
        assert found, f"expected line start at {expected_start}"

        # circle 圆心平移：(50,30) → (150,180)，半径 scale=1:1 不变
        circles = [e for e in doc.modelspace() if e.dxftype() == "CIRCLE"]
        assert len(circles) == 1
        assert abs(circles[0].dxf.center.x - 150.0) < 1e-6
        assert abs(circles[0].dxf.center.y - 180.0) < 1e-6
        assert abs(circles[0].dxf.radius - 10.0) < 1e-6

        # arc 回读角度
        arcs = [e for e in doc.modelspace() if e.dxftype() == "ARC"]
        assert len(arcs) == 1
        assert abs(arcs[0].dxf.start_angle - 0.0) < 1e-6
        assert abs(arcs[0].dxf.end_angle - 90.0) < 1e-6

        # 标注文字按视图落图公式：text (50,-8) → (150, 142)
        dim_texts = [e for e in doc.modelspace()
                     if e.dxftype() == "TEXT" and e.dxf.layer == "标注"]
        assert len(dim_texts) == 1
        insert = dim_texts[0].dxf.insert
        assert abs(insert.x - 150.0) < 1e-6
        assert abs(insert.y - 142.0) < 1e-6
        assert "100" in dim_texts[0].dxf.text

    @pytest.mark.asyncio
    async def test_scale_factor_applied(self, tmp_path):
        """scale="1:2" 视图：落图坐标与半径均减半"""
        ctx = _make_ctx(tmp_path, {3: _views_result(scale="1:2")})
        await DxfBuildExecutor()(ctx)
        doc = ezdxf.readfile(tmp_path / "output" / "drawing.dxf")
        msp = doc.modelspace()

        # line 端点：局部 (100,60) × 0.5 + (100,150) = (150,180)
        lines = [e for e in msp if e.dxftype() == "LINE" and e.dxf.layer == "轮廓线"]
        found = any(
            abs(e.dxf.end.x - 150.0) < 1e-6 and abs(e.dxf.end.y - 180.0) < 1e-6
            for e in lines
        )
        assert found, "scale 1:2 下落图坐标未减半"

        # circle：圆心 (50,30)×0.5+(100,150) = (125,165)，半径 10×0.5 = 5
        circles = [e for e in msp if e.dxftype() == "CIRCLE"]
        assert len(circles) == 1
        assert abs(circles[0].dxf.center.x - 125.0) < 1e-6
        assert abs(circles[0].dxf.center.y - 165.0) < 1e-6
        assert abs(circles[0].dxf.radius - 5.0) < 1e-6

    @pytest.mark.asyncio
    async def test_insunits_mm(self, tmp_path):
        """DXF header 必须写 $INSUNITS=4（毫米）"""
        ctx = _full_ctx(tmp_path)
        await DxfBuildExecutor()(ctx)
        doc = ezdxf.readfile(tmp_path / "output" / "drawing.dxf")
        assert doc.header["$INSUNITS"] == 4

    @pytest.mark.asyncio
    async def test_hidden_and_center_layers(self, tmp_path):
        """隐藏线/中心线分别落对应图层"""
        ctx = _full_ctx(tmp_path)
        await DxfBuildExecutor()(ctx)
        doc = ezdxf.readfile(tmp_path / "output" / "drawing.dxf")
        msp = doc.modelspace()
        hidden = [e for e in msp if e.dxf.layer == "隐藏线"]
        center = [e for e in msp if e.dxf.layer == "中心线"]
        assert len(hidden) == 1
        assert len(center) == 1
        assert doc.layers.get("隐藏线").dxf.linetype == "HIDDEN"
        assert doc.layers.get("中心线").dxf.linetype == "CENTER"


class TestErrorAndDegrade:
    @pytest.mark.asyncio
    async def test_missing_views_raises(self, tmp_path):
        """缺 views → SWException(GEN_STEP_FAILED)"""
        ctx = _make_ctx(tmp_path, {4: _dimensions_result()})
        with pytest.raises(SWException) as exc_info:
            await DxfBuildExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    @pytest.mark.asyncio
    async def test_missing_optional_upstreams_degrades(self, tmp_path):
        """缺 dimensions/bom/tech → 降级正常完成，视图仍落图"""
        ctx = _make_ctx(tmp_path, {3: _views_result()})
        result = await DxfBuildExecutor()(ctx)
        dxf_file = tmp_path / "output" / "drawing.dxf"
        assert dxf_file.exists() and dxf_file.stat().st_size > 0

        counts = result["dxf_structure"]["entity_counts"]
        assert "dimension" not in counts
        # 视图实体照常：2 line（轮廓） + 1 hidden + 1 center + 图框/标题栏
        assert counts["circle"] == 1
        assert counts["arc"] == 1

        doc = ezdxf.readfile(dxf_file)
        # 标注层/BOM层/技术要求层无实体
        msp = doc.modelspace()
        assert not [e for e in msp if e.dxf.layer == "标注"]
        assert not [e for e in msp if e.dxf.layer == "BOM"]
        assert not [e for e in msp if e.dxf.layer == "技术要求"]

    @pytest.mark.asyncio
    async def test_views_from_checkpoint(self, tmp_path):
        """内存缺失时回退 output/views.json 检查点"""
        import json
        out_dir = tmp_path / "output"
        out_dir.mkdir(parents=True)
        (out_dir / "views.json").write_text(
            json.dumps(_views_result(), ensure_ascii=False), encoding="utf-8")
        ctx = _make_ctx(tmp_path)
        result = await DxfBuildExecutor()(ctx)
        assert (out_dir / "drawing.dxf").exists()
        assert result["dxf_structure"]["entity_counts"]["circle"] == 1


class TestHelpers:
    def test_translate_formula(self):
        # scale=1:1：纯平移
        p = _translate((0.0, 0.0), {"x": 100.0, "y": 150.0}, 1.0)
        assert p == (100.0, 150.0)
        p2 = _translate((100.0, 60.0), {"x": 100.0, "y": 150.0}, 1.0)
        assert p2 == (200.0, 210.0)
        # scale=0.5（"1:2"）：局部坐标减半
        p3 = _translate((100.0, 60.0), {"x": 100.0, "y": 150.0}, 0.5)
        assert p3 == (150.0, 180.0)

    def test_parse_scale(self):
        from app.generators.steps.step7_dxf_build import _parse_scale
        assert _parse_scale("1:2") == 0.5
        assert _parse_scale("1:2.5") == pytest.approx(0.4)
        assert _parse_scale("1:50") == pytest.approx(0.02)
        assert _parse_scale("2:1") == 2.0
        assert _parse_scale("1:1") == 1.0
        # 缺省/非法 → 1.0
        assert _parse_scale(None) == 1.0
        assert _parse_scale("garbage") == 1.0

    def test_dim_text_tolerance(self):
        from app.generators.steps.step7_dxf_build import DxfBuildExecutor
        assert DxfBuildExecutor._dim_text(
            {"value": 100.0, "tolerance": {"upper": 0.5, "lower": -0.5}}) == "100±0.5"
        assert DxfBuildExecutor._dim_text(
            {"value": 50.0, "tolerance": {"upper": 0.2, "lower": -0.1}}) == "50 +0.2/-0.1"
        assert DxfBuildExecutor._dim_text({"value": 25.0}) == "25"
