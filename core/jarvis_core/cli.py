"""Interfaz de línea de comandos de JARVIS.

Uso:
    jarvis chat        # conversación interactiva por terminal
    jarvis ask "..."   # una sola pregunta y salir
    jarvis tools       # lista las herramientas disponibles
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from jarvis_core.agent.emotion import EmotionState
from jarvis_core.agent.orchestrator import Orchestrator
from jarvis_core.config import Settings
from jarvis_core.llm.factory import build_llm
from jarvis_core.memory.store import MemoryStore
from jarvis_core.tasks.store import TaskStore
from jarvis_core.tools.builtin import build_default_registry


async def _confirm_terminal(name: str, arguments: dict[str, Any]) -> bool:
    """Solicita autorización humana; el modelo nunca puede concedérsela solo."""
    detail = json.dumps(arguments, ensure_ascii=False)
    answer = await asyncio.to_thread(
        input,
        f"\nPermiso requerido para {name} con {detail}. ¿Autorizar? [s/N] ",
    )
    return answer.strip().lower() in {"s", "si", "sí", "y", "yes"}


def _load_dotenv() -> None:
    """Carga un `.env` mínimo si existe (sin dependencias externas)."""
    import os
    from pathlib import Path

    for candidate in (Path(".env"), Path("../.env"), Path("../../.env")):
        if candidate.is_file():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return


def _build(settings: Settings) -> tuple[Orchestrator, MemoryStore, TaskStore]:
    memory = MemoryStore(settings.memory_db_path)
    tasks = TaskStore(settings.tasks_db_path)
    emotion = EmotionState()
    registry = build_default_registry(settings, memory, tasks, emotion)
    llm = build_llm(settings)
    orchestrator = Orchestrator(
        llm,
        registry,
        settings,
        confirm=_confirm_terminal,
        emotion=emotion,
    )
    return orchestrator, memory, tasks


async def _chat(settings: Settings) -> None:
    orchestrator, memory, tasks = _build(settings)
    print(f"{settings.persona_name} en línea. Escribe 'salir' para terminar.\n")
    try:
        while True:
            try:
                user = input("tú > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user:
                continue
            if user.lower() in {"salir", "exit", "quit"}:
                break
            reply = await orchestrator.send(user)
            print(f"\n{settings.persona_name} > {reply.text}\n")
    finally:
        memory.close()
        tasks.close()


async def _ask(settings: Settings, question: str) -> None:
    orchestrator, memory, tasks = _build(settings)
    try:
        reply = await orchestrator.send(question)
        print(reply.text)
    finally:
        memory.close()
        tasks.close()


def _tools(settings: Settings) -> None:
    memory = MemoryStore(settings.memory_db_path)
    tasks = TaskStore(settings.tasks_db_path)
    registry = build_default_registry(settings, memory, tasks, EmotionState())
    print("Herramientas disponibles:")
    for definition in registry.definitions():
        print(f"  - {definition['name']}: {definition['description']}")
    memory.close()
    tasks.close()


def _wake_test(settings: Settings, paths: list[str]) -> None:
    """Ayuda a elegir JARVIS_WAKEWORD_THRESHOLD con la voz y la sala reales."""
    from jarvis_core.voice.local import LocalVoiceError
    from jarvis_core.voice.wakeword import calibrate

    print(f"Modelo: {settings.wakeword_model}   umbral actual: {settings.wakeword_threshold}\n")
    print(f"{'grabación':<40}{'pico':>8}   ¿activa?")
    print("─" * 60)
    peaks: list[float] = []
    for path in paths:
        try:
            (_, peak), = calibrate([path], settings.wakeword_model)
        except (LocalVoiceError, OSError) as exc:
            print(f"{path:<40}{'—':>8}   {exc}")
            continue
        peaks.append(peak)
        activa = "sí" if peak >= settings.wakeword_threshold else "no"
        print(f"{path:<40}{peak:>8.3f}   {activa}")

    if peaks:
        print(
            "\nGraba varias veces «Hey JARVIS» y también frases normales que NO deban\n"
            "activar. El umbral correcto queda por encima del pico de las frases\n"
            "normales y claramente por debajo del de las activaciones."
        )


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(
        prog="jarvis", description="JARVIS_OS — núcleo agéntico"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("chat", help="Conversación interactiva por terminal")
    ask_p = sub.add_parser("ask", help="Una sola pregunta y salir")
    ask_p.add_argument("question", help="La pregunta o instrucción")
    sub.add_parser("tools", help="Lista las herramientas disponibles")
    wake_p = sub.add_parser(
        "wake-test",
        help="Mide la palabra de activación en grabaciones WAV para ajustar el umbral",
    )
    wake_p.add_argument("wav", nargs="+", help="Grabaciones a medir")

    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command == "chat" or args.command is None:
        asyncio.run(_chat(settings))
    elif args.command == "ask":
        asyncio.run(_ask(settings, args.question))
    elif args.command == "tools":
        _tools(settings)
    elif args.command == "wake-test":
        _wake_test(settings, args.wav)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
