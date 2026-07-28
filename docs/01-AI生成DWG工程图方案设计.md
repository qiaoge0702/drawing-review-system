# AI生成DWG工程图方案设计

**业务场景**: LB26拉臂装置（环卫车厢可卸式垃圾车上装）  
**核心目标**: 从3D模型自动生成基础质量DWG，人工审核后出图  
**日期**: 2026-07-28

---

## 一、业务分析

### 1.1 痛点

LB26案例：208个文件 → 仅3张工程图。出图严重滞后。

| 痛点 | 根因 |
|------|------|
| 焊接件图繁琐 | 结构复杂、标注量大、BOM多 |
| 技术要求重复 | 相似零件无模板，每次重写 |
| 公差不一致 | 依赖个人经验 |
| 标准件遗漏 | 螺栓/垫圈数量多，易漏 |

### 1.2 出图优先级

| 优先级 | 类型 | 数量 | 策略 |
|--------|------|------|------|
| P0 | 焊接件工程图 | ~15张 | 优先自动化 |
| P1 | 装配图 | ~10张 | 总体视图+BOM |
| P2 | 机加/钣金件 | ~50张 | 尺寸+公差 |
| P3 | 标准件/外购件 | ~130个 | 不生成，引用标准 |

---

## 二、系统架构

### 2.1 数据流

```
SW文件(.SLDASM/.SLDPRT)
    |
    v
┌─────────────────┐
| 1.几何解析       |
| 提取装配树/特征  |
└─────────────────┘
    |
    v
┌─────────────────┐
| 2.视图投影       |
| 正交投影→2D轮廓  |
└─────────────────┘
    |
    v
┌─────────────────┐
| 3.尺寸标注       |
| 特征识别→位置优化 |
└─────────────────┘
    |
    v
┌─────────────────┐
| 4.BOM+技术要求   |
| 装配树→BOM表     |
| 零件类型→模板    |
└─────────────────┘
    |
    v
┌─────────────────┐
| 5.DXF组装        |
| ezdxf构建图层    |
└─────────────────┘
    |
    v
┌─────────────────┐
| 6.审查闭环       |
| 现有系统规则校验  |
└─────────────────┘
    |
    v
DWG输出 → 人工审核 → 签字出图
```

### 2.2 与现有系统融合

| 现有模块 | 作用 |
|----------|------|
| `dxf_parser.py` | 生成后验证DXF结构 |
| `analyzer.py` | 生成前分析3D特征 |
| `engine.py` | 生成后规则校验 |
| `dwg_converter.py` | DXF→DWG转换 |

### 2.3 新增模块

```
app/
├── generators/
│   ├── sw_parser.py              # 3D几何解析
│   ├── view_projector.py         # 视图投影
│   ├── dimension_placer.py       # 尺寸标注
│   ├── bom_generator.py          # BOM生成
│   ├── tech_requirement_generator.py # 技术要求
│   ├── layout_optimizer.py       # 布局优化
│   └── dxf_builder.py            # DXF组装
└── knowledge/
    ├── templates/                # 技术要求模板
    ├── tolerance_rules.json      # 公差规则
    └── weld_specs.json           # 焊接规范
```

---

## 三、核心模块

### 3.1 几何解析

```python
class SWParser:
    def parse_assembly(self, path: str) -> AssemblyModel: ...
    def parse_part(self, path: str) -> PartModel: ...

class PartModel:
    name: str
    material: Material
    geometry: Geometry
    features: List[Feature]       # 孔/槽/倒角等加工特征
```

**技术选型**: pywin32+SW API（有SW环境）/ trimesh解析STEP（无SW环境）

### 3.2 视图投影

```python
class ViewProjector:
    def generate_views(self, model: PartModel) -> List[View]: ...
    def generate_section(self, model: PartModel, plane: Plane) -> SectionView: ...

class View:
    view_type: ViewType           # FRONT/TOP/LEFT/SECTION
    entities: List[2DEntity]      # 线/弧/圆
    hidden_lines: List[Line]
    scale: float
```

**算法**: 正交投影矩阵 → 轮廓提取 → 剖面线填充

### 3.3 尺寸标注

```python
class DimensionPlacer:
    def place(self, view: View, features: List[Feature]) -> List[Dimension]: ...
    def recommend_tolerance(self, feature: Feature) -> Tolerance: ...

class Dimension:
    dim_type: DimensionType       # LINEAR/DIAMETER/RADIUS
    measurement: float
    tolerance: Tolerance
    position: Point2D
```

**AI赋能**: 特征识别→标注类型→公差推荐→位置优化（避免重叠）

### 3.4 技术要求生成

```python
class TechRequirementGenerator:
    def generate(self, part_type: PartType, material: Material) -> List[str]: ...
    def load_template(self, part_type: PartType) -> str: ...
```

**模板示例**（焊接件）:
```
1.焊接符合GB/T 985.1，焊缝质量等级{{grade}}
2.未注焊缝尺寸K={{size}}，焊后{{stress_relief}}
3.探伤要求：{{ndt}}
```

**AI流程**: 零件类型识别 → 加载模板 → 特征分析填充变量 → LLM润色

### 3.5 DXF构建

```python
class DXFBuilder:
    def add_view(self, view: View, layer: str): ...
    def add_dimension(self, dim: Dimension, layer: str): ...
    def add_bom_table(self, bom: BOMTable): ...
    def add_title_block(self, info: DrawingInfo): ...
    def save(self, path: str): ...
```

---

## 四、AI赋能点

| 赋能点 | 输入 | 输出 | 方式 |
|--------|------|------|------|
| 公差推荐 | 特征类型+尺寸范围 | 公差等级 | 规则库+历史学习 |
| 技术要求 | 零件类型+材料 | 完整技术条文 | RAG模板+LLM润色 |
| 尺寸位置 | 视图边界+已有标注 | 最优位置 | 启发式算法 |
| 布局优化 | 视图数量+图纸尺寸 | 视图排列 | 矩形装箱+约束满足 |

---

## 五、实施路线

| 阶段 | 周期 | 目标 | 验收 |
|------|------|------|------|
| Phase 1 | 2-3周 | 基础框架 | 能读取LB26装配树，输出可打开DXF |
| Phase 2 | 3-4周 | 核心功能 | BOM一致率>95%，尺寸覆盖主要特征 |
| Phase 3 | 2-3周 | AI增强 | 公差推荐准确率>80%，技术要求通过率>90% |
| Phase 4 | 2周 | 集成验证 | LB26焊接件图完整性>85%，单张<30秒 |

---

## 六、风险对策

| 风险 | 对策 |
|------|------|
| SW API依赖 | 同时开发STEP解析路径 |
| 3D几何复杂 | 先支持简单零件，逐步扩展 |
| 公差不准 | 规则库兜底，AI逐步学习 |
| 技术要求遗漏 | 模板库覆盖，人工审核兜底 |

---

## 七、关键结论

1. **技术可行**: ezdxf+ODA+AI可实现基础质量DWG
2. **深度融合**: 复用现有审查系统，形成生成→审查闭环
3. **分阶段**: 从P0焊接件入手，逐步扩展
4. **人机协作**: AI生成初稿（省70%时间），人工审核确认
5. **知识积累**: 模板库+规则库，越用越准
