# 03-sw_drawing封装

**位置**: `app/generators/sw_drawing.py` (~600行)  
**方案B命运**: 原样复用（方案B核心依赖）

---

## 职责

SW原生真图纸引擎封装，提供两阶段API：
- `create_drawing_sync`: Step3建图纸+真视图+PNG快照
- `finalize_drawing_sync`: Step7写标题栏属性+导出DWG/PDF

---

## 7类COM调用

| 类别 | API | 说明 |
|------|-----|------|
| 文档 | `OpenDoc6`, `NewDocument`, `CloseAllDocuments` | 类型自动识别，必带Silent |
| 视图 | `CreateDrawViewFromModelView3` | 中文预定义视图名（*前视/*上视/*左视） |
| 显示 | `SetDisplayMode3` | _DISPLAY_HLV=3（隐藏线可见虚线） |
| 比例 | `ScaleDecimal` | 晚期绑定属性写；SetScale2不存在 |
| 位置 | `Position` | 必须用VARIANT safearray传 |
| 测量 | `GetOutline` | 视图轮廓实测（米→mm换算） |
| 保存 | `Extension.SaveAs` | 统一通道，支持SLDDRW/DWG/PDF/PNG |

---

## Create流程输出契约

```python
{
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

---

## 迭代重定位机制

```python
# 最多3轮收敛，判据0.2mm（_POS_TOL_M = 2e-4米）
for _ in range(3):
    outlines = _measure_outlines(...)   # GetOutline实测
    delta = 目标中心 - 实测中心
    _set_view_position(view, dx_m, dy_m)  # 增量平移
    drw.ForceRebuild3(True)
```

---

## 纪律与红线

- **中文视图名**: `config.predefined_view_names` 映射（front→*前视），禁止硬编码"*Front"
- **单位换算**: SW内部=米；图纸坐标=mm；布局输出mm→插入时/1000
- **如实原则**: 取不到的数据留空+warnings，禁止编造
