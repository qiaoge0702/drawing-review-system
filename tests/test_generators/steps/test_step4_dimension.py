"""
Step 4 尺寸标注执行器单元测试

不依赖 SW 环境：直接构造 views 数据（line/circle/arc 组合）驱动执行器，
验证标注提取、位置、公差、重叠检测、异常路径与产物落盘。
"""

import json
from pathlib import Path

import pytest

from app.generators.models import StepContext
from app.generators.steps.step4_dimension import (
    DimensionExecutor,
    extract_view_dimensions,
    detect_overlaps,
)
from app.models.generation import StepName
from app.core.exceptions import SWException, ErrorCode


def _box_view(name: str = "front", min_x=0.0, min_y=0.0, max_x=100.0, max_y=50.0,
              extra_entities=None):
    """构造一个矩形外廓视图：4 条 line + 可选附加实体"""
    entities = [
        {"type": "line", "x1": min_x, "y1": min_y, "x2": max_x, "y2": min_y},
        {"type": "line", "x1": max_x, "y1": min_y, "x2": max_x, "y2": max_y},
        {"type": "line", "x1": max_x, "y1": max_y, "x2": min_x, "y2": max_y},
        {"type": "line", "x1": min_x, "y1": max_y, "x2": min_x, "y2": min_y},
    ]
    if extra_entities:
        entities.extend(extra_entities)
    return {
        "name": name,
        "display_name": name,
        "entities": entities,
        "bounding_box": {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y},
        "scale": "1:1",
    }


def _make_ctx(tmp_path: Path, views_result=None, parameters=None) -> StepContext:
    previous = {}
    if views_result is not None:
        previous[3] = views_result
    return StepContext(
        task_id="test-step4",
        step=4,
        step_name=StepName.DIMENSION,
        work_dir=tmp_path,
        parameters=parameters or {},
        previous_results=previous,
    )


class TestDimensionExecutor:
    @pytest.mark.asyncio
    async def test_linear_diameter_radius_extraction(self, tmp_path):
        """line 外廓 → linear；circle → diameter(⌀)；arc → radius(R)"""
        view = _box_view(extra_entities=[
            {"type": "circle", "cx": 50.0, "cy": 25.0, "r": 10.0},
            {"type": "arc", "cx": 20.0, "cy": 20.0, "r": 5.0,
             "start_angle": 0.0, "end_angle": 90.0},
        ])
        ctx = _make_ctx(tmp_path, {"views": [view], "layout": {}})
        result = await DimensionExecutor()(ctx)

        by_type = {}
        for d in result["dimensions"]:
            by_type.setdefault(d["type"], []).append(d)

        # 外廓 linear 标注 2 条（宽 + 高）
        linear = by_type["linear"]
        assert len(linear) == 2
        width_dim = next(d for d in linear if d["value"] == pytest.approx(100.0))
        height_dim = next(d for d in linear if d["value"] == pytest.approx(50.0))

        # 直径标注
        dia = by_type["diameter"]
        assert len(dia) == 1
        assert dia[0]["value"] == pytest.approx(20.0)
        assert dia[0]["prefix"] == "⌀"

        # 半径标注
        rad = by_type["radius"]
        assert len(rad) == 1
        assert rad[0]["value"] == pytest.approx(5.0)
        assert rad[0]["prefix"] == "R"

        # 契约公共字段
        for d in result["dimensions"]:
            assert d["unit"] == "mm"
            assert d["is_automatic"] is True
            assert d["confidence"] == 1.0
            assert d["tolerance"]["grade"] == "IT14"
            assert d["associated_entities"]
            assert d["view_name"] == "front"  # 所属视图名（Step7 定位用）
            assert set(d["position"]) == {"x1", "y1", "x2", "y2", "text_x", "text_y"}

    @pytest.mark.asyncio
    async def test_bbox_outline_position_and_value(self, tmp_path):
        """外廓标注：标注线偏移 bbox 外 10mm，文字居中，值正确"""
        view = _box_view(min_x=10.0, min_y=20.0, max_x=110.0, max_y=70.0)
        ctx = _make_ctx(tmp_path, {"views": [view]})
        result = await DimensionExecutor()(ctx)

        linear = [d for d in result["dimensions"] if d["type"] == "linear"]
        width_dim = next(d for d in linear if d["value"] == pytest.approx(100.0))
        height_dim = next(d for d in linear if d["value"] == pytest.approx(50.0))

        # 宽度标注线在 bbox 下方 10mm（min_y - 10），文字水平居中
        assert width_dim["position"]["y1"] == pytest.approx(10.0)
        assert width_dim["position"]["y2"] == pytest.approx(10.0)
        assert width_dim["position"]["x1"] == pytest.approx(10.0)
        assert width_dim["position"]["x2"] == pytest.approx(110.0)
        assert width_dim["position"]["text_x"] == pytest.approx(60.0)
        assert width_dim["position"]["text_y"] == pytest.approx(10.0)

        # 高度标注线在 bbox 左侧 10mm（min_x - 10），文字垂直居中
        assert height_dim["position"]["x1"] == pytest.approx(0.0)
        assert height_dim["position"]["x2"] == pytest.approx(0.0)
        assert height_dim["position"]["y1"] == pytest.approx(20.0)
        assert height_dim["position"]["y2"] == pytest.approx(70.0)
        assert height_dim["position"]["text_x"] == pytest.approx(0.0)
        assert height_dim["position"]["text_y"] == pytest.approx(45.0)

    @pytest.mark.asyncio
    async def test_custom_dimension_config(self, tmp_path):
        view = _box_view()
        ctx = _make_ctx(tmp_path, {"views": [view]},
                        parameters={"dimension_config": {
                            "default_tolerance_grade": "IT12",
                            "dimension_offset": 20.0,
                            "default_tolerance_upper": 0.2,
                            "default_tolerance_lower": -0.2,
                        }})
        result = await DimensionExecutor()(ctx)
        width_dim = next(d for d in result["dimensions"] if d["value"] == pytest.approx(100.0))
        assert width_dim["tolerance"]["grade"] == "IT12"
        assert width_dim["tolerance"]["upper"] == pytest.approx(0.2)
        assert width_dim["tolerance"]["lower"] == pytest.approx(-0.2)
        assert width_dim["position"]["y1"] == pytest.approx(-20.0)  # 偏移 20mm

    @pytest.mark.asyncio
    async def test_invalid_dimension_config_raises(self, tmp_path):
        view = _box_view()
        for bad_cfg in ({"dimension_offset": -5}, {"dimension_offset": 0},
                        {"default_tolerance_grade": ""},
                        {"default_tolerance_upper": "big"}):
            ctx = _make_ctx(tmp_path, {"views": [view]},
                            parameters={"dimension_config": bad_cfg})
            with pytest.raises(SWException) as exc_info:
                await DimensionExecutor()(ctx)
            assert exc_info.value.error_code == ErrorCode.GEN_INVALID_FILE

    @pytest.mark.asyncio
    async def test_missing_views_input_raises(self, tmp_path):
        """缺 Step3 输入 → SWException，禁止静默空数据"""
        ctx = _make_ctx(tmp_path, views_result=None)
        with pytest.raises(SWException) as exc_info:
            await DimensionExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    @pytest.mark.asyncio
    async def test_empty_entities_raises(self, tmp_path):
        view = _box_view()
        view["entities"] = []
        ctx = _make_ctx(tmp_path, {"views": [view]})
        with pytest.raises(SWException) as exc_info:
            await DimensionExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    @pytest.mark.asyncio
    async def test_overlap_detection_and_score(self, tmp_path):
        """圆心位于 bbox 下边中点的圆，其直径标注线与宽度标注线共线且区间相交 → 重叠"""
        view = _box_view(min_x=0.0, min_y=0.0, max_x=100.0, max_y=50.0,
                         extra_entities=[{"type": "circle", "cx": 50.0, "cy": -10.0, "r": 30.0}])
        # 宽度标注线 y = -10，区间 [0,100]；直径标注线 y = -10，区间 [20,80] → 重叠
        ctx = _make_ctx(tmp_path, {"views": [view]})
        result = await DimensionExecutor()(ctx)

        assert len(result["overlaps"]) == 1
        ov = result["overlaps"][0]
        assert ov["severity"] == "warning"
        assert len(ov["dim_ids"]) == 2
        total = len(result["dimensions"])
        assert result["placement_score"] == pytest.approx(1.0 - 1 / total, abs=1e-3)

    @pytest.mark.asyncio
    async def test_no_overlap_full_score(self, tmp_path):
        view = _box_view(extra_entities=[{"type": "circle", "cx": 50.0, "cy": 25.0, "r": 5.0}])
        ctx = _make_ctx(tmp_path, {"views": [view]})
        result = await DimensionExecutor()(ctx)
        assert result["overlaps"] == []
        assert result["placement_score"] == 1.0

    @pytest.mark.asyncio
    async def test_placement_score_clamped_to_zero(self, tmp_path):
        """大量重叠（重叠对数 > 标注数）→ placement_score clamp 到 0.0，不出现负分。
        完整避让布局属 M4 AI 范围，M2 只做检测与评分。"""
        # 4 个圆的直径标注线均与宽度标注线共线（y=-10）且区间相交：
        # dims=6（2 外廓 + 4 直径），overlaps ≥ C(4,2)+4 = 10 > 6 → 未 clamp 会为负
        circles = [
            {"type": "circle", "cx": 40.0, "cy": -10.0, "r": 20.0},
            {"type": "circle", "cx": 50.0, "cy": -10.0, "r": 20.0},
            {"type": "circle", "cx": 60.0, "cy": -10.0, "r": 20.0},
            {"type": "circle", "cx": 70.0, "cy": -10.0, "r": 20.0},
        ]
        view = _box_view(min_x=0.0, min_y=0.0, max_x=100.0, max_y=50.0,
                         extra_entities=circles)
        ctx = _make_ctx(tmp_path, {"views": [view]})
        result = await DimensionExecutor()(ctx)

        assert len(result["overlaps"]) > len(result["dimensions"])
        assert result["placement_score"] == 0.0

    @pytest.mark.asyncio
    async def test_multi_view_view_name_populated(self, tmp_path):
        """多视图：每条标注 view_name 指向所属视图"""
        views = [_box_view("front"), _box_view("top", max_x=100.0, max_y=30.0)]
        ctx = _make_ctx(tmp_path, {"views": views})
        result = await DimensionExecutor()(ctx)
        names = [d["view_name"] for d in result["dimensions"]]
        assert names[:2] == ["front", "front"]
        assert names[2:] == ["top", "top"]

    @pytest.mark.asyncio
    async def test_dimensions_json_artifact(self, tmp_path):
        view = _box_view()
        ctx = _make_ctx(tmp_path, {"views": [view]})
        result = await DimensionExecutor()(ctx)

        dims_file = tmp_path / "output" / "dimensions.json"
        assert dims_file.exists()
        on_disk = json.loads(dims_file.read_text(encoding="utf-8"))
        assert on_disk == result
        # 中文/符号字符不转义（ensure_ascii=False）
        raw = dims_file.read_text(encoding="utf-8")
        assert "\\u" not in raw

    @pytest.mark.asyncio
    async def test_load_views_from_checkpoint_file(self, tmp_path):
        """previous_results 缺 Step3 时回退读 output/views.json 检查点"""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True)
        views_data = {"views": [_box_view()], "layout": {}}
        (output_dir / "views.json").write_text(
            json.dumps(views_data, ensure_ascii=False), encoding="utf-8")

        ctx = _make_ctx(tmp_path, views_result=None)
        result = await DimensionExecutor()(ctx)
        assert len(result["dimensions"]) == 2

    @pytest.mark.asyncio
    async def test_multi_view_ids_unique(self, tmp_path):
        views = [_box_view("front"), _box_view("top", max_x=100.0, max_y=30.0)]
        ctx = _make_ctx(tmp_path, {"views": views})
        result = await DimensionExecutor()(ctx)
        ids = [d["id"] for d in result["dimensions"]]
        assert len(ids) == len(set(ids))
        assert len(result["dimensions"]) == 4  # 每视图 2 条外廓标注


class TestPureFunctions:
    def test_extract_view_dimensions_circle_arc(self):
        view = _box_view(extra_entities=[
            {"type": "circle", "cx": 0.0, "cy": 0.0, "r": 8.0},
            {"type": "arc", "cx": 1.0, "cy": 2.0, "r": 3.0,
             "start_angle": 0.0, "end_angle": 180.0},
        ])
        cfg = {"grade": "IT14", "offset": 10.0, "upper": 0.5, "lower": -0.5}
        dims, next_seq = extract_view_dimensions(view, cfg, 1)
        assert next_seq == 1 + len(dims)
        assert [d["type"] for d in dims] == ["linear", "linear", "diameter", "radius"]
        assert dims[2]["associated_entities"] == ["front_e4"]
        assert dims[3]["associated_entities"] == ["front_e5"]

    def test_detect_overlaps_intersecting(self):
        cfg_dims = [
            {"id": "dim_a", "position": {"x1": 0, "y1": -10, "x2": 100, "y2": -10,
                                          "text_x": 50, "text_y": -10}},
            {"id": "dim_b", "position": {"x1": 40, "y1": -10, "x2": 60, "y2": -10,
                                          "text_x": 50, "text_y": -10}},
            {"id": "dim_c", "position": {"x1": -10, "y1": 0, "x2": -10, "y2": 50,
                                          "text_x": -10, "text_y": 25}},
        ]
        overlaps = detect_overlaps({}, cfg_dims)
        assert len(overlaps) == 1
        assert set(overlaps[0]["dim_ids"]) == {"dim_a", "dim_b"}

    def test_detect_overlaps_parallel_lines_no_overlap(self):
        cfg_dims = [
            {"id": "dim_a", "position": {"x1": 0, "y1": -10, "x2": 100, "y2": -10,
                                          "text_x": 50, "text_y": -10}},
            {"id": "dim_b", "position": {"x1": 0, "y1": -25, "x2": 100, "y2": -25,
                                          "text_x": 50, "text_y": -25}},
        ]
        assert detect_overlaps({}, cfg_dims) == []
