"""Que JARVIS sepa de qué es capaz y qué le falta para lo que no puede.

Sin esto, ante «conéctate a mi Proxmox, las claves ya están en el .env» no había
nada que hacer: no puede listar sus herramientas, no sabe qué variables existen
ni cuáles están puestas, y no distingue «esto no se puede» de «esto está apagado»
o de «falta una credencial». Se quedaba en «no puedo», que es la peor respuesta
posible porque no dice qué haría falta.

**Nunca se expone el valor de un secreto**, solo si está puesto. El agente
necesita saber que hay una credencial, no cuál es; y su respuesta puede acabar
en un chat, en un log o leída en voz alta.
"""

from __future__ import annotations

from typing import Any, ClassVar

from jarvis_core.config import Settings
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult


class Capability:
    """Una cosa que JARVIS podría hacer, y qué hace falta para ello."""

    def __init__(
        self,
        name: str,
        summary: str,
        *,
        tools: tuple[str, ...] = (),
        env: tuple[str, ...] = (),
        enabled_by: str = "",
        how: str = "",
    ) -> None:
        self.name = name
        self.summary = summary
        self.tools = tools
        self.env = env
        self.enabled_by = enabled_by
        self.how = how


#: Catálogo de lo que el sistema puede ofrecer. Es la diferencia entre que JARVIS
#: diga «no puedo» y que diga «puedo, pero falta esta variable».
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "proxmox", "Consultar el estado del servidor Proxmox y sus VMs/contenedores.",
        tools=("proxmox_status", "proxmox_guests"),
        env=("JARVIS_PROXMOX_URL", "JARVIS_PROXMOX_TOKEN_ID", "JARVIS_PROXMOX_TOKEN_SECRET"),
        how="Crea un token en Datacenter → Permissions → API Tokens y ponlo en el .env.",
    ),
    Capability(
        "domotica", "Leer y accionar dispositivos de Home Assistant.",
        tools=("query_connector_module", "run_connector_module_action"),
        enabled_by="JARVIS_CONNECTORS_ENABLED",
        how="Registra el módulo desde el botón ⚡ CONECTORES del HUD, con su URL y token.",
    ),
    Capability(
        "musica", "Buscar y reproducir música de la biblioteca.",
        tools=("search_music", "play_music"),
        env=("JARVIS_NAVIDROME_URL", "JARVIS_NAVIDROME_USERNAME", "JARVIS_NAVIDROME_PASSWORD"),
    ),
    Capability(
        "correo", "Enviar correos por SMTP.",
        tools=("send_email",),
        env=("JARVIS_SMTP_HOST", "JARVIS_SMTP_USER", "JARVIS_SMTP_PASS"),
    ),
    Capability(
        "internet", "Buscar en la web y leer páginas públicas.",
        tools=("search_web", "fetch_web_page"),
        enabled_by="JARVIS_INTERNET_ACCESS_ENABLED",
    ),
    Capability(
        "workspace", "Crear, leer y modificar archivos, y ejecutar scripts de Python.",
        tools=("create_file", "read_file", "run_python_file"),
        enabled_by="JARVIS_AGENT_CONTROL_ENABLED",
    ),
    Capability(
        "agenda", "Consultar y crear eventos de calendario.",
        tools=("list_events", "create_event"),
    ),
    Capability(
        "tareas", "Programar recordatorios y tareas periódicas.",
        tools=("schedule_task", "list_tasks"),
    ),
    Capability(
        "memoria", "Recordar hechos del usuario entre conversaciones.",
        tools=("remember", "recall"),
    ),
    Capability(
        "habilidades", "Aprender una habilidad nueva y reutilizarla después.",
        tools=("learn_skill", "execute_skill"),
        enabled_by="JARVIS_SKILLS_PYTHON_ENABLED",
        how="Enciéndelo solo si quieres que JARVIS ejecute código que él mismo escribe.",
    ),
    Capability(
        "mcp", "Añadir herramientas de terceros mediante servidores MCP.",
        tools=("list_mcp_servers",),
        how="Registra el servidor desde el botón ⚡ CONECTORES del HUD.",
    ),
)


class DescribeCapabilitiesTool(Tool):
    name = "describe_capabilities"
    description = (
        "Inventario de lo que puedes hacer ahora mismo y de lo que te falta para lo "
        "que no. Distingue tres cosas que no son lo mismo: la capacidad no existe, "
        "está apagada, o le falta una credencial. Úsalo **antes** de decirle al "
        "usuario que no puedes algo, y también cuando te diga que ya ha configurado "
        "algo y quieras comprobarlo. No devuelve el valor de ningún secreto."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "capability": {
                "type": "string",
                "description": "Nombre de una capacidad concreta. Vacío = el inventario entero.",
            }
        },
        "additionalProperties": False,
    }

    def __init__(self, settings: Settings, registry: ToolRegistry) -> None:
        self._settings = settings
        self._registry = registry

    def _report(self, cap: Capability) -> str:
        import os

        disponibles = set(self._registry.names())
        presentes = [t for t in cap.tools if t in disponibles]
        faltan_env = [v for v in cap.env if not os.environ.get(v, "").strip()]
        apagada = bool(cap.enabled_by) and not _flag(self._settings, cap.enabled_by)

        if presentes and not faltan_env and not apagada:
            return f"✅ {cap.name}: disponible. {cap.summary} Herramientas: {', '.join(presentes)}."

        motivos = []
        if apagada:
            motivos.append(f"está apagada ({cap.enabled_by}=false)")
        if faltan_env:
            motivos.append(f"faltan variables de entorno: {', '.join(faltan_env)}")
        if not presentes and not faltan_env and not apagada:
            motivos.append("no hay ninguna herramienta registrada para esto en esta versión")
        elif not presentes:
            motivos.append("sus herramientas no están registradas")

        linea = f"⚠️ {cap.name}: no disponible — {'; '.join(motivos)}."
        if cap.how:
            linea += f" {cap.how}"
        return linea

    async def run(self, capability: str = "", **kwargs: Any) -> ToolResult:
        pedido = capability.strip().lower()
        catalogo = [c for c in CAPABILITIES if not pedido or c.name == pedido]
        if pedido and not catalogo:
            nombres = ", ".join(c.name for c in CAPABILITIES)
            return ToolResult(
                f"No conozco la capacidad '{capability}'. Las que sé mirar: {nombres}.",
                is_error=True,
            )

        lineas = [self._report(c) for c in catalogo]
        if not pedido:
            registradas = sorted(self._registry.names())
            lineas.append("")
            lineas.append(f"Herramientas registradas ahora mismo ({len(registradas)}): "
                          + ", ".join(registradas))
        return ToolResult("\n".join(lineas))


def _flag(settings: Settings, env_name: str) -> bool:
    """El ajuste que corresponde a una variable de entorno booleana."""
    equivalencias = {
        "JARVIS_CONNECTORS_ENABLED": "connectors_enabled",
        "JARVIS_INTERNET_ACCESS_ENABLED": "internet_access_enabled",
        "JARVIS_AGENT_CONTROL_ENABLED": "agent_control_enabled",
        "JARVIS_SKILLS_PYTHON_ENABLED": "skills_python_enabled",
    }
    atributo = equivalencias.get(env_name)
    return bool(getattr(settings, atributo, False)) if atributo else False


def register_introspection_tool(registry: ToolRegistry, settings: Settings) -> None:
    registry.register(DescribeCapabilitiesTool(settings, registry))
