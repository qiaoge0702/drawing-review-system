# 专用车辆上装设计图纸智能审查系统

**Special Vehicle Superstructure Design Review System**

基于 AI 大模型 + 结构化数据提取的专用车辆工程图纸智能审查系统。

---

## 当前状态（2026-07-24 更新）

### ✅ 已完成（v1.1）

| 模块 | 文件 | 功能 | 状态 |
|------|------|------|------|
| **核心配置** | `app/core/config.py` | 环境配置、日志 | ✅ |
| | `app/core/exceptions.py` | 业务异常、错误码 | ✅ |
| **数据模型** | `app/models/vehicle.py` | 车型枚举、底盘参数 | ✅ |
| | `app/models/structure.py` | 副车架/厢体/罐体 | ✅ |
| | `app/models/drawing.py` | 图纸元数据、实体 | ✅ |
| | `app/models/check_result.py` | 审查结果、问题分级 | ✅ |
| **DXF 解析** | `app/parsers/dxf_parser.py` | 多层容错解析 | ✅ 16测试 |
| | `app/parsers/entity_extractor.py` | 14种实体提取 | ✅ 27测试 |
| | `app/parsers/metadata_extractor.py` | 标题栏提取 | ✅ 17测试 |
| **材料提取** | `app/parsers/material_extractor.py` | BOM/尺寸/焊接/技术要求 | ✅ |
| **DWG 转换** | `app/services/dwg_converter.py` | ODA/LibreDWG 双引擎 | ✅ |
| **PNG 渲染** | `app/renderers/dxf_renderer.py` | ezdxf+Pillow 渲染 | ✅ |
| **AI 分析** | `app/ai/analyzer.py` | Vision+Text 多 Provider | ✅ |
| **规则引擎** | `app/rules/engine.py` | GB 1589/图纸规范硬校验 | ✅ 10测试 |
| **报告生成** | `app/services/report_generator.py` | Markdown 审查报告 | ✅ |
| **Web 服务** | `app/main.py` | FastAPI + WebSocket | ✅ |

**单元测试：112 个，全部通过**

### ⏳ 待开发

- 人工审核批注（暂缓）
- PDF 报告导出（当前为 Markdown）
- 生产级部署（数据库落库）

---

## 核心能力

```
用户上传 DWG/DXF
    │
    ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  DWG→DXF 转换   │ →   │  DXF 解析引擎   │ →   │  结构化数据提取  │
│  ODA/LibreDWG   │     │  14种实体+容错  │     │  BOM/尺寸/焊接  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │
                              ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   PNG 渲染      │     │   AI 智能审查   │ ←   │   生产规则库    │
│  2048x2048      │     │  Kimi/GPT-4o    │     │   GB 标准模板   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   审查结果展示   │
                    │  问题列表+建议  │
                    └─────────────────┘
```

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| DWG 转换 | LibreDWG / ODA File Converter | 命令行调用，自动切换 |
| DXF 解析 | ezdxf 1.4+ | 纯 Python，14 种实体 |
| 数据模型 | Pydantic 2.0+ | 类型安全 |
| AI 模型 | Kimi K3 / GPT-4o / 自定义 | Vision + Text |
| 图像渲染 | ezdxf + Pillow / matplotlib | PNG 生成 |
| 后端框架 | FastAPI | REST API + WebSocket |
| 前端 | 原生 HTML/CSS/JS | 单页应用 |
| 测试 | pytest | 102 测试通过 |

---

## 快速开始

### 环境要求
- Python 3.10+
- LibreDWG (`brew install libredwg`) 或 ODA File Converter
- Kimi API Key 或 OpenAI API Key

### 安装依赖
```bash
cd /Users/liuqiao/Workbuddy/工程图纸审核
source /Users/liuqiao/.workbuddy/binaries/python/envs/default/bin/activate
pip install -r requirements.txt
```

### 运行测试
```bash
python -m pytest tests/ -v
# 预期：102 passed
```

### 启动服务
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

浏览器打开 http://localhost:8000

---

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/upload` | POST | 上传 DWG/DXF |
| `/api/analyze` | POST | 执行 AI 审查 |
| `/api/result/{task_id}` | GET | 获取审查结果 |
| `/api/rule-check/{task_id}` | GET | 获取规则检查结果 |
| `/api/materials/{task_id}` | GET | 获取材料数据（BOM/尺寸/焊接） |
| `/api/report/{task_id}` | GET | 生成并下载审查报告 |
| `/api/rules` | GET/PUT | 生产规则管理 |
| `/api/models` | GET | 支持的 AI 模型列表 |
| `/ws/{task_id}` | WS | 进度推送 |

---

## 项目结构

```
工程图纸审核/
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── core/                   # 配置 + 异常
│   ├── models/                 # 数据模型
│   ├── parsers/                # DXF 解析 + 材料提取
│   ├── services/               # DWG 转换
│   ├── renderers/              # PNG 渲染
│   ├── ai/                     # AI 分析
│   ├── rules/                  # 生产规则 + 规则引擎
│   └── templates/              # 前端页面
├── tests/                      # 测试（102 通过）
├── docs/                       # 文档
│   └── 交付物材料构成说明.md    # 业务需求文档
├── uploads/                    # 上传缓存
├── output/                     # 输出缓存
└── requirements.txt
```

---

## 文档索引

- [交付物材料构成说明](./docs/交付物材料构成说明.md) - 业务需求与审查范围
- [工作日志](./.workbuddy/memory/2026-07-22.md) - 开发历程

---

**版本**: v1.1  
**状态**: 核心功能完成，规则引擎+报告导出已上线  
**更新**: 2026-07-24
