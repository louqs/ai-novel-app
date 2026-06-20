"""LLM 通用类型定义."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class LLMTier(str, Enum):
    """模型层级 — 对应不同能力和成本的模型."""

    PREMIUM = "premium"    # 最高质量: 正文撰写、精修
    STANDARD = "standard"  # 平衡: 大纲、审查、风格
    BUDGET = "budget"      # 低成本: 元数据提取、RAG 索引


class LLMResponse(BaseModel):
    """统一的 LLM 响应格式."""

    content: str = ""
    model: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str = "stop"
    latency_ms: float = 0.0


class LLMError(Exception):
    """LLM 调用异常."""

    def __init__(self, message: str, provider: str = "", status_code: int | None = None) -> None:
        self.provider = provider
        self.status_code = status_code
        super().__init__(message)
