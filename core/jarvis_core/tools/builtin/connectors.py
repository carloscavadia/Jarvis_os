"""Puente seguro entre las herramientas de JARVIS y automatizaciones de n8n."""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request
import uuid
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import certifi

from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult

if TYPE_CHECKING:
    from jarvis_core.connectors.runtime import ConnectorRuntime
    from jarvis_core.connectors.store import ConnectorStore


def validate_connector_url(raw_url: str) -> str:
    """Valida una URL configurada por el administrador, no elegida por el modelo."""
    raw_url = raw_url.strip()
    if len(raw_url) > 2000 or any(ord(char) < 32 for char in raw_url):
        raise ValueError("La URL del conector no es válida.")
    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("El conector requiere una URL HTTP o HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "La URL del conector no admite credenciales, query ni fragmento."
        )
    return urlunsplit(parsed)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class N8nConnectorClient:
    """Invoca un único webhook administrado y limita estrictamente las respuestas."""

    def __init__(
        self,
        webhook_url: str,
        token: str,
        *,
        timeout: float = 20.0,
        max_payload_bytes: int = 64 * 1024,
        max_response_bytes: int = 256 * 1024,
    ) -> None:
        self.webhook_url = validate_connector_url(webhook_url)
        self.token = token
        self.timeout = timeout
        self.max_payload_bytes = max_payload_bytes
        self.max_response_bytes = max_response_bytes
        tls_context = ssl.create_default_context(cafile=certifi.where())
        self._opener = urllib.request.build_opener(
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=tls_context),
        )

    def _invoke(self, action: str, payload: dict[str, Any]) -> ToolResult:
        envelope = {
            "action": action,
            "payload": payload,
            "request_id": uuid.uuid4().hex,
            "source": "jarvis_os",
        }
        try:
            body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            return ToolResult(
                "El payload del conector no es JSON válido.", is_error=True
            )
        if len(body) > self.max_payload_bytes:
            return ToolResult(
                "El payload del conector supera el límite permitido.", is_error=True
            )

        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json,text/plain",
                "User-Agent": "JARVIS-OS/0.2 connector",
                "X-Jarvis-Connector-Token": self.token,
            },
        )
        try:
            response = self._opener.open(request, timeout=self.timeout)
            with response:
                raw = response.read(self.max_response_bytes + 1)
                content_type = response.headers.get_content_type().lower()
        except urllib.error.HTTPError as exc:
            return ToolResult(
                f"n8n rechazó la acción con HTTP {exc.code}.", is_error=True
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ToolResult(
                f"No se pudo contactar el conector n8n: {type(exc).__name__}.",
                is_error=True,
            )
        if len(raw) > self.max_response_bytes:
            return ToolResult(
                "La respuesta del conector supera el límite.", is_error=True
            )
        if content_type not in {"application/json", "text/plain"}:
            return ToolResult(
                f"n8n devolvió un tipo no permitido: {content_type}.", is_error=True
            )
        text = raw.decode("utf-8", errors="replace").strip()
        if content_type == "application/json" and text:
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                return ToolResult("n8n devolvió JSON inválido.", is_error=True)
        return ToolResult(text or "Acción completada por n8n.")

    async def invoke(self, action: str, payload: dict[str, Any]) -> ToolResult:
        return await asyncio.to_thread(self._invoke, action, payload)


class ListConnectorsTool(Tool):
    name = "list_connectors"
    description = "Lista los servicios y acciones actualmente conectados a JARVIS."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, read_actions: list[str], write_actions: list[str]) -> None:
        self.read_actions = read_actions
        self.write_actions = write_actions

    async def run(self, **kwargs: Any) -> ToolResult:
        del kwargs
        services = sorted(
            {
                action.partition(".")[0]
                for action in self.read_actions + self.write_actions
            }
        )
        return ToolResult(
            json.dumps(
                {
                    "backend": "n8n_connector_bus",
                    "services": services,
                    "read_actions": self.read_actions,
                    "write_actions_requiring_approval": self.write_actions,
                    "note": "Solo estas acciones están disponibles; no inventes conectores.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )


class _ConnectorActionTool(Tool):
    actions: list[str]

    def __init__(self, client: N8nConnectorClient, actions: list[str]) -> None:
        self.client = client
        self.actions = actions
        self.input_schema = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": actions,
                    "description": "Acción exacta declarada por el administrador.",
                },
                "payload": {
                    "type": "object",
                    "description": "Datos mínimos requeridos por la automatización.",
                },
            },
            "required": ["action", "payload"],
            "additionalProperties": False,
        }

    async def run(
        self, action: str = "", payload: dict[str, Any] | None = None, **kwargs: Any
    ) -> ToolResult:
        del kwargs
        if action not in self.actions:
            return ToolResult("Acción de conector no permitida.", is_error=True)
        if not isinstance(payload, dict):
            return ToolResult("El payload debe ser un objeto JSON.", is_error=True)
        return await self.client.invoke(action, payload)


class QueryConnectorTool(_ConnectorActionTool):
    name = "query_connector"
    description = "Consulta datos sin modificarlos mediante un conector n8n. Acciones disponibles: "

    def __init__(self, client: N8nConnectorClient, actions: list[str]) -> None:
        super().__init__(client, actions)
        self.description += ", ".join(actions)


class RunConnectorActionTool(_ConnectorActionTool):
    name = "run_connector_action"
    description = (
        "Ejecuta mediante n8n una acción que cambia estado, como enviar mensajes o correos. "
        "Requiere aprobación humana. Acciones disponibles: "
    )
    requires_confirmation = True

    def __init__(self, client: N8nConnectorClient, actions: list[str]) -> None:
        super().__init__(client, actions)
        self.description += ", ".join(actions)


def register_connector_tools(
    registry: ToolRegistry,
    *,
    webhook_url: str,
    token: str,
    read_actions: list[str],
    write_actions: list[str],
    timeout: float,
    max_payload_bytes: int,
    max_response_bytes: int,
) -> None:
    client = N8nConnectorClient(
        webhook_url,
        token,
        timeout=timeout,
        max_payload_bytes=max_payload_bytes,
        max_response_bytes=max_response_bytes,
    )
    registry.register(ListConnectorsTool(read_actions, write_actions))
    if read_actions:
        registry.register(QueryConnectorTool(client, read_actions))
    if write_actions:
        registry.register(RunConnectorActionTool(client, write_actions))


class ListConnectorModulesTool(Tool):
    name = "list_connector_modules"
    description = "Lista los módulos de conectores registrados, sus servicios y acciones disponibles."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, store: ConnectorStore) -> None:
        self.store = store

    async def run(self, **kwargs: Any) -> ToolResult:
        del kwargs
        return ToolResult(
            json.dumps(self.store.list_public(), ensure_ascii=False, indent=2)
        )


class _ModuleActionTool(Tool):
    write = False

    def __init__(self, runtime: ConnectorRuntime) -> None:
        self.runtime = runtime
        self.input_schema = {
            "type": "object",
            "properties": {
                "connector": {
                    "type": "string",
                    "description": "Nombre exacto del módulo registrado.",
                },
                "action": {
                    "type": "string",
                    "description": "Acción exacta expuesta por el módulo.",
                },
                "payload": {
                    "type": "object",
                    "description": "Datos mínimos para la acción.",
                },
            },
            "required": ["connector", "action", "payload"],
            "additionalProperties": False,
        }

    async def run(
        self,
        connector: str = "",
        action: str = "",
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        if not isinstance(payload, dict):
            return ToolResult("El payload debe ser un objeto JSON.", is_error=True)
        return await self.runtime.invoke(connector, action, payload, write=self.write)


class QueryConnectorModuleTool(_ModuleActionTool):
    name = "query_connector_module"
    description = (
        "Consulta un módulo de conector registrado sin modificar el servicio externo."
    )


class RunConnectorModuleActionTool(_ModuleActionTool):
    name = "run_connector_module_action"
    description = "Ejecuta una acción externa de un módulo registrado. Requiere aprobación humana."
    requires_confirmation = True
    write = True


def register_dynamic_connector_tools(
    registry: ToolRegistry, store: ConnectorStore, runtime: ConnectorRuntime
) -> None:
    registry.register(ListConnectorModulesTool(store))
    registry.register(QueryConnectorModuleTool(runtime))
    registry.register(RunConnectorModuleActionTool(runtime))
