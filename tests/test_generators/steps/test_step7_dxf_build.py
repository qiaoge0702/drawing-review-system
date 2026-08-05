"""
Step 7 图纸收尾执行器单元测试（方案B重写 2026-08-02）

ezdxf 拼装 DXF 全部逻辑已删除；新契约 = 在 Step3 中间 SLDDRW 上收尾：
标题栏自定义属性 → 另存 SLDDRW/DWG/PDF + PNG 终图快照。

不依赖 SW 环境：monkeypatch run_sw 隔离 COM 层，覆盖：
- 正常流：title_block 字段如实组装（图号/名称/材料/重量/比例），
  属性透传 COM 层，输出四份产物路径
- 降级：缺 Step2 几何数据 → 标题栏留空 + warnings（诚实原则）
- 异常：缺 Step3 产物 / 缺 drawing_path（旧 DXF 契约检查点）→ SWException
- COM 层 SWException 原样上抛；普通异常包装 GEN_SW_NOT_AVAILABLE
"""

from pathlib import Path

import pytest

from app.generators.models import StepContext
from app.generators.steps import step7_dxf_build
from app.generators.steps.step7_dxf_build import (
    DxfBuildExecutor,
    title_block_info,
)
from app.models.generation import StepName
from app.core.exceptions import SWException, ErrorCode


# ----------------------------------------------------------------------
# Mock 产物
# ----------------------------------------------------------------------

def _views_result(scale: str = "1:10"):
    """Step3 方案B契约产物"""
    return {
        "views": [
            {
                "name": "front",
                "display_name": "主视图",
                "projection": "first_angle",
                "entities": [],
                "hidden_lines": [],
                "center_lines": [],
                "section_hatch": None,
                "bounding_box": {"min_x": 0.0, "min_y": 0.0,
                                 "max_x": 1000.0, "max_y": 300.0},
                "scale": scale,
            }
        ],
        "layout": {
            "sheet_size": "A0",
            "orientation": "landscape",
            "view_positions": {
                "front": {"x": 20.0, "y": 500.0, "width": 100.0, "height": 30.0},
            },
        },
        "drawing_path": "C:/fake/task/step_3/output/drawing.slddrw",
        "snapshot_path": "C:/fake/task/step_3/output/snapshot.png",
        "scale_denominator": 10.0,
        "sheet_size": "A0",
    }


def _geometry():
    return {
        "bom": [
            {"level": 0, "name": "拉臂总成",
             "path": "C:/asm/LB26.00000拉臂总成.SLDASM", "quantity": 1,
             "is_suppressed": False},
            {"level": 1, "name": "旋转轴", "path": "C:/p/LB26.00001.SLDPRT",
             "quantity": 1, "is_suppressed": False, "mass": 3.5},
            {"level": 1, "name": "隔套", "path": "C:/p/LB26.00003.SLDPRT",
             "quantity": 2, "is_suppressed": False, "mass": 0.75},
        ],
        "materials": {"45": 3},
    }


def _make_ctx(tmp_path: Path, previous: dict) -> StepContext:
    return StepContext(
        task_id="test-step7",
        step=7,
        step_name=StepName.DXF_BUILD,
        work_dir=tmp_path,
        parameters={},
        previous_results=previous,
    )


def _patch_run_sw(monkeypatch, captured: dict, warnings=None):
    async def fake_run_sw(func, drawing_path, properties, model_path,
                          output_dir, task_id):
        captured["func"] = func.__name__
        captured["drawing_path"] = drawing_path
        captured["properties"] = dict(properties)
        captured["model_path"] = model_path
        captured["output_dir"] = output_dir
        return {
            "slddrw_path": str(Path(output_dir) / "drawing.slddrw"),
            "snapshot_path": str(Path(output_dir) / "snapshot.png"),
            "properties_applied": [k for k, v in properties.items() if v],
            "properties_readback": {k: v for k, v in properties.items() if v},
            "warnings": list(warnings or []),
        }
    monkeypatch.setattr(step7_dxf_build, "run_sw", fake_run_sw)


# ----------------------------------------------------------------------
# title_block_info（纯函数）
# ----------------------------------------------------------------------

class TestTitleBlockInfo:
    def test_full_info(self):
        info = title_block_info(_geometry(), _views_result("1:10"), [])
        # 缺陷3：代号/名称按模板 $PRPSHEET:{代号}/{名称} 语义拆分
        assert info["drawing_number"] == "LB26.00000"
        assert info["name"] == "拉臂总成"
        assert info["material"] == "45"
        assert info["weight"] == "5.000"  # 3.5×1 + 0.75×2
        assert info["scale"] == "1:10"

    def test_assembly_without_material_see_bom(self):
        """装配体且材料未提取 → 材料按惯例填'见明细表' + 如实 warning"""
        g = _geometry()
        g["materials"] = {}
        warnings = []
        info = title_block_info(g, _views_result(), warnings)
        assert info["material"] == "见明细表"
        assert any("见明细表" in w for w in warnings)

    def test_multi_material_see_bom(self):
        g = _geometry()
        g["materials"] = {"Q235": 14, "45": 2}
        info = title_block_info(g, _views_result(), [])
        assert info["material"] == "见明细表"

    def test_missing_geometry_blanks_with_warning(self):
        warnings = []
        info = title_block_info(None, _views_result(), warnings)
        assert info["drawing_number"] == ""
        assert info["material"] == ""
        assert info["weight"] == ""
        assert info["scale"] == "1:10"  # 比例来自 Step3，不受影响
        assert warnings

    def test_no_mass_weight_blank(self):
        g = _geometry()
        for item in g["bom"]:
            item.pop("mass", None)
        warnings = []
        info = title_block_info(g, _views_result(), warnings)
        assert info["weight"] == ""
        assert any("重量" in w for w in warnings)


# ----------------------------------------------------------------------
# 执行器
# ----------------------------------------------------------------------

class TestDxfBuildExecutor:
    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path, monkeypatch):
        captured = {}
        _patch_run_sw(monkeypatch, captured)
        ctx = _make_ctx(tmp_path, {2: _geometry(), 3: _views_result("1:10")})
        result = await DxfBuildExecutor()(ctx)

        # COM 层收到 Step3 中间 SLDDRW 路径与标题栏属性
        assert captured["func"] == "finalize_drawing_sync"
        assert captured["drawing_path"] == \
            "C:/fake/task/step_3/output/drawing.slddrw"
        # 缺陷3：模型级中文属性名 + 模型路径透传
        props = captured["properties"]
        assert props["代号"] == "LB26.00000"
        assert props["名称"] == "拉臂总成"
        assert props["材料"] == "45"
        assert props["质量"] == "5.000"
        assert props["比例"] == "1:10"
        assert captured["model_path"] == "C:/asm/LB26.00000拉臂总成.SLDASM"

        # 输出契约：骨架版 SLDDRW + 快照 + title_block
        out = tmp_path / "output"
        assert result["slddrw_path"] == str(out / "drawing.slddrw")
        assert result["snapshot_path"] == str(out / "snapshot.png")
        assert result["title_block"]["scale"] == "1:10"
        assert set(result["properties_applied"]) == set(props)

    @pytest.mark.asyncio
    async def test_degraded_without_geometry(self, tmp_path, monkeypatch):
        """缺 Step2 → 标题栏留空 + warnings，不报错（诚实原则）"""
        captured = {}
        _patch_run_sw(monkeypatch, captured)
        ctx = _make_ctx(tmp_path, {3: _views_result()})
        result = await DxfBuildExecutor()(ctx)
        assert captured["properties"]["代号"] == ""
        assert result["title_block"]["drawing_number"] == ""
        assert result.get("warnings")

    @pytest.mark.asyncio
    async def test_com_warnings_propagated(self, tmp_path, monkeypatch):
        captured = {}
        _patch_run_sw(monkeypatch, captured,
                      warnings=["标题栏属性 Foo 写入失败（如实上报）"])
        ctx = _make_ctx(tmp_path, {2: _geometry(), 3: _views_result()})
        result = await DxfBuildExecutor()(ctx)
        assert any("Foo" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_missing_step3_raises(self, tmp_path):
        ctx = _make_ctx(tmp_path, {})
        with pytest.raises(SWException) as exc_info:
            await DxfBuildExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    @pytest.mark.asyncio
    async def test_missing_drawing_path_raises(self, tmp_path):
        """旧 DXF 契约 Step3 检查点（无 drawing_path）→ 明确报错要求重跑"""
        step3 = _views_result()
        del step3["drawing_path"]
        ctx = _make_ctx(tmp_path, {3: step3})
        with pytest.raises(SWException) as exc_info:
            await DxfBuildExecutor()(ctx)
        assert "drawing_path" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sw_exception_propagates(self, tmp_path, monkeypatch):
        async def failing(func, *args):
            raise SWException("SW died",
                              error_code=ErrorCode.GEN_SW_NOT_AVAILABLE)
        monkeypatch.setattr(step7_dxf_build, "run_sw", failing)
        ctx = _make_ctx(tmp_path, {3: _views_result()})
        with pytest.raises(SWException) as exc_info:
            await DxfBuildExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE

    @pytest.mark.asyncio
    async def test_generic_exception_wrapped(self, tmp_path, monkeypatch):
        async def failing(func, *args):
            raise RuntimeError("com exploded")
        monkeypatch.setattr(step7_dxf_build, "run_sw", failing)
        ctx = _make_ctx(tmp_path, {3: _views_result()})
        with pytest.raises(SWException) as exc_info:
            await DxfBuildExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE
