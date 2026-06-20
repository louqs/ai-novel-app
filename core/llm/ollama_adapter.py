"""Ollama 适配器 — 本地 LLM 降级方案.

通过 httpx 调用 Ollama 的 /api/chat 端点。

用法:
    adapter = OllamaAdapter(base_url="http://localhost:11434", default_model="qwen3:14b")
    response = await adapter.complete(
        messages=[{"role": "user", "content": "写一段小说开头..."}],
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


class OllamaAdapter(BaseLLMAdapter):
    """Ollama 本地 LLM 适配器."""

    provider_name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "qwen3:14b",
        timeout: float = 300.0,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout),
        )

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
        """调用 Ollama Chat API."""
        model_name = model or self._default_model

        # Ollama 使用 'system' 等标准 role
        ollama_messages = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
            },
        }
        if temperature is not None:
            payload["options"]["temperature"] = temperature

        start = time.perf_counter()

        try:
            response = await self._call_with_retry(
                method="POST",
                url="/api/chat",
                json=payload,
            )
        except httpx.HTTPError as e:
            raise LLMError(
                f"Ollama API 错误: {e}",
                provider="ollama",
            ) from e

        elapsed = (time.perf_counter() - start) * 1000

        data = response.json()
        content = data.get("message", {}).get("content", "")

        # Ollama 的 token 统计
        tokens_in = data.get("prompt_eval_count", 0)
        tokens_out = data.get("eval_count", 0)
        finish_reason = data.get("done_reason", "stop")

        return LLMResponse(
            content=content,
            model=data.get("model", model_name),
            provider="ollama",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            finish_reason=finish_reason,
            latency_ms=round(elapsed, 2),
        )

    @retry(
        retry=retry_if_exception_type((httpx.ConnectError, httpx.RemoteProtocolError)),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def _call_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """带重试的 HTTP 调用."""
        response = await self._client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    async def _health_check(self) -> None:
        """检查 Ollama 是否可用."""
        response = await self._client.get("/api/tags")
        response.raise_for_status()

    async def close(self) -> None:
        """关闭 httpx 客户端."""
        await self._client.aclose()

    @staticmethod
    def _convert_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """确保消息格式兼容 Ollama.

        Ollama 支持 system/user/assistant/tool 角色。
        将 'system' 消息保留（Ollama 支持）。
        """
        converted: list[dict[str, str]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"
            converted.append({"role": role, "content": content})
        return converted
