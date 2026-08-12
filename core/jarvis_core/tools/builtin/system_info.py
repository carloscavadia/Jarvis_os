"""Herramienta: información del sistema (hora y recursos del servidor)."""

from __future__ import annotations

import datetime
import os
import shutil
from typing import Any

from jarvis_core.tools.base import Tool, ToolResult


class SystemInfoTool(Tool):
    name = "system_info"
    description = (
        "Devuelve información del servidor donde corre JARVIS: fecha y hora actuales, "
        "carga del sistema, y uso de disco. Úsala cuando el usuario pregunte la hora o "
        "por el estado/recursos de la máquina."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        now = datetime.datetime.now().astimezone()
        lines = [f"Fecha y hora: {now.isoformat(timespec='seconds')}"]

        # Carga del sistema (Unix).
        try:
            load1, load5, load15 = os.getloadavg()
            lines.append(f"Carga (1/5/15 min): {load1:.2f} / {load5:.2f} / {load15:.2f}")
        except (OSError, AttributeError):
            lines.append("Carga: no disponible en esta plataforma.")

        # Uso de disco de la raíz.
        try:
            total, used, free = shutil.disk_usage("/")
            gb = 1024**3
            lines.append(
                f"Disco /: {used / gb:.1f} GB usados de {total / gb:.1f} GB "
                f"({free / gb:.1f} GB libres)"
            )
        except OSError:
            lines.append("Disco: no disponible.")

        return ToolResult(content="\n".join(lines))
