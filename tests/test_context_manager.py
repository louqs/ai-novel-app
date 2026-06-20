"""上下文管理器单元测试."""

from __future__ import annotations

import asyncio

import pytest

from core.context_manager import ContextManager


@pytest.mark.asyncio
async def test_basic_set_get(context_manager: ContextManager) -> None:
    """验证基本读写."""
    await context_manager.set("project:test", "chapter", 5)
    value = await context_manager.get("project:test", "chapter")
    assert value == 5


@pytest.mark.asyncio
async def test_default_value(context_manager: ContextManager) -> None:
    """验证默认值."""
    value = await context_manager.get("nonexistent", "key", "fallback")
    assert value == "fallback"

    value = await context_manager.get("nonexistent", "key")
    assert value is None


@pytest.mark.asyncio
async def test_delete(context_manager: ContextManager) -> None:
    """验证删除."""
    await context_manager.set("project:test", "key", "value")
    await context_manager.delete("project:test", "key")
    value = await context_manager.get("project:test", "key")
    assert value is None


@pytest.mark.asyncio
async def test_list_keys(context_manager: ContextManager) -> None:
    """验证列出键."""
    await context_manager.set("project:test", "a", 1)
    await context_manager.set("project:test", "b", 2)
    await context_manager.set("project:test", "c", 3)

    keys = await context_manager.list_keys("project:test")
    assert set(keys) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_get_namespace(context_manager: ContextManager) -> None:
    """验证获取命名空间快照."""
    await context_manager.set_many("project:test", {"chapter": 5, "word_count": 3000})

    snapshot = await context_manager.get_namespace("project:test")
    assert snapshot == {"chapter": 5, "word_count": 3000}


@pytest.mark.asyncio
async def test_set_many(context_manager: ContextManager) -> None:
    """验证批量设置."""
    await context_manager.set_many("project:test", {"x": 1, "y": 2, "z": 3})

    assert await context_manager.get("project:test", "x") == 1
    assert await context_manager.get("project:test", "y") == 2
    assert await context_manager.get("project:test", "z") == 3


@pytest.mark.asyncio
async def test_ttl_expiry(context_manager: ContextManager) -> None:
    """验证 TTL 过期."""
    await context_manager.set("project:test", "temp", "data", ttl_seconds=1)

    # 立即检查 — 应该还在
    assert await context_manager.get("project:test", "temp") == "data"

    # 等待过期
    await asyncio.sleep(1.5)

    # 应该没了
    assert await context_manager.get("project:test", "temp") is None


@pytest.mark.asyncio
async def test_transaction_commit(context_manager: ContextManager) -> None:
    """验证事务提交."""
    await context_manager.set("project:test", "before", "old_value")

    async with context_manager.transaction("project:test") as tx:
        tx["before"] = "new_value"
        tx["extra"] = "added"

    assert await context_manager.get("project:test", "before") == "new_value"
    assert await context_manager.get("project:test", "extra") == "added"


@pytest.mark.asyncio
async def test_transaction_rollback(context_manager: ContextManager) -> None:
    """验证事务回滚."""
    await context_manager.set("project:test", "before", "safe_value")

    try:
        async with context_manager.transaction("project:test") as tx:
            tx["before"] = "changed_value"
            tx["extra"] = "should_not_persist"
            raise ValueError("模拟异常")
    except ValueError:
        pass

    # 回滚: before 保持原值, extra 不存在
    assert await context_manager.get("project:test", "before") == "safe_value"
    assert await context_manager.get("project:test", "extra") is None


@pytest.mark.asyncio
async def test_namespace_isolation(context_manager: ContextManager) -> None:
    """验证命名空间隔离."""
    await context_manager.set("ns:a", "key", "value_a")
    await context_manager.set("ns:b", "key", "value_b")

    assert await context_manager.get("ns:a", "key") == "value_a"
    assert await context_manager.get("ns:b", "key") == "value_b"


@pytest.mark.asyncio
async def test_concurrent_access(context_manager: ContextManager) -> None:
    """验证并发写安全性."""
    async def writer(start: int) -> None:
        for i in range(start, start + 50):
            await context_manager.set("project:concurrent", f"key_{i}", i)

    await asyncio.gather(writer(0), writer(50), writer(100))

    keys = await context_manager.list_keys("project:concurrent")
    assert len(keys) == 150
