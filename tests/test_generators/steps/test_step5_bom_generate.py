"""
Step 5 BOM 生成执行器单元测试

不依赖 SW 环境：直接构造 Step2 bom 数据驱动执行器，
验证聚合、图号提取、外购件识别、诚实空值、排序、异常路径与产物落盘。
"""

import json
from pathlib import Path

import pytest

from app.generators.models import StepContext
from app.generators.steps.step5_bom_generate import (
    BomGenerateExecutor,
    aggregate_bom,
    _extract_drawing_number,
)
from app.models.generation import StepName
from app.core.exceptions import SWException, ErrorCode

COLUMNS = ["序号", "图号", "名称", "数量", "材料", "单重", "总重", "备注"]


def _item(name, path="", quantity=1, is_suppressed=False, level=1):
    return {
        "level": level,
        "name": name,
        "path": path,
        "quantity": quantity,
        "is_suppressed": is_suppressed,
    }


def _make_ctx(tmp_path: Path, step2_result=None, parameters=None) -> StepContext:
    previous = {}
    if step2_result is not None:
        previous[2] = step2_result
    return StepContext(
        task_id="test-step5",
        step=5,
        step_name=StepName.BOM_GENERATE,
        work_dir=tmp_path,
        parameters=parameters or {},
        previous_results=previous,
    )


class TestAggregateBom:
    def test_duplicate_components_quantity_accumulated(self):
        """同一图号组件出现多次 → 聚合为一行，数量累加"""
        bom = [
            _item("连接板", "C:/parts/LB26.11001.SLDPRT", 2),
            _item("连接板", "C:/other/LB26.11001.SLDPRT", 3),
        ]
        rows = aggregate_bom(bom)
        assert len(rows) == 1
        assert rows[0]["drawing_number"] == "LB26.11001"
        assert rows[0]["quantity"] == 5

    def test_suppressed_excluded(self):
        bom = [
            _item("底架焊合", "C:/p/LB26.11000.SLDASM", 1),
            _item("隐藏件", "C:/p/HIDDEN.SLDPRT", 9, is_suppressed=True),
        ]
        rows = aggregate_bom(bom)
        assert len(rows) == 1
        assert rows[0]["drawing_number"] == "LB26.11000"

    def test_sort_assembly_first_purchased_last(self):
        """装配件在前、外购件（GB/T）在后；同级按图号字典序"""
        bom = [
            _item("螺栓 GB/T 5782", "C:/std/GBT5782-M10.SLDPRT", 4),
            _item("横梁", "C:/p/LB26.12000.SLDPRT", 1),
            _item("底架焊合", "C:/p/LB26.11000.SLDASM", 1),
        ]
        rows = aggregate_bom(bom)
        assert [r["drawing_number"] for r in rows] == [
            "LB26.11000", "LB26.12000", "GBT5782-M10",
        ]
        assert rows[2]["purchased"] is True


    def test_jbt_recognized_as_purchased(self):
        """真实案例：JB/T 7940.1 油杯等标准件前缀均应识别为外购件"""
        bom = [
            _item("油杯 JB/T 7940.1", "C:/std/JBT7940-M10.SLDPRT", 2),
            _item("油杯 JB╱T 7940.1", "C:/std/JBT7940B-M10.SLDPRT", 1),
            _item("螺栓 GB╱T 5782", "C:/std/GBT5782.SLDPRT", 1),
            _item("气缸 Q/320201ABC01", "C:/std/CYL.SLDPRT", 1),
            _item("法兰 HG/T 20592", "C:/std/FLG.SLDPRT", 1),
            _item("连接板", "C:/p/LB26.11001.SLDPRT", 1),
        ]
        rows = aggregate_bom(bom)
        by_dn = {r["drawing_number"]: r for r in rows}
        assert by_dn["JBT7940-M10"]["purchased"] is True
        assert by_dn["JBT7940B-M10"]["purchased"] is True
        assert by_dn["GBT5782"]["purchased"] is True
        assert by_dn["CYL"]["purchased"] is True
        assert by_dn["FLG"]["purchased"] is True
        assert by_dn["LB26.11001"]["purchased"] is False
        # 排序：非外购件在前
        assert rows[0]["drawing_number"] == "LB26.11001"


class TestDrawingNumberExtraction:
    def test_path_preferred(self):
        item = _item("任意名称", "D:/models/LB26.11000.SLDPRT")
        assert _extract_drawing_number(item) == "LB26.11000"

    def test_fallback_to_name(self):
        item = _item("底架焊合", "")
        assert _extract_drawing_number(item) == "底架焊合"


class TestBomGenerateExecutor:
    @pytest.mark.asyncio
    async def test_full_output_contract(self, tmp_path):
        """契约字段完整性 + 聚合 + 外购备注 + 诚实空值"""
        bom = [
            _item("底架焊合", "C:/p/LB26.11000.SLDASM", 1),
            _item("连接板", "C:/p/LB26.11001.SLDPRT", 2),
            _item("连接板", "C:/p/LB26.11001.SLDPRT", 1),
            _item("螺栓 GB/T 5782", "C:/std/GBT5782.SLDPRT", 4),
            _item("虚拟件", "C:/p/GHOST.SLDPRT", 1, is_suppressed=True),
        ]
        ctx = _make_ctx(tmp_path, {"bom": bom, "bom_summary": {}})
        result = await BomGenerateExecutor()(ctx)

        table = result["bom_table"]
        assert table["columns"] == COLUMNS
        # 默认 position：标题栏正上方、右对齐图框；height 按行数动态计算
        # 3 行数据 → 20 + 3*15 = 65
        assert table["position"] == {"x": 240.0, "y": 50.0, "width": 160.0, "height": 65.0}
        # 表格在图幅内（A3 横向有效范围 [10,410]×[10,287]）
        assert table["position"]["x"] + table["position"]["width"] <= 410.0
        assert table["position"]["y"] + table["position"]["height"] <= 287.0
        assert table["style"]["header_height"] == 20.0
        assert table["style"]["column_widths"] == [15.0, 45.0, 45.0, 15.0, 20.0, 20.0, 20.0, 20.0]
        assert table["style"]["text_align"] == "left"
        assert result["source_total_items"] == 5

        rows = table["rows"]
        assert len(rows) == 3
        # 序号从 1 连续；排序：装配在前外购在后
        assert [r[0] for r in rows] == [1, 2, 3]
        assert rows[0][1] == "LB26.11000"
        assert rows[1][1] == "LB26.11001" and rows[1][3] == 3  # 数量累加
        assert rows[2][1] == "GBT5782"
        # 材料/单重/总重：诚实空字符串
        for r in rows:
            assert r[4] == "" and r[5] == "" and r[6] == ""
        # 外购件备注
        assert rows[0][7] == "" and rows[2][7] == "外购"

    @pytest.mark.asyncio
    async def test_artifact_written(self, tmp_path):
        """产物落盘 output/bom.json，utf-8 中文可读"""
        ctx = _make_ctx(tmp_path, {"bom": [_item("底架焊合", "C:/p/LB26.11000.SLDASM")]})
        await BomGenerateExecutor()(ctx)
        bom_file = tmp_path / "output" / "bom.json"
        assert bom_file.exists()
        data = json.loads(bom_file.read_text(encoding="utf-8"))
        assert data["bom_table"]["rows"][0][2] == "底架焊合"
        assert data["source_total_items"] == 1

    @pytest.mark.asyncio
    async def test_bom_config_override(self, tmp_path):
        """bom_config 合法覆盖 position/style"""
        ctx = _make_ctx(
            tmp_path,
            {"bom": [_item("A", "C:/p/A.SLDPRT")]},
            parameters={"bom_config": {
                "position": {"x": 100, "y": 500},
                "style": {"row_height": 12},
            }},
        )
        result = await BomGenerateExecutor()(ctx)
        pos = result["bom_table"]["position"]
        assert pos["x"] == 100.0 and pos["y"] == 500.0
        assert pos["width"] == 160.0  # 未覆盖字段保持默认
        # height 始终按行数动态覆盖：1 行 × row_height 12 + header 20 = 32
        assert pos["height"] == 32.0
        assert result["bom_table"]["style"]["row_height"] == 12.0

    @pytest.mark.asyncio
    async def test_bom_config_invalid_rejected(self, tmp_path):
        """bom_config 非法值显式报错"""
        ctx = _make_ctx(
            tmp_path,
            {"bom": [_item("A", "C:/p/A.SLDPRT")]},
            parameters={"bom_config": {"position": {"x": "bad"}}},
        )
        with pytest.raises(SWException) as exc:
            await BomGenerateExecutor()(ctx)
        assert exc.value.error_code == ErrorCode.GEN_INVALID_FILE

    @pytest.mark.asyncio
    async def test_column_widths_override_and_validation(self, tmp_path):
        """column_widths 合法覆盖生效；长度不符/非正数 → SWException"""
        ctx = _make_ctx(
            tmp_path,
            {"bom": [_item("A", "C:/p/A.SLDPRT")]},
            parameters={"bom_config": {
                "style": {"column_widths": [10, 40, 40, 10, 15, 15, 15, 15],
                          "text_align": "center"},
            }},
        )
        result = await BomGenerateExecutor()(ctx)
        style = result["bom_table"]["style"]
        assert style["column_widths"] == [10.0, 40.0, 40.0, 10.0, 15.0, 15.0, 15.0, 15.0]
        assert style["text_align"] == "center"

        # 长度不符 → SWException
        ctx_bad = _make_ctx(
            tmp_path,
            {"bom": [_item("A", "C:/p/A.SLDPRT")]},
            parameters={"bom_config": {"style": {"column_widths": [10, 20, 30]}}},
        )
        with pytest.raises(SWException) as exc:
            await BomGenerateExecutor()(ctx_bad)
        assert exc.value.error_code == ErrorCode.GEN_INVALID_FILE

        # 含非正数 → SWException
        ctx_bad2 = _make_ctx(
            tmp_path,
            {"bom": [_item("A", "C:/p/A.SLDPRT")]},
            parameters={"bom_config": {"style": {
                "column_widths": [10, 40, 40, 10, 15, 15, 15, -5]}}},
        )
        with pytest.raises(SWException):
            await BomGenerateExecutor()(ctx_bad2)

    @pytest.mark.asyncio
    async def test_height_dynamic_by_rows(self, tmp_path):
        """config 静态 height 不生效：height = header_height + rows×row_height"""
        bom = [_item(f"件{i}", f"C:/p/P{i:02d}.SLDPRT") for i in range(5)]
        ctx = _make_ctx(
            tmp_path, {"bom": bom},
            parameters={"bom_config": {"position": {"height": 999}}})
        result = await BomGenerateExecutor()(ctx)
        assert result["bom_table"]["position"]["height"] == 20.0 + 5 * 15.0

    @pytest.mark.asyncio
    async def test_missing_step2_raises(self, tmp_path):
        """无 Step2 结果且无检查点 → SWException"""
        ctx = _make_ctx(tmp_path)
        with pytest.raises(SWException) as exc:
            await BomGenerateExecutor()(ctx)
        assert exc.value.error_code == ErrorCode.GEN_STEP_FAILED
        assert not (tmp_path / "output" / "bom.json").exists()

    @pytest.mark.asyncio
    async def test_all_suppressed_raises(self, tmp_path):
        """全部被抑制 → 聚合后空表 → SWException（禁止静默空数据）"""
        bom = [_item("X", "C:/p/X.SLDPRT", 1, is_suppressed=True)]
        ctx = _make_ctx(tmp_path, {"bom": bom})
        with pytest.raises(SWException) as exc:
            await BomGenerateExecutor()(ctx)
        assert exc.value.error_code == ErrorCode.GEN_STEP_FAILED

    @pytest.mark.asyncio
    async def test_checkpoint_fallback(self, tmp_path):
        """previous_results 缺失时回退 output/geometry.json 检查点"""
        out = tmp_path / "output"
        out.mkdir(parents=True)
        (out / "geometry.json").write_text(
            json.dumps({"bom": [_item("横梁", "C:/p/LB26.12000.SLDPRT", 1)]},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        ctx = _make_ctx(tmp_path)
        result = await BomGenerateExecutor()(ctx)
        assert result["bom_table"]["rows"][0][1] == "LB26.12000"
