"""Tests for butlers.core.liveness — the shared last_seen_at +
liveness_ttl_seconds staleness formula (bu-dvzya).

Cross-consumer parity coverage (registry.py's _derive_eligibility_state vs.
InfraStateSource's heartbeat-stale check) lives in
tests/core/qa/test_infra_state.py, alongside the InfraStateSource test
helpers it reuses.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from butlers.core.control_plane_identity import (
    ExpectedDaemon,
    ProbeFailure,
    ProbeOutcome,
    SingleFlightProber,
    _effective_observer_interval_s,
    probe_once,
    run_shadow_cycle,
    verify_identity,
)
from butlers.core.liveness import (
    CLOCK_SKEW_TOLERANCE,
    DEFAULT_LIVENESS_TTL_SECONDS,
    is_liveness_stale,
    normalize_liveness_ttl_seconds,
)
from butlers.core.utils import generate_uuid7_string

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# is_liveness_stale
# ---------------------------------------------------------------------------


def test_missing_last_seen_is_stale():
    assert is_liveness_stale(None, ttl_seconds=300, now=_NOW) is True


def test_fresh_within_ttl_is_not_stale():
    last_seen = _NOW - timedelta(seconds=100)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_exactly_at_ttl_boundary_is_not_stale():
    # last_seen_at + ttl == now is still fresh (>=, not strictly >).
    last_seen = _NOW - timedelta(seconds=300)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_just_past_ttl_boundary_is_stale():
    last_seen = _NOW - timedelta(seconds=301)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is True


def test_future_within_skew_tolerance_is_not_stale():
    last_seen = _NOW + timedelta(minutes=1)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_exactly_at_skew_tolerance_boundary_is_not_stale():
    last_seen = _NOW + CLOCK_SKEW_TOLERANCE
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is False


def test_future_beyond_skew_tolerance_is_stale():
    # Without the skew guard, the unbounded TTL window would keep this
    # "fresh" forever; a future-dated timestamp must not evade detection.
    last_seen = _NOW + timedelta(minutes=10)
    assert is_liveness_stale(last_seen, ttl_seconds=300, now=_NOW) is True


def test_custom_ttl_overrides_default():
    last_seen = _NOW - timedelta(seconds=100)
    assert is_liveness_stale(last_seen, ttl_seconds=50, now=_NOW) is True
    assert is_liveness_stale(last_seen, ttl_seconds=150, now=_NOW) is False


@pytest.mark.parametrize("raw_ttl", [None, "not-a-number", 0, -5])
def test_malformed_ttl_falls_back_to_default(raw_ttl):
    stale_anchor = _NOW - timedelta(seconds=DEFAULT_LIVENESS_TTL_SECONDS + 10)
    fresh_anchor = _NOW - timedelta(seconds=DEFAULT_LIVENESS_TTL_SECONDS - 10)
    assert is_liveness_stale(stale_anchor, ttl_seconds=raw_ttl, now=_NOW) is True
    assert is_liveness_stale(fresh_anchor, ttl_seconds=raw_ttl, now=_NOW) is False


def test_now_defaults_to_current_time_when_omitted():
    # A last_seen_at from "just now" against the real wall clock must read fresh.
    assert is_liveness_stale(datetime.now(UTC), ttl_seconds=300) is False


# ---------------------------------------------------------------------------
# normalize_liveness_ttl_seconds
# ---------------------------------------------------------------------------


def test_normalize_liveness_ttl_seconds_positive_passthrough():
    assert normalize_liveness_ttl_seconds(120) == 120


@pytest.mark.parametrize("raw", [None, "bogus", 0, -1, [], {}])
def test_normalize_liveness_ttl_seconds_defaults_on_bad_input(raw):
    assert normalize_liveness_ttl_seconds(raw) == DEFAULT_LIVENESS_TTL_SECONDS


def test_normalize_liveness_ttl_seconds_custom_default():
    assert normalize_liveness_ttl_seconds(None, default=60) == 60


def _identity_payload(*, epoch: int = 7) -> dict:
    return {
        "schema_version": "butler.control.v1",
        "butler_name": "health",
        "boot_instance_id": generate_uuid7_string(),
        "boot_epoch": epoch,
        "route_contract": {"min": 1, "max": 1},
        "accepting_routes": True,
    }


def test_identity_verifier_rejects_untrusted_or_not_ready_facts():
    expected = ExpectedDaemon("health", 41103, "butlers-up")
    with pytest.raises(ValueError, match="backend host"):
        ExpectedDaemon("health", 41103, "http://caller-selected.example").url
    for change, category in [
        ({"butler_name": "finance"}, "invalid_identity"),
        ({"boot_epoch": 6}, "invalid_identity"),
        ({"observed_at": "2026-09-23T00:00:00Z"}, "invalid_identity"),
        ({"route_contract": {"min": 2, "max": 2}}, "invalid_contract"),
        ({"accepting_routes": False}, "not_accepting"),
    ]:
        original = _identity_payload()
        payload = {**original, **change}
        with pytest.raises(ProbeFailure) as exc:
            verify_identity(payload, expected, 7, uuid.UUID(original["boot_instance_id"]))
        assert exc.value.category == category

    payload = _identity_payload()
    with pytest.raises(ProbeFailure) as exc:
        verify_identity(payload, expected, 7, uuid.UUID(generate_uuid7_string()))
    assert exc.value.category == "invalid_identity"


async def test_receiver_probe_uses_exact_roster_url_and_db_fence():
    expected = ExpectedDaemon("health", 41103, "butlers-up")
    payload = _identity_payload()
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        {"boot_epoch": 7, "probe_sequence": 3},
        {"boot_epoch": 7, "probe_sequence": 4},
        {"boot_epoch": 7, "probe_sequence": 5},
    ]
    pool.fetchval.side_effect = [uuid.UUID(payload["boot_instance_id"]), True] * 3

    async def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == expected.url
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        prober = SingleFlightProber(pool, client=client)
        outcome = await prober.probe(expected)
        again = await prober.probe(expected)
        concurrent = await asyncio.gather(prober.probe(expected), prober.probe(expected))

    assert outcome.category == "healthy" and outcome.recorded
    assert again.category == "healthy" and again.recorded
    assert all(result.category == "healthy" and result.recorded for result in concurrent)
    assert pool.fetchrow.await_count == 3
    assert pool.fetchrow.await_args.args == (
        "SELECT boot_epoch, probe_sequence FROM public.reserve_butler_probe($1)",
        "health",
    )
    assert pool.fetchval.await_args.args[1:7] == ("health", 7, 5, True, True, True)


async def test_failed_identity_records_attempt_without_healthy_renewal():
    expected = ExpectedDaemon("health", 41103, "butlers-up")
    payload = _identity_payload()
    pool = AsyncMock()
    pool.fetchrow.return_value = {"boot_epoch": 7, "probe_sequence": 4}
    pool.fetchval.side_effect = [uuid.UUID(payload["boot_instance_id"]), True]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={**payload, "boot_epoch": 6})
        )
    ) as client:
        outcome = await probe_once(pool, expected, client=client)

    assert outcome.category == "invalid_identity" and outcome.recorded
    assert pool.fetchval.await_args.args[1:] == (
        "health",
        7,
        4,
        False,
        None,
        None,
        "invalid_identity",
    )


async def test_receiver_probe_refuses_bounded_network_failures():
    expected = ExpectedDaemon("health", 41103, "butlers-up")
    for mode, category in [
        ("timeout", "timeout"),
        ("oversized", "invalid_identity"),
        ("duplicate", "invalid_identity"),
    ]:
        payload = _identity_payload()
        pool = AsyncMock()
        pool.fetchrow.return_value = {"boot_epoch": 7, "probe_sequence": 5}
        pool.fetchval.side_effect = [uuid.UUID(payload["boot_instance_id"]), True]

        async def respond(_request: httpx.Request) -> httpx.Response:
            if mode == "timeout":
                await asyncio.sleep(0.02)
                return httpx.Response(200, json=payload)
            if mode == "duplicate":
                body = json.dumps(payload).replace(
                    '"boot_epoch": 7', '"boot_epoch": 7, "boot_epoch": 7'
                )
                return httpx.Response(200, content=body)
            return httpx.Response(200, content=b"x" * 2049)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            outcome = await probe_once(
                pool, expected, client=client, deadline_s=0.001 if mode == "timeout" else 1
            )

        assert outcome.category == category and outcome.recorded
        assert pool.fetchval.await_args.args[4] is False


async def test_partial_shadow_cycle_cannot_claim_complete_fleet():
    pool = AsyncMock()
    pool.fetch.return_value = []
    pool.fetchval.return_value = datetime.now(UTC)
    prober = SimpleNamespace(probe=AsyncMock(return_value=ProbeOutcome("observer_error", False)))

    cycle = await run_shadow_cycle(pool, (ExpectedDaemon("health", 41103, "butlers-up"),), prober)

    assert cycle.complete is False
    assert cycle.expected_count == 1 and cycle.recorded_count == 0
    assert cycle.mismatch_count == 1

    # A later operator TTL reduction wins over the Git seed; the observer
    # must shorten its next interval rather than keep the original 300s.
    pool.fetch.return_value = [{"name": "health", "liveness_ttl_seconds": 30}]
    assert (
        await _effective_observer_interval_s(pool, (ExpectedDaemon("health", 41103, "butlers-up"),))
        == 15
    )
