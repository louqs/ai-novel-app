"""插件模板 — 新插件请遵循此结构.

快速上手:
    1. 复制 _template/ 目录，重命名为你的插件名
    2. 修改 PluginManifest 中的 name, version, description
    3. 填写 dependencies（如有依赖其他插件）
    4. 实现你需要钩住的生命周期方法

示例——一个最小插件:

    from core.plugin_manager import PluginManifest
    from core.kernel_api import IKernelAPI

    class MyPlugin:
        name = "my-plugin"
        version = "0.1.0"

        async def on_load(self, kernel: IKernelAPI) -> None:
            self._kernel = kernel
            logger = kernel.get_logger(self.name)
            logger.info("插件已加载")

        async def on_unload(self) -> None:
            pass

    def create_manifest() -> PluginManifest:
        return PluginManifest(
            name="my-plugin",
            version="0.1.0",
            description="我的自定义插件",
            hooks=["on_load", "on_unload"],
        )

    def create_plugin() -> MyPlugin:
        return MyPlugin()
"""

from core.plugin_manager import PluginManifest


class TemplatePlugin:
    """插件模板 — 复制并重命名此类."""

    # ------------------------------------------------------------------
    # 元数据 (必需)
    # ------------------------------------------------------------------
    name: str = "template"
    version: str = "0.1.0"

    # ------------------------------------------------------------------
    # 生命周期钩子 (可选)
    # ------------------------------------------------------------------

    async def on_load(self, kernel) -> None:
        """插件加载时调用。kernel 是 IKernelAPI 实例。

        在此方法中:
        - 订阅事件: kernel.event_bus.subscribe(...)
        - 注册 MCP 工具 (如有)
        - 初始化内部状态
        """
        pass

    async def on_unload(self) -> None:
        """插件卸载时调用。清理资源。"""
        pass

    async def on_pause(self) -> None:
        """暂停插件。"""
        pass

    async def on_resume(self) -> None:
        """恢复插件。"""
        pass

    # ------------------------------------------------------------------
    # 章节流水线钩子 (可选)
    # ------------------------------------------------------------------

    async def on_chapter_before(self, ctx: dict) -> dict:
        """章节生成前调用。可修改上下文。"""
        return ctx

    async def on_chapter_after(self, chapter: dict) -> dict:
        """章节生成后调用。可修改章节内容。"""
        return chapter

    # ------------------------------------------------------------------
    # 质量门禁钩子 (可选)
    # ------------------------------------------------------------------

    async def on_gate_check(self, chapter: dict) -> "GateResult":  # noqa: F821
        """门禁检查。返回 GateResult。"""
        from core.quality_gate import GateResult, GateVerdict

        return GateResult(
            gate_name=self.name,
            verdict=GateVerdict.PASS,
            issues=[],
            score=1.0,
        )

    # ------------------------------------------------------------------
    # 记忆更新钩子 (可选)
    # ------------------------------------------------------------------

    async def on_memory_update(self, chapter: dict) -> None:
        """章节完成后更新记忆。"""
        pass


def create_manifest() -> PluginManifest:
    """返回此插件的清单。插件管理器在注册时调用。"""
    return PluginManifest(
        name="template",
        version="0.1.0",
        description="插件模板 — 复制并自定义",
        dependencies=[],
        optional_dependencies=[],
        hooks=[
            "on_load",
            "on_unload",
            "on_chapter_before",
            "on_chapter_after",
            "on_gate_check",
            "on_memory_update",
        ],
    )


def create_plugin():
    """工厂函数 — 返回插件实例。"""
    return TemplatePlugin()
