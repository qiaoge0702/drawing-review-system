"""
B-M1 图纸布局逃生口修复单元测试

覆盖：
1. 5b 实测重排失败时按 GB 比例降档重试，最终保留有效 positions
2. 轴测图与俯视图包围盒相交时自动改放右下角/降档规避
3. SW 预定义视图名可通过 settings.sw.predefined_view_names 配置

全部基于 mock COM，不依赖真实 SolidWorks。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.generators import sw_drawing
from app.generators.view_strategy import ViewName, get_sw_view_name


# ---------- Fake COM 对象（针对本修复场景定制） ----------

class FakeView:
    def __init__(self, view_name: str, w_m: float, h_m: float, cx: float, cy: float):
        self.view_name = view_name
        self.scale_decimal = None
        self._w, self._h = w_m, h_m
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
        vals = getattr(v, "value", v)
        self._cx, self._cy = vals[0], vals[1]

    @property
    def GetOutline(self):
        den = 1.0 / self.scale_decimal if self.scale_decimal else 1.0
        w, h = self._w / den, self._h / den
        return (
            self._cx - w / 2,
            self._cy - h / 2,
            self._cx + w / 2,
            self._cy + h / 2,
        )

    def SetDisplayMode3(self, a, mode, b, c):
        return True


class FakeSheet:
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
        self.insert_calls.append((model, view_name, x, y))
        # 默认 200×100×50 mm 零件在 1:1 下的轮廓（米）
        size_map = {
            "*前视": (0.2, 0.05),
            "*上视": (0.2, 0.1),
            "*左视": (0.1, 0.05),
        }
        w, h = size_map.get(view_name, (0.05, 0.05))
        v = FakeView(view_name, w, h, x, y)
        self.views.append(v)
        return v

    def GetCurrentSheet(self):
        return FakeSheet()

    def ForceRebuild3(self, flag):
        return True

    def SaveAs(self, path):
        if self._fail_save:
            return False
        self.saved_as.append(path)
        Path(path).write_text("fake", encoding="utf-8")
        return True


class FakeModelDoc:
    # 200×100×50 mm（米），默认回退为板类策略：front/top/left
    def GetPartBox(self, flag):
        return (0.0, 0.0, 0.0, 0.2, 0.1, 0.05)

    def GetBox(self, flag):
        return self.GetPartBox(flag)


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


# ---------- 修复①：5b 重排失败降档成功 ----------

class TestMeasuredLayoutFallback:
    def test_fallback_to_smaller_scale_on_layout_failure(self, tmp_path):
        """
        模拟 _first_angle_layout_b_m1 在 5b 首次重排时返回 None，
        验证 create_drawing_sync 会按 GB 比例序列降档，最终保留有效 positions。
        """
        real_layout = sw_drawing._first_angle_layout_b_m1
        real_pick = sw_drawing._pick_scale_measured_impl
        state = {"ready": False, "after_failures": 0}

        def pick_side(*args, **kwargs):
            result = real_pick(*args, **kwargs)
            state["ready"] = True
            return result

        def layout_side(*args, **kwargs):
            if state["ready"]:
                state["after_failures"] += 1
                if state["after_failures"] == 1:
                    return None
            return real_layout(*args, **kwargs)

        with patch.object(sw_drawing, "_pick_scale_measured_impl", side_effect=pick_side):
            with patch.object(sw_drawing, "_first_angle_layout_b_m1", side_effect=layout_side):
                app = FakeSwApp()
                r = sw_drawing.create_drawing_sync(
                    "C:/fake/part.sldprt",
                    None,
                    str(tmp_path),
                    task_id="t_fallback",
                    sw_app=app,
                )

        assert r["positions"]
        assert set(r["positions"].keys()) == {"front", "top", "left"}
        # 初始 pick 选 1:1，首次重排失败后应降到 1:2 成功
        assert r["scale_den"] == 2.0
        assert state["after_failures"] >= 1


# ---------- 修复②：轴测/俯视争位规避 ----------

class TestIsometricTopCollisionAvoidance:
    def test_iso_moves_to_bottom_right_on_top_collision(self):
        """
        轴测图左下角与俯视包围盒相交时，应改放右下角；
        仍相交则降一档比例，且最终不与俯视相交。
        """
        # 构造一个俯视图压得很低、轴测图很高的场景
        view_sizes = {
            "front": (200.0, 80.0),
            "right": (40.0, 80.0),
            "top": (200.0, 120.0),
            "isometric": (100.0, 180.0),
        }
        positions = sw_drawing._first_angle_layout_b_m1(
            view_sizes, 1.0, 420.0, 297.0, 25.0
        )
        assert positions is not None
        iso = positions["isometric"]
        top = positions["top"]
        # 轴测图应位于右下角（x 接近右边界）
        assert iso["x"] >= 420.0 - 20.0 - iso["width"]
        # 最终不应与俯视相交
        assert not sw_drawing._rects_intersect(iso, top)

    def test_iso_scale_down_when_both_corners_collide(self):
        """
        左下、右下角均与俯视相交时，轴测图应降一档比例。
        """
        view_sizes = {
            "front": (360.0, 40.0),
            "top": (360.0, 100.0),   # 俯视够宽够低，左右下角都挡
            "isometric": (80.0, 150.0),
        }
        positions = sw_drawing._first_angle_layout_b_m1(
            view_sizes, 1.0, 420.0, 297.0, 20.0
        )
        assert positions is not None
        iso = positions["isometric"]
        top = positions["top"]
        # 降档后轴测图尺寸应小于原始 80×150
        assert iso["width"] < 80.0
        assert iso["height"] < 150.0
        assert not sw_drawing._rects_intersect(iso, top)


# ---------- 修复③：SW 预定义视图名可配置 ----------

class TestConfigurablePredefinedViewNames:
    def test_default_names_unchanged(self):
        assert get_sw_view_name(ViewName.FRONT, 0) == "*前视"
        assert get_sw_view_name(ViewName.FRONT, 1) == "*Front"
        assert get_sw_view_name(ViewName.ISOMETRIC, 0) == "*等轴测"

    def test_settings_override_prepends_config_candidates(self):
        mock_settings = MagicMock()
        mock_settings.sw.predefined_view_names = {
            "front": "*Front,*正视",
            "isometric": "*Iso",
        }
        with patch("app.generators.view_strategy.get_settings", return_value=mock_settings):
            assert get_sw_view_name(ViewName.FRONT, 0) == "*Front"
            assert get_sw_view_name(ViewName.FRONT, 1) == "*正视"
            assert get_sw_view_name(ViewName.FRONT, 2) == "*前视"
            assert get_sw_view_name(ViewName.ISOMETRIC, 0) == "*Iso"
            assert get_sw_view_name(ViewName.ISOMETRIC, 1) == "*等轴测"
            assert get_sw_view_name(ViewName.TOP, 0) == "*上视"
