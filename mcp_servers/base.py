"""MCP 基座 — Server / Client 抽象 + Tool 定义.

用法 — 创建一个 MCP Server:

    from mcp_servers.base import BaseMCPServer, MCPToolDefinition, mcp_tool

    class MyServer(BaseMCPServer):
        server_name = "my-server"
        server_version = "0.1.0"

        @mcp_tool(description="搜索写作技巧")
        async def search_tips(self, query: str, limit: int = 5) -> dict:
            return {"results": [...]}

        def get_tools(self) -> list[MCPToolDefinition]:
            return [self._make_tool("search_tips")]

用法 — 连接一个 MCP Server:

    from mcp_servers.transport import StdioMCPClient
    client = StdioMCPClient(command="python", args=["-m", "my_server"])
    await client.connect()
    tools = await client.list_tools()
    result = await client.call_tool("search_tips", {"query": "仙侠"})
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Any, Callable

from pydantic import BaseModel, Field


# =============================================================================
# 类型定义
# =============================================================================


class MCPToolDefinition(BaseModel):
    """MCP 工具定义 — 遵循 MCP 规范."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    # input_schema 示例:
    # {
    #     "type": "object",
    #     "properties": {"query": {"type": "string", "description": "搜索关键词"}},
    #     "required": ["query"]
    # }


class MCPResourceDefinition(BaseModel):
    """MCP 资源定义."""

    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"


# =============================================================================
# mcp_tool 装饰器
# =============================================================================


def mcp_tool(
    name: str | None = None,
    description: str = "",
) -> Callable:
    """装饰器 — 标记一个方法为 MCP 工具.

    自动从函数签名和 docstring 提取 input_schema。

    Args:
        name: 工具名 (默认用方法名).
        description: 工具描述 (默认用 docstring 首行).
    """

    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__

        # 从 docstring 提取描述
        tool_desc = description
        if not tool_desc and func.__doc__:
            tool_desc = func.__doc__.strip().split("\n")[0]

        # 从函数签名提取参数 schema
        sig = inspect.signature(func)
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            param_type = "string"
            param_desc = ""

            # 从类型注解推断
            if param.annotation is not inspect.Parameter.empty:
                ann = param.annotation
                if ann is int or ann == "int":
                    param_type = "integer"
                elif ann is float or ann == "float":
                    param_type = "number"
                elif ann is bool or ann == "bool":
                    param_type = "boolean"
                elif ann is list or str(ann).startswith("list"):
                    param_type = "array"
                elif ann is dict or str(ann).startswith("dict"):
                    param_type = "object"

            properties[param_name] = {
                "type": param_type,
                "description": param_desc,
            }

            # 无默认值的参数为必需
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            input_schema["required"] = required

        # 挂载元数据
        func._mcp_tool_meta = {  # type: ignore[attr-defined]
            "name": tool_name,
            "description": tool_desc,
            "input_schema": input_schema,
        }

        return func

    return decorator


# =============================================================================
# Server 基类
# =============================================================================


class BaseMCPServer(ABC):
    """MCP Server 基类.

    子类需要:
        - 设置 server_name 和 server_version
        - 实现 get_tools() 返回工具定义列表
        - 实现 call_tool() 执行工具调用
    """

    server_name: str = ""
    server_version: str = "0.1.0"

    def get_tools(self) -> list[MCPToolDefinition]:
        """返回此 Server 提供的工具列表."""
        tools: list[MCPToolDefinition] = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if callable(attr) and hasattr(attr, "_mcp_tool_meta"):
                meta = getattr(attr, "_mcp_tool_meta")
                tools.append(
                    MCPToolDefinition(
                        name=meta["name"],
                        description=meta["description"],
                        input_schema=meta["input_schema"],
                    )
                )
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """调用一个工具并返回结果."""
        # 查找方法
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if callable(attr) and hasattr(attr, "_mcp_tool_meta"):
                meta = getattr(attr, "_mcp_tool_meta")
                if meta["name"] == tool_name:
                    return await attr(**arguments)

        raise ValueError(f"未知工具: {tool_name}")

    def get_resources(self) -> list[MCPResourceDefinition]:
        """返回资源列表 (子类可选覆盖)."""
        return []

    async def read_resource(self, uri: str) -> Any:
        """读取资源 (子类可选覆盖)."""
        raise NotImplementedError(f"资源不存在: {uri}")

    def _make_tool(self, method_name: str) -> MCPToolDefinition:
        """从已装饰的方法生成 MCPToolDefinition."""
        method = getattr(self, method_name, None)
        if method is None or not hasattr(method, "_mcp_tool_meta"):
            raise ValueError(f"方法 '{method_name}' 未用 @mcp_tool 装饰")
        meta = getattr(method, "_mcp_tool_meta")
        return MCPToolDefinition(
            name=meta["name"],
            description=meta["description"],
            input_schema=meta["input_schema"],
        )


# =============================================================================
# Client 基类
# =============================================================================


class BaseMCPClient(ABC):
    """MCP Client 基类.

    子类实现具体传输方式 (stdio, SSE, HTTP).
    """

    @abstractmethod
    async def connect(self) -> None:
        """建立连接."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接."""
        ...

    @abstractmethod
    async def list_tools(self) -> list[MCPToolDefinition]:
        """列出 Server 提供的工具."""
        ...

    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """调用工具并返回结果."""
        ...

    async def list_resources(self) -> list[MCPResourceDefinition]:
        """列出资源 (可选)."""
        return []

    async def read_resource(self, uri: str) -> Any:
        """读取资源 (可选)."""
        raise NotImplementedError
