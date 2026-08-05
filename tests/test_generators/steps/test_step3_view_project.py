"""
Step 3 视图投影执行器单元测试（STL/trimesh 回退路径已删除，仅保留引擎无关用例）
"""

from pathlib import Path

import pytest

from app.generators.models import StepContext
from app.generators.steps import step3_view_project
from app.generators.steps.step3_view_project import (
    ViewProjectExecutor,
    FirstAngleLayoutEngine,
    measure_title_block_rect,
)
from app.generators.view_strategy import (
    get_view_strategy,
    SHEET_A3_WIDTH,
    SHEET_A3_HEIGHT,
)
from app.generators.type_recognition import PartType
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


class TestViewProjectExecutor:
    @pytest.mark.asyncio
    async def test_sw_unavailable_raises(self, tmp_path, monkeypatch):
        """SW 调用失败必须抛 GEN_SW_NOT_AVAILABLE，禁止静默空数据"""
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
    async def test_unsupported_engine_rejected(self, tmp_path):
        """engine 参数仅支持 sw_api；stl/auto 已删除"""
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        for bad in ("stl", "auto"):
            ctx = _make_ctx(tmp_path, src)
            ctx.parameters["engine"] = bad
            with pytest.raises(SWException) as exc_info:
                await ViewProjectExecutor()(ctx)
            assert exc_info.value.error_code == ErrorCode.GEN_UNSUPPORTED_FEATURE

    @pytest.mark.asyncio
    async def test_missing_source_file(self, tmp_path):
        ctx = _make_ctx(tmp_path, tmp_path / "nonexistent.sldprt")
        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_INVALID_FILE

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


# ---------- 布局引擎实测路径测试 ----------

class _FakeDrawing:
    def ForceRebuild3(self, flag):
        return True


class _FakeView:
    """Position 中心与 GetOutline 中心一致的假视图"""

    def __init__(self, name: str, w_mm: float, h_mm: float, scale_den: float = 1.0):
        self.name = name
        self._w_mm = w_mm
        self._h_mm = h_mm
        self._cx = 0.0
        self._cy = 0.0
        self.scale_decimal = 1.0 / scale_den

    @property
    def Position(self):
        return [self._cx, self._cy]

    @Position.setter
    def Position(self, v):
        vals = getattr(v, "value", v)
        self._cx, self._cy = vals[0], vals[1]

    @property
    def ScaleDecimal(self):
        return self.scale_decimal

    @ScaleDecimal.setter
    def ScaleDecimal(self, v):
        self.scale_decimal = v

    @property
    def GetOutline(self):
        den = 1.0 / self.scale_decimal if self.scale_decimal else 1.0
        w_m = (self._w_mm / 1000.0) / den
        h_m = (self._h_mm / 1000.0) / den
        return (
            self._cx - w_m / 2,
            self._cy - h_m / 2,
            self._cx + w_m / 2,
            self._cy + h_m / 2,
        )


class _FixedOutlineView:
    """GetOutline 返回固定轮廓，用于模拟锚点偏差或不可调重叠"""

    def __init__(self, outline_m: tuple, scale_decimal: float = 1.0):
        self.outline = outline_m
        self._pos = [0.0, 0.0]
        self.scale_decimal = scale_decimal

    @property
    def Position(self):
        return self._pos[:]

    @Position.setter
    def Position(self, v):
        vals = getattr(v, "value", v)
        self._pos = [vals[0], vals[1]]

    @property
    def ScaleDecimal(self):
        return self.scale_decimal

    @ScaleDecimal.setter
    def ScaleDecimal(self, v):
        self.scale_decimal = v

    @property
    def GetOutline(self):
        return self.outline


class TestFirstAngleLayoutEngineMeasured:
    def test_isometric_placed_above_title_block(self):
        """mock GetOutline 返回固定轮廓，轴测图应摆在标题栏上方右侧区域"""
        title_block = (250.0, 0.0, 170.0, 50.0)  # 右下角标题栏
        engine = FirstAngleLayoutEngine(
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            title_block_bbox=title_block,
        )
        view_sizes = {
            "front": (200.0, 150.0),
            "right": (100.0, 150.0),
            "top": (200.0, 100.0),
            "isometric": (150.0, 120.0),
        }
        strategy = get_view_strategy(PartType.BEAM)
        view_objects = {
            name: _FakeView(name, w, h, scale_den=2.0)
            for name, (w, h) in view_sizes.items()
        }

        positions = engine.layout(
            view_sizes,
            2.0,
            strategy,
            view_objects=view_objects,
            drawing=_FakeDrawing(),
        )

        assert positions is not None
        iso = positions["isometric"]
        # 轴测图在标题栏上方（底边不低于标题栏顶）
        assert iso["y"] >= title_block[1] + title_block[3]
        # 不出图框
        assert iso["x"] >= 0
        assert iso["y"] >= 0
        assert iso["x"] + iso["width"] <= SHEET_A3_WIDTH
        assert iso["y"] + iso["height"] <= SHEET_A3_HEIGHT
        # 所有视图均不压标题栏
        for name, pos in positions.items():
            assert not engine._rect_intersects_title_block(pos), f"{name} 压标题栏"

    def test_layout_raises_when_title_block_overlap_persists(self):
        """实测轮廓持续压标题栏且超 2 次重排失败 → 报错不静默"""
        title_block = (250.0, 0.0, 170.0, 50.0)
        engine = FirstAngleLayoutEngine(
            SHEET_A3_WIDTH,
            SHEET_A3_HEIGHT,
            title_block_bbox=title_block,
        )
        view_sizes = {
            "front": (200.0, 150.0),
            "right": (100.0, 150.0),
            "top": (200.0, 100.0),
            "isometric": (150.0, 120.0),
        }
        strategy = get_view_strategy(PartType.BEAM)
        view_objects = {
            name: _FakeView(name, w, h, scale_den=2.0)
            for name, (w, h) in view_sizes.items()
        }
        # 把轴测图轮廓固定到标题栏内部，模拟无法通过平移/降比例摆脱
        view_objects["isometric"] = _FixedOutlineView(
            (0.300, 0.010, 0.390, 0.050)  # 300,10 → 390,50 mm，压标题栏
        )

        with pytest.raises(SWException) as exc_info:
            engine.layout(
                view_sizes,
                2.0,
                strategy,
                view_objects=view_objects,
                drawing=_FakeDrawing(),
            )
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    def test_measure_title_block_rect_from_drawing(self):
        """从 drawing.TitleBlock.GetBoundingBox 实测并转换为 mm"""
        tb = type("TitleBlock", (), {})()

        def get_box():
            return (0.250, 0.0, 0.420, 0.050)

        tb.GetBoundingBox = get_box
        drawing = type("Drawing", (), {"TitleBlock": tb})()
        rect = measure_title_block_rect(drawing, 420.0, 297.0)
        assert rect == (250.0, 0.0, 170.0, 50.0)

    def test_measure_title_block_rect_fallback(self):
        """GetBoundingBox 失败时使用保底 60mm"""
        drawing = type("Drawing", (), {})()
        rect = measure_title_block_rect(drawing, 420.0, 297.0)
        assert rect == (0.0, 0.0, 420.0, 60.0)
