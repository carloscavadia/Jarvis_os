"""«¿Dónde estoy?» a través de Home Assistant.

La IP no vale aquí: dentro de la red el gateway solo ve 192.168.x.x, y desde
fuera devuelve la salida del operador. La app Companion de Home Assistant sí
publica GPS, y el conector ya estaba puesto.

Lo que se fija: que la procedencia baja SIEMPRE —«tu móvil hace dos minutos» y
«la dirección del .env» no son lo mismo— y que ninguna de las formas de no saber
acaba en una coordenada inventada.
"""

import asyncio
import json
import time

import pytest
from jarvis_core.location.resolver import (
    STALE_AFTER_SECONDS,
    LocationResolver,
    Place,
    extract_place,
)
from jarvis_core.tools.base import ToolResult
from jarvis_core.tools.builtin.location_tool import WhereAmITool


class FakeRecord:
    def __init__(self, name, connector_type="home_assistant", enabled=True):
        self.name = name
        self.connector_type = connector_type
        self.enabled = enabled


class FakeStore:
    def __init__(self, records):
        self._records = records

    def list(self):
        return self._records


class FakeRuntime:
    """Imita al ConnectorRuntime en lo justo: qué acciones responden y con qué."""

    def __init__(self, *, entities=None, states=None, records=None, allowed=None):
        self.store = FakeStore(records if records is not None else [FakeRecord("casa")])
        self.entities = entities or {}
        self.states = states or {}
        self.allowed = allowed if allowed is not None else {
            "homeassistant.entities", "homeassistant.state",
        }
        self.calls = []

    async def invoke(self, connector, action, payload, *, write):
        self.calls.append((connector, action, dict(payload)))
        if action not in self.allowed:
            return ToolResult(
                f"'{action}' no está permitida en '{connector}'.", is_error=True
            )
        if action == "homeassistant.entities":
            dominio = payload.get("domain", "")
            return ToolResult(json.dumps({"entities": self.entities.get(dominio, [])}))
        if action == "homeassistant.state":
            estado = self.states.get(payload.get("entity_id"))
            if estado is None:
                return ToolResult("Entidad no encontrada.", is_error=True)
            return ToolResult(json.dumps(estado))
        return ToolResult("Acción desconocida.", is_error=True)


def estado_ha(entity_id, lat, lon, *, nombre="", precision=None, cuando=None, estado="home"):
    atributos = {"friendly_name": nombre or entity_id}
    if lat is not None:
        atributos["latitude"] = lat
    if lon is not None:
        atributos["longitude"] = lon
    if precision is not None:
        atributos["gps_accuracy"] = precision
    cuerpo = {"entity_id": entity_id, "state": estado, "attributes": atributos}
    if cuando:
        cuerpo["last_updated"] = cuando
    return cuerpo


CASA = Place(lat=40.4168, lon=-3.7038, source="config", label="Casa")


# ── Leer la posición del formato de Home Assistant ───────────────────────────

def test_se_saca_la_posicion_de_los_atributos():
    lugar = extract_place(estado_ha("person.carlos", 40.42, -3.70, nombre="Carlos", precision=12))
    assert lugar.lat == 40.42 and lugar.lon == -3.70
    assert lugar.label == "Carlos"
    assert lugar.accuracy_m == 12
    assert lugar.source == "home_assistant"


def test_una_entidad_sin_coordenadas_no_es_una_posicion():
    """La mayoría de entidades de Home Assistant no tienen dónde: no valen."""
    assert extract_place(estado_ha("light.salon", None, None)) is None
    assert extract_place({"entity_id": "x"}) is None
    assert extract_place(None) is None


def test_el_cero_cero_no_es_una_posicion():
    """0,0 es el Golfo de Guinea: en la práctica es un campo sin rellenar.

    Tomarlo por bueno mandaría a JARVIS a calcular rutas desde el Atlántico sin
    que nada pareciera roto.
    """
    assert extract_place(estado_ha("device_tracker.movil", 0, 0)) is None


@pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), ("x", 3), (None, 3)])
def test_una_coordenada_imposible_se_descarta(lat, lon):
    assert extract_place(estado_ha("device_tracker.movil", lat, lon)) is None


def test_se_recoge_cuando_se_actualizo():
    lugar = extract_place(
        estado_ha("person.carlos", 40.4, -3.7, cuando="2026-08-23T10:00:00+00:00")
    )
    assert lugar.updated_at is not None


def test_una_posicion_vieja_se_marca_como_caducada():
    """Un móvil quieto deja de reportar, pero lo de ayer no es dónde estás."""
    ahora = time.time()
    fresca = Place(1.0, 2.0, "home_assistant", updated_at=ahora - 60)
    vieja = Place(1.0, 2.0, "home_assistant", updated_at=ahora - STALE_AFTER_SECONDS - 60)
    assert fresca.is_stale(ahora) is False
    assert vieja.is_stale(ahora) is True
    # Y sin marca de tiempo no se puede afirmar que esté caducada.
    assert Place(1.0, 2.0, "config").is_stale(ahora) is False


def test_la_procedencia_baja_siempre():
    cuerpo = Place(1.0, 2.0, "home_assistant", entity_id="person.carlos").as_dict()
    assert cuerpo["source"] == "home_assistant"
    assert cuerpo["entity_id"] == "person.carlos"
    assert "stale" in cuerpo


# ── La cascada ───────────────────────────────────────────────────────────────

def test_con_entidad_configurada_se_va_derecho_a_ella():
    runtime = FakeRuntime(states={"person.carlos": estado_ha("person.carlos", 40.42, -3.70)})
    resolver = LocationResolver(runtime, entity_id="person.carlos", home=CASA)
    resultado = asyncio.run(resolver.resolve())
    assert resultado.place.source == "home_assistant"
    # No se pierde tiempo listando entidades si ya sabemos cuál es.
    assert all(accion != "homeassistant.entities" for _, accion, _ in runtime.calls)


def test_sin_entidad_configurada_la_descubre():
    runtime = FakeRuntime(
        entities={"person": [{"entity_id": "person.carlos", "state": "home"}]},
        states={"person.carlos": estado_ha("person.carlos", 40.42, -3.70, nombre="Carlos")},
    )
    resultado = asyncio.run(LocationResolver(runtime).resolve())
    assert resultado.place.entity_id == "person.carlos"


def test_las_personas_van_antes_que_los_dispositivos():
    """`person` agrupa los aparatos de alguien; Home Assistant ya eligió el mejor."""
    runtime = FakeRuntime(
        entities={
            "person": [{"entity_id": "person.carlos", "state": "home"}],
            "device_tracker": [{"entity_id": "device_tracker.movil", "state": "home"}],
        },
        states={
            "person.carlos": estado_ha("person.carlos", 40.42, -3.70),
            "device_tracker.movil": estado_ha("device_tracker.movil", 1.0, 1.0),
        },
    )
    resultado = asyncio.run(LocationResolver(runtime).resolve())
    assert resultado.place.entity_id == "person.carlos"


def test_se_saltan_las_entidades_sin_datos():
    """Gastar una llamada en una entidad 'unknown' es tiempo de espera para nada."""
    runtime = FakeRuntime(
        entities={"person": [
            {"entity_id": "person.vacia", "state": "unknown"},
            {"entity_id": "person.carlos", "state": "home"},
        ]},
        states={"person.carlos": estado_ha("person.carlos", 40.42, -3.70)},
    )
    asyncio.run(LocationResolver(runtime).resolve())
    consultadas = [p.get("entity_id") for _, a, p in runtime.calls if a == "homeassistant.state"]
    assert "person.vacia" not in consultadas


def test_si_la_primera_entidad_no_trae_coordenadas_se_prueba_la_siguiente():
    runtime = FakeRuntime(
        entities={"person": [
            {"entity_id": "person.sin_gps", "state": "home"},
            {"entity_id": "person.carlos", "state": "home"},
        ]},
        states={
            "person.sin_gps": estado_ha("person.sin_gps", None, None),
            "person.carlos": estado_ha("person.carlos", 40.42, -3.70),
        },
    )
    resultado = asyncio.run(LocationResolver(runtime).resolve())
    assert resultado.place.entity_id == "person.carlos"


# ── Cuando no se sabe ────────────────────────────────────────────────────────

def test_sin_home_assistant_cae_a_la_direccion_fija_y_lo_dice():
    """El respaldo vale, pero no puede pasar por «dónde estás ahora»."""
    runtime = FakeRuntime(records=[])
    resultado = asyncio.run(LocationResolver(runtime, home=CASA).resolve())
    assert resultado.place.source == "config"
    assert "no dónde estás ahora" in resultado.note


def test_sin_nada_configurado_dice_que_falta_en_vez_de_inventar():
    runtime = FakeRuntime(records=[])
    resultado = asyncio.run(LocationResolver(runtime).resolve())
    assert resultado.place is None
    assert "CONECTORES" in resultado.note or "JARVIS_HOME_LAT" in resultado.note


def test_si_falta_la_accion_de_lectura_dice_exactamente_cual():
    """El caso frecuente y arreglable: el módulo existe pero no declara la acción.

    Un «acción no permitida» a secas obliga al usuario a adivinar; el nombre
    exacto convierte el fallo en una instrucción.
    """
    runtime = FakeRuntime(
        entities={"person": [{"entity_id": "person.carlos", "state": "home"}]},
        allowed={"homeassistant.entities"},
    )
    resultado = asyncio.run(LocationResolver(runtime).resolve())
    assert resultado.place is None
    assert "homeassistant.state" in resultado.note


def test_un_modulo_desactivado_no_cuenta():
    runtime = FakeRuntime(records=[FakeRecord("casa", enabled=False)])
    resultado = asyncio.run(LocationResolver(runtime, home=CASA).resolve())
    assert resultado.place.source == "config"


# ── La herramienta ───────────────────────────────────────────────────────────

def test_la_herramienta_devuelve_la_posicion_con_su_origen():
    runtime = FakeRuntime(states={"person.carlos": estado_ha("person.carlos", 40.42, -3.70)})
    tool = WhereAmITool(LocationResolver(runtime, entity_id="person.carlos"))
    salida = asyncio.run(tool.run())
    assert not salida.is_error
    cuerpo = json.loads(salida.content)
    assert cuerpo["source"] == "home_assistant"
    assert cuerpo["lat"] == 40.42


def test_la_herramienta_avisa_cuando_el_dato_es_de_la_configuracion():
    """Sin el aviso, JARVIS afirmaría dónde estás cuando está adivinando."""
    tool = WhereAmITool(LocationResolver(FakeRuntime(records=[]), home=CASA))
    cuerpo = json.loads(asyncio.run(tool.run()).content)
    assert cuerpo["source"] == "config"
    assert "warning" in cuerpo


def test_la_herramienta_falla_con_una_instruccion_no_con_un_error():
    tool = WhereAmITool(LocationResolver(FakeRuntime(records=[])))
    salida = asyncio.run(tool.run())
    assert salida.is_error
    assert len(salida.content) > 40


def test_sin_runtime_de_conectores_no_revienta():
    """El gateway pasa None cuando no hay conectores configurados.

    Eso no es una avería: es no tener de dónde sacar la posición, y tiene que
    caer al respaldo igual que si el módulo no existiera.
    """
    resultado = asyncio.run(LocationResolver(None, home=CASA).resolve())
    assert resultado.place.source == "config"
    assert asyncio.run(LocationResolver(None).resolve()).place is None
