# AGENTS.md

## 开工顺序

每次进入本仓库，先阅读以下文件：

1. `CONTEXT.md`：稳定的项目背景、技术路线与红线；
2. `docs/当前状态.md`：唯一的动态进度来源；
3. `docs/交接记录.md`：上一轮的交接与待办；
4. 与任务相关的现行设计文档（从 `docs/00-文档索引.md` 进入）。

历史进度快照、`docs/reviews/` 和 `docs/archive/` 仅作证据或追溯使用，不能替代当前状态或现行方案。

## 项目与环境边界

- 项目目标：用 SolidWorks 原生 API 生成 SLDDRW/DWG/PDF，并对图纸进行审查；人工签发是最终门禁。
- Mac：日常 Python/前端/文档工作、DXF 路线和不依赖 Windows COM 的测试。
- Windows：`pywin32`、SolidWorks COM、真实 SLDASM/SLDDRW 生成和目视验收。
- SolidWorks COM 必须经 `app/generators/sw_com.py` 的单线程执行器访问；不得并发调用、不得强杀 SW 进程。
- `LB26拉臂装置/` 是验收样本与 CAD 资产，默认只读。未经用户明确授权，不得改名、覆盖、删除或批量处理。
- `uploads/`、`output/`、`temp/`、`logs/` 是本机运行产物；不得作为源代码提交或交接介质。
- 密钥和本机路径仅放在 `.env` 或操作系统环境变量中；不得提交或打印其值。

## 修改规则

1. 先检查工作区已有改动；不覆盖、回滚或格式化与任务无关的用户改动。
2. 以最小改动完成任务。修改 API、Pydantic 模型、流水线步骤时，检查调用方和对应测试。
3. `/api/generate` 契约只加不改；检查点只在成功步骤落盘；缺失数据应如实记录 warning，不得编造或静默伪造成功。
4. 真实 SW 验证不能在非 Windows/SolidWorks 环境假称已完成；明确列为待验证项。
5. 不修改审查模块（`app/parsers/`、`app/ai/`、`app/rules/`、`app/renderers/`），除非任务明确涉及审查能力。

## 验证命令

安装通用开发依赖：

```bash
python -m pip install -r requirements-dev.txt
```

运行当前可收集的测试：

```bash
python -m pytest tests/ -v
```

Windows 真机环境额外安装：

```powershell
python -m pip install -r requirements-windows.txt
```

当前限制：部分生成步骤在模块导入时依赖 `pythoncom`/`win32com`；在 Mac 上尚不能仅用 `-m "not sw_real"` 实现完整测试收集。不要伪造跨平台测试结论；相关兼容性改造应作为单独任务处理。

## 多 Agent 与交接

- 日常最多并行 3 个子 agent；优先按文件边界拆分。
- 后端可修改 `app/services/`、`app/routers/` 和已明确认领的步骤文件；前端只修改 `app/templates/`、`app/static/`；测试 agent 默认只修改 `tests/`；文档 agent 默认只修改 `docs/`。
- 共享文件只由主 agent 集成：`app/main.py`、`app/generators/pipeline.py`、`app/models/generation.py`、`app/services/generation_service.py`、`requirements*.txt`、`README.md`、`AGENTS.md`、`CONTEXT.md`。
- 真正的 SW COM/CAD 操作全局串行，只能由一个 agent 执行。
- 每个子任务交接时报告：修改文件、验证命令及结果、未验证项与原因、风险/阻塞、建议下一步。
- Codex 与 OpenClaw 不同时写同一工作区。切换工具或设备前，应更新 `docs/当前状态.md`、追加 `docs/交接记录.md`，并将可交接改动提交到 Git 分支。
