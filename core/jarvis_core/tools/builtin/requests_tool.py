"""Pedirle al usuario lo que hace falta para poder hacer algo.

Saber qué falta no sirve de nada si la única salida es escribirlo en prosa y
esperar que el usuario lo interprete. Esto convierte «me falta X» en una
petición estructurada que el HUD puede presentar como algo accionable.

**Un secreto no vuelve nunca por el chat.** Si el usuario teclea un token en la
conversación, ese token entra en el historial y viaja al modelo en cada turno
posterior, queda en los logs y puede acabar leído en voz alta. Por eso una
petición de credencial dice *dónde* ponerla, y el valor va por un camino que no
pasa por el modelo. JARVIS se entera de que ya está, no de cuál es.
"""

from __future__ import annotations

from typing import Any, ClassVar

from jarvis_core.tools.base import Tool, ToolResult, ToolRegistry

#: Qué tipo de cosa se está pidiendo. Cada una se presenta distinto y, sobre
#: todo, se recoge distinto.
REQUEST_KINDS = {
    "credential": (
        "Un secreto: token, contraseña o clave de API. El usuario lo pondrá donde "
        "corresponda; tú no lo verás ni debes pedirlo por el chat."
    ),
    "setting": "Un valor no secreto: una URL, un identificador, un nombre de host.",
    "permission": "Que el usuario active algo o conceda un permiso en el servidor.",
    "decision": "Que el usuario elija entre alternativas antes de que sigas.",
    "info": "Un dato que solo el usuario conoce y que no es secreto.",
}


class RequestFromUserTool(Tool):
    name = "request_from_user"
    description = (
        "Pide formalmente lo que te falta para completar un encargo, y el HUD lo "
        "presentará como algo accionable en vez de como una frase más. Úsalo cuando "
        "describe_capabilities te diga que falta una credencial, una variable o un "
        "permiso.\n\n"
        "Para un secreto (kind='credential') **nunca pidas que te lo escriban en el "
        "chat**: acabaría en el historial y viajaría al modelo en cada turno. Indica "
        "dónde se pone y sigue sin él; el sistema te avisará cuando esté disponible."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": sorted(REQUEST_KINDS)},
            "what": {
                "type": "string",
                "maxLength": 120,
                "description": "Qué necesitas, en pocas palabras. P. ej. «el token de API de Proxmox».",
            },
            "why": {
                "type": "string",
                "maxLength": 300,
                "description": "Para qué lo necesitas. El usuario decide mejor si sabe qué desbloquea.",
            },
            "where": {
                "type": "string",
                "maxLength": 300,
                "description": (
                    "Dónde se pone o cómo se consigue, con el detalle suficiente para "
                    "actuar. P. ej. «JARVIS_PROXMOX_TOKEN_SECRET en el .env; el token "
                    "se crea en Datacenter → Permissions → API Tokens»."
                ),
            },
            "options": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "maxItems": 5,
                "description": "Solo para kind='decision': las alternativas entre las que elegir.",
            },
        },
        "required": ["kind", "what", "why"],
        "additionalProperties": False,
    }

    async def run(
        self,
        kind: str = "",
        what: str = "",
        why: str = "",
        where: str = "",
        options: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        if kind not in REQUEST_KINDS:
            return ToolResult(
                f"Tipo de petición inválido. Los válidos: {', '.join(sorted(REQUEST_KINDS))}.",
                is_error=True,
            )
        if not what.strip() or not why.strip():
            return ToolResult(
                "Una petición sin qué o sin para qué no es accionable.", is_error=True
            )
        if kind == "decision" and not options:
            return ToolResult(
                "Una decisión necesita alternativas concretas entre las que elegir.",
                is_error=True,
            )
        if kind == "credential" and not where.strip():
            return ToolResult(
                "Para pedir una credencial hay que decir dónde se pone; si no, el "
                "usuario tiene el problema pero no la solución.",
                is_error=True,
            )

        # El resultado que lee el modelo es deliberadamente sobrio: la petición
        # ya está en pantalla, y repetirla en la respuesta sería duplicarla.
        aviso = (
            " Sigue con lo que puedas hacer sin ello y no vuelvas a pedirlo en este turno."
        )
        return ToolResult(f"Petición mostrada al usuario: {what.strip()}.{aviso}")


def register_request_tool(registry: ToolRegistry) -> None:
    registry.register(RequestFromUserTool())
