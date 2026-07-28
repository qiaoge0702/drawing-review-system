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
|  AI审查DWG      |  ← 已有：112测试通过
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
| 3D解析 | pywin32 / trimesh | SW API或STEP解析 |
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
├── tests/                        # 测试（112通过）
├── docs/                         # 项目文档
│   ├── 00-项目总览.md             # 本文档
│   ├── 01-AI生成DWG方案.md        # 生成方案设计
│   ├── 02-业务需求.md             # 交付物+图纸类型
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
| [01-AI生成DWG方案](docs/01-AI生成DWG方案.md) | 生成引擎架构+模块设计+实施路线 |
| [02-业务需求](docs/02-业务需求.md) | 交付物构成+图纸类型定义 |

---

**版本**: v2.0  
**更新**: 2026-07-28  
**状态**: 审查系统稳定，生成系统方案确定，进入开发
