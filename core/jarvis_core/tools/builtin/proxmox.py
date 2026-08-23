"""Consultar el servidor Proxmox desde el agente.

El HUD ya pintaba la telemetría, pero eso era un endpoint del gateway: el agente
no tenía ninguna herramienta y no podía responder «¿cómo está el servidor?» ni
«¿qué VMs tengo encendidas?». Tenía las credenciales delante y las manos atadas.

Solo lectura. Arrancar y parar máquinas es otra cosa: destructivo, y merece su
propia herramienta con aprobación explícita.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.error
import urllib.request
from typing import Any, ClassVar

from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult

logger = logging.getLogger("jarvis.proxmox")


class ProxmoxClient:
    def __init__(self, url: str, token_id: str, token_secret: str, verify_ssl: bool) -> None:
        self.url = url.rstrip("/")
        self._token_id = token_id
        self._token_secret = token_secret
        self._verify_ssl = verify_ssl

    @property
    def configured(self) -> bool:
        return bool(self.url and self._token_id and self._token_secret)

    def _get(self, path: str) -> Any:
        ctx = ssl.create_default_context()
        if not self._verify_ssl:
            # Proxmox recién instalado trae un certificado autofirmado. Es una
            # decisión explícita del usuario (JARVIS_PROXMOX_VERIFY_SSL), no un
            # atajo silencioso.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        peticion = urllib.request.Request(
            f"{self.url}/api2/json{path}",
            headers={"Authorization": f"PVEAPIToken={self._token_id}={self._token_secret}"},
        )
        with urllib.request.urlopen(peticion, timeout=10, context=ctx) as respuesta:
            return json.loads(respuesta.read().decode("utf-8")).get("data")

    async def get(self, path: str) -> Any:
        return await asyncio.to_thread(self._get, path)


def _no_configurado() -> ToolResult:
    return ToolResult(
        "Proxmox no está configurado. Necesito JARVIS_PROXMOX_URL, "
        "JARVIS_PROXMOX_TOKEN_ID y JARVIS_PROXMOX_TOKEN_SECRET en el .env del "
        "servidor. El token se crea en Datacenter → Permissions → API Tokens.",
        is_error=True,
    )


def _fallo(exc: Exception) -> ToolResult:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return ToolResult(
                "Proxmox rechazó las credenciales (HTTP "
                f"{exc.code}). Revisa el token y que tenga permisos de lectura.",
                is_error=True,
            )
        return ToolResult(f"Proxmox respondió HTTP {exc.code}.", is_error=True)
    return ToolResult(f"No pude contactar con Proxmox: {type(exc).__name__}.", is_error=True)


class ProxmoxStatusTool(Tool):
    name = "proxmox_status"
    description = "Estado de los nodos del servidor Proxmox: CPU, memoria, disco y tiempo encendido."
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self, client: ProxmoxClient) -> None:
        self.client = client

    async def run(self, **kwargs: Any) -> ToolResult:
        if not self.client.configured:
            return _no_configurado()
        try:
            nodos = await self.client.get("/nodes")
        except Exception as exc:
            logger.warning("Fallo consultando nodos de Proxmox", exc_info=True)
            return _fallo(exc)
        if not nodos:
            return ToolResult("Proxmox respondió, pero no hay ningún nodo.")

        lineas = []
        for n in nodos:
            mem, maxmem = float(n.get("mem", 0)), float(n.get("maxmem", 1)) or 1
            disk, maxdisk = float(n.get("disk", 0)), float(n.get("maxdisk", 1)) or 1
            horas = int(float(n.get("uptime", 0)) // 3600)
            lineas.append(
                f"{n.get('node')}: {n.get('status')} · CPU {float(n.get('cpu', 0)) * 100:.1f}% "
                f"· RAM {mem / maxmem * 100:.1f}% ({mem / 2**30:.1f}/{maxmem / 2**30:.1f} GiB) "
                f"· disco {disk / maxdisk * 100:.1f}% · encendido {horas} h"
            )
        return ToolResult("\n".join(lineas))


class ProxmoxGuestsTool(Tool):
    name = "proxmox_guests"
    description = (
        "Lista las máquinas virtuales y los contenedores LXC del servidor Proxmox, "
        "con su estado, memoria y en qué nodo viven."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "only_running": {"type": "boolean", "description": "Solo las que están encendidas."},
        },
        "additionalProperties": False,
    }

    def __init__(self, client: ProxmoxClient) -> None:
        self.client = client

    async def run(self, only_running: bool = False, **kwargs: Any) -> ToolResult:
        if not self.client.configured:
            return _no_configurado()
        try:
            recursos = await self.client.get("/cluster/resources?type=vm")
        except Exception as exc:
            logger.warning("Fallo listando invitados de Proxmox", exc_info=True)
            return _fallo(exc)

        invitados = [r for r in (recursos or []) if r.get("type") in {"qemu", "lxc"}]
        if only_running:
            invitados = [r for r in invitados if r.get("status") == "running"]
        if not invitados:
            return ToolResult("No hay máquinas ni contenedores que listar.")

        # Estructurado a propósito: el pizarrón del HUD lo agrupa y lo cuenta solo.
        return ToolResult(json.dumps(
            {
                "total": len(invitados),
                "guests": [
                    {
                        "vmid": r.get("vmid"),
                        "name": r.get("name") or f"vm-{r.get('vmid')}",
                        "type": "LXC" if r.get("type") == "lxc" else "VM",
                        "status": r.get("status"),
                        "node": r.get("node"),
                        "mem_gib": round(float(r.get("maxmem", 0)) / 2**30, 1),
                    }
                    for r in invitados
                ],
            },
            ensure_ascii=False, separators=(",", ":"),
        ))


def register_proxmox_tools(registry: ToolRegistry, settings) -> None:
    """Se registran aunque falten credenciales: así JARVIS puede *decir* que la
    capacidad existe y qué le falta, en vez de comportarse como si no existiera."""
    cliente = ProxmoxClient(
        settings.proxmox_url,
        settings.proxmox_token_id,
        settings.proxmox_token_secret,
        settings.proxmox_verify_ssl,
    )
    registry.register(ProxmoxStatusTool(cliente))
    registry.register(ProxmoxGuestsTool(cliente))
