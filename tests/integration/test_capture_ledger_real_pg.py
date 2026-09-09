"""Real-Postgres tests for the capture() core tool and public.captures ledger.

bu-2jtfw.9 acceptance checks:
  1. capture() with a target write that raises leaves receipt_state='held'
     with a refusal_reason and no target_row_id.
  2. Two concurrent capture() calls with the same mutation_id yield one row.
  3. A routed capture's target_row_id SELECTs in its named target table.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from butlers.config import ButlerType
from butlers.core_tools._base import ToolContext
from butlers.core_tools._capture import register_capture_tools

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _register_capture(pool, *, butler_name: str = "general"):
    registered: dict[str, callable] = {}

    def _core_tool(_group: str):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    ctx = ToolContext(
        daemon=SimpleNamespace(),
        pool=pool,
        spawner=None,
        butler_name=butler_name,
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_capture_tools(ctx, SimpleNamespace(), _core_tool)
    return registered["capture"]


@pytest.fixture
async def general_pool(postgres_container):
    """A real Postgres pool with both the core and general migration chains applied."""
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(
        postgres_container,
        chains=["core", "general"],
        schemas={"general": "general"},
        pool_schema="general",
    )
    try:
        yield pool
    finally:
        await pool.close()


async def test_routed_capture_target_row_id_selects_in_target_table(general_pool):
    """A routed capture's receipt cites a target_row_id that genuinely SELECTs."""
    capture = _register_capture(general_pool, butler_name="general")

    result = await capture(channel="telegram", content="remember to water the plants")

    assert result["receipt_state"] == "routed"
    assert result["target_schema"] == "general"
    assert result["target_table"] == "collection_items"

    row = await general_pool.fetchrow(
        "SELECT id, data FROM collection_items WHERE id = $1", uuid.UUID(result["target_row_id"])
    )
    assert row is not None
    assert row["data"]["content"] == "remember to water the plants"

    ledger_row = await general_pool.fetchrow(
        "SELECT receipt_state, target_row_id FROM public.captures WHERE capture_id = $1",
        uuid.UUID(result["capture_id"]),
    )
    assert ledger_row["receipt_state"] == "routed"
    assert str(ledger_row["target_row_id"]) == result["target_row_id"]


async def test_target_write_failure_leaves_held_with_refusal_reason(general_pool, monkeypatch):
    """A raising target write leaves receipt_state='held', no target_row_id."""
    from butlers.core_tools import _capture as capture_mod

    async def _boom(pool, *, channel, content):
        raise RuntimeError("simulated target write failure")

    monkeypatch.setattr(capture_mod, "_route_into_general", _boom)
    capture = _register_capture(general_pool, butler_name="general")

    result = await capture(channel="telegram", content="a note that will fail to route")

    assert result["receipt_state"] == "held"
    assert result["refusal_reason"] is not None
    assert "simulated target write failure" in result["refusal_reason"]

    ledger_row = await general_pool.fetchrow(
        "SELECT receipt_state, target_row_id, refusal_reason FROM public.captures"
        " WHERE capture_id = $1",
        uuid.UUID(result["capture_id"]),
    )
    assert ledger_row["receipt_state"] == "held"
    assert ledger_row["target_row_id"] is None
    assert ledger_row["refusal_reason"] is not None


async def test_concurrent_capture_same_mutation_id_yields_one_row(general_pool):
    """Two concurrent capture() calls with the same mutation_id yield one row."""
    capture = _register_capture(general_pool, butler_name="general")
    mutation_id = uuid.uuid4()

    results = await asyncio.gather(
        capture(channel="telegram", content="dup A", mutation_id=mutation_id),
        capture(channel="telegram", content="dup A", mutation_id=mutation_id),
    )

    capture_ids = {r["capture_id"] for r in results}
    assert len(capture_ids) == 1

    count = await general_pool.fetchval(
        "SELECT count(*) FROM public.captures WHERE mutation_id = $1", mutation_id
    )
    assert count == 1


async def test_held_row_survives_when_routing_never_attempted(general_pool):
    """A capture from a non-general butler stays held -- discoverable, not lost."""
    capture = _register_capture(general_pool, butler_name="switchboard")

    result = await capture(channel="telegram", content="a note from another butler")

    assert result["receipt_state"] == "held"

    row = await general_pool.fetchrow(
        "SELECT receipt_state, target_row_id FROM public.captures WHERE capture_id = $1",
        uuid.UUID(result["capture_id"]),
    )
    assert row["receipt_state"] == "held"
    assert row["target_row_id"] is None


async def test_bank_alert_is_refused_naming_finance(general_pool):
    """A bank-alert payload is refused naming Finance and its tool."""
    capture = _register_capture(general_pool, butler_name="general")

    result = await capture(
        channel="email", content="Your bank account balance is now $42.10 after a withdrawal"
    )

    assert result["receipt_state"] == "refused"
    assert "Finance" in result["refusal_reason"]
    assert "finance.record_transaction" in result["refusal_reason"]

    row = await general_pool.fetchrow(
        "SELECT receipt_state, target_row_id FROM public.captures WHERE capture_id = $1",
        uuid.UUID(result["capture_id"]),
    )
    assert row["receipt_state"] == "refused"
    assert row["target_row_id"] is None
