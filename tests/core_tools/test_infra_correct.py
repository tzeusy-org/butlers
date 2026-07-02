"""Regression tests for the infra ``correct`` MCP tool's pool wiring.

Prior regression: cross-schema ``data_correction`` (and friends) always
received ``registered_butlers=None`` and never a ``target_pool``, so the
underlying handler silently ran the CURRENT butler's own DB pool against
another butler's schema instead of the target butler's pool. These tests pin
that the tool layer resolves the real butler registry and wires the target
butler's own pool through to the correction handler.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.core_tools._base import ToolContext
from butlers.core_tools._infra import register_infra_tools


class _FakeButlerConfig:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeTargetDatabase:
    """Stand-in for butlers.db.Database that records construction + connect() calls."""

    instances: list[_FakeTargetDatabase] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.pool = None
        type(self).instances.append(self)

    async def connect(self) -> SimpleNamespace:
        self.pool = SimpleNamespace(schema=self.kwargs["schema"], marker="target-pool")
        return self.pool


def _register_and_grab_correct(
    monkeypatch: pytest.MonkeyPatch,
    *,
    butler_name: str = "general",
    registered: list[str] | None = None,
    switchboard_client: Any | None = None,
    own_pool: Any | None = None,
):
    registered = registered if registered is not None else ["finance", "general"]

    monkeypatch.setattr(
        "butlers.config.list_butlers",
        lambda: [_FakeButlerConfig(name) for name in registered],
    )
    _FakeTargetDatabase.instances = []
    monkeypatch.setattr("butlers.db.Database", _FakeTargetDatabase)
    monkeypatch.setattr(
        "butlers.core_tools._infra.get_current_runtime_session_id",
        lambda: str(uuid.uuid4()),
    )

    tools: dict[str, callable] = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            tools[fn.__name__] = fn
            return fn

        return decorator

    mcp = SimpleNamespace()
    own_pool = own_pool if own_pool is not None else SimpleNamespace(marker="own-pool")
    source_db = SimpleNamespace(
        db_name="butlers",
        host="localhost",
        port=5432,
        user="butlers",
        password="butlers",
        ssl=None,
        max_pool_size=10,
    )
    ctx = ToolContext(
        daemon=SimpleNamespace(
            db=source_db,
            switchboard_client=switchboard_client,
            _started_at=0.0,
            _check_health=lambda: None,
            _modules=[],
            _module_statuses={},
            config=SimpleNamespace(name=butler_name, description="t", port=0),
        ),
        pool=own_pool,
        spawner=None,
        butler_name=butler_name,
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_infra_tools(ctx, mcp, _core_tool)
    return tools["correct"], own_pool


async def test_cross_schema_data_correction_uses_target_butlers_pool(
    monkeypatch: pytest.MonkeyPatch,
):
    """A data_correction targeting another butler's schema must use THAT
    butler's pool, not the current butler's own pool."""
    correct, own_pool = _register_and_grab_correct(monkeypatch, butler_name="general")

    fake_handler = AsyncMock(
        return_value={
            "status": "applied",
            "correction_id": "id",
            "summary": "ok",
            "original_data_snapshot": None,
            "correction_details": None,
        }
    )
    monkeypatch.setattr("butlers.core_tools._infra.handle_data_correction", fake_handler)

    result = await correct(
        correction_type="data_correction",
        target_session_id=str(uuid.uuid4()),
        description="fix it",
        target_butler="finance",
        state_key="some_key",
        corrected_value="new_value",
    )

    assert result["status"] == "applied"
    fake_handler.assert_awaited_once()
    _pool_arg, kwargs = fake_handler.await_args.args, fake_handler.await_args.kwargs

    # The correction record still goes through the current butler's own pool
    # (the positional 'pool' argument).
    assert _pool_arg[0] is own_pool

    # But the target butler's schema was correctly resolved and wired through
    # as its own pool — not the current butler's pool, and not None.
    assert kwargs["target_butler"] == "finance"
    assert kwargs["target_pool"] is not None
    assert kwargs["target_pool"] is not own_pool
    assert kwargs["target_pool"].schema == "finance"
    assert kwargs["registered_butlers"] == ["finance", "general"]

    # The pool was created scoped to the target butler's schema.
    assert len(_FakeTargetDatabase.instances) == 1
    assert _FakeTargetDatabase.instances[0].kwargs["schema"] == "finance"
    assert _FakeTargetDatabase.instances[0].kwargs["db_name"] == "butlers"


async def test_same_butler_correction_does_not_create_target_pool(
    monkeypatch: pytest.MonkeyPatch,
):
    """When target_butler equals the current butler, no extra pool is created
    and the handler falls back to the current butler's own pool."""
    correct, own_pool = _register_and_grab_correct(monkeypatch, butler_name="general")

    fake_handler = AsyncMock(
        return_value={
            "status": "applied",
            "correction_id": "id",
            "summary": "ok",
            "original_data_snapshot": None,
            "correction_details": None,
        }
    )
    monkeypatch.setattr("butlers.core_tools._infra.handle_data_correction", fake_handler)

    await correct(
        correction_type="data_correction",
        target_session_id=str(uuid.uuid4()),
        description="fix it",
        target_butler="general",
        state_key="some_key",
        corrected_value="new_value",
    )

    fake_handler.assert_awaited_once()
    kwargs = fake_handler.await_args.kwargs
    assert kwargs["target_pool"] is None
    assert _FakeTargetDatabase.instances == []


async def test_unregistered_target_butler_does_not_create_pool(
    monkeypatch: pytest.MonkeyPatch,
):
    """An unknown target_butler must not trigger a pool connection attempt;
    the handler itself is responsible for the butler_not_registered failure."""
    correct, _own_pool = _register_and_grab_correct(monkeypatch, butler_name="general")

    fake_handler = AsyncMock(
        return_value={
            "status": "failed",
            "correction_id": "id",
            "summary": "butler not registered",
            "original_data_snapshot": None,
            "correction_details": {"target_butler": "ghost"},
        }
    )
    monkeypatch.setattr("butlers.core_tools._infra.handle_data_correction", fake_handler)

    await correct(
        correction_type="data_correction",
        target_session_id=str(uuid.uuid4()),
        description="fix it",
        target_butler="ghost",
        state_key="some_key",
        corrected_value="new_value",
    )

    kwargs = fake_handler.await_args.kwargs
    assert kwargs["target_pool"] is None
    assert kwargs["registered_butlers"] == ["finance", "general"]
    assert _FakeTargetDatabase.instances == []


async def test_registered_butler_names_cached_across_calls(
    monkeypatch: pytest.MonkeyPatch,
):
    """``list_butlers()`` does synchronous disk I/O + TOML parsing; the tool
    must call it at most once per daemon lifetime, not once per correction."""
    correct, _own_pool = _register_and_grab_correct(monkeypatch, butler_name="general")

    call_count = 0

    def _counting_list_butlers():
        nonlocal call_count
        call_count += 1
        return [_FakeButlerConfig(name) for name in ("finance", "general")]

    monkeypatch.setattr("butlers.config.list_butlers", _counting_list_butlers)

    fake_handler = AsyncMock(
        return_value={
            "status": "applied",
            "correction_id": "id",
            "summary": "ok",
            "original_data_snapshot": None,
            "correction_details": None,
        }
    )
    monkeypatch.setattr("butlers.core_tools._infra.handle_data_correction", fake_handler)

    for _ in range(3):
        await correct(
            correction_type="data_correction",
            target_session_id=str(uuid.uuid4()),
            description="fix it",
            target_butler="finance",
            state_key="some_key",
            corrected_value="new_value",
        )

    assert call_count == 1


async def test_cross_schema_target_pool_is_cached_across_calls(
    monkeypatch: pytest.MonkeyPatch,
):
    """Repeated corrections against the same target butler reuse the pool
    instead of opening a new connection every time."""
    correct, _own_pool = _register_and_grab_correct(monkeypatch, butler_name="general")

    fake_handler = AsyncMock(
        return_value={
            "status": "applied",
            "correction_id": "id",
            "summary": "ok",
            "original_data_snapshot": None,
            "correction_details": None,
        }
    )
    monkeypatch.setattr("butlers.core_tools._infra.handle_data_correction", fake_handler)

    for _ in range(2):
        await correct(
            correction_type="data_correction",
            target_session_id=str(uuid.uuid4()),
            description="fix it",
            target_butler="finance",
            state_key="some_key",
            corrected_value="new_value",
        )

    assert len(_FakeTargetDatabase.instances) == 1


# ---------------------------------------------------------------------------
# misroute correction — real registered_butlers list (bu-e2vdj)
#
# Prior regression: the `correct` tool hardcoded registered_butlers=[] for
# handle_misroute specifically, so check_misroute_preconditions always
# returned butler_not_registered and misroute correction was unusable
# end-to-end, even against a correctly registered target butler.
# ---------------------------------------------------------------------------


async def test_misroute_correction_wires_real_registered_butlers(
    monkeypatch: pytest.MonkeyPatch,
):
    """A misroute correction targeting a real registered butler must receive
    the actual roster (not an empty list) so it passes butler validation."""
    switchboard_client = AsyncMock()
    correct, own_pool = _register_and_grab_correct(
        monkeypatch, butler_name="general", switchboard_client=switchboard_client
    )

    fake_handler = AsyncMock(
        return_value={
            "status": "applied",
            "correction_id": "id",
            "summary": "ok",
            "original_data_snapshot": None,
            "correction_details": None,
        }
    )
    monkeypatch.setattr("butlers.core_tools._infra.handle_misroute", fake_handler)

    result = await correct(
        correction_type="misroute",
        target_session_id=str(uuid.uuid4()),
        description="wrong butler handled this",
        correct_butler="finance",
    )

    assert result["status"] == "applied"
    fake_handler.assert_awaited_once()
    _pool_arg, kwargs = fake_handler.await_args.args, fake_handler.await_args.kwargs

    assert _pool_arg[0] is own_pool
    # This is the exact regression: registered_butlers must be the real
    # roster, never a hardcoded empty list.
    assert kwargs["registered_butlers"] == ["finance", "general"]
    assert kwargs["registered_butlers"] != []
    assert kwargs["switchboard_client"] is switchboard_client
    assert kwargs["correct_butler"] == "finance"


async def test_misroute_correction_unregistered_target_still_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """An unregistered correct_butler is still rejected — the tool layer
    passes the real roster through and lets the handler's precondition
    check (check_misroute_preconditions) reject it as butler_not_registered."""
    session_id = uuid.uuid4()

    async def _fetchrow(sql: str, *args):
        if "sessions" in sql.lower():
            return {"id": session_id, "trigger_source": "ingestion", "ingestion_event_id": "evt-1"}
        return None

    functional_own_pool = SimpleNamespace(
        fetchrow=AsyncMock(side_effect=_fetchrow),
        fetchval=AsyncMock(return_value=0),
        fetch=AsyncMock(return_value=[]),
        execute=AsyncMock(return_value=None),
    )

    correct, _own_pool = _register_and_grab_correct(
        monkeypatch,
        butler_name="general",
        switchboard_client=AsyncMock(),
        own_pool=functional_own_pool,
    )

    # Use the real handler (not a mock) so check_misroute_preconditions
    # actually runs against the wired-through registered_butlers list.
    result = await correct(
        correction_type="misroute",
        target_session_id=str(session_id),
        description="wrong butler handled this",
        correct_butler="ghost_butler",
    )

    assert result["status"] == "failed"
    assert "ghost_butler" in result["summary"]
    assert "not registered" in result["summary"].lower()
