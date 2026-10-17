"""Provider allowance windows: account-scoped exhaustion (bu-q7vx1q.13).

A provider plan usage-limit exhausts the whole provider account until its reset.
These tests pin the routing seam (every resolver drops every entry on an exhausted
account, a different account is still selected, the exclusion lifts at ``reset_at``),
the state writer's idempotence rules, and honest rendering of unknown state.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.dispatch_intent import derive_dispatch_intent
from butlers.core.model_routing import (
    ALLOWANCE_DEFAULT_WINDOW,
    ALLOWANCE_OUTCOME,
    AllowanceAttemptContext,
    CandidateOutcome,
    Complexity,
    allowance_deferral,
    capture_allowance_attempt,
    clear_allowance_exhaustion,
    clear_routing_decision_cache,
    get_breaker_state,
    mark_allowance_exhausted,
    next_same_tier_candidate,
    resolve_dispatch,
    resolve_model,
)
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner
from butlers.db import register_jsonb_codec
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

    entry = uuid.uuid4()
    started = datetime(2026, 10, 3, 12, tzinfo=UTC)
    observed = started - timedelta(seconds=1)
    pool.fetchrow.return_value = {
        "account_key": "codex",
        "started_at": started,
        "observed_rejection_at": observed,
    }
    context = await capture_allowance_attempt(pool, entry)
    assert context == AllowanceAttemptContext("codex", started, observed)
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.started_at = started + timedelta(seconds=1)
    with patch("butlers.core.model_routing.clear_routing_decision_cache") as invalidate:
        await clear_allowance_exhaustion(pool, entry, attempt_context=context)
        invalidate.assert_not_called()  # Database error cannot invalidate or escape.
        assert pool.execute.await_args.args[1:] == (entry, "codex", started, observed)
        pool.execute.side_effect = None
        pool.execute.return_value = "UPDATE 0"
        await clear_allowance_exhaustion(pool, entry, attempt_context=context)
        invalidate.assert_not_called()
        pool.execute.reset_mock()
        for missing in (None, dataclasses.replace(context, observed_rejection_at=None)):
            await clear_allowance_exhaustion(pool, entry, attempt_context=missing)
        pool.execute.assert_not_awaited()
        pool.execute.return_value = "UPDATE 1"
        await clear_allowance_exhaustion(pool, entry, attempt_context=context)
        invalidate.assert_called_once_with()

    for row in (
        None,
        {},
        {"account_key": None, "started_at": started, "observed_rejection_at": observed},
        {
            "account_key": "codex",
            "started_at": started.replace(tzinfo=None),
            "observed_rejection_at": observed,
        },
        {
            "account_key": "codex",
            "started_at": started,
            "observed_rejection_at": observed.isoformat(),
        },
        {
            "account_key": "codex",
            "started_at": started,
            "observed_rejection_at": observed.replace(tzinfo=None),
        },
    ):
        pool.fetchrow.return_value = row
        assert await capture_allowance_attempt(pool, entry) is None
    pool.fetchrow.side_effect = asyncpg.PostgresError("synthetic capture unavailable")
    assert await capture_allowance_attempt(pool, entry) is None

    async def blocked(*_args):
        await asyncio.Event().wait()

    pool.fetchrow.side_effect = blocked
    with patch("butlers.core.model_routing.ALLOWANCE_CAPTURE_TIMEOUT_S", 0.001):
        assert await capture_allowance_attempt(pool, entry) is None
    pool.fetchrow.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await capture_allowance_attempt(pool, entry)


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


class _PausedSuccessAdapter(RuntimeAdapter):
    """Unpaid provider boundary; all allowance SQL and attempt recording remain real."""

    binary_name = "synthetic"

    def __init__(self) -> None:
        self.invoked = asyncio.Event()
        self.release = asyncio.Event()
        self.invoke_calls = 0

    async def invoke(self, **_kwargs):
        self.invoke_calls += 1
        self.invoked.set()
        await self.release.wait()
        return "synthetic success", [], None

    async def reset(self) -> None:
        pass

    def build_config_file(self, _mcp_servers, tmp_dir: Path) -> Path:
        path = tmp_dir / "synthetic.json"
        path.write_text("{}")
        return path

    def parse_system_prompt_file(self, _config_dir: Path) -> str:
        return ""


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

    async def test_allowance_account_column_groups_entries_across_runtime_types(
        self, pool, migrated_db_url
    ) -> None:
        a = await _entry(pool, "a", runtime_type="codex", allowance_account="shared-plan")
        b = await _entry(pool, "b", runtime_type="opencode", allowance_account="shared-plan")
        c = await _entry(pool, "c", runtime_type="claude")
        await _exhaust(pool, "shared-plan", _later())

        nxt = await next_same_tier_candidate(pool, "general", "workhorse", [])
        assert nxt is not None and nxt[3] == c, (a, b)

        # Preserve the cross-runtime grouping proof above, then exercise the
        # missing writer seam via the actual mounted API and three SQL consumers.
        from types import SimpleNamespace

        import httpx

        from butlers.api.routers import model_settings
        from butlers.db import register_jsonb_codec
        from tests.api.auth_helpers import create_authenticated_domain_app
        from tests.three_seams_helpers import baseline_function

        # This dedicated codec-aware pool does not change the old string-JSON fixture.
        api_pool = await asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
        )
        try:
            await api_pool.execute(
                "TRUNCATE public.provider_allowance_states, public.model_catalog CASCADE"
            )
            db = SimpleNamespace(credential_shared_pool=lambda: api_pool)
            old_writer = baseline_function("create_catalog_entry", vars(model_settings))
            # Historical writer executes its real INSERT but drops the requested label.
            for alias, account, priority in [("old-a", "acct-a", 90), ("old-b", "acct-b", 80)]:
                await old_writer(
                    model_settings.ModelCatalogCreate(
                        alias=alias,
                        runtime_type="codex",
                        model_id=alias,
                        allowance_account=account,
                        priority=priority,
                    ),
                    db,
                )
            old_rows = await api_pool.fetch(
                "SELECT id,allowance_account FROM public.model_catalog ORDER BY alias"
            )
            assert len(old_rows) == 2 and all(r["allowance_account"] is None for r in old_rows)
            await mark_allowance_exhausted(
                api_pool, old_rows[0]["id"], reset_at=_later(), attempt_id=None
            )
            clear_routing_decision_cache()
            assert await resolve_model(api_pool, "general", Complexity.WORKHORSE) is None
            assert await next_same_tier_candidate(api_pool, "general", "workhorse", []) is None
            old_dispatch = await resolve_dispatch(
                api_pool,
                "general",
                derive_dispatch_intent("external", Complexity.WORKHORSE),
                allow_tier_fallthrough=False,
            )
            assert old_dispatch.selection is None
            assert all(
                c.outcome is CandidateOutcome.EXCLUDED_ALLOWANCE for c in old_dispatch.candidates
            )

            await api_pool.execute(
                "TRUNCATE public.provider_allowance_states, public.model_catalog CASCADE"
            )
            app = create_authenticated_domain_app()
            app.dependency_overrides[model_settings._get_db_manager] = lambda: db
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                entries = {}
                for alias, account, priority in [
                    ("same-a", "acct-a", 90),
                    ("same-b", "acct-b", 80),
                    ("sibling-a", "acct-a", 70),
                    ("sentinel", "acct-c", 10),
                ]:
                    created = await client.post(
                        "/api/settings/models",
                        json={
                            "alias": alias,
                            "runtime_type": "codex",
                            "model_id": alias,
                            "allowance_account": account,
                            "priority": priority,
                        },
                    )
                    assert created.status_code == 201, created.text
                    assert created.json()["data"]["allowance_account"] == account
                    entries[alias] = uuid.UUID(created.json()["data"]["id"])
                async with api_pool.acquire() as readback:
                    actual = await readback.fetch(
                        "SELECT alias,runtime_type,allowance_account FROM public.model_catalog"
                    )
                assert {r["alias"]: r["allowance_account"] for r in actual} == {
                    "same-a": "acct-a",
                    "same-b": "acct-b",
                    "sibling-a": "acct-a",
                    "sentinel": "acct-c",
                }
                assert {r["runtime_type"] for r in actual} == {"codex"}
                await mark_allowance_exhausted(
                    api_pool, entries["same-a"], reset_at=_later(), attempt_id=None
                )
                clear_routing_decision_cache()
                resolved = await resolve_model(api_pool, "general", Complexity.WORKHORSE)
                assert resolved is not None and resolved[3] == entries["same-b"]
                nxt = await next_same_tier_candidate(api_pool, "general", "workhorse", [])
                assert nxt is not None and nxt[3] == entries["same-b"]
                resolution = await resolve_dispatch(
                    api_pool,
                    "general",
                    derive_dispatch_intent("external", Complexity.WORKHORSE),
                    allow_tier_fallthrough=False,
                )
                assert (
                    resolution.selection is not None
                    and resolution.selection[3] == entries["same-b"]
                )
                outcomes = {c.catalog_entry_id: c.outcome for c in resolution.candidates}
                assert outcomes[entries["same-a"]] is CandidateOutcome.EXCLUDED_ALLOWANCE
                assert outcomes[entries["sibling-a"]] is CandidateOutcome.EXCLUDED_ALLOWANCE
                assert outcomes[entries["sentinel"]] is not CandidateOutcome.EXCLUDED_ALLOWANCE
                for body, expected in [
                    ({"priority": 81}, "acct-b"),
                    ({"allowance_account": "acct-d"}, "acct-d"),
                    ({"allowance_account": None}, None),
                ]:
                    updated = await client.put(
                        f"/api/settings/models/{entries['same-b']}", json=body
                    )
                    assert (
                        updated.status_code == 200
                        and updated.json()["data"]["allowance_account"] == expected
                    )
                    assert (
                        await api_pool.fetchval(
                            "SELECT allowance_account FROM public.model_catalog WHERE id=$1",
                            entries["same-b"],
                        )
                        == expected
                    )
                stepped = await client.put(
                    f"/api/settings/models/{entries['same-a']}/priority", json={"delta": 1}
                )
                assert (
                    stepped.status_code == 200
                    and stepped.json()["data"]["allowance_account"] == "acct-a"
                )
                listed = await client.get("/api/settings/models")
                assert listed.status_code == 200 and len(listed.json()["data"]) == 4
        finally:
            clear_routing_decision_cache()
            await api_pool.close()

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

        original = dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states"))
        await clear_allowance_exhaustion(pool, entry)  # No evidence cannot authorize a clear.
        assert (
            dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states")) == original
        )
        context = await capture_allowance_attempt(pool, entry)
        assert context is not None and context.started_at > context.observed_rejection_at
        await clear_allowance_exhaustion(pool, other, attempt_context=context)
        # A different current account is untouched even with otherwise valid evidence.
        assert await pool.fetchval("SELECT state FROM public.provider_allowance_states") == (
            "exhausted"
        )

        # A repeat with the same horizon and nullable causing ID still replaces the
        # rejection fence. The old success must not erase any of its durable fields.
        await mark_allowance_exhausted(pool, entry, reset_at=original["reset_at"], attempt_id=None)
        repeated = dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states"))
        assert repeated["updated_at"] > original["updated_at"]
        assert repeated["first_seen_at"] == original["first_seen_at"]
        await clear_allowance_exhaustion(pool, entry, attempt_context=context)
        assert (
            dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states")) == repeated
        )
        context = await capture_allowance_attempt(pool, entry)
        equal = dataclasses.replace(context, started_at=repeated["updated_at"])
        await clear_allowance_exhaustion(pool, entry, attempt_context=equal)
        assert (
            dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states")) == repeated
        )
        second_success = await capture_allowance_attempt(pool, entry)
        await clear_allowance_exhaustion(pool, entry, attempt_context=context)
        row = await pool.fetchrow("SELECT * FROM public.provider_allowance_states")
        assert row["state"] == "available" and row["reset_at"] is None
        assert row["reset_source"] == "unknown"
        assert row["first_seen_at"] == original["first_seen_at"]
        assert row["last_rejection_attempt_id"] is None
        cleared = dict(row)
        await clear_allowance_exhaustion(pool, entry, attempt_context=context)
        assert (
            dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states")) == cleared
        )
        await clear_allowance_exhaustion(pool, entry, attempt_context=second_success)
        assert (
            dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states")) == cleared
        )
        nxt = await next_same_tier_candidate(pool, "general", "workhorse", [])
        assert nxt is not None

        # Missing/available/unknown at capture never clears a later rejection.
        for state in (None, "available", "unknown"):
            await pool.execute("DELETE FROM public.provider_allowance_states")
            if state is not None:
                await _exhaust(pool, "codex", _later(), state=state)
            context = await capture_allowance_attempt(pool, entry)
            assert context is not None and context.observed_rejection_at is None
            await mark_allowance_exhausted(pool, entry, reset_at=_later(), attempt_id=None)
            before = dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states"))
            await clear_allowance_exhaustion(pool, entry, attempt_context=context)
            assert (
                dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states"))
                == before
            )

        # A legacy/default-timestamp row needs no causing ID or migration. Explicit
        # cross-runtime labels share one account; unrelated accounts remain unchanged.
        await pool.execute("DELETE FROM public.provider_allowance_states")
        await _exhaust(pool, "codex", _later())
        await mark_allowance_exhausted(pool, other, reset_at=_later(), attempt_id=7)
        untouched = dict(
            await pool.fetchrow(
                "SELECT * FROM public.provider_allowance_states WHERE account_key='opencode'"
            )
        )
        sibling = await _entry(pool, "shared", runtime_type="opencode", allowance_account="codex")
        context = await capture_allowance_attempt(pool, sibling)
        assert context.account_key == "codex" and context.observed_rejection_at is not None
        await clear_allowance_exhaustion(pool, sibling, attempt_context=context)
        assert (
            await pool.fetchval(
                "SELECT state FROM public.provider_allowance_states WHERE account_key='codex'"
            )
            == "available"
        )
        assert (
            dict(
                await pool.fetchrow(
                    "SELECT * FROM public.provider_allowance_states WHERE account_key='opencode'"
                )
            )
            == untouched
        )

        await mark_allowance_exhausted(pool, sibling, reset_at=_later(), attempt_id=64)
        context = await capture_allowance_attempt(pool, sibling)
        await clear_allowance_exhaustion(pool, sibling, attempt_context=context)
        assert dict(
            await pool.fetchrow(
                "SELECT state, last_rejection_attempt_id FROM public.provider_allowance_states "
                "WHERE account_key='codex'"
            )
        ) == {"state": "available", "last_rejection_attempt_id": 64}

        # Account relabel and catalog deletion make the captured fence ineligible.
        await mark_allowance_exhausted(pool, entry, reset_at=_later(), attempt_id=None)
        context = await capture_allowance_attempt(pool, entry)
        before = dict(
            await pool.fetchrow(
                "SELECT * FROM public.provider_allowance_states WHERE account_key='codex'"
            )
        )
        await pool.execute(
            "UPDATE public.model_catalog SET allowance_account='opencode' WHERE id=$1", entry
        )
        await clear_allowance_exhaustion(pool, entry, attempt_context=context)
        await pool.execute("DELETE FROM public.model_catalog WHERE id=$1", entry)
        await clear_allowance_exhaustion(pool, entry, attempt_context=context)
        assert (
            dict(
                await pool.fetchrow(
                    "SELECT * FROM public.provider_allowance_states WHERE account_key='codex'"
                )
            )
            == before
        )
        assert await capture_allowance_attempt(pool, entry) is None

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

    async def test_inflight_spawner_success_preserves_later_account_rejection(
        self, pool, migrated_db_url: str, tmp_path: Path
    ) -> None:
        entry = await _entry(pool, "inflight", runtime_type=DEFAULT_RUNTIME_TYPE)
        intent = derive_dispatch_intent("tick", Complexity.WORKHORSE)
        # Establish that this exact entry fits the invocation's butler/tier/intent:
        # later None selections must mean allowance exclusion, not an empty catalog
        # or an unrelated hard-fit failure. These readers use the fixture pool,
        # independently of the runtime's capture/clear/provenance connection.
        async with pool.acquire() as reader:
            candidate = await next_same_tier_candidate(reader, "allowance-test", "workhorse", [])
            assert candidate is not None and candidate[3] == entry
            resolved = await resolve_model(
                reader, "allowance-test", Complexity.WORKHORSE, allow_tier_fallthrough=False
            )
            assert resolved is not None and resolved[3] == entry
            dispatch = await resolve_dispatch(
                reader, "allowance-test", intent, allow_tier_fallthrough=False
            )
            assert dispatch.selection is not None and dispatch.selection[3] == entry
            assert [(c.catalog_entry_id, c.outcome) for c in dispatch.candidates] == [
                (entry, CandidateOutcome.SELECTED)
            ]
            assert dispatch.candidates[0].exclusions == ()
            assert await allowance_deferral(reader, "allowance-test", Complexity.WORKHORSE) is None

        async def assert_retained_allowance_readers() -> None:
            # Each invocation acquires after the producer write/commit and reads the
            # real SQL; no session/selection patch supplies these reader answers.
            clear_routing_decision_cache()
            async with pool.acquire() as reader:
                candidate = await next_same_tier_candidate(
                    reader, "allowance-test", "workhorse", []
                )
                assert candidate is None
                resolved = await resolve_model(
                    reader, "allowance-test", Complexity.WORKHORSE, allow_tier_fallthrough=False
                )
                assert resolved is None
                dispatch = await resolve_dispatch(
                    reader, "allowance-test", intent, allow_tier_fallthrough=False
                )
                assert dispatch.selection is None
                assert [(c.catalog_entry_id, c.outcome) for c in dispatch.candidates] == [
                    (entry, CandidateOutcome.EXCLUDED_ALLOWANCE)
                ]
                assert dispatch.candidates[0].exclusions == ()
                assert (
                    await allowance_deferral(reader, "allowance-test", Complexity.WORKHORSE)
                    == latest["reset_at"]
                )

        await mark_allowance_exhausted(pool, entry, reset_at=_later(), attempt_id=None)
        adapter = _PausedSuccessAdapter()
        session_id = uuid.uuid4()
        # The production JSONB codec is required by the real serialized attempt
        # recorder. Session/control transport and selection are synthetic here;
        # allowance capture/write/clear and durable success provenance are real.
        async with asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
        ) as runtime_pool:
            config = ButlerConfig(
                name="allowance-test",
                port=9100,
                runtime_seed=RuntimeSeedConfig(max_concurrent_sessions=1),
                modules={},
                env_required=[],
                env_optional=[],
            )
            with (
                patch(
                    "butlers.core.spawner.session_create",
                    new_callable=AsyncMock,
                    return_value=session_id,
                ),
                patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
                patch.object(Spawner, "_ensure_mcp_endpoints_warmed", new_callable=AsyncMock),
                patch(
                    "butlers.core.spawner.resolve_model_with_effective_tier",
                    new_callable=AsyncMock,
                    return_value=(
                        DEFAULT_RUNTIME_TYPE,
                        "inflight-model",
                        [],
                        entry,
                        1800,
                        "workhorse",
                    ),
                ),
            ):
                spawner = Spawner(config, tmp_path, pool=runtime_pool, runtime=adapter)
                task = asyncio.create_task(spawner.trigger("synthetic", "tick"))
                try:
                    await asyncio.wait_for(adapter.invoked.wait(), timeout=10)
                    await mark_allowance_exhausted(pool, entry, reset_at=_later(), attempt_id=None)
                    latest = dict(
                        await pool.fetchrow("SELECT * FROM public.provider_allowance_states")
                    )
                    await assert_retained_allowance_readers()
                finally:
                    adapter.release.set()
                    result = await asyncio.wait_for(task, timeout=10)
            assert result.success is True and result.output == "synthetic success"
            assert adapter.invoke_calls == 1
            assert (
                dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states"))
                == latest
            )
            attempts = await runtime_pool.fetch(
                "SELECT outcome, attempt_index, ts, session_id FROM public.model_dispatch_attempts "
                "WHERE catalog_entry_id=$1 ORDER BY attempt_index",
                entry,
            )
            assert len(attempts) == 1
            assert attempts[0]["outcome"] == "success" and attempts[0]["attempt_index"] == 0
            assert attempts[0]["session_id"] == session_id
            assert attempts[0]["ts"] >= latest["updated_at"]
            await assert_retained_allowance_readers()

    async def test_clear_and_rejection_serialize_without_losing_latest_exhaustion(
        self, pool
    ) -> None:
        entry = await _entry(pool, "race", runtime_type="codex")
        await mark_allowance_exhausted(pool, entry, reset_at=_later(), attempt_id=None)

        async def wait_for_lock(conn):
            # Observe the real PostgreSQL row-lock wait, rather than guessing the
            # order from sleeps. The third pool acquisition is read-only evidence.
            async with asyncio.timeout(5):
                while not await pool.fetchval(
                    "SELECT cardinality(pg_blocking_pids($1)) > 0", conn.get_server_pid()
                ):
                    await asyncio.sleep(0.01)

        async with pool.acquire() as rejection_conn, pool.acquire() as success_conn:
            # Rejection owns the row first: the waiting UPDATE must recheck the
            # committed replacement fence and leave every rejection field intact.
            context = await capture_allowance_attempt(pool, entry)
            async with rejection_conn.transaction():
                await mark_allowance_exhausted(
                    rejection_conn, entry, reset_at=_later(), attempt_id=11
                )
                latest = dict(
                    await rejection_conn.fetchrow("SELECT * FROM public.provider_allowance_states")
                )
                clearing = asyncio.create_task(
                    clear_allowance_exhaustion(success_conn, entry, attempt_context=context)
                )
                try:
                    await wait_for_lock(success_conn)
                except BaseException:
                    clearing.cancel()
                    await asyncio.gather(clearing, return_exceptions=True)
                    raise
            await asyncio.wait_for(clearing, timeout=5)
            assert (
                dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states"))
                == latest
            )

            # Success owns the row first: its eligible clear may commit, but the
            # waiting later rejection must be the final durable exhausted state.
            context = await capture_allowance_attempt(pool, entry)
            later_reset = _later() + timedelta(hours=1)
            async with success_conn.transaction():
                await clear_allowance_exhaustion(success_conn, entry, attempt_context=context)
                assert (
                    await success_conn.fetchval(
                        "SELECT state FROM public.provider_allowance_states"
                    )
                    == "available"
                )
                rejecting = asyncio.create_task(
                    mark_allowance_exhausted(
                        rejection_conn, entry, reset_at=later_reset, attempt_id=22
                    )
                )
                try:
                    await wait_for_lock(rejection_conn)
                except BaseException:
                    rejecting.cancel()
                    await asyncio.gather(rejecting, return_exceptions=True)
                    raise
            assert await asyncio.wait_for(rejecting, timeout=5) == later_reset
            latest = dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states"))
            assert latest["state"] == "exhausted" and latest["reset_at"] == later_reset
            assert latest["last_rejection_attempt_id"] == 22
            await clear_allowance_exhaustion(success_conn, entry, attempt_context=context)
            assert (
                dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states"))
                == latest
            )

            # An early BEGIN cannot backdate a later rejection, on either INSERT
            # or repeat UPDATE. SQL clock_timestamp is evaluated at the write.
            for existing in (True, False):
                if not existing:
                    await pool.execute("DELETE FROM public.provider_allowance_states")
                async with rejection_conn.transaction():
                    began = await rejection_conn.fetchval("SELECT now()")
                    context = await capture_allowance_attempt(success_conn, entry)
                    assert context.started_at > began
                    await mark_allowance_exhausted(
                        rejection_conn, entry, reset_at=later_reset, attempt_id=None
                    )
                    row = dict(
                        await rejection_conn.fetchrow(
                            "SELECT * FROM public.provider_allowance_states"
                        )
                    )
                    assert row["updated_at"] > context.started_at
                await clear_allowance_exhaustion(success_conn, entry, attempt_context=context)
                assert (
                    dict(await pool.fetchrow("SELECT * FROM public.provider_allowance_states"))
                    == row
                )


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
