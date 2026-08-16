"""MCP Manager: gestiona la conexión con servidores MCP y convierte sus herramientas a JARVIS OS."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from jarvis_core.mcp.store import MCPServerRecord, MCPStore
from jarvis_core.tools.base import Tool, ToolResult

logger = logging.getLogger("jarvis.mcp")

#: Variables que un proceso hijo necesita para arrancar. El resto del entorno del
#: gateway —clave del gateway, master key de conectores, token de Telegram,
#: credenciales de Navidrome— no se le entrega: un servidor MCP es código de
#: terceros y no tiene por qué ver los secretos de la casa.
_ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSL_CERT_FILE")


def _child_env(extra: dict[str, str] | None) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    if extra:
        env.update(extra)
    return env



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

    async def run(self, **kwargs: Any) -> ToolResult:
        return await self._manager.call_mcp_tool(
            self._server_name, self._mcp_tool_name, kwargs
        )


class MCPManager:
    """Orquestador de servidores y herramientas MCP (Model Context Protocol)."""

    def __init__(self, store: MCPStore, *, timeout: float = 20.0) -> None:
        self.store = store
        #: Sin tope, un servidor que no responde cuelga a quien le hable. Y como
        #: `sync_servers` corre al arrancar el gateway, un `npx` que se queda
        #: descargando dejaba al servicio entero sin llegar nunca a estar listo.
        self.timeout = timeout
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
            except TimeoutError:
                logger.error(
                    "El servidor MCP '%s' no respondió en %.0f s; se deja fuera.",
                    record.name,
                    self.timeout,
                )
                results[record.name] = {
                    "status": "error",
                    "error": f"sin respuesta en {self.timeout:.0f} s",
                    "tools_count": 0,
                }
            except Exception as exc:
                logger.error("Error conectando a servidor MCP %s: %s", record.name, exc)
                results[record.name] = {"status": "error", "error": str(exc), "tools_count": 0}

        return results

    async def connect_server(self, record: MCPServerRecord) -> list[DynamicMCPTool]:
        """Prueba e inspecciona las herramientas de un servidor MCP específico.

        Con tope de tiempo: un `npx` que se queda descargando, o un servidor SSE
        que acepta la conexión y no contesta, colgaba indefinidamente a quien
        llamara —incluido el arranque del gateway, que sincroniza los servidores
        guardados antes de aceptar peticiones—.
        """
        return await asyncio.wait_for(self._connect(record), timeout=self.timeout)

    async def _connect(self, record: MCPServerRecord) -> list[DynamicMCPTool]:
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

            env = _child_env(record.env)

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
            return await asyncio.wait_for(
                self._call(record, tool_name, arguments), timeout=self.timeout
            )
        except TimeoutError:
            # Mismo motivo que en `connect_server`, un piso más abajo: sin tope,
            # una herramienta que no vuelve deja el turno del agente colgado.
            logger.error(
                "La herramienta MCP '%s/%s' no respondió en %.0f s.",
                server_name, tool_name, self.timeout,
            )
            return ToolResult(
                f"El servidor MCP '{server_name}' no respondió a tiempo.", is_error=True
            )

    async def _call(
        self, record: MCPServerRecord, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        server_name = record.name
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
            from mcp.client.sse import sse_client

            if record.transport == "stdio":
                env = _child_env(record.env)
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
