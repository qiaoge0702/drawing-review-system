"""
M2 收口修复包3 回归测试：视图坐标系统一 + 第一角布局 + BOM/标题栏修复

覆盖：
- Step3 第一角布局（俯视在主视正下方、左视在主视正右方）+ 图幅 A3→A0 选型
- Step5 BOM 高度图幅适配（压缩行高/截断 + warnings）+ 列宽内容适配
- Step7 标题栏比例取 Step3 实际 scale、材料多材料→"见明细表"、
  BOM 列宽 column_widths 消费、标注=局部+视图偏移抽样
"""

import json
from pathlib import Path

import ezdxf
import pytest

from app.generators.models import StepContext
from app.generators.steps.step3_view_project import _build_layout
from app.generators.steps.step5_bom_generate import BomGenerateExecutor
from app.generators.steps.step7_dxf_build import DxfBuildExecutor
from app.models.generation import StepName


def _view(name: str, w: float, h: float) -> dict:
    """合成视图：局部坐标已归一化（原点左下角），矩形轮廓"""
    return {
        "name": name,
        "display_name": name,
        "projection": "first_angle",
        "entities": [
            {"type": "line", "x1": 0.0, "y1": 0.0, "x2": w, "y2": 0.0},
            {"type": "line", "x1": w, "y1": 0.0, "x2": w, "y2": h},
            {"type": "line", "x1": w, "y1": h, "x2": 0.0, "y2": h},
            {"type": "line", "x1": 0.0, "y1": h, "x2": 0.0, "y2": 0.0},
        ],
        "hidden_lines": [],
        "center_lines": [],
        "section_hatch": None,
        "bounding_box": {"min_x": 0.0, "min_y": 0.0, "max_x": w, "max_y": h},
        "scale": "1:1",
    }


def _ctx(tmp_path: Path, step: int, step_name, previous=None, parameters=None):
    return StepContext(
        task_id="test-fixpack3",
        step=step,
        step_name=step_name,
        work_dir=tmp_path,
        parameters=parameters or {},
        previous_results=previous or {},
    )


# ----------------------------------------------------------------------
# Step3：第一角布局 + 图幅选型
# ----------------------------------------------------------------------

class TestFirstAngleLayout:
    def test_real_case_layout_a0(self):
        """真机尺寸（LB26：主1002x327/俯1002x6959.5/左6924x327，BOM 43 行）
        → 图幅 A0、比例按最终图幅重算=1:10（2026-08-01 老板验收修复：
        原“A3 基准比例 1:50 放到 A0”导致视图缩成左上角一小簇）、
        第一角相对位置、全部落在有效区内"""
        views = [_view("front", 1002.0, 327.0),
                 _view("top", 1002.0, 6959.5009),
                 _view("left", 6924.0, 327.0)]
        layout = _build_layout(views, "t", bom_rows=43)

        assert views[0]["scale"] == "1:10"
        assert layout["sheet_size"] == "A0"
        pos = layout["view_positions"]
        f, t, l = pos["front"], pos["top"], pos["left"]
        # 第一角：俯视在主视正下方（x 对齐），左视在主视正右方
        assert t["x"] == pytest.approx(f["x"])
        assert t["y"] + t["height"] < f["y"]
        assert l["x"] >= f["x"] + f["width"]
        assert l["y"] + l["height"] == pytest.approx(f["y"] + f["height"])
        # 全部落在 A0 有效区（边距 20）且不重叠
        rects = []
        for p in pos.values():
            assert p["x"] >= 20.0 and p["y"] >= 20.0
            assert p["x"] + p["width"] <= 1189.0 - 20.0
            assert p["y"] + p["height"] <= 841.0 - 20.0
            rects.append((p["x"], p["y"], p["x"] + p["width"], p["y"] + p["height"]))
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                overlap = (min(a[2], b[2]) - max(a[0], b[0]) > 0
                           and min(a[3], b[3]) - max(a[1], b[1]) > 0)
                assert not overlap, f"视图 {i}/{j} 重叠"

    def test_small_part_stays_a3(self):
        """小件无 BOM → A3，第一角布局"""
        views = [_view("front", 10.0, 30.0),
                 _view("top", 10.0, 20.0),
                 _view("left", 20.0, 30.0)]
        layout = _build_layout(views, "t")
        assert views[0]["scale"] == "1:1"
        assert layout["sheet_size"] == "A3"
        pos = layout["view_positions"]
        assert pos["top"]["y"] + pos["top"]["height"] < pos["front"]["y"]
        assert pos["left"]["x"] > pos["front"]["x"]

    def test_bom_estimate_forces_bigger_sheet(self):
        """视图装得下 A3 但 BOM 43 行装不下 → 升级到能装下 BOM 的图幅"""
        views = [_view("front", 100.0, 50.0)]
        layout = _build_layout(views, "t", bom_rows=43)
        assert layout["sheet_size"] in ("A1", "A0")  # A2 高 410 也装不下 665+50


# ----------------------------------------------------------------------
# Step5：BOM 高度/列宽图幅适配
# ----------------------------------------------------------------------

def _bom_items(n: int) -> list:
    return [{"level": 1, "name": f"件{i}", "path": f"C:/p/P{i:03d}.SLDPRT",
             "quantity": 1, "is_suppressed": False} for i in range(n)]


class TestBomFit:
    @pytest.mark.asyncio
    async def test_overflow_compresses_row_height_with_warning(self, tmp_path):
        """43 行 × 15mm = 665 超出 A3 有效区 → 压缩行高 + warnings 如实声明"""
        ctx = _ctx(tmp_path, 5, StepName.BOM_GENERATE,
                   previous={2: {"bom": _bom_items(43), "bom_summary": {}}})
        result = await BomGenerateExecutor()(ctx)
        table = result["bom_table"]
        assert len(table["rows"]) == 43  # 行高可压缩，不截断
        assert table["style"]["row_height"] < 15.0
        assert table["position"]["y"] + table["position"]["height"] <= 287.0
        assert result.get("warnings") and "压缩" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_extreme_overflow_truncates_with_warning(self, tmp_path):
        """行高压到下限仍装不下 → 截断 + warnings（禁止静默）"""
        ctx = _ctx(tmp_path, 5, StepName.BOM_GENERATE,
                   previous={2: {"bom": _bom_items(500), "bom_summary": {}}})
        result = await BomGenerateExecutor()(ctx)
        table = result["bom_table"]
        assert len(table["rows"]) < 500
        assert table["position"]["y"] + table["position"]["height"] <= 287.0
        assert result.get("warnings") and "截断" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_a0_sheet_from_step3_layout(self, tmp_path):
        """Step3 选型 A0 → BOM 默认定位右对齐 A0 图框，43 行不压缩"""
        ctx = _ctx(tmp_path, 5, StepName.BOM_GENERATE,
                   previous={2: {"bom": _bom_items(43), "bom_summary": {}},
                             3: {"layout": {"sheet_size": "A0"}}})
        result = await BomGenerateExecutor()(ctx)
        table = result["bom_table"]
        assert table["position"]["x"] == pytest.approx(1189.0 - 20.0 - 160.0)
        assert table["position"]["y"] + table["position"]["height"] <= 841.0 - 10.0
        assert table["style"]["row_height"] == 15.0
        assert not result.get("warnings")

    @pytest.mark.asyncio
    async def test_column_width_adapts_to_content(self, tmp_path):
        """长图号/名称 → 列宽按内容加宽 + warning"""
        bom = [{"level": 1, "name": "超长名称" * 10,
                "path": "C:/p/LB26.11000超长图号ABCDE.SLDPRT",
                "quantity": 1, "is_suppressed": False}]
        ctx = _ctx(tmp_path, 5, StepName.BOM_GENERATE,
                   previous={2: {"bom": bom, "bom_summary": {}}})
        result = await BomGenerateExecutor()(ctx)
        style = result["bom_table"]["style"]
        assert style["column_widths"][1] > 45.0  # 图号列加宽
        assert style["column_widths"][2] > 45.0  # 名称列加宽
        assert any("列加宽" in w for w in result.get("warnings", []))


# ----------------------------------------------------------------------
# Step7：标题栏比例/材料 + BOM 列宽 + 标注偏移抽样
# ----------------------------------------------------------------------

def _views_for_dxf(scale: str = "1:50") -> dict:
    front = _view("front", 1000.0, 300.0)
    front["scale"] = scale
    return {
        "views": [front],
        "layout": {
            "sheet_size": "A0",
            "orientation": "landscape",
            "view_positions": {
                "front": {"x": 20.0, "y": 700.0, "width": 20.0, "height": 6.0},
            },
        },
    }


class TestDxfTitleAndOffsets:
    @pytest.mark.asyncio
    async def test_title_scale_and_material(self, tmp_path):
        """比例取 Step3 实际 scale（1:50，禁止写死 1:1）；
        多材料（{材料:计数} 形态）→ "见明细表"，禁止填计数数字"""
        geometry = {
            "bom": [{"level": 0, "name": "底架焊合",
                     "path": "C:/asm/LB26.11000.SLDASM", "quantity": 1,
                     "is_suppressed": False}],
            "materials": {"Q235": 14, "Q355": 61, "45": 2},
            "total_mass": 0.0,
        }
        ctx = _ctx(tmp_path, 7, StepName.DXF_BUILD,
                   previous={2: geometry, 3: _views_for_dxf("1:50")})
        result = await DxfBuildExecutor()(ctx)
        assert result["dxf_structure"]["header"]["sheet_size"] == "A0"

        doc = ezdxf.readfile(tmp_path / "output" / "drawing.dxf")
        texts = [e.dxf.text for e in doc.modelspace() if e.dxftype() == "TEXT"]
        assert any("比例: 1:50" in t for t in texts)
        assert any("材料: 见明细表" in t for t in texts)
        assert not any("材料: 14" in t for t in texts)
        # 图框使用 A0 尺寸
        frame_lines = [e for e in doc.modelspace()
                       if e.dxftype() == "LINE" and e.dxf.layer == "图框"]
        max_x = max(max(e.dxf.start.x, e.dxf.end.x) for e in frame_lines)
        assert max_x == pytest.approx(1189.0 - 10.0)

    @pytest.mark.asyncio
    async def test_single_material_filled_directly(self, tmp_path):
        """唯一材料（{件名:材料} 形态）→ 材料栏直接填"""
        geometry = {"bom": [], "materials": {"P1": "Q235"}, "total_mass": 0.0}
        ctx = _ctx(tmp_path, 7, StepName.DXF_BUILD,
                   previous={2: geometry, 3: _views_for_dxf()})
        await DxfBuildExecutor()(ctx)
        doc = ezdxf.readfile(tmp_path / "output" / "drawing.dxf")
        texts = [e.dxf.text for e in doc.modelspace() if e.dxftype() == "TEXT"]
        assert any("材料: Q235" in t for t in texts)

    @pytest.mark.asyncio
    async def test_dimension_local_plus_view_offset(self, tmp_path):
        """标注落图坐标 = 视图局部 × scale + 视图图幅偏移（抽样断言）"""
        dims = {"dimensions": [{
            "id": "dim_x", "type": "linear", "value": 1000.0, "unit": "mm",
            "tolerance": {},
            "position": {"x1": 0.0, "y1": -200.0, "x2": 1000.0, "y2": -200.0},
            "associated_entities": [], "is_automatic": True,
            "confidence": 1.0, "view_name": "front",
        }]}
        ctx = _ctx(tmp_path, 7, StepName.DXF_BUILD,
                   previous={3: _views_for_dxf("1:50"), 4: dims})
        await DxfBuildExecutor()(ctx)
        doc = ezdxf.readfile(tmp_path / "output" / "drawing.dxf")
        dim_lines = [e for e in doc.modelspace()
                     if e.dxftype() == "LINE" and e.dxf.layer == "标注"]
        # 标注线：局部 (0,-200)→(1000,-200)，scale=0.02，偏移 (20,700)
        # → 图纸 (20,696) → (40,696)
        found = any(
            abs(e.dxf.start.x - 20.0) < 1e-6 and abs(e.dxf.start.y - 696.0) < 1e-6
            and abs(e.dxf.end.x - 40.0) < 1e-6 and abs(e.dxf.end.y - 696.0) < 1e-6
            for e in dim_lines)
        assert found, "标注坐标 ≠ 局部×scale + 视图偏移"

    @pytest.mark.asyncio
    async def test_bom_column_widths_consumed(self, tmp_path):
        """BOM 落图消费 style.column_widths（归一化到表宽），不再均分压穿"""
        bom = {"bom_table": {
            "columns": ["序号", "图号"],
            "rows": [[1, "LB26.11000"]],
            "position": {"x": 100.0, "y": 50.0, "width": 200.0, "height": 35.0},
            "style": {"header_height": 20.0, "row_height": 15.0,
                      "font_size": 3.5, "column_widths": [1.0, 3.0]},
        }}
        ctx = _ctx(tmp_path, 7, StepName.DXF_BUILD,
                   previous={3: _views_for_dxf(), 5: bom})
        await DxfBuildExecutor()(ctx)
        doc = ezdxf.readfile(tmp_path / "output" / "drawing.dxf")
        texts = {e.dxf.text: e for e in doc.modelspace()
                 if e.dxftype() == "TEXT" and e.dxf.layer == "BOM"}
        # 图号列表头 x = 100 + 200×(1/4) + 1 = 151
        assert texts["图号"].dxf.insert.x == pytest.approx(151.0)
