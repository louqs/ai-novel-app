"""事件总线 — 微内核的中枢神经系统.

支持:
    - 发布/订阅 (fan-out 到所有订阅者)
    - 请求/响应 (点对点, correlation_id 配对)
    - 优先级排序投递
    - TTL 过期
    - 通配符订阅 (event_type 后缀以 '.*' 结尾)

用法:
    bus = EventBus()
    await bus.start()

    # 订阅
    sub_id = await bus.subscribe("pipeline.chapter.accepted", my_handler)

    # 发布 (fire-and-forget)
    await bus.publish(EventEnvelope(event_type="pipeline.chapter.accepted", ...))

    # 请求/响应
    response = await bus.request(EventEnvelope(event_type="agent.task.request", ...), timeout=30)

    await bus.stop()
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from fnmatch import fnmatch
from typing import Any, Callable, Coroutine

from core.logging_config import get_logger

logger = get_logger(__name__)

# =============================================================================
# 类型别名
# =============================================================================

EventHandler = Callable[..., Coroutine[Any, Any, Any]]


# =============================================================================
# 枚举
# =============================================================================


class EventPriority(Enum):
    """事件优先级 — 数值越大越优先投递."""

    LOW = 0
    NORMAL = 50
    HIGH = 100
    CRITICAL = 200


class EventCategory(Enum):
    """事件大类 — 用于粗粒度过滤."""

    SYSTEM = auto()
    PIPELINE = auto()
    QUALITY_GATE = auto()
    MEMORY = auto()
    AGENT = auto()
    USER = auto()


# =============================================================================
# 事件信封 (不可变)
# =============================================================================


@dataclass(frozen=True)
class EventEnvelope:
    """不可变的事件信封.

    Attributes:
        event_id: 全局唯一标识
        event_type: 点号分隔的事件类型, e.g. "pipeline.chapter.draft_complete"
        category: 事件大类
        priority: 优先级
        source: 来源插件/Agent 名称
        timestamp: 发布时间 (UTC)
        payload: 载荷数据
        correlation_id: 请求-响应配对标识
        ttl_seconds: 过期时间 (None = 永不过期)
    """

    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    event_type: str = ""
    category: EventCategory = EventCategory.SYSTEM
    priority: EventPriority = EventPriority.NORMAL
    source: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    ttl_seconds: int | None = None

    @property
    def is_expired(self) -> bool:
        if self.ttl_seconds is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self.timestamp).total_seconds()
        return elapsed > self.ttl_seconds


# =============================================================================
# 订阅记录 (内部)
# =============================================================================


@dataclass(order=True)
class _SubscriberRecord:
    """内部订阅记录 — 按 priority 排序."""

    priority: int
    subscription_id: str
    handler: EventHandler = field(compare=False)
    event_type: str = field(compare=False)
    filter_fn: Callable[[EventEnvelope], bool] | None = field(compare=False)


# =============================================================================
# 事件总线接口
# =============================================================================


class IEventBus:
    """事件总线公共接口 — 供插件和内核使用."""

    async def publish(self, envelope: EventEnvelope) -> None:
        """Fire-and-forget 发布 — 所有匹配的订阅者并发收到."""
        raise NotImplementedError

    async def request(self, envelope: EventEnvelope, timeout: float = 30.0) -> EventEnvelope:
        """发布并等待恰好一个响应. 超时抛出 TimeoutError."""
        raise NotImplementedError

    async def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        filter_fn: Callable[[EventEnvelope], bool] | None = None,
    ) -> str:
        """注册处理器. 返回 subscription_id 用于取消订阅.

        event_type 支持通配符后缀 '.*' — 匹配所有以该前缀开头的事件.
        """
        raise NotImplementedError

    async def unsubscribe(self, subscription_id: str) -> None:
        """取消订阅."""
        raise NotImplementedError

    async def subscribe_many(
        self,
        event_types: list[str],
        handler: EventHandler,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        filter_fn: Callable[[EventEnvelope], bool] | None = None,
    ) -> list[str]:
        """一次订阅多个事件类型."""
        raise NotImplementedError


# =============================================================================
# 事件总线实现
# =============================================================================


class EventBus(IEventBus):
    """异步事件总线实现.

    内部结构:
        _subscribers: event_type → [排序后的 _SubscriberRecord]
        _queue: asyncio.Queue — 待分发的信封
        _pending_requests: correlation_id → asyncio.Future (用于 request-response)
        _dispatcher_task: 后台协程 — 从队列取信封并扇出给所有匹配订阅者
    """

    def __init__(self, queue_size: int = 0) -> None:
        """初始化.

        Args:
            queue_size: 事件队列最大长度. 0 = 无界.
        """
        self._subscribers: dict[str, list[_SubscriberRecord]] = {}
        self._queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=queue_size)
        self._pending_requests: dict[str, asyncio.Future[EventEnvelope]] = {}
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._running = False
        self._lock = asyncio.Lock()

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动事件总线的分发循环."""
        self._running = True
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
        logger.info("事件总线已启动")

    async def stop(self) -> None:
        """停止事件总线."""
        self._running = False
        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None

        # 取消所有待处理的请求
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        logger.info("事件总线已停止")

    # ---- 发布 ----

    async def publish(self, envelope: EventEnvelope) -> None:
        """发布事件 — 入队后立即返回."""
        if not self._running:
            logger.warning("事件总线未运行, 丢弃事件", event_type=envelope.event_type)
            return
        await self._queue.put(envelope)

    async def request(self, envelope: EventEnvelope, timeout: float = 30.0) -> EventEnvelope:
        """发布请求并等待匹配的响应.

        响应通过 correlation_id 匹配。
        """
        if not envelope.correlation_id:
            # 自动生成 correlation_id
            envelope = EventEnvelope(
                event_id=envelope.event_id,
                event_type=envelope.event_type,
                category=envelope.category,
                priority=envelope.priority,
                source=envelope.source,
                timestamp=envelope.timestamp,
                payload=envelope.payload,
                correlation_id=f"req_{uuid.uuid4().hex[:12]}",
                ttl_seconds=envelope.ttl_seconds,
            )

        future: asyncio.Future[EventEnvelope] = asyncio.Future()
        self._pending_requests[envelope.correlation_id] = future

        try:
            await self._queue.put(envelope)
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            logger.warning("请求超时", correlation_id=envelope.correlation_id)
            raise TimeoutError(f"请求超时: {envelope.correlation_id} (>{timeout}s)")
        finally:
            self._pending_requests.pop(envelope.correlation_id, None)

    async def respond(self, correlation_id: str, response: EventEnvelope) -> None:
        """发送响应并完成一个 pending request."""
        future = self._pending_requests.get(correlation_id)
        if future and not future.done():
            future.set_result(response)

    # ---- 订阅 ----

    async def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        filter_fn: Callable[[EventEnvelope], bool] | None = None,
    ) -> str:
        """订阅事件. event_type 支持 'prefix.*' 通配符."""
        sub_id = f"sub_{uuid.uuid4().hex[:12]}"
        record = _SubscriberRecord(
            priority=priority.value,
            subscription_id=sub_id,
            handler=handler,
            event_type=event_type,
            filter_fn=filter_fn,
        )

        async with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(record)
            # 保持按 priority 降序
            self._subscribers[event_type].sort(key=lambda r: r.priority, reverse=True)

        logger.debug("订阅已注册", subscription_id=sub_id, event_type=event_type)
        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        """取消订阅."""
        async with self._lock:
            for evt_type, records in self._subscribers.items():
                self._subscribers[evt_type] = [r for r in records if r.subscription_id != subscription_id]
            # 清理空列表
            empty = [k for k, v in self._subscribers.items() if not v]
            for k in empty:
                del self._subscribers[k]
        logger.debug("订阅已取消", subscription_id=subscription_id)

    async def subscribe_many(
        self,
        event_types: list[str],
        handler: EventHandler,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        filter_fn: Callable[[EventEnvelope], bool] | None = None,
    ) -> list[str]:
        """一次订阅多个事件类型."""
        sub_ids = []
        for evt_type in event_types:
            sub_id = await self.subscribe(evt_type, handler, priority=priority, filter_fn=filter_fn)
            sub_ids.append(sub_id)
        return sub_ids

    # ---- 内部 ----

    async def _dispatch_loop(self) -> None:
        """后台分发循环 — 唯一消费者, 从队列取信封并扇出."""
        while self._running:
            try:
                envelope = await self._queue.get()
            except (asyncio.CancelledError, RuntimeError):
                break

            if envelope.is_expired:
                logger.debug("丢弃过期事件", event_type=envelope.event_type, event_id=envelope.event_id)
                self._queue.task_done()
                continue

            # 解析匹配的订阅者
            subscribers = self._resolve_subscribers(envelope.event_type)

            if not subscribers:
                logger.debug("无订阅者", event_type=envelope.event_type)

            # 并发送给所有匹配的订阅者
            tasks = []
            for record in subscribers:
                # 应用过滤器
                if record.filter_fn and not record.filter_fn(envelope):
                    continue
                tasks.append(self._deliver(record.handler, envelope))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            self._queue.task_done()

    def _resolve_subscribers(self, event_type: str) -> list[_SubscriberRecord]:
        """解析所有匹配的订阅者 (精确 + 通配符)."""
        results: dict[str, _SubscriberRecord] = {}  # 按 sub_id 去重
        async def _collect() -> None:
            # 这是一个同步方法, 但需要访问 self._subscribers
            pass

        # 精确匹配优先
        exact = self._subscribers.get(event_type, [])
        for r in exact:
            results[r.subscription_id] = r

        # 通配符匹配 (已注册的 pattern 中匹配当前 event_type)
        for pattern, records in self._subscribers.items():
            if pattern == event_type:
                continue  # 已经处理
            if "*" in pattern and fnmatch(event_type, pattern):
                for r in records:
                    if r.subscription_id not in results:
                        results[r.subscription_id] = r

        return sorted(results.values(), key=lambda r: r.priority, reverse=True)

    async def _deliver(self, handler: EventHandler, envelope: EventEnvelope) -> None:
        """安全投递一个事件给处理器."""
        try:
            await handler(envelope)
        except Exception:
            logger.exception(
                "事件处理器异常",
                event_type=envelope.event_type,
                handler=str(handler),
            )
