"""Agenda local: almacén y herramientas.

Separada de las tareas a propósito. Una tarea es algo que JARVIS **ejecuta** a
una hora; un evento es algo que **ocurre**. Mezclarlas obligaría a que cada cita
del dentista fuese un trabajo del scheduler.
"""

import asyncio
import datetime
import time

import pytest
from jarvis_core.calendar.store import CalendarStore
from jarvis_core.tools.base import ToolRegistry
from jarvis_core.tools.builtin.calendar_tools import register_calendar_tools

MADRID = "Europe/Madrid"


@pytest.fixture()
def agenda(tmp_path):
    return CalendarStore(str(tmp_path / "cal.db"))


@pytest.fixture()
def herramientas(agenda):
    registry = ToolRegistry()
    register_calendar_tools(registry, agenda, MADRID)
    return registry


def _epoch(iso: str) -> float:
    from zoneinfo import ZoneInfo

    return datetime.datetime.fromisoformat(iso).replace(tzinfo=ZoneInfo(MADRID)).timestamp()


def test_un_evento_sin_fin_dura_una_hora(agenda):
    evento = agenda.add("Dentista", _epoch("2026-08-25T17:30"))
    assert evento.ends_at - evento.starts_at == 3600


def test_no_se_admite_un_evento_que_acaba_antes_de_empezar(agenda):
    with pytest.raises(ValueError):
        agenda.add("Imposible", _epoch("2026-08-25T17:30"), _epoch("2026-08-25T16:00"))


def test_un_evento_sin_titulo_se_rechaza(agenda):
    with pytest.raises(ValueError):
        agenda.add("   ", time.time())


def test_la_consulta_por_rango_devuelve_lo_que_se_solapa(agenda):
    """Una reunión de 9 a 11 tiene que salir al preguntar por las 10."""
    agenda.add("Reunión", _epoch("2026-08-25T09:00"), _epoch("2026-08-25T11:00"))

    encontrados = agenda.range(_epoch("2026-08-25T10:00"), _epoch("2026-08-25T10:15"))

    assert [e.title for e in encontrados] == ["Reunión"]


def test_los_proximos_no_incluyen_lo_ya_terminado(agenda):
    ahora = time.time()
    agenda.add("Pasado", ahora - 7200, ahora - 3600)
    agenda.add("Futuro", ahora + 3600)

    assert [e.title for e in agenda.upcoming(ahora)] == ["Futuro"]


def test_mover_solo_el_inicio_no_deja_el_evento_invertido(agenda):
    evento = agenda.add("Cita", _epoch("2026-08-25T10:00"), _epoch("2026-08-25T11:00"))

    movido = agenda.update(evento.id, starts_at=_epoch("2026-08-25T18:00"))

    assert movido.ends_at >= movido.starts_at


def test_borrar_dice_si_existia(agenda):
    evento = agenda.add("Cita", time.time() + 60)
    assert agenda.delete(evento.id) is True
    assert agenda.delete(evento.id) is False


def test_la_herramienta_interpreta_la_hora_en_la_zona_del_usuario(herramientas, agenda):
    """Dentro del contenedor el reloj es UTC: «a las 17:30» sonaría a las 19:30."""
    asyncio.run(
        herramientas.execute(
            "create_event", {"title": "Dentista", "starts_at": "2026-08-25T17:30"}
        )
    )

    from zoneinfo import ZoneInfo

    guardado = agenda.upcoming(_epoch("2026-08-01T00:00"))[0]
    local = datetime.datetime.fromtimestamp(guardado.starts_at, tz=ZoneInfo(MADRID))
    assert (local.hour, local.minute) == (17, 30)


def test_la_agenda_se_lee_en_lenguaje_natural(herramientas):
    asyncio.run(
        herramientas.execute(
            "create_event",
            {
                "title": "Dentista",
                "starts_at": "2026-08-25T17:30",
                "location": "Clínica Sur",
            },
        )
    )

    resultado = asyncio.run(herramientas.execute("list_events", {}))

    assert "Dentista" in resultado.content
    assert "17:30" in resultado.content
    assert "Clínica Sur" in resultado.content
    assert "martes" in resultado.content


def test_una_agenda_vacia_lo_dice_sin_romperse(herramientas):
    resultado = asyncio.run(herramientas.execute("list_events", {}))
    assert "No hay nada" in resultado.content
    assert not resultado.is_error


def test_una_fecha_invalida_se_reporta_como_error(herramientas):
    resultado = asyncio.run(
        herramientas.execute("create_event", {"title": "X", "starts_at": "el martes"})
    )
    assert resultado.is_error


def test_borrar_una_cita_exige_confirmacion(herramientas):
    """Es irreversible y el usuario puede no tenerla anotada en otro sitio."""
    assert herramientas.get("delete_event").requires_confirmation is True
    assert herramientas.get("create_event").requires_confirmation is False


def test_la_busqueda_encuentra_por_lugar(herramientas, agenda):
    agenda.add("Revisión", time.time() + 3600, location="Clínica Sur")
    resultado = asyncio.run(herramientas.execute("list_events", {"query": "clínica"}))
    assert "Revisión" in resultado.content
