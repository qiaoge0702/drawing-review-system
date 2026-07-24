"""
AI 审查分析器
将 PNG 图片 + DXF JSON + 生产规则组装后发送给 AI 模型进行审查

支持多 Provider：
- OpenAI（GPT-4o 系列）：支持 Vision + JSON mode
- Kimi/Moonshot（kimi-k3 等）：支持 Vision，不支持 JSON mode
  - K3 始终启用思考模式，支持 reasoning_effort（low/high/max）
  - K2.6/K2.7 Code 支持思考模式
- 自定义兼容 API

关键设计：
- API Key 不持久化，仅用于当次请求
- 非视觉模型自动跳过图片
- JSON mode 仅对支持的模型启用
- K3 的 reasoning_effort 默认 high（平衡速度与质量）
"""

import json
import logging
import base64
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Union

from app.core.config import settings
from app.core.exceptions import DesignReviewException, ErrorCode
from app.ai.prompts.review_prompt import COMPREHENSIVE_REVIEW_PROMPT

logger = logging.getLogger(__name__)


class AIAnalysisError(DesignReviewException):
    """AI 分析异常"""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(
            message,
            error_code=ErrorCode.SYS_INTERNAL_ERROR,
            detail=detail
        )


class AIAnalyzer:
    """
    AI 审查分析器

    Usage:
        analyzer = AIAnalyzer(
            api_key="sk-xxx",
            model="gpt-4o",
            base_url="https://api.openai.com/v1"
        )
        result = analyzer.analyze(
            image_path="/path/to/drawing.png",
            dxf_json={"entities": {...}},
            rules_json={"material_specs": {...}}
        )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        """
        初始化 AI 分析器

        Args:
            api_key: API 密钥（优先于配置文件）
            model: 模型名称（优先于配置文件）
            base_url: API 基础 URL
            provider: 提供商名称（openai / kimi / custom）
        """
        self.api_key = api_key or settings.ai.api_key
        self.model = model or settings.ai.model
        self.base_url = base_url or settings.ai.base_url
        self.provider = provider or settings.ai.provider or self._detect_provider()

        if not self.api_key:
            raise AIAnalysisError(
                "未配置 API Key",
                detail="请在界面输入 API Key 或设置环境变量 AI_API_KEY"
            )

        logger.info(f"AI 分析器初始化: provider={self.provider}, model={self.model}")

    def _detect_provider(self) -> str:
        """根据 model 名称或 base_url 自动推断 provider"""
        model_lower = self.model.lower()
        url_lower = (self.base_url or "").lower()

        if "kimi" in model_lower or "moonshot" in url_lower or "kimi" in url_lower:
            return "kimi"
        if "openai" in url_lower or "gpt" in model_lower or "gpt-4" in model_lower:
            return "openai"
        return "custom"

    def _supports_json_mode(self) -> bool:
        """检查模型是否支持 JSON mode（仅 OpenAI GPT-4 系列）"""
        if self.provider != "openai":
            return False
        model_lower = self.model.lower()
        return "gpt-4o" in model_lower or "gpt-4-turbo" in model_lower or "gpt-4-1106" in model_lower

    def _supports_vision(self) -> bool:
        """检查模型是否支持视觉（图片输入）"""
        model_lower = self.model.lower()

        # OpenAI: GPT-4o 系列、GPT-4 Turbo 支持
        if self.provider == "openai":
            return ("gpt-4o" in model_lower or
                    "gpt-4-turbo" in model_lower or
                    "gpt-4-1106" in model_lower or
                    "gpt-4-vision" in model_lower)

        # Kimi: kimi-k3/k2.6/k2.7 等支持视觉
        if self.provider == "kimi":
            return "kimi" in model_lower or "vision" in model_lower or "vl" in model_lower

        # 自定义：默认假设支持
        return True

    def _supports_reasoning_effort(self) -> bool:
        """检查模型是否支持 reasoning_effort 参数（Kimi K3 等）"""
        model_lower = self.model.lower()
        if self.provider == "kimi":
            # K3 始终启用思考模式，支持 reasoning_effort
            return "kimi-k3" in model_lower
        return False

    def analyze(
        self,
        image_path: Optional[Union[str, Path]],
        dxf_json: Dict[str, Any],
        rules_json: Dict[str, Any],
        drawing_name: str = "",
    ) -> Dict[str, Any]:
        """
        执行综合审查

        Args:
            image_path: PNG 图片路径（如果模型不支持视觉则为 None）
            dxf_json: DXF 提取的结构化数据
            rules_json: 生产规则 JSON
            drawing_name: 图纸名称

        Returns:
            AI 返回的审查结果字典

        Raises:
            AIAnalysisError: 分析失败
        """
        from openai import OpenAI

        # 构建 client
        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = OpenAI(**client_kwargs)

        # 准备 Prompt
        dxf_json_str = json.dumps(dxf_json, ensure_ascii=False, indent=2)
        rules_json_str = json.dumps(rules_json, ensure_ascii=False, indent=2)

        prompt = COMPREHENSIVE_REVIEW_PROMPT.format(
            dxf_json=dxf_json_str,
            rules_json=rules_json_str,
        )

        if drawing_name:
            prompt = f"## 图纸名称: {drawing_name}\n\n" + prompt

        # 构建 messages
        messages = self._build_messages(prompt, image_path)

        # 调用 API
        logger.info(f"调用 AI API: model={self.model}, "
                    f"has_image={image_path is not None}, "
                    f"json_mode={self._supports_json_mode()}")

        try:
            # K3 思考模式消耗 token 较多，增大 max_tokens
            max_tokens = 16384 if self._supports_reasoning_effort() else 4096

            api_kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": settings.ai.temperature,
                "max_tokens": max_tokens,
            }

            # Kimi K3: 添加 reasoning_effort（默认 high，平衡速度与质量）
            if self._supports_reasoning_effort():
                api_kwargs["reasoning_effort"] = "high"

            # JSON mode（仅 OpenAI 支持）
            if self._supports_json_mode():
                api_kwargs["response_format"] = {"type": "json_object"}
                # JSON mode 需要在 prompt 中明确要求输出 JSON
                messages[-1]["content"] = self._ensure_json_instruction(
                    messages[-1]["content"] if isinstance(messages[-1]["content"], str)
                    else prompt
                )

            response = client.chat.completions.create(**api_kwargs)

            content = response.choices[0].message.content
            logger.debug(f"AI 响应长度: {len(content)} 字符")

            # 解析 JSON 结果
            result = self._parse_response(content)
            logger.info("AI 分析完成")

            return result

        except Exception as e:
            logger.error(f"AI API 调用失败: {e}")
            raise AIAnalysisError(
                f"AI 分析失败: {str(e)}",
                detail=str(e)
            )

    def _build_messages(self, prompt: str, image_path: Optional[Union[str, Path]]) -> List[Dict]:
        """
        构建 messages 数组

        如果模型支持视觉且有图片：使用 content array 格式
        否则：使用纯文本格式
        """
        # 系统消息
        system_msg = {
            "role": "system",
            "content": "你是专用车辆上装设计审查专家。请严格按照要求输出 JSON 格式的审查结果。"
        }

        if image_path and self._supports_vision():
            # 视觉模式：图片 + 文本
            image_path = Path(image_path)
            if not image_path.exists():
                logger.warning(f"图片不存在，跳过视觉分析: {image_path}")
                return [system_msg, {"role": "user", "content": prompt}]

            # base64 编码图片
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")

            # 根据文件扩展名确定 MIME
            ext = image_path.suffix.lower()
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(ext, "image/png")

            # OpenAI vision 格式
            user_msg = {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{image_data}",
                            "detail": "high"
                        }
                    }
                ]
            }
        else:
            # 纯文本模式
            if image_path:
                logger.info("模型不支持视觉，跳过图片输入")
            user_msg = {"role": "user", "content": prompt}

        return [system_msg, user_msg]

    def _ensure_json_instruction(self, content) -> str:
        """确保 content 中包含 JSON 输出指令（JSON mode 要求）"""
        if isinstance(content, str):
            if "json" not in content.lower():
                return content + "\n\n请确保输出为合法的 JSON 格式。"
            return content
        return content

    def _parse_response(self, content: str) -> Dict[str, Any]:
        """
        解析 AI 返回的 JSON 结果

        处理多种情况：
        - 纯 JSON
        - Markdown 代码块中的 JSON
        - 带前后文字的 JSON
        """
        content = content.strip()

        # 去除 Markdown 代码块
        if content.startswith("```"):
            lines = content.split("\n")
            # 找到 JSON 内容
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                elif line.strip() == "```" and in_block:
                    break
                elif in_block:
                    json_lines.append(line)

            if json_lines:
                content = "\n".join(json_lines)

        # 尝试解析 JSON
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试找到第一个 { 和最后一个 }
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                try:
                    return json.loads(content[start:end + 1])
                except json.JSONDecodeError as e:
                    logger.error(f"JSON 解析失败: {e}")
                    logger.debug(f"原始内容: {content[:500]}")
                    raise AIAnalysisError(
                        "AI 返回内容无法解析为 JSON",
                        detail=f"解析错误: {e}\n原始内容前500字符: {content[:500]}"
                    )
            else:
                raise AIAnalysisError(
                    "AI 返回内容中未找到 JSON",
                    detail=f"原始内容: {content[:500]}"
                )


# 便捷函数
def analyze_drawing(
    api_key: str,
    model: str,
    base_url: Optional[str],
    image_path: Optional[Union[str, Path]],
    dxf_json: Dict[str, Any],
    rules_json: Dict[str, Any],
    drawing_name: str = "",
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    """
    便捷函数：执行 AI 审查分析

    Args:
        api_key: API Key
        model: 模型名称
        base_url: API 基础 URL
        image_path: PNG 图片路径
        dxf_json: DXF 结构化数据
        rules_json: 生产规则
        drawing_name: 图纸名称
        provider: 提供商名称

    Returns:
        审查结果字典
    """
    analyzer = AIAnalyzer(
        api_key=api_key,
        model=model,
        base_url=base_url,
        provider=provider,
    )
    return analyzer.analyze(image_path, dxf_json, rules_json, drawing_name)
