"""
配置管理模块
支持环境变量、.env文件、默认值的多层配置
"""

import os
import logging
from typing import Optional, List, Dict
from pathlib import Path
from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()


class LoggingConfig(BaseSettings):
    """日志配置"""
    model_config = SettingsConfigDict(env_prefix="LOG_")
    
    level: str = Field(default="INFO", description="日志级别")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="日志格式"
    )
    file_path: Optional[str] = Field(
        default=str(PROJECT_ROOT / "logs" / "app.log"),
        description="日志文件路径（设为空字符串则仅控制台输出）"
    )
    max_bytes: int = Field(default=10 * 1024 * 1024, description="单个日志文件最大字节数")
    backup_count: int = Field(default=5, description="保留的备份文件数量")
    
    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v_upper


class DXFConfig(BaseSettings):
    """DXF解析配置"""
    model_config = SettingsConfigDict(env_prefix="DXF_")
    
    encoding_fallbacks: List[str] = Field(
        default=["utf-8", "gbk", "gb2312", "latin-1", "cp1252"],
        description="编码回退列表"
    )
    max_file_size_mb: int = Field(default=100, description="最大文件大小(MB)")
    recover_on_error: bool = Field(default=True, description="出错时尝试恢复")
    entity_batch_size: int = Field(default=1000, description="实体批量处理大小")


class AIConfig(BaseSettings):
    """AI分析配置"""
    model_config = SettingsConfigDict(env_prefix="AI_")
    
    provider: str = Field(default="openai", description="AI提供商")
    api_key: Optional[str] = Field(default=None, description="API密钥")
    base_url: Optional[str] = Field(default=None, description="API基础URL")
    model: str = Field(default="gpt-4", description="模型名称")
    timeout_seconds: int = Field(default=60, description="请求超时时间")
    max_retries: int = Field(default=3, description="最大重试次数")
    temperature: float = Field(default=0.1, description="温度参数")


class StorageConfig(BaseSettings):
    """存储配置"""
    model_config = SettingsConfigDict(env_prefix="STORAGE_")
    
    upload_dir: Path = Field(
        default=PROJECT_ROOT / "uploads",
        description="上传文件存储目录"
    )
    output_dir: Path = Field(
        default=PROJECT_ROOT / "output",
        description="输出文件存储目录"
    )
    temp_dir: Path = Field(
        default=PROJECT_ROOT / "temp",
        description="临时文件目录"
    )
    max_upload_size_mb: int = Field(default=50, description="最大上传文件大小(MB)")
    allowed_extensions: List[str] = Field(
        default=[".dxf", ".dwg", ".pdf"],
        description="允许的文件扩展名"
    )
    cleanup_temp_after_hours: int = Field(default=24, description="临时文件清理时间(小时)")


class SWConfig(BaseSettings):
    """SolidWorks API 配置（Step3 工程图视图投影引擎）"""
    model_config = SettingsConfigDict(env_prefix="SW_")

    drawing_template: str = Field(
        default=r"C:\ProgramData\SolidWorks\SOLIDWORKS 2025\templates\gb_a3.drwdot",
        description="工程图模板路径（国标 A3）"
    )
    predefined_view_names: Dict[str, str] = Field(
        default={"front": "*前视", "top": "*上视", "left": "*左视"},
        description="预定义视图名映射（中文版 SW 必须中文）"
    )
    view_insert_positions: Dict[str, List[float]] = Field(
        default={"front": [0.15, 0.15], "top": [0.15, 0.08], "left": [0.28, 0.15]},
        description="视图插入图纸坐标（米）"
    )
    spline_sample_points: int = Field(default=50, description="样条边离散采样点数")


class Settings(BaseSettings):
    """
    应用主配置类
    
    配置优先级（从高到低）：
    1. 环境变量
    2. .env文件
    3. 默认值
    """
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # 应用信息
    app_name: str = Field(default="专用车辆上装设计审查系统", description="应用名称")
    app_version: str = Field(default="1.0.0", description="应用版本")
    debug: bool = Field(default=False, description="调试模式")
    
    # 服务器配置
    host: str = Field(default="0.0.0.0", description="监听地址")
    port: int = Field(default=8000, description="监听端口")
    
    # 子配置
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    dxf: DXFConfig = Field(default_factory=DXFConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    sw: SWConfig = Field(default_factory=SWConfig)
    
    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v
    
    def setup_logging(self) -> None:
        """配置日志系统"""
        log_level = getattr(logging, self.logging.level)
        
        handlers: List[logging.Handler] = [logging.StreamHandler()]
        
        if self.logging.file_path:
            from logging.handlers import RotatingFileHandler
            # 确保日志目录存在
            Path(self.logging.file_path).parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                self.logging.file_path,
                maxBytes=self.logging.max_bytes,
                backupCount=self.logging.backup_count,
                encoding="utf-8"
            )
            handlers.append(file_handler)
        
        logging.basicConfig(
            level=log_level,
            format=self.logging.format,
            handlers=handlers,
            force=True
        )
        
        # 设置第三方库日志级别
        logging.getLogger("ezdxf").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)


@lru_cache()
def get_settings() -> Settings:
    """
    获取配置实例（单例模式）
    
    Returns:
        Settings: 应用配置实例
    """
    return Settings()


# 全局配置实例
settings = get_settings()
