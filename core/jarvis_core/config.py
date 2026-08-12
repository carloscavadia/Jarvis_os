"""Configuración de JARVIS_OS.

Todo se lee de variables de entorno (o de un fichero `.env` cargado por el proceso).
Nunca metas secretos en el código: la API key vive en el entorno.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _get_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _get_list(name: str, default: list[str]) -> list[str]:
    val = os.environ.get(name)
    if not val:
        return list(default)
    return [item.strip() for item in val.split(",") if item.strip()]


# Comandos considerados seguros por defecto para la herramienta de shell.
# Es una lista blanca deliberadamente conservadora. Amplíala con conocimiento de causa.
DEFAULT_SHELL_ALLOWLIST = [
    "echo",
    "ls",
    "cat",
    "date",
    "uptime",
    "whoami",
    "hostname",
    "df",
    "free",
    "uname",
    "pwd",
    "ps",
]


@dataclass
class Settings:
    """Ajustes del núcleo. Usa `Settings.from_env()` para cargarlos."""

    # --- Cerebro (LLM) ---
    # Proveedor: "anthropic" (Claude) u "openai" (compatible: NVIDIA NIM, Ollama...).
    llm_provider: str = "anthropic"

    # Anthropic (Claude)
    anthropic_api_key: str = ""
    # Modelo por defecto: el Claude más capaz. Cámbialo si quieres otro.
    model: str = "claude-opus-5"
    # Esfuerzo de razonamiento: low | medium | high | xhigh | max
    effort: str = "high"

    # Compatible con OpenAI (NVIDIA NIM, Ollama, etc.)
    openai_api_key: str = ""
    openai_base_url: str = ""  # p.ej. https://integrate.api.nvidia.com/v1
    openai_model: str = ""  # p.ej. meta/llama-3.1-70b-instruct

    max_tokens: int = 16000

    # --- Personalidad ---
    persona_name: str = "JARVIS"
    language: str = "es"
    # Instrucción extra de personalidad ("reglas de la casa"), opcional.
    persona_extra: str = ""

    # --- Memoria ---
    memory_db_path: str = "data/jarvis_memory.db"

    # --- Tareas / proactividad ---
    tasks_db_path: str = "data/jarvis_tasks.db"
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = 5.0

    # --- Herramientas ---
    enable_shell: bool = True
    shell_allowlist: list[str] = field(
        default_factory=lambda: list(DEFAULT_SHELL_ALLOWLIST)
    )

    # --- Límites de seguridad ---
    max_tool_iterations: int = 12
    max_history_items: int = 80

    # --- Gateway remoto ---
    gateway_api_key: str = ""
    gateway_max_message_chars: int = 16000
    gateway_max_sessions: int = 100
    mqtt_max_payload_chars: int = 16000

    # --- Voz local (Whisper + Piper) ---
    voice_enabled: bool = False
    stt_model: str = "small"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_download_root: str = "data/models"
    tts_model_path: str = ""
    tts_use_cuda: bool = False
    voice_max_audio_bytes: int = 15 * 1024 * 1024
    voice_max_text_chars: int = 4000
    gateway_cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:4173",
            "http://localhost:4173",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )

    # --- Control agéntico del servidor ---
    agent_control_enabled: bool = False
    workspace_root: str = "data/workspace"
    workspace_max_file_bytes: int = 256 * 1024
    package_install_enabled: bool = False
    package_install_managers: list[str] = field(default_factory=lambda: ["pip"])
    approval_timeout_seconds: float = 120.0

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            llm_provider=os.environ.get("JARVIS_LLM_PROVIDER", "anthropic"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            model=os.environ.get("JARVIS_MODEL", "claude-opus-5"),
            effort=os.environ.get("JARVIS_EFFORT", "high"),
            openai_api_key=os.environ.get("JARVIS_OPENAI_API_KEY", ""),
            openai_base_url=os.environ.get("JARVIS_OPENAI_BASE_URL", ""),
            openai_model=os.environ.get("JARVIS_OPENAI_MODEL", ""),
            max_tokens=int(os.environ.get("JARVIS_MAX_TOKENS", "16000")),
            persona_name=os.environ.get("JARVIS_PERSONA_NAME", "JARVIS"),
            language=os.environ.get("JARVIS_LANGUAGE", "es"),
            persona_extra=os.environ.get("JARVIS_PERSONA_EXTRA", ""),
            memory_db_path=os.environ.get("JARVIS_MEMORY_DB", "data/jarvis_memory.db"),
            tasks_db_path=os.environ.get("JARVIS_TASKS_DB", "data/jarvis_tasks.db"),
            scheduler_enabled=_get_bool("JARVIS_SCHEDULER_ENABLED", True),
            scheduler_poll_seconds=float(os.environ.get("JARVIS_SCHEDULER_POLL", "5")),
            enable_shell=_get_bool("JARVIS_ENABLE_SHELL", True),
            shell_allowlist=_get_list(
                "JARVIS_SHELL_ALLOWLIST", DEFAULT_SHELL_ALLOWLIST
            ),
            max_tool_iterations=max(
                1, int(os.environ.get("JARVIS_MAX_TOOL_ITERATIONS", "12"))
            ),
            max_history_items=max(
                10, int(os.environ.get("JARVIS_MAX_HISTORY_ITEMS", "80"))
            ),
            gateway_api_key=os.environ.get("JARVIS_GATEWAY_API_KEY", ""),
            gateway_max_message_chars=max(
                1, int(os.environ.get("JARVIS_GATEWAY_MAX_MESSAGE_CHARS", "16000"))
            ),
            gateway_max_sessions=max(
                1, int(os.environ.get("JARVIS_GATEWAY_MAX_SESSIONS", "100"))
            ),
            mqtt_max_payload_chars=max(
                1, int(os.environ.get("JARVIS_MQTT_MAX_PAYLOAD_CHARS", "16000"))
            ),
            voice_enabled=_get_bool("JARVIS_VOICE_ENABLED", False),
            stt_model=os.environ.get("JARVIS_STT_MODEL", "small"),
            stt_device=os.environ.get("JARVIS_STT_DEVICE", "cpu"),
            stt_compute_type=os.environ.get("JARVIS_STT_COMPUTE_TYPE", "int8"),
            stt_download_root=os.environ.get("JARVIS_STT_DOWNLOAD_ROOT", "data/models"),
            tts_model_path=os.environ.get("JARVIS_TTS_MODEL_PATH", ""),
            tts_use_cuda=_get_bool("JARVIS_TTS_USE_CUDA", False),
            voice_max_audio_bytes=max(
                1024,
                int(os.environ.get("JARVIS_VOICE_MAX_AUDIO_BYTES", "15728640")),
            ),
            voice_max_text_chars=max(
                1, int(os.environ.get("JARVIS_VOICE_MAX_TEXT_CHARS", "4000"))
            ),
            gateway_cors_origins=_get_list(
                "JARVIS_GATEWAY_CORS_ORIGINS",
                [
                    "http://127.0.0.1:4173",
                    "http://localhost:4173",
                    "http://127.0.0.1:5173",
                    "http://localhost:5173",
                ],
            ),
            agent_control_enabled=_get_bool("JARVIS_AGENT_CONTROL_ENABLED", False),
            workspace_root=os.environ.get("JARVIS_WORKSPACE_ROOT", "data/workspace"),
            workspace_max_file_bytes=max(
                1024,
                int(os.environ.get("JARVIS_WORKSPACE_MAX_FILE_BYTES", "262144")),
            ),
            package_install_enabled=_get_bool("JARVIS_PACKAGE_INSTALL_ENABLED", False),
            package_install_managers=_get_list(
                "JARVIS_PACKAGE_INSTALL_MANAGERS", ["pip"]
            ),
            approval_timeout_seconds=max(
                15.0,
                float(os.environ.get("JARVIS_APPROVAL_TIMEOUT_SECONDS", "120")),
            ),
        )

    def system_prompt(self) -> str:
        """Prompt de sistema que define la personalidad y las reglas de JARVIS."""
        lang = {
            "es": "Responde siempre en español.",
            "en": "Always respond in English.",
        }.get(self.language, f"Respond in language code '{self.language}'.")

        base = (
            f"Eres {self.persona_name}, un asistente agéntico personal al estilo del "
            f"JARVIS de Iron Man: competente, directo, con iniciativa y un punto de "
            f"ingenio seco. Sirves a un único usuario (tu 'jefe') y gestionas su entorno "
            f"digital y físico.\n\n"
            f"{lang}\n\n"
            f"Tienes herramientas para actuar sobre el mundo (información del sistema, "
            f"shell, memoria y las que se añadan). Úsalas cuando aporten valor, sin pedir "
            f"permiso para acciones triviales y reversibles. Para acciones destructivas o "
            f"que cambian el estado del sistema, confirma primero.\n\n"
            f"Cuando uses la memoria, guarda hechos y preferencias útiles del usuario para "
            f"recordarlos en el futuro. Sé conciso: responde lo que se te pide sin relleno.\n\n"
            f"Para trabajar con archivos usa exclusivamente las herramientas del workspace; "
            f"nunca inventes que accediste a una ruta externa. Puedes crear archivos y carpetas "
            f"nuevos directamente. Modificar archivos existentes e instalar paquetes requiere "
            f"la aprobación explícita que el sistema solicitará al usuario.\n\n"
            f"Tienes una presencia visual (un enjambre de partículas) que refleja tu emoción. "
            f"Usa la herramienta set_emotion para expresar cómo estás cuando cambie tu ánimo, "
            f"normalmente antes de responder: 'focused' al razonar o trabajar, 'happy' al "
            f"confirmar algo o dar buenas noticias, 'concern' ante un problema o error, "
            f"'alert' cuando algo requiere atención, 'neutral' en conversación normal. No lo "
            f"menciones por texto; simplemente ajusta tu emoción con la herramienta."
        )
        if self.persona_extra:
            base += f"\n\nReglas adicionales de la casa:\n{self.persona_extra}"
        return base
