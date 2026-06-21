"""IKernelAPI 实现 — 将内核各组件连接在一起.

插件通过 on_load(kernel: IKernelAPI) 获得此实例，
可以访问事件总线、配置、上下文、LLM 路由、模型注册中心、RAG 检索等。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Coroutine

from core.config_manager import ConfigManager
from core.context_manager import ContextManager, IContextManager
from core.event_bus import IEventBus
from core.kernel_api import IKernelAPI
from core.llm.adapter import BaseLLMAdapter
from core.llm.claude_adapter import ClaudeAdapter
from core.llm.ollama_adapter import OllamaAdapter
from core.llm.openai_compatible_adapter import OpenAICompatibleAdapter
from core.llm.registry import ModelRegistry
from core.llm.types import LLMTier
from core.logging_config import get_logger
from core.plugin_manager import PluginEntry, PluginManager
from rag.retrieval import RetrievalEngine

logger = get_logger(__name__)


class Kernel(IKernelAPI):
    """内核实现 — 所有核心服务的门面."""

    def __init__(
        self,
        event_bus: IEventBus,
        plugin_manager: PluginManager,
        config_manager: ConfigManager,
        context_manager: ContextManager,
        model_registry: ModelRegistry | None = None,
        data_dir: str | Path = "./novel_output",
        database: Any = None,
    ) -> None:
        self.event_bus = event_bus
        self._plugin_manager = plugin_manager
        self._config_manager = config_manager
        self._context_manager = context_manager
        self._model_registry = model_registry
        self._data_dir = Path(data_dir)
        self._retrieval_engine: RetrievalEngine | None = None
        self.db = database  # DatabaseManager 实例

    # ---- 模型注册中心 ----

    @property
    def model_registry(self) -> ModelRegistry | None:
        return self._model_registry

    async def setup_providers_from_config(self) -> ModelRegistry:
        """从配置自动创建并注册所有 Provider。

        调用时机: 应用启动时，ModelRouter 被替换为 ModelRegistry。
        """
        if self._model_registry is not None:
            return self._model_registry

        config = self._config_manager.get_all()
        registry = ModelRegistry(config, db=self.db)

        # 从 providers 配置加载
        providers_cfg = config.get("providers", [])
        for provider_cfg in providers_cfg:
            name = provider_cfg.get("name", "")
            ptype = provider_cfg.get("type", "openai_compatible")
            api_key_env = provider_cfg.get("api_key_env", "")
            api_key = os.getenv(api_key_env, "")
            default_model = provider_cfg.get("default_model", "")

            if not api_key and ptype != "ollama":
                continue  # 没有 API Key 的 Provider 跳过，避免无意义降级
            try:
                if ptype == "openai_compatible":
                    base_url = provider_cfg.get("base_url", "")
                    if base_url:
                        adapter = OpenAICompatibleAdapter(
                            name=name, base_url=base_url,
                            api_key=api_key, default_model=default_model,
                        )
                        registry.register_adapter(adapter)
                        logger.info("Provider 已注册", name=name, type=ptype)

                elif ptype == "claude":
                    adapter = ClaudeAdapter(api_key=api_key)
                    adapter.provider_name = name
                    registry.register_adapter(adapter)
                    logger.info("Provider 已注册", name=name, type=ptype)

                elif ptype == "ollama":
                    base_url = provider_cfg.get("base_url", "http://localhost:11434")
                    adapter = OllamaAdapter(base_url=base_url, default_model=default_model)
                    adapter.provider_name = name
                    registry.register_adapter(adapter)
                    logger.info("Provider 已注册", name=name, type=ptype)
            except Exception:
                logger.warning("Provider 注册失败", name=name, type=ptype)

        # 从数据库加载额外 Provider
        if self.db:
            for p in await self.db.list_providers_db():
                name = p["name"]
                if registry.get_adapter(name):
                    continue  # 已存在
                try:
                    adapter = OpenAICompatibleAdapter(
                        name=name, base_url=p.get("base_url",""),
                        api_key=p.get("api_key",""), default_model=p.get("default_model",""),
                    )
                    registry.register_adapter(adapter)
                    logger.info("从数据库加载 Provider", name=name)
                except Exception:
                    pass

            # 从数据库加载保存的 tier 配置
            await registry.load_from_database()

        self._model_registry = registry
        logger.info("模型注册中心已初始化", providers=len(registry.list_providers()))
        return registry

    # ---- 检索引擎 ----

    def set_retrieval_engine(self, engine: RetrievalEngine) -> None:
        self._retrieval_engine = engine

    # ---- 插件注册表 ----

    async def get_plugin(self, name: str) -> PluginEntry:
        return await self._plugin_manager.get(name)

    # ---- 配置 ----

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config_manager.get(key, default)

    async def on_config_change(self, key: str, callback: Callable[..., Coroutine]) -> None:
        await self._config_manager.on_change(key, callback)

    # ---- 上下文管理 ----

    def context(self) -> IContextManager:
        return self._context_manager

    # ---- LLM 调用 ----

    async def call_llm(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "standard",
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """调用 LLM — 通过 ModelRegistry 路由。

        新增 provider/model 参数，允许调用方临时切换到任意模型。
        """
        registry = self._model_registry
        if registry is None:
            raise RuntimeError("模型注册中心未初始化")

        try:
            tier_enum = LLMTier(tier)
        except ValueError:
            tier_enum = LLMTier.STANDARD

        result = await registry.complete(
            messages=messages,
            tier=tier_enum,
            max_tokens_override=max_tokens,
            temperature_override=temperature,
            tools=tools,
            provider_override=provider,
            model_override=model,
        )
        return {
            "content": result.content,
            "model": result.model,
            "provider": result.provider,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "finish_reason": result.finish_reason,
            "latency_ms": result.latency_ms,
        }

    async def call_llm_stream(self, messages, *, tier="standard", provider=None, model=None, **kwargs):
        """流式调用 LLM——逐 token 异步生成器。"""
        registry = self._model_registry
        if registry is None:
            raise RuntimeError("模型注册中心未初始化")
        async for token in registry.stream(messages, tier=tier, provider_override=provider, model_override=model, **kwargs):
            yield token

    # ---- RAG / 知识图谱 / 日志 / 文件 I/O (同前) ----

    async def rag_retrieve(
        self,
        query: str,
        project_id: str,
        *,
        top_k: int = 4,
        bm25_candidates: int = 8,
        categories: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self._retrieval_engine is None:
            return []
        from models.rag import DocumentCategory
        cats = None
        if categories:
            cats = [DocumentCategory(c) for c in categories]
        results = await self._retrieval_engine.retrieve(
            query=query, project_id=project_id, top_k=top_k, categories=cats,
        )
        return [
            {"doc_id": r.doc.doc_id, "category": r.doc.category.value, "content": r.doc.content,
             "score": r.combined_score, "rank": r.rank, "metadata": r.doc.metadata}
            for r in results
        ]

    async def graph_query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        try:
            plugin = await self._plugin_manager.get("graph-manager")
            if plugin.instance and hasattr(plugin.instance, '_store') and plugin.instance._store:
                return await plugin.instance._store.execute(cypher, params or {})
        except Exception:
            pass
        return []

    def get_logger(self, name: str) -> Any:
        return get_logger(name, plugin=name)

    async def read_project_file(self, project_id: str, path: str) -> str:
        full_path = self._data_dir / project_id / path
        if not full_path.exists():
            raise FileNotFoundError(f"文件不存在: {full_path}")
        return full_path.read_text(encoding="utf-8")

    async def write_project_file(self, project_id: str, path: str, content: str) -> None:
        full_path = self._data_dir / project_id / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    def get_project_dir(self, project_id: str) -> Path:
        return self._data_dir / project_id
