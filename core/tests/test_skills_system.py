"""Pruebas unitarias para el Sistema de Habilidades (Skills) y Auto-Aprendizaje en JARVIS OS."""

import pytest
from jarvis_core.skills.learning_engine import SelfLearningEngine
from jarvis_core.skills.manager import SkillManager
from jarvis_core.skills.store import SkillRecord, SkillStore


def test_skills_store_crud():
    store = SkillStore(":memory:")
    assert len(store.list_all()) == 0

    record = SkillRecord(
        name="format_json",
        title="Formatear JSON de Servidores",
        trigger="formatear json",
        description="Aplica sangría de 2 espacios a JSONs",
        skill_type="instruction",
        content="Instrucciones para formatear JSON.",
    )
    store.save(record)

    all_skills = store.list_all()
    assert len(all_skills) == 1
    assert all_skills[0].name == "format_json"
    assert all_skills[0].trigger == "formatear json"

    store.increment_usage("format_json", success=True)
    updated = store.get("format_json")
    assert updated is not None
    assert updated.usage_count == 1

    store.set_enabled("format_json", False)
    disabled_rec = store.get("format_json")
    assert disabled_rec is not None
    assert disabled_rec.enabled is False

    deleted = store.delete("format_json")
    assert deleted is True
    assert len(store.list_all()) == 0


@pytest.mark.asyncio
async def test_skill_manager_and_learning_engine():
    store = SkillStore(":memory:")
    manager = SkillManager(store)
    engine = SelfLearningEngine(store, manager)

    learned = engine.learn_new_skill(
        name="calculate_math",
        title="Calculadora Matemática",
        trigger="calcular math",
        description="Calcula expresiones sencillas",
        content="result = 42",
        skill_type="python",
    )

    assert learned.name == "calculate_math"
    assert len(manager.get_registered_tools()) == 1

    res = await manager.execute_skill("calculate_math", params="2+2")
    assert "42" in res.content
