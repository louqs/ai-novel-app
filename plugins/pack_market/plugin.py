"""知识包市场插件 — 管理本地知识包 + RAG 索引."""

from __future__ import annotations

from typing import Any

from core.knowledge_pack import KnowledgePackMarket
from core.logging_config import get_logger
from core.plugin_manager import PluginManifest

logger = get_logger(__name__)


class PackMarketPlugin:
    """知识包市场插件."""

    name = "pack-market"
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None
        self._market: KnowledgePackMarket | None = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        self._market = KnowledgePackMarket(kernel)
        logger.info("知识包市场已加载", packs=len(await self._market.list_local()))

    async def on_unload(self) -> None:
        self._market = None

    @property
    def market(self) -> KnowledgePackMarket:
        if self._market is None:
            self._market = KnowledgePackMarket(self._kernel)
        return self._market


def create_manifest() -> PluginManifest:
    return PluginManifest(name="pack-market", version="0.1.0",
                          description="知识包市场 — 打包/安装/卸载/共享写作知识", dependencies=[], hooks=["on_load", "on_unload"])


def create_plugin() -> PackMarketPlugin:
    return PackMarketPlugin()
