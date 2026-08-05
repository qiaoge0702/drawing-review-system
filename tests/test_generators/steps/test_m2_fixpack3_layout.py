"""
M2 收口修复包3 回归测试：视图坐标系统一 + 第一角布局 + BOM/标题栏修复

覆盖：
- Step3 第一角布局（俯视在主视正下方、左视在主视正右方）+ 图幅 A3→A0 选型
- Step5 BOM 高度图幅适配（压缩行高/截断 + warnings）+ 列宽内容适配
- Step7（方案B重写）：标题栏比例/材料/重量如实填写 + 执行器接线（mock COM 层）
"""

from pathlib import Path

import pytest

from app.generators.models import StepContext
from app.generators.steps.step3_view_project import (
    _compute_layout_with_retry,
    _select_sheet_by_content,
)
from app.generators.type_recognition import PartType
from app.generators.view_strategy import (
    get_view_strategy,
    SHEET_A3_WIDTH,
    SHEET_A3_HEIGHT,
)
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
# Step3：第一角布局 + 图幅选型（B-M1：主视=最宽投影视图，居中偏上；
#        侧视在主视右侧；俯视主视正下方；轴测左下角）
# ----------------------------------------------------------------------

def _layout_flow(sizes: dict, bom_rows: int = 0):
    """模拟生产链路：A3 比例初算 → 图幅选型 → 升级后按最终图幅重算比例"""
    strategy = get_view_strategy(PartType.BEAM)
    positions, den, _ = _compute_layout_with_retry(
        sizes, SHEET_A3_WIDTH, SHEET_A3_HEIGHT, strategy, "t")
    sheet, w, h = _select_sheet_by_content(sizes, den, bom_rows, "t")
    if (w, h) != (SHEET_A3_WIDTH, SHEET_A3_HEIGHT):
        positions, den, _ = _compute_layout_with_retry(
            sizes, w, h, strategy, "t")
    return sheet, den, positions


class TestFirstAngleLayout:
    def test_real_case_layout_a0(self):
        """真机尺寸（LB26：端视1002x327/俯视1002x6959.5/侧视6924x327，BOM 43 行）
        → 图幅 A0、比例按最终图幅重算=1:10、主视=长梁侧视（最宽者）居中、
        端视在主视右侧、俯视在主视正下方、全部落有效区不重叠"""
        sizes = {"front": (1002.0, 327.0),
                 "top": (1002.0, 6959.5009),
                 "left": (6924.0, 327.0)}
        sheet, den, pos = _layout_flow(sizes, bom_rows=43)

        assert sheet == "A0"
        assert den == 10.0
        # 主视 = 最宽者（left 长梁侧视）
        main, side, top = pos["left"], pos["front"], pos["top"]
        # 俯视在主视正下方（x 对齐），端视在主视正右方
        assert top["x"] == pytest.approx(main["x"])
        assert top["y"] + top["height"] < main["y"]
        assert side["x"] >= main["x"] + main["width"]
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
        """小件无 BOM → A3，1:1，主视=最宽者"""
        sizes = {"front": (10.0, 30.0),
                 "top": (10.0, 20.0),
                 "left": (20.0, 30.0)}
        sheet, den, pos = _layout_flow(sizes)
        assert den == 1.0
        assert sheet == "A3"
        main, side, top = pos["left"], pos["front"], pos["top"]
        assert top["y"] + top["height"] < main["y"]
        assert side["x"] > main["x"]

    def test_bom_estimate_forces_bigger_sheet(self):
        """视图装得下 A3 但 BOM 43 行装不下 → 升级到能装下 BOM 的图幅"""
        sheet, den, pos = _layout_flow({"front": (100.0, 50.0)}, bom_rows=43)
        assert sheet == "A0"  # A1 高 594 也装不下 665+50


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
# Step7（方案B重写）：标题栏字段如实填写 + 执行器接线（mock COM 层）
# ----------------------------------------------------------------------

def _views_for_drawing(scale: str = "1:50") -> dict:
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
        # 方案B重写新增：Step3 中间 SLDDRW（Step7 在其上收尾）
        "drawing_path": "C:/fake/step3/drawing.slddrw",
        "snapshot_path": "C:/fake/step3/snapshot.png",
        "scale_denominator": 50.0,
        "sheet_size": "A0",
    }


class TestTitleBlockAndFinalize:
    @pytest.mark.asyncio
    async def test_title_scale_and_material(self, tmp_path, monkeypatch):
        """比例取 Step3 实际 scale（1:50，禁止写死 1:1）；
        多材料（{材料:计数} 形态）→ "见明细表"，禁止填计数数字"""
        from app.generators.steps import step7_dxf_build as s7

        captured = {}

        async def fake_run_sw(func, drawing_path, properties, model_path,
                              output_dir, task_id):
            captured["drawing_path"] = drawing_path
            captured["properties"] = properties
            captured["model_path"] = model_path
            return {"slddrw_path": f"{output_dir}/drawing.slddrw",
                    "snapshot_path": f"{output_dir}/snapshot.png",
                    "properties_applied": list(properties),
                    "properties_readback": {}, "warnings": []}

        monkeypatch.setattr(s7, "run_sw", fake_run_sw)
        geometry = {
            "bom": [{"level": 0, "name": "底架焊合",
                     "path": "C:/asm/LB26.11000.SLDASM", "quantity": 1,
                     "is_suppressed": False}],
            "materials": {"Q235": 14, "Q355": 61, "45": 2},
        }
        ctx = _ctx(tmp_path, 7, StepName.DXF_BUILD,
                   previous={2: geometry, 3: _views_for_drawing("1:50")})
        result = await DxfBuildExecutor()(ctx)
        # 旧 step3 drawing_path 直接透传给 COM 收尾层
        assert captured["drawing_path"] == "C:/fake/step3/drawing.slddrw"
        props = captured["properties"]
        assert props["比例"] == "1:50"
        assert props["材料"] == "见明细表"
        assert "14" not in props["材料"]
        assert props["代号"] == "LB26.11000"
        assert props["名称"] == "底架焊合"
        # 缺陷3：模型路径回退自 Step2 顶层 BOM path
        assert captured["model_path"] == "C:/asm/LB26.11000.SLDASM"
        assert result["slddrw_path"].endswith("drawing.slddrw")
        assert result["snapshot_path"].endswith("snapshot.png")

    @pytest.mark.asyncio
    async def test_single_material_and_weight(self, tmp_path, monkeypatch):
        """唯一材料（{件名:材料} 形态）→ 材料栏直填；重量=mass×数量求和 kg"""
        from app.generators.steps import step7_dxf_build as s7

        captured = {}

        async def fake_run_sw(func, drawing_path, properties, model_path,
                              output_dir, task_id):
            captured["properties"] = properties
            return {"slddrw_path": "a", "snapshot_path": "b",
                    "properties_applied": [],
                    "properties_readback": {}, "warnings": []}

        monkeypatch.setattr(s7, "run_sw", fake_run_sw)
        geometry = {
            "bom": [{"level": 1, "name": "板", "path": "C:/p/P1.SLDPRT",
                     "quantity": 2, "is_suppressed": False, "mass": 1.5},
                    {"level": 1, "name": "轴", "path": "C:/p/P2.SLDPRT",
                     "quantity": 1, "is_suppressed": False, "mass": 0.25}],
            "materials": {"P1": "Q235"},
        }
        ctx = _ctx(tmp_path, 7, StepName.DXF_BUILD,
                   previous={2: geometry, 3: _views_for_drawing()})
        await DxfBuildExecutor()(ctx)
        assert captured["properties"]["材料"] == "Q235"
        assert captured["properties"]["质量"] == "3.250"  # 1.5×2 + 0.25

    @pytest.mark.asyncio
    async def test_missing_drawing_path_raises(self, tmp_path):
        """缺 Step3 drawing_path（旧 DXF 契约检查点）→ SWException 上抛"""
        from app.core.exceptions import SWException
        bad_step3 = _views_for_drawing()
        del bad_step3["drawing_path"]
        ctx = _ctx(tmp_path, 7, StepName.DXF_BUILD, previous={3: bad_step3})
        with pytest.raises(SWException):
            await DxfBuildExecutor()(ctx)
