"""内核 API — 暴露给插件的公共接口。

每个插件在 on_load(kernel: IKernelAPI) 时获得此接口的实例，
通过它可以访问事件总线、配置、上下文管理、LLM 路由、RAG 和知识图谱。

Phase 0 提供接口定义，具体实现在后续 Phase 中逐步完善。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine

from core.context_manager import IContextManager
from core.event_bus import IEventBus
from core.plugin_manager import PluginEntry


class IKernelAPI(ABC):
    """内核暴露给插件的公共 API 接口."""

    # ---- 事件总线 ----
    event_bus: IEventBus

    # ---- 插件注册表 (只读) ----
    @abstractmethod
    async def get_plugin(self, name: str) -> PluginEntry: ...

    # ---- 配置 ----
    @abstractmethod
    def get_config(self, key: str, default: Any = None) -> Any: ...

    @abstractmethod
    async def on_config_change(self, key: str, callback: Callable[..., Coroutine]) -> None: ...

    # ---- 上下文管理 ----
    @abstractmethod
    def context(self) -> IContextManager: ...

    # ---- LLM 路由 ----
    @abstractmethod
    async def call_llm(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "standard",
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> dict[str, Any]: ...

    # ---- RAG 检索 ----
    @abstractmethod
    async def rag_retrieve(
        self,
        query: str,
        project_id: str,
        *,
        top_k: int = 4,
        bm25_candidates: int = 8,
        categories: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    # ---- 知识图谱 ----
    @abstractmethod
    async def graph_query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    # ---- 日志 ----
    @abstractmethod
    def get_logger(self, name: str) -> Any: ...

    # ---- 项目文件 I/O ----
    @abstractmethod
    async def read_project_file(self, project_id: str, path: str) -> str: ...

    @abstractmethod
    async def write_project_file(self, project_id: str, path: str, content: str) -> None: ...
