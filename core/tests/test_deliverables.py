"""Tareas con fecha de entrega: el puente entre el gestor y el calendario.

Antes eran dos mitades que no se hablaban. Una tarea es una EJECUCIÓN
programada: su fecha dice cuándo actúa JARVIS. Un evento de calendario tiene
fechas pero no dispara nada. Un entregable —«el informe vence el viernes»— no
cabía en ninguna de las dos: programarlo hacía que el viernes JARVIS ejecutara
algo en vez de avisar de que vencía.

`due_at` es esa tercera fecha, y lo que se fija aquí es que no se confunde con
`next_run` en ninguna dirección.
"""

import asyncio
import datetime
import time

import pytest
from jarvis_core.tasks.store import TaskStore
from jarvis_core.tools.builtin.task_tools import (
    DUE_REMINDER_LEAD_SECONDS,
    CompleteTaskTool,
    ListTasksTool,
    ScheduleTaskTool,
)

UTC = datetime.timezone.utc


@pytest.fixture()
def store(tmp_path):
    return TaskStore(str(tmp_path / "tasks.db"))


def iso(offset_seconds: float) -> str:
    return datetime.datetime.fromtimestamp(time.time() + offset_seconds, tz=UTC).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


# ── El plazo y la ejecución son fechas distintas ─────────────────────────────

def test_un_plazo_programa_el_aviso_la_vispera(store):
    """«Vence el viernes» tiene que avisar el jueves, no ejecutarse el viernes."""
    tool = ScheduleTaskTool(store, UTC)
    salida = asyncio.run(
        tool.run(title="Informe", prompt="recuérdame el informe", due_at=iso(10 * 86400))
    )
    assert not salida.is_error
    tarea = store.get(1)
    assert tarea.due_at is not None
    diferencia = tarea.due_at - tarea.next_run
    assert abs(diferencia - DUE_REMINDER_LEAD_SECONDS) < 5, "el aviso no cae la víspera"


def test_si_se_dan_las_dos_fechas_se_respetan_las_dos(store):
    """Actuar el lunes y entregar el viernes es legítimo y no se pisa."""
    tool = ScheduleTaskTool(store, UTC)
    asyncio.run(
        tool.run(
            title="Informe", prompt="empieza el informe",
            at=iso(2 * 86400), due_at=iso(9 * 86400),
        )
    )
    tarea = store.get(1)
    assert tarea.due_at - tarea.next_run > 6 * 86400


def test_un_plazo_inminente_no_manda_el_aviso_al_pasado(store):
    """Con la antelación por defecto, un plazo de dentro de una hora caería ayer.

    Sin este ajuste la tarea se rechazaba entera —«la primera ejecución no puede
    estar en el pasado»— y no había forma de anotar algo que vence hoy.
    """
    tool = ScheduleTaskTool(store, UTC)
    salida = asyncio.run(
        tool.run(title="Urgente", prompt="avisa", due_at=iso(3600))
    )
    assert not salida.is_error, salida.content
    assert store.get(1).next_run > time.time()


def test_una_tarea_sin_plazo_sigue_funcionando_igual(store):
    tool = ScheduleTaskTool(store, UTC)
    asyncio.run(tool.run(title="Riego", prompt="riega", delay_seconds=3600))
    tarea = store.get(1)
    assert tarea.due_at is None
    assert not tarea.is_overdue(time.time() + 10 * 86400)


def test_una_fecha_de_entrega_absurda_se_rechaza(store):
    salida = asyncio.run(
        ScheduleTaskTool(store, UTC).run(title="X", prompt="y", due_at="el viernes que viene")
    )
    assert salida.is_error
    assert "entrega" in salida.content.lower()


# ── Vencidas ─────────────────────────────────────────────────────────────────

def test_vencida_es_plazo_pasado_y_sin_cerrar(store):
    ahora = time.time()
    tid = store.add("Informe", "avisa", ahora + 60, due_at=ahora - 3600)
    assert store.get(tid).is_overdue(ahora) is True
    # Cerrarla la saca de vencidas: entregar no es seguir debiendo.
    store.complete(tid)
    assert store.get(tid).is_overdue(ahora) is False
    assert store.overdue(ahora) == []


def test_una_tarea_sin_plazo_nunca_esta_vencida(store):
    ahora = time.time()
    tid = store.add("Riego", "riega", ahora - 99999)
    assert store.get(tid).is_overdue(ahora) is False


def test_el_aviso_de_vencimiento_se_manda_una_sola_vez(store):
    """En cada vuelta del bucle sería ruido hasta volverse invisible."""
    ahora = time.time()
    tid = store.add("Informe", "avisa", ahora + 60, due_at=ahora - 10)
    assert [t.id for t in store.overdue(ahora, only_unnotified=True)] == [tid]
    store.mark_due_notified(tid)
    assert store.overdue(ahora, only_unnotified=True) == []
    # Pero sigue vencida: lo que se calla es el aviso, no el hecho.
    assert [t.id for t in store.overdue(ahora)] == [tid]


def test_cambiar_el_plazo_rearma_el_aviso(store):
    ahora = time.time()
    tid = store.add("Informe", "avisa", ahora + 60, due_at=ahora - 10)
    store.mark_due_notified(tid)
    store.update(tid, due_at=ahora + 86400)
    assert store.get(tid).due_notified is False


def test_quitar_el_plazo_lo_quita_de_verdad(store):
    ahora = time.time()
    tid = store.add("Informe", "avisa", ahora + 60, due_at=ahora - 10)
    store.update(tid, clear_due=True)
    tarea = store.get(tid)
    assert tarea.due_at is None
    assert tarea.is_overdue(ahora) is False


# ── El calendario pregunta por la fecha correcta ─────────────────────────────

def test_el_calendario_filtra_por_entrega_y_no_por_ejecucion(store):
    """Preguntar por `next_run` mostraría los avisos en vez de los plazos."""
    ahora = time.time()
    # Avisa dentro de una hora; entrega dentro de diez días.
    store.add("Informe", "avisa", ahora + 3600, due_at=ahora + 10 * 86400)

    hoy = store.due_between(ahora, ahora + 86400)
    assert hoy == [], "el aviso de hoy no es una entrega de hoy"

    ese_dia = store.due_between(ahora + 9.5 * 86400, ahora + 10.5 * 86400)
    assert [t.title for t in ese_dia] == ["Informe"]


def test_las_tareas_sin_plazo_no_ensucian_el_calendario(store):
    ahora = time.time()
    store.add("Riego", "riega", ahora + 3600)
    assert store.due_between(ahora - 86400, ahora + 86400) == []


# ── Cerrar no es cancelar ────────────────────────────────────────────────────

def test_completar_y_cancelar_dejan_rastros_distintos(store):
    ahora = time.time()
    hecha = store.add("Entregada", "x", ahora + 60, due_at=ahora + 3600)
    abandonada = store.add("Abandonada", "x", ahora + 60, due_at=ahora + 3600)
    store.complete(hecha)
    store.cancel(abandonada)
    assert store.get(hecha).status == "done"
    assert store.get(abandonada).status == "cancelled"


def test_la_herramienta_de_cierre_avisa_si_no_existe(store):
    salida = asyncio.run(CompleteTaskTool(store, UTC).run(task_id=999))
    assert salida.is_error


def test_el_listado_pone_el_plazo_por_delante(store):
    """Entre «se ejecuta el martes» y «venció hace dos días», manda lo segundo."""
    ahora = time.time()
    store.add("Tarde", "x", ahora + 60, due_at=ahora - 2 * 86400)
    salida = asyncio.run(ListTasksTool(store, UTC).run())
    assert "VENCIDA" in salida.content


# ── El planificador persigue lo vencido ──────────────────────────────────────

def test_el_planificador_avisa_de_lo_vencido_y_no_lo_repite(store):
    from jarvis_core.tasks.scheduler import Scheduler

    ahora = time.time()
    store.add("Informe", "avisa", ahora + 3600, due_at=ahora - 60)
    avisados = []

    async def al_vencer(task):
        avisados.append(task.id)

    planificador = Scheduler(store, on_fire=lambda t: None, on_overdue=al_vencer)
    asyncio.run(planificador._chase_overdue())
    asyncio.run(planificador._chase_overdue())
    assert avisados == [1], "el aviso tiene que salir una vez, no en cada vuelta"


def test_si_el_aviso_falla_se_reintenta_en_la_vuelta_siguiente(store):
    """Marcar antes de avisar perdería el aviso justo cuando hace falta."""
    from jarvis_core.tasks.scheduler import Scheduler

    ahora = time.time()
    store.add("Informe", "avisa", ahora + 3600, due_at=ahora - 60)
    intentos = []

    async def al_vencer(task):
        intentos.append(task.id)
        if len(intentos) == 1:
            raise RuntimeError("el canal estaba caído")

    planificador = Scheduler(store, on_fire=lambda t: None, on_overdue=al_vencer)
    asyncio.run(planificador._chase_overdue())
    asyncio.run(planificador._chase_overdue())
    asyncio.run(planificador._chase_overdue())
    assert intentos == [1, 1], "tras el fallo hay que reintentar, y luego parar"


def test_sin_manejador_de_vencimientos_el_bucle_no_se_rompe(store):
    from jarvis_core.tasks.scheduler import Scheduler

    ahora = time.time()
    store.add("Informe", "avisa", ahora + 3600, due_at=ahora - 60)
    planificador = Scheduler(store, on_fire=lambda t: None)
    asyncio.run(planificador._chase_overdue())
    assert store.get(1).due_notified is False


def test_lo_cancelado_desaparece_del_calendario_pero_lo_cumplido_no(store):
    """Cancelar dice que eso ya no va a hacerse: pintarlo sería un plazo falso.

    Lo cumplido sí sigue saliendo —el HUD lo tacha—, porque un día pasado tiene
    que poder contar lo que se entregó.
    """
    ahora = time.time()
    cancelada = store.add("Cancelada", "x", ahora + 60, due_at=ahora + 3600)
    hecha = store.add("Hecha", "x", ahora + 60, due_at=ahora + 3600)
    store.cancel(cancelada)
    store.complete(hecha)
    titulos = [t.title for t in store.due_between(ahora, ahora + 86400)]
    assert titulos == ["Hecha"]
