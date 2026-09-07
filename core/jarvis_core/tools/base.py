"""Base del sistema de herramientas.

Una `Tool` es una capacidad: tiene nombre, descripción (que el LLM lee para decidir cuándo
usarla), un esquema JSON de entrada y un método `run` asíncrono. El `ToolRegistry` las
agrupa y genera las definiciones que el modelo necesita.

Añadir una capacidad = subclasear `Tool` (o usar `tool_from_function`) y registrarla.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class ToolResult:
    """Resultado de ejecutar una herramienta."""

    content: str
    is_error: bool = False


class Tool(ABC):
    """Clase base de una herramienta."""

    name: str
    description: str
    input_schema: dict[str, Any]
    #: Si es True, requiere confirmación antes de ejecutarse (acciones sensibles).
    requires_confirmation: bool = False
    #: Campo de `policy_subject` que identifica la *clase* de operación, para que una
    #: concesión de sesión sea util sin ser un cheque en blanco. `run_shell` usa
    #: `executable`, de modo que aprobar `git status` concede `git`, no la línea exacta
    #: (que no volvería a repetirse) ni la herramienta entera (que sería demasiado).
    #: Sin valor, la concesión cubre la herramienta completa.
    policy_scope_key: str | None = None

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        """Ejecuta la herramienta con los parámetros dados por el modelo."""
        raise NotImplementedError

    def definition(self) -> dict[str, Any]:
        """Definición en el formato que espera la API de tool-use."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def policy_subject(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Lo que el motor de políticas evalúa para esta llamada.

        Por defecto son los argumentos tal cual. Una herramienta puede añadir campos
        derivados para que las reglas sean legibles: `run_shell` expone `executable`
        aparte del comando completo, y así una regla dice `executable: git` en vez de
        intentar acertar con un glob sobre la línea entera.
        """
        return dict(arguments)


class FunctionTool(Tool):
    """Envuelve una función asíncrona como herramienta (azúcar sintáctico)."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        func: Callable[..., Awaitable[ToolResult | str]],
        requires_confirmation: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.requires_confirmation = requires_confirmation
        self._func = func

    async def run(self, **kwargs: Any) -> ToolResult:
        result = await self._func(**kwargs)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(content=str(result))


def tool_from_function(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    *,
    requires_confirmation: bool = False,
) -> Callable[[Callable[..., Awaitable[Any]]], FunctionTool]:
    """Decorador para crear una herramienta a partir de una corrutina."""

    def wrap(func: Callable[..., Awaitable[Any]]) -> FunctionTool:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(f"La herramienta '{name}' debe ser una función async.")
        return FunctionTool(name, description, input_schema, func, requires_confirmation)

    return wrap


class ToolRegistry:
    """Contenedor de herramientas disponibles para el agente."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Herramienta duplicada: '{tool.name}'")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[dict[str, Any]]:
        """Definiciones de todas las herramientas, para pasarlas al LLM."""
        return [t.definition() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def subset(self, names: list[str]) -> ToolRegistry:
        """Vista acotada con solo algunas herramientas, compartiendo instancias.

        Es lo que permite dar a un subagente un conjunto reducido sin duplicar
        estado ni perder las guardas: la herramienta es **el mismo objeto**, así
        que el workspace confinado, la lista blanca del shell y la marca de
        `requires_confirmation` siguen siendo exactamente las mismas.
        """
        acotado = ToolRegistry()
        for nombre in names:
            herramienta = self._tools.get(nombre)
            if herramienta is not None:
                acotado._tools[nombre] = herramienta
        return acotado

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Ejecuta una herramienta por nombre, capturando errores."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(content=f"Herramienta desconocida: '{name}'", is_error=True)
        try:
            return await tool.run(**arguments)
        except Exception as exc:  # noqa: BLE001 — el error vuelve al modelo para que reaccione.
            return ToolResult(content=f"Error ejecutando '{name}': {exc}", is_error=True)
