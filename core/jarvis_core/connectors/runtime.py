"""Ejecución segura de módulos n8n y Home Assistant."""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request
import uuid
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import certifi

from jarvis_core.connectors.store import ConnectorRecord, ConnectorStore
from jarvis_core.tools.base import ToolResult


def validate_connector_url(raw_url: str) -> str:
    raw_url = raw_url.strip()
    if len(raw_url) > 2000 or any(ord(char) < 32 for char in raw_url):
        raise ValueError("La URL del conector no es válida.")
    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("El conector requiere una URL HTTP o HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("La URL no admite credenciales, query ni fragmento.")
    return urlunsplit(parsed)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ConnectorRuntime:
    def __init__(
        self,
        store: ConnectorStore,
        *,
        timeout: float = 20.0,
        max_payload_bytes: int = 64 * 1024,
        max_response_bytes: int = 256 * 1024,
    ) -> None:
        self.store = store
        self.timeout = timeout
        self.max_payload_bytes = max_payload_bytes
        self.max_response_bytes = max_response_bytes
        tls = ssl.create_default_context(cafile=certifi.where())
        self._opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=tls)
        )

    def _request(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> ToolResult:
        data = None
        if payload is not None:
            try:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError):
                return ToolResult("El payload no es JSON válido.", is_error=True)
            if len(data) > self.max_payload_bytes:
                return ToolResult(
                    "El payload supera el límite permitido.", is_error=True
                )
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
                content_type = response.headers.get_content_type().lower()
        except urllib.error.HTTPError as exc:
            return ToolResult(f"El conector respondió HTTP {exc.code}.", is_error=True)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ToolResult(
                f"No se pudo contactar el conector: {type(exc).__name__}.",
                is_error=True,
            )
        if len(raw) > self.max_response_bytes:
            return ToolResult(
                "La respuesta del conector supera el límite.", is_error=True
            )
        if content_type not in {"application/json", "text/plain"}:
            return ToolResult(
                f"Tipo de respuesta no permitido: {content_type}.", is_error=True
            )
        text = raw.decode("utf-8", errors="replace").strip()
        if content_type == "application/json" and text:
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                return ToolResult("El conector devolvió JSON inválido.", is_error=True)
        return ToolResult(text or "Conector disponible.")

    def _n8n(
        self, record: ConnectorRecord, action: str, payload: dict[str, Any]
    ) -> ToolResult:
        url = validate_connector_url(str(record.config.get("url", "")))
        token = str(record.config["_secrets"].get("token", ""))
        return self._request(
            url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json,text/plain",
                "User-Agent": "JARVIS-OS/0.3 connector-module",
                "X-Jarvis-Connector-Token": token,
            },
            payload={
                "action": action,
                "payload": payload,
                "request_id": uuid.uuid4().hex,
                "source": "jarvis_os",
            },
        )

    def _home_assistant(
        self, record: ConnectorRecord, action: str, payload: dict[str, Any]
    ) -> ToolResult:
        base = (
            validate_connector_url(str(record.config.get("url", ""))).rstrip("/") + "/"
        )
        token = str(record.config["_secrets"].get("token", ""))
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json,text/plain",
            "Content-Type": "application/json",
            "User-Agent": "JARVIS-OS/0.3 home-assistant",
        }
        if action == "homeassistant.state":
            entity_id = str(payload.get("entity_id", ""))
            if not entity_id or len(entity_id) > 128:
                return ToolResult("Falta entity_id válido.", is_error=True)
            url = urljoin(base, f"api/states/{quote(entity_id, safe='._-')}")
            return self._request(url, method="GET", headers=headers)
        if action == "homeassistant.service":
            domain = str(payload.get("domain", ""))
            service = str(payload.get("service", ""))
            if (
                not domain.replace("_", "").isalnum()
                or not service.replace("_", "").isalnum()
            ):
                return ToolResult("Dominio o servicio inválido.", is_error=True)
            service_data = payload.get("service_data", {})
            if not isinstance(service_data, dict):
                return ToolResult("service_data debe ser un objeto.", is_error=True)
            url = urljoin(base, f"api/services/{quote(domain)}/{quote(service)}")
            return self._request(
                url, method="POST", headers=headers, payload=service_data
            )
        return ToolResult("Acción de Home Assistant no implementada.", is_error=True)

    def _invoke(
        self, connector: str, action: str, payload: dict[str, Any], write: bool
    ) -> ToolResult:
        record = self.store.get(connector)
        if record is None or not record.enabled:
            return ToolResult("El módulo no existe o está desactivado.", is_error=True)
        allowed_key = "write_actions" if write else "read_actions"
        if action not in record.config.get(allowed_key, []):
            return ToolResult("Acción no permitida para este módulo.", is_error=True)
        if record.connector_type == "n8n":
            return self._n8n(record, action, payload)
        if record.connector_type == "home_assistant":
            return self._home_assistant(record, action, payload)
        return ToolResult("Tipo de módulo no soportado.", is_error=True)

    async def invoke(
        self, connector: str, action: str, payload: dict[str, Any], *, write: bool
    ) -> ToolResult:
        return await asyncio.to_thread(self._invoke, connector, action, payload, write)

    async def test(self, connector: str) -> ToolResult:
        record = self.store.get(connector)
        if record is None:
            return ToolResult("Módulo no encontrado.", is_error=True)
        if record.connector_type == "home_assistant":
            base = (
                validate_connector_url(str(record.config.get("url", ""))).rstrip("/")
                + "/"
            )
            token = str(record.config["_secrets"].get("token", ""))
            return await asyncio.to_thread(
                self._request,
                urljoin(base, "api/"),
                method="GET",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json,text/plain",
                },
            )
        return await asyncio.to_thread(self._n8n, record, "connector.test", {})
