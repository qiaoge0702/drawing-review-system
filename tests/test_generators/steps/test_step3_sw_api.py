"""
Step 3 SW API 版视图投影 - 单元测试（mock COM 边界，不依赖真实 SW）

Fake 对象模拟侦察报告验证过的 COM 鸭子类型：
- curve.Identity / IsCircle 为属性（非方法）
- edge.GetCurve / edge.GetCurveParams3 为属性
- curve.Evaluate(u) 为方法（样条采样）
- view.GetVisibleEntities2(comp, type_code) 为方法（仅类型码 1=Edge；隐藏线由
  显示模式差集提取：线框(1)全集 − HLR(2)可见集）
- view.SetDisplayMode4 / GetDisplayMode3 驱动显示模式切换
- ModelToViewTransform.ArrayData 为 16 维矩阵（索引 12 为缩放）
"""

import json
from pathlib import Path

import pytest

from app.generators import sw_drawing, view_extractor
from app.generators.models import StepContext
from app.generators.steps import step3_view_project
from app.generators.steps.step3_view_project import ViewProjectExecutor
from app.generators.sw_drawing import extract_views_sync
from app.generators.view_extractor import (
    apply_xform, edge_to_entities, extract_view_entities, bounding_box_of,
)
from app.models.generation import StepName
from app.core.exceptions import SWException, ErrorCode

# 前视矩阵（SW 官方 ArrayData 布局）：旋转[0-8]=(x,y,z)→(x,z)，平移[9-11]=0，比例[12]=1
FRONT_ARR = [1, 0, 0,
             0, 0, 1,
             0, -1, 0,
             0, 0, 0,
             1, 0, 0, 0]


# ---------- Fake COM 对象 ----------

class FakeCurve:
    def __init__(self, identity, line_params=None, circle_params=None, eval_fn=None):
        self._id = identity
        self._line = line_params
        self._circle = circle_params
        self._eval_fn = eval_fn

    @property
    def Identity(self):
        return self._id

    @property
    def LineParams(self):
        if self._line is None:
            raise AttributeError("no line params")
        return self._line

    @property
    def CircleParams(self):
        if self._circle is None:
            raise AttributeError("no circle params")
        return self._circle

    def Evaluate(self, u):
        return self._eval_fn(u)


class FakeEdge:
    def __init__(self, curve, params3=None):
        self._curve = curve
        self._params3 = params3

    @property
    def GetCurve(self):
        return self._curve

    @property
    def GetCurveParams3(self):
        if self._params3 is None:
            raise AttributeError("no params3")
        return self._params3


class FakeXform:
    def __init__(self, arr):
        self.ArrayData = arr


class FakeView:
    def __init__(self, edges_by_mode, arr=FRONT_ARR, scale=1.0, initial_mode=2,
                 set_display_ok=True):
        # edges_by_mode: {display_mode: [edges]} 或 {(id(comp), display_mode): [edges]}
        # display_mode: 1=线框（Edge 全集，含隐藏边），2=HLR（仅可见边）
        self._edges_by_mode = edges_by_mode
        self._arr = arr
        self.ScaleDecimal = scale
        self._mode = initial_mode
        self._set_ok = set_display_ok
        self.display_mode_calls = 0

    @property
    def GetVisibleComponents(self):
        return self._comps

    def set_components(self, comps):
        self._comps = comps

    def GetVisibleEntities2(self, comp, code):
        if code != 1:  # 只读 Edge（swViewEntityType_e: Edge=1）
            return []
        return self._edges_by_mode.get((id(comp), self._mode),
                                       self._edges_by_mode.get(self._mode, []))

    @property
    def ModelToViewTransform(self):
        return FakeXform(self._arr)

    def GetDisplayMode3(self, use_parent):
        return self._mode

    def SetDisplayMode4(self, use_parent, mode, facetted, edges):
        self.display_mode_calls += 1
        if not self._set_ok:
            raise RuntimeError("SetDisplayMode4 not supported")
        self._mode = mode
        return True

    def SetDisplayMode3(self, *args):
        return self.SetDisplayMode4(*args)


class FakeDrawing:
    def __init__(self, views):
        self._views = views  # 按插入顺序返回
        self._idx = 0
        self.rebuilt = 0

    def CreateDrawViewFromModelView3(self, src, view_name, x, y, scale):
        v = self._views[self._idx]
        self._idx += 1
        if v is not None:
            v.inserted_view_name = view_name
        return v

    def ForceRebuild3(self, flag):
        self.rebuilt += 1
        return True

    def ResolveAllLightWeightComponents(self, flag):
        return True


class FakeSWApp:
    def __init__(self, drawing, doc=object()):
        self._drawing = drawing
        self._doc = doc
        self.closed = False

    def OpenDoc6(self, path, dtype, opts, cfg, errors, warnings):
        return self._doc

    def NewDocument(self, template, paper, w, h):
        return self._drawing

    def CloseAllDocuments(self, flag):
        self.closed = True


def _line_edge(p1, p2):
    """p1/p2 为三维点（米）"""
    curve = FakeCurve(3001, line_params=list(p1) + list(p2))
    params3 = list(p1) + list(p2) + [0.0, 1.0, 1]
    return FakeEdge(curve, params3)


def _circle_edge(center, radius, start=None, end=None):
    curve = FakeCurve(3002, circle_params=list(center) + [0, 0, 1, radius])
    s = start if start is not None else list(center)
    e = end if end is not None else list(center)
    params3 = s + e + [0.0, 6.283, 1]
    return FakeEdge(curve, params3)


def _spline_edge():
    import math
    curve = FakeCurve(3004, eval_fn=lambda u: [u, math.sin(u), 0.0])
    return FakeEdge(curve, [0.0, 0.0, 0.0, 1.0, 0.8415, 0.0, 0.0, 1.0, 1])


def _make_ctx(tmp_path: Path, params: dict) -> StepContext:
    return StepContext(
        task_id="test-step3-sw", step=3, step_name=StepName.VIEW_PROJECT,
        work_dir=tmp_path, parameters=params, previous_results={},
    )


def _run_direct(fake_app):
    """fake run_sw：直接同步调用，并注入 sw_app"""
    async def fake_run_sw(func, *args):
        if func is sw_drawing.extract_views_sync:
            return func(args[0], args[1], sw_app=fake_app)
        return func(*args)
    return fake_run_sw


# ---------- 矩阵变换 ----------

class TestApplyXform:
    def test_identity_rotation(self):
        # SW 官方布局：旋转=[0-8]，平移=[9-11]（图纸放置，弃用），比例=[12]（弃用）
        arr = [1, 0, 0,
               0, 1, 0,
               0, 0, 1,
               10, 20, 30,   # 图纸放置平移（米），不进入实体坐标
               1, 0, 0, 0]
        assert apply_xform(arr, 1.0, 2.0, 3.0) == (1.0, 2.0)

    def test_front_view_maps_xz(self):
        assert apply_xform(FRONT_ARR, 1.0, 2.0, 3.0) == (1.0, 3.0)

    def test_scale_element_ignored(self):
        """真机根因回归：视图比例（arr[12]，如模板 1:50 → 0.02）不得进入实体坐标"""
        arr = [1, 0, 0,
               0, 1, 0,
               0, 0, 1,
               0.1476, 0.1510, 0.065,  # 真实图纸放置平移
               0.02, 0.0, 0.0, 0.0]     # 模板 1:50 视图比例
        # 2m 模型点 → 仍是 2m（实际尺寸），不放大 50 倍、不含放置平移
        assert apply_xform(arr, 2.0, 0.0, 0.0) == (2.0, 0.0)


# ---------- 实体映射 ----------

class TestEdgeToEntities:
    def test_line_maps_to_contract_mm(self):
        edge = _line_edge((0.0, 0.0, 0.0), (0.1, 0.0, 0.05))
        ents, note = edge_to_entities(edge, FRONT_ARR)
        assert note is None
        assert ents == [{"type": "line", "x1": 0.0, "y1": 0.0, "x2": 100.0, "y2": 50.0}]

    def test_full_circle_maps_to_contract(self):
        edge = _circle_edge((0.01, 0.0, 0.02), 0.005)  # 起终点相同 → 整圆
        ents, note = edge_to_entities(edge, FRONT_ARR)
        assert note is None
        assert ents == [{"type": "circle", "cx": 10.0, "cy": 20.0, "r": 5.0}]

    def test_arc_when_endpoints_differ(self):
        center = (0.0, 0.0, 0.0)
        start = [0.01, 0.0, 0.0]   # 视图(x,z) → 角度 0°
        end = [0.0, 0.0, 0.01]     # → 角度 90°
        edge = _circle_edge(center, 0.01, start=start, end=end)
        ents, note = edge_to_entities(edge, FRONT_ARR)
        assert note is None
        assert len(ents) == 1
        arc = ents[0]
        assert arc["type"] == "arc"
        assert arc["r"] == pytest.approx(10.0)
        assert arc["start_angle"] == pytest.approx(0.0)
        assert arc["end_angle"] == pytest.approx(90.0)

    def test_circle_radius_actual_mm_regardless_of_view_scale(self):
        """真机根因回归：半径 = 实际尺寸 mm，不乘视图比例（scale_decimal 已移除）"""
        edge = _circle_edge((0.0, 0.0, 0.0), 0.01)
        ents, _ = edge_to_entities(edge, FRONT_ARR)
        assert ents[0]["r"] == pytest.approx(10.0)

    def test_spline_discretized_to_polyline(self):
        edge = _spline_edge()
        ents, note = edge_to_entities(edge, FRONT_ARR, spline_samples=10)
        assert len(ents) == 10
        assert all(e["type"] == "line" for e in ents)
        assert "INTERSECTION" in note
        # 端点衔接：前段终点 == 后段起点
        for a, b in zip(ents, ents[1:]):
            assert (a["x2"], a["y2"]) == (b["x1"], b["y1"])

    def test_unextractable_edge_reports_note(self):
        class BadCurve:
            @property
            def Identity(self):
                raise RuntimeError("COM dead")
        edge = FakeEdge(BadCurve())
        ents, note = edge_to_entities(edge, FRONT_ARR)
        assert ents == []
        assert note is not None


class TestExtractViewEntities:
    def test_multi_component_assembly_merged(self):
        comp1_edges = [_line_edge((0, 0, 0), (0.1, 0, 0))]
        comp2_edges = [_circle_edge((0.05, 0, 0.05), 0.01)]
        ents, notes = extract_view_entities([comp1_edges, comp2_edges], FRONT_ARR)
        types = sorted(e["type"] for e in ents)
        assert types == ["circle", "line"]

    def test_skipped_edges_recorded_in_notes(self):
        edge = _spline_edge()
        edge._params3 = None  # 采样必需参数缺失 → 提取失败
        ents, notes = extract_view_entities([[edge]], FRONT_ARR)
        assert ents == []
        assert notes and "comp0" in notes[0]


class TestBoundingBox:
    def test_includes_circle_extents(self):
        ents = [
            {"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
            {"type": "circle", "cx": 50, "cy": 50, "r": 10},
        ]
        bb = bounding_box_of(ents)
        assert bb == {"min_x": 0, "min_y": 0, "max_x": 60, "max_y": 60}


# ---------- 工程图提取全流程（Fake SW App） ----------

def _build_fake_app(edges_by_mode, views_n=1, set_display_ok=True):
    views = []
    for _ in range(views_n):
        v = FakeView(edges_by_mode, set_display_ok=set_display_ok)
        v.set_components(["comp0"])
        views.append(v)
    drw = FakeDrawing(views)
    return FakeSWApp(drw), drw, views


class TestExtractViewsSync:
    def test_contract_view_structure(self, tmp_path):
        edges = [_line_edge((0, 0, 0), (0.1, 0, 0.05)),
                 _circle_edge((0.05, 0, 0.025), 0.005)]
        hidden = _line_edge((0, 0, 0.01), (0.1, 0, 0.01))
        # 线框(1)=可见+隐藏全集；HLR(2)=仅可见边 → 差集 1 条隐藏线
        app, drw, views = _build_fake_app({1: edges + [hidden], 2: edges})
        result = extract_views_sync(str(tmp_path / "p.sldprt"), ["front"], sw_app=app)

        view = result["views"][0]
        assert view["name"] == "front"
        assert view["display_name"] == "主视图"
        assert view["projection"] == "first_angle"
        assert {e["type"] for e in view["entities"]} == {"line", "circle"}
        assert len(view["hidden_lines"]) == 1, "线框−HLR 差集应得 1 条隐藏线"
        assert view["hidden_lines"][0]["type"] == "line"
        # 成功提取后不得再写"取不到"类 warning
        assert not any("hidden_lines 取不到" in w or "hidden_lines 提取失败" in w
                       for w in result["warnings"])
        assert set(view["bounding_box"]) == {"min_x", "min_y", "max_x", "max_y"}
        assert view["scale"] == "1:1"
        # 中文版预定义视图名
        assert views[0].inserted_view_name == "*前视"
        assert drw.rebuilt >= 1
        # 显示模式被切换且恢复到初始模式（HLR=2）
        assert views[0].display_mode_calls >= 2
        assert views[0].GetDisplayMode3(True) == 2
        assert app.closed is False  # 注入 app 时由调用方负责关闭

    def test_assembly_multi_component(self, tmp_path):
        comp_a, comp_b = "compA", "compB"
        edges_by_mode = {
            (id(comp_a), 1): [_line_edge((0, 0, 0), (0.1, 0, 0))],
            (id(comp_a), 2): [_line_edge((0, 0, 0), (0.1, 0, 0))],
            (id(comp_b), 1): [_circle_edge((0, 0, 0), 0.01)],
            (id(comp_b), 2): [_circle_edge((0, 0, 0), 0.01)],
        }
        app, _, _ = _build_fake_app(edges_by_mode)
        # 替换为多组件
        drw = app._drawing
        drw._views[0].set_components([comp_a, comp_b])
        result = extract_views_sync(str(tmp_path / "a.sldasm"), ["front"], sw_app=app)
        types = sorted(e["type"] for e in result["views"][0]["entities"])
        assert types == ["circle", "line"]

    def test_hidden_lines_unavailable_reported_not_silent(self, tmp_path):
        """显示模式切换不可用 → 隐藏线提取失败：hidden_lines 为空 + warnings 如实记录"""
        edges = [_line_edge((0, 0, 0), (0.1, 0, 0))]
        app, _, views = _build_fake_app({1: edges, 2: edges}, set_display_ok=False)
        result = extract_views_sync(str(tmp_path / "p.sldprt"), ["front"], sw_app=app)

        assert result["views"][0]["hidden_lines"] == []
        assert any("hidden_lines 提取失败" in w for w in result["warnings"])
        assert views[0].display_mode_calls >= 1  # 确实尝试过显示模式切换

    def test_view_insert_failure_raises(self, tmp_path):
        drw = FakeDrawing([None])
        app = FakeSWApp(drw)
        with pytest.raises(SWException) as exc_info:
            extract_views_sync(str(tmp_path / "p.sldprt"), ["front"], sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    def test_no_entities_raises(self, tmp_path):
        app, _, _ = _build_fake_app({1: [], 2: []})
        with pytest.raises(SWException) as exc_info:
            extract_views_sync(str(tmp_path / "p.sldprt"), ["front"], sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED


# ---------- 执行器集成（SW 路径） ----------

class TestExecutorSWPath:
    @pytest.mark.asyncio
    async def test_sw_api_end_to_end(self, tmp_path, monkeypatch):
        edges = [_line_edge((0, 0, 0), (0.1, 0, 0.05)),
                 _circle_edge((0.05, 0, 0.025), 0.005)]
        hidden = _line_edge((0, 0, 0.02), (0.05, 0, 0.02))
        app, _, _ = _build_fake_app({1: edges + [hidden], 2: edges})
        monkeypatch.setattr(step3_view_project, "run_sw", _run_direct(app))

        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, {"source_file": str(src), "views": ["front"]})

        result = await ViewProjectExecutor()(ctx)

        view = result["views"][0]
        assert view["name"] == "front"
        assert any(e["type"] == "circle" for e in view["entities"])
        assert len(view["hidden_lines"]) == 1, "线框−HLR 差集应得 1 条隐藏线"
        assert set(result["layout"]["view_positions"]) == {"front"}
        # 落盘一致
        on_disk = json.loads((tmp_path / "output" / "views.json").read_text(encoding="utf-8"))
        assert on_disk == result

    @pytest.mark.asyncio
    async def test_sw_api_engine_propagates_unavailable(self, tmp_path, monkeypatch):
        """engine=sw_api 时 SW 不可用直接上抛，不回退"""
        async def failing_run_sw(func, *args):
            raise SWException("no SW", error_code=ErrorCode.GEN_SW_NOT_AVAILABLE)
        monkeypatch.setattr(step3_view_project, "run_sw", failing_run_sw)

        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, {"source_file": str(src), "engine": "sw_api"})
        with pytest.raises(SWException) as exc_info:
            await ViewProjectExecutor()(ctx)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE

    @pytest.mark.asyncio
    async def test_auto_falls_back_to_stl(self, tmp_path, monkeypatch):
        """auto 模式：SW 不可用 → 回退 STL 路径产出契约"""
        import trimesh
        box = trimesh.creation.box(extents=[10.0, 20.0, 30.0])

        async def fake_run_sw(func, *args):
            if func is sw_drawing.extract_views_sync:
                raise SWException("no SW", error_code=ErrorCode.GEN_SW_NOT_AVAILABLE)
            Path(args[1]).write_text("solid fake")
            return args[1]
        monkeypatch.setattr(step3_view_project, "run_sw", fake_run_sw)
        monkeypatch.setattr(trimesh, "load", lambda *a, **k: box)

        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, {"source_file": str(src), "views": ["front"]})

        result = await ViewProjectExecutor()(ctx)
        assert [v["name"] for v in result["views"]] == ["front"]
        assert all(e["type"] == "line" for e in result["views"][0]["entities"])
        assert (tmp_path / "output" / "views.json").exists()

    @pytest.mark.asyncio
    async def test_hidden_lines_warning_in_executor_result(self, tmp_path, monkeypatch):
        """执行器结果如实携带隐藏线提取失败的 warning"""
        edges = [_line_edge((0, 0, 0), (0.1, 0, 0))]
        app, _, _ = _build_fake_app({1: edges, 2: edges}, set_display_ok=False)
        monkeypatch.setattr(step3_view_project, "run_sw", _run_direct(app))

        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, {"source_file": str(src), "views": ["front"]})

        result = await ViewProjectExecutor()(ctx)
        assert result["views"][0]["hidden_lines"] == []
        assert any("hidden_lines 提取失败" in w for w in result.get("warnings", []))
