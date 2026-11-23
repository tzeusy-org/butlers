"""Legacy ``user_context`` versus perception shadow parity (bu-dhloi1.21 AC4).

The comparator is pure: legacy context entries and owner situation reads in,
one closed-vocabulary receipt per kind out. The runner is dark and reads the
perception side exactly as ``/now`` does.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from butlers.context_bus import ContextEntry
from butlers.perception import flags, legacy_parity
from butlers.perception.legacy_parity import (
    PARITY_KINDS,
    REASONS,
    LegacyParity,
    LegacyParityDark,
    LegacyParityUnavailable,
    compare,
    log_line,
)
from butlers.perception.model import ACTIVITIES, STATES
from butlers.perception.situation import Audience, SituationRead

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
OWNER = UUID("00000000-0000-4000-8000-000000000001")
PLACE_REF = UUID("00000000-0000-4000-8000-0000000000c1")
LOCALITY_REF = UUID("00000000-0000-4000-8000-0000000000c2")
RECORD_REF = UUID("00000000-0000-4000-8000-0000000000c3")
ROOM = "Master Bedroom"
TITLE = "Dentist with Dr Ortega"


def _entry(signal: str, value: str | None = None, *, expires=NOW + timedelta(hours=1), meta=None):
    return ContextEntry(
        signal_type=signal,
        value=value,
        set_by_butler="general",
        set_at=NOW - timedelta(minutes=5),
        expires_at=expires,
        confidence=1.0,
        metadata=meta,
    )


def _read(kind, value=None, *, state="known", fresh_until=NOW + timedelta(hours=1), stale=None):
    return SituationRead(
        kind=kind,
        state=state,
        value=value if state == "known" else None,
        stale_last_value=stale,
        source="context.activity",
        observed_at=NOW - timedelta(minutes=1),
        fresh_until=fresh_until if state == "known" else None,
        age_seconds=60.0,
        labels={},
        availability_reason="confirmed" if state == "known" else state,
        generation=1,
    )


def _place(presence):
    return {"presence": presence, "place_ref": str(PLACE_REF), "locality_ref": str(LOCALITY_REF)}


def _suppression(active):
    return {"active": active, "record_ref": str(RECORD_REF) if active else None}


def _by_kind(receipts):
    assert tuple(r.kind for r in receipts) == PARITY_KINDS
    return {r.kind: r for r in receipts}


# -- compare -------------------------------------------------------------------


def test_matching_signals_agree_on_every_kind():
    legacy = [_entry("meeting"), _entry("at_home"), _entry("in_space", ROOM)]
    reads = {
        "owner.activity": _read("owner.activity", ["meeting"]),
        "owner.place": _read("owner.place", _place("inside")),
        "owner.suppression": _read("owner.suppression", _suppression(False)),
    }
    receipts = _by_kind(compare(legacy, reads, NOW))
    for receipt in receipts.values():
        assert receipt.agree and receipt.reason == "agree"
    assert receipts["owner.activity"].legacy == ("meeting",)
    assert receipts["owner.place"].legacy == ("inside",)
    assert receipts["owner.suppression"].perception == ("off",)


def test_activity_sets_compare_as_sets():
    legacy = [_entry("traveling"), _entry("meeting")]
    reads = {"owner.activity": _read("owner.activity", ["meeting", "traveling"])}
    activity = _by_kind(compare(legacy, reads, NOW))["owner.activity"]
    assert activity.agree
    assert activity.legacy == activity.perception == ("meeting", "traveling")


def test_a_different_activity_set_differs():
    reads = {"owner.activity": _read("owner.activity", ["focused"])}
    activity = _by_kind(compare([_entry("meeting")], reads, NOW))["owner.activity"]
    assert (activity.agree, activity.reason) == (False, "value_differs")


def test_legacy_asserting_what_perception_does_not_is_legacy_only():
    reads = {"owner.activity": _read("owner.activity", [])}
    activity = _by_kind(compare([_entry("sleeping")], reads, NOW))["owner.activity"]
    assert (activity.agree, activity.reason) == (False, "legacy_only")


def test_perception_asserting_what_legacy_does_not_is_perception_only():
    reads = {
        "owner.activity": _read("owner.activity", ["exercising"]),
        "owner.place": _read("owner.place", _place("outside")),
    }
    receipts = _by_kind(compare([], reads, NOW))
    assert receipts["owner.activity"].reason == "perception_only"
    assert receipts["owner.place"].reason == "perception_only"


@pytest.mark.parametrize("state", ["unknown", "unmeasurable", "unconfigured"])
def test_perception_not_known_is_legacy_only_or_unknown(state):
    reads = {kind: _read(kind, state=state) for kind in PARITY_KINDS}
    receipts = _by_kind(compare([_entry("meeting")], reads, NOW))
    assert receipts["owner.activity"].reason == "legacy_only"
    assert receipts["owner.place"].reason == "perception_unknown"
    # Legacy always asserts suppression on or off.
    assert receipts["owner.suppression"].reason == "legacy_only"
    for receipt in receipts.values():
        assert not receipt.agree
        assert receipt.perception_state == state
        assert receipt.perception == ()


def test_a_known_read_past_its_freshness_is_unknown():
    reads = {"owner.activity": _read("owner.activity", ["meeting"], fresh_until=NOW)}
    activity = _by_kind(compare([_entry("meeting")], reads, NOW))["owner.activity"]
    assert (activity.perception_state, activity.reason) == ("unknown", "legacy_only")
    assert not activity.agree


def test_a_legacy_signal_expired_at_as_of_does_not_count():
    reads = {"owner.activity": _read("owner.activity", [])}
    activity = _by_kind(compare([_entry("meeting", expires=NOW)], reads, NOW))["owner.activity"]
    assert activity.agree and activity.legacy == ()


def test_dnd_maps_to_suppression_on():
    reads = {"owner.suppression": _read("owner.suppression", _suppression(True))}
    receipt = _by_kind(compare([_entry("dnd")], reads, NOW))["owner.suppression"]
    assert receipt.agree and receipt.legacy == receipt.perception == ("on",)
    reads = {"owner.suppression": _read("owner.suppression", _suppression(False))}
    receipt = _by_kind(compare([_entry("dnd")], reads, NOW))["owner.suppression"]
    assert receipt.reason == "value_differs"


def test_a_room_alone_is_inside():
    reads = {"owner.place": _read("owner.place", _place("inside"))}
    place = _by_kind(compare([_entry("in_space", ROOM)], reads, NOW))["owner.place"]
    assert place.agree and place.legacy == ("inside",)


def test_away_is_outside_and_home_with_away_is_a_legacy_conflict():
    reads = {"owner.place": _read("owner.place", _place("outside"))}
    assert _by_kind(compare([_entry("away")], reads, NOW))["owner.place"].agree
    place = _by_kind(compare([_entry("at_home"), _entry("away")], reads, NOW))["owner.place"]
    assert (place.agree, place.reason) == (False, "legacy_conflict")
    assert place.legacy == ("inside", "outside")


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("owner.activity", ["meeting", "not-an-activity"]),
        ("owner.activity", "meeting"),
        ("owner.place", {"presence": "somewhere"}),
        ("owner.suppression", {"active": "yes"}),
    ],
)
def test_an_off_shape_or_missing_read_is_unreadable(kind, value):
    receipt = _by_kind(compare([_entry("meeting")], {kind: _read(kind, value)}, NOW))[kind]
    assert (receipt.perception_state, receipt.reason, receipt.agree) == (
        "unreadable",
        "unreadable",
        False,
    )
    missing = _by_kind(compare([], {}, NOW))[kind]
    assert missing.reason == "unreadable"


def test_an_aware_instant_is_required():
    with pytest.raises(ValueError):
        compare([], {}, NOW.replace(tzinfo=None))


def test_receipts_and_the_log_line_are_content_blind():
    """No room, title, free text, coordinate or reference reaches a receipt or the log."""
    legacy = [
        _entry("in_space", ROOM, meta={"title": TITLE, "lat": 1.352083, "lon": 103.819836}),
        _entry("at_home", "home", meta={"source": "ha_presence", "note": TITLE}),
        _entry("meeting", TITLE, meta={"title": TITLE}),
        _entry("dnd", "On call", meta={"reason": TITLE}),
    ]
    reads = {
        "owner.activity": _read("owner.activity", ["meeting"]),
        "owner.place": _read("owner.place", _place("inside"), stale=_place("outside")),
        "owner.suppression": _read("owner.suppression", _suppression(True)),
    }
    receipts = compare(legacy, reads, NOW)
    rendered = [repr(receipts), json.dumps([asdict(r) for r in receipts]), log_line(receipts)]
    forbidden = [ROOM, TITLE, "On call", "ha_presence", "home", "1.352083", "103.819836"]
    forbidden += [str(PLACE_REF), str(LOCALITY_REF), str(RECORD_REF), "context.activity"]
    for text in rendered:
        for needle in forbidden:
            assert needle not in text, (needle, text)
        assert not re.search(r"\d+\.\d{3,}", text), text  # no coordinate-like number
    vocabulary = ACTIVITIES | {"inside", "outside", "on", "off"}
    for receipt in receipts:
        assert set(receipt.legacy) | set(receipt.perception) <= vocabulary
        assert receipt.reason in REASONS
        assert receipt.perception_state in STATES | {"unreadable"}
    # The log line carries kinds, states, agreement and reasons, nothing else.
    entry = r"owner\.[a-z]+ state=[a-z]+ agree=(?:true|false) reason=[a-z_]+"
    assert re.fullmatch(rf"legacy parity: {entry}(?:; {entry})*", log_line(receipts))


# -- runner --------------------------------------------------------------------


class _Reader:
    def __init__(self, log, *, fail_on=None):
        self.log = log
        self.fail_on = fail_on

    def transaction(self):
        reader = self

        class _Tx:
            async def __aenter__(self):
                reader.log.append("begin")

            async def __aexit__(self, exc_type, exc, tb):
                reader.log.append("rollback" if exc_type else "commit")
                return False

        return _Tx()

    async def execute(self, sql):
        self.log.append(sql)

    async def close(self):
        self.log.append("close")


def _fake_reads(log, values, *, error=None):
    async def situation_read(connection, *, audience, kind, subject, as_of):
        assert audience is Audience.OWNER and subject == OWNER and as_of == NOW
        assert "SET LOCAL ROLE perception_owner_reader" in log
        log.append(f"read {kind}")
        if error is not None and kind == "owner.place":
            raise error
        return values.get(kind) or _read(kind, state="unconfigured")

    return situation_read


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(flags, "LEGACY_PARITY_LIVE", True)


async def test_the_runner_is_dark_and_touches_nothing(monkeypatch):
    touched = []

    async def legacy(pool):
        touched.append("legacy")
        return []

    async def opener():
        touched.append("reader")

    monkeypatch.setattr(legacy_parity, "get_active_context", legacy)
    with pytest.raises(LegacyParityDark):
        await LegacyParity(object(), subject=OWNER, open_reader=opener).run(as_of=NOW)
    assert touched == []


async def test_the_runner_reads_legacy_then_perception_as_now_does(monkeypatch, live, caplog):
    log: list[str] = []
    pool = object()

    async def legacy(given):
        assert given is pool
        log.append("legacy")
        return [_entry("meeting")]

    async def opener():
        log.append("open")
        return _Reader(log)

    values = {"owner.activity": _read("owner.activity", ["meeting"])}
    monkeypatch.setattr(legacy_parity, "get_active_context", legacy)
    monkeypatch.setattr(legacy_parity, "situation_read", _fake_reads(log, values))
    with caplog.at_level(logging.INFO, logger=legacy_parity.__name__):
        receipts = await LegacyParity(pool, subject=OWNER, open_reader=opener).run(as_of=NOW)
    assert log == [
        "legacy",
        "open",
        "begin",
        "SET LOCAL ROLE perception_owner_reader",
        "read owner.activity",
        "read owner.place",
        "read owner.suppression",
        "commit",
        "close",
    ]
    assert _by_kind(receipts)["owner.activity"].agree
    lines = [r.getMessage() for r in caplog.records if r.name == legacy_parity.__name__]
    assert lines == [log_line(receipts)]


async def test_an_unreadable_kind_is_a_receipt_not_a_failure(monkeypatch, live):
    log: list[str] = []

    async def opener():
        return _Reader(log)

    async def legacy(pool):
        return []

    monkeypatch.setattr(legacy_parity, "get_active_context", legacy)
    monkeypatch.setattr(
        legacy_parity, "situation_read", _fake_reads(log, {}, error=ValueError("bad"))
    )
    receipts = await LegacyParity(object(), subject=OWNER, open_reader=opener).run(as_of=NOW)
    assert _by_kind(receipts)["owner.place"].reason == "unreadable"


async def test_no_reader_is_unavailable(monkeypatch, live):
    async def opener():
        return None

    async def legacy(pool):
        return []

    monkeypatch.setattr(legacy_parity, "get_active_context", legacy)
    with pytest.raises(LegacyParityUnavailable):
        await LegacyParity(object(), subject=OWNER, open_reader=opener).run(as_of=NOW)


async def test_a_reader_failure_is_unavailable_and_closes(monkeypatch, live):
    log: list[str] = []

    async def opener():
        return _Reader(log)

    async def legacy(pool):
        return []

    monkeypatch.setattr(legacy_parity, "get_active_context", legacy)
    monkeypatch.setattr(
        legacy_parity, "situation_read", _fake_reads(log, {}, error=PermissionError("role"))
    )
    with pytest.raises(LegacyParityUnavailable):
        await LegacyParity(object(), subject=OWNER, open_reader=opener).run(as_of=NOW)
    assert log[-2:] == ["rollback", "close"]


async def test_a_legacy_failure_is_unavailable_before_any_reader(monkeypatch, live):
    opened = []

    async def opener():
        opened.append(True)

    async def legacy(pool):
        raise OSError("down")

    monkeypatch.setattr(legacy_parity, "get_active_context", legacy)
    with pytest.raises(LegacyParityUnavailable):
        await LegacyParity(object(), subject=OWNER, open_reader=opener).run(as_of=NOW)
    assert opened == []


async def test_the_default_reader_needs_the_dsn(monkeypatch, live):
    monkeypatch.delenv(legacy_parity.READER_DSN_ENV, raising=False)
    assert await legacy_parity.open_owner_reader() is None
