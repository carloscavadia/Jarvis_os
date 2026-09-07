"""Tests del motor de políticas y del registro de auditoría."""

import asyncio

import pytest

from jarvis_core.agent.orchestrator import Orchestrator
from jarvis_core.config import Settings
from jarvis_core.llm.base import LLMResponse, ToolCall
from jarvis_core.policy.audit import AuditLog, digest_arguments
from jarvis_core.policy.rules import Decision, PolicyEngine, Rule
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult
from jarvis_core.tools.builtin.shell import ShellTool

# --- motor de políticas -------------------------------------------------


def test_sin_reglas_se_usa_la_linea_base():
    engine = PolicyEngine()
    assert engine.evaluate("x", {}, baseline=Decision.ALLOW).decision is Decision.ALLOW
    assert engine.evaluate("x", {}, baseline=Decision.ASK).decision is Decision.ASK


def test_regla_allow_evita_la_confirmacion():
    engine = PolicyEngine(
        [Rule(tool="run_shell", decision=Decision.ALLOW, match={"executable": "ls"})]
    )
    verdict = engine.evaluate("run_shell", {"executable": "ls"}, baseline=Decision.ASK)
    assert verdict.allowed


def test_los_globs_funcionan_en_herramienta_y_argumentos():
    engine = PolicyEngine(
        [Rule(tool="run_*", decision=Decision.ALLOW, match={"executable": "git*"})]
    )
    assert engine.evaluate("run_shell", {"executable": "git"}, baseline=Decision.ASK).allowed
    assert not engine.evaluate("run_shell", {"executable": "svn"}, baseline=Decision.ASK).allowed


def test_argumento_ausente_no_coincide():
    """Una regla que exige un campo no debe aplicarse cuando ese campo no viene."""
    engine = PolicyEngine(
        [Rule(tool="*", decision=Decision.ALLOW, match={"path": "/tmp/*"})]
    )
    assert engine.evaluate("read_file", {}, baseline=Decision.ASK).decision is Decision.ASK


def test_deny_gana_a_una_allow_declarada_antes():
    """El orden de escritura no debe poder colar un permiso por delante de un veto."""
    engine = PolicyEngine(
        [
            Rule(tool="run_shell", decision=Decision.ALLOW, match={"executable": "curl"}),
            Rule(tool="run_shell", decision=Decision.DENY, match={"executable": "curl"}),
        ]
    )
    assert engine.evaluate("run_shell", {"executable": "curl"}, baseline=Decision.ASK).denied


def test_denegaciones_criticas_no_se_pueden_levantar_con_reglas():
    engine = PolicyEngine(
        [Rule(tool="run_shell", decision=Decision.ALLOW, match={"executable": "dd"})]
    )
    assert engine.evaluate("run_shell", {"executable": "dd"}, baseline=Decision.ALLOW).denied


def test_denegaciones_criticas_no_se_pueden_levantar_con_concesiones():
    engine = PolicyEngine()
    engine.grant(
        "s1", Rule(tool="run_shell", decision=Decision.ALLOW, match={"executable": "shutdown"})
    )
    verdict = engine.evaluate(
        "run_shell", {"executable": "shutdown"}, baseline=Decision.ASK, session="s1"
    )
    assert verdict.denied


def test_la_auditoria_no_es_escribible_por_herramienta():
    engine = PolicyEngine()
    verdict = engine.evaluate(
        "update_file", {"path": "data/jarvis_audit.db"}, baseline=Decision.ALLOW
    )
    assert verdict.denied


def test_una_concesion_no_puede_ser_una_denegacion():
    engine = PolicyEngine()
    with pytest.raises(ValueError):
        engine.grant("s1", Rule(tool="x", decision=Decision.DENY))


def test_las_concesiones_estan_aisladas_por_sesion_y_se_revocan():
    engine = PolicyEngine()
    engine.grant("s1", Rule(tool="run_shell", decision=Decision.ALLOW, match={"executable": "curl"}))
    args = {"executable": "curl"}
    assert engine.evaluate("run_shell", args, baseline=Decision.ASK, session="s1").allowed
    # Otra conversación no hereda el permiso.
    assert not engine.evaluate("run_shell", args, baseline=Decision.ASK, session="s2").allowed
    engine.revoke_session("s1")
    assert not engine.evaluate("run_shell", args, baseline=Decision.ASK, session="s1").allowed


def test_el_veredicto_siempre_trae_un_motivo():
    engine = PolicyEngine([Rule(tool="x", decision=Decision.DENY)])
    assert engine.evaluate("x", {}, baseline=Decision.ALLOW).reason
    assert engine.evaluate("otra", {}, baseline=Decision.ALLOW).reason


# --- auditoría ----------------------------------------------------------


def test_la_auditoria_registra_y_devuelve_en_orden_inverso(tmp_path):
    log = AuditLog(str(tmp_path / "audit.db"))
    log.record(tool="a", decision="allow", arguments={"x": 1})
    log.record(tool="b", decision="deny", arguments={"x": 2}, reason="no")
    entradas = log.tail()
    assert [e.tool for e in entradas] == ["b", "a"]
    assert entradas[0].reason == "no"
    log.close()


def test_la_auditoria_enmascara_secretos_y_no_los_escribe(tmp_path):
    log = AuditLog(str(tmp_path / "audit.db"))
    log.record(
        tool="send",
        decision="allow",
        arguments={"api_key": "nvapi-supersecreto", "AUTH_TOKEN": "abc", "user": "carlos"},
    )
    preview = log.tail()[0].args_preview
    assert "supersecreto" not in preview
    assert "abc" not in preview
    assert "carlos" in preview
    log.close()


def test_el_digest_ignora_el_orden_de_las_claves(tmp_path):
    assert digest_arguments({"a": 1, "b": 2}) == digest_arguments({"b": 2, "a": 1})
    assert digest_arguments({"a": 1}) != digest_arguments({"a": 2})


def test_los_valores_largos_se_recortan_en_la_vista_previa(tmp_path):
    log = AuditLog(str(tmp_path / "audit.db"))
    log.record(tool="t", decision="allow", arguments={"blob": "x" * 5000})
    assert len(log.tail()[0].args_preview) <= 2000
    log.close()


def test_la_auditoria_no_expone_forma_de_borrar():
    """Append-only: si aparece un `delete`, esta garantía deja de ser cierta."""
    assert not hasattr(AuditLog, "delete")
    assert not hasattr(AuditLog, "update")


# --- ShellTool sobre el motor -------------------------------------------


async def test_shell_expone_el_ejecutable_para_las_reglas():
    tool = ShellTool(allowlist=["ls"])
    assert tool.policy_subject({"command": "git status --short"})["executable"] == "git"


async def test_shell_tolera_argumentos_con_operadores():
    """El filtro de caracteres rechazaba URLs con `&`; `exec` los hace inofensivos."""
    tool = ShellTool(allowlist=["echo"])
    result = await tool.run(command="echo 'a=1&b=2'")
    assert not result.is_error
    assert "a=1&b=2" in result.content


async def test_shell_sin_motor_sigue_rechazando_lo_no_listado():
    tool = ShellTool(allowlist=["ls"])
    result = await tool.run(command="curl https://example.com")
    assert result.is_error
    assert "lista blanca" in result.content


async def test_shell_con_motor_delega_la_decision():
    tool = ShellTool(allowlist=["ls"], governed_by_policy=True)
    result = await tool.run(command="echo hola")
    assert not result.is_error


async def test_las_reglas_por_defecto_cubren_la_lista_blanca():
    tool = ShellTool(allowlist=["ls", "cat"], governed_by_policy=True)
    engine = PolicyEngine(tool.default_rules())
    assert engine.evaluate("run_shell", {"executable": "ls"}, baseline=Decision.ASK).allowed
    assert engine.evaluate("run_shell", {"executable": "nc"}, baseline=Decision.ASK).decision is Decision.ASK


# --- integración con el orquestador -------------------------------------


class _FakeLLM:
    """Pide una herramienta en el primer turno y responde en el segundo."""

    def __init__(self, tool_name: str, arguments: dict):
        self._tool_name = tool_name
        self._arguments = arguments
        self.calls = 0

    async def complete(self, system, history, tools, on_text_delta=None, system_overlay=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id="1", name=self._tool_name, input=self._arguments)],
                stop_reason="tool_use",
                provider="fake",
                assistant_content=None,
            )
        return LLMResponse(
            text="listo", tool_calls=[], stop_reason="end_turn",
            provider="fake", assistant_content=None,
        )


class _SpyTool(Tool):
    name = "spy"
    description = "herramienta de prueba"
    requires_confirmation = True
    input_schema = {"type": "object", "properties": {}, "additionalProperties": True}

    def __init__(self):
        self.ran = False

    async def run(self, **kwargs):
        self.ran = True
        return ToolResult(content="hecho")


def _orchestrator(tool, llm, policy=None, audit=None, confirm=None):
    registry = ToolRegistry()
    registry.register(tool)
    return Orchestrator(
        llm=llm, registry=registry, settings=Settings(),
        confirm=confirm, policy=policy, audit=audit, session_id="s1",
    )


async def test_una_regla_allow_evita_preguntar_al_humano():
    """Sin regla, `requires_confirmation=True` obligaría a confirmar."""
    tool = _SpyTool()
    policy = PolicyEngine([Rule(tool="spy", decision=Decision.ALLOW)])
    preguntado = False

    async def confirm(name, args):
        nonlocal preguntado
        preguntado = True
        return True

    agent = _orchestrator(tool, _FakeLLM("spy", {}), policy=policy, confirm=confirm)
    await agent.send("haz algo")
    assert tool.ran
    assert not preguntado


async def test_una_denegacion_impide_ejecutar_aunque_el_canal_apruebe():
    tool = _SpyTool()
    policy = PolicyEngine([Rule(tool="spy", decision=Decision.DENY, reason="nunca")])

    async def confirm(name, args):
        return True

    agent = _orchestrator(tool, _FakeLLM("spy", {}), policy=policy, confirm=confirm)
    await agent.send("haz algo")
    assert not tool.ran


async def test_sin_motor_el_comportamiento_anterior_se_conserva():
    """Fail-closed: herramienta sensible + canal sin política de confirmación."""
    tool = _SpyTool()
    agent = _orchestrator(tool, _FakeLLM("spy", {}), policy=None, confirm=None)
    await agent.send("haz algo")
    assert not tool.ran


async def test_la_ejecucion_queda_registrada_en_la_auditoria(tmp_path):
    tool = _SpyTool()
    audit = AuditLog(str(tmp_path / "audit.db"))
    policy = PolicyEngine([Rule(tool="spy", decision=Decision.ALLOW)])
    agent = _orchestrator(tool, _FakeLLM("spy", {"x": 1}), policy=policy, audit=audit)
    await agent.send("haz algo")
    decisiones = [e.decision for e in audit.tail()]
    assert "allow" in decisiones
    assert "executed" in decisiones
    audit.close()


async def test_una_denegacion_tambien_queda_registrada(tmp_path):
    tool = _SpyTool()
    audit = AuditLog(str(tmp_path / "audit.db"))
    policy = PolicyEngine([Rule(tool="spy", decision=Decision.DENY, reason="prohibido")])
    agent = _orchestrator(tool, _FakeLLM("spy", {}), policy=policy, audit=audit)
    await agent.send("haz algo")
    entrada = audit.tail()[0]
    assert entrada.decision == "deny"
    assert entrada.reason == "prohibido"
    assert entrada.outcome == "blocked"
    audit.close()


async def test_un_fallo_de_auditoria_no_tumba_el_turno(tmp_path):
    """Perder una línea de bitácora es malo; perder la conversación es peor."""
    tool = _SpyTool()
    audit = AuditLog(str(tmp_path / "audit.db"))
    audit.close()  # escribir sobre una conexión cerrada lanza.
    policy = PolicyEngine([Rule(tool="spy", decision=Decision.ALLOW)])
    agent = _orchestrator(tool, _FakeLLM("spy", {}), policy=policy, audit=audit)
    reply = await agent.send("haz algo")
    assert reply.text == "listo"
    assert tool.ran


# --- concesiones de sesión desde una aprobación --------------------------


async def test_una_concesion_cubre_el_ejecutable_no_la_linea_exacta():
    """Conceder `git status` debe valer para `git log`, o la concesión es inútil."""
    tool = ShellTool(allowlist=["ls"], governed_by_policy=True)
    policy = PolicyEngine()
    agent = _orchestrator(tool, _FakeLLM("run_shell", {}), policy=policy)

    etiqueta = agent.grant_for_session("run_shell", {"command": "git status --short"})
    assert etiqueta == "run_shell (executable=git)"

    verdict = policy.evaluate(
        "run_shell",
        tool.policy_subject({"command": "git log --oneline"}),
        baseline=Decision.ASK,
        session="s1",
    )
    assert verdict.allowed


async def test_una_concesion_no_se_extiende_a_otro_ejecutable():
    tool = ShellTool(allowlist=["ls"], governed_by_policy=True)
    policy = PolicyEngine()
    agent = _orchestrator(tool, _FakeLLM("run_shell", {}), policy=policy)
    agent.grant_for_session("run_shell", {"command": "git status"})

    verdict = policy.evaluate(
        "run_shell",
        tool.policy_subject({"command": "curl https://example.com"}),
        baseline=Decision.ASK,
        session="s1",
    )
    assert not verdict.allowed


async def test_una_concesion_nunca_levanta_una_denegacion_critica():
    """El botón del HUD no puede abrir `dd` por mucho que el usuario lo pulse."""
    tool = ShellTool(allowlist=["ls"], governed_by_policy=True)
    policy = PolicyEngine()
    agent = _orchestrator(tool, _FakeLLM("run_shell", {}), policy=policy)
    agent.grant_for_session("run_shell", {"command": "dd if=/dev/zero of=/dev/sda"})

    verdict = policy.evaluate(
        "run_shell",
        tool.policy_subject({"command": "dd if=/dev/zero of=/dev/sda"}),
        baseline=Decision.ASK,
        session="s1",
    )
    assert verdict.denied


async def test_sin_ejecutable_no_se_concede_nada():
    """Un comando mal formado degradaría la concesión a `run_shell` entero."""
    tool = ShellTool(allowlist=["ls"], governed_by_policy=True)
    policy = PolicyEngine()
    agent = _orchestrator(tool, _FakeLLM("run_shell", {}), policy=policy)
    assert agent.grant_for_session("run_shell", {"command": "'sin cerrar"}) is None
    assert policy.grants_for("s1") == []


async def test_una_herramienta_sin_scope_key_se_concede_entera():
    tool = _SpyTool()
    policy = PolicyEngine()
    agent = _orchestrator(tool, _FakeLLM("spy", {}), policy=policy)
    assert agent.grant_for_session("spy", {"x": 1}) == "spy"
    assert policy.evaluate("spy", {"x": 2}, baseline=Decision.ASK, session="s1").allowed


async def test_la_concesion_queda_en_la_auditoria(tmp_path):
    tool = ShellTool(allowlist=["ls"], governed_by_policy=True)
    audit = AuditLog(str(tmp_path / "audit.db"))
    agent = _orchestrator(
        tool, _FakeLLM("run_shell", {}), policy=PolicyEngine(), audit=audit
    )
    agent.grant_for_session("run_shell", {"command": "git status"})
    entrada = audit.tail()[0]
    assert entrada.decision == "grant"
    assert "executable=git" in entrada.reason
    audit.close()


async def test_sin_motor_no_se_concede_nada():
    tool = _SpyTool()
    agent = _orchestrator(tool, _FakeLLM("spy", {}), policy=None)
    assert agent.grant_for_session("spy", {}) is None


async def test_tras_conceder_la_segunda_llamada_ya_no_pregunta():
    """El recorrido completo: primero pregunta, se concede, y deja de preguntar."""
    tool = ShellTool(allowlist=[], governed_by_policy=True)
    policy = PolicyEngine()
    preguntas = 0

    async def confirm(name, args):
        nonlocal preguntas
        preguntas += 1
        return True

    agent = _orchestrator(
        tool, _FakeLLM("run_shell", {"command": "echo uno"}), policy=policy, confirm=confirm
    )
    await agent.send("primero")
    assert preguntas == 1

    agent.grant_for_session("run_shell", {"command": "echo uno"})
    agent._llm = _FakeLLM("run_shell", {"command": "echo dos"})
    await agent.send("segundo")
    assert preguntas == 1


# --- la memoria no puede bloquear el bucle de eventos ---------------------


async def test_la_memoria_lenta_no_congela_el_bucle_de_eventos():
    """`recall_semantic` con embeddings remotos es una petición HTTP síncrona.

    Llamarla desde la corrutina bloqueaba el bucle entero: no solo retrasaba ese
    turno, congelaba el streaming, los WebSockets y el puente MQTT de todos los
    clientes mientras durase. Aquí se comprueba que otra tarea sigue avanzando
    mientras la memoria tarda.
    """
    import time as _time

    class MemoriaLenta:
        """Tarda 300 ms de forma bloqueante, como una llamada de red síncrona."""

        def recall(self, query="", limit=10):
            _time.sleep(0.3)
            return []

        def recall_semantic(self, query="", limit=10, min_similarity=0.0):
            _time.sleep(0.3)
            return []

    tool = _SpyTool()
    agent = _orchestrator(
        tool, _FakeLLM("spy", {}), policy=PolicyEngine([Rule(tool="spy", decision=Decision.ALLOW)])
    )
    agent._memory = MemoriaLenta()

    latidos = 0

    async def corazon():
        nonlocal latidos
        for _ in range(40):
            await asyncio.sleep(0.01)
            latidos += 1

    tarea = asyncio.create_task(corazon())
    await agent.send("hola")
    tarea.cancel()

    # Con la memoria dentro del bucle, `latidos` se quedaba en 0 o 1: nada más
    # podía correr. Fuera del bucle, el latido sigue su ritmo.
    assert latidos > 5, f"el bucle estuvo bloqueado: solo {latidos} latidos"
