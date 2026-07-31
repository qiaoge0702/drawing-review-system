"""
Step 3 视图投影执行器单元测试

不依赖真实 SW 环境：
- run_sw 的 STEP 导出环节用 monkeypatch mock（仅生成空 .step 文件占位）
- trimesh.load 替换为内置几何（trimesh.creation.box），验证投影与契约 JSON
"""

import json
from pathlib import Path

import pytest
import trimesh

from app.generators.models import StepContext
from app.generators.steps import step3_view_project
from app.generators.steps.step3_view_project import ViewProjectExecutor, project_mesh
from app.models.generation import StepName
from app.core.exceptions import SWException, ErrorCode


def _make_ctx(tmp_path: Path, source_file: Path) -> StepContext:
    return StepContext(
        task_id="test-step3",
        step=3,
        step_name=StepName.VIEW_PROJECT,
        work_dir=tmp_path,
        parameters={"source_file": str(source_file)},
        previous_results={1: {"file_type": "part"}},
    )


@pytest.fixture
def fake_sw_box(monkeypatch, tmp_path):
    """Mock SW 导出 + STEP 加载，返回 10x20x30 盒体 mesh"""
    box = trimesh.creation.box(extents=[10.0, 20.0, 30.0])

    async def fake_run_sw(func, source_file, step_path):
        Path(step_path).write_text("ISO-10303-21;")  # 占位 STEP 文件
        return step_path

    monkeypatch.setattr(step3_view_project, "run_sw", fake_run_sw)
    monkeypatch.setattr(trimesh, "load", lambda *a, **k: box)
    return box


class TestViewProjectExecutor:
    @pytest.mark.asyncio
    async def test_three_views_contract(self, tmp_path, fake_sw_box):
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)

        result = await ViewProjectExecutor()(ctx)

        gb_scales = {"1:1", "1:2", "1:2.5", "1:5", "1:10", "1:20", "1:50", "1:100"}
        # 三视图数量与名称
        assert [v["name"] for v in result["views"]] == ["front", "top", "left"]
        for view in result["views"]:
            assert view["entities"], f"{view['name']} entities 为空"
            # 契约：scale 必须是 GB 标准比例系列成员，禁止硬编码以外取值
            assert view["scale"] in gb_scales
            assert view["hidden_lines"] == []
            assert view["center_lines"] == []
            assert all(e["type"] == "line" for e in view["entities"])
            # 契约：实体已归一化，bbox 原点 = 视图左下角
            bb = view["bounding_box"]
            assert bb["min_x"] == 0.0
            assert bb["min_y"] == 0.0
            xs = [e["x1"] for e in view["entities"]] + [e["x2"] for e in view["entities"]]
            ys = [e["y1"] for e in view["entities"]] + [e["y2"] for e in view["entities"]]
            assert min(xs) == pytest.approx(0.0)
            assert min(ys) == pytest.approx(0.0)

        # 盒体 front 投影 (u=X, v=Z) 应为 10x30 矩形，归一化后 0..10 / 0..30
        front = result["views"][0]["bounding_box"]
        assert front["max_x"] == pytest.approx(10.0)
        assert front["max_y"] == pytest.approx(30.0)
        # 矩形轮廓应收敛为 4 条线段
        assert len(result["views"][0]["entities"]) == 4

        # top 投影 (u=X, v=Y) → 10x20
        top = result["views"][1]["bounding_box"]
        assert top["max_y"] - top["min_y"] == pytest.approx(20.0)

        # layout 结构 + 图纸坐标必须落在 A3 图幅内（0≤x≤420, 0≤y≤297）
        layout = result["layout"]
        assert layout["sheet_size"] == "A3"
        assert set(layout["view_positions"]) == {"front", "top", "left"}
        for name, pos in layout["view_positions"].items():
            assert 0.0 <= pos["x"] <= 420.0, f"{name} x 越界"
            assert 0.0 <= pos["y"] <= 297.0, f"{name} y 越界"
            assert pos["x"] + pos["width"] <= 420.0, f"{name} 右缘越界"
            assert pos["y"] + pos["height"] <= 297.0, f"{name} 上缘越界"

    @pytest.mark.asyncio
    async def test_views_json_artifact(self, tmp_path, fake_sw_box):
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)

        result = await ViewProjectExecutor()(ctx)

        views_file = tmp_path / "output" / "views.json"
        assert views_file.exists()
        on_disk = json.loads(views_file.read_text(encoding="utf-8"))
        assert on_disk == result

    @pytest.mark.asyncio
    async def test_sw_unavailable_raises(self, tmp_path, monkeypatch):
        """导出失败必须抛 GEN_SW_NOT_AVAILABLE，禁止静默空数据"""
        async def failing_run_sw(func, *args):
            raise RuntimeError("SW not installed")

        monkeypatch.setattr(step3_view_project, "run_sw", failing_run_sw)
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)

        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE

    @pytest.mark.asyncio
    async def test_missing_source_file(self, tmp_path):
        ctx = _make_ctx(tmp_path, tmp_path / "nonexistent.sldprt")
        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_INVALID_FILE

    @pytest.mark.asyncio
    async def test_auto_scale_gb_series(self, tmp_path, monkeypatch):
        """大零件（1000mm 盒体）→ 自动比例向下取 GB 系列，图纸坐标仍在图幅内"""
        big_box = trimesh.creation.box(extents=[1000.0, 1000.0, 1000.0])

        async def fake_run_sw(func, source_file, out_path):
            Path(out_path).write_text("solid fake")
            return out_path

        monkeypatch.setattr(step3_view_project, "run_sw", fake_run_sw)
        monkeypatch.setattr(trimesh, "load", lambda *a, **k: big_box)
        src = tmp_path / "big.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)
        ctx.parameters["views"] = ["front"]  # 单视图子集，聚焦比例计算

        result = await ViewProjectExecutor()(ctx)

        # 1000mm / 380mm 可用宽 ≈ 2.63 → GB 系列向下取 1:5
        for view in result["views"]:
            assert view["scale"] == "1:5"
            # 缩放后 1000/5 = 200mm 宽
            pos = result["layout"]["view_positions"][view["name"]]
            assert pos["width"] == pytest.approx(200.0)
            assert pos["height"] == pytest.approx(200.0)
            assert 0.0 <= pos["x"] <= 420.0
            assert 0.0 <= pos["y"] <= 297.0

    def test_scale_computation_rejects_degenerate(self):
        """比例计算输入异常（空视图/零尺寸）→ SWException，禁止静默"""
        from app.generators.steps.step3_view_project import _compute_scale_denominator
        with pytest.raises(SWException):
            _compute_scale_denominator([])
        zero_view = {"name": "front",
                     "bounding_box": {"min_x": 0.0, "min_y": 0.0,
                                      "max_x": 0.0, "max_y": 0.0}}
        with pytest.raises(SWException):
            _compute_scale_denominator([zero_view])


class TestProjectMesh:
    def test_box_projection_is_rectangle(self):
        box = trimesh.creation.box(extents=[2.0, 4.0, 6.0])
        view = project_mesh(box, "front")
        bb = view["bounding_box"]
        assert bb["max_x"] - bb["min_x"] == pytest.approx(2.0)
        assert bb["max_y"] - bb["min_y"] == pytest.approx(6.0)
        assert len(view["entities"]) == 4

    def test_cylinder_projection_nonempty(self):
        cyl = trimesh.creation.cylinder(radius=5.0, height=10.0, sections=32)
        for name in ("front", "top", "left"):
            view = project_mesh(cyl, name)
            assert view["entities"]
        # top 投影应近似圆盘（直径 10）
        top = project_mesh(cyl, "top")["bounding_box"]
        assert top["max_x"] - top["min_x"] == pytest.approx(10.0, abs=0.2)
