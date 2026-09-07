"""Las operaciones que un nodo sabe hacer.

Todas comparten dos reglas:

1. **Reciben datos tipados, no cadenas de shell.** `run_command` toma `argv` como
   lista y se ejecuta con `create_subprocess_exec`, sin intérprete: una tubería o un
   `&&` llegan como argumento literal del programa, nunca como estructura.
2. **Validan contra `NodeCapabilities` antes de tocar nada.** La comprobación no se
   hace en el despachador y se asume aquí: cada operación pide su permiso, para que
   añadir una nueva no pueda olvidarse de pedirlo.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import time
from typing import Any

from jarvis_node.capabilities import CapabilityError, NodeCapabilities

_ARRANQUE = time.time()


async def system_info(caps: NodeCapabilities, **_: Any) -> dict[str, Any]:
    """Identidad y salud de la máquina. Es la operación más inocua: no toca disco."""
    uso = shutil.disk_usage(os.path.expanduser("~"))
    return {
        "node_id": caps.node_id,
        "hostname": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "disk_total_gb": round(uso.total / 1e9, 2),
        "disk_free_gb": round(uso.free / 1e9, 2),
        "node_uptime_seconds": round(time.time() - _ARRANQUE, 1),
    }


async def list_directory(caps: NodeCapabilities, path: str = "", **_: Any) -> dict[str, Any]:
    destino = caps.resolve_path(path)
    if not destino.is_dir():
        raise CapabilityError(f"'{destino}' no es un directorio.")
    entradas = []
    for hijo in sorted(destino.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            info = hijo.stat()
            tamano = info.st_size
        except OSError:
            # Un archivo que desaparece o que no se puede leer no debe tumbar el
            # listado entero: se muestra sin tamaño.
            tamano = None
        entradas.append(
            {"name": hijo.name, "dir": hijo.is_dir(), "size": tamano}
        )
    return {"path": str(destino), "entries": entradas}


async def read_file(
    caps: NodeCapabilities, path: str = "", max_bytes: int = 0, **_: Any
) -> dict[str, Any]:
    destino = caps.resolve_path(path)
    if not destino.is_file():
        raise CapabilityError(f"'{destino}' no es un archivo.")
    tope = min(int(max_bytes) or caps.max_output_bytes, caps.max_output_bytes)
    datos = destino.read_bytes()[: tope + 1]
    truncado = len(datos) > tope
    texto = datos[:tope].decode("utf-8", errors="replace")
    return {"path": str(destino), "content": texto, "truncated": truncado}


async def write_file(
    caps: NodeCapabilities, path: str = "", content: str = "", **_: Any
) -> dict[str, Any]:
    destino = caps.resolve_path(path, write=True)
    if destino.is_dir():
        raise CapabilityError(f"'{destino}' es un directorio.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    datos = content.encode("utf-8")
    destino.write_bytes(datos)
    return {"path": str(destino), "bytes_written": len(datos)}


async def run_command(
    caps: NodeCapabilities,
    argv: list[str] | None = None,
    cwd: str = "",
    timeout: float = 0,
    **_: Any,
) -> dict[str, Any]:
    """Ejecuta un programa. `argv` es una lista; nunca se pasa por una shell."""
    argv = list(argv or [])
    nombre = caps.check_executable(argv)
    directorio = str(caps.resolve_path(cwd)) if cwd else None
    limite = min(float(timeout) or caps.command_timeout, caps.command_timeout)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=directorio,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        salida, error = await asyncio.wait_for(proc.communicate(), timeout=limite)
    except asyncio.TimeoutError:
        # Sin matarlo, el proceso sobrevive al nodo y sigue consumiendo la máquina
        # mientras nadie lo mira.
        proc.kill()
        await proc.wait()
        raise CapabilityError(f"'{nombre}' superó el límite de {limite:.0f}s.") from None

    def recortar(datos: bytes) -> str:
        texto = datos.decode("utf-8", errors="replace")
        if len(texto) > caps.max_output_bytes:
            return texto[: caps.max_output_bytes] + "\n… (salida recortada)"
        return texto

    return {
        "executable": nombre,
        "exit_code": proc.returncode,
        "stdout": recortar(salida),
        "stderr": recortar(error),
    }


async def list_processes(caps: NodeCapabilities, filter: str = "", **_: Any) -> dict[str, Any]:
    try:
        import psutil
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise CapabilityError("psutil no está instalado en este nodo.") from exc

    aguja = filter.lower()
    procesos = []
    for proceso in psutil.process_iter(["pid", "name", "username"]):
        try:
            info = proceso.info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Los procesos mueren mientras se recorre la lista, y de algunos no se
            # tiene permiso: saltarlos es lo correcto, no abortar el inventario.
            # Se listan las dos excepciones concretas en vez de `Exception` para que
            # un fallo real de psutil siga saliendo a la luz.
            continue
        nombre = (info.get("name") or "").lower()
        if aguja and aguja not in nombre:
            continue
        procesos.append(
            {"pid": info.get("pid"), "name": info.get("name"), "user": info.get("username")}
        )
        if len(procesos) >= 500:
            break
    return {"processes": procesos, "count": len(procesos)}


#: Registro de operaciones. El despachador solo conoce este mapa, así que añadir una
#: capacidad es escribir la función y declararla aquí.
OPERATIONS = {
    "system_info": system_info,
    "list_directory": list_directory,
    "read_file": read_file,
    "write_file": write_file,
    "run_command": run_command,
    "list_processes": list_processes,
}
