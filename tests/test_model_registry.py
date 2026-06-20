"""模型注册中心 + 通用适配器 测试."""

from __future__ import annotations

import pytest

from core.llm.openai_compatible_adapter import OpenAICompatibleAdapter
from core.llm.registry import ModelRegistry
from core.llm.types import LLMTier


# =============================================================================
# Registry Tests
# =============================================================================


class MockAdapter:
    """模拟 LLM 适配器——用于测试注册中心。"""

    def __init__(self, name: str, healthy: bool = True):
        self.provider_name = name
        self.name = name
        self._healthy = healthy
        self._call_count = 0
        self._default_model = f"{name}-default"

    async def health_check(self) -> tuple[bool, str | None]:
        if self._healthy:
            return True, None
        return False, "Mock unhealthy"

    async def complete(self, messages, *, model="", max_tokens=4096,
                       temperature=None, tools=None, **kwargs):
        self._call_count += 1
        if not self._healthy:
            raise RuntimeError(f"{self.name} is unhealthy")
        from core.llm.types import LLMResponse
        return LLMResponse(
            content=f"Response from {self.name}/{model or self._default_model}",
            model=model or self._default_model,
            provider=self.name,
            tokens_in=10,
            tokens_out=20,
            finish_reason="stop",
        )


@pytest.fixture
def registry_with_mocks():
    """带两个 Mock Provider 的注册中心."""
    registry = ModelRegistry()
    registry.register_adapter(MockAdapter("alpha"))
    registry.register_adapter(MockAdapter("beta"))
    registry.set_tier_model("premium", "alpha", "alpha-pro")
    registry.set_tier_model("standard", "alpha", "alpha-standard")
    registry.set_tier_model("budget", "beta", "beta-lite")
    return registry


# ---- Provider 管理 ----


def test_register_provider():
    """验证注册 Provider."""
    registry = ModelRegistry()
    registry.register_adapter(MockAdapter("test-provider"))
    assert registry.get_adapter("test-provider") is not None
    assert len(registry.list_providers()) == 1


def test_unregister_provider():
    """验证移除 Provider."""
    registry = ModelRegistry()
    registry.register_adapter(MockAdapter("temp"))
    registry.unregister("temp")
    assert registry.get_adapter("temp") is None


def test_list_providers():
    """验证列出 Provider."""
    registry = ModelRegistry()
    registry.register_adapter(MockAdapter("A"))
    registry.register_adapter(MockAdapter("B"))
    providers = registry.list_providers()
    assert len(providers) == 2
    assert {p["name"] for p in providers} == {"A", "B"}


# ---- Tier 映射 ----


def test_set_tier_model(registry_with_mocks):
    """验证设置 Tier 模型."""
    mapping = registry_with_mocks.get_tier_model("premium")
    assert mapping["provider"] == "alpha"
    assert mapping["model"] == "alpha-pro"


def test_list_tier_models(registry_with_mocks):
    """验证列出所有 Tier 映射."""
    tiers = registry_with_mocks.list_tier_models()
    assert "premium" in tiers
    assert "standard" in tiers
    assert "budget" in tiers


def test_set_tier_invalid_provider():
    """验证设置不存在的 Provider 会报错."""
    registry = ModelRegistry()
    with pytest.raises(ValueError, match="未注册"):
        registry.set_tier_model("premium", "ghost", "model")


# ---- 调用 ----


@pytest.mark.asyncio
async def test_complete_by_tier(registry_with_mocks):
    """验证按 Tier 调用."""
    result = await registry_with_mocks.complete(
        messages=[{"role": "user", "content": "Hello"}],
        tier="premium",
    )
    assert result.provider == "alpha"
    assert result.model == "alpha-pro"


@pytest.mark.asyncio
async def test_complete_override(registry_with_mocks):
    """验证调用时覆盖 Provider 和 Model."""
    result = await registry_with_mocks.complete(
        messages=[{"role": "user", "content": "Hello"}],
        tier="premium",
        provider_override="beta",
        model_override="beta-experimental",
    )
    assert result.provider == "beta"
    assert result.model == "beta-experimental"


@pytest.mark.asyncio
async def test_complete_fallback(registry_with_mocks):
    """验证主 Provider 不可用时的降级."""
    # 注册一个不健康的 Provider 作为主
    bad = MockAdapter("bad-provider", healthy=False)
    registry_with_mocks.register_adapter(bad)
    registry_with_mocks.set_tier_model("premium", "bad-provider", "bad-model")

    # 应该降级到 alpha 或 beta
    result = await registry_with_mocks.complete(
        messages=[{"role": "user", "content": "Hello"}],
        tier="premium",
    )
    assert result.provider != "bad-provider"


@pytest.mark.asyncio
async def test_switch_tier_model(registry_with_mocks):
    """验证热切换 Tier 模型."""
    result = await registry_with_mocks.switch_tier_model("premium", "beta", "beta-pro")
    assert result["provider"] == "beta"
    assert result["model"] == "beta-pro"

    mapping = registry_with_mocks.get_tier_model("premium")
    assert mapping["provider"] == "beta"


# ---- 健康检查 ----


@pytest.mark.asyncio
async def test_health_check_all(registry_with_mocks):
    """验证全量健康检查."""
    results = await registry_with_mocks.health_check_all()
    assert results["alpha"]["healthy"] is True
    assert results["beta"]["healthy"] is True


@pytest.mark.asyncio
async def test_test_connection(registry_with_mocks):
    """验证单 Provider 连接测试."""
    result = await registry_with_mocks.test_connection("alpha")
    assert result["healthy"] is True


# =============================================================================
# OpenAICompatibleAdapter Tests (unit, no real API)
# =============================================================================


def test_adapter_creation():
    """验证适配器创建."""
    adapter = OpenAICompatibleAdapter(
        name="test",
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        default_model="test-model",
    )
    assert adapter.provider_name == "test"
    assert adapter._default_model == "test-model"


def test_adapter_provider_name_override():
    """验证 provider_name 可被覆盖."""
    adapter = OpenAICompatibleAdapter(
        name="custom-name",
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        default_model="test",
    )
    assert adapter.provider_name == "custom-name"
