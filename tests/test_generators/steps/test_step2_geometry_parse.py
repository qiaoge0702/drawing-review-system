"""
Step 2 几何解析 - 单元测试（mock COM 边界，不依赖真实 SW）

Fake 对象模拟真机探索验证过的 COM 鸭子类型：
- comp.GetMaterialPropertyName2(0, "") → 材料名
- comp.GetModelDoc2 → doc.Extension.CreateMassProperty2 / CreateMassProperty → .Mass (kg)
- comp.GetChildren 为 tuple
"""

import json
from pathlib import Path

import pytest

from app.generators.models import StepContext
from app.generators.steps import step2_geometry_parse
from app.generators.steps.step2_geometry_parse import (
    GeometryParseExecutor,
    _comp_material,
    _comp_mass_kg,
    _walk_components,
    _enrich_bom_material_mass,
)
from app.models.generation import StepName


# ---------- Fake COM 对象 ----------

class FakeMassProperty:
    def __init__(self, mass):
        self.Mass = mass


class FakeExtension:
    def __init__(self, mass):
        self._mass = mass

    def CreateMassProperty2(self, options, config):
        return FakeMassProperty(self._mass)


class FakeModelDoc:
    def __init__(self, mass=None, material=""):
        self.Extension = FakeExtension(mass)
        self._material = material

    def GetMaterialPropertyName2(self, config):
        # 真机 IPartDoc.GetMaterialPropertyName2 返回 (材料名, 材料库名)
        return (self._material, "") if self._material else ("", "")


class FakeComp:
    def __init__(self, name, path="", material="", mass=None, children=(),
                 suppressed=False):
        self.Name2 = name
        self.GetPathName = path
        self.IsSuppressed = suppressed
        self.ReferencedConfiguration = ""
        self._doc = (FakeModelDoc(mass, material)
                     if (mass is not None or material) else None)
        self.GetChildren = tuple(children)

    def GetMaterialPropertyName2(self, option, config):
        raise AttributeError("IComponent2 上无此方法（真机实证，材料在 IPartDoc 上）")

    @property
    def GetModelDoc2(self):
        return self._doc


class FakeConfig:
    def __init__(self, root):
        self.GetRootComponent = root


class FakeDoc:
    def __init__(self, root):
        self.GetConfigurationNames = ["默认"]
        self._root = root

    def GetConfigurationByName(self, name):
        return FakeConfig(self._root)


class FakeParser:
    def __init__(self, root):
        self._doc = FakeDoc(root)

    def open_document(self, filepath):
        return self._doc


def _make_ctx(tmp_path: Path) -> StepContext:
    return StepContext(
        task_id="test-step2", step=2, step_name=StepName.GEOMETRY_PARSE,
        work_dir=tmp_path, parameters={"source_file": "C:/p/A.SLDASM"},
        previous_results={},
    )


# ---------- 组件级提取 ----------

class TestCompMaterial:
    def test_material_name_returned(self):
        assert _comp_material(FakeComp("板", material="Q355B")) == "Q355B"

    def test_missing_material_returns_empty(self):
        assert _comp_material(FakeComp("板", mass=1.0)) == ""

    def test_api_absent_returns_empty(self):
        class Bare:
            pass
        assert _comp_material(Bare()) == ""


class TestCompMass:
    def test_mass_kg_returned(self):
        assert _comp_mass_kg(FakeComp("板", mass=12.5)) == pytest.approx(12.5)

    def test_no_model_doc_returns_none(self):
        assert _comp_mass_kg(FakeComp("装配", mass=None)) is None

    def test_com_failure_returns_none(self):
        class Bad:
            @property
            def GetModelDoc2(self):
                raise RuntimeError("COM dead")
        assert _comp_mass_kg(Bad()) is None


class TestWalkComponents:
    def test_preorder_matches_traverse_bom(self):
        c1 = FakeComp("子1")
        c2 = FakeComp("子2", children=[FakeComp("孙1")])
        root = FakeComp("根", children=[c1, c2])
        names = [c.Name2 for c in _walk_components(root)]
        assert names == ["根", "子1", "子2", "孙1"]


# ---------- BOM 富化 ----------

class TestEnrichBom:
    def test_material_mass_filled(self):
        root = FakeComp("根", material="Q355B", mass=100.0, children=[
            FakeComp("子1", path="C:/p/P1.SLDPRT", material="45#", mass=1.25),
            FakeComp("子2", path="C:/p/P2.SLDPRT"),  # 取不到 → 空
        ])
        bom = [
            {"name": "根", "path": "C:/p/A.SLDASM", "is_suppressed": False},
            {"name": "子1", "path": "C:/p/P1.SLDPRT", "is_suppressed": False},
            {"name": "子2", "path": "C:/p/P2.SLDPRT", "is_suppressed": False},
        ]
        warnings = _enrich_bom_material_mass(FakeParser(root), "C:/p/A.SLDASM", bom)
        assert bom[0]["material"] == "Q355B" and bom[0]["mass"] == 100.0
        assert bom[1]["material"] == "45#" and bom[1]["mass"] == 1.25
        # 取不到 → 空 + warning（诚实原则）
        assert bom[2]["material"] == "" and bom[2]["mass"] == ""
        assert any("材料取不到" in w for w in warnings)
        assert any("单重取不到" in w for w in warnings)

    def test_mass_cached_by_path(self):
        """同路径组件质量缓存复用（CreateMassProperty 只算一次）"""
        child1 = FakeComp("子1", path="C:/p/P1.SLDPRT", mass=2.0)
        child2 = FakeComp("子2", path="C:/p/P1.SLDPRT", mass=2.0)
        root = FakeComp("根", children=[child1, child2])
        bom = [
            {"name": "根", "path": "C:/p/A.SLDASM", "is_suppressed": False},
            {"name": "子1", "path": "C:/p/P1.SLDPRT", "is_suppressed": False},
            {"name": "子2", "path": "C:/p/P1.SLDPRT", "is_suppressed": False},
        ]
        _enrich_bom_material_mass(FakeParser(root), "C:/p/A.SLDASM", bom)
        assert bom[1]["mass"] == 2.0 and bom[2]["mass"] == 2.0

    def test_suppressed_skipped(self):
        root = FakeComp("根", children=[FakeComp("隐", mass=1.0)])
        bom = [
            {"name": "根", "path": "A", "is_suppressed": False},
            {"name": "隐", "path": "B", "is_suppressed": True},
        ]
        _enrich_bom_material_mass(FakeParser(root), "A", bom)
        assert bom[1]["material"] == "" and bom[1]["mass"] == ""

    def test_tree_unavailable_warns_and_keeps_empty(self):
        class BadParser:
            def open_document(self, filepath):
                raise RuntimeError("no doc")
        bom = [{"name": "X", "path": "X", "is_suppressed": False}]
        warnings = _enrich_bom_material_mass(BadParser(), "X", bom)
        assert bom[0]["material"] == "" and bom[0]["mass"] == ""
        assert warnings and "整体跳过" in warnings[0]


# ---------- 执行器 ----------

class TestExecutor:
    @pytest.mark.asyncio
    async def test_bom_normalized_with_material_mass(self, tmp_path, monkeypatch):
        raw_bom = [
            {"level": 0, "name": "底架焊合", "path": "C:/p/LB26.11000.SLDASM",
             "quantity": 1, "is_suppressed": False, "material": "Q355B", "mass": 85.2},
            {"level": 1, "name": "连接板", "path": "C:/p/LB26.11001.SLDPRT",
             "quantity": 2, "is_suppressed": False, "material": "45#", "mass": 12.3456},
            {"level": 1, "name": "缺数据件", "path": "C:/p/X.SLDPRT",
             "quantity": 1, "is_suppressed": False, "material": None, "mass": None},
        ]

        async def fake_run_sw(func, *args):
            return raw_bom, ["单重取不到: 1/3 条（已留空）"]
        monkeypatch.setattr(step2_geometry_parse, "run_sw", fake_run_sw)

        result = await GeometryParseExecutor()(_make_ctx(tmp_path))

        bom = result["bom"]
        assert bom[0]["material"] == "Q355B" and bom[0]["mass"] == 85.2
        assert bom[1]["material"] == "45#" and bom[1]["mass"] == 12.3456
        # 缺失 → 空字符串（诚实原则）
        assert bom[2]["material"] == "" and bom[2]["mass"] == ""
        # 材料统计 + 总质量（85.2×1 + 12.3456×2）
        assert result["materials"] == {"Q355B": 1, "45#": 1}
        assert result["total_mass"] == pytest.approx(round(85.2 + 12.3456 * 2, 3))
        assert result["warnings"] == ["单重取不到: 1/3 条（已留空）"]
        # 落盘
        data = json.loads((tmp_path / "output" / "bom.json").read_text(encoding="utf-8"))
        assert data["bom"][1]["mass"] == 12.3456
