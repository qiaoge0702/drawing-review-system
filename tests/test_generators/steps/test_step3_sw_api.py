"""
Step 3 SW 原生导出 DXF 层 - 单元测试（mock COM 边界，不依赖真实 SW）

覆盖 sw_drawing.export_dxf_sync：
- 正常流：OpenDoc6 → GetPartBox → 布局 → NewDocument(图幅模板)
  → CreateDrawViewFromModelView3 × 3 → 设比例/隐藏线可见 → SaveAs DXF
  → CloseAllDocuments
- 失败流：打不开文档 / 工程图创建失败 → GEN_SW_NOT_AVAILABLE；
  视图插入失败 / DXF 导出失败 → GEN_STEP_FAILED
以及 executor 端到端接线（monkeypatch run_sw + fixture DXF → views.json 契约）
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

from tests.test_generators.steps.test_step3_dxf_parse import (
    _build_fixture_dxf, POSITIONS, DEN,
)


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


class FakeExtension:
    def __init__(self, drw):
        self._drw = drw

    def SaveAs(self, path, version, options, export_data, errors, warnings):
        self._drw.saved_as = path
        self._drw.save_options = options
        Path(path).write_text("dxf", encoding="utf-8")
        return True


class FakeDrawing:
    def __init__(self, fail_insert=False, fail_save=False):
        self.insert_calls = []
        self.views = []
        self.saved_as = None
        self.save_options = None
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

    def ForceRebuild3(self, flag):
        return True

    def SaveAs(self, path):  # Extension.SaveAs 回退路径
        if self._fail_save:
            return False
        self.saved_as = path
        Path(path).write_text("dxf", encoding="utf-8")
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


# ---------- export_dxf_sync ----------

class TestExportDxfSync:
    def test_happy_path(self, tmp_path):
        app = FakeSwApp()
        r = sw_drawing.export_dxf_sync(
            "C:/fake/part.sldprt", ["front", "top", "left"],
            str(tmp_path), bom_rows=0, task_id="t1", sw_app=app)
        drw = app._drw
        # 三视图插入，预定义视图名取配置
        assert len(drw.insert_calls) == 3
        names = [c[1] for c in drw.insert_calls]
        assert names == ["*前视", "*上视", "*左视"]
        # 每个视图设了比例与隐藏线可见(3)
        for v in drw.views:
            assert v.scale_decimal is not None
            assert 3 in v.display_modes
        # DXF 静默导出到输出目录，文档已关闭
        assert r["dxf_path"] == str(tmp_path / "raw_export.dxf")
        assert Path(r["dxf_path"]).exists()
        assert drw.saved_as == r["dxf_path"]
        assert app.closed is True
        # 布局结果齐备
        assert r["sheet"] in ("A3", "A2", "A1", "A0")
        assert set(r["positions"].keys()) == {"front", "top", "left"}
        assert r["scale_den"] >= 1

    def test_insert_positions_match_layout(self, tmp_path):
        app = FakeSwApp()
        r = sw_drawing.export_dxf_sync(
            "C:/fake/part.sldprt", ["front", "top", "left"],
            str(tmp_path), sw_app=app)
        for (model, vname, x, y, z), name in zip(
                app._drw.insert_calls, ["front", "top", "left"]):
            p = r["positions"][name]
            # 插入锚点 = 区域中心（米）
            assert x == pytest.approx((p["x"] + p["width"] / 2) / 1000.0)
            assert y == pytest.approx((p["y"] + p["height"] / 2) / 1000.0)

    def test_open_doc_failure(self, tmp_path):
        app = FakeSwApp(open_ok=False)
        with pytest.raises(SWException) as exc_info:
            sw_drawing.export_dxf_sync(
                "C:/fake/part.sldprt", ["front"], str(tmp_path), sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE
        assert app.closed is True

    def test_new_document_failure(self, tmp_path):
        app = FakeSwApp()
        app._drw = None  # NewDocument 返回 None = 工程图创建失败
        with pytest.raises(SWException) as exc_info:
            sw_drawing.export_dxf_sync(
                "C:/fake/part.sldprt", ["front"], str(tmp_path), sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_SW_NOT_AVAILABLE

    def test_insert_view_failure(self, tmp_path):
        app = FakeSwApp(drw=FakeDrawing(fail_insert=True))
        with pytest.raises(SWException) as exc_info:
            sw_drawing.export_dxf_sync(
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
            sw_drawing.export_dxf_sync(
                "C:/fake/part.sldprt", ["front"], str(tmp_path), sw_app=app)
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED


# ---------- executor 端到端接线 ----------

def _make_ctx(tmp_path: Path, source_file: Path) -> StepContext:
    return StepContext(
        task_id="test-step3-export",
        step=3,
        step_name=StepName.VIEW_PROJECT,
        work_dir=tmp_path,
        parameters={"source_file": str(source_file)},
        previous_results={},
    )


class TestExecutorWiring:
    @pytest.mark.asyncio
    async def test_views_json_contract(self, tmp_path, monkeypatch):
        """monkeypatch run_sw（落 fixture DXF）→ executor 组装 views.json 契约"""
        async def fake_run_sw(func, source_file, view_names, output_dir,
                              bom_rows, task_id):
            dxf = Path(output_dir) / "raw_export.dxf"
            _build_fixture_dxf(dxf)
            return {"dxf_path": str(dxf), "sheet": "A3", "scale_den": DEN,
                    "positions": POSITIONS, "warnings": ["fake-com-warning"]}

        monkeypatch.setattr(step3_view_project, "run_sw", fake_run_sw)
        src = tmp_path / "part.sldprt"
        src.write_text("dummy")
        ctx = _make_ctx(tmp_path, src)
        result = await ViewProjectExecutor()(ctx)

        # 契约：views 三视图 + layout（sheet/orientation/view_positions=实际插入位置）
        assert [v["name"] for v in result["views"]] == ["front", "top", "left"]
        assert result["layout"]["sheet_size"] == "A3"
        assert result["layout"]["orientation"] == "landscape"
        assert result["layout"]["view_positions"] == POSITIONS
        front = result["views"][0]
        assert len(front["entities"]) == 4
        assert len(front["hidden_lines"]) == 2
        assert front["scale"] == "1:2"
        assert "fake-com-warning" in result["warnings"]

        # views.json 落盘且字段一致
        saved = json.loads(
            (tmp_path / "output" / "views.json").read_text(encoding="utf-8"))
        assert saved["views"][0].keys() == front.keys()
        assert saved["layout"]["view_positions"] == POSITIONS

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
