"""通用 OpenAI 兼容适配器 — 适配任何兼容 OpenAI API 的大模型。

支持:
    - DeepSeek:        base_url=https://api.deepseek.com/v1
    - 通义千问 (Qwen):  base_url=https://dashscope.aliyuncs.com/compatible-mode/v1
    - 智谱 (GLM):       base_url=https://open.bigmodel.cn/api/paas/v4
    - Kimi (月之暗面):   base_url=https://api.moonshot.cn/v1
    - 百川 (Baichuan):   base_url=https://api.baichuan-ai.com/v1
    - Groq:             base_url=https://api.groq.com/openai/v1
    - Together AI:      base_url=https://api.together.xyz/v1
    - 硅基流动 (SiliconFlow): base_url=https://api.siliconflow.cn/v1
    - 任何其他 OpenAI 兼容 API

用法:
    adapter = OpenAICompatibleAdapter(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-xxx",
        default_model="deepseek-chat",
    )
    response = await adapter.complete(messages=[...])
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

# httpx 错误类型用于重试
RETRYABLE = (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout)


class OpenAICompatibleAdapter(BaseLLMAdapter):
    """通用 OpenAI 兼容 API 适配器.

    通过 OpenAI 标准 /v1/chat/completions 端点通信。
    大多数国产大模型都兼容此格式。
    """

    provider_name = "openai_compatible"

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str = "",
        *,
        timeout: float = 120.0,
        max_retries: int = 3,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.provider_name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._extra_headers = extra_headers or {}

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, read=timeout),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **self._extra_headers,
            },
        )
        self._max_retries = max_retries

    # ------------------------------------------------------------------
    # BaseLLMAdapter 实现
    # ------------------------------------------------------------------

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
        """调用 OpenAI 兼容 /v1/chat/completions."""
        model_name = model or self._default_model
        if not model_name:
            raise LLMError(f"未指定模型 (provider={self.name})", provider=self.name)

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools

        start = time.perf_counter()

        try:
            response = await self._call_with_retry(
                method="POST",
                url="/v1/chat/completions",
                json=payload,
            )
        except httpx.HTTPStatusError as e:
            raise LLMError(
                f"{self.name} API 错误 [{e.response.status_code}]: {e.response.text[:500]}",
                provider=self.name,
                status_code=e.response.status_code,
            ) from e
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} 网络错误: {e}", provider=self.name) from e

        elapsed = (time.perf_counter() - start) * 1000
        data = response.json()

        # 提取内容
        choices = data.get("choices", [])
        if not choices:
            raise LLMError(f"{self.name} 返回了空 choices", provider=self.name)

        message = choices[0].get("message", {})
        content = message.get("content", "") or ""
        # 某些模型（如 DeepSeek R1）用 reasoning_content
        reasoning = message.get("reasoning_content", "")
        if reasoning and not content:
            content = reasoning

        finish_reason = choices[0].get("finish_reason", "stop")

        # Token 统计
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)

        return LLMResponse(
            content=content,
            model=data.get("model", model_name),
            provider=self.name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            finish_reason=finish_reason,
            latency_ms=round(elapsed, 2),
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float | None = None,
        **kwargs: Any,
    ):
        """流式生成——逐 token yield 文本。"""
        model_name = model or self._default_model
        payload = {"model": model_name, "messages": messages, "max_tokens": max_tokens, "stream": True}
        if temperature is not None:
            payload["temperature"] = temperature

        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(300, read=300),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json", **self._extra_headers},
        ) as client:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            chunk = json.loads(line[6:])
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                yield token
                        except json.JSONDecodeError:
                            continue

    async def _health_check(self) -> None:
        """发送最小请求验证连通性."""
        response = await self._client.post(
            "/v1/chat/completions",
            json={
                "model": self._default_model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            timeout=httpx.Timeout(15),
        )
        response.raise_for_status()

    async def list_models(self) -> list[dict[str, Any]]:
        """获取可用模型列表."""
        try:
            response = await self._client.get("/v1/models")
            response.raise_for_status()
            data = response.json()
            return data.get("data", [])
        except Exception:
            return [{"id": self._default_model, "object": "model"}]

    async def close(self) -> None:
        """释放 httpx 客户端."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # 重试
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _call_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = await self._client.request(method, url, **kwargs)
        response.raise_for_status()
        return response
