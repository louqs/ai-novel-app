"""插件管理器 — 管理所有业务插件的完整生命周期.

核心职责:
    1. 注册插件 (register)
    2. 按依赖拓扑顺序加载 (load / load_all)
    3. 卸载 (unload) — 反向拓扑顺序, 先卸载依赖方
    4. 依赖解析 (Kahn 算法 DAG 拓扑排序)

用法:
    from core.event_bus import EventBus
    from core.plugin_manager import PluginManager, PluginManifest

    bus = EventBus()
    pm = PluginManager(event_bus=bus)

    manifest = PluginManifest(name="my-plugin", version="1.0", dependencies=["world-builder"])
    await pm.register(manifest, plugin_instance)
    await pm.load("my-plugin")
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.events import BuiltInEvents
from core.event_bus import EventBus, EventCategory, EventEnvelope, EventPriority, IEventBus
from core.logging_config import get_logger
from core.plugin_errors import (
    CircularDependencyError,
    MissingDependencyError,
    PluginAlreadyRegisteredError,
    PluginLoadError,
    PluginNotFoundError,
)

logger = get_logger(__name__)


# =============================================================================
# 枚举 & 数据类
# =============================================================================


class PluginState(str, Enum):
    """插件生命周期状态."""

    REGISTERED = "registered"
    LOADING = "loading"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    UNLOADING = "unloading"
    UNLOADED = "unloaded"


@dataclass
class PluginManifest:
    """插件元数据 — 注册时提供."""

    name: str
    version: str
    description: str = ""
    author: str = ""
    dependencies: list[str] = field(default_factory=list)
    optional_dependencies: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PluginEntry:
    """插件运行时记录."""

    manifest: PluginManifest
    instance: Any  # 实际的插件对象 (duck-typed PluginHooks)
    state: PluginState = PluginState.REGISTERED
    loaded_at: float | None = None
    error_message: str | None = None


# =============================================================================
# 接口
# =============================================================================


class IPluginManager:
    """插件管理器公共接口."""

    async def register(self, manifest: PluginManifest, instance: Any) -> None:
        raise NotImplementedError

    async def load(self, name: str) -> None:
        raise NotImplementedError

    async def unload(self, name: str) -> None:
        raise NotImplementedError

    async def load_all(self) -> None:
        raise NotImplementedError

    async def get(self, name: str) -> PluginEntry:
        raise NotImplementedError

    async def list_active(self) -> list[PluginEntry]:
        raise NotImplementedError

    async def resolve_dependencies(self, name: str) -> list[str]:
        raise NotImplementedError


# =============================================================================
# 实现
# =============================================================================


class PluginManager(IPluginManager):
    """基于 Kahn 算法拓扑排序的插件管理器."""

    def __init__(self, event_bus: IEventBus, kernel: Any = None) -> None:
        self._event_bus = event_bus
        self._kernel = kernel
        self._registry: dict[str, PluginEntry] = {}
        self._lock = asyncio.Lock()
        self._loaded_names: set[str] = set()  # 避免重复加载

    def set_kernel(self, kernel: Any) -> None:
        self._kernel = kernel

    # ---- 注册 ----

    async def register(self, manifest: PluginManifest, instance: Any) -> None:
        """注册插件. 实例必须至少有 name 和 version 属性."""
        async with self._lock:
            if manifest.name in self._registry:
                raise PluginAlreadyRegisteredError(manifest.name)

            entry = PluginEntry(manifest=manifest, instance=instance)
            self._registry[manifest.name] = entry
            logger.info("插件已注册", name=manifest.name, version=manifest.version)

    async def register_many(self, plugins: list[tuple[PluginManifest, Any]]) -> None:
        """批量注册."""
        for manifest, instance in plugins:
            await self.register(manifest, instance)

    # ---- 加载 ----

    async def load(self, name: str) -> None:
        """加载指定插件 (含全部依赖, 按拓扑顺序)."""
        async with self._lock:
            load_order = await self._resolve_deps_topological(name)
            await self._load_in_order(load_order)

    async def load_all(self) -> None:
        """加载全部已注册插件."""
        async with self._lock:
            all_names = list(self._registry.keys())
            load_order = await self._resolve_all_topological(all_names)
            await self._load_in_order(load_order)

    async def _load_in_order(self, names: list[str]) -> None:
        """按拓扑顺序逐个加载."""
        # 用于回滚的已加载列表
        loaded_so_far: list[str] = []

        for name in names:
            entry = self._registry[name]

            if entry.state == PluginState.ACTIVE:
                logger.debug("插件已激活, 跳过", name=name)
                continue

            entry.state = PluginState.LOADING
            logger.info("正在加载插件", name=name, deps=entry.manifest.dependencies)

            try:
                # 调用 on_load 钩子 (如有)
                instance = entry.instance
                if hasattr(instance, "on_load"):
                    # 传入 kernel（如果有的话）；否则传 event_bus
                    target = self._kernel if self._kernel is not None else self._event_bus
                    try:
                        await instance.on_load(target)
                    except Exception:
                        logger.warning("插件 on_load 失败 (将在激活后重试)", name=name)

                entry.state = PluginState.ACTIVE
                loaded_so_far.append(name)
                self._loaded_names.add(name)
                logger.info("插件已激活", name=name)

                # 发布生命周期事件
                if isinstance(self._event_bus, EventBus):
                    await self._event_bus.publish(
                        EventEnvelope(
                            event_type=BuiltInEvents.PLUGIN_LOADED,
                            category=EventCategory.SYSTEM,
                            priority=EventPriority.NORMAL,
                            source="plugin_manager",
                            payload={
                                "name": entry.manifest.name,
                                "version": entry.manifest.version,
                                "hooks": entry.manifest.hooks,
                                "tools": entry.manifest.tools,
                                "skills": entry.manifest.skills,
                            },
                        )
                    )

            except Exception as exc:
                entry.state = PluginState.ERROR
                entry.error_message = str(exc)
                logger.exception("插件加载失败", name=name)

                # 回滚已加载的插件
                for loaded_name in reversed(loaded_so_far):
                    await self._force_unload(loaded_name)
                raise PluginLoadError(name, str(exc)) from exc

    # ---- 卸载 ----

    async def unload(self, name: str) -> None:
        """卸载指定插件 (含其所有依赖方, 按反向拓扑顺序)."""
        async with self._lock:
            # 找出所有依赖于 name 的已激活插件
            dependents = self._find_dependents(name)
            unload_order = [name] + dependents
            # 反向拓扑
            unload_order = list(reversed(await self._resolve_all_topological(unload_order)))

            logger.info("正在卸载插件链", names=unload_order)

            for uname in unload_order:
                await self._force_unload(uname)

    async def _force_unload(self, name: str) -> None:
        """强制卸载单个插件 (不发事件)."""
        entry = self._registry.get(name)
        if entry is None or entry.state == PluginState.UNLOADED:
            return

        entry.state = PluginState.UNLOADING
        try:
            instance = entry.instance
            if hasattr(instance, "on_unload"):
                await instance.on_unload()
        except Exception:
            logger.exception("插件卸载异常", name=name)
        finally:
            entry.state = PluginState.UNLOADED
            self._loaded_names.discard(name)
            logger.info("插件已卸载", name=name)

    # ---- 查询 ----

    async def get(self, name: str) -> PluginEntry:
        """获取插件条目."""
        entry = self._registry.get(name)
        if entry is None:
            raise PluginNotFoundError(name)
        return entry

    async def list_active(self) -> list[PluginEntry]:
        """列出所有已激活插件."""
        return [e for e in self._registry.values() if e.state == PluginState.ACTIVE]

    async def list_all(self) -> list[PluginEntry]:
        """列出所有已注册插件."""
        return list(self._registry.values())

    # ---- 依赖解析 ----

    async def resolve_dependencies(self, name: str) -> list[str]:
        """为单个插件解析依赖 (拓扑排序)."""
        async with self._lock:
            return await self._resolve_deps_topological(name)

    async def _resolve_deps_topological(self, name: str) -> list[str]:
        """Kahn 算法拓扑排序 — 从目标插件向上追溯到所有依赖.

        返回: 拓扑序列表 (目标插件在最后).
        """
        if name not in self._registry:
            raise PluginNotFoundError(name)

        # 收集涉及的插件 (name + 其所有传递依赖)
        involved = set()
        queue = deque([name])
        while queue:
            current = queue.popleft()
            if current in involved:
                continue
            if current not in self._registry:
                raise MissingDependencyError(name, current)
            involved.add(current)
            entry = self._registry[current]
            for dep in entry.manifest.dependencies:
                if dep not in involved:
                    queue.append(dep)

        return await self._resolve_all_topological(list(involved))

    async def _resolve_all_topological(self, names: list[str]) -> list[str]:
        """对给定的插件集做 Kahn 拓扑排序.

        Args:
            names: 要排序的插件名称列表.

        Returns:
            拓扑排序列表 (被依赖的在前).

        Raises:
            CircularDependencyError: 检测到循环依赖.
            MissingDependencyError: 依赖的插件不在名字列表中（且未注册）.
        """
        name_set = set(names)

        # 构建邻接表和入度表
        adj: dict[str, list[str]] = {n: [] for n in names}
        in_degree: dict[str, int] = {n: 0 for n in names}

        for name in names:
            entry = self._registry[name]
            for dep in entry.manifest.dependencies:
                if dep not in name_set:
                    # 依赖不在待解析列表中 — 检查是否已注册
                    if dep in self._registry and dep not in name_set:
                        # 已注册但不在本次排序范围, 跳过 (假定已激活)
                        continue
                    raise MissingDependencyError(name, dep)
                # dep → name (dep 必须在 name 之前加载)
                adj[dep].append(name)
                in_degree[name] += 1

        # Kahn: 入度为 0 的入队
        queue: deque[str] = deque()
        for name in names:
            if in_degree[name] == 0:
                queue.append(name)

        result: list[str] = []
        while queue:
            node = queue.popleft()
            result.append(node)
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # 检测循环依赖
        if len(result) != len(names):
            # 找出形成环的节点
            remaining = name_set - set(result)
            cycle_nodes = list(remaining)
            raise CircularDependencyError(
                name=next(iter(remaining)),
                cycle=cycle_nodes,
            )

        return result

    def _find_dependents(self, name: str) -> list[str]:
        """找出所有直接或间接依赖 name 的已激活插件."""
        dependents = []
        for entry_name, entry in self._registry.items():
            if entry.state != PluginState.ACTIVE:
                continue
            # 检查传递依赖
            all_deps = self._collect_all_deps(entry_name)
            if name in all_deps:
                dependents.append(entry_name)
        return dependents

    def _collect_all_deps(self, name: str) -> set[str]:
        """收集一个插件的所有传递依赖."""
        all_deps: set[str] = set()
        queue = deque([name])
        while queue:
            current = queue.popleft()
            entry = self._registry.get(current)
            if entry is None:
                continue
            for dep in entry.manifest.dependencies:
                if dep not in all_deps:
                    all_deps.add(dep)
                    queue.append(dep)
        return all_deps
