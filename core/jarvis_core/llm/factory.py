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

    if provider in {"openai_responses", "openai-native", "openai_native"}:
        from jarvis_core.llm.openai_responses import OpenAIResponsesProvider

        return OpenAIResponsesProvider(
            api_key=settings.openai_responses_api_key,
            model=settings.openai_responses_model,
            max_tokens=settings.openai_responses_max_tokens,
            reasoning_effort=settings.openai_responses_effort,
            verbosity=settings.openai_responses_verbosity,
            daily_token_limit=settings.openai_responses_daily_token_limit,
            usage_db_path=settings.openai_usage_db_path,
            history_items=settings.openai_responses_history_items,
        )

    if provider in {"openai", "nvidia", "ollama", "compatible"}:
        from jarvis_core.llm.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            max_tokens=settings.max_tokens,
            enable_thinking=settings.openai_enable_thinking,
        )

    raise ValueError(
        f"Proveedor de LLM desconocido: '{settings.llm_provider}'. "
        f"Usa 'anthropic', 'openai_responses' u 'openai' "
        f"(compatible con NVIDIA NIM / Ollama)."
    )
