"""Finance domain time claims: the pure rule and the job end to end (bu-5ii4gj.13 S3).

The end-to-end tests run the real finance job against real Postgres (core + finance chains), the
real claim reconcile and the real outbound push to a provider double, so a bill moving, being paid,
or a source failing is judged on the rows and provider calls it actually produces.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from butlers.api.read_models.calendar_workspace import CalendarOverlayRow, compute_overlay_freshness
from butlers.core.state import state_get
from butlers.jobs import calendar_overlay as job
from tests.modules.test_calendar_reminder_integration import (
    _make_module,
    _ReminderProviderDouble,
)

SGT = ZoneInfo("Asia/Singapore")
TODAY = date(2030, 3, 1)
NOW = datetime(2030, 3, 1, 4, 0, tzinfo=UTC)  # 12:00 SGT


def _bill(i="b1", due=date(2030, 3, 10), amount=84.2, status="pending", payee="Electric Co"):
    return {
        "id": i,
        "payee": payee,
        "amount": amount,
        "currency": "SGD",
        "due_date": due,
        "status": status,
        "payment_method": None,
    }


def _sub(i="s1", renew=date(2030, 3, 12), auto=True, cancel_by=None, service="Streamo"):
    return {
        "id": i,
        "service": service,
        "amount": 9.99,
        "currency": "SGD",
        "next_renewal": renew,
        "auto_renew": auto,
        "frequency": "monthly",
        "cancel_by": cancel_by,
    }


def _claims(bills=(), subs=(), now=NOW):
    return job.finance_claims(list(bills), list(subs), today=TODAY, zone=SGT, now=now)


def _flat(by_date):
    return {c["claim_key"]: c for day in by_date.values() for c in day}


@pytest.mark.unit
def test_a_future_reminder_moment_makes_a_reminder_claim_and_a_past_one_an_overlay_claim():
    by_date = _claims(bills=[_bill(due=date(2030, 3, 10)), _bill("b2", due=date(2030, 3, 2))])
    claims = _flat(by_date)

    far = claims["finance:bill:b1"]
    assert far["presence"] == "reminder"
    assert far["span"]["start"] == datetime(2030, 3, 7, 9, tzinfo=SGT).isoformat()
    # Due in two days: the 3-days-before moment is already past, so no reminder row is claimed.
    near = claims["finance:bill:b2"]
    assert near["presence"] == "overlay" and near["span"] == {
        "date": "2030-03-02",
        "timezone": "Asia/Singapore",
    }
    assert sorted(by_date) == ["2030-03-02", "2030-03-10"]


@pytest.mark.unit
def test_only_an_auto_renewing_subscription_gets_a_reminder_and_cancel_by_gets_its_own_claim():
    by_date = _claims(
        subs=[
            _sub("s1", renew=date(2030, 3, 20), auto=True),
            _sub("s2", renew=date(2030, 3, 20), auto=False),
            _sub("s3", renew=date(2030, 4, 30), cancel_by=date(2030, 3, 15), service="Gym"),
        ]
    )
    claims = _flat(by_date)

    assert claims["finance:subscription:s1"]["presence"] == "reminder"
    assert claims["finance:subscription:s2"]["presence"] == "overlay"
    cancel = claims["finance:subscription_cancel_by:s3"]
    assert cancel["label"] == "Cancel Gym" and cancel["presence"] == "reminder"
    assert cancel["span"]["start"] == datetime(2030, 3, 12, 9, tzinfo=SGT).isoformat()


@pytest.mark.unit
def test_the_record_version_follows_what_the_claim_depends_on_and_nothing_else():
    base = _flat(_claims(bills=[_bill()]))["finance:bill:b1"]["record_version"]
    assert _flat(_claims(bills=[_bill()]))["finance:bill:b1"]["record_version"] == base
    for changed in (_bill(amount=90), _bill(due=date(2030, 3, 11)), _bill(status="overdue")):
        assert _flat(_claims(bills=[changed]))["finance:bill:b1"]["record_version"] != base


@pytest.mark.unit
def test_a_row_without_an_id_carries_no_claim():
    row = _bill()
    del row["id"]
    assert _claims(bills=[row]) == {}


# --- end to end -------------------------------------------------------------------------------


def e2e(fn):
    """Real Postgres: needs Docker."""
    for mark in (
        pytest.mark.integration,
        pytest.mark.asyncio(loop_scope="session"),
        pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    ):
        fn = mark(fn)
    return fn


async def _world(db, monkeypatch):
    pool = await db("core", "memory", "finance", schema="finance")
    monkeypatch.setattr("butlers.jobs.briefing._now", lambda: NOW)
    monkeypatch.setattr(job, "_utcnow", lambda: NOW)
    for table in ("bills", "subscriptions", "facts"):
        await pool.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    mod = _make_module(pool, butler_name="finance")
    mod._projection_tables_available_cache = True
    mod._provider = _ReminderProviderDouble()
    mod._resolved_calendar_id = "butlers-calendar"
    # The job builds its own module for the pool; hand it this one so the provider double sees
    # the push.
    from butlers.modules.calendar import CalendarModule

    monkeypatch.setattr(CalendarModule, "for_pool", classmethod(lambda cls, p, *, butler_name: mod))
    return pool, mod


async def _add_bill(pool, *, due, status="pending", amount=84.2):
    return await pool.fetchval(
        "INSERT INTO bills (payee, amount, currency, due_date, frequency, status) "
        "VALUES ('Electric Co', $1, 'SGD', $2, 'one_time', $3) RETURNING id",
        amount,
        due,
        status,
    )


async def _rows(pool):
    return await pool.fetch(
        "SELECT origin_ref, status, starts_at, xmin::text AS xmin FROM calendar_events "
        "WHERE origin_ref LIKE 'claim:finance:%' ORDER BY origin_ref"
    )


@e2e
async def test_a_bill_owns_one_row_that_moves_with_it_and_is_retracted_when_paid(db, monkeypatch):
    pool, mod = await _world(db, monkeypatch)
    bill = await _add_bill(pool, due=date(2030, 3, 10))

    first = await job.run_finance_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()
    again = await job.run_finance_calendar_overlay_contribution(pool, None)

    rows = await _rows(pool)
    assert [r["origin_ref"] for r in rows] == [f"claim:finance:bill:{bill}"]
    assert rows[0]["starts_at"] == datetime(2030, 3, 7, 9, tzinfo=SGT)
    assert first["claims"]["created"] == 1 and again["claims"]["unchanged"] == 1
    assert again["claims"]["updated"] == 0 and len(mod._provider.create_calls) == 1

    await pool.execute("UPDATE bills SET due_date = '2030-03-12' WHERE id = $1", bill)
    moved = await job.run_finance_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()
    assert moved["claims"]["updated"] == 1
    assert (await _rows(pool))[0]["starts_at"] == datetime(2030, 3, 9, 9, tzinfo=SGT)
    assert len(mod._provider.update_calls) == 1 and len(mod._provider.create_calls) == 1

    await pool.execute("UPDATE bills SET status = 'paid' WHERE id = $1", bill)
    paid = await job.run_finance_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()
    assert paid["claims"]["retracted"] == 1
    assert [r["status"] for r in await _rows(pool)] == ["cancelled"]
    assert len(mod._provider.delete_calls) == 1


@e2e
async def test_a_failed_source_read_retracts_nothing_and_reads_partial(db, monkeypatch):
    pool, mod = await _world(db, monkeypatch)
    bill = await _add_bill(pool, due=date(2030, 3, 10))
    await job.run_finance_calendar_overlay_contribution(pool, None)
    stored_before = await state_get(pool, job.overlay_key("2030-03-10"))
    await pool.execute("UPDATE bills SET status = 'paid' WHERE id = $1", bill)

    await pool.execute("ALTER TABLE subscriptions RENAME TO subscriptions_gone")
    try:
        result = await job.run_finance_calendar_overlay_contribution(pool, None)
    finally:
        await pool.execute("ALTER TABLE subscriptions_gone RENAME TO subscriptions")

    assert result["complete"] is False and result["claims"]["retracted"] == 0
    assert [r["status"] for r in await _rows(pool)] == ["confirmed"]  # the paid bill's row stands
    stored = await state_get(pool, job.overlay_key("2030-03-10"))
    assert stored["complete"] is False
    assert (
        stored["claims"] == stored_before["claims"]
        and stored["entries"] == stored_before["entries"]
    )
    assert stored["generated_at"] == stored_before["generated_at"]
    row = CalendarOverlayRow(butler="finance", key=job.overlay_key("2030-03-10"), value=stored)
    states = {
        f["butler"]: f["state"] for f in compute_overlay_freshness([row], available=True, now=NOW)
    }
    assert states["finance"] == "partial"


def test_the_owners_reminder_days_replace_the_default_and_the_version_follows():
    bill = _bill(due=date(2030, 3, 20))
    default = _flat(_claims(bills=[bill]))["finance:bill:b1"]
    five = _flat(job.finance_claims([bill], [], today=TODAY, zone=SGT, now=NOW, bill_days=5))[
        "finance:bill:b1"
    ]

    assert default["span"]["start"].startswith("2030-03-17T09:00")
    assert five["span"]["start"].startswith("2030-03-15T09:00")
    assert five["record_version"] != default["record_version"]


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("5 days before due date", 5),
        ("prefers reminders 3 days before", 3),
        ("on the due date, 0 days", 0),
        ("remind me early", None),
        ("400 days", None),
        (None, None),
    ],
)
def test_a_reminder_preference_is_read_from_the_owners_words(text, days):
    assert job.parse_reminder_days(text) == days


@e2e
async def test_a_stored_reminder_preference_moves_the_bill_reminder(db, monkeypatch):
    pool, mod = await _world(db, monkeypatch)
    await _add_bill(pool, due=date(2030, 3, 10))
    await pool.execute(
        "INSERT INTO facts (subject, predicate, content, scope, validity) "
        "VALUES ('user', 'bill_reminder_preference', '5 days before due date', 'global', 'active')"
    )

    await job.run_finance_calendar_overlay_contribution(pool, None)

    assert (await _rows(pool))[0]["starts_at"] == datetime(2030, 3, 5, 9, tzinfo=SGT)


@e2e
async def test_a_bill_paid_before_the_window_keeps_its_row_as_history(db, monkeypatch):
    pool, mod = await _world(db, monkeypatch)
    bill = await _add_bill(pool, due=date(2030, 3, 10))
    await job.run_finance_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()

    # Ten days on the bill was paid and the window starts after its reminder.
    later = datetime(2030, 3, 12, 4, 0, tzinfo=UTC)
    monkeypatch.setattr("butlers.jobs.briefing._now", lambda: later)
    monkeypatch.setattr(job, "_utcnow", lambda: later)
    await pool.execute("UPDATE bills SET status = 'paid' WHERE id = $1", bill)
    result = await job.run_finance_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()

    assert result["claims"]["retracted"] == 0
    assert [r["status"] for r in await _rows(pool)] == ["confirmed"]
    assert not mod._provider.delete_calls
