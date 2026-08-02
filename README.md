# 专用车辆上装设计图纸智能系统

**当前阶段**: 从「图纸审查」扩展到「图纸生成+审查」闭环

---

## 系统定位

基于AI大模型的专用车辆工程图纸全生命周期系统：

```
3D模型(SolidWorks)
    |
    v
┌─────────────────┐
|  AI生成DWG      |  ← 新增：本阶段重点
|  • 视图投影      |
|  • 尺寸标注      |
|  • BOM生成       |
|  • 技术要求      |
└─────────────────┘
    |
    v
┌─────────────────┐
|  AI审查DWG      |  ← 已有：审查模块测试全通过
|  • DXF解析       |
|  • 规则校验      |
|  • Vision分析    |
└─────────────────┘
    |
    v
┌─────────────────┐
|  人工审核        |  ← 必须：签字出图
|  • 确认修正      |
|  • 签字盖章      |
└─────────────────┘
```

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 3D解析 | pywin32 | 一律走 SW 原生 API（含视图/DXF导出） |
| 2D生成 | ezdxf + Pillow | DXF构建+渲染验证 |
| DWG转换 | ODA File Converter | DXF→DWG |
| AI模型 | Kimi K3 / GPT-4o | Vision+Text多模态 |
| 后端 | FastAPI + WebSocket | REST API + 进度推送 |
| 数据模型 | Pydantic 2.0+ | 类型安全 |

---

## 项目结构

```
drawing-review-system/
├── app/                          # 源代码
│   ├── main.py                   # FastAPI入口
│   ├── core/                     # 配置+异常
│   ├── models/                   # 数据模型
│   ├── parsers/                  # DXF解析+SW解析
│   ├── generators/               # AI生成引擎（新增）
│   ├── ai/                       # AI分析+Prompt
│   ├── rules/                    # 规则引擎
│   ├── services/                 # DWG转换+报告
│   └── renderers/                # PNG渲染
├── tests/                        # 测试（149通过 / 16跳过）
├── docs/                         # 项目文档
│   ├── 00-文档索引.md             # 文档导航
│   ├── 01-AI生成DWG工程图方案设计.md # 生成方案设计
│   ├── 02-业务需求.md             # 交付物+图纸类型
│   ├── 03-图纸类型参考.md         # 图纸类型规范
│   ├── plans/                    # 生产级开发计划（M0-M6）
│   └── archive/                  # 归档资料
├── data/                         # 数据资产
│   ├── samples/                  # 样例图纸
│   └── LB26拉臂装置/             # 案例数据
└── requirements.txt
```

---

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 运行测试
python -m pytest tests/ -v

# 启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## API接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/upload` | POST | 上传DWG/DXF |
| `/api/analyze` | POST | 执行AI审查 |
| `/api/generate` | POST | **新增**：AI生成DWG |
| `/api/result/{task_id}` | GET | 获取结果 |
| `/ws/{task_id}` | WS | 进度推送 |

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [01-AI生成DWG工程图方案设计](docs/01-AI生成DWG工程图方案设计.md) | 生成引擎架构+模块设计+实施路线 |
| [02-业务需求](docs/02-业务需求.md) | 交付物构成+图纸类型定义 |
| [03-图纸类型参考](docs/03-图纸类型参考.md) | 图纸类型规范 |
| [plans/00-开发计划总览](docs/plans/00-开发计划总览.md) | 里程碑 M0-M6 + 质量门禁 |
| [plans/M0-基线巩固报告](docs/plans/M0-基线巩固报告.md) | 环境锁定+测试基线 |

---

**版本**: v2.2  
**更新**: 2026-07-30  
**状态**: M1 框架搭建完成（2026-07-30）— 8步流水线骨架 + /api/generate 契约冻结 + WS进度推送 + /generate 前端页（153测试通过）；M2 核心生成待启动（Step3 视图投影 / Step7 DXF 构建真实执行器）

### 里程碑进度

| 里程碑 | 状态 |
|--------|------|
| M0 基线巩固 | ✅ 完成（2026-07-29） |
| M1 框架搭建 | ✅ 完成（2026-07-30） |
| M2-M6 | ⏳ 未开始 |
