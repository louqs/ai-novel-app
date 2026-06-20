"""模型路由器 — 根据 (tier, task_type) 路由到对应适配器.

配置驱动:
    llm:
      tiers:
        premium:
          provider: claude
          model: claude-opus-4-8-20250514
        standard:
          provider: claude
          model: claude-sonnet-4-6-20250514
        budget:
          provider: claude
          model: claude-haiku-4-5-20250514

用法:
    router = ModelRouter(config)
    router.register_adapter("claude", claude_adapter)
    router.register_adapter("ollama", ollama_adapter)

    response = await router.complete(
        tier=LLMTier.PREMIUM,
        messages=[{"role": "user", "content": "写小说..."}],
    )
"""

from __future__ import annotations

import time
from typing import Any

from core.llm.adapter import BaseLLMAdapter
from core.llm.types import LLMError, LLMResponse, LLMTier
from core.logging_config import get_logger

logger = get_logger(__name__)


class ModelRouter:
    """多层模型路由器.

    维护 provider_name → adapter 的映射。
    根据 tier 从配置读取 provider 和 model，路由到对应 adapter。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """初始化.

        Args:
            config: 完整的应用配置 dict (通常来自 ConfigManager.get_all()).
        """
        self._config = config
        self._adapters: dict[str, BaseLLMAdapter] = {}
        self._tier_configs: dict[LLMTier, dict[str, Any]] = {}

        self._load_tier_configs()

    # ---- Adapter 管理 ----

    def register_adapter(self, provider_name: str, adapter: BaseLLMAdapter) -> None:
        """注册一个 Provider 适配器."""
        self._adapters[provider_name] = adapter
        logger.info("LLM 适配器已注册", provider=provider_name)

    def unregister_adapter(self, provider_name: str) -> None:
        """移除适配器."""
        self._adapters.pop(provider_name, None)

    def get_adapter(self, provider_name: str) -> BaseLLMAdapter | None:
        """获取适配器."""
        return self._adapters.get(provider_name)

    # ---- 路由 ----

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        tier: LLMTier = LLMTier.STANDARD,
        task_type: str = "",
        max_tokens_override: int | None = None,
        temperature_override: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """按 tier 路由调用 LLM.

        Args:
            messages: 标准消息列表 [{"role": "...", "content": "..."}].
            tier: 模型层级.
            task_type: 任务类型 (用于日志).
            max_tokens_override: 覆盖配置的 max_tokens.
            temperature_override: 覆盖配置的 temperature.
            tools: 工具定义列表.

        Returns:
            LLMResponse (统一格式).

        Raises:
            LLMError: 路由失败或所有 provider 不可用.
        """
        tier_config = self._tier_configs.get(tier)
        if tier_config is None:
            raise LLMError(f"未知的 tier: {tier}")

        provider = tier_config.get("provider", "claude")
        model = tier_config.get("model", "")
        max_tokens = max_tokens_override or tier_config.get("max_tokens", 4096)
        temperature = temperature_override if temperature_override is not None else tier_config.get("temperature")

        start = time.perf_counter()

        # 主 Provider
        adapter = self._adapters.get(provider)
        if adapter is not None:
            try:
                result = await adapter.complete(
                    messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=tools,
                    **kwargs,
                )
                logger.debug(
                    "LLM 调用完成",
                    tier=tier.value,
                    provider=provider,
                    model=result.model,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=result.latency_ms,
                    task=task_type,
                )
                return result
            except Exception as exc:
                logger.warning("LLM 主 Provider 失败, 尝试降级", provider=provider, error=str(exc))

        # 降级: 尝试其他 provider
        fallback_order = self._get_fallback_order(provider)
        for fallback_provider in fallback_order:
            fallback_adapter = self._adapters.get(fallback_provider)
            if fallback_adapter is None:
                continue
            try:
                result = await fallback_adapter.complete(
                    messages,
                    model="",  # 用 fallback provider 的默认模型
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=tools,
                    **kwargs,
                )
                elapsed = (time.perf_counter() - start) * 1000
                logger.warning(
                    "LLM 降级调用完成",
                    original_provider=provider,
                    fallback_provider=fallback_provider,
                    model=result.model,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=elapsed,
                )
                return result
            except Exception as exc:
                logger.warning("LLM 降级 Provider 也失败", provider=fallback_provider, error=str(exc))

        raise LLMError(
            f"所有 LLM Provider 均不可用 (尝试了 {[provider] + fallback_order})",
            provider=provider,
        )

    async def health_check_all(self) -> dict[str, dict[str, Any]]:
        """检查所有适配器的健康状态."""
        results = {}
        for name, adapter in self._adapters.items():
            healthy, error = await adapter.health_check()
            results[name] = {"healthy": healthy, "error": error}
        return results

    # ---- 内部 ----

    def _load_tier_configs(self) -> None:
        """从配置加载 tier 映射."""
        llm_config = self._config.get("llm", {})
        tiers_config = llm_config.get("tiers", {})

        tier_map = {
            "premium": LLMTier.PREMIUM,
            "standard": LLMTier.STANDARD,
            "budget": LLMTier.BUDGET,
        }

        for tier_name, tier_enum in tier_map.items():
            tier_data = tiers_config.get(tier_name, {})
            if tier_data:
                self._tier_configs[tier_enum] = dict(tier_data)

        # 确保所有 tier 都有默认值
        defaults: dict[LLMTier, dict[str, Any]] = {
            LLMTier.PREMIUM: {"provider": "claude", "model": "claude-opus-4-8-20250514", "max_tokens": 8192},
            LLMTier.STANDARD: {"provider": "claude", "model": "claude-sonnet-4-6-20250514", "max_tokens": 4096},
            LLMTier.BUDGET: {"provider": "claude", "model": "claude-haiku-4-5-20250514", "max_tokens": 2048},
        }
        for tier, default in defaults.items():
            if tier not in self._tier_configs:
                self._tier_configs[tier] = default

    def _get_fallback_order(self, failed_provider: str) -> list[str]:
        """返回降级尝试的 provider 顺序."""
        all_providers = list(self._adapters.keys())
        # 先 Ollama (本地), 再其他
        order = []
        if "ollama" in all_providers and failed_provider != "ollama":
            order.append("ollama")
        for p in all_providers:
            if p not in order and p != failed_provider:
                order.append(p)
        return order
