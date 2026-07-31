"""
Step 3 视图投影执行器 - 独立验证测试（tester 补充，非 dev 自写）

补充 dev 测试（test_step3_view_project.py）未覆盖的契约点：
- 不支持的视图名 → SWException(GEN_UNSUPPORTED_FEATURE)
- 自定义 views 子集（如仅 ["front"]）
- display_name / projection="first_angle" / bounding_box 四键完整性
- project_mesh 空网格 / 无轮廓 → SWException(GEN_STEP_FAILED)
- 导出目标必须是 .stl（契约：格式已从 STEP 改为 STL）
- 导出失败时禁止静默返回空数据（断言无返回值路径 + views.json 不落盘）
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


def _make_ctx(tmp_path: Path, source_file: Path, views=None) -> StepContext:
    params = {"source_file": str(source_file)}
    if views is not None:
        params["views"] = views
    return StepContext(
        task_id="test-step3-ind",
        step=3,
        step_name=StepName.VIEW_PROJECT,
        work_dir=tmp_path,
        parameters=params,
        previous_results={},
    )


@pytest.fixture
def fake_sw_box(monkeypatch):
    """Mock run_sw 导出 + trimesh.load，返回 10x20x30 盒体；记录导出路径后缀"""
    box = trimesh.creation.box(extents=[10.0, 20.0, 30.0])
    recorded = {}

    async def fake_run_sw(func, source_file, out_path):
        recorded["out_path"] = out_path
        Path(out_path).write_text("solid fake")  # 占位 STL
        return out_path

    monkeypatch.setattr(step3_view_project, "run_sw", fake_run_sw)
    monkeypatch.setattr(trimesh, "load", lambda *a, **k: box)
    return recorded


class TestUnsupportedViews:
    @pytest.mark.asyncio
    async def test_unsupported_view_raises_gen_unsupported_feature(self, tmp_path):
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src, views=["front", "isometric"])

        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_UNSUPPORTED_FEATURE

    @pytest.mark.asyncio
    async def test_case_sensitive_view_name_rejected(self, tmp_path):
        """"Front"（大写）不在允许集合内，必须抛错而非静默忽略"""
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src, views=["Front"])

        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_UNSUPPORTED_FEATURE


class TestCustomViewSubset:
    @pytest.mark.asyncio
    async def test_single_front_view(self, tmp_path, fake_sw_box):
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src, views=["front"])

        result = await ViewProjectExecutor()(ctx)

        assert [v["name"] for v in result["views"]] == ["front"]
        assert set(result["layout"]["view_positions"]) == {"front"}
        on_disk = json.loads(
            (tmp_path / "output" / "views.json").read_text(encoding="utf-8")
        )
        assert on_disk == result

    @pytest.mark.asyncio
    async def test_display_name_and_projection_fields(self, tmp_path, fake_sw_box):
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)

        result = await ViewProjectExecutor()(ctx)

        expected = {"front": "主视图", "top": "俯视图", "left": "左视图"}
        for view in result["views"]:
            assert view["display_name"] == expected[view["name"]]
            assert view["projection"] == "first_angle"
            bb = view["bounding_box"]
            assert set(bb) == {"min_x", "min_y", "max_x", "max_y"}


class TestExportFormat:
    @pytest.mark.asyncio
    async def test_export_target_is_stl_not_step(self, tmp_path, fake_sw_box):
        """契约：导出格式已从 STEP 改为 STL，run_sw 收到的路径必须以 .stl 结尾"""
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)

        await ViewProjectExecutor()(ctx)

        assert fake_sw_box["out_path"].lower().endswith(".stl"), (
            f"导出目标不是 STL: {fake_sw_box['out_path']}"
        )


class TestFailurePaths:
    @pytest.mark.asyncio
    async def test_sw_exception_from_export_propagates(self, tmp_path, monkeypatch):
        """run_sw 抛出 SWException（如 SW 未安装）时必须原样上抛，不得改写为空数据"""
        async def failing_run_sw(func, *args):
            raise SWException(
                "SW unavailable", error_code=ErrorCode.GEN_SW_NOT_AVAILABLE
            )

        monkeypatch.setattr(step3_view_project, "run_sw", failing_run_sw)
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)

        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE
        # 失败路径禁止落盘契约 JSON（避免下游拿到残缺产物）
        assert not (tmp_path / "output" / "views.json").exists()

    @pytest.mark.asyncio
    async def test_missing_source_file_error_carries_context(self, tmp_path):
        ctx = _make_ctx(tmp_path, tmp_path / "nope.sldprt")
        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        err = exc_info.value
        assert err.error_code == ErrorCode.GEN_INVALID_FILE
        assert getattr(err, "task_id", None) == "test-step3-ind"
        assert getattr(err, "step", None) == 3


class TestProjectMeshEdgeCases:
    def test_empty_mesh_raises_gen_step_failed(self):
        """空网格（零面片）→ SWException(GEN_STEP_FAILED)"""
        empty = trimesh.Trimesh(vertices=[], faces=[])
        with pytest.raises(SWException) as exc_info:
            project_mesh(empty, "front")
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    def test_degenerate_zero_area_mesh_raises(self):
        """所有面片投影面积为零（如垂直于投影面的薄片）→ SWException(GEN_STEP_FAILED)"""
        # 一个位于 X=0 平面内的三角形，front 投影 (u=X) 后 u 恒为 0，面积为零
        verts = [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        mesh = trimesh.Trimesh(vertices=verts, faces=[[0, 1, 2]])
        with pytest.raises(SWException) as exc_info:
            project_mesh(mesh, "front")
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    def test_left_view_bounding_box_uses_yz(self):
        """left 投影 (u=Y, v=Z)：10x20x30 盒体应为 20x30"""
        box = trimesh.creation.box(extents=[10.0, 20.0, 30.0])
        bb = project_mesh(box, "left")["bounding_box"]
        assert bb["max_x"] - bb["min_x"] == pytest.approx(20.0)
        assert bb["max_y"] - bb["min_y"] == pytest.approx(30.0)
