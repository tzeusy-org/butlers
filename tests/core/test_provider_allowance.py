"""Provider allowance windows: account-scoped exhaustion (bu-q7vx1q.13).

A provider plan usage-limit exhausts the whole provider account until its reset.
These tests pin the routing seam (every resolver drops every entry on an exhausted
account, a different account is still selected, the exclusion lifts at ``reset_at``),
the state writer's idempotence rules, and honest rendering of unknown state.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.core.dispatch_intent import derive_dispatch_intent
from butlers.core.model_routing import (
    ALLOWANCE_DEFAULT_WINDOW,
    ALLOWANCE_OUTCOME,
    CandidateOutcome,
    Complexity,
    allowance_deferral,
    clear_allowance_exhaustion,
    clear_routing_decision_cache,
    get_breaker_state,
    mark_allowance_exhausted,
    next_same_tier_candidate,
    resolve_dispatch,
    resolve_model,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
db_only = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

# ---------------------------------------------------------------------------
# Unit tests: no database
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_unparsed_reset_falls_back_to_default_window_and_says_so() -> None:
    pool = AsyncMock()
    pool.fetchval.return_value = "codex"
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    written = await mark_allowance_exhausted(
        pool, uuid.uuid4(), reset_at=None, attempt_id=7, now=now
    )

    assert written == now + ALLOWANCE_DEFAULT_WINDOW
    args = pool.execute.await_args.args
    assert args[1:] == ("codex", now + ALLOWANCE_DEFAULT_WINDOW, "default_window", 7)


@pytest.mark.unit
async def test_parsed_reset_is_recorded_as_parsed() -> None:
    pool = AsyncMock()
    pool.fetchval.return_value = "codex"
    reset = datetime(2026, 10, 3, 15, 10, tzinfo=UTC)

    await mark_allowance_exhausted(pool, uuid.uuid4(), reset_at=reset, attempt_id=None)

    assert pool.execute.await_args.args[1:] == ("codex", reset, "parsed", None)


@pytest.mark.unit
async def test_allowance_write_failure_never_raises_into_failover() -> None:
    pool = AsyncMock()
    pool.fetchval.return_value = "codex"
    pool.execute.side_effect = asyncpg.PostgresError("boom")

    assert await mark_allowance_exhausted(pool, uuid.uuid4(), reset_at=None, attempt_id=1) is None


@pytest.mark.unit
async def test_deferral_lookup_failure_fails_open() -> None:
    pool = AsyncMock()
    pool.fetchrow.side_effect = RuntimeError("db down")

    assert await allowance_deferral(pool, "general", Complexity.WORKHORSE) is None


@pytest.mark.unit
async def test_deferral_ignores_non_datetime_rows() -> None:
    """A mock or empty aggregate row (no reset) never reads as a deferral."""
    pool = AsyncMock()
    pool.fetchrow.return_value = {"reset_at": None}

    assert await allowance_deferral(pool, "general", "workhorse") is None


# ---------------------------------------------------------------------------
# Real-Postgres tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str):
    clear_routing_decision_cache()
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    await p.execute(
        "TRUNCATE public.model_round_robin_counters, public.butler_model_overrides, "
        "public.token_limits, public.token_usage_ledger, public.model_catalog, "
        "public.provider_allowance_states CASCADE"
    )
    yield p
    await p.execute("TRUNCATE public.provider_allowance_states")
    await p.close()


async def _entry(
    pool: asyncpg.Pool,
    alias: str,
    *,
    runtime_type: str,
    tier: str = "workhorse",
    priority: int = 0,
    allowance_account: str | None = None,
) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority,
             session_timeout_s, allowance_account)
        VALUES ($1, $2, $3, $4::jsonb, $5, true, $6, 1800, $7)
        RETURNING id
        """,
        alias,
        runtime_type,
        f"{alias}-model",
        json.dumps([]),
        tier,
        priority,
        allowance_account,
    )


async def _exhaust(
    pool: asyncpg.Pool, account_key: str, reset_at: datetime, state: str = "exhausted"
) -> None:
    await pool.execute(
        "INSERT INTO public.provider_allowance_states (account_key, state, reset_at, reset_source) "
        "VALUES ($1, $2, $3, 'parsed')",
        account_key,
        state,
        reset_at,
    )


def _later() -> datetime:
    return datetime.now(UTC) + timedelta(hours=2)


class TestAccountScopedExclusion:
    pytestmark = db_only

    async def test_exhausted_account_excludes_every_entry_on_it(self, pool) -> None:
        dead_hi = await _entry(pool, "codex-hi", runtime_type="codex", priority=90)
        dead_lo = await _entry(pool, "codex-lo", runtime_type="codex", priority=80)
        live = await _entry(pool, "opencode", runtime_type="opencode", priority=10)
        await _exhaust(pool, "codex", _later())

        nxt = await next_same_tier_candidate(pool, "general", "workhorse", [])
        assert nxt is not None and nxt[3] == live
        resolved = await resolve_model(pool, "general", Complexity.WORKHORSE)
        assert resolved is not None and resolved[3] == live
        # The sibling on the dead account is excluded even though it was never attempted.
        after_live = await next_same_tier_candidate(pool, "general", "workhorse", [live])
        assert after_live is None, (dead_hi, dead_lo)

    async def test_allowance_account_column_groups_entries_across_runtime_types(self, pool) -> None:
        a = await _entry(pool, "a", runtime_type="codex", allowance_account="shared-plan")
        b = await _entry(pool, "b", runtime_type="opencode", allowance_account="shared-plan")
        c = await _entry(pool, "c", runtime_type="claude")
        await _exhaust(pool, "shared-plan", _later())

        nxt = await next_same_tier_candidate(pool, "general", "workhorse", [])
        assert nxt is not None and nxt[3] == c, (a, b)

    async def test_exclusion_lifts_at_reset_at(self, pool) -> None:
        only = await _entry(pool, "codex-only", runtime_type="codex")
        await _exhaust(pool, "codex", datetime.now(UTC) + timedelta(seconds=1))
        assert await next_same_tier_candidate(pool, "general", "workhorse", []) is None

        await pool.execute(
            "UPDATE public.provider_allowance_states SET reset_at = now() - interval '1 second'"
        )
        nxt = await next_same_tier_candidate(pool, "general", "workhorse", [])
        assert nxt is not None and nxt[3] == only

    async def test_unknown_state_does_not_exclude(self, pool) -> None:
        only = await _entry(pool, "codex-only", runtime_type="codex")
        await _exhaust(pool, "codex", _later(), state="unknown")

        nxt = await next_same_tier_candidate(pool, "general", "workhorse", [])
        assert nxt is not None and nxt[3] == only

    async def test_receipt_resolution_names_the_allowance_exclusion(self, pool) -> None:
        dead = await _entry(pool, "codex", runtime_type="codex", priority=50)
        live = await _entry(pool, "opencode", runtime_type="opencode", priority=10)
        await _exhaust(pool, "codex", _later())

        resolution = await resolve_dispatch(
            pool,
            "general",
            derive_dispatch_intent("external", Complexity.WORKHORSE),
            allow_tier_fallthrough=False,
        )

        outcomes = {c.catalog_entry_id: c for c in resolution.candidates}
        assert resolution.selection is not None and resolution.selection[3] == live
        assert outcomes[dead].outcome is CandidateOutcome.EXCLUDED_ALLOWANCE
        assert outcomes[dead].describe()["exclusion"] == "allowance_exhausted"

    async def test_all_candidates_exhausted_is_not_hard_fit(self, pool) -> None:
        await _entry(pool, "codex", runtime_type="codex")
        await _exhaust(pool, "codex", _later())

        resolution = await resolve_dispatch(
            pool,
            "general",
            derive_dispatch_intent("external", Complexity.WORKHORSE),
            allow_tier_fallthrough=False,
        )

        assert resolution.selection is None
        assert [c.outcome for c in resolution.candidates] == [CandidateOutcome.EXCLUDED_ALLOWANCE]


class TestAllowanceStateWriter:
    pytestmark = db_only

    async def test_repeat_rejection_keeps_latest_reset_and_first_seen(self, pool) -> None:
        entry = await _entry(pool, "codex", runtime_type="codex")
        later = datetime.now(UTC) + timedelta(hours=5)
        sooner = datetime.now(UTC) + timedelta(hours=1)

        await mark_allowance_exhausted(pool, entry, reset_at=later, attempt_id=None)
        first = await pool.fetchrow("SELECT * FROM public.provider_allowance_states")
        await mark_allowance_exhausted(pool, entry, reset_at=sooner, attempt_id=None)
        row = await pool.fetchrow("SELECT * FROM public.provider_allowance_states")

        assert row["reset_at"] == later
        assert row["reset_source"] == "parsed"
        assert row["first_seen_at"] == first["first_seen_at"]
        assert await pool.fetchval("SELECT count(*) FROM public.provider_allowance_states") == 1

    async def test_default_window_never_shortens_a_parsed_reset(self, pool) -> None:
        entry = await _entry(pool, "codex", runtime_type="codex")
        parsed = datetime.now(UTC) + timedelta(hours=5)

        await mark_allowance_exhausted(pool, entry, reset_at=parsed, attempt_id=None)
        await mark_allowance_exhausted(pool, entry, reset_at=None, attempt_id=None)
        row = await pool.fetchrow("SELECT * FROM public.provider_allowance_states")

        assert row["reset_at"] == parsed and row["reset_source"] == "parsed"

    async def test_success_clears_only_an_exhausted_account(self, pool) -> None:
        entry = await _entry(pool, "codex", runtime_type="codex")
        other = await _entry(pool, "opencode", runtime_type="opencode")
        await mark_allowance_exhausted(pool, entry, reset_at=_later(), attempt_id=None)

        await clear_allowance_exhaustion(pool, other)  # different account: untouched
        assert await pool.fetchval("SELECT state FROM public.provider_allowance_states") == (
            "exhausted"
        )
        await clear_allowance_exhaustion(pool, entry)
        row = await pool.fetchrow("SELECT * FROM public.provider_allowance_states")
        assert row["state"] == "available" and row["reset_at"] is None
        nxt = await next_same_tier_candidate(pool, "general", "workhorse", [])
        assert nxt is not None

    async def test_allowance_outcome_never_trips_the_breaker(self, pool) -> None:
        entry = await _entry(pool, "codex", runtime_type="codex")
        for _i in range(10):
            await pool.execute(
                "INSERT INTO public.model_dispatch_attempts "
                "(catalog_entry_id, butler, outcome, attempt_index) VALUES ($1, 'general', $2, 0)",
                entry,
                ALLOWANCE_OUTCOME,
            )

        state = await get_breaker_state(pool, entry)
        assert state.open is False


class TestAllowanceDeferral:
    pytestmark = db_only

    async def test_defers_to_earliest_reset_only_when_every_entry_is_blocked(self, pool) -> None:
        await _entry(pool, "codex", runtime_type="codex")
        await _entry(pool, "opencode", runtime_type="opencode")
        soon, later = datetime.now(UTC) + timedelta(hours=1), _later()
        await _exhaust(pool, "codex", later)
        assert await allowance_deferral(pool, "general", Complexity.WORKHORSE) is None

        await _exhaust(pool, "opencode", soon)
        assert await allowance_deferral(pool, "general", Complexity.WORKHORSE) == soon

    async def test_an_available_lower_tier_entry_prevents_deferral(self, pool) -> None:
        await _entry(pool, "codex", runtime_type="codex", tier="workhorse")
        await _entry(pool, "local", runtime_type="opencode", tier="cheap")
        await _exhaust(pool, "codex", _later())

        assert await allowance_deferral(pool, "general", Complexity.WORKHORSE) is None

    async def test_empty_catalog_does_not_defer(self, pool) -> None:
        assert await allowance_deferral(pool, "general", Complexity.WORKHORSE) is None
