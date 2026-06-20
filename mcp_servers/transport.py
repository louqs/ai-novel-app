"""MCP 传输层实现 — stdio 和 SSE 传输.

StdioMCPClient: 通过子进程 stdio 与 MCP Server 通信.
SSEMCPClient: 通过 HTTP/SSE 与 MCP Server 通信.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from mcp_servers.base import BaseMCPClient, MCPToolDefinition
from core.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Stdio Client — 通过子进程通信
# =============================================================================


class StdioMCPClient(BaseMCPClient):
    """通过 stdio 子进程连接 MCP Server.

    适用场景: 本地 Python MCP Server.

    用法:
        client = StdioMCPClient(command="python", args=["-m", "mcp_servers.my_server"])
        await client.connect()
        tools = await client.list_tools()
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self._command = command
        self._args = args or []
        self._env = env
        self._cwd = cwd
        self._process: asyncio.subprocess.Process | None = None
        self._reader_lock = asyncio.Lock()

    async def connect(self) -> None:
        """启动子进程并建立 stdio 通道."""
        process_env = os.environ.copy()
        if self._env:
            process_env.update(self._env)

        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
            cwd=self._cwd,
        )

    async def disconnect(self) -> None:
        """终止子进程."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()
            self._process = None

    async def list_tools(self) -> list[MCPToolDefinition]:
        """通过 stdio 列出工具."""
        response = await self._send_request({
            "method": "tools/list",
            "params": {},
        })
        tools_data = response.get("tools", [])
        return [MCPToolDefinition(**t) for t in tools_data]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """通过 stdio 调用工具."""
        response = await self._send_request({
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        })
        return response.get("content", response)

    # ---- 内部 ----

    async def _send_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """发送 JSON-RPC 风格请求并接收响应."""
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("MCP Client 未连接")

        request_str = json.dumps(request) + "\n"

        async with self._reader_lock:
            # 发送
            self._process.stdin.write(request_str.encode("utf-8"))
            await self._process.stdin.drain()

            # 接收
            line = await self._process.stdout.readline()
            if not line:
                raise ConnectionError("MCP Server 连接已关闭")

        return json.loads(line.decode("utf-8"))


# =============================================================================
# SSE Client — 通过 HTTP/SSE 通信
# =============================================================================


class SSEMCPClient(BaseMCPClient):
    """通过 HTTP/SSE 连接 MCP Server.

    适用场景: 远程 MCP Server.

    用法:
        client = SSEMCPClient(base_url="http://localhost:8001")
        await client.connect()
        tools = await client.list_tools()
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._headers = headers or {}
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """建立 HTTP 连接."""
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            headers=self._headers,
        )

    async def disconnect(self) -> None:
        """关闭 HTTP 连接."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def list_tools(self) -> list[MCPToolDefinition]:
        """通过 HTTP 列出工具."""
        response = await self._request("GET", "/tools")
        tools_data = response.get("tools", [])
        return [MCPToolDefinition(**t) for t in tools_data]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """通过 HTTP 调用工具."""
        response = await self._request(
            "POST",
            f"/tools/{tool_name}/call",
            json={"arguments": arguments},
        )
        return response.get("content", response)

    # ---- 内部 ----

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发送 HTTP 请求."""
        if self._client is None:
            raise RuntimeError("MCP Client 未连接")

        response = await self._client.request(method, path, json=json)
        response.raise_for_status()
        return response.json()
