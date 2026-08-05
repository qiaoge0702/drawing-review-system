"""
图纸生成流水线引擎

管理从 SW 文件到 DXF 的完整生成流程，支持：
- 8 步骤顺序执行
- 检查点保存/恢复
- 单步重跑
- 异常处理和日志记录
"""

import os
import json
import logging
import shutil
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.models.generation import (
    TaskConfig,
    TaskResult,
    StepResult,
    StepStatus,
    StepName,
    PipelineState,
    Artifact,
    ArtifactType,
)
from app.generators.models import StepContext, StepConfig
from app.core.exceptions import GenerationException, ErrorCode
from app.core.config import settings

logger = logging.getLogger(__name__)


# 运行时执行顺序：1 → 2 → 3 → 7 → 5 → 6 → 4 → 8
# 设计意图：逐级盖楼、每级留证；Step7 紧接 Step3 完成骨架版 SLDDRW，
# Step5/6 追加表格/技术要求，Step4 最后补标注并导出终版 DWG/PDF。
EXECUTION_ORDER = [1, 2, 3, 7, 5, 6, 4, 8]

# 步骤配置表（requires 按新执行顺序的依赖关系）
STEP_CONFIGS = [
    StepConfig(StepName.SW_LOAD, "3D模型加载", timeout_seconds=60, retryable=True, max_retries=2),
    StepConfig(StepName.GEOMETRY_PARSE, "几何解析", timeout_seconds=120, requires=[1]),
    StepConfig(StepName.VIEW_PROJECT, "视图投影", timeout_seconds=180, requires=[2]),
    StepConfig(StepName.DIMENSION, "尺寸标注", timeout_seconds=120, requires=[6]),
    StepConfig(StepName.BOM_GENERATE, "BOM生成", timeout_seconds=60, requires=[7]),
    StepConfig(StepName.TECH_REQUIREMENT, "技术要求", timeout_seconds=60, requires=[5]),
    StepConfig(StepName.DXF_BUILD, "图纸收尾", timeout_seconds=120, requires=[3]),
    StepConfig(StepName.REVIEW, "审查闭环", timeout_seconds=120, requires=[4]),
]

# 1x1 透明 PNG（无真图快照时的占位图，保证每步 preview.png 落盘可追踪）
_PLACEHOLDER_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c626000010000050001a5f64540000000"
    "49454e44ae426082"
)


class GeneratePipeline:
    """
    生成流水线引擎
    
    Usage:
        pipeline = GeneratePipeline()
        result = await pipeline.run(task_id, source_file, config)
        
        # 重跑某步骤
        result = await pipeline.rerun_from(task_id, from_step=4, params={...})
    """
    
    def __init__(self, storage_root: Optional[Path] = None):
        self.storage_root = storage_root or settings.storage.temp_dir / "generate"
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._step_executors: Dict[StepName, Any] = {}
        self._progress_callback: Optional[Any] = None  # async cb(task_id, event: dict)
        logger.info(f"GeneratePipeline initialized, storage: {self.storage_root}")
    
    def register_executor(self, step_name: StepName, executor: Any):
        """注册步骤执行器"""
        self._step_executors[step_name] = executor
        logger.debug(f"Registered executor for {step_name.value}")
    
    def set_progress_callback(self, callback: Any):
        """注册进度回调：async callable(task_id, event_dict)，每步状态变化时触发"""
        self._progress_callback = callback
    
    async def _emit_progress(self, task_id: str, event: Dict[str, Any]):
        """触发进度回调（异常不影响流水线）"""
        if self._progress_callback is None:
            return
        try:
            await self._progress_callback(task_id, event)
        except Exception as e:
            logger.warning(f"[Task:{task_id}] Progress callback error: {e}")
    
    async def run(
        self,
        task_id: str,
        source_file: str,
        config: TaskConfig
    ) -> TaskResult:
        """
        执行完整生成流水线
        
        Args:
            task_id: 任务唯一ID
            source_file: 源SW文件路径
            config: 生成配置
            
        Returns:
            TaskResult: 任务执行结果
        """
        logger.info(f"[Task:{task_id}] Starting pipeline for {source_file}")
        
        task_dir = self._get_task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        
        result = TaskResult(
            task_id=task_id,
            status=PipelineState.RUNNING,
            source_file=source_file,
            config=config,
            started_at=datetime.utcnow(),
        )
        
        # 保存任务配置和源文件信息
        self._save_task_config(task_id, config)
        self._save_task_meta(task_id, source_file)
        
        try:
            for exec_index, step_num in enumerate(EXECUTION_ORDER, start=1):
                step_config = STEP_CONFIGS[step_num - 1]
                
                # 检查是否有可用的完成检查点（失败/损坏的不算）
                checkpoint = self._load_checkpoint(task_id, step_num)
                if checkpoint is not None:
                    logger.info(f"[Task:{task_id}] Step {step_num} found checkpoint, loading...")
                    result.steps.append(checkpoint)
                    result.current_step = step_num
                    result.progress = int(exec_index / 8 * 100)
                    continue
                
                # 执行步骤（含重试循环，重试次数累加）
                await self._emit_progress(task_id, {
                    "type": "step_start",
                    "status": result.status.value,
                    "current_step": step_num,
                    "progress": result.progress,
                    "step_name": step_config.name.value,
                    "display_name": step_config.display_name,
                })
                max_attempts = 1 + (step_config.max_retries if step_config.retryable else 0)
                step_result: Optional[StepResult] = None
                for attempt in range(1, max_attempts + 1):
                    if attempt > 1:
                        logger.warning(
                            f"[Task:{task_id}] Step {step_num} retry "
                            f"{attempt - 1}/{step_config.max_retries}"
                        )
                    step_result = await self._execute_step(
                        task_id=task_id,
                        step_num=step_num,
                        step_config=step_config,
                        source_file=source_file,
                        config=config,
                        previous_results=result.steps,
                        execution_count=attempt,
                    )
                    if step_result.is_success:
                        break
                
                result.steps.append(step_result)
                result.current_step = step_num
                result.progress = int(exec_index / 8 * 100)
                
                # 仅成功的步骤保存检查点
                if step_result.is_success:
                    self._save_checkpoint(task_id, step_result)
                
                # 推送步骤进度
                await self._emit_progress(task_id, {
                    "type": "step",
                    "status": result.status.value,
                    "current_step": step_num,
                    "progress": result.progress,
                    "step": step_result.model_dump(mode="json"),
                })
                
                # 步骤失败处理
                if step_result.is_failed:
                    result.status = PipelineState.ERROR
                    result.error = f"Step {step_num} failed: {step_result.error}"
                    logger.error(f"[Task:{task_id}] Pipeline failed at step {step_num}: {step_result.error}")
                    break
            
            if result.status != PipelineState.ERROR:
                result.status = PipelineState.COMPLETED
                result.completed_at = datetime.utcnow()
                result.total_duration_ms = sum(s.duration_ms for s in result.steps)
                logger.info(f"[Task:{task_id}] Pipeline completed in {result.total_duration_ms}ms")
            
        except Exception as e:
            result.status = PipelineState.ERROR
            result.error = str(e)
            logger.exception(f"[Task:{task_id}] Pipeline exception: {e}")
        
        # 保存最终结果
        self._save_task_result(task_id, result)
        
        # 推送最终状态
        await self._emit_progress(task_id, {
            "type": "finished",
            "status": result.status.value,
            "current_step": result.current_step,
            "progress": result.progress,
            "error": result.error,
        })
        
        return result
    
    async def rerun_from(
        self,
        task_id: str,
        from_step: int,
        parameter_overrides: Optional[Dict[str, Any]] = None
    ) -> TaskResult:
        """
        从指定步骤重跑
        
        Args:
            task_id: 任务ID
            from_step: 从第几步开始重跑（1-8）
            parameter_overrides: 参数覆盖
            
        Returns:
            TaskResult: 新的执行结果
        """
        logger.info(f"[Task:{task_id}] Rerunning from step {from_step}")
        
        # 加载原任务配置
        config = self._load_task_config(task_id)
        source_file = self._load_source_file(task_id)
        
        if parameter_overrides:
            for key, value in parameter_overrides.items():
                setattr(config, key, value)
        
        # 清除目标步骤及执行顺序中后续步骤的检查点（按 EXECUTION_ORDER）
        if from_step not in EXECUTION_ORDER:
            raise ValueError(
                f"from_step {from_step} 不在执行顺序 {EXECUTION_ORDER} 中"
            )
        start_idx = EXECUTION_ORDER.index(from_step)
        for step_num in EXECUTION_ORDER[start_idx:]:
            self._clear_checkpoint(task_id, step_num)
        
        # 重新执行
        return await self.run(task_id, source_file, config)
    
    async def _execute_step(
        self,
        task_id: str,
        step_num: int,
        step_config: StepConfig,
        source_file: str,
        config: TaskConfig,
        previous_results: List[StepResult],
        execution_count: int = 1,
    ) -> StepResult:
        """执行单个步骤"""
        step_name = step_config.name
        logger.info(f"[Task:{task_id}] Executing step {step_num}: {step_name.value}")
        
        step_dir = self._get_step_dir(task_id, step_num)
        step_dir.mkdir(parents=True, exist_ok=True)
        
        step_result = StepResult(
            step=step_num,
            name=step_name,
            status=StepStatus.RUNNING,
            started_at=datetime.utcnow(),
            execution_count=execution_count,
        )
        
        start_time = datetime.utcnow()
        
        try:
            # 构建执行上下文
            ctx = StepContext(
                task_id=task_id,
                step=step_num,
                step_name=step_name,
                work_dir=step_dir,
                parameters={**config.model_dump(), "source_file": source_file},
                previous_results={r.step: r.output_data for r in previous_results if r.output_data},
            )
            
            # 获取执行器
            executor = self._step_executors.get(step_name)
            if not executor:
                raise GenerationException(
                    f"No executor registered for step {step_name.value}",
                    error_code=ErrorCode.GEN_PIPELINE_ERROR,
                    task_id=task_id,
                    step=step_num,
                )
            
            # 执行（带超时）
            output_data = await asyncio.wait_for(
                executor(ctx),
                timeout=step_config.timeout_seconds
            )
            
            step_result.status = StepStatus.COMPLETED
            step_result.output_data = output_data
            step_result.completed_at = datetime.utcnow()

            # 每步留痕：result.json + preview.png + 版本副本（7/5/6/4）
            await self._leave_trace(ctx, step_result)
            
            # 收集产物
            output_dir = step_dir / "output"
            if output_dir.exists():
                for file_path in output_dir.iterdir():
                    if file_path.is_file():
                        artifact = Artifact(
                            id=f"{task_id}_step{step_num}_{file_path.name}",
                            name=file_path.name,
                            type=self._guess_artifact_type(file_path.suffix),
                            path=str(file_path),
                            size=file_path.stat().st_size,
                        )
                        step_result.artifacts.append(artifact)
            
            logger.info(f"[Task:{task_id}] Step {step_num} completed")
            
        except asyncio.TimeoutError:
            step_result.status = StepStatus.ERROR
            step_result.error = f"Step timeout after {step_config.timeout_seconds}s"
            logger.error(f"[Task:{task_id}] Step {step_num} timeout")
            
        except Exception as e:
            step_result.status = StepStatus.ERROR
            step_result.error = str(e)
            logger.exception(f"[Task:{task_id}] Step {step_num} failed: {e}")
        
        finally:
            step_result.duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        return step_result

    async def _leave_trace(self, ctx: StepContext, step_result: StepResult) -> None:
        """
        每步成功后落盘可追溯产物：
        - result.json：该步输出数据
        - preview.png：真图快照（无快照时落盘占位图，保证可追踪）
        - 版本副本：Step7→step7_skeleton.slddrw，Step5→step5_table.slddrw，
                    Step6→step6_table.slddrw，Step4→step4_final.slddrw
        Step4 末尾额外导出终版 DWG/PDF/PNG（终版导出从 Step7 迁移至此）。
        """
        output_dir = ctx.get_output_path("")
        output_dir.mkdir(parents=True, exist_ok=True)
        data = step_result.output_data or {}
        task_dir = self._get_task_dir(ctx.task_id)

        # 1) result.json
        result_file = output_dir / "result.json"
        try:
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.warning(f"[Task:{ctx.task_id}] Step {ctx.step} result.json 落盘失败: {e}")

        # 2) preview.png：优先复用执行器产出的快照
        preview_file = output_dir / "preview.png"
        snapshot_src: Optional[Path] = None
        for key in ("final_snapshot_path", "snapshot_path"):
            src = data.get(key)
            if src:
                p = Path(src)
                if p.exists():
                    snapshot_src = p
                    break
        if not snapshot_src:
            for f in output_dir.iterdir():
                if f.suffix.lower() == ".png":
                    snapshot_src = f
                    break
        try:
            if snapshot_src and snapshot_src.resolve() != preview_file.resolve():
                shutil.copy2(snapshot_src, preview_file)
            elif not preview_file.exists():
                preview_file.write_bytes(_PLACEHOLDER_PNG_BYTES)
        except Exception as e:
            logger.warning(f"[Task:{ctx.task_id}] Step {ctx.step} preview.png 落盘失败: {e}")

        # 3) 版本副本（逐级盖楼）
        version_copy: Optional[Path] = None
        if ctx.step == 7:
            src_key = data.get("slddrw_path") or ctx.previous_results.get(3, {}).get("drawing_path")
            if src_key:
                src = Path(src_key)
                if src.exists():
                    version_copy = output_dir / "step7_skeleton.slddrw"
                    shutil.copy2(src, version_copy)
                else:
                    logger.warning(f"[Task:{ctx.task_id}] Step {ctx.step} 上一步版本副本缺失，跳过复制: {src}")
        elif ctx.step == 5:
            src = task_dir / "step_7" / "output" / "step7_skeleton.slddrw"
            if src.exists():
                version_copy = output_dir / "step5_table.slddrw"
                shutil.copy2(src, version_copy)
            else:
                logger.warning(f"[Task:{ctx.task_id}] Step {ctx.step} 上一步版本副本缺失，跳过复制: {src}")
        elif ctx.step == 6:
            src = task_dir / "step_5" / "output" / "step5_table.slddrw"
            if src.exists():
                version_copy = output_dir / "step6_table.slddrw"
                shutil.copy2(src, version_copy)
            else:
                logger.warning(f"[Task:{ctx.task_id}] Step {ctx.step} 上一步版本副本缺失，跳过复制: {src}")
        elif ctx.step == 4:
            src = task_dir / "step_6" / "output" / "step6_table.slddrw"
            if src.exists():
                version_copy = output_dir / "step4_final.slddrw"
                shutil.copy2(src, version_copy)
                # 终版全格式导出挪到 Step4 完成后
                await self._export_final_version(ctx.task_id, version_copy, output_dir)
            else:
                logger.warning(f"[Task:{ctx.task_id}] Step {ctx.step} 上一步版本副本缺失，跳过复制: {src}")

        if version_copy:
            logger.debug(f"[Task:{ctx.task_id}] Step {ctx.step} 版本副本 -> {version_copy}")

    async def _export_final_version(self, task_id: str, slddrw_path: Path, output_dir: Path) -> None:
        """
        Step4 完成后导出终版 DWG/PDF/PNG。
        真机由 sw_drawing.export_final_sync 在 SW COM 线程执行；
        导出失败仅记录 warning，不阻塞流水线（Step4 本身已成功留痕）。
        """
        try:
            from app.generators import sw_drawing
            from app.generators.sw_com import run_sw

            await run_sw(
                sw_drawing.export_final_sync,
                str(slddrw_path),
                str(output_dir),
                task_id,
            )
            logger.info(f"[Task:{task_id}] Step4 终版导出完成 -> {output_dir}")
        except Exception as e:
            logger.warning(f"[Task:{task_id}] Step4 终版导出失败（后续可单独重跑或人工导出）: {e}")

    # --- 检查点管理 ---
    
    def _save_checkpoint(self, task_id: str, step_result: StepResult):
        """保存步骤检查点（临时文件 + os.replace 原子落盘）"""
        step_dir = self._get_step_dir(task_id, step_result.step)
        step_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_file = step_dir / "checkpoint.json"
        tmp_file = step_dir / "checkpoint.json.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(step_result.model_dump(), f, ensure_ascii=False, default=str, indent=2)
            os.replace(tmp_file, checkpoint_file)
            logger.debug(f"[Task:{task_id}] Checkpoint saved for step {step_result.step}")
        except Exception as e:
            logger.warning(f"[Task:{task_id}] Failed to save checkpoint: {e}")
            try:
                tmp_file.unlink(missing_ok=True)
            except Exception:
                pass
    
    def _load_checkpoint(self, task_id: str, step: int) -> Optional[StepResult]:
        """
        加载步骤检查点
        
        仅接受 status=COMPLETED 的检查点；文件缺失、JSON 损坏、
        模型校验失败、状态非完成时一律返回 None（视为无检查点，重跑该步）。
        """
        checkpoint_file = self._get_step_dir(task_id, step) / "checkpoint.json"
        if not checkpoint_file.exists():
            return None
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            step_result = StepResult(**data)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(
                f"[Task:{task_id}] Corrupted checkpoint at step {step}, "
                f"will re-execute: {e}"
            )
            return None
        if step_result.status != StepStatus.COMPLETED:
            logger.warning(
                f"[Task:{task_id}] Checkpoint at step {step} not completed "
                f"(status={step_result.status.value}), will re-execute"
            )
            return None
        return step_result
    
    def _clear_checkpoint(self, task_id: str, step: int):
        """清除检查点"""
        step_dir = self._get_step_dir(task_id, step)
        if step_dir.exists():
            shutil.rmtree(step_dir)
            logger.debug(f"[Task:{task_id}] Checkpoint cleared for step {step}")
    
    # --- 任务数据管理 ---
    
    def _save_task_config(self, task_id: str, config: TaskConfig):
        """保存任务配置"""
        task_dir = self._get_task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        config_file = task_dir / "config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)
    
    def _save_task_meta(self, task_id: str, source_file: str):
        """保存任务元数据"""
        task_dir = self._get_task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        meta_file = task_dir / "meta.json"
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump({"source_file": source_file}, f, ensure_ascii=False, indent=2)
    
    def _load_task_config(self, task_id: str) -> TaskConfig:
        """加载任务配置"""
        config_file = self._get_task_dir(task_id) / "config.json"
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return TaskConfig(**data)
    
    def _load_source_file(self, task_id: str) -> str:
        """加载源文件路径"""
        meta_file = self._get_task_dir(task_id) / "meta.json"
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["source_file"]
    
    def _save_task_result(self, task_id: str, result: TaskResult):
        """保存任务结果"""
        result_file = self._get_task_dir(task_id) / "result.json"
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(), f, ensure_ascii=False, default=str, indent=2)
    
    # --- 工具方法 ---
    
    def _get_task_dir(self, task_id: str) -> Path:
        """获取任务目录"""
        return self.storage_root / task_id
    
    def _get_step_dir(self, task_id: str, step: int) -> Path:
        """获取步骤目录"""
        return self._get_task_dir(task_id) / f"step_{step}"
    
    @staticmethod
    def _guess_artifact_type(suffix: str) -> ArtifactType:
        """猜测产物类型"""
        type_map = {
            ".json": ArtifactType.JSON,
            ".svg": ArtifactType.SVG,
            ".png": ArtifactType.PNG,
            ".dxf": ArtifactType.DXF,
            ".txt": ArtifactType.TXT,
        }
        return type_map.get(suffix.lower(), ArtifactType.JSON)
