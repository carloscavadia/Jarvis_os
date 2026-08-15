"""Ejecución segura de módulos n8n y Home Assistant."""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request
import uuid
from typing import Any, ClassVar
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
    HOME_ASSISTANT_READ_ACTIONS: ClassVar[frozenset[str]] = frozenset(
        {"homeassistant.entities"}
    )

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
            # El cuerpo del error suele decir exactamente qué falta; descartarlo
            # dejaba al agente adivinando a ciegas ante un 400.
            try:
                detail = exc.read(512).decode("utf-8", errors="replace")
            except OSError:
                # Sin cuerpo legible seguimos informando el código, que ya orienta.
                detail = ""
            detail = " ".join(detail.split())[:300]
            return ToolResult(
                f"El conector respondió HTTP {exc.code}."
                + (f" Detalle: {detail}" if detail else ""),
                is_error=True,
            )
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
        token = str(record.config["_secrets"].get("token", "")).strip()
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json,text/plain",
            "User-Agent": "JARVIS-OS/0.3 connector-module",
            "X-Jarvis-Connector-Token": token,
        }
        
        if token:
            # Soporte para especificación explícita de cabecera (ej: X-API-Key: mi_secreto)
            if ":" in token and not token.lower().startswith(("http://", "https://")):
                parts = token.split(":", 1)
                custom_header = parts[0].strip()
                custom_val = parts[1].strip()
                if custom_header and custom_val:
                    headers[custom_header] = custom_val
            
            if token.lower().startswith("bearer "):
                headers["Authorization"] = token
            elif token.count(".") == 2:
                # Token JWT válido de 3 partes (header.payload.signature)
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers["Authorization"] = token

        return self._request(
            url,
            method="POST",
            headers=headers,
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
        if action == "homeassistant.entities":
            domain = str(payload.get("domain", "")).strip().lower()
            query = str(payload.get("query", "")).strip().lower()
            try:
                limit = max(1, min(int(payload.get("limit", 300)), 500))
                offset = max(0, int(payload.get("offset", 0)))
            except (TypeError, ValueError):
                return ToolResult("limit u offset inválido.", is_error=True)
            if domain and not domain.replace("_", "").isalnum():
                return ToolResult("Dominio de Home Assistant inválido.", is_error=True)
            request = urllib.request.Request(
                urljoin(base, "api/states"), method="GET", headers=headers
            )
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    # Los inventarios domésticos pueden superar el límite normal de
                    # una respuesta individual, pero se compactan antes de llegar al LLM.
                    raw = response.read(max(self.max_response_bytes, 2 * 1024 * 1024) + 1)
            except urllib.error.HTTPError as exc:
                return ToolResult(
                    f"Home Assistant respondió HTTP {exc.code}.", is_error=True
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                return ToolResult(
                    f"No se pudo contactar Home Assistant: {type(exc).__name__}.",
                    is_error=True,
                )
            if len(raw) > max(self.max_response_bytes, 2 * 1024 * 1024):
                return ToolResult(
                    "El inventario de Home Assistant supera 2 MiB; usa un filtro domain.",
                    is_error=True,
                )
            try:
                states = json.loads(raw)
            except json.JSONDecodeError:
                return ToolResult("Home Assistant devolvió JSON inválido.", is_error=True)
            if not isinstance(states, list):
                return ToolResult("Home Assistant no devolvió una lista de entidades.", is_error=True)

            entities: list[dict[str, Any]] = []
            for item in states:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entity_id", ""))
                entity_domain = entity_id.partition(".")[0]
                attributes = item.get("attributes", {})
                if not isinstance(attributes, dict):
                    attributes = {}
                name = str(attributes.get("friendly_name", entity_id))
                if domain and entity_domain != domain:
                    continue
                if query and query not in f"{entity_id} {name}".lower():
                    continue
                entity = {
                    "entity_id": entity_id,
                    "name": name,
                    "domain": entity_domain,
                    "state": str(item.get("state", "")),
                }
                for source, target in (
                    ("device_class", "device_class"),
                    ("unit_of_measurement", "unit"),
                ):
                    if attributes.get(source) is not None:
                        entity[target] = str(attributes[source])
                entities.append(entity)

            total = len(entities)
            page = entities[offset : offset + limit]
            return ToolResult(
                json.dumps(
                    {
                        "total": total,
                        "returned": len(page),
                        "offset": offset,
                        "has_more": offset + len(page) < total,
                        "entities": page,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        if action == "homeassistant.service":
            domain = str(payload.get("domain", ""))
            service = str(payload.get("service", ""))
            if (
                not domain.replace("_", "").isalnum()
                or not service.replace("_", "").isalnum()
            ):
                return ToolResult("Dominio o servicio inválido.", is_error=True)
            # Home Assistant recibe los datos del servicio como cuerpo directo, así
            # que `{"domain","service","entity_id"}` es la forma natural y la que
            # cualquiera escribe primero. Anidar en `service_data` es una convención
            # nuestra: se aceptan las dos y, sin anidar, el resto de claves son los
            # datos. Antes se descartaban en silencio y Home Assistant devolvía un
            # 400 imposible de diagnosticar desde el mensaje.
            if "service_data" in payload:
                service_data = payload["service_data"]
                if not isinstance(service_data, dict):
                    return ToolResult("service_data debe ser un objeto.", is_error=True)
            else:
                service_data = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"domain", "service"}
                }
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
        read_actions = set(record.config.get("read_actions", []))
        write_actions = set(record.config.get("write_actions", []))
        allowed_actions = set(write_actions if write else read_actions)
        if record.connector_type == "home_assistant" and not write:
            allowed_actions.update(self.HOME_ASSISTANT_READ_ACTIONS)
        if action not in allowed_actions:
            # La separación lectura/escritura es lo que sostiene la aprobación
            # humana: `query_connector_module` no la pide y
            # `run_connector_module_action` sí. Unir las dos listas hacía que una
            # consulta pudiera enviar un correo o abrir una puerta sin
            # preguntar, así que en vez de ensancharla se dice exactamente qué
            # falta: el «Acción no permitida» a secas era imposible de arreglar
            # sin adivinar en qué lista está declarada la acción.
            declared_as_write = action in write_actions
            declared_as_read = action in read_actions
            if write and declared_as_read:
                detail = (
                    f"'{action}' está declarada como lectura en '{connector}'. "
                    "Consúltala con la herramienta de consulta, o declárala en "
                    "write_actions si de verdad modifica algo."
                )
            elif not write and declared_as_write:
                detail = (
                    f"'{action}' está declarada como escritura en '{connector}' y "
                    "requiere aprobación: ejecútala con la herramienta de acción, "
                    "no con la de consulta."
                )
            else:
                available = sorted(read_actions | write_actions)
                detail = (
                    f"'{action}' no está declarada en '{connector}'. "
                    + (
                        "Acciones disponibles: " + ", ".join(available[:20])
                        if available
                        else "El módulo no declara ninguna acción todavía."
                    )
                )
            return ToolResult(f"Acción no permitida. {detail}", is_error=True)
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

