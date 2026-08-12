"""JARVIS_OS — núcleo agéntico.

Expone las piezas principales para construir y ejecutar el agente:

    from jarvis_core import Settings, Orchestrator, ToolRegistry
"""

from jarvis_core.config import Settings
from jarvis_core.agent.orchestrator import Orchestrator
from jarvis_core.tools.base import Tool, ToolRegistry

__all__ = ["Settings", "Orchestrator", "Tool", "ToolRegistry"]
__version__ = "0.1.0"
