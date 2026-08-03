# 03-sw_drawing封装

**位置**: `app/generators/sw_drawing.py` (~600行)

## 3.1 职责
SW原生真图纸引擎封装，提供两阶段API：
- `create_drawing_sync`: Step3建图纸+真视图+PNG快照
- `finalize_drawing_sync`: Step7写标题栏属性+导出DWG/PDF

## 3.2 关键接口与契约

**Create流程** (`create_drawing_sync`):
```python
{
    "source_file": str,           # 输入: SW零件/装配路径
    "view_names": ["front","top","left"],
    "output_dir": str,
    "bom_rows": int,              # BOM行数（图幅选型用）
    "task_id": str,
}
→ {
    "drawing_path": str,          # step_{n}/output/drawing.slddrw
    "snapshot_path": str,         # step_{n}/output/snapshot.png
    "sheet": "A3",                # 实际图幅
    "sheet_width/height": float,  # mm
    "scale_den": float,           # 比例分母（1:X）
    "positions": {view: {x,y,width,height}},  # 图纸坐标mm
    "view_sizes": {view: {width,height}},     # 实际尺寸mm（未缩放）
    "warnings": [str],            # 如实上报列表
}
```

**7类COM调用**:
| 类别 | API | 说明 |
|------|-----|------|
| 文档 | `OpenDoc6`, `NewDocument`, `CloseAllDocuments` | 类型自动识别，必带Silent |
| 视图 | `CreateDrawViewFromModelView3` | 中文预定义视图名（*前视/*上视/*左视） |
| 显示 | `SetDisplayMode3` | _DISPLAY_HLV=3（隐藏线可见虚线） |
| 比例 | `ScaleDecimal` | 晚期绑定属性写；SetScale2不存在 |
| 位置 | `Position` | 必须用VARIANT safearray传 |
| 测量 | `GetOutline` | 视图轮廓实测（米→mm换算） |
| 保存 | `Extension.SaveAs` | 统一通道，支持SLDDRW/DWG/PDF/PNG |

**迭代重定位机制**:
```python
# 最多3轮收敛，判据0.2mm（_POS_TOL_M = 2e-4米）
for _ in range(3):
    outlines = _measure_outlines(...)   # GetOutline实测
    delta = 目标中心 - 实测中心
    _set_view_position(view, dx_m, dy_m)  # 增量平移
    drw.ForceRebuild3(True)
```

## 3.3 B-M1 新增资产（2026-08-02）

B-M1 智能骨架交付中，`sw_drawing.py` 集成以下新增模块：

### 类型识别集成
- **模块**: `type_recognition.py`
- **功能**: 5类零件自动识别（standard_part/beam/plate/weldment/assembly）
- **输入**: 文件名 + 包围盒尺寸
- **输出**: `type_info` 写入 views.json

### 视图策略库集成
- **模块**: `view_strategy.py`
- **功能**: 
  - 各类型视图组合策略（standard_part 1视图 / plate 3视图 / beam/weldment/assembly 4视图）
  - 主视方向选择（投影包围盒长宽比最大）
  - 比例序列选择（GB标准序列选能放下的最大比例）
  - 中英文SW视图名适配（*前视/*Front、*等轴测/*Isometric 等）

### 布局算法集成
- **位置**: `step3_view_project.py` 调用
- **算法**: 第一角投影摆位
  - 主视：中上居中
  - 俯视：主视正下方，X对齐
  - 右视：主视右侧，Y平齐
  - 轴测：左下角
- **间距**: 20-30mm
- **校验**: 出界则降比例重算（最多3次）

### 验证状态
| 项目 | 状态 | 说明 |
|------|------|------|
| 单测覆盖 | ✅ 已覆盖 | 类型识别38项+视图策略29项+布局算法 |
| 真机验证 | ⏳ 未验证 | COM API 实际调用待 LB26 真机验收 |

## 3.4 纪律与红线
- **中文视图名**: `config.predefined_view_names` 映射（front→*前视），禁止硬编码"*Front"
- **单位换算**: SW内部=米；图纸坐标=mm；布局输出mm→插入时/1000
- **如实原则**: 取不到的数据留空+warnings，禁止编造

## 3.5 测试位置
- 集成测试依赖真实SW环境，mock测试需注入`sw_app`
- **B-M1单测**: `tests/test_generators/test_type_recognition.py` (38项)
- **B-M1单测**: `tests/test_generators/test_view_strategy.py` (29项)
- **B-M1单测**: `tests/test_generators/test_layout.py` (布局算法)

## 3.6 方案B命运
**原样复用**。方案B核心依赖模块，Step3/7直接调用。B-M1在此基础上集成类型识别、视图策略、布局算法。
