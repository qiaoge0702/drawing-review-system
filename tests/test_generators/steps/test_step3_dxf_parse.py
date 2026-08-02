"""
Step 3 解析层单元测试：SW 原生导出 DXF → ezdxf 归一化（fixture DXF 脚本生成固化）

覆盖：
- 区域分配：实体按包围盒中心分配到 front/top/left 图幅区域
- 线型分类：CONTINUOUS→entities、HIDDEN→hidden_lines、CENTER→center_lines
  （含 BYLAYER 回溯图层线型）
- 坐标换算：图纸坐标 → 视图局部实际尺寸 mm（减区域原点 × 比例分母），
  再按视图包围盒左下角归一（min 归零）
- 纪律：视图区域无实体 → SWException（禁假成功）；$INSUNITS≠4 → warning
"""

import ezdxf
import pytest

from app.generators.view_extractor import parse_exported_dxf, bounding_box_of
from app.core.exceptions import SWException, ErrorCode

# 布局：A3 420×297，比例 1:2（den=2）
# front 区域 (20,150,100,50)；top 区域 (20,60,100,40)；left 区域 (160,150,40,50)
POSITIONS = {
    "front": {"x": 20.0, "y": 150.0, "width": 100.0, "height": 50.0},
    "top": {"x": 20.0, "y": 60.0, "width": 100.0, "height": 40.0},
    "left": {"x": 160.0, "y": 150.0, "width": 40.0, "height": 50.0},
}
DEN = 2.0


def _build_fixture_dxf(path, with_hidden=True, with_center=True, insunits=4):
    # insunits ≠ 4 时坐标按该单位书写（如 6=米：mm 坐标 /1000），
    # 解析层应归一化回 mm（2026-08-01 单位归一化 fixture）
    _UNIT_DIV = {1: 25.4, 6: 1000.0}
    div = _UNIT_DIV.get(insunits, 1.0)
    def U(v):
        return v / div
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = insunits
    for lt, desc in (("HIDDEN", "Dashed"), ("CENTER", "Center")):
        if lt not in doc.linetypes:
            doc.linetypes.add(lt, pattern="A,6,-3", description=desc)
    doc.layers.add("HIDDENLYR", linetype="HIDDEN")
    msp = doc.modelspace()
    # front：实线矩形 (30,160)-(110,195)
    msp.add_line((U(30), U(160)), (U(110), U(160)))
    msp.add_line((U(110), U(160)), (U(110), U(195)))
    msp.add_line((U(110), U(195)), (U(30), U(195)))
    msp.add_line((U(30), U(195)), (U(30), U(160)))
    if with_hidden:
        # front：虚线（HIDDEN 线型）
        msp.add_line((U(40), U(170)), (U(100), U(170)), dxfattribs={"linetype": "HIDDEN"})
    if with_center:
        # front：BYLAYER 回溯 → 图层 HIDDENLYR（虚线）
        msp.add_line((U(40), U(180)), (U(100), U(180)), dxfattribs={"layer": "HIDDENLYR"})
        # top：点划线圆（CENTER）
        msp.add_circle((U(70), U(80)), U(10), dxfattribs={"linetype": "CENTER"})
    # top：实线矩形 (30,65)-(110,95)
    msp.add_line((U(30), U(65)), (U(110), U(65)))
    msp.add_line((U(110), U(65)), (U(110), U(95)))
    msp.add_line((U(110), U(95)), (U(30), U(95)))
    msp.add_line((U(30), U(95)), (U(30), U(65)))
    # left：实线矩形 (165,160)-(195,195)
    msp.add_line((U(165), U(160)), (U(195), U(160)))
    msp.add_line((U(195), U(160)), (U(195), U(195)))
    msp.add_line((U(195), U(195)), (U(165), U(195)))
    msp.add_line((U(165), U(195)), (U(165), U(160)))
    doc.saveas(path)


@pytest.fixture
def dxf_path(tmp_path):
    p = tmp_path / "raw_export.dxf"
    _build_fixture_dxf(p)
    return str(p)


def _build_clean(tmp_path):
    p = tmp_path / "raw_clean.dxf"
    _build_fixture_dxf(p)
    return p


class TestRegionAssignment:
    def test_entities_assigned_to_three_views(self, dxf_path):
        r = parse_exported_dxf(dxf_path, POSITIONS, DEN,
                               ["front", "top", "left"])
        views = {v["name"]: v for v in r["views"]}
        assert len(views["front"]["entities"]) == 4
        assert len(views["top"]["entities"]) == 4
        assert len(views["left"]["entities"]) == 4

    def test_empty_view_region_raises(self, tmp_path):
        """单视图区域无实体 → SWException（禁假成功）"""
        p = tmp_path / "raw_export.dxf"
        _build_fixture_dxf(p)
        positions = dict(POSITIONS)
        # left 区域挪到空白处
        positions["left"] = {"x": 300.0, "y": 250.0, "width": 40.0, "height": 50.0}
        with pytest.raises(SWException) as exc_info:
            parse_exported_dxf(str(p), positions, DEN, ["front", "top", "left"])
        assert exc_info.value.error_code == ErrorCode.GEN_STEP_FAILED

    def test_missing_dxf_raises(self, tmp_path):
        with pytest.raises(SWException):
            parse_exported_dxf(str(tmp_path / "nope.dxf"), POSITIONS, DEN,
                               ["front", "top", "left"])


class TestLinetypeClassification:
    def test_hidden_and_center_lines_classified(self, dxf_path):
        r = parse_exported_dxf(dxf_path, POSITIONS, DEN,
                               ["front", "top", "left"])
        views = {v["name"]: v for v in r["views"]}
        # front：1 条 HIDDEN 线型 + 1 条 BYLAYER→HIDDENLYR
        assert len(views["front"]["hidden_lines"]) == 2
        # top：1 个 CENTER 圆
        assert len(views["top"]["center_lines"]) == 1
        assert views["top"]["center_lines"][0]["type"] == "circle"

    def test_no_hidden_lines_warns_but_succeeds(self, tmp_path):
        """无 HIDDEN 线型导出：如实 warning + 空列表，禁止编造"""
        p = tmp_path / "raw_export.dxf"
        _build_fixture_dxf(p, with_hidden=False, with_center=False)
        r = parse_exported_dxf(str(p), POSITIONS, DEN, ["front", "top", "left"])
        views = {v["name"]: v for v in r["views"]}
        assert views["front"]["hidden_lines"] == []
        assert any("hidden_lines 为空" in w for w in r["warnings"])


class TestCoordinateTransform:
    def test_local_coords_actual_size_normalized(self, dxf_path):
        """局部坐标 = (图纸坐标 − 区域原点) × den，再按包围盒左下角归一"""
        r = parse_exported_dxf(dxf_path, POSITIONS, DEN,
                               ["front", "top", "left"])
        front = next(v for v in r["views"] if v["name"] == "front")
        # 图纸矩形 (30,160)-(110,195)；区域原点 (20,150)；den=2
        # → 局部 (20,20)-(180,90) → 归一 (0,0)-(160,70)
        bb = front["bounding_box"]
        assert bb == {"min_x": 0.0, "min_y": 0.0, "max_x": 160.0, "max_y": 70.0}
        xs = sorted({e["x1"] for e in front["entities"]})
        ys = sorted({e["y1"] for e in front["entities"]})
        assert xs == [0.0, 160.0]
        assert ys == [0.0, 70.0]

    def test_center_circle_radius_scaled(self, dxf_path):
        r = parse_exported_dxf(dxf_path, POSITIONS, DEN,
                               ["front", "top", "left"])
        top = next(v for v in r["views"] if v["name"] == "top")
        circle = top["center_lines"][0]
        # 图纸半径 10 × den 2 = 20（实际尺寸 mm）
        assert circle["r"] == 20.0

    def test_scale_string(self, dxf_path):
        r = parse_exported_dxf(dxf_path, POSITIONS, DEN, ["front", "top", "left"])
        assert all(v["scale"] == "1:2" for v in r["views"])

    def test_insunits_non_mm_warns(self, tmp_path):
        p = tmp_path / "raw_export.dxf"
        _build_fixture_dxf(p, insunits=1)
        r = parse_exported_dxf(str(p), POSITIONS, DEN, ["front", "top", "left"])
        assert any("$INSUNITS" in w for w in r["warnings"])


class TestUnitNormalization:
    """单位归一化（2026-08-01 修复）：$INSUNITS ≠ 4 时实体坐标换算到 mm
    后再做区域分配与局部坐标换算，输出与 mm fixture 完全一致"""

    @pytest.mark.parametrize("insunits", [1, 6])  # 1=英寸, 6=米（SW COM 内部单位）
    def test_non_mm_dxf_normalized_to_mm(self, tmp_path, insunits):
        p = tmp_path / "raw_export.dxf"
        _build_fixture_dxf(p, insunits=insunits)
        r = parse_exported_dxf(str(p), POSITIONS, DEN,
                               ["front", "top", "left"])
        # 如实 warning：非 mm 单位 + 换算系数
        assert any("$INSUNITS" in w and "归一化" in w for w in r["warnings"])
        front = next(v for v in r["views"] if v["name"] == "front")
        # 与 mm fixture 相同断言：局部归一 (0,0)-(160,70)
        assert front["bounding_box"] == {"min_x": 0.0, "min_y": 0.0,
                                         "max_x": 160.0, "max_y": 70.0}
        xs = sorted({round(e["x1"], 3) for e in front["entities"]})
        assert xs == [0.0, 160.0]
        top = next(v for v in r["views"] if v["name"] == "top")
        # 点划线圆半径：图纸 10mm × den 2 = 20（单位归一化后一致）
        assert top["center_lines"][0]["r"] == pytest.approx(20.0, abs=1e-3)

    def test_mm_dxf_no_conversion_warning(self, dxf_path):
        r = parse_exported_dxf(dxf_path, POSITIONS, DEN,
                               ["front", "top", "left"])
        assert not any("归一化" in w for w in r["warnings"])


class TestSheetFormatExclusion:
    """图框/标题栏线条（sheet format 图层）不污染视图区域分配与包围盒
    （2026-08-01 真机根因：A0 边框线中心落入 top 区域，bbox 被拉成 8210mm）"""

    def test_sheet_format_lines_excluded(self, tmp_path):
        p = tmp_path / "raw_export.dxf"
        _build_fixture_dxf(p)
        # 追加图幅级线条：sheet format 图层 "5"，竖线贯穿全图幅且中心在 top 区域
        doc = ezdxf.readfile(str(p))
        doc.layers.add("5")
        msp = doc.modelspace()
        msp.add_line((25, 0), (25, 297), dxfattribs={"layer": "5"})
        msp.add_line((30, 90), (180, 90), dxfattribs={"layer": "5"})
        doc.saveas(str(p))
        r = parse_exported_dxf(str(p), POSITIONS, DEN,
                               ["front", "top", "left"])
        assert any("图框/标题栏" in w for w in r["warnings"])
        top = next(v for v in r["views"] if v["name"] == "top")
        # top 视图包围盒不被贯穿线拉高：仍为几何矩形 (30,65)-(110,95) 归一
        # （含点划线圆 (70,80) r10 → 局部 (20,10)-(180,90) ×…，高不含 297 图幅线）
        bb = top["bounding_box"]
        assert bb["max_y"] <= 90.0  # 无图框线时圆底 60×2... 实际见下断言
        assert bb["max_y"] < 200.0  # 关键：不被 297mm 图幅线 × den 拉成几百
        # 与无污染 fixture 完全一致
        r0 = parse_exported_dxf(str(_build_clean(tmp_path)), POSITIONS, DEN,
                                ["front", "top", "left"])
        top0 = next(v for v in r0["views"] if v["name"] == "top")
        assert bb == top0["bounding_box"]


class TestContractShape:
    def test_view_fields_match_contract(self, dxf_path):
        """契约字段逐一对齐旧 views.json 结构"""
        r = parse_exported_dxf(dxf_path, POSITIONS, DEN, ["front", "top", "left"])
        for v in r["views"]:
            assert set(v.keys()) == {
                "name", "display_name", "projection", "entities",
                "hidden_lines", "center_lines", "section_hatch",
                "bounding_box", "scale"}
            assert v["projection"] == "first_angle"
            assert v["section_hatch"] is None
            assert set(v["bounding_box"].keys()) == {
                "min_x", "min_y", "max_x", "max_y"}
        names = [v["name"] for v in r["views"]]
        assert names == ["front", "top", "left"]


def test_bounding_box_of_kept_compatible():
    bb = bounding_box_of([
        {"type": "line", "x1": 1, "y1": 2, "x2": 3, "y2": 4},
        {"type": "circle", "cx": 10, "cy": 10, "r": 5},
    ])
    assert bb == {"min_x": 1.0, "min_y": 2.0, "max_x": 15.0, "max_y": 15.0}
