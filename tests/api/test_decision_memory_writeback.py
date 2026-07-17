"""Dashboard decision-memory wiring tests."""

from __future__ import annotations

from types import SimpleNamespace

from butlers.api.routers import approvals
from butlers.modules.approvals.decision_memory import SchemaScopedPool
from butlers.modules.base import ToolMeta


def test_dashboard_writer_uses_own_memory_schema_and_v2_tool_metadata(monkeypatch) -> None:
    """REST decisions match the owning daemon's private store and fingerprint basis."""
    metadata = ToolMeta(arg_sensitivities={"recipient": True, "text": False})
    settings = approvals._DecisionMemorySettings(
        memory_schema="home_mem",
        embedding_model="all-MiniLM-L6-v2",
        tool_metadata={"notify": metadata},
    )
    monkeypatch.setattr(approvals, "_decision_memory_settings_for", lambda _name: settings)

    pool = object()
    db_mgr = SimpleNamespace(butlers_with_module=lambda module_name: ["home"])
    writer = approvals._decision_memory_writer_for(db_mgr, "home", pool)

    assert writer is not None
    assert writer._tool_meta_provider("notify") is metadata
    memory_pool = writer._memory_pool_provider()
    assert isinstance(memory_pool, SchemaScopedPool)
    assert memory_pool._pool is pool
    assert memory_pool._schema == "home_mem"


def test_dashboard_writer_uses_configured_embedding_model(monkeypatch) -> None:
    """REST writeback must embed through the owning memory configuration."""
    settings = approvals._DecisionMemorySettings(
        memory_schema=None,
        embedding_model="custom-decision-memory-model",
        tool_metadata={},
    )
    monkeypatch.setattr(approvals, "_decision_memory_settings_for", lambda _name: settings)

    requested_models: list[str] = []
    expected_engine = object()

    def _get_embedding_engine(model_name: str = "all-MiniLM-L6-v2"):
        requested_models.append(model_name)
        return expected_engine

    monkeypatch.setattr(
        "butlers.modules.memory.tools.get_embedding_engine",
        _get_embedding_engine,
    )
    db_mgr = SimpleNamespace(butlers_with_module=lambda module_name: ["home"])
    writer = approvals._decision_memory_writer_for(db_mgr, "home", object())

    assert writer is not None
    assert writer._embedding_engine_provider() is expected_engine
    assert requested_models == ["custom-decision-memory-model"]
