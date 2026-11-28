"""Domain time claims reconciled into the butler's own calendar rows (bu-5ii4gj.13 S2).

Real Postgres plus the real outbound push against a provider double, so retraction, moves and
revival are judged at the seam that talks to the provider.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.temporal.domain_claims import ClaimError, parse_claim
from tests.modules.test_calendar_reminder_integration import (
    _make_module,
    _ReminderProviderDouble,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_START = datetime(2030, 3, 4, 9, 0, tzinfo=UTC)
_WIN = _START - timedelta(days=1)  # the producer's window starts the day before the claim


def _claim(record: str = "b1", *, version="v1", presence="block", status="active", **over):
    base = {
        "claim_key": f"finance:bill:{record}",
        "record_version": version,
        "kind": "bill_due",
        "label": "Electric Co",
        "priority": "high",
        "presence": presence,
        "status": status,
        "owner_action": True,
        "span": {
            "start": _START.isoformat(),
            "end": (_START + timedelta(hours=1)).isoformat(),
            "timezone": "Asia/Singapore",
        },
        "door": {"route": "/butlers/finance", "ref": record},
    }
    base.update(over)
    return base


async def _module(db):
    pool = await db("core", schema="finance")
    mod = _make_module(pool, butler_name="finance")
    mod._projection_tables_available_cache = True
    mod._provider = _ReminderProviderDouble()
    mod._resolved_calendar_id = "butlers-calendar"
    return mod, pool


async def _rows(pool):
    return await pool.fetch(
        "SELECT id, origin_ref, status, title, starts_at, source_butler, xmin::text AS xmin, "
        "metadata FROM calendar_events WHERE origin_ref LIKE 'claim:%' ORDER BY origin_ref"
    )


async def test_a_block_or_reminder_claim_owns_one_row_and_an_overlay_claim_owns_none(db):
    mod, pool = await _module(db)

    result = await mod.reconcile_domain_claims(
        "finance",
        [
            _claim("a", presence="block"),
            _claim("b", presence="reminder"),
            _claim("c", presence="overlay"),
        ],
        complete=True,
        window_start=_WIN,
    )

    rows = await _rows(pool)
    assert [r["origin_ref"] for r in rows] == ["claim:finance:bill:a", "claim:finance:bill:b"]
    assert {r["source_butler"] for r in rows} == {"finance"}
    assert sorted(result.created) == ["finance:bill:a", "finance:bill:b"]


async def test_the_same_complete_set_twice_writes_nothing_the_second_time(db):
    mod, pool = await _module(db)
    claims = [_claim("a"), _claim("b", presence="reminder")]
    await mod.reconcile_domain_claims("finance", claims, complete=True, window_start=_WIN)
    before = {r["origin_ref"]: r["xmin"] for r in await _rows(pool)}

    second = await mod.reconcile_domain_claims("finance", claims, complete=True, window_start=_WIN)

    assert second.writes == 0 and sorted(second.unchanged) == ["finance:bill:a", "finance:bill:b"]
    assert {r["origin_ref"]: r["xmin"] for r in await _rows(pool)} == before


async def test_a_changed_version_moves_the_same_row_and_the_provider_gets_a_patch(db):
    mod, pool = await _module(db)
    provider = mod._provider
    await mod.reconcile_domain_claims("finance", [_claim()], complete=True, window_start=_WIN)
    await mod._push_internal_events_to_provider()
    row_id = (await _rows(pool))[0]["id"]
    assert len(provider.create_calls) == 1

    later = _START + timedelta(days=2)
    moved = _claim(
        version="v2",
        span={
            "start": later.isoformat(),
            "end": (later + timedelta(hours=1)).isoformat(),
            "timezone": "Asia/Singapore",
        },
    )
    result = await mod.reconcile_domain_claims("finance", [moved], complete=True, window_start=_WIN)
    await mod._push_internal_events_to_provider()

    rows = await _rows(pool)
    assert len(rows) == 1 and rows[0]["id"] == row_id and rows[0]["starts_at"] == later
    assert result.updated == ["finance:bill:b1"]
    assert len(provider.create_calls) == 1 and len(provider.update_calls) == 1


async def test_a_settled_claim_retracts_its_row_and_the_provider_copy_is_deleted(db):
    mod, pool = await _module(db)
    provider = mod._provider
    await mod.reconcile_domain_claims("finance", [_claim()], complete=True, window_start=_WIN)
    await mod._push_internal_events_to_provider()

    result = await mod.reconcile_domain_claims(
        "finance", [_claim(version="v2", status="settled")], complete=True, window_start=_WIN
    )
    await mod._push_internal_events_to_provider()

    rows = await _rows(pool)
    assert [r["status"] for r in rows] == ["cancelled"]  # history stays: cancelled, not deleted
    assert result.retracted == ["finance:bill:b1"]
    assert len(provider.delete_calls) == 1 and not provider.events


async def test_an_absent_claim_is_retracted_only_on_a_complete_read(db):
    mod, pool = await _module(db)
    await mod.reconcile_domain_claims(
        "finance", [_claim("a"), _claim("b")], complete=True, window_start=_WIN
    )

    partial = await mod.reconcile_domain_claims(
        "finance", [_claim("a")], complete=False, window_start=_WIN
    )
    assert partial.retracted == [] and partial.retraction_ran is False
    assert {r["status"] for r in await _rows(pool)} == {"confirmed"}

    full = await mod.reconcile_domain_claims(
        "finance", [_claim("a")], complete=True, window_start=_WIN
    )
    assert full.retracted == ["finance:bill:b"]
    assert {r["origin_ref"]: r["status"] for r in await _rows(pool)} == {
        "claim:finance:bill:a": "confirmed",
        "claim:finance:bill:b": "cancelled",
    }


async def test_a_malformed_claim_is_skipped_and_withholds_retraction(db):
    mod, pool = await _module(db)
    await mod.reconcile_domain_claims(
        "finance", [_claim("a"), _claim("b")], complete=True, window_start=_WIN
    )

    result = await mod.reconcile_domain_claims(
        "finance",
        [_claim("a"), {"claim_key": "finance:bill:b", "label": ""}],
        complete=True,
        window_start=_WIN,
    )

    assert result.invalid == ["finance:bill:b"] and result.retracted == []
    assert {r["status"] for r in await _rows(pool)} == {"confirmed"}


async def test_a_returning_claim_revives_the_same_row(db):
    mod, pool = await _module(db)
    provider = mod._provider
    await mod.reconcile_domain_claims("finance", [_claim()], complete=True, window_start=_WIN)
    await mod._push_internal_events_to_provider()
    row_id = (await _rows(pool))[0]["id"]
    await mod.reconcile_domain_claims("finance", [], complete=True, window_start=_WIN)
    await mod._push_internal_events_to_provider()
    assert len(provider.delete_calls) == 1

    result = await mod.reconcile_domain_claims(
        "finance", [_claim(version="v3")], complete=True, window_start=_WIN
    )
    await mod._push_internal_events_to_provider()

    # The push recreates the provider copy (the delete cleared its id), it does not patch.
    assert len(provider.create_calls) == 2
    rows = await _rows(pool)
    assert len(rows) == 1 and rows[0]["id"] == row_id and rows[0]["status"] == "confirmed"
    assert result.revived == ["finance:bill:b1"]


async def test_a_butler_cannot_reconcile_anothers_claims(db):
    mod, pool = await _module(db)

    with pytest.raises(ClaimError):
        await mod.reconcile_domain_claims("travel", [_claim()], complete=True, window_start=_WIN)
    result = await mod.reconcile_domain_claims(
        "finance", [_claim(claim_key="travel:leg:1")], complete=True, window_start=_WIN
    )

    assert result.invalid == ["travel:leg:1"] and await _rows(pool) == []


async def test_the_reminder_tick_skips_a_block_claim_and_keeps_a_reminder_claim(db):
    mod, pool = await _module(db)
    past = datetime.now(UTC) - timedelta(hours=2)
    span = {
        "start": past.isoformat(),
        "timezone": "UTC",
        "end": (past + timedelta(hours=1)).isoformat(),
    }
    await mod.reconcile_domain_claims(
        "finance",
        [_claim("a", presence="block", span=span), _claim("b", presence="reminder", span=span)],
        complete=True,
        window_start=_WIN,
    )
    seen = []

    async def notify(envelope):
        seen.append(envelope["reminder_event_id"])

    assert await mod.tick("finance", notify_fn=notify) == 1
    row = next(r for r in await _rows(pool) if r["origin_ref"].endswith(":b"))
    assert seen == [str(row["id"])]


@pytest.mark.unit
def test_a_claim_needs_an_offset_a_known_zone_and_an_end_unless_it_is_a_reminder():
    ok = _claim()
    assert parse_claim(ok, source_butler="finance").span.all_day is False
    day = _claim(span={"date": "2030-03-04", "timezone": "Asia/Singapore"})
    assert parse_claim(day, source_butler="finance").span.all_day is True
    for bad in (
        {"start": "2030-03-04T09:00:00", "end": "2030-03-04T10:00:00", "timezone": "UTC"},
        {"start": _START.isoformat(), "end": _START.isoformat(), "timezone": "UTC"},
        {
            "start": _START.isoformat(),
            "end": (_START + timedelta(hours=1)).isoformat(),
            "timezone": "Nowhere/Land",
        },
        {"start": _START.isoformat(), "timezone": "UTC"},
    ):
        with pytest.raises(ClaimError):
            parse_claim(_claim(span=bad), source_butler="finance")
    assert parse_claim(
        _claim(presence="reminder", span={"start": _START.isoformat(), "timezone": "UTC"}),
        source_butler="finance",
    ).span.end == _START + timedelta(minutes=15)


async def test_the_overlay_writer_stores_schema_v2_claims_per_date(db):
    from datetime import date

    from butlers.core.state import state_get
    from butlers.jobs import calendar_overlay as job

    pool = await db("core", schema="finance")
    claim = _claim()
    await job.write_overlay_envelopes(
        pool,
        butler="finance",
        today=date(2030, 3, 4),
        lookahead_days=1,
        entries_by_date={},
        claims_by_date={"2030-03-04": [claim]},
    )

    first = await state_get(pool, job.overlay_key("2030-03-04"))
    second = await state_get(pool, job.overlay_key("2030-03-05"))
    assert first["schema_version"] == 2 and first["claims"] == [claim]
    assert second["schema_version"] == 2 and second["claims"] == []


def _moved(version, days=2):
    later = _START + timedelta(days=days)
    return _claim(
        version=version,
        span={
            "start": later.isoformat(),
            "end": (later + timedelta(hours=1)).isoformat(),
            "timezone": "Asia/Singapore",
        },
    )


async def _reconcile(mod, claims):
    return await mod.reconcile_domain_claims("finance", claims, complete=True, window_start=_WIN)


async def test_an_owner_move_survives_a_re_emit_and_an_amount_change_but_not_a_span_change(db):
    mod, pool = await _module(db)
    await _reconcile(mod, [_claim()])
    owner_time = _START + timedelta(hours=5)
    await pool.execute(
        "UPDATE calendar_events SET starts_at = $1::timestamptz, "
        "ends_at = $1::timestamptz + interval '1 hour', title = 'My own title' "
        "WHERE origin_ref LIKE 'claim:%'",
        owner_time,
    )
    before = (await _rows(pool))[0]["xmin"]

    # Same version: no write at all.
    result = await _reconcile(mod, [_claim()])
    row = (await _rows(pool))[0]
    assert result.unchanged == ["finance:bill:b1"] and row["xmin"] == before
    assert row["starts_at"] == owner_time and row["title"] == "My own title"

    # New version, same span (an amount changed): metadata refreshes, the owner's move stands.
    result = await _reconcile(mod, [_claim(version="v2")])
    row = (await _rows(pool))[0]
    assert result.updated == ["finance:bill:b1"]
    assert row["starts_at"] == owner_time and row["title"] == "My own title"
    assert row["metadata"]["claim"]["record_version"] == "v2"

    # The claim's own span moved: the row follows the record again, the override is dropped.
    await _reconcile(mod, [_moved("v3")])
    row = (await _rows(pool))[0]
    assert row["starts_at"] == _START + timedelta(days=2) and row["title"] == "Electric Co"


async def test_an_owner_delete_stays_gone_until_the_claims_span_changes(db):
    mod, pool = await _module(db)
    await _reconcile(mod, [_claim()])
    await pool.execute(
        "UPDATE calendar_events SET status = 'cancelled' WHERE origin_ref LIKE 'claim:%'"
    )

    again = await _reconcile(mod, [_claim()])
    amount = await _reconcile(mod, [_claim(version="v2")])
    assert again.unchanged == amount.unchanged == ["finance:bill:b1"]
    assert [r["status"] for r in await _rows(pool)] == ["cancelled"]

    back = await _reconcile(mod, [_moved("v3")])
    assert back.revived == ["finance:bill:b1"]
    assert [r["status"] for r in await _rows(pool)] == ["confirmed"]


async def test_a_row_that_slid_out_of_the_window_keeps_its_state_and_the_push_deletes_nothing(db):
    mod, pool = await _module(db)
    provider = mod._provider
    await mod.reconcile_domain_claims("finance", [_claim()], complete=True, window_start=_WIN)
    await mod._push_internal_events_to_provider()

    # The day after the claim's date the window starts later and the producer no longer lists it.
    tomorrow = _START + timedelta(days=1)
    result = await mod.reconcile_domain_claims("finance", [], complete=True, window_start=tomorrow)
    await mod._push_internal_events_to_provider()

    assert result.retracted == [] and not provider.delete_calls
    assert [r["status"] for r in await _rows(pool)] == ["confirmed"]

    # An upcoming row that disappears inside the window is still retracted.
    await mod.reconcile_domain_claims("finance", [], complete=True, window_start=_WIN)
    assert [r["status"] for r in await _rows(pool)] == ["cancelled"]


async def test_an_over_cap_label_or_door_is_an_invalid_claim(db):
    mod, pool = await _module(db)
    result = await mod.reconcile_domain_claims(
        "finance",
        [
            _claim("a", label="x" * 121),
            _claim("b", door={"route": "/x", "ref": "y" * 3000}),
            _claim("c"),
        ],
        complete=True,
        window_start=_WIN,
    )
    assert result.invalid == ["finance:bill:a", "finance:bill:b"]
    assert result.created == ["finance:bill:c"] and result.retraction_ran is False


def test_clip_label_cuts_a_long_label_to_the_cap_with_an_ellipsis():
    from butlers.core.temporal.domain_claims import MAX_LABEL_CHARS, clip_label

    assert clip_label("  Electric Co ") == "Electric Co"
    clipped = clip_label("P" * 130)
    assert len(clipped) == MAX_LABEL_CHARS and clipped.endswith("…")
