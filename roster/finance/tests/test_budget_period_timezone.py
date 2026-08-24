"""Budget periods are intervals on the OWNER's calendar, not on UTC's [bu-4zd9h].

A budget window used to be derived from a bare date — ``date.today()`` in the
tool layer, ``datetime.now(UTC).date()`` inside ``budget_status`` — while the
transactions it was matched against are ``TIMESTAMPTZ`` instants.  For any owner
who does not live in UTC that mis-attributes spending near a period boundary:
a Sunday-night coffee lands in next week's budget, and a June 30th flight lands
in Q3.  CI runs in UTC, where the two frames agree, so nothing here can be
caught by waiting for it to fail.

Every test below pins BOTH ends of the comparison — an exact reference instant
and an exact owner timezone — so it asserts the same thing at 3am as at 3pm.
``Asia/Singapore`` (UTC+8, no DST) is the fixture zone: its offset is large
enough that the owner-local date differs from the UTC date for a third of every
day, and constant enough that the expected instants can be written down.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from butlers.core.general_settings import save_general_settings
from butlers.tools.switchboard.insight.broker import create_insight_tables

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

OWNER_TZ = "Asia/Singapore"  # UTC+8, no DST — a stable 8-hour skew from UTC
SGT = ZoneInfo(OWNER_TZ)

CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS finance"

# ``state`` holds the shared general settings the owner timezone is read from;
# it lives in ``public`` so any butler-scoped search_path can reach it.
CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version    INTEGER NOT NULL DEFAULT 1
)
"""

CREATE_TRANSACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS finance.transactions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    posted_at  TIMESTAMPTZ NOT NULL,
    merchant   TEXT NOT NULL,
    amount     NUMERIC(14, 2) NOT NULL,
    currency   CHAR(3) NOT NULL,
    direction  TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    category   TEXT NOT NULL,
    deleted_at TIMESTAMPTZ
)
"""

CREATE_BUDGETS_SQL = """
CREATE TABLE IF NOT EXISTS finance.budgets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category        TEXT NOT NULL,
    period          TEXT NOT NULL CHECK (period IN ('weekly','monthly','quarterly','yearly')),
    amount          NUMERIC(14, 2) NOT NULL,
    currency        CHAR(3) NOT NULL DEFAULT 'USD',
    warn_threshold  NUMERIC(5, 4) NOT NULL DEFAULT 0.8000,
    alert_threshold NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _setup(pool, *, timezone: str = OWNER_TZ) -> None:
    """Provision the finance tables and pin the owner's configured timezone."""
    await pool.execute(CREATE_SCHEMA_SQL)
    await pool.execute(CREATE_STATE_SQL)
    await pool.execute(CREATE_TRANSACTIONS_SQL)
    await pool.execute(CREATE_BUDGETS_SQL)
    await save_general_settings(
        pool,
        timezone=timezone,
        language="en-US",
        date_format="YYYY-mm-dd",
        time_format="HH:MM",
        week_starts_on="Monday",
        currency="USD",
    )


async def _budget(pool, *, category: str, period: str, amount: str = "100.00") -> None:
    await pool.execute(
        "INSERT INTO finance.budgets (category, period, amount, currency) VALUES ($1,$2,$3,'USD')",
        category,
        period,
        amount,
    )


async def _spend(pool, *, category: str, at: datetime, amount: str = "110.00") -> None:
    await pool.execute(
        "INSERT INTO finance.transactions "
        "(posted_at, merchant, amount, currency, direction, category) "
        "VALUES ($1, 'Merchant', $2, 'USD', 'debit', $3)",
        at,
        amount,
        category,
    )


async def _status(pool, *, now: datetime) -> dict:
    """Run ``budget_status`` through a finance-scoped connection at a pinned instant."""
    from butlers.tools.finance.budgets import budget_status

    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO finance, public")
        return await budget_status(conn, now=now)


def _only(result: dict) -> dict:
    assert result["count"] == 1, result
    return result["items"][0]


# ---------------------------------------------------------------------------
# The window instants themselves
# ---------------------------------------------------------------------------


async def test_monthly_window_starts_at_owner_midnight_not_utc_midnight(
    provisioned_postgres_pool,
):
    """A day boundary: 2026-08-01 begins at 2026-07-31T16:00Z for a UTC+8 owner.

    Spending at 2026-07-31T17:30Z is already August 1st, 01:30 in Singapore, so
    it belongs to the August budget.  A UTC-derived window still calls that
    instant July and drops it.
    """
    async with provisioned_postgres_pool() as pool:
        await _setup(pool)
        await _budget(pool, category="rent", period="monthly", amount="100.00")
        await _spend(pool, category="rent", at=datetime(2026, 7, 31, 17, 30, tzinfo=UTC))

        item = _only(await _status(pool, now=datetime(2026, 7, 31, 17, 0, tzinfo=UTC)))

        assert item["period_start"] == "2026-08-01"
        assert item["period_end"] == "2026-08-31"
        assert item["spent"] == "110.00"
        assert item["status"] == "exceeded"


async def test_monthly_window_excludes_the_owners_previous_day(provisioned_postgres_pool):
    """The other side of the same boundary: 2026-07-31T15:00Z is still July.

    23:00 on July 31st in Singapore must not count against the August budget,
    even though the scan is running after UTC has ticked past that instant.
    """
    async with provisioned_postgres_pool() as pool:
        await _setup(pool)
        await _budget(pool, category="rent", period="monthly", amount="100.00")
        await _spend(pool, category="rent", at=datetime(2026, 7, 31, 15, 0, tzinfo=UTC))

        item = _only(await _status(pool, now=datetime(2026, 7, 31, 17, 0, tzinfo=UTC)))

        assert item["period_start"] == "2026-08-01"
        assert item["spent"] == "0"  # empty window
        assert item["status"] == "on_track"


async def test_weekly_window_follows_the_owners_monday(provisioned_postgres_pool):
    """An ISO-week boundary: 2026-07-06 is a Monday in Singapore while UTC says Sunday.

    2026-07-05T17:30Z is Monday 01:30 SGT — the first hours of ISO week 28 —
    so it opens the new week's budget rather than closing the old one's.
    """
    async with provisioned_postgres_pool() as pool:
        await _setup(pool)
        await _budget(pool, category="coffee", period="weekly", amount="100.00")
        await _spend(pool, category="coffee", at=datetime(2026, 7, 5, 17, 30, tzinfo=UTC))

        item = _only(await _status(pool, now=datetime(2026, 7, 5, 17, 0, tzinfo=UTC)))

        assert item["period_start"] == "2026-07-06"
        assert item["period_end"] == "2026-07-12"
        assert item["spent"] == "110.00"


async def test_weekly_window_excludes_the_owners_sunday_night(provisioned_postgres_pool):
    """2026-07-05T15:00Z is Sunday 23:00 SGT — the last hour of the PREVIOUS week."""
    async with provisioned_postgres_pool() as pool:
        await _setup(pool)
        await _budget(pool, category="coffee", period="weekly", amount="100.00")
        await _spend(pool, category="coffee", at=datetime(2026, 7, 5, 15, 0, tzinfo=UTC))

        item = _only(await _status(pool, now=datetime(2026, 7, 5, 17, 0, tzinfo=UTC)))

        assert item["period_start"] == "2026-07-06"
        assert item["spent"] == "0"  # empty window


async def test_weekly_window_excludes_the_owners_next_monday(provisioned_postgres_pool):
    """The upper edge: 2026-07-12T16:30Z is already Monday 00:30 SGT of week 29."""
    async with provisioned_postgres_pool() as pool:
        await _setup(pool)
        await _budget(pool, category="coffee", period="weekly", amount="100.00")
        await _spend(pool, category="coffee", at=datetime(2026, 7, 12, 16, 30, tzinfo=UTC))

        item = _only(await _status(pool, now=datetime(2026, 7, 5, 17, 0, tzinfo=UTC)))

        assert item["period_start"] == "2026-07-06"
        assert item["period_end"] == "2026-07-12"
        assert item["spent"] == "0"  # empty window


async def test_window_bounds_are_half_open_on_the_exact_instants(provisioned_postgres_pool):
    """Owner-local midnight opens the window; the next period's midnight closes it.

    Both edges are asserted in one window so an off-by-one in either direction
    (an inclusive upper bound, or an exclusive lower one) shows up as a spend
    total that is not exactly one transaction.
    """
    async with provisioned_postgres_pool() as pool:
        await _setup(pool)
        await _budget(pool, category="coffee", period="weekly", amount="100.00")
        # Exactly 2026-07-06T00:00 SGT — the first instant of the window.
        await _spend(
            pool,
            category="coffee",
            at=datetime(2026, 7, 5, 16, 0, tzinfo=UTC),
            amount="110.00",
        )
        # Exactly 2026-07-13T00:00 SGT — the first instant of the NEXT window.
        await _spend(
            pool,
            category="coffee",
            at=datetime(2026, 7, 12, 16, 0, tzinfo=UTC),
            amount="7.00",
        )

        item = _only(await _status(pool, now=datetime(2026, 7, 5, 17, 0, tzinfo=UTC)))

        assert item["period_start"] == "2026-07-06"
        assert item["spent"] == "110.00"


async def test_quarterly_window_follows_the_owners_quarter(provisioned_postgres_pool):
    """A quarter boundary: Q3 opens at 2026-06-30T16:00Z for a UTC+8 owner.

    The rarest boundary to hit by accident and the costliest to get wrong — a
    quarterly budget mis-attributes for three months, not one day.
    """
    async with provisioned_postgres_pool() as pool:
        await _setup(pool)
        await _budget(pool, category="travel", period="quarterly", amount="100.00")
        await _spend(pool, category="travel", at=datetime(2026, 6, 30, 17, 30, tzinfo=UTC))

        item = _only(await _status(pool, now=datetime(2026, 6, 30, 17, 0, tzinfo=UTC)))

        assert item["period_start"] == "2026-07-01"
        assert item["period_end"] == "2026-09-30"
        assert item["spent"] == "110.00"


async def test_quarterly_window_excludes_the_owners_last_quarter_night(
    provisioned_postgres_pool,
):
    """2026-06-30T15:00Z is 23:00 SGT on the last night of Q2 — not Q3 spending."""
    async with provisioned_postgres_pool() as pool:
        await _setup(pool)
        await _budget(pool, category="travel", period="quarterly", amount="100.00")
        await _spend(pool, category="travel", at=datetime(2026, 6, 30, 15, 0, tzinfo=UTC))

        item = _only(await _status(pool, now=datetime(2026, 6, 30, 17, 0, tzinfo=UTC)))

        assert item["period_start"] == "2026-07-01"
        assert item["spent"] == "0"  # empty window


async def test_utc_owner_keeps_the_utc_window(provisioned_postgres_pool):
    """An unconfigured (UTC) owner sees exactly the pre-existing behaviour.

    The timezone decision is a generalisation, not a redefinition: the default
    of ``UTC`` has to leave every existing deployment — and CI — untouched.
    """
    async with provisioned_postgres_pool() as pool:
        await _setup(pool, timezone="UTC")
        await _budget(pool, category="coffee", period="weekly", amount="100.00")
        # Sunday 23:00 UTC: inside the UTC week, outside the Singapore one.
        await _spend(pool, category="coffee", at=datetime(2026, 7, 5, 23, 0, tzinfo=UTC))

        item = _only(await _status(pool, now=datetime(2026, 7, 5, 17, 0, tzinfo=UTC)))

        assert item["period_start"] == "2026-06-29"
        assert item["period_end"] == "2026-07-05"
        assert item["spent"] == "110.00"


# ---------------------------------------------------------------------------
# The dedup identity and expiry the insight scan derives from that window
# ---------------------------------------------------------------------------


async def _setup_scan(pool, *, timezone: str = OWNER_TZ) -> None:
    from butlers.core.owner_conditions import create_owner_conditions_table

    await _setup(pool, timezone=timezone)
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS finance.bills (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            payee      TEXT NOT NULL,
            amount     NUMERIC(14, 2) NOT NULL,
            currency   CHAR(3) NOT NULL,
            due_date   DATE NOT NULL,
            frequency  TEXT,
            status     TEXT NOT NULL DEFAULT 'pending',
            paid_at    TIMESTAMPTZ
        )
        """
    )
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS finance.subscriptions (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            service       TEXT NOT NULL,
            amount        NUMERIC(14, 2) NOT NULL,
            currency      CHAR(3) NOT NULL,
            frequency     TEXT NOT NULL,
            next_renewal  DATE,
            status        TEXT NOT NULL DEFAULT 'active'
        )
        """
    )
    await create_insight_tables(pool)
    await create_owner_conditions_table(pool)
    await pool.execute(
        "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'normal') "
        "ON CONFLICT (id) DO NOTHING"
    )


def _capture_proposals(monkeypatch) -> list[dict]:
    """Record every candidate the scan proposes, at the broker-call seam.

    The broker rejects any candidate whose ``expires_at`` is already past
    (``roster/switchboard/tools/insight/broker.py``: "expires_at must be in the
    future") and it reads the real wall clock to decide that.  Pinning the scan's
    clock to an exact past instant -- the only way to make a timezone-boundary
    assertion deterministic at every wall clock -- therefore never reaches
    ``insight_candidates``, in red and in green alike.  Capturing at the seam
    asserts precisely what the scan derived from its anchor, which is what this
    file is about, and leaves the broker's own expiry contract to the broker's
    own tests.
    """
    from butlers.jobs._roster_loader import load_roster_jobs

    finance_jobs = load_roster_jobs("finance")
    proposals: list[dict] = []

    async def _record(pool: object, **kwargs: object) -> str:
        proposals.append(kwargs)
        return "accepted"

    monkeypatch.setattr(finance_jobs, "_propose", _record)
    return proposals


def _budget_proposal(proposals: list[dict]) -> dict:
    matches = [p for p in proposals if p["category"] == "budget-threshold"]
    assert len(matches) == 1, proposals
    return matches[0]


async def test_insight_scan_dedup_key_uses_the_owners_iso_week(
    provisioned_postgres_pool, monkeypatch
):
    """The dedup token is derived from the owner's week, so it turns over on the owner's Monday.

    Anchored at 2026-07-05T17:00Z — Monday 01:00 SGT, ISO week 28 — while UTC is
    still Sunday of week 27.  A UTC-derived token stamps the alert ``2026-W27``,
    which means the new week's first alert is silenced by the old week's cooldown.
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_scan(pool)
        proposals = _capture_proposals(monkeypatch)
        await _budget(pool, category="coffee", period="weekly", amount="50.00")
        await _spend(
            pool,
            category="coffee",
            at=datetime(2026, 7, 5, 17, 30, tzinfo=UTC),
            amount="55.00",
        )

        await run_insight_scan(pool, now=datetime(2026, 7, 5, 17, 0, tzinfo=UTC))

        cand = _budget_proposal(proposals)
        assert cand["dedup_key"] == "finance:budget-threshold:coffee:2026-W28-exceeded"
        # Monday through Sunday inclusive — the whole of the owner's new week.
        assert cand["cooldown_days"] == 7
        # The candidate expires at the owner's next Monday midnight, not UTC's.
        assert cand["expires_at"] == datetime(2026, 7, 13, tzinfo=SGT)


async def test_insight_scan_dedup_key_uses_the_owners_quarter(
    provisioned_postgres_pool, monkeypatch
):
    """Anchored at 2026-06-30T17:00Z — Q3 for the owner, still Q2 for UTC."""
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_scan(pool)
        proposals = _capture_proposals(monkeypatch)
        await _budget(pool, category="travel", period="quarterly", amount="1000.00")
        await _spend(
            pool,
            category="travel",
            at=datetime(2026, 6, 30, 17, 30, tzinfo=UTC),
            amount="850.00",
        )

        await run_insight_scan(pool, now=datetime(2026, 6, 30, 17, 0, tzinfo=UTC))

        cand = _budget_proposal(proposals)
        assert cand["dedup_key"] == "finance:budget-threshold:travel:2026-Q3-warning"
        assert cand["expires_at"] == datetime(2026, 10, 1, tzinfo=SGT)


async def test_insight_scan_dedup_key_uses_the_owners_month(provisioned_postgres_pool, monkeypatch):
    """The month-scoped identity comes from the owner's calendar too.

    One anchor governs every window the scan derives, so a key ending
    ``2026-08`` has to name the month the owner is in.  Anchored at
    2026-07-31T17:00Z: August 1st, 01:00 in Singapore, still July for UTC.
    """
    from butlers.jobs._roster.finance_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_scan(pool)
        proposals = _capture_proposals(monkeypatch)
        await _budget(pool, category="rent", period="monthly", amount="100.00")
        await _spend(pool, category="rent", at=datetime(2026, 7, 31, 17, 30, tzinfo=UTC))

        await run_insight_scan(pool, now=datetime(2026, 7, 31, 17, 0, tzinfo=UTC))

        cand = _budget_proposal(proposals)
        assert cand["dedup_key"] == "finance:budget-threshold:rent:2026-08-exceeded"
        assert cand["expires_at"] == datetime(2026, 9, 1, tzinfo=SGT)
