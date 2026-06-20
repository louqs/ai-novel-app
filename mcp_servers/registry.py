"""MCP Server 注册表 — 管理所有已连接的 MCP Server.

用法:
    registry = MCPServerRegistry()
    registry.register_client("knowledge", client)
    await registry.connect_all()
    tools = await registry.list_all_tools()
"""

from __future__ import annotations

from typing import Any

from mcp_servers.base import BaseMCPClient, MCPToolDefinition
from core.logging_config import get_logger

logger = get_logger(__name__)


class MCPServerRegistry:
    """MCP Server 注册表.

    管理多个 MCP Server 的连接和工具发现。
    """

    def __init__(self) -> None:
        self._clients: dict[str, BaseMCPClient] = {}
        self._tool_index: dict[str, tuple[str, BaseMCPClient]] = {}
        # _tool_index[tool_name] = (server_name, client)

    # ---- 注册 ----

    def register_client(self, server_name: str, client: BaseMCPClient) -> None:
        """注册一个 MCP Client."""
        self._clients[server_name] = client
        logger.info("MCP Client 已注册", server=server_name)

    def unregister_client(self, server_name: str) -> None:
        """移除一个 MCP Client."""
        self._clients.pop(server_name, None)
        # 清除相关工具索引
        self._tool_index = {
            name: (srv, cli)
            for name, (srv, cli) in self._tool_index.items()
            if srv != server_name
        }

    # ---- 连接 ----

    async def connect_all(self) -> None:
        """连接所有已注册的 MCP Server."""
        for name, client in self._clients.items():
            try:
                await client.connect()
                logger.info("MCP Server 已连接", server=name)

                # 索引工具
                tools = await client.list_tools()
                for tool in tools:
                    self._tool_index[tool.name] = (name, client)
            except Exception:
                logger.exception("MCP Server 连接失败", server=name)

    async def disconnect_all(self) -> None:
        """断开所有连接."""
        for name, client in self._clients.items():
            try:
                await client.disconnect()
                logger.info("MCP Server 已断开", server=name)
            except Exception:
                logger.exception("MCP Server 断开失败", server=name)
        self._tool_index.clear()

    # ---- 工具查询 ----

    async def list_all_tools(self) -> list[dict[str, Any]]:
        """列出所有 Server 的全部工具."""
        result = []
        for tool_name, (server_name, _) in self._tool_index.items():
            result.append({
                "name": tool_name,
                "server": server_name,
            })
        return result

    def get_client_for_tool(self, tool_name: str) -> BaseMCPClient | None:
        """查找拥有指定工具的 Client."""
        entry = self._tool_index.get(tool_name)
        return entry[1] if entry else None

    # ---- 调用 ----

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """调用指定工具.

        Raises:
            ValueError: 工具未找到.
        """
        entry = self._tool_index.get(tool_name)
        if entry is None:
            available = list(self._tool_index.keys())
            raise ValueError(f"工具 '{tool_name}' 未找到。可用工具: {available}")

        _server_name, client = entry
        return await client.call_tool(tool_name, arguments)
