"""Motor de políticas: decide si una herramienta puede ejecutarse.

Hasta ahora la única palanca era `Tool.requires_confirmation`, un booleano de clase:
o la herramienta preguntaba **siempre**, o no preguntaba **nunca**. Con veinte
herramientas de lectura eso se aguanta; con control de máquina no, porque el usuario
acaba aprobando cincuenta `ls` al día y terminando por conceder todo a ciegas —que es
exactamente el fallo que la confirmación pretendía evitar.

Aquí la decisión pasa a ser de tres estados y depende de los argumentos, no solo del
nombre:

    ALLOW → ejecuta y audita
    ASK   → pide confirmación al humano
    DENY  → no se ejecuta, ni preguntando

El orden de evaluación importa y es deliberado:

    1. Denegaciones críticas (`CRITICAL_DENY_RULES`) — no configurables.
    2. Reglas de denegación del usuario.
    3. Concesiones de sesión ("permite esto mientras dure la conversación").
    4. Reglas de allow/ask del usuario.
    5. Línea base de la herramienta.

Las denegaciones van primero y ninguna concesión las levanta: una lista de denegación
que un permiso posterior puede pisar no es una lista de denegación.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Decision(str, Enum):
    """Qué hacer con una llamada a herramienta."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class Rule:
    """Una regla de política.

    `tool` y los valores de `match` son patrones estilo glob (`fnmatch`), de modo que
    `git *` o `/etc/*` funcionan sin obligar al usuario a escribir expresiones
    regulares. Un `match` vacío coincide con cualquier argumento.
    """

    tool: str
    decision: Decision
    match: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    def matches(self, tool: str, arguments: dict[str, Any]) -> bool:
        if not fnmatch.fnmatchcase(tool, self.tool):
            return False
        for key, pattern in self.match.items():
            value = arguments.get(key)
            if value is None:
                return False
            if not fnmatch.fnmatchcase(str(value), pattern):
                return False
        return True


@dataclass(frozen=True)
class Verdict:
    """Resultado de evaluar una llamada, con su porqué.

    El motivo no es decorativo: es lo que se muestra al humano cuando se le pide
    confirmación y lo que queda escrito en la auditoría. Una decisión sin motivo
    obliga a leer la configuración para entender qué pasó.
    """

    decision: Decision
    reason: str
    rule: Rule | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def denied(self) -> bool:
        return self.decision is Decision.DENY


#: Denegaciones que no se pueden desactivar por configuración.
#:
#: Son las operaciones cuyo daño es inmediato e irreversible, o que desarmarían al
#: propio sistema de control (borrar la auditoría, reescribir la política). Se evalúan
#: antes que nada y ninguna concesión de sesión las levanta. Si algún día una de estas
#: hace falta de verdad, se hace a mano fuera de JARVIS: ese roce es intencionado.
CRITICAL_DENY_RULES: tuple[Rule, ...] = (
    Rule(
        tool="run_shell",
        decision=Decision.DENY,
        match={"executable": "mkfs*"},
        reason="Formatear un sistema de archivos es irreversible.",
    ),
    Rule(
        tool="run_shell",
        decision=Decision.DENY,
        match={"executable": "dd"},
        reason="`dd` puede sobrescribir un disco entero sin confirmación.",
    ),
    Rule(
        tool="run_shell",
        decision=Decision.DENY,
        match={"command": "rm *-r*/"},
        reason="Borrado recursivo desde la raíz.",
    ),
    Rule(
        tool="run_shell",
        decision=Decision.DENY,
        match={"executable": "shutdown"},
        reason="Apagar el anfitrión deja a JARVIS sin forma de revertirlo.",
    ),
    Rule(
        tool="run_shell",
        decision=Decision.DENY,
        match={"executable": "reboot"},
        reason="Reiniciar el anfitrión deja a JARVIS sin forma de revertirlo.",
    ),
    # El registro de auditoría y la base de políticas son la única prueba de lo que
    # hizo el agente. Si el agente puede tocarlas, no son prueba de nada.
    Rule(
        tool="*",
        decision=Decision.DENY,
        match={"path": "*jarvis_audit.db*"},
        reason="La auditoría no es escribible desde las herramientas.",
    ),
    Rule(
        tool="*",
        decision=Decision.DENY,
        match={"path": "*/.env*"},
        reason="Los secretos no se leen ni se escriben por herramienta.",
    ),
)


class PolicyEngine:
    """Evalúa llamadas a herramientas contra reglas y concesiones de sesión."""

    def __init__(
        self,
        rules: list[Rule] | None = None,
        *,
        include_critical_denies: bool = True,
    ) -> None:
        self._rules = list(rules or [])
        self._critical = list(CRITICAL_DENY_RULES) if include_critical_denies else []
        #: Concesiones vivas: {id_de_sesión: [reglas]}. No se persisten a propósito;
        #: un permiso dado en una conversación no debe sobrevivir a un reinicio.
        self._grants: dict[str, list[Rule]] = {}

    # -- configuración ----------------------------------------------------

    def add_rule(self, rule: Rule) -> None:
        self._rules.append(rule)

    @property
    def rules(self) -> list[Rule]:
        """Reglas efectivas, con las denegaciones críticas primero."""
        return [*self._critical, *self._rules]

    # -- concesiones de sesión --------------------------------------------

    def grant(self, session: str, rule: Rule) -> None:
        """Concede un permiso mientras dure la sesión.

        Es lo que respalda el "permítelo durante esta conversación" de la UI: sin
        esto, la única respuesta posible a una confirmación es 'sí, una vez', y el
        usuario acaba desactivando la confirmación entera.
        """
        if rule.decision is Decision.DENY:
            raise ValueError("Una concesión no puede ser una denegación.")
        self._grants.setdefault(session, []).append(rule)

    def revoke_session(self, session: str) -> None:
        self._grants.pop(session, None)

    def grants_for(self, session: str) -> list[Rule]:
        return list(self._grants.get(session, []))

    # -- evaluación --------------------------------------------------------

    def evaluate(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        baseline: Decision = Decision.ASK,
        session: str | None = None,
    ) -> Verdict:
        """Decide qué hacer con una llamada concreta.

        `baseline` es lo que se aplica si ninguna regla opina; normalmente sale de
        `Tool.requires_confirmation`, de modo que una instalación sin reglas se
        comporta igual que antes de que existiera este motor.
        """
        for rule in self._critical:
            if rule.matches(tool, arguments):
                return Verdict(Decision.DENY, rule.reason or "Denegación crítica.", rule)

        for rule in self._rules:
            if rule.decision is Decision.DENY and rule.matches(tool, arguments):
                return Verdict(Decision.DENY, rule.reason or "Denegado por política.", rule)

        if session is not None:
            for rule in self._grants.get(session, []):
                if rule.matches(tool, arguments):
                    return Verdict(
                        rule.decision,
                        rule.reason or "Permitido para esta sesión.",
                        rule,
                    )

        for rule in self._rules:
            if rule.matches(tool, arguments):
                return Verdict(rule.decision, rule.reason or "Regla de política.", rule)

        return Verdict(baseline, "Sin regla aplicable; se usa la línea base.")
