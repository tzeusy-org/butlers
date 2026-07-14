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

Schema is hand-rolled (mirrors the sibling precedent in
tests/integration/test_pending_actions_writers_jsonb_roundtrip.py) rather than
running the full "core" + "relationship" Alembic chains: only the columns the
code under test actually touches are created, matching the real DDL
(core_002_identity.py's public.entities, 013_relationship_facts.py's
relationship.entity_facts, 014_predicate_registry.py's
relationship.entity_predicate_registry). ``propose_insight_candidate`` and
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

_NOW = datetime(2026, 7, 1, tzinfo=UTC)


@pytest.fixture
async def identity_pool(provisioned_postgres_pool):
    """Provision the minimal real-shaped tables the code under test touches."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name  VARCHAR NOT NULL,
                entity_type     VARCHAR NOT NULL DEFAULT 'other',
                aliases         TEXT[] NOT NULL DEFAULT '{}',
                metadata        JSONB DEFAULT '{}'::jsonb,
                roles           TEXT[] NOT NULL DEFAULT '{}',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_facts (
                id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                predicate   TEXT        NOT NULL,
                object      TEXT        NOT NULL,
                object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
                src         TEXT        NOT NULL,
                conf        FLOAT       NOT NULL DEFAULT 1.0 CHECK (conf >= 0.0 AND conf <= 1.0),
                last_seen   TIMESTAMPTZ,
                observed_at TIMESTAMPTZ,
                weight      INT,
                verified    BOOL        NOT NULL DEFAULT false,
                "primary"   BOOL,
                validity    TEXT        NOT NULL DEFAULT 'active'
                                CHECK (validity IN ('active', 'retracted', 'superseded')),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # Required by relationship_assert_fact's _insert_active_fact:
        # "INSERT ... ON CONFLICT (subject, predicate, object) WHERE validity='active'"
        # needs a matching partial unique index (mirrors rel_013's uq_ef_spo_active).
        await pool.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
                ON relationship.entity_facts (subject, predicate, object)
                WHERE validity = 'active'
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
                predicate   TEXT        NOT NULL PRIMARY KEY,
                kind        TEXT        NOT NULL CHECK (kind IN ('contact', 'relational', 'override')),
                object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
                description TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await pool.execute("""
            INSERT INTO relationship.entity_predicate_registry (predicate, kind, object_kind)
            VALUES ('has-email', 'contact', 'literal')
            ON CONFLICT (predicate) DO NOTHING
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS public.ingestion_events (
                id                       UUID PRIMARY KEY,
                received_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                source_channel           TEXT NOT NULL,
                source_provider          TEXT NOT NULL DEFAULT 'gmail',
                source_endpoint_identity TEXT NOT NULL DEFAULT 'gmail:user:test@example.com',
                source_sender_identity   TEXT,
                source_sender_display_name TEXT,
                source_thread_identity   TEXT,
                external_event_id        TEXT NOT NULL DEFAULT '',
                dedupe_key               TEXT NOT NULL UNIQUE,
                dedupe_strategy          TEXT NOT NULL DEFAULT 'connector_api',
                ingestion_tier           TEXT NOT NULL DEFAULT 'full',
                policy_tier              TEXT NOT NULL DEFAULT 'default',
                status                   TEXT NOT NULL DEFAULT 'ingested'
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name TEXT NOT NULL,
                tool_args JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                agent_summary TEXT,
                session_id UUID,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ,
                decided_by TEXT,
                decided_at TIMESTAMPTZ,
                execution_result JSONB,
                why TEXT,
                evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                approval_rule_id UUID
            )
        """)
        yield pool


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
            (id, received_at, source_channel, source_sender_identity,
             source_sender_display_name, source_thread_identity, dedupe_key, status)
        VALUES ($1, $2, 'email', $3, $4, $5, $6, 'ingested')
        """,
        uuid.uuid4(),
        _NOW - timedelta(days=day_offset),
        address,
        display_name,
        thread_id,
        f"dedupe:{address}:{thread_id}:{day_offset}",
    )


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


class TestBackfillScriptRealDb:
    async def test_links_existing_entity_via_real_central_writer(self, identity_pool) -> None:
        import importlib.util
        from pathlib import Path

        script_path = (
            Path(__file__).resolve().parent.parent.parent
            / "scripts"
            / "backfill_email_identity_facts.py"
        )
        spec = importlib.util.spec_from_file_location(
            "backfill_email_identity_facts_db", script_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        pool = identity_pool
        existing_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ('John Doe', 'person') RETURNING id"
        )
        await _insert_event(pool, address="john.doe@example.com", thread_id="t1", day_offset=0)

        summary = await mod.backfill_email_identity_facts(
            pool, lookback_days=180, row_limit=20_000, dry_run=False
        )

        assert summary["linked"] == 1
        assert summary["errors"] == 0

        fact_row = await pool.fetchrow(
            "SELECT subject, object, validity FROM relationship.entity_facts "
            "WHERE predicate = 'has-email' AND object = $1",
            "john.doe@example.com",
        )
        assert fact_row is not None
        assert fact_row["subject"] == existing_id
        assert fact_row["validity"] == "active"

    async def test_dry_run_leaves_no_fact_behind(self, identity_pool) -> None:
        import importlib.util
        from pathlib import Path

        script_path = (
            Path(__file__).resolve().parent.parent.parent
            / "scripts"
            / "backfill_email_identity_facts.py"
        )
        spec = importlib.util.spec_from_file_location(
            "backfill_email_identity_facts_db_dryrun", script_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        pool = identity_pool
        await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ('John Doe', 'person') RETURNING id"
        )
        await _insert_event(pool, address="john.doe@example.com", thread_id="t1", day_offset=0)

        summary = await mod.backfill_email_identity_facts(
            pool, lookback_days=180, row_limit=20_000, dry_run=True
        )

        assert summary["linked"] == 1  # "would link"
        fact_count = await pool.fetchval("SELECT COUNT(*) FROM relationship.entity_facts")
        assert fact_count == 0


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
