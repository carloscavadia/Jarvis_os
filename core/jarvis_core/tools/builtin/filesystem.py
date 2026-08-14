"""Herramientas de archivos confinadas a un directorio de trabajo."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from jarvis_core.tools.base import Tool, ToolResult


class WorkspaceGuard:
    """Resuelve rutas relativas sin permitir escapes ni enlaces fuera del workspace."""

    def __init__(self, root: str, max_file_bytes: int) -> None:
        self.root = Path(root).expanduser().resolve()
        self.max_file_bytes = max_file_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, raw_path: str, *, allow_root: bool = False) -> Path:
        raw = (raw_path or ".").strip()
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(self.root)
            except ValueError:
                pass
        if candidate.is_absolute():
            raise ValueError("La ruta debe ser relativa al workspace.")
        resolved = (self.root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("La ruta sale del workspace permitido.") from exc
        if resolved == self.root and not allow_root:
            raise ValueError("Esta operación requiere una ruta dentro del workspace.")
        return resolved

    def display(self, path: Path) -> str:
        try:
            relative = path.relative_to(self.root)
            return "." if not relative.parts else relative.as_posix()
        except ValueError:
            return path.name


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = (
        "Lista archivos y carpetas dentro del workspace seguro del servidor. "
        "Las rutas siempre son relativas a ese workspace."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Carpeta relativa; '.' es la raíz.",
            }
        },
        "additionalProperties": False,
    }

    def __init__(self, guard: WorkspaceGuard, max_entries: int = 200) -> None:
        self.guard = guard
        self.max_entries = max_entries

    def _list(self, raw_path: str) -> ToolResult:
        path = self.guard.resolve(raw_path, allow_root=True)
        if not path.exists():
            return ToolResult(content="La carpeta no existe.", is_error=True)
        if not path.is_dir():
            return ToolResult(content="La ruta no es una carpeta.", is_error=True)
        entries = sorted(
            path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
        )
        lines: list[str] = []
        for item in entries[: self.max_entries]:
            if item.is_symlink():
                kind, size = "enlace bloqueado", ""
            elif item.is_dir():
                kind, size = "carpeta", ""
            else:
                kind, size = "archivo", f" · {item.stat().st_size} bytes"
            lines.append(f"{kind}: {item.name}{size}")
        if len(entries) > self.max_entries:
            lines.append(f"… {len(entries) - self.max_entries} entradas omitidas")
        return ToolResult(content="\n".join(lines) or "(carpeta vacía)")

    async def run(self, path: str = ".", **kwargs: Any) -> ToolResult:
        return await asyncio.to_thread(self._list, path)


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Lee un archivo de texto UTF-8 dentro del workspace seguro. "
        "No puede acceder a secretos ni archivos del sistema fuera del workspace."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa del archivo."}
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, guard: WorkspaceGuard) -> None:
        self.guard = guard

    def _read(self, raw_path: str) -> ToolResult:
        path = self.guard.resolve(raw_path)
        if not path.is_file():
            return ToolResult(content="El archivo no existe.", is_error=True)
        size = path.stat().st_size
        if size > self.guard.max_file_bytes:
            return ToolResult(
                content=f"El archivo supera el límite de {self.guard.max_file_bytes} bytes.",
                is_error=True,
            )
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(content="El archivo no es texto UTF-8.", is_error=True)
        return ToolResult(content=content or "(archivo vacío)")

    async def run(self, path: str = "", **kwargs: Any) -> ToolResult:
        return await asyncio.to_thread(self._read, path)


class CreateDirectoryTool(Tool):
    name = "create_directory"
    description = (
        "Crea una carpeta, incluyendo carpetas padre, dentro del workspace seguro."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa de la carpeta."}
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, guard: WorkspaceGuard) -> None:
        self.guard = guard

    def _create(self, raw_path: str) -> ToolResult:
        path = self.guard.resolve(raw_path)
        if path.exists():
            if path.is_dir():
                return ToolResult(
                    content=f"La carpeta {self.guard.display(path)} ya existe."
                )
            return ToolResult(
                content="La ruta ya existe y no es una carpeta.", is_error=True
            )
        path.mkdir(parents=True)
        return ToolResult(content=f"Carpeta creada: {self.guard.display(path)}")

    async def run(self, path: str = "", **kwargs: Any) -> ToolResult:
        return await asyncio.to_thread(self._create, path)


class CreateFileTool(Tool):
    name = "create_file"
    description = (
        "Crea un archivo de texto nuevo dentro del workspace seguro. "
        "Nunca sobrescribe un archivo existente."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Ruta relativa del archivo nuevo.",
            },
            "content": {"type": "string", "description": "Contenido UTF-8."},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def __init__(self, guard: WorkspaceGuard) -> None:
        self.guard = guard

    def _create(self, raw_path: str, content: str) -> ToolResult:
        encoded = content.encode("utf-8")
        if len(encoded) > self.guard.max_file_bytes:
            return ToolResult(
                content="El contenido supera el límite permitido.", is_error=True
            )
        path = self.guard.resolve(raw_path)
        if not path.parent.is_dir():
            return ToolResult(content="La carpeta padre no existe.", is_error=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
        except FileExistsError:
            return ToolResult(
                content="El archivo ya existe; usa update_file con autorización.",
                is_error=True,
            )
        return ToolResult(content=f"Archivo creado: {self.guard.display(path)}")

    async def run(self, path: str = "", content: str = "", **kwargs: Any) -> ToolResult:
        return await asyncio.to_thread(self._create, path, content)


class UpdateFileTool(Tool):
    name = "update_file"
    description = (
        "Reemplaza o añade texto a un archivo existente del workspace. "
        "Es sensible y exige permiso explícito del usuario antes de modificarlo."
    )
    requires_confirmation = True
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa del archivo."},
            "content": {"type": "string", "description": "Contenido UTF-8."},
            "mode": {"type": "string", "enum": ["replace", "append"]},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def __init__(self, guard: WorkspaceGuard) -> None:
        self.guard = guard

    def _update(self, raw_path: str, content: str, mode: str) -> ToolResult:
        path = self.guard.resolve(raw_path)
        if not path.is_file():
            return ToolResult(content="El archivo no existe.", is_error=True)
        current_size = path.stat().st_size if mode == "append" else 0
        if current_size + len(content.encode("utf-8")) > self.guard.max_file_bytes:
            return ToolResult(
                content="El resultado supera el límite permitido.", is_error=True
            )
        with path.open("a" if mode == "append" else "w", encoding="utf-8") as handle:
            handle.write(content)
        return ToolResult(content=f"Archivo actualizado: {self.guard.display(path)}")

    async def run(
        self,
        path: str = "",
        content: str = "",
        mode: str = "replace",
        **kwargs: Any,
    ) -> ToolResult:
        if mode not in {"replace", "append"}:
            return ToolResult(content="Modo inválido.", is_error=True)
        return await asyncio.to_thread(self._update, path, content, mode)


def register_filesystem_tools(
    registry: Any,
    *,
    root: str,
    max_file_bytes: int,
) -> None:
    guard = WorkspaceGuard(root, max_file_bytes)
    registry.register(ListDirectoryTool(guard))
    registry.register(ReadFileTool(guard))
    registry.register(CreateDirectoryTool(guard))
    registry.register(CreateFileTool(guard))
    registry.register(UpdateFileTool(guard))
