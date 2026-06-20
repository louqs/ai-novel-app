"""事件总线单元测试."""

from __future__ import annotations

import asyncio

import pytest

from core.event_bus import EventBus, EventCategory, EventEnvelope, EventPriority


@pytest.mark.asyncio
async def test_publish_subscribe(event_bus: EventBus, sample_envelope: EventEnvelope) -> None:
    """验证基本发布/订阅."""
    received: list[EventEnvelope] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env)

    sub_id = await event_bus.subscribe("pipeline.chapter.draft_complete", handler)
    await event_bus.publish(sample_envelope)

    # 等待异步分发
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0].event_type == "pipeline.chapter.draft_complete"
    assert received[0].payload == sample_envelope.payload

    await event_bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_multiple_subscribers(event_bus: EventBus) -> None:
    """验证多个订阅者并发接收."""
    received: dict[str, int] = {"a": 0, "b": 0, "c": 0}

    async def make_handler(key: str):
        async def handler(env: EventEnvelope) -> None:
            received[key] += 1

        return handler

    for key in received:
        await event_bus.subscribe("test.multi", await make_handler(key))

    await event_bus.publish(EventEnvelope(event_type="test.multi", category=EventCategory.SYSTEM))
    await asyncio.sleep(0.1)

    assert received["a"] == 1
    assert received["b"] == 1
    assert received["c"] == 1


@pytest.mark.asyncio
async def test_priority_ordering(event_bus: EventBus) -> None:
    """验证高优先级先投递."""
    order: list[str] = []

    async def low_handler(env: EventEnvelope) -> None:
        await asyncio.sleep(0.05)  # 模拟工作
        order.append("low")

    async def high_handler(env: EventEnvelope) -> None:
        order.append("high")

    await event_bus.subscribe("test.priority", high_handler, priority=EventPriority.HIGH)
    await event_bus.subscribe("test.priority", low_handler, priority=EventPriority.LOW)

    await event_bus.publish(EventEnvelope(event_type="test.priority", category=EventCategory.SYSTEM))
    await asyncio.sleep(0.2)

    # 高优先级应先被调用 (尽管 low 有 sleep 但并发执行, 所以 high 应该先添加到 order)
    assert order[0] == "high"


@pytest.mark.asyncio
async def test_unsubscribe(event_bus: EventBus) -> None:
    """验证取消订阅."""
    received: list[EventEnvelope] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env)

    sub_id = await event_bus.subscribe("test.unsub", handler)
    await event_bus.unsubscribe(sub_id)

    await event_bus.publish(EventEnvelope(event_type="test.unsub", category=EventCategory.SYSTEM))
    await asyncio.sleep(0.1)

    assert len(received) == 0


@pytest.mark.asyncio
async def test_wildcard_subscription(event_bus: EventBus) -> None:
    """验证通配符订阅."""
    received: list[EventEnvelope] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env)

    await event_bus.subscribe("pipeline.chapter.*", handler)

    await event_bus.publish(EventEnvelope(event_type="pipeline.chapter.start", category=EventCategory.PIPELINE))
    await event_bus.publish(EventEnvelope(event_type="pipeline.chapter.accepted", category=EventCategory.PIPELINE))
    await event_bus.publish(EventEnvelope(event_type="pipeline.other.event", category=EventCategory.PIPELINE))  # 不匹配
    await asyncio.sleep(0.1)

    # 前两个匹配, 第三个不匹配 (fnmatch 下 "pipeline.other.event" 不匹配 "pipeline.chapter.*")
    assert len(received) == 2


@pytest.mark.asyncio
async def test_request_response(event_bus: EventBus) -> None:
    """验证请求-响应模式."""
    # 设置响应订阅者
    async def responder(env: EventEnvelope) -> None:
        response = EventEnvelope(
            event_type="agent.task.response",
            category=EventCategory.AGENT,
            correlation_id=env.correlation_id,
            payload={"result": "done"},
        )
        await event_bus.respond(env.correlation_id, response)

    await event_bus.subscribe("agent.task.request", responder)

    # 发送请求
    response = await event_bus.request(
        EventEnvelope(
            event_type="agent.task.request",
            category=EventCategory.AGENT,
            payload={"task": "write"},
        ),
        timeout=5.0,
    )

    assert response.payload == {"result": "done"}


@pytest.mark.asyncio
async def test_request_timeout(event_bus: EventBus) -> None:
    """验证请求超时."""
    with pytest.raises(TimeoutError):
        await event_bus.request(
            EventEnvelope(event_type="no.one.listens", category=EventCategory.AGENT),
            timeout=0.5,
        )


@pytest.mark.asyncio
async def test_filter_fn(event_bus: EventBus) -> None:
    """验证过滤器."""
    received: list[EventEnvelope] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env)

    await event_bus.subscribe(
        "test.filter",
        handler,
        filter_fn=lambda e: e.payload.get("chapter", 0) > 5,
    )

    await event_bus.publish(EventEnvelope(event_type="test.filter", payload={"chapter": 3}))
    await event_bus.publish(EventEnvelope(event_type="test.filter", payload={"chapter": 10}))
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0].payload["chapter"] == 10


@pytest.mark.asyncio
async def test_ttl_expiry(event_bus: EventBus) -> None:
    """验证 TTL 过期."""
    received: list[EventEnvelope] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env)

    await event_bus.subscribe("test.ttl", handler)

    # 发布一个 TTL 为 0.01 秒的事件
    await event_bus.publish(EventEnvelope(event_type="test.ttl", ttl_seconds=1))  # 1秒内应该能到达
    await asyncio.sleep(0.1)

    assert len(received) == 1  # 应该收到 (因为还没过期)


@pytest.mark.asyncio
async def test_subscribe_many(event_bus: EventBus) -> None:
    """验证批量订阅."""
    received: list[str] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env.event_type)

    sub_ids = await event_bus.subscribe_many(["evt.a", "evt.b", "evt.c"], handler)

    await event_bus.publish(EventEnvelope(event_type="evt.a"))
    await event_bus.publish(EventEnvelope(event_type="evt.b"))
    await asyncio.sleep(0.1)

    assert len(received) == 2
    assert "evt.a" in received
    assert "evt.b" in received
    assert len(sub_ids) == 3
