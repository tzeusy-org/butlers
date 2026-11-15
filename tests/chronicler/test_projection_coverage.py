"""Pure-logic tests for the OwnTracks projection-coverage ledger (bu-s11n0s.7, slice 2).

The real-Postgres proofs live in
``tests/integration/test_owntracks_retention_coverage_integration.py``; these pin the decision
rule of :class:`ProjectionCoverage` and the points adapter's movement-carry bookkeeping against
an in-memory stand-in for the two chronicler_032 tables.
"""

from __future__ import annotations

import hashlib
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
from butlers.chronicler.projection_coverage import (
    DISCOVERY_LOOKBACK,
    DISCOVERY_SETTLE,
    ProjectionCoverage,
    UncoveredScan,
    config_revision,
    discover_uncovered,
    raw_row_digest_sql,
    record_attempt,
)

pytestmark = pytest.mark.unit

_RETENTION = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)


REVISION = config_revision("owntracks.points", {"case": "unit"})
PLACE_REVISION = config_revision("owntracks.place_cluster", {"case": "unit"})


def digest_for(raw_id: UUID, version: int = 0) -> str:
    """A well-formed content digest; ``version`` stands for a re-written row."""
    return "v1:" + hashlib.sha256(f"{raw_id}:{version}".encode()).hexdigest()


class _Tables:
    """The open-member and coverage tables, keyed the way their primary keys are.

    ``coverage`` holds the latest-written record per raw id for the existing assertions;
    ``records`` is keyed by the full chronicler_034 primary key.
    """

    def __init__(self) -> None:
        self.open: dict[UUID, dict[str, Any]] = {}
        self.coverage: dict[UUID, dict[str, Any]] = {}
        self.records: dict[tuple[str, UUID, str, str], dict[str, Any]] = {}
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
                adapter, keys, raw_id, logical, retention, digest, events, episodes, batch = row
                assert raw_id not in self.open, "primary key violation"
                assert keys and all(keys), "carry_keys check violation"
                self.open[raw_id] = {
                    "adapter_name": adapter,
                    "carry_keys": list(keys),
                    "raw_source_id": raw_id,
                    "logical_source_key": logical,
                    "retention_at": retention,
                    "content_digest": digest,
                    "output_event_ids": list(events),
                    "output_episode_ids": list(episodes),
                    "joined_batch_id": batch,
                }
            else:
                assert (
                    "ON CONFLICT (adapter_name, raw_source_id, config_revision, content_digest) "
                    "DO NOTHING" in command
                )
                (
                    adapter,
                    raw_id,
                    revision,
                    digest,
                    logical,
                    retention,
                    disposition,
                    events,
                    episodes,
                    batch,
                ) = row
                key = (adapter, raw_id, revision, digest)
                if key in self.records:
                    continue
                self.records[key] = self.coverage[raw_id] = {
                    "config_revision": revision,
                    "content_digest": digest,
                    "logical_source_key": logical,
                    "retention_at": retention,
                    "disposition": disposition,
                    "output_event_ids": list(events),
                    "output_episode_ids": list(episodes),
                    "projection_batch_id": batch,
                }


def _row(**overrides: Any) -> dict[str, Any]:
    row_id = overrides.pop("id", uuid4())
    return {
        "id": row_id,
        "idempotency_key": f"owntracks:alice:{(row_id.int if row_id else 0) % 10_000}:location",
        "endpoint_identity": "owntracks:alice",
        "retention_at": _RETENTION,
        "content_digest": digest_for(row_id) if row_id else None,
        **overrides,
    }


def _ledger(revision: str = REVISION) -> ProjectionCoverage:
    return ProjectionCoverage("owntracks.points", config_revision=revision)


def _place_ledger() -> ProjectionCoverage:
    return ProjectionCoverage(
        "owntracks.place_cluster", config_revision=PLACE_REVISION, evaluates_every_row=True
    )


def test_unknown_adapter_has_no_coverage_contract() -> None:
    with pytest.raises(ValueError):
        ProjectionCoverage("google_calendar.completed", config_revision=REVISION)
    with pytest.raises(ValueError):
        config_revision("google_calendar.completed", {})


@pytest.mark.parametrize("revision", ["", "legacy", "v1:abc", REVISION.upper()])
def test_a_ledger_needs_a_well_formed_config_revision(revision: str) -> None:
    with pytest.raises(ValueError):
        ProjectionCoverage("owntracks.points", config_revision=revision)


def test_config_revision_is_deterministic_and_moves_with_any_setting() -> None:
    base = config_revision("owntracks.ssid_presence", {"mapping": "a", "gap": 60})
    assert base == config_revision("owntracks.ssid_presence", {"gap": 60, "mapping": "a"})
    assert base.startswith("v1:") and len(base) == 3 + 64
    assert base != config_revision("owntracks.ssid_presence", {"mapping": "b", "gap": 60})
    assert base != config_revision("owntracks.ssid_presence", {"mapping": "a", "gap": 61})
    assert base != config_revision("owntracks.place_cluster", {"mapping": "a", "gap": 60})


@pytest.mark.parametrize(
    "overrides",
    [
        {"retention_at": None},
        {"retention_at": datetime(2026, 3, 26, 10, 0)},  # naive
        {"idempotency_key": "  "},
        {"id": None},
        {"content_digest": None},
        {"content_digest": "legacy"},
        {"content_digest": "v2:" + "0" * 64},
        {"content_digest": "v1:" + "0" * 63},
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
    assert tables.open[row["id"]]["carry_keys"] == [key]
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


async def test_a_row_in_two_carries_waits_for_both_and_keeps_each_closing_episode() -> None:
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
    assert tables.open[seed["id"]]["carry_keys"] == sorted([movement, trail])

    episode = uuid4()
    closing = _ledger()
    closing.release_prefix(TRAIL_CARRY_PREFIX)
    closing.hold(seed["id"], trail)  # still the trail seed
    closing.close_carry(movement, episode)
    await closing.flush(tables)
    assert tables.open[seed["id"]]["carry_keys"] == [trail]
    assert tables.open[seed["id"]]["output_episode_ids"] == [episode]
    assert tables.coverage == {}

    resolved = _ledger()
    resolved.release_prefix(TRAIL_CARRY_PREFIX)
    await resolved.flush(tables)
    assert seed["id"] not in tables.open
    assert tables.coverage[seed["id"]]["output_episode_ids"] == [episode]


async def test_closing_one_of_two_persistent_carries_keeps_the_row_open() -> None:
    tables = _Tables()
    stay, trip = "place:owntracks:alice:1", "away:owntracks:alice:1"
    row = _row()
    first = _place_ledger()
    first.observe(row)
    first.hold(row["id"], stay)
    first.hold(row["id"], trip)
    await first.flush(tables)

    stay_episode, trip_episode = uuid4(), uuid4()
    stay_closes = _place_ledger()
    stay_closes.close_carry(stay, stay_episode)
    await stay_closes.flush(tables)
    assert tables.open[row["id"]]["carry_keys"] == [trip]
    assert tables.coverage == {}

    trip_closes = _place_ledger()
    trip_closes.close_carry(trip, trip_episode)
    await trip_closes.flush(tables)
    record = tables.coverage[row["id"]]
    assert record["disposition"] == "projected"
    assert set(record["output_episode_ids"]) == {stay_episode, trip_episode}


async def test_carry_output_reaches_stored_members_while_the_carry_stays_open() -> None:
    tables = _Tables()
    key = "place:owntracks:alice:1"
    early = _row()
    first = _place_ledger()
    first.observe(early)
    first.hold(early["id"], key)
    await first.flush(tables)
    assert tables.open[early["id"]]["output_episode_ids"] == []

    episode = uuid4()
    reached = _place_ledger()
    reached.carry_output(key, episode)
    await reached.flush(tables)
    assert tables.open[early["id"]]["carry_keys"] == [key]
    assert tables.open[early["id"]]["output_episode_ids"] == [episode]
    assert tables.coverage == {}


async def test_output_for_a_stored_member_applies_only_to_a_stored_member() -> None:
    tables = _Tables()
    key = "departure:owntracks:alice"
    member, stranger = _row(), _row()
    first = _place_ledger()
    first.observe(member)
    first.hold(member["id"], key)
    await first.flush(tables)

    episode = uuid4()
    later = _place_ledger()
    later.record_episode([{"id": member["id"]}, {"id": stranger["id"]}], episode)
    later.close_carry(key)
    await later.flush(tables)
    assert tables.coverage[member["id"]]["output_episode_ids"] == [episode]
    assert stranger["id"] not in tables.coverage


async def test_an_evaluating_adapter_records_a_finished_row_without_output() -> None:
    tables = _Tables()
    row, held = _row(), _row()
    ledger = _place_ledger()
    ledger.observe(row)
    ledger.observe(held)
    ledger.hold(held["id"], "place:owntracks:alice:1")
    summary = await ledger.flush(tables)
    record = tables.coverage[row["id"]]
    assert record["disposition"] == "evaluated_no_output"
    assert record["output_event_ids"] == [] and record["output_episode_ids"] == []
    assert held["id"] not in tables.coverage
    assert summary.evaluated == 1

    closing = _place_ledger()
    closing.close_carry("place:owntracks:alice:1")
    await closing.flush(tables)
    assert tables.coverage[held["id"]]["disposition"] == "evaluated_no_output"


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


async def test_a_record_names_the_revision_and_the_content_it_was_decided_for() -> None:
    tables = _Tables()
    row = _row()
    ledger = _ledger()
    ledger.observe(row)
    ledger.record_event(row, uuid4())
    await ledger.flush(tables)
    record = tables.coverage[row["id"]]
    assert record["config_revision"] == REVISION
    assert record["content_digest"] == row["content_digest"]


async def test_a_held_row_closes_for_the_content_it_was_read_with() -> None:
    tables = _Tables()
    key = f"{MOVEMENT_CARRY_PREFIX}owntracks:alice"
    row = _row()
    first = _ledger()
    first.observe(row)
    first.record_event(row, uuid4())
    first.hold(row["id"], key)
    await first.flush(tables)
    assert tables.open[row["id"]]["content_digest"] == row["content_digest"]

    closing = _ledger()
    closing.close_carry(key, uuid4())
    await closing.flush(tables)
    assert tables.coverage[row["id"]]["content_digest"] == row["content_digest"]


async def test_a_new_revision_or_new_content_gets_its_own_record_and_keeps_the_old() -> None:
    tables = _Tables()
    row = _row()
    for ledger, read in (
        (_ledger(), row),
        (_ledger(), row),  # same revision and content: first record wins
        (_ledger(config_revision("owntracks.points", {"case": "changed"})), row),
        (_ledger(), {**row, "content_digest": digest_for(row["id"], version=1)}),
    ):
        ledger.observe(read)
        ledger.record_event(read, uuid4())
        await ledger.flush(tables)
    assert {(key[2], key[3]) for key in tables.records} == {
        (REVISION, row["content_digest"]),
        (config_revision("owntracks.points", {"case": "changed"}), row["content_digest"]),
        (REVISION, digest_for(row["id"], version=1)),
    }


async def test_a_reset_carry_member_not_read_again_leaves_uncovered() -> None:
    tables = _Tables()
    key = "ssid:owntracks:alice:2026-03-26T08:00:00+00:00"
    row = _row()
    first = _place_ledger()
    first.observe(row)
    first.record_episode([row], uuid4())
    first.hold(row["id"], key)
    await first.flush(tables)

    reset = _place_ledger()
    reset.reset_prefix("ssid:")
    reset.close_carry(key, uuid4())  # a void carry closes nothing
    summary = await reset.flush(tables)
    assert row["id"] not in tables.open
    assert tables.coverage == {}, "an evaluating adapter must not credit a row it did not read"
    assert summary.discarded == 1


async def test_a_reset_carry_member_read_again_is_decided_without_its_old_outputs() -> None:
    tables = _Tables()
    key = "ssid:owntracks:alice:2026-03-26T08:00:00+00:00"
    evaluated, rebuilt = _row(), _row()
    old_episode, new_episode = uuid4(), uuid4()
    first = _place_ledger()
    for row in (evaluated, rebuilt):
        first.observe(row)
        first.hold(row["id"], key)
    first.record_episode([evaluated, rebuilt], old_episode)
    await first.flush(tables)

    replay = _place_ledger()
    replay.reset_prefix("ssid:")
    replay.observe(evaluated)  # now unmapped: no output
    replay.observe(rebuilt)
    replay.record_episode([rebuilt], new_episode)
    replay.hold(rebuilt["id"], "ssid:owntracks:alice:2026-03-26T09:00:00+00:00")
    summary = await replay.flush(tables)
    record = tables.coverage[evaluated["id"]]
    assert record["disposition"] == "evaluated_no_output"
    assert record["output_episode_ids"] == []
    member = tables.open[rebuilt["id"]]
    assert member["output_episode_ids"] == [new_episode]
    assert member["joined_batch_id"] == replay.batch_id
    assert summary.discarded == 0


async def test_a_released_prefix_does_not_void_outputs_like_a_reset() -> None:
    tables = _Tables()
    key = f"{TRAIL_CARRY_PREFIX}owntracks:alice"
    row, event = _row(), uuid4()
    first = _ledger()
    first.observe(row)
    first.record_event(row, event)
    first.hold(row["id"], key)
    await first.flush(tables)

    released = _ledger()
    released.release_prefix(TRAIL_CARRY_PREFIX)
    await released.flush(tables)
    assert tables.coverage[row["id"]]["output_event_ids"] == [event]


class _RunRecords:
    """Captures the statements the run-level helpers send."""

    def __init__(self, found: int = 0) -> None:
        self.found = found
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.calls.append((query, args))
        return [{"id": uuid4()} for _ in range(min(self.found, args[-1]))]

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        return "INSERT 0 1"


async def test_a_failed_attempt_commits_only_the_exception_class() -> None:
    conn = _RunRecords()
    await record_attempt(
        conn, "owntracks.points", config_revision=REVISION, error_class="TimeoutError"
    )
    ((query, args),) = conn.calls
    assert "location_projection_attempts" in query
    assert args == ("owntracks.points", REVISION, "failed", "TimeoutError", None, None)


async def test_a_successful_attempt_carries_the_discovery_count() -> None:
    conn = _RunRecords()
    await record_attempt(
        conn,
        "owntracks.points",
        config_revision=REVISION,
        uncovered=UncoveredScan(count=3, capped=False),
    )
    ((_query, args),) = conn.calls
    assert args == ("owntracks.points", REVISION, "succeeded", None, 3, False)
    with pytest.raises(ValueError):
        await record_attempt(conn, "google_calendar.completed", config_revision=REVISION)


@pytest.mark.parametrize(("found", "expected"), [(0, (0, False)), (4, (4, False)), (5, (4, True))])
async def test_discovery_counts_up_to_its_limit_and_says_when_it_stopped(
    found: int, expected: tuple[int, bool]
) -> None:
    conn = _RunRecords(found=found)
    scan = await discover_uncovered(
        conn, "owntracks.place_cluster", config_revision=PLACE_REVISION, limit=4
    )
    assert (scan.count, scan.capped) == expected
    ((query, args),) = conn.calls
    # Exact matching: revision and the digest of the row as stored, open carries excluded.
    assert "c.config_revision = $2" in query
    assert f"c.content_digest = {raw_row_digest_sql('p')}" in query
    assert "location_projection_open_members" in query
    assert args == (
        "owntracks.place_cluster",
        PLACE_REVISION,
        DISCOVERY_SETTLE,
        DISCOVERY_LOOKBACK,
        5,
    )


def test_the_digest_sql_hashes_every_raw_column_as_stored() -> None:
    sql = raw_row_digest_sql("p")
    for column in (
        "p.id::text",
        "p.idempotency_key",
        "p.endpoint_identity",
        "extract(epoch FROM p.ts)",
        "extract(epoch FROM p.recorded_at)",
        "extract(epoch FROM p.retention_at)",
        "p.lat",
        "p.lon",
        "p.accuracy",
        "p.trigger",
        "p.event",
        "p.raw_payload",
    ):
        assert column in sql
    assert sql.startswith("('v1:' || encode(sha256(")
    assert "p." not in raw_row_digest_sql()


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
    assert ledger._holds == {fresh[0]["id"]: {key}}
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
    assert extended._closed == {} and extended._holds == {rows[0]["id"]: {key}}

    stored_episode = uuid4()
    replaced = _ledger()
    replaced.observe(rows[0])
    OwnTracksPointAdapter._record_movement_coverage(
        replaced,
        [_segment(rows, extends=False, episode=None)],
        {"owntracks:alice": {"episode_id": str(stored_episode)}},
    )
    assert replaced._closed == {key: {stored_episode}}
