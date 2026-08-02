# 05-step6技术要求模板

**位置**: `app/generators/steps/step6_tech_requirement.py` (~260行)  
**方案B命运**: 原样复用

---

## 职责

规则驱动模板系统，非AI。根据template_id渲染技术要求文本块。

---

## 内置模板库

| ID | 名称 | 默认变量 |
|----|------|----------|
| weldment_general | 焊接件通用 | grade, size, stress_relief, ndt |
| machining_general | 机加工件通用 | tolerance_grade, chamfer, fillet, roughness, surface_treatment |

---

## 输入参数

```python
{
    "template_id": "weldment_general",  # 缺省
    "tech_variables": {var: value},     # 覆盖默认值
    "tech_config": {
        "position": {x, y, width, height},  # 覆盖默认位置
        "style": {font_size, line_spacing}   # 覆盖默认样式
    }
}
```

---

## 输出契约

```python
{
    "tech_requirements": {
        "template_id": str,
        "template_name": str,
        "variables": {实际使用的变量},
        "content": [str],          # 渲染后条目列表
        "position": {x, y, width, height},
        "style": {font_size, line_spacing},
    },
    "available_templates": [str],  # 可用模板ID列表
}
```

---

## 变量解析规则

- 占位语法: `{var_name}`
- 优先级: overrides > defaults
- 缺失变量 → `SWException`（禁止静默留空）
- 多余覆盖变量 → warning记录但不报错

---

## 纪律与红线

- 非法值显式报错（与step4/5同款模式）
- 纯文本处理，不依赖SW COM，可无SW环境单测

---

## 测试位置

- `tests/unit/test_step6_tech_requirement.py`（推荐补充）
