"""MCP Manager: gestiona la conexión con servidores MCP y convierte sus herramientas a JARVIS OS."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from jarvis_core.mcp.store import MCPServerRecord, MCPStore
from jarvis_core.tools.base import Tool, ToolResult

logger = logging.getLogger("jarvis.mcp")


class DynamicMCPTool(Tool):
    """Herramienta de JARVIS OS adaptada dinámicamente desde un servidor MCP."""

    def __init__(
        self,
        server_name: str,
        mcp_tool_name: str,
        description: str,
        input_schema: dict[str, Any],
        manager: MCPManager,
    ) -> None:
        self._server_name = server_name
        self._mcp_tool_name = mcp_tool_name
        # Prefijo único para evitar colisiones entre servidores
        self.name = f"mcp_{server_name}_{mcp_tool_name}".replace("-", "_").replace(".", "_")
        self.description = f"[MCP: {server_name}] {description or mcp_tool_name}"
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        self._manager = manager

    async def execute(self, **kwargs: Any) -> ToolResult:
        return await self._manager.call_mcp_tool(
            self._server_name, self._mcp_tool_name, kwargs
        )


class MCPManager:
    """Orquestador de servidores y herramientas MCP (Model Context Protocol)."""

    def __init__(self, store: MCPStore) -> None:
        self.store = store
        self._active_sessions: dict[str, Any] = {}
        self._active_tools: dict[str, DynamicMCPTool] = {}

    def get_registered_tools(self) -> list[Tool]:
        """Devuelve todas las herramientas MCP activas listas para registrar en el Registry de JARVIS."""
        return list(self._active_tools.values())

    async def sync_servers(self) -> dict[str, Any]:
        """Sincroniza y conecta todos los servidores MCP habilitados en el store."""
        records = self.store.list_all()
        results = {}

        for record in records:
            if not record.enabled:
                results[record.name] = {"status": "disabled", "tools_count": 0}
                continue
            try:
                tools = await self.connect_server(record)
                results[record.name] = {
                    "status": "connected",
                    "tools_count": len(tools),
                    "tools": [t.name for t in tools],
                }
            except Exception as exc:
                logger.error("Error conectando a servidor MCP %s: %s", record.name, exc)
                results[record.name] = {"status": "error", "error": str(exc), "tools_count": 0}

        return results

    async def connect_server(self, record: MCPServerRecord) -> list[DynamicMCPTool]:
        """Prueba e inspecciona las herramientas de un servidor MCP específico."""
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
            from mcp.client.sse import sse_client
        except ImportError:
            logger.warning("Librería 'mcp' no instalada. Instala 'mcp' para activar MCP.")
            return []

        tools_created = []

        if record.transport == "stdio":
            if not record.command:
                raise ValueError("El campo 'command' es obligatorio para transport='stdio'")

            env = dict(os.environ)
            if record.env:
                env.update(record.env)

            params = StdioServerParameters(
                command=record.command,
                args=record.args or [],
                env=env,
            )

            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    mcp_tools_res = await session.list_tools()
                    for mcp_tool in mcp_tools_res.tools:
                        dyn_tool = DynamicMCPTool(
                            server_name=record.name,
                            mcp_tool_name=mcp_tool.name,
                            description=mcp_tool.description or "",
                            input_schema=mcp_tool.inputSchema or {"type": "object"},
                            manager=self,
                        )
                        self._active_tools[dyn_tool.name] = dyn_tool
                        tools_created.append(dyn_tool)

        elif record.transport == "sse":
            if not record.url:
                raise ValueError("El campo 'url' es obligatorio para transport='sse'")

            async with sse_client(record.url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    mcp_tools_res = await session.list_tools()
                    for mcp_tool in mcp_tools_res.tools:
                        dyn_tool = DynamicMCPTool(
                            server_name=record.name,
                            mcp_tool_name=mcp_tool.name,
                            description=mcp_tool.description or "",
                            input_schema=mcp_tool.inputSchema or {"type": "object"},
                            manager=self,
                        )
                        self._active_tools[dyn_tool.name] = dyn_tool
                        tools_created.append(dyn_tool)

        logger.info("Servidor MCP '%s' conectado con %d herramientas.", record.name, len(tools_created))
        return tools_created

    async def call_mcp_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """Ejecuta una herramienta en el servidor MCP indicado."""
        record = self.store.get(server_name)
        if not record or not record.enabled:
            return ToolResult(
                f"El servidor MCP '{server_name}' no existe o está desactivado.", is_error=True
            )

        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
            from mcp.client.sse import sse_client

            if record.transport == "stdio":
                env = dict(os.environ)
                if record.env:
                    env.update(record.env)
                params = StdioServerParameters(
                    command=record.command,
                    args=record.args or [],
                    env=env,
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        res = await session.call_tool(tool_name, arguments=arguments)
                        contents = []
                        for content in res.content:
                            if hasattr(content, "text"):
                                contents.append(content.text)
                            else:
                                contents.append(str(content))
                        output = "\n".join(contents) if contents else "Ejecutado con éxito."
                        return ToolResult(output, is_error=getattr(res, "isError", False))

            elif record.transport == "sse":
                async with sse_client(record.url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        res = await session.call_tool(tool_name, arguments=arguments)
                        contents = []
                        for content in res.content:
                            if hasattr(content, "text"):
                                contents.append(content.text)
                            else:
                                contents.append(str(content))
                        output = "\n".join(contents) if contents else "Ejecutado con éxito."
                        return ToolResult(output, is_error=getattr(res, "isError", False))

            return ToolResult("Transporte MCP no soportado.", is_error=True)

        except Exception as exc:
            logger.error("Error al ejecutar herramienta MCP '%s/%s': %s", server_name, tool_name, exc)
            return ToolResult(f"Error MCP: {exc}", is_error=True)
