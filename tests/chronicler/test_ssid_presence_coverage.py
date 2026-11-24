"""Projection coverage of the OwnTracks SSID-presence adapter (bu-s11n0s.7, slice 2, PR-B).

Each test runs the adapter's own pure grouping (``group_ssid_points``) and its coverage
bookkeeping (``record_presence_coverage``) over consecutive runs, flushing into the in-memory
stand-in for the coverage tables. Every hold path has a test: the open presence span, the open
singleton (both resolutions), an endpoint not read this run, and a replay from the start (a
rebuilt span and one the replay no longer builds). The real-Postgres proofs live in
``tests/integration/test_owntracks_retention_coverage_integration.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest

from butlers.chronicler.adapters.owntracks_ssid import (
    PRESENCE_CARRY_PREFIX,
    OwnTracksSsidPresenceAdapter,
    SsidPresenceSpan,
    group_ssid_points,
    presence_carry_key,
    record_presence_coverage,
)
from butlers.chronicler.projection_coverage import ProjectionCoverage, config_revision
from tests.chronicler.test_projection_coverage import _Tables, digest_for

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 3, 26, 8, 0, tzinfo=UTC)
_EP = "owntracks:alice"
_OTHER_EP = "owntracks:bob"
_PLACES = {"home-wifi": "home", "office-wifi": "office"}


def _fix(minutes: float, ssid: str | None = "home-wifi", endpoint: str = _EP) -> dict[str, Any]:
    raw_id = uuid4()
    return {
        "id": raw_id,
        "idempotency_key": f"{endpoint}:{raw_id.int % 10**9}:location",
        "retention_at": _T0 + timedelta(minutes=minutes),
        "content_digest": digest_for(raw_id),
        "ts": _T0 + timedelta(minutes=minutes),
        "endpoint_identity": endpoint,
        "ssid": ssid,
    }


def _revision(places: dict[str, str]) -> str:
    # Stands in for the adapter's revision (the fixture's "office" is not an adapter place).
    return config_revision("owntracks.ssid_presence", {"places": dict(places)})


def _episode(span: SsidPresenceSpan) -> UUID:
    # The presence upsert is idempotent by source_ref: a re-emitted span keeps its id.
    return uuid5(NAMESPACE_URL, span.source_ref())


class _Adapter:
    """The coverage half of one adapter run per call, over the adapter's pure functions."""

    def __init__(self) -> None:
        self.tables = _Tables()
        self.carry: dict[str, Any] = {}
        self.places = dict(_PLACES)
        self.episodes: dict[str, UUID] = {}

    async def run(
        self, *fixes: dict[str, Any], replay: bool = False, mapping_changed: bool = False
    ) -> None:
        rows = sorted(fixes, key=lambda row: row["ts"])
        prior = {} if replay or mapping_changed else dict(self.carry)
        open_spans: dict[str, SsidPresenceSpan] = {}
        spans, carry = group_ssid_points(
            rows,
            ssid_places=self.places,
            max_gap=timedelta(minutes=30),
            prior_carryover=prior,
            open_spans=open_spans,
        )
        episodes = {span.source_ref(): _episode(span) for span in spans}
        self.episodes.update(episodes)
        ledger = ProjectionCoverage(
            "owntracks.ssid_presence",
            config_revision=_revision(self.places),
            evaluates_every_row=True,
        )
        if mapping_changed:
            ledger.reset_prefix(PRESENCE_CARRY_PREFIX)
        for row in rows:
            ledger.observe(row)
        record_presence_coverage(
            ledger,
            spans=spans,
            episodes=episodes,
            open_spans=open_spans,
            prior_carryover=prior,
            present={row["endpoint_identity"] for row in rows},
        )
        await ledger.flush(self.tables)
        self.carry = carry

    def keys(self, fix: dict[str, Any]) -> list[str] | None:
        member = self.tables.open.get(fix["id"])
        return member["carry_keys"] if member is not None else None

    def record(self, fix: dict[str, Any]) -> dict[str, Any] | None:
        return self.tables.coverage.get(fix["id"])


def _key(start_minutes: float, endpoint: str = _EP) -> str:
    return presence_carry_key(endpoint, _T0 + timedelta(minutes=start_minutes))


def _presence_episode(start_minutes: float, ssid: str = "home-wifi") -> UUID:
    span = SsidPresenceSpan(
        endpoint_identity=_EP,
        ssid=ssid,
        place=_PLACES[ssid],
        start_at=_T0 + timedelta(minutes=start_minutes),
        end_at=_T0 + timedelta(minutes=start_minutes),
        point_count=2,
    )
    return _episode(span)


async def test_an_open_presence_span_holds_its_members_and_a_closed_one_covers_them() -> None:
    adapter = _Adapter()
    home = [_fix(0), _fix(10), _fix(20)]
    await adapter.run(*home)
    episode = _presence_episode(0)
    for fix in home:
        assert adapter.keys(fix) == [_key(0)]
        assert adapter.tables.open[fix["id"]]["output_episode_ids"] == [episode]
        assert adapter.record(fix) is None

    # The owner joins another network: the carried span closes with its episode.
    office = _fix(25, "office-wifi")
    await adapter.run(office)
    for fix in home:
        record = adapter.record(fix)
        assert record is not None and record["disposition"] == "projected"
        assert record["output_episode_ids"] == [episode]
        assert adapter.keys(fix) is None
    assert adapter.keys(office) == [_key(25)] and adapter.record(office) is None


async def test_an_open_singleton_is_held_then_evaluated_when_it_never_grows() -> None:
    adapter = _Adapter()
    lone = _fix(0)
    await adapter.run(lone)
    assert adapter.keys(lone) == [_key(0)]
    assert adapter.tables.open[lone["id"]]["output_episode_ids"] == []

    # The next fix is past the gap: the singleton never became presence.
    later = _fix(120)
    await adapter.run(later)
    record = adapter.record(lone)
    assert record is not None and record["disposition"] == "evaluated_no_output"
    assert adapter.keys(later) == [_key(120)]


async def test_a_singleton_that_grows_into_presence_gains_the_episode_while_open() -> None:
    adapter = _Adapter()
    lone = _fix(0)
    await adapter.run(lone)

    second = _fix(10)
    await adapter.run(second)  # the span now has two observations, still open
    episode = _presence_episode(0)
    for fix in (lone, second):
        assert adapter.keys(fix) == [_key(0)]
        assert adapter.tables.open[fix["id"]]["output_episode_ids"] == [episode]
        assert adapter.record(fix) is None

    await adapter.run(_fix(15, None))  # a fix with no network ends the span
    for fix in (lone, second):
        record = adapter.record(fix)
        assert record is not None and record["output_episode_ids"] == [episode]


async def test_unmapped_and_missing_networks_are_evaluated_without_output() -> None:
    adapter = _Adapter()
    unmapped, missing = _fix(0, "cafe-wifi"), _fix(5, None)
    await adapter.run(unmapped, missing)
    for fix in (unmapped, missing):
        record = adapter.record(fix)
        assert record is not None and record["disposition"] == "evaluated_no_output"
        assert adapter.keys(fix) is None


async def test_an_endpoint_absent_from_a_run_keeps_its_carry() -> None:
    adapter = _Adapter()
    home = [_fix(0), _fix(10)]
    await adapter.run(*home)

    await adapter.run(_fix(15, "office-wifi", _OTHER_EP), _fix(20, None, _OTHER_EP))
    for fix in home:
        assert adapter.keys(fix) == [_key(0)] and adapter.record(fix) is None


async def test_a_replay_that_rebuilds_and_ends_a_span_closes_its_stored_carry() -> None:
    adapter = _Adapter()
    home = [_fix(0), _fix(10)]
    await adapter.run(*home)

    # A cursor rebuild drops the carryover and re-reads from the start.
    await adapter.run(*home, _fix(15, None), replay=True)
    for fix in home:
        record = adapter.record(fix)
        assert record is not None and record["disposition"] == "projected"
        assert record["output_episode_ids"] == [_presence_episode(0)]
        assert adapter.keys(fix) is None


async def test_a_replay_ending_another_span_never_releases_a_stored_open_span() -> None:
    # A carry is scoped to one span (endpoint and start): the replay ends an earlier span at the
    # same endpoint, which must not cover members of the still-open span stored before it.
    adapter = _Adapter()
    later = [_fix(30), _fix(40)]
    await adapter.run(*later)
    assert all(adapter.keys(fix) == [_key(30)] for fix in later)

    earlier = [_fix(0), _fix(10)]
    await adapter.run(*earlier, _fix(15, None), replay=True)
    assert all(adapter.record(fix) is not None for fix in earlier)
    for fix in later:
        assert adapter.keys(fix) == [_key(30)] and adapter.record(fix) is None


async def test_a_cursor_replay_that_no_longer_builds_a_stored_span_keeps_it_held() -> None:
    adapter = _Adapter()
    home = [_fix(0), _fix(10)]
    await adapter.run(*home)

    # Same mapping, rebuilt cursor, but the replay only reaches the network-less fix: the stored
    # carry was not rebuilt, so its members stay held rather than covered.
    await adapter.run(_fix(-5, None), replay=True)
    for fix in home:
        assert adapter.keys(fix) == [_key(0)] and adapter.record(fix) is None


async def test_a_mapping_change_re_decides_held_fixes_under_the_new_revision() -> None:
    adapter = _Adapter()
    home = [_fix(0), _fix(10)]
    await adapter.run(*home)

    # The owner unmaps the network: the replay reads the fixes again and evaluates them as
    # unmapped, under the new revision; the old presence episode is not credited.
    adapter.places.pop("home-wifi")
    new_revision = _revision(adapter.places)
    await adapter.run(*home, mapping_changed=True)
    for fix in home:
        record = adapter.record(fix)
        assert adapter.keys(fix) is None
        assert record is not None and record["disposition"] == "evaluated_no_output"
        assert record["config_revision"] == new_revision


async def test_a_mapping_change_leaves_a_held_fix_the_replay_has_not_reached_uncovered() -> None:
    adapter = _Adapter()
    home = [_fix(0), _fix(10)]
    await adapter.run(*home)

    adapter.places["home-wifi"] = "work"
    # A batch-limited replay reads only an earlier fix this run.
    await adapter.run(_fix(-5, "office-wifi"), mapping_changed=True)
    for fix in home:
        assert adapter.keys(fix) is None and adapter.record(fix) is None


def test_the_adapter_revision_includes_the_owner_mapping_and_the_grouping_settings() -> None:
    base = OwnTracksSsidPresenceAdapter(ssid_places={"home-wifi": "home"}).coverage_revision
    same = OwnTracksSsidPresenceAdapter(ssid_places={"home-wifi": "Home "}).coverage_revision
    assert base == same, "the mapping is compared after normalization"
    for other in (
        OwnTracksSsidPresenceAdapter(ssid_places={"home-wifi": "work"}),
        OwnTracksSsidPresenceAdapter(ssid_places={}),
        OwnTracksSsidPresenceAdapter(ssid_places={"home-wifi": "home"}, max_gap_minutes=30),
    ):
        assert other.coverage_revision != base
