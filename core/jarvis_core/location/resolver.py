"""De dónde sale «dónde estoy» — y por qué no sale de la IP.

La IP no sirve aquí. Desde la propia red el gateway solo ve `192.168.x.x`, que
no geolocaliza nada; desde fuera devuelve el punto de salida del operador, que
puede estar a decenas de kilómetros. Y encima hay que mandársela a un tercero.

Lo que sí sirve, y ya está instalado, es Home Assistant: la app Companion
publica la posición del móvil en `person.*` y `device_tracker.*`, con
`latitude` y `longitude` entre los atributos. Precisión de GPS, sin que nada
salga de casa y sin depender de que el HUD esté abierto.

Se resuelve en cascada, y **siempre se dice de dónde salió**: una ubicación sin
procedencia no se puede juzgar, y aquí la diferencia entre «tu móvil hace dos
minutos» y «la dirección de casa que hay en el .env» cambia por completo lo que
uno haría con ella.

Un detalle que condiciona el diseño: la acción `homeassistant.entities` del
conector recorta los atributos —solo deja id, nombre, dominio y estado—, así que
las coordenadas NO vienen ahí. Hay que pedir cada entidad con
`homeassistant.state`, que devuelve la respuesta cruda de Home Assistant.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

#: Dominios donde vive la posición, en orden de preferencia. `person` primero
#: porque agrupa varios dispositivos de la misma persona y Home Assistant ya
#: eligió el más fiable; `device_tracker` es el aparato suelto.
LOCATION_DOMAINS = ("person", "device_tracker")

#: Cuántas entidades se consultan al descubrir. Cada una es una llamada a Home
#: Assistant; con más de esto la búsqueda tardaría más que lo que resuelve.
MAX_DISCOVERY_PROBES = 8

#: A partir de cuándo una posición deja de ser «ahora». Media hora: un móvil
#: quieto deja de reportar, así que exigir minutos daría falsos negativos, pero
#: una posición de ayer no es dónde estás.
STALE_AFTER_SECONDS = 1800.0


@dataclass
class Place:
    lat: float
    lon: float
    #: De dónde salió: 'home_assistant' o 'config'. Va siempre.
    source: str
    label: str = ""
    entity_id: str = ""
    accuracy_m: float | None = None
    updated_at: float | None = None

    def is_stale(self, now: float) -> bool:
        if self.updated_at is None:
            return False
        return (now - self.updated_at) > STALE_AFTER_SECONDS

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        ahora = time.time() if now is None else now
        salida: dict[str, Any] = {
            "lat": self.lat,
            "lon": self.lon,
            "source": self.source,
            "label": self.label,
            "stale": self.is_stale(ahora),
        }
        if self.entity_id:
            salida["entity_id"] = self.entity_id
        if self.accuracy_m is not None:
            salida["accuracy_m"] = self.accuracy_m
        if self.updated_at is not None:
            salida["updated_at"] = self.updated_at
            salida["age_seconds"] = max(0.0, ahora - self.updated_at)
        return salida


@dataclass
class Resolution:
    place: Place | None
    #: Qué habría que hacer si no se pudo, en palabras del usuario. Nunca es un
    #: volcado de error: es la instrucción que falta.
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.place is not None


def _coordenada(valor: Any, limite: float = 180.0) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if numero != numero or abs(numero) > limite:  # NaN o fuera del mundo
        return None
    return numero


def extract_place(estado: dict[str, Any], *, source: str = "home_assistant") -> Place | None:
    """Saca la posición de la respuesta de Home Assistant para una entidad.

    Función aparte y pura porque es donde está el formato ajeno: si Home
    Assistant cambia de sitio los atributos, se arregla aquí y se prueba sin red.
    """
    if not isinstance(estado, dict):
        return None
    atributos = estado.get("attributes")
    if not isinstance(atributos, dict):
        return None
    lat = _coordenada(atributos.get("latitude"), 90.0)
    lon = _coordenada(atributos.get("longitude"))
    if lat is None or lon is None:
        return None
    # Un 0,0 exacto es el Golfo de Guinea: en la práctica siempre es un campo sin
    # rellenar, y tratarlo como posición manda a JARVIS al Atlántico.
    if lat == 0 and lon == 0:
        return None

    actualizado: float | None = None
    marca = estado.get("last_updated") or estado.get("last_changed")
    if isinstance(marca, str):
        import datetime

        try:
            actualizado = datetime.datetime.fromisoformat(
                marca.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            actualizado = None

    return Place(
        lat=lat,
        lon=lon,
        source=source,
        label=str(atributos.get("friendly_name") or estado.get("state") or ""),
        entity_id=str(estado.get("entity_id", "")),
        accuracy_m=_coordenada(atributos.get("gps_accuracy")),
        updated_at=actualizado,
    )


class LocationResolver:
    """Resuelve la posición actual pasando por Home Assistant y, si no, por el .env."""

    def __init__(
        self,
        runtime: Any,
        *,
        entity_id: str = "",
        home: Place | None = None,
        connector_name: str = "",
    ) -> None:
        self.runtime = runtime
        self.entity_id = entity_id.strip()
        self.home = home
        self.connector_name = connector_name.strip()

    # ── Cascada ──────────────────────────────────────────────────────────────

    async def resolve(self) -> Resolution:
        modulo = self._home_assistant_module()
        if modulo is None:
            if self.home is not None:
                return Resolution(
                    self.home,
                    "No hay ningún módulo de Home Assistant registrado; esto es la "
                    "dirección fija de la configuración, no dónde estás ahora.",
                )
            return Resolution(
                None,
                "No sé dónde estás. Registra Home Assistant desde ⚡ CONECTORES "
                "—con la app Companion publicando tu ubicación— o pon "
                "JARVIS_HOME_LAT y JARVIS_HOME_LON en el .env.",
            )

        candidatos = [self.entity_id] if self.entity_id else await self._discover(modulo)
        problema = ""
        for entidad in candidatos[:MAX_DISCOVERY_PROBES]:
            lugar, fallo = await self._read_entity(modulo, entidad)
            if lugar is not None:
                return Resolution(lugar)
            problema = problema or fallo

        if self.home is not None:
            aviso = problema or (
                "Home Assistant no da coordenadas de ninguna persona ni dispositivo."
            )
            return Resolution(
                self.home,
                f"{aviso} Esto es la dirección fija de la configuración, no dónde estás ahora.",
            )
        return Resolution(
            None,
            problema
            or (
                "Home Assistant no expone ninguna posición. Comprueba que la app "
                "Companion tiene el permiso de ubicación y que tu entidad "
                "'person.*' o 'device_tracker.*' trae latitude y longitude."
            ),
        )

    # ── Piezas ───────────────────────────────────────────────────────────────

    def _home_assistant_module(self) -> str | None:
        # `runtime` puede no existir: el gateway lo deja en None cuando no hay
        # conectores configurados, que es un estado normal y no un error.
        if self.runtime is None:
            return None
        if self.connector_name:
            return self.connector_name
        store = getattr(self.runtime, "store", None)
        if store is None:
            return None
        try:
            modulos = store.list()
        except Exception:
            return None
        for registro in modulos:
            if getattr(registro, "connector_type", "") == "home_assistant" and getattr(
                registro, "enabled", False
            ):
                return registro.name
        return None

    async def _discover(self, modulo: str) -> list[str]:
        """Qué entidades podrían saber dónde estás.

        Se listan aquí y se leen una a una después porque el listado del conector
        recorta los atributos: las coordenadas no vienen en él.
        """
        encontradas: list[str] = []
        for dominio in LOCATION_DOMAINS:
            resultado = await self.runtime.invoke(
                modulo, "homeassistant.entities", {"domain": dominio, "limit": 50}, write=False
            )
            if resultado.is_error:
                continue
            try:
                cuerpo = json.loads(resultado.content)
            except (json.JSONDecodeError, TypeError):
                continue
            for entidad in cuerpo.get("entities", []) or []:
                if not isinstance(entidad, dict):
                    continue
                estado = str(entidad.get("state", "")).lower()
                # Una entidad sin datos no va a traer coordenadas; gastar una
                # llamada en ella es tiempo que el usuario espera para nada.
                if estado in {"unknown", "unavailable", ""}:
                    continue
                identificador = str(entidad.get("entity_id", ""))
                if identificador:
                    encontradas.append(identificador)
        return encontradas

    async def _read_entity(self, modulo: str, entidad: str) -> tuple[Place | None, str]:
        if not entidad:
            return None, ""
        resultado = await self.runtime.invoke(
            modulo, "homeassistant.state", {"entity_id": entidad}, write=False
        )
        if resultado.is_error:
            # El caso frecuente y arreglable: la acción existe pero el módulo no
            # la tiene declarada. Decir exactamente qué añadir vale más que el
            # texto del error.
            if "no permitida" in resultado.content.lower() or "declarada" in resultado.content.lower():
                return None, (
                    "Añade 'homeassistant.state' a las acciones de lectura del módulo "
                    f"'{modulo}' en ⚡ CONECTORES: sin ella puedo ver tus entidades "
                    "pero no sus coordenadas."
                )
            return None, resultado.content
        try:
            estado = json.loads(resultado.content)
        except (json.JSONDecodeError, TypeError):
            return None, ""
        return extract_place(estado), ""
