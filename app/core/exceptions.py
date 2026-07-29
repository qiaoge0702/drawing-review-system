"""
业务异常定义模块
定义系统所有自定义异常，支持错误码、详细信息和上下文追踪
"""

from typing import Optional, Dict, Any
from enum import Enum


class ErrorCode(str, Enum):
    """错误码枚举"""
    # 系统级错误 (SYS)
    SYS_INTERNAL_ERROR = "SYS_001"
    SYS_CONFIG_ERROR = "SYS_002"
    SYS_FILE_NOT_FOUND = "SYS_003"
    SYS_PERMISSION_DENIED = "SYS_004"
    
    # DXF解析错误 (DXF)
    DXF_PARSE_ERROR = "DXF_001"
    DXF_ENCODING_ERROR = "DXF_002"
    DXF_CORRUPTED = "DXF_003"
    DXF_UNSUPPORTED_VERSION = "DXF_004"
    DXF_ENTITY_EXTRACT_ERROR = "DXF_005"
    
    # 识别错误 (REC)
    REC_VEHICLE_TYPE_FAILED = "REC_001"
    REC_SUBFRAME_NOT_FOUND = "REC_002"
    REC_BODY_NOT_FOUND = "REC_003"
    REC_DIMENSION_ERROR = "REC_004"
    REC_SECTION_TYPE_UNKNOWN = "REC_005"
    
    # 规则检查错误 (RULE)
    RULE_VALIDATION_ERROR = "RULE_001"
    RULE_CALCULATION_ERROR = "RULE_002"
    RULE_PARAMETER_MISSING = "RULE_003"
    
    # 数据验证错误 (VAL)
    VAL_INVALID_INPUT = "VAL_001"
    VAL_MISSING_REQUIRED = "VAL_002"
    VAL_TYPE_MISMATCH = "VAL_003"
    VAL_RANGE_ERROR = "VAL_004"
    
    # 生成错误 (GEN)
    GEN_PIPELINE_ERROR = "GEN_001"
    GEN_STEP_FAILED = "GEN_002"
    GEN_SW_NOT_AVAILABLE = "GEN_003"
    GEN_SW_TIMEOUT = "GEN_004"
    GEN_INVALID_FILE = "GEN_005"
    GEN_UNSUPPORTED_FEATURE = "GEN_006"
    GEN_CHECKPOINT_ERROR = "GEN_007"
    GEN_RERUN_ERROR = "GEN_008"


class DesignReviewException(Exception):
    """
    设计审查系统基础异常类
    
    Attributes:
        error_code: 错误码
        message: 错误消息
        detail: 详细错误信息
        context: 错误上下文数据
    """
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.SYS_INTERNAL_ERROR,
        detail: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.detail = detail
        self.context = context or {}
    
    def __str__(self) -> str:
        parts = [f"[{self.error_code}] {self.message}"]
        if self.detail:
            parts.append(f"Detail: {self.detail}")
        if self.context:
            parts.append(f"Context: {self.context}")
        return " | ".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，用于API响应"""
        return {
            "error_code": self.error_code.value,
            "message": self.message,
            "detail": self.detail,
            "context": self.context
        }


class DXFParseException(DesignReviewException):
    """DXF文件解析异常"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.DXF_PARSE_ERROR,
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
        detail: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        ctx = context or {}
        if file_path:
            ctx["file_path"] = file_path
        if line_number:
            ctx["line_number"] = line_number
        super().__init__(message, error_code, detail, ctx)
        self.file_path = file_path
        self.line_number = line_number


class RecognitionException(DesignReviewException):
    """结构识别异常"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.REC_VEHICLE_TYPE_FAILED,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        detail: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        ctx = context or {}
        if entity_type:
            ctx["entity_type"] = entity_type
        if entity_id:
            ctx["entity_id"] = entity_id
        super().__init__(message, error_code, detail, ctx)
        self.entity_type = entity_type
        self.entity_id = entity_id


class RuleCheckException(DesignReviewException):
    """规则检查异常"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.RULE_VALIDATION_ERROR,
        rule_id: Optional[str] = None,
        rule_name: Optional[str] = None,
        detail: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        ctx = context or {}
        if rule_id:
            ctx["rule_id"] = rule_id
        if rule_name:
            ctx["rule_name"] = rule_name
        super().__init__(message, error_code, detail, ctx)
        self.rule_id = rule_id
        self.rule_name = rule_name


class ValidationException(DesignReviewException):
    """数据验证异常"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.VAL_INVALID_INPUT,
        field_name: Optional[str] = None,
        field_value: Optional[Any] = None,
        detail: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        ctx = context or {}
        if field_name:
            ctx["field_name"] = field_name
        if field_value is not None:
            ctx["field_value"] = str(field_value)
        super().__init__(message, error_code, detail, ctx)
        self.field_name = field_name
        self.field_value = field_value


class GenerationException(DesignReviewException):
    """图纸生成异常"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.GEN_PIPELINE_ERROR,
        task_id: Optional[str] = None,
        step: Optional[int] = None,
        step_name: Optional[str] = None,
        detail: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        recoverable: bool = False
    ):
        ctx = context or {}
        if task_id:
            ctx["task_id"] = task_id
        if step:
            ctx["step"] = step
        if step_name:
            ctx["step_name"] = step_name
        ctx["recoverable"] = recoverable
        super().__init__(message, error_code, detail, ctx)
        self.task_id = task_id
        self.step = step
        self.step_name = step_name
        self.recoverable = recoverable


class SWException(GenerationException):
    """SolidWorks 相关异常"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.GEN_SW_NOT_AVAILABLE,
        sw_version: Optional[str] = None,
        detail: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        ctx = context or {}
        if sw_version:
            ctx["sw_version"] = sw_version
        super().__init__(
            message,
            error_code=error_code,
            detail=detail,
            context=ctx,
            recoverable=error_code == ErrorCode.GEN_SW_TIMEOUT
        )
        self.sw_version = sw_version
