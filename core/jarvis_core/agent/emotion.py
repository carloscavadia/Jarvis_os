"""Estado emocional de JARVIS.

JARVIS expresa una 'emoción' que colorea la animación del HUD. La emoción la elige el propio
agente llamando a la herramienta `set_emotion` (ver tools/builtin/emotion_tool.py). Este
objeto guarda la emoción actual y la comparte entre la herramienta y el orquestador.
"""

from __future__ import annotations

from dataclasses import dataclass

# Emociones válidas (deben coincidir con las del HUD y docs/protocol.md).
VALID_EMOTIONS = {"neutral", "happy", "alert", "focused", "concern"}


@dataclass
class EmotionState:
    current: str = "neutral"

    def set(self, emotion: str) -> bool:
        emotion = (emotion or "").strip().lower()
        if emotion in VALID_EMOTIONS:
            self.current = emotion
            return True
        return False
