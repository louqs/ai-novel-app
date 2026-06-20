"""上下文管理器 — 命名空间化的共享状态存储.

插件不持有可变内部状态，全部通过上下文管理器读写。
支持 TTL 自动逐出、写锁事务。

规范命名空间:
    project:<id>       — 项目级设定、角色、进度
    session:<id>       — 会话级临时状态
    pipeline:<id>      — 流水线阶段、门禁结果
    agent:<name>       — Agent 工作草稿
    global             — 跨项目配置

用法:
    ctx = ContextManager()
    await ctx.start()

    # 基本读写
    await ctx.set("project:proj_001", "current_chapter", 5)
    chapter = await ctx.get("project:proj_001", "current_chapter")  # -> 5

    # 事务
    async with ctx.transaction("project:proj_001") as tx:
        tx["current_chapter"] = 6
        tx["total_words"] += 3000
    # 自动提交 (无异常) 或 回滚 (异常)
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from core.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# 接口
# =============================================================================


class IContextManager:
    """上下文管理器公共接口."""

    async def get(self, namespace: str, key: str, default: Any = None) -> Any:  # noqa: D401
        raise NotImplementedError

    async def set(self, namespace: str, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        raise NotImplementedError

    async def delete(self, namespace: str, key: str) -> None:
        raise NotImplementedError

    async def list_keys(self, namespace: str) -> list[str]:
        raise NotImplementedError

    async def get_namespace(self, namespace: str) -> dict[str, Any]:
        raise NotImplementedError

    async def set_many(self, namespace: str, items: dict[str, Any]) -> None:
        raise NotImplementedError

    @asynccontextmanager
    async def transaction(self, namespace: str) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError


# =============================================================================
# 实现
# =============================================================================


class ContextManager(IContextManager):
    """基于内存的命名空间 KV 存储.

    特性:
        - asyncio.Lock 每命名空间, 保证写安全
        - TTL 逐出 (后台任务每 30 秒)
        - transaction() 写锁事务
    """

    def __init__(self, eviction_interval: float = 30.0) -> None:
        self._store: dict[str, dict[str, tuple[Any, float, int | None]]] = {}
        #  _store[ns][key] = (value, inserted_at_unix, ttl_seconds)

        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()  # 保护 _store 和 _locks 创建
        self._eviction_interval = eviction_interval
        self._eviction_task: asyncio.Task[None] | None = None
        self._running = False

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动后台 TTL 逐出任务."""
        self._running = True
        self._eviction_task = asyncio.create_task(self._eviction_loop())
        logger.info("上下文管理器已启动", eviction_interval=self._eviction_interval)

    async def stop(self) -> None:
        """停止上下文管理器."""
        self._running = False
        if self._eviction_task:
            self._eviction_task.cancel()
            try:
                await self._eviction_task
            except asyncio.CancelledError:
                pass
            self._eviction_task = None
        logger.info("上下文管理器已停止")

    # ---- 基本操作 ----

    async def get(self, namespace: str, key: str, default: Any = None) -> Any:
        """获取命名空间中的值."""
        ns = self._store.get(namespace)
        if ns is None:
            return default
        entry = ns.get(key)
        if entry is None:
            return default
        value, _inserted_at, ttl = entry
        # 检查 TTL
        if ttl is not None:
            if time.time() - _inserted_at > ttl:
                await self.delete(namespace, key)
                return default
        return value

    async def set(self, namespace: str, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """设置命名空间中的值."""
        lock = await self._get_lock(namespace)
        async with lock:
            if namespace not in self._store:
                self._store[namespace] = {}
            self._store[namespace][key] = (value, time.time(), ttl_seconds)

    async def delete(self, namespace: str, key: str) -> None:
        """删除命名空间中的键."""
        lock = await self._get_lock(namespace)
        async with lock:
            ns = self._store.get(namespace)
            if ns and key in ns:
                del ns[key]
                # 清理空命名空间
                if not ns:
                    del self._store[namespace]

    async def list_keys(self, namespace: str) -> list[str]:
        """列出命名空间中的所有键."""
        ns = self._store.get(namespace)
        if ns is None:
            return []
        # 过滤过期键
        now = time.time()
        valid = []
        for key, (_value, inserted_at, ttl) in ns.items():
            if ttl is not None and now - inserted_at > ttl:
                continue
            valid.append(key)
        return valid

    async def get_namespace(self, namespace: str) -> dict[str, Any]:
        """获取整个命名空间的当前快照 (不含过期键)."""
        ns = self._store.get(namespace)
        if ns is None:
            return {}
        now = time.time()
        result: dict[str, Any] = {}
        for key, (value, inserted_at, ttl) in ns.items():
            if ttl is not None and now - inserted_at > ttl:
                continue
            result[key] = value
        return result

    async def set_many(self, namespace: str, items: dict[str, Any]) -> None:
        """批量设置命名空间中的值."""
        lock = await self._get_lock(namespace)
        async with lock:
            if namespace not in self._store:
                self._store[namespace] = {}
            for key, value in items.items():
                self._store[namespace][key] = (value, time.time(), None)

    # ---- 事务 ----

    @asynccontextmanager
    async def transaction(self, namespace: str) -> AsyncIterator[dict[str, Any]]:
        """获取写锁并提供一个可修改的快照.

        退出时:
            - 无异常 → 提交 (写回 _store)
            - 有异常 → 回滚 (丢弃修改)

        用法:
            async with ctx.transaction("project:proj_001") as tx:
                tx["current_chapter"] = 6
                tx["total_words"] += 3000
                # 可能抛出异常 ...
        """
        lock = await self._get_lock(namespace)
        async with lock:
            # 快照
            snapshot = await self.get_namespace(namespace)
            snapshot_copy = dict(snapshot)
            try:
                yield snapshot_copy
            except Exception:
                # 回滚: 不做任何持久化
                logger.debug("事务回滚", namespace=namespace)
                raise
            else:
                # 提交: 写回
                if namespace not in self._store:
                    self._store[namespace] = {}
                now = time.time()
                for key, value in snapshot_copy.items():
                    self._store[namespace][key] = (value, now, None)
                # 删除在事务中被移除的键
                removed = set(snapshot.keys()) - set(snapshot_copy.keys())
                for key in removed:
                    self._store[namespace].pop(key, None)
                logger.debug("事务已提交", namespace=namespace, keys=len(snapshot_copy))

    # ---- 内部 ----

    async def _get_lock(self, namespace: str) -> asyncio.Lock:
        """获取或创建命名空间级别的锁."""
        if namespace in self._locks:
            return self._locks[namespace]
        async with self._global_lock:
            if namespace not in self._locks:
                self._locks[namespace] = asyncio.Lock()
            return self._locks[namespace]

    async def _eviction_loop(self) -> None:
        """后台 TTL 逐出循环."""
        while self._running:
            try:
                await asyncio.sleep(self._eviction_interval)
                await self._evict_expired()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("TTL 逐出异常")

    async def _evict_expired(self) -> None:
        """逐出所有命名空间中过期的键."""
        now = time.time()
        total_evicted = 0
        for ns_name, ns_data in list(self._store.items()):
            lock = self._locks.get(ns_name)
            if lock is None:
                continue
            async with lock:
                expired_keys = []
                for key, (_value, inserted_at, ttl) in ns_data.items():
                    if ttl is not None and now - inserted_at > ttl:
                        expired_keys.append(key)
                for key in expired_keys:
                    del ns_data[key]
                    total_evicted += 1
                # 清理空命名空间
                if not ns_data:
                    del self._store[ns_name]

        if total_evicted > 0:
            logger.debug("TTL 逐出完成", evicted=total_evicted)
