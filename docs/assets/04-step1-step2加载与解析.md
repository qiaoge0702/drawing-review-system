# 04-step1-step2加载与解析

**位置**: 
- Step1: `app/generators/steps/step1_sw_load.py` (~140行)
- Step2: `app/generators/steps/step2_geometry_parse.py` (~280行)

**方案B命运**: 原样复用

---

## Step1: SW加载

**职责**: 通过pywin32+SW COM加载文件，提取装配体/零件基本信息。

**输出契约**:
```python
{
    "file_type": "assembly" | "part",
    "name": str, "path": str,
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

---

## Step2: 几何解析

**职责**: 提取BOM+材料+质量，为Step5/6提供数据。

**BOM条目字段**:
```python
{
    "level": int, "name": str, "path": str,
    "quantity": int, "is_suppressed": bool,
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

---

## 纪律与红线

- **诚实原则**: material/mass取不到留空，禁止编造默认值
- **COM线程**: 一律经`run_sw()`排队到专用线程
