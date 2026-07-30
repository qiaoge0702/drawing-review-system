"""
生成任务服务

管理生成流水线的后台执行、任务注册表、进度推送桥接。
单例模式，由 main.py 注入 WS 通知函数。

并发约束：全局串行执行（SW COM 单线程限制），任务排队处理。
"""

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.generators.pipeline import GeneratePipeline
from app.generators.steps import (
    SWLoadExecutor,
    GeometryParseExecutor,
    ViewProjectExecutor,
    DimensionExecutor,
    BomGenerateExecutor,
    TechRequirementExecutor,
    DxfBuildExecutor,
    ReviewExecutor,
)
from app.models.generation import (
    PipelineState,
    StepName,
    TaskConfig,
    TaskResult,
)

logger = logging.getLogger(__name__)

# WS 通知函数类型：async (task_id, payload: dict) -> None
NotifyFunc = Callable[[str, Dict[str, Any]], Awaitable[None]]


class GenerationService:
    """生成任务管理器（单例）"""

    def __init__(self, notify: Optional[NotifyFunc] = None):
        self._notify = notify
        self._tasks: Dict[str, TaskResult] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # ─── 流水线构建 ───

    def _build_pipeline(self) -> GeneratePipeline:
        """构建注册了全部 8 步执行器的流水线"""
        pipeline = GeneratePipeline()
        pipeline.register_executor(StepName.SW_LOAD, SWLoadExecutor())
        pipeline.register_executor(StepName.GEOMETRY_PARSE, GeometryParseExecutor())
        pipeline.register_executor(StepName.VIEW_PROJECT, ViewProjectExecutor())
        pipeline.register_executor(StepName.DIMENSION, DimensionExecutor())
        pipeline.register_executor(StepName.BOM_GENERATE, BomGenerateExecutor())
        pipeline.register_executor(StepName.TECH_REQUIREMENT, TechRequirementExecutor())
        pipeline.register_executor(StepName.DXF_BUILD, DxfBuildExecutor())
        pipeline.register_executor(StepName.REVIEW, ReviewExecutor())
        pipeline.set_progress_callback(self._on_progress)
        return pipeline

    async def _on_progress(self, task_id: str, event: Dict[str, Any]):
        """流水线进度回调 → 更新注册表 + 推送 WS"""
        # 同步内存状态
        task = self._tasks.get(task_id)
        if task is not None:
            task.current_step = event.get("current_step", task.current_step)
            task.progress = event.get("progress", task.progress)
            if event.get("type") == "finished":
                task.status = PipelineState(event["status"])
                task.error = event.get("error")

        if self._notify:
            await self._notify(task_id, event)

    # ─── 任务生命周期 ───

    async def create_task(self, source_file: str, config: TaskConfig) -> str:
        """创建生成任务并排队，返回 task_id"""
        task_id = uuid.uuid4().hex[:12]
        self._tasks[task_id] = TaskResult(
            task_id=task_id,
            status=PipelineState.QUEUED,
            source_file=source_file,
            config=config,
        )
        await self._queue.put(("run", task_id, source_file, config, None))
        self._ensure_worker()
        logger.info(f"[Task:{task_id}] Generation task queued")
        return task_id

    async def rerun_task(
        self,
        task_id: str,
        from_step: int,
        parameter_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        """从指定步骤重跑（校验范围）"""
        if not 1 <= from_step <= 8:
            raise ValueError(f"from_step 必须在 1-8 之间: {from_step}")
        task = self.get_task(task_id)
        if task.status == PipelineState.RUNNING:
            raise ValueError(f"任务正在运行，不能重跑: {task_id}")
        await self._queue.put(
            ("rerun", task_id, None, None, (from_step, parameter_overrides))
        )
        self._ensure_worker()

    def get_task(self, task_id: str) -> TaskResult:
        """获取任务（内存优先，磁盘回退）"""
        task = self._tasks.get(task_id)
        if task is not None:
            return task
        # S2: task_id 格式校验，防磁盘回退路径逃逸
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", task_id):
            raise KeyError(f"任务不存在: {task_id}")
        # 磁盘回退（服务重启后恢复）
        result_file = (
            GeneratePipeline().storage_root / task_id / "result.json"
        )
        if result_file.exists():
            with open(result_file, "r", encoding="utf-8") as f:
                task = TaskResult(**json.load(f))
            self._tasks[task_id] = task
            return task
        raise KeyError(f"任务不存在: {task_id}")

    def list_tasks(self) -> List[TaskResult]:
        """列出全部任务"""
        return list(self._tasks.values())

    # ─── 串行工作器 ───

    def _ensure_worker(self):
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def _worker(self):
        """串行消费任务队列（SW COM 单线程约束）"""
        while True:
            try:
                kind, task_id, source_file, config, extra = await asyncio.wait_for(
                    self._queue.get(), timeout=60.0
                )
            except asyncio.TimeoutError:
                return  # 队列空闲，退出工作器

            try:
                pipeline = self._build_pipeline()
                task = self._tasks[task_id]
                task.status = PipelineState.RUNNING

                if kind == "run":
                    result = await pipeline.run(task_id, source_file, config)
                else:  # rerun
                    from_step, overrides = extra
                    result = await pipeline.rerun_from(task_id, from_step, overrides)

                self._tasks[task_id] = result
            except Exception as e:
                logger.exception(f"[Task:{task_id}] Worker error: {e}")
                task = self._tasks.get(task_id)
                if task is not None:
                    task.status = PipelineState.ERROR
                    task.error = str(e)
                if self._notify:
                    await self._notify(task_id, {
                        "type": "finished",
                        "status": PipelineState.ERROR.value,
                        "error": str(e),
                        "progress": task.progress if task else 0,
                    })
