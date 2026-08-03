"""
Step 3 SW 原生真图纸层 - 单元测试（mock COM 边界，不依赖真实 SW）

# 方案B重写：export_dxf_sync（DXF 线稿）已删除，改为 create_drawing_sync
覆盖 sw_drawing.create_drawing_sync：
- 正常流：OpenDoc6 → GetPartBox/GetBox → 布局 → NewDocument(企业模板)
  → CreateDrawViewFromModelView3 × 3（中文视图名）→ 设比例/隐藏线可见
  → Extension.SaveAs SLDDRW + PNG 快照 → CloseAllDocuments
- 失败流：打不开文档 / 工程图创建失败 → GEN_SW_NOT_AVAILABLE；
  视图插入失败 / 保存失败 → GEN_STEP_FAILED
以及 executor 端到端接线（monkeypatch run_sw → views.json 契约 + 新增字段）
"""

import json
from pathlib import Path

import pytest

from app.generators import sw_drawing
from app.generators.models import StepContext
from app.generators.steps import step3_view_project
from app.generators.steps.step3_view_project import ViewProjectExecutor
from app.models.generation import StepName
from app.core.exceptions import SWException, ErrorCode


# ---------- Fake COM 对象 ----------

# 视图 1:1 轮廓尺寸（米），与 FakeModelDoc 包围盒一致：front=X×Z, top=X×Y, left=Y×Z
_VIEW_SIZES_M = {"*前视": (0.2, 0.05), "*上视": (0.2, 0.1), "*左视": (0.1, 0.05)}


class FakeView:
    def __init__(self, view_name, cx, cy):
        self.scale_decimal = None
        self.display_modes = []
        self._w, self._h = _VIEW_SIZES_M[view_name]
        self._cx, self._cy = cx, cy

    @property
    def ScaleDecimal(self):
        return self.scale_decimal

    @ScaleDecimal.setter
    def ScaleDecimal(self, v):
        self.scale_decimal = v

    @property
    def Position(self):
        return [self._cx, self._cy]

    @Position.setter
    def Position(self, v):
        vals = getattr(v, "value", v)  # 兼容 VARIANT safearray
        self._cx, self._cy = vals[0], vals[1]

    @property
    def GetOutline(self):
        den = 1.0 / self.scale_decimal if self.scale_decimal else 1.0
        w, h = self._w / den, self._h / den
        return (self._cx - w / 2, self._cy - h / 2,
                self._cx + w / 2, self._cy + h / 2)

    def SetDisplayMode3(self, a, mode, b, c):
        self.display_modes.append(mode)
        return True


class FakeSheet:
    # 模板图纸页 A3 横向（米）
    def GetSize(self, vw, vh):
        return (True, 0.42, 0.297)


class FakeExtension:
    def __init__(self, drw):
        self._drw = drw

    def SaveAs(self, path, version, options, export_data, errors, warnings):
        if self._drw._fail_save:
            return False
        self._drw.saved_as.append(path)
        Path(path).write_text("fake", encoding="utf-8")
        return True


class FakeDrawing:
    def __init__(self, fail_insert=False, fail_save=False):
        self.insert_calls = []
        self.views = []
        self.saved_as = []
        self._fail_insert = fail_insert
        self._fail_save = fail_save
        self.Extension = FakeExtension(self)

    def CreateDrawViewFromModelView3(self, model, view_name, x, y, z):
        if self._fail_insert:
            return None
        self.insert_calls.append((model, view_name, x, y, z))
        v = FakeView(view_name, x, y)
        self.views.append(v)
        return v

    def GetCurrentSheet(self):
        return FakeSheet()

    def ForceRebuild3(self, flag):
        return True

    def SaveAs(self, path):  # Extension.SaveAs 回退路径
        if self._fail_save:
            return False
        self.saved_as.append(path)
        Path(path).write_text("fake", encoding="utf-8")
        return True


class FakeModelDoc:
    # 200(X) × 100(Y) × 50(Z) mm，米制
    def GetPartBox(self, flag):
        return (0.0, 0.0, 0.0, 0.2, 0.1, 0.05)

    def GetBox(self, flag):
        return (0.0, 0.0, 0.0, 0.2, 0.1, 0.05)


class FakeSwApp:
    def __init__(self, drw=None, open_ok=True):
        self._drw = drw if drw is not None else FakeDrawing()
        self._open_ok = open_ok
        self.closed = False
        self.new_doc_templates = []

    def OpenDoc6(self, path, doc_type, opts, cfg, errors, warnings):
        return FakeModelDoc() if self._open_ok else None

    def NewDocument(self, template, paper_size, w, h):
        self.new_doc_templates.append(template)
        return self._drw

    def CloseAllDocuments(self, flag):
        self.closed = True


# ---------- create_drawing_sync ----------

class TestCreateDrawingSync:
    def test_happy_path(self, tmp_path):
        app = FakeSwApp()
        r = sw_drawing.create_drawing_sync(
            "C:/fake/part.sldprt", ["front", "top", "left"],
            str(tmp_path), bom_rows=0, task_id="t1", sw_app=app)
        drw = app._drw
        # 三视图插入，预定义视图名取配置（中文）
        assert len(drw.insert_calls) == 3
        names = [c[1] for c in drw.insert_calls]
        assert names == ["*前视", "*上视", "*左视"]
        # 每个视图设了比例与隐藏线可见(3)
        for v in drw.views:
            assert v.scale_decimal is not None
            assert 3 in v.display_modes
        # 企业模板建图纸（不是按图幅映射的旧 gb_ 模板）
        from app.core.config import get_settings
        assert app.new_doc_templates == [get_settings().sw.enterprise_template]
        # 中间 SLDDRW + PNG 快照已保存，文档已关闭
        assert r["drawing_path"] == str(tmp_path / "drawing.slddrw")
        assert r["snapshot_path"] == str(tmp_path / "snapshot.png")
        assert Path(r["drawing_path"]).exists()
        assert Path(r["snapshot_path"]).exists()
        assert app.closed is True
        # 布局结果齐备（模板 A3 图幅，比例自适应，禁止 1:100 失真）
        assert r["sheet"] == "A3"
        assert set(r["positions"].keys()) == {"front", "top", "left"}
        assert r["scale_den"] >= 1
        assert r["scale_den"] <= 5  # 200×100mm 件在 A3 上不应离谱缩小
        assert set(r["view_sizes"].keys()) == {"front", "top", "left"}

    def test_insert_positions_match_layout(self, tmp_path):
        app = FakeSwApp()
        r = sw_drawing.create_drawing_sync(
            "C:/fake/part.sldprt", ["front", "top", "left"],
            str(tmp_path), sw_app=app)
        # 第一角布局语义：俯视在主视正下方、左视在主视正右方（实测轮廓位置）
        pos = r["positions"]
        assert pos["top"]["y"] + pos["top"]["height"] <= \
            pos["front"]["y"] + 1.0
        assert pos["left"]["x"] >= pos["front"]["x"] + 1.0

    def test_open_doc_failure(self, tmp_path):
        app = FakeSwApp(open_ok=False)
        with pytest.raises(SWException) as exc_info:
            sw_drawing.create_drawing_sync(
                "C:/fake/part.sldprt", ["front"], str(tmp_path), sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE
        assert app.closed is True

    def test_new_document_failure(self, tmp_path):
        app = FakeSwApp()
        app._drw = None  # NewDocument 返回 None = 工程图创建失败
        with pytest.raises(SWException) as exc_info:
            sw_drawing.create_drawing_sync(
                "C:/fake/part.sldprt", ["front"], str(tmp_path), sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE

    def test_insert_view_failure(self, tmp_path):
        app = FakeSwApp(drw=FakeDrawing(fail_insert=True))
        with pytest.raises(SWException) as exc_info:
            sw_drawing.create_drawing_sync(
                "C:/fake/part.sldprt", ["front"], str(tmp_path), sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    def test_save_failure(self, tmp_path):
        drw = FakeDrawing(fail_save=True)

        class FailExt:
            def SaveAs(self, *a):
                raise AttributeError("no extension")

        drw.Extension = FailExt()
        app = FakeSwApp(drw=drw)
        with pytest.raises(SWException) as exc_info:
            sw_drawing.create_drawing_sync(
                "C:/fake/part.sldprt", ["front"], str(tmp_path), sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED


# ---------- finalize_drawing_sync（Step7 COM 层） ----------

class FakeCpm:
    def __init__(self):
        self.props = {}

    def Set2(self, name, value):
        self.props[name] = value
        return True

    def Add3(self, name, ftype, value, overwrite):
        self.props[name] = value
        return True


class FakeFinalizeDrawing(FakeDrawing):
    def __init__(self):
        super().__init__()
        self.cpm = FakeCpm()
        self.Extension.cpm = self.cpm

    @property
    def Extension(self):
        return self._ext

    @Extension.setter
    def Extension(self, v):
        self._ext = v


class _ExtWithCpm:
    def __init__(self, drw):
        self._drw = drw

    def SaveAs(self, path, version, options, export_data, errors, warnings):
        self._drw.saved_as.append(path)
        Path(path).write_text("fake", encoding="utf-8")
        return True

    def CustomPropertyManager(self, config):
        return self._drw.cpm


class FakeMassProp:
    def __init__(self, mass):
        self.Mass = mass


class _ModelExt:
    """模型侧 Extension：CustomPropertyManager + CreateMassProperty"""
    def __init__(self, model):
        self._m = model

    def CustomPropertyManager(self, config):
        return self._m.cpm

    def CreateMassProperty(self):
        return self._m.mass_prop


class FakeModel:
    def __init__(self, mass=0.0):
        self.cpm = FakeCpm()
        self.mass_prop = FakeMassProp(mass)
        self.Extension = _ModelExt(self)


class TestFinalizeDrawingSync:
    def _app(self, tmp_path, model_mass=0.0):
        drw = FakeFinalizeDrawing()
        drw.Extension = _ExtWithCpm(drw)
        model = FakeModel(mass=model_mass)

        class App(FakeSwApp):
            def OpenDoc6(self_, path, doc_type, opts, cfg, errors, warnings):
                assert opts & 1  # Silent 必带
                assert not (opts & 2)  # 可写（要改自定义属性）
                if str(path).lower().endswith(".slddrw"):
                    assert doc_type == 3  # 工程图
                    return drw
                return model  # 模型（$PRPSHEET 数据源）

        return App(drw=drw), drw, model

    def test_happy_path(self, tmp_path):
        app, drw, model = self._app(tmp_path)
        # 缺陷3：中文属性名写模型级（$PRPSHEET 数据源），空值跳过
        props = {"代号": "LB26.00000", "名称": "拉臂总成",
                 "材料": "见明细表", "重量": "12.500", "比例": "1:10",
                 "Empty": ""}
        r = sw_drawing.finalize_drawing_sync(
            "C:/fake/step3/drawing.slddrw", props,
            "C:/fake/LB26.00000拉臂总成.SLDASM", str(tmp_path),
            task_id="t7", sw_app=app)
        # 属性写到模型级，不是图纸级
        assert model.cpm.props == {"代号": "LB26.00000",
                                   "名称": "拉臂总成",
                                   "材料": "见明细表",
                                   "重量": "12.500", "比例": "1:10"}
        assert drw.cpm.props == {}
        assert set(r["properties_applied"]) == set(model.cpm.props)

    def test_weight_fallback_to_model_mass(self, tmp_path):
        """调用方重量留空 → 从模型 MassProperty 实测（kg）回填"""
        app, drw, model = self._app(tmp_path, model_mass=11763.8091)
        props = {"代号": "LB26.00000", "名称": "拉臂总成"}
        r = sw_drawing.finalize_drawing_sync(
            "C:/fake/step3/drawing.slddrw", props,
            "C:/fake/model.SLDASM", str(tmp_path), sw_app=app)
        assert model.cpm.props["质量"] == "11763.809"
        assert "质量" in r["properties_applied"]

    def test_no_model_path_warns(self, tmp_path):
        """缺模型路径 → 跳过模型属性写入 + 如实 warning，不阻断导出"""
        app, drw, model = self._app(tmp_path)
        r = sw_drawing.finalize_drawing_sync(
            "C:/fake/step3/drawing.slddrw", {"代号": "LB26.00000"},
            "", str(tmp_path), sw_app=app)
        assert r["properties_applied"] == []
        assert any("模型路径" in w for w in r["warnings"])
        assert Path(r["slddrw_path"]).exists()
        # 四份产物全部另存
        assert r["slddrw_path"] == str(tmp_path / "drawing.slddrw")
        assert r["dwg_path"] == str(tmp_path / "drawing.dwg")
        assert r["pdf_path"] == str(tmp_path / "drawing.pdf")
        assert r["final_snapshot_path"] == str(tmp_path / "final_snapshot.png")
        for k in ("slddrw_path", "dwg_path", "pdf_path", "final_snapshot_path"):
            assert Path(r[k]).exists()
        assert app.closed is True

    def test_open_failure(self, tmp_path):
        app = FakeSwApp(open_ok=False)
        with pytest.raises(SWException) as exc_info:
            sw_drawing.finalize_drawing_sync(
                "C:/fake/x.slddrw", {}, "C:/fake/m.SLDASM",
                str(tmp_path), sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE


# ---------- executor 端到端接线 ----------

def _make_ctx(tmp_path: Path, source_file: Path) -> StepContext:
    return StepContext(
        task_id="test-step3-drawing",
        step=3,
        step_name=StepName.VIEW_PROJECT,
        work_dir=tmp_path,
        parameters={"source_file": str(source_file)},
        previous_results={},
    )


def _fake_sw_result(tmp_path: Path) -> dict:
    positions = {
        "front": {"x": 20.0, "y": 200.0, "width": 100.0, "height": 25.0},
        "top": {"x": 20.0, "y": 130.0, "width": 100.0, "height": 50.0},
        "left": {"x": 160.0, "y": 200.0, "width": 50.0, "height": 25.0},
    }
    return {"drawing_path": str(tmp_path / "drawing.slddrw"),
            "snapshot_path": str(tmp_path / "snapshot.png"),
            "sheet": "A3", "sheet_width": 420.0, "sheet_height": 297.0,
            "scale_den": 2.0, "positions": positions,
            "view_sizes": {"front": {"width": 200.0, "height": 50.0},
                           "top": {"width": 200.0, "height": 100.0},
                           "left": {"width": 100.0, "height": 50.0}},
            "warnings": ["fake-com-warning"]}


class TestExecutorWiring:
    @pytest.mark.asyncio
    async def test_views_json_contract(self, tmp_path, monkeypatch):
        """monkeypatch run_sw → executor 组装 views.json 契约（只加不改）"""
        async def fake_run_sw(func, source_file, view_names, output_dir,
                              bom_rows, task_id, sw_app=None, use_b_m1=True):
            return _fake_sw_result(Path(output_dir))

        monkeypatch.setattr(step3_view_project, "run_sw", fake_run_sw)
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)
        result = await ViewProjectExecutor()(ctx)

        # 契约：views 三视图 + layout（sheet/orientation/view_positions=实际位置）
        assert [v["name"] for v in result["views"]] == ["front", "top", "left"]
        assert result["layout"]["sheet_size"] == "A3"
        assert result["layout"]["orientation"] == "landscape"
        front = result["views"][0]
        # 方案B：拆线 entities 如实为空，scale/bounding_box 契约字段保留
        assert front["entities"] == []
        assert front["hidden_lines"] == []
        assert front["scale"] == "1:2"
        assert front["bounding_box"]["max_x"] == 200.0
        # 新增字段
        assert result["drawing_path"].endswith("drawing.slddrw")
        assert result["snapshot_path"].endswith("snapshot.png")
        assert result["scale_denominator"] == 2.0
        assert result["sheet_size"] == "A3"
        assert "fake-com-warning" in result["warnings"]

        # views.json 落盘且字段一致
        saved = json.loads(
            (tmp_path / "output" / "views.json").read_text(encoding="utf-8"))
        assert saved["views"][0].keys() == front.keys()
        assert saved["layout"]["view_positions"] == \
            result["layout"]["view_positions"]
        assert saved["drawing_path"] == result["drawing_path"]

    @pytest.mark.asyncio
    async def test_sw_failure_no_artifact(self, tmp_path, monkeypatch):
        async def failing_run_sw(func, *args):
            raise SWException("SW unavailable",
                              error_code=ErrorCode.GEN_SW_NOT_AVAILABLE)
        monkeypatch.setattr(step3_view_project, "run_sw", failing_run_sw)
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        with pytest.raises(SWException):
            await ViewProjectExecutor()(_make_ctx(tmp_path, src))
        assert not (tmp_path / "output" / "views.json").exists()
