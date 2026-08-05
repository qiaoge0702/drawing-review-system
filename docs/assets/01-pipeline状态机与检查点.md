# 01-pipeline状态机与检查点机制

**位置**: `app/generators/pipeline.py` (~400行)

## 1.1 职责
管理8步生成流水线（**按 `EXECUTION_ORDER=[1,2,3,7,5,6,4,8]` 执行**），提供断点续跑、单步重试、检查点持久化能力。

**步骤依赖链（requires）**：
- Step7 ← [3]（骨架收尾依赖视图投影完成）
- Step5 ← [7]（BOM生成依赖骨架版 SLDDRW）
- Step6 ← [5]（技术要求依赖 BOM 数据就位）
- Step4 ← [6]（尺寸标注依赖表格与技术要求完成，避免早期标注干扰布局）
- Step8 ← [4]（审查闭环依赖终版图纸完成）

## 1.2 每步留痕

每步成功后落盘**三件套**：

| 产物 | 说明 | 示例路径 |
|------|------|----------|
| `preview.png` | SW 真图快照（供 AI 质检与人工目视） | `output/step_3/preview.png` |
| `result.json` | 结构化输出数据（现有） | `output/step_3/result.json` |
| 版本 SLDDRW 副本 | 该步完成时的图纸状态，翻车可精确定位 | 见下表 |

**版本 SLDDRW 命名表**：

| 步骤 | 版本文件名 | 说明 |
|------|-----------|------|
| Step3 | — | 视图投影完成，但 Step7 才存骨架版 |
| Step7 | `step7_skeleton.slddrw` | 骨架版：含视图+标题栏，无标注/表格 |
| Step5 | `step5_table.slddrw` | 表格版（上）：骨架 + BOM 表 |
| Step6 | `step6_table.slddrw` | 表格版（下）：骨架 + BOM 表 + 技术要求 |
| Step4 | `step4_final.slddrw` | 终版：含全部标注，此后导出 DWG/PDF |

**断点重跑**：从某步重跑时，打开前一步版本 SLDDRW 继续（如重跑 Step5 则打开 `step7_skeleton.slddrw`），无需从头重建图纸。

## 1.3 关键接口与契约

| 方法 | 职责 | 输入/输出 |
|------|------|-----------|
| `run(task_id, source_file, config)` | 执行完整流水线 | 返回 `TaskResult` |
| `rerun_from(task_id, from_step, overrides)` | 从指定步骤重跑 | 清除目标步骤及后续检查点 |
| `register_executor(step_name, executor)` | 注册步骤执行器 | executor为 `async (StepContext) -> dict` |

**检查点机制**:
```python
# 保存：仅成功步骤写检查点（原子落盘：tmp + os.replace）
step_dir / "checkpoint.json"   # 包含完整StepResult

# 加载：仅接受status=COMPLETED；失败/损坏/JSON错误一律视为无效
if checkpoint.status != StepStatus.COMPLETED: return None
```

**步骤配置表** (`STEP_CONFIGS`):
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

## 1.4 纪律与红线
- **检查点纪律**: 仅成功步骤写检查点；失败/损坏一律视为无效重跑
- **重试纪律**: 仅Step1支持重试（SW连接不稳定），其余步骤失败即停
- **进度回调**: `_emit_progress` 异常不影响流水线执行

## 1.5 测试位置
- `tests/` 目录待补充pipeline基础测试（当前基线278通过/16跳过不含pipeline专项）

## 1.6 方案B命运
**原样复用**。状态机框架与检查点机制与SW/DXF路线无关，8步结构保持不变。**执行顺序与留痕机制为 2026-08-05 调整后形态**。
