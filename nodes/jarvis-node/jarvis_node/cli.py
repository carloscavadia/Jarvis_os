"""Punto de entrada: `jarvis-node`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from jarvis_node.agent import NodeAgent
from jarvis_node.config import ConfigError, load_capabilities
from jarvis_node.mqtt import NodeTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis-node", description="Agente de máquina de JARVIS_OS.")
    parser.add_argument("--config", help="Ruta del fichero de configuración.")
    parser.add_argument("--host", help="Broker MQTT (o JARVIS_NODE_BROKER).")
    parser.add_argument("--port", type=int, default=8883)
    parser.add_argument("--no-tls", action="store_true", help="Solo para pruebas en local.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Valida la configuración, imprime el manifiesto y sale sin conectar.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        caps = load_capabilities(args.config)
    except ConfigError as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        return 2

    agent = NodeAgent(caps)
    if args.check:
        # Ver qué se está a punto de conceder, antes de concederlo.
        print(agent.manifest_json())
        return 0

    host = args.host or os.environ.get("JARVIS_NODE_BROKER", "")
    if not host:
        print("Falta el broker: usa --host o JARVIS_NODE_BROKER.", file=sys.stderr)
        return 2

    async def run() -> None:
        transport = NodeTransport(
            agent,
            host,
            args.port,
            username=os.environ.get("JARVIS_NODE_USER", ""),
            password=os.environ.get("JARVIS_NODE_PASSWORD", ""),
            use_tls=not args.no_tls,
        )
        transport.start()
        try:
            await asyncio.Event().wait()
        finally:
            transport.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
