# 04-step1-step2加载与解析

## 4.1 Step1: SW加载
**位置**: `app/generators/steps/step1_sw_load.py` (~140行)

**职责**: 通过pywin32+SW COM加载文件，提取装配体/零件基本信息。

**输出契约**:
```python
{
    "file_type": "assembly" | "part",
    "name": str,
    "path": str,
    "component_count": int,       # 装配体
    "components": [{name, path, instance_id, quantity, is_suppressed, is_hidden}],
    "material": {name, description},  # 零件
    "mass": float,                # kg
    "bounding_box": (x, y, z),    # mm
    "feature_count": int,
    "features": [{name, type}],
    "snapshot_path": None,        # M2占位
}
```

**资源管理**:
```python
parser = SWParser()
try:
    result = _parse_xxx(parser, filepath)
finally:
    parser.close_document() + parser.quit()  # 绝不泄漏SW句柄
```

## 4.2 Step2: 几何解析
**位置**: `app/generators/steps/step2_geometry_parse.py` (~280行)

**职责**: 提取BOM+材料+质量，为Step5/6提供数据。

**BOM条目字段**:
```python
{
    "level": int,
    "name": str,
    "path": str,
    "quantity": int,
    "is_suppressed": bool,
    "type": "standard"|"weldment"|"sheet_metal"|...,
    "material": str,      # 真机提取，取不到→空
    "mass": float|"",     # kg，取不到→空
}
```

**材料/单重提取** (`_enrich_bom_material_mass`):
- 路径1: 晚期绑定直调（mock友好）
- 路径2: 早期绑定IPartDoc（真机主路径，`gencache.EnsureModule`）
- 质量缓存: 同路径组件复用mass_cache

**输出契约**:
```python
{
    "bom": [...],
    "bom_summary": {total_items, unique_items, standard_parts, custom_parts},
    "materials": {材料名: 计数},
    "total_mass": float,      # kg，Σ(单重×数量)
    "part_info": {...},       # 零件时附加
    "warnings": [str],
}
```

## 4.3 B-M1 新增资产（2026-08-02）

B-M1 智能骨架交付中，Step1/Step2 的输出被 **类型识别模块** 扩展使用：

### 类型识别输入
- **来源**: Step1 输出的 `bounding_box` (包围盒尺寸 mm)
- **来源**: Step1 输出的 `file_type` (assembly/part)
- **来源**: Step1 输出的 `component_count` (装配体零件数)
- **来源**: 源文件名（用于标准件关键词匹配）

### 类型识别输出
- **写入**: `result.json` 中的 `type_info` 字段
```json
{
  "type": "beam",
  "reason": "细长特征：最大边=6512.00mm > 次小边×5=500.00mm",
  "priority": 2,
  "bounding_box": {
    "dx": 6512.0,
    "dy": 100.0,
    "dz": 50.0,
    "edges": {"min": 50.0, "mid": 100.0, "max": 6512.0}
  }
}
```

### 新增资产清单

| 模块 | 位置 | 功能 | 验证状态 |
|------|------|------|----------|
| `type_recognition.py` | `app/generators/` | 5类零件识别规则 | 单测✅ 真机⏳ |
| `view_strategy.py` | `app/generators/` | 视图策略库+比例序列+中英文适配 | 单测✅ 真机⏳ |
| 布局算法 | `step3_view_project.py` | 第一角投影摆位算法 | 单测✅ 真机⏳ |

**识别规则（按优先级）**:
| 类型 | 判定规则 | 优先级 |
|------|----------|--------|
| `standard_part` | 文件名关键词（螺栓/螺母/垫圈/轴承/bolt/nut/washer/bearing）或包围盒最大边<100mm | 1 |
| `beam` | 最大边 > 次小边×5（细长特征） | 2 |
| `plate` | 最小边（厚度）< 次小边/5（薄板特征） | 3 |
| `weldment` | 装配体且零件数≤50（无焊缝API时按此近似） | 4 |
| `assembly` | 装配体且零件数>50 | 5 |

**视图策略**:
| 类型 | 视图组合 | 比例策略 |
|------|----------|----------|
| `standard_part` | 主视×1 | 自适应放大（占图幅40-60%） |
| `plate` | 主视+俯视+左视（3视图） | 自适应，占图幅60-80% |
| `beam` | 主视(侧立面)+右视+俯视+轴测图（4视图） | 标准序列选能放下的最大比例 |
| `weldment` | 主视+俯视+左视+轴测图（4视图） | 同beam |
| `assembly` | 主视+右视+俯视+轴测图（4视图） | 同beam |

**比例序列**: `GB_SCALE_RATIOS = [1, 2, 5, 10, 15, 20, 25, 30, 40, 50, 100]`
- 选"全部视图能放进图幅"的最大比例（即比例分母最小）
- LB26长梁（6512mm）预期选中比例≥1:30

**SW视图名适配（中英文环境）**:
| 视图 | 中文环境 | 英文环境 |
|------|----------|----------|
| 主视 | `*前视` | `*Front` |
| 俯视 | `*上视` | `*Top` |
| 左视 | `*左视` | `*Left` |
| 右视 | `*右视` | `*Right` |
| 轴测 | `*等轴测` | `*Isometric` |

- 最多尝试2次，失败记录warning跳过（不阻塞交付）

### 验证状态
| 项目 | 状态 | 说明 |
|------|------|------|
| 单测覆盖 | ✅ 已覆盖 | 类型识别38项+视图策略29项 |
| 真机验证 | ⏳ 未验证 | 端到端流程待 LB26 真机验收 |

## 4.4 纪律与红线
- **诚实原则**: material/mass取不到留空，禁止编造默认值
- **COM线程**: 一律经`run_sw()`排队到专用线程
- **类型判定**: 结果写入result.json，非黑箱操作

## 4.5 测试位置
- Step2可单测（纯计算逻辑，不依赖SW COM）
- **B-M1单测**: `tests/test_generators/test_type_recognition.py` (38项)
- **B-M1单测**: `tests/test_generators/test_view_strategy.py` (29项)

## 4.6 方案B命运
**原样复用**。Step1/2在DXF和方案B路线中完全一致。B-M1在此基础上扩展类型识别能力，支撑智能骨架的视图策略选择。
