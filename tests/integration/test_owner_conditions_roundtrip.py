"""Real-Postgres regression: the owner_conditions ledger (bu-ep4ks.6).

owner_conditions.reconcile_snapshot/get_active_condition/list_conditions are
thin facades over the exact same butlers.core.condition_ledger engine that
tests/integration/test_infra_conditions_roundtrip.py already exercises
exhaustively (AC1-4: complete-snapshot resolution, incomplete-snapshot
non-resolution, recurrence/history, and advisory-lock concurrency). This file
does not re-derive those proofs against a second table — it instead covers
what is specific to owner_conditions: the full open -> escalate -> resolve ->
reopen lifecycle against the real public.owner_conditions table, and that it
is a genuinely separate ledger from public.infra_conditions (the same source
string in each table never contends or cross-resolves).
"""

from __future__ import annotations

import asyncio
import json
import shutil

import asyncpg
import pytest

from butlers.core.condition_ledger import resolve_condition as resolve_ledger_condition
from butlers.core.infra_conditions import Observation as InfraObservation
from butlers.core.infra_conditions import reconcile_snapshot as infra_reconcile_snapshot
from butlers.core.owner_conditions import (
    Observation,
    compute_fingerprint,
    get_active_condition,
    list_conditions,
    reconcile_snapshot,
    resolve_condition,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=2, max_size=10)
    yield p
    await p.close()


@pytest.fixture
async def codec_pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=2, max_size=10, init=register_jsonb_codec
    )
    yield p
    await p.close()


def _fp(name: str) -> str:
    return compute_fingerprint("finance:bill-overdue", 1, {"bill_id": name})


class TestOwnerConditionLifecycle:
    async def test_open_confirm_escalate_resolve_reopen(self, pool: asyncpg.Pool) -> None:
        fp = _fp("payee-utility-co")

        opened = await reconcile_snapshot(
            pool,
            source="finance:bill-overdue",
            observations=[Observation(fingerprint=fp, summary="Utility Co bill overdue")],
            snapshot_complete=True,
            initial_grace_seconds=0,
        )
        assert opened[0].transition == "opened"
        assert opened[0].episode == 1
        assert opened[0].escalation_level == "L0"

        # Confirming while still due immediately claims the L0->L1 escalation
        # (initial_grace_seconds=0 makes it due right away).
        escalated = await reconcile_snapshot(
            pool,
            source="finance:bill-overdue",
            observations=[Observation(fingerprint=fp, summary="still overdue")],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert escalated[0].transition == "escalation_due"
        assert escalated[0].escalation_level == "L1"
        assert escalated[0].state == "aging"

        active = await get_active_condition(pool, source="finance:bill-overdue", fingerprint=fp)
        assert active["summary"] == "still overdue"

        # The bill is paid: the next complete snapshot no longer observes it.
        resolved = await reconcile_snapshot(
            pool,
            source="finance:bill-overdue",
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert resolved[0].transition == "resolved"
        assert resolved[0].recovered_after_s >= 0
        assert (
            await get_active_condition(pool, source="finance:bill-overdue", fingerprint=fp) is None
        )

        # Recurrence (e.g. the same bill goes overdue again next cycle)
        # creates episode 2 and preserves episode 1's resolved history.
        reopened = await reconcile_snapshot(
            pool,
            source="finance:bill-overdue",
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert reopened[0].transition == "reopened"
        assert reopened[0].episode == 2

        total, rows = await list_conditions(pool, source="finance:bill-overdue")
        episodes = sorted((r["episode"], r["state"]) for r in rows if r["fingerprint"] == fp)
        assert episodes == [(1, "resolved"), (2, "open")]


class TestExplicitOwnerConditionResolution:
    async def test_req_owner_condition_ledger_004_resolves_open_and_preserves_metadata(
        self, pool: asyncpg.Pool
    ) -> None:
        source = "relationship:commitment"
        fp = compute_fingerprint(source, 1, {"commitment": "send-book"})
        creation_metadata = {
            "class": "commitment",
            "kind": "promise",
            "direction": "owner_to_other",
            "counterparty_entity_id": "entity-1",
            "confidence": 0.91,
            "evidence_opened": {"source": "conversation", "session_id": "open-1"},
            "identity_payload": {"version": 1, "fingerprint_basis": "send-book"},
        }
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp, metadata=creation_metadata)],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )

        transition = await resolve_condition(
            pool,
            source=source,
            fingerprint=fp,
            resolution_metadata={
                "resolution_reason": "satisfied",
                "evidence_closed": {"source": "owner_confirmed", "session_id": "close-1"},
                "class": "should-not-clobber",
                "confidence": 0.1,
                "identity_payload": {"version": 99},
            },
        )

        assert transition is not None
        assert transition.transition == "resolved"
        assert transition.state == "resolved"
        assert transition.resolved_at is not None
        assert transition.recovered_after_s is not None
        assert transition.recovered_after_s >= 0

        row = await pool.fetchrow(
            "SELECT state, resolved_at, recovered_after_s, metadata "
            "FROM public.owner_conditions WHERE source = $1 AND fingerprint = $2 AND episode = 1",
            source,
            fp,
        )
        assert row is not None
        assert row["state"] == "resolved"
        assert row["resolved_at"] == transition.resolved_at
        assert row["recovered_after_s"] == pytest.approx(transition.recovered_after_s)
        metadata = (
            json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        )
        assert metadata == {
            **creation_metadata,
            "resolution_reason": "satisfied",
            "evidence_closed": {"source": "owner_confirmed", "session_id": "close-1"},
        }
        assert await get_active_condition(pool, source=source, fingerprint=fp) is None

    async def test_req_owner_condition_ledger_004_resolves_aging_and_supports_none_metadata(
        self, pool: asyncpg.Pool
    ) -> None:
        source = "finance:commitment-aging"
        fp = compute_fingerprint(source, 1, {"commitment": "renewal"})
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp, metadata={"class": "commitment"})],
            snapshot_complete=False,
            initial_grace_seconds=0,
        )
        escalated = await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )
        assert escalated[0].state == "aging"

        transition = await resolve_condition(
            pool, source=source, fingerprint=fp, resolution_metadata=None
        )

        assert transition is not None
        assert transition.transition == "resolved"
        assert transition.state == "resolved"

    async def test_req_owner_condition_ledger_004_jsonb_codec_stores_object_metadata(
        self, codec_pool: asyncpg.Pool
    ) -> None:
        source = "relationship:commitment-codec"
        fp = compute_fingerprint(source, 1, {"commitment": "codec"})
        await reconcile_snapshot(
            codec_pool,
            source=source,
            observations=[
                Observation(
                    fingerprint=fp,
                    metadata={"class": "commitment", "identity_payload": {"version": 1}},
                )
            ],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )

        transition = await resolve_condition(
            codec_pool,
            source=source,
            fingerprint=fp,
            resolution_metadata={"resolution_reason": "satisfied"},
        )

        assert transition is not None
        stored = await codec_pool.fetchrow(
            "SELECT metadata, jsonb_typeof(metadata) AS metadata_type "
            "FROM public.owner_conditions WHERE source = $1 AND fingerprint = $2",
            source,
            fp,
        )
        assert stored is not None
        assert stored["metadata_type"] == "object"
        assert stored["metadata"] == {
            "class": "commitment",
            "identity_payload": {"version": 1},
            "resolution_reason": "satisfied",
        }

    async def test_req_owner_condition_ledger_004_missing_and_already_resolved_are_noops(
        self, pool: asyncpg.Pool
    ) -> None:
        source = "general:commitment"
        fp = compute_fingerprint(source, 1, {"commitment": "call"})

        assert await resolve_condition(pool, source=source, fingerprint=fp) is None
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.owner_conditions WHERE source = $1 AND fingerprint = $2",
                source,
                fp,
            )
            == 0
        )

        await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )
        first = await resolve_condition(pool, source=source, fingerprint=fp)
        assert first is not None
        assert await resolve_condition(pool, source=source, fingerprint=fp) is None
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.owner_conditions WHERE source = $1 AND fingerprint = $2",
                source,
                fp,
            )
            == 1
        )

    async def test_req_owner_condition_ledger_004_resolution_races_empty_complete_snapshot(
        self, pool: asyncpg.Pool
    ) -> None:
        source = "general:commitment-race"
        fp = compute_fingerprint(source, 1, {"commitment": "race"})
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )
        start = asyncio.Barrier(2)

        async def resolve_explicitly():
            await start.wait()
            return await resolve_condition(pool, source=source, fingerprint=fp)

        async def reconcile_empty_snapshot():
            await start.wait()
            return await reconcile_snapshot(
                pool,
                source=source,
                observations=[],
                snapshot_complete=True,
                initial_grace_seconds=3600,
            )

        explicit, snapshot = await asyncio.wait_for(
            asyncio.gather(resolve_explicitly(), reconcile_empty_snapshot()), timeout=10
        )
        transitions = ([explicit] if explicit is not None else []) + [
            transition for transition in snapshot if transition.fingerprint == fp
        ]
        assert len(transitions) == 1
        assert transitions[0].transition == "resolved"
        assert await get_active_condition(pool, source=source, fingerprint=fp) is None

    async def test_req_owner_condition_ledger_004_resolution_then_reobservation_opens_episode_two(
        self, pool: asyncpg.Pool
    ) -> None:
        source = "general:commitment-recurrence"
        fp = compute_fingerprint(source, 1, {"commitment": "reobserve"})
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )
        explicit = await resolve_condition(pool, source=source, fingerprint=fp)
        assert explicit is not None

        empty = await reconcile_snapshot(
            pool,
            source=source,
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert empty == []

        reopened = await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp)],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert len(reopened) == 1
        assert reopened[0].transition == "reopened"
        assert reopened[0].episode == 2

    async def test_req_owner_condition_ledger_004_generic_engine_resolves_owner_table(
        self, pool: asyncpg.Pool
    ) -> None:
        source = "custom:source"
        fp = "custom-fingerprint"
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp, metadata={"created": True})],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )

        transition = await resolve_ledger_condition(
            pool,
            table="public.owner_conditions",
            source=source,
            fingerprint=fp,
            resolution_metadata={"closed": True},
        )
        assert transition is not None
        assert transition.transition == "resolved"
        metadata = await pool.fetchval(
            "SELECT metadata FROM public.owner_conditions WHERE source = $1 AND fingerprint = $2",
            source,
            fp,
        )
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        assert metadata == {"closed": True, "created": True}


class TestOwnerConditionsIsolatedFromInfraConditions:
    async def test_same_source_string_in_each_table_does_not_cross_resolve_or_contend(
        self, pool: asyncpg.Pool
    ) -> None:
        """A pathological but possible collision: a producer named "finance"
        reconciles both an infra_conditions episode and an owner_conditions
        episode using the identical source/fingerprint strings. The two
        tables must behave as fully independent ledgers -- opening one must
        never resolve, confirm, or contend with the other."""
        shared_source = "finance"
        shared_fp = compute_fingerprint(shared_source, 1, {"probe": "cross-table-isolation"})

        results = await asyncio.gather(
            reconcile_snapshot(
                pool,
                source=shared_source,
                observations=[Observation(fingerprint=shared_fp)],
                snapshot_complete=False,
                initial_grace_seconds=3600,
            ),
            infra_reconcile_snapshot(
                pool,
                source=shared_source,
                observations=[InfraObservation(fingerprint=shared_fp)],
                snapshot_complete=False,
                initial_grace_seconds=3600,
            ),
        )
        assert [r.transition for r in results[0]] == ["opened"]
        assert [r.transition for r in results[1]] == ["opened"]

        owner_active = await get_active_condition(pool, source=shared_source, fingerprint=shared_fp)
        assert owner_active is not None

        # Resolving the owner_conditions episode must not touch the
        # infra_conditions row for the identical (source, fingerprint).
        await reconcile_snapshot(
            pool,
            source=shared_source,
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        assert await get_active_condition(pool, source=shared_source, fingerprint=shared_fp) is None

        from butlers.core.infra_conditions import get_active_condition as infra_get_active

        assert (
            await infra_get_active(pool, source=shared_source, fingerprint=shared_fp)
        ) is not None
