# CONTEXT.md - 项目上下文舱单

> 项目切换标准：主代理切入本项目时，读本文件 + 最新进度快照即可开工。
> 本文件只写稳定信息，动态进度见 `进度状态-YYYY-MM-DD.md`（读最新一份）。

## 1. 项目定位

专用车辆上装设计图纸智能系统 —— 基于 AI 大模型的图纸全生命周期系统：`3D模型(SolidWorks) → AI生成DWG → AI审查DWG → 人工审核签字`。审查模块已完成，当前主攻生成模块。

## 2. 技术栈快照

| 层级 | 技术 | 版本锁定 |
|------|------|----------|
| 3D解析 | pywin32 (SW COM)，**一律走 SW 原生 API** | pywin32 312 |
| 2D生成 | ezdxf + Pillow | ezdxf 1.4.4 |
| DWG转换 | ODA File Converter | — |
| AI模型 | Kimi K3 / GPT-4o（多模态） | — |
| 后端 | FastAPI + Pydantic 2.0 + asyncio | FastAPI 0.140.7 / Python 3.12.10 |
| 前端 | Vanilla JS + ES Module（M1-M2）；Vue3+TS+Vite（M3 起切换） | — |
| SW 版本 | SolidWorks 2025 正式版（COM 实测可连接） | — |

## 3. 目录拓扑

```
drawing-review-system/
├── app/
│   ├── main.py            # FastAPI 入口（审查API + 生成路由挂载 + WS）
│   ├── core/              # 配置 + 异常体系（ErrorCode 枚举）
│   ├── models/            # Pydantic 模型（generation.py = 生成契约）
│   ├── parsers/           # DXF解析 + SW解析(sw_parser)
│   ├── generators/        # 生成引擎：pipeline.py(8步状态机) + sw_com.py(COM线程) + steps/
│   ├── routers/           # generate.py（生成API，契约已冻结）
│   ├── services/          # generation_service(任务队列) + dwg转换 + 报告
│   ├── ai/  rules/  renderers/
│   ├── templates/         # index.html(审查) + generate.html(生成)
│   └── static/generate/js/ # 前端 ES Module 五层：api/store/ws/views/main
├── tests/                 # pytest，基线 153 通过 / 16 跳过
├── docs/plans/            # 生产级开发计划（00总览 + 01-07子文档 + M0报告）
├── docs/reviews/          # 代码审查报告
├── scripts/dry_run_pipeline.py  # M1 门禁验证脚本
├── LB26拉臂装置/          # 案例数据（M2 验收目标：LB26 焊接件）
└── 进度状态-YYYY-MM-DD.md # 进度快照（取代关系，读最新）
```

## 4. 当前里程碑 + 下一步

- **当前**: M1 框架搭建 ✅ 完成（2026-07-30）；M2 包1/包2/包3 已落地（测试 281 通过）
- **下一步**: Step3 引擎换 **SW 原生导出 DXF** 方案 + Step4 标注保守放置（待人工清单）——2026-08-01 老板批示，详见 `进度状态-2026-08-01.md`
- **硬约束**: 无许可到期风险；**SW API 原生优先**（凡 SW 原生能做的事禁止自研，见 MEMORY.md 2026-08-01 铁律）

## 5. 禁区与约定

- **API 契约已冻结**：`/api/generate` 系列路由的请求/响应模型不得破坏式变更（只能加字段，不能改/删字段）
- **SW COM 纪律**：一切 COM 调用必须经 `app/generators/sw_com.py` 的单线程执行器，禁止在 async 函数里直接同步调用
- **前端纪律**：M1-M2 坚持 Vanilla JS + ES Module 分层，禁止提前引入 Vue3/构建链
- **检查点纪律**：仅成功步骤写检查点；失败/损坏检查点一律视为无效重跑
- **占位 executor**：`steps/placeholders.py` 的 Step3-8 是 M1 空骨架，M2 逐文件替换为真实执行器，替换时保持 executor 接口签名不变
- **测试红线**：153 通过 / 16 跳过为基线，任何变更不得打破；新增能力必须带测试
- 全局铁律见主代理 SOUL.md（严禁蔓延 ≤5文件/≤200行、完全遵循设计、闭环留痕）
