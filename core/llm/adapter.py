"""LLM 适配器抽象基类."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from core.llm.types import LLMResponse


class BaseLLMAdapter(ABC):
    """LLM 适配器基类."""

    provider_name: str = "base"

    @abstractmethod
    async def _complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        ...

    async def complete(self, messages, *, model="", max_tokens=4096, temperature=None, tools=None, **kwargs) -> LLMResponse:
        return await self._complete(messages, model=model, max_tokens=max_tokens, temperature=temperature, tools=tools, **kwargs)

    async def stream(self, messages, *, model="", max_tokens=4096, temperature=None, **kwargs) -> AsyncIterator[str]:
        """流式生成——逐 token yield 文本。默认回退到非流式."""
        response = await self.complete(messages, model=model, max_tokens=max_tokens, temperature=temperature, **kwargs)
        yield response.content

    async def health_check(self) -> tuple[bool, str | None]:
        """检查适配器健康状态.

        Returns:
            (is_healthy, error_message) — 成功时 error_message 为 None
        """
        try:
            await self._health_check()
            return True, None
        except Exception as exc:
            return False, str(exc)[:200]

    async def _health_check(self) -> None:
        """子类覆盖此方法实现具体的健康检查逻辑."""
        pass
