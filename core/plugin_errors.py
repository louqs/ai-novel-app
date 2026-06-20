"""插件管理异常."""

from __future__ import annotations


class PluginError(Exception):
    """插件相关异常基类."""


class PluginNotFoundError(PluginError):
    """插件未找到."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"插件未找到: '{name}'")


class PluginAlreadyRegisteredError(PluginError):
    """插件已注册."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"插件已注册: '{name}'")


class CircularDependencyError(PluginError):
    """循环依赖."""

    def __init__(self, name: str, cycle: list[str]) -> None:
        self.name = name
        self.cycle = cycle
        cycle_str = " → ".join(cycle)
        super().__init__(f"检测到循环依赖: {cycle_str}")


class PluginLoadError(PluginError):
    """插件加载失败."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"插件 '{name}' 加载失败: {reason}")


class MissingDependencyError(PluginError):
    """缺少依赖插件."""

    def __init__(self, name: str, missing: str) -> None:
        self.name = name
        self.missing = missing
        super().__init__(f"插件 '{name}' 缺少依赖: '{missing}'")
