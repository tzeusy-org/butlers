"""Pure-logic tests for the OwnTracks projection-coverage ledger (bu-s11n0s.7, slice 2).

The real-Postgres proofs live in
``tests/integration/test_owntracks_retention_coverage_integration.py``; these pin the decision
rule of :class:`ProjectionCoverage` and the points adapter's movement-carry bookkeeping against
an in-memory stand-in for the two chronicler_032 tables.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from butlers.chronicler.adapters.owntracks import (
    MOVEMENT_CARRY_PREFIX,
    TRAIL_CARRY_PREFIX,
    OwnTracksPointAdapter,
)
from butlers.chronicler.projection_coverage import ProjectionCoverage

pytestmark = pytest.mark.unit

_RETENTION = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)


class _Tables:
    """The open-member and coverage tables, keyed the way their primary keys are."""

    def __init__(self) -> None:
        self.open: dict[UUID, dict[str, Any]] = {}
        self.coverage: dict[UUID, dict[str, Any]] = {}
        self.writes = 0

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        assert query.startswith("SELECT") and "location_projection_open_members" in query
        (adapter,) = args
        return [dict(row) for row in self.open.values() if row["adapter_name"] == adapter]

    async def execute(self, query: str, *args: Any) -> str:
        assert query.startswith("DELETE FROM location_projection_open_members")
        _adapter, ids = args
        self.writes += 1
        for raw_id in ids:
            self.open.pop(raw_id)
        return f"DELETE {len(ids)}"

    async def executemany(self, command: str, args: Iterable[Sequence[Any]]) -> None:
        self.writes += 1
        for row in args:
            if command.startswith("INSERT INTO location_projection_open_members"):
                adapter, key, raw_id, logical, retention, events, episodes, batch = row
                assert raw_id not in self.open, "primary key violation"
                self.open[raw_id] = {
                    "adapter_name": adapter,
                    "carry_key": key,
                    "raw_source_id": raw_id,
                    "logical_source_key": logical,
                    "retention_at": retention,
                    "output_event_ids": list(events),
                    "output_episode_ids": list(episodes),
                    "joined_batch_id": batch,
                }
            else:
                assert "ON CONFLICT (adapter_name, raw_source_id) DO NOTHING" in command
                adapter, raw_id, logical, retention, disposition, events, episodes, batch = row
                self.coverage.setdefault(
                    raw_id,
                    {
                        "logical_source_key": logical,
                        "retention_at": retention,
                        "disposition": disposition,
                        "output_event_ids": list(events),
                        "output_episode_ids": list(episodes),
                        "projection_batch_id": batch,
                    },
                )


def _row(**overrides: Any) -> dict[str, Any]:
    row_id = uuid4()
    return {
        "id": row_id,
        "idempotency_key": f"owntracks:alice:{row_id.int % 10_000}:location",
        "endpoint_identity": "owntracks:alice",
        "retention_at": _RETENTION,
        **overrides,
    }


def _ledger() -> ProjectionCoverage:
    return ProjectionCoverage("owntracks.points")


def test_unknown_adapter_has_no_coverage_contract() -> None:
    with pytest.raises(ValueError):
        ProjectionCoverage("google_calendar.completed")


@pytest.mark.parametrize(
    "overrides",
    [
        {"retention_at": None},
        {"retention_at": datetime(2026, 3, 26, 10, 0)},  # naive
        {"idempotency_key": "  "},
        {"id": None},
    ],
)
async def test_a_row_read_without_a_trustworthy_identity_or_clock_is_never_covered(
    overrides: dict[str, Any],
) -> None:
    tables = _Tables()
    ledger = _ledger()
    row = _row(**overrides)
    assert ledger.observe(row) is False
    ledger.record_event(row, uuid4())
    await ledger.flush(tables)
    assert tables.coverage == {} and tables.open == {}


async def test_terminal_row_is_projected_with_exactly_its_own_outputs() -> None:
    tables = _Tables()
    ledger = _ledger()
    row, other = _row(), _row()
    event, episode, unrelated = uuid4(), uuid4(), uuid4()
    ledger.observe(row)
    ledger.observe(other)
    ledger.record_event(row, event)
    ledger.record_episode([row], episode)
    ledger.record_event({"id": uuid4()}, unrelated)  # never observed: ignored
    await ledger.flush(tables)

    record = tables.coverage[row["id"]]
    assert record["disposition"] == "projected"
    assert record["output_event_ids"] == [event]
    assert record["output_episode_ids"] == [episode]
    assert record["retention_at"] == _RETENTION
    assert record["logical_source_key"] == row["idempotency_key"]
    assert record["projection_batch_id"] == ledger.batch_id
    assert other["id"] not in tables.coverage, "a row with no output is never covered"


async def test_held_row_waits_open_until_its_carry_closes_with_the_carry_episode() -> None:
    tables = _Tables()
    key = f"{MOVEMENT_CARRY_PREFIX}owntracks:alice"
    row, event = _row(), uuid4()
    first = _ledger()
    first.observe(row)
    first.record_event(row, event)
    first.hold(row["id"], key)
    await first.flush(tables)
    assert tables.coverage == {}
    assert tables.open[row["id"]]["carry_key"] == key
    assert tables.open[row["id"]]["output_event_ids"] == [event]

    # A run that neither closes nor releases the carry leaves the member untouched.
    writes = tables.writes
    idle = _ledger()
    idle.observe(other := _row())
    idle.record_event(other, uuid4())
    await idle.flush(tables)
    assert row["id"] in tables.open and row["id"] not in tables.coverage
    assert tables.writes == writes + 1  # only the unrelated row's coverage

    episode = uuid4()
    closing = _ledger()
    closing.close_carry(key, episode)
    await closing.flush(tables)
    assert row["id"] not in tables.open
    record = tables.coverage[row["id"]]
    assert record["output_event_ids"] == [event]
    assert record["output_episode_ids"] == [episode]
    assert record["projection_batch_id"] == closing.batch_id


async def test_released_trail_member_is_covered_only_when_not_held_again() -> None:
    tables = _Tables()
    key = f"{TRAIL_CARRY_PREFIX}owntracks:alice"
    seed = _row()
    first = _ledger()
    first.observe(seed)
    first.record_event(seed, uuid4())
    first.hold(seed["id"], key)
    await first.flush(tables)

    still_seed = _ledger()
    still_seed.release_prefix(TRAIL_CARRY_PREFIX)
    still_seed.hold(seed["id"], key)
    await still_seed.flush(tables)
    assert seed["id"] in tables.open and tables.coverage == {}

    resolved = _ledger()
    resolved.release_prefix(TRAIL_CARRY_PREFIX)
    await resolved.flush(tables)
    assert seed["id"] not in tables.open
    assert tables.coverage[seed["id"]]["disposition"] == "projected"


async def test_movement_carry_wins_over_trail_so_its_closing_episode_is_attached() -> None:
    tables = _Tables()
    movement = f"{MOVEMENT_CARRY_PREFIX}owntracks:alice"
    trail = f"{TRAIL_CARRY_PREFIX}owntracks:alice"
    seed = _row()
    first = _ledger()
    first.observe(seed)
    first.record_event(seed, uuid4())
    first.hold(seed["id"], trail)
    first.hold(seed["id"], movement)
    await first.flush(tables)
    assert tables.open[seed["id"]]["carry_key"] == movement

    episode = uuid4()
    closing = _ledger()
    closing.release_prefix(TRAIL_CARRY_PREFIX)
    closing.hold(seed["id"], trail)  # still the trail seed
    closing.close_carry(movement, episode)
    await closing.flush(tables)
    assert tables.open[seed["id"]]["carry_key"] == trail
    assert tables.open[seed["id"]]["output_episode_ids"] == [episode]
    assert tables.coverage == {}


async def test_first_terminal_record_wins() -> None:
    tables = _Tables()
    row, first_event = _row(), uuid4()
    first = _ledger()
    first.observe(row)
    first.record_event(row, first_event)
    await first.flush(tables)
    replay = _ledger()
    replay.observe(row)
    replay.record_event(row, uuid4())
    await replay.flush(tables)
    assert tables.coverage[row["id"]]["output_event_ids"] == [first_event]


def _segment(rows: list[dict], *, extends: bool, episode: UUID | None, late: bool = False) -> dict:
    return {
        "rows": rows,
        "source_ref": "connectors.owntracks_points:movement:owntracks:alice:1" if extends else None,
        "episode_id": episode,
        "late": late,
    }


def test_points_adapter_closes_an_extended_carry_with_the_extension_episode() -> None:
    ledger = _ledger()
    extension, fresh, late = [_row(), _row()], [_row()], [_row()]
    for row in (*extension, *fresh, *late):
        ledger.observe(row)
    extension_episode, late_episode = uuid4(), uuid4()
    OwnTracksPointAdapter._record_movement_coverage(
        ledger,
        [
            _segment(extension, extends=True, episode=extension_episode),
            _segment(fresh, extends=False, episode=None),
            _segment(late, extends=False, episode=late_episode, late=True),
        ],
        {"owntracks:alice": {"episode_id": str(uuid4())}},
    )
    key = f"{MOVEMENT_CARRY_PREFIX}owntracks:alice"
    assert ledger._closed == {key: {extension_episode}}
    assert ledger._holds == {fresh[0]["id"]: key}
    assert ledger._rows[late[0]["id"]].episode_ids == {late_episode}
    assert all(ledger._rows[r["id"]].episode_ids == {extension_episode} for r in extension)


def test_points_adapter_keeps_a_still_open_extended_carry_and_closes_a_replaced_one() -> None:
    key = f"{MOVEMENT_CARRY_PREFIX}owntracks:alice"
    extended = _ledger()
    rows = [_row()]
    extended.observe(rows[0])
    OwnTracksPointAdapter._record_movement_coverage(
        extended, [_segment(rows, extends=True, episode=uuid4())], {}
    )
    assert extended._closed == {} and extended._holds == {rows[0]["id"]: key}

    stored_episode = uuid4()
    replaced = _ledger()
    replaced.observe(rows[0])
    OwnTracksPointAdapter._record_movement_coverage(
        replaced,
        [_segment(rows, extends=False, episode=None)],
        {"owntracks:alice": {"episode_id": str(stored_episode)}},
    )
    assert replaced._closed == {key: {stored_episode}}
