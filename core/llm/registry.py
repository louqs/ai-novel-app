"""模型注册中心 — 多 Provider 管理 + 运行时热切换。

特性:
    1. 注册任意数量的 LLM Provider（Claude/Ollama/OpenAI Compatible）
    2. 管理每个 Provider 下的模型列表
    3. 运行时热切换默认模型（无需重启）
    4. 按 tier 路由时自动选择当前激活的 Provider
    5. 连接测试

用法:
    registry = ModelRegistry()

    # 注册 Provider
    registry.register_adapter(OpenAICompatibleAdapter(name="deepseek", base_url=..., api_key=...))
    registry.register_adapter(ClaudeAdapter(api_key=...))

    # 设置各 tier 的默认模型
    registry.set_tier_model("premium", "deepseek", "deepseek-chat")
    registry.set_tier_model("standard", "deepseek", "deepseek-chat")

    # 热切换
    registry.switch_tier_model("premium", "claude", "claude-opus-4-8-20250514")

    # 路由调用
    response = await registry.complete(tier="premium", messages=[...])
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from core.llm.adapter import BaseLLMAdapter
from core.llm.types import LLMError, LLMResponse, LLMTier
from core.logging_config import get_logger

logger = get_logger(__name__)


class ModelRegistry:
    """多 Provider 模型注册中心。

    管理三层映射:
        LLMTier → (provider_name, model_name) → BaseLLMAdapter → API 调用
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._adapters: dict[str, BaseLLMAdapter] = {}
        # _tier_map[tier] = {"provider": str, "model": str}
        self._tier_map: dict[LLMTier, dict[str, str]] = {}
        self._config = config or {}
        self._lock = asyncio.Lock()

        # 从配置初始化 tier 映射
        self._load_tier_defaults()

    # =========================================================================
    # Provider 管理
    # =========================================================================

    def register_adapter(self, adapter: BaseLLMAdapter) -> None:
        """注册一个 Provider 适配器。

        adapter.provider_name 作为唯一标识。
        """
        self._adapters[adapter.provider_name] = adapter
        logger.info("LLM Provider 已注册", provider=adapter.provider_name)

    def unregister(self, provider_name: str) -> None:
        """移除一个 Provider。"""
        self._adapters.pop(provider_name, None)
        # 清理引用该 provider 的 tier
        for tier, mapping in self._tier_map.items():
            if mapping.get("provider") == provider_name:
                mapping["provider"] = ""
                mapping["model"] = ""
        logger.info("LLM Provider 已移除", provider=provider_name)

    def get_adapter(self, provider_name: str) -> BaseLLMAdapter | None:
        """获取指定 Provider 的适配器。"""
        return self._adapters.get(provider_name)

    def list_providers(self) -> list[dict[str, Any]]:
        """列出所有已注册的 Provider 及其健康状态。"""
        return [
            {
                "name": name,
                "type": type(adapter).__name__,
                "default_model": getattr(adapter, "_default_model", ""),
            }
            for name, adapter in self._adapters.items()
        ]

    # =========================================================================
    # Tier → Model 映射
    # =========================================================================

    def set_tier_model(self, tier: str | LLMTier, provider_name: str, model_name: str) -> None:
        """设置某个 tier 使用哪个 Provider 的哪个模型。

        Args:
            tier: "premium" | "standard" | "budget"
            provider_name: 已注册的 Provider 名称
            model_name: 该 Provider 的模型名称
        """
        if isinstance(tier, str):
            tier = LLMTier(tier)

        if provider_name not in self._adapters:
            raise ValueError(f"Provider '{provider_name}' 未注册。可用: {list(self._adapters.keys())}")

        self._tier_map[tier] = {"provider": provider_name, "model": model_name}
        logger.info("Tier 模型已设置", tier=tier.value, provider=provider_name, model=model_name)

    def get_tier_model(self, tier: str | LLMTier) -> dict[str, str]:
        """获取某个 tier 当前使用的 Provider 和模型。"""
        if isinstance(tier, str):
            tier = LLMTier(tier)
        return self._tier_map.get(tier, {"provider": "", "model": ""})

    def list_tier_models(self) -> dict[str, dict[str, str]]:
        """列出所有 tier 的模型映射。"""
        return {
            tier.value: mapping
            for tier, mapping in self._tier_map.items()
        }

    async def switch_tier_model(self, tier: str | LLMTier, provider_name: str, model_name: str) -> dict[str, Any]:
        """热切换某 tier 的模型（运行时生效，无需重启）。

        返回切换后的状态。
        """
        async with self._lock:
            self.set_tier_model(tier, provider_name, model_name)
            # 验证连通性
            adapter = self._adapters.get(provider_name)
            healthy = False
            error_msg = None
            if adapter:
                healthy, error_msg = await adapter.health_check()
            return {
                "tier": tier.value if isinstance(tier, LLMTier) else tier,
                "provider": provider_name,
                "model": model_name,
                "healthy": healthy,
                "error": error_msg,
            }

    # =========================================================================
    # 统一调用入口
    # =========================================================================

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str | LLMTier = "standard",
        max_tokens_override: int | None = None,
        temperature_override: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        provider_override: str | None = None,
        model_override: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """按 tier 路由调用 LLM，支持覆盖 Provider 和 Model。

        降级策略:
            1. 优先使用 provider_override / model_override
            2. 否则使用 tier 映射的 Provider/Model
            3. 主 Provider 不可用时尝试其他 Provider
        """
        if isinstance(tier, str):
            try:
                tier = LLMTier(tier)
            except ValueError:
                tier = LLMTier.STANDARD

        # 确定 Provider 和 Model
        if provider_override and model_override:
            provider_name = provider_override
            model_name = model_override
        else:
            tier_mapping = self._tier_map.get(tier, {})
            provider_name = provider_override or tier_mapping.get("provider", "")
            model_name = model_override or tier_mapping.get("model", "")

        if not provider_name:
            raise LLMError(f"Tier '{tier.value}' 未配置 Provider。请先调用 set_tier_model()")

        start = time.perf_counter()
        adapter = self._adapters.get(provider_name)

        # 尝试主 Provider
        if adapter:
            try:
                max_tokens = max_tokens_override or 4096
                temperature = temperature_override

                result = await adapter.complete(
                    messages=messages,
                    model=model_name,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=tools,
                    **kwargs,
                )
                logger.debug(
                    "LLM 调用完成",
                    tier=tier.value,
                    provider=provider_name,
                    model=result.model,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=result.latency_ms,
                )
                return result
            except Exception as exc:
                logger.warning("主 Provider 调用失败，尝试降级", provider=provider_name, error=str(exc))

        # 降级：尝试其他可用的 Provider
        for fallback_name, fallback_adapter in self._adapters.items():
            if fallback_name == provider_name:
                continue
            try:
                # 用该 Provider 的默认模型
                fallback_model = getattr(fallback_adapter, "_default_model", "")
                result = await fallback_adapter.complete(
                    messages=messages,
                    model=fallback_model,
                    max_tokens=max_tokens_override or 4096,
                    temperature=temperature_override,
                    tools=tools,
                    **kwargs,
                )
                elapsed = (time.perf_counter() - start) * 1000
                logger.warning(
                    "LLM 降级调用完成",
                    original_provider=provider_name,
                    fallback_provider=fallback_name,
                    model=result.model,
                    latency_ms=elapsed,
                )
                return result
            except Exception as exc:
                logger.warning("降级 Provider 也失败", provider=fallback_name, error=str(exc))

        raise LLMError(
            f"所有 Provider 均不可用 (主={provider_name}, 共{len(self._adapters)}个)",
            provider=provider_name,
        )

    # =========================================================================
    # 健康检查
    # =========================================================================

    async def health_check_all(self) -> dict[str, dict[str, Any]]:
        """检查所有 Provider 的健康状态。"""
        results = {}
        for name, adapter in self._adapters.items():
            healthy, error = await adapter.health_check()
            results[name] = {"healthy": healthy, "error": error}
        return results

    async def stream(self, messages, *, tier="standard", provider_override=None, model_override=None, **kwargs):
        """流式调用——逐 token yield。"""
        if isinstance(tier, str):
            try: tier = LLMTier(tier)
            except ValueError: tier = LLMTier.STANDARD

        provider_name = provider_override or self._tier_map.get(tier, {}).get("provider", "")
        model_name = model_override or self._tier_map.get(tier, {}).get("model", "")
        adapter = self._adapters.get(provider_name)

        if not adapter:
            raise LLMError(f"Provider '{provider_name}' 未注册", provider=provider_name)

        async for token in adapter.stream(messages, model=model_name, **kwargs):
            yield token

    async def test_connection(self, provider_name: str) -> dict[str, Any]:
        """测试单个 Provider 的连接。"""
        adapter = self._adapters.get(provider_name)
        if not adapter:
            return {"provider": provider_name, "healthy": False, "error": "Provider 未注册"}
        healthy, error = await adapter.health_check()
        return {"provider": provider_name, "healthy": healthy, "error": error}

    # =========================================================================
    # 内部
    # =========================================================================

    def _load_tier_defaults(self) -> None:
        """从配置加载默认 tier 映射。"""
        tiers_config = self._config.get("llm", {}).get("tiers", {})
        defaults = {
            LLMTier.PREMIUM: tiers_config.get("premium", {}),
            LLMTier.STANDARD: tiers_config.get("standard", {}),
            LLMTier.BUDGET: tiers_config.get("budget", {}),
        }
        for tier, cfg in defaults.items():
            if cfg.get("provider") and cfg.get("model"):
                self._tier_map[tier] = {
                    "provider": cfg["provider"],
                    "model": cfg["model"],
                }
