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


# ── Sin fecha: el borrador que evita que se invente una ──────────────────────
#
# El caso real: «recuérdame que tengo una cita por videollamada con los que me
# van a remodelar el apartamento». Nadie dijo cuándo, y la respuesta fue «ya
# tienes la cita agendada para el lunes 24 de agosto a las 17:00». Ni el día ni
# la hora salieron del usuario.
#
# La causa de fondo era que «sin fecha» no se podía representar: sin momento
# indicado, la tarea se creaba «dentro de un minuto». Al modelo le quedaban dos
# salidas —preguntar, o inventar— y una de las dos estaba a un paso.

def test_sin_fecha_se_anota_como_borrador_en_vez_de_inventar(store):
    salida = asyncio.run(
        ScheduleTaskTool(store, UTC).run(
            title="Cita videollamada remodelación", prompt="recuérdame la cita"
        )
    )
    assert not salida.is_error
    assert "SIN FECHA" in salida.content
    # Y el resultado le dice qué hacer: preguntar.
    assert "pregúntale" in salida.content.lower()
    tarea = store.get(1)
    assert tarea.status == "draft"
    assert tarea.due_at is None


def test_un_borrador_no_avisa_nunca(store):
    """Lo peor sería que saltara: un aviso a una hora que nadie acordó."""
    asyncio.run(ScheduleTaskTool(store, UTC).run(title="Cita", prompt="recuérdame"))
    # Ni ahora ni dentro de un año.
    assert store.due(time.time() + 1) == []
    assert store.due(time.time() + 365 * 86400) == []


def test_un_borrador_se_ve_en_el_listado(store):
    """Un borrador invisible se olvida, que es lo contrario de anotarlo."""
    asyncio.run(ScheduleTaskTool(store, UTC).run(title="Cita", prompt="recuérdame"))
    assert [t.title for t in store.list()] == ["Cita"]
    salida = asyncio.run(ListTasksTool(store, UTC).run())
    assert "SIN FECHA" in salida.content
    # Y no enseña la hora de creación como si fuera la de la cita.
    assert "borrador" in salida.content.lower()


def test_el_borrador_se_convierte_en_tarea_cuando_llega_la_fecha(store):
    asyncio.run(ScheduleTaskTool(store, UTC).run(title="Cita", prompt="recuérdame"))
    cuando = time.time() + 3 * 86400
    tarea = store.reschedule(1, cuando)
    assert tarea.status == "pending"
    assert abs(tarea.next_run - cuando) < 1
    assert store.due(cuando + 1)


def test_un_borrador_no_se_promueve_sin_una_fecha_de_verdad(store):
    """Heredar la suya —la de creación— lo pondría a dispararse de inmediato."""
    asyncio.run(ScheduleTaskTool(store, UTC).run(title="Cita", prompt="recuérdame"))
    assert store.reschedule(1) is None
    assert store.get(1).status == "draft"
    assert store.due(time.time() + 1) == []


def test_con_fecha_no_hay_borrador(store):
    """Lo que sí se sabe se programa: el borrador es solo para lo que falta."""
    asyncio.run(
        ScheduleTaskTool(store, UTC).run(
            title="Cita", prompt="recuérdame", at=iso(3 * 86400)
        )
    )
    assert store.get(1).status == "pending"


def test_un_plazo_tambien_basta_para_no_ser_borrador(store):
    asyncio.run(
        ScheduleTaskTool(store, UTC).run(
            title="Informe", prompt="avisa", due_at=iso(5 * 86400)
        )
    )
    assert store.get(1).status == "pending"


def test_la_herramienta_dice_en_su_descripcion_que_no_invente():
    """La regla vive donde el modelo la lee justo antes de decidir."""
    descripcion = ScheduleTaskTool.description
    assert "NUNCA te inventes la fecha" in descripcion


def test_el_prompt_del_sistema_prohibe_inventar_datos():
    from jarvis_core.config import Settings

    assert "NUNCA inventes fechas" in Settings().system_prompt()


# ── El día sin la hora ───────────────────────────────────────────────────────
#
# Segundo caso real: «recuérdame hoy ir a buscar la medicina del niño». Aquí el
# usuario SÍ dijo el día. La respuesta fue «te lo he agendado para hoy a las
# 17:00»: la hora se la inventó —y son las mismas 17:00 del caso anterior—.
#
# La causa vuelve a ser que la honestidad no cabía: pasar solo la fecha da
# medianoche, que para «hoy» ya pasó, y la herramienta lo rechazaba por estar en
# el pasado. La única llamada que funcionaba era una con hora inventada.

def test_una_fecha_sin_hora_se_reconoce_como_tal():
    from jarvis_core.tools.builtin.task_tools import is_date_only

    assert is_date_only("2026-08-23") is True
    assert is_date_only("2026-08-23T17:00:00") is False
    assert is_date_only("") is False
    assert is_date_only(None) is False


def test_hoy_sin_hora_no_se_va_al_pasado(store):
    """Medianoche de hoy ya pasó: el aviso tiene que caer por delante."""
    import datetime as dt

    hoy = dt.date.today().isoformat()
    salida = asyncio.run(
        ScheduleTaskTool(store, UTC).run(
            title="Medicina del niño", prompt="ir a buscarla", at=hoy
        )
    )
    assert not salida.is_error, salida.content
    assert store.get(1).next_run > time.time()


def test_un_dia_futuro_sin_hora_usa_la_hora_por_defecto(store):
    import datetime as dt

    manana = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    asyncio.run(
        ScheduleTaskTool(store, UTC, default_hour=9).run(
            title="Medicina", prompt="busca", at=manana
        )
    )
    momento = dt.datetime.fromtimestamp(store.get(1).next_run, tz=UTC)
    assert momento.hour == 9 and momento.minute == 0


def test_la_imprecision_se_guarda_y_se_dice(store):
    """Sin esto, una hora elegida a dedo se lee igual que una acordada."""
    import datetime as dt

    salida = asyncio.run(
        ScheduleTaskTool(store, UTC).run(
            title="Medicina", prompt="busca",
            at=(dt.date.today() + dt.timedelta(days=1)).isoformat(),
        )
    )
    assert store.get(1).time_precision == "day"
    # Y el resultado le dice al modelo que la hora es suya, no del usuario.
    assert "NO la hora" in salida.content
    assert "elegido yo" in salida.content


def test_con_hora_explicita_no_hay_imprecision(store):
    asyncio.run(
        ScheduleTaskTool(store, UTC).run(title="Cita", prompt="avisa", at=iso(3 * 86400))
    )
    tarea = store.get(1)
    assert tarea.time_precision == "exact"
    assert "elegido yo" not in asyncio.run(ListTasksTool(store, UTC).run()).content


def test_dar_la_hora_despues_deja_de_ser_aproximada(store):
    import datetime as dt

    asyncio.run(
        ScheduleTaskTool(store, UTC).run(
            title="Medicina", prompt="busca",
            at=(dt.date.today() + dt.timedelta(days=1)).isoformat(),
        )
    )
    assert store.reschedule(1, time.time() + 7200).time_precision == "exact"


def test_el_listado_avisa_de_que_la_hora_la_puso_el_sistema(store):
    import datetime as dt

    asyncio.run(
        ScheduleTaskTool(store, UTC).run(
            title="Medicina", prompt="busca",
            at=(dt.date.today() + dt.timedelta(days=1)).isoformat(),
        )
    )
    assert "hora puesta por el sistema" in asyncio.run(ListTasksTool(store, UTC).run()).content


def test_la_hora_por_defecto_es_configurable(store):
    import datetime as dt

    manana = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    asyncio.run(
        ScheduleTaskTool(store, UTC, default_hour=20).run(
            title="Medicina", prompt="busca", at=manana
        )
    )
    assert dt.datetime.fromtimestamp(store.get(1).next_run, tz=UTC).hour == 20


def test_el_prompt_prohibe_los_consejos_con_datos_supuestos():
    """«Pásate por la farmacia que te pilla de camino» presupone lo que no sabe.

    Un añadido inventado hace dudar de todo lo demás, aunque sea correcto.
    """
    from jarvis_core.config import Settings

    prompt = Settings().system_prompt()
    assert "farmacia" in prompt
    assert "no añadas recomendaciones" in prompt.lower()
