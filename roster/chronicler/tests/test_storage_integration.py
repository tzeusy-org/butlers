"""Integration tests for Chronicler storage primitives and adapters.

Uses the shared ``provisioned_postgres_pool`` fixture to run against a
real PostgreSQL instance. The Chronicler migration is applied directly
via the SQL strings baked into
``roster/chronicler/migrations/001_chronicler_tables.py``; integration
happens here rather than through an Alembic CLI run to stay aligned
with other butler migration tests (see ``tests/migrations/``).
"""

from __future__ import annotations

import importlib.util as _importlib_util
from datetime import UTC, datetime, timedelta
from pathlib import Path as _Path
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.adapters.sessions import (
    EPISODE_TYPE_WORK,
    EVENT_TYPE_SESSION_COMPLETED,
    EVENT_TYPE_SESSION_STARTED,
    CoreSessionsAdapter,
)
from butlers.chronicler.aggregations import lane_for_activity, union_seconds
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import (
    Compatibility,
    Confidence,
    Episode,
    Layer,
    LinkRelation,
    Override,
    OverrideTarget,
    PointEvent,
    SourceAdapterState,
)
from butlers.chronicler.storage import (
    get_checkpoint,
    get_checkpoint_subsource,
    get_episode,
    get_source_state,
    insert_override,
    link_event_to_episode,
    list_episode_events,
    list_episodes,
    list_overlapping_episodes,
    list_overrides_for,
    list_point_events,
    mark_source_active,
    record_idempotency,
    refresh_episode_evidence_refs,
    register_source,
    upsert_checkpoint,
    upsert_checkpoint_subsource,
    upsert_episode,
    upsert_point_event,
)

_inline_ddl_spec = _importlib_util.spec_from_file_location(
    "_inline_ddl",
    _Path(__file__).parent / "_inline_ddl.py",
)
assert _inline_ddl_spec is not None and _inline_ddl_spec.loader is not None
_inline_ddl_mod = _importlib_util.module_from_spec(_inline_ddl_spec)
_inline_ddl_spec.loader.exec_module(_inline_ddl_mod)  # type: ignore[union-attr]
make_sessions_table_ddl = _inline_ddl_mod.make_sessions_table_ddl

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]


# ── Schema bring-up fixture ────────────────────────────────────────────────


async def _apply_chronicler_schema(pool) -> None:
    """Run the Chronicler DDL against the provisioned pool.

    Keeps the integration lightweight: no Alembic bootstrap required.
    Must stay in sync with the full chronicler migration chain
    (``roster/chronicler/migrations/``). The CI test
    ``tests/chronicler/test_schema_drift.py`` automatically detects drift
    between this inline DDL and the Alembic migration chain.

    Current migrations reflected here:
    - 001_chronicler_tables: base schema
    - 002_per_schema_watermarks: subsource column and composite PK on projection_checkpoints
    - 005_tuple_watermark: watermark_id column on projection_checkpoints
    - 006_checkpoint_carryover: carryover JSONB column on projection_checkpoints
    - 015_point_events_entity_id: entity_id column on point_events + v_point_events_corrected update
    - 016_drop_episodes_entity_id: drops episodes.entity_id (added in 013) +
      recreates v_episodes_corrected without it
    - 017_iea_layer_confidence_evidence: layer/confidence/evidence_refs on
      episodes, layer on point_events, both exposed on the corrected views

    Tables intentionally omitted (not needed by storage integration tests):
    - ``tier2_cache`` (migration 004)
    - ``episode_entities`` (migration 014)
    """
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS source_adapter_state (
            source_name TEXT PRIMARY KEY,
            chronicler_compatibility TEXT NOT NULL
                CHECK (chronicler_compatibility IN (
                    'supported', 'deferred', 'not_time_bearing', 'planned'
                )),
            read_surface TEXT,
            boundary_semantics TEXT,
            optional_schema BOOLEAN NOT NULL DEFAULT false,
            active BOOLEAN NOT NULL DEFAULT false,
            inactive_reason TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # projection_checkpoints: composite PK (source_name, subsource) so that
    # each sub-source (e.g. butler schema) can track its watermark independently.
    # subsource = '' is the global (adapter-level) sentinel row.
    # watermark_id added in migration 005_tuple_watermark: stores the source-table
    # id of the last-projected row to form a tuple watermark (watermark, watermark_id)
    # that eliminates batch-boundary missed-row edge cases.
    # carryover added in migration 006_checkpoint_carryover: nullable JSONB for
    # open-episode state that adapters persist across batch boundaries.
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS projection_checkpoints (
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name)
                ON DELETE CASCADE,
            subsource TEXT NOT NULL DEFAULT '',
            watermark TIMESTAMPTZ,
            watermark_id BIGINT,
            carryover JSONB,
            last_run_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_error TEXT,
            rows_projected BIGINT NOT NULL DEFAULT 0,
            run_count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_name, subsource)
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS point_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name),
            source_ref TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            precision TEXT NOT NULL DEFAULT 'exact'
                CHECK (precision IN ('exact', 'minute', 'hour', 'day', 'unknown')),
            title TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            privacy TEXT NOT NULL DEFAULT 'normal'
                CHECK (privacy IN ('normal', 'sensitive', 'restricted')),
            retention_days INTEGER,
            tombstone_at TIMESTAMPTZ,
            tombstone_reason TEXT,
            entity_id UUID,
            layer TEXT NOT NULL DEFAULT 'evidence'
                CHECK (layer IN ('intent', 'evidence', 'activity')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_name, source_ref)
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name),
            source_ref TEXT NOT NULL,
            episode_type TEXT NOT NULL,
            start_at TIMESTAMPTZ NOT NULL,
            end_at TIMESTAMPTZ,
            precision TEXT NOT NULL DEFAULT 'exact'
                CHECK (precision IN ('exact', 'minute', 'hour', 'day', 'unknown')),
            title TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            privacy TEXT NOT NULL DEFAULT 'normal'
                CHECK (privacy IN ('normal', 'sensitive', 'restricted')),
            retention_days INTEGER,
            tombstone_at TIMESTAMPTZ,
            tombstone_reason TEXT,
            layer TEXT NOT NULL DEFAULT 'evidence'
                CHECK (layer IN ('intent', 'evidence', 'activity')),
            confidence TEXT NOT NULL DEFAULT 'low'
                CHECK (confidence IN ('high', 'medium', 'low')),
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_name, source_ref),
            CHECK (end_at IS NULL OR end_at >= start_at)
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS episode_event_links (
            episode_id UUID NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
            event_id UUID NOT NULL REFERENCES point_events(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'supports'
                CHECK (relation IN ('supports', 'boundary_start', 'boundary_end', 'evidence')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (episode_id, event_id, relation)
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS overrides (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            target_kind TEXT NOT NULL CHECK (target_kind IN ('episode', 'point_event')),
            target_id UUID NOT NULL,
            corrected_start_at TIMESTAMPTZ,
            corrected_end_at TIMESTAMPTZ,
            corrected_title TEXT,
            corrected_privacy TEXT
                CHECK (corrected_privacy IS NULL OR
                       corrected_privacy IN ('normal', 'sensitive', 'restricted')),
            corrected_tombstone_at TIMESTAMPTZ,
            note TEXT,
            submitted_by TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (
                corrected_start_at IS NOT NULL OR
                corrected_end_at IS NOT NULL OR
                corrected_title IS NOT NULL OR
                corrected_privacy IS NOT NULL OR
                corrected_tombstone_at IS NOT NULL OR
                note IS NOT NULL
            )
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name)
                ON DELETE CASCADE,
            key TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            hit_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (source_name, key)
        )
    """)
    await pool.execute("""
        CREATE OR REPLACE VIEW v_latest_overrides AS
        SELECT DISTINCT ON (target_kind, target_id)
            target_kind,
            target_id,
            corrected_start_at,
            corrected_end_at,
            corrected_title,
            corrected_privacy,
            corrected_tombstone_at,
            note,
            created_at AS corrected_at
        FROM overrides
        ORDER BY target_kind, target_id, created_at DESC
    """)
    await pool.execute("""
        CREATE OR REPLACE VIEW v_episodes_corrected AS
        SELECT
            e.id,
            e.source_name,
            e.source_ref,
            e.episode_type,
            COALESCE(o.corrected_start_at, e.start_at) AS start_at,
            COALESCE(o.corrected_end_at, e.end_at) AS end_at,
            e.precision,
            COALESCE(o.corrected_title, e.title) AS title,
            e.payload,
            COALESCE(o.corrected_privacy, e.privacy) AS privacy,
            e.retention_days,
            COALESCE(o.corrected_tombstone_at, e.tombstone_at) AS tombstone_at,
            e.start_at AS canonical_start_at,
            e.end_at AS canonical_end_at,
            e.title AS canonical_title,
            e.privacy AS canonical_privacy,
            o.corrected_at,
            o.note AS correction_note,
            e.created_at,
            e.updated_at,
            e.layer,
            e.confidence,
            e.evidence_refs
        FROM episodes e
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'episode' AND o.target_id = e.id
    """)
    await pool.execute("""
        CREATE OR REPLACE VIEW v_point_events_corrected AS
        SELECT
            p.id,
            p.source_name,
            p.source_ref,
            p.event_type,
            COALESCE(o.corrected_start_at, p.occurred_at) AS occurred_at,
            p.precision,
            COALESCE(o.corrected_title, p.title) AS title,
            p.payload,
            COALESCE(o.corrected_privacy, p.privacy) AS privacy,
            p.retention_days,
            COALESCE(o.corrected_tombstone_at, p.tombstone_at) AS tombstone_at,
            p.occurred_at AS canonical_occurred_at,
            p.title AS canonical_title,
            p.privacy AS canonical_privacy,
            o.corrected_at,
            o.note AS correction_note,
            p.created_at,
            p.updated_at,
            p.entity_id,
            p.layer
        FROM point_events p
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'point_event' AND o.target_id = p.id
    """)


@pytest.fixture
async def chronicler_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        await _apply_chronicler_schema(pool)
        await seed_source_registry(pool, sources=INITIAL_SOURCES)
        yield pool


# ── Source registry ────────────────────────────────────────────────────────


async def test_initial_sources_seeded(chronicler_pool) -> None:
    state = await get_source_state(chronicler_pool, "core.sessions")
    assert state is not None
    assert state.chronicler_compatibility == Compatibility.SUPPORTED


async def test_register_and_toggle_active(chronicler_pool) -> None:
    state = SourceAdapterState(
        source_name="test.source",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="test.view",
        boundary_semantics="instant",
    )
    await register_source(chronicler_pool, state)
    await mark_source_active(chronicler_pool, "test.source", active=True)
    stored = await get_source_state(chronicler_pool, "test.source")
    assert stored is not None
    assert stored.active is True

    await mark_source_active(
        chronicler_pool, "test.source", active=False, inactive_reason="missing schema"
    )
    stored = await get_source_state(chronicler_pool, "test.source")
    assert stored is not None
    assert stored.active is False
    assert stored.inactive_reason == "missing schema"


# ── Idempotent upserts ─────────────────────────────────────────────────────


async def test_point_event_idempotent_replay(chronicler_pool) -> None:
    now = datetime.now(UTC)
    event = PointEvent(
        source_name="core.sessions",
        source_ref="test:e1",
        event_type=EVENT_TYPE_SESSION_STARTED,
        occurred_at=now,
        title="first",
    )
    first = await upsert_point_event(chronicler_pool, event)

    event.title = "updated"
    second = await upsert_point_event(chronicler_pool, event)

    assert first.id == second.id
    assert second.title == "updated"
    count = await chronicler_pool.fetchval(
        "SELECT COUNT(*) FROM point_events WHERE source_ref = $1",
        "test:e1",
    )
    assert count == 1


async def test_open_then_close_episode(chronicler_pool) -> None:
    now = datetime.now(UTC)
    ep = Episode(
        source_name="core.sessions",
        source_ref="test:ep1",
        episode_type=EPISODE_TYPE_WORK,
        start_at=now,
        end_at=None,
    )
    opened = await upsert_episode(chronicler_pool, ep)
    assert opened.end_at is None

    ep.end_at = now + timedelta(minutes=10)
    closed = await upsert_episode(chronicler_pool, ep)
    assert closed.id == opened.id
    assert closed.end_at is not None


async def test_overlapping_episodes_both_stored(chronicler_pool) -> None:
    base = datetime.now(UTC)
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="overlap-a",
            episode_type="work",
            start_at=base,
            end_at=base + timedelta(hours=1),
        ),
    )
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="google_calendar.completed",
            source_ref="overlap-b",
            episode_type="scheduled_block",
            start_at=base + timedelta(minutes=15),
            end_at=base + timedelta(minutes=45),
        ),
    )
    results = await list_episodes(chronicler_pool, limit=100)
    assert len(results) == 2


async def test_list_episodes_overlaps_with(chronicler_pool) -> None:
    base = datetime.now(UTC) - timedelta(days=1)
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="window-hit",
            episode_type="work",
            start_at=base,
            end_at=base + timedelta(hours=1),
        ),
    )
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="window-miss",
            episode_type="work",
            start_at=base - timedelta(days=3),
            end_at=base - timedelta(days=3, hours=-1),
        ),
    )
    hits = await list_episodes(
        chronicler_pool,
        overlaps_with=(base - timedelta(minutes=5), base + timedelta(hours=2)),
    )
    source_refs = {e.source_ref for e in hits}
    assert "window-hit" in source_refs
    assert "window-miss" not in source_refs


async def test_list_overlapping_episodes_includes_target(chronicler_pool) -> None:
    base = datetime.now(UTC)
    target = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="target-ep",
            episode_type="work",
            start_at=base,
            end_at=base + timedelta(hours=2),
        ),
    )
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="google_calendar.completed",
            source_ref="overlap-ep",
            episode_type="scheduled_block",
            start_at=base + timedelta(minutes=30),
            end_at=base + timedelta(minutes=45),
        ),
    )
    assert target.id is not None
    overlapping = await list_overlapping_episodes(chronicler_pool, target.id)
    refs = {e.source_ref for e in overlapping}
    assert {"target-ep", "overlap-ep"}.issubset(refs)


# ── Correction overlay semantics ───────────────────────────────────────────


async def test_override_preserves_canonical_and_applies_to_view(chronicler_pool) -> None:
    base = datetime.now(UTC)
    canonical = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="correct-me",
            episode_type="work",
            start_at=base,
            end_at=base + timedelta(minutes=30),
            title="original",
        ),
    )
    assert canonical.id is not None

    corrected_start = base - timedelta(minutes=15)
    await insert_override(
        chronicler_pool,
        Override(
            target_kind=OverrideTarget.EPISODE,
            target_id=canonical.id,
            corrected_start_at=corrected_start,
            corrected_title="revised",
            note="user says start was earlier",
        ),
    )
    corrected = await get_episode(chronicler_pool, canonical.id)
    assert corrected is not None
    assert corrected.start_at == corrected_start
    assert corrected.title == "revised"
    assert corrected.canonical_start_at == canonical.start_at
    assert corrected.canonical_title == "original"
    assert corrected.correction_note == "user says start was earlier"


async def test_later_override_wins(chronicler_pool) -> None:
    base = datetime.now(UTC)
    canonical = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="correct-chain",
            episode_type="work",
            start_at=base,
            end_at=base + timedelta(minutes=15),
            title="orig",
        ),
    )
    assert canonical.id is not None

    await insert_override(
        chronicler_pool,
        Override(
            target_kind=OverrideTarget.EPISODE,
            target_id=canonical.id,
            corrected_title="first edit",
        ),
    )
    await insert_override(
        chronicler_pool,
        Override(
            target_kind=OverrideTarget.EPISODE,
            target_id=canonical.id,
            corrected_title="second edit",
        ),
    )
    corrected = await get_episode(chronicler_pool, canonical.id)
    assert corrected is not None
    assert corrected.title == "second edit"

    history = await list_overrides_for(
        chronicler_pool,
        target_kind=OverrideTarget.EPISODE,
        target_id=canonical.id,
    )
    assert len(history) == 2
    assert history[0].corrected_title == "second edit"
    assert history[1].corrected_title == "first edit"


async def test_tombstone_hides_from_default_view(chronicler_pool) -> None:
    now = datetime.now(UTC)
    canonical = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="tomb-me",
            episode_type="work",
            start_at=now,
            end_at=now + timedelta(minutes=5),
        ),
    )
    assert canonical.id is not None
    await insert_override(
        chronicler_pool,
        Override(
            target_kind=OverrideTarget.EPISODE,
            target_id=canonical.id,
            corrected_tombstone_at=now + timedelta(seconds=1),
        ),
    )
    missing = await get_episode(chronicler_pool, canonical.id)
    assert missing is None
    visible = await get_episode(chronicler_pool, canonical.id, include_tombstoned=True)
    assert visible is not None


# ── Event links ────────────────────────────────────────────────────────────


async def test_link_events_to_episode(chronicler_pool) -> None:
    now = datetime.now(UTC)
    ep = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="link-ep",
            episode_type="work",
            start_at=now,
            end_at=now + timedelta(minutes=15),
        ),
    )
    ev1 = await upsert_point_event(
        chronicler_pool,
        PointEvent(
            source_name="core.sessions",
            source_ref="link-ev-1",
            event_type=EVENT_TYPE_SESSION_STARTED,
            occurred_at=now,
        ),
    )
    ev2 = await upsert_point_event(
        chronicler_pool,
        PointEvent(
            source_name="core.sessions",
            source_ref="link-ev-2",
            event_type=EVENT_TYPE_SESSION_COMPLETED,
            occurred_at=now + timedelta(minutes=15),
        ),
    )
    assert ep.id and ev1.id and ev2.id
    await link_event_to_episode(
        chronicler_pool,
        episode_id=ep.id,
        event_id=ev1.id,
        relation=LinkRelation.BOUNDARY_START,
    )
    await link_event_to_episode(
        chronicler_pool,
        episode_id=ep.id,
        event_id=ev2.id,
        relation=LinkRelation.BOUNDARY_END,
    )

    events = await list_episode_events(chronicler_pool, ep.id)
    assert {e.source_ref for e in events} == {"link-ev-1", "link-ev-2"}


async def test_refresh_episode_evidence_refs_from_links(chronicler_pool) -> None:
    """evidence_refs is denormalized from episode_event_links, occurrence-ordered."""
    now = datetime.now(UTC)
    ep = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="evref-ep",
            episode_type="work",
            start_at=now,
            end_at=now + timedelta(minutes=30),
        ),
    )
    # Insert events out of occurrence order to prove ordering comes from the DB.
    ev_late = await upsert_point_event(
        chronicler_pool,
        PointEvent(
            source_name="core.sessions",
            source_ref="evref-late",
            event_type=EVENT_TYPE_SESSION_COMPLETED,
            occurred_at=now + timedelta(minutes=30),
        ),
    )
    ev_early = await upsert_point_event(
        chronicler_pool,
        PointEvent(
            source_name="core.sessions",
            source_ref="evref-early",
            event_type=EVENT_TYPE_SESSION_STARTED,
            occurred_at=now,
        ),
    )
    assert ep.id and ev_early.id and ev_late.id

    # No links yet -> empty refs.
    assert await refresh_episode_evidence_refs(chronicler_pool, ep.id) == []

    await link_event_to_episode(
        chronicler_pool,
        episode_id=ep.id,
        event_id=ev_late.id,
        relation=LinkRelation.BOUNDARY_END,
    )
    await link_event_to_episode(
        chronicler_pool,
        episode_id=ep.id,
        event_id=ev_early.id,
        relation=LinkRelation.BOUNDARY_START,
    )

    refs = await refresh_episode_evidence_refs(chronicler_pool, ep.id)
    # Ordered by occurrence: early event id first, late event id second.
    assert refs == [str(ev_early.id), str(ev_late.id)]

    # Persisted onto the episode row's evidence_refs surface.
    stored = await get_episode(chronicler_pool, ep.id)
    assert stored is not None
    assert stored.evidence_refs == [str(ev_early.id), str(ev_late.id)]


# ── Checkpoints ────────────────────────────────────────────────────────────


async def test_checkpoint_success_advances_watermark(chronicler_pool) -> None:
    now = datetime.now(UTC)
    await upsert_checkpoint(
        chronicler_pool,
        "core.sessions",
        watermark=now,
        success=True,
        rows_projected=7,
    )
    cp = await get_checkpoint(chronicler_pool, "core.sessions")
    assert cp is not None
    assert cp.watermark == now
    assert cp.rows_projected == 7
    assert cp.last_error is None


async def test_checkpoint_failure_does_not_advance_watermark(chronicler_pool) -> None:
    await upsert_checkpoint(
        chronicler_pool,
        "core.sessions",
        watermark=datetime.now(UTC),
        success=True,
        rows_projected=3,
    )
    # A failed run should keep the previous watermark.
    await upsert_checkpoint(
        chronicler_pool,
        "core.sessions",
        success=False,
        error="boom",
    )
    cp = await get_checkpoint(chronicler_pool, "core.sessions")
    assert cp is not None
    assert cp.watermark is not None  # unchanged
    assert cp.last_error == "boom"


async def test_record_idempotency_inserted_vs_duplicate(chronicler_pool) -> None:
    inserted_first = await record_idempotency(
        chronicler_pool, source_name="core.sessions", key="key-1"
    )
    inserted_again = await record_idempotency(
        chronicler_pool, source_name="core.sessions", key="key-1"
    )
    assert inserted_first is True
    assert inserted_again is False


# ── Cross-schema sessions adapter integration ─────────────────────────────


async def test_sessions_adapter_projects_and_replays(chronicler_pool) -> None:
    """Exercise the sessions adapter end-to-end using a fake butler schema."""
    fake_schema = "testbutler"
    async with chronicler_pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{fake_schema}"')
        await conn.execute(make_sessions_table_ddl(fake_schema))
        # One open + one closed session.
        open_id = uuid4()
        closed_id = uuid4()
        now = datetime.now(UTC)
        await conn.execute(
            f"""
            INSERT INTO "{fake_schema}".sessions (
                id, prompt, trigger_source, request_id, started_at, completed_at
            ) VALUES
              ($1, 'open', 'external', 'r1', $2, NULL),
              ($3, 'closed', 'external', 'r2', $4, $5)
            """,
            open_id,
            now - timedelta(minutes=10),
            closed_id,
            now - timedelta(minutes=30),
            now - timedelta(minutes=25),
        )

    adapter = CoreSessionsAdapter(butler_schemas=(fake_schema,))
    result = await adapter.run(pool=chronicler_pool, chronicler_pool=chronicler_pool)
    assert result.success
    assert result.rows_projected == 2
    assert result.episodes_opened == 1
    assert result.episodes_closed == 1

    episodes = await list_episodes(chronicler_pool, source_name="core.sessions")
    assert len(episodes) == 2
    events = await list_point_events(chronicler_pool, source_name="core.sessions")
    # 1 started for open + 1 started + 1 completed for closed
    assert len(events) == 3

    # Replay is idempotent.
    result2 = await adapter.run(pool=chronicler_pool, chronicler_pool=chronicler_pool)
    assert result2.success
    episodes_after = await list_episodes(chronicler_pool, source_name="core.sessions")
    assert len(episodes_after) == 2

    # Close the open session and re-run; the open episode should be closed
    # in place without creating a duplicate.
    async with chronicler_pool.acquire() as conn:
        await conn.execute(
            f'UPDATE "{fake_schema}".sessions SET completed_at = $1 WHERE id = $2',
            datetime.now(UTC),
            open_id,
        )
    await adapter.run(pool=chronicler_pool, chronicler_pool=chronicler_pool)
    episodes_final = await list_episodes(chronicler_pool, source_name="core.sessions")
    assert len(episodes_final) == 2
    for ep in episodes_final:
        assert ep.end_at is not None, f"Episode {ep.source_ref} still open after replay"


# ── IEA layer / confidence / evidence_refs (bu-66v7ff) ─────────────────────


async def test_freshly_projected_activity_source_is_activity(chronicler_pool) -> None:
    """Regression (bu-66v7ff): an activity-source adapter stamps layer=activity
    on the ongoing write path, and its boundary point events are layer=evidence.

    Exercises the real sessions adapter end-to-end (not just storage) so the
    "stamp only on backfill" gap cannot regress silently.
    """
    fake_schema = "iealayer"
    async with chronicler_pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{fake_schema}"')
        await conn.execute(make_sessions_table_ddl(fake_schema))
        now = datetime.now(UTC)
        await conn.execute(
            f"""
            INSERT INTO "{fake_schema}".sessions (
                id, prompt, trigger_source, request_id, started_at, completed_at
            ) VALUES ($1, 'done', 'external', 'r1', $2, $3)
            """,
            uuid4(),
            now - timedelta(minutes=30),
            now - timedelta(minutes=25),
        )

    adapter = CoreSessionsAdapter(butler_schemas=(fake_schema,))
    result = await adapter.run(pool=chronicler_pool, chronicler_pool=chronicler_pool)
    assert result.success

    episodes = await list_episodes(chronicler_pool, source_name="core.sessions")
    assert episodes
    assert all(ep.layer == Layer.ACTIVITY for ep in episodes)

    events = await list_point_events(chronicler_pool, source_name="core.sessions")
    assert events
    assert all(ev.layer == Layer.EVIDENCE for ev in events)


async def test_upsert_episode_round_trips_intent_layer(chronicler_pool) -> None:
    """A calendar-style intent episode stores and reads back as layer=intent."""
    ep = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="google_calendar.completed",
            source_ref="iea-intent-1",
            episode_type="scheduled_block",
            start_at=datetime.now(UTC) - timedelta(hours=5),
            end_at=datetime.now(UTC),
            title="5h calendar block",
            layer=Layer.INTENT,
        ),
    )
    assert ep.layer == Layer.INTENT
    # Read path (corrected view) also surfaces the layer.
    fetched = await get_episode(chronicler_pool, ep.id)
    assert fetched is not None
    assert fetched.layer == Layer.INTENT


async def test_upsert_episode_defaults_are_conservative(chronicler_pool) -> None:
    """An episode upserted without IEA fields defaults to the conservative,
    never-counted values (evidence / low / empty chain)."""
    ep = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="iea-default-1",
            episode_type="work",
            start_at=datetime.now(UTC) - timedelta(hours=1),
            end_at=datetime.now(UTC),
            title="unstamped",
        ),
    )
    assert ep.layer == Layer.EVIDENCE
    assert ep.confidence == Confidence.LOW
    assert ep.evidence_refs == []


async def test_overlapping_episodes_still_permitted(chronicler_pool) -> None:
    """Adding the layer column must not introduce a uniqueness/exclusion
    constraint that rejects time-overlapping episodes (intent + activity that
    cover the same window legitimately coexist)."""
    start = datetime.now(UTC) - timedelta(hours=2)
    end = datetime.now(UTC)
    intent = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="google_calendar.completed",
            source_ref="iea-overlap-intent",
            episode_type="scheduled_block",
            start_at=start,
            end_at=end,
            title="planned meeting",
            layer=Layer.INTENT,
        ),
    )
    activity = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="iea-overlap-activity",
            episode_type="work",
            start_at=start + timedelta(minutes=10),
            end_at=end - timedelta(minutes=10),
            title="actual work",
            layer=Layer.ACTIVITY,
        ),
    )
    overlapping = await list_overlapping_episodes(chronicler_pool, intent.id)
    ids = {ep.id for ep in overlapping}
    assert intent.id in ids
    assert activity.id in ids


async def test_uncorroborated_calendar_block_counts_zero_over_real_view(
    chronicler_pool,
) -> None:
    """IEA counting regression (tasks.md §4), exercised against REAL Postgres.

    A 5 h calendar block (intent layer) overlapped by a 2 h GPS-dwell movement
    episode (activity layer). The aggregation seam ``lane_for_activity`` is run
    over rows read back from the real ``v_episodes_corrected`` view (the layer
    column round-trips through the view), so a wrong SQL/layer filter would be
    caught here — a mocked pool cannot (this exact gap caused a prior main-red,
    see bu-3n44q5). Expectation: the calendar block contributes 0 s to every
    lane; the overlapping activity episode is the only thing counted, under its
    own Travel lane.
    """
    window_start = datetime(2026, 6, 20, 9, 0, tzinfo=UTC)
    window_end = datetime(2026, 6, 20, 18, 0, tzinfo=UTC)

    # 5 h planned calendar block — intent, must never count.
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="google_calendar.completed",
            source_ref="iea-5h-calendar",
            episode_type="scheduled_block",
            start_at=window_start,
            end_at=window_start + timedelta(hours=5),
            title="5h calendar block",
            layer=Layer.INTENT,
        ),
    )
    # Overlapping 2 h GPS-dwell movement — activity, the thing that counts.
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="owntracks.points",
            source_ref="iea-gps-dwell",
            episode_type="movement_episode",
            start_at=window_start,
            end_at=window_start + timedelta(hours=2),
            title="GPS dwell",
            layer=Layer.ACTIVITY,
        ),
    )

    # Read back from the REAL corrected view, then run the real counting seam.
    rows = await chronicler_pool.fetch(
        """
        SELECT source_name, episode_type, start_at, end_at, layer,
               payload->>'trigger_source' AS trigger_source
        FROM v_episodes_corrected
        WHERE start_at < $2 AND (end_at IS NULL OR end_at > $1)
          AND tombstone_at IS NULL
        """,
        window_start,
        window_end,
    )

    lane_intervals: dict[str, list[tuple[datetime, datetime]]] = {}
    for row in rows:
        lane = lane_for_activity(
            row["layer"],
            row["source_name"],
            row["episode_type"],
            trigger_source=row["trigger_source"],
        )
        if lane is None:
            continue
        lane_intervals.setdefault(lane, []).append((row["start_at"], row["end_at"]))

    lane_seconds = {lane: union_seconds(ivals) for lane, ivals in lane_intervals.items()}

    # The intent calendar block contributes nothing; only Travel is counted.
    assert lane_seconds == {"travel": pytest.approx(2 * 3600.0)}
    assert "calendar" not in lane_seconds
    assert "intent" not in lane_seconds


async def test_sessions_adapter_degrades_when_schema_missing(chronicler_pool) -> None:
    """Adapter MUST skip cleanly when the optional schema has no sessions
    table."""
    adapter = CoreSessionsAdapter(butler_schemas=("ghost",))
    result = await adapter.run(pool=chronicler_pool, chronicler_pool=chronicler_pool)
    assert result.success
    assert result.rows_projected == 0
    assert any("ghost" in w for w in result.warnings)


# ── Per-schema watermark independence ─────────────────────────────────────


async def test_checkpoint_subsource_roundtrip(chronicler_pool) -> None:
    """Per-subsource checkpoints are keyed independently from the global row."""
    now = datetime.now(UTC)

    # Write a global checkpoint.
    await upsert_checkpoint(
        chronicler_pool, "core.sessions", watermark=now, success=True, rows_projected=3
    )
    # Write two per-schema checkpoints.
    schema_a_wm = now - timedelta(hours=1)
    schema_b_wm = now - timedelta(minutes=5)
    await upsert_checkpoint_subsource(
        chronicler_pool,
        "core.sessions",
        "schema_a",
        watermark=schema_a_wm,
        success=True,
        rows_projected=1,
    )
    await upsert_checkpoint_subsource(
        chronicler_pool,
        "core.sessions",
        "schema_b",
        watermark=schema_b_wm,
        success=True,
        rows_projected=2,
    )

    global_cp = await get_checkpoint(chronicler_pool, "core.sessions")
    assert global_cp is not None
    assert global_cp.subsource is None  # exposed as None by Python model
    assert global_cp.watermark == now
    assert global_cp.rows_projected == 3

    cp_a = await get_checkpoint_subsource(chronicler_pool, "core.sessions", "schema_a")
    assert cp_a is not None
    assert cp_a.subsource == "schema_a"
    assert cp_a.watermark == schema_a_wm
    assert cp_a.rows_projected == 1

    cp_b = await get_checkpoint_subsource(chronicler_pool, "core.sessions", "schema_b")
    assert cp_b is not None
    assert cp_b.subsource == "schema_b"
    assert cp_b.watermark == schema_b_wm
    assert cp_b.rows_projected == 2


async def test_sessions_adapter_per_schema_watermarks_advance_independently(
    chronicler_pool,
) -> None:
    """Each schema's watermark advances only for its own sessions.

    Schema A has older sessions; schema B has current sessions.
    After projection, the per-schema watermarks reflect each schema's
    own newest ``started_at``, not a global max.
    """
    schema_a = "wm_test_alpha"
    schema_b = "wm_test_beta"
    now = datetime.now(UTC)

    async with chronicler_pool.acquire() as conn:
        for schema in (schema_a, schema_b):
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            await conn.execute(make_sessions_table_ddl(schema))

        # Schema A: one old closed session (30 days ago).
        old_time = now - timedelta(days=30)
        await conn.execute(
            f"""
            INSERT INTO "{schema_a}".sessions (
                prompt, trigger_source, request_id, started_at, completed_at
            ) VALUES ('old', 'external', 'r-old', $1, $2)
            """,
            old_time,
            old_time + timedelta(minutes=5),
        )
        # Schema B: one recent closed session (2 minutes ago).
        recent_time = now - timedelta(minutes=2)
        await conn.execute(
            f"""
            INSERT INTO "{schema_b}".sessions (
                prompt, trigger_source, request_id, started_at, completed_at
            ) VALUES ('recent', 'external', 'r-recent', $1, $2)
            """,
            recent_time,
            recent_time + timedelta(minutes=1),
        )

    adapter = CoreSessionsAdapter(butler_schemas=(schema_a, schema_b))
    result = await adapter.run(pool=chronicler_pool, chronicler_pool=chronicler_pool)
    assert result.success
    assert result.rows_projected == 2

    # Per-schema watermarks must reflect each schema's own sessions.
    cp_a = await get_checkpoint_subsource(chronicler_pool, "core.sessions", schema_a)
    cp_b = await get_checkpoint_subsource(chronicler_pool, "core.sessions", schema_b)
    assert cp_a is not None and cp_a.watermark is not None
    assert cp_b is not None and cp_b.watermark is not None
    # Schema A watermark should reflect the old session's started_at.
    assert abs((cp_a.watermark - old_time).total_seconds()) < 1
    # Schema B watermark should reflect the recent session's started_at.
    assert abs((cp_b.watermark - recent_time).total_seconds()) < 1
    # Schema A watermark is much earlier than schema B's.
    assert cp_a.watermark < cp_b.watermark - timedelta(days=25)

    # Second run: add a new session to schema A only; schema B stays silent.
    # Schema B watermark must NOT advance (no new rows).
    new_a_time = now - timedelta(minutes=1)
    async with chronicler_pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO "{schema_a}".sessions (
                prompt, trigger_source, request_id, started_at, completed_at
            ) VALUES ('newer', 'external', 'r-newer', $1, $2)
            """,
            new_a_time,
            new_a_time + timedelta(minutes=1),
        )
    result2 = await adapter.run(pool=chronicler_pool, chronicler_pool=chronicler_pool)
    assert result2.success
    # The adapter re-fetches any row whose completed_at > since (to close open
    # episodes). At minimum the new schema A session is projected; already-closed
    # sessions from both schemas may also be re-visited idempotently.
    assert result2.rows_projected >= 1

    cp_a2 = await get_checkpoint_subsource(chronicler_pool, "core.sessions", schema_a)
    cp_b2 = await get_checkpoint_subsource(chronicler_pool, "core.sessions", schema_b)
    assert cp_a2 is not None and cp_a2.watermark is not None
    # Schema A watermark advanced to the newer session.
    assert cp_a2.watermark > cp_a.watermark
    # Schema B watermark unchanged — no new sessions.
    assert cp_b2 is not None
    assert cp_b2.watermark == cp_b.watermark


async def test_sessions_adapter_global_watermark_is_conservative(
    chronicler_pool,
) -> None:
    """Global summary watermark must not skip ahead of idle schemas.

    When Schema A advances (new sessions) and Schema B is idle, the
    global result.watermark must equal Schema B's existing watermark
    (the minimum), not Schema A's new watermark. Otherwise a newly
    registered Schema C would use the inflated global as its fallback
    and skip data that arrived between B's watermark and A's new one.
    """
    schema_a = "gwm_test_alpha"
    schema_b = "gwm_test_beta"
    now = datetime.now(UTC)

    async with chronicler_pool.acquire() as conn:
        for schema in (schema_a, schema_b):
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            await conn.execute(make_sessions_table_ddl(schema))

        # Schema A: old session (30 days ago).
        old_time = now - timedelta(days=30)
        await conn.execute(
            f"""
            INSERT INTO "{schema_a}".sessions (
                prompt, trigger_source, request_id, started_at, completed_at
            ) VALUES ('old-a', 'external', 'r-gwm-a', $1, $2)
            """,
            old_time,
            old_time + timedelta(minutes=5),
        )
        # Schema B: session also 30 days ago (same baseline).
        await conn.execute(
            f"""
            INSERT INTO "{schema_b}".sessions (
                prompt, trigger_source, request_id, started_at, completed_at
            ) VALUES ('old-b', 'external', 'r-gwm-b', $1, $2)
            """,
            old_time,
            old_time + timedelta(minutes=5),
        )

    adapter = CoreSessionsAdapter(butler_schemas=(schema_a, schema_b))
    result = await adapter.run(pool=chronicler_pool, chronicler_pool=chronicler_pool)
    assert result.success
    assert result.rows_projected == 2

    cp_a = await get_checkpoint_subsource(chronicler_pool, "core.sessions", schema_a)
    cp_b = await get_checkpoint_subsource(chronicler_pool, "core.sessions", schema_b)
    assert cp_a is not None and cp_a.watermark is not None
    assert cp_b is not None and cp_b.watermark is not None

    # Second run: add a new session to schema A only (1 minute ago).
    # Schema B stays silent.
    new_a_time = now - timedelta(minutes=1)
    async with chronicler_pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO "{schema_a}".sessions (
                prompt, trigger_source, request_id, started_at, completed_at
            ) VALUES ('new-a', 'external', 'r-gwm-a2', $1, $2)
            """,
            new_a_time,
            new_a_time + timedelta(minutes=1),
        )
    result2 = await adapter.run(pool=chronicler_pool, chronicler_pool=chronicler_pool)
    assert result2.success

    # The global summary watermark must be the MINIMUM of all schemas —
    # Schema B's watermark (old_time) not Schema A's new watermark (new_a_time).
    # Without the fix, schema_watermarks would only contain new_a_time and
    # result2.watermark would jump to ~new_a_time, silently skipping old_time
    # for any schema that joins after this run.
    assert result2.watermark is not None
    assert abs((result2.watermark - cp_b.watermark).total_seconds()) < 1, (
        f"Global watermark {result2.watermark} should equal Schema B's existing "
        f"watermark {cp_b.watermark}, not Schema A's new watermark {new_a_time}"
    )


# ── point_events entity_id (bu-kihe8) ────────────────────────────────────


async def test_upsert_point_event_with_entity_id(chronicler_pool) -> None:
    """Point events can be written and read back with entity_id set."""
    from uuid import uuid4

    from butlers.chronicler.models import PointEvent, Precision, Privacy

    eid = uuid4()
    now = datetime.now(UTC)
    ev = PointEvent(
        source_name="health.meals",
        source_ref="entity-pe-test-1",
        event_type="eating_event",
        occurred_at=now,
        precision=Precision.EXACT,
        privacy=Privacy.SENSITIVE,
        entity_id=eid,
    )
    stored = await upsert_point_event(chronicler_pool, ev)
    assert stored.entity_id == eid

    # Also verify via list_point_events (reads from v_point_events_corrected).
    events = await list_point_events(chronicler_pool, source_name="health.meals")
    matching = [e for e in events if e.source_ref == "entity-pe-test-1"]
    assert len(matching) == 1
    assert matching[0].entity_id == eid


async def test_upsert_point_event_entity_id_null_by_default(chronicler_pool) -> None:
    """Point events without entity_id default to NULL."""
    from butlers.chronicler.models import PointEvent, Precision, Privacy

    now = datetime.now(UTC)
    ev = PointEvent(
        source_name="health.meals",
        source_ref="entity-pe-null-1",
        event_type="eating_event",
        occurred_at=now,
        precision=Precision.EXACT,
        privacy=Privacy.SENSITIVE,
    )
    stored = await upsert_point_event(chronicler_pool, ev)
    assert stored.entity_id is None


async def test_list_point_events_entity_id_filter(chronicler_pool) -> None:
    """list_point_events(entity_id=) filters to point events for that entity only."""
    from uuid import uuid4

    from butlers.chronicler.models import PointEvent, Precision

    entity_a = uuid4()
    entity_b = uuid4()
    now = datetime.now(UTC)

    for i, eid in enumerate([entity_a, entity_a, entity_b, None]):
        ev = PointEvent(
            source_name="health.steps",
            source_ref=f"entity-pe-filter-{i}",
            event_type="daily_steps",
            occurred_at=now - timedelta(hours=i),
            precision=Precision.DAY,
            entity_id=eid,
        )
        await upsert_point_event(chronicler_pool, ev)

    results_a = await list_point_events(chronicler_pool, entity_id=entity_a)
    assert len(results_a) == 2
    assert all(e.entity_id == entity_a for e in results_a)

    results_b = await list_point_events(chronicler_pool, entity_id=entity_b)
    assert len(results_b) == 1
    assert results_b[0].entity_id == entity_b


async def test_upsert_point_event_entity_id_updates_on_replay(chronicler_pool) -> None:
    """Replaying a point event with a new entity_id updates the stored entity_id."""
    from uuid import uuid4

    from butlers.chronicler.models import PointEvent, Precision, Privacy

    eid_1 = uuid4()
    eid_2 = uuid4()
    now = datetime.now(UTC)
    ev = PointEvent(
        source_name="health.heart_rate",
        source_ref="entity-pe-replay-hr",
        event_type="heart_rate_summary",
        occurred_at=now,
        precision=Precision.DAY,
        privacy=Privacy.SENSITIVE,
        entity_id=eid_1,
    )
    first = await upsert_point_event(chronicler_pool, ev)
    assert first.entity_id == eid_1

    ev.entity_id = eid_2
    second = await upsert_point_event(chronicler_pool, ev)
    assert second.id == first.id  # same row via ON CONFLICT
    assert second.entity_id == eid_2


# ── IEA read API: evidence chain + correction prompts (S9a, bu-qld86v) ──────
#
# HTTP-driven integration tests for the read/correction surfaces that resolve
# real joins/views (episode_event_links, v_episodes_corrected, the corrections
# overlay).  Driven against the provisioned Postgres pool rather than a mocked
# pool so the SQL — and the corrections-overlay write path — is exercised end to
# end (see "Mocked-pool vs integration test gap").


def _build_chronicler_api(pool) -> httpx.ASGITransport:
    """Build a FastAPI app whose chronicler router is wired to ``pool``."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app(api_key="")
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break
    else:  # pragma: no cover — defensive
        raise AssertionError("chronicler router not registered on the app")
    return httpx.ASGITransport(app=app)


async def test_evidence_chain_resolves_links_and_confidence(chronicler_pool) -> None:
    """GET /episodes/{id}/evidence-chain resolves the link chain + confidence."""
    now = datetime.now(UTC)
    ep = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="evchain-ep",
            episode_type="work",
            start_at=now,
            end_at=now + timedelta(minutes=30),
            layer=Layer.ACTIVITY,
            confidence=Confidence.LOW,
        ),
    )
    ev_early = await upsert_point_event(
        chronicler_pool,
        PointEvent(
            source_name="core.sessions",
            source_ref="evchain-early",
            event_type=EVENT_TYPE_SESSION_STARTED,
            occurred_at=now,
            title="session started",
        ),
    )
    ev_late = await upsert_point_event(
        chronicler_pool,
        PointEvent(
            source_name="core.sessions",
            source_ref="evchain-late",
            event_type=EVENT_TYPE_SESSION_COMPLETED,
            occurred_at=now + timedelta(minutes=30),
        ),
    )
    assert ep.id and ev_early.id and ev_late.id
    await link_event_to_episode(
        chronicler_pool,
        episode_id=ep.id,
        event_id=ev_early.id,
        relation=LinkRelation.BOUNDARY_START,
    )
    await link_event_to_episode(
        chronicler_pool,
        episode_id=ep.id,
        event_id=ev_late.id,
        relation=LinkRelation.BOUNDARY_END,
    )
    await refresh_episode_evidence_refs(chronicler_pool, ep.id)

    transport = _build_chronicler_api(chronicler_pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/chronicler/episodes/{ep.id}/evidence-chain")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["episode_id"] == str(ep.id)
    assert body["layer"] == "activity"
    assert body["confidence"] == "low"
    # evidence_refs denormalized + occurrence-ordered.
    assert body["evidence_refs"] == [str(ev_early.id), str(ev_late.id)]
    # Links resolve to the underlying point-events with source + descriptor.
    assert [link["event_id"] for link in body["links"]] == [str(ev_early.id), str(ev_late.id)]
    first = body["links"][0]
    assert first["source_name"] == "core.sessions"
    assert first["relation"] == "boundary_start"
    assert first["descriptor"] == "session started"  # event title
    # Event with no title falls back to "{source_name} {event_type}".
    last = body["links"][1]
    assert last["descriptor"] == f"core.sessions {EVENT_TYPE_SESSION_COMPLETED}"


async def test_evidence_chain_404_for_unknown_episode(chronicler_pool) -> None:
    transport = _build_chronicler_api(chronicler_pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/chronicler/episodes/{uuid4()}/evidence-chain")
    assert resp.status_code == 404


async def test_correction_prompts_read_then_overlay_write_resolves(chronicler_pool) -> None:
    """Low-confidence activities surface as prompts; an overlay write clears them.

    The write path reuses the EXISTING corrections overlay endpoint
    (POST /episodes/{id}/corrections), which records an ``overrides`` row; once
    present the episode's ``corrected_at`` is non-NULL and it drops off the list.
    """
    now = datetime.now(UTC)
    window_start = (now - timedelta(hours=1)).isoformat()
    window_end = (now + timedelta(hours=2)).isoformat()

    # Low-confidence activity → a prompt.
    ep_low = await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="prompt-low",
            episode_type="work",
            start_at=now,
            end_at=now + timedelta(minutes=20),
            title="ambiguous block",
            layer=Layer.ACTIVITY,
            confidence=Confidence.LOW,
        ),
    )
    # High-confidence activity → never a prompt.
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="prompt-high",
            episode_type="work",
            start_at=now,
            end_at=now + timedelta(minutes=20),
            layer=Layer.ACTIVITY,
            confidence=Confidence.HIGH,
        ),
    )
    # Low-confidence but intent layer (calendar) → excluded by the layer filter.
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="google_calendar.completed",
            source_ref="prompt-intent",
            episode_type="scheduled_block",
            start_at=now,
            end_at=now + timedelta(minutes=20),
            layer=Layer.INTENT,
            confidence=Confidence.LOW,
        ),
    )
    assert ep_low.id

    transport = _build_chronicler_api(chronicler_pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/chronicler/correction-prompts",
            params={"start_at": window_start, "end_at": window_end},
        )
        assert resp.status_code == 200, resp.text
        prompts = resp.json()["data"]["prompts"]
        assert [p["episode_id"] for p in prompts] == [str(ep_low.id)]
        only = prompts[0]
        assert only["confidence"] == "low"
        assert only["best_guess_lane"] == "work"
        assert only["title"] == "ambiguous block"

        # Write a correction through the EXISTING corrections overlay endpoint.
        write = await client.post(
            f"/api/chronicler/episodes/{ep_low.id}/corrections",
            json={"note": "confirmed: this was focused work", "submitted_by": "user"},
        )
        assert write.status_code == 201, write.text

        # The prompt is now addressed (corrected_at set) and drops off the list.
        resp2 = await client.get(
            "/api/chronicler/correction-prompts",
            params={"start_at": window_start, "end_at": window_end},
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["data"]["prompts"] == []


async def test_correction_prompts_param_validation(chronicler_pool) -> None:
    transport = _build_chronicler_api(chronicler_pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/chronicler/correction-prompts",
            params={"end_at": datetime.now(UTC).isoformat()},
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_parameter"
