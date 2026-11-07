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


async def test_dashboard_writer_does_not_block_the_decision_on_slow_writeback(monkeypatch) -> None:
    """A cold embedding-model load must not hold the HTTP decision response [bu-dhloi1.160]."""
    import asyncio

    settings = approvals._DecisionMemorySettings(
        memory_schema=None, embedding_model="m", tool_metadata={}
    )
    monkeypatch.setattr(approvals, "_decision_memory_settings_for", lambda _name: settings)
    db_mgr = SimpleNamespace(butlers_with_module=lambda module_name: ["home"])
    writer = approvals._decision_memory_writer_for(db_mgr, "home", object())
    assert writer is not None

    release = asyncio.Event()
    finished: list[str] = []

    async def _slow_terminal(self, action, outcome):
        await release.wait()
        finished.append(f"{action}:{outcome}")

    async def _slow_rule(self, rule, *, active):
        await release.wait()
        finished.append(f"{rule}:{active}")

    monkeypatch.setattr(approvals.DecisionMemoryWriter, "record_terminal_decision", _slow_terminal)
    monkeypatch.setattr(approvals.DecisionMemoryWriter, "record_standing_rule", _slow_rule)

    await asyncio.wait_for(writer.record_terminal_decision("a1", "rejected"), timeout=1)
    await asyncio.wait_for(writer.record_standing_rule("r1", active=False), timeout=1)
    assert finished == []

    release.set()
    for _ in range(50):
        if len(finished) == 2:
            break
        await asyncio.sleep(0.01)
    assert sorted(finished) == ["a1:rejected", "r1:False"]


async def test_dashboard_writer_background_failure_is_logged_not_raised(
    monkeypatch, caplog
) -> None:
    import asyncio

    settings = approvals._DecisionMemorySettings(
        memory_schema=None, embedding_model="m", tool_metadata={}
    )
    monkeypatch.setattr(approvals, "_decision_memory_settings_for", lambda _name: settings)
    db_mgr = SimpleNamespace(butlers_with_module=lambda module_name: ["home"])
    writer = approvals._decision_memory_writer_for(db_mgr, "home", object())

    async def _boom(self, action, outcome):
        raise RuntimeError("model load failed")

    monkeypatch.setattr(approvals.DecisionMemoryWriter, "record_terminal_decision", _boom)
    with caplog.at_level("WARNING"):
        await writer.record_terminal_decision("a1", "rejected")
        for _ in range(50):
            if any("background" in r.getMessage() for r in caplog.records):
                break
            await asyncio.sleep(0.01)
    assert any("background" in r.getMessage() for r in caplog.records)


async def test_detached_writebacks_run_in_creation_order_and_drain(monkeypatch) -> None:
    """Create-then-revoke must land in order even when the first write is slow."""
    import asyncio

    settings = approvals._DecisionMemorySettings(
        memory_schema=None, embedding_model="m", tool_metadata={}
    )
    monkeypatch.setattr(approvals, "_decision_memory_settings_for", lambda _name: settings)
    db_mgr = SimpleNamespace(butlers_with_module=lambda module_name: ["home"])
    writer = approvals._decision_memory_writer_for(db_mgr, "home", object())
    order: list[bool] = []

    async def _rule(self, rule, *, active):
        if active:
            await asyncio.sleep(0.05)  # cold model load on the first write only
        order.append(active)

    monkeypatch.setattr(approvals.DecisionMemoryWriter, "record_standing_rule", _rule)
    await writer.record_standing_rule("r", active=True)
    await writer.record_standing_rule("r", active=False)
    await approvals.drain_decision_writebacks()
    assert order == [True, False]
