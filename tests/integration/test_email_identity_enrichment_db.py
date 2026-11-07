"""Real-Postgres regression for the email identity enrichment loop + backfill (bu-qeaou).

Mocked-pool unit tests (tests/jobs/test_relationship_email_identity_enrichment.py,
tests/scripts/test_backfill_email_identity_facts.py) already cover the control
flow with a fake pool. Per the repo's DB-query verification policy (a prior PR
broke main for ~8h exactly this way — relationship.facts accessed bare under a
scoped search_path), this file exercises the same code paths against a REAL
Postgres instance (testcontainers) so JSONB round-tripping, real SQL syntax,
and the actual ``relationship.entity_facts`` / ``public.entities`` / real
``relationship_assert_fact`` central-writer FK/constraint shapes are verified,
not just asserted against a mock.

Schema is the real migrated core, relationship (``relationship`` schema) and
approvals (``public``) chains, the same layout the hand-rolled fixture used to
mirror. ``propose_insight_candidate`` and
``state_set`` are stubbed — both are unrelated concerns that already fail
gracefully by design (a missing insight-candidates table is swallowed by the
job's own try/except; state_set is fire-and-forget observability).
"""

from __future__ import annotations

import shutil
import sys
import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import ModuleType
from typing import Any

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_MODULE_KEY = "butlers.jobs._roster.relationship_jobs"


def _get_rjobs() -> ModuleType:
    mod = sys.modules.get(_MODULE_KEY)
    if mod is None:
        from butlers.jobs._roster_loader import load_roster_jobs

        mod = load_roster_jobs("relationship")
    return mod


_ASSERT_FACT_PATCH = "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"


@pytest.fixture
async def identity_pool(db):
    """The real chains the code under test touches, with approval admission enabled."""
    pool = await db("core", "relationship", "approvals", schemas={"relationship": "relationship"})
    await pool.execute(
        "UPDATE public.approvals_policy "
        "SET quiet_start_hour = NULL, quiet_end_hour = NULL, timezone = 'UTC' WHERE id = 1"
    )
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true WHERE singleton"
    )
    return pool


async def _insert_event(
    pool,
    *,
    address: str,
    thread_id: str,
    day_offset: int,
    display_name: str | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO public.ingestion_events
            (id, received_at, source_channel, source_provider, source_endpoint_identity,
             source_sender_identity, source_sender_display_name, source_thread_identity,
             external_event_id, dedupe_key, dedupe_strategy, ingestion_tier, policy_tier,
             status)
        VALUES ($1, $2, 'email', 'gmail', 'gmail:user:test@example.com', $3, $4, $5, '',
                $6, 'connector_api', 'full', 'default', 'ingested')
        """,
        uuid.uuid4(),
        # The job's lookback window ends at the wall clock, so events must too.
        datetime.now(UTC) - timedelta(days=day_offset),
        address,
        display_name,
        thread_id,
        f"dedupe:{address}:{thread_id}:{day_offset}",
    )


@pytest.fixture(autouse=True)
def _register_real_approval_hooks(identity_pool):
    """Register real pool-scoped hooks against the integration database pool."""
    import butlers.core.approvals_hooks as _hooks
    from butlers.modules.approvals.email_guard import (
        check_email_recipient,
        check_recipient,
    )
    from butlers.modules.approvals.park import park_pending_action as _real_park

    runtime = _hooks.register_approval_hooks(
        identity_pool,
        email_guard=check_email_recipient,
        recipient_guard=check_recipient,
        park_pending_action=_real_park,
    )
    yield
    _hooks.unregister_approval_hooks(identity_pool, runtime)


@pytest.fixture(autouse=True)
def _patch_insight_and_state(monkeypatch: pytest.MonkeyPatch):
    async def fake_propose_insight_candidate(_pool: Any, **_kwargs: Any) -> dict[str, str]:
        return {"status": "accepted"}

    broker = import_module("butlers.tools.switchboard.insight.broker")
    monkeypatch.setattr(broker, "propose_insight_candidate", fake_propose_insight_candidate)

    async def fake_state_set(*_args: Any, **_kwargs: Any) -> None:
        return None

    rjobs = _get_rjobs()
    monkeypatch.setattr(rjobs, "state_set", fake_state_set)


class TestEmailIdentityEnrichmentJobRealDb:
    async def test_creates_entity_and_pending_action_for_new_correspondent(
        self, identity_pool
    ) -> None:
        pool = identity_pool
        for i, thread in enumerate(["t1", "t2", "t3"]):
            await _insert_event(
                pool, address="john.doe@example.com", thread_id=thread, day_offset=i
            )

        rjobs = _get_rjobs()
        result = await rjobs.run_email_identity_enrichment(pool)

        assert result["created_new"] == 1
        assert result["errors"] == 0

        entity_row = await pool.fetchrow(
            "SELECT canonical_name, metadata FROM public.entities "
            "WHERE metadata->>'proposed_from_address' = $1",
            "john.doe@example.com",
        )
        assert entity_row is not None
        assert entity_row["canonical_name"] == "John Doe"

        action_row = await pool.fetchrow(
            "SELECT tool_name, tool_args, status FROM pending_actions "
            "WHERE tool_name = 'relationship_assert_fact'"
        )
        assert action_row is not None
        assert action_row["status"] == "pending"
        tool_args = action_row["tool_args"]
        assert isinstance(tool_args, dict), (
            f"tool_args arrived as {type(tool_args).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert tool_args["predicate"] == "has-email"
        assert tool_args["object"] == "john.doe@example.com"

        # No fact was written directly — approval is still required.
        fact_count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE object = $1",
            "john.doe@example.com",
        )
        assert fact_count == 0

    async def test_prefers_stored_display_name_over_local_part(self, identity_pool) -> None:
        """The proposed entity uses the stored From: display name, not the local-part (bu-vs9cr)."""
        pool = identity_pool
        for i, thread in enumerate(["t1", "t2", "t3"]):
            await _insert_event(
                pool,
                address="hsbc.bank.singapore@example.com",
                thread_id=thread,
                day_offset=i,
                # Only the newest row carries the stored name; it must still win.
                display_name="Alice Tan" if thread == "t1" else None,
            )

        rjobs = _get_rjobs()
        result = await rjobs.run_email_identity_enrichment(pool)

        assert result["created_new"] == 1
        entity_row = await pool.fetchrow(
            "SELECT canonical_name FROM public.entities "
            "WHERE metadata->>'proposed_from_address' = $1",
            "hsbc.bank.singapore@example.com",
        )
        assert entity_row is not None
        # Real stored name, NOT the local-part guess "Hsbc Bank Singapore".
        assert entity_row["canonical_name"] == "Alice Tan"

    async def test_links_to_existing_real_entity_by_name_match(self, identity_pool) -> None:
        pool = identity_pool
        existing_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ('John Doe', 'person') RETURNING id"
        )
        for i, thread in enumerate(["t1", "t2", "t3"]):
            await _insert_event(
                pool, address="john.doe@example.com", thread_id=thread, day_offset=i
            )

        rjobs = _get_rjobs()
        result = await rjobs.run_email_identity_enrichment(pool)

        assert result["linked_existing"] == 1
        assert result["created_new"] == 0

        entity_count = await pool.fetchval(
            "SELECT COUNT(*) FROM public.entities WHERE canonical_name = 'John Doe'"
        )
        assert entity_count == 1, "must link to the existing entity, not create a duplicate"

        action_row = await pool.fetchrow(
            "SELECT tool_args FROM pending_actions WHERE tool_name = 'relationship_assert_fact'"
        )
        assert action_row["tool_args"]["subject"] == str(existing_id)

    async def test_idempotent_rerun_does_not_duplicate_pending_action(self, identity_pool) -> None:
        pool = identity_pool
        for i, thread in enumerate(["t1", "t2", "t3"]):
            await _insert_event(
                pool, address="john.doe@example.com", thread_id=thread, day_offset=i
            )

        rjobs = _get_rjobs()
        await rjobs.run_email_identity_enrichment(pool)
        second = await rjobs.run_email_identity_enrichment(pool)

        assert second["already_pending"] == 1
        assert second["created_new"] == 0

        action_count = await pool.fetchval(
            "SELECT COUNT(*) FROM pending_actions WHERE tool_name = 'relationship_assert_fact'"
        )
        assert action_count == 1


# ---------------------------------------------------------------------------
# Rejected-proposal orphan cleanup (bu-3w3tb)
# ---------------------------------------------------------------------------


async def _seed_proposed_entity(pool, *, address: str, name: str = "Proposed Person") -> uuid.UUID:
    """Mint a placeholder entity exactly as the enrichment job's create branch does."""
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
        "VALUES ($1, 'person', jsonb_build_object("
        "  'proposed_from_address', $2::text, "
        "  'proposed_source', 'email_identity_enrichment')) "
        "RETURNING id",
        name,
        address,
    )


async def _seed_enrichment_action(pool, *, entity_id: uuid.UUID, address: str, status: str) -> None:
    await pool.execute(
        "INSERT INTO pending_actions (tool_name, tool_args, status) "
        "VALUES ('relationship_assert_fact', jsonb_build_object("
        "  'subject', $1::text, 'predicate', 'has-email', 'object', $2::text), $3)",
        str(entity_id),
        address,
        status,
    )


class TestRejectedProposalOrphanCleanup:
    async def test_job_archives_orphan_from_rejected_proposal(self, identity_pool) -> None:
        """propose -> reject -> the orphaned entity is soft-deleted (archived), not
        hard-deleted, and its proposed_from_address idempotency tag survives."""
        pool = identity_pool
        addr = "rejected@example.com"
        eid = await _seed_proposed_entity(pool, address=addr)
        await _seed_enrichment_action(pool, entity_id=eid, address=addr, status="rejected")

        rjobs = _get_rjobs()
        # No ingestion events -> the job reaps orphans, finds no new senders, returns.
        result = await rjobs.run_email_identity_enrichment(pool)
        assert result["archived_orphans"] == 1
        assert result["created_new"] == 0

        row = await pool.fetchrow("SELECT metadata FROM public.entities WHERE id = $1", eid)
        assert row is not None, "entity must be soft-deleted, not hard-deleted"
        meta = row["metadata"]
        assert meta.get("deleted_at") is not None
        assert meta.get("archived_reason") == "rejected_email_identity_enrichment"
        # Idempotency guard preserved: the address is still tagged, never re-proposed.
        assert meta.get("proposed_from_address") == addr

        # Idempotent: a second reap does not touch the already-archived entity.
        again = await rjobs.archive_rejected_identity_enrichment_orphans(pool)
        assert again == 0

    async def test_reap_leaves_approved_entity_intact(self, identity_pool) -> None:
        """propose -> approve (live pending_action) -> entity is left intact."""
        pool = identity_pool
        addr = "approved@example.com"
        eid = await _seed_proposed_entity(pool, address=addr)
        await _seed_enrichment_action(pool, entity_id=eid, address=addr, status="approved")

        rjobs = _get_rjobs()
        assert await rjobs.archive_rejected_identity_enrichment_orphans(pool) == 0

        row = await pool.fetchrow("SELECT metadata FROM public.entities WHERE id = $1", eid)
        assert row["metadata"].get("deleted_at") is None

    async def test_reap_leaves_referenced_entity_intact(self, identity_pool) -> None:
        """A rejected proposal whose entity has ACQUIRED a reference (an active
        entity_fact) has graduated to a real entity and is left fully intact --
        the safety gate never archives a referenced entity."""
        pool = identity_pool
        addr = "graduated@example.com"
        eid = await _seed_proposed_entity(pool, address=addr)
        await _seed_enrichment_action(pool, entity_id=eid, address=addr, status="rejected")
        await pool.execute(
            "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
            "VALUES ($1, 'has-email', $2, 'literal', 'test')",
            eid,
            addr,
        )

        rjobs = _get_rjobs()
        assert await rjobs.archive_rejected_identity_enrichment_orphans(pool) == 0

        row = await pool.fetchrow("SELECT metadata FROM public.entities WHERE id = $1", eid)
        assert row["metadata"].get("deleted_at") is None

    async def test_reap_skips_undecided_pending_proposal(self, identity_pool) -> None:
        """A still-pending proposal is undecided -> its entity is not archived."""
        pool = identity_pool
        addr = "pending@example.com"
        eid = await _seed_proposed_entity(pool, address=addr)
        await _seed_enrichment_action(pool, entity_id=eid, address=addr, status="pending")

        rjobs = _get_rjobs()
        assert await rjobs.archive_rejected_identity_enrichment_orphans(pool) == 0

        row = await pool.fetchrow("SELECT metadata FROM public.entities WHERE id = $1", eid)
        assert row["metadata"].get("deleted_at") is None

    async def test_reap_archives_expired_proposal(self, identity_pool) -> None:
        """An auto-expired (abandoned) proposal orphan is archived, same as rejected."""
        pool = identity_pool
        addr = "expired@example.com"
        eid = await _seed_proposed_entity(pool, address=addr)
        await _seed_enrichment_action(pool, entity_id=eid, address=addr, status="expired")

        rjobs = _get_rjobs()
        assert await rjobs.archive_rejected_identity_enrichment_orphans(pool) == 1

        row = await pool.fetchrow("SELECT metadata FROM public.entities WHERE id = $1", eid)
        assert row["metadata"].get("deleted_at") is not None

    async def test_reap_ignores_entities_without_enrichment_provenance(self, identity_pool) -> None:
        """An entity NOT tagged proposed_source='email_identity_enrichment' is never
        touched, even with a rejected has-email action pointing at it."""
        pool = identity_pool
        addr = "real@example.com"
        eid = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ('Real Person', 'person') RETURNING id"
        )
        await _seed_enrichment_action(pool, entity_id=eid, address=addr, status="rejected")

        rjobs = _get_rjobs()
        assert await rjobs.archive_rejected_identity_enrichment_orphans(pool) == 0

        row = await pool.fetchrow("SELECT metadata FROM public.entities WHERE id = $1", eid)
        assert row["metadata"].get("deleted_at") is None
