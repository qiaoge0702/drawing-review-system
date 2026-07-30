"""
FastAPI 入口模块
提供 REST API + WebSocket 进度推送 + 静态文件服务

API 列表：
  POST   /api/upload     上传 DWG/DXF 文件
  POST   /api/analyze    执行 AI 审查
  GET    /api/result/{task_id}  获取审查结果
  GET    /api/rules      获取生产规则
  PUT    /api/rules      更新生产规则
  POST   /api/generate   创建图纸生成任务（M1 新增）
  GET    /api/generate/{task_id}  生成任务详情
  POST   /api/generate/{task_id}/rerun  单步重跑
  WS     /ws             WebSocket 进度推送
"""

import os
import json
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

from fastapi import (
    FastAPI, UploadFile, File, Form,
    WebSocket, WebSocketDisconnect, HTTPException,
    Request
)
from fastapi.responses import (
    JSONResponse, HTMLResponse, FileResponse, RedirectResponse
)
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import DesignReviewException
from app.parsers.dxf_parser import DXFParser, ParseOptions
from app.parsers.material_extractor import MaterialExtractor
from app.services.dwg_converter import DWGConverter
from app.renderers.dxf_renderer import DXFRenderer
from app.ai.analyzer import AIAnalyzer
from app.rules.engine import RuleEngine, get_rule_engine
from app.services.report_generator import get_report_generator
from app.services.generation_service import GenerationService
from app.routers import generate as generate_router

logger = logging.getLogger(__name__)

# ─── 全局组件 ───
dwg_converter = DWGConverter()
dxf_renderer = DXFRenderer()
rule_engine = get_rule_engine()

# ─── 任务存储（内存，简单实现）───
_tasks: Dict[str, Dict[str, Any]] = {}
_websocket_connections: Dict[str, WebSocket] = {}


async def _generation_notify(task_id: str, event: Dict[str, Any]):
    """生成任务进度 → WS 推送"""
    ws = _websocket_connections.get(task_id)
    if ws:
        try:
            await ws.send_json({"task_id": task_id, **event})
        except Exception:
            pass


# ─── 生成任务服务（单例）───
generation_service = GenerationService(notify=_generation_notify)
generate_router.init_service(generation_service)


# ─── FastAPI 应用 ───
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="专用车辆上装设计图纸智能审查系统",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 生成系统路由（M1）
app.include_router(generate_router.router)

# 静态文件（前端模板）
TEMPLATES_DIR = Path(__file__).parent / "templates"
RULES_FILE = Path(__file__).parent / "rules" / "default_rules.json"

# 确保存储目录存在
settings.storage.upload_dir.mkdir(parents=True, exist_ok=True)
settings.storage.output_dir.mkdir(parents=True, exist_ok=True)
settings.storage.temp_dir.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════
# 页面路由
# ═══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index():
    """主页面"""
    index_path = TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>模板文件不存在</h1>", status_code=404)
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/generate", response_class=HTMLResponse)
async def generate_page():
    """生成系统页面（M1）"""
    page_path = TEMPLATES_DIR / "generate.html"
    if not page_path.exists():
        return HTMLResponse("<h1>模板文件不存在</h1>", status_code=404)
    return HTMLResponse(page_path.read_text(encoding="utf-8"))


# 静态资源
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ═══════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════

@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "dwg_converter": dwg_converter.converter_name,
        "time": datetime.now().isoformat(),
    }


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    上传图纸文件（DWG 或 DXF）

    如果是 DWG，自动转换为 DXF
    返回 task_id 和文件信息
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    ext = Path(file.filename).suffix.lower()
    if ext not in [".dwg", ".dxf"]:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext}，仅支持 .dwg 和 .dxf"
        )

    # 保存上传文件
    task_id = str(uuid.uuid4())[:8]
    upload_dir = settings.storage.upload_dir / task_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_path = upload_dir / file.filename
    content = await file.read()

    if len(content) > settings.storage.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大，最大 {settings.storage.max_upload_size_mb}MB"
        )

    saved_path.write_bytes(content)

    logger.info(f"文件上传成功: {saved_path.name}, 大小: {len(content)} bytes, task_id: {task_id}")

    # 初始化任务
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "uploaded",
        "original_filename": file.filename,
        "original_path": str(saved_path),
        "file_type": ext.lstrip("."),
        "upload_time": datetime.now().isoformat(),
        "progress": 0,
        "steps": [],
    }

    # 如果是 DWG，立即转换
    dxf_path = saved_path
    if ext == ".dwg":
        try:
            await _notify(task_id, "正在转换 DWG → DXF...", 10)

            # 在线程池中执行转换（避免阻塞）
            loop = asyncio.get_event_loop()
            dxf_path = await loop.run_in_executor(
                None,
                dwg_converter.convert,
                saved_path,
                upload_dir,
            )

            _tasks[task_id]["dxf_path"] = str(dxf_path)
            _tasks[task_id]["steps"].append({
                "step": "dwg_convert",
                "status": "done",
                "message": f"DWG→DXF 转换成功（{dwg_converter.converter_name}）",
            })
            await _notify(task_id, "DWG→DXF 转换完成", 20)

        except Exception as e:
            logger.error(f"DWG 转换失败: {e}")
            _tasks[task_id]["status"] = "error"
            _tasks[task_id]["error"] = str(e)
            raise HTTPException(
                status_code=422,
                detail=f"DWG 转换失败: {str(e)}"
            )
    else:
        _tasks[task_id]["dxf_path"] = str(saved_path)

    # 立即解析 DXF 获取基本信息
    try:
        await _notify(task_id, "正在解析 DXF 文件...", 30)

        loop = asyncio.get_event_loop()
        drawing, parser = await loop.run_in_executor(
            None,
            _parse_dxf,
            Path(_tasks[task_id]["dxf_path"]),
        )

        _tasks[task_id]["drawing_info"] = {
            "file_name": drawing.info.file_name,
            "file_size": drawing.info.file_size,
            "metadata": drawing.metadata.model_dump(),
            "extents": {
                "width": drawing.extents.width,
                "height": drawing.extents.height,
            },
            "entity_counts": {
                "line": drawing.entities.line_count,
                "circle": drawing.entities.circle_count,
                "arc": drawing.entities.arc_count,
                "polyline": drawing.entities.polyline_count,
                "lwpolyline": drawing.entities.lwpolyline_count,
                "dimension": drawing.entities.dimension_count,
                "text": drawing.entities.text_count,
                "mtext": drawing.entities.mtext_count,
                "insert": drawing.entities.insert_count,
                "hatch": drawing.entities.hatch_count,
                "ellipse": drawing.entities.ellipse_count,
                "spline": drawing.entities.spline_count,
            },
            "total_entities": drawing.entities.get_total_entity_count(),
            "layer_count": drawing.entities.layer_count,
            "estimated_type": drawing.estimate_drawing_type(),
        }
        _tasks[task_id]["drawing_obj"] = drawing  # 保留对象供后续使用
        _tasks[task_id]["parser_obj"] = parser  # 保留 parser（含 doc/msp）供材料提取用
        _tasks[task_id]["status"] = "parsed"
        await _notify(task_id, "DXF 解析完成", 40)

        # 自动执行规则检查
        try:
            await _notify(task_id, "正在执行规则检查...", 45)
            rule_issues = rule_engine.check_drawing(_tasks[task_id]["drawing_info"])
            _tasks[task_id]["rule_issues"] = [i.model_dump() for i in rule_issues]
            _tasks[task_id]["rule_check_summary"] = {
                "total": len(rule_issues),
                "critical": sum(1 for i in rule_issues if i.severity.value == "critical"),
                "warning": sum(1 for i in rule_issues if i.severity.value == "warning"),
                "info": sum(1 for i in rule_issues if i.severity.value == "info"),
            }
            await _notify(task_id, f"规则检查完成，发现 {len(rule_issues)} 个问题", 48)
        except Exception as e:
            logger.warning(f"规则检查失败（不影响主流程）: {e}")
            _tasks[task_id]["rule_issues"] = []
            _tasks[task_id]["rule_check_summary"] = {"total": 0, "critical": 0, "warning": 0, "info": 0}

    except Exception as e:
        logger.error(f"DXF 解析失败: {e}")
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["error"] = str(e)
        raise HTTPException(
            status_code=422,
            detail=f"DXF 解析失败: {str(e)}"
        )

    return _get_task_summary(task_id)


class AnalyzeRequest(BaseModel):
    """分析请求"""
    task_id: str
    api_key: str
    model: str = "gpt-4o"
    base_url: Optional[str] = None
    provider: Optional[str] = None


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    """
    执行 AI 审查分析

    流程：
    1. DXF → PNG 渲染
    2. 组装 DXF JSON + 规则 JSON
    3. 调用 AI API
    4. 返回审查结果
    """
    task_id = req.task_id
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    task = _tasks[task_id]
    task["status"] = "analyzing"

    try:
        dxf_path = Path(task["dxf_path"])
        output_dir = settings.storage.output_dir / task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # 步骤1: DXF → PNG
        await _notify(task_id, "正在渲染图纸图片...", 50)
        loop = asyncio.get_event_loop()
        png_path = await loop.run_in_executor(
            None,
            dxf_renderer.render,
            dxf_path,
            output_dir / (dxf_path.stem + ".png"),
        )
        task["png_path"] = str(png_path)
        await _notify(task_id, "图纸图片渲染完成", 60)

        # 步骤2: 准备数据
        await _notify(task_id, "正在准备审查数据...", 65)

        # 获取解析后的 drawing 对象
        drawing = task.get("drawing_obj")
        if drawing is None:
            drawing, _ = await loop.run_in_executor(
                None, _parse_dxf, dxf_path
            )

        # 构建 DXF JSON
        dxf_json = _build_dxf_json(drawing)

        # 加载规则
        rules_json = _load_rules()

        await _notify(task_id, "正在调用 AI 进行审查...", 70)

        # 步骤3: AI 分析
        analyzer = AIAnalyzer(
            api_key=req.api_key,
            model=req.model,
            base_url=req.base_url,
            provider=req.provider,
        )

        result = await loop.run_in_executor(
            None,
            analyzer.analyze,
            png_path,
            dxf_json,
            rules_json,
            task.get("original_filename", ""),
        )

        task["result"] = result
        task["status"] = "completed"
        task["completed_time"] = datetime.now().isoformat()
        await _notify(task_id, "AI 审查完成", 100)

        return _get_task_summary(task_id)

    except Exception as e:
        logger.error(f"AI 分析失败: {e}", exc_info=True)
        task["status"] = "error"
        task["error"] = str(e)
        await _notify(task_id, f"分析失败: {str(e)}", -1)
        raise HTTPException(
            status_code=500,
            detail=f"AI 分析失败: {str(e)}"
        )


@app.get("/api/result/{task_id}")
async def get_result(task_id: str):
    """获取审查结果"""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    task = _tasks[task_id]
    return _get_task_summary(task_id)


@app.get("/api/image/{task_id}")
async def get_image(task_id: str):
    """获取渲染的 PNG 图片"""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    png_path = _tasks[task_id].get("png_path")
    if not png_path or not Path(png_path).exists():
        raise HTTPException(status_code=404, detail="图片不存在")

    return FileResponse(png_path, media_type="image/png")


@app.get("/api/rules")
async def get_rules():
    """获取生产规则"""
    return _load_rules()


@app.put("/api/rules")
async def update_rules(rules: Dict[str, Any]):
    """更新生产规则"""
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
    return {"status": "ok", "message": "规则已更新"}


@app.get("/api/models")
async def get_models():
    """获取支持的模型列表"""
    return {
        "openai": [
            {"id": "gpt-4o", "name": "GPT-4o", "vision": True, "json_mode": True},
            {"id": "gpt-4o-mini", "name": "GPT-4o mini", "vision": True, "json_mode": True},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "vision": True, "json_mode": True},
        ],
        "kimi": [
            {"id": "kimi-k3", "name": "Kimi K3 (推荐)", "vision": True, "json_mode": False},
            {"id": "kimi-k2.6", "name": "Kimi K2.6", "vision": True, "json_mode": False},
            {"id": "kimi-k2.7-code", "name": "Kimi K2.7 Code", "vision": True, "json_mode": False},
            {"id": "moonshot-v1-128k-vision-preview", "name": "Moonshot Vision (旧版)", "vision": True, "json_mode": False},
        ],
        "custom": [],
    }


@app.get("/api/rule-check/{task_id}")
async def get_rule_check(task_id: str):
    """获取规则检查结果"""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    task = _tasks[task_id]
    return {
        "task_id": task_id,
        "summary": task.get("rule_check_summary", {}),
        "issues": task.get("rule_issues", []),
    }


@app.get("/api/report/{task_id}")
async def get_report(task_id: str):
    """生成并下载审查报告（Markdown）"""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    task = _tasks[task_id]

    # 组装报告数据
    drawing_info = task.get("drawing_info", {})
    rule_issues = task.get("rule_issues", [])
    rule_summary = task.get("rule_check_summary", {})
    ai_result = task.get("result")

    # 获取材料数据（如果已解析）
    materials = None
    parser = task.get("parser_obj")
    if parser and parser.doc and parser.msp:
        try:
            loop = asyncio.get_event_loop()
            materials = await loop.run_in_executor(None, _extract_materials, parser)
        except Exception as e:
            logger.warning(f"材料数据提取失败: {e}")

    # 生成报告
    generator = get_report_generator()
    report_md = generator.generate_markdown(
        drawing_info=drawing_info,
        rule_issues=rule_issues,
        rule_summary=rule_summary,
        ai_result=ai_result,
        materials=materials,
    )

    # 保存到输出目录
    output_dir = settings.storage.output_dir / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"审查报告_{task_id}.md"
    report_path.write_text(report_md, encoding="utf-8")

    logger.info(f"审查报告已生成: {report_path}")

    return {
        "task_id": task_id,
        "report_path": str(report_path),
        "report_content": report_md,
    }


@app.get("/api/materials/{task_id}")
async def get_materials(task_id: str):
    """
    获取从 DXF 提取的结构化材料数据

    返回：
    - BOM 明细表（INSERT + ATTRIB）
    - 尺寸标注汇总（DIMENSION）
    - 文字内容分类（技术要求/注释/标题栏/其他）
    - 焊接符号清单（MLEADER/LEADER/INSERT）
    """
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    task = _tasks[task_id]
    parser = task.get("parser_obj")

    if parser is None or parser.doc is None or parser.msp is None:
        raise HTTPException(
            status_code=400,
            detail="图纸未解析或解析数据不可用，请重新上传"
        )

    # 在线程池中执行材料提取（避免阻塞）
    loop = asyncio.get_event_loop()
    materials = await loop.run_in_executor(
        None,
        _extract_materials,
        parser,
    )

    return materials


def _extract_materials(parser) -> Dict[str, Any]:
    """提取材料数据（同步函数，在线程池中执行）"""
    extractor = MaterialExtractor(parser.doc, parser.msp)
    return extractor.extract_all()


# ═══════════════════════════════════════════
# WebSocket

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """WebSocket 进度推送"""
    await websocket.accept()
    _websocket_connections[task_id] = websocket

    try:
        # 如果任务已完成，发送当前状态
        if task_id in _tasks:
            task = _tasks[task_id]
            await websocket.send_json({
                "type": "status",
                "task_id": task_id,
                "status": task.get("status"),
                "progress": task.get("progress", 0),
                "message": task.get("steps", [{}])[-1].get("message", "") if task.get("steps") else "",
            })

        # 保持连接，等待消息
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        logger.debug(f"WebSocket 断开: {task_id}")
    finally:
        # S3: 仅在注册表中仍是本连接时才移除（同 task_id 多连接防误删）
        if _websocket_connections.get(task_id) is websocket:
            _websocket_connections.pop(task_id, None)


async def _notify(task_id: str, message: str, progress: int):
    """推送进度通知"""
    if task_id in _tasks:
        _tasks[task_id]["progress"] = progress
        _tasks[task_id]["steps"].append({
            "step": f"step_{len(_tasks[task_id]['steps'])}",
            "message": message,
            "progress": progress,
            "time": datetime.now().isoformat(),
        })

    ws = _websocket_connections.get(task_id)
    if ws:
        try:
            await ws.send_json({
                "type": "progress",
                "task_id": task_id,
                "message": message,
                "progress": progress,
            })
        except Exception:
            pass


# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

def _parse_dxf(dxf_path: Path):
    """解析 DXF 文件（同步函数，在线程池中执行）"""
    parser = DXFParser(dxf_path)
    drawing = parser.parse(ParseOptions())
    # 返回 drawing 和 parser（含 doc/msp 供材料提取用）
    return drawing, parser


def _build_dxf_json(drawing) -> Dict[str, Any]:
    """
    从 Drawing 对象构建 AI 分析用的 JSON 数据

    包含：元数据、图纸范围、实体统计、关键实体数据
    """
    data = {
        "file_info": {
            "file_name": drawing.info.file_name,
            "file_type": drawing.info.file_type,
            "file_size": drawing.info.file_size,
        },
        "metadata": drawing.metadata.model_dump(),
        "extents": {
            "width_mm": round(drawing.extents.width, 2),
            "height_mm": round(drawing.extents.height, 2),
            "min_x": round(drawing.extents.min_x, 2),
            "min_y": round(drawing.extents.min_y, 2),
            "max_x": round(drawing.extents.max_x, 2),
            "max_y": round(drawing.extents.max_y, 2),
        },
        "entity_summary": {
            "total": drawing.entities.get_total_entity_count(),
            "line_count": drawing.entities.line_count,
            "circle_count": drawing.entities.circle_count,
            "arc_count": drawing.entities.arc_count,
            "polyline_count": drawing.entities.polyline_count,
            "lwpolyline_count": drawing.entities.lwpolyline_count,
            "dimension_count": drawing.entities.dimension_count,
            "text_count": drawing.entities.text_count,
            "mtext_count": drawing.entities.mtext_count,
            "insert_count": drawing.entities.insert_count,
            "hatch_count": drawing.entities.hatch_count,
            "ellipse_count": drawing.entities.ellipse_count,
            "spline_count": drawing.entities.spline_count,
            "layer_count": drawing.entities.layer_count,
            "estimated_type": drawing.estimate_drawing_type(),
        },
        "layers": [
            {"name": l.name, "color": l.color, "linetype": l.linetype}
            for l in drawing.entities.layers[:50]  # 限制数量
        ],
    }

    # 添加关键实体数据（限制数量避免 token 过多）
    entities = drawing.entities.entities
    for etype, items in entities.items():
        if etype in ("line", "circle", "arc", "dimension", "text", "mtext"):
            data[f"entities_{etype}"] = items[:100]
        elif etype in ("insert", "hatch", "spline", "ellipse"):
            data[f"entities_{etype}"] = items[:50]
        else:
            data[f"entities_{etype}"] = items[:30]

    return data


def _load_rules() -> Dict[str, Any]:
    """加载生产规则"""
    if RULES_FILE.exists():
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _get_task_summary(task_id: str) -> Dict[str, Any]:
    """获取任务摘要"""
    task = _tasks[task_id]
    result = {
        "task_id": task_id,
        "status": task.get("status"),
        "original_filename": task.get("original_filename"),
        "file_type": task.get("file_type"),
        "progress": task.get("progress", 0),
        "steps": task.get("steps", []),
        "drawing_info": task.get("drawing_info"),
        "error": task.get("error"),
    }
    if "result" in task:
        result["result"] = task["result"]
    if "rule_issues" in task:
        result["rule_issues"] = task["rule_issues"]
        result["rule_check_summary"] = task.get("rule_check_summary", {})
    return result


# ─── 启动事件 ───

@app.on_event("startup")
async def startup():
    """应用启动"""
    settings.setup_logging()
    logger.info(f"=== {settings.app_name} v{settings.app_version} ===")
    logger.info(f"DWG 转换器: {dwg_converter.converter_name}")
    logger.info(f"存储目录: upload={settings.storage.upload_dir}, "
                f"output={settings.storage.output_dir}")
    logger.info(f"监听: http://{settings.host}:{settings.port}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
