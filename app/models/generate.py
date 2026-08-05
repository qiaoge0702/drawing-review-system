"""
图纸生成 API 契约模型（B-M1+ 扩展）

本模块定义前端 ↔ 后端的 API 请求/响应数据结构。
供 app/routers/generate.py 后续迁移使用；当前阶段先定义，不破坏现有路由。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.generation import TaskConfig


class GenerateRequest(BaseModel):
    """创建生成任务请求（B-M1+ 扩展）

    在原有 TaskConfig 基础上，新增用户覆盖参数，支持：
    - 强制指定零件类型
    - 自定义视图列表（增删改）
    - 布局模式切换
    - 手动指定视图位置
    """
    source_file: str = Field(..., description="源 SW 文件绝对路径（.SLDASM/.SLDPRT）")
    config: TaskConfig = Field(default_factory=TaskConfig)

    # B-M1+ 新增覆盖字段
    part_type_override: Optional[str] = Field(
        default=None,
        description="强制指定零件类型（standard_part / plate / beam / weldment / assembly）"
    )
    views_override: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="视图覆盖参数列表，每项为 ViewConfig 兼容字典（支持 action: add/update/remove）"
    )
    layout_mode: Optional[str] = Field(
        default="auto",
        description="布局模式：auto（自动）/ manual（用户辅助指定）"
    )
    positions_override: Optional[Dict[str, Any]] = Field(
        default=None,
        description="位置覆盖参数：{view_id: {x, y, rotation}}"
    )


class RerunRequest(BaseModel):
    """从指定步骤重跑请求（B-M1+ 扩展，供 Phase5 使用）

    支持在重跑时传入任意覆盖参数，由服务端合并到原任务配置。
    """
    from_step: int = Field(..., ge=1, le=8, description="从第几步开始重跑")
    overrides: Dict[str, Any] = Field(
        default_factory=dict,
        description="全局覆盖参数（可包含 views_override / scale_mode / spacing 等任意字段）"
    )
