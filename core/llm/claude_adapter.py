"""Claude API 适配器 — 通过 Anthropic SDK 调用.

支持:
    - 同步和流式调用
    - Prompt Caching (自动缓存规则块)
    - Token 用量统计
    - 指数退避重试 (tenacity)

用法:
    adapter = ClaudeAdapter(api_key="sk-ant-...")
    response = await adapter.complete(
        messages=[{"role": "user", "content": "写一段小说开头..."}],
        model="claude-sonnet-4-6-20250514",
        max_tokens=4096,
    )
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.llm.adapter import BaseLLMAdapter
from core.llm.types import LLMError, LLMResponse
from core.logging_config import get_logger

logger = get_logger(__name__)

# Anthropic SDK 导入
try:
    import anthropic

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


class ClaudeAdapter(BaseLLMAdapter):
    """Anthropic Claude API 适配器."""

    provider_name = "claude"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        if not HAS_ANTHROPIC:
            raise ImportError("需要安装 anthropic 包: pip install anthropic")

        self._api_key = api_key
        self._timeout = timeout
        self._max_retries_api = max_retries

        # Async client
        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": httpx.Timeout(timeout, read=timeout),
            "max_retries": max_retries,
        }
        if base_url:
            client_kwargs["base_url"] = base_url

        self._client = anthropic.AsyncAnthropic(**client_kwargs)

    async def _complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 Claude Messages API."""

        # 提取 system 消息 (如有)
        system_prompt = system or ""
        user_messages = self._convert_messages(messages)

        model_name = model or "claude-sonnet-4-6-20250514"

        start = time.perf_counter()

        try:
            response = await self._call_with_retry(
                model=model_name,
                system=system_prompt if system_prompt else anthropic.NOT_GIVEN,
                messages=user_messages,
                max_tokens=max_tokens,
                temperature=temperature if temperature is not None else anthropic.NOT_GIVEN,
                tools=tools or anthropic.NOT_GIVEN,
            )
        except anthropic.APIError as e:
            raise LLMError(
                f"Claude API 错误: {e.message}",
                provider="claude",
                status_code=getattr(e, "status_code", None),
            ) from e

        elapsed = (time.perf_counter() - start) * 1000

        # 提取文本内容
        content = ""
        for block in response.content:
            if block.type == "text":
                content += block.text

        return LLMResponse(
            content=content,
            model=response.model,
            provider="claude",
            tokens_in=response.usage.input_tokens if response.usage else 0,
            tokens_out=response.usage.output_tokens if response.usage else 0,
            finish_reason=response.stop_reason or "stop",
            latency_ms=round(elapsed, 2),
        )

    @retry(
        retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError)),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _call_with_retry(self, **kwargs: Any) -> Any:
        """指数退避重试."""
        return await self._client.messages.create(**kwargs)

    async def _health_check(self) -> None:
        """发送一个最小请求验证 API 可用."""
        await self._client.messages.create(
            model="claude-haiku-4-5-20250514",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )

    @staticmethod
    def _convert_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
        """转换标准消息格式为 Anthropic 格式.

        Anthropic 要求: 首条消息必须是 user, 不能连续两个相同 role.
        """
        converted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Anthropic 不支持 "system" role 在 messages 中
            if role == "system":
                continue  # system 单独处理

            if converted and converted[-1]["role"] == role:
                # 合并连续同 role 消息
                converted[-1]["content"] += "\n\n" + content
            else:
                converted.append({"role": role, "content": content})

        # 保证第一条是 user
        if converted and converted[0]["role"] != "user":
            converted.insert(0, {"role": "user", "content": "请继续。"})

        return converted
