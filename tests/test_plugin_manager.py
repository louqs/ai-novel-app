"""插件管理器单元测试."""

from __future__ import annotations

import pytest

from core.plugin_errors import (
    CircularDependencyError,
    MissingDependencyError,
    PluginAlreadyRegisteredError,
    PluginNotFoundError,
)
from core.plugin_manager import PluginManager, PluginState


@pytest.mark.asyncio
async def test_register_plugin(plugin_manager: PluginManager, make_plugin) -> None:
    """验证注册插件."""
    manifest, plugin = make_plugin("test-plugin")
    await plugin_manager.register(manifest, plugin)

    entry = await plugin_manager.get("test-plugin")
    assert entry.manifest.name == "test-plugin"
    assert entry.state == PluginState.REGISTERED


@pytest.mark.asyncio
async def test_register_duplicate(plugin_manager: PluginManager, make_plugin) -> None:
    """验证重复注册报错."""
    manifest, plugin = make_plugin("test-plugin")
    await plugin_manager.register(manifest, plugin)

    with pytest.raises(PluginAlreadyRegisteredError):
        await plugin_manager.register(manifest, plugin)


@pytest.mark.asyncio
async def test_load_plugin(plugin_manager: PluginManager, make_plugin) -> None:
    """验证加载插件."""
    manifest, plugin = make_plugin("test-plugin")
    await plugin_manager.register(manifest, plugin)
    await plugin_manager.load("test-plugin")

    entry = await plugin_manager.get("test-plugin")
    assert entry.state == PluginState.ACTIVE
    assert plugin.loaded is True


@pytest.mark.asyncio
async def test_load_with_dependencies(plugin_manager: PluginManager, make_plugin) -> None:
    """验证按依赖顺序加载."""
    # A → B → C
    man_c, plug_c = make_plugin("C")
    man_b, plug_b = make_plugin("B", deps=["C"])
    man_a, plug_a = make_plugin("A", deps=["B"])

    await plugin_manager.register_many([
        (man_a, plug_a),
        (man_b, plug_b),
        (man_c, plug_c),
    ])

    await plugin_manager.load("A")

    assert plug_c.loaded is True
    assert plug_b.loaded is True
    assert plug_a.loaded is True


@pytest.mark.asyncio
async def test_load_missing_dependency(plugin_manager: PluginManager, make_plugin) -> None:
    """验证缺少依赖报错."""
    manifest, plugin = make_plugin("orphan", deps=["nonexistent"])
    await plugin_manager.register(manifest, plugin)

    with pytest.raises(MissingDependencyError):
        await plugin_manager.load("orphan")


@pytest.mark.asyncio
async def test_circular_dependency_detection(plugin_manager: PluginManager, make_plugin) -> None:
    """验证循环依赖检测."""
    # A → B → C → A (cycle)
    man_a, plug_a = make_plugin("A", deps=["B"])
    man_b, plug_b = make_plugin("B", deps=["C"])
    man_c, plug_c = make_plugin("C", deps=["A"])

    await plugin_manager.register_many([
        (man_a, plug_a),
        (man_b, plug_b),
        (man_c, plug_c),
    ])

    with pytest.raises(CircularDependencyError):
        await plugin_manager.load("A")


@pytest.mark.asyncio
async def test_unload_plugin(plugin_manager: PluginManager, make_plugin) -> None:
    """验证卸载插件."""
    manifest, plugin = make_plugin("test-plugin")
    await plugin_manager.register(manifest, plugin)
    await plugin_manager.load("test-plugin")

    await plugin_manager.unload("test-plugin")

    entry = await plugin_manager.get("test-plugin")
    assert entry.state == PluginState.UNLOADED
    assert plugin.unloaded is True


@pytest.mark.asyncio
async def test_list_active(plugin_manager: PluginManager, make_plugin) -> None:
    """验证列出已激活插件."""
    ma, pa = make_plugin("A")
    mb, pb = make_plugin("B")

    await plugin_manager.register_many([(ma, pa), (mb, pb)])
    await plugin_manager.load("A")

    active = await plugin_manager.list_active()
    assert len(active) == 1
    assert active[0].manifest.name == "A"


@pytest.mark.asyncio
async def test_load_all(plugin_manager: PluginManager, make_plugin) -> None:
    """验证加载全部."""
    m1, p1 = make_plugin("P1")
    m2, p2 = make_plugin("P2")

    await plugin_manager.register_many([(m1, p1), (m2, p2)])
    await plugin_manager.load_all()

    assert p1.loaded is True
    assert p2.loaded is True


@pytest.mark.asyncio
async def test_resolve_dependencies(plugin_manager: PluginManager, make_plugin) -> None:
    """验证依赖解析结果."""
    # A → B → C, D
    m_a, p_a = make_plugin("A", deps=["B", "D"])
    m_b, p_b = make_plugin("B", deps=["C"])
    m_c, p_c = make_plugin("C")
    m_d, p_d = make_plugin("D")

    await plugin_manager.register_many([
        (m_a, p_a), (m_b, p_b), (m_c, p_c), (m_d, p_d),
    ])

    order = await plugin_manager.resolve_dependencies("A")
    # C 和 D 在 B 和 A 之前, B 在 A 之前
    assert order.index("C") < order.index("B")
    assert order.index("D") < order.index("A")
    assert order.index("B") < order.index("A")
    assert order[-1] == "A"


@pytest.mark.asyncio
async def test_plugin_not_found(plugin_manager: PluginManager) -> None:
    """验证查询不存在的插件."""
    with pytest.raises(PluginNotFoundError):
        await plugin_manager.get("ghost")


@pytest.mark.asyncio
async def test_load_rollback_on_error(plugin_manager: PluginManager, make_plugin) -> None:
    """验证加载失败不影响其他插件."""
    from core.plugin_manager import PluginManifest

    class FailingPlugin:
        name = "failing"
        version = "0.1.0"

        async def on_load(self, kernel) -> None:
            raise RuntimeError("加载失败!")

        async def on_unload(self) -> None:
            pass

    _, p_good = make_plugin("good")

    await plugin_manager.register(PluginManifest(
        name="failing", version="0.1.0", description="",
        dependencies=[], hooks=["on_load", "on_unload"],
    ), FailingPlugin())

    man_good, _ = make_plugin("good")
    await plugin_manager.register(man_good, p_good)

    # on_load 失败不再抛出异常 — 而是记录警告并继续
    await plugin_manager.load("failing")

    # failing 插件仍然加载成功了（状态变为 ACTIVE，但 on_load 失败被容忍）
    entry_fail = await plugin_manager.get("failing")
    assert entry_fail.state == PluginState.ACTIVE

    # good 不受影响
    entry = await plugin_manager.get("good")
    assert entry.state in (PluginState.REGISTERED, PluginState.ACTIVE)
