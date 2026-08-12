"""Fábrica de proveedores de LLM.

Elige el cerebro según la configuración. La memoria interna de JARVIS es independiente del
proveedor: cambiar de cerebro no afecta a lo que JARVIS recuerda.
"""

from __future__ import annotations

from jarvis_core.config import Settings
from jarvis_core.llm.base import LLMProvider


def build_llm(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.lower()

    if provider == "anthropic":
        from jarvis_core.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.model,
            max_tokens=settings.max_tokens,
            effort=settings.effort,
        )

    if provider in {"openai", "nvidia", "ollama", "compatible"}:
        from jarvis_core.llm.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            max_tokens=settings.max_tokens,
        )

    raise ValueError(
        f"Proveedor de LLM desconocido: '{settings.llm_provider}'. "
        f"Usa 'anthropic' u 'openai' (compatible con NVIDIA NIM / Ollama)."
    )
