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
from uuid import uuid4

import pytest

from butlers.chronicler.adapters.sessions import (
    EPISODE_TYPE_WORK,
    EVENT_TYPE_SESSION_COMPLETED,
    EVENT_TYPE_SESSION_STARTED,
    CoreSessionsAdapter,
)
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import (
    Compatibility,
    Episode,
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
    - 013_episodes_entity_id: entity_id column on episodes + v_episodes_corrected update

    Tables intentionally omitted (not needed by storage integration tests):
    - ``tier2_cache`` (migration 004)
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
            entity_id UUID,
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
            e.entity_id
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
            p.updated_at
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


# ── entity_id filter (bu-aqe7n / task 12.5) ──────────────────────────────


async def test_upsert_episode_with_entity_id(chronicler_pool) -> None:
    """Episodes can be written and read back with entity_id set."""
    from uuid import uuid4

    eid = uuid4()
    now = datetime.now(UTC)
    ep = Episode(
        source_name="core.sessions",
        source_ref="entity-ep-1",
        episode_type="work",
        start_at=now,
        end_at=now + timedelta(minutes=10),
        entity_id=eid,
    )
    stored = await upsert_episode(chronicler_pool, ep)
    assert stored.entity_id == eid

    fetched = await get_episode(chronicler_pool, stored.id)
    assert fetched is not None
    assert fetched.entity_id == eid


async def test_list_episodes_entity_id_filter_returns_matching_only(
    chronicler_pool,
) -> None:
    """list_episodes(entity_id=) filters to episodes for that entity only."""
    from uuid import uuid4

    entity_a = uuid4()
    entity_b = uuid4()
    now = datetime.now(UTC)

    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="eid-filter-a",
            episode_type="work",
            start_at=now - timedelta(hours=2),
            end_at=now - timedelta(hours=1),
            entity_id=entity_a,
        ),
    )
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="eid-filter-b",
            episode_type="work",
            start_at=now - timedelta(hours=4),
            end_at=now - timedelta(hours=3),
            entity_id=entity_b,
        ),
    )
    await upsert_episode(
        chronicler_pool,
        Episode(
            source_name="core.sessions",
            source_ref="eid-filter-none",
            episode_type="work",
            start_at=now - timedelta(hours=6),
            end_at=now - timedelta(hours=5),
            # entity_id not set — NULL
        ),
    )

    results_a = await list_episodes(chronicler_pool, entity_id=entity_a)
    refs_a = {e.source_ref for e in results_a}
    assert "eid-filter-a" in refs_a, "entity_a episode must appear"
    assert "eid-filter-b" not in refs_a, "entity_b episode must not appear"
    assert "eid-filter-none" not in refs_a, "no-entity episode must not appear"

    results_b = await list_episodes(chronicler_pool, entity_id=entity_b)
    refs_b = {e.source_ref for e in results_b}
    assert "eid-filter-b" in refs_b
    assert "eid-filter-a" not in refs_b

    # No filter — all three appear.
    all_results = await list_episodes(chronicler_pool)
    all_refs = {e.source_ref for e in all_results}
    assert {"eid-filter-a", "eid-filter-b", "eid-filter-none"}.issubset(all_refs)


async def test_list_episodes_entity_id_filter_empty_when_no_match(
    chronicler_pool,
) -> None:
    """Filtering on an entity_id with no matching episodes returns empty list."""
    from uuid import uuid4

    unknown_entity = uuid4()
    results = await list_episodes(chronicler_pool, entity_id=unknown_entity)
    assert results == []


async def test_upsert_episode_entity_id_updates_on_replay(chronicler_pool) -> None:
    """Replaying an episode with a new entity_id updates the stored entity_id."""
    from uuid import uuid4

    eid_1 = uuid4()
    eid_2 = uuid4()
    now = datetime.now(UTC)
    ep = Episode(
        source_name="core.sessions",
        source_ref="entity-ep-replay",
        episode_type="work",
        start_at=now,
        end_at=now + timedelta(minutes=5),
        entity_id=eid_1,
    )
    first = await upsert_episode(chronicler_pool, ep)
    assert first.entity_id == eid_1

    ep.entity_id = eid_2
    second = await upsert_episode(chronicler_pool, ep)
    assert second.id == first.id  # same row
    assert second.entity_id == eid_2
