"""FastAPI 依赖注入 — 管理 Kernel 和核心服务生命周期."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

from fastapi import Request

from core.config_manager import ConfigManager
from core.context_manager import ContextManager
from core.database import DatabaseManager
from core.event_bus import EventBus
from core.kernel_impl import Kernel
from core.llm.router import ModelRouter
from core.plugin_manager import PluginManager


class AppState:
    """应用全局状态 — 持有所有核心服务引用."""

    def __init__(self) -> None:
        self.config: ConfigManager | None = None
        self.event_bus: EventBus | None = None
        self.context_manager: ContextManager | None = None
        self.plugin_manager: PluginManager | None = None
        self.model_router: ModelRouter | None = None
        self.kernel: Kernel | None = None
        self._initialized = False

    async def initialize(self, config_dir: str = "config", data_dir: str = "novel_output") -> None:
        """初始化所有核心服务."""
        if self._initialized:
            return

        # 1. 配置
        self.config = ConfigManager(config_dir)
        await self.config.load()

        # 2. 事件总线
        self.event_bus = EventBus()
        await self.event_bus.start()

        # 3. 上下文管理
        self.context_manager = ContextManager()
        await self.context_manager.start()

        # 4. 插件管理
        self.plugin_manager = PluginManager(event_bus=self.event_bus)

        # 5. LLM — 使用 ModelRegistry 替代 ModelRouter
        self.model_router = ModelRouter(self.config.get_all())

        # 6. 内核
        # 数据库 (SQLite 零配置)
        db_path = self.config.get("database.path", "data/novel.db")
        self.database = DatabaseManager(db_path)
        await self.database.connect()

        # 从配置读取数据目录
        data_dir = self.config.get("app.data_dir", data_dir)

        self.kernel = Kernel(
            event_bus=self.event_bus,
            plugin_manager=self.plugin_manager,
            config_manager=self.config,
            context_manager=self.context_manager,
            model_registry=None,
            data_dir=Path(data_dir),
            database=self.database,
        )

        # 7. 从配置初始化多 Provider
        self.plugin_manager.set_kernel(self.kernel)
        await self.kernel.setup_providers_from_config()

        # 7. 加载所有插件
        await self._load_all_plugins()

        self._initialized = True

    async def shutdown(self) -> None:
        """关闭所有服务."""
        if self.event_bus:
            await self.event_bus.stop()
        if self.context_manager:
            await self.context_manager.stop()
        if self.database:
            await self.database.close()

    async def _load_all_plugins(self) -> None:
        """自动发现并加载所有插件（包括 Skill Builder 生成的）。"""
        import importlib
        from pathlib import Path

        plugin_modules = [
            "plugins.idea_incubator.plugin",
            "plugins.world_builder.plugin",
            "plugins.outline_planner.plugin",
            "plugins.chapter_writer.plugin",
            "plugins.style_adapter.plugin",
            "plugins.consistency_checker.plugin",
            "plugins.foreshadow_manager.plugin",
            "plugins.anti_ai_detection.plugin",
            "plugins.quality_evaluator.plugin",
            "plugins.graph_manager.plugin",
            "plugins.writing_coach.plugin",
            "plugins.pipeline_editor.plugin",
            "plugins.cover_artist.plugin",
            "plugins.pack_market.plugin",
        ]

        # 自动发现 Skill Builder 生成的插件 (_built/)
        built_dir = Path("plugins/_built")
        if built_dir.exists():
            for plugin_dir in built_dir.iterdir():
                if plugin_dir.is_dir() and (plugin_dir / "plugin.py").exists():
                    mod_name = f"plugins._built.{plugin_dir.name}.plugin"
                    if mod_name not in plugin_modules:
                        plugin_modules.append(mod_name)

        for mod_name in plugin_modules:
            try:
                mod = importlib.import_module(mod_name)
                manifest = mod.create_manifest()
                instance = mod.create_plugin()
                await self.plugin_manager.register(manifest, instance)
            except Exception:
                pass

        await self.plugin_manager.load_all()

        # 触发 on_load (传入 kernel)
        for entry in await self.plugin_manager.list_all():
            if entry.state.value == "active":
                continue
            try:
                if hasattr(entry.instance, "on_load"):
                    await entry.instance.on_load(self.kernel)
            except Exception:
                pass

        # 自动重新索引已安装的知识包
        await self._reindex_packs()

    async def _reindex_packs(self) -> None:
        """启动时自动重新索引知识包。"""
        try:
            packs_dir = Path("knowledge_base/packs")
            if not packs_dir.exists():
                return
            from core.knowledge_pack import KnowledgePackMarket
            market = KnowledgePackMarket(self.kernel)
            local = await market.list_local()
            if local:
                from core.logging_config import get_logger
                logger = get_logger(__name__)
                logger.info("知识包已就绪", count=len(local))
        except Exception:
            pass


# 全局单例
_app_state: AppState | None = None


async def get_app_state() -> AppState:
    """获取或创建全局 AppState."""
    global _app_state
    if _app_state is None:
        _app_state = AppState()
        await _app_state.initialize()
    return _app_state


async def get_kernel(request: Request | None = None) -> Kernel:
    """FastAPI 依赖 — 获取 Kernel."""
    state = await get_app_state()
    return state.kernel
