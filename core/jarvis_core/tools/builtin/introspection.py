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
        "http", "Hablar con cualquier API de la red del usuario (conector HTTP genérico).",
        tools=("query_connector_module", "run_connector_module_action"),
        enabled_by="JARVIS_CONNECTORS_ENABLED",
        how=(
            "Si un servicio no tiene herramienta propia, propón registrarlo como "
            "conector de tipo 'http' desde ⚡ CONECTORES: hace falta la URL base, un "
            "token y las rutas exactas permitidas, declaradas como «GET /ruta» en "
            "lectura y «POST /ruta» en escritura."
        ),
    ),
    Capability(
        "ubicacion", "Saber dónde está el usuario ahora mismo.",
        tools=("where_am_i",),
        env=("JARVIS_LOCATION_ENTITY", "JARVIS_HOME_LAT", "JARVIS_HOME_LON"),
        enabled_by="JARVIS_CONNECTORS_ENABLED",
        how=(
            "Sale de Home Assistant: la app Companion publica la posición del móvil "
            "en 'person.*' o 'device_tracker.*'. El módulo de Home Assistant necesita "
            "'homeassistant.state' entre sus acciones de lectura, porque el listado de "
            "entidades no trae las coordenadas. Sin eso queda la dirección fija del "
            ".env, que no es dónde está el usuario y hay que decirlo al usarla. "
            "Por IP no se puede: dentro de la red solo se ve una dirección privada y "
            "desde fuera sale la del operador."
        ),
    ),
    Capability(
        "navegador", "Conducir un navegador real: buscar, entrar en un resultado, leer, volver.",
        tools=("browse",),
        enabled_by="JARVIS_BROWSER_ENABLED",
        how=(
            "Necesita Chromium en el servidor: reconstruir la imagen con "
            "INSTALL_BROWSER=true y poner JARVIS_BROWSER_ENABLED=true. Sirve para lo "
            "que fetch_web_page no alcanza: páginas que se montan con JavaScript, "
            "formularios y resultados que hay que ir abriendo."
        ),
    ),
    Capability(
        "visor", "Enseñar algo para mirarlo: una imagen, un PDF, un documento, un vídeo o una web.",
        tools=("open_viewer", "show_in_workspace"),
        enabled_by="JARVIS_HUD_WORKSPACE_ENABLED",
        how=(
            "El visor abre ventanas flotantes en el HUD, que se arrastran y conviven. "
            "No es el pizarrón: el pizarrón es para datos —listas, tablas, código— y "
            "el visor para lo que se mira. Las imágenes, PDFs y documentos salen del "
            "workspace; los vídeos, de YouTube; las webs, de una URL pública."
        ),
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
        "Lo que eres y de qué eres capaz: tus capacidades y qué le falta a cada una, "
        "las habilidades que has aprendido y con qué éxito, los especialistas a los "
        "que puedes delegar, tus límites y qué recuerdas del usuario.\n\n"
        "Distingue tres cosas que no son lo mismo: la capacidad no existe, está "
        "apagada, o le falta una credencial. Úsalo **antes** de decirle al usuario "
        "que no puedes algo, cuando te diga que ya ha configurado algo y quieras "
        "comprobarlo, y cuando te pregunte qué sabes hacer. Nunca devuelve el valor "
        "de un secreto ni el contenido de la memoria: para eso está recall."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["all", "capabilities", "skills", "identity", "limits", "memory"],
                "description": "Qué parte mirar. Por defecto, todo.",
            },
            "capability": {
                "type": "string",
                "description": "Una capacidad concreta. Implica scope='capabilities'.",
            },
        },
        "additionalProperties": False,
    }

    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        skills=None,
        memory=None,
        subagents=(),
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._skills = skills
        self._memory = memory
        self._subagents = list(subagents)

    def _identidad(self) -> list[str]:
        """Quién es y con qué cerebro piensa."""
        s = self._settings
        modelo = {
            "anthropic": f"{s.model} (Anthropic)",
            "openai_responses": f"{s.openai_responses_model} (OpenAI)",
        }.get(s.llm_provider, f"{s.openai_model or 'sin definir'} ({s.openai_base_url or s.llm_provider})")
        lineas = [
            "── QUIÉN ERES ──",
            f"Nombre: {s.persona_name} · idioma: {s.language}",
            f"Cerebro: {modelo} · esfuerzo: {s.effort}",
        ]
        if s.offline_mode:
            lineas.append("Modo offline activo: nada sale de la red.")
        if self._persona():
            lineas.append(f"Reglas de la casa vigentes: {self._persona()}")
        return lineas

    def _persona(self) -> str:
        return (self._settings.persona_extra or "").strip()

    def _limites(self) -> list[str]:
        """Lo que no puede hacer por diseño. Saberlo evita prometer de más."""
        s = self._settings
        return [
            "── TUS LÍMITES ──",
            f"Hasta {s.max_tool_iterations} pasos de herramientas por turno, "
            f"y {s.llm_timeout_seconds:.0f} s por llamada al modelo.",
            f"Recuerdas {s.max_history_items} mensajes de la conversación; "
            f"lo anterior vive en la memoria si lo guardaste.",
            f"El resultado de una herramienta se te entrega recortado a "
            f"{s.max_tool_output_chars} caracteres.",
            "Las acciones sensibles necesitan la aprobación del usuario en el HUD; "
            "tú no puedes concedértela.",
        ]

    def _habilidades(self) -> list[str]:
        """Lo que ha aprendido por su cuenta, con qué éxito y cuánto lo usa."""
        if self._skills is None:
            return ["── HABILIDADES APRENDIDAS ──", "El sistema de habilidades no está activo."]
        try:
            aprendidas = self._skills.list_all()
        except Exception:
            return ["── HABILIDADES APRENDIDAS ──", "No pude leer el registro de habilidades."]
        if not aprendidas:
            return [
                "── HABILIDADES APRENDIDAS ──",
                "Ninguna todavía. Cuando resuelvas algo que volverá a hacer falta, "
                "guárdalo con learn_skill en vez de rehacerlo cada vez.",
            ]
        lineas = ["── HABILIDADES APRENDIDAS ──"]
        for h in aprendidas:
            estado = "activa" if h.enabled else "pausada"
            fiabilidad = (
                f", acierto {h.success_rate * 100:.0f}%" if h.usage_count else ", sin usar aún"
            )
            lineas.append(
                f"- {h.name} ({h.skill_type}, {estado}): {h.description or h.title} "
                f"· usada {h.usage_count} veces{fiabilidad}"
            )
        return lineas

    def _especialistas(self) -> list[str]:
        if not self._subagents:
            return []
        lineas = ["── ESPECIALISTAS A LOS QUE PUEDES DELEGAR ──"]
        for spec in self._subagents:
            lineas.append(f"- {spec.name}: {spec.purpose} ({len(spec.tools)} herramientas)")
        return lineas

    def _memoria(self) -> list[str]:
        """Cuánto recuerda, no qué. El contenido se consulta con recall."""
        if self._memory is None:
            return []
        try:
            hechos = self._memory.recall("", limit=100)
        except Exception:
            return []
        if not hechos:
            return ["── MEMORIA ──", "No recuerdas nada del usuario todavía."]
        claves = ", ".join(h.key for h in hechos[:12])
        extra = f" y {len(hechos) - 12} más" if len(hechos) > 12 else ""
        return [
            "── MEMORIA ──",
            f"Recuerdas {len(hechos)} hechos sobre el usuario. Temas: {claves}{extra}. "
            "Usa recall para leer alguno.",
        ]

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

    async def run(self, scope: str = "all", capability: str = "", **kwargs: Any) -> ToolResult:
        pedido = capability.strip().lower()
        if pedido:
            scope = "capabilities"
        ambito = (scope or "all").strip().lower()

        if pedido:
            catalogo = [c for c in CAPABILITIES if c.name == pedido]
            if not catalogo:
                nombres = ", ".join(c.name for c in CAPABILITIES)
                return ToolResult(
                    f"No conozco la capacidad '{capability}'. Las que sé mirar: {nombres}.",
                    is_error=True,
                )
            return ToolResult(self._report(catalogo[0]))

        secciones: list[str] = []
        if ambito in {"all", "identity"}:
            secciones.append("\n".join(self._identidad()))
        if ambito in {"all", "capabilities"}:
            lineas = ["── LO QUE PUEDES HACER ──"]
            lineas += [self._report(c) for c in CAPABILITIES]
            registradas = sorted(self._registry.names())
            lineas.append(f"Herramientas registradas ({len(registradas)}): " + ", ".join(registradas))
            secciones.append("\n".join(lineas))
        if ambito in {"all", "skills"}:
            secciones.append("\n".join(self._habilidades()))
            especialistas = self._especialistas()
            if especialistas:
                secciones.append("\n".join(especialistas))
        if ambito in {"all", "memory"}:
            memoria = self._memoria()
            if memoria:
                secciones.append("\n".join(memoria))
        if ambito in {"all", "limits"}:
            secciones.append("\n".join(self._limites()))

        if not secciones:
            return ToolResult(f"Ámbito desconocido: '{scope}'.", is_error=True)
        return ToolResult("\n\n".join(secciones))


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


def register_introspection_tool(
    registry: ToolRegistry,
    settings: Settings,
    skills=None,
    memory=None,
    subagents=(),
) -> None:
    registry.register(
        DescribeCapabilitiesTool(settings, registry, skills, memory, subagents)
    )
