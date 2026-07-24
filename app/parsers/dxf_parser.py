"""
DXF文件解析器模块
提供多层容错读取、编码自动检测、实体提取等功能
"""

import os
import logging
import math
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union, List, Dict, Any, BinaryIO
from datetime import datetime
from dataclasses import dataclass

import ezdxf
import ezdxf.recover

from app.core.config import settings
from app.core.exceptions import DXFParseException, ErrorCode
from app.models.drawing import (
    Drawing, DrawingInfo, DrawingMetadata, DrawingExtents,
    ExtractedEntities, LayerInfo
)
from .entity_extractor import EntityExtractor
from .metadata_extractor import MetadataExtractor

logger = logging.getLogger(__name__)


# ─── 宽松加载模式 ───
# LibreDWG 导出的 DXF 存在句柄错乱（DIMSTYLE.block_record_handle 指向 LINE 实体、
# Layout.block_record_handle 指向 BLOCK 而非 BLOCK_RECORD 等），
# 导致 ezdxf 的第二加载阶段（post_load_hook）和布局设置（Layouts.load）崩溃。
# 此 context manager 临时替换多个关键方法为宽松版本，逐实体 try/except 跳过错误。
# 第一加载阶段（实体创建、BLOCKS/ENTITIES 段加载）不受影响。

@contextmanager
def _permissive_loading():
    """
    临时替换 ezdxf 的 _2nd_loading_stage 和 Layout.__init__ 为宽松版本。
    post_load_hook 失败的实体会被跳过而非导致整个文件加载失败。
    Layout 的 block_record 类型不匹配时也跳过而非崩溃。
    """
    from ezdxf.document import Drawing
    from ezdxf.layouts.layout import Layout

    original_2nd_stage = Drawing._2nd_loading_stage
    original_layout_init = Layout.__init__

    def permissive_2nd_loading_stage(self):
        """宽松版第二加载阶段：跳过 post_load_hook 失败的实体"""
        db = self.entitydb
        post_load_cmds = []
        skipped = 0

        for entity in list(db.values()):
            try:
                cmd = entity.post_load_hook(self)
                if cmd is not None:
                    post_load_cmds.append(cmd)
            except Exception as e:
                skipped += 1
                logger.debug(
                    f"跳过 post_load_hook: {entity.dxftype()}"
                    f"(handle={entity.dxf.handle}): {e}"
                )

        if skipped > 0:
            logger.info(f"宽松加载：跳过了 {skipped} 个 post_load_hook 失败的实体")

        for cmd in post_load_cmds:
            try:
                cmd.execute(self)
            except Exception as e:
                logger.debug(f"跳过延迟命令: {e}")

    def permissive_layout_init(self, layout, doc):
        """宽松版 Layout.__init__：block_record 类型不匹配时在 entitydb 中搜索正确的 BLOCK_RECORD"""
        import ezdxf.lldxf.const as const
        from ezdxf.layouts.layout import _find_layout_block_record

        self.dxf_layout = layout
        handle = layout.dxf.get("block_record_handle", "0")
        block_record = None
        try:
            entity = doc.entitydb[handle]
            if entity.dxftype() == "BLOCK_RECORD":
                block_record = entity
        except KeyError:
            pass

        if block_record is None:
            # 先尝试 ezdxf 内置查找
            block_record = _find_layout_block_record(layout)

        if block_record is None:
            # 在 entitydb 中搜索同名的 BLOCK_RECORD
            layout_name = layout.dxf.get("name", "")
            for ent in doc.entitydb.values():
                if ent.dxftype() == "BLOCK_RECORD":
                    if ent.dxf.get("name", "") == layout_name:
                        block_record = ent
                        break

        if block_record is None:
            # 最后手段：取任意一个 BLOCK_RECORD（通常是 *Model_Space 或 *Paper_Space）
            for ent in doc.entitydb.values():
                if ent.dxftype() == "BLOCK_RECORD":
                    block_record = ent
                    logger.warning(
                        f"Layout '{layout_name}' 找不到匹配的 BLOCK_RECORD，"
                        f"使用 fallback: {ent.dxf.get('name', '?')}"
                    )
                    break

        if block_record is None:
            raise const.DXFStructureError(
                f"无法为 layout '{layout.dxf.name}' 找到任何 BLOCK_RECORD"
            )

        try:
            block_record.dxf.layout = layout.dxf.handle
        except Exception:
            pass
        super(Layout, self).__init__(block_record)

    Drawing._2nd_loading_stage = permissive_2nd_loading_stage
    Layout.__init__ = permissive_layout_init

    try:
        yield
    finally:
        Drawing._2nd_loading_stage = original_2nd_stage
        Layout.__init__ = original_layout_init


@dataclass
class ParseOptions:
    """解析选项"""
    recover_on_error: bool = True
    extract_metadata: bool = True
    extract_entities: bool = True
    calculate_extents: bool = True
    include_raw_data: bool = False


class DXFParserError(Exception):
    """DXF解析错误（内部使用）"""
    pass


def _fix_dxf_structure(file_path: Path) -> Optional[Path]:
    """
    修复中文 CAD 导出 DXF 的 MTEXT 换行导致的结构损坏

    问题：中文 CAD 在导出 DXF 时，MTEXT 内容中的换行符会破坏
    DXF 的 group code / value 交替行结构。

    修复策略（持续合并直到结构恢复同步）：
    1. 状态机交替读取 group_code 和 value
    2. 当期待 group_code 但当前行不是整数 → 是续行，合并到上一个 value
    3. 当期待 group_code 且当前行是整数，但在续行模式中：
       检查 i+2 是否也是合法 group code（确认结构同步）
       如果不是 → 当前行是 MTEXT 内容中的数字片段，合并
    4. 补全 EOF 标签
    """
    # 编码检测：先试 strict 模式确定真实编码
    lines = None
    for enc in ["utf-8", "gbk", "gb2312", "latin-1"]:
        try:
            with open(file_path, "r", encoding=enc, errors="strict") as f:
                lines = f.readlines()
            logger.debug(f"结构修复：使用编码 {enc} 读取成功")
            break
        except (UnicodeDecodeError, Exception):
            continue
    if lines is None:
        # 最后兜底
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        logger.debug("结构修复：使用 UTF-8 replace 兜底读取")

    def is_valid_gc(s: str) -> bool:
        """检查是否是合法 DXF group code（0-1071）"""
        try:
            val = int(s)
            return 0 <= val <= 1071
        except ValueError:
            return False

    fixed_lines = []
    needs_fix = False
    expect_group_code = True
    in_continuation = False

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n\r")
        stripped = line.strip()

        if expect_group_code:
            if is_valid_gc(stripped):
                # 可能是 group code
                if in_continuation:
                    # 在续行模式中，验证 i+2 是否也是 gc
                    # 结构: gc(i) → value(i+1) → gc(i+2)
                    i2 = i + 2
                    if i2 < len(lines) and is_valid_gc(lines[i2].strip()):
                        # 结构同步确认
                        in_continuation = False
                    else:
                        # 当前数字是 MTEXT 内容片段，合并
                        if fixed_lines:
                            prev = fixed_lines[-1].rstrip("\n\r")
                            fixed_lines[-1] = prev + stripped + "\n"
                            needs_fix = True
                        i += 1
                        continue

                fixed_lines.append(line + "\n")
                expect_group_code = False
                i += 1
            else:
                # 不是 group code → 续行
                if fixed_lines:
                    prev = fixed_lines[-1].rstrip("\n\r")
                    fixed_lines[-1] = prev + stripped + "\n"
                    needs_fix = True
                    in_continuation = True
                i += 1
        else:
            # value 行
            fixed_lines.append(line + "\n")
            expect_group_code = True
            i += 1

    # 补全 EOF
    last_content = "".join(fixed_lines[-4:]).strip()
    # 第二遍：清理空的表条目（LibreDWG 导出问题）
    # 空条目特征：0/ENTITY_TYPE 后面直接跟 0/，中间没有任何属性
    table_entry_types = {
        "LAYER", "STYLE", "LTYPE", "APPID", "DIMSTYLE",
        "BLOCK_RECORD", "VIEW", "UCS", "VPORT",
    }
    cleaned_lines = []
    i = 0
    removed_count = 0
    while i < len(fixed_lines):
        # 检查当前行是否是 gc=0
        if fixed_lines[i].strip() == "0" and i + 1 < len(fixed_lines):
            etype = fixed_lines[i + 1].strip()
            if etype in table_entry_types:
                # 检查下一个是否也是 gc=0（空条目）
                if i + 2 < len(fixed_lines) and fixed_lines[i + 2].strip() == "0":
                    # 空条目，跳过这两行
                    removed_count += 1
                    i += 2
                    needs_fix = True
                    continue
        cleaned_lines.append(fixed_lines[i])
        i += 1

    if removed_count > 0:
        fixed_lines = cleaned_lines
        logger.info(f"清理了 {removed_count} 个空表条目")

    # 第三遍：为缺少 SEQEND 的 INSERT/POLYLINE 序列补全
    # LibreDWG 导出的 INSERT 有 ATTRIB 子实体但缺少 SEQEND 终止符
    # 还需处理 attribs_follow=1 但无 ATTRIB 也无 SEQEND 的情况
    entity_types = {
        "INSERT", "LINE", "CIRCLE", "ARC", "TEXT", "MTEXT",
        "LWPOLYLINE", "POLYLINE", "POINT", "HATCH", "DIMENSION",
        "SPLINE", "ELLIPSE", "BLOCK", "ENDBLK", "SEQEND",
        "ATTRIB", "ATTDEF", "VERTEX", "LEADER", "MLEADER",
        "RAY", "XLINE", "TOLERANCE", "FRAME",
        "3DFACE", "SOLID", "3DSOLID", "BODY", "REGION",
        "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION",
        "ACAD_TABLE", "DICTIONARY", "IMAGE", "MLINE",
        "MLEADERSTYLE", "WIPEOUT", "MULTILEADER",
        "DATATABLE", "GEODATA", "PLOTSETTINGS",
        "DIMASSOC", "ACAD_PLACEHOLDER",
    }
    seqend_fixed = []
    seqend_added = 0
    i = 0
    while i < len(fixed_lines):
        seqend_fixed.append(fixed_lines[i])

        # 检测 INSERT 或 POLYLINE 实体
        if (fixed_lines[i].strip() == "0" and i + 1 < len(fixed_lines)
                and fixed_lines[i + 1].strip() in ("INSERT", "POLYLINE")):
            etype = fixed_lines[i + 1].strip()
            # 向前扫描子实体和 SEQEND
            has_sub = False       # 有 ATTRIB/ATTDEF/VERTEX
            has_seqend = False
            attribs_follow = None  # INSERT 的 gc=66 值
            # POLYLINE 总是需要 SEQEND
            needs_seqend = (etype == "POLYLINE")

            j = i + 2
            while j < len(fixed_lines):
                if fixed_lines[j].strip() == "0" and j + 1 < len(fixed_lines):
                    sub_type = fixed_lines[j + 1].strip()
                    if sub_type in ("ATTRIB", "ATTDEF", "VERTEX"):
                        has_sub = True
                    elif sub_type == "SEQEND":
                        has_seqend = True
                        break
                    elif sub_type in entity_types or sub_type not in (
                        "ATTRIB", "ATTDEF", "VERTEX"
                    ):
                        # 遇到下一个实体
                        break
                else:
                    # 记录 INSERT 的 attribs_follow 标志（gc=66）
                    if etype == "INSERT" and fixed_lines[j].strip() == "66":
                        val = fixed_lines[j + 1].strip() if j + 1 < len(fixed_lines) else ""
                        if val == "1":
                            attribs_follow = "1"
                            needs_seqend = True
                j += 2

            # 如果需要 SEQEND 但没有，在下一个实体前插入
            if needs_seqend and not has_seqend:
                # 复制从 i+1 到 j-1（实体属性和子实体）
                for k in range(i + 1, j):
                    seqend_fixed.append(fixed_lines[k])
                # 插入 SEQEND
                seqend_fixed.append("  0\n")
                seqend_fixed.append("SEQEND\n")
                seqend_added += 1
                needs_fix = True
                i = j
                continue

        i += 1

    if seqend_added > 0:
        fixed_lines = seqend_fixed
        logger.info(f"补充了 {seqend_added} 个缺失的 SEQEND")

    # 第四遍：检查并补充缺失的 ENTITIES 段
    # LibreDWG 不输出 ENTITIES 段和 *Model_Space 块，所有实体在命名块中
    # 策略：从 BLOCKS 段中找到包含最多绘图实体的命名块，
    # 将该块中从第一个实体到 ENDBLK 之间的所有行直接切片复制到 ENTITIES 段
    drawing_entity_types = {
        "LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE",
        "TEXT", "MTEXT", "DIMENSION", "INSERT", "HATCH",
        "SPLINE", "ELLIPSE", "POINT", "LEADER", "MLEADER",
        "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION",
        "RAY", "XLINE", "TOLERANCE", "3DFACE", "SOLID",
    }

    has_entities = False
    for k in range(len(fixed_lines) - 3):
        if (fixed_lines[k].strip() == "0"
                and fixed_lines[k + 1].strip() == "SECTION"
                and fixed_lines[k + 2].strip() == "2"
                and fixed_lines[k + 3].strip() == "ENTITIES"):
            has_entities = True
            break

    if not has_entities:
        # 找到 BLOCKS 段的边界
        blocks_start = -1
        blocks_end = -1
        for k in range(len(fixed_lines) - 1):
            if fixed_lines[k].strip() != "0":
                continue
            next_val = fixed_lines[k + 1].strip() if k + 1 < len(fixed_lines) else ""
            if next_val == "SECTION" and k + 3 < len(fixed_lines):
                if fixed_lines[k + 2].strip() == "2" and fixed_lines[k + 3].strip() == "BLOCKS":
                    blocks_start = k
                    continue
            if blocks_start >= 0 and next_val == "ENDSEC":
                blocks_end = k
                break

        # 没找到 ENDSEC 时，BLOCKS 段延伸到文件末尾
        if blocks_start >= 0 and blocks_end < 0:
            blocks_end = len(fixed_lines)
            logger.debug("BLOCKS 段没有 ENDSEC，使用文件末尾作为边界")

        # 在 BLOCKS 段中找到绘图实体最多的命名块（跳过维度块 *D####）
        best_block_first_entity = -1  # 第一个 0/实体类型 的位置
        best_block_endblk = -1        # 0/ENDBLK 的位置
        best_block_score = 0
        best_block_name = ""

        if blocks_start >= 0 and blocks_end >= 0:
            i = blocks_start
            while i < blocks_end:
                if (fixed_lines[i].strip() == "0"
                        and i + 1 < len(fixed_lines)
                        and fixed_lines[i + 1].strip() == "BLOCK"):
                    # 读取块名
                    block_name = ""
                    j = i + 2
                    while j < blocks_end:
                        gc = fixed_lines[j].strip()
                        if gc == "2":
                            block_name = fixed_lines[j + 1].strip() if j + 1 < len(fixed_lines) else ""
                            break
                        if gc == "0":
                            break
                        j += 2

                    # 扫描到 ENDBLK，统计绘图实体并记录第一个实体位置
                    score = 0
                    first_entity = -1
                    endblk_pos = -1
                    j = i + 2
                    while j < blocks_end:
                        if fixed_lines[j].strip() == "0":
                            etype = fixed_lines[j + 1].strip() if j + 1 < len(fixed_lines) else ""
                            if etype == "ENDBLK":
                                endblk_pos = j
                                break
                            if etype in drawing_entity_types:
                                score += 1
                                if first_entity < 0:
                                    first_entity = j
                        j += 1

                    # 跳过维度块（*D####），只选有实体的命名块
                    is_dim_block = block_name.startswith("*D")
                    if score > best_block_score and not is_dim_block and first_entity >= 0:
                        best_block_score = score
                        best_block_first_entity = first_entity
                        best_block_endblk = endblk_pos
                        best_block_name = block_name

                    # 跳到 ENDBLK 之后继续找下一个块
                    if endblk_pos >= 0:
                        i = endblk_pos + 2  # 跳过 0/ENDBLK
                    else:
                        i = j
                else:
                    i += 1

        # 构建 ENTITIES 段：直接切片复制第一个实体到 ENDBLK 之间的所有行
        entity_lines = []
        if best_block_score > 0 and best_block_first_entity >= 0 and best_block_endblk > best_block_first_entity:
            logger.info(
                f"从块 '{best_block_name}' 提取 {best_block_score} 个实体到 ENTITIES 段 "
                f"(lines {best_block_first_entity}~{best_block_endblk})"
            )
            entity_lines = fixed_lines[best_block_first_entity:best_block_endblk]
            needs_fix = True

        # 找到最后一个 ENDSEC 的位置
        last_endsec = -1
        for k in range(len(fixed_lines) - 1, 0, -1):
            if (fixed_lines[k].strip() == "ENDSEC"
                    and fixed_lines[k - 1].strip() == "0"):
                last_endsec = k
                break

        if last_endsec >= 0:
            entities_section = [
                "  0\n", "SECTION\n", "  2\n", "ENTITIES\n",
            ]
            entities_section.extend(entity_lines)
            entities_section.extend([
                "  0\n", "ENDSEC\n",
                "  0\n", "EOF\n",
            ])
            fixed_lines = fixed_lines[:last_endsec + 1] + entities_section
            needs_fix = True
            logger.info(f"补充了 ENTITIES 段，包含 {len(entity_lines)} 行实体数据")
    else:
        # 检查 EOF
        last_content = "".join(fixed_lines[-4:]).strip()
        if "EOF" not in last_content:
            fixed_lines.append("  0\n")
            fixed_lines.append("EOF\n")
            needs_fix = True
            logger.info("补充缺失的 EOF 标签")

    if not needs_fix:
        logger.debug("DXF 结构检查通过，无需修复")
        return None

    logger.info("DXF 结构修复：发现并修复了错位行")

    fixed_path = file_path.parent / (file_path.stem + "_fixed.dxf")
    try:
        with open(fixed_path, "w", encoding="utf-8") as f:
            f.writelines(fixed_lines)
        logger.info(f"修复后文件已写入: {fixed_path}")
        return fixed_path
    except Exception as e:
        logger.error(f"写入修复文件失败: {e}")
        return None


class DXFParser:
    """
    DXF文件解析器
    
    功能：
    - 多层容错读取（标准读取 → 结构修复 → 恢复模式 → 编码回退）
    - 自动编码检测
    - 实体提取和统计
    - 标题栏元数据提取
    - 图纸范围计算
    
    Usage:
        parser = DXFParser("/path/to/file.dxf")
        drawing = parser.parse()
    """
    
    def __init__(self, file_path: Union[str, Path]):
        self.file_path = Path(file_path).resolve()
        self.doc: Optional[ezdxf.document.Drawing] = None
        self.msp = None
        self._validate_file()
    
    def _validate_file(self) -> None:
        """验证文件有效性"""
        if not self.file_path.exists():
            raise DXFParseException(
                f"文件不存在: {self.file_path}",
                ErrorCode.SYS_FILE_NOT_FOUND,
                file_path=str(self.file_path)
            )
        
        if not self.file_path.is_file():
            raise DXFParseException(
                f"路径不是文件: {self.file_path}",
                ErrorCode.SYS_FILE_NOT_FOUND,
                file_path=str(self.file_path)
            )
        
        file_size = self.file_path.stat().st_size
        max_size = settings.dxf.max_file_size_mb * 1024 * 1024
        if file_size > max_size:
            raise DXFParseException(
                f"文件过大: {file_size / 1024 / 1024:.1f}MB > {settings.dxf.max_file_size_mb}MB",
                ErrorCode.VAL_RANGE_ERROR,
                file_path=str(self.file_path),
                detail=f"文件大小: {file_size} bytes"
            )
        
        if self.file_path.suffix.lower() != ".dxf":
            logger.warning(f"文件扩展名不是.dxf: {self.file_path.suffix}")
    
    def parse(self, options: Optional[ParseOptions] = None) -> Drawing:
        opts = options or ParseOptions()
        
        logger.info(f"开始解析DXF文件: {self.file_path}")
        start_time = datetime.now()
        
        try:
            self._load_file()
            
            drawing_info = self._extract_file_info()
            
            metadata = DrawingMetadata()
            if opts.extract_metadata:
                metadata = self._extract_metadata()
            
            extents = DrawingExtents(
                min_x=0, min_y=0, min_z=0,
                max_x=0, max_y=0, max_z=0
            )
            if opts.calculate_extents:
                extents = self._calculate_extents()
            
            entities = ExtractedEntities()
            if opts.extract_entities:
                entities = self._extract_entities()
            
            drawing = Drawing(
                info=drawing_info,
                metadata=metadata,
                extents=extents,
                entities=entities,
                raw_data=None
            )
            
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"DXF解析完成，耗时: {elapsed:.2f}s，实体数: {entities.get_total_entity_count()}")
            
            return drawing
            
        except DXFParseException:
            raise
        except Exception as e:
            logger.exception(f"DXF解析失败: {self.file_path}")
            raise DXFParseException(
                f"DXF解析失败: {str(e)}",
                ErrorCode.DXF_PARSE_ERROR,
                file_path=str(self.file_path),
                detail=str(e)
            )
    
    def _load_file(self) -> None:
        """
        加载DXF文件（多层容错）

        尝试顺序：
        1. 标准读取
        2. 结构修复后读取
        3. 结构修复 + recover
        4. recover 模式
        5. 不同编码尝试
        """
        errors = []

        # 尝试1: 标准读取
        try:
            logger.debug(f"尝试标准读取: {self.file_path}")
            self.doc = ezdxf.readfile(self.file_path)
            self.msp = self.doc.modelspace()
            logger.debug("标准读取成功")
            return
        except Exception as e:
            errors.append(f"标准读取失败: {e}")
            logger.debug(f"标准读取失败: {e}")

        # 尝试2: 结构修复后读取
        try:
            logger.debug(f"尝试结构修复后读取: {self.file_path}")
            fixed_path = _fix_dxf_structure(self.file_path)
            if fixed_path:
                self.doc = ezdxf.readfile(fixed_path)
                self.msp = self.doc.modelspace()
                entity_count = sum(1 for _ in self.msp)
                if entity_count == 0:
                    self._populate_modelspace_from_blocks()
                logger.info(f"结构修复后读取成功，实体数: {sum(1 for _ in self.msp)}")
                return
        except Exception as e:
            errors.append(f"结构修复读取失败: {e}")
            logger.debug(f"结构修复读取失败: {e}")

        # 尝试3: 结构修复 + recover
        try:
            logger.debug(f"尝试结构修复+recover: {self.file_path}")
            fixed_path = _fix_dxf_structure(self.file_path)
            if fixed_path:
                self.doc, auditor = ezdxf.recover.readfile(fixed_path)
                self.msp = self.doc.modelspace()
                entity_count = sum(1 for _ in self.msp)
                if entity_count == 0:
                    self._populate_modelspace_from_blocks()
                logger.info(f"结构修复+recover 读取成功，实体数: {sum(1 for _ in self.msp)}")
                return
        except Exception as e:
            errors.append(f"结构修复+recover失败: {e}")
            logger.debug(f"结构修复+recover失败: {e}")

        # 尝试4: 恢复模式
        if settings.dxf.recover_on_error:
            try:
                logger.debug(f"尝试恢复模式读取: {self.file_path}")
                self.doc, auditor = ezdxf.recover.readfile(self.file_path)
                self.msp = self.doc.modelspace()

                if auditor.has_errors:
                    error_count = len(auditor.errors)
                    logger.warning(f"恢复模式读取成功，但发现 {error_count} 个错误")
                else:
                    logger.debug("恢复模式读取成功")
                return
            except Exception as e:
                errors.append(f"恢复模式失败: {e}")
                logger.debug(f"恢复模式失败: {e}")

        # 尝试5: 宽松加载模式（跳过 post_load_hook 失败的实体）
        try:
            logger.info(f"尝试宽松加载模式: {self.file_path}")
            fixed_path = _fix_dxf_structure(self.file_path)
            read_path = fixed_path if fixed_path else self.file_path

            with _permissive_loading():
                try:
                    self.doc = ezdxf.readfile(read_path)
                    self.msp = self.doc.modelspace()
                except Exception:
                    self.doc, auditor = ezdxf.recover.readfile(read_path, errors="ignore")
                    self.msp = self.doc.modelspace()

            entity_count = sum(1 for _ in self.msp)
            logger.info(f"宽松模式读取成功！模型空间实体数: {entity_count}")
            if entity_count == 0:
                self._populate_modelspace_from_blocks()
                entity_count = sum(1 for _ in self.msp)
                logger.info(f"兜底补充后模型空间实体数: {entity_count}")
            return
        except Exception as e:
            errors.append(f"宽松模式失败: {e}")
            logger.debug(f"宽松模式失败: {e}")

        # 尝试6: 不同编码
        for encoding in settings.dxf.encoding_fallbacks:
            try:
                logger.debug(f"尝试编码 {encoding}: {self.file_path}")
                self.doc = ezdxf.readfile(self.file_path, encoding=encoding)
                self.msp = self.doc.modelspace()
                logger.debug(f"编码 {encoding} 读取成功")
                return
            except Exception as e:
                errors.append(f"编码 {encoding} 失败: {e}")
                logger.debug(f"编码 {encoding} 失败: {e}")

        error_detail = "; ".join(errors)
        raise DXFParseException(
            "无法解析DXF文件，所有读取方式均失败",
            ErrorCode.DXF_PARSE_ERROR,
            file_path=str(self.file_path),
            detail=error_detail
        )

    def _populate_modelspace_from_blocks(self) -> None:
        """
        ezdxf 层兜底：如果模型空间没有实体，从 doc.blocks 中找到
        绘图实体最多的命名块，将其实体复制到模型空间。

        适用于 LibreDWG 导出的 DXF（无 ENTITIES 段，实体在命名块中），
        文本层修复（第四遍）未成功时的最终兜底。
        """
        entity_count = sum(1 for _ in self.msp)
        if entity_count > 0:
            return

        if not self.doc:
            return

        drawing_entity_types = {
            "LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE",
            "TEXT", "MTEXT", "DIMENSION", "INSERT", "HATCH",
            "SPLINE", "ELLIPSE", "POINT", "LEADER", "MLEADER",
            "RAY", "XLINE", "TOLERANCE", "3DFACE", "SOLID",
        }

        best_block = None
        best_score = 0
        best_name = ""

        try:
            for block in self.doc.blocks:
                name = block.name if hasattr(block, 'name') else str(block)
                # 跳过维度块和系统块
                if name.startswith("*D") or name.startswith("*"):
                    continue
                score = 0
                try:
                    for ent in block.entity_space:
                        etype = ent.dxftype() if hasattr(ent, 'dxftype') else ""
                        if etype in drawing_entity_types:
                            score += 1
                except Exception:
                    continue
                if score > best_score:
                    best_score = score
                    best_block = block
                    best_name = name
        except Exception as e:
            logger.warning(f"扫描 blocks 失败: {e}")
            return

        if best_block is None or best_score == 0:
            logger.warning("未找到包含绘图实体的命名块")
            return

        logger.info(f"从块 '{best_name}' 复制 {best_score} 个实体到模型空间")

        copied = 0
        failed = 0
        try:
            for ent in list(best_block.entity_space):
                etype = ent.dxftype() if hasattr(ent, 'dxftype') else ""
                if etype not in drawing_entity_types:
                    continue
                try:
                    new_ent = ent.copy()
                    self.msp.add_entity(new_ent)
                    copied += 1
                except Exception as e:
                    failed += 1
                    if failed <= 5:
                        logger.debug(f"复制实体 {etype} 失败: {e}")
        except Exception as e:
            logger.warning(f"从块复制实体失败: {e}")

        logger.info(f"模型空间补充完成：成功 {copied}，失败 {failed}")

    def _extract_file_info(self) -> DrawingInfo:
        stat = self.file_path.stat()
        return DrawingInfo(
            file_name=self.file_path.name,
            file_path=str(self.file_path),
            file_size=stat.st_size,
            file_type="dxf",
            created_at=datetime.fromtimestamp(stat.st_ctime),
            modified_at=datetime.fromtimestamp(stat.st_mtime)
        )
    
    def _extract_metadata(self) -> DrawingMetadata:
        extractor = MetadataExtractor(self.doc, self.msp)
        return extractor.extract()

    def _extract_entities(self) -> ExtractedEntities:
        extractor = EntityExtractor(self.doc, self.msp)
        return extractor.extract()

    def _calculate_extents(self) -> DrawingExtents:
        try:
            from ezdxf import bbox
            extents = bbox.extents(self.msp)
            if extents:
                return DrawingExtents(
                    min_x=extents.extmin[0],
                    min_y=extents.extmin[1],
                    min_z=extents.extmin[2] if len(extents.extmin) > 2 else 0,
                    max_x=extents.extmax[0],
                    max_y=extents.extmax[1],
                    max_z=extents.extmax[2] if len(extents.extmax) > 2 else 0
                )
        except Exception as e:
            logger.warning(f"使用extents()计算范围失败: {e}")
        
        return self._calculate_extents_manual()
    
    def _calculate_extents_manual(self) -> DrawingExtents:
        """
        手动计算图纸范围。

        直接读取常见实体类型的坐标点，并用 1%-99% 分位数过滤
        LibreDWG 导出 DXF 中常见的异常坐标点，避免范围被异常值拉得过宽。
        """
        xs = []
        ys = []
        zs = []

        for entity in self.msp:
            try:
                etype = entity.dxftype()
                if etype == "LINE":
                    xs.extend([entity.dxf.start.x, entity.dxf.end.x])
                    ys.extend([entity.dxf.start.y, entity.dxf.end.y])
                    zs.extend([entity.dxf.start.z, entity.dxf.end.z])
                elif etype == "CIRCLE":
                    c = entity.dxf.center
                    r = entity.dxf.radius
                    xs.extend([c.x - r, c.x + r])
                    ys.extend([c.y - r, c.y + r])
                    zs.extend([c.z - r, c.z + r])
                elif etype == "ARC":
                    c = entity.dxf.center
                    r = entity.dxf.radius
                    xs.extend([c.x - r, c.x + r])
                    ys.extend([c.y - r, c.y + r])
                    zs.extend([c.z - r, c.z + r])
                elif etype == "LWPOLYLINE":
                    for p in entity.get_points():
                        xs.append(p[0])
                        ys.append(p[1])
                elif etype == "POLYLINE":
                    for v in entity.vertices:
                        xs.append(v.dxf.location.x)
                        ys.append(v.dxf.location.y)
                        zs.append(v.dxf.location.z)
                elif etype == "ELLIPSE":
                    c = entity.dxf.center
                    major = entity.dxf.major_axis
                    width = 2 * math.sqrt(major.x ** 2 + major.y ** 2)
                    height = width * entity.dxf.ratio
                    xs.extend([c.x - width, c.x + width])
                    ys.extend([c.y - height, c.y + height])
                    zs.append(c.z)
                elif etype in ("TEXT", "MTEXT", "INSERT", "POINT"):
                    xs.append(entity.dxf.insert.x)
                    ys.append(entity.dxf.insert.y)
                    zs.append(entity.dxf.insert.z)
            except Exception:
                pass

        if len(xs) < 2:
            return DrawingExtents(
                min_x=0, min_y=0, min_z=0,
                max_x=0, max_y=0, max_z=0
            )

        def percentile_bounds(values, lower=5.0, upper=95.0):
            values = sorted(values)
            n = len(values)
            lo = max(0, min(int(n * lower / 100), n - 1))
            hi = max(0, min(int(n * upper / 100), n - 1))
            if hi <= lo:
                hi = n - 1
            return values[lo], values[hi]

        min_x, max_x = percentile_bounds(xs)
        min_y, max_y = percentile_bounds(ys)
        if zs:
            min_z, max_z = percentile_bounds(zs)
        else:
            min_z, max_z = 0.0, 0.0

        return DrawingExtents(
            min_x=min_x, min_y=min_y, min_z=min_z,
            max_x=max_x, max_y=max_y, max_z=max_z
        )
    
    def get_dxf_version(self) -> Optional[str]:
        if not self.doc:
            return None
        return self.doc.dxfversion
    
    def get_layer_count(self) -> int:
        if not self.doc:
            return 0
        return len(self.doc.layers)
    
    def get_layer_names(self) -> List[str]:
        if not self.doc:
            return []
        return [layer.dxf.name for layer in self.doc.layers]


def parse_dxf(file_path: Union[str, Path], **kwargs) -> Drawing:
    options = ParseOptions(**kwargs)
    parser = DXFParser(file_path)
    return parser.parse(options)
