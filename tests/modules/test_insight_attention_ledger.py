"""Priority-aware quiet-hours bypass + context-bus gating for delivery_cycle().

Covers bu-qvnce.8 slices 1-2 (the "One attention ledger" move, RFC 0011
Amendment 1): the insight-delivery-cycle side of the attention policy.

- A candidate at/above URGENT_PRIORITY_THRESHOLD (90) is delivered even
  during quiet hours or an active context-bus dnd/sleeping signal.
- Routine (sub-threshold) candidates are left untouched (still 'pending')
  rather than delivered or silently dropped — they get another chance on a
  later, non-suppressed cycle.
- When no urgent candidate is pending, a fully suppressed cycle behaves as
  before: ``skipped=True``, nothing delivered.

Ledger-row content assertions (the actual INSERT into
``public.attention_ledger``) live in
``tests/integration/test_attention_ledger_roundtrip.py`` against a fully
migrated database — the ``insight_pool`` fixture here creates only the four
insight tables (see ``create_insight_tables``) plus the Owner Attention Policy
row, so ledger writes fail open silently, which is itself part of the
degraded-honesty contract this test file does not need to re-verify.

The policy row is not optional decoration: it is the difference between a
quiet-hours assertion that means something and one that is green because the
table was missing. See ``_DEFAULT_QUIET_START_HOUR`` below.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

_docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
    pytest.mark.integration,
]


# The Owner Attention Policy the ``insight_pool`` fixture seeds — the
# production default core_160 writes into ``public.approvals_policy`` for this
# single-owner (Asia/Singapore) deployment.
#
# Seeding it is what gives every instant in this file something to be quiet or
# loud *relative to*. Without the row the table does not exist at all, so
# ``get_approvals_policy_quiet_hours`` catches the UndefinedTableError, returns
# ``None``, and ``is_policy_quiet_now`` then reports every hour of every day as
# awake: the quiet-hours branch of ``delivery_cycle`` is unreachable and a cycle
# cannot observe suppression however loud the clock gets. With the row present
# the window is real — 23:00-08:00 SGT is 15:00-24:00 UTC — so a cycle handed
# the wall clock is suppressed for nine hours of every day, and every call site
# has to name its own instant.
_DEFAULT_QUIET_START_HOUR = 23
_DEFAULT_QUIET_END_HOUR = 8
_DEFAULT_QUIET_TIMEZONE = "Asia/Singapore"

# The instant every cycle in this file that does not name its own is evaluated
# at. 12:00 UTC is 20:00 SGT — comfortably outside the seeded quiet window, so
# a cycle run here is awake and any suppression it reports came from the thing
# the test actually set up. It is also past ``_DAILY_HOLD_FALLBACK_UTC_HOUR``
# (11), which the travel-day test below relies on.
_PINNED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


# ``_future`` stays on the wall clock on purpose: ``_PINNED_NOW`` is a past
# instant on every real run, so a ``_future()`` expiry is still in the future
# when a cycle is evaluated at ``_PINNED_NOW``. A test that wants an
# already-expired candidate would have to anchor the expiry to ``_PINNED_NOW``
# instead; no test in this file does.
def _future(days: int = 7) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


@pytest.fixture
async def insight_pool(provisioned_postgres_pool):
    """Provision a fresh database with insight tables and the owner policy.

    The Owner Attention Policy is part of the schema every one of these tests
    runs against in production, so it is seeded here with its production
    default. A test that is *about* quiet-hours suppression overrides it with
    :func:`_set_owner_attention_policy`.
    """
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    async with provisioned_postgres_pool() as pool:
        await create_insight_tables(pool)
        await _set_owner_attention_policy(
            pool,
            quiet_start_hour=_DEFAULT_QUIET_START_HOUR,
            quiet_end_hour=_DEFAULT_QUIET_END_HOUR,
            timezone=_DEFAULT_QUIET_TIMEZONE,
        )
        yield pool


async def _set_owner_attention_policy(
    pool,
    *,
    quiet_start_hour: int | None,
    quiet_end_hour: int | None,
    timezone: str = "UTC",
):
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.approvals_policy (
            id INTEGER PRIMARY KEY DEFAULT 1,
            quiet_start_hour INTEGER,
            quiet_end_hour INTEGER,
            timezone TEXT NOT NULL DEFAULT 'UTC'
        )
    """)
    await pool.execute(
        """
        INSERT INTO public.approvals_policy (id, quiet_start_hour, quiet_end_hour, timezone)
        VALUES (1, $1, $2, $3)
        ON CONFLICT (id) DO UPDATE
            SET quiet_start_hour = EXCLUDED.quiet_start_hour,
                quiet_end_hour = EXCLUDED.quiet_end_hour,
                timezone = EXCLUDED.timezone
        """,
        quiet_start_hour,
        quiet_end_hour,
        timezone,
    )


async def _insert_candidate(pool, *, dedup_key: str, priority: int, origin_butler: str = "health"):
    # Seed created_at from the Python clock rather than the PG DEFAULT now().
    # delivery_cycle's retention purge (broker._purge_old_insight_data) computes
    # its cutoff from datetime.now(UTC), so under libfaketime (+45d/+120d) a row
    # left at the unshifted testcontainer-PG now() looks weeks old and gets
    # deleted mid-cycle. Anchoring created_at to the same clock the purge reads
    # keeps candidates fresh under both real and faked clocks; identical to
    # PG now() on a normal run.
    await pool.execute(
        """
        INSERT INTO insight_candidates
            (origin_butler, priority, category, dedup_key, expires_at, message,
             status, created_at)
        VALUES ($1, $2, 'health', $3, $4, 'candidate message', 'pending', $5)
        """,
        origin_butler,
        priority,
        dedup_key,
        _future(),
        datetime.now(UTC),
    )


async def _candidate_id(pool, dedup_key: str) -> str:
    row = await pool.fetchrow("SELECT id FROM insight_candidates WHERE dedup_key = $1", dedup_key)
    return str(row["id"])


class TestUrgentBypassesQuietHours:
    async def test_urgent_delivered_routine_stays_pending(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        # Canonical policy covers the fixed noon test instant.
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _set_owner_attention_policy(insight_pool, quiet_start_hour=0, quiet_end_hour=23)
        await _insert_candidate(insight_pool, dedup_key="health:routine:1:2026", priority=70)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:1:2026", priority=95)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(
            insight_pool,
            notify_fn=notify_mock,
            now=_PINNED_NOW,
        )

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
        assert len(result["delivered"]) == 1

        urgent_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:1:2026'"
        )
        routine_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:1:2026'"
        )
        assert urgent_row["status"] == "delivered"
        assert routine_row["status"] == "pending"

    async def test_fully_suppressed_when_no_urgent_candidate_pending(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _set_owner_attention_policy(insight_pool, quiet_start_hour=0, quiet_end_hour=23)
        await _insert_candidate(insight_pool, dedup_key="health:routine:2:2026", priority=70)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(
            insight_pool,
            notify_fn=notify_mock,
            now=_PINNED_NOW,
        )

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()

        row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:2:2026'"
        )
        assert row["status"] == "pending"


class TestContextBusGating:
    """Suppression by the context bus alone, with quiet hours inactive.

    "no quiet hours" in these test names means the Owner Attention Policy the
    ``insight_pool`` fixture seeds is not *active* at the instant the cycle is
    evaluated at — ``_PINNED_NOW``, which is 20:00 SGT. The policy row itself
    is always present; without it ``is_policy_quiet_now`` would report every
    hour awake and these assertions would hold for a reason unrelated to the
    context bus.
    """

    async def test_dnd_signal_suppresses_routine_when_no_quiet_hours(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        # The fixture's production-default window does not cover
        # ``_PINNED_NOW``, so quiet hours are inactive at this instant and the
        # context bus is the only thing left that can suppress. Naming the
        # instant is what makes that true: on the wall clock this cycle would
        # be quiet-hours-suppressed for nine hours of every day, and the
        # assertion below cannot tell that apart from a context-bus hold.
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:3:2026", priority=70)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()

    @pytest.mark.pg_clock
    async def test_dnd_signal_bypassed_for_urgent_candidate(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:4:2026", priority=70)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:4:2026", priority=92)

        # Hour-independent by construction: an urgent candidate fails open past
        # quiet hours as well as the context bus (RFC 0011 Amendment 1), so this
        # outcome holds at every instant. Named anyway so no cycle in this file
        # reads the wall clock.
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="sleeping"),
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
        urgent_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:4:2026'"
        )
        routine_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:4:2026'"
        )
        assert urgent_row["status"] == "delivered"
        assert routine_row["status"] == "pending"

    async def test_meeting_signal_suppresses_routine_when_no_quiet_hours(self, insight_pool):
        """bu-ep4ks.9 slice 2: presence-aware delivery extends the shared
        dnd/sleeping suppression set with meeting/traveling."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:m1:2026", priority=70)

        # As in the dnd sibling above: a quiet-hours hold produces the exact
        # same skipped=True / not-awaited pair, so the awake instant is what
        # makes `meeting` the demonstrated cause.
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="meeting"),
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()

    async def test_traveling_signal_suppresses_routine_when_no_quiet_hours(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:t1:2026", priority=70)

        # Same indistinguishability as the dnd/meeting siblings: without an
        # awake instant a quiet-hours hold satisfies both assertions below.
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="traveling"),
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()

    async def test_meeting_signal_bypassed_for_urgent_candidate(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:m2:2026", priority=70)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:m2:2026", priority=93)

        # Hour-independent for the same reason as the dnd/urgent sibling above.
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="meeting"),
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
        urgent_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:m2:2026'"
        )
        routine_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:m2:2026'"
        )
        assert urgent_row["status"] == "delivered"
        assert routine_row["status"] == "pending"

    async def test_suppressed_ledger_row_carries_held_by_signal_telemetry(self, insight_pool):
        """The suppressed attention-ledger row must carry a structured
        `held_by` field naming the specific signal, not just the free-text
        `reason` string — see bu-ep4ks.9 slice 2 "held by <signal>" telemetry."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:h1:2026", priority=70)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with (
            patch(
                "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
                new=AsyncMock(return_value="meeting"),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.record_attention_event",
                new=AsyncMock(return_value="fake-id"),
            ) as ledger_mock,
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is True
        ledger_mock.assert_awaited_once()
        _, kwargs = ledger_mock.call_args
        assert kwargs["outcome"] == "suppressed"
        assert kwargs["reason"] == "context_bus:meeting"
        assert kwargs["metadata"] == {"held_by": "meeting"}

    async def test_no_context_signal_and_no_quiet_hours_delivers_normally(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:5:2026", priority=70)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()


class TestUrgentOnlySubCycle:
    """bu-o8233 (JARVIS pursuit move 8 slice 4) — hourly urgent sub-cycle.

    Today's single daily cron slot means a priority>=90 candidate proposed
    minutes after the daily run could otherwise sit 'pending' for nearly 24h.
    ``delivery_cycle(urgent_only=True)`` is the hourly job's entry point:
    routine candidates are never touched, urgent ones are never capped by the
    daily budget, and quiet-hours/context-bus are bypassed without even being
    queried (urgent always fails open past both, per RFC 0011 Amendment 1).
    """

    async def test_selects_only_urgent_leaves_routine_pending(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:u1:2026", priority=70)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:u1:2026", priority=95)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(
            insight_pool, notify_fn=notify_mock, urgent_only=True, now=_PINNED_NOW
        )

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
        assert len(result["delivered"]) == 1

        urgent_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:u1:2026'"
        )
        routine_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:u1:2026'"
        )
        assert urgent_row["status"] == "delivered"
        assert routine_row["status"] == "pending"

    async def test_bypasses_quiet_hours_without_querying_context_bus(self, insight_pool):
        """urgent_only skips the quiet-hours/context-bus consult entirely — it
        isn't just bypassed after being computed, it is never queried."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        # urgent_only must skip both canonical-policy and context reads, rather
        # than calculating and then ignoring their answers.
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:u2:2026", priority=91)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with (
            patch(
                "butlers.tools.switchboard.insight.broker.get_approvals_policy_quiet_hours",
                new=AsyncMock(side_effect=AssertionError("policy must not be queried")),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
                new=AsyncMock(side_effect=AssertionError("context bus must not be queried")),
            ),
        ):
            result = await delivery_cycle(
                insight_pool, notify_fn=notify_mock, urgent_only=True, now=_PINNED_NOW
            )

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
        row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:u2:2026'"
        )
        assert row["status"] == "delivered"

    async def test_no_daily_budget_cap_delivers_all_eligible_as_one_digest(self, insight_pool):
        """A verbosity budget of 1 would cap a normal cycle to one candidate;
        urgent_only ignores the daily budget and delivers every eligible
        urgent candidate this cycle, folded into one composed digest."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO UPDATE SET verbosity='minimal'
        """)
        for i in range(3):
            await _insert_candidate(
                insight_pool, dedup_key=f"health:urgent:u3-{i}:2026", priority=90 + i
            )

        notify_mock = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(
            insight_pool, notify_fn=notify_mock, urgent_only=True, now=_PINNED_NOW
        )

        assert result["skipped"] is False
        assert len(result["delivered"]) == 3
        notify_mock.assert_awaited_once()
        composed_message = notify_mock.await_args.args[0]
        assert "3" in composed_message  # digest header mentions the count

    async def test_respects_explicit_verbosity_off(self, insight_pool):
        """verbosity=off is a hard user opt-out, not a time-based deferral —
        urgent_only does not override it (unlike quiet hours/context bus)."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'off')
            ON CONFLICT (id) DO UPDATE SET verbosity='off'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:u4:2026", priority=95)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(
            insight_pool, notify_fn=notify_mock, urgent_only=True, now=_PINNED_NOW
        )

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()
        row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:u4:2026'"
        )
        assert row["status"] == "filtered"

    async def test_verbosity_off_does_not_filter_routine_pending(self, insight_pool):
        """verbosity=off must only claim the urgent working set this cycle
        considers — a routine candidate pending alongside it must stay
        untouched 'pending' for a later, non-suppressed cycle, not get
        collaterally marked 'filtered' by the hourly urgent sub-cycle."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'off')
            ON CONFLICT (id) DO UPDATE SET verbosity='off'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:u4b:2026", priority=95)
        await _insert_candidate(insight_pool, dedup_key="health:routine:u4b:2026", priority=70)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(
            insight_pool, notify_fn=notify_mock, urgent_only=True, now=_PINNED_NOW
        )

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()
        urgent_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:u4b:2026'"
        )
        routine_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:u4b:2026'"
        )
        assert urgent_row["status"] == "filtered"
        assert routine_row["status"] == "pending"

    async def test_no_urgent_pending_is_a_cheap_noop(self, insight_pool):
        """Routine candidates are never selected, filtered, or otherwise
        touched by an urgent_only cycle when none meet the threshold."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:u5:2026", priority=70)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(
            insight_pool, notify_fn=notify_mock, urgent_only=True, now=_PINNED_NOW
        )

        assert result["delivered"] == []
        notify_mock.assert_not_awaited()
        row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:u5:2026'"
        )
        assert row["status"] == "pending"

    async def test_urgent_delivery_not_resent_by_a_later_daily_cycle(self, insight_pool):
        """Idempotency: a candidate the hourly urgent sub-cycle already
        delivered must not be re-selected/re-sent by the next daily cycle —
        the row's own status='delivered' transition is the guard."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:u6:2026", priority=95)

        urgent_notify = AsyncMock(return_value={"status": "ok"})
        urgent_result = await delivery_cycle(
            insight_pool, notify_fn=urgent_notify, urgent_only=True, now=_PINNED_NOW
        )
        assert len(urgent_result["delivered"]) == 1

        # A routine candidate proposed after the urgent flush; the next daily
        # cycle should only ever see and deliver this one.
        await _insert_candidate(insight_pool, dedup_key="health:routine:u6:2026", priority=70)
        routine_id = str(
            (
                await insight_pool.fetchrow(
                    "SELECT id FROM insight_candidates WHERE dedup_key = 'health:routine:u6:2026'"
                )
            )["id"]
        )

        daily_notify = AsyncMock(return_value={"status": "ok"})
        daily_result = await delivery_cycle(insight_pool, notify_fn=daily_notify, now=_PINNED_NOW)

        assert daily_result["delivered"] == [routine_id]
        daily_notify.assert_awaited_once()


class TestFailedDeliveryLedgerRecording:
    """The deliver_success=False branch stamps outcome="failed" ledger rows (bu-wsm9m).

    Before this fix the insight choke point's delivery-failure branch bumped
    ``delivery_attempt_count`` and (after 3 strikes) marked candidates
    ``filtered`` with only a warn-log — no ``record_attention_event`` — so a
    genuine insight-delivery outage read identically to a benign quiet-hours
    hold (which DOES write a ``suppressed`` row) on the exact surface built to
    prove silence is chosen. Mirrors the notify() choke point's all-paths
    failed accounting (bu-zcos8/bu-hmdqz.3).

    ``insight_pool`` only creates the four insight tables, so the real ledger
    write would fail open silently; these tests patch
    ``record_attention_event`` in the broker module to capture the exact call
    shape while still asserting the unchanged bookkeeping (attempt-count bump,
    3-strikes ``filtered`` transition) against the real insight tables.
    """

    @staticmethod
    def _patch_ledger():
        calls: list[dict] = []

        async def _capture(_pool, **kwargs):
            calls.append(kwargs)
            return "ledger-row-id"

        patcher = patch(
            "butlers.tools.switchboard.insight.broker.record_attention_event",
            new=AsyncMock(side_effect=_capture),
        )
        return patcher, calls

    @staticmethod
    def _no_context_signal():
        return patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        )

    async def _seed_normal_verbosity(self, pool):
        """Seed verbosity=normal only.

        The name this used to carry ("no quiet hours") described the fixture's
        missing ``public.approvals_policy`` table rather than anything this
        helper does. The policy now exists with its production default; these
        tests stay out of the quiet-hours branch by evaluating their cycle at
        ``_PINNED_NOW``, not by leaving the policy unset.
        """
        await pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)

    async def test_notify_error_return_records_failed_ledger_row(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await self._seed_normal_verbosity(insight_pool)
        await _insert_candidate(insight_pool, dedup_key="health:fail:e1:2026", priority=70)
        cand_id = await _candidate_id(insight_pool, "health:fail:e1:2026")

        notify_mock = AsyncMock(return_value={"status": "error", "error": "boom"})
        patcher, calls = self._patch_ledger()
        with patcher, self._no_context_signal():
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        # No successful delivery.
        assert result["delivered"] == []

        # A single failed ledger row with a machine-readable reason.
        failed = [c for c in calls if c.get("outcome") == "failed"]
        assert len(failed) == 1
        row = failed[0]
        assert row["source"] == "insight"
        assert row["reason"] == "delivery_error:boom"
        assert row["notification_ref"] == cand_id
        assert row["dedup_key"] == "health:fail:e1:2026"
        assert row["intent"] == "insight"
        # Pre-3-strikes: retryable, not terminally filtered.
        assert row["metadata"]["retryable"] is True
        assert row["metadata"]["terminally_filtered"] is False
        assert row["metadata"]["failed_attempts"] == 1

        # Existing behavior unchanged: attempt count bumped, still pending.
        cand = await insight_pool.fetchrow(
            "SELECT status, delivery_attempt_count FROM insight_candidates WHERE id = $1::uuid",
            cand_id,
        )
        assert cand["status"] == "pending"
        assert cand["delivery_attempt_count"] == 1

    async def test_notify_exception_records_failed_ledger_row(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await self._seed_normal_verbosity(insight_pool)
        await _insert_candidate(insight_pool, dedup_key="health:fail:x1:2026", priority=70)
        cand_id = await _candidate_id(insight_pool, "health:fail:x1:2026")

        notify_mock = AsyncMock(side_effect=ValueError("weird"))
        patcher, calls = self._patch_ledger()
        with patcher, self._no_context_signal():
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["delivered"] == []
        failed = [c for c in calls if c.get("outcome") == "failed"]
        assert len(failed) == 1
        assert failed[0]["reason"] == "unexpected_error:ValueError"
        assert failed[0]["notification_ref"] == cand_id

        cand = await insight_pool.fetchrow(
            "SELECT status, delivery_attempt_count FROM insight_candidates WHERE id = $1::uuid",
            cand_id,
        )
        assert cand["status"] == "pending"
        assert cand["delivery_attempt_count"] == 1

    async def test_third_strike_records_terminally_filtered_metadata(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await self._seed_normal_verbosity(insight_pool)
        await _insert_candidate(insight_pool, dedup_key="health:fail:t1:2026", priority=70)
        cand_id = await _candidate_id(insight_pool, "health:fail:t1:2026")
        # Two prior failures already recorded; this cycle is the 3rd strike.
        await insight_pool.execute(
            "UPDATE insight_candidates SET delivery_attempt_count = 2 WHERE id = $1::uuid",
            cand_id,
        )

        notify_mock = AsyncMock(return_value={"status": "error", "error": "still down"})
        patcher, calls = self._patch_ledger()
        with patcher, self._no_context_signal():
            await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        failed = [c for c in calls if c.get("outcome") == "failed"]
        assert len(failed) == 1
        row = failed[0]
        assert row["reason"] == "delivery_error:still down"
        assert row["metadata"]["failed_attempts"] == 3
        assert row["metadata"]["terminally_filtered"] is True
        assert row["metadata"]["retryable"] is False

        # Existing behavior unchanged: 3-strikes filtered transition + metadata.
        cand = await insight_pool.fetchrow(
            "SELECT status, delivery_attempt_count, metadata "
            "FROM insight_candidates WHERE id = $1::uuid",
            cand_id,
        )
        assert cand["status"] == "filtered"
        assert cand["delivery_attempt_count"] == 3
        import json as _json

        meta = cand["metadata"]
        meta = _json.loads(meta) if isinstance(meta, str) else meta
        assert meta["delivery_failure"] is True
        assert meta["failed_attempts"] == 3

    async def test_digest_failure_records_one_failed_row_per_candidate(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await self._seed_normal_verbosity(insight_pool)
        await _insert_candidate(insight_pool, dedup_key="health:fail:d1:2026", priority=72)
        await _insert_candidate(insight_pool, dedup_key="health:fail:d2:2026", priority=71)
        id1 = await _candidate_id(insight_pool, "health:fail:d1:2026")
        id2 = await _candidate_id(insight_pool, "health:fail:d2:2026")

        notify_mock = AsyncMock(return_value={"status": "error", "error": "digest boom"})
        patcher, calls = self._patch_ledger()
        with patcher, self._no_context_signal():
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        # A >1 selection means notify was called once with a digest, and both
        # candidates get their own failed row.
        assert result["delivered"] == []
        failed = [c for c in calls if c.get("outcome") == "failed"]
        assert len(failed) == 2
        refs = {c["notification_ref"] for c in failed}
        assert refs == {id1, id2}
        for c in failed:
            assert c["reason"] == "delivery_error:digest boom"
            assert c["source"] == "insight"


# ===========================================================================
# daily_hold_mode: hold-until-first-active daily cadence (bu-ep4ks.9 slice 5)
# ===========================================================================


class TestDailyHoldMode:
    """delivery_cycle(daily_hold_mode=True) only changes behaviour when the
    cycle would otherwise be fully suppressed with no urgent candidate
    pending: it bypasses dnd/meeting/sleeping/quiet_hours suppression once
    the hard fallback deadline is reached, but a travel day always defers
    regardless of the deadline. daily_hold_mode=False (the default, used by
    every pre-slice-5 call site) must behave identically to before."""

    async def _seed(self, pool, *, dedup_key: str = "health:routine:dh1:2026"):
        await pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(pool, dedup_key=dedup_key, priority=70)

    async def test_default_mode_never_bypasses_deadline(self, insight_pool):
        """daily_hold_mode=False (default): suppressed with no urgent pending
        stays skipped even at/after the hard fallback hour — unchanged from
        pre-slice-5 behaviour."""
        from butlers.tools.switchboard.insight.broker import (
            _DAILY_HOLD_FALLBACK_UTC_HOUR,
            delivery_cycle,
        )

        await self._seed(insight_pool)
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ):
            result = await delivery_cycle(
                insight_pool,
                notify_fn=notify_mock,
                now=datetime(2026, 1, 15, _DAILY_HOLD_FALLBACK_UTC_HOUR + 2, 0, tzinfo=UTC),
            )

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()

    async def test_before_hard_deadline_still_holds(self, insight_pool):
        """daily_hold_mode=True, before the hard fallback hour: still held,
        exactly like the non-hold-mode suppression skip."""
        from butlers.tools.switchboard.insight.broker import (
            _DAILY_HOLD_FALLBACK_UTC_HOUR,
            delivery_cycle,
        )

        await self._seed(insight_pool, dedup_key="health:routine:dh2:2026")
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ):
            result = await delivery_cycle(
                insight_pool,
                notify_fn=notify_mock,
                daily_hold_mode=True,
                now=datetime(2026, 1, 15, _DAILY_HOLD_FALLBACK_UTC_HOUR - 1, 0, tzinfo=UTC),
            )

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()
        row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:dh2:2026'"
        )
        assert row["status"] == "pending"

    async def test_hard_deadline_bypasses_dnd_suppression(self, insight_pool):
        """daily_hold_mode=True, at/after the hard fallback hour: a dnd-held
        routine digest is force-delivered rather than skipped for the day."""
        from butlers.tools.switchboard.insight.broker import (
            _DAILY_HOLD_FALLBACK_UTC_HOUR,
            delivery_cycle,
        )

        await self._seed(insight_pool, dedup_key="health:routine:dh3:2026")
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ):
            result = await delivery_cycle(
                insight_pool,
                notify_fn=notify_mock,
                daily_hold_mode=True,
                now=datetime(2026, 1, 15, _DAILY_HOLD_FALLBACK_UTC_HOUR, 0, tzinfo=UTC),
            )

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
        row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:dh3:2026'"
        )
        assert row["status"] == "delivered"

    async def test_travel_day_defers_even_past_hard_deadline(self, insight_pool):
        """daily_hold_mode=True with `traveling` as the suppressing signal:
        never force-delivered by the hard fallback deadline, unlike
        dnd/meeting/sleeping/quiet_hours."""
        from butlers.tools.switchboard.insight.broker import (
            delivery_cycle,
        )

        await self._seed(insight_pool, dedup_key="health:routine:dh4:2026")
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="traveling"),
        ):
            # ``traveling`` has to be the signal that is actually holding this
            # cycle, so the instant must be awake: quiet hours are checked
            # first and win the ``_suppression_signal`` slot, and the hard
            # fallback deadline *does* bypass quiet_hours. 23:00 UTC (the old
            # instant here) is 07:00 SGT — inside the fixture's window — so it
            # would exercise the deadline bypass, not the travel-day defer.
            # ``_PINNED_NOW`` is 20:00 SGT and hour 12 >= the fallback hour
            # (11), which is what "past hard deadline" needs.
            result = await delivery_cycle(
                insight_pool,
                notify_fn=notify_mock,
                daily_hold_mode=True,
                now=_PINNED_NOW,
            )

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()
        row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:dh4:2026'"
        )
        assert row["status"] == "pending"

    async def test_travel_day_defer_ledger_reason(self, insight_pool):
        """The travel-day defer records its own distinct reason/held_by,
        separate from the generic context_bus:traveling suppression reason."""
        from butlers.tools.switchboard.insight.broker import (
            _DAILY_HOLD_FALLBACK_UTC_HOUR,
            delivery_cycle,
        )

        await self._seed(insight_pool, dedup_key="health:routine:dh5:2026")
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with (
            patch(
                "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
                new=AsyncMock(return_value="traveling"),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.record_attention_event",
                new=AsyncMock(return_value="fake-id"),
            ) as ledger_mock,
        ):
            result = await delivery_cycle(
                insight_pool,
                notify_fn=notify_mock,
                daily_hold_mode=True,
                now=datetime(2026, 1, 15, _DAILY_HOLD_FALLBACK_UTC_HOUR, 0, tzinfo=UTC),
            )

        assert result["skipped"] is True
        ledger_mock.assert_awaited_once()
        _, kwargs = ledger_mock.call_args
        assert kwargs["outcome"] == "suppressed"
        assert kwargs["reason"] == "travel_day_defer"
        assert kwargs["metadata"] == {"held_by": "traveling"}

    async def test_urgent_candidate_still_bypasses_on_travel_day(self, insight_pool):
        """An urgent (priority>=90) candidate is delivered on a travel day
        exactly as it would be for any other suppressing signal — travel-day
        defer only changes the no-urgent-pending fallback path."""
        from butlers.tools.switchboard.insight.broker import (
            _DAILY_HOLD_FALLBACK_UTC_HOUR,
            delivery_cycle,
        )

        await self._seed(insight_pool, dedup_key="health:routine:dh6:2026")
        await _insert_candidate(insight_pool, dedup_key="health:urgent:dh6:2026", priority=95)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="traveling"),
        ):
            result = await delivery_cycle(
                insight_pool,
                notify_fn=notify_mock,
                daily_hold_mode=True,
                now=datetime(2026, 1, 15, _DAILY_HOLD_FALLBACK_UTC_HOUR, 0, tzinfo=UTC),
            )

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
        urgent_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:dh6:2026'"
        )
        routine_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:dh6:2026'"
        )
        assert urgent_row["status"] == "delivered"
        assert routine_row["status"] == "pending"

    async def test_no_suppression_daily_hold_mode_delivers_normally(self, insight_pool):
        """daily_hold_mode=True has no effect when the cycle isn't
        suppressed at all."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await self._seed(insight_pool, dedup_key="health:routine:dh7:2026")
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ):
            result = await delivery_cycle(
                insight_pool,
                notify_fn=notify_mock,
                daily_hold_mode=True,
                now=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
            )

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()


# ===========================================================================
# Broker catch-up cycle at suppression end (bu-kqnum.3 slice 3)
# ===========================================================================


class TestBrokerCatchupCycle:
    """A fully-suppressed skip (no urgent candidate pending) reconciles a
    one-shot catch-up task at the suppression's own computed end instant,
    instead of relying solely on the next regularly scheduled cron tick.
    ``reconcile_catchup_task`` itself (SQL, deterministic-name reconciliation)
    is covered by tests/modules/test_insight_catchup.py against a mocked
    pool; this class proves delivery_cycle's *wiring* into it — which
    suppression branches call it, with what computed boundary, and that a
    scheduling failure never escapes the suppressed-cycle return."""

    async def _seed(self, pool, *, dedup_key: str = "health:routine:cu1:2026"):
        await pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal'
        """)
        await _insert_candidate(pool, dedup_key=dedup_key, priority=70)

    async def test_quiet_hours_suppression_schedules_catchup_at_quiet_end(self, insight_pool):
        from butlers.core.approvals_policy import policy_quiet_hours_deliver_at
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await self._seed(insight_pool, dedup_key="health:routine:cu1:2026")
        # The fixture's seeded policy is 23:00-08:00 Asia/Singapore, i.e.
        # 15:00-24:00 UTC (see the file-level comment on _DEFAULT_QUIET_*
        # above) — unlike _PINNED_NOW (20:00 SGT, deliberately awake so the
        # context-bus tests elsewhere in this file can isolate their own
        # signal), this instant must actually fall inside that window.
        quiet_now = datetime(2026, 1, 15, 20, 0, tzinfo=UTC)
        expected_deliver_at = policy_quiet_hours_deliver_at(
            {
                "quiet_start_hour": _DEFAULT_QUIET_START_HOUR,
                "quiet_end_hour": _DEFAULT_QUIET_END_HOUR,
                "timezone": _DEFAULT_QUIET_TIMEZONE,
            },
            now=quiet_now,
        )
        assert expected_deliver_at is not None, "fixture instant must actually be quiet"

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.reconcile_catchup_task",
            new=AsyncMock(return_value={"status": "ok", "state": "task_created"}),
        ) as catchup_mock:
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=quiet_now)

        assert result["skipped"] is True
        catchup_mock.assert_awaited_once()
        _, kwargs = catchup_mock.call_args
        assert kwargs["deliver_at"] == expected_deliver_at
        assert kwargs["reason"] == "quiet_hours"

    async def test_context_bus_signal_schedules_catchup_at_max_hold_end(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import _CONTEXT_MAX_HOLD, delivery_cycle

        await self._seed(insight_pool, dedup_key="health:routine:cu2:2026")
        set_at = _PINNED_NOW - timedelta(hours=1)
        expected_deliver_at = set_at + _CONTEXT_MAX_HOLD["dnd"]

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with (
            patch(
                "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
                new=AsyncMock(return_value="dnd"),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker._get_suppressing_context_signal_detail",
                new=AsyncMock(return_value=("dnd", set_at)),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.reconcile_catchup_task",
                new=AsyncMock(return_value={"status": "ok", "state": "task_created"}),
            ) as catchup_mock,
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is True
        catchup_mock.assert_awaited_once()
        _, kwargs = catchup_mock.call_args
        assert kwargs["deliver_at"] == expected_deliver_at
        assert kwargs["reason"] == "context_bus:dnd"

    async def test_travel_day_defer_also_schedules_catchup(self, insight_pool):
        """The travel-day defer branch is a suppressed skip too — it must
        reconcile a catch-up for `traveling`'s own max-hold end, not defer
        indefinitely until tomorrow's windowed cron."""
        from butlers.tools.switchboard.insight.broker import (
            _CONTEXT_MAX_HOLD,
            _DAILY_HOLD_FALLBACK_UTC_HOUR,
            delivery_cycle,
        )

        await self._seed(insight_pool, dedup_key="health:routine:cu3:2026")
        set_at = _PINNED_NOW - timedelta(hours=1)
        expected_deliver_at = set_at + _CONTEXT_MAX_HOLD["traveling"]

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with (
            patch(
                "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
                new=AsyncMock(return_value="traveling"),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker._get_suppressing_context_signal_detail",
                new=AsyncMock(return_value=("traveling", set_at)),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.reconcile_catchup_task",
                new=AsyncMock(return_value={"status": "ok", "state": "task_created"}),
            ) as catchup_mock,
        ):
            result = await delivery_cycle(
                insight_pool,
                notify_fn=notify_mock,
                daily_hold_mode=True,
                # Past the hard fallback deadline: proves travel-day defer
                # schedules a catch-up even though it deliberately never
                # force-delivers via that same deadline.
                now=datetime(2026, 1, 15, _DAILY_HOLD_FALLBACK_UTC_HOUR + 2, 0, tzinfo=UTC),
            )

        assert result["skipped"] is True
        catchup_mock.assert_awaited_once()
        _, kwargs = catchup_mock.call_args
        assert kwargs["deliver_at"] == expected_deliver_at
        assert kwargs["reason"] == "context_bus:traveling"

    async def test_urgent_bypass_does_not_schedule_catchup(self, insight_pool):
        """A cycle that delivers an urgent candidate this tick was never
        fully suppressed — no catch-up is needed (Slices 1-2 regression
        guard: the urgent-bypass path stays untouched by this change)."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await self._seed(insight_pool, dedup_key="health:routine:cu4:2026")
        await _insert_candidate(insight_pool, dedup_key="health:urgent:cu4:2026", priority=95)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with (
            patch(
                "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
                new=AsyncMock(return_value="dnd"),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.reconcile_catchup_task",
                new=AsyncMock(return_value={"status": "ok", "state": "task_created"}),
            ) as catchup_mock,
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is False
        catchup_mock.assert_not_awaited()

    async def test_catchup_scheduling_failure_does_not_abort_suppressed_return(self, insight_pool):
        """reconcile_catchup_task is best-effort/fail-open: a scheduling
        hiccup must not raise out of delivery_cycle or change its result."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await self._seed(insight_pool, dedup_key="health:routine:cu5:2026")
        set_at = _PINNED_NOW - timedelta(hours=1)
        notify_mock = AsyncMock(return_value={"status": "ok"})
        with (
            patch(
                "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
                new=AsyncMock(return_value="dnd"),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker._get_suppressing_context_signal_detail",
                new=AsyncMock(return_value=("dnd", set_at)),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.reconcile_catchup_task",
                new=AsyncMock(side_effect=RuntimeError("scheduler unavailable")),
            ) as catchup_mock,
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()
        catchup_mock.assert_awaited_once()

    async def test_no_pending_candidates_does_not_schedule_catchup(self, insight_pool):
        """Nothing to catch up on when there are no pending candidates at
        all — delivery_cycle's early return before the suppression consult
        must not reconcile a stray catch-up task."""
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.reconcile_catchup_task",
            new=AsyncMock(return_value={"status": "ok", "state": "task_created"}),
        ) as catchup_mock:
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock, now=_PINNED_NOW)

        assert result["skipped"] is False
        catchup_mock.assert_not_awaited()
