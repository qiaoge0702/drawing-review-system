"""
核心模块
包含配置管理、异常定义等基础设施
"""

from .config import Settings, get_settings
from .exceptions import (
    DesignReviewException,
    DXFParseException,
    RecognitionException,
    RuleCheckException,
    ValidationException,
    ErrorCode,
)

__all__ = [
    "Settings",
    "get_settings",
    "DesignReviewException",
    "DXFParseException",
    "RecognitionException",
    "RuleCheckException",
    "ValidationException",
    "ErrorCode",
]
