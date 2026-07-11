"""Real-Postgres regression: the attention ledger + seeded owner quiet hours.

Exercises core_160 (bu-qvnce.8, move 8 slice 1) against a fully migrated
Postgres instance (testcontainers) — not just mocked-pool unit tests:

- ``public.attention_ledger`` is created with the expected columns and CHECK
  constraints (source/outcome vocabulary, priority_score range).
- A fresh, never-configured install gets sane owner-level quiet-hours
  defaults seeded into both ``public.approvals_policy`` (the notify() owner-page
  gate) and ``public.insight_settings`` (the insight-delivery-cycle gate) —
  23:00-08:00 Asia/Singapore — without any owner action.
- The seed is idempotent/non-destructive: an owner who already configured
  either policy before this migration ran keeps their own configuration.
- ``record_attention_event`` round-trips through the real table via the
  actual production writer in ``butlers.core.attention_ledger``.
"""

from __future__ import annotations

import json
import shutil

import asyncpg
import pytest

from butlers.api.routers.attention_ledger import _query_ledger, _query_ledger_summary
from butlers.core.attention_ledger import record_attention_event
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


async def test_attention_ledger_table_exists_with_expected_columns(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'attention_ledger'
        """
    )
    columns = {r["column_name"] for r in rows}
    assert columns == {
        "id",
        "occurred_at",
        "origin_butler",
        "source",
        "channel",
        "intent",
        "priority_label",
        "priority_score",
        "dedup_key",
        "outcome",
        "reason",
        "notification_ref",
        "metadata",
    }


async def test_source_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO public.attention_ledger (origin_butler, source, outcome)
            VALUES ('health', 'bogus', 'delivered')
            """
        )


async def test_outcome_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO public.attention_ledger (origin_butler, source, outcome)
            VALUES ('health', 'notify', 'ghosted')
            """
        )


async def test_priority_score_out_of_range_rejected(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO public.attention_ledger
                (origin_butler, source, outcome, priority_score)
            VALUES ('health', 'notify', 'delivered', 101)
            """
        )


# ---------------------------------------------------------------------------
# Seeded owner-level quiet hours (fresh, never-configured install)
# ---------------------------------------------------------------------------


async def test_approvals_policy_seeded_to_asia_singapore_defaults(pool: asyncpg.Pool) -> None:
    row = await pool.fetchrow(
        "SELECT quiet_start_hour, quiet_end_hour, timezone FROM public.approvals_policy WHERE id = 1"
    )
    assert row is not None
    assert row["quiet_start_hour"] == 23
    assert row["quiet_end_hour"] == 8
    assert row["timezone"] == "Asia/Singapore"


async def test_insight_settings_seeded_to_asia_singapore_defaults(pool: asyncpg.Pool) -> None:
    row = await pool.fetchrow(
        "SELECT quiet_start, quiet_end, quiet_timezone FROM public.insight_settings WHERE id = 1"
    )
    assert row is not None
    assert row["quiet_start"] == 23
    assert row["quiet_end"] == 8
    assert row["quiet_timezone"] == "Asia/Singapore"


# ---------------------------------------------------------------------------
# Idempotency: a pre-existing owner configuration is never clobbered.
#
# These re-run core_160's exact guarded seed SQL (WHERE ... IS NULL) directly
# against rows that already carry a custom (non-default) configuration —
# simulating an owner who configured either policy before this migration
# shipped. If core_160's guard clause changes, update both the migration and
# this test together.
# ---------------------------------------------------------------------------


async def test_seed_does_not_clobber_existing_approvals_policy(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        UPDATE public.approvals_policy
        SET quiet_start_hour = 10, quiet_end_hour = 14, timezone = 'America/New_York'
        WHERE id = 1
        """
    )
    await pool.execute("""
        UPDATE public.approvals_policy
        SET quiet_start_hour = 23, quiet_end_hour = 8, timezone = 'Asia/Singapore'
        WHERE id = 1 AND quiet_start_hour IS NULL AND quiet_end_hour IS NULL
    """)
    row = await pool.fetchrow(
        "SELECT quiet_start_hour, quiet_end_hour, timezone FROM public.approvals_policy WHERE id = 1"
    )
    assert row["quiet_start_hour"] == 10
    assert row["quiet_end_hour"] == 14
    assert row["timezone"] == "America/New_York"

    # Restore the module-scoped DB's seeded state so other tests in this file
    # do not depend on execution order (module-scoped migrated_db_url is
    # shared across every test function in this module).
    await pool.execute("""
        UPDATE public.approvals_policy
        SET quiet_start_hour = 23, quiet_end_hour = 8, timezone = 'Asia/Singapore'
        WHERE id = 1
    """)


async def test_seed_does_not_clobber_existing_insight_settings(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        UPDATE public.insight_settings
        SET quiet_start = 1, quiet_end = 5, quiet_timezone = 'UTC'
        WHERE id = 1
        """
    )
    await pool.execute("""
        UPDATE public.insight_settings
        SET quiet_start = 23, quiet_end = 8, quiet_timezone = 'Asia/Singapore'
        WHERE id = 1 AND quiet_start IS NULL AND quiet_end IS NULL AND quiet_timezone IS NULL
    """)
    row = await pool.fetchrow(
        "SELECT quiet_start, quiet_end, quiet_timezone FROM public.insight_settings WHERE id = 1"
    )
    assert row["quiet_start"] == 1
    assert row["quiet_end"] == 5
    assert row["quiet_timezone"] == "UTC"

    # Restore seeded state — see comment in the approvals_policy counterpart above.
    await pool.execute("""
        UPDATE public.insight_settings
        SET quiet_start = 23, quiet_end = 8, quiet_timezone = 'Asia/Singapore'
        WHERE id = 1
    """)


# ---------------------------------------------------------------------------
# record_attention_event() round-trip via the real production writer.
# ---------------------------------------------------------------------------


async def test_record_attention_event_round_trips(pool: asyncpg.Pool) -> None:
    row_id = await record_attention_event(
        pool,
        origin_butler="finance",
        source="insight",
        outcome="coalesced",
        channel="telegram",
        intent="insight",
        priority=80,
        dedup_key="finance:bill-due:abc:2026-01-01",
        reason=None,
        notification_ref="candidate-42",
        metadata={"insight_count": 3},
    )
    assert row_id is not None

    row = await pool.fetchrow("SELECT * FROM public.attention_ledger WHERE id = $1::uuid", row_id)
    assert row is not None
    assert row["origin_butler"] == "finance"
    assert row["source"] == "insight"
    assert row["outcome"] == "coalesced"
    assert row["priority_label"] == "80"
    assert row["priority_score"] == 80
    assert row["dedup_key"] == "finance:bill-due:abc:2026-01-01"
    assert row["notification_ref"] == "candidate-42"
    # This bare asyncpg.create_pool() has no custom jsonb->dict codec (unlike
    # the production Database.connect() pool), so jsonb columns read back as
    # raw JSON text here — parse before comparing.
    stored_metadata = row["metadata"]
    if isinstance(stored_metadata, str):
        stored_metadata = json.loads(stored_metadata)
    assert stored_metadata == {"insight_count": 3}


async def test_record_attention_event_notify_coalesced_round_trips(pool: asyncpg.Pool) -> None:
    """bu-o8233 (JARVIS pursuit move 8 slice 4): same-window coalescing at the
    notify() boundary (``_tick_deferred_notification_pass``) records
    ``source="notify"`` + ``outcome="coalesced"`` — a combination the insight
    engine already used, but notify() never had before this change. Confirms
    the real CHECK constraint (``chk_attention_ledger_outcome``) permits
    'coalesced' for source='notify', not just source='insight'.
    """
    row_id = await record_attention_event(
        pool,
        origin_butler="finance",
        source="notify",
        outcome="coalesced",
        channel="telegram",
        intent="send",
        priority="medium",
        notification_ref="deferred-notification-7",
    )
    assert row_id is not None

    row = await pool.fetchrow("SELECT * FROM public.attention_ledger WHERE id = $1::uuid", row_id)
    assert row is not None
    assert row["source"] == "notify"
    assert row["outcome"] == "coalesced"
    assert row["priority_label"] == "medium"
    assert row["priority_score"] == 50
    assert row["notification_ref"] == "deferred-notification-7"


async def test_record_attention_event_notify_priority_label_normalization(
    pool: asyncpg.Pool,
) -> None:
    row_id = await record_attention_event(
        pool,
        origin_butler="health",
        source="notify",
        outcome="suppressed",
        channel="telegram",
        intent="send",
        priority="high",
        reason="quiet_hours",
    )
    assert row_id is not None
    row = await pool.fetchrow(
        "SELECT priority_label, priority_score, reason FROM public.attention_ledger WHERE id = $1::uuid",
        row_id,
    )
    assert row["priority_label"] == "high"
    assert row["priority_score"] == 90
    assert row["reason"] == "quiet_hours"


# ---------------------------------------------------------------------------
# Reader endpoint queries (bu-tdd4k.4) -- against the real table, not a
# mocked pool. Uses origin_butler names not touched by any test above so
# counts stay deterministic regardless of module-scoped DB/test ordering.
# ---------------------------------------------------------------------------


async def test_query_ledger_list_filters_by_outcome_and_origin_butler(pool: asyncpg.Pool) -> None:
    for outcome in ("delivered", "suppressed", "suppressed"):
        await record_attention_event(
            pool,
            origin_butler="reader_test_alpha",
            source="notify",
            outcome=outcome,
            channel="telegram",
            intent="send",
        )
    await record_attention_event(
        pool,
        origin_butler="reader_test_beta",
        source="notify",
        outcome="suppressed",
        channel="telegram",
        intent="send",
    )

    result = await _query_ledger(
        pool,
        offset=0,
        limit=50,
        since=None,
        until=None,
        intent=None,
        source=None,
        outcome="suppressed",
        origin_butler="reader_test_alpha",
    )

    assert result.meta.total == 2
    assert len(result.data) == 2
    assert all(e.origin_butler == "reader_test_alpha" for e in result.data)
    assert all(e.outcome == "suppressed" for e in result.data)
    # newest-first ordering
    assert result.data[0].occurred_at >= result.data[1].occurred_at


async def test_query_ledger_list_windows_by_since_until(pool: asyncpg.Pool) -> None:
    import datetime as dt

    await record_attention_event(
        pool,
        origin_butler="reader_test_gamma",
        source="insight",
        outcome="delivered",
        intent="insight",
    )

    far_future = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    far_past = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)

    in_window = await _query_ledger(
        pool,
        offset=0,
        limit=50,
        since=far_past,
        until=far_future,
        intent=None,
        source=None,
        outcome=None,
        origin_butler="reader_test_gamma",
    )
    assert in_window.meta.total == 1

    out_of_window = await _query_ledger(
        pool,
        offset=0,
        limit=50,
        since=far_future,
        until=None,
        intent=None,
        source=None,
        outcome=None,
        origin_butler="reader_test_gamma",
    )
    assert out_of_window.meta.total == 0


async def test_query_ledger_summary_flags_suppressed_never_delivered(pool: asyncpg.Pool) -> None:
    """The real GROUP BY/FILTER SQL against Postgres -- not a mocked
    aggregate -- computes suppressed_never_delivered exactly as the epic's
    live secrets_lifecycle failure required (120 suppressed / 0 delivered)."""
    import datetime as dt

    for _ in range(3):
        await record_attention_event(
            pool,
            origin_butler="reader_test_delta",
            source="notify",
            outcome="suppressed",
            channel="telegram",
            intent="send",
        )
    await record_attention_event(
        pool,
        origin_butler="reader_test_epsilon",
        source="notify",
        outcome="delivered",
        channel="telegram",
        intent="send",
    )
    await record_attention_event(
        pool,
        origin_butler="reader_test_epsilon",
        source="notify",
        outcome="suppressed",
        channel="telegram",
        intent="send",
    )

    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    result = await _query_ledger_summary(
        pool,
        since=since,
        until=None,
        intent=None,
        source=None,
        origin_butler=None,
    )

    by_name = {s.origin_butler: s for s in result.by_source}
    assert by_name["reader_test_delta"].suppressed == 3
    assert by_name["reader_test_delta"].delivered == 0
    assert by_name["reader_test_delta"].suppressed_never_delivered is True
    assert by_name["reader_test_epsilon"].delivered == 1
    assert by_name["reader_test_epsilon"].suppressed_never_delivered is False
    assert "reader_test_delta" in result.flagged_sources
    assert "reader_test_epsilon" not in result.flagged_sources
