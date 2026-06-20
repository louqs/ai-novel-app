"""pytest 共享 fixtures."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Any

import pytest
import yaml

from core.config_manager import ConfigManager
from core.context_manager import ContextManager
from core.event_bus import EventBus, EventEnvelope, EventPriority, EventCategory
from core.plugin_manager import PluginManager, PluginManifest


# =============================================================================
# Event Bus fixtures
# =============================================================================


@pytest.fixture
async def event_bus() -> AsyncGenerator[EventBus, None]:
    """启动并返回一个 EventBus 实例."""
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


@pytest.fixture
def sample_envelope() -> EventEnvelope:
    """返回一个示例事件信封."""
    return EventEnvelope(
        event_type="pipeline.chapter.draft_complete",
        category=EventCategory.PIPELINE,
        priority=EventPriority.NORMAL,
        source="test",
        payload={"chapter": 1, "word_count": 3000},
    )


# =============================================================================
# Context Manager fixtures
# =============================================================================


@pytest.fixture
async def context_manager() -> AsyncGenerator[ContextManager, None]:
    """启动并返回一个 ContextManager 实例."""
    ctx = ContextManager()
    await ctx.start()
    yield ctx
    await ctx.stop()


# =============================================================================
# Config Manager fixtures
# =============================================================================


@pytest.fixture
def temp_config_dir() -> Any:
    """创建临时配置目录."""
    tmp = tempfile.mkdtemp(prefix="novel_config_")
    yield Path(tmp)
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def default_config_yaml() -> dict[str, Any]:
    """返回默认配置 dict."""
    return {
        "app": {
            "name": "ai-novel-app-test",
            "version": "0.1.0",
            "env": "test",
            "debug": True,
            "data_dir": "./test_output",
        },
        "llm": {
            "default_tier": "standard",
            "tiers": {
                "premium": {"provider": "claude", "model": "claude-opus-test", "max_tokens": 8192},
                "standard": {"provider": "claude", "model": "claude-sonnet-test", "max_tokens": 4096},
                "budget": {"provider": "claude", "model": "claude-haiku-test", "max_tokens": 2048},
            },
        },
        "rag": {"retrieval": {"bm25_candidates": 8, "semantic_top_k": 4}},
        "logging": {"level": "DEBUG", "json_output": False},
    }


@pytest.fixture
async def config_manager(temp_config_dir: Path, default_config_yaml: dict) -> ConfigManager:
    """返回一个已加载测试配置的 ConfigManager."""
    # 写入 default.yaml
    with open(temp_config_dir / "default.yaml", "w") as f:
        yaml.dump(default_config_yaml, f)

    cm = ConfigManager(str(temp_config_dir))
    await cm.load(env="test")
    return cm


# =============================================================================
# Plugin Manager fixtures
# =============================================================================


@pytest.fixture
async def plugin_manager(event_bus: EventBus) -> PluginManager:
    """返回一个 PluginManager 实例."""
    return PluginManager(event_bus=event_bus)


class DummyPlugin:
    """测试用插件."""

    def __init__(self, name: str, version: str = "0.1.0") -> None:
        self.name = name
        self.version = version
        self.loaded = False
        self.unloaded = False
        self.kernel = None

    async def on_load(self, kernel: Any) -> None:
        self.loaded = True
        self.kernel = kernel

    async def on_unload(self) -> None:
        self.unloaded = True


@pytest.fixture
def make_plugin() -> Any:
    """工厂 fixture — 创建测试插件."""
    def _make(name: str, deps: list[str] | None = None) -> tuple[PluginManifest, DummyPlugin]:
        manifest = PluginManifest(
            name=name,
            version="0.1.0",
            description=f"Test plugin {name}",
            dependencies=deps or [],
            hooks=["on_load", "on_unload"],
        )
        return manifest, DummyPlugin(name)

    return _make


# =============================================================================
# Async helpers
# =============================================================================


@pytest.fixture(scope="session")
def event_loop():
    """Session 级事件循环."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
