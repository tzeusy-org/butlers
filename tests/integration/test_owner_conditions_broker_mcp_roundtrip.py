"""Real-Postgres round-trip for the owner-conditions broker MCP tools (bu-vdv7j).

REQ-owner-condition-ledger-005 defines ``resolve_owner_condition`` as the MCP
doorway an LLM-driven butler session uses to close one known owner condition
without producing a complete snapshot. ``tests/modules/
test_module_owner_conditions_broker.py`` pins the envelope shapes against a
mocked ledger; this file drives the registered tool functions against the real
``public.owner_conditions`` table so the round-trip
(``reconcile_owner_condition`` opens -> ``resolve_owner_condition`` closes ->
row state and metadata are as promised) is proven end to end, including the
"no database write" half of the invalid-reason scenario, which a mocked pool
can only approximate.
"""

from __future__ import annotations

import json
import shutil
from typing import Any
from unittest.mock import MagicMock

import asyncpg
import pytest

from butlers.core.owner_conditions import compute_fingerprint
from butlers.core.tool_call_capture import (
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

SESSION_ID = "9f1d0c2e-0000-4000-8000-000000000abc"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=2, max_size=5)
    yield p
    await p.close()


@pytest.fixture
async def tools(pool: asyncpg.Pool) -> dict[str, Any]:
    """Register the broker's MCP tools against the real pool and return them."""
    import sys

    from butlers.modules.registry import default_registry

    default_registry()
    module_cls = sys.modules[
        "butlers.modules._roster_switchboard.owner_conditions_broker"
    ].OwnerConditionsBrokerModule
    module = module_cls()

    registered: dict[str, Any] = {}
    mcp = MagicMock()

    def tool_decorator(*_args, **kwargs):
        def decorator(fn):
            registered[kwargs.get("name") or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    db = MagicMock()
    db.pool = pool
    await module.register_tools(mcp=mcp, config={}, db=db, butler_name="switchboard")
    return registered


def _metadata(row: asyncpg.Record) -> dict[str, Any]:
    raw = row["metadata"]
    return json.loads(raw) if isinstance(raw, str) else raw


async def _open_condition(
    tools: dict[str, Any], *, source: str, fingerprint: str, metadata: dict[str, Any]
) -> dict[str, Any]:
    return await tools["reconcile_owner_condition"](
        source=source,
        observations=[
            {"fingerprint": fingerprint, "summary": "standing concern", "metadata": metadata}
        ],
        snapshot_complete=False,
        initial_grace_seconds=3600,
    )


class TestResolveOwnerConditionMcpRoundTrip:
    async def test_req_owner_condition_ledger_005_reconcile_then_resolve_round_trip(
        self, tools: dict[str, Any], pool: asyncpg.Pool
    ) -> None:
        """AC6/REQ-005: open via the MCP tool, close via the MCP tool, verify the row."""
        source = "finance:bill-overdue"
        fingerprint = compute_fingerprint(source, 1, {"bill_id": "roundtrip-utility"})
        creation_metadata = {
            "class": "commitment",
            "counterparty_entity_id": "entity-round-trip",
            "evidence_opened": {"source": "conversation", "session_id": "open-session"},
        }

        opened = await _open_condition(
            tools, source=source, fingerprint=fingerprint, metadata=creation_metadata
        )
        assert opened["status"] == "accepted"
        assert opened["transitions"][0]["transition"] == "opened"
        assert opened["transitions"][0]["state"] == "open"

        token = set_current_runtime_session_id(SESSION_ID)
        try:
            resolved = await tools["resolve_owner_condition"](
                source=source,
                fingerprint=fingerprint,
                resolution_reason="satisfied",
                resolution_detail="owner confirmed the payment cleared",
            )
        finally:
            reset_current_runtime_session_id(token)

        assert resolved == {
            "status": "resolved",
            "episode": 1,
            "fingerprint": fingerprint,
            "resolution_reason": "satisfied",
        }

        row = await pool.fetchrow(
            "SELECT state, resolved_at, recovered_after_s, metadata FROM public.owner_conditions "
            "WHERE source = $1 AND fingerprint = $2",
            source,
            fingerprint,
        )
        assert row is not None
        assert row["state"] == "resolved"
        assert row["resolved_at"] is not None
        assert row["recovered_after_s"] is not None
        assert _metadata(row) == {
            **creation_metadata,
            "resolution_reason": "satisfied",
            "evidence_closed": {
                "source": "owner_confirmed",
                "detail": "owner confirmed the payment cleared",
                "session_id": SESSION_ID,
            },
        }

    async def test_req_owner_condition_ledger_005_second_resolution_is_not_found(
        self, tools: dict[str, Any]
    ) -> None:
        """REQ-005: an already-resolved identity answers not_found, not an error."""
        source = "finance:bill-overdue"
        fingerprint = compute_fingerprint(source, 1, {"bill_id": "double-resolve"})
        await _open_condition(tools, source=source, fingerprint=fingerprint, metadata={})

        first = await tools["resolve_owner_condition"](
            source=source, fingerprint=fingerprint, resolution_reason="cancelled"
        )
        assert first["status"] == "resolved"

        second = await tools["resolve_owner_condition"](
            source=source, fingerprint=fingerprint, resolution_reason="cancelled"
        )
        assert second == {"status": "not_found"}

    async def test_req_owner_condition_ledger_005_never_observed_identity_is_not_found(
        self, tools: dict[str, Any], pool: asyncpg.Pool
    ) -> None:
        """REQ-005: a fingerprint that was never observed answers not_found and writes nothing."""
        source = "finance:bill-overdue"
        fingerprint = compute_fingerprint(source, 1, {"bill_id": "never-observed"})

        before = await pool.fetchval("SELECT count(*) FROM public.owner_conditions")
        result = await tools["resolve_owner_condition"](
            source=source, fingerprint=fingerprint, resolution_reason="expired"
        )
        assert result == {"status": "not_found"}
        assert await pool.fetchval("SELECT count(*) FROM public.owner_conditions") == before

    async def test_req_owner_condition_ledger_005_invalid_reason_leaves_the_row_untouched(
        self, tools: dict[str, Any], pool: asyncpg.Pool
    ) -> None:
        """REQ-005: an unknown reason is an error AND the active row is byte-identical after."""
        source = "finance:bill-overdue"
        fingerprint = compute_fingerprint(source, 1, {"bill_id": "invalid-reason"})
        await _open_condition(
            tools, source=source, fingerprint=fingerprint, metadata={"class": "commitment"}
        )

        select = (
            "SELECT to_jsonb(c) AS snapshot FROM public.owner_conditions c "
            "WHERE source = $1 AND fingerprint = $2"
        )
        before = await pool.fetchval(select, source, fingerprint)

        result = await tools["resolve_owner_condition"](
            source=source, fingerprint=fingerprint, resolution_reason="handled"
        )
        assert result["status"] == "error"
        assert "resolution_reason" in result["reason"]

        after = await pool.fetchval(select, source, fingerprint)
        assert after == before
        state = await pool.fetchval(
            "SELECT state FROM public.owner_conditions WHERE source = $1 AND fingerprint = $2",
            source,
            fingerprint,
        )
        assert state == "open"

    @pytest.mark.parametrize("reserved_key", ["resolution_reason", "evidence_closed"])
    async def test_req_owner_condition_ledger_006_reserved_key_is_rejected_without_a_write(
        self, tools: dict[str, Any], pool: asyncpg.Pool, reserved_key: str
    ) -> None:
        """A producer may not claim the keys the resolver writes (REQ-006).

        This test replaces a pin on the opposite behaviour. Creation-wins
        (REQ-004) used to apply to ``resolution_reason``/``evidence_closed``
        too, so a producer that set one at creation time permanently kept its
        own value and the resolver's closing evidence -- the ``session_id``
        provenance REQ-005 requires included -- was dropped with no error and
        nothing in the returned envelope to show it. The old test pinned that
        loss as a known consequence. Reserving the two keys at this boundary
        makes its premise unreachable: the creation call now fails instead.

        Creation-wins itself is unchanged and still pinned, at the level where
        it remains reachable -- ``tests/integration/
        test_owner_conditions_roundtrip.py::TestExplicitOwnerConditionResolution
        ::test_req_owner_condition_ledger_004_resolves_open_and_preserves_metadata``
        collides ``class``, ``confidence`` and ``identity_payload`` directly
        against the engine and asserts the creation values survive.
        """
        source = "relationship:commitment"
        fingerprint = compute_fingerprint(source, 1, {"commitment": f"reserved-{reserved_key}"})

        rejected = await _open_condition(
            tools,
            source=source,
            fingerprint=fingerprint,
            metadata={"class": "commitment", reserved_key: {"source": "producer_preset"}},
        )

        assert rejected["status"] == "error"
        assert reserved_key in rejected["reason"]
        assert fingerprint in rejected["reason"]

        # "Without a write" is the load-bearing half: a rejected snapshot must
        # leave no row behind, not a row missing one key.
        row = await pool.fetchrow(
            "SELECT id FROM public.owner_conditions WHERE source = $1 AND fingerprint = $2",
            source,
            fingerprint,
        )
        assert row is None

    async def test_req_owner_condition_ledger_006_reserves_two_names_not_a_namespace(
        self, tools: dict[str, Any], pool: asyncpg.Pool
    ) -> None:
        """The reservation is two exact key names, not a prefix or a schema.

        A guard written slightly too wide -- anything starting with
        ``resolution``, anything containing ``evidence`` -- would pass the
        rejection test above while quietly forbidding metadata a producer is
        entitled to. These near-misses are the ones that would be caught by
        such a guard, so they belong in creation metadata here.
        """
        source = "relationship:commitment"
        fingerprint = compute_fingerprint(source, 1, {"commitment": "near-miss-keys"})
        creation_metadata = {
            "class": "commitment",
            "resolution_window": "before Friday",
            "evidence_opened": {"source": "conversation", "session_id": "open-session"},
        }

        opened = await _open_condition(
            tools, source=source, fingerprint=fingerprint, metadata=creation_metadata
        )
        assert opened["status"] == "accepted"

        token = set_current_runtime_session_id(SESSION_ID)
        try:
            resolved = await tools["resolve_owner_condition"](
                source=source,
                fingerprint=fingerprint,
                resolution_reason="satisfied",
                resolution_detail="owner confirmed",
            )
        finally:
            reset_current_runtime_session_id(token)

        assert resolved["status"] == "resolved"

        row = await pool.fetchrow(
            "SELECT metadata FROM public.owner_conditions WHERE source = $1 AND fingerprint = $2",
            source,
            fingerprint,
        )
        assert row is not None
        metadata = _metadata(row)
        # Every creation-time key survives, near-misses included...
        assert {k: metadata[k] for k in creation_metadata} == creation_metadata
        # ...and the closing evidence lands, session id included.
        assert metadata["resolution_reason"] == "satisfied"
        assert metadata["evidence_closed"]["session_id"] == SESSION_ID
