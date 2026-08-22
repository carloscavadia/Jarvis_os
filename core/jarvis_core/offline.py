"""Modo offline: la garantía de que nada sale de tu red.

No significa «sin red». Home Assistant, Navidrome, un n8n autoalojado o un
Ollama en otra máquina son tuyos y siguen funcionando. Lo que se corta es el
tráfico hacia fuera: OpenAI Realtime, la búsqueda web, los embeddings remotos y
cualquier proveedor de LLM en la nube.

Hasta ahora era posible montar todo esto a mano —proveedor local, voz local,
palabra de activación local— pero comprobarlo exigía auditar diez variables de
entorno, y bastaba olvidar una para que el audio o los hechos personales
salieran de casa sin que nadie se enterase. Aquí es un interruptor, y el estado
se puede verificar.

El criterio es **cerrado por defecto**: un destino que no se pueda demostrar
interno se considera externo. Un nombre de dominio propio en la LAN se declara
en `JARVIS_OFFLINE_ALLOWED_HOSTS`.
"""

from __future__ import annotations

import ipaddress
import urllib.parse

#: Sufijos que solo existen dentro de una red doméstica.
_PRIVATE_SUFFIXES = (".local", ".lan", ".home", ".internal", ".arpa")
_PRIVATE_NAMES = {"localhost", "host.docker.internal"}


def is_private_endpoint(url: str, allowed_hosts: tuple[str, ...] = ()) -> bool:
    """¿Este destino está dentro de tu red?

    Cerrado por defecto: si no se puede demostrar que es interno, no lo es.
    """
    if not url:
        return True  # nada configurado no es tráfico saliente
    parsed = urllib.parse.urlsplit(url if "//" in url else f"//{url}")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return False
    if host in _PRIVATE_NAMES or host in {h.lower() for h in allowed_hosts}:
        return True
    if host.endswith(_PRIVATE_SUFFIXES):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        # Un nombre que hay que resolver. No se resuelve aquí a propósito: una
        # comprobación de privacidad no debería depender de una consulta DNS que
        # puede fallar o mentir. Se declara explícitamente o se considera fuera.
        return False


def offline_violations(settings) -> list[str]:
    """Qué seguiría saliendo de la red pese al modo offline.

    Se usa en `/ready` y al arrancar: el objetivo es que puedas comprobarlo de un
    vistazo en vez de auditar la configuración a mano.
    """
    if not settings.offline_mode:
        return []

    permitidos = tuple(settings.offline_allowed_hosts)
    problemas: list[str] = []

    proveedor = settings.llm_provider.lower()
    if proveedor == "anthropic":
        problemas.append(
            "el proveedor de LLM es Anthropic (nube). Usa JARVIS_LLM_PROVIDER=openai "
            "con JARVIS_OPENAI_BASE_URL apuntando a tu Ollama."
        )
    elif proveedor in {"openai_responses", "openai-native", "openai_native"}:
        problemas.append("el proveedor de LLM es OpenAI (nube).")
    elif proveedor in {"openai", "nvidia", "ollama", "compatible"} and not is_private_endpoint(
        settings.openai_base_url, permitidos
    ):
        problemas.append(
            f"JARVIS_OPENAI_BASE_URL apunta fuera de tu red ({settings.openai_base_url or 'sin definir'})."
        )

    if settings.embeddings_provider in {"openai", "nvidia", "remote"} and not is_private_endpoint(
        settings.openai_base_url, permitidos
    ):
        problemas.append("los embeddings remotos saldrían de tu red; usa JARVIS_EMBEDDINGS=local.")

    if settings.n8n_webhook_url and not is_private_endpoint(settings.n8n_webhook_url, permitidos):
        problemas.append("el webhook de n8n apunta fuera de tu red.")

    return problemas


def apply_offline_mode(settings) -> None:
    """Apaga lo que sale a internet. Se aplica al cargar la configuración.

    Forzarlo aquí y no en cada punto de uso es deliberado: así no hay forma de
    olvidarse de una ruta. Lo que no se puede apagar sin más —el proveedor de
    LLM— se reporta como incumplimiento en `/ready`.
    """
    if not settings.offline_mode:
        return
    # Voz de OpenAI: el audio saldría de casa.
    settings.openai_realtime_enabled = False
    settings.realtime_conversation_enabled = False
    # Búsqueda y lectura web: internet público por definición.
    settings.internet_access_enabled = False
    # Embeddings: si el endpoint no es interno, se cae al modelo local.
    if settings.embeddings_provider in {"openai", "nvidia", "remote"} and not is_private_endpoint(
        settings.openai_base_url, tuple(settings.offline_allowed_hosts)
    ):
        settings.embeddings_provider = "local"
