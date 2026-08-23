"""El calendario y las tareas, vistos desde el HUD.

Lo que se fija: que un entregable aparece en el calendario SIN copiarse a su
base —la tarea sigue siendo el único sitio donde vive su plazo— y que cerrar un
entregable no es lo mismo que cancelarlo.
"""

import time

import pytest
from fastapi.testclient import TestClient
from jarvis_core.tasks.store import TaskStore
from jarvis_gateway import app as gateway_module
from jarvis_gateway import runtime

KEY = {"X-Jarvis-Key": "ci-test-key"}


@pytest.fixture(autouse=True)
def tareas(tmp_path, monkeypatch):
    """Un almacén vacío por test.

    El del gateway es un fichero compartido que sobrevive entre ejecuciones y
    que otros módulos también llenan: apoyarse en él hacía que estos tests
    pasaran sueltos y fallaran en la suite, según qué se hubiera creado antes.
    """
    store = TaskStore(str(tmp_path / "tasks.db"))
    monkeypatch.setattr(runtime.sessions, "tasks", store)
    return store


def test_un_entregable_aparece_en_el_calendario_sin_duplicarse(tareas):
    """Se refleja al vuelo, no se copia.

    Copiarlo a la base del calendario obligaría a mantener dos registros
    sincronizados, y en cuanto uno se moviera el calendario estaría mintiendo.
    """
    ahora = time.time()
    tid = tareas.add(
        "Informe trimestral", "avisa", ahora + 3600, due_at=ahora + 3 * 86400
    )
    antes = len(runtime.sessions.calendar.range(ahora - 86400, ahora + 30 * 86400))

    with TestClient(gateway_module.app) as client:
        cuerpo = client.get("/calendar/events", headers=KEY).json()

    entregas = cuerpo["deliverables"]
    assert [d["task_id"] for d in entregas] == [tid]
    assert entregas[0]["source"] == "task"
    assert entregas[0]["overdue"] is False
    # Y la base del calendario no ha crecido.
    despues = len(runtime.sessions.calendar.range(ahora - 86400, ahora + 30 * 86400))
    assert despues == antes


def test_el_calendario_marca_lo_vencido(tareas):
    ahora = time.time()
    tareas.add("Tarde", "avisa", ahora + 3600, due_at=ahora - 3600)
    with TestClient(gateway_module.app) as client:
        entregas = client.get("/calendar/events", headers=KEY).json()["deliverables"]
    assert entregas[0]["overdue"] is True


def test_una_tarea_sin_plazo_no_sale_en_el_calendario(tareas):
    tareas.add("Riego", "riega", time.time() + 3600)
    with TestClient(gateway_module.app) as client:
        cuerpo = client.get("/calendar/events", headers=KEY).json()
    assert cuerpo["deliverables"] == []


def test_el_listado_de_tareas_dice_el_plazo_y_si_vencio(tareas):
    ahora = time.time()
    tareas.add("Tarde", "avisa", ahora + 3600, due_at=ahora - 60)
    with TestClient(gateway_module.app) as client:
        tareas = client.get("/tasks", headers=KEY).json()["tasks"]
    tarea = next(t for t in tareas if t["title"] == "Tarde")
    assert tarea["due_at"] is not None
    assert tarea["overdue"] is True


def test_cerrar_un_entregable_no_es_cancelarlo(tareas):
    ahora = time.time()
    tid = tareas.add("Entregada", "x", ahora + 3600, due_at=ahora + 60)
    otra = tareas.add("Abandonada", "x", ahora + 3600, due_at=ahora + 60)
    # Las comprobaciones van dentro del bloque: al salir, el ciclo de vida de la
    # app cierra los almacenes y la conexión ya no sirve.
    with TestClient(gateway_module.app) as client:
        assert client.post(f"/tasks/{tid}/complete", headers=KEY).status_code == 200
        assert client.delete(f"/tasks/{otra}", headers=KEY).status_code == 200
        assert tareas.get(tid).status == "done"
        assert tareas.get(otra).status == "cancelled"


def test_cerrar_algo_que_no_existe_da_404():
    with TestClient(gateway_module.app) as client:
        assert client.post("/tasks/99999/complete", headers=KEY).status_code == 404


def test_cerrar_pide_llave():
    with TestClient(gateway_module.app) as client:
        assert client.post("/tasks/1/complete").status_code == 401
