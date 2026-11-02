"""Real-Postgres regression: the attention ledger + Owner Attention Policy.

Exercises core_160 (bu-qvnce.8, move 8 slice 1) against a fully migrated
Postgres instance (testcontainers) — not just mocked-pool unit tests:

- ``public.attention_ledger`` is created with the expected columns and CHECK
  constraints (source/outcome vocabulary, priority_score range).
- A fresh, never-configured install gets sane Owner Attention Policy defaults
  in ``public.approvals_policy`` — 23:00-08:00 Asia/Singapore — without any
  owner action.
- The canonical seed is idempotent/non-destructive: an owner who already
  configured the policy before the seed ran keeps their configuration.
- A fully migrated database has no legacy quiet-hour columns on
  ``public.insight_settings``.
- ``record_attention_event`` round-trips through the real table via the
  actual production writer in ``butlers.core.attention_ledger``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.api.routers.attention_ledger import _query_ledger, _query_ledger_summary
from butlers.core.attention_ledger import record_attention_event
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.switchboard.insight.broker import expire_candidates
from scripts import repair_attention_ledger_metadata as metadata_repair

docker_available = shutil.which("docker") is not None
_TEST_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
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
# Seeded Owner Attention Policy (fresh, never-configured install)
# ---------------------------------------------------------------------------


async def test_approvals_policy_seeded_to_asia_singapore_defaults(pool: asyncpg.Pool) -> None:
    row = await pool.fetchrow(
        "SELECT quiet_start_hour, quiet_end_hour, timezone FROM public.approvals_policy WHERE id = 1"
    )
    assert row is not None
    assert row["quiet_start_hour"] == 23
    assert row["quiet_end_hour"] == 8
    assert row["timezone"] == "Asia/Singapore"


async def test_insight_settings_has_no_legacy_quiet_hour_columns(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'insight_settings'
          AND column_name IN ('quiet_start', 'quiet_end', 'quiet_timezone')
        """
    )
    assert rows == []


# ---------------------------------------------------------------------------
# Idempotency: a pre-existing owner configuration is never clobbered.
#
# This re-runs core_160's exact canonical guarded seed SQL (WHERE ... IS NULL)
# directly against a row that already carries a custom (non-default)
# configuration. If core_160's guard clause changes, update both the migration
# and this test together.
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


async def test_expired_outcome_round_trips_and_is_summarized_per_origin(
    pool: asyncpg.Pool,
) -> None:
    test_now = _TEST_NOW
    row_id = await record_attention_event(
        pool,
        origin_butler="health",
        source="insight",
        outcome="expired",
        intent="insight",
        dedup_key="health:signal:today",
        reason="blocked_by:budget",
        notification_ref="candidate-expired",
        metadata={"blocked_by": "budget"},
    )
    assert row_id is not None

    row = await pool.fetchrow(
        "SELECT outcome, reason FROM public.attention_ledger WHERE id=$1::uuid", row_id
    )
    assert dict(row) == {"outcome": "expired", "reason": "blocked_by:budget"}

    summary = await _query_ledger_summary(
        pool,
        since=test_now - timedelta(minutes=1),
        until=None,
        intent="insight",
        source="insight",
        origin_butler="health",
    )
    assert summary.by_source[0].expired_unseen >= 1


async def test_expire_candidates_writes_one_blocked_by_row_per_expiry(
    pool: asyncpg.Pool,
) -> None:
    test_now = _TEST_NOW
    candidate_ids = await pool.fetch(
        """
        INSERT INTO public.insight_candidates
            (origin_butler, priority, category, dedup_key, expires_at, message, metadata)
        VALUES
            ('finance', 60, 'Bills', 'finance:bill:expired-a', $1,
             'a', '{"blocked_by":"held_by"}'::jsonb),
            ('finance', 60, 'Bills', 'finance:bill:expired-b', $2,
             'b', '{"blocked_by":"dedup"}'::jsonb)
        RETURNING id
        """,
        test_now - timedelta(hours=1),
        test_now - timedelta(hours=2),
    )

    assert await expire_candidates(pool, now=test_now) == 2
    rows = await pool.fetch(
        """
        SELECT notification_ref, outcome, reason, dedup_key
        FROM public.attention_ledger
        WHERE notification_ref = ANY($1::text[])
        ORDER BY notification_ref
        """,
        [str(row["id"]) for row in candidate_ids],
    )
    assert {(row["outcome"], row["reason"]) for row in rows} == {
        ("expired", "blocked_by:held_by"),
        ("expired", "blocked_by:dedup"),
    }
    assert {row["dedup_key"] for row in rows} == {None}

    listed = await _query_ledger(
        pool,
        offset=0,
        limit=50,
        since=test_now - timedelta(days=1),
        until=None,
        intent="insight",
        source="insight",
        outcome="expired",
        origin_butler="finance",
    )
    expected_refs = {str(row["id"]) for row in candidate_ids}
    expired_entries = [entry for entry in listed.data if entry.notification_ref in expected_refs]
    assert len(expired_entries) == 2
    assert all(entry.dedup_key is None for entry in expired_entries)


async def test_expire_candidates_keeps_candidate_pending_when_ledger_write_fails(
    pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from butlers.tools.switchboard.insight import broker as insight_broker

    test_now = _TEST_NOW
    candidate_id = await pool.fetchval(
        """
        INSERT INTO public.insight_candidates
            (origin_butler, priority, category, dedup_key, expires_at, message)
        VALUES ('finance', 60, 'Bills', 'finance:bill:ledger-retry', $1, 'retry me')
        RETURNING id
        """,
        test_now - timedelta(hours=1),
    )

    production_writer = insight_broker.record_attention_event

    async def unavailable_ledger(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(insight_broker, "record_attention_event", unavailable_ledger)

    with pytest.raises(RuntimeError, match="expiry ledger"):
        await expire_candidates(pool, now=test_now)

    assert (
        await pool.fetchval(
            "SELECT status FROM public.insight_candidates WHERE id = $1", candidate_id
        )
        == "pending"
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.attention_ledger WHERE notification_ref = $1",
            str(candidate_id),
        )
        == 0
    )

    monkeypatch.setattr(insight_broker, "record_attention_event", production_writer)
    assert await expire_candidates(pool, now=test_now) == 1
    assert (
        await pool.fetchval(
            "SELECT status FROM public.insight_candidates WHERE id = $1", candidate_id
        )
        == "expired"
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.attention_ledger WHERE notification_ref = $1",
            str(candidate_id),
        )
        == 1
    )


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


@pytest.mark.pg_clock
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


@pytest.mark.pg_clock
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


@pytest.mark.parametrize("production_codec", [False, True], ids=["bare", "production-codec"])
async def test_exact_metadata_repair_preserves_native_values_and_atomicity(
    migrated_db_url: str, tmp_path, production_codec: bool
) -> None:
    """Real text->JSONB casts, normal-role SQL, independent readback and rollback.

    The failure/cancellation adapter delegates every actual statement/transaction
    to PostgreSQL, then raises after the first real UPDATE. It cannot simulate a
    committed/rolled-back database result.
    """
    options, target = metadata_repair.connection_options(migrated_db_url + "?sslmode=disable")

    async def ordinary_role(conn):
        await conn.execute('SET ROLE "butler_switchboard_rw"')
        assert await conn.fetchval("SELECT current_user") == metadata_repair.ROLE

    @asynccontextmanager
    async def carrier():
        if production_codec:
            async with metadata_repair.open_database(options) as selected:
                yield selected
        else:
            selected = await asyncpg.create_pool(
                migrated_db_url, min_size=1, max_size=1, setup=ordinary_role, command_timeout=30
            )
            try:
                yield selected
            finally:
                await selected.close()

    ids = sorted([uuid4(), uuid4()])
    session = str(uuid4())
    inner = (
        '{"session_id":"' + session + '","n":0.12345678901234567890123456789,'
        '"nested":{"quoted":"a\\nb"}}'
    )
    original = [json.dumps(inner), json.dumps('{"other":{"legitimate":[1,true,null]}}')]
    async with carrier() as selected:
        for row_id, encoded in zip(ids, original, strict=True):
            await selected.execute(
                """INSERT INTO public.attention_ledger
                (id,origin_butler,source,outcome,metadata) VALUES
                ($1,'chronicler','notify','delivered',$2::text::jsonb)""",
                row_id,
                encoded,
            )
        plan = await metadata_repair.plan_repair(selected, ids, target=target)
        assert [r["status"] for r in plan["rows"]] == ["repair", "repair"]
        for suffix, fault in (
            ("failure", RuntimeError("injected")),
            ("cancel", asyncio.CancelledError()),
        ):

            @asynccontextmanager
            async def failing_acquire(*, failure=fault, **kwargs):
                async with selected.acquire(**kwargs) as real:

                    class FailAfterUpdate:
                        transaction = real.transaction
                        fetchrow = real.fetchrow
                        fetch = real.fetch
                        fetchval = real.fetchval

                        async def execute(self, sql, *args, **execute_kwargs):
                            await real.execute(sql, *args, **execute_kwargs)
                            raise failure

                    yield FailAfterUpdate()

            class FailingPool:
                acquire = staticmethod(failing_acquire)

            with pytest.raises(type(fault)):
                await metadata_repair.apply_repair(
                    FailingPool(), plan, target=target, beforeimages=tmp_path / suffix
                )
            async with carrier() as observer:
                assert (
                    await observer.fetchval(
                        "SELECT bool_and(jsonb_typeof(metadata)='string') "
                        "FROM public.attention_ledger WHERE id=ANY($1::uuid[])",
                        ids,
                    )
                    is True
                )
        # A stale member prevents even the first eligible row from changing.
        await selected.execute(metadata_repair.UPDATE, ids[1], '{"drift":true}')
        with pytest.raises(metadata_repair.Refused):
            await metadata_repair.apply_repair(
                selected, plan, target=target, beforeimages=tmp_path / "stale"
            )
        assert (
            await selected.fetchval(
                "SELECT jsonb_typeof(metadata) FROM public.attention_ledger WHERE id=$1", ids[0]
            )
            == "string"
        )
        await selected.execute(metadata_repair.UPDATE, ids[1], original[1])
        result, texts = await metadata_repair.apply_repair(
            selected, plan, target=target, beforeimages=tmp_path / "before"
        )
        assert result == {"updated": 2, "already_applied": 0, "unchanged": 0}
    async with carrier() as observer:
        await metadata_repair.verify_repair(observer, plan, target=target, expected_texts=texts)
        assert (
            await observer.fetchval(
                "SELECT metadata=$2::text::jsonb AND metadata->>'session_id'=$3 "
                "FROM public.attention_ledger WHERE id=$1",
                ids[0],
                inner,
                session,
            )
            is True
        )
        from butlers.core.attention_ledger import find_notify_dispatch_for_session

        dispatch = await find_notify_dispatch_for_session(
            observer, origin_butler="chronicler", session_id=session
        )
        assert dispatch is not None and dispatch.outcome == "delivered"
        result, repeat_texts = await metadata_repair.apply_repair(
            observer, plan, target=target, beforeimages=tmp_path / "repeat"
        )
        assert result == {"updated": 0, "already_applied": 2, "unchanged": 0}
        # A real JSONB string can contain malformed/duplicate-key inner JSON.
        await observer.execute(metadata_repair.UPDATE, ids[1], json.dumps('{"x":1,"x":2}'))
        malformed = await metadata_repair.plan_repair(observer, ids, target=target)
        assert malformed["rows"][1]["status"] == "refused"
        with pytest.raises(metadata_repair.Refused):
            await metadata_repair.apply_repair(
                observer, malformed, target=target, beforeimages=tmp_path / "malformed"
            )
    async with carrier() as independent:
        # The original reviewed plan still detects the malformed-row drift.
        with pytest.raises(metadata_repair.Refused):
            await metadata_repair.verify_repair(
                independent, plan, target=target, expected_texts=repeat_texts
            )
