"""
DWG 文件转换模块
将 DWG 文件转换为 DXF 格式，供后续解析使用

转换策略：
1. 优先使用 ODA File Converter（如果安装并配置了 ODA_PATH 环境变量）
2. 回退使用 LibreDWG 的 dwg2dxf 命令行工具

关键容错：
- subprocess 使用字节模式（不用 text=True），手动用 errors="replace" 解码
  原因：LibreDWG 输出可能包含非 UTF-8 字节，直接 text=True 会崩溃
"""

import os
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Optional, Union

from app.core.config import settings
from app.core.exceptions import DesignReviewException, ErrorCode

logger = logging.getLogger(__name__)


class DWGConversionError(DesignReviewException):
    """DWG 转换异常"""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(
            message,
            error_code=ErrorCode.DXF_PARSE_ERROR,
            detail=detail
        )


class DWGConverter:
    """
    DWG -> DXF 转换器

    Usage:
        converter = DWGConverter()
        dxf_path = converter.convert("/path/to/file.dwg")
    """

    # ODA File Converter 可执行文件路径（优先）
    ODA_CONVERTER = os.environ.get("ODA_PATH", "")

    # LibreDWG dwg2dxf 路径
    LIBREDWG_PATH = shutil.which("dwg2dxf") or "/opt/homebrew/bin/dwg2dxf"

    def __init__(self):
        self._od_converter = self._find_od_converter()
        self._libredwg = self._find_libredwg()

    def _find_od_converter(self) -> Optional[str]:
        """查找 ODA File Converter"""
        candidates = []
        if self.ODA_CONVERTER and Path(self.ODA_CONVERTER).exists():
            candidates.append(self.ODA_CONVERTER)

        # macOS 常见安装位置
        candidates.extend([
            "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
            "/usr/local/bin/ODAFileConverter",
        ])

        for path in candidates:
            if Path(path).exists():
                logger.info(f"找到 ODA File Converter: {path}")
                return path

        logger.debug("未找到 ODA File Converter，将使用 LibreDWG")
        return None

    def _find_libredwg(self) -> Optional[str]:
        """查找 LibreDWG dwg2dxf"""
        if Path(self.LIBREDWG_PATH).exists():
            logger.info(f"找到 LibreDWG dwg2dxf: {self.LIBREDWG_PATH}")
            return self.LIBREDWG_PATH

        # 尝试 homebrew 其他路径
        import glob
        matches = glob.glob("/opt/homebrew/Cellar/libredwg/*/bin/dwg2dxf")
        if matches:
            logger.info(f"找到 LibreDWG dwg2dxf: {matches[0]}")
            return matches[0]

        logger.warning("未找到 LibreDWG dwg2dxf")
        return None

    @property
    def is_available(self) -> bool:
        """是否有可用的转换工具"""
        return self._od_converter is not None or self._libredwg is not None

    @property
    def converter_name(self) -> str:
        """当前使用的转换器名称"""
        if self._od_converter:
            return "ODA File Converter"
        elif self._libredwg:
            return "LibreDWG"
        return "none"

    def convert(
        self,
        dwg_path: Union[str, Path],
        output_dir: Optional[Union[str, Path]] = None
    ) -> Path:
        """
        将 DWG 文件转换为 DXF

        Args:
            dwg_path: DWG 文件路径
            output_dir: 输出目录（默认同目录）

        Returns:
            转换后的 DXF 文件路径

        Raises:
            DWGConversionError: 转换失败
        """
        dwg_path = Path(dwg_path).resolve()

        if not dwg_path.exists():
            raise DWGConversionError(
                f"DWG 文件不存在: {dwg_path}",
                detail=f"路径: {dwg_path}"
            )

        if dwg_path.suffix.lower() != ".dwg":
            raise DWGConversionError(
                f"文件不是 DWG 格式: {dwg_path.suffix}",
                detail=f"路径: {dwg_path}"
            )

        output_dir = Path(output_dir) if output_dir else dwg_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # 输出 DXF 文件路径
        dxf_path = output_dir / (dwg_path.stem + ".dxf")

        logger.info(f"开始 DWG -> DXF 转换: {dwg_path.name} -> {dxf_path.name}")

        # 优先用 ODA，回退用 LibreDWG
        if self._od_converter:
            try:
                self._convert_with_oda(dwg_path, output_dir, dxf_path)
                if dxf_path.exists():
                    logger.info(f"ODA 转换成功: {dxf_path}")
                    return dxf_path
            except Exception as e:
                logger.warning(f"ODA 转换失败，回退到 LibreDWG: {e}")

        if self._libredwg:
            self._convert_with_libredwg(dwg_path, dxf_path)
            if dxf_path.exists():
                logger.info(f"LibreDWG 转换成功: {dxf_path}")
                return dxf_path

        raise DWGConversionError(
            "DWG 转换失败：无可用工具或转换出错",
            detail=f"ODA: {'可用' if self._od_converter else '不可用'}, "
                  f"LibreDWG: {'可用' if self._libredwg else '不可用'}"
        )

    def _convert_with_oda(
        self,
        dwg_path: Path,
        output_dir: Path,
        expected_dxf_path: Path
    ) -> None:
        """
        使用 ODA File Converter 转换

        ODA File Converter 的命令行格式：
        ODAFileConverter <input_dir> <output_dir> <ACAD_VERSION> <recurse> <verify> <audit>
        它按目录批量转换，不支持单文件
        """
        # ODA 按目录转换，先复制到临时目录避免影响其他文件
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_in, tempfile.TemporaryDirectory() as tmp_out:
            tmp_in = Path(tmp_in)
            tmp_out = Path(tmp_out)

            # 复制 DWG 到临时输入目录
            shutil.copy2(dwg_path, tmp_in / dwg_path.name)

            cmd = [
                self._od_converter,
                str(tmp_in),
                str(tmp_out),
                "ACAD2018",  # 输出 DXF 版本
                "0",          # 不递归
                "1",          # 验证
                "1",          # 审计
            ]

            logger.debug(f"ODA 命令: {' '.join(cmd)}")

            # 关键：用字节模式，不用 text=True
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=120,
            )

            # 解码输出（用 errors="replace" 容错）
            stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

            if stdout:
                logger.debug(f"ODA stdout: {stdout[:500]}")
            if stderr:
                logger.debug(f"ODA stderr: {stderr[:500]}")

            # 查找输出文件
            dxf_files = list(tmp_out.glob("*.dxf"))
            if dxf_files:
                shutil.copy2(dxf_files[0], expected_dxf_path)
            else:
                raise DWGConversionError(
                    "ODA 转换未生成 DXF 文件",
                    detail=f"stdout: {stdout[:200]}, stderr: {stderr[:200]}"
                )

    def _convert_with_libredwg(
        self,
        dwg_path: Path,
        dxf_path: Path
    ) -> None:
        """
        使用 LibreDWG dwg2dxf 转换

        dwg2dxf 命令格式：
        dwg2dxf -o <output.dxf> <input.dwg>
        """
        cmd = [
            self._libredwg,
            "-o",
            str(dxf_path),
            str(dwg_path),
        ]

        logger.debug(f"LibreDWG 命令: {' '.join(cmd)}")

        # 关键：用字节模式，不用 text=True
        # LibreDWG 输出可能包含非 UTF-8 字节，text=True 会崩溃
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,
        )

        # 用 errors="replace" 容错解码
        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

        if stdout:
            logger.debug(f"LibreDWG stdout: {stdout[:500]}")
        if stderr:
            logger.debug(f"LibreDWG stderr: {stderr[:500]}")

        # dwg2dxf 返回非 0 不一定代表失败，检查输出文件
        if not dxf_path.exists():
            raise DWGConversionError(
                f"LibreDWG 转换失败，未生成 DXF 文件",
                detail=f"returncode: {result.returncode}, "
                      f"stderr: {stderr[:300]}"
            )


# 全局便捷函数
_converter_instance: Optional[DWGConverter] = None


def get_converter() -> DWGConverter:
    """获取转换器单例"""
    global _converter_instance
    if _converter_instance is None:
        _converter_instance = DWGConverter()
    return _converter_instance


def convert_dwg_to_dxf(
    dwg_path: Union[str, Path],
    output_dir: Optional[Union[str, Path]] = None
) -> Path:
    """
    便捷函数：DWG -> DXF 转换

    Args:
        dwg_path: DWG 文件路径
        output_dir: 输出目录（默认同目录）

    Returns:
        DXF 文件路径
    """
    return get_converter().convert(dwg_path, output_dir)
