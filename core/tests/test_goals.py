"""Pruebas del motor persistente de objetivos."""

import json

from jarvis_core.goals.store import GoalStore
from jarvis_core.tools.builtin.goal_tools import (
    CloseGoalTool,
    CreateGoalTool,
    UpdateGoalStepTool,
)


async def test_goal_requires_verified_steps_before_completion(tmp_path):
    store = GoalStore(str(tmp_path / "goals.db"))
    create = CreateGoalTool(store)
    update = UpdateGoalStepTool(store)
    close = CloseGoalTool(store)
    try:
        created = await create.run(
            title="Preparar mañana",
            description="Organizar agenda y avisos.",
            steps=[
                {"title": "Leer agenda", "verification": "Eventos recuperados"},
                {"title": "Crear resumen", "verification": "Resumen presentado"},
            ],
        )
        goal_id = json.loads(created.content)["goal_id"]
        assert (await close.run(goal_id=goal_id, status="completed")).is_error
        assert (
            await update.run(
                goal_id=goal_id, position=1, status="completed", evidence="3 eventos"
            )
        ).is_error is False
        assert (
            await update.run(
                goal_id=goal_id,
                position=2,
                status="completed",
                evidence="HUD actualizado",
            )
        ).is_error is False
        completed = await close.run(goal_id=goal_id, status="completed")
        assert json.loads(completed.content)["status"] == "completed"
    finally:
        store.close()


async def test_only_one_goal_can_be_active(tmp_path):
    store = GoalStore(str(tmp_path / "goals.db"))
    tool = CreateGoalTool(store)
    steps = [
        {"title": "Uno", "verification": "ok"},
        {"title": "Dos", "verification": "ok"},
    ]
    try:
        assert not (
            await tool.run(title="Primero", description="A", steps=steps)
        ).is_error
        assert (await tool.run(title="Segundo", description="B", steps=steps)).is_error
    finally:
        store.close()


def test_blocked_goal_can_resume_and_keeps_audit_events(tmp_path):
    store = GoalStore(str(tmp_path / "goals.db"))
    try:
        goal_id = store.create(
            "Objetivo",
            "Descripción",
            [
                {"title": "Uno", "verification": "ok"},
                {"title": "Dos", "verification": "ok"},
            ],
        )
        store.set_status(goal_id, "blocked")
        assert store.current().status == "blocked"
        assert store.resume(goal_id).status == "active"
        events = store.events(goal_id)
        assert [event["type"] for event in events] == ["created", "blocked", "resumed"]
    finally:
        store.close()
