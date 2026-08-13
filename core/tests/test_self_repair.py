"""Pruebas de staging, conflictos, aprobación implícita y rollback."""

import pytest
from jarvis_core.repairs.store import RepairStore
from jarvis_core.tools.builtin.self_repair import (
    ApplySelfRepairTool,
    ProposeSelfRepairTool,
    RollbackSelfRepairTool,
)


def make_store(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return RepairStore(str(tmp_path / "repairs.db"), str(root)), root


def test_propose_apply_and_rollback_are_atomic(tmp_path):
    store, root = make_store(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")

    proposal = store.propose(
        "module.py", "value = 2\n", "Corrige el valor.", "python"
    )
    assert proposal["status"] == "validated"
    assert target.read_text() == "value = 1\n"

    applied = store.apply(proposal["id"])
    assert applied["status"] == "applied"
    assert target.read_text() == "value = 2\n"

    rolled_back = store.rollback(proposal["id"])
    assert rolled_back["status"] == "rolled_back"
    assert target.read_text() == "value = 1\n"
    store.close()


def test_conflict_and_escape_are_blocked(tmp_path):
    store, root = make_store(tmp_path)
    target = root / "config.json"
    target.write_text('{"enabled":false}', encoding="utf-8")
    proposal = store.propose(
        "config.json", '{"enabled":true}', "Activa la función.", "json"
    )
    target.write_text('{"enabled":"changed"}', encoding="utf-8")

    with pytest.raises(ValueError, match="cambió desde el diagnóstico"):
        store.apply(proposal["id"])
    with pytest.raises(ValueError, match="sale de la raíz"):
        store.inspect("../secret.env")
    (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    with pytest.raises(ValueError, match="credenciales"):
        store.inspect(".env")
    store.close()


async def test_sensitive_tools_are_marked_for_confirmation(tmp_path):
    store, root = make_store(tmp_path)
    (root / "settings.toml").write_text("enabled = false\n", encoding="utf-8")
    proposal_result = await ProposeSelfRepairTool(store).run(
        path="settings.toml",
        content="enabled = true\n",
        rationale="Activa la opción.",
        validator="toml",
    )
    assert not proposal_result.is_error
    assert ApplySelfRepairTool(store).requires_confirmation
    assert RollbackSelfRepairTool(store).requires_confirmation
    store.close()


def test_invalid_python_never_enters_staging(tmp_path):
    store, root = make_store(tmp_path)
    (root / "module.py").write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        store.propose("module.py", "def broken(:", "Prueba", "python")
    assert store.list() == []
    store.close()
