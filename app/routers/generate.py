"""
生成系统 API 路由

API 契约（M1 冻结）：
  POST /api/generate                    创建生成任务
  GET  /api/generate                    任务列表
  GET  /api/generate/{task_id}          任务详情（8步状态+产物）
  POST /api/generate/{task_id}/rerun    从指定步骤重跑
  GET  /api/generate/{task_id}/artifacts/{step}/{filename}  下载步骤产物
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.models.generation import TaskConfig, TaskResult
from app.services.generation_service import GenerationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/generate", tags=["generate"])

# 由 main.py 注入
_service: Optional[GenerationService] = None


def init_service(service: GenerationService):
    global _service
    _service = service


def _svc() -> GenerationService:
    if _service is None:
        raise HTTPException(status_code=503, detail="生成服务未初始化")
    return _service


# ─── 请求/响应模型（契约） ───


class GenerateRequest(BaseModel):
    """创建生成任务请求"""
    source_file: str = Field(..., description="源 SW 文件绝对路径（.SLDASM/.SLDPRT）")
    config: TaskConfig = Field(default_factory=TaskConfig)


class GenerateResponse(BaseModel):
    """创建生成任务响应"""
    task_id: str
    status: str
    message: str = "任务已排队"


class RerunRequest(BaseModel):
    """重跑请求"""
    from_step: int = Field(..., ge=1, le=8, description="从第几步开始重跑")
    parameter_overrides: Optional[Dict[str, Any]] = Field(
        default=None, description="任务配置字段覆盖"
    )


class TaskListResponse(BaseModel):
    """任务列表响应"""
    total: int
    tasks: List[TaskResult]


# ─── 路由 ───


@router.post("", response_model=GenerateResponse, status_code=202)
async def create_generate_task(req: GenerateRequest):
    """创建生成任务（异步执行，202 Accepted）"""
    path = Path(req.source_file)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"源文件不存在: {req.source_file}")
    if path.suffix.lower() not in (".sldasm", ".sldprt"):
        raise HTTPException(
            status_code=400, detail=f"不支持的文件类型: {path.suffix}，仅支持 .SLDASM/.SLDPRT"
        )
    task_id = await _svc().create_task(str(path), req.config)
    return GenerateResponse(task_id=task_id, status="queued")


@router.get("", response_model=TaskListResponse)
async def list_generate_tasks():
    """任务列表"""
    tasks = _svc().list_tasks()
    return TaskListResponse(total=len(tasks), tasks=tasks)


@router.get("/{task_id}", response_model=TaskResult)
async def get_generate_task(task_id: str):
    """任务详情"""
    try:
        return _svc().get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")


@router.post("/{task_id}/rerun", response_model=GenerateResponse, status_code=202)
async def rerun_generate_task(task_id: str, req: RerunRequest):
    """从指定步骤重跑"""
    try:
        await _svc().rerun_task(task_id, req.from_step, req.parameter_overrides)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return GenerateResponse(task_id=task_id, status="queued", message=f"已从 Step{req.from_step} 重跑")


@router.get("/{task_id}/artifacts/{step}/{filename}")
async def download_artifact(task_id: str, step: int, filename: str):
    """下载步骤产物（防路径穿越）"""
    if not 1 <= step <= 8:
        raise HTTPException(status_code=400, detail=f"step 必须在 1-8 之间: {step}")
    try:
        task = _svc().get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    step_result = next((s for s in task.steps if s.step == step), None)
    if step_result is None:
        raise HTTPException(status_code=404, detail=f"步骤 {step} 尚未执行")

    artifact = next((a for a in step_result.artifacts if a.name == filename), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"产物不存在: {filename}")

    file_path = Path(artifact.path).resolve()
    # S1 纵深防御：产物必须位于该任务步骤目录内
    step_dir = (settings.storage.temp_dir / "generate" / task_id / f"step_{step}").resolve()
    if not file_path.is_relative_to(step_dir):
        raise HTTPException(status_code=403, detail="非法产物路径")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="产物文件已被清理")
    return FileResponse(file_path, filename=filename)
