"""Tests for the Calendar completed-instance Chronicler projection adapter.

Covers the title fallback chain when the upstream Google Calendar event has
no summary/title — the adapter should pick the next most-meaningful field
from the joined ``calendar_events`` row (title → location → truncated
description → schema-qualified placeholder).

Also covers the butler-managed calendar exclusion guard (defence-in-depth):
instances whose ``calendar_sources.lane = 'butler'`` must never be projected
into the user's Chronicle Calendar lane. Cross-schema dedup via
``origin_instance_ref`` collapse (regression for "five Labour Day bars" bug).

Episode-entities join table (bu-3zve1):
- Owner-only graceful degradation when ``calendar_event_entities`` is absent.
- Schema skipped (no projection, no delete) on transient DB error.
- Owner + participants written when join table present.
- DELETE-then-INSERT replaces stale attendees on second adapter run.
- Idempotent replay does not duplicate ``episode_entities`` rows.
- The resolved owner is written to ``episode_entities`` with role='owner'.
- Role-precedence collapse when the same entity appears as both owner
  and participant (owner wins).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from butlers.chronicler.adapters.calendar import (
    BUTLER_MANAGED_SOURCE_KINDS,
    EPISODE_TYPE_SCHEDULED_BLOCK,
    SOURCE_NAME,
    CalendarCompletedAdapter,
)
from butlers.chronicler.models import Episode, Layer

_NOW = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """asyncpg.Record-like dict subclass."""

    def __getattr__(self, name: str) -> object:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _make_row(
    *,
    metadata: dict | None = None,
    event_title: str | None = None,
    event_description: str | None = None,
    event_location: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> _Row:
    starts_at = starts_at or (_NOW - timedelta(hours=1))
    ends_at = ends_at or _NOW
    return _Row(
        {
            "id": uuid4(),
            "event_id": uuid4(),
            "source_id": uuid4(),
            "origin_instance_ref": "evt:abc:2026-04-01T09:00:00Z",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "status": "confirmed",
            "timezone": "UTC",
            "metadata": metadata if metadata is not None else {},
            "updated_at": ends_at,
            "event_title": event_title,
            "event_description": event_description,
            "event_location": event_location,
        }
    )


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        return None


def _chronicler_pool() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _chronicler_pool_with_tracking() -> tuple[AsyncMock, AsyncMock]:
    """Return a (pool, conn) pair where conn.execute/executemany calls are trackable.

    The conn is also configured with a transaction() context manager that is
    compatible with ``async with conn.transaction():``.
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool, conn


async def _project_one(row: _Row) -> Episode:
    """Drive ``_project_row`` directly with a single row and capture the Episode."""
    adapter = CalendarCompletedAdapter(butler_schemas=("butler_test",))
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured.append(episode)
        return episode

    cp = _chronicler_pool()
    with patch(
        "butlers.chronicler.adapters.calendar.upsert_episode",
        side_effect=_fake_upsert,
    ):
        await adapter._project_row(cp, "butler_test", row)
    assert captured, "upsert_episode was not invoked"
    return captured[0]


async def _project_one_tracked(
    row: _Row,
    *,
    entity_id: UUID | None = None,
    participant_ids: list[UUID] | None = None,
) -> tuple[Episode, AsyncMock]:
    """Drive ``_project_row`` and return the Episode plus the chronicler conn mock.

    The conn mock captures all execute/executemany calls so tests can inspect
    which SQL statements were issued for ``episode_entities``.

    The fake ``upsert_episode`` returns the episode with a stable UUID so
    ``_upsert_episode_entities`` has a valid episode.id to work with.
    """
    adapter = CalendarCompletedAdapter(butler_schemas=("butler_test",))
    captured: list[Episode] = []
    episode_id = uuid4()

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        episode = Episode(
            id=episode_id,
            source_name=episode.source_name,
            source_ref=episode.source_ref,
            episode_type=episode.episode_type,
            start_at=episode.start_at,
            end_at=episode.end_at,
            precision=episode.precision,
            title=episode.title,
            payload=episode.payload,
            privacy=episode.privacy,
        )
        captured.append(episode)
        return episode

    cp, conn = _chronicler_pool_with_tracking()
    with patch(
        "butlers.chronicler.adapters.calendar.upsert_episode",
        side_effect=_fake_upsert,
    ):
        await adapter._project_row(
            cp,
            "butler_test",
            row,
            entity_id=entity_id,
            participant_ids=participant_ids,
        )
    assert captured, "upsert_episode was not invoked"
    return captured[0], conn


# ---------------------------------------------------------------------------
# Title fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_title_uses_metadata_summary_when_present() -> None:
    """When instance metadata has a summary, it wins over event-level fields."""
    row = _make_row(
        metadata={"summary": "Standup"},
        event_title="Wrong Event Title",
        event_location="Conference Room A",
    )
    ep = await _project_one(row)
    assert ep.title == "Standup"


@pytest.mark.unit
async def test_title_falls_back_to_event_title_when_metadata_empty() -> None:
    """No summary in instance metadata → use the joined ``calendar_events.title``."""
    row = _make_row(
        metadata={},
        event_title="Sprint Planning",
        event_location="Zoom",
    )
    ep = await _project_one(row)
    assert ep.title == "Sprint Planning"


@pytest.mark.unit
async def test_title_falls_back_to_truncated_description() -> None:
    long_desc = (
        "This is a fairly long description that should be truncated to keep "
        "the projected episode title manageable for downstream consumers."
    )
    row = _make_row(
        metadata={},
        event_title=None,
        event_location=None,
        event_description=long_desc,
    )
    ep = await _project_one(row)
    assert ep.title is not None
    assert ep.title.startswith("This is a fairly long")
    assert len(ep.title) <= 80
    assert ep.title.endswith("…")


@pytest.mark.unit
async def test_title_final_fallback_when_no_richer_context() -> None:
    """All richer fields blank/whitespace → schema-qualified placeholder."""
    row = _make_row(
        metadata={},
        event_title="   ",  # whitespace-only must not win
        event_location="",
        event_description=None,
    )
    ep = await _project_one(row)
    assert ep.title == "butler_test: calendar block"


@pytest.mark.unit
async def test_episode_basic_fields() -> None:
    starts = _NOW - timedelta(hours=1)
    ends = _NOW
    row = _make_row(
        starts_at=starts,
        ends_at=ends,
        event_title="Lunch with Jordan",
    )
    ep = await _project_one(row)
    assert ep.source_name == SOURCE_NAME
    assert ep.episode_type == EPISODE_TYPE_SCHEDULED_BLOCK
    assert ep.start_at == starts
    assert ep.end_at == ends
    assert ep.title == "Lunch with Jordan"


async def test_freshly_projected_calendar_block_is_intent() -> None:
    """Regression (bu-66v7ff): the calendar adapter stamps layer=intent on the
    ongoing write path, not only on a one-time backfill.

    A calendar block is planned intent and must never be counted as lived time;
    relying on the conservative ``evidence`` column default would silently
    misclassify every freshly-projected calendar block.
    """
    row = _make_row(event_title="Sprint planning")
    ep = await _project_one(row)
    assert ep.layer == Layer.INTENT


# ---------------------------------------------------------------------------
# Butler-managed calendar exclusion (Track B defence-in-depth)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_butler_managed_source_kinds_includes_scheduler_and_reminders() -> None:
    """The documented butler-managed source kinds must be present in the constant."""
    assert "internal_scheduler" in BUTLER_MANAGED_SOURCE_KINDS
    assert "internal_reminders" in BUTLER_MANAGED_SOURCE_KINDS


def _make_pool_with_rows(rows: list[_Row] | None, *, table_exists: bool = True) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=table_exists)
    conn.fetch = AsyncMock(return_value=rows if rows is not None else [])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


@pytest.mark.unit
async def test_fetch_instances_sql_excludes_butler_lane_no_since() -> None:
    """The SQL emitted for the no-since path must contain the butler-lane guard."""
    pool = _make_pool_with_rows([])
    adapter = CalendarCompletedAdapter(butler_schemas=("test_schema",))

    now = datetime.now(UTC)
    await adapter._fetch_instances(pool, "test_schema", None, now)

    fetch_args = pool.acquire.return_value._obj.fetch.call_args[0]
    sql = fetch_args[0] if fetch_args else ""
    assert "cs.lane != 'butler'" in sql, (
        "Exclusion guard 'cs.lane != \\'butler\\'' must appear in the no-since SQL query"
    )
    assert "INNER JOIN" in sql.upper() or "JOIN" in sql.upper(), (
        "calendar_sources join must be present"
    )


@pytest.mark.unit
async def test_project_user_lane_rows_are_still_projected() -> None:
    """User-lane calendar events continue to be projected after the fix."""
    user_row = _make_row(event_title="Dentist appointment")

    adapter = CalendarCompletedAdapter(butler_schemas=("test_schema",))
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured.append(episode)
        return episode

    with (
        patch.object(
            adapter,
            "_fetch_instances",
            new=AsyncMock(return_value=[user_row]),
        ),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 1
    # episodes_closed must remain 0: a past calendar block is not evidence of
    # attendance.  Only rows_projected should be incremented (see bu-gnoi0).
    assert result.episodes_closed == 0
    assert len(captured) == 1
    assert captured[0].title == "Dentist appointment"


# ---------------------------------------------------------------------------
# Cross-schema fan-out collapse
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_project_collapses_same_origin_instance_across_schemas() -> None:
    """Same Google Calendar event in N schemas projects to ONE chronicler episode.

    Regression for the "five Labour Day bars" bug. The dedup key is
    ``origin_instance_ref`` alone (the upstream Google Calendar identifier).
    """
    shared_origin_ref = "evt:labour_day:2026-05-01T00:00:00Z"
    rows_by_schema = {
        "schema_a": [_make_row(event_title="Labour Day")],
        "schema_b": [_make_row(event_title="Labour Day")],
        "schema_c": [_make_row(event_title="Labour Day")],
    }
    for rows in rows_by_schema.values():
        for row in rows:
            row["origin_instance_ref"] = shared_origin_ref

    adapter = CalendarCompletedAdapter(
        butler_schemas=tuple(rows_by_schema.keys()),
    )
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured.append(episode)
        return episode

    async def _fake_fetch(_pool: object, schema: str, _since: object, _now: object) -> list[_Row]:
        return rows_by_schema[schema]

    with (
        patch.object(adapter, "_fetch_instances", new=AsyncMock(side_effect=_fake_fetch)),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 1, "Cross-schema fan-out must collapse to a single projection"
    assert len(captured) == 1
    assert captured[0].source_ref == f"calendar:{shared_origin_ref}"


@pytest.mark.unit
async def test_project_collapses_same_origin_under_multiple_event_ids_in_one_schema() -> None:
    """Two rows in ONE schema sharing origin_instance_ref collapse to a single episode.

    The unique constraint on calendar_event_instances is
    ``(event_id, origin_instance_ref)``, so the calendar sync can legitimately
    insert duplicate origin_instance_ref rows under different event_ids.
    Chronicler must still emit one episode.
    """
    shared_origin_ref = "evt:dup_event_ids:2026-05-01T07:00:00Z"
    row1 = _make_row(event_title="Daily standup")
    row2 = _make_row(event_title="Daily standup")
    row1["origin_instance_ref"] = shared_origin_ref
    row2["origin_instance_ref"] = shared_origin_ref
    row1["event_id"] = uuid4()
    row2["event_id"] = uuid4()

    adapter = CalendarCompletedAdapter(butler_schemas=("schema_only",))
    captured: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured.append(episode)
        return episode

    with (
        patch.object(
            adapter,
            "_fetch_instances",
            new=AsyncMock(return_value=[row1, row2]),
        ),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 1
    assert len(captured) == 1
    assert captured[0].source_ref == f"calendar:{shared_origin_ref}"


# ---------------------------------------------------------------------------
# episode_entities join table (bu-3zve1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_episode_entities_owner_only_when_table_absent() -> None:
    """When calendar_event_entities is absent, only the owner row is written.

    _fetch_event_entities returns {} when the table is missing (detected via
    information_schema.tables).  The adapter must degrade gracefully and write
    only the owner entity to episode_entities.
    """
    owner_id = uuid4()
    row = _make_row(event_title="Team sync")

    adapter = CalendarCompletedAdapter(butler_schemas=("butler_test",))
    episode_id = uuid4()
    captured_upserts: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        episode = Episode(
            id=episode_id,
            source_name=episode.source_name,
            source_ref=episode.source_ref,
            episode_type=episode.episode_type,
            start_at=episode.start_at,
            end_at=episode.end_at,
            precision=episode.precision,
            title=episode.title,
            payload=episode.payload,
            privacy=episode.privacy,
        )
        captured_upserts.append(episode)
        return episode

    cp, conn = _chronicler_pool_with_tracking()

    # Simulate calendar_event_entities absent by patching _fetch_event_entities
    # to return an empty mapping (table-absent path returns {}).
    with (
        patch.object(
            adapter,
            "_fetch_event_entities",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        await adapter._project_row(cp, "butler_test", row, entity_id=owner_id)

    assert captured_upserts, "upsert_episode was not called"

    # DELETE should have run for the episode.
    delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in str(c)]
    assert delete_calls, "DELETE FROM episode_entities was not called"

    # INSERT should have been called with owner entity only.
    insert_calls = conn.executemany.call_args_list
    assert len(insert_calls) == 1, f"Expected 1 executemany call, got {len(insert_calls)}"
    rows_inserted = insert_calls[0].args[1]
    assert len(rows_inserted) == 1, (
        f"Expected 1 row inserted (owner-only), got {len(rows_inserted)}"
    )
    ep_id_arg, entity_id_arg, role_arg = rows_inserted[0]
    assert ep_id_arg == episode_id
    assert entity_id_arg == owner_id
    assert role_arg == "owner"


@pytest.mark.unit
async def test_project_skips_schema_on_transient_fetch_event_entities_error() -> None:
    """A transient DB error in _fetch_event_entities causes the schema to be skipped.

    When _fetch_event_entities returns None (transient error), the adapter must
    NOT proceed to project rows and must NOT delete existing episode_entities.
    A warning is appended to the result instead.
    """
    row = _make_row(event_title="Weekly planning")

    adapter = CalendarCompletedAdapter(butler_schemas=("butler_test",))
    captured_upserts: list[Episode] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured_upserts.append(episode)
        return episode

    with (
        patch.object(
            adapter,
            "_fetch_instances",
            new=AsyncMock(return_value=[row]),
        ),
        patch.object(
            adapter,
            "_fetch_event_entities",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    # No episodes projected — schema was skipped.
    assert result.rows_projected == 0, "Schema must be skipped when fetch_event_entities fails"
    assert captured_upserts == [], "upsert_episode must not be called on transient error"
    # A warning must be recorded.
    assert any("butler_test" in w for w in result.warnings), (
        "A warning about the skipped schema must be appended to result.warnings"
    )


@pytest.mark.unit
async def test_episode_entities_owner_and_participants_when_table_present() -> None:
    """When join table is present, owner + participants are all written."""
    owner_id = uuid4()
    participant_a = uuid4()
    participant_b = uuid4()
    row = _make_row(event_title="All-hands")

    _, conn = await _project_one_tracked(
        row,
        entity_id=owner_id,
        participant_ids=[participant_a, participant_b],
    )

    # DELETE must run first.
    delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in str(c)]
    assert delete_calls, "DELETE FROM episode_entities was not called"

    # executemany must be called with 3 rows: owner + 2 participants.
    insert_calls = conn.executemany.call_args_list
    assert len(insert_calls) == 1, f"Expected 1 executemany call, got {len(insert_calls)}"
    rows_inserted = insert_calls[0].args[1]
    assert len(rows_inserted) == 3, (
        f"Expected 3 rows (owner + 2 participants), got {len(rows_inserted)}"
    )

    roles_by_entity = {eid: role for _, eid, role in rows_inserted}
    assert roles_by_entity[owner_id] == "owner"
    assert roles_by_entity[participant_a] == "participant"
    assert roles_by_entity[participant_b] == "participant"


@pytest.mark.unit
async def test_episode_entities_delete_then_insert_replaces_stale_attendees() -> None:
    """On a second adapter run, stale episode_entities rows are replaced.

    The adapter does DELETE-then-INSERT so upstream attendee removals
    (e.g. a participant was uninvited) propagate on the next run.  We simulate
    two consecutive calls to _project_row and verify the DELETE runs each time.
    """
    owner_id = uuid4()
    participant_old = uuid4()
    participant_new = uuid4()
    row = _make_row(event_title="Planning meeting")
    episode_id = uuid4()

    adapter = CalendarCompletedAdapter(butler_schemas=("butler_test",))

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        return Episode(
            id=episode_id,
            source_name=episode.source_name,
            source_ref=episode.source_ref,
            episode_type=episode.episode_type,
            start_at=episode.start_at,
            end_at=episode.end_at,
            precision=episode.precision,
            title=episode.title,
            payload=episode.payload,
            privacy=episode.privacy,
        )

    # First run: participant_old is an attendee.
    cp1, conn1 = _chronicler_pool_with_tracking()
    with patch("butlers.chronicler.adapters.calendar.upsert_episode", side_effect=_fake_upsert):
        await adapter._project_row(
            cp1, "butler_test", row, entity_id=owner_id, participant_ids=[participant_old]
        )

    # Second run: participant_old is gone; participant_new arrives.
    cp2, conn2 = _chronicler_pool_with_tracking()
    with patch("butlers.chronicler.adapters.calendar.upsert_episode", side_effect=_fake_upsert):
        await adapter._project_row(
            cp2, "butler_test", row, entity_id=owner_id, participant_ids=[participant_new]
        )

    # Both runs must have emitted a DELETE.
    for conn in (conn1, conn2):
        delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in str(c)]
        assert delete_calls, "DELETE FROM episode_entities was not called on this run"

    # Second run must write owner + participant_new (not participant_old).
    insert_calls2 = conn2.executemany.call_args_list
    assert len(insert_calls2) == 1
    rows2 = insert_calls2[0].args[1]
    entity_ids_written = {eid for _, eid, _ in rows2}
    assert participant_new in entity_ids_written, "participant_new must be in second run"
    assert participant_old not in entity_ids_written, (
        "participant_old was removed upstream and must not appear in second run"
    )


@pytest.mark.unit
async def test_episode_entities_idempotent_replay_no_duplicates() -> None:
    """Replaying the same row twice produces the same set — no duplicate rows.

    The DELETE-then-INSERT pattern ensures idempotency: running the adapter
    twice with the same attendee set produces exactly the same rows (no
    duplicates), because the DELETE clears all prior rows before re-inserting.
    """
    owner_id = uuid4()
    participant_id = uuid4()
    row = _make_row(event_title="Weekly 1:1")
    episode_id = uuid4()

    adapter = CalendarCompletedAdapter(butler_schemas=("butler_test",))

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        return Episode(
            id=episode_id,
            source_name=episode.source_name,
            source_ref=episode.source_ref,
            episode_type=episode.episode_type,
            start_at=episode.start_at,
            end_at=episode.end_at,
            precision=episode.precision,
            title=episode.title,
            payload=episode.payload,
            privacy=episode.privacy,
        )

    all_inserted_rows: list[list] = []

    for _ in range(2):
        cp, conn = _chronicler_pool_with_tracking()
        with patch("butlers.chronicler.adapters.calendar.upsert_episode", side_effect=_fake_upsert):
            await adapter._project_row(
                cp, "butler_test", row, entity_id=owner_id, participant_ids=[participant_id]
            )
        insert_calls = conn.executemany.call_args_list
        assert len(insert_calls) == 1
        all_inserted_rows.append(insert_calls[0].args[1])

    # Both runs must insert the same rows (same count, same entity_ids, same roles).
    rows_run1 = sorted((str(eid), role) for _, eid, role in all_inserted_rows[0])
    rows_run2 = sorted((str(eid), role) for _, eid, role in all_inserted_rows[1])
    assert rows_run1 == rows_run2, (
        "Idempotent replay must produce identical episode_entities rows on every run"
    )
    assert len(rows_run1) == 2, "Expected 2 rows (owner + participant)"


@pytest.mark.unit
async def test_owner_written_to_episode_entities_join_table() -> None:
    """The resolved owner entity is written to episode_entities with role='owner'.

    The derived ``episodes.entity_id`` column was dropped (bu-cfsgy); the owner
    now lives solely in the ``episode_entities`` join table.
    """
    owner_id = uuid4()
    participant_id = uuid4()
    row = _make_row(event_title="Kickoff")

    _, conn = await _project_one_tracked(
        row,
        entity_id=owner_id,
        participant_ids=[participant_id],
    )

    # The owner row in episode_entities must carry owner_id.
    insert_calls = conn.executemany.call_args_list
    assert insert_calls, "executemany was not called"
    rows_inserted = insert_calls[0].args[1]
    owner_rows = [(ep_id, eid, role) for ep_id, eid, role in rows_inserted if role == "owner"]
    assert len(owner_rows) == 1, "Expected exactly one 'owner' row in episode_entities"
    assert owner_rows[0][1] == owner_id, "episode_entities owner row must carry the resolved owner"


@pytest.mark.unit
async def test_episode_entities_role_precedence_collapse_owner_beats_participant() -> None:
    """When the same entity appears as both owner and participant, role='owner' wins.

    The calendar module may list the account owner as an attendee in
    calendar_event_entities.  The adapter must collapse the two signals to a
    single row with role='owner' (highest precedence), never 'participant'.
    """
    owner_id = uuid4()
    other_participant = uuid4()

    # owner_id appears in both the owner slot AND the participant_ids list.
    row = _make_row(event_title="Review session")
    episode, conn = await _project_one_tracked(
        row,
        entity_id=owner_id,
        participant_ids=[owner_id, other_participant],  # owner_id listed as participant too
    )

    insert_calls = conn.executemany.call_args_list
    assert insert_calls, "executemany was not called"
    rows_inserted = insert_calls[0].args[1]
    roles_by_entity = {eid: role for _, eid, role in rows_inserted}

    # owner_id must appear exactly once with role='owner'.
    assert owner_id in roles_by_entity, "owner_id must be in episode_entities"
    assert roles_by_entity[owner_id] == "owner", (
        f"Expected role='owner' for owner_id but got {roles_by_entity[owner_id]!r}"
    )
    # other_participant gets 'participant'.
    assert roles_by_entity[other_participant] == "participant"
    # Only 2 unique entities (no duplicate row for owner_id).
    assert len(rows_inserted) == 2, (
        f"Expected 2 rows (owner + other_participant), got {len(rows_inserted)}"
    )


# ---------------------------------------------------------------------------
# Attendance-semantics guard (bu-gnoi0)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_calendar_adapter_never_increments_episodes_closed() -> None:
    """CalendarCompletedAdapter must NOT increment episodes_closed for any row.

    A past calendar block (ends_at <= now) only proves the appointment was on
    the calendar — it is NOT evidence the user attended.  Incrementing
    episodes_closed would signal "completed/attended" to downstream LLM
    sessions, causing false attendance assertions in day-close routing.

    rows_projected is the only counter that should increase.
    """
    rows = [
        _make_row(event_title="Morning standup"),
        _make_row(event_title="Dr Ng heart followup"),
        _make_row(event_title="Team retro"),
    ]
    # Give each row a unique origin_instance_ref so they aren't deduped.
    for i, row in enumerate(rows):
        row["origin_instance_ref"] = f"evt:test:{i}"

    adapter = CalendarCompletedAdapter(butler_schemas=("schema_a",))

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        return episode

    with (
        patch.object(
            adapter,
            "_fetch_instances",
            new=AsyncMock(return_value=rows),
        ),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 3, "All three calendar rows must be projected"
    assert result.episodes_closed == 0, (
        "episodes_closed must stay 0 for calendar adapter — past calendar blocks "
        "are scheduled appointments, not confirmed attendances (bu-gnoi0)"
    )


@pytest.mark.unit
def test_day_close_prompt_prohibits_attendance_assertions() -> None:
    """The chronicler day-close prompt must explicitly prohibit attendance assertions.

    The butler.toml prompt drives the LLM session that generates the day-close
    routing text.  It must contain the explicit 'CALENDAR ATTENDANCE RULE'
    guard so the LLM does not infer attendance from a scheduled_block episode.
    """
    import tomllib
    from pathlib import Path

    toml_path = Path(__file__).parents[2] / "roster" / "chronicler" / "butler.toml"
    with toml_path.open("rb") as fh:
        cfg = tomllib.load(fh)

    day_close = next(
        (s for s in cfg["butler"]["schedule"] if s["name"] == "chronicler_day_close"),
        None,
    )
    assert day_close is not None, "chronicler_day_close schedule entry not found"
    prompt: str = day_close.get("prompt", "")

    assert "CALENDAR ATTENDANCE RULE" in prompt, (
        "Day-close prompt must contain 'CALENDAR ATTENDANCE RULE' guard (bu-gnoi0)"
    )
    assert "NOT evidence of attendance" in prompt, (
        "Day-close prompt must state that a past calendar block is NOT evidence of attendance"
    )
    assert "Calendar had X scheduled" in prompt, (
        "Day-close prompt must specify the correct 'Calendar had X scheduled' phrasing"
    )
