"""Pruebas del registro auditable de automatizaciones proactivas."""

from jarvis_core.connectors.events import ProactiveEventStore


def test_proactive_event_lifecycle_is_persistent(tmp_path):
    path = str(tmp_path / "events.db")
    store = ProactiveEventStore(path)
    event_id = store.create(
        connector="home",
        event_type="alarm.triggered",
        title="Alarma",
        text="Movimiento detectado.",
        severity="critical",
        policy="request_action",
        status="pending_approval",
        action="homeassistant.service",
        payload={"domain": "light", "service": "turn_on"},
    )
    assert store.get(event_id)["status"] == "pending_approval"
    assert store.transition(
        event_id, from_status="pending_approval", to_status="denied", result="No"
    )["status"] == "denied"
    store.close()

    reopened = ProactiveEventStore(path)
    try:
        assert reopened.list_recent()[0]["result"] == "No"
    finally:
        reopened.close()
