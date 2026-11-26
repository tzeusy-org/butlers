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
        "door": {"route": "/finance/bills", "ref": record},
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
    )

    rows = await _rows(pool)
    assert [r["origin_ref"] for r in rows] == ["claim:finance:bill:a", "claim:finance:bill:b"]
    assert {r["source_butler"] for r in rows} == {"finance"}
    assert sorted(result.created) == ["finance:bill:a", "finance:bill:b"]


async def test_the_same_complete_set_twice_writes_nothing_the_second_time(db):
    mod, pool = await _module(db)
    claims = [_claim("a"), _claim("b", presence="reminder")]
    await mod.reconcile_domain_claims("finance", claims, complete=True)
    before = {r["origin_ref"]: r["xmin"] for r in await _rows(pool)}

    second = await mod.reconcile_domain_claims("finance", claims, complete=True)

    assert second.writes == 0 and sorted(second.unchanged) == ["finance:bill:a", "finance:bill:b"]
    assert {r["origin_ref"]: r["xmin"] for r in await _rows(pool)} == before


async def test_a_changed_version_moves_the_same_row_and_the_provider_gets_a_patch(db):
    mod, pool = await _module(db)
    provider = mod._provider
    await mod.reconcile_domain_claims("finance", [_claim()], complete=True)
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
    result = await mod.reconcile_domain_claims("finance", [moved], complete=True)
    await mod._push_internal_events_to_provider()

    rows = await _rows(pool)
    assert len(rows) == 1 and rows[0]["id"] == row_id and rows[0]["starts_at"] == later
    assert result.updated == ["finance:bill:b1"]
    assert len(provider.create_calls) == 1 and len(provider.update_calls) == 1


async def test_a_settled_claim_retracts_its_row_and_the_provider_copy_is_deleted(db):
    mod, pool = await _module(db)
    provider = mod._provider
    await mod.reconcile_domain_claims("finance", [_claim()], complete=True)
    await mod._push_internal_events_to_provider()

    result = await mod.reconcile_domain_claims(
        "finance", [_claim(version="v2", status="settled")], complete=True
    )
    await mod._push_internal_events_to_provider()

    rows = await _rows(pool)
    assert [r["status"] for r in rows] == ["cancelled"]  # history stays: cancelled, not deleted
    assert result.retracted == ["finance:bill:b1"]
    assert len(provider.delete_calls) == 1 and not provider.events


async def test_an_absent_claim_is_retracted_only_on_a_complete_read(db):
    mod, pool = await _module(db)
    await mod.reconcile_domain_claims("finance", [_claim("a"), _claim("b")], complete=True)

    partial = await mod.reconcile_domain_claims("finance", [_claim("a")], complete=False)
    assert partial.retracted == [] and partial.retraction_ran is False
    assert {r["status"] for r in await _rows(pool)} == {"confirmed"}

    full = await mod.reconcile_domain_claims("finance", [_claim("a")], complete=True)
    assert full.retracted == ["finance:bill:b"]
    assert {r["origin_ref"]: r["status"] for r in await _rows(pool)} == {
        "claim:finance:bill:a": "confirmed",
        "claim:finance:bill:b": "cancelled",
    }


async def test_a_malformed_claim_is_skipped_and_withholds_retraction(db):
    mod, pool = await _module(db)
    await mod.reconcile_domain_claims("finance", [_claim("a"), _claim("b")], complete=True)

    result = await mod.reconcile_domain_claims(
        "finance", [_claim("a"), {"claim_key": "finance:bill:b", "label": ""}], complete=True
    )

    assert result.invalid == ["finance:bill:b"] and result.retracted == []
    assert {r["status"] for r in await _rows(pool)} == {"confirmed"}


async def test_a_returning_claim_revives_the_same_row(db):
    mod, pool = await _module(db)
    await mod.reconcile_domain_claims("finance", [_claim()], complete=True)
    row_id = (await _rows(pool))[0]["id"]
    await mod.reconcile_domain_claims("finance", [], complete=True)

    result = await mod.reconcile_domain_claims("finance", [_claim(version="v3")], complete=True)

    rows = await _rows(pool)
    assert len(rows) == 1 and rows[0]["id"] == row_id and rows[0]["status"] == "confirmed"
    assert result.revived == ["finance:bill:b1"]


async def test_a_butler_cannot_reconcile_anothers_claims(db):
    mod, pool = await _module(db)

    with pytest.raises(ClaimError):
        await mod.reconcile_domain_claims("travel", [_claim()], complete=True)
    result = await mod.reconcile_domain_claims(
        "finance", [_claim(claim_key="travel:leg:1")], complete=True
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
