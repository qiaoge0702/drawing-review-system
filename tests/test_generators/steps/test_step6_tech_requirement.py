"""
Step 6 技术要求执行器单元测试

不依赖 SW 环境：直接构造 StepContext 驱动执行器，
验证模板渲染、变量覆盖/缺失、未知模板、position 覆盖、异常路径与产物落盘。
"""

import json
import re
from pathlib import Path

import pytest

from app.generators.models import StepContext
from app.generators.steps.step6_tech_requirement import (
    TechRequirementExecutor,
    TECH_TEMPLATES,
    render_template,
)
from app.models.generation import StepName
from app.core.exceptions import SWException

_VAR_PATTERN = re.compile(r"\{(\w+)\}")


def _make_ctx(tmp_path: Path, parameters=None) -> StepContext:
    return StepContext(
        task_id="test-step6",
        step=6,
        step_name=StepName.TECH_REQUIREMENT,
        work_dir=tmp_path,
        parameters=parameters or {},
    )


def _run(tmp_path: Path, parameters=None):
    ctx = _make_ctx(tmp_path, parameters)
    executor = TechRequirementExecutor()
    import asyncio
    return asyncio.run(executor(ctx))


class TestDefaultTemplate:
    def test_default_template_render(self, tmp_path):
        """缺省模板 = weldment_general，变量插值正确、8 条齐全、无残留占位符"""
        result = _run(tmp_path)
        tr = result["tech_requirements"]

        assert tr["template_id"] == "weldment_general"
        assert tr["template_name"] == "焊接件通用模板"
        assert tr["variables"] == {
            "grade": "二级",
            "size": "5",
            "stress_relief": "消除应力",
            "ndt": "UT-2级",
        }
        assert len(tr["content"]) == 8
        assert tr["content"][0] == "1.焊接应符合GB/T 985.1规定"
        assert tr["content"][1] == "2.焊缝质量等级：二级（GB/T 19418）"
        assert "5mm" in tr["content"][2]
        assert "消除应力" in tr["content"][3]
        assert "UT-2级" in tr["content"][4]
        for line in tr["content"]:
            assert not _VAR_PATTERN.search(line), f"残留占位符: {line}"

    def test_default_position(self, tmp_path):
        result = _run(tmp_path)
        pos = result["tech_requirements"]["position"]
        # 默认 position：图框左下角空白区，且在图幅内（y+height ≤ 287）
        assert pos == {"x": 20.0, "y": 20.0, "width": 200.0, "height": 120.0}
        assert pos["y"] + pos["height"] <= 287.0
        assert pos["x"] + pos["width"] <= 410.0

    def test_default_style(self, tmp_path):
        result = _run(tmp_path)
        assert result["tech_requirements"]["style"] == {
            "font_size": 3.5, "line_spacing": 1.5,
        }


class TestVariableOverride:
    def test_override_takes_effect(self, tmp_path):
        """覆盖默认变量生效"""
        result = _run(tmp_path, {"tech_variables": {"grade": "一级", "size": "8"}})
        tr = result["tech_requirements"]
        assert tr["variables"]["grade"] == "一级"
        assert tr["variables"]["size"] == "8"
        assert "一级" in tr["content"][1]
        assert "8mm" in tr["content"][2]
        # 未覆盖变量保持默认
        assert tr["variables"]["ndt"] == "UT-2级"

    def test_extra_override_warns_but_succeeds(self, tmp_path, caplog):
        """多余覆盖变量（模板未用）→ warning 但不报错"""
        result = _run(tmp_path, {"tech_variables": {"unused_var": "x"}})
        assert len(result["tech_requirements"]["content"]) == 8
        assert "unused_var" in caplog.text

    def test_missing_variable_raises(self):
        """模板变量既无默认值也未覆盖 → SWException"""
        template = {
            "template_id": "t_no_default",
            "template_name": "缺默认模板",
            "variables": {},
            "content_template": ["1.必须提供{must_var}"],
        }
        with pytest.raises(SWException):
            render_template(template, {})

    def test_missing_variable_via_executor(self, tmp_path, monkeypatch):
        """执行器路径下变量缺失 → SWException"""
        monkeypatch.setitem(TECH_TEMPLATES, "t_no_default", {
            "template_id": "t_no_default",
            "template_name": "缺默认模板",
            "variables": {},
            "content_template": ["1.必须提供{must_var}"],
        })
        try:
            with pytest.raises(SWException):
                _run(tmp_path, {"template_id": "t_no_default"})
        finally:
            TECH_TEMPLATES.pop("t_no_default", None)


class TestTemplateSelection:
    def test_unknown_template_raises(self, tmp_path):
        with pytest.raises(SWException):
            _run(tmp_path, {"template_id": "not_exists"})

    def test_machining_template_render(self, tmp_path):
        """machining_general 模板渲染"""
        result = _run(tmp_path, {"template_id": "machining_general"})
        tr = result["tech_requirements"]
        assert tr["template_id"] == "machining_general"
        assert tr["template_name"] == "机加工件通用模板"
        assert "GB/T 1804-m" in tr["content"][0]
        assert "C1" in tr["content"][1]
        assert "R2" in tr["content"][2]
        assert "Ra6.3" in tr["content"][3]
        assert "发黑" in tr["content"][4]
        for line in tr["content"]:
            assert not _VAR_PATTERN.search(line), f"残留占位符: {line}"


class TestConfig:
    def test_position_override(self, tmp_path):
        result = _run(tmp_path, {
            "tech_config": {"position": {"x": 10, "y": 20}},
        })
        pos = result["tech_requirements"]["position"]
        assert pos["x"] == 10.0 and pos["y"] == 20.0
        assert pos["width"] == 200.0 and pos["height"] == 120.0

    def test_style_override(self, tmp_path):
        result = _run(tmp_path, {
            "tech_config": {"style": {"font_size": 5.0}},
        })
        style = result["tech_requirements"]["style"]
        assert style["font_size"] == 5.0
        assert style["line_spacing"] == 1.5  # 未覆盖字段保持默认

    def test_invalid_style_raises(self, tmp_path):
        for bad in ({"style": {"font_size": -1}}, {"style": {"font_size": "big"}},
                    {"style": "not-a-dict"}):
            with pytest.raises(SWException):
                _run(tmp_path, {"tech_config": bad})

    def test_invalid_position_raises(self, tmp_path):
        with pytest.raises(SWException):
            _run(tmp_path, {"tech_config": {"position": {"x": "bad"}}})

    def test_invalid_tech_config_raises(self, tmp_path):
        with pytest.raises(SWException):
            _run(tmp_path, {"tech_config": "not-a-dict"})

    def test_invalid_tech_variables_raises(self, tmp_path):
        with pytest.raises(SWException):
            _run(tmp_path, {"tech_variables": ["not", "a", "dict"]})


class TestArtifact:
    def test_output_file_and_contract(self, tmp_path):
        """产物落盘 + 契约字段完整性 + available_templates"""
        result = _run(tmp_path)

        out_file = tmp_path / "output" / "tech_requirements.json"
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data == result

        tr = data["tech_requirements"]
        for key in ("template_id", "template_name", "variables", "content",
                    "position", "style"):
            assert key in tr
        assert set(tr["position"]) == {"x", "y", "width", "height"}

        assert "available_templates" in data
        assert "weldment_general" in data["available_templates"]
        assert "machining_general" in data["available_templates"]
