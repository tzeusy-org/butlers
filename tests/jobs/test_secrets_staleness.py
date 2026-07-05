"""Tests for butlers.jobs.secrets_staleness — background probe engine (bu-a63hn).

Covers:
- _collect_probe_targets: aggregates system/cli/user families using the same
  fetch helpers as the inventory endpoint, skips never_set rows, skips plain
  'cli' rows (no live test path) and cli-auth rows for unregistered providers.
- _is_stale: never-verified is always stale; recent vs. past-window last_verified.
- _dispatch_probe: dispatches to the exact probe_* / test_api_key functions per
  family, and converts HTTPException / unexpected errors into skipped outcomes
  instead of raising.
- _sweep: serializes probes and trips a per-group circuit breaker after N
  consecutive failures, skipping the rest of that group for the sweep.
- run_secrets_staleness_check: filters to stale targets only, never raises.
- run_secrets_probe_all: sweeps every target regardless of staleness; a
  concurrent call while one is in flight raises ProbeAllAlreadyRunning.

No real database required — DatabaseManager and its pools are faked/mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from butlers.api.models import ApiMeta, ApiResponse
from butlers.api.routers.cli_auth import CLIAuthTestResponse
from butlers.api.routers.secrets_v2 import CliRuntime, SystemSecret, TestResult, UserSecret
from butlers.jobs.secrets_staleness import (
    ProbeAllAlreadyRunning,
    ProbeOutcome,
    ProbeTarget,
    _collect_probe_targets,
    _dispatch_probe,
    _is_stale,
    _sweep,
    run_secrets_probe_all,
    run_secrets_staleness_check,
)

pytestmark = pytest.mark.unit


class _FakeDatabaseManager:
    """Minimal stand-in for DatabaseManager: butler_names / pool / credential_shared_pool."""

    def __init__(self, *, butler_pools: dict[str, object], shared_pool: object | None):
        self._butler_pools = butler_pools
        self._shared_pool = shared_pool

    @property
    def butler_names(self) -> list[str]:
        return list(self._butler_pools)

    def pool(self, name: str):
        try:
            return self._butler_pools[name]
        except KeyError:
            raise KeyError(name) from None

    def credential_shared_pool(self):
        if self._shared_pool is None:
            raise KeyError("shared_pool")
        return self._shared_pool


# ---------------------------------------------------------------------------
# _collect_probe_targets
# ---------------------------------------------------------------------------


async def test_collect_probe_targets_aggregates_all_families():
    finance_pool = object()
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={"finance": finance_pool}, shared_pool=shared_pool)
    entity_id = str(uuid4())

    async def fake_fetch_system_secrets(pool, butler_name, **kwargs):
        if pool is finance_pool:
            return [SystemSecret(key="FINANCE_KEY", state="ok", butler="finance")]
        if pool is shared_pool:
            return [
                SystemSecret(key="BUTLER_TELEGRAM_TOKEN", state="expiring", butler="shared-public"),
                SystemSecret(
                    key="cli-should-be-excluded",
                    state="ok",
                    butler="shared-public",
                    category="cli",
                ),
                SystemSecret(key="NEVER_SET_KEY", state="never_set", butler="shared-public"),
            ]
        return []

    async def fake_fetch_cli_secrets(pool):
        return [
            CliRuntime(key="cli-auth/claude", state="failing"),
            CliRuntime(key="cli-auth/not-a-real-provider", state="ok"),
            CliRuntime(key="plain-cli-token", state="ok", category="cli"),
            CliRuntime(key="cli-auth/codex", state="never_set"),
        ]

    async def fake_fetch_user_secrets(pool, *, identity):
        assert identity is None
        return [
            UserSecret(id="u1", entity_id=entity_id, type="google_oauth_refresh", state="expiring"),
        ]

    with (
        patch(
            "butlers.jobs.secrets_staleness._fetch_system_secrets",
            side_effect=fake_fetch_system_secrets,
        ),
        patch(
            "butlers.jobs.secrets_staleness._fetch_cli_secrets",
            side_effect=fake_fetch_cli_secrets,
        ),
        patch(
            "butlers.jobs.secrets_staleness._fetch_user_secrets",
            side_effect=fake_fetch_user_secrets,
        ),
    ):
        targets = await _collect_probe_targets(db)

    by_key = {t.canonical_key: t for t in targets}

    assert by_key["s:FINANCE_KEY"].family == "system"
    assert by_key["s:BUTLER_TELEGRAM_TOKEN"].state == "expiring"
    assert "s:cli-should-be-excluded" not in by_key, "cli/cli-auth rows must not double-count"
    assert "s:NEVER_SET_KEY" not in by_key, "never_set system rows must be skipped"

    assert by_key["c:cli-auth/claude"].family == "cli"
    assert by_key["c:cli-auth/claude"].cli_provider == "claude"
    assert "c:cli-auth/not-a-real-provider" not in by_key, (
        "unregistered cli-auth provider must be skipped"
    )
    assert "c:plain-cli-token" not in by_key, "plain 'cli' rows have no live test path"
    assert "c:cli-auth/codex" not in by_key, "never_set cli rows must be skipped"

    assert by_key["u:google"].family == "user"
    assert by_key["u:google"].user_provider == "google"
    assert str(by_key["u:google"].user_identity) == entity_id


async def test_collect_probe_targets_returns_system_only_when_no_shared_pool():
    finance_pool = object()
    db = _FakeDatabaseManager(butler_pools={"finance": finance_pool}, shared_pool=None)

    async def fake_fetch_system_secrets(pool, butler_name, **kwargs):
        return [SystemSecret(key="FINANCE_KEY", state="ok", butler="finance")]

    with patch(
        "butlers.jobs.secrets_staleness._fetch_system_secrets",
        side_effect=fake_fetch_system_secrets,
    ):
        targets = await _collect_probe_targets(db)

    assert [t.canonical_key for t in targets] == ["s:FINANCE_KEY"]


# ---------------------------------------------------------------------------
# _is_stale
# ---------------------------------------------------------------------------


def _make_target(*, last_verified=None, state="ok") -> ProbeTarget:
    return ProbeTarget(
        canonical_key="s:KEY",
        family="system",
        label="KEY",
        state=state,
        last_verified=last_verified,
        circuit_group="system:KEY",
        system_key="KEY",
    )


def test_is_stale_true_when_never_verified():
    target = _make_target(last_verified=None)
    assert _is_stale(target, staleness_s=3600, now=datetime.now(UTC)) is True


def test_is_stale_false_when_recently_verified():
    now = datetime.now(UTC)
    target = _make_target(last_verified=now - timedelta(minutes=5))
    assert _is_stale(target, staleness_s=3600, now=now) is False


def test_is_stale_true_when_past_window():
    now = datetime.now(UTC)
    target = _make_target(last_verified=now - timedelta(hours=25))
    assert _is_stale(target, staleness_s=24 * 3600, now=now) is True


# ---------------------------------------------------------------------------
# _dispatch_probe
# ---------------------------------------------------------------------------


async def test_dispatch_probe_system_success():
    target = _make_target()
    fake_response = ApiResponse[TestResult](data=TestResult(ok=True, message=None), meta=ApiMeta())
    with patch(
        "butlers.jobs.secrets_staleness.probe_system_credential",
        new=AsyncMock(return_value=fake_response),
    ) as mock_probe:
        outcome = await _dispatch_probe(object(), target)

    mock_probe.assert_awaited_once()
    assert mock_probe.await_args.args[0] == "KEY"
    assert outcome.ok is True
    assert outcome.skipped is False


async def test_dispatch_probe_user_success():
    entity_id = uuid4()
    target = ProbeTarget(
        canonical_key="u:google",
        family="user",
        label="google",
        state="expiring",
        last_verified=None,
        circuit_group="user:google",
        user_provider="google",
        user_identity=entity_id,
    )
    fake_response = ApiResponse[TestResult](
        data=TestResult(ok=False, message="refresh failed"), meta=ApiMeta()
    )
    with patch(
        "butlers.jobs.secrets_staleness.probe_user_credential",
        new=AsyncMock(return_value=fake_response),
    ) as mock_probe:
        outcome = await _dispatch_probe(object(), target)

    mock_probe.assert_awaited_once()
    assert mock_probe.await_args.kwargs["identity"] == entity_id
    assert outcome.ok is False
    assert outcome.message == "refresh failed"


async def test_dispatch_probe_cli_success():
    target = ProbeTarget(
        canonical_key="c:cli-auth/claude",
        family="cli",
        label="cli-auth/claude",
        state="ok",
        last_verified=None,
        circuit_group="cli:claude",
        cli_provider="claude",
    )
    fake_result = CLIAuthTestResponse(provider="claude", success=True, detail="ok")
    with patch(
        "butlers.api.routers.cli_auth.test_api_key",
        new=AsyncMock(return_value=fake_result),
    ) as mock_test:
        outcome = await _dispatch_probe(object(), target)

    mock_test.assert_awaited_once()
    assert outcome.ok is True
    assert outcome.family == "cli"


async def test_dispatch_probe_rate_limited_is_skipped_not_raised():
    target = _make_target()
    with patch(
        "butlers.jobs.secrets_staleness.probe_system_credential",
        new=AsyncMock(side_effect=HTTPException(status_code=429, detail="slow down")),
    ):
        outcome = await _dispatch_probe(object(), target)

    assert outcome.ok is None
    assert outcome.skipped is True
    assert outcome.skip_reason == "rate_limited"


async def test_dispatch_probe_unexpected_error_is_skipped_not_raised():
    target = _make_target()
    with patch(
        "butlers.jobs.secrets_staleness.probe_system_credential",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        outcome = await _dispatch_probe(object(), target)

    assert outcome.ok is None
    assert outcome.skipped is True
    assert outcome.skip_reason == "error"


# ---------------------------------------------------------------------------
# _sweep — circuit breaker
# ---------------------------------------------------------------------------


async def test_sweep_trips_circuit_breaker_after_consecutive_failures():
    targets = [
        ProbeTarget(
            canonical_key=f"u:google-{i}",
            family="user",
            label="google",
            state="expiring",
            last_verified=None,
            circuit_group="user:google",
            user_provider="google",
            user_identity=uuid4(),
        )
        for i in range(5)
    ]

    call_count = 0

    async def fake_dispatch(db, target):
        nonlocal call_count
        call_count += 1
        return ProbeOutcome(
            key=target.canonical_key, family=target.family, label=target.label, ok=False
        )

    with patch("butlers.jobs.secrets_staleness._dispatch_probe", side_effect=fake_dispatch):
        outcomes = await _sweep(object(), targets)

    # 3 real dispatches (the threshold), then the breaker trips and the
    # remaining 2 are skipped without ever calling _dispatch_probe.
    assert call_count == 3
    assert [o.skip_reason for o in outcomes[3:]] == ["circuit_open", "circuit_open"]
    assert all(o.ok is False for o in outcomes[:3])


async def test_sweep_trips_circuit_breaker_on_unexpected_errors():
    # A hard-down provider raises/times out rather than returning ok=False —
    # _dispatch_probe converts that into a skipped outcome with
    # skip_reason="error". The breaker must count those as failures too, or a
    # full outage would never trip it (each remaining target would still pay
    # for its own timeout instead of being skipped).
    targets = [
        ProbeTarget(
            canonical_key=f"u:google-{i}",
            family="user",
            label="google",
            state="expiring",
            last_verified=None,
            circuit_group="user:google",
            user_provider="google",
            user_identity=uuid4(),
        )
        for i in range(5)
    ]

    call_count = 0

    async def fake_dispatch(db, target):
        nonlocal call_count
        call_count += 1
        return ProbeOutcome(
            key=target.canonical_key,
            family=target.family,
            label=target.label,
            ok=None,
            skipped=True,
            skip_reason="error",
        )

    with patch("butlers.jobs.secrets_staleness._dispatch_probe", side_effect=fake_dispatch):
        outcomes = await _sweep(object(), targets)

    assert call_count == 3
    assert [o.skip_reason for o in outcomes[3:]] == ["circuit_open", "circuit_open"]
    assert all(o.skip_reason == "error" for o in outcomes[:3])


async def test_sweep_resets_failure_streak_on_success():
    targets = [
        ProbeTarget(
            canonical_key=f"u:google-{i}",
            family="user",
            label="google",
            state="expiring",
            last_verified=None,
            circuit_group="user:google",
            user_provider="google",
            user_identity=uuid4(),
        )
        for i in range(4)
    ]
    # fail, fail, ok, fail — breaker should never trip since the ok resets the streak.
    results = [False, False, True, False]

    async def fake_dispatch(db, target):
        idx = int(target.canonical_key.rsplit("-", 1)[-1])
        return ProbeOutcome(
            key=target.canonical_key, family=target.family, label=target.label, ok=results[idx]
        )

    with patch("butlers.jobs.secrets_staleness._dispatch_probe", side_effect=fake_dispatch):
        outcomes = await _sweep(object(), targets)

    assert all(not o.skipped for o in outcomes)


# ---------------------------------------------------------------------------
# run_secrets_staleness_check
# ---------------------------------------------------------------------------


async def test_run_secrets_staleness_check_only_probes_stale_targets():
    fresh = _make_target(last_verified=datetime.now(UTC))
    stale = _make_target(last_verified=None)
    stale = ProbeTarget(
        **{**stale.__dict__, "canonical_key": "s:STALE_KEY", "system_key": "STALE_KEY"}
    )

    with (
        patch(
            "butlers.jobs.secrets_staleness._collect_probe_targets",
            new=AsyncMock(return_value=[fresh, stale]),
        ),
        patch(
            "butlers.jobs.secrets_staleness._dispatch_probe",
            new=AsyncMock(
                return_value=ProbeOutcome(
                    key="s:STALE_KEY", family="system", label="STALE_KEY", ok=True
                )
            ),
        ) as mock_dispatch,
    ):
        summary = await run_secrets_staleness_check(object(), staleness_s=3600)

    mock_dispatch.assert_awaited_once()
    assert summary["scanned"] == 2
    assert summary["stale"] == 1
    assert summary["probed"] == 1
    assert summary["ok"] == 1
    assert summary["failed"] == 0


async def test_run_secrets_staleness_check_collection_failure_is_clean_noop():
    with patch(
        "butlers.jobs.secrets_staleness._collect_probe_targets",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        summary = await run_secrets_staleness_check(object())

    assert summary == {"scanned": 0, "stale": 0, "probed": 0, "ok": 0, "failed": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# run_secrets_probe_all
# ---------------------------------------------------------------------------


async def test_run_secrets_probe_all_sweeps_every_target_regardless_of_staleness():
    fresh = _make_target(last_verified=datetime.now(UTC))

    with (
        patch(
            "butlers.jobs.secrets_staleness._collect_probe_targets",
            new=AsyncMock(return_value=[fresh]),
        ),
        patch(
            "butlers.jobs.secrets_staleness._dispatch_probe",
            new=AsyncMock(
                return_value=ProbeOutcome(key="s:KEY", family="system", label="KEY", ok=True)
            ),
        ) as mock_dispatch,
    ):
        outcomes = await run_secrets_probe_all(object())

    mock_dispatch.assert_awaited_once()
    assert len(outcomes) == 1
    assert outcomes[0].ok is True


async def test_run_secrets_probe_all_rejects_concurrent_sweep():
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_collect(db):
        started.set()
        await release.wait()
        return []

    with patch("butlers.jobs.secrets_staleness._collect_probe_targets", side_effect=slow_collect):
        first = asyncio.ensure_future(run_secrets_probe_all(object()))
        await started.wait()

        with pytest.raises(ProbeAllAlreadyRunning):
            await run_secrets_probe_all(object())

        release.set()
        await first
