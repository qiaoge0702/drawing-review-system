# 01-pipeline状态机与检查点

**位置**: `app/generators/pipeline.py` (~400行)  
**方案B命运**: 原样复用

---

## 职责

管理8步生成流水线（Step1→8顺序执行），提供断点续跑、单步重试、检查点持久化。

---

## 关键接口

| 方法 | 职责 | 输入/输出 |
|------|------|-----------|
| `run(task_id, source_file, config)` | 执行完整流水线 | 返回 `TaskResult` |
| `rerun_from(task_id, from_step, overrides)` | 从指定步骤重跑 | 清除目标及后续检查点 |
| `register_executor(step_name, executor)` | 注册步骤执行器 | executor: `async (StepContext) -> dict` |

---

## 检查点机制

```python
# 保存：仅成功步骤写检查点（原子落盘：tmp + os.replace）
step_dir / "checkpoint.json"   # 包含完整StepResult

# 加载：仅接受status=COMPLETED；失败/损坏/JSON错误一律视为无效
if checkpoint.status != StepStatus.COMPLETED: return None
```

---

## 步骤配置表

| Step | 名称 | 超时 | 重试 |
|------|------|------|------|
| 1 | SW_LOAD | 60s | ✓ (max=2) |
| 2 | GEOMETRY_PARSE | 120s | ✗ |
| 3 | VIEW_PROJECT | 180s | ✗ |
| 4 | DIMENSION | 120s | ✗ |
| 5 | BOM_GENERATE | 60s | ✗ |
| 6 | TECH_REQUIREMENT | 60s | ✗ |
| 7 | DXF_BUILD | 120s | ✗ |
| 8 | REVIEW | 120s | ✗ |

---

## 纪律与红线

- **检查点纪律**: 仅成功步骤写检查点；失败/损坏一律视为无效重跑
- **重试纪律**: 仅Step1支持重试（SW连接不稳定），其余步骤失败即停
- **进度回调**: `_emit_progress` 异常不影响流水线执行

---

## 测试位置

- `tests/` 目录待补充pipeline基础测试（当前基线278通过/16跳过不含pipeline专项）
