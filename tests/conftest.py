"""
Pytest Fixtures
提供测试用共享资源
"""

import pytest
from pathlib import Path
import ezdxf


@pytest.fixture
def fixtures_dir() -> Path:
    """测试夹具目录"""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_dxf_path(fixtures_dir: Path) -> Path:
    """样例DXF文件路径"""
    return fixtures_dir / "test_sample.dxf"


@pytest.fixture
def create_test_dxf(tmp_path: Path) -> Path:
    """
    创建一个标准测试DXF文件
    
    Returns:
        创建的DXF文件路径
    """
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # 添加几何图形
    msp.add_line((0, 0), (100, 100))
    msp.add_circle((50, 50), 25)
    msp.add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100), (0, 0)], close=True)
    
    # 添加文字（模拟标题栏）
    msp.add_text('测试图纸', height=10).set_placement((10, 80))
    msp.add_text('图号: TEST-001', height=5).set_placement((10, 60))
    msp.add_text('设计: 张三', height=5).set_placement((10, 40))
    msp.add_text('审核: 李四', height=5).set_placement((60, 40))
    msp.add_text('比例: 1:1', height=5).set_placement((10, 20))
    msp.add_text('材料: Q355B', height=5).set_placement((60, 20))
    
    # 添加圆弧
    msp.add_arc((50, 50), 30, 0, 90)
    
    # 添加多行文字
    mtext = msp.add_mtext("多行文字\\n测试内容")
    mtext.dxf.char_height = 5
    mtext.dxf.insert = (60, 80)
    
    file_path = tmp_path / "test.dxf"
    doc.saveas(file_path)
    return file_path


@pytest.fixture
def empty_dxf(tmp_path: Path) -> Path:
    """创建一个空DXF文件"""
    doc = ezdxf.new('R2010')
    file_path = tmp_path / "empty.dxf"
    doc.saveas(file_path)
    return file_path


@pytest.fixture
def corrupted_dxf(tmp_path: Path) -> Path:
    """创建一个损坏的DXF文件"""
    file_path = tmp_path / "corrupted.dxf"
    file_path.write_text("这不是有效的DXF内容\nINVALID CONTENT")
    return file_path


@pytest.fixture
def large_dxf(tmp_path: Path) -> Path:
    """创建一个包含大量实体的DXF文件"""
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()

    # 添加1000条直线
    for i in range(1000):
        msp.add_line((i, 0), (i, 100))

    file_path = tmp_path / "large.dxf"
    doc.saveas(file_path)
    return file_path


# ─── 真实复杂场景 Fixtures ───

PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def libredwg_dxf() -> Path:
    """LibreDWG 导出的复杂 DXF 文件（含结构损坏）

    这是一个真实的专用车辆上装设计图纸，由 LibreDWG 从 DWG 转换而来，
    包含 MTEXT 续行、缺失 ENTITIES 段、句柄错乱等典型结构问题。

    如果文件不存在则跳过测试。
    """
    path = PROJECT_ROOT / "temp" / "箱体底架.dxf"
    if not path.exists():
        pytest.skip(f"真实 DXF 测试文件不存在: {path}")
    return path


@pytest.fixture
def dxf_with_outliers(tmp_path: Path) -> Path:
    """创建包含极端异常坐标的 DXF 文件

    主体图形在 0~100 范围内，但有少量异常坐标在 100000+ 处，
    用于验证分位数边界裁剪逻辑。
    """
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()

    # 主体图形：100 条正常直线
    for i in range(100):
        msp.add_line((i, 0), (i, 100))

    # 异常坐标：3 条远离主体的直线
    msp.add_line((500000, 500000), (560000, 560000))
    msp.add_line((-800000, -800000), (-750000, -750000))
    msp.add_line((300000, -90000), (310000, -89000))

    file_path = tmp_path / "with_outliers.dxf"
    doc.saveas(file_path)
    return file_path
