"""
生成引擎内部模型

复用 app.models.generation 的公共模型，定义引擎内部使用的扩展模型。
"""

from typing import Optional, Dict, Any, List, Callable, Awaitable
from dataclasses import dataclass, field
from pathlib import Path

from app.models.generation import StepName, StepStatus, Artifact


@dataclass
class StepContext:
    """步骤执行上下文"""
    task_id: str
    step: int
    step_name: StepName
    work_dir: Path                          # 步骤工作目录
    input_data: Dict[str, Any] = field(default_factory=dict)   # 输入数据
    parameters: Dict[str, Any] = field(default_factory=dict)   # 执行参数
    previous_results: Dict[int, Any] = field(default_factory=dict)  # 前序步骤结果
    
    def get_input_path(self, filename: str) -> Path:
        """获取输入文件路径"""
        return self.work_dir / "input" / filename
    
    def get_output_path(self, filename: str) -> Path:
        """获取输出文件路径"""
        return self.work_dir / "output" / filename


@dataclass
class StepConfig:
    """步骤配置"""
    name: StepName
    display_name: str
    timeout_seconds: int = 60
    retryable: bool = False
    max_retries: int = 0
    requires: List[int] = field(default_factory=list)      # 依赖步骤编号
    idempotent: bool = True                                 # 是否幂等（支持独立重跑）


# 步骤执行函数类型
StepExecutor = Callable[[StepContext], Awaitable[Dict[str, Any]]]
